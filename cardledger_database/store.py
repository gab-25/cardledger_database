"""Where the generator keeps its cache and published files: a local folder (tests, dry runs) or S3."""

import os
from pathlib import Path
from typing import Protocol

from .errors import SourceError


class Store(Protocol):
    def get(self, key: str) -> bytes | None: ...

    def put(self, key: str, data: bytes) -> None: ...


class LocalStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def get(self, key: str) -> bytes | None:
        path = self.root / key
        return path.read_bytes() if path.is_file() else None

    def put(self, key: str, data: bytes) -> None:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


class S3Store:
    def __init__(self, bucket: str, endpoint: str | None = None, client=None):
        import boto3

        self.bucket = bucket
        self.client = client or boto3.client("s3", endpoint_url=endpoint)

    def get(self, key: str) -> bytes | None:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.client.exceptions.NoSuchKey:
            return None

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)


def store_from_env() -> Store:
    bucket = os.environ.get("CARDLEDGER_S3_BUCKET")
    if not bucket:
        raise SourceError("CARDLEDGER_S3_BUCKET is not set: no store to keep the cache and the files in")
    return S3Store(bucket, os.environ.get("CARDLEDGER_S3_ENDPOINT") or None)
