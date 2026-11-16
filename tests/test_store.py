from botocore.stub import Stubber
import boto3

from cardledger_database.store import LocalStore, S3Store, store_from_env
import pytest

from cardledger_database.errors import SourceError


def test_local_store_roundtrip(tmp_path):
    store = LocalStore(tmp_path)
    store.put("cache/a/b.json", b"hello")
    assert store.get("cache/a/b.json") == b"hello"


def test_local_store_missing_key_is_none(tmp_path):
    assert LocalStore(tmp_path).get("nope") is None


def test_s3_store_missing_key_is_none():
    client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="y")
    with Stubber(client) as stub:
        stub.add_client_error("get_object", service_error_code="NoSuchKey")
        assert S3Store("bucket", client=client).get("k") is None


def test_store_from_env_requires_bucket(monkeypatch):
    monkeypatch.delenv("CARDLEDGER_S3_BUCKET", raising=False)
    with pytest.raises(SourceError):
        store_from_env()
