# Threat Exposure — Design

> Spec for the **Threat Exposure** feature: matching live threat intel
> against the tenant's actual cloud posture. Companion to `CISOBrief-v2.md`
> (the v2 PRD — Threat Exposure is named there in §5, Appendix A, and §17).
>
> Date: 2026-05-20
> Status: design draft, awaiting KK review before the implementation plan.

---

## 1. What we are building

CISO question #3 (`CISOBrief-v2.md` §2.1): *"Given what's in my environment,
what threats and vulnerabilities should I care about today?"* — and #7:
*"If the board asks about a headline cloud breach, what's my one-paragraph
'are we exposed' answer?"*

Threat Exposure answers both. It is the third section of the **Brief**
screen (after Top Risks and Posture Diff) and a `/threats` route on the
web app.

The v2 PRD already commits the shape of this feature:

- §5 IA — Brief screen has a **Threat Exposure** section: *"CVEs and
  threat-actor activity matched to actual assets."*
- Appendix A — *"Given my environment, what threats matter? → Brief →
  Threat Exposure (Shasta `threat_intel` × assets)."*
- §17 — v1's raw KEV/NVD/EPSS ingestion is **retired**; v2 Threat Exposure
  *"uses the same upstream feeds but now joined against real assets."*

So the design question is not "add a feed." It is: make the feed
**effective**, which the product principles define precisely.

## 2. The principle this feature is judged against

`CISOBrief-v2.md` §3: **"Their environment is the relevance. We don't show
generic CVE feeds. Every item references a specific resource."** And §1.1:
the success metric is *"how often the CISO took an action they would not
have taken without us"* — not "how many findings did we surface."

v1 showed a generic KEV/NVD firehose and got sunset (§17). A raw feed is an
anti-feature here. **Effective threat intel = a deterministic join** of a
live threat signal against the tenant's real `findings`, producing
`"You are exposed to <actively-exploited attack pattern> via <these 4
specific resources>"` — never a CVE list.

## 3. The fork in the road — two flavors of "threats × your environment"

There are two ways to do the join, with very different feasibility:

| Flavor | What it produces | Feasible today? |
|---|---|---|
| **Technique-level** | "Internet-exposed remote access is being actively exploited; you have 4 security groups open on 22/3389." | **Yes** — joins a curated threat catalog against existing `findings`. No new inventory needed. |
| **CVE-level** | "CVE-2026-XXXX is in CISA KEV and you run the affected version." | **Not yet** — needs per-resource *version* inventory, which Shasta config scans don't produce. |

The honest blocker for CVE-level: Shasta produces config findings, not a
software bill of materials. The right source for version-level matching is
**AWS Inspector**, which already does CVE scanning on EC2/ECR/Lambda and is
already a real-time `events` source in `CISOBrief-v2.md` §9.1 — but
Inspector data is not yet flowing end-to-end (`HANDOFF.md`: alert pipeline
"wired but not end-to-end tested").

**Decision: ship technique-level first (TI-1), CVE-level second (TI-2).**
TI-1 is buildable from data we already have, it is CISO-grade (it answers
the board-breach question directly), and it is the demo that lands. TI-2
layers on once Inspector events are confirmed live.

This spec fully specifies **TI-1**. TI-2 is scoped in §11.

## 4. Invariants respected

Carried from the platform's working principles (`CLAUDE.md`) and the Denali
invariants already adopted for the AI slice
(`2026-05-18-ai-security-slice-1-design.md` §4):

1. **Determinism is the spine. AI is the surface.** The feed sync and the
   matcher are deterministic functions of (catalog version, KEV/EPSS
   snapshot, tenant findings). The LLM is used **only** to write the
   "why it matters" prose — never to decide an exposure, rank it, or match
   it. This mirrors `CISOBrief-v2.md` §3 ("Determinism first, LLM second").
2. **Every conclusion carries its evidence.** Each `threat_exposures` row
   carries an inline `evidence_packet` (the v0.1 schema from the AI slice
   spec §7) listing the exact findings and KEV entries that produced it.
3. **The apps never call upstream sources.** KEV/EPSS are pulled by a
   backend Lambda only (`CLAUDE.md`: "The iOS / web apps never call
   upstream sources. Only the API Gateway.").
4. **Open by default.** The threat-technique catalog is a versioned,
   in-repo JSON file designed to be readable as a standalone artefact.
5. **Read-only.** No action is taken against any customer environment;
   Threat Exposure is pure surfacing.

## 5. Demo target

> **KK opens the Brief. The Threat Exposure section reads "You are exposed
> to 3 actively-exploited attack patterns." The top card: "Internet-exposed
> remote access — 4 security groups allow 0.0.0.0/0 on ports 22/3389.
> 2 remote-access CVEs were added to CISA KEV in the last 30 days; both are
> flagged as used in ransomware campaigns." He taps it: a why-it-matters
> paragraph, the 4 specific security-group ARNs (each a link into the
> finding), and the named KEV CVEs with their EPSS scores. He asks the
> voice interface "what threats should I care about today?" and gets a
> two-sentence answer naming the technique and one resource.**

No raw CVE list anywhere. No NVD browser. No per-CVE cards in TI-1 — those
are TI-2, gated on Inspector.

## 6. Architecture overview

A new pipeline parallel to the scan pipeline: a nightly **feed sync**, a
per-tenant **matcher**, and a read API. No new datastore — reuses the
Aurora cluster, the `findings` table, the `llm_cache` table, the API
Gateway, and the CORS/gateway-response patterns.

```
Upstream (backend-only):
  CISA KEV JSON  ──┐
  EPSS API       ──┤
                   ▼
  EventBridge cron (nightly)
    → threat_feed_sync Lambda
        • fetch KEV (~1.3k entries) + EPSS score per KEV CVE
        • tag each entry with technique_keys via the in-repo catalog
        • upsert threat_feed   (tenant-independent)
        • fan out one threat-match-queue message per active tenant

  Scan completes (shasta_runner, existing):
    → on scans.status='completed', enqueue one threat-match-queue message
      for that tenant

  threat-match-queue (SQS) → threat_match Lambda (per tenant):
    • load the technique catalog
    • join catalog.check_ids  ×  open findings   → exposed resources
    • join catalog.kev_match  ×  threat_feed      → activity weight
    • compute rank_score (deterministic)
    • upsert threat_exposures rows + inline evidence_packet
    • fire-and-forget LLM narrative render (waitUntil-style)

  Read path:
    GET /v1/threats        → ranked exposures for the Brief
    GET /v1/threats/{id}   → one exposure + evidence + matched findings
    voice tool get_threat_exposure()
    → web Brief "Threat Exposure" section + /threats route
    → iOS Brief "Threat Exposure" section
```

New: one SQL migration, one in-repo catalog file, one SQS queue, one cron
rule, three Lambdas (`threat_feed_sync`, `threat_match`, `threats_api`),
one web route + one Brief section, one iOS Brief section, one voice tool.

## 7. The threat-technique catalog

A versioned JSON file in the repo — `platform/threat/techniques.json` —
not a table. Same pattern as the policy templates and questionnaire banks
already lifted from Shasta (`HANDOFF.md`). It is the deterministic mapping
between a real-world attack pattern and (a) the Shasta checks that say a
tenant is exposed and (b) the KEV entries that say the pattern is hot.

```json
{
  "version": "0.1.0",
  "techniques": [
    {
      "technique_key": "exposed_remote_access",
      "title": "Internet-exposed remote access (SSH/RDP)",
      "description": "Security groups or firewall rules allow 0.0.0.0/0 to reach SSH (22) or RDP (3389).",
      "severity_floor": "high",
      "check_ids": [
        "ec2_security_group_ingress_open_to_world_ssh",
        "ec2_security_group_ingress_open_to_world_rdp"
      ],
      "event_match": { "sources": ["cloudtrail"],
                       "actions": ["AuthorizeSecurityGroupIngress"] },
      "kev_match": { "keywords": ["remote desktop", "RDP", "OpenSSH", "SSH"],
                     "products": ["Windows", "OpenSSH"] },
      "actor_context": "A primary ransomware initial-access vector; brokered by initial-access brokers and used by most major affiliates.",
      "mitre_attack": ["T1133", "T1190"]
    }
  ]
}
```

Field rules:

- `check_ids` — **must be verified against the live Shasta check library
  at build time.** A CI test asserts every `check_id` in the catalog
  exists in Shasta; an unknown id fails the build (prevents a technique
  silently never matching). See §9.4.
- `kev_match` — used by `threat_feed_sync` to tag KEV entries. A KEV entry
  is tagged with this `technique_key` if any `keyword` is a
  case-insensitive substring of the entry's `vulnerabilityName`/`shortDescription`
  **or** any `product` matches the entry's `product`/`vendorProject`.
  Matching is deterministic and recorded in the evidence packet.
- `event_match` — optional; lets a recent real-time `events` row (drift /
  alert) raise a technique's activity even with no fresh KEV (TI-1.1, see
  §11). Defined now so the schema is stable.
- `severity_floor` — the exposure's severity is `max(severity_floor,
  highest matched finding severity)`.

TI-1 ships **~15–20 techniques** covering the highest-signal cloud attack
patterns: exposed remote access, public object storage, public-facing
databases, public disk snapshots/AMIs, root/admin without MFA,
over-permissive IAM (wildcard admin), public Elasticsearch/Redis/Mongo,
exposed Kubernetes API, plaintext secrets in IAM/SSM, disabled logging
(detection-evasion exposure), unencrypted regulated data stores. The exact
set is finalised against the Shasta check library when the catalog is
written (decision in §10).

**Catalog source:** seed from Shasta's `threat_intel` module where it
already maps techniques to checks; hand-curate the rest. The catalog is
the spec — it ships in the repo and is reviewable in a PR.

## 8. Data model

One SQL migration: `platform/sql/006_threat_exposure.sql`. Two tables —
one tenant-independent (the feed), one tenant-scoped (the matches).

### 8.1 `threat_feed` — external intel, tenant-independent

Refreshed wholesale by the nightly cron. No `tenant_id` — same status as
the existing `llm_cache` table, which is also tenant-independent for
shared prompts.

```sql
CREATE TABLE threat_feed (
  id                 UUID         PRIMARY KEY,
  source             TEXT         NOT NULL CHECK (source IN ('cisa_kev')),
  cve_id             TEXT         NOT NULL,
  title              TEXT         NOT NULL,
  description        TEXT,
  vendor_project     TEXT,
  product            TEXT,
  kev_date_added     DATE,
  kev_due_date       DATE,
  known_ransomware   BOOLEAN      NOT NULL DEFAULT false,
  epss_score         NUMERIC(6,5),                       -- 0..1, NULL if EPSS lookup failed
  technique_keys     JSONB        NOT NULL DEFAULT '[]'::jsonb,  -- tagged at ingest
  catalog_version    TEXT         NOT NULL,               -- catalog used for the tagging
  raw                JSONB        NOT NULL,
  first_fetched_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  last_fetched_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  UNIQUE (source, cve_id)
);

CREATE INDEX threat_feed_added_idx ON threat_feed(kev_date_added DESC);
CREATE INDEX threat_feed_technique_idx ON threat_feed USING GIN (technique_keys);
```

Volume: CISA KEV is ~1,300 entries total — the whole feed fits in one
table with no pruning. EPSS as a standalone feed is ~250k rows; we do
**not** store it standalone — we look up the EPSS score only for CVEs that
are in KEV and store it on the row. That keeps `threat_feed` at ~1.3k rows.

### 8.2 `threat_exposures` — per-tenant match results

Upserted by the matcher; one row per (tenant, technique). Rewritten on
every matcher run — it is a materialised view, not an append log.

```sql
CREATE TABLE threat_exposures (
  id                 UUID         PRIMARY KEY,
  tenant_id          UUID         NOT NULL REFERENCES tenants(tenant_id),
  technique_key      TEXT         NOT NULL,
  title              TEXT         NOT NULL,
  severity           TEXT         NOT NULL,               -- critical|high|medium|low
  exposure_count     INT          NOT NULL DEFAULT 0,     -- # open findings matched
  exposed_resources  JSONB        NOT NULL DEFAULT '[]'::jsonb,  -- [resource_arn, ...]
  finding_ids        JSONB        NOT NULL DEFAULT '[]'::jsonb,
  active_kev_cves    JSONB        NOT NULL DEFAULT '[]'::jsonb,  -- [{cve_id, epss, ransomware, added}]
  activity_weight    NUMERIC      NOT NULL DEFAULT 0,     -- KEV/EPSS-derived, deterministic
  rank_score         NUMERIC      NOT NULL DEFAULT 0,
  narrative          TEXT,                                 -- LLM "why it matters", NULL until rendered
  narrative_model    TEXT,                                 -- model+version, NULL until rendered
  evidence_packet    JSONB        NOT NULL,
  catalog_version    TEXT         NOT NULL,
  computed_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  UNIQUE (tenant_id, technique_key)
);

CREATE INDEX threat_exposures_tenant_rank_idx
  ON threat_exposures(tenant_id, rank_score DESC);
```

A technique with `exposure_count = 0` is **deleted**, not stored — the
Brief shows only what the tenant is actually exposed to. `evidence_packet`
reuses the v0.1 schema (`2026-05-18-ai-security-slice-1-design.md` §7):
`detector.id = "threat.matcher"`, `source_events` lists the matched
finding rows and KEV entries, `reasoning_chain` records the join, `model`
is `null` (the matcher is deterministic; the narrative is rendered
separately and is not part of the packet).

## 9. Pipeline components

### 9.1 `threat_feed_sync` Lambda (cron)

- New EventBridge cron rule, nightly (`cron(0 6 * * ? *)` — 06:00 UTC,
  before the daily scan window). Added to `events-stack.ts` — it already
  owns the ingestion-and-fan-out constructs; this is the first scheduled
  rule in the stack (`scan-stack.ts` notes a nightly scan cron as "later"
  — unrelated, keep separate).
- Python Lambda, stdlib `urllib` only (no SDK dep — same discipline as
  `lambda/policies/anthropic_call.py`).
- Steps:
  1. `GET https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json`.
  2. For each KEV entry, look up EPSS:
     `GET https://api.first.org/data/v1/epss?cve=<batched cve ids>`
     (FIRST's EPSS API supports comma-batched CVE queries; chunk at 100).
     EPSS lookup failure on a CVE → `epss_score = NULL`, not a hard fail.
  3. Load `platform/threat/techniques.json`; tag each KEV entry's
     `technique_keys` per the `kev_match` rules (§7).
  4. Upsert all rows into `threat_feed` (`ON CONFLICT (source, cve_id)`
     → update score, technique_keys, `last_fetched_at`).
  5. `SELECT tenant_id FROM tenants WHERE status = 'approved'`; enqueue
     one `threat-match-queue` message per tenant `{tenant_id, reason:'feed_refresh'}`.
- Idempotent: a re-run produces the same `threat_feed` state.
- Network: KEV/EPSS are public HTTPS — the Lambda needs egress (NAT or a
  public-subnet placement consistent with the other outbound Lambdas).

### 9.2 `threat_match` Lambda (SQS consumer)

- Triggered by `threat-match-queue` (SQS standard; DLQ `threat-match-dlq`,
  `maxReceiveCount=3`, `batchSize=1`). Producers: `threat_feed_sync`
  (one msg/tenant nightly) and `shasta_runner` on scan completion.
- Per message (one tenant):
  1. `SET app.tenant_id` from the message (RLS); load the catalog.
  2. For each technique: `SELECT` open `findings`
     (`status='fail' AND resolved_at IS NULL`) whose `check_id` is in the
     technique's `check_ids` → `exposed_resources`, `finding_ids`,
     `exposure_count`, severity.
  3. `active_kev_cves` = `threat_feed` rows whose `technique_keys`
     contains this `technique_key` and `kev_date_added` within 365 days.
  4. `activity_weight` (deterministic):
     `Σ over active KEV cves of [ (epss_score or 0.1)
        × ransomware_bonus(1.5 if known_ransomware else 1.0)
        × recency_decay(added_within_30d:1.0, 90d:0.7, 365d:0.4) ]`.
  5. `rank_score = severity_weight(severity)
        × log10(1 + exposure_count)
        × (1 + activity_weight)` — pure function, no randomness.
  6. Upsert `threat_exposures`; delete rows with `exposure_count = 0`;
     build `evidence_packet`.
  7. For each upserted row whose `narrative` is stale (catalog or
     exposure-set changed): enqueue/inline the LLM render (§9.3). Do not
     block the matcher on it.
- The whole upsert+delete for a tenant runs in one transaction.

### 9.3 Narrative rendering — the only LLM in the pipeline

A **sixth** LLM prompt type, alongside the five in `CISOBrief-v2.md` §11:

- `threat-why-it-matters` — input: technique title + description +
  `actor_context`, `exposure_count`, sample exposed resource identifiers,
  the active KEV CVE ids, tenant sector. Output: 2–3 plain sentences,
  no hype, naming the resource count and the action to take.
- Model: `claude-sonnet-4-6` via the existing `anthropic_call.py` helper
  (duplicate it into `lambda/threat_match/` — the project keeps these
  per-Lambda, not a layer, per `HANDOFF.md`).
- Cached in `llm_cache`, key `threat:<tenant_id>:<technique_key>#why`,
  invalidated when `exposure_count` or `active_kev_cves` changes.
- The narrative is **prose only**. It never changes `severity`,
  `rank_score`, `exposure_count`, or which findings matched — those are
  all set by the deterministic matcher before the LLM is called. This is
  the §3 / §4.1 invariant made concrete.

### 9.4 `threats_api` Lambda

| Method | Path | Returns |
|---|---|---|
| `GET` | `/v1/threats` | ranked `[{id, technique_key, title, severity, exposure_count, active_kev_count, rank_score, narrative, computed_at}, ...]` |
| `GET` | `/v1/threats/{id}` | one row + full `exposed_resources` (joined to finding title/severity) + `active_kev_cves` + `evidence_packet` |

Cognito-JWT-authenticated; tenant from the JWT, never the client. CORS +
gateway-response patterns match existing endpoints. Added to
`api-stack.ts`.

## 10. UI

### 10.1 Web

- **Brief / Welcome — "Threat Exposure" section** (new, below Posture
  Diff per `CISOBrief-v2.md` §5). Header: *"You are exposed to N
  actively-exploited attack patterns."* Top 3 exposure cards: title,
  severity pill, `"<exposure_count> resources"`, a one-line activity
  badge (`"2 KEV CVEs in 30d · ransomware"`), the narrative. "See all" →
  `/threats`.
- **`/threats` route** (`ThreatExposure.tsx`): full ranked list. Each row
  expands to the matched resource ARNs (each linking to the finding
  detail) and the active KEV CVEs with EPSS scores. Empty state: *"No
  active threat exposure matched your current findings."*

### 10.2 iOS

- **Brief — "Threat Exposure" section**: a `Section` listing the top
  exposures from `GET /v1/threats`; tap → detail screen with narrative,
  exposed-resource list (each pushing the existing finding detail), KEV
  CVE list. Read-only, consistent with the iOS Brief.

### 10.3 Voice

- New tool `get_threat_exposure(limit?)` in `voice_session` → `GET /v1/threats`.
  Maps to CISO question #3. The §12.3 system prompt already forbids
  speculation; the tool returns only matched exposures, so the model
  answers "you are exposed to X via N resources" or "nothing matched."

## 11. Out of scope for TI-1 (explicit)

- **TI-2 — CVE-level matching.** Per-CVE exposure cards joining KEV/EPSS
  CVE ids directly against **AWS Inspector** `events`. Deferred until the
  Inspector real-time path is confirmed end-to-end (`HANDOFF.md` lists the
  alert pipeline as wired-but-untested). TI-2 reuses `threat_feed` as-is
  and adds a CVE-level branch to the matcher; no schema change to
  `threat_feed`.
- **TI-1.1 — drift-driven activity.** Using recent `events` rows
  (`event_match` in the catalog, already in the schema) to raise a
  technique's `activity_weight` when a real-time drift event matches.
- **Posture Diff integration** — surfacing "a new technique became
  hot since yesterday" in the Brief's Posture Diff. Future.
- **Push notifications** on a newly-hot exposure — the §13 push engine is
  AWS-event-driven; threat-feed-driven pushes are a separate rule family,
  later.
- **NVD / MITRE ATT&CK / vendor advisory feeds** — TI-1 is KEV + EPSS
  only. KEV is the highest-signal feed (actively exploited, government-
  curated); adding more feeds before TI-1 is proven repeats the v1 mistake.
- **Non-AWS techniques** — the TI-1 catalog targets the AWS check library
  (the only cloud with findings at volume per `HANDOFF.md`). Azure/GCP/
  Entra technique entries are added as those check sets mature.
- **KMS-signed evidence packets** — `signature` stays `null`, same as the
  AI slice.

## 12. Cross-cutting

### 12.1 Feature flag

- API env var `THREAT_EXPOSURE_ENABLED=true|false` gates `/v1/threats*`
  (404 when off) — mirrors the AI slice's `AI_FEATURES_ENABLED`.
- Web reads the existing `/v1/config` endpoint and hides the Threat
  Exposure section + `/threats` route + voice tool when off.
- Default off in prod until the matcher is verified on KK's tenant.

### 12.2 Cost / capacity

- `threat_feed`: ~1.3k rows. `threat_exposures`: ~15–20 rows/tenant.
  Negligible Aurora footprint.
- `threat_feed_sync`: one invocation/night; ~1.3k KEV entries + ~13 EPSS
  batch calls. Well under a minute.
- `threat_match`: one invocation per tenant per scan + per night. At
  current scale (~3 tenants) trivial.
- LLM: ~15 short Sonnet calls per tenant when exposures change, cached.

### 12.3 Security

- KEV/EPSS fetched server-side only; the apps never see upstream.
- `threat_feed` is tenant-independent and contains only public data — no
  RLS needed (consistent with `llm_cache`).
- `threat_exposures` is tenant-scoped; every API query is
  `WHERE tenant_id = $1` from the JWT.
- The matcher only reads `findings`/`threat_feed` and writes
  `threat_exposures` for the one tenant in the SQS message.

### 12.4 Tests

- **Catalog validation (load-bearing):** CI test asserts every `check_id`
  in `techniques.json` exists in the live Shasta check library, and the
  JSON validates against a schema. Unknown id → build fails.
- **`threat_feed_sync`:** unit test with a recorded KEV JSON fixture +
  stubbed EPSS responses → assert `threat_feed` rows + technique tagging.
- **`threat_match`:** golden test — fixture catalog + fixture `findings` +
  fixture `threat_feed` → assert `threat_exposures` rows, `rank_score`,
  `evidence_packet`, and that `exposure_count=0` techniques are absent.
  `rank_score` is a pure function — assert it byte-for-byte.
- **API integration:** under `platform/tests/api/`, existing pattern.
- **E2E (manual):** run the matcher against KK's real tenant (~480
  findings across 3 clouds) and eyeball the Brief.

### 12.5 Decision log

| Q | Recommendation | Decide by |
|---|---|---|
| Final TI-1 technique set + `check_id` mapping | Write `techniques.json` against the live Shasta check library; ~15–20 techniques | Before the catalog file is written |
| Seed catalog from Shasta `threat_intel` module vs. fully hand-curate | Seed where Shasta maps techniques→checks; hand-curate the rest | Catalog authoring |
| EPSS source — FIRST API vs. bulk CSV | FIRST API, batched by CVE (only ~1.3k CVEs needed) | `threat_feed_sync` build |
| `severity_weight` / `recency_decay` constants | Tune on KK's real findings before flag-on | Before prod flag-on |
| Matcher trigger from `shasta_runner` — direct async invoke vs. SQS enqueue | SQS enqueue (one queue, one consumer, retries/DLQ for free) | `scan` wiring |

## 13. Sequencing

| Step | Deliverable |
|---|---|
| 1 | `006_threat_exposure.sql` migration + `platform/threat/techniques.json` (~15–20 techniques) + catalog-validation CI test |
| 2 | `threat_feed_sync` Lambda + nightly cron + `threat-match-queue`/DLQ in `events-stack.ts` |
| 3 | `threat_match` Lambda + `anthropic_call.py` narrative render; `shasta_runner` enqueues a match message on scan completion |
| 4 | `threats_api` Lambda + `/v1/threats*` routes in `api-stack.ts` |
| 5 | Web — Brief "Threat Exposure" section + `/threats` route |
| 6 | iOS — Brief "Threat Exposure" section |
| 7 | Voice — `get_threat_exposure` tool in `voice_session` |
| 8 | Verify on KK's tenant, tune ranking constants, flip `THREAT_EXPOSURE_ENABLED` on |

Estimate ≈6–8 days for TI-1. TI-2 (CVE-level via Inspector) is a separate
spec, written once the Inspector real-time path is confirmed live.

---

*Spec ends here. Implementation plan is written separately once this spec
is approved.*
