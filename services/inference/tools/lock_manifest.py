"""Pin official metadata without downloading large model weights.

Run with the service's core dependencies installed. This command only writes the
requested local manifest; it creates no cloud resources and downloads no weights.
"""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

import httpx


def lock_model(client: httpx.Client, model: dict) -> dict:
    model_id = model["modelId"]
    response = client.get(f"https://huggingface.co/api/models/{model_id}", params={"blobs": "true"})
    response.raise_for_status()
    info = response.json()
    model["revision"] = info["sha"]
    files = []
    for item in info["siblings"]:
        name = item["rfilename"]
        if "/" in name or Path(name).suffix not in {".json", ".txt", ".safetensors", ".model"}:
            continue
        uri = f"https://huggingface.co/{model_id}/resolve/{info['sha']}/{quote(name)}"
        lfs = item.get("lfs")
        if lfs:
            digest = lfs.get("sha256") or lfs.get("oid")
            size = lfs["size"]
        else:
            size = item.get("size", 0)
            if size > 16 * 1024 * 1024 or name.endswith(".safetensors"):
                raise ValueError(f"Missing LFS metadata for large artifact: {name}")
            small = client.get(uri)
            small.raise_for_status()
            if len(small.content) > 16 * 1024 * 1024:
                raise ValueError(f"Configuration artifact is too large: {name}")
            size, digest = len(small.content), hashlib.sha256(small.content).hexdigest()
        files.append({"name": name, "uri": uri, "sha256": digest, "bytes": size})
    if not any(item["name"].endswith(".safetensors") for item in files):
        raise ValueError(f"No safetensors artifacts for {model_id}")
    model["files"] = sorted(files, key=lambda item: item["name"])
    return model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists. Use a new immutable filename.")
    manifest = json.loads(args.template.read_text(encoding="utf-8"))
    manifest["taxonomySha256"] = hashlib.sha256((args.template.parent / "taxonomy.json").read_bytes()).hexdigest()
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        for name in ("detector", "embedding"):
            manifest[name] = lock_model(client, manifest[name])
    manifest.update(status="locked", manifestVersion=args.version)
    manifest["notes"] = "Official artifact metadata pinned; weights not downloaded; model accuracy and capacity remain unverified."
    data = (json.dumps(manifest, indent=2) + "\n").encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    print(json.dumps({"manifest": str(args.output), "sha256": hashlib.sha256(data).hexdigest(),
                      "artifactBytes": sum(item["bytes"] for name in ("detector", "embedding") for item in manifest[name]["files"]),
                      "weightsDownloaded": False}, indent=2))


if __name__ == "__main__":
    main()
