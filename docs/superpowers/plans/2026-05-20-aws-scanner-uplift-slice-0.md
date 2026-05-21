# AWS Scanner Uplift — Slice 0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lay the foundation for the AWS scanner uplift — a coverage scorecard anchored to public benchmarks, a `scans.tier` column, and the scanner running as an ECS Fargate task instead of a Lambda.

**Architecture:** Two independent task groups. **Group A (measurement)** vendors benchmark control catalogs, builds a Shasta coverage manifest, and a scorecard generator that reports coverage % per benchmark. **Group B (compute)** migrates the existing `shasta_runner` container image from a Lambda to a Fargate task, threads a `scan_tier` parameter through, and changes the onboarding trigger from `lambda:Invoke` to `ecs:RunTask`. Group A and Group B share no code and can be executed/reviewed in either order.

**Tech Stack:** Python 3.12, pytest, AWS CDK (TypeScript), ECS Fargate, Aurora PostgreSQL (Data API), Docker.

**Spec:** `docs/superpowers/specs/2026-05-20-aws-scanner-uplift-design.md` (§3, §4, §6 gap analysis, §8, §9, §13).

---

## Conventions

- All Python paths are under `platform/lambda/shasta_runner/app/`.
- Run Python tests from `platform/lambda/shasta_runner/` with `python -m pytest`.
- The scanner test harness adds `app/` and the sibling `ai_scanner/` to `sys.path` (see `app/tests/conftest.py`) — import new modules by bare name (`from coverage.registry import ...`).
- SQL migrations live in `platform/sql/`, numbered sequentially. The next free number is `009`.
- CDK commands run from `platform/`.
- Commit after every task with a Conventional Commit message.

---

# Group A — Measurement track

## Task A1: `scans.tier` migration

**Files:**
- Create: `platform/sql/009_scan_tier.sql`

- [ ] **Step 1: Write the migration**

```sql
-- platform/sql/009_scan_tier.sql
-- AWS scanner uplift, Slice 0: record which depth tier a scan ran at.
--
-- The uplifted scanner runs at one of three tiers (quick | medium | deep).
-- The app shows the tier in scan history; the scanner reads it to filter
-- the check registry. Existing rows predate tiers — backfill to 'quick'
-- (the legacy scan was a shallow single-region pass, closest to quick).
--
-- See: docs/superpowers/specs/2026-05-20-aws-scanner-uplift-design.md §9

BEGIN;

ALTER TABLE scans
  ADD COLUMN tier TEXT NOT NULL DEFAULT 'quick'
  CHECK (tier IN ('quick', 'medium', 'deep'));

COMMIT;
```

- [ ] **Step 2: Apply the migration to the dev Aurora cluster**

Run (cluster + secret ARNs are in `HANDOFF.md`):

```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot \
  --sql "$(cat platform/sql/009_scan_tier.sql)"
```

Expected: `{"numberOfRecordsUpdated": 0}` (DDL returns no row count; no error = success).

- [ ] **Step 3: Verify the column exists**

Run:

```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot \
  --sql "SELECT column_name FROM information_schema.columns WHERE table_name='scans' AND column_name='tier'"
```

Expected: one record, `tier`.

- [ ] **Step 4: Commit**

```bash
git add platform/sql/009_scan_tier.sql
git commit -m "feat: add tier column to scans table"
```

---

## Task A2: Benchmark control catalogs

The scorecard is anchored to four public benchmarks. Each is vendored as a normalized JSON file: a list of control objects `{"id": "...", "title": "..."}`. The catalogs are sourced once from official publications and committed; a `README.md` records provenance so a future refresh is reproducible.

**Files:**
- Create: `platform/lambda/shasta_runner/app/coverage/__init__.py`
- Create: `platform/lambda/shasta_runner/app/coverage/benchmarks/README.md`
- Create: `platform/lambda/shasta_runner/app/coverage/benchmarks/cis_aws.json`
- Create: `platform/lambda/shasta_runner/app/coverage/benchmarks/fsbp.json`
- Create: `platform/lambda/shasta_runner/app/coverage/benchmarks/pci_dss.json`
- Create: `platform/lambda/shasta_runner/app/coverage/benchmarks/nist_800_53.json`
- Test: `platform/lambda/shasta_runner/app/tests/test_benchmarks.py`

- [ ] **Step 1: Create the package marker**

Create `coverage/__init__.py` as an empty file (one line):

```python
"""AWS posture coverage engine — see spec §5, §6."""
```

- [ ] **Step 2: Write the failing catalog-shape test**

```python
# app/tests/test_benchmarks.py
"""The four benchmark catalogs must exist and be shaped uniformly:
a JSON list of {"id": str, "title": str} objects with unique, non-empty ids."""
import json
from pathlib import Path

import pytest

_BENCH_DIR = Path(__file__).resolve().parent.parent / "coverage" / "benchmarks"
_CATALOGS = ["cis_aws", "fsbp", "pci_dss", "nist_800_53"]


@pytest.mark.parametrize("name", _CATALOGS)
def test_catalog_is_well_formed(name):
    path = _BENCH_DIR / f"{name}.json"
    assert path.exists(), f"missing catalog {path}"
    controls = json.loads(path.read_text())
    assert isinstance(controls, list) and controls, f"{name}: not a non-empty list"
    ids = [c["id"] for c in controls]
    assert all(isinstance(c["id"], str) and c["id"] for c in controls), f"{name}: bad id"
    assert all(isinstance(c["title"], str) and c["title"] for c in controls), f"{name}: bad title"
    assert len(ids) == len(set(ids)), f"{name}: duplicate ids"
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd platform/lambda/shasta_runner && python -m pytest app/tests/test_benchmarks.py -v`
Expected: FAIL — `missing catalog .../cis_aws.json`.

- [ ] **Step 4: Vendor the catalogs**

Create the four JSON files. Each is a JSON list of `{"id", "title"}`. Source each from the official publication and transform to this shape:

- `cis_aws.json` — CIS Amazon Web Services Foundations Benchmark, latest version. Control `id` = the recommendation number (e.g. `"1.4"`); `title` = the recommendation title. ~60 controls.
- `fsbp.json` — AWS Foundational Security Best Practices standard. Control `id` = the FSBP control ID (e.g. `"S3.8"`, `"EC2.2"`); `title` = the control title. ~340 controls. Source: the AWS Security Hub controls reference.
- `pci_dss.json` — PCI DSS v4.0 requirements. Control `id` = the requirement number (e.g. `"1.2.1"`); `title` = the requirement summary. ~250 requirements.
- `nist_800_53.json` — NIST SP 800-53 Rev 5 controls. Control `id` = the control identifier (e.g. `"AC-2"`, `"SC-7"`); `title` = the control name. ~1000 controls (base controls; control enhancements like `AC-2(1)` may be included or omitted — be consistent and record the choice in the README).

Example of the required shape (`cis_aws.json`, abbreviated — the real file has all controls):

```json
[
  {"id": "1.1", "title": "Maintain current contact details"},
  {"id": "1.4", "title": "Ensure no 'root' user account access key exists"},
  {"id": "2.1.1", "title": "Ensure S3 Bucket Policy is set to deny HTTP requests"}
]
```

If a control list is large, write a throwaway transform script, run it, then delete it — only the JSON output is committed. Do not commit a partial catalog: the test in Step 2 only checks shape, but a partial catalog silently understates the denominator in the scorecard.

- [ ] **Step 5: Write the provenance README**

```markdown
# Benchmark catalogs

Each `*.json` file is a normalized control catalog: a JSON list of
`{"id": str, "title": str}`. Consumed by `coverage/scorecard.py`.

| File | Benchmark | Version | Source |
|------|-----------|---------|--------|
| `cis_aws.json` | CIS AWS Foundations Benchmark | <version> | <official URL / publication> |
| `fsbp.json` | AWS Foundational Security Best Practices | <retrieval date> | AWS Security Hub controls reference |
| `pci_dss.json` | PCI DSS | v4.0 | PCI SSC publication |
| `nist_800_53.json` | NIST SP 800-53 | Rev 5 | NIST publication |

NIST catalog scope: <base controls only | base + enhancements> — chosen
<date>. Keep the choice consistent across refreshes.

To refresh: re-source the publication, re-run the transform to the shape
above, replace the JSON, update the version/date in this table.
```

Fill the `<...>` placeholders with the real version/date/URL used.

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd platform/lambda/shasta_runner && python -m pytest app/tests/test_benchmarks.py -v`
Expected: PASS — 4 parametrized cases.

- [ ] **Step 7: Commit**

```bash
git add platform/lambda/shasta_runner/app/coverage/__init__.py \
        platform/lambda/shasta_runner/app/coverage/benchmarks/ \
        platform/lambda/shasta_runner/app/tests/test_benchmarks.py
git commit -m "feat: vendor CIS/FSBP/PCI/NIST benchmark control catalogs"
```

---

## Task A3: Shasta coverage manifest

The scorecard needs to know which benchmark controls Shasta's *existing* checks already satisfy, so the future coverage engine doesn't duplicate them and the scorecard reports today's baseline. This is the gap-analysis input: a static manifest mapping each Shasta check to the benchmark controls it covers.

**Files:**
- Create: `platform/lambda/shasta_runner/app/coverage/shasta_manifest.py`
- Test: `platform/lambda/shasta_runner/app/tests/test_shasta_manifest.py`

- [ ] **Step 1: Write the failing test**

```python
# app/tests/test_shasta_manifest.py
"""The Shasta manifest enumerates every Shasta cloud check and the
benchmark controls it covers. Each entry's framework keys must be a
subset of the known benchmark names; control ids are plain strings."""
from coverage.shasta_manifest import SHASTA_CHECKS

_BENCHMARKS = {"cis_aws", "fsbp", "pci_dss", "nist_800_53"}


def test_manifest_is_non_empty():
    assert len(SHASTA_CHECKS) > 0


def test_manifest_entries_are_well_formed():
    for check_id, entry in SHASTA_CHECKS.items():
        assert isinstance(check_id, str) and check_id
        assert isinstance(entry, dict)
        assert set(entry).issubset(_BENCHMARKS), f"{check_id}: unknown benchmark key"
        for controls in entry.values():
            assert isinstance(controls, list)
            assert all(isinstance(c, str) and c for c in controls)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd platform/lambda/shasta_runner && python -m pytest app/tests/test_shasta_manifest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'coverage.shasta_manifest'`.

- [ ] **Step 3: Build the manifest**

Read every Shasta AWS module source at `~/Projects/Shasta/src/shasta/aws/*.py` (do NOT edit Shasta — read only). For each `check_id` a module emits, record the benchmark controls it covers. Cross-reference each check's existing `cis_aws_controls` attribute and the existing `framework_map.py` (`FRAMEWORK_MAP`, keyed by `check_id`, with `fedramp` → NIST and `pci_dss` keys) to seed the mapping; map `fedramp` entries under the `nist_800_53` key. For FSBP, map by judgement against the FSBP control whose intent matches the check.

Write the result as a literal dict. Structure:

```python
# app/coverage/shasta_manifest.py
"""Static manifest: which benchmark controls Shasta's existing AWS checks
already cover. The scorecard's baseline; the coverage engine's
deconfliction reference (do not re-implement a check listed here).

Built by reading ~/Projects/Shasta/src/shasta/aws/*.py — see spec §6
"Gap analysis". Refresh when the bundled Shasta version changes.

Keys are Shasta check_ids. Values map a benchmark name
(cis_aws | fsbp | pci_dss | nist_800_53) to the control ids covered.
A check with no benchmark mapping still appears, with an empty dict,
so the manifest is a complete inventory of Shasta's checks.
"""
from __future__ import annotations

SHASTA_CHECKS: dict[str, dict[str, list[str]]] = {
    "cloudtrail-enabled": {
        "cis_aws":     ["3.1"],
        "fsbp":        ["CloudTrail.1"],
        "pci_dss":     ["10.2.1"],
        "nist_800_53": ["AU-2", "AU-12"],
    },
    "config-enabled": {
        "cis_aws":     ["3.3"],
        "fsbp":        ["Config.1"],
        "nist_800_53": ["CM-2", "CM-3", "CA-7"],
    },
    # ... one entry per Shasta AWS check_id ...
}
```

Completeness bar: every `check_id` that appears in `~/Projects/Shasta/src/shasta/aws/*.py` (excluding `ai_checks.py`, which `ai_pass` owns) has exactly one entry. An entry with no confident benchmark mapping is `{}` — present but empty.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd platform/lambda/shasta_runner && python -m pytest app/tests/test_shasta_manifest.py -v`
Expected: PASS — 2 tests.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/shasta_runner/app/coverage/shasta_manifest.py \
        platform/lambda/shasta_runner/app/tests/test_shasta_manifest.py
git commit -m "feat: add Shasta AWS coverage manifest for scorecard baseline"
```

---

## Task A4: Scorecard generator

**Files:**
- Create: `platform/lambda/shasta_runner/app/coverage/scorecard.py`
- Test: `platform/lambda/shasta_runner/app/tests/test_scorecard.py`

- [ ] **Step 1: Write the failing test**

```python
# app/tests/test_scorecard.py
"""compute_scorecard maps covered control ids against each benchmark
catalog and reports coverage. It must count only ids that exist in the
catalog, dedupe across checks, and never exceed 100%."""
from coverage.scorecard import compute_scorecard


def test_counts_covered_controls_against_catalog():
    catalogs = {"cis_aws": [{"id": "1.1", "title": "a"}, {"id": "1.2", "title": "b"}]}
    coverage_map = {"check-x": {"cis_aws": ["1.1"]}}
    result = compute_scorecard(catalogs, coverage_map)
    cis = result["benchmarks"]["cis_aws"]
    assert cis["total"] == 2
    assert cis["covered"] == 1
    assert cis["coverage_pct"] == 50.0
    assert cis["uncovered"] == ["1.2"]


def test_dedupes_controls_covered_by_multiple_checks():
    catalogs = {"cis_aws": [{"id": "1.1", "title": "a"}]}
    coverage_map = {"check-x": {"cis_aws": ["1.1"]}, "check-y": {"cis_aws": ["1.1"]}}
    result = compute_scorecard(catalogs, coverage_map)
    assert result["benchmarks"]["cis_aws"]["covered"] == 1


def test_ignores_covered_ids_not_in_catalog():
    catalogs = {"cis_aws": [{"id": "1.1", "title": "a"}]}
    coverage_map = {"check-x": {"cis_aws": ["1.1", "9.9"]}}
    cis = compute_scorecard(catalogs, coverage_map)["benchmarks"]["cis_aws"]
    assert cis["covered"] == 1
    assert cis["coverage_pct"] == 100.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd platform/lambda/shasta_runner && python -m pytest app/tests/test_scorecard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'coverage.scorecard'`.

- [ ] **Step 3: Implement the scorecard generator**

```python
# app/coverage/scorecard.py
"""Coverage scorecard — maps the controls our checks cover against the
vendored benchmark catalogs and reports coverage per benchmark.

compute_scorecard is pure (testable). load_catalogs / render_markdown /
the __main__ block read and write files; scripts/gen_scorecard.py is the
CLI entry point. See spec §8.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_BENCH_DIR = Path(__file__).resolve().parent / "benchmarks"
_BENCHMARK_NAMES = ["cis_aws", "fsbp", "pci_dss", "nist_800_53"]
_BENCHMARK_LABELS = {
    "cis_aws":     "CIS AWS Foundations Benchmark",
    "fsbp":        "AWS Foundational Security Best Practices",
    "pci_dss":     "PCI DSS v4.0",
    "nist_800_53": "NIST SP 800-53 Rev 5",
}


def load_catalogs() -> dict[str, list[dict[str, str]]]:
    """Load every vendored benchmark catalog keyed by benchmark name."""
    return {
        name: json.loads((_BENCH_DIR / f"{name}.json").read_text())
        for name in _BENCHMARK_NAMES
    }


def compute_scorecard(
    catalogs: dict[str, list[dict[str, str]]],
    coverage_map: dict[str, dict[str, list[str]]],
) -> dict[str, Any]:
    """Compute coverage of each benchmark.

    catalogs: benchmark name -> list of {"id", "title"}.
    coverage_map: check_id -> benchmark name -> list of covered control ids.
    Returns {"benchmarks": {name: {total, covered, coverage_pct, uncovered}}}.
    Covered ids absent from the catalog are ignored; coverage never exceeds 100%.
    """
    covered_by_benchmark: dict[str, set[str]] = {n: set() for n in catalogs}
    for entry in coverage_map.values():
        for benchmark, control_ids in entry.items():
            if benchmark in covered_by_benchmark:
                covered_by_benchmark[benchmark].update(control_ids)

    benchmarks: dict[str, Any] = {}
    for name, controls in catalogs.items():
        catalog_ids = [c["id"] for c in controls]
        catalog_id_set = set(catalog_ids)
        covered = sorted(covered_by_benchmark[name] & catalog_id_set)
        uncovered = [cid for cid in catalog_ids if cid not in covered_by_benchmark[name]]
        total = len(catalog_ids)
        pct = round(100.0 * len(covered) / total, 1) if total else 0.0
        benchmarks[name] = {
            "total":        total,
            "covered":      len(covered),
            "coverage_pct": pct,
            "uncovered":    uncovered,
        }
    return {"benchmarks": benchmarks}


def render_markdown(scorecard: dict[str, Any]) -> str:
    """Render the scorecard as the committed Markdown report."""
    lines = [
        "# AWS Coverage Scorecard",
        "",
        "> Generated by `scripts/gen_scorecard.py` — do not edit by hand.",
        "",
        "| Benchmark | Covered | Total | Coverage |",
        "|-----------|---------|-------|----------|",
    ]
    for name in _BENCHMARK_NAMES:
        b = scorecard["benchmarks"][name]
        lines.append(
            f"| {_BENCHMARK_LABELS[name]} | {b['covered']} | {b['total']} "
            f"| {b['coverage_pct']}% |"
        )
    lines.append("")
    for name in _BENCHMARK_NAMES:
        b = scorecard["benchmarks"][name]
        lines.append(f"## {_BENCHMARK_LABELS[name]} — uncovered ({len(b['uncovered'])})")
        lines.append("")
        lines.append(", ".join(b["uncovered"]) if b["uncovered"] else "_full coverage_")
        lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd platform/lambda/shasta_runner && python -m pytest app/tests/test_scorecard.py -v`
Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/shasta_runner/app/coverage/scorecard.py \
        platform/lambda/shasta_runner/app/tests/test_scorecard.py
git commit -m "feat: add coverage scorecard generator"
```

---

## Task A5: Scorecard CLI + committed report + freshness test

**Files:**
- Create: `platform/lambda/shasta_runner/scripts/gen_scorecard.py`
- Create: `docs/coverage/aws-scorecard.md` (generated)
- Create: `docs/coverage/aws-scorecard.json` (generated)
- Test: `platform/lambda/shasta_runner/app/tests/test_scorecard_fresh.py`

- [ ] **Step 1: Write the CLI script**

```python
# platform/lambda/shasta_runner/scripts/gen_scorecard.py
"""Regenerate the committed AWS coverage scorecard.

Coverage today = the Shasta manifest only (the coverage engine has no
checks yet — Slice 1+). As the engine's registry fills, add its checks
to the coverage map here.

Usage: python scripts/gen_scorecard.py
Writes docs/coverage/aws-scorecard.{md,json} at the repo root.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_APP = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(_APP))

from coverage.scorecard import compute_scorecard, load_catalogs, render_markdown
from coverage.shasta_manifest import SHASTA_CHECKS

_REPO_ROOT = Path(__file__).resolve().parents[4]
_OUT_DIR = _REPO_ROOT / "docs" / "coverage"


def build_coverage_map() -> dict[str, dict[str, list[str]]]:
    """The coverage map the scorecard scores. Today: Shasta only."""
    return dict(SHASTA_CHECKS)


def main() -> None:
    scorecard = compute_scorecard(load_catalogs(), build_coverage_map())
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    (_OUT_DIR / "aws-scorecard.json").write_text(
        json.dumps(scorecard, indent=2, sort_keys=True) + "\n"
    )
    (_OUT_DIR / "aws-scorecard.md").write_text(render_markdown(scorecard))
    for name, b in scorecard["benchmarks"].items():
        print(f"{name}: {b['covered']}/{b['total']} ({b['coverage_pct']}%)")


if __name__ == "__main__":
    main()
```

Note on `_REPO_ROOT`: `parents[4]` of `platform/lambda/shasta_runner/scripts/gen_scorecard.py` is the repo root (`scripts` → `shasta_runner` → `lambda` → `platform` → repo). Verify when running Step 2.

- [ ] **Step 2: Run the script to generate the scorecard**

Run: `cd platform/lambda/shasta_runner && python scripts/gen_scorecard.py`
Expected: prints four `name: covered/total (pct%)` lines; creates `docs/coverage/aws-scorecard.md` and `.json` at the repo root. If the files land in the wrong directory, fix the `parents[N]` index and re-run.

- [ ] **Step 3: Write the freshness test**

```python
# app/tests/test_scorecard_fresh.py
"""The committed scorecard must match a fresh regeneration — so coverage
% cannot silently rot. If this fails, run: python scripts/gen_scorecard.py"""
import json
import sys
from pathlib import Path

from coverage.scorecard import compute_scorecard, load_catalogs
from coverage.shasta_manifest import SHASTA_CHECKS

_REPO_ROOT = Path(__file__).resolve().parents[5]
_SCORECARD_JSON = _REPO_ROOT / "docs" / "coverage" / "aws-scorecard.json"


def test_committed_scorecard_is_current():
    fresh = compute_scorecard(load_catalogs(), dict(SHASTA_CHECKS))
    committed = json.loads(_SCORECARD_JSON.read_text())
    assert committed == fresh, "stale scorecard — run scripts/gen_scorecard.py"
```

Note on `_REPO_ROOT`: from `app/tests/test_scorecard_fresh.py`, `parents[5]` is the repo root (`tests` → `app` → `shasta_runner` → `lambda` → `platform` → repo). Verify in Step 4.

- [ ] **Step 4: Run the freshness test to verify it passes**

Run: `cd platform/lambda/shasta_runner && python -m pytest app/tests/test_scorecard_fresh.py -v`
Expected: PASS. If it fails with a path error, fix the `parents[N]` index.

- [ ] **Step 5: Run the full scanner test suite**

Run: `cd platform/lambda/shasta_runner && python -m pytest app/tests/ -v`
Expected: all tests PASS (the new benchmark/manifest/scorecard tests plus the pre-existing detector/enumerate tests).

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/shasta_runner/scripts/gen_scorecard.py \
        platform/lambda/shasta_runner/app/tests/test_scorecard_fresh.py \
        docs/coverage/aws-scorecard.md docs/coverage/aws-scorecard.json
git commit -m "feat: generate committed AWS coverage scorecard with freshness test"
```

---

# Group B — Compute migration (Lambda → Fargate)

## Task B1: Scanner accepts a Fargate entrypoint + `scan_tier`

The scanner runs today as a Lambda (`main.handler`, params from the event dict). As a Fargate task it has no event — params arrive as environment variables. Add a `run.py` entrypoint that reads params from the environment and calls `handler`, and thread `scan_tier` through.

**Files:**
- Create: `platform/lambda/shasta_runner/app/run.py`
- Modify: `platform/lambda/shasta_runner/app/main.py:150-161` (handler signature — read `scan_tier`)
- Test: `platform/lambda/shasta_runner/app/tests/test_run_entrypoint.py`

- [ ] **Step 1: Write the failing test**

```python
# app/tests/test_run_entrypoint.py
"""run.build_event reads scan parameters from a dict of env vars into the
event shape main.handler expects. regions is comma-split; scan_tier
defaults to 'quick'; a missing required var raises KeyError."""
import pytest

from run import build_event


def test_build_event_maps_env_to_event():
    env = {
        "SCAN_ID": "s1", "TENANT_ID": "t1", "CONN_ID": "c1",
        "ROLE_ARN": "arn:aws:iam::111:role/CISOCopilotReader",
        "EXTERNAL_ID": "x1", "ACCOUNT_ID": "111111111111",
        "REGIONS": "us-east-1,us-west-2", "SCAN_TIER": "medium",
    }
    event = build_event(env)
    assert event["scan_id"] == "s1"
    assert event["regions"] == ["us-east-1", "us-west-2"]
    assert event["scan_tier"] == "medium"


def test_scan_tier_defaults_to_quick():
    env = {
        "SCAN_ID": "s1", "TENANT_ID": "t1", "CONN_ID": "c1",
        "ROLE_ARN": "r", "EXTERNAL_ID": "x", "ACCOUNT_ID": "111111111111",
    }
    event = build_event(env)
    assert event["scan_tier"] == "quick"
    assert event["regions"] == ["us-east-1"]


def test_missing_required_var_raises():
    with pytest.raises(KeyError):
        build_event({"SCAN_ID": "s1"})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd platform/lambda/shasta_runner && python -m pytest app/tests/test_run_entrypoint.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'run'`.

- [ ] **Step 3: Implement `run.py`**

```python
# app/run.py
"""Fargate entrypoint for the AWS scanner.

As a Lambda the scanner is invoked as main.handler(event, context).
As a Fargate task there is no event — scan parameters arrive as
environment variables (set via ecs:RunTask container overrides). This
script reads them into the event shape and calls the handler.

Usage (the container CMD for Fargate): python run.py
"""
from __future__ import annotations

import os
import sys

from main import handler

_REQUIRED = ("SCAN_ID", "TENANT_ID", "CONN_ID", "ROLE_ARN", "EXTERNAL_ID", "ACCOUNT_ID")


def build_event(env: dict[str, str]) -> dict:
    """Map scanner env vars to the event dict main.handler expects.
    Raises KeyError if a required var is missing."""
    event = {
        "scan_id":     env["SCAN_ID"],
        "tenant_id":   env["TENANT_ID"],
        "conn_id":     env["CONN_ID"],
        "role_arn":    env["ROLE_ARN"],
        "external_id": env["EXTERNAL_ID"],
        "account_id":  env["ACCOUNT_ID"],
        "scan_tier":   env.get("SCAN_TIER", "quick"),
    }
    regions = env.get("REGIONS", "").strip()
    event["regions"] = [r.strip() for r in regions.split(",") if r.strip()] or ["us-east-1"]
    return event


def main() -> None:
    missing = [v for v in _REQUIRED if not os.environ.get(v)]
    if missing:
        print(f"FATAL: missing required env vars: {missing}")
        sys.exit(1)
    result = handler(build_event(dict(os.environ)), None)
    print(f"scan finished: {result}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Thread `scan_tier` into the handler**

In `app/main.py`, modify the `handler` function. After the existing line `regions     = event.get("regions") or ["us-east-1"]` (around line 157), add:

```python
    scan_tier   = event.get("scan_tier", "quick")
```

Then change the `print` on the next line from:

```python
    print(f"scan start: scan={scan_id} account={account_id} regions={regions}")
```

to:

```python
    print(f"scan start: scan={scan_id} account={account_id} regions={regions} tier={scan_tier}")
```

`scan_tier` is read and logged now; the coverage engine that consumes it arrives in Slice 1. No other handler logic changes in this slice.

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd platform/lambda/shasta_runner && python -m pytest app/tests/test_run_entrypoint.py -v`
Expected: PASS — 3 tests.

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/shasta_runner/app/run.py \
        platform/lambda/shasta_runner/app/main.py \
        platform/lambda/shasta_runner/app/tests/test_run_entrypoint.py
git commit -m "feat: add Fargate entrypoint and scan_tier param to AWS scanner"
```

---

## Task B2: Dockerfile runs under Fargate

The image is built `FROM public.ecr.aws/lambda/python:3.12`, whose `ENTRYPOINT` is the Lambda runtime bootstrap. Under Fargate the task definition overrides `entryPoint` + `command` to run `python run.py` directly — the Lambda base image is fine as long as the work dir holds the app code (it does: `COPY app/ ${LAMBDA_TASK_ROOT}/`). Make this explicit and verifiable.

**Files:**
- Modify: `platform/lambda/shasta_runner/Dockerfile`

- [ ] **Step 1: Add a Fargate-mode comment + WORKDIR to the Dockerfile**

In `platform/lambda/shasta_runner/Dockerfile`, replace the last two lines:

```dockerfile
# Our wrapper handler.
COPY app/ ${LAMBDA_TASK_ROOT}/

CMD ["main.handler"]
```

with:

```dockerfile
# Our wrapper handler.
COPY app/ ${LAMBDA_TASK_ROOT}/

# Lambda mode: the base image ENTRYPOINT runs the runtime bootstrap and
# CMD names the handler. Fargate mode: the ECS task definition overrides
# entryPoint+command to `python run.py` (run.py reads params from env
# vars). Both run the same image with the same code at LAMBDA_TASK_ROOT.
WORKDIR ${LAMBDA_TASK_ROOT}
CMD ["main.handler"]
```

- [ ] **Step 2: Build the image locally to verify it still builds**

Run: `cd platform/lambda/shasta_runner && ./build.sh`
Expected: ends with `==> done. Image URI: ...:latest` and pushes to ECR. (`build.sh` stages Shasta + the shared modules, builds `linux/amd64`, pushes.)

- [ ] **Step 3: Verify the Fargate entrypoint runs inside the image**

Run (overrides the Lambda entrypoint; expects the missing-vars guard to fire):

```bash
docker run --rm --entrypoint python shasta-runner:latest run.py
```

Expected: prints `FATAL: missing required env vars: [...]` and exits non-zero. This confirms `run.py` is importable and the handler chain loads inside the image.

- [ ] **Step 4: Commit**

```bash
git add platform/lambda/shasta_runner/Dockerfile
git commit -m "chore: document Fargate run mode in scanner Dockerfile"
```

---

## Task B3: ECS Fargate task definition in CDK

Add an ECS cluster + a Fargate task definition for the scanner image to `ScanStack`. The task runs in the existing VPC. The `ScanStack` keeps the existing `shastaRunner` Lambda for now (Slice 1+ removes it once the Fargate path is proven) — this task only *adds* the Fargate path.

**Files:**
- Modify: `platform/bin/` CDK app entry (pass `vpc` to `ScanStack`)
- Modify: `platform/lib/scan-stack.ts` (add ECS cluster + task definition + outputs)

- [ ] **Step 1: Pass the VPC into ScanStack**

In the CDK app entry file under `platform/bin/` (the file that constructs `new ScanStack(app, 'CisoCopilotScan', {...})`), add `vpc: network.vpc,` to the `ScanStack` props object, alongside the existing `aiScannerRepo` etc.

- [ ] **Step 2: Add `vpc` to the ScanStack props interface**

In `platform/lib/scan-stack.ts`, add the `ec2` import at the top with the other imports:

```typescript
import * as ec2 from 'aws-cdk-lib/aws-ec2';
```

and add to the `ScanStackProps` interface:

```typescript
  vpc: ec2.Vpc;
```

- [ ] **Step 3: Add the ECS imports**

In `platform/lib/scan-stack.ts`, add with the other imports:

```typescript
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as logs from 'aws-cdk-lib/aws-logs';
```

- [ ] **Step 4: Add the cluster + task definition**

In `platform/lib/scan-stack.ts`, inside the `constructor`, after the `// ===== AWS scanner =====` Lambda block (after the `shastaRunner.addToRolePolicy(... secretsManager ...)` call, before `// ===== Azure scanner =====`), insert:

```typescript
    // ===== AWS scanner — Fargate task =====
    // The uplifted scan (Medium/Deep tiers) exceeds Lambda's 15-min ceiling,
    // so the scanner also runs as a Fargate task. Same image, different
    // entrypoint: the task overrides entryPoint+command to `python run.py`,
    // which reads scan params from container env overrides set by RunTask.
    const scanCluster = new ecs.Cluster(this, 'ScanCluster', {
      clusterName: 'ciso-copilot-scan',
      vpc:         props.vpc,
    });

    const scanTaskDef = new ecs.FargateTaskDefinition(this, 'ScanTaskDef', {
      family:         'ciso-copilot-aws-scan',
      cpu:            2048,   // 2 vCPU — headroom for Medium; Deep tunes later
      memoryLimitMiB: 4096,
    });

    scanTaskDef.addContainer('scanner', {
      image: ecs.ContainerImage.fromEcrRepository(props.shastaRunnerRepo, 'latest'),
      // Override the Lambda base-image entrypoint — run the Fargate script.
      entryPoint: ['python'],
      command:    ['run.py'],
      environment: dbEnv,
      logging: ecs.LogDriver.awsLogs({
        streamPrefix: 'aws-scan',
        logRetention: logs.RetentionDays.ONE_MONTH,
      }),
    });

    // Same permissions the scanner Lambda has: assume the customer reader
    // role, Aurora Data API, read connection secrets.
    props.dbCluster.grantDataApiAccess(scanTaskDef.taskRole);
    scanTaskDef.taskRole.addToPrincipalPolicy(new iam.PolicyStatement({
      actions:   ['sts:AssumeRole'],
      resources: ['arn:aws:iam::*:role/CISOCopilotReader'],
    }));
    scanTaskDef.taskRole.addToPrincipalPolicy(new iam.PolicyStatement({
      actions:   ['secretsmanager:GetSecretValue'],
      resources: [secretsArn],
    }));

    this.scanCluster = scanCluster;
    this.scanTaskDef = scanTaskDef;

    new cdk.CfnOutput(this, 'ScanClusterArn', { value: scanCluster.clusterArn });
    new cdk.CfnOutput(this, 'ScanTaskDefArn', { value: scanTaskDef.taskDefinitionArn });
```

- [ ] **Step 5: Expose the cluster + task def as stack properties**

In `platform/lib/scan-stack.ts`, add to the `public readonly` declarations at the top of the `ScanStack` class (alongside `public readonly shastaRunner: ...`):

```typescript
  public readonly scanCluster: ecs.Cluster;
  public readonly scanTaskDef: ecs.FargateTaskDefinition;
```

- [ ] **Step 6: Synthesize to verify the stack compiles**

Run: `cd platform && npx cdk synth CisoCopilotScan`
Expected: prints the synthesized CloudFormation YAML with no TypeScript or synth errors. Confirm an `AWS::ECS::Cluster` and an `AWS::ECS::TaskDefinition` appear in the output.

- [ ] **Step 7: Deploy the stack**

Run: `cd platform && npx cdk deploy CisoCopilotScan --require-approval never`
Expected: `CisoCopilotScan` deploys successfully; outputs include `ScanClusterArn` and `ScanTaskDefArn`.

- [ ] **Step 8: Commit**

```bash
git add platform/lib/scan-stack.ts platform/bin/
git commit -m "feat: add ECS Fargate task definition for the AWS scanner"
```

---

## Task B4: Onboarding triggers the scan via RunTask

`onboarding_aws_complete` currently async-invokes the scanner Lambda. Switch it to `ecs:RunTask` on the Fargate task definition, passing scan params as container environment overrides and `SCAN_TIER=quick`.

**Files:**
- Modify: `platform/lambda/onboarding_aws_complete/main.py:28-34` (clients + env), `:107-155` (`_enqueue_initial_scan`)
- Modify: `platform/lib/` the stack that defines the `onboarding_aws_complete` Lambda (env vars + `ecs:RunTask` / `iam:PassRole` permissions)

- [ ] **Step 1: Locate the onboarding Lambda's stack definition**

Run: `grep -rl "onboarding_aws_complete\|OnboardingAwsComplete" platform/lib/`
Expected: one stack file (the API stack). Note its path — call it `<API_STACK>` below.

- [ ] **Step 2: Replace the Lambda-invoke clients + env in the handler**

In `platform/lambda/onboarding_aws_complete/main.py`, replace these lines (28-34):

```python
CENTRAL_EVENT_BUS_ARN = os.environ["CENTRAL_EVENT_BUS_ARN"]
SHASTA_RUNNER_FN      = os.environ.get("SHASTA_RUNNER_FN", "")

rds_data = boto3.client("rds-data")
sm       = boto3.client("secretsmanager")
events   = boto3.client("events")
lambda_client = boto3.client("lambda")
```

with:

```python
CENTRAL_EVENT_BUS_ARN = os.environ["CENTRAL_EVENT_BUS_ARN"]
SCAN_CLUSTER_ARN  = os.environ.get("SCAN_CLUSTER_ARN", "")
SCAN_TASK_DEF_ARN = os.environ.get("SCAN_TASK_DEF_ARN", "")
SCAN_SUBNET_IDS   = os.environ.get("SCAN_SUBNET_IDS", "")
SCAN_SECURITY_GROUP_ID = os.environ.get("SCAN_SECURITY_GROUP_ID", "")

rds_data = boto3.client("rds-data")
sm       = boto3.client("secretsmanager")
events   = boto3.client("events")
ecs      = boto3.client("ecs")
```

- [ ] **Step 3: Rewrite `_enqueue_initial_scan` to use RunTask**

In `platform/lambda/onboarding_aws_complete/main.py`, replace the entire `_enqueue_initial_scan` function (lines 107-155) with:

```python
def _enqueue_initial_scan(
    *, tenant_id: str, conn_id: str, role_arn: str, external_id: str, account_id: str,
) -> str | None:
    """Insert a scan row and start the scanner Fargate task (Quick tier).

    Fails open — if RunTask fails, the connection is still active and the
    user can re-trigger from the app. A transient ECS hiccup must not
    block onboarding.
    """
    import uuid
    if not (SCAN_CLUSTER_ARN and SCAN_TASK_DEF_ARN and SCAN_SUBNET_IDS):
        print("WARN: scan task not configured; skipping initial scan")
        return None

    scan_id = str(uuid.uuid4())
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "INSERT INTO scans (scan_id, tenant_id, conn_id, trigger, status, tier, scope) "
            "VALUES (CAST(:sid AS UUID), CAST(:tid AS UUID), CAST(:cid AS UUID), "
            "        'onboarding', 'queued', 'quick', CAST(:scope AS JSONB))"
        ),
        parameters=[
            {"name": "sid",   "value": {"stringValue": scan_id}},
            {"name": "tid",   "value": {"stringValue": tenant_id}},
            {"name": "cid",   "value": {"stringValue": conn_id}},
            {"name": "scope", "value": {"stringValue": json.dumps({"regions": ["us-east-1"]})}},
        ],
    )

    try:
        ecs.run_task(
            cluster=SCAN_CLUSTER_ARN,
            taskDefinition=SCAN_TASK_DEF_ARN,
            launchType="FARGATE",
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets":        [s for s in SCAN_SUBNET_IDS.split(",") if s],
                    "securityGroups": [SCAN_SECURITY_GROUP_ID] if SCAN_SECURITY_GROUP_ID else [],
                    "assignPublicIp": "DISABLED",
                },
            },
            overrides={
                "containerOverrides": [{
                    "name": "scanner",
                    "environment": [
                        {"name": "SCAN_ID",     "value": scan_id},
                        {"name": "TENANT_ID",   "value": tenant_id},
                        {"name": "CONN_ID",     "value": conn_id},
                        {"name": "ROLE_ARN",    "value": role_arn},
                        {"name": "EXTERNAL_ID", "value": external_id},
                        {"name": "ACCOUNT_ID",  "value": account_id},
                        {"name": "REGIONS",     "value": "us-east-1"},
                        {"name": "SCAN_TIER",   "value": "quick"},
                    ],
                }],
            },
        )
        print(f"initial scan {scan_id} (quick) started for {conn_id}")
    except Exception as e:
        print(f"WARN: initial scan RunTask failed for {conn_id}: {e}")

    return scan_id
```

The container name `scanner` must match the name given in `scanTaskDef.addContainer('scanner', ...)` in Task B3 Step 4.

- [ ] **Step 4: Add env vars + permissions to the onboarding Lambda in CDK**

In `<API_STACK>` (from Step 1), find where the `onboarding_aws_complete` Lambda is defined. Add to its `environment` object:

```typescript
        SCAN_CLUSTER_ARN:       props.scanCluster.clusterArn,
        SCAN_TASK_DEF_ARN:      props.scanTaskDef.taskDefinitionArn,
        SCAN_SUBNET_IDS:        props.vpc.privateSubnets.map(s => s.subnetId).join(','),
        SCAN_SECURITY_GROUP_ID: props.scanTaskSecurityGroupId,
```

This requires `scanCluster`, `scanTaskDef`, `vpc`, and a scan-task security group to reach the API stack. In Task B3 the cluster + task def are already public readonly on `ScanStack`. Add to `ScanStack` (Task B3 file) a security group for the task and expose its id:

```typescript
    // In ScanStack, near the cluster definition:
    const scanTaskSg = new ec2.SecurityGroup(this, 'ScanTaskSg', {
      vpc:         props.vpc,
      description: 'AWS scanner Fargate task — egress only',
    });
    this.scanTaskSecurityGroupId = scanTaskSg.securityGroupId;
```

Add `public readonly scanTaskSecurityGroupId: string;` to the class, and pass it (plus `scanCluster`, `scanTaskDef`, `vpc`) into the API stack's props in the CDK app entry. Wire the `run_task` call to use this SG via the `SCAN_SECURITY_GROUP_ID` env var (already done in Step 3).

Then grant the onboarding Lambda permission to run the task and pass its roles. After the onboarding Lambda is defined:

```typescript
    onboardingAwsComplete.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['ecs:RunTask'],
      resources: [props.scanTaskDef.taskDefinitionArn],
    }));
    onboardingAwsComplete.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['iam:PassRole'],
      resources: [
        props.scanTaskDef.taskRole.roleArn,
        props.scanTaskDef.executionRole!.roleArn,
      ],
    }));
```

Use the actual CDK variable name of the onboarding Lambda construct in place of `onboardingAwsComplete`.

- [ ] **Step 5: Synthesize both stacks**

Run: `cd platform && npx cdk synth CisoCopilotScan CisoCopilotApi`
Expected: both stacks synthesize with no errors.

- [ ] **Step 6: Deploy both stacks**

Run: `cd platform && npx cdk deploy CisoCopilotScan CisoCopilotApi --require-approval never`
Expected: both deploy successfully.

- [ ] **Step 7: Commit**

```bash
git add platform/lambda/onboarding_aws_complete/main.py platform/lib/
git commit -m "feat: trigger AWS scan via Fargate RunTask on onboarding"
```

---

## Task B5: End-to-end verification

- [ ] **Step 1: Manually run the scan task**

Pick a real `cloud_connections` row (an active AWS connection — query `scans`/`cloud_connections` for one, or use KK's account). Insert a `scans` row and start the task by hand to confirm the Fargate path works end to end:

```bash
aws ecs run-task \
  --cluster ciso-copilot-scan \
  --task-definition ciso-copilot-aws-scan \
  --launch-type FARGATE \
  --network-configuration 'awsvpcConfiguration={subnets=[<private-subnet-id>],securityGroups=[<scan-task-sg-id>],assignPublicIp=DISABLED}' \
  --overrides '{"containerOverrides":[{"name":"scanner","environment":[
      {"name":"SCAN_ID","value":"<new-uuid>"},
      {"name":"TENANT_ID","value":"<tenant>"},
      {"name":"CONN_ID","value":"<conn>"},
      {"name":"ROLE_ARN","value":"arn:aws:iam::<acct>:role/CISOCopilotReader"},
      {"name":"EXTERNAL_ID","value":"<external-id>"},
      {"name":"ACCOUNT_ID","value":"<acct>"},
      {"name":"REGIONS","value":"us-east-1"},
      {"name":"SCAN_TIER","value":"quick"}]}]}'
```

(Insert the matching `scans` row first, as `_enqueue_initial_scan` does, so `_update_scan` has a row to update.)

- [ ] **Step 2: Watch the task logs**

Run: `aws logs tail /aws/ecs/aws-scan --since 20m --follow` (log group name derives from the `streamPrefix` — confirm the exact name in the ECS console if needed).
Expected: `scan start: ... tier=quick`, per-module finding counts, and finally `scan complete: ... findings`.

- [ ] **Step 3: Confirm the scan row completed**

Run:

```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot \
  --sql "SELECT status, tier FROM scans WHERE scan_id=CAST('<scan-uuid>' AS UUID)"
```

Expected: `status = completed`, `tier = quick`.

- [ ] **Step 4: No commit**

This task is verification only — nothing to commit. If a step fails, fix the cause in the relevant earlier task and re-verify.

---

## Self-review checklist (for the implementer, before declaring Slice 0 done)

- [ ] `python -m pytest app/tests/ -v` — all green from `platform/lambda/shasta_runner/`.
- [ ] `docs/coverage/aws-scorecard.md` is committed and shows a non-zero baseline coverage % per benchmark.
- [ ] `npx cdk synth` clean for `CisoCopilotScan` and `CisoCopilotApi`.
- [ ] A Quick scan completes end-to-end on Fargate (Task B5) with `scans.tier = 'quick'`.
- [ ] The legacy `shastaRunner` Lambda is still present and untouched — Slice 1+ retires it once the Fargate path has run in onboarding.
