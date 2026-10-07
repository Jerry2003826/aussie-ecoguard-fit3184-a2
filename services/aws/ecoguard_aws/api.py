from __future__ import annotations
import base64
import json
import re
import time
import uuid
from ecoguard_domain import DomainError, Principal
from .repository import Conflict


def principal_from_event(event, config):
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    if not claims.get("sub") or claims.get("token_use") != "access":
        raise DomainError("UNAUTHENTICATED", "A valid Cognito access token is required.", 401)
    if config.cognito_client_id and claims.get("client_id") != config.cognito_client_id:
        raise DomainError("WRONG_CLIENT", "The access token belongs to another application.", 401)
    if int(claims.get("exp", 0)) <= time.time():
        raise DomainError("SESSION_EXPIRED", "Your session expired. Sign in again.", 401)
    token = next((v for k, v in event.get("headers", {}).items() if k.lower() == "authorization"), "")
    token = token[7:] if token.lower().startswith("bearer ") else ""
    return Principal(claims["sub"], claims.get("email", ""), claims.get("email_verified") in (True, "true"), token)


def dispatch(service, principal, method, path, body=None, params=None):
    body, params = body or {}, params or {}
    if not path.startswith("/api/v1/"):
        raise DomainError("NOT_FOUND", "The requested endpoint was not found.", 404)
    route = path[len("/api/v1/"):].strip("/")
    parts = route.split("/")
    if route == "uploads" and method == "POST":
        return 201, service.create_upload(principal, body)
    if len(parts) == 3 and parts[0] == "uploads" and method == "POST":
        if parts[2] == "complete":
            return 202, service.complete_upload(principal, parts[1])
        if parts[2] == "renew":
            return 200, service.renew_upload(principal, parts[1])
    if len(parts) == 2 and parts[0] == "jobs" and method == "GET":
        return 200, service.job_view(service.operation(parts[1], principal), principal)
    if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "retry" and method == "POST":
        return 202, service.retry(principal, parts[1])
    if route == "media" and method == "GET":
        return 200, service.list_media(principal, params)
    if route == "media/tags" and method == "POST":
        return 200, service.bulk_tags(principal, body)
    if route == "media/delete" and method == "POST":
        return 202, service.delete_media(principal, body)
    if len(parts) == 2 and parts[0] == "media":
        if method == "GET":
            return 200, service.media_view(service.media(parts[1], principal))
        if method == "PATCH":
            return 200, service.patch_media(principal, parts[1], body)
    if len(parts) == 3 and parts[0] == "media" and parts[2] == "access" and method == "POST":
        return 200, service.access(principal, parts[1], body)
    if route == "search/tags" and method == "POST":
        return 200, service.search(principal, body)
    if route == "search/thumbnail" and method == "POST":
        media = service.resolve_url(principal, body.get("thumbnailUrl"), thumbnail=True)
        return 200, {"mediaId": media["mediaId"], "originalUrl": service.access(principal, media["mediaId"], {"kind": "original"})["url"], "media": service.media_view(media)}
    if route == "query-jobs" and method == "POST":
        return 202, service.create_query(principal, body)
    if len(parts) == 2 and parts[0] == "query-jobs" and method == "GET":
        op = service.operation(parts[1], principal)
        if op.get("type") != "QUERY":
            raise DomainError("NOT_FOUND", "The query task was not found.", 404)
        return 200, service.job_view(op, principal)
    if len(parts) == 3 and parts[0] == "query-jobs" and method == "POST":
        if parts[2] == "complete":
            return 202, service.complete_upload(principal, parts[1])
        if parts[2] == "cancel":
            return 202, service.cancel_query(principal, parts[1])
    if route == "subscriptions":
        if method == "GET":
            return 200, service.subscriptions(principal)
        if method == "POST":
            return 201, service.subscribe(principal, body)
    if len(parts) == 2 and parts[0] == "subscriptions" and method == "DELETE":
        return 200, service.unsubscribe(principal, parts[1])
    if route == "chat/sessions" and method == "POST":
        return 201, service.chat_session(principal)
    if route == "chat/messages" and method == "POST":
        return 200, service.chat_message(principal, body)
    raise DomainError("NOT_FOUND", "The requested endpoint was not found.", 404)


def response(status, data=None, error=None, request_id=None):
    payload = {"error": {"code": error.code, "message": error.message}} if error else {"data": data}
    payload["requestId"] = request_id or uuid.uuid4().hex
    return {"statusCode": status, "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"}, "body": json.dumps(payload, allow_nan=False)}


def handle(event, context, service):
    request_id = event.get("requestContext", {}).get("requestId", uuid.uuid4().hex)
    try:
        if event.get("requestContext", {}).get("http", {}).get("method") == "OPTIONS":
            return {"statusCode": 204, "headers": {"Cache-Control": "no-store"}, "body": ""}
        principal = principal_from_event(event, service.config)
        method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
        raw = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode()
        if len(raw) > 65536:
            raise DomainError("REQUEST_TOO_LARGE", "API requests must be smaller than 64 KiB. Upload media directly to storage.", 413)
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise DomainError("INVALID_BODY", "The JSON request body must be an object.")
        path = event.get("rawPath", "")
        if method == "POST" and path.rstrip("/").endswith("/subscriptions") and not principal.email_verified:
            import boto3
            info = boto3.client("cognito-idp", region_name=service.config.region).get_user(AccessToken=principal.token)
            attrs = {a["Name"]: a["Value"] for a in info["UserAttributes"]}
            principal = Principal(principal.sub, attrs.get("email", ""), attrs.get("email_verified") == "true", principal.token)
        status, data = dispatch(service, principal, method, path, body, event.get("queryStringParameters"))
        return response(status, data, request_id=request_id)
    except DomainError as error:
        return response(error.status, error=error, request_id=request_id)
    except Conflict:
        return response(409, error=DomainError("CONCURRENT_UPDATE", "The record changed; reload and retry.", 409), request_id=request_id)
    except (ValueError, TypeError, KeyError, UnicodeError):
        return response(422, error=DomainError("INVALID_REQUEST", "The request contains invalid or missing fields."), request_id=request_id)
    except Exception as error:
        # Do not serialize exception details: SDK errors and URLs can contain credentials.
        print(json.dumps({"requestId": request_id, "errorType": type(error).__name__, "message": "Backend dependency failed"}))
        return response(503, error=DomainError("DEPENDENCY_UNAVAILABLE", "A backend service is unavailable. Please retry.", 503), request_id=request_id)
