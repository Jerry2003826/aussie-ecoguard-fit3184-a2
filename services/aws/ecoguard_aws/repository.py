"""Conditional document operations, with the same atomic contract in AWS and tests."""
from __future__ import annotations
import copy
import threading
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

KEYS = {"media": ("mediaId",), "operations": ("operationId",), "index": ("scopeSpecies", "observedAtMediaId"), "subscriptions": ("ownerSub", "subscriptionId"), "outbox": ("eventId",)}


class Conflict(Exception):
    pass


@dataclass
class Write:
    table: str
    item: dict
    expected: int | None = None  # None requires the item not to exist.
    delete: bool = False


def key_for(table: str, item: dict):
    return {name: item[name] for name in KEYS[table]}


class MemoryRepository:
    """In-memory repository for tests and the explicitly labelled local development server."""
    def __init__(self):
        self.data = {name: {} for name in KEYS}
        self.lock = threading.RLock()

    def _key(self, table, item):
        return tuple(item[name] for name in KEYS[table])

    def get(self, table, key):
        with self.lock:
            return copy.deepcopy(self.data[table].get(self._key(table, key)))

    def transact(self, writes: list[Write]):
        with self.lock:
            seen = set()
            for w in writes:
                key = self._key(w.table, w.item)
                if (w.table, key) in seen:
                    raise ValueError("Duplicate transaction item")
                seen.add((w.table, key))
                old = self.data[w.table].get(key)
                if (w.expected is None and old is not None) or (w.expected is not None and (old is None or old.get("_rev") != w.expected)):
                    raise Conflict()
            for w in writes:
                key = self._key(w.table, w.item)
                if w.delete:
                    self.data[w.table].pop(key, None)
                else:
                    self.data[w.table][key] = copy.deepcopy(dict(w.item, _rev=(w.expected or 0) + 1))

    def page(self, table, *, partition=None, start=None, limit=100):
        with self.lock:
            rows = sorted(self.data[table].values(), key=lambda r: self._key(table, r))
            if partition:
                rows = [r for r in rows if all(r.get(k) == v for k, v in partition.items())]
            if start:
                rows = [r for r in rows if self._key(table, r) > self._key(table, start)]
            chosen = rows[:limit]
            return copy.deepcopy(chosen), (key_for(table, chosen[-1]) if len(rows) > limit else None)

    def all(self, table, *, partition=None):
        start = None
        while True:
            rows, start = self.page(table, partition=partition, start=start)
            yield from rows
            if not start:
                return


def _decimal(value):
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _decimal(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_decimal(v) for v in value]
    return value


def _normal(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _normal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normal(v) for v in value]
    return value


class DynamoRepository:
    def __init__(self, config, session=None):
        import boto3
        from boto3.dynamodb.types import TypeSerializer, TypeDeserializer
        self.client = (session or boto3.Session(region_name=config.region)).client("dynamodb")
        self.tables = config.tables()
        self.serializer, self.deserializer = TypeSerializer(), TypeDeserializer()

    def _encode(self, value):
        return {k: self.serializer.serialize(v) for k, v in _decimal(value).items()}

    def _decode(self, value):
        return _normal({k: self.deserializer.deserialize(v) for k, v in value.items()})

    def get(self, table, key):
        result = self.client.get_item(TableName=self.tables[table], Key=self._encode(key), ConsistentRead=True)
        return self._decode(result["Item"]) if "Item" in result else None

    def transact(self, writes):
        if not writes:
            return
        if len(writes) > 100:
            raise ValueError("Transaction exceeds DynamoDB's 100-item limit")
        items = []
        for w in writes:
            condition = {"ConditionExpression": "attribute_not_exists(#pk)", "ExpressionAttributeNames": {"#pk": KEYS[w.table][0]}} if w.expected is None else {"ConditionExpression": "#rev = :rev", "ExpressionAttributeNames": {"#rev": "_rev"}, "ExpressionAttributeValues": self._encode({":rev": w.expected})}
            if w.delete:
                item = {"TableName": self.tables[w.table], "Key": self._encode(key_for(w.table, w.item)), **condition}
                items.append({"Delete": item})
            else:
                item = {"TableName": self.tables[w.table], "Item": self._encode(dict(w.item, _rev=(w.expected or 0) + 1)), **condition}
                items.append({"Put": item})
        try:
            self.client.transact_write_items(TransactItems=items)
        except self.client.exceptions.TransactionCanceledException as error:
            reasons = error.response.get("CancellationReasons", [])
            if any(r.get("Code") == "ConditionalCheckFailed" for r in reasons):
                raise Conflict() from error
            raise

    def page(self, table, *, partition=None, start=None, limit=100):
        args: dict[str, Any] = {"TableName": self.tables[table], "Limit": limit, "ConsistentRead": True}
        if start:
            args["ExclusiveStartKey"] = self._encode(start)
        if partition:
            key, value = next(iter(partition.items()))
            if key != KEYS[table][0] or len(partition) != 1:
                raise ValueError("Only primary partition queries are supported")
            args.update(KeyConditionExpression="#pk = :pk", ExpressionAttributeNames={"#pk": key}, ExpressionAttributeValues=self._encode({":pk": value}))
            result = self.client.query(**args)
        else:
            result = self.client.scan(**args)
        return [self._decode(i) for i in result.get("Items", [])], self._decode(result["LastEvaluatedKey"]) if result.get("LastEvaluatedKey") else None

    def all(self, table, *, partition=None):
        cursor = None
        while True:
            items, cursor = self.page(table, partition=partition, start=cursor)
            yield from items
            if not cursor:
                return
