# Scan Screen — Design

> Status: approved (brainstorm 2026-05-22)
> Cross-cloud surface — affects AWS, Azure, GCP, Entra.
> Predecessors / related:
> - Azure scanner uplift Slice 2 (the Connect-page subscription picker —
>   *retired* by this design).
> - GCP scanner uplift Slices 1a + 1b (shipped); Slice 2a (org onboarding,
>   in flight) seeds the project list this screen consumes.

## 1. Purpose

Replace the silent auto-scan-on-onboarding with an explicit **Scan
screen**: a single, multi-cloud landing page where the user sees every
connected cloud, picks scope (subscriptions / projects, where
applicable) and tier (Quick / Medium), and launches scans — either one
at a time or all at once. The Scan screen also serves as the permanent
home for re-running scans; the per-connection-row ScanPicker on the
Connect page is removed.

## 2. Motivation

The current behavior — onboarding completes, a Quick scan auto-fires —
made sense when the user had no choices to make. Slices 1a/1b/2a have
introduced real scope choices (Azure subscriptions, GCP projects) that
matter at scale: an enterprise GCP customer can have 50–150 projects;
auto-scanning all of them is slow, noisy, and wasteful. A silent
auto-scan that fires before the user has chosen scope is actively
wrong.

The Scan screen also consolidates a redundancy that has crept in: the
Connect page currently carries a per-row ScanPicker (subscriptions for
Azure, tier for AWS). Centralising scan triggering in one place removes
two-places-do-the-same-thing rot.

## 3. Scope

**In scope:**
- A new `/scan` route + nav entry "Scan".
- Per-cloud cards: AWS (tier only), Azure (subscriptions + tier), GCP
  (projects + tier; single-project simplifies to tier only), Entra
  (button only).
- A "Launch all scans" bulk action.
- Live scan progress rendered in-card.
- Onboarding webhooks stop auto-scanning; the Connect page surfaces a
  toast linking to `/scan` when a connection flips to `active`.
- Retiring the Connect-page per-row ScanPicker + the inline Azure
  subscription checklist.

**Out of scope (deferred):**
- A "scheduled / recurring" scan UX. Scan triggering stays manual.
- Per-card historical scan list. The card shows only the latest scan;
  the Findings page is the historical store.
- A Deep-tier launcher on the screen. Deep stays gated behind the
  existing `/contact/deep-scan` Contact-Us flow.

## 4. Key decisions (brainstorm outcomes)

1. **Stacked-cards layout** — one full-width card per active
   connection, all visible. (Not tabs, not a sidebar — overkill for the
   typical ≤5 connections.)
2. **Connect-page ScanPicker is removed**, not coexisting. The Scan
   screen is the single home for scope + tier + trigger; the Connect
   page becomes about adding/managing connections only.
3. **Onboarding webhooks drop the silent auto-scan.** A freshly
   onboarded connection appears at `/scan` with `latest_scan: null`
   and a subtle highlight; the user clicks Scan (or Launch all).
4. **No aggressive auto-redirect** post-onboard — a toast nudges to
   `/scan` instead. A user connecting multiple clouds in one sitting
   should not be bounced to `/scan` after each onboarding.
5. **Entra has no tier and no scope** on the card — the tenant is the
   scope and `_rescan_entra` is tierless today. Card is just a "Scan"
   button.
6. **"Launch all" is best-effort parallel**, not transactional — a
   partial failure surfaces a toast naming the failed cloud; the others
   still launch.

## 5. Architecture

### 5.1 Route + nav

- New route `/scan` registered in `web/src/App.tsx` under the existing
  `<Shell>` (auth-gated).
- New nav entry `"Scan"` in `web/src/chat/ModuleRail.tsx`, placed
  immediately after `"Connect clouds"` so the top-to-bottom reading
  order is `Connect → Scan → Findings`.

### 5.2 Page composition

```
ScanPage (web/src/routes/Scan.tsx)
  └─ uses listConnections() → filters status === 'active'
  ├─ <LaunchAllButton />        — fires when ≥1 active connection
  ├─ <ScanCard /> × N           — one per active connection
  │    ├─ <ScanCardHeader />    — cloud name, account identifier, last-scan pill
  │    ├─ <AwsScanCardBody />   — tier picker only
  │    ├─ <AzureScanCardBody /> — subscription checklist + tier picker
  │    ├─ <GcpScanCardBody />   — project checklist + tier picker (or tier-only in single-project mode)
  │    └─ <EntraScanCardBody /> — empty body
  ├─ <PendingConnectionsBlock /> — small disabled rows for non-active
  │                               connections + link to /connect
  └─ <EmptyState />              — when there are 0 connections
```

Card body components branch on `connection.cloud_type` inside the
shared `ScanCard` shell; the shell owns the header, the progress
rendering, and the "Scan" button. Bodies own only their picker form.

### 5.3 Per-card behaviour

For each active connection, the card shows one of three states:

- **Ready** — the picker form + a "Scan" button. Default tier = Quick;
  default scope = all subscriptions/projects.
- **Running** — body replaced by `<ScanProgress />` (the existing
  shared component, reused). Polls `GET /v1/scans/{id}` until terminal.
- **Last scan failed/partial** — header shows a red/amber pill; body
  is the Ready form again so the user can re-launch.

Clicking "Scan" on an Azure or GCP card:
1. If `selected` differs from the connection's current `scope.selected`,
   call `PATCH /connections/{id}` with the new selected list.
2. Call `POST /connections/{id}/rescan` with `{tier}`.
3. Optimistically flip the card to Running and start polling.

Clicking "Scan" on an AWS or Entra card skips step 1 (no scope changes
on those clouds) and goes straight to step 2.

### 5.4 "Launch all scans"

A primary button at the top of the page. Calls `POST
/connections/{id}/rescan` for every active card **in parallel**, with
`tier=quick` and each card's current default scope. If any call returns
non-200, surface a toast naming the failed cloud — the rest still
launch. No transaction semantics.

### 5.5 Cards per cloud

#### AWS
- Header: account id + last-scan pill (status / tier / started_at).
- Body: tier picker (Quick · Medium · "Contact us" link for Deep).
- Scan → `POST /connections/{id}/rescan` `{tier}`.

#### Azure
- Header: subscription count + last-scan pill.
- Body: a subscription checklist rendered from
  `scope.subscription_names` (default = all checked, must ≥1) + tier
  picker.
- Scan → optional `PATCH` then `POST .../rescan` `{tier}`.

#### GCP (single-project mode)
- Header: project id + last-scan pill.
- Body: tier picker only — there's nothing to pick.
- Scan → `POST .../rescan` `{tier}`.

#### GCP (org mode, Slice 2a)
- Header: organisation name + project count + last-scan pill.
- Body: a project checklist rendered from
  `scope.projects` (default = all checked, must ≥1) + tier picker. A
  nudge appears above the list when count > 10: *"Trim to your prod
  projects for a faster first scan."*
- Scan → optional `PATCH` then `POST .../rescan` `{tier}`.

#### Entra
- Header: tenant id + last-scan pill.
- Body: empty (the tenant is the scope; `_rescan_entra` is tierless).
- Scan → `POST .../rescan` (no body).

## 6. Onboarding handoff

### 6.1 Webhooks stop auto-scanning

`onboarding_aws_complete`, `onboarding_azure_complete`,
`onboarding_gcp_complete`, `onboarding_entra_callback` no longer insert
a `scans` row or invoke a scanner. They mark the connection `active`,
seed `scope`, and return. A freshly onboarded connection appears in
`GET /connections` with `latest_scan: null`.

### 6.2 Frontend toast on new-active detection

The Connect page already polls `GET /connections` for connection-status
transitions (pending → active). When the poll detects a `pending →
active` transition, the page surfaces a toast:

> *"Your `<cloud>` connection is ready. Run your first scan →"* (links
> to `/scan`)

This handles AWS / Azure / GCP, which all complete out-of-band (CFN
console, Cloud Shell). Entra's admin-consent redirect lands inside the
web app; its callback handler navigates to `/scan` directly (no toast
needed).

### 6.3 New-connection highlight

`/scan` itself decorates any card whose connection has `latest_scan ===
null` with a subtle border accent + a *"Never scanned"* badge in the
header, so the user immediately sees what's pending after onboarding.

## 7. Connect-page migration

`web/src/routes/ConnectClouds.tsx` is reduced:

- Keep the cloud tiles (Add AWS / Azure / Entra / GCP) and the cloud
  onboarding flows.
- Keep the connection list, but each row shows only status, account
  identifier, last-scan summary (read-only), and a Delete button.
- Remove the per-row `ScanPicker` component invocations.
- Remove the inline Azure subscription checklist.

The shared `web/src/scan/` module (`useScanStatus`, `ScanTypeBadge`,
`ScanProgress`, `scanLabels`) is retained — `/scan` reuses
`ScanProgress` and the polling hook directly. `ScanPicker` is moved
into the new Scan-screen components (or replaced by per-cloud picker
components).

## 8. Out of scope / explicit non-goals

- No scheduled or recurring scans. The Scan screen is a manual
  trigger surface.
- No multi-tenant scan launching. The screen is scoped to the caller's
  tenant via the existing `_resolve_tenant_id` chain.
- No per-card scan history. Latest scan only; Findings page is the
  historical store.
- No Deep-tier launcher. Deep stays gated behind `/contact/deep-scan`.

## 9. Testing

- Unit tests for the per-cloud card body components (each body is a
  small pure-render piece — checklist validation, tier-state, the
  "scope changed → call PATCH" branch).
- Visual smoke test by the user (an agent cannot pass Google OAuth).
- Backend changes for §6.1 (onboarding webhooks drop auto-scan) need
  no live verification beyond confirming a `latest_scan: null` row
  appears on `GET /connections` after onboarding completes; the
  scanner code itself is unchanged.

## 10. Open items (to resolve during planning)

1. The exact toast component / library — the codebase doesn't yet have
   a toast primitive; the implementation plan picks one (or rolls a
   minimal inline one).
2. Whether "Launch all" defaults to Quick *always* or matches each
   card's current tier selection. Lean: always Quick (the bulk action
   is the "give me a quick read across everything" gesture).
3. Whether the new-connection highlight should auto-clear after the
   first scan completes (yes; once `latest_scan` is non-null the badge
   drops).
