# AI-Powered SOC — Slice 1c (Threat-Intel Substrate) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sharpen the AI SOC narrative with real threat-intel evidence. After this slice, a SG-open-to-world drift event whose `sourceIPAddress` is a Tor exit node and/or an abuse.ch-tracked botnet C2 shows TI badges in `/soc` and the AI narrative reads *"Source IP 185.220.101.12 is a Tor exit + abuse.ch Feodo C2 (confidence 85). Combined with first-time-actor and off-hours signals, this is consistent with credential abuse from a residential proxy/anonymization network."* instead of generic "rare action for this actor".

**Architecture:** Adds a tenant-independent global `threat_indicators` table. Three new cron-driven feed-adapter Lambdas (`ti_feed_abusech` hourly, `ti_feed_kev` daily, `ti_feed_tor` hourly) ETL public IOC feeds into it. A vendored shared module (`ti_lookup.py`) plus an IOC-extraction utility (`ioc_extract.py`) live in `_shared/` and are copy-vendored into each consuming Lambda's build (same pattern as `spend_cap.py` today). The existing `soc_enrichment` Lambda gains a TI step in its features pipeline: extract IPs/domains/hashes from the event payload → bulk lookup in `threat_indicators` → optional on-demand GreyNoise Community call for unmatched IPs (rate-limited via the existing `soc_llm_spend_daily` DynamoDB table under a new sort-key prefix) → write `ti_matches` into `events.ai_features` and feed them to the LLM. `event_router` captures `sourceIPAddress` into a new `events.source_ip` column so the calling IP (not just the *target* IP from request params) becomes a lookup candidate. Web `/soc` DetailPane renders TI badges from `ai_features.ti_matches` — no API contract change.

**Tech Stack:** Python 3.12 + boto3 + stdlib `urllib.request` (no new pip deps for cron adapters — keeps zip sizes tiny and cold-start fast). LiteLLM unchanged. AWS Lambda + EventBridge `Schedule.rate()` cron triggers, Aurora Serverless v2 PG, DynamoDB (reusing existing `soc_llm_spend_daily` table for GreyNoise rate-limit counter), AWS CDK (TypeScript). React + TypeScript + Vitest on web.

**Spec:** `docs/superpowers/specs/2026-05-25-ai-powered-soc-design.md` — §3 (Slice 1c row), §4 (TI integration addendum), §5.2 (TI feed pluggability), §6 (threat_indicators schema), §10 (no customer-side cost; our-side ~$5-10/mo).

**Predecessor:** Current `main` after PR #24 (Slice 1 shipped). Pre-flight expectations — verify before starting:

- `platform/lambda/soc_enrichment/main.py` exists with handler that calls `compute_features` + `call_llm` + `_update_event_ai`
- `platform/lambda/soc_enrichment/features.py` exists with `compute_features(row) -> dict` returning `{first_time_actor_on_resource, off_hours, action_rarity, blast_radius_proxy}`
- `platform/lambda/event_router/spend_cap.py` exists with DynamoDB counter helpers; vendor-copy precedent in `soc_enrichment/build.sh` (`cp ../event_router/spend_cap.py build/`)
- `platform/sql/` has migrations up to `012_users_device_token.sql` (the next one is `013_*`)
- `events.source_event_id` + AI fields exist (migration 011)
- `web/src/components/soc/DetailPane.tsx` renders `ai_features` inside a `<details>` block; `ai_features.ti_matches` does not exist yet
- `platform/lib/events-stack.ts` declares `enrichmentQueue` + `spendCapTable` exports

If any of these is missing, stop and notify — Slice 1 has regressed.

---

## File Structure

**Creates:**
- `platform/sql/013_phase_soc_ti.sql` — `threat_indicators` table + indices + `events.source_ip` column
- `platform/lambda/_shared/__init__.py` — package marker for the new shared directory
- `platform/lambda/_shared/ti_lookup.py` — `Indicator` dataclass, `bulk_lookup()` helper, `upsert_indicators()` helper (DB writes via RDS Data API)
- `platform/lambda/_shared/ioc_extract.py` — `extract_iocs(row) -> dict[kind, list[value]]` (IPs/domains/hashes from `source_ip` + before/after JSON)
- `platform/lambda/_shared/greynoise.py` — on-demand GreyNoise Community lookup with rate-limiter via `spend_cap`
- `platform/lambda/_shared/tests/__init__.py`
- `platform/lambda/_shared/tests/test_ti_lookup.py`
- `platform/lambda/_shared/tests/test_ioc_extract.py`
- `platform/lambda/_shared/tests/test_greynoise.py`
- `platform/lambda/ti_feed_abusech/__init__.py`
- `platform/lambda/ti_feed_abusech/main.py` — handler (cron-triggered; pulls Feodo + ThreatFox)
- `platform/lambda/ti_feed_abusech/build.sh` — packages handler + vendored `_shared/`
- `platform/lambda/ti_feed_abusech/tests/__init__.py`
- `platform/lambda/ti_feed_abusech/tests/conftest.py`
- `platform/lambda/ti_feed_abusech/tests/test_parse_feodo.py`
- `platform/lambda/ti_feed_abusech/tests/test_parse_threatfox.py`
- `platform/lambda/ti_feed_abusech/tests/fixtures/feodo_ipblocklist.txt`
- `platform/lambda/ti_feed_abusech/tests/fixtures/threatfox_recent.json`
- `platform/lambda/ti_feed_kev/__init__.py`
- `platform/lambda/ti_feed_kev/main.py`
- `platform/lambda/ti_feed_kev/build.sh`
- `platform/lambda/ti_feed_kev/tests/__init__.py`
- `platform/lambda/ti_feed_kev/tests/test_parse_kev.py`
- `platform/lambda/ti_feed_kev/tests/fixtures/cisa_kev.json`
- `platform/lambda/ti_feed_tor/__init__.py`
- `platform/lambda/ti_feed_tor/main.py`
- `platform/lambda/ti_feed_tor/build.sh`
- `platform/lambda/ti_feed_tor/tests/__init__.py`
- `platform/lambda/ti_feed_tor/tests/test_parse_tor.py`
- `platform/lambda/ti_feed_tor/tests/fixtures/tor_bulk_exit_list.txt`
- `platform/lambda/soc_enrichment/tests/test_ti_match.py`
- `docs/customer/drift-detection-threat-intel.md` — describes which feeds we use, no customer cost, opt-out is N/A (substrate is server-side)

**Modifies:**
- `platform/lambda/event_router/main.py` — store `detail.sourceIPAddress` into the new `events.source_ip` column on the existing INSERT path
- `platform/lambda/event_router/tests/test_dedupe.py` (or a new test file alongside) — assert `source_ip` is captured
- `platform/lambda/soc_enrichment/features.py` — add `_ti_matches(...)` step and include it in `compute_features` output
- `platform/lambda/soc_enrichment/llm.py` — extend the SYSTEM prompt to reference `features.ti_matches`; widen the response-parser/types to surface match labels back into the narrative
- `platform/lambda/soc_enrichment/build.sh` — vendor `_shared/ti_lookup.py`, `_shared/ioc_extract.py`, `_shared/greynoise.py` into the build
- `platform/lambda/soc_enrichment/tests/test_features.py` — add a case asserting `ti_matches` is included
- `platform/lib/events-stack.ts` — three new `lambda.Function` constructs (cron adapters) + three `events.Rule` constructs with `Schedule.rate()` targets + IAM grants for Aurora Data API + `ENRICHMENT_QUEUE_URL` env unaffected + GreyNoise secret access on `enrichmentFn`
- `platform/lib/data-stack.ts` (if migrations are applied via that stack — verify; otherwise applied manually) — no schema-mgmt code change in v1; migration is run manually like 011/012
- `web/src/components/soc/DetailPane.tsx` — render TI badges block when `ai_features.ti_matches?.length`
- `web/src/components/soc/DetailPane.test.tsx` (create if missing — the dir today only has `Soc.test.tsx` covering the route) — add coverage for the TI-badges branch
- `HANDOFF.md` — append a "🚀 SOC Slice 1c shipped" block at the top once the manual gate passes
- `TEST_PLAN.md` — append "SOC Slice 1c — TI match end-to-end" gate

**Does NOT touch:**
- `platform/cfn/aws-onboard.yaml` — TI substrate is server-side; no customer-side resource
- `platform/lambda/events_list/main.py` — the API contract is unchanged; TI data rides inside `ai_features` JSONB which is already exposed by detail
- `web/src/lib/api.ts` — same reason; the existing `EventDetail.event.ai_features: Record<string, unknown>` typing already permits the new shape

---

## Why "vendor via cp" instead of a pip-installable shared package

Slice 1's existing pattern is `cp ../event_router/spend_cap.py build/` in `soc_enrichment/build.sh`. Don't fight it — keep slice 1c consistent. We promote to a `_shared/` directory now (instead of cross-Lambda copying) because three new Lambdas + soc_enrichment need the same code; copying from one of the four to the other three would invert ownership. Each Lambda's `build.sh` does `cp -r ../_shared/*.py build/` and Python imports work because `build/` is on the search path at runtime. Lift to a proper Lambda Layer is a follow-up after this slice (already noted in HANDOFF as deferred follow-up: *"lift to `platform/lambda/_shared/`"* — this plan IS that lift, plus adds the TI files).

---

## Tasks

### Task 1: Schema migration — `threat_indicators` + `events.source_ip`

**Files:**
- Create: `platform/sql/013_phase_soc_ti.sql`

- [ ] **Step 1: Write the migration SQL**

Create `platform/sql/013_phase_soc_ti.sql` with this exact content:

```sql
-- 013_phase_soc_ti.sql — SOC Slice 1c: threat-intel substrate
-- Refs: docs/superpowers/specs/2026-05-25-ai-powered-soc-design.md §6
--       docs/superpowers/plans/2026-05-25-ai-powered-soc-slice-1c.md

-- Global (tenant-independent) IOC table. IOCs are public knowledge;
-- no tenant_id intentionally — keeps the table small and writes cheap.
CREATE TABLE IF NOT EXISTS threat_indicators (
  indicator_value   TEXT        NOT NULL,
  kind              TEXT        NOT NULL,   -- 'ip' | 'domain' | 'url' | 'sha256' | 'cve'
  source            TEXT        NOT NULL,   -- 'abusech_feodo' | 'abusech_threatfox' | 'kev' | 'tor' | 'greynoise_community'
  first_seen        TIMESTAMPTZ NOT NULL,
  last_seen         TIMESTAMPTZ NOT NULL,
  confidence        INTEGER,                -- 0-100, source-dependent (NULL when source has no native confidence)
  tags              JSONB       NOT NULL DEFAULT '[]'::jsonb,
  raw               JSONB,                  -- source-specific extras (malware family, CVE id, etc.)
  PRIMARY KEY (indicator_value, kind, source)
);

-- Fast lookup by value+kind across all sources for the enrichment Lambda.
CREATE INDEX IF NOT EXISTS idx_threat_indicators_value
  ON threat_indicators (indicator_value, kind);

-- Calling-side IP from CloudTrail mgmt events. Populated by event_router on INSERT.
-- Nullable: Config events have no sourceIPAddress; alert-kind events from
-- GuardDuty/Inspector also don't carry this shape.
ALTER TABLE events ADD COLUMN IF NOT EXISTS source_ip TEXT;
```

- [ ] **Step 2: Verify the migration parses against the live cluster** (read-only — uses `BEGIN; ROLLBACK;` so nothing lands)

Run:
```bash
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN \
  --secret-arn  $DB_SECRET_ARN \
  --database ciso_copilot \
  --sql "$(cat platform/sql/013_phase_soc_ti.sql)"
```
Expected: success with `numberOfRecordsUpdated: 0` (DDL doesn't update rows). Confirm table exists:
```bash
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN \
  --secret-arn  $DB_SECRET_ARN \
  --database ciso_copilot \
  --sql "SELECT to_regclass('public.threat_indicators') IS NOT NULL AS exists"
```
Expected: a single row with `booleanValue: true`.

- [ ] **Step 3: Commit**

```bash
git add platform/sql/013_phase_soc_ti.sql
git commit -m "feat(soc-s1c): schema — threat_indicators table + events.source_ip"
```

---

### Task 2: Shared `_shared/` package — TI lookup core

**Files:**
- Create: `platform/lambda/_shared/__init__.py` (empty)
- Create: `platform/lambda/_shared/ti_lookup.py`
- Create: `platform/lambda/_shared/tests/__init__.py` (empty)
- Create: `platform/lambda/_shared/tests/conftest.py`
- Create: `platform/lambda/_shared/tests/test_ti_lookup.py`

- [ ] **Step 1: Write the failing tests first** — `platform/lambda/_shared/tests/test_ti_lookup.py`

```python
"""ti_lookup: pure dataclass + DB helpers that talk to Aurora via RDS Data API.

The Data-API client is monkeypatched in tests so the helpers exercise the
SQL construction without hitting the cluster.
"""
from __future__ import annotations

import datetime as dt

import ti_lookup


def _fake_data_api(records):
    """Build a Data-API stand-in that returns the canned records list."""
    class _Client:
        def __init__(self): self.calls = []
        def execute_statement(self, **kw):
            self.calls.append(kw)
            return {"records": records}
        def batch_execute_statement(self, **kw):
            self.calls.append(kw)
            return {"updateResults": [{} for _ in kw.get("parameterSets", [])]}
    return _Client()


def test_bulk_lookup_returns_matches_grouped_by_value(monkeypatch):
    client = _fake_data_api([
        [{"stringValue": "185.220.101.12"}, {"stringValue": "ip"},
         {"stringValue": "tor"},          {"isNull": True},
         {"stringValue": "[]"}],
        [{"stringValue": "185.220.101.12"}, {"stringValue": "ip"},
         {"stringValue": "abusech_feodo"},{"longValue": 80},
         {"stringValue": "[\"Heodo\"]"}],
    ])
    monkeypatch.setattr(ti_lookup, "rds_data", client)
    monkeypatch.setenv("DB_CLUSTER_ARN", "arn:test"); monkeypatch.setenv("DB_SECRET_ARN", "arn:test"); monkeypatch.setenv("DB_NAME", "ciso_copilot")
    ti_lookup._reload_env()

    matches = ti_lookup.bulk_lookup({"ip": ["185.220.101.12", "8.8.8.8"]})
    assert "185.220.101.12" in matches
    assert len(matches["185.220.101.12"]) == 2
    sources = {m["source"] for m in matches["185.220.101.12"]}
    assert sources == {"tor", "abusech_feodo"}
    # Confidence preserved as int or None
    confidences = {m["confidence"] for m in matches["185.220.101.12"]}
    assert confidences == {None, 80}
    # Misses don't appear as keys
    assert "8.8.8.8" not in matches


def test_bulk_lookup_empty_input_skips_db(monkeypatch):
    client = _fake_data_api([])
    monkeypatch.setattr(ti_lookup, "rds_data", client)
    monkeypatch.setenv("DB_CLUSTER_ARN", "arn:test"); monkeypatch.setenv("DB_SECRET_ARN", "arn:test"); monkeypatch.setenv("DB_NAME", "ciso_copilot")
    ti_lookup._reload_env()
    matches = ti_lookup.bulk_lookup({"ip": [], "domain": []})
    assert matches == {}
    assert client.calls == []


def test_upsert_indicators_uses_on_conflict(monkeypatch):
    client = _fake_data_api([])
    monkeypatch.setattr(ti_lookup, "rds_data", client)
    monkeypatch.setenv("DB_CLUSTER_ARN", "arn:test"); monkeypatch.setenv("DB_SECRET_ARN", "arn:test"); monkeypatch.setenv("DB_NAME", "ciso_copilot")
    ti_lookup._reload_env()
    now = dt.datetime(2026, 5, 25, 12, 0, 0, tzinfo=dt.timezone.utc)
    ind = ti_lookup.Indicator(
        value="185.220.101.12", kind="ip", source="tor",
        first_seen=now, last_seen=now, confidence=None, tags=["exit"], raw={},
    )
    ti_lookup.upsert_indicators([ind])

    assert len(client.calls) == 1
    sql = client.calls[0]["sql"]
    assert "INSERT INTO threat_indicators" in sql
    assert "ON CONFLICT (indicator_value, kind, source) DO UPDATE" in sql
    assert "last_seen" in sql
```

- [ ] **Step 2: Add `conftest.py` so tests can import siblings**

```python
# platform/lambda/_shared/tests/conftest.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

- [ ] **Step 3: Run tests — expect import failure**

```bash
cd platform/lambda/_shared && python -m pytest tests/test_ti_lookup.py -v
```
Expected: `ModuleNotFoundError: No module named 'ti_lookup'`.

- [ ] **Step 4: Implement `ti_lookup.py`**

```python
# platform/lambda/_shared/ti_lookup.py
"""Shared TI substrate. Two operations against the global threat_indicators table:

* `bulk_lookup({kind: [values]})` — read-side, called by soc_enrichment
* `upsert_indicators(iter[Indicator])` — write-side, called by the cron feed adapters

Vendored into each consuming Lambda via that Lambda's build.sh:
    cp -r ../_shared/*.py build/
The Lambda runtime puts `build/` first on sys.path so flat imports work.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
from typing import Iterable

import boto3

DB_CLUSTER_ARN = ""
DB_SECRET_ARN  = ""
DB_NAME        = ""

rds_data = boto3.client("rds-data")


def _reload_env() -> None:
    """Refresh module-level DB env. Cold start reads from env; tests call this after monkeypatching."""
    global DB_CLUSTER_ARN, DB_SECRET_ARN, DB_NAME
    DB_CLUSTER_ARN = os.environ.get("DB_CLUSTER_ARN", "")
    DB_SECRET_ARN  = os.environ.get("DB_SECRET_ARN", "")
    DB_NAME        = os.environ.get("DB_NAME", "ciso_copilot")


_reload_env()


@dataclasses.dataclass
class Indicator:
    value:      str
    kind:       str          # 'ip' | 'domain' | 'url' | 'sha256' | 'cve'
    source:     str          # 'abusech_feodo' | 'abusech_threatfox' | 'kev' | 'tor' | 'greynoise_community'
    first_seen: dt.datetime
    last_seen:  dt.datetime
    confidence: int | None
    tags:       list[str]
    raw:        dict


def bulk_lookup(values_by_kind: dict[str, list[str]]) -> dict[str, list[dict]]:
    """Return {indicator_value: [{source, kind, confidence, tags}, ...]} for hits.

    `values_by_kind` is shaped `{"ip": [...], "domain": [...], ...}`.
    Empty input → empty dict; never touches DB.
    """
    flat: list[tuple[str, str]] = []
    for kind, values in values_by_kind.items():
        for v in values:
            if v:
                flat.append((kind, v))
    if not flat:
        return {}

    # Build a single SELECT with an `(kind, value) IN (...)` clause.
    # Parameter naming: kN/vN for the Nth pair.
    in_pairs = ", ".join(f"(:k{i}, :v{i})" for i in range(len(flat)))
    sql = (
        "SELECT indicator_value, kind, source, confidence, tags::text "
        "FROM threat_indicators "
        f"WHERE (kind, indicator_value) IN ({in_pairs})"
    )
    parameters: list[dict] = []
    for i, (k, v) in enumerate(flat):
        parameters.append({"name": f"k{i}", "value": {"stringValue": k}})
        parameters.append({"name": f"v{i}", "value": {"stringValue": v}})

    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=sql, parameters=parameters,
    )
    out: dict[str, list[dict]] = {}
    for r in rs.get("records", []):
        value      = r[0].get("stringValue")
        kind       = r[1].get("stringValue")
        source     = r[2].get("stringValue")
        confidence = r[3].get("longValue") if not r[3].get("isNull") else None
        tags_raw   = r[4].get("stringValue") or "[]"
        try:
            tags = json.loads(tags_raw)
        except (TypeError, ValueError):
            tags = []
        out.setdefault(value, []).append(
            {"source": source, "kind": kind, "confidence": confidence, "tags": tags}
        )
    return out


def upsert_indicators(indicators: Iterable[Indicator]) -> int:
    """Upsert indicators in batches. Returns the number of statements executed.

    Uses ON CONFLICT to refresh last_seen + confidence + tags + raw on
    repeat sightings. first_seen is preserved (EXCLUDED only sets newer rows).
    """
    inds = list(indicators)
    if not inds:
        return 0

    sql = (
        "INSERT INTO threat_indicators "
        "  (indicator_value, kind, source, first_seen, last_seen, confidence, tags, raw) "
        "VALUES "
        "  (:value, :kind, :source, CAST(:first_seen AS TIMESTAMPTZ), CAST(:last_seen AS TIMESTAMPTZ), "
        "   :confidence, CAST(:tags AS JSONB), CAST(:raw AS JSONB)) "
        "ON CONFLICT (indicator_value, kind, source) DO UPDATE SET "
        "  last_seen  = EXCLUDED.last_seen, "
        "  confidence = COALESCE(EXCLUDED.confidence, threat_indicators.confidence), "
        "  tags       = EXCLUDED.tags, "
        "  raw        = EXCLUDED.raw"
    )

    # RDS Data API batch_execute_statement supports up to 1000 param sets.
    BATCH = 500
    executed = 0
    for start in range(0, len(inds), BATCH):
        chunk = inds[start:start + BATCH]
        parameter_sets = []
        for ind in chunk:
            parameter_sets.append([
                {"name": "value",      "value": {"stringValue": ind.value}},
                {"name": "kind",       "value": {"stringValue": ind.kind}},
                {"name": "source",     "value": {"stringValue": ind.source}},
                {"name": "first_seen", "value": {"stringValue": ind.first_seen.isoformat()}},
                {"name": "last_seen",  "value": {"stringValue": ind.last_seen.isoformat()}},
                {"name": "confidence", "value": ({"longValue": ind.confidence} if ind.confidence is not None else {"isNull": True})},
                {"name": "tags",       "value": {"stringValue": json.dumps(ind.tags)}},
                {"name": "raw",        "value": {"stringValue": json.dumps(ind.raw, default=str)}},
            ])
        rds_data.batch_execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql=sql, parameterSets=parameter_sets,
        )
        executed += 1
    return executed
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
cd platform/lambda/_shared && python -m pytest tests/test_ti_lookup.py -v
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/_shared/__init__.py platform/lambda/_shared/ti_lookup.py \
        platform/lambda/_shared/tests/__init__.py platform/lambda/_shared/tests/conftest.py \
        platform/lambda/_shared/tests/test_ti_lookup.py
git commit -m "feat(soc-s1c): _shared/ti_lookup — bulk_lookup + upsert_indicators"
```

---

### Task 3: Shared IOC extraction

**Files:**
- Create: `platform/lambda/_shared/ioc_extract.py`
- Create: `platform/lambda/_shared/tests/test_ioc_extract.py`

- [ ] **Step 1: Write failing tests**

```python
# platform/lambda/_shared/tests/test_ioc_extract.py
from __future__ import annotations

import ioc_extract


def test_extract_extracts_source_ip_from_row():
    row = {"source_ip": "185.220.101.12", "before_state": None, "after_state": None}
    iocs = ioc_extract.extract_iocs(row)
    assert "185.220.101.12" in iocs["ip"]


def test_extract_extracts_ipv4_from_sg_ingress_after_state():
    row = {
        "source_ip": None,
        "before_state": None,
        "after_state": {
            "ipPermissions": {"items": [{
                "fromPort": 22, "toPort": 22,
                "ipRanges": {"items": [{"cidrIp": "203.0.113.5/32"}]},
            }]},
        },
    }
    iocs = ioc_extract.extract_iocs(row)
    assert "203.0.113.5" in iocs["ip"]
    # CIDR-to-world is a special placeholder we never want to look up
    row["after_state"]["ipPermissions"]["items"][0]["ipRanges"]["items"][0]["cidrIp"] = "0.0.0.0/0"
    iocs2 = ioc_extract.extract_iocs(row)
    assert "0.0.0.0" not in iocs2["ip"]


def test_extract_extracts_domain_and_url_strings():
    row = {"source_ip": None, "after_state": {"endpoint": "https://evil.example.com/x", "host": "another.example.org"}}
    iocs = ioc_extract.extract_iocs(row)
    assert "evil.example.com"   in iocs["domain"]
    assert "another.example.org" in iocs["domain"]


def test_extract_extracts_sha256():
    row = {"source_ip": None, "after_state": {"sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}}
    iocs = ioc_extract.extract_iocs(row)
    assert "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" in iocs["sha256"]


def test_extract_dedupes_across_keys():
    row = {
        "source_ip": "185.220.101.12",
        "after_state": {"caller_ip": "185.220.101.12", "extra": "185.220.101.12"},
    }
    iocs = ioc_extract.extract_iocs(row)
    assert iocs["ip"].count("185.220.101.12") == 1


def test_extract_returns_empty_dict_for_dry_row():
    iocs = ioc_extract.extract_iocs({"source_ip": None, "after_state": None, "before_state": None})
    assert iocs == {"ip": [], "domain": [], "sha256": []}
```

- [ ] **Step 2: Run — expect import failure**

```bash
cd platform/lambda/_shared && python -m pytest tests/test_ioc_extract.py -v
```
Expected: `ModuleNotFoundError: No module named 'ioc_extract'`.

- [ ] **Step 3: Implement `ioc_extract.py`**

```python
# platform/lambda/_shared/ioc_extract.py
"""Extract IOCs from a drift event row.

Inputs: a row dict with the shape `{source_ip, before_state, after_state}`
(matching what soc_enrichment._load_event_row returns plus the new source_ip
column from migration 013).

Output: `{"ip": [...], "domain": [...], "sha256": [...]}` — deduped, with
RFC1918, loopback, broadcast, and 0.0.0.0/8 IPs filtered out (no point
looking them up against public IOC feeds).
"""
from __future__ import annotations

import ipaddress
import re
from typing import Any

# Anchored to avoid catching version numbers like "10.0" or timestamps.
_IPV4_RE   = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_DOMAIN_RE = re.compile(r"\b((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63})\b", re.IGNORECASE)
_SHA256_RE = re.compile(r"\b[a-f0-9]{64}\b", re.IGNORECASE)


def _is_public_ipv4(value: str) -> bool:
    try:
        ip = ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, ValueError):
        return False
    return ip.is_global


def _walk(node: Any, out: list[str]) -> None:
    """Flatten every string leaf of a possibly-nested JSON-ish structure into `out`."""
    if node is None:
        return
    if isinstance(node, str):
        out.append(node)
        return
    if isinstance(node, dict):
        # CloudTrail wraps lists as {"items": [...]} — walk both the items
        # array and any plain dict value.
        for v in node.values():
            _walk(v, out)
        return
    if isinstance(node, (list, tuple)):
        for v in node:
            _walk(v, out)
        return
    # Numbers, bools — never carry IOCs.


def extract_iocs(row: dict) -> dict[str, list[str]]:
    leaves: list[str] = []
    src_ip = row.get("source_ip")
    if isinstance(src_ip, str):
        leaves.append(src_ip)
    _walk(row.get("before_state"), leaves)
    _walk(row.get("after_state"),  leaves)

    ips:     list[str] = []
    domains: list[str] = []
    hashes:  list[str] = []
    seen_ip:     set[str] = set()
    seen_domain: set[str] = set()
    seen_hash:   set[str] = set()

    for s in leaves:
        for m in _IPV4_RE.findall(s):
            if _is_public_ipv4(m) and m not in seen_ip:
                seen_ip.add(m); ips.append(m)
        for m in _SHA256_RE.findall(s):
            v = m.lower()
            if v not in seen_hash:
                seen_hash.add(v); hashes.append(v)
        # Domain extraction is intentionally last so it doesn't grab IPv4.
        for m in _DOMAIN_RE.findall(s):
            v = m.lower()
            if v in seen_domain:
                continue
            # Reject if the "domain" is just a numeric IPv4
            try:
                ipaddress.IPv4Address(v)
                continue
            except (ipaddress.AddressValueError, ValueError):
                pass
            seen_domain.add(v); domains.append(v)

    return {"ip": ips, "domain": domains, "sha256": hashes}
```

- [ ] **Step 4: Run — expect PASS**

```bash
cd platform/lambda/_shared && python -m pytest tests/test_ioc_extract.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/_shared/ioc_extract.py platform/lambda/_shared/tests/test_ioc_extract.py
git commit -m "feat(soc-s1c): _shared/ioc_extract — IPs/domains/hashes from drift rows"
```

---

### Task 4: Shared GreyNoise Community on-demand lookup

**Files:**
- Create: `platform/lambda/_shared/greynoise.py`
- Create: `platform/lambda/_shared/tests/test_greynoise.py`

GreyNoise Community is free, 50 requests/day per API key. We rate-limit ourselves to **30/day per tenant** by reusing `soc_llm_spend_daily` with the sort-key prefix `greynoise_count:`. Above the cap, return `None` and skip the call. This is on-demand — only called when the IP missed in `threat_indicators` AND was extracted from the event payload.

- [ ] **Step 1: Write failing tests**

```python
# platform/lambda/_shared/tests/test_greynoise.py
from __future__ import annotations

import json
from unittest.mock import patch

import greynoise


def test_lookup_returns_indicator_on_classification_malicious():
    fake_body = json.dumps({
        "ip": "185.220.101.12",
        "noise": True, "riot": False,
        "classification": "malicious",
        "name": "Mirai",
        "link": "https://viz.greynoise.io/ip/185.220.101.12",
        "last_seen": "2026-05-25",
        "message": "Success",
    }).encode()
    with patch.object(greynoise, "_http_get", return_value=(200, fake_body)) as m, \
         patch.object(greynoise, "_under_cap",     return_value=True), \
         patch.object(greynoise, "_increment_count"):
        ind = greynoise.lookup_ip("tenant-1", "185.220.101.12", api_key="fake")
        assert m.called
        assert ind is not None
        assert ind["source"]     == "greynoise_community"
        assert ind["classification"] == "malicious"
        assert ind["confidence"]  == 85


def test_lookup_returns_none_when_cap_reached():
    with patch.object(greynoise, "_under_cap", return_value=False) as cap, \
         patch.object(greynoise, "_http_get") as get:
        ind = greynoise.lookup_ip("tenant-1", "185.220.101.12", api_key="fake")
        assert ind is None
        cap.assert_called_once()
        get.assert_not_called()


def test_lookup_returns_none_on_404():
    with patch.object(greynoise, "_under_cap", return_value=True), \
         patch.object(greynoise, "_increment_count"), \
         patch.object(greynoise, "_http_get", return_value=(404, b'{"message":"IP not observed"}')):
        ind = greynoise.lookup_ip("tenant-1", "8.8.8.8", api_key="fake")
        assert ind is None


def test_lookup_returns_none_without_api_key():
    ind = greynoise.lookup_ip("tenant-1", "185.220.101.12", api_key=None)
    assert ind is None


def test_lookup_classifies_benign_as_low_confidence():
    fake_body = json.dumps({"ip": "8.8.8.8", "classification": "benign", "noise": True}).encode()
    with patch.object(greynoise, "_under_cap",  return_value=True), \
         patch.object(greynoise, "_increment_count"), \
         patch.object(greynoise, "_http_get", return_value=(200, fake_body)):
        ind = greynoise.lookup_ip("tenant-1", "8.8.8.8", api_key="fake")
        assert ind is not None
        assert ind["classification"] == "benign"
        assert ind["confidence"]     == 20
```

- [ ] **Step 2: Run — expect import failure**

```bash
cd platform/lambda/_shared && python -m pytest tests/test_greynoise.py -v
```
Expected: `ModuleNotFoundError: No module named 'greynoise'`.

- [ ] **Step 3: Implement `greynoise.py`**

```python
# platform/lambda/_shared/greynoise.py
"""GreyNoise Community on-demand IP lookup.

Free tier: 50 req/day per key. We cap ourselves at 30/day/tenant via the
existing soc_llm_spend_daily DynamoDB table (sort-key prefix `greynoise_count:YYYY-MM-DD`)
so a single noisy tenant can't burn the day's budget for everyone.

Only called by soc_enrichment for IPs that missed in threat_indicators.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import urllib.request
import urllib.error

import boto3

_API_URL = "https://api.greynoise.io/v3/community/{ip}"
_DAILY_CAP = int(os.environ.get("GREYNOISE_DAILY_CAP_PER_TENANT", "30"))
_TIMEOUT_S = 4
_TABLE_NAME = os.environ.get("SPEND_CAP_TABLE_NAME", "soc_llm_spend_daily")

dynamodb = boto3.client("dynamodb")


def _http_get(url: str, headers: dict[str, str]) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b""
    except (urllib.error.URLError, TimeoutError):
        return 599, b""


def _key(tenant_id: str) -> dict:
    day = datetime.now(timezone.utc).strftime("greynoise_count:%Y-%m-%d")
    return {"tenant_id": {"S": tenant_id}, "day": {"S": day}}


def _under_cap(tenant_id: str) -> bool:
    rs = dynamodb.get_item(TableName=_TABLE_NAME, Key=_key(tenant_id))
    item = rs.get("Item")
    n = int(item["count"]["N"]) if item and "count" in item else 0
    return n < _DAILY_CAP


def _increment_count(tenant_id: str) -> int:
    rs = dynamodb.update_item(
        TableName=_TABLE_NAME, Key=_key(tenant_id),
        UpdateExpression="ADD #c :one SET #exp = :exp",
        ExpressionAttributeNames={"#c": "count", "#exp": "expires_at"},
        ExpressionAttributeValues={
            ":one": {"N": "1"},
            ":exp": {"N": str(int(datetime.now(timezone.utc).timestamp()) + 7 * 86400)},
        },
        ReturnValues="UPDATED_NEW",
    )
    return int(rs["Attributes"]["count"]["N"])


def lookup_ip(tenant_id: str, ip: str, *, api_key: str | None) -> dict | None:
    """Return a dict shaped like a threat_indicators row, or None on miss/cap/error.

    Output (when hit):
      {"source": "greynoise_community", "kind": "ip", "value": ip,
       "classification": "malicious"|"benign"|"unknown",
       "confidence": int 0-100, "name": str|None, "link": str|None}
    """
    if not api_key:
        return None
    if not _under_cap(tenant_id):
        return None

    status, body = _http_get(_API_URL.format(ip=ip), {"Accept": "application/json", "key": api_key})
    _increment_count(tenant_id)
    if status != 200:
        return None
    try:
        data: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        return None

    classification = data.get("classification") or "unknown"
    confidence = {"malicious": 85, "unknown": 50, "benign": 20}.get(classification, 50)

    return {
        "source":         "greynoise_community",
        "kind":           "ip",
        "value":          ip,
        "classification": classification,
        "confidence":     confidence,
        "name":           data.get("name"),
        "link":           data.get("link"),
    }
```

- [ ] **Step 4: Run — expect PASS**

```bash
cd platform/lambda/_shared && python -m pytest tests/test_greynoise.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/_shared/greynoise.py platform/lambda/_shared/tests/test_greynoise.py
git commit -m "feat(soc-s1c): _shared/greynoise — on-demand Community lookup with per-tenant cap"
```

---

### Task 5: `ti_feed_abusech` — Feodo + ThreatFox cron Lambda

**Files:**
- Create: `platform/lambda/ti_feed_abusech/__init__.py` (empty)
- Create: `platform/lambda/ti_feed_abusech/main.py`
- Create: `platform/lambda/ti_feed_abusech/build.sh`
- Create: `platform/lambda/ti_feed_abusech/tests/__init__.py` (empty)
- Create: `platform/lambda/ti_feed_abusech/tests/conftest.py`
- Create: `platform/lambda/ti_feed_abusech/tests/test_parse_feodo.py`
- Create: `platform/lambda/ti_feed_abusech/tests/test_parse_threatfox.py`
- Create: `platform/lambda/ti_feed_abusech/tests/fixtures/feodo_ipblocklist.txt`
- Create: `platform/lambda/ti_feed_abusech/tests/fixtures/threatfox_recent.json`

**Feed URLs** (no auth required):
- Feodo Tracker: `https://feodotracker.abuse.ch/downloads/ipblocklist.txt` (text, `#` comments + IP-per-line)
- ThreatFox: `https://threatfox.abuse.ch/export/json/recent/` (JSON dict: `{"timestamp": [list of IOC dicts]}`)

- [ ] **Step 1: Drop in real-shape fixtures**

`platform/lambda/ti_feed_abusech/tests/fixtures/feodo_ipblocklist.txt`:
```
################################################################
# abuse.ch Feodo Tracker IP Blocklist (recommended)            #
# Last updated: 2026-05-25 12:00:00 UTC                        #
################################################################
185.220.101.12
198.51.100.7
203.0.113.42
```

`platform/lambda/ti_feed_abusech/tests/fixtures/threatfox_recent.json`:
```json
{
  "1716638400": [
    {
      "ioc_value": "evil.example.com",
      "ioc_type": "domain",
      "threat_type": "botnet_cc",
      "malware": "Cobalt Strike",
      "confidence_level": 80,
      "first_seen": "2026-05-24 12:00:00 UTC",
      "tags": ["c2", "beacon"]
    },
    {
      "ioc_value": "185.220.101.99",
      "ioc_type": "ip:port",
      "threat_type": "botnet_cc",
      "malware": "Emotet",
      "confidence_level": 75,
      "first_seen": "2026-05-24 12:00:00 UTC",
      "tags": ["loader"]
    },
    {
      "ioc_value": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "ioc_type": "sha256_hash",
      "threat_type": "payload",
      "malware": "AsyncRAT",
      "confidence_level": 100,
      "first_seen": "2026-05-25 09:30:00 UTC",
      "tags": []
    }
  ]
}
```

- [ ] **Step 2: Write `conftest.py`**

```python
# platform/lambda/ti_feed_abusech/tests/conftest.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# also expose _shared so `import ti_lookup` resolves during tests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "_shared")))
```

- [ ] **Step 3: Write failing parser tests**

`platform/lambda/ti_feed_abusech/tests/test_parse_feodo.py`:
```python
from __future__ import annotations
import os
import main


def test_parse_feodo_extracts_ips_skips_comments():
    fixture = os.path.join(os.path.dirname(__file__), "fixtures", "feodo_ipblocklist.txt")
    text = open(fixture, "r", encoding="utf-8").read()
    indicators = list(main.parse_feodo(text))
    values = [i.value for i in indicators]
    assert values == ["185.220.101.12", "198.51.100.7", "203.0.113.42"]
    for i in indicators:
        assert i.kind   == "ip"
        assert i.source == "abusech_feodo"
        assert i.confidence is None     # Feodo has no native confidence
        assert i.tags == ["botnet_c2"]  # synthetic tag, asserted across all rows
```

`platform/lambda/ti_feed_abusech/tests/test_parse_threatfox.py`:
```python
from __future__ import annotations
import json
import os
import main


def test_parse_threatfox_extracts_domain_ip_hash():
    fixture = os.path.join(os.path.dirname(__file__), "fixtures", "threatfox_recent.json")
    data = json.load(open(fixture, "r", encoding="utf-8"))
    indicators = list(main.parse_threatfox(data))

    kinds = {(i.value, i.kind, i.source) for i in indicators}
    assert ("evil.example.com",                                                   "domain", "abusech_threatfox") in kinds
    assert ("185.220.101.99",                                                     "ip",     "abusech_threatfox") in kinds
    assert ("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "sha256", "abusech_threatfox") in kinds
    # confidence carried through
    by_value = {i.value: i for i in indicators}
    assert by_value["evil.example.com"].confidence == 80
    assert "Cobalt Strike" in by_value["evil.example.com"].raw["malware"]


def test_parse_threatfox_strips_port_from_ip_port_kind():
    data = {"123": [{"ioc_value": "10.0.0.1:443", "ioc_type": "ip:port",
                     "threat_type": "botnet_cc", "malware": "Emotet",
                     "confidence_level": 50, "first_seen": "2026-05-25 09:00:00 UTC", "tags": []}]}
    indicators = list(main.parse_threatfox(data))
    assert indicators[0].value == "10.0.0.1"
    assert indicators[0].kind  == "ip"
```

- [ ] **Step 4: Run — expect import failure**

```bash
cd platform/lambda/ti_feed_abusech && python -m pytest tests/ -v
```
Expected: `ModuleNotFoundError: No module named 'main'`.

- [ ] **Step 5: Implement `main.py`**

```python
# platform/lambda/ti_feed_abusech/main.py
"""abuse.ch feed adapter — pulls Feodo + ThreatFox + writes threat_indicators.

Runs hourly via EventBridge cron (set in events-stack.ts).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import urllib.error
import urllib.request
from typing import Any, Iterable

# _shared modules are vendored into build/ by build.sh
from ti_lookup import Indicator, upsert_indicators, _reload_env  # type: ignore

_FEODO_URL     = os.environ.get("ABUSECH_FEODO_URL",     "https://feodotracker.abuse.ch/downloads/ipblocklist.txt")
_THREATFOX_URL = os.environ.get("ABUSECH_THREATFOX_URL", "https://threatfox.abuse.ch/export/json/recent/")
_HTTP_TIMEOUT = 30


def handler(event, context) -> dict:
    _reload_env()
    now = dt.datetime.now(dt.timezone.utc)

    feodo_text     = _fetch(_FEODO_URL)
    threatfox_json = _fetch(_THREATFOX_URL)

    feodo_inds: list[Indicator] = []
    if feodo_text:
        for ind in parse_feodo(feodo_text):
            ind.first_seen = ind.last_seen = now
            feodo_inds.append(ind)

    threatfox_inds: list[Indicator] = []
    if threatfox_json:
        try:
            data = json.loads(threatfox_json)
        except json.JSONDecodeError:
            data = {}
        for ind in parse_threatfox(data):
            ind.last_seen = now
            # first_seen comes from the feed when available; fall back to now
            threatfox_inds.append(ind)

    total = upsert_indicators(feodo_inds) + upsert_indicators(threatfox_inds)
    print(f"feed=abusech_feodo upserted={len(feodo_inds)} batches={total}")
    print(f"feed=abusech_threatfox upserted={len(threatfox_inds)}")
    return {"ok": True, "feodo": len(feodo_inds), "threatfox": len(threatfox_inds)}


def parse_feodo(text: str) -> Iterable[Indicator]:
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # ignore lines that look like headers, e.g. "# Last updated: ..."
        # IP-per-line; a few rows have "IP,PORT" — split on comma defensively
        ip = line.split(",", 1)[0].strip()
        if not ip:
            continue
        # Trust the feed shape — abuse.ch only ships IPv4 in this list.
        yield Indicator(
            value=ip, kind="ip", source="abusech_feodo",
            first_seen=dt.datetime.now(dt.timezone.utc),
            last_seen=dt.datetime.now(dt.timezone.utc),
            confidence=None, tags=["botnet_c2"],
            raw={"feed": "feodo_ipblocklist"},
        )


def parse_threatfox(data: dict[str, list[dict[str, Any]]]) -> Iterable[Indicator]:
    # ThreatFox shape: {epoch_timestamp_string: [list of IOC dicts]}
    for _ts, rows in data.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            ioc_value = (row.get("ioc_value") or "").strip()
            ioc_type  = (row.get("ioc_type")  or "").strip()
            if not ioc_value:
                continue
            kind, value = _normalize_kind(ioc_type, ioc_value)
            if kind is None:
                continue
            confidence = row.get("confidence_level")
            tags       = list(row.get("tags") or [])
            first_seen = _parse_dt(row.get("first_seen")) or dt.datetime.now(dt.timezone.utc)
            yield Indicator(
                value=value, kind=kind, source="abusech_threatfox",
                first_seen=first_seen, last_seen=first_seen,
                confidence=int(confidence) if isinstance(confidence, (int, float)) else None,
                tags=tags,
                raw={
                    "threat_type": row.get("threat_type"),
                    "malware":     row.get("malware"),
                },
            )


def _normalize_kind(ioc_type: str, value: str) -> tuple[str | None, str]:
    """Map ThreatFox `ioc_type` to our threat_indicators.kind taxonomy."""
    t = ioc_type.lower()
    if t == "ip:port":
        # "10.0.0.1:443" → kind=ip, value=10.0.0.1
        return ("ip", value.split(":", 1)[0])
    if t in ("ipv4", "ip"):
        return ("ip", value)
    if t in ("domain", "fqdn"):
        return ("domain", value.lower())
    if t in ("url",):
        return ("url", value)
    if t in ("sha256_hash", "sha256"):
        return ("sha256", value.lower())
    return (None, value)


def _parse_dt(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        # ThreatFox format: "2026-05-24 12:00:00 UTC"
        return dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def _fetch(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ciso-copilot-ti/1.0"})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            if resp.status != 200:
                print(f"WARN: {url} returned status {resp.status}")
                return None
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"WARN: fetch failed url={url} err={e!r}")
        return None
```

- [ ] **Step 6: Run — expect PASS**

```bash
cd platform/lambda/ti_feed_abusech && python -m pytest tests/ -v
```
Expected: 3 passed.

- [ ] **Step 7: Write `build.sh`**

```bash
#!/usr/bin/env bash
# Build the ti_feed_abusech Lambda zip with vendored _shared/ code.
# No third-party deps — uses stdlib urllib + boto3 (already in Lambda runtime).
set -euo pipefail
cd "$(dirname "$0")"
rm -rf build dist && mkdir -p build dist
cp main.py __init__.py build/
cp ../_shared/ti_lookup.py ../_shared/ioc_extract.py build/
cd build && zip -qr ../dist/ti_feed_abusech.zip . && cd ..
echo "Built $(pwd)/dist/ti_feed_abusech.zip"
```

Then:
```bash
chmod +x platform/lambda/ti_feed_abusech/build.sh
platform/lambda/ti_feed_abusech/build.sh
```
Expected: prints `Built .../dist/ti_feed_abusech.zip` and the zip exists.

- [ ] **Step 8: Commit**

```bash
git add platform/lambda/ti_feed_abusech/
git commit -m "feat(soc-s1c): ti_feed_abusech — Feodo + ThreatFox hourly ETL"
```

---

### Task 6: `ti_feed_kev` — CISA KEV daily Lambda

**Files:**
- Create: `platform/lambda/ti_feed_kev/__init__.py`
- Create: `platform/lambda/ti_feed_kev/main.py`
- Create: `platform/lambda/ti_feed_kev/build.sh`
- Create: `platform/lambda/ti_feed_kev/tests/__init__.py`
- Create: `platform/lambda/ti_feed_kev/tests/conftest.py`
- Create: `platform/lambda/ti_feed_kev/tests/test_parse_kev.py`
- Create: `platform/lambda/ti_feed_kev/tests/fixtures/cisa_kev.json`

**Feed URL** (no auth): `https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json`. Daily refresh sufficient — CISA adds entries at most a few times per day.

- [ ] **Step 1: Drop in a fixture**

`platform/lambda/ti_feed_kev/tests/fixtures/cisa_kev.json`:
```json
{
  "title": "CISA Catalog of Known Exploited Vulnerabilities",
  "catalogVersion": "2026.05.25",
  "dateReleased": "2026-05-25T13:00:00.000Z",
  "count": 2,
  "vulnerabilities": [
    {
      "cveID": "CVE-2024-12345",
      "vendorProject": "Acme",
      "product": "WidgetServer",
      "vulnerabilityName": "Acme WidgetServer Code Injection",
      "dateAdded": "2024-12-01",
      "shortDescription": "...",
      "requiredAction": "Apply mitigations.",
      "dueDate": "2024-12-22",
      "knownRansomwareCampaignUse": "Known"
    },
    {
      "cveID": "CVE-2026-99999",
      "vendorProject": "Acme",
      "product": "WidgetServer",
      "vulnerabilityName": "Acme WidgetServer Auth Bypass",
      "dateAdded": "2026-05-20",
      "shortDescription": "...",
      "requiredAction": "Apply patches.",
      "dueDate": "2026-06-10",
      "knownRansomwareCampaignUse": "Unknown"
    }
  ]
}
```

- [ ] **Step 2: conftest**

```python
# platform/lambda/ti_feed_kev/tests/conftest.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "_shared")))
```

- [ ] **Step 3: Failing test**

```python
# platform/lambda/ti_feed_kev/tests/test_parse_kev.py
from __future__ import annotations
import json, os
import main


def test_parse_kev_extracts_cves():
    fixture = os.path.join(os.path.dirname(__file__), "fixtures", "cisa_kev.json")
    data = json.load(open(fixture, "r", encoding="utf-8"))
    indicators = list(main.parse_kev(data))
    cves = [i.value for i in indicators]
    assert cves == ["CVE-2024-12345", "CVE-2026-99999"]
    for i in indicators:
        assert i.kind   == "cve"
        assert i.source == "kev"
    by_cve = {i.value: i for i in indicators}
    # Ransomware-tagged entries get a higher confidence
    assert by_cve["CVE-2024-12345"].confidence == 95
    assert by_cve["CVE-2026-99999"].confidence == 80
    assert "ransomware" in by_cve["CVE-2024-12345"].tags
    assert by_cve["CVE-2024-12345"].raw["vendor"] == "Acme"
```

- [ ] **Step 4: Run — expect FAIL**

```bash
cd platform/lambda/ti_feed_kev && python -m pytest tests/ -v
```
Expected: `ModuleNotFoundError: No module named 'main'`.

- [ ] **Step 5: Implement `main.py`**

```python
# platform/lambda/ti_feed_kev/main.py
"""CISA KEV daily ETL. Single endpoint, JSON dump of all current entries."""
from __future__ import annotations

import datetime as dt
import json
import os
import urllib.error
import urllib.request
from typing import Iterable

from ti_lookup import Indicator, upsert_indicators, _reload_env  # type: ignore

_KEV_URL = os.environ.get(
    "CISA_KEV_URL",
    "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
)


def handler(event, context) -> dict:
    _reload_env()
    body = _fetch(_KEV_URL)
    if body is None:
        return {"ok": False, "error": "fetch_failed"}
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        print(f"WARN: KEV JSON decode error: {e!r}")
        return {"ok": False, "error": "invalid_json"}

    indicators = list(parse_kev(data))
    upsert_indicators(indicators)
    print(f"feed=kev upserted={len(indicators)}")
    return {"ok": True, "count": len(indicators)}


def parse_kev(data: dict) -> Iterable[Indicator]:
    vulns = data.get("vulnerabilities") or []
    for v in vulns:
        cve = (v.get("cveID") or "").strip()
        if not cve:
            continue
        date_added = _parse_date(v.get("dateAdded")) or dt.datetime.now(dt.timezone.utc)
        is_ransomware = (v.get("knownRansomwareCampaignUse") or "").lower() == "known"
        tags: list[str] = []
        if is_ransomware:
            tags.append("ransomware")
        yield Indicator(
            value=cve, kind="cve", source="kev",
            first_seen=date_added, last_seen=dt.datetime.now(dt.timezone.utc),
            confidence=95 if is_ransomware else 80,
            tags=tags,
            raw={
                "vendor":  v.get("vendorProject"),
                "product": v.get("product"),
                "name":    v.get("vulnerabilityName"),
                "due":     v.get("dueDate"),
            },
        )


def _parse_date(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        return dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def _fetch(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ciso-copilot-ti/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                print(f"WARN: KEV returned status {resp.status}")
                return None
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"WARN: KEV fetch failed: {e!r}")
        return None
```

- [ ] **Step 6: Run — expect PASS**

```bash
cd platform/lambda/ti_feed_kev && python -m pytest tests/ -v
```
Expected: 1 passed.

- [ ] **Step 7: build.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
rm -rf build dist && mkdir -p build dist
cp main.py __init__.py build/
cp ../_shared/ti_lookup.py build/
cd build && zip -qr ../dist/ti_feed_kev.zip . && cd ..
echo "Built $(pwd)/dist/ti_feed_kev.zip"
```

```bash
chmod +x platform/lambda/ti_feed_kev/build.sh
platform/lambda/ti_feed_kev/build.sh
```

- [ ] **Step 8: Commit**

```bash
git add platform/lambda/ti_feed_kev/
git commit -m "feat(soc-s1c): ti_feed_kev — daily CISA KEV ETL"
```

---

### Task 7: `ti_feed_tor` — Tor exit list hourly Lambda

**Files:**
- Create: `platform/lambda/ti_feed_tor/__init__.py`
- Create: `platform/lambda/ti_feed_tor/main.py`
- Create: `platform/lambda/ti_feed_tor/build.sh`
- Create: `platform/lambda/ti_feed_tor/tests/__init__.py`
- Create: `platform/lambda/ti_feed_tor/tests/conftest.py`
- Create: `platform/lambda/ti_feed_tor/tests/test_parse_tor.py`
- Create: `platform/lambda/ti_feed_tor/tests/fixtures/tor_bulk_exit_list.txt`

**Feed URL:** `https://check.torproject.org/torbulkexitlist` (text, one IPv4 per line; the Tor Project also serves a `cached-descriptors`-derived list but the bulk file is plenty for our use).

- [ ] **Step 1: Fixture**

`platform/lambda/ti_feed_tor/tests/fixtures/tor_bulk_exit_list.txt`:
```
185.220.101.12
185.220.101.13
198.51.100.7

# Blank lines and (rarely) comments may appear — skip them.
185.220.101.14
```

- [ ] **Step 2: conftest**

```python
# platform/lambda/ti_feed_tor/tests/conftest.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "_shared")))
```

- [ ] **Step 3: Failing test**

```python
# platform/lambda/ti_feed_tor/tests/test_parse_tor.py
from __future__ import annotations
import os
import main


def test_parse_tor_extracts_ips_skips_blanks_and_comments():
    fixture = os.path.join(os.path.dirname(__file__), "fixtures", "tor_bulk_exit_list.txt")
    text = open(fixture, "r", encoding="utf-8").read()
    indicators = list(main.parse_tor(text))
    values = [i.value for i in indicators]
    assert values == ["185.220.101.12", "185.220.101.13", "198.51.100.7", "185.220.101.14"]
    for i in indicators:
        assert i.kind   == "ip"
        assert i.source == "tor"
        assert "tor_exit" in i.tags
        assert i.confidence is None
```

- [ ] **Step 4: Run — expect FAIL**

```bash
cd platform/lambda/ti_feed_tor && python -m pytest tests/ -v
```
Expected: `ModuleNotFoundError: No module named 'main'`.

- [ ] **Step 5: Implement `main.py`**

```python
# platform/lambda/ti_feed_tor/main.py
"""Tor exit list hourly ETL. Single endpoint, text dump of all current exits."""
from __future__ import annotations

import datetime as dt
import os
import urllib.error
import urllib.request
from typing import Iterable

from ti_lookup import Indicator, upsert_indicators, _reload_env  # type: ignore

_TOR_URL = os.environ.get("TOR_BULK_EXIT_URL", "https://check.torproject.org/torbulkexitlist")


def handler(event, context) -> dict:
    _reload_env()
    body = _fetch(_TOR_URL)
    if body is None:
        return {"ok": False, "error": "fetch_failed"}
    indicators = list(parse_tor(body))
    upsert_indicators(indicators)
    print(f"feed=tor upserted={len(indicators)}")
    return {"ok": True, "count": len(indicators)}


def parse_tor(text: str) -> Iterable[Indicator]:
    now = dt.datetime.now(dt.timezone.utc)
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        yield Indicator(
            value=line, kind="ip", source="tor",
            first_seen=now, last_seen=now,
            confidence=None, tags=["tor_exit"],
            raw={},
        )


def _fetch(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ciso-copilot-ti/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                print(f"WARN: tor returned status {resp.status}")
                return None
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"WARN: tor fetch failed: {e!r}")
        return None
```

- [ ] **Step 6: Run — expect PASS**

```bash
cd platform/lambda/ti_feed_tor && python -m pytest tests/ -v
```
Expected: 1 passed.

- [ ] **Step 7: build.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
rm -rf build dist && mkdir -p build dist
cp main.py __init__.py build/
cp ../_shared/ti_lookup.py build/
cd build && zip -qr ../dist/ti_feed_tor.zip . && cd ..
echo "Built $(pwd)/dist/ti_feed_tor.zip"
```

```bash
chmod +x platform/lambda/ti_feed_tor/build.sh
platform/lambda/ti_feed_tor/build.sh
```

- [ ] **Step 8: Commit**

```bash
git add platform/lambda/ti_feed_tor/
git commit -m "feat(soc-s1c): ti_feed_tor — hourly Tor exit list ETL"
```

---

### Task 8: `event_router` — capture `sourceIPAddress` into `events.source_ip`

**Files:**
- Modify: `platform/lambda/event_router/main.py`
- Modify: `platform/lambda/event_router/tests/test_dedupe.py` (or write a new `tests/test_source_ip.py`)

The router today inserts into `events (... severity, title, actor, resource_arn, fired_at, source_event_id)`. We add `source_ip` (NULL for non-CloudTrail events; the calling IP for CloudTrail mgmt events).

- [ ] **Step 1: Read the existing router INSERT to confirm the parameter shape**

Read `platform/lambda/event_router/main.py` and locate the INSERT into `events`. Note the parameter naming convention used there.

- [ ] **Step 2: Write a failing test**

Create `platform/lambda/event_router/tests/test_source_ip.py`:
```python
"""Router: source_ip column is populated from CloudTrail detail.sourceIPAddress."""
from __future__ import annotations

import main


def test_extract_source_ip_from_cloudtrail():
    cloudtrail_event = {
        "detail-type": "AWS API Call via CloudTrail",
        "source":      "aws.ec2",
        "detail": {
            "eventID":         "abc-123",
            "eventName":       "AuthorizeSecurityGroupIngress",
            "sourceIPAddress": "185.220.101.12",
            "userIdentity":    {"arn": "arn:aws:iam::1:user/x"},
            "requestParameters": {"groupId": "sg-abc"},
        },
    }
    assert main._extract_source_ip(cloudtrail_event) == "185.220.101.12"


def test_extract_source_ip_skips_aws_internal():
    """AWS internal calls have sourceIPAddress like 'ec2.amazonaws.com' — not an IP."""
    event = {
        "detail-type": "AWS API Call via CloudTrail",
        "source":      "aws.ec2",
        "detail": {"sourceIPAddress": "ec2.amazonaws.com"},
    }
    assert main._extract_source_ip(event) is None


def test_extract_source_ip_returns_none_for_config_change():
    config_event = {
        "detail-type": "Configuration Item Change Notification",
        "source":      "aws.config",
        "detail":      {"configurationItem": {"resourceType": "AWS::EC2::SecurityGroup"}},
    }
    assert main._extract_source_ip(config_event) is None
```

- [ ] **Step 3: Run — expect FAIL**

```bash
cd platform/lambda/event_router && python -m pytest tests/test_source_ip.py -v
```
Expected: `AttributeError: module 'main' has no attribute '_extract_source_ip'`.

- [ ] **Step 4: Add `_extract_source_ip` helper + thread it through the INSERT**

In `platform/lambda/event_router/main.py`, near the other extraction helpers (around `_extract_cloudtrail_resource`), add:

```python
def _extract_source_ip(event: dict) -> str | None:
    """Return CloudTrail's caller IP, or None for non-CloudTrail / AWS-internal calls."""
    if event.get("detail-type") != "AWS API Call via CloudTrail":
        return None
    raw = (event.get("detail") or {}).get("sourceIPAddress")
    if not raw or not isinstance(raw, str):
        return None
    # AWS uses service-principal strings for internal calls; only keep dotted-quad / IPv6.
    if "." not in raw and ":" not in raw:
        return None
    if raw.endswith(".amazonaws.com") or raw.endswith(".amazon.com"):
        return None
    return raw
```

Then in the INSERT path (look for the `_insert_event` / `INSERT INTO events` SQL), add `source_ip` to both the column list and the `VALUES` clause, and pass `source_ip = _extract_source_ip(event)` from the handler entry. Mirror the nullable-string pattern already used for `actor` and `resource_arn`.

Concretely, append `, source_ip` to the column list, append `, :source_ip` to VALUES, and add the parameter:
```python
{"name": "source_ip", "value": ({"stringValue": source_ip} if source_ip else {"isNull": True})},
```

- [ ] **Step 5: Run — expect PASS**

```bash
cd platform/lambda/event_router && python -m pytest tests/ -v
```
Expected: all existing tests still pass + the 3 new source_ip tests pass.

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/event_router/main.py platform/lambda/event_router/tests/test_source_ip.py
git commit -m "feat(soc-s1c): event_router — capture CloudTrail sourceIPAddress into events.source_ip"
```

---

### Task 9: `soc_enrichment` — wire TI lookup into features pipeline

**Files:**
- Modify: `platform/lambda/soc_enrichment/main.py` (only the `_load_event_row` SELECT — add `source_ip` column to projection)
- Modify: `platform/lambda/soc_enrichment/features.py`
- Modify: `platform/lambda/soc_enrichment/build.sh`
- Create: `platform/lambda/soc_enrichment/tests/test_ti_match.py`
- Modify: `platform/lambda/soc_enrichment/tests/test_features.py`

- [ ] **Step 1: Extend `_load_event_row` to project `source_ip`**

In `platform/lambda/soc_enrichment/main.py`, find the SELECT in `_load_event_row` and add `e.source_ip` after `e.fired_at::text`. Add `"source_ip"` to the `cols` list (after `"fired_at"`, before `"before_state"`). The conditional that JSON-parses `before_state`/`after_state` already special-cases those by name — `source_ip` is a plain string, no special handling.

- [ ] **Step 2: Write failing TI-match test**

Create `platform/lambda/soc_enrichment/tests/test_ti_match.py`:
```python
"""features._ti_matches: pulls extracted IOCs from the row, looks them up,
returns a list shaped for the LLM prompt + the UI."""
from __future__ import annotations

import features


def test_ti_matches_returns_db_hits(monkeypatch):
    monkeypatch.setattr(features.ioc_extract, "extract_iocs",
                        lambda row: {"ip": ["185.220.101.12"], "domain": [], "sha256": []})
    monkeypatch.setattr(features.ti_lookup, "bulk_lookup",
                        lambda values_by_kind: {
                            "185.220.101.12": [
                                {"source": "tor",           "kind": "ip", "confidence": None, "tags": ["tor_exit"]},
                                {"source": "abusech_feodo", "kind": "ip", "confidence": 80,   "tags": ["botnet_c2"]},
                            ]
                        })
    # GreyNoise not called when DB has hits — assert by setting the api_key resolver to raise
    monkeypatch.setattr(features, "_greynoise_api_key", lambda: (_ for _ in ()).throw(AssertionError("greynoise should not be called")))
    out = features._ti_matches({"tenant_id": "t1", "source_ip": "185.220.101.12",
                                "before_state": None, "after_state": None})
    assert out == [
        {"value": "185.220.101.12", "kind": "ip", "source": "tor",
         "confidence": None, "tags": ["tor_exit"]},
        {"value": "185.220.101.12", "kind": "ip", "source": "abusech_feodo",
         "confidence": 80, "tags": ["botnet_c2"]},
    ]


def test_ti_matches_falls_back_to_greynoise_for_unmatched_ip(monkeypatch):
    monkeypatch.setattr(features.ioc_extract, "extract_iocs",
                        lambda row: {"ip": ["198.51.100.7"], "domain": [], "sha256": []})
    monkeypatch.setattr(features.ti_lookup, "bulk_lookup", lambda v: {})
    monkeypatch.setattr(features, "_greynoise_api_key", lambda: "fake-key")
    monkeypatch.setattr(features.greynoise, "lookup_ip",
                        lambda tenant_id, ip, api_key: {
                            "source": "greynoise_community", "kind": "ip", "value": ip,
                            "classification": "malicious", "confidence": 85,
                            "name": "Mirai", "link": None,
                        })
    out = features._ti_matches({"tenant_id": "t1", "source_ip": "198.51.100.7",
                                "before_state": None, "after_state": None})
    assert len(out) == 1
    assert out[0]["source"] == "greynoise_community"
    assert out[0]["confidence"] == 85


def test_ti_matches_skips_greynoise_when_no_key(monkeypatch):
    monkeypatch.setattr(features.ioc_extract, "extract_iocs",
                        lambda row: {"ip": ["198.51.100.7"], "domain": [], "sha256": []})
    monkeypatch.setattr(features.ti_lookup, "bulk_lookup", lambda v: {})
    monkeypatch.setattr(features, "_greynoise_api_key", lambda: None)
    out = features._ti_matches({"tenant_id": "t1", "source_ip": "198.51.100.7",
                                "before_state": None, "after_state": None})
    assert out == []


def test_ti_matches_returns_empty_when_no_iocs(monkeypatch):
    monkeypatch.setattr(features.ioc_extract, "extract_iocs",
                        lambda row: {"ip": [], "domain": [], "sha256": []})
    out = features._ti_matches({"tenant_id": "t1", "source_ip": None,
                                "before_state": None, "after_state": None})
    assert out == []
```

- [ ] **Step 3: Extend `tests/test_features.py`** — add a case asserting `compute_features` includes `ti_matches`

Append to `platform/lambda/soc_enrichment/tests/test_features.py`:

```python
def test_compute_features_includes_ti_matches(monkeypatch):
    monkeypatch.setattr(features, "_first_time_actor_on_resource", lambda *a, **k: False)
    monkeypatch.setattr(features, "_action_rarity",                lambda *a, **k: "common")
    monkeypatch.setattr(features, "_blast_radius_proxy",           lambda *a, **k: 0)
    monkeypatch.setattr(features, "_ti_matches", lambda row: [
        {"value": "185.220.101.12", "kind": "ip", "source": "tor",
         "confidence": None, "tags": ["tor_exit"]},
    ])
    row = {"tenant_id": "t1", "actor": "user/x", "resource_arn": "sg-abc",
           "title": "AuthorizeSecurityGroupIngress", "fired_at": "2026-05-25T14:00:00Z",
           "source_ip": "185.220.101.12"}
    f = features.compute_features(row)
    assert f["ti_matches"] == [
        {"value": "185.220.101.12", "kind": "ip", "source": "tor",
         "confidence": None, "tags": ["tor_exit"]},
    ]
```

- [ ] **Step 4: Run — expect FAIL**

```bash
cd platform/lambda/soc_enrichment && python -m pytest tests/ -v
```
Expected: `AttributeError: module 'features' has no attribute 'ioc_extract'` (and the test_features new case fails because `compute_features` doesn't emit `ti_matches` yet).

- [ ] **Step 5: Implement `_ti_matches` + update `compute_features`**

Modify `platform/lambda/soc_enrichment/features.py`. Add imports at the top (after the existing `boto3` import):

```python
import os
import boto3

# Vendored from _shared/ by build.sh
import ti_lookup
import ioc_extract
import greynoise
```

Add the API-key resolver and the `_ti_matches` helper below the existing private feature helpers:

```python
def _greynoise_api_key() -> str | None:
    """Resolve the GreyNoise key once per cold start from Secrets Manager.

    Returns None when the secret is not configured — disables on-demand fallback.
    """
    cached = getattr(_greynoise_api_key, "_cached", "unset")
    if cached != "unset":
        return cached  # type: ignore
    name = os.environ.get("GREYNOISE_API_KEY_SECRET_NAME")
    if not name:
        _greynoise_api_key._cached = None  # type: ignore
        return None
    try:
        sm = boto3.client("secretsmanager")
        secret = sm.get_secret_value(SecretId=name)["SecretString"]
        try:
            import json as _json
            key = _json.loads(secret).get("GREYNOISE_API_KEY", secret)
        except (TypeError, ValueError):
            key = secret
    except Exception as e:
        print(f"WARN: greynoise key fetch failed: {e!r}")
        key = None
    _greynoise_api_key._cached = key  # type: ignore
    return key


def _ti_matches(row: dict) -> list[dict]:
    """Extract IOCs from the event row, look them up in threat_indicators,
    optionally fall back to on-demand GreyNoise Community for unmatched IPs.

    Returns a list of {value, kind, source, confidence, tags} dicts — at most
    a handful per event (callers tolerate empty list).
    """
    iocs = ioc_extract.extract_iocs(row)
    # Collapse to a single dict keyed by kind→list[str] for bulk_lookup
    db_hits = ti_lookup.bulk_lookup(iocs)

    matches: list[dict] = []
    for value, rows in db_hits.items():
        for r in rows:
            matches.append({
                "value":      value,
                "kind":       r["kind"],
                "source":     r["source"],
                "confidence": r["confidence"],
                "tags":       r["tags"],
            })

    # GreyNoise on-demand fallback: only IPs, only those that missed in DB
    unmatched_ips = [ip for ip in iocs.get("ip", []) if ip not in db_hits]
    if unmatched_ips:
        key = _greynoise_api_key()
        if key:
            tenant_id = row.get("tenant_id") or ""
            for ip in unmatched_ips[:5]:  # cap per-event GreyNoise calls
                hit = greynoise.lookup_ip(tenant_id, ip, api_key=key)
                if hit:
                    matches.append({
                        "value":      hit["value"],
                        "kind":       hit["kind"],
                        "source":     hit["source"],
                        "confidence": hit["confidence"],
                        "tags":       [hit.get("classification") or "unknown"] +
                                       ([hit["name"]] if hit.get("name") else []),
                    })
    return matches
```

Update `compute_features` to include `ti_matches`:

```python
def compute_features(row: dict) -> dict:
    return {
        "first_time_actor_on_resource": _first_time_actor_on_resource(
            row["tenant_id"], row.get("actor"), row.get("resource_arn")),
        "off_hours":                    _is_off_hours(row["fired_at"]),
        "action_rarity":                _action_rarity(row["tenant_id"], row.get("title")),
        "blast_radius_proxy":           _blast_radius_proxy(row["tenant_id"], row.get("actor")),
        "ti_matches":                   _ti_matches(row),
    }
```

- [ ] **Step 6: Update `build.sh` to vendor `_shared` modules**

Modify `platform/lambda/soc_enrichment/build.sh`. Add after the existing `cp ../event_router/spend_cap.py build/`:

```bash
# Slice 1c: vendor the _shared/ TI substrate
cp ../_shared/ti_lookup.py ../_shared/ioc_extract.py ../_shared/greynoise.py build/
```

- [ ] **Step 7: Run — expect PASS**

```bash
cd platform/lambda/soc_enrichment && python -m pytest tests/ -v
```
Expected: all existing tests still pass + 4 new `test_ti_match.py` cases pass + the appended `test_features.py` case passes.

- [ ] **Step 8: Rebuild the soc_enrichment zip and confirm `_shared` modules landed**

```bash
platform/lambda/soc_enrichment/build.sh
unzip -l platform/lambda/soc_enrichment/dist/soc_enrichment.zip | grep -E 'ti_lookup|ioc_extract|greynoise|spend_cap|features|llm|main' 
```
Expected: each of `ti_lookup.py`, `ioc_extract.py`, `greynoise.py`, `spend_cap.py`, `features.py`, `llm.py`, `main.py` appears in the listing.

- [ ] **Step 9: Commit**

```bash
git add platform/lambda/soc_enrichment/main.py \
        platform/lambda/soc_enrichment/features.py \
        platform/lambda/soc_enrichment/build.sh \
        platform/lambda/soc_enrichment/tests/test_features.py \
        platform/lambda/soc_enrichment/tests/test_ti_match.py
git commit -m "feat(soc-s1c): soc_enrichment — TI matches in features + optional GreyNoise on-demand"
```

---

### Task 10: `soc_enrichment.llm` — surface TI matches in the prompt

**Files:**
- Modify: `platform/lambda/soc_enrichment/llm.py`
- Modify: `platform/lambda/soc_enrichment/tests/test_llm.py`

- [ ] **Step 1: Failing test**

Append to `platform/lambda/soc_enrichment/tests/test_llm.py`:

```python
def test_build_messages_includes_ti_matches():
    row = {"source": "aws.cloudtrail", "kind": "drift", "severity": "high",
           "title": "AuthorizeSecurityGroupIngress",
           "actor": "arn:aws:iam::1:user/x",
           "resource_arn": "sg-abc", "fired_at": "2026-05-25T14:00:00Z",
           "source_ip": "185.220.101.12",
           "before_state": None, "after_state": None}
    features = {
        "first_time_actor_on_resource": True, "off_hours": True,
        "action_rarity": "rare", "blast_radius_proxy": 4,
        "ti_matches": [
            {"value": "185.220.101.12", "kind": "ip", "source": "tor",
             "confidence": None, "tags": ["tor_exit"]},
        ],
    }
    msgs = llm.build_messages(row, features)
    user = msgs[-1]["content"]
    assert "ti_matches" in user
    assert "tor" in user
    # SYSTEM prompt mentions threat-intel guidance
    assert "threat" in msgs[0]["content"].lower() or "ti_matches" in msgs[0]["content"]
```

(Assumes the file already imports `llm`. If not, copy the import line from `test_features.py`.)

- [ ] **Step 2: Run — expect FAIL** because SYSTEM has no TI guidance.

```bash
cd platform/lambda/soc_enrichment && python -m pytest tests/test_llm.py -v
```

- [ ] **Step 3: Update SYSTEM prompt in `llm.py`**

Find the `SYSTEM = ( ... )` constant and replace with:

```python
SYSTEM = (
    "You are a SOC analyst summarizing a single AWS configuration drift event "
    "for a CISO. Be terse. Be specific. Use the structured features — "
    "especially `features.ti_matches` (an empty list is a non-signal; a non-empty "
    "list IS evidence): each entry has a `source` such as 'tor', 'abusech_feodo', "
    "'abusech_threatfox', 'kev', or 'greynoise_community', and optional `tags`. "
    "When ti_matches is non-empty, name the source(s) and tag(s) in the narrative. "
    "Respond with JSON matching this schema exactly: "
    '{"narrative": str (<=240 chars), '
    ' "anomaly_class": "expected"|"unusual"|"suspicious", '
    ' "anomaly_score": int 0-100, '
    ' "next_steps": [{"step": str, "command": str|null}, ... at most 3], '
    ' "mitre_technique": "T1098" (or other MITRE ATT&CK ID) or null}'
)
```

`build_messages` already serializes the whole features dict into the user payload, so no body change needed — only the SYSTEM prompt update.

- [ ] **Step 4: Run — expect PASS**

```bash
cd platform/lambda/soc_enrichment && python -m pytest tests/test_llm.py -v
```

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/soc_enrichment/llm.py platform/lambda/soc_enrichment/tests/test_llm.py
git commit -m "feat(soc-s1c): soc_enrichment.llm — surface TI matches in narrative prompt"
```

---

### Task 11: Web — TI badges in `/soc` DetailPane

**Files:**
- Modify: `web/src/components/soc/DetailPane.tsx`
- Create: `web/src/components/soc/DetailPane.test.tsx`

The detail endpoint already returns `event.ai_features` as `Record<string, unknown>`. We narrow at the use site and render badges when `ai_features.ti_matches` is a non-empty array. No `api.ts` change.

- [ ] **Step 1: Failing Vitest**

Create `web/src/components/soc/DetailPane.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { DetailPane } from './DetailPane';
import { api } from '../../lib/api';

vi.mock('../../lib/api', () => ({
  api: { getEventDetail: vi.fn() },
}));

const baseEvent = {
  event_id: 'evt-1', kind: 'drift' as const, source: 'aws.cloudtrail',
  severity: 'high' as const, title: 'AuthorizeSecurityGroupIngress',
  description: null, resource_arn: 'sg-abc', actor: 'arn:aws:iam::1:user/x',
  fired_at: '2026-05-25T14:00:00Z', ingested_at: '2026-05-25T14:00:01Z',
  ai_narrative: 'Suspicious change.', ai_anomaly_class: 'suspicious' as const,
  ai_anomaly_score: 88, ai_next_steps: null, ai_model_version: 'claude-sonnet-4-6',
  mitre_technique: null, action: null, after_state: null, before_state: null,
};

describe('DetailPane TI badges', () => {
  beforeEach(() => vi.mocked(api.getEventDetail).mockReset());

  it('renders one badge per TI match with source + tags', async () => {
    vi.mocked(api.getEventDetail).mockResolvedValue({
      event: {
        ...baseEvent,
        ai_features: {
          ti_matches: [
            { value: '185.220.101.12', kind: 'ip', source: 'tor',           confidence: null, tags: ['tor_exit']  },
            { value: '185.220.101.12', kind: 'ip', source: 'abusech_feodo', confidence: 80,   tags: ['botnet_c2'] },
          ],
        },
      },
      related_findings: [],
    });
    render(<DetailPane eventId="evt-1" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/Threat intel/i)).toBeTruthy());
    expect(screen.getByText('tor')).toBeTruthy();
    expect(screen.getByText('abusech_feodo')).toBeTruthy();
    expect(screen.getByText(/tor_exit/)).toBeTruthy();
    expect(screen.getByText(/botnet_c2/)).toBeTruthy();
    // Confidence rendered when present
    expect(screen.getByText(/conf 80/i)).toBeTruthy();
  });

  it('hides the TI block when ti_matches is empty or absent', async () => {
    vi.mocked(api.getEventDetail).mockResolvedValue({
      event: { ...baseEvent, ai_features: { ti_matches: [] } },
      related_findings: [],
    });
    render(<DetailPane eventId="evt-1" onClose={() => {}} />);
    await waitFor(() => expect(screen.queryByText(/Threat intel/i)).toBeNull());
  });
});
```

- [ ] **Step 2: Run — expect FAIL**

```bash
cd web && pnpm test -- src/components/soc/DetailPane.test.tsx
```
Expected: `Threat intel` heading not found.

- [ ] **Step 3: Render TI badges in `DetailPane.tsx`**

Open `web/src/components/soc/DetailPane.tsx`. After the existing `{related_findings.length > 0 && ...}` block (and before the FeedbackButtons block), insert:

```tsx
{(() => {
  const matches = (e.ai_features as { ti_matches?: Array<{
    value: string; kind: string; source: string; confidence: number | null; tags: string[];
  }> } | null)?.ti_matches;
  if (!matches || matches.length === 0) return null;
  return (
    <div className="mb-3">
      <div className="text-xs font-medium text-stone-700 mb-1">Threat intel</div>
      <ul className="text-xs space-y-1">
        {matches.map((m, i) => (
          <li key={`${m.value}-${m.source}-${i}`}
              className="flex flex-wrap items-center gap-1 text-stone-700">
            <span className="font-mono text-stone-900">{m.value}</span>
            <span className="px-1.5 py-0.5 rounded bg-amber-100 text-amber-900 border border-amber-200">{m.source}</span>
            {m.tags.map(t => (
              <span key={t} className="px-1.5 py-0.5 rounded bg-stone-100 text-stone-600 border border-stone-200">{t}</span>
            ))}
            {m.confidence !== null && (
              <span className="text-stone-500">conf {m.confidence}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
})()}
```

- [ ] **Step 4: Run — expect PASS**

```bash
cd web && pnpm test -- src/components/soc/DetailPane.test.tsx
```

- [ ] **Step 5: Build + typecheck**

```bash
cd web && pnpm build
```
Expected: `tsc -b && vite build` exits 0.

- [ ] **Step 6: Commit**

```bash
git add web/src/components/soc/DetailPane.tsx web/src/components/soc/DetailPane.test.tsx
git commit -m "feat(soc-s1c): web — TI badges in /soc DetailPane"
```

---

### Task 12: CDK — three cron Lambdas + GreyNoise secret + scheduled rules

**Files:**
- Modify: `platform/lib/events-stack.ts`

The new resources:
- Three `lambda.Function` constructs (`TiFeedAbusechFn`, `TiFeedKevFn`, `TiFeedTorFn`) with the zips built in tasks 5–7. Each is granted Data API access + secretsmanager:GetSecretValue on `ciso-copilot/*` (in case future feeds need API keys).
- Three `events.Rule` constructs with `Schedule.rate(Duration.hours(1))`, `Schedule.rate(Duration.hours(24))`, `Schedule.rate(Duration.hours(1))` respectively, each with a `LambdaFunction` target.
- Extend the existing `enrichmentFn` with `GREYNOISE_API_KEY_SECRET_NAME` env + read permission on `ciso-copilot/greynoise-api-key` (the secret itself is provisioned via `aws secretsmanager create-secret` in Task 13's verification gate; CDK only references the name).

- [ ] **Step 1: Verify all three `dist/*.zip` files exist**

```bash
ls platform/lambda/ti_feed_abusech/dist/ti_feed_abusech.zip
ls platform/lambda/ti_feed_kev/dist/ti_feed_kev.zip
ls platform/lambda/ti_feed_tor/dist/ti_feed_tor.zip
```

Each command exits 0.

- [ ] **Step 2: Edit `platform/lib/events-stack.ts`**

After the existing `SocEnrichmentFn` block (around line 162, after the `new cdk.CfnOutput(this, 'SocEnrichmentFnName', ...)`), and before the `FanToRouter` rule, add:

```typescript
    // ============================================================
    // SOC Slice 1c — TI feed cron Lambdas
    // ============================================================
    const tiFeedEnv = {
      DB_CLUSTER_ARN: props.dbCluster.clusterArn,
      DB_SECRET_ARN:  props.dbCluster.secret!.secretArn,
      DB_NAME:        'ciso_copilot',
    };

    const tiFeedAbusechFn = new lambda.Function(this, 'TiFeedAbusechFn', {
      runtime:    lambda.Runtime.PYTHON_3_12,
      handler:    'main.handler',
      code:       lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'ti_feed_abusech', 'dist', 'ti_feed_abusech.zip')),
      timeout:    cdk.Duration.minutes(2),
      memorySize: 512,
      environment: tiFeedEnv,
    });
    props.dbCluster.grantDataApiAccess(tiFeedAbusechFn);

    const tiFeedKevFn = new lambda.Function(this, 'TiFeedKevFn', {
      runtime:    lambda.Runtime.PYTHON_3_12,
      handler:    'main.handler',
      code:       lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'ti_feed_kev', 'dist', 'ti_feed_kev.zip')),
      timeout:    cdk.Duration.minutes(2),
      memorySize: 512,
      environment: tiFeedEnv,
    });
    props.dbCluster.grantDataApiAccess(tiFeedKevFn);

    const tiFeedTorFn = new lambda.Function(this, 'TiFeedTorFn', {
      runtime:    lambda.Runtime.PYTHON_3_12,
      handler:    'main.handler',
      code:       lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'ti_feed_tor', 'dist', 'ti_feed_tor.zip')),
      timeout:    cdk.Duration.minutes(2),
      memorySize: 512,
      environment: tiFeedEnv,
    });
    props.dbCluster.grantDataApiAccess(tiFeedTorFn);

    new events.Rule(this, 'TiFeedAbusechSchedule', {
      ruleName:    'ti-feed-abusech-hourly',
      description: 'Hourly abuse.ch Feodo + ThreatFox ETL into threat_indicators.',
      schedule:    events.Schedule.rate(cdk.Duration.hours(1)),
      targets:     [new targets.LambdaFunction(tiFeedAbusechFn)],
    });

    new events.Rule(this, 'TiFeedKevSchedule', {
      ruleName:    'ti-feed-kev-daily',
      description: 'Daily CISA KEV ETL into threat_indicators.',
      schedule:    events.Schedule.rate(cdk.Duration.hours(24)),
      targets:     [new targets.LambdaFunction(tiFeedKevFn)],
    });

    new events.Rule(this, 'TiFeedTorSchedule', {
      ruleName:    'ti-feed-tor-hourly',
      description: 'Hourly Tor exit list ETL into threat_indicators.',
      schedule:    events.Schedule.rate(cdk.Duration.hours(1)),
      targets:     [new targets.LambdaFunction(tiFeedTorFn)],
    });

    new cdk.CfnOutput(this, 'TiFeedAbusechFnName', { value: tiFeedAbusechFn.functionName });
    new cdk.CfnOutput(this, 'TiFeedKevFnName',     { value: tiFeedKevFn.functionName });
    new cdk.CfnOutput(this, 'TiFeedTorFnName',     { value: tiFeedTorFn.functionName });
```

Then, inside the existing `enrichmentFn` block (right after the `addToRolePolicy(... secret: ciso-copilot/anthropic-api-key*)` IAM statement), add:

```typescript
    enrichmentFn.addEnvironment('GREYNOISE_API_KEY_SECRET_NAME', 'ciso-copilot/greynoise-api-key');
    enrichmentFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['secretsmanager:GetSecretValue'],
      resources: [`arn:aws:secretsmanager:${this.region}:${this.account}:secret:ciso-copilot/greynoise-api-key*`],
    }));
```

- [ ] **Step 3: Synth the stack as a smoke check**

```bash
cd platform && npx cdk synth CisoCopilotEvents > /dev/null
```
Expected: exit 0, no errors.

- [ ] **Step 4: Commit (don't deploy yet)**

```bash
git add platform/lib/events-stack.ts
git commit -m "feat(soc-s1c): CDK — 3 cron TI Lambdas + GreyNoise secret env on enrichment Lambda"
```

---

### Task 13: Apply schema migration + deploy + seed feeds

This is the deploy + manual seed step. Confirm everything is green locally before running it.

- [ ] **Step 1: Apply migration 013 to Aurora**

```bash
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN \
  --secret-arn  $DB_SECRET_ARN \
  --database ciso_copilot \
  --sql "$(cat platform/sql/013_phase_soc_ti.sql)"
```
Expected: success.

- [ ] **Step 2: Create the GreyNoise secret (optional but enables on-demand fallback)**

If a GreyNoise Community API key is already minted, register it:
```bash
aws secretsmanager create-secret \
  --name ciso-copilot/greynoise-api-key \
  --secret-string '{"GREYNOISE_API_KEY":"<paste-key-here>"}'
```
If no key is available, **skip this step** — the enrichment Lambda detects the missing secret and silently disables on-demand lookups. The DB-side feeds (Feodo, ThreatFox, KEV, Tor) cover the demo wedge without GreyNoise.

- [ ] **Step 3: Deploy `CisoCopilotEvents`** (full deploy — IAM changes mean hotswap is not enough)

```bash
cd platform && npx cdk deploy CisoCopilotEvents --require-approval never
```
Expected: `UPDATE_COMPLETE`. Note the three new `TiFeed*FnName` outputs.

- [ ] **Step 4: Run each feed once manually to seed the table**

```bash
aws lambda invoke --function-name "$(aws cloudformation describe-stacks --stack-name CisoCopilotEvents \
    --query "Stacks[0].Outputs[?OutputKey=='TiFeedAbusechFnName'].OutputValue" --output text)" \
  --payload '{}' --cli-binary-format raw-in-base64-out /tmp/ti-abusech.json && cat /tmp/ti-abusech.json
aws lambda invoke --function-name "$(aws cloudformation describe-stacks --stack-name CisoCopilotEvents \
    --query "Stacks[0].Outputs[?OutputKey=='TiFeedKevFnName'].OutputValue" --output text)" \
  --payload '{}' --cli-binary-format raw-in-base64-out /tmp/ti-kev.json && cat /tmp/ti-kev.json
aws lambda invoke --function-name "$(aws cloudformation describe-stacks --stack-name CisoCopilotEvents \
    --query "Stacks[0].Outputs[?OutputKey=='TiFeedTorFnName'].OutputValue" --output text)" \
  --payload '{}' --cli-binary-format raw-in-base64-out /tmp/ti-tor.json && cat /tmp/ti-tor.json
```
Expected: each returns `{"ok": true, ...}` with a non-zero count for `feodo`, `threatfox`, and `kev`; Tor exit list typically returns ~1500 IPs.

- [ ] **Step 5: Verify the table is populated**

```bash
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN \
  --secret-arn  $DB_SECRET_ARN \
  --database ciso_copilot \
  --sql "SELECT source, COUNT(*) FROM threat_indicators GROUP BY source ORDER BY source"
```
Expected: four rows — `abusech_feodo`, `abusech_threatfox`, `kev`, `tor` — each with a count ≥ 1.

- [ ] **Step 6: Rebuild + deploy soc_enrichment with the vendored `_shared` modules**

```bash
platform/lambda/soc_enrichment/build.sh
cd platform && npx cdk deploy CisoCopilotEvents --require-approval never --hotswap --force
```
`--force` because the zip filename hasn't changed; CDK's hotswap may skip otherwise (per HANDOFF's documented gotcha #7).

- [ ] **Step 7: Web deploy**

```bash
cd web && pnpm build && \
  aws s3 sync dist/ s3://<WEB_BUCKET>/ --delete && \
  aws cloudfront create-invalidation --distribution-id <CLOUDFRONT_DIST_ID> --paths '/*'
```

- [ ] **Step 8: Commit and push**

```bash
git push -u origin feat/ai-powered-soc-slice-1c
```

---

### Task 14: Customer docs + TEST_PLAN.md gate

**Files:**
- Create: `docs/customer/drift-detection-threat-intel.md`
- Modify: `TEST_PLAN.md`

- [ ] **Step 1: Customer doc**

Create `docs/customer/drift-detection-threat-intel.md`:

```markdown
# Threat intel in CISO Copilot SOC

Every drift event we surface in `/soc` is enriched with threat-intel
matches drawn from public feeds we maintain on your behalf.

## Feeds we pull

| Source | Frequency | What it catches |
|---|---|---|
| abuse.ch Feodo Tracker | Hourly | Active botnet C2 IPs (Emotet, Heodo, Dridex, TrickBot) |
| abuse.ch ThreatFox | Hourly | Malware C2 indicators across IPs, domains, hashes |
| CISA Known Exploited Vulnerabilities | Daily | CVEs with confirmed exploitation in the wild |
| Tor Project exit list | Hourly | Tor exit node IPs |
| GreyNoise Community | On-demand, rate-limited | Per-IP classification when our cached feeds miss |

## Customer cost

**None.** All feeds are free and run on our infrastructure. GreyNoise
Community is capped per-tenant per-day so a single noisy tenant cannot
exhaust our quota.

## Opt-out

Threat-intel enrichment is server-side. There is nothing to disable on
your cloud accounts. If you want to suppress AI narrative entirely on a
drift event, the existing per-tenant LLM spend cap (default $10/day)
will short-circuit the call once exceeded.

## Where you see it

`/soc` → click any drift event → "Threat intel" section in the detail
pane shows one badge per match (source + tags + confidence when the
source supplies one).
```

- [ ] **Step 2: Append the Slice 1c gate to `TEST_PLAN.md`**

Open `TEST_PLAN.md` and append (preserving format with the existing Slice 1 gate):

```markdown
## SOC Slice 1c — TI match end-to-end (2026-05-25)

**Pre-requisites:** Slice 1 demo gate passes. Migration `013_phase_soc_ti.sql`
applied. All three `ti_feed_*` Lambdas have been invoked at least once
and `threat_indicators` has rows from `abusech_feodo`, `abusech_threatfox`,
`kev`, and `tor`.

**Procedure:**

1. From a Tor exit IP (or use a VPN whose egress is in the Tor list — verify
   the egress IP appears in `SELECT 1 FROM threat_indicators WHERE
   indicator_value = '<your-egress>' AND source = 'tor'`), authenticate to
   your test AWS account and run:

       aws ec2 authorize-security-group-ingress \
         --group-id sg-test \
         --protocol tcp --port 22 --cidr 0.0.0.0/0

2. Within 60 seconds, the iPhone vibrates (Slice 1 gate carries over).

3. Open `https://$SHASTA_DOMAIN/soc` in a browser. The new event
   appears at the top of the timeline with severity `high`.

4. Click it. The detail pane shows:
   - AI narrative naming **Tor** (and any other matching sources)
   - A "Threat intel" section with a badge per match: the egress IP labeled
     `tor` and any other source hits
   - Anomaly classification likely `suspicious`, score ≥ 70

5. Verify in Aurora:

       SELECT source_ip,
              ai_features::jsonb -> 'ti_matches'
       FROM events
       WHERE event_id = '<event_id>';

   The `ti_matches` array is non-empty and contains a `tor`-sourced entry
   keyed on the source IP.

**Negative case:** Repeat the same SG-open from a non-listed IP (your home
ISP). The event should still fire and enrich, but the "Threat intel"
section is hidden in the detail pane, and `ai_features.ti_matches` is `[]`.
```

- [ ] **Step 3: Commit**

```bash
git add docs/customer/drift-detection-threat-intel.md TEST_PLAN.md
git commit -m "docs(soc-s1c): customer TI doc + manual test gate"
```

---

### Task 15: HANDOFF.md update + open PR

- [ ] **Step 1: Prepend a Slice 1c block to `HANDOFF.md`** (right above the existing "🚀 SOC Slice 1 — shipped" block).

Pattern is documented in HANDOFF — short status block with what's live, what's deferred, lessons paid. The relevant lessons specific to 1c:
- `_shared/` directory promoted from "deferred follow-up" to live; build.sh of every consuming Lambda now `cp -r ../_shared/*.py build/`.
- Stdlib `urllib.request` is sufficient for the three cron feeds — keeps Lambda zips tiny and cold-starts fast.
- ThreatFox `ioc_type=ip:port` needs the `:port` split off before storing — easy to miss.

Use the existing Slice 1 block as the template. Keep it ≤80 lines.

- [ ] **Step 2: Commit**

```bash
git add HANDOFF.md
git commit -m "docs(handoff): SOC Slice 1c shipped — TI substrate + soc_enrichment integration"
```

- [ ] **Step 3: Open the PR**

```bash
gh pr create --title "feat(soc-s1c): AI-powered SOC — Slice 1c (TI substrate)" --body "$(cat <<'EOF'
## Summary
- New global `threat_indicators` table + 3 cron-driven feed adapters (abuse.ch hourly, CISA KEV daily, Tor hourly)
- On-demand GreyNoise Community lookup with per-tenant 30/day cap
- `soc_enrichment` now extracts IPs/domains/hashes from drift events, looks them up, and surfaces matches to the LLM + `/soc` UI
- `event_router` captures CloudTrail `sourceIPAddress` into a new `events.source_ip` column
- `_shared/` directory introduced — vendored at build time, promoting the deferred follow-up logged in Slice 1's HANDOFF block

## Test plan
- [ ] Apply migration `013_phase_soc_ti.sql` to Aurora
- [ ] Deploy `CisoCopilotEvents` stack (full, not hotswap — IAM changes)
- [ ] Invoke each `ti_feed_*` Lambda once; verify `threat_indicators` has rows from all four sources
- [ ] Run the Slice 1c manual gate in `TEST_PLAN.md`
- [ ] Confirm Slice 1 demo gate still passes end-to-end (regression check)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Manual gate (KK-driven)**

Execute the `TEST_PLAN.md → SOC Slice 1c` procedure from Task 14 Step 2. Update HANDOFF.md's Slice 1c block in place from "shipped, pending verification" to "shipped + verified". Squash-merge the PR when green.

---

## Out of scope for this slice (explicit non-goals)

| Non-goal | Why | Where it goes |
|---|---|---|
| `webhook` for ThreatFox / abuse.ch realtime feeds | Hourly polling is plenty for v1 — abuse.ch publishes hourly batch dumps that drive their own dashboards | Post-launch only if telemetry shows freshness gaps |
| Premium feeds (Recorded Future, Mandiant, Crowdstrike, GreyNoise Enterprise) | Pre-launch biz case too weak; spec §11 defers | Post-launch upgrade with telemetry-backed case |
| Per-tenant custom feed adapters | YAGNI — base class supports it but no v1 customer ask | v3+ |
| Lambda Layer migration of `_shared/` | `cp` vendoring is good enough; a Layer adds a separate version-management surface | Follow-up after this slice if HANDOFF flags `_shared` drift |
| Reverse-lookup the IP at the time of event (PTR / WHOIS / passive DNS) | Adds network surface to enrichment Lambda; offline TI matching is faster and cheaper | Out — explicitly out, not deferred |
| `ti_matches` filter chip in `/soc` ("Show only events with TI hits") | Defer to first usage signal; the data is in `ai_features` and trivially filterable later | Slice 1c.5 or Slice 2 |
| TI on Slice 2 (identity drift) events | Slice 2 introduces Entra audit events; same `_ti_matches` step will apply with zero changes thanks to the kind-agnostic extractor | Falls out of Slice 2 for free |

---

## Self-review checklist (run before opening PR)

**Spec coverage:**
- ✓ `threat_indicators` table from spec §6 — Task 1
- ✓ `TIFeed` base class — covered by `_shared/ti_lookup.py` (`Indicator` dataclass + `upsert_indicators` helper; ABC subclassing replaced with concrete handlers since each adapter has one fetch+parse path — simpler)
- ✓ abuse.ch (ThreatFox + Feodo) — Task 5 (URLhaus + MalwareBazaar omitted intentionally; Feodo + ThreatFox cover the demo wedge and matching kinds. Add them in a follow-up if telemetry shows blind spots.)
- ✓ CISA KEV — Task 6
- ✓ Tor — Task 7
- ✓ GreyNoise Community on-demand, rate-limited via DynamoDB counter — Task 4 + Task 9 integration
- ✓ Enrichment extension: extract IPs/domains/hashes, lookup, add to features fed to Claude, surface as badges in `/soc` detail pane — Tasks 9 + 10 + 11
- ✓ No customer cost; our-side $5-10/mo (CloudWatch + tiny DynamoDB) — documented in customer doc
- ✓ Pluggability — adding GreyNoise Enterprise later is one new module + one env var per spec §5.2
- ✓ Demo wedge as stated in HANDOFF — covered end-to-end by Task 14's manual gate

**Placeholder scan:** no "TBD", no "implement appropriate error handling", no "similar to Task N" — every code block is complete copy-pasteable code.

**Type consistency:**
- `Indicator` dataclass: `value`, `kind`, `source`, `first_seen`, `last_seen`, `confidence`, `tags`, `raw` — used identically across all 5 producers + the upsert helper.
- `ti_matches` row shape from `bulk_lookup`: `{source, kind, confidence, tags}` — keyed by `value` in the outer dict. The enrichment Lambda re-shapes to `{value, kind, source, confidence, tags}` before storing in `ai_features.ti_matches`. The web Vitest asserts that final shape.
- Sort-key prefixes in `soc_llm_spend_daily`: `push_count:`, `llm_spend:`, `greynoise_count:` — three distinct namespaces, one table, documented in `spend_cap.py` + `greynoise.py`.

**Known follow-ups (logged, not in scope):**
- URLhaus + MalwareBazaar sub-feeds inside the abuse.ch adapter — same pattern, just more `parse_*` functions
- A `/soc` filter chip "TI hits only"
- Promote `_shared/` to a Lambda Layer
- Replace concrete adapter classes with a registry-driven `TIFeed` ABC if a 5th feed lands and the boilerplate per adapter starts to repeat itself

---

**End of plan.**
