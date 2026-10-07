"""Explicitly development-only local HTTP adapter, with no fabricated AI or email success.

Run with ECO_MODE=local and PYTHONPATH=packages/domain;services/aws.
Only bind to loopback. This process keeps all records and uploaded bytes in memory.
"""
import os
import threading
import time
from collections import deque
from contextlib import asynccontextmanager

from ecoguard_domain import DomainError, Principal
from .api import dispatch
from .config import Config
from .repository import MemoryRepository, Conflict
from .storage import MemoryStorage
from .service import Service
from .cloud_clients import UnconfiguredInference
from .media_processing import Preprocessor
from .workers import Workers, WorkerBusy


class LocalQueue:
    def __init__(self):
        self.pending = deque()
        self.deferred = deque()

    def send(self, kind, payload):
        self.pending.append((kind, payload))


class LocalTaskPump:
    """A single local consumer with durable, visible failure states in the repository."""
    def __init__(self, workers, queue):
        self.workers, self.queue = workers, queue
        self.lock = threading.Lock()

    def run(self):
        if not self.lock.acquire(blocking=False):
            return  # The active consumer or periodic scheduler will pick up new work.
        try:
            for _ in range(len(self.queue.deferred)):
                ready_at, kind, payload = self.queue.deferred.popleft()
                if ready_at <= time.time():
                    self.queue.send(kind, payload)
                else:
                    self.queue.deferred.append((ready_at, kind, payload))
            self.workers.dispatch()
            self.workers.cleanup()
            for _ in range(50):
                if not self.queue.pending:
                    break
                kind, payload = self.queue.pending.popleft()
                if kind == "NOTIFY":
                    continue  # Never claim that the local adapter sent SNS email.
                try:
                    getattr(self.workers, {"INGEST": "ingest", "INFER": "infer", "DELETE": "delete"}[kind])(payload["entityId"])
                except WorkerBusy:
                    self.queue.deferred.append((time.time() + 1, kind, payload))
                except Exception as error:
                    try:
                        operation = self.workers.s.operation(payload["entityId"])
                        failure = error if isinstance(error, DomainError) else DomainError("LOCAL_DEPENDENCY_FAILED", "A development dependency failed. Retry this task.", 503, retryable=True)
                        self.workers._failure(payload["entityId"], failure, expected=operation)
                    except Exception:
                        # If recording the failure also fails, retain this delivery.
                        self.queue.deferred.append((time.time() + 1, kind, payload))
                    print(f"Development task failed: {type(error).__name__}; no cloud success was simulated")
                self.workers.dispatch()
        except Exception as error:
            print(f"Development queue error: {type(error).__name__}")
        finally:
            self.lock.release()


def create_app(*, service=None):
    from fastapi import FastAPI, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse, Response
    config = Config.from_env()
    if config.mode != "local" or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        raise RuntimeError("Set ECO_MODE=local to explicitly enable development adapters")
    if service is None:
        service = Service(config, MemoryRepository(), MemoryStorage(config), inference=UnconfiguredInference())
    stopped = threading.Event()
    @asynccontextmanager
    async def lifespan(app):
        def scheduler():
            while not stopped.wait(1):
                pump.run()
        scheduler_thread = threading.Thread(target=scheduler, daemon=True)
        scheduler_thread.start()
        try:
            yield
        finally:
            stopped.set()
            # The daemon cannot hold process shutdown hostage to a failed dependency.
            scheduler_thread.join(timeout=1)
    app = FastAPI(title="Aussie EcoGuard — DEVELOPMENT ONLY", docs_url="/_dev/docs", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=[config.web_url], allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], allow_headers=["Content-Type", "Authorization", "Idempotency-Key", "X-Ecoguard-Dev-User", "X-Ecoguard-Dev-Email", "x-amz-checksum-sha256"])
    queue = LocalQueue()
    workers = Workers(service, Preprocessor(config, service.storage), queue)
    app.state.service, app.state.workers = service, workers
    pump = LocalTaskPump(workers, queue)
    app.state.pump = pump

    @app.exception_handler(DomainError)
    async def domain_error(request, error):
        return JSONResponse({"error": {"code": error.code, "message": error.message, "retryable": error.retryable}}, status_code=error.status)

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
        threading.Thread(target=pump.run, daemon=True).start()
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
        threading.Thread(target=pump.run, daemon=True).start()
        return JSONResponse({"data": data, "mode": "development-only"}, status_code=status, headers={"Cache-Control": "no-store"})

    return app


def main():
    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
