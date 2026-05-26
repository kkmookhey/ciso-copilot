# Web-app test plan — "ready for self-service"

> **Goal**: every step a new customer would take, without verbal hand-holding,
> works on the web app. Today is testing + bugfix only. No new features.
>
> Target: a stranger could land on the web app, sign in, connect a cloud,
> see findings, all on their own.

**Web app**: https://shasta.transilience.cloud/
**Test account**: KK's `kkmookhey@gmail.com` (Google sign-in; Microsoft on web is a known-broken path — see test 2).

Each test below: do the action, write **PASS / FAIL / observation**. If FAIL, paste what you saw — we'll fix in line.

---

## Section 1 — First-time visitor

### T1.1 — Landing without auth
1. Open web app in **incognito / private** browser window.
2. Expectation: redirected to `/signin`, see "CISO Copilot" + "Sign in with corporate account" button.

### T1.2 — Sign-in with Google (the working path)
1. Click "Sign in with corporate account".
2. On Cognito Hosted UI, click "Google".
3. Pick `kkmookhey@gmail.com` (or sign in if not already).
4. Expectation: redirected back to web at `/callback`, then `/`, see Welcome screen with tenant name (`gmail.com`).
5. Pull-down browser refresh: still signed in (token persisted in `localStorage`).

### T1.3 — Sign-in with Microsoft (expected to misbehave)
1. Open a fresh incognito.
2. Click sign-in → pick "Microsoft" on the Cognito picker.
3. Expectation: **fails** with iss-mismatch OR "Assignment required" depending on which tenant the legacy `Microsoft` IdP is configured for. We'll address this by porting the email-first flow to web — if test 1.2 passes, we know the core works.
4. Note exactly what you see and any URL in the address bar at point of failure.

### T1.4 — Sign-out + sign back in
1. From Welcome, click your profile / sign-out (wherever Shell exposes it).
2. Confirm redirected to `/signin`.
3. Click sign-in again → Google → expect to land back at `/` without re-entering creds (browser still has Google session).

---

## Section 2 — Tenant approval flow (already approved here)

Your tenant `gmail.com` is already approved in DB; this section is a smoke check.

### T2.1 — Confirm `/` doesn't render PendingApproval
1. After T1.2, you should see Welcome directly, **not** the "Access request pending" screen.

### T2.2 — Approval email path (cold)
*Skip this for now unless we sign in with a fresh email.* If we register a new tenant, we should see an approval email in `kkmookhey@gmail.com` from `kkmookhey@gmail.com` (sandbox-SES sender) within ~1 min. The Approve link should flip tenant to `approved`.

---

## Section 3 — Connect screen

### T3.1 — Navigate to Connect
1. From Welcome, click "Connect" link in shell.
2. Expectation: see 4 cloud tiles (AWS, Azure, Entra, GCP), each clickable.

### T3.2 — AWS tile → generate CFN deep-link
1. Click AWS.
2. Expectation: card appears with "One-click AWS connection" + a Launch CloudFormation button + presigned URL behind it.
3. Verify the URL contains `console.aws.amazon.com/cloudformation/home?...templateURL=https%3A%2F%2Fciso-copilot-cdn-...s3...amazonaws.com%2Fcfn%2Faws-onboard.yaml%3FX-Amz-Algorithm%3D...` (presigned).
4. Right-click → copy link. **Do not** open it (you already have an active AWS connection — opening would create a second pending row).

### T3.3 — Azure tile → generate Cloud Shell command
1. Click Azure.
2. Expectation: card with "Run in Azure Cloud Shell" + a `curl … | bash` command + "Open Cloud Shell" button.
3. Verify the command points at `https://d2pvi2ahuyphb0.cloudfront.net/cfn/azure/onboard.sh` with `?conn_id=...&external_id=...&api_base=...`.
4. **Optional**: actually paste it into a real Azure Cloud Shell to onboard a real subscription. If you don't have an Azure tenant handy, just verify the curl command renders correctly.

### T3.4 — Entra tile → admin-consent URL
1. Click Entra.
2. Expectation: card with "Entra admin consent" + a Microsoft admin consent URL.
3. Verify URL is `https://login.microsoftonline.com/common/adminconsent?client_id=093442df-...&redirect_uri=...%2Fonboarding%2Fentra%2Fcallback&state=...`.
4. **Optional**: actually click through to admin-consent your own `017c6f31-...` dev tenant if you haven't already.

### T3.5 — GCP tile → generate gcloud script
1. Click GCP.
2. Expectation: card with "Run in Google Cloud Shell" + a `curl … | bash` command.
3. Verify the command points at `https://d2pvi2ahuyphb0.cloudfront.net/cfn/gcp/onboard.sh`.
4. **Optional**: paste in a real GCP Cloud Shell against a test project.

### T3.6 — Network errors
1. Throttle network (browser devtools → offline) and click AWS again.
2. Expectation: a red error message appears beneath the tiles, not a silent failure.

---

## Section 4 — Risks / findings

### T4.1 — Navigate to Findings
1. From Connect, click "Findings" / "Risks" in the shell.
2. Expectation: list of findings from your AWS scan. Should be ~270+ rows.

### T4.2 — Severity sort
1. Verify ordering: critical → high → medium → low → info (or whatever sort the API returns).
2. Click into one row.
3. Expectation: detail view with title, description, resource ARN, region, framework mappings, remediation text.

### T4.3 — Filter by severity (if UI supports it)
1. If there's a severity filter, set it to "critical" only.
2. Expectation: list shortens to critical findings only.

### T4.4 — Filter by cloud (if UI supports it)
1. Set cloud filter to AWS (only option for now).
2. Expectation: same list.

### T4.5 — Pull to refresh / refetch
1. Hard-refresh browser tab.
2. Expectation: list re-renders without errors, count unchanged.

### T4.6 — Empty state
*Hard to test without deleting the connection. Skip unless we want to.*

---

## Section 5 — Cross-cutting

### T5.1 — Token refresh
1. Wait ~50 minutes after signing in (id_token TTL is 1h).
2. Click around — Connect, Findings.
3. Expectation: no surprise sign-in screen; the refresh-token flow in `lib/cognito.ts` should silently re-mint.

### T5.2 — Direct deep-link
1. From signed-in state, paste `https://shasta.transilience.cloud/findings` into a new tab.
2. Expectation: lands directly on Findings, not signed back out.

### T5.3 — Direct deep-link while signed out
1. Open in incognito, paste `https://shasta.transilience.cloud/findings`.
2. Expectation: redirected to `/signin`. After sign-in, you should land on Findings (or Welcome — depends on what the app remembers).

### T5.4 — Sign-out clears state
1. Sign out.
2. Try to navigate to `/findings` directly.
3. Expectation: bounced to `/signin`; no leaked data visible.

### T5.5 — Browser back button
1. Sign in → navigate Welcome → Connect → Findings.
2. Press browser back twice.
3. Expectation: lands back at Welcome without breaking auth state.

---

## Section 6 — Browser compatibility (quick smoke)

### T6.1 — Safari (Mac)
- Repeat T1.2 + T3.2 + T4.1.

### T6.2 — Chrome
- Repeat T1.2 + T3.2 + T4.1.

### T6.3 — Mobile Safari (your iPhone)
- Open the web URL in Mobile Safari (not the iOS app). Repeat T1.2 + T4.1.
- Layout should reflow for mobile width.

---

## Bug catch list (fill in as we go)

| # | Test | What broke | Status |
|---|------|------------|--------|
| 1 | T1.3 | (expected Microsoft fail) | known: web sign-in needs email-first port |
| | | | |
| | | | |

---

## Order of execution

1. Section 1 (sign-in flows) — establishes a session.
2. Section 3 (connect screens) — quick UI smoke.
3. Section 4 (findings) — most valuable user-facing feature.
4. Section 5 (cross-cutting) — token refresh, deep links.
5. Section 6 (browser compat) — if time permits.

Skip Section 2 unless we register a new tenant on purpose.

## SOC Slice 1 — AWS Config drift end-to-end (added 2026-05-25)

### Setup (one-time per test session)

- Test AWS account `470226123496` already onboarded with `ConfigRecordingMode=essentials` (default in the latest aws-onboard.yaml).
- Test user has `device_token` populated in the `users` table (verify via Aurora query).
- iPhone signed in to CISO Copilot iOS app on TestFlight.
- APNs Platform Application provisioned (Sandbox): `arn:aws:sns:us-east-1:470226123496:app/APNS_SANDBOX/CISOCopilotAPNSSandbox`.

### Gate

1. In the test AWS account, open a security group to the world on port 22:
   ```bash
   aws ec2 authorize-security-group-ingress \
     --group-id sg-TESTGROUP \
     --protocol tcp --port 22 --cidr 0.0.0.0/0
   ```

2. **Within 20s:** Refresh https://shasta.transilience.cloud/soc — the event appears at the top of the timeline with severity `high`, source `aws.config`, title `AuthorizeSecurityGroupIngress`, resource shown as `sg-TESTGROUP`, actor shown as the IAM user that ran the command.

3. **Within 60s:** iPhone vibrates with a push notification matching the templated body: `drift · high · sg-TESTGROUP · AuthorizeSecurityGroupIngress · by <user>`.

4. **Within ~25s of the event:** Tap the timeline row in `/soc`. The detail pane shows:
   - AI narrative (1-2 sentences naming what happened and why it's notable)
   - Anomaly class (`unusual` or `suspicious` likely; `expected` if this actor regularly opens SGs)
   - Anomaly score 0-100
   - Suggested next steps (e.g., "Revoke the rule" with the corresponding `aws ec2 revoke-security-group-ingress` command)
   - "Why this fired (features)" expandable block showing `first_time_actor_on_resource`, `off_hours`, `action_rarity`, `blast_radius_proxy`
   - Related findings on the resource (if any)
   - Feedback buttons

5. Click 👍 helpful. Expect "Thanks for the feedback" + new row in `feedback` table with `target_kind='event'`, `sentiment='up'`.

6. **Cleanup:** Revoke the rule:
   ```bash
   aws ec2 revoke-security-group-ingress \
     --group-id sg-TESTGROUP \
     --protocol tcp --port 22 --cidr 0.0.0.0/0
   ```
   This generates a second drift event with severity `medium` (revocation is the safe direction — rule fires `medium` per severity_rules.py). Verify it appears in /soc.

### Failure modes to watch

- AI narrative absent for >30s after event lands → check CloudWatch logs for `/aws/lambda/CisoCopilotEvents-SocEnrichment*` (Anthropic 5xx? cap reached? prompt parse failure?).
- Push doesn't arrive → check `users.device_token` populated; check `events.push_sent=true` for the row; check CloudWatch logs for SNS publish errors.
- Duplicate event row → check `source_event_id` is populated; the ON CONFLICT should have deduped.
- 42P10 SQL error → indicates partial unique index ON CONFLICT WHERE clause missing in router INSERT (was a real bug fixed in commit `aeb28bc`).

---

## SOC Slice 1c — TI match end-to-end (2026-05-26)

Verifies the threat-intel substrate (Slice 1c): `/soc` shows a "Threat
intel" section in the detail pane when the drift event's `sourceIPAddress`
or any IP/domain/sha256 in the event payload matches an entry in the
`threat_indicators` table, and the AI narrative names the source.

### Pre-requisites

- Slice 1 demo gate passes (drift end-to-end working).
- Migration `013_phase_soc_ti.sql` applied (Task 1 of Slice 1c, commit `7e611af`).
- All three `ti_feed_*` Lambdas invoked at least once. Verify with:
  ```bash
  aws rds-data execute-statement \
    --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
    --secret-arn  arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
    --database ciso_copilot \
    --sql "SELECT source, COUNT(*) FROM threat_indicators GROUP BY source ORDER BY source"
  ```
  Expected: four rows — `abusech_feodo`, `abusech_threatfox`, `kev`, `tor` — each with count ≥ 1.
- `soc_enrichment` Lambda redeployed with `_shared` modules vendored (Task 13 step 6, hotswap deploy of `CisoCopilotEvents`).
- `events.source_ip` column populated by `event_router` on new CloudTrail events (verify with `SELECT source_ip FROM events WHERE source_ip IS NOT NULL ORDER BY fired_at DESC LIMIT 5`).

### Gate

1. Pick a Tor exit IP from the seeded table:
   ```bash
   aws rds-data execute-statement \
     --resource-arn ... --secret-arn ... --database ciso_copilot \
     --sql "SELECT indicator_value FROM threat_indicators WHERE source='tor' LIMIT 5"
   ```
   Authenticate to the test AWS account via that IP (e.g., spin up an
   EC2 in a region whose default egress is in the Tor list, OR use a
   VPN whose egress is a known Tor exit). Verify your egress matches:
   `curl https://api.ipify.org` should return one of the IPs above.

2. From that source IP, open a security group to the world:
   ```bash
   aws ec2 authorize-security-group-ingress \
     --group-id sg-TESTGROUP \
     --protocol tcp --port 22 --cidr 0.0.0.0/0
   ```

3. Within ~25s, refresh https://shasta.transilience.cloud/soc. The event
   appears at the top of the timeline with severity `high`.

4. Click the event row. The detail pane shows:
   - AI narrative explicitly naming **Tor** (e.g., "Source IP X.X.X.X is
     a Tor exit; ingress :22 opened to internet by user/x").
   - A "Threat intel" section listing one badge per match: the egress
     IP labeled `tor` with tag `tor_exit`. If the IP also appears in
     `abusech_feodo` or `abusech_threatfox`, additional badges show.
   - Anomaly classification likely `suspicious`, score ≥ 70.

5. Verify in Aurora:
   ```sql
   SELECT source_ip,
          ai_features::jsonb -> 'ti_matches'
   FROM events
   WHERE event_id = '<event_id>';
   ```
   The `ti_matches` array is non-empty and contains a `tor`-sourced
   entry keyed on the source IP.

### Negative case

Repeat the same SG-open from a non-listed IP (your home ISP, or a
fresh cloud VM in an account whose egress isn't in the Tor list). The
event should still fire and enrich, but:

- "Threat intel" section is HIDDEN in the detail pane.
- `ai_features.ti_matches` is `[]` in Aurora.
- AI narrative does NOT mention any TI source.

### Failure modes to watch

- "Threat intel" section never appears on a known-Tor IP → check that
  the source IP is in `threat_indicators` (the table is global, so any
  tenant sees all IOCs). Then check `soc_enrichment` CloudWatch logs
  for `_ti_matches` errors or missing vendored modules
  (`ModuleNotFoundError: ti_lookup`).
- `events.source_ip` is NULL on CloudTrail events → confirm
  `event_router` was redeployed with the source_ip patch (Task 8,
  commit `ee05afe`). Run `aws lambda get-function-configuration
  --function-name CisoCopilotEvents-EventRouter*` and check the code
  SHA matches what's currently on `feat/ai-powered-soc-slice-1c`.
- `ti_matches` populated but UI shows no badges → check the browser
  console for the JSON shape; the UI narrows
  `ai_features.ti_matches` and is strict on `Array<{ value, kind,
  source, confidence, tags }>` shape.

