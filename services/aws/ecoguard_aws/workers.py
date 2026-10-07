from __future__ import annotations
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote_plus

from ecoguard_domain import DomainError, Principal, can_read, effective_tags
from ecoguard_domain.rules import normalize_tags, iso_time, cosine
from .repository import Write, Conflict
from .service import event

INFERENCE_LEASE_SECONDS = 600  # Lambda execution limit is 480 seconds.
SHORT_WORKER_LEASE_SECONDS = 150  # Ingest/delete Lambdas have a 120-second limit.


class Workers:
    def __init__(self, service, preprocessor=None, queue=None):
        self.s = service
        self.repo, self.storage = service.repo, service.storage
        self.preprocessor, self.queue = preprocessor, queue

    def _claim(self, job_id, allowed, state, lease=INFERENCE_LEASE_SECONDS):
        op = self.s.operation(job_id)
        if op["status"] == state and op.get("leaseUntil", 0) > time.time():
            raise RuntimeError("The task is already leased")
        if op["status"] not in allowed and op["status"] != state:
            return None
        new = dict(op, status=state, attemptId=op.get("attemptId", 0)+1, leaseUntil=int(time.time())+lease, stage=state)
        self.repo.transact([Write("operations", new, op["_rev"])])
        return self.s.operation(job_id)

    def _failure(self, job_id, error, expected=None):
        op = self.s.operation(job_id)
        if expected is not None and op["_rev"] != expected["_rev"]:
            return
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
        op = self._claim(job_id, {"QUEUED", "QUERY_QUEUED"}, "PREPROCESSING", SHORT_WORKER_LEASE_SECONDS)
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
        artifacts = None
        try:
            # Image and description share one deadline rather than two full budgets.
            deadline_at = (datetime.now(timezone.utc)+timedelta(seconds=330)).isoformat()
            if op.get("description") and not op.get("frames"):
                feature = self._description_feature(op, deadline_at)
                detection = {"features": [feature], "tags": [], "counts": {}, "manifestVersion": op["manifestVersion"]}
            else:
                frames = [{**{k: v for k, v in frame.items() if k != "key"}, "url": self.storage.sign_read(frame["key"], 900)} for frame in op["frames"]]
                payload = {"jobId": job_id, "ownerSub": op["ownerSub"], "generation": op["generation"], "manifestVersion": op["manifestVersion"], "mediaType": op["fileType"], "frames": frames, "deadlineAt": deadline_at}
                detection = self.s.inference.infer(payload)
            if detection.get("manifestVersion") != op["manifestVersion"]:
                raise DomainError("MODEL_VERSION_MISMATCH", "Inference returned the wrong model manifest.", 502)
            if op.get("queryType") == "pet" and op.get("description") and op.get("frames"):
                text_feature = self._description_feature(op, deadline_at)
                features = detection.get("features", [])
                if not features:
                    raise DomainError("NO_PET_FEATURES", "No pet could be matched in the query image. Try a clearer photo or search by description alone.")
                if any(f.get("featureVersion") != text_feature["featureVersion"] for f in features):
                    raise DomainError("FEATURE_VERSION_MISMATCH", "The photo and description use incompatible embedding versions.", 502)
                detection["descriptionFeature"] = text_feature
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
            # Isolate attempts: an expired producer must not overwrite or remove a winner.
            feature_key = f"derived/{old['mediaId']}/{op['generation']}/inference-{op['attemptId']}/features.json"
            target = f"library/{classification}/{old['ownerSub']}/{old['mediaId']}/{op['generation']}/{op['attemptId']}/original"
            # Register before S3 side effects. The scheduled cleaner can recover a hard
            # crash, or a failed compensating delete, without depending on this process.
            artifacts = {"operationId": f"ARTIFACTS#{job_id}#{op['generation']}#{op['attemptId']}", "type": "ARTIFACTS", "status": "PENDING", "ownerSub": op["ownerSub"], "mediaId": old["mediaId"], "producerId": job_id, "producerRev": op["_rev"], "generation": op["generation"], "attemptId": op["attemptId"], "leaseUntil": op["leaseUntil"], "objectKeys": [feature_key, target]}
            self.repo.transact([Write("operations", artifacts)])
            self.storage.put(feature_key, json.dumps({"features": detection.get("features", [])}).encode(), "application/json")
            self.storage.copy(op["sourceKey"], target)
            new = dict(old, status="READY", autoTags=tags, autoCounts=counts, originalKey=target, featureKey=feature_key, classification=classification, modelVersion=op["manifestVersion"], recordVersion=old["recordVersion"]+1, knownUrlAliases=list(set(old.get("knownUrlAliases", []) + [old["originalKey"]])), objectKeys=list(set(old.get("objectKeys", []) + [feature_key, target])))
            new["tags"], new["counts"] = effective_tags(new)
            finished = dict(op, status="READY", leaseUntil=0, completedAt=iso_time())
            self.repo.transact([Write("media", new, old["_rev"]), Write("operations", finished, op["_rev"]), Write("operations", artifacts, 1, delete=True), *self.s.index_writes(old, new), Write("outbox", event("NOTIFY", old["mediaId"], old["generation"], event_id=f"NOTIFY:{old['mediaId']}:{new['recordVersion']}", recordVersion=new["recordVersion"]))])
            self.storage.delete(op["sourceKey"])
        except Conflict:
            # A cancelled or superseded producer is finished, not a retry of its old writes.
            self._failure(job_id, DomainError("RECORD_CHANGED", "The record changed during processing. Retry this upload."), expected=op)
        except DomainError as error:
            self._failure(job_id, error, expected=op)
        finally:
            if artifacts:
                self._clean_artifacts(artifacts, producer_finished=True)

    def _clean_artifacts(self, artifacts, *, producer_finished=False):
        current = self.repo.get("operations", {"operationId": artifacts["operationId"]})
        if not current:
            if not producer_finished:
                return True
            media = self.repo.get("media", {"mediaId": artifacts["mediaId"]})
            if media and set(artifacts["objectKeys"]) <= set(media.get("objectKeys", [])):
                return True  # Publication won, possibly with an unknown transaction response.
            # A previous sweep may have run before an in-flight copy returned. Restore
            # the durable record before compensating those final, attempt-scoped writes.
            try:
                self.repo.transact([Write("operations", artifacts)])
            except Conflict:
                pass
            current = self.repo.get("operations", {"operationId": artifacts["operationId"]})
            if not current:
                return True  # A concurrent cleaner already finished after this producer.
        if not producer_finished and current["leaseUntil"] > time.time():
            return False
        producer = self.repo.get("operations", {"operationId": current["producerId"]})
        if producer and producer["_rev"] == current["producerRev"]:
            # Also fence an uncertain transaction outcome before compensation: either
            # publication wins (and its referenced bytes survive), or this fence wins.
            self._failure(producer["operationId"], DomainError("PROCESSING_INTERRUPTED", "Processing stopped before publication. Retry this upload."), expected=producer)
            producer = self.repo.get("operations", {"operationId": current["producerId"]})
            if producer and producer["_rev"] == current["producerRev"]:
                return False
        media = self.repo.get("media", {"mediaId": current["mediaId"]})
        referenced = set(media.get("objectKeys", [])) if media else set()
        for key in set(current["objectKeys"]) - referenced:
            self.storage.delete(key)
        try:
            self.repo.transact([Write("operations", current, current["_rev"], delete=True)])
        except Conflict:
            pass  # Another cleaner already removed this immutable cleanup record.
        return True

    def _deleted_object_keys(self, media, generation):
        keys = set(media.get("objectKeys", []))
        prefixes = [f"derived/{media['mediaId']}/"]
        prefixes += [f"library/{category}/{media['ownerSub']}/{media['mediaId']}/" for category in ("pet", "invasive", "mixed", "uncertain")]
        for prefix in prefixes:
            for key in self.storage.keys(prefix):
                part = key[len(prefix):].split("/", 1)[0]
                # New writers always include generation. Retain newer generations;
                # the old unversioned archive name is covered for existing media.
                if (part.isdigit() and int(part) < generation) or part == "original":
                    keys.add(key)
        return keys

    def _description_feature(self, op, deadline_at):
        result = self.s.inference.embed({"jobId": op["operationId"], "manifestVersion": op["manifestVersion"], "text": op["description"], "deadlineAt": deadline_at})
        if result.get("manifestVersion") != op["manifestVersion"]:
            raise DomainError("MODEL_VERSION_MISMATCH", "Text inference returned the wrong model manifest.", 502)
        return {"vector": result["vector"], "featureVersion": result["featureVersion"]}

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
            description_feature = detection.get("descriptionFeature")
            items = []
            for media in self.s._candidates(principal, public_only=True):
                if (op.get("from") and media["observedAt"] < op["from"]) or (op.get("to") and media["observedAt"] > op["to"]) or not media.get("featureKey"):
                    continue
                stored = json.loads(self.storage.read(media["featureKey"], 2*1024**2)).get("features", [])
                # Both modalities must score the same candidate pet crop. Averaging
                # prevents a strong photo score from silently overriding the text.
                def similarity(query, target):
                    image_score = cosine(query["vector"], target["vector"])
                    return (image_score + cosine(description_feature["vector"], target["vector"]))/2 if description_feature else image_score
                score = max((similarity(q, target) for q in query_features for target in stored if q.get("featureVersion") == target.get("featureVersion") and (not q.get("species") or q.get("species") == target.get("species"))), default=-1)
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
            return False
        # Do not clean an active attempt or transiently failed task: retries still need its bytes.
        if op.get("leaseUntil", 0) > time.time() and op["status"] == "CANCELLED":
            return False
        keys = set(op.get("objectKeys", []))
        if op.get("sourceKey"):
            keys.add(op["sourceKey"])
        keys.update(self.storage.keys(f"derived/{job_id}/"))
        for key in keys:
            self.storage.delete(key)
        latest = self.s.operation(job_id)
        new = dict(latest, cleaned=True, cleanupStatus="COMPLETED", frames=[], objectKeys=[], expiresAt=latest.get("expiresAt", int(time.time())+86400))
        self.repo.transact([Write("operations", new, latest["_rev"])])
        return True

    def delete(self, job_id):
        op = self._claim(job_id, {"QUEUED"}, "DELETING", SHORT_WORKER_LEASE_SECONDS)
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
            try:
                for key in self._deleted_object_keys(media, op["generation"]):
                    self.storage.delete(key)
            except Exception:
                self._failure(job_id, DomainError("DELETE_FAILED", "Some stored objects could not be deleted. Retry this deletion."), expected=op)
                raise
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
        checked = cleaned = 0
        failures = []
        for op in self.repo.all("operations"):
            try:
                if op.get("type") == "ARTIFACTS":
                    checked += 1
                    cleaned += bool(self._clean_artifacts(op))
                elif op.get("type") == "QUERY":
                    checked += 1
                    if op.get("cleanupDeadline", float("inf")) <= time.time() and op["status"] not in ("FAILED", "CANCELLED", "QUERY_READY", "CLEANED"):
                        self._failure(op["operationId"], DomainError("QUERY_EXPIRED", "The query exceeded its retention period."))
                    cleaned += bool(self.clean_query(op["operationId"]))
                elif op.get("type") == "UPLOAD" and op.get("status") == "UPLOADING" and op.get("uploadExpiresAt", float("inf")) + 3600 <= time.time():
                    checked += 1
                    self._failure(op["operationId"], DomainError("UPLOAD_EXPIRED", "The upload was not completed. Delete the failed entry to release its reservation."))
            except Exception as error:
                # Keep the durable record and continue; one broken object must not
                # prevent later artifacts or temporary queries from being removed.
                failures.append({"operationId": op["operationId"], "errorType": type(error).__name__})
        if failures:
            print(json.dumps({"cleanupFailed": len(failures), "failures": failures}))
        return {"checked": checked, "cleaned": cleaned, "failed": len(failures), "failures": failures}


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
