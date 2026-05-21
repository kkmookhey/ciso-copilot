# app/region_discovery.py
"""AWS region footprint probe — the scanner's Stage 1 + 2.

Stage 1: enumerate the account's enabled regions.
Stage 2: probe each region in parallel with a few cheap list/describe
calls and classify it active / default_only / empty / unknown.

Every enabled region is still scanned (no region blind spot); the
classification lets Stage 3 vary scan *depth* per region. See
docs/superpowers/specs/2026-05-21-scan-performance-design.md §5-6.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

ACTIVE       = "active"
DEFAULT_ONLY = "default_only"
EMPTY        = "empty"
UNKNOWN      = "unknown"

# Used only when region enumeration itself fails — a documented,
# non-silent fallback. All marked 'unknown' so Stage 3 scans them
# conservatively (never a silent miss).
_DEGRADED_DEFAULT_REGIONS = (
    "us-east-1", "us-east-2", "us-west-1", "us-west-2",
    "eu-west-1", "eu-central-1", "ap-south-1",
    "ap-southeast-1", "ap-southeast-2", "ap-northeast-1",
)


@dataclass(frozen=True)
class RegionDiscovery:
    """Outcome of Stage 1 + 2."""
    region_states:   dict[str, str]   # region -> active|default_only|empty|unknown
    enabled_regions: list[str]        # all enabled regions enumerated
    method:          str              # 'footprint_probe' | 'degraded_default'


def classify_region(*, has_real: bool, has_default_vpc: bool,
                     errored: bool) -> str:
    """Classify one region from its probe signals.

    `errored` always wins → 'unknown' (an undetermined region is never
    silently treated as empty). Otherwise: any real resource → active;
    only a default VPC → default_only; nothing → empty.
    """
    if errored:
        return UNKNOWN
    if has_real:
        return ACTIVE
    if has_default_vpc:
        return DEFAULT_ONLY
    return EMPTY


def _has_any(client, op: str, key: str, **kwargs) -> bool:
    resp = getattr(client, op)(**kwargs)
    return bool(resp.get(key))


def probe_region(make_client, region: str) -> str:
    """Probe one region and return its state.

    `make_client(service)` returns a boto3 client for `service` bound to
    this region. Any exception → 'unknown' (the anti-blind-spot rule).
    """
    try:
        has_real = False
        has_default_vpc = False

        for vpc in make_client("ec2").describe_vpcs().get("Vpcs", []):
            if vpc.get("IsDefault"):
                has_default_vpc = True
            else:
                has_real = True

        if not has_real:
            probes = [
                ("ec2",    "describe_instances",       "Reservations"),
                ("lambda", "list_functions",           "Functions"),
                ("rds",    "describe_db_instances",    "DBInstances"),
                ("elbv2",  "describe_load_balancers",  "LoadBalancers"),
                ("ecs",    "list_clusters",            "clusterArns"),
                ("eks",    "list_clusters",            "clusters"),
            ]
            for service, op, key in probes:
                if _has_any(make_client(service), op, key):
                    has_real = True
                    break

        return classify_region(has_real=has_real,
                                has_default_vpc=has_default_vpc,
                                errored=False)
    except Exception as e:
        print(f"region_discovery: probe failed in {region} ({e}); "
              f"region state = unknown (scanned conservatively)")
        return UNKNOWN


def _list_enabled_regions(ec2_client) -> list[str]:
    """Enabled (opted-in or opt-in-not-required) regions for the account."""
    resp = ec2_client.describe_regions(
        Filters=[{"Name": "opt-in-status",
                  "Values": ["opt-in-not-required", "opted-in"]}],
    )
    return [r["RegionName"] for r in resp.get("Regions", [])]


def discover_regions(ec2_client, make_client_for_region) -> RegionDiscovery:
    """Stage 1 + 2. `make_client_for_region(region)` returns a callable
    make_client(service) -> boto3 client for that service in that region.

    If region enumeration fails, returns a degraded RegionDiscovery —
    the documented fallback region set, all 'unknown' — never a silent
    narrowing.
    """
    try:
        enabled = _list_enabled_regions(ec2_client)
    except Exception as e:
        print(f"region_discovery: describe_regions failed ({e}); "
              f"falling back to degraded default region set")
        return RegionDiscovery(
            region_states={r: UNKNOWN for r in _DEGRADED_DEFAULT_REGIONS},
            enabled_regions=[],
            method="degraded_default",
        )

    states: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(len(enabled), 16) or 1) as ex:
        futures = {
            ex.submit(probe_region, make_client_for_region(r), r): r
            for r in enabled
        }
        for future, region in futures.items():
            try:
                states[region] = future.result()
            except Exception:
                states[region] = UNKNOWN

    return RegionDiscovery(
        region_states=states,
        enabled_regions=sorted(enabled),
        method="footprint_probe",
    )
