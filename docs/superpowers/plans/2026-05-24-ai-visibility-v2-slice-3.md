# AI Visibility v2 Slice 3 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a framework-registry engine that crosswalks AI and standard compliance controls onto findings at scan-commit time, expand the `/ai` view from 4 to 8 framework tiles, and author the first rule set covering S2's Entra sign-in finding kinds.

**Architecture:** New module `scanner_core/framework_registry.py` is called inside each scanner's commit path. Mappings live in `scanner_core/ai_framework_registry.json` (additive, idempotent, set-union merge). Engine ships first with `rules: []` (no behavior change); rules are added in follow-on PRs after source validation per spec §12.

**Tech Stack:** Python 3.12, AWS Lambda + Fargate, Aurora PostgreSQL (Data API), pytest, vitest (web tile expansion), boto3, fnmatch.

**Spec:** `docs/superpowers/specs/2026-05-24-ai-visibility-v2-slice-3-design.md`. Read decisions D-1 through D-8 before starting; §11 (commit-path empirical map) is binding.

---

## File Structure

**Creates:**
- `platform/lambda/scanner_core/framework_registry.py` — engine
- `platform/lambda/scanner_core/ai_framework_registry.json` — mapping authorship
- `platform/lambda/scanner_core/tests/test_framework_registry.py` — unit tests

**Modifies (write path):**
- `platform/lambda/shasta_runner/app/unified_writer.py` — add registry call in `commit_scan`
- `platform/lambda/shasta_runner_azure/app/unified_writer.py` — same
- `platform/lambda/shasta_runner_gcp/app/unified_writer.py` — same
- `platform/lambda/ai_scanner/unified_writer.py` — same
- `platform/lambda/shasta_runner_entra/app/main.py:152-185` — wrap `_insert_findings` + `_insert_finding_param_lists`
- `platform/lambda/shasta_runner_entra/build.sh` — copy `scanner_core/` into the image
- `platform/lambda/shasta_runner/build.sh` (verify already copies; add if missing)

**Modifies (read path):**
- `platform/lambda/ai_summary/main.py:41` — expand `_AI_FRAMEWORKS` from 4 to 8 keys
- `platform/lambda/ai_summary/tests/test_main.py` — 8-framework assertions
- `web/src/routes/AISummary.tsx` — 4-tile → 8-tile grid (4×2 layout)
- `web/src/routes/AISummary.test.tsx` — render assertions for new tiles

**Modifies (UI defensibility per §14.1):**
- `web/src/routes/AISummary.tsx` — add "Mapping only — not a compliance attestation" tooltip
- `web/src/routes/TopRisks.tsx` — framework-filter chip carries the same disclaimer

**Each file has one responsibility:** the registry module owns selector matching + additive merge; the JSON owns authorship; each `unified_writer.py` owns its scanner's commit; the read-side files own their existing render responsibilities (S3 only adds tile counts).

**Per-scanner commit paths verified 2026-05-24** (binding, spec §11):
- 4 sites use `commit_scan` (AWS, Azure, GCP, ai_scanner) — each carries its own copy of `unified_writer.py`
- 1 site uses direct `rds_data.batch_execute_statement` (Entra `main.py`)
- 5 hook sites total. Plan does NOT consolidate `unified_writer.py` first — duplicate hook is 4×4=16 lines, consolidation is its own slice's work with regression risk.

---

## Slice A — Engine + JSON skeleton + unit tests

Lands the registry module with `rules: []` (no behavior change). Pure code-only PR.

### Task A1: Create the empty registry JSON

**Files:**
- Create: `platform/lambda/scanner_core/ai_framework_registry.json`

- [ ] **Step 1: Create the JSON skeleton**

Write to `platform/lambda/scanner_core/ai_framework_registry.json`:

```json
{
  "frameworks": {
    "nist_ai_rmf": {
      "name": "NIST AI RMF",
      "source": "NIST AI 100-1 (2023)",
      "control_descriptions": {}
    },
    "iso_42001": {
      "name": "ISO/IEC 42001",
      "source": "ISO/IEC 42001:2023",
      "control_descriptions": {}
    },
    "soc2_ai": {
      "name": "SOC 2 + AI",
      "source": "AICPA Description Criteria for AI Systems (2024)",
      "control_descriptions": {}
    },
    "eu_ai_act": {
      "name": "EU AI Act",
      "source": "Regulation (EU) 2024/1689",
      "control_descriptions": {}
    },
    "nist_ai_600_1": {
      "name": "NIST AI 600-1",
      "source": "NIST AI 600-1 (2024)",
      "control_descriptions": {}
    },
    "owasp_llm_top10": {
      "name": "OWASP LLM Top 10",
      "source": "OWASP 2025",
      "control_descriptions": {}
    },
    "owasp_agentic": {
      "name": "OWASP Agentic",
      "source": "OWASP Agentic AI Top 10",
      "control_descriptions": {}
    },
    "mitre_atlas": {
      "name": "MITRE ATLAS",
      "source": "MITRE ATLAS v4",
      "control_descriptions": {}
    }
  },
  "rules": []
}
```

- [ ] **Step 2: Commit**

```bash
git add platform/lambda/scanner_core/ai_framework_registry.json
git commit -m "feat(s3): add framework registry JSON skeleton with 8 AI framework keys"
```

### Task A2: Write failing test for module-load validation

**Files:**
- Create: `platform/lambda/scanner_core/tests/test_framework_registry.py`

- [ ] **Step 1: Write the failing test**

Write to `platform/lambda/scanner_core/tests/test_framework_registry.py`:

```python
"""Unit tests for the framework registry engine."""
from __future__ import annotations

import json
import pytest
from pathlib import Path

from scanner_core import framework_registry as fr


def test_shipping_registry_loads_and_validates():
    """The shipping JSON must parse and pass schema validation."""
    registry = fr.load_registry()
    assert "frameworks" in registry
    assert "rules" in registry
    assert isinstance(registry["rules"], list)


def test_validate_rejects_rule_with_no_id():
    bad = {"frameworks": {}, "rules": [{"when": {"check_id_eq": "x"}, "add_frameworks": {"x": ["1"]}}]}
    with pytest.raises(fr.RegistryValidationError, match="missing 'id'"):
        fr.validate_registry(bad)


def test_validate_rejects_rule_referencing_unknown_framework():
    bad = {
        "frameworks": {"nist_ai_rmf": {"name": "x", "source": "x", "control_descriptions": {}}},
        "rules": [
            {"id": "r1", "when": {"check_id_eq": "x"}, "add_frameworks": {"made_up_fw": ["X"]}},
        ],
    }
    with pytest.raises(fr.RegistryValidationError, match="unknown framework"):
        fr.validate_registry(bad)


def test_validate_rejects_unknown_selector():
    bad = {
        "frameworks": {"nist_ai_rmf": {"name": "x", "source": "x", "control_descriptions": {}}},
        "rules": [
            {"id": "r1", "when": {"some_unknown_selector": "x"}, "add_frameworks": {"nist_ai_rmf": ["X"]}},
        ],
    }
    with pytest.raises(fr.RegistryValidationError, match="unknown selector"):
        fr.validate_registry(bad)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd platform/lambda/scanner_core
python -m pytest tests/test_framework_registry.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'scanner_core.framework_registry'`.

- [ ] **Step 3: Commit**

```bash
git add platform/lambda/scanner_core/tests/test_framework_registry.py
git commit -m "test(s3): failing tests for framework_registry loader and validator"
```

### Task A3: Implement registry loader + schema validator

**Files:**
- Create: `platform/lambda/scanner_core/framework_registry.py`

- [ ] **Step 1: Write the loader + validator**

Write to `platform/lambda/scanner_core/framework_registry.py`:

```python
"""Framework registry engine — applies compliance crosswalk to findings.

See docs/superpowers/specs/2026-05-24-ai-visibility-v2-slice-3-design.md
for design rationale.
"""
from __future__ import annotations

import fnmatch
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

_KNOWN_SELECTORS = frozenset({
    "check_id_eq",
    "check_id_glob",
    "domain",
    "resource_type_glob",
    "ai_touching",
    "evidence_packet_eq",
})

_REGISTRY_PATH = Path(__file__).parent / "ai_framework_registry.json"

# AI-touching entity kinds — mirrors ai_summary._AI_RESOURCE_KINDS.
# Used by the ai_touching selector. Kept in code so deploys carry the truth.
_AI_RESOURCE_KINDS = frozenset({
    "bedrock_model", "bedrock_guardrail", "sagemaker_endpoint",
    "sagemaker_model", "sagemaker_training_job", "comprehend_endpoint",
    "lambda_ai_function",
    "azure_openai_deployment", "azure_ml_workspace", "cognitive_service",
    "vertex_endpoint",
    "ai_saas_app", "ai_code_finding",
    "ai_user_signin", "ai_api_key", "ai_org_member", "ai_project",
    "ai_provider_org",
    "ai_agent", "ai_embedding", "ai_framework", "ai_mcp_server",
    "ai_model", "ai_prompt", "ai_tool", "ai_vector_db",
})


class RegistryValidationError(Exception):
    """Raised at module load / image build if the registry JSON is malformed."""


class RegistryApplyError(Exception):
    """Raised by apply() if a rule's selector fails. Caller wraps in try/except."""


def load_registry(path: Path | None = None) -> dict:
    """Load + validate the registry. Called once at module import."""
    target = path or _REGISTRY_PATH
    with open(target) as f:
        registry = json.load(f)
    validate_registry(registry)
    return registry


def validate_registry(registry: dict) -> None:
    """Schema validation. Raises RegistryValidationError on any defect."""
    if "frameworks" not in registry or not isinstance(registry["frameworks"], dict):
        raise RegistryValidationError("missing or invalid 'frameworks' block")
    if "rules" not in registry or not isinstance(registry["rules"], list):
        raise RegistryValidationError("missing or invalid 'rules' block")

    known_fws = set(registry["frameworks"].keys())

    for i, rule in enumerate(registry["rules"]):
        ctx = f"rule[{i}]"
        if "id" not in rule:
            raise RegistryValidationError(f"{ctx}: missing 'id'")
        if "when" not in rule or not isinstance(rule["when"], dict) or not rule["when"]:
            raise RegistryValidationError(f"rule[{rule.get('id', i)}]: 'when' must be a non-empty dict")
        if "add_frameworks" not in rule or not isinstance(rule["add_frameworks"], dict) or not rule["add_frameworks"]:
            raise RegistryValidationError(f"rule[{rule['id']}]: 'add_frameworks' must be a non-empty dict")

        unknown_sel = set(rule["when"].keys()) - _KNOWN_SELECTORS
        if unknown_sel:
            raise RegistryValidationError(
                f"rule[{rule['id']}]: unknown selector(s) {sorted(unknown_sel)}"
            )

        unknown_fw = set(rule["add_frameworks"].keys()) - known_fws
        if unknown_fw:
            raise RegistryValidationError(
                f"rule[{rule['id']}]: unknown framework(s) {sorted(unknown_fw)} in add_frameworks"
            )


# Loaded once at module import. Failures fail the Lambda cold-start.
_REGISTRY: dict = load_registry()
```

- [ ] **Step 2: Create the test package init**

Write to `platform/lambda/scanner_core/tests/__init__.py`:

```python
```

(Empty file.)

- [ ] **Step 3: Run tests to verify they pass**

```bash
cd platform/lambda/scanner_core
python -m pytest tests/test_framework_registry.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add platform/lambda/scanner_core/framework_registry.py \
        platform/lambda/scanner_core/tests/__init__.py
git commit -m "feat(s3): framework_registry loader + schema validator"
```

### Task A4: Add selector-matching tests

**Files:**
- Modify: `platform/lambda/scanner_core/tests/test_framework_registry.py` (append)

- [ ] **Step 1: Add selector tests**

Append to `platform/lambda/scanner_core/tests/test_framework_registry.py`:

```python
# --- Selector matching ---

@pytest.fixture
def simple_registry():
    return {
        "frameworks": {
            "nist_ai_rmf": {"name": "x", "source": "x", "control_descriptions": {}},
            "soc2_ai":     {"name": "x", "source": "x", "control_descriptions": {}},
        },
        "rules": [
            {
                "id": "by_check_eq",
                "when": {"check_id_eq": "ai_signin_personal_tier"},
                "add_frameworks": {"nist_ai_rmf": ["GOVERN-1.1"]},
            },
            {
                "id": "by_check_glob",
                "when": {"check_id_glob": "cis_aws_2.1.*"},
                "add_frameworks": {"soc2_ai": ["X.1"]},
            },
            {
                "id": "by_domain",
                "when": {"domain": "ai"},
                "add_frameworks": {"nist_ai_rmf": ["MEASURE-1.1"]},
            },
            {
                "id": "by_resource_type",
                "when": {"resource_type_glob": "aws_bedrock_*"},
                "add_frameworks": {"nist_ai_rmf": ["MEASURE-2.1"]},
            },
            {
                "id": "by_ai_touching",
                "when": {"ai_touching": True},
                "add_frameworks": {"nist_ai_rmf": ["MEASURE-3.1"]},
            },
            {
                "id": "by_evidence",
                "when": {"evidence_packet_eq": {"is_ai": "true"}},
                "add_frameworks": {"nist_ai_rmf": ["MEASURE-4.1"]},
            },
        ],
    }


def _finding(check_id="x", domain=None, resource_type=None, evidence_packet=None,
             subject_entity_id=None, frameworks=None):
    return {
        "check_id":          check_id,
        "domain":            domain,
        "resource_type":     resource_type,
        "evidence_packet":   evidence_packet or {},
        "subject_entity_id": subject_entity_id,
        "frameworks":        frameworks or {},
    }


def test_check_id_eq_matches_exact(simple_registry):
    f = _finding(check_id="ai_signin_personal_tier")
    result = fr.apply(f, entity_index={}, registry=simple_registry)
    assert "nist_ai_rmf" in result["frameworks"]
    assert "GOVERN-1.1" in result["frameworks"]["nist_ai_rmf"]


def test_check_id_glob_matches_prefix(simple_registry):
    f = _finding(check_id="cis_aws_2.1.1")
    result = fr.apply(f, entity_index={}, registry=simple_registry)
    assert "soc2_ai" in result["frameworks"]


def test_domain_matches(simple_registry):
    f = _finding(domain="ai", check_id="anything")
    result = fr.apply(f, entity_index={}, registry=simple_registry)
    assert "MEASURE-1.1" in result["frameworks"]["nist_ai_rmf"]


def test_resource_type_glob_matches(simple_registry):
    f = _finding(resource_type="aws_bedrock_endpoint", check_id="anything")
    result = fr.apply(f, entity_index={}, registry=simple_registry)
    assert "MEASURE-2.1" in result["frameworks"]["nist_ai_rmf"]


def test_ai_touching_via_evidence_packet(simple_registry):
    f = _finding(evidence_packet={"is_ai": "true"}, check_id="anything")
    result = fr.apply(f, entity_index={}, registry=simple_registry)
    # Both by_ai_touching and by_evidence rules fire.
    assert "MEASURE-3.1" in result["frameworks"]["nist_ai_rmf"]
    assert "MEASURE-4.1" in result["frameworks"]["nist_ai_rmf"]


def test_ai_touching_via_entity_domain(simple_registry):
    f = _finding(subject_entity_id="abc-123", check_id="anything")
    entity_index = {"abc-123": {"domain": "ai", "kind": "bedrock_model"}}
    result = fr.apply(f, entity_index=entity_index, registry=simple_registry)
    assert "MEASURE-3.1" in result["frameworks"]["nist_ai_rmf"]


def test_ai_touching_false_when_entity_missing(simple_registry):
    """If subject_entity_id points to a missing entity, ai_touching is False (not error)."""
    f = _finding(subject_entity_id="missing", check_id="anything")
    result = fr.apply(f, entity_index={}, registry=simple_registry)
    assert "MEASURE-3.1" not in result["frameworks"].get("nist_ai_rmf", [])


def test_evidence_packet_eq_match(simple_registry):
    f = _finding(evidence_packet={"is_ai": "true"}, check_id="anything")
    result = fr.apply(f, entity_index={}, registry=simple_registry)
    assert "MEASURE-4.1" in result["frameworks"]["nist_ai_rmf"]


def test_no_rule_matches_no_op(simple_registry):
    f = _finding(check_id="random_check", frameworks={"soc2": ["CC1.1"]})
    result = fr.apply(f, entity_index={}, registry=simple_registry)
    assert result["frameworks"] == {"soc2": ["CC1.1"]}
```

- [ ] **Step 2: Run tests — they fail because `fr.apply` doesn't exist**

```bash
cd platform/lambda/scanner_core
python -m pytest tests/test_framework_registry.py -v
```

Expected: 4 pass (the validation tests) + 9 fail (`AttributeError: module 'scanner_core.framework_registry' has no attribute 'apply'`).

- [ ] **Step 3: Commit failing tests**

```bash
git add platform/lambda/scanner_core/tests/test_framework_registry.py
git commit -m "test(s3): failing selector-matching tests"
```

### Task A5: Implement selector matching + apply()

**Files:**
- Modify: `platform/lambda/scanner_core/framework_registry.py` (append)

- [ ] **Step 1: Append selector engine + apply()**

Append to `platform/lambda/scanner_core/framework_registry.py`:

```python
# ----- Selector matching + apply() -----


def _matches(finding: dict, entity_index: dict, when: dict) -> bool:
    """All selectors AND-ed together. False on any miss."""
    for selector, expected in when.items():
        if selector == "check_id_eq":
            if finding.get("check_id") != expected:
                return False
        elif selector == "check_id_glob":
            if not fnmatch.fnmatchcase(finding.get("check_id") or "", expected):
                return False
        elif selector == "domain":
            if finding.get("domain") != expected:
                return False
        elif selector == "resource_type_glob":
            if not fnmatch.fnmatchcase(finding.get("resource_type") or "", expected):
                return False
        elif selector == "ai_touching":
            actual = _is_ai_touching(finding, entity_index)
            if actual != expected:
                return False
        elif selector == "evidence_packet_eq":
            ep = finding.get("evidence_packet") or {}
            for k, v in expected.items():
                if str(ep.get(k)) != str(v):
                    return False
        else:
            # Should be caught by validation; defensive.
            raise RegistryApplyError(f"unknown selector at apply time: {selector}")
    return True


def _is_ai_touching(finding: dict, entity_index: dict) -> bool:
    """Mirrors ai_summary._IS_AI_TOUCHING predicate.

    A finding is AI-touching if:
      - subject entity has domain='ai', OR
      - subject entity has an AI-resource kind, OR
      - evidence_packet ->> 'is_ai' = 'true'.

    Framework-key match is NOT used here (that would be circular with apply()).
    """
    ep = finding.get("evidence_packet") or {}
    if str(ep.get("is_ai")) == "true":
        return True
    eid = finding.get("subject_entity_id")
    if not eid:
        return False
    entity = entity_index.get(eid)
    if not entity:
        return False
    if entity.get("domain") == "ai":
        return True
    if entity.get("kind") in _AI_RESOURCE_KINDS:
        return True
    return False


def apply(finding: dict, entity_index: dict, registry: dict | None = None) -> dict:
    """Apply registry rules to a finding. Returns the SAME finding object
    (mutated in place — the caller already owns it). Additive, idempotent.

    Side-effects:
      - finding['frameworks'] updated (set-union per framework key, sorted)
      - finding['evidence_packet']['_registry_rule_ids'] appended (set-union, sorted)
    """
    reg = registry if registry is not None else _REGISTRY
    rules_fired: list[str] = []

    for rule in reg["rules"]:
        try:
            if _matches(finding, entity_index, rule["when"]):
                rules_fired.append(rule["id"])
                for fw, ctrls in rule["add_frameworks"].items():
                    existing = set(finding["frameworks"].get(fw) or [])
                    finding["frameworks"][fw] = sorted(existing | set(ctrls))
        except RegistryApplyError:
            # Re-raise so the writer's try/except catches it and logs.
            raise

    if rules_fired:
        ep = finding.setdefault("evidence_packet", {})
        prior = set(ep.get("_registry_rule_ids") or [])
        ep["_registry_rule_ids"] = sorted(prior | set(rules_fired))

    return finding
```

- [ ] **Step 2: Run all selector tests — they pass**

```bash
cd platform/lambda/scanner_core
python -m pytest tests/test_framework_registry.py -v
```

Expected: all 13 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add platform/lambda/scanner_core/framework_registry.py
git commit -m "feat(s3): framework_registry apply() with 6 selector kinds + provenance"
```

### Task A6: Add additive-merge and idempotency tests

**Files:**
- Modify: `platform/lambda/scanner_core/tests/test_framework_registry.py` (append)

- [ ] **Step 1: Add tests**

Append:

```python
# --- Additive merge + idempotency ---

def test_existing_frameworks_preserved(simple_registry):
    """Shasta-emitted controls must not be overwritten."""
    f = _finding(check_id="ai_signin_personal_tier",
                 frameworks={"nist_ai_rmf": ["MANAGE-1.0"]})
    result = fr.apply(f, entity_index={}, registry=simple_registry)
    assert "MANAGE-1.0" in result["frameworks"]["nist_ai_rmf"]
    assert "GOVERN-1.1" in result["frameworks"]["nist_ai_rmf"]
    # Sorted for diff stability.
    assert result["frameworks"]["nist_ai_rmf"] == sorted(result["frameworks"]["nist_ai_rmf"])


def test_duplicate_controls_deduped(simple_registry):
    """Re-apply produces same output."""
    f = _finding(check_id="ai_signin_personal_tier",
                 frameworks={"nist_ai_rmf": ["GOVERN-1.1"]})
    result = fr.apply(f, entity_index={}, registry=simple_registry)
    assert result["frameworks"]["nist_ai_rmf"].count("GOVERN-1.1") == 1


def test_idempotency(simple_registry):
    """apply(apply(f)) == apply(f)."""
    f = _finding(check_id="ai_signin_personal_tier")
    once = fr.apply(dict(f, frameworks={}), entity_index={}, registry=simple_registry)
    twice = fr.apply(once, entity_index={}, registry=simple_registry)
    assert once["frameworks"] == twice["frameworks"]
    assert once["evidence_packet"]["_registry_rule_ids"] == twice["evidence_packet"]["_registry_rule_ids"]


def test_provenance_rule_ids_recorded(simple_registry):
    f = _finding(check_id="ai_signin_personal_tier")
    result = fr.apply(f, entity_index={}, registry=simple_registry)
    assert result["evidence_packet"]["_registry_rule_ids"] == ["by_check_eq"]


def test_provenance_multiple_rules_recorded(simple_registry):
    f = _finding(check_id="ai_signin_personal_tier", domain="ai")
    result = fr.apply(f, entity_index={}, registry=simple_registry)
    assert sorted(result["evidence_packet"]["_registry_rule_ids"]) == ["by_check_eq", "by_domain"]
```

- [ ] **Step 2: Run all tests**

```bash
cd platform/lambda/scanner_core
python -m pytest tests/test_framework_registry.py -v
```

Expected: 18 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add platform/lambda/scanner_core/tests/test_framework_registry.py
git commit -m "test(s3): additive merge, idempotency, and provenance tests"
```

### Task A7: Branch + push + open PR for Slice A

- [ ] **Step 1: Create branch + push**

```bash
git checkout -b feat/ai-visibility-v2-slice-3-engine
git push -u origin feat/ai-visibility-v2-slice-3-engine
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --title "feat(s3): framework registry engine + empty JSON skeleton" --body "$(cat <<'EOF'
## Summary
Engine for AI Visibility v2 Slice 3 — pure code-only PR, no behavior change.

- New module `scanner_core/framework_registry.py` (loader, validator, 6-selector matcher, additive merge, provenance)
- New `scanner_core/ai_framework_registry.json` with 8 framework keys + `rules: []`
- 18 unit tests covering schema validation, every selector kind, additive merge, idempotency, provenance

## Test plan
- [x] All 18 unit tests pass
- [ ] No behavior change in any scanner (Slice B wires the engine in)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Wait for KK review + merge before proceeding to Slice B.**

---

## Slice B — Hook into 4 commit_scan paths

Wires the engine into AWS / Azure / GCP / ai_scanner. Still no-op because `rules: []`.

### Task B1: Wire registry into shasta_runner_azure unified_writer

**Files:**
- Modify: `platform/lambda/shasta_runner_azure/app/unified_writer.py`

- [ ] **Step 1: Add the import + apply call inside commit_scan**

Open `platform/lambda/shasta_runner_azure/app/unified_writer.py`, find `def commit_scan(...)`, locate the loop that processes findings before the INSERT. Add the import at the top of the file:

```python
from framework_registry import apply as apply_registry, RegistryApplyError
import logging
log = logging.getLogger(__name__)
```

Inside `commit_scan`, **before** the findings INSERT loop, build the entity_index from the entities being committed:

```python
entity_index = {
    str(e.id): {"domain": e.domain, "kind": e.kind}
    for e in entities
}
# Backfill index for findings referencing entities NOT in this commit batch.
missing_eids = [
    str(f.subject_entity_id) for f in findings
    if f.subject_entity_id and str(f.subject_entity_id) not in entity_index
]
if missing_eids:
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql="SELECT id::text, domain, kind FROM entities WHERE id = ANY(CAST(:ids AS UUID[]))",
        parameters=[{"name": "ids", "value": {"stringValue": "{" + ",".join(missing_eids) + "}"}}],
    )
    for r in rs.get("records", []):
        entity_index[r[0]["stringValue"]] = {
            "domain": r[1].get("stringValue"),
            "kind":   r[2].get("stringValue"),
        }
```

Then wrap the per-finding loop with the registry apply:

```python
rules_fired_count: dict[str, int] = {}
apply_failed_count = 0
for f in findings:
    finding_dict = {
        "check_id":          f.check_id,
        "domain":            f.domain,
        "resource_type":     f.resource_type,
        "evidence_packet":   f.evidence_packet or {},
        "subject_entity_id": str(f.subject_entity_id) if f.subject_entity_id else None,
        "frameworks":        dict(f.frameworks or {}),
    }
    try:
        apply_registry(finding_dict, entity_index)
        f.frameworks = finding_dict["frameworks"]
        f.evidence_packet = finding_dict["evidence_packet"]
        for rid in finding_dict.get("evidence_packet", {}).get("_registry_rule_ids", []):
            rules_fired_count[rid] = rules_fired_count.get(rid, 0) + 1
    except RegistryApplyError as e:
        apply_failed_count += 1
        log.warning("registry_apply_failed", extra={
            "check_id": f.check_id, "err": str(e),
        })
    # else: continue with existing INSERT logic
```

After the loop, emit per-scan counters once:

```python
log.info("registry_apply_summary", extra={
    "rules_fired_count": rules_fired_count,
    "apply_failed_count": apply_failed_count,
    "missing_entities_count": len(missing_eids),
    "scan_id": str(scan.scan_id) if hasattr(scan, "scan_id") else None,
})
```

- [ ] **Step 2: Run the Azure scanner test suite**

```bash
cd platform/lambda/shasta_runner_azure
python -m pytest -v
```

Expected: all existing tests pass. The registry is a no-op with `rules: []`.

- [ ] **Step 3: Commit**

```bash
git add platform/lambda/shasta_runner_azure/app/unified_writer.py
git commit -m "feat(s3): wire framework_registry into shasta_runner_azure.commit_scan"
```

### Task B2: Wire registry into shasta_runner_gcp unified_writer

**Files:**
- Modify: `platform/lambda/shasta_runner_gcp/app/unified_writer.py`

- [ ] **Step 1: Apply the same change as Task B1**

Apply the identical pattern from Task B1 to `platform/lambda/shasta_runner_gcp/app/unified_writer.py`. The code is the same; only the file path differs.

- [ ] **Step 2: Run the GCP scanner test suite**

```bash
cd platform/lambda/shasta_runner_gcp
python -m pytest -v
```

Expected: all existing tests pass.

- [ ] **Step 3: Commit**

```bash
git add platform/lambda/shasta_runner_gcp/app/unified_writer.py
git commit -m "feat(s3): wire framework_registry into shasta_runner_gcp.commit_scan"
```

### Task B3: Wire registry into shasta_runner (AWS) unified_writer

**Files:**
- Modify: `platform/lambda/shasta_runner/app/unified_writer.py`
- Verify: `platform/lambda/shasta_runner/build.sh` already copies `scanner_core/`

- [ ] **Step 1: Verify build.sh copies scanner_core**

```bash
grep "scanner_core" platform/lambda/shasta_runner/build.sh
```

If empty, add the copy step matching the pattern in `shasta_runner_azure/build.sh`:

```bash
cp -r ../scanner_core app/scanner_core
```

- [ ] **Step 2: Apply Task B1's pattern**

Apply the same code change to `platform/lambda/shasta_runner/app/unified_writer.py`.

- [ ] **Step 3: Run the AWS scanner test suite**

```bash
cd platform/lambda/shasta_runner
python -m pytest -v
```

Expected: all 101 scanner tests pass.

- [ ] **Step 4: Commit**

```bash
git add platform/lambda/shasta_runner/app/unified_writer.py platform/lambda/shasta_runner/build.sh
git commit -m "feat(s3): wire framework_registry into shasta_runner (AWS) commit_scan"
```

### Task B4: Wire registry into ai_scanner unified_writer

**Files:**
- Modify: `platform/lambda/ai_scanner/unified_writer.py`

- [ ] **Step 1: Apply Task B1's pattern**

Apply the same code change to `platform/lambda/ai_scanner/unified_writer.py`. (Note: ai_scanner's `unified_writer.py` lives at the package root, not under `app/`.)

- [ ] **Step 2: Run the ai_scanner test suite**

```bash
cd platform/lambda/ai_scanner
python -m pytest -v
```

Expected: all existing tests pass.

- [ ] **Step 3: Commit**

```bash
git add platform/lambda/ai_scanner/unified_writer.py
git commit -m "feat(s3): wire framework_registry into ai_scanner.commit_scan"
```

### Task B5: Branch + PR for Slice B

- [ ] **Step 1: Push and open PR**

```bash
git push -u origin feat/ai-visibility-v2-slice-3-engine
gh pr create --title "feat(s3): wire registry into 4 commit_scan paths" --body "$(cat <<'EOF'
## Summary
Hooks the framework registry into the 4 scanners that use `unified_writer.commit_scan()`: AWS, Azure, GCP, and ai_scanner. Engine is still a no-op (`rules: []`), so no behavior change for customers — but every scan now exercises the hook.

## Test plan
- [x] All 4 scanner suites pass (101 AWS + Azure + GCP + ai_scanner)
- [ ] Manual Quick scan against KK's tenant after deploy — verify scan completes and `registry_apply_summary` log line appears in CloudWatch with `rules_fired_count: {}`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: Wait for KK review + merge.**

---

## Slice C — Hook into Entra batch_execute path

Adds the registry to the Entra scanner's distinct write path.

### Task C1: Verify build.sh copies scanner_core into Entra image

**Files:**
- Modify: `platform/lambda/shasta_runner_entra/build.sh`

- [ ] **Step 1: Check current state**

```bash
grep "scanner_core" platform/lambda/shasta_runner_entra/build.sh
```

- [ ] **Step 2: Add copy step if missing**

If the grep returned nothing, append to `platform/lambda/shasta_runner_entra/build.sh` (before the `docker build` line):

```bash
cp -r ../scanner_core app/scanner_core
```

- [ ] **Step 3: Commit**

```bash
git add platform/lambda/shasta_runner_entra/build.sh
git commit -m "feat(s3): copy scanner_core into shasta_runner_entra image"
```

### Task C2: Wire registry into Entra _insert_findings + _insert_finding_param_lists

**Files:**
- Modify: `platform/lambda/shasta_runner_entra/app/main.py:152-185`

- [ ] **Step 1: Add import**

At top of `main.py`:

```python
from framework_registry import apply as apply_registry, RegistryApplyError
```

- [ ] **Step 2: Add helper to enrich a finding dict before insert**

After the `_FINDING_INSERT_SQL` constant, add:

```python
def _enrich_with_registry(findings: list, entity_index: dict) -> dict:
    """Mutates each finding in-place; returns counters."""
    rules_fired: dict[str, int] = {}
    apply_failed = 0
    for f in findings:
        try:
            apply_registry(f, entity_index)
            for rid in f.get("evidence_packet", {}).get("_registry_rule_ids", []):
                rules_fired[rid] = rules_fired.get(rid, 0) + 1
        except RegistryApplyError as e:
            apply_failed += 1
            log.warning("registry_apply_failed", extra={
                "check_id": f.get("check_id"), "err": str(e),
            })
    return {"rules_fired_count": rules_fired, "apply_failed_count": apply_failed}
```

- [ ] **Step 3: Add entity_index builder**

Entra commits only findings — entities don't accompany the commit. The entity_index is built entirely from a DB lookup:

```python
def _build_entity_index_for_findings(findings: list) -> dict:
    eids = sorted({
        str(f["subject_entity_id"])
        for f in findings
        if f.get("subject_entity_id")
    })
    if not eids:
        return {}
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql="SELECT id::text, domain, kind FROM entities WHERE id = ANY(CAST(:ids AS UUID[]))",
        parameters=[{"name": "ids", "value": {"stringValue": "{" + ",".join(eids) + "}"}}],
    )
    return {
        r[0]["stringValue"]: {"domain": r[1].get("stringValue"), "kind": r[2].get("stringValue")}
        for r in rs.get("records", [])
    }
```

- [ ] **Step 4: Hook into _insert_findings**

Modify the body of `_insert_findings` so the registry runs **before** the loop that builds param_sets:

```python
def _insert_findings(findings, scan_id, tenant_id, conn_id, entra_tenant_id):
    if not findings:
        return 0
    # NEW: apply framework registry before insert
    entity_index = _build_entity_index_for_findings(
        [_finding_to_dict(f) for f in findings]
    )
    counters = _enrich_with_registry(
        [_finding_to_dict(f) for f in findings], entity_index,
    )
    log.info("registry_apply_summary", extra={**counters, "scan_id": scan_id})
    # ... existing loop ...
```

You need a `_finding_to_dict` helper that returns the dict shape `apply_registry` expects. If the existing finding model is dataclass-shaped, add:

```python
def _finding_to_dict(f) -> dict:
    return {
        "check_id":          getattr(f, "check_id", None),
        "domain":            getattr(f, "domain", None),
        "resource_type":     getattr(f, "resource_type", None),
        "evidence_packet":   getattr(f, "evidence_packet", None) or {},
        "subject_entity_id": getattr(f, "subject_entity_id", None),
        "frameworks":        dict(getattr(f, "frameworks", None) or {}),
    }
```

After enrichment, write the mutated `frameworks` and `evidence_packet` back to the source finding before serialising for the INSERT.

Note: the AI sign-in pass uses `_insert_finding_param_lists` (param shape pre-built). For that path, the registry needs to operate on the already-built param dicts. Either:
- (a) Refactor the AI sign-in pass to emit Finding-shaped objects so it shares `_insert_findings`, OR
- (b) Add a parallel `_enrich_param_lists` that reads/writes the `frameworks` and `evidence_packet` JSON-string fields inside the param dict.

Pick (b) for minimal change. Implement `_enrich_param_lists`:

```python
def _enrich_param_lists(param_lists: list[list[dict]]) -> dict:
    """Mutates param dicts in place. Each param_list entry is a list of
    {name, value} dicts as built for batch_execute_statement."""
    findings_view = []
    for ps in param_lists:
        view = {}
        for p in ps:
            view[p["name"]] = _unwrap_param(p["value"])
        findings_view.append(view)
    entity_index = _build_entity_index_for_findings(findings_view)
    counters = {"rules_fired_count": {}, "apply_failed_count": 0}
    for ps, view in zip(param_lists, findings_view):
        try:
            apply_registry(view, entity_index)
            counters["rules_fired_count"] = {
                **counters["rules_fired_count"],
                **{rid: counters["rules_fired_count"].get(rid, 0) + 1
                   for rid in view.get("evidence_packet", {}).get("_registry_rule_ids", [])},
            }
            # Write back the JSON-string fields.
            for p in ps:
                if p["name"] == "frameworks":
                    p["value"] = {"stringValue": json.dumps(view["frameworks"])}
                elif p["name"] == "evidence_packet":
                    p["value"] = {"stringValue": json.dumps(view["evidence_packet"])}
        except RegistryApplyError as e:
            counters["apply_failed_count"] += 1
            log.warning("registry_apply_failed", extra={"err": str(e)})
    return counters


def _unwrap_param(value_obj: dict):
    """Unwrap an RDS-Data param value back to a Python value."""
    if "stringValue" in value_obj:
        try:
            return json.loads(value_obj["stringValue"])
        except (TypeError, ValueError):
            return value_obj["stringValue"]
    return None
```

Then call `_enrich_param_lists(param_lists)` at the start of `_insert_finding_param_lists`, before the batch loop.

- [ ] **Step 5: Run the Entra scanner suite**

```bash
cd platform/lambda/shasta_runner_entra
python -m pytest -v
```

Expected: all existing tests pass (registry is still a no-op).

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/shasta_runner_entra/app/main.py
git commit -m "feat(s3): wire framework_registry into Entra's direct batch_execute path"
```

### Task C3: Branch + PR for Slice C

- [ ] **Step 1: Push and open PR**

```bash
git push origin feat/ai-visibility-v2-slice-3-engine
gh pr create --title "feat(s3): wire framework_registry into Entra commit path" --body "$(cat <<'EOF'
## Summary
Entra uses its own `_insert_findings` + `_insert_finding_param_lists` path (not `unified_writer.commit_scan`). This PR adds parallel registry-enrichment hooks for both methods plus the build.sh copy step.

Engine is still a no-op (`rules: []`).

## Test plan
- [x] Entra scanner suite passes
- [ ] Manual Entra rescan after deploy — verify `registry_apply_summary` log line in CloudWatch

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: Wait for KK review + merge.**

---

## Slice D — Read-side: _AI_FRAMEWORKS 4→8 + /ai tile grid

Expands the `/ai` view from 4 to 8 framework tiles. Visible behavior change because Shasta already emits all 8 framework keys.

### Task D1: Expand _AI_FRAMEWORKS tuple in ai_summary

**Files:**
- Modify: `platform/lambda/ai_summary/main.py:41`

- [ ] **Step 1: Update the tuple**

Change line 41 of `platform/lambda/ai_summary/main.py` from:

```python
_AI_FRAMEWORKS = ("nist_ai_rmf", "iso_42001", "soc2_ai", "eu_ai_act")
```

to:

```python
_AI_FRAMEWORKS = (
    "nist_ai_rmf", "iso_42001", "soc2_ai", "eu_ai_act",
    "nist_ai_600_1", "owasp_llm_top10", "owasp_agentic", "mitre_atlas",
)
```

- [ ] **Step 2: Commit**

```bash
git add platform/lambda/ai_summary/main.py
git commit -m "feat(s3): expand _AI_FRAMEWORKS from 4 to 8 keys in ai_summary"
```

### Task D2: Update ai_summary tests for 8 frameworks

**Files:**
- Modify: `platform/lambda/ai_summary/tests/test_main.py`

- [ ] **Step 1: Extend the by_framework fixture and assertions**

Open `tests/test_main.py`. The current test asserts `by_framework` for 4 keys. Extend the fixture data to include rows for all 8 keys, and assert all 8 appear in the response with the correct fail/partial/pass counts.

The existing test pattern:

```python
assert body["by_framework"]["nist_ai_rmf"] == {"fail": 4, "partial": 1, "pass": 8}
assert body["by_framework"]["iso_42001"]   == {"fail": 3, "partial": 2, "pass": 6}
assert body["by_framework"]["soc2_ai"]     == {"fail": 0, "partial": 0, "pass": 0}
assert body["by_framework"]["eu_ai_act"]   == {"fail": 0, "partial": 0, "pass": 0}
```

Add fixture rows for `nist_ai_600_1`, `owasp_llm_top10`, `owasp_agentic`, `mitre_atlas` (any non-zero counts you choose), then add the 4 assertions.

- [ ] **Step 2: Run tests**

```bash
cd platform/lambda/ai_summary
python -m pytest tests/test_main.py -v
```

Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add platform/lambda/ai_summary/tests/test_main.py
git commit -m "test(s3): assert ai_summary returns all 8 AI framework tiles"
```

### Task D3: Expand /ai tile grid to 8 tiles

**Files:**
- Modify: `web/src/routes/AISummary.tsx`

- [ ] **Step 1: Find the framework tile rendering**

In `AISummary.tsx`, locate the by-framework tile section (around the area that maps over the 4 known frameworks). Update the framework array:

```tsx
const AI_FRAMEWORKS = [
  { key: "nist_ai_rmf",      label: "NIST AI RMF" },
  { key: "iso_42001",        label: "ISO 42001" },
  { key: "soc2_ai",          label: "SOC 2 + AI" },
  { key: "eu_ai_act",        label: "EU AI Act" },
  { key: "nist_ai_600_1",    label: "NIST AI 600-1" },
  { key: "owasp_llm_top10",  label: "OWASP LLM Top 10" },
  { key: "owasp_agentic",    label: "OWASP Agentic" },
  { key: "mitre_atlas",      label: "MITRE ATLAS" },
] as const;
```

Update the grid container to a 4×2 layout: `grid grid-cols-2 sm:grid-cols-4 gap-3` (or whatever utility classes match the project's existing Tailwind usage).

- [ ] **Step 2: Update vitest**

In `web/src/routes/AISummary.test.tsx`, find the assertion that counts framework tiles. Update from 4 to 8, and add label assertions for the 4 new ones.

- [ ] **Step 3: Run web tests**

```bash
cd web
pnpm test src/routes/AISummary.test.tsx
```

Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add web/src/routes/AISummary.tsx web/src/routes/AISummary.test.tsx
git commit -m "feat(s3-web): render 8 AI framework tiles on /ai"
```

### Task D4: Deploy + verify Slice D

- [ ] **Step 1: Deploy CisoCopilotApi hotswap**

```bash
cd platform
npx cdk deploy CisoCopilotApi --require-approval never --hotswap
```

Expected: `UPDATE_COMPLETE`, hotswap path used.

- [ ] **Step 2: Build + sync web**

```bash
cd web
pnpm build
aws s3 sync dist/ s3://ciso-copilot-app-470226123496/ --delete
aws cloudfront create-invalidation --distribution-id E2FV1Z0DJ4RQS4 --paths '/*'
```

- [ ] **Step 3: Verify on KK's tenant (KK does this manually)**

KK opens `https://shasta.transilience.cloud/ai` in incognito, signs in with Google, confirms 8 framework tiles render. Expected counts (from §2 of the spec):
- NIST AI RMF, OWASP LLM Top 10, NIST AI 600-1: ~85 findings each
- OWASP Agentic, MITRE ATLAS: ~102 each
- ISO 42001, EU AI Act: ~68 each
- SOC 2 AI: 0 (until Slice E1 or whenever AICPA mapping authoring lands)

### Task D5: Branch + PR for Slice D

- [ ] **Step 1: Push and open PR**

```bash
git push origin feat/ai-visibility-v2-slice-3-engine
gh pr create --title "feat(s3): expand /ai to 8 AI framework tiles" --body "$(cat <<'EOF'
## Summary
Surfaces the 4 AI frameworks Shasta was already emitting but the UI ignored: NIST AI 600-1, OWASP LLM Top 10, OWASP Agentic, MITRE ATLAS. /ai goes from 4 to 8 tiles.

## Test plan
- [x] ai_summary tests pass (8-key by_framework assertions)
- [x] AISummary.test.tsx asserts 8 tiles
- [ ] KK verifies in incognito after deploy: 8 tiles render with non-zero counts on all except SOC 2 AI

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: Wait for KK review + merge.**

---

## Slice E — First authored rule set: S2 Entra finding tagging

Adds 3 rules to the JSON for the 3 S2 Entra finding kinds, using verified IDs from public framework documents. SOC 2 AI authoring is held until AICPA doc is obtained.

### Task E1: Obtain source documents for verification

Per spec §12, every control ID in a rule PR must be validated against its source document. KK obtains:

- NIST AI RMF — public, at nist.gov/itl/ai-risk-management-framework
- ISO/IEC 42001:2023 — purchased from iso.org (paywalled)
- EU AI Act — public, at eur-lex.europa.eu (Regulation 2024/1689)
- OWASP LLM Top 10 — public, at genai.owasp.org
- OWASP Agentic — public (or draft), at genai.owasp.org
- MITRE ATLAS — public, at atlas.mitre.org

If ISO 42001 not yet purchased: omit `iso_42001` from the Slice E rule. Add it in a follow-on.

### Task E2: Add 3 rules for S2 Entra finding kinds

**Files:**
- Modify: `platform/lambda/scanner_core/ai_framework_registry.json`

- [ ] **Step 1: Edit the JSON**

Add to `rules: [...]`:

```json
{
  "id": "ai_signin_personal_tier_controls",
  "when": { "check_id_eq": "ai_signin_personal_tier" },
  "add_frameworks": {
    "nist_ai_rmf":   ["<VERIFIED-ID>", "<VERIFIED-ID>"],
    "eu_ai_act":     ["Article 9", "Article 26"],
    "owasp_llm_top10": ["<VERIFIED-ID>"]
  }
},
{
  "id": "ai_signin_corp_tier_controls",
  "when": { "check_id_eq": "ai_signin_corp_tier" },
  "add_frameworks": {
    "nist_ai_rmf":   ["<VERIFIED-ID>"],
    "eu_ai_act":     ["Article 9"]
  }
},
{
  "id": "ai_signin_unknown_tier_controls",
  "when": { "check_id_eq": "ai_signin_unknown_tier" },
  "add_frameworks": {
    "nist_ai_rmf":   ["<VERIFIED-ID>"],
    "eu_ai_act":     ["Article 9", "Article 26"]
  }
}
```

Replace every `<VERIFIED-ID>` with the actual control ID from the source document. Update each framework's `control_descriptions` block to include the new IDs with their verbatim descriptions.

- [ ] **Step 2: Commit**

```bash
git add platform/lambda/scanner_core/ai_framework_registry.json
git commit -m "feat(s3): author rules for S2 Entra sign-in finding kinds"
```

### Task E3: Add unit tests for the 3 rules

**Files:**
- Modify: `platform/lambda/scanner_core/tests/test_framework_registry.py`

- [ ] **Step 1: Add tests using the shipping registry**

Append to `test_framework_registry.py`:

```python
# --- Integration: shipping registry rules for S2 Entra findings ---

def test_personal_tier_finding_tagged_with_ai_frameworks():
    f = _finding(check_id="ai_signin_personal_tier")
    result = fr.apply(f, entity_index={}, registry=fr.load_registry())
    assert "nist_ai_rmf" in result["frameworks"]
    assert "eu_ai_act" in result["frameworks"]
    assert "Article 9" in result["frameworks"]["eu_ai_act"]


def test_corp_tier_finding_tagged():
    f = _finding(check_id="ai_signin_corp_tier")
    result = fr.apply(f, entity_index={}, registry=fr.load_registry())
    assert "nist_ai_rmf" in result["frameworks"]


def test_unknown_tier_finding_tagged():
    f = _finding(check_id="ai_signin_unknown_tier")
    result = fr.apply(f, entity_index={}, registry=fr.load_registry())
    assert "eu_ai_act" in result["frameworks"]


def test_unrelated_finding_not_tagged_by_entra_rules():
    f = _finding(check_id="cis_aws_2.1.1")
    result = fr.apply(f, entity_index={}, registry=fr.load_registry())
    assert "Article 9" not in (result["frameworks"].get("eu_ai_act") or [])
```

- [ ] **Step 2: Run tests**

```bash
cd platform/lambda/scanner_core
python -m pytest tests/test_framework_registry.py -v
```

Expected: all 22 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add platform/lambda/scanner_core/tests/test_framework_registry.py
git commit -m "test(s3): integration tests for S2 Entra rule set"
```

### Task E4: Deploy + verify

- [ ] **Step 1: Rebuild + push scanner images**

```bash
cd platform/lambda/shasta_runner_entra && ./build.sh
cd ../shasta_runner_azure && ./build.sh
cd ../shasta_runner_gcp && ./build.sh
cd ../shasta_runner && ./build.sh
cd ../ai_scanner && ./build.sh
```

Each `build.sh` pushes to ECR and tags `:latest`.

- [ ] **Step 2: Trigger Entra rescan to verify**

KK triggers a rescan of his Entra connection via `/scan`. After completion:

```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot \
  --sql "SELECT check_id, frameworks, evidence_packet->'_registry_rule_ids' FROM findings WHERE check_id LIKE 'ai_signin_%' ORDER BY last_seen DESC LIMIT 5"
```

Expected: findings carry `nist_ai_rmf` + `eu_ai_act` keys; `_registry_rule_ids` lists the matched rule IDs.

- [ ] **Step 3: Verify CloudWatch counter**

```bash
aws logs filter-log-events --log-group-name "/aws/lambda/ciso-copilot-shasta-runner-entra" \
  --filter-pattern '"registry_apply_summary"' --start-time $(($(date +%s%3N) - 600000))
```

Expected: at least one log entry with non-empty `rules_fired_count`.

### Task E5: Capture verification screenshot for HANDOFF

Per spec §9: post-ship verification artifact.

- [ ] **Step 1: KK navigates to /findings?framework=eu_ai_act → group by Category**

Confirms an AI row appears, captures screenshot.

- [ ] **Step 2: Save screenshot under docs/handoff-screenshots/**

```bash
mkdir -p docs/handoff-screenshots
# KK saves the screenshot as docs/handoff-screenshots/2026-MM-DD-s3-eu-ai-act-ai-row.png
```

- [ ] **Step 3: Update HANDOFF.md with S3 ship block + screenshot reference**

Add a "🚀 AI Visibility v2 — Slice 3 shipped" block at the top of HANDOFF.md (matching the existing slice-block style), reference the screenshot.

### Task E6: Branch + PR for Slice E

- [ ] **Step 1: Push and open PR**

```bash
git push origin feat/ai-visibility-v2-slice-3-engine
gh pr create --title "feat(s3): author first rule set — S2 Entra finding tagging" --body "$(cat <<'EOF'
## Summary
Adds 3 rules to the framework registry, one per S2 Entra sign-in finding kind, with control IDs verified against source documents per spec §12. Lights up the AI row under EU AI Act and NIST AI RMF on the `/findings` per-framework view.

SOC 2 AI authoring is deferred (AICPA document not yet obtained).
ISO 42001 authoring is conditional on document purchase.

## Verification
- [x] 22 unit tests pass
- [ ] KK rescans Entra; findings carry expected framework keys; CloudWatch `rules_fired_count` non-empty; screenshot captured to HANDOFF

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: Wait for KK review + merge.**

---

## Slice F — Compliance-defensibility UI copy

Per spec §14.1: every framework surface must read "mapping, not attestation".

### Task F1: Add tooltip to /ai framework tiles

**Files:**
- Modify: `web/src/routes/AISummary.tsx`

- [ ] **Step 1: Add a tooltip component to each framework tile**

In `AISummary.tsx`, wrap each framework tile with a tooltip showing:

> Mapping only — not a compliance attestation. Verify with your auditor.

Use the project's existing tooltip pattern (check existing components for the convention; if none, a CSS `:hover` `title` attribute or a small `Info` icon with `aria-label` is sufficient).

- [ ] **Step 2: Update AISummary.test.tsx**

Add assertion that the tooltip text is present on each framework tile.

- [ ] **Step 3: Run web tests**

```bash
cd web && pnpm test src/routes/AISummary.test.tsx
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add web/src/routes/AISummary.tsx web/src/routes/AISummary.test.tsx
git commit -m "feat(s3-web): mapping-not-attestation tooltip on /ai framework tiles"
```

### Task F2: Add same disclaimer to /findings framework filter chip

**Files:**
- Modify: `web/src/routes/TopRisks.tsx`

- [ ] **Step 1: Locate the framework-filter chip render**

In `TopRisks.tsx`, find where `framework: <fw>` chip is rendered (search for `framework: ${framework}` around line 209). Add the same tooltip:

> Mapping only — not a compliance attestation. Verify with your auditor.

- [ ] **Step 2: Commit**

```bash
git add web/src/routes/TopRisks.tsx
git commit -m "feat(s3-web): mapping-not-attestation tooltip on framework filter chip"
```

### Task F3: Deploy + branch + PR

- [ ] **Step 1: Build + sync web**

```bash
cd web
pnpm build
aws s3 sync dist/ s3://ciso-copilot-app-470226123496/ --delete
aws cloudfront create-invalidation --distribution-id E2FV1Z0DJ4RQS4 --paths '/*'
```

- [ ] **Step 2: Push and open PR**

```bash
git push origin feat/ai-visibility-v2-slice-3-engine
gh pr create --title "feat(s3-web): mapping-not-attestation tooltips on /ai and /findings" --body "$(cat <<'EOF'
## Summary
Per spec §14.1 — every framework surface carries a tooltip clarifying that the product produces mappings, not compliance attestations. Customers/auditors see the disclaimer on hover for every framework tile and every framework filter chip.

## Test plan
- [x] AISummary.test.tsx asserts tooltip presence
- [ ] KK spot-checks /ai and /findings?framework=eu_ai_act

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Wait for KK review + merge.** Slice F is the final S3 PR.

---

## Self-Review

**1. Spec coverage:**

| Spec section | Plan task(s) |
|---|---|
| §1 Goal | Whole plan |
| §2 Empirical context | Slice D (light up unsurfaced frameworks) |
| §3 Decisions D-1..D-8 | D-1 → Slice E (verbatim AICPA — deferred); D-2 → Slice D; D-3 → Slice E; D-4 → engine semantics in Slice A; D-5 → Slices B+C; D-6 → Task A1 (JSON); D-7 → no SQL migration (recipe in §11 only); D-8 → no new view |
| §4 Architecture | Slice A + B + C |
| §5 Registry schema | Task A1 + A3 (loader/validator) + A5 (apply) |
| §6 Data flow | Tasks B1-B4 + C2 (write path); D1-D3 (read path) |
| §7 Error handling | A3 (cold-start validation); B1-B4 + C2 (per-finding try/except); rules_fired_count + apply_failed_count counters in same tasks |
| §8 Testing | Tasks A2/A4/A6 (unit, ≥18 cases); B1-B4 (scanner suites still pass); D2 (read-side regression); E3 (integration with shipping registry) |
| §9 Success criteria | Tasks D4 (8 tiles), E4 (Entra rescan verification), E5 (screenshot to HANDOFF) |
| §10 Open questions | Q1 → Task E1 (AICPA deferred); Q2 → Task E2 (Art. 9/26 already specified); Q3 → no rule fires until authorship PRs; Q4 → Task E1 (per-source validation) |
| §11 Impl notes | Task A1 (engine independent of authorship); Slices C1 (build.sh); Slice E (no SQL migration — recipe documented) |
| §12 Authoritative sources | Task E1 |
| §13 Dependencies | All dependencies already shipped |
| §14.1 Positioning | Slice F |
| §14.2 Provenance | Task A5 (`_registry_rule_ids` set-merge) |
| §15 Out of scope | A2/A3 not covered; deferred to future slice |

No gaps identified.

**2. Placeholder scan:** Searched for "TBD", "TODO", "implement later", "Similar to Task N", "add appropriate". The only `<VERIFIED-ID>` placeholders are in Task E2's example JSON, which is correct — the implementer fills them in per Task E1's source-document workflow. This is not a plan defect; it's the intentional gate per spec §12 that forces verification before merge.

**3. Type consistency:**
- Selector names (`check_id_eq`, `check_id_glob`, `domain`, `resource_type_glob`, `ai_touching`, `evidence_packet_eq`) match between Task A3's validator, Task A5's matcher, and Task A4/A6's tests.
- Function names: `load_registry`, `validate_registry`, `apply`, `_matches`, `_is_ai_touching` — consistent across Tasks A3, A4, A5.
- Counter names: `rules_fired_count`, `apply_failed_count`, `missing_entities_count` — consistent across Tasks B1-B4 and C2.
- Provenance key: `_registry_rule_ids` — consistent across Tasks A5, A6, E3, E4.
- Framework keys in JSON (Task A1) match the read-side tuple in Task D1: `nist_ai_rmf`, `iso_42001`, `soc2_ai`, `eu_ai_act`, `nist_ai_600_1`, `owasp_llm_top10`, `owasp_agentic`, `mitre_atlas`.

No inconsistencies identified.

---

## Out of scope (carry to follow-on slices)

- **SOC 2 AI rule authoring** — gated on KK obtaining the AICPA Description Criteria for AI Systems document. Becomes Slice E1.
- **AI-touching cloud finding crosswalk rules** (e.g., S3 encryption controls for Bedrock training buckets) — needs per-control authorship per spec §10 Q3. Becomes Slice E2.
- **Resource classification feature** (e.g., `handles_cardholder_data` evidence flag) — a separate feature, not a registry feature. Defer indefinitely.
- **`unified_writer.py` consolidation into `scanner_core/`** — would collapse 4 hook sites to 1, but Slice 0 deliberately didn't consolidate it. Treat as separate cleanup work.
- **One-off backfill script** — documented as a recipe in spec §11 instead of shipped as code.
