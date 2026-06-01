"""DDB-backed idempotency for autonomous broadcast.

seen?(tenant, finding, scan) → bool — was this exact tuple broadcast already?
mark_seen(...)               → conditional PutItem with 7-day TTL
"""
from __future__ import annotations
import hashlib
import os
import time

import boto3
from botocore.exceptions import ClientError

_TTL_SECONDS = 7 * 86400  # 7 days

_dynamodb = None


def _table():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb")
    return _dynamodb.Table(os.environ["AUTONOMOUS_BROADCAST_SEEN_TABLE"])


def _key(*, tenant_id: str, finding_id: str, scan_id: str) -> str:
    return hashlib.sha256(
        f"{tenant_id}|{finding_id}|{scan_id}".encode("utf-8")
    ).hexdigest()


def seen(*, tenant_id: str, finding_id: str, scan_id: str) -> bool:
    resp = _table().get_item(Key={"seen_key": _key(
        tenant_id=tenant_id, finding_id=finding_id, scan_id=scan_id)})
    return bool(resp.get("Item"))


def mark_seen(*, tenant_id: str, finding_id: str, scan_id: str) -> None:
    k = _key(tenant_id=tenant_id, finding_id=finding_id, scan_id=scan_id)
    try:
        _table().put_item(
            Item={"seen_key": k, "ttl_epoch": int(time.time()) + _TTL_SECONDS},
            ConditionExpression="attribute_not_exists(seen_key)",
        )
    except ClientError as e:
        # Race: another invocation marked first. That's fine — the
        # broadcast either fired once (theirs) or will fire once (ours,
        # depending on which side of the SQS visibility window we're on).
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
