"""Explicitly development-only local HTTP adapter, with no fabricated AI or email success.

Run with ECO_MODE=local and PYTHONPATH=packages/domain;services/aws.
Only bind to loopback. This process keeps all records and uploaded bytes in memory.
"""
import os
import threading
from collections import deque

from ecoguard_domain import DomainError, Principal
from .api import dispatch
from .config import Config
from .repository import MemoryRepository, Conflict
from .storage import MemoryStorage
from .service import Service
from .cloud_clients import UnconfiguredInference
from .media_processing import Preprocessor
from .workers import Workers


class LocalQueue:
    def __init__(self):
        self.pending = deque()

    def send(self, kind, payload):
        self.pending.append((kind, payload))


def create_app(*, service=None):
    from fastapi import FastAPI, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse, Response
    config = Config.from_env()
    if config.mode != "local" or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        raise RuntimeError("Set ECO_MODE=local to explicitly enable development adapters")
    if service is None:
        service = Service(config, MemoryRepository(), MemoryStorage(config), inference=UnconfiguredInference())
    app = FastAPI(title="Aussie EcoGuard — DEVELOPMENT ONLY", docs_url="/_dev/docs")
    app.add_middleware(CORSMiddleware, allow_origins=[config.web_url], allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], allow_headers=["Content-Type", "Authorization", "Idempotency-Key", "X-Ecoguard-Dev-User", "X-Ecoguard-Dev-Email", "x-amz-checksum-sha256"])
    queue = LocalQueue()
    workers = Workers(service, Preprocessor(config, service.storage), queue)
    app.state.service, app.state.workers = service, workers

    def pump():
        try:
            workers.dispatch()
            count = 0
            while queue.pending and count < 50:
                kind, payload = queue.pending.popleft()
                count += 1
                if kind == "NOTIFY":
                    # Local mode does not claim that SNS sent an email.
                    continue
                try:
                    getattr(workers, {"INGEST": "ingest", "INFER": "infer", "DELETE": "delete"}[kind])(payload["entityId"])
                except Exception as exc:
                    print(f"Development worker stopped: {type(exc).__name__}; no cloud success was simulated")
                workers.dispatch()
        except Exception as exc:
            print(f"Development queue error: {type(exc).__name__}")

    @app.exception_handler(DomainError)
    async def domain_error(request, error):
        return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=error.status)

    @app.exception_handler(Conflict)
    async def conflict_error(request, error):
        return JSONResponse({"error": {"code": "CONCURRENT_UPDATE", "message": "Reload and retry this operation."}}, status_code=409)

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "mode": "development-only", "persistentStorage": False, "realCloudConnected": False}

    @app.put("/_dev/uploads/{token}")
    async def put_upload(token: str, request: Request):
        data = await request.body()
        if len(data) > 100*1024**2:
            raise DomainError("FILE_TOO_LARGE", "File exceeds the local upload limit.", 413)
        key = service.storage.accept_upload(token, data)
        queue.send("INGEST", {"entityId": key.rsplit("/", 1)[1]})
        threading.Thread(target=pump, daemon=True).start()
        return Response(status_code=200)

    @app.get("/_dev/objects/{token}")
    async def get_object(token: str):
        data, content_type = service.storage.resolve_read(token)
        return Response(data, media_type=content_type, headers={"Cache-Control": "no-store"})

    @app.api_route("/api/v1/{path:path}", methods=["GET", "POST", "PATCH", "DELETE"])
    async def api(path: str, request: Request):
        user = request.headers.get("x-ecoguard-dev-user", "")
        if not user or len(user) > 100:
            raise DomainError("UNAUTHENTICATED", "Choose an explicit local development identity.", 401)
        principal = Principal(user, request.headers.get("x-ecoguard-dev-email", f"{user}@example.invalid"), True)
        body = await request.json() if request.method in ("POST", "PATCH") and await request.body() else {}
        status, data = dispatch(service, principal, request.method, "/api/v1/"+path, body, dict(request.query_params))
        threading.Thread(target=pump, daemon=True).start()
        return JSONResponse({"data": data, "mode": "development-only"}, status_code=status, headers={"Cache-Control": "no-store"})

    return app


def main():
    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
