"""Deadline/renewal/cleanup regression with explicit non-model fixtures."""
import hashlib
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "packages/domain"), str(ROOT / "services/aws")]
from ecoguard_domain import DomainError, Principal
from ecoguard_aws.config import Config
from ecoguard_aws.repository import MemoryRepository, Write
from ecoguard_aws.service import Service
from ecoguard_aws.storage import MemoryStorage
from ecoguard_aws.workers import Workers
from test_backend import FixtureInference, FixturePreprocessor


class QueryLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.25
        self.clock = patch("time.time", side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.config = Config(mode="local", model_manifest_version="fixture-v1")
        self.repo, self.inference = MemoryRepository(), FixtureInference()
        self.storage = MemoryStorage(self.config)
        self.service = Service(self.config, self.repo, self.storage, inference=self.inference)
        self.workers = Workers(self.service, FixturePreprocessor(self.storage))
        self.owner = Principal("query-lifecycle-owner")
        self.data = b"explicit query lifecycle fixture"

    def metadata(self):
        return {"filename": "query-fixture.jpg", "contentType": "image/jpeg", "sizeBytes": len(self.data),
                "sha256": hashlib.sha256(self.data).hexdigest()}

    def query(self, *, upload=True):
        result = self.service.create_query(self.owner, {"type": "file", **self.metadata()})
        if upload:
            self.storage.accept_upload(result["uploadUrl"].rsplit("/", 1)[1], self.data)
        return result

    def deadline(self, job_id, deadline=1900, **values):
        old = self.service.operation(job_id)
        self.repo.transact([Write("operations", dict(old, cleanupDeadline=deadline, resultExpiresAt=deadline, **values), old["_rev"])])
        return self.service.operation(job_id)

    def test_upload_authorization_and_total_query_lifetime_follow_plan(self):
        library = self.service.create_upload(self.owner, self.metadata())
        query = self.query(upload=False)
        for result in (library, query):
            with self.subTest(kind=result.get("queryId", "library")):
                self.assertEqual(result["expiresIn"], 600)
                self.assertEqual(self.service.operation(result["jobId"])["uploadExpiresAt"], 1600)
        operation = self.service.operation(query["jobId"])
        self.assertEqual(operation["cleanupDeadline"], 1900)
        self.assertEqual(operation["resultExpiresAt"], 1900)

    def test_runtime_duration_environment_values_are_typed_and_validated(self):
        with patch.dict(os.environ, {"ECO_MODE": "local", "ECO_UPLOAD_URL_SECONDS": "120", "ECO_QUERY_LIFETIME_SECONDS": "900"}):
            config = Config.from_env()
            self.assertEqual(config.upload_url_seconds, 120)
            self.assertEqual(config.query_lifetime_seconds, 900)
            self.assertIsInstance(config.upload_url_seconds, int)
        for value in ("0", "-1", "not-an-integer"):
            with self.subTest(value=value), patch.dict(os.environ, {"ECO_MODE": "local", "ECO_UPLOAD_URL_SECONDS": value}), self.assertRaises(ValueError):
                Config.from_env()

    def test_renew_query_uses_remaining_budget_without_extending_deadline(self):
        query = self.query(upload=False)
        self.deadline(query["jobId"])
        self.now = 1500.75
        renewed = self.service.renew_upload(self.owner, query["jobId"])
        self.assertEqual(renewed["expiresIn"], 399)
        self.assertLessEqual(self.storage.uploads[renewed["uploadUrl"].rsplit("/", 1)[1]]["expires"], 1900)
        op = self.service.operation(query["jobId"])
        self.assertEqual((op["cleanupDeadline"], op["resultExpiresAt"]), (1900, 1900))
        self.assertLessEqual(op["uploadExpiresAt"], 1900)

    def test_renew_checks_existing_bytes_and_advances_without_another_put_url(self):
        for kind in ("library", "query"):
            with self.subTest(kind=kind):
                result = self.query() if kind == "query" else self.service.create_upload(self.owner, self.metadata())
                if kind == "library":
                    self.storage.accept_upload(result["uploadUrl"].rsplit("/", 1)[1], self.data)
                renewed = self.service.renew_upload(self.owner, result["jobId"])
                self.assertTrue(renewed["alreadyUploaded"])
                self.assertNotIn("uploadUrl", renewed)
                self.assertEqual(renewed["status"], "QUERY_QUEUED" if kind == "query" else "QUEUED")

    def test_renew_does_not_accept_mismatched_stored_bytes(self):
        query = self.query(upload=False)
        self.storage.put(self.service.operation(query["jobId"])["sourceKey"], b"corrupt fixture", "image/jpeg")
        with self.assertRaises(DomainError) as caught:
            self.service.renew_upload(self.owner, query["jobId"])
        self.assertEqual(caught.exception.code, "CHECKSUM_MISMATCH")

    def test_expired_query_cannot_complete_or_renew(self):
        query = self.query()
        self.deadline(query["jobId"])
        self.now = 1900
        for action in (self.service.complete_upload, self.service.renew_upload):
            with self.subTest(action=action.__name__), self.assertRaises(DomainError) as caught:
                action(self.owner, query["jobId"])
            self.assertEqual(caught.exception.code, "QUERY_EXPIRED")
            self.assertEqual(caught.exception.status, 410)
        self.workers.cleanup()
        self.assertTrue(self.service.operation(query["jobId"])["cleaned"])
        self.assertFalse(self.storage.objects)

    def test_expired_query_is_not_preprocessed(self):
        query = self.query()
        self.deadline(query["jobId"])
        self.now = 1900
        self.workers.ingest(query["jobId"])
        self.assertEqual(self.workers.preprocessor.calls, 0)
        self.assertEqual(self.inference.calls, 0)
        self.assertEqual(self.service.operation(query["jobId"])["error"]["code"], "QUERY_EXPIRED")
        self.assertFalse(self.storage.objects)

    def test_expired_query_is_not_sent_to_inference(self):
        query = self.query()
        self.workers.ingest(query["jobId"])
        self.deadline(query["jobId"])
        self.now = 1900
        self.workers.infer(query["jobId"])
        self.assertEqual(self.inference.calls, 0)
        self.assertEqual(self.service.operation(query["jobId"])["status"], "FAILED")
        self.assertFalse(self.storage.objects)

    def test_expiry_fences_publication_without_shortening_live_producer_lease(self):
        query = self.query()
        self.workers.ingest(query["jobId"])
        initial = self.deadline(query["jobId"], status="INFERRING", leaseUntil=2200)
        self.now = 1901
        self.workers.cleanup()
        expired = self.service.operation(query["jobId"])
        self.assertEqual(expired["status"], "FAILED")
        self.assertEqual(expired["error"]["code"], "QUERY_EXPIRED")
        self.assertEqual(expired["leaseUntil"], 2200)
        self.assertGreater(expired["_rev"], initial["_rev"])
        self.assertFalse(expired["cleaned"])
        self.assertIn(expired["sourceKey"], self.storage.objects)
        self.now = 2201
        self.workers.cleanup()
        self.assertTrue(self.service.operation(query["jobId"])["cleaned"])
        self.assertFalse(self.storage.objects)

    def test_inference_budget_is_capped_by_total_query_deadline(self):
        query = self.query()
        self.workers.ingest(query["jobId"])
        self.deadline(query["jobId"])
        self.now = 1800.25
        real_infer = self.inference.infer
        seen = []
        def capture(payload):
            seen.append(payload)
            return real_infer(payload)
        self.inference.infer = capture
        self.workers.infer(query["jobId"])
        self.assertEqual(len(seen), 1)
        self.assertLessEqual(datetime.fromisoformat(seen[0]["deadlineAt"].replace("Z", "+00:00")).timestamp(), 1900)

    def test_inference_returning_after_total_deadline_cannot_publish_results(self):
        query = self.query()
        self.workers.ingest(query["jobId"])
        self.deadline(query["jobId"])
        self.now = 1800
        self.inference.before_return = lambda: setattr(self, "now", 1901)
        self.workers.infer(query["jobId"])
        op = self.service.operation(query["jobId"])
        self.assertEqual(op["status"], "FAILED")
        self.assertEqual(op["error"]["code"], "QUERY_EXPIRED")
        self.assertNotIn("resultIds", op)
        self.assertTrue(op["cleaned"])
        self.assertFalse(self.storage.objects)

    def test_late_query_put_is_reclaimed_by_terminal_ingest(self):
        query = self.query()
        self.workers.ingest(query["jobId"])
        self.workers.infer(query["jobId"])
        self.assertFalse(self.storage.objects)
        self.storage.accept_upload(query["uploadUrl"].rsplit("/", 1)[1], self.data)
        self.workers.ingest(query["jobId"])
        self.assertEqual(self.service.operation(query["jobId"])["status"], "QUERY_READY")
        self.assertFalse(self.storage.objects)


if __name__ == "__main__":
    unittest.main()
