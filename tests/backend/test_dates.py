"""Temporal boundary regressions using explicit records/vectors, not AI evidence."""
import hashlib
import json
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "packages/domain"), str(ROOT / "services/aws")]
from ecoguard_domain import DomainError, Principal
from ecoguard_domain.rules import iso_time
from ecoguard_aws.config import Config
from ecoguard_aws.repository import MemoryRepository, Write
from ecoguard_aws.service import Service
from ecoguard_aws.storage import MemoryStorage
from ecoguard_aws.workers import Workers

BASE = "2026-10-08T00:00:00Z"


class DateTests(unittest.TestCase):
    def setUp(self):
        config = Config(mode="local")
        self.repo = MemoryRepository()
        self.storage = MemoryStorage(config)
        self.service = Service(config, self.repo, self.storage)
        self.principal = Principal("date-fixture-owner")
        self.sequence = 0

    def add(self, media_id, observed_at):
        key = f"date-fixture/{media_id}.json"
        row = {"mediaId": media_id, "ownerSub": self.principal.sub,
               "status": "READY", "visibility": "public", "fileType": "image",
               "observedAt": observed_at, "tags": ["cat"], "counts": {"cat": 1},
               "autoTags": ["cat"], "autoCounts": {"cat": 1}, "featureKey": key}
        self.storage.put(key, json.dumps({"features": [{"species": "cat", "vector": [1., 0.], "featureVersion": "date-fixture-v1"}]}).encode())
        self.repo.transact([Write("media", row), *[Write("index", item) for item in self.service._index_items(row).values()]])

    def search(self, **bounds):
        result = self.service.search(self.principal, {"minimumCounts": {"cat": 1}, **bounds})
        return [row["mediaId"] for row in result["items"]]

    def pet(self, **bounds):
        self.sequence += 1
        job_id = f"date-fixture-query-{self.sequence}"
        op = {"operationId": job_id, "jobId": job_id, "ownerSub": self.principal.sub,
              "type": "QUERY", "queryType": "pet", "status": "INFERRING",
              "generation": 1, "frames": [], "objectKeys": [], **bounds}
        self.repo.transact([Write("operations", op)])
        Workers(self.service)._query_result(self.service.operation(job_id), {
            "features": [{"species": "cat", "vector": [1., 0.], "featureVersion": "date-fixture-v1"}]})
        completed = self.service.operation(job_id)
        self.assertTrue(completed["cleaned"])
        return set(completed["resultIds"])

    def test_iso_time_canonicalizes_precision_and_timezone(self):
        for value in (BASE, "2026-10-08T00:00:00.000Z", "2026-10-08T11:00:00+11:00", "2026-10-07T20:00:00-04:00"):
            with self.subTest(value=value):
                self.assertEqual(iso_time(value), "2026-10-08T00:00:00.000000Z")
        self.assertEqual(iso_time("2026-10-08T11:00:00.5+11:00"), "2026-10-08T00:00:00.500000Z")
        self.assertRegex(iso_time(), r"T\d{2}:\d{2}:\d{2}\.\d{6}Z$")

    def test_timezone_is_still_required(self):
        for value in ("2026-10-08", "2026-10-08T00:00:00", "invalid", 123):
            with self.subTest(value=value), self.assertRaises(DomainError) as caught:
                iso_time(value)
            self.assertEqual(caught.exception.code, "INVALID_DATE")

    def test_upload_persists_canonical_timestamp(self):
        upload = self.service.create_upload(self.principal, {
            "filename": "date-fixture.png", "contentType": "image/png", "sizeBytes": 1,
            "sha256": "a" * 64, "observedAt": "2026-10-08T11:00:00+11:00"})
        row = self.repo.get("media", {"mediaId": upload["mediaId"]})
        self.assertEqual(row["observedAt"], "2026-10-08T00:00:00.000000Z")

    def test_whole_second_bounds_include_equal_and_exclude_later(self):
        self.add("whole", BASE)
        self.add("later", "2026-10-08T00:00:00.500000Z")
        self.assertEqual(self.search(**{"from": BASE}), ["later", "whole"])
        self.assertEqual(self.search(to=BASE), ["whole"])
        self.assertEqual(self.search(**{"from": BASE, "to": BASE}), ["whole"])

    def test_microsecond_bounds_are_inclusive_for_legacy_offsets(self):
        self.add("before", "2026-10-08T00:00:00.499999Z")
        self.add("equal", "2026-10-08T11:00:00.5+11:00")
        self.add("after", "2026-10-07T20:00:00.500001-04:00")
        bound = "2026-10-08T00:00:00.500000Z"
        self.assertEqual(self.search(**{"from": bound}), ["after", "equal"])
        self.assertEqual(self.search(to=bound), ["equal", "before"])
        self.assertEqual(self.search(**{"from": bound, "to": bound}), ["equal"])

    def test_same_instant_pagination_ties_by_id_without_loss_or_duplicates(self):
        for media_id, stamp in (("a", BASE), ("b", "2026-10-08T00:00:00.000000Z"), ("c", "2026-10-07T20:00:00-04:00"), ("d", "2026-10-08T00:00:00.000001Z")):
            self.add(media_id, stamp)
        ids, cursor = [], None
        for _ in range(5):
            result = self.service.list_media(self.principal, {"limit": 1, "cursor": cursor})
            ids.extend(row["mediaId"] for row in result["items"])
            cursor = result["nextCursor"]
            if cursor is None:
                break
        self.assertEqual(ids, ["d", "c", "b", "a"])
        self.assertIsNone(cursor)

    def test_legacy_cursor_requires_restart_instead_of_silently_skipping_rows(self):
        self.add("whole", BASE)
        self.add("later", "2026-10-08T00:00:00.500000Z")
        # The old lexical sort returned whole first. Interpreting its cursor as
        # a chronological boundary now would silently drop the unreturned later.
        context = {"gallery": self.principal.sub}
        self.repo.transact([Write("operations", {"operationId": "CURSOR#legacy", "type": "CURSOR", "ownerSub": self.principal.sub,
            "expiresAt": int(time.time()) + 900, "fingerprint": hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest(),
            "afterKey": [BASE, "whole"]})])
        with self.assertRaises(DomainError) as caught:
            self.service.list_media(self.principal, {"cursor": "legacy", "limit": 100})
        self.assertEqual(caught.exception.code, "CURSOR_EXPIRED")
        self.assertEqual(caught.exception.status, 409)
        result = self.service.list_media(self.principal, {})
        self.assertEqual([row["mediaId"] for row in result["items"]], ["later", "whole"])

    def test_legacy_filtered_cursor_also_requires_restart(self):
        self.add("a", "2026-10-08T00:00:00.000000Z")
        self.add("b", BASE)
        context = {"minimumCounts": {"cat": 1}, "from": BASE, "to": None, "fileType": None, "publicOnly": False}
        self.repo.transact([Write("operations", {"operationId": "CURSOR#legacy-filtered", "type": "CURSOR", "ownerSub": self.principal.sub,
            "expiresAt": int(time.time()) + 900, "fingerprint": hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest(), "afterKey": [BASE, "b"]})])
        with self.assertRaises(DomainError) as caught:
            self.search(**{"from": "2026-10-08T11:00:00+11:00", "cursor": "legacy-filtered"})
        self.assertEqual(caught.exception.code, "CURSOR_EXPIRED")
        with self.assertRaises(DomainError) as caught:
            self.search(**{"from": "2026-10-08T00:00:01Z", "cursor": "legacy-filtered"})
        self.assertEqual(caught.exception.code, "CURSOR_EXPIRED")

    def test_equivalent_timezone_bounds_can_continue_new_cursor(self):
        self.add("whole", BASE)
        self.add("later", "2026-10-08T00:00:00.5Z")
        result = self.service.search(self.principal, {"minimumCounts": {"cat": 1}, "from": BASE, "limit": 1})
        self.assertEqual([row["mediaId"] for row in result["items"]], ["later"])
        self.assertEqual(self.search(**{"from": "2026-10-08T11:00:00+11:00", "cursor": result["nextCursor"]}), ["whole"])
        state = self.service.operation("CURSOR#" + result["nextCursor"])
        self.assertEqual(state["orderVersion"], 2)
        with self.assertRaises(DomainError) as caught:
            self.search(**{"from": "2026-10-08T00:00:01Z", "cursor": result["nextCursor"]})
        self.assertEqual(caught.exception.code, "CURSOR_EXPIRED")

    def test_pet_bounds_use_inclusive_instants_for_legacy_records_and_jobs(self):
        self.add("whole", BASE)
        self.add("equal-offset", "2026-10-08T11:00:00+11:00")
        self.add("later", "2026-10-08T00:00:00.000001Z")
        self.assertEqual(self.pet(**{"from": BASE}), {"whole", "equal-offset", "later"})
        self.assertEqual(self.pet(to=BASE), {"whole", "equal-offset"})
        self.assertEqual(self.pet(**{"from": "2026-10-07T20:00:00-04:00", "to": BASE}), {"whole", "equal-offset"})
        self.assertEqual(self.pet(**{"from": "2026-10-08T00:00:00.000001Z", "to": "2026-10-08T11:00:00.000001+11:00"}), {"later"})

    def test_reversed_bounds_continue_to_return_no_matches(self):
        self.add("whole", BASE)
        self.add("later", "2026-10-08T00:00:00.500000Z")
        bounds = {"from": "2026-10-08T00:00:00.500000Z", "to": BASE}
        self.assertEqual(self.search(**bounds), [])
        self.assertEqual(self.pet(**bounds), set())


if __name__ == "__main__":
    unittest.main()
