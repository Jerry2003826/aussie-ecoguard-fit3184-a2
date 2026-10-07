"""Explicit offline acceptance: real FFmpeg/models, in-memory storage, no cloud claims."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "services/inference"), str(ROOT / "services/aws"), str(ROOT / "packages/domain")]
from PIL import Image
from app.runtime import ModelRuntime
from app.schemas import InferRequest
from app.settings import Settings
from ecoguard_domain import Principal
from ecoguard_aws.config import Config
from ecoguard_aws.media_processing import Preprocessor
from ecoguard_aws.repository import MemoryRepository
from ecoguard_aws.service import Service
from ecoguard_aws.storage import MemoryStorage
from ecoguard_aws.workers import Workers


class EmptyEntryModelFixture:
    """Rekognition is independently cloud-tested by the root agent, not by this fixture."""
    def __init__(self):
        self.calls = 0

    def detect_labels(self, **kwargs):
        self.calls += 1
        return {"Labels": [], "LabelModelVersion": "explicit-no-Rekognition-fixture"}


class OfflineModelAdapter:
    def __init__(self, runtime, storage):
        self.runtime, self.storage = runtime, storage
        self.calls = []

    def authorize_query(self, principal, request_id, purpose):
        # This binds the local harness identity only; it is not Cognito validation.
        return {"sub": principal.sub, "requestId": request_id}

    def fetch_local_frame(self, url, digest, directory, settings, deadline):
        content, _ = self.storage.resolve_read(url.rsplit("/", 1)[1])
        assert hashlib.sha256(content).hexdigest() == digest
        with Image.open(io.BytesIO(content)) as image:
            return image.convert("RGB")

    def infer(self, payload):
        request = InferRequest.model_validate(payload)
        start = time.monotonic()
        print(f"Real inference starting: {len(request.frames)} frame(s)", flush=True)
        with tempfile.TemporaryDirectory(prefix="ecoguard-real-video-") as directory:
            with patch("app.runtime.fetch_image", self.fetch_local_frame):
                response = self.runtime.infer(request, Path(directory), time.monotonic()+180)
        assert not Path(directory).exists()
        self.calls.append({"frameTimes": [f.timestampSeconds for f in request.frames],
                           "frameCounts": [f["counts"] for f in response["frames"]],
                           "counts": response["counts"], "features": len(response["features"]),
                           "seconds": round(time.monotonic()-start, 3), "tempDirectoryCleaned": True})
        print(json.dumps(self.calls[-1]), flush=True)
        return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--ffprobe", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    # Cached artifacts were independently SHA-256 verified before this run.
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Networking disabled for local video acceptance"))
    manifest_path = ROOT / "config/model.locked.json"
    manifest = json.loads(manifest_path.read_text())
    cfg = Config(mode="local", model_manifest_version=manifest["manifestVersion"], ffmpeg=args.ffmpeg, ffprobe=args.ffprobe)
    repo, storage = MemoryRepository(), MemoryStorage(cfg)
    runtime = ModelRuntime(Settings(manifest_path=str(manifest_path), manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                                    taxonomy_path=str(ROOT / "config/taxonomy.json"), model_cache_dir=str(ROOT / "models"), torch_threads=4))
    adapter, entry = OfflineModelAdapter(runtime, storage), EmptyEntryModelFixture()
    service = Service(cfg, repo, storage, inference=adapter)
    workers = Workers(service, Preprocessor(cfg, storage, entry))
    principal = Principal("offline-real-model-user")
    outputs = {}
    for duration in (4, 24):
        video = args.output_dir / f"cats-{duration}s.mp4"
        subprocess.run([args.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-loop", "1", "-framerate", "1",
                        "-i", str(ROOT / "tmp/model-smoke/coco_sample.png"), "-t", str(duration), "-c:v", "libx264",
                        "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-y", str(video)], check=True, timeout=20)
        content = video.read_bytes()
        metadata = {"filename": video.name, "contentType": "video/mp4", "sizeBytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        upload = service.create_upload(principal, metadata) if duration == 4 else service.create_query(principal, {"type": "file", **metadata})
        storage.accept_upload(upload["uploadUrl"].rsplit("/", 1)[1], content)
        workers.ingest(upload["jobId"])
        frames = service.operation(upload["jobId"])["frames"]
        assert [f["timestampSeconds"] for f in frames] == ([0] if duration == 4 else [0, 10, 20])
        workers.infer(upload["jobId"])
        outputs[duration] = upload
    assert adapter.calls[0]["counts"] == {"cat": 2}
    assert adapter.calls[1]["frameCounts"] == [{"cat": 2}]*3
    assert adapter.calls[1]["counts"] == {"cat": 2}, "Three frames must not become six cats"
    query = service.operation(outputs[24]["jobId"])
    assert query["status"] == "QUERY_READY" and query["cleaned"]
    assert query["resultIds"] == [outputs[4]["mediaId"]]
    assert query["sourceKey"] not in storage.objects
    assert not list(storage.keys(f"derived/{outputs[24]['jobId']}/"))
    assert len(list(repo.all("media"))) == 1
    assert len([x for x in repo.all("outbox") if x["kind"] == "NOTIFY"]) == 1
    media = service.media(outputs[4]["mediaId"], principal)
    deletion = service.delete_media(principal, {"urls": [service.media_view(media)["url"]]})
    workers.delete(deletion["items"][0]["jobId"])
    assert not storage.objects and not list(repo.all("media")) and not list(repo.all("index"))
    report = {"status": "passed", "kind": "real-FFmpeg-real-models-local-adapter-pipeline", "manifestVersion": manifest["manifestVersion"],
              "calls": adapter.calls, "entryModelFixtureCalls": entry.calls, "realDetectorFrames": 4,
              "realImageEmbeddingCalls": sum(x["features"] for x in adapter.calls),
              "querySourceAndDerivedCleaned": True, "libraryDeleteLeftZeroObjects": True, "queryDidNotCreateMediaOrNotification": True,
              "networkDisabled": True, "cloudValidated": False, "realRekognitionInThisRun": False,
              "accuracyBenchmarkPassed": False, "limits": "One public sample repeated into 4s/24s videos; in-memory storage/identity adapters. Not a 120s, diverse-species, or cloud acceptance result."}
    (args.output_dir / "real-video-pipeline.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"status": "passed", "report": str(args.output_dir / "real-video-pipeline.json")}), flush=True)


if __name__ == "__main__":
    main()
