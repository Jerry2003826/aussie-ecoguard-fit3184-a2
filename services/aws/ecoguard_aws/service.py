from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, unquote

from ecoguard_domain import DomainError, Principal, can_read, can_edit, effective_tags, matches
from ecoguard_domain.rules import apply_tags, normalize_tags, minimum_counts, iso_time, parse_time, cosine
from .repository import Write, Conflict


def identifier():
    return uuid.uuid4().hex


def event(kind, entity_id, generation, *, event_id=None, **payload):
    return {"eventId": event_id or f"{kind}:{entity_id}:{generation}", "kind": kind, "entityId": entity_id, "generation": generation, "payload": payload, "status": "PENDING", "createdAt": iso_time()}


def clean(item):
    return {k: v for k, v in item.items() if not k.startswith("_")}


class Service:
    def __init__(self, config, repository, storage, *, inference=None, notifier=None, lex=None):
        self.config, self.repo, self.storage = config, repository, storage
        self.inference, self.notifier, self.lex = inference, notifier, lex

    def _get(self, table, key):
        item = self.repo.get(table, key)
        if not item:
            raise DomainError("NOT_FOUND", "The requested resource was not found.", 404)
        return item

    def operation(self, operation_id, principal=None):
        item = self._get("operations", {"operationId": operation_id})
        if principal and item.get("ownerSub") != principal.sub:
            raise DomainError("NOT_FOUND", "The requested task was not found.", 404)
        return item

    def media(self, media_id, principal, *, edit=False):
        item = self._get("media", {"mediaId": media_id})
        if not (can_edit(principal, item) if edit else can_read(principal, item)):
            raise DomainError("NOT_FOUND", "The requested media was not found.", 404)
        return item

    def media_view(self, item):
        public = {k: item.get(k) for k in ("mediaId", "ownerSub", "filename", "fileType", "contentType", "sizeBytes", "status", "visibility", "tags", "counts", "observedAt", "uploadedAt", "modelVersion", "recordVersion", "classification", "jobId", "error") if item.get(k) is not None}
        public["url"] = f"{self.config.web_url.rstrip('/')}/media/{item['mediaId']}"
        if item.get("status") == "READY":
            for key, field in (("thumbnailKey", "thumbnailUrl"), ("originalKey", "originalUrl")):
                if item.get(key):
                    public[field] = self.storage.sign_read(item[key], 60)
        return public

    def _metadata(self, body):
        filename = body.get("filename")
        content_type = body.get("contentType")
        size = body.get("sizeBytes")
        sha = body.get("sha256", "")
        if not isinstance(filename, str) or not 1 <= len(filename) <= 255 or any(ord(c) < 32 for c in filename):
            raise DomainError("INVALID_FILENAME", "Provide a filename of at most 255 characters.")
        if content_type not in ("image/jpeg", "image/png", "image/webp", "video/mp4"):
            raise DomainError("UNSUPPORTED_TYPE", "Use JPEG, PNG, WebP or MP4.")
        limit = 100 * 1024**2 if content_type == "video/mp4" else 20 * 1024**2
        if type(size) is not int or not 0 < size <= limit:
            raise DomainError("FILE_TOO_LARGE", "Images may be up to 20 MiB; videos up to 100 MiB.", 413)
        if not isinstance(sha, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", sha):
            raise DomainError("INVALID_CHECKSUM", "Provide the file's SHA-256 hex digest.")
        return {"filename": filename, "contentType": content_type, "sizeBytes": size, "sha256": sha.lower(), "fileType": "video" if content_type.startswith("video") else "image"}

    def create_upload(self, principal, body):
        meta = self._metadata(body)
        visibility = body.get("visibility", "private")
        if visibility not in ("private", "public"):
            raise DomainError("INVALID_VISIBILITY", "Visibility must be private or public.")
        dedup_id = f"DEDUP#{principal.sub}#{meta['sha256']}"
        previous = self.repo.get("operations", {"operationId": dedup_id})
        if previous:
            existing = self.repo.get("media", {"mediaId": previous["mediaId"]})
            if existing and existing["status"] not in ("DELETED", "DELETING"):
                return {"duplicate": True, "uploadId": existing["jobId"], "jobId": existing["jobId"], "mediaId": existing["mediaId"], "media": self.media_view(existing)}
            raise DomainError("DUPLICATE_PENDING", "The previous copy is being cleaned up; try again shortly.", 409)
        upload_id, media_id = identifier(), identifier()
        now = iso_time()
        key = f"incoming/library/{upload_id}"
        media = {**meta, "mediaId": media_id, "ownerSub": principal.sub, "visibility": visibility, "status": "UPLOADING", "uploadedAt": now, "observedAt": iso_time(body.get("observedAt")) if body.get("observedAt") else now, "originalKey": key, "jobId": upload_id, "generation": 1, "recordVersion": 1, "autoTags": [], "autoCounts": {}, "manualAdded": [], "manualRemoved": [], "tags": [], "counts": {}, "objectKeys": [key]}
        ttl = self.config.upload_url_seconds
        operation = {**meta, "operationId": upload_id, "jobId": upload_id, "type": "UPLOAD", "ownerSub": principal.sub, "mediaId": media_id, "sourceKey": key, "status": "UPLOADING", "generation": 1, "attemptId": 0, "createdAt": now, "uploadExpiresAt": int(time.time())+ttl, "manifestVersion": self.config.model_manifest_version}
        try:
            self.repo.transact([Write("media", media), Write("operations", operation), Write("operations", {"operationId": dedup_id, "type": "DEDUP", "mediaId": media_id, "ownerSub": principal.sub})])
        except Conflict:
            # Only a competing content reservation can exist with these fresh IDs.
            return self.create_upload(principal, body)
        return {"duplicate": False, "uploadId": upload_id, "jobId": upload_id, "mediaId": media_id, **self.storage.sign_upload(key, meta["contentType"], meta["sha256"], meta["sizeBytes"], ttl)}

    def _require_live_query(self, op):
        if op.get("type") == "QUERY" and op.get("cleanupDeadline", float("inf")) <= time.time():
            raise DomainError("QUERY_EXPIRED", "The query expired. Submit a new query.", 410)

    def renew_upload(self, principal, operation_id):
        op = self.operation(operation_id, principal)
        self._require_live_query(op)
        if op["status"] not in ("UPLOADING", "QUERY_UPLOADING"):
            raise DomainError("UPLOAD_CLOSED", "This upload has already been accepted.", 409)
        try:
            self.storage.head(op["sourceKey"])
        except DomainError as error:
            if error.code != "OBJECT_NOT_FOUND":
                raise
        else:
            # A response may have been lost after a successful PUT. Verify and
            # advance that upload before granting permission to overwrite it.
            return {"uploadId": operation_id, "alreadyUploaded": True, **self.complete_upload(principal, operation_id)}
        ttl = self.config.upload_url_seconds
        if op.get("type") == "QUERY":
            ttl = min(ttl, math.floor(op["cleanupDeadline"] - time.time()))
        if ttl < 1:
            raise DomainError("QUERY_EXPIRED", "The query expired. Submit a new query.", 410)
        self.repo.transact([Write("operations", dict(op, uploadExpiresAt=int(time.time())+ttl), op["_rev"])])
        return {"uploadId": operation_id, **self.storage.sign_upload(op["sourceKey"], op["contentType"], op["sha256"], op["sizeBytes"], ttl)}

    def complete_upload(self, principal, operation_id):
        op = self.operation(operation_id, principal)
        if op["type"] not in ("UPLOAD", "QUERY"):
            raise DomainError("INVALID_TASK", "This is not an upload task.")
        self._require_live_query(op)
        if op["status"] not in ("UPLOADING", "QUERY_UPLOADING"):
            return self.job_view(op, principal)
        head = self.storage.head(op["sourceKey"])
        if head["size"] != op["sizeBytes"] or head["sha256"] != op["sha256"]:
            raise DomainError("CHECKSUM_MISMATCH", "Stored bytes do not match the upload declaration.")
        op["sourceVersionId"] = head.get("versionId")
        op["status"] = "QUERY_QUEUED" if op["type"] == "QUERY" else "QUEUED"
        writes = [Write("operations", op, op["_rev"]), Write("outbox", event("INGEST", operation_id, op["generation"]))]
        if op.get("mediaId"):
            media = self._get("media", {"mediaId": op["mediaId"]})
            if media["status"] in ("DELETING", "DELETED"):
                raise DomainError("TASK_CANCELLED", "The media has been deleted.", 409)
            media["status"] = "QUEUED"
            writes.append(Write("media", media, media["_rev"]))
        try:
            self.repo.transact(writes)
        except Conflict:
            return self.job_view(self.operation(operation_id, principal), principal)
        return self.job_view(self.operation(operation_id, principal), principal)

    def job_view(self, op, principal):
        if op.get("type") == "QUERY" and op.get("resultExpiresAt", float("inf")) <= time.time():
            raise DomainError("QUERY_EXPIRED", "These temporary query results have expired. Submit a new query.", 410)
        result = {k: op.get(k) for k in ("jobId", "mediaId", "queryId", "status", "error", "stage", "attemptId", "cleaned", "progress", "cleanupStatus", "retryable", "cleanupDeadline", "resultExpiresAt") if op.get(k) is not None}
        if op.get("resultIds") is not None:
            result["items"] = []
            for media_id in op["resultIds"]:
                media = self.repo.get("media", {"mediaId": media_id})
                if media and can_read(principal, media) and media["status"] == "READY":
                    view = self.media_view(media)
                    if str(media_id) in op.get("scores", {}):
                        view["matchScore"] = op["scores"][media_id]
                        # Frame references are cleared after query cleanup, so use
                        # retained input metadata to explain the scoring modality.
                        if op.get("description") and op.get("fileType"):
                            basis = ("Image and description similarity; both must match the same detected pet."
                                     if op.get("manifestVersion") == "ecoguard-models-2026-10-08-v3"
                                     else "Image and description similarity, weighted equally for the same detected pet.")
                        elif op.get("description"):
                            basis = "Description similarity to a detected pet."
                        else:
                            basis = "Image similarity to a detected pet."
                        view["matchReason"] = basis + " Possible sighting, not confirmed identity."
                    result["items"].append(view)
        return result

    def _index_items(self, media):
        if media["status"] != "READY":
            return {}
        scope = "PUBLIC" if media["visibility"] == "public" else f"USER#{media['ownerSub']}"
        return {(f"{scope}#{tag}", f"{media['observedAt']}#{media['mediaId']}"): {"scopeSpecies": f"{scope}#{tag}", "observedAtMediaId": f"{media['observedAt']}#{media['mediaId']}", "mediaId": media["mediaId"]} for tag in set(media["tags"]) | {"_all"}}

    def index_writes(self, old, new):
        before, after = self._index_items(old), self._index_items(new)
        writes = []
        for pair in before.keys() | after.keys():
            key = {"scopeSpecies": pair[0], "observedAtMediaId": pair[1]}
            existing = self.repo.get("index", key)
            if pair in after:
                if not existing:
                    writes.append(Write("index", after[pair]))
            elif existing:
                writes.append(Write("index", existing, existing["_rev"], delete=True))
        return writes

    def _candidates(self, principal, tag="_all", public_only=False):
        scopes = ["PUBLIC"] if public_only else [f"USER#{principal.sub}", "PUBLIC"]
        seen = set()
        for scope in scopes:
            for entry in self.repo.all("index", partition={"scopeSpecies": f"{scope}#{tag}"}):
                media_id = entry["mediaId"]
                if media_id in seen:
                    continue
                seen.add(media_id)
                media = self.repo.get("media", {"mediaId": media_id})
                if media and media["status"] == "READY" and can_read(principal, media) and (not public_only or media["visibility"] == "public"):
                    yield media

    def _paginate(self, principal, items, body, context):
        limit = body.get("limit", 20)
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            raise DomainError("INVALID_LIMIT", "limit must be an integer.")
        if not 1 <= limit <= 100:
            raise DomainError("INVALID_LIMIT", "limit must be between 1 and 100.")
        fingerprint = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()
        cursor = body.get("cursor")
        after_key = None
        if cursor:
            state = self.operation(f"CURSOR#{cursor}", principal)
            # Old cursors followed lexical timestamp order, which can disagree
            # with chronological order. Restart rather than silently skip rows.
            if state.get("orderVersion") != 2 or state.get("expiresAt", 0) <= time.time() or state.get("fingerprint") != fingerprint:
                raise DomainError("CURSOR_EXPIRED", "This cursor expired or belongs to another query. Run the query again.", 409)
            after_key = (parse_time(state["afterKey"][0]), state["afterKey"][1])
        def order_key(media):
            return parse_time(media["observedAt"]), media["mediaId"]
        ordered = sorted(items, key=order_key, reverse=True)
        if after_key:
            ordered = [m for m in ordered if order_key(m) < after_key]
        chosen = ordered[:limit]
        next_cursor = None
        if limit < len(ordered):
            next_cursor = identifier()
            self.repo.transact([Write("operations", {"operationId": f"CURSOR#{next_cursor}", "type": "CURSOR", "ownerSub": principal.sub, "expiresAt": int(time.time())+900, "orderVersion": 2, "fingerprint": fingerprint, "afterKey": [iso_time(chosen[-1]["observedAt"]), chosen[-1]["mediaId"]]})])
        return {"items": [self.media_view(m) for m in chosen], "nextCursor": next_cursor}

    def list_media(self, principal, params):
        # Include uploading/failed records in the owner's gallery; never expose another user's private records.
        items = [m for m in self.repo.all("media") if m["ownerSub"] == principal.sub and m["status"] not in ("DELETED", "DELETING")]
        return self._paginate(principal, items, params, {"gallery": principal.sub})

    def search(self, principal, body, *, raw=False, public_only=False):
        conditions = minimum_counts(body.get("minimumCounts", {}))
        after = iso_time(body["from"]) if body.get("from") else None
        before = iso_time(body["to"]) if body.get("to") else None
        after_time = parse_time(after) if after else None
        before_time = parse_time(before) if before else None
        file_type = body.get("fileType")
        items = [m for m in self._candidates(principal, sorted(conditions)[0], public_only) if matches(m, conditions) and (not after_time or parse_time(m["observedAt"]) >= after_time) and (not before_time or parse_time(m["observedAt"]) <= before_time) and (not file_type or m["fileType"] == file_type)]
        if raw:
            return items
        return self._paginate(principal, items, body, {"minimumCounts": conditions, "from": after, "to": before, "fileType": file_type, "publicOnly": public_only})

    def access(self, principal, media_id, body):
        media = self.media(media_id, principal)
        if media["status"] != "READY":
            raise DomainError("NOT_READY", "This media is not ready to view.", 409)
        kind = body.get("kind", "original")
        if kind not in ("thumbnail", "original"):
            raise DomainError("INVALID_KIND", "kind must be original or thumbnail.")
        key = media.get("thumbnailKey" if kind == "thumbnail" else "originalKey")
        if not key:
            raise DomainError("NOT_FOUND", "This media has no requested asset.", 404)
        return {"url": self.storage.sign_read(key, 60), "expiresIn": 60}

    def resolve_url(self, principal, url, *, thumbnail=False, edit=False):
        if not isinstance(url, str) or len(url) > 8192:
            raise DomainError("INVALID_URL", "Provide a system media URL.")
        parsed = urlparse(url)
        web = urlparse(self.config.web_url)
        api = urlparse(self.config.api_url)
        path = unquote(parsed.path)
        if not thumbnail and parsed.scheme == web.scheme and parsed.netloc == web.netloc and re.fullmatch(r"/media/[a-f0-9]{32}", path):
            return self.media(path.rsplit("/", 1)[1], principal, edit=edit)
        bucket = self.config.media_bucket
        allowed = {f"{bucket}.s3.amazonaws.com", f"{bucket}.s3.{self.config.region}.amazonaws.com"}
        if parsed.scheme == "https" and parsed.netloc in allowed:
            target = path.lstrip("/")
        elif self.config.mode == "local" and parsed.netloc == api.netloc and path.startswith("/_dev/objects/"):
            token = path.rsplit("/", 1)[1]
            target = self.storage.uploads.get(token, {}).get("readKey")
        else:
            raise DomainError("INVALID_URL", "Only media URLs issued by this application are accepted.")
        for media in self.repo.all("media"):
            candidates = [media.get("thumbnailKey")] if thumbnail else [media.get("originalKey"), media.get("thumbnailKey"), *media.get("knownUrlAliases", [])]
            if target and target in candidates:
                return self.media(media["mediaId"], principal, edit=edit)
        raise DomainError("NOT_FOUND", "No accessible media matches this URL.", 404)

    def patch_media(self, principal, media_id, body):
        old = self.media(media_id, principal, edit=True)
        visibility = body.get("visibility")
        if visibility not in ("public", "private"):
            raise DomainError("INVALID_VISIBILITY", "Visibility must be private or public.")
        if body.get("recordVersion", old["recordVersion"]) != old["recordVersion"]:
            raise DomainError("VERSION_CONFLICT", "The record changed. Reload and try again.", 409)
        if old["visibility"] == visibility:
            return self.media_view(old)
        new = dict(old, visibility=visibility, recordVersion=old["recordVersion"]+1)
        self.repo.transact([Write("media", new, old["_rev"]), *self.index_writes(old, new), Write("outbox", event("NOTIFY", media_id, new["generation"], event_id=f"NOTIFY:{media_id}:{new['recordVersion']}", recordVersion=new["recordVersion"]))])
        return self.media_view(new)

    def bulk_tags(self, principal, body):
        urls = body.get("urls")
        if not isinstance(urls, list) or not 1 <= len(urls) <= 10:
            raise DomainError("INVALID_BATCH", "Select between 1 and 10 files.")
        tags = normalize_tags(body.get("tags"))
        operation = body.get("operation")
        if type(operation) is not int or operation not in (0, 1):
            raise DomainError("INVALID_OPERATION", "operation must be 1 or 0.")
        media = {m["mediaId"]: m for m in (self.resolve_url(principal, u, edit=True) for u in urls)}
        results = []
        for media_id, old in media.items():
            if old["status"] != "READY":
                raise DomainError("NOT_READY", "Wait for processing before editing tags.", 409)
        for media_id, old in media.items():
            new = apply_tags(old, tags, operation)
            if len(new["tags"]) > 30:
                results.append({"mediaId": media_id, "status": "failed", "error": "A media record can have at most 30 effective tags."})
                continue
            if new == old:
                results.append({"mediaId": media_id, "status": "unchanged"})
                continue
            new["recordVersion"] += 1
            try:
                self.repo.transact([Write("media", new, old["_rev"]), *self.index_writes(old, new), Write("outbox", event("NOTIFY", media_id, new["generation"], event_id=f"NOTIFY:{media_id}:{new['recordVersion']}", recordVersion=new["recordVersion"]))])
                results.append({"mediaId": media_id, "status": "updated", "media": self.media_view(new)})
            except Conflict:
                results.append({"mediaId": media_id, "status": "failed", "error": "The record changed; retry this file."})
        return {"items": results}

    def _deletion_receipt(self, principal, url):
        # Stable media URLs remain retryable after the underlying media row is
        # removed. Only the original owner can recover the retained delete job.
        if not isinstance(url, str) or len(url) > 8192:
            return None
        parsed, web = urlparse(url), urlparse(self.config.web_url)
        path = unquote(parsed.path)
        if parsed.scheme != web.scheme or parsed.netloc != web.netloc or not re.fullmatch(r"/media/[a-f0-9]{32}", path):
            return None
        media_id = path.rsplit("/", 1)[1]
        media = self.repo.get("media", {"mediaId": media_id})
        if media and (media["ownerSub"] != principal.sub or media["status"] != "DELETING"):
            return None
        for receipt in self.repo.all("operations"):
            if (receipt.get("type") == "DELETE" and receipt.get("mediaId") == media_id
                    and receipt.get("ownerSub") == principal.sub
                    and receipt.get("expiresAt", float("inf")) > time.time()
                    and (not media or receipt["generation"] == media["generation"])):
                return receipt
        return None

    def delete_media(self, principal, body):
        urls = body.get("urls")
        if not isinstance(urls, list) or not 1 <= len(urls) <= 10:
            raise DomainError("INVALID_BATCH", "Select between 1 and 10 files.")
        records = {}
        for url in urls:
            receipt = self._deletion_receipt(principal, url)
            media = None if receipt else self.resolve_url(principal, url, edit=True)
            records[(receipt or media)["mediaId"]] = (media, receipt)
        results = []
        for media_id, (old, receipt) in records.items():
            if receipt:
                status = "DELETED" if receipt["status"] == "DELETED" else ("failed" if receipt["status"] == "FAILED" else "DELETING")
                result = {"mediaId": media_id, "jobId": receipt["jobId"], "status": status}
                if status == "failed":
                    result["error"] = "Some objects could not be deleted. Retry the existing deletion job."
                results.append(result)
                continue
            job_id = identifier()
            new = dict(old, status="DELETING", generation=old["generation"]+1, recordVersion=old["recordVersion"]+1)
            op = {"operationId": job_id, "jobId": job_id, "type": "DELETE", "ownerSub": principal.sub, "mediaId": media_id, "status": "QUEUED", "generation": new["generation"], "createdAt": iso_time()}
            writes = [Write("media", new, old["_rev"]), Write("operations", op), Write("outbox", event("DELETE", job_id, new["generation"])), *self.index_writes(old, new)]
            prior_job = self.repo.get("operations", {"operationId": old["jobId"]})
            if prior_job and prior_job["status"] not in ("READY", "FAILED", "CANCELLED"):
                writes.append(Write("operations", dict(prior_job, status="CANCELLED", generation=new["generation"], cleanupStatus="PENDING"), prior_job["_rev"]))
            try:
                self.repo.transact(writes)
                results.append({"mediaId": media_id, "jobId": job_id, "status": "DELETING"})
            except Conflict:
                results.append({"mediaId": media_id, "status": "failed", "error": "The record changed; retry this file."})
        return {"items": results}

    def create_query(self, principal, body):
        kind = body.get("type")
        if kind not in ("file", "pet"):
            raise DomainError("INVALID_QUERY", "type must be file or pet.")
        description = body.get("description", "")
        if not isinstance(description, str) or len(description) > 1000:
            raise DomainError("INVALID_DESCRIPTION", "Descriptions must be at most 1000 characters.")
        description = description.strip()
        has_file = bool(body.get("filename"))
        if not has_file and (kind == "file" or not description.strip()):
            raise DomainError("MISSING_INPUT", "Provide a query file or a pet description.")
        meta = self._metadata(body) if has_file else {}
        query_id = identifier()
        if self.inference is None:
            raise DomainError("INFERENCE_UNAVAILABLE", "Cross-cloud inference is not configured.", 503)
        auth = self.inference.authorize_query(principal, query_id, kind)
        if auth.get("sub") != principal.sub or auth.get("requestId") != query_id:
            raise DomainError("CROSS_CLOUD_IDENTITY", "The secondary cloud rejected the query identity.", 403)
        now = datetime.now(timezone.utc)
        deadline = int(time.time()) + self.config.query_lifetime_seconds
        op = {**meta, "operationId": query_id, "queryId": query_id, "jobId": query_id, "type": "QUERY", "queryType": kind, "ownerSub": principal.sub, "description": description, "status": "QUERY_UPLOADING" if has_file else "PREPROCESSED", "generation": 1, "attemptId": 0, "createdAt": iso_time(), "manifestVersion": self.config.model_manifest_version, "from": iso_time(body.get("from") or (now-timedelta(days=30)).isoformat()) if kind == "pet" else (iso_time(body["from"]) if body.get("from") else None), "to": iso_time(body["to"]) if body.get("to") else None, "cleanupDeadline": deadline, "resultExpiresAt": deadline, "cleaned": False}
        response = {"queryId": query_id, "jobId": query_id, "status": op["status"]}
        if has_file:
            op["sourceKey"] = f"incoming/query/{query_id}"
            ttl = min(self.config.upload_url_seconds, math.floor(deadline - time.time()))
            if ttl < 1:
                raise DomainError("QUERY_EXPIRED", "The query expired. Submit a new query.", 410)
            op["uploadExpiresAt"] = int(time.time())+ttl
            response.update(self.storage.sign_upload(op["sourceKey"], meta["contentType"], meta["sha256"], meta["sizeBytes"], ttl))
        writes = [Write("operations", op)]
        if not has_file:
            writes.append(Write("outbox", event("INFER", query_id, 1)))
        self.repo.transact(writes)
        return response

    def cancel_query(self, principal, query_id):
        old = self.operation(query_id, principal)
        if old.get("type") != "QUERY":
            raise DomainError("INVALID_TASK", "This is not a query task.")
        if old["status"] not in ("CLEANED", "CANCELLED"):
            new = dict(old, status="CANCELLED", generation=old["generation"]+1, cleanupStatus="PENDING")
            self.repo.transact([Write("operations", new, old["_rev"])])
        return self.job_view(self.operation(query_id, principal), principal)

    def retry(self, principal, job_id):
        old = self.operation(job_id, principal)
        self._require_live_query(old)
        if old["status"] != "FAILED" or old.get("leaseUntil", 0) > time.time():
            raise DomainError("NOT_RETRYABLE", "Only failed tasks without a running attempt can be retried.", 409)
        if old.get("type") == "QUERY" and (not old.get("retryable") or old.get("cleaned")):
            raise DomainError("NOT_RETRYABLE", "This query cannot be retried. Submit a new query.", 409)
        new = dict(old, status="QUEUED", error=None, retryable=False, attemptId=old.get("attemptId", 0)+1, leaseUntil=0)
        kind = "DELETE" if old["type"] == "DELETE" else "INGEST"
        if old["type"] == "QUERY":
            kind = "INFER" if old.get("frames") or not old.get("sourceKey") else "INGEST"
            new.update(status="PREPROCESSED" if kind == "INFER" else "QUERY_QUEUED", cleanupStatus=None)
        writes = [Write("operations", new, old["_rev"]), Write("outbox", event(kind, job_id, old["generation"], event_id=f"{kind}:{job_id}:retry:{new['attemptId']}"))]
        if old["type"] == "UPLOAD":
            media = self.media(old["mediaId"], principal, edit=True)
            writes.append(Write("media", dict(media, status="QUEUED", error=None), media["_rev"]))
        self.repo.transact(writes)
        return self.job_view(new, principal)

    def subscriptions(self, principal):
        result = []
        for row in self.repo.all("subscriptions", partition={"ownerSub": principal.sub}):
            if row.get("status") != "CANCELLED" and self.notifier:
                status = self.notifier.confirmation(row["topicArn"], row["email"])
                if status == "ACTIVE" and row["status"] != "ACTIVE":
                    new = dict(row, status="ACTIVE", confirmationStatus="CONFIRMED")
                    try:
                        self.repo.transact([Write("subscriptions", new, row["_rev"])])
                        row = new
                    except Conflict:
                        pass
            result.append({k: row[k] for k in ("subscriptionId", "tags", "status", "confirmationStatus")})
        return {"items": result}

    def subscribe(self, principal, body):
        if not principal.email_verified or not principal.email:
            raise DomainError("EMAIL_NOT_VERIFIED", "Verify your Cognito email before subscribing.", 403)
        tags = normalize_tags(body.get("tags"))
        if not tags:
            raise DomainError("INVALID_TAGS", "Select at least one notification tag.")
        if self.notifier is None:
            raise DomainError("MAIL_UNAVAILABLE", "Email delivery is not configured.", 503)
        topic_arn, status = self.notifier.subscribe(principal.sub, principal.email)
        subscription_id = identifier()
        item = {"ownerSub": principal.sub, "subscriptionId": subscription_id, "email": principal.email, "tags": tags, "topicArn": topic_arn, "status": status, "confirmationStatus": "CONFIRMED" if status == "ACTIVE" else "PENDING", "createdAt": iso_time()}
        self.repo.transact([Write("subscriptions", item)])
        return {k: item[k] for k in ("subscriptionId", "tags", "status", "confirmationStatus")}

    def unsubscribe(self, principal, subscription_id):
        old = self._get("subscriptions", {"ownerSub": principal.sub, "subscriptionId": subscription_id})
        if old["status"] != "CANCELLED":
            self.repo.transact([Write("subscriptions", dict(old, status="CANCELLED"), old["_rev"])])
        return {"subscriptionId": subscription_id, "status": "CANCELLED"}

    def chat_session(self, principal):
        session_id = identifier()
        self.repo.transact([Write("operations", {"operationId": f"CHAT#{session_id}", "type": "CHAT", "sessionId": session_id, "ownerSub": principal.sub, "expiresAt": int(time.time())+3600})])
        return {"sessionId": session_id}

    def chat_message(self, principal, body):
        session_id, text = body.get("sessionId"), body.get("text")
        if not isinstance(session_id, str) or not re.fullmatch(r"[a-f0-9]{32}", session_id):
            raise DomainError("INVALID_SESSION", "Start a new chat session.", 404)
        session = self.operation(f"CHAT#{session_id}", principal)
        if session["expiresAt"] < time.time():
            raise DomainError("SESSION_EXPIRED", "Start a new chat session.", 409)
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 1000:
            raise DomainError("INVALID_MESSAGE", "Messages must contain 1–1000 characters.")
        if self.lex is None:
            raise DomainError("LEX_UNAVAILABLE", "AWS Lex has not been configured. No simulated bot is used.", 503)
        response = self.lex.recognize(session_id, text)
        latest = self.operation(f"CHAT#{session_id}", principal)
        items = []
        for media_id in latest.get("resultIds", []):
            media = self.repo.get("media", {"mediaId": media_id})
            if media and can_read(principal, media) and media["status"] == "READY":
                items.append(self.media_view(media))
        return {"messages": response.get("messages", []), "items": items, "sessionId": session_id}
