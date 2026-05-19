"""Tests for the network enumeration helper."""
from __future__ import annotations

import boto3
from botocore.stub import Stubber


def test_network_enumeration_vpc_subnet_sg_with_contains_edges():
    from enumerate_network import enumerate_network

    ec2 = boto3.client("ec2", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(ec2)

    stub.add_response(
        "describe_vpcs",
        {"Vpcs": [{
            "VpcId":     "vpc-aaa",
            "CidrBlock": "10.0.0.0/16",
            "IsDefault": False,
        }]},
    )
    stub.add_response(
        "describe_subnets",
        {"Subnets": [{
            "SubnetId":         "subnet-bbb",
            "VpcId":            "vpc-aaa",
            "CidrBlock":        "10.0.1.0/24",
            "AvailabilityZone": "us-east-1a",
        }]},
    )
    stub.add_response(
        "describe_security_groups",
        {"SecurityGroups": [{
            "GroupId":   "sg-ccc",
            "GroupName": "default",
            "VpcId":     "vpc-aaa",
        }]},
    )
    stub.activate()

    out = enumerate_network(
        ec2,
        account_id="123456789012",
        tenant_id="tnt-1",
        region="us-east-1",
    )

    kinds = sorted(e.kind for e in out["entities"])
    assert kinds == ["aws_security_group", "aws_subnet", "aws_vpc"]

    vpc = next(e for e in out["entities"] if e.kind == "aws_vpc")
    assert vpc.natural_key == "arn:aws:ec2:us-east-1:123456789012:vpc/vpc-aaa"
    assert vpc.attributes["cidr_block"] == "10.0.0.0/16"

    sub = next(e for e in out["entities"] if e.kind == "aws_subnet")
    assert sub.natural_key == "arn:aws:ec2:us-east-1:123456789012:subnet/subnet-bbb"
    assert sub.attributes["vpc_id"] == "vpc-aaa"

    sg = next(e for e in out["entities"] if e.kind == "aws_security_group")
    assert sg.display_name == "default"
    assert sg.natural_key == "arn:aws:ec2:us-east-1:123456789012:security-group/sg-ccc"

    # Edges: account→vpc, vpc→subnet, vpc→sg = 3
    assert len(out["edges"]) == 3
    edge_specs = sorted(
        (e.source_kind, e.target_kind, e.kind) for e in out["edges"]
    )
    assert edge_specs == sorted([
        ("aws_account", "aws_vpc",            "contains"),
        ("aws_vpc",     "aws_subnet",         "contains"),
        ("aws_vpc",     "aws_security_group", "contains"),
    ])


def test_network_enumeration_subnet_without_vpc_omits_edge():
    """If a subnet has no VpcId, no vpc→subnet edge is emitted (defensive)."""
    from enumerate_network import enumerate_network

    ec2 = boto3.client("ec2", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(ec2)
    stub.add_response("describe_vpcs", {"Vpcs": []})
    stub.add_response("describe_subnets",
                      {"Subnets": [{"SubnetId": "subnet-x", "CidrBlock": "10.0.0.0/24"}]})
    stub.add_response("describe_security_groups", {"SecurityGroups": []})
    stub.activate()

    out = enumerate_network(ec2, account_id="111", tenant_id="t", region="us-east-1")
    assert len(out["entities"]) == 1
    assert out["edges"] == []
