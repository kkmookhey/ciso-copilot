# Scan Progress & Scan-Type UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a running scan visible in the web app (progress view + chunked Phase-1/Phase-2 results), label every findings/dashboard view by the scan behind it, and give an already-connected cloud a Quick/Medium/Deep scan picker with Deep routed to a Contact-Us page.

**Architecture:** A full vertical slice. Two small backend changes — (1) `GET /connections` carries each connection's latest scan so the app can discover an in-flight scan on any page load; (2) AWS rescan becomes tier-aware and routes to the v2 Fargate scanner instead of the legacy Lambda — plus a shared `web/src/scan/` module (polling hook, scan-type badge, progress view, label helpers) wired into `ConnectClouds`, the findings/`Risks`/`Dashboard` headers, and a new Contact-Us route.

**Tech Stack:** Python 3 Lambda + Aurora RDS Data API; AWS CDK (TypeScript); React 19 + TypeScript + Tailwind + Vite; Vitest + React Testing Library.

**Source spec:** `docs/superpowers/specs/2026-05-21-scan-performance-design.md` §10. The scan-status API (`GET /v1/scans/{scan_id}`), the `scans.phase` / `scans.tier` columns, and the v2 Fargate scanner are already shipped (PR #4, merged).

---

## Notes for the implementer

- **Repo root:** `/Users/kkmookhey/Projects/CISOBrief`. Web app in `web/`, platform in `platform/`. Web package manager is **pnpm**.
- **API lambdas are not unit-tested in this repo** (no test harness for them) — verify backend Python changes structurally (`python3 -c "import ast; ast.parse(...)"`) and by validating SQL against the live DB with a read-only `aws rds-data` call, then by a post-deploy smoke check. This mirrors how the scanner plan handled non-importable code.
- **Aurora Data API ARNs** (for SQL validation):
  - cluster: `arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh`
  - secret: `arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp`
  - database: `ciso_copilot`
- **`scans` table columns** (confirmed live): `scan_id, tenant_id, conn_id, trigger, status, scope, step_fn_arn, started_at, finished_at, error, stats, tier, phase`. There is **no `created_at`** — `started_at` defaults to `now()` at insert, so order "latest scan" by `started_at DESC`.
- **`tier`** ∈ `quick|medium|deep` (default `quick`). **`phase`** ∈ `region_discovery|first_signal|crown_jewel|full|done` (default `done` — which is wrong for a fresh queued scan; Task 2 fixes the rescan insert to set `region_discovery`).
- **Tiers are an AWS-scanner-v2 concept.** Azure/Entra/GCP scanners are not uplifted. The tier picker and the live progress view are **AWS-only**; non-AWS connections keep the existing plain "Rescan" button with the toast. Flag if that should change.
- **Known backend limitation (deferred review item, see HANDOFF):** the scanner writes the coverage map to `scans.scope` only *after* Phase 2 finishes — so during a running scan `coverage_map` is empty. The progress view must work from `phase` + `finding_count` alone while running, and show the region census only once `coverage_map` is populated. `ScanProgress` (Task 3) is designed to degrade gracefully.
- **Commit after every task** with a Conventional Commit message.
- Tasks 1–2 are the backend group; tasks 3–6 the web group (they depend on Task 1's API shape). Task 7 builds/deploys/verifies. They may be reviewed as two groups.

## File structure

```
platform/lambda/connections_list/main.py   modified — latest_scan + tiered Fargate rescan  (Tasks 1,2)
platform/lib/api-stack.ts                   modified — SCAN_* env + ecs grants on conns Lambda (Task 2)
web/src/lib/api.ts                           modified — scan types, getScanStatus, rescan tier (Tasks 1,2)
web/src/scan/scanLabels.ts                   new — tier/phase label helpers + relative time      (Task 3)
web/src/scan/scanLabels.test.ts              new — tests                                          (Task 3)
web/src/scan/useScanStatus.ts                new — polling hook for GET /v1/scans/{id}            (Task 3)
web/src/scan/useScanStatus.test.tsx          new — tests                                          (Task 3)
web/src/scan/ScanTypeBadge.tsx               new — "Quick Scan · 2h ago" pill                     (Task 3)
web/src/scan/ScanTypeBadge.test.tsx          new — tests                                          (Task 3)
web/src/scan/ScanProgress.tsx                new — in-progress scan view                          (Task 3)
web/src/scan/ScanProgress.test.tsx           new — tests                                          (Task 3)
web/src/routes/ConnectClouds.tsx             modified — scan-type picker + live progress          (Task 4)
web/src/routes/ContactDeepScan.tsx           new — Deep-tier Contact-Us page                      (Task 5)
web/src/App.tsx                              modified — /contact/deep-scan route                  (Task 5)
web/src/routes/TopRisks.tsx                  modified — scan-type badge in header                 (Task 6)
web/src/routes/Risks.tsx                     modified — scan-type badge in header                 (Task 6)
web/src/routes/Dashboard.tsx                 modified — scan-type badge in header                 (Task 6)
```

---

## Task 1: Backend — `latest_scan` on `GET /connections`

The app must discover an in-flight scan on a cold page load (the spec's primary case: the user onboards a cloud and *returns* to the app). Add the most-recent scan per connection to the `/connections` response.

**Files:**
- Modify: `platform/lambda/connections_list/main.py`
- Modify: `web/src/lib/api.ts`

- [ ] **Step 1: Rewrite the `_list_connections` SQL + response**

In `platform/lambda/connections_list/main.py`, replace the SQL and the row-mapping in `_list_connections` (currently lines ~67–94):

```python
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=(
            "SELECT c.conn_id::text, c.cloud_type, c.display_name, c.status, "
            "       c.account_identifier, c.signals::text, "
            "       c.last_scan_at::text, c.created_at::text, "
            "       s.scan_id::text, s.tier, s.status, s.phase, s.started_at::text "
            "FROM cloud_connections c "
            "LEFT JOIN LATERAL ("
            "  SELECT scan_id, tier, status, phase, started_at "
            "  FROM scans WHERE scans.conn_id = c.conn_id "
            "  ORDER BY started_at DESC LIMIT 1"
            ") s ON true "
            "WHERE c.tenant_id = CAST(:tid AS UUID) "
            "ORDER BY c.created_at DESC"
        ),
        parameters=[{"name": "tid", "value": {"stringValue": tenant_id}}],
    )

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

    return _resp(200, {"connections": connections})
```

Update the module docstring's `Response for GET` block to add `"latest_scan": {...} | null`.

- [ ] **Step 2: Syntax-check the Lambda**

Run: `python3 -c "import ast; ast.parse(open('platform/lambda/connections_list/main.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Validate the SQL against the live DB**

Run (read-only — the JOIN must parse and execute):

```bash
aws rds-data execute-statement \
  --resource-arn "arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh" \
  --secret-arn "arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp" \
  --database ciso_copilot \
  --sql "SELECT c.conn_id::text, s.scan_id::text, s.tier, s.status, s.phase, s.started_at::text FROM cloud_connections c LEFT JOIN LATERAL (SELECT scan_id, tier, status, phase, started_at FROM scans WHERE scans.conn_id = c.conn_id ORDER BY started_at DESC LIMIT 1) s ON true ORDER BY c.created_at DESC LIMIT 5" \
  --output json
```

Expected: a JSON `records` array, no `DatabaseErrorException`. Connections with a scan show the scan's id/tier/status/phase; connections with none show nulls.

- [ ] **Step 4: Add the scan types + `latest_scan` + `getScanStatus` to the API client**

In `web/src/lib/api.ts`, add these exported types near the top (after the imports, before `MeResponse`):

```ts
export type ScanTier   = "quick" | "medium" | "deep";
export type ScanPhase  = "region_discovery" | "first_signal" | "crown_jewel" | "full" | "done";
export type ScanState  = "queued" | "running" | "partial" | "completed" | "failed";

export interface LatestScan {
  scan_id:    string;
  tier:       ScanTier;
  status:     ScanState;
  phase:      ScanPhase;
  started_at: string | null;
}

export interface ScanCoverageMap {
  tier?:    string;
  regions?: Record<string, { state: string; errors?: string[] }>;
}

export interface ScanStatus {
  scan_id:       string;
  tier:          ScanTier;
  status:        ScanState;
  phase:         ScanPhase;
  coverage_map:  ScanCoverageMap | null;
  started_at:    string | null;
  finished_at:   string | null;
  finding_count: number;
}
```

Add `latest_scan` to the `Connection` interface (after `created_at`):

```ts
  created_at:         string;
  latest_scan:        LatestScan | null;
```

Add the `getScanStatus` method to the `api` object (place it next to `rescanConnection`):

```ts
  getScanStatus: (scanId: string) => call<ScanStatus>(`/scans/${scanId}`),
```

- [ ] **Step 5: Typecheck the web app**

Run: `cd web && npx tsc -b`
Expected: no errors. (Existing `ConnectClouds.tsx` etc. still compile — `latest_scan` is additive.)

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/connections_list/main.py web/src/lib/api.ts
git commit -m "feat: expose latest scan per connection + scan-status API client"
```

---

## Task 2: Backend — tier-aware AWS rescan via the v2 Fargate scanner

The current `_rescan_aws` async-invokes the legacy `SHASTA_RUNNER_FN` Lambda with a region list and no tier. The v2 scanner runs on Fargate and reads `SCAN_TIER`. Make AWS rescan accept a tier and run the Fargate task — mirroring `onboarding_aws_complete._enqueue_initial_scan`.

**Files:**
- Modify: `platform/lambda/connections_list/main.py`
- Modify: `platform/lib/api-stack.ts`
- Modify: `web/src/lib/api.ts`

- [ ] **Step 1: Give the connections Lambda the scan env vars + ECS permissions**

In `platform/lib/api-stack.ts`, find the Lambda construct that backs the `/connections` resource (it is wired to `connectionByIdRes.addResource('rescan')` around line 445 — trace the `LambdaIntegration` back to its `lambda.Function`). The onboarding Lambda already has the pattern; mirror it onto the connections Lambda:

1. Add to the connections Lambda's `environment`:
   ```ts
   SCAN_CLUSTER_ARN:       props.scanCluster.clusterArn,
   SCAN_TASK_DEF_ARN:      props.scanTaskDefFamily,
   SCAN_SUBNET_IDS:        props.scanSubnetIds.join(','),   // use the same value the onboarding Lambda uses
   SCAN_SECURITY_GROUP_ID: props.scanTaskSecurityGroupId,
   ```
   (Match the exact prop expressions used for the onboarding Lambda's `SCAN_*` env — see api-stack.ts lines ~132–137.)
2. Add the same two IAM policy statements the onboarding Lambda has (api-stack.ts lines ~153–166):
   ```ts
   connectionsFn.addToRolePolicy(new iam.PolicyStatement({
     actions:   ['ecs:RunTask'],
     resources: [`arn:aws:ecs:${this.region}:${this.account}:task-definition/${props.scanTaskDefFamily}:*`],
   }));
   connectionsFn.addToRolePolicy(new iam.PolicyStatement({
     actions:   ['iam:PassRole'],
     resources: [props.scanTaskDefTaskRoleArn, props.scanTaskDefExecutionRoleArn],
   }));
   ```
   (Use the connections Lambda's actual variable name.)

- [ ] **Step 2: Verify the CDK still synthesizes**

Run: `cd platform && npx cdk synth CisoCopilotApi`
Expected: synthesizes clean, no errors.

- [ ] **Step 3: Make `_insert_scan` set `tier` and `phase`**

In `platform/lambda/connections_list/main.py`, replace `_insert_scan` (currently lines ~364–378):

```python
def _insert_scan(scan_id: str, tenant_id: str, conn_id: str, scope: dict,
                 *, tier: str = "quick") -> None:
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "INSERT INTO scans "
            "(scan_id, tenant_id, conn_id, trigger, status, tier, phase, scope) "
            "VALUES (CAST(:sid AS UUID), CAST(:tid AS UUID), CAST(:cid AS UUID), "
            "        'manual', 'queued', :tier, 'region_discovery', "
            "        CAST(:scope AS JSONB))"
        ),
        parameters=[
            {"name": "sid",   "value": {"stringValue": scan_id}},
            {"name": "tid",   "value": {"stringValue": tenant_id}},
            {"name": "cid",   "value": {"stringValue": conn_id}},
            {"name": "tier",  "value": {"stringValue": tier}},
            {"name": "scope", "value": {"stringValue": json.dumps(scope)}},
        ],
    )
```

(`'region_discovery'` is correct for every fresh scan — non-AWS scanners simply never advance it; that field is unused for them.)

- [ ] **Step 4: Make `_rescan` read the tier from the request body**

In `_rescan` (currently lines ~101–141), after resolving `conn` and before the `cloud = conn["cloud_type"]` dispatch, parse the tier:

```python
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        body = {}
    tier = (body.get("tier") or "medium").lower()
    if tier not in ("quick", "medium"):
        # 'deep' is gated (the web routes Deep to Contact-Us); reject anything else.
        return _resp(422, {"error": "unsupported_tier", "tier": tier})
```

Then change the AWS dispatch line to pass `tier`:

```python
        if cloud == "aws":
            scan_id = _rescan_aws(conn, tenant_id, tier)
```

(Leave the `azure`/`entra`/`gcp` branches unchanged — they do not take a tier.)

- [ ] **Step 5: Rewrite `_rescan_aws` to run the Fargate task**

Replace `_rescan_aws` (currently lines ~144–174) entirely:

```python
def _rescan_aws(conn: dict, tenant_id: str, tier: str) -> str:
    """Start a v2 Fargate scan at `tier`. Region discovery runs inside the
    scanner — REGIONS is intentionally omitted."""
    if not (SCAN_CLUSTER_ARN and SCAN_TASK_DEF_ARN and SCAN_SUBNET_IDS):
        raise _IncompleteConnection("scan task not configured")
    secret_arn  = conn.get("credentials_secret_arn")
    account_id  = conn.get("account_identifier")
    external_id = conn.get("external_id")
    if not secret_arn or not account_id:
        raise _IncompleteConnection("missing credentials_secret_arn or account_identifier")

    secret   = _get_secret_json(secret_arn)
    role_arn = secret.get("role_arn")
    if not role_arn:
        raise _IncompleteConnection("missing role_arn in secret")
    ext_id = external_id or secret.get("external_id")

    scan_id = str(uuid.uuid4())
    _insert_scan(scan_id, tenant_id, conn["conn_id"], {}, tier=tier)
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
                        {"name": "CONN_ID",     "value": conn["conn_id"]},
                        {"name": "ROLE_ARN",    "value": role_arn},
                        {"name": "EXTERNAL_ID", "value": ext_id or ""},
                        {"name": "ACCOUNT_ID",  "value": account_id},
                        {"name": "SCAN_TIER",   "value": tier},
                    ],
                }],
            },
        )
        print(f"rescan {scan_id} ({tier}) started for {conn['conn_id']}")
    except Exception as e:
        print(f"WARN: rescan RunTask failed for {conn['conn_id']}: {e}")
    return scan_id
```

Add the ECS client + env vars near the other module globals (after the `GCP_RUNNER_FN` line ~37 and the `lambda_client` line ~41):

```python
SCAN_CLUSTER_ARN  = os.environ.get("SCAN_CLUSTER_ARN", "")
SCAN_TASK_DEF_ARN = os.environ.get("SCAN_TASK_DEF_ARN", "")
SCAN_SUBNET_IDS   = os.environ.get("SCAN_SUBNET_IDS", "")
SCAN_SECURITY_GROUP_ID = os.environ.get("SCAN_SECURITY_GROUP_ID", "")
```

```python
ecs = boto3.client("ecs")
```

(`_rescan_aws` no longer reads `conn["scope"]` for regions; the v2 scanner discovers regions itself.)

- [ ] **Step 6: Syntax-check the Lambda**

Run: `python3 -c "import ast; ast.parse(open('platform/lambda/connections_list/main.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 7: Make `rescanConnection` accept a tier in the API client**

In `web/src/lib/api.ts`, replace `rescanConnection`:

```ts
  rescanConnection: (connId: string, tier: "quick" | "medium" = "medium") =>
    call<{ scan_id: string; status: string }>(`/connections/${connId}/rescan`, {
      method: "POST", body: JSON.stringify({ tier }),
    }),
```

- [ ] **Step 8: Typecheck the web app**

Run: `cd web && npx tsc -b`
Expected: no errors. (`ConnectClouds.tsx` calls `rescanConnection(connId)` with no tier — still valid, `tier` defaults.)

- [ ] **Step 9: Commit**

```bash
git add platform/lambda/connections_list/main.py platform/lib/api-stack.ts web/src/lib/api.ts
git commit -m "feat: route AWS rescan to the v2 Fargate scanner with a depth tier"
```

---

## Task 3: Web — the shared `src/scan/` module

A polling hook, a scan-type badge, a progress view, and label helpers — reused by ConnectClouds and the findings/dashboard headers. Mirrors the codebase's feature-folder convention (`src/chat/`).

**Files:**
- Create: `web/src/scan/scanLabels.ts`, `web/src/scan/scanLabels.test.ts`
- Create: `web/src/scan/useScanStatus.ts`, `web/src/scan/useScanStatus.test.tsx`
- Create: `web/src/scan/ScanTypeBadge.tsx`, `web/src/scan/ScanTypeBadge.test.tsx`
- Create: `web/src/scan/ScanProgress.tsx`, `web/src/scan/ScanProgress.test.tsx`

- [ ] **Step 1: Write the failing test for `scanLabels.ts`**

Create `web/src/scan/scanLabels.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import {
  scanTierLabel, scanTierBlurb, scanTierDuration, phaseLabel, relativeTime,
  mostRecentCompletedScan,
} from "./scanLabels";
import type { Connection } from "../lib/api";

describe("scanLabels", () => {
  it("labels each tier", () => {
    expect(scanTierLabel("quick")).toBe("Quick Scan");
    expect(scanTierLabel("medium")).toBe("Medium Scan");
    expect(scanTierLabel("deep")).toBe("Deep Scan");
  });

  it("has a blurb and a duration for each tier", () => {
    expect(scanTierBlurb("quick")).toMatch(/crown/i);
    expect(scanTierDuration("medium")).toMatch(/min/);
  });

  it("maps phases to human text", () => {
    expect(phaseLabel("region_discovery")).toMatch(/regions/i);
    expect(phaseLabel("crown_jewel")).toMatch(/phase 2/i);
  });

  it("formats relative time", () => {
    const fiveMinAgo = new Date(Date.now() - 5 * 60 * 1000).toISOString();
    expect(relativeTime(fiveMinAgo)).toBe("5m ago");
    expect(relativeTime(new Date().toISOString())).toBe("just now");
  });

  it("picks the most recent completed scan across connections", () => {
    const conns = [
      { latest_scan: { scan_id: "a", tier: "quick", status: "completed",
                       phase: "done", started_at: "2026-05-20T00:00:00Z" } },
      { latest_scan: { scan_id: "b", tier: "medium", status: "completed",
                       phase: "done", started_at: "2026-05-21T00:00:00Z" } },
      { latest_scan: { scan_id: "c", tier: "deep", status: "running",
                       phase: "full", started_at: "2026-05-22T00:00:00Z" } },
    ] as Connection[];
    const r = mostRecentCompletedScan(conns);
    expect(r?.scan_id).toBe("b"); // 'c' is still running; 'b' is the newest completed
  });

  it("returns null when no connection has a completed scan", () => {
    expect(mostRecentCompletedScan([] as Connection[])).toBeNull();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd web && npx vitest run src/scan/scanLabels.test.ts`
Expected: FAIL — `scanLabels.ts` does not exist.

- [ ] **Step 3: Implement `scanLabels.ts`**

Create `web/src/scan/scanLabels.ts`:

```ts
import type { Connection, LatestScan, ScanPhase, ScanTier } from "../lib/api";

const TIER_LABEL: Record<ScanTier, string> = {
  quick: "Quick Scan", medium: "Medium Scan", deep: "Deep Scan",
};
export function scanTierLabel(tier: ScanTier): string {
  return TIER_LABEL[tier] ?? "Scan";
}

const TIER_BLURB: Record<ScanTier, string> = {
  quick:  "Crown-jewel checks across your active regions.",
  medium: "Full posture across every region.",
  deep:   "Full posture plus code & vulnerability review.",
};
export function scanTierBlurb(tier: ScanTier): string {
  return TIER_BLURB[tier] ?? "";
}

const TIER_DURATION: Record<ScanTier, string> = {
  quick: "~5 min", medium: "~20 min", deep: "code & vuln review",
};
export function scanTierDuration(tier: ScanTier): string {
  return TIER_DURATION[tier] ?? "";
}

const PHASE_TEXT: Record<ScanPhase, string> = {
  region_discovery: "Discovering regions…",
  first_signal:     "Phase 1: account posture…",
  crown_jewel:      "Phase 2: crown-jewel checks…",
  full:             "Scanning every region…",
  done:             "Scan complete",
};
export function phaseLabel(phase: ScanPhase): string {
  return PHASE_TEXT[phase] ?? phase;
}

export function relativeTime(iso: string): string {
  const secs = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return "just now";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

/** The newest completed/partial scan across all connections — "the scan
 *  behind" the findings/dashboard views. Returns null if there is none. */
export function mostRecentCompletedScan(connections: Connection[]): LatestScan | null {
  const done = connections
    .map((c) => c.latest_scan)
    .filter((s): s is LatestScan =>
      s != null && (s.status === "completed" || s.status === "partial") && s.started_at != null);
  if (done.length === 0) return null;
  return done.reduce((a, b) =>
    new Date(b.started_at!).getTime() > new Date(a.started_at!).getTime() ? b : a);
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd web && npx vitest run src/scan/scanLabels.test.ts`
Expected: PASS (6 tests).

- [ ] **Step 5: Write the failing test for `useScanStatus.ts`**

Create `web/src/scan/useScanStatus.test.tsx`:

```tsx
// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor, cleanup } from "@testing-library/react";

const getScanStatus = vi.fn();
vi.mock("../lib/api", () => ({ api: { getScanStatus: (id: string) => getScanStatus(id) } }));

import { useScanStatus } from "./useScanStatus";

beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => cleanup());

describe("useScanStatus", () => {
  it("does nothing when scanId is null", async () => {
    const { result } = renderHook(() => useScanStatus(null));
    expect(result.current.scan).toBeNull();
    expect(result.current.loading).toBe(false);
    expect(getScanStatus).not.toHaveBeenCalled();
  });

  it("fetches and stops once the scan is terminal", async () => {
    getScanStatus.mockResolvedValue({
      scan_id: "s1", tier: "quick", status: "completed", phase: "done",
      coverage_map: null, started_at: null, finished_at: null, finding_count: 12,
    });
    const { result } = renderHook(() => useScanStatus("s1", 50));
    await waitFor(() => expect(result.current.scan?.status).toBe("completed"));
    const callsAfterTerminal = getScanStatus.mock.calls.length;
    await new Promise((r) => setTimeout(r, 120));
    // No further polls after a terminal status.
    expect(getScanStatus.mock.calls.length).toBe(callsAfterTerminal);
  });

  it("keeps polling while the scan is running", async () => {
    getScanStatus.mockResolvedValue({
      scan_id: "s2", tier: "quick", status: "running", phase: "crown_jewel",
      coverage_map: null, started_at: null, finished_at: null, finding_count: 3,
    });
    renderHook(() => useScanStatus("s2", 30));
    await waitFor(() => expect(getScanStatus.mock.calls.length).toBeGreaterThan(1));
  });
});
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `cd web && npx vitest run src/scan/useScanStatus.test.tsx`
Expected: FAIL — `useScanStatus.ts` does not exist.

- [ ] **Step 7: Implement `useScanStatus.ts`**

Create `web/src/scan/useScanStatus.ts`:

```ts
import { useEffect, useRef, useState } from "react";
import { api, type ScanStatus } from "../lib/api";

const TERMINAL = new Set<ScanStatus["status"]>(["partial", "completed", "failed"]);

export interface UseScanStatus {
  scan:    ScanStatus | null;
  loading: boolean;
  error:   string | null;
}

/** Poll GET /v1/scans/{id} every `intervalMs` until the scan reaches a
 *  terminal status, then stop. Pass scanId=null to disable polling. */
export function useScanStatus(scanId: string | null, intervalMs = 4000): UseScanStatus {
  const [scan, setScan]       = useState<ScanStatus | null>(null);
  const [loading, setLoading] = useState<boolean>(scanId != null);
  const [error, setError]     = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    if (!scanId) { setScan(null); setLoading(false); setError(null); return; }
    let cancelled = false;
    setLoading(true);

    const tick = async () => {
      try {
        const s = await api.getScanStatus(scanId);
        if (cancelled) return;
        setScan(s); setError(null); setLoading(false);
        if (!TERMINAL.has(s.status)) {
          timer.current = window.setTimeout(tick, intervalMs);
        }
      } catch (e) {
        if (cancelled) return;
        setError((e as Error).message); setLoading(false);
        timer.current = window.setTimeout(tick, intervalMs * 2);
      }
    };
    tick();

    return () => {
      cancelled = true;
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [scanId, intervalMs]);

  return { scan, loading, error };
}
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `cd web && npx vitest run src/scan/useScanStatus.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 9: Write the failing test for `ScanTypeBadge.tsx`**

Create `web/src/scan/ScanTypeBadge.test.tsx`:

```tsx
// @vitest-environment jsdom
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { ScanTypeBadge } from "./ScanTypeBadge";

afterEach(() => cleanup());

describe("ScanTypeBadge", () => {
  it("renders the tier label and relative time", () => {
    const oneHourAgo = new Date(Date.now() - 60 * 60 * 1000).toISOString();
    render(<ScanTypeBadge tier="quick" at={oneHourAgo} />);
    expect(screen.getByText(/Quick Scan/)).toBeTruthy();
    expect(screen.getByText(/1h ago/)).toBeTruthy();
  });

  it("renders nothing when tier is null", () => {
    const { container } = render(<ScanTypeBadge tier={null} at={null} />);
    expect(container.firstChild).toBeNull();
  });
});
```

- [ ] **Step 10: Run the test to verify it fails**

Run: `cd web && npx vitest run src/scan/ScanTypeBadge.test.tsx`
Expected: FAIL — `ScanTypeBadge.tsx` does not exist.

- [ ] **Step 11: Implement `ScanTypeBadge.tsx`**

Create `web/src/scan/ScanTypeBadge.tsx`:

```tsx
import type { ScanTier } from "../lib/api";
import { scanTierLabel, relativeTime } from "./scanLabels";

/** A pill naming the scan behind the current view — "Quick Scan · 2h ago".
 *  Renders nothing when there is no scan to attribute. */
export function ScanTypeBadge({ tier, at }: { tier: ScanTier | null; at: string | null }) {
  if (!tier) return null;
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-700">
      {scanTierLabel(tier)}{at ? ` · ${relativeTime(at)}` : ""}
    </span>
  );
}
```

- [ ] **Step 12: Run the test to verify it passes**

Run: `cd web && npx vitest run src/scan/ScanTypeBadge.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 13: Write the failing test for `ScanProgress.tsx`**

Create `web/src/scan/ScanProgress.test.tsx`:

```tsx
// @vitest-environment jsdom
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { ScanProgress } from "./ScanProgress";
import type { ScanStatus } from "../lib/api";

afterEach(() => cleanup());

const base: ScanStatus = {
  scan_id: "s1", tier: "quick", status: "running", phase: "crown_jewel",
  coverage_map: null, started_at: null, finished_at: null, finding_count: 7,
};

describe("ScanProgress", () => {
  it("shows the phase text and finding count while running", () => {
    render(<ScanProgress scan={base} />);
    expect(screen.getByText(/Phase 2/i)).toBeTruthy();
    expect(screen.getByText(/7 findings/)).toBeTruthy();
    expect(screen.getByText(/Quick Scan running/i)).toBeTruthy();
  });

  it("shows a failure message for a failed scan", () => {
    render(<ScanProgress scan={{ ...base, status: "failed" }} />);
    expect(screen.getByText(/failed/i)).toBeTruthy();
  });

  it("shows the region census once the coverage map is populated", () => {
    render(<ScanProgress scan={{
      ...base, status: "completed", phase: "done",
      coverage_map: { regions: {
        "us-east-1": { state: "active" }, "us-west-1": { state: "default_only" },
      } },
    }} />);
    expect(screen.getByText(/2 regions scanned/)).toBeTruthy();
    expect(screen.getByText(/1 active/)).toBeTruthy();
  });
});
```

- [ ] **Step 14: Run the test to verify it fails**

Run: `cd web && npx vitest run src/scan/ScanProgress.test.tsx`
Expected: FAIL — `ScanProgress.tsx` does not exist.

- [ ] **Step 15: Implement `ScanProgress.tsx`**

Create `web/src/scan/ScanProgress.tsx`:

```tsx
import type { ScanStatus } from "../lib/api";
import { phaseLabel, scanTierLabel } from "./scanLabels";

/** The in-progress / just-finished scan view. Works from `phase` +
 *  `finding_count` while running; shows the region census only once the
 *  scanner has written the coverage map (it does so after Phase 2). */
export function ScanProgress({ scan }: { scan: ScanStatus }) {
  const done   = scan.status === "completed" || scan.status === "partial";
  const failed = scan.status === "failed";
  const regions = scan.coverage_map?.regions
    ? Object.values(scan.coverage_map.regions)
    : null;
  const activeCount = regions
    ? regions.filter((r) => r.state === "active").length
    : null;

  return (
    <div className="mt-2 rounded-xl border border-blue-200 bg-blue-50 p-3 text-sm">
      <div className="flex items-center justify-between">
        <span className="font-medium text-blue-800">
          {scanTierLabel(scan.tier)} {done ? "complete" : failed ? "failed" : "running"}
        </span>
        <span className="text-xs text-blue-600">{scan.finding_count} findings</span>
      </div>
      {failed ? (
        <div className="mt-1 text-xs text-red-600">
          Scan failed — retry from the Scan button.
        </div>
      ) : (
        <div className="mt-1 text-xs text-blue-700">{phaseLabel(scan.phase)}</div>
      )}
      {regions && (
        <div className="mt-1 text-xs text-blue-600">
          {regions.length} regions scanned
          {activeCount != null ? ` · ${activeCount} active` : ""}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 16: Run the test to verify it passes**

Run: `cd web && npx vitest run src/scan/ScanProgress.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 17: Commit**

```bash
git add web/src/scan/
git commit -m "feat: add the shared scan-status web module (hook, badge, progress)"
```

---

## Task 4: Web — the scan-type picker + live progress in ConnectClouds

Replace the plain "Rescan" button on each AWS connection with a Quick/Medium/Deep picker, show a live progress view for any in-flight scan, and route Deep to Contact-Us.

**Files:**
- Modify: `web/src/routes/ConnectClouds.tsx`

- [ ] **Step 1: Add imports**

At the top of `web/src/routes/ConnectClouds.tsx`, add `useNavigate` to the react-router import and import the scan module pieces:

```tsx
import { Link, useNavigate } from "react-router-dom";
import { useScanStatus } from "../scan/useScanStatus";
import { ScanProgress } from "../scan/ScanProgress";
import { scanTierBlurb, scanTierDuration } from "../scan/scanLabels";
```

- [ ] **Step 2: Add a `ConnectionRow` component**

At the bottom of `ConnectClouds.tsx` (next to `CloudTile` / `CloudStatusPill`), add a `ConnectionRow` that owns its own scan polling. It seeds the polled scan id from `conn.latest_scan` if that scan is non-terminal, and updates it when the user triggers a new scan:

```tsx
function ConnectionRow({
  conn, actionMsg, onDelete,
}: {
  conn: Connection;
  actionMsg?: string;
  onDelete: (connId: string, status: Connection["status"]) => void;
}) {
  const navigate = useNavigate();
  const seedId =
    conn.latest_scan &&
    !["completed", "partial", "failed"].includes(conn.latest_scan.status)
      ? conn.latest_scan.scan_id
      : null;
  const [scanId, setScanId] = useState<string | null>(seedId);
  const [scanMsg, setScanMsg] = useState<string | null>(null);
  const { scan } = useScanStatus(scanId);

  async function startScan(tier: "quick" | "medium") {
    setScanMsg("Queuing scan…");
    try {
      const r = await api.rescanConnection(conn.conn_id, tier);
      setScanId(r.scan_id);
      setScanMsg(null);
    } catch (e) {
      setScanMsg(`Failed: ${(e as Error).message}`);
    }
  }

  const isAws = conn.cloud_type === "aws";

  return (
    <li className="py-3 text-sm">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="font-medium flex items-center gap-2">
            <span className="uppercase text-xs text-slate-500 font-mono">{conn.cloud_type}</span>
            <span className="truncate">{conn.display_name}</span>
          </div>
          <div className="text-xs text-slate-500 truncate">{conn.account_identifier ?? "—"}</div>
          {actionMsg && <div className="text-xs text-blue-600 mt-1">{actionMsg}</div>}
          {scanMsg  && <div className="text-xs text-blue-600 mt-1">{scanMsg}</div>}
        </div>
        <CloudStatusPill status={conn.status} />
        <div className="flex items-center gap-2 shrink-0">
          {conn.status === "active" && isAws && (
            <ScanPicker
              onPick={(tier) =>
                tier === "deep" ? navigate("/contact/deep-scan") : startScan(tier)}
            />
          )}
          {conn.status === "active" && !isAws && (
            <button
              type="button"
              onClick={() => startScan("medium")}
              className="px-3 py-1.5 rounded-md bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs"
            >
              Rescan
            </button>
          )}
          <button
            type="button"
            onClick={() => onDelete(conn.conn_id, conn.status)}
            className="px-3 py-1.5 rounded-md bg-red-50 hover:bg-red-100 text-red-700 text-xs"
          >
            Delete
          </button>
        </div>
      </div>
      {scan && <ScanProgress scan={scan} />}
    </li>
  );
}
```

- [ ] **Step 3: Add the `ScanPicker` split-button component**

Also at the bottom of `ConnectClouds.tsx`:

```tsx
function ScanPicker({ onPick }: { onPick: (tier: "quick" | "medium" | "deep") => void }) {
  const [open, setOpen] = useState(false);
  const tiers: Array<"quick" | "medium" | "deep"> = ["quick", "medium", "deep"];
  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="px-3 py-1.5 rounded-md bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs"
      >
        Scan ▾
      </button>
      {open && (
        <div className="absolute right-0 z-10 mt-1 w-56 rounded-lg border border-slate-200 bg-white shadow-lg">
          {tiers.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => { setOpen(false); onPick(t); }}
              className="block w-full text-left px-3 py-2 hover:bg-slate-50"
            >
              <div className="text-sm font-medium capitalize">
                {t}{t === "deep" ? " — contact us" : ""}
              </div>
              <div className="text-xs text-slate-500">
                {scanTierBlurb(t)} <span className="text-slate-400">({scanTierDuration(t)})</span>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Use `ConnectionRow` in the connected-clouds list**

In the `ConnectClouds` component, replace the connected-clouds `<li>...</li>` block (currently lines ~128–161 — the whole `.map((c) => (<li>…</li>))`) with:

```tsx
            {cloudConnections.filter((c) => c.status !== "revoked").map((c) => (
              <ConnectionRow
                key={c.conn_id}
                conn={c}
                actionMsg={cloudActionMsg[c.conn_id]}
                onDelete={deleteCloud}
              />
            ))}
```

Then delete the now-unused `rescanCloud` function (lines ~22–33) and the `cloudActionMsg` writes inside it — but **keep** the `cloudActionMsg` state and the `deleteCloud` function (still used). `deleteCloud`'s signature already matches `onDelete`.

- [ ] **Step 5: Typecheck**

Run: `cd web && npx tsc -b`
Expected: no errors. Resolve any unused-import / unused-variable errors (e.g. drop `cloudActionMsg` if truly unused after Step 4 — it is still read for the delete-failure message, so keep it).

- [ ] **Step 6: Manual smoke in the dev server**

Run: `cd web && pnpm dev`. Open `/connect`, confirm: an active AWS connection shows a `Scan ▾` button; clicking it lists Quick / Medium / Deep with blurbs; picking Quick shows "Queuing scan…" then a `ScanProgress` card; picking Deep navigates to `/contact/deep-scan` (a 404-to-`/` until Task 5 — acceptable here).

- [ ] **Step 7: Commit**

```bash
git add web/src/routes/ConnectClouds.tsx
git commit -m "feat: scan-type picker and live scan progress on the connect screen"
```

---

## Task 5: Web — the Deep-tier Contact-Us route

**Files:**
- Create: `web/src/routes/ContactDeepScan.tsx`
- Modify: `web/src/App.tsx`

- [ ] **Step 1: Create the Contact-Us page**

Create `web/src/routes/ContactDeepScan.tsx`:

```tsx
import { Link } from "react-router-dom";

/// Interim face of the Deep-tier entitlement gate (AWS uplift spec §11) —
/// later replaced by a payment gateway.
export function ContactDeepScan() {
  return (
    <div className="max-w-2xl">
      <h1 className="text-3xl font-bold tracking-tight">Deep Scan</h1>
      <p className="text-slate-600 mt-2">
        A Deep Scan runs the full posture review plus source-code and
        vulnerability analysis. It is a premium tier — talk to us to enable it
        for your account.
      </p>
      <div className="mt-8 rounded-2xl border border-slate-200 p-6">
        <h2 className="font-semibold text-lg">Get in touch</h2>
        <p className="text-sm text-slate-700 mt-2">
          Email us and we will turn on Deep Scans for your tenant.
        </p>
        <a
          href="mailto:hello@transilience.ai?subject=Deep%20Scan%20access"
          className="mt-4 inline-block bg-blue-600 hover:bg-blue-700 text-white font-medium px-5 py-2.5 rounded-lg"
        >
          Email us →
        </a>
      </div>
      <Link to="/connect" className="mt-6 inline-block text-sm text-blue-600 hover:underline">
        ← Back to connections
      </Link>
    </div>
  );
}
```

- [ ] **Step 2: Wire the route**

In `web/src/App.tsx`, add the import:

```tsx
import { ContactDeepScan } from "./routes/ContactDeepScan";
```

Add the route inside the `<Route element={<Shell />}>` group (next to `/connect`):

```tsx
          <Route path="/contact/deep-scan" element={<ContactDeepScan />} />
```

- [ ] **Step 3: Typecheck**

Run: `cd web && npx tsc -b`
Expected: no errors.

- [ ] **Step 4: Manual smoke**

With `pnpm dev` running, from `/connect` pick **Deep** on an AWS connection → lands on `/contact/deep-scan` showing the page; the "Back to connections" link returns to `/connect`.

- [ ] **Step 5: Commit**

```bash
git add web/src/routes/ContactDeepScan.tsx web/src/App.tsx
git commit -m "feat: add the Deep-scan contact-us route"
```

---

## Task 6: Web — scan-type labels on the findings, Risks, and Dashboard headers

Every findings/dashboard view names the scan behind it (§10.3). The label shows the most-recent completed scan across all connections.

**Assumption (flag if wrong):** with one cloud connected, "the scan behind the findings" is unambiguous. With multiple, the label shows the single most-recent completed scan across all connections — per-connection attribution of individual findings is out of scope here.

**Files:**
- Modify: `web/src/routes/TopRisks.tsx`, `web/src/routes/Risks.tsx`, `web/src/routes/Dashboard.tsx`

- [ ] **Step 1: TopRisks (`/findings`) — fetch connections + render the badge**

In `web/src/routes/TopRisks.tsx`:

1. Add imports:
   ```tsx
   import { ScanTypeBadge } from "../scan/ScanTypeBadge";
   import { mostRecentCompletedScan } from "../scan/scanLabels";
   import { type LatestScan } from "../lib/api";
   ```
2. Add state and a fetch in the existing data-loading `useEffect`:
   ```tsx
   const [latestScan, setLatestScan] = useState<LatestScan | null>(null);
   ```
   ```tsx
   api.listConnections()
     .then((r) => setLatestScan(mostRecentCompletedScan(r.connections)))
     .catch(() => setLatestScan(null));
   ```
3. Render the badge in the page header — change the `<h1>` line (line ~212) to:
   ```tsx
      <div className="flex items-center gap-3">
        <h1 className="text-3xl font-bold tracking-tight">Findings</h1>
        <ScanTypeBadge tier={latestScan?.tier ?? null} at={latestScan?.started_at ?? null} />
      </div>
   ```

- [ ] **Step 2: Risks (`/risks`) — same treatment**

In `web/src/routes/Risks.tsx`: add the same imports and `latestScan` state, fetch `api.listConnections()` once in a `useEffect` (add one if there is no mount-effect), and render `<ScanTypeBadge .../>` beside the `<h1>Risk register</h1>` header (wrap the `<h1>` and badge in a `<div className="flex items-center gap-3">`).

- [ ] **Step 3: Dashboard (`/dashboard`) — same treatment**

In `web/src/routes/Dashboard.tsx`: it already calls `api.listConnections()` in its mount `useEffect` — reuse that response. Add `latestScan` state, set it in the existing `.then` via `mostRecentCompletedScan(r.connections)`, and render `<ScanTypeBadge .../>` beside the `<h1>Welcome</h1>` header.

- [ ] **Step 4: Typecheck**

Run: `cd web && npx tsc -b`
Expected: no errors.

- [ ] **Step 5: Manual smoke**

With `pnpm dev`, sign in and visit `/findings`, `/risks`, `/dashboard` — each header shows a "Quick Scan · …" / "Medium Scan · …" pill (or no pill if no completed scan exists).

- [ ] **Step 6: Commit**

```bash
git add web/src/routes/TopRisks.tsx web/src/routes/Risks.tsx web/src/routes/Dashboard.tsx
git commit -m "feat: label findings, risks, and dashboard views by scan type"
```

---

## Task 7: Build, deploy, and verify end-to-end

**Files:** none (build + deploy + verification).

- [ ] **Step 1: Full web build + test + lint**

Run, from `web/`:
```bash
pnpm build          # tsc -b && vite build — must succeed
npx vitest run      # all tests, including the new src/scan/* — must pass
pnpm lint           # must pass
```
Expected: all green.

- [ ] **Step 2: Deploy the backend**

Task 2 changed IAM (the connections Lambda's policy) — a full deploy is required, not a hotswap:
```bash
cd platform && npx cdk deploy CisoCopilotApi --require-approval never
```
Expected: `CisoCopilotApi` deploys; the connections Lambda picks up the new `SCAN_*` env vars and `ecs:RunTask` / `iam:PassRole` permissions.

- [ ] **Step 3: Deploy the web app**

```bash
cd web
pnpm build
aws s3 sync dist/ s3://ciso-copilot-app-470226123496/ --delete
aws cloudfront create-invalidation --distribution-id E2FV1Z0DJ4RQS4 --paths '/*'
```

- [ ] **Step 4: Verify `GET /connections` returns `latest_scan`**

In a signed-in browser session, open the network tab on `/connect` and confirm the `/connections` response includes a `latest_scan` object on the AWS connection (it has prior scans from V2-10 verification).

- [ ] **Step 5: Verify a tiered rescan end-to-end**

On `/connect`, pick **Quick** for the AWS connection. Confirm:
- a new ECS task starts (`aws ecs list-tasks --cluster ciso-copilot-scan` shows a RUNNING task);
- the `ScanProgress` card appears and its phase text advances `Discovering regions… → Phase 1 → Phase 2 → Scan complete` as the poll refreshes;
- the live finding count climbs;
- on completion the region census line appears ("N regions scanned · M active").

- [ ] **Step 6: Verify the scan-type labels + Deep route**

- `/findings`, `/risks`, `/dashboard` each show a scan-type badge in the header.
- On `/connect`, picking **Deep** navigates to `/contact/deep-scan`.

- [ ] **Step 7: No commit**

Verification only. If a step reveals a defect, report it for triage — do not patch silently.

---

## Self-review checklist (for the implementer, before declaring this plan done)

- [ ] `cd web && pnpm build` — clean.
- [ ] `cd web && npx vitest run` — all green, including `src/scan/*`.
- [ ] `cd platform && npx cdk synth CisoCopilotApi` — clean.
- [ ] `GET /connections` returns `latest_scan` per connection (null when no scans).
- [ ] An AWS Quick rescan from the UI starts a Fargate task at the chosen tier and the progress view tracks it through the phases.
- [ ] Findings / Risks / Dashboard headers carry a scan-type badge; Deep routes to Contact-Us.
- [ ] Spec §10 coverage: §10.1 backend contract (Task 1 — the app discovers the scan; the `GET /v1/scans/{id}` API already shipped), §10.2 progress view + chunked results (Tasks 3–4 — `ScanProgress` + the two-phase finding count climbing), §10.3 scan-type labels (Task 6), §10.4 picker + Deep→Contact-Us (Tasks 4–5), §10.5 web-only (no iOS work — flag below if iOS should get the label).

## Out of scope / flag to the controller

- **iOS scan-type label (§10.5).** The spec says iOS shows results with the scan-type label only. That is a separate iOS change, not in this web plan.
- **Mid-scan region census.** The scanner writes the coverage map only after Phase 2 (a deferred review item in HANDOFF). Until that is fixed, `ScanProgress` shows the region census only at completion, not live. The progress view degrades gracefully — no change needed here, but the richer live census depends on that backend fix.
- **Per-connection finding attribution.** The scan-type badge reflects the single most-recent completed scan across all connections, not per-finding provenance.
