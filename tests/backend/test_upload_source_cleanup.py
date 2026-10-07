"""Terminal upload source recovery using real local storage/repository adapters.

Detection/preprocessing bytes are explicit fixtures, not model or cloud evidence.
"""
import hashlib
import io
import sys
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "packages/domain"), str(ROOT / "services/aws")]
from ecoguard_domain import DomainError, Principal
from ecoguard_aws.config import Config
from ecoguard_aws.repository import MemoryRepository, Write
from ecoguard_aws.service import Service
from ecoguard_aws.storage import MemoryStorage, S3Storage
from ecoguard_aws.workers import Workers
from test_backend import FixtureInference, FixturePreprocessor


class UploadSourceCleanupTests(unittest.TestCase):
    def setUp(self):
        self.config = Config(mode="local", model_manifest_version="fixture-v1")
        self.repo = MemoryRepository()
        self.storage = MemoryStorage(self.config)
        self.inference = FixtureInference()
        self.service = Service(self.config, self.repo, self.storage, inference=self.inference)
        self.workers = Workers(self.service, FixturePreprocessor(self.storage))
        self.owner = Principal("source-cleanup-owner")

    def upload(self, data=b"explicit upload source recovery fixture"):
        result = self.service.create_upload(self.owner, {"filename": "source-fixture.jpg", "contentType": "image/jpeg",
            "sizeBytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        result["fixtureData"] = data
        result["fixtureToken"] = result["uploadUrl"].rsplit("/", 1)[1]
        result["fixtureSource"] = self.storage.accept_upload(result["fixtureToken"], data)
        return result

    def ready(self, data=b"explicit upload source recovery fixture"):
        upload = self.upload(data)
        self.workers.ingest(upload["jobId"])
        self.workers.infer(upload["jobId"])
        return upload

    def replay(self, upload):
        self.assertGreater(self.storage.uploads[upload["fixtureToken"]]["expires"], time.time())
        self.storage.accept_upload(upload["fixtureToken"], upload["fixtureData"])
        self.assertIn(upload["fixtureSource"], self.storage.objects)

    def delete(self, upload):
        result = self.service.delete_media(self.owner, {"urls": [f"{self.config.web_url}/media/{upload['mediaId']}"]})["items"][0]
        self.workers.delete(result["jobId"])
        self.assertEqual(self.service.operation(result["jobId"])["status"], "DELETED")
        return result

    def assert_source_recovered(self, upload):
        self.assertNotIn(upload["fixtureSource"], self.storage.objects)
        # Retain the immutable source reference for another late signed PUT.
        self.assertEqual(self.service.operation(upload["jobId"])["sourceKey"], upload["fixtureSource"])

    def test_deleted_upload_replay_is_reclaimed_by_late_ingest_without_resurrection(self):
        upload = self.ready()
        self.delete(upload)
        self.assertFalse(self.storage.objects)
        inference_calls, preprocessing_calls = self.inference.calls, self.workers.preprocessor.calls
        self.replay(upload)
        self.workers.ingest(upload["jobId"])
        self.assert_source_recovered(upload)
        self.assertIsNone(self.repo.get("media", {"mediaId": upload["mediaId"]}))
        self.assertEqual(self.service.operation(upload["jobId"])["status"], "DELETED")
        self.assertEqual((self.inference.calls, self.workers.preprocessor.calls), (inference_calls, preprocessing_calls))

    def test_deleted_upload_replay_is_reclaimed_without_an_ingest_event(self):
        upload = self.ready()
        self.delete(upload)
        self.replay(upload)
        result = self.workers.cleanup()
        self.assertEqual((result["checked"], result["cleaned"], result["failed"]), (1, 1, 0))
        self.assert_source_recovered(upload)
        self.assertFalse(self.storage.objects)
        self.assertIsNone(self.repo.get("media", {"mediaId": upload["mediaId"]}))

    def test_ready_replay_preserves_published_archive_and_record(self):
        upload = self.ready()
        before = self.service.media(upload["mediaId"], self.owner)
        stored = dict(self.storage.objects)
        self.replay(upload)
        self.workers.ingest(upload["jobId"])
        self.assert_source_recovered(upload)
        self.assertEqual(self.storage.objects, stored)
        self.assertEqual(self.service.media(upload["mediaId"], self.owner), before)

    def test_initial_source_delete_failure_after_publication_is_recovered(self):
        upload = self.upload()
        self.workers.ingest(upload["jobId"])
        real_delete = self.storage.delete
        def fail_source(key):
            if key == upload["fixtureSource"]:
                raise RuntimeError("fixture source delete failure")
            real_delete(key)
        with patch.object(self.storage, "delete", side_effect=fail_source):
            with self.assertRaisesRegex(RuntimeError, "fixture source delete failure"):
                self.workers.infer(upload["jobId"])
        media = self.service.media(upload["mediaId"], self.owner)
        self.assertEqual(media["status"], "READY")
        self.assertIn(upload["fixtureSource"], self.storage.objects)
        self.assertEqual(self.workers.cleanup()["cleaned"], 1)
        self.assert_source_recovered(upload)
        self.assertEqual(self.storage.read(media["originalKey"]), upload["fixtureData"])

    def test_repeated_replays_remain_reclaimable_and_missing_source_is_idempotent(self):
        upload = self.ready()
        self.delete(upload)
        for iteration in range(4):
            self.replay(upload)
            if iteration % 2:
                self.workers.ingest(upload["jobId"])
            else:
                self.assertEqual(self.workers.cleanup()["cleaned"], 1)
            self.assert_source_recovered(upload)
        with patch.object(self.storage, "delete", wraps=self.storage.delete) as delete:
            result = self.workers.cleanup()
        # Counters count successfully swept operations, including an already
        # absent source. Always delete so hidden S3 versions are covered too.
        self.assertEqual((result["cleaned"], result["failed"]), (1, 0))
        delete.assert_called_once_with(upload["fixtureSource"])
        self.assertFalse(self.storage.objects)

    def test_failed_upload_keeps_source_for_real_retry(self):
        upload = self.upload()
        self.workers.ingest(upload["jobId"])
        self.inference.error = DomainError("FIXTURE_INFERENCE_FAILED", "Explicit retry fixture.", 503)
        self.workers.infer(upload["jobId"])
        self.assertEqual(self.service.operation(upload["jobId"])["status"], "FAILED")
        self.workers.ingest(upload["jobId"])
        self.workers.cleanup()
        self.assertEqual(self.storage.read(upload["fixtureSource"]), upload["fixtureData"])
        self.inference.error = None
        self.service.retry(self.owner, upload["jobId"])
        self.workers.ingest(upload["jobId"])
        self.workers.infer(upload["jobId"])
        media = self.service.media(upload["mediaId"], self.owner)
        self.assertEqual(media["status"], "READY")
        self.assertEqual(self.storage.read(media["originalKey"]), upload["fixtureData"])

    def test_cancelled_upload_keeps_live_lease_bytes_then_reclaims_after_expiry(self):
        upload = self.upload()
        self.service.complete_upload(self.owner, upload["jobId"])
        active = self.workers._claim(upload["jobId"], {"QUEUED"}, "PREPROCESSING", 150)
        deletion = self.service.delete_media(self.owner, {"urls": [f"{self.config.web_url}/media/{upload['mediaId']}"]})["items"][0]
        self.assertEqual(self.service.operation(upload["jobId"])["status"], "CANCELLED")
        self.workers.ingest(upload["jobId"])
        self.assertEqual(self.workers.cleanup()["cleaned"], 0)
        self.assertIn(upload["fixtureSource"], self.storage.objects)
        with patch("ecoguard_aws.workers.time.time", return_value=active["leaseUntil"] + 1):
            self.assertEqual(self.workers.cleanup()["cleaned"], 1)
            self.assert_source_recovered(upload)
            self.workers.delete(deletion["jobId"])
        self.assertEqual(self.service.operation(deletion["jobId"])["status"], "DELETED")

    def test_legacy_ready_record_still_using_source_keeps_original(self):
        upload = self.ready()
        self.replay(upload)
        media = self.service.media(upload["mediaId"], self.owner)
        self.repo.transact([Write("media", dict(media, originalKey=upload["fixtureSource"]), media["_rev"])])
        self.workers.ingest(upload["jobId"])
        result = self.workers.cleanup()
        self.assertEqual((result["cleaned"], result["failed"]), (0, 0))
        self.assertEqual(self.storage.read(upload["fixtureSource"]), upload["fixtureData"])

    def test_non_owned_source_key_is_rejected_without_deleting_either_upload(self):
        retired = self.ready(b"retired upload")
        self.delete(retired)
        other = self.upload(b"other pending upload")
        op = self.service.operation(retired["jobId"])
        self.repo.transact([Write("operations", dict(op, sourceKey=other["fixtureSource"]), op["_rev"])])
        with self.assertRaises(DomainError) as error:
            self.workers.ingest(retired["jobId"])
        self.assertEqual(error.exception.code, "INVALID_SOURCE_KEY")
        with redirect_stdout(io.StringIO()):
            result = self.workers.cleanup()
        self.assertEqual((result["cleaned"], result["failed"]), (0, 1))
        self.assertEqual(self.storage.read(other["fixtureSource"]), other["fixtureData"])
        self.assertEqual(self.service.operation(other["jobId"])["status"], "UPLOADING")

    def test_one_source_delete_failure_does_not_starve_later_source_and_can_retry(self):
        uploads = sorted([self.ready(b"source one"), self.ready(b"source two")], key=lambda item: item["jobId"])
        for upload in uploads:
            self.replay(upload)
        bad, good = uploads
        real_delete = self.storage.delete
        def fail_first(key):
            if key == bad["fixtureSource"]:
                raise RuntimeError("fixture isolated source failure")
            real_delete(key)
        with patch.object(self.storage, "delete", side_effect=fail_first), redirect_stdout(io.StringIO()):
            result = self.workers.cleanup()
        self.assertEqual((result["checked"], result["cleaned"], result["failed"]), (2, 1, 1))
        self.assertEqual(result["failures"], [{"operationId": bad["jobId"], "errorType": "RuntimeError"}])
        self.assertIn(bad["fixtureSource"], self.storage.objects)
        self.assert_source_recovered(good)
        retried = self.workers.cleanup()
        self.assertEqual((retried["cleaned"], retried["failed"]), (2, 0))
        self.assert_source_recovered(bad)

    def test_head_404_does_not_hide_noncurrent_versions_from_source_recovery(self):
        upload = self.ready()
        source = upload["fixtureSource"]
        # A prior partial DeleteObjects failure can leave this valid S3 state:
        # a current delete marker masks historical bytes from HEAD and ListObjects.
        versions = {"historical-data": "data", "another-old-version": "data", "current-marker": "marker"}
        class NotFound(Exception):
            response = {"Error": {"Code": "404"}}
        class VersionedS3:
            exceptions = SimpleNamespace(ClientError=NotFound)
            delete_attempts = 0

            def head_object(self, **kwargs):
                raise NotFound()

            def get_paginator(self, name):
                self_name = name
                def paginate(**kwargs):
                    if self_name != "list_object_versions":
                        raise AssertionError("Hidden versions require the versions listing API")
                    yield {"Versions": [{"Key": source, "VersionId": key} for key, kind in versions.items() if kind == "data"],
                           "DeleteMarkers": [{"Key": source, "VersionId": key} for key, kind in versions.items() if kind == "marker"]}
                return SimpleNamespace(paginate=paginate)

            def delete_objects(self, **kwargs):
                self.delete_attempts += 1
                if self.delete_attempts == 1:
                    versions.pop("another-old-version", None)
                    return {"Errors": [{"Key": source, "VersionId": key, "Code": "InternalError"}
                                       for key in ("historical-data", "current-marker")]}
                for entry in kwargs["Delete"]["Objects"]:
                    versions.pop(entry["VersionId"], None)
                return {}

            def delete_object(self, **kwargs):
                if "VersionId" in kwargs:
                    versions.pop(kwargs["VersionId"], None)
                else:
                    versions["last-marker"] = "marker"
        # Bypass only the SDK/session constructor; exercise real S3Storage methods.
        storage = S3Storage.__new__(S3Storage)
        storage.client, storage.bucket = VersionedS3(), "local-api-fixture-bucket"
        self.workers.storage = storage
        with redirect_stdout(io.StringIO()):
            partial = self.workers.cleanup()
        self.assertEqual((partial["cleaned"], partial["failed"]), (0, 1))
        self.assertEqual(versions, {"historical-data": "data", "current-marker": "marker"})
        self.assertEqual(self.service.operation(upload["jobId"])["sourceKey"], source)
        result = self.workers.cleanup()
        self.assertEqual((result["cleaned"], result["failed"]), (1, 0))
        self.assertEqual(versions, {})


if __name__ == "__main__":
    unittest.main()
