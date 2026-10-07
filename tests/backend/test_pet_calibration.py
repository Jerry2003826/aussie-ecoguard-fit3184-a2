"""Version fences and modality gates; synthetic vectors are not model evidence."""
import json
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "packages/domain"), str(ROOT / "services/aws")]
from ecoguard_aws.pet_scoring import CALIBRATED_MANIFEST, calibration, calibrated_score, pair_score
from ecoguard_aws.config import Config
from ecoguard_aws.repository import MemoryRepository, Write
from ecoguard_aws.service import Service
from ecoguard_aws.storage import MemoryStorage
from ecoguard_aws.workers import Workers

POLICY = {"featureVersion": "explicit-fixture-v3", "image": {"scale": 10., "bias": -5., "minimumScore": .5},
          "text": {"scale": 100., "bias": -8., "minimumScore": .5}}


def feature(x, *, version="explicit-fixture-v3"):
    return {"vector": [x, math.sqrt(1-x*x)], "featureVersion": version, "species": "cat"}


class PetCalibrationTests(unittest.TestCase):
    @patch("ecoguard_aws.pet_scoring.calibration", return_value=POLICY)
    def test_scoring_past_deadline_cannot_publish_a_ready_result(self, _):
        config = Config(mode="local", model_manifest_version=CALIBRATED_MANIFEST)
        repo = MemoryRepository()
        storage = MemoryStorage(config)
        service = Service(config, repo, storage)
        value = {"vector": [1., 0.], "featureVersion": POLICY["featureVersion"], "species": "cat"}
        storage.put("library-feature", json.dumps({"features": [value]}).encode())
        storage.put("query-source", b"owned query input")
        media = {"mediaId": "public-pet", "ownerSub": "gallery", "visibility": "public", "status": "READY",
                 "observedAt": "2026-01-01T00:00:00Z", "featureKey": "library-feature",
                 "tags": ["cat"], "counts": {"cat": 1}, "fileType": "image"}
        op = {"operationId": "late-query", "type": "QUERY", "queryType": "pet", "ownerSub": "searcher",
              "status": "INFERRING", "leaseUntil": 1600, "generation": 1, "attemptId": 1,
              "manifestVersion": CALIBRATED_MANIFEST, "cleanupDeadline": 1010, "sourceKey": "query-source"}
        repo.transact([Write("media", media), *service.index_writes({"status": "UPLOADING"}, media), Write("operations", op)])
        clock = [1000]
        read = storage.read
        def slow_read(key, *args):
            data = read(key, *args)
            clock[0] = 1011
            return data
        with patch("ecoguard_aws.workers.time.time", side_effect=lambda: clock[0]), patch.object(storage, "read", side_effect=slow_read):
            Workers(service)._query_result(service.operation("late-query"), {"features": [value]})
        result = service.operation("late-query")
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["error"]["code"], "QUERY_EXPIRED")
        self.assertNotIn("resultIds", result)
        self.assertTrue(result["cleaned"])
        self.assertNotIn("query-source", storage.objects)
        self.assertIn("library-feature", storage.objects)

    def test_packaged_policy_matches_frozen_model_manifest(self):
        manifest = json.loads((ROOT / "config/model.locked.json").read_text())
        self.assertEqual(manifest["manifestVersion"], CALIBRATED_MANIFEST)
        self.assertEqual(calibration(), manifest["retrievalCalibration"])
        self.assertTrue(calibration()["semanticOnly"])
        self.assertEqual(calibration()["fit"]["thresholdRule"], "fixed 0.5 before holdout")
        self.assertEqual(calibration()["fit"]["sourceCounts"]["holdoutUsed"], 0)

    @patch("ecoguard_aws.pet_scoring.calibration", return_value=POLICY)
    def test_low_raw_text_uses_its_own_scale(self, _):
        target = {**feature(1), "vector": [1., 0.]}
        self.assertGreater(pair_score(feature(.1), target, mode="text", manifest_version=CALIBRATED_MANIFEST), .5)
        self.assertEqual(pair_score(feature(.1), target, mode="text", manifest_version="old-manifest"), .1)

    @patch("ecoguard_aws.pet_scoring.calibration", return_value=POLICY)
    def test_identical_photo_cannot_override_irrelevant_text(self, _):
        target = {**feature(1), "vector": [1., 0.]}
        self.assertEqual(pair_score(target, target, mode="combined", description=feature(0), manifest_version=CALIBRATED_MANIFEST), -1)

    @patch("ecoguard_aws.pet_scoring.calibration", return_value=POLICY)
    def test_combined_score_requires_both_on_the_same_crop(self, _):
        config = Config(mode="local", model_manifest_version=CALIBRATED_MANIFEST)
        repo = MemoryRepository()
        storage = MemoryStorage(config)
        service = Service(config, repo, storage)
        query = {"vector": [1., 0.], "featureVersion": POLICY["featureVersion"], "species": "cat"}
        text = {"vector": [0., 1.], "featureVersion": POLICY["featureVersion"]}
        # Each query modality separately matches a different animal crop.
        storage.put("features", json.dumps({"features": [query, {**query, "vector": [0., 1.]}]}).encode())
        media = {"mediaId": "two-crops", "ownerSub": "gallery", "visibility": "public",
                 "status": "READY", "observedAt": "2026-01-01T00:00:00Z", "featureKey": "features",
                 "tags": ["cat"], "counts": {"cat": 2}, "fileType": "image"}
        repo.transact([Write("media", media), *service.index_writes({"status": "UPLOADING"}, media)])
        op = {"operationId": "query", "ownerSub": "searcher", "type": "QUERY", "queryType": "pet",
              "status": "INFERRING", "leaseUntil": 0, "manifestVersion": CALIBRATED_MANIFEST,
              "description": "explicit synthetic text"}
        repo.transact([Write("operations", op)])
        Workers(service)._query_result(service.operation("query"), {"features": [query], "descriptionFeature": text})
        self.assertEqual(service.operation("query")["resultIds"], [])
        # Positive control: the same indexed candidate must be reachable when a
        # single animal crop satisfies both modalities.
        storage.put("features", json.dumps({"features": [{**query, "vector": [math.sqrt(.5), math.sqrt(.5)]}]}).encode())
        repo.transact([Write("operations", {**op, "operationId": "positive-control"})])
        Workers(service)._query_result(service.operation("positive-control"), {"features": [query], "descriptionFeature": text})
        self.assertEqual(service.operation("positive-control")["resultIds"], ["two-crops"])

    def test_versions_must_match_for_each_modality(self):
        self.assertEqual(pair_score(feature(1), feature(1, version="old"), mode="image", manifest_version=CALIBRATED_MANIFEST), -1)
        self.assertEqual(pair_score(feature(1), feature(1), mode="combined", description=feature(1, version="old"), manifest_version=CALIBRATED_MANIFEST), -1)

    def test_historical_combined_tasks_keep_their_scoring(self):
        target = {**feature(1), "vector": [1., 0.]}
        self.assertAlmostEqual(pair_score(target, target, mode="combined", description=feature(0), manifest_version="old-manifest"), .5)

    def test_stable_transform_does_not_overflow(self):
        self.assertEqual(calibrated_score(1, {"scale": 10000., "bias": 0}), 1.)
        self.assertEqual(calibrated_score(-1, {"scale": 10000., "bias": 0}), 0.)


if __name__ == "__main__":
    unittest.main()
