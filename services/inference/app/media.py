"""Bounded, non-redirecting downloads from explicitly configured S3 buckets."""
import hashlib
import re
import time
import warnings
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from .errors import DeadlineExceeded, ServiceError
from .settings import Settings


def remaining(deadline: float) -> float:
    value = deadline - time.monotonic()
    if value <= 0:
        raise DeadlineExceeded()
    return value


def validate_source_url(url: str, settings: Settings) -> None:
    try:
        parts = urlsplit(url)
        hostname = parts.hostname or ""
        key = unquote(parts.path.lstrip("/"))
        if (parts.scheme != "https" or parts.port not in (None, 443) or parts.username or parts.password
                or parts.fragment or hostname not in settings.source_hosts
                or not re.fullmatch(r"[a-z0-9][a-z0-9.-]+\.s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com", hostname)
                or "\\" in key or any(segment in (".", "..") for segment in key.split("/"))
                or "%" in key or not any(key.startswith(prefix) for prefix in settings.source_prefixes)):
            raise ValueError("Source not allowed")
    except (ValueError, TypeError):
        raise ServiceError("SOURCE_NOT_ALLOWED", "The frame URL is outside the configured S3 sources.", 400) from None


def fetch_image(url: str, expected_sha256: str | None, directory: Path, settings: Settings,
                deadline: float, client: httpx.Client | None = None) -> Image.Image:
    validate_source_url(url, settings)
    target = directory / "frame.bin"
    size = 0
    digest = hashlib.sha256()
    owns_client = client is None
    client = client or httpx.Client(follow_redirects=False, trust_env=False)
    try:
        timeout = min(remaining(deadline), 20)
        with client.stream("GET", url, timeout=httpx.Timeout(timeout, connect=min(5, timeout)),
                           follow_redirects=False, headers={"Accept-Encoding": "identity"}) as response:
            if response.status_code != 200:
                raise ServiceError("FRAME_FETCH_FAILED", "The frame could not be read from its authorized source.", 502, response.status_code >= 500)
            content_length = response.headers.get("content-length")
            if content_length and (not content_length.isdecimal() or int(content_length) > settings.max_frame_bytes):
                raise ServiceError("FRAME_TOO_LARGE", "The frame exceeds the byte limit.", 413)
            with target.open("xb") as output:
                for chunk in response.iter_bytes(65536):
                    remaining(deadline)
                    size += len(chunk)
                    if size > settings.max_frame_bytes:
                        raise ServiceError("FRAME_TOO_LARGE", "The frame exceeds the byte limit.", 413)
                    digest.update(chunk)
                    output.write(chunk)
        if expected_sha256 and digest.hexdigest() != expected_sha256:
            raise ServiceError("FRAME_CHECKSUM_MISMATCH", "The frame checksum did not match.", 422)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(target) as original:
                if original.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ServiceError("UNSUPPORTED_FRAME", "A JPEG, PNG or WebP frame is required.", 415)
                if original.width * original.height > settings.max_image_pixels:
                    raise ServiceError("FRAME_TOO_LARGE", "The frame exceeds the decoded pixel limit.", 413)
                if getattr(original, "n_frames", 1) != 1:
                    raise ServiceError("UNSUPPORTED_FRAME", "Animated frames must be sampled by the AWS worker.", 415)
                result = ImageOps.exif_transpose(original).convert("RGB")
                result.load()
                return result
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ServiceError("INVALID_FRAME", "The downloaded object is not a safe image.", 422) from None
    except (httpx.HTTPError, OSError):
        raise ServiceError("FRAME_FETCH_FAILED", "The frame could not be decoded or downloaded.", 502, True) from None
    finally:
        if owns_client:
            client.close()
        target.unlink(missing_ok=True)
