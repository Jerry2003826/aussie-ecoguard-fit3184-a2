import hashlib
import io
import time

import httpx
import pytest
from PIL import Image

from app.errors import ServiceError
from app.media import fetch_image, validate_source_url


URL = "https://a2-media.s3.us-east-1.amazonaws.com/derived/media1/frame.jpg?X-Amz-Signature=not-a-real-signature"


@pytest.mark.parametrize("url", [
    "http://a2-media.s3.us-east-1.amazonaws.com/derived/f.jpg",
    "https://169.254.169.254/latest/meta-data/",
    "https://a2-media.s3.us-east-1.amazonaws.com.evil.example/derived/f.jpg",
    "https://other-bucket.s3.us-east-1.amazonaws.com/derived/f.jpg",
    "https://a2-media.s3.us-east-1.amazonaws.com/incoming/private.jpg",
    "https://a2-media.s3.us-east-1.amazonaws.com/derived/%2e%2e/private.jpg",
    "https://a2-media.s3.us-east-1.amazonaws.com:8443/derived/f.jpg",
    "https://user:password@a2-media.s3.us-east-1.amazonaws.com/derived/f.jpg",
    "https://a2-media.s3.us-east-1.amazonaws.com/derived/f.jpg#fragment",
])
def test_reject_ssrf_and_unapproved_sources(settings, url):
    with pytest.raises(ServiceError) as error:
        validate_source_url(url, settings)
    assert error.value.code == "SOURCE_NOT_ALLOWED"


def image_bytes():
    output = io.BytesIO()
    Image.new("RGB", (32, 16), color="orange").save(output, format="JPEG")
    return output.getvalue()


def test_download_checksum_decode_and_cleanup(settings, tmp_path):
    data = image_bytes()
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=data))) as client:
        image = fetch_image(URL, hashlib.sha256(data).hexdigest(), tmp_path, settings, time.monotonic() + 10, client)
        assert image.size == (32, 16)
        assert image.mode == "RGB"
        image.close()
    assert list(tmp_path.iterdir()) == []


def test_checksum_mismatch_cleans_download(settings, tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=image_bytes()))) as client:
        with pytest.raises(ServiceError) as error:
            fetch_image(URL, "0" * 64, tmp_path, settings, time.monotonic() + 10, client)
        assert error.value.code == "FRAME_CHECKSUM_MISMATCH"
    assert list(tmp_path.iterdir()) == []


def test_redirect_not_followed(settings, tmp_path):
    calls = []
    def response(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})
    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        with pytest.raises(ServiceError):
            fetch_image(URL, "0" * 64, tmp_path, settings, time.monotonic() + 10, client)
    assert calls == [URL]
    assert list(tmp_path.iterdir()) == []
