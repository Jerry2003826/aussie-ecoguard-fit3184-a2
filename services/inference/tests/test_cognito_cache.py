"""Cognito JWKS cache boundaries, with signed disposable RSA token fixtures."""
import asyncio
from types import SimpleNamespace

import httpx
import jwt
import pytest

from app.auth import CognitoVerifier
from app.errors import ServiceError


@pytest.fixture
def cache_clock(monkeypatch):
    clock = SimpleNamespace(now=10000.0)
    # Replace only the auth module's clock, not asyncio's process-wide clock.
    monkeypatch.setattr("app.auth.time", SimpleNamespace(monotonic=lambda: clock.now))
    return clock


@pytest.mark.parametrize("response", [httpx.Response(503), httpx.Response(200, json={"keys": []})])
async def test_expired_cache_refresh_failure_and_throttled_retry_fail_closed(settings, keys, token_factory, cache_clock, response):
    requests = []
    def unavailable(request):
        requests.append(str(request.url))
        return response
    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as client:
        verifier = CognitoVerifier(settings, client)
        verifier.keys = {"test-key": keys[1]}
        verifier.refreshed_at = cache_clock.now - 3600
        token = "Bearer " + token_factory()
        for _ in range(2):
            with pytest.raises(ServiceError) as error:
                await verifier.verify(token)
            assert error.value.code == "IDENTITY_PROVIDER_UNAVAILABLE"
            assert error.value.status == 503
            assert error.value.retryable
    assert requests == [settings.cognito_issuer + "/.well-known/jwks.json"]


async def test_concurrent_requests_cannot_use_expired_cache_while_refresh_is_throttled(settings, keys, token_factory, cache_clock):
    requests = []
    async def unavailable(request):
        requests.append(str(request.url))
        await asyncio.sleep(0)
        return httpx.Response(503)
    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as client:
        verifier = CognitoVerifier(settings, client)
        verifier.keys = {"test-key": keys[1]}
        verifier.refreshed_at = cache_clock.now - 3601
        results = await asyncio.gather(*[verifier.verify("Bearer " + token_factory()) for _ in range(8)], return_exceptions=True)
    assert len(requests) == 1
    assert all(isinstance(result, ServiceError) and result.status == 503 for result in results)


async def test_valid_warm_cache_does_not_require_identity_provider(settings, keys, token_factory, cache_clock):
    def no_request(request):
        pytest.fail("A valid warm key must not require a network refresh")
    async with httpx.AsyncClient(transport=httpx.MockTransport(no_request)) as client:
        verifier = CognitoVerifier(settings, client)
        verifier.keys = {"test-key": keys[1]}
        verifier.refreshed_at = cache_clock.now - 3599
        verifier.last_attempt = cache_clock.now
        assert await verifier.verify("Bearer " + token_factory()) == "user-123"


async def test_unknown_key_refreshes_and_unknown_key_throttling_still_rejects(settings, keys, token_factory, cache_clock):
    token = token_factory()
    claims = jwt.decode(token, options={"verify_signature": False})
    rotated = jwt.encode(claims, keys[0], algorithm="RS256", headers={"kid": "rotated-key"})
    unknown = jwt.encode(claims, keys[0], algorithm="RS256", headers={"kid": "unknown-key"})
    requests = []
    def rotated_keys(request):
        requests.append(str(request.url))
        return httpx.Response(200, json={"keys": [keys[1], {**keys[1], "kid": "rotated-key"}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(rotated_keys)) as client:
        verifier = CognitoVerifier(settings, client)
        verifier.keys = {"test-key": keys[1]}
        verifier.refreshed_at = cache_clock.now - 10
        assert await verifier.verify("Bearer " + rotated) == "user-123"
        with pytest.raises(ServiceError) as error:
            await verifier.verify("Bearer " + unknown)
        assert error.value.status == 401
        assert await verifier.verify("Bearer " + token) == "user-123"
    assert requests == [settings.cognito_issuer + "/.well-known/jwks.json"]


async def test_expired_cache_recovers_after_successful_refresh(settings, keys, token_factory, cache_clock):
    requests = []
    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(503) if len(requests) == 1 else httpx.Response(200, json={"keys": [keys[1]]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        verifier = CognitoVerifier(settings, client)
        verifier.keys = {"test-key": keys[1]}
        verifier.refreshed_at = cache_clock.now - 3601
        with pytest.raises(ServiceError):
            await verifier.verify("Bearer " + token_factory())
        cache_clock.now += 5
        assert await verifier.verify("Bearer " + token_factory()) == "user-123"
        assert verifier.refreshed_at == cache_clock.now
    assert len(requests) == 2
