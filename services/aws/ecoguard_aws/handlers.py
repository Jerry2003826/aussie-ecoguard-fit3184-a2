from __future__ import annotations
import json
from .runtime import runtime
from .api import handle
from .workers import message_jobs


def api(event, context):
    return handle(event, context, runtime()[0])


def _batch(event, context, kind):
    service, workers = runtime()
    failed = []
    for record in event.get("Records", []):
        try:
            for message in message_jobs(record):
                if kind == "notify":
                    workers.notify(message)
                else:
                    getattr(workers, kind)(message["entityId"])
        except Exception as error:
            print(json.dumps({"messageId": record.get("messageId"), "errorType": type(error).__name__, "worker": kind}))
            failed.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failed}


def ingest(event, context):
    return _batch(event, context, "ingest")


def infer(event, context):
    return _batch(event, context, "infer")


def delete(event, context):
    return _batch(event, context, "delete")


def notify(event, context):
    return _batch(event, context, "notify")


def outbox(event, context):
    return runtime()[1].dispatch()


def cleanup(event, context):
    return runtime()[1].cleanup()


def lex_fulfillment(event, context):
    from .lex_fulfillment import fulfill
    return fulfill(event, runtime()[0])
