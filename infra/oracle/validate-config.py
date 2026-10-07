#!/usr/bin/env python3
"""Offline deployment gate. Reads configuration and hashes; never prints tokens."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from urllib.parse import urlsplit


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def read_env(path):
    require(path.stat().st_size <= 16384, "Environment file is unexpectedly large")
    if os.name == "posix":
        require(not stat.S_IMODE(path.stat().st_mode) & 0o077,
                "Environment file must have no group/other permissions (use 0600)")
    values = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        require(separator and re.fullmatch(r"[A-Z][A-Z0-9_]*", key),
                f"Invalid literal environment entry on line {line_number}")
        require(key not in values, f"Duplicate environment key on line {line_number}")
        require(not any(char in value for char in "\"'`$\x00") and value == value.strip(),
                f"Shell expansion/quoting is not allowed on line {line_number}")
        require("REPLACE_WITH" not in value and "<" not in value and ">" not in value,
                f"Unfilled environment placeholder on line {line_number}")
        values[key] = value
    return values


def validate(env_path, manifest_path, taxonomy_path, cache_path):
    env = read_env(env_path)
    require(env.get("SERVICE_AUTH_MODE") == "oracle", "SERVICE_AUTH_MODE must be oracle")
    for key in ("LOCAL_SERVICE_TOKEN", "K_SERVICE", "GOOGLE_APPLICATION_CREDENTIALS",
                "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        require(not env.get(key), f"Do not configure {key} on this inference host")
    audience = urlsplit(env.get("SERVICE_TOKEN_AUDIENCE", ""))
    require(audience.scheme == "https" and audience.hostname and "." in audience.hostname
            and audience.hostname not in {"localhost", "example.com", "example.org"}
            and not audience.username and not audience.password and audience.port in {None, 443}
            and not audience.query and not audience.fragment
            and not audience.path.endswith("/") and "//" not in audience.path,
            "SERVICE_TOKEN_AUDIENCE must be the exact existing public HTTPS base URL, without trailing slash")
    require(bool(re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com",
                              env.get("SERVICE_TOKEN_EMAIL", ""))), "Configure the exact GCP WIF invoker service-account email")
    issuer = env.get("COGNITO_ISSUER", "")
    require(bool(re.fullmatch(r"https://cognito-idp\.([a-z]{2}-[a-z]+-\d)\.amazonaws\.com/\1_[A-Za-z0-9]+", issuer)),
            "COGNITO_ISSUER must be the deployed Cognito user-pool issuer")
    require(bool(re.fullmatch(r"[a-z0-9]{10,128}", env.get("COGNITO_CLIENT_ID", ""))), "Configure the Cognito app client ID")
    hosts = env.get("SOURCE_S3_HOSTS", "").split(",")
    require(all(re.fullmatch(r"[a-z0-9][a-z0-9.-]+\.s3\.[a-z]{2}-[a-z]+-\d\.amazonaws\.com", h) for h in hosts),
            "SOURCE_S3_HOSTS must contain exact regional S3 bucket hosts, without wildcards or URLs")
    require(set(env.get("SOURCE_KEY_PREFIXES", "").split(",")) == {"derived/", "queries/"},
            "SOURCE_KEY_PREFIXES must be derived/,queries/")
    for key, expected in (("MODEL_MANIFEST_PATH", "/config/model.locked.json"),
                          ("TAXONOMY_PATH", "/config/taxonomy.json"), ("MODEL_CACHE_DIR", "/models")):
        require(env.get(key) == expected, f"{key} does not match the Compose mount")
    require(env.get("INFERENCE_TIMEOUT_SECONDS", "").isdigit() and env.get("TORCH_THREADS", "").isdigit(),
            "Timeout and threads must be positive decimal integers")
    timeout, threads = int(env["INFERENCE_TIMEOUT_SECONDS"]), int(env["TORCH_THREADS"])
    require(1 <= timeout <= 330 and 1 <= threads <= 4, "Timeout/threads exceed this deployment template's limits")
    require(env.get("HF_HUB_OFFLINE") == "1" and env.get("TRANSFORMERS_OFFLINE") == "1",
            "Transformers and Hugging Face loading must use the preloaded cache")

    require(manifest_path.stat().st_size <= 1048576, "Model manifest is too large")
    digest = env.get("MODEL_MANIFEST_SHA256", "")
    require(bool(re.fullmatch(r"[a-f0-9]{64}", digest)) and sha256(manifest_path) == digest,
            "Model manifest SHA-256 mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("schemaVersion") == "1" and manifest.get("status") == "locked"
            and manifest.get("entryModel") == "aws-rekognition", "A locked version-1 model manifest is required")
    require(sha256(taxonomy_path) == manifest.get("taxonomySha256"), "Taxonomy SHA-256 mismatch")
    count, total = 0, 0
    cache_root = cache_path.resolve(strict=True)
    for role, adapter in (("detector", "grounding-dino"), ("embedding", "siglip2")):
        model = manifest[role]
        require(model["adapter"] == adapter and re.fullmatch(r"[a-f0-9]{40}", model["revision"]),
                f"Invalid {role} adapter or mutable revision")
        require(model["files"] and any(item["name"].endswith(".safetensors") for item in model["files"]),
                f"Missing {role} safetensors artifacts")
        seen = set()
        for item in model["files"]:
            name = item["name"]
            relative = PurePosixPath(name)
            require(not relative.is_absolute() and ".." not in relative.parts and "\\" not in name
                    and name not in seen and relative.suffix in {".json", ".txt", ".safetensors", ".model"},
                    "Unsafe or duplicate model artifact path")
            seen.add(name)
            destination = (cache_root / adapter / model["revision"] / name).resolve(strict=True)
            require(destination.is_relative_to(cache_root) and destination.is_file(), "Model artifact escapes cache or is not a file")
            require(isinstance(item["bytes"], int) and 0 < item["bytes"] <= 3_000_000_000
                    and destination.stat().st_size == item["bytes"], f"Artifact size mismatch: {adapter}/{name}")
            require(re.fullmatch(r"[a-f0-9]{64}", item["sha256"]) and sha256(destination) == item["sha256"],
                    f"Artifact SHA-256 mismatch: {adapter}/{name}")
            count += 1
            total += item["bytes"]
    return {"status": "configuration-and-cache-verified", "artifacts": count, "modelBytes": total,
            "manifestVersion": manifest.get("manifestVersion"), "cloudAcceptance": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--taxonomy", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(validate(args.env, args.manifest, args.taxonomy, args.cache)))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        # No environment values, credentials, request bodies, or signed URLs are printed.
        print(f"Deployment blocked: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
