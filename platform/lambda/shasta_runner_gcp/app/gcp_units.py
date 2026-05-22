"""GCP scan tiers — which Shasta GCP modules run at which tier and, for
Quick, in which phase. Pure data + logic, no Shasta import, so it is
unit-testable. main.py maps these module-name strings to the Shasta
`run_all_gcp_*_checks` functions.

Tiers (spec section 5.4):
  Quick  — phase 1 (first signal): iam, storage
           phase 2 (crown jewel):  networking, encryption, compute
  Medium — all 7 modules, single phase
  Deep   — all 7 modules, single phase. Deep's "+ AI pass" is deferred
           to a later slice (spec open item #2) — module-wise Deep
           equals Medium for now.
"""
from __future__ import annotations

_QUICK_PHASE_1 = ["iam", "storage"]
_QUICK_PHASE_2 = ["networking", "encryption", "compute"]
_MEDIUM_EXTRA  = ["logging", "cloud_run"]

ALL_MODULES = _QUICK_PHASE_1 + _QUICK_PHASE_2 + _MEDIUM_EXTRA


def modules_for_tier(tier: str) -> tuple[list[str], list[str]]:
    """Return (phase_1_modules, phase_2_modules) for `tier`.

    Quick splits across two phases so phase 1 can commit early; Medium
    and Deep run everything in phase 1 (phase 2 empty)."""
    t = tier.lower()
    if t == "quick":
        return (list(_QUICK_PHASE_1), list(_QUICK_PHASE_2))
    if t in ("medium", "deep"):
        return (list(ALL_MODULES), [])
    raise ValueError(f"unknown scan tier: {tier}")
