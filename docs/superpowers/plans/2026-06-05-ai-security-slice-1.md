# AI Security Slice 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Broaden Shasta's AI-security surface beyond Entra by shipping a Google Workspace shadow-AI scanner + AWS Bedrock runtime detection + a CycloneDX-ML AI-BOM export, with ~8 new mapping rules wired into the already-shipped 8 AI-family framework packs and a one-time `framework_meta` deduplication.

**Architecture:** Five sub-slices, each a separate PR. **1.1** consolidates `framework_meta` (lowest risk, unblocks everything). **1.2** adds the AI-BOM export (no new ingest, ships value alone). **1.3** extends `event_router` for Bedrock runtime events. **1.4** ships the heavy lift — Google Workspace OAuth + Fargate scanner + 4 detectors + UI. **1.5** wires the 8 new mapping rules + end-to-end smoke. The `_shared/mcp_oauth/` patterns (KMS-envelope crypto + JIT refresh + advisory lock + PKCE + state JWT) are reused wholesale for the Workspace OAuth flow. No `_shared/cme/` hoist — `scanner_core/` is already canonical.

**Tech Stack:** Python 3.12 Lambda, Aurora Data API (boto3), AWS KMS, AWS Bedrock CloudTrail/EventBridge, Google Workspace Admin SDK Reports API (`google-api-python-client`), `cyclonedx-python-lib` for CycloneDX-ML serialization, React + TypeScript + Vite, AWS CDK (TypeScript).

**Spec:** `docs/superpowers/specs/2026-06-04-ai-security-slice-1-design.md`. Re-read it before starting; the plan does not duplicate the spec's rationale.

**Calendar dependency:** Google verification of restricted Workspace OAuth scopes (`admin.reports.audit.readonly` + `admin.directory.user.readonly`) takes 2-4 weeks. Sub-slice 1.4 Task 1 kicks off verification day 1; dev work proceeds against unverified client; production flips to verified client when approved. Worst case the slice 1.4 PR merges with the Workspace tile showing a "Pending verification" state — UX-acceptable; no blocking.

---

## File Structure

**New files:**
- `platform/sql/016_workspace_connector.sql` — `tenant_workspace_oauth` table
- `platform/lambda/scanner_core/framework_meta.py` — consolidated FRAMEWORK_META
- `platform/lambda/_shared/mcp_oauth/providers/google_workspace.py` — OAuth URLs + scope config
- `platform/lambda/connectors/handlers_google_workspace.py` — OAuth initiate + callback handlers
- `platform/lambda/connectors/tests/test_handlers_google_workspace.py`
- `platform/lambda/ai_bom_export/main.py` — CycloneDX-ML AI-BOM endpoint handler
- `platform/lambda/ai_bom_export/requirements.txt` — pinned `cyclonedx-python-lib`
- `platform/lambda/ai_bom_export/tests/test_main.py`
- `platform/lambda/shasta_runner_workspace/Dockerfile` — Fargate image scaffold
- `platform/lambda/shasta_runner_workspace/build.sh` — image build + push + staging script
- `platform/lambda/shasta_runner_workspace/app/main.py` — scanner entry point
- `platform/lambda/shasta_runner_workspace/app/google_client.py` — Workspace SDK auth + paginated audit-log iterator
- `platform/lambda/shasta_runner_workspace/app/ai_saas_catalog.py` — re-exported from Entra path
- `platform/lambda/shasta_runner_workspace/app/detectors/gws_ai_signin_personal_tier.py`
- `platform/lambda/shasta_runner_workspace/app/detectors/gws_ai_oauth_grant.py`
- `platform/lambda/shasta_runner_workspace/app/detectors/gws_drive_shared_to_ai_domain.py`
- `platform/lambda/shasta_runner_workspace/app/detectors/gws_gemini_assigned.py`
- `platform/lambda/shasta_runner_workspace/app/tests/conftest.py`
- `platform/lambda/shasta_runner_workspace/app/tests/test_each_detector.py`
- `web/src/components/ExportAIBOMButton.tsx` — `/ai` page header button

**Modified files:**
- `platform/lambda/ai_summary/main.py` — `import framework_meta` → `from scanner_core.framework_meta import …`
- `platform/lambda/compliance_summary/main.py` — same import change
- `platform/lambda/ai_summary/framework_meta.py` — DELETED (now re-exported)
- `platform/lambda/compliance_summary/framework_meta.py` — DELETED (now re-exported)
- `platform/lambda/event_router/main.py` — new Bedrock event handler branch
- `platform/lambda/event_router/tests/test_bedrock.py` — NEW (per-event + rollup tests)
- `platform/lambda/connections_list/main.py` — handle `kind='google_workspace'` rescan
- `platform/lambda/scanner_core/ai_framework_registry.json` — +8 new rules in `rules[]`
- `platform/lambda/scanner_core/tests/test_framework_registry.py` — +8 new rule-application tests
- `platform/lambda/scripts/sync_framework_map.py` — add `shasta_runner_workspace` to targets
- `platform/lib/api-stack.ts` — `ai_bom_export` Lambda + route, `connectors` route additions for `/connect/google_workspace` + `/callback/google_workspace`, KMS/SSM grants
- `platform/lib/scan-stack.ts` — `shasta_runner_workspace` Fargate task def, ECR repo, EventBridge rules for Bedrock InvokeModel + daily rollup
- `platform/lib/data-stack.ts` — `tenant_workspace_oauth` is just a table (no CDK change — applied via Data API SQL)
- `platform/bin/ciso-copilot.ts` — wire any new env vars / stack outputs
- `web/src/routes/AISummary.tsx` — mount Shadow AI row + Export AI-BOM button
- `web/src/routes/ConnectClouds.tsx` — Google Workspace tile + OAuth flow handoff
- `web/src/lib/api.ts` — `exportAIBOM()`, `initiateWorkspaceOAuth()`, `disconnectWorkspace()`

---

## Sub-slice 1.1 — `framework_meta` consolidation

Pure refactor. Lowest risk. Unblocks 1.2–1.5. Targets a duplication between `ai_summary/framework_meta.py` and `compliance_summary/framework_meta.py` (verified identical at audit-time 2026-06-04; if they have drifted, that's part of this slice's scope to resolve toward the more recent version).

### Task 1.1.1: Move FRAMEWORK_META to `scanner_core/`

**Files:**
- Create: `platform/lambda/scanner_core/framework_meta.py`
- Modify: `platform/lambda/ai_summary/main.py`
- Modify: `platform/lambda/compliance_summary/main.py`
- Delete: `platform/lambda/ai_summary/framework_meta.py`
- Delete: `platform/lambda/compliance_summary/framework_meta.py`

- [ ] **Step 1: Verify the two existing copies are identical**

Run:
```bash
diff platform/lambda/ai_summary/framework_meta.py platform/lambda/compliance_summary/framework_meta.py
```
Expected: empty output (= identical). **If non-empty: stop, diff each block, ask KK which is canonical, document the divergence in the commit message.**

- [ ] **Step 2: Copy the (verified-identical) canonical file to scanner_core/**

Run:
```bash
cp platform/lambda/ai_summary/framework_meta.py platform/lambda/scanner_core/framework_meta.py
```

- [ ] **Step 3: Update `ai_summary/main.py` import**

Find the line:
```python
from framework_meta import ai_family_meta
```
Replace with:
```python
from scanner_core.framework_meta import ai_family_meta
```

(`scanner_core` is already on `PYTHONPATH` for Lambdas that import from it — verify with `grep -l 'from scanner_core' platform/lambda/` to confirm pattern; if no consumers exist, add `scanner_core` to the Lambda's bundling path in CDK.)

- [ ] **Step 4: Update `compliance_summary/main.py` import**

Find the line:
```python
from framework_meta import FRAMEWORK_META
```
Replace with:
```python
from scanner_core.framework_meta import FRAMEWORK_META
```

- [ ] **Step 5: Run existing tests against the new layout**

Run:
```bash
cd platform/lambda && python -m pytest ai_summary/tests/ compliance_summary/tests/ -v
```
Expected: all existing tests pass (same dict, same import names, just from a new module path).

- [ ] **Step 6: Delete the two duplicated copies**

Run:
```bash
git rm platform/lambda/ai_summary/framework_meta.py platform/lambda/compliance_summary/framework_meta.py
```

- [ ] **Step 7: Verify Lambda bundling still works**

The two consumer Lambdas (`ai_summary`, `compliance_summary`) need `scanner_core/` bundled into their zip. Check the existing CDK pattern:
```bash
grep -A 3 "ai_summary\|compliance_summary" platform/lib/api-stack.ts | grep -E "fromAsset|bundling|layer"
```
If `scanner_core/` is bundled as a Lambda Layer or via shared `Code.fromAsset()` aggregation, the import will resolve automatically. If not, add the bundling step here.

- [ ] **Step 8: Commit**

```bash
git add platform/lambda/scanner_core/framework_meta.py \
        platform/lambda/ai_summary/main.py \
        platform/lambda/compliance_summary/main.py
git rm platform/lambda/ai_summary/framework_meta.py platform/lambda/compliance_summary/framework_meta.py
git commit -m "refactor(framework-meta): consolidate to scanner_core canonical home"
```

### Task 1.1.2: Hotswap deploy + smoke

- [ ] **Step 1: cdk hotswap api stack**

```bash
cd platform
npx cdk deploy CisoCopilotApi --require-approval never --hotswap
```
Expected: `ai_summary` + `compliance_summary` Lambdas update. No IAM changes.

- [ ] **Step 2: Smoke `/ai/summary` returns 200**

```bash
curl -s -H "Authorization: Bearer $JWT" https://api.shasta.io/v1/ai/summary | jq '.frameworks_meta | keys'
```
Expected: array including `["nist_ai_rmf", "iso_42001", "owasp_llm_top10", ...]` — the 8 AI-family keys.

- [ ] **Step 3: Smoke `/compliance/summary` returns 200**

```bash
curl -s -H "Authorization: Bearer $JWT" https://api.shasta.io/v1/compliance/summary | jq '.summary | keys' | head
```
Expected: framework keys present.

### Task 1.1.3: Ship PR 1.1

- [ ] **Step 1: Push branch + PR**

```bash
git push -u origin feat/ai-security-slice-1
gh pr create --title "refactor(framework-meta): consolidate to scanner_core" \
  --body "$(cat <<'EOF'
## Summary
- Move FRAMEWORK_META from duplicated `ai_summary/framework_meta.py` + `compliance_summary/framework_meta.py` into one canonical `scanner_core/framework_meta.py`
- Both consumers re-import from the new path

Sub-slice 1.1 of the AI Security Slice 1 plan
(`docs/superpowers/plans/2026-06-05-ai-security-slice-1.md`). Pure refactor — no behavior change. Unblocks 1.2–1.5.

## Test plan
- [x] Existing `ai_summary` + `compliance_summary` test suites pass
- [x] `/ai/summary` smoke returns the 8 AI-family framework keys
- [x] `/compliance/summary` smoke returns 200

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Sub-slice 1.2 — AI-BOM export

No new ingest; reads only. Ships value alone. Tests against entity/edge/finding fixtures.

### Task 1.2.1: Pin `cyclonedx-python-lib` version

**Files:**
- Create: `platform/lambda/ai_bom_export/requirements.txt`

- [ ] **Step 1: Find the current PyPI version supporting CycloneDX-ML 1.6**

Run:
```bash
pip index versions cyclonedx-python-lib 2>&1 | head -3
# or: curl -s https://pypi.org/pypi/cyclonedx-python-lib/json | jq -r '.info.version'
```
Note the version reported (e.g. `8.9.0`).

- [ ] **Step 2: Verify it supports `MachineLearningModel` component type**

Run:
```bash
python3 -c "
import cyclonedx.model.component as c
import inspect
mc = [name for name in dir(c) if 'achine' in name or 'achin' in name]
print('ML-related symbols:', mc)
# look for: MachineLearningModel, or Component with type='machine-learning-model'
from cyclonedx.model.component import ComponentType
print('ComponentType values:', [t.value for t in ComponentType])
"
```
Expected: `ComponentType` enum includes `machine-learning-model`.

If `machine-learning-model` is not in the enum, the library's CycloneDX-ML support is stale — bump to a newer release or fall back to spec 1.5 with a documented limitation. **Do not invent the value.**

- [ ] **Step 3: Pin in requirements**

`platform/lambda/ai_bom_export/requirements.txt`:
```
cyclonedx-python-lib==<exact-version-from-step-1>
```

- [ ] **Step 4: Commit**

```bash
git add platform/lambda/ai_bom_export/requirements.txt
git commit -m "feat(ai-bom): pin cyclonedx-python-lib for CycloneDX-ML 1.6"
```

### Task 1.2.2: `ai_bom_export` Lambda — handler skeleton

**Files:**
- Create: `platform/lambda/ai_bom_export/main.py`
- Create: `platform/lambda/ai_bom_export/tests/__init__.py`
- Create: `platform/lambda/ai_bom_export/tests/test_main.py`

- [ ] **Step 1: Write the failing test for handler shape**

`platform/lambda/ai_bom_export/tests/test_main.py`:
```python
"""ai_bom_export — CycloneDX-ML 1.6 AI-BOM endpoint.

Auth: existing JWT path (tenant from claims.sub → users.tenant_id).
Format: ?format=cyclonedx (only Slice 1 value). Unknown format → 400.
"""
from unittest.mock import MagicMock, patch
import json
import pytest


@pytest.fixture
def mock_rds(monkeypatch):
    fake = MagicMock()
    monkeypatch.setenv("DB_CLUSTER_ARN", "arn:aws:rds:::cluster/x")
    monkeypatch.setenv("DB_SECRET_ARN", "arn:aws:secretsmanager:::secret/x")
    monkeypatch.setenv("DB_NAME", "ciso_copilot")
    import main
    monkeypatch.setattr(main, "rds_data", fake)
    return fake


def _event_with_tenant(tenant_id="t-1", fmt="cyclonedx"):
    return {
        "queryStringParameters": {"format": fmt},
        "requestContext": {
            "authorizer": {
                "claims": {
                    "sub": "subject-1",
                    "identities": '[{"userId": "subject-1"}]',
                }
            }
        },
    }


def test_returns_401_when_no_tenant(monkeypatch, mock_rds):
    mock_rds.execute_statement.return_value = {"records": []}  # users lookup empty
    import main
    resp = main.handler(_event_with_tenant(), None)
    assert resp["statusCode"] == 401
    assert json.loads(resp["body"])["error"] == "no_tenant"


def test_returns_400_for_unknown_format(monkeypatch, mock_rds):
    mock_rds.execute_statement.return_value = {
        "records": [[{"stringValue": "t-1"}]]
    }
    import main
    resp = main.handler(_event_with_tenant(fmt="spdx-ai"), None)
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"] == "unknown_format"
    assert json.loads(resp["body"])["supported"] == ["cyclonedx"]


def test_returns_200_with_cyclonedx_payload_for_empty_inventory(monkeypatch, mock_rds):
    # users lookup → tenant resolved; entity/edge/finding lookups → empty
    mock_rds.execute_statement.side_effect = [
        {"records": [[{"stringValue": "t-1"}]]},  # tenant_id
        {"records": []},  # entities
        {"records": []},  # edges
        {"records": []},  # findings
    ]
    import main
    resp = main.handler(_event_with_tenant(), None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["bomFormat"] == "CycloneDX"
    assert body["specVersion"] == "1.6"
    assert body["components"] == []
    assert body["dependencies"] == []
    assert body["vulnerabilities"] == []
    assert resp["headers"]["Content-Type"].startswith("application/vnd.cyclonedx+json")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd platform/lambda/ai_bom_export && python -m pytest tests/ -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'main'` (haven't written `main.py` yet).

- [ ] **Step 3: Write minimal `main.py` that satisfies the three tests**

`platform/lambda/ai_bom_export/main.py`:
```python
"""GET /v1/ai/bom?format=cyclonedx — CycloneDX-ML 1.6 AI-BOM export.

Reads AI-touching entities + edges + AI-attached findings for the
caller's tenant and emits a valid CycloneDX-ML 1.6 JSON document.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import boto3
from cyclonedx.model.bom import Bom
from cyclonedx.output.json import JsonV1Dot6
from cyclonedx.schema import SchemaVersion

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

rds_data = boto3.client("rds-data")

_SUPPORTED_FORMATS = ["cyclonedx"]


def handler(event: dict, context) -> dict:
    tenant_id = _resolve_tenant_id(event)
    if not tenant_id:
        return _resp(401, {"error": "no_tenant"})

    fmt = (event.get("queryStringParameters") or {}).get("format", "cyclonedx")
    if fmt not in _SUPPORTED_FORMATS:
        return _resp(400, {"error": "unknown_format", "supported": _SUPPORTED_FORMATS})

    entities = _select_ai_entities(tenant_id)
    edges    = _select_ai_edges(tenant_id)
    findings = _select_ai_findings(tenant_id)

    bom = _build_bom(tenant_id, entities, edges, findings)
    body = JsonV1Dot6(bom).output_as_string()

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/vnd.cyclonedx+json; version=1.6",
            "Content-Disposition": f'attachment; filename="shasta-ai-bom-{tenant_id}-{date_str}.cdx.json"',
        },
        "body": body,
    }


def _resolve_tenant_id(event: dict) -> str | None:
    """Canonical pattern: identities[0].userId → users.sso_subject lookup."""
    claims = (event.get("requestContext", {}).get("authorizer", {}).get("claims", {}) or {})
    identities_raw = claims.get("identities")
    subject = None
    if identities_raw:
        try:
            identities = json.loads(identities_raw) if isinstance(identities_raw, str) else identities_raw
            if identities:
                subject = identities[0].get("userId")
        except (json.JSONDecodeError, KeyError, IndexError):
            pass
    if not subject:
        subject = claims.get("sub")
    if not subject:
        return None

    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql="SELECT tenant_id::text FROM users WHERE sso_subject = :s LIMIT 1",
        parameters=[{"name": "s", "value": {"stringValue": subject}}],
    )
    records = rs.get("records", [])
    if not records:
        return None
    return records[0][0].get("stringValue")


def _select_ai_entities(tenant_id: str) -> list[dict]:
    # Stubbed empty for skeleton; Task 1.2.3 implements.
    return []


def _select_ai_edges(tenant_id: str) -> list[dict]:
    # Stubbed empty for skeleton; Task 1.2.4 implements.
    return []


def _select_ai_findings(tenant_id: str) -> list[dict]:
    # Stubbed empty for skeleton; Task 1.2.5 implements.
    return []


def _build_bom(tenant_id: str, entities: list, edges: list, findings: list) -> Bom:
    bom = Bom()
    # Components / dependencies / vulnerabilities filled in by later tasks.
    return bom


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd platform/lambda/ai_bom_export && python -m pytest tests/ -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/ai_bom_export/main.py platform/lambda/ai_bom_export/tests/
git commit -m "feat(ai-bom): handler skeleton with tenant resolution + format validation"
```

### Task 1.2.3: Entity → CycloneDX component mapping

**Files:**
- Modify: `platform/lambda/ai_bom_export/main.py`
- Modify: `platform/lambda/ai_bom_export/tests/test_main.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_main.py`:
```python
def test_entity_emits_machine_learning_model_component(monkeypatch, mock_rds):
    mock_rds.execute_statement.side_effect = [
        {"records": [[{"stringValue": "t-1"}]]},  # tenant
        # entities — one bedrock_model
        {"records": [[
            {"stringValue": "e-1"},                              # id
            {"stringValue": "bedrock_model"},                    # kind
            {"stringValue": "claude-3-opus"},                    # name
            {"stringValue": "arn:aws:bedrock:us-east-1:111:model/x"},  # resource_arn
            {"stringValue": "shasta-runner-aws"},                # detector_id
            {"stringValue": "2026-05-01T10:00:00Z"},             # created_at
        ]]},
        {"records": []},  # edges
        {"records": []},  # findings
    ]
    import main, json
    resp = main.handler(_event_with_tenant(), None)
    body = json.loads(resp["body"])
    assert len(body["components"]) == 1
    comp = body["components"][0]
    assert comp["type"] == "machine-learning-model"
    assert comp["bom-ref"] == "e-1"
    assert comp["name"] == "claude-3-opus"
    props = {p["name"]: p["value"] for p in comp["properties"]}
    assert props["shasta:kind"] == "bedrock_model"
    assert props["shasta:detector_id"] == "shasta-runner-aws"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd platform/lambda/ai_bom_export && python -m pytest tests/ -v -k test_entity_emits`
Expected: FAIL — `len(body["components"]) == 0`.

- [ ] **Step 3: Implement `_select_ai_entities` + `_entity_to_component`**

Replace the stub `_select_ai_entities` and extend `_build_bom`:
```python
from cyclonedx.model.component import Component, ComponentType
from cyclonedx.model import Property

# entities.kind → CycloneDX ComponentType (per spec §8.3)
_KIND_TO_TYPE: dict[str, ComponentType] = {
    "bedrock_model":            ComponentType.MACHINE_LEARNING_MODEL,
    "ai_model":                 ComponentType.MACHINE_LEARNING_MODEL,
    "sagemaker_model":          ComponentType.MACHINE_LEARNING_MODEL,
    "sagemaker_endpoint":       ComponentType.MACHINE_LEARNING_MODEL,
    "azure_openai_deployment":  ComponentType.MACHINE_LEARNING_MODEL,
    "vertex_endpoint":          ComponentType.MACHINE_LEARNING_MODEL,
    "ai_agent":                 ComponentType.APPLICATION,
    "ai_mcp_server":            ComponentType.APPLICATION,
    "ai_saas_app":              ComponentType.APPLICATION,
    "ai_framework":             ComponentType.LIBRARY,
    "ai_tool":                  ComponentType.LIBRARY,
    "ai_vector_db":             ComponentType.DATA,
    "ai_prompt":                ComponentType.DATA,
    "ai_embedding":             ComponentType.DATA,
}

# Excluded from BOM (operational rollups + transient events)
_EXCLUDED_KINDS = frozenset({"bedrock_invocation", "ai_user_signin"})


def _select_ai_entities(tenant_id: str) -> list[dict]:
    in_kinds = ",".join(f"'{k}'" for k in _KIND_TO_TYPE.keys())
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            f"SELECT id::text, kind, name, resource_arn, detector_id, created_at::text "
            f"FROM entities "
            f"WHERE tenant_id = CAST(:tid AS UUID) "
            f"  AND kind IN ({in_kinds})"
        ),
        parameters=[{"name": "tid", "value": {"stringValue": tenant_id}}],
    )
    rows = []
    for r in rs.get("records", []):
        rows.append({
            "id":           r[0].get("stringValue"),
            "kind":         r[1].get("stringValue"),
            "name":         r[2].get("stringValue") or "(unnamed)",
            "resource_arn": r[3].get("stringValue") or "",
            "detector_id":  r[4].get("stringValue") or "",
            "created_at":   r[5].get("stringValue") or "",
        })
    return rows


def _entity_to_component(e: dict) -> Component:
    return Component(
        type=_KIND_TO_TYPE[e["kind"]],
        bom_ref=e["id"],
        name=e["name"],
        properties=[
            Property(name="shasta:kind",          value=e["kind"]),
            Property(name="shasta:detector_id",   value=e["detector_id"]),
            Property(name="shasta:resource_arn",  value=e["resource_arn"]),
            Property(name="shasta:discovered_at", value=e["created_at"]),
        ],
    )


def _build_bom(tenant_id: str, entities: list, edges: list, findings: list) -> Bom:
    bom = Bom()
    for e in entities:
        bom.components.add(_entity_to_component(e))
    return bom
```

- [ ] **Step 4: Run to verify pass**

Run: `cd platform/lambda/ai_bom_export && python -m pytest tests/ -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/ai_bom_export/main.py platform/lambda/ai_bom_export/tests/test_main.py
git commit -m "feat(ai-bom): entity → CycloneDX component mapping"
```

### Task 1.2.4: Edge → dependency mapping

**Files:**
- Modify: `platform/lambda/ai_bom_export/main.py`
- Modify: `platform/lambda/ai_bom_export/tests/test_main.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_main.py`:
```python
def test_edges_emit_dependencies(monkeypatch, mock_rds):
    mock_rds.execute_statement.side_effect = [
        {"records": [[{"stringValue": "t-1"}]]},  # tenant
        # entities — two: one repo, one framework
        {"records": [
            [{"stringValue": "repo-1"}, {"stringValue": "ai_framework"},
             {"stringValue": "langchain"}, {"stringValue": ""},
             {"stringValue": "ai-scanner"}, {"stringValue": "2026-05-01"}],
            [{"stringValue": "fw-1"},   {"stringValue": "ai_framework"},
             {"stringValue": "openai"},  {"stringValue": ""},
             {"stringValue": "ai-scanner"}, {"stringValue": "2026-05-01"}],
        ]},
        # edges — repo uses framework
        {"records": [[{"stringValue": "repo-1"}, {"stringValue": "fw-1"}, {"stringValue": "uses"}]]},
        {"records": []},  # findings
    ]
    import main, json
    resp = main.handler(_event_with_tenant(), None)
    body = json.loads(resp["body"])
    assert len(body["dependencies"]) == 1
    dep = body["dependencies"][0]
    assert dep["ref"] == "repo-1"
    assert dep["dependsOn"] == ["fw-1"]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd platform/lambda/ai_bom_export && python -m pytest tests/ -v -k test_edges_emit`
Expected: FAIL.

- [ ] **Step 3: Implement `_select_ai_edges` + Dependency emit**

Add to `main.py`:
```python
from cyclonedx.model.dependency import Dependency


def _select_ai_edges(tenant_id: str) -> list[dict]:
    in_kinds = ",".join(f"'{k}'" for k in _KIND_TO_TYPE.keys())
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            f"SELECT e.source_entity_id::text, e.target_entity_id::text, e.kind "
            f"FROM edges e "
            f"WHERE e.tenant_id = CAST(:tid AS UUID) "
            f"  AND EXISTS (SELECT 1 FROM entities s "
            f"              WHERE s.id = e.source_entity_id "
            f"                AND s.kind IN ({in_kinds})) "
            f"  AND EXISTS (SELECT 1 FROM entities t "
            f"              WHERE t.id = e.target_entity_id "
            f"                AND t.kind IN ({in_kinds}))"
        ),
        parameters=[{"name": "tid", "value": {"stringValue": tenant_id}}],
    )
    return [
        {"source": r[0].get("stringValue"),
         "target": r[1].get("stringValue"),
         "kind":   r[2].get("stringValue")}
        for r in rs.get("records", [])
    ]
```

In `_build_bom`:
```python
    # group edges by source for one Dependency per source
    from collections import defaultdict
    deps_by_source: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        deps_by_source[edge["source"]].append(edge["target"])
    for source, targets in deps_by_source.items():
        bom.dependencies.add(Dependency(ref=source, dependencies=[
            Dependency(ref=t) for t in targets
        ]))
```

- [ ] **Step 4: Run to verify pass**

Run: `cd platform/lambda/ai_bom_export && python -m pytest tests/ -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/ai_bom_export/main.py platform/lambda/ai_bom_export/tests/test_main.py
git commit -m "feat(ai-bom): edge → CycloneDX dependency mapping"
```

### Task 1.2.5: Finding → vulnerability mapping

**Files:**
- Modify: `platform/lambda/ai_bom_export/main.py`
- Modify: `platform/lambda/ai_bom_export/tests/test_main.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_main.py`:
```python
def test_findings_emit_vulnerabilities(monkeypatch, mock_rds):
    mock_rds.execute_statement.side_effect = [
        {"records": [[{"stringValue": "t-1"}]]},  # tenant
        {"records": [[{"stringValue": "fw-1"}, {"stringValue": "ai_framework"},
                      {"stringValue": "langchain"}, {"stringValue": ""},
                      {"stringValue": "ai-scanner"}, {"stringValue": "2026-05-01"}]]},
        {"records": []},  # edges
        # findings — one SCA vuln tagged against owasp_llm_top10
        {"records": [[
            {"stringValue": "f-1"},                       # finding_id
            {"stringValue": "sca_vuln:CVE-2026-45134"},  # check_id
            {"stringValue": "critical"},                  # severity
            {"stringValue": "fw-1"},                      # entity_id (attached entity)
            {"stringValue": '{"owasp_llm_top10": ["LLM03:2025"]}'},  # frameworks
        ]]},
    ]
    import main, json
    resp = main.handler(_event_with_tenant(), None)
    body = json.loads(resp["body"])
    assert len(body["vulnerabilities"]) == 1
    vuln = body["vulnerabilities"][0]
    assert vuln["bom-ref"] == "f-1"
    assert vuln["id"] == "sca_vuln:CVE-2026-45134"
    assert vuln["ratings"][0]["severity"] == "critical"
    assert vuln["affects"][0]["ref"] == "fw-1"
```

- [ ] **Step 2: Run to verify failure**

Expected: FAIL.

- [ ] **Step 3: Implement `_select_ai_findings` + vulnerability emit**

```python
from cyclonedx.model.vulnerability import (
    Vulnerability, VulnerabilityRating, VulnerabilityScoreSource, BomTarget,
)
from cyclonedx.model.impact_analysis import ImpactAnalysisState
from cyclonedx.model.vulnerability import VulnerabilitySeverity

_SEVERITY_MAP = {
    "critical":      VulnerabilitySeverity.CRITICAL,
    "high":          VulnerabilitySeverity.HIGH,
    "medium":        VulnerabilitySeverity.MEDIUM,
    "low":           VulnerabilitySeverity.LOW,
    "informational": VulnerabilitySeverity.INFO,
}


def _select_ai_findings(tenant_id: str) -> list[dict]:
    in_kinds = ",".join(f"'{k}'" for k in _KIND_TO_TYPE.keys())
    # NOTE: `f.frameworks ? 'owasp_llm_top10'` requires `frameworks` to be
    # a JSON object; PER FINDINGS.md §A.4, ai_supply_chain_matcher emits
    # `frameworks: []` (an array) for some critical findings. The `?`
    # operator returns false for arrays, so those rows are NOT filtered
    # in via that branch — they're caught instead by the
    # `check_id LIKE 'sca_vuln:%'` branch. Keep both branches.
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            f"SELECT DISTINCT f.finding_id::text, f.check_id, f.severity, "
            f"       fe.entity_id::text, f.frameworks::text "
            f"FROM findings f "
            f"JOIN finding_entities fe ON fe.finding_id = f.finding_id "
            f"JOIN entities e          ON e.id = fe.entity_id "
            f"WHERE f.tenant_id = CAST(:tid AS UUID) "
            f"  AND e.kind IN ({in_kinds}) "
            f"  AND ( "
            f"        (jsonb_typeof(f.frameworks) = 'object' "
            f"         AND f.frameworks ? 'owasp_llm_top10') "
            f"     OR f.check_id LIKE 'sca_vuln:%' "
            f"  )"
        ),
        parameters=[{"name": "tid", "value": {"stringValue": tenant_id}}],
    )
    return [
        {"finding_id": r[0].get("stringValue"),
         "check_id":   r[1].get("stringValue"),
         "severity":   r[2].get("stringValue"),
         "entity_id":  r[3].get("stringValue"),
         # Defensive: ai_supply_chain_matcher writes frameworks as [], not {}.
         # Coerce to {} so downstream is array-safe (FINDINGS A.4).
         "frameworks": _safe_frameworks(r[4].get("stringValue"))}
        for r in rs.get("records", [])
    ]


def _safe_frameworks(raw: str | None) -> dict:
    """Coerce frameworks JSON to a dict. Handles legacy buggy `[]` rows."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
```

In `_build_bom`:
```python
    for f in findings:
        sev = _SEVERITY_MAP.get(f["severity"], VulnerabilitySeverity.UNKNOWN)
        vuln = Vulnerability(
            bom_ref=f["finding_id"],
            id=f["check_id"],
            ratings=[VulnerabilityRating(severity=sev, source_name="shasta")],
            affects=[BomTarget(ref=f["entity_id"])],
        )
        bom.vulnerabilities.add(vuln)
```

(Library symbol names may differ across versions — verify via the version pinned in Task 1.2.1 and adjust imports if needed.)

- [ ] **Step 4: Run to verify pass**

Run: `cd platform/lambda/ai_bom_export && python -m pytest tests/ -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/ai_bom_export/main.py platform/lambda/ai_bom_export/tests/test_main.py
git commit -m "feat(ai-bom): finding → CycloneDX vulnerability mapping"
```

### Task 1.2.6: CDK — `ai_bom_export` Lambda + API route + IAM

**Files:**
- Modify: `platform/lib/api-stack.ts`

- [ ] **Step 1: Find the existing Lambda + route pattern in api-stack.ts**

Run:
```bash
grep -n "compliance_summary\|compliance/summary" platform/lib/api-stack.ts | head -10
```
Use `compliance_summary` Lambda + its `/v1/compliance/summary` GET route as the template — same auth, same Aurora grant pattern.

- [ ] **Step 2: Add `ai_bom_export` Lambda + grants + route**

Add to `api-stack.ts` (place alongside `compliance_summary` Lambda declaration):
```typescript
const aiBomExportFn = new lambda.Function(this, 'AIBOMExportFn', {
  functionName: 'ciso-copilot-ai-bom-export',
  runtime: lambda.Runtime.PYTHON_3_12,
  handler: 'main.handler',
  code: lambda.Code.fromAsset('lambda/ai_bom_export', {
    bundling: {
      image: lambda.Runtime.PYTHON_3_12.bundlingImage,
      command: [
        'bash', '-c',
        'pip install -r requirements.txt -t /asset-output && cp -r . /asset-output',
      ],
    },
  }),
  timeout: cdk.Duration.seconds(30),
  memorySize: 512,
  environment: {
    DB_CLUSTER_ARN: props.dbCluster.clusterArn,
    DB_SECRET_ARN:  props.dbCluster.secret!.secretArn,
    DB_NAME:        'ciso_copilot',
  },
});
props.dbCluster.grantDataApiAccess(aiBomExportFn);

// route: GET /v1/ai/bom
const aiBom = aiRoot.addResource('bom');  // aiRoot = /v1/ai (find existing)
aiBom.addMethod('GET', new apigw.LambdaIntegration(aiBomExportFn), {
  authorizer: cognitoAuthorizer,  // existing
  authorizationType: apigw.AuthorizationType.COGNITO,
});
```

Verify by `cdk synth`:
```bash
cd platform && npx cdk synth CisoCopilotApi > /tmp/synth.txt && grep -c "AIBOMExport" /tmp/synth.txt
```
Expected: > 0.

- [ ] **Step 3: Hotswap deploy**

```bash
cd platform && npx cdk deploy CisoCopilotApi --require-approval never --hotswap
```
Note: IAM changes (the new Lambda + grant). If hotswap can't do it, it will fall through to full deploy.

- [ ] **Step 4: Smoke the endpoint**

```bash
curl -sS -H "Authorization: Bearer $JWT" \
  "https://api.shasta.io/v1/ai/bom?format=cyclonedx" -o /tmp/bom.json -w "%{http_code}\n"
jq '.bomFormat, .specVersion, (.components | length)' /tmp/bom.json
```
Expected: `200`, then `"CycloneDX"`, `"1.6"`, and component count.

- [ ] **Step 5: Validate against CycloneDX schema (offline)**

```bash
pip install cyclonedx-bom
cyclonedx validate --input-file /tmp/bom.json --input-format json --input-version v1_6
```
Expected: `Loaded BOM is valid against the JSON Schema for v1.6`.

- [ ] **Step 6: Commit**

```bash
git add platform/lib/api-stack.ts
git commit -m "feat(cdk): wire ai_bom_export Lambda + /v1/ai/bom route"
```

### Task 1.2.7: Frontend — Export AI-BOM button on `/ai`

**Files:**
- Create: `web/src/components/ExportAIBOMButton.tsx`
- Modify: `web/src/lib/api.ts`
- Modify: `web/src/routes/AISummary.tsx`

- [ ] **Step 1: Add API client function**

Append to `web/src/lib/api.ts`:
```typescript
export async function exportAIBOM(): Promise<Blob> {
  const token = await getAccessToken();  // existing pattern
  const r = await fetch(`${API_BASE}/v1/ai/bom?format=cyclonedx`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!r.ok) throw new Error(`Export failed: ${r.status}`);
  return r.blob();
}
```

- [ ] **Step 2: Create `ExportAIBOMButton.tsx`**

```tsx
import { useState } from 'react';
import { exportAIBOM } from '../lib/api';

export function ExportAIBOMButton() {
  const [busy, setBusy] = useState(false);
  const onClick = async () => {
    setBusy(true);
    try {
      const blob = await exportAIBOM();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      const date = new Date().toISOString().slice(0, 10);
      a.href = url;
      a.download = `shasta-ai-bom-${date}.cdx.json`;
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setBusy(false);
    }
  };
  return (
    <button
      className="px-3 py-1.5 text-sm border border-slate-300 rounded hover:bg-slate-50 disabled:opacity-50"
      onClick={onClick}
      disabled={busy}
    >
      {busy ? 'Exporting…' : 'Export AI-BOM'}
    </button>
  );
}
```

- [ ] **Step 3: Mount in `AISummary.tsx` page header**

Find the page title row and add the button on the right side:
```tsx
import { ExportAIBOMButton } from '../components/ExportAIBOMButton';

// in the title row JSX:
<div className="flex items-center justify-between mb-4">
  <h1 className="text-2xl font-semibold">AI Surface</h1>
  <ExportAIBOMButton />
</div>
```

- [ ] **Step 4: Build + smoke locally**

```bash
cd web && pnpm build && pnpm dev
```
Open `http://localhost:5173/ai`. Click "Export AI-BOM". Verify file downloads.

- [ ] **Step 5: Deploy web**

```bash
cd web && pnpm build
aws s3 sync dist/ s3://<WEB_BUCKET>/ --delete
aws cloudfront create-invalidation --distribution-id <CLOUDFRONT_DIST_ID> --paths '/*'
```

- [ ] **Step 6: Commit**

```bash
git add web/src/components/ExportAIBOMButton.tsx web/src/lib/api.ts web/src/routes/AISummary.tsx
git commit -m "feat(web): Export AI-BOM button on /ai page"
```

### Task 1.2.8: Ship PR 1.2

- [ ] **Step 1: `gh pr create`**

```bash
gh pr create --title "feat(ai-bom): CycloneDX-ML 1.6 AI-BOM export on /ai" \
  --body "$(cat <<'EOF'
## Summary
- New `ai_bom_export` Lambda emits CycloneDX-ML 1.6 JSON from `entities` + `edges` + AI-attached findings
- `GET /v1/ai/bom?format=cyclonedx` returns the BOM as a downloadable attachment
- "Export AI-BOM" button on `/ai` page header

Sub-slice 1.2 of the AI Security Slice 1 plan.

## Test plan
- [x] Unit tests cover handler shape, format validation, entity/edge/finding mapping
- [x] Smoke: `curl /v1/ai/bom?format=cyclonedx` returns 200 with valid JSON
- [x] Offline: validates clean against CycloneDX-ML 1.6 schema via `cyclonedx validate`
- [x] Browser-side: button downloads file with correct filename
EOF
)"
```

---

## Sub-slice 1.3 — Bedrock InvokeModel runtime detector

Extension to existing `event_router`. No new Lambda. Per-call rollup; daily high-volume finding via separate scheduled invocation.

### Task 1.3.1: Add EventBridge rule for Bedrock events

**Files:**
- Modify: `platform/lib/scan-stack.ts` (or wherever EventBridge rules live — `grep -n "EventBridge\|RuleProps" platform/lib/*.ts` to confirm)

- [ ] **Step 1: Locate existing event_router EventBridge rule**

```bash
grep -n "event_router\|EventRouterFn\|aws.eventbridge.Rule" platform/lib/scan-stack.ts platform/lib/api-stack.ts | head
```

- [ ] **Step 2: Add new rule alongside existing SOC drift rule**

```typescript
new events.Rule(this, 'BedrockInvokeRule', {
  ruleName: 'ciso-copilot-bedrock-invoke',
  eventPattern: {
    // PER CLAUDE.md GOTCHA: do NOT filter on `source`.
    // CloudTrail emits source=aws.bedrock-runtime; SOC events emit
    // source=aws.<service>; the universal filter is detail-type +
    // detail.eventName.
    detailType: ['AWS API Call via CloudTrail'],
    detail: {
      eventName: [
        'InvokeModel', 'InvokeModelWithResponseStream',
        'Converse', 'ConverseStream',
        'InvokeAgent', 'Retrieve', 'RetrieveAndGenerate',
      ],
    },
  },
  targets: [new targets.LambdaFunction(eventRouterFn)],  // existing fn
});
```

- [ ] **Step 3: cdk synth + verify rule shape**

```bash
cd platform && npx cdk synth CisoCopilotScan > /tmp/synth.txt
grep -A 20 "BedrockInvokeRule" /tmp/synth.txt | head -30
```
Expected: pattern matches above (no `source` field).

- [ ] **Step 4: Commit**

```bash
git add platform/lib/scan-stack.ts
git commit -m "feat(cdk): EventBridge rule for Bedrock InvokeModel + siblings"
```

### Task 1.3.2: `event_router` branch for Bedrock events — entity upserts

**Files:**
- Modify: `platform/lambda/event_router/main.py`
- Create: `platform/lambda/event_router/tests/test_bedrock.py`

- [ ] **Step 1: Write failing test for entity upserts**

`platform/lambda/event_router/tests/test_bedrock.py`:
```python
"""Bedrock InvokeModel handler — per-call entity upserts.

bedrock_model entity is upserted on every InvokeModel event.
bedrock_invocation rollup entity is upserted with counter++.
"""
from unittest.mock import MagicMock
import pytest


def _bedrock_invoke_event(
    account_id="111111111111",
    region="us-east-1",
    model_id="anthropic.claude-3-opus-20240229-v1:0",
    principal_arn="arn:aws:iam::111111111111:role/PlatformTeam",
    event_name="InvokeModel",
):
    return {
        "detail-type": "AWS API Call via CloudTrail",
        "source": "aws.bedrock-runtime",  # informational; not filtered on
        "detail": {
            "eventName":          event_name,
            "eventTime":          "2026-06-05T10:00:00Z",
            "awsRegion":          region,
            "recipientAccountId": account_id,
            "userIdentity":       {"arn": principal_arn, "type": "AssumedRole"},
            "requestParameters":  {"modelId": model_id},
            "sourceIPAddress":    "10.0.0.42",
        },
    }


@pytest.fixture
def mock_rds(monkeypatch):
    import main
    fake = MagicMock()
    monkeypatch.setattr(main, "rds_data", fake)
    monkeypatch.setenv("DB_CLUSTER_ARN", "arn:x")
    monkeypatch.setenv("DB_SECRET_ARN", "arn:y")
    monkeypatch.setenv("DB_NAME", "ciso_copilot")
    return fake


def test_invokemodel_upserts_bedrock_model_entity(mock_rds):
    # tenant lookup returns t-1; entity upsert returns id
    mock_rds.execute_statement.side_effect = [
        {"records": [[{"stringValue": "t-1"}]]},  # tenant_id lookup
        {"records": [[{"stringValue": "e-1"}]]},  # bedrock_model upsert
        {"records": [[{"stringValue": "e-2"}]]},  # bedrock_invocation upsert (rollup)
        {"records": []},  # edge upsert
    ]
    import main
    main.handler(_bedrock_invoke_event(), None)
    # Sanity: ≥3 statements were issued
    assert mock_rds.execute_statement.call_count >= 3
    # The 2nd statement should INSERT into entities with kind=bedrock_model
    second_sql = mock_rds.execute_statement.call_args_list[1].kwargs["sql"]
    assert "INSERT INTO entities" in second_sql
    assert "bedrock_model" in second_sql or ":kind" in second_sql
```

- [ ] **Step 2: Run failing test**

```bash
cd platform/lambda/event_router && python -m pytest tests/ -v -k test_invokemodel
```
Expected: FAIL.

- [ ] **Step 3: Implement Bedrock branch in `main.py`**

In `event_router/main.py`, add a handler branch. Find the existing `def handler(event, context)` and route Bedrock events:

```python
_BEDROCK_EVENT_NAMES = frozenset({
    "InvokeModel", "InvokeModelWithResponseStream",
    "Converse", "ConverseStream",
    "InvokeAgent", "Retrieve", "RetrieveAndGenerate",
})

def _is_bedrock_event(event: dict) -> bool:
    return (
        event.get("detail-type") == "AWS API Call via CloudTrail"
        and event.get("detail", {}).get("eventName") in _BEDROCK_EVENT_NAMES
    )


def _handle_bedrock(event: dict) -> dict:
    detail = event["detail"]
    account_id = detail["recipientAccountId"]
    tenant_id = _tenant_for_account(account_id)
    if not tenant_id:
        return {"status": "no_tenant_for_account", "account_id": account_id}

    model_id     = (detail.get("requestParameters") or {}).get("modelId", "unknown")
    principal    = detail.get("userIdentity", {}).get("arn", "unknown")
    region       = detail.get("awsRegion", "unknown")
    event_day    = detail["eventTime"][:10]  # YYYY-MM-DD

    # 1) Upsert bedrock_model entity (per tenant + model + region)
    model_nk = f"bedrock_model::{region}::{model_id}"
    model_entity_id = _upsert_entity(
        tenant_id=tenant_id, kind="bedrock_model",
        natural_key=model_nk, name=model_id,
        attributes={"region": region, "model_id": model_id},
        detector_id="event-router-bedrock",
    )

    # 2) Upsert bedrock_invocation rollup (per tenant + principal + model + day)
    inv_nk = f"bedrock_invocation::{principal}::{model_id}::{event_day}"
    _upsert_invocation_rollup(
        tenant_id=tenant_id, natural_key=inv_nk,
        principal=principal, model_id=model_id, day=event_day,
    )

    # 3) Upsert edge: iam_principal --uses-> bedrock_model
    principal_nk = f"iam_principal::{principal}"
    principal_entity_id = _upsert_entity(
        tenant_id=tenant_id, kind="iam_principal",
        natural_key=principal_nk, name=principal,
        attributes={"arn": principal},
        detector_id="event-router-bedrock",
    )
    _upsert_edge(
        tenant_id=tenant_id,
        source=principal_entity_id, target=model_entity_id, kind="uses",
    )

    return {"status": "ok", "model": model_id, "principal": principal}


def handler(event: dict, context) -> dict:
    if _is_bedrock_event(event):
        return _handle_bedrock(event)
    # ... existing routing for SOC drift / other events
```

Implement `_upsert_entity`, `_upsert_invocation_rollup`, `_upsert_edge`, `_tenant_for_account` (or reuse if existing helpers cover the shape) — follow the existing `entities_api._upsert_repo_entity` pattern, careful about the Aurora schema gotchas in CLAUDE.md (entities PK is `id`; finding_id not id on findings; etc).

- [ ] **Step 4: Run test to verify pass**

```bash
cd platform/lambda/event_router && python -m pytest tests/ -v
```

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/event_router/main.py platform/lambda/event_router/tests/test_bedrock.py
git commit -m "feat(event-router): Bedrock InvokeModel entity + rollup upserts"
```

### Task 1.3.3: Bedrock findings — unsanctioned, cross-region, model_inventory

**Files:**
- Modify: `platform/lambda/event_router/main.py`
- Modify: `platform/lambda/event_router/tests/test_bedrock.py`

- [ ] **Step 1: Write failing tests for the 3 per-event findings**

```python
def test_unsanctioned_principal_emits_finding(mock_rds):
    # tenant with bedrock_allowed_principals set; principal NOT in the set
    mock_rds.execute_statement.side_effect = [
        {"records": [[{"stringValue": "t-1"}]]},  # tenant lookup
        {"records": [[{"stringValue": '{"bedrock_allowed_principals":["arn:aws:iam::111:role/Allowed"]}'}]]},  # evidence_packet lookup
        {"records": [[{"stringValue": "e-1"}]]},  # bedrock_model
        {"records": [[{"stringValue": "e-2"}]]},  # rollup
        {"records": [[{"stringValue": "e-3"}]]},  # principal
        {"records": []},  # edge
        {"records": [[{"stringValue": "f-1"}]]},  # finding insert
    ]
    import main
    main.handler(_bedrock_invoke_event(), None)
    # The last call should be a findings INSERT with check_id
    last_call = mock_rds.execute_statement.call_args_list[-1]
    assert "INSERT INTO findings" in last_call.kwargs["sql"]
    params = {p["name"]: p["value"] for p in last_call.kwargs["parameters"]}
    assert params["check_id"]["stringValue"] == "aws_bedrock_invoke_unsanctioned"


def test_no_finding_when_principal_in_allowed_list(mock_rds):
    mock_rds.execute_statement.side_effect = [
        {"records": [[{"stringValue": "t-1"}]]},
        {"records": [[{"stringValue": '{"bedrock_allowed_principals":["arn:aws:iam::111111111111:role/PlatformTeam"]}'}]]},
        {"records": [[{"stringValue": "e-1"}]]},
        {"records": [[{"stringValue": "e-2"}]]},
        {"records": [[{"stringValue": "e-3"}]]},
        {"records": []},
    ]
    import main
    main.handler(_bedrock_invoke_event(), None)
    # No INSERT INTO findings call
    finding_calls = [c for c in mock_rds.execute_statement.call_args_list
                     if "INSERT INTO findings" in c.kwargs.get("sql", "")]
    assert len(finding_calls) == 0


def test_cross_region_emits_low_severity_finding(mock_rds):
    # Principal in us-east-1 invokes model in us-west-2 (mock the model entity is in us-west-2)
    # Implementation reads bedrock_model.attributes.region vs the principal's region
    # ... follow the same pattern
    pass  # leave as marker test; implement once the field is settled
```

- [ ] **Step 2: Implement detectors**

In `_handle_bedrock` after the edge upsert:
```python
    # Detector A: unsanctioned principal
    allowed = _bedrock_allowed_principals(tenant_id, account_id)
    if allowed and principal not in allowed:
        _emit_finding(
            tenant_id=tenant_id,
            check_id="aws_bedrock_invoke_unsanctioned",
            severity="medium", status="fail",
            entity_id=model_entity_id,
            conn_id=_conn_id_for_account(tenant_id, account_id),
            scan_id=_synthetic_scan_id("event_router"),  # existing helper
            evidence_packet={
                "principal": principal, "model_id": model_id,
                "region": region, "allowed_count": len(allowed),
            },
            frameworks={  # tagged here; later picked up by registry.apply()
                "nist_ai_rmf": ["GOVERN 1.1", "MANAGE 1.3"],
                "owasp_llm_top10": ["LLM08:2025"],
            },
        )

    # Detector B: model_inventory (first sighting per (model_id, region))
    if _is_first_sighting(tenant_id, model_nk):
        _emit_finding(
            tenant_id=tenant_id,
            check_id="aws_bedrock_model_inventory",
            severity="informational", status="pass",
            entity_id=model_entity_id,
            conn_id=_conn_id_for_account(tenant_id, account_id),
            scan_id=_synthetic_scan_id("event_router"),
            evidence_packet={"model_id": model_id, "region": region},
            frameworks={"nist_ai_rmf": ["MAP 1.1"]},
        )

    # Detector C: cross-region (principal region ≠ model region)
    # principal region is derived from event awsRegion (where the call was made)
    # model region is `region` here (where Bedrock service ran)
    # cross-region applies if userIdentity.invokedBy or other regional metadata diverges
    # NOTE: in practice for Bedrock all events are regional to where the model lives;
    # leaving this as Slice 2 once we have a concrete heuristic. Document the deferral.
```

- [ ] **Step 3: Run + commit**

```bash
cd platform/lambda/event_router && python -m pytest tests/ -v
git add platform/lambda/event_router/main.py platform/lambda/event_router/tests/test_bedrock.py
git commit -m "feat(event-router): Bedrock unsanctioned + model_inventory detectors"
```

### Task 1.3.4: Daily high-volume rollup — scheduled invocation

**Files:**
- Modify: `platform/lib/scan-stack.ts`
- Modify: `platform/lambda/event_router/main.py`

- [ ] **Step 1: Add EventBridge schedule (00:05 UTC daily)**

```typescript
new events.Rule(this, 'BedrockDailyRollupRule', {
  ruleName: 'ciso-copilot-bedrock-daily-rollup',
  schedule: events.Schedule.cron({ minute: '5', hour: '0' }),  // 00:05 UTC
  targets: [new targets.LambdaFunction(eventRouterFn, {
    event: events.RuleTargetInput.fromObject({
      'detail-type': 'shasta.scheduled.bedrock_daily_rollup',
    }),
  })],
});
```

- [ ] **Step 2: Add scheduled handler branch in `event_router/main.py`**

```python
def _handle_daily_rollup(event: dict) -> dict:
    """Emit aws_bedrock_invoke_high_volume for rollups > threshold."""
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "SELECT tenant_id::text, attributes->>'principal', attributes->>'model_id', "
            "       (attributes->>'invocation_count')::int "
            "FROM entities "
            "WHERE kind = 'bedrock_invocation' "
            "  AND attributes->>'day' = :day"
        ),
        parameters=[{"name": "day", "value": {"stringValue": yesterday}}],
    )
    emitted = 0
    for r in rs.get("records", []):
        tenant_id, principal, model_id, count = (
            r[0].get("stringValue"), r[1].get("stringValue"),
            r[2].get("stringValue"), int(r[3].get("longValue", 0)),
        )
        threshold = _high_volume_threshold(tenant_id)  # default 10_000
        if count > threshold:
            _emit_finding(
                tenant_id=tenant_id,
                check_id="aws_bedrock_invoke_high_volume",
                severity="medium", status="fail",
                evidence_packet={
                    "principal": principal, "model_id": model_id,
                    "invocations": count, "threshold": threshold, "day": yesterday,
                },
                frameworks={
                    "nist_ai_rmf": ["MEASURE 2.3", "MANAGE 2.2"],
                    "owasp_llm_top10": ["LLM10:2025"],
                },
            )
            emitted += 1
    return {"status": "ok", "emitted": emitted, "day": yesterday}


def handler(event: dict, context) -> dict:
    if event.get("detail-type") == "shasta.scheduled.bedrock_daily_rollup":
        return _handle_daily_rollup(event)
    if _is_bedrock_event(event):
        return _handle_bedrock(event)
    # ... existing routing
```

- [ ] **Step 3: Add test for daily rollup**

```python
def test_daily_rollup_emits_high_volume_finding_above_threshold(mock_rds):
    mock_rds.execute_statement.side_effect = [
        {"records": [[
            {"stringValue": "t-1"},
            {"stringValue": "arn:aws:iam::111:role/Heavy"},
            {"stringValue": "anthropic.claude-3-opus"},
            {"longValue": 15000},  # > 10_000 threshold
        ]]},
        {"records": [[{"stringValue": "f-1"}]]},  # finding insert
    ]
    import main
    resp = main.handler({"detail-type": "shasta.scheduled.bedrock_daily_rollup"}, None)
    assert resp["emitted"] == 1
```

- [ ] **Step 4: Commit**

```bash
git add platform/lib/scan-stack.ts platform/lambda/event_router/main.py platform/lambda/event_router/tests/test_bedrock.py
git commit -m "feat(event-router): daily rollup emits high_volume Bedrock finding"
```

### Task 1.3.5: cdk deploy + ship PR 1.3

- [ ] **Step 1: Deploy**

```bash
cd platform && npx cdk deploy CisoCopilotScan --require-approval never --hotswap
```

- [ ] **Step 2: Smoke**

Trigger Bedrock from any AWS account already connected:
```bash
aws bedrock-runtime invoke-model --model-id anthropic.claude-3-haiku-20240307-v1:0 \
  --body '{"anthropic_version":"bedrock-2023-05-31","max_tokens":10,"messages":[{"role":"user","content":"hi"}]}' \
  --content-type application/json /tmp/out.json
```

Within 60s, check Aurora:
```bash
aws rds-data execute-statement --resource-arn $DB_CLUSTER_ARN \
  --secret-arn $DB_SECRET_ARN --database ciso_copilot \
  --sql "SELECT kind, name FROM entities WHERE kind LIKE 'bedrock%' ORDER BY created_at DESC LIMIT 5"
```
Expected: at least one `bedrock_model` row.

- [ ] **Step 3: PR**

```bash
gh pr create --title "feat(bedrock): runtime detector via event_router + daily rollup" \
  --body "$(cat <<'EOF'
## Summary
- EventBridge rule routes Bedrock CloudTrail events (`InvokeModel`, `Converse`, `InvokeAgent`, etc.) to existing `event_router`
- Per-event: upserts `bedrock_model` + `iam_principal --uses-> bedrock_model` edge + daily `bedrock_invocation` rollup row
- Per-event detectors: `aws_bedrock_invoke_unsanctioned`, `aws_bedrock_model_inventory`
- Daily 00:05 UTC schedule: `aws_bedrock_invoke_high_volume` for rollups > threshold (default 10k/day)
- Cross-region detector deferred to Slice 2 (no concrete heuristic yet)

Sub-slice 1.3 of the AI Security Slice 1 plan.
EOF
)"
```

---

## Sub-slice 1.4 — Google Workspace scanner (the heavy lift)

Largest sub-slice. May break into 1.4a (OAuth + schema) and 1.4b (scanner + detectors) if review burden gets heavy.

### Task 1.4.0: KICK OFF GOOGLE VERIFICATION (calendar, not code)

- [ ] **Step 1: Register OAuth client in Google Cloud Console**

KK action: in the existing Shasta GCP project (or a new dedicated project), create OAuth 2.0 Client ID of type Web Application. Authorized redirect URI: `https://api.shasta.io/v1/connectors/callback/google_workspace`. Note client ID + secret.

- [ ] **Step 2: Configure OAuth consent screen + submit for verification**

KK action: in the consent screen config, request scopes:
- `https://www.googleapis.com/auth/admin.reports.audit.readonly`
- `https://www.googleapis.com/auth/admin.directory.user.readonly`
- `https://www.googleapis.com/auth/admin.directory.domain.readonly`

All three are restricted scopes — submit for Google's verification queue. Typical timeline: 2-4 weeks.

- [ ] **Step 3: Put credentials in SSM**

```bash
aws ssm put-parameter --name /cisocopilot/connectors/google_workspace/client-id \
  --type SecureString --value "<client_id>" --overwrite
aws ssm put-parameter --name /cisocopilot/connectors/google_workspace/client-secret \
  --type SecureString --value "<client_secret>" --overwrite
```

(Dev/test client used while verification is pending; flip to verified-prod client when approved.)

### Task 1.4.1: SQL migration 016 — `tenant_workspace_oauth`

**Files:**
- Create: `platform/sql/016_workspace_connector.sql`

- [ ] **Step 1: Write migration**

```sql
CREATE TABLE tenant_workspace_oauth (
    tenant_id              UUID         NOT NULL REFERENCES tenants(tenant_id),
    workspace_domain       TEXT         NOT NULL,
    super_admin_email      TEXT         NOT NULL,
    access_token_enc       BYTEA,
    access_data_key_ct     BYTEA,
    access_expires_at      TIMESTAMPTZ,
    refresh_token_enc      BYTEA,
    refresh_data_key_ct    BYTEA,
    scopes                 TEXT[]       NOT NULL DEFAULT '{}',
    installed_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    revoked_at             TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, workspace_domain)
);

CREATE INDEX idx_tenant_workspace_oauth_active
  ON tenant_workspace_oauth (tenant_id)
  WHERE revoked_at IS NULL;
```

- [ ] **Step 2: Apply via Data API**

```bash
set -a && . platform/.env && set +a
aws rds-data execute-statement --resource-arn "$DB_CLUSTER_ARN" \
  --secret-arn "$DB_SECRET_ARN" --database ciso_copilot \
  --sql "$(cat platform/sql/016_workspace_connector.sql)"
```
Expected: `numberOfRecordsUpdated: 0` (DDL has no row count).

- [ ] **Step 3: Verify**

```bash
aws rds-data execute-statement --resource-arn "$DB_CLUSTER_ARN" \
  --secret-arn "$DB_SECRET_ARN" --database ciso_copilot \
  --sql "SELECT column_name FROM information_schema.columns WHERE table_name = 'tenant_workspace_oauth' ORDER BY ordinal_position"
```
Expected: 11 columns in declared order.

- [ ] **Step 4: Commit**

```bash
git add platform/sql/016_workspace_connector.sql
git commit -m "feat(sql): migration 016 — tenant_workspace_oauth table"
```

### Task 1.4.2: Google Workspace OAuth provider config

**Files:**
- Create: `platform/lambda/_shared/mcp_oauth/providers/google_workspace.py`
- Create: `platform/lambda/_shared/mcp_oauth/providers/tests/test_google_workspace.py`

- [ ] **Step 1: Write the failing test**

```python
"""Google Workspace OAuth — build authorize URL with PKCE + state."""
import urllib.parse


def test_build_authorize_url_includes_pkce_and_state():
    from _shared.mcp_oauth.providers.google_workspace import build_authorize_url
    url = build_authorize_url(
        client_id="cid", redirect_uri="https://api.shasta.io/cb",
        state="signed.state.jwt", code_challenge="abc123",
    )
    parts = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parts.query)
    assert parts.netloc == "accounts.google.com"
    assert "/o/oauth2/v2/auth" in parts.path
    assert qs["client_id"] == ["cid"]
    assert qs["state"] == ["signed.state.jwt"]
    assert qs["code_challenge"] == ["abc123"]
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["access_type"] == ["offline"]  # required for refresh_token
    assert qs["prompt"] == ["consent"]       # force refresh_token even on re-grant
    assert "admin.reports.audit.readonly" in qs["scope"][0]
```

- [ ] **Step 2: Run to verify failure**

```bash
cd platform/lambda && python -m pytest _shared/mcp_oauth/providers/tests/test_google_workspace.py -v
```
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement provider**

```python
"""Google Workspace OAuth provider config.

Restricted scopes — requires Google verification before production use.
"""
from __future__ import annotations
import urllib.parse

ADMIN_SCOPES = " ".join([
    "https://www.googleapis.com/auth/admin.reports.audit.readonly",
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
    "https://www.googleapis.com/auth/admin.directory.domain.readonly",
])

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL     = "https://oauth2.googleapis.com/token"


def build_authorize_url(*, client_id: str, redirect_uri: str, state: str,
                         code_challenge: str, scope: str = ADMIN_SCOPES) -> str:
    params = {
        "client_id":             client_id,
        "redirect_uri":          redirect_uri,
        "response_type":         "code",
        "scope":                 scope,
        "state":                 state,
        "code_challenge":        code_challenge,
        "code_challenge_method": "S256",
        "access_type":           "offline",  # for refresh_token
        "prompt":                "consent",  # force fresh consent → guarantees refresh_token
        "include_granted_scopes": "true",
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(*, client_id: str, client_secret: str, code: str,
                   redirect_uri: str, code_verifier: str) -> dict:
    """POST to TOKEN_URL. Returns dict with access_token, refresh_token, expires_in."""
    import requests
    r = requests.post(TOKEN_URL, data={
        "client_id":     client_id,
        "client_secret": client_secret,
        "code":          code,
        "redirect_uri":  redirect_uri,
        "code_verifier": code_verifier,
        "grant_type":    "authorization_code",
    }, timeout=10)
    r.raise_for_status()
    return r.json()


def refresh_access_token(*, client_id: str, client_secret: str,
                          refresh_token: str) -> dict:
    """POST to TOKEN_URL for token refresh. Returns dict with access_token, expires_in.
    Note: Google's response does NOT include a new refresh_token; refresh tokens are
    long-lived (no rotation by default). The caller writes back only access_token + expires_at."""
    import requests
    r = requests.post(TOKEN_URL, data={
        "client_id":     client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type":    "refresh_token",
    }, timeout=10)
    r.raise_for_status()
    return r.json()
```

- [ ] **Step 4: Run + commit**

```bash
cd platform/lambda && python -m pytest _shared/mcp_oauth/providers/tests/test_google_workspace.py -v
git add platform/lambda/_shared/mcp_oauth/providers/google_workspace.py \
        platform/lambda/_shared/mcp_oauth/providers/tests/
git commit -m "feat(mcp-oauth): Google Workspace OAuth provider config"
```

### Task 1.4.3: Connectors handlers — initiate + callback

**Files:**
- Create: `platform/lambda/connectors/handlers_google_workspace.py`
- Create: `platform/lambda/connectors/tests/test_handlers_google_workspace.py`
- Modify: `platform/lambda/connectors/main.py` (route registry)

(This task mirrors the existing `handlers_slack.py` + tests pattern — read those first as templates.)

- [ ] **Step 1: Read the Slack handler template**

```bash
wc -l platform/lambda/connectors/handlers_slack.py
head -60 platform/lambda/connectors/handlers_slack.py
```

- [ ] **Step 2: Write the failing test**

`platform/lambda/connectors/tests/test_handlers_google_workspace.py`:
```python
"""Workspace OAuth flow handlers — initiate + callback."""
from unittest.mock import MagicMock, patch
import pytest


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("STATE_JWT_SECRET", "s" * 32)
    monkeypatch.setenv("GOOGLE_WS_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_WS_CLIENT_SECRET", "csec")
    monkeypatch.setenv("CALLBACK_BASE_URL", "https://api.shasta.io/v1/connectors")


def test_initiate_returns_302_with_authorize_url(env, monkeypatch):
    from connectors import handlers_google_workspace as h
    fake_pkce = MagicMock()
    fake_pkce.store_verifier.return_value = None
    monkeypatch.setattr(h, "pkce_store", fake_pkce)
    event = {
        "requestContext": {"authorizer": {"claims": {"sub": "subject-1"}}},
    }
    resp = h.initiate(event, None)
    assert resp["statusCode"] == 302
    loc = resp["headers"]["Location"]
    assert "accounts.google.com/o/oauth2/v2/auth" in loc
    assert "scope=" in loc and "code_challenge=" in loc


def test_callback_persists_tokens_and_redirects(env, monkeypatch):
    from connectors import handlers_google_workspace as h
    # Mock all the deps: state verify, PKCE consume, token exchange, KMS, RDS
    monkeypatch.setattr(h, "verify_state", lambda token, expected_provider: {
        "subject": "subject-1", "nonce": "n", "csrf_token_hash": "x",
    })
    fake_pkce = MagicMock()
    fake_pkce.fetch_verifier.return_value = "verifier"
    monkeypatch.setattr(h, "pkce_store", fake_pkce)
    monkeypatch.setattr(h, "exchange_code", lambda **kw: {
        "access_token": "at", "refresh_token": "rt",
        "expires_in": 3600,
        "id_token": "<jwt-with-hd-claim>",  # for super_admin_email + workspace_domain
    })
    fake_crypto = MagicMock()
    fake_crypto.encrypt_token.return_value = (b"ct", b"dk")
    monkeypatch.setattr(h, "crypto", fake_crypto)
    fake_rds = MagicMock()
    fake_rds.execute_statement.return_value = {"records": []}
    monkeypatch.setattr(h, "rds_data", fake_rds)
    monkeypatch.setattr(h, "_resolve_user_context", lambda event: {"user_id": "u-1", "tenant_id": "t-1"})
    monkeypatch.setattr(h, "_parse_id_token", lambda token: {"email": "admin@kkmookhey.com", "hd": "kkmookhey.com"})

    event = {
        "queryStringParameters": {
            "code": "auth-code", "state": "signed.state.jwt",
        },
    }
    resp = h.callback(event, None)
    assert resp["statusCode"] == 302
    assert "?ok=google_workspace" in resp["headers"]["Location"]
    # Must have INSERTed tokens
    sqls = [c.kwargs["sql"] for c in fake_rds.execute_statement.call_args_list]
    assert any("INSERT INTO tenant_workspace_oauth" in s for s in sqls)
```

- [ ] **Step 3: Run to fail**

```bash
cd platform/lambda && python -m pytest connectors/tests/test_handlers_google_workspace.py -v
```
Expected: FAIL.

- [ ] **Step 4: Implement handlers**

`platform/lambda/connectors/handlers_google_workspace.py`:
```python
"""Workspace OAuth flow — initiate + callback handlers."""
from __future__ import annotations

import base64
import json
import os
import secrets

from _shared.mcp_oauth import crypto, pkce as pkce_store
from _shared.mcp_oauth.providers.google_workspace import (
    build_authorize_url, exchange_code,
)
from _shared.mcp_oauth.state import sign_state, verify_state
from .handlers_common import _resolve_user_context, _ssm_get, _resp_redirect, _resp_json

# Env populated by CDK
STATE_JWT_SECRET     = os.environ["STATE_JWT_SECRET"]
CALLBACK_BASE_URL    = os.environ["CALLBACK_BASE_URL"]
WEB_BASE_URL         = os.environ["WEB_BASE_URL"]
DB_CLUSTER_ARN       = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN        = os.environ["DB_SECRET_ARN"]
DB_NAME              = os.environ["DB_NAME"]

# SSM-stored OAuth client credentials
GOOGLE_WS_CLIENT_ID     = _ssm_get("/cisocopilot/connectors/google_workspace/client-id")
GOOGLE_WS_CLIENT_SECRET = _ssm_get("/cisocopilot/connectors/google_workspace/client-secret")

import boto3
rds_data = boto3.client("rds-data")


def initiate(event: dict, context) -> dict:
    ctx = _resolve_user_context(event)
    if not ctx:
        return _resp_json(401, {"error": "no_user"})

    verifier, challenge = pkce_store.generate_pair()
    nonce = secrets.token_urlsafe(16)
    state = sign_state(
        secret=STATE_JWT_SECRET, provider="google_workspace",
        subject=ctx["user_id"], nonce=nonce,
        csrf_token_hash="",  # cookie retired per MCP Slice 1 review
    )
    pkce_store.store_verifier(nonce=nonce, verifier=verifier, ttl_seconds=600)

    url = build_authorize_url(
        client_id=GOOGLE_WS_CLIENT_ID,
        redirect_uri=f"{CALLBACK_BASE_URL}/callback/google_workspace",
        state=state, code_challenge=challenge,
    )
    return _resp_redirect(url)


def callback(event: dict, context) -> dict:
    qs = event.get("queryStringParameters") or {}
    code = qs.get("code")
    state_jwt = qs.get("state")
    if not code or not state_jwt:
        return _resp_json(400, {"error": "missing_code_or_state"})

    state = verify_state(state_jwt, secret=STATE_JWT_SECRET,
                          expected_provider="google_workspace")
    if not state:
        return _resp_json(400, {"error": "invalid_state"})

    verifier = pkce_store.fetch_verifier(nonce=state["nonce"])
    if not verifier:
        return _resp_json(400, {"error": "verifier_not_found_or_expired"})

    tokens = exchange_code(
        client_id=GOOGLE_WS_CLIENT_ID, client_secret=GOOGLE_WS_CLIENT_SECRET,
        code=code,
        redirect_uri=f"{CALLBACK_BASE_URL}/callback/google_workspace",
        code_verifier=verifier,
    )
    # Extract workspace_domain (the `hd` claim) + super_admin_email from id_token
    id_claims = _parse_id_token(tokens["id_token"])
    workspace_domain  = id_claims["hd"]
    super_admin_email = id_claims["email"]

    ctx = _resolve_user_context(event)
    if not ctx:
        return _resp_json(401, {"error": "no_user"})
    tenant_id = ctx["tenant_id"]

    # KMS-encrypt both tokens
    access_ct, access_dk  = crypto.encrypt_token(tokens["access_token"])
    refresh_ct, refresh_dk = crypto.encrypt_token(tokens["refresh_token"])
    expires_in = int(tokens["expires_in"])

    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "INSERT INTO tenant_workspace_oauth ("
            "  tenant_id, workspace_domain, super_admin_email,"
            "  access_token_enc, access_data_key_ct, access_expires_at,"
            "  refresh_token_enc, refresh_data_key_ct,"
            "  scopes, installed_at"
            ") VALUES ("
            "  CAST(:tid AS UUID), :dom, :email,"
            "  :act, :adk, NOW() + INTERVAL '1 second' * :exp,"
            "  :rct, :rdk,"
            "  ARRAY['admin.reports.audit.readonly','admin.directory.user.readonly','admin.directory.domain.readonly'],"
            "  NOW()"
            ") "
            "ON CONFLICT (tenant_id, workspace_domain) DO UPDATE SET "
            "  super_admin_email   = EXCLUDED.super_admin_email,"
            "  access_token_enc    = EXCLUDED.access_token_enc,"
            "  access_data_key_ct  = EXCLUDED.access_data_key_ct,"
            "  access_expires_at   = EXCLUDED.access_expires_at,"
            "  refresh_token_enc   = EXCLUDED.refresh_token_enc,"
            "  refresh_data_key_ct = EXCLUDED.refresh_data_key_ct,"
            "  scopes              = EXCLUDED.scopes,"
            "  installed_at        = NOW(),"
            "  revoked_at          = NULL"
        ),
        parameters=[
            {"name": "tid",  "value": {"stringValue": tenant_id}},
            {"name": "dom",  "value": {"stringValue": workspace_domain}},
            {"name": "email","value": {"stringValue": super_admin_email}},
            {"name": "act",  "value": {"blobValue":   access_ct}},
            {"name": "adk",  "value": {"blobValue":   access_dk}},
            {"name": "exp",  "value": {"longValue":   expires_in}},
            {"name": "rct",  "value": {"blobValue":   refresh_ct}},
            {"name": "rdk",  "value": {"blobValue":   refresh_dk}},
        ],
    )

    return _resp_redirect(f"{WEB_BASE_URL}/connect?ok=google_workspace")


def _parse_id_token(id_token: str) -> dict:
    """Parse JWT payload (NO signature verification — Google's response is over TLS to a known endpoint).
    Returns {email, hd, sub, ...}."""
    payload_b64 = id_token.split(".")[1]
    payload_b64 += "=" * (4 - len(payload_b64) % 4)  # padding
    return json.loads(base64.urlsafe_b64decode(payload_b64))
```

- [ ] **Step 5: Wire routes in `connectors/main.py`**

Add to the route registry:
```python
from .handlers_google_workspace import initiate as gws_initiate, callback as gws_callback

ROUTES = {
    # ... existing slack routes
    ("POST", "/v1/connectors/connect/google_workspace"):  gws_initiate,
    ("GET",  "/v1/connectors/callback/google_workspace"): gws_callback,
}
```

**Step 5a: Preflight — CFN resource cap check before adding routes via CDK**

Per `docs/codebase/FINDINGS.md` cross-cutting + the MCP Slice 2 hotfix
(PR #41), the `CisoCopilotApi` stack has hit the **500-resource-per-stack
CloudFormation cap** before — admin connector routes had to move out to a
bootstrap script. Each new API Gateway method adds ~5-8 resources (Method
+ Permission + Deployment ref + ...). Two new routes = ~10-16 resources.

Run:
```bash
cd platform && npm install --silent 2>/dev/null  # if needed
npx cdk synth CisoCopilotApi 2>/dev/null | grep -c '"Type":'
```

Compare against the 500 cap. **If current count + 16 > 495 (5-resource
safety margin):** do NOT add the 2 routes via CDK. Instead, follow the
PR #41 pattern in `platform/scripts/bootstrap-admin-routes.sh` — wire
the routes via the out-of-CDK bootstrap script that uses
`aws apigateway create-resource` + `create-method` + `put-integration`
directly. The route still terminates at the `connectors/main.py`
handler (same registry entry).

**If current count + 16 ≤ 495:** proceed with the normal CDK route
addition in api-stack.ts (template in MCP Slice 1's PR #34 — find by
`grep -n "connect/slack\|callback/slack" platform/lib/api-stack.ts`).

- [ ] **Step 6: Run + commit**

```bash
cd platform/lambda && python -m pytest connectors/tests/test_handlers_google_workspace.py -v
git add platform/lambda/connectors/handlers_google_workspace.py \
        platform/lambda/connectors/tests/test_handlers_google_workspace.py \
        platform/lambda/connectors/main.py
git commit -m "feat(connectors): Google Workspace OAuth initiate + callback handlers"
```

### Task 1.4.4: Workspace scanner — Fargate scaffold

**Files:**
- Create: `platform/lambda/shasta_runner_workspace/Dockerfile`
- Create: `platform/lambda/shasta_runner_workspace/build.sh`
- Create: `platform/lambda/shasta_runner_workspace/app/main.py`
- Create: `platform/lambda/shasta_runner_workspace/app/google_client.py`
- Create: `platform/lambda/shasta_runner_workspace/app/requirements.txt`
- Modify: `platform/lib/scan-stack.ts` — ECR repo + Fargate task def + IAM

(This task mirrors `shasta_runner_azure/` — read it first as the template.)

- [ ] **Step 1: Read Azure scanner template**

```bash
ls platform/lambda/shasta_runner_azure/
cat platform/lambda/shasta_runner_azure/Dockerfile
cat platform/lambda/shasta_runner_azure/build.sh
head -50 platform/lambda/shasta_runner_azure/app/main.py
```

- [ ] **Step 2: Write `Dockerfile`**

```dockerfile
FROM public.ecr.aws/lambda/python:3.12

# Tools (none beyond python3.12 base for now)
COPY app/requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Stage shared modules from sibling Lambdas
COPY _shared              /var/task/_shared
COPY scanner_core         /var/task/scanner_core
COPY ai_scanner/unified_writer.py /var/task/unified_writer.py

# Stage Workspace scanner code
COPY app                  /var/task/

ENV PYTHONPATH=/var/task

ENTRYPOINT ["python", "-m", "main"]
```

- [ ] **Step 3: Write `build.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

REGION="${AWS_REGION:-us-east-1}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REPO="ciso-copilot-shasta-runner-workspace"
ECR="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"

aws ecr get-login-password --region "$REGION" | \
  docker login --username AWS --password-stdin "$ECR"

# Stage shared dirs alongside Dockerfile
rm -rf _shared scanner_core ai_scanner
cp -r ../_shared              _shared
cp -r ../scanner_core         scanner_core
mkdir -p ai_scanner && cp ../ai_scanner/unified_writer.py ai_scanner/unified_writer.py

docker build --platform linux/amd64 -t "$REPO:latest" .
docker tag "$REPO:latest" "$ECR/$REPO:latest"
docker push "$ECR/$REPO:latest"

DIGEST="$(aws ecr describe-images --repository-name "$REPO" \
  --image-ids imageTag=latest --query 'imageDetails[0].imageDigest' --output text)"
echo "Pushed ${ECR}/${REPO}@${DIGEST}"

# Cleanup staged copies
rm -rf _shared scanner_core ai_scanner
```

`chmod +x build.sh`.

- [ ] **Step 4: Write `requirements.txt`**

```
google-api-python-client>=2.130
google-auth>=2.30
google-auth-httplib2>=0.2
boto3
```

- [ ] **Step 5: Write `app/main.py` skeleton**

```python
"""shasta_runner_workspace — Google Workspace audit-log + directory scanner.

Fargate task entrypoint. Env vars:
  - TENANT_ID, WORKSPACE_DOMAIN, SCAN_ID (passed via containerOverrides)
  - DB_CLUSTER_ARN, DB_SECRET_ARN, DB_NAME (from task def)
  - AUTONOMOUS_BROADCAST_QUEUE_URL (from task def, like Azure/GCP)
"""
from __future__ import annotations

import os
import sys

import boto3

from google_client import build_workspace_service
from detectors.gws_ai_signin_personal_tier import detect as detect_signin
from detectors.gws_ai_oauth_grant          import detect as detect_oauth
from detectors.gws_drive_shared_to_ai_domain import detect as detect_drive
from detectors.gws_gemini_assigned         import detect as detect_gemini

from unified_writer import UnifiedWriter

TENANT_ID         = os.environ["TENANT_ID"]
WORKSPACE_DOMAIN  = os.environ["WORKSPACE_DOMAIN"]
SCAN_ID           = os.environ["SCAN_ID"]


def run():
    rds_data = boto3.client("rds-data")
    services = build_workspace_service(TENANT_ID, WORKSPACE_DOMAIN, rds_data)
    writer = UnifiedWriter(rds_data=rds_data, tenant_id=TENANT_ID, scan_id=SCAN_ID)

    for detect in (detect_signin, detect_oauth, detect_drive, detect_gemini):
        for emission in detect(services, tenant_id=TENANT_ID, scan_id=SCAN_ID):
            writer.write(emission)

    writer.commit()
    print(f"workspace scan complete: tenant={TENANT_ID} domain={WORKSPACE_DOMAIN}")


if __name__ == "__main__":
    sys.exit(run() or 0)
```

- [ ] **Step 6: Write `app/google_client.py`**

```python
"""Build authenticated Google Workspace SDK clients for a tenant.

Loads encrypted refresh token from tenant_workspace_oauth, JIT-refreshes
the access token, returns ready-to-use service objects.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from _shared.mcp_oauth import crypto
from _shared.mcp_oauth.providers.google_workspace import refresh_access_token

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

GOOGLE_WS_CLIENT_ID     = os.environ["GOOGLE_WS_CLIENT_ID"]
GOOGLE_WS_CLIENT_SECRET = os.environ["GOOGLE_WS_CLIENT_SECRET"]


def build_workspace_service(tenant_id: str, workspace_domain: str, rds_data):
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "SELECT access_token_enc, access_data_key_ct, access_expires_at, "
            "       refresh_token_enc, refresh_data_key_ct, super_admin_email "
            "FROM tenant_workspace_oauth "
            "WHERE tenant_id = CAST(:tid AS UUID) "
            "  AND workspace_domain = :dom "
            "  AND revoked_at IS NULL"
        ),
        parameters=[
            {"name": "tid", "value": {"stringValue": tenant_id}},
            {"name": "dom", "value": {"stringValue": workspace_domain}},
        ],
    )
    records = rs.get("records", [])
    if not records:
        raise RuntimeError(f"No Workspace OAuth row for {tenant_id} / {workspace_domain}")
    r = records[0]
    access_ct = r[0].get("blobValue")
    access_dk = r[1].get("blobValue")
    expires_at = r[2].get("stringValue")
    refresh_ct = r[3].get("blobValue")
    refresh_dk = r[4].get("blobValue")
    super_admin = r[5].get("stringValue")

    # JIT refresh if access token expired or near expiry (< 5 min)
    now = datetime.now(timezone.utc)
    expires_dt = datetime.fromisoformat(expires_at.replace(" ", "T")).replace(tzinfo=timezone.utc)
    if (expires_dt - now).total_seconds() < 300:
        refresh_token = crypto.decrypt_token(refresh_ct, refresh_dk)
        fresh = refresh_access_token(
            client_id=GOOGLE_WS_CLIENT_ID,
            client_secret=GOOGLE_WS_CLIENT_SECRET,
            refresh_token=refresh_token,
        )
        access_token = fresh["access_token"]
        # Write back the new access token + expires_at
        new_ct, new_dk = crypto.encrypt_token(access_token)
        rds_data.execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql=(
                "UPDATE tenant_workspace_oauth "
                "SET access_token_enc = :ct, access_data_key_ct = :dk, "
                "    access_expires_at = NOW() + INTERVAL '1 second' * :exp "
                "WHERE tenant_id = CAST(:tid AS UUID) AND workspace_domain = :dom"
            ),
            parameters=[
                {"name": "ct",  "value": {"blobValue":   new_ct}},
                {"name": "dk",  "value": {"blobValue":   new_dk}},
                {"name": "exp", "value": {"longValue":   int(fresh["expires_in"])}},
                {"name": "tid", "value": {"stringValue": tenant_id}},
                {"name": "dom", "value": {"stringValue": workspace_domain}},
            ],
        )
    else:
        access_token = crypto.decrypt_token(access_ct, access_dk)

    creds = Credentials(token=access_token)

    return {
        "reports":   build("admin", "reports_v1",   credentials=creds, cache_discovery=False),
        "directory": build("admin", "directory_v1", credentials=creds, cache_discovery=False),
        "super_admin_email": super_admin,
    }
```

- [ ] **Step 7: Commit scaffold**

```bash
git add platform/lambda/shasta_runner_workspace/
git commit -m "feat(workspace-scanner): Fargate scaffold + google_client with JIT refresh"
```

### Task 1.4.5: Detector — `gws_ai_signin_personal_tier`

**Files:**
- Create: `platform/lambda/shasta_runner_workspace/app/detectors/__init__.py`
- Create: `platform/lambda/shasta_runner_workspace/app/detectors/gws_ai_signin_personal_tier.py`
- Create: `platform/lambda/shasta_runner_workspace/app/tests/conftest.py`
- Create: `platform/lambda/shasta_runner_workspace/app/tests/test_gws_ai_signin_personal_tier.py`

- [ ] **Step 1: Write failing test**

```python
"""gws_ai_signin_personal_tier — login audit shows employee using personal
Google account to sign into a known AI SaaS (chatgpt.com / claude.ai / etc.)."""
from unittest.mock import MagicMock


def test_emits_finding_for_personal_chatgpt_signin():
    from detectors.gws_ai_signin_personal_tier import detect

    fake_reports = MagicMock()
    fake_reports.activities().list().execute.return_value = {
        "items": [
            {
                "id":    {"time": "2026-06-05T10:00:00Z", "uniqueQualifier": "x"},
                "actor": {"email": "employee@kkmookhey.com",
                          "profileId": "p-1"},
                "events": [{"name": "login_success",
                            "parameters": [
                                {"name": "login_type",     "value": "saml"},
                                {"name": "client_app_name", "value": "chatgpt.com"},
                                {"name": "is_third_party_id_provider", "boolValue": True},
                            ]}],
            }
        ]
    }
    services = {"reports": fake_reports, "directory": MagicMock()}
    emissions = list(detect(services, tenant_id="t-1", scan_id="s-1"))
    # Expect at least one FindingEmission
    findings = [e for e in emissions if e.kind == "finding"]
    assert len(findings) >= 1
    f = findings[0]
    assert f.check_id == "gws_ai_signin_personal_tier"
    assert f.severity == "high"
    assert "chatgpt" in f.evidence_packet.get("client_app_name", "").lower()
```

- [ ] **Step 2: Implement detector**

```python
"""gws_ai_signin_personal_tier — detect Workspace users signing into known
external AI apps via a third-party identity provider (i.e. personal Google
account, not the workspace SSO).
"""
from __future__ import annotations

from typing import Iterator

from detectors.base import FindingEmission, EntityEmission

# Seeded catalog (mirrors Entra's ai_saas_catalog.json — domains/clients we
# treat as known consumer AI SaaS).
_AI_SAAS_DOMAINS = frozenset({
    "chatgpt.com", "openai.com",
    "claude.ai", "anthropic.com",
    "perplexity.ai",
    "gemini.google.com",
    "copilot.microsoft.com",
    "huggingface.co",
    "replicate.com",
    "midjourney.com",
})


def detect(services: dict, *, tenant_id: str, scan_id: str) -> Iterator:
    reports = services["reports"]
    page_token = None
    while True:
        kwargs = dict(
            userKey="all", applicationName="login",
            maxResults=1000,
        )
        if page_token:
            kwargs["pageToken"] = page_token
        resp = reports.activities().list(**kwargs).execute()
        for activity in resp.get("items", []):
            for ev in activity.get("events", []):
                params = {p["name"]: p.get("value") or p.get("boolValue")
                          for p in ev.get("parameters", [])}
                client_app = (params.get("client_app_name") or "").lower()
                is_third_party_idp = bool(params.get("is_third_party_id_provider"))
                if not is_third_party_idp:
                    continue
                if not any(d in client_app for d in _AI_SAAS_DOMAINS):
                    continue
                yield FindingEmission(
                    check_id="gws_ai_signin_personal_tier",
                    severity="high",
                    status="fail",
                    natural_key=f"gws_signin::{activity['id']['uniqueQualifier']}",
                    evidence_packet={
                        "actor_email":     activity["actor"].get("email"),
                        "client_app_name": client_app,
                        "event_time":      activity["id"]["time"],
                    },
                    frameworks={
                        "nist_ai_rmf":      ["GOVERN 3.2", "GOVERN 6.1"],
                        "nist_ai_600_1":    ["NIST.AI.600-1:2.4", "NIST.AI.600-1:2.8"],
                        "eu_ai_act":        ["Article 9", "Article 26"],
                        "owasp_llm_top10":  ["LLM02:2025"],
                        "mitre_atlas":      ["AML.T0057"],
                    },
                )
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
```

- [ ] **Step 3: Run + commit**

```bash
cd platform/lambda/shasta_runner_workspace/app && python -m pytest tests/ -v
git add platform/lambda/shasta_runner_workspace/app/detectors/ \
        platform/lambda/shasta_runner_workspace/app/tests/
git commit -m "feat(workspace-scanner): gws_ai_signin_personal_tier detector"
```

### Task 1.4.6: Detectors — oauth_grant, drive_shared, gemini_assigned

(Mirror the pattern from 1.4.5 — each detector ~50-80 lines. TDD: failing test → implement → passing test → commit, one per detector.)

- [ ] **Step 1: `gws_ai_oauth_grant`** — read Workspace `token` audit log; emit finding when scope set includes Drive/Gmail scopes for AI vendor apps. Same TDD loop. Commit.

- [ ] **Step 2: `gws_drive_shared_to_ai_domain`** — read Workspace `drive` audit log; emit finding when externally shared to `@openai.com` / `@anthropic.com` / etc. Same loop.

- [ ] **Step 3: `gws_gemini_assigned`** — read Directory API for user license assignments; emit informational `ai_model` entity (kind=`gemini`) for Gemini Pro / Duet licensees. Same loop.

- [ ] **Step 4: Build + push scanner image**

```bash
cd platform/lambda/shasta_runner_workspace && ./build.sh
```
Note the pushed image digest.

### Task 1.4.7: CDK — Workspace Fargate task + ECR + IAM

**Files:**
- Modify: `platform/lib/scan-stack.ts`

- [ ] **Step 1: Mirror Azure/GCP Fargate task def for Workspace**

```typescript
const workspaceRepo = ecr.Repository.fromRepositoryName(
  this, 'WorkspaceRunnerRepo', 'ciso-copilot-shasta-runner-workspace',
);
const workspaceTaskDef = new ecs.FargateTaskDefinition(this, 'WorkspaceTaskDef', {
  cpu: 512, memoryLimitMiB: 1024,
});
workspaceTaskDef.addContainer('scanner', {
  image: ecs.ContainerImage.fromEcrRepository(workspaceRepo),
  environment: {
    DB_CLUSTER_ARN: props.dbCluster.clusterArn,
    DB_SECRET_ARN:  props.dbCluster.secret!.secretArn,
    DB_NAME:        'ciso_copilot',
    AUTONOMOUS_BROADCAST_QUEUE_URL: props.autonomousBroadcastQueue.queueUrl,
    GOOGLE_WS_CLIENT_ID:     _ssmStringParam(this, 'GwsClientIdParam',
      '/cisocopilot/connectors/google_workspace/client-id').stringValue,
    GOOGLE_WS_CLIENT_SECRET: _ssmStringParam(this, 'GwsClientSecretParam',
      '/cisocopilot/connectors/google_workspace/client-secret').stringValue,
  },
  logging: ecs.LogDriver.awsLogs({ streamPrefix: 'shasta-runner-workspace' }),
});
props.dbCluster.grantDataApiAccess(workspaceTaskDef.taskRole);
props.autonomousBroadcastQueue.grantSendMessages(workspaceTaskDef.taskRole);
props.connectorTokensKey.grantEncryptDecrypt(workspaceTaskDef.taskRole);
```

- [ ] **Step 2: cdk deploy**

```bash
cd platform && npx cdk deploy CisoCopilotScan
```

- [ ] **Step 3: Commit**

```bash
git add platform/lib/scan-stack.ts
git commit -m "feat(cdk): Workspace Fargate task def + IAM"
```

### Task 1.4.8: `connections_list` — handle Workspace rescan trigger

**Files:**
- Modify: `platform/lambda/connections_list/main.py`

- [ ] **Step 1: Add Workspace rescan branch**

In the existing rescan handler (mirroring how AWS/Azure/GCP rescans are triggered), add a `google_workspace` case that does an `ecs:RunTask` against the new Workspace task def with `containerOverrides[].environment` carrying `TENANT_ID`, `WORKSPACE_DOMAIN`, `SCAN_ID`.

- [ ] **Step 2: Test + commit**

```bash
cd platform/lambda/connections_list && python -m pytest tests/ -v
git add platform/lambda/connections_list/main.py
git commit -m "feat(connections-list): trigger Workspace scan on Connect"
```

### Task 1.4.9: Web — ConnectClouds tile + AISummary Shadow AI row

**Files:**
- Modify: `web/src/routes/ConnectClouds.tsx`
- Modify: `web/src/routes/AISummary.tsx`
- Modify: `web/src/lib/api.ts`

- [ ] **Step 1: Add API helpers**

```typescript
// web/src/lib/api.ts
export async function initiateWorkspaceOAuth(): Promise<void> {
  window.location.href = `${API_BASE}/v1/connectors/connect/google_workspace?token=${await getAccessToken()}`;
}
export async function disconnectWorkspace(): Promise<void> {
  const token = await getAccessToken();
  await fetch(`${API_BASE}/v1/connectors/google_workspace`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${token}` },
  });
}
```

- [ ] **Step 2: Add Workspace tile to ConnectClouds.tsx**

Place next to AWS / Azure / Entra / GCP tiles. Render the same `[Connect]` / `[Disconnect]` pattern with a "Pending verification" state when `verification_pending: true` is returned by `/v1/me/connections` (a flag set when client_id is recognized as the unverified dev client).

- [ ] **Step 3: Add Shadow AI row to AISummary.tsx**

```tsx
// 3 tiles: personal-tier sign-ins, AI OAuth grants, unsanctioned Bedrock invocations
function ShadowAIRow({ counts }: { counts: { personal_signins: number; ai_oauth_grants: number; unsanctioned_bedrock: number } }) {
  return (
    <div className="grid grid-cols-3 gap-3 mb-4">
      <ShadowTile label="Personal-tier sign-ins" count={counts.personal_signins}
        to="/findings?check_id_prefix=ai_signin_personal_tier" />
      <ShadowTile label="OAuth grants to AI vendors" count={counts.ai_oauth_grants}
        to="/findings?check_id=gws_ai_oauth_grant" />
      <ShadowTile label="Unsanctioned Bedrock invocations" count={counts.unsanctioned_bedrock}
        to="/findings?check_id=aws_bedrock_invoke_unsanctioned" />
    </div>
  );
}
```

Wire `counts` from `/v1/ai/summary` (extend the existing endpoint to include these three counts; add a Lambda-side query that selects from `findings` filtered by the relevant `check_id`s).

- [ ] **Step 4: Build + deploy web + commit**

```bash
cd web && pnpm build
aws s3 sync dist/ s3://<WEB_BUCKET>/ --delete
aws cloudfront create-invalidation --distribution-id <CLOUDFRONT_DIST_ID> --paths '/*'
git add web/src/lib/api.ts web/src/routes/ConnectClouds.tsx web/src/routes/AISummary.tsx
git commit -m "feat(web): Workspace tile + Shadow AI row on /ai"
```

### Task 1.4.10: Ship PR 1.4

```bash
gh pr create --title "feat(workspace): Google Workspace shadow-AI scanner end-to-end" \
  --body "$(cat <<'EOF'
## Summary
- Google Workspace OAuth admin consent flow (`_shared/mcp_oauth/providers/google_workspace.py` + connectors handlers)
- New `tenant_workspace_oauth` table (migration 016)
- New `shasta_runner_workspace` Fargate scanner with 4 detectors:
  - `gws_ai_signin_personal_tier` — sister to Entra's personal-tier detection
  - `gws_ai_oauth_grant` — Workspace users granting OAuth scopes to AI apps
  - `gws_drive_shared_to_ai_domain` — Drive files shared to AI vendor domains
  - `gws_gemini_assigned` — Gemini license inventory
- ConnectClouds tile + Shadow AI row on `/ai`

Sub-slice 1.4 of the AI Security Slice 1 plan.

## Calendar dependency
Google verification for restricted scopes (`admin.reports.audit.readonly`,
`admin.directory.user.readonly`) is in flight; PR ships with "Pending verification"
state on the Workspace tile until approval lands.
EOF
)"
```

---

## Sub-slice 1.5 — Mapping rules + sync script + smoke

### Task 1.5.1: Add 8 new mapping rules

**Files:**
- Modify: `platform/lambda/scanner_core/ai_framework_registry.json`
- Modify: `platform/lambda/scanner_core/tests/test_framework_registry.py`

- [ ] **Step 1: Write 8 failing tests**

In `scanner_core/tests/test_framework_registry.py`:
```python
@pytest.mark.parametrize("check_id, expected_fw, expected_controls", [
    ("gws_ai_signin_personal_tier",       "nist_ai_rmf",    ["GOVERN 3.2", "GOVERN 6.1"]),
    ("gws_ai_signin_personal_tier",       "owasp_llm_top10", ["LLM02:2025"]),
    ("gws_ai_oauth_grant",                "nist_ai_rmf",    ["MAP 4.1", "MANAGE 1.3"]),
    ("gws_drive_shared_to_ai_domain",     "owasp_llm_top10", ["LLM02:2025"]),
    ("gws_gemini_assigned",               "nist_ai_rmf",    ["MAP 1.1"]),
    ("aws_bedrock_invoke_unsanctioned",   "owasp_llm_top10", ["LLM08:2025"]),
    ("aws_bedrock_invoke_high_volume",    "owasp_llm_top10", ["LLM10:2025"]),
    ("aws_bedrock_model_inventory",       "nist_ai_rmf",    ["MAP 1.1"]),
])
def test_new_check_ids_tagged(check_id, expected_fw, expected_controls):
    from framework_registry import load_registry, apply
    registry = load_registry()
    finding = {"check_id": check_id, "frameworks": {}}
    apply(finding, registry)
    assert expected_fw in finding["frameworks"]
    for c in expected_controls:
        assert c in finding["frameworks"][expected_fw]
```

- [ ] **Step 2: Run to verify failure**

```bash
cd platform/lambda/scanner_core && python -m pytest tests/ -v -k test_new_check_ids
```
Expected: 8 failures.

- [ ] **Step 3: Add the 8 rules to the registry JSON**

In `ai_framework_registry.json`'s `rules[]` array, append:
```json
{
  "id": "gws_ai_signin_personal_tier_controls",
  "when": {"check_id_eq": "gws_ai_signin_personal_tier"},
  "add_frameworks": {
    "nist_ai_rmf":      ["GOVERN 3.2", "GOVERN 6.1"],
    "nist_ai_600_1":    ["NIST.AI.600-1:2.4", "NIST.AI.600-1:2.8", "NIST.AI.600-1:2.9", "NIST.AI.600-1:2.12"],
    "eu_ai_act":        ["Article 9", "Article 26"],
    "owasp_llm_top10":  ["LLM02:2025"],
    "mitre_atlas":      ["AML.T0057"]
  }
},
{
  "id": "gws_ai_oauth_grant_controls",
  "when": {"check_id_eq": "gws_ai_oauth_grant"},
  "add_frameworks": {
    "nist_ai_rmf":      ["MAP 4.1", "MANAGE 1.3"],
    "eu_ai_act":        ["Article 26"],
    "owasp_llm_top10":  ["LLM06:2025"]
  }
}
```

… and 6 more matching the test table (control IDs subject to KK review at impl time — these are interpretation calls).

- [ ] **Step 4: Run to verify pass**

```bash
cd platform/lambda/scanner_core && python -m pytest tests/ -v
```
Expected: 8 new tests + all existing tests pass.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/scanner_core/ai_framework_registry.json \
        platform/lambda/scanner_core/tests/test_framework_registry.py
git commit -m "feat(cme): 8 new mapping rules for Workspace + Bedrock detectors"
```

### Task 1.5.2: (REMOVED — no sync script exists)

Per `docs/codebase/FINDINGS.md` §A.7: `sync_framework_map.py` is referenced
in 4 runner docstrings but does **not exist** in the repo. The "5-way
duplication" of `framework_registry.py` + `ai_framework_registry.json` is
handled by each scanner's `build.sh` doing `cp -r ../scanner_core scanner_core`
at image build time — Task 1.4.4's Dockerfile + build.sh already does this
for the Workspace scanner. So no per-runner registry edit is needed.

`framework_map.py` (the FedRAMP+PCI crosswalk) is a separate hand-mirrored
file across 4 cloud runners. Workspace doesn't need it — Workspace
findings are identity/SaaS-side and aren't tagged with FedRAMP/PCI.

**The latent drift bug (FINDINGS A.7) is out of scope for Slice 1.**
Adding the script proper would be a cross-cutting refactor. Note it in
HANDOFF as a follow-up.

### Task 1.5.3: End-to-end smoke

- [ ] **Step 1: KK installs Shasta against kkmookhey.com Workspace**

Via `/connect-clouds`. OAuth admin consent screen renders. Tokens land in `tenant_workspace_oauth`. First scan kicks off.

- [ ] **Step 2: Verify Workspace findings appear in /findings**

```bash
aws rds-data execute-statement --resource-arn $DB_CLUSTER_ARN \
  --secret-arn $DB_SECRET_ARN --database ciso_copilot \
  --sql "SELECT check_id, severity, count(*) FROM findings WHERE check_id LIKE 'gws_%' GROUP BY check_id, severity"
```
Expected: ≥1 row per emitted detector.

- [ ] **Step 3: Verify Bedrock findings land**

Trigger a Bedrock invoke (Task 1.3.5 step 2). Verify `bedrock_model` + `bedrock_invocation` entities appear.

- [ ] **Step 4: Verify framework tiles on /ai update**

Hit `/ai` in browser. NIST AI RMF + OWASP LLM Top 10 + EU AI Act + NIST AI 600-1 + ISO 42001 + MITRE ATLAS tile scores should reflect the new findings.

- [ ] **Step 5: Download + validate AI-BOM**

Click "Export AI-BOM" on `/ai`. Validate the downloaded file with `cyclonedx validate --input-file shasta-ai-bom-*.cdx.json --input-format json --input-version v1_6`. Expected: clean validation.

- [ ] **Step 6: Inject critical-fail synthetic finding for broadcast smoke**

```bash
aws rds-data execute-statement --resource-arn $DB_CLUSTER_ARN \
  --secret-arn $DB_SECRET_ARN --database ciso_copilot \
  --sql "INSERT INTO findings (tenant_id, check_id, severity, status, ...) VALUES (...)"
```
Verify Block Kit card lands in `#log-alerts` within 60s.

### Task 1.5.4: Ship PR 1.5 + close the slice

```bash
gh pr create --title "feat(cme): 8 new mapping rules + sync_framework_map workspace target" \
  --body "Closes AI Security Slice 1. End-to-end smoke documented in PR description."
```

Update `HANDOFF.md` with the new shipped state. Commit. Push.

---

## Self-review checklist

Before declaring this plan complete:
- [ ] Spec coverage: every requirement in `2026-06-04-ai-security-slice-1-design.md` §3 (In scope) has at least one task above
- [ ] No placeholders: search for "TBD" / "TODO" / "Fill in" — none found
- [ ] Type consistency: detector check_ids match across the plan (gws_*, aws_bedrock_*)
- [ ] Each sub-slice ends in a PR ship task
- [ ] TDD pattern (failing test → implement → passing test → commit) used throughout
- [ ] Aurora schema gotchas (CLAUDE.md) noted where SQL is written
