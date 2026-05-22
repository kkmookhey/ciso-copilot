"""Fargate entrypoint for the Azure scanner.

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

_REQUIRED = ("SCAN_ID", "TENANT_ID", "CONN_ID", "AZURE_TENANT_ID",
             "CLIENT_ID", "SECRET_ARN", "SUBSCRIPTION_IDS")


def build_event(env: dict[str, str]) -> dict:
    """Map scanner env vars to the event dict main.handler expects.
    SUBSCRIPTION_IDS is a comma-separated list. Raises KeyError if a
    required var is missing."""
    return {
        "scan_id":          env["SCAN_ID"],
        "tenant_id":        env["TENANT_ID"],
        "conn_id":          env["CONN_ID"],
        "azure_tenant_id":  env["AZURE_TENANT_ID"],
        "client_id":        env["CLIENT_ID"],
        "secret_arn":       env["SECRET_ARN"],
        "subscription_ids": [s.strip() for s in env["SUBSCRIPTION_IDS"].split(",")
                             if s.strip()],
        "scan_tier":        env.get("SCAN_TIER", "quick"),
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
