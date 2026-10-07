from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    mode: str = "aws"
    region: str = "us-east-1"
    media_table: str = "ecoguard-media"
    operations_table: str = "ecoguard-operations"
    index_table: str = "ecoguard-search-index"
    subscriptions_table: str = "ecoguard-subscriptions"
    outbox_table: str = "ecoguard-outbox"
    media_bucket: str = ""
    ingest_queue_url: str = ""
    inference_queue_url: str = ""
    delete_queue_url: str = ""
    notification_queue_url: str = ""
    inference_url: str = ""
    google_credentials_file: str = ""
    google_invoker_email: str = ""
    cognito_client_id: str = ""
    cognito_user_pool_id: str = ""
    web_url: str = "http://127.0.0.1:5173"
    api_url: str = "http://127.0.0.1:8000"
    model_manifest_version: str = "unconfigured"
    lex_bot_id: str = ""
    lex_alias_id: str = ""
    lex_locale: str = "en_US"
    resource_prefix: str = "ecoguard"
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    local_inference_url: str = ""
    pet_score_threshold: float = 0.2
    upload_url_seconds: int = 600
    query_lifetime_seconds: int = 900
    inference_request_seconds: int = 330
    frame_url_seconds: int = 900
    query_receipt_seconds: int = 86400

    def __post_init__(self):
        for name in ("upload_url_seconds", "query_lifetime_seconds", "inference_request_seconds", "frame_url_seconds", "query_receipt_seconds"):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= 86400:
                raise ValueError(f"{name} must be an integer between 1 and 86400 seconds")
        if self.inference_request_seconds > 330:
            raise ValueError("inference_request_seconds must fit the deployed 330-second model budget")
        if self.query_receipt_seconds < self.query_lifetime_seconds:
            raise ValueError("query_receipt_seconds must cover the query lifetime")

    @classmethod
    def from_env(cls):
        values = {name: os.environ["ECO_" + name.upper()] for name in cls.__dataclass_fields__ if "ECO_" + name.upper() in os.environ}
        values["region"] = values.get("region", os.environ.get("AWS_REGION", "us-east-1"))
        if "pet_score_threshold" in values:
            values["pet_score_threshold"] = float(values["pet_score_threshold"])
        for name in ("upload_url_seconds", "query_lifetime_seconds", "inference_request_seconds", "frame_url_seconds", "query_receipt_seconds"):
            if name in values:
                values[name] = int(values[name])
        cfg = cls(**values)
        if cfg.mode not in ("aws", "local"):
            raise ValueError("ECO_MODE must be aws or local")
        if cfg.mode == "local" and os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
            raise ValueError("Development mode must never run inside Lambda")
        return cfg

    def tables(self):
        return {"media": self.media_table, "operations": self.operations_table, "index": self.index_table, "subscriptions": self.subscriptions_table, "outbox": self.outbox_table}
