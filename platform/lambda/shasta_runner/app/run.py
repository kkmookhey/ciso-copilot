# app/run.py
"""Fargate entrypoint for the AWS scanner.

As a Lambda the scanner is invoked as main.handler(event, context).
As a Fargate task there is no event — scan parameters arrive as
environment variables (set via ecs:RunTask container overrides). This
script reads them into the event shape and calls the handler.

Usage (the container CMD for Fargate): python run.py

NOTE: `from main import handler` is intentionally placed inside main()
rather than at module top. main.py's module-level code reads
DB_CLUSTER_ARN / DB_SECRET_ARN / DB_NAME from os.environ and constructs
boto3 clients; importing it unconditionally would break test collection
for build_event, which is a pure function that needs none of that.
Moving the import into main() keeps build_event independently testable
without any env-var or AWS scaffolding.
"""
from __future__ import annotations

import os
import sys

_REQUIRED = ("SCAN_ID", "TENANT_ID", "CONN_ID", "ROLE_ARN", "EXTERNAL_ID", "ACCOUNT_ID")


def build_event(env: dict[str, str]) -> dict:
    """Map scanner env vars to the event dict main.handler expects.
    REGIONS is comma-split into an explicit override, or omitted so the scanner discovers regions.
    Raises KeyError if a required var is missing."""
    event = {
        "scan_id":     env["SCAN_ID"],
        "tenant_id":   env["TENANT_ID"],
        "conn_id":     env["CONN_ID"],
        "role_arn":    env["ROLE_ARN"],
        "external_id": env["EXTERNAL_ID"],
        "account_id":  env["ACCOUNT_ID"],
        "scan_tier":   env.get("SCAN_TIER", "quick"),
    }
    regions = [r.strip() for r in env.get("REGIONS", "").split(",") if r.strip()]
    if regions:
        # An explicit REGIONS override; otherwise omit 'regions' so the
        # scanner's region-discovery pre-pass picks the scan scope.
        event["regions"] = regions
    return event


def main() -> None:
    missing = [v for v in _REQUIRED if not os.environ.get(v)]
    if missing:
        print(f"FATAL: missing required env vars: {missing}")
        sys.exit(1)
    from main import handler  # deferred: avoids module-level env-var reads at import time
    result = handler(build_event(dict(os.environ)), None)
    print(f"scan finished: {result}")


if __name__ == "__main__":
    main()
