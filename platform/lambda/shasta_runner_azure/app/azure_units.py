"""Azure scan tiers — which Shasta Azure modules run at which tier and,
for Quick, in which phase. Pure data + logic, no Shasta import, so it is
unit-testable. main.py maps these module-name strings to the Shasta
`run_all_azure_*_checks` functions.

Tiers (spec section 6):
  Quick  — phase 1 (first signal): iam, governance
           phase 2 (crown jewel):  storage, networking, compute, encryption
  Medium — Quick set + databases, appservice, monitoring (single phase)
  Deep   — Medium set + backup, diagnostic_settings, private_endpoints
"""
from __future__ import annotations

_QUICK_PHASE_1 = ["iam", "governance"]
_QUICK_PHASE_2 = ["storage", "networking", "compute", "encryption"]
_MEDIUM_EXTRA  = ["databases", "appservice", "monitoring"]
_DEEP_EXTRA    = ["backup", "diagnostic_settings", "private_endpoints"]

ALL_MODULES = _QUICK_PHASE_1 + _QUICK_PHASE_2 + _MEDIUM_EXTRA + _DEEP_EXTRA


def modules_for_tier(tier: str) -> tuple[list[str], list[str]]:
    """Return (phase_1_modules, phase_2_modules) for `tier`.

    Quick splits across two phases so phase 1 can commit early; Medium
    and Deep run everything in phase 1 (phase 2 empty)."""
    t = tier.lower()
    if t == "quick":
        return (list(_QUICK_PHASE_1), list(_QUICK_PHASE_2))
    if t == "medium":
        return (_QUICK_PHASE_1 + _QUICK_PHASE_2 + _MEDIUM_EXTRA, [])
    if t == "deep":
        return (_QUICK_PHASE_1 + _QUICK_PHASE_2 + _MEDIUM_EXTRA + _DEEP_EXTRA, [])
    raise ValueError(f"unknown scan tier: {tier}")
