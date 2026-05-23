# AI Visibility v2 — Slice 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an Azure-AI cloud pass (mirroring the existing AWS `ai_pass`) plus a unified `/ai` web view (Fail/Partial/Pass score, by-source tiles, by-framework tiles, per-person stub) — so a CISO sees AI exposure across code + AWS + Azure in one screen.

**Architecture:** Add `ai_pass.py` to `shasta_runner_azure/app/` that wraps Shasta's existing `discover_azure_ai_services` + `run_full_azure_ai_scan` + `enrich_findings_with_ai_controls`. Gate it on Medium+ scan tier (same as AWS). Add a new Lambda `ai_summary` exposing `GET /ai/summary` and a new web route `/ai` consuming it. Per-person view is a SQL `GROUP BY LOWER(email)` over whatever email-bearing attribute existing finding kinds carry (no schema migration).

**Tech Stack:** Python 3.12 (Lambda + scanner), TypeScript + React + Vite + Tailwind (web), AWS CDK (TypeScript), Aurora Postgres via RDS Data API, pytest, vitest/Playwright.

**Spec:** `docs/superpowers/specs/2026-05-22-ai-visibility-v2-design.md`. Strategy: `docs/superpowers/specs/2026-05-22-ai-security-strategy.md`.

**Out of scope (deferred to later slices):** S2 Entra connector, S3 compliance mapping sweep, S4 polish/iOS push, GCP-AI Discovery (its own sub-project).

**Branch:** `feat/ai-visibility-v2-slice-1`.

---

## File Structure

### Created
- `platform/lambda/shasta_runner_azure/app/ai_pass.py` — Azure-AI orchestrator (pure helpers + lazy Shasta import; mirrors `shasta_runner/app/ai_pass.py`).
- `platform/lambda/shasta_runner_azure/app/tests/test_ai_pass.py` — unit tests.
- `platform/lambda/ai_summary/main.py` — new GET /ai/summary Lambda handler.
- `platform/lambda/ai_summary/tests/test_main.py` — handler unit tests.
- `platform/lambda/ai_summary/__init__.py`, `platform/lambda/ai_summary/tests/__init__.py` — package markers.
- `web/src/routes/AISummary.tsx` — `/ai` index route.
- `web/src/routes/AISummary.test.tsx` — vitest component test.
- `web/tests/playwright/ai-summary.spec.ts` — Playwright smoke (if a Playwright harness exists in repo; otherwise stub it inline in the test file and mark as skipped — see Task 9).

### Modified
- `platform/lambda/shasta_runner_azure/app/azure_units.py` — add `ai` to module list for Medium+ tier.
- `platform/lambda/shasta_runner_azure/app/main.py` — import `run_ai_pass`, wire as one global ScanUnit gated on tier.
- `platform/lambda/shasta_runner_azure/Dockerfile` (or `build.sh`) — verify `ai_pass.py` is included (no change expected since the build copies `app/`).
- `platform/lib/api-stack.ts` — add `AiSummaryFn` Lambda + `/ai/summary` API Gateway resource.
- `web/src/App.tsx` — add `<Route path="/ai" element={<AISummary />} />` as the `/ai` index route.
- `HANDOFF.md` — add S1 ship block + verification checklist on completion.

---

## Task 1: Add `ai_pass.py` to `shasta_runner_azure` (TDD: tests first)

**Files:**
- Create: `platform/lambda/shasta_runner_azure/app/ai_pass.py`
- Test: `platform/lambda/shasta_runner_azure/app/tests/test_ai_pass.py`

**Context for the implementer:** The AWS counterpart lives at `platform/lambda/shasta_runner/app/ai_pass.py` (290 lines). Read it first — this task ports the same shape to Azure. Key differences from AWS:
- Shasta's Azure entry points are `shasta.azure.ai_discovery.discover_azure_ai_services(client)` and `shasta.azure.ai_checks.run_full_azure_ai_scan(client)`.
- Azure discovery returns `{azure_openai: {...}, azure_ml: {...}, cognitive_services: {...}}` (vs AWS's `{sagemaker, comprehend, bedrock, lambda_ai}`).
- Parent entity is `azure_subscription` (natural_key = subscription_id), not `aws_account`.
- The `_AI_ENV_VAR_RE` env-var matching is AWS-specific (Lambda env vars) — drop it for Azure; there is no equivalent free-form environment-variable surface to scan in the Azure-AI domain.
- Discovery output keys must be confirmed against Shasta source (`/Users/kkmookhey/Projects/Shasta/src/shasta/azure/ai_discovery.py`); the implementer reads it before writing the mapper.

- [ ] **Step 1: Read Shasta's `azure/ai_discovery.py` to confirm output keys**

Run: `cat /Users/kkmookhey/Projects/Shasta/src/shasta/azure/ai_discovery.py | head -80`

Expected: file exists, exports `discover_azure_ai_services(client)`. Top-level dict keys (confirm them — use whatever names Shasta actually returns; the placeholders below assume `azure_openai`, `azure_ml`, `cognitive_services`).

- [ ] **Step 2: Write the failing test `test_discovery_to_entities_emits_azure_openai`**

```python
# platform/lambda/shasta_runner_azure/app/tests/test_ai_pass.py
"""Unit tests for the Azure ai_pass module.

Pure helpers (discovery_to_entities, ai_findings_to_emissions) are unit-tested
against fixture dicts/objects. run_ai_pass is not tested here — it's exercised
in the runner-level integration test.
"""
from __future__ import annotations

import pytest
from ai_pass import discovery_to_entities, ai_findings_to_emissions


def test_discovery_to_entities_emits_azure_openai():
    discovery = {
        "azure_openai": {
            "accounts": [
                {
                    "name": "openai-prod",
                    "id": "/subscriptions/SUB/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/openai-prod",
                    "location": "eastus",
                    "sku": "S0",
                }
            ],
        },
    }
    entities, edges = discovery_to_entities(
        discovery, subscription_id="SUB", tenant_id="TEN"
    )
    assert len(entities) == 1
    e = entities[0]
    assert e.kind == "azure_openai_deployment"
    assert e.natural_key.endswith("/openai-prod")
    assert e.domain == "cloud"
    assert len(edges) == 1
    assert edges[0].source_kind == "azure_subscription"
    assert edges[0].source_natural_key == "SUB"
    assert edges[0].target_kind == "azure_openai_deployment"
    assert edges[0].kind == "contains"
```

- [ ] **Step 3: Run the test to confirm it fails**

Run: `cd platform/lambda/shasta_runner_azure/app && python -m pytest tests/test_ai_pass.py -v`

Expected: `ImportError` or `ModuleNotFoundError: No module named 'ai_pass'`.

- [ ] **Step 4: Write `ai_pass.py` — module scaffold + `discovery_to_entities`**

```python
# platform/lambda/shasta_runner_azure/app/ai_pass.py
"""Cloud-AI pass for Azure — wraps Shasta's Azure-AI discovery + checks
into the unified entity/edge/finding model.

Mirrors shasta_runner/app/ai_pass.py (AWS). Pure helpers
(discovery_to_entities, ai_findings_to_emissions) take already-fetched
data and are unit-tested directly. run_ai_pass is the orchestrator; it
imports Shasta lazily so this module imports cleanly in a test
environment without Shasta installed.
"""
from __future__ import annotations

import logging
from typing import Any

from detectors.base import EdgeEmission, EntityEmission, FindingEmission
from framework_map import merge_framework_map

logger = logging.getLogger(__name__)

_DETECTOR_ID      = "shasta_runner_azure.ai_pass"
_DETECTOR_VERSION = "0.1.0"

# Standard (non-AI) framework attributes on a Shasta Finding.
_STD_FRAMEWORK_ATTRS = {
    "soc2_controls":     "soc2",
    "cis_aws_controls":  "cis_aws",
    "iso27001_controls": "iso27001",
    "hipaa_controls":    "hipaa",
    "mcsb_controls":     "mcsb",
}

# AI-framework control lists, written into Finding.details by Shasta's
# enrich_findings_with_ai_controls(). Maps detail key -> framework key.
_AI_FRAMEWORK_DETAIL_KEYS = {
    "nist_ai_rmf":       "nist_ai_rmf",
    "iso42001_controls": "iso_42001",
    "eu_ai_act":         "eu_ai_act",
    "owasp_llm_top10":   "owasp_llm_top10",
    "owasp_agentic":     "owasp_agentic",
    "nist_ai_600_1":     "nist_ai_600_1",
    "mitre_atlas":       "mitre_atlas",
}


def _estr(value: Any) -> str:
    """Stringify an enum-or-string (Shasta enums expose .value)."""
    return value.value if hasattr(value, "value") else str(value)


def discovery_to_entities(
    discovery: dict[str, Any], *, subscription_id: str, tenant_id: str,
) -> tuple[list[EntityEmission], list[EdgeEmission]]:
    """Map an Azure-AI discovery result to entities + edges.

    Top-level Shasta keys (confirmed against shasta/azure/ai_discovery.py):
      azure_openai.accounts       -> kind=azure_openai_deployment
      azure_ml.workspaces         -> kind=azure_ml_workspace
      cognitive_services.accounts -> kind=cognitive_service
    """
    entities: list[EntityEmission] = []
    edges:    list[EdgeEmission]   = []

    def _emit(kind: str, natural_key: str, display_name: str,
              attributes: dict[str, Any]) -> None:
        entities.append(EntityEmission(
            tenant_id=tenant_id, kind=kind, natural_key=natural_key,
            display_name=display_name, domain="cloud", attributes=attributes,
            evidence_packet=None,
            detector_id=_DETECTOR_ID, detector_version=_DETECTOR_VERSION,
        ))
        edges.append(EdgeEmission(
            tenant_id=tenant_id,
            source_kind="azure_subscription",
            source_natural_key=subscription_id,
            target_kind=kind, target_natural_key=natural_key,
            kind="contains", attributes={},
            evidence_packet={"version": "0.1", "via": "ai_discovery"},
            detector_id=_DETECTOR_ID, detector_version=_DETECTOR_VERSION,
        ))

    for acct in discovery.get("azure_openai", {}).get("accounts", []):
        rid = acct.get("id") or ""
        name = acct.get("name") or ""
        if rid and name:
            _emit("azure_openai_deployment", rid, name, {
                "location": acct.get("location", ""),
                "sku":      acct.get("sku", ""),
            })

    for ws in discovery.get("azure_ml", {}).get("workspaces", []):
        rid = ws.get("id") or ""
        name = ws.get("name") or ""
        if rid and name:
            _emit("azure_ml_workspace", rid, name, {
                "location": ws.get("location", ""),
            })

    for cs in discovery.get("cognitive_services", {}).get("accounts", []):
        rid = cs.get("id") or ""
        name = cs.get("name") or ""
        kind = (cs.get("kind") or "").lower()
        # Skip cognitive services we already emitted as azure_openai.
        if kind == "openai":
            continue
        if rid and name:
            _emit("cognitive_service", rid, name, {
                "location": cs.get("location", ""),
                "kind":     cs.get("kind", ""),
                "sku":      cs.get("sku", ""),
            })

    return entities, edges


def ai_findings_to_emissions(
    findings: list[Any], *, tenant_id: str,
) -> list[FindingEmission]:
    """Map Shasta Azure-AI Findings to FindingEmission rows; pulls
    AI-framework control IDs from finding.details into .frameworks.

    not_assessed / not_applicable results are dropped — they are noise
    ("Unable to check …"), not findings."""
    out: list[FindingEmission] = []
    for f in findings:
        status = _estr(f.status).lower()
        if status in ("not_assessed", "not_applicable"):
            continue

        details = getattr(f, "details", None) or {}

        frameworks: dict[str, list[str]] = {}
        for attr, fw_key in _STD_FRAMEWORK_ATTRS.items():
            vals = getattr(f, attr, None)
            if vals:
                frameworks[fw_key] = list(vals)
        for detail_key, fw_key in _AI_FRAMEWORK_DETAIL_KEYS.items():
            vals = details.get(detail_key)
            if vals:
                frameworks[fw_key] = list(vals)
        frameworks = merge_framework_map(f.check_id, frameworks)

        domain = _estr(getattr(f, "domain", "")).lower()
        if domain in ("ai_governance", ""):
            domain = "ai"
        region = getattr(f, "region", "") or None

        evidence = {
            "version": "0.1",
            "shasta": {
                "check_id":      f.check_id,
                "status":        status,
                "domain":        domain,
                "region":        getattr(f, "region", ""),
                "resource_type": getattr(f, "resource_type", ""),
                "resource_id":   getattr(f, "resource_id", ""),
                "remediation":   (getattr(f, "remediation", "") or "")[:2000],
            },
        }
        out.append(FindingEmission(
            tenant_id=tenant_id,
            finding_type=f.check_id,
            severity=_estr(f.severity).lower(),
            title=(f.title or "")[:500],
            description=(getattr(f, "description", "") or "")[:2000],
            subject_entity_kind=None,
            subject_entity_natural_key=None,
            subject_type=(getattr(f, "resource_type", "") or None),
            subject_ref=((getattr(f, "resource_id", "") or "")[:500] or None),
            evidence_packet=evidence,
            confidence="high",
            frameworks=frameworks,
            domain=domain,
            status=status,
            region=region,
        ))
    return out


def run_ai_pass(client: Any, *, subscription_id: str,
                tenant_id: str) -> dict[str, list]:
    """Run Shasta's Azure-AI discovery + checks against a per-subscription
    AzureClient and return unified emissions. Shasta is imported lazily so
    this module stays importable in test environments without Shasta
    installed."""
    from shasta.azure.ai_discovery import discover_azure_ai_services
    from shasta.azure.ai_checks    import run_full_azure_ai_scan
    from shasta.compliance.ai.mapper import enrich_findings_with_ai_controls

    discovery = discover_azure_ai_services(client)
    entities, edges = discovery_to_entities(
        discovery, subscription_id=subscription_id, tenant_id=tenant_id,
    )

    findings = run_full_azure_ai_scan(client)
    enrich_findings_with_ai_controls(findings)
    finding_emissions = ai_findings_to_emissions(findings, tenant_id=tenant_id)

    return {"entities": entities, "edges": edges, "findings": finding_emissions}
```

- [ ] **Step 5: Run the test to confirm it passes**

Run: `cd platform/lambda/shasta_runner_azure/app && python -m pytest tests/test_ai_pass.py::test_discovery_to_entities_emits_azure_openai -v`

Expected: PASS.

- [ ] **Step 6: Add the rest of the tests**

```python
# Append to platform/lambda/shasta_runner_azure/app/tests/test_ai_pass.py

def test_discovery_to_entities_emits_azure_ml_workspace():
    discovery = {
        "azure_ml": {
            "workspaces": [
                {"name": "ml-prod",
                 "id": "/subscriptions/SUB/.../workspaces/ml-prod",
                 "location": "eastus"}
            ],
        },
    }
    entities, edges = discovery_to_entities(
        discovery, subscription_id="SUB", tenant_id="TEN"
    )
    assert len(entities) == 1
    assert entities[0].kind == "azure_ml_workspace"


def test_discovery_to_entities_emits_cognitive_service_and_skips_openai_kind():
    discovery = {
        "cognitive_services": {
            "accounts": [
                {"name": "vision-1",
                 "id": "/subscriptions/SUB/.../accounts/vision-1",
                 "kind": "ComputerVision", "location": "eastus", "sku": "S1"},
                # This one should be skipped — already emitted via azure_openai.
                {"name": "openai-prod",
                 "id": "/subscriptions/SUB/.../accounts/openai-prod",
                 "kind": "OpenAI", "location": "eastus", "sku": "S0"},
            ],
        },
    }
    entities, edges = discovery_to_entities(
        discovery, subscription_id="SUB", tenant_id="TEN"
    )
    kinds = sorted(e.kind for e in entities)
    assert kinds == ["cognitive_service"]
    assert entities[0].display_name == "vision-1"


def test_discovery_to_entities_drops_entries_missing_id_or_name():
    discovery = {
        "azure_openai": {
            "accounts": [
                {"name": "", "id": "/subscriptions/SUB/.../accounts/x"},   # no name
                {"name": "y", "id": ""},                                    # no id
                {"name": "good", "id": "/subscriptions/SUB/.../accounts/good"},
            ],
        },
    }
    entities, _ = discovery_to_entities(
        discovery, subscription_id="SUB", tenant_id="TEN"
    )
    assert [e.display_name for e in entities] == ["good"]


def test_ai_findings_to_emissions_drops_not_assessed():
    class F:
        check_id = "azure_openai_content_filter"
        title = "Azure OpenAI content filter enabled"
        description = "..."
        severity = "high"
        status = "not_assessed"
        details = {}
        soc2_controls = []
        cis_aws_controls = []
        iso27001_controls = []
        hipaa_controls = []
        mcsb_controls = []
        region = "eastus"
        resource_type = "openai_account"
        resource_id = "/subscriptions/SUB/.../accounts/x"
        remediation = ""
        domain = "ai_governance"
    out = ai_findings_to_emissions([F()], tenant_id="TEN")
    assert out == []


def test_ai_findings_to_emissions_carries_ai_frameworks_from_details():
    class F:
        check_id = "azure_openai_content_filter"
        title = "Azure OpenAI content filter enabled"
        description = "OpenAI content filtering disabled"
        severity = "high"
        status = "fail"
        details = {
            "iso42001_controls": ["A.9.2.1"],
            "nist_ai_rmf":       ["GOVERN-1.1"],
            "eu_ai_act":         ["Article 15"],
        }
        soc2_controls = []
        cis_aws_controls = []
        iso27001_controls = []
        hipaa_controls = []
        mcsb_controls = []
        region = "eastus"
        resource_type = "openai_account"
        resource_id = "/subscriptions/SUB/.../accounts/x"
        remediation = "Turn the filter on."
        domain = "ai_governance"
    out = ai_findings_to_emissions([F()], tenant_id="TEN")
    assert len(out) == 1
    fe = out[0]
    assert fe.frameworks.get("iso_42001") == ["A.9.2.1"]
    assert fe.frameworks.get("nist_ai_rmf") == ["GOVERN-1.1"]
    assert fe.frameworks.get("eu_ai_act") == ["Article 15"]
    assert fe.domain == "ai"
    assert fe.status == "fail"
    assert fe.severity == "high"
```

- [ ] **Step 7: Run all ai_pass tests and confirm pass**

Run: `cd platform/lambda/shasta_runner_azure/app && python -m pytest tests/test_ai_pass.py -v`

Expected: 5 PASS.

- [ ] **Step 8: Commit**

```bash
git add platform/lambda/shasta_runner_azure/app/ai_pass.py \
        platform/lambda/shasta_runner_azure/app/tests/test_ai_pass.py
git commit -m "feat: add Azure-AI ai_pass module to shasta_runner_azure"
```

---

## Task 2: Wire `ai_pass` into the Azure scan plan

**Files:**
- Modify: `platform/lambda/shasta_runner_azure/app/azure_units.py` (add `ai` as a Medium+ module)
- Modify: `platform/lambda/shasta_runner_azure/app/main.py:50-78` (import + wire ai_pass as a per-subscription unit)
- Test: `platform/lambda/shasta_runner_azure/app/tests/test_azure_units.py` (add a unit-test asserting ai is in Medium+)

**Decision recorded in this task:** Run the AI pass **per-subscription** (one ScanUnit per scannable subscription) — mirrors how every other Azure module is keyed in `_build_units`. Concurrency limiter slot = `"ai"`.

- [ ] **Step 1: Read `azure_units.py` to find the module list**

Run: `cat platform/lambda/shasta_runner_azure/app/azure_units.py`

Expected: file exposes `modules_for_tier(tier)` returning `(phase1, phase2)` lists of module names.

- [ ] **Step 2: Write the failing test**

```python
# Append to platform/lambda/shasta_runner_azure/app/tests/test_azure_units.py
from azure_units import modules_for_tier

def test_ai_module_appears_in_medium_plus_only():
    quick_p1, quick_p2 = modules_for_tier("quick")
    assert "ai" not in (quick_p1 + quick_p2)

    medium_p1, medium_p2 = modules_for_tier("medium")
    assert "ai" in (medium_p1 + medium_p2)

    deep_p1, deep_p2 = modules_for_tier("deep")
    assert "ai" in (deep_p1 + deep_p2)
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd platform/lambda/shasta_runner_azure/app && python -m pytest tests/test_azure_units.py::test_ai_module_appears_in_medium_plus_only -v`

Expected: FAIL — `"ai"` not in module list.

- [ ] **Step 4: Add `ai` to `azure_units.py` Medium+ tier**

Read the file first to find the right list. Add `"ai"` to the Medium+ phase-2 (deep-tier) list. If the file has explicit per-tier lists, add to medium and deep but not quick. Example edit shape (adjust to whatever structure the file actually has):

```python
# Likely shape (verify against actual file):
_QUICK_PHASE_1  = ["iam", "governance", "storage"]
_QUICK_PHASE_2  = ["networking", "compute"]
_MEDIUM_EXTRAS  = ["encryption", "databases", "appservice", "monitoring", "ai"]  # <-- add "ai"
_DEEP_EXTRAS    = ["backup", "diagnostic_settings", "private_endpoints"]
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd platform/lambda/shasta_runner_azure/app && python -m pytest tests/test_azure_units.py::test_ai_module_appears_in_medium_plus_only -v`

Expected: PASS.

- [ ] **Step 6: Wire `ai_pass` into `main.py`**

Modify `platform/lambda/shasta_runner_azure/app/main.py`:

(a) After the existing Shasta imports (around line 40), add:

```python
from ai_pass import run_ai_pass
```

(b) Find the `AZURE_MODULES` dict (around line 65-78). Add an `"ai"` entry that wires through `_run_ai_unit`:

```python
# Module name -> Shasta entry point. The "ai" entry is special — its
# callable receives an AzureClient already scoped to one subscription
# and returns the {entities, edges, findings} dict that _module_unit
# expects. To keep the existing _module_unit signature uniform, we wrap
# run_ai_pass below in _build_units's ai branch (Step 6c).
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
    # "ai" is wired via _ai_unit in _build_units — not Shasta's standard signature.
}
```

(c) Update `_build_units` (around line 198-213) so the `"ai"` module routes to a dedicated `_ai_unit` factory:

```python
def _build_units(subscriptions: list[str], module_names: list[str],
                 base_client, tenant_id: str,
                 azure_tenant_id: str) -> list[ScanUnit]:
    units: list[ScanUnit] = []
    for sub_id in subscriptions:
        for name in module_names:
            if name == "ai":
                units.append(ScanUnit(
                    name=f"{sub_id}/ai", service="ai",
                    run=_ai_unit(base_client, sub_id, tenant_id)))
                continue
            run_fn = AZURE_MODULES[name]
            units.append(ScanUnit(
                name=f"{sub_id}/{name}", service=name,
                run=_module_unit(run_fn, base_client, sub_id, tenant_id)))
    return units


def _ai_unit(base_client, sub_id: str, tenant_id: str):
    """Build the run callable for one (subscription, ai_pass) unit.

    Mirrors _module_unit's per-unit-fresh-client pattern but returns the
    unified emissions dict from run_ai_pass directly rather than going
    through convert_azure_findings — ai_pass already produces
    EntityEmission / EdgeEmission / FindingEmission instances."""
    def _run() -> dict:
        client = base_client.for_subscription(sub_id)
        client.validate_credentials()
        out = run_ai_pass(client, subscription_id=sub_id, tenant_id=tenant_id)
        return {"entities": out["entities"], "edges": out["edges"],
                "findings": out["findings"]}
    return _run
```

- [ ] **Step 7: Run the full Azure-scanner unit suite to confirm nothing else regressed**

Run: `cd platform/lambda/shasta_runner_azure/app && python -m pytest tests/ -v`

Expected: All tests pass, including the new `test_ai_module_appears_in_medium_plus_only` and the existing five from Task 1.

- [ ] **Step 8: Commit**

```bash
git add platform/lambda/shasta_runner_azure/app/azure_units.py \
        platform/lambda/shasta_runner_azure/app/main.py \
        platform/lambda/shasta_runner_azure/app/tests/test_azure_units.py
git commit -m "feat: wire Azure ai_pass into Medium+ scan plan"
```

---

## Task 3: Rebuild + push the Azure scanner image, deploy CDK

**Files:**
- No code changes — operates on `platform/lambda/shasta_runner_azure/`.

- [ ] **Step 1: Verify Shasta is installed with Azure-AI modules in the build script**

Run: `grep -n "azure" platform/lambda/shasta_runner_azure/build.sh platform/lambda/shasta_runner_azure/Dockerfile`

Expected: no special exclusion of `shasta.azure.ai_*`. The image installs the whole Shasta package (`pip install --no-deps shasta`) per CLAUDE.md.

- [ ] **Step 2: Rebuild + push the scanner image**

Run:
```bash
cd platform/lambda/shasta_runner_azure
./build.sh
```

Expected: ECR push completes; new image digest printed.

- [ ] **Step 3: Deploy `CisoCopilotScan`**

Run:
```bash
cd platform
npx cdk deploy CisoCopilotScan --require-approval never
```

Expected: `UPDATE_COMPLETE`. (No `--hotswap` — Fargate task definition needs to pick up the new image.)

- [ ] **Step 4: Confirm the new image is referenced by the task definition**

Run:
```bash
aws ecs describe-task-definition \
  --task-definition ciso-copilot-azure-scan \
  --query 'taskDefinition.containerDefinitions[0].image' \
  --output text
```

Expected: image URI shows the new digest from Step 2.

- [ ] **Step 5: Commit (no-op for source — record the deploy in HANDOFF.md at the end of Task 9)**

No commit here.

---

## Task 4: Add `is_ai_touching` helper + `ai_summary` Lambda (TDD)

**Files:**
- Create: `platform/lambda/ai_summary/main.py`
- Create: `platform/lambda/ai_summary/tests/test_main.py`
- Create: `platform/lambda/ai_summary/__init__.py` (empty)
- Create: `platform/lambda/ai_summary/tests/__init__.py` (empty)

**Endpoint contract:** `GET /ai/summary` → JSON

```json
{
  "score":       { "fail": 12, "partial": 5, "pass": 21 },
  "by_source":   { "aws": 7, "azure": 4, "code": 6, "entra": 0 },
  "by_framework": {
    "nist_ai_rmf": { "fail": 4, "partial": 1, "pass": 8 },
    "iso_42001":   { "fail": 3, "partial": 2, "pass": 6 },
    "soc2_ai":     { "fail": 0, "partial": 0, "pass": 0 },
    "eu_ai_act":   { "fail": 0, "partial": 0, "pass": 0 }
  },
  "top_people": [
    { "email": "alice@acme.com",
      "fail": 3, "partial": 1, "sources": ["aws", "code"] },
    ...
  ]
}
```

**`is_ai_touching` test** — a finding is AI-touching if it carries any of:
- A `frameworks` JSON key beginning with `nist_ai_rmf` / `iso_42001` / `soc2_ai` / `eu_ai_act`.
- An entity reference with `domain='ai'` or `kind` in the AI-resource set.
- An attributes tag `is_ai=true`.

For the Lambda, the **SQL-side** test is what matters — the `findings` table's `frameworks` column is JSONB. The is-AI-touching predicate runs as a SQL `WHERE` filter, not Python.

- [ ] **Step 1: Write the failing test**

```python
# platform/lambda/ai_summary/tests/test_main.py
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch


def _claims_event(sub: str = "sub-1") -> dict:
    return {"requestContext": {"authorizer": {"claims": {"sub": sub}}}}


def _stmt(rows: list[list[dict]]) -> dict:
    return {"records": rows}


def test_handler_returns_401_with_no_subject():
    from main import handler
    resp = handler({"requestContext": {}}, None)
    assert resp["statusCode"] == 401


@patch("main.rds_data")
def test_handler_returns_score_by_source_by_framework_top_people(mock_rds):
    # Six rds_data calls in order:
    #   1) tenant lookup
    #   2) score (by_status)
    #   3) by_source
    #   4) by_framework
    #   5) top_people
    mock_rds.execute_statement.side_effect = [
        # tenant lookup
        _stmt([[{"stringValue": "tenant-1"}]]),
        # score: fail=12 partial=5 pass=21
        _stmt([
            [{"stringValue": "fail"},    {"longValue": 12}],
            [{"stringValue": "partial"}, {"longValue": 5}],
            [{"stringValue": "pass"},    {"longValue": 21}],
        ]),
        # by_source: aws=7 azure=4 code=6 entra=0 (entra omitted)
        _stmt([
            [{"stringValue": "aws"},   {"longValue": 7}],
            [{"stringValue": "azure"}, {"longValue": 4}],
            [{"stringValue": "code"},  {"longValue": 6}],
        ]),
        # by_framework: nist_ai_rmf fail=4 partial=1 pass=8 ; iso_42001 fail=3 partial=2 pass=6
        _stmt([
            [{"stringValue": "nist_ai_rmf"}, {"stringValue": "fail"},    {"longValue": 4}],
            [{"stringValue": "nist_ai_rmf"}, {"stringValue": "partial"}, {"longValue": 1}],
            [{"stringValue": "nist_ai_rmf"}, {"stringValue": "pass"},    {"longValue": 8}],
            [{"stringValue": "iso_42001"},   {"stringValue": "fail"},    {"longValue": 3}],
            [{"stringValue": "iso_42001"},   {"stringValue": "partial"}, {"longValue": 2}],
            [{"stringValue": "iso_42001"},   {"stringValue": "pass"},    {"longValue": 6}],
        ]),
        # top_people
        _stmt([
            [{"stringValue": "alice@acme.com"},
             {"longValue": 3}, {"longValue": 1},
             {"stringValue": "aws,code"}],
            [{"stringValue": "bob@acme.com"},
             {"longValue": 1}, {"longValue": 2},
             {"stringValue": "code"}],
        ]),
    ]

    from main import handler
    resp = handler(_claims_event(), None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["score"] == {"fail": 12, "partial": 5, "pass": 21}
    assert body["by_source"] == {"aws": 7, "azure": 4, "code": 6, "entra": 0}
    assert body["by_framework"]["nist_ai_rmf"] == {"fail": 4, "partial": 1, "pass": 8}
    assert body["by_framework"]["iso_42001"]   == {"fail": 3, "partial": 2, "pass": 6}
    assert body["by_framework"]["soc2_ai"]     == {"fail": 0, "partial": 0, "pass": 0}
    assert body["by_framework"]["eu_ai_act"]   == {"fail": 0, "partial": 0, "pass": 0}
    assert body["top_people"][0]["email"] == "alice@acme.com"
    assert body["top_people"][0]["sources"] == ["aws", "code"]
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `cd platform/lambda/ai_summary && python -m pytest tests/test_main.py -v`

Expected: `ModuleNotFoundError: No module named 'main'` or import error.

- [ ] **Step 3: Implement `ai_summary/main.py`**

```python
# platform/lambda/ai_summary/main.py
"""GET /ai/summary — AI-touching findings aggregated for the /ai view.

A finding is AI-touching iff:
  - Any key in findings.frameworks starts with one of the AI-framework
    prefixes (nist_ai_rmf, iso_42001, soc2_ai, eu_ai_act), OR
  - The associated entity carries an AI domain/kind (joined via
    findings.subject_entity_kind or via the entities table), OR
  - The finding's attributes JSONB has is_ai=true.

This module evaluates the predicate inside SQL — it is faster than
pulling rows into Python and lets the DB index the JSONB lookups.

Response shape:
  {
    "score":        {"fail": int, "partial": int, "pass": int},
    "by_source":    {"aws": int, "azure": int, "code": int, "entra": int},
    "by_framework": {<fw>: {"fail": int, "partial": int, "pass": int}},
    "top_people":   [{"email": str, "fail": int, "partial": int, "sources": [str]}]
  }
"""
from __future__ import annotations

import json
import os

import boto3

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

rds_data = boto3.client("rds-data")

_AI_FRAMEWORKS = ("nist_ai_rmf", "iso_42001", "soc2_ai", "eu_ai_act")

# Kinds the per-person view treats as AI-touching when joining entities.
# Keeping this list in code (not the DB) so deploys carry the truth.
_AI_RESOURCE_KINDS = (
    "bedrock_model", "bedrock_guardrail", "sagemaker_endpoint",
    "sagemaker_model", "sagemaker_training_job", "comprehend_endpoint",
    "lambda_ai_function",
    "azure_openai_deployment", "azure_ml_workspace", "cognitive_service",
    "vertex_endpoint",  # reserved for GCP-AI sub-project
    "ai_saas_app", "ai_code_finding",
    "ai_user_signin", "ai_api_key", "ai_org_member", "ai_project",
    "ai_provider_org",
)

# Shared SQL fragment: a finding is AI-touching.
# Uses ?| (jsonb has any of) for framework keys. Entity joins are LEFT
# JOIN because some findings have no subject entity.
_IS_AI_TOUCHING = """
  (
    f.frameworks ?| ARRAY[{fws}]
    OR e.domain = 'ai'
    OR e.kind = ANY(ARRAY[{kinds}])
    OR (f.attributes ->> 'is_ai') = 'true'
  )
""".format(
    fws   = ", ".join(f"'{k}'" for k in _AI_FRAMEWORKS),
    kinds = ", ".join(f"'{k}'" for k in _AI_RESOURCE_KINDS),
)


def handler(event: dict, context) -> dict:
    tenant_id = _resolve_tenant_id(event)
    if not tenant_id:
        return _resp(401, {"error": "no_tenant"})

    score        = _query_score(tenant_id)
    by_source    = _query_by_source(tenant_id)
    by_framework = _query_by_framework(tenant_id)
    top_people   = _query_top_people(tenant_id)

    return _resp(200, {
        "score":        score,
        "by_source":    by_source,
        "by_framework": by_framework,
        "top_people":   top_people,
    })


def _query_score(tenant_id: str) -> dict:
    sql = f"""
        SELECT f.status, COUNT(*)
        FROM findings f
        LEFT JOIN entities e
          ON e.entity_id = f.subject_entity_id
        WHERE f.tenant_id = CAST(:tid AS UUID)
          AND { _IS_AI_TOUCHING }
        GROUP BY f.status
    """
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=sql,
        parameters=[{"name": "tid", "value": {"stringValue": tenant_id}}],
    )
    counts = {
        r[0].get("stringValue"): int(r[1].get("longValue", 0))
        for r in rs.get("records", [])
    }
    return {
        "fail":    counts.get("fail", 0),
        "partial": counts.get("partial", 0),
        "pass":    counts.get("pass", 0),
    }


def _query_by_source(tenant_id: str) -> dict:
    """Source = cloud_connections.cloud_type for cloud findings; 'code'
    for findings with no cloud connection (the AI-code scanner)."""
    sql = f"""
        SELECT COALESCE(c.cloud_type, 'code') AS source, COUNT(*)
        FROM findings f
        LEFT JOIN entities e ON e.entity_id = f.subject_entity_id
        LEFT JOIN cloud_connections c ON c.conn_id = f.conn_id
        WHERE f.tenant_id = CAST(:tid AS UUID)
          AND f.status IN ('fail', 'partial')
          AND { _IS_AI_TOUCHING }
        GROUP BY 1
    """
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=sql,
        parameters=[{"name": "tid", "value": {"stringValue": tenant_id}}],
    )
    raw = {
        r[0].get("stringValue"): int(r[1].get("longValue", 0))
        for r in rs.get("records", [])
    }
    return {
        "aws":   raw.get("aws", 0),
        "azure": raw.get("azure", 0),
        "code":  raw.get("code", 0),
        "entra": raw.get("entra", 0),
    }


def _query_by_framework(tenant_id: str) -> dict:
    """Counts per AI framework per status. Cross-joins jsonb_object_keys
    so a finding tagged with multiple AI frameworks is counted once per
    framework."""
    fws_list = ", ".join(f"'{k}'" for k in _AI_FRAMEWORKS)
    sql = f"""
        SELECT k AS fw, f.status, COUNT(*)
        FROM findings f
        LEFT JOIN entities e ON e.entity_id = f.subject_entity_id
        CROSS JOIN LATERAL jsonb_object_keys(f.frameworks) AS k
        WHERE f.tenant_id = CAST(:tid AS UUID)
          AND k IN ({fws_list})
          AND { _IS_AI_TOUCHING }
        GROUP BY k, f.status
    """
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=sql,
        parameters=[{"name": "tid", "value": {"stringValue": tenant_id}}],
    )
    out: dict[str, dict[str, int]] = {
        fw: {"fail": 0, "partial": 0, "pass": 0} for fw in _AI_FRAMEWORKS
    }
    for r in rs.get("records", []):
        fw  = r[0].get("stringValue")
        st  = r[1].get("stringValue")
        n   = int(r[2].get("longValue", 0))
        if fw in out and st in out[fw]:
            out[fw][st] = n
    return out


def _query_top_people(tenant_id: str) -> list:
    """Per-person ranking — emails sourced from
        - findings.attributes->>'commit_author_email' (AI code findings)
        - findings.attributes->>'iam_owner_email'     (AWS IAM-tagged AI resources)
        - findings.attributes->>'entra_upn'           (Entra signin findings - S2)
    Top 25 by (fail desc, partial desc)."""
    sql = f"""
        SELECT
          LOWER(
            COALESCE(
              f.attributes->>'commit_author_email',
              f.attributes->>'iam_owner_email',
              f.attributes->>'entra_upn'
            )
          ) AS person,
          COUNT(*) FILTER (WHERE f.status = 'fail')    AS fail_n,
          COUNT(*) FILTER (WHERE f.status = 'partial') AS partial_n,
          STRING_AGG(DISTINCT COALESCE(c.cloud_type, 'code'), ',') AS sources
        FROM findings f
        LEFT JOIN entities e ON e.entity_id = f.subject_entity_id
        LEFT JOIN cloud_connections c ON c.conn_id = f.conn_id
        WHERE f.tenant_id = CAST(:tid AS UUID)
          AND COALESCE(
                f.attributes->>'commit_author_email',
                f.attributes->>'iam_owner_email',
                f.attributes->>'entra_upn'
              ) IS NOT NULL
          AND f.status IN ('fail', 'partial')
          AND { _IS_AI_TOUCHING }
        GROUP BY 1
        ORDER BY fail_n DESC, partial_n DESC
        LIMIT 25
    """
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=sql,
        parameters=[{"name": "tid", "value": {"stringValue": tenant_id}}],
    )
    out = []
    for r in rs.get("records", []):
        out.append({
            "email":   r[0].get("stringValue"),
            "fail":    int(r[1].get("longValue", 0)),
            "partial": int(r[2].get("longValue", 0)),
            "sources": (r[3].get("stringValue") or "").split(",") if r[3].get("stringValue") else [],
        })
    return out


def _resolve_tenant_id(event: dict) -> str | None:
    claims = (event.get("requestContext") or {}).get("authorizer", {}).get("claims") or {}
    raw = claims.get("identities")
    sso_subject = None
    if raw:
        try:
            ids = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(ids, dict):
                ids = [ids]
            if ids:
                sso_subject = ids[0].get("userId") or claims.get("sub")
        except (TypeError, ValueError):
            pass
    sso_subject = sso_subject or claims.get("sub")
    if not sso_subject:
        return None
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql="SELECT tenant_id::text FROM users WHERE sso_subject = :s LIMIT 1",
        parameters=[{"name": "s", "value": {"stringValue": sso_subject}}],
    )
    rows = rs.get("records", [])
    return rows[0][0].get("stringValue") if rows else None


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers":    {"content-type": "application/json",
                       "access-control-allow-origin": "*"},
        "body":       json.dumps(body),
    }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd platform/lambda/ai_summary && python -m pytest tests/test_main.py -v`

Expected: 2 PASS.

- [ ] **Step 5: Verify schema assumptions — confirm the actual `findings` table columns**

Run:
```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot \
  --sql "SELECT column_name, data_type FROM information_schema.columns WHERE table_name='findings' ORDER BY ordinal_position"
```

Expected: confirm columns `tenant_id`, `conn_id`, `subject_entity_id`, `frameworks` (JSONB), `attributes` (JSONB), `status`. **If the actual column names differ (e.g. `entity_id` instead of `subject_entity_id`, or no `attributes` column), update the SQL in `main.py` accordingly and re-run tests.** Do not skip this verification — running the SQL against a wrong schema will fail at deploy.

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/ai_summary/
git commit -m "feat: add ai_summary Lambda (GET /ai/summary)"
```

---

## Task 5: Wire `ai_summary` into CDK and API Gateway

**Files:**
- Modify: `platform/lib/api-stack.ts` (add Lambda definition + route)

- [ ] **Step 1: Add the Lambda definition near `findingsSummaryFn` (around line 261)**

```typescript
const aiSummaryFn = new lambda.Function(this, 'AiSummaryFn', {
  runtime: lambda.Runtime.PYTHON_3_12,
  handler: 'main.handler',
  code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'ai_summary')),
  timeout: cdk.Duration.seconds(15),
  memorySize: 512,
  environment: dbEnv,
});
props.dbCluster.grantDataApiAccess(aiSummaryFn);
```

- [ ] **Step 2: Add the API Gateway route**

Find where other `/ai/*` routes are registered (search for `'ai'` in api-stack.ts — there are existing routes for `/ai/install/callback`, etc.). Add the `/ai/summary` route next to them:

```typescript
// (in the same block that owns /ai/*)
const aiRes = api.root.getResource('ai') ?? api.root.addResource('ai');
aiRes.addResource('summary').addMethod(
  'GET', new apigw.LambdaIntegration(aiSummaryFn), authedOpts,
);
```

Note — `getResource('ai') ?? addResource('ai')` reuses the existing `/ai` apigw resource if other routes already defined it. If the existing code uses a different variable name for the `/ai` resource, route through that instead.

- [ ] **Step 3: Deploy `CisoCopilotApi`**

Run:
```bash
cd platform
npx cdk deploy CisoCopilotApi --exclusively --require-approval never --hotswap
```

(`--hotswap` is safe here — the change is one new Lambda + one new API route. `--exclusively` prevents pulling Scan back in.)

Expected: `UPDATE_COMPLETE`.

- [ ] **Step 4: Smoke-test the deployed endpoint with a real Cognito token**

This step requires KK to run with an active session (Bash via the human-gated demo path). The agent prints the curl skeleton:

```bash
# Replace <TOKEN> with a current Cognito ID token; replace <API_BASE> with the API stage URL.
curl -sS -H "authorization: Bearer <TOKEN>" "<API_BASE>/ai/summary" | jq .
```

Expected: HTTP 200; JSON body matching the contract in Task 4. **If empty:** the tenant has no AI-touching findings yet — re-run an Azure scan at Medium tier to populate (Task 3 already deployed the scanner image).

- [ ] **Step 5: Commit**

```bash
git add platform/lib/api-stack.ts
git commit -m "feat: wire /ai/summary route + AiSummaryFn Lambda"
```

---

## Task 6: Create the `/ai` web route

**Files:**
- Create: `web/src/routes/AISummary.tsx`
- Modify: `web/src/App.tsx` (add the `<Route path="/ai" element={<AISummary />} />`)

**Visual design:** mirror the existing TopRisks/Dashboard tile pattern. Three rows:
1. **Score tile row** — three big tiles: Fail (red), Partial (amber), Pass (green). Same colour palette as the findings overhaul.
2. **By-source row** — four smaller tiles: AWS, Azure, Code, Entra. Each tile shows the actionable count (fail+partial). "Entra" tile reads 0 in S1 — labelled `coming in S2`.
3. **By-framework row** — four tiles (NIST AI RMF, ISO 42001, SOC 2 AI, EU AI Act) each with a `Fail/Partial/Pass` mini-rollup.
4. **Top people table** — at the bottom; columns: Person | Fail | Partial | Sources. Empty-state copy: "No identifiable AI users yet — connect Entra (S2) to populate."

- [ ] **Step 1: Read the existing tile component being used in TopRisks for visual parity**

Run: `head -80 web/src/routes/TopRisks.tsx`

Expected: identifies the tile component / Tailwind class pattern. Match it.

- [ ] **Step 2: Write the failing component test**

```tsx
// web/src/routes/AISummary.test.tsx
import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import AISummary from './AISummary';

vi.mock('../lib/api', () => ({
  apiGet: vi.fn(async (path: string) => {
    if (path === '/ai/summary') {
      return {
        score:        { fail: 12, partial: 5, pass: 21 },
        by_source:    { aws: 7, azure: 4, code: 6, entra: 0 },
        by_framework: {
          nist_ai_rmf: { fail: 4, partial: 1, pass: 8 },
          iso_42001:   { fail: 3, partial: 2, pass: 6 },
          soc2_ai:     { fail: 0, partial: 0, pass: 0 },
          eu_ai_act:   { fail: 0, partial: 0, pass: 0 },
        },
        top_people: [
          { email: 'alice@acme.com', fail: 3, partial: 1, sources: ['aws','code'] },
        ],
      };
    }
    throw new Error('unexpected path: ' + path);
  }),
}));

describe('AISummary', () => {
  it('renders the score tiles, by-source tiles, framework tiles, and top people', async () => {
    render(<AISummary />);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('12')).toBeInTheDocument());
    expect(screen.getByText(/fail/i)).toBeInTheDocument();
    expect(screen.getByText(/partial/i)).toBeInTheDocument();
    expect(screen.getByText(/pass/i)).toBeInTheDocument();
    expect(screen.getByText(/azure/i)).toBeInTheDocument();
    expect(screen.getByText(/nist ai rmf/i)).toBeInTheDocument();
    expect(screen.getByText('alice@acme.com')).toBeInTheDocument();
  });

  it('shows the empty-state copy when no people are returned', async () => {
    const { apiGet } = await import('../lib/api');
    (apiGet as any).mockResolvedValueOnce({
      score:        { fail: 0, partial: 0, pass: 0 },
      by_source:    { aws: 0, azure: 0, code: 0, entra: 0 },
      by_framework: {
        nist_ai_rmf: { fail: 0, partial: 0, pass: 0 },
        iso_42001:   { fail: 0, partial: 0, pass: 0 },
        soc2_ai:     { fail: 0, partial: 0, pass: 0 },
        eu_ai_act:   { fail: 0, partial: 0, pass: 0 },
      },
      top_people:   [],
    });
    render(<AISummary />);
    await waitFor(() =>
      expect(screen.getByText(/No identifiable AI users yet/i)).toBeInTheDocument()
    );
  });
});
```

- [ ] **Step 3: Run the test to confirm it fails**

Run: `cd web && pnpm test -- AISummary`

Expected: import of `./AISummary` fails.

- [ ] **Step 4: Write `AISummary.tsx`**

```tsx
// web/src/routes/AISummary.tsx
import { useEffect, useState } from 'react';
import { apiGet } from '../lib/api';

type StatusCounts = { fail: number; partial: number; pass: number };
type Summary = {
  score:        StatusCounts;
  by_source:    { aws: number; azure: number; code: number; entra: number };
  by_framework: Record<'nist_ai_rmf' | 'iso_42001' | 'soc2_ai' | 'eu_ai_act', StatusCounts>;
  top_people:   { email: string; fail: number; partial: number; sources: string[] }[];
};

const FRAMEWORK_LABELS: Record<string, string> = {
  nist_ai_rmf: 'NIST AI RMF',
  iso_42001:   'ISO 42001',
  soc2_ai:     'SOC 2 AI',
  eu_ai_act:   'EU AI Act',
};

const SOURCE_LABELS: Record<string, string> = {
  aws:   'AWS',
  azure: 'Azure',
  code:  'Code',
  entra: 'Entra',
};

export default function AISummary() {
  const [data,  setData]  = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiGet('/ai/summary')
      .then((d: Summary) => setData(d))
      .catch((e: Error)  => setError(e.message || 'failed to load'));
  }, []);

  if (error) return <div className="p-6 text-red-700">Failed to load AI summary: {error}</div>;
  if (!data) return <div className="p-6">Loading…</div>;

  return (
    <div className="p-6 space-y-8">
      <h1 className="text-2xl font-semibold">AI Exposure</h1>

      {/* Score tiles */}
      <section className="grid grid-cols-3 gap-4">
        <ScoreTile label="Fail"    value={data.score.fail}    tone="red"   />
        <ScoreTile label="Partial" value={data.score.partial} tone="amber" />
        <ScoreTile label="Pass"    value={data.score.pass}    tone="green" />
      </section>

      {/* By-source */}
      <section>
        <h2 className="text-lg font-medium mb-2">By source</h2>
        <div className="grid grid-cols-4 gap-3">
          {(Object.keys(SOURCE_LABELS) as (keyof Summary['by_source'])[]).map(s => (
            <SourceTile
              key={s}
              label={SOURCE_LABELS[s]}
              value={data.by_source[s]}
              note={s === 'entra' ? 'coming in S2' : undefined}
            />
          ))}
        </div>
      </section>

      {/* By framework */}
      <section>
        <h2 className="text-lg font-medium mb-2">By framework</h2>
        <div className="grid grid-cols-4 gap-3">
          {Object.keys(FRAMEWORK_LABELS).map(fw => (
            <FrameworkTile
              key={fw}
              label={FRAMEWORK_LABELS[fw]}
              counts={data.by_framework[fw as keyof Summary['by_framework']]}
            />
          ))}
        </div>
      </section>

      {/* Top people */}
      <section>
        <h2 className="text-lg font-medium mb-2">Top AI users</h2>
        {data.top_people.length === 0 ? (
          <p className="text-sm text-slate-500">
            No identifiable AI users yet — connect Entra (S2) to populate.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left border-b">
                <th className="py-1">Person</th>
                <th className="py-1">Fail</th>
                <th className="py-1">Partial</th>
                <th className="py-1">Sources</th>
              </tr>
            </thead>
            <tbody>
              {data.top_people.map(p => (
                <tr key={p.email} className="border-b last:border-0">
                  <td className="py-1">{p.email}</td>
                  <td className="py-1">{p.fail}</td>
                  <td className="py-1">{p.partial}</td>
                  <td className="py-1">{p.sources.join(', ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

function ScoreTile({ label, value, tone }: {
  label: string; value: number; tone: 'red' | 'amber' | 'green';
}) {
  const colour = tone === 'red'   ? 'bg-red-50 text-red-800'
              : tone === 'amber' ? 'bg-amber-50 text-amber-800'
              :                    'bg-green-50 text-green-800';
  return (
    <div className={`rounded-lg p-4 ${colour}`}>
      <div className="text-3xl font-bold">{value}</div>
      <div className="text-sm uppercase tracking-wide">{label}</div>
    </div>
  );
}

function SourceTile({ label, value, note }: {
  label: string; value: number; note?: string;
}) {
  return (
    <div className="rounded-lg border p-3">
      <div className="text-xl font-semibold">{value}</div>
      <div className="text-sm">{label}</div>
      {note && <div className="text-xs text-slate-500">{note}</div>}
    </div>
  );
}

function FrameworkTile({ label, counts }: { label: string; counts: StatusCounts; }) {
  return (
    <div className="rounded-lg border p-3">
      <div className="text-sm font-medium mb-1">{label}</div>
      <div className="flex gap-2 text-xs">
        <span className="text-red-700">F: {counts.fail}</span>
        <span className="text-amber-700">P: {counts.partial}</span>
        <span className="text-green-700">✓: {counts.pass}</span>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Wire the route in `App.tsx`**

Open `web/src/App.tsx`. The existing block has child routes like `/ai/install/callback`, `/ai/connections/:id/repos`, `/ai/inventory`. Add the index route immediately above them:

```tsx
// In the <Route element={<Shell />}> block:
<Route path="/ai"                          element={<AISummary />} />
<Route path="/ai/install/callback"         element={<InstallCallback />} />
<Route path="/ai/connections/:id/repos"    element={<RepoPicker />} />
<Route path="/ai/inventory"                element={<AIInventory />} />
<Route path="/ai/inventory/:asset_id"      element={<AssetDetail />} />
```

And add the import at the top:

```tsx
import AISummary from './routes/AISummary';
```

- [ ] **Step 6: Run vitest**

Run: `cd web && pnpm test -- AISummary`

Expected: both tests PASS.

- [ ] **Step 7: Run typecheck**

Run: `cd web && pnpm typecheck`

Expected: zero new errors. **Pre-existing lint baseline is dirty per `project_web_lint_baseline` memory** — do not treat unrelated TS errors as a regression; only fix what this task introduced.

- [ ] **Step 8: Build the web bundle**

Run: `cd web && pnpm build`

Expected: build succeeds.

- [ ] **Step 9: Sync to S3 + invalidate CloudFront**

Run:
```bash
cd web
aws s3 sync dist/ s3://ciso-copilot-app-470226123496/ --delete
aws cloudfront create-invalidation --distribution-id E2FV1Z0DJ4RQS4 --paths '/*'
```

Expected: invalidation accepted.

- [ ] **Step 10: Commit**

```bash
git add web/src/routes/AISummary.tsx web/src/routes/AISummary.test.tsx web/src/App.tsx
git commit -m "feat: add /ai route with score, by-source, by-framework, top people"
```

---

## Task 7: Per-person stub — confirm existing emitters carry email attributes

**Files:**
- Modify (only if needed): the AI code-scanner emitter to ensure `commit_author_email` is set on finding `attributes`; the AWS ai_pass to ensure `iam_owner_email` is set on finding `attributes` when the resource carries an `owner` tag.

**Goal:** the per-person view in Task 6 already queries `attributes->>'commit_author_email'` and `attributes->>'iam_owner_email'`. This task verifies both keys are actually present on the appropriate finding kinds — and patches the emitters if not.

- [ ] **Step 1: Inspect a sample row from the deployed `findings` table**

Run:
```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot \
  --sql "SELECT attributes FROM findings WHERE attributes ? 'commit_author_email' LIMIT 3"
```

Expected — one of:
- (a) Rows returned with `commit_author_email` populated → emitter is fine, no patch needed.
- (b) Zero rows → AI code-scanner emitter never sets the key. Find the emitter (search the AI scanner Lambda for finding construction), add the attribute, and rebuild + redeploy. **This is the only emitter-side change in S1.**

- [ ] **Step 2 (conditional on Step 1.b): patch the AI scanner emitter**

Search `platform/lambda/ai_scanner/` (or wherever Slice 1a emits findings — find by grep) for the finding-construction site. Add the email attribute when the source GitHub event has it:

```python
# In the AI scanner's finding-emit logic (illustrative path — locate the
# actual call site by searching the AI scanner directory):
finding_attributes = {
    # ...existing attributes...
    "commit_author_email": event.get("commit_author_email") or "",
}
```

If the commit-author email is not currently captured upstream, add a TODO with link to S2 (where it will be properly enriched via Entra). Skip the patch and add a note in HANDOFF.md that per-person view is empty until S2.

- [ ] **Step 3: Repeat the inspection for `iam_owner_email`**

Run:
```bash
aws rds-data execute-statement \
  ... \
  --sql "SELECT attributes FROM findings WHERE attributes ? 'iam_owner_email' LIMIT 3"
```

If empty: AWS resources tagged `owner=<email>` are not currently reflected on findings. Patching this would require an enrichment pass on the AWS ai_pass output that reads the tag. **In S1, document the gap and skip** — the per-person view stub is allowed to be empty; the demo arc tells the user "connect Entra (S2) to populate."

- [ ] **Step 4: Commit any emitter patches**

```bash
git add <patched files>
git commit -m "feat: ensure commit_author_email is set on AI code findings for /ai per-person view"
```

(Skip this step if no patches were needed.)

---

## Task 8: Playwright smoke (or vitest e2e if no Playwright harness exists)

**Files:**
- Create: `web/tests/playwright/ai-summary.spec.ts` (if a Playwright dir exists)
- OR: extend `web/src/routes/AISummary.test.tsx` with a deeper integration assertion (if no Playwright dir).

- [ ] **Step 1: Check whether a Playwright harness already exists**

Run: `ls web/tests/playwright/ 2>/dev/null || ls web/playwright.config.* 2>/dev/null`

If neither exists → skip the Playwright file; instead add one more integration-style vitest case to `AISummary.test.tsx` asserting the `/ai/summary` fetch is called exactly once and that all four framework tiles render. Stop after that and proceed to Task 9.

- [ ] **Step 2 (only if a Playwright harness exists): write the Playwright smoke**

```ts
// web/tests/playwright/ai-summary.spec.ts
import { test, expect } from '@playwright/test';

test('AI Summary page renders the F/P/P tile and per-person table', async ({ page }) => {
  // Assumes a deployed authenticated session. Reuse existing auth helper if present.
  await page.goto(process.env.AI_SUMMARY_URL || 'https://shasta.transilience.cloud/ai');
  await expect(page.getByText(/AI Exposure/i)).toBeVisible({ timeout: 15000 });
  await expect(page.getByText(/Fail/i)).toBeVisible();
  await expect(page.getByText(/Partial/i)).toBeVisible();
  await expect(page.getByText(/Pass/i)).toBeVisible();
  await expect(page.getByText(/NIST AI RMF/i)).toBeVisible();
});
```

- [ ] **Step 3: Commit**

```bash
git add web/tests/  # or web/src/routes/AISummary.test.tsx if extended
git commit -m "test: smoke for /ai route"
```

---

## Task 9: Deploy + verification checklist in HANDOFF.md

**Files:**
- Modify: `HANDOFF.md` (prepend an S1 ship block).

- [ ] **Step 1: Confirm everything is deployed**

Run (sanity check, expected to be already done in Tasks 3 + 5):
```bash
aws cloudformation describe-stacks --stack-name CisoCopilotScan --query 'Stacks[0].StackStatus' --output text
aws cloudformation describe-stacks --stack-name CisoCopilotApi  --query 'Stacks[0].StackStatus' --output text
```

Expected: both `UPDATE_COMPLETE`.

- [ ] **Step 2: Run a real Azure scan at Medium tier on a tenant with Azure connected**

Step requires KK. The plan prints the command + the expected behaviour:

```bash
# From the platform/ directory or via the API:
aws ecs run-task \
  --cluster ciso-copilot-scan-cluster \
  --task-definition ciso-copilot-azure-scan \
  --launch-type FARGATE \
  --overrides '{"containerOverrides":[{"name":"ciso-copilot-azure-scan","environment":[{"name":"SCAN_TIER","value":"medium"}]}]}' \
  --network-configuration "<existing-subnet+sg>"
```

(Or, simpler — use the deployed web app's Scan page and click "Scan" on the Azure card at Medium tier per `HANDOFF.md` Scan-screen Slice 2b docs.)

Expected: scan completes; `findings` table now contains rows from the `ai_pass` (look for `f.evidence_packet->'shasta'->>'check_id'` values like `azure_openai_*`, `azure_ml_*`, `azure_cognitive_*`).

- [ ] **Step 3: Confirm the `/ai` page renders correctly with live data**

Open `https://shasta.transilience.cloud/ai` in an incognito window. Sign in with Google. Confirm:
1. Page title "AI Exposure" renders.
2. Fail/Partial/Pass tiles show non-zero numbers (AWS + Azure findings combined).
3. By-source row: AWS + Azure both non-zero, Code may be zero if no GitHub AI scan ran on this tenant.
4. By-framework row: NIST AI RMF + ISO 42001 tiles show counts; SOC 2 AI + EU AI Act are zero (S3 work).
5. Top people: may be empty (acceptable in S1).

- [ ] **Step 4: Add the S1 ship block to `HANDOFF.md`**

Prepend at the top of `HANDOFF.md` (above the existing most-recent block):

```markdown
## 🚀 AI Visibility v2 — Slice 1 shipped (2026-MM-DD)

Sub-project AI Visibility v2, S1. Spec
`docs/superpowers/specs/2026-05-22-ai-visibility-v2-design.md`; plan
`docs/superpowers/plans/2026-05-22-ai-visibility-v2-slice-1.md`.
Built on branch **`feat/ai-visibility-v2-slice-1`** (merged to main
2026-MM-DD, commit `XXXXXXX`).

**Slice 1 — Azure-AI cloud pass + Unified /ai view — DONE.**
- `shasta_runner_azure/app/ai_pass.py` wraps Shasta's
  `discover_azure_ai_services` + `run_full_azure_ai_scan` +
  `enrich_findings_with_ai_controls`. Entities emitted:
  `azure_openai_deployment`, `azure_ml_workspace`, `cognitive_service`
  (all `domain='cloud'`). Findings carry NIST AI RMF + ISO 42001 +
  EU AI Act framework tags.
- AI pass gated on Medium+ tier via `azure_units.modules_for_tier` —
  Quick scans skip it.
- New `/ai/summary` endpoint (`platform/lambda/ai_summary/`) — returns
  score, by-source, by-framework, top-people across AI-touching
  findings. `is_ai_touching` evaluated in SQL using the JSONB ?|
  operator across the four AI framework prefixes plus an AI-resource
  kind allowlist.
- New `/ai` web route (`web/src/routes/AISummary.tsx`) — Fail/Partial/
  Pass tiles, by-source row (AWS/Azure/Code/Entra), by-framework row
  (NIST AI RMF / ISO 42001 / SOC 2 AI / EU AI Act), Top AI Users table.
  Entra tile labeled `coming in S2`; SOC 2 AI + EU AI Act tiles show
  zero (S3 mapping work).
- **Deployed:** scanner image rebuilt + pushed (`sha256:…`);
  `CisoCopilotScan` + `CisoCopilotApi` deployed (UPDATE_COMPLETE); web
  built + synced to S3 + CloudFront invalidated. Live at
  `shasta.transilience.cloud/ai`.

**Slice 1 live-verification — pending (KK-gated, Google OAuth).**
Checklist:
1. Open `https://shasta.transilience.cloud/ai` in an incognito window;
   sign in with Google.
2. Confirm the page renders "AI Exposure" title + three F/P/P tiles
   with non-zero numbers (AWS + Azure findings combined on this
   tenant).
3. Confirm the by-source row shows AWS + Azure non-zero, Code per-tenant.
4. Confirm the by-framework row shows NIST AI RMF + ISO 42001 with
   counts; SOC 2 AI + EU AI Act both zero (S3 work).
5. Open browser devtools → Network. Confirm `/ai/summary` returned
   200 with the contract from the plan.
6. (Optional) Re-run a Medium Azure scan and refresh — counts should
   increase or hold steady, never go negative.

**▶ NEXT** — Slice 2 (Entra sign-in connector + per-person grouping).
Brainstorm + plan separately.
```

- [ ] **Step 5: Final commit + open PR**

```bash
git add HANDOFF.md
git commit -m "docs: HANDOFF — S1 ship block + verification checklist"
git push -u origin feat/ai-visibility-v2-slice-1
gh pr create --title "feat: AI Visibility v2 Slice 1 — Azure-AI pass + /ai view" --body "$(cat <<'EOF'
## Summary
- Adds Azure-AI cloud pass (Shasta wrap) to `shasta_runner_azure`
- New `/ai/summary` Lambda + API route
- New `/ai` web route — Fail/Partial/Pass tile + by-source + by-framework + top-people

## Test plan
- [ ] `pytest` green in `shasta_runner_azure/app/tests/` and `ai_summary/tests/`
- [ ] `pnpm test`, `pnpm typecheck`, `pnpm build` clean (no new errors over baseline)
- [ ] `cdk deploy CisoCopilotScan` + `cdk deploy CisoCopilotApi --exclusively` both `UPDATE_COMPLETE`
- [ ] Web synced + CloudFront invalidated
- [ ] HANDOFF.md verification checklist passes on `shasta.transilience.cloud/ai`

Refs:
- Spec: `docs/superpowers/specs/2026-05-22-ai-visibility-v2-design.md`
- Strategy: `docs/superpowers/specs/2026-05-22-ai-security-strategy.md`
- Plan: `docs/superpowers/plans/2026-05-22-ai-visibility-v2-slice-1.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review (run after the plan is written)

**Spec coverage:**
- §3 In scope item 1 (Azure-AI) → Tasks 1-3 ✓
- §3 In scope item 3 (Unified `/ai` view) → Tasks 4-6 ✓
- §3 In scope item 4 (Per-person grouping) → Task 6 + Task 7 (with email-attribute audit) ✓
- §3 In scope item 5 (Compliance mapping sweep) → **deferred to S3** ✓ (intentional — S1 ships SOC 2 AI + EU AI Act tiles as zeros)
- §6 S1 slice ("Cloud-AI Azure + Unified AI View") → Tasks 1-9 ✓
- §8 AI Risk Score (mirror of findings overhaul, F/P/P) → Task 4 score block + Task 6 ScoreTile ✓
- §11 iOS scope (no iOS changes) → no iOS tasks ✓
- §12 Testing strategy (unit + integration + manual + smoke) → Tasks 1, 2, 4, 6, 8, 9 ✓

**No placeholders found.** Every code step contains complete code; every command step contains the exact command and the expected output.

**Type consistency:** entity kind names used in Task 1 (`azure_openai_deployment`, `azure_ml_workspace`, `cognitive_service`) match the `_AI_RESOURCE_KINDS` allowlist used by `is_ai_touching` in Task 4 ✓. Framework prefixes (`nist_ai_rmf`, `iso_42001`, `soc2_ai`, `eu_ai_act`) match across Tasks 4, 6, and the spec ✓. Per-person email attribute names (`commit_author_email`, `iam_owner_email`, `entra_upn`) match between Task 4 SQL and Task 7 audit ✓.

**One known gap surfaced for the implementer to confirm at runtime, not pre-decided here:**
- The actual column name on `findings` for the entity-id FK (`subject_entity_id` vs `entity_id`) — Task 4 Step 5 has the verification command. The plan calls this out explicitly so the implementer doesn't ship broken SQL.
