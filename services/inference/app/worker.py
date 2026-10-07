import multiprocessing
import tempfile
import threading
import time
from datetime import datetime, timezone

from .errors import DeadlineExceeded, ServiceError
from .manifest import load_manifest
from .runtime import child_main
from .settings import Settings


class InferenceWorker:
    """A bounded queue of one. Timeout kills the model process before media cleanup.

    A thread timeout alone would return early while a model continued running;
    isolating the models makes deadline cleanup and cancellation enforceable.
    """
    def __init__(self, settings: Settings, target=child_main):
        self.settings, self.target = settings, target
        self.context = multiprocessing.get_context("spawn")
        self.process = None
        self.connection = None
        self.lock = threading.Lock()
        self.model_status = {"ready": False, "loadedModels": []}

    def status(self) -> dict:
        status = dict(self.model_status)
        if self.process is None or not self.process.is_alive():
            status.update(ready=False, loadedModels=[])
        try:
            manifest = load_manifest(self.settings)
            status.update(configured=True, manifestVersion=manifest["manifestVersion"])
        except ServiceError:
            status.update(configured=False, ready=False)
        return status

    def _stop(self):
        if self.process:
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(timeout=3)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=3)
            self.process.close()
            self.process = None
        if self.connection:
            self.connection.close()
            self.connection = None
        self.model_status = {"ready": False, "loadedModels": []}

    def close(self):
        with self.lock:
            self._stop()

    def run(self, operation: str, payload: dict, cancelled: threading.Event | None = None) -> dict:
        seconds = self.settings.inference_timeout
        if payload.get("deadlineAt"):
            deadline_at = datetime.fromisoformat(str(payload["deadlineAt"]).replace("Z", "+00:00"))
            seconds = min(seconds, (deadline_at - datetime.now(timezone.utc)).total_seconds())
        if seconds <= 0:
            raise DeadlineExceeded()
        deadline = time.monotonic() + seconds
        if not self.lock.acquire(blocking=False):
            raise ServiceError("MODEL_BUSY", "The model worker is busy; retry this job later.", 429, True)
        try:
            manifest = load_manifest(self.settings)
            if operation != "warmup" and payload["manifestVersion"] != manifest["manifestVersion"]:
                raise ServiceError("MANIFEST_VERSION_MISMATCH", "The requested model version is not served by this revision.", 409)
            if self.process is None or not self.process.is_alive():
                self._stop()
                parent, child = self.context.Pipe()
                self.process = self.context.Process(target=self.target, args=(child, self.settings), daemon=True)
                self.connection = parent
                self.process.start()
                child.close()
            with tempfile.TemporaryDirectory(prefix="ecoguard-request-") as directory:
                self.connection.send((operation, payload, directory, deadline))
                while True:
                    if cancelled is not None and cancelled.is_set():
                        self._stop()
                        raise ServiceError("REQUEST_CANCELLED", "The request was cancelled.", 499)
                    if time.monotonic() >= deadline:
                        self._stop()
                        raise DeadlineExceeded()
                    if self.connection.poll(min(0.1, max(0, deadline - time.monotonic()))):
                        try:
                            response = self.connection.recv()
                        except (EOFError, OSError):
                            self._stop()
                            raise ServiceError("MODEL_WORKER_EXITED", "The model worker exited unexpectedly.", 503, True) from None
                        self.model_status = response.get("status", {"ready": False, "loadedModels": []})
                        if "error" in response:
                            error = response["error"]
                            raise ServiceError(error["code"], error["message"], error["status"], error["retryable"])
                        return response["result"]
                    if not self.process.is_alive():
                        self._stop()
                        raise ServiceError("MODEL_WORKER_EXITED", "The model worker exited unexpectedly.", 503, True)
        except (OSError, BrokenPipeError):
            self._stop()
            raise ServiceError("MODEL_WORKER_EXITED", "The model worker is unavailable.", 503, True) from None
        finally:
            self.lock.release()
