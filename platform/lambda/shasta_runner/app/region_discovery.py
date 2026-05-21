# app/region_discovery.py
"""AWS region discovery — the scanner's step 0.

Before scanning, determine which regions the account actually uses, so
the scan covers the customer's real footprint: never the hardcoded
us-east-1 default, never a blind sweep of all ~17 enabled regions.

Detection: list enabled regions, then one resourcegroupstaggingapi
GetResources call per region — a region is active if it returns any
resource. See docs/superpowers/specs/2026-05-21-region-discovery-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass

# Always scanned: global services (IAM, CloudFront, Route 53, STS) anchor
# in us-east-1.
_GLOBAL_ANCHOR = "us-east-1"

# Used only when region enumeration itself fails — a documented, non-silent
# fallback (method is reported as 'degraded_default'). The common high-use
# AWS regions; better an over-broad scan than a silent miss.
_DEGRADED_DEFAULT_REGIONS = (
    "us-east-1", "us-east-2", "us-west-1", "us-west-2",
    "eu-west-1", "eu-central-1", "ap-south-1",
    "ap-southeast-1", "ap-southeast-2", "ap-northeast-1",
)


@dataclass(frozen=True)
class RegionDiscovery:
    """Outcome of region discovery for one scan."""
    active_regions:  list[str]   # regions to scan (sorted; includes us-east-1)
    enabled_regions: list[str]   # all opted-in regions enumerated
    skipped_empty:   list[str]   # enabled, swept clean, deliberately skipped
    errored_regions: list[str]   # sweep errored — included in active_regions
    method:          str         # 'tagging_api' | 'degraded_default'


def classify_regions(
    enabled_regions: list[str],
    probe: dict[str, bool | None],
) -> RegionDiscovery:
    """Turn per-region probe results into a RegionDiscovery.

    probe[region] is True (has resources), False (empty), or None (the
    sweep errored). A region is active if it is NOT a positive 'empty'
    result — i.e. True or None both count as active. us-east-1 is always
    active and never appears in skipped_empty.
    """
    active = {_GLOBAL_ANCHOR}
    skipped: list[str] = []
    errored: list[str] = []
    for region, has_resources in probe.items():
        if has_resources is None:
            errored.append(region)
            active.add(region)
        elif has_resources:
            active.add(region)
        elif region != _GLOBAL_ANCHOR:
            skipped.append(region)
    return RegionDiscovery(
        active_regions=sorted(active),
        enabled_regions=sorted(enabled_regions),
        skipped_empty=sorted(skipped),
        errored_regions=sorted(errored),
        method="tagging_api",
    )
