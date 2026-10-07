from __future__ import annotations
import hashlib
import json
import time
from datetime import datetime
from ecoguard_domain import DomainError


class CloudRunClient:
    MAX_TRANSIENT_RETRIES = 2

    def __init__(self, config):
        self.config = config
        self.token, self.token_expiry = None, 0

    @staticmethod
    def _timeout(deadline):
        remaining = deadline - time.time()
        if remaining <= 0:
            raise DomainError("INFERENCE_TIMEOUT", "The inference request exhausted its time budget. Retry while the query remains active.", 504, retryable=True)
        connect = min(5, remaining / 4)
        return connect, remaining - connect

    def _google_token(self, deadline):
        if self.token and time.time() < self.token_expiry - 120:
            return self.token
        import google.auth
        from google.auth.exceptions import GoogleAuthError, TransportError
        from google.auth.transport.requests import Request
        import requests
        if not self.config.google_credentials_file or not self.config.google_invoker_email:
            raise DomainError("FEDERATION_UNCONFIGURED", "Cross-cloud service identity is not configured.", 503)
        with open(self.config.google_credentials_file, encoding="utf-8") as stream:
            federation = json.load(stream)
        # Mint the ID token as the federated principal, not as an already impersonated SA.
        # roles/iam.workloadIdentityUser on the target SA grants this principal getOpenIdToken.
        federation.pop("service_account_impersonation_url", None)
        federation.pop("service_account_impersonation", None)
        credentials, _ = google.auth.load_credentials_from_dict(federation, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        transport = Request()
        def bounded_request(*args, **kwargs):
            kwargs["timeout"] = self._timeout(deadline)
            return transport(*args, **kwargs)
        try:
            credentials.refresh(bounded_request)
        except GoogleAuthError as error:
            transient = isinstance(error, TransportError) or bool(getattr(error, "retryable", False))
            raise DomainError("FEDERATION_FAILED", "Could not obtain the inference service identity.", 503, retryable=transient) from error
        response = requests.post(f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{self.config.google_invoker_email}:generateIdToken", headers={"Authorization": f"Bearer {credentials.token}"}, json={"audience": self.config.inference_url.rstrip("/"), "includeEmail": True}, timeout=self._timeout(deadline))
        if response.status_code != 200:
            raise DomainError("FEDERATION_FAILED", "Could not obtain the inference service identity.", 503, retryable=response.status_code in (408, 429) or response.status_code >= 500)
        self.token = response.json()["token"]
        self.token_expiry = time.time() + 3300
        return self.token

    def _call(self, path, payload, *, user_token=None, timeout=390):
        import requests
        url = self.config.inference_url.rstrip("/")
        if not url or not url.startswith("https://"):
            raise DomainError("INFERENCE_UNAVAILABLE", "A protected HTTPS inference service is required.", 503)
        deadline = time.time() + timeout
        if payload.get("deadlineAt"):
            parsed = datetime.fromisoformat(payload["deadlineAt"].replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise DomainError("INVALID_DEADLINE", "Inference deadlines must include a time zone.")
            deadline = min(deadline, parsed.timestamp())
        for attempt in range(self.MAX_TRANSIENT_RETRIES + 1):
            self._timeout(deadline)
            try:
                headers = {"X-Serverless-Authorization": f"Bearer {self._google_token(deadline)}"}
                if user_token:
                    headers["Authorization"] = f"Bearer {user_token}"
                response = requests.post(url + path, json=payload, headers=headers, timeout=self._timeout(deadline))
                self._timeout(deadline)  # A late response must not escape the shared budget.
                if response.status_code in (408, 429) or response.status_code >= 500:
                    raise DomainError("INFERENCE_TRANSIENT", "The inference service is temporarily unavailable. Retry this task.", 503, retryable=True)
                if response.status_code >= 400:
                    raise DomainError("INFERENCE_REJECTED", "The inference service rejected this task.", 422)
                data = response.json()
                if not isinstance(data, dict):
                    raise DomainError("INFERENCE_RESPONSE", "The inference service returned an invalid response.", 502)
                return data.get("data", data)
            except requests.RequestException as error:
                failure = DomainError("INFERENCE_TRANSIENT", "Inference transport failed. Retry this task.", 503, retryable=True)
                failure.__cause__ = error
            except DomainError as error:
                if not error.retryable:
                    raise
                failure = error
            if attempt == self.MAX_TRANSIENT_RETRIES or deadline - time.time() <= 0.2 * (2 ** attempt):
                raise failure
            time.sleep(0.2 * (2 ** attempt))

    def authorize_query(self, principal, request_id, purpose):
        if not principal.token:
            raise DomainError("TOKEN_REQUIRED", "A Cognito access token is required.", 401)
        return self._call("/v1/query-authorizations", {"requestId": request_id, "purpose": purpose}, user_token=principal.token, timeout=8)

    def infer(self, payload):
        return self._call("/v1/infer", payload)

    def embed(self, payload):
        return self._call("/v1/embed", payload)


class UnconfiguredInference:
    """Development cannot silently pretend to run the production models."""
    def authorize_query(self, *args, **kwargs):
        raise DomainError("CLOUD_NOT_CONFIGURED", "Connect the real Cognito and secondary-cloud inference environment to run query processing.", 503)

    def infer(self, payload):
        raise DomainError("MODEL_NOT_CONFIGURED", "No real inference model is configured in this development environment.", 503)

    embed = infer


class SnsNotifier:
    def __init__(self, config, session=None):
        import boto3
        self.config = config
        self.client = (session or boto3.Session(region_name=config.region)).client("sns")

    def subscribe(self, sub, email):
        suffix = hashlib.sha256(sub.encode()).hexdigest()[:24]
        topic = self.client.create_topic(Name=f"{self.config.resource_prefix}-user-{suffix}")["TopicArn"]
        status = self.confirmation(topic, email)
        if status == "MISSING":
            self.client.subscribe(TopicArn=topic, Protocol="email", Endpoint=email, ReturnSubscriptionArn=True)
        return topic, "ACTIVE" if status == "ACTIVE" else "PENDING_CONFIRMATION"

    def confirmation(self, topic, email):
        for page in self.client.get_paginator("list_subscriptions_by_topic").paginate(TopicArn=topic):
            for subscription in page.get("Subscriptions", []):
                if subscription.get("Protocol") == "email" and subscription.get("Endpoint") == email:
                    return "PENDING_CONFIRMATION" if subscription["SubscriptionArn"] == "PendingConfirmation" else "ACTIVE"
        return "MISSING"

    def publish(self, topic, message):
        return self.client.publish(TopicArn=topic, Subject="Aussie EcoGuard: matching sighting", Message=message)["MessageId"]


class LexClient:
    def __init__(self, config, session=None):
        import boto3
        self.config = config
        self.client = (session or boto3.Session(region_name=config.region)).client("lexv2-runtime")

    def recognize(self, session_id, text):
        if not self.config.lex_bot_id or not self.config.lex_alias_id:
            raise DomainError("LEX_UNAVAILABLE", "AWS Lex is not configured.", 503)
        return self.client.recognize_text(botId=self.config.lex_bot_id, botAliasId=self.config.lex_alias_id, localeId=self.config.lex_locale, sessionId=session_id, text=text)
