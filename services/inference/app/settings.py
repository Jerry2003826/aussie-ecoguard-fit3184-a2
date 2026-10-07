from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    service_auth_mode: str = "cloud-run"
    local_service_token: str = ""
    cloud_run_service: str = ""
    service_token_audience: str = ""
    service_token_email: str = ""
    cognito_issuer: str = ""
    cognito_client_id: str = ""
    manifest_path: str = ""
    manifest_sha256: str = ""
    taxonomy_path: str = ""
    model_cache_dir: str = "/tmp/ecoguard-models"
    source_hosts: tuple[str, ...] = ()
    source_prefixes: tuple[str, ...] = ("derived/", "queries/")
    max_request_bytes: int = 1048576
    max_response_bytes: int = 2097152
    max_frame_bytes: int = 20971520
    max_image_pixels: int = 40000000
    max_detections_per_frame: int = 64
    max_pet_features: int = 32
    inference_timeout: int = 240
    authorization_timeout: float = 8
    torch_threads: int = 4

    @classmethod
    def from_env(cls):
        base = Path(__file__).resolve().parents[3] / "config"
        return cls(
            service_auth_mode=os.getenv("SERVICE_AUTH_MODE", "cloud-run"),
            local_service_token=os.getenv("LOCAL_SERVICE_TOKEN", ""),
            cloud_run_service=os.getenv("K_SERVICE", ""),
            service_token_audience=os.getenv("SERVICE_TOKEN_AUDIENCE", ""),
            service_token_email=os.getenv("SERVICE_TOKEN_EMAIL", ""),
            cognito_issuer=os.getenv("COGNITO_ISSUER", ""),
            cognito_client_id=os.getenv("COGNITO_CLIENT_ID", ""),
            manifest_path=os.getenv("MODEL_MANIFEST_PATH", str(base / "model.example.json")),
            manifest_sha256=os.getenv("MODEL_MANIFEST_SHA256", ""),
            taxonomy_path=os.getenv("TAXONOMY_PATH", str(base / "taxonomy.json")),
            model_cache_dir=os.getenv("MODEL_CACHE_DIR", "/tmp/ecoguard-models"),
            source_hosts=tuple(x.strip().lower() for x in os.getenv("SOURCE_S3_HOSTS", "").split(",") if x.strip()),
            source_prefixes=tuple(x.strip() for x in os.getenv("SOURCE_KEY_PREFIXES", "derived/,queries/").split(",") if x.strip()),
            inference_timeout=int(os.getenv("INFERENCE_TIMEOUT_SECONDS", "240")),
            torch_threads=int(os.getenv("TORCH_THREADS", "4")),
        )
