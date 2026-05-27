# Scan Screen — Slice 2b Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the cross-cloud **Scan screen** (`/scan`) — a post-onboard landing where the user sees every active cloud, picks scope (subscriptions / projects), picks tier, and launches scans. Drop the silent auto-scan-on-onboarding across all four onboarding webhooks. Retire the Connect-page per-row ScanPicker and the inline Azure subscription checklist.

**Architecture:** A new `/scan` route renders one `ScanCard` per active connection, with a per-cloud body component (AWS/Azure/GCP/Entra). A "Launch all scans" button fires `POST /connections/{id}/rescan` for every card in parallel. Backend changes are minimal — the 4 onboarding webhooks simply stop inserting a scan row + calling the trigger; the Connect page surfaces a toast linking to `/scan` when it detects a `pending → active` transition. The shared `web/src/scan/` module (`ScanProgress`, `useScanStatus`) is reused without modification.

**Tech Stack:** React + TypeScript + Tailwind (web), Python 3.12 (Lambda).

**Spec:** `docs/superpowers/specs/2026-05-22-scan-screen-design.md`
**Predecessors (merged):** Slices 1a + 1b + 2a. The v2 GCP scanner runs end-to-end; org-mode onboarding code is live (live-verification still pending on KK's side).

---

## Background an implementer needs

- **Existing shared scan module** lives at `web/src/scan/` and is reused as-is: `useScanStatus(scanId)` polls `GET /v1/scans/{id}` until terminal; `<ScanProgress>` renders the live phase + counts; `scanLabels.ts` maps phases to user-friendly strings. Tests for these already pass.
- **API helpers** in `web/src/lib/api.ts`: `api.listConnections()`, `api.rescanConnection(connId, tier)`, `api.updateConnectionSubscriptions(connId, selected)`. The `updateConnectionSubscriptions` name is Azure-historical — it patches `scope.selected` via `PATCH /connections/{id}` and works for GCP org-mode connections too (the API endpoint validates `selected` against `scope.subscriptions` OR `scope.projects` depending on which is present — confirm by reading the backend in Task 6 if unsure; the existing endpoint may need a small rename/extension, captured in Task 6's optional sub-step).
- **Connection shape** returned by `GET /connections` has the keys: `conn_id`, `tenant_id`, `cloud_type`, `display_name`, `status`, `account_identifier`, `signals`, `last_scan_at`, `created_at`, `latest_scan` (`{scan_id, tier, status, phase, started_at}` or `null`), `scope` (the per-cloud-shape blob). The current `Connection` TypeScript type may already mirror this — check before adding new fields.
- **Nav placement**: `web/src/chat/ModuleRail.tsx` `BASE_ITEMS` array — add `{ to: "/scan", label: "Scan" }` right after the existing `"Connect clouds"` entry.
- **The current `ConnectClouds.tsx` (523 lines)** contains both `ScanPicker` (the tier dropdown) and `SubscriptionPicker` (the Azure subscription checklist). Both are *deleted* by Task 6 — not kept for coexistence. Lines marked for deletion are noted in Task 6.
- **`onboarding_gcp_complete` already does no auto-scan in org mode** (Slice 2a). Task 1 only needs to remove the project-mode auto-scan call.
- **Web build/typecheck**: `cd web && pnpm typecheck && pnpm build`. The web dev server: `pnpm dev` (Vite, default port). Browser-smoke verification of the live UI is human-gated (an agent can't pass Google OAuth) — Task 7 documents the smoke checklist; the agent reports "code shipped, browser-smoke pending".
- **Web deploy**: `pnpm build && aws s3 sync dist/ s3://<WEB_BUCKET>/ --delete && aws cloudfront create-invalidation --distribution-id <CLOUDFRONT_DIST_ID> --paths '/*'`.

## File structure

```
platform/lambda/onboarding_aws_complete/main.py     MODIFIED — drop auto-scan
platform/lambda/onboarding_azure_complete/main.py   MODIFIED — drop auto-scan
platform/lambda/onboarding_gcp_complete/main.py     MODIFIED — drop project-mode auto-scan (org already done)
platform/lambda/onboarding_entra_callback/main.py   MODIFIED — success redirect href = APP_DOMAIN + /scan
web/src/App.tsx                                     MODIFIED — register /scan route
web/src/chat/ModuleRail.tsx                         MODIFIED — add "Scan" nav entry
web/src/routes/Scan.tsx                             NEW — page shell + empty state + LaunchAll
web/src/scan/ScanCard.tsx                           NEW — shared card shell + per-cloud body router
web/src/scan/AwsScanCardBody.tsx                    NEW — tier picker only
web/src/scan/AzureScanCardBody.tsx                  NEW — subscription checklist + tier
web/src/scan/GcpScanCardBody.tsx                    NEW — project checklist (org mode) or tier-only (project mode)
web/src/scan/EntraScanCardBody.tsx                  NEW — empty body, just Scan
web/src/routes/ConnectClouds.tsx                    MODIFIED — remove ScanPicker + SubscriptionPicker; add toast on pending→active
HANDOFF.md                                          MODIFIED — record Slice 2b
```

---

### Task 1: Drop auto-scan from the four onboarding webhooks + Entra redirect

**Files:**
- Modify: `platform/lambda/onboarding_aws_complete/main.py`
- Modify: `platform/lambda/onboarding_azure_complete/main.py`
- Modify: `platform/lambda/onboarding_gcp_complete/main.py`
- Modify: `platform/lambda/onboarding_entra_callback/main.py`

Each onboarding webhook currently inserts a `scans` row + triggers the scanner immediately after the connection flips to `active`. Slice 2b replaces that with: do nothing — the Scan screen is the user's explicit trigger point. The Entra HTML success page additionally redirects the user to `/scan` (it's an in-app callback, so the redirect is direct).

#### 1.1 — `onboarding_aws_complete`

- [ ] **Step 1: Remove the `_enqueue_initial_scan` call in `handler`**

Open `platform/lambda/onboarding_aws_complete/main.py`. Find the line (around line 99):

```python
    scan_id = _enqueue_initial_scan(
```

…and the surrounding block — usually:

```python
    scan_id = _enqueue_initial_scan(
        tenant_id  = conn["tenant_id"],
        conn_id    = conn["conn_id"],
        account_id = aws_account_id,
        role_arn   = role_arn,
        external_id= external_id,
    )

    return _resp(200, {
        "status":          "active",
        "connection_id":   conn["conn_id"],
        "initial_scan_id": scan_id,
    })
```

Replace it with (the scan is no longer auto-fired; the response keeps `initial_scan_id` as `null` for backward compatibility with any web code reading the key):

```python
    # Slice 2b: no auto-scan on onboarding. The user starts the first
    # scan from /scan — a freshly onboarded connection appears with
    # latest_scan: null and a "Never scanned" badge.
    return _resp(200, {
        "status":          "active",
        "connection_id":   conn["conn_id"],
        "initial_scan_id": None,
    })
```

The `_enqueue_initial_scan` *function* and its imports may become unused — leave them in place for the moment (deleting unused code beyond this scope expands the diff; they can be cleaned up later).

#### 1.2 — `onboarding_azure_complete`

- [ ] **Step 2: Remove the `_run_initial_scan` call in `handler`**

Open `platform/lambda/onboarding_azure_complete/main.py`. Find the call site — usually:

```python
    initial_scan_id = _run_initial_scan(
        tenant_id        = conn["tenant_id"],
        conn_id          = conn["conn_id"],
        azure_tenant_id  = azure_tenant_id,
        client_id        = client_id,
        secret_arn       = secret_arn,
        subscription_ids = subscription_ids,
    )

    return _resp(200, {
        "status":          "active",
        "connection_id":   conn["conn_id"],
        "initial_scan_id": initial_scan_id,
    })
```

Replace with:

```python
    # Slice 2b: no auto-scan on onboarding (the Scan screen takes over).
    return _resp(200, {
        "status":          "active",
        "connection_id":   conn["conn_id"],
        "initial_scan_id": None,
    })
```

Leave `_run_initial_scan` in place (unused, ignorable).

#### 1.3 — `onboarding_gcp_complete`

- [ ] **Step 3: Remove the project-mode `_run_initial_scan` branch (org branch already returns None)**

Open `platform/lambda/onboarding_gcp_complete/main.py`. Find the conditional installed by Slice 2a:

```python
    if mode == "org":
        # Org mode does not auto-scan — the project list is empty until the
        # scanner enumerates on first scan. The user starts the scan manually
        # (Connect-page rescan today; the /scan screen after Slice 2b).
        initial_scan_id = None
    else:
        initial_scan_id = _run_initial_scan(
            tenant_id = conn["tenant_id"],
            conn_id   = conn["conn_id"],
            scope     = scope,
        )

    return _resp(200, {
        "status":          "active",
        "connection_id":   conn["conn_id"],
        "mode":            mode,
        "initial_scan_id": initial_scan_id,
    })
```

Replace the whole conditional + return with:

```python
    # Slice 2b: no auto-scan in either mode — the Scan screen is the
    # trigger. A freshly onboarded GCP connection appears with
    # latest_scan: null; the user clicks Scan on /scan.
    return _resp(200, {
        "status":          "active",
        "connection_id":   conn["conn_id"],
        "mode":            mode,
        "initial_scan_id": None,
    })
```

#### 1.4 — `onboarding_entra_callback` — success redirect

- [ ] **Step 4: Make the HTML success page link to `/scan`**

Open `platform/lambda/onboarding_entra_callback/main.py`. Find the `_html_redirect` function (around line 147). The `<a>` href currently points at `APP_DOMAIN`:

```python
        f'<a href="{APP_DOMAIN}" style="color:#2563eb;text-decoration:none;font-weight:500;">'
```

Replace it with (rstrip the trailing slash if any, then add `/scan`):

```python
        f'<a href="{APP_DOMAIN.rstrip("/")}/scan" style="color:#2563eb;text-decoration:none;font-weight:500;">'
```

Update the surrounding link text. Find:

```python
"← Back to CISO Copilot</a>"
```

Replace with:

```python
"Run your first scan →</a>"
```

#### 1.5 — Verify and commit

- [ ] **Step 5: AST-parse all four files**

Run:

```bash
for f in onboarding_aws_complete onboarding_azure_complete onboarding_gcp_complete onboarding_entra_callback; do
  python3 -c "import ast; ast.parse(open('platform/lambda/$f/main.py').read()); print('$f: parses OK')"
done
```

Expected: 4 `parses OK` lines.

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/onboarding_aws_complete/main.py \
        platform/lambda/onboarding_azure_complete/main.py \
        platform/lambda/onboarding_gcp_complete/main.py \
        platform/lambda/onboarding_entra_callback/main.py
git commit -m "feat: onboarding webhooks stop auto-scanning; entra redirects to /scan"
```

---

### Task 2: `/scan` route, nav entry, page shell, empty state

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/chat/ModuleRail.tsx`
- Create: `web/src/routes/Scan.tsx`

- [ ] **Step 1: Register the route**

Open `web/src/App.tsx`. Find the existing `<Route element={<Shell />}>` block (around line 35-46). Inside it, immediately after the `<Route path="/connect" ... />` line, add:

```tsx
          <Route path="/scan"      element={<Scan />} />
```

At the top of the file, add the import alongside the other route imports:

```tsx
import Scan from "./routes/Scan";
```

- [ ] **Step 2: Add the nav entry**

Open `web/src/chat/ModuleRail.tsx`. Find the `BASE_ITEMS` array. Add `{ to: "/scan", label: "Scan" }` immediately AFTER `{ to: "/connect", label: "Connect clouds" }`:

```typescript
const BASE_ITEMS: Array<{ to: string; label: string }> = [
  { to: "/",               label: "Chat" },
  { to: "/dashboard",      label: "Dashboard" },
  { to: "/findings",       label: "Findings" },
  { to: "/risks",          label: "Risk register" },
  { to: "/policies",       label: "Policies" },
  { to: "/questionnaires", label: "Questionnaires" },
  { to: "/trust",          label: "Trust center" },
  { to: "/ai/inventory",   label: "AI inventory" },
  { to: "/connect",        label: "Connect clouds" },
  { to: "/scan",           label: "Scan" },
];
```

- [ ] **Step 3: Create the page shell**

Create `web/src/routes/Scan.tsx` with:

```tsx
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Connection } from "../lib/api";
import { ScanCard } from "../scan/ScanCard";

export default function Scan() {
  const [conns, setConns] = useState<Connection[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);

  async function reload() {
    try {
      const { connections } = await api.listConnections();
      setConns(connections);
    } catch (e) {
      setErr(String(e));
    }
  }

  useEffect(() => { reload(); }, []);

  const active   = (conns ?? []).filter(c => c.status === "active");
  const pending  = (conns ?? []).filter(c => c.status !== "active");

  async function launchAll() {
    setLaunching(true);
    try {
      // Best-effort parallel. Failures surface in the per-card UI on next
      // reload; no transaction.
      await Promise.allSettled(
        active.map(c => api.rescanConnection(c.conn_id, "quick"))
      );
      await reload();
    } finally {
      setLaunching(false);
    }
  }

  if (conns === null && !err) {
    return <div className="p-8 text-stone-500">Loading…</div>;
  }
  if (err) {
    return <div className="p-8 text-red-700">Failed to load connections: {err}</div>;
  }
  if (active.length === 0 && pending.length === 0) {
    return (
      <div className="max-w-xl mx-auto mt-16 text-center">
        <h1 className="text-2xl font-semibold text-stone-800">No clouds connected yet</h1>
        <p className="mt-3 text-stone-600">
          Connect a cloud to start scanning.
        </p>
        <Link to="/connect"
              className="mt-6 inline-block px-5 py-2 rounded-md bg-orange-600 text-white font-medium hover:bg-orange-700">
          Connect a cloud →
        </Link>
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold text-stone-800">Scan</h1>
        {active.length > 1 && (
          <button onClick={launchAll} disabled={launching}
            className="px-4 py-2 rounded-md bg-orange-600 text-white font-medium hover:bg-orange-700 disabled:opacity-50">
            {launching ? "Launching…" : "Launch all scans"}
          </button>
        )}
      </div>

      <div className="space-y-4">
        {active.map(conn => (
          <ScanCard key={conn.conn_id} conn={conn} onChanged={reload} />
        ))}
      </div>

      {pending.length > 0 && (
        <div className="mt-8 p-4 border border-stone-300 rounded-md bg-stone-50 text-sm text-stone-600">
          <div className="font-medium mb-1">Not ready to scan</div>
          {pending.map(c => (
            <div key={c.conn_id}>
              {c.cloud_type.toUpperCase()} {c.account_identifier ?? "(pending)"} — {c.status}
            </div>
          ))}
          <Link to="/connect" className="mt-2 inline-block text-orange-700 underline">
            Go to Connect →
          </Link>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Typecheck**

Run: `cd web && pnpm typecheck`
Expected: 0 errors. (Type errors will reference `ScanCard` — that's expected; Task 3 creates it. Just confirm no NEW errors beyond `ScanCard not found`. If the existing baseline is dirty per `project_web_lint_baseline`, that's fine — only flag new errors introduced by this task.)

Run `pnpm typecheck` from `web/`. If the only new error is `Module '"../scan/ScanCard"' has no exported member 'ScanCard'`, that is expected and resolves in Task 3. Move on.

- [ ] **Step 5: Commit**

```bash
git add web/src/App.tsx web/src/chat/ModuleRail.tsx web/src/routes/Scan.tsx
git commit -m "feat: /scan route + nav entry + page shell"
```

---

### Task 3: `ScanCard` shell + cloud-router

**File:** Create `web/src/scan/ScanCard.tsx`

The card shell handles the header (cloud name + last-scan pill + new-connection badge) and delegates the body to the cloud-specific component. While a scan is running it renders `<ScanProgress>` in place of the body.

- [ ] **Step 1: Create `ScanCard.tsx`**

Create `web/src/scan/ScanCard.tsx`:

```tsx
import { useState } from "react";
import type { Connection } from "../lib/api";
import { ScanProgress } from "./ScanProgress";
import { AwsScanCardBody } from "./AwsScanCardBody";
import { AzureScanCardBody } from "./AzureScanCardBody";
import { GcpScanCardBody } from "./GcpScanCardBody";
import { EntraScanCardBody } from "./EntraScanCardBody";

interface Props {
  conn: Connection;
  onChanged: () => void;        // re-fetches connections after scope edits / scan starts
}

export function ScanCard({ conn, onChanged }: Props) {
  // While a scan is running the body is replaced by ScanProgress; once
  // terminal we re-render the body. Local state captures the *current*
  // running scan_id so the body can re-mount after completion.
  const [runningScanId, setRunningScanId] = useState<string | null>(
    conn.latest_scan?.status === "running" ? conn.latest_scan.scan_id : null
  );

  const cloud = conn.cloud_type;
  const isNew = conn.latest_scan === null;
  const lastStatus = conn.latest_scan?.status;

  const accountLabel = conn.account_identifier ?? "(pending)";

  return (
    <div className={
      "border rounded-md bg-white shadow-sm " +
      (isNew ? "border-orange-400 ring-1 ring-orange-200" : "border-stone-200")
    }>
      <div className="flex items-center justify-between px-4 py-3 border-b border-stone-200">
        <div className="flex items-center gap-3">
          <span className="font-semibold uppercase text-xs text-stone-500">{cloud}</span>
          <span className="font-medium text-stone-800">{accountLabel}</span>
          {isNew && (
            <span className="text-xs px-2 py-0.5 bg-orange-100 text-orange-800 rounded">
              Never scanned
            </span>
          )}
          {lastStatus === "failed" && (
            <span className="text-xs px-2 py-0.5 bg-red-100 text-red-800 rounded">
              Last scan failed
            </span>
          )}
          {lastStatus === "partial" && (
            <span className="text-xs px-2 py-0.5 bg-amber-100 text-amber-800 rounded">
              Last scan partial
            </span>
          )}
        </div>
        {conn.latest_scan && (
          <span className="text-xs text-stone-500">
            Last scan: {conn.latest_scan.tier} · {conn.latest_scan.status}
          </span>
        )}
      </div>

      <div className="p-4">
        {runningScanId ? (
          <ScanProgress
            scanId={runningScanId}
            onTerminal={() => { setRunningScanId(null); onChanged(); }}
          />
        ) : (
          <CardBody
            conn={conn}
            onScanStarted={(scanId) => { setRunningScanId(scanId); }}
            onChanged={onChanged}
          />
        )}
      </div>
    </div>
  );
}

function CardBody({ conn, onScanStarted, onChanged }: {
  conn: Connection;
  onScanStarted: (scanId: string) => void;
  onChanged: () => void;
}) {
  switch (conn.cloud_type) {
    case "aws":   return <AwsScanCardBody   conn={conn} onScanStarted={onScanStarted} />;
    case "azure": return <AzureScanCardBody conn={conn} onScanStarted={onScanStarted} onChanged={onChanged} />;
    case "gcp":   return <GcpScanCardBody   conn={conn} onScanStarted={onScanStarted} onChanged={onChanged} />;
    case "entra": return <EntraScanCardBody conn={conn} onScanStarted={onScanStarted} />;
    default:      return <div className="text-stone-500">Unknown cloud type: {conn.cloud_type}</div>;
  }
}
```

- [ ] **Step 2: Verify `ScanProgress` has an `onTerminal` callback**

Run: `grep -n "onTerminal\|interface.*Props\|export function ScanProgress" web/src/scan/ScanProgress.tsx`

If `onTerminal` is NOT in the existing `ScanProgress` props, add it. The component is small (~50 lines); the addition is:
- Add `onTerminal?: () => void;` to the Props interface.
- In the `useEffect` that watches `status` for terminal values (`completed`/`failed`/`partial`), invoke `onTerminal?.()`.

(If you make this change, include `ScanProgress.tsx` in the Task 3 commit. If it already has `onTerminal`, no change needed.)

- [ ] **Step 3: Typecheck**

Run: `cd web && pnpm typecheck`
Expected: new errors only reference `AwsScanCardBody` / `AzureScanCardBody` / `GcpScanCardBody` / `EntraScanCardBody` (created in Task 4). No errors in `ScanCard.tsx` itself, no errors in `Scan.tsx`.

- [ ] **Step 4: Commit**

```bash
git add web/src/scan/ScanCard.tsx
# Also stage web/src/scan/ScanProgress.tsx if you modified it in Step 2.
git commit -m "feat: ScanCard shell — header, state machine, per-cloud body router"
```

---

### Task 4: Per-cloud card bodies (AWS, Azure, GCP, Entra)

**Files (create all four):**
- `web/src/scan/AwsScanCardBody.tsx`
- `web/src/scan/AzureScanCardBody.tsx`
- `web/src/scan/GcpScanCardBody.tsx`
- `web/src/scan/EntraScanCardBody.tsx`

Each body owns its own picker form + Scan button. All four share a small shape: receive `conn` + `onScanStarted(scanId)`; the Azure/GCP variants also receive `onChanged` so the page can refetch after a `PATCH /connections/{id}` saves scope.

- [ ] **Step 1: AWS body — tier only**

Create `web/src/scan/AwsScanCardBody.tsx`:

```tsx
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type Connection } from "../lib/api";

interface Props {
  conn: Connection;
  onScanStarted: (scanId: string) => void;
}

export function AwsScanCardBody({ conn, onScanStarted }: Props) {
  const [tier, setTier] = useState<"quick" | "medium">("quick");
  const [busy, setBusy] = useState(false);
  const [err, setErr]   = useState<string | null>(null);

  async function startScan() {
    setBusy(true); setErr(null);
    try {
      const { scan_id } = await api.rescanConnection(conn.conn_id, tier);
      onScanStarted(scan_id);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="text-sm text-stone-600">
        Regions are auto-discovered by the scanner — no scope picker for AWS.
      </div>
      <div className="flex items-center gap-3">
        <label className="text-sm font-medium text-stone-700">Tier</label>
        <select value={tier} onChange={e => setTier(e.target.value as "quick" | "medium")}
                className="px-2 py-1 border rounded">
          <option value="quick">Quick</option>
          <option value="medium">Medium</option>
        </select>
        <Link to="/contact/deep-scan" className="text-xs text-stone-500 underline">
          Deep? Contact us
        </Link>
      </div>
      {err && <div className="text-sm text-red-700">{err}</div>}
      <button onClick={startScan} disabled={busy}
        className="px-4 py-1.5 rounded-md bg-orange-600 text-white text-sm font-medium hover:bg-orange-700 disabled:opacity-50">
        {busy ? "Starting…" : "Scan"}
      </button>
    </div>
  );
}
```

- [ ] **Step 2: Azure body — subscription checklist + tier**

Create `web/src/scan/AzureScanCardBody.tsx`:

```tsx
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type Connection } from "../lib/api";

interface Props {
  conn: Connection;
  onScanStarted: (scanId: string) => void;
  onChanged: () => void;
}

export function AzureScanCardBody({ conn, onScanStarted, onChanged }: Props) {
  const allSubs = conn.scope?.subscriptions ?? [];
  const names   = conn.scope?.subscription_names ?? {};
  const initial = new Set<string>(conn.scope?.selected ?? allSubs);
  const [selected, setSelected] = useState<Set<string>>(initial);
  const [tier, setTier]         = useState<"quick" | "medium">("quick");
  const [busy, setBusy]         = useState(false);
  const [err, setErr]           = useState<string | null>(null);

  function toggle(sub: string) {
    const next = new Set(selected);
    if (next.has(sub)) { next.delete(sub); } else { next.add(sub); }
    setSelected(next);
  }

  const changed = !setsEqual(selected, new Set(conn.scope?.selected ?? allSubs));

  async function startScan() {
    if (selected.size === 0) { setErr("Select at least one subscription"); return; }
    setBusy(true); setErr(null);
    try {
      if (changed) {
        await api.updateConnectionSubscriptions(conn.conn_id, [...selected]);
        onChanged();
      }
      const { scan_id } = await api.rescanConnection(conn.conn_id, tier);
      onScanStarted(scan_id);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div>
        <div className="text-sm font-medium text-stone-700 mb-1">
          Subscriptions ({selected.size} of {allSubs.length})
        </div>
        <div className="max-h-48 overflow-auto border rounded p-2 space-y-1 text-sm">
          {allSubs.map(sub => (
            <label key={sub} className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={selected.has(sub)}
                     onChange={() => toggle(sub)} />
              <span className="text-stone-700">{names[sub] ?? sub}</span>
              {names[sub] && <span className="text-xs text-stone-400">{sub}</span>}
            </label>
          ))}
        </div>
      </div>
      <div className="flex items-center gap-3">
        <label className="text-sm font-medium text-stone-700">Tier</label>
        <select value={tier} onChange={e => setTier(e.target.value as "quick" | "medium")}
                className="px-2 py-1 border rounded">
          <option value="quick">Quick</option>
          <option value="medium">Medium</option>
        </select>
        <Link to="/contact/deep-scan" className="text-xs text-stone-500 underline">
          Deep? Contact us
        </Link>
      </div>
      {err && <div className="text-sm text-red-700">{err}</div>}
      <button onClick={startScan} disabled={busy || selected.size === 0}
        className="px-4 py-1.5 rounded-md bg-orange-600 text-white text-sm font-medium hover:bg-orange-700 disabled:opacity-50">
        {busy ? "Starting…" : "Scan"}
      </button>
    </div>
  );
}

function setsEqual<T>(a: Set<T>, b: Set<T>): boolean {
  if (a.size !== b.size) return false;
  for (const x of a) if (!b.has(x)) return false;
  return true;
}
```

- [ ] **Step 3: GCP body — branches on `scope.mode`**

Create `web/src/scan/GcpScanCardBody.tsx`:

```tsx
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type Connection } from "../lib/api";

interface Props {
  conn: Connection;
  onScanStarted: (scanId: string) => void;
  onChanged: () => void;
}

export function GcpScanCardBody({ conn, onScanStarted, onChanged }: Props) {
  const mode = (conn.scope?.mode as string | undefined) ?? "project";
  const [tier, setTier] = useState<"quick" | "medium">("quick");
  const [busy, setBusy] = useState(false);
  const [err, setErr]   = useState<string | null>(null);

  // Org mode: project checklist. Project mode: empty body (tier only).
  const projectsObj = (conn.scope?.projects as Record<string, string> | undefined) ?? {};
  const allProjects = Object.keys(projectsObj);
  const initial     = new Set<string>(conn.scope?.selected ?? allProjects);
  const [selected, setSelected] = useState<Set<string>>(initial);

  function toggle(pid: string) {
    const next = new Set(selected);
    if (next.has(pid)) { next.delete(pid); } else { next.add(pid); }
    setSelected(next);
  }
  const changed = mode === "org" && !setsEqual(selected, new Set(conn.scope?.selected ?? allProjects));

  async function startScan() {
    if (mode === "org" && allProjects.length > 0 && selected.size === 0) {
      setErr("Select at least one project");
      return;
    }
    setBusy(true); setErr(null);
    try {
      if (changed) {
        await api.updateConnectionSubscriptions(conn.conn_id, [...selected]);
        onChanged();
      }
      const { scan_id } = await api.rescanConnection(conn.conn_id, tier);
      onScanStarted(scan_id);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      {mode === "org" && allProjects.length === 0 && (
        <div className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded p-2">
          Projects are discovered on the first scan. Click Scan to enumerate
          and scan everything in the organisation; you can trim the set
          before subsequent scans.
        </div>
      )}
      {mode === "org" && allProjects.length > 0 && (
        <div>
          <div className="text-sm font-medium text-stone-700 mb-1">
            Projects ({selected.size} of {allProjects.length})
          </div>
          {allProjects.length > 10 && (
            <div className="text-xs text-stone-500 mb-1">
              Trim to your prod projects for a faster first scan.
            </div>
          )}
          <div className="max-h-64 overflow-auto border rounded p-2 space-y-1 text-sm">
            {allProjects.map(pid => (
              <label key={pid} className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={selected.has(pid)}
                       onChange={() => toggle(pid)} />
                <span className="text-stone-700">{projectsObj[pid] || pid}</span>
                {projectsObj[pid] && projectsObj[pid] !== pid && (
                  <span className="text-xs text-stone-400">{pid}</span>
                )}
              </label>
            ))}
          </div>
        </div>
      )}
      <div className="flex items-center gap-3">
        <label className="text-sm font-medium text-stone-700">Tier</label>
        <select value={tier} onChange={e => setTier(e.target.value as "quick" | "medium")}
                className="px-2 py-1 border rounded">
          <option value="quick">Quick</option>
          <option value="medium">Medium</option>
        </select>
        <Link to="/contact/deep-scan" className="text-xs text-stone-500 underline">
          Deep? Contact us
        </Link>
      </div>
      {err && <div className="text-sm text-red-700">{err}</div>}
      <button onClick={startScan} disabled={busy}
        className="px-4 py-1.5 rounded-md bg-orange-600 text-white text-sm font-medium hover:bg-orange-700 disabled:opacity-50">
        {busy ? "Starting…" : "Scan"}
      </button>
    </div>
  );
}

function setsEqual<T>(a: Set<T>, b: Set<T>): boolean {
  if (a.size !== b.size) return false;
  for (const x of a) if (!b.has(x)) return false;
  return true;
}
```

- [ ] **Step 4: Entra body — empty, just Scan**

Create `web/src/scan/EntraScanCardBody.tsx`:

```tsx
import { useState } from "react";
import { api, type Connection } from "../lib/api";

interface Props {
  conn: Connection;
  onScanStarted: (scanId: string) => void;
}

export function EntraScanCardBody({ conn, onScanStarted }: Props) {
  const [busy, setBusy] = useState(false);
  const [err, setErr]   = useState<string | null>(null);

  async function startScan() {
    setBusy(true); setErr(null);
    try {
      const { scan_id } = await api.rescanConnection(conn.conn_id, "quick");
      onScanStarted(scan_id);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="text-sm text-stone-600">
        The tenant is the scope — no scope picker, no tier choice.
      </div>
      {err && <div className="text-sm text-red-700">{err}</div>}
      <button onClick={startScan} disabled={busy}
        className="px-4 py-1.5 rounded-md bg-orange-600 text-white text-sm font-medium hover:bg-orange-700 disabled:opacity-50">
        {busy ? "Starting…" : "Scan"}
      </button>
    </div>
  );
}
```

- [ ] **Step 5: Typecheck and build**

Run: `cd web && pnpm typecheck && pnpm build`

Expected: no NEW type errors. Build succeeds. (Pre-existing baseline lint errors per the project's `web_lint_baseline` memory may still be present — only flag new errors.)

If `api.rescanConnection` only accepts `"medium"` and rejects `"quick"`, check `web/src/lib/api.ts` line 414 — the signature should already accept `"quick" | "medium"` (it was made tier-aware in earlier slices). If not, widen it.

If the `Connection` type doesn't have a `scope.mode` or `scope.projects` field, extend the interface in `web/src/lib/api.ts` to include them (optional fields, GCP-only).

- [ ] **Step 6: Commit**

```bash
git add web/src/scan/AwsScanCardBody.tsx \
        web/src/scan/AzureScanCardBody.tsx \
        web/src/scan/GcpScanCardBody.tsx \
        web/src/scan/EntraScanCardBody.tsx \
        web/src/lib/api.ts
git commit -m "feat: per-cloud card bodies (aws/azure/gcp/entra)"
```

(Include `api.ts` if you extended the `Connection` type.)

---

### Task 5: Connect-page retrofit — remove pickers, add toast

**File:** `web/src/routes/ConnectClouds.tsx`

Two changes:
1. Remove the `ScanPicker` component and all its call sites; remove the `SubscriptionPicker` component and its call site.
2. Add a small toast component that surfaces when a connection transitions from a non-`active` status to `active` on the polling refresh. The toast links to `/scan`.

- [ ] **Step 1: Identify and remove the picker call sites + components**

Open `web/src/routes/ConnectClouds.tsx`. The current file has, around line 441-465, a render block that includes:

```tsx
            <ScanPicker
              ...
            />
        ...
        <SubscriptionPicker conn={conn} onSaved={onConnSaved} />
```

Delete those entire blocks (the JSX uses + the prop chains that flow into them).

Find the `function ScanPicker(...)` and `function SubscriptionPicker(...)` definitions later in the file (lines 286-470ish) and DELETE both functions in their entirety.

Audit imports at the top — remove any imports that become unused (e.g., `useScanStatus` if no longer referenced from this file; `scanLabels` if no longer used here). Run `pnpm typecheck` after deletion to find newly-unused imports.

The connection-row render should become:

```tsx
<div key={conn.conn_id} className="border rounded-md p-4 bg-white">
  <div className="flex items-center justify-between">
    <div>
      <div className="font-medium text-stone-800">
        {conn.cloud_type.toUpperCase()}{" "}
        {conn.account_identifier ?? `Added ${formatDate(conn.created_at)}`}
      </div>
      <div className="text-xs text-stone-500 mt-1">
        {conn.status}
        {conn.latest_scan && (
          <> · last scan: {conn.latest_scan.tier} · {conn.latest_scan.status}</>
        )}
      </div>
    </div>
    <button onClick={() => onDelete(conn.conn_id)}
            className="text-red-600 hover:underline text-sm">
      Delete
    </button>
  </div>
</div>
```

(If `formatDate` doesn't exist, inline `new Date(conn.created_at).toLocaleDateString()`.)

- [ ] **Step 2: Add a pending→active toast**

Near the top of the `ConnectClouds` component (the default-exported function), add toast state:

```tsx
import { useNavigate } from "react-router-dom";
// ... existing imports ...

export default function ConnectClouds() {
  const [conns, setConns] = useState<Connection[]>([]);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [toast, setToast] = useState<{ cloud: string } | null>(null);
  const prevStatusesRef = useRef<Record<string, string>>({});
  const nav = useNavigate();

  function reloadConnections() {
    api.listConnections().then(({ connections }) => {
      // Detect pending → active transitions
      for (const c of connections) {
        const prev = prevStatusesRef.current[c.conn_id];
        if (prev && prev !== "active" && c.status === "active") {
          setToast({ cloud: c.cloud_type });
        }
        prevStatusesRef.current[c.conn_id] = c.status;
      }
      setConns(connections);
    });
  }
```

(Add `useRef` to the existing `react` import.)

Render the toast at the top of the returned JSX:

```tsx
{toast && (
  <div className="fixed top-4 right-4 z-50 max-w-sm bg-white border border-orange-300 shadow-lg rounded-md p-4">
    <div className="font-medium text-stone-800">
      Your {toast.cloud.toUpperCase()} connection is ready
    </div>
    <button onClick={() => { setToast(null); nav("/scan"); }}
      className="mt-2 text-orange-700 underline text-sm">
      Run your first scan →
    </button>
    <button onClick={() => setToast(null)}
      className="absolute top-1 right-2 text-stone-400 hover:text-stone-600">×</button>
  </div>
)}
```

- [ ] **Step 3: Typecheck and build**

Run: `cd web && pnpm typecheck && pnpm build`
Expected: 0 new type errors. Build succeeds. The deletion of the two picker components may produce "unused import" or "declared but never read" warnings — clean those up.

- [ ] **Step 4: Commit**

```bash
git add web/src/routes/ConnectClouds.tsx
git commit -m "refactor: connect page retires the inline pickers; adds /scan toast"
```

---

### Task 6: Confirm `updateConnectionSubscriptions` accepts GCP projects too

**File:** `platform/lambda/connections_list/main.py`

The current `_update_scope` (PATCH /connections/{id}) validates `selected` against `scope.subscriptions`. For GCP org connections, `scope.projects` is the validation set instead. This task generalises it.

- [ ] **Step 1: Inspect `_update_scope`**

Open `platform/lambda/connections_list/main.py`. Find `_update_scope` (around line 341). It currently does:

```python
    scope = conn.get("scope") or {}
    discovered = set(scope.get("subscriptions") or [])
    unknown = [s for s in selected if s not in discovered]
```

If it only checks `subscriptions`, replace those two lines with:

```python
    scope = conn.get("scope") or {}
    # The picker may be choosing subscriptions (Azure) or projects (GCP
    # org mode). Validate against whichever the connection has.
    discovered_subs = set(scope.get("subscriptions") or [])
    discovered_proj = set(((scope.get("projects") or {}) ).keys()) \
                      if isinstance(scope.get("projects"), dict) else set()
    discovered = discovered_subs or discovered_proj
    unknown = [s for s in selected if s not in discovered]
```

- [ ] **Step 2: Verify**

Run: `cd platform/lambda/connections_list && python3 -c "import ast; ast.parse(open('main.py').read()); print('parses OK')"`
Expected: `parses OK`.

- [ ] **Step 3: Commit**

```bash
git add platform/lambda/connections_list/main.py
git commit -m "feat: PATCH /connections/{id} validates selected against subs or projects"
```

---

### Task 7: Deploy + browser-smoke

- [ ] **Step 1: Deploy backend Lambda code changes**

Run: `cd platform && npx cdk deploy CisoCopilotApi --exclusively --require-approval never`
Expected: `UPDATE_COMPLETE`.

- [ ] **Step 2: Build + deploy web**

Run:

```bash
cd web && pnpm build && \
  aws s3 sync dist/ s3://<WEB_BUCKET>/ --delete && \
  aws cloudfront create-invalidation --distribution-id <CLOUDFRONT_DIST_ID> --paths '/*'
```

Expected: build succeeds; S3 sync uploads; CloudFront invalidation queued.

- [ ] **Step 3: Document the browser-smoke checklist + commit**

This is the human-gated final verification — an agent cannot pass Google OAuth. Append a checklist to `HANDOFF.md` (the next task documents the full Slice 2b shipped block; the smoke checklist lives inside that). Verification steps:

1. Open `https://$SHASTA_DOMAIN` in an incognito window.
2. Sign in with Google.
3. Click "Scan" in the nav. Confirm the page renders the existing GCP project connection as a card.
4. Confirm the AWS connection card shows only a tier picker; the Azure card shows the subscription checklist; the GCP card shows the tier picker (single-project mode).
5. Click "Scan" on the GCP card. Confirm the card flips to ScanProgress and polls until terminal.
6. Click "Connect clouds" — confirm the per-row ScanPicker / SubscriptionPicker are gone; only Delete buttons remain.

If any step fails, fix in a follow-up commit on the branch.

---

### Task 8: Update HANDOFF + commit

- [ ] **Step 1: Add the Slice 2b section to HANDOFF.md**

In `HANDOFF.md`, add a new section above the Slice 2a block:

```markdown
## 🚀 Scan Screen — Slice 2b shipped (2026-05-22)

Cross-cloud `/scan` surface. Spec
`docs/superpowers/specs/2026-05-22-scan-screen-design.md`; plan
`docs/superpowers/plans/2026-05-22-scan-screen-slice-2b.md`. Built
subagent-driven on branch **`feat/scan-screen-slice-2b`**.

- New `/scan` route — stacked cards, one per active connection. AWS
  card: tier picker. Azure: subscription checklist + tier. GCP: tier
  (project mode) or project checklist + tier (org mode). Entra: just
  a Scan button. "Launch all scans" fires every card in parallel.
- All four onboarding webhooks dropped the silent auto-scan. A freshly
  onboarded connection lands at `/scan` with `latest_scan: null` and a
  "Never scanned" badge.
- Connect page retired its per-row ScanPicker + the Azure subscription
  checklist; only Delete remains. The Connect page polls for connection
  status; a `pending → active` transition surfaces a toast linking to
  `/scan`. The Entra HTML success page redirects to `/scan` directly.
- Deployed: `CisoCopilotApi` deploy (`UPDATE_COMPLETE`); web built and
  synced to S3 + CloudFront invalidated.
- **Browser-smoke pending** — an agent can't pass Google OAuth. Visual
  verification steps documented in the plan's Task 7.
```

- [ ] **Step 2: Commit**

```bash
git add HANDOFF.md
git commit -m "docs: record Scan-screen Slice 2b shipped"
```

---

## Self-review

**Spec coverage** (against `2026-05-22-scan-screen-design.md`):
- §3 in-scope items — Task 2 (route + nav + shell + empty state) covers the route/nav/empty state; Tasks 3-4 cover per-cloud cards; Task 5 covers the LaunchAll + ScanProgress + new-connection badge; Task 1 covers the onboarding-webhook auto-scan removals + Entra redirect + Connect-page retrofit.
- §6 toast on pending→active — Task 5 (Connect page).
- §6.3 new-connection highlight — Task 3 (the `isNew` badge in `ScanCard`).
- §7 Connect-page migration — Task 5.

**Placeholder scan:** the comments referencing future-work files are intentional explanatory hooks, not unfilled requirements. No "TBD"/"implement later" anywhere.

**Type / contract consistency:** the `ScanCard` props (`conn`, `onChanged`) match the page's invocation; the per-cloud body props (`conn`, `onScanStarted`, optional `onChanged`) match the `ScanCard` router. `api.rescanConnection(conn_id, tier)` and `api.updateConnectionSubscriptions(conn_id, selected)` match the existing API helpers in `web/src/lib/api.ts`. The `Connection.scope.mode` / `scope.projects` / `scope.subscription_names` field names match what the backend writes (Slice 1b + 2a).

**Scope cuts from spec:**
- Toast component is rolled inline rather than introducing a toast library (spec open item §10.1).
- "Launch all scans" defaults to Quick always, not per-card (spec open item §10.2).
- New-connection highlight clears once `latest_scan` is non-null (spec open item §10.3 — implicit via `isNew = conn.latest_scan === null`).

All three open items are resolved by the plan and consistent with the design intent. No issues found.
