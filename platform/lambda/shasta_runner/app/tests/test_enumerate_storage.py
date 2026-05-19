"""Tests for the S3 storage enumeration helper."""
from __future__ import annotations

from datetime import datetime, timezone

import boto3
from botocore.stub import Stubber


def _now():
    return datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_storage_enumeration_two_buckets():
    from enumerate_storage import enumerate_storage

    s3 = boto3.client("s3", region_name="us-east-1",
                      aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(s3)
    stub.add_response(
        "list_buckets",
        {
            "Buckets": [
                {"Name": "bucket-one", "CreationDate": _now()},
                {"Name": "bucket-two", "CreationDate": _now()},
            ],
            "Owner": {"DisplayName": "owner", "ID": "abc"},
        },
    )
    # Region lookups
    stub.add_response("get_bucket_location",
                      {"LocationConstraint": "us-west-2"},
                      {"Bucket": "bucket-one"})
    stub.add_response("get_bucket_location",
                      {"LocationConstraint": ""},
                      {"Bucket": "bucket-two"})
    stub.activate()

    out = enumerate_storage(s3, account_id="123456789012", tenant_id="tnt-1")

    assert len(out["entities"]) == 2
    assert len(out["edges"]) == 2

    by_name = {e.display_name: e for e in out["entities"]}
    one = by_name["bucket-one"]
    assert one.kind == "aws_s3_bucket"
    assert one.natural_key == "arn:aws:s3:::bucket-one"
    assert one.domain == "cloud"
    assert one.attributes["region"] == "us-west-2"

    two = by_name["bucket-two"]
    assert two.attributes["region"] == "us-east-1"

    for edge in out["edges"]:
        assert edge.kind == "contains"
        assert edge.source_kind == "aws_account"
        assert edge.source_natural_key == "123456789012"
        assert edge.target_kind == "aws_s3_bucket"
        assert edge.detector_id == "shasta_runner.storage"


def test_storage_enumeration_region_lookup_failure_is_swallowed():
    """If get_bucket_location raises, the bucket is still emitted (no region)."""
    from enumerate_storage import enumerate_storage

    s3 = boto3.client("s3", region_name="us-east-1",
                      aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(s3)
    stub.add_response(
        "list_buckets",
        {"Buckets": [{"Name": "denied-bucket", "CreationDate": _now()}],
         "Owner": {"DisplayName": "o", "ID": "i"}},
    )
    stub.add_client_error("get_bucket_location", service_error_code="AccessDenied")
    stub.activate()

    out = enumerate_storage(s3, account_id="111", tenant_id="tnt-1")
    assert len(out["entities"]) == 1
    e = out["entities"][0]
    assert e.display_name == "denied-bucket"
    assert "region" not in e.attributes


def test_storage_enumeration_no_buckets():
    from enumerate_storage import enumerate_storage

    s3 = boto3.client("s3", region_name="us-east-1",
                      aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(s3)
    stub.add_response("list_buckets", {"Buckets": [], "Owner": {"DisplayName": "o", "ID": "i"}})
    stub.activate()

    out = enumerate_storage(s3, account_id="111", tenant_id="tnt-1")
    assert out == {"entities": [], "edges": []}
