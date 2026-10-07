from __future__ import annotations
import base64
import hashlib
import time
import uuid
from urllib.parse import quote

from ecoguard_domain import DomainError


def checksum_header(sha256: str) -> str:
    return base64.b64encode(bytes.fromhex(sha256)).decode()


class MemoryStorage:
    def __init__(self, config):
        self.config, self.objects, self.uploads = config, {}, {}

    def sign_upload(self, key, content_type, sha256, size, expires=None):
        expires = self.config.upload_url_seconds if expires is None else expires
        token = uuid.uuid4().hex
        self.uploads[token] = {"key": key, "contentType": content_type, "sha256": sha256, "size": size, "expires": time.time() + expires}
        return {"uploadUrl": f"{self.config.api_url}/_dev/uploads/{token}", "headers": {"Content-Type": content_type, "x-amz-checksum-sha256": checksum_header(sha256)}, "expiresIn": expires}

    def accept_upload(self, token, data):
        info = self.uploads.get(token)
        if not info or info["expires"] < time.time():
            raise DomainError("UPLOAD_EXPIRED", "Upload authorization expired.", 403)
        if len(data) != info["size"] or hashlib.sha256(data).hexdigest() != info["sha256"]:
            raise DomainError("CHECKSUM_MISMATCH", "Uploaded bytes do not match the declared size and checksum.")
        self.put(info["key"], data, info["contentType"])
        return info["key"]

    def put(self, key, data, content_type="application/octet-stream"):
        self.objects[key] = {"data": data, "contentType": content_type}

    def head(self, key):
        item = self.objects.get(key)
        if item is None:
            raise DomainError("OBJECT_NOT_FOUND", "The uploaded object does not exist.", 404)
        return {"size": len(item["data"]), "sha256": hashlib.sha256(item["data"]).hexdigest(), "contentType": item["contentType"]}

    def read(self, key, max_bytes=104857600):
        self.head(key)
        data = self.objects[key]["data"]
        if len(data) > max_bytes:
            raise DomainError("FILE_TOO_LARGE", "The object exceeds the processing limit.", 413)
        return data

    def delete(self, key):
        self.objects.pop(key, None)

    def copy(self, source, target):
        self.objects[target] = dict(self.objects[source])

    def keys(self, prefix):
        return sorted(k for k in self.objects if k.startswith(prefix))

    def sign_read(self, key, expires=60):
        token = uuid.uuid4().hex
        self.uploads[token] = {"readKey": key, "expires": time.time() + expires}
        return f"{self.config.api_url}/_dev/objects/{token}"

    def resolve_read(self, token):
        info = self.uploads.get(token)
        if not info or "readKey" not in info or info["expires"] < time.time():
            raise DomainError("LINK_EXPIRED", "This development download link has expired.", 403)
        key = info["readKey"]
        return self.read(key), self.head(key)["contentType"]


class S3Storage:
    def __init__(self, config, session=None):
        import boto3
        from botocore.config import Config
        self.config = config
        self.client = (session or boto3.Session(region_name=config.region)).client("s3", config=Config(signature_version="s3v4", retries={"max_attempts": 3}))
        self.bucket = config.media_bucket

    def sign_upload(self, key, content_type, sha256, size, expires=None):
        expires = self.config.upload_url_seconds if expires is None else expires
        params = {"Bucket": self.bucket, "Key": key, "ContentType": content_type, "ChecksumSHA256": checksum_header(sha256), "ContentLength": size}
        return {"uploadUrl": self.client.generate_presigned_url("put_object", Params=params, ExpiresIn=expires), "headers": {"Content-Type": content_type, "x-amz-checksum-sha256": checksum_header(sha256)}, "expiresIn": expires}

    def head(self, key):
        try:
            h = self.client.head_object(Bucket=self.bucket, Key=key, ChecksumMode="ENABLED")
        except self.client.exceptions.ClientError as error:
            if str(error.response.get("Error", {}).get("Code")) in ("404", "NoSuchKey", "NotFound"):
                raise DomainError("OBJECT_NOT_FOUND", "The uploaded object does not exist.", 404) from error
            raise
        return {"size": h["ContentLength"], "sha256": base64.b64decode(h["ChecksumSHA256"]).hex() if h.get("ChecksumSHA256") else None, "contentType": h.get("ContentType"), "versionId": h.get("VersionId")}

    def read(self, key, max_bytes=104857600):
        if self.head(key)["size"] > max_bytes:
            raise DomainError("FILE_TOO_LARGE", "The object exceeds the processing limit.", 413)
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        try:
            data = response["Body"].read(max_bytes + 1)
        finally:
            response["Body"].close()
        if len(data) > max_bytes:
            raise DomainError("FILE_TOO_LARGE", "The object exceeds the processing limit.", 413)
        return data

    def put(self, key, data, content_type="application/octet-stream"):
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type, ChecksumSHA256=base64.b64encode(hashlib.sha256(data).digest()).decode())

    def copy(self, source, target):
        self.client.copy_object(Bucket=self.bucket, Key=target, CopySource={"Bucket": self.bucket, "Key": source}, ChecksumAlgorithm="SHA256")

    def delete(self, key):
        # Delete every exact object version, not only a new delete marker.
        for page in self.client.get_paginator("list_object_versions").paginate(Bucket=self.bucket, Prefix=key):
            objects = [{"Key": i["Key"], "VersionId": i["VersionId"]} for group in ("Versions", "DeleteMarkers") for i in page.get(group, []) if i["Key"] == key]
            for i in range(0, len(objects), 1000):
                response = self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": objects[i:i+1000], "Quiet": True})
                if response.get("Errors"):
                    raise RuntimeError("S3 did not delete all object versions")
        self.client.delete_object(Bucket=self.bucket, Key=key)
        # With versioning enabled the last delete creates a marker; remove that marker too.
        for page in self.client.get_paginator("list_object_versions").paginate(Bucket=self.bucket, Prefix=key):
            for marker in page.get("DeleteMarkers", []):
                if marker["Key"] == key:
                    self.client.delete_object(Bucket=self.bucket, Key=key, VersionId=marker["VersionId"])

    def keys(self, prefix):
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                yield item["Key"]

    def sign_read(self, key, expires=60):
        return self.client.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires)
