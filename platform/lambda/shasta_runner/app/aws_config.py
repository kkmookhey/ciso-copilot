# app/aws_config.py
"""Shared botocore Config for every AWS client the scanner builds.

Without explicit timeouts, a slow or unreachable regional endpoint
blocks a boto3 call indefinitely and hangs the whole scan. This Config
bounds every call: connect within 10s, read within 30s, at most 3
attempts total. Apply it to every client created in this repo — Shasta
is frozen and cannot carry it.
"""
from __future__ import annotations

from botocore.config import Config

SCAN_BOTO_CONFIG = Config(
    connect_timeout=10,
    read_timeout=30,
    retries={"max_attempts": 3, "mode": "standard"},
)
