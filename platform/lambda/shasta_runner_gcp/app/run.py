"""Fargate entrypoint for the GCP scanner.

As a Lambda the scanner is invoked as main.handler(event, context). As a
Fargate task there is no event — scan parameters arrive as environment
variables (set via ecs:RunTask container overrides). This script reads
them into the event shape and calls the handler.

Usage (the container command for Fargate): python run.py

`from main import handler` is deferred into main() — main.py's
module-level code constructs boto3 clients and (when first used) imports
shasta.*, so importing it unconditionally would break build_event's test
collection. build_event is a pure function and stays independently
testable.
"""
from __future__ import annotations

import os
import sys

_REQUIRED = ("SCAN_ID", "TENANT_ID", "CONN_ID", "PROJECT_IDS",
             "WIF_PROJECT_NUMBER", "SA_EMAIL", "WIF_POOL", "WIF_PROVIDER")


def build_event(env: dict[str, str]) -> dict:
    """Map scanner env vars to the event dict main.handler expects.
    PROJECT_IDS is a comma-separated list. Raises KeyError if a required
    var is missing."""
    return {
        "scan_id":            env["SCAN_ID"],
        "tenant_id":          env["TENANT_ID"],
        "conn_id":            env["CONN_ID"],
        "project_ids":        [p.strip() for p in env["PROJECT_IDS"].split(",")
                               if p.strip()],
        "wif_project_number": env["WIF_PROJECT_NUMBER"],
        "sa_email":           env["SA_EMAIL"],
        "wif_pool":           env["WIF_POOL"],
        "wif_provider":       env["WIF_PROVIDER"],
        "scan_tier":          env.get("SCAN_TIER", "quick"),
    }


def main() -> None:
    missing = [v for v in _REQUIRED if not os.environ.get(v)]
    if missing:
        print(f"FATAL: missing required env vars: {missing}")
        sys.exit(1)
    from main import handler  # deferred — avoids module-level imports at collection
    result = handler(build_event(dict(os.environ)), None)
    print(f"scan finished: {result}")


if __name__ == "__main__":
    main()
