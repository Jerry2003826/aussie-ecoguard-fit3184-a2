import asyncio
import json
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .auth import CognitoVerifier, GoogleServiceVerifier, require_service
from .errors import ServiceError
from .schemas import EmbedRequest, EmbedResponse, InferRequest, InferResponse, QueryAuthorization
from .settings import Settings
from .worker import InferenceWorker

logger = logging.getLogger("ecoguard.inference")


class BoundedBody:
    def __init__(self, app, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in {"GET", "HEAD"}:
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > self.max_bytes:
                response = JSONResponse({"error": {"code": "REQUEST_TOO_LARGE", "message": "The request body exceeds the limit.", "retryable": False}}, 413)
                return await response(scope, receive, send)
            chunks.append(body)
            if not message.get("more_body", False):
                break
        consumed = False

        async def bounded_receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)


def create_app(settings: Settings | None = None, worker=None, verifier=None, service_verifier=None) -> FastAPI:
    settings = settings or Settings.from_env()
    worker = worker or InferenceWorker(settings)
    verifier = verifier or CognitoVerifier(settings)
    service_verifier = service_verifier or GoogleServiceVerifier(settings)

    @asynccontextmanager
    async def lifespan(application):
        yield
        await asyncio.to_thread(worker.close)

    app = FastAPI(title="Aussie EcoGuard private inference", version="1.0.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(BoundedBody, max_bytes=settings.max_request_bytes)
    app.state.worker, app.state.verifier = worker, verifier
    app.state.service_verifier = service_verifier

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, error: ServiceError):
        return JSONResponse({"error": {"code": error.code, "message": error.message, "retryable": error.retryable}},
                            status_code=error.status, headers={"Cache-Control": "no-store"})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        # FastAPI's default includes input values, which can be signed URLs.
        return JSONResponse({"error": {"code": "INVALID_REQUEST", "message": "The request does not satisfy the inference contract.",
                                       "retryable": False,
                                       "fields": [".".join(map(str, item["loc"])) for item in error.errors()]}}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, error: Exception):
        logger.error("inference_request_failed", extra={"exception_type": type(error).__name__})
        return JSONResponse({"error": {"code": "INTERNAL_ERROR", "message": "The service could not complete this operation.",
                                       "retryable": True}}, status_code=500)

    @app.get("/healthz")
    async def health():
        return {"status": "alive", "schemaVersion": "1", "models": worker.status()}

    @app.get("/readyz")
    async def ready():
        status = worker.status()
        return JSONResponse({"schemaVersion": "1", **status}, status_code=200 if status.get("ready") else 503)

    @app.post("/v1/query-authorizations")
    async def query_authorizations(body: QueryAuthorization, request: Request):
        try:
            async with asyncio.timeout(settings.authorization_timeout):
                await require_service(request, settings, service_verifier)
                subject = await verifier.verify(request.headers.get("authorization"))
        except TimeoutError:
            raise ServiceError("AUTHORIZATION_TIMEOUT", "Cross-cloud authorization timed out.", 503, True) from None
        return JSONResponse({"sub": subject, "requestId": body.requestId}, headers={"Cache-Control": "no-store"})

    async def execute(operation: str, body: dict, request: Request):
        try:
            async with asyncio.timeout(settings.authorization_timeout):
                await require_service(request, settings, service_verifier)
        except TimeoutError:
            raise ServiceError("SERVICE_AUTHORIZATION_TIMEOUT", "Service identity verification timed out.", 503, True) from None
        cancelled = threading.Event()
        task = asyncio.create_task(asyncio.to_thread(worker.run, operation, body, cancelled))
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=0.2)
                if not task.done() and await request.is_disconnected():
                    cancelled.set()
            result = await task
        except asyncio.CancelledError:
            cancelled.set()
            try:
                await asyncio.shield(task)
            except ServiceError:
                pass
            raise
        encoded = json.dumps(result, separators=(",", ":"), allow_nan=False).encode()
        if len(encoded) > settings.max_response_bytes:
            raise ServiceError("RESPONSE_TOO_LARGE", "The inference result exceeds the response limit.", 502)
        return result

    @app.post("/v1/infer", response_model=InferResponse)
    async def infer(body: InferRequest, request: Request):
        result = await execute("infer", body.model_dump(mode="json"), request)
        return InferResponse.model_validate(result)

    @app.post("/v1/embed", response_model=EmbedResponse)
    async def embed(body: EmbedRequest, request: Request):
        result = await execute("embed", body.model_dump(mode="json"), request)
        return EmbedResponse.model_validate(result)

    @app.post("/v1/warmup")
    async def warmup(request: Request):
        return await execute("warmup", {}, request)

    return app


app = create_app()
