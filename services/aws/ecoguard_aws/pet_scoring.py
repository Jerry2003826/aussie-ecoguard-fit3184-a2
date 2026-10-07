"""Versioned semantic candidate scores, never an individual-identity probability."""
from functools import lru_cache
import json
import math
from pathlib import Path

from ecoguard_domain import DomainError
from ecoguard_domain.rules import cosine

CALIBRATED_MANIFEST = "ecoguard-models-2026-10-08-v3"


@lru_cache(maxsize=1)
def calibration():
    try:
        policy = json.loads(Path(__file__).with_name("pet-calibration.json").read_text(encoding="utf-8"))
        if (policy["policyVersion"] != "siglip2-semantic-platt-v1"
                or policy["semanticOnly"] is not True
                or policy["combination"] != "and_geometric_mean"
                or not isinstance(policy["featureVersion"], str)):
            raise ValueError("Invalid policy contract")
        for mode in ("image", "text"):
            values = policy[mode]
            if (not all(type(values[k]) in (int, float) and math.isfinite(values[k])
                        for k in ("scale", "bias", "minimumScore"))
                    or values["scale"] <= 0 or values["minimumScore"] != 0.5):
                raise ValueError("Invalid frozen calibration")
        return policy
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise DomainError("RETRIEVAL_POLICY_UNAVAILABLE", "The model's retrieval policy is unavailable.", 503) from error


def calibrated_score(raw, values):
    z = values["scale"] * raw + values["bias"]
    # Avoid exp overflow at either end of the monotonic transform.
    return 1 / (1 + math.exp(-z)) if z >= 0 else math.exp(z) / (1 + math.exp(z))


def pair_score(query, target, *, mode, description=None, manifest_version=None):
    version = query.get("featureVersion")
    if not version or target.get("featureVersion") != version:
        return -1.0
    if description is not None and description.get("featureVersion") != version:
        return -1.0
    raw = cosine(query["vector"], target["vector"])
    if manifest_version != CALIBRATED_MANIFEST:
        # Retain historical tasks' scoring; never apply the new calibration to
        # old embeddings or silently mix their feature versions.
        return (raw + cosine(description["vector"], target["vector"])) / 2 if description else raw
    policy = calibration()
    if version != policy["featureVersion"]:
        raise DomainError("FEATURE_VERSION_MISMATCH", "The query does not match the frozen retrieval model.", 502)
    first_mode = "text" if mode == "text" else "image"
    score = calibrated_score(raw, policy[first_mode])
    if score < policy[first_mode]["minimumScore"]:
        return -1.0
    if description is not None:
        text_score = calibrated_score(cosine(description["vector"], target["vector"]), policy["text"])
        if text_score < policy["text"]["minimumScore"]:
            return -1.0
        score = math.sqrt(score * text_score)
    return score
