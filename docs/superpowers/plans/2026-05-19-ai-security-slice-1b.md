# AI Security Slice 1b — Scanner + AI Inventory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Customer can click "Scan" on a repo, wait ~30s, see real AI assets (frameworks, models, MCP servers, vector DBs, prompts, agents) and AI-typed findings appear on the web app's AI Inventory tab and the iOS AI tab — each with a deterministic Trust Evidence Packet.

**Architecture:** New container Lambda `ai_scanner` (x86_64, 2048 MB, 600 s, 4 GB ephemeral) consumes an SQS queue, shallow-clones a GitHub repo via the installation token, runs 8 deterministic detectors + a cross-detector correlator pass, writes assets/relationships/findings to Aurora in one transaction. New `ai_scan_api` Lambda exposes 5 endpoints (scan trigger + list/detail + asset list/detail). Web app enables the Scan button on the repo picker, adds AI Inventory + AssetDetail pages. iOS gets a read-only AI tab. Builds on Slice 1a (`004_phase_ai.sql` schema, `ai_github` Lambda, GitHub App install + repo picker).

**Tech Stack:** Python 3.12 container Lambda, ripgrep, AST via `ast` stdlib (no tree-sitter to keep image small), boto3 RDS-Data API for writes, urllib stdlib for HTTP, SQS with DLQ, Vite + React + TS + Tailwind, SwiftUI.

**Spec:** `docs/superpowers/specs/2026-05-18-ai-security-slice-1-design.md` §9 (full mini-slice 1b), §6 (data model, already migrated), §7 (evidence packet format).

---

## Pre-flight check (do BEFORE Task 1)

```bash
cd /Users/kkmookhey/Projects/CISOBrief

# 1. Confirm Slice 1a is merged
git log --oneline -5
# Expected: top commit is fa4a7a8 (or descendant). HEAD is on main.

# 2. Create the Slice 1b branch
git switch -c feat/ai-security-slice-1b

# 3. Confirm Docker Desktop is running (required for the container build)
docker info >/dev/null && echo "docker OK" || echo "docker NOT running — start Docker Desktop"

# 4. Confirm pytest available locally (used throughout for TDD)
/Users/kkmookhey/venv/bin/pytest --version
```

All four checks must succeed before starting Task 1. If pytest isn't in `~/venv`, find an alternative or `pip install pytest --user`.

## File structure

Files created or modified by this plan, with responsibilities:

```
platform/
  lambda/
    ai_scanner/                                     [CREATE] new directory
      Dockerfile                                    [CREATE] container image (python:3.12 base + ripgrep + deps)
      build.sh                                      [CREATE] build + push to ECR
      requirements.txt                              [CREATE] runtime deps for the scanner
      main.py                                       [CREATE] SQS event handler entry point
      scan_runner.py                                [CREATE] clone repo + run detectors + write
      writer.py                                     [CREATE] transactional upserts to ai_* tables
      evidence.py                                   [CREATE] EvidencePacket builder
      detectors/
        __init__.py                                 [CREATE] empty
        base.py                                     [CREATE] Detector protocol + DetectorResult dataclass
        framework.py                                [CREATE] detector 1
        model_usage.py                              [CREATE] detector 2
        mcp_server.py                               [CREATE] detector 3
        agentic_workflow.py                         [CREATE] detector 4
        vector_db.py                                [CREATE] detector 5
        embedding.py                                [CREATE] detector 6
        prompt.py                                   [CREATE] detector 7
        secrets_in_ai_code.py                       [CREATE] detector 8
        correlator.py                               [CREATE] cross-detector pass
      tests/
        __init__.py                                 [CREATE] empty
        conftest.py                                 [CREATE] sys.path injection (matches ai_github pattern)
        fixtures/
          <one dir per detector>/                   [CREATE] synthetic repos + golden JSON
        test_evidence.py                            [CREATE] EvidencePacket round-trip + signature stub
        test_writer.py                              [CREATE] transactional write path
        test_detectors.py                           [CREATE] per-detector golden-file tests
        test_correlator.py                          [CREATE] cross-detector emission tests
        test_scan_runner.py                         [CREATE] end-to-end on a fixture repo
    ai_scan_api/                                    [CREATE] new directory (NEW Lambda)
      main.py                                       [CREATE] handler dispatch for 5 routes
      helpers.py                                    [CREATE] resp + tenant resolver (mirror ai_github)
      tests/
        __init__.py                                 [CREATE] empty
        conftest.py                                 [CREATE] sys.path injection
        test_handler.py                             [CREATE] per-route tests
  lib/
    ecr-stack.ts                                    [MODIFY] declare ai-scanner ECR repo
    scan-stack.ts                                   [MODIFY] add SQS queue + ai_scanner DockerImageFunction
    api-stack.ts                                    [MODIFY] add ai_scan_api Lambda + 5 routes + SQS write IAM
    bin/platform.ts                                 [MODIFY] thread new ECR repo to scan-stack

web/
  src/
    lib/api.ts                                      [MODIFY] add Scan/AIAsset types + 5 client methods
    routes/
      RepoPicker.tsx                                [MODIFY] enable Scan button + status polling
      AIInventory.tsx                               [CREATE] /ai/inventory
      AssetDetail.tsx                               [CREATE] /ai/inventory/:asset_id
    App.tsx                                         [MODIFY] register 2 new routes

ios/CISOCopilot/
  Services/
    APIClient.swift                                 [MODIFY] add AI inventory methods + types
  Views/
    MainTabView.swift                               [MODIFY] add "AI" tab
    AI/
      AIInventoryView.swift                         [CREATE]
      AIAssetDetailView.swift                       [CREATE]
  project.yml                                       [MODIFY] register new source files (xcodegen)
```

---

## Phase A — Scanner infrastructure (3 tasks)

### Task 1: Add `ai-scanner` ECR repository

**Files:**
- Modify: `platform/lib/ecr-stack.ts`
- Modify: `platform/bin/platform.ts`

The existing pattern: one `ecr.Repository` per scanner. Declare a new one for `ai-scanner`, export it through the stack's public field, thread it into `scan-stack` via `bin/platform.ts`.

- [ ] **Step 1: Add the repo field to `EcrStack`**

Read `platform/lib/ecr-stack.ts` for the existing class shape. Add (matching style):

```typescript
  public readonly aiScanner: ecr.Repository;
```

And in the constructor, add a line alongside the existing `repo('...', '...')` calls:

```typescript
    this.aiScanner = repo('AiScanner', 'ai-scanner');
```

- [ ] **Step 2: Update `bin/platform.ts` to thread the new repo into scan-stack**

Read `platform/bin/platform.ts` and find where `scan-stack` is instantiated. Add `aiScannerRepo: ecr.aiScanner,` (or whatever the local name is) to the props passed in.

- [ ] **Step 3: Synth + deploy ECR stack**

```bash
cd platform
npx cdk synth CisoCopilotEcr > /tmp/synth-ecr.yaml 2>&1
grep -c 'ai-scanner' /tmp/synth-ecr.yaml
# Expected: ≥1 (the repository name appears at least once)

npx cdk deploy CisoCopilotEcr --require-approval never
# Expected: "1 resource added" (the new Repository)
```

- [ ] **Step 4: Commit**

```bash
git add platform/lib/ecr-stack.ts platform/bin/platform.ts
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "feat(platform): add ai-scanner ECR repo"
```

---

### Task 2: Add SQS scan queue + DLQ to scan-stack

**Files:**
- Modify: `platform/lib/scan-stack.ts`

A standard SQS queue throttles scanner invocations. The DLQ captures permanently-failed messages (3 redrive attempts then quarantined).

- [ ] **Step 1: Read existing scan-stack structure**

Read `platform/lib/scan-stack.ts` end-to-end. Locate the `ScanStackProps` interface (currently has `shastaRunnerRepo`, `shastaRunnerAzureRepo`, etc.) and the constructor. You will:
- Add `aiScannerRepo: ecr.Repository;` to props
- Add `import * as sqs from 'aws-cdk-lib/aws-sqs';` to the imports

- [ ] **Step 2: Add the props field + sqs import**

In the imports block at the top of `scan-stack.ts`:

```typescript
import * as sqs from 'aws-cdk-lib/aws-sqs';
```

In `ScanStackProps`:

```typescript
  aiScannerRepo: ecr.Repository;
```

- [ ] **Step 3: Declare the DLQ + main queue (inside the stack constructor)**

After the existing Lambda definitions and before the `CfnOutput`s, add:

```typescript
    // ========================================================================
    // ai-scan-queue — SQS work queue for the AI scanner Lambda
    // ========================================================================
    const aiScanDlq = new sqs.Queue(this, 'AiScanDlq', {
      queueName:        'ai-scan-dlq',
      retentionPeriod:  cdk.Duration.days(14),
    });

    this.aiScanQueue = new sqs.Queue(this, 'AiScanQueue', {
      queueName:               'ai-scan-queue',
      visibilityTimeout:       cdk.Duration.seconds(720),  // > Lambda timeout (600s)
      retentionPeriod:         cdk.Duration.days(4),
      deadLetterQueue: {
        queue:           aiScanDlq,
        maxReceiveCount: 3,
      },
    });
```

Add the public field at the top of the class so api-stack can reference it:

```typescript
  public readonly aiScanQueue: sqs.Queue;
```

- [ ] **Step 4: Synth + deploy**

```bash
cd platform
npx cdk synth CisoCopilotScan > /tmp/synth-scan.yaml 2>&1
grep -c 'AiScanQueue\|AiScanDlq' /tmp/synth-scan.yaml
# Expected: ≥2

npx cdk deploy CisoCopilotScan --require-approval never
# Expected: 2 resources added (Queue + Dlq), unless other changes accumulate
```

- [ ] **Step 5: Commit**

```bash
git add platform/lib/scan-stack.ts
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "feat(platform): add ai-scan-queue SQS + DLQ in scan-stack"
```

---

### Task 3: Scaffold ai_scanner Lambda directory (Dockerfile + build.sh + requirements.txt + skeleton)

**Files:**
- Create: `platform/lambda/ai_scanner/Dockerfile`
- Create: `platform/lambda/ai_scanner/build.sh` (executable)
- Create: `platform/lambda/ai_scanner/requirements.txt`
- Create: `platform/lambda/ai_scanner/main.py` (stub handler)
- Create: `platform/lambda/ai_scanner/tests/__init__.py` (empty)
- Create: `platform/lambda/ai_scanner/tests/conftest.py`

Mirror the pattern in `platform/lambda/shasta_runner/` but skip the Shasta staging step (we don't depend on Shasta source). Install `boto3` + a small list of deps.

- [ ] **Step 1: Write `Dockerfile`**

```dockerfile
# platform/lambda/ai_scanner/Dockerfile
# ai-scanner — Lambda container that clones a customer's GitHub repo and
# runs 8 deterministic AI-discovery detectors over the source.
#
# Build with build.sh; pushed to the ai-scanner ECR repo.

FROM public.ecr.aws/lambda/python:3.12

# System deps: git for cloning, ripgrep for fast pattern search.
RUN dnf install -y git ripgrep && dnf clean all

# Python deps. Pinned for reproducibility.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Source.
COPY . ${LAMBDA_TASK_ROOT}/

CMD ["main.handler"]
```

- [ ] **Step 2: Write `requirements.txt`**

```
# platform/lambda/ai_scanner/requirements.txt
# Runtime deps for the AI scanner Lambda.
boto3>=1.35.0
PyJWT[crypto]==2.10.1
```

`PyJWT[crypto]` is needed because the scanner mints a GitHub App JWT to download an installation token before cloning. Same pattern as the `ai_github` Lambda in Slice 1a.

- [ ] **Step 3: Write `build.sh`**

```bash
#!/bin/bash
# platform/lambda/ai_scanner/build.sh — build + push ai-scanner image to ECR.

set -euo pipefail
cd "$(dirname "$0")"

REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REPO="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/ai-scanner"
TAG="${1:-latest}"

echo "==> ECR auth"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$REPO" >/dev/null

echo "==> docker build (linux/amd64) → $REPO:$TAG"
docker build \
  --platform linux/amd64 \
  --provenance=false \
  ${NO_CACHE:+--no-cache} \
  -t "ai-scanner:$TAG" \
  -t "$REPO:$TAG" \
  -t "$REPO:latest" \
  .

echo "==> docker push $REPO:$TAG"
docker push "$REPO:$TAG"
if [[ "$TAG" != "latest" ]]; then
  docker push "$REPO:latest"
fi

echo "==> done. Image URI: $REPO:$TAG"
```

Make it executable:

```bash
chmod +x platform/lambda/ai_scanner/build.sh
```

- [ ] **Step 4: Write the handler stub**

```python
# platform/lambda/ai_scanner/main.py
"""SQS-triggered handler for the AI scanner Lambda.

Event shape (SQS batch):
  {"Records": [{"body": "{\"scan_id\": \"...\", \"tenant_id\": \"...\", ...}"}]}

For each record, run a full scan + write the result. Errors raise so SQS
retries the message (up to maxReceiveCount, then DLQ).
"""
from __future__ import annotations

import json
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ai_scanner")


def handler(event: dict, context) -> dict:
    records = event.get("Records") or []
    log.info("ai_scanner invoked with %d record(s)", len(records))
    for r in records:
        body = json.loads(r.get("body") or "{}")
        scan_id = body.get("scan_id")
        log.info("scan_id=%s (stub — implementation in Tasks 4+)", scan_id)
    return {"statusCode": 200, "body": json.dumps({"scans_processed": len(records)})}
```

- [ ] **Step 5: Write the test conftest**

```python
# platform/lambda/ai_scanner/tests/conftest.py
"""Make modules inside ai_scanner/ importable by bare name in tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

Plus an empty `tests/__init__.py`:

```bash
touch platform/lambda/ai_scanner/tests/__init__.py
```

- [ ] **Step 6: Sanity-check the image builds locally**

```bash
cd platform/lambda/ai_scanner
./build.sh
# Expected: image built + pushed. Takes ~2-3 min for the first build
# (downloading the lambda/python:3.12 base + dnf install of git/ripgrep).
```

Watch for failures — if `dnf install` errors on `ripgrep` (sometimes called `rg` on Amazon Linux 2023), check the AL2023 package name. If unavailable, fall back to `grep -P` or skip ripgrep for now and revisit.

- [ ] **Step 7: Commit**

```bash
git add platform/lambda/ai_scanner/
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "feat(platform): scaffold ai_scanner Lambda (Dockerfile + build.sh + stub handler)"
```

---

## Phase B — Scan runner core (2 tasks)

### Task 4: Scan context + shallow clone helper

**Files:**
- Create: `platform/lambda/ai_scanner/scan_runner.py`
- Create: `platform/lambda/ai_scanner/tests/test_scan_runner.py`

The scan runner orchestrates: parse SQS body → resolve installation token (via the github-app credentials secret already in Secrets Manager from Slice 1a) → shallow-clone the repo into `/tmp/scan-<scan_id>` → return a ScanContext that the detector pipeline consumes.

- [ ] **Step 1: Write the failing test**

```python
# platform/lambda/ai_scanner/tests/test_scan_runner.py
"""Tests for the scan runner orchestration layer."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def stub_env(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_SECRET_ARN", "arn:fake")
    monkeypatch.setenv("DB_CLUSTER_ARN", "arn:db")
    monkeypatch.setenv("DB_SECRET_ARN", "arn:secret")
    monkeypatch.setenv("DB_NAME", "ciso_copilot")
    monkeypatch.setenv("SCANNER_VERSION", "0.1.0-test")
    # boto3 stubs (cred + secrets access) so module-level imports succeed
    import boto3
    class _FakeSm:
        def get_secret_value(self, SecretId):
            return {"SecretString": json.dumps({
                "app_id": "3763791",
                "client_id": "Iv23liTest",
                "client_secret": "secret",
                "private_key": "-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----",
            })}
    class _FakeData:
        def execute_statement(self, **kw): return {"records": []}
    monkeypatch.setattr(boto3, "client",
                        lambda name, **_kw: _FakeSm() if name == "secretsmanager" else _FakeData())


def test_scan_context_built_from_sqs_body():
    import scan_runner
    body = {
        "scan_id":         "11111111-1111-1111-1111-111111111111",
        "tenant_id":       "22222222-2222-2222-2222-222222222222",
        "connection_id":   "33333333-3333-3333-3333-333333333333",
        "repo_asset_id":   "44444444-4444-4444-4444-444444444444",
        "repo_full_name":  "kk/foo",
        "default_branch":  "main",
        "installation_id": 99999,
    }
    ctx = scan_runner.ScanContext.from_message(body, repo_workdir=Path("/tmp/x"), head_commit_sha="abc123")
    assert ctx.scan_id == body["scan_id"]
    assert ctx.repo_full_name == "kk/foo"
    assert ctx.installation_id == 99999


def test_clone_repo_uses_installation_token(monkeypatch, tmp_path):
    """clone_repo should call git with the right URL + return commit SHA."""
    import scan_runner

    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        # write a fake .git/HEAD so the next rev-parse works
        head_dir = Path(kw.get("cwd") or ".") / ".git"
        head_dir.mkdir(parents=True, exist_ok=True)
        (head_dir / "HEAD").write_text("ref: refs/heads/main\n")
        # next call should be rev-parse — return a fixture sha
        return subprocess.CompletedProcess(cmd, 0, stdout=b"deadbeef1234\n", stderr=b"")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(scan_runner, "_installation_token", lambda iid: "ghs_fake_token")

    workdir = tmp_path / "scan"
    sha = scan_runner.clone_repo(installation_id=99999, repo_full_name="kk/foo",
                                  default_branch="main", workdir=workdir)
    assert sha == "deadbeef1234"
    # ensure the clone URL embedded the token
    assert any("x-access-token:ghs_fake_token@github.com/kk/foo.git" in str(part)
               for part in captured["cmd"])


def test_clone_repo_fails_on_oversize(monkeypatch, tmp_path):
    """If the repo exceeds the 4 GB ceiling, raise RepoTooLarge."""
    import scan_runner

    def fake_check_output(cmd, **kw):
        # `du -s -B1 .` returns ~4.5 GB
        return b"4831838208\t.\n"
    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(scan_runner, "_installation_token", lambda iid: "tok")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
        a[0] if a else [], 0, stdout=b"abc\n", stderr=b""
    ))

    with pytest.raises(scan_runner.RepoTooLarge):
        scan_runner.clone_repo(installation_id=1, repo_full_name="kk/big",
                                default_branch="main", workdir=tmp_path / "x")
```

Run it and confirm it fails:

```bash
/Users/kkmookhey/venv/bin/pytest platform/lambda/ai_scanner/tests/test_scan_runner.py -v
# Expected: ImportError on scan_runner
```

- [ ] **Step 2: Implement `scan_runner.py`**

```python
# platform/lambda/ai_scanner/scan_runner.py
"""Scan orchestration: build context, clone repo, hand off to detectors."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import boto3
import jwt as pyjwt

GITHUB_APP_SECRET_ARN = os.environ["GITHUB_APP_SECRET_ARN"]
SCANNER_VERSION = os.environ.get("SCANNER_VERSION", "0.1.0")
MAX_CLONE_BYTES = 4 * 1024 * 1024 * 1024  # 4 GB

_sm = boto3.client("secretsmanager")
_credentials_cache: dict | None = None
_token_cache: dict[int, tuple[str, float]] = {}


class RepoTooLarge(RuntimeError):
    """Repo exceeds the scanner's 4 GB clone ceiling."""


@dataclass(frozen=True)
class ScanContext:
    scan_id:         str
    tenant_id:       str
    connection_id:   str
    repo_asset_id:   str
    repo_full_name:  str
    default_branch:  str
    head_commit_sha: str
    installation_id: int
    repo_workdir:    Path
    scanner_version: str = SCANNER_VERSION

    @classmethod
    def from_message(cls, body: dict, repo_workdir: Path, head_commit_sha: str) -> "ScanContext":
        return cls(
            scan_id=body["scan_id"],
            tenant_id=body["tenant_id"],
            connection_id=body["connection_id"],
            repo_asset_id=body["repo_asset_id"],
            repo_full_name=body["repo_full_name"],
            default_branch=body["default_branch"],
            head_commit_sha=head_commit_sha,
            installation_id=body["installation_id"],
            repo_workdir=repo_workdir,
        )


def clone_repo(installation_id: int, repo_full_name: str, default_branch: str,
               workdir: Path) -> str:
    """Shallow-clone the repo and return the head commit SHA. Raises RepoTooLarge."""
    workdir.mkdir(parents=True, exist_ok=True)
    token = _installation_token(installation_id)
    url = f"https://x-access-token:{token}@github.com/{repo_full_name}.git"

    subprocess.run(
        ["git", "clone", "--depth=1", "--single-branch", "--branch", default_branch,
         url, str(workdir)],
        check=True, capture_output=True,
    )

    # Sanity-check size BEFORE we hand off to detectors.
    out = subprocess.check_output(["du", "-s", "-B1", str(workdir)])
    bytes_used = int(out.split()[0])
    if bytes_used > MAX_CLONE_BYTES:
        shutil.rmtree(workdir, ignore_errors=True)
        raise RepoTooLarge(f"{repo_full_name} is {bytes_used} bytes, ceiling is {MAX_CLONE_BYTES}")

    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(workdir),
        check=True, capture_output=True,
    ).stdout.decode().strip()
    return sha


# ----- GitHub App auth (lifted from ai_github Lambda) -----------------------

def _credentials() -> dict:
    global _credentials_cache
    if _credentials_cache is None:
        v = _sm.get_secret_value(SecretId=GITHUB_APP_SECRET_ARN)
        _credentials_cache = json.loads(v["SecretString"])
    return _credentials_cache


def _installation_token(installation_id: int) -> str:
    cached = _token_cache.get(installation_id)
    if cached and cached[1] > time.time() + 60:
        return cached[0]

    c = _credentials()
    iss = c.get("client_id") or str(c.get("app_id") or "")
    now = int(time.time())
    app_jwt = pyjwt.encode(
        {"iat": now - 30, "exp": now + 600 - 30, "iss": iss},
        c["private_key"], algorithm="RS256",
    )

    import urllib.request
    req = urllib.request.Request(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        data=b"", method="POST",
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        body = json.loads(r.read())
    token = body["token"]
    import datetime as dt
    exp = dt.datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00")).timestamp()
    _token_cache[installation_id] = (token, exp)
    return token
```

Run the tests:

```bash
/Users/kkmookhey/venv/bin/pytest platform/lambda/ai_scanner/tests/test_scan_runner.py -v
# Expected: 3 passed
```

- [ ] **Step 3: Commit**

```bash
git add platform/lambda/ai_scanner/scan_runner.py platform/lambda/ai_scanner/tests/test_scan_runner.py
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "feat(platform): ai_scanner — ScanContext + clone_repo (with 4GB ceiling)"
```

---

### Task 5: Detector orchestration + transactional write path

**Files:**
- Create: `platform/lambda/ai_scanner/writer.py`
- Create: `platform/lambda/ai_scanner/tests/test_writer.py`

The writer wraps Aurora Data API in an explicit transaction. After all 8 detectors run + the correlator, the orchestrator hands their accumulated `DetectorResult` to `writer.commit_scan()` which:
1. Begins a transaction.
2. Upserts ai_assets (ON CONFLICT updates last_seen_at).
3. Upserts ai_relationships (ON CONFLICT updates last_seen_at).
4. Inserts findings.
5. Updates the ai_scans row with counts + status=success.
6. Commits — or rolls back on any error.

- [ ] **Step 1: Write the failing test**

```python
# platform/lambda/ai_scanner/tests/test_writer.py
"""Tests for the transactional writer."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def stub_env(monkeypatch):
    monkeypatch.setenv("DB_CLUSTER_ARN", "arn:db")
    monkeypatch.setenv("DB_SECRET_ARN", "arn:secret")
    monkeypatch.setenv("DB_NAME", "ciso_copilot")
    import boto3
    monkeypatch.setattr(boto3, "client", lambda _n, **_kw: MagicMock())


def _ctx():
    from scan_runner import ScanContext
    from pathlib import Path
    return ScanContext(
        scan_id="11111111-1111-1111-1111-111111111111",
        tenant_id="22222222-2222-2222-2222-222222222222",
        connection_id="33333333-3333-3333-3333-333333333333",
        repo_asset_id="44444444-4444-4444-4444-444444444444",
        repo_full_name="kk/foo", default_branch="main",
        head_commit_sha="abc123", installation_id=1,
        repo_workdir=Path("/tmp/x"),
    )


def test_commit_scan_runs_transactional_writes(monkeypatch):
    import writer
    calls: list[dict] = []
    tx_id = "txid-fake"

    def fake_call(method, **kw):
        calls.append({"method": method, **kw})
        if method == "begin_transaction":
            return {"transactionId": tx_id}
        return {"records": []}

    fake_client = MagicMock()
    fake_client.begin_transaction = lambda **kw: fake_call("begin_transaction", **kw)
    fake_client.commit_transaction = lambda **kw: fake_call("commit_transaction", **kw)
    fake_client.rollback_transaction = lambda **kw: fake_call("rollback_transaction", **kw)
    fake_client.execute_statement = lambda **kw: fake_call("execute_statement", **kw)
    fake_client.batch_execute_statement = lambda **kw: fake_call("batch_execute_statement", **kw)
    monkeypatch.setattr(writer, "_rds", fake_client)

    from detectors.base import AssetEmission, RelEmission, FindingEmission
    asset = AssetEmission(
        tenant_id="22222222-2222-2222-2222-222222222222",
        connection_id="33333333-3333-3333-3333-333333333333",
        asset_type="framework",
        name="langchain",
        source_repo_id="44444444-4444-4444-4444-444444444444",
        source_path="src/agent.py",
        attributes={"version": ">=0.3"},
        evidence_packet={"version": "0.1"},
        detector_id="ai.detectors.framework",
        detector_version="0.1.0",
    )
    writer.commit_scan(_ctx(), assets=[asset], relationships=[], findings=[])

    methods = [c["method"] for c in calls]
    assert methods[0]  == "begin_transaction"
    assert "execute_statement" in methods
    assert methods[-1] == "commit_transaction"


def test_commit_scan_rolls_back_on_error(monkeypatch):
    import writer

    fake_client = MagicMock()
    fake_client.begin_transaction = lambda **kw: {"transactionId": "tx"}
    fake_client.commit_transaction = MagicMock()
    fake_client.rollback_transaction = MagicMock()
    def fake_execute(**kw):
        raise RuntimeError("boom")
    fake_client.execute_statement = fake_execute
    fake_client.batch_execute_statement = fake_execute
    monkeypatch.setattr(writer, "_rds", fake_client)

    from detectors.base import AssetEmission
    asset = AssetEmission(
        tenant_id="t", connection_id="c", asset_type="framework", name="x",
        source_repo_id="r", source_path="/p", attributes={}, evidence_packet={},
        detector_id="d", detector_version="0.1",
    )

    with pytest.raises(RuntimeError, match="boom"):
        writer.commit_scan(_ctx(), assets=[asset], relationships=[], findings=[])

    fake_client.rollback_transaction.assert_called_once()
    fake_client.commit_transaction.assert_not_called()
```

Run, confirm failure:

```bash
/Users/kkmookhey/venv/bin/pytest platform/lambda/ai_scanner/tests/test_writer.py -v
# Expected: ImportError on writer or detectors.base
```

- [ ] **Step 2: Implement `detectors/base.py` first (test depends on it)**

```python
# platform/lambda/ai_scanner/detectors/base.py
"""Detector protocol + emission dataclasses."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class AssetEmission:
    tenant_id:        str
    connection_id:    str
    asset_type:       str
    name:             str
    source_repo_id:   str | None
    source_path:      str | None
    attributes:       dict[str, Any]
    evidence_packet:  dict[str, Any]
    detector_id:      str
    detector_version: str
    # caller assigns id at write time so relationships can reference it
    id:               str | None = None


@dataclass(frozen=True)
class RelEmission:
    tenant_id:           str
    source_asset_ref:    str   # placeholder key used to resolve to source_asset_id at write time
    target_asset_ref:    str
    relationship_type:   str
    attributes:          dict[str, Any]
    evidence_packet:     dict[str, Any]
    detector_id:         str
    detector_version:    str


@dataclass(frozen=True)
class FindingEmission:
    tenant_id:        str
    finding_type:     str          # e.g. "unapproved_provider"
    severity:         str          # "critical" | "high" | "medium" | "low" | "info"
    title:            str
    description:      str
    subject_type:     str          # "ai_asset" | "ai_relationship"
    subject_ref:      str
    evidence_packet:  dict[str, Any]
    confidence:       str          # "high" | "medium" | "low"


@dataclass(frozen=True)
class DetectorResult:
    assets:        list[AssetEmission] = field(default_factory=list)
    relationships: list[RelEmission]    = field(default_factory=list)
    findings:      list[FindingEmission] = field(default_factory=list)


class Detector(Protocol):
    detector_id:      str
    detector_version: str

    def detect(self, ctx: "Any") -> DetectorResult: ...
```

- [ ] **Step 3: Implement `writer.py`**

```python
# platform/lambda/ai_scanner/writer.py
"""Transactional writes to ai_assets / ai_relationships / findings / ai_scans."""
from __future__ import annotations

import json
import os
import uuid

import boto3

from detectors.base import AssetEmission, RelEmission, FindingEmission

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

_rds = boto3.client("rds-data")


def commit_scan(ctx, assets: list[AssetEmission],
                relationships: list[RelEmission],
                findings:      list[FindingEmission]) -> None:
    """Run all writes inside one transaction. Raises on failure (callers handle)."""
    tx = _rds.begin_transaction(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
    )["transactionId"]
    try:
        # Map asset emission → assigned UUID for later relationship resolution
        asset_id_by_ref: dict[str, str] = {}
        for a in assets:
            assigned_id = a.id or str(uuid.uuid4())
            asset_id_by_ref[_asset_ref(a)] = assigned_id
            _upsert_asset(tx, a, assigned_id, scan_id=ctx.scan_id)

        for r in relationships:
            source_id = asset_id_by_ref.get(r.source_asset_ref)
            target_id = asset_id_by_ref.get(r.target_asset_ref)
            if not source_id or not target_id:
                # Detector emitted a relationship pointing at an asset that
                # wasn't emitted in the same scan — skip silently rather than
                # rolling back. This is benign for cross-repo edges.
                continue
            _upsert_relationship(tx, r, source_id, target_id, scan_id=ctx.scan_id)

        for f in findings:
            subject_id = asset_id_by_ref.get(f.subject_ref) or f.subject_ref
            _insert_finding(tx, f, subject_id, scan_id=ctx.scan_id, ctx=ctx)

        _update_scan(tx, ctx, len(assets), len(relationships), len(findings),
                     status="success")

        _rds.commit_transaction(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, transactionId=tx,
        )
    except Exception:
        _rds.rollback_transaction(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, transactionId=tx,
        )
        raise


def mark_scan_failed(ctx, error_message: str) -> None:
    """Update an in-progress scan to status=failed (used when clone_repo errors)."""
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

def _asset_ref(a: AssetEmission) -> str:
    """Stable key for asset_id_by_ref before the row has an id."""
    return f"{a.asset_type}::{a.source_repo_id or ''}::{a.source_path or ''}::{a.name}"


def _upsert_asset(tx: str, a: AssetEmission, assigned_id: str, scan_id: str) -> None:
    _rds.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        transactionId=tx,
        sql=(
            "INSERT INTO ai_assets "
            "  (id, tenant_id, connection_id, asset_type, name, source_repo_id, "
            "   source_path, attributes, evidence_packet, detector_id, detector_version, scan_id) "
            "VALUES (CAST(:id AS UUID), CAST(:tid AS UUID), CAST(:cid AS UUID), "
            "        :atype, :name, "
            "        CASE WHEN :repo='' THEN NULL ELSE CAST(:repo AS UUID) END, "
            "        :spath, CAST(:attrs AS JSONB), CAST(:ev AS JSONB), "
            "        :did, :dver, CAST(:sid AS UUID)) "
            "ON CONFLICT (tenant_id, asset_type, source_repo_id, source_path, name) "
            "  DO UPDATE SET last_seen_at=NOW(), evidence_packet=EXCLUDED.evidence_packet, "
            "                attributes=EXCLUDED.attributes"
        ),
        parameters=[
            {"name": "id",    "value": {"stringValue": assigned_id}},
            {"name": "tid",   "value": {"stringValue": a.tenant_id}},
            {"name": "cid",   "value": {"stringValue": a.connection_id}},
            {"name": "atype", "value": {"stringValue": a.asset_type}},
            {"name": "name",  "value": {"stringValue": a.name}},
            {"name": "repo",  "value": {"stringValue": a.source_repo_id or ""}},
            {"name": "spath", "value": {"stringValue": a.source_path or ""}},
            {"name": "attrs", "value": {"stringValue": json.dumps(a.attributes)}},
            {"name": "ev",    "value": {"stringValue": json.dumps(a.evidence_packet)}},
            {"name": "did",   "value": {"stringValue": a.detector_id}},
            {"name": "dver",  "value": {"stringValue": a.detector_version}},
            {"name": "sid",   "value": {"stringValue": scan_id}},
        ],
    )


def _upsert_relationship(tx: str, r: RelEmission, source_id: str, target_id: str,
                         scan_id: str) -> None:
    _rds.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        transactionId=tx,
        sql=(
            "INSERT INTO ai_relationships "
            "  (id, tenant_id, source_asset_id, target_asset_id, relationship_type, "
            "   attributes, evidence_packet, detector_id, detector_version, scan_id) "
            "VALUES (CAST(:rid AS UUID), CAST(:tid AS UUID), CAST(:src AS UUID), "
            "        CAST(:tgt AS UUID), :rtype, CAST(:attrs AS JSONB), CAST(:ev AS JSONB), "
            "        :did, :dver, CAST(:sid AS UUID)) "
            "ON CONFLICT (source_asset_id, target_asset_id, relationship_type) "
            "  DO UPDATE SET last_seen_at=NOW(), evidence_packet=EXCLUDED.evidence_packet, "
            "                attributes=EXCLUDED.attributes"
        ),
        parameters=[
            {"name": "rid",   "value": {"stringValue": str(uuid.uuid4())}},
            {"name": "tid",   "value": {"stringValue": r.tenant_id}},
            {"name": "src",   "value": {"stringValue": source_id}},
            {"name": "tgt",   "value": {"stringValue": target_id}},
            {"name": "rtype", "value": {"stringValue": r.relationship_type}},
            {"name": "attrs", "value": {"stringValue": json.dumps(r.attributes)}},
            {"name": "ev",    "value": {"stringValue": json.dumps(r.evidence_packet)}},
            {"name": "did",   "value": {"stringValue": r.detector_id}},
            {"name": "dver",  "value": {"stringValue": r.detector_version}},
            {"name": "sid",   "value": {"stringValue": scan_id}},
        ],
    )


def _insert_finding(tx: str, f: FindingEmission, subject_id: str, scan_id: str, ctx) -> None:
    """Insert into the existing findings table with category='ai'."""
    fid = str(uuid.uuid4())
    _rds.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        transactionId=tx,
        sql=(
            "INSERT INTO findings "
            "  (finding_id, tenant_id, conn_id, scan_id, check_id, title, description, "
            "   severity, status, resource_arn, resource_type, region, domain, frameworks, "
            "   remediation, first_seen, last_seen, evidence_packet) "
            "VALUES (CAST(:fid AS UUID), CAST(:tid AS UUID), CAST(:conn AS UUID), "
            "        CAST(:sid AS UUID), :ftype, :title, :desc, :sev, 'fail', :subj, "
            "        :stype, NULL, 'ai', '{}'::jsonb, NULL, NOW(), NOW(), CAST(:ev AS JSONB))"
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
            {"name": "subj",  "value": {"stringValue": subject_id}},
            {"name": "stype", "value": {"stringValue": f.subject_type}},
            {"name": "ev",    "value": {"stringValue": json.dumps(f.evidence_packet)}},
        ],
    )


def _update_scan(tx: str, ctx, asset_count: int, rel_count: int, finding_count: int,
                 status: str) -> None:
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
            {"name": "ac",  "value": {"longValue":   asset_count}},
            {"name": "rc",  "value": {"longValue":   rel_count}},
            {"name": "fc",  "value": {"longValue":   finding_count}},
            {"name": "sv",  "value": {"stringValue": ctx.scanner_version}},
            {"name": "sid", "value": {"stringValue": ctx.scan_id}},
        ],
    )
```

**Note on UUIDs:** Aurora doesn't have pgcrypto, so we generate all UUIDs in Python (matches Slice 1a `004_phase_ai.sql`). The `:rid` parameter above is pre-bound to `str(uuid.uuid4())`. Do not introduce a SQL function call like `gen_random_uuid()` — it will fail at runtime.

Run the tests:

```bash
/Users/kkmookhey/venv/bin/pytest platform/lambda/ai_scanner/tests/test_writer.py -v
# Expected: 2 passed
```

- [ ] **Step 4: Commit**

```bash
git add platform/lambda/ai_scanner/detectors/base.py platform/lambda/ai_scanner/detectors/__init__.py platform/lambda/ai_scanner/writer.py platform/lambda/ai_scanner/tests/test_writer.py
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "feat(platform): ai_scanner — detector base + transactional writer"
```

(Create `detectors/__init__.py` as empty before the commit if not already present.)

---

## Phase C — Detectors (9 tasks)

Each detector is a separate file under `detectors/` with:
- `detector_id: str` (module-level constant matching `ai.detectors.<name>`)
- `detector_version: str` (start at `"0.1.0"`)
- `detect(ctx) -> DetectorResult` (pure function)

Each has:
- **Goldens** under `tests/fixtures/<detector_name>/`: small synthetic repo + expected JSON output.
- **One test file** `tests/test_detectors.py` (shared) OR per-detector test files. We use a shared `test_detectors.py` with parametrized tests over fixture directories — keeps the test count down and the pattern uniform.

**Common helper** used by every detector — file walker:

```python
# Inside each detector module, OR factor into detectors/_walk.py
import subprocess

def ripgrep(workdir: Path, pattern: str, *, types: list[str] | None = None,
            ignore_case: bool = False) -> list[tuple[Path, int, str]]:
    """Run ripgrep and return (path, line_number, line) tuples."""
    cmd = ["rg", "--no-heading", "--line-number", "--no-color"]
    if ignore_case:
        cmd.append("-i")
    if types:
        for t in types:
            cmd.extend(["-t", t])
    cmd.extend(["--", pattern, str(workdir)])
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode == 1:  # no matches
        return []
    if out.returncode != 0:
        raise RuntimeError(f"ripgrep failed: {out.stderr}")
    results = []
    for line in out.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        path, lineno_str, content = parts
        results.append((Path(path), int(lineno_str), content))
    return results
```

Place that helper in `platform/lambda/ai_scanner/detectors/_walk.py` (new file).

### Task 6: Detector base interface + evidence builder

**Files:**
- Create: `platform/lambda/ai_scanner/evidence.py`
- Create: `platform/lambda/ai_scanner/detectors/_walk.py`
- Create: `platform/lambda/ai_scanner/tests/test_evidence.py`

The `evidence.py` builds packets per the open spec in `docs/superpowers/specs/2026-05-18-ai-security-slice-1-design.md` §7.

- [ ] **Step 1: Write the test**

```python
# platform/lambda/ai_scanner/tests/test_evidence.py
"""Tests for the EvidencePacket builder."""
from __future__ import annotations

import re

import pytest


def test_build_packet_shape():
    import evidence
    p = evidence.build(
        detector_id="ai.detectors.framework", detector_version="0.1.0",
        subject_kind="ai_asset", subject_type="framework", subject_name="langchain",
        source_events=[{"kind": "file", "repo": "kk/foo", "commit_sha": "abc123",
                        "path": "/app/agent.py", "snippet_lines": [12, 12],
                        "snippet": "from langchain import LLMChain"}],
        reasoning_chain=["matched import on line 12"],
        confidence="high",
    )
    assert p["version"] == "0.1"
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                        p["packet_id"])
    assert p["produced_at"].endswith("Z")
    assert p["detector"]["id"] == "ai.detectors.framework"
    assert p["subject"]["kind"] == "ai_asset"
    assert p["subject"]["type"] == "framework"
    assert p["subject"]["name"] == "langchain"
    assert p["source_events"][0]["path"] == "/app/agent.py"
    assert p["model"] is None
    assert p["signature"] is None
    assert p["confidence"] == "high"
```

Run, confirm failure.

- [ ] **Step 2: Implement `evidence.py`**

```python
# platform/lambda/ai_scanner/evidence.py
"""Build Trust Evidence Packets per the open Slice-1 spec.

Schema: docs/superpowers/specs/2026-05-18-ai-security-slice-1-design.md §7.
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any


def build(*,
          detector_id: str,
          detector_version: str,
          subject_kind: str,           # "ai_asset" | "ai_relationship" | "finding"
          subject_type: str,           # asset_type / relationship_type / finding_type
          subject_name: str,
          source_events: list[dict[str, Any]],
          reasoning_chain: list[str],
          confidence: str,             # "high" | "medium" | "low"
          subject_id: str | None = None,
          graph_trace: list[Any] | None = None,
          ) -> dict[str, Any]:
    return {
        "version":           "0.1",
        "packet_id":         str(uuid.uuid4()),
        "produced_at":       dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "detector":          {"id": detector_id, "version": detector_version},
        "subject":           {"kind": subject_kind, "id": subject_id,
                              "type": subject_type, "name": subject_name},
        "source_events":     source_events,
        "graph_trace":       graph_trace or [],
        "reasoning_chain":   reasoning_chain,
        "model":             None,     # deterministic detectors — no LLM
        "confidence":        confidence,
        "signature":         None,     # KMS signing deferred
    }
```

- [ ] **Step 3: Implement `_walk.py`** (the ripgrep helper from above, copy verbatim into the file)

- [ ] **Step 4: Run tests; commit**

```bash
/Users/kkmookhey/venv/bin/pytest platform/lambda/ai_scanner/tests/test_evidence.py -v
# Expected: 1 passed

git add platform/lambda/ai_scanner/evidence.py platform/lambda/ai_scanner/detectors/_walk.py platform/lambda/ai_scanner/tests/test_evidence.py
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "feat(platform): ai_scanner — evidence packet builder + ripgrep helper"
```

---

### Tasks 7–14: The eight detectors

**Shared fixture + golden test infrastructure** (do this once before Task 7).

Create `platform/lambda/ai_scanner/tests/test_detectors.py`:

```python
# platform/lambda/ai_scanner/tests/test_detectors.py
"""Per-detector golden-file tests.

For each fixture under tests/fixtures/<detector_name>/<scenario>/:
  - repo/    a synthetic repo (small set of files)
  - expected.json   the emissions the detector should produce
"""
from __future__ import annotations

import importlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"


def _scenarios():
    """Yield (detector_module, fixture_dir) pairs from every fixture."""
    for det_dir in FIXTURE_ROOT.iterdir():
        if not det_dir.is_dir():
            continue
        for scenario in det_dir.iterdir():
            if not scenario.is_dir():
                continue
            if not (scenario / "repo").is_dir():
                continue
            if not (scenario / "expected.json").exists():
                continue
            yield (det_dir.name, scenario)


def _make_ctx(repo_dir: Path):
    """Build a minimal ScanContext for fixture runs (no real GitHub)."""
    from scan_runner import ScanContext
    return ScanContext(
        scan_id="11111111-1111-1111-1111-111111111111",
        tenant_id="22222222-2222-2222-2222-222222222222",
        connection_id="33333333-3333-3333-3333-333333333333",
        repo_asset_id="44444444-4444-4444-4444-444444444444",
        repo_full_name="fixture/repo",
        default_branch="main",
        head_commit_sha="fixture-sha",
        installation_id=0,
        repo_workdir=repo_dir,
    )


def _normalise(result):
    """Convert DetectorResult to a stable dict for golden comparison."""
    def strip_dynamic(p):
        # Strip packet_id + produced_at — these are non-deterministic.
        p = {**p}
        p.pop("packet_id", None)
        p.pop("produced_at", None)
        if "subject" in p:
            p["subject"] = {**p["subject"]}
            p["subject"].pop("id", None)
        return p
    return {
        "assets": [
            {**asdict(a), "evidence_packet": strip_dynamic(a.evidence_packet)}
            for a in sorted(result.assets, key=lambda x: (x.asset_type, x.name, x.source_path or ""))
        ],
        "relationships": [
            {**asdict(r), "evidence_packet": strip_dynamic(r.evidence_packet)}
            for r in sorted(result.relationships, key=lambda x: (x.relationship_type, x.source_asset_ref, x.target_asset_ref))
        ],
        "findings": [
            {**asdict(f), "evidence_packet": strip_dynamic(f.evidence_packet)}
            for f in sorted(result.findings, key=lambda x: (x.finding_type, x.subject_ref))
        ],
    }


@pytest.mark.parametrize("detector_name,scenario", list(_scenarios()),
                         ids=lambda v: v.name if isinstance(v, Path) else v)
def test_detector_golden(detector_name, scenario):
    module = importlib.import_module(f"detectors.{detector_name}")
    repo_dir = scenario / "repo"
    ctx = _make_ctx(repo_dir)
    result = module.detect(ctx)
    actual = _normalise(result)
    expected = json.loads((scenario / "expected.json").read_text())
    assert actual == expected, (
        f"Detector {detector_name} emission diverged on fixture {scenario.name}.\n"
        f"Actual:\n{json.dumps(actual, indent=2)}\n\nExpected:\n{json.dumps(expected, indent=2)}"
    )
```

Each detector task below creates its fixtures + module. The shared test discovers them automatically.

---

### Task 7: `framework` detector

**Files:**
- Create: `platform/lambda/ai_scanner/detectors/framework.py`
- Create: `platform/lambda/ai_scanner/tests/fixtures/framework/langchain_in_repo/repo/app/agent.py`
- Create: `platform/lambda/ai_scanner/tests/fixtures/framework/langchain_in_repo/expected.json`
- Create: `platform/lambda/ai_scanner/tests/fixtures/framework/no_framework/repo/main.py`
- Create: `platform/lambda/ai_scanner/tests/fixtures/framework/no_framework/expected.json`

**Behavior:** Detect imports of `langchain`, `langgraph`, `llama_index`, `crewai`, `autogen`, `semantic_kernel`, `dspy`. Each unique framework → one `framework` asset + one `repository → uses → framework` relationship. No findings.

- [ ] **Step 1: Write the fixtures**

`tests/fixtures/framework/langchain_in_repo/repo/app/agent.py`:

```python
from langchain.chains import LLMChain
from langchain.llms import OpenAI

chain = LLMChain(llm=OpenAI(), prompt="hello")
```

`tests/fixtures/framework/langchain_in_repo/expected.json`:

```json
{
  "assets": [
    {
      "tenant_id": "22222222-2222-2222-2222-222222222222",
      "connection_id": "33333333-3333-3333-3333-333333333333",
      "asset_type": "framework",
      "name": "langchain",
      "source_repo_id": "44444444-4444-4444-4444-444444444444",
      "source_path": "app/agent.py",
      "attributes": {"imports_seen": 2},
      "evidence_packet": {
        "version": "0.1",
        "detector": {"id": "ai.detectors.framework", "version": "0.1.0"},
        "subject": {"kind": "ai_asset", "type": "framework", "name": "langchain"},
        "source_events": [
          {"kind": "file", "repo": "fixture/repo", "commit_sha": "fixture-sha",
           "path": "app/agent.py", "snippet_lines": [1, 1],
           "snippet": "from langchain.chains import LLMChain"}
        ],
        "graph_trace": [],
        "reasoning_chain": ["matched langchain import on app/agent.py:1"],
        "model": null,
        "confidence": "high",
        "signature": null
      },
      "detector_id": "ai.detectors.framework",
      "detector_version": "0.1.0",
      "id": null
    }
  ],
  "relationships": [
    {
      "tenant_id": "22222222-2222-2222-2222-222222222222",
      "source_asset_ref": "repository::::44444444-4444-4444-4444-444444444444",
      "target_asset_ref": "framework::44444444-4444-4444-4444-444444444444::app/agent.py::langchain",
      "relationship_type": "uses",
      "attributes": {},
      "evidence_packet": {
        "version": "0.1",
        "detector": {"id": "ai.detectors.framework", "version": "0.1.0"},
        "subject": {"kind": "ai_relationship", "type": "uses", "name": "repo→uses→langchain"},
        "source_events": [],
        "graph_trace": [],
        "reasoning_chain": ["framework detected in repo"],
        "model": null,
        "confidence": "high",
        "signature": null
      },
      "detector_id": "ai.detectors.framework",
      "detector_version": "0.1.0"
    }
  ],
  "findings": []
}
```

`tests/fixtures/framework/no_framework/repo/main.py`:

```python
print("hello world")
```

`tests/fixtures/framework/no_framework/expected.json`:

```json
{"assets": [], "relationships": [], "findings": []}
```

- [ ] **Step 2: Run the test (expect failure — detector doesn't exist yet)**

```bash
/Users/kkmookhey/venv/bin/pytest platform/lambda/ai_scanner/tests/test_detectors.py -k framework -v
# Expected: ImportError on detectors.framework
```

- [ ] **Step 3: Implement the detector**

```python
# platform/lambda/ai_scanner/detectors/framework.py
"""Detect AI-framework imports (langchain, llama_index, crewai, autogen,
semantic_kernel, dspy, langgraph)."""
from __future__ import annotations

from pathlib import Path

from detectors._walk import ripgrep
from detectors.base import AssetEmission, RelEmission, DetectorResult
import evidence as ev

detector_id      = "ai.detectors.framework"
detector_version = "0.1.0"

FRAMEWORKS = [
    "langchain", "langgraph", "llama_index", "llama_cpp", "crewai",
    "autogen", "semantic_kernel", "dspy",
]


def detect(ctx) -> DetectorResult:
    assets: list[AssetEmission] = []
    rels:   list[RelEmission] = []

    # repo asset ref — emits the synthetic repo node for relationship binding
    repo_ref = f"repository::::{ctx.repo_asset_id}"

    for fw in FRAMEWORKS:
        # Match `from langchain ...` or `import langchain` (top-of-line word boundary)
        pattern = rf"^\s*(from|import)\s+{fw}(\b|\.)"
        matches = ripgrep(ctx.repo_workdir, pattern, types=["py"])
        if not matches:
            continue

        # Use the first match as the canonical evidence location.
        first_path, first_line, first_snippet = matches[0]
        rel_path = str(first_path.relative_to(ctx.repo_workdir))

        packet = ev.build(
            detector_id=detector_id, detector_version=detector_version,
            subject_kind="ai_asset", subject_type="framework", subject_name=fw,
            source_events=[{
                "kind": "file",
                "repo": ctx.repo_full_name,
                "commit_sha": ctx.head_commit_sha,
                "path": rel_path,
                "snippet_lines": [first_line, first_line],
                "snippet": first_snippet,
            }],
            reasoning_chain=[f"matched {fw} import on {rel_path}:{first_line}"],
            confidence="high",
        )
        assets.append(AssetEmission(
            tenant_id=ctx.tenant_id,
            connection_id=ctx.connection_id,
            asset_type="framework",
            name=fw,
            source_repo_id=ctx.repo_asset_id,
            source_path=rel_path,
            attributes={"imports_seen": len(matches)},
            evidence_packet=packet,
            detector_id=detector_id,
            detector_version=detector_version,
        ))

        rel_packet = ev.build(
            detector_id=detector_id, detector_version=detector_version,
            subject_kind="ai_relationship", subject_type="uses",
            subject_name=f"repo→uses→{fw}",
            source_events=[],
            reasoning_chain=["framework detected in repo"],
            confidence="high",
        )
        rels.append(RelEmission(
            tenant_id=ctx.tenant_id,
            source_asset_ref=repo_ref,
            target_asset_ref=f"framework::{ctx.repo_asset_id}::{rel_path}::{fw}",
            relationship_type="uses",
            attributes={},
            evidence_packet=rel_packet,
            detector_id=detector_id,
            detector_version=detector_version,
        ))

    return DetectorResult(assets=assets, relationships=rels, findings=[])
```

- [ ] **Step 4: Run the test until green; fixture authoring iteration is normal**

```bash
/Users/kkmookhey/venv/bin/pytest platform/lambda/ai_scanner/tests/test_detectors.py -k framework -v
```

If the test fails because the expected.json doesn't exactly match what the detector emits (whitespace, snippet capture, etc.), debug by printing the actual emission and update `expected.json` to match — the test is a snapshot, but the implementation is the source of truth for the snapshot.

Expected: 2 passed (langchain_in_repo + no_framework).

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/ai_scanner/detectors/framework.py platform/lambda/ai_scanner/tests/fixtures/framework/ platform/lambda/ai_scanner/tests/test_detectors.py
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "feat(platform): ai_scanner — framework detector + fixtures"
```

---

### Task 8: `model_usage` detector

**Files:**
- Create: `platform/lambda/ai_scanner/detectors/model_usage.py`
- Create: `platform/lambda/ai_scanner/tests/fixtures/model_usage/openai_calls/repo/app/llm.py`
- Create: `platform/lambda/ai_scanner/tests/fixtures/model_usage/openai_calls/expected.json`
- Create: `platform/lambda/ai_scanner/tests/fixtures/model_usage/anthropic_calls/repo/app/claude.py`
- Create: `platform/lambda/ai_scanner/tests/fixtures/model_usage/anthropic_calls/expected.json`
- Create: `platform/lambda/ai_scanner/tests/fixtures/model_usage/bedrock_calls/repo/app/bedrock.py`
- Create: `platform/lambda/ai_scanner/tests/fixtures/model_usage/bedrock_calls/expected.json`

**Behavior:** Detect SDK calls to commercial LLM providers. One `model` asset per (provider, model_id) tuple. `repository → calls → model` edge per asset. **No findings** in this slice — the `unapproved_provider` finding is deferred (the allowlist needs to be in tenant config, which we don't have yet).

**Patterns:**
- `openai.ChatCompletion.create(model="gpt-4o-mini", ...)` → provider=openai, model=gpt-4o-mini
- `OpenAI().chat.completions.create(model="gpt-4o", ...)` → provider=openai, model=gpt-4o
- `Anthropic().messages.create(model="claude-sonnet-4-6", ...)` → provider=anthropic, model=claude-sonnet-4-6
- `bedrock.invoke_model(modelId="anthropic.claude-...", ...)` → provider=bedrock, model=anthropic.claude-…

Detection is regex-based: `\bmodel\s*=\s*["']([^"']+)["']` near a known SDK call. Use `ripgrep` with `-A 5` (5 lines of context) to capture model strings near SDK calls.

- [ ] **Step 1: Write the fixtures**

`tests/fixtures/model_usage/openai_calls/repo/app/llm.py`:

```python
from openai import OpenAI

client = OpenAI()
resp = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "hi"}],
)
```

`expected.json` (just the openai/gpt-4o-mini entry; full structure mirrors the framework detector):

```json
{
  "assets": [
    {
      "tenant_id": "22222222-2222-2222-2222-222222222222",
      "connection_id": "33333333-3333-3333-3333-333333333333",
      "asset_type": "model",
      "name": "openai/gpt-4o-mini",
      "source_repo_id": "44444444-4444-4444-4444-444444444444",
      "source_path": "app/llm.py",
      "attributes": {"provider": "openai", "model_id": "gpt-4o-mini"},
      "evidence_packet": {
        "version": "0.1",
        "detector": {"id": "ai.detectors.model_usage", "version": "0.1.0"},
        "subject": {"kind": "ai_asset", "type": "model", "name": "openai/gpt-4o-mini"},
        "source_events": [
          {"kind": "file", "repo": "fixture/repo", "commit_sha": "fixture-sha",
           "path": "app/llm.py", "snippet_lines": [5, 5],
           "snippet": "    model=\"gpt-4o-mini\","}
        ],
        "graph_trace": [],
        "reasoning_chain": ["matched model=\"gpt-4o-mini\" in openai SDK call at app/llm.py:5"],
        "model": null,
        "confidence": "high",
        "signature": null
      },
      "detector_id": "ai.detectors.model_usage",
      "detector_version": "0.1.0",
      "id": null
    }
  ],
  "relationships": [
    {
      "tenant_id": "22222222-2222-2222-2222-222222222222",
      "source_asset_ref": "repository::::44444444-4444-4444-4444-444444444444",
      "target_asset_ref": "model::44444444-4444-4444-4444-444444444444::app/llm.py::openai/gpt-4o-mini",
      "relationship_type": "calls",
      "attributes": {"provider": "openai"},
      "evidence_packet": {
        "version": "0.1",
        "detector": {"id": "ai.detectors.model_usage", "version": "0.1.0"},
        "subject": {"kind": "ai_relationship", "type": "calls", "name": "repo→calls→openai/gpt-4o-mini"},
        "source_events": [],
        "graph_trace": [],
        "reasoning_chain": ["model use detected in repo"],
        "model": null,
        "confidence": "high",
        "signature": null
      },
      "detector_id": "ai.detectors.model_usage",
      "detector_version": "0.1.0"
    }
  ],
  "findings": []
}
```

Repeat the same pattern for `anthropic_calls` (`claude.py` calling `anthropic.Anthropic().messages.create(model="claude-sonnet-4-6", ...)`) and `bedrock_calls` (`bedrock.py` calling `boto3.client("bedrock-runtime").invoke_model(modelId="anthropic.claude-...", ...)`).

- [ ] **Step 2: Implement `detectors/model_usage.py`**

```python
# platform/lambda/ai_scanner/detectors/model_usage.py
"""Detect calls to commercial LLM SDKs (OpenAI, Anthropic, Bedrock).

Strategy: ripgrep for `model="..."` strings, then check the same file for
a known SDK import. Conservative — only emits when both signals coincide.
"""
from __future__ import annotations

import re
from pathlib import Path

from detectors._walk import ripgrep
from detectors.base import AssetEmission, RelEmission, DetectorResult
import evidence as ev

detector_id      = "ai.detectors.model_usage"
detector_version = "0.1.0"

# Map: SDK import substring → provider tag, model-kwarg name
SDKS = [
    # (import_marker, provider, model_kwarg)
    ("from openai",      "openai",    "model"),
    ("import openai",    "openai",    "model"),
    ("from anthropic",   "anthropic", "model"),
    ("import anthropic", "anthropic", "model"),
    ("bedrock-runtime",  "bedrock",   "modelId"),
    ("bedrock_runtime",  "bedrock",   "modelId"),
]


def detect(ctx) -> DetectorResult:
    assets: list[AssetEmission] = []
    rels:   list[RelEmission] = []
    seen: set[tuple[str, str, str]] = set()  # (path, provider, model)

    py_files = list(ctx.repo_workdir.rglob("*.py"))
    for f in py_files:
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        rel_path = str(f.relative_to(ctx.repo_workdir))

        for marker, provider, model_kwarg in SDKS:
            if marker not in text:
                continue
            pat = re.compile(rf'\b{model_kwarg}\s*=\s*["\']([^"\']+)["\']')
            for m in pat.finditer(text):
                model_id = m.group(1)
                key = (rel_path, provider, model_id)
                if key in seen:
                    continue
                seen.add(key)

                # Find the line number
                line_no = text[:m.start()].count("\n") + 1
                snippet = text.splitlines()[line_no - 1] if line_no <= text.count("\n") + 1 else ""

                packet = ev.build(
                    detector_id=detector_id, detector_version=detector_version,
                    subject_kind="ai_asset", subject_type="model",
                    subject_name=f"{provider}/{model_id}",
                    source_events=[{
                        "kind": "file", "repo": ctx.repo_full_name,
                        "commit_sha": ctx.head_commit_sha,
                        "path": rel_path, "snippet_lines": [line_no, line_no],
                        "snippet": snippet,
                    }],
                    reasoning_chain=[
                        f"matched {model_kwarg}=\"{model_id}\" in {provider} SDK call at {rel_path}:{line_no}"
                    ],
                    confidence="high",
                )
                assets.append(AssetEmission(
                    tenant_id=ctx.tenant_id, connection_id=ctx.connection_id,
                    asset_type="model", name=f"{provider}/{model_id}",
                    source_repo_id=ctx.repo_asset_id, source_path=rel_path,
                    attributes={"provider": provider, "model_id": model_id},
                    evidence_packet=packet,
                    detector_id=detector_id, detector_version=detector_version,
                ))

                rel_packet = ev.build(
                    detector_id=detector_id, detector_version=detector_version,
                    subject_kind="ai_relationship", subject_type="calls",
                    subject_name=f"repo→calls→{provider}/{model_id}",
                    source_events=[],
                    reasoning_chain=["model use detected in repo"],
                    confidence="high",
                )
                rels.append(RelEmission(
                    tenant_id=ctx.tenant_id,
                    source_asset_ref=f"repository::::{ctx.repo_asset_id}",
                    target_asset_ref=f"model::{ctx.repo_asset_id}::{rel_path}::{provider}/{model_id}",
                    relationship_type="calls",
                    attributes={"provider": provider},
                    evidence_packet=rel_packet,
                    detector_id=detector_id, detector_version=detector_version,
                ))

    return DetectorResult(assets=assets, relationships=rels, findings=[])
```

- [ ] **Step 3: Run + iterate fixtures until green; commit**

```bash
/Users/kkmookhey/venv/bin/pytest platform/lambda/ai_scanner/tests/test_detectors.py -k model_usage -v
# Expected: 3 passed

git add platform/lambda/ai_scanner/detectors/model_usage.py platform/lambda/ai_scanner/tests/fixtures/model_usage/
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "feat(platform): ai_scanner — model_usage detector (OpenAI/Anthropic/Bedrock) + fixtures"
```

---

### Task 9: `mcp_server` detector

**Files:**
- Create: `platform/lambda/ai_scanner/detectors/mcp_server.py`
- Create: `platform/lambda/ai_scanner/tests/fixtures/mcp_server/python_server/repo/mcp/server.py`
- Create: `platform/lambda/ai_scanner/tests/fixtures/mcp_server/python_server/expected.json`
- Create: `platform/lambda/ai_scanner/tests/fixtures/mcp_server/mcp_json/repo/mcp.json`
- Create: `platform/lambda/ai_scanner/tests/fixtures/mcp_server/mcp_json/expected.json`

**Behavior:** Detect MCP server declarations. Two signals:
1. Python: `from mcp.server import Server` + `@server.list_tools()` decorators
2. Config: `mcp.json` or `claude_desktop_config.json` files

Each detected MCP server emits one `mcp_server` asset + one `tool` asset per declared tool + `repository→deploys→mcp_server` + `mcp_server→invokes→tool` edges. **Finding:** `mcp_with_broad_perms` (HIGH severity) if any declared tool implies write scope (e.g., tool name contains `create_`, `delete_`, `write_`, `update_`).

- [ ] **Step 1: Fixtures**

`tests/fixtures/mcp_server/python_server/repo/mcp/server.py`:

```python
from mcp.server import Server

server = Server("kk-tools")

@server.list_tools()
async def list_tools():
    return [
        {"name": "read_file", "description": "Read a file"},
        {"name": "create_pr",  "description": "Open a pull request"},
    ]
```

`expected.json` for `python_server`: 1 mcp_server asset + 2 tool assets + 1 deploys edge + 2 invokes edges + 1 mcp_with_broad_perms finding (because `create_pr` matches the write-scope heuristic).

`tests/fixtures/mcp_server/mcp_json/repo/mcp.json`:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["@modelcontextprotocol/server-filesystem", "/tmp"]
    }
  }
}
```

`expected.json` for `mcp_json`: 1 mcp_server asset (name=filesystem) + 1 deploys edge + 0 tools (since no tool list in this config file) + 0 findings.

- [ ] **Step 2: Implement `mcp_server.py`** (the implementer fleshes this out from the patterns above — same shape as `framework.py`, with file-pattern detection for `mcp.json`/`claude_desktop_config.json` + AST/regex detection for `@server.list_tools()` decorators).

Use `ctx.repo_workdir.rglob("mcp.json")` and `rglob("claude_desktop_config.json")` for config-file search. For Python decorator detection, use `ast.parse` on each `.py` file and walk for `ast.FunctionDef` nodes with decorators referencing `list_tools`.

- [ ] **Step 3: Run, iterate, commit**

```bash
/Users/kkmookhey/venv/bin/pytest platform/lambda/ai_scanner/tests/test_detectors.py -k mcp_server -v
# Expected: 2 passed

git add platform/lambda/ai_scanner/detectors/mcp_server.py platform/lambda/ai_scanner/tests/fixtures/mcp_server/
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "feat(platform): ai_scanner — mcp_server detector (Python AST + mcp.json) + fixtures"
```

---

### Task 10: `agentic_workflow` detector

**Files:**
- Create: `platform/lambda/ai_scanner/detectors/agentic_workflow.py`
- Create: 2 fixtures

**Behavior:** Heuristic — Python AST walk for a function or top-level block containing BOTH:
1. A `while` loop or a recursive call.
2. An LLM SDK call (matching the `model_usage` SDK markers).
3. AND a tool-execution pattern (e.g., a function called on `response.tool_calls`, or `if tool_call:` etc.).

Emits one `agent` asset per detected pattern + `agent → orchestrates → model` + finding `autonomous_loop_no_human_in_loop` (MEDIUM, confidence='medium').

This is the fuzziest detector. False positives expected; that's why `confidence='medium'`.

- [ ] **Step 1–3:** Fixtures + implementation + tests, same pattern as prior detectors. Implementer fleshes out AST walker.

```bash
git add ... && git -c user.email=... commit -m "feat(platform): ai_scanner — agentic_workflow detector (medium-confidence heuristic)"
```

---

### Task 11: `vector_db` detector

**Files:**
- Create: `platform/lambda/ai_scanner/detectors/vector_db.py`
- Create: fixtures for chromadb, pinecone, weaviate, qdrant, faiss, pgvector

**Behavior:** Detect imports of `chromadb`, `pinecone`, `weaviate`, `qdrant_client`, `faiss`. Also detect `pgvector` SQL usage (search for `vector(` calls in SQL files OR `CREATE EXTENSION vector` in migrations).

Each unique vector DB → one `vector_db` asset + `repository → retrieves → vector_db` edge. No findings.

Same TDD pattern as `framework.py`. Implementer follows the established shape.

```bash
git add ... && git -c user.email=... commit -m "feat(platform): ai_scanner — vector_db detector + fixtures"
```

---

### Task 12: `embedding` detector

**Files:**
- Create: `platform/lambda/ai_scanner/detectors/embedding.py`
- Create: fixtures for openai embeddings, voyage, cohere

**Behavior:** Detect calls to `text-embedding-*` (OpenAI), `voyage.embed`, `cohere.embed`. One `embedding` asset per provider. `repository → generates → embedding` edge. No findings.

Same pattern.

```bash
git add ... && git -c user.email=... commit -m "feat(platform): ai_scanner — embedding detector + fixtures"
```

---

### Task 13: `prompt` detector

**Files:**
- Create: `platform/lambda/ai_scanner/detectors/prompt.py`
- Create: fixtures for prompt file + inline prompt

**Behavior:** Detect:
1. Files matching `prompt*.{txt,md}`, `prompts/*`, `*.prompt`.
2. Multi-line string literals (>200 chars) inside function arguments to known SDK calls (e.g., `openai.completions.create(prompt="<long>")`).

Each detected prompt → one `prompt` asset + `repository → accesses → prompt` edge. **Finding:** `prompt_with_secret_pattern` (HIGH) if the prompt content matches a secret regex (the same regex used in `secrets_in_ai_code`).

```bash
git add ... && git -c user.email=... commit -m "feat(platform): ai_scanner — prompt detector + fixtures"
```

---

### Task 14: `secrets_in_ai_code` detector

**Files:**
- Create: `platform/lambda/ai_scanner/detectors/secrets_in_ai_code.py`
- Create: 2 fixtures (positive case: secret + LLM SDK in same file; negative case: secret without AI SDK)

**Behavior:** Gitleaks-style regex set:
- `sk-[A-Za-z0-9]{32,}` (OpenAI)
- `sk-ant-[A-Za-z0-9_-]{40,}` (Anthropic)
- `xoxb-[A-Za-z0-9-]{20,}` (Slack)
- `AKIA[0-9A-Z]{16}` (AWS access key)

Only emits a finding when the file ALSO imports a known LLM SDK (correlation reduces false positives — generic secrets without AI context are someone else's problem).

No assets, no relationships. One `hardcoded_credential_in_ai_module` finding per match (HIGH severity).

```bash
git add ... && git -c user.email=... commit -m "feat(platform): ai_scanner — secrets_in_ai_code detector + fixtures"
```

---

### Task 15: Cross-detector correlator

**Files:**
- Create: `platform/lambda/ai_scanner/detectors/correlator.py`
- Create: `platform/lambda/ai_scanner/tests/fixtures/correlator/agent_calls_mcp/repo/...`
- Create: matching expected.json

**Behavior:** Runs after all 8 detectors. Takes the full set of emitted assets + the file context, and adds derived relationships:

- `agent` + `mcp_server` colocated in same file → emit `agent → invokes → mcp_server` edge.
- `model` + `vector_db` + `prompt` co-located → emit `model → retrieves → vector_db` if a RAG-shaped call chain is detected.

The correlator's `detect()` signature takes the partial `DetectorResult` so far (or the full set) and returns ADDITIONAL emissions.

```python
# platform/lambda/ai_scanner/detectors/correlator.py
"""Cross-detector correlator. Adds derived relationships after all detectors run."""
from __future__ import annotations

from collections import defaultdict
from detectors.base import RelEmission, DetectorResult
import evidence as ev

detector_id      = "ai.detectors.correlator"
detector_version = "0.1.0"


def correlate(ctx, results: list[DetectorResult]) -> DetectorResult:
    rels: list[RelEmission] = []

    # Group assets by source_path
    assets_by_path: dict[str, list] = defaultdict(list)
    for r in results:
        for a in r.assets:
            if a.source_path:
                assets_by_path[a.source_path].append(a)

    for path, assets in assets_by_path.items():
        types_in_file = {a.asset_type for a in assets}

        # Pattern: agent + mcp_server in same file → invokes
        if "agent" in types_in_file and "mcp_server" in types_in_file:
            agent  = next(a for a in assets if a.asset_type == "agent")
            mcp    = next(a for a in assets if a.asset_type == "mcp_server")
            packet = ev.build(
                detector_id=detector_id, detector_version=detector_version,
                subject_kind="ai_relationship", subject_type="invokes",
                subject_name=f"{agent.name}→invokes→{mcp.name}",
                source_events=[{
                    "kind": "file", "repo": ctx.repo_full_name,
                    "commit_sha": ctx.head_commit_sha,
                    "path": path, "snippet_lines": [1, 1],
                    "snippet": "(co-located in same file)",
                }],
                reasoning_chain=["agent and mcp_server detected in same file"],
                confidence="medium",
            )
            rels.append(RelEmission(
                tenant_id=ctx.tenant_id,
                source_asset_ref=f"agent::{ctx.repo_asset_id}::{path}::{agent.name}",
                target_asset_ref=f"mcp_server::{ctx.repo_asset_id}::{path}::{mcp.name}",
                relationship_type="invokes",
                attributes={},
                evidence_packet=packet,
                detector_id=detector_id,
                detector_version=detector_version,
            ))

        # Pattern: model + vector_db + prompt → retrieves (RAG-shaped)
        if "model" in types_in_file and "vector_db" in types_in_file and "prompt" in types_in_file:
            model = next(a for a in assets if a.asset_type == "model")
            vdb   = next(a for a in assets if a.asset_type == "vector_db")
            packet = ev.build(
                detector_id=detector_id, detector_version=detector_version,
                subject_kind="ai_relationship", subject_type="retrieves",
                subject_name=f"{model.name}→retrieves→{vdb.name}",
                source_events=[{
                    "kind": "file", "repo": ctx.repo_full_name,
                    "commit_sha": ctx.head_commit_sha,
                    "path": path, "snippet_lines": [1, 1],
                    "snippet": "(model, vector_db, prompt co-located — RAG-shaped)",
                }],
                reasoning_chain=["model, vector_db, prompt detected in same file"],
                confidence="medium",
            )
            rels.append(RelEmission(
                tenant_id=ctx.tenant_id,
                source_asset_ref=f"model::{ctx.repo_asset_id}::{path}::{model.name}",
                target_asset_ref=f"vector_db::{ctx.repo_asset_id}::{path}::{vdb.name}",
                relationship_type="retrieves",
                attributes={},
                evidence_packet=packet,
                detector_id=detector_id,
                detector_version=detector_version,
            ))

    return DetectorResult(assets=[], relationships=rels, findings=[])
```

Fixtures + tests follow same shape; one positive (agent+mcp colocated) + one negative (no colocation).

```bash
git add ... && git -c user.email=... commit -m "feat(platform): ai_scanner — cross-detector correlator + fixtures"
```

---

### Task 15b: Wire `main.py` to orchestrate the full scan

**Files:**
- Modify: `platform/lambda/ai_scanner/main.py` (replace stub from Task 3 with real orchestration)
- Modify: `platform/lambda/ai_scanner/tests/test_scan_runner.py` (add an end-to-end test using one fixture repo)

This is the integration point. Everything else is in place by now; this task ties it together.

- [ ] **Step 1: Write the failing end-to-end test**

Append to `test_scan_runner.py`:

```python
def test_handler_runs_full_scan_pipeline(monkeypatch, tmp_path):
    """End-to-end: SQS record → clone (stubbed) → detectors → writer (stubbed) → success."""
    import main, scan_runner, writer
    from pathlib import Path

    # Stub clone_repo to point at a real fixture repo containing langchain code
    fixture_root = Path(__file__).parent / "fixtures" / "framework" / "langchain_in_repo" / "repo"
    monkeypatch.setattr(scan_runner, "clone_repo",
                        lambda installation_id, repo_full_name, default_branch, workdir:
                        ("deadbeef", _copytree_to(fixture_root, workdir))[0])

    # Capture writer calls
    calls = []
    monkeypatch.setattr(writer, "commit_scan",
                        lambda ctx, assets, relationships, findings:
                        calls.append({"assets": len(assets),
                                      "relationships": len(relationships),
                                      "findings": len(findings)}))
    monkeypatch.setattr(writer, "mark_scan_failed",
                        lambda ctx, msg: calls.append({"failed": msg}))

    sqs_event = {"Records": [{
        "body": json.dumps({
            "scan_id":         "11111111-1111-1111-1111-111111111111",
            "tenant_id":       "22222222-2222-2222-2222-222222222222",
            "connection_id":   "33333333-3333-3333-3333-333333333333",
            "repo_asset_id":   "44444444-4444-4444-4444-444444444444",
            "repo_full_name":  "kk/foo",
            "default_branch":  "main",
            "installation_id": 99999,
        }),
    }]}

    main.handler(sqs_event, None)

    assert len(calls) == 1
    assert calls[0]["assets"] >= 1   # at least the langchain framework asset


def _copytree_to(src, dst):
    """Helper: copy fixture repo into a temp workdir."""
    import shutil
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return dst
```

Run; confirm it fails because main.py's `handler` is still the stub.

- [ ] **Step 2: Replace `main.py` with the orchestration**

```python
# platform/lambda/ai_scanner/main.py
"""SQS-triggered handler for the AI scanner Lambda.

Orchestrates: parse SQS body → mark scan running → clone repo → run all
detectors → run correlator → commit transactionally. On error: mark scan
failed and re-raise so SQS retries (max 3, then DLQ).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path

import scan_runner
import writer
from detectors import (
    framework, model_usage, mcp_server, agentic_workflow,
    vector_db, embedding, prompt, secrets_in_ai_code, correlator,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ai_scanner")

DETECTORS = [
    framework, model_usage, mcp_server, agentic_workflow,
    vector_db, embedding, prompt, secrets_in_ai_code,
]


def handler(event: dict, context) -> dict:
    records = event.get("Records") or []
    log.info("ai_scanner invoked with %d record(s)", len(records))
    for r in records:
        body = json.loads(r.get("body") or "{}")
        _run_one(body)
    return {"statusCode": 200, "body": json.dumps({"scans_processed": len(records)})}


def _run_one(body: dict) -> None:
    scan_id        = body["scan_id"]
    workdir        = Path(tempfile.gettempdir()) / f"scan-{scan_id}"
    if workdir.exists():
        shutil.rmtree(workdir, ignore_errors=True)

    ctx = None
    try:
        # Phase 1: clone
        sha = scan_runner.clone_repo(
            installation_id=body["installation_id"],
            repo_full_name=body["repo_full_name"],
            default_branch=body["default_branch"],
            workdir=workdir,
        )
        ctx = scan_runner.ScanContext.from_message(body, workdir, sha)
        log.info("cloned %s@%s into %s", ctx.repo_full_name, sha, workdir)

        # Phase 2: run all 8 detectors
        results = []
        for det in DETECTORS:
            log.info("running %s", det.detector_id)
            results.append(det.detect(ctx))

        # Phase 3: correlator
        corr_result = correlator.correlate(ctx, results)

        # Phase 4: aggregate + commit
        all_assets        = [a for r in results for a in r.assets] + corr_result.assets
        all_relationships = [r for res in results for r in res.relationships] + corr_result.relationships
        all_findings      = [f for r in results for f in r.findings] + corr_result.findings
        writer.commit_scan(ctx, all_assets, all_relationships, all_findings)
        log.info("scan %s committed: %d assets, %d rels, %d findings",
                 scan_id, len(all_assets), len(all_relationships), len(all_findings))

    except scan_runner.RepoTooLarge as e:
        if ctx is not None:
            writer.mark_scan_failed(ctx, f"clone_too_large: {e}")
        else:
            # We didn't get far enough to have a ctx; fail the scan row directly.
            from scan_runner import ScanContext
            fake_ctx = ScanContext.from_message(body, workdir, head_commit_sha="")
            writer.mark_scan_failed(fake_ctx, f"clone_too_large: {e}")
        # Don't re-raise — DLQ retrying a too-large repo won't help.
    except Exception as e:
        log.exception("scan %s failed", scan_id)
        if ctx is not None:
            writer.mark_scan_failed(ctx, f"{type(e).__name__}: {e}")
        raise  # Re-raise so SQS retries up to maxReceiveCount, then DLQ.
    finally:
        # Always wipe the clone — Lambda /tmp persists across warm invocations.
        if workdir.exists():
            shutil.rmtree(workdir, ignore_errors=True)
```

- [ ] **Step 3: Run the test (the framework fixture must be reachable by the integration test)**

```bash
/Users/kkmookhey/venv/bin/pytest platform/lambda/ai_scanner/tests/test_scan_runner.py::test_handler_runs_full_scan_pipeline -v
# Expected: 1 passed
```

Also re-run the full suite to confirm nothing regressed:

```bash
/Users/kkmookhey/venv/bin/pytest platform/lambda/ai_scanner/tests/ -v
```

- [ ] **Step 4: Commit**

```bash
git add platform/lambda/ai_scanner/main.py platform/lambda/ai_scanner/tests/test_scan_runner.py
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "feat(platform): ai_scanner — wire handler to orchestrate clone → detectors → correlator → commit"
```

---

## Phase D — API endpoints + scan trigger (2 tasks)

### Task 16: `ai_scan_api` Lambda — 5 routes

**Files:**
- Create: `platform/lambda/ai_scan_api/main.py`
- Create: `platform/lambda/ai_scan_api/helpers.py`
- Create: `platform/lambda/ai_scan_api/tests/__init__.py` + `conftest.py` + `test_handler.py`

Five routes, same dispatch pattern as `ai_github`. **API Gateway strips `/v1` before forwarding**, so use bare path comparisons (this was the bug we hit in 1a — don't repeat it).

- `POST /ai/scans` → insert ai_scans row + create repo ai_asset row if absent + enqueue SQS message
- `GET /ai/scans` → list tenant's scans (filterable by connection_id, status)
- `GET /ai/scans/{id}` → scan detail
- `GET /ai/assets` → list assets (filters: repo, type, since)
- `GET /ai/assets/{id}` → asset detail + full evidence packet

Helpers (`resp`, `resolve_tenant_id`) are copied verbatim from `ai_github/helpers.py`. **Do not import across Lambdas** — Lambda zips are separate; each one needs its own copy.

(Step-by-step content for this task: ~250 lines of test code + ~400 lines of handler code following the same shape as 1a's `ai_github/main.py`. The TDD steps mirror Tasks 5–9 of the Slice 1a plan exactly.)

```bash
git add platform/lambda/ai_scan_api/
git -c user.email=kkmookhey@gmail.com -c user.name="KK Mookhey" commit -m "feat(platform): ai_scan_api Lambda — 5 routes (POST scans + GET scans/assets)"
```

---

### Task 17: CDK wiring — ai_scanner DockerImageFunction + ai_scan_api Lambda + SQS event source + 5 API routes

**Files:**
- Modify: `platform/lib/scan-stack.ts` — add `ai_scanner` DockerImageFunction with SQS event source
- Modify: `platform/lib/api-stack.ts` — add `ai_scan_api` Lambda + 5 routes + SQS write permission

In `scan-stack.ts`, after the existing scanner Lambdas, add (matching the existing `DockerImageFunction` shape):

```typescript
    this.aiScanner = new lambda.DockerImageFunction(this, 'AiScanner', {
      code:        lambda.DockerImageCode.fromEcr(props.aiScannerRepo, { tagOrDigest: 'latest' }),
      timeout:     cdk.Duration.seconds(600),
      memorySize:  2048,
      ephemeralStorageSize: cdk.Size.gibibytes(4),
      architecture: lambda.Architecture.X86_64,
      environment: {
        ...dbEnv,
        GITHUB_APP_SECRET_ARN: `arn:aws:secretsmanager:${this.region}:${this.account}:secret:ciso-copilot/github-app/credentials`,
        SCANNER_VERSION:       '0.1.0',
      },
    });
    props.dbCluster.grantDataApiAccess(this.aiScanner);
    this.aiScanner.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['secretsmanager:GetSecretValue'],
      resources: [
        `arn:aws:secretsmanager:${this.region}:${this.account}:secret:ciso-copilot/github-app/credentials*`,
      ],
    }));

    // Wire SQS event source — max 5 concurrent invocations
    this.aiScanner.addEventSource(new lambda_event.SqsEventSource(this.aiScanQueue, {
      batchSize: 1,
      maxConcurrency: 5,
    }));
```

Add `import * as lambda_event from 'aws-cdk-lib/aws-lambda-event-sources';` to the imports.

In `api-stack.ts`, add the `ai_scan_api` Lambda (zip, no bundling needed — pure boto3 + stdlib) + the 5 API routes + grant SQS:SendMessage to it.

Both stacks deploy together. Run `npx cdk deploy CisoCopilotScan CisoCopilotApi --require-approval never`.

```bash
git add platform/lib/scan-stack.ts platform/lib/api-stack.ts
git -c user.email=... commit -m "feat(platform): wire ai_scanner Lambda + SQS event + ai_scan_api routes"
```

---

## Phase E — Web UI (4 tasks)

### Task 18: `api.ts` — add scan/asset types + 5 client methods

**Files:**
- Modify: `web/src/lib/api.ts`

Add types:

```typescript
export interface AIScanSummary {
  id:                             string;
  repo_full_name:                 string;
  status:                         "queued" | "running" | "success" | "failed";
  started_at:                     string;
  completed_at:                   string | null;
  error_message:                  string | null;
  assets_discovered_count:        number;
  relationships_discovered_count: number;
  findings_generated_count:       number;
}

export interface AIAssetSummary {
  id:            string;
  asset_type:    "repository" | "model" | "mcp_server" | "framework" |
                 "vector_db" | "prompt" | "agent" | "embedding" | "tool";
  name:          string;
  source_repo:   { id: string; full_name: string } | null;
  source_path:   string | null;
  detector_id:   string;
  first_seen_at: string;
  last_seen_at:  string;
}

export interface AIAssetDetail extends AIAssetSummary {
  attributes:       Record<string, unknown>;
  evidence_packet:  Record<string, unknown>;
  connection_id:    string;
}
```

Add methods (using `call<T>` per existing convention):

```typescript
async startScan(connectionId: string, repoFullName: string): Promise<{ scan_id: string }> { ... }
async listScans(connectionId?: string): Promise<{ scans: AIScanSummary[] }> { ... }
async getScan(scanId: string): Promise<AIScanSummary> { ... }
async listAIAssets(opts?: {repo?: string; type?: string; page?: number}): Promise<{ assets: AIAssetSummary[]; next_page: number | null }> { ... }
async getAIAsset(assetId: string): Promise<AIAssetDetail> { ... }
```

(TDD here is via TypeScript compile + manual smoke. No unit tests for api.ts since it just wraps `call<T>`.)

```bash
git add web/src/lib/api.ts
git -c user.email=... commit -m "feat(web): api.ts — AI scan + asset types and client methods"
```

---

### Task 19: `RepoPicker.tsx` — enable Scan button + status polling

**Files:**
- Modify: `web/src/routes/RepoPicker.tsx`

Change the Scan button from disabled to enabled. On click:

```tsx
async function startScan(repoFullName: string) {
  setScanning(prev => ({ ...prev, [repoFullName]: { status: "queued" } }));
  try {
    const { scan_id } = await api.startScan(id!, repoFullName);
    poll(repoFullName, scan_id);
  } catch (e) {
    setScanning(prev => ({ ...prev, [repoFullName]: { status: "failed", error: (e as Error).message } }));
  }
}

function poll(repoFullName: string, scanId: string) {
  const tick = async () => {
    try {
      const s = await api.getScan(scanId);
      setScanning(prev => ({ ...prev, [repoFullName]: { status: s.status, ...s } }));
      if (s.status === "queued" || s.status === "running") {
        setTimeout(tick, 3000);
      }
    } catch { setTimeout(tick, 5000); }
  };
  setTimeout(tick, 1500);
}
```

Update the JSX so each row's Scan button reflects state: `Scan` / `Scanning…` / `Success: N assets` / `Failed`.

```bash
git add web/src/routes/RepoPicker.tsx
git -c user.email=... commit -m "feat(web): RepoPicker — enable Scan + status polling"
```

---

### Task 20: `AIInventory.tsx` — new /ai/inventory route

**Files:**
- Create: `web/src/routes/AIInventory.tsx`
- Modify: `web/src/App.tsx` (register route)

Table of `ai_assets` for the tenant, grouped by source_repo. Columns: asset_type, name, source_path, detector_id, first/last seen. Filter chips by asset_type. Row click → `/ai/inventory/:asset_id`.

Implement using `api.listAIAssets()`. Pagination via `?page=N` query string state.

```bash
git add web/src/routes/AIInventory.tsx web/src/App.tsx
git -c user.email=... commit -m "feat(web): /ai/inventory tab (asset list grouped by repo)"
```

---

### Task 21: `AssetDetail.tsx` — new /ai/inventory/:asset_id route

**Files:**
- Create: `web/src/routes/AssetDetail.tsx`
- Modify: `web/src/App.tsx`

Per-asset page. Header (name, type, source path linking to GitHub). Attributes block (rendered JSON). **Evidence packet** collapsible (`<details>`/`<pre>`). Source-code snippet rendered from `evidence_packet.source_events[0]` via GitHub raw API (or just show the inline `snippet` field if provided). Related assets placeholder for 1c.

```bash
git add web/src/routes/AssetDetail.tsx web/src/App.tsx
git -c user.email=... commit -m "feat(web): /ai/inventory/:asset_id detail page + evidence packet view"
```

---

## Phase F — iOS UI (2 tasks)

### Task 22: iOS APIClient additions + AI tab in MainTabView

**Files:**
- Modify: `ios/CISOCopilot/Services/APIClient.swift` — add methods + Codable structs for AIAsset and AIScan
- Modify: `ios/CISOCopilot/Views/MainTabView.swift` — add a new "AI" tab in the bottom bar (between Risks and Settings)

The TabView gets a new entry:

```swift
NavigationStack {
    AIInventoryView()
}
.tabItem { Label("AI", systemImage: "brain.head.profile") }
```

Add `AIAsset`, `AIScan` Codable structs in `APIClient.swift` mirroring the web types. Methods:

```swift
func listAIAssets() async throws -> [AIAsset] { ... }
func getAIAsset(_ id: UUID) async throws -> AIAssetDetail { ... }
```

```bash
git add ios/CISOCopilot/Services/APIClient.swift ios/CISOCopilot/Views/MainTabView.swift ios/project.yml
git -c user.email=... commit -m "feat(ios): add AI tab to MainTabView + APIClient methods"
```

### Task 23: iOS AIInventoryView + AIAssetDetailView

**Files:**
- Create: `ios/CISOCopilot/Views/AI/AIInventoryView.swift`
- Create: `ios/CISOCopilot/Views/AI/AIAssetDetailView.swift`
- Modify: `ios/project.yml` — register the new source files

AIInventoryView: a `List` grouped by `source_repo`, each section showing the assets. Pull-to-refresh. Tap row → `NavigationLink` to AIAssetDetailView.

AIAssetDetailView: scrollable list of attributes + a collapsible "Evidence Packet" `DisclosureGroup` containing raw JSON.

```bash
git add ios/CISOCopilot/Views/AI/ ios/project.yml
git -c user.email=... commit -m "feat(ios): AI Inventory + Asset Detail (read-only)"
```

---

## Phase G — Deploy + E2E (1 task)

### Task 24: Build image, deploy backend + web, install iOS, run the demo

**Files:** none (deploy step).

- [ ] **Step 1: Build + push the scanner image**

```bash
cd platform/lambda/ai_scanner
./build.sh
```

- [ ] **Step 2: Deploy backend stacks (Scan + Api)**

```bash
cd platform
npx cdk deploy CisoCopilotScan CisoCopilotApi --require-approval never
```

- [ ] **Step 3: Deploy web**

```bash
cd web
pnpm build
aws s3 sync dist/ s3://ciso-copilot-app-470226123496/ --delete --region us-east-1
aws cloudfront create-invalidation --distribution-id E2FV1Z0DJ4RQS4 --paths '/*' --region us-east-1
```

- [ ] **Step 4: Build + install iOS**

```bash
cd ios
xcodegen generate
xcodebuild build \
  -project CISOCopilot.xcodeproj -scheme CISOCopilot \
  -destination "id=00008140-001E104E3A9B001C" \
  -derivedDataPath build-device \
  -allowProvisioningUpdates
xcrun devicectl device install app --device 00008140-001E104E3A9B001C \
  build-device/Build/Products/Debug-iphoneos/CISOCopilot.app
```

- [ ] **Step 5: The demo**

In the web app on a fresh tab:

1. Sign in.
2. Connect clouds → click the existing GitHub connection (from Slice 1a) → land on repo picker.
3. Pick an AI-bearing repo (e.g., a repo that imports `openai` or `langchain`). Click **Scan**.
4. Watch the status pill: `Scanning…` → ~30 s later → `Success: N assets`.
5. Navigate to the new **AI Inventory** tab.
6. See the assets discovered (frameworks, models, MCP servers, etc.) grouped by repo.
7. Click an asset → see its detail page + evidence packet.
8. On iOS, open the app → tap the new **AI** tab → see the same assets read-only.

DB sanity:

```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot \
  --sql "SELECT asset_type, COUNT(*) FROM ai_assets GROUP BY asset_type ORDER BY 2 DESC" \
  --region us-east-1
```

Expected: counts per asset_type.

- [ ] **Step 6: No commit (deploy + verification only)**

---

## Out of scope for 1b (do NOT add here)

- Trust graph viz (cytoscape.js) — Slice 1c
- AI Risks tab — Slice 1c
- Per-asset relationships endpoint + UI — Slice 1c
- KMS-signed evidence packets — later
- Push-webhook-driven rescan on commit — later
- Sparse-checkout for monorepos > 4 GB — later
- All-repos aggregate trust graph — later

If you discover during execution that any of the above is "needed" to finish 1b, stop and surface it rather than scope-creeping.

---

## Self-review checklist

- All 8 detectors have a task: ✅ Tasks 7–14.
- Correlator + write path have tasks: ✅ Tasks 5 (writer), 15 (correlator).
- SQS handler orchestration (clone → detectors → correlator → writer): ✅ Task 15b.
- All 5 API endpoints from spec §9.7 have a task: ✅ Task 16.
- Web UI from spec §9.8 covered: ✅ Tasks 18–21.
- iOS UI from spec §9.9 covered: ✅ Tasks 22–23.
- Deploy + E2E: ✅ Task 24.
- Total: 25 tasks. Estimated ≈8 days.

## Known gaps / decisions deferred to execution time

1. **Allowlist for `unapproved_provider`.** Spec §11.5 flagged this as an open question. This plan ships `model_usage` with NO `unapproved_provider` finding emitted (it just creates `model` assets). Add the finding-emission once the tenant config has an allowlist column — a Slice 1c+ concern.
2. **AI finding severity scoring.** Plan uses fixed severity per finding type (HIGH for credential leaks + broad-perm MCP, MEDIUM for autonomous-loop, etc.). When AI Risks tab lands in 1c, confirm these scores feed into the existing `findings_summary` aggregator correctly without re-scoring.
3. **Tree-sitter vs. stdlib `ast`.** Plan uses stdlib `ast` to keep the image small. If detector 4 (agentic_workflow) becomes too noisy with `ast` alone, revisit with tree-sitter (will add ~150 MB to the image).
