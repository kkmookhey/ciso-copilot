"""Refreshable assumed-role credentials."""
from datetime import datetime, timedelta, timezone

import boto3
from botocore.stub import Stubber

from assumed_role import build_refreshable_credentials, session_from_credentials


def _sts_stub():
    sts = boto3.client("sts", region_name="us-east-1",
                        aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(sts)
    stub.add_response("assume_role", {
        "Credentials": {
            "AccessKeyId": "AKIAEXAMPLE000001",
            "SecretAccessKey": "secret",
            "SessionToken": "token",
            "Expiration": datetime.now(timezone.utc) + timedelta(hours=1),
        },
    })
    stub.activate()
    return sts


def test_build_refreshable_credentials_assumes_the_role():
    creds = build_refreshable_credentials(
        _sts_stub(), "arn:aws:iam::111:role/CISOCopilotReader", "ext-1")
    frozen = creds.get_frozen_credentials()
    assert frozen.access_key == "AKIAEXAMPLE000001"
    assert frozen.token == "token"


def test_session_from_credentials_targets_the_region():
    creds = build_refreshable_credentials(
        _sts_stub(), "arn:aws:iam::111:role/CISOCopilotReader", "ext-1")
    session = session_from_credentials(creds, "eu-west-1")
    assert session.region_name == "eu-west-1"
