import hashlib
import json
import re
import time
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import httpx

from .errors import ServiceError
from .media import remaining
from .settings import Settings


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(settings: Settings) -> dict:
    try:
        raw = Path(settings.manifest_path).read_bytes()
        if len(raw) > 1048576 or not re.fullmatch("[a-f0-9]{64}", settings.manifest_sha256):
            raise ValueError("Manifest digest missing")
        if hashlib.sha256(raw).hexdigest() != settings.manifest_sha256:
            raise ValueError("Manifest digest differs")
        value = json.loads(raw)
        if value["schemaVersion"] != "1" or value["status"] != "locked" or value["entryModel"] != "aws-rekognition":
            raise ValueError("Manifest is not locked")
        if not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,160}", value["manifestVersion"]):
            raise ValueError("Invalid version")
        if not re.fullmatch("[a-f0-9]{64}", value["taxonomySha256"]):
            raise ValueError("Taxonomy digest required")
        for name, adapter in (("detector", "grounding-dino"), ("embedding", "siglip2")):
            model = value[name]
            if model["adapter"] != adapter or not re.fullmatch("[a-f0-9]{40}", model["revision"]):
                raise ValueError("Unsupported adapter or mutable revision")
            if not model["files"] or not any(item["name"].endswith(".safetensors") for item in model["files"]):
                raise ValueError("Safe model weights required")
            seen = set()
            for item in model["files"]:
                path = PurePosixPath(item["name"])
                if (path.is_absolute() or ".." in path.parts or "\\" in item["name"] or item["name"] in seen
                        or path.suffix not in {".json", ".txt", ".safetensors", ".model"}
                        or not re.fullmatch("[a-f0-9]{64}", item["sha256"])
                        or not 0 < item["bytes"] <= 3_000_000_000):
                    raise ValueError("Invalid model artifact")
                seen.add(item["name"])
                source = urlsplit(item["uri"])
                expected_prefix = f"/{model['modelId']}/resolve/{model['revision']}/"
                if not (source.scheme == "gs" and source.netloc and source.path
                        or source.scheme == "https" and source.netloc == "huggingface.co"
                        and source.path.startswith(expected_prefix)):
                    raise ValueError("Untrusted model artifact source")
            if sum(item["bytes"] for item in model["files"]) > 4_000_000_000:
                raise ValueError("Model artifact total too large")
        if not 0 < value["detector"]["boxThreshold"] <= 1 or not 0 < value["detector"]["textThreshold"] <= 1:
            raise ValueError("Invalid confidence threshold")
        if not 0 < value["detector"]["nmsIou"] <= 1:
            raise ValueError("Invalid suppression threshold")
        if value["detector"].get("promptStrategy", "per_species") != "per_species":
            raise ValueError("Unsupported prompt strategy")
        size = value["detector"].get("imageSize", {"shortest_edge": 640, "longest_edge": 1024})
        if not 256 <= size["shortest_edge"] <= 800 or not size["shortest_edge"] <= size["longest_edge"] <= 1333:
            raise ValueError("Invalid detector resize")
        return value
    except (OSError, ValueError, KeyError, TypeError):
        raise ServiceError("MODEL_NOT_CONFIGURED", "An immutable, SHA-256 verified model manifest is required.", 503) from None


def materialize(model: dict, settings: Settings, deadline: float) -> Path:
    """Only model artifacts persist in this cache; never request media."""
    directory = Path(settings.model_cache_dir) / model["adapter"] / model["revision"]
    directory.mkdir(parents=True, exist_ok=True)
    for item in model["files"]:
        remaining(deadline)
        destination = directory / item["name"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.stat().st_size == item["bytes"] and sha256_file(destination) == item["sha256"]:
                continue
            destination.unlink()
        partial = destination.with_suffix(destination.suffix + ".partial")
        try:
            source = urlsplit(item["uri"])
            if source.scheme == "gs":
                from google.cloud import storage
                blob = storage.Client().bucket(source.netloc).blob(source.path.lstrip("/"))
                # Streaming keeps model artifacts from adding a second full in-memory copy.
                with blob.open("rb", timeout=min(remaining(deadline), 30)) as stream, partial.open("wb") as output:
                    copied = 0
                    while chunk := stream.read(1024 * 1024):
                        remaining(deadline)
                        copied += len(chunk)
                        if copied > item["bytes"]:
                            raise ValueError("Artifact too large")
                        output.write(chunk)
            else:
                with httpx.Client(follow_redirects=True, trust_env=False, timeout=30) as client:
                    with client.stream("GET", item["uri"]) as response, partial.open("wb") as output:
                        response.raise_for_status()
                        copied = 0
                        for chunk in response.iter_bytes(1024 * 1024):
                            remaining(deadline)
                            copied += len(chunk)
                            if copied > item["bytes"]:
                                raise ValueError("Artifact too large")
                            output.write(chunk)
            if partial.stat().st_size != item["bytes"] or sha256_file(partial) != item["sha256"]:
                raise ServiceError("MODEL_INTEGRITY_FAILED", "A model artifact failed SHA-256 verification.", 503)
            partial.replace(destination)
        except ServiceError:
            raise
        except Exception:
            # Model source errors can contain signed download URLs. Do not expose them.
            raise ServiceError("MODEL_DOWNLOAD_FAILED", "A pinned model artifact could not be obtained.", 503, True) from None
        finally:
            partial.unlink(missing_ok=True)
    return directory
