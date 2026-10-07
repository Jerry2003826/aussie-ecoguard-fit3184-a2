"""Compare official simple prompts with taxonomy prompts on a local smoke image."""
import hashlib
import json
import time
from pathlib import Path

from PIL import Image

from app.runtime import ModelRuntime
from app.settings import Settings


def main():
    root = Path(__file__).resolve().parents[3]
    path = root / "config/model.locked.json"
    runtime = ModelRuntime(Settings(manifest_path=str(path), manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                    taxonomy_path=str(root / "config/taxonomy.json"), model_cache_dir=str(root / "models")))
    processor, model = runtime.ensure_model("detector", time.monotonic() + 120)
    image = Image.open(root / "tmp/model-smoke/coco_sample.png").convert("RGB")
    records = []
    for prompt in ("a cat. a remote control.", "cat. dog. fox. pig. toad.", ". ".join(runtime.taxonomy.prompts) + ".", "cat."):
        inputs = processor(images=image, text=prompt, return_tensors="pt")
        with runtime.torch.inference_mode():
            output = model(**inputs)
        result = processor.post_process_grounded_object_detection(output, inputs.input_ids, threshold=0.25,
                                                                   text_threshold=0.25, target_sizes=[(image.height, image.width)])[0]
        record = {"prompt": prompt, "labels": result["text_labels"], "scores": result["scores"].tolist(), "boxes": result["boxes"].tolist()}
        records.append(record)
        print(json.dumps(record), flush=True)
    (root / "services/inference/evidence/detector-prompts-20261007.json").write_text(json.dumps(records, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
