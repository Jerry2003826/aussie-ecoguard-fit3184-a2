import asyncio
import sys
import time
from dataclasses import replace

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from app.auth import CognitoVerifier, GoogleServiceVerifier
from app.errors import ServiceError
from app.main import create_app


@pytest.fixture
def oracle_settings(settings):
    return replace(settings, service_auth_mode="oracle", service_token_audience="https://inference.example.test",
                   service_token_email="a2-invoker@test-project.iam.gserviceaccount.com")


@pytest.fixture
def google_token(oracle_settings, keys):
    def create(**overrides):
        claims = {"iss": "https://accounts.google.com", "aud": oracle_settings.service_token_audience,
                  "sub": "123456789012345678901", "email": oracle_settings.service_token_email,
                  "email_verified": True, "iat": int(time.time()) - 2, "exp": int(time.time()) + 120}
        claims.update(overrides)
        return jwt.encode(claims, keys[0], algorithm="RS256", headers={"kid": "test-key"})
    return create


def service_verifier(settings, keys):
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"keys": [keys[1]]}, headers={"Cache-Control": "public, max-age=3600"})))
    return GoogleServiceVerifier(settings, client)


async def test_google_service_token_has_real_rsa_signature(oracle_settings, keys, google_token):
    verifier = service_verifier(oracle_settings, keys)
    assert await verifier.verify("Bearer " + google_token()) == oracle_settings.service_token_email
    assert await verifier.verify("Bearer " + google_token(iss="accounts.google.com")) == oracle_settings.service_token_email
    await verifier.client.aclose()


@pytest.mark.parametrize("claims", [
    {"exp": 1}, {"iat": 4102444800}, {"aud": "https://different-service.example.test"},
    {"aud": ["https://inference.example.test", "https://another.example.test"]},
    {"email": "attacker@test-project.iam.gserviceaccount.com"}, {"email": "a.user@gmail.com"},
    {"email_verified": False}, {"email_verified": "true"}, {"iss": "https://evil.example.test"},
    {"sub": ""},
])
async def test_reject_expiry_audience_email_and_issuer(oracle_settings, keys, google_token, claims):
    verifier = service_verifier(oracle_settings, keys)
    with pytest.raises(ServiceError) as error:
        await verifier.verify("Bearer " + google_token(**claims))
    assert error.value.code == "UNAUTHORIZED_SERVICE"
    assert error.value.status == 401
    await verifier.client.aclose()


async def test_oracle_rejects_tampered_signature(oracle_settings, keys, google_token):
    verifier = service_verifier(oracle_settings, keys)
    header, payload, signature = google_token().split(".")
    signature = ("A" if signature[0] != "A" else "B") + signature[1:]
    with pytest.raises(ServiceError) as error:
        await verifier.verify(f"Bearer {header}.{payload}.{signature}")
    assert error.value.status == 401
    await verifier.client.aclose()


async def test_google_jwks_single_flight_and_fixed_url(oracle_settings, keys, google_token):
    requests = []
    async def respond(request):
        requests.append(str(request.url))
        await asyncio.sleep(0.02)
        return httpx.Response(200, json={"keys": [keys[1]]}, headers={"cache-control": "max-age=3600"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        verifier = GoogleServiceVerifier(oracle_settings, client)
        results = await asyncio.gather(*[verifier.verify("Bearer " + google_token()) for _ in range(12)])
    assert len(set(results)) == 1
    assert requests == ["https://www.googleapis.com/oauth2/v3/certs"]


async def test_google_jwks_deadline_is_bounded(oracle_settings, keys, google_token):
    async def slow(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={"keys": [keys[1]]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
        verifier = GoogleServiceVerifier(replace(oracle_settings, authorization_timeout=0.03), client)
        start = time.monotonic()
        with pytest.raises(ServiceError) as error:
            await verifier.verify("Bearer " + google_token())
        assert time.monotonic() - start < 0.5
        assert error.value.code == "SERVICE_AUTHORIZATION_TIMEOUT"


async def test_expired_jwks_cache_cannot_be_used_after_refresh_failure(oracle_settings, keys, google_token):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    verifier = GoogleServiceVerifier(oracle_settings, client)
    verifier.keys = {"test-key": keys[1]}
    verifier.expires_at = time.monotonic() - 1
    with pytest.raises(ServiceError) as error:
        await verifier.verify("Bearer " + google_token())
    assert error.value.status == 503
    # Refresh throttling must not accidentally authorize with stale cached keys.
    with pytest.raises(ServiceError) as error:
        await verifier.verify("Bearer " + google_token())
    assert error.value.status == 503
    await client.aclose()


@pytest.mark.parametrize("field", ["service_token_audience", "service_token_email"])
async def test_required_oracle_configuration(oracle_settings, keys, google_token, field):
    verifier = service_verifier(replace(oracle_settings, **{field: ""}), keys)
    with pytest.raises(ServiceError) as error:
        await verifier.verify("Bearer " + google_token())
    assert error.value.code == "SERVICE_AUTH_NOT_CONFIGURED"
    await verifier.client.aclose()


def test_oracle_dual_header_query_and_no_platform_bypass(oracle_settings, keys, google_token, token_factory):
    # Even an injected K_SERVICE value does not grant Oracle platform trust.
    settings = replace(oracle_settings, cloud_run_service="forged-cloud-run-name")
    cognito_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"keys": [keys[1]]})))
    cognito = CognitoVerifier(settings, cognito_client)
    google = service_verifier(settings, keys)
    with TestClient(create_app(settings, verifier=cognito, service_verifier=google)) as client:
        body = {"requestId": "oracle-q1", "purpose": "pet"}
        headers = {"X-Serverless-Authorization": "Bearer " + google_token(), "Authorization": "Bearer " + token_factory()}
        valid = client.post("/v1/query-authorizations", json=body, headers=headers)
        assert valid.status_code == 200
        assert valid.json() == {"sub": "user-123", "requestId": "oracle-q1"}
        assert "torch" not in sys.modules
        assert client.post("/v1/query-authorizations", json=body,
                           headers={"X-Serverless-Authorization": headers["X-Serverless-Authorization"]}).status_code == 401
        for value in ("Bearer bogus-platform-token", "Bearer " + settings.local_service_token,
                      "Bearer " + google_token(email="wrong@test-project.iam.gserviceaccount.com")):
            bad = client.post("/v1/query-authorizations", json=body, headers={**headers, "X-Serverless-Authorization": value})
            assert bad.status_code == 401
        assert client.post("/v1/warmup", headers={"X-Serverless-Authorization": "Bearer bogus-platform-token"}).status_code == 401
        assert client.get("/healthz").json()["models"]["loadedModels"] == []


def test_original_cloud_run_iam_branch_preserved(settings, keys, token_factory):
    cloud = replace(settings, service_auth_mode="cloud-run", cloud_run_service="real-cloud-run-service")
    cognito_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"keys": [keys[1]]})))
    with TestClient(create_app(cloud, verifier=CognitoVerifier(cloud, cognito_client))) as client:
        result = client.post("/v1/query-authorizations", json={"requestId": "q1", "purpose": "file"},
                             headers={"X-Serverless-Authorization": "Bearer already-verified-by-cloud-run",
                                      "Authorization": "Bearer " + token_factory()})
        assert result.status_code == 200
