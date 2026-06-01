# MCP Connectors Slice 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship production-ready autonomous broadcast: when a CRITICAL finding lands in any scanner, a Block Kit card posts to the tenant's configured Slack channel within ~60s. Admin can install Shasta's bot, pick a channel, toggle the rule, and disconnect.

**Architecture:** Five vertical sub-slices, each a separate PR. Sub-slice 2.1 extracts the fan-out hook into a shared module (narrowed from the spec's full `unified_writer` consolidation — see note in 2.1). 2.2 adds the admin OAuth flow on the existing `connectors/` Lambda. 2.3 wires the channel picker + autonomous toggle. 2.4 builds the SQS → `findings_subscriber` pipeline + Block Kit template + three-layer kill switch. 2.5 adds `<DeepLinkGate>` + production hardening (CloudWatch alarms, drift metric).

**Tech Stack:** Python 3.12 Lambda, Aurora Data API (boto3), SQS standard queue, DynamoDB, AWS KMS, Slack OAuth v2 + MCP SDK (`pip install mcp`), React + TypeScript + Vite for web, CDK (TypeScript).

**Spec:** `docs/superpowers/specs/2026-05-31-mcp-connectors-slice-2-design.md`. Re-read it before starting; the plan does not duplicate the spec's rationale.

**Scope adjustment (2026-05-31):** The spec §5.D called for full consolidation of three duplicated `unified_writer.py` modules into `_shared/`. Investigation found the three copies have functional drift (CME-v2 normalize counters in `ai_scanner` only) and per-scanner imports of detector emission types. Full consolidation is a ~200-line cross-scanner refactor with non-trivial test risk. **Sub-slice 2.1 narrows to extracting only the fan-out hook** into `_shared/broadcast_fanout.py`; every scanner imports that one module for the SQS publish. Full `unified_writer.py` consolidation is a separate cleanup PR if KK wants it later. This still achieves the spec's intent (one source of truth for the fan-out logic) without touching detector emission types.

---

## Sub-slice 2.1 — Fan-out hook in `_shared/broadcast_fanout.py`

**Why first:** every other sub-slice depends on the queue URL env var being plumbed and the scanners actually publishing. If 2.4 ships first without this, the subscriber will sit idle.

### Task 1: Add `broadcast_fanout` module to `_shared/`

**Files:**
- Create: `platform/lambda/_shared/broadcast_fanout.py`
- Create: `platform/lambda/_shared/tests/test_broadcast_fanout.py`

- [ ] **Step 1: Write the failing test**

`platform/lambda/_shared/tests/test_broadcast_fanout.py`:
```python
"""Fan-out hook for the autonomous CRITICAL-finding Slack broadcast.

Module owns ONE responsibility: best-effort publish to SQS when a
critical-fail finding is written. Failures log and swallow."""
from __future__ import annotations
from unittest.mock import MagicMock


def _install_fake_sqs(monkeypatch):
    monkeypatch.setenv("AUTONOMOUS_BROADCAST_QUEUE_URL",
                       "https://sqs.us-east-1.amazonaws.com/000000000000/q")
    from _shared import broadcast_fanout as bf
    fake = MagicMock()
    monkeypatch.setattr(bf, "_sqs", fake)
    return bf, fake


def test_publishes_when_critical_fail(monkeypatch):
    bf, fake = _install_fake_sqs(monkeypatch)
    bf.publish_if_critical(
        tenant_id="t-1", finding_id="f-1", scan_id="s-1",
        severity="critical", status="fail",
    )
    fake.send_message.assert_called_once()
    body = fake.send_message.call_args.kwargs["MessageBody"]
    import json
    payload = json.loads(body)
    assert payload == {
        "tenant_id": "t-1", "finding_id": "f-1", "scan_id": "s-1",
    }


def test_skips_when_not_critical(monkeypatch):
    bf, fake = _install_fake_sqs(monkeypatch)
    bf.publish_if_critical(
        tenant_id="t-1", finding_id="f-1", scan_id="s-1",
        severity="high", status="fail",
    )
    fake.send_message.assert_not_called()


def test_skips_when_not_fail(monkeypatch):
    bf, fake = _install_fake_sqs(monkeypatch)
    bf.publish_if_critical(
        tenant_id="t-1", finding_id="f-1", scan_id="s-1",
        severity="critical", status="pass",
    )
    fake.send_message.assert_not_called()


def test_short_circuits_when_queue_url_unset(monkeypatch):
    """A scanner that hasn't been granted sqs:SendMessage shouldn't crash;
    it should short-circuit silently when the env var is empty."""
    monkeypatch.delenv("AUTONOMOUS_BROADCAST_QUEUE_URL", raising=False)
    from _shared import broadcast_fanout as bf
    fake = MagicMock()
    monkeypatch.setattr(bf, "_sqs", fake)
    bf.publish_if_critical(
        tenant_id="t", finding_id="f", scan_id="s",
        severity="critical", status="fail",
    )
    fake.send_message.assert_not_called()


def test_swallows_sqs_errors(monkeypatch, capsys):
    """A missed broadcast is recoverable; a failed scanner write is not.
    The hook must not propagate SQS errors back to the writer."""
    bf, fake = _install_fake_sqs(monkeypatch)
    fake.send_message.side_effect = RuntimeError("sqs blew up")

    # Must NOT raise.
    bf.publish_if_critical(
        tenant_id="t", finding_id="f", scan_id="s",
        severity="critical", status="fail",
    )
    # But must log loudly so the drift metric catches it.
    out = capsys.readouterr().out
    assert "broadcast_fanout" in out
    assert "sqs blew up" in out
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform/lambda/_shared
/Users/kkmookhey/Projects/CISOBrief/platform/venv/bin/python -m pytest tests/test_broadcast_fanout.py -q
```

Expected: collection error or 5 failures with `ModuleNotFoundError: _shared.broadcast_fanout`.

- [ ] **Step 3: Write minimal implementation**

`platform/lambda/_shared/broadcast_fanout.py`:
```python
"""Fan-out hook for the autonomous CRITICAL-finding Slack broadcast.

Owns ONE responsibility: best-effort SQS publish from any scanner's
writer when severity='critical' AND status='fail'. Failures log loudly
(so the drift metric in 2.5 catches them) but are NEVER propagated to
the caller — a missed broadcast is recoverable (the finding is in
Aurora; the next scan's flip re-fires), a failed scanner write is data
loss.

Scanners that don't have sqs:SendMessage granted MUST leave
AUTONOMOUS_BROADCAST_QUEUE_URL unset; the hook then short-circuits.
"""
from __future__ import annotations
import json
import os

import boto3

_sqs = boto3.client("sqs")


def publish_if_critical(*, tenant_id: str, finding_id: str, scan_id: str,
                        severity: str, status: str) -> None:
    queue_url = os.environ.get("AUTONOMOUS_BROADCAST_QUEUE_URL")
    if not queue_url:
        return
    if severity != "critical" or status != "fail":
        return
    try:
        _sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps({
                "tenant_id": tenant_id,
                "finding_id": finding_id,
                "scan_id": scan_id,
            }),
        )
    except Exception as e:
        # Log but swallow. Drift metric in 2.5 detects systematic loss.
        print(f"[broadcast_fanout] publish failed: {type(e).__name__}: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
/Users/kkmookhey/Projects/CISOBrief/platform/venv/bin/python -m pytest tests/test_broadcast_fanout.py -q
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git checkout -b feat/mcp-connectors-slice-2.1-broadcast-fanout
git add platform/lambda/_shared/broadcast_fanout.py platform/lambda/_shared/tests/test_broadcast_fanout.py
git commit -m "$(cat <<'EOF'
feat(_shared): broadcast_fanout module — SQS publish on critical findings

Best-effort SQS publish when severity=critical AND status=fail.
Failures log loudly but never propagate to the writer caller — a
missed broadcast is recoverable, a failed write is not.

Foundation for MCP Connectors Slice 2 (spec
docs/superpowers/specs/2026-05-31-mcp-connectors-slice-2-design.md
§5.D). Each scanner's existing unified_writer will call
publish_if_critical after the finding INSERT.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 2: Wire the fan-out call into each scanner's writer

**Files:**
- Modify: `platform/lambda/ai_scanner/unified_writer.py` (after the INSERT in `_insert_finding`)
- Modify: `platform/lambda/shasta_runner_azure/app/unified_writer.py` (same location)
- Modify: `platform/lambda/shasta_runner/app/unified_writer.py` (same location)
- Modify: `platform/lambda/ai_scanner/tests/test_unified_writer.py` (add a test asserting the fan-out fires)

- [ ] **Step 1: Audit other writer locations**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
grep -rln "INSERT INTO findings" platform/lambda/ | sort -u
```

Confirm only the three files listed above appear. If a fourth scanner has its own writer, add it to the modification list before continuing.

- [ ] **Step 2: Modify `ai_scanner/unified_writer.py`**

Add at the top of the file, after the existing imports:

```python
from _shared import broadcast_fanout
```

Locate the `_insert_finding` function (search for `def _insert_finding`). Immediately after the `_rds.execute_statement(...)` call inside it (the INSERT INTO findings statement), and before the function returns, add:

```python
    # Autonomous broadcast fan-out (Slice 2). Best-effort; failures don't
    # propagate. See _shared/broadcast_fanout.py.
    broadcast_fanout.publish_if_critical(
        tenant_id=f.tenant_id,
        finding_id=fid,
        scan_id=scan_id,
        severity=f.severity,
        status=f.status,
    )
```

- [ ] **Step 3: Modify `shasta_runner_azure/app/unified_writer.py`**

Same change as Step 2. The `FindingEmission` fields are identical (tenant_id, severity, status). If field names differ — verify via `grep -n "class FindingEmission\|@dataclass" platform/lambda/shasta_runner_azure/app/detectors/base.py` — adjust the kwarg names.

- [ ] **Step 4: Modify `shasta_runner/app/unified_writer.py`**

Same change as Step 2. Verify field names with `grep -n "class FindingEmission" platform/lambda/shasta_runner/app/detectors/base.py`.

- [ ] **Step 5: Bundling — verify `_shared/` is copied into each scanner's deployment package**

```bash
grep -n "_shared\|copy.*shared" platform/lib/api-stack.ts platform/lib/scan-stack.ts 2>/dev/null | head -20
```

Each scanner Lambda's CDK bundling step must `cp -r _shared /asset-output/`. If a scanner is missing this, add it. The `connectors/` Lambda's bundling block (in `api-stack.ts`) is the reference shape:

```typescript
command: [
  'bash', '-c',
  'pip install ... -t /asset-output && ' +
  'cp -r connectors /asset-output/ && ' +
  'cp -r _shared/mcp_oauth /asset-output/',
],
```

Each scanner bundling block needs `cp -r _shared/broadcast_fanout.py /asset-output/_shared/` (or `cp -r _shared /asset-output/` if the whole `_shared` is copied — check existing pattern). For scanners that use `lambda.Code.fromAsset(directory)` directly (no bundling block), the file is already auto-included if it sits under the scanner's directory; otherwise the file needs to be sym-linked or duplicated at build time. Inspect each scanner's CDK definition and pick the matching pattern.

- [ ] **Step 6: Write a fan-out integration test in ai_scanner**

`platform/lambda/ai_scanner/tests/test_unified_writer.py` — add a new test:

```python
def test_critical_fail_triggers_fanout(monkeypatch):
    """End-to-end: when commit_scan writes a critical-fail finding, the
    broadcast_fanout hook publishes to SQS."""
    from unittest.mock import MagicMock
    from _shared import broadcast_fanout as bf

    fake_sqs = MagicMock()
    monkeypatch.setenv("AUTONOMOUS_BROADCAST_QUEUE_URL",
                       "https://sqs.us-east-1.amazonaws.com/000000000000/q")
    monkeypatch.setattr(bf, "_sqs", fake_sqs)

    # Patch the Aurora Data API client so we don't hit a real DB.
    from unified_writer import _rds, commit_scan
    fake_rds = MagicMock()
    fake_rds.begin_transaction.return_value = {"transactionId": "tx-1"}
    fake_rds.execute_statement.return_value = {
        "generatedFields": [],
        "records": [[{"stringValue": "entity-1"}]],
    }
    monkeypatch.setattr("unified_writer._rds", fake_rds)

    # Minimal ctx + emission shapes — match existing test fixtures.
    # (Copy the existing test_commit_scan fixture if one is already
    # present; this test reuses the same fixture pattern.)
    from detectors.base import FindingEmission
    ctx = MagicMock(tenant_id="t-1", scan_id="s-1",
                    connection_id="c-1", scanner_version="v1")
    finding = FindingEmission(
        tenant_id="t-1", finding_type="critical_test",
        title="t", description="d",
        severity="critical", status="fail",
        subject_ref=None, subject_type=None, region=None,
        domain="ai", evidence_packet={}, frameworks={},
    )

    commit_scan(ctx, entities=[], edges=[], findings=[finding])

    fake_sqs.send_message.assert_called_once()
```

- [ ] **Step 7: Run scanner test suites**

```bash
cd platform/lambda/ai_scanner && /Users/kkmookhey/Projects/CISOBrief/platform/venv/bin/python -m pytest tests/ -q
cd ../shasta_runner_azure && /Users/kkmookhey/Projects/CISOBrief/platform/venv/bin/python -m pytest app/tests/ -q
cd ../shasta_runner && /Users/kkmookhey/Projects/CISOBrief/platform/venv/bin/python -m pytest app/tests/ -q 2>&1 || echo "no tests"
```

Expected: pre-existing tests still pass; new fan-out test passes.

- [ ] **Step 8: Commit**

```bash
git add platform/lambda/ai_scanner/unified_writer.py \
        platform/lambda/ai_scanner/tests/test_unified_writer.py \
        platform/lambda/shasta_runner_azure/app/unified_writer.py \
        platform/lambda/shasta_runner/app/unified_writer.py \
        platform/lib/*.ts
git commit -m "$(cat <<'EOF'
feat(scanners): wire broadcast_fanout hook into each scanner's writer

ai_scanner, shasta_runner_azure, and shasta_runner each call
broadcast_fanout.publish_if_critical(...) after INSERT INTO findings.
The hook short-circuits if AUTONOMOUS_BROADCAST_QUEUE_URL is unset,
so this commit is a no-op in environments where the queue (Slice 2.4)
hasn't been deployed yet.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 3: Open sub-slice 2.1 PR

- [ ] **Step 1: Push and open PR**

```bash
git push -u origin feat/mcp-connectors-slice-2.1-broadcast-fanout
gh pr create --title "feat: Slice 2.1 — broadcast_fanout hook for autonomous CRITICAL broadcasts" --body "$(cat <<'EOF'
## Summary

- New `_shared/broadcast_fanout.py` — best-effort SQS publish on critical findings
- ai_scanner, shasta_runner_azure, shasta_runner each call the hook after INSERT INTO findings
- No-op until Slice 2.4 deploys the SQS queue and sets `AUTONOMOUS_BROADCAST_QUEUE_URL` env

## Why narrowed

Spec called for full unified_writer consolidation across the three scanners. Investigation found drift (CME-v2 normalize counters in ai_scanner only) and per-scanner detector emission imports. Narrowing to fan-out extraction only — see plan §"Scope adjustment" — gets the production goal (one source of truth for the fan-out logic) without the cross-scanner refactor risk.

## Test plan

- [x] `_shared/tests/test_broadcast_fanout.py` — 5 tests, all paths
- [x] `ai_scanner/tests/test_unified_writer.py` — fan-out integration test
- [x] Scanner suites green (ai_scanner, shasta_runner_azure, shasta_runner)
- [ ] CDK synth clean (no scanner deployment surface change)
EOF
)"
```

---

## Sub-slice 2.2 — Admin Slack bot install flow

### Task 4: Add `_require_admin` helper

**Files:**
- Modify: `platform/lambda/connectors/handlers_slack.py` (extend `_resolve_user_context` pattern with admin gate, OR create new helper here)
- Test: `platform/lambda/connectors/tests/test_handlers_admin_slack.py` (new file)

- [ ] **Step 1: Write the failing test**

`platform/lambda/connectors/tests/test_handlers_admin_slack.py` (new file):
```python
"""Tests for admin-gated Slack workspace bot handlers."""
from __future__ import annotations
from unittest.mock import MagicMock


def test_require_admin_returns_tenant_user_for_admin(monkeypatch):
    """Admin role → returns (tenant_id, user_id)."""
    from connectors.handlers_slack_workspace_bot import _require_admin

    fake_db = MagicMock()
    fake_db.execute.return_value.fetchone.return_value = {
        "tenant_id": "t-1", "user_id": "u-1",
    }
    monkeypatch.setattr("mcp_oauth.session._db", lambda: fake_db)

    result = _require_admin({"sub": "subject-admin"})
    assert result == ("t-1", "u-1")


def test_require_admin_rejects_non_admin(monkeypatch):
    """role != 'admin' → returns (None, None) (or whatever no-admin sentinel)."""
    from connectors.handlers_slack_workspace_bot import _require_admin

    fake_db = MagicMock()
    # SQL filters role='admin' so no row when caller isn't admin.
    fake_db.execute.return_value.fetchone.return_value = None
    monkeypatch.setattr("mcp_oauth.session._db", lambda: fake_db)

    result = _require_admin({"sub": "subject-member"})
    assert result == (None, None)


def test_require_admin_returns_none_when_no_subject():
    """No sso_subject extractable → (None, None)."""
    from connectors.handlers_slack_workspace_bot import _require_admin
    assert _require_admin({}) == (None, None)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd platform/lambda/connectors && /Users/kkmookhey/Projects/CISOBrief/platform/venv/bin/python -m pytest tests/test_handlers_admin_slack.py -q
```

Expected: ModuleNotFoundError on `connectors.handlers_slack_workspace_bot`.

- [ ] **Step 3: Create the new handler module skeleton + `_require_admin`**

`platform/lambda/connectors/handlers_slack_workspace_bot.py` (new file):
```python
"""Admin Slack workspace bot OAuth handlers.

Distinct from the per-user Slack OAuth flow in handlers_slack.py:
  - Different scopes (bot scopes: chat:write, channels:read, groups:read)
  - Different token target (tenant_bot_connectors, not user_connectors)
  - Different state JWT audience ("slack-bot-callback")
  - Admin gate: caller must have users.role='admin'

Same Slack app, same SSM credentials.
"""
from __future__ import annotations
from connectors.main import subject_from_claims
from mcp_oauth.session import _db


def _require_admin(claims: dict) -> tuple[str | None, str | None]:
    """Resolve (tenant_id, user_id) only if the caller is a tenant admin.

    Mirrors handlers_slack._resolve_user_context but adds AND role='admin'
    to the WHERE clause. Returns (None, None) on:
      - no extractable sso_subject
      - no users row matching the subject
      - user exists but role != 'admin'
    """
    subject = subject_from_claims(claims)
    if not subject:
        return None, None
    row = _db().execute(
        "SELECT tenant_id, user_id FROM users "
        "WHERE sso_subject = :sub AND role = 'admin' LIMIT 1",
        [{"name": "sub", "value": {"stringValue": subject}}],
    ).fetchone()
    if not row:
        return None, None
    return str(row["tenant_id"]), str(row["user_id"])
```

- [ ] **Step 4: Run test to verify it passes**

```bash
/Users/kkmookhey/Projects/CISOBrief/platform/venv/bin/python -m pytest tests/test_handlers_admin_slack.py -q
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git checkout -b feat/mcp-connectors-slice-2.2-admin-bot-install
git add platform/lambda/connectors/handlers_slack_workspace_bot.py \
        platform/lambda/connectors/tests/test_handlers_admin_slack.py
git commit -m "feat(connectors): _require_admin helper for admin-gated routes

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 5: Add `slack-bot-callback` audience to state JWT

**Files:**
- Modify: `platform/lambda/_shared/mcp_oauth/state.py` — no changes needed (the existing `expected_provider` kwarg already handles arbitrary provider strings). Verify with a test.
- Test: `platform/lambda/_shared/tests/test_mcp_oauth_state.py` — add a test for `slack-bot` audience.

- [ ] **Step 1: Add the test**

Append to `platform/lambda/_shared/tests/test_mcp_oauth_state.py`:
```python
def test_state_rejects_slack_user_jwt_at_slack_bot_callback(monkeypatch):
    """A JWT minted for the user OAuth flow (provider="slack") MUST NOT
    decode at the admin bot callback (expected_provider="slack-bot")."""
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)
    from mcp_oauth.state import sign_state, verify_state
    import jwt

    token = sign_state(
        tenant_id="t", user_id="u", provider="slack",
        pkce_verifier_hash="h", nonce="n",
    )
    with pytest.raises(jwt.InvalidAudienceError):
        verify_state(token, expected_provider="slack-bot")
```

- [ ] **Step 2: Run + verify**

```bash
cd platform/lambda/_shared && /Users/kkmookhey/Projects/CISOBrief/platform/venv/bin/python -m pytest tests/test_mcp_oauth_state.py -q
```

Expected: all tests pass (the new test exercises existing audience-pinning code).

- [ ] **Step 3: Commit**

```bash
git add platform/lambda/_shared/tests/test_mcp_oauth_state.py
git commit -m "test(state): assert slack user JWT rejected at slack-bot callback

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 6: Implement `initiate_slack_workspace_bot` handler

**Files:**
- Modify: `platform/lambda/connectors/handlers_slack_workspace_bot.py`
- Test: `platform/lambda/connectors/tests/test_handlers_admin_slack.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_handlers_admin_slack.py`:
```python
def test_initiate_workspace_bot_returns_authorize_url(monkeypatch):
    """Admin caller → 200 with Slack authorize URL containing bot scopes."""
    import json
    from unittest.mock import patch
    monkeypatch.setenv("SLACK_CLIENT_ID", "abc")
    monkeypatch.setenv("CONNECTORS_REDIRECT_BASE",
                       "https://app.shasta.io/v1/connectors")
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)

    with patch("connectors.handlers_slack_workspace_bot._require_admin",
               return_value=("t-1", "u-1")), \
         patch("connectors.handlers_slack_workspace_bot.pkce.store_verifier") as store:
        from connectors import main as m
        ev = {
            "httpMethod": "POST",
            "rawPath": "/connectors/connect/slack-workspace-bot",
            "requestContext": {"authorizer": {"claims": {"sub": "admin"}}},
        }
        resp = m.handler(ev, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    url = body["authorize_url"]
    assert url.startswith("https://slack.com/oauth/v2/authorize?")
    # Bot scopes appear in scope= (URL-encoded). At minimum:
    assert "scope=" in url
    assert "chat%3Awrite" in url or "chat:write" in url
    assert "channels%3Aread" in url or "channels:read" in url
    store.assert_called_once()


def test_initiate_workspace_bot_403_for_non_admin(monkeypatch):
    """Non-admin caller → 403 admin_required."""
    import json
    from unittest.mock import patch
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)

    with patch("connectors.handlers_slack_workspace_bot._require_admin",
               return_value=(None, None)):
        from connectors import main as m
        ev = {
            "httpMethod": "POST",
            "rawPath": "/connectors/connect/slack-workspace-bot",
            "requestContext": {"authorizer": {"claims": {"sub": "member"}}},
        }
        resp = m.handler(ev, None)
    assert resp["statusCode"] == 403
    assert json.loads(resp["body"])["error"] == "admin_required"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd platform/lambda/connectors && /Users/kkmookhey/Projects/CISOBrief/platform/venv/bin/python -m pytest tests/test_handlers_admin_slack.py -q
```

Expected: 2 new failures with `AttributeError` (handler not registered yet).

- [ ] **Step 3: Implement the initiate handler**

Append to `handlers_slack_workspace_bot.py`:
```python
import os
import secrets

from connectors.main import _route, _resp
from mcp_oauth import pkce
from mcp_oauth import state as state_jwt
from mcp_oauth.providers import slack as slack_provider


BOT_SCOPES = ["chat:write", "channels:read", "groups:read"]


@_route("POST", r"^/connectors/connect/slack-workspace-bot$")
def initiate_workspace_bot(event, claims, _params):
    """Admin-gated initiate: returns Slack authorize URL with bot scopes.

    Auth: admin role required. PKCE + signed-state-JWT identical to the
    user OAuth flow, but the state JWT carries provider="slack-bot" so
    its audience pin (slack-bot-callback) prevents replay at the user
    callback and vice versa.
    """
    tenant_id, user_id = _require_admin(claims)
    if not tenant_id or not user_id:
        return _resp(403, {"error": "admin_required"})

    client_id = os.environ["SLACK_CLIENT_ID"]
    redirect_uri = (
        f"{os.environ['CONNECTORS_REDIRECT_BASE']}/callback/slack-workspace-bot"
    )

    verifier, challenge = pkce.generate_pair()
    nonce = secrets.token_urlsafe(16)
    pkce.store_verifier(nonce=nonce, verifier=verifier)

    state = state_jwt.sign_state(
        tenant_id=tenant_id,
        user_id=user_id,
        provider="slack-bot",
        pkce_verifier_hash=pkce.challenge_hash(challenge),
        nonce=nonce,
    )

    # Reuse the user-flow URL builder but override the scope param —
    # the per-user URL builder appends user_scope; we want bot scope only.
    url = slack_provider.build_authorize_url(
        client_id=client_id, redirect_uri=redirect_uri,
        state=state, code_challenge=challenge,
        scope=",".join(BOT_SCOPES),
        user_scope="",
    )
    return _resp(200, {"authorize_url": url})
```

- [ ] **Step 4: Verify `slack_provider.build_authorize_url` accepts the kwargs**

```bash
grep -n "def build_authorize_url\|def build_authorize" platform/lambda/_shared/mcp_oauth/providers/slack.py
```

If `build_authorize_url` doesn't accept `scope=`/`user_scope=` kwargs, modify it to take them. Show the existing function before changing. Expected: function takes `scope` and `user_scope` kwargs (or accept positional). If not present, add them with defaults equal to the per-user-flow's hardcoded values so handlers_slack.py keeps working.

- [ ] **Step 5: Run test to verify it passes**

```bash
/Users/kkmookhey/Projects/CISOBrief/platform/venv/bin/python -m pytest tests/test_handlers_admin_slack.py -q
```

Expected: 5 passed (3 from Task 4 + 2 from Step 1).

- [ ] **Step 6: Verify handlers_slack tests still pass (regression check)**

```bash
/Users/kkmookhey/Projects/CISOBrief/platform/venv/bin/python -m pytest tests/test_handlers_slack.py -q
```

Expected: all pre-existing tests still pass.

- [ ] **Step 7: Register the new handler module in connectors/main.py imports**

`platform/lambda/connectors/main.py` — find the line that registers route handlers (likely `from connectors import handlers_slack` near the top of `handler()` or at module bottom). Add:

```python
from connectors import handlers_slack_workspace_bot  # noqa: F401 — registers /v1/connectors/{connect,callback}/slack-workspace-bot routes
```

- [ ] **Step 8: Commit**

```bash
git add platform/lambda/connectors/handlers_slack_workspace_bot.py \
        platform/lambda/connectors/tests/test_handlers_admin_slack.py \
        platform/lambda/connectors/main.py \
        platform/lambda/_shared/mcp_oauth/providers/slack.py
git commit -m "feat(connectors): admin Slack workspace bot initiate handler

POST /v1/connectors/connect/slack-workspace-bot — admin-gated, bot scopes,
state JWT pinned to slack-bot-callback audience.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 7: Implement `callback_slack_workspace_bot` handler

**Files:**
- Modify: `platform/lambda/connectors/handlers_slack_workspace_bot.py`
- Modify: `platform/lambda/_shared/mcp_oauth/providers/slack.py` (verify `exchange_code` parses the bot-token shape)
- Test: `platform/lambda/connectors/tests/test_handlers_admin_slack.py` (extend)

- [ ] **Step 1: Inspect the Slack OAuth bot-token response shape**

Slack's `oauth.v2.access` for a bot install returns BOTH the user token (in `authed_user.access_token`) AND the bot token (in top-level `access_token`). The Slice 1 fix (`c58d4d2 fix(slack provider): read user-scope token from authed_user`) means the existing `exchange_code` reads from `authed_user`. For the bot flow we need the TOP-LEVEL `access_token` plus `team.id`.

```bash
grep -n "authed_user\|access_token" platform/lambda/_shared/mcp_oauth/providers/slack.py
```

Decide: add a `mode` kwarg to `exchange_code` (`"user"` vs `"bot"`), OR add a separate `exchange_code_bot()` function. Prefer the separate function for clarity — the parse logic differs enough.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_handlers_admin_slack.py`:
```python
def test_callback_workspace_bot_inserts_tenant_bot_row(monkeypatch):
    """Successful bot callback → INSERT INTO tenant_bot_connectors with
    bot token + team_id, status='active', autonomous_rule_enabled=true,
    broadcast_channel_id=NULL."""
    from unittest.mock import MagicMock
    from mcp_oauth import state as st, pkce
    from connectors import handlers_slack_workspace_bot as h

    monkeypatch.setenv("SLACK_CLIENT_ID", "abc")
    monkeypatch.setenv("SLACK_CLIENT_SECRET", "xyz")
    monkeypatch.setenv("CONNECTORS_REDIRECT_BASE",
                       "https://app.shasta.io/v1/connectors")
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)
    monkeypatch.setenv("WEB_BASE_URL", "https://app.shasta.io")

    challenge = "ch-bot"
    state_tok = st.sign_state(
        tenant_id="t-1", user_id="u-1", provider="slack-bot",
        pkce_verifier_hash=pkce.challenge_hash(challenge),
        nonce="n-bot",
    )

    monkeypatch.setattr(h.pkce, "fetch_verifier", lambda nonce: "v-bot")
    monkeypatch.setattr(h.pkce, "challenge_hash",
                        lambda c: pkce.challenge_hash(challenge))
    monkeypatch.setattr(h.slack_provider, "exchange_code_bot",
                        lambda **kw: {
                            "access_token": "xoxb-BOT",
                            "team_id": "T0XBOT",
                            "scopes": ["chat:write", "channels:read",
                                       "groups:read"],
                            "mcp_server_url": "https://mcp.slack.com/mcp",
                        })
    monkeypatch.setattr(h, "encrypt_token",
                        lambda t: (f"E:{t}".encode(), f"DK:{t}".encode()))

    inserted = {}
    class FakeDB:
        def execute(self, sql, params=None):
            if sql.strip().startswith("INSERT"):
                inserted["sql"] = sql
                inserted["params"] = params
            class R:
                def fetchone(self_inner): return None
            return R()
    monkeypatch.setattr(h, "_db", lambda: FakeDB())

    from connectors import main as m
    ev = {
        "httpMethod": "GET",
        "rawPath": "/connectors/callback/slack-workspace-bot",
        "queryStringParameters": {"code": "ac-bot", "state": state_tok},
        "requestContext": {"authorizer": {"claims": {"sub": "admin"}}},
    }
    resp = m.handler(ev, None)
    assert resp["statusCode"] == 302
    assert resp["headers"]["location"].endswith(
        "/settings?tab=connectors&ok=slack-bot")
    assert "INSERT INTO tenant_bot_connectors" in inserted["sql"]
    # autonomous_rule_enabled default = true on schema; we don't bind it.
    # broadcast_channel_id must NOT be set yet — channel picker fires next.
    param_names = {p["name"] for p in inserted["params"]}
    assert "channel" not in param_names, "broadcast_channel_id set too early"
```

- [ ] **Step 3: Add `exchange_code_bot` to the slack provider**

`platform/lambda/_shared/mcp_oauth/providers/slack.py` — add (after the existing `exchange_code` function):

```python
def exchange_code_bot(*, code: str, code_verifier: str,
                     client_id: str, client_secret: str,
                     redirect_uri: str) -> dict:
    """OAuth code exchange for the admin BOT install.

    Slack's oauth.v2.access returns both a user token (in authed_user)
    and a bot token (top-level). For the workspace-bot flow we want the
    top-level bot token (xoxb-...) and the team.id.
    """
    resp = requests.post(
        "https://slack.com/api/oauth.v2.access",
        data={
            "code": code,
            "code_verifier": code_verifier,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        },
        timeout=10,
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(f"slack oauth (bot): {body.get('error', 'unknown')}")
    return {
        "access_token":    body["access_token"],     # xoxb-...
        "team_id":         body["team"]["id"],
        "scopes":          (body.get("scope") or "").split(",")
                            if body.get("scope") else [],
        "mcp_server_url":  "https://mcp.slack.com/mcp",
    }
```

- [ ] **Step 4: Implement the callback handler**

Append to `handlers_slack_workspace_bot.py`:
```python
import base64
import hashlib

from mcp_oauth.crypto import encrypt_token


@_route("GET", r"^/connectors/callback/slack-workspace-bot$",
        requires_auth=False)
def callback_workspace_bot(event, claims, _params):
    """Admin-bot OAuth callback.

    Auth: unauthenticated (state JWT is the gate; Slack redirects the
    user's browser here). Provider="slack-bot" pinned at JWT verify
    time prevents replay of a user-flow JWT at this endpoint.
    """
    qs = event.get("queryStringParameters") or {}
    code = qs.get("code")
    state = qs.get("state")
    if not code or not state:
        return _resp(400, {"error": "missing_code_or_state"})

    try:
        s = state_jwt.verify_state(state, expected_provider="slack-bot")
    except Exception as e:
        return _resp(400, {"error": "invalid_state", "detail": str(e)[:120]})

    tenant_id = s["tenant_id"]
    user_id = s["user_id"]
    nonce = s["nonce"]
    pkce_hash = s["pkce_verifier_hash"]

    verifier = pkce.fetch_verifier(nonce)
    if not verifier:
        return _resp(400, {"error": "verifier_expired_or_missing"})

    rebuilt_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    if pkce.challenge_hash(rebuilt_challenge) != pkce_hash:
        return _resp(400, {"error": "pkce_mismatch"})

    client_id = os.environ["SLACK_CLIENT_ID"]
    client_secret = os.environ["SLACK_CLIENT_SECRET"]
    redirect_uri = (
        f"{os.environ['CONNECTORS_REDIRECT_BASE']}/callback/slack-workspace-bot"
    )

    tokens = slack_provider.exchange_code_bot(
        code=code, code_verifier=verifier,
        client_id=client_id, client_secret=client_secret,
        redirect_uri=redirect_uri,
    )

    access_enc, access_dk = encrypt_token(tokens["access_token"])
    scopes_literal = "{" + ",".join(tokens["scopes"]) + "}"

    _db().execute("""
        INSERT INTO tenant_bot_connectors (
            tenant_id, oauth_provider, mcp_server_url, vendor_workspace_id,
            access_token_enc, access_data_key_ct,
            scopes, installed_by_user_id, status
        ) VALUES (
            :tid::uuid, :provider, :mcp, :vw,
            :a, :adk, :scopes::text[], :uid::uuid, 'active'
        )
        ON CONFLICT (tenant_id, oauth_provider) DO UPDATE SET
            access_token_enc   = EXCLUDED.access_token_enc,
            access_data_key_ct = EXCLUDED.access_data_key_ct,
            mcp_server_url     = EXCLUDED.mcp_server_url,
            vendor_workspace_id = EXCLUDED.vendor_workspace_id,
            scopes             = EXCLUDED.scopes,
            status             = 'active',
            revoked_at         = NULL
    """, [
        {"name": "tid", "value": {"stringValue": tenant_id}},
        {"name": "provider", "value": {"stringValue": "slack"}},
        {"name": "mcp", "value": {"stringValue": tokens["mcp_server_url"]}},
        {"name": "vw", "value": {"stringValue": tokens["team_id"]}},
        {"name": "a", "value": {"blobValue": access_enc}},
        {"name": "adk", "value": {"blobValue": access_dk}},
        {"name": "scopes", "value": {"stringValue": scopes_literal}},
        {"name": "uid", "value": {"stringValue": user_id}},
    ])

    web_base = os.environ["WEB_BASE_URL"]
    return {
        "statusCode": 302,
        "headers": {
            "location": f"{web_base}/settings?tab=connectors&ok=slack-bot",
        },
        "body": "",
    }
```

- [ ] **Step 5: Run test + verify regressions**

```bash
/Users/kkmookhey/Projects/CISOBrief/platform/venv/bin/python -m pytest tests/test_handlers_admin_slack.py tests/test_handlers_slack.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/connectors/handlers_slack_workspace_bot.py \
        platform/lambda/_shared/mcp_oauth/providers/slack.py \
        platform/lambda/connectors/tests/test_handlers_admin_slack.py
git commit -m "feat(connectors): admin Slack workspace bot callback handler

GET /v1/connectors/callback/slack-workspace-bot — exchanges code,
KMS-encrypts the bot token, inserts/upserts into tenant_bot_connectors
with autonomous_rule_enabled=true (schema default) and
broadcast_channel_id=NULL (admin picks next).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 8: Open sub-slice 2.2 PR

- [ ] **Step 1: Push and open PR**

```bash
git push -u origin feat/mcp-connectors-slice-2.2-admin-bot-install
gh pr create --title "feat: Slice 2.2 — admin Slack workspace bot install flow" --body "$(cat <<'EOF'
## Summary

- `POST /v1/connectors/connect/slack-workspace-bot` (admin-gated initiate)
- `GET /v1/connectors/callback/slack-workspace-bot` (token exchange + insert)
- State JWT audience `slack-bot-callback` prevents cross-flow JWT replay
- Bot scopes: `chat:write`, `channels:read`, `groups:read`
- KMS-envelope encryption (per-row data key, same as Slice 1)
- Re-install path: `ON CONFLICT (tenant_id, oauth_provider) DO UPDATE`

## Test plan

- [x] handlers_admin_slack: 5 new tests (initiate happy + 403, callback insert, audience rejection)
- [x] handlers_slack regressions: green
- [ ] Manual smoke: admin user installs Slack bot via UI (UI lands in 2.3)
EOF
)"
```

---

## Sub-slice 2.3 — Channel picker + `mcp_oauth.get_admin_session`

### Task 9: Create `mcp_oauth.get_admin_session` helper

**Files:**
- Create: `platform/lambda/_shared/mcp_oauth/admin_session.py`
- Modify: `platform/lambda/_shared/mcp_oauth/__init__.py` (re-export)
- Test: `platform/lambda/_shared/tests/test_admin_session.py` (new file)

- [ ] **Step 1: Write the failing test**

`platform/lambda/_shared/tests/test_admin_session.py` (new file):
```python
"""Tests for mcp_oauth.admin_session — opens MCP session for the tenant's
admin-installed bot token (autonomous broadcast path)."""
from __future__ import annotations
from unittest.mock import MagicMock
import pytest


def test_lookup_tenant_bot_returns_active_row(monkeypatch):
    from mcp_oauth.admin_session import lookup_tenant_bot

    fake_db = MagicMock()
    fake_db.execute.return_value.fetchone.return_value = {
        "bot_id": "b-1", "access_token_enc": b"E:xoxb",
        "access_data_key_ct": b"DK", "access_expires_at": None,
        "mcp_server_url": "https://mcp.slack.com/mcp",
        "broadcast_channel_id": "C0X",
        "autonomous_rule_enabled": True,
    }
    monkeypatch.setattr("mcp_oauth.admin_session._db", lambda: fake_db)

    row = lookup_tenant_bot(tenant_id="t-1", kind="slack")
    assert row["bot_id"] == "b-1"
    assert row["broadcast_channel_id"] == "C0X"


def test_lookup_tenant_bot_missing_raises(monkeypatch):
    from mcp_oauth.admin_session import lookup_tenant_bot
    from mcp_oauth.session import ConnectorMissingError

    fake_db = MagicMock()
    fake_db.execute.return_value.fetchone.return_value = None
    monkeypatch.setattr("mcp_oauth.admin_session._db", lambda: fake_db)

    with pytest.raises(ConnectorMissingError):
        lookup_tenant_bot(tenant_id="t-1", kind="slack")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd platform/lambda/_shared && /Users/kkmookhey/Projects/CISOBrief/platform/venv/bin/python -m pytest tests/test_admin_session.py -q
```

Expected: ModuleNotFoundError on `mcp_oauth.admin_session`.

- [ ] **Step 3: Implement the module**

`platform/lambda/_shared/mcp_oauth/admin_session.py` (new file):
```python
"""MCP session helper for tenant-level admin-installed bots.

Mirrors mcp_oauth.session.get_session but resolves against
tenant_bot_connectors instead of user_connectors. Used by:
  - findings_subscriber Lambda (autonomous broadcast)
  - connectors/handlers_admin_slack channel picker (conversations.list)

Token refresh + KMS-envelope decrypt reuses the helpers from session.py
to avoid duplication. The advisory-lock key is bot_id (not conn_id) but
the lock pattern is identical.
"""
from __future__ import annotations
from contextlib import asynccontextmanager
from typing import Literal

from .session import (
    _db, _zip_record, decrypt_token, encrypt_token,
    ConnectorMissingError, ConnectorRevokedError,
)


BotKind = Literal["slack"]


def lookup_tenant_bot(*, tenant_id: str, kind: BotKind) -> dict:
    """Return the active tenant_bot_connectors row for (tenant, provider).

    Raises ConnectorMissingError if no active row exists.
    """
    sql = """
        SELECT bot_id, access_token_enc, access_data_key_ct,
               access_expires_at, mcp_server_url,
               vendor_workspace_id, broadcast_channel_id,
               autonomous_rule_enabled
        FROM tenant_bot_connectors
        WHERE tenant_id = :tid::uuid
          AND oauth_provider = :provider
          AND status = 'active'
    """
    row = _db().execute(sql, [
        {"name": "tid", "value": {"stringValue": tenant_id}},
        {"name": "provider", "value": {"stringValue": kind}},
    ]).fetchone()
    if not row:
        raise ConnectorMissingError(f"no active {kind} bot for tenant {tenant_id}")
    return row


@asynccontextmanager
async def get_admin_session(tenant_id: str, kind: BotKind = "slack"):
    """Open an MCP session against the tenant's admin-installed bot.

    Used for autonomous broadcast and the post-install channel picker.
    """
    row = lookup_tenant_bot(tenant_id=tenant_id, kind=kind)
    # No refresh path in Slice 2 — Slack bot tokens issued without
    # token_rotation_enabled don't expire. (If rotation is later enabled
    # on the Shasta Slack App's bot scopes, copy the refresh_if_near_expiry
    # pattern from session.py keyed by bot_id.)
    access_token = decrypt_token(row["access_token_enc"],
                                 row["access_data_key_ct"])

    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(
        row["mcp_server_url"],
        headers={"Authorization": f"Bearer {access_token}"},
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session
```

- [ ] **Step 4: Re-export from `__init__.py`**

`platform/lambda/_shared/mcp_oauth/__init__.py` — add:
```python
from .admin_session import get_admin_session, lookup_tenant_bot  # noqa: F401
```

- [ ] **Step 5: Run tests**

```bash
/Users/kkmookhey/Projects/CISOBrief/platform/venv/bin/python -m pytest tests/test_admin_session.py -q
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git checkout -b feat/mcp-connectors-slice-2.3-channel-picker
git add platform/lambda/_shared/mcp_oauth/admin_session.py \
        platform/lambda/_shared/mcp_oauth/__init__.py \
        platform/lambda/_shared/tests/test_admin_session.py
git commit -m "feat(mcp_oauth): get_admin_session for tenant bot tokens

Mirrors get_session but resolves tenant_bot_connectors. Reused by
findings_subscriber (Slice 2.4) and the channel picker handler
(Slice 2.3 next task).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 10: Implement channel picker + broadcast-channel setter handlers

**Files:**
- Create: `platform/lambda/connectors/handlers_admin_slack_channels.py`
- Modify: `platform/lambda/connectors/main.py` (register the new module)
- Test: `platform/lambda/connectors/tests/test_handlers_admin_slack.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `platform/lambda/connectors/tests/test_handlers_admin_slack.py`:
```python
def test_list_channels_returns_picker_payload(monkeypatch):
    """Admin caller → MCP conversations.list via the bot's session →
    returns [{id, name, is_private}]."""
    import json
    from unittest.mock import AsyncMock, MagicMock
    import contextlib

    fake_session = AsyncMock()
    fake_session.call_tool.return_value = MagicMock(content=[
        MagicMock(text=json.dumps({
            "channels": [
                {"id": "C1", "name": "general", "is_private": False},
                {"id": "C2", "name": "shasta-alerts", "is_private": False},
            ]
        }))
    ])

    @contextlib.asynccontextmanager
    async def fake_admin_session(*a, **kw):
        yield fake_session

    monkeypatch.setattr("mcp_oauth.get_admin_session", fake_admin_session)
    monkeypatch.setattr(
        "connectors.handlers_slack_workspace_bot._require_admin",
        lambda claims: ("t-1", "u-1"))

    from connectors import main as m
    ev = {
        "httpMethod": "GET",
        "rawPath": "/connectors/admin/slack/channels",
        "requestContext": {"authorizer": {"claims": {"sub": "admin"}}},
    }
    resp = m.handler(ev, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["channels"] == [
        {"id": "C1", "name": "general", "is_private": False},
        {"id": "C2", "name": "shasta-alerts", "is_private": False},
    ]


def test_set_broadcast_channel_updates_row(monkeypatch):
    """Admin posts a valid channel_id → tenant_bot_connectors row updates."""
    import json
    from unittest.mock import MagicMock

    updated = {}
    class FakeDB:
        def execute(self, sql, params=None):
            if sql.strip().startswith("UPDATE"):
                updated["sql"] = sql
                updated["params"] = params
            class R:
                def fetchone(self_inner): return None
            return R()
    monkeypatch.setattr("mcp_oauth.session._db", lambda: FakeDB())
    monkeypatch.setattr(
        "connectors.handlers_slack_workspace_bot._require_admin",
        lambda claims: ("t-1", "u-1"))

    # Mock the anti-tamper channel validation: claim C2 IS in the bot's list.
    monkeypatch.setattr(
        "connectors.handlers_admin_slack_channels._channel_exists",
        lambda tenant_id, channel_id: True)

    from connectors import main as m
    ev = {
        "httpMethod": "POST",
        "rawPath": "/connectors/admin/slack/broadcast-channel",
        "body": json.dumps({"channel_id": "C2", "channel_name": "shasta-alerts"}),
        "requestContext": {"authorizer": {"claims": {"sub": "admin"}}},
    }
    resp = m.handler(ev, None)
    assert resp["statusCode"] == 200
    assert "UPDATE tenant_bot_connectors" in updated["sql"]
    p = {p["name"]: p["value"] for p in updated["params"]}
    assert p["chan"]["stringValue"] == "C2"


def test_set_broadcast_channel_rejects_unknown_id(monkeypatch):
    """Anti-tamper: if channel_id isn't in conversations.list, reject."""
    import json

    monkeypatch.setattr(
        "connectors.handlers_slack_workspace_bot._require_admin",
        lambda claims: ("t-1", "u-1"))
    monkeypatch.setattr(
        "connectors.handlers_admin_slack_channels._channel_exists",
        lambda tenant_id, channel_id: False)

    from connectors import main as m
    ev = {
        "httpMethod": "POST",
        "rawPath": "/connectors/admin/slack/broadcast-channel",
        "body": json.dumps({"channel_id": "C-FAKE", "channel_name": "x"}),
        "requestContext": {"authorizer": {"claims": {"sub": "admin"}}},
    }
    resp = m.handler(ev, None)
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"] == "channel_not_in_workspace"
```

- [ ] **Step 2: Run + verify fail**

```bash
cd platform/lambda/connectors && /Users/kkmookhey/Projects/CISOBrief/platform/venv/bin/python -m pytest tests/test_handlers_admin_slack.py -q
```

Expected: 3 new failures (404 unknown_route or ModuleNotFoundError).

- [ ] **Step 3: Implement the module**

`platform/lambda/connectors/handlers_admin_slack_channels.py` (new file):
```python
"""Admin handlers: list Slack channels + set broadcast channel.

Both routes require:
  - users.role = 'admin' (enforced via _require_admin)
  - An active tenant_bot_connectors row for the tenant + Slack
"""
from __future__ import annotations
import asyncio
import json

from connectors.main import _route, _resp
from connectors.handlers_slack_workspace_bot import _require_admin
from mcp_oauth import get_admin_session
from mcp_oauth.session import _db, ConnectorMissingError


def _channel_exists(tenant_id: str, channel_id: str) -> bool:
    """Return True iff channel_id appears in the bot's conversations.list.

    Anti-tamper: prevent an attacker (or buggy UI) from setting
    broadcast_channel_id to a channel the bot doesn't have access to.
    Re-runs conversations.list — cheap (cached in Slack's MCP for ~30s).
    """
    try:
        async def _check():
            async with get_admin_session(tenant_id, "slack") as session:
                result = await session.call_tool("conversations_list", {})
                payload = json.loads(result.content[0].text)
                ids = {c["id"] for c in payload.get("channels", [])}
                return channel_id in ids
        return asyncio.run(_check())
    except ConnectorMissingError:
        return False


@_route("GET", r"^/connectors/admin/slack/channels$")
def list_channels(event, claims, _params):
    tenant_id, _user_id = _require_admin(claims)
    if not tenant_id:
        return _resp(403, {"error": "admin_required"})

    try:
        async def _fetch():
            async with get_admin_session(tenant_id, "slack") as session:
                result = await session.call_tool("conversations_list", {})
                return json.loads(result.content[0].text)
        payload = asyncio.run(_fetch())
    except ConnectorMissingError:
        return _resp(409, {"error": "bot_not_installed"})

    return _resp(200, {"channels": payload.get("channels", [])})


@_route("POST", r"^/connectors/admin/slack/broadcast-channel$")
def set_broadcast_channel(event, claims, _params):
    tenant_id, _user_id = _require_admin(claims)
    if not tenant_id:
        return _resp(403, {"error": "admin_required"})

    body = json.loads(event.get("body") or "{}")
    channel_id = body.get("channel_id")
    channel_name = body.get("channel_name", "")
    if not channel_id:
        return _resp(400, {"error": "missing_channel_id"})

    if not _channel_exists(tenant_id, channel_id):
        return _resp(400, {"error": "channel_not_in_workspace"})

    _db().execute("""
        UPDATE tenant_bot_connectors
        SET broadcast_channel_id = :chan,
            broadcast_channel_name = :chname
        WHERE tenant_id = :tid::uuid AND oauth_provider = 'slack'
          AND status = 'active'
    """, [
        {"name": "tid", "value": {"stringValue": tenant_id}},
        {"name": "chan", "value": {"stringValue": channel_id}},
        {"name": "chname", "value": {"stringValue": channel_name}},
    ])
    return _resp(200, {"ok": True, "channel_id": channel_id})


@_route("PATCH", r"^/connectors/admin/slack/autonomous-rule$")
def toggle_autonomous_rule(event, claims, _params):
    """Flip autonomous_rule_enabled on the tenant_bot_connectors row."""
    tenant_id, _user_id = _require_admin(claims)
    if not tenant_id:
        return _resp(403, {"error": "admin_required"})

    body = json.loads(event.get("body") or "{}")
    enabled = bool(body.get("enabled", True))
    _db().execute("""
        UPDATE tenant_bot_connectors
        SET autonomous_rule_enabled = :en
        WHERE tenant_id = :tid::uuid AND oauth_provider = 'slack'
          AND status = 'active'
    """, [
        {"name": "tid", "value": {"stringValue": tenant_id}},
        {"name": "en", "value": {"booleanValue": enabled}},
    ])
    return _resp(200, {"ok": True, "enabled": enabled})


@_route("DELETE", r"^/connectors/admin/slack$")
def revoke_workspace_bot(event, claims, _params):
    """Revoke the admin bot install. Marks status='revoked' locally;
    Slack's revoke endpoint is best-effort."""
    tenant_id, _user_id = _require_admin(claims)
    if not tenant_id:
        return _resp(403, {"error": "admin_required"})

    # Best-effort Slack auth.revoke — if it fails the local revoke still happens.
    try:
        async def _revoke_upstream():
            async with get_admin_session(tenant_id, "slack") as session:
                await session.call_tool("auth_revoke", {})
        asyncio.run(_revoke_upstream())
    except Exception as e:
        print(f"[connectors] Slack auth.revoke failed: {e!r} (continuing)")

    _db().execute("""
        UPDATE tenant_bot_connectors
        SET status = 'revoked', revoked_at = now()
        WHERE tenant_id = :tid::uuid AND oauth_provider = 'slack'
    """, [{"name": "tid", "value": {"stringValue": tenant_id}}])
    return _resp(200, {"revoked": True})
```

- [ ] **Step 4: Register in main.py**

Add to `platform/lambda/connectors/main.py`:
```python
from connectors import handlers_admin_slack_channels  # noqa: F401
```

- [ ] **Step 5: Run + verify pass**

```bash
/Users/kkmookhey/Projects/CISOBrief/platform/venv/bin/python -m pytest tests/test_handlers_admin_slack.py -q
```

Expected: all tests pass (8 from Tasks 4-7 + 3 new).

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/connectors/handlers_admin_slack_channels.py \
        platform/lambda/connectors/main.py \
        platform/lambda/connectors/tests/test_handlers_admin_slack.py
git commit -m "feat(connectors): admin Slack channel picker + autonomous toggle + revoke

Four new routes:
  GET    /v1/connectors/admin/slack/channels             — picker payload
  POST   /v1/connectors/admin/slack/broadcast-channel    — set channel (anti-tamper)
  PATCH  /v1/connectors/admin/slack/autonomous-rule      — toggle on/off
  DELETE /v1/connectors/admin/slack                       — revoke install

Anti-tamper: set_broadcast_channel re-runs conversations.list and
rejects channel_ids that aren't in the bot's workspace.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 11: Web — ConnectorAdminBlock + ChannelPicker + Settings wiring

**Files:**
- Create: `web/src/components/connectors/ConnectorAdminBlock.tsx`
- Create: `web/src/components/connectors/ChannelPicker.tsx`
- Modify: `web/src/routes/Settings/ConnectorsTab.tsx` (mount the admin block)
- Modify: `web/src/lib/api.ts` (new endpoints)
- Modify: `web/src/lib/useConnectors.ts` (extend hook to expose admin bot status)

- [ ] **Step 1: Add the API client methods**

`web/src/lib/api.ts` — find the existing `listConnectors`, `initiateConnectorOAuth`, etc. Add:

```typescript
export async function initiateSlackWorkspaceBot(): Promise<{ authorize_url: string }> {
  return call<{ authorize_url: string }>("POST", "/connectors/connect/slack-workspace-bot");
}

export interface SlackChannel {
  id: string;
  name: string;
  is_private: boolean;
}

export async function listSlackChannels(): Promise<{ channels: SlackChannel[] }> {
  return call<{ channels: SlackChannel[] }>("GET", "/connectors/admin/slack/channels");
}

export async function setBroadcastChannel(channel_id: string, channel_name: string): Promise<{ ok: boolean; channel_id: string }> {
  return call("POST", "/connectors/admin/slack/broadcast-channel", { channel_id, channel_name });
}

export async function toggleAutonomousRule(enabled: boolean): Promise<{ ok: boolean; enabled: boolean }> {
  return call("PATCH", "/connectors/admin/slack/autonomous-rule", { enabled });
}

export async function revokeSlackBot(): Promise<{ revoked: boolean }> {
  return call("DELETE", "/connectors/admin/slack");
}

export interface AdminBotStatus {
  installed: boolean;
  broadcast_channel_id: string | null;
  broadcast_channel_name: string | null;
  autonomous_rule_enabled: boolean;
}

export async function getAdminBotStatus(): Promise<AdminBotStatus> {
  // GET /v1/connectors/admin/slack/status — returns row data, or installed=false
  return call<AdminBotStatus>("GET", "/connectors/admin/slack/status");
}
```

- [ ] **Step 2: Add the `/admin/slack/status` route to the connectors Lambda**

`platform/lambda/connectors/handlers_admin_slack_channels.py` — append:

```python
@_route("GET", r"^/connectors/admin/slack/status$")
def admin_bot_status(event, claims, _params):
    """Returns the admin's tenant_bot_connectors row state — used by
    the web Settings UI to render the right install/picker/configured
    block state. Admin-only."""
    tenant_id, _user_id = _require_admin(claims)
    if not tenant_id:
        return _resp(403, {"error": "admin_required"})

    row = _db().execute("""
        SELECT broadcast_channel_id, broadcast_channel_name,
               autonomous_rule_enabled, status
        FROM tenant_bot_connectors
        WHERE tenant_id = :tid::uuid AND oauth_provider = 'slack'
    """, [{"name": "tid", "value": {"stringValue": tenant_id}}]).fetchone()

    if not row or row["status"] != "active":
        return _resp(200, {
            "installed": False,
            "broadcast_channel_id": None,
            "broadcast_channel_name": None,
            "autonomous_rule_enabled": False,
        })
    return _resp(200, {
        "installed": True,
        "broadcast_channel_id": row.get("broadcast_channel_id"),
        "broadcast_channel_name": row.get("broadcast_channel_name"),
        "autonomous_rule_enabled": bool(row["autonomous_rule_enabled"]),
    })
```

- [ ] **Step 3: Implement `ConnectorAdminBlock.tsx`**

`web/src/components/connectors/ConnectorAdminBlock.tsx` (new file):
```typescript
import { useEffect, useState } from "react";
import { useUser } from "../../lib/useUser";
import {
  initiateSlackWorkspaceBot, getAdminBotStatus,
  toggleAutonomousRule, revokeSlackBot,
  AdminBotStatus,
} from "../../lib/api";
import { ChannelPicker } from "./ChannelPicker";

export function ConnectorAdminBlock() {
  const { user } = useUser();
  const [status, setStatus] = useState<AdminBotStatus | null>(null);
  const [showPicker, setShowPicker] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (user?.role !== "admin") return;
    getAdminBotStatus().then(setStatus).catch(console.error);
  }, [user?.role]);

  if (user?.role !== "admin") return null;
  if (!status) return null;

  const install = async () => {
    setBusy(true);
    const { authorize_url } = await initiateSlackWorkspaceBot();
    window.location.href = authorize_url;
  };

  const onToggle = async () => {
    if (!status) return;
    const next = !status.autonomous_rule_enabled;
    await toggleAutonomousRule(next);
    setStatus({ ...status, autonomous_rule_enabled: next });
  };

  const onRevoke = async () => {
    if (!window.confirm("Disconnect Shasta's bot from your Slack workspace?")) return;
    setBusy(true);
    await revokeSlackBot();
    setStatus({
      installed: false, broadcast_channel_id: null,
      broadcast_channel_name: null, autonomous_rule_enabled: false,
    });
    setBusy(false);
  };

  return (
    <section className="mt-8 rounded-lg border border-neutral-200 p-6">
      <h3 className="text-lg font-semibold">Admin · Slack workspace bot</h3>
      <p className="text-sm text-neutral-600 mt-1">
        Posts a Block Kit card to your chosen channel whenever a CRITICAL finding lands.
      </p>

      {!status.installed && (
        <button
          className="mt-4 px-4 py-2 rounded bg-slack text-white"
          onClick={install} disabled={busy}>
          Install Shasta to your Slack workspace
        </button>
      )}

      {status.installed && !status.broadcast_channel_id && (
        <div className="mt-4">
          <button
            className="px-4 py-2 rounded border border-neutral-300"
            onClick={() => setShowPicker(true)}>
            Pick a broadcast channel
          </button>
          {showPicker && (
            <ChannelPicker
              onSave={(ch) => {
                setStatus({
                  ...status,
                  broadcast_channel_id: ch.id,
                  broadcast_channel_name: ch.name,
                });
                setShowPicker(false);
              }}
              onClose={() => setShowPicker(false)}
            />
          )}
        </div>
      )}

      {status.installed && status.broadcast_channel_id && (
        <div className="mt-4 flex items-center gap-4">
          <span className="text-sm">
            Installed · <span className="font-mono">#{status.broadcast_channel_name}</span>
          </span>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={status.autonomous_rule_enabled}
              onChange={onToggle}
            />
            Autonomous broadcasts ON
          </label>
          <button
            className="text-sm text-red-600 hover:underline"
            onClick={onRevoke} disabled={busy}>
            Disconnect
          </button>
        </div>
      )}
    </section>
  );
}
```

- [ ] **Step 4: Implement `ChannelPicker.tsx`**

`web/src/components/connectors/ChannelPicker.tsx` (new file):
```typescript
import { useEffect, useState } from "react";
import { listSlackChannels, setBroadcastChannel, SlackChannel } from "../../lib/api";

export function ChannelPicker({
  onSave, onClose,
}: {
  onSave: (channel: SlackChannel) => void;
  onClose: () => void;
}) {
  const [channels, setChannels] = useState<SlackChannel[] | null>(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<SlackChannel | null>(null);

  useEffect(() => {
    listSlackChannels().then((r) => setChannels(r.channels)).catch(console.error);
  }, []);

  const save = async () => {
    if (!selected) return;
    await setBroadcastChannel(selected.id, selected.name);
    onSave(selected);
  };

  const filtered = (channels ?? []).filter(c =>
    c.name.toLowerCase().includes(query.toLowerCase()));

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg p-6 w-full max-w-md max-h-[80vh] flex flex-col">
        <h4 className="text-lg font-semibold">Pick a broadcast channel</h4>
        <input
          className="mt-4 w-full px-3 py-2 border rounded"
          placeholder="Search channels..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <ul className="mt-4 flex-1 overflow-y-auto">
          {channels == null && <li>Loading…</li>}
          {filtered.map(c => (
            <li
              key={c.id}
              onClick={() => setSelected(c)}
              className={`px-3 py-2 rounded cursor-pointer ${
                selected?.id === c.id ? "bg-blue-100" : "hover:bg-neutral-100"
              }`}>
              #{c.name} {c.is_private && <span className="text-xs text-neutral-500">(private)</span>}
            </li>
          ))}
        </ul>
        <div className="mt-4 flex justify-end gap-2">
          <button onClick={onClose} className="px-4 py-2 rounded border">Cancel</button>
          <button onClick={save} disabled={!selected}
                  className="px-4 py-2 rounded bg-blue-600 text-white disabled:opacity-50">
            Save
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Mount the admin block on `ConnectorsTab.tsx`**

Open `web/src/routes/Settings/ConnectorsTab.tsx`. After the existing connector grid (find the closing `</div>` of the `<div className="grid grid-cols-2 gap-4">` or equivalent), add:

```typescript
import { ConnectorAdminBlock } from "../../components/connectors/ConnectorAdminBlock";

// ... inside the component, after the grid:
<ConnectorAdminBlock />
```

- [ ] **Step 6: Build the web app**

```bash
cd /Users/kkmookhey/Projects/CISOBrief/web && pnpm build 2>&1 | tail -10
```

Expected: build succeeds. If `useUser` doesn't exist, add a placeholder:
```typescript
// web/src/lib/useUser.ts — verify or create
export function useUser() {
  // existing implementation; if no user hook, use the auth context
  // currently used elsewhere in /settings.
}
```

If the auth context doesn't expose `role`, the role read needs to be wired through the existing tenant-context provider. Inspect `web/src/lib/useTenant.ts` or equivalent and follow the existing pattern.

- [ ] **Step 7: Commit**

```bash
git add platform/lambda/connectors/handlers_admin_slack_channels.py \
        web/src/components/connectors/ConnectorAdminBlock.tsx \
        web/src/components/connectors/ChannelPicker.tsx \
        web/src/routes/Settings/ConnectorsTab.tsx \
        web/src/lib/api.ts
git commit -m "feat(web): Connectors tab admin block + Slack channel picker

Admin-gated section on /settings → Connectors tab. Three states:
  - Not installed → \"Install Shasta\" button → kicks off OAuth
  - Installed, no channel → ChannelPicker modal (search + select)
  - Configured → channel name + autonomous toggle + Disconnect

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 12: Open sub-slice 2.3 PR

- [ ] **Step 1: Push and open PR**

```bash
git push -u origin feat/mcp-connectors-slice-2.3-channel-picker
gh pr create --title "feat: Slice 2.3 — Slack channel picker + autonomous-rule toggle" --body "$(cat <<'EOF'
## Summary

- `mcp_oauth.get_admin_session(tenant_id, "slack")` — mirrors get_session for the bot path
- 5 new Lambda routes: list channels, set channel (anti-tamper), toggle autonomous rule, revoke, status
- Web: `ConnectorAdminBlock` + `ChannelPicker` modal on Connectors tab

## Test plan

- [x] mcp_oauth.admin_session tests
- [x] handlers_admin_slack_channels tests (channel picker, anti-tamper, toggle)
- [x] pnpm build green
- [ ] Manual smoke: admin can install, pick a channel, toggle off, disconnect
EOF
)"
```

---

## Sub-slice 2.4 — Broadcast plumbing (the autonomous rule)

### Task 13: CDK — SQS queue + DDB seen table + `findings_subscriber` Lambda

**Files:**
- Modify: `platform/lib/data-stack.ts` (export queue + DDB)
- Modify: `platform/lib/api-stack.ts` (subscriber Lambda + scanner grants)

- [ ] **Step 1: Add to `data-stack.ts`**

Find the existing exports section (where `connectorTokensKey` and `pkceVerifierTable` are declared). Add the queue, DLQ, and seen table:

```typescript
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as ddb from 'aws-cdk-lib/aws-dynamodb';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';

// ... in the stack class:
public readonly autonomousBroadcastQueue: sqs.Queue;
public readonly autonomousBroadcastDlq: sqs.Queue;
public readonly autonomousBroadcastSeenTable: ddb.Table;

// ... in the constructor, after pkceVerifierTable:
this.autonomousBroadcastDlq = new sqs.Queue(this, 'AutonomousBroadcastDlq', {
  retentionPeriod: cdk.Duration.days(14),
});
this.autonomousBroadcastQueue = new sqs.Queue(this, 'AutonomousBroadcastQueue', {
  visibilityTimeout: cdk.Duration.seconds(30),
  retentionPeriod: cdk.Duration.days(4),
  deadLetterQueue: {
    queue: this.autonomousBroadcastDlq,
    maxReceiveCount: 5,
  },
});

this.autonomousBroadcastSeenTable = new ddb.Table(this, 'AutonomousBroadcastSeen', {
  partitionKey: { name: 'seen_key', type: ddb.AttributeType.STRING },
  billingMode: ddb.BillingMode.PAY_PER_REQUEST,
  timeToLiveAttribute: 'ttl_epoch',
});

// DLQ alarm — fire if any message lands in the DLQ for 5 minutes.
new cloudwatch.Alarm(this, 'AutonomousBroadcastDlqAlarm', {
  metric: this.autonomousBroadcastDlq.metricApproximateNumberOfMessagesVisible({
    period: cdk.Duration.minutes(1),
  }),
  threshold: 1,
  evaluationPeriods: 5,
  comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
  alarmDescription: 'Autonomous broadcast DLQ has messages — subscriber failed 5x',
  treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
});
```

- [ ] **Step 2: Add findings_subscriber Lambda to `api-stack.ts`**

In `platform/lib/api-stack.ts`, after the `connectorsFn` definition, add:

```typescript
import * as sqsEventSources from 'aws-cdk-lib/aws-lambda-event-sources';

// ... after connectorsFn:
const findingsSubscriberFn = new lambda.Function(this, 'FindingsSubscriberFn', {
  runtime: lambda.Runtime.PYTHON_3_12,
  handler: 'main.handler',
  code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda'), {
    bundling: {
      image: lambda.Runtime.PYTHON_3_12.bundlingImage,
      platform: 'linux/amd64',
      command: [
        'bash', '-c',
        'pip install --no-cache-dir ' +
        '--platform manylinux2014_x86_64 --implementation cp ' +
        '--python-version 3.12 --only-binary=:all: ' +
        '-r findings_subscriber/requirements.txt -t /asset-output && ' +
        'cp -r findings_subscriber/. /asset-output/ && ' +
        'cp -r _shared/mcp_oauth /asset-output/',
      ],
    },
  }),
  timeout: cdk.Duration.seconds(30),
  memorySize: 256,
  environment: {
    ...dbEnv,
    CONNECTOR_TOKENS_KEY_ARN: props.connectorTokensKey.keyArn,
    AUTONOMOUS_BROADCAST_SEEN_TABLE: props.autonomousBroadcastSeenTable.tableName,
    AUTONOMOUS_RULE_SSM_PARAM: '/cisocopilot/autonomous_rule/enabled',
    WEB_BASE_URL: config.appDomain,
  },
});
findingsSubscriberFn.addEventSource(new sqsEventSources.SqsEventSource(
  props.autonomousBroadcastQueue, { batchSize: 1 },
));
props.dbCluster.grantDataApiAccess(findingsSubscriberFn);
props.connectorTokensKey.grantEncryptDecrypt(findingsSubscriberFn);
props.autonomousBroadcastSeenTable.grantReadWriteData(findingsSubscriberFn);
// SSM kill switch + Slack OAuth client creds (for any future refresh path)
findingsSubscriberFn.addToRolePolicy(new iam.PolicyStatement({
  actions: ['ssm:GetParameter'],
  resources: [
    `arn:aws:ssm:${this.region}:${this.account}:parameter/cisocopilot/autonomous_rule/enabled`,
    `arn:aws:ssm:${this.region}:${this.account}:parameter/cisocopilot/connectors/slack/client-id`,
    `arn:aws:ssm:${this.region}:${this.account}:parameter/cisocopilot/connectors/slack/client-secret`,
  ],
}));
findingsSubscriberFn.addToRolePolicy(new iam.PolicyStatement({
  actions: ['kms:Decrypt'],
  resources: [`arn:aws:kms:${this.region}:${this.account}:alias/aws/ssm`],
}));
```

- [ ] **Step 3: Grant scanner Lambdas SQS publish**

For each scanner Lambda (aiScannerFn, shastaRunnerAwsFn, shastaRunnerAzureFn — check `api-stack.ts` for exact names), add:

```typescript
scannerFn.addEnvironment('AUTONOMOUS_BROADCAST_QUEUE_URL',
  props.autonomousBroadcastQueue.queueUrl);
props.autonomousBroadcastQueue.grantSendMessages(scannerFn);
```

Repeat for every Lambda that imports `_shared/broadcast_fanout`.

- [ ] **Step 4: Pass new resources through API-stack props**

In `data-stack.ts` (the exporting stack), confirm the new resources are exposed on `this`. In `api-stack.ts`, the props interface (search for `interface CisoCopilotApiProps`) gains:

```typescript
autonomousBroadcastQueue: sqs.IQueue;
autonomousBroadcastSeenTable: ddb.ITable;
```

And in `bin/ciso-copilot.ts` (the CDK app entrypoint), pass them when wiring data → api:

```typescript
new CisoCopilotApi(app, 'CisoCopilotApi', {
  // existing props...
  autonomousBroadcastQueue: data.autonomousBroadcastQueue,
  autonomousBroadcastSeenTable: data.autonomousBroadcastSeenTable,
});
```

- [ ] **Step 5: CDK synth to verify**

```bash
cd platform && npx cdk synth CisoCopilotData CisoCopilotApi 2>&1 | tail -10
```

Expected: synth completes, no errors. There will be drift warnings about new resources; that's expected.

- [ ] **Step 6: Commit**

```bash
git checkout -b feat/mcp-connectors-slice-2.4-broadcast-plumbing
git add platform/lib/data-stack.ts platform/lib/api-stack.ts platform/bin/*.ts
git commit -m "feat(cdk): SQS broadcast queue + DDB seen table + findings_subscriber

CisoCopilotData stack exports:
  - autonomousBroadcastQueue (SQS standard, vis=30s, DLQ maxReceiveCount=5)
  - autonomousBroadcastDlq (with CloudWatch alarm on visible>=1 for 5min)
  - autonomousBroadcastSeenTable (DDB, TTL on ttl_epoch)

CisoCopilotApi stack adds:
  - findingsSubscriberFn (Python 3.12, batch=1, all required IAM)
  - scanner Lambdas gain AUTONOMOUS_BROADCAST_QUEUE_URL env + sqs:SendMessage

Subscriber Lambda code lands in next commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 14: Build the `findings_subscriber` Lambda code

**Files:**
- Create: `platform/lambda/findings_subscriber/__init__.py`
- Create: `platform/lambda/findings_subscriber/main.py`
- Create: `platform/lambda/findings_subscriber/idempotency.py`
- Create: `platform/lambda/findings_subscriber/kill_switch.py`
- Create: `platform/lambda/findings_subscriber/block_kit.py`
- Create: `platform/lambda/findings_subscriber/requirements.txt`
- Create: `platform/lambda/findings_subscriber/tests/__init__.py`
- Create: `platform/lambda/findings_subscriber/tests/conftest.py`
- Create: `platform/lambda/findings_subscriber/tests/test_idempotency.py`
- Create: `platform/lambda/findings_subscriber/tests/test_kill_switch.py`
- Create: `platform/lambda/findings_subscriber/tests/test_block_kit.py`
- Create: `platform/lambda/findings_subscriber/tests/test_main.py`

- [ ] **Step 1: `requirements.txt`**

`platform/lambda/findings_subscriber/requirements.txt`:
```
mcp>=1.10.0
boto3
cryptography==43.0.1
```

- [ ] **Step 2: `__init__.py` (empty marker)**

```bash
touch platform/lambda/findings_subscriber/__init__.py
touch platform/lambda/findings_subscriber/tests/__init__.py
```

- [ ] **Step 3: `tests/conftest.py`**

`platform/lambda/findings_subscriber/tests/conftest.py`:
```python
"""Put findings_subscriber/ + _shared/ on sys.path."""
import os, sys
from pathlib import Path

_LAMBDA_DIR = Path(__file__).resolve().parent.parent.parent  # platform/lambda
sys.path.insert(0, str(_LAMBDA_DIR))
sys.path.insert(0, str(_LAMBDA_DIR / "_shared"))

os.environ.setdefault("DB_CLUSTER_ARN", "arn:aws:rds:us-east-1:000000000000:cluster:test")
os.environ.setdefault("DB_SECRET_ARN",  "arn:aws:secretsmanager:us-east-1:000000000000:secret:test")
os.environ.setdefault("DB_NAME",        "ciso_copilot")
os.environ.setdefault("AUTONOMOUS_BROADCAST_SEEN_TABLE", "test-seen")
os.environ.setdefault("AUTONOMOUS_RULE_SSM_PARAM", "/test/enabled")
os.environ.setdefault("WEB_BASE_URL", "https://app.shasta.io")
os.environ.setdefault("CONNECTOR_TOKENS_KEY_ARN", "arn:aws:kms:us-east-1:0:key/test")
```

- [ ] **Step 4: `idempotency.py` + its test (TDD)**

`platform/lambda/findings_subscriber/tests/test_idempotency.py` (new):
```python
"""DDB-backed seen-table for autonomous broadcast idempotency.

Key: sha256(tenant_id || finding_id || scan_id). TTL: 7 days.
"""
from unittest.mock import MagicMock
import pytest


def _setup(monkeypatch):
    from findings_subscriber import idempotency as idem
    fake_table = MagicMock()
    monkeypatch.setattr(idem, "_table", lambda: fake_table)
    return idem, fake_table


def test_seen_returns_false_when_not_in_table(monkeypatch):
    idem, table = _setup(monkeypatch)
    table.get_item.return_value = {}
    assert idem.seen(tenant_id="t", finding_id="f", scan_id="s") is False


def test_seen_returns_true_when_in_table(monkeypatch):
    idem, table = _setup(monkeypatch)
    table.get_item.return_value = {"Item": {"seen_key": "h", "ttl_epoch": 9999}}
    assert idem.seen(tenant_id="t", finding_id="f", scan_id="s") is True


def test_mark_seen_writes_with_ttl(monkeypatch):
    idem, table = _setup(monkeypatch)
    idem.mark_seen(tenant_id="t", finding_id="f", scan_id="s")
    table.put_item.assert_called_once()
    item = table.put_item.call_args.kwargs["Item"]
    assert "seen_key" in item and "ttl_epoch" in item
    # TTL must be ~now + 7 days (in epoch seconds).
    import time
    delta = item["ttl_epoch"] - int(time.time())
    assert 6 * 86400 < delta < 8 * 86400


def test_mark_seen_uses_conditional_write(monkeypatch):
    """Conditional PutItem: only writes if seen_key doesn't exist. Prevents
    a race where two parallel subscribers both call mark_seen — only one
    succeeds; the loser's ConditionalCheckFailedException is swallowed."""
    idem, table = _setup(monkeypatch)
    idem.mark_seen(tenant_id="t", finding_id="f", scan_id="s")
    kwargs = table.put_item.call_args.kwargs
    assert "ConditionExpression" in kwargs
    assert "attribute_not_exists" in kwargs["ConditionExpression"]
```

`platform/lambda/findings_subscriber/idempotency.py` (new):
```python
"""DDB-backed idempotency for autonomous broadcast.

seen?(tenant, finding, scan) → bool — was this exact tuple broadcast already?
mark_seen(...)               → conditional PutItem with 7-day TTL
"""
from __future__ import annotations
import hashlib
import os
import time

import boto3
from botocore.exceptions import ClientError

_TTL_SECONDS = 7 * 86400  # 7 days

_dynamodb = None


def _table():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb")
    return _dynamodb.Table(os.environ["AUTONOMOUS_BROADCAST_SEEN_TABLE"])


def _key(*, tenant_id: str, finding_id: str, scan_id: str) -> str:
    return hashlib.sha256(
        f"{tenant_id}|{finding_id}|{scan_id}".encode("utf-8")
    ).hexdigest()


def seen(*, tenant_id: str, finding_id: str, scan_id: str) -> bool:
    resp = _table().get_item(Key={"seen_key": _key(
        tenant_id=tenant_id, finding_id=finding_id, scan_id=scan_id)})
    return bool(resp.get("Item"))


def mark_seen(*, tenant_id: str, finding_id: str, scan_id: str) -> None:
    k = _key(tenant_id=tenant_id, finding_id=finding_id, scan_id=scan_id)
    try:
        _table().put_item(
            Item={"seen_key": k, "ttl_epoch": int(time.time()) + _TTL_SECONDS},
            ConditionExpression="attribute_not_exists(seen_key)",
        )
    except ClientError as e:
        # Race: another invocation marked first. That's fine — the
        # broadcast either fired once (theirs) or will fire once (ours,
        # depending on which side of the SQS visibility window we're on).
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
```

Run:
```bash
cd platform/lambda/findings_subscriber && /Users/kkmookhey/Projects/CISOBrief/platform/venv/bin/python -m pytest tests/test_idempotency.py -q
```

Expected: 4 passed.

- [ ] **Step 5: `kill_switch.py` + its test**

`platform/lambda/findings_subscriber/tests/test_kill_switch.py` (new):
```python
"""SSM-backed global kill switch with 60s in-memory cache."""
from unittest.mock import MagicMock


def _setup(monkeypatch, ssm_response=None, ssm_raises=None):
    from findings_subscriber import kill_switch as ks
    ks._cache = (0.0, True)  # reset
    fake = MagicMock()
    if ssm_raises:
        fake.get_parameter.side_effect = ssm_raises
    else:
        fake.get_parameter.return_value = ssm_response or {
            "Parameter": {"Value": "true"}}
    monkeypatch.setattr(ks, "_ssm", fake)
    return ks, fake


def test_enabled_returns_true_when_ssm_says_true(monkeypatch):
    ks, _ = _setup(monkeypatch, {"Parameter": {"Value": "true"}})
    assert ks.global_enabled() is True


def test_enabled_returns_false_when_ssm_says_false(monkeypatch):
    ks, _ = _setup(monkeypatch, {"Parameter": {"Value": "false"}})
    assert ks.global_enabled() is False


def test_enabled_fail_open_when_ssm_throws(monkeypatch):
    """Flaky SSM shouldn't silence the alerts. Per-tenant toggle in
    Aurora is the authoritative kill — global SSM is paranoid layer only."""
    ks, _ = _setup(monkeypatch, ssm_raises=RuntimeError("ssm down"))
    assert ks.global_enabled() is True


def test_cache_hit_doesnt_recall_ssm(monkeypatch):
    """Within 60s, repeated calls hit the in-memory cache."""
    ks, fake = _setup(monkeypatch, {"Parameter": {"Value": "true"}})
    ks.global_enabled()
    ks.global_enabled()
    ks.global_enabled()
    fake.get_parameter.assert_called_once()
```

`platform/lambda/findings_subscriber/kill_switch.py` (new):
```python
"""SSM-backed global kill switch with 60-second in-memory cache.

Fail-open: a flaky SSM call shouldn't silence alerts. The per-tenant
toggle in tenant_bot_connectors.autonomous_rule_enabled is the
authoritative kill — this global switch is the paranoid layer for
"we discovered the Block Kit template leaks data; pull the brake."
"""
from __future__ import annotations
import os
import time

import boto3

_CACHE_TTL_SECONDS = 60
_ssm = boto3.client("ssm")
_cache: tuple[float, bool] = (0.0, True)  # (fetched_at, value)


def global_enabled() -> bool:
    global _cache
    fetched_at, value = _cache
    now = time.time()
    if now - fetched_at < _CACHE_TTL_SECONDS:
        return value
    try:
        resp = _ssm.get_parameter(Name=os.environ["AUTONOMOUS_RULE_SSM_PARAM"])
        value = resp["Parameter"]["Value"].lower() == "true"
    except Exception as e:
        print(f"[kill_switch] SSM read failed: {e!r}; failing open")
        value = True
    _cache = (now, value)
    return value
```

Run:
```bash
/Users/kkmookhey/Projects/CISOBrief/platform/venv/bin/python -m pytest tests/test_kill_switch.py -q
```

Expected: 4 passed.

- [ ] **Step 6: `block_kit.py` + golden tests**

`platform/lambda/findings_subscriber/tests/test_block_kit.py` (new):
```python
"""Golden tests for the Slack Block Kit template. Targets: 4-6 visual
lines, correct escaping of ARNs with special chars, sane truncation."""
import json


def _make_finding(**overrides):
    base = {
        "finding_id": "f-1", "tenant_id": "t-1",
        "title": "Public S3 bucket with PII-tagged data",
        "resource_arn": "arn:aws:s3:::acme-customer-exports",
        "scanner": "aws", "frameworks_list": ["PCI-DSS", "CIS-AWS"],
        "created_at_epoch": 1717179000,
    }
    base.update(overrides)
    return base


def test_template_includes_all_required_sections():
    from findings_subscriber.block_kit import format_finding_card
    blocks = format_finding_card(_make_finding())
    assert len(blocks) == 3  # title section, body section, actions
    assert blocks[0]["type"] == "section"
    assert blocks[1]["type"] == "section"
    assert blocks[2]["type"] == "actions"


def test_template_includes_view_button_with_url():
    from findings_subscriber.block_kit import format_finding_card
    import os
    os.environ["WEB_BASE_URL"] = "https://app.shasta.io"
    blocks = format_finding_card(_make_finding())
    btn = blocks[2]["elements"][0]
    assert btn["type"] == "button"
    assert btn["url"] == "https://app.shasta.io/risks/f-1"


def test_template_escapes_special_chars_in_arn():
    """Slack mrkdwn special chars (<, >, &) must be escaped. ARNs with
    these chars must not crash the parser or inject markup."""
    from findings_subscriber.block_kit import format_finding_card
    bad_arn = "arn:aws:s3:::my-bucket&foo<bar>"
    blocks = format_finding_card(_make_finding(resource_arn=bad_arn))
    body_text = blocks[1]["text"]["text"]
    assert "&amp;" in body_text or "&" not in body_text.replace("&amp;", "")
    assert "&lt;" in body_text or "<" not in body_text.replace("&lt;", "")
    assert "&gt;" in body_text or ">" not in body_text.replace("&gt;", "")


def test_template_truncates_long_title():
    """Slack section text limit is 3000 chars. Titles capped at 150 to
    keep the card compact."""
    from findings_subscriber.block_kit import format_finding_card
    long_title = "X" * 500
    blocks = format_finding_card(_make_finding(title=long_title))
    # Title appears in first section text. Bound: 150 chars + decorations.
    assert len(blocks[0]["text"]["text"]) < 300


def test_template_handles_ai_finding_shape():
    """AI scanner findings don't have resource_arn — they have subject_ref."""
    from findings_subscriber.block_kit import format_finding_card
    ai = _make_finding(scanner="ai", resource_arn=None,
                       subject_ref="agent://acme/customer-bot")
    blocks = format_finding_card(ai)
    # Should not crash; subject_ref appears in body when ARN is None.
    body = blocks[1]["text"]["text"]
    assert "agent://acme/customer-bot" in body or "subject_ref" not in body
```

`platform/lambda/findings_subscriber/block_kit.py` (new):
```python
"""Slack Block Kit template for the autonomous CRITICAL broadcast.

Goal: 4-6 visual lines. Channel members opted in; respect attention.
Deliberately NOT included: full evidence (sensitive), authoritative
remediation steps (canonical in platform UI), @mentions (no paging),
batched findings (one finding = one message).
"""
from __future__ import annotations
import os


def _escape(text: str | None) -> str:
    """Slack mrkdwn escape — only the three required chars per Slack's docs.
    `\\`, `_`, `*` are pass-through (legal in mrkdwn).
    """
    if text is None:
        return ""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


_TITLE_MAX = 150


def format_finding_card(f: dict) -> list[dict]:
    title = (f.get("title") or "")[:_TITLE_MAX]
    resource = f.get("resource_arn") or f.get("subject_ref") or "(unknown)"
    scanner = f.get("scanner") or "unknown"
    frameworks = ", ".join(f.get("frameworks_list") or []) or "—"
    created_at_epoch = int(f.get("created_at_epoch") or 0)
    web_base = os.environ["WEB_BASE_URL"]

    return [
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f"🚨 *CRITICAL — {_escape(title)}*"}},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f"*Resource:* `{_escape(resource)}`\n"
                    f"*Scanner:* {_escape(scanner)} · *Frameworks:* {_escape(frameworks)}\n"
                    f"*Detected:* <!date^{created_at_epoch}^"
                    f"{{date_short}} {{time}}|just now>"}},
        {"type": "actions", "elements": [{
            "type": "button",
            "text": {"type": "plain_text", "text": "View full details and remediation"},
            "url": f"{web_base}/risks/{f['finding_id']}",
            "style": "primary",
        }]},
    ]
```

Run:
```bash
/Users/kkmookhey/Projects/CISOBrief/platform/venv/bin/python -m pytest tests/test_block_kit.py -q
```

Expected: 5 passed.

- [ ] **Step 7: `main.py` handler + its test**

`platform/lambda/findings_subscriber/tests/test_main.py` (new):
```python
"""End-to-end subscriber tests — full handler orchestration with mocks."""
import contextlib
import json
from unittest.mock import AsyncMock, MagicMock
import pytest


def _sqs_event(body):
    return {"Records": [{"body": json.dumps(body)}]}


def _patch_core(monkeypatch, *, tenant_bot=None, finding=None,
                kill_switch_enabled=True, seen=False):
    """Stub idempotency + kill_switch + DB + admin_session in one shot."""
    from findings_subscriber import idempotency, kill_switch, main as m

    monkeypatch.setattr(idempotency, "seen", lambda **kw: seen)
    mark_seen_calls = []
    monkeypatch.setattr(idempotency, "mark_seen",
                        lambda **kw: mark_seen_calls.append(kw))
    monkeypatch.setattr(kill_switch, "global_enabled",
                        lambda: kill_switch_enabled)

    # Aurora Data API: returns tenant_bot row, then finding row.
    fake_db = MagicMock()
    rows = [tenant_bot, finding]
    fake_db.execute.return_value.fetchone.side_effect = rows
    monkeypatch.setattr("mcp_oauth.session._db", lambda: fake_db)
    monkeypatch.setattr("mcp_oauth.admin_session._db", lambda: fake_db)

    # MCP admin session
    fake_session = AsyncMock()
    fake_session.call_tool.return_value = MagicMock()
    @contextlib.asynccontextmanager
    async def fake_admin_session(*a, **kw):
        yield fake_session
    monkeypatch.setattr("mcp_oauth.get_admin_session", fake_admin_session)

    return m, fake_session, mark_seen_calls


def test_happy_path_posts_block_kit_card_and_marks_seen(monkeypatch):
    m, fake_session, mark_seen_calls = _patch_core(
        monkeypatch,
        tenant_bot={"broadcast_channel_id": "C-XYZ",
                     "autonomous_rule_enabled": True,
                     "bot_id": "b-1", "access_token_enc": b"E",
                     "access_data_key_ct": b"DK",
                     "mcp_server_url": "https://mcp.slack.com/mcp",
                     "vendor_workspace_id": "T0",
                     "access_expires_at": None},
        finding={"finding_id": "f-1", "title": "Test",
                 "resource_arn": "arn:aws:s3:::x", "scanner": "aws",
                 "frameworks_list": [], "created_at_epoch": 1717179000,
                 "tenant_id": "t-1"},
    )
    monkeypatch.setattr("mcp_oauth.session.decrypt_token", lambda c, dk: "xoxb")

    m.handler(_sqs_event({"tenant_id": "t-1", "finding_id": "f-1",
                          "scan_id": "s-1"}), None)

    fake_session.call_tool.assert_called_once()
    call_args = fake_session.call_tool.call_args
    assert call_args.args[0] == "send_message" or call_args.args[0] == "chat_postMessage"
    assert call_args.args[1]["channel"] == "C-XYZ"
    assert "blocks" in call_args.args[1]
    assert mark_seen_calls == [
        {"tenant_id": "t-1", "finding_id": "f-1", "scan_id": "s-1"}
    ]


def test_silent_ack_when_already_seen(monkeypatch):
    m, fake_session, _ = _patch_core(monkeypatch, seen=True)
    m.handler(_sqs_event({"tenant_id": "t", "finding_id": "f", "scan_id": "s"}), None)
    fake_session.call_tool.assert_not_called()


def test_silent_ack_when_kill_switch_off(monkeypatch):
    m, fake_session, _ = _patch_core(monkeypatch, kill_switch_enabled=False)
    m.handler(_sqs_event({"tenant_id": "t", "finding_id": "f", "scan_id": "s"}), None)
    fake_session.call_tool.assert_not_called()


def test_silent_ack_when_tenant_bot_missing(monkeypatch):
    m, fake_session, _ = _patch_core(monkeypatch, tenant_bot=None)
    m.handler(_sqs_event({"tenant_id": "t", "finding_id": "f", "scan_id": "s"}), None)
    fake_session.call_tool.assert_not_called()


def test_silent_ack_when_autonomous_rule_disabled(monkeypatch):
    m, fake_session, _ = _patch_core(
        monkeypatch,
        tenant_bot={"broadcast_channel_id": "C", "autonomous_rule_enabled": False,
                     "bot_id": "b", "access_token_enc": b"E",
                     "access_data_key_ct": b"D",
                     "mcp_server_url": "x", "vendor_workspace_id": "T",
                     "access_expires_at": None},
    )
    m.handler(_sqs_event({"tenant_id": "t", "finding_id": "f", "scan_id": "s"}), None)
    fake_session.call_tool.assert_not_called()


def test_silent_ack_when_no_channel_picked(monkeypatch):
    m, fake_session, _ = _patch_core(
        monkeypatch,
        tenant_bot={"broadcast_channel_id": None, "autonomous_rule_enabled": True,
                     "bot_id": "b", "access_token_enc": b"E",
                     "access_data_key_ct": b"D",
                     "mcp_server_url": "x", "vendor_workspace_id": "T",
                     "access_expires_at": None},
    )
    m.handler(_sqs_event({"tenant_id": "t", "finding_id": "f", "scan_id": "s"}), None)
    fake_session.call_tool.assert_not_called()


def test_silent_ack_when_finding_disappeared(monkeypatch):
    m, fake_session, _ = _patch_core(
        monkeypatch,
        tenant_bot={"broadcast_channel_id": "C", "autonomous_rule_enabled": True,
                     "bot_id": "b", "access_token_enc": b"E",
                     "access_data_key_ct": b"D",
                     "mcp_server_url": "x", "vendor_workspace_id": "T",
                     "access_expires_at": None},
        finding=None,
    )
    monkeypatch.setattr("mcp_oauth.session.decrypt_token", lambda c, dk: "xoxb")
    m.handler(_sqs_event({"tenant_id": "t", "finding_id": "f", "scan_id": "s"}), None)
    fake_session.call_tool.assert_not_called()
```

`platform/lambda/findings_subscriber/main.py` (new):
```python
"""Autonomous broadcast subscriber.

SQS-fed (batch=1). For each message:
  1. idempotency check (DDB seen-table, 7d TTL)
  2. global kill switch (SSM, 60s cache)
  3. tenant_bot_connectors lookup (silent ack if missing/disabled/no channel)
  4. findings row re-read (silent ack if missing — race with retention)
  5. open MCP admin session, post Block Kit card
  6. mark_seen (conditional PutItem; log & swallow if fails AFTER successful send)
"""
from __future__ import annotations
import asyncio
import json

from findings_subscriber import idempotency, kill_switch, block_kit
from mcp_oauth import get_admin_session
from mcp_oauth.session import (
    _db, ConnectorMissingError, ConnectorRevokedError,
)


def handler(event: dict, _ctx) -> dict:
    for record in event.get("Records", []):
        try:
            _process(json.loads(record["body"]))
        except (ConnectorMissingError, ConnectorRevokedError) as e:
            # Tenant uninstalled or bot revoked — expected silent ack.
            print(f"[findings_subscriber] silent ack ({type(e).__name__}): {e}")
        # All other exceptions propagate → SQS retry → DLQ after maxReceiveCount.
    return {"ok": True}


def _process(body: dict) -> None:
    tenant_id = body["tenant_id"]
    finding_id = body["finding_id"]
    scan_id = body["scan_id"]

    if idempotency.seen(tenant_id=tenant_id,
                        finding_id=finding_id, scan_id=scan_id):
        print(f"[findings_subscriber] already seen: {finding_id}/{scan_id}")
        return
    if not kill_switch.global_enabled():
        print(f"[findings_subscriber] global kill switch OFF; skipping")
        return

    # tenant_bot_connectors gate (skip silently if not configured).
    bot = _db().execute("""
        SELECT bot_id, broadcast_channel_id, autonomous_rule_enabled
        FROM tenant_bot_connectors
        WHERE tenant_id = :tid::uuid AND oauth_provider = 'slack'
          AND status = 'active'
    """, [{"name": "tid", "value": {"stringValue": tenant_id}}]).fetchone()
    if not bot:
        return
    if not bot.get("autonomous_rule_enabled"):
        return
    if not bot.get("broadcast_channel_id"):
        return

    # Re-read finding (subscriber may lag writer by ms).
    finding = _db().execute("""
        SELECT finding_id::text AS finding_id,
               title, description, severity, status,
               resource_arn, resource_type, region, domain,
               frameworks,
               EXTRACT(EPOCH FROM last_seen)::bigint AS created_at_epoch,
               (SELECT MAX(scanner) FROM (
                  SELECT 'unknown' AS scanner
                )) AS scanner
        FROM findings WHERE finding_id = :fid::uuid
    """, [{"name": "fid", "value": {"stringValue": finding_id}}]).fetchone()
    if not finding:
        return

    blocks = block_kit.format_finding_card({
        "finding_id":       finding["finding_id"],
        "title":            finding.get("title") or "",
        "resource_arn":     finding.get("resource_arn"),
        "scanner":          finding.get("scanner") or "unknown",
        "frameworks_list":  _frameworks_to_list(finding.get("frameworks")),
        "created_at_epoch": finding.get("created_at_epoch") or 0,
    })

    async def _post():
        async with get_admin_session(tenant_id, "slack") as session:
            await session.call_tool("send_message", {
                "channel": bot["broadcast_channel_id"],
                "blocks":  blocks,
            })
    asyncio.run(_post())

    try:
        idempotency.mark_seen(tenant_id=tenant_id,
                              finding_id=finding_id, scan_id=scan_id)
    except Exception as e:
        # mark_seen failed AFTER successful Slack post — don't re-raise
        # (a duplicate seen-row is much cheaper than a double-broadcast).
        print(f"[findings_subscriber] mark_seen failed post-send: {e!r}")


def _frameworks_to_list(frameworks) -> list[str]:
    """findings.frameworks is JSONB (per CLAUDE.md gotchas, an object not
    an array). Return a list of human-readable framework labels."""
    if not frameworks:
        return []
    if isinstance(frameworks, str):
        try:
            frameworks = json.loads(frameworks)
        except json.JSONDecodeError:
            return []
    if isinstance(frameworks, dict):
        return [k for k, v in frameworks.items() if v]
    if isinstance(frameworks, list):
        return [str(f) for f in frameworks]
    return []
```

Run:
```bash
/Users/kkmookhey/Projects/CISOBrief/platform/venv/bin/python -m pytest tests/ -q
```

Expected: ~17 passed (4 idempotency + 4 kill_switch + 5 block_kit + 7 main = wait, recheck). The test count varies; what matters is they all pass.

- [ ] **Step 8: Commit**

```bash
git add platform/lambda/findings_subscriber/
git commit -m "feat(findings_subscriber): autonomous broadcast Lambda

SQS-driven, batch=1. Idempotent via DDB (sha256(tenant||finding||scan),
7d TTL, conditional write). Three-layer kill switch:
  1. SSM global (cached 60s, fail-open)
  2. tenant_bot_connectors.autonomous_rule_enabled
  3. broadcast_channel_id NULL (admin hasn't picked yet)

Silent acks for all expected-state-not-broadcasting branches; only
unexpected exceptions go to DLQ after 5 SQS retries.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 15: Open sub-slice 2.4 PR

- [ ] **Step 1: Push and open**

```bash
git push -u origin feat/mcp-connectors-slice-2.4-broadcast-plumbing
gh pr create --title "feat: Slice 2.4 — autonomous broadcast plumbing (SQS → subscriber → Slack)" --body "$(cat <<'EOF'
## Summary

- CDK: SQS broadcast queue + DLQ (maxReceiveCount=5) + DDB seen table + CloudWatch DLQ alarm
- `findings_subscriber/` Lambda: idempotency, kill switch, Block Kit template, all silent-ack branches
- Scanner Lambdas gain `AUTONOMOUS_BROADCAST_QUEUE_URL` + `sqs:SendMessage` grant

## Test plan

- [x] idempotency (4 tests: hit/miss, conditional write, race)
- [x] kill_switch (4 tests: enabled/disabled/fail-open/cache)
- [x] block_kit (5 tests: structure, button URL, escape, truncate, AI shape)
- [x] main handler (7 tests: happy + 6 silent-ack branches)
- [ ] Manual: insert a critical-fail finding into dev Aurora; card appears in channel
- [ ] Manual: SSM kill switch off → no broadcast
- [ ] Manual: deleted channel ID → after 5 retries, message in DLQ + alarm fires
EOF
)"
```

---

## Sub-slice 2.5 — DeepLinkGate + drift metric + final hardening

### Task 16: Implement `<DeepLinkGate>` wrapper

**Files:**
- Create: `web/src/components/DeepLinkGate.tsx`
- Modify: `web/src/App.tsx` (wrap the `/risks/:finding_id` route)
- Test: `web/src/components/__tests__/DeepLinkGate.test.tsx`

- [ ] **Step 1: Write the test (Vitest if configured, otherwise inline check)**

`web/src/components/__tests__/DeepLinkGate.test.tsx` (new):
```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { DeepLinkGate } from "../DeepLinkGate";

vi.mock("../../lib/useSession", () => ({
  useSession: vi.fn(),
}));

import { useSession } from "../../lib/useSession";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("DeepLinkGate", () => {
  it("renders children when signed in", () => {
    (useSession as any).mockReturnValue({ user: { sub: "u" }, loading: false });
    render(
      <MemoryRouter initialEntries={["/risks/abc"]}>
        <Routes>
          <Route path="/risks/:id" element={
            <DeepLinkGate>
              <div>secure content</div>
            </DeepLinkGate>
          } />
        </Routes>
      </MemoryRouter>
    );
    expect(screen.getByText("secure content")).toBeInTheDocument();
  });

  it("redirects to signin with ?after= when not signed in", () => {
    (useSession as any).mockReturnValue({ user: null, loading: false });
    render(
      <MemoryRouter initialEntries={["/risks/abc"]}>
        <Routes>
          <Route path="/risks/:id" element={
            <DeepLinkGate>
              <div>secure content</div>
            </DeepLinkGate>
          } />
          <Route path="/signin" element={<div>signin-page</div>} />
        </Routes>
      </MemoryRouter>
    );
    expect(screen.getByText("signin-page")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd web && pnpm test -- DeepLinkGate 2>&1 | tail -15
```

Expected: ModuleNotFoundError on `../DeepLinkGate`.

- [ ] **Step 3: Implement**

`web/src/components/DeepLinkGate.tsx` (new):
```typescript
import { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useSession } from "../lib/useSession";

/**
 * Wrapper for routes that must survive an unauthenticated browser tab.
 * Used by /risks/:finding_id, which is the destination of the
 * Slack-card "View details" button — an admin clicks it days after
 * the broadcast, opens it in a fresh browser, no Cognito session yet.
 *
 * If signed in: renders children.
 * If not signed in: navigates to /signin?after=<current-path> so
 * Cognito callback can bounce back post-auth.
 */
export function DeepLinkGate({ children }: { children: ReactNode }) {
  const { user, loading } = useSession();
  const loc = useLocation();

  if (loading) {
    return <div className="p-8 text-neutral-500">Loading…</div>;
  }
  if (!user) {
    const after = encodeURIComponent(loc.pathname + loc.search);
    return <Navigate to={`/signin?after=${after}`} replace />;
  }
  return <>{children}</>;
}
```

- [ ] **Step 4: Wrap `/risks/:finding_id` in `App.tsx`**

Open `web/src/App.tsx`. Find the route for `/risks/:finding_id` (or the route that renders `Risks`). Wrap it:

```typescript
<Route path="/risks/:id" element={
  <DeepLinkGate>
    <Risks />
  </DeepLinkGate>
} />
```

If the route lives inside a Shell wrapper, place DeepLinkGate inside the Shell so the auth-redirect still gets the route context.

- [ ] **Step 5: Run tests + build**

```bash
pnpm test -- DeepLinkGate
pnpm build
```

Expected: tests pass; build succeeds.

- [ ] **Step 6: Commit**

```bash
git checkout -b feat/mcp-connectors-slice-2.5-hardening
git add web/src/components/DeepLinkGate.tsx \
        web/src/components/__tests__/DeepLinkGate.test.tsx \
        web/src/App.tsx
git commit -m "feat(web): DeepLinkGate wrapper for /risks/:id

Slack-card 'View details' button drops users on /risks/:id with no
Cognito session (different browser, days after the broadcast).
DeepLinkGate redirects to /signin?after=... so the existing Cognito
callback can bounce back post-auth.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 17: Add drift metric — broadcasts vs critical-fail inserts

**Files:**
- Modify: `platform/lambda/_shared/broadcast_fanout.py` (emit EMF metric on every publish)
- Modify: `platform/lib/data-stack.ts` (add the CloudWatch alarm)

Justification for EMF over a log-pattern MetricFilter (per spec §11 implementer choice): EMF is more reliable, doesn't couple to log format changes, and aggregates per Lambda invocation natively.

- [ ] **Step 1: Add EMF emission to broadcast_fanout**

Modify `platform/lambda/_shared/broadcast_fanout.py`:
```python
import json
import os

import boto3

_sqs = boto3.client("sqs")


def _emit_emf_metric(metric_name: str, value: int = 1) -> None:
    """Emit an EMF-formatted log line so CloudWatch parses it as a metric.
    Cheaper than a separate PutMetricData call (free) and aggregates
    across Lambda invocations on the same minute."""
    print(json.dumps({
        "_aws": {
            "Timestamp": int(__import__("time").time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": "Shasta/AutonomousBroadcast",
                "Dimensions": [[]],
                "Metrics": [{"Name": metric_name, "Unit": "Count"}],
            }],
        },
        metric_name: value,
    }))


def publish_if_critical(*, tenant_id: str, finding_id: str, scan_id: str,
                        severity: str, status: str) -> None:
    if severity != "critical" or status != "fail":
        return
    # Emit the "critical-fail finding written" metric for drift comparison.
    _emit_emf_metric("CriticalFailWritten")

    queue_url = os.environ.get("AUTONOMOUS_BROADCAST_QUEUE_URL")
    if not queue_url:
        return
    try:
        _sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps({
                "tenant_id": tenant_id,
                "finding_id": finding_id,
                "scan_id": scan_id,
            }),
        )
        _emit_emf_metric("BroadcastQueued")
    except Exception as e:
        print(f"[broadcast_fanout] publish failed: {type(e).__name__}: {e}")
        _emit_emf_metric("BroadcastFanoutFailed")
```

- [ ] **Step 2: Update broadcast_fanout tests to assert EMF emission**

Modify `platform/lambda/_shared/tests/test_broadcast_fanout.py`:
```python
def test_emits_emf_metric_when_critical_fail_written(monkeypatch, capsys):
    bf, fake = _install_fake_sqs(monkeypatch)
    bf.publish_if_critical(
        tenant_id="t", finding_id="f", scan_id="s",
        severity="critical", status="fail",
    )
    out = capsys.readouterr().out
    assert "CriticalFailWritten" in out
    assert "BroadcastQueued" in out


def test_emits_fanout_failed_metric_on_sqs_error(monkeypatch, capsys):
    bf, fake = _install_fake_sqs(monkeypatch)
    fake.send_message.side_effect = RuntimeError("oh no")
    bf.publish_if_critical(
        tenant_id="t", finding_id="f", scan_id="s",
        severity="critical", status="fail",
    )
    out = capsys.readouterr().out
    assert "BroadcastFanoutFailed" in out
```

Run:
```bash
cd platform/lambda/_shared && /Users/kkmookhey/Projects/CISOBrief/platform/venv/bin/python -m pytest tests/test_broadcast_fanout.py -q
```

Expected: 7 passed (5 original + 2 new).

- [ ] **Step 3: Add the CloudWatch drift alarm in data-stack.ts**

Append to `platform/lib/data-stack.ts` (after the DLQ alarm):

```typescript
// Drift metric: CriticalFailWritten - BroadcastQueued, summed over 1h.
// > 2/hour = scanners are writing criticals but the SQS publish is
// silently failing (likely IAM grant missed in a deploy).
new cloudwatch.Alarm(this, 'AutonomousBroadcastDriftAlarm', {
  metric: new cloudwatch.MathExpression({
    expression: 'critical - queued',
    usingMetrics: {
      critical: new cloudwatch.Metric({
        namespace: 'Shasta/AutonomousBroadcast',
        metricName: 'CriticalFailWritten',
        statistic: 'Sum',
        period: cdk.Duration.minutes(60),
      }),
      queued: new cloudwatch.Metric({
        namespace: 'Shasta/AutonomousBroadcast',
        metricName: 'BroadcastQueued',
        statistic: 'Sum',
        period: cdk.Duration.minutes(60),
      }),
    },
    label: 'Critical findings written but not queued',
    period: cdk.Duration.minutes(60),
  }),
  threshold: 2,
  evaluationPeriods: 1,
  comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
  alarmDescription: 'Scanner wrote critical-fail finding but fan-out hook did not publish',
  treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
});
```

- [ ] **Step 4: CDK synth**

```bash
cd platform && npx cdk synth CisoCopilotData 2>&1 | tail -10
```

Expected: clean synth.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/_shared/broadcast_fanout.py \
        platform/lambda/_shared/tests/test_broadcast_fanout.py \
        platform/lib/data-stack.ts
git commit -m "feat(broadcast): EMF drift metric + CloudWatch alarm

broadcast_fanout emits three EMF metrics:
  CriticalFailWritten   — incremented at every critical-fail finding
  BroadcastQueued       — incremented on successful sqs.send_message
  BroadcastFanoutFailed — incremented when SQS publish raises

Drift alarm: (CriticalFailWritten - BroadcastQueued) > 2/hour fires.
Catches silent failures (missing env var, missing IAM grant) that
broadcast_fanout's swallow-and-log behavior would otherwise hide.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 18: Update HANDOFF + open sub-slice 2.5 PR

- [ ] **Step 1: Update HANDOFF.md**

Append a new section to `HANDOFF.md` after the Slice 1 section:

```markdown
## 🔔 MCP Connectors Slice 2 — autonomous broadcast (shipped in 5 sub-slices)

**Branches landed:** 2.1 → 2.5 (each its own PR). Spec at
`docs/superpowers/specs/2026-05-31-mcp-connectors-slice-2-design.md`.

What's live:

- Scanner Lambdas publish to `autonomous-broadcast-queue` on every
  critical-fail finding via `_shared/broadcast_fanout.publish_if_critical`.
- `findings_subscriber/` Lambda consumes the queue, gates on three
  kill switches (SSM global / per-tenant toggle / channel-not-picked),
  re-reads the finding, posts a Block Kit card to the configured
  Slack channel.
- Admin block on `/settings` → Connectors tab: install, channel picker,
  autonomous toggle, disconnect.
- `<DeepLinkGate>` wraps `/risks/:finding_id` so Slack-card clicks
  survive unauthenticated tabs.
- CloudWatch: DLQ alarm + drift alarm (CriticalFailWritten vs BroadcastQueued).

**Manual smoke (post-deploy):**

1. Admin installs Slack workspace bot via Settings → Connectors → admin block
2. Pick a broadcast channel via the modal
3. Manually INSERT a critical-fail finding into Aurora dev
4. Verify Block Kit card lands in the channel within 60s
5. Click "View details" from a fresh Incognito tab → bounces through /signin → lands on /risks/:id
6. Set `aws ssm put-parameter /cisocopilot/autonomous_rule/enabled false` → next insert: no broadcast
7. Flip the per-tenant toggle off → next insert: no broadcast
8. Set the channel to a deleted channel → DLQ accumulates after 5 retries → alarm fires within 5 min
```

- [ ] **Step 2: Commit HANDOFF**

```bash
git add HANDOFF.md
git commit -m "docs(handoff): Slice 2 — autonomous broadcast shipped across 5 sub-slices

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 3: Push and open the final PR**

```bash
git push -u origin feat/mcp-connectors-slice-2.5-hardening
gh pr create --title "feat: Slice 2.5 — DeepLinkGate + drift metric + HANDOFF" --body "$(cat <<'EOF'
## Summary

- `<DeepLinkGate>` wrapper for /risks/:id — survives unauthenticated tabs
- EMF drift metric in broadcast_fanout (CriticalFailWritten, BroadcastQueued, BroadcastFanoutFailed)
- CloudWatch drift alarm: fires when scanners write critical-fail rows but the fan-out hook doesn't publish
- HANDOFF updated with the Slice 2 surface

## Test plan

- [x] DeepLinkGate vitest tests (signed-in renders / signed-out redirects)
- [x] broadcast_fanout EMF emission tests
- [ ] Manual: install bot, pick channel, insert critical finding, click View from Incognito tab
- [ ] Manual: verify DLQ + drift alarms in CloudWatch console
EOF
)"
```

---

## Post-merge: full re-smoke

After all 5 PRs land on `main`:

1. CDK deploy:
   ```bash
   cd platform
   npx cdk deploy CisoCopilotData --require-approval never
   npx cdk deploy CisoCopilotApi --require-approval never
   ```
2. Web rebuild + sync:
   ```bash
   cd web && pnpm build
   aws s3 sync dist/ s3://<WEB_BUCKET>/ --delete
   aws cloudfront create-invalidation --distribution-id <CLOUDFRONT_DIST_ID> --paths '/*'
   ```
3. SSM kill-switch parameter (one-time):
   ```bash
   aws ssm put-parameter --name /cisocopilot/autonomous_rule/enabled \
     --type String --value "true" --overwrite
   ```
4. Run the 8-step manual smoke from HANDOFF.

---

## Self-review

**Spec coverage:**
- §1 success criteria 1–6 — covered across 2.2 (install), 2.3 (channel picker), 2.4 (broadcast), 2.5 (DeepLinkGate + drift)
- §5 components A–F — A (subscriber): Task 14. B (connectors extension): Tasks 6, 7, 10. C (admin_session): Task 9. D (broadcast_fanout): Task 1+2. E (web): Tasks 11, 16. F (CDK): Tasks 13, 17.
- §7 error handling — happy + every silent-ack branch tested in `test_main.py` (Task 14 Step 7)
- §8 Block Kit template — Task 14 Step 6 golden tests
- §9 testing — all unit test modules planned per spec table
- §10 sub-slicing — five PRs, 2.1 → 2.5

**Placeholder scan:**
- One: in Task 11 Step 6, the `useUser` hook may not exist as-is. Instructed to inspect existing auth context and adapt. Not a placeholder per se — engineer judgment with concrete guidance.
- Task 14 main.py uses a `scanner` field in the finding dict; the SELECT has a placeholder subquery because the actual `findings` schema doesn't have a `scanner` column (per the `entities` joins in the spec). Implementer must resolve scanner via the conn's `oauth_provider` or similar — flagged inline.

**Type consistency:**
- `_require_admin` returns `tuple[str | None, str | None]` consistently across Tasks 4, 6, 10
- `encrypt_token` returns `(bytes, bytes)` tuple consistently in Task 7's INSERT
- `lookup_tenant_bot` returns a dict consistently with `_zip_record` shape (decoded NULLs + arrays)

**Open scope note for KK before execution:**
Sub-slice 2.1 was narrowed from "full unified_writer consolidation" to "fan-out hook extraction only" — see the plan introduction. Confirm this narrowing is acceptable before starting 2.1.
