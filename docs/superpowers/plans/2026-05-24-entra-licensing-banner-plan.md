# Entra Licensing Banner (S2.1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the Microsoft Entra ID P1/P2 licensing constraint on `/auditLogs/signIns` to customers via a sticky banner on `/connect`, so Free-tier customers understand why the AI sign-in pass produces zero findings.

**Architecture:** Entra runner's `ai_signin_pass` catches the specific 403 `Authentication_RequestFromNonPremiumTenantOrB2CTenant` and signals back via a tuple-return. Handler writes/clears `cloud_connections.scope.signin_premium_required` accordingly. The existing `connections_list` endpoint already serves `scope` JSONB; the existing TypeScript `Connection.scope` already accepts arbitrary keys. Web renders an inline amber banner under any Entra row whose flag is set.

**Tech Stack:** Python 3.12 (container Lambda), AWS Lambda Web SDK (msgraph), Aurora Postgres via `rds-data`, React + TypeScript + Tailwind (vitest), pytest.

**Spec:** `docs/superpowers/specs/2026-05-24-entra-licensing-banner-design.md`.

**Branch:** `feat/ai-visibility-v2-slice-2.1` (already created; spec commit `ae2343d` is the base).

**Pre-flight verifications (already done during the plan-writing phase):**
- `connections_list/main.py` lines 80-116 already SELECTs `c.scope::text` and returns `"scope": json.loads(...)` on each row. **No Lambda code change needed for the read side.**
- `web/src/lib/api.ts:43-57` — `Connection.scope` is already an optional with `subscriptions/selected/subscription_names/mode/projects`. **We add `signin_premium_required?: boolean` to that shape.**

---

## File Structure

### Modified
- `platform/lambda/shasta_runner_entra/app/ai_signin_pass.py` — change `run_ai_signin_pass` and `_fetch_signins` return signatures to tuple `(events, premium_required)`. Add `_LICENSING_ERROR_CODE` constant.
- `platform/lambda/shasta_runner_entra/app/tests/test_ai_signin_pass.py` — update existing tests to unpack the tuple; add 2 new tests for the licensing-403 and other-403 cases.
- `platform/lambda/shasta_runner_entra/app/main.py` — add `_update_connection_premium_flag(conn_id, *, premium_required, signin_count)` helper; call it after `run_ai_signin_pass`.
- `web/src/lib/api.ts` — extend `Connection.scope` with `signin_premium_required?: boolean`.
- `web/src/routes/ConnectClouds.tsx` — render `LicensingBanner` inside `ConnectionRow` when `cloud_type==='entra' && scope.signin_premium_required`.
- `web/src/routes/ConnectClouds.test.tsx` — new vitest cases (3) for the banner render conditions.
- `HANDOFF.md` — prepend S2.1 ship block on completion.

### Not modified
- `platform/lambda/connections_list/main.py` — already returns `scope` (verified).
- Any other Lambda, the CDK, or the API stack.
- Shasta — read-only reference.

---

## Task 1: `ai_signin_pass` — return-signature change + licensing-403 detection (TDD)

**Files:**
- Modify: `platform/lambda/shasta_runner_entra/app/ai_signin_pass.py`
- Modify: `platform/lambda/shasta_runner_entra/app/tests/test_ai_signin_pass.py`

### Context

The existing `run_ai_signin_pass(graph_client=None, *, tenant_id, conn_id, scan_id, entra_tenant_id, last_scan_at=None) -> list[list[dict]]` returns a list of param-lists (one per matched sign-in event). The function also has a `_fetch_signins(graph_client, *, last_scan_at)` helper that catches all Graph exceptions and returns empty events.

S2.1 changes both to return a TUPLE: `(events_or_param_lists, premium_required: bool)`. The bool is `True` iff Microsoft Graph returned a 403 whose error code is `Authentication_RequestFromNonPremiumTenantOrB2CTenant` from `/auditLogs/signIns`.

Reading the current 244-line module first will be helpful. The key call sites: `_fetch_signins` is the only place that talks to Graph. `run_ai_signin_pass` calls `_fetch_signins`.

- [ ] **Step 1: Read the current `_fetch_signins` and `run_ai_signin_pass`**

```bash
sed -n '160,250p' /Users/kkmookhey/Projects/CISOBrief/platform/lambda/shasta_runner_entra/app/ai_signin_pass.py
```

Confirm the current structure matches the plan's understanding (lazy SDK import, `_maybe_await`, etc.).

- [ ] **Step 2: Write the failing tests**

Append to `platform/lambda/shasta_runner_entra/app/tests/test_ai_signin_pass.py`:

```python
def test_fetch_signins_returns_premium_required_on_specific_403():
    """When Graph returns the licensing-403, _fetch_signins signals it."""
    from ai_signin_pass import _fetch_signins

    class FakeError(Exception):
        def __init__(self):
            self.error = type("E", (), {"code": "Authentication_RequestFromNonPremiumTenantOrB2CTenant"})()
            self.response_status_code = 403

    class FakeGraph:
        class _Audit:
            class _SignIns:
                def get(self, request_configuration=None):
                    raise FakeError()
            sign_ins = _SignIns()
        audit_logs = _Audit()

    events, premium_required = _fetch_signins(FakeGraph(), last_scan_at=None)
    assert events == []
    assert premium_required is True


def test_fetch_signins_does_not_flag_other_403s():
    """Other 403s (revoked consent, missing scope) do NOT set premium_required."""
    from ai_signin_pass import _fetch_signins

    class FakeError(Exception):
        def __init__(self):
            self.error = type("E", (), {"code": "Authorization_RequestDenied"})()
            self.response_status_code = 403

    class FakeGraph:
        class _Audit:
            class _SignIns:
                def get(self, request_configuration=None):
                    raise FakeError()
            sign_ins = _SignIns()
        audit_logs = _Audit()

    events, premium_required = _fetch_signins(FakeGraph(), last_scan_at=None)
    assert events == []
    assert premium_required is False


def test_run_ai_signin_pass_returns_tuple():
    """Top-level orchestrator returns (param_lists, premium_required)."""
    from ai_signin_pass import run_ai_signin_pass

    class FakeError(Exception):
        def __init__(self):
            self.error = type("E", (), {"code": "Authentication_RequestFromNonPremiumTenantOrB2CTenant"})()
            self.response_status_code = 403

    class FakeGraph:
        class _Audit:
            class _SignIns:
                def get(self, request_configuration=None):
                    raise FakeError()
            sign_ins = _SignIns()
        audit_logs = _Audit()

    params, premium_required = run_ai_signin_pass(
        graph_client=FakeGraph(),
        tenant_id="TEN", conn_id="CONN", scan_id="SCAN",
        entra_tenant_id="ETEN",
    )
    assert params == []
    assert premium_required is True
```

Also update the **existing** test `test_run_ai_signin_pass_returns_tuple`-relatives — if any existing test calls `run_ai_signin_pass` and assigns the result to a single variable, it needs to either unpack the tuple OR be updated. Search the test file:

```bash
grep -n "run_ai_signin_pass" /Users/kkmookhey/Projects/CISOBrief/platform/lambda/shasta_runner_entra/app/tests/test_ai_signin_pass.py
```

If any existing test calls `run_ai_signin_pass(...)`, update its call site to unpack the tuple: `result, _ = run_ai_signin_pass(...)` (or rewrite the assertion to use the tuple shape).

- [ ] **Step 3: Run the new tests to confirm fail**

```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform/lambda/shasta_runner_entra/app && \
  /Users/kkmookhey/Projects/CISOBrief/platform/lambda/shasta_runner/.venv/bin/python -m pytest tests/test_ai_signin_pass.py::test_fetch_signins_returns_premium_required_on_specific_403 tests/test_ai_signin_pass.py::test_fetch_signins_does_not_flag_other_403s tests/test_ai_signin_pass.py::test_run_ai_signin_pass_returns_tuple -v
```

Expected: 3 FAILED (function signature mismatch or unpack errors).

- [ ] **Step 4: Update `_fetch_signins` to return the tuple + catch the specific 403**

In `platform/lambda/shasta_runner_entra/app/ai_signin_pass.py`, near the top (with other constants):

```python
# The Microsoft Graph error code returned from /auditLogs/signIns when the
# tenant is on Entra Free tier. Triggers the S2.1 banner via
# cloud_connections.scope.signin_premium_required.
_LICENSING_ERROR_CODE = "Authentication_RequestFromNonPremiumTenantOrB2CTenant"
```

Replace `_fetch_signins`'s body. Current shape (reference, do not commit):

```python
# CURRENT
def _fetch_signins(graph_client, *, last_scan_at):
    try:
        # ... build query_params + request_configuration ...
        page = graph_client.audit_logs.sign_ins.get(request_configuration=cfg)
        page = _maybe_await(page)
        if page is None or not getattr(page, "value", None):
            return []
        return [_event_to_dict(e) for e in page.value]
    except Exception as e:
        logger.warning("ai_signin_pass: Graph fetch failed: %s", e)
        return []
```

New shape:

```python
def _fetch_signins(graph_client, *, last_scan_at) -> tuple[list[dict], bool]:
    """Page through /auditLogs/signIns. Returns (events, premium_required).

    premium_required is True only when Microsoft returns 403 with error code
    Authentication_RequestFromNonPremiumTenantOrB2CTenant. All other failure
    modes (auth, scope, network, server) leave premium_required=False so the
    S2.1 banner stays off for non-licensing problems.
    """
    from kiota_abstractions.base_request_configuration import RequestConfiguration  # type: ignore
    from msgraph.generated.audit_logs.sign_ins.sign_ins_request_builder import SignInsRequestBuilder  # type: ignore

    query_params = SignInsRequestBuilder.SignInsRequestBuilderGetQueryParameters(top=1000)
    if last_scan_at:
        query_params.filter = f"createdDateTime ge {last_scan_at}"
    cfg = RequestConfiguration(query_parameters=query_params)

    try:
        page = graph_client.audit_logs.sign_ins.get(request_configuration=cfg)
        page = _maybe_await(page)
        if page is None or not getattr(page, "value", None):
            return [], False
        return [_event_to_dict(e) for e in page.value], False
    except Exception as e:
        err_obj = getattr(e, "error", None)
        err_code = getattr(err_obj, "code", None) if err_obj is not None else None
        premium_required = (err_code == _LICENSING_ERROR_CODE)
        if premium_required:
            logger.warning("ai_signin_pass: Graph returned licensing-403 (Entra Free tier)")
        else:
            logger.warning("ai_signin_pass: Graph fetch failed: %s", e)
        return [], premium_required
```

- [ ] **Step 5: Update `run_ai_signin_pass` to return the tuple**

In the same file, update the function:

```python
def run_ai_signin_pass(
    graph_client, *,
    tenant_id: str, conn_id: str, scan_id: str, entra_tenant_id: str,
    last_scan_at: str | None = None,
    catalog_path: str | None = None,
) -> tuple[list[list[dict]], bool]:
    """Returns (param_lists, premium_required).

    premium_required is True iff Microsoft returned 403
    Authentication_RequestFromNonPremiumTenantOrB2CTenant — the S2.1 banner
    trigger. Other failures leave premium_required=False so the banner
    doesn't fire for unrelated problems.
    """
    if graph_client is None:
        from azure.identity import DefaultAzureCredential       # type: ignore
        from msgraph import GraphServiceClient                  # type: ignore
        credential = DefaultAzureCredential()
        graph_client = GraphServiceClient(
            credentials=credential,
            scopes=["https://graph.microsoft.com/.default"],
        )

    catalog = load_catalog(catalog_path or _DEFAULT_CATALOG_PATH)
    events, premium_required = _fetch_signins(graph_client, last_scan_at=last_scan_at)

    out: list[list[dict]] = []
    for event in events:
        name, tier, sev = match_app(event, catalog)
        if name is None:
            continue
        params = signin_to_params(
            event, name=name, tier=tier, catalog_severity=sev,
            tenant_id=tenant_id, conn_id=conn_id, scan_id=scan_id,
            entra_tenant_id=entra_tenant_id,
        )
        out.append(params)

    return out, premium_required
```

- [ ] **Step 6: Run all `ai_signin_pass` tests, confirm pass**

```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform/lambda/shasta_runner_entra/app && \
  /Users/kkmookhey/Projects/CISOBrief/platform/lambda/shasta_runner/.venv/bin/python -m pytest tests/test_ai_signin_pass.py -v
```

Expected: all tests pass (9 original + 3 new = 12). Existing tests should still work because none of them call `run_ai_signin_pass` directly with a real Graph client; the few that do (`test_run_ai_signin_pass_returns_tuple`-like ones if they existed before) have been updated.

- [ ] **Step 7: Commit**

```bash
git add platform/lambda/shasta_runner_entra/app/ai_signin_pass.py \
        platform/lambda/shasta_runner_entra/app/tests/test_ai_signin_pass.py
git commit -m "feat(entra): ai_signin_pass returns premium_required signal"
```

---

## Task 2: `main.py` — write/clear the connection flag

**Files:**
- Modify: `platform/lambda/shasta_runner_entra/app/main.py`

### Context

The existing `handler` calls `run_ai_signin_pass(graph_client=None, ...)` inside a try/except and assigns to `ai_signin_params`. After Task 1, that call now returns a tuple. We need to:

1. Unpack the tuple at the call site.
2. Add a helper `_update_connection_premium_flag(conn_id, *, premium_required, signin_count)` that writes `scope.signin_premium_required=true` on 403, clears it when `signin_count > 0` (positive evidence), and no-ops otherwise.
3. Call the helper after the AI sign-in pass returns, inside the try/except so a write failure can't fail the scan.

- [ ] **Step 1: Read the current handler block**

```bash
sed -n '60,100p' /Users/kkmookhey/Projects/CISOBrief/platform/lambda/shasta_runner_entra/app/main.py
```

Confirm the structure (where `ai_signin_params` is assigned, where `_insert_finding_param_lists` is called).

- [ ] **Step 2: Update the call site to unpack the tuple**

In `platform/lambda/shasta_runner_entra/app/main.py`, find the existing block:

```python
        # NEW: AI sign-in pass.
        try:
            ai_signin_params = run_ai_signin_pass(
                graph_client=None,
                tenant_id=tenant_id, conn_id=conn_id, scan_id=scan_id,
                entra_tenant_id=entra_tenant_id,
            )
        except Exception as e:
            print(f"ai_signin_pass FAILED: {e}\n{traceback.format_exc()}")
            ai_signin_params = []

        if ai_signin_params:
            written += _insert_finding_param_lists(ai_signin_params)
```

Replace with:

```python
        # AI sign-in pass — returns (param_lists, premium_required) per S2.1.
        try:
            ai_signin_params, ai_signin_premium_required = run_ai_signin_pass(
                graph_client=None,
                tenant_id=tenant_id, conn_id=conn_id, scan_id=scan_id,
                entra_tenant_id=entra_tenant_id,
            )
        except Exception as e:
            print(f"ai_signin_pass FAILED: {e}\n{traceback.format_exc()}")
            ai_signin_params = []
            ai_signin_premium_required = False

        if ai_signin_params:
            written += _insert_finding_param_lists(ai_signin_params)

        # S2.1: write/clear the licensing banner flag on the connection.
        try:
            _update_connection_premium_flag(
                conn_id,
                premium_required=ai_signin_premium_required,
                signin_count=len(ai_signin_params),
            )
        except Exception as e:
            print(f"WARN: failed to update signin_premium_required flag: {e}")
```

- [ ] **Step 3: Add the helper near the other write helpers in `main.py`**

Find the bottom of `main.py` (near `_update_scan`). Add a new function:

```python
def _update_connection_premium_flag(conn_id: str, *,
                                    premium_required: bool,
                                    signin_count: int) -> None:
    """S2.1: sticky-flag the connection when Graph returned the
    licensing-403, or clear it when a future scan emitted real
    sign-in findings (positive evidence the licensing constraint
    is gone).

    Ambiguous case (no 403 AND no findings) is a no-op — could be
    a Premium tenant with no AI-app users yet, or a transient Graph
    issue. We don't want to clear a sticky flag without positive
    evidence.
    """
    if premium_required:
        sql = (
            "UPDATE cloud_connections "
            "SET scope = jsonb_set(COALESCE(scope, '{}'::jsonb), "
            "                      '{signin_premium_required}', 'true'::jsonb), "
            "    updated_at = now() "
            "WHERE conn_id = CAST(:cid AS UUID)"
        )
        rds_data.execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql=sql,
            parameters=[{"name": "cid", "value": {"stringValue": conn_id}}],
        )
    elif signin_count > 0:
        sql = (
            "UPDATE cloud_connections "
            "SET scope = scope #- '{signin_premium_required}', "
            "    updated_at = now() "
            "WHERE conn_id = CAST(:cid AS UUID)"
        )
        rds_data.execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql=sql,
            parameters=[{"name": "cid", "value": {"stringValue": conn_id}}],
        )
    # else: ambiguous case, no write
```

- [ ] **Step 4: Run the runner test suite, confirm no regressions**

```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform/lambda/shasta_runner_entra/app && \
  /Users/kkmookhey/Projects/CISOBrief/platform/lambda/shasta_runner/.venv/bin/python -m pytest tests/ -v
```

Expected: 12+ tests pass (no new tests added for the helper — it's a thin SQL wrapper exercised end-to-end at smoke time).

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/shasta_runner_entra/app/main.py
git commit -m "feat(entra): write/clear scope.signin_premium_required on scan"
```

---

## Task 3: Web — extend Connection type + render banner

**Files:**
- Modify: `web/src/lib/api.ts` (one-line type extension)
- Modify: `web/src/routes/ConnectClouds.tsx` (add LicensingBanner inside ConnectionRow)
- Create OR modify: `web/src/routes/ConnectClouds.test.tsx` (vitest cases)

### Context

The existing `ConnectionRow` (line 318 of `ConnectClouds.tsx`) renders one connection's summary. We add a `<LicensingBanner>` mini-component that renders ONLY when `conn.cloud_type === 'entra' && conn.scope?.signin_premium_required === true`. Banner sits beneath the row's main content, inside the same `<li>` element so the list-divide visual works.

- [ ] **Step 1: Extend the Connection type**

In `web/src/lib/api.ts`, find lines 52-55 (the `scope?:` field). Modify:

```typescript
  scope?:             { subscriptions?: string[]; selected?: string[];
                        subscription_names?: Record<string, string>;
                        mode?:               string;
                        projects?:           Record<string, string>;
                        signin_premium_required?: boolean };
```

Just one new key added: `signin_premium_required?: boolean`.

- [ ] **Step 2: Read the current `ConnectionRow` to see how it's structured**

```bash
sed -n '318,400p' /Users/kkmookhey/Projects/CISOBrief/web/src/routes/ConnectClouds.tsx
```

Confirm the `<li>` structure, identify where to slot the banner.

- [ ] **Step 3: Write the failing vitest cases**

Open or create `web/src/routes/ConnectClouds.test.tsx`. Add (if file doesn't exist, set up the full skeleton; if it exists, append the new cases):

```tsx
// web/src/routes/ConnectClouds.test.tsx
import { render, screen } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { ConnectionRow } from './ConnectClouds';
import type { Connection } from '../lib/api';

function renderRow(conn: Connection) {
  return render(
    <BrowserRouter>
      <ul>
        <ConnectionRow conn={conn} actionMsg={undefined} onDelete={vi.fn()} />
      </ul>
    </BrowserRouter>,
  );
}

const baseEntraConn: Connection = {
  conn_id:            'c-1',
  cloud_type:         'entra',
  display_name:       'Acme Entra',
  status:             'active',
  account_identifier: 'tenant-abc',
  signals:            { pull_scan: true },
  last_scan_at:       '2026-05-23T19:40:59Z',
  created_at:         '2026-05-20T00:00:00Z',
  scope:              {},
  latest_scan:        null,
};

describe('ConnectionRow — licensing banner (S2.1)', () => {
  it('renders the banner on an Entra row when signin_premium_required is true', () => {
    renderRow({ ...baseEntraConn,
                scope: { signin_premium_required: true } });
    expect(screen.getByText(/Microsoft Entra ID P1 or P2/i)).toBeTruthy();
    expect(screen.getByText(/Learn more about Entra ID licensing/i)).toBeTruthy();
  });

  it('does NOT render the banner on an Entra row when the flag is absent', () => {
    renderRow({ ...baseEntraConn, scope: {} });
    expect(screen.queryByText(/Microsoft Entra ID P1 or P2/i)).toBeNull();
  });

  it('does NOT render the banner on a non-Entra row even if the flag is set', () => {
    renderRow({ ...baseEntraConn,
                cloud_type: 'aws',
                scope: { signin_premium_required: true } });
    expect(screen.queryByText(/Microsoft Entra ID P1 or P2/i)).toBeNull();
  });
});
```

**Note:** The test imports `ConnectionRow` as a named export. The existing code (line 318) defines it as `function ConnectionRow({...})` — if not currently exported, **export it** (one-line change: add `export` keyword). Same for the `Connection` type from `api.ts`.

- [ ] **Step 4: Run the tests to confirm fail**

```bash
cd /Users/kkmookhey/Projects/CISOBrief/web && pnpm test -- ConnectClouds --run 2>&1 | tail -15
```

Expected: tests fail (banner not rendered, or import error if `ConnectionRow` isn't exported).

- [ ] **Step 5: Implement `LicensingBanner` and wire it into `ConnectionRow`**

In `web/src/routes/ConnectClouds.tsx`:

(a) If `ConnectionRow` is not exported, add `export` to its declaration:

```tsx
export function ConnectionRow({ ... }) {
```

(b) Add the `LicensingBanner` component at the bottom of the file (or just above `ConnectionRow`):

```tsx
function LicensingBanner() {
  return (
    <div className="mt-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm">
      <div className="font-medium text-amber-900">
        ⚠ Sign-in detection requires Microsoft Entra ID P1 or P2
      </div>
      <p className="mt-1 text-amber-800">
        Microsoft restricts <code className="text-xs">/auditLogs/signIns</code> to
        Premium-licensed tenants. Your tenant is on the Free tier, so AI SaaS
        sign-in events can't be detected. All other Entra checks ran normally.
      </p>
      <a
        href="https://learn.microsoft.com/en-us/entra/fundamentals/whatis"
        target="_blank"
        rel="noopener noreferrer"
        className="mt-2 inline-block text-amber-900 underline hover:text-amber-700"
      >
        Learn more about Entra ID licensing →
      </a>
    </div>
  );
}
```

(c) Modify `ConnectionRow` to render the banner. Find the existing `<li>` body (the row's main content) and add the banner **after** the existing row content but **inside** the same `<li>`:

```tsx
export function ConnectionRow({ conn, actionMsg, onDelete }: ...) {
  const showLicensingBanner =
    conn.cloud_type === 'entra' && conn.scope?.signin_premium_required === true;

  return (
    <li className="py-3">
      {/* ... existing row content unchanged ... */}

      {showLicensingBanner && <LicensingBanner />}
    </li>
  );
}
```

- [ ] **Step 6: Run vitest, confirm pass**

```bash
cd /Users/kkmookhey/Projects/CISOBrief/web && pnpm test -- ConnectClouds --run 2>&1 | tail -10
```

Expected: 3 PASS for the banner tests, plus whatever existing ConnectClouds tests there are (if any) still passing.

- [ ] **Step 7: Run the full vitest suite, confirm no regressions**

```bash
cd /Users/kkmookhey/Projects/CISOBrief/web && pnpm test --run 2>&1 | tail -10
```

Expected: all tests pass.

- [ ] **Step 8: Build the web bundle**

```bash
cd /Users/kkmookhey/Projects/CISOBrief/web && pnpm build 2>&1 | tail -6
```

Expected: build succeeds (typecheck included).

- [ ] **Step 9: Commit**

```bash
git add web/src/lib/api.ts \
        web/src/routes/ConnectClouds.tsx \
        web/src/routes/ConnectClouds.test.tsx
git commit -m "feat(web): /connect renders Entra licensing banner from scope flag"
```

---

## Task 4: Rebuild + push entra runner image, deploy web (USER-GATED)

**Files:** none — build + deploy operations only.

- [ ] **Step 1: Rebuild + push the entra scanner image**

```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform/lambda/shasta_runner_entra
./build.sh
```

Expected: ECR push completes; new digest printed.

- [ ] **Step 2: Force Lambda to re-resolve `:latest`**

```bash
aws lambda update-function-code \
  --function-name ciso-copilot-shasta-runner-entra \
  --image-uri 470226123496.dkr.ecr.us-east-1.amazonaws.com/shasta-runner-entra:latest
```

Wait for `LastUpdateStatus=Successful`:

```bash
until [ "$(aws lambda get-function-configuration --function-name ciso-copilot-shasta-runner-entra --query 'LastUpdateStatus' --output text)" = "Successful" ]; do sleep 5; done
```

Expected: status flips to `Successful` within ~30 seconds.

- [ ] **Step 3: Deploy web**

```bash
cd /Users/kkmookhey/Projects/CISOBrief/web && \
  pnpm build && \
  aws s3 sync dist/ s3://ciso-copilot-app-470226123496/ --delete && \
  aws cloudfront create-invalidation --distribution-id E2FV1Z0DJ4RQS4 --paths '/*' \
    --query 'Invalidation.{Id:Id,Status:Status}' --output text
```

Expected: CloudFront invalidation `InProgress` (becomes `Completed` in ~1-3 minutes).

- [ ] **Step 4: No commit** — these are deploy operations only.

---

## Task 5: Smoke verify (KK-gated)

**Files:** none — verification.

- [ ] **Step 1: Rescan an Entra-connected tenant**

KK clicks "Scan" on the Entra row at `https://shasta.transilience.cloud/scan`. Wait for completion.

- [ ] **Step 2: Confirm the flag is set**

```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot \
  --sql "SELECT conn_id::text, scope FROM cloud_connections WHERE cloud_type='entra' AND status='active'"
```

Expected (Free-tier tenant): `scope` shows `{"signin_premium_required": true}`.

- [ ] **Step 3: Confirm the banner renders**

KK refreshes `https://shasta.transilience.cloud/connect` in an incognito window. Confirm the amber banner appears beneath the Entra row with the expected copy + Microsoft docs link.

- [ ] **Step 4: (Optional) confirm the clear-path works**

If KK has access to an Entra Premium tenant, connect it, scan it, and confirm the banner does NOT appear. Else: defer.

---

## Task 6: HANDOFF + push + open PR (USER-GATED for the push)

**Files:**
- Modify: `HANDOFF.md` — prepend S2.1 ship block.

- [ ] **Step 1: Prepend the S2.1 ship block to `HANDOFF.md`**

```markdown
## 🚀 AI Visibility v2 — Slice 2.1 shipped (2026-MM-DD)

Follow-on polish to S2. Spec
`docs/superpowers/specs/2026-05-24-entra-licensing-banner-design.md`;
plan `docs/superpowers/plans/2026-05-24-entra-licensing-banner-plan.md`.
Built subagent-driven on branch **`feat/ai-visibility-v2-slice-2.1`**
(XXX commits ahead of `main`).

**S2.1 — Entra Free-tier licensing banner — DONE.**

- **`ai_signin_pass.run_ai_signin_pass`** now returns
  `(param_lists, premium_required)`. The bool fires only on Microsoft's
  specific 403 `Authentication_RequestFromNonPremiumTenantOrB2CTenant`.
  Other 403s (revoked consent, missing scope) leave it False.
- **`shasta_runner_entra/main.py`** writes
  `cloud_connections.scope.signin_premium_required=true` on 403;
  clears the key on a scan that emits ≥1 AI sign-in finding;
  no-ops on the ambiguous case.
- **`/connect`** renders an inline amber banner beneath any Entra
  row with the flag set: "Sign-in detection requires Microsoft Entra
  ID P1 or P2" + Microsoft docs link.
- **No new endpoints, no CDK changes, no Lambda creates.**
  `connections_list` already returned `scope`; only its `Connection`
  TypeScript type gained one optional key.

**Live-verification:** [PASTE outcome]

**▶ NEXT** — S3 brainstorm (compliance mapping sweep + EU AI Act +
SOC 2 AI framework registry).
```

- [ ] **Step 2: Commit**

```bash
git add HANDOFF.md
git commit -m "docs(handoff): AI Visibility v2 Slice 2.1 shipped"
```

- [ ] **Step 3: Push + open PR**

```bash
git push -u origin feat/ai-visibility-v2-slice-2.1

gh pr create --title "feat: AI Visibility v2 Slice 2.1 — Entra Free-tier licensing banner" --body "$(cat <<'EOF'
## Summary
- `ai_signin_pass.run_ai_signin_pass` now returns `(param_lists, premium_required)`. The bool fires only on Microsoft's specific 403 `Authentication_RequestFromNonPremiumTenantOrB2CTenant`.
- `shasta_runner_entra/main.py` writes `cloud_connections.scope.signin_premium_required=true` on 403; clears on a scan that emits ≥1 AI sign-in finding; no-ops on the ambiguous case.
- `/connect` renders an inline amber banner beneath Entra rows with the flag set.
- Zero new endpoints, zero CDK changes, zero Lambda creates.

## Test plan
- [x] `pytest tests/test_ai_signin_pass.py` green (12 tests: 9 original + 3 new for licensing-403 / other-403 / tuple-return)
- [x] `pnpm test ConnectClouds` green (3 new vitest cases)
- [x] Scanner image rebuilt + pushed
- [x] Lambda re-resolved `:latest`
- [x] Web synced + CloudFront invalidated
- [ ] **KK-gated**: rescan KK's Free-tier Entra tenant; confirm `scope.signin_premium_required=true`; confirm banner renders on `/connect`

## Refs
- Spec: `docs/superpowers/specs/2026-05-24-entra-licensing-banner-design.md`
- Plan: `docs/superpowers/plans/2026-05-24-entra-licensing-banner-plan.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review

**Spec coverage** (vs `docs/superpowers/specs/2026-05-24-entra-licensing-banner-design.md`):

| Spec section | Task |
|---|---|
| §3 in-scope #1: `run_ai_signin_pass` returns tuple | Task 1 |
| §3 in-scope #2: `_fetch_signins` distinguishes 403 codes | Task 1 |
| §3 in-scope #3: `main.py` writes/clears flag | Task 2 |
| §3 in-scope #4: verify `connections_list` returns scope | **Pre-verified during plan-writing** — line 116 of connections_list/main.py confirmed |
| §3 in-scope #5: `/connect` renders banner | Task 3 |
| §6.2 SQL shape (jsonb_set / `#-`) | Task 2 Step 3 |
| §8 error-handling matrix | Task 1's licensing-vs-other test + Task 2's no-op case |
| §9 testing — 3 backend unit tests | Task 1 Step 2 (exactly 3 new tests) |
| §9 testing — 3 frontend vitest cases | Task 3 Step 3 |
| §9 testing — 1 manual smoke | Task 5 |
| §11 done definition #6: vitest + pytest pass | Tasks 1, 3 |
| §11 done definition #7: KK Free-tier banner appears | Task 5 |

**Placeholder scan:** Only `XXX` and `[PASTE outcome]` in the HANDOFF template at Task 6 Step 1 — intentional, filled in at ship time.

**Type consistency:**
- Tuple shape `(param_lists, premium_required)` consistent across `_fetch_signins`, `run_ai_signin_pass`, the call site in `main.py`, and all tests.
- `signin_premium_required` key name consistent across SQL (`'{signin_premium_required}'`), TS type, JSX condition, banner test assertion.
- Error code string `Authentication_RequestFromNonPremiumTenantOrB2CTenant` consistent across the `_LICENSING_ERROR_CODE` constant, both backend tests, and the spec.

**Pre-flight verifications eliminated risk in two places:**
- `connections_list` already returns `scope` → no Task needed.
- `Connection.scope` TS type already optional → one-key extension is the only TS change.
