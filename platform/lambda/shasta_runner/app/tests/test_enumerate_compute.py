"""Tests for the EC2 + Lambda compute enumeration helper."""
from __future__ import annotations

from datetime import datetime, timezone

import boto3
from botocore.stub import Stubber


def _now():
    return datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_compute_enumeration_ec2_and_lambda_with_assumes_edges():
    from enumerate_compute import enumerate_compute

    ec2 = boto3.client("ec2", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    lam = boto3.client("lambda", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    ec2_stub = Stubber(ec2)
    lam_stub = Stubber(lam)

    ec2_stub.add_response(
        "describe_instances",
        {
            "Reservations": [{
                "ReservationId": "r-1",
                "OwnerId":       "123456789012",
                "Instances": [{
                    "InstanceId":   "i-aaaa1111",
                    "InstanceType": "t3.micro",
                    "State":        {"Code": 16, "Name": "running"},
                    "VpcId":        "vpc-xyz",
                    "IamInstanceProfile": {
                        "Arn": "arn:aws:iam::123456789012:instance-profile/webrole-profile",
                        "Id":  "AIPAEXAMPLEAAAAAAAAA",
                    },
                    "Placement": {"AvailabilityZone": "us-east-1a"},
                }],
            }],
        },
    )
    lam_stub.add_response(
        "list_functions",
        {
            "Functions": [{
                "FunctionName": "my-fn",
                "FunctionArn":  "arn:aws:lambda:us-east-1:123456789012:function:my-fn",
                "Runtime":      "python3.12",
                "Role":         "arn:aws:iam::123456789012:role/my-fn-role",
                "Handler":      "main.handler",
                "MemorySize":   512,
            }],
        },
    )
    ec2_stub.activate()
    lam_stub.activate()

    out = enumerate_compute(
        ec2, lam,
        account_id="123456789012",
        tenant_id="tnt-1",
        region="us-east-1",
    )

    # Entities
    kinds = sorted(e.kind for e in out["entities"])
    assert kinds == ["aws_ec2_instance", "aws_lambda_function"]

    ec2_ent = next(e for e in out["entities"] if e.kind == "aws_ec2_instance")
    assert ec2_ent.natural_key == "arn:aws:ec2:us-east-1:123456789012:instance/i-aaaa1111"
    assert ec2_ent.display_name == "i-aaaa1111"
    assert ec2_ent.attributes["region"] == "us-east-1"
    assert ec2_ent.attributes["state"] == "running"

    lam_ent = next(e for e in out["entities"] if e.kind == "aws_lambda_function")
    assert lam_ent.natural_key == "arn:aws:lambda:us-east-1:123456789012:function:my-fn"
    assert lam_ent.attributes["runtime"] == "python3.12"

    # Edges: 2 contains (account→ec2, account→lambda) + 2 assumes
    assumes = [e for e in out["edges"] if e.kind == "assumes"]
    contains = [e for e in out["edges"] if e.kind == "contains"]
    assert len(contains) == 2
    assert len(assumes) == 2

    # EC2 assumes — natural_key is the instance-profile ARN
    ec2_assumes = next(
        e for e in assumes
        if e.source_kind == "aws_ec2_instance"
    )
    assert ec2_assumes.target_kind == "aws_iam_role"
    assert ec2_assumes.target_natural_key == \
        "arn:aws:iam::123456789012:instance-profile/webrole-profile"

    # Lambda assumes — natural_key IS the role ARN
    lam_assumes = next(
        e for e in assumes
        if e.source_kind == "aws_lambda_function"
    )
    assert lam_assumes.target_kind == "aws_iam_role"
    assert lam_assumes.target_natural_key == \
        "arn:aws:iam::123456789012:role/my-fn-role"


def test_compute_enumeration_no_assumes_when_role_missing():
    from enumerate_compute import enumerate_compute

    ec2 = boto3.client("ec2", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    lam = boto3.client("lambda", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    ec2_stub = Stubber(ec2)
    lam_stub = Stubber(lam)

    # EC2 instance without instance profile
    ec2_stub.add_response(
        "describe_instances",
        {"Reservations": [{
            "ReservationId": "r-1",
            "OwnerId":       "111",
            "Instances": [{
                "InstanceId":   "i-noprofile1",
                "InstanceType": "t3.nano",
                "State":        {"Code": 16, "Name": "running"},
            }],
        }]},
    )
    lam_stub.add_response("list_functions", {"Functions": []})

    ec2_stub.activate()
    lam_stub.activate()

    out = enumerate_compute(
        ec2, lam,
        account_id="111",
        tenant_id="tnt-1",
        region="us-east-1",
    )
    assert not any(e.kind == "assumes" for e in out["edges"])
    assert len(out["entities"]) == 1
