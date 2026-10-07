from __future__ import annotations
from .config import Config
from .repository import DynamoRepository
from .storage import S3Storage
from .service import Service
from .cloud_clients import CloudRunClient, SnsNotifier, LexClient
from .media_processing import Preprocessor
from .workers import Workers, AwsQueue

_runtime = None


def runtime():
    global _runtime
    if _runtime is None:
        import boto3
        config = Config.from_env()
        if config.mode != "aws":
            raise RuntimeError("Lambda handlers cannot use local development adapters")
        session = boto3.Session(region_name=config.region)
        repository, storage = DynamoRepository(config, session), S3Storage(config, session)
        service = Service(config, repository, storage, inference=CloudRunClient(config), notifier=SnsNotifier(config, session), lex=LexClient(config, session))
        workers = Workers(service, Preprocessor(config, storage, session.client("rekognition")), AwsQueue(config, session))
        _runtime = (service, workers)
    return _runtime
