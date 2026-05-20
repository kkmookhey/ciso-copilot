# AI Discovery — Cloud-AI Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fold Shasta's AWS-AI discovery and 15 AWS-AI security checks into the existing AWS cloud scan, emitting AI-service entities and AI findings — with NIST AI RMF / ISO 42001 control mappings — into the unified `entities`/`edges`/`findings` model.

**Architecture:** A new `ai_pass.py` module in the existing `shasta_runner` container Lambda calls Shasta's `discover_aws_ai_services`, `run_full_aws_ai_scan`, and `enrich_findings_with_ai_controls`, maps the results to the shared `EntityEmission`/`EdgeEmission`/`FindingEmission` types, and the handler folds them into the one `commit_scan` transaction. A shared-module fix makes `unified_writer` actually persist the `findings.frameworks` column so `compliance_summary` can roll up AI frameworks.

**Tech Stack:** Python 3.12, AWS Lambda (container image), Shasta (`pip install --no-deps` sub-package), Aurora Postgres via the RDS Data API, pytest.

---

## Scope

This is **plan 1 of 2** for the `2026-05-20-ai-discovery-connectors-design.md` spec. It covers the **cloud-AI connector** (spec §6) and the shared `findings.frameworks` fix (spec §8). The **provider connectors** (OpenAI/Anthropic, spec §7) are an independent subsystem with their own plan — they need a new Lambda, a new connection flow, and an OpenAI/Anthropic admin-API research pass.

## Deviations from the spec (discovered during planning)

1. **Spec §8 assumed `compliance_summary` picks up AI frameworks "for free."** It does not yet — `unified_writer._insert_finding` hardcodes the `findings.frameworks` column to `'{}'::jsonb`. **Phase 0** fixes this; it is a prerequisite, not optional.
2. **Bedrock model entities are deferred.** Shasta's `discover_aws_ai_services` has a key-name mismatch (`_discover_bedrock` returns `foundation_models`, the merge expects `models`) so the merged dict carries no Bedrock model list. We do not edit Shasta (CLAUDE.md rule). Bedrock *findings* from the 15 checks are unaffected and still flow. Bedrock-model inventory is a follow-up (a Shasta fix).
3. **Finding `status` is recorded as `'fail'` for every finding.** `unified_writer._insert_finding` hardcodes `status='fail'` — a pre-existing condition affecting all scanners, not just AI. AI-framework controls will therefore show in the compliance view as *failing where a finding exists*. Surfacing pass/partial status is a separate `unified_writer` enhancement, out of scope here.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `platform/lambda/ai_scanner/detectors/base.py` | Shared emission dataclasses | Modify — add `frameworks` to `FindingEmission` |
| `platform/lambda/ai_scanner/unified_writer.py` | Transactional writer | Modify — persist `frameworks` in `_insert_finding` |
| `platform/lambda/ai_scanner/tests/test_unified_writer.py` | Writer unit tests | Modify — add a test |
| `platform/lambda/shasta_runner/app/ai_pass.py` | Cloud-AI pass: Shasta AI → emissions | Create |
| `platform/lambda/shasta_runner/app/main.py` | AWS scan handler | Modify — call `run_ai_pass`, fold results into `commit_scan` |
| `platform/lambda/shasta_runner/app/tests/test_ai_pass.py` | Cloud-AI pass unit tests | Create |

Note: `shasta_runner/build.sh` copies `unified_writer.py` and `detectors/base.py` from `ai_scanner/` at build time, so the Phase 0 edits flow into the `shasta_runner` image automatically — no manual copy.

---

## Phase 0 — Persist `findings.frameworks`

### Task 1: Add `frameworks` to `FindingEmission` and write it in `_insert_finding`

**Files:**
- Modify: `platform/lambda/ai_scanner/detectors/base.py`
- Modify: `platform/lambda/ai_scanner/unified_writer.py` (function `_insert_finding`, ~lines 232-264)
- Test: `platform/lambda/ai_scanner/tests/test_unified_writer.py`

- [ ] **Step 1: Write the failing test**

Append to `platform/lambda/ai_scanner/tests/test_unified_writer.py` (the file already defines `_stub_rds(monkeypatch)` returning `(fake, calls)`, and `_ctx()`; it already imports `json` and `pytest`):

```python
def test_insert_finding_persists_frameworks(monkeypatch):
    import unified_writer
    from detectors.base import FindingEmission
    _fake, calls = _stub_rds(monkeypatch)

    f = FindingEmission(
        tenant_id="t1",
        finding_type="bedrock-guardrails-configured",
        severity="medium",
        title="Bedrock guardrails not configured",
        description="No guardrails found on the account.",
        subject_entity_kind=None,
        subject_entity_natural_key=None,
        subject_type=None,
        subject_ref=None,
        evidence_packet={"version": "0.1"},
        confidence="high",
        frameworks={"nist_ai_rmf": ["MANAGE-2"], "iso_42001": ["AI-8.3"]},
    )
    unified_writer.commit_scan(_ctx(), entities=[], edges=[], findings=[f])

    finding_calls = [c for c in calls if "INSERT INTO findings" in c["sql"]]
    assert len(finding_calls) == 1
    params = {p["name"]: p["value"] for p in finding_calls[0]["parameters"]}
    assert json.loads(params["fw"]["stringValue"]) == {
        "nist_ai_rmf": ["MANAGE-2"],
        "iso_42001":   ["AI-8.3"],
    }
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd platform/lambda/ai_scanner && python -m pytest tests/test_unified_writer.py::test_insert_finding_persists_frameworks -v`
Expected: FAIL — `TypeError: FindingEmission.__init__() got an unexpected keyword argument 'frameworks'`.

- [ ] **Step 3: Add the `frameworks` field to `FindingEmission`**

In `platform/lambda/ai_scanner/detectors/base.py`, the `FindingEmission` dataclass ends with `confidence: str`. Add a defaulted field after it (`field` is already imported — `DetectorResult` uses `field(default_factory=list)`):

```python
@dataclass(frozen=True)
class FindingEmission:
    tenant_id:                  str
    finding_type:               str
    severity:                   str
    title:                      str
    description:                str
    subject_entity_kind:        str | None
    subject_entity_natural_key: str | None
    subject_type:               str | None
    subject_ref:                str | None
    evidence_packet:            dict[str, Any]
    confidence:                 str
    frameworks:                 dict[str, list[str]] = field(default_factory=dict)
```

- [ ] **Step 4: Persist `frameworks` in `_insert_finding`**

In `platform/lambda/ai_scanner/unified_writer.py`, `_insert_finding` currently has this line in the `VALUES` clause:

```python
            "        :stype, NULL, 'ai', '{}'::jsonb, NULL, NOW(), NOW(), CAST(:ev AS JSONB), "
```

Change `'{}'::jsonb` to a bound parameter:

```python
            "        :stype, NULL, 'ai', CAST(:fw AS JSONB), NULL, NOW(), NOW(), CAST(:ev AS JSONB), "
```

Then add the `fw` parameter to the `parameters` list (next to the `ev` parameter):

```python
            {"name": "ev",    "value": {"stringValue": json.dumps(f.evidence_packet)}},
            {"name": "fw",    "value": {"stringValue": json.dumps(f.frameworks)}},
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd platform/lambda/ai_scanner && python -m pytest tests/test_unified_writer.py -v`
Expected: PASS — the new test and all existing `test_unified_writer.py` tests pass.

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/ai_scanner/detectors/base.py \
        platform/lambda/ai_scanner/unified_writer.py \
        platform/lambda/ai_scanner/tests/test_unified_writer.py
git commit -m "feat(platform): persist findings.frameworks from FindingEmission

unified_writer hardcoded the findings.frameworks column to '{}', so
compliance_summary (which rolls up that column) never saw any framework
mappings. Add a frameworks field to FindingEmission and write it."
```

---

## Phase 1 — Cloud-AI pass

### Task 2: `ai_pass.discovery_to_entities` — map Shasta AI discovery to entities

**Files:**
- Create: `platform/lambda/shasta_runner/app/ai_pass.py`
- Test: `platform/lambda/shasta_runner/app/tests/test_ai_pass.py`

`discover_aws_ai_services` returns a dict with `sagemaker` (`endpoints`/`models`/`training_jobs`), `comprehend` (`endpoints`), `bedrock`, `lambda_ai`. Only SageMaker and Comprehend carry usable item lists (see Deviation 2). Each item becomes a `domain='cloud'` entity plus an `aws_account → contains → <entity>` edge.

- [ ] **Step 1: Write the failing test**

Create `platform/lambda/shasta_runner/app/tests/test_ai_pass.py`:

```python
"""Unit tests for the cloud-AI pass (ai_pass.py)."""


def test_discovery_to_entities_maps_sagemaker_and_comprehend():
    from ai_pass import discovery_to_entities

    discovery = {
        "sagemaker": {
            "available": True,
            "endpoints":     [{"name": "fraud-ep", "status": "InService",
                               "creation_time": "2026-01-02"}],
            "models":        [{"name": "fraud-model", "creation_time": "2026-01-01"}],
            "training_jobs": [{"name": "fraud-train", "status": "Completed",
                               "creation_time": "2025-12-30"}],
            "total_resources": 3,
        },
        "comprehend": {
            "available": True,
            "endpoints": [{"arn": "arn:aws:comprehend:us-east-1:111122223333:"
                                  "document-classifier-endpoint/pii",
                           "status": "IN_SERVICE", "model_arn": "arn:aws:comprehend:..."}],
            "total_resources": 1,
        },
        "bedrock":   {"available": True, "models": [], "total_resources": 4},
        "lambda_ai": {"available": False, "functions": [], "total_resources": 0},
    }

    entities, edges = discovery_to_entities(
        discovery, account_id="111122223333", tenant_id="tnt-1",
    )

    by_kind = {e.kind: e for e in entities}
    assert set(by_kind) == {
        "sagemaker_endpoint", "sagemaker_model",
        "sagemaker_training_job", "comprehend_endpoint",
    }
    assert by_kind["sagemaker_endpoint"].natural_key == "sagemaker:endpoint/fraud-ep"
    assert by_kind["sagemaker_endpoint"].domain == "cloud"
    assert by_kind["sagemaker_endpoint"].display_name == "fraud-ep"
    assert by_kind["sagemaker_endpoint"].attributes["status"] == "InService"
    assert by_kind["comprehend_endpoint"].natural_key == (
        "arn:aws:comprehend:us-east-1:111122223333:"
        "document-classifier-endpoint/pii"
    )
    # one contains-edge per entity, all rooted at the account
    assert len(edges) == 4
    assert all(e.kind == "contains" for e in edges)
    assert all(e.source_kind == "aws_account"
               and e.source_natural_key == "111122223333" for e in edges)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd platform/lambda/shasta_runner && python -m pytest app/tests/test_ai_pass.py::test_discovery_to_entities_maps_sagemaker_and_comprehend -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_pass'`.

- [ ] **Step 3: Create `ai_pass.py` with `discovery_to_entities`**

Create `platform/lambda/shasta_runner/app/ai_pass.py`:

```python
"""Cloud-AI pass — wraps Shasta's AWS-AI discovery + checks into the
unified entity/edge/finding model.

Pure helpers (discovery_to_entities, ai_findings_to_emissions) take
already-fetched data and are unit-tested directly. run_ai_pass is the
orchestrator; it imports Shasta lazily so this module imports cleanly
in a test environment without Shasta installed.
"""
from __future__ import annotations

from typing import Any

from detectors.base import EdgeEmission, EntityEmission, FindingEmission

_DETECTOR_ID      = "shasta_runner.ai_pass"
_DETECTOR_VERSION = "0.1.0"


def discovery_to_entities(
    discovery: dict[str, Any], *, account_id: str, tenant_id: str,
) -> tuple[list[EntityEmission], list[EdgeEmission]]:
    """Map a Shasta discover_aws_ai_services() result to entities + edges.

    Each AI service becomes a domain='cloud' entity plus an
    aws_account --contains--> entity edge. Bedrock + lambda_ai lists are
    empty from Shasta today (key-name mismatch) and produce nothing.
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
            source_kind="aws_account", source_natural_key=account_id,
            target_kind=kind, target_natural_key=natural_key,
            kind="contains", attributes={},
            evidence_packet={"version": "0.1", "via": "ai_discovery"},
            detector_id=_DETECTOR_ID, detector_version=_DETECTOR_VERSION,
        ))

    sm = discovery.get("sagemaker", {})
    for ep in sm.get("endpoints", []):
        name = ep.get("name", "")
        if name:
            _emit("sagemaker_endpoint", f"sagemaker:endpoint/{name}", name,
                  {"status": ep.get("status", ""),
                   "creation_time": ep.get("creation_time", "")})
    for m in sm.get("models", []):
        name = m.get("name", "")
        if name:
            _emit("sagemaker_model", f"sagemaker:model/{name}", name,
                  {"creation_time": m.get("creation_time", "")})
    for tj in sm.get("training_jobs", []):
        name = tj.get("name", "")
        if name:
            _emit("sagemaker_training_job", f"sagemaker:training-job/{name}", name,
                  {"status": tj.get("status", ""),
                   "creation_time": tj.get("creation_time", "")})

    for ce in discovery.get("comprehend", {}).get("endpoints", []):
        arn = ce.get("arn", "")
        if arn:
            _emit("comprehend_endpoint", arn, arn.rsplit("/", 1)[-1] or arn,
                  {"status": ce.get("status", ""),
                   "model_arn": ce.get("model_arn", "")})

    return entities, edges
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd platform/lambda/shasta_runner && python -m pytest app/tests/test_ai_pass.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/shasta_runner/app/ai_pass.py \
        platform/lambda/shasta_runner/app/tests/test_ai_pass.py
git commit -m "feat(platform): map Shasta AI discovery to cloud entities"
```

### Task 3: `ai_pass.ai_findings_to_emissions` — map AI-check findings to FindingEmission

**Files:**
- Modify: `platform/lambda/shasta_runner/app/ai_pass.py`
- Test: `platform/lambda/shasta_runner/app/tests/test_ai_pass.py`

Shasta's `Finding` has no AI-framework attributes; `enrich_findings_with_ai_controls` populates `finding.details` with the keys `nist_ai_rmf`, `iso42001_controls`, `eu_ai_act`, `owasp_llm_top10`, `owasp_agentic`, `nist_ai_600_1`, `mitre_atlas` (each `list[str]`). This function reads those into `FindingEmission.frameworks`.

- [ ] **Step 1: Write the failing test**

Append to `platform/lambda/shasta_runner/app/tests/test_ai_pass.py`:

```python
def test_ai_findings_to_emissions_pulls_frameworks_from_details():
    import types
    from ai_pass import ai_findings_to_emissions

    finding = types.SimpleNamespace(
        check_id="bedrock-guardrails-configured",
        title="Bedrock guardrails not configured",
        description="No guardrails on the account.",
        severity="MEDIUM",
        status="fail",
        domain="ai_governance",
        region="us-east-1",
        resource_type="bedrock-guardrails",
        resource_id="arn:aws:bedrock:us-east-1:111122223333:guardrails",
        remediation="Configure a guardrail.",
        soc2_controls=[],
        cis_aws_controls=[],
        iso27001_controls=[],
        hipaa_controls=[],
        mcsb_controls=[],
        details={
            "nist_ai_rmf":       ["MANAGE-2"],
            "iso42001_controls": ["AI-8.3"],
            "owasp_llm_top10":   ["LLM01"],
        },
    )

    emissions = ai_findings_to_emissions([finding], tenant_id="tnt-1")

    assert len(emissions) == 1
    e = emissions[0]
    assert e.finding_type == "bedrock-guardrails-configured"
    assert e.severity == "medium"
    assert e.tenant_id == "tnt-1"
    assert e.frameworks == {
        "nist_ai_rmf":     ["MANAGE-2"],
        "iso_42001":       ["AI-8.3"],
        "owasp_llm_top10": ["LLM01"],
    }
    assert e.evidence_packet["shasta"]["check_id"] == "bedrock-guardrails-configured"


def test_ai_findings_to_emissions_handles_missing_details():
    import types
    from ai_pass import ai_findings_to_emissions

    finding = types.SimpleNamespace(
        check_id="sagemaker-endpoint-encryption",
        title="SageMaker endpoint not encrypted",
        description="",
        severity="high",
        status="fail",
        domain="ai_governance",
        region="us-east-1",
        resource_type="sagemaker-endpoint",
        resource_id="fraud-ep",
        remediation="",
        soc2_controls=[],
        cis_aws_controls=[],
        iso27001_controls=[],
        hipaa_controls=[],
        mcsb_controls=[],
        details={},
    )

    emissions = ai_findings_to_emissions([finding], tenant_id="tnt-1")
    assert emissions[0].frameworks == {}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd platform/lambda/shasta_runner && python -m pytest app/tests/test_ai_pass.py -k ai_findings -v`
Expected: FAIL — `AttributeError: module 'ai_pass' has no attribute 'ai_findings_to_emissions'`.

- [ ] **Step 3: Add `ai_findings_to_emissions` to `ai_pass.py`**

Add to `platform/lambda/shasta_runner/app/ai_pass.py` (constants near the top, function below `discovery_to_entities`):

```python
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


def ai_findings_to_emissions(
    findings: list[Any], *, tenant_id: str,
) -> list[FindingEmission]:
    """Map Shasta AI-check Findings (already enriched via
    enrich_findings_with_ai_controls) to FindingEmission rows, pulling
    AI-framework control IDs from finding.details into .frameworks."""
    out: list[FindingEmission] = []
    for f in findings:
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

        evidence = {
            "version": "0.1",
            "shasta": {
                "check_id":      f.check_id,
                "status":        _estr(f.status).lower(),
                "domain":        _estr(getattr(f, "domain", "")).lower(),
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
        ))
    return out
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd platform/lambda/shasta_runner && python -m pytest app/tests/test_ai_pass.py -v`
Expected: PASS — all four `test_ai_pass.py` tests.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/shasta_runner/app/ai_pass.py \
        platform/lambda/shasta_runner/app/tests/test_ai_pass.py
git commit -m "feat(platform): map Shasta AI findings to FindingEmission with framework controls"
```

### Task 4: `run_ai_pass` orchestrator + wire into the AWS scan handler

**Files:**
- Modify: `platform/lambda/shasta_runner/app/ai_pass.py` (add `run_ai_pass`)
- Modify: `platform/lambda/shasta_runner/app/main.py` (import + call inside `handler`)

This task is glue between Shasta and the pure helpers; it is verified by the Task 5 end-to-end run, not a unit test (it depends on a live Shasta + AWS client).

- [ ] **Step 1: Add `run_ai_pass` to `ai_pass.py`**

Append to `platform/lambda/shasta_runner/app/ai_pass.py`:

```python
def run_ai_pass(client: Any, *, account_id: str, tenant_id: str) -> dict[str, list]:
    """Run Shasta's AWS-AI discovery + checks against an assumed-role client
    and return unified emissions. Shasta is imported lazily so this module
    stays importable in test environments without Shasta installed."""
    from shasta.aws.ai_discovery import discover_aws_ai_services
    from shasta.aws.ai_checks import run_full_aws_ai_scan
    from shasta.compliance.ai.mapper import enrich_findings_with_ai_controls

    discovery = discover_aws_ai_services(client)
    entities, edges = discovery_to_entities(
        discovery, account_id=account_id, tenant_id=tenant_id,
    )

    findings = run_full_aws_ai_scan(client)
    enrich_findings_with_ai_controls(findings)
    finding_emissions = ai_findings_to_emissions(findings, tenant_id=tenant_id)

    return {"entities": entities, "edges": edges, "findings": finding_emissions}
```

- [ ] **Step 2: Import `run_ai_pass` in `main.py`**

In `platform/lambda/shasta_runner/app/main.py`, the entity-emission imports block is:

```python
# === Entity-emission helpers (this module) ===
from arn_to_entity     import parse_arn
from enumerate_compute import enumerate_compute
from enumerate_iam     import enumerate_iam
from enumerate_network import enumerate_network
from enumerate_storage import enumerate_storage
```

Add `ai_pass` to it:

```python
# === Entity-emission helpers (this module) ===
from ai_pass           import run_ai_pass
from arn_to_entity     import parse_arn
from enumerate_compute import enumerate_compute
from enumerate_iam     import enumerate_iam
from enumerate_network import enumerate_network
from enumerate_storage import enumerate_storage
```

- [ ] **Step 3: Call `run_ai_pass` in the handler and fold results into `commit_scan`**

In `main.py`, the handler currently ends the scan body with:

```python
        # --- Convert Shasta findings to FindingEmission, derive ARN→entity FKs
        finding_emissions = _convert_findings(
            all_shasta_findings, tenant_id, account_id, entities, edges,
        )

        # --- Single transactional write
        commit_scan(ctx, entities=entities, edges=edges, findings=finding_emissions)
```

Replace that block with:

```python
        # --- Cloud-AI pass: Shasta AI discovery + 15 AI checks + framework mapping.
        # Wrapped like every other module so one failure doesn't kill the scan.
        ai_finding_emissions: list[FindingEmission] = []
        try:
            ai_client = AssumedRoleAWSClient(credentials, "us-east-1", account_id)
            ai_result = run_ai_pass(ai_client, account_id=account_id, tenant_id=tenant_id)
            entities.extend(ai_result["entities"])
            edges.extend(ai_result["edges"])
            ai_finding_emissions = ai_result["findings"]
            module_stats["ai_pass"] = {
                "entities": len(ai_result["entities"]),
                "findings": len(ai_result["findings"]),
            }
            print(f"ai_pass: {len(ai_result['entities'])} entities, "
                  f"{len(ai_result['findings'])} findings")
        except Exception as e:
            print(f"ai_pass FAILED: {e}\n{traceback.format_exc()}")
            module_stats["ai_pass"] = {"error": str(e)[:200]}

        # --- Convert Shasta findings to FindingEmission, derive ARN→entity FKs
        finding_emissions = _convert_findings(
            all_shasta_findings, tenant_id, account_id, entities, edges,
        )

        # --- Single transactional write
        commit_scan(ctx, entities=entities, edges=edges,
                    findings=finding_emissions + ai_finding_emissions)
```

- [ ] **Step 4: Update the completion-stats finding count**

Still in `main.py`, the `_update_scan(scan_id, status="completed", stats={...})` call and the return dict use `len(finding_emissions)`. Both must count the AI findings too. Change every `len(finding_emissions)` in the completion block to `len(finding_emissions) + len(ai_finding_emissions)`. The block becomes:

```python
        total_findings = len(finding_emissions) + len(ai_finding_emissions)
        _update_scan(scan_id, status="completed", stats={
            "entities":      len(entities),
            "edges":         len(edges),
            "findings":      total_findings,
            "modules":       module_stats,
            "regions":       regions,
            "global_runs":   len(GLOBAL_MODULES),
            "regional_runs": len(REGIONAL_MODULES) * len(regions),
        })
        print(f"scan complete: {len(entities)} entities, {len(edges)} edges, "
              f"{total_findings} findings")
        return {
            "scan_id":          scan_id,
            "entities_written": len(entities),
            "edges_written":    len(edges),
            "findings_written": total_findings,
        }
```

- [ ] **Step 5: Run the existing test suite to confirm nothing broke**

Run: `cd platform/lambda/shasta_runner && python -m pytest app/tests/ -v`
Expected: PASS — `test_ai_pass.py` plus the existing `test_arn_to_entity.py` / `test_enumerate_*.py` tests. (`main.py` has no unit test; it imports Shasta and is covered by the Task 5 E2E.)

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/shasta_runner/app/ai_pass.py \
        platform/lambda/shasta_runner/app/main.py
git commit -m "feat(platform): fold the cloud-AI pass into the AWS scan handler"
```

---

## Phase 2 — Deploy & verify

### Task 5: Build, deploy, and end-to-end verify on KK's tenant

**Files:** none (deploy + manual verification).

- [ ] **Step 1: Run the full per-Lambda test suites**

```bash
cd platform/lambda/ai_scanner   && python -m pytest tests/ -v
cd platform/lambda/shasta_runner && python -m pytest app/tests/ -v
```
Expected: PASS for both.

- [ ] **Step 2: Build and push the `shasta_runner` image**

`build.sh` stages Shasta from `~/Projects/Shasta`, copies the updated `unified_writer.py` + `detectors/base.py` from `ai_scanner/`, builds `linux/amd64`, and pushes to the `shasta-runner` ECR repo.

```bash
cd platform/lambda/shasta_runner && ./build.sh
```
Expected: ends with a `docker push` of `<account>.dkr.ecr.us-east-1.amazonaws.com/shasta-runner:latest`.

- [ ] **Step 3: Point the Lambda at the new image**

Container Lambdas do not hotswap — update the function code explicitly.

```bash
SHASTA_FN=$(aws lambda list-functions \
  --query "Functions[?contains(FunctionName,'ShastaRunner') && !contains(FunctionName,'Azure') && !contains(FunctionName,'Gcp') && !contains(FunctionName,'Entra')].FunctionName | [0]" \
  --output text)
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
aws lambda update-function-code \
  --function-name "$SHASTA_FN" \
  --image-uri "${ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com/shasta-runner:latest"
aws lambda wait function-updated --function-name "$SHASTA_FN"
```
Expected: the function update completes; `wait` returns 0.

- [ ] **Step 4: Trigger an AWS scan on KK's tenant**

Trigger a scan the normal way (the web app's "Rescan" on the AWS connection, or the Step Functions execution). Then tail the logs:

```bash
aws logs tail "/aws/lambda/$SHASTA_FN" --since 10m --follow
```
Expected: a `ai_pass: N entities, M findings` log line, and `scan complete: ...`.

- [ ] **Step 5: Verify AI entities landed**

Query Aurora via the Data API (cluster + secret ARNs are in `HANDOFF.md`):

```sql
SELECT kind, count(*) FROM entities
WHERE tenant_id = '<KK_TENANT_ID>'
  AND kind IN ('sagemaker_endpoint','sagemaker_model',
               'sagemaker_training_job','comprehend_endpoint')
GROUP BY kind;
```
Expected: rows for whichever AI services exist in KK's account (may be zero if the account runs no SageMaker/Comprehend — in that case verify with an account that does, or confirm via the findings in Step 6).

- [ ] **Step 6: Verify AI findings carry framework mappings**

```sql
SELECT check_id, frameworks
FROM findings
WHERE tenant_id = '<KK_TENANT_ID>'
  AND frameworks ? 'nist_ai_rmf'
LIMIT 10;
```
Expected: AI-check findings (e.g. `bedrock-guardrails-configured`, `sagemaker-endpoint-encryption`) with a non-empty `frameworks` JSONB containing `nist_ai_rmf` / `iso_42001` keys.

- [ ] **Step 7: Verify the compliance view shows the AI frameworks**

Open the web app's compliance surface (or call `GET /v1/compliance/summary` with a valid token). Confirm `nist_ai_rmf` and `iso_42001` now appear as frameworks alongside `soc2` / `cis_aws`.

- [ ] **Step 8: Commit a HANDOFF note**

Append a short note to `HANDOFF.md` recording that the cloud-AI pass is deployed (entities, AI findings, NIST AI RMF / ISO 42001 in compliance), and the two known limitations from the Deviations section (Bedrock-model inventory deferred; finding status hardcoded `fail`).

```bash
git add HANDOFF.md
git commit -m "docs: HANDOFF — cloud-AI pass deployed in the AWS scan"
```

---

## Self-Review

**Spec coverage** (against `2026-05-20-ai-discovery-connectors-design.md`):
- §6.1 cloud-AI runs in `shasta_runner` — Task 4. ✓
- §6.2 calls `discover_aws_ai_services` + `run_full_aws_ai_scan` + `compliance/ai` mapper — Tasks 2/3/4. ✓
- §6.3 entities `domain='cloud'`, `aws_account → contains` edges — Task 2. ✓ (Bedrock-model entities deferred — Deviation 2.)
- §6.4 findings carry `frameworks` from the AI mapper — Tasks 1/3. ✓
- §6.5 no IAM change — confirmed (ReadOnlyAccess covers Bedrock/SageMaker/Comprehend); no task needed. ✓
- §8 AI posture in the existing compliance view — Task 1 makes it real; Task 5 Step 7 verifies. ✓
- §7 provider connectors — **out of scope for this plan** (plan 2). ✓ stated.
- §9 `ai_scans` migration — **not needed for cloud-AI** (cloud scans use the legacy `scans` table; `commit_scan`'s `ai_scans` update is a harmless no-op). The migration belongs to plan 2. ✓

**Placeholder scan:** no TBD/TODO; every code step has complete code; the one discovery command (Task 5 Step 3) is a real `aws` command, not a placeholder.

**Type consistency:** `FindingEmission` gains `frameworks: dict[str, list[str]]` (Task 1) and every `FindingEmission(...)` constructed in Task 3 passes `frameworks=`. `discovery_to_entities` returns `(list, list)`; `ai_findings_to_emissions` returns `list`; `run_ai_pass` returns `{"entities","edges","findings"}` and the handler reads exactly those keys. `_estr`, `_DETECTOR_ID`, `_DETECTOR_VERSION`, `_STD_FRAMEWORK_ATTRS`, `_AI_FRAMEWORK_DETAIL_KEYS` are all defined in `ai_pass.py` before use.
