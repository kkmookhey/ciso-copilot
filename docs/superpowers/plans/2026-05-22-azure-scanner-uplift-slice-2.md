# Azure Scanner Uplift — Slice 2: Web Subscription Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user choose which Azure subscriptions get scanned — a checklist on the Azure connection in the web app, persisted to the connection's `scope.selected`, which the Azure rescan then honours.

**Architecture:** `cloud_connections.scope` for an Azure connection becomes `{"subscriptions": [...all discovered...], "selected": [...chosen...]}`. `GET /connections` starts returning `scope`; a new `PATCH /connections/{id}` updates `scope.selected`. `_rescan_azure` scans `selected` (falling back to `subscriptions`). The web Connect page renders a per-Azure-connection checklist. `ScanProgress` gains a subscription-keyed render branch for Azure scans.

**Tech Stack:** Python 3.12 (Lambda), boto3 / Aurora Data API, AWS CDK (TypeScript), React + TypeScript + Vite + Tailwind.

**Spec:** `docs/superpowers/specs/2026-05-21-azure-scanner-uplift-design.md` (§8.3, §9).

---

## Background — current state (verified by exploration)

- `cloud_connections.scope` for Azure = `{"subscriptions": ["<id>", ...]}` (no `selected`).
- `connections_list/main.py` `_list_connections` (lines 68-117) does **not** SELECT or return `scope`. `_get_connection_full` (used by `_rescan`/`_delete`) **does** fetch `scope::text`.
- `handler` routing (lines 51-61): `GET .../connections` → `_list_connections`; `POST .../connections/{id}/rescan` → `_rescan`; `DELETE .../connections/{id}` → `_delete`. No `PATCH`.
- `_rescan_azure` (lines ~248-249): `subscriptions = scope.get("subscriptions") or []`.
- `onboarding_azure_complete/main.py`: writes `scope = {"subscriptions": subscription_ids}`; `_run_initial_scan` scans the raw onboarding list (not from scope) — no change needed there for the *initial* scan.
- Web `Connection` type (`web/src/lib/api.ts`) has no `scope` field. `ScanCoverageMap` (api.ts) has `tier?` + `regions?`, no `subscriptions?`.
- `ConnectClouds.tsx` `ConnectionRow` renders a flat `<li>`. Active **AWS** rows get the `ScanPicker` (Quick/Medium/Deep dropdown; "deep" routes to `/contact/deep-scan`); active **non-AWS** rows (incl. Azure) get a flat "Rescan" button hardcoded to `startScan("medium")`. No expand affordance. The `seedId` scan-poll guard is AWS-only. The Azure v2 scanner *is* tier-aware (Slice 1a/1b) — so Azure should get the existing `ScanPicker`; only Entra/GCP (still single-pass legacy Lambdas) keep the flat Rescan button.
- `ScanProgress.tsx` renders `scan.coverage_map?.regions` only.
- `api-stack.ts` lines 463-469: the `connections` / `{id}` / `rescan` routes; `connectionByIdRes` is a variable; `connectionsListFn` already has the IAM it needs (a PATCH only writes Aurora).

---

## Conventions

- Work on a branch: before Task 1, `git checkout -b feat/azure-subscription-picker` from `main`. Commit after each task. Never `--no-verify`.
- `connections_list` / `onboarding_azure_complete` are Lambda Python with no local test venv — verify with `python3 -m py_compile` + the live E2E (Task 8).
- Web: verify with `cd web && pnpm build` (runs `tsc -b` then `vite build`). `pnpm lint` has a known-dirty baseline (~42 pre-existing errors) — do **not** treat pre-existing lint errors as regressions; only fix lint errors in files this slice creates/changes.
- The web UI cannot be browser-tested by an agent (Google OAuth blocks automated sign-in) — Task 8 notes which checks are code-level vs. need a human.

---

## Task 1: `GET /connections` returns `scope`

**Files:** Modify `platform/lambda/connections_list/main.py`

- [ ] **Step 1: Add `scope` to the SELECT**

In `_list_connections`, the SELECT's first line currently reads:

```python
            "SELECT c.conn_id::text, c.cloud_type, c.display_name, c.status, "
            "       c.account_identifier, c.signals::text, "
            "       c.last_scan_at::text, c.created_at::text, "
```

Change it to add `c.scope::text` (a 9th `c.` column — the latest-scan columns `s.*` then shift to indices 9-13):

```python
            "SELECT c.conn_id::text, c.cloud_type, c.display_name, c.status, "
            "       c.account_identifier, c.signals::text, "
            "       c.last_scan_at::text, c.created_at::text, c.scope::text, "
```

- [ ] **Step 2: Shift the latest-scan record indices and add `scope` to the dict**

The `scope` column is now `r[8]`; the LATERAL `s.*` columns shift from `r[8..12]` to `r[9..13]`. Replace the result-building loop:

```python
    connections = []
    for r in rs.get("records", []):
        latest_scan = None
        if not r[8].get("isNull"):
            latest_scan = {
                "scan_id":    r[8].get("stringValue"),
                "tier":       r[9].get("stringValue"),
                "status":     r[10].get("stringValue"),
                "phase":      r[11].get("stringValue"),
                "started_at": r[12].get("stringValue") if not r[12].get("isNull") else None,
            }
        connections.append({
            "conn_id":            r[0].get("stringValue"),
            "cloud_type":         r[1].get("stringValue"),
            "display_name":       r[2].get("stringValue"),
            "status":             r[3].get("stringValue"),
            "account_identifier": r[4].get("stringValue") if not r[4].get("isNull") else None,
            "signals":            json.loads(r[5].get("stringValue") or "{}"),
            "last_scan_at":       r[6].get("stringValue") if not r[6].get("isNull") else None,
            "created_at":         r[7].get("stringValue"),
            "latest_scan":        latest_scan,
        })
```

with:

```python
    connections = []
    for r in rs.get("records", []):
        latest_scan = None
        if not r[9].get("isNull"):
            latest_scan = {
                "scan_id":    r[9].get("stringValue"),
                "tier":       r[10].get("stringValue"),
                "status":     r[11].get("stringValue"),
                "phase":      r[12].get("stringValue"),
                "started_at": r[13].get("stringValue") if not r[13].get("isNull") else None,
            }
        connections.append({
            "conn_id":            r[0].get("stringValue"),
            "cloud_type":         r[1].get("stringValue"),
            "display_name":       r[2].get("stringValue"),
            "status":             r[3].get("stringValue"),
            "account_identifier": r[4].get("stringValue") if not r[4].get("isNull") else None,
            "signals":            json.loads(r[5].get("stringValue") or "{}"),
            "last_scan_at":       r[6].get("stringValue") if not r[6].get("isNull") else None,
            "created_at":         r[7].get("stringValue"),
            "scope":              json.loads(r[8].get("stringValue") or "{}"),
            "latest_scan":        latest_scan,
        })
```

- [ ] **Step 2b: Confirm the LATERAL subquery column count is unchanged**

The `s.*` SELECT list inside the LATERAL (`SELECT scan_id, tier, status, phase, started_at`) is unchanged — only the outer column list grew. Re-read the full SELECT in `_list_connections` and confirm the outer list now has 9 `c.` columns followed by 5 `s.` columns (14 total, indices 0-13).

- [ ] **Step 3: Syntax-check**

Run: `cd platform/lambda/connections_list && python3 -m py_compile main.py && echo "py_compile OK"`. Expected: `py_compile OK`.

- [ ] **Step 4: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/lambda/connections_list/main.py
git commit -m "$(cat <<'EOF'
feat: GET /connections returns the connection scope

The web subscription picker needs each Azure connection's scope
(subscriptions + selected). Adds c.scope to the SELECT and the response.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `PATCH /connections/{id}` — update `scope.selected`

**Files:** Modify `platform/lambda/connections_list/main.py`

- [ ] **Step 1: Add the routing branch**

In `handler`, after the `DELETE` branch and before the final `return _resp(404, ...)`, the routing currently is:

```python
    if method == "POST" and "/connections/" in path and path.rstrip("/").endswith("/rescan"):
        return _rescan(event)
    if method == "DELETE" and "/connections/" in path and not path.rstrip("/").endswith("/rescan"):
        return _delete(event)
```

Add a `PATCH` branch immediately after the `DELETE` branch:

```python
    if method == "POST" and "/connections/" in path and path.rstrip("/").endswith("/rescan"):
        return _rescan(event)
    if method == "DELETE" and "/connections/" in path and not path.rstrip("/").endswith("/rescan"):
        return _delete(event)
    if method == "PATCH" and "/connections/" in path:
        return _update_scope(event)
```

- [ ] **Step 2: Add the `_update_scope` function**

Add this function (place it just after the `_rescan` function block, before the `_delete` function):

```python
# ============================================================================
# PATCH /connections/{id} — update the selected subscriptions
# ============================================================================

def _update_scope(event: dict) -> dict:
    """Update which subscriptions an Azure connection scans.
    Body: {"selected": ["<sub-id>", ...]}. Every id must be one of the
    connection's discovered `scope.subscriptions`, and at least one must
    be selected (a connection with zero selected subs cannot scan)."""
    tenant_id = _resolve_tenant_id(event)
    if not tenant_id:
        return _resp(401, {"error": "no_tenant"})

    conn_id = _extract_conn_id(event)
    if not conn_id:
        return _resp(400, {"error": "missing_conn_id"})

    conn = _get_connection_full(conn_id, tenant_id)
    if not conn:
        return _resp(404, {"error": "connection_not_found"})
    if conn["cloud_type"] != "azure":
        return _resp(422, {"error": "not_an_azure_connection"})

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})
    selected = body.get("selected")
    if not isinstance(selected, list) or not selected:
        return _resp(422, {"error": "selected_must_be_nonempty_list"})

    scope = conn.get("scope") or {}
    discovered = set(scope.get("subscriptions") or [])
    unknown = [s for s in selected if s not in discovered]
    if unknown:
        return _resp(422, {"error": "unknown_subscriptions", "subscriptions": unknown})

    new_scope = {**scope, "selected": list(selected)}
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "UPDATE cloud_connections "
            "SET scope = CAST(:scope AS JSONB), updated_at = now() "
            "WHERE conn_id = CAST(:cid AS UUID)"
        ),
        parameters=[
            {"name": "cid",   "value": {"stringValue": conn_id}},
            {"name": "scope", "value": {"stringValue": json.dumps(new_scope)}},
        ],
    )
    return _resp(200, {"status": "updated", "selected": list(selected)})
```

- [ ] **Step 3: Syntax-check**

Run: `cd platform/lambda/connections_list && python3 -m py_compile main.py && echo "py_compile OK"`. Expected: `py_compile OK`.

- [ ] **Step 4: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/lambda/connections_list/main.py
git commit -m "$(cat <<'EOF'
feat: PATCH /connections/{id} updates the selected subscriptions

Validates the selected list is a non-empty subset of the connection's
discovered subscriptions, then writes scope.selected.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Scanner triggers honour `selected`

**Files:** Modify `platform/lambda/connections_list/main.py`, `platform/lambda/onboarding_azure_complete/main.py`

- [ ] **Step 1: `_rescan_azure` prefers `selected`**

In `connections_list/main.py` `_rescan_azure`, the lines currently read:

```python
    scope = conn.get("scope") or {}
    subscriptions = scope.get("subscriptions") or []
    if not subscriptions:
        raise _IncompleteConnection("missing subscriptions in scope")
```

Replace with:

```python
    scope = conn.get("scope") or {}
    # Scan the user-selected subset; fall back to all discovered
    # subscriptions if the connection predates the picker.
    subscriptions = scope.get("selected") or scope.get("subscriptions") or []
    if not subscriptions:
        raise _IncompleteConnection("missing subscriptions in scope")
```

- [ ] **Step 2: `onboarding_azure_complete` seeds `selected` = all**

In `onboarding_azure_complete/main.py` `handler`, the `cloud_connections` UPDATE writes `scope`. The parameter currently is:

```python
            {"name": "scope", "value": {"stringValue": json.dumps({"subscriptions": subscription_ids})}},
```

Change it so `selected` defaults to all discovered subscriptions on first connect (spec §8.3):

```python
            {"name": "scope", "value": {"stringValue": json.dumps({"subscriptions": subscription_ids, "selected": subscription_ids})}},
```

- [ ] **Step 3: Syntax-check both**

Run:
```bash
cd platform/lambda/connections_list && python3 -m py_compile main.py && \
cd ../onboarding_azure_complete && python3 -m py_compile main.py && echo "py_compile OK"
```
Expected: `py_compile OK`.

- [ ] **Step 4: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/lambda/connections_list/main.py platform/lambda/onboarding_azure_complete/main.py
git commit -m "$(cat <<'EOF'
feat: Azure rescan scans the selected subscriptions

_rescan_azure prefers scope.selected (falls back to subscriptions for
pre-picker connections); onboarding seeds selected = all on connect.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: CDK — add the `PATCH /connections/{id}` route

**Files:** Modify `platform/lib/api-stack.ts`

- [ ] **Step 1: Add the PATCH method**

In `api-stack.ts`, the connections routes currently read:

```typescript
    connectionsRes.addMethod('GET', new apigw.LambdaIntegration(connectionsListFn), authedOpts);
    connectionByIdRes.addMethod('DELETE', new apigw.LambdaIntegration(connectionsListFn), authedOpts);
    connectionByIdRes.addResource('rescan').addMethod(
      'POST', new apigw.LambdaIntegration(connectionsListFn), authedOpts,
    );
```

Add a `PATCH` method on `connectionByIdRes` (right after the `DELETE` line):

```typescript
    connectionsRes.addMethod('GET', new apigw.LambdaIntegration(connectionsListFn), authedOpts);
    connectionByIdRes.addMethod('DELETE', new apigw.LambdaIntegration(connectionsListFn), authedOpts);
    connectionByIdRes.addMethod('PATCH', new apigw.LambdaIntegration(connectionsListFn), authedOpts);
    connectionByIdRes.addResource('rescan').addMethod(
      'POST', new apigw.LambdaIntegration(connectionsListFn), authedOpts,
    );
```

(No new IAM is needed — the PATCH handler only writes Aurora, and `connectionsListFn` already has `grantDataApiAccess`. The RestApi's `defaultCorsPreflightOptions` adds the OPTIONS preflight automatically.)

- [ ] **Step 2: Synth-check**

Run: `cd /Users/kkmookhey/Projects/CISOBrief/platform && npx cdk synth CisoCopilotApi >/dev/null && echo "synth OK"`. Expected: `synth OK`.

- [ ] **Step 3: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/lib/api-stack.ts
git commit -m "$(cat <<'EOF'
feat: add PATCH /connections/{id} route

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Web — `Connection` type + API client

**Files:** Modify `web/src/lib/api.ts`

- [ ] **Step 1: Add `scope` to the `Connection` type**

In `web/src/lib/api.ts`, the `Connection` interface (around lines 42-52) has fields `conn_id` … `latest_scan`. Add an optional `scope` field:

```typescript
  scope?: { subscriptions?: string[]; selected?: string[] };
```

Place it just before `latest_scan` in the interface.

- [ ] **Step 2: Add `subscriptions` to `ScanCoverageMap`**

The `ScanCoverageMap` interface (around lines 20-23) currently is:

```typescript
interface ScanCoverageMap {
  tier?:    string;
  regions?: Record<string, { state: string; errors?: string[] }>;
}
```

Add a `subscriptions` shape (the Azure scanner writes a subscription-keyed map):

```typescript
interface ScanCoverageMap {
  tier?:         string;
  regions?:      Record<string, { state: string; errors?: string[] }>;
  subscriptions?: Record<string, { state: string; errors?: string[] }>;
}
```

- [ ] **Step 3: Add the `updateConnectionSubscriptions` API function**

Near the existing connection functions (`listConnections`, `rescanConnection`, `deleteConnection`), add:

```typescript
  updateConnectionSubscriptions(connId: string, selected: string[]):
      Promise<{ status: string; selected: string[] }> {
    return apiFetch(`/connections/${connId}`, {
      method: "PATCH",
      body:   JSON.stringify({ selected }),
    });
  },
```

Match the exact style of `rescanConnection` (same `apiFetch` helper, same method/body shape). If `rescanConnection` uses a differently-named helper or pattern, mirror that — read the surrounding functions first and match them exactly.

- [ ] **Step 4: Typecheck**

Run: `cd web && pnpm build 2>&1 | tail -6`. Expected: build succeeds (`tsc -b` clean, `vite build` ok). A `tsc` error in `api.ts` means a type mistake — fix it.

- [ ] **Step 5: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add web/src/lib/api.ts
git commit -m "$(cat <<'EOF'
feat: web api — connection scope + updateConnectionSubscriptions

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Web — subscription checklist + scan-depth picker on Azure rows

Two changes to the Azure `ConnectionRow`: (1) an expandable subscription checklist — a "Subscriptions (N of M)" toggle revealing a checkbox per `scope.subscriptions` entry (checked = in `scope.selected`), Save calls `updateConnectionSubscriptions`; (2) give active Azure rows the existing `ScanPicker` (Quick/Medium/Deep), replacing their flat Medium-only "Rescan" button.

**Files:** Modify `web/src/routes/ConnectClouds.tsx`

- [ ] **Step 1: Add the `SubscriptionPicker` component**

In `ConnectClouds.tsx`, add a new component (place it just before the `ConnectionRow` function). It is self-contained — props are the connection and a reload callback:

```tsx
function SubscriptionPicker({ conn, onSaved }: {
  conn: Connection;
  onSaved: () => void;
}) {
  const all = conn.scope?.subscriptions ?? [];
  // selected defaults to all when scope.selected is absent (pre-picker connections)
  const initial = conn.scope?.selected ?? all;
  const [open, setOpen]       = useState(false);
  const [checked, setChecked] = useState<Set<string>>(new Set(initial));
  const [busy, setBusy]       = useState(false);
  const [err, setErr]         = useState<string | null>(null);

  if (all.length === 0) return null;

  function toggle(sub: string) {
    setChecked((prev) => {
      const next = new Set(prev);
      next.has(sub) ? next.delete(sub) : next.add(sub);
      return next;
    });
  }

  async function save() {
    setBusy(true);
    setErr(null);
    try {
      await api.updateConnectionSubscriptions(conn.conn_id, [...checked]);
      onSaved();
      setOpen(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="text-xs text-slate-600 hover:text-slate-900"
      >
        {open ? "▾" : "▸"} Subscriptions ({checked.size} of {all.length} scanned)
      </button>
      {open && (
        <div className="mt-2 rounded-lg border border-slate-200 p-3">
          <ul className="space-y-1">
            {all.map((sub) => (
              <li key={sub}>
                <label className="flex items-center gap-2 text-xs text-slate-700">
                  <input
                    type="checkbox"
                    checked={checked.has(sub)}
                    onChange={() => toggle(sub)}
                  />
                  <span className="font-mono">{sub}</span>
                </label>
              </li>
            ))}
          </ul>
          {err && <div className="mt-2 text-xs text-red-600">{err}</div>}
          <div className="mt-2 flex items-center gap-2">
            <button
              type="button"
              onClick={save}
              disabled={busy || checked.size === 0}
              className="px-3 py-1 rounded-md bg-blue-600 hover:bg-blue-700 disabled:bg-slate-300 text-white text-xs"
            >
              {busy ? "Saving…" : "Save"}
            </button>
            {checked.size === 0 && (
              <span className="text-xs text-slate-400">Select at least one.</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Render `SubscriptionPicker` in `ConnectionRow`**

In the `ConnectionRow` function, the `<li>` renders the row header, the action buttons, and `{scan && <ScanProgress scan={scan} />}`. Add the picker for active Azure connections. Find the line:

```tsx
      {scan && <ScanProgress scan={scan} />}
```

and change it to:

```tsx
      {conn.status === "active" && conn.cloud_type === "azure" && (
        <SubscriptionPicker conn={conn} onSaved={onConnSaved} />
      )}
      {scan && <ScanProgress scan={scan} />}
```

- [ ] **Step 3: Thread the `onConnSaved` callback into `ConnectionRow`**

`ConnectionRow`'s props are `{ conn, actionMsg, onDelete }`. Add `onConnSaved: () => void`:
- In the `ConnectionRow` props type, add `onConnSaved: () => void;`.
- Where `ConnectionRow` is rendered (the `cloudConnections.filter(...).map(...)`), pass `onConnSaved={loadConnections}` — use whatever the existing function is that reloads the connection list (the same function the page calls on mount / after `connectAws` etc.; read the component to find its exact name — it is the function that sets `cloudConnections`). If that function takes no args and is in scope, pass it directly.

- [ ] **Step 4: Give Azure connections the scan-depth `ScanPicker`**

`ConnectionRow` currently renders the `ScanPicker` (Quick/Medium/Deep dropdown) only for active **AWS** connections; active non-AWS connections get a flat "Rescan" button hardcoded to `startScan("medium")`. The Azure v2 scanner is tier-aware, so Azure should get the same `ScanPicker`; only Entra/GCP keep the flat button.

First add an `isAzure` const next to the existing `isAws` const in `ConnectionRow`:

```tsx
  const isAzure = conn.cloud_type === "azure";
```

Then find the two `conn.status === "active"` render blocks. The first renders `<ScanPicker .../>` gated on `isAws`; the second renders the flat `<button>…Rescan</button>` gated on `!isAws`. Change the gates:
- The `ScanPicker` block: gate on `isAws || isAzure` instead of `isAws`.
- The flat "Rescan" button block: gate on `!isAws && !isAzure` instead of `!isAws` (so only Entra/GCP keep it).

Leave the `ScanPicker`'s `onPick` handler exactly as-is — `tier === "deep" ? navigate("/contact/deep-scan") : startScan(tier)` works identically for Azure (the rescan API gates `deep` to Contact-Us for every cloud, and `startScan` already calls `api.rescanConnection(conn.conn_id, tier)` which Slice 1b made tier-aware for Azure).

- [ ] **Step 5: Typecheck + build**

Run: `cd web && pnpm build 2>&1 | tail -6`. Expected: build succeeds. Fix any `tsc` error in `ConnectClouds.tsx`.

- [ ] **Step 6: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add web/src/routes/ConnectClouds.tsx
git commit -m "$(cat <<'EOF'
feat: subscription + scan-depth pickers on Azure connection rows

An expandable subscription checklist (Save persists via PATCH
/connections/{id}), and the Quick/Medium/Deep ScanPicker — the same one
AWS rows have — now that the Azure v2 scanner is tier-aware.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Web — `ScanProgress` Azure adapter + Azure scan polling

**Files:** Modify `web/src/scan/ScanProgress.tsx`, `web/src/routes/ConnectClouds.tsx`

- [ ] **Step 1: Render the subscription census for Azure scans**

In `ScanProgress.tsx`, the component currently derives `regions` from `scan.coverage_map?.regions`. Generalise it to also handle the Azure subscription-keyed map. Replace:

```tsx
  const regions = scan.coverage_map?.regions
    ? Object.values(scan.coverage_map.regions)
    : null;
  const activeCount = regions
    ? regions.filter((r) => r.state === "active").length
    : null;
```

with:

```tsx
  // AWS scans carry a region-keyed coverage map; Azure scans a
  // subscription-keyed one. Render whichever is present.
  const census = scan.coverage_map?.regions
    ?? scan.coverage_map?.subscriptions
    ?? null;
  const censusUnit = scan.coverage_map?.subscriptions ? "subscriptions" : "regions";
  const cells = census ? Object.values(census) : null;
  const activeCount = cells
    ? cells.filter((c) => c.state === "active").length
    : null;
```

Then replace the render block:

```tsx
      {regions && (
        <div className="mt-1 text-xs text-blue-600">
          {regions.length} regions scanned
          {activeCount != null ? ` · ${activeCount} active` : ""}
        </div>
      )}
```

with:

```tsx
      {cells && (
        <div className="mt-1 text-xs text-blue-600">
          {cells.length} {censusUnit} scanned
          {activeCount != null ? ` · ${activeCount} active` : ""}
        </div>
      )}
```

- [ ] **Step 2: Let Azure connections seed scan-progress polling**

In `ConnectClouds.tsx` `ConnectionRow`, the `seedId` constant only seeds a live poll for AWS. It currently reads:

```tsx
  const seedId =
    conn.cloud_type === "aws" && conn.latest_scan &&
    !["completed", "partial", "failed"].includes(conn.latest_scan.status)
      ? conn.latest_scan.scan_id
      : null;
```

Change the cloud check to include Azure:

```tsx
  const seedId =
    ["aws", "azure"].includes(conn.cloud_type) && conn.latest_scan &&
    !["completed", "partial", "failed"].includes(conn.latest_scan.status)
      ? conn.latest_scan.scan_id
      : null;
```

- [ ] **Step 3: Typecheck + build**

Run: `cd web && pnpm build 2>&1 | tail -6`. Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add web/src/scan/ScanProgress.tsx web/src/routes/ConnectClouds.tsx
git commit -m "$(cat <<'EOF'
feat: ScanProgress renders the Azure subscription census

ScanProgress shows "N subscriptions scanned" for a subscription-keyed
coverage map; Azure connection rows now seed live scan polling.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Deploy + verify

**Files:** none (deploy + verification).

- [ ] **Step 1: Deploy the API**

Run:
```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform && npx cdk deploy CisoCopilotApi --require-approval never
```
Expected: completes successfully (new `PATCH` method + updated `connections_list` Lambda code). No cross-stack export changes, so no two-phase needed.

- [ ] **Step 2: Deploy the web app**

Run:
```bash
cd /Users/kkmookhey/Projects/CISOBrief/web && pnpm build && \
  aws s3 sync dist/ s3://<WEB_BUCKET>/ --delete && \
  aws cloudfront create-invalidation --distribution-id <CLOUDFRONT_DIST_ID> --paths '/*'
```
Expected: build succeeds, sync uploads, invalidation created.

- [ ] **Step 3: Backend E2E — PATCH then rescan honours `selected`**

The Azure connection is `conn_id 79964b99-6501-413d-8f22-0431e870184d`, tenant `99d08352-53dd-4b59-beed-92cc755cb802`, with two subscriptions `cb0d6ed4-a7c9-4929-8707-4a477a2cc9b5` and `8cd2b4cc-c789-466d-a8f7-8f51fb20985d`. Direct-invoke the `connections_list` Lambda with a synthetic `PATCH` event selecting only the first subscription (get the function name with `aws lambda list-functions --query "Functions[?contains(FunctionName,'ConnectionsList')].FunctionName" --output text`; the `sso_subject` for the synthetic Cognito claim is `110278684770426120220`).

Write `/tmp/patch_event.json`:
```json
{
  "httpMethod": "PATCH",
  "path": "/connections/79964b99-6501-413d-8f22-0431e870184d",
  "pathParameters": {"id": "79964b99-6501-413d-8f22-0431e870184d"},
  "body": "{\"selected\": [\"cb0d6ed4-a7c9-4929-8707-4a477a2cc9b5\"]}",
  "requestContext": {"authorizer": {"claims": {"sub": "110278684770426120220"}}}
}
```
Invoke it; expected response `200` with `{"status": "updated", "selected": ["cb0d6ed4-..."]}`.

Then confirm the DB:
```bash
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN \
  --secret-arn $DB_SECRET_ARN \
  --database ciso_copilot \
  --sql "SELECT scope FROM cloud_connections WHERE conn_id = CAST('79964b99-6501-413d-8f22-0431e870184d' AS UUID)"
```
Expected: `scope` is `{"subscriptions": [...both...], "selected": ["cb0d6ed4-..."]}`.

Then trigger a rescan (synthetic `POST .../rescan` event, body `{"tier":"quick"}` — same technique as Slice 1b's verification) and confirm in the Fargate task / the `scans.scope` map that **only `cb0d6ed4-…` was scanned** (the other subscription should not appear in the coverage map's `subscriptions`). Restore `selected` to both subscriptions afterward with another PATCH so the connection is left scanning everything.

- [ ] **Step 4: Web verification (note the limit)**

`pnpm build` (Step 2) is the code-level gate — `tsc` proves the types line up. The pickers' **visual + interaction behaviour cannot be agent-verified** (Google OAuth blocks automated sign-in). Flag for the user: on `https://$SHASTA_DOMAIN/` → Connect → the Azure connection, confirm the "Subscriptions (N of M)" toggle expands, checkboxes reflect `selected`, Save persists; the Quick/Medium/Deep `ScanPicker` shows on the Azure row (not the flat Rescan button); and a subsequent scan's progress card shows "N subscriptions scanned".

- [ ] **Step 5: Update HANDOFF.md and commit**

Add an entry under the Azure Scanner Uplift section of `HANDOFF.md`: Slice 2 complete — `GET /connections` returns `scope`; `PATCH /connections/{id}` updates `scope.selected`; `_rescan_azure` honours `selected`; the web Connect page has a per-Azure-connection subscription checklist; `ScanProgress` renders the subscription census. Note that the **Azure scanner uplift is now fully complete** (Slices 0, 1a, 1b, 2 + the legacy-Lambda retirement). Commit:

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add HANDOFF.md
git commit -m "$(cat <<'EOF'
docs: record Azure-uplift Slice 2 (web subscription picker)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Done criteria

- [ ] `GET /connections` returns each connection's `scope`.
- [ ] `PATCH /connections/{id}` validates and persists `scope.selected`; rejects empty / unknown-subscription / non-Azure requests.
- [ ] `_rescan_azure` scans `scope.selected` (falling back to `subscriptions`); onboarding seeds `selected` = all.
- [ ] The web Connect page shows an expandable subscription checklist on active Azure connections (Save persists via PATCH), and Azure rows have the Quick/Medium/Deep `ScanPicker` instead of the flat Rescan button.
- [ ] `ScanProgress` shows "N subscriptions scanned" for an Azure scan; Azure rows seed live scan polling.
- [ ] `cdk synth` green; `pnpm build` green; API + web deployed.
- [ ] Backend E2E proved a rescan honours `selected`; the visual picker check is handed to the user.
- [ ] No change to AWS / Entra / GCP connection behaviour.
