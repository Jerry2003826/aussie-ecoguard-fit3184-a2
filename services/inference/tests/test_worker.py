import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from app.errors import ServiceError
from app.worker import InferenceWorker


def slow_test_worker(connection, settings):
    message = connection.recv()
    _, _, directory, _ = message
    Path(settings.model_cache_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.model_cache_dir, "test-temp-path.txt").write_text(directory)
    Path(directory, "private-query-image").write_bytes(b"test-only-sensitive-data")
    time.sleep(30)


def test_deadline_kills_process_then_cleans_query_media(locked_settings):
    worker = InferenceWorker(replace(locked_settings, inference_timeout=2), target=slow_test_worker)
    started = time.monotonic()
    with pytest.raises(ServiceError) as error:
        worker.run("embed", {"manifestVersion": "unit-test-model-v1"})
    assert error.value.code == "DEADLINE_EXCEEDED"
    assert time.monotonic() - started < 6
    marker = Path(locked_settings.model_cache_dir, "test-temp-path.txt")
    assert marker.exists(), "Test process must actually start and write request media"
    assert not Path(marker.read_text()).exists()
    assert worker.process is None


def test_cancellation_kills_process_and_cleans_query_media(locked_settings):
    worker = InferenceWorker(replace(locked_settings, inference_timeout=10), target=slow_test_worker)
    cancelled = threading.Event()
    marker = Path(locked_settings.model_cache_dir, "test-temp-path.txt")
    def cancel_after_started():
        timeout = time.monotonic() + 4
        while not marker.exists() and time.monotonic() < timeout:
            time.sleep(0.02)
        cancelled.set()
    thread = threading.Thread(target=cancel_after_started)
    thread.start()
    with pytest.raises(ServiceError) as error:
        worker.run("embed", {"manifestVersion": "unit-test-model-v1"}, cancelled)
    thread.join()
    assert error.value.code == "REQUEST_CANCELLED"
    assert marker.exists()
    assert not Path(marker.read_text()).exists()
    assert worker.process is None
