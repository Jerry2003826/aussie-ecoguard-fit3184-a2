from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass(frozen=True)
class Principal:
    sub: str
    email: str = ""
    email_verified: bool = False
    token: str = ""


SPECIES = frozenset({"cat", "dog", "fox", "feral_pig", "pig", "cane_toad", "toad", "rabbit", "bird", "horse", "sheep", "cow", "deer", "kangaroo"})
ALIASES = {"cats": "cat", "dogs": "dog", "foxes": "fox", "wild_boar": "feral_pig", "boar": "feral_pig", "feral_pigs": "feral_pig", "cane_toads": "cane_toad", "rabbits": "rabbit"}
HIDDEN = frozenset({"DELETING", "DELETED", "CANCELLED"})


def normalize_tag(value: str) -> str:
    if not isinstance(value, str):
        raise DomainError("INVALID_TAG", "Tags must be strings.")
    value = re.sub(r"[\s-]+", "_", value.strip().lower())
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value):
        raise DomainError("INVALID_TAG", "Use 1–64 letters, numbers, spaces or underscores for a tag.")
    return ALIASES.get(value, value)


def normalize_tags(values: Any) -> list[str]:
    if not isinstance(values, list) or len(values) > 30:
        raise DomainError("INVALID_TAGS", "Provide at most 30 tags.")
    return sorted({normalize_tag(v) for v in values})


def can_read(principal: Principal, media: dict) -> bool:
    return bool(principal.sub) and media.get("status") not in HIDDEN and (media.get("ownerSub") == principal.sub or media.get("visibility") == "public")


def can_edit(principal: Principal, media: dict) -> bool:
    return bool(principal.sub) and media.get("ownerSub") == principal.sub and media.get("status") not in HIDDEN


def effective_tags(media: dict) -> tuple[list[str], dict[str, int]]:
    removed = set(media.get("manualRemoved", []))
    manual = set(media.get("manualAdded", []))
    automatic = set(media.get("autoTags", []))
    tags = (automatic | manual) - removed
    counts = {k: int(v) for k, v in media.get("autoCounts", {}).items() if k in tags and k in SPECIES and int(v) > 0}
    for tag in manual & SPECIES - removed:
        counts[tag] = max(1, counts.get(tag, 0))
    return sorted(tags), counts


def apply_tags(media: dict, tags: list[str], operation: int) -> dict:
    if type(operation) is not int or operation not in (0, 1):
        raise DomainError("INVALID_OPERATION", "operation must be 1 (add) or 0 (remove).")
    added, removed = set(media.get("manualAdded", [])), set(media.get("manualRemoved", []))
    existing, _ = effective_tags(media)
    for tag in tags:
        if operation == 1:
            if tag not in existing:
                added.add(tag)
                removed.discard(tag)
        elif tag in existing:
            added.discard(tag)
            removed.add(tag)
    result = dict(media, manualAdded=sorted(added), manualRemoved=sorted(removed))
    result["tags"], result["counts"] = effective_tags(result)
    return result


def minimum_counts(value: Any, *, allow_empty: bool = False) -> dict[str, int]:
    if not isinstance(value, dict) or len(value) > 20 or (not value and not allow_empty):
        raise DomainError("INVALID_QUERY", "Provide between 1 and 20 minimum-count conditions.")
    output = {}
    for tag, count in value.items():
        if type(count) is not int or not 1 <= count <= 1000:
            raise DomainError("INVALID_COUNT", "Minimum counts must be integers between 1 and 1000.")
        key = normalize_tag(tag)
        output[key] = max(count, output.get(key, 0))
    return output


def matches(media: dict, conditions: dict[str, int]) -> bool:
    if not conditions or media.get("status") != "READY":
        return False
    tags, counts = effective_tags(media)
    return all((counts.get(tag, 0) if tag in SPECIES else int(tag in tags)) >= count for tag, count in conditions.items())


def sample_times(duration: float) -> list[int]:
    if not math.isfinite(duration) or not 0 < duration <= 120:
        raise DomainError("VIDEO_DURATION", "Videos must be longer than zero and no longer than 120 seconds.")
    return list(range(0, math.ceil(duration), 10))


def aggregate_counts(frames: list[dict]) -> dict[str, int]:
    output: dict[str, int] = {}
    for frame in frames:
        for species, count in frame.get("counts", {}).items():
            species = normalize_tag(species)
            if type(count) is not int or count < 0:
                raise DomainError("INVALID_DETECTION", "Detector returned an invalid count.", 502)
            output[species] = max(output.get(species, 0), count)
    return {k: v for k, v in output.items() if v > 0}


def parse_time(value: str) -> datetime:
    """Compare instants, including legacy timestamps with variable precision/offsets."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError, AttributeError, TypeError, OverflowError):
        raise DomainError("INVALID_DATE", "Dates must include an ISO 8601 timezone.")


def iso_time(value: str | None = None) -> str:
    parsed = datetime.now(timezone.utc) if value is None else parse_time(value)
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right) or not all(math.isfinite(x) for x in left + right):
        return -1.0
    denom = math.sqrt(sum(x*x for x in left) * sum(x*x for x in right))
    return sum(a*b for a, b in zip(left, right)) / denom if denom else -1.0
