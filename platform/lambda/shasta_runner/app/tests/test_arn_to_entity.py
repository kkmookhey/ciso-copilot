"""Tests for the ARN parser."""
from __future__ import annotations


def test_s3_bucket():
    from arn_to_entity import parse_arn
    out = parse_arn("arn:aws:s3:::my-bucket")
    assert out is not None
    assert out["kind"] == "aws_s3_bucket"
    assert out["natural_key"] == "arn:aws:s3:::my-bucket"
    assert out["display_name"] == "my-bucket"
    assert out["attributes"]["service"] == "s3"


def test_iam_role():
    from arn_to_entity import parse_arn
    out = parse_arn("arn:aws:iam::123456789012:role/Foo")
    assert out is not None
    assert out["kind"] == "aws_iam_role"
    assert out["display_name"] == "Foo"
    assert out["attributes"]["account"] == "123456789012"
    assert out["attributes"]["resource_type"] == "role"


def test_iam_user():
    from arn_to_entity import parse_arn
    out = parse_arn("arn:aws:iam::123456789012:user/bob")
    assert out["kind"] == "aws_iam_user"
    assert out["display_name"] == "bob"


def test_ec2_instance():
    from arn_to_entity import parse_arn
    out = parse_arn("arn:aws:ec2:us-east-1:123456789012:instance/i-abc123")
    assert out["kind"] == "aws_ec2_instance"
    assert out["attributes"]["region"] == "us-east-1"
    assert out["display_name"] == "i-abc123"


def test_vpc():
    from arn_to_entity import parse_arn
    out = parse_arn("arn:aws:ec2:us-east-1:123:vpc/vpc-abc")
    assert out["kind"] == "aws_vpc"
    assert out["display_name"] == "vpc-abc"


def test_lambda_function():
    from arn_to_entity import parse_arn
    out = parse_arn("arn:aws:lambda:us-east-1:123:function:ciso-copilot-shasta-runner")
    assert out["kind"] == "aws_lambda_function"
    assert out["display_name"] == "ciso-copilot-shasta-runner"


def test_unknown_service_returns_none():
    from arn_to_entity import parse_arn
    assert parse_arn("arn:aws:weird:::xyz") is None


def test_not_an_arn_returns_none():
    from arn_to_entity import parse_arn
    assert parse_arn("not-an-arn") is None
    assert parse_arn("") is None
    assert parse_arn(None) is None
