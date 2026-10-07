"""Evaluate hashed, precomputed pet features without models, credentials or network.

Usage: python scripts/evaluate_pet_quality.py manifest.json --output report.json

Schema 1 manifest: benchmarkKind (semantic-smoke or pet-identity),
modelManifestVersion, featureVersion, featureBundle {path, sha256}, assets
[{id, path, sha256, sourceUrl, license, split, individualId?, sequenceId?}],
gallery [{mediaId, assetId}], queries [{queryId, split, mode, assetId?,
description?, positiveIds, individualId?}], optional identityLabels
{humanVerified: true, labeler, method}, and optional qualityGates
{recallAt1, recallAt5, maxNoMatchFalsePositiveRate}. Splits are calibration/holdout;
modes are photo/text/combined. Gates apply independently to each holdout mode.

The hashed feature bundle has schemaVersion: 1, the same two versions,
provenance {kind: real-model|fixture|mock, runId}, media {mediaId: [feature]},
queries {queryId: {features: [feature], descriptionFeature?, error?}}, and
optional mediaErrors {mediaId: {code, message}}. A feature has vector,
featureVersion, optional species/frameId/bbox. Text queries use one text feature;
combined queries use photo features plus descriptionFeature. Failed inference
must be represented by an explicit error, never by a missing entry.

Pet identity requires human individual labels, at least 50 distinct holdout query
source hashes, no query/gallery source reuse, and no cross-split identity,
sequence or byte-hash reuse. Text identity queries also need an assetId as their
human description's provenance anchor; that image is not used as a query feature.
Keep source assets and feature bundles in ignored storage, such as tmp/.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "packages/domain"), str(ROOT / "services/aws")]
from ecoguard_aws.config import Config
from ecoguard_aws.repository import MemoryRepository, Write
from ecoguard_aws.service import Service
from ecoguard_aws.storage import MemoryStorage
from ecoguard_aws.pet_scoring import CALIBRATED_MANIFEST, calibration
from ecoguard_aws.workers import Workers

THRESHOLD = 0.2
MIN_IDENTITY_HOLDOUT_SOURCES = 50
MODES = ("photo", "text", "combined")
SPLITS = ("calibration", "holdout")
CLAIMS = {name: False for name in (
    "modelAcceptance", "identityAcceptance", "cloudAcceptance",
    "deploymentAcceptance", "fullAssignmentAcceptance",
)}


class InvalidBenchmark(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def require(condition, code, message):
    if not condition:
        raise InvalidBenchmark(code, message)


def fields(value, required, optional, context):
    require(isinstance(value, dict), "INVALID_OBJECT", f"{context} must be an object.")
    require(set(required) <= value.keys(), "MISSING_FIELD", f"{context} requires {sorted(set(required)-value.keys())}.")
    require(value.keys() <= set(required) | set(optional), "UNKNOWN_FIELD", f"{context} has unknown fields: {sorted(value.keys()-set(required)-set(optional))}.")


def text(value, context):
    require(isinstance(value, str) and bool(value.strip()), "INVALID_TEXT", f"{context} must be nonempty text.")
    return value


def identifier(value, context):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value),
            "INVALID_ID", f"{context} must be a stable 1-128 character ID.")
    return value


def finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def unique_rows(rows, key, context):
    require(isinstance(rows, list) and bool(rows), "EMPTY_COLLECTION", f"{context} must be a nonempty list.")
    result = {}
    for row in rows:
        require(isinstance(row, dict), "INVALID_OBJECT", f"Each {context} row must be an object.")
        value = identifier(row.get(key), f"{context}.{key}")
        require(value not in result, "DUPLICATE_ID", f"Duplicate {context} ID: {value}.")
        result[value] = row
    return result


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hashed_path(base, value, checksum, context):
    text(value, context + ".path")
    require(isinstance(checksum, str) and re.fullmatch(r"[a-f0-9]{64}", checksum),
            "INVALID_HASH", f"{context} requires a lowercase SHA-256 hash.")
    try:
        path = (base / value).resolve()
        require(path.is_file(), "SOURCE_UNAVAILABLE", f"{context} file is unavailable: {path.name}.")
        require(sha256(path) == checksum, "HASH_MISMATCH", f"{context} bytes do not match their SHA-256.")
    except (OSError, ValueError) as error:
        if isinstance(error, InvalidBenchmark):
            raise
        raise InvalidBenchmark("SOURCE_UNAVAILABLE", f"{context} cannot be read: {type(error).__name__}.") from error
    return path


def load_json(path):
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, "DUPLICATE_JSON_KEY", f"Duplicate JSON key: {key}.")
            result[key] = value
        return result
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InvalidBenchmark("INVALID_JSON", f"Cannot read JSON from {path.name}: {type(error).__name__}.") from error


def validate_error(value, context):
    fields(value, {"code", "message"}, set(), context)
    identifier(value["code"], context + ".code")
    text(value["message"], context + ".message")


def validate_identity(manifest, assets, gallery, queries):
    labels = manifest.get("identityLabels")
    fields(labels, {"humanVerified", "labeler", "method"}, set(), "identityLabels")
    require(labels["humanVerified"] is True, "HUMAN_LABELS_REQUIRED", "Identity evaluation requires human-verified individual labels.")
    text(labels["labeler"], "identityLabels.labeler")
    text(labels["method"], "identityLabels.method")
    seen = {key: {} for key in ("individualId", "sequenceId", "sha256")}
    for asset in assets.values():
        identifier(asset.get("individualId"), "identity asset.individualId")
        for key, mapping in seen.items():
            value = asset.get(key)
            if value is not None:
                require(value not in mapping or mapping[value] == asset["split"], "SPLIT_LEAKAGE",
                        f"{key} is reused between calibration and holdout.")
                mapping[value] = asset["split"]
    gallery_assets = {row["assetId"] for row in gallery.values()}
    gallery_hashes = {assets[value]["sha256"] for value in gallery_assets}
    holdout_sources = set()
    for query in queries.values():
        require(query.get("assetId") in assets, "IDENTITY_SOURCE_REQUIRED", f"{query['queryId']} needs a human-labeled source asset, including text mode.")
        asset = assets[query["assetId"]]
        require(query["assetId"] not in gallery_assets and asset["sha256"] not in gallery_hashes,
                "SELF_MATCH_LEAKAGE", f"{query['queryId']} reuses gallery source bytes.")
        identity = asset["individualId"]
        require(query.get("individualId", identity) == identity, "IDENTITY_LABEL_MISMATCH", f"{query['queryId']} has inconsistent individual labels.")
        positives = {media_id for media_id, row in gallery.items() if assets[row["assetId"]]["individualId"] == identity}
        require(set(query["positiveIds"]) == positives, "POSITIVE_LABEL_MISMATCH",
                f"{query['queryId']} positiveIds do not match the human individual labels.")
        if query["split"] == "holdout":
            holdout_sources.add(asset["sha256"])
    require(len(holdout_sources) >= MIN_IDENTITY_HOLDOUT_SOURCES, "IDENTITY_HOLDOUT_UNAVAILABLE",
            f"A genuine independent holdout set is unavailable: need {MIN_IDENTITY_HOLDOUT_SOURCES} unique query-source hashes, found {len(holdout_sources)}.")
    return len(holdout_sources)


def validate(manifest_path):
    manifest = load_json(manifest_path)
    fields(manifest, {"schemaVersion", "benchmarkKind", "modelManifestVersion", "featureVersion", "featureBundle", "assets", "gallery", "queries"},
           {"identityLabels", "qualityGates"}, "manifest")
    require(type(manifest["schemaVersion"]) is int and manifest["schemaVersion"] == 1, "SCHEMA_VERSION", "Only schemaVersion 1 is supported.")
    require(manifest["benchmarkKind"] in ("semantic-smoke", "pet-identity"), "BENCHMARK_KIND", "Use semantic-smoke or pet-identity.")
    for key in ("modelManifestVersion", "featureVersion"):
        text(manifest[key], key)
    assets = unique_rows(manifest["assets"], "id", "assets")
    for asset_id, asset in assets.items():
        fields(asset, {"id", "path", "sha256", "sourceUrl", "license", "split"}, {"individualId", "sequenceId"}, "asset " + asset_id)
        require(asset["split"] in SPLITS, "INVALID_SPLIT", f"{asset_id} has an invalid split.")
        text(asset["license"], asset_id + ".license")
        try:
            source = urlparse(text(asset["sourceUrl"], asset_id + ".sourceUrl"))
        except ValueError as error:
            if isinstance(error, InvalidBenchmark):
                raise
            raise InvalidBenchmark("INVALID_SOURCE", asset_id + " has an invalid source URL.") from error
        require(source.scheme in ("https", "http") and source.hostname and not source.username and not source.password,
                "INVALID_SOURCE", f"{asset_id} needs a source URL without credentials.")
        for key in ("individualId", "sequenceId"):
            if key in asset:
                identifier(asset[key], asset_id + "." + key)
        hashed_path(manifest_path.parent, asset["path"], asset["sha256"], "asset " + asset_id)
    gallery = unique_rows(manifest["gallery"], "mediaId", "gallery")
    gallery_assets = set()
    for row in gallery.values():
        fields(row, {"mediaId", "assetId"}, set(), "gallery row")
        identifier(row["assetId"], "gallery.assetId")
        require(row["assetId"] in assets, "UNKNOWN_ASSET", f"Gallery {row['mediaId']} references an unknown asset.")
        require(row["assetId"] not in gallery_assets, "DUPLICATE_GALLERY_ASSET", "Gallery asset IDs must not be duplicated.")
        gallery_assets.add(row["assetId"])
    queries = unique_rows(manifest["queries"], "queryId", "queries")
    for query in queries.values():
        fields(query, {"queryId", "split", "mode", "positiveIds"}, {"assetId", "description", "individualId"}, "query " + query["queryId"])
        require(query["mode"] in MODES and query["split"] in SPLITS, "INVALID_QUERY", "Query mode or split is invalid.")
        if "assetId" in query:
            identifier(query["assetId"], query["queryId"] + ".assetId")
            require(query["assetId"] in assets, "UNKNOWN_ASSET", f"{query['queryId']} references an unknown asset.")
            require(assets[query["assetId"]]["split"] == query["split"], "SPLIT_MISMATCH", f"{query['queryId']} disagrees with its source split.")
        if query["mode"] in ("photo", "combined"):
            require(query.get("assetId") in assets, "MISSING_PHOTO", f"{query['queryId']} requires a photo asset.")
        if query["mode"] in ("text", "combined"):
            text(query.get("description"), query["queryId"] + ".description")
            require(len(query["description"]) <= 1000, "INVALID_DESCRIPTION", "Descriptions must be at most 1000 characters, as in production.")
        else:
            require(not query.get("description"), "MODE_MISMATCH", "Photo mode cannot silently ignore a description.")
        if "individualId" in query:
            identifier(query["individualId"], query["queryId"] + ".individualId")
        positives = query["positiveIds"]
        require(isinstance(positives, list) and all(isinstance(x, str) for x in positives), "INVALID_POSITIVES", "positiveIds must be a list of media IDs, empty for no-match cases.")
        require(len(positives) == len(set(positives)) and set(positives) <= gallery.keys(), "INVALID_POSITIVES", "positiveIds must contain unique known gallery IDs.")
    identity_sources = validate_identity(manifest, assets, gallery, queries) if manifest["benchmarkKind"] == "pet-identity" else None
    gates = manifest.get("qualityGates", {})
    fields(gates, set(), {"recallAt1", "recallAt5", "maxNoMatchFalsePositiveRate"}, "qualityGates")
    for name, value in gates.items():
        require(finite_number(value) and 0 <= value <= 1, "INVALID_GATE", f"{name} must be a finite fraction between 0 and 1.")
    fields(manifest["featureBundle"], {"path", "sha256"}, set(), "featureBundle")
    bundle_path = hashed_path(manifest_path.parent, manifest["featureBundle"]["path"], manifest["featureBundle"]["sha256"], "featureBundle")
    bundle = load_json(bundle_path)
    fields(bundle, {"schemaVersion", "modelManifestVersion", "featureVersion", "provenance", "media", "queries"}, {"mediaErrors"}, "bundle")
    require(type(bundle["schemaVersion"]) is int and bundle["schemaVersion"] == 1, "SCHEMA_VERSION", "Feature bundle requires schemaVersion 1.")
    for key in ("modelManifestVersion", "featureVersion"):
        require(bundle[key] == manifest[key], "VERSION_MISMATCH", f"Bundle {key} does not match the manifest.")
    fields(bundle["provenance"], {"kind", "runId"}, set(), "provenance")
    require(bundle["provenance"]["kind"] in ("real-model", "fixture", "mock"), "INVALID_PROVENANCE", "Declare real-model, fixture or mock provenance.")
    text(bundle["provenance"]["runId"], "provenance.runId")
    for key, expected in (("media", gallery), ("queries", queries)):
        require(isinstance(bundle[key], dict) and set(bundle[key]) == set(expected), "FEATURE_IDS_MISMATCH", f"Bundle {key} must contain exactly the manifest IDs; no omitted or extra cases.")
    errors = bundle.get("mediaErrors", {})
    require(isinstance(errors, dict) and errors.keys() <= gallery.keys(), "FEATURE_IDS_MISMATCH", "mediaErrors may only reference known gallery IDs.")
    dimension = None

    def feature(value, context):
        nonlocal dimension
        fields(value, {"vector", "featureVersion"}, {"species", "frameId", "bbox"}, context)
        require(value["featureVersion"] == manifest["featureVersion"], "VERSION_MISMATCH", context + " uses a different feature version.")
        vector = value["vector"]
        require(isinstance(vector, list) and bool(vector) and all(finite_number(x) for x in vector),
                "INVALID_VECTOR", context + " requires a nonempty finite numeric vector.")
        # Production cosine squares components. Reject overflow/underflow inputs
        # rather than letting them silently turn all candidates into misses.
        squared_norm = sum(float(x) * float(x) for x in vector)
        require(math.isfinite(squared_norm) and 0 < squared_norm <= math.sqrt(sys.float_info.max),
                "INVALID_VECTOR", context + " has a zero or numerically unsafe norm.")
        if dimension is None:
            dimension = len(vector)
        require(len(vector) == dimension, "VECTOR_DIMENSION_MISMATCH", context + " has a different vector dimension.")
        if "species" in value:
            text(value["species"], context + ".species")

    def features(values, context, failed=False):
        require(isinstance(values, list) and (bool(values) or failed), "MISSING_FEATURES", context + " needs features or an explicit inference error.")
        for i, value in enumerate(values):
            feature(value, f"{context}[{i}]")

    for media_id, values in bundle["media"].items():
        if media_id in errors:
            validate_error(errors[media_id], "media error " + media_id)
        features(values, "media " + media_id, media_id in errors)
    for query_id, value in bundle["queries"].items():
        fields(value, {"features"}, {"descriptionFeature", "error"}, "query features " + query_id)
        failed = "error" in value
        if failed:
            validate_error(value["error"], "query error " + query_id)
        features(value["features"], "query " + query_id, failed)
        mode = queries[query_id]["mode"]
        if "descriptionFeature" in value:
            require(mode == "combined", "MODE_MISMATCH", "Only combined mode consumes descriptionFeature.")
            feature(value["descriptionFeature"], query_id + ".descriptionFeature")
        require(mode != "combined" or "descriptionFeature" in value or failed, "MISSING_FEATURES", query_id + " needs both photo and description features.")
        if mode == "text" and not failed:
            require(len(value["features"]) == 1 and "species" not in value["features"][0], "MODE_MISMATCH", "Text mode requires one species-neutral text embedding.")
    return manifest, bundle, assets, gallery, queries, identity_sources, dimension


def metrics(cases):
    positive = [case for case in cases if case["positiveIds"]]
    negative = [case for case in cases if not case["positiveIds"]]
    hits = {k: sum(case[f"hitAt{k}"] for case in positive) for k in (1, 5)}
    false_positives = sum(case["falsePositive"] for case in negative)
    return {"queries": len(cases), "failedQueries": sum(case["status"] == "error" for case in cases),
            "positiveQueries": len(positive), "hitAt1": hits[1], "hitAt5": hits[5],
            "recallAt1": hits[1] / len(positive) if positive else None,
            "recallAt5": hits[5] / len(positive) if positive else None,
            "noMatchQueries": len(negative), "falsePositiveQueries": false_positives,
            "noMatchFalsePositiveRate": false_positives / len(negative) if negative else None,
            "noMatchInferenceFailures": sum(case["status"] == "error" for case in negative)}


def evaluate(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    manifest, bundle, assets, gallery, queries, identity_sources, dimension = validate(manifest_path)
    config = Config(mode="local", pet_score_threshold=THRESHOLD, model_manifest_version=manifest["modelManifestVersion"])
    repo = MemoryRepository()
    storage = MemoryStorage(config)
    service = Service(config, repo, storage)
    worker = Workers(service)
    for media_id, row in gallery.items():
        key = f"evaluation-features/{media_id}.json"
        storage.put(key, json.dumps({"features": bundle["media"][media_id]}).encode())
        media = {"mediaId": media_id, "ownerSub": "offline-gallery", "visibility": "public", "status": "READY",
                 "observedAt": "2026-01-01T00:00:00Z", "fileType": "image", "tags": [], "counts": {}, "featureKey": key}
        repo.transact([Write("media", media), *service.index_writes({"status": "UPLOADING"}, media)])
    cases = []
    for query_id, query in queries.items():
        value = bundle["queries"][query_id]
        case = {"queryId": query_id, "split": query["split"], "mode": query["mode"], "positiveIds": query["positiveIds"],
                "status": "completed", "rankedIds": [], "scores": {}, "error": None}
        if value.get("error"):
            case.update(status="error", error=value["error"])
        elif bundle.get("mediaErrors"):
            case.update(status="error", error={"code": "GALLERY_INFERENCE_FAILED", "message": "Gallery features are incomplete; no query can be scored fairly.", "mediaIds": sorted(bundle["mediaErrors"])})
        else:
            operation = {"operationId": query_id, "queryId": query_id, "jobId": query_id,
                         "type": "QUERY", "queryType": "pet", "ownerSub": "offline-evaluator",
                         "status": "INFERRING", "leaseUntil": 0, "generation": 1, "attemptId": 1,
                         "description": query.get("description", ""), "from": None, "to": None}
            repo.transact([Write("operations", operation)])
            detection = {"features": value["features"]}
            if query["mode"] == "combined":
                detection["descriptionFeature"] = value["descriptionFeature"]
            try:
                # The production worker owns candidate eligibility, crop pairing,
                # species filtering, cosine, threshold, ordering and top-10 limit.
                worker._query_result(service.operation(query_id), detection)
                completed = service.operation(query_id)
                case.update(rankedIds=completed["resultIds"], scores=completed["scores"])
            except Exception as error:
                case.update(status="error", error={"code": getattr(error, "code", type(error).__name__),
                                                   "message": "Production retrieval failed for this case; it remains in the denominator."})
        expected = set(case["positiveIds"])
        case.update(hitAt1=bool(expected & set(case["rankedIds"][:1])),
                    hitAt5=bool(expected & set(case["rankedIds"][:5])),
                    falsePositive=not expected and bool(case["rankedIds"]))
        cases.append(case)
    by_split = {}
    for split in SPLITS:
        split_cases = [case for case in cases if case["split"] == split]
        by_split[split] = {"all": metrics(split_cases), "byMode": {
            mode: metrics([case for case in split_cases if case["mode"] == mode]) for mode in MODES}}
    gate_results = []
    gates = manifest.get("qualityGates", {})
    if gates:
        holdout = by_split["holdout"]["byMode"]
        present = [mode for mode in MODES if holdout[mode]["queries"]]
        if not present:
            gate_results.append({"passed": False, "reason": "No holdout queries; calibration cannot satisfy quality gates."})
        for mode in present:
            measured = holdout[mode]
            for name, threshold in gates.items():
                field = "noMatchFalsePositiveRate" if name == "maxNoMatchFalsePositiveRate" else name
                actual = measured[field]
                passed = actual is not None and (actual <= threshold if name.startswith("max") else actual >= threshold)
                gate_results.append({"mode": mode, "metric": field, "required": threshold, "actual": actual,
                                     "passed": passed, "reason": None if actual is not None else "No eligible denominator."})
    failed_queries = sum(case["status"] == "error" for case in cases)
    gates_passed = all(row["passed"] for row in gate_results) if gates else None
    report = {"schemaVersion": 1, "reportKind": "offline-pet-quality", "status": "feature_failures" if failed_queries else "quality_gate_failed" if gates_passed is False else "completed",
              "benchmarkKind": manifest["benchmarkKind"], "checkedAtUTC": datetime.now(timezone.utc).isoformat(),
              "modelManifestVersion": manifest["modelManifestVersion"], "featureVersion": manifest["featureVersion"],
              "manifestSha256": sha256(manifest_path), "featureBundleSha256": manifest["featureBundle"]["sha256"],
              "provenance": bundle["provenance"], "vectorDimension": dimension, "threshold": THRESHOLD,
              "scoringImplementation": "ecoguard_aws.workers.Workers._query_result",
              "metricDefinition": "Recall@K is the proportion of positive queries with at least one labeled positive among the first K returned IDs; failed queries remain in denominators.",
              "identityHoldoutUniqueSources": identity_sources, "claims": dict(CLAIMS),
              "evidenceScope": "fixture-only" if bundle["provenance"]["kind"] != "real-model" else manifest["benchmarkKind"],
              "bySplit": by_split, "qualityGatesPassed": gates_passed, "gateResults": gate_results,
              "failedQueries": failed_queries, "cases": cases,
              "limits": "Offline precomputed-feature retrieval only. Provenance and human labels are attestations, not independently certified. No model was invoked, no threshold was tuned, and no identity, cloud, deployment or assignment acceptance is inferred."}
    if manifest["modelManifestVersion"] == CALIBRATED_MANIFEST:
        policy = calibration()
        report["scoringPolicy"] = policy
        report["modalityThresholds"] = {mode: policy[mode]["minimumScore"] for mode in ("image", "text")}
        report["thresholdMeaning"] = "Outer result filter; each calibrated modality must also pass its frozen 0.5 gate. Scores are semantic candidate scores, not identity probabilities."
    else:
        report["scoringPolicy"] = "historical raw cosine; combined arithmetic mean"
    return report, 1 if failed_queries or gates_passed is False else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report, exit_code = evaluate(args.manifest)
    except InvalidBenchmark as error:
        report = {"schemaVersion": 1, "reportKind": "offline-pet-quality", "status": "invalid_manifest",
                  "error": {"code": error.code, "message": str(error)}, "claims": dict(CLAIMS)}
        exit_code = 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(args.output), "exitCode": exit_code}))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
