"""DynamoDB-backed counters. Used by router (push rate limit, sort-key prefix 'push_count:')
and by soc_enrichment (LLM spend cap, sort-key prefix 'llm_spend:'). One table, two key shapes."""
from __future__ import annotations
import os
import time
import boto3
from datetime import datetime, timezone

dynamodb  = boto3.client("dynamodb")
TABLE_NAME = os.environ.get("SPEND_CAP_TABLE_NAME", "soc_llm_spend_daily")


def _expires_at(days: int = 90) -> int:
    return int(time.time()) + days * 86400


def push_count_increment(tenant_id: str) -> int:
    """Increment this hour's push counter for the tenant. Returns the NEW count."""
    hour_key = datetime.now(timezone.utc).strftime("push_count:%Y-%m-%dT%H")
    rs = dynamodb.update_item(
        TableName=TABLE_NAME,
        Key={"tenant_id": {"S": tenant_id}, "day": {"S": hour_key}},
        UpdateExpression="ADD #c :one SET #exp = :exp",
        ExpressionAttributeNames={"#c": "count", "#exp": "expires_at"},
        ExpressionAttributeValues={":one": {"N": "1"}, ":exp": {"N": str(_expires_at())}},
        ReturnValues="UPDATED_NEW",
    )
    return int(rs["Attributes"]["count"]["N"])


def push_count_current(tenant_id: str) -> int:
    """Read this hour's push count without incrementing."""
    hour_key = datetime.now(timezone.utc).strftime("push_count:%Y-%m-%dT%H")
    rs = dynamodb.get_item(
        TableName=TABLE_NAME,
        Key={"tenant_id": {"S": tenant_id}, "day": {"S": hour_key}},
    )
    item = rs.get("Item")
    return int(item["count"]["N"]) if item and "count" in item else 0


def llm_spend_today_cents(tenant_id: str) -> int:
    day_key = datetime.now(timezone.utc).strftime("llm_spend:%Y-%m-%d")
    rs = dynamodb.get_item(
        TableName=TABLE_NAME,
        Key={"tenant_id": {"S": tenant_id}, "day": {"S": day_key}},
    )
    item = rs.get("Item")
    return int(item["cents"]["N"]) if item and "cents" in item else 0


def llm_spend_add(tenant_id: str, cents: int) -> int:
    day_key = datetime.now(timezone.utc).strftime("llm_spend:%Y-%m-%d")
    rs = dynamodb.update_item(
        TableName=TABLE_NAME,
        Key={"tenant_id": {"S": tenant_id}, "day": {"S": day_key}},
        UpdateExpression="ADD #c :n SET #exp = :exp",
        ExpressionAttributeNames={"#c": "cents", "#exp": "expires_at"},
        ExpressionAttributeValues={":n": {"N": str(int(cents))}, ":exp": {"N": str(_expires_at())}},
        ReturnValues="UPDATED_NEW",
    )
    return int(rs["Attributes"]["cents"]["N"])
