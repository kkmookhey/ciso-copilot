# GCP Scanner Uplift — Slice 1a Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the GCP scanner (`platform/lambda/shasta_runner_gcp/`) as the v2 three-stage, tier-aware, parallel Fargate pipeline — built against the existing single-project onboarding so it is end-to-end verifiable before org onboarding exists (Slice 2a).

**Architecture:** A thin `main.py` orchestrator plus pure, unit-testable adapter modules, mirroring `shasta_runner_azure/`. The orchestrator runs three stages — project eligibility, footprint probe, tier-aware parallel `(project × module)` scan — and commits through the shared `unified_writer`. It runs as an ECS Fargate task; the legacy GCP Lambda is left in place (retired in Slice 1b).

**Tech Stack:** Python 3.12, Shasta GCP modules, `scanner_core` (`scan_pipeline`/`scan_state`), `unified_writer`, AWS CDK (TypeScript), ECS Fargate, Google Workload Identity Federation.

**Spec:** `docs/superpowers/specs/2026-05-22-gcp-scanner-uplift-design.md`

---

## Background an implementer needs

- The scanner package is `platform/lambda/shasta_runner_gcp/`. The current `app/main.py` is a legacy single-pass Lambda; this slice replaces it. `app/framework_map.py` is kept as-is.
- The **Azure scanner** (`platform/lambda/shasta_runner_azure/`) is the reference implementation. Every adapter module here has an Azure twin — read the twin before writing.
- `build.sh` copies shared modules into `app/` at image build: `detectors/base.py` + `unified_writer.py` from `../ai_scanner/`, `scan_pipeline.py` + `scan_state.py` from `../scanner_core/`. These copies are gitignored — the source of truth is the sibling package.
- `main.py` imports `shasta.*`, which is **not** installed in the test venv, so `main.py` is verified structurally + by live scan, never unit-tested. All other modules are pure and TDD'd.
- Run scanner unit tests with the package's own venv: `cd platform/lambda/shasta_runner_gcp && python -m pytest app/tests/ -v`. The `conftest.py` (Task 1) puts the sibling shared-module dirs on `sys.path` so bare-name imports resolve in tests.
- **WIF role gotcha:** the customer's WIF provider (created by `cfn/gcp/onboard.sh`) trusts the AWS role named `ciso-copilot-gcp-scanner`. The new Fargate task must run with a task role whose assumed-role name is exactly `ciso-copilot-gcp-scanner`, or every existing GCP connection's credential exchange breaks. Task 10 handles this by widening the existing `gcpScannerRole` trust policy to also trust `ecs-tasks.amazonaws.com` and using it as the task role.
- Confirmed Shasta GCP client API (`shasta/gcp/client.py`): `GCPClient(project_id, credentials, region=None)`; `.for_project(project_id)` → sibling client; `.validate_credentials()` → raises on failure, populates account info; `.discover_services()` → `list[str]` of enabled APIs; `.list_projects()` → `list[dict]`. Shasta GCP modules iterate regions internally — the scan unit is `(project × module)`, no region nesting.

## File structure

```
platform/lambda/shasta_runner_gcp/
  app/
    main.py              REWRITTEN — three-stage orchestrator (imports shasta.*)
    run.py               NEW — Fargate entrypoint, env-var → event
    gcp_credential.py    NEW — pure: WIF external_account info builder
    gcp_units.py         NEW — pure: tier → module lists
    gcp_id_to_entity.py  NEW — pure: GCP resource ID → entity shape
    gcp_findings.py      NEW — pure: Shasta GCP Finding → unified emissions
    project_discovery.py NEW — pure: Stage 1+2 footprint probe
    framework_map.py     UNCHANGED
    tests/
      __init__.py        NEW
      conftest.py        NEW — sys.path setup for shared-module imports
      test_gcp_units.py            NEW
      test_gcp_credential.py       NEW
      test_gcp_id_to_entity.py     NEW
      test_gcp_findings.py         NEW
      test_project_discovery.py    NEW
      test_run.py                  NEW
  build.sh               MODIFIED — copy shared modules from ai_scanner + scanner_core
  Dockerfile             MODIFIED — entrypoint stays main.handler (Lambda); Fargate uses run.py
  .gitignore             MODIFIED — ignore the runtime copies of shared modules
platform/lib/scan-stack.ts  MODIFIED — add GcpScanTaskDef; widen gcpScannerRole trust
```

---

### Task 1: Test scaffolding

**Files:**
- Create: `platform/lambda/shasta_runner_gcp/app/tests/__init__.py`
- Create: `platform/lambda/shasta_runner_gcp/app/tests/conftest.py`

- [ ] **Step 1: Create the test package init**

Create `platform/lambda/shasta_runner_gcp/app/tests/__init__.py` as an empty file.

- [ ] **Step 2: Create conftest.py**

Create `platform/lambda/shasta_runner_gcp/app/tests/conftest.py`:

```python
"""Make shasta_runner_gcp/app modules importable by bare name in tests.
At runtime build.sh copies shared modules into app/ (detectors/base.py +
unified_writer.py from ai_scanner; scan_pipeline.py + scan_state.py from
scanner_core); for tests we add those source directories to sys.path so
the bare-name imports resolve."""
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

- [ ] **Step 3: Verify pytest collects an empty suite**

Run: `cd platform/lambda/shasta_runner_gcp && python -m pytest app/tests/ -v`
Expected: `collected 0 items` — no errors.

- [ ] **Step 4: Commit**

```bash
git add platform/lambda/shasta_runner_gcp/app/tests/__init__.py \
        platform/lambda/shasta_runner_gcp/app/tests/conftest.py
git commit -m "test: scaffold shasta_runner_gcp test suite"
```

---

### Task 2: `gcp_units.py` — tier → module lists

**Files:**
- Create: `platform/lambda/shasta_runner_gcp/app/gcp_units.py`
- Test: `platform/lambda/shasta_runner_gcp/app/tests/test_gcp_units.py`

- [ ] **Step 1: Write the failing test**

Create `platform/lambda/shasta_runner_gcp/app/tests/test_gcp_units.py`:

```python
import pytest
from gcp_units import ALL_MODULES, modules_for_tier


def test_quick_splits_into_two_phases():
    phase1, phase2 = modules_for_tier("quick")
    assert phase1 == ["iam", "storage"]
    assert phase2 == ["networking", "encryption", "compute"]


def test_medium_runs_all_modules_in_phase_one():
    phase1, phase2 = modules_for_tier("medium")
    assert set(phase1) == set(ALL_MODULES)
    assert phase2 == []


def test_deep_runs_all_modules_in_phase_one():
    phase1, phase2 = modules_for_tier("deep")
    assert set(phase1) == set(ALL_MODULES)
    assert phase2 == []


def test_tier_is_case_insensitive():
    assert modules_for_tier("QUICK") == modules_for_tier("quick")


def test_unknown_tier_raises():
    with pytest.raises(ValueError, match="unknown scan tier"):
        modules_for_tier("turbo")


def test_modules_for_tier_returns_fresh_lists():
    phase1, _ = modules_for_tier("quick")
    phase1.append("mutated")
    phase1_again, _ = modules_for_tier("quick")
    assert "mutated" not in phase1_again
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd platform/lambda/shasta_runner_gcp && python -m pytest app/tests/test_gcp_units.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gcp_units'`

- [ ] **Step 3: Write the implementation**

Create `platform/lambda/shasta_runner_gcp/app/gcp_units.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd platform/lambda/shasta_runner_gcp && python -m pytest app/tests/test_gcp_units.py -v`
Expected: PASS — 6 passed.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/shasta_runner_gcp/app/gcp_units.py \
        platform/lambda/shasta_runner_gcp/app/tests/test_gcp_units.py
git commit -m "feat: gcp_units — GCP scan tier module split"
```

---

### Task 3: `gcp_credential.py` — WIF external_account info builder

**Files:**
- Create: `platform/lambda/shasta_runner_gcp/app/gcp_credential.py`
- Test: `platform/lambda/shasta_runner_gcp/app/tests/test_gcp_credential.py`

- [ ] **Step 1: Write the failing test**

Create `platform/lambda/shasta_runner_gcp/app/tests/test_gcp_credential.py`:

```python
from gcp_credential import build_external_account_info


def test_builds_audience_from_wif_project_pool_provider():
    info = build_external_account_info(
        wif_project_number="123456789",
        sa_email="ciso-copilot-reader@proj.iam.gserviceaccount.com",
        wif_pool="ciso-copilot-pool",
        wif_provider="ciso-copilot-aws-provider",
    )
    assert info["audience"] == (
        "//iam.googleapis.com/projects/123456789"
        "/locations/global/workloadIdentityPools/ciso-copilot-pool"
        "/providers/ciso-copilot-aws-provider"
    )


def test_builds_impersonation_url_from_sa_email():
    info = build_external_account_info(
        wif_project_number="123456789",
        sa_email="ciso-copilot-reader@proj.iam.gserviceaccount.com",
        wif_pool="pool", wif_provider="provider",
    )
    assert info["service_account_impersonation_url"] == (
        "https://iamcredentials.googleapis.com/v1/projects/-"
        "/serviceAccounts/ciso-copilot-reader@proj.iam.gserviceaccount.com"
        ":generateAccessToken"
    )


def test_static_fields_are_aws_external_account_shape():
    info = build_external_account_info("1", "sa@x.iam", "p", "pr")
    assert info["type"] == "external_account"
    assert info["subject_token_type"] == "urn:ietf:params:aws:token-type:aws4_request"
    assert info["token_url"] == "https://sts.googleapis.com/v1/token"
    assert info["credential_source"]["environment_id"] == "aws1"
    assert "GetCallerIdentity" in info["credential_source"]["regional_cred_verification_url"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd platform/lambda/shasta_runner_gcp && python -m pytest app/tests/test_gcp_credential.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gcp_credential'`

- [ ] **Step 3: Write the implementation**

Create `platform/lambda/shasta_runner_gcp/app/gcp_credential.py`:

```python
"""Workload Identity Federation credential setup for the GCP scanner.

Builds the `external_account` info dict that google-auth's
`aws.Credentials.from_info` consumes. The scanner's AWS task role is the
only "key": google-auth signs an AWS GetCallerIdentity request as the
subject token, GCP STS exchanges it, and the customer's reader service
account is impersonated. No private key on disk anywhere.

Pure: no google-auth import here, so it is unit-testable without the
scanner runtime. main.py calls `google.auth.aws.Credentials.from_info`
on the dict this returns.
"""
from __future__ import annotations


def build_external_account_info(
    wif_project_number: str,
    sa_email: str,
    wif_pool: str,
    wif_provider: str,
) -> dict:
    """Return the external_account info dict for WIF.

    `wif_project_number` is the project that hosts the Workload Identity
    Pool (in single-project onboarding, the scanned project itself; in
    org onboarding, the host project)."""
    audience = (
        f"//iam.googleapis.com/projects/{wif_project_number}"
        f"/locations/global/workloadIdentityPools/{wif_pool}"
        f"/providers/{wif_provider}"
    )
    impersonation_url = (
        f"https://iamcredentials.googleapis.com/v1/projects/-"
        f"/serviceAccounts/{sa_email}:generateAccessToken"
    )
    return {
        "type":                              "external_account",
        "audience":                          audience,
        "subject_token_type":                "urn:ietf:params:aws:token-type:aws4_request",
        "service_account_impersonation_url": impersonation_url,
        "token_url":                         "https://sts.googleapis.com/v1/token",
        "credential_source": {
            "environment_id":                 "aws1",
            "regional_cred_verification_url": "https://sts.{region}.amazonaws.com?Action=GetCallerIdentity&Version=2011-06-15",
        },
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd platform/lambda/shasta_runner_gcp && python -m pytest app/tests/test_gcp_credential.py -v`
Expected: PASS — 3 passed.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/shasta_runner_gcp/app/gcp_credential.py \
        platform/lambda/shasta_runner_gcp/app/tests/test_gcp_credential.py
git commit -m "feat: gcp_credential — WIF external_account info builder"
```

---

### Task 4: `gcp_id_to_entity.py` — GCP resource ID → entity shape

**Files:**
- Create: `platform/lambda/shasta_runner_gcp/app/gcp_id_to_entity.py`
- Test: `platform/lambda/shasta_runner_gcp/app/tests/test_gcp_id_to_entity.py`

- [ ] **Step 1: Write the failing test**

Create `platform/lambda/shasta_runner_gcp/app/tests/test_gcp_id_to_entity.py`:

```python
from gcp_id_to_entity import parse_gcp_id


def test_parses_compute_instance_selflink():
    rid = ("https://www.googleapis.com/compute/v1/projects/my-proj"
           "/zones/us-central1-a/instances/web-1")
    parsed = parse_gcp_id(rid)
    assert parsed["kind"] == "gcp_compute_instance"
    assert parsed["natural_key"] == rid
    assert parsed["display_name"] == "web-1"
    assert parsed["attributes"]["project"] == "my-proj"


def test_parses_storage_bucket_full_resource_name():
    rid = "//storage.googleapis.com/projects/_/buckets/my-data-bucket"
    parsed = parse_gcp_id(rid)
    assert parsed["kind"] == "gcp_storage_bucket"
    assert parsed["display_name"] == "my-data-bucket"


def test_parses_vpc_network_selflink():
    rid = ("https://www.googleapis.com/compute/v1/projects/my-proj"
           "/global/networks/default")
    parsed = parse_gcp_id(rid)
    assert parsed["kind"] == "gcp_vpc_network"
    assert parsed["display_name"] == "default"


def test_parses_service_account_full_resource_name():
    rid = ("//iam.googleapis.com/projects/my-proj/serviceAccounts/"
           "svc@my-proj.iam.gserviceaccount.com")
    parsed = parse_gcp_id(rid)
    assert parsed["kind"] == "gcp_service_account"
    assert parsed["display_name"] == "svc@my-proj.iam.gserviceaccount.com"


def test_unknown_collection_returns_none():
    assert parse_gcp_id(
        "https://www.googleapis.com/compute/v1/projects/p/global/widgets/w"
    ) is None


def test_non_resource_string_returns_none():
    assert parse_gcp_id("just-a-name") is None
    assert parse_gcp_id("") is None
    assert parse_gcp_id(None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd platform/lambda/shasta_runner_gcp && python -m pytest app/tests/test_gcp_id_to_entity.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gcp_id_to_entity'`

- [ ] **Step 3: Write the implementation**

Create `platform/lambda/shasta_runner_gcp/app/gcp_id_to_entity.py`:

```python
"""Parse a GCP resource identifier into an entity-emission shape — the
GCP analog of arn_to_entity.parse_arn / azure_id_to_entity.parse_azure_id.

Handles the two standard GCP identifier forms:
  - selfLink URLs:  https://www.googleapis.com/<svc>/<ver>/projects/<p>/.../<collection>/<name>
  - full resource names:  //<svc>.googleapis.com/projects/<p>/.../<collection>/<name>

Strategy: tokenise the path, find the last known `<collection>` token,
take the token after it as the resource name. Returns
{kind, natural_key, display_name, attributes} for collections in
_KIND_MAP, or None otherwise — the caller keeps the finding and emits no
entity (same contract as parse_arn).

NOTE: the exact resource_id strings Shasta GCP modules emit should be
confirmed against a live scan (see Task 11). _KIND_MAP covers the
standard GCP forms; extend it if a live scan surfaces others.
"""
from __future__ import annotations

# path collection token -> entity kind.
_KIND_MAP = {
    "instances":      "gcp_compute_instance",
    "buckets":        "gcp_storage_bucket",
    "networks":       "gcp_vpc_network",
    "subnetworks":    "gcp_subnetwork",
    "firewalls":      "gcp_firewall",
    "clusters":       "gcp_gke_cluster",
    "serviceAccounts": "gcp_service_account",
    "keyRings":       "gcp_kms_keyring",
    "services":       "gcp_cloud_run_service",
}


def parse_gcp_id(resource_id: str | None) -> dict | None:
    """Return {kind, natural_key, display_name, attributes} or None."""
    if not resource_id or not isinstance(resource_id, str):
        return None
    raw = resource_id.strip()
    # Strip the scheme / leading marker, keep the path.
    path = raw
    for prefix in ("https://", "http://", "//"):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    else:
        return None  # not a selfLink or full resource name

    tokens = [t for t in path.split("/") if t]
    project = None
    if "projects" in tokens:
        idx = tokens.index("projects")
        if idx + 1 < len(tokens):
            project = tokens[idx + 1]

    # Find the last collection token that we recognise.
    for i in range(len(tokens) - 2, -1, -1):
        kind = _KIND_MAP.get(tokens[i])
        if kind is not None:
            name = tokens[i + 1]
            return {
                "kind":         kind,
                "natural_key":  raw,
                "display_name": name,
                "attributes": {
                    "service":    "gcp",
                    "project":    project,
                    "collection": tokens[i],
                },
            }
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd platform/lambda/shasta_runner_gcp && python -m pytest app/tests/test_gcp_id_to_entity.py -v`
Expected: PASS — 6 passed.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/shasta_runner_gcp/app/gcp_id_to_entity.py \
        platform/lambda/shasta_runner_gcp/app/tests/test_gcp_id_to_entity.py
git commit -m "feat: gcp_id_to_entity — parse GCP resource IDs to entities"
```

---

### Task 5: `gcp_findings.py` — Shasta GCP Finding → unified emissions

**Files:**
- Create: `platform/lambda/shasta_runner_gcp/app/gcp_findings.py`
- Test: `platform/lambda/shasta_runner_gcp/app/tests/test_gcp_findings.py`

- [ ] **Step 1: Write the failing test**

Create `platform/lambda/shasta_runner_gcp/app/tests/test_gcp_findings.py`:

```python
from dataclasses import dataclass, field

from gcp_findings import convert_gcp_findings, project_entity


@dataclass
class _Enum:
    value: str


@dataclass
class _FakeFinding:
    """Duck-typed stand-in for a Shasta GCP Finding."""
    check_id:       str = "gcp-iam-1"
    title:          str = "Service account has owner role"
    description:    str = "An SA holds roles/owner."
    severity:       _Enum = field(default_factory=lambda: _Enum("high"))
    status:         _Enum = field(default_factory=lambda: _Enum("fail"))
    domain:         _Enum = field(default_factory=lambda: _Enum("iam"))
    resource_id:    str = ""
    resource_type:  str = "service_account"
    region:         str | None = None
    remediation:    str = "Remove the owner binding."
    soc2_controls:      list = field(default_factory=list)
    cis_aws_controls:   list = field(default_factory=list)
    cis_azure_controls: list = field(default_factory=list)
    cis_gcp_controls:   list = field(default_factory=lambda: ["1.4"])
    mcsb_controls:      list = field(default_factory=list)
    iso27001_controls:  list = field(default_factory=list)
    hipaa_controls:     list = field(default_factory=list)


def test_project_entity_shape():
    e = project_entity("my-proj", "tenant-1")
    assert e.kind == "gcp_project"
    assert e.natural_key == "my-proj"
    assert e.domain == "cloud"
    assert e.attributes["service"] == "gcp"


def test_convert_emits_a_finding_per_shasta_finding():
    out = convert_gcp_findings([_FakeFinding()], "tenant-1", "my-proj")
    assert len(out["findings"]) == 1
    f = out["findings"][0]
    assert f.finding_type == "gcp-iam-1"
    assert f.status == "fail"
    assert f.domain == "iam"
    assert f.frameworks  # cis_gcp at minimum


def test_convert_drops_not_assessed_findings():
    skipped = _FakeFinding(status=_Enum("not_assessed"))
    out = convert_gcp_findings([skipped], "tenant-1", "my-proj")
    assert out["findings"] == []


def test_convert_emits_subject_entity_and_edge_for_known_resource():
    rid = ("https://www.googleapis.com/compute/v1/projects/my-proj"
           "/zones/us-central1-a/instances/web-1")
    out = convert_gcp_findings(
        [_FakeFinding(resource_id=rid, resource_type="compute_instance")],
        "tenant-1", "my-proj")
    assert len(out["entities"]) == 1
    assert out["entities"][0].kind == "gcp_compute_instance"
    assert len(out["edges"]) == 1
    edge = out["edges"][0]
    assert edge.source_kind == "gcp_project"
    assert edge.source_natural_key == "my-proj"
    assert edge.target_kind == "gcp_compute_instance"
    assert edge.kind == "contains"


def test_convert_dedupes_repeated_resources():
    rid = ("https://www.googleapis.com/compute/v1/projects/my-proj"
           "/zones/us-central1-a/instances/web-1")
    out = convert_gcp_findings(
        [_FakeFinding(resource_id=rid), _FakeFinding(resource_id=rid)],
        "tenant-1", "my-proj")
    assert len(out["entities"]) == 1
    assert len(out["edges"]) == 1
    assert len(out["findings"]) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd platform/lambda/shasta_runner_gcp && python -m pytest app/tests/test_gcp_findings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gcp_findings'`

- [ ] **Step 3: Write the implementation**

Create `platform/lambda/shasta_runner_gcp/app/gcp_findings.py`:

```python
"""Convert Shasta GCP Finding objects into the platform's unified
emission types — the GCP analog of azure_findings.convert_azure_findings.

Pure: no Shasta or Google-SDK import. Operates on duck-typed Finding
objects (anything with the Shasta Finding fields), so it is
unit-testable without the scanner runtime.
"""
from __future__ import annotations

from typing import Any

from detectors.base import EdgeEmission, EntityEmission, FindingEmission
from framework_map import merge_framework_map
from gcp_id_to_entity import parse_gcp_id

_DETECTOR_ID_BASE = "shasta_runner_gcp"


def project_entity(project_id: str, tenant_id: str) -> EntityEmission:
    """The top-level entity for a GCP project."""
    return EntityEmission(
        tenant_id=tenant_id,
        kind="gcp_project",
        natural_key=project_id,
        display_name=project_id,
        domain="cloud",
        attributes={"service": "gcp", "project": project_id},
        evidence_packet=None,
        detector_id=f"{_DETECTOR_ID_BASE}.project",
        detector_version="0.1.0",
    )


def convert_gcp_findings(shasta_findings: list[Any], tenant_id: str,
                         project_id: str) -> dict:
    """Convert Shasta Finding objects to {entities, edges, findings}.
    Pure, no shared state — safe to call concurrently per project. A
    resource ID that parses to a known kind also emits a subject entity
    + a `contains` edge from the project."""
    out_findings: list[FindingEmission] = []
    out_entities: list[EntityEmission] = []
    out_edges:    list[EdgeEmission]   = []
    seen: set[tuple[str, str]] = set()

    for f in shasta_findings:
        if f.status.value.lower() in ("not_assessed", "not_applicable"):
            continue
        rid = (getattr(f, "resource_id", "") or "").strip()
        subj_kind = subj_nk = None
        parsed = parse_gcp_id(rid) if rid else None
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
                    tenant_id=tenant_id, source_kind="gcp_project",
                    source_natural_key=project_id, target_kind=subj_kind,
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

- [ ] **Step 4: Run test to verify it passes**

Run: `cd platform/lambda/shasta_runner_gcp && python -m pytest app/tests/test_gcp_findings.py -v`
Expected: PASS — 5 passed.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/shasta_runner_gcp/app/gcp_findings.py \
        platform/lambda/shasta_runner_gcp/app/tests/test_gcp_findings.py
git commit -m "feat: gcp_findings — Shasta GCP findings to unified emissions"
```

---

### Task 6: `project_discovery.py` — Stage 1+2 footprint probe

**Files:**
- Create: `platform/lambda/shasta_runner_gcp/app/project_discovery.py`
- Test: `platform/lambda/shasta_runner_gcp/app/tests/test_project_discovery.py`

- [ ] **Step 1: Write the failing test**

Create `platform/lambda/shasta_runner_gcp/app/tests/test_project_discovery.py`:

```python
from project_discovery import discover_projects


def test_empty_input_returns_empty():
    assert discover_projects([], lambda p: "active") == {}


def test_classifies_each_project_by_probe_result():
    states = {"proj-a": "active", "proj-b": "empty"}
    out = discover_projects(["proj-a", "proj-b"], lambda p: states[p])
    assert out == {"proj-a": "active", "proj-b": "empty"}


def test_probe_exception_classifies_unknown():
    def probe(p):
        raise RuntimeError("permission denied")
    assert discover_projects(["proj-a"], probe) == {"proj-a": "unknown"}


def test_unexpected_probe_value_classifies_unknown():
    assert discover_projects(["proj-a"], lambda p: "weird") == {"proj-a": "unknown"}


def test_no_project_is_silently_dropped():
    out = discover_projects(["a", "b", "c"], lambda p: "active")
    assert set(out) == {"a", "b", "c"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd platform/lambda/shasta_runner_gcp && python -m pytest app/tests/test_project_discovery.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'project_discovery'`

- [ ] **Step 3: Write the implementation**

Create `platform/lambda/shasta_runner_gcp/app/project_discovery.py`:

```python
"""Stage 1 + 2 of the GCP scan pipeline.

Stage 1: the project list to scan is passed in (single-project
onboarding gives one; org onboarding will give the user-selected
subset — a later slice).
Stage 2: a parallel per-project footprint probe classifies each project
`active` / `empty` / `unknown`.

The probe is injected as a callable so this module stays pure and
unit-testable; the concrete Shasta-GCP probe lives in main.py.

Anti-blind-spot invariant: any probe failure — an exception, or a probe
return value that is not `active`/`empty` — classifies the project
`unknown`. A project is never silently dropped, and a probe error is
never mislabelled `empty`.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable

_VALID = ("active", "empty")


def discover_projects(
    project_ids: list[str],
    probe: Callable[[str], str],
    *,
    max_workers: int = 8,
) -> dict[str, str]:
    """Probe each project concurrently. `probe(project_id)` returns
    `active` or `empty`; any exception or other value -> `unknown`.
    Returns {project_id: 'active' | 'empty' | 'unknown'}."""
    if not project_ids:
        return {}

    def _probe_one(project_id: str) -> tuple[str, str]:
        try:
            state = probe(project_id)
            if state not in _VALID:
                print(f"project probe {project_id}: unexpected state "
                      f"{state!r} -> unknown")
                return (project_id, "unknown")
            return (project_id, state)
        except Exception as e:
            print(f"project probe {project_id} FAILED: {e} -> unknown")
            return (project_id, "unknown")

    states: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for project_id, state in ex.map(_probe_one, project_ids):
            states[project_id] = state
    return states
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd platform/lambda/shasta_runner_gcp && python -m pytest app/tests/test_project_discovery.py -v`
Expected: PASS — 5 passed.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/shasta_runner_gcp/app/project_discovery.py \
        platform/lambda/shasta_runner_gcp/app/tests/test_project_discovery.py
git commit -m "feat: project_discovery — GCP footprint probe (stage 1+2)"
```

---

### Task 7: `run.py` — Fargate entrypoint

**Files:**
- Create: `platform/lambda/shasta_runner_gcp/app/run.py`
- Test: `platform/lambda/shasta_runner_gcp/app/tests/test_run.py`

- [ ] **Step 1: Write the failing test**

Create `platform/lambda/shasta_runner_gcp/app/tests/test_run.py`:

```python
import pytest

from run import build_event


def _env(**overrides):
    base = {
        "SCAN_ID":            "scan-1",
        "TENANT_ID":          "tenant-1",
        "CONN_ID":            "conn-1",
        "PROJECT_IDS":        "proj-a, proj-b",
        "WIF_PROJECT_NUMBER": "123456789",
        "SA_EMAIL":           "ciso-copilot-reader@proj.iam.gserviceaccount.com",
        "WIF_POOL":           "ciso-copilot-pool",
        "WIF_PROVIDER":       "ciso-copilot-aws-provider",
    }
    base.update(overrides)
    return base


def test_build_event_maps_env_vars():
    event = build_event(_env())
    assert event["scan_id"] == "scan-1"
    assert event["tenant_id"] == "tenant-1"
    assert event["conn_id"] == "conn-1"
    assert event["wif_project_number"] == "123456789"
    assert event["sa_email"].startswith("ciso-copilot-reader@")
    assert event["wif_pool"] == "ciso-copilot-pool"
    assert event["wif_provider"] == "ciso-copilot-aws-provider"


def test_build_event_splits_project_ids_and_trims():
    event = build_event(_env())
    assert event["project_ids"] == ["proj-a", "proj-b"]


def test_build_event_defaults_scan_tier_to_quick():
    assert build_event(_env())["scan_tier"] == "quick"


def test_build_event_respects_scan_tier():
    assert build_event(_env(SCAN_TIER="medium"))["scan_tier"] == "medium"


def test_build_event_missing_required_var_raises():
    env = _env()
    del env["SCAN_ID"]
    with pytest.raises(KeyError):
        build_event(env)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd platform/lambda/shasta_runner_gcp && python -m pytest app/tests/test_run.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'run'`

- [ ] **Step 3: Write the implementation**

Create `platform/lambda/shasta_runner_gcp/app/run.py`:

```python
"""Fargate entrypoint for the GCP scanner.

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

_REQUIRED = ("SCAN_ID", "TENANT_ID", "CONN_ID", "PROJECT_IDS",
             "WIF_PROJECT_NUMBER", "SA_EMAIL", "WIF_POOL", "WIF_PROVIDER")


def build_event(env: dict[str, str]) -> dict:
    """Map scanner env vars to the event dict main.handler expects.
    PROJECT_IDS is a comma-separated list. Raises KeyError if a required
    var is missing."""
    return {
        "scan_id":            env["SCAN_ID"],
        "tenant_id":          env["TENANT_ID"],
        "conn_id":            env["CONN_ID"],
        "project_ids":        [p.strip() for p in env["PROJECT_IDS"].split(",")
                               if p.strip()],
        "wif_project_number": env["WIF_PROJECT_NUMBER"],
        "sa_email":           env["SA_EMAIL"],
        "wif_pool":           env["WIF_POOL"],
        "wif_provider":       env["WIF_PROVIDER"],
        "scan_tier":          env.get("SCAN_TIER", "quick"),
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

- [ ] **Step 4: Run test to verify it passes**

Run: `cd platform/lambda/shasta_runner_gcp && python -m pytest app/tests/test_run.py -v`
Expected: PASS — 5 passed.

- [ ] **Step 5: Run the whole suite**

Run: `cd platform/lambda/shasta_runner_gcp && python -m pytest app/tests/ -v`
Expected: PASS — 29 passed (6 units + 3 credential + 6 id_to_entity + 5 findings + 5 discovery + 5 run; allowing for the small count differences, all green).

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/shasta_runner_gcp/app/run.py \
        platform/lambda/shasta_runner_gcp/app/tests/test_run.py
git commit -m "feat: run.py — GCP scanner Fargate entrypoint"
```

---

### Task 8: `main.py` — three-stage orchestrator rewrite

**Files:**
- Modify (full rewrite): `platform/lambda/shasta_runner_gcp/app/main.py`

This module imports `shasta.*` and is not unit-tested — it is verified structurally here and by the live scan in Task 11.

- [ ] **Step 1: Rewrite main.py**

Replace the entire contents of `platform/lambda/shasta_runner_gcp/app/main.py` with:

```python
"""shasta-runner-gcp — the v2 three-stage GCP scanner.

Invoked as a Fargate task (via run.py) or directly as a Lambda with:
  {
    "scan_id": "uuid", "tenant_id": "uuid", "conn_id": "uuid",
    "project_ids": ["<gcp project>", ...],
    "wif_project_number": "<project hosting the WIF pool>",
    "sa_email": "<reader SA email>",
    "wif_pool": "<pool id>", "wif_provider": "<provider id>",
    "scan_tier": "quick|medium|deep"
  }

Three stages (spec sections 5.3):
  1. Project eligibility — the project_ids list.
  2. Footprint probe — per-project active/empty/unknown.
  3. Tier-aware parallel scan — project x Shasta-module ScanUnits
     through scanner_core.run_units; two-phase Quick early-commit.

Credentials: a WIF external_account credential lets google-auth exchange
the Fargate task's AWS role for an impersonated GCP reader SA. No private
key on disk anywhere.
"""
from __future__ import annotations

import os
import traceback
from dataclasses import dataclass

# === Shasta imports ===
from shasta.gcp.client import GCPClient
from shasta.gcp import (
    compute        as gcp_compute,
    storage        as gcp_storage,
    networking     as gcp_networking,
    iam            as gcp_iam,
    encryption     as gcp_encryption,
    logging_checks as gcp_logging,
    cloud_run      as gcp_cloud_run,
)

# === Adapter modules (this package) ===
from gcp_credential    import build_external_account_info
from gcp_findings      import convert_gcp_findings, project_entity
from gcp_units         import modules_for_tier
from project_discovery import discover_projects

# === Shared modules (copied in by build.sh) ===
from detectors.base import EntityEmission
from scan_pipeline  import ConcurrencyLimiter, ScanUnit, run_units
from scan_state     import record_scan_scope, update_scan
from unified_writer import commit_scan, mark_scan_failed

_SCANNER_VERSION = "shasta_runner_gcp.0.2.0"

# Module name -> Shasta entry point. Each takes a GCPClient, returns
# list[Finding]. The names match gcp_units' tier lists.
GCP_MODULES = {
    "iam":        gcp_iam.run_all_gcp_iam_checks,
    "storage":    gcp_storage.run_all_gcp_storage_checks,
    "networking": gcp_networking.run_all_gcp_networking_checks,
    "encryption": gcp_encryption.run_all_gcp_encryption_checks,
    "compute":    gcp_compute.run_all_gcp_compute_checks,
    "logging":    gcp_logging.run_all_gcp_logging_checks,
    "cloud_run":  gcp_cloud_run.run_all_gcp_cloud_run_checks,
}

# Projects in these states are scanned; `empty` is skipped.
_SCANNABLE = ("active", "unknown")


@dataclass(frozen=True)
class CloudScanContext:
    """Minimal ScanContext for unified_writer (reads these by attr)."""
    scan_id:         str
    tenant_id:       str
    connection_id:   str
    scanner_version: str = _SCANNER_VERSION


def handler(event: dict, context) -> dict:
    scan_id            = event["scan_id"]
    tenant_id          = event["tenant_id"]
    conn_id            = event["conn_id"]
    project_ids        = event["project_ids"]
    wif_project_number = event["wif_project_number"]
    sa_email           = event["sa_email"]
    wif_pool           = event["wif_pool"]
    wif_provider       = event["wif_provider"]
    scan_tier          = event.get("scan_tier", "quick")

    print(f"gcp scan start: scan={scan_id} tier={scan_tier} "
          f"projects={project_ids}")
    ctx = CloudScanContext(scan_id=scan_id, tenant_id=tenant_id,
                           connection_id=conn_id)
    update_scan(scan_id, status="running", phase="region_discovery")

    try:
        # --- Credentials: one WIF credential, shared by every project ---
        from google.auth import aws as google_aws
        info = build_external_account_info(
            wif_project_number, sa_email, wif_pool, wif_provider)
        credentials = google_aws.Credentials.from_info(info)

        # A base client bound to the first project — used only to mint
        # per-project sibling clients via for_project().
        base_client = GCPClient(project_id=project_ids[0],
                                credentials=credentials)

        # --- Stage 1 + 2: project discovery ----------------------------
        def _probe(project_id: str) -> str:
            c = base_client.for_project(project_id)
            c.validate_credentials()             # raises if unreachable
            return "active" if c.discover_services() else "empty"

        states = discover_projects(project_ids, _probe)
        print(f"project discovery: {states}")
        scannable = [p for p, st in states.items() if st in _SCANNABLE]

        # --- Stage 3: build + run scan units ---------------------------
        phase1_mods, phase2_mods = modules_for_tier(scan_tier)
        limiter = ConcurrencyLimiter(default=8)
        coverage_map = {p: {"state": states[p], "modules_run": [],
                            "errors": []} for p in project_ids}

        entities: list[EntityEmission] = [
            project_entity(p, tenant_id) for p in scannable]
        edges: list = []
        findings: list = []

        phase1_units = _build_units(scannable, phase1_mods, base_client,
                                    tenant_id)
        phase2_units = _build_units(scannable, phase2_mods, base_client,
                                    tenant_id)

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
            "projects": coverage_map,
        })
        update_scan(scan_id, status=final_status, phase="done", stats={
            "entities": len(entities), "edges": len(edges),
            "findings": len(findings), "tier": scan_tier,
            "projects": scannable,
        })
        print(f"gcp scan complete ({final_status}): {len(entities)} "
              f"entities, {len(edges)} edges, {len(findings)} findings")
        return {"scan_id": scan_id, "status": final_status,
                "findings_written": len(findings)}

    except Exception as e:
        err = f"{e}: {traceback.format_exc()}"[:1000]
        print(f"GCP SCAN FAILED: {err}")
        try:
            mark_scan_failed(ctx, err)
        except Exception:
            pass
        update_scan(scan_id, status="failed", phase="done", error=err)
        raise


def _build_units(projects: list[str], module_names: list[str],
                 base_client, tenant_id: str) -> list[ScanUnit]:
    """One ScanUnit per (project, module). Each unit builds its own
    GCPClient inside `run` — fresh per unit via for_project — so
    concurrent units never share a mutable Shasta GCP client."""
    units: list[ScanUnit] = []
    for project_id in projects:
        for name in module_names:
            run_fn = GCP_MODULES[name]
            units.append(ScanUnit(
                name=f"{project_id}/{name}", service=name,
                run=_module_unit(run_fn, base_client, project_id,
                                 tenant_id)))
    return units


def _module_unit(run_fn, base_client, project_id: str, tenant_id: str):
    """Build the `run` callable for one (project, module) unit."""
    def _run() -> dict:
        # Fresh GCPClient per unit: for_project returns a sibling with
        # its own service-client cache, so units never share mutable
        # Google SDK state across the thread pool. validate_credentials
        # populates the client's account_info and is required before
        # the Shasta module runs.
        client = base_client.for_project(project_id)
        client.validate_credentials()
        shasta_findings = run_fn(client)
        return convert_gcp_findings(shasta_findings, tenant_id, project_id)
    return _run


def _absorb(results, entities, edges, findings, coverage_map) -> None:
    """Merge a run_units UnitResults into the accumulators + coverage
    map. Unit name format is `<project_id>/<module>`."""
    entities.extend(results.entities)
    edges.extend(results.edges)
    findings.extend(results.findings)
    for o in results.outcomes:
        project_id = o.name.split("/", 1)[0]
        bucket = coverage_map.get(project_id)
        if bucket is None:
            continue
        if o.status == "success":
            bucket["modules_run"].append(o.name)
        else:
            bucket["errors"].append(
                f"{o.status}: {o.name} {o.detail}".strip())
```

- [ ] **Step 2: Verify the module parses (syntax check)**

Run: `cd platform/lambda/shasta_runner_gcp && python -c "import ast; ast.parse(open('app/main.py').read()); print('main.py parses OK')"`
Expected: `main.py parses OK`

- [ ] **Step 3: Verify the existing unit suite still passes**

Run: `cd platform/lambda/shasta_runner_gcp && python -m pytest app/tests/ -v`
Expected: PASS — all tests still green (main.py is not imported by the suite).

- [ ] **Step 4: Commit**

```bash
git add platform/lambda/shasta_runner_gcp/app/main.py
git commit -m "feat: rewrite GCP scanner as v2 three-stage orchestrator"
```

---

### Task 9: `build.sh`, `.gitignore`, Dockerfile — shared-module wiring

**Files:**
- Modify: `platform/lambda/shasta_runner_gcp/build.sh`
- Modify: `platform/lambda/shasta_runner_gcp/.gitignore`
- Modify: `platform/lambda/shasta_runner_gcp/Dockerfile`

- [ ] **Step 1: Update build.sh to copy shared modules**

Replace the entire contents of `platform/lambda/shasta_runner_gcp/build.sh` with:

```bash
#!/bin/bash
# Build + push shasta-runner-gcp container image to ECR.
#
# Usage:
#   ./build.sh           # tags 'latest'
#   ./build.sh v0.2.0    # tags 'v0.2.0' + 'latest'

set -euo pipefail
cd "$(dirname "$0")"

REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REPO="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/shasta-runner-gcp"
TAG="${1:-latest}"

SHASTA_SRC="${SHASTA_SRC:-$HOME/Projects/Shasta}"
[[ -d "$SHASTA_SRC" ]] || { echo "ERROR: Shasta source not found at $SHASTA_SRC" >&2; exit 1; }

echo "==> staging Shasta from $SHASTA_SRC"
rm -rf .build
mkdir -p .build
rsync -a --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
  --exclude='*.pyc' --exclude='data' --exclude='tests' \
  --exclude='.pytest_cache' --exclude='.ruff_cache' \
  "$SHASTA_SRC/" .build/shasta/

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

echo "==> ECR auth"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$REPO" >/dev/null

echo "==> docker build (linux/amd64) → $REPO:$TAG"
docker build --platform linux/amd64 --provenance=false \
  ${NO_CACHE:+--no-cache} \
  -t "shasta-runner-gcp:$TAG" -t "$REPO:$TAG" -t "$REPO:latest" .

echo "==> docker push $REPO:$TAG"
docker push "$REPO:$TAG"
[[ "$TAG" != "latest" ]] && docker push "$REPO:latest"

rm -rf .build
echo "==> done. Image URI: $REPO:$TAG"
```

- [ ] **Step 2: Update .gitignore to exclude the runtime copies**

Replace the entire contents of `platform/lambda/shasta_runner_gcp/.gitignore` with:

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

- [ ] **Step 3: Update the Dockerfile**

Replace the entire contents of `platform/lambda/shasta_runner_gcp/Dockerfile` with:

```dockerfile
# shasta-runner-gcp — container for GCP scans (Workload Identity Federation).
# Runs as an ECS Fargate task: `python run.py`. The Lambda CMD is retained
# so the legacy GCP Lambda still works until it is retired (Slice 1b).

FROM public.ecr.aws/lambda/python:3.12

RUN pip install --no-cache-dir \
    "boto3>=1.35.0" \
    "pydantic>=2.9.0" \
    "google-auth>=2.29.0" \
    "google-api-python-client>=2.128.0" \
    "google-cloud-storage>=2.16.0" \
    "google-auth-httplib2>=0.2.0"

COPY .build/shasta /tmp/shasta
RUN cd /tmp/shasta && pip install --no-cache-dir --no-deps . && rm -rf /tmp/shasta

COPY app/ ${LAMBDA_TASK_ROOT}/

CMD ["main.handler"]
```

- [ ] **Step 4: Verify the shared-module copy works**

Run: `cd platform/lambda/shasta_runner_gcp && bash -c 'set -e; rm -rf app/detectors app/unified_writer.py app/scan_pipeline.py app/scan_state.py; mkdir -p app/detectors; cp ../ai_scanner/detectors/base.py app/detectors/base.py; touch app/detectors/__init__.py; cp ../ai_scanner/unified_writer.py app/unified_writer.py; cp ../scanner_core/scan_pipeline.py app/scan_pipeline.py; cp ../scanner_core/scan_state.py app/scan_state.py; ls app/detectors app/unified_writer.py app/scan_pipeline.py app/scan_state.py'`
Expected: lists `app/detectors/base.py`, `app/detectors/__init__.py`, `app/unified_writer.py`, `app/scan_pipeline.py`, `app/scan_state.py` — no errors.

- [ ] **Step 5: Re-run the unit suite (now with shared modules present)**

Run: `cd platform/lambda/shasta_runner_gcp && python -m pytest app/tests/ -v`
Expected: PASS — all green. (The suite resolves shared modules via conftest's `sys.path`; the copies just confirm the build step works.)

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/shasta_runner_gcp/build.sh \
        platform/lambda/shasta_runner_gcp/.gitignore \
        platform/lambda/shasta_runner_gcp/Dockerfile
git commit -m "build: wire scanner_core + ai_scanner shared modules into GCP scanner"
```

---

### Task 10: CDK — `GcpScanTaskDef` Fargate task definition

**Files:**
- Modify: `platform/lib/scan-stack.ts`

- [ ] **Step 1: Widen the gcpScannerRole trust policy**

In `platform/lib/scan-stack.ts`, find the `gcpScannerRole` definition (around line 220):

```typescript
    const gcpScannerRole = new iam.Role(this, 'GcpScannerRole', {
      roleName: 'ciso-copilot-gcp-scanner',
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });
```

Replace it with (the role is now shared by the legacy Lambda *and* the new Fargate task; both must be able to assume it, and the role name must stay `ciso-copilot-gcp-scanner` because the customer WIF provider trusts that exact name):

```typescript
    // Shared by the legacy GCP Lambda AND the new Fargate task. The
    // customer's WIF provider (cfn/gcp/onboard.sh) trusts the AWS role
    // named 'ciso-copilot-gcp-scanner' — the assumed-role identity of
    // whatever runs the scan must carry that name, so this single role
    // is used as both the Lambda role and the Fargate task role. The
    // trust policy admits both service principals.
    const gcpScannerRole = new iam.Role(this, 'GcpScannerRole', {
      roleName: 'ciso-copilot-gcp-scanner',
      assumedBy: new iam.CompositePrincipal(
        new iam.ServicePrincipal('lambda.amazonaws.com'),
        new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      ),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });
```

- [ ] **Step 2: Add the GcpScanTaskDef after the legacy GcpRunner Lambda**

In `platform/lib/scan-stack.ts`, find the line `props.dbCluster.grantDataApiAccess(this.shastaRunnerGcp);` (around line 237, immediately after the `GcpRunner` Lambda). Insert the following immediately after that line:

```typescript

    // ===== GCP scanner — v2 Fargate task =====
    // The customer WIF provider trusts the 'ciso-copilot-gcp-scanner'
    // role; gcpScannerRole IS that role, used here as the task role so
    // google-auth's GetCallerIdentity reflects the trusted name.
    const gcpScanTaskDef = new ecs.FargateTaskDefinition(this, 'GcpScanTaskDef', {
      family:         'ciso-copilot-gcp-scan',
      cpu:            4096,
      memoryLimitMiB: 8192,
      taskRole:       gcpScannerRole,
    });

    gcpScanTaskDef.addContainer('scanner', {
      image: ecs.ContainerImage.fromEcrRepository(props.shastaRunnerGcpRepo, 'latest'),
      entryPoint: ['python'],
      command:    ['run.py'],
      environment: dbEnv,
      logging: ecs.LogDriver.awsLogs({
        streamPrefix: 'gcp-scan',
        logRetention: logs.RetentionDays.ONE_MONTH,
      }),
    });

    // gcpScannerRole already has Data API access granted via the
    // GcpRunner Lambda's grantDataApiAccess call below — the Fargate
    // task shares the same role, so no extra grant is needed. The WIF
    // GetCallerIdentity call requires no IAM policy (a principal may
    // always describe itself).

    this.gcpScanTaskDef       = gcpScanTaskDef;
    this.gcpScanTaskDefFamily = 'ciso-copilot-gcp-scan';

    new cdk.CfnOutput(this, 'GcpScanTaskDefArn',    { value: gcpScanTaskDef.taskDefinitionArn });
    new cdk.CfnOutput(this, 'GcpScanTaskDefFamily', { value: 'ciso-copilot-gcp-scan' });
```

- [ ] **Step 3: Declare the public readonly fields**

In `platform/lib/scan-stack.ts`, find the existing public field declarations near the top of the class (around line 51-53, where `azureScanTaskDef` / `azureScanTaskDefFamily` are declared). Add these two lines alongside them:

```typescript
  public readonly gcpScanTaskDef:       ecs.FargateTaskDefinition;
  public readonly gcpScanTaskDefFamily: string;
```

- [ ] **Step 4: Synthesize the stack to verify the CDK compiles**

Run: `cd platform && npx cdk synth CisoCopilotScan > /dev/null && echo "cdk synth OK"`
Expected: `cdk synth OK` — no TypeScript or synth errors.

- [ ] **Step 5: Commit**

```bash
git add platform/lib/scan-stack.ts
git commit -m "feat: add ciso-copilot-gcp-scan Fargate task definition"
```

---

### Task 11: Build the image, deploy, and live-verify a Quick scan

This task has no automated test — it is the end-to-end verification gate for Slice 1a. It runs against the existing GCP connection (single-project onboarding).

- [ ] **Step 1: Build and push the scanner image**

Run: `cd platform/lambda/shasta_runner_gcp && ./build.sh`
Expected: ends with `==> done. Image URI: ...dkr.ecr.us-east-1.amazonaws.com/shasta-runner-gcp:latest`

- [ ] **Step 2: Deploy the Scan stack**

Run: `cd platform && npx cdk deploy CisoCopilotScan --require-approval never`
Expected: `CisoCopilotScan` deploys successfully; outputs include `GcpScanTaskDefArn` and `GcpScanTaskDefFamily`.

- [ ] **Step 3: Gather the values needed to launch a scan**

Run these to collect the live GCP connection's identifiers and the network config:

```bash
# The existing GCP connection: conn_id + scope (project_id, project_number, sa_email, wif_pool, wif_provider)
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN \
  --secret-arn $DB_SECRET_ARN \
  --database ciso_copilot \
  --sql "SELECT conn_id, tenant_id, scope FROM cloud_connections WHERE provider='gcp' ORDER BY created_at DESC LIMIT 1" \
  --output json

# Scan cluster + subnets + security group (Scan stack outputs)
aws cloudformation describe-stacks --stack-name CisoCopilotScan \
  --query "Stacks[0].Outputs[?contains(OutputKey,'ScanCluster')||contains(OutputKey,'Subnet')||contains(OutputKey,'SecurityGroup')]" \
  --output table
```

Expected: one GCP connection row with a `scope` JSON containing `project_id`, `project_number`, `sa_email`, `wif_pool`, `wif_provider`; and the cluster ARN / subnet IDs / security-group ID.

- [ ] **Step 4: Insert a scan row**

Substitute `<SCAN_ID>` (a fresh UUID — generate with `python -c "import uuid; print(uuid.uuid4())"`), `<TENANT_ID>`, `<CONN_ID>` from Step 3:

```bash
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN \
  --secret-arn $DB_SECRET_ARN \
  --database ciso_copilot \
  --sql "INSERT INTO scans (scan_id, tenant_id, conn_id, status, phase, tier, started_at) VALUES (CAST('<SCAN_ID>' AS UUID), CAST('<TENANT_ID>' AS UUID), CAST('<CONN_ID>' AS UUID), 'queued', 'region_discovery', 'quick', now())"
```

Expected: `numberOfRecordsUpdated: 1`.

- [ ] **Step 5: Launch the Fargate scan task**

Substitute the values from Steps 3-4 (`<SUBNET_IDS>` comma-separated, `<SG_ID>`, `<CLUSTER_ARN>`, and the `scope` fields):

```bash
aws ecs run-task \
  --cluster <CLUSTER_ARN> \
  --task-definition ciso-copilot-gcp-scan \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[<SUBNET_IDS>],securityGroups=[<SG_ID>],assignPublicIp=DISABLED}" \
  --overrides '{"containerOverrides":[{"name":"scanner","environment":[
    {"name":"SCAN_ID","value":"<SCAN_ID>"},
    {"name":"TENANT_ID","value":"<TENANT_ID>"},
    {"name":"CONN_ID","value":"<CONN_ID>"},
    {"name":"PROJECT_IDS","value":"<project_id>"},
    {"name":"WIF_PROJECT_NUMBER","value":"<project_number>"},
    {"name":"SA_EMAIL","value":"<sa_email>"},
    {"name":"WIF_POOL","value":"<wif_pool>"},
    {"name":"WIF_PROVIDER","value":"<wif_provider>"},
    {"name":"SCAN_TIER","value":"quick"}
  ]}]}'
```

Expected: a JSON response with `tasks[0].taskArn` and no `failures[]`.

- [ ] **Step 6: Watch the scan logs**

Run: `aws logs tail "/aws/ecs/gcp-scan" --since 10m --follow` (Ctrl-C once you see `gcp scan complete`).
Expected: log lines progress through `gcp scan start`, `project discovery: {...}`, `quick phase 1 committed: N findings`, `gcp scan complete (completed): ...`.

- [ ] **Step 7: Verify the scan landed in the database**

Substitute `<SCAN_ID>`:

```bash
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN \
  --secret-arn $DB_SECRET_ARN \
  --database ciso_copilot \
  --sql "SELECT status, phase, tier, scope::text, stats::text FROM scans WHERE scan_id = CAST('<SCAN_ID>' AS UUID)" \
  --output json
```

Expected: `status` = `completed` (or `partial` if a module errored), `phase` = `done`, `tier` = `quick`, `scope` is a project-keyed coverage map (`{"tier":"quick","projects":{"<project_id>":{"state":"active","modules_run":[...],"errors":[]}}}`), `stats` carries non-zero `findings`/`entities`.

- [ ] **Step 8: Verify findings + entities were written through unified_writer**

Substitute `<SCAN_ID>`:

```bash
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN \
  --secret-arn $DB_SECRET_ARN \
  --database ciso_copilot \
  --sql "SELECT (SELECT count(*) FROM findings WHERE scan_id = CAST('<SCAN_ID>' AS UUID)) AS findings, (SELECT count(*) FROM entities WHERE tenant_id = CAST('<TENANT_ID>' AS UUID) AND kind LIKE 'gcp_%') AS gcp_entities" \
  --output json
```

Expected: `findings` > 0; `gcp_entities` ≥ 1 (at least the `gcp_project` entity). If `gcp_entities` is exactly 1, inspect a few findings' `resource_id` values to confirm whether `gcp_id_to_entity._KIND_MAP` needs extending for this Shasta version (spec/Task 4 note) — record the observation but do not block Slice 1a on it.

- [ ] **Step 9: Update HANDOFF.md**

Add a section to `HANDOFF.md` recording Slice 1a as shipped: the new v2 GCP scanner, the `ciso-copilot-gcp-scan` Fargate task def, the live-verified Quick scan ID and its result counts, and that the legacy GCP Lambda is still wired (retired in Slice 1b).

- [ ] **Step 10: Commit**

```bash
git add HANDOFF.md
git commit -m "docs: record GCP scanner uplift Slice 1a shipped"
```

---

## Self-review

**Spec coverage** (against `2026-05-22-gcp-scanner-uplift-design.md`):
- §5.1 adapter modules — `gcp_credential` (T3), `project_discovery` (T6, `discover_projects`; `enumerate_projects` deferred to Slice 2a where org-mode needs it), `gcp_units` (T2), `gcp_findings` (T5), `gcp_id_to_entity` (T4), `run.py` (T7) — covered.
- §5.2 shared modules — wired by `build.sh` (T9).
- §5.3 three-stage orchestrator — `main.py` (T8): credential setup, project discovery, tier-aware parallel scan, project-keyed coverage map, partial-status logic — covered.
- §5.4 tier split — `gcp_units` (T2); Deep's AI pass deferred per spec open item #2 — covered with documented deferral.
- §5.5 connection mode — Slice 1a runs single-project only; `scope.mode` branching is Slice 2a. The orchestrator takes an explicit `project_ids` list, which 2a will populate from `scope.selected`. No 1a gap.
- Fargate task def — `GcpScanTaskDef` (T10).
- §9 — Slice 1a explicitly built against existing single-project onboarding; legacy Lambda left in place. Production triggers (1b), org onboarding (2a), picker (2b) are out of scope here — covered.
- §10 testing — every pure module TDD'd; `main.py` structural + live scan (T11).

**Placeholder scan:** the `<SCAN_ID>` / `<CONN_ID>` / `<SUBNET_IDS>` tokens in Task 11 are runtime values the engineer fills from the Step 3 query output — they are explicitly labelled substitutions, not unresolved plan placeholders. No "TBD"/"implement later" anywhere.

**Type consistency:** `build_external_account_info` (T3) signature matches its call in `main.py` (T8). `convert_gcp_findings(findings, tenant_id, project_id)` and `project_entity(project_id, tenant_id)` (T5) match their calls in `main.py`. `discover_projects(project_ids, probe)` (T6) matches. `modules_for_tier` (T2) returns a 2-tuple consumed correctly in T8. `parse_gcp_id` (T4) is consumed by `gcp_findings` (T5). The `run.py` event keys (T7) exactly match the keys `handler` reads (T8): `scan_id`, `tenant_id`, `conn_id`, `project_ids`, `wif_project_number`, `sa_email`, `wif_pool`, `wif_provider`, `scan_tier`. `EntityEmission`/`EdgeEmission`/`FindingEmission` field names match the shared `detectors/base.py` usage in the Azure twin.

No issues found.
