# GuardDuty TI Enrichment — extend SOC briefs to native GuardDuty findings

> Brainstormed 2026-06-10. Extends the existing drift-only SOC AI-enrichment
> pipeline to also produce a CISO-ready brief for high/critical **GuardDuty**
> findings, layering account-specific context (behavioral baseline + Tor/KEV/
> abuse.ch attribution) on top of GuardDuty's native verdict — without
> rebuilding the malicious-IP judgment GuardDuty already makes.
>
> Cross-refs:
> - [`../../codebase/06-soc-ti.md`](../../codebase/06-soc-ti.md) — SOC + TI subsystem map; flags "Enrichment is drift-only" (line 240), the gap this spec closes

## 0. Codebase baseline — verified 2026-06-10

Everything below was confirmed by `grep`/`Read` against the working tree, not memory.

- **Event router** — `platform/lambda/event_router/main.py`. **Already shipped:**
  - Enqueue gate at **line 197**: `if kind == "drift" and ENRICHMENT_QUEUE_URL:` → only drift events are sent to the enrichment SQS queue. Native alerts (`kind=="alert"`) are inserted into `events` but never enqueued.
  - `_classify_kind` (line 363) returns `"drift"` for `AWS API Call via CloudTrail` / `Configuration Item Change Notification`, else `"alert"`.
  - `_severity` (line 411) already maps `aws.guardduty` numeric severity: `>=8 critical`, `>=7 high`, `>=4 medium`, `>=1 low`.
  - `_source_event_id` (line 388) already keys `aws.guardduty` on `detail.id` (idempotency).
  - `_extract_source_ip` (line 348) returns `None` for anything that isn't CloudTrail → GuardDuty alerts land with `source_ip = NULL`.
  - `_extract_states` (line 370) returns `(None, None)` for non-drift → GuardDuty alerts have no before/after.
  - `_insert_event` (line 442) writes the `events` row (incl. `source_ip`, `normalized`).
- **Enrichment consumer** — `platform/lambda/soc_enrichment/`. **Already shipped:**
  - `main.py:_load_event_row` SELECTs `events e LEFT JOIN drift_events d` — already tolerant of non-drift rows (returns `before_state/after_state = NULL`). Writeback `UPDATE events SET ai_narrative, ai_anomaly_class, ai_anomaly_score, ai_next_steps, ai_features, ai_model_version, ai_enriched_at, …`.
  - `features.py:compute_features` → `first_time_actor_on_resource`, `off_hours`, `action_rarity`, `blast_radius_proxy`, `ti_matches`. All read `events` columns only; none require `drift_events`.
  - `features.py:_ti_matches` calls `ioc_extract.extract_iocs(row)` → `ti_lookup.bulk_lookup(...)` → optional GreyNoise fallback.
  - `llm.py` — `SYSTEM` prompt is **drift-specific** ("summarizing a single AWS configuration **drift** event"). `call_llm` enforces the **$10/day/tenant** spend cap (`DAILY_CAP_CENTS_DEFAULT=1000`), returns `model_version="cap_reached"` when exceeded. Model `claude-sonnet-4-6` via LiteLLM. Output schema: `narrative`, `anomaly_class`, `anomaly_score`, `next_steps[]`, `mitre_technique`.
- **Shared TI** — `platform/lambda/_shared/`:
  - `ioc_extract.py:extract_iocs(row)` reads **only** `source_ip`, `before_state`, `after_state`; `_walk` recurses arbitrary nested JSON; filters RFC1918/loopback/link-local/multicast.
  - `ti_lookup.py:bulk_lookup` / `upsert_indicators` against the global `threat_indicators` table.
  - `greynoise.py` — **inert without an API key** (returns `None`); no key is provisioned today.
- **Feeds** (producers, unchanged by this spec) — `ti_feed_abusech` (Feodo `botnet_c2` + ThreatFox, hourly), `ti_feed_kev` (CISA KEV, daily), `ti_feed_tor` (`tor_exit`, hourly). Write `threat_indicators(indicator_value, kind, source, confidence, tags, …)`.
- **Schema** — `events` table created in `platform/sql/002_phase_a.sql`; `source_ip` added in `013_phase_soc_ti.sql`; `ai_*` columns added in `011_phase_soc.sql`. **No `iocs` column exists** (`grep iocs platform/sql/*.sql` → empty). Last migration is **`015_mcp_connectors.sql`; next is 016.**
- **SOC UI** — `web/src/routes/Soc.tsx` calls `api.listEvents` only (line 17) and **hardcodes `kind: 'drift'` at line 18** — so GuardDuty alerts would be filtered out of the list even once enriched (real gap; see §5.6). Header (line 32) already reads "Live drift + alert feed". `getEventDetail` lives in `web/src/components/soc/DetailPane.tsx:12` (defined `api.ts:585`); `DetailPane.tsx` renders `ai_narrative` / `ai_next_steps` / `ai_anomaly_*` (also `Timeline.tsx`). `FeedbackButtons.tsx`, `FilterChips.tsx` present (FilterChips controls severity + source, **not** kind). `DetailPane` will render GuardDuty briefs with zero new rendering code.
- **GuardDuty native TI** (verified via AWS docs, `guardduty/latest/ug`): GuardDuty "uses threat intelligence feeds, such as lists of malicious IP addresses and domains, file hashes, and ML models." Findings carry `service.action.{networkConnectionAction,awsApiCallAction,dnsRequestAction}.remoteIpDetails.ipAddressV4` (+ org/ASN/ISP, country/city/geo) and `dnsRequestAction.domain`. **The malicious-IP verdict already exists in the finding.**

**What's genuinely new in this slice:**
1. `events.iocs JSONB` column — migration **016**.
2. Router: `_extract_iocs_guardduty(detail)` + populate `events.iocs` for GuardDuty alerts.
3. Router: widen the enqueue gate (line 197) to include GuardDuty alerts at severity ≥ high.
4. Enrichment: read `events.iocs` and feed it into `_ti_matches`.
5. LLM: make `SYSTEM` kind-aware (GuardDuty framing that adds context, not restating the verdict).
6. UI: a free-tier cap note in the SOC view.
7. Web: widen `Soc.tsx` list query — drop the hardcoded `kind: 'drift'` so GuardDuty alerts surface in the timeline. (Also promote `ioc_extract._is_lookup_worthy_ipv4` → public; second caller.)

## 1. Goal and success criteria

**Goal:** a high/critical GuardDuty finding produces the same AI brief that drift events already get — narrative, anomaly score, next steps, MITRE technique — enriched with the customer's behavioral baseline and supplemental TI attribution, while reusing the existing pipeline.

Success criteria (testable):
1. A `aws.guardduty` alert with `_severity` ∈ {high, critical} is enqueued to the enrichment queue; medium/low/info GuardDuty alerts and **all non-GuardDuty alerts** are NOT.
2. `events.iocs` for that alert contains the finding's `remoteIpDetails` IP(s) and any `dnsRequestAction.domain`, RFC1918/noise filtered.
3. If that IP/domain is in `threat_indicators`, the brief's `ai_features.ti_matches` includes the Tor/KEV/abuse.ch source + tags.
4. The brief's `ai_narrative` adds account-specific context and recommended actions; it does not merely restate "this IP is malicious."
5. Drift enrichment behavior is byte-for-byte unchanged (regression-guarded).
6. Spend over the **shared** $10/day/tenant cap returns `cap_reached` for GuardDuty briefs exactly as for drift.
7. The SOC view shows a free-tier cap note.
8. A high-severity `aws.guardduty` alert appears in the SOC timeline (not filtered out by the list query's `kind` constraint).

## 2. Why this design (and what was reconsidered)

GuardDuty already owns the malicious-IP verdict (§0), so the value-add is *context*, not *detection*. The pipeline that produces that context (behavioral features + TI match + CISO narrative) already exists for drift and is already kind-tolerant (`LEFT JOIN drift_events`). The cheapest correct design is therefore **extend, don't build**: bridge the one real gap (GuardDuty IOCs don't reach `ioc_extract`) and flip the enqueue gate.

**Reconsidered — IOC extraction mechanism:**
- *Rejected: blind `_walk` over the raw finding.* Generic, but grabs the victim resource's own public IP and AWS service IPs, and loses GuardDuty's explicit "remoteIpDetails = adversary" signal. Noisy briefs.
- *Chosen: targeted `_extract_iocs_guardduty`.* Pulls exactly `remoteIpDetails` + `dnsRequestAction.domain`. Precise; scoped to GuardDuty (the only source in scope).

**Reconsidered — where IOCs live:** overloading `source_ip` (single TEXT) can't hold an IP + a domain, and `before/after_state` live in `drift_events` (semantically wrong for an alert). A dedicated nullable `events.iocs JSONB` is explicit and generalizes to other sources later.

## 3. Scope

**In scope:** GuardDuty (`source == "aws.guardduty"`) alerts at severity ≥ high; the 7 changes listed in §0.

**Out of scope (YAGNI):**
- Inspector2 / SecurityHub / other native alert sources (GuardDuty-only by decision).
- GreyNoise (no key; fallback stays inert).
- Cross-tenant IOC sightings (would be new privacy-sensitive data capture).
- Any change to the feed producers or `threat_indicators` schema.
- Backfill of pre-existing GuardDuty events (enrichment applies to new events going forward).

## 4. Components & architecture

```
EventBridge ─► event_router ─► events (INSERT, now incl. events.iocs for GD)
                   │
                   └─ gate (line 197): drift  OR (aws.guardduty AND sev≥high)
                                        └─► SQS enrichment queue
                                                  │
                       soc_enrichment ◄───────────┘
                          ├─ _load_event_row (+ e.iocs)
                          ├─ compute_features → _ti_matches(iocs + bulk_lookup)
                          ├─ call_llm (kind-aware SYSTEM, shared $10/day cap)
                          └─ UPDATE events.ai_*
                                                  │
                       web SOC view (DetailPane) ◄┘  + free-tier cap note
```

## 5. Component detail

### 5.1 Migration 016 — `events.iocs`
`platform/sql/016_events_iocs.sql`: `ALTER TABLE events ADD COLUMN IF NOT EXISTS iocs JSONB;` Nullable; shape `{"ip": [...], "domain": [...], "sha256": [...]}`. No backfill.

### 5.2 Router — `_extract_iocs_guardduty(detail)`
New helper near `_extract_source_ip`. Reads, defensively:
- `service.action.networkConnectionAction.remoteIpDetails.ipAddressV4`
- `service.action.awsApiCallAction.remoteIpDetails.ipAddressV4`
- `service.action.dnsRequestAction.domain`
Returns `{"ip":[...], "domain":[...]}` deduped. **No IP filtering here.** The router is flat-zipped and does not vendor `_shared/ioc_extract.py` (it only copies `push.py` / symlinks `spend_cap.py`); pulling in ioc_extract just to make one filter call isn't worth it. Filtering runs enrichment-side (§5.4), where `ioc_extract` already lives. (GuardDuty `remoteIpDetails` is the adversary's public IP — RFC1918 is essentially never present, so the filter is a safety net, not a hot path.) Called only when `source == "aws.guardduty"`. Result written to `events.iocs` in `_insert_event` (add one column to the INSERT).

### 5.3 Router — enqueue gate (line 197)
```python
_HIGH = {"high", "critical"}
enrich = kind == "drift" or (
    kind == "alert" and source == "aws.guardduty" and severity in _HIGH
)
if enrich and ENRICHMENT_QUEUE_URL:
    sqs.send_message(...)   # body unchanged: {event_id, tenant_id}
```

### 5.4 Enrichment — consume `events.iocs`
- `_load_event_row` SELECT: add `e.iocs::text`; parse JSON into the row dict (mirror the `before/after_state` JSON handling).
- **Promote `ioc_extract._is_lookup_worthy_ipv4` → `is_lookup_worthy_ipv4`** (drop the underscore; update the existing internal call site in `extract_iocs`). It now has a second caller and is a shared utility.
- `features._ti_matches`: union the pre-extracted `row["iocs"]` with `ioc_extract.extract_iocs(row)` before `bulk_lookup`, passing `events.iocs` IPs through `is_lookup_worthy_ipv4` first (the filter the router deliberately skipped, §5.2). Drift rows have `iocs=None` → unchanged. (For GuardDuty, `extract_iocs` finds nothing useful; `events.iocs` carries the signal.)

### 5.5 LLM — kind-aware SYSTEM prompt
`llm.py`: branch the framing on `row["kind"]` (or `source`). For GuardDuty: *"You are summarizing a GuardDuty security finding for a CISO. GuardDuty has ALREADY judged the remote IP/domain malicious — do NOT restate that. Your job: add account-specific context using the behavioral `features` and `features.ti_matches` (supplemental attribution: Tor/KEV/abuse.ch), and give the top concrete next steps."* Output schema unchanged. Drift prompt unchanged.

### 5.6 UI — surface GuardDuty briefs + free-tier cap note
- **`web/src/routes/Soc.tsx:18` — drop the hardcoded `kind: 'drift'`** from the `api.listEvents` call so GuardDuty alerts appear in the timeline. The header (line 32) already reads "Live drift + alert feed" — both were always intended; the filter was never wired. Severity default `['critical','high']` (line 10) already aligns with the sev≥high gate. Leaving `kind` unconstrained returns both kinds; a `kind` FilterChip is a future nicety, out of scope.
- **`web/src/components/soc/DetailPane.tsx`** — in the AI-enrichment section, a small muted note: *"AI briefs are rate-limited on the free tier — $10/day of analysis per workspace."* Static copy (no new API). When `ai_model_version == "cap_reached"`, show the matching empty-state ("daily AI budget reached") rather than a blank brief.

## 6. Testing
- **Router (`event_router/tests`):** GuardDuty high → enqueued + `iocs` populated; GuardDuty medium → not enqueued; non-GuardDuty alert → not enqueued; drift → still enqueued. `_extract_iocs_guardduty` over real `networkConnectionAction` / `dnsRequestAction` fixtures; RFC1918 remote IP filtered.
- **Enrichment (`soc_enrichment/tests`):** `_load_event_row` parses `iocs`; `_ti_matches` returns the seeded Tor/KEV/abuse.ch match for a GuardDuty IP; drift path unchanged (existing tests stay green).
- **LLM:** `build_messages` selects the GuardDuty SYSTEM for `kind="alert"`/guardduty and the drift SYSTEM otherwise (no live model call).
- **Web:** `Soc.test.tsx` — a high-severity `aws.guardduty` alert renders in the `Timeline` (regression guard for the dropped `kind:'drift'` constraint). `DetailPane.test.tsx` renders the cap note; `cap_reached` empty-state.

## 7. Risks
- **LLM cost on alert volume** — mitigated by the sev≥high gate + the existing shared $10/day cap. The cap is now shared across drift + GuardDuty; a GuardDuty burst could exhaust a tenant's drift budget (acceptable; documented in UI).
- **GuardDuty finding shape variance** — many finding types lack `remoteIpDetails` (e.g. some IAM/cryptomining types). `_extract_iocs_guardduty` returns empty for those; the brief still runs on behavioral features alone (no TI match). Acceptable.
- **Idempotency** — writeback is an `UPDATE … WHERE event_id`; re-delivery is safe. Unchanged.

## 8. References
- AWS GuardDuty finding format — `https://docs.aws.amazon.com/guardduty/latest/ug/guardduty_finding-format.html`
- SOC + TI subsystem doc — `docs/codebase/06-soc-ti.md`
- Pipeline gap note — `docs/codebase/06-soc-ti.md` line 240 ("Enrichment is drift-only")
