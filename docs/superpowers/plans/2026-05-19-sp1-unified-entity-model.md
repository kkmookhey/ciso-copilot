# SP1 — Unified Entity + Edge Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace AI-specific `ai_assets` / `ai_relationships` schema with a domain-agnostic `entities` + `edges` graph that cloud, AI, and future ASM/attack-path scanners all write into. Add `findings.subject_entity_id` so "all findings on this entity" is one indexed join.

**Architecture:** New tables, new shared `unified_writer.py` copied into each Lambda, AI scanner refactored to emit entities/edges with new natural-key semantics (models + frameworks dedupe across repos), `shasta_runner` derives cloud entities from existing finding ARNs plus four boto3 enumeration passes (IAM/storage/compute/network), new `crossdomain.py` detector emits cross-domain `deploys_to` edges from GitHub Actions YAMLs, `ai_scan_api` Lambda renamed to `entities_api` with two new graph + relationships endpoints. Big-bang migration on one feature branch; old tables held in soak for 1 week before drop.

**Tech Stack:** PostgreSQL 16 (Aurora) + Python 3.12 Lambda + boto3 + Aurora Data API + Vite/React/TS web + SwiftUI iOS + AWS CDK (TypeScript).

**Spec:** `docs/superpowers/specs/2026-05-19-sp1-unified-entity-model-design.md` (all section references below point at this doc).

**Sandbox note for the executor:** The Bash sandbox in this environment silently resets `.git/HEAD` to `main` between calls unless you pass `dangerouslyDisableSandbox: true` on every Bash command that needs branch state to persist. Skip this and your commits land on the wrong branch. The Slice 1b session lost ~30 minutes to this exact bug — see HANDOFF.md.

---

## Pre-flight check (do BEFORE Task 1)

```bash
cd /Users/kkmookhey/Projects/CISOBrief

# 1. Confirm Slice 1b PR is open / merged. Branch should be:
git branch --show-current
# Expected: feat/ai-security-slice-1b (we're branching from here)

# 2. Make a new branch for SP1
git switch -c feat/sp1-unified-entities

# 3. Confirm Docker, ripgrep, pytest available locally
docker info >/dev/null && echo "docker OK"
which rg && rg --version | head -1
/Users/kkmookhey/venv/bin/pytest --version

# 4. Confirm AWS CLI works with the right credentials
aws sts get-caller-identity --query Account --output text
# Expected: 470226123496
```

All four must succeed before starting.

## File structure overview

Files this plan creates or modifies, grouped by responsibility:

```
platform/
  sql/
    005_unified_entities.sql                       [CREATE] entities + edges + findings FK
  lambda/
    ai_scanner/
      unified_writer.py                            [CREATE] canonical writer (was writer.py)
      writer.py                                    [DELETE — replaced by unified_writer.py]
      main.py                                      [MODIFY] use unified_writer
      detectors/
        base.py                                    [MODIFY] EntityEmission + EdgeEmission
        framework.py                               [MODIFY] new emission types + natural_key
        model_usage.py                             [MODIFY] same
        mcp_server.py                              [MODIFY] same
        agentic_workflow.py                        [MODIFY] same
        vector_db.py                               [MODIFY] same
        embedding.py                               [MODIFY] same
        prompt.py                                  [MODIFY] same
        secrets_in_ai_code.py                      [MODIFY] same
        correlator.py                              [MODIFY] same
        crossdomain.py                             [CREATE] new detector (GitHub Actions → IAM)
      tests/
        test_detectors.py                          [MODIFY] update sort keys for new fields
        test_writer.py                             [DELETE — replaced by test_unified_writer.py]
        test_unified_writer.py                     [CREATE] writer tests + regressions
        fixtures/**/expected.json                  [MODIFY] regenerate all goldens
        fixtures/crossdomain/<scenarios>           [CREATE] crossdomain fixtures
    shasta_runner/
      app/
        main.py                                    [MODIFY] derive entities + 4 enum passes
        unified_writer.py                          [CREATE] copy from ai_scanner via build.sh
        arn_to_entity.py                           [CREATE] ARN parsing helper
        enumerate_iam.py                           [CREATE] iam.list_roles etc.
        enumerate_storage.py                       [CREATE] s3.list_buckets etc.
        enumerate_compute.py                       [CREATE] ec2 + lambda enum
        enumerate_network.py                       [CREATE] vpc + subnet + sg enum
        tests/
          test_arn_to_entity.py                    [CREATE]
          test_enumerate_*.py                      [CREATE] four files
      build.sh                                     [MODIFY] cp unified_writer.py from ai_scanner
    entities_api/                                  [CREATE — replaces ai_scan_api/]
      main.py                                      [CREATE] 5 routes
      helpers.py                                   [CREATE] mirror ai_scan_api/helpers
      unified_writer.py                            [CREATE] copy from ai_scanner
      tests/
        __init__.py                                [CREATE] empty
        conftest.py                                [CREATE] sys.path injection
        test_handler.py                            [CREATE] per-route tests
    ai_scan_api/                                   [DELETE — replaced by entities_api]
  lib/
    api-stack.ts                                   [MODIFY] rename Lambda + add new routes
  scripts/
    migrate_to_entities.py                         [CREATE] one-shot migration

web/
  src/
    lib/api.ts                                    [MODIFY] type+method rename, new methods
    routes/
      AIInventory.tsx                              [MODIFY] consume new field names
      AssetDetail.tsx                              [MODIFY] consume new field names

ios/CISOCopilot/
  Services/APIClient.swift                         [MODIFY] type+method rename
  Views/AI/
    AIInventoryView.swift                          [MODIFY] consume renamed types
    AIAssetDetailView.swift                        [MODIFY] consume renamed types
```

---

## Phase A — Schema + unified writer (3 tasks)

### Task 1: SQL migration

**Files:**
- Create: `platform/sql/005_unified_entities.sql`

- [ ] **Step 1: Write the migration SQL**

```sql
-- platform/sql/005_unified_entities.sql
-- SP1 — Unified entity + edge model. Spec: docs/superpowers/specs/2026-05-19-sp1-unified-entity-model-design.md §4.

BEGIN;

CREATE TABLE entities (
  id               UUID         PRIMARY KEY,
  tenant_id        UUID         NOT NULL REFERENCES tenants(tenant_id),
  kind             TEXT         NOT NULL,
  natural_key      TEXT         NOT NULL,
  display_name     TEXT         NOT NULL,
  domain           TEXT         NOT NULL
                                CHECK (domain IN ('cloud', 'ai', 'asm', 'identity', 'repo')),
  attributes       JSONB        NOT NULL DEFAULT '{}'::jsonb,
  evidence_packet  JSONB,
  detector_id      TEXT         NOT NULL,
  detector_version TEXT         NOT NULL,
  scan_id          UUID,
  first_seen_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  last_seen_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  UNIQUE (tenant_id, kind, natural_key)
);

CREATE INDEX entities_tenant_kind_idx   ON entities(tenant_id, kind);
CREATE INDEX entities_tenant_domain_idx ON entities(tenant_id, domain);

CREATE TABLE edges (
  id                UUID         PRIMARY KEY,
  tenant_id         UUID         NOT NULL REFERENCES tenants(tenant_id),
  source_entity_id  UUID         NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  target_entity_id  UUID         NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  kind              TEXT         NOT NULL,
  attributes        JSONB        NOT NULL DEFAULT '{}'::jsonb,
  evidence_packet   JSONB        NOT NULL,
  detector_id       TEXT         NOT NULL,
  detector_version  TEXT         NOT NULL,
  scan_id           UUID,
  first_seen_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  last_seen_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  UNIQUE (source_entity_id, target_entity_id, kind)
);

CREATE INDEX edges_tenant_idx ON edges(tenant_id);
CREATE INDEX edges_source_idx ON edges(source_entity_id);
CREATE INDEX edges_target_idx ON edges(target_entity_id);

-- Findings linkage
ALTER TABLE findings ADD COLUMN subject_entity_id UUID REFERENCES entities(id);
CREATE INDEX findings_subject_entity_idx ON findings(subject_entity_id)
  WHERE subject_entity_id IS NOT NULL;

COMMIT;
```

- [ ] **Step 2: Apply the migration to Aurora**

```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot --region us-east-1 \
  --sql "$(cat platform/sql/005_unified_entities.sql)"
```

Expected: `numberOfRecordsUpdated: 0` and no errors. Verify:

```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot --region us-east-1 \
  --sql "SELECT COUNT(*) FROM entities; SELECT COUNT(*) FROM edges; SELECT subject_entity_id FROM findings LIMIT 1;"
```
Expected: 0, 0, NULL (column exists).

- [ ] **Step 3: Commit**

```bash
git switch feat/sp1-unified-entities
git add platform/sql/005_unified_entities.sql
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "feat(sql): 005 unified entities + edges + findings.subject_entity_id"
```

---

### Task 2: `unified_writer.py` — entity upsert + tests

**Files:**
- Create: `platform/lambda/ai_scanner/unified_writer.py`
- Modify: `platform/lambda/ai_scanner/detectors/base.py`
- Create: `platform/lambda/ai_scanner/tests/test_unified_writer.py`

Detector emission types change shape; the writer becomes domain-agnostic. The asset_id_by_ref → natural_key map gets pre-seeded with the repo entity (regression for Slice 1b's writer bug).

- [ ] **Step 1: Update `detectors/base.py` with new emission types**

```python
# platform/lambda/ai_scanner/detectors/base.py
"""Detector emission types — domain-agnostic.

After SP1, detectors emit (kind, natural_key) pairs that the writer
dedupes on (tenant_id, kind, natural_key). The repo asset is no longer
implicit in source_repo_id — it's an entity referenced by natural_key
the same way every other entity is."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class EntityEmission:
    tenant_id:        str
    kind:             str           # 'ai_framework' | 'ai_model' | 'github_repo' | ...
    natural_key:      str           # per-kind canonical key — see spec §5
    display_name:     str
    domain:           str           # 'ai' | 'cloud' | 'repo' | 'identity' | 'asm'
    attributes:       dict[str, Any]
    evidence_packet:  dict[str, Any] | None
    detector_id:      str
    detector_version: str
    connection_id:    str | None = None
    source_path:      str | None = None   # optional, for UI / evidence


@dataclass(frozen=True)
class EdgeEmission:
    tenant_id:            str
    source_kind:          str        # entity kind of source
    source_natural_key:   str
    target_kind:          str
    target_natural_key:   str
    kind:                 str         # 'uses' | 'calls' | 'deploys_to' | ...
    attributes:           dict[str, Any]
    evidence_packet:      dict[str, Any]
    detector_id:          str
    detector_version:     str


@dataclass(frozen=True)
class FindingEmission:
    tenant_id:                str
    finding_type:             str         # 'unapproved_provider' | 'mcp_with_broad_perms' | ...
    severity:                 str         # 'critical' | 'high' | 'medium' | 'low' | 'info'
    title:                    str
    description:              str
    subject_entity_kind:      str | None  # link to entity (preferred) ...
    subject_entity_natural_key: str | None
    subject_type:             str | None  # ... or legacy free-text (for orphans)
    subject_ref:              str | None
    evidence_packet:          dict[str, Any]
    confidence:               str         # 'high' | 'medium' | 'low'


@dataclass(frozen=True)
class DetectorResult:
    entities:      list[EntityEmission] = field(default_factory=list)
    edges:         list[EdgeEmission]    = field(default_factory=list)
    findings:      list[FindingEmission] = field(default_factory=list)


class Detector(Protocol):
    detector_id:      str
    detector_version: str

    def detect(self, ctx: "Any") -> DetectorResult: ...
```

- [ ] **Step 2: Write the failing tests**

```python
# platform/lambda/ai_scanner/tests/test_unified_writer.py
"""Tests for unified_writer — transactional semantics, ON CONFLICT
RETURNING, stub support, and repo-root pre-seed regression."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def stub_env(monkeypatch):
    monkeypatch.setenv("DB_CLUSTER_ARN", "arn:db")
    monkeypatch.setenv("DB_SECRET_ARN",  "arn:secret")
    monkeypatch.setenv("DB_NAME",        "ciso_copilot")
    import boto3
    monkeypatch.setattr(boto3, "client", lambda _n, **_kw: MagicMock())


def _ctx():
    from scan_runner import ScanContext
    return ScanContext(
        scan_id="11111111-1111-1111-1111-111111111111",
        tenant_id="22222222-2222-2222-2222-222222222222",
        connection_id="33333333-3333-3333-3333-333333333333",
        repo_asset_id="44444444-4444-4444-4444-444444444444",
        repo_full_name="kk/foo", default_branch="main",
        head_commit_sha="abc123", installation_id=1,
        repo_workdir=Path("/tmp/x"),
    )


def _stub_rds(monkeypatch, persisted_id="aaaa1111-aaaa-aaaa-aaaa-aaaaaaaaaaaa"):
    """Stub Aurora Data API to return a deterministic id on every UPSERT
    so the writer's resolution map is predictable."""
    import unified_writer
    fake = MagicMock()
    fake.begin_transaction = lambda **kw: {"transactionId": "tx"}
    fake.commit_transaction = MagicMock()
    fake.rollback_transaction = MagicMock()
    calls = []
    def fake_execute(**kw):
        calls.append(kw)
        return {"records": [[{"stringValue": persisted_id}]]}
    fake.execute_statement = fake_execute
    monkeypatch.setattr(unified_writer, "_rds", fake)
    return fake, calls


def test_entity_upsert_returns_persisted_id(monkeypatch):
    """The writer must use the id returned by ON CONFLICT RETURNING — not
    the assigned UUID — so edges and findings get the correct FK."""
    import unified_writer
    from detectors.base import EntityEmission
    fake, calls = _stub_rds(monkeypatch, persisted_id="exists-already-id")

    e = EntityEmission(
        tenant_id="t1", kind="ai_framework", natural_key="langchain",
        display_name="langchain", domain="ai",
        attributes={"imports_seen": 2}, evidence_packet={"version": "0.1"},
        detector_id="ai.detectors.framework", detector_version="0.2.0",
    )
    unified_writer.commit_scan(_ctx(), entities=[e], edges=[], findings=[])

    # Last call should be the ai_scans UPDATE, before that the entity upsert
    upsert_calls = [c for c in calls if "INSERT INTO entities" in (c.get("sql") or "")]
    assert len(upsert_calls) == 1
    assert "RETURNING id::text" in upsert_calls[0]["sql"]


def test_edge_resolves_against_entity_emitted_in_same_scan(monkeypatch):
    import unified_writer
    from detectors.base import EntityEmission, EdgeEmission
    fake, calls = _stub_rds(monkeypatch)

    repo = EntityEmission(
        tenant_id="t1", kind="github_repo", natural_key="github.com/kk/foo",
        display_name="kk/foo", domain="repo",
        attributes={}, evidence_packet=None,
        detector_id="manual.repo_attach", detector_version="0.1.0",
    )
    fw = EntityEmission(
        tenant_id="t1", kind="ai_framework", natural_key="langchain",
        display_name="langchain", domain="ai",
        attributes={}, evidence_packet={"version": "0.1"},
        detector_id="ai.detectors.framework", detector_version="0.2.0",
    )
    edge = EdgeEmission(
        tenant_id="t1",
        source_kind="github_repo", source_natural_key="github.com/kk/foo",
        target_kind="ai_framework", target_natural_key="langchain",
        kind="uses", attributes={}, evidence_packet={"version": "0.1"},
        detector_id="ai.detectors.framework", detector_version="0.2.0",
    )
    unified_writer.commit_scan(_ctx(), entities=[repo, fw], edges=[edge], findings=[])

    edge_calls = [c for c in calls if "INSERT INTO edges" in (c.get("sql") or "")]
    assert len(edge_calls) == 1, "edge was dropped — resolution failed"


def test_cross_scan_edge_creates_stub_entity(monkeypatch):
    """When an edge points at an entity the current scan did NOT emit,
    the writer should create a stub entity and link to it (so cross-domain
    edges work the day they're emitted, even before the other scanner runs)."""
    import unified_writer
    from detectors.base import EntityEmission, EdgeEmission
    fake, calls = _stub_rds(monkeypatch)

    repo = EntityEmission(
        tenant_id="t1", kind="github_repo", natural_key="github.com/kk/foo",
        display_name="kk/foo", domain="repo",
        attributes={}, evidence_packet=None,
        detector_id="manual.repo_attach", detector_version="0.1.0",
    )
    edge = EdgeEmission(
        tenant_id="t1",
        source_kind="github_repo", source_natural_key="github.com/kk/foo",
        target_kind="aws_iam_role",
        target_natural_key="arn:aws:iam::470226123496:role/Deploy",
        kind="deploys_to", attributes={}, evidence_packet={"version": "0.1"},
        detector_id="ai.detectors.crossdomain", detector_version="0.1.0",
    )
    unified_writer.commit_scan(_ctx(), entities=[repo], edges=[edge], findings=[])

    stub_calls = [c for c in calls
                  if "INSERT INTO entities" in (c.get("sql") or "")
                  and any(p.get("name") == "stub" and p.get("value", {}).get("booleanValue") is True
                          for p in c.get("parameters") or [])]
    assert len(stub_calls) == 1, "stub entity was not created for the cross-domain target"


def test_rollback_on_error(monkeypatch):
    import unified_writer
    from detectors.base import EntityEmission
    fake = MagicMock()
    fake.begin_transaction = lambda **kw: {"transactionId": "tx"}
    fake.commit_transaction = MagicMock()
    fake.rollback_transaction = MagicMock()
    def boom(**kw): raise RuntimeError("boom")
    fake.execute_statement = boom
    monkeypatch.setattr(unified_writer, "_rds", fake)

    e = EntityEmission(
        tenant_id="t1", kind="ai_framework", natural_key="x",
        display_name="x", domain="ai", attributes={}, evidence_packet={},
        detector_id="d", detector_version="0.1",
    )
    with pytest.raises(RuntimeError, match="boom"):
        unified_writer.commit_scan(_ctx(), entities=[e], edges=[], findings=[])

    fake.rollback_transaction.assert_called_once()
    fake.commit_transaction.assert_not_called()
```

Run; confirm failures:

```bash
/Users/kkmookhey/venv/bin/pytest platform/lambda/ai_scanner/tests/test_unified_writer.py -v
# Expected: ImportError on unified_writer
```

- [ ] **Step 3: Implement `unified_writer.py`**

```python
# platform/lambda/ai_scanner/unified_writer.py
"""Transactional writes to entities / edges / findings / ai_scans.

Public surface:
  commit_scan(ctx, entities=, edges=, findings=) -> None
  mark_scan_failed(ctx, error_message) -> None

Key semantics:
  - Entity UPSERT uses ON CONFLICT (tenant_id, kind, natural_key) DO UPDATE
    SET last_seen_at=NOW(), ... RETURNING id::text. The returned id is the
    PERSISTED id (existing row on conflict, new row on insert). Always use
    the returned id; never trust the client-side UUID after a possible
    conflict. (Spec §9.3 regression #2.)
  - Edges resolve target/source by (kind, natural_key). If an edge points
    at an entity NOT emitted in this scan AND not yet in the table, a stub
    entity is created with attributes={'_stub': true}. (Spec §9.2.)
  - Findings link to entities via subject_entity_id when the detector
    provides (subject_entity_kind, subject_entity_natural_key). NULL FK
    is allowed for legacy / unresolvable findings.
"""
from __future__ import annotations

import json
import os
import uuid

import boto3

from detectors.base import EntityEmission, EdgeEmission, FindingEmission

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

_rds = boto3.client("rds-data")


def commit_scan(ctx, *,
                entities: list[EntityEmission],
                edges:    list[EdgeEmission],
                findings: list[FindingEmission]) -> None:
    tx = _rds.begin_transaction(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
    )["transactionId"]
    try:
        id_by_key: dict[tuple[str, str], str] = {}
        for e in entities:
            persisted_id = _upsert_entity(tx, e, ctx.tenant_id, scan_id=ctx.scan_id, stub=False)
            id_by_key[(e.kind, e.natural_key)] = persisted_id

        for edge in edges:
            src_id = _resolve_or_stub(tx, ctx.tenant_id, edge.source_kind,
                                       edge.source_natural_key, ctx.scan_id, id_by_key)
            tgt_id = _resolve_or_stub(tx, ctx.tenant_id, edge.target_kind,
                                       edge.target_natural_key, ctx.scan_id, id_by_key)
            _upsert_edge(tx, edge, src_id, tgt_id, scan_id=ctx.scan_id)

        for f in findings:
            entity_id = None
            if f.subject_entity_kind and f.subject_entity_natural_key:
                entity_id = _resolve_or_stub(tx, ctx.tenant_id, f.subject_entity_kind,
                                              f.subject_entity_natural_key, ctx.scan_id, id_by_key)
            _insert_finding(tx, f, entity_id, scan_id=ctx.scan_id, ctx=ctx)

        _update_scan(tx, ctx, len(entities), len(edges), len(findings), status="success")

        _rds.commit_transaction(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, transactionId=tx,
        )
    except Exception:
        _rds.rollback_transaction(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, transactionId=tx,
        )
        raise


def mark_scan_failed(ctx, error_message: str) -> None:
    _rds.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "UPDATE ai_scans SET status='failed', completed_at=NOW(), "
            "error_message=:msg WHERE id=CAST(:id AS UUID)"
        ),
        parameters=[
            {"name": "id",  "value": {"stringValue": ctx.scan_id}},
            {"name": "msg", "value": {"stringValue": error_message[:1000]}},
        ],
    )


# ---- internal write helpers ------------------------------------------------

def _upsert_entity(tx: str, e: EntityEmission | None,
                    tenant_id: str | None = None, *,
                    scan_id: str | None = None, stub: bool = False,
                    kind: str | None = None, natural_key: str | None = None,
                    display_name: str | None = None, domain: str | None = None) -> str:
    """Upsert and return the PERSISTED id. Either pass a full EntityEmission,
    OR pass stub=True with (tenant_id, kind, natural_key, display_name, domain)
    to create a placeholder stub for cross-domain edges."""
    if e is not None:
        params = {
            "id":     str(uuid.uuid4()),
            "tid":    e.tenant_id,
            "kind":   e.kind,
            "nk":     e.natural_key,
            "name":   e.display_name,
            "dom":    e.domain,
            "attrs":  json.dumps({**e.attributes, "_stub": False} if stub else e.attributes),
            "ev":     json.dumps(e.evidence_packet) if e.evidence_packet else None,
            "did":    e.detector_id,
            "dver":   e.detector_version,
            "sid":    scan_id,
        }
    else:
        params = {
            "id":     str(uuid.uuid4()),
            "tid":    tenant_id,
            "kind":   kind,
            "nk":     natural_key,
            "name":   display_name,
            "dom":    domain,
            "attrs":  json.dumps({"_stub": True}),
            "ev":     None,
            "did":    "manual.stub",
            "dver":   "0.1.0",
            "sid":    scan_id,
        }
    result = _rds.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        transactionId=tx,
        sql=(
            "INSERT INTO entities "
            "  (id, tenant_id, kind, natural_key, display_name, domain, "
            "   attributes, evidence_packet, detector_id, detector_version, scan_id) "
            "VALUES (CAST(:id AS UUID), CAST(:tid AS UUID), :kind, :nk, :name, :dom, "
            "        CAST(:attrs AS JSONB), "
            "        CASE WHEN :ev IS NULL THEN NULL ELSE CAST(:ev AS JSONB) END, "
            "        :did, :dver, "
            "        CASE WHEN :sid IS NULL THEN NULL ELSE CAST(:sid AS UUID) END) "
            "ON CONFLICT (tenant_id, kind, natural_key) "
            "  DO UPDATE SET last_seen_at=NOW(), "
            "                attributes=COALESCE(EXCLUDED.attributes - '_stub', entities.attributes), "
            "                evidence_packet=COALESCE(EXCLUDED.evidence_packet, entities.evidence_packet), "
            "                display_name=EXCLUDED.display_name "
            "RETURNING id::text"
        ),
        parameters=[
            {"name": "id",    "value": {"stringValue": params["id"]}},
            {"name": "tid",   "value": {"stringValue": params["tid"]}},
            {"name": "kind",  "value": {"stringValue": params["kind"]}},
            {"name": "nk",    "value": {"stringValue": params["nk"]}},
            {"name": "name",  "value": {"stringValue": params["name"]}},
            {"name": "dom",   "value": {"stringValue": params["dom"]}},
            {"name": "attrs", "value": {"stringValue": params["attrs"]}},
            {"name": "ev",
             "value": {"isNull": True} if params["ev"] is None
                      else {"stringValue": params["ev"]}},
            {"name": "did",   "value": {"stringValue": params["did"]}},
            {"name": "dver",  "value": {"stringValue": params["dver"]}},
            {"name": "sid",
             "value": {"isNull": True} if params["sid"] is None
                      else {"stringValue": params["sid"]}},
            {"name": "stub",  "value": {"booleanValue": bool(stub)}},
        ],
    )
    rows = result.get("records", [])
    if rows and rows[0] and "stringValue" in rows[0][0]:
        return rows[0][0]["stringValue"]
    return params["id"]


def _resolve_or_stub(tx, tenant_id, kind, natural_key, scan_id,
                     id_by_key: dict[tuple[str, str], str]) -> str:
    """Look up the entity id for (kind, natural_key). First check the
    in-scan map; then upsert a stub if absent. Returns the entity id."""
    if (kind, natural_key) in id_by_key:
        return id_by_key[(kind, natural_key)]
    persisted_id = _upsert_entity(
        tx, e=None, tenant_id=tenant_id, scan_id=scan_id, stub=True,
        kind=kind, natural_key=natural_key,
        display_name=natural_key, domain=_domain_for(kind),
    )
    id_by_key[(kind, natural_key)] = persisted_id
    return persisted_id


def _domain_for(kind: str) -> str:
    if kind.startswith("ai_"):     return "ai"
    if kind.startswith("aws_"):    return "cloud"
    if kind.startswith("azure_"):  return "cloud"
    if kind.startswith("gcp_"):    return "cloud"
    if kind.startswith("entra_"):  return "identity"
    if kind.startswith("github_"): return "repo"
    return "asm"


def _upsert_edge(tx: str, e: EdgeEmission, source_id: str, target_id: str,
                 scan_id: str | None) -> None:
    _rds.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        transactionId=tx,
        sql=(
            "INSERT INTO edges "
            "  (id, tenant_id, source_entity_id, target_entity_id, kind, "
            "   attributes, evidence_packet, detector_id, detector_version, scan_id) "
            "VALUES (CAST(:id AS UUID), CAST(:tid AS UUID), CAST(:src AS UUID), "
            "        CAST(:tgt AS UUID), :kind, CAST(:attrs AS JSONB), CAST(:ev AS JSONB), "
            "        :did, :dver, "
            "        CASE WHEN :sid IS NULL THEN NULL ELSE CAST(:sid AS UUID) END) "
            "ON CONFLICT (source_entity_id, target_entity_id, kind) "
            "  DO UPDATE SET last_seen_at=NOW(), evidence_packet=EXCLUDED.evidence_packet, "
            "                attributes=EXCLUDED.attributes"
        ),
        parameters=[
            {"name": "id",    "value": {"stringValue": str(uuid.uuid4())}},
            {"name": "tid",   "value": {"stringValue": e.tenant_id}},
            {"name": "src",   "value": {"stringValue": source_id}},
            {"name": "tgt",   "value": {"stringValue": target_id}},
            {"name": "kind",  "value": {"stringValue": e.kind}},
            {"name": "attrs", "value": {"stringValue": json.dumps(e.attributes)}},
            {"name": "ev",    "value": {"stringValue": json.dumps(e.evidence_packet)}},
            {"name": "did",   "value": {"stringValue": e.detector_id}},
            {"name": "dver",  "value": {"stringValue": e.detector_version}},
            {"name": "sid",
             "value": {"isNull": True} if scan_id is None
                      else {"stringValue": scan_id}},
        ],
    )


def _insert_finding(tx, f: FindingEmission, entity_id: str | None,
                     scan_id: str, ctx) -> None:
    fid = str(uuid.uuid4())
    _rds.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        transactionId=tx,
        sql=(
            "INSERT INTO findings "
            "  (finding_id, tenant_id, conn_id, scan_id, check_id, title, description, "
            "   severity, status, resource_arn, resource_type, region, domain, frameworks, "
            "   remediation, first_seen, last_seen, evidence_packet, subject_entity_id) "
            "VALUES (CAST(:fid AS UUID), CAST(:tid AS UUID), CAST(:conn AS UUID), "
            "        CAST(:sid AS UUID), :ftype, :title, :desc, :sev, 'fail', :subj, "
            "        :stype, NULL, 'ai', '{}'::jsonb, NULL, NOW(), NOW(), CAST(:ev AS JSONB), "
            "        CASE WHEN :eid IS NULL THEN NULL ELSE CAST(:eid AS UUID) END)"
        ),
        parameters=[
            {"name": "fid",   "value": {"stringValue": fid}},
            {"name": "tid",   "value": {"stringValue": f.tenant_id}},
            {"name": "conn",  "value": {"stringValue": ctx.connection_id}},
            {"name": "sid",   "value": {"stringValue": scan_id}},
            {"name": "ftype", "value": {"stringValue": f.finding_type}},
            {"name": "title", "value": {"stringValue": f.title}},
            {"name": "desc",  "value": {"stringValue": f.description}},
            {"name": "sev",   "value": {"stringValue": f.severity}},
            {"name": "subj",  "value": {"stringValue": f.subject_ref or ""}},
            {"name": "stype", "value": {"stringValue": f.subject_type or "ai_module"}},
            {"name": "ev",    "value": {"stringValue": json.dumps(f.evidence_packet)}},
            {"name": "eid",
             "value": {"isNull": True} if entity_id is None
                      else {"stringValue": entity_id}},
        ],
    )


def _update_scan(tx, ctx, entity_count, edge_count, finding_count, status):
    _rds.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        transactionId=tx,
        sql=(
            "UPDATE ai_scans SET status=:st, completed_at=NOW(), "
            "  assets_discovered_count=:ac, relationships_discovered_count=:rc, "
            "  findings_generated_count=:fc, scanner_version=:sv "
            "WHERE id = CAST(:sid AS UUID)"
        ),
        parameters=[
            {"name": "st",  "value": {"stringValue": status}},
            {"name": "ac",  "value": {"longValue":   entity_count}},
            {"name": "rc",  "value": {"longValue":   edge_count}},
            {"name": "fc",  "value": {"longValue":   finding_count}},
            {"name": "sv",  "value": {"stringValue": ctx.scanner_version}},
            {"name": "sid", "value": {"stringValue": ctx.scan_id}},
        ],
    )
```

- [ ] **Step 4: Run tests to verify pass**

```bash
/Users/kkmookhey/venv/bin/pytest platform/lambda/ai_scanner/tests/test_unified_writer.py -v
# Expected: 4 passed
```

- [ ] **Step 5: Commit**

```bash
git switch feat/sp1-unified-entities
git add platform/lambda/ai_scanner/unified_writer.py \
        platform/lambda/ai_scanner/detectors/base.py \
        platform/lambda/ai_scanner/tests/test_unified_writer.py
git rm platform/lambda/ai_scanner/writer.py platform/lambda/ai_scanner/tests/test_writer.py
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "feat(platform): unified_writer — entities/edges/findings with stubs + tests"
```

---

### Task 3: Update `tests/test_detectors.py` sort keys

**Files:**
- Modify: `platform/lambda/ai_scanner/tests/test_detectors.py`

Detectors now emit entities/edges instead of assets/rels; the test runner's `_normalise()` sort keys need updating.

- [ ] **Step 1: Update sort keys**

Replace the body of `_normalise()`:

```python
def _normalise(result):
    def strip_dynamic(p):
        if p is None:
            return None
        p = {**p}
        p.pop("packet_id", None)
        p.pop("produced_at", None)
        if "subject" in p:
            p["subject"] = {**p["subject"]}
            p["subject"].pop("id", None)
        return p
    return {
        "entities": [
            {**asdict(e), "evidence_packet": strip_dynamic(e.evidence_packet)}
            for e in sorted(result.entities, key=lambda x: (x.kind, x.natural_key, x.source_path or ""))
        ],
        "edges": [
            {**asdict(r), "evidence_packet": strip_dynamic(r.evidence_packet)}
            for r in sorted(result.edges, key=lambda x: (x.kind, x.source_natural_key, x.target_natural_key))
        ],
        "findings": [
            {**asdict(f), "evidence_packet": strip_dynamic(f.evidence_packet)}
            for f in sorted(result.findings, key=lambda x: (x.finding_type, x.subject_ref or x.subject_entity_natural_key or ""))
        ],
    }
```

- [ ] **Step 2: Don't run yet — fixtures will be regenerated per detector in Tasks 4-12. Commit the runner change alone.**

```bash
git switch feat/sp1-unified-entities
git add platform/lambda/ai_scanner/tests/test_detectors.py
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "test(platform): update detector test runner for entity/edge sort keys"
```

---

## Phase B — Refactor ai_scanner detectors (9 tasks, mechanical)

Each detector gets the same shape change. Pattern is established in Task 4 (`framework`); subsequent detectors apply it.

### Task 4: Rewrite `framework.py` (pattern-setter)

**Files:**
- Modify: `platform/lambda/ai_scanner/detectors/framework.py`
- Modify: `platform/lambda/ai_scanner/tests/fixtures/framework/*/expected.json` (regenerate)

- [ ] **Step 1: Update `framework.py`**

```python
# platform/lambda/ai_scanner/detectors/framework.py
"""Detect AI-framework imports (langchain, langgraph, llama_index, ...)."""
from __future__ import annotations

from detectors._walk import ripgrep
from detectors.base import EntityEmission, EdgeEmission, DetectorResult
import evidence as ev

detector_id      = "ai.detectors.framework"
detector_version = "0.2.0"

FRAMEWORKS = [
    "langchain", "langgraph", "llama_index", "llama_cpp", "crewai",
    "autogen", "semantic_kernel", "dspy",
]


def detect(ctx) -> DetectorResult:
    entities: list[EntityEmission] = []
    edges:    list[EdgeEmission] = []
    repo_natural_key = f"github.com/{ctx.repo_full_name}"

    for fw in FRAMEWORKS:
        pattern = rf"^\s*(from|import)\s+{fw}(\b|\.)"
        matches = ripgrep(ctx.repo_workdir, pattern, types=["py"])
        if not matches:
            continue
        matches.sort(key=lambda m: (str(m[0]), m[1]))
        first_path, first_line, first_snippet = matches[0]
        rel_path = str(first_path.relative_to(ctx.repo_workdir))

        packet = ev.build(
            detector_id=detector_id, detector_version=detector_version,
            subject_kind="ai_asset", subject_type="ai_framework", subject_name=fw,
            source_events=[{
                "kind": "file", "repo": ctx.repo_full_name,
                "commit_sha": ctx.head_commit_sha,
                "path": rel_path, "snippet_lines": [first_line, first_line],
                "snippet": first_snippet,
            }],
            reasoning_chain=[f"matched {fw} import on {rel_path}:{first_line}"],
            confidence="high",
        )
        entities.append(EntityEmission(
            tenant_id=ctx.tenant_id, kind="ai_framework",
            natural_key=fw, display_name=fw, domain="ai",
            attributes={"imports_seen": len(matches)},
            evidence_packet=packet,
            detector_id=detector_id, detector_version=detector_version,
            connection_id=ctx.connection_id, source_path=rel_path,
        ))

        rel_packet = ev.build(
            detector_id=detector_id, detector_version=detector_version,
            subject_kind="ai_relationship", subject_type="uses",
            subject_name=f"repo→uses→{fw}",
            source_events=[], reasoning_chain=["framework detected in repo"],
            confidence="high",
        )
        edges.append(EdgeEmission(
            tenant_id=ctx.tenant_id,
            source_kind="github_repo", source_natural_key=repo_natural_key,
            target_kind="ai_framework", target_natural_key=fw,
            kind="uses", attributes={}, evidence_packet=rel_packet,
            detector_id=detector_id, detector_version=detector_version,
        ))

    return DetectorResult(entities=entities, edges=edges, findings=[])
```

- [ ] **Step 2: Regenerate goldens with the probe pattern**

```bash
/Users/kkmookhey/venv/bin/python <<'EOF'
import sys, os, json
sys.path.insert(0, 'platform/lambda/ai_scanner')
os.environ['GITHUB_APP_SECRET_ARN'] = 'arn:test'
from pathlib import Path
from dataclasses import asdict
from scan_runner import ScanContext
import detectors.framework as p

def strip(pkt):
    if pkt is None: return None
    p = {**pkt}; p.pop('packet_id', None); p.pop('produced_at', None)
    p['subject'] = {k: v for k, v in p['subject'].items() if k != 'id'}
    return p

for fx in ['langchain_in_repo', 'no_framework']:
    ctx = ScanContext(
        scan_id='11111111-1111-1111-1111-111111111111',
        tenant_id='22222222-2222-2222-2222-222222222222',
        connection_id='33333333-3333-3333-3333-333333333333',
        repo_asset_id='44444444-4444-4444-4444-444444444444',
        repo_full_name='fixture/repo', default_branch='main',
        head_commit_sha='fixture-sha', installation_id=0,
        repo_workdir=Path(f'platform/lambda/ai_scanner/tests/fixtures/framework/{fx}/repo'),
    )
    r = p.detect(ctx)
    out = {
        'entities': sorted([{**asdict(e), 'evidence_packet': strip(e.evidence_packet)}
                            for e in r.entities],
                            key=lambda x: (x['kind'], x['natural_key'], x['source_path'] or '')),
        'edges': sorted([{**asdict(e), 'evidence_packet': strip(e.evidence_packet)}
                          for e in r.edges],
                          key=lambda x: (x['kind'], x['source_natural_key'], x['target_natural_key'])),
        'findings': [],
    }
    Path(f'platform/lambda/ai_scanner/tests/fixtures/framework/{fx}/expected.json').write_text(
        json.dumps(out, indent=2) + '\n')
    print(f'wrote {fx}')
EOF
/Users/kkmookhey/venv/bin/pytest platform/lambda/ai_scanner/tests/test_detectors.py -k framework -v
```
Expected: 2 passed.

- [ ] **Step 3: Commit**

```bash
git switch feat/sp1-unified-entities
git add platform/lambda/ai_scanner/detectors/framework.py \
        platform/lambda/ai_scanner/tests/fixtures/framework/
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "refactor(platform): framework detector — EntityEmission/EdgeEmission + framework dedup"
```

---

### Task 5: Rewrite `model_usage.py`

**Same pattern as Task 4.** Apply these transforms:

1. Imports change `AssetEmission, RelEmission` → `EntityEmission, EdgeEmission`.
2. `detector_version` bumped to `"0.3.0"`.
3. `repo_natural_key = f"github.com/{ctx.repo_full_name}"`.
4. For each match, emit:
   ```python
   entities.append(EntityEmission(
       tenant_id=ctx.tenant_id, kind="ai_model",
       natural_key=f"{resolved}/{model_id}",
       display_name=f"{resolved}/{model_id}",
       domain="ai",
       attributes={"provider": resolved, "model_id": model_id},
       evidence_packet=packet,
       detector_id=detector_id, detector_version=detector_version,
       connection_id=ctx.connection_id, source_path=rel_path,
   ))
   edges.append(EdgeEmission(
       tenant_id=ctx.tenant_id,
       source_kind="github_repo", source_natural_key=repo_natural_key,
       target_kind="ai_model", target_natural_key=f"{resolved}/{model_id}",
       kind="calls",
       attributes={"provider": resolved}, evidence_packet=rel_packet,
       detector_id=detector_id, detector_version=detector_version,
   ))
   ```
5. Return `DetectorResult(entities=entities, edges=edges, findings=[])`.

- [ ] **Step 1: Apply transforms to `detectors/model_usage.py`**
- [ ] **Step 2: Regenerate goldens** for `openai_calls`, `anthropic_calls`, `bedrock_calls` using the probe pattern from Task 4 (substitute fixture names).
- [ ] **Step 3: Run `pytest -k model_usage`** — expect 3 passed.
- [ ] **Step 4: Commit**

```bash
git add platform/lambda/ai_scanner/detectors/model_usage.py \
        platform/lambda/ai_scanner/tests/fixtures/model_usage/
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "refactor(platform): model_usage detector — EntityEmission/EdgeEmission, ai_model dedup across repos"
```

---

### Task 6: Rewrite `mcp_server.py`

Same pattern. Specifics for this detector:

- `mcp_server` natural_key: `f"{repo_natural_key}::{rel_path}::{server_name}"` (per-file scope).
- `tool` natural_key: `f"{repo_natural_key}::{rel_path}::{tool_name}"` (per-file scope, scoped to the server's file).
- Edges:
  ```python
  # repo → deploys → mcp_server
  EdgeEmission(source_kind="github_repo", source_natural_key=repo_natural_key,
               target_kind="ai_mcp_server", target_natural_key=mcp_nk,
               kind="deploys", ...)
  # mcp_server → invokes → tool
  EdgeEmission(source_kind="ai_mcp_server", source_natural_key=mcp_nk,
               target_kind="ai_tool", target_natural_key=tool_nk,
               kind="invokes", ...)
  ```
- Findings: `mcp_with_broad_perms` now sets `subject_entity_kind="ai_mcp_server"`, `subject_entity_natural_key=mcp_nk` (drop `subject_ref` legacy field — leave it None).

- [ ] Steps 1-4 mirror Task 4 (apply transforms, regenerate `python_server` + `mcp_json` goldens, run tests, commit).

```bash
git add platform/lambda/ai_scanner/detectors/mcp_server.py \
        platform/lambda/ai_scanner/tests/fixtures/mcp_server/
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "refactor(platform): mcp_server detector — EntityEmission/EdgeEmission + per-file natural_key"
```

---

### Task 7: Rewrite `agentic_workflow.py`

- `ai_agent` natural_key: `f"{repo_natural_key}::{rel_path}::{fn_name}"`.
- No edges from this detector — agent→model orchestration lives in the correlator (Task 12).
- Finding `autonomous_loop_no_human_in_loop` sets `subject_entity_kind="ai_agent"`, `subject_entity_natural_key=agent_nk`.

- [ ] Steps 1-4 mirror Task 4 (apply transforms, regen `agent_loop` + `just_llm` goldens, test, commit).

```bash
git add platform/lambda/ai_scanner/detectors/agentic_workflow.py \
        platform/lambda/ai_scanner/tests/fixtures/agentic_workflow/
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "refactor(platform): agentic_workflow detector — EntityEmission"
```

---

### Task 8: Rewrite `vector_db.py`

- `ai_vector_db` natural_key: bare name (e.g., `"chromadb"`, `"pgvector"`).
- Edge: `github_repo → retrieves → ai_vector_db`.

- [ ] Steps mirror Task 4. Regen `chromadb_import`, `pgvector_sql`, `no_vector_db` goldens.

```bash
git add platform/lambda/ai_scanner/detectors/vector_db.py \
        platform/lambda/ai_scanner/tests/fixtures/vector_db/
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "refactor(platform): vector_db detector — EntityEmission + dedup by name"
```

---

### Task 9: Rewrite `embedding.py`

- `ai_embedding` natural_key: `f"{provider}/{model_id}"` (matches model_usage pattern).
- Edge: `github_repo → generates → ai_embedding`.

- [ ] Steps mirror Task 4. Regen `openai_embed`, `no_embedding` goldens.

```bash
git add platform/lambda/ai_scanner/detectors/embedding.py \
        platform/lambda/ai_scanner/tests/fixtures/embedding/
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "refactor(platform): embedding detector — EntityEmission"
```

---

### Task 10: Rewrite `prompt.py`

- `ai_prompt` natural_key: `f"{repo_natural_key}::{rel_path}::{name}"` (per-file).
- Edge: `github_repo → accesses → ai_prompt`.
- Finding `prompt_with_secret_pattern` sets `subject_entity_kind="ai_prompt"`, `subject_entity_natural_key=prompt_nk`.

- [ ] Steps mirror Task 4. Regen `prompt_file`, `prompt_with_secret`, `no_prompts` goldens.

```bash
git add platform/lambda/ai_scanner/detectors/prompt.py \
        platform/lambda/ai_scanner/tests/fixtures/prompt/
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "refactor(platform): prompt detector — EntityEmission"
```

---

### Task 11: Rewrite `secrets_in_ai_code.py`

This detector emits only findings, no entities/edges. The finding gets `subject_type="ai_module"` and `subject_ref=f"{rel_path}:{line}"` (no entity linkage — secrets in code don't have a natural entity in our taxonomy yet).

- [ ] Steps mirror Task 4. Regen `secret_in_ai_module`, `secret_without_sdk` goldens.

```bash
git add platform/lambda/ai_scanner/detectors/secrets_in_ai_code.py \
        platform/lambda/ai_scanner/tests/fixtures/secrets_in_ai_code/
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "refactor(platform): secrets_in_ai_code — emit FindingEmission with new shape"
```

---

### Task 12: Rewrite `correlator.py`

Same shape change. Specifics:

- The correlator takes `results: list[DetectorResult]` and produces ADDITIONAL `EdgeEmission`s only.
- Group emissions by `source_path`. The natural_keys it references are produced by the upstream detectors (model_usage, mcp_server, agentic_workflow, vector_db, prompt) — use the SAME natural_key formulas.
- Patterns:
  - `ai_agent` + `ai_mcp_server` colocated → `EdgeEmission(source_kind="ai_agent", ..., target_kind="ai_mcp_server", ..., kind="invokes", confidence="medium")`
  - `ai_agent` + `ai_model` colocated → `kind="orchestrates"`
  - `ai_model` + `ai_vector_db` + `ai_prompt` colocated → `model → retrieves → vector_db`

- [ ] Steps 1-4 mirror Task 4. The unit tests are in `tests/test_correlator.py` — update fixture builders to use `EntityEmission` instead of `AssetEmission`. Re-run.

```bash
git add platform/lambda/ai_scanner/detectors/correlator.py \
        platform/lambda/ai_scanner/tests/test_correlator.py
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "refactor(platform): correlator — EdgeEmission with kind-based references"
```

---

### Task 13: Wire `main.py` to use `unified_writer` + emit `github_repo`

**Files:**
- Modify: `platform/lambda/ai_scanner/main.py`
- Modify: `platform/lambda/ai_scanner/tests/test_scan_runner.py`

The handler now:
1. Always emits a `github_repo` entity at the start of each scan (with `natural_key=f"github.com/{ctx.repo_full_name}"`).
2. Calls `unified_writer.commit_scan(ctx, entities=..., edges=..., findings=...)`.
3. Aggregates detector results + correlator into the three lists.

- [ ] **Step 1: Update `main.py`**

```python
# platform/lambda/ai_scanner/main.py
"""SQS-triggered handler for the AI scanner Lambda. Spec §9."""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path

import scan_runner
import unified_writer
from detectors import (
    framework, model_usage, mcp_server, agentic_workflow,
    vector_db, embedding, prompt, secrets_in_ai_code, correlator,
    crossdomain,
)
from detectors.base import EntityEmission

logging.basicConfig(level=logging.INFO, force=True)
log = logging.getLogger("ai_scanner")

DETECTORS = [
    framework, model_usage, mcp_server, agentic_workflow,
    vector_db, embedding, prompt, secrets_in_ai_code, crossdomain,
]


def handler(event, context):
    records = event.get("Records") or []
    print(f"[ai_scanner] invoked with {len(records)} record(s)")
    for r in records:
        body = json.loads(r.get("body") or "{}")
        _run_one(body)
    return {"statusCode": 200, "body": json.dumps({"scans_processed": len(records)})}


def _run_one(body):
    scan_id = body["scan_id"]
    workdir = Path(tempfile.gettempdir()) / f"scan-{scan_id}"
    print(f"[ai_scanner] scan {scan_id} repo={body.get('repo_full_name')} "
          f"branch={body.get('default_branch')} workdir={workdir}")
    if workdir.exists():
        shutil.rmtree(workdir, ignore_errors=True)

    ctx = None
    try:
        sha = scan_runner.clone_repo(
            installation_id=body["installation_id"],
            repo_full_name=body["repo_full_name"],
            default_branch=body["default_branch"],
            workdir=workdir,
        )
        ctx = scan_runner.ScanContext.from_message(body, workdir, sha)
        py_count = sum(1 for _ in workdir.rglob("*.py"))
        print(f"[ai_scanner] cloned {ctx.repo_full_name}@{sha} ({py_count} .py files)")

        results = []
        for det in DETECTORS:
            r = det.detect(ctx)
            print(f"[ai_scanner]   {det.detector_id}: "
                  f"{len(r.entities)} entities, {len(r.edges)} edges, {len(r.findings)} findings")
            results.append(r)
        corr = correlator.correlate(ctx, results)

        repo_entity = EntityEmission(
            tenant_id=ctx.tenant_id, kind="github_repo",
            natural_key=f"github.com/{ctx.repo_full_name}",
            display_name=ctx.repo_full_name, domain="repo",
            attributes={"default_branch": ctx.default_branch,
                         "head_commit_sha": ctx.head_commit_sha},
            evidence_packet=None,
            detector_id="manual.repo_attach", detector_version="0.1.0",
            connection_id=ctx.connection_id,
        )

        all_entities = [repo_entity] + [e for r in results for e in r.entities] + corr.entities
        all_edges    = [e for r in results for e in r.edges] + corr.edges
        all_findings = [f for r in results for f in r.findings] + corr.findings

        unified_writer.commit_scan(ctx,
                                    entities=all_entities,
                                    edges=all_edges,
                                    findings=all_findings)
        print(f"[ai_scanner] scan {scan_id} committed: "
              f"{len(all_entities)} entities, {len(all_edges)} edges, {len(all_findings)} findings")

    except scan_runner.RepoTooLarge as e:
        print(f"[ai_scanner] scan {scan_id} aborted: repo too large")
        if ctx is None:
            ctx = scan_runner.ScanContext.from_message(body, workdir, head_commit_sha="")
        unified_writer.mark_scan_failed(ctx, f"clone_too_large: {e}")
    except Exception as e:
        log.exception(f"scan {scan_id} failed")
        if ctx is not None:
            unified_writer.mark_scan_failed(ctx, f"{type(e).__name__}: {e}")
        raise
    finally:
        if workdir.exists():
            shutil.rmtree(workdir, ignore_errors=True)
```

- [ ] **Step 2: Update `test_scan_runner.py`** — the E2E `test_handler_runs_full_scan_pipeline` test asserts `calls[0]["assets"] >= 1`. Change to `calls[0]["entities"] >= 1`, and update the writer stub to match the new `commit_scan` keyword signature.

- [ ] **Step 3: Run the full suite**

```bash
/Users/kkmookhey/venv/bin/pytest platform/lambda/ai_scanner/tests/ -v
# Expected: ~36 passed (8 detectors × goldens + correlator + unified_writer + scan_runner + evidence)
```

- [ ] **Step 4: Commit**

```bash
git add platform/lambda/ai_scanner/main.py \
        platform/lambda/ai_scanner/tests/test_scan_runner.py
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "feat(platform): ai_scanner main — emit github_repo + use unified_writer"
```

---

## Phase C — Cross-domain detector (1 task)

### Task 14: New `detectors/crossdomain.py` for GitHub Actions deploy detection

**Files:**
- Create: `platform/lambda/ai_scanner/detectors/crossdomain.py`
- Create: `platform/lambda/ai_scanner/tests/fixtures/crossdomain/with_oidc/repo/.github/workflows/deploy.yml`
- Create: `platform/lambda/ai_scanner/tests/fixtures/crossdomain/with_oidc/expected.json`
- Create: `platform/lambda/ai_scanner/tests/fixtures/crossdomain/no_workflows/repo/README.md`
- Create: `platform/lambda/ai_scanner/tests/fixtures/crossdomain/no_workflows/expected.json`

- [ ] **Step 1: Write the fixtures**

`tests/fixtures/crossdomain/with_oidc/repo/.github/workflows/deploy.yml`:

```yaml
name: Deploy
on: push
permissions:
  id-token: write
  contents: read
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::470226123496:role/GitHubActionsDeployRole
          aws-region: us-east-1
      - run: aws s3 cp dist/ s3://my-bucket/ --recursive
```

`tests/fixtures/crossdomain/no_workflows/repo/README.md`:

```
just a readme, no workflows here
```

`tests/fixtures/crossdomain/no_workflows/expected.json`:

```json
{"entities": [], "edges": [], "findings": []}
```

(`with_oidc/expected.json` will be regenerated in Step 3 after implementing.)

- [ ] **Step 2: Implement `crossdomain.py`**

```python
# platform/lambda/ai_scanner/detectors/crossdomain.py
"""Cross-domain detector: scan .github/workflows/*.yml for AWS OIDC
role-to-assume references, emit github_repo → deploys_to → aws_iam_role
edges. The target IAM role entity may not yet exist in the database (if
the cloud scanner hasn't run); unified_writer will create a stub for it."""
from __future__ import annotations

import re

from detectors.base import EdgeEmission, DetectorResult
import evidence as ev

detector_id      = "ai.detectors.crossdomain"
detector_version = "0.1.0"

ROLE_RE = re.compile(
    r'role-to-assume\s*:\s*["\']?(arn:aws:iam::\d+:role/[A-Za-z0-9+=,.@_\-]+)["\']?',
    re.IGNORECASE,
)


def detect(ctx) -> DetectorResult:
    edges: list[EdgeEmission] = []
    repo_natural_key = f"github.com/{ctx.repo_full_name}"

    workflows_dir = ctx.repo_workdir / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return DetectorResult()

    files = sorted([p for p in workflows_dir.iterdir()
                    if p.is_file() and p.suffix in (".yml", ".yaml")])
    for f in files:
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        for m in ROLE_RE.finditer(text):
            role_arn = m.group(1)
            line_no = text[:m.start()].count("\n") + 1
            packet = ev.build(
                detector_id=detector_id, detector_version=detector_version,
                subject_kind="ai_relationship", subject_type="deploys_to",
                subject_name=f"repo→deploys_to→{role_arn}",
                source_events=[{
                    "kind": "file", "repo": ctx.repo_full_name,
                    "commit_sha": ctx.head_commit_sha,
                    "path": str(f.relative_to(ctx.repo_workdir)),
                    "snippet_lines": [line_no, line_no],
                    "snippet": text.splitlines()[line_no - 1].strip(),
                }],
                reasoning_chain=[f"GitHub Actions workflow assumes {role_arn}"],
                confidence="medium",
            )
            edges.append(EdgeEmission(
                tenant_id=ctx.tenant_id,
                source_kind="github_repo", source_natural_key=repo_natural_key,
                target_kind="aws_iam_role", target_natural_key=role_arn,
                kind="deploys_to",
                attributes={"role_arn": role_arn},
                evidence_packet=packet,
                detector_id=detector_id, detector_version=detector_version,
            ))

    return DetectorResult(entities=[], edges=edges, findings=[])
```

- [ ] **Step 3: Generate the `with_oidc/expected.json`** using the probe pattern (substitute `detectors.crossdomain`, fixture path).

- [ ] **Step 4: Run tests**

```bash
/Users/kkmookhey/venv/bin/pytest platform/lambda/ai_scanner/tests/test_detectors.py -k crossdomain -v
# Expected: 2 passed
```

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/ai_scanner/detectors/crossdomain.py \
        platform/lambda/ai_scanner/tests/fixtures/crossdomain/
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "feat(platform): ai_scanner — crossdomain detector (GitHub Actions → AWS IAM role)"
```

---

## Phase D — shasta_runner refactor (5 tasks)

### Task 15: ARN-to-entity helper

**Files:**
- Create: `platform/lambda/shasta_runner/app/arn_to_entity.py`
- Create: `platform/lambda/shasta_runner/app/tests/test_arn_to_entity.py`

- [ ] **Step 1: Write the failing tests**

```python
# platform/lambda/shasta_runner/app/tests/test_arn_to_entity.py
import pytest

def test_s3_bucket():
    from arn_to_entity import parse_arn
    out = parse_arn("arn:aws:s3:::my-bucket")
    assert out == {"kind": "aws_s3_bucket", "natural_key": "arn:aws:s3:::my-bucket",
                   "display_name": "my-bucket", "attributes": {"service": "s3"}}

def test_iam_role():
    from arn_to_entity import parse_arn
    out = parse_arn("arn:aws:iam::123456789012:role/Foo")
    assert out["kind"] == "aws_iam_role"
    assert out["display_name"] == "Foo"
    assert out["attributes"]["account"] == "123456789012"

def test_ec2_instance():
    from arn_to_entity import parse_arn
    out = parse_arn("arn:aws:ec2:us-east-1:123456789012:instance/i-abc123")
    assert out["kind"] == "aws_ec2_instance"
    assert out["attributes"]["region"] == "us-east-1"
    assert out["display_name"] == "i-abc123"

def test_unknown_returns_none():
    from arn_to_entity import parse_arn
    assert parse_arn("arn:aws:weird:::") is None
    assert parse_arn("not-an-arn") is None
```

- [ ] **Step 2: Implement `arn_to_entity.py`**

```python
# platform/lambda/shasta_runner/app/arn_to_entity.py
"""Parse an AWS ARN into an entity emission shape."""
from __future__ import annotations

import re

# arn:aws:<service>:<region>:<account>:<resource_type>/<resource_id>
# or:  arn:aws:s3:::<bucket>           (s3 has empty region/account)
_ARN_RE = re.compile(r"^arn:aws:([^:]+):([^:]*):([^:]*):(.+)$")

_KIND_MAP = {
    ("s3",     "*"):     "aws_s3_bucket",
    ("iam",    "role"):  "aws_iam_role",
    ("iam",    "user"):  "aws_iam_user",
    ("ec2",    "instance"):       "aws_ec2_instance",
    ("ec2",    "vpc"):            "aws_vpc",
    ("ec2",    "subnet"):         "aws_subnet",
    ("ec2",    "security-group"): "aws_security_group",
    ("lambda", "function"):       "aws_lambda_function",
}


def parse_arn(arn: str) -> dict | None:
    m = _ARN_RE.match(arn)
    if not m:
        return None
    service, region, account, resource = m.groups()

    if "/" in resource:
        resource_type, resource_id = resource.split("/", 1)
    elif ":" in resource:
        resource_type, resource_id = resource.split(":", 1)
    else:
        resource_type, resource_id = "*", resource

    kind = _KIND_MAP.get((service, resource_type)) or _KIND_MAP.get((service, "*"))
    if kind is None:
        return None

    return {
        "kind": kind,
        "natural_key": arn,
        "display_name": resource_id or arn,
        "attributes": {
            "service": service,
            **({"region":  region}  if region  else {}),
            **({"account": account} if account else {}),
        },
    }
```

- [ ] **Step 3: Add `tests/__init__.py` + `tests/conftest.py`** if absent.

```python
# platform/lambda/shasta_runner/app/tests/conftest.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

- [ ] **Step 4: Run + commit**

```bash
/Users/kkmookhey/venv/bin/pytest platform/lambda/shasta_runner/app/tests/test_arn_to_entity.py -v
# Expected: 4 passed

git switch feat/sp1-unified-entities
git add platform/lambda/shasta_runner/app/arn_to_entity.py \
        platform/lambda/shasta_runner/app/tests/
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "feat(platform): shasta_runner — ARN parser for entity extraction"
```

---

### Task 16: IAM enumeration

**Files:**
- Create: `platform/lambda/shasta_runner/app/enumerate_iam.py`
- Create: `platform/lambda/shasta_runner/app/tests/test_enumerate_iam.py`

- [ ] **Step 1: Write tests with `botocore.stub.Stubber`**

```python
# platform/lambda/shasta_runner/app/tests/test_enumerate_iam.py
from datetime import datetime
import boto3
from botocore.stub import Stubber

def test_iam_enum_emits_roles_and_users():
    from enumerate_iam import enumerate_iam
    client = boto3.client("iam", region_name="us-east-1")
    with Stubber(client) as s:
        s.add_response("list_roles", {"Roles": [
            {"RoleName": "Foo", "Arn": "arn:aws:iam::123:role/Foo",
             "Path": "/", "RoleId": "AROAXYZ", "CreateDate": datetime.utcnow()},
        ]})
        s.add_response("list_users", {"Users": [
            {"UserName": "bob", "Arn": "arn:aws:iam::123:user/bob",
             "Path": "/", "UserId": "AIDAXYZ", "CreateDate": datetime.utcnow()},
        ]})
        result = enumerate_iam(client, account_id="123", tenant_id="t1")

    kinds = sorted(e.kind for e in result["entities"])
    assert "aws_iam_role" in kinds
    assert "aws_iam_user" in kinds
    # account → contains → role + account → contains → user
    assert any(e.kind == "contains" and e.target_kind == "aws_iam_role" for e in result["edges"])
```

- [ ] **Step 2: Implement `enumerate_iam.py`**

```python
# platform/lambda/shasta_runner/app/enumerate_iam.py
"""Enumerate IAM roles + users and emit entity/edge dataclasses for the
unified_writer to persist."""
from __future__ import annotations

from detectors.base import EntityEmission, EdgeEmission   # copied into Lambda

DETECTOR_ID      = "shasta_runner.iam"
DETECTOR_VERSION = "0.1.0"


def enumerate_iam(iam_client, *, account_id: str, tenant_id: str) -> dict:
    entities: list[EntityEmission] = []
    edges:    list[EdgeEmission]    = []

    account_nk = account_id
    paginator = iam_client.get_paginator("list_roles") if hasattr(iam_client, "get_paginator") else None
    role_iter = (paginator.paginate() if paginator else
                 [iam_client.list_roles()])
    for page in role_iter:
        for r in page["Roles"]:
            arn = r["Arn"]
            entities.append(EntityEmission(
                tenant_id=tenant_id, kind="aws_iam_role",
                natural_key=arn, display_name=r["RoleName"], domain="cloud",
                attributes={"path": r.get("Path", "/"), "role_id": r["RoleId"]},
                evidence_packet=None,
                detector_id=DETECTOR_ID, detector_version=DETECTOR_VERSION,
            ))
            edges.append(EdgeEmission(
                tenant_id=tenant_id,
                source_kind="aws_account", source_natural_key=account_nk,
                target_kind="aws_iam_role", target_natural_key=arn,
                kind="contains", attributes={},
                evidence_packet={"version": "0.1", "via": "iam.list_roles"},
                detector_id=DETECTOR_ID, detector_version=DETECTOR_VERSION,
            ))

    user_paginator = iam_client.get_paginator("list_users") if hasattr(iam_client, "get_paginator") else None
    user_iter = (user_paginator.paginate() if user_paginator else
                 [iam_client.list_users()])
    for page in user_iter:
        for u in page["Users"]:
            arn = u["Arn"]
            entities.append(EntityEmission(
                tenant_id=tenant_id, kind="aws_iam_user",
                natural_key=arn, display_name=u["UserName"], domain="cloud",
                attributes={"path": u.get("Path", "/"), "user_id": u["UserId"]},
                evidence_packet=None,
                detector_id=DETECTOR_ID, detector_version=DETECTOR_VERSION,
            ))
            edges.append(EdgeEmission(
                tenant_id=tenant_id,
                source_kind="aws_account", source_natural_key=account_nk,
                target_kind="aws_iam_user", target_natural_key=arn,
                kind="contains", attributes={},
                evidence_packet={"version": "0.1", "via": "iam.list_users"},
                detector_id=DETECTOR_ID, detector_version=DETECTOR_VERSION,
            ))

    return {"entities": entities, "edges": edges}
```

- [ ] **Step 3: Make `detectors.base` + `unified_writer` importable from shasta_runner**

`detectors/base.py` and `unified_writer.py` live under `ai_scanner/`. shasta_runner needs both. To preserve the `from detectors.base import …` import in both Lambdas (so `unified_writer.py` doesn't need a rewrite), the build.sh COPIES the same package shape into shasta_runner. Add to `platform/lambda/shasta_runner/build.sh` BEFORE the `docker build` line:

```bash
# Copy shared modules from ai_scanner — preserves the detectors/base package
# shape so unified_writer.py imports work identically in both Lambdas.
mkdir -p app/detectors
cp ../ai_scanner/detectors/base.py     app/detectors/base.py
touch                                   app/detectors/__init__.py
cp ../ai_scanner/unified_writer.py     app/unified_writer.py
```

Add the corresponding lines to `platform/lambda/entities_api/build.sh` in Task 20.

Add `app/detectors/`, `app/unified_writer.py` to `.gitignore` in each Lambda directory so the copied files don't get committed.

- [ ] **Step 4: Run + commit**

```bash
/Users/kkmookhey/venv/bin/pytest platform/lambda/shasta_runner/app/tests/test_enumerate_iam.py -v
# Expected: 1 passed (or however many you add)

git add platform/lambda/shasta_runner/app/enumerate_iam.py \
        platform/lambda/shasta_runner/app/tests/test_enumerate_iam.py \
        platform/lambda/shasta_runner/build.sh
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "feat(platform): shasta_runner — IAM enumeration (roles + users + contains edges)"
```

---

### Task 17: Storage (S3) enumeration

**Same pattern as Task 16.** File: `enumerate_storage.py`.

- [ ] **Step 1: Write `test_enumerate_storage.py`** stubbing `s3.list_buckets` returning 2 buckets with `Name`, `CreationDate`.
- [ ] **Step 2: Implement `enumerate_storage.py`** — emits one `aws_s3_bucket` per bucket + one `aws_account → contains → aws_s3_bucket` edge per. `get_bucket_location` is best-effort (skip on failure).
- [ ] **Step 3: Run tests.**
- [ ] **Step 4: Commit.**

```bash
git add platform/lambda/shasta_runner/app/enumerate_storage.py \
        platform/lambda/shasta_runner/app/tests/test_enumerate_storage.py
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "feat(platform): shasta_runner — S3 storage enumeration"
```

---

### Task 18: Compute (EC2 + Lambda) enumeration

**Files:**
- Create: `platform/lambda/shasta_runner/app/enumerate_compute.py`
- Create: `platform/lambda/shasta_runner/app/tests/test_enumerate_compute.py`

Same pattern. Stub `ec2.describe_instances` returning a `Reservations[Instances[]]` payload with one instance + `IamInstanceProfile.Arn`. Stub `lambda.list_functions` with one function having `Role` populated.

Emissions:
- For each EC2 instance: `aws_ec2_instance` entity + `aws_account → contains → instance` edge + if `IamInstanceProfile` set, `instance → assumes → iam_role` edge.
- For each Lambda: `aws_lambda_function` entity + `account → contains → lambda` + `lambda → assumes → iam_role` (using `Role` ARN).

- [ ] Steps 1-4 mirror Task 16.

```bash
git add platform/lambda/shasta_runner/app/enumerate_compute.py \
        platform/lambda/shasta_runner/app/tests/test_enumerate_compute.py
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "feat(platform): shasta_runner — EC2 + Lambda enumeration with assumes edges"
```

---

### Task 19: Network enumeration + handler integration

**Files:**
- Create: `platform/lambda/shasta_runner/app/enumerate_network.py`
- Create: `platform/lambda/shasta_runner/app/tests/test_enumerate_network.py`
- Modify: `platform/lambda/shasta_runner/app/main.py`

Network: VPCs, subnets, security groups. Emit `vpc → contains → subnet`, `vpc → contains → security_group`.

- [ ] **Step 1: enumerate_network.py + tests** (same pattern as Task 16).
- [ ] **Step 2: Modify `main.py`** to:
  1. Call the four enum functions (passing the boto3 client and account_id).
  2. Parse each finding's `resource_id` via `arn_to_entity.parse_arn` → derive entity emission + `aws_account → contains → resource` edge.
  3. Always emit the `aws_account` entity itself.
  4. Replace `_insert_findings` with `unified_writer.commit_scan(ctx, entities=..., edges=..., findings=...)`.
- [ ] **Step 3: Update `build.sh`** to copy `unified_writer.py` from `../ai_scanner/`.
- [ ] **Step 4: Smoke test locally** (the existing scan_runner happy-path test).
- [ ] **Step 5: Commit**

```bash
git add platform/lambda/shasta_runner/app/enumerate_network.py \
        platform/lambda/shasta_runner/app/tests/test_enumerate_network.py \
        platform/lambda/shasta_runner/app/main.py \
        platform/lambda/shasta_runner/build.sh
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "feat(platform): shasta_runner — network enum + handler uses unified_writer"
```

---

## Phase E — entities_api Lambda (3 tasks)

### Task 20: Scaffold `entities_api` + scan endpoints

**Files:**
- Create: `platform/lambda/entities_api/main.py`
- Create: `platform/lambda/entities_api/helpers.py`
- Create: `platform/lambda/entities_api/build.sh`
- Create: `platform/lambda/entities_api/tests/__init__.py`
- Create: `platform/lambda/entities_api/tests/conftest.py`
- Create: `platform/lambda/entities_api/tests/test_handler.py`
- Delete: `platform/lambda/ai_scan_api/` (entire directory)

Helpers (`resp`, `resolve_tenant_id`): copy verbatim from `ai_scan_api/helpers.py`. Don't import — duplicate.

- [ ] **Step 1: Copy helpers and conftest**

```bash
mkdir -p platform/lambda/entities_api/tests
cp platform/lambda/ai_scan_api/helpers.py            platform/lambda/entities_api/helpers.py
cp platform/lambda/ai_scan_api/tests/conftest.py     platform/lambda/entities_api/tests/conftest.py
touch platform/lambda/entities_api/tests/__init__.py
```

- [ ] **Step 2: Write `build.sh`** that copies `unified_writer.py` from `../ai_scanner/`. Similar to scan_runner's build.sh in Task 19.

- [ ] **Step 3: Write `test_handler.py`** with stubs for the 5 routes — mirror `ai_scan_api/tests/test_handler.py` shape but use the new entity-based fixtures. Cover: `test_unknown_route_404`, `test_start_scan_happy_path`, `test_list_scans_filters`, `test_get_scan_404`, `test_list_entities_pagination`, `test_get_entity_returns_evidence_packet`, `test_get_entity_graph_recursive_cte`.

- [ ] **Step 4: Implement `main.py`** with 5 routes.

The three scan routes (`POST /ai/scans`, `GET /ai/scans`, `GET /ai/scans/{id}`) port verbatim from `ai_scan_api/main.py` with two changes: `_upsert_repo_asset` becomes `_upsert_repo_entity` (writes to `entities` with `kind='github_repo'`, `natural_key=f'github.com/{repo_full_name}'`, `domain='repo'`); and the LEFT JOIN on `ai_assets` for repo display name becomes a LEFT JOIN on `entities` filtered by `kind='github_repo'`.

The four new routes (entities + graph + relationships):

```python
# platform/lambda/entities_api/main.py — relevant new pieces

def _list_entities(event):
    tenant_id = helpers.resolve_tenant_id(event)
    if not tenant_id:
        return helpers.resp(401, {"error": "no_tenant"})
    q = event.get("queryStringParameters") or {}
    domain = q.get("domain"); kind = q.get("kind"); repo_id = q.get("repo")
    try:
        page     = max(1, int(q.get("page", "1")))
        per_page = min(int(q.get("per_page", "50")), 200)
    except (TypeError, ValueError):
        return helpers.resp(400, {"error": "bad_pagination"})

    sql = (
        "SELECT e.id::text, e.kind, e.natural_key, e.display_name, e.domain, "
        "       e.detector_id, "
        "       to_char(e.first_seen_at, 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), "
        "       to_char(e.last_seen_at,  'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), "
        "       e.attributes::text "
        "FROM entities e "
        "WHERE e.tenant_id = CAST(:tid AS UUID)"
    )
    params = [{"name": "tid", "value": {"stringValue": tenant_id}}]
    if domain:
        sql += " AND e.domain = :dom"
        params.append({"name": "dom", "value": {"stringValue": domain}})
    if kind:
        sql += " AND e.kind = :kind"
        params.append({"name": "kind", "value": {"stringValue": kind}})
    if repo_id:
        # Filter by edges where this repo is the source
        sql += (" AND e.id IN ("
                "  SELECT target_entity_id FROM edges "
                "  WHERE source_entity_id = CAST(:rid AS UUID) "
                "    AND tenant_id = CAST(:tid AS UUID))")
        params.append({"name": "rid", "value": {"stringValue": repo_id}})
    sql += " ORDER BY e.last_seen_at DESC LIMIT :lim OFFSET :off"
    params.append({"name": "lim", "value": {"longValue": per_page + 1}})
    params.append({"name": "off", "value": {"longValue": (page - 1) * per_page}})

    rs = helpers.rds_data.execute_statement(
        resourceArn=helpers.DB_CLUSTER_ARN, secretArn=helpers.DB_SECRET_ARN,
        database=helpers.DB_NAME, sql=sql, parameters=params,
    )
    records = rs.get("records", [])
    has_next = len(records) > per_page
    return helpers.resp(200, {
        "entities": [_row_to_entity(r) for r in records[:per_page]],
        "next_page": (page + 1) if has_next else None,
    })


def _row_to_entity(r):
    return {
        "id":            r[0].get("stringValue"),
        "kind":          r[1].get("stringValue"),
        "natural_key":   r[2].get("stringValue"),
        "display_name":  r[3].get("stringValue"),
        "domain":        r[4].get("stringValue"),
        "detector_id":   r[5].get("stringValue"),
        "first_seen_at": r[6].get("stringValue"),
        "last_seen_at":  r[7].get("stringValue"),
        "attributes":    json.loads(r[8].get("stringValue") or "{}"),
        "source_path":   json.loads(r[8].get("stringValue") or "{}").get("source_path"),
    }


def _get_entity(event):
    tenant_id = helpers.resolve_tenant_id(event)
    if not tenant_id:
        return helpers.resp(401, {"error": "no_tenant"})
    entity_id = (event.get("pathParameters") or {}).get("id")
    if not entity_id:
        return helpers.resp(400, {"error": "missing_id"})

    rs = helpers.rds_data.execute_statement(
        resourceArn=helpers.DB_CLUSTER_ARN, secretArn=helpers.DB_SECRET_ARN,
        database=helpers.DB_NAME,
        sql=("SELECT id::text, kind, natural_key, display_name, domain, "
             "       detector_id, "
             "       to_char(first_seen_at, 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), "
             "       to_char(last_seen_at,  'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), "
             "       attributes::text, COALESCE(evidence_packet::text, 'null'), "
             "       COALESCE(connection_id::text, '') "
             "FROM entities WHERE tenant_id = CAST(:tid AS UUID) "
             "  AND id = CAST(:eid AS UUID) LIMIT 1"),
        parameters=[
            {"name": "tid", "value": {"stringValue": tenant_id}},
            {"name": "eid", "value": {"stringValue": entity_id}},
        ],
    )
    rows = rs.get("records", [])
    if not rows:
        return helpers.resp(404, {"error": "not_found"})
    r = rows[0]
    return helpers.resp(200, {
        "id":              r[0].get("stringValue"),
        "kind":            r[1].get("stringValue"),
        "natural_key":     r[2].get("stringValue"),
        "display_name":    r[3].get("stringValue"),
        "domain":          r[4].get("stringValue"),
        "detector_id":     r[5].get("stringValue"),
        "first_seen_at":   r[6].get("stringValue"),
        "last_seen_at":    r[7].get("stringValue"),
        "attributes":      json.loads(r[8].get("stringValue") or "{}"),
        "evidence_packet": json.loads(r[9].get("stringValue") or "null"),
        "connection_id":   r[10].get("stringValue") or None,
    })


def _entity_graph(event):
    """Recursive CTE walking outward from the root entity, capped at depth +
    node count. Returns cytoscape-shaped JSON."""
    tenant_id = helpers.resolve_tenant_id(event)
    if not tenant_id:
        return helpers.resp(401, {"error": "no_tenant"})
    # Path is /entities/{id}/graph — extract id from path parameters
    entity_id = (event.get("pathParameters") or {}).get("id")
    if not entity_id:
        return helpers.resp(400, {"error": "missing_id"})
    q = event.get("queryStringParameters") or {}
    try:
        depth     = min(int(q.get("depth", "4")), 8)
        max_nodes = min(int(q.get("max_nodes", "500")), 1000)
    except (TypeError, ValueError):
        return helpers.resp(400, {"error": "bad_query"})

    # Recursive CTE walks edges in both directions
    nodes_sql = (
        "WITH RECURSIVE walked(id, depth) AS ( "
        "  SELECT id, 0 FROM entities "
        "  WHERE id = CAST(:root AS UUID) AND tenant_id = CAST(:tid AS UUID) "
        "  UNION "
        "  SELECT next_id, walked.depth + 1 FROM walked "
        "  CROSS JOIN LATERAL ( "
        "    SELECT CASE WHEN source_entity_id = walked.id "
        "                THEN target_entity_id ELSE source_entity_id END AS next_id "
        "    FROM edges "
        "    WHERE (source_entity_id = walked.id OR target_entity_id = walked.id) "
        "      AND tenant_id = CAST(:tid AS UUID) "
        "  ) e "
        "  WHERE walked.depth < :max_depth "
        ") "
        "SELECT e.id::text, e.kind, e.display_name, e.attributes::text "
        "FROM (SELECT DISTINCT id FROM walked) w "
        "JOIN entities e ON e.id = w.id "
        "LIMIT :max_nodes"
    )
    nrs = helpers.rds_data.execute_statement(
        resourceArn=helpers.DB_CLUSTER_ARN, secretArn=helpers.DB_SECRET_ARN,
        database=helpers.DB_NAME, sql=nodes_sql,
        parameters=[
            {"name": "root",      "value": {"stringValue": entity_id}},
            {"name": "tid",       "value": {"stringValue": tenant_id}},
            {"name": "max_depth", "value": {"longValue":   depth}},
            {"name": "max_nodes", "value": {"longValue":   max_nodes + 1}},
        ],
    )
    node_rows = nrs.get("records", [])
    truncated = len(node_rows) > max_nodes
    node_rows = node_rows[:max_nodes]
    node_ids = [r[0].get("stringValue") for r in node_rows]
    if not node_ids:
        return helpers.resp(404, {"error": "not_found"})

    # Edges among the walked nodes
    ers = helpers.rds_data.execute_statement(
        resourceArn=helpers.DB_CLUSTER_ARN, secretArn=helpers.DB_SECRET_ARN,
        database=helpers.DB_NAME,
        sql=("SELECT id::text, source_entity_id::text, target_entity_id::text, kind "
             "FROM edges WHERE tenant_id = CAST(:tid AS UUID) "
             "  AND source_entity_id::text = ANY(string_to_array(:ids, ',')) "
             "  AND target_entity_id::text = ANY(string_to_array(:ids, ','))"),
        parameters=[
            {"name": "tid", "value": {"stringValue": tenant_id}},
            {"name": "ids", "value": {"stringValue": ",".join(node_ids)}},
        ],
    )
    return helpers.resp(200, {
        "nodes": [{"data": {
            "id":         r[0].get("stringValue"),
            "label":      r[2].get("stringValue"),
            "type":       r[1].get("stringValue"),
            "attributes": json.loads(r[3].get("stringValue") or "{}"),
        }} for r in node_rows],
        "edges": [{"data": {
            "id":     r[0].get("stringValue"),
            "source": r[1].get("stringValue"),
            "target": r[2].get("stringValue"),
            "label":  r[3].get("stringValue"),
        }} for r in ers.get("records", [])],
        "meta": {"root_id": entity_id, "node_count": len(node_rows), "truncated": truncated},
    })


def _entity_relationships(event):
    tenant_id = helpers.resolve_tenant_id(event)
    if not tenant_id:
        return helpers.resp(401, {"error": "no_tenant"})
    entity_id = (event.get("pathParameters") or {}).get("id")
    direction = (event.get("queryStringParameters") or {}).get("direction", "both")
    if direction not in ("both", "outgoing", "incoming"):
        return helpers.resp(400, {"error": "bad_direction"})

    where_clauses = []
    if direction in ("both", "outgoing"):
        where_clauses.append("source_entity_id = CAST(:eid AS UUID)")
    if direction in ("both", "incoming"):
        where_clauses.append("target_entity_id = CAST(:eid AS UUID)")
    where = " OR ".join(where_clauses)

    sql = (
        "SELECT e.id::text, e.kind, "
        "  CASE WHEN e.source_entity_id = CAST(:eid AS UUID) "
        "       THEN 'outgoing' ELSE 'incoming' END AS direction, "
        "  other.id::text, other.kind, other.natural_key, other.display_name "
        "FROM edges e "
        "JOIN entities other ON other.id = CASE "
        "  WHEN e.source_entity_id = CAST(:eid AS UUID) THEN e.target_entity_id "
        "                                                 ELSE e.source_entity_id END "
        f"WHERE e.tenant_id = CAST(:tid AS UUID) AND ({where}) "
        "ORDER BY e.last_seen_at DESC LIMIT 500"
    )
    rs = helpers.rds_data.execute_statement(
        resourceArn=helpers.DB_CLUSTER_ARN, secretArn=helpers.DB_SECRET_ARN,
        database=helpers.DB_NAME, sql=sql,
        parameters=[
            {"name": "tid", "value": {"stringValue": tenant_id}},
            {"name": "eid", "value": {"stringValue": entity_id}},
        ],
    )
    return helpers.resp(200, {
        "relationships": [{
            "id":        r[0].get("stringValue"),
            "kind":      r[1].get("stringValue"),
            "direction": r[2].get("stringValue"),
            "other_entity": {
                "id":           r[3].get("stringValue"),
                "kind":         r[4].get("stringValue"),
                "natural_key":  r[5].get("stringValue"),
                "display_name": r[6].get("stringValue"),
            },
        } for r in rs.get("records", [])],
    })
```

The full file is ~500 lines (5 routes × ~100 lines each). The three scan routes carry over verbatim — see `ai_scan_api/main.py` for the existing shape and adapt the SQL to query `entities` where it queried `ai_assets`. Match the pattern in `_list_entities` above for the change shape.

- [ ] **Step 5: Delete ai_scan_api**

```bash
git rm -r platform/lambda/ai_scan_api/
```

- [ ] **Step 6: Run tests + commit**

```bash
/Users/kkmookhey/venv/bin/pytest platform/lambda/entities_api/tests/ -v
# Expected: 7+ passed

git switch feat/sp1-unified-entities
git add platform/lambda/entities_api/
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "feat(platform): entities_api Lambda — replaces ai_scan_api, adds graph + relationships routes"
```

---

### Task 21: CDK wiring

**Files:**
- Modify: `platform/lib/api-stack.ts`

Rename `AiScanApiFn` → `EntitiesApiFn`. Update `Code.fromAsset(... 'ai_scan_api' ...)` → `'entities_api'`. Keep existing `POST/GET /v1/ai/scans` routes targeting the new Lambda. Add new resources: `aiRes.addResource('entities')`, `entitiesRes.addResource('{id}')`, `entityIdRes.addResource('graph')`, `entityIdRes.addResource('relationships')`. Wire each to `entitiesApiFn`.

- [ ] **Step 1: Apply the edits**
- [ ] **Step 2: Synth + verify**

```bash
cd platform
npx cdk synth CisoCopilotApi > /tmp/synth-api.yaml 2>&1 && echo "synth ok"
grep -c "EntitiesApiFn\|entities/{id}/graph" /tmp/synth-api.yaml
# Expected: ≥2
```

- [ ] **Step 3: Commit (do NOT deploy yet — wait until Phase G)**

```bash
git switch feat/sp1-unified-entities
git add platform/lib/api-stack.ts
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "feat(platform): CDK — rename ai_scan_api → entities_api + add graph/relationships routes"
```

---

## Phase F — Data migration script (1 task)

### Task 22: `migrate_to_entities.py`

**Files:**
- Create: `platform/scripts/migrate_to_entities.py`

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python3
"""One-shot data migration: ai_assets/ai_relationships → entities/edges.

Run from KK's laptop. Idempotent. See spec §13."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid

CLUSTER_ARN = "arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh"
SECRET_ARN  = "arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp"
DB_NAME     = "ciso_copilot"
REGION      = "us-east-1"


def run_sql(sql: str, parameters=None) -> dict:
    cmd = [
        "aws", "rds-data", "execute-statement",
        "--resource-arn", CLUSTER_ARN, "--secret-arn", SECRET_ARN,
        "--database", DB_NAME, "--region", REGION,
        "--sql", sql,
    ]
    if parameters:
        cmd += ["--parameters", json.dumps(parameters)]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(out.stdout) if out.stdout else {}


def derive_natural_key(asset_type, name, source_repo_id, source_path,
                       repo_name_by_id):
    """Map a row of ai_assets to its new (kind, natural_key)."""
    # Cross-repo dedup kinds — natural_key is just the name
    if asset_type in ("framework", "model", "vector_db", "embedding"):
        return f"ai_{asset_type}", name
    # Per-file kinds — natural_key embeds repo + path
    if asset_type in ("mcp_server", "tool", "agent", "prompt"):
        repo_name = repo_name_by_id.get(source_repo_id) if source_repo_id else None
        repo_nk = f"github.com/{repo_name}" if repo_name else ""
        return f"ai_{asset_type}", f"{repo_nk}::{source_path or ''}::{name}"
    if asset_type == "repository":
        return "github_repo", f"github.com/{name}"
    return None, None


def main():
    # 1. Read all ai_assets
    rows = run_sql("SELECT id::text, tenant_id::text, asset_type, name, "
                    "       source_repo_id::text, source_path, attributes::text, "
                    "       evidence_packet::text, detector_id, detector_version, "
                    "       connection_id::text "
                    "FROM ai_assets")
    assets = rows.get("records", [])
    print(f"Loaded {len(assets)} ai_assets rows")

    # 2. Build repo_name_by_id (rows where asset_type=='repository')
    repo_name_by_id = {}
    for r in assets:
        if r[2]["stringValue"] == "repository":
            repo_name_by_id[r[0]["stringValue"]] = r[3]["stringValue"]

    # 3. Upsert entities — collect (old_id, kind, natural_key) → resolve later
    old_to_new = {}
    upserted = 0
    for r in assets:
        old_id      = r[0]["stringValue"]
        tenant_id   = r[1]["stringValue"]
        asset_type  = r[2]["stringValue"]
        name        = r[3]["stringValue"]
        source_repo = r[4].get("stringValue") if not r[4].get("isNull") else None
        source_path = r[5].get("stringValue", "") or ""
        attributes  = r[6].get("stringValue", "{}")
        evidence    = r[7].get("stringValue") if not r[7].get("isNull") else None

        kind, nk = derive_natural_key(asset_type, name, source_repo, source_path, repo_name_by_id)
        if not kind:
            print(f"SKIP unknown asset_type={asset_type} id={old_id}")
            continue
        domain = "repo" if kind == "github_repo" else "ai"
        new_id = str(uuid.uuid4())

        run_sql(
            "INSERT INTO entities (id, tenant_id, kind, natural_key, display_name, "
            "  domain, attributes, evidence_packet, detector_id, detector_version) "
            "VALUES (CAST(:id AS UUID), CAST(:tid AS UUID), :kind, :nk, :dn, :dom, "
            "        CAST(:attrs AS JSONB), "
            "        CASE WHEN :ev = '' THEN NULL ELSE CAST(:ev AS JSONB) END, "
            "        :did, :dver) "
            "ON CONFLICT (tenant_id, kind, natural_key) DO NOTHING",
            [
                {"name": "id",    "value": {"stringValue": new_id}},
                {"name": "tid",   "value": {"stringValue": tenant_id}},
                {"name": "kind",  "value": {"stringValue": kind}},
                {"name": "nk",    "value": {"stringValue": nk}},
                {"name": "dn",    "value": {"stringValue": name}},
                {"name": "dom",   "value": {"stringValue": domain}},
                {"name": "attrs", "value": {"stringValue": attributes}},
                {"name": "ev",    "value": {"stringValue": evidence or ""}},
                {"name": "did",   "value": {"stringValue": r[8]["stringValue"]}},
                {"name": "dver",  "value": {"stringValue": r[9]["stringValue"]}},
            ],
        )
        # Resolve the now-persisted id
        resolved = run_sql(
            "SELECT id::text FROM entities WHERE tenant_id = CAST(:tid AS UUID) "
            "  AND kind = :kind AND natural_key = :nk",
            [{"name": "tid", "value": {"stringValue": tenant_id}},
             {"name": "kind", "value": {"stringValue": kind}},
             {"name": "nk", "value": {"stringValue": nk}}],
        )
        if resolved["records"]:
            old_to_new[old_id] = resolved["records"][0][0]["stringValue"]
            upserted += 1

    print(f"Upserted {upserted} entities (dedup delta: {len(assets) - upserted})")

    # 4. Migrate ai_relationships → edges
    rel_rows = run_sql("SELECT source_asset_id::text, target_asset_id::text, "
                        "       relationship_type, attributes::text, evidence_packet::text, "
                        "       detector_id, detector_version, tenant_id::text "
                        "FROM ai_relationships")
    edges_migrated = 0
    for r in rel_rows.get("records", []):
        src = old_to_new.get(r[0]["stringValue"])
        tgt = old_to_new.get(r[1]["stringValue"])
        if not src or not tgt:
            continue
        new_id = str(uuid.uuid4())
        run_sql(
            "INSERT INTO edges (id, tenant_id, source_entity_id, target_entity_id, "
            "  kind, attributes, evidence_packet, detector_id, detector_version) "
            "VALUES (CAST(:id AS UUID), CAST(:tid AS UUID), CAST(:src AS UUID), "
            "        CAST(:tgt AS UUID), :kind, CAST(:attrs AS JSONB), CAST(:ev AS JSONB), "
            "        :did, :dver) "
            "ON CONFLICT (source_entity_id, target_entity_id, kind) DO NOTHING",
            [
                {"name": "id",    "value": {"stringValue": new_id}},
                {"name": "tid",   "value": {"stringValue": r[7]["stringValue"]}},
                {"name": "src",   "value": {"stringValue": src}},
                {"name": "tgt",   "value": {"stringValue": tgt}},
                {"name": "kind",  "value": {"stringValue": r[2]["stringValue"]}},
                {"name": "attrs", "value": {"stringValue": r[3]["stringValue"]}},
                {"name": "ev",    "value": {"stringValue": r[4]["stringValue"]}},
                {"name": "did",   "value": {"stringValue": r[5]["stringValue"]}},
                {"name": "dver",  "value": {"stringValue": r[6]["stringValue"]}},
            ],
        )
        edges_migrated += 1
    print(f"Migrated {edges_migrated} edges")

    # 5. Backfill findings.subject_entity_id (best-effort)
    # ...for each finding with resource_arn matching arn:aws:*, look up the
    # entity by natural_key = resource_arn and UPDATE.
    backfill = run_sql(
        "UPDATE findings SET subject_entity_id = e.id "
        "FROM entities e "
        "WHERE findings.subject_entity_id IS NULL "
        "  AND findings.resource_arn IS NOT NULL "
        "  AND findings.resource_arn != '' "
        "  AND e.natural_key = findings.resource_arn "
        "  AND e.tenant_id = findings.tenant_id"
    )
    print(f"Backfilled findings: {backfill.get('numberOfRecordsUpdated', 0)}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Dry-run on KK's tenant (with the new tables empty)**

```bash
chmod +x platform/scripts/migrate_to_entities.py
python3 platform/scripts/migrate_to_entities.py
```
Inspect the output. Expected: `Upserted N entities`, `dedup delta: ≥0`, `Migrated M edges`, `Backfilled findings: K`.

- [ ] **Step 3: Verify counts**

```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot --region us-east-1 \
  --sql "SELECT kind, COUNT(*) FROM entities GROUP BY kind ORDER BY 2 DESC"
```

- [ ] **Step 4: Commit the script**

```bash
git switch feat/sp1-unified-entities
git add platform/scripts/migrate_to_entities.py
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "feat(platform): data migration script ai_assets/ai_relationships → entities/edges"
```

---

## Phase G — Web + iOS rewire (2 tasks)

### Task 23: Web — api.ts + AIInventory + AssetDetail

**Files:**
- Modify: `web/src/lib/api.ts`
- Modify: `web/src/routes/AIInventory.tsx`
- Modify: `web/src/routes/AssetDetail.tsx`

- [ ] **Step 1: api.ts**

Replace `AIAssetSummary`, `AIAssetDetail`, `listAIAssets`, `getAIAsset` with:

```typescript
export type EntityKind =
  | "github_repo" | "ai_framework" | "ai_model" | "ai_mcp_server"
  | "ai_tool" | "ai_agent" | "ai_vector_db" | "ai_embedding" | "ai_prompt"
  | "aws_account" | "aws_s3_bucket" | "aws_iam_role" | "aws_iam_user"
  | "aws_lambda_function" | "aws_ec2_instance" | "aws_vpc" | "aws_subnet"
  | "aws_security_group";

export interface EntitySummary {
  id:             string;
  kind:           EntityKind;
  natural_key:    string;
  display_name:   string;
  domain:         "ai" | "cloud" | "repo" | "identity" | "asm";
  source_path:    string | null;
  detector_id:    string;
  first_seen_at:  string;
  last_seen_at:   string;
  attributes:     Record<string, unknown>;
}

export interface EntityDetail extends EntitySummary {
  evidence_packet: Record<string, unknown> | null;
  connection_id:   string | null;
}

export interface EntityGraph {
  nodes: { data: { id: string; label: string; type: EntityKind; attributes: Record<string, unknown> } }[];
  edges: { data: { id: string; source: string; target: string; label: string; evidence_packet_id: string | null } }[];
  meta:  { root_id: string; node_count: number; truncated: boolean };
}

// Append to `api` object:
  listEntities: (params?: { domain?: string; kind?: string; repo?: string; page?: number; per_page?: number }) => {
    const q = new URLSearchParams();
    if (params?.domain)   q.set("domain", params.domain);
    if (params?.kind)     q.set("kind", params.kind);
    if (params?.repo)     q.set("repo", params.repo);
    if (params?.page)     q.set("page", String(params.page));
    if (params?.per_page) q.set("per_page", String(params.per_page));
    const qs = q.toString();
    return call<{ entities: EntitySummary[]; next_page: number | null }>(
      `/entities${qs ? "?" + qs : ""}`,
    );
  },
  getEntity:       (id: string) => call<EntityDetail>(`/entities/${id}`),
  getEntityGraph:  (id: string, depth = 4, maxNodes = 500) =>
    call<EntityGraph>(`/entities/${id}/graph?depth=${depth}&max_nodes=${maxNodes}`),
  getEntityRelationships: (id: string, direction: "both" | "outgoing" | "incoming" = "both") =>
    call<{ relationships: { id: string; kind: string; other_entity: EntitySummary; direction: string }[] }>(
      `/entities/${id}/relationships?direction=${direction}`,
    ),
// Delete old listAIAssets, getAIAsset, AIAssetSummary, AIAssetDetail definitions.
```

- [ ] **Step 2: AIInventory.tsx** — replace `listAIAssets({type: typeFilter})` → `listEntities({domain: "ai", kind: typeFilter || undefined})`. Replace field reads: `asset.asset_type → entity.kind`, `asset.name → entity.display_name`, `asset.source_repo → derived from edges`. Drop `groupByRepo` (entities no longer carry source_repo embedded; for SP1 the inventory shows flat list grouped by kind chip — repo grouping returns in SP2 via the graph endpoint).

- [ ] **Step 3: AssetDetail.tsx** — replace `getAIAsset(asset_id)` → `getEntity(asset_id)`. Field renames: `asset.asset_type → entity.kind`, `asset.name → entity.display_name`. The GitHub deep-link uses `entity.attributes.source_path` instead of top-level source_path.

- [ ] **Step 4: Typecheck + build**

```bash
cd web
pnpm tsc --noEmit
pnpm build
# Expected: clean
```

- [ ] **Step 5: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git switch feat/sp1-unified-entities
git add web/src/lib/api.ts web/src/routes/AIInventory.tsx web/src/routes/AssetDetail.tsx
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "feat(web): repoint AI Inventory + Asset Detail at entities API"
```

---

### Task 24: iOS — APIClient + view renames

**Files:**
- Modify: `ios/CISOCopilot/Services/APIClient.swift`
- Modify: `ios/CISOCopilot/Views/AI/AIInventoryView.swift`
- Modify: `ios/CISOCopilot/Views/AI/AIAssetDetailView.swift`

- [ ] **Step 1: APIClient.swift** — same shape as web. Rename methods + types:

```swift
struct EntityRepoRef: Decodable, Hashable {
    let id: String
    let full_name: String
}

struct EntitySummary: Decodable, Identifiable, Hashable {
    let id: String
    let kind: String
    let natural_key: String
    let display_name: String
    let domain: String
    let source_path: String?
    let detector_id: String
    let first_seen_at: String
    let last_seen_at: String
    let attributes: AnyJSON
}

struct EntityDetail: Decodable {
    let id: String
    let kind: String
    let natural_key: String
    let display_name: String
    let domain: String
    let source_path: String?
    let detector_id: String
    let first_seen_at: String
    let last_seen_at: String
    let attributes: AnyJSON
    let evidence_packet: AnyJSON
    let connection_id: String?
}

// Methods
func listEntities(domain: String? = nil, kind: String? = nil) async throws -> [EntitySummary] { ... }
func getEntity(_ id: String) async throws -> EntityDetail { ... }
```

Delete the `AIAsset*` types and methods.

- [ ] **Step 2: AIInventoryView.swift** — `assets: [AIAssetSummary]` → `entities: [EntitySummary]`. Field accesses: `.asset_type → .kind`, `.name → .display_name`, `.source_repo → derive from attributes`. Drop `groupedByRepo` for SP1 (inventory shows flat list; SP2 reintroduces grouping via graph).

- [ ] **Step 3: AIAssetDetailView.swift** — `let fallback: AIAssetSummary → fallback: EntitySummary`. Field renames.

- [ ] **Step 4: Build + install**

```bash
cd ios
xcodegen generate
xcodebuild build \
  -project CISOCopilot.xcodeproj -scheme CISOCopilot \
  -destination "id=00008140-001E104E3A9B001C" \
  -derivedDataPath build-device -allowProvisioningUpdates 2>&1 | tail -5
# Expected: BUILD SUCCEEDED
```

- [ ] **Step 5: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git switch feat/sp1-unified-entities
git add ios/CISOCopilot/Services/APIClient.swift ios/CISOCopilot/Views/AI/
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "feat(ios): repoint AI tab at entities API"
```

---

## Phase H — Deploy + E2E (1 task)

### Task 25: Deploy + verify

**Files:** none (deploy + verification).

- [ ] **Step 1: Build + push ai_scanner image**

```bash
cd platform/lambda/ai_scanner && ./build.sh
```

- [ ] **Step 2: Build + push shasta_runner image** (now has new dep on `arn_to_entity` + 4 enum modules + copied unified_writer)

```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform/lambda/shasta_runner
./build.sh
```

- [ ] **Step 3: Deploy CDK**

```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform
npx cdk deploy CisoCopilotScan CisoCopilotApi --require-approval never 2>&1 | tail -15
```

Expected: both stacks complete.

- [ ] **Step 4: Update both container Lambdas to the latest image**

```bash
aws lambda update-function-code --function-name ciso-copilot-ai-scanner \
  --image-uri 470226123496.dkr.ecr.us-east-1.amazonaws.com/ai-scanner:latest \
  --query LastUpdateStatus --output text
aws lambda wait function-updated --function-name ciso-copilot-ai-scanner

aws lambda update-function-code --function-name ciso-copilot-shasta-runner \
  --image-uri 470226123496.dkr.ecr.us-east-1.amazonaws.com/ciso-copilot-shasta-runner:latest \
  --query LastUpdateStatus --output text
aws lambda wait function-updated --function-name ciso-copilot-shasta-runner
```

- [ ] **Step 5: Run the data migration**

```bash
python3 platform/scripts/migrate_to_entities.py
```

- [ ] **Step 6: Deploy web**

```bash
cd /Users/kkmookhey/Projects/CISOBrief/web
pnpm build
aws s3 sync dist/ s3://ciso-copilot-app-470226123496/ --delete --region us-east-1
aws cloudfront create-invalidation --distribution-id E2FV1Z0DJ4RQS4 --paths '/*' --region us-east-1
```

- [ ] **Step 7: Install iOS**

```bash
xcrun devicectl device install app --device 00008140-001E104E3A9B001C \
  /Users/kkmookhey/Projects/CISOBrief/ios/build-device/Build/Products/Debug-iphoneos/CISOCopilot.app
```

- [ ] **Step 8: E2E verification**

1. Hard-reload `https://shasta.transilience.cloud/`.
2. Trigger an AWS scan from /connect (rescan AWS connection). Verify:
   ```bash
   aws rds-data execute-statement --resource-arn ... --sql \
     "SELECT kind, COUNT(*) FROM entities GROUP BY kind ORDER BY 2 DESC"
   ```
   Expected: significant `aws_*` counts (every IAM role, bucket, EC2, etc.) — NOT just findings count.
3. Trigger an AI scan on `kkmookhey/ciso-copilot`. Verify 3+ AI entities migrate cleanly + new `deploys_to` edge if the repo has GitHub Actions workflows.
4. Hit `GET /v1/entities/{aws_account_id}/graph?depth=3` via curl with a JWT, eyeball the JSON shape.
5. Open /ai/inventory in browser — flat list of AI entities renders.
6. Open iOS app → AI tab → same entities render.

- [ ] **Step 9: Push branch + open PR**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git push -u origin feat/sp1-unified-entities
gh pr create --base main --head feat/sp1-unified-entities \
  --title "SP1: Unified entity + edge model" \
  --body "$(cat <<'EOF'
## Summary
Replaces AI-specific ai_assets/ai_relationships with domain-agnostic
entities + edges that cloud, AI, and future ASM scanners all write into.
Adds findings.subject_entity_id FK for fast "all findings on this entity"
queries. New crossdomain.py detector emits github_repo→deploys_to→aws_iam_role
edges from GitHub Actions OIDC role-to-assume directives. shasta_runner
gains four boto3 enumeration passes (IAM/storage/compute/network).

Spec: docs/superpowers/specs/2026-05-19-sp1-unified-entity-model-design.md

## Test plan
- [x] All ai_scanner unit tests pass
- [x] shasta_runner enumeration tests pass
- [x] entities_api route tests pass
- [x] Web pnpm build + tsc clean
- [x] iOS BUILD SUCCEEDED
- [x] Data migration ran on KK's tenant
- [x] Live E2E: AWS rescan + AI rescan + entities populated + graph endpoint returns

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 10: (separate PR, ~1 week later) Drop legacy tables**

```sql
-- platform/sql/006_drop_legacy_ai_tables.sql
DROP TABLE ai_relationships;
DROP TABLE ai_assets;
```

DO NOT include this in the SP1 PR. Land it as a follow-up after a week of stable scans.

---

## Self-review checklist (executor reads before claiming SP1 done)

- [ ] `entities` and `edges` tables exist in Aurora; `findings.subject_entity_id` column populated for migrated rows.
- [ ] `pytest platform/lambda/ai_scanner/tests/` returns ≥36 passed (all goldens + writer + scan_runner + correlator + crossdomain).
- [ ] `pytest platform/lambda/shasta_runner/app/tests/` covers ARN parsing + 4 enum passes.
- [ ] `pytest platform/lambda/entities_api/tests/` covers all 5 routes including graph CTE.
- [ ] CDK deploys both stacks clean.
- [ ] Web bundle builds; iOS BUILD SUCCEEDED.
- [ ] Data migration ran end-to-end on KK's tenant with positive counts.
- [ ] Live AI scan produces entities + edges; live AWS scan produces full cloud inventory.
- [ ] `GET /v1/entities/{account_id}/graph` returns cytoscape-shaped payload.
- [ ] HANDOFF.md + memory updated.

## Out of scope (do NOT add here — keep the discipline)

Per spec §17:
- Cytoscape graph viz (SP2)
- AI Risks tab consolidation (SP3)
- Chat surface (SP4) / Voice on web (SP5) / Dynamic dashboards (SP6)
- Per-domain inventory pages
- Azure / GCP / Entra enumeration
- LLM-assisted cross-domain edge inference
- Dropping `ai_assets` + `ai_relationships` tables (follow-up commit after soak)
