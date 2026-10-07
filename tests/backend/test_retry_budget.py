"""HTTP retries and local task recovery, using explicit transport/model fixtures."""
import hashlib
import sys
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "packages/domain"), str(ROOT / "services/aws")]
from ecoguard_domain import DomainError, Principal
from ecoguard_aws.cloud_clients import CloudRunClient
from ecoguard_aws.config import Config
from ecoguard_aws.repository import MemoryRepository, Write
from ecoguard_aws.service import Service
from ecoguard_aws.storage import MemoryStorage
from ecoguard_aws.workers import Workers
from test_backend import FixtureInference, FixturePreprocessor


class RetryBudgetTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        clock = patch("time.time", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        sleep = patch("time.sleep", side_effect=lambda seconds: setattr(self, "now", self.now + seconds))
        sleep.start()
        self.addCleanup(sleep.stop)
        self.config = Config(mode="local", inference_url="https://inference.example.invalid", model_manifest_version="fixture-v1")
        self.client = CloudRunClient(self.config)
        self.client._google_token = Mock(return_value="disposable-fixture-token")

    def payload(self, deadline=1010):
        return {"deadlineAt": datetime.fromtimestamp(deadline, timezone.utc).isoformat()}

    def response(self, status, data=None):
        return Mock(status_code=status, json=Mock(return_value=data or {"data": {"fixture": True}}))

    def test_initial_plus_two_transient_retries_can_recover(self):
        with patch("requests.post", side_effect=[self.response(429), self.response(500), self.response(200)]) as post:
            self.assertEqual(self.client.infer(self.payload()), {"fixture": True})
        self.assertEqual(post.call_count, 3)
        self.assertLess(self.now, 1010)
        for call in post.call_args_list:
            self.assertLessEqual(sum(call.kwargs["timeout"]), 10)

    def test_transient_exhaustion_is_explicit_retryable_domain_failure(self):
        import requests
        for outcome in (self.response(503), self.response(408), requests.ConnectionError("fixture network")):
            with self.subTest(outcome=type(outcome).__name__):
                self.now = 1000
                with patch("requests.post", side_effect=[outcome] * 4) as post, self.assertRaises(DomainError) as caught:
                    self.client.infer(self.payload())
                self.assertEqual(post.call_count, 3)
                self.assertTrue(caught.exception.retryable)

    def test_permanent_rejection_does_not_retry(self):
        with patch("requests.post", return_value=self.response(422)) as post, self.assertRaises(DomainError) as caught:
            self.client.infer(self.payload())
        self.assertEqual(post.call_count, 1)
        self.assertFalse(caught.exception.retryable)

    def test_api_errors_expose_the_safe_retry_contract(self):
        import json
        from ecoguard_aws.api import response
        for retryable in (False, True):
            error = DomainError("FIXTURE_ERROR", "A safe fixture message.", 503, retryable=retryable)
            body = json.loads(response(503, error=error, request_id="fixture-request")["body"])
            self.assertEqual(body["error"], {"code": "FIXTURE_ERROR", "message": "A safe fixture message.", "retryable": retryable})
            self.assertEqual(body["requestId"], "fixture-request")

    def test_deadline_prevents_another_retry_and_late_success(self):
        for status in (503, 200):
            with self.subTest(status=status):
                self.now = 1000
                def delayed(*args, **kwargs):
                    self.now = 1011
                    return self.response(status)
                with patch("requests.post", side_effect=delayed) as post, self.assertRaises(DomainError) as caught:
                    self.client.infer(self.payload())
                self.assertEqual(post.call_count, 1)
                self.assertTrue(caught.exception.retryable)

    def test_authorization_has_one_shared_eight_second_budget(self):
        seen = []
        def delayed(*args, **kwargs):
            seen.append(kwargs["timeout"])
            self.now += 4
            return self.response(503)
        with patch("requests.post", side_effect=delayed), self.assertRaises(DomainError):
            self.client.authorize_query(Principal("owner", token="fixture-user-token"), "request", "file")
        self.assertEqual(len(seen), 2)
        self.assertLessEqual(sum(seen[0]), 8)
        self.assertLess(sum(seen[1]), 4)

    def test_identity_exchange_and_http_share_the_same_deadline(self):
        def slow_identity(deadline):
            self.assertEqual(deadline, 1008)
            self.now = 1007
            return "fixture-service-token"
        self.client._google_token = slow_identity
        with patch("requests.post", return_value=self.response(200)) as post:
            self.client.authorize_query(Principal("owner", token="fixture-user-token"), "request", "file")
        self.assertLessEqual(sum(post.call_args.kwargs["timeout"]), 1)

    def test_wif_transport_is_bounded_before_iam_exchange(self):
        import json
        from unittest.mock import mock_open
        from dataclasses import replace
        self.client.config = replace(self.config, google_credentials_file="fixture-public-wif.json", google_invoker_email="fixture@example.invalid")
        del self.client._google_token
        credentials = Mock(token="fixture-access-token")
        def refresh(transport):
            transport("https://sts.googleapis.com/v1/token", method="POST")
            self.now = 1007
        credentials.refresh.side_effect = refresh
        with patch("builtins.open", mock_open(read_data=json.dumps({"type": "external_account"}))), patch("google.auth.load_credentials_from_dict", return_value=(credentials, None)), patch("google.auth.transport.requests.Request") as request, patch("requests.post", return_value=self.response(200, {"token": "fixture-id-token"})) as post:
            self.assertEqual(self.client._google_token(1008), "fixture-id-token")
        self.assertLessEqual(sum(request.return_value.call_args.kwargs["timeout"]), 8)
        self.assertLessEqual(sum(post.call_args.kwargs["timeout"]), 1)

    def make_query(self):
        repo, inference = MemoryRepository(), FixtureInference()
        storage = MemoryStorage(self.config)
        service = Service(self.config, repo, storage, inference=inference)
        workers = Workers(service, FixturePreprocessor(storage))
        owner, data = Principal("retry-owner"), b"explicit fixture query bytes"
        result = service.create_query(owner, {"type": "file", "filename": "fixture.jpg", "contentType": "image/jpeg", "sizeBytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        storage.accept_upload(result["uploadUrl"].rsplit("/", 1)[1], data)
        workers.ingest(result["jobId"])
        return service, workers, inference, owner, result["jobId"]

    def test_retryable_query_failure_retains_input_then_owner_retry_succeeds(self):
        service, workers, inference, owner, job = self.make_query()
        inference.error = DomainError("INFERENCE_TRANSIENT", "Fixture retry exhausted", 503, retryable=True)
        workers.infer(job)
        failed = service.operation(job)
        self.assertEqual(failed["status"], "FAILED")
        self.assertTrue(service.job_view(failed, owner)["retryable"])
        self.assertEqual(service.job_view(failed, owner)["cleanupDeadline"], 1900)
        self.assertEqual(service.job_view(failed, owner)["resultExpiresAt"], 1900)
        self.assertFalse(workers.clean_query(job))
        self.assertIn(failed["sourceKey"], service.storage.objects)
        with self.assertRaises(DomainError):
            service.retry(Principal("someone-else"), job)
        retried = service.retry(owner, job)
        self.assertEqual(retried["status"], "PREPROCESSED")
        self.assertFalse(retried["retryable"])
        self.assertEqual(service.operation(job)["cleanupDeadline"], failed["cleanupDeadline"])
        inference.error = None
        workers.infer(job)
        self.assertEqual(service.operation(job)["status"], "QUERY_READY")
        self.assertFalse(service.storage.objects)

    def test_retryable_query_failure_is_cleaned_at_original_deadline(self):
        service, workers, inference, owner, job = self.make_query()
        inference.error = DomainError("INFERENCE_TRANSIENT", "Fixture retry exhausted", 503, retryable=True)
        workers.infer(job)
        self.now = service.operation(job)["cleanupDeadline"]
        with self.assertRaises(DomainError) as caught:
            service.retry(owner, job)
        self.assertEqual(caught.exception.code, "QUERY_EXPIRED")
        workers.cleanup()
        expired = service.operation(job)
        self.assertFalse(expired["retryable"])
        self.assertTrue(expired["cleaned"])
        self.assertEqual(expired["error"]["code"], "QUERY_EXPIRED")
        self.assertFalse(service.storage.objects)

    def test_permanent_query_failure_is_not_retryable(self):
        service, workers, inference, owner, job = self.make_query()
        inference.error = DomainError("NO_ANIMALS", "Fixture permanent failure")
        workers.infer(job)
        self.assertTrue(service.operation(job)["cleaned"])
        with self.assertRaises(DomainError) as caught:
            service.retry(owner, job)
        self.assertEqual(caught.exception.code, "NOT_RETRYABLE")

    def test_local_pump_records_retryable_failure_instead_of_losing_task(self):
        from ecoguard_aws.local import LocalQueue, LocalTaskPump
        service, workers, inference, owner, job = self.make_query()
        queue = LocalQueue()
        workers.queue = queue
        inference.error = RuntimeError("explicit local fixture transport failure")
        pump = LocalTaskPump(workers, queue)
        pump.run()
        op = service.operation(job)
        self.assertEqual(op["status"], "FAILED")
        self.assertTrue(op["retryable"])
        self.assertIn(op["sourceKey"], service.storage.objects)
        service.retry(owner, job)
        inference.error = None
        pump.run()
        self.assertEqual(service.operation(job)["status"], "QUERY_READY")
        self.assertFalse(service.storage.objects)

    def test_local_duplicate_delivery_does_not_fail_a_live_lease(self):
        from ecoguard_aws.local import LocalQueue, LocalTaskPump
        service, workers, inference, owner, job = self.make_query()
        old = service.operation(job)
        service.repo.transact([Write("operations", dict(old, status="INFERRING", leaseUntil=1050), old["_rev"])])
        queue = LocalQueue()
        workers.queue = queue
        pump = LocalTaskPump(workers, queue)
        pump.run()
        self.assertEqual(service.operation(job)["status"], "INFERRING")
        self.assertEqual(service.operation(job)["leaseUntil"], 1050)
        self.assertEqual(inference.calls, 0)
        self.assertTrue(queue.deferred)
        self.now = 1051
        pump.run()
        self.assertEqual(service.operation(job)["status"], "QUERY_READY")

    def test_local_consumers_are_serialized_while_real_worker_is_active(self):
        from ecoguard_aws.local import LocalQueue, LocalTaskPump
        service, workers, inference, owner, job = self.make_query()
        queue = LocalQueue()
        workers.queue = queue
        pump = LocalTaskPump(workers, queue)
        entered, release = threading.Event(), threading.Event()
        def pause():
            entered.set()
            if not release.wait(3):
                raise AssertionError("Fixture worker was not released")
        inference.before_return = pause
        thread = threading.Thread(target=pump.run)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            pump.run()
            self.assertEqual(service.operation(job)["status"], "INFERRING")
            self.assertEqual(inference.calls, 1)
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(service.operation(job)["status"], "QUERY_READY")

    def test_local_idle_pump_reclaims_cancelled_query(self):
        from ecoguard_aws.local import LocalQueue, LocalTaskPump
        service, workers, inference, owner, job = self.make_query()
        workers.queue = LocalQueue()
        service.cancel_query(owner, job)
        LocalTaskPump(workers, workers.queue).run()
        self.assertEqual(service.operation(job)["status"], "CANCELLED")
        self.assertTrue(service.operation(job)["cleaned"])
        self.assertFalse(service.storage.objects)

    def test_preprocessing_failure_retries_ingest_and_text_failure_retries_infer(self):
        for kind in ("file", "text"):
            with self.subTest(kind=kind):
                service, workers, inference, owner, job = self.make_query()
                if kind == "text":
                    job = service.create_query(owner, {"type": "pet", "description": "fixture ginger cat"})["jobId"]
                else:
                    old = service.operation(job)
                    service.repo.transact([Write("operations", dict(old, status="PREPROCESSING", frames=[]), old["_rev"])])
                workers._failure(job, DomainError("FIXTURE_TRANSIENT", "Fixture dependency unavailable", 503, retryable=True))
                view = service.retry(owner, job)
                self.assertEqual(view["status"], "QUERY_QUEUED" if kind == "file" else "PREPROCESSED")
                events = [row for row in service.repo.all("outbox") if row["eventId"].startswith(("INGEST:" if kind == "file" else "INFER:") + job + ":retry:")]
                self.assertEqual(len(events), 1)


if __name__ == "__main__":
    unittest.main()
