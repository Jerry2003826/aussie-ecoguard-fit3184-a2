"""Real job_view contract with local records; scores are fixtures, never AI evidence.

The frontend contract test consumes --emit output directly so it cannot pass by
inventing a different response shape than the Python service actually returns.
"""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "packages/domain"), str(ROOT / "services/aws")]
from ecoguard_domain import Principal
from ecoguard_aws.config import Config
from ecoguard_aws.repository import MemoryRepository, Write
from ecoguard_aws.service import Service
from ecoguard_aws.storage import MemoryStorage


def job_payload(mode, *, scored=True, private=False, manifest=None):
    config = Config(mode="local")
    repo = MemoryRepository()
    service = Service(config, repo, MemoryStorage(config))
    repo.transact([Write("media", {
        "mediaId": "a" * 32, "ownerSub": "other-owner", "filename": "pet-contract-fixture.jpg",
        "fileType": "image", "status": "READY", "visibility": "private" if private else "public",
        "tags": ["cat"], "counts": {"cat": 1}, "observedAt": "2026-10-08T00:00:00Z",
    })])
    op = {"jobId": "pet-contract-" + mode, "queryId": "pet-contract-" + mode, "type": "QUERY",
          "queryType": "pet", "ownerSub": "searcher", "status": "QUERY_READY", "cleaned": True,
          "frames": [], "resultIds": ["a" * 32], "scores": {"a" * 32: 0.556087} if scored else {},
          "description": "A ginger cat" if mode in ("text", "combined") else ""}
    if mode in ("image", "combined"):
        op.update(filename="query-photo.jpg", fileType="image")
    if manifest:
        op["manifestVersion"] = manifest
    return service.job_view(op, Principal("searcher"))


class PetResultContractTests(unittest.TestCase):
    def assert_mode(self, mode, expected):
        item = job_payload(mode)["items"][0]
        self.assertEqual(item["matchScore"], 0.556087)
        self.assertEqual(item["matchReason"], expected)
        self.assertNotIn("%", item["matchReason"])

    def test_image_reason_survives_cleaned_frame_list(self):
        self.assert_mode("image", "Image similarity to a detected pet. Possible sighting, not confirmed identity.")

    def test_description_reason_does_not_claim_image_input(self):
        self.assert_mode("text", "Description similarity to a detected pet. Possible sighting, not confirmed identity.")

    def test_combined_reason_describes_both_inputs_and_equal_weight(self):
        self.assert_mode("combined", "Image and description similarity, weighted equally for the same detected pet. Possible sighting, not confirmed identity.")

    def test_v3_combined_reason_describes_both_gates(self):
        item = job_payload("combined", manifest="ecoguard-models-2026-10-08-v3")["items"][0]
        self.assertIn("both must match the same detected pet", item["matchReason"])
        self.assertNotIn("weighted equally", item["matchReason"])
        self.assertIn("not confirmed identity", item["matchReason"])

    def test_unscored_results_do_not_gain_match_claims(self):
        item = job_payload("image", scored=False)["items"][0]
        self.assertNotIn("matchScore", item)
        self.assertNotIn("matchReason", item)

    def test_score_does_not_reveal_another_users_private_result(self):
        self.assertEqual(job_payload("text", private=True)["items"], [])


if __name__ == "__main__":
    if "--emit" in sys.argv:
        print(json.dumps({mode: job_payload(mode) for mode in ("image", "text", "combined")}))
    else:
        unittest.main()
