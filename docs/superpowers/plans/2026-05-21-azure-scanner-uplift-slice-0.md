# Azure Scanner Uplift — Slice 0: Shared Scanner Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the cloud-agnostic scanner pipeline into a new `platform/lambda/scanner_core/` package so the upcoming Azure scanner (Slice 1) can reuse it, with zero behaviour change to the AWS scanner.

**Architecture:** Move `scan_pipeline.py` out of the AWS scanner into `scanner_core/`; add a new `scan_state.py` there (the `scans`-table status/phase/scope writes, extracted from AWS `main.py`). The AWS scanner's `build.sh` copies `scanner_core/` modules into `app/` at image-build time — the same mechanism it already uses to pull modules from `ai_scanner/`. `scan_policy.py` and `unified_writer.py` deliberately do **not** move (see spec §3, §4.1).

**Tech Stack:** Python 3.12, pytest, boto3, AWS Aurora Data API, Docker, AWS CDK (TypeScript), ECS Fargate.

**Spec:** `docs/superpowers/specs/2026-05-21-azure-scanner-uplift-design.md`

---

## File Structure

**Created:**
- `platform/lambda/scanner_core/scan_pipeline.py` — moved from `shasta_runner/app/`, unchanged. `run_units`, `ConcurrencyLimiter`, `ScanUnit`.
- `platform/lambda/scanner_core/scan_state.py` — **new.** `update_scan`, `record_scan_scope` — the `scans`-table writes, cloud-agnostic.
- `platform/lambda/scanner_core/tests/conftest.py` — puts `scanner_core/` on `sys.path` for the package's own tests.
- `platform/lambda/scanner_core/tests/__init__.py` — empty package marker.
- `platform/lambda/scanner_core/tests/test_scan_pipeline.py` — moved from `shasta_runner/app/tests/`, unchanged.
- `platform/lambda/scanner_core/tests/test_scan_state.py` — **new.** Tests for `scan_state`.

**Modified:**
- `platform/lambda/shasta_runner/build.sh` — add a copy step for `scanner_core/` modules.
- `platform/lambda/shasta_runner/.gitignore` — ignore the build-time copies `app/scan_pipeline.py`, `app/scan_state.py`.
- `platform/lambda/shasta_runner/app/tests/conftest.py` — add `scanner_core/` to `sys.path`.
- `platform/lambda/shasta_runner/app/main.py` — drop the inline `_update_scan` + the DB-config constants/client; import `update_scan` / `record_scan_scope` from `scan_state`; keep a thin local `_record_scan_scope` that builds the AWS region-shaped scope dict.

**Deleted (git-tracked → becomes a build-time copy):**
- `platform/lambda/shasta_runner/app/scan_pipeline.py`
- `platform/lambda/shasta_runner/app/tests/test_scan_pipeline.py`

---

## Conventions

- Scanner tests run with the `shasta_runner` virtualenv (it already has pytest + boto3 + pydantic). The `.venv` is gitignored and assumed to exist; if it does not, that is a pre-existing environment problem, not part of this slice.
- `main.py` imports `shasta.*`, which is not installed in that venv, so `main.py` is **not importable in tests** — this is a known, documented constraint (HANDOFF "Gotchas"). `main.py` changes in this slice are verified by the test suite staying green (no test imports `main`), a `py_compile` check, an image build, and a live smoke scan.

---

## Task 1: Establish the green baseline

No code change — record the starting state so regressions are detectable.

**Files:** none.

- [ ] **Step 1: Run the full AWS scanner test suite**

Run:
```bash
cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/ -q
```
Expected: all tests pass. Record the exact number reported (HANDOFF says ~102). This count is the regression gate for Tasks 3 and 5.

- [ ] **Step 2: Confirm the working tree is clean of unrelated changes**

Run:
```bash
git status --short
```
Expected: no modified files under `platform/lambda/shasta_runner/` (the UI-fix changes from earlier in the session, if any remain, are unrelated and should already be committed or stashed). If `app/scan_pipeline.py` or `app/unified_writer.py` show as modified, that means a stale build copy — discard those before starting.

---

## Task 2: Create `scanner_core/` and move `scan_pipeline.py`

**Files:**
- Create: `platform/lambda/scanner_core/scan_pipeline.py` (via `git mv`)
- Create: `platform/lambda/scanner_core/tests/test_scan_pipeline.py` (via `git mv`)
- Create: `platform/lambda/scanner_core/tests/__init__.py`
- Create: `platform/lambda/scanner_core/tests/conftest.py`

- [ ] **Step 1: Create the directory structure and move the module + its test**

Run:
```bash
cd /Users/kkmookhey/Projects/CISOBrief
mkdir -p platform/lambda/scanner_core/tests
git mv platform/lambda/shasta_runner/app/scan_pipeline.py \
       platform/lambda/scanner_core/scan_pipeline.py
git mv platform/lambda/shasta_runner/app/tests/test_scan_pipeline.py \
       platform/lambda/scanner_core/tests/test_scan_pipeline.py
```

The moved files are unchanged. `scan_pipeline.py` is stdlib-only (`threading`, `traceback`, `concurrent.futures`, `contextlib`, `dataclasses`, `typing`). `test_scan_pipeline.py` imports `from scan_pipeline import ConcurrencyLimiter, ScanUnit, run_units` — that bare-name import resolves once `scanner_core/` is on `sys.path` (Step 3).

- [ ] **Step 2: Create the empty test package marker**

Create `platform/lambda/scanner_core/tests/__init__.py` as an empty file.

- [ ] **Step 3: Create the test conftest that puts `scanner_core/` on the path**

Create `platform/lambda/scanner_core/tests/conftest.py`:

```python
"""Put scanner_core/ on sys.path so the package's tests can import its
modules by bare name (`from scan_pipeline import ...`), mirroring how
the modules are imported at runtime once build.sh copies them flat into
a scanner image's app/ directory."""
import sys
from pathlib import Path

_CORE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_CORE))
```

- [ ] **Step 4: Run the moved test from the scanner_core location**

Run:
```bash
cd platform/lambda/shasta_runner && \
  ./.venv/bin/python -m pytest ../scanner_core/tests/test_scan_pipeline.py -q
```
Expected: the same `test_scan_pipeline.py` tests pass (same count as before the move).

- [ ] **Step 5: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/lambda/scanner_core/
git commit -m "$(cat <<'EOF'
refactor: move scan_pipeline into new scanner_core package

scan_pipeline is cloud-agnostic (ScanUnit/run_units/ConcurrencyLimiter,
zero region assumptions). Moving it to scanner_core/ so the Azure
scanner can reuse it. AWS scanner wiring follows in the next commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Wire the AWS scanner to consume `scanner_core/`

After Task 2 the AWS scanner is broken — `app/scan_pipeline.py` no longer exists, so `main.py`'s `from scan_pipeline import ...` and the `app/tests/` that import it cannot resolve. This task restores it: tests resolve `scan_pipeline` from `scanner_core/` via `sys.path`, and the image build copies it into `app/`.

**Files:**
- Modify: `platform/lambda/shasta_runner/app/tests/conftest.py`
- Modify: `platform/lambda/shasta_runner/build.sh`
- Modify: `platform/lambda/shasta_runner/.gitignore`

- [ ] **Step 1: Add `scanner_core/` to the test `sys.path`**

In `platform/lambda/shasta_runner/app/tests/conftest.py`, the file currently reads:

```python
"""Make shasta_runner/app modules importable by bare name in tests.

At runtime in Lambda, build.sh copies `detectors/base.py` and
`unified_writer.py` from ai_scanner into `app/`. For tests we add
ai_scanner's directory to sys.path so the bare-name imports
(`from detectors.base import ...`, `import unified_writer`) resolve
without needing the copy."""
import sys
from pathlib import Path

_APP        = Path(__file__).resolve().parent.parent
_AI_SCANNER = _APP.parent.parent / "ai_scanner"

sys.path.insert(0, str(_APP))
sys.path.insert(0, str(_AI_SCANNER))
```

Replace it with:

```python
"""Make shasta_runner/app modules importable by bare name in tests.

At runtime in a scanner image, build.sh copies shared modules into
`app/`: `detectors/base.py` + `unified_writer.py` from ai_scanner, and
`scan_pipeline.py` + `scan_state.py` from scanner_core. For tests we add
those source directories to sys.path so the bare-name imports resolve
without needing the build-time copies."""
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

- [ ] **Step 2: Add the `scanner_core` copy step to `build.sh`**

In `platform/lambda/shasta_runner/build.sh`, find the existing block that copies modules from `ai_scanner` (step "1b"):

```bash
# 1b. Copy shared modules from sibling ai_scanner Lambda (detectors/base.py
#     + unified_writer.py). These are imported by app/main.py at runtime;
#     they live in ai_scanner so they don't fork. .gitignore excludes the
#     copies so they don't get committed.
echo "==> copying shared modules from ../ai_scanner"
rm -rf app/detectors app/unified_writer.py
mkdir -p app/detectors
cp ../ai_scanner/detectors/base.py app/detectors/base.py
touch                              app/detectors/__init__.py
cp ../ai_scanner/unified_writer.py app/unified_writer.py
```

Immediately **after** that block, add:

```bash
# 1c. Copy shared modules from the sibling scanner_core package
#     (scan_pipeline.py + scan_state.py). Same rationale as 1b — one
#     source of truth, .gitignore excludes the runtime copies.
echo "==> copying shared modules from ../scanner_core"
rm -f app/scan_pipeline.py app/scan_state.py
cp ../scanner_core/scan_pipeline.py app/scan_pipeline.py
cp ../scanner_core/scan_state.py    app/scan_state.py
```

- [ ] **Step 3: Ignore the build-time copies**

In `platform/lambda/shasta_runner/.gitignore`, the file currently reads:

```
.build/
.venv/

# Build-time copies of shared modules from ../ai_scanner (see build.sh).
# Source of truth lives in ai_scanner; don't commit the runtime copies.
app/detectors/
app/unified_writer.py
```

Replace the comment block + entries with:

```
.build/
.venv/

# Build-time copies of shared modules (see build.sh steps 1b/1c).
# Source of truth lives in ai_scanner/ and scanner_core/; don't commit
# the runtime copies.
app/detectors/
app/unified_writer.py
app/scan_pipeline.py
app/scan_state.py
```

- [ ] **Step 4: Confirm the AWS scanner test suite is green again**

Run:
```bash
cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/ -q
```
Expected: the same count as Task 1 Step 1, minus the `test_scan_pipeline.py` tests (they moved to `scanner_core/tests/` in Task 2). All still pass — `scan_pipeline` now resolves from `scanner_core/` via the conftest path entry. If any test errors with `ModuleNotFoundError: scan_pipeline`, the conftest change in Step 1 is wrong.

- [ ] **Step 5: Run the scanner_core test suite too**

Run:
```bash
cd platform/lambda/shasta_runner && \
  ./.venv/bin/python -m pytest ../scanner_core/tests/ -q
```
Expected: `test_scan_pipeline.py` passes. Combined with Step 4, the total across both directories equals Task 1's baseline count.

- [ ] **Step 6: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/lambda/shasta_runner/build.sh \
        platform/lambda/shasta_runner/.gitignore \
        platform/lambda/shasta_runner/app/tests/conftest.py
git commit -m "$(cat <<'EOF'
refactor: wire shasta_runner to consume scanner_core

build.sh copies scan_pipeline from scanner_core/ into app/ at image
build (same mechanism as the ai_scanner copies); test conftest adds
scanner_core/ to sys.path so tests resolve it without a build.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Add `scan_state.py` to `scanner_core/` (TDD)

`scan_state.py` is a **new** module holding the `scans`-table writes, extracted (next task) from AWS `main.py`. It is cloud-agnostic: `record_scan_scope` takes an already-shaped `scope` dict, and DB config is read lazily inside the functions so the module imports cleanly without env vars.

**Files:**
- Create: `platform/lambda/scanner_core/scan_state.py`
- Test: `platform/lambda/scanner_core/tests/test_scan_state.py`

- [ ] **Step 1: Write the failing test**

Create `platform/lambda/scanner_core/tests/test_scan_state.py`:

```python
"""scan_state writes scan status/phase/stats and the coverage map to the
`scans` table via the Aurora Data API. The rds-data client and DB config
are isolated here with a fake so the SQL/params can be asserted."""
import json

import pytest

import scan_state


class FakeRds:
    """Records execute_statement calls."""
    def __init__(self):
        self.calls = []

    def execute_statement(self, **kwargs):
        self.calls.append(kwargs)
        return {}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    fake = FakeRds()
    monkeypatch.setattr(scan_state, "_rds", fake)
    monkeypatch.setenv("DB_CLUSTER_ARN", "arn:cluster")
    monkeypatch.setenv("DB_SECRET_ARN", "arn:secret")
    monkeypatch.setenv("DB_NAME", "ciso_copilot")
    return fake


def _params(call):
    """execute_statement parameters list -> {name: value-dict}."""
    return {p["name"]: p["value"] for p in call["parameters"]}


def test_update_scan_status_only(_isolate):
    scan_state.update_scan("scan-1", "running")
    call = _isolate.calls[-1]
    assert call["resourceArn"] == "arn:cluster"
    assert call["secretArn"] == "arn:secret"
    assert call["database"] == "ciso_copilot"
    assert call["sql"] == (
        "UPDATE scans SET status = :status "
        "WHERE scan_id = CAST(:sid AS UUID)")
    p = _params(call)
    assert p["sid"] == {"stringValue": "scan-1"}
    assert p["status"] == {"stringValue": "running"}


def test_update_scan_with_phase(_isolate):
    scan_state.update_scan("scan-1", "running", phase="first_signal")
    call = _isolate.calls[-1]
    assert "phase = :phase" in call["sql"]
    assert _params(call)["phase"] == {"stringValue": "first_signal"}


def test_update_scan_terminal_status_sets_finished_at(_isolate):
    scan_state.update_scan("scan-1", "completed", phase="done")
    sql = _isolate.calls[-1]["sql"]
    assert "finished_at = now()" in sql


def test_update_scan_running_does_not_set_finished_at(_isolate):
    scan_state.update_scan("scan-1", "running")
    assert "finished_at" not in _isolate.calls[-1]["sql"]


def test_update_scan_with_stats(_isolate):
    scan_state.update_scan("scan-1", "completed", stats={"findings": 7})
    call = _isolate.calls[-1]
    assert "stats = CAST(:stats AS JSONB)" in call["sql"]
    assert json.loads(_params(call)["stats"]["stringValue"]) == {"findings": 7}


def test_update_scan_with_error(_isolate):
    scan_state.update_scan("scan-1", "failed", error="boom")
    call = _isolate.calls[-1]
    assert "error = :error" in call["sql"]
    assert _params(call)["error"] == {"stringValue": "boom"}


def test_record_scan_scope_writes_passed_dict(_isolate):
    scope = {"tier": "quick", "regions": {"us-east-1": {"state": "active"}}}
    scan_state.record_scan_scope("scan-1", scope)
    call = _isolate.calls[-1]
    assert call["sql"] == (
        "UPDATE scans SET scope = CAST(:scope AS JSONB) "
        "WHERE scan_id = CAST(:sid AS UUID)")
    p = _params(call)
    assert p["sid"] == {"stringValue": "scan-1"}
    assert json.loads(p["scope"]["stringValue"]) == scope
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
cd platform/lambda/shasta_runner && \
  ./.venv/bin/python -m pytest ../scanner_core/tests/test_scan_state.py -q
```
Expected: FAIL — `ModuleNotFoundError: No module named 'scan_state'`.

- [ ] **Step 3: Write `scan_state.py`**

Create `platform/lambda/scanner_core/scan_state.py`:

```python
"""Shared `scans`-table state writes — status / phase / stats and the
per-scan coverage map. Cloud-agnostic: the AWS and Azure scanners both
use it.

`record_scan_scope` takes an already-shaped `scope` dict, so a
region-keyed (AWS) or subscription-keyed (Azure) coverage map both work
without this module knowing the difference.

DB config (`DB_CLUSTER_ARN` / `DB_SECRET_ARN` / `DB_NAME`) is read
lazily inside the functions, not at import — so the module imports
cleanly in test collection without those env vars set.
"""
from __future__ import annotations

import json
import os

import boto3

# boto3.client() is offline (no creds/network needed), so a module-level
# client is safe at import. Tests monkeypatch this attribute.
_rds = boto3.client("rds-data")


def _db() -> tuple[str, str, str]:
    return (os.environ["DB_CLUSTER_ARN"],
            os.environ["DB_SECRET_ARN"],
            os.environ["DB_NAME"])


def update_scan(scan_id: str, status: str, *, phase: str | None = None,
                stats: dict | None = None, error: str | None = None) -> None:
    """UPDATE the `scans` row. `phase`/`stats`/`error` are written only
    when supplied. A terminal status also stamps `finished_at`."""
    cluster, secret, name = _db()
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
        params.append({"name": "stats",
                       "value": {"stringValue": json.dumps(stats)}})
    if error is not None:
        sql_parts.append("error = :error")
        params.append({"name": "error", "value": {"stringValue": error}})
    sql = ", ".join(sql_parts) + " WHERE scan_id = CAST(:sid AS UUID)"
    _rds.execute_statement(resourceArn=cluster, secretArn=secret,
                           database=name, sql=sql, parameters=params)


def record_scan_scope(scan_id: str, scope: dict) -> None:
    """Write a pre-shaped coverage map to `scans.scope`. The caller owns
    the shape — this module does not interpret it."""
    cluster, secret, name = _db()
    _rds.execute_statement(
        resourceArn=cluster, secretArn=secret, database=name,
        sql=("UPDATE scans SET scope = CAST(:scope AS JSONB) "
             "WHERE scan_id = CAST(:sid AS UUID)"),
        parameters=[
            {"name": "sid",   "value": {"stringValue": scan_id}},
            {"name": "scope", "value": {"stringValue": json.dumps(scope)}},
        ],
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run:
```bash
cd platform/lambda/shasta_runner && \
  ./.venv/bin/python -m pytest ../scanner_core/tests/test_scan_state.py -q
```
Expected: PASS — all 7 tests.

- [ ] **Step 5: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/lambda/scanner_core/scan_state.py \
        platform/lambda/scanner_core/tests/test_scan_state.py
git commit -m "$(cat <<'EOF'
feat: add scan_state to scanner_core

update_scan + record_scan_scope — the scans-table writes, cloud-agnostic
(scope dict passed in pre-shaped). Lazy DB config so it imports without
env vars. AWS main.py is migrated onto it next.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Migrate AWS `main.py` onto `scan_state`

Replace `main.py`'s inline `_update_scan` and the body of `_record_scan_scope` with calls into `scan_state`. Behaviour is identical — the AWS region-shaped `scope` dict is still built in `main.py`.

`main.py` is not importable in the test venv (it imports `shasta.*`), so verification is: the test suite stays green, `py_compile` passes, and the image build + live smoke scan in Task 6.

**Files:**
- Modify: `platform/lambda/shasta_runner/app/main.py`

- [ ] **Step 1: Remove the DB-config constants and the module-level rds client**

In `main.py`, delete these lines (currently around lines 79-87):

```python
# === Config ===
DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

_SCANNER_VERSION  = "shasta_runner.0.2.0"
_DETECTOR_ID_BASE = "shasta_runner"

rds_data = boto3.client("rds-data")
sts      = boto3.client("sts")
```

Replace with (drops the three `DB_*` constants and the `rds_data` client — now owned by `scan_state`; keeps the version constants and the `sts` client):

```python
_SCANNER_VERSION  = "shasta_runner.0.2.0"
_DETECTOR_ID_BASE = "shasta_runner"

sts = boto3.client("sts")
```

Then check whether `import os` (near the top of `main.py`) is still
used:
```bash
cd platform/lambda/shasta_runner && grep -n "os\." app/main.py
```
If there is **no output**, `os` is now unused (its only use was the
deleted `os.environ[...]` reads) — delete the `import os` line. If there
is output, leave the import.

- [ ] **Step 2: Add the `scan_state` import**

In `main.py`, find the import block (currently around lines 71-76):

```python
from scan_pipeline     import ConcurrencyLimiter, ScanUnit, run_units
from scan_policy       import build_scan_plan

# === Shared writer + emission types (copied in by build.sh) ===
from detectors.base import EdgeEmission, EntityEmission, FindingEmission
from unified_writer import commit_scan, mark_scan_failed
```

Replace with:

```python
from scan_pipeline     import ConcurrencyLimiter, ScanUnit, run_units
from scan_policy       import build_scan_plan

# === Shared modules (copied in by build.sh) ===
from detectors.base import EdgeEmission, EntityEmission, FindingEmission
from scan_state     import record_scan_scope, update_scan
from unified_writer import commit_scan, mark_scan_failed
```

- [ ] **Step 3: Replace the `_record_scan_scope` body with a thin wrapper**

In `main.py`, the function currently reads (around lines 550-568):

```python
def _record_scan_scope(scan_id: str, scan_tier: str, discovery_method: str,
                       coverage_map: dict) -> None:
    """Write the per-scan coverage map to scans.scope (spec §9)."""
    scope = {
        "tier": scan_tier,
        "discovery": {"method": discovery_method},
        "regions": {k: v for k, v in coverage_map.items() if k != "global"},
    }
    if "global" in coverage_map:
        scope["global"] = coverage_map["global"]
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

Replace it with (keeps the AWS region-shaped scope-dict construction here, delegates the write to `scan_state`):

```python
def _record_scan_scope(scan_id: str, scan_tier: str, discovery_method: str,
                       coverage_map: dict) -> None:
    """Build the AWS region-shaped coverage map and write it to
    scans.scope (spec §9). The shaping is AWS-specific; the write is
    delegated to the shared scanner_core.scan_state."""
    scope = {
        "tier": scan_tier,
        "discovery": {"method": discovery_method},
        "regions": {k: v for k, v in coverage_map.items() if k != "global"},
    }
    if "global" in coverage_map:
        scope["global"] = coverage_map["global"]
    record_scan_scope(scan_id, scope)
```

- [ ] **Step 4: Delete the inline `_update_scan` function**

In `main.py`, delete the entire `_update_scan` function (currently the last function in the file, around lines 571-592):

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

Also update the section header comment just above it (currently around line 546-548):

```python
# ============================================================================
# Legacy `scans` table updates (kept — separate from ai_scans / commit_scan)
# ============================================================================
```

Change it to:

```python
# ============================================================================
# `scans` table scope write (AWS region-shaped; write delegated to scan_state)
# ============================================================================
```

- [ ] **Step 5: Point the six `_update_scan` call sites at the imported `update_scan`**

In `main.py`'s `handler`, there are six calls to `_update_scan`. Rename each to `update_scan` (the imported function — identical signature). The call sites:

```python
update_scan(scan_id, status="running", phase="region_discovery")
```
```python
update_scan(scan_id, status="running", phase="first_signal")
```
```python
update_scan(scan_id, status="running", phase="crown_jewel")
```
```python
update_scan(scan_id, status="running", phase="full")
```
```python
update_scan(scan_id, status=final_status, phase="done", stats={
    "entities": len(entities), "edges": len(edges),
    "findings": len(findings), "tier": scan_tier,
    "regions": regions,
})
```
```python
update_scan(scan_id, status="failed", phase="done", error=err)
```

The mechanical change is `_update_scan(` → `update_scan(` at all six sites. `_record_scan_scope` is unchanged at its call site (it is still a local function — Step 3 only changed its body).

- [ ] **Step 6: Syntax-check `main.py`**

Run:
```bash
cd platform/lambda/shasta_runner && ./.venv/bin/python -m py_compile app/main.py
```
Expected: no output, exit 0. (This catches syntax errors; it does not import `shasta.*`, so it does not fully load the module — that is expected.)

- [ ] **Step 7: Confirm no stale reference remains**

Run:
```bash
cd platform/lambda/shasta_runner && \
  grep -n "_update_scan\|DB_CLUSTER_ARN\|DB_SECRET_ARN\|DB_NAME\|rds_data" app/main.py
```
Expected: **no output.** Any hit means a reference to a now-deleted symbol was missed — fix it before continuing.

- [ ] **Step 8: Run the full AWS scanner test suite**

Run:
```bash
cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/ -q
```
Expected: same count as Task 3 Step 4, all passing. No test imports `main`, so this confirms nothing else regressed.

- [ ] **Step 9: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/lambda/shasta_runner/app/main.py
git commit -m "$(cat <<'EOF'
refactor: migrate AWS scanner main.py onto scanner_core.scan_state

Drops the inline _update_scan and the DB-config constants; imports
update_scan/record_scan_scope from scan_state. The AWS region-shaped
scope dict is still built in main.py. No behaviour change.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Build, deploy, and regression-verify the AWS scanner

The shared-core extraction must not change the deployed AWS scanner's behaviour. This task rebuilds the image (exercising the new `build.sh` copy step), deploys it, and verifies a live scan.

**Files:** none (build + deploy + verification).

- [ ] **Step 1: Build and push the scanner image**

Run:
```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform/lambda/shasta_runner && ./build.sh
```
Expected: the build logs show both `==> copying shared modules from ../ai_scanner` and the new `==> copying shared modules from ../scanner_core`, then a successful `docker push` ending with `==> done. Image URI: …/shasta-runner:latest`.

- [ ] **Step 2: Confirm the copied modules landed in the build context**

Run:
```bash
ls /Users/kkmookhey/Projects/CISOBrief/platform/lambda/shasta_runner/app/scan_pipeline.py \
   /Users/kkmookhey/Projects/CISOBrief/platform/lambda/shasta_runner/app/scan_state.py
```
Expected: both files exist (build-time copies; gitignored). If missing, the `build.sh` step 1c edit from Task 3 is wrong.

- [ ] **Step 3: Deploy the scanner stack**

Run:
```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform && npx cdk deploy CisoCopilotScan --require-approval never
```
Expected: completes successfully. As with prior scanner-image deploys, CDK may report `CisoCopilotScan (no changes)` — the ECS task definition pins the `:latest` tag, so the next `RunTask` pulls the freshly pushed image regardless.

- [ ] **Step 4: Run a live Quick smoke scan**

Trigger a Quick rescan of the existing AWS connection (via the web app's scan picker, or by invoking the rescan path). Then watch the scan row reach a terminal state:

```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot \
  --sql "SELECT scan_id, status, phase, tier, jsonb_typeof(scope) AS scope_type FROM scans ORDER BY started_at DESC LIMIT 3"
```
Expected: the newest row reaches `status = completed` (or `partial`), `phase = done`, `tier = quick`, and `scope_type = object` — confirming `update_scan` and `record_scan_scope` (now routed through `scan_state`) still write correctly. A Quick scan completes in roughly 3-5 minutes (HANDOFF baseline).

- [ ] **Step 5: Update HANDOFF.md**

Add a short entry under the AWS scanner section of `HANDOFF.md` recording that the shared `scanner_core/` package now exists (`scan_pipeline` + `scan_state`), that `shasta_runner` consumes it via `build.sh`, and that Slice 0 of the Azure scanner uplift is complete. Then commit:

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add HANDOFF.md
git commit -m "$(cat <<'EOF'
docs: record Azure-uplift Slice 0 (scanner_core extraction)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Done criteria

- [ ] `platform/lambda/scanner_core/` exists with `scan_pipeline.py`, `scan_state.py`, and a passing `tests/` suite.
- [ ] `shasta_runner/build.sh` copies `scanner_core/` modules into `app/`; the copies are gitignored.
- [ ] AWS `main.py` uses `scan_state.update_scan` / `record_scan_scope`; no `_update_scan`, `DB_*`, or `rds_data` references remain in it.
- [ ] The full AWS scanner test suite passes at the Task 1 baseline count (`scan_pipeline` tests now counted under `scanner_core/tests/`).
- [ ] A live Quick scan completes cleanly with a well-formed `scans.scope`.
- [ ] No `ai_scanner` change, no Azure change — those are Slice 1.
