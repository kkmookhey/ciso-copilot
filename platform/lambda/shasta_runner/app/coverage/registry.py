# app/coverage/registry.py
"""The coverage engine registry — the single source of truth for which
posture checks and collectors exist, and which run at a given scan tier.

A check's `min_tier` is the LOWEST tier at which it runs: a 'quick' scan
runs only min_tier=quick checks; 'medium' runs quick+medium; 'deep' runs
all. See spec §3, §6.
"""
from __future__ import annotations

from coverage.checks import ecr as _checks_ecr
from coverage.checks import secretsmanager as _checks_sm
from coverage.checks import sqs as _checks_sqs
from coverage.collectors import ecr as _collect_ecr
from coverage.collectors import secretsmanager as _collect_sm
from coverage.collectors import sqs as _collect_sqs
from coverage.model import Check

ALL_CHECKS: list[Check] = [
    *_checks_sqs.CHECKS,
    *_checks_sm.CHECKS,
    *_checks_ecr.CHECKS,
]

# service name -> collector.collect callable
COLLECTORS = {
    "sqs":            _collect_sqs.collect,
    "secretsmanager": _collect_sm.collect,
    "ecr":            _collect_ecr.collect,
}

_TIER_ORDER = {"quick": 0, "medium": 1, "deep": 2}


def checks_for_tier(tier: str) -> list[Check]:
    """Every check that runs at `tier` — i.e. whose min_tier is at or
    below `tier` in the quick < medium < deep ordering."""
    ceiling = _TIER_ORDER[tier]
    return [c for c in ALL_CHECKS if _TIER_ORDER[c.min_tier] <= ceiling]
