"""A real-model local smoke, not a species-accuracy or cloud acceptance test."""
import argparse
import hashlib
import importlib.metadata
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil
from PIL import Image, ImageOps

from app.runtime import ModelRuntime
from app.schemas import EmbedRequest
from app.settings import Settings
from app.taxonomy import frame_counts, merge_detections


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--taxonomy", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings(manifest_path=str(args.manifest), manifest_sha256=hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
                        taxonomy_path=str(args.taxonomy), model_cache_dir=str(args.cache), torch_threads=4)
    runtime = ModelRuntime(settings)
    deadline = time.monotonic() + 1200
    record = {"kind": "local-real-model-smoke", "createdAt": datetime.now(timezone.utc).isoformat(),
              "manifestSha256": settings.manifest_sha256, "manifestVersion": runtime.manifest["manifestVersion"],
              "imageSha256": hashlib.sha256(args.image.read_bytes()).hexdigest(),
              "cloudValidated": False, "accuracyBenchmarkPassed": False, "stages": []}
    def stage(name, action):
        start = time.monotonic()
        print(f"Starting {name}", flush=True)
        result = action()
        memory = psutil.Process().memory_info()
        record["stages"].append({"name": name, "seconds": round(time.monotonic() - start, 3),
                                 "rssBytes": memory.rss, "peakRssBytes": getattr(memory, "peak_wset", memory.rss)})
        print(json.dumps(record["stages"][-1]), flush=True)
        return result
    stage("load-detector", lambda: runtime.ensure_model("detector", deadline))
    stage("load-embedding", lambda: runtime.ensure_model("embedding", deadline))
    with Image.open(args.image) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    try:
        record["rawDetections"] = stage("detect-image", lambda: runtime.detect(image, deadline))
        record["detections"] = merge_detections(record["rawDetections"], runtime.taxonomy, runtime.manifest["detector"]["nmsIou"])
        record["counts"] = frame_counts(record["detections"])
        image_vector = stage("embed-image", lambda: runtime.image_embedding(image, deadline))
        texts = ["a photo of two cats on a couch", "a photo of a red car on a road"]
        record["retrieval"] = []
        for text in texts:
            request = EmbedRequest(jobId="local-smoke", manifestVersion=runtime.manifest["manifestVersion"], text=text)
            vector = stage("embed-text", lambda: runtime.embed(request, deadline))["vector"]
            record["retrieval"].append({"text": text, "cosine": sum(a * b for a, b in zip(image_vector, vector, strict=True)),
                                        "dimensions": len(vector), "vectorNorm": math.sqrt(sum(x * x for x in vector))})
        record["imageVectorDimensions"] = len(image_vector)
        record["featureVersion"] = runtime.feature_version
        record["models"] = runtime.status()
        record["packages"] = {name: importlib.metadata.version(name) for name in ("torch", "transformers", "safetensors", "Pillow")}
        record["status"] = "smoke-completed"
    finally:
        image.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "status": record["status"], "accuracyBenchmarkPassed": False}), flush=True)


if __name__ == "__main__":
    main()
