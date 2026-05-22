"""Shared `scans`-table state writes — status / phase / stats and the
per-scan coverage map. Cloud-agnostic: the AWS and Azure scanners both
use it.

`record_scan_scope` takes an already-shaped `scope` dict, so a
region-keyed (AWS) or subscription-keyed (Azure) coverage map both work
without this module knowing the difference.

DB config (`DB_CLUSTER_ARN` / `DB_SECRET_ARN` / `DB_NAME`) is read
lazily inside the functions, not at import — so the module imports
cleanly in test collection without those env vars set.
"""
from __future__ import annotations

import json
import os

import boto3

# boto3.client() is offline (no creds/network needed), so a module-level
# client is safe at import. Tests monkeypatch this attribute.
_rds = boto3.client("rds-data")


def _db() -> tuple[str, str, str]:
    return (os.environ["DB_CLUSTER_ARN"],
            os.environ["DB_SECRET_ARN"],
            os.environ["DB_NAME"])


def update_scan(scan_id: str, status: str, *, phase: str | None = None,
                stats: dict | None = None, error: str | None = None) -> None:
    """UPDATE the `scans` row. `phase`/`stats`/`error` are written only
    when supplied. A terminal status also stamps `finished_at`."""
    cluster, secret, name = _db()
    sql_parts = ["UPDATE scans SET status = :status"]
    params = [
        {"name": "sid",    "value": {"stringValue": scan_id}},
        {"name": "status", "value": {"stringValue": status}},
    ]
    if phase is not None:
        sql_parts.append("phase = :phase")
        params.append({"name": "phase", "value": {"stringValue": phase}})
    if status in ("completed", "failed", "partial"):
        sql_parts.append("finished_at = now()")
    if stats is not None:
        sql_parts.append("stats = CAST(:stats AS JSONB)")
        params.append({"name": "stats",
                       "value": {"stringValue": json.dumps(stats)}})
    if error is not None:
        sql_parts.append("error = :error")
        params.append({"name": "error", "value": {"stringValue": error}})
    sql = ", ".join(sql_parts) + " WHERE scan_id = CAST(:sid AS UUID)"
    _rds.execute_statement(resourceArn=cluster, secretArn=secret,
                           database=name, sql=sql, parameters=params)


def record_scan_scope(scan_id: str, scope: dict) -> None:
    """Write a pre-shaped coverage map to `scans.scope`. The caller owns
    the shape — this module does not interpret it."""
    cluster, secret, name = _db()
    _rds.execute_statement(
        resourceArn=cluster, secretArn=secret, database=name,
        sql=("UPDATE scans SET scope = CAST(:scope AS JSONB) "
             "WHERE scan_id = CAST(:sid AS UUID)"),
        parameters=[
            {"name": "sid",   "value": {"stringValue": scan_id}},
            {"name": "scope", "value": {"stringValue": json.dumps(scope)}},
        ],
    )
