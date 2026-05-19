"""Tests for the IAM enumeration helper."""
from __future__ import annotations

from datetime import datetime, timezone

import boto3
from botocore.stub import Stubber


def _now():
    return datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_iam_enumeration_emits_roles_users_and_contains_edges():
    from enumerate_iam import enumerate_iam

    iam = boto3.client("iam", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(iam)

    stub.add_response(
        "list_roles",
        {
            "Roles": [{
                "Path":       "/",
                "RoleName":   "AdminRole",
                "RoleId":     "AROAEXAMPLEAAAAAAAAA",
                "Arn":        "arn:aws:iam::123456789012:role/AdminRole",
                "CreateDate": _now(),
            }],
            "IsTruncated": False,
        },
    )
    stub.add_response(
        "list_users",
        {
            "Users": [{
                "Path":       "/",
                "UserName":   "alice",
                "UserId":     "AIDAEXAMPLEAAAAAAAAA",
                "Arn":        "arn:aws:iam::123456789012:user/alice",
                "CreateDate": _now(),
            }],
            "IsTruncated": False,
        },
    )
    stub.activate()

    out = enumerate_iam(iam, account_id="123456789012", tenant_id="tnt-1")

    assert "entities" in out and "edges" in out
    kinds = sorted(e.kind for e in out["entities"])
    assert kinds == ["aws_iam_role", "aws_iam_user"]

    role = next(e for e in out["entities"] if e.kind == "aws_iam_role")
    assert role.natural_key == "arn:aws:iam::123456789012:role/AdminRole"
    assert role.display_name == "AdminRole"
    assert role.domain == "cloud"
    assert role.attributes["resource_type"] == "role"
    assert role.tenant_id == "tnt-1"

    user = next(e for e in out["entities"] if e.kind == "aws_iam_user")
    assert user.natural_key == "arn:aws:iam::123456789012:user/alice"
    assert user.display_name == "alice"

    assert len(out["edges"]) == 2
    for edge in out["edges"]:
        assert edge.kind == "contains"
        assert edge.source_kind == "aws_account"
        assert edge.source_natural_key == "123456789012"
        assert edge.target_kind in ("aws_iam_role", "aws_iam_user")
        assert edge.detector_id == "shasta_runner.iam"


def test_iam_enumeration_empty_account():
    from enumerate_iam import enumerate_iam

    iam = boto3.client("iam", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(iam)
    stub.add_response("list_roles", {"Roles": [], "IsTruncated": False})
    stub.add_response("list_users", {"Users": [], "IsTruncated": False})
    stub.activate()

    out = enumerate_iam(iam, account_id="111111111111", tenant_id="tnt-1")
    assert out == {"entities": [], "edges": []}
