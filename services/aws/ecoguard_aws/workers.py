from __future__ import annotations
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote_plus

from ecoguard_domain import DomainError, Principal, can_read, effective_tags
from ecoguard_domain.rules import normalize_tags, iso_time, cosine
from .repository import Write, Conflict
from .service import event


class Workers:
    def __init__(self, service, preprocessor=None, queue=None):
        self.s = service
        self.repo, self.storage = service.repo, service.storage
        self.preprocessor, self.queue = preprocessor, queue

    def _claim(self, job_id, allowed, state, lease=470):
        op = self.s.operation(job_id)
        if op["status"] == state and op.get("leaseUntil", 0) > time.time():
            raise RuntimeError("The task is already leased")
        if op["status"] not in allowed and op["status"] != state:
            return None
        new = dict(op, status=state, attemptId=op.get("attemptId", 0)+1, leaseUntil=int(time.time())+lease, stage=state)
        self.repo.transact([Write("operations", new, op["_rev"])])
        return self.s.operation(job_id)

    def _failure(self, job_id, error):
        op = self.s.operation(job_id)
        if op["status"] in ("CANCELLED", "CLEANED", "DELETED", "READY", "QUERY_READY"):
            return
        new = dict(op, status="FAILED", leaseUntil=0, error={"code": error.code, "message": error.message}, cleanupStatus="PENDING" if op["type"] == "QUERY" else None)
        writes = [Write("operations", new, op["_rev"])]
        if op.get("mediaId") and op["type"] == "UPLOAD":
            media = self.repo.get("media", {"mediaId": op["mediaId"]})
            if media and media["generation"] == op["generation"] and media["status"] not in ("DELETING", "DELETED"):
                writes.append(Write("media", dict(media, status="FAILED", error=new["error"]), media["_rev"]))
        try:
            self.repo.transact(writes)
        except Conflict:
            return
        if op["type"] == "QUERY":
            self.clean_query(job_id)

    def ingest(self, job_id):
        initial = self.s.operation(job_id)
        if initial["status"] in ("UPLOADING", "QUERY_UPLOADING"):
            self.s.complete_upload(Principal(initial["ownerSub"]), job_id)
        op = self._claim(job_id, {"QUEUED", "QUERY_QUEUED"}, "PREPROCESSING", 115)
        if op is None:
            return
        try:
            if self.preprocessor is None:
                raise DomainError("PREPROCESSOR_UNAVAILABLE", "Media preprocessing is not configured.", 503)
            result = self.preprocessor.process(op)
            latest = self.s.operation(job_id)
            if latest["_rev"] != op["_rev"] or latest["generation"] != op["generation"]:
                for key in result["objectKeys"]:
                    self.storage.delete(key)
                return
            new = dict(op, status="PREPROCESSED", leaseUntil=0, **result)
            writes = [Write("operations", new, op["_rev"]), Write("outbox", event("INFER", job_id, op["generation"], event_id=f"INFER:{job_id}:{op['generation']}:{op['attemptId']}"))]
            if op.get("mediaId"):
                media = self.repo.get("media", {"mediaId": op["mediaId"]})
                if not media or media["generation"] != op["generation"] or media["status"] in ("DELETING", "DELETED"):
                    return
                writes.append(Write("media", dict(media, status="INFERRING", thumbnailKey=result["thumbnailKey"], objectKeys=list(set(media.get("objectKeys", []) + result["objectKeys"]))), media["_rev"]))
            self.repo.transact(writes)
        except DomainError as error:
            self._failure(job_id, error)

    def infer(self, job_id):
        op = self._claim(job_id, {"PREPROCESSED"}, "INFERRING")
        if op is None:
            return
        try:
            if op.get("description") and not op.get("frames"):
                result = self.s.inference.embed({"jobId": job_id, "manifestVersion": op["manifestVersion"], "text": op["description"]})
                detection = {"features": [{"vector": result["vector"], "featureVersion": result["featureVersion"]}], "tags": [], "counts": {}, "manifestVersion": op["manifestVersion"]}
            else:
                frames = [{**{k: v for k, v in frame.items() if k != "key"}, "url": self.storage.sign_read(frame["key"], 900)} for frame in op["frames"]]
                payload = {"jobId": job_id, "ownerSub": op["ownerSub"], "generation": op["generation"], "manifestVersion": op["manifestVersion"], "mediaType": op["fileType"], "frames": frames, "deadlineAt": (datetime.now(timezone.utc)+timedelta(seconds=330)).isoformat()}
                detection = self.s.inference.infer(payload)
            if detection.get("manifestVersion") != op["manifestVersion"]:
                raise DomainError("MODEL_VERSION_MISMATCH", "Inference returned the wrong model manifest.", 502)
            latest = self.s.operation(job_id)
            if latest["_rev"] != op["_rev"]:
                return
            if op["type"] == "QUERY":
                self._query_result(op, detection)
                return
            old = self.repo.get("media", {"mediaId": op["mediaId"]})
            if not old or old["generation"] != op["generation"] or old["status"] in ("DELETED", "DELETING"):
                return
            tags = normalize_tags(detection.get("tags", []))
            counts = detection.get("counts", {})
            if not isinstance(counts, dict) or any(type(v) is not int or v < 0 for v in counts.values()):
                raise DomainError("INVALID_DETECTION", "Inference returned invalid animal counts.", 502)
            classification = detection.get("classification", "uncertain")
            if classification not in ("pet", "invasive", "mixed", "uncertain"):
                classification = "uncertain"
            feature_key = f"derived/{old['mediaId']}/{old['generation']}/features.json"
            self.storage.put(feature_key, json.dumps({"features": detection.get("features", [])}).encode(), "application/json")
            target = f"library/{classification}/{old['ownerSub']}/{old['mediaId']}/original"
            self.storage.copy(op["sourceKey"], target)
            new = dict(old, status="READY", autoTags=tags, autoCounts=counts, originalKey=target, featureKey=feature_key, classification=classification, modelVersion=op["manifestVersion"], recordVersion=old["recordVersion"]+1, knownUrlAliases=list(set(old.get("knownUrlAliases", []) + [old["originalKey"]])), objectKeys=list(set(old.get("objectKeys", []) + [feature_key, target])))
            new["tags"], new["counts"] = effective_tags(new)
            finished = dict(op, status="READY", leaseUntil=0, completedAt=iso_time())
            self.repo.transact([Write("media", new, old["_rev"]), Write("operations", finished, op["_rev"]), *self.s.index_writes(old, new), Write("outbox", event("NOTIFY", old["mediaId"], old["generation"], event_id=f"NOTIFY:{old['mediaId']}:{new['recordVersion']}", recordVersion=new["recordVersion"]))])
            self.storage.delete(op["sourceKey"])
        except DomainError as error:
            self._failure(job_id, error)

    def _query_result(self, op, detection):
        principal = Principal(op["ownerSub"])
        scores = {}
        if op["queryType"] == "file":
            conditions = {k: 1 for k, v in detection.get("counts", {}).items() if v > 0}
            if not conditions:
                raise DomainError("NO_ANIMALS", "No animal species were detected. Try a clearer query file.")
            items = self.s.search(principal, {"minimumCounts": conditions, "from": op.get("from"), "to": op.get("to")}, raw=True)
        else:
            query_features = detection.get("features", [])
            items = []
            for media in self.s._candidates(principal, public_only=True):
                if (op.get("from") and media["observedAt"] < op["from"]) or (op.get("to") and media["observedAt"] > op["to"]) or not media.get("featureKey"):
                    continue
                stored = json.loads(self.storage.read(media["featureKey"], 2*1024**2)).get("features", [])
                score = max((cosine(q["vector"], target["vector"]) for q in query_features for target in stored if q.get("featureVersion") == target.get("featureVersion") and (not q.get("species") or q.get("species") == target.get("species"))), default=-1)
                if score >= self.s.config.pet_score_threshold:
                    scores[media["mediaId"]] = round(score, 6)
                    items.append(media)
            items.sort(key=lambda m: scores[m["mediaId"]], reverse=True)
            items = items[:10]
        result_ids = [m["mediaId"] for m in items[:100]]
        new = dict(op, status="QUERY_READY", leaseUntil=0, resultIds=result_ids, scores={k: scores[k] for k in result_ids if k in scores}, cleanupStatus="PENDING", completedAt=iso_time())
        self.repo.transact([Write("operations", new, op["_rev"])])
        self.clean_query(op["operationId"])

    def clean_query(self, job_id):
        op = self.s.operation(job_id)
        if op.get("type") != "QUERY" or op["status"] not in ("QUERY_READY", "FAILED", "CANCELLED", "CLEANED"):
            return
        # Do not clean an active attempt or transiently failed task: retries still need its bytes.
        if op.get("leaseUntil", 0) > time.time() and op["status"] == "CANCELLED":
            return
        keys = set(op.get("objectKeys", []))
        if op.get("sourceKey"):
            keys.add(op["sourceKey"])
        keys.update(self.storage.keys(f"derived/{job_id}/"))
        for key in keys:
            self.storage.delete(key)
        latest = self.s.operation(job_id)
        new = dict(latest, cleaned=True, cleanupStatus="COMPLETED", frames=[], objectKeys=[], expiresAt=latest.get("expiresAt", int(time.time())+86400))
        self.repo.transact([Write("operations", new, latest["_rev"])])

    def delete(self, job_id):
        op = self._claim(job_id, {"QUEUED"}, "DELETING", 115)
        if op is None:
            return
        media = self.repo.get("media", {"mediaId": op["mediaId"]})
        if media:
            if media["status"] != "DELETING" or media["generation"] != op["generation"]:
                return
            prior = self.repo.get("operations", {"operationId": media["jobId"]})
            if prior and prior.get("leaseUntil", 0) > time.time():
                # Wait until a cancelled producer cannot create more objects.
                raise RuntimeError("Waiting for the previous processing lease to expire")
            keys = set(media.get("objectKeys", []))
            keys.update(self.storage.keys(f"derived/{media['mediaId']}/"))
            for key in keys:
                self.storage.delete(key)
            dedup = self.repo.get("operations", {"operationId": f"DEDUP#{media['ownerSub']}#{media['sha256']}"})
            latest = self.s.operation(job_id)
            writes = [Write("media", media, media["_rev"], delete=True), Write("operations", dict(latest, status="DELETED", leaseUntil=0, expiresAt=int(time.time())+86400*14), latest["_rev"])]
            if dedup and dedup.get("mediaId") == media["mediaId"]:
                writes.append(Write("operations", dedup, dedup["_rev"], delete=True))
            if prior:
                writes.append(Write("operations", dict(prior, status="DELETED", frames=[], objectKeys=[], expiresAt=int(time.time())+86400*14), prior["_rev"]))
            self.repo.transact(writes)
        else:
            self.repo.transact([Write("operations", dict(op, status="DELETED", leaseUntil=0), op["_rev"])])

    def notify(self, payload):
        media = self.repo.get("media", {"mediaId": payload["entityId"]})
        if not media or media["status"] != "READY" or media["generation"] != payload["generation"] or media["recordVersion"] != payload.get("payload", {}).get("recordVersion"):
            return
        if self.s.notifier is None:
            raise RuntimeError("SNS is not configured")
        recipients = {}
        for subscription in self.repo.all("subscriptions"):
            if subscription["status"] != "ACTIVE" or not set(subscription["tags"]) & set(media["tags"]):
                continue
            principal = Principal(subscription["ownerSub"])
            if can_read(principal, media):
                recipients[subscription["ownerSub"]] = subscription
        for sub, subscription in recipients.items():
            key = f"DELIVERY#{sub}#{media['mediaId']}#{media['recordVersion']}"
            if self.repo.get("operations", {"operationId": key}):
                continue
            ledger = {"operationId": key, "type": "DELIVERY", "ownerSub": sub, "status": "SENDING", "createdAt": iso_time(), "expiresAt": int(time.time())+86400*14}
            try:
                self.repo.transact([Write("operations", ledger)])
            except Conflict:
                continue
            # Recheck current ACL immediately before publishing delayed notifications.
            current = self.repo.get("media", {"mediaId": media["mediaId"]})
            current_sub = self.repo.get("subscriptions", {"ownerSub": sub, "subscriptionId": subscription["subscriptionId"]})
            if not current or not current_sub or current_sub["status"] != "ACTIVE" or not can_read(Principal(sub), current) or current["status"] != "READY":
                state, message_id = "SKIPPED", None
            else:
                try:
                    message_id = self.s.notifier.publish(subscription["topicArn"], f"A matching sighting is available in Aussie EcoGuard.\nSign in to view it: {self.s.config.web_url.rstrip('/')}/media/{media['mediaId']}\n\nManage notifications in your subscription settings.")
                    state = "SENT"
                except Exception:
                    state, message_id = "UNKNOWN", None
            current_ledger = self.s.operation(key)
            self.repo.transact([Write("operations", dict(current_ledger, status=state, messageId=message_id), current_ledger["_rev"])])

    def dispatch(self, limit=100):
        dispatched = 0
        for item in self.repo.all("outbox"):
            if item["status"] != "PENDING":
                continue
            if dispatched >= limit:
                break
            if self.queue is None:
                raise RuntimeError("No queue dispatcher configured")
            self.queue.send(item["kind"], {k: item[k] for k in ("eventId", "kind", "entityId", "generation", "payload")})
            # Crash after send and before mark is safe: downstream actions are idempotent.
            try:
                self.repo.transact([Write("outbox", dict(item, status="SENT", sentAt=iso_time(), expiresAt=int(time.time())+86400*14), item["_rev"])])
            except Conflict:
                pass
            dispatched += 1
        return {"dispatched": dispatched}

    def cleanup(self):
        cleaned = 0
        for op in self.repo.all("operations"):
            if op.get("type") == "QUERY":
                if op.get("cleanupDeadline", float("inf")) <= time.time() and op["status"] not in ("FAILED", "CANCELLED", "QUERY_READY", "CLEANED"):
                    self._failure(op["operationId"], DomainError("QUERY_EXPIRED", "The query exceeded its retention period."))
                self.clean_query(op["operationId"])
                cleaned += 1
            elif op.get("type") == "UPLOAD" and op.get("status") == "UPLOADING" and op.get("uploadExpiresAt", float("inf")) + 3600 <= time.time():
                self._failure(op["operationId"], DomainError("UPLOAD_EXPIRED", "The upload was not completed. Delete the failed entry to release its reservation."))
        return {"checked": cleaned}


class AwsQueue:
    def __init__(self, config, session=None):
        import boto3
        self.client = (session or boto3.Session(region_name=config.region)).client("sqs")
        self.urls = {"INGEST": config.ingest_queue_url, "INFER": config.inference_queue_url, "DELETE": config.delete_queue_url, "NOTIFY": config.notification_queue_url}

    def send(self, kind, payload):
        if not self.urls.get(kind):
            raise RuntimeError(f"Queue for {kind} is not configured")
        self.client.send_message(QueueUrl=self.urls[kind], MessageBody=json.dumps(payload))


def message_jobs(record):
    payload = json.loads(record["body"])
    if "Records" in payload:
        for notification in payload["Records"]:
            if notification.get("eventSource") == "aws:s3":
                key = unquote_plus(notification["s3"]["object"]["key"])
                if key.startswith(("incoming/library/", "incoming/query/")):
                    yield {"kind": "INGEST", "entityId": key.rsplit("/", 1)[1]}
    elif payload.get("Event") == "s3:TestEvent":
        return
    else:
        yield payload
