"""Statistical features for SOC enrichment. All read from Aurora events history."""
from __future__ import annotations
import os
from datetime import datetime
import boto3

# Vendored from _shared/ by build.sh
import ti_lookup
import ioc_extract
import greynoise

DB_CLUSTER_ARN = os.environ.get("DB_CLUSTER_ARN", "")
DB_SECRET_ARN  = os.environ.get("DB_SECRET_ARN", "")
DB_NAME        = os.environ.get("DB_NAME", "ciso_copilot")

rds_data = boto3.client("rds-data")

# Business hours: Mon-Fri 09:00-18:00 UTC. Per-tenant tz is a future enhancement.
BIZ_START = 9
BIZ_END   = 18


def _is_off_hours(fired_at_iso: str) -> bool:
    t = datetime.fromisoformat(fired_at_iso.replace("Z", "+00:00"))
    if t.weekday() >= 5:        # 5,6 = Sat,Sun
        return True
    return not (BIZ_START <= t.hour < BIZ_END)


def _first_time_actor_on_resource(tenant_id: str, actor: str | None, resource_arn: str | None) -> bool:
    if not actor or not resource_arn:
        return False
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "SELECT 1 FROM events "
            "WHERE tenant_id = CAST(:t AS UUID) AND actor = :a AND resource_arn = :r "
            "  AND fired_at > now() - interval '30 days' "
            "LIMIT 1"
        ),
        parameters=[
            {"name": "t", "value": {"stringValue": tenant_id}},
            {"name": "a", "value": {"stringValue": actor}},
            {"name": "r", "value": {"stringValue": resource_arn}},
        ],
    )
    return len(rs.get("records", [])) == 0


def _action_rarity(tenant_id: str, action: str | None) -> str:
    if not action:
        return "common"
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "SELECT count(*) FROM events "
            "WHERE tenant_id = CAST(:t AS UUID) AND title = :a "
            "  AND fired_at > now() - interval '30 days'"
        ),
        parameters=[
            {"name": "t", "value": {"stringValue": tenant_id}},
            {"name": "a", "value": {"stringValue": action}},
        ],
    )
    rows = rs.get("records", [])
    if not rows:
        return "common"
    n = rows[0][0].get("longValue", 0)
    if n == 0: return "first_time"
    if n < 5:  return "rare"
    return "common"


def _blast_radius_proxy(tenant_id: str, actor: str | None) -> int:
    if not actor:
        return 0
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "SELECT count(DISTINCT resource_arn) FROM events "
            "WHERE tenant_id = CAST(:t AS UUID) AND actor = :a "
            "  AND fired_at > now() - interval '30 days' AND resource_arn IS NOT NULL"
        ),
        parameters=[
            {"name": "t", "value": {"stringValue": tenant_id}},
            {"name": "a", "value": {"stringValue": actor}},
        ],
    )
    rows = rs.get("records", [])
    return rows[0][0].get("longValue", 0) if rows else 0


def _greynoise_api_key() -> str | None:
    """Resolve the GreyNoise key once per cold start from Secrets Manager.

    Returns None when the secret is not configured — disables on-demand fallback.
    """
    cached = getattr(_greynoise_api_key, "_cached", "unset")
    if cached != "unset":
        return cached  # type: ignore
    name = os.environ.get("GREYNOISE_API_KEY_SECRET_NAME")
    if not name:
        _greynoise_api_key._cached = None  # type: ignore
        return None
    try:
        sm = boto3.client("secretsmanager")
        secret = sm.get_secret_value(SecretId=name)["SecretString"]
        try:
            import json as _json
            key = _json.loads(secret).get("GREYNOISE_API_KEY", secret)
        except (TypeError, ValueError):
            key = secret
    except Exception as e:
        print(f"WARN: greynoise key fetch failed: {e!r}")
        key = None
    _greynoise_api_key._cached = key  # type: ignore
    return key


def _ti_matches(row: dict) -> list[dict]:
    """Extract IOCs from the event row, look them up in threat_indicators,
    optionally fall back to on-demand GreyNoise Community for unmatched IPs.

    Returns a list of {value, kind, source, confidence, tags} dicts — at most
    a handful per event (callers tolerate empty list).
    """
    iocs = ioc_extract.extract_iocs(row)
    # Collapse to a single dict keyed by kind→list[str] for bulk_lookup
    db_hits = ti_lookup.bulk_lookup(iocs)

    matches: list[dict] = []
    for value, rows in db_hits.items():
        for r in rows:
            matches.append({
                "value":      value,
                "kind":       r["kind"],
                "source":     r["source"],
                "confidence": r["confidence"],
                "tags":       r["tags"],
            })

    # GreyNoise on-demand fallback: only IPs, only those that missed in DB
    unmatched_ips = [ip for ip in iocs.get("ip", []) if ip not in db_hits]
    if unmatched_ips:
        key = _greynoise_api_key()
        if key:
            tenant_id = row.get("tenant_id") or ""
            for ip in unmatched_ips[:5]:  # cap per-event GreyNoise calls
                hit = greynoise.lookup_ip(tenant_id, ip, api_key=key)
                if hit:
                    matches.append({
                        "value":      hit["value"],
                        "kind":       hit["kind"],
                        "source":     hit["source"],
                        "confidence": hit["confidence"],
                        "tags":       [hit.get("classification") or "unknown"] +
                                       ([hit["name"]] if hit.get("name") else []),
                    })
    return matches


def compute_features(row: dict) -> dict:
    return {
        "first_time_actor_on_resource": _first_time_actor_on_resource(
            row["tenant_id"], row.get("actor"), row.get("resource_arn")),
        "off_hours":                    _is_off_hours(row["fired_at"]),
        "action_rarity":                _action_rarity(row["tenant_id"], row.get("title")),
        "blast_radius_proxy":           _blast_radius_proxy(row["tenant_id"], row.get("actor")),
        "ti_matches":                   _ti_matches(row),
    }
