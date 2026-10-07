"""Core AND/batch/idempotency contract; detector outputs are explicit fixtures."""
import hashlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "packages/domain"), str(ROOT / "services/aws")]
from ecoguard_domain import DomainError, Principal
from ecoguard_aws.config import Config
from ecoguard_aws.repository import MemoryRepository, Conflict
from ecoguard_aws.service import Service
from ecoguard_aws.storage import MemoryStorage
from ecoguard_aws.workers import Workers
from test_backend import FixtureInference, FixturePreprocessor


class CoreManagementTests(unittest.TestCase):
    def setUp(self):
        self.config = Config(mode="local", model_manifest_version="fixture-v1")
        self.repo, self.inference = MemoryRepository(), FixtureInference()
        self.storage = MemoryStorage(self.config)
        self.service = Service(self.config, self.repo, self.storage, inference=self.inference)
        self.workers = Workers(self.service, FixturePreprocessor(self.storage))
        self.owner, self.other = Principal("core-owner"), Principal("other-owner")

    def metadata(self, data, video=False):
        return {"filename": "explicit-fixture.mp4" if video else "explicit-fixture.jpg",
                "contentType": "video/mp4" if video else "image/jpeg", "sizeBytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}

    def ready(self, data, counts, principal=None, **options):
        principal = principal or self.owner
        upload = self.service.create_upload(principal, {**self.metadata(data), **options})
        self.storage.accept_upload(upload["uploadUrl"].rsplit("/", 1)[1], data)
        self.inference.counts = counts
        self.workers.ingest(upload["jobId"])
        self.workers.infer(upload["jobId"])
        return self.service.media(upload["mediaId"], principal)

    def url(self, media):
        return f"{self.config.web_url}/media/{media['mediaId']}"

    def test_image_and_video_file_queries_match_all_species_and_current_acl(self):
        both = self.ready(b"both species", {"feral_pig": 1, "cane_toad": 3})
        self.ready(b"only pig", {"feral_pig": 2})
        self.ready(b"only toad", {"cane_toad": 4})
        self.ready(b"other private both", {"feral_pig": 2, "cane_toad": 4}, self.other)
        public = self.ready(b"other public both", {"feral_pig": 2, "cane_toad": 4}, self.other, visibility="public")
        initial_media, initial_index = list(self.repo.all("media")), list(self.repo.all("index"))
        notify_before = [row for row in self.repo.all("outbox") if row["kind"] == "NOTIFY"]
        for video in (False, True):
            with self.subTest(fileType="video" if video else "image"):
                data = b"explicit video query fixture" if video else b"explicit image query fixture"
                query = self.service.create_query(self.owner, {"type": "file", **self.metadata(data, video)})
                self.storage.accept_upload(query["uploadUrl"].rsplit("/", 1)[1], data)
                self.inference.counts = {"feral_pig": 1, "cane_toad": 1}
                self.workers.ingest(query["jobId"])
                self.workers.infer(query["jobId"])
                op = self.service.operation(query["jobId"])
                self.assertEqual(set(op["resultIds"]), {both["mediaId"], public["mediaId"]})
                self.assertTrue(op["cleaned"])
                self.assertFalse(list(self.storage.keys(f"derived/{query['jobId']}/")))
                self.assertNotIn(op["sourceKey"], self.storage.objects)
        self.assertEqual(list(self.repo.all("media")), initial_media)
        self.assertEqual(list(self.repo.all("index")), initial_index)
        self.assertEqual([row for row in self.repo.all("outbox") if row["kind"] == "NOTIFY"], notify_before)

    def test_duplicate_batch_urls_do_not_duplicate_updates_or_notifications(self):
        media = self.ready(b"deduplicated URL fixture", {"fox": 2})
        before = len(list(self.repo.all("outbox")))
        body = {"urls": [self.url(media), self.url(media)], "tags": ["ginger", "ginger"], "operation": 1}
        result = self.service.bulk_tags(self.owner, body)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["status"], "updated")
        self.assertEqual(len(list(self.repo.all("outbox"))), before + 1)
        self.assertEqual(self.service.bulk_tags(self.owner, body)["items"][0]["status"], "unchanged")
        self.assertEqual(len(list(self.repo.all("outbox"))), before + 1)

    def test_batch_conflict_reports_failed_item_and_preserves_other_success(self):
        first = self.ready(b"conflict first", {"fox": 2})
        second = self.ready(b"conflict second", {"dog": 1})
        real_transact = self.repo.transact
        rejected = []
        def conflict_one(writes):
            if not rejected and any(w.table == "media" and w.item["mediaId"] == first["mediaId"] for w in writes):
                rejected.append(True)
                raise Conflict()
            return real_transact(writes)
        body = {"urls": [self.url(first), self.url(second)], "tags": ["ginger"], "operation": 1}
        with patch.object(self.repo, "transact", side_effect=conflict_one):
            result = self.service.bulk_tags(self.owner, body)
        self.assertEqual([item["status"] for item in result["items"]], ["failed", "updated"])
        self.assertNotIn("ginger", self.service.media(first["mediaId"], self.owner)["tags"])
        self.assertIn("ginger", self.service.media(second["mediaId"], self.owner)["tags"])
        retried = self.service.bulk_tags(self.owner, body)
        self.assertEqual([item["status"] for item in retried["items"]], ["updated", "unchanged"])

    def test_batch_delete_auth_validation_precedes_any_mutation(self):
        own = self.ready(b"owner delete fixture", {"fox": 1})
        other = self.ready(b"other delete fixture", {"dog": 1}, self.other, visibility="public")
        with self.assertRaises(DomainError):
            self.service.delete_media(self.owner, {"urls": [self.url(own), self.url(other)]})
        self.assertEqual(self.service.media(own["mediaId"], self.owner)["status"], "READY")
        self.assertFalse([row for row in self.repo.all("operations") if row.get("type") == "DELETE"])

    def test_repeated_delete_returns_same_pending_and_completed_receipt(self):
        media = self.ready(b"repeat delete fixture", {"fox": 1})
        body = {"urls": [self.url(media)]}
        first = self.service.delete_media(self.owner, body)["items"][0]
        pending = self.service.delete_media(self.owner, body)["items"][0]
        self.assertEqual((pending["jobId"], pending["status"]), (first["jobId"], "DELETING"))
        self.workers.delete(first["jobId"])
        completed = self.service.delete_media(self.owner, body)["items"][0]
        self.assertEqual((completed["jobId"], completed["status"]), (first["jobId"], "DELETED"))
        self.assertEqual(len([row for row in self.repo.all("operations") if row.get("type") == "DELETE"]), 1)
        self.assertFalse(self.storage.objects)
        with self.assertRaises(DomainError):
            self.service.delete_media(self.other, body)


if __name__ == "__main__":
    unittest.main()
