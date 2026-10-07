"""Bounded real 120s/12-frame video regression with explicit offline transport.

Memory database/storage and empty Rekognition fixture; no cloud/identity claims.
Requires verified cached model weights. Refuses to overwrite prior evidence.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from datetime import datetime, timezone

from check_core_local import ROOT, offline_child, no_network, local_frame, events, EmptyEntryFixture
from PIL import Image
from app.errors import ServiceError
from app.schemas import InferRequest
from app.settings import Settings
from app.worker import InferenceWorker
from ecoguard_domain import Principal
from ecoguard_aws.config import Config
from ecoguard_aws.media_processing import Preprocessor
from ecoguard_aws.repository import MemoryRepository
from ecoguard_aws.service import Service
from ecoguard_aws.storage import MemoryStorage
from ecoguard_aws.workers import Workers

OUT = None
SAMPLE = None


def save(report):
    (OUT / "video-120s.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


class OfflineWorkerAdapter:
    def __init__(self, worker, storage, frame_root):
        self.worker, self.storage, self.frame_root = worker, storage, frame_root
        self.result = None
        self.seconds = None
        self.error = None

    def authorize_query(self, principal, request_id, purpose):
        return {"sub": principal.sub, "requestId": request_id}

    def infer(self, payload):
        mapped = dict(payload)
        mapped["frames"] = []
        for frame in payload["frames"]:
            content, _ = self.storage.resolve_read(frame["url"].rsplit("/", 1)[1])
            assert hashlib.sha256(content).hexdigest() == frame["sha256"]
            mapped["frames"].append(local_frame(content, self.frame_root, frame["frameId"], frame["timestampSeconds"]))
        InferRequest.model_validate(mapped)
        started = time.monotonic()
        try:
            self.result = self.worker.run("infer", mapped)
            return self.result
        except ServiceError as error:
            self.error = {"code": error.code, "status": error.status, "retryable": error.retryable}
            # Preserve production retry semantics: 429/502/503/504 stay transient.
            if error.status in (429, 502, 503, 504):
                raise RuntimeError(f"Offline dependency failure ({error.status})") from error
            raise
        finally:
            self.seconds = round(time.monotonic() - started, 3)


def run_video(worker, settings, frame_root, report):
    ffmpeg = Path(os.environ["USERPROFILE"]) / "scoop/apps/ffmpeg/current/bin/ffmpeg.exe"
    ffprobe = ffmpeg.with_name("ffprobe.exe")
    cfg = Config(mode="local", model_manifest_version=json.loads(Path(settings.manifest_path).read_text())["manifestVersion"],
                 ffmpeg=str(ffmpeg), ffprobe=str(ffprobe))
    repo, storage = MemoryRepository(), MemoryStorage(cfg)
    adapter, entry = OfflineWorkerAdapter(worker, storage, frame_root), EmptyEntryFixture()
    service = Service(cfg, repo, storage, inference=adapter)
    pipeline = Workers(service, Preprocessor(cfg, storage, entry))
    principal = Principal("offline-extended-acceptance")
    video = OUT / "cats-120s.mp4"
    start = time.monotonic()
    subprocess.run([str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-loop", "1", "-framerate", "1",
                    "-i", str(SAMPLE), "-t", "120", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-y", str(video)], check=True, timeout=30)
    info = json.loads(subprocess.check_output([str(ffprobe), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video)], timeout=15))
    content = video.read_bytes()
    sample = {"filename": video.name, "contentType": "video/mp4", "sizeBytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    upload = service.create_query(principal, {"type": "file", **sample})
    storage.accept_upload(upload["uploadUrl"].rsplit("/", 1)[1], content)
    prep_start = time.monotonic()
    pipeline.ingest(upload["jobId"])
    prep_seconds = round(time.monotonic() - prep_start, 3)
    op = service.operation(upload["jobId"])
    times = [frame["timestampSeconds"] for frame in op["frames"]]
    assert times == list(range(0, 120, 10))
    report["video120"] = {"status": "running", "file": video.name, "durationSeconds": float(info["format"]["duration"]),
                           "bytes": len(content), "sha256": sample["sha256"], "frameSeconds": times, "preprocessSeconds": prep_seconds,
                           "entryModelEmptyFixtureCalls": entry.calls, "configuredModelTimeoutSeconds": settings.inference_timeout,
                           "awsPayloadDeadlineSeconds": 330}
    save(report)
    print(json.dumps({"video120": "preprocessed", "frames": times, "seconds": prep_seconds}), flush=True)
    failure = None
    try:
        pipeline.infer(upload["jobId"])
    except Exception as error:
        failure = {"type": type(error).__name__, "serviceError": adapter.error}
    op = service.operation(upload["jobId"])
    result = adapter.result
    natural_cleanup = bool(op.get("cleaned")) and not storage.objects
    # On transient failure, the business worker intentionally retains retry input.
    # Record this before the local acceptance fixture removes its own test data.
    retained_objects = len(storage.objects)
    report["video120"].update({"status": "passed" if result and op["status"] == "QUERY_READY" else "failed",
                               "inferenceSeconds": adapter.seconds, "wholePipelineSeconds": round(time.monotonic() - start, 3),
                               "operationStatus": op["status"], "error": failure, "automaticQueryCleanup": natural_cleanup,
                               "retainedRetryObjectsBeforeHarnessCleanup": retained_objects,
                               "queryDidNotCreateMediaOrNotification": not list(repo.all("media")) and not [e for e in repo.all("outbox") if e["kind"] == "NOTIFY"]})
    if result:
        report["video120"].update({"counts": result["counts"], "frameCounts": [f["counts"] for f in result["frames"]],
                                   "features": len(result["features"]), "truncatedFeatures": result["truncatedFeatures"],
                                   "finishedWithinDefault240Seconds": adapter.seconds < 240})
        if result["counts"] != {"cat": 2} or len(result["frames"]) != 12 or not natural_cleanup:
            report["video120"]["status"] = "failed-result-or-cleanup"
    for key in list(storage.objects):
        storage.delete(key)
    report["video120"]["harnessCleanupLeftZeroStorageObjects"] = not storage.objects
    save(report)
    print(json.dumps(report["video120"]), flush=True)


def main():
    global OUT, SAMPLE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--models", type=Path, default=ROOT / "models")
    args = parser.parse_args()
    SAMPLE = args.sample.resolve()
    if hashlib.sha256(SAMPLE.read_bytes()).hexdigest() != "cf6f3c4befa148732c7453e0de5afab00f682427435fead2d88b07a9615cdac2":
        parser.error("Fixed two-cat COCO sample required")
    OUT = args.output_dir.resolve()
    OUT.mkdir(parents=True, exist_ok=False)
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")
    socket.socket.connect = no_network
    telemetry_path = OUT / "telemetry.jsonl"
    os.environ["ECOGUARD_ACCEPTANCE_TELEMETRY"] = str(telemetry_path)
    path = ROOT / "config/model.locked.json"
    settings = Settings(manifest_path=str(path), manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                        taxonomy_path=str(ROOT / "config/taxonomy.json"), model_cache_dir=str(args.models.resolve()),
                        torch_threads=4, inference_timeout=330)
    worker = InferenceWorker(settings, target=offline_child)
    report = {"checkedAtUtc": datetime.now(timezone.utc).isoformat(), "scope": "real cached models / local transport / empty Rekognition / memory data; no cloud or identity claim",
              "manifestVersion": json.loads(path.read_text())["manifestVersion"], "manifestSha256": settings.manifest_sha256}
    try:
        with tempfile.TemporaryDirectory(prefix="ecoguard-video-regression-") as directory:
            frame_root = Path(directory)
            os.environ["ECOGUARD_ACCEPTANCE_MEDIA_ROOT"] = str(frame_root)
            run_video(worker, settings, frame_root, report)
        report["fixtureDirectoryCleaned"] = not frame_root.exists()
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)[:200]}
    finally:
        worker.close()
        trace = events(telemetry_path)
        directories = {x["directory"] for x in trace if x["event"] == "frame_loaded"}
        report["modelChildStopped"] = worker.process is None
        report["requestDirectoriesCleaned"] = all(not Path(p).exists() for p in directories)
        report["passed"] = (report.get("video120", {}).get("status") == "passed" and report["modelChildStopped"]
                            and report["requestDirectoriesCleaned"] and report.get("fixtureDirectoryCleaned", False))
        save(report)
        print(json.dumps({"passed": report["passed"], "modelChildStopped": report["modelChildStopped"], "requestDirectoriesCleaned": report["requestDirectoriesCleaned"]}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
