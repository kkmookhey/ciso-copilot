# Scan Execution v2 — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the AWS scanner as a three-stage, parallel, tier-aware pipeline so a 16-region account scans in tier-appropriate wall-clock time, with a per-scan coverage map and no region blind spots.

**Architecture:** Stage 1 enumerates enabled regions; Stage 2 probes each region in parallel and classifies it `active` / `default_only` / `empty` / `unknown`; Stage 3 builds tier-and-region-state-aware scan *units* and runs them through one in-task `ThreadPoolExecutor` bounded by per-service concurrency caps. The transactional writer is preserved (Quick commits twice — Phase 1 then Phase 2). A scan-status API exposes progress.

**Tech Stack:** Python 3.12, pytest, `botocore.stub.Stubber`, `concurrent.futures`, AWS ECS Fargate, Aurora (Data API), API Gateway + Lambda.

**Spec:** `docs/superpowers/specs/2026-05-21-scan-performance-design.md` (rev 3). This plan covers the backend — spec §14 tasks 1-8 + 10. The web UX (spec §10, §14 task 9) is a separate follow-on plan.

---

## Conventions

- Scanner paths are under `platform/lambda/shasta_runner/`. Run tests with `./.venv/bin/python -m pytest` from that directory.
- `app/tests/conftest.py` puts `app/` on `sys.path` — import new modules by bare name.
- `app/main.py` imports `shasta.*` and is **not importable in the test venv** — its changes are verified structurally (`ast.parse` + `grep`) and end-to-end in Task 10.
- This plan does **not** rebuild or deploy the scanner image until Task 10. Tasks 1-9 land code + migrations; the live scanner runs the old image until Task 10's build/deploy. (So Task 3's change to `region_discovery.py`'s `RegionDiscovery` shape leaves `main.py` referentially stale only until Task 7 rewrites the handler — both land before Task 10.)
- Commit after every task with a Conventional Commit message.
- Aurora Data API ARNs are in `CLAUDE.md`.

## File structure

```
app/aws_config.py            modified — adaptive retry                      (Task 1)
app/scan_pipeline.py         new — ScanUnit, ConcurrencyLimiter, run_units   (Task 2)
app/region_discovery.py      rewritten — 4-state footprint probe            (Task 3)
app/scan_policy.py           new — (tier × region_state) -> ScanPlan         (Task 4)
app/coverage/engine.py       modified — per-region entry point              (Task 5)
platform/sql/010_scan_phase.sql   new — scans.phase column                  (Task 6)
app/main.py                  rewritten handler — the three-stage pipeline    (Task 7)
platform/lambda/scans_status/main.py   new — GET /v1/scans/{id}              (Task 8)
platform/lib/api-stack.ts    modified — the scans-status route              (Task 8)
platform/lambda/shasta_runner/Dockerfile   modified — PYTHONUNBUFFERED       (Task 9)
platform/lib/scan-stack.ts   modified — Fargate task 4 vCPU / 8 GB           (Task 9)
```

---

## Task 1: Adaptive retry in the shared boto config

**Files:**
- Modify: `app/aws_config.py`
- Test: `app/tests/test_aws_config.py`

- [ ] **Step 1: Write the failing test**

```python
# app/tests/test_aws_config.py
"""SCAN_BOTO_CONFIG uses adaptive retry — client-side, throttle-aware
rate limiting — so 16-way concurrent scanning does not hammer a
throttling service."""
from aws_config import SCAN_BOTO_CONFIG


def test_scan_config_uses_adaptive_retry():
    assert SCAN_BOTO_CONFIG.retries["mode"] == "adaptive"
    assert SCAN_BOTO_CONFIG.retries["max_attempts"] >= 3


def test_scan_config_keeps_timeouts():
    assert SCAN_BOTO_CONFIG.connect_timeout == 10
    assert SCAN_BOTO_CONFIG.read_timeout == 30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_aws_config.py -v`
Expected: FAIL — `test_scan_config_uses_adaptive_retry` fails (mode is currently `standard`).

- [ ] **Step 3: Change the retry mode**

In `app/aws_config.py`, the `SCAN_BOTO_CONFIG` definition currently has `retries={"max_attempts": 3, "mode": "standard"}`. Change `"mode"` to `"adaptive"`:

```python
SCAN_BOTO_CONFIG = Config(
    connect_timeout=10,
    read_timeout=30,
    retries={"max_attempts": 3, "mode": "adaptive"},
)
```

Update the module docstring's retry sentence to say "adaptive retry mode (client-side, throttle-aware rate limiting)".

- [ ] **Step 4: Run test to verify it passes**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_aws_config.py -v`
Expected: PASS — 2 tests.

- [ ] **Step 5: Run the full suite and commit**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/ -q` — expect all pass.

```bash
git add platform/lambda/shasta_runner/app/aws_config.py \
        platform/lambda/shasta_runner/app/tests/test_aws_config.py
git commit -m "feat: use adaptive retry for scanner boto clients"
```

---

## Task 2: The parallel scan-unit pipeline

**Files:**
- Create: `app/scan_pipeline.py`
- Test: `app/tests/test_scan_pipeline.py`

- [ ] **Step 1: Write the failing test**

```python
# app/tests/test_scan_pipeline.py
"""scan_pipeline runs scan units concurrently, merges their emissions,
isolates failures, and bounds per-service concurrency."""
import threading
import time

from scan_pipeline import ConcurrencyLimiter, ScanUnit, run_units


def _unit(name, service, entities=(), findings=(), raises=None, sleep=0.0):
    def _run():
        if sleep:
            time.sleep(sleep)
        if raises:
            raise raises
        return {"entities": list(entities), "edges": [], "findings": list(findings)}
    return ScanUnit(name=name, service=service, run=_run)


def test_run_units_merges_emissions():
    units = [
        _unit("a", "ec2", entities=["e1"], findings=["f1"]),
        _unit("b", "s3", entities=["e2"], findings=["f2", "f3"]),
    ]
    res = run_units(units, limiter=ConcurrencyLimiter())
    assert sorted(res.entities) == ["e1", "e2"]
    assert sorted(res.findings) == ["f1", "f2", "f3"]
    assert {o.name: o.status for o in res.outcomes} == {"a": "success", "b": "success"}


def test_run_units_isolates_a_failing_unit():
    units = [
        _unit("ok", "ec2", findings=["f1"]),
        _unit("bad", "ec2", raises=RuntimeError("boom")),
    ]
    res = run_units(units, limiter=ConcurrencyLimiter())
    assert res.findings == ["f1"]
    outcomes = {o.name: o.status for o in res.outcomes}
    assert outcomes == {"ok": "success", "bad": "error"}


def test_run_units_marks_stragglers_timeout():
    units = [
        _unit("fast", "ec2", findings=["f1"]),
        _unit("slow", "ec2", findings=["f2"], sleep=2.0),
    ]
    res = run_units(units, limiter=ConcurrencyLimiter(), batch_timeout=0.5)
    assert res.findings == ["f1"]
    outcomes = {o.name: o.status for o in res.outcomes}
    assert outcomes["fast"] == "success"
    assert outcomes["slow"] == "timeout"


def test_concurrency_limiter_caps_per_service():
    limiter = ConcurrencyLimiter(default=2)
    live = 0
    peak = 0
    lock = threading.Lock()

    def _run():
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.1)
        with lock:
            live -= 1
        return {"entities": [], "edges": [], "findings": []}

    units = [ScanUnit(name=f"u{i}", service="ec2", run=_run) for i in range(6)]
    run_units(units, limiter=limiter, max_workers=6)
    assert peak <= 2  # the ec2 cap held despite 6 workers
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_scan_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scan_pipeline'`.

- [ ] **Step 3: Implement `scan_pipeline.py`**

```python
# app/scan_pipeline.py
"""Parallel scan-unit pipeline.

All scanner work is expressed as independent ScanUnits, each producing
{entities, edges, findings}. run_units fans them across a thread pool
(the work is I/O-bound — AWS API calls), merges results, isolates
per-unit failures, and bounds per-AWS-service concurrency. The module
is pure orchestration — no AWS, no scanner specifics — so it can be
shared by the Azure / GCP scanners later (spec §11).
"""
from __future__ import annotations

import threading
import traceback
from concurrent.futures import (FIRST_COMPLETED, ThreadPoolExecutor, wait)
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ScanUnit:
    """One independent piece of scan work.

    `run` is a zero-arg callable returning a dict with 'entities',
    'edges', 'findings' lists. `service` keys the per-service
    concurrency cap (e.g. 'ec2', 'iam').
    """
    name:    str
    service: str
    run:     Callable[[], dict]


@dataclass
class UnitOutcome:
    name:   str
    status: str            # 'success' | 'error' | 'timeout'
    detail: str = ""


@dataclass
class UnitResults:
    entities: list = field(default_factory=list)
    edges:    list = field(default_factory=list)
    findings: list = field(default_factory=list)
    outcomes: list[UnitOutcome] = field(default_factory=list)


class ConcurrencyLimiter:
    """Per-AWS-service bounded semaphores. A global max_workers is too
    blunt — services have different throttling limits — so each unit
    acquires its service's slot for the duration of its run."""

    def __init__(self, default: int = 8,
                 per_service: dict[str, int] | None = None):
        self._default = default
        self._per_service = per_service or {}
        self._sems: dict[str, threading.BoundedSemaphore] = {}
        self._lock = threading.Lock()

    def _sem(self, service: str) -> threading.BoundedSemaphore:
        with self._lock:
            sem = self._sems.get(service)
            if sem is None:
                cap = self._per_service.get(service, self._default)
                sem = threading.BoundedSemaphore(cap)
                self._sems[service] = sem
            return sem

    @contextmanager
    def acquire(self, service: str):
        sem = self._sem(service)
        sem.acquire()
        try:
            yield
        finally:
            sem.release()


def run_units(units: list[ScanUnit], *,
              limiter: ConcurrencyLimiter,
              max_workers: int = 16,
              batch_timeout: float | None = None) -> UnitResults:
    """Run every unit concurrently; merge results; isolate failures.

    A unit that raises is recorded `error`; one still running when
    `batch_timeout` elapses is recorded `timeout` (its eventual result
    is discarded — a straggler region never holds the scan hostage).
    Each unit holds its service's concurrency slot for its whole run.
    """
    results = UnitResults()
    if not units:
        return results

    def _wrapped(unit: ScanUnit) -> dict:
        with limiter.acquire(unit.service):
            return unit.run()

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_to_unit = {ex.submit(_wrapped, u): u for u in units}
        done, not_done = wait(future_to_unit, timeout=batch_timeout)

        for future in done:
            unit = future_to_unit[future]
            try:
                out = future.result()
                results.entities += out.get("entities", [])
                results.edges    += out.get("edges", [])
                results.findings += out.get("findings", [])
                results.outcomes.append(UnitOutcome(unit.name, "success"))
            except Exception as e:
                print(f"scan unit {unit.name} FAILED: {e}\n"
                      f"{traceback.format_exc()}")
                results.outcomes.append(
                    UnitOutcome(unit.name, "error", str(e)[:200]))

        for future in not_done:
            unit = future_to_unit[future]
            future.cancel()
            print(f"scan unit {unit.name} TIMED OUT (batch deadline)")
            results.outcomes.append(
                UnitOutcome(unit.name, "timeout", "exceeded batch deadline"))

    return results
```

(`FIRST_COMPLETED` is imported for clarity of intent though `wait` with a
timeout is used directly; remove the unused import if your linter
flags it — keep `wait`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_scan_pipeline.py -v`
Expected: PASS — 4 tests.

- [ ] **Step 5: Run the full suite and commit**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/ -q` — expect all pass.

```bash
git add platform/lambda/shasta_runner/app/scan_pipeline.py \
        platform/lambda/shasta_runner/app/tests/test_scan_pipeline.py
git commit -m "feat: add parallel scan-unit pipeline with per-service caps"
```

---

## Task 3: Four-state region footprint probe

Replace `region_discovery.py`'s binary `active`/`empty` classifier with the four-state footprint probe from spec §6 — `active` / `default_only` / `empty` / `unknown`.

**Files:**
- Rewrite: `app/region_discovery.py`
- Rewrite: `app/tests/test_region_discovery.py`

- [ ] **Step 1: Rewrite the test**

Replace the entire contents of `app/tests/test_region_discovery.py` with:

```python
# app/tests/test_region_discovery.py
"""Four-state region footprint probe: classify each enabled region
active / default_only / empty / unknown."""
import boto3
from botocore.stub import Stubber

from region_discovery import (RegionDiscovery, classify_region,
                              discover_regions, probe_region)


# ---- classify_region (pure) ----

def test_classify_active_when_real_resources():
    assert classify_region(has_real=True, has_default_vpc=True, errored=False) == "active"
    assert classify_region(has_real=True, has_default_vpc=False, errored=False) == "active"


def test_classify_default_only():
    assert classify_region(has_real=False, has_default_vpc=True, errored=False) == "default_only"


def test_classify_empty():
    assert classify_region(has_real=False, has_default_vpc=False, errored=False) == "empty"


def test_classify_unknown_on_error_regardless_of_signals():
    assert classify_region(has_real=True, has_default_vpc=True, errored=True) == "unknown"
    assert classify_region(has_real=False, has_default_vpc=False, errored=True) == "unknown"


# ---- probe_region ----

def _ec2_with(vpcs, instances):
    ec2 = boto3.client("ec2", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(ec2)
    stub.add_response("describe_vpcs", {"Vpcs": vpcs})
    stub.add_response("describe_instances",
                      {"Reservations": [{"Instances": instances}] if instances else []})
    stub.activate()
    return ec2


def _empty_client(service, op, key):
    c = boto3.client(service, region_name="us-east-1",
                     aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(c)
    stub.add_response(op, {key: []})
    stub.activate()
    return c


def _make_client_factory(ec2):
    """Returns make_client(service) — ec2 is the stubbed one; the other
    services return empty so the test isolates the VPC/EC2 signal."""
    empties = {
        "lambda": lambda: _empty_client("lambda", "list_functions", "Functions"),
        "rds":    lambda: _empty_client("rds", "describe_db_instances", "DBInstances"),
        "elbv2":  lambda: _empty_client("elbv2", "describe_load_balancers", "LoadBalancers"),
        "ecs":    lambda: _empty_client("ecs", "list_clusters", "clusterArns"),
        "eks":    lambda: _empty_client("eks", "list_clusters", "clusters"),
    }
    def _make(service):
        if service == "ec2":
            return ec2
        return empties[service]()
    return _make


def test_probe_region_active_with_nondefault_vpc():
    ec2 = _ec2_with([{"VpcId": "vpc-1", "IsDefault": False}], [])
    state = probe_region(_make_client_factory(ec2), "us-east-1")
    assert state == "active"


def test_probe_region_default_only():
    ec2 = _ec2_with([{"VpcId": "vpc-d", "IsDefault": True}], [])
    state = probe_region(_make_client_factory(ec2), "us-east-1")
    assert state == "default_only"


def test_probe_region_empty_when_no_vpc_no_resources():
    ec2 = _ec2_with([], [])
    state = probe_region(_make_client_factory(ec2), "us-east-1")
    assert state == "empty"


def test_probe_region_unknown_on_error():
    def _boom(service):
        raise RuntimeError("AccessDenied")
    assert probe_region(_boom, "us-east-1") == "unknown"


# ---- discover_regions ----

def test_discover_regions_builds_state_map():
    ec2 = boto3.client("ec2", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(ec2)
    stub.add_response("describe_regions",
                      {"Regions": [{"RegionName": "us-east-1"},
                                   {"RegionName": "eu-west-1"}]})
    stub.activate()

    def make_client_for_region(region):
        # both regions probe empty
        return _make_client_factory(_ec2_with([], []))

    rd = discover_regions(ec2, make_client_for_region)
    assert set(rd.region_states) == {"us-east-1", "eu-west-1"}
    assert rd.method == "footprint_probe"


def test_discover_regions_degrades_when_listing_fails():
    ec2 = boto3.client("ec2", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(ec2)
    stub.add_client_error("describe_regions", "UnauthorizedOperation")
    stub.activate()

    rd = discover_regions(ec2, lambda r: None)
    assert rd.method == "degraded_default"
    # degraded fallback regions are scanned conservatively as 'unknown'
    assert all(s == "unknown" for s in rd.region_states.values())
    assert "us-east-1" in rd.region_states
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_region_discovery.py -v`
Expected: FAIL — `ImportError` (`classify_region` / `probe_region` not defined).

- [ ] **Step 3: Rewrite `region_discovery.py`**

Replace the entire contents of `app/region_discovery.py` with:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_region_discovery.py -v`
Expected: PASS — all tests.

- [ ] **Step 5: Run the full suite and commit**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/ -q`
Expected: all pass. (No test imports `main.py`, so `main.py`'s now-stale use of the old `RegionDiscovery` shape does not break the suite — Task 7 fixes it.)

```bash
git add platform/lambda/shasta_runner/app/region_discovery.py \
        platform/lambda/shasta_runner/app/tests/test_region_discovery.py
git commit -m "feat: four-state region footprint probe"
```

---

## Task 4: The scan policy — (tier × region_state) → ScanPlan

**Files:**
- Create: `app/scan_policy.py`
- Test: `app/tests/test_scan_policy.py`

- [ ] **Step 1: Write the failing test**

```python
# app/tests/test_scan_policy.py
"""The scan policy turns (scan_tier, region states) into a ScanPlan:
which global modules run, and per region what depth — encoding the
spec §7.3 (tier × region_state) matrix."""
from scan_policy import build_scan_plan


def test_quick_runs_globals_and_coverage_no_regional_shasta_no_ai():
    plan = build_scan_plan("quick", {"us-east-1": "active", "eu-west-1": "active"})
    assert plan.run_global_enums is True
    assert plan.global_modules            # global Shasta modules run at Quick
    assert plan.run_ai_pass is False
    for region, rp in plan.per_region.items():
        assert rp.run_enums is True
        assert rp.coverage is True
        assert rp.regional_shasta is False     # Quick skips the 12 regional modules


def test_medium_active_region_is_full_depth():
    plan = build_scan_plan("medium", {"us-east-1": "active"})
    rp = plan.per_region["us-east-1"]
    assert rp.regional_shasta is True
    assert rp.coverage is True
    assert plan.run_ai_pass is True


def test_medium_default_only_region_is_shallow():
    plan = build_scan_plan("medium", {"ap-south-1": "default_only"})
    rp = plan.per_region["ap-south-1"]
    # default-only regions skip the heavy regional Shasta sweep even at Medium
    assert rp.regional_shasta is False
    assert rp.run_enums is True


def test_unknown_region_scanned_as_active():
    plan = build_scan_plan("medium", {"me-central-1": "unknown"})
    rp = plan.per_region["me-central-1"]
    # unknown is scanned conservatively, same depth as active
    assert rp.regional_shasta is True


def test_empty_region_still_present_but_minimal():
    plan = build_scan_plan("medium", {"ap-northeast-3": "empty"})
    rp = plan.per_region["ap-northeast-3"]
    assert rp.regional_shasta is False
    # every region is still in the plan — no silent drop
    assert "ap-northeast-3" in plan.per_region


def test_deep_active_region_adds_capabilities():
    plan = build_scan_plan("deep", {"us-east-1": "active"})
    rp = plan.per_region["us-east-1"]
    assert rp.regional_shasta is True
    assert plan.run_capabilities is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_scan_policy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scan_policy'`.

- [ ] **Step 3: Implement `scan_policy.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_scan_policy.py -v`
Expected: PASS — 6 tests.

- [ ] **Step 5: Run the full suite and commit**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/ -q` — expect all pass.

```bash
git add platform/lambda/shasta_runner/app/scan_policy.py \
        platform/lambda/shasta_runner/app/tests/test_scan_policy.py
git commit -m "feat: add (tier x region_state) scan policy"
```

---

## Task 5: Coverage engine per-region entry point

`run_coverage` loops regions internally. Add a per-region function so the handler's thread pool owns region concurrency.

**Files:**
- Modify: `app/coverage/engine.py`
- Modify: `app/tests/test_coverage_engine.py`

- [ ] **Step 1: Add the failing test**

Append to `app/tests/test_coverage_engine.py`:

```python
def test_run_coverage_for_region_scans_one_region(monkeypatch):
    from coverage import engine
    from coverage.model import Resource

    def fake_sqs_collect(client, *, account_id, region):
        return [Resource(service="sqs", resource_type="queue",
                         arn=f"arn:aws:sqs:{region}:111:q1", name="q1",
                         region=region, raw={})]
    monkeypatch.setitem(engine.COLLECTORS, "sqs", fake_sqs_collect)

    class _FakeSession:
        def client(self, name, **kwargs):
            return f"client:{name}"

    result = engine.run_coverage_for_region(
        _FakeSession(), "eu-west-1",
        account_id="111", tenant_id="t", scan_tier="quick")

    assert all(e.attributes["region"] == "eu-west-1"
               for e in result["entities"] if e.kind == "aws_sqs_queue")
    assert any(f.finding_type == "sqs-encryption-at-rest"
               for f in result["findings"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_coverage_engine.py::test_run_coverage_for_region_scans_one_region -v`
Expected: FAIL — `AttributeError: module 'coverage.engine' has no attribute 'run_coverage_for_region'`.

- [ ] **Step 3: Add the per-region entry point**

In `app/coverage/engine.py`, add this function (place it after `run_coverage`):

```python
def run_coverage_for_region(
    session: Any, region: str, *,
    account_id: str, tenant_id: str, scan_tier: str,
) -> dict[str, list]:
    """Run the tier-filtered coverage checks for ONE region.

    The handler's thread pool calls this once per region, so region
    concurrency lives in the handler — not here. `session` is a boto3
    session bound to `region`.
    """
    entities: list[EntityEmission] = []
    edges:    list[EdgeEmission]   = []
    findings: list[FindingEmission] = []

    checks = checks_for_tier(scan_tier)
    checks_by_service: dict[str, list] = {}
    for c in checks:
        checks_by_service.setdefault(c.service, []).append(c)

    for service, service_checks in checks_by_service.items():
        try:
            client = session.client(service, config=SCAN_BOTO_CONFIG)
            resources = COLLECTORS[service](
                client, account_id=account_id, region=region)
        except Exception as e:
            print(f"coverage/{service}@{region} collect FAILED: {e}\n"
                  f"{traceback.format_exc()}")
            continue

        for r in resources:
            kind = f"aws_{r.service}_{r.resource_type}"
            entities.append(EntityEmission(
                tenant_id=tenant_id, kind=kind, natural_key=r.arn,
                display_name=r.name, domain="cloud",
                attributes={"service": r.service, "account": account_id,
                            "region": r.region,
                            "resource_type": r.resource_type},
                evidence_packet=None,
                detector_id=_DETECTOR_ID, detector_version=_DETECTOR_VERSION,
            ))
            edges.append(EdgeEmission(
                tenant_id=tenant_id,
                source_kind="aws_account", source_natural_key=account_id,
                target_kind=kind, target_natural_key=r.arn,
                kind="contains", attributes={},
                evidence_packet={"version": "0.1", "via": "coverage_engine"},
                detector_id=_DETECTOR_ID, detector_version=_DETECTOR_VERSION,
            ))
            for check in service_checks:
                if check.resource_type != r.resource_type:
                    continue
                outcome = check.evaluate(r)
                findings.append(_to_finding(check, r, outcome, kind, tenant_id))

    return {"entities": entities, "edges": edges, "findings": findings}
```

Then replace the body of the existing `run_coverage` so it delegates to
the per-region function (keeping its signature for any other caller):

```python
def run_coverage(
    make_session: Callable[[str], Any], *,
    account_id: str, tenant_id: str,
    regions: list[str], scan_tier: str,
) -> dict[str, list]:
    """Run the coverage engine across `regions` (serial — the handler's
    pool is the parallel path; this is kept for direct/test use)."""
    merged: dict[str, list] = {"entities": [], "edges": [], "findings": []}
    for region in regions:
        part = run_coverage_for_region(
            make_session(region), region,
            account_id=account_id, tenant_id=tenant_id, scan_tier=scan_tier)
        for k in merged:
            merged[k] += part[k]
    return merged
```

- [ ] **Step 4: Run the coverage tests to verify they pass**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_coverage_engine.py -v`
Expected: PASS — all tests (the existing ones still pass; `run_coverage` now delegates).

- [ ] **Step 5: Run the full suite and commit**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/ -q` — expect all pass.

```bash
git add platform/lambda/shasta_runner/app/coverage/engine.py \
        platform/lambda/shasta_runner/app/tests/test_coverage_engine.py
git commit -m "feat: add per-region entry point to the coverage engine"
```

---

## Task 6: `scans.phase` migration

**Files:**
- Create: `platform/sql/010_scan_phase.sql`

- [ ] **Step 1: Write the migration**

```sql
-- platform/sql/010_scan_phase.sql
-- Scan execution v2: a `phase` field so the app can show what a running
-- scan is doing, not merely that it is running.
--
-- Values: region_discovery | first_signal | crown_jewel | full | done.
-- Existing rows predate phases — backfill to 'done' (they are historical
-- completed/failed scans, not in-flight).
--
-- See: docs/superpowers/specs/2026-05-21-scan-performance-design.md §10.1

BEGIN;

ALTER TABLE scans
  ADD COLUMN phase TEXT NOT NULL DEFAULT 'done'
  CHECK (phase IN ('region_discovery', 'first_signal', 'crown_jewel',
                   'full', 'done'));

COMMIT;
```

- [ ] **Step 2: Apply the migration to the dev Aurora cluster**

Run (the RDS Data API rejects multi-statement SQL — run the `ALTER TABLE` alone, without the `BEGIN;`/`COMMIT;` wrapper; Aurora auto-commits DDL):

```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot \
  --sql "ALTER TABLE scans ADD COLUMN phase TEXT NOT NULL DEFAULT 'done' CHECK (phase IN ('region_discovery','first_signal','crown_jewel','full','done'))"
```

Expected: `{"numberOfRecordsUpdated": 0}` (DDL; no error = success). If it reports the column already exists, treat as already-applied.

- [ ] **Step 3: Verify the column**

```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot \
  --sql "SELECT column_name FROM information_schema.columns WHERE table_name='scans' AND column_name='phase'"
```

Expected: one record, `phase`.

- [ ] **Step 4: Commit**

```bash
git add platform/sql/010_scan_phase.sql
git commit -m "feat: add phase column to scans table"
```

---

## Task 7: Rewrite the scanner handler as the three-stage pipeline

This is the integration task. The handler becomes: assume role → Stage 1+2 (region discovery) → build the `ScanPlan` → build `ScanUnit`s → run them through `scan_pipeline` (Quick in two phases with two commits) → emit the coverage map.

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Update imports**

In `app/main.py`'s `# === Entity-emission helpers (this module) ===` import block, the line `from region_discovery import discover_regions` stays. Add:

```python
from scan_pipeline   import ConcurrencyLimiter, ScanUnit, run_units
from scan_policy     import build_scan_plan
from coverage.engine import run_coverage_for_region
```

(Keep the existing `from coverage.engine import run_coverage` line — or replace it with the `run_coverage_for_region` import; `run_coverage` is no longer called by the handler after this task. Removing the unused import is fine.)

- [ ] **Step 2: Replace the `handler` function**

Replace the entire `handler` function (from `def handler(event, context) -> dict:` through its final `raise` in the `except` block) with:

```python
def handler(event: dict, context) -> dict:
    scan_id     = event["scan_id"]
    tenant_id   = event["tenant_id"]
    conn_id     = event["conn_id"]
    role_arn    = event["role_arn"]
    external_id = event["external_id"]
    account_id  = event["account_id"]
    explicit_regions = event.get("regions")
    scan_tier   = event.get("scan_tier", "quick")

    print(f"scan start: scan={scan_id} account={account_id} tier={scan_tier}")
    ctx = CloudScanContext(scan_id=scan_id, tenant_id=tenant_id, connection_id=conn_id)
    _update_scan(scan_id, status="running", phase="region_discovery")

    try:
        credentials = build_refreshable_credentials(sts, role_arn, external_id)
        boto_session = _make_session(credentials, "us-east-1")

        # --- Stage 1 + 2: region discovery -------------------------------
        if explicit_regions:
            region_states = {r: "active" for r in explicit_regions}
            discovery_method = "explicit_override"
            print(f"region scope: explicit override {list(explicit_regions)}")
        else:
            rd = discover_regions(
                boto_session.client("ec2", config=SCAN_BOTO_CONFIG),
                lambda region: (lambda service:
                    _make_session(credentials, region).client(
                        service, config=SCAN_BOTO_CONFIG)),
            )
            region_states = rd.region_states
            discovery_method = rd.method
            print(f"region discovery: method={discovery_method} "
                  f"states={region_states}")

        regions = sorted(region_states)
        plan = build_scan_plan(scan_tier, region_states)
        limiter = ConcurrencyLimiter(default=8, per_service={"iam": 3})

        # --- Stage 3: build + run scan units -----------------------------
        # coverage_map: region -> {state, modules_run, modules_skipped, errors}
        coverage_map = {r: {"state": region_states[r], "modules_run": [],
                            "modules_skipped": [], "errors": []}
                        for r in regions}
        entities: list[EntityEmission] = [_account_entity(account_id, tenant_id)]
        edges:    list[EdgeEmission]   = []
        findings: list[FindingEmission] = []

        global_units, region_units = _build_units(
            plan, credentials, account_id, tenant_id, regions, scan_tier)

        committed = 0
        if scan_tier == "quick":
            # Phase 1 — First Signal: global units, early commit.
            _update_scan(scan_id, status="running", phase="first_signal")
            r1 = run_units(global_units, limiter=limiter)
            _absorb(r1, entities, edges, findings, coverage_map)
            commit_scan(ctx, entities=list(entities), edges=list(edges),
                        findings=list(findings))
            committed = len(findings)
            print(f"quick phase 1 committed: {committed} findings")
            # Phase 2 — Crown Jewel: per-region units.
            _update_scan(scan_id, status="running", phase="crown_jewel")
            r2 = run_units(region_units, limiter=limiter)
            _absorb(r2, entities, edges, findings, coverage_map)
            commit_scan(ctx, entities=list(entities), edges=list(edges),
                        findings=list(findings))
        else:
            _update_scan(scan_id, status="running", phase="full")
            res = run_units(global_units + region_units, limiter=limiter)
            _absorb(res, entities, edges, findings, coverage_map)
            commit_scan(ctx, entities=list(entities), edges=list(edges),
                        findings=list(findings))

        # A scan with any unit error/timeout is 'partial', else 'completed'.
        had_gap = any(c["errors"] for c in coverage_map.values())
        final_status = "partial" if had_gap else "completed"
        _record_scan_scope(scan_id, scan_tier, discovery_method, coverage_map)
        _update_scan(scan_id, status=final_status, phase="done", stats={
            "entities": len(entities), "edges": len(edges),
            "findings": len(findings), "tier": scan_tier,
            "regions": regions,
        })
        print(f"scan complete ({final_status}): {len(entities)} entities, "
              f"{len(edges)} edges, {len(findings)} findings")
        return {"scan_id": scan_id, "status": final_status,
                "findings_written": len(findings)}

    except Exception as e:
        err = f"{e}: {traceback.format_exc()}"[:1000]
        print(f"SCAN FAILED: {err}")
        try:
            mark_scan_failed(ctx, err)
        except Exception:
            pass
        _update_scan(scan_id, status="failed", phase="done", error=err)
        raise
```

- [ ] **Step 3: Add the unit-building + absorb helpers**

In `app/main.py`, add these helper functions (place them after the `handler`, before `_make_session`):

```python
def _build_units(plan, credentials, account_id, tenant_id, regions, scan_tier):
    """Build the global and per-region ScanUnits for `plan`.

    Returns (global_units, region_units). Each unit's `run` returns
    {entities, edges, findings}; Shasta findings are converted to
    FindingEmission inside the unit (per-unit, no shared state — safe to
    run concurrently)."""
    global_units: list[ScanUnit] = []
    region_units: list[ScanUnit] = []

    # Global entity enums (IAM, S3).
    if plan.run_global_enums:
        global_units.append(ScanUnit(
            name="global/enum_iam", service="iam",
            run=lambda: _enum_unit(enumerate_iam,
                _make_session(credentials, "us-east-1").client("iam", config=SCAN_BOTO_CONFIG),
                account_id=account_id, tenant_id=tenant_id)))
        global_units.append(ScanUnit(
            name="global/enum_s3", service="s3",
            run=lambda: _enum_unit(enumerate_storage,
                _make_session(credentials, "us-east-1").client("s3", config=SCAN_BOTO_CONFIG),
                account_id=account_id, tenant_id=tenant_id)))

    # Global Shasta modules.
    if plan.global_modules:
        for name, run_fn in GLOBAL_MODULES:
            global_units.append(ScanUnit(
                name=f"global/{name}", service=name,
                run=_shasta_unit_fn(run_fn, credentials, "us-east-1",
                                    account_id, tenant_id, regions)))

    # AI pass (Medium+) — a single global unit.
    if plan.run_ai_pass:
        global_units.append(ScanUnit(
            name="global/ai_pass", service="ai",
            run=_ai_unit_fn(credentials, "us-east-1", account_id, tenant_id, regions)))

    # Per-region units.
    for region, rp in plan.per_region.items():
        if rp.run_enums:
            region_units.append(ScanUnit(
                name=f"{region}/enum_compute", service="ec2",
                run=_compute_enum_fn(credentials, region, account_id, tenant_id)))
            region_units.append(ScanUnit(
                name=f"{region}/enum_network", service="ec2",
                run=_network_enum_fn(credentials, region, account_id, tenant_id)))
        if rp.coverage:
            region_units.append(ScanUnit(
                name=f"{region}/coverage", service="coverage",
                run=_coverage_unit_fn(credentials, region, account_id,
                                      tenant_id, scan_tier)))
        if rp.regional_shasta:
            for name, run_fn in REGIONAL_MODULES:
                region_units.append(ScanUnit(
                    name=f"{region}/{name}", service=name,
                    run=_shasta_unit_fn(run_fn, credentials, region,
                                        account_id, tenant_id, regions)))
    return global_units, region_units


def _enum_unit(enum_fn, client, **kw) -> dict:
    out = enum_fn(client, **kw)
    return {"entities": out["entities"], "edges": out["edges"], "findings": []}


def _compute_enum_fn(credentials, region, account_id, tenant_id):
    def _run():
        s = _make_session(credentials, region)
        out = enumerate_compute(
            s.client("ec2", config=SCAN_BOTO_CONFIG),
            s.client("lambda", config=SCAN_BOTO_CONFIG),
            account_id=account_id, tenant_id=tenant_id, region=region)
        return {"entities": out["entities"], "edges": out["edges"], "findings": []}
    return _run


def _network_enum_fn(credentials, region, account_id, tenant_id):
    def _run():
        s = _make_session(credentials, region)
        out = enumerate_network(
            s.client("ec2", config=SCAN_BOTO_CONFIG),
            account_id=account_id, tenant_id=tenant_id, region=region)
        return {"entities": out["entities"], "edges": out["edges"], "findings": []}
    return _run


def _shasta_unit_fn(run_fn, credentials, region, account_id, tenant_id, regions):
    def _run():
        client = AssumedRoleAWSClient(credentials, region, account_id,
                                      scan_regions=regions)
        shasta_findings = run_fn(client)
        return convert_shasta_findings(shasta_findings, tenant_id, account_id)
    return _run


def _ai_unit_fn(credentials, region, account_id, tenant_id, regions):
    def _run():
        client = AssumedRoleAWSClient(credentials, region, account_id,
                                      scan_regions=regions)
        ai = run_ai_pass(client, account_id=account_id, tenant_id=tenant_id)
        return {"entities": ai["entities"], "edges": ai["edges"],
                "findings": ai["findings"]}
    return _run


def _coverage_unit_fn(credentials, region, account_id, tenant_id, scan_tier):
    def _run():
        return run_coverage_for_region(
            _make_session(credentials, region), region,
            account_id=account_id, tenant_id=tenant_id, scan_tier=scan_tier)
    return _run


def _absorb(results, entities, edges, findings, coverage_map):
    """Merge a run_units UnitResults into the scan accumulators and the
    coverage map. Unit name format is 'region/module' or 'global/module'."""
    entities.extend(results.entities)
    edges.extend(results.edges)
    findings.extend(results.findings)
    for o in results.outcomes:
        region = o.name.split("/", 1)[0]
        bucket = coverage_map.get(region)
        if bucket is None:           # 'global/...' units
            continue
        if o.status == "success":
            bucket["modules_run"].append(o.name)
        else:
            bucket["errors"].append(f"{o.status}: {o.name} {o.detail}".strip())
    return findings
```

- [ ] **Step 4: Convert `_convert_findings` to a pure per-unit function**

In `app/main.py`, replace the `_convert_findings(shasta_findings, tenant_id, account_id, entities, edges)` function (which mutates shared `entities`/`edges` lists) with a pure version that returns its own emissions — so it is safe to call from a worker thread:

```python
def convert_shasta_findings(shasta_findings: list[Any], tenant_id: str,
                            account_id: str) -> dict:
    """Convert Shasta Finding objects to a {entities, edges, findings}
    dict — pure, no shared state, safe to call concurrently. ARN-derived
    subject entities + 'contains' edges are emitted alongside; the
    writer's natural-key UPSERT dedupes overlaps across units."""
    out_findings: list[FindingEmission] = []
    out_entities: list[EntityEmission] = []
    out_edges:    list[EdgeEmission]   = []
    seen: set[tuple[str, str]] = set()

    for f in shasta_findings:
        if f.status.value.lower() in ("not_assessed", "not_applicable"):
            continue
        arn = (getattr(f, "resource_id", "") or "").strip()
        subj_kind = subj_nk = None
        parsed = parse_arn(arn) if arn else None
        if parsed:
            subj_kind, subj_nk = parsed["kind"], parsed["natural_key"]
            if (subj_kind, subj_nk) not in seen:
                seen.add((subj_kind, subj_nk))
                out_entities.append(EntityEmission(
                    tenant_id=tenant_id, kind=subj_kind, natural_key=subj_nk,
                    display_name=parsed["display_name"], domain="cloud",
                    attributes=parsed["attributes"], evidence_packet=None,
                    detector_id=f"{_DETECTOR_ID_BASE}.finding_arn",
                    detector_version="0.1.0"))
                out_edges.append(EdgeEmission(
                    tenant_id=tenant_id, source_kind="aws_account",
                    source_natural_key=account_id, target_kind=subj_kind,
                    target_natural_key=subj_nk, kind="contains", attributes={},
                    evidence_packet={"version": "0.1", "via": "finding.resource_id"},
                    detector_id=f"{_DETECTOR_ID_BASE}.finding_arn",
                    detector_version="0.1.0"))
        out_findings.append(_shasta_to_emission(f, tenant_id, subj_kind, subj_nk))

    return {"entities": out_entities, "edges": out_edges, "findings": out_findings}
```

`_shasta_to_emission` and `_safe_details` are unchanged. Delete the old `_convert_findings`.

- [ ] **Step 5: Update `_record_scan_scope` and `_update_scan`**

Replace `_record_scan_scope` with a version that writes the coverage map:

```python
def _record_scan_scope(scan_id: str, scan_tier: str, discovery_method: str,
                       coverage_map: dict) -> None:
    """Write the per-scan coverage map to scans.scope (spec §9)."""
    scope = {
        "tier": scan_tier,
        "discovery": {"method": discovery_method},
        "regions": coverage_map,
    }
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("UPDATE scans SET scope = CAST(:scope AS JSONB) "
             "WHERE scan_id = CAST(:sid AS UUID)"),
        parameters=[
            {"name": "sid",   "value": {"stringValue": scan_id}},
            {"name": "scope", "value": {"stringValue": json.dumps(scope)}},
        ],
    )
```

And give `_update_scan` a `phase` parameter — change its signature and add `phase` to the SQL when supplied:

```python
def _update_scan(scan_id: str, status: str, *, phase: str | None = None,
                  stats: dict | None = None, error: str | None = None) -> None:
    sql_parts = ["UPDATE scans SET status = :status"]
    params = [
        {"name": "sid",    "value": {"stringValue": scan_id}},
        {"name": "status", "value": {"stringValue": status}},
    ]
    if phase is not None:
        sql_parts.append("phase = :phase")
        params.append({"name": "phase", "value": {"stringValue": phase}})
    if status in ("completed", "failed", "partial"):
        sql_parts.append("finished_at = now()")
    if stats is not None:
        sql_parts.append("stats = CAST(:stats AS JSONB)")
        params.append({"name": "stats", "value": {"stringValue": json.dumps(stats)}})
    if error is not None:
        sql_parts.append("error = :error")
        params.append({"name": "error", "value": {"stringValue": error}})
    sql = ", ".join(sql_parts) + " WHERE scan_id = CAST(:sid AS UUID)"
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=sql, parameters=params)
```

Every `_update_scan(...)` call in the new `handler` already passes `phase=` as a keyword — consistent with this signature.

- [ ] **Step 6: Verify main.py parses and is wired**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -c "import ast; ast.parse(open('app/main.py').read()); print('main.py parses OK')"`
Expected: `main.py parses OK`.

Run: `grep -n "run_units\|build_scan_plan\|run_coverage_for_region\|convert_shasta_findings\|_build_units\|_absorb" app/main.py`
Expected: all present — the imports, the `handler`'s use of `build_scan_plan` / `run_units`, the `_build_units` helper, `_absorb`, `convert_shasta_findings`. Confirm the old `_convert_findings` is gone (`grep -n "_convert_findings" app/main.py` returns nothing).

- [ ] **Step 7: Run the full suite and commit**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/ -q`
Expected: all pass (no test imports `main.py`; the suite must stay green).

```bash
git add platform/lambda/shasta_runner/app/main.py
git commit -m "feat: rewrite scanner handler as the three-stage parallel pipeline"
```

---

## Task 8: Scan-status API

Expose a scan's `tier` / `status` / `phase` / coverage map so the app can poll progress.

**Files:**
- Create: `platform/lambda/scans_status/main.py`
- Modify: `platform/lib/api-stack.ts`

- [ ] **Step 1: Locate the API stack's route pattern**

Run: `grep -nE "addResource|addMethod|integration|LambdaIntegration|jwt|authorizer" platform/lib/api-stack.ts | head -40`

Read enough of `platform/lib/api-stack.ts` to see how an existing JWT-authed GET route is declared (e.g. how `findings_list` or `connections_list` is wired — its Lambda construct, its `addResource(...).addMethod("GET", ...)`, and the Cognito authorizer). The new route mirrors that exact pattern.

- [ ] **Step 2: Write the scan-status Lambda**

Create `platform/lambda/scans_status/main.py`:

```python
"""GET /v1/scans/{scan_id} — scan progress for the web/iOS app.

Returns the scan's tier, status, phase, the coverage map (scope), and
finding counts so the app can render an in-progress scan and label
results by scan type. JWT-authed; tenant-scoped.
"""
from __future__ import annotations

import json
import os

import boto3

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

rds_data = boto3.client("rds-data")


def handler(event: dict, context) -> dict:
    claims = (event.get("requestContext", {})
                   .get("authorizer", {}).get("jwt", {}).get("claims", {}))
    tenant_id = claims.get("custom:tenant_id")
    scan_id = (event.get("pathParameters") or {}).get("scan_id")
    if not tenant_id or not scan_id:
        return _resp(400, {"error": "missing_tenant_or_scan_id"})

    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("SELECT tier, status, phase, scope, started_at, finished_at "
             "FROM scans WHERE scan_id = CAST(:sid AS UUID) "
             "AND tenant_id = CAST(:tid AS UUID)"),
        parameters=[
            {"name": "sid", "value": {"stringValue": scan_id}},
            {"name": "tid", "value": {"stringValue": tenant_id}},
        ],
    )
    rows = rs.get("records", [])
    if not rows:
        return _resp(404, {"error": "scan_not_found"})
    r = rows[0]

    fc = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("SELECT count(*) FROM findings "
             "WHERE scan_id = CAST(:sid AS UUID)"),
        parameters=[{"name": "sid", "value": {"stringValue": scan_id}}],
    )
    finding_count = fc["records"][0][0].get("longValue", 0)

    scope_raw = r[3].get("stringValue") if not r[3].get("isNull") else None
    return _resp(200, {
        "scan_id":       scan_id,
        "tier":          r[0].get("stringValue"),
        "status":        r[1].get("stringValue"),
        "phase":         r[2].get("stringValue"),
        "coverage_map":  json.loads(scope_raw) if scope_raw else None,
        "started_at":    r[4].get("stringValue"),
        "finished_at":   r[5].get("stringValue") if not r[5].get("isNull") else None,
        "finding_count": finding_count,
    })


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json",
                    "access-control-allow-origin": "*"},
        "body": json.dumps(body),
    }
```

If, in Step 1, the existing routes read tenant from a different claim
key than `custom:tenant_id`, match the existing key — use whatever the
codebase's other tenant-scoped Lambdas use.

- [ ] **Step 3: Wire the route in `api-stack.ts`**

In `platform/lib/api-stack.ts`, following the pattern observed in Step 1, add:
- a `lambda.Function` (or `DockerImageFunction` / `PythonFunction` — whichever the file uses for the simple Python Lambdas) for `platform/lambda/scans_status`, with the `dbEnv` environment and `grantDataApiAccess`;
- a `GET` method on a `/v1/scans/{scan_id}` resource, with the same Cognito JWT authorizer the other authed GET routes use.

Match the surrounding code's construct style exactly (it is a mechanical mirror of, e.g., the `findings_list` route).

- [ ] **Step 4: Synthesize**

Run: `cd platform && npx cdk synth CisoCopilotApi`
Expected: synthesizes with no error; the synthesized template contains the new Lambda + the `/v1/scans/{scan_id}` GET method.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/scans_status/ platform/lib/api-stack.ts
git commit -m "feat: add GET /v1/scans/{id} scan-status endpoint"
```

---

## Task 9: Observability + Fargate task sizing

**Files:**
- Modify: `platform/lambda/shasta_runner/Dockerfile`
- Modify: `platform/lib/scan-stack.ts`

- [ ] **Step 1: Add `PYTHONUNBUFFERED` to the Dockerfile**

In `platform/lambda/shasta_runner/Dockerfile`, add this line immediately after the `FROM` line:

```dockerfile
# Stream stdout live to CloudWatch — the scanner block-buffers otherwise,
# making a running scan unobservable until it exits.
ENV PYTHONUNBUFFERED=1
```

- [ ] **Step 2: Resize the Fargate task**

In `platform/lib/scan-stack.ts`, find the `FargateTaskDefinition` for the scanner (`ScanTaskDef`, family `ciso-copilot-aws-scan`). Change `cpu: 2048` to `cpu: 4096` and `memoryLimitMiB: 4096` to `memoryLimitMiB: 8192`. Update the inline comment to note the work is I/O-bound but ~16 worker threads want the headroom.

- [ ] **Step 3: Synthesize**

Run: `cd platform && npx cdk synth CisoCopilotScan`
Expected: synthesizes cleanly; the task definition shows `Cpu: '4096'`, `Memory: '8192'`.

- [ ] **Step 4: Commit**

```bash
git add platform/lambda/shasta_runner/Dockerfile platform/lib/scan-stack.ts
git commit -m "chore: unbuffer scanner stdout; size Fargate task for parallel scan"
```

---

## Task 10: Build, deploy, and verify end-to-end

**Files:** none (build + deploy + verification).

- [ ] **Step 1: Rebuild and push the scanner image**

Run: `cd platform/lambda/shasta_runner && ./build.sh`
Expected: ends with `==> done. Image URI: ...:latest`.

- [ ] **Step 2: Deploy the changed stacks**

Run: `cd platform && npx cdk deploy CisoCopilotScan CisoCopilotApi --require-approval never`
Expected: both deploy — `CisoCopilotScan` picks up the resized task; `CisoCopilotApi` picks up the scan-status route.

- [ ] **Step 3: Run a MEDIUM-tier discovery scan**

Find an active AWS connection:

```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot \
  --sql "SELECT conn_id::text, tenant_id::text, account_identifier, credentials_secret_arn FROM cloud_connections WHERE cloud='aws' AND status='active' LIMIT 1"
```

Fetch `role_arn`/`external_id` from the connection's secret (`aws secretsmanager get-secret-value --secret-id <arn> --query SecretString --output text`), and the scan network config from the onboarding Lambda's env (`aws lambda list-functions --query 'Functions[?contains(FunctionName,\`OnboardingAwsComplete\`)].FunctionName' --output text`, then `aws lambda get-function-configuration --function-name <fn> --query 'Environment.Variables.{subnets:SCAN_SUBNET_IDS,sg:SCAN_SECURITY_GROUP_ID}'`).

Insert a scan row (`<scan-uuid-medium>` = a fresh UUID), then start the Fargate task — **omit `REGIONS`** so discovery runs:

```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot \
  --sql "INSERT INTO scans (scan_id, tenant_id, conn_id, trigger, status, tier, scope) VALUES (CAST('<scan-uuid-medium>' AS UUID), CAST('<tenant>' AS UUID), CAST('<conn>' AS UUID), 'manual', 'queued', 'medium', CAST('{}' AS JSONB))"

aws ecs run-task \
  --cluster ciso-copilot-scan --task-definition ciso-copilot-aws-scan \
  --launch-type FARGATE \
  --network-configuration 'awsvpcConfiguration={subnets=[<subnet1>,<subnet2>],securityGroups=[<sg-id>],assignPublicIp=DISABLED}' \
  --overrides '{"containerOverrides":[{"name":"scanner","environment":[
      {"name":"SCAN_ID","value":"<scan-uuid-medium>"},
      {"name":"TENANT_ID","value":"<tenant>"},
      {"name":"CONN_ID","value":"<conn>"},
      {"name":"ROLE_ARN","value":"<role_arn>"},
      {"name":"EXTERNAL_ID","value":"<external_id>"},
      {"name":"ACCOUNT_ID","value":"<account_id>"},
      {"name":"SCAN_TIER","value":"medium"}]}]}'
```

- [ ] **Step 4: Watch progress (logs now stream live)**

Poll the task: `aws ecs describe-tasks --cluster ciso-copilot-scan --tasks <task-arn> --query 'tasks[0].lastStatus'` every ~60s until `STOPPED`. With parallelism + adaptive retry, expect completion **well under 25 minutes** (the pre-v2 serial scan ran 108 min). The task logs (`PYTHONUNBUFFERED` now in effect) stream `scan start`, `region discovery`, per-unit lines, `scan complete (...)` live.

If the task is still RUNNING after 35 minutes, capture logs and report DONE_WITH_CONCERNS.

- [ ] **Step 5: Verify the outcome**

When STOPPED, confirm container `scanner` exitCode 0. Then:

```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot \
  --sql "SELECT status, phase, tier, scope FROM scans WHERE scan_id=CAST('<scan-uuid-medium>' AS UUID)"
```

Expected: `status` = `completed` (or `partial` if a region errored — both are valid), `phase` = `done`, `tier` = `medium`, `scope` = a JSON coverage map with a per-region `state` / `modules_run` / `errors` breakdown.

- [ ] **Step 6: Verify the scan-status API**

With a valid Cognito JWT for the tenant (obtain as the web app does, or via the existing auth flow), call `GET /v1/scans/<scan-uuid-medium>` and confirm it returns `tier`, `status`, `phase`, `coverage_map`, and `finding_count`. If obtaining a JWT is impractical in this environment, note that and confirm the Lambda instead via a direct `aws lambda invoke` with a synthetic authorizer-claims event.

- [ ] **Step 7: Run a QUICK-tier scan and confirm two phases**

Repeat Step 3 with a fresh `<scan-uuid-quick>`, `tier='quick'`, `SCAN_TIER=quick`. While it runs, poll the `scans` row's `phase` — expect it to pass through `region_discovery` → `first_signal` → `crown_jewel` → `done`. After `first_signal`, confirm findings already exist for the scan (Phase 1 committed early):

```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot \
  --sql "SELECT count(*) FROM findings WHERE scan_id=CAST('<scan-uuid-quick>' AS UUID)"
```

Expected: a non-zero count is observable while `phase` is still `crown_jewel` — proving Phase 1's early commit. Full Quick completes in ~3-5 min.

- [ ] **Step 8: No commit**

Verification only. If a step reveals a defect, report it — do not attempt code fixes; the controller triages.

---

## Self-review checklist (for the implementer, before declaring this plan done)

- [ ] `./.venv/bin/python -m pytest app/tests/ -q` — all green.
- [ ] `npx cdk synth CisoCopilotScan CisoCopilotApi` — clean.
- [ ] A Medium discovery scan completed in well under the pre-v2 time, wrote a coverage map to `scans.scope`, and finished `completed` or `partial` (never hung).
- [ ] A Quick scan visibly moved through the phases and committed Phase 1 findings before Phase 2 finished.
- [ ] `GET /v1/scans/{id}` returns tier/status/phase/coverage_map.
- [ ] No region was silently dropped — every enabled region appears in the coverage map.
