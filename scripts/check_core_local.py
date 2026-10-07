"""Actual cached models + production core logic, with explicit local I/O adapters.

No cloud, Cognito, Rekognition or mail claim; no seeded READY/media detections.
Both image variants originate from one already-used public COCO sample.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
OUT = None
sys.path[:0] = [str(ROOT / "services/inference"), str(ROOT / "services/aws"), str(ROOT / "packages/domain")]
from PIL import Image
from app.schemas import InferRequest, EmbedRequest
from app.settings import Settings
from app.worker import InferenceWorker
from ecoguard_domain import DomainError, Principal
from ecoguard_aws.api import dispatch
from ecoguard_aws.config import Config
from ecoguard_aws.media_processing import Preprocessor
from ecoguard_aws.repository import MemoryRepository
from ecoguard_aws.service import Service
from ecoguard_aws.storage import MemoryStorage
from ecoguard_aws.workers import Workers, message_jobs
ORIGINAL_CONNECT = socket.socket.connect


def no_network(sock, address):
    # Windows asyncio uses a local socketpair internally; external connections
    # remain prohibited, and ASGITransport does not bind a local HTTP server.
    if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
        return ORIGINAL_CONNECT(sock, address)
    raise RuntimeError("Outbound networking disabled for offline acceptance")


def telemetry(event, **values):
    path = Path(os.environ["ECOGUARD_ACCEPTANCE_TELEMETRY"])
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": event, "monotonic": time.monotonic(), **values}) + "\n")


def offline_child(connection, settings):
    """Keep the actual killable child/runtime, substitute only S3 frame transport."""
    from app import runtime as module
    socket.socket.connect = no_network
    original_detect = module.ModelRuntime.detect
    original_embed = module.ModelRuntime.image_embedding

    def fetch(url, expected_digest, directory, _settings, _deadline):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != "offline-acceptance.invalid":
            raise RuntimeError("Unexpected local fixture URL")
        name = Path(parsed.path).name
        if name != expected_digest + ".jpg":
            raise RuntimeError("Local fixture path mismatch")
        content = (Path(os.environ["ECOGUARD_ACCEPTANCE_MEDIA_ROOT"]) / name).read_bytes()
        if hashlib.sha256(content).hexdigest() != expected_digest:
            raise RuntimeError("Local fixture checksum mismatch")
        target = directory / name
        target.write_bytes(content)
        telemetry("frame_loaded", directory=str(directory), bytes=len(content))
        with Image.open(io.BytesIO(content)) as picture:
            return picture.convert("RGB")

    def detect(self, image, deadline):
        started = time.monotonic()
        result = original_detect(self, image, deadline)
        telemetry("detect_complete", seconds=round(time.monotonic() - started, 3))
        return result

    def embed(self, image, deadline):
        result = original_embed(self, image, deadline)
        telemetry("image_embed_complete")
        return result

    module.fetch_image = fetch
    module.ModelRuntime.detect = detect
    module.ModelRuntime.image_embedding = embed
    module.child_main(connection, settings)


def local_frame(content, root, frame_id="0", timestamp=0):
    digest = hashlib.sha256(content).hexdigest()
    (root / (digest + ".jpg")).write_bytes(content)
    return {"frameId": frame_id, "timestampSeconds": timestamp,
            "url": f"https://offline-acceptance.invalid/{digest}.jpg", "sha256": digest,
            "entryLabels": [], "entryModelVersion": "explicit-no-Rekognition-fixture"}


def events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line] if path.exists() else []




class EmptyEntryFixture:
    def __init__(self):
        self.calls = 0

    def detect_labels(self, **_kwargs):
        self.calls += 1
        return {"Labels": [], "LabelModelVersion": "empty-local-entry-fixture-not-Rekognition"}


class LocalQueue:
    def __init__(self):
        self.pending, self.delivered, self.notification_events = [], [], []

    def send(self, kind, payload):
        self.pending.append((kind, payload))

    def drain(self, workers):
        for _ in range(100):
            workers.dispatch()
            if not self.pending:
                return
            kind, payload = self.pending.pop(0)
            if kind == "NOTIFY":
                self.notification_events.append(payload)
            else:
                getattr(workers, {"INGEST": "ingest", "INFER": "infer", "DELETE": "delete"}[kind])(payload["entityId"])
            self.delivered.append({"kind": kind, "eventId": payload.get("eventId")})
        raise RuntimeError("Local queue drain limit exceeded")


class RealModelAdapter:
    def __init__(self, worker, storage, frame_root, cancellation, exports):
        self.worker, self.storage, self.frame_root = worker, storage, frame_root
        self.cancellation, self.exports = cancellation, exports
        self.calls = []

    def authorize_query(self, principal, request_id, purpose):
        return {"sub": principal.sub, "requestId": request_id}

    def infer(self, payload):
        mapped = dict(payload)
        mapped["frames"] = []
        for frame in payload["frames"]:
            content, _ = self.storage.resolve_read(frame["url"].rsplit("/", 1)[1])
            if hashlib.sha256(content).hexdigest() != frame["sha256"]:
                raise ValueError("Frame checksum mismatch")
            mapped["frames"].append(local_frame(content, self.frame_root, frame["frameId"], frame["timestampSeconds"]))
        InferRequest.model_validate(mapped)
        start = time.monotonic()
        result = self.worker.run("infer", mapped, self.cancellation)
        record = {"jobId": payload["jobId"], "operation": "infer", "frames": len(payload["frames"]),
                  "counts": result["counts"], "features": len(result["features"]), "seconds": round(time.monotonic()-start, 3)}
        self.calls.append(record)
        self.exports["inferenceResults"].append({"jobId": payload["jobId"], "frameSha256": [x["sha256"] for x in payload["frames"]], "result": result})
        print(json.dumps(record), flush=True)
        return result

    def embed(self, payload):
        EmbedRequest.model_validate(payload)
        start = time.monotonic()
        result = self.worker.run("embed", payload, self.cancellation)
        self.calls.append({"jobId": payload["jobId"], "operation": "embed", "seconds": round(time.monotonic()-start, 3)})
        digest = hashlib.sha256(payload["text"].encode()).hexdigest()
        self.exports["textEmbeddings"][digest] = {"text": payload["text"], "textSha256": digest, **result}
        return result


def save(report, exports):
    (OUT / "local-core-e2e.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (OUT / "local-core-real-features.json").write_text(json.dumps(exports, separators=(",", ":")), encoding="utf-8")


def main():
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, required=True, help="Existing two-cat COCO PNG; never downloaded by this tool.")
    parser.add_argument("--output-dir", type=Path, required=True, help="New evidence directory; must not already exist.")
    parser.add_argument("--models", type=Path, default=ROOT / "models", help="Previously verified offline model cache.")
    args = parser.parse_args()
    source = args.sample.resolve().read_bytes()
    expected = "cf6f3c4befa148732c7453e0de5afab00f682427435fead2d88b07a9615cdac2"
    if hashlib.sha256(source).hexdigest() != expected:
        parser.error("The sample SHA-256 differs from the fixed two-cat count fixture.")
    OUT = args.output_dir.resolve()
    OUT.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")
    socket.socket.connect = no_network
    manifest_path = ROOT / "config/model.locked.json"
    manifest = json.loads(manifest_path.read_text())
    settings = Settings(manifest_path=str(manifest_path), manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                        taxonomy_path=str(ROOT / "config/taxonomy.json"), model_cache_dir=str(args.models.resolve()), torch_threads=4, inference_timeout=330)
    cfg = Config(mode="local", model_manifest_version=manifest["manifestVersion"])
    repo, storage, queue, entry = MemoryRepository(), MemoryStorage(cfg), LocalQueue(), EmptyEntryFixture()
    telemetry_path = OUT / "local-core-e2e-telemetry.jsonl"
    telemetry_path.write_text("", encoding="utf-8")
    os.environ["ECOGUARD_ACCEPTANCE_TELEMETRY"] = str(telemetry_path)
    worker = InferenceWorker(settings, target=offline_child)
    cancellation = threading.Event()
    deadline = threading.Timer(300, cancellation.set)
    deadline.daemon = True
    deadline.start()
    with Image.open(io.BytesIO(source)) as image:
        with image.crop((0, 0, image.width//2, image.height)) as crop:
            buffer = io.BytesIO()
            crop.save(buffer, format="PNG")
            variant = buffer.getvalue()
    report = {"schemaVersion": "1", "createdAtUtc": datetime.now(timezone.utc).isoformat(), "status": "running",
              "scope": {"realModels": True, "cloud": False, "cognito": False, "rekognition": False, "mail": False,
                        "storage": "MemoryStorage", "database": "MemoryRepository", "queue": "LocalQueue driven by production Outbox/Workers",
                        "identity": "explicit local principals", "heavyModelConcurrency": 1, "outboundNetworkBlocked": True,
                        "wholeRunCancellationBudgetSeconds": 300},
              "sampleCaveat": "Full COCO image and a crop of the same image, plus reused query bytes. Near-duplicate source, not an independent pet-identity or accuracy evaluation set.",
              "manifestVersion": manifest["manifestVersion"], "candidateScoreThreshold": cfg.pet_score_threshold,
              "checks": [], "queries": {}, "knownQualityObservations": []}
    exports = {"schemaVersion": "1", "manifestVersion": manifest["manifestVersion"], "manifestSha256": settings.manifest_sha256,
               "source": {"path": str(args.sample.resolve()), "sha256": hashlib.sha256(source).hexdigest(),
                          "variantSha256": hashlib.sha256(variant).hexdigest(), "relationship": "same existing source image; crop is not an independent pet sample"},
               "inferenceResults": [], "textEmbeddings": {}, "libraryFeatureSnapshots": []}
    alice, bob = Principal("local-core-alice"), Principal("local-core-bob")

    def check(name, condition, **detail):
        report["checks"].append({"name": name, "passed": bool(condition), **detail})
        save(report, exports)
        if not condition:
            raise AssertionError(name)

    def call(principal, method, path, body=None, params=None):
        return dispatch(service, principal, method, "/api/v1/"+path, body, params)[1]

    def metadata(content, name):
        return {"filename": name, "contentType": "image/png", "sizeBytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}

    def denied(name, action):
        try:
            action()
        except DomainError as error:
            check(name, error.status == 404, code=error.code)
        else:
            check(name, False)

    def query(name, kind, content=None, text=None):
        body = {"type": kind}
        if content is not None:
            body.update(metadata(content, name+".png"))
        if text:
            body["description"] = text
        before = len(list(repo.all("media"))), len(queue.notification_events)
        item = call(bob if kind == "pet" else alice, "POST", "query-jobs", body)
        if content is not None:
            storage.accept_upload(item["uploadUrl"].rsplit("/", 1)[1], content)
            call(bob if kind == "pet" else alice, "POST", f"query-jobs/{item['queryId']}/complete")
        queue.drain(workers)
        result = call(bob if kind == "pet" else alice, "GET", f"query-jobs/{item['queryId']}")
        op = service.operation(item["queryId"])
        check(name+" query ready/clean", result["status"] == "QUERY_READY" and result["cleaned"])
        check(name+" query left no input/derived bytes", (not op.get("sourceKey") or op["sourceKey"] not in storage.objects)
              and not storage.keys(f"derived/{item['queryId']}/"))
        check(name+" query created no media/notification", before == (len(list(repo.all("media"))), len(queue.notification_events)))
        report["queries"][name] = {"queryId": item["queryId"], "status": result["status"], "cleaned": result["cleaned"],
                                   "items": [{k: row.get(k) for k in ("mediaId", "counts", "matchScore", "matchReason")} for row in result.get("items", [])]}
        return item, result

    try:
        with tempfile.TemporaryDirectory(prefix="ecoguard-local-core-") as directory:
            frame_root = Path(directory)
            os.environ["ECOGUARD_ACCEPTANCE_MEDIA_ROOT"] = str(frame_root)
            adapter = RealModelAdapter(worker, storage, frame_root, cancellation, exports)
            service = Service(cfg, repo, storage, inference=adapter)
            workers = Workers(service, Preprocessor(cfg, storage, entry), queue)
            uploads = []
            for name, content, visibility in (("source", source, "public"), ("same-source-crop", variant, "private")):
                item = call(alice, "POST", "uploads", {**metadata(content, name+".png"), "visibility": visibility})
                key = storage.accept_upload(item["uploadUrl"].rsplit("/", 1)[1], content)
                # Use the real S3-event decoder against an explicitly local event envelope.
                event = {"body": json.dumps({"Records": [{"eventSource": "aws:s3", "s3": {"object": {"key": key}}}]})}
                for job in message_jobs(event):
                    workers.ingest(job["entityId"])
                queue.drain(workers)
                media = call(alice, "GET", f"media/{item['mediaId']}")
                check(name+" genuine model result reached READY", media["status"] == "READY" and media["modelVersion"] == manifest["manifestVersion"])
                check(name+" actual cat count", media["counts"] == {"cat": 2 if name == "source" else 1}, actual=media["counts"])
                record = service.media(item["mediaId"], alice)
                exports["libraryFeatureSnapshots"].append({"mediaId": item["mediaId"], "sourceSha256": hashlib.sha256(content).hexdigest(),
                    "visibility": visibility, "counts": media["counts"], "features": json.loads(storage.read(record["featureKey"]))["features"]})
                before_calls = len(adapter.calls)
                workers.ingest(item["jobId"])
                check(name+" duplicate event did not infer twice", len(adapter.calls) == before_calls)
                uploads.append(item)
            public_id, private_id = [x["mediaId"] for x in uploads]
            duplicate = call(alice, "POST", "uploads", metadata(source, "renamed-same-bytes.png"))
            check("same owner same content renamed deduplicates", duplicate["duplicate"] and duplicate["mediaId"] == public_id and len(list(repo.all("media"))) == 2)
            check("no artifact ledger left after real publication", not [x for x in repo.all("operations") if x.get("type") == "ARTIFACTS"])
            full = call(alice, "POST", "search/tags", {"minimumCounts": {"cat": 1}})
            check("real indexed count search owner sees both", {x["mediaId"] for x in full["items"]} == {public_id, private_id})
            check("real indexed count search other sees public only", [x["mediaId"] for x in call(bob, "POST", "search/tags", {"minimumCounts": {"cat": 1}})["items"]] == [public_id])
            check("minimum count two selects source", [x["mediaId"] for x in call(alice, "POST", "search/tags", {"minimumCounts": {"cat": 2}})["items"]] == [public_id])
            page1 = call(alice, "GET", "media", params={"limit": 1})
            page2 = call(alice, "GET", "media", params={"limit": 1, "cursor": page1["nextCursor"]})
            check("real gallery cursor covers both exactly once", {page1["items"][0]["mediaId"], page2["items"][0]["mediaId"]} == {public_id, private_id} and page2["nextCursor"] is None)
            denied("private media lookup denied", lambda: call(bob, "GET", f"media/{private_id}"))
            media = call(alice, "GET", f"media/{public_id}")
            thumbnail = media["thumbnailUrl"]
            thumb_bytes, _ = storage.resolve_read(thumbnail.rsplit("/", 1)[1])
            with Image.open(io.BytesIO(thumb_bytes)) as image:
                check("real thumbnail decodes within 480px", image.format == "JPEG" and max(image.size) <= 480, size=list(image.size))
            resolved = call(bob, "POST", "search/thumbnail", {"thumbnailUrl": thumbnail})
            original, _ = storage.resolve_read(resolved["originalUrl"].rsplit("/", 1)[1])
            check("thumbnail reverse resolves real archived original bytes", resolved["mediaId"] == public_id and original == source)
            _, file_result = query("file-image", "file", source)
            check("real file query AND uses actual indexed species", {x["mediaId"] for x in file_result["items"]} == {public_id, private_id})
            photo_query, photo_result = query("pet-image", "pet", source)
            check("same-source photo pet query yields only public candidate", [x["mediaId"] for x in photo_result["items"]] == [public_id])
            check("pet image result includes calibrated candidate score and image reason", 0.5 <= photo_result["items"][0]["matchScore"] <= 1 and "Image similarity" in photo_result["items"][0]["matchReason"])
            _, text_result = query("pet-text", "pet", text="a photo of a cat")
            _, irrelevant_text = query("pet-text-negative", "pet", text="a photo of a red car on a road")
            _, combined = query("pet-combined", "pet", source, "a photo of a cat")
            _, contradictory = query("pet-combined-negative", "pet", source, "a photo of a red car on a road")
            check("combined result explains both modalities", bool(combined["items"]) and "Image and description" in combined["items"][0]["matchReason"])
            check("matching generic cat text yields candidate", public_id in [x["mediaId"] for x in text_result["items"]])
            check("irrelevant car text returns no candidate", not irrelevant_text["items"])
            check("photo cannot override irrelevant car text", not contradictory["items"])
            report["knownQualityObservations"] = [
                {"case": "matching text", "candidateReturned": public_id in [x["mediaId"] for x in text_result["items"]], "requiredForQuality": True},
                {"case": "irrelevant text", "candidateReturned": bool(irrelevant_text["items"]), "requiredForQuality": False},
                {"case": "photo plus irrelevant text", "candidateReturned": bool(contradictory["items"]), "note": "Contradictory description must be rejected by the calibrated dual-modality gate; this fixture does not prove identity."},
            ]
            denied("another user cannot read pet query", lambda: call(alice, "GET", f"query-jobs/{photo_query['queryId']}"))
            call(alice, "PATCH", f"media/{public_id}", {"visibility": "private"})
            check("existing query rechecks current visibility", call(bob, "GET", f"query-jobs/{photo_query['queryId']}")["items"] == [])
            call(alice, "PATCH", f"media/{public_id}", {"visibility": "public"})
            url = call(alice, "GET", f"media/{public_id}")["url"]
            add = call(alice, "POST", "media/tags", {"urls": [url], "tags": ["ginger"], "operation": 1})
            check("real record tag update is indexed for AND", add["items"][0]["status"] == "updated" and [x["mediaId"] for x in call(bob, "POST", "search/tags", {"minimumCounts": {"cat": 2, "ginger": 1}})["items"]] == [public_id])
            check("same tag update remains no-op", call(alice, "POST", "media/tags", {"urls": [url], "tags": ["ginger"], "operation": 1})["items"][0]["status"] == "unchanged")
            call(alice, "POST", "media/tags", {"urls": [url], "tags": ["cat"], "operation": 0})
            check("manual animal removal removes effective index/count", call(bob, "POST", "search/tags", {"minimumCounts": {"cat": 1}})["items"] == [])
            call(alice, "POST", "media/tags", {"urls": [url], "tags": ["cat"], "operation": 1})
            check("restoring species preserves actual automatic count", call(alice, "GET", f"media/{public_id}")["counts"]["cat"] == 2)
            denied("nonowner cannot edit tags", lambda: call(bob, "POST", "media/tags", {"urls": [url], "tags": ["red"], "operation": 1}))
            cancelled = call(alice, "POST", "query-jobs", {"type": "file", **metadata(source, "cancel.png")})
            storage.accept_upload(cancelled["uploadUrl"].rsplit("/", 1)[1], source)
            call(alice, "POST", f"query-jobs/{cancelled['queryId']}/cancel")
            workers.cleanup()
            cancelled_op = service.operation(cancelled["queryId"])
            check("cancelled uploaded query input is removed", cancelled_op["cleaned"] and cancelled_op["sourceKey"] not in storage.objects)
            invalid = b"not-valid-png-but-checksum-is-correct"
            invalid_query = call(alice, "POST", "query-jobs", {"type": "file", **metadata(invalid, "invalid.png")})
            storage.accept_upload(invalid_query["uploadUrl"].rsplit("/", 1)[1], invalid)
            call(alice, "POST", f"query-jobs/{invalid_query['queryId']}/complete")
            queue.drain(workers)
            invalid_op = service.operation(invalid_query["queryId"])
            check("true image decoder rejects invalid query and cleans", invalid_op["status"] == "FAILED" and invalid_op["error"]["code"] == "INVALID_IMAGE" and invalid_op["cleaned"] and invalid_op["sourceKey"] not in storage.objects)
            urls = [call(alice, "GET", f"media/{x['mediaId']}")["url"] for x in uploads]
            delete = call(alice, "POST", "media/delete", {"urls": urls})
            queue.drain(workers)
            check("both actual delete jobs complete", all(call(alice, "GET", f"jobs/{x['jobId']}")["status"] == "DELETED" for x in delete["items"]))
            check("originals thumbnails frames and feature objects all deleted", not storage.objects, objectCount=len(storage.objects))
            check("media and search indexes both empty", not list(repo.all("media")) and not list(repo.all("index")))
            check("dedup and artifact ledgers released", not [x for x in repo.all("operations") if x.get("type") in ("DEDUP", "ARTIFACTS")])
            check("completed pet query cannot reveal deleted media", call(bob, "GET", f"query-jobs/{photo_query['queryId']}")["items"] == [])
            denied("deleted thumbnail reverse returns not found", lambda: call(alice, "POST", "search/thumbnail", {"thumbnailUrl": thumbnail}))
            report["models"] = {"calls": adapter.calls, "entryEmptyFixtureCalls": entry.calls}
            report["queue"] = {"delivered": queue.delivered, "notificationEventsHeldWithoutSending": len(queue.notification_events)}
            report["status"] = "local-functional-chain-passed"
        report["fixtureDirectoryCleaned"] = not frame_root.exists()
    except Exception as error:
        report["status"] = "failed"
        report["error"] = {"type": type(error).__name__, "message": str(error)[:300]}
    finally:
        deadline.cancel()
        worker.close()
        trace = events(telemetry_path)
        directories = {x["directory"] for x in trace if x["event"] == "frame_loaded"}
        report["telemetry"] = {"file": telemetry_path.name, "detectorFramesCompleted": sum(x["event"] == "detect_complete" for x in trace),
            "imageEmbeddingsCompleted": sum(x["event"] == "image_embed_complete" for x in trace),
            "allRequestDirectoriesCleaned": all(not Path(path).exists() for path in directories)}
        report["modelChildStopped"] = worker.process is None
        report["elapsedSeconds"] = round(time.monotonic()-started, 3)
        report["functionalChecksPassed"] = sum(x["passed"] for x in report["checks"])
        report["functionalChecksFailed"] = sum(not x["passed"] for x in report["checks"])
        report["finalLocalRows"] = {name: len(list(repo.all(name))) for name in repo.data}
        report["finalLocalObjects"] = len(storage.objects)
        # All objects here are the acceptance harness's private in-memory test data.
        storage.objects.clear()
        save(report, exports)
        print(json.dumps({k: report[k] for k in ("status", "elapsedSeconds", "functionalChecksPassed", "functionalChecksFailed", "finalLocalObjects", "telemetry")}), flush=True)
    return 0 if report["status"] == "local-functional-chain-passed" and report["modelChildStopped"] and report["telemetry"]["allRequestDirectoriesCleaned"] and not report["finalLocalObjects"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
