# Azure Scanner Uplift — Slice 1a: Azure Scanner Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Azure scanner (`platform/lambda/shasta_runner_azure/`) on the AWS "Scan Execution v2" architecture — a three-stage, parallel, tier-aware pipeline running on Fargate, writing through the unified entity model — verifiable by a manual `ecs run-task`.

**Architecture:** New Azure adapter modules wrap the 12 Shasta Azure check modules as `ScanUnit`s and feed them through the shared `scanner_core` pipeline. One Fargate task scans all selected subscriptions of a connection; subscription is the inner parallelism axis (as region is for AWS). Findings go through `unified_writer.commit_scan` (entities + edges + findings); scan state goes through `scanner_core.scan_state`.

**Tech Stack:** Python 3.12, pytest, boto3, Azure SDK, Shasta (read-only dependency), AWS Aurora Data API, Docker, ECS Fargate, AWS CDK (TypeScript).

**Spec:** `docs/superpowers/specs/2026-05-21-azure-scanner-uplift-design.md`

**Scope note:** This is Slice 1**a** — the scanner backend. It is verified by a manual `ecs run-task`. Slice 1**b** (rewiring the production triggers `onboarding_azure_complete` + `connections_list._rescan_azure` to `ecs:RunTask`, and the CDK API-stack env wiring) is a separate plan.

---

## File Structure

**Created (`platform/lambda/shasta_runner_azure/app/`):**
- `azure_id_to_entity.py` — parse an Azure Resource Manager ID → entity descriptor (Azure analog of `arn_to_entity.py`). Pure.
- `azure_findings.py` — `convert_azure_findings` + `subscription_entity`: Shasta `Finding` → `EntityEmission`/`EdgeEmission`/`FindingEmission`. Pure (duck-typed; no Shasta/Azure-SDK import).
- `subscription_discovery.py` — Stage 1+2: parallel per-subscription footprint probe → `active`/`empty`/`unknown`. Pure (probe injected as a callable).
- `azure_units.py` — the tier → Shasta-module map + `modules_for_tier`. Pure.
- `azure_credential.py` — `apply_sp_credentials`: inject the service-principal creds into `os.environ`. Pure.
- `run.py` — Fargate entrypoint (reads scan params from env vars).
- `tests/__init__.py`, `tests/conftest.py`, and one `test_*.py` per pure module.

**Modified:**
- `platform/lambda/shasta_runner_azure/app/main.py` — full rewrite: the three-stage orchestrator.
- `platform/lambda/shasta_runner_azure/build.sh` — copy `detectors/base.py` + `unified_writer.py` from `ai_scanner/`, and `scan_pipeline.py` + `scan_state.py` from `scanner_core/`.
- `platform/lambda/shasta_runner_azure/.gitignore` — ignore the build-time copies.
- `platform/lib/scan-stack.ts` — add an Azure Fargate task definition.

**Unchanged:** `shasta_runner_azure/app/framework_map.py` (already present), `Dockerfile` (Fargate overrides the entrypoint, same as the AWS scanner).

---

## Conventions

- **Shasta is read-only.** `/Users/kkmookhey/Projects/Shasta` is a reference dependency — never edit it.
- **Test venv:** the pure adapter modules are tested with the existing `shasta_runner` virtualenv (it has pytest + boto3 + pydantic): `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest <path> -q`. The pure modules deliberately do **not** import the Azure SDK or Shasta, so they need no Azure dependencies to test.
- **`main.py` is not unit-testable** — it imports `shasta.*` (not in the venv). It is verified by `py_compile`, an image build, and the live `ecs run-task` smoke scan (Task 9). This is the same documented constraint as the AWS scanner.
- **Git:** work on a branch. Before Task 1, create it: `git checkout -b feat/azure-scanner-slice-1a` from `main`. Commit after each task. Never `--no-verify`.

---

## Task 1: Azure scanner test scaffold + `azure_id_to_entity.py` (TDD)

`azure_id_to_entity.parse_azure_id` parses an Azure Resource Manager ID into an entity descriptor, mirroring `arn_to_entity.parse_arn`: it returns a descriptor for resource types in a curated `_KIND_MAP`, or `None` otherwise (caller keeps the finding, emits no entity).

**Files:**
- Create: `platform/lambda/shasta_runner_azure/app/tests/__init__.py`
- Create: `platform/lambda/shasta_runner_azure/app/tests/conftest.py`
- Create: `platform/lambda/shasta_runner_azure/app/azure_id_to_entity.py`
- Test: `platform/lambda/shasta_runner_azure/app/tests/test_azure_id_to_entity.py`

- [ ] **Step 1: Create the test package marker**

Create `platform/lambda/shasta_runner_azure/app/tests/__init__.py` as an empty file.

- [ ] **Step 2: Create the test conftest**

Create `platform/lambda/shasta_runner_azure/app/tests/conftest.py`:

```python
"""Make shasta_runner_azure/app modules importable by bare name in
tests. At runtime build.sh copies shared modules into app/
(detectors/base.py + unified_writer.py from ai_scanner; scan_pipeline.py
+ scan_state.py from scanner_core); for tests we add those source
directories to sys.path so the bare-name imports resolve."""
import sys
from pathlib import Path

_APP         = Path(__file__).resolve().parent.parent
_LAMBDA_ROOT = _APP.parent.parent
_AI_SCANNER  = _LAMBDA_ROOT / "ai_scanner"
_CORE        = _LAMBDA_ROOT / "scanner_core"

sys.path.insert(0, str(_APP))
sys.path.insert(0, str(_AI_SCANNER))
sys.path.insert(0, str(_CORE))
```

- [ ] **Step 3: Write the failing test**

Create `platform/lambda/shasta_runner_azure/app/tests/test_azure_id_to_entity.py`:

```python
"""azure_id_to_entity parses an Azure Resource Manager ID into an entity
descriptor, or returns None for IDs we have no kind mapping for."""
from azure_id_to_entity import parse_azure_id

_STORAGE = ("/subscriptions/sub-1/resourceGroups/rg-a/providers/"
            "Microsoft.Storage/storageAccounts/mystorage")
_VM = ("/subscriptions/sub-1/resourceGroups/rg-a/providers/"
       "Microsoft.Compute/virtualMachines/myvm")
_UNMAPPED = ("/subscriptions/sub-1/resourceGroups/rg-a/providers/"
             "Microsoft.Cdn/profiles/myprofile")


def test_parses_storage_account():
    out = parse_azure_id(_STORAGE)
    assert out["kind"] == "azure_storage_account"
    assert out["natural_key"] == _STORAGE
    assert out["display_name"] == "mystorage"
    assert out["attributes"]["subscription"] == "sub-1"
    assert out["attributes"]["resource_group"] == "rg-a"
    assert out["attributes"]["service"] == "azure"


def test_parses_virtual_machine():
    assert parse_azure_id(_VM)["kind"] == "azure_virtual_machine"


def test_case_insensitive_provider_match():
    lower = _STORAGE.replace("Microsoft.Storage", "microsoft.storage")
    assert parse_azure_id(lower)["kind"] == "azure_storage_account"


def test_unmapped_type_returns_none():
    assert parse_azure_id(_UNMAPPED) is None


def test_non_arm_string_returns_none():
    assert parse_azure_id("not-an-azure-id") is None
    assert parse_azure_id("arn:aws:s3:::bucket") is None


def test_empty_returns_none():
    assert parse_azure_id("") is None
    assert parse_azure_id(None) is None
```

- [ ] **Step 4: Run the test to verify it fails**

Run:
```bash
cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest \
  ../shasta_runner_azure/app/tests/test_azure_id_to_entity.py -q
```
Expected: FAIL — `ModuleNotFoundError: No module named 'azure_id_to_entity'`.

- [ ] **Step 5: Write `azure_id_to_entity.py`**

Create `platform/lambda/shasta_runner_azure/app/azure_id_to_entity.py`:

```python
"""Parse an Azure Resource Manager ID into an entity-emission shape —
the Azure analog of arn_to_entity.parse_arn.

ARM IDs look like:
  /subscriptions/<sub>/resourceGroups/<rg>/providers/<ns>/<type>/<name>

Returns {kind, natural_key, display_name, attributes} for resource types
in _KIND_MAP, or None otherwise — caller keeps the finding and emits no
entity (same contract as parse_arn).
"""
from __future__ import annotations

import re

_ARM_RE = re.compile(
    r"^/subscriptions/(?P<sub>[^/]+)"
    r"/resourceGroups/(?P<rg>[^/]+)"
    r"/providers/(?P<ns>[^/]+)/(?P<type>[^/]+)/(?P<name>[^/]+)$",
    re.IGNORECASE,
)

# (provider-namespace lower, resource-type lower) -> entity kind.
_KIND_MAP = {
    ("microsoft.storage", "storageaccounts"):       "azure_storage_account",
    ("microsoft.compute", "virtualmachines"):       "azure_virtual_machine",
    ("microsoft.compute", "disks"):                 "azure_managed_disk",
    ("microsoft.network", "virtualnetworks"):       "azure_virtual_network",
    ("microsoft.network", "networksecuritygroups"): "azure_network_security_group",
    ("microsoft.network", "publicipaddresses"):     "azure_public_ip",
    ("microsoft.keyvault", "vaults"):               "azure_key_vault",
    ("microsoft.sql", "servers"):                   "azure_sql_server",
    ("microsoft.dbforpostgresql", "servers"):       "azure_postgresql_server",
    ("microsoft.web", "sites"):                     "azure_app_service",
}


def parse_azure_id(resource_id: str | None) -> dict | None:
    """Return {kind, natural_key, display_name, attributes} or None."""
    if not resource_id or not isinstance(resource_id, str):
        return None
    m = _ARM_RE.match(resource_id.strip())
    if not m:
        return None
    kind = _KIND_MAP.get((m.group("ns").lower(), m.group("type").lower()))
    if kind is None:
        return None
    return {
        "kind":         kind,
        "natural_key":  resource_id,
        "display_name": m.group("name"),
        "attributes": {
            "service":        "azure",
            "namespace":      m.group("ns"),
            "resource_type":  m.group("type"),
            "subscription":   m.group("sub"),
            "resource_group": m.group("rg"),
        },
    }
```

- [ ] **Step 6: Run the test to verify it passes**

Run the Step 4 command. Expected: PASS — 6 tests.

- [ ] **Step 7: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/lambda/shasta_runner_azure/app/azure_id_to_entity.py \
        platform/lambda/shasta_runner_azure/app/tests/
git commit -m "$(cat <<'EOF'
feat: add azure_id_to_entity to the Azure scanner

Parses Azure Resource Manager IDs into entity descriptors — the Azure
analog of arn_to_entity. Curated kind map; None for unmapped types.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `azure_findings.py` (TDD)

`convert_azure_findings` turns Shasta `Finding` objects into the platform's `EntityEmission`/`EdgeEmission`/`FindingEmission` types — the Azure analog of `shasta_runner/app/main.py`'s `convert_shasta_findings` + `_shasta_to_emission`. It is pure: it operates on duck-typed `Finding` objects, so it imports neither Shasta nor the Azure SDK.

**Pre-check:** confirm `shasta_runner_azure/app/framework_map.py` exports `merge_framework_map(check_id: str, frameworks: dict) -> dict`. Run `grep -n "def merge_framework_map" platform/lambda/shasta_runner_azure/app/framework_map.py`. If the signature differs, adjust the call in Step 3 accordingly and note it.

**Files:**
- Create: `platform/lambda/shasta_runner_azure/app/azure_findings.py`
- Test: `platform/lambda/shasta_runner_azure/app/tests/test_azure_findings.py`

- [ ] **Step 1: Write the failing test**

Create `platform/lambda/shasta_runner_azure/app/tests/test_azure_findings.py`:

```python
"""azure_findings converts duck-typed Shasta Finding objects into the
platform's unified emission types."""
from dataclasses import dataclass, field

from azure_findings import convert_azure_findings, subscription_entity


class _Enum:
    """Stand-in for a Shasta StrEnum value (has .value)."""
    def __init__(self, value):
        self.value = value


@dataclass
class FakeFinding:
    check_id:          str
    title:             str
    description:       str
    severity:          object
    status:            object
    domain:            object
    resource_type:     str
    resource_id:       str
    region:            str = ""
    remediation:       str = ""
    soc2_controls:     list = field(default_factory=list)
    cis_aws_controls:  list = field(default_factory=list)
    cis_azure_controls: list = field(default_factory=list)
    cis_gcp_controls:  list = field(default_factory=list)
    mcsb_controls:     list = field(default_factory=list)
    iso27001_controls: list = field(default_factory=list)
    hipaa_controls:    list = field(default_factory=list)


_STORAGE_ID = ("/subscriptions/sub-1/resourceGroups/rg-a/providers/"
               "Microsoft.Storage/storageAccounts/mystorage")


def _finding(**kw):
    base = dict(
        check_id="azure-storage-001", title="Blob public access enabled",
        description="The storage account allows public blob access.",
        severity=_Enum("high"), status=_Enum("fail"), domain=_Enum("cloud"),
        resource_type="storageAccounts", resource_id=_STORAGE_ID,
        cis_azure_controls=["3.1"],
    )
    base.update(kw)
    return FakeFinding(**base)


def test_subscription_entity():
    e = subscription_entity("sub-1", "tenant-1")
    assert e.kind == "azure_subscription"
    assert e.natural_key == "sub-1"
    assert e.domain == "cloud"


def test_converts_a_failing_finding():
    out = convert_azure_findings([_finding()], "tenant-1", "sub-1")
    assert len(out["findings"]) == 1
    f = out["findings"][0]
    assert f.finding_type == "azure-storage-001"
    assert f.severity == "high"
    assert f.status == "fail"
    assert f.subject_entity_kind == "azure_storage_account"
    assert f.frameworks.get("cis_azure") == ["3.1"]


def test_emits_entity_and_edge_for_known_resource():
    out = convert_azure_findings([_finding()], "tenant-1", "sub-1")
    assert len(out["entities"]) == 1
    assert out["entities"][0].kind == "azure_storage_account"
    assert len(out["edges"]) == 1
    edge = out["edges"][0]
    assert edge.source_kind == "azure_subscription"
    assert edge.source_natural_key == "sub-1"
    assert edge.target_natural_key == _STORAGE_ID
    assert edge.kind == "contains"


def test_deduplicates_entities_across_findings():
    out = convert_azure_findings([_finding(), _finding()], "tenant-1", "sub-1")
    assert len(out["findings"]) == 2
    assert len(out["entities"]) == 1  # same resource, deduped


def test_unmapped_resource_keeps_finding_without_entity():
    f = _finding(resource_id="/subscriptions/sub-1/resourceGroups/rg/"
                 "providers/Microsoft.Cdn/profiles/p")
    out = convert_azure_findings([f], "tenant-1", "sub-1")
    assert len(out["findings"]) == 1
    assert out["findings"][0].subject_entity_kind is None
    assert out["entities"] == []


def test_skips_not_assessed_and_not_applicable():
    skipped = [_finding(status=_Enum("not_assessed")),
               _finding(status=_Enum("not_applicable"))]
    out = convert_azure_findings(skipped, "tenant-1", "sub-1")
    assert out["findings"] == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest \
  ../shasta_runner_azure/app/tests/test_azure_findings.py -q
```
Expected: FAIL — `ModuleNotFoundError: No module named 'azure_findings'`.

- [ ] **Step 3: Write `azure_findings.py`**

Create `platform/lambda/shasta_runner_azure/app/azure_findings.py`:

```python
"""Convert Shasta Azure Finding objects into the platform's unified
emission types — the Azure analog of shasta_runner/app/main.py's
convert_shasta_findings + _shasta_to_emission + _account_entity.

Pure: no Shasta or Azure-SDK import. Operates on duck-typed Finding
objects (anything with the Shasta Finding fields), so it is unit-testable
without the scanner runtime.
"""
from __future__ import annotations

from typing import Any

from azure_id_to_entity import parse_azure_id
from detectors.base import EdgeEmission, EntityEmission, FindingEmission
from framework_map import merge_framework_map

_DETECTOR_ID_BASE = "shasta_runner_azure"


def subscription_entity(subscription_id: str, tenant_id: str) -> EntityEmission:
    """The top-level entity for an Azure subscription."""
    return EntityEmission(
        tenant_id=tenant_id,
        kind="azure_subscription",
        natural_key=subscription_id,
        display_name=subscription_id,
        domain="cloud",
        attributes={"service": "azure", "subscription": subscription_id},
        evidence_packet=None,
        detector_id=f"{_DETECTOR_ID_BASE}.subscription",
        detector_version="0.1.0",
    )


def convert_azure_findings(shasta_findings: list[Any], tenant_id: str,
                           subscription_id: str) -> dict:
    """Convert Shasta Finding objects to {entities, edges, findings}.
    Pure, no shared state — safe to call concurrently per subscription.
    A resource ID that parses to a known kind also emits a subject
    entity + a `contains` edge from the subscription."""
    out_findings: list[FindingEmission] = []
    out_entities: list[EntityEmission] = []
    out_edges:    list[EdgeEmission]   = []
    seen: set[tuple[str, str]] = set()

    for f in shasta_findings:
        if f.status.value.lower() in ("not_assessed", "not_applicable"):
            continue
        rid = (getattr(f, "resource_id", "") or "").strip()
        subj_kind = subj_nk = None
        parsed = parse_azure_id(rid) if rid else None
        if parsed:
            subj_kind, subj_nk = parsed["kind"], parsed["natural_key"]
            if (subj_kind, subj_nk) not in seen:
                seen.add((subj_kind, subj_nk))
                out_entities.append(EntityEmission(
                    tenant_id=tenant_id, kind=subj_kind, natural_key=subj_nk,
                    display_name=parsed["display_name"], domain="cloud",
                    attributes=parsed["attributes"], evidence_packet=None,
                    detector_id=f"{_DETECTOR_ID_BASE}.finding_resource",
                    detector_version="0.1.0"))
                out_edges.append(EdgeEmission(
                    tenant_id=tenant_id, source_kind="azure_subscription",
                    source_natural_key=subscription_id, target_kind=subj_kind,
                    target_natural_key=subj_nk, kind="contains", attributes={},
                    evidence_packet={"version": "0.1", "via": "finding.resource_id"},
                    detector_id=f"{_DETECTOR_ID_BASE}.finding_resource",
                    detector_version="0.1.0"))
        out_findings.append(_to_emission(f, tenant_id, subj_kind, subj_nk))

    return {"entities": out_entities, "edges": out_edges, "findings": out_findings}


def _to_emission(f, tenant_id: str, subj_kind: str | None,
                 subj_nk: str | None) -> FindingEmission:
    frameworks = {
        "soc2":      f.soc2_controls,
        "cis_aws":   f.cis_aws_controls,
        "cis_azure": f.cis_azure_controls,
        "cis_gcp":   f.cis_gcp_controls,
        "mcsb":      f.mcsb_controls,
        "iso27001":  f.iso27001_controls,
        "hipaa":     f.hipaa_controls,
    }
    frameworks = {k: v for k, v in frameworks.items() if v}
    frameworks = merge_framework_map(f.check_id, frameworks)

    status = f.status.value.lower()
    domain = f.domain.value.lower()
    if domain == "ai_governance":
        domain = "ai"
    region = f.region or None

    evidence = {
        "version": "0.1",
        "shasta": {
            "check_id":      f.check_id,
            "status":        status,
            "domain":        domain,
            "region":        f.region,
            "resource_type": f.resource_type,
            "resource_id":   f.resource_id,
            "remediation":   (f.remediation or "")[:2000],
            "frameworks":    frameworks,
        },
    }
    return FindingEmission(
        tenant_id=tenant_id,
        finding_type=f.check_id,
        severity=f.severity.value.lower(),
        title=f.title[:500],
        description=(f.description or "")[:2000],
        subject_entity_kind=subj_kind,
        subject_entity_natural_key=subj_nk,
        subject_type=f.resource_type[:200] if f.resource_type else None,
        subject_ref=(f.resource_id or "")[:500] if f.resource_id else None,
        evidence_packet=evidence,
        confidence="high",
        frameworks=frameworks,
        domain=domain,
        status=status,
        region=region,
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run the Step 2 command. Expected: PASS — 6 tests.

- [ ] **Step 5: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/lambda/shasta_runner_azure/app/azure_findings.py \
        platform/lambda/shasta_runner_azure/app/tests/test_azure_findings.py
git commit -m "$(cat <<'EOF'
feat: add azure_findings — Shasta Finding -> unified emissions

Azure analog of convert_shasta_findings: emits FindingEmission plus
subject entities + contains edges from the subscription. Pure and
duck-typed, no Shasta/Azure-SDK import.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `subscription_discovery.py` (TDD)

Stage 1+2 of the pipeline. Stage 1 is the selected subscription list (passed in). Stage 2 is a parallel per-subscription footprint probe. The probe itself is injected as a callable so this module stays pure and testable; the concrete probe (which uses the Azure SDK) is wired in `main.py` (Task 6). Anti-blind-spot invariant: any probe exception → `unknown`, never silently dropped or mislabelled `empty`.

**Files:**
- Create: `platform/lambda/shasta_runner_azure/app/subscription_discovery.py`
- Test: `platform/lambda/shasta_runner_azure/app/tests/test_subscription_discovery.py`

- [ ] **Step 1: Write the failing test**

Create `platform/lambda/shasta_runner_azure/app/tests/test_subscription_discovery.py`:

```python
"""subscription_discovery probes each selected subscription in parallel
and classifies it active / empty / unknown."""
from subscription_discovery import discover_subscriptions


def test_classifies_from_probe_results():
    def probe(sub_id):
        return {"s-active": "active", "s-empty": "empty"}[sub_id]
    states = discover_subscriptions(["s-active", "s-empty"], probe)
    assert states == {"s-active": "active", "s-empty": "empty"}


def test_probe_exception_yields_unknown():
    def probe(sub_id):
        if sub_id == "s-bad":
            raise RuntimeError("boom")
        return "active"
    states = discover_subscriptions(["s-ok", "s-bad"], probe)
    assert states == {"s-ok": "active", "s-bad": "unknown"}


def test_unexpected_probe_value_yields_unknown():
    states = discover_subscriptions(["s-1"], lambda s: "garbage")
    assert states == {"s-1": "unknown"}


def test_empty_input():
    assert discover_subscriptions([], lambda s: "active") == {}
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest \
  ../shasta_runner_azure/app/tests/test_subscription_discovery.py -q
```
Expected: FAIL — `ModuleNotFoundError: No module named 'subscription_discovery'`.

- [ ] **Step 3: Write `subscription_discovery.py`**

Create `platform/lambda/shasta_runner_azure/app/subscription_discovery.py`:

```python
"""Stage 1 + 2 of the Azure scan pipeline.

Stage 1: the selected subscription list is passed in (the connection
already chose it).
Stage 2: a parallel per-subscription footprint probe classifies each
subscription `active` / `empty` / `unknown`.

The probe is injected as a callable so this module stays pure and
unit-testable; the concrete Azure-SDK probe lives in main.py.

Anti-blind-spot invariant: any probe failure — an exception, or a
probe return value that is not `active`/`empty` — classifies the
subscription `unknown`. A subscription is never silently dropped, and a
probe error is never mislabelled `empty`.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable

_VALID = ("active", "empty")


def discover_subscriptions(
    subscription_ids: list[str],
    probe: Callable[[str], str],
    *,
    max_workers: int = 8,
) -> dict[str, str]:
    """Probe each subscription concurrently. `probe(sub_id)` returns
    `active` or `empty`; any exception or other value -> `unknown`.
    Returns {subscription_id: 'active' | 'empty' | 'unknown'}."""
    if not subscription_ids:
        return {}

    def _probe_one(sub_id: str) -> tuple[str, str]:
        try:
            state = probe(sub_id)
            if state not in _VALID:
                print(f"subscription probe {sub_id}: unexpected state "
                      f"{state!r} -> unknown")
                return (sub_id, "unknown")
            return (sub_id, state)
        except Exception as e:
            print(f"subscription probe {sub_id} FAILED: {e} -> unknown")
            return (sub_id, "unknown")

    states: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for sub_id, state in ex.map(_probe_one, subscription_ids):
            states[sub_id] = state
    return states
```

- [ ] **Step 4: Run the test to verify it passes**

Run the Step 2 command. Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/lambda/shasta_runner_azure/app/subscription_discovery.py \
        platform/lambda/shasta_runner_azure/app/tests/test_subscription_discovery.py
git commit -m "$(cat <<'EOF'
feat: add subscription_discovery — Azure footprint probe

Parallel per-subscription probe -> active/empty/unknown. Probe injected
as a callable so the module is pure and testable. Anti-blind-spot: any
probe failure classifies the subscription unknown, never empty.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `azure_units.py` (TDD)

The tier → Shasta-module mapping. Quick is two-phase (first-signal then crown-jewel, for early commit); Medium and Deep run everything in phase 1. Pure data + logic — `main.py` maps the module-name strings to the actual Shasta functions.

**Files:**
- Create: `platform/lambda/shasta_runner_azure/app/azure_units.py`
- Test: `platform/lambda/shasta_runner_azure/app/tests/test_azure_units.py`

- [ ] **Step 1: Write the failing test**

Create `platform/lambda/shasta_runner_azure/app/tests/test_azure_units.py`:

```python
"""azure_units maps a scan tier to which Shasta Azure modules run, split
into the two Quick phases."""
import pytest

from azure_units import ALL_MODULES, modules_for_tier


def test_quick_is_two_phase():
    p1, p2 = modules_for_tier("quick")
    assert p1 == ["iam", "governance"]
    assert p2 == ["storage", "networking", "compute", "encryption"]


def test_medium_is_single_phase_nine_modules():
    p1, p2 = modules_for_tier("medium")
    assert p2 == []
    assert len(p1) == 9
    assert set(p1) == {"iam", "governance", "storage", "networking",
                       "compute", "encryption", "databases",
                       "appservice", "monitoring"}


def test_deep_is_single_phase_all_twelve():
    p1, p2 = modules_for_tier("deep")
    assert p2 == []
    assert len(p1) == 12
    assert set(p1) == set(ALL_MODULES)


def test_tier_is_case_insensitive():
    assert modules_for_tier("QUICK") == modules_for_tier("quick")


def test_unknown_tier_raises():
    with pytest.raises(ValueError):
        modules_for_tier("turbo")
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest \
  ../shasta_runner_azure/app/tests/test_azure_units.py -q
```
Expected: FAIL — `ModuleNotFoundError: No module named 'azure_units'`.

- [ ] **Step 3: Write `azure_units.py`**

Create `platform/lambda/shasta_runner_azure/app/azure_units.py`:

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run the Step 2 command. Expected: PASS — 5 tests.

- [ ] **Step 5: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/lambda/shasta_runner_azure/app/azure_units.py \
        platform/lambda/shasta_runner_azure/app/tests/test_azure_units.py
git commit -m "$(cat <<'EOF'
feat: add azure_units — tier -> Shasta-module mapping

Quick is two-phase (iam/governance then storage/networking/compute/
encryption); Medium and Deep are single-phase. Pure logic.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `azure_credential.py` (TDD) + `run.py`

`azure_credential.apply_sp_credentials` injects the service-principal credentials into `os.environ` so Shasta's `AzureClient` (which uses `DefaultAzureCredential`) picks them up. All selected subscriptions share one SP, so the values are connection-constant — the `os.environ` write is done once at process start and is safe even though scan units run in parallel threads.

`run.py` is the Fargate entrypoint — it reads scan parameters from env vars and calls `main.handler`, mirroring the AWS scanner's `run.py`.

**Files:**
- Create: `platform/lambda/shasta_runner_azure/app/azure_credential.py`
- Create: `platform/lambda/shasta_runner_azure/app/run.py`
- Test: `platform/lambda/shasta_runner_azure/app/tests/test_azure_credential.py`
- Test: `platform/lambda/shasta_runner_azure/app/tests/test_run.py`

- [ ] **Step 1: Write the failing tests**

Create `platform/lambda/shasta_runner_azure/app/tests/test_azure_credential.py`:

```python
"""azure_credential.apply_sp_credentials injects the SP credentials from
the connection secret into os.environ."""
import os

from azure_credential import apply_sp_credentials


def test_injects_all_three_env_vars(monkeypatch):
    for k in ("AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_TENANT_ID"):
        monkeypatch.delenv(k, raising=False)
    apply_sp_credentials({
        "client_id": "appid-1",
        "client_secret": "secret-1",
        "azure_tenant_id": "tenant-1",
    })
    assert os.environ["AZURE_CLIENT_ID"] == "appid-1"
    assert os.environ["AZURE_CLIENT_SECRET"] == "secret-1"
    assert os.environ["AZURE_TENANT_ID"] == "tenant-1"
```

Create `platform/lambda/shasta_runner_azure/app/tests/test_run.py`:

```python
"""run.build_event maps Fargate env vars to the handler event dict."""
import pytest

from run import build_event


def _env(**over):
    base = {
        "SCAN_ID": "scan-1", "TENANT_ID": "tenant-1", "CONN_ID": "conn-1",
        "AZURE_TENANT_ID": "az-tenant-1", "CLIENT_ID": "appid-1",
        "SECRET_ARN": "arn:secret", "SUBSCRIPTION_IDS": "sub-a,sub-b",
    }
    base.update(over)
    return base


def test_build_event_maps_all_fields():
    e = build_event(_env())
    assert e["scan_id"] == "scan-1"
    assert e["tenant_id"] == "tenant-1"
    assert e["conn_id"] == "conn-1"
    assert e["azure_tenant_id"] == "az-tenant-1"
    assert e["client_id"] == "appid-1"
    assert e["secret_arn"] == "arn:secret"
    assert e["subscription_ids"] == ["sub-a", "sub-b"]
    assert e["scan_tier"] == "quick"  # default


def test_build_event_respects_scan_tier():
    assert build_event(_env(SCAN_TIER="medium"))["scan_tier"] == "medium"


def test_build_event_splits_and_strips_subscription_ids():
    e = build_event(_env(SUBSCRIPTION_IDS=" sub-a , sub-b ,"))
    assert e["subscription_ids"] == ["sub-a", "sub-b"]


def test_build_event_missing_required_key_raises():
    env = _env()
    del env["SCAN_ID"]
    with pytest.raises(KeyError):
        build_event(env)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest \
  ../shasta_runner_azure/app/tests/test_azure_credential.py \
  ../shasta_runner_azure/app/tests/test_run.py -q
```
Expected: FAIL — `ModuleNotFoundError` for `azure_credential` and `run`.

- [ ] **Step 3: Write `azure_credential.py`**

Create `platform/lambda/shasta_runner_azure/app/azure_credential.py`:

```python
"""Service-principal credential setup for the Azure scanner.

All selected subscriptions of a connection share ONE service principal,
so the SP credentials are connection-constant. They are injected into
os.environ once at process start; Shasta's AzureClient then picks them
up via DefaultAzureCredential. Because the values are constant for the
whole run, the os.environ write is safe even though the scan units run
in parallel threads.
"""
from __future__ import annotations

import os


def apply_sp_credentials(secret: dict) -> None:
    """Inject the service-principal credentials from the connection
    secret JSON into os.environ for DefaultAzureCredential to consume.
    `secret` must carry `client_id`, `client_secret`, `azure_tenant_id`.
    """
    os.environ["AZURE_CLIENT_ID"]     = secret["client_id"]
    os.environ["AZURE_CLIENT_SECRET"] = secret["client_secret"]
    os.environ["AZURE_TENANT_ID"]     = secret["azure_tenant_id"]
```

- [ ] **Step 4: Write `run.py`**

Create `platform/lambda/shasta_runner_azure/app/run.py`:

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run the Step 2 command. Expected: PASS — 1 + 4 tests.

- [ ] **Step 6: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/lambda/shasta_runner_azure/app/azure_credential.py \
        platform/lambda/shasta_runner_azure/app/run.py \
        platform/lambda/shasta_runner_azure/app/tests/test_azure_credential.py \
        platform/lambda/shasta_runner_azure/app/tests/test_run.py
git commit -m "$(cat <<'EOF'
feat: add azure_credential + run.py (Fargate entrypoint)

apply_sp_credentials injects the connection-constant SP creds into
os.environ for DefaultAzureCredential. run.py maps Fargate env vars to
the handler event.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Rewrite `main.py` — the three-stage orchestrator

Replace the legacy single-pass Azure scanner with the v2 orchestrator: probe subscriptions, build subscription × Shasta-module `ScanUnit`s, run them through `scanner_core.run_units`, write via `unified_writer.commit_scan`, track state via `scanner_core.scan_state`. Two-phase Quick (early commit), single-phase Medium/Deep.

`main.py` imports `shasta.*` and the Azure SDK, so it is not unit-testable; it is verified by `py_compile` here and the live scan in Task 9.

**Files:**
- Modify (full rewrite): `platform/lambda/shasta_runner_azure/app/main.py`

- [ ] **Step 1: Replace `main.py` entirely**

Overwrite `platform/lambda/shasta_runner_azure/app/main.py` with:

```python
"""shasta-runner-azure — runs Shasta's Azure checks across a customer's
selected subscriptions.

Invoked as a Fargate task (via run.py) or directly as a Lambda with:
  {
    "scan_id": "uuid", "tenant_id": "uuid", "conn_id": "uuid",
    "azure_tenant_id": "<customer Entra tenant>",
    "client_id": "<SP appId>", "secret_arn": "<Secrets Manager ARN>",
    "subscription_ids": ["<sub>", ...], "scan_tier": "quick|medium|deep"
  }

Three stages (spec sections 4-5):
  1. Subscription eligibility — the selected subscription list.
  2. Footprint probe — per-subscription active/empty/unknown.
  3. Tier-aware parallel scan — subscription x Shasta-module ScanUnits
     through scanner_core.run_units; two-phase Quick early-commit.
"""
from __future__ import annotations

import json
import traceback
from dataclasses import dataclass

import boto3

# === Shasta imports ===
from shasta.azure.client import AzureClient
from shasta.azure.appservice         import run_all_azure_appservice_checks
from shasta.azure.backup             import run_all_azure_backup_checks
from shasta.azure.compute            import run_all_azure_compute_checks
from shasta.azure.databases          import run_all_azure_database_checks
from shasta.azure.diagnostic_settings import run_all_azure_diagnostic_settings_checks
from shasta.azure.encryption         import run_all_azure_encryption_checks
from shasta.azure.governance         import run_all_azure_governance_checks
from shasta.azure.iam                import run_all_azure_iam_checks
from shasta.azure.monitoring         import run_all_azure_monitoring_checks
from shasta.azure.networking         import run_all_azure_networking_checks
from shasta.azure.private_endpoints  import run_all_azure_private_endpoint_checks
from shasta.azure.storage            import run_all_azure_storage_checks

# === Adapter modules (this package) ===
from azure_credential       import apply_sp_credentials
from azure_findings         import convert_azure_findings, subscription_entity
from azure_units            import modules_for_tier
from subscription_discovery import discover_subscriptions

# === Shared modules (copied in by build.sh) ===
from detectors.base import EntityEmission
from scan_pipeline  import ConcurrencyLimiter, ScanUnit, run_units
from scan_state     import record_scan_scope, update_scan
from unified_writer import commit_scan, mark_scan_failed

_SCANNER_VERSION  = "shasta_runner_azure.0.2.0"

sm = boto3.client("secretsmanager")

# Module name -> Shasta entry point. Each takes an AzureClient, returns
# list[Finding]. The names match azure_units' tier lists.
AZURE_MODULES = {
    "iam":                 run_all_azure_iam_checks,
    "governance":          run_all_azure_governance_checks,
    "storage":             run_all_azure_storage_checks,
    "networking":          run_all_azure_networking_checks,
    "compute":             run_all_azure_compute_checks,
    "encryption":          run_all_azure_encryption_checks,
    "databases":           run_all_azure_database_checks,
    "appservice":          run_all_azure_appservice_checks,
    "monitoring":          run_all_azure_monitoring_checks,
    "backup":              run_all_azure_backup_checks,
    "diagnostic_settings": run_all_azure_diagnostic_settings_checks,
    "private_endpoints":   run_all_azure_private_endpoint_checks,
}

# Subscriptions in these states are scanned; `empty` is skipped.
_SCANNABLE = ("active", "unknown")


@dataclass(frozen=True)
class CloudScanContext:
    """Minimal ScanContext for unified_writer (reads these by attr)."""
    scan_id:         str
    tenant_id:       str
    connection_id:   str
    scanner_version: str = _SCANNER_VERSION


def handler(event: dict, context) -> dict:
    scan_id          = event["scan_id"]
    tenant_id        = event["tenant_id"]
    conn_id          = event["conn_id"]
    azure_tenant_id  = event["azure_tenant_id"]
    secret_arn       = event["secret_arn"]
    subscription_ids = event["subscription_ids"]
    scan_tier        = event.get("scan_tier", "quick")

    print(f"azure scan start: scan={scan_id} tier={scan_tier} "
          f"subs={subscription_ids}")
    ctx = CloudScanContext(scan_id=scan_id, tenant_id=tenant_id,
                           connection_id=conn_id)
    update_scan(scan_id, status="running", phase="region_discovery")

    try:
        # --- Credentials: one SP, shared by every subscription ----------
        secret = json.loads(
            sm.get_secret_value(SecretId=secret_arn)["SecretString"])
        apply_sp_credentials(secret)
        base_client = AzureClient(tenant_id=azure_tenant_id)

        # --- Stage 1 + 2: subscription discovery ------------------------
        def _probe(sub_id: str) -> str:
            c = base_client.for_subscription(sub_id)
            c.validate_credentials()              # raises if unreachable
            return "active" if c.discover_services() else "empty"

        states = discover_subscriptions(subscription_ids, _probe)
        print(f"subscription discovery: {states}")
        scannable = [s for s, st in states.items() if st in _SCANNABLE]

        # --- Stage 3: build + run scan units ----------------------------
        phase1_mods, phase2_mods = modules_for_tier(scan_tier)
        limiter = ConcurrencyLimiter(default=8)
        coverage_map = {s: {"state": states[s], "modules_run": [],
                            "errors": []} for s in subscription_ids}

        entities: list[EntityEmission] = [
            subscription_entity(s, tenant_id) for s in scannable]
        edges: list = []
        findings: list = []

        phase1_units = _build_units(scannable, phase1_mods, base_client,
                                    tenant_id, azure_tenant_id)
        phase2_units = _build_units(scannable, phase2_mods, base_client,
                                    tenant_id, azure_tenant_id)

        if scan_tier.lower() == "quick":
            update_scan(scan_id, status="running", phase="first_signal")
            r1 = run_units(phase1_units, limiter=limiter)
            _absorb(r1, entities, edges, findings, coverage_map)
            commit_scan(ctx, entities=list(entities), edges=list(edges),
                        findings=list(findings))
            print(f"quick phase 1 committed: {len(findings)} findings")
            update_scan(scan_id, status="running", phase="crown_jewel")
            r2 = run_units(phase2_units, limiter=limiter)
            _absorb(r2, entities, edges, findings, coverage_map)
            commit_scan(ctx, entities=list(entities), edges=list(edges),
                        findings=list(findings))
        else:
            update_scan(scan_id, status="running", phase="full")
            res = run_units(phase1_units + phase2_units, limiter=limiter)
            _absorb(res, entities, edges, findings, coverage_map)
            commit_scan(ctx, entities=list(entities), edges=list(edges),
                        findings=list(findings))

        had_gap = any(c["errors"] for c in coverage_map.values())
        final_status = "partial" if had_gap else "completed"
        record_scan_scope(scan_id, {
            "tier": scan_tier,
            "subscriptions": coverage_map,
        })
        update_scan(scan_id, status=final_status, phase="done", stats={
            "entities": len(entities), "edges": len(edges),
            "findings": len(findings), "tier": scan_tier,
            "subscriptions": scannable,
        })
        print(f"azure scan complete ({final_status}): {len(entities)} "
              f"entities, {len(edges)} edges, {len(findings)} findings")
        return {"scan_id": scan_id, "status": final_status,
                "findings_written": len(findings)}

    except Exception as e:
        err = f"{e}: {traceback.format_exc()}"[:1000]
        print(f"AZURE SCAN FAILED: {err}")
        try:
            mark_scan_failed(ctx, err)
        except Exception:
            pass
        update_scan(scan_id, status="failed", phase="done", error=err)
        raise


def _build_units(subscriptions: list[str], module_names: list[str],
                 base_client, tenant_id: str,
                 azure_tenant_id: str) -> list[ScanUnit]:
    """One ScanUnit per (subscription, module). Each unit builds its own
    AzureClient inside `run` — fresh per unit, mirroring the AWS
    scanner's per-unit client — so concurrent units never share a
    mutable Azure SDK client."""
    units: list[ScanUnit] = []
    for sub_id in subscriptions:
        for name in module_names:
            run_fn = AZURE_MODULES[name]
            units.append(ScanUnit(
                name=f"{sub_id}/{name}", service=name,
                run=_module_unit(run_fn, base_client, sub_id,
                                 tenant_id)))
    return units


def _module_unit(run_fn, base_client, sub_id: str, tenant_id: str):
    """Build the `run` callable for one (subscription, module) unit."""
    def _run() -> dict:
        client = base_client.for_subscription(sub_id)
        client.validate_credentials()
        shasta_findings = run_fn(client)
        return convert_azure_findings(shasta_findings, tenant_id, sub_id)
    return _run


def _absorb(results, entities, edges, findings, coverage_map) -> None:
    """Merge a run_units UnitResults into the accumulators + coverage
    map. Unit name format is `<subscription_id>/<module>`."""
    entities.extend(results.entities)
    edges.extend(results.edges)
    findings.extend(results.findings)
    for o in results.outcomes:
        sub_id = o.name.split("/", 1)[0]
        bucket = coverage_map.get(sub_id)
        if bucket is None:
            continue
        if o.status == "success":
            bucket["modules_run"].append(o.name)
        else:
            bucket["errors"].append(
                f"{o.status}: {o.name} {o.detail}".strip())
```

- [ ] **Step 2: Syntax-check `main.py`**

Run:
```bash
cd platform/lambda/shasta_runner_azure && \
  python3 -m py_compile app/main.py && echo "py_compile OK"
```
Expected: `py_compile OK`. (This catches syntax errors. It does not import `shasta.*`, so it does not fully load the module — expected.)

- [ ] **Step 3: Confirm the legacy direct-`findings` write path is gone**

Run:
```bash
cd platform/lambda/shasta_runner_azure && \
  grep -n "batch_execute_statement\|_insert_findings\|_FINDING_INSERT" app/main.py
```
Expected: **no output** — the legacy raw-SQL findings insert is fully replaced by `unified_writer.commit_scan`.

- [ ] **Step 4: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/lambda/shasta_runner_azure/app/main.py
git commit -m "$(cat <<'EOF'
feat: rewrite the Azure scanner as the v2 three-stage orchestrator

Subscription discovery -> tier-aware parallel subscription x module
ScanUnits through scanner_core.run_units -> unified_writer.commit_scan.
Two-phase Quick early-commit. Replaces the legacy single-pass scanner
and its direct findings-table writes.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Wire `build.sh`, `.gitignore`, and the test conftest copies

The rewritten `main.py` imports shared modules (`detectors.base`, `scan_pipeline`, `scan_state`, `unified_writer`) that are not in `shasta_runner_azure/app/`. `build.sh` must copy them into `app/` at image build, mirroring `shasta_runner/build.sh`.

**Files:**
- Modify: `platform/lambda/shasta_runner_azure/build.sh`
- Modify: `platform/lambda/shasta_runner_azure/.gitignore`

- [ ] **Step 1: Read the current `build.sh`**

Run `cat platform/lambda/shasta_runner_azure/build.sh` and locate the line that stages Shasta into `.build/` (the `rsync` block) — the copy steps go immediately after it, before the ECR-auth step. Mirror the structure of `shasta_runner/build.sh` steps 1b/1c.

- [ ] **Step 2: Add the shared-module copy steps to `build.sh`**

Immediately after the Shasta staging block and before the ECR-auth step, insert:

```bash
# Copy shared modules from sibling packages. Source of truth lives in
# ai_scanner/ and scanner_core/; .gitignore excludes the runtime copies.
echo "==> copying shared modules from ../ai_scanner"
rm -rf app/detectors app/unified_writer.py
mkdir -p app/detectors
cp ../ai_scanner/detectors/base.py app/detectors/base.py
touch                              app/detectors/__init__.py
cp ../ai_scanner/unified_writer.py app/unified_writer.py

echo "==> copying shared modules from ../scanner_core"
rm -f app/scan_pipeline.py app/scan_state.py
cp ../scanner_core/scan_pipeline.py app/scan_pipeline.py
cp ../scanner_core/scan_state.py    app/scan_state.py
```

(If `build.sh` uses an absolute or `$(dirname)`-relative path rather than a `../`-relative one, match its existing style — confirm by reading the file in Step 1. The `shasta_runner/build.sh` uses `../ai_scanner` because it `cd`s to its own directory first; verify `shasta_runner_azure/build.sh` does the same `cd "$(dirname "$0")"` and adjust if not.)

- [ ] **Step 3: Update `.gitignore`**

Overwrite `platform/lambda/shasta_runner_azure/.gitignore` with:

```
.build/
.venv/

# Build-time copies of shared modules (see build.sh).
# Source of truth lives in ai_scanner/ and scanner_core/; don't commit
# the runtime copies.
app/detectors/
app/unified_writer.py
app/scan_pipeline.py
app/scan_state.py
```

- [ ] **Step 4: Verify the full Azure adapter test suite passes**

Run:
```bash
cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest \
  ../shasta_runner_azure/app/tests/ -q
```
Expected: all tests from Tasks 1-5 pass (6 + 6 + 4 + 5 + 1 + 4 = 26).

- [ ] **Step 5: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/lambda/shasta_runner_azure/build.sh \
        platform/lambda/shasta_runner_azure/.gitignore
git commit -m "$(cat <<'EOF'
build: copy scanner_core + ai_scanner shared modules into the Azure image

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Add the Azure Fargate task definition to CDK

Mirror the AWS `ScanTaskDef` Fargate task definition for Azure, in `platform/lib/scan-stack.ts`. The current Azure scanner is a `DockerImageFunction` (Lambda) — that stays for now (Slice 1b retires it); this task only **adds** the Fargate task def so it can be invoked manually.

**Files:**
- Modify: `platform/lib/scan-stack.ts`

- [ ] **Step 1: Read the AWS Fargate task-def block**

Run `grep -n "ScanTaskDef\|ScanCluster\|FargateTaskDefinition\|scanTaskDef\|AzureRunner" platform/lib/scan-stack.ts` and read the `ScanTaskDef` construct (the AWS Fargate task definition, ~line 94) and the `AzureRunner` construct (~line 141) in full.

- [ ] **Step 2: Add the Azure Fargate task definition**

After the existing Azure `AzureRunner` `DockerImageFunction` construct, add an Azure Fargate task definition mirroring `ScanTaskDef`. It reuses the existing scan **cluster** (`this.scanCluster`) and the existing scan **security group**. The differences from the AWS task def:
- Family name: `ciso-copilot-azure-scan`
- Container image: from the Azure ECR repo (`props.shastaRunnerAzureRepo`), tag `latest`
- `entryPoint: ['python']`, `command: ['run.py']`
- Environment: `dbEnv` only (scan params arrive as `RunTask` container overrides)
- Log prefix: `azure-scan`
- IAM: `dbCluster.grantDataApiAccess(taskDef.taskRole)` and `secretsmanager:GetSecretValue` on `arn:aws:secretsmanager:*:*:secret:ciso-copilot/connections/*`. **No** `sts:AssumeRole` (Azure uses SP creds, not role assumption).
- Container name: `scanner` (so `RunTask` container overrides can target it by a stable name)

Expose the new task definition and add stack outputs `AzureScanTaskDefArn` and `AzureScanTaskDefFamily` (export the **family name**, not the revision-pinned ARN — the AWS scanner's HANDOFF gotcha: a revisioned ARN exported cross-stack deadlocks CFN).

Match the exact CDK style, prop names, and helper patterns of the existing `ScanTaskDef` block — do not invent new patterns. If a needed value (e.g. `props.shastaRunnerAzureRepo`) is not already a stack prop, confirm whether the existing `AzureRunner` construct already references the Azure repo and reuse that same reference.

- [ ] **Step 3: Synth-check the CDK app**

Run:
```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform && npx cdk synth CisoCopilotScan >/dev/null && echo "synth OK"
```
Expected: `synth OK` (no TypeScript or synthesis errors).

- [ ] **Step 4: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/lib/scan-stack.ts
git commit -m "$(cat <<'EOF'
feat: add the Azure scanner Fargate task definition

ciso-copilot-azure-scan task def (4 vCPU / 8 GB, python run.py) on the
existing scan cluster. The legacy Azure Lambda stays for now; Slice 1b
rewires the triggers and retires it.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Build, deploy, and live smoke-scan

Build the Azure scanner image, deploy the stack, and verify with a manual `ecs run-task` against the live Azure connection (`conn_id 79964b99-6501-413d-8f22-0431e870184d`, 2 subscriptions).

**Files:** none (build + deploy + verification).

- [ ] **Step 1: Build and push the Azure scanner image**

Run:
```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform/lambda/shasta_runner_azure && ./build.sh
```
Expected: the build log shows the shared-module copy steps, then a successful `docker push` of `shasta-runner-azure:latest`.

- [ ] **Step 2: Deploy the scanner stack**

Run:
```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform && npx cdk deploy CisoCopilotScan --require-approval never
```
Expected: completes successfully; the new `ciso-copilot-azure-scan` task definition is created.

- [ ] **Step 3: Insert a scan row and trigger a Fargate task**

A scan row must exist before the scanner runs (the scanner UPDATEs it). Insert one, then `ecs run-task`.

First, insert a `queued` scan row (replace `<SCAN_ID>` with a fresh UUID — generate with `python3 -c "import uuid;print(uuid.uuid4())"`):
```bash
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN \
  --secret-arn $DB_SECRET_ARN \
  --database ciso_copilot \
  --sql "INSERT INTO scans (scan_id, tenant_id, conn_id, trigger, status, tier, phase) VALUES (CAST('<SCAN_ID>' AS UUID), CAST('68db8abc-6388-4676-afe7-f24f8e49d6eb' AS UUID), CAST('79964b99-6501-413d-8f22-0431e870184d' AS UUID), 'manual', 'queued', 'quick', 'region_discovery')"
```
(The `tenant_id` `68db8abc-…` and `conn_id` `79964b99-…` are the live Azure connection. Confirm the tenant_id with: `SELECT tenant_id FROM cloud_connections WHERE conn_id = CAST('79964b99-6501-413d-8f22-0431e870184d' AS UUID)` before inserting.)

Then run the Fargate task. Get the cluster ARN, the `ciso-copilot-azure-scan` task-def ARN, the private subnet IDs, and the scan security group from the `CisoCopilotScan` / `CisoCopilotNetwork` stack outputs (or `aws ecs list-task-definitions`, `aws ec2 describe-subnets`). The connection's secret ARN is `cloud_connections.credentials_secret_arn` for `conn_id 79964b99-…`; `azure_tenant_id` and `client_id` are in that secret's JSON. `SUBSCRIPTION_IDS` is the connection's `scope.subscriptions` — `cb0d6ed4-a7c9-4929-8707-4a477a2cc9b5,8cd2b4cc-c789-466d-a8f7-8f51fb20985d`.

```bash
aws ecs run-task \
  --cluster <SCAN_CLUSTER_ARN> \
  --task-definition ciso-copilot-azure-scan \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[<SUBNET_IDS>],securityGroups=[<SCAN_SG_ID>],assignPublicIp=DISABLED}" \
  --overrides '{"containerOverrides":[{"name":"scanner","environment":[
    {"name":"SCAN_ID","value":"<SCAN_ID>"},
    {"name":"TENANT_ID","value":"68db8abc-6388-4676-afe7-f24f8e49d6eb"},
    {"name":"CONN_ID","value":"79964b99-6501-413d-8f22-0431e870184d"},
    {"name":"AZURE_TENANT_ID","value":"<from secret>"},
    {"name":"CLIENT_ID","value":"<from secret>"},
    {"name":"SECRET_ARN","value":"<credentials_secret_arn>"},
    {"name":"SUBSCRIPTION_IDS","value":"cb0d6ed4-a7c9-4929-8707-4a477a2cc9b5,8cd2b4cc-c789-466d-a8f7-8f51fb20985d"},
    {"name":"SCAN_TIER","value":"quick"}]}]}'
```

- [ ] **Step 4: Watch the scan reach a terminal state**

Poll the scan row (a Quick scan should finish in a few minutes):
```bash
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN \
  --secret-arn $DB_SECRET_ARN \
  --database ciso_copilot \
  --sql "SELECT status, phase, tier, jsonb_typeof(scope) AS scope_type, (stats->>'findings') AS findings FROM scans WHERE scan_id = CAST('<SCAN_ID>' AS UUID)"
```
Expected: `status` = `completed` (or `partial`), `phase` = `done`, `tier` = `quick`, `scope_type` = `object` (a subscription-keyed coverage map), `findings` > 0. If the scan fails, read the Fargate task logs (`aws logs tail /ecs/azure-scan --since 15m` or the log group the task def created) to diagnose.

- [ ] **Step 5: Spot-check that entities + findings were written**

```bash
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN \
  --secret-arn $DB_SECRET_ARN \
  --database ciso_copilot \
  --sql "SELECT count(*) FROM entities WHERE kind = 'azure_subscription'"
```
Expected: ≥ 1 — confirming `unified_writer.commit_scan` wrote Azure entities (the legacy scanner wrote none).

- [ ] **Step 6: Update HANDOFF.md and commit**

Add an entry under the Azure Scanner Uplift section of `HANDOFF.md` recording that Slice 1a is complete: the v2 Azure scanner backend exists, runs on Fargate, writes through the unified entity model, and was live-verified. Note that Slice 1b (rewiring `onboarding_azure_complete` + `_rescan_azure` to `ecs:RunTask`, retiring the legacy Azure Lambda) is next. Commit:

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add HANDOFF.md
git commit -m "$(cat <<'EOF'
docs: record Azure-uplift Slice 1a (v2 Azure scanner backend)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Done criteria

- [ ] Five new pure adapter modules (`azure_id_to_entity`, `azure_findings`, `subscription_discovery`, `azure_units`, `azure_credential`) each have a passing test suite (26 tests total).
- [ ] `main.py` is the v2 three-stage orchestrator; the legacy single-pass scanner and its direct `findings`-table writes are gone; `py_compile` passes.
- [ ] `run.py` Fargate entrypoint exists and `build_event` is tested.
- [ ] `build.sh` copies the shared modules; the copies are gitignored.
- [ ] An `ciso-copilot-azure-scan` Fargate task definition exists in `scan-stack.ts`; `cdk synth` passes.
- [ ] A live Quick scan against the real Azure connection completed (`completed`/`partial`, `phase=done`, subscription-keyed `scope`, findings > 0) and wrote `azure_subscription` entities.
- [ ] No change to `onboarding_azure_complete`, `connections_list`, or the legacy Azure Lambda — those are Slice 1b.
```
