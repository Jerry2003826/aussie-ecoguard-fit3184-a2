import hashlib
import json
from collections import Counter
from pathlib import Path

from .errors import ServiceError


class Taxonomy:
    def __init__(self, path: str, expected_version: str, expected_sha256: str | None = None):
        raw = Path(path).read_bytes()
        if expected_sha256 is not None and hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise ServiceError("TAXONOMY_INTEGRITY_FAILED", "The taxonomy failed SHA-256 verification.", 503)
        self.data = json.loads(raw)
        if self.data.get("version") != expected_version:
            raise ServiceError("TAXONOMY_VERSION_MISMATCH", "The model taxonomy version is not available.", 503)
        self.species = self.data["species"]
        self.aliases = {self.normalize(alias): name for name, item in self.species.items()
                        for alias in [name, *item["aliases"], *item.get("prompts", [])]}
        self.prompts = list(dict.fromkeys(prompt for value in self.species.values() for prompt in value.get("prompts", [])))
        self.families = self.data.get("overlapFamilies", [])

    @staticmethod
    def normalize(value: str) -> str:
        return " ".join(value.lower().replace("_", " ").strip(" .").split())

    def canonical(self, value: str) -> str | None:
        # Exact vocabulary matches only. Never promote a generic pig/toad.
        return self.aliases.get(self.normalize(value))

    def category(self, species: str) -> str:
        return self.species[species]["category"]

    def classification(self, counts: dict[str, int]) -> str:
        categories = {self.category(key) for key, count in counts.items() if count > 0}
        if {"pet", "invasive"} <= categories:
            return "mixed"
        if "invasive" in categories:
            return "invasive"
        if "pet" in categories:
            return "pet"
        return "uncertain"


def box_iou(left: dict, right: dict) -> float:
    x1, y1 = max(left["x"], right["x"]), max(left["y"], right["y"])
    x2 = min(left["x"] + left["width"], right["x"] + right["width"])
    y2 = min(left["y"] + left["height"], right["y"] + right["height"])
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    union = left["width"] * left["height"] + right["width"] * right["height"] - intersection
    return intersection / union if union else 0


def merge_detections(detections: list[dict], taxonomy: Taxonomy, iou_threshold: float) -> list[dict]:
    """Suppress same-object duplicates across models, including generic family labels."""
    result = []
    for detection in sorted(detections, key=lambda item: item["confidence"], reverse=True):
        overlapping = None
        for existing in result:
            same_family = existing["species"] == detection["species"] or any(
                existing["species"] in family and detection["species"] in family for family in taxonomy.families)
            overlap = box_iou(existing["bbox"], detection["bbox"])
            if (same_family and overlap >= iou_threshold) or overlap >= 0.85:
                overlapping = existing
                break
        if overlapping is None:
            result.append(detection)
        # High confidence generic labels must not create a second animal or
        # be silently upgraded to an invasive species by a weaker detection.
    return result


def frame_counts(detections: list[dict]) -> dict[str, int]:
    return dict(sorted(Counter(item["species"] for item in detections).items()))


def aggregate_counts(frames: list[dict]) -> dict[str, int]:
    result: dict[str, int] = {}
    for frame in frames:
        for species, count in frame["counts"].items():
            result[species] = max(result.get(species, 0), count)
    return dict(sorted(result.items()))
