"""GreyNoise Community on-demand IP lookup.

Free tier: 50 req/day per key. We cap ourselves at 30/day/tenant via the
existing soc_llm_spend_daily DynamoDB table (sort-key prefix
`greynoise_count:YYYY-MM-DD`) so a single noisy tenant can't burn the
day's budget for everyone.

Only called by soc_enrichment for IPs that missed in threat_indicators.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import urllib.request
import urllib.error

import boto3

_API_URL = "https://api.greynoise.io/v3/community/{ip}"
_DAILY_CAP = int(os.environ.get("GREYNOISE_DAILY_CAP_PER_TENANT", "30"))
_TIMEOUT_S = 4
_TABLE_NAME = os.environ.get("SPEND_CAP_TABLE_NAME", "soc_llm_spend_daily")

dynamodb = boto3.client("dynamodb")


def _http_get(url: str, headers: dict[str, str]) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b""
    except (urllib.error.URLError, TimeoutError):
        return 599, b""


def _key(tenant_id: str) -> dict:
    day = datetime.now(timezone.utc).strftime("greynoise_count:%Y-%m-%d")
    return {"tenant_id": {"S": tenant_id}, "day": {"S": day}}


def _under_cap(tenant_id: str) -> bool:
    rs = dynamodb.get_item(TableName=_TABLE_NAME, Key=_key(tenant_id))
    item = rs.get("Item")
    n = int(item["count"]["N"]) if item and "count" in item else 0
    return n < _DAILY_CAP


def _increment_count(tenant_id: str) -> int:
    rs = dynamodb.update_item(
        TableName=_TABLE_NAME, Key=_key(tenant_id),
        UpdateExpression="ADD #c :one SET #exp = :exp",
        ExpressionAttributeNames={"#c": "count", "#exp": "expires_at"},
        ExpressionAttributeValues={
            ":one": {"N": "1"},
            ":exp": {"N": str(int(datetime.now(timezone.utc).timestamp()) + 7 * 86400)},
        },
        ReturnValues="UPDATED_NEW",
    )
    return int(rs["Attributes"]["count"]["N"])


def lookup_ip(tenant_id: str, ip: str, *, api_key: str | None) -> dict | None:
    """Return a dict shaped like a threat_indicators row, or None on miss/cap/error.

    Output (when hit):
      {"source": "greynoise_community", "kind": "ip", "value": ip,
       "classification": "malicious"|"benign"|"unknown",
       "confidence": int 0-100, "name": str|None, "link": str|None}
    """
    if not api_key:
        return None
    if not _under_cap(tenant_id):
        return None

    status, body = _http_get(_API_URL.format(ip=ip), {"Accept": "application/json", "key": api_key})
    _increment_count(tenant_id)
    if status != 200:
        return None
    try:
        data: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        return None

    classification = data.get("classification") or "unknown"
    confidence = {"malicious": 85, "unknown": 50, "benign": 20}.get(classification, 50)

    return {
        "source":         "greynoise_community",
        "kind":           "ip",
        "value":          ip,
        "classification": classification,
        "confidence":     confidence,
        "name":           data.get("name"),
        "link":           data.get("link"),
    }
