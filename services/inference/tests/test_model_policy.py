"""Pure policy/contract regression tests; no downloaded model or accuracy claims."""
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.errors import ServiceError
from app.manifest import load_manifest
from app.runtime import prepare_embedding_text, winning_class
from app.schemas import Detection


def test_new_text_policy_matches_training_lowercase_without_changing_legacy():
    assert prepare_embedding_text("  A GINGER Cat Wearing A Collar  ", "lowercase") == "a ginger cat wearing a collar"
    assert prepare_embedding_text("A GINGER Cat", "legacy") == "A GINGER Cat"


def test_shared_class_bank_resolves_species_instead_of_prompt_grounding_confidence():
    # DINO's highest independent prompt used to be fox; a shared crop classifier
    # selects the best image/text class, independent of that other prompt score.
    assert winning_class([0.12, 0.04, 0.06], 112, .5)[0] == 0
    assert winning_class([0.04, 0.12, 0.06], 112, .5)[0] == 1
    assert winning_class([0.12, 0.12, 0.06], 112, .5) is None


def test_reject_class_and_ambiguous_classes_are_not_promoted_to_pet():
    assert winning_class([.01, .02, .15], 112, .5)[0] == 2
    assert winning_class([.10, .10, .10], 112, .5) is None
    with pytest.raises(ServiceError):
        winning_class([float("nan"), .1], 112, .5)


def test_detection_provenance_supports_hybrid_classifier_and_legacy():
    for source in ("aws-rekognition", "grounding-dino", "grounding-dino+siglip2"):
        detection = Detection(species="cat", confidence=.9, source=source,
                              bbox={"x":0,"y":0,"width":1,"height":1})
        assert detection.source == source


def test_locked_crop_classifier_requires_explicit_text_and_rejection_policy(locked_settings):
    path = locked_settings.manifest_path
    with open(path, encoding="utf-8") as stream:
        manifest = json.load(stream)
    manifest["detector"].update(promptStrategy="object_then_siglip", proposalPrompts=["cat.", "dog."],
        classifier={"template":"this is a photo of a {label}.", "minimumClassProbability":.5,
                    "rejectLabels":["car","background"]})
    manifest["embedding"]["textPreprocessing"] = "lowercase"
    policy = json.loads((Path(__file__).resolve().parents[3] / "config/model.locked.json").read_text())["retrievalCalibration"]
    policy["featureVersion"] = f"{manifest['embedding']['featureVersion']}:{manifest['embedding']['revision']}:{manifest['preprocessingVersion']}"
    manifest["retrievalCalibration"] = policy
    def save():
        data = json.dumps(manifest).encode()
        with open(path, "wb") as stream:
            stream.write(data)
        return replace(locked_settings, manifest_sha256=hashlib.sha256(data).hexdigest())
    assert load_manifest(save())["detector"]["promptStrategy"] == "object_then_siglip"
    for key, invalid in (("minimumClassProbability",.1),("rejectLabels",[]),("template","guess {label}")):
        old = manifest["detector"]["classifier"][key]
        manifest["detector"]["classifier"][key] = invalid
        with pytest.raises(ServiceError):
            load_manifest(save())
        manifest["detector"]["classifier"][key] = old
    manifest["embedding"]["textPreprocessing"] = "legacy"
    with pytest.raises(ServiceError):
        load_manifest(save())


@pytest.mark.parametrize("mutation", ["missing", "version", "nan", "slope", "threshold", "identity", "holdout"])
def test_v3_calibration_fails_closed_for_missing_or_incompatible_policy(tmp_path, settings, mutation):
    manifest = json.loads((Path(__file__).resolve().parents[3] / "config/model.locked.json").read_text())
    if mutation == "missing":
        del manifest["retrievalCalibration"]
    elif mutation == "version":
        manifest["retrievalCalibration"]["featureVersion"] = "older-feature-version"
    elif mutation == "nan":
        manifest["retrievalCalibration"]["text"]["bias"] = float("nan")
    elif mutation == "slope":
        manifest["retrievalCalibration"]["image"]["scale"] = -1
    elif mutation == "threshold":
        manifest["retrievalCalibration"]["text"]["minimumScore"] = .2
    elif mutation == "identity":
        manifest["retrievalCalibration"]["semanticOnly"] = False
    else:
        manifest["retrievalCalibration"]["fit"]["sourceCounts"]["holdoutUsed"] = 8
    raw = json.dumps(manifest).encode()
    path = tmp_path / "bad-policy.json"
    path.write_bytes(raw)
    with pytest.raises(ServiceError):
        load_manifest(replace(settings, manifest_path=str(path), manifest_sha256=hashlib.sha256(raw).hexdigest()))


def test_archived_v2_and_v3_remain_distinct_explicit_contracts(settings):
    root = Path(__file__).resolve().parents[3]
    versions = []
    for name in ("config/manifests/ecoguard-models-2026-10-07-v2.json", "config/model.locked.json"):
        path = root / name
        value = load_manifest(replace(settings, manifest_path=str(path), manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        versions.append(value["manifestVersion"])
    assert versions == ["ecoguard-models-2026-10-07-v2", "ecoguard-models-2026-10-08-v3"]
