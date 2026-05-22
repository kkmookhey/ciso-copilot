# app/scan_policy.py
"""Scan policy — the (scan_tier x region_state) -> depth matrix.

Encodes spec §7.3. Every enabled region is always in the plan (no
region blind spot); the policy only varies *depth*. Pure logic — no
AWS — so the Azure/GCP scanners can reuse it.
"""
from __future__ import annotations

from dataclasses import dataclass

# 'unknown' regions are scanned with the same depth as 'active' — an
# undetermined region is never under-scanned.
_FULL_DEPTH_STATES = {"active", "unknown"}


@dataclass(frozen=True)
class RegionPlan:
    run_enums:       bool   # compute/network entity enums
    regional_shasta: bool   # the 12 regional Shasta modules
    coverage:        bool   # the coverage engine for this region


@dataclass(frozen=True)
class ScanPlan:
    run_global_enums: bool
    global_modules:   bool   # the global Shasta modules (IAM, Orgs, CloudFront, logging)
    run_ai_pass:      bool
    run_capabilities: bool   # heavy Deep-tier capability modules
    per_region:       dict[str, RegionPlan]


def build_scan_plan(scan_tier: str,
                    region_states: dict[str, str]) -> ScanPlan:
    """Build the ScanPlan for `scan_tier` over the classified regions."""
    tier = scan_tier.lower()
    is_medium_plus = tier in ("medium", "deep")

    per_region: dict[str, RegionPlan] = {}
    for region, state in region_states.items():
        full_depth = state in _FULL_DEPTH_STATES
        per_region[region] = RegionPlan(
            run_enums=True,                       # every region: inventory
            regional_shasta=is_medium_plus and full_depth,
            coverage=True,                        # coverage engine every tier
        )

    return ScanPlan(
        run_global_enums=True,
        global_modules=True,                      # global posture: every tier
        run_ai_pass=is_medium_plus,
        run_capabilities=(tier == "deep"),
        per_region=per_region,
    )
