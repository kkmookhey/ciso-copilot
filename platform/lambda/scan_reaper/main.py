"""Scheduled stuck-scan reaper.

The onboarding/rescan Lambdas only mark a scan `failed` when ECS `RunTask`
throws *synchronously*. A Fargate task that starts and then dies (image-pull
failure, OOM, spot reclaim) — or an entra scan Lambda that times out — leaves
the `scans` row stuck at `queued`/`running` forever, so the UI shows
"Discovering regions…" indefinitely.

This runs on a schedule and fails any non-terminal scan that is BOTH
  (a) older than GRACE_MINUTES, and
  (b) NOT backed by a live (RUNNING/PENDING) ECS task on the scan cluster.

The live-task check means a legitimately long-running Fargate scan is never
reaped, no matter how old its `started_at` is (the scanner bumps `phase`, not
a timestamp, so elapsed time alone can't tell "slow" from "dead"). Entra scans
run as Lambdas — no ECS task — but finish or time out within Lambda's 15-min
ceiling, so the grace period alone is a safe backstop for them.
"""
from __future__ import annotations

import os

import boto3

DB_CLUSTER_ARN   = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN    = os.environ["DB_SECRET_ARN"]
DB_NAME          = os.environ["DB_NAME"]
SCAN_CLUSTER_ARN = os.environ["SCAN_CLUSTER_ARN"]
GRACE_MINUTES    = int(os.environ.get("GRACE_MINUTES", "20"))

rds_data = boto3.client("rds-data")
ecs      = boto3.client("ecs")


def handler(event: dict, context) -> dict:
    live  = _live_scan_ids()
    stale = _stale_scan_ids()
    reaped = [sid for sid in stale if sid not in live]
    for sid in reaped:
        _reap(sid)
    print(f"scan_reaper: {len(stale)} stale (> {GRACE_MINUTES}m), "
          f"{len(live)} live ECS tasks, reaped {len(reaped)}")
    return {"stale": len(stale), "live": len(live),
            "reaped": len(reaped), "reaped_ids": reaped}


def _live_scan_ids() -> set[str]:
    """SCAN_IDs of every RUNNING or PENDING task on the scan cluster, read from
    each task's container env overrides (set by RunTask)."""
    live: set[str] = set()
    for desired in ("RUNNING", "PENDING"):
        arns: list[str] = []
        for page in ecs.get_paginator("list_tasks").paginate(
                cluster=SCAN_CLUSTER_ARN, desiredStatus=desired):
            arns.extend(page.get("taskArns", []))
        for i in range(0, len(arns), 100):  # describe_tasks caps at 100 ARNs
            chunk = arns[i:i + 100]
            if not chunk:
                continue
            for task in ecs.describe_tasks(
                    cluster=SCAN_CLUSTER_ARN, tasks=chunk).get("tasks", []):
                overrides = (task.get("overrides") or {}).get("containerOverrides", [])
                for ov in overrides:
                    for env in ov.get("environment", []):
                        if env.get("name") == "SCAN_ID" and env.get("value"):
                            live.add(env["value"])
    return live


def _stale_scan_ids() -> list[str]:
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "SELECT scan_id::text FROM scans "
            "WHERE status IN ('queued','running') "
            "AND started_at < now() - (:grace * interval '1 minute')"
        ),
        parameters=[{"name": "grace", "value": {"longValue": GRACE_MINUTES}}],
    )
    return [r[0].get("stringValue") for r in rs.get("records", [])]


def _reap(scan_id: str) -> None:
    # Re-check status in the UPDATE to avoid racing a scan that finished between
    # the SELECT and now. COALESCE preserves any error the scanner already set.
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "UPDATE scans SET status = 'failed', finished_at = now(), "
            "error = COALESCE(error, :err) "
            "WHERE scan_id = CAST(:sid AS UUID) AND status IN ('queued','running')"
        ),
        parameters=[
            {"name": "sid", "value": {"stringValue": scan_id}},
            {"name": "err", "value": {"stringValue":
                f"reaped: no live scan task after {GRACE_MINUTES} min "
                "(task failed to start or died) — please re-scan"}},
        ],
    )
