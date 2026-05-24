# Entra Free-Tier Licensing Banner (S2.1) — Design Spec

> Small follow-on slice to AI Visibility v2 Slice 2 (`docs/superpowers/specs/2026-05-22-ai-visibility-v2-design.md`).
> Surfaces the Microsoft Entra ID P1/P2 licensing requirement for `auditLogs/signIns`
> in the web UI so customers understand why the AI sign-in pass produces zero
> findings on Free-tier tenants.
>
> Date: 2026-05-24
> Status: brainstorm-approved by KK on 2026-05-24; awaiting written-spec review
> before the implementation plan is written.

---

## 1. What we are building

A sticky boolean flag (`cloud_connections.scope.signin_premium_required`) set by
the Entra runner when Microsoft Graph returns the specific 403
`Authentication_RequestFromNonPremiumTenantOrB2CTenant` error from
`/auditLogs/signIns`. The web app's `/connect` page reads the flag from the
existing `/connections` endpoint and renders a small banner under the affected
Entra row explaining that AI sign-in detection requires Entra ID P1 or P2.

The flag is **sticky** (persists between scans) but **self-clearing** — a future
scan that returns one or more `ai_signin_*` findings (positive signal that
licensing is no longer the gate) wipes the flag.

## 2. Why this is needed

The AI Visibility v2 S2 smoke verification on 2026-05-23 revealed that
`auditLogs/signIns` is gated by Microsoft on Entra ID Premium licensing — not
something we can work around in code. Spec §9.5 was updated post-smoke to
reflect this; HANDOFF documented the licensing wall. But until the banner ships,
a Free-tier customer who runs an Entra scan sees:

- Score tile populated normally (Shasta findings)
- Entra source tile on `/ai` reading zero AI findings
- No explanation for why

The banner closes that loop with one targeted message: "Microsoft restricts
this endpoint to Premium tenants — your scan ran correctly otherwise."

## 3. Scope and non-goals

**In scope:**
1. `ai_signin_pass.run_ai_signin_pass` return-signature change from
   `list[list[dict]]` to `tuple[list[list[dict]], bool]` where the bool is
   `premium_required`.
2. `_fetch_signins` catches the specific 403 (error code
   `Authentication_RequestFromNonPremiumTenantOrB2CTenant`) and signals back via
   the tuple; other 403s (revoked consent, missing scope) do NOT trigger the
   flag.
3. `shasta_runner_entra/app/main.py` writes/clears
   `cloud_connections.scope.signin_premium_required` after the AI sign-in pass.
4. Verify the existing `connections_list` Lambda returns the `scope` JSONB on
   each row; patch if not.
5. `web/src/routes/ConnectClouds.tsx` renders an inline amber-bordered banner
   beneath any `cloud_type='entra'` row whose `scope.signin_premium_required` is
   true.

**Explicitly out of scope (deferred):**
- "Upgrade your Entra licensing" deep-link / purchase flow.
- Per-tenant suppression ("I know — don't show me this banner").
- Email / Slack notification when the flag flips.
- A banner inside `/ai` (the other obvious placement). Brainstorm chose
  `/connect` only to keep the scope of S2.1 tight.
- Generalizing the flag for other Microsoft licensing-gated endpoints (eg
  Identity Protection requires P2). S2.1 only handles the one specific 403 from
  the sign-in pass.

## 4. Decisions log

| # | Decision | Rationale |
|---|---|---|
| D1 | Banner appears on `/connect` Entra row only (not `/ai`) | One place, one signal; matches the mental model "this connector is partially working, here's why" |
| D2 | Sticky flag on `cloud_connections.scope.signin_premium_required` | Simplest write-side model; distinguishes "Free tier" from "Premium tier with no AI users yet" by using the specific 403 as positive signal; auto-clears on a scan that returns sign-in data |
| D3 | Only the specific error code `Authentication_RequestFromNonPremiumTenantOrB2CTenant` triggers the flag | Other 403s (revoked consent, missing scope) need different handling and shouldn't masquerade as licensing problems |
| D4 | `run_ai_signin_pass` returns a tuple `(param_lists, premium_required)` rather than mutating an output dict or throwing | Explicit return value is the simplest signature change |
| D5 | Banner copy explains the licensing constraint + links to Microsoft docs | Customers should know it's a Microsoft-side gate, not a CISO Copilot bug |

## 5. Architecture

One backend path + one frontend render. No new tables, no new endpoints.

```
Entra scan
  → shasta_runner_entra/app/ai_signin_pass.py
    → _fetch_signins catches APIError where error.code ==
      'Authentication_RequestFromNonPremiumTenantOrB2CTenant'
    → run_ai_signin_pass returns (param_lists, premium_required: bool)

  → shasta_runner_entra/app/main.py handler
    → on premium_required=True: UPDATE cloud_connections
        SET scope = jsonb_set(COALESCE(scope, '{}'::jsonb),
                              '{signin_premium_required}', 'true')
    → on premium_required=False AND len(param_lists) > 0:
        SET scope = scope #- '{signin_premium_required}'   (clear)
    → on premium_required=False AND len(param_lists) == 0: NO write
        (neither Free-tier confirmed nor positive signal — could be no AI users)

  → connections_list/main.py (existing)
    → returns `scope` JSONB on each row (verify; patch if absent)

  → web/src/routes/ConnectClouds.tsx ConnectionRow
    → when conn.cloud_type === 'entra' && conn.scope?.signin_premium_required
        render <LicensingBanner> inline beneath the row
```

## 6. Components

### 6.1 `ai_signin_pass.py` — signature + error-handling change

`run_ai_signin_pass` and `_fetch_signins` return-signatures change.

```python
def run_ai_signin_pass(...) -> tuple[list[list[dict]], bool]:
    """Returns (param_lists, premium_required).
    premium_required is True iff Microsoft returned 403
    Authentication_RequestFromNonPremiumTenantOrB2CTenant from
    /auditLogs/signIns. Other failures (auth, scope, network) leave
    premium_required=False — they're not licensing problems and
    shouldn't trigger the banner.
    """
```

`_fetch_signins` catches `APIError` and inspects `error.code`. If the code
matches the licensing-required string, it returns `(events=[], premium_required=True)`;
otherwise it logs the error, returns `(events=[], premium_required=False)`.

### 6.2 `shasta_runner_entra/app/main.py` — handler write

After the AI sign-in pass call (existing try/except remains), add a small
helper:

```python
def _update_connection_premium_flag(conn_id: str, *,
                                    premium_required: bool,
                                    signin_count: int) -> None:
    """Sticky-flag write/clear on cloud_connections.scope.

    Sets `scope.signin_premium_required=true` when the specific 403 was hit.
    Clears the key when a scan emitted >=1 AI sign-in finding (positive
    signal that licensing is no longer the gate). On the ambiguous case
    (no 403 AND no findings — could be Premium tenant with no AI users,
    or a transient Graph error), leave the existing flag value alone.
    """
    if premium_required:
        sql = ("UPDATE cloud_connections "
               "SET scope = jsonb_set(COALESCE(scope, '{}'::jsonb), "
               "                      '{signin_premium_required}', 'true') "
               "WHERE conn_id = CAST(:cid AS UUID)")
        rds_data.execute_statement(...)
    elif signin_count > 0:
        sql = ("UPDATE cloud_connections "
               "SET scope = scope #- '{signin_premium_required}' "
               "WHERE conn_id = CAST(:cid AS UUID)")
        rds_data.execute_statement(...)
    # else: ambiguous case, no-op
```

Called once after `run_ai_signin_pass` returns, inside the existing try/except
so a write failure doesn't fail the scan.

### 6.3 `connections_list/main.py` — verify scope passes through

The existing endpoint already serves `cloud_connections` rows. The `scope`
JSONB column is currently used for Azure subscription names and GCP project
lists — so it almost certainly already passes through. **Verify** in the plan;
patch if absent (likely a one-line column add to the SELECT).

### 6.4 `web/src/routes/ConnectClouds.tsx` — banner render

Add a `LicensingBanner` mini-component (or inline JSX) inside `ConnectionRow`.
Renders only when:
- `conn.cloud_type === 'entra'`
- `conn.scope?.signin_premium_required === true`

Visual style: amber border + amber-50 background card, mirroring the existing
pending-status visual treatment in the same file. Renders inline beneath the
row, doesn't disrupt list layout.

**Banner copy:**

```
⚠ Sign-in detection requires Microsoft Entra ID P1 or P2

Microsoft restricts /auditLogs/signIns to Premium-licensed tenants.
Your tenant is on the Free tier, so AI SaaS sign-in events can't be
detected. All other Entra checks ran normally.

  Learn more about Entra ID licensing →
  (link: https://learn.microsoft.com/en-us/entra/fundamentals/whatis)
```

## 7. Data flow

The `scope` JSONB shape on Entra rows after S2.1:

```json
{
  "signin_premium_required": true
}
```

(or absent, when not flagged). Coexists harmlessly with other future scope keys.

The flag's lifecycle:

```
new entra connection → status='active', scope={}
  ↓
first scan, Free-tier tenant → 403 caught
  ↓
scope = { "signin_premium_required": true }
  ↓
customer upgrades to P1 + rescans → signin_count >= 1
  ↓
scope = {}  (key removed via JSONB #-)
```

## 8. Error handling

| Case | Behaviour |
|---|---|
| Graph 403 `Authentication_RequestFromNonPremiumTenantOrB2CTenant` | Flag set; scan completes with Shasta findings |
| Graph 403 other reasons (revoked consent, missing scope) | Flag NOT set; scan logs the error, completes with Shasta findings |
| Graph network / 5xx error | Flag NOT set; scan logs the error, completes with Shasta findings |
| Connection has flag=true, scan now emits sign-in findings | Flag cleared on this scan |
| Connection has flag=false, scan emits 0 sign-in findings AND no 403 | No write — ambiguous case (could be Premium tenant with no AI users) |
| Connection had no flag, scan returns 0 sign-in findings AND no 403 | No write |
| Aurora write fails | Logged but does NOT fail the scan (matches existing try/except discipline) |

## 9. Testing

**Backend (Python):**
- Unit test for `_fetch_signins`: simulate the specific 403 APIError shape,
  assert it returns `premium_required=True` + empty events.
- Unit test for `_fetch_signins`: simulate a different 403 (e.g.
  `Authorization_RequestDenied`), assert it returns `premium_required=False`
  + empty events.
- Unit test for `_update_connection_premium_flag`: mock `rds_data`, assert the
  SET-branch SQL when `premium_required=True`, assert the CLEAR-branch SQL when
  `premium_required=False` AND `signin_count > 0`, assert no-op when ambiguous.

**Frontend (vitest):**
- Render an Entra `ConnectionRow` with `scope.signin_premium_required=true`,
  assert the banner text and link appear.
- Render an Entra `ConnectionRow` without the flag, assert no banner.
- Render a non-Entra (AWS/Azure/GCP) row with the flag accidentally set, assert
  no banner — banner is Entra-only.

**Manual smoke:**
- KK's existing Free-tier tenant, after S2.1 ships: rerun the Entra scan;
  refresh `/connect`; confirm banner appears beneath the Entra row.

## 10. Risks & open questions

| Risk | Mitigation |
|---|---|
| Microsoft changes the error code string upstream | Catalog matching is a soft contract — if the code changes, we silently no-op (don't set the flag) and KK notices via missing banner; not a regression |
| `connections_list` endpoint doesn't return `scope` | Caught at Plan Task 1 — verify first; one-line patch if needed |
| Web `ConnectionRow` doesn't currently receive `scope` in its props | Caught at Plan; thread through if needed |
| Stale flag for tenant that upgrades licensing but never rescans | Documented limit; rescan is a one-click action — acceptable |

## 11. Done definition

S2.1 is done when:

1. `ai_signin_pass.run_ai_signin_pass` returns `(param_lists, premium_required)`.
2. `_fetch_signins` distinguishes the licensing 403 from other 403s.
3. `shasta_runner_entra/main.py` writes/clears the flag correctly per §6.2.
4. `/connections` API surface exposes `scope` on each row.
5. `/connect` web page renders the banner under Entra rows with the flag.
6. Vitest + pytest suites pass.
7. KK's Free-tier tenant shows the banner after a rescan.

---

*Update this spec only if a structural decision changes. Implementation
detail belongs in the plan.*
