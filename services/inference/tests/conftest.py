import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.settings import Settings


@pytest.fixture
def settings():
    return Settings(service_auth_mode="local", local_service_token="unit-test-service-token-32-characters-long",
                    cognito_issuer="https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TEST",
                    cognito_client_id="test-client", source_hosts=("a2-media.s3.us-east-1.amazonaws.com",),
                    taxonomy_path=str(Path(__file__).resolve().parents[3] / "config" / "taxonomy.json"))


@pytest.fixture
def keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    public.update(kid="test-key", alg="RS256", use="sig")
    return private, public


@pytest.fixture
def token_factory(settings, keys):
    def make(**overrides):
        claims = {"iss": settings.cognito_issuer, "client_id": settings.cognito_client_id,
                  "sub": "user-123", "token_use": "access", "exp": int(time.time()) + 120,
                  "iat": int(time.time()) - 2}
        claims.update(overrides)
        return jwt.encode(claims, keys[0], algorithm="RS256", headers={"kid": "test-key"})
    return make


@pytest.fixture
def locked_settings(tmp_path, settings):
    """Tiny NON-MODEL bytes test integrity handling, not recognition quality."""
    root = Path(__file__).resolve().parents[3]
    manifest = json.loads((root / "config/model.example.json").read_text())
    manifest.update(status="locked", manifestVersion="unit-test-model-v1")
    manifest["taxonomySha256"] = hashlib.sha256((root / "config/taxonomy.json").read_bytes()).hexdigest()
    for name in ("detector", "embedding"):
        model = manifest[name]
        model["revision"] = "a" * 40
        content = b"not-real-weights-only-a-checksum-test"
        model["files"] = [{"name": "model.safetensors", "uri": f"https://huggingface.co/{model['modelId']}/resolve/{'a' * 40}/model.safetensors",
                           "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return replace(settings, manifest_path=str(path), manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                   model_cache_dir=str(tmp_path / "cache"))
