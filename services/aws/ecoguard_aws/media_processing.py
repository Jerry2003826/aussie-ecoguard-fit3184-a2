from __future__ import annotations
import hashlib
import io
import json
import subprocess
import tempfile
from pathlib import Path
from ecoguard_domain import DomainError
from ecoguard_domain.rules import sample_times


class Preprocessor:
    def __init__(self, config, storage, rekognition=None):
        self.config, self.storage, self.rekognition = config, storage, rekognition

    def _command(self, args, timeout):
        try:
            return subprocess.run(args, check=True, capture_output=True, timeout=timeout).stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as error:
            raise DomainError("MEDIA_DECODE_FAILED", "The media could not be decoded within its processing limit.") from error

    def process(self, operation):
        from PIL import Image, ImageOps, UnidentifiedImageError
        Image.MAX_IMAGE_PIXELS = 60_000_000
        data = self.storage.read(operation["sourceKey"])
        if len(data) != operation["sizeBytes"] or hashlib.sha256(data).hexdigest() != operation["sha256"]:
            raise DomainError("CHECKSUM_MISMATCH", "Processing input failed its content checksum.")
        prefix = f"derived/{operation.get('mediaId', operation['operationId'])}/{operation['generation']}/{operation.get('attemptId', 0)}/"
        frames = []
        generated = []
        with tempfile.TemporaryDirectory(prefix="ecoguard-") as temp:
            root = Path(temp)
            if operation["fileType"] == "video":
                source = root / "source.mp4"
                source.write_bytes(data)
                info = json.loads(self._command([self.config.ffprobe, "-v", "error", "-protocol_whitelist", "file,pipe", "-show_entries", "format=duration,format_name", "-of", "json", str(source)], 15))
                fmt = info.get("format", {})
                if not set(str(fmt.get("format_name", "")).split(",")) & {"mov", "mp4"}:
                    raise DomainError("INVALID_MEDIA_TYPE", "The uploaded file is not an MP4 video.")
                times = sample_times(float(fmt.get("duration", "nan")))
                for timestamp in times:
                    target = root / f"frame-{timestamp}.jpg"
                    self._command([self.config.ffmpeg, "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe", "-ss", str(timestamp), "-i", str(source), "-frames:v", "1", "-vf", "scale=1600:1600:force_original_aspect_ratio=decrease", "-q:v", "3", "-y", str(target)], 15)
                    frames.append((timestamp, target.read_bytes()))
            else:
                try:
                    with Image.open(io.BytesIO(data)) as image:
                        expected = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}[operation["contentType"]]
                        if image.format != expected:
                            raise DomainError("INVALID_MEDIA_TYPE", "File bytes do not match the declared image format.")
                        if image.width * image.height > Image.MAX_IMAGE_PIXELS:
                            raise DomainError("INVALID_IMAGE", "The image exceeds the 60 megapixel processing limit.")
                        image = ImageOps.exif_transpose(image).convert("RGB")
                        image.thumbnail((1600, 1600))
                        output = io.BytesIO()
                        image.save(output, format="JPEG", quality=88, optimize=True)
                        frames.append((0, output.getvalue()))
                except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
                    raise DomainError("INVALID_IMAGE", "The image is invalid or exceeds its pixel limit.") from error
            if not frames:
                raise DomainError("EMPTY_VIDEO", "No frames could be decoded.")
            thumbnail = None
            result = []
            for index, (timestamp, frame) in enumerate(frames):
                frame_key = prefix + f"frame-{index}.jpg"
                self.storage.put(frame_key, frame, "image/jpeg")
                generated.append(frame_key)
                if index == 0:
                    with Image.open(io.BytesIO(frame)) as image:
                        image.thumbnail((480, 480))
                        small = io.BytesIO()
                        image.save(small, format="JPEG", quality=75, optimize=True)
                        thumbnail = prefix + "thumbnail.jpg"
                        self.storage.put(thumbnail, small.getvalue(), "image/jpeg")
                        generated.append(thumbnail)
                if self.rekognition is None:
                    raise DomainError("REKOGNITION_UNCONFIGURED", "The required entry model is not configured. No simulated labels were generated.", 503)
                detection = self.rekognition.detect_labels(Image={"Bytes": frame}, MaxLabels=50, MinConfidence=65, Features=["GENERAL_LABELS"])
                result.append({"frameId": str(index), "timestampSeconds": timestamp, "key": frame_key, "sha256": hashlib.sha256(frame).hexdigest(), "entryLabels": detection.get("Labels", []), "entryModelVersion": detection.get("LabelModelVersion", "unknown")})
        return {"frames": result, "objectKeys": generated, "thumbnailKey": thumbnail}
