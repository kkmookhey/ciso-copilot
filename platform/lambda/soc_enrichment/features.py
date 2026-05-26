"""Statistical features for SOC enrichment. All read from Aurora events history."""
from __future__ import annotations
import os
from datetime import datetime
import boto3

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


def compute_features(row: dict) -> dict:
    return {
        "first_time_actor_on_resource": _first_time_actor_on_resource(
            row["tenant_id"], row.get("actor"), row.get("resource_arn")),
        "off_hours":                    _is_off_hours(row["fired_at"]),
        "action_rarity":                _action_rarity(row["tenant_id"], row.get("title")),
        "blast_radius_proxy":           _blast_radius_proxy(row["tenant_id"], row.get("actor")),
    }
