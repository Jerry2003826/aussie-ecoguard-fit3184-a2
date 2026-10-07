from datetime import datetime
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:@-]+$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Box(StrictModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def in_bounds(self):
        if self.x + self.width > 1.00001 or self.y + self.height > 1.00001:
            raise ValueError("Bounding box extends outside image")
        return self


class EntryBox(StrictModel):
    Width: float = Field(ge=0, le=1)
    Height: float = Field(ge=0, le=1)
    Left: float = Field(ge=0, le=1)
    Top: float = Field(ge=0, le=1)


class EntryInstance(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)
    BoundingBox: EntryBox
    Confidence: float = Field(ge=0, le=100)


class EntryLabel(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)
    Name: str = Field(min_length=1, max_length=100)
    Confidence: float = Field(ge=0, le=100)
    Instances: list[EntryInstance] = Field(default_factory=list, max_length=128)


class Frame(StrictModel):
    frameId: Identifier
    timestampSeconds: float = Field(ge=0, lt=120)
    url: str = Field(min_length=12, max_length=8192)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    entryLabels: list[EntryLabel] = Field(max_length=100)
    entryModelVersion: str | None = Field(default=None, max_length=100)


class InferRequest(StrictModel):
    schemaVersion: Literal["1"] = "1"
    jobId: Identifier
    ownerSub: Identifier
    generation: int = Field(ge=1)
    manifestVersion: Identifier
    mediaType: Literal["image", "video"]
    frames: list[Frame] = Field(min_length=1, max_length=12)
    deadlineAt: datetime | None = None

    @model_validator(mode="after")
    def frame_order(self):
        times = [frame.timestampSeconds for frame in self.frames]
        if len({frame.frameId for frame in self.frames}) != len(self.frames):
            raise ValueError("Frame IDs must be unique")
        if self.mediaType == "image" and (len(times) != 1 or times != [0]):
            raise ValueError("Images require one frame at time zero")
        if self.mediaType == "video" and times != [i * 10 for i in range(len(times))]:
            raise ValueError("Video frames must be ordered at 0, 10, 20 seconds")
        if self.deadlineAt is not None and self.deadlineAt.tzinfo is None:
            raise ValueError("deadlineAt must include a timezone")
        return self


class EmbedRequest(StrictModel):
    schemaVersion: Literal["1"] = "1"
    jobId: Identifier
    manifestVersion: Identifier
    text: str = Field(min_length=1, max_length=1000)
    deadlineAt: datetime | None = None

    @model_validator(mode="after")
    def valid_text(self):
        self.text = self.text.strip()
        if not self.text:
            raise ValueError("Text must not be blank")
        if self.deadlineAt is not None and self.deadlineAt.tzinfo is None:
            raise ValueError("deadlineAt must include a timezone")
        return self


class QueryAuthorization(StrictModel):
    requestId: Identifier
    purpose: Literal["file", "pet"]


class Detection(StrictModel):
    species: str
    confidence: float = Field(ge=0, le=1)
    bbox: Box
    source: Literal["aws-rekognition", "grounding-dino", "grounding-dino+siglip2"]


class Feature(StrictModel):
    species: Literal["cat", "dog"]
    vector: list[float] = Field(min_length=1, max_length=4096)
    featureVersion: str
    frameId: str
    bbox: Box


class FrameResult(StrictModel):
    frameId: str
    timestampSeconds: float
    counts: dict[str, int]
    detections: list[Detection]
    entryModelVersion: str | None = None


class InferResponse(StrictModel):
    schemaVersion: Literal["1"] = "1"
    jobId: str
    manifestVersion: str
    counts: dict[str, int]
    tags: list[str]
    frames: list[FrameResult]
    features: list[Feature]
    classification: Literal["pet", "invasive", "mixed", "uncertain"]
    featureVersion: str
    truncatedFeatures: bool = False


class EmbedResponse(StrictModel):
    schemaVersion: Literal["1"] = "1"
    vector: list[float] = Field(min_length=1, max_length=4096)
    featureVersion: str
    manifestVersion: str
