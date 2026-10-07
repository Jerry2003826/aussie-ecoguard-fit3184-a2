import math

import pytest
from pydantic import ValidationError

from app.errors import ServiceError
from app.runtime import normalized_vector
from app.schemas import InferRequest
from app.taxonomy import Taxonomy, aggregate_counts, frame_counts, merge_detections


def test_video_counts_are_maxima_and_mixed_species_are_union():
    assert aggregate_counts([{"counts": {"cat": 1}}, {"counts": {"cat": 3, "fox": 2}}, {"counts": {"cat": 2}}]) == {"cat": 3, "fox": 2}
    assert aggregate_counts([{"counts": {"cat": 1}}] * 3) == {"cat": 1}


def test_same_object_is_not_double_counted_across_models(settings):
    taxonomy = Taxonomy(settings.taxonomy_path, "taxonomy-2026-10-07-v1")
    box = {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}
    merged = merge_detections([
        {"species": "cat", "confidence": 0.95, "source": "aws-rekognition", "bbox": box},
        {"species": "cat", "confidence": 0.92, "source": "grounding-dino", "bbox": box},
        {"species": "cat", "confidence": 0.85, "source": "grounding-dino", "bbox": {**box, "x": 0.6}},
    ], taxonomy, 0.5)
    assert frame_counts(merged) == {"cat": 2}


def test_generic_pig_is_not_silently_promoted(settings):
    taxonomy = Taxonomy(settings.taxonomy_path, "taxonomy-2026-10-07-v1")
    assert taxonomy.canonical("Pig") == "pig"
    assert taxonomy.canonical("Cane Toad") == "cane_toad"
    assert taxonomy.canonical("Toad") == "toad"
    assert taxonomy.canonical("frog") is None
    box = {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}
    merged = merge_detections([
        {"species": "pig", "confidence": 0.95, "bbox": box},
        {"species": "feral_pig", "confidence": 0.8, "bbox": box},
    ], taxonomy, 0.5)
    assert frame_counts(merged) == {"pig": 1}
    assert taxonomy.classification(frame_counts(merged)) == "uncertain"
    assert taxonomy.classification({"cat": 1, "fox": 2}) == "mixed"


def test_invalid_frame_schedule_and_missing_hash_rejected():
    body = {"jobId": "job1", "ownerSub": "user1", "generation": 1, "manifestVersion": "v1", "mediaType": "video",
            "frames": [{"frameId": "f1", "timestampSeconds": 5, "url": "https://example.com/f.jpg", "sha256": "a" * 64, "entryLabels": []}]}
    with pytest.raises(ValidationError):
        InferRequest.model_validate(body)
    body["frames"][0]["timestampSeconds"] = 0
    del body["frames"][0]["sha256"]
    with pytest.raises(ValidationError):
        InferRequest.model_validate(body)


def test_embedding_vectors_normalized_and_nonfinite_rejected():
    vector = normalized_vector([3, 4])
    assert vector == [0.6, 0.8]
    assert math.isclose(sum(item * item for item in vector), 1)
    for invalid in ([0, 0], [math.nan, 1], [math.inf, 1], []):
        with pytest.raises(ServiceError):
            normalized_vector(invalid)
