"""Service authentication for Cloud Run or an independently hosted Oracle service.

Cloud Run strips the service token signature after validating the
X-Serverless-Authorization token. Never pretend an unverified decode authenticates
that token. Cloud Run deployment MUST deny unauthenticated invocation in IAM.
Oracle has no such IAM proxy: it verifies the complete Google token itself.
"""
import asyncio
import hmac
import re
import time

import httpx
import jwt
from fastapi import Request

from .errors import ServiceError
from .settings import Settings


def bearer(value: str | None) -> str:
    if not value or not value.startswith("Bearer ") or not 8 < len(value) <= 20000:
        raise ServiceError("UNAUTHORIZED", "A valid bearer token is required.", 401)
    token = value[7:].strip()
    if not token or any(char.isspace() for char in token):
        raise ServiceError("UNAUTHORIZED", "A valid bearer token is required.", 401)
    return token


async def require_service(request: Request, settings: Settings, oracle_verifier=None) -> None:
    token = bearer(request.headers.get("x-serverless-authorization"))
    if settings.service_auth_mode == "oracle":
        if oracle_verifier is None:
            raise ServiceError("SERVICE_AUTH_NOT_CONFIGURED", "Oracle service authentication is not configured.", 503)
        # Never use K_SERVICE or mere header presence to authorize this branch.
        # Oracle receives the ORIGINAL signed token, not Cloud Run's stripped one.
        await oracle_verifier.verify("Bearer " + token)
        return
    if settings.service_auth_mode == "cloud-run":
        if not settings.cloud_run_service:
            raise ServiceError("SERVICE_AUTH_NOT_CONFIGURED", "Private Cloud Run authentication is not configured.", 503)
        # The IAM layer already validated this token and its invoker permission.
        # Header presence is a deployment guard, not a signature verification.
        return
    if settings.service_auth_mode == "local" and not settings.cloud_run_service:
        if len(settings.local_service_token) >= 32 and hmac.compare_digest(token, settings.local_service_token):
            return
    raise ServiceError("UNAUTHORIZED_SERVICE", "The service identity is not authorized.", 401)


class GoogleServiceVerifier:
    """Verify Google-issued service-account ID tokens against fixed Google JWKS.

    No URL, key, algorithm, issuer or email is taken from an unverified token.
    The cache is shared per app instance; a lock coalesces concurrent refreshes.
    Unknown kid refreshes are throttled. Expired key caches fail closed.
    """
    JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
    ISSUERS = ("https://accounts.google.com", "accounts.google.com")

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings, self.client = settings, client
        self.keys: dict[str, dict] = {}
        self.expires_at = 0.0
        self.last_attempt = 0.0
        self.lock = asyncio.Lock()

    def _configured(self):
        if (not self.settings.service_token_audience.strip()
                or len(self.settings.service_token_audience) > 2048
                or not re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9-]+\.iam\.gserviceaccount\.com", self.settings.service_token_email)):
            raise ServiceError("SERVICE_AUTH_NOT_CONFIGURED", "Oracle requires an exact token audience and service-account email.", 503)

    async def _refresh(self, kid: str):
        async with self.lock:
            now = time.monotonic()
            if kid in self.keys and now < self.expires_at:
                return
            if now - self.last_attempt < 5:
                return
            self.last_attempt = now

            async def fetch(client):
                async with client.stream("GET", self.JWKS_URL, follow_redirects=False,
                                         timeout=httpx.Timeout(4, connect=1.5)) as response:
                    response.raise_for_status()
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 65536:
                            raise ValueError("Oversize Google JWKS")
                    import json
                    payload = json.loads(data)
                    entries = payload["keys"]
                    keys = {entry["kid"]: entry for entry in entries if entry.get("kty") == "RSA"
                            and entry.get("alg") == "RS256" and entry.get("use", "sig") == "sig"
                            and isinstance(entry.get("kid"), str)}
                    if not keys:
                        raise ValueError("Empty Google JWKS")
                    match = re.search(r"(?:^|,)\s*max-age=(\d+)(?:\s*(?:,|$))", response.headers.get("cache-control", ""))
                    ttl = min(int(match.group(1)), 3600) if match else 3600
                    # Validate the fetched keys before publishing a cache entry.
                    for entry in keys.values():
                        jwt.PyJWK.from_dict(entry, algorithm="RS256")
                    self.keys, self.expires_at = keys, time.monotonic() + ttl

            try:
                if self.client is not None:
                    await fetch(self.client)
                else:
                    async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
                        await fetch(client)
            except (httpx.HTTPError, ValueError, KeyError, TypeError, jwt.PyJWTError):
                raise ServiceError("SERVICE_IDENTITY_PROVIDER_UNAVAILABLE", "Google signing keys are temporarily unavailable.", 503, True) from None

    async def verify(self, authorization: str | None) -> str:
        try:
            async with asyncio.timeout(self.settings.authorization_timeout):
                return await self._verify(authorization)
        except TimeoutError:
            raise ServiceError("SERVICE_AUTHORIZATION_TIMEOUT", "Service identity verification timed out.", 503, True) from None

    async def _verify(self, authorization: str | None) -> str:
        self._configured()
        token = bearer(authorization)
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if header.get("alg") != "RS256" or not isinstance(kid, str) or not 1 <= len(kid) <= 200:
                raise ValueError("Unsupported Google signing key")
            if kid not in self.keys or time.monotonic() >= self.expires_at:
                await self._refresh(kid)
            if time.monotonic() >= self.expires_at:
                raise ServiceError("SERVICE_IDENTITY_PROVIDER_UNAVAILABLE", "Google signing keys could not be refreshed.", 503, True)
            if kid not in self.keys:
                raise ValueError("Unknown Google signing key")
            key = jwt.PyJWK.from_dict(self.keys[kid], algorithm="RS256").key
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.settings.service_token_audience,
                                issuer=self.ISSUERS, leeway=0,
                                options={"strict_aud": True, "require": ["iss", "aud", "exp", "iat", "sub", "email", "email_verified"]})
            if claims["email"] != self.settings.service_token_email or claims["email_verified"] is not True:
                raise ValueError("Wrong service account")
            if not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 160:
                raise ValueError("Missing service-account subject")
            return claims["email"]
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise ServiceError("UNAUTHORIZED_SERVICE", "The Google service-account token is invalid or unauthorized.", 401) from None


class CognitoVerifier:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client
        self.keys: dict[str, dict] = {}
        self.refreshed_at = 0.0
        self.last_attempt = 0.0
        self.lock = asyncio.Lock()

    async def _refresh(self, kid: str) -> None:
        async with self.lock:
            now = time.monotonic()
            if kid in self.keys and now - self.refreshed_at < 3600:
                return
            if now - self.last_attempt < 5:
                return
            self.last_attempt = now
            issuer = self.settings.cognito_issuer
            if not re.fullmatch(r"https://cognito-idp\.[a-z0-9-]+\.amazonaws\.com(?:\.cn)?/[A-Za-z0-9_-]+", issuer):
                raise ServiceError("AUTH_NOT_CONFIGURED", "Cognito authorization is not configured.", 503)
            async def fetch(client):
                response = await client.get(issuer + "/.well-known/jwks.json", timeout=httpx.Timeout(4, connect=1.5))
                response.raise_for_status()
                if len(response.content) > 65536:
                    raise ValueError("Oversize JWKS")
                data = response.json()
                keys = {entry["kid"]: entry for entry in data["keys"]
                        if entry.get("kty") == "RSA" and entry.get("alg", "RS256") == "RS256"
                        and entry.get("use", "sig") == "sig"}
                if not keys:
                    raise ValueError("Empty JWKS")
                self.keys, self.refreshed_at = keys, time.monotonic()
            try:
                if self.client:
                    await fetch(self.client)
                else:
                    async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
                        await fetch(client)
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                raise ServiceError("IDENTITY_PROVIDER_UNAVAILABLE", "Cognito signing keys are temporarily unavailable.", 503, True) from None

    async def verify(self, authorization: str | None) -> str:
        token = bearer(authorization)
        if not self.settings.cognito_client_id:
            raise ServiceError("AUTH_NOT_CONFIGURED", "Cognito authorization is not configured.", 503)
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if header.get("alg") != "RS256" or not isinstance(kid, str) or not 1 <= len(kid) <= 200:
                raise ValueError("Unsupported key")
            if kid not in self.keys or time.monotonic() - self.refreshed_at >= 3600:
                await self._refresh(kid)
            # A concurrent refresh failure may leave this request throttled.
            # Recheck freshness before trusting any previously cached key.
            if time.monotonic() - self.refreshed_at >= 3600:
                raise ServiceError("IDENTITY_PROVIDER_UNAVAILABLE", "Cognito signing keys could not be refreshed.", 503, True)
            if kid not in self.keys:
                raise ValueError("Unknown signing key")
            public_key = jwt.PyJWK.from_dict(self.keys[kid], algorithm="RS256").key
            claims = jwt.decode(token, public_key, algorithms=["RS256"], issuer=self.settings.cognito_issuer,
                                options={"verify_aud": False, "require": ["exp", "iat", "iss", "sub", "client_id", "token_use"]},
                                leeway=0)
            if claims.get("token_use") != "access" or claims.get("client_id") != self.settings.cognito_client_id:
                raise ValueError("Wrong purpose or client")
            if not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 160:
                raise ValueError("Invalid subject")
            return claims["sub"]
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise ServiceError("UNAUTHORIZED", "The Cognito access token is invalid or expired.", 401) from None
