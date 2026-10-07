"""Real model adapters. No production mock or synthetic output path exists."""
import math
import time
from pathlib import Path

from .errors import ServiceError
from .manifest import load_manifest, materialize
from .media import fetch_image, remaining
from .schemas import EmbedRequest, InferRequest
from .settings import Settings
from .taxonomy import Taxonomy, aggregate_counts, frame_counts, merge_detections


def normalized_vector(values: list[float]) -> list[float]:
    if not values or len(values) > 4096 or not all(math.isfinite(value) for value in values):
        raise ServiceError("INVALID_MODEL_OUTPUT", "The embedding model returned invalid values.", 502)
    norm = math.sqrt(sum(value * value for value in values))
    if norm <= 1e-12:
        raise ServiceError("INVALID_MODEL_OUTPUT", "The embedding model returned a zero vector.", 502)
    return [float(value / norm) for value in values]


class ModelRuntime:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.manifest = load_manifest(settings)
        self.taxonomy = Taxonomy(settings.taxonomy_path, self.manifest["taxonomyVersion"], self.manifest["taxonomySha256"])
        self.models: dict[str, tuple] = {}
        self.torch = None

    def status(self) -> dict:
        return {"manifestVersion": self.manifest["manifestVersion"], "loadedModels": sorted(self.models),
                "featureVersion": self.feature_version, "ready": {"detector", "embedding"} <= self.models.keys()}

    @property
    def feature_version(self) -> str:
        model = self.manifest["embedding"]
        # Exact model revision plus preprocessing are part of feature identity.
        return f"{model['featureVersion']}:{model['revision']}:{self.manifest['preprocessingVersion']}"

    def ensure_model(self, name: str, deadline: float):
        if name in self.models:
            return self.models[name]
        if self.torch is None:
            try:
                import torch
                torch.set_num_threads(max(1, min(self.settings.torch_threads, 8)))
                self.torch = torch
            except ImportError:
                raise ServiceError("MODEL_RUNTIME_MISSING", "Install the locked CPU model dependencies before inference.", 503) from None
        directory = materialize(self.manifest[name], self.settings, deadline)
        remaining(deadline)
        try:
            from transformers import AutoModel, AutoModelForZeroShotObjectDetection, AutoProcessor
            processor = AutoProcessor.from_pretrained(directory, local_files_only=True, trust_remote_code=False, use_fast=False)
            factory = AutoModelForZeroShotObjectDetection if name == "detector" else AutoModel
            model = factory.from_pretrained(directory, local_files_only=True, trust_remote_code=False,
                                            use_safetensors=True).to("cpu").eval()
            self.models[name] = (processor, model)
        except (ImportError, OSError, ValueError, RuntimeError):
            raise ServiceError("MODEL_LOAD_FAILED", "The verified model is incompatible with this runtime.", 503) from None
        remaining(deadline)
        return self.models[name]

    def detect(self, image, deadline: float) -> list[dict]:
        processor, model = self.ensure_model("detector", deadline)
        config = self.manifest["detector"]
        detections = []
        # Independent canonical prompts avoid competing aliases being concatenated
        # into false labels. Sequential passes bound memory; NMS resolves overlaps.
        # Cross-prompt confidence still requires the annotated species benchmark.
        for canonical, definition in self.taxonomy.species.items():
            remaining(deadline)
            prompt = definition["prompts"][0] + "."
            inputs = processor(images=image, text=prompt, return_tensors="pt",
                               size=config.get("imageSize", {"shortest_edge": 640, "longest_edge": 1024}))
            with self.torch.inference_mode():
                outputs = model(**inputs)
            remaining(deadline)
            result = processor.post_process_grounded_object_detection(
                outputs, inputs.input_ids, threshold=config["boxThreshold"],
                text_threshold=config["textThreshold"], target_sizes=[(image.height, image.width)])[0]
            labels = result["text_labels"] if "text_labels" in result else result["labels"]
            for label, score, box in zip(labels, result["scores"], result["boxes"], strict=True):
                species = self.taxonomy.canonical(str(label))
                if species != canonical:
                    continue
                x1, y1, x2, y2 = box.tolist()
                x1, x2 = max(0, x1 / image.width), min(1, x2 / image.width)
                y1, y2 = max(0, y1 / image.height), min(1, y2 / image.height)
                if x2 <= x1 or y2 <= y1:
                    continue
                detections.append({"species": species, "confidence": float(score), "source": "grounding-dino",
                                   "bbox": {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1}})
        return detections

    def entry_detections(self, labels: list) -> list[dict]:
        detections = []
        threshold = self.manifest.get("entryConfidenceThreshold", 0.7)
        for label in labels:
            species = self.taxonomy.canonical(label.Name)
            if species is None or label.Confidence / 100 < threshold:
                continue
            for instance in label.Instances:
                box = instance.BoundingBox
                score = min(label.Confidence, instance.Confidence) / 100
                width, height = min(box.Width, 1 - box.Left), min(box.Height, 1 - box.Top)
                if score >= threshold and width > 0 and height > 0:
                    detections.append({"species": species, "confidence": score, "source": "aws-rekognition",
                                       "bbox": {"x": box.Left, "y": box.Top, "width": width, "height": height}})
        return detections

    def image_embedding(self, image, deadline: float) -> list[float]:
        processor, model = self.ensure_model("embedding", deadline)
        inputs = processor(images=image, return_tensors="pt")
        with self.torch.inference_mode():
            output = model.get_image_features(**inputs)
        remaining(deadline)
        return normalized_vector(output[0].float().tolist())

    def embed(self, request: EmbedRequest, deadline: float) -> dict:
        self.check_version(request.manifestVersion)
        processor, model = self.ensure_model("embedding", deadline)
        max_length = model.config.text_config.max_position_embeddings
        inputs = processor(text=[request.text], padding="max_length", truncation=True,
                           max_length=max_length, return_tensors="pt")
        with self.torch.inference_mode():
            output = model.get_text_features(**inputs)
        remaining(deadline)
        return {"schemaVersion": "1", "vector": normalized_vector(output[0].float().tolist()),
                "featureVersion": self.feature_version, "manifestVersion": request.manifestVersion}

    def check_version(self, version: str) -> None:
        if version != self.manifest["manifestVersion"]:
            raise ServiceError("MANIFEST_VERSION_MISMATCH", "The requested immutable model version is not served by this revision.", 409)

    def infer(self, request: InferRequest, directory: Path, deadline: float) -> dict:
        self.check_version(request.manifestVersion)
        frames, features = [], []
        truncated = False
        for frame in request.frames:
            remaining(deadline)
            image = fetch_image(frame.url, frame.sha256, directory, self.settings, deadline)
            try:
                detections = merge_detections(self.entry_detections(frame.entryLabels) + self.detect(image, deadline),
                                              self.taxonomy, self.manifest["detector"]["nmsIou"])
                if len(detections) > self.settings.max_detections_per_frame:
                    raise ServiceError("TOO_MANY_DETECTIONS", "The frame exceeds the supported number of animal detections.", 422)
                frames.append({"frameId": frame.frameId, "timestampSeconds": frame.timestampSeconds,
                               "counts": frame_counts(detections), "detections": detections,
                               "entryModelVersion": frame.entryModelVersion})
                for detection in detections:
                    if self.taxonomy.category(detection["species"]) != "pet":
                        continue
                    if len(features) >= self.settings.max_pet_features:
                        truncated = True
                        continue
                    box = detection["bbox"]
                    bounds = (max(0, int(box["x"] * image.width)), max(0, int(box["y"] * image.height)),
                              min(image.width, math.ceil((box["x"] + box["width"]) * image.width)),
                              min(image.height, math.ceil((box["y"] + box["height"]) * image.height)))
                    with image.crop(bounds) as crop:
                        vector = self.image_embedding(crop, deadline)
                    features.append({"species": detection["species"], "vector": vector,
                                     "featureVersion": self.feature_version, "frameId": frame.frameId, "bbox": box})
            finally:
                image.close()
        counts = aggregate_counts(frames)
        return {"schemaVersion": "1", "jobId": request.jobId, "manifestVersion": request.manifestVersion,
                "counts": counts, "tags": sorted(counts), "frames": frames, "features": features,
                "classification": self.taxonomy.classification(counts), "featureVersion": self.feature_version,
                "truncatedFeatures": truncated}


def child_main(connection, settings: Settings):
    """One killable worker owns models. The parent owns all media temp directories."""
    runtime = None
    while True:
        try:
            message = connection.recv()
        except EOFError:
            break
        if message is None:
            break
        try:
            if runtime is None:
                runtime = ModelRuntime(settings)
            operation, payload, directory, deadline = message
            if operation == "infer":
                result = runtime.infer(InferRequest.model_validate(payload), Path(directory), deadline)
            elif operation == "embed":
                result = runtime.embed(EmbedRequest.model_validate(payload), deadline)
            elif operation == "warmup":
                runtime.ensure_model("detector", deadline)
                runtime.ensure_model("embedding", deadline)
                result = runtime.status()
            else:
                raise ServiceError("UNSUPPORTED_OPERATION", "Unsupported inference operation.", 400)
            connection.send({"result": result, "status": runtime.status()})
        except ServiceError as error:
            connection.send({"error": {"code": error.code, "message": error.message,
                                       "status": error.status, "retryable": error.retryable},
                             "status": runtime.status() if runtime else {"ready": False, "loadedModels": []}})
        except Exception:
            # Model/library exceptions may include signed URLs or image data.
            connection.send({"error": {"code": "INFERENCE_FAILED", "message": "The model operation failed.",
                                       "status": 502, "retryable": True}, "status": {"ready": False, "loadedModels": []}})
    connection.close()
