"""Deterministic backend acceptance of rules; test doubles are NOT model/cloud evidence."""
from __future__ import annotations
import hashlib
import io
import json
import sys
import time
import unittest
from threading import Event
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "packages/domain"), str(ROOT / "services/aws")]
from ecoguard_domain import DomainError, Principal, can_read, effective_tags, matches
from ecoguard_domain.rules import apply_tags, sample_times, aggregate_counts
from ecoguard_aws.api import handle, dispatch
from ecoguard_aws.config import Config
from ecoguard_aws.repository import MemoryRepository, Write, Conflict
from ecoguard_aws.storage import MemoryStorage
from ecoguard_aws.service import Service
from ecoguard_aws.workers import Workers


class FixtureInference:
    """Only deterministic pipeline fixtures, never an animal detector."""
    def __init__(self):
        self.calls = 0
        self.before_return = None
        self.error = None
        self.counts = {"fox": 2}

    def authorize_query(self, principal, request_id, purpose):
        return {"sub": principal.sub, "requestId": request_id}

    def infer(self, payload):
        self.calls += 1
        if self.error:
            raise self.error
        if self.before_return:
            self.before_return()
        return {"schemaVersion": "1", "manifestVersion": payload["manifestVersion"], "counts": self.counts, "tags": list(self.counts), "classification": "invasive", "features": [{"species": "fox", "vector": [1., 0.], "featureVersion": "test-v1"}]}

    def embed(self, payload):
        return {"vector": [1., 0.], "featureVersion": "test-v1", "manifestVersion": payload["manifestVersion"]}


class FixturePreprocessor:
    def __init__(self, storage):
        self.storage, self.calls = storage, 0

    def process(self, operation):
        self.calls += 1
        self.storage.read(operation["sourceKey"])
        prefix = f"derived/{operation.get('mediaId', operation['operationId'])}/{operation['generation']}/"
        key = prefix + "frame.jpg"
        self.storage.put(key, b"explicit-test-frame", "image/jpeg")
        return {"frames": [{"frameId": "0", "timestampSeconds": 0, "key": key, "sha256": hashlib.sha256(b"explicit-test-frame").hexdigest(), "entryLabels": []}], "objectKeys": [key], "thumbnailKey": key}


class RecordingQueue:
    def __init__(self):
        self.messages = []

    def send(self, kind, payload):
        self.messages.append((kind, payload))


class RecordingNotifier:
    def __init__(self):
        self.messages, self.error = [], None

    def subscribe(self, sub, email):
        return "topic-"+sub, "ACTIVE"

    def confirmation(self, topic, email):
        return "ACTIVE"

    def publish(self, topic, message):
        self.messages.append((topic, message))
        if self.error:
            raise self.error
        return "test-message-id"


class RulesTest(unittest.TestCase):
    def test_sampling_and_maximum_count_do_not_double_count(self):
        self.assertEqual(sample_times(24), [0, 10, 20])
        self.assertEqual(sample_times(4), [0])
        self.assertEqual(len(sample_times(120)), 12)
        self.assertEqual(aggregate_counts([{"counts": {"cat": n}} for n in (1, 3, 2)]), {"cat": 3})
        with self.assertRaises(DomainError):
            sample_times(120.1)

    def test_and_and_manual_labels_preserve_count_semantics(self):
        row = {"status": "READY", "autoTags": ["fox", "cane_toad"], "autoCounts": {"fox": 2, "cane_toad": 3}}
        self.assertTrue(matches(row, {"fox": 2, "cane_toad": 3}))
        self.assertFalse(matches(row, {"fox": 3, "cane_toad": 3}))
        removed = apply_tags(row, ["fox"], 0)
        self.assertFalse(matches(removed, {"fox": 1}))
        added = apply_tags(removed, ["fox", "ginger"], 1)
        self.assertEqual(effective_tags(added)[1]["fox"], 2)
        self.assertNotIn("ginger", effective_tags(added)[1])
        self.assertEqual(apply_tags(added, ["ginger"], 1), added)


class BackendTest(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(mode="local", model_manifest_version="fixture-v1")
        self.repo, self.storage = MemoryRepository(), MemoryStorage(self.cfg)
        self.inference, self.notifier = FixtureInference(), RecordingNotifier()
        self.service = Service(self.cfg, self.repo, self.storage, inference=self.inference, notifier=self.notifier)
        self.queue = RecordingQueue()
        self.workers = Workers(self.service, FixturePreprocessor(self.storage), self.queue)
        self.alice = Principal("alice", "alice@example.invalid", True)
        self.bob = Principal("bob", "bob@example.invalid", True)

    def metadata(self, data=b"explicit fixture content", name="animal.jpg", **kwargs):
        return {"filename": name, "contentType": "image/jpeg", "sizeBytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), **kwargs}

    def upload(self, principal=None, data=b"explicit fixture content", **kwargs):
        principal = principal or self.alice
        result = self.service.create_upload(principal, self.metadata(data, **kwargs))
        token = result["uploadUrl"].rsplit("/", 1)[1]
        self.storage.accept_upload(token, data)
        return result

    def ready(self, principal=None, data=b"explicit fixture content", **kwargs):
        uploaded = self.upload(principal, data, **kwargs)
        self.workers.ingest(uploaded["jobId"])
        self.workers.infer(uploaded["jobId"])
        return self.service.media(uploaded["mediaId"], principal or self.alice)

    def test_checksum_rejects_fake_bytes(self):
        result = self.service.create_upload(self.alice, self.metadata())
        with self.assertRaises(DomainError):
            self.storage.accept_upload(result["uploadUrl"].rsplit("/", 1)[1], b"wrong")

    def test_concurrent_dedup_one_record_per_owner(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            values = list(executor.map(lambda _: self.service.create_upload(self.alice, self.metadata()), range(16)))
        self.assertEqual(sum(not v["duplicate"] for v in values), 1)
        self.assertEqual(len(list(self.repo.all("media"))), 1)
        other = self.service.create_upload(self.bob, self.metadata())
        self.assertFalse(other["duplicate"])
        renamed = self.service.create_upload(self.alice, self.metadata(name="renamed.jpg"))
        self.assertTrue(renamed["duplicate"])
        changed = self.service.create_upload(self.alice, self.metadata(data=b"different bytes"))
        self.assertFalse(changed["duplicate"])

    def test_s3_event_can_arrive_before_complete_and_replay_is_safe(self):
        uploaded = self.upload()
        self.workers.ingest(uploaded["jobId"])
        self.service.complete_upload(self.alice, uploaded["uploadId"])
        self.workers.ingest(uploaded["jobId"])
        self.workers.infer(uploaded["jobId"])
        self.workers.infer(uploaded["jobId"])
        self.assertEqual(self.inference.calls, 1)
        row = self.service.media(uploaded["mediaId"], self.alice)
        self.assertEqual(row["status"], "READY")
        self.assertTrue(row["originalKey"].startswith("library/invasive/"))
        self.assertEqual(len([e for e in self.repo.all("outbox") if e["kind"] == "NOTIFY"]), 1)

    def _delete_during_infer_copy(self, *, copy_before_delete, sweep_before_copy=False):
        uploaded = self.upload()
        self.workers.ingest(uploaded["jobId"])
        copied, release = Event(), Event()

        def paused_copy(source, target):
            # Capture source bytes like an already submitted S3 copy request.
            data, content_type = self.storage.read(source), self.storage.head(source)["contentType"]
            if copy_before_delete:
                self.storage.put(target, data, content_type)
            copied.set()
            if not release.wait(5):
                raise TimeoutError("The controlled deletion did not release the copy")
            if not copy_before_delete:
                self.storage.put(target, data, content_type)

        with patch.object(self.storage, "copy", side_effect=paused_copy), ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.workers.infer, uploaded["jobId"])
            try:
                self.assertTrue(copied.wait(5))
                row = self.service.media(uploaded["mediaId"], self.alice)
                deletion = self.service.delete_media(self.alice, {"urls": [self.service.media_view(row)["url"]]})["items"][0]
                prior = self.service.operation(uploaded["jobId"])
                with patch("ecoguard_aws.workers.time.time", return_value=prior["leaseUntil"]+1):
                    self.workers.delete(deletion["jobId"])
                    if sweep_before_copy:
                        self.workers.cleanup()
                self.assertEqual(self.service.operation(deletion["jobId"])["status"], "DELETED")
            finally:
                release.set()
            error = future.exception(timeout=5)
        self.assertFalse(self.storage.objects, f"Deleted media left stored objects: {list(self.storage.objects)}")
        self.assertIsNone(error, f"A cancelled inference should finish without resurrecting media: {error!r}")
        self.assertIsNone(self.repo.get("media", {"mediaId": uploaded["mediaId"]}))
        self.assertFalse(list(self.repo.all("index")))
        self.assertFalse([e for e in self.repo.all("outbox") if e["kind"] == "NOTIFY"])

    def test_delete_after_inference_copy_before_commit_removes_all_objects(self):
        self._delete_during_infer_copy(copy_before_delete=True)

    def test_copy_finishing_after_delete_cannot_leave_objects_or_resurrect(self):
        self._delete_during_infer_copy(copy_before_delete=False)

    def test_copy_finishing_after_delete_and_sweep_is_still_compensated(self):
        self._delete_during_infer_copy(copy_before_delete=False, sweep_before_copy=True)

    def test_failed_artifact_compensation_is_durable_and_retried(self):
        uploaded = self.upload()
        self.workers.ingest(uploaded["jobId"])
        original_copy = self.storage.copy

        def copy_then_fail(source, target):
            original_copy(source, target)
            raise RuntimeError("Copy response was lost")

        with patch.object(self.storage, "copy", side_effect=copy_then_fail), patch.object(self.storage, "delete", side_effect=RuntimeError("Storage unavailable")):
            with self.assertRaises(RuntimeError):
                self.workers.infer(uploaded["jobId"])
        pending = [o for o in self.repo.all("operations") if o["type"] == "ARTIFACTS"]
        self.assertEqual(len(pending), 1)
        self.assertTrue(all(k in self.storage.objects for k in pending[0]["objectKeys"]))
        self.assertEqual(self.service.operation(uploaded["jobId"])["status"], "FAILED")
        with patch("ecoguard_aws.workers.time.time", return_value=pending[0]["leaseUntil"]+1):
            self.workers.cleanup()
        self.assertTrue(all(k not in self.storage.objects for k in pending[0]["objectKeys"]))
        self.assertFalse([o for o in self.repo.all("operations") if o["type"] == "ARTIFACTS"])
        self.service.retry(self.alice, uploaded["jobId"])
        self.workers.ingest(uploaded["jobId"])
        self.workers.infer(uploaded["jobId"])
        self.assertEqual(self.service.media(uploaded["mediaId"], self.alice)["status"], "READY")

    def test_hard_crash_artifacts_are_fenced_and_cleaned_after_lease(self):
        uploaded = self.upload()
        self.workers.ingest(uploaded["jobId"])
        original_copy = self.storage.copy

        def interrupted_copy(source, target):
            original_copy(source, target)
            raise SystemExit("Simulated process termination after the S3 writes")

        # Skip finally's compensation to model a process that cannot run Python cleanup.
        with patch.object(self.storage, "copy", side_effect=interrupted_copy), patch.object(self.workers, "_clean_artifacts"):
            with self.assertRaises(SystemExit):
                self.workers.infer(uploaded["jobId"])
        pending = next(o for o in self.repo.all("operations") if o["type"] == "ARTIFACTS")
        self.workers.cleanup()
        self.assertEqual(self.service.operation(uploaded["jobId"])["status"], "INFERRING")
        self.assertTrue(all(k in self.storage.objects for k in pending["objectKeys"]))
        with patch("ecoguard_aws.workers.time.time", return_value=pending["leaseUntil"]+1):
            self.workers.cleanup()
        self.assertEqual(self.service.operation(uploaded["jobId"])["status"], "FAILED")
        self.assertTrue(all(k not in self.storage.objects for k in pending["objectKeys"]))
        self.assertFalse([o for o in self.repo.all("operations") if o["type"] == "ARTIFACTS"])

    def test_lost_successful_transaction_response_does_not_remove_published_bytes(self):
        uploaded = self.upload()
        self.workers.ingest(uploaded["jobId"])
        transact = self.repo.transact

        def lost_response(writes):
            transact(writes)
            if any(w.table == "media" and w.item.get("status") == "READY" for w in writes):
                raise RuntimeError("The transaction succeeded but its response was lost")

        with patch.object(self.repo, "transact", side_effect=lost_response):
            with self.assertRaises(RuntimeError):
                self.workers.infer(uploaded["jobId"])
        row = self.service.media(uploaded["mediaId"], self.alice)
        self.assertEqual(row["status"], "READY")
        self.assertIn(row["originalKey"], self.storage.objects)
        self.assertIn(row["featureKey"], self.storage.objects)
        self.assertFalse([o for o in self.repo.all("operations") if o["type"] == "ARTIFACTS"])
        self.workers.infer(uploaded["jobId"])
        self.assertEqual(self.inference.calls, 1)
        deletion = self.service.delete_media(self.alice, {"urls": [self.service.media_view(row)["url"]]})["items"][0]
        self.workers.delete(deletion["jobId"])
        self.assertFalse(self.storage.objects)

    def _late_attempt_cannot_delete_winner(self, *, next_generation):
        uploaded = self.upload()
        self.workers.ingest(uploaded["jobId"])
        entered, release = Event(), Event()
        original_copy = self.storage.copy
        first_target = []

        def paused_first_copy(source, target):
            if entered.is_set():
                return original_copy(source, target)
            data, content_type = self.storage.read(source), self.storage.head(source)["contentType"]
            first_target.append(target)
            entered.set()
            if not release.wait(5):
                raise TimeoutError("Replacement attempt did not release the old copy")
            self.storage.put(target, data, content_type)

        with patch.object(self.storage, "copy", side_effect=paused_first_copy), ThreadPoolExecutor(max_workers=1) as executor:
            late = executor.submit(self.workers.infer, uploaded["jobId"])
            try:
                self.assertTrue(entered.wait(5))
                old = self.service.operation(uploaded["jobId"])
                if next_generation:
                    media = self.service.media(uploaded["mediaId"], self.alice)
                    self.repo.transact([Write("operations", dict(old, status="PREPROCESSED", generation=old["generation"]+1, leaseUntil=0), old["_rev"]), Write("media", dict(media, generation=media["generation"]+1), media["_rev"])])
                with patch("ecoguard_aws.workers.time.time", return_value=old["leaseUntil"]+1):
                    self.workers.infer(uploaded["jobId"])
                winner = self.service.media(uploaded["mediaId"], self.alice)
                self.assertEqual(winner["status"], "READY")
                self.assertNotEqual(winner["originalKey"], first_target[0])
            finally:
                release.set()
            late.result(timeout=5)
        current = self.service.media(uploaded["mediaId"], self.alice)
        self.assertEqual(current, winner)
        self.assertIn(current["originalKey"], self.storage.objects)
        self.assertIn(current["featureKey"], self.storage.objects)
        self.assertNotIn(first_target[0], self.storage.objects)
        self.assertFalse([o for o in self.repo.all("operations") if o["type"] == "ARTIFACTS"])
        self.assertEqual(len([e for e in self.repo.all("outbox") if e["kind"] == "NOTIFY"]), 1)

    def test_late_inference_cannot_remove_new_attempt_outputs(self):
        self._late_attempt_cannot_delete_winner(next_generation=False)

    def test_late_inference_cannot_remove_new_generation_outputs(self):
        self._late_attempt_cannot_delete_winner(next_generation=True)

    def test_delete_failure_can_be_retried_and_scans_unregistered_archive(self):
        row = self.ready()
        orphan = f"library/pet/{row['ownerSub']}/{row['mediaId']}/{row['generation']}/99/original"
        newer = f"library/pet/{row['ownerSub']}/{row['mediaId']}/{row['generation']+2}/1/original"
        legacy = f"library/uncertain/{row['ownerSub']}/{row['mediaId']}/original"
        for key in (orphan, newer, legacy):
            self.storage.put(key, b"unregistered fixture")
        deletion = self.service.delete_media(self.alice, {"urls": [self.service.media_view(row)["url"]]})["items"][0]
        with patch.object(self.storage, "delete", side_effect=RuntimeError("Temporary S3 failure")):
            with self.assertRaises(RuntimeError):
                self.workers.delete(deletion["jobId"])
        self.assertEqual(self.service.operation(deletion["jobId"])["status"], "FAILED")
        self.assertEqual(self.repo.get("media", {"mediaId": row["mediaId"]})["status"], "DELETING")
        self.service.retry(self.alice, deletion["jobId"])
        self.workers.delete(deletion["jobId"])
        self.workers.delete(deletion["jobId"])
        self.assertEqual(self.service.operation(deletion["jobId"])["status"], "DELETED")
        self.assertEqual(set(self.storage.objects), {newer})

    def test_cleanup_failure_does_not_starve_other_artifacts_or_queries(self):
        for suffix in ("a", "b"):
            key = f"derived/orphan/{suffix}"
            self.storage.put(key, b"orphan")
            self.repo.transact([Write("operations", {"operationId": f"ARTIFACTS#{suffix}", "type": "ARTIFACTS", "status": "PENDING", "mediaId": f"missing-{suffix}", "producerId": f"missing-producer-{suffix}", "producerRev": 1, "leaseUntil": 0, "objectKeys": [key]})])
        self.storage.put("incoming/query/zz-query", b"query fixture")
        self.repo.transact([Write("operations", {"operationId": "zz-query", "type": "QUERY", "status": "CANCELLED", "leaseUntil": 0, "sourceKey": "incoming/query/zz-query", "objectKeys": []})])
        delete = self.storage.delete

        def reject_first(key):
            if key == "derived/orphan/a":
                raise RuntimeError("One object remains unavailable")
            delete(key)

        with patch.object(self.storage, "delete", side_effect=reject_first):
            result = self.workers.cleanup()
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["cleaned"], 2)
        self.assertEqual(result["failures"][0]["operationId"], "ARTIFACTS#a")
        self.assertIsNotNone(self.repo.get("operations", {"operationId": "ARTIFACTS#a"}))
        self.assertIsNone(self.repo.get("operations", {"operationId": "ARTIFACTS#b"}))
        self.assertTrue(self.service.operation("zz-query")["cleaned"])
        self.assertEqual(set(self.storage.objects), {"derived/orphan/a"})

    def test_worker_leases_cover_the_deployed_execution_limits(self):
        uploaded = self.upload()
        process = self.workers.preprocessor.process
        captured = {}

        def record_ingest(operation):
            captured["ingest"] = operation["leaseUntil"]
            return process(operation)

        def record_infer():
            captured["infer"] = self.service.operation(uploaded["jobId"])["leaseUntil"]

        self.inference.before_return = record_infer
        with patch("ecoguard_aws.workers.time.time", return_value=1000), patch.object(self.workers.preprocessor, "process", side_effect=record_ingest):
            self.workers.ingest(uploaded["jobId"])
            self.workers.infer(uploaded["jobId"])
            row = self.service.media(uploaded["mediaId"], self.alice)
            deletion = self.service.delete_media(self.alice, {"urls": [self.service.media_view(row)["url"]]})["items"][0]
            delete = self.storage.delete

            def record_delete(key):
                captured["delete"] = self.service.operation(deletion["jobId"])["leaseUntil"]
                delete(key)

            with patch.object(self.storage, "delete", side_effect=record_delete):
                self.workers.delete(deletion["jobId"])
        # Live infra allows 120s for ingest/delete and 480s for inference. Cleanup
        # must not assume these processes are dead before those limits plus margin.
        self.assertGreaterEqual(captured["ingest"]-1000, 150)
        self.assertGreaterEqual(captured["delete"]-1000, 150)
        self.assertGreaterEqual(captured["infer"]-1000, 510)

    def test_private_public_and_bulk_authorization(self):
        alice = self.ready()
        bob = self.ready(self.bob, b"bob fixture")
        with self.assertRaises(DomainError):
            self.service.media(alice["mediaId"], self.bob)
        self.assertEqual(self.service.search(self.bob, {"minimumCounts": {"fox": 2}})["items"][0]["mediaId"], bob["mediaId"])
        self.service.patch_media(self.alice, alice["mediaId"], {"visibility": "public"})
        self.assertEqual(len(self.service.search(self.bob, {"minimumCounts": {"fox": 2}})["items"]), 2)
        with self.assertRaises(DomainError):
            self.service.bulk_tags(self.bob, {"urls": [self.service.media_view(bob)["url"], self.service.media_view(alice)["url"]], "tags": ["ginger"], "operation": 1})
        self.assertNotIn("ginger", self.service.media(bob["mediaId"], self.bob)["tags"])

    def test_manual_removal_and_noop_do_not_publish_duplicate_updates(self):
        row = self.ready()
        url = self.service.media_view(row)["url"]
        self.service.bulk_tags(self.alice, {"urls": [url], "tags": ["fox"], "operation": 0})
        current = self.service.media(row["mediaId"], self.alice)
        self.assertEqual(current["counts"], {})
        self.assertEqual(self.service.search(self.alice, {"minimumCounts": {"fox": 1}})["items"], [])
        before = current["recordVersion"]
        self.service.bulk_tags(self.alice, {"urls": [url], "tags": ["fox"], "operation": 0})
        self.assertEqual(self.service.media(row["mediaId"], self.alice)["recordVersion"], before)

    def test_thumbnail_resolution_never_fetches_arbitrary_urls(self):
        row = self.ready()
        view = self.service.media_view(row)
        self.assertEqual(self.service.resolve_url(self.alice, view["thumbnailUrl"], thumbnail=True)["mediaId"], row["mediaId"])
        for bad in ("http://169.254.169.254/latest/meta-data", "https://evil.example/derived/x", "file:///etc/passwd"):
            with self.assertRaises(DomainError):
                self.service.resolve_url(self.alice, bad, thumbnail=True)

    def test_delete_cleans_objects_and_releases_dedup(self):
        row = self.ready()
        result = self.service.delete_media(self.alice, {"urls": [self.service.media_view(row)["url"]]})
        job_id = result["items"][0]["jobId"]
        self.workers.delete(job_id)
        self.assertIsNone(self.repo.get("media", {"mediaId": row["mediaId"]}))
        self.assertEqual(list(self.storage.keys(f"derived/{row['mediaId']}/")), [])
        self.assertEqual(self.service.operation(job_id)["status"], "DELETED")
        self.assertFalse(self.service.create_upload(self.alice, self.metadata())["duplicate"])

    def test_delete_during_inference_prevents_result_resurrection(self):
        uploaded = self.upload()
        self.workers.ingest(uploaded["jobId"])
        self.inference.before_return = lambda: self.service.delete_media(self.alice, {"urls": [f"{self.cfg.web_url}/media/{uploaded['mediaId']}"]})
        self.workers.infer(uploaded["jobId"])
        row = self.repo.get("media", {"mediaId": uploaded["mediaId"]})
        self.assertEqual(row["status"], "DELETING")
        self.assertEqual(self.service.operation(uploaded["jobId"])["status"], "CANCELLED")
        self.assertFalse(list(self.repo.all("index")))

    def test_query_never_creates_media_or_notification_and_cleans(self):
        self.ready()
        initial_media = len(list(self.repo.all("media")))
        initial_notify = len([e for e in self.repo.all("outbox") if e["kind"] == "NOTIFY"])
        query = self.service.create_query(self.alice, {"type": "file", **self.metadata(b"query fixture")})
        self.storage.accept_upload(query["uploadUrl"].rsplit("/", 1)[1], b"query fixture")
        self.workers.ingest(query["jobId"])
        self.workers.infer(query["jobId"])
        op = self.service.operation(query["jobId"])
        self.assertEqual(op["status"], "QUERY_READY")
        self.assertTrue(op["cleaned"])
        self.assertEqual(len(op["resultIds"]), 1)
        self.assertEqual(len(list(self.repo.all("media"))), initial_media)
        self.assertEqual(len([e for e in self.repo.all("outbox") if e["kind"] == "NOTIFY"]), initial_notify)
        self.assertEqual(list(self.storage.keys(f"derived/{query['jobId']}/")), [])
        self.assertNotIn(f"incoming/query/{query['jobId']}", self.storage.objects)

    def test_transient_query_failure_preserves_input_for_retry(self):
        query = self.service.create_query(self.alice, {"type": "file", **self.metadata()})
        self.storage.accept_upload(query["uploadUrl"].rsplit("/", 1)[1], b"explicit fixture content")
        self.workers.ingest(query["jobId"])
        self.inference.error = RuntimeError("transient test network failure")
        with self.assertRaises(RuntimeError):
            self.workers.infer(query["jobId"])
        self.workers.clean_query(query["jobId"])
        self.assertIn(f"incoming/query/{query['jobId']}", self.storage.objects)
        self.assertFalse(self.service.operation(query["jobId"])["cleaned"])

    def test_queries_reject_forged_second_cloud_identity(self):
        self.inference.authorize_query = lambda *a: {"sub": "bob", "requestId": a[1]}
        with self.assertRaises(DomainError) as caught:
            self.service.create_query(self.alice, {"type": "pet", "description": "fox"})
        self.assertEqual(caught.exception.status, 403)

    def test_photo_and_description_both_affect_pet_ranking(self):
        image_match = self.ready(data=b"image-only-match", visibility="public")
        combined_match = self.ready(data=b"combined-match", visibility="public")
        self.storage.put(combined_match["featureKey"], json.dumps({"features": [{"species": "fox", "vector": [0.6, 0.8], "featureVersion": "test-v1"}]}).encode())
        calls = []
        def embed(payload):
            calls.append(payload)
            return {"vector": [0., 1.], "featureVersion": "test-v1", "manifestVersion": payload["manifestVersion"]}
        self.inference.embed = embed
        original_infer = self.inference.infer
        image_payloads = []
        def capture_infer(payload):
            image_payloads.append(payload)
            return original_infer(payload)
        self.inference.infer = capture_infer
        query = self.service.create_query(self.alice, {"type": "pet", "description": "distinctive markings", **self.metadata()})
        self.storage.accept_upload(query["uploadUrl"].rsplit("/", 1)[1], b"explicit fixture content")
        self.workers.ingest(query["jobId"])
        self.workers.infer(query["jobId"])
        op = self.service.operation(query["jobId"])
        self.assertEqual(len(calls), 1)
        sys.path.insert(0, str(ROOT / "services/inference"))
        from app.schemas import InferRequest, EmbedRequest
        InferRequest.model_validate(image_payloads[0])
        EmbedRequest.model_validate(calls[0])
        self.assertEqual(image_payloads[0]["deadlineAt"], calls[0]["deadlineAt"])
        self.assertEqual(op["resultIds"], [combined_match["mediaId"], image_match["mediaId"]])
        self.assertEqual(op["scores"][combined_match["mediaId"]], 0.7)
        self.assertTrue(op["cleaned"])

    def test_photo_description_incompatible_embeddings_fail_and_clean(self):
        self.inference.embed = lambda payload: {"vector": [1., 0.], "featureVersion": "incompatible-version", "manifestVersion": payload["manifestVersion"]}
        query = self.service.create_query(self.alice, {"type": "pet", "description": "a fox", **self.metadata()})
        self.storage.accept_upload(query["uploadUrl"].rsplit("/", 1)[1], b"explicit fixture content")
        self.workers.ingest(query["jobId"])
        self.workers.infer(query["jobId"])
        op = self.service.operation(query["jobId"])
        self.assertEqual(op["status"], "FAILED")
        self.assertEqual(op["error"]["code"], "FEATURE_VERSION_MISMATCH")
        self.assertTrue(op["cleaned"])
        self.assertNotIn(op["sourceKey"], self.storage.objects)

    def test_text_query_rejects_wrong_embedding_manifest(self):
        self.inference.embed = lambda payload: {"vector": [1., 0.], "featureVersion": "test-v1", "manifestVersion": "wrong-manifest"}
        query = self.service.create_query(self.alice, {"type": "pet", "description": "a fox"})
        self.workers.infer(query["jobId"])
        op = self.service.operation(query["jobId"])
        self.assertEqual(op["status"], "FAILED")
        self.assertEqual(op["error"]["code"], "MODEL_VERSION_MISMATCH")
        self.assertTrue(op["cleaned"])

    def test_photo_with_whitespace_description_uses_image_only(self):
        self.inference.embed = lambda _: self.fail("A blank description must not invoke text inference")
        query = self.service.create_query(self.alice, {"type": "pet", "description": "  \n ", **self.metadata()})
        self.storage.accept_upload(query["uploadUrl"].rsplit("/", 1)[1], b"explicit fixture content")
        self.workers.ingest(query["jobId"])
        self.workers.infer(query["jobId"])
        self.assertEqual(self.service.operation(query["jobId"])["status"], "QUERY_READY")

    def test_cancel_waits_for_active_attempt_then_cleans_all_query_bytes(self):
        query = self.service.create_query(self.alice, {"type": "file", **self.metadata()})
        self.storage.accept_upload(query["uploadUrl"].rsplit("/", 1)[1], b"explicit fixture content")
        self.workers.ingest(query["jobId"])
        op = self.service.operation(query["jobId"])
        self.repo.transact([Write("operations", dict(op, status="INFERRING", leaseUntil=int(time.time())+470), op["_rev"])])
        self.service.cancel_query(self.alice, query["jobId"])
        self.workers.clean_query(query["jobId"])
        self.assertIn(op["sourceKey"], self.storage.objects)
        with patch("ecoguard_aws.workers.time.time", return_value=time.time()+500):
            self.workers.clean_query(query["jobId"])
        cleaned = self.service.operation(query["jobId"])
        self.assertTrue(cleaned["cleaned"])
        self.assertNotIn(op["sourceKey"], self.storage.objects)
        self.assertFalse(list(self.storage.keys(f"derived/{query['jobId']}/")))
        ttl = cleaned["expiresAt"]
        with patch("ecoguard_aws.workers.time.time", return_value=time.time()+5000):
            self.workers.clean_query(query["jobId"])
        self.assertEqual(self.service.operation(query["jobId"])["expiresAt"], ttl)

    def test_no_animals_query_fails_and_removes_temporary_media(self):
        self.inference.counts = {}
        query = self.service.create_query(self.alice, {"type": "file", **self.metadata()})
        self.storage.accept_upload(query["uploadUrl"].rsplit("/", 1)[1], b"explicit fixture content")
        self.workers.ingest(query["jobId"])
        self.workers.infer(query["jobId"])
        op = self.service.operation(query["jobId"])
        self.assertEqual(op["error"]["code"], "NO_ANIMALS")
        self.assertEqual(op["status"], "FAILED")
        self.assertTrue(op["cleaned"])
        self.assertFalse(self.storage.objects)

    def test_notification_routing_and_replay(self):
        self.service.subscribe(self.alice, {"tags": ["fox", "dog"]})
        self.service.subscribe(self.bob, {"tags": ["fox"]})
        row = self.ready()
        message = [e for e in self.repo.all("outbox") if e["kind"] == "NOTIFY"][0]
        self.workers.notify(message)
        self.workers.notify(message)
        self.assertEqual(len(self.notifier.messages), 1)
        self.assertEqual(self.notifier.messages[0][0], "topic-alice")
        self.service.patch_media(self.alice, row["mediaId"], {"visibility": "public"})
        next_message = max((e for e in self.repo.all("outbox") if e["kind"] == "NOTIFY"), key=lambda e: e["payload"]["recordVersion"])
        self.workers.notify(next_message)
        self.assertEqual(len(self.notifier.messages), 3)

    def test_unknown_notification_outcome_is_not_blindly_republished(self):
        self.service.subscribe(self.alice, {"tags": ["fox"]})
        self.ready()
        self.notifier.error = RuntimeError("Publish outcome unknown")
        msg = next(e for e in self.repo.all("outbox") if e["kind"] == "NOTIFY")
        self.workers.notify(msg)
        self.workers.notify(msg)
        self.assertEqual(len(self.notifier.messages), 1)
        self.assertIn("UNKNOWN", [o["status"] for o in self.repo.all("operations") if o["type"] == "DELIVERY"])

    def test_api_rejects_id_tokens_and_anonymous_calls(self):
        for claims in ({}, {"sub": "alice", "token_use": "id", "exp": time.time()+100}, {"sub": "alice", "token_use": "access", "exp": 0}):
            event = {"rawPath": "/api/v1/media", "requestContext": {"http": {"method": "GET"}, "authorizer": {"jwt": {"claims": claims}}}}
            self.assertEqual(handle(event, None, self.service)["statusCode"], 401)

    def test_cursor_bound_to_owner_and_filter(self):
        self.ready(data=b"one")
        self.ready(data=b"two")
        query = {"minimumCounts": {"fox": 1}, "limit": 1}
        result = self.service.search(self.alice, query)
        second = self.service.search(self.alice, {**query, "cursor": result["nextCursor"]})
        self.assertNotEqual(result["items"][0]["mediaId"], second["items"][0]["mediaId"])
        with self.assertRaises(DomainError):
            self.service.search(self.bob, {**query, "cursor": result["nextCursor"]})
        with self.assertRaises(DomainError):
            self.service.search(self.alice, {**query, "minimumCounts": {"dog": 1}, "cursor": result["nextCursor"]})

    def test_lex_session_cannot_be_reused_by_another_user(self):
        session = self.service.chat_session(self.alice)
        with self.assertRaises(DomainError) as caught:
            self.service.chat_message(self.bob, {"sessionId": session["sessionId"], "text": "foxes"})
        self.assertEqual(caught.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
