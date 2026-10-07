"""Reproduce the frozen semantic calibration from public, labeled cosine pairs.

No images, embeddings, models, credentials or network are used. This fits breed
and species similarity, never the probability that two photos show one animal.
Run `python scripts/fit_pet_calibration.py --check` to verify the shipped policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGULARIZATION = 0.01


def sigmoid(value):
    return 1 / (1 + math.exp(-value)) if value >= 0 else math.exp(value) / (1 + math.exp(value))


def fit(rows):
    xs = [row["cosine"] for row in rows]
    ys = [row["positive"] for row in rows]
    if (not xs or not all(type(x) in (int, float) and math.isfinite(x) and -1 <= x <= 1 for x in xs)
            or not all(type(y) is bool for y in ys) or set(ys) != {True, False}):
        raise ValueError("Calibration requires finite cosine pairs and both Boolean labels.")
    mean = sum(xs) / len(xs)
    std = math.sqrt(sum((x - mean) ** 2 for x in xs) / len(xs))
    if std <= 1e-12:
        raise ValueError("Calibration needs nonconstant scores.")
    zs = [(x - mean) / std for x in xs]

    def loss(a, b):
        logits = [a * z + b for z in zs]
        return (sum(max(t, 0) - y * t + math.log1p(math.exp(-abs(t))) for t, y in zip(logits, ys)) / len(xs)
                + REGULARIZATION * (a * a + b * b) / 2)

    a, b = 1., 0.
    for iteration in range(100):
        ps = [sigmoid(a * z + b) for z in zs]
        ws = [p * (1 - p) for p in ps]
        ga = sum((p - y) * z for p, y, z in zip(ps, ys, zs)) / len(xs) + REGULARIZATION * a
        gb = sum(p - y for p, y in zip(ps, ys)) / len(xs) + REGULARIZATION * b
        haa = sum(w * z * z for w, z in zip(ws, zs)) / len(xs) + REGULARIZATION
        hab = sum(w * z for w, z in zip(ws, zs)) / len(xs)
        hbb = sum(ws) / len(xs) + REGULARIZATION
        determinant = haa * hbb - hab * hab
        da = (hbb * ga - hab * gb) / determinant
        db = (haa * gb - hab * ga) / determinant
        if max(abs(ga), abs(gb)) < 1e-11:
            break
        step = 1.
        while step > 1e-12:
            next_a, next_b = max(1e-9, a - step * da), b - step * db
            if loss(next_a, next_b) < loss(a, b):
                break
            step /= 2
        a, b = next_a, next_b
    scale = a / std
    bias = b - scale * mean
    if not scale > 0 or not all(math.isfinite(value) for value in (scale, bias)):
        raise ValueError("Calibration did not yield a finite monotonic policy.")
    positive = [x for x, y in zip(xs, ys) if y]
    negative = [x for x, y in zip(xs, ys) if not y]
    policy = {"scale": scale, "bias": bias, "minimumScore": 0.5}
    stats = {"pairs": len(xs), "positivePairs": len(positive), "negativePairs": len(negative),
             "positiveCosineRange": [min(positive), max(positive)], "negativeCosineRange": [min(negative), max(negative)],
             "cosineAtDecisionBoundary": -bias / scale, "fitIterations": iteration + 1,
             "truePositive": sum(sigmoid(scale * x + bias) >= .5 for x in positive),
             "falsePositive": sum(sigmoid(scale * x + bias) >= .5 for x in negative),
             "standardizationMean": mean, "standardizationStd": std}
    return policy, stats


def reproduce(pairs_path, context_path):
    pairs = json.loads(pairs_path.read_text(encoding="utf-8"))
    policy = json.loads(context_path.read_text(encoding="utf-8"))
    if (set(pairs) != {"image", "text"} or policy["semanticOnly"] is not True
            or policy["fit"]["regularization"] != REGULARIZATION
            or policy["fit"]["sourceCounts"]["holdoutUsed"] != 0):
        raise ValueError("Only the frozen calibration split is permitted.")
    allowed = set(policy["fit"]["calibrationSourceIds"])
    for mode in ("image", "text"):
        for row in pairs[mode]:
            if row["targetId"] not in allowed or mode == "image" and row["queryId"] not in allowed:
                raise ValueError("A pair references a source outside calibration.")
        policy[mode], policy["fit"][mode] = fit(pairs[mode])
    canonical = json.dumps(pairs, sort_keys=True, separators=(",", ":")).encode()
    policy["fit"]["dataSha256"] = hashlib.sha256(canonical).hexdigest()
    return policy


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=Path, default=ROOT / "config/calibration/pet-semantic-v3-pairs.json")
    parser.add_argument("--context", type=Path, default=ROOT / "config/calibration/pet-semantic-v3-context.json")
    parser.add_argument("--manifest", type=Path, default=ROOT / "config/model.locked.json")
    parser.add_argument("--check", action="store_true", help="Require exact data equality with manifest retrievalCalibration.")
    parser.add_argument("--output", type=Path, help="Optional output file; never edits the deployed manifest.")
    args = parser.parse_args(argv)
    try:
        policy = reproduce(args.pairs, args.context)
        matches = policy == json.loads(args.manifest.read_text(encoding="utf-8"))["retrievalCalibration"] if args.check else None
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(policy, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print(json.dumps({"status": "mismatch" if matches is False else "reproduced", "matchesManifest": matches,
                          "pairDataSha256": policy["fit"]["dataSha256"], "imagePairs": policy["fit"]["image"]["pairs"],
                          "textPairs": policy["fit"]["text"]["pairs"], "semanticOnly": True, "holdoutUsed": 0}))
        return 1 if matches is False else 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "invalid_calibration", "error": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
