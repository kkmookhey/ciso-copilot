"""SSM-backed global kill switch with 60-second in-memory cache.

Fail-open: a flaky SSM call shouldn't silence alerts. The per-tenant
toggle in tenant_bot_connectors.autonomous_rule_enabled is the
authoritative kill — this global switch is the paranoid layer for
"we discovered the Block Kit template leaks data; pull the brake."
"""
from __future__ import annotations
import os
import time

import boto3

_CACHE_TTL_SECONDS = 60
_ssm = boto3.client("ssm")
_cache: tuple[float, bool] = (0.0, True)  # (fetched_at, value)


def global_enabled() -> bool:
    global _cache
    fetched_at, value = _cache
    now = time.time()
    if now - fetched_at < _CACHE_TTL_SECONDS:
        return value
    try:
        resp = _ssm.get_parameter(Name=os.environ["AUTONOMOUS_RULE_SSM_PARAM"])
        value = resp["Parameter"]["Value"].lower() == "true"
    except Exception as e:
        print(f"[kill_switch] SSM read failed: {e!r}; failing open")
        value = True
    _cache = (now, value)
    return value
