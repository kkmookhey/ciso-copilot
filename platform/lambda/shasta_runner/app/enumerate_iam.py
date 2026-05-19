"""IAM enumeration — emits entities + contains edges for roles and users.

Used by `shasta_runner.main` to populate the entity graph with the IAM
inventory of the customer account *separately* from Shasta's finding-driven
emissions. This gives us a complete inventory even when no checks fire on
a particular role/user.

Pure-function module: takes a boto3 IAM client (already region-bound and
credentialed via the assumed-role session) and returns
`{"entities": [...], "edges": [...]}`.
"""
from __future__ import annotations

from typing import Any

from detectors.base import EdgeEmission, EntityEmission

_DETECTOR_ID      = "shasta_runner.iam"
_DETECTOR_VERSION = "0.1.0"


def enumerate_iam(iam_client, *, account_id: str, tenant_id: str) -> dict[str, list]:
    """Page through iam.list_roles + iam.list_users; emit entities + edges."""
    entities: list[EntityEmission] = []
    edges:    list[EdgeEmission]   = []

    account_nk = account_id

    # --- Roles -------------------------------------------------------------
    for role in _paginate(iam_client, "list_roles", "Roles"):
        arn  = role["Arn"]
        name = role["RoleName"]
        attrs: dict[str, Any] = {
            "service":       "iam",
            "account":       account_id,
            "resource_type": "role",
            "path":          role.get("Path") or "/",
        }
        if "CreateDate" in role:
            attrs["create_date"] = role["CreateDate"].isoformat() if hasattr(
                role["CreateDate"], "isoformat") else str(role["CreateDate"])
        entities.append(EntityEmission(
            tenant_id=tenant_id,
            kind="aws_iam_role",
            natural_key=arn,
            display_name=name,
            domain="cloud",
            attributes=attrs,
            evidence_packet=None,
            detector_id=_DETECTOR_ID,
            detector_version=_DETECTOR_VERSION,
        ))
        edges.append(EdgeEmission(
            tenant_id=tenant_id,
            source_kind="aws_account",
            source_natural_key=account_nk,
            target_kind="aws_iam_role",
            target_natural_key=arn,
            kind="contains",
            attributes={},
            evidence_packet={"version": "0.1", "via": "iam.list_roles"},
            detector_id=_DETECTOR_ID,
            detector_version=_DETECTOR_VERSION,
        ))

    # --- Users -------------------------------------------------------------
    for user in _paginate(iam_client, "list_users", "Users"):
        arn  = user["Arn"]
        name = user["UserName"]
        attrs = {
            "service":       "iam",
            "account":       account_id,
            "resource_type": "user",
            "path":          user.get("Path") or "/",
        }
        if "CreateDate" in user:
            attrs["create_date"] = user["CreateDate"].isoformat() if hasattr(
                user["CreateDate"], "isoformat") else str(user["CreateDate"])
        entities.append(EntityEmission(
            tenant_id=tenant_id,
            kind="aws_iam_user",
            natural_key=arn,
            display_name=name,
            domain="cloud",
            attributes=attrs,
            evidence_packet=None,
            detector_id=_DETECTOR_ID,
            detector_version=_DETECTOR_VERSION,
        ))
        edges.append(EdgeEmission(
            tenant_id=tenant_id,
            source_kind="aws_account",
            source_natural_key=account_nk,
            target_kind="aws_iam_user",
            target_natural_key=arn,
            kind="contains",
            attributes={},
            evidence_packet={"version": "0.1", "via": "iam.list_users"},
            detector_id=_DETECTOR_ID,
            detector_version=_DETECTOR_VERSION,
        ))

    return {"entities": entities, "edges": edges}


def _paginate(client, op_name: str, list_key: str):
    """Tiny pagination helper — yields items from `list_key`. Uses the
    boto3 paginator when available, else falls back to a manual loop."""
    if client.can_paginate(op_name):
        for page in client.get_paginator(op_name).paginate():
            yield from page.get(list_key, [])
        return
    # Fallback (shouldn't happen for iam.list_* but keeps Stubber tests
    # simple — they don't need to mock pagination tokens).
    resp = getattr(client, op_name)()
    yield from resp.get(list_key, [])
    while resp.get("IsTruncated") and resp.get("Marker"):
        resp = getattr(client, op_name)(Marker=resp["Marker"])
        yield from resp.get(list_key, [])
