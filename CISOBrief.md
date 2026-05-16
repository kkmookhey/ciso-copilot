# Sentinel Brief — PRD v0.2

**A daily threat & vulnerability briefing for CISOs, filtered through their own tech stack.**

| | |
|---|---|
| **Document type** | Product Requirements Document — build-from spec for iOS v1 |
| **Audience** | Claude Code (executor) + YouTube viewers (observers) |
| **Version** | 0.2 — Draft for live build |
| **Author** | KK Mookhey |
| **Date** | May 16, 2026 |
| **Platform** | iOS (SwiftUI) + Cloudflare Workers + AWS Lambda |
| **Primary persona** | Jennifer Chen — Deputy CISO, US mid-market |
| **Demo length target** | ~75 min end-to-end on YouTube |

## How to read this document

This PRD is written to be executable. Sections 1–3 explain the product. Sections 4–10 are the build spec: scope, screens, data sources, architecture, data model, and the order to build in. Sections 11–13 cover non-functional requirements, what success looks like, and what's deliberately not in v1.

Sections marked `[BUILD]` are executable detail. Everything else is context.

---

## 1. The product in one paragraph

Sentinel Brief is an iOS app that delivers a personalized, 5-minute daily security briefing to a CISO based on the tech stack they configure once. It pulls from public sources (CISA KEV, NVD, EPSS, vendor advisories), filters to what touches the user's stack, ranks by exploit signal, and pushes a brief plus out-of-band alerts. Every priority item generates three things: a "why this matters to you" explanation, a board-ready paragraph, and a set of questions the CISO can copy and send to their Infrastructure / SOC / VulnMgmt teams. The user marks items useful or not; that feedback persists for v2 tuning. v1 ships with brief + alerts + Ask My Team + board paragraph + feedback. Phase 2 adds an Attack Surface Management module.

### 1.1 Why this is worth building (and watching)

Every CISO already drinks from the firehose of CVEs, advisories, and threat intel. None of it is filtered to their actual environment. The unsolved problem isn't "more data" — it's relevance. A product that knows the CISO runs Okta + CrowdStrike + AWS + Microsoft 365 can ignore 90% of the noise and surface the 10% that matters. That's the wedge.

For the demo audience: this is a real cybersecurity app built on real public data sources. Nothing is mocked. By the end of the build, viewers should be able to install the app on their own phone, enter their own stack, and get a real brief tomorrow morning. That's the contract.

### 1.2 The positioning, stated bluntly

**Not** "a daily cybersecurity newsletter for CISOs" — that exists.
**Yes** "a daily, environment-aware CISO briefing that tells me what changed, what matters to us, and what I should ask my team to do today."

The difference matters because it changes what we measure success on. We don't optimize for "how many items did we surface." We optimize for "how often did the CISO act on what we surfaced."

---

## 2. Target user

### 2.1 Primary — Jennifer Chen

Deputy CISO at a US mid-market company. ~5,000 employees. Hybrid AWS + Azure. Microsoft 365, Okta, CrowdStrike, Splunk. Reports to a CIO. Security team of 8. Reads on phone first, laptop second. Has roughly 15 useful minutes between waking up and the first meeting. Will delete the app after one false-positive alert.

### 2.2 The four questions she needs answered every morning

1. What broke in the world overnight that affects MY stack?
2. What should my team be working on today that they aren't?
3. If the board asks about a headline incident, what's my one-paragraph answer?
4. What's the single thing I should personally escalate?

The product is judged by how well a 5-minute scroll answers these four. No other metric matters in v1.

---

## 3. Product principles

| Principle | What it means in practice |
|---|---|
| **Relevance over completeness** | Hide things that don't apply, even if interesting. A short, relevant brief beats a comprehensive one. |
| **Show the reasoning** | Every item shows why it's in the brief — which stack component matched, which source, what confidence. |
| **Push, don't pull** | Brief arrives via push notification. The app is the secondary surface, not the primary. |
| **Quiet by default** | Out-of-band alerts have a hard cap (default: 2/week). Above that, the model is wrong, not the world. |
| **Trust is earned through feedback** | Every item gets thumbs up/down. Below 70% useful rate, the ranking is broken — fix it, don't ship more features. |
| **No accounts on day one** | First run: enter stack, get brief tomorrow. Email is optional. Account creation is a v1.5 problem. |
| **Determinism first, LLM second** | Matching is deterministic (CPE + keyword). LLMs are used only for prose generation (explanations, board paragraphs, team questions) — never to decide relevance. |

---

## 4. `[BUILD]` Scope for v1

### 4.1 In scope

- Stack profile setup wizard (multi-select chips across 8 categories).
- Daily brief screen — items ranked by relevance × exploit signal, classified as **Act Now / Check Today / Watch / FYI**.
- Per-item detail screen with:
  - "Why this matters to you" — full-prose explanation (LLM-generated, cached).
  - "Ask my team" — 3 questions each for Infra, SOC, VulnMgmt with copy buttons.
  - "Board paragraph" — one-paragraph executive summary with copy button.
  - Sources (linked).
  - Matched stack components (chips).
  - CVSS, EPSS, KEV status badges.
- Push notifications for Act Now items, rate-limited (default 2/week).
- Thumbs up/down feedback on every item, optional "why" free-text on thumbs down.
- Profile edit (change stack anytime).
- History view (last 14 days of briefs).

### 4.2 Out of scope for v1

- User accounts, login, multi-device sync (local-only in v1; cloud sync v1.5).
- Android. iOS first; Android after iOS validates.
- Email or web dashboard delivery — push only in v1.
- Attack Surface Management — Phase 2, designed in but not built.
- Sharing / team features beyond copy buttons.
- Paid tier, billing, monetization.
- Integrations with vuln scanners, SIEMs, EDRs — v2.
- Feedback-driven learning loop — captured in v1, used in v2.
- Multi-audience briefing variants (board / SOC / infra) — v2. v1 ships one CISO version + per-item team questions.

### 4.3 The demo contract

By the end of the YouTube build, the following has to work on a real iPhone simulator with real data:

1. Launch app → onboarding wizard → enter stack profile.
2. App fetches real data from CISA KEV and NVD via Cloudflare Worker.
3. Brief screen shows ranked items filtered to the entered stack, classified Act Now / Check Today / Watch / FYI.
4. Tap a priority item → see LLM-generated "why this matters", Ask-my-team questions, board paragraph.
5. Thumbs up/down persists locally and posts to backend.
6. Push notification fires when a new KEV entry matches the stack.

---

## 5. `[BUILD]` Screens & information architecture

Five screens in v1. That's it. Adding a sixth requires deleting one.

| Screen | Purpose & contents |
|---|---|
| **A. Onboarding wizard** | First-launch flow. 6 screens of chip-selectors (cloud, identity, EDR, SIEM, key SaaS, regulated data, sector, employee band). Skippable per screen. Final screen: "Generate my first brief" button. |
| **B. Today's Brief** | Default home. Sections: **Act Now** (red, top), **Check Today** (orange), **Watch** (yellow), **FYI** (grey). Each row: title, severity chip, one-line summary, thumbs feedback. Pull to refresh. |
| **C. Item detail** | Header with severity label. Tabs or sections: **Why this matters** (prose) · **Ask my team** (3 chips: Infra/SOC/VulnMgmt, each expands to 3 questions with copy buttons) · **Board paragraph** (copy button) · **Details** (CVSS, EPSS, KEV badge, affected products, sources). |
| **D. Profile** | Current stack (editable chips), notification preferences (severity threshold, quiet hours, weekly alert budget), data sources status indicators. |
| **E. History** | Last 14 days. Tap a date → that day's brief. Filter by thumbs-up only. |

### 5.1 Severity classification

| Label | Color | Criteria |
|---|---|---|
| **Act Now** | Red | KEV-listed AND matches user stack with high confidence (vendor + product) |
| **Check Today** | Orange | EPSS ≥ 0.7 AND matches stack with high confidence, OR KEV-listed with medium confidence match |
| **Watch** | Yellow | Stack match (any confidence) + CVSS ≥ 7.0, no exploitation evidence yet |
| **FYI** | Grey | Sector or geography match only; no direct stack match |

### 5.2 Navigation

- Tab bar: **Brief** (default) · **History** · **Profile**.
- Onboarding is modal, shown once, dismissible to Brief after completion.
- Item detail is a push from Brief or History.

---

## 6. `[BUILD]` Data sources

v1 uses only free, public, no-auth (or free-key) sources. The demo viewer should be able to replicate the build without paying for anything.

| Source | Endpoint | Role in v1 |
|---|---|---|
| **CISA KEV** | `https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json` | JSON pull, no auth, CC0. Primary exploitation signal — KEV inclusion = active exploitation. |
| **NVD CVE API 2.0** | `https://services.nvd.nist.gov/rest/json/cves/2.0` | Free API key (50 req / 30s). Use `lastModStartDate` / `lastModEndDate` for incremental sync. Source of CVE descriptions, CVSS, CPE matches. |
| **EPSS (FIRST)** | `https://epss.empiricalsecurity.com/epss_scores-current.csv.gz` | Free, no auth. Daily CSV with probability scores per CVE. Used in ranking. |
| **CISA Advisories RSS** | `https://www.cisa.gov/cybersecurity-advisories/all.xml` | ICS advisories, alerts, AAs. RSS, no auth. Used for "Watch" items. |
| **Vendor RSS (top 5 only in v1)** | Microsoft MSRC, AWS Security Bulletins, Okta Trust, Cisco PSIRT, Citrix Security | Hardcoded list. Filtered per user's stack. |

### 6.1 Rate limits and politeness

- **KEV:** poll once per hour from the Cloudflare Cron worker. Static URL, ~5MB JSON.
- **NVD:** API key required (free, request at `nvd.nist.gov/developers/request-an-api-key`). 50 req / 30s with key. Incremental sync via `lastModStartDate` every 2h.
- **EPSS:** bulk daily CSV download. One pull per day. Cheaper than per-CVE queries.
- **All RSS:** poll every 30 min. Cache aggressively.

Critical: the iOS app never calls these sources directly. All data flows through the Cloudflare Worker, which is the only thing rate-limited. The app talks to our backend only.

---

## 7. `[BUILD]` Architecture

Three layers: a SwiftUI iOS app, a Cloudflare edge layer (Workers + R2 + Cron + D1), and an AWS compute layer (Lambda + Bedrock + DynamoDB). Cloudflare handles ingestion, storage of raw feeds, and the API. AWS handles the heavy work — matching, ranking, and LLM prose generation.

### 7.1 Component diagram

```
                          ┌──────────────────────────┐
                          │     Public Sources       │
                          │  KEV · NVD · EPSS · RSS  │
                          └────────────┬─────────────┘
                                       │
                          ┌────────────▼─────────────┐
                          │  Cloudflare Cron Workers │
                          │  (hourly KEV, 2h NVD,    │
                          │   daily EPSS, 30m RSS)   │
                          └────────────┬─────────────┘
                                       │
                          ┌────────────▼─────────────┐
                          │ Cloudflare R2 (raw blobs)│
                          │ Cloudflare D1 (parsed)   │
                          └────────────┬─────────────┘
                                       │
       ┌───────────────┐  HTTPS        │
       │   iOS App     │◄──────────────┤
       │   (SwiftUI)   │  GET /brief   │
       │               │               ▼
       │               │   ┌──────────────────────┐
       │               │   │ Cloudflare Worker    │
       │               │──►│ (API gateway)        │
       │               │   │ POST /brief          │
       └───────┬───────┘   │ POST /feedback       │
               │           │ POST /register-token │
               │           └──────────┬───────────┘
               │                      │ (invoke)
               │                      ▼
               │           ┌──────────────────────┐
               │           │  AWS Lambda          │
               │           │  - Matcher           │
               │           │  - Ranker            │
               │           │  - Bedrock caller    │
               │           └──────────┬───────────┘
               │                      │
               │           ┌──────────▼───────────┐
               │           │  Amazon Bedrock      │
               │           │  (Claude Sonnet 4.6) │
               │           │  - Why-it-matters    │
               │           │  - Board paragraph   │
               │           │  - Team questions    │
               │           └──────────┬───────────┘
               │                      │
               │           ┌──────────▼───────────┐
               │           │  DynamoDB            │
               │           │  - users             │
               │           │  - briefs            │
               │           │  - feedback          │
               │           │  - llm_cache         │
               │           └──────────────────────┘
               │
               │  APNs push
               ▼
       ┌───────────────┐
       │  Apple APNs   │◄───── triggered by Lambda when new KEV match
       └───────────────┘
```

### 7.2 Cloudflare layer

- **Cloudflare Workers** — API gateway the iOS app talks to. Endpoints: `POST /brief`, `POST /feedback`, `POST /register-token`, `GET /history`. Sub-50ms response times, no cold starts.
- **Cloudflare Cron Triggers** — scheduled ingestion. One Worker per source: `cron-kev` (hourly), `cron-nvd` (every 2h), `cron-epss` (daily 06:00 UTC), `cron-rss` (every 30 min).
- **Cloudflare R2** — raw blob storage for the JSON/CSV dumps from each source. S3-compatible, zero egress fees. Each pull is timestamped so we can audit what we saw when.
- **Cloudflare D1 (SQLite at the edge)** — parsed and normalized CVE / KEV / EPSS / advisory data. The matcher Lambda reads from here over HTTPS via the Worker.

### 7.3 AWS layer

- **AWS Lambda (Python 3.12)** — single function `match-and-rank`. Invoked by the Worker. Takes a stack profile, reads relevant CVEs from D1, runs the matching algorithm, calls Bedrock for prose, writes the brief to DynamoDB, returns it.
- **Amazon Bedrock** — Claude Sonnet 4.6 (`anthropic.claude-sonnet-4-6-v1`). Three prompts:
  1. `why-it-matters` — given a CVE + user stack, write a 2–3 sentence explanation tied to the stack.
  2. `board-paragraph` — given a CVE, write one paragraph the CISO can paste into a board update.
  3. `team-questions` — given a CVE + user stack, write 3 questions each for Infra/SOC/VulnMgmt.
- **DynamoDB** — single-table design. Partition key `PK`, sort key `SK`. Entities: `USER#<id>`, `BRIEF#<date>`, `FEEDBACK#<itemId>`, `LLMCACHE#<cveId>#<promptType>`. The LLM cache is critical — never call Bedrock twice for the same CVE + prompt; cache by CVE ID and last-modified timestamp.

### 7.4 iOS app

- **Language:** Swift 5.10+, SwiftUI.
- **Minimum iOS:** 17.0.
- **Storage:** SwiftData for local cache (briefs, feedback queue, profile). Profile is also POSTed to backend.
- **Networking:** URLSession with async/await. No Alamofire.
- **Architecture pattern:** MVVM with `@Observable` view models.
- **Push:** `UNUserNotificationCenter`. Backend sends device token at first launch + on every change.

### 7.5 The matching algorithm

Deliberately simple. Deterministic. Readable in 30 seconds. No ML, no embeddings in v1.

1. **Tokenize user stack** into normalized vendor/product pairs (e.g., "Okta" → `vendor:okta`, "Microsoft 365" → `vendor:microsoft`, `product:office365`).
2. **For each CVE**, extract vendor/product from CPE strings. If no CPE, fall back to keyword match against description (lower confidence).
3. **Match types:**
   - **High confidence** = vendor hit + product hit
   - **Medium confidence** = vendor hit only
   - **Low confidence** = sector or geography keyword match
4. **Relevance score** = `match_confidence × (0.4 × CVSS_normalized + 0.3 × EPSS + 0.3 × KEV_boost)`
   - `KEV_boost` = 1.0 if in KEV catalog, else 0
   - `CVSS_normalized` = CVSS / 10
   - `EPSS` = raw EPSS probability (0–1)
5. **Severity classification** per §5.1 thresholds.
6. **Top 10 items** shown in brief; **Act Now items** also generate push notifications (rate-limited to 2/week).

### 7.6 Why this architecture for the demo

- **Cloudflare Workers + Cron** is the cleanest "I deployed a serverless ingestion pipeline in 3 commands" beat on camera.
- **AWS Lambda + Bedrock** is the cleanest way to show real LLM calls happening server-side (not from the device) without leaking API keys.
- **R2 + D1 + DynamoDB** split is honest: R2 for raw archives, D1 for queryable structured data, DynamoDB for user data that needs single-digit-ms latency.
- **Both clouds working together** is more impressive on camera than one monolith, and reflects real-world architecture decisions.

---

## 8. `[BUILD]` Data model

### 8.1 Cloudflare D1 (SQLite)

```sql
CREATE TABLE cves (
  cve_id TEXT PRIMARY KEY,
  description TEXT,
  cvss_score REAL,
  cvss_vector TEXT,
  published_at TEXT,
  last_modified TEXT,
  cpe_matches TEXT,  -- JSON array
  vendors TEXT,      -- JSON array, lowercase
  products TEXT      -- JSON array, lowercase
);

CREATE TABLE kev (
  cve_id TEXT PRIMARY KEY REFERENCES cves(cve_id),
  date_added TEXT,
  due_date TEXT,
  ransomware_use INTEGER,  -- 0/1
  required_action TEXT
);

CREATE TABLE epss (
  cve_id TEXT PRIMARY KEY REFERENCES cves(cve_id),
  score REAL,
  percentile REAL,
  date TEXT
);

CREATE TABLE advisories (
  id TEXT PRIMARY KEY,
  source TEXT,
  title TEXT,
  summary TEXT,
  url TEXT,
  published_at TEXT,
  vendors TEXT  -- JSON array
);

CREATE INDEX idx_cves_vendors ON cves(vendors);
CREATE INDEX idx_cves_modified ON cves(last_modified);
CREATE INDEX idx_kev_date ON kev(date_added);
```

### 8.2 DynamoDB (single-table)

| PK | SK | Attributes |
|---|---|---|
| `USER#<deviceId>` | `PROFILE` | stack JSON, prefs JSON, device_token, created_at |
| `USER#<deviceId>` | `BRIEF#<YYYY-MM-DD>` | items JSON, generated_at |
| `USER#<deviceId>` | `FEEDBACK#<itemId>#<timestamp>` | sentiment, reason, item_ref |
| `LLMCACHE` | `<cveId>#<promptType>` | response, model_version, generated_at, source_last_modified |

### 8.3 iOS (SwiftData)

```swift
@Model
class StackProfile {
  var cloudProviders: [String]
  var identityProvider: String?
  var edr: [String]
  var siem: [String]
  var saas: [String]
  var regulatedData: [String]
  var sector: String?
  var employeeBand: String?
  var lastSynced: Date?
}

@Model
class Brief {
  var id: String
  var generatedAt: Date
  var actNow: [Item]
  var checkToday: [Item]
  var watch: [Item]
  var fyi: [Item]
}

@Model
class Item {
  var id: String                  // CVE ID or advisory ID
  var title: String
  var summary: String
  var severity: String            // "act_now" | "check_today" | "watch" | "fyi"
  var cveId: String?
  var cvssScore: Double?
  var epssScore: Double?
  var inKev: Bool
  var sources: [String]
  var matchedStack: [String]
  var confidence: String          // "high" | "medium" | "low"
  var whyItMatters: String?       // LLM-generated prose
  var boardParagraph: String?     // LLM-generated prose
  var teamQuestions: TeamQuestions?
  var feedback: Feedback?
}

@Model
class TeamQuestions {
  var infrastructure: [String]
  var soc: [String]
  var vulnMgmt: [String]
}

@Model
class Feedback {
  var itemId: String
  var sentiment: String           // "up" | "down"
  var reason: String?
  var createdAt: Date
  var synced: Bool
}
```

---

## 9. `[BUILD]` Order of operations for the live demo

This is the spine of the YouTube video. Each phase ends with something visibly working. Target: ~75 minutes.

| # | Phase | Budget | Done means |
|---|---|---|---|
| **0** | **Setup** | 5 min | Xcode project (SwiftUI, iOS 17). Wrangler CLI authenticated to Cloudflare. AWS CLI authenticated. NVD API key obtained. |
| **1** | **Cloudflare: KEV ingestion** | 8 min | `cron-kev` Worker fetches CISA KEV JSON hourly, stores raw in R2, parses into D1. Verify real KEV data in D1. |
| **2** | **Cloudflare: NVD + EPSS ingestion** | 10 min | `cron-nvd` does incremental sync via `lastModStartDate`. `cron-epss` pulls daily CSV. CPE → vendors/products extraction works. |
| **3** | **Cloudflare: API Worker** | 7 min | `POST /brief` accepts stack profile, queries D1, returns ranked CVE list (no LLM yet). Test with `curl`. |
| **4** | **AWS Lambda: matcher + Bedrock** | 12 min | Lambda invoked by Worker. Implements §7.5 matching. Calls Bedrock for `why-it-matters`, `board-paragraph`, `team-questions`. Returns enriched brief. Cache check before each Bedrock call. |
| **5** | **iOS: onboarding wizard** | 8 min | 6-screen chip selector. State held in `@Observable` view model. Persists to SwiftData. Final screen POSTs profile to Worker. |
| **6** | **iOS: Brief screen** | 10 min | `GET /brief` from Worker. Render sections by severity (Act Now red, Check Today orange, Watch yellow, FYI grey). Pull to refresh. |
| **7** | **iOS: Item detail** | 10 min | Push navigation. Sections: Why it matters (prose), Ask my team (3 collapsible groups with copy buttons), Board paragraph (copy button), Details (CVSS/EPSS/KEV badges, sources, matched stack chips). |
| **8** | **iOS: Feedback + Push** | 5 min | Thumbs up/down POSTs to Worker. APNs registration on first launch. Lambda triggers push when a new KEV entry matches stack. Test on simulator. |

### 9.1 What to cut if running long

- **If over by phase 7:** ship without History screen entirely. It's nice but not part of the demo contract.
- **If over by phase 5:** cut the per-section severity styling, render all items in one list with severity chips inline.
- **Phase 8 push notifications are non-negotiable.** That's the emotional payoff shot. If everything else is cut to the bone, push has to fire on camera.

### 9.2 What to highlight on-camera

- The Cloudflare cron firing and pulling real KEV data — show it in the Workers dashboard.
- The Lambda invocation with Bedrock generating the "Why this matters" prose in real time — show CloudWatch logs.
- The matching algorithm picking up a CVE that matches "Okta" in the user's profile.
- The first time the brief appears on the simulator with real data. Hero shot.
- The push notification firing. End the video here if possible.

---

## 10. `[BUILD]` LLM prompts (for the Bedrock calls)

These go in the Lambda as plain Python strings. Keep them small, deterministic, and cacheable.

### 10.1 `why-it-matters`

```
You are a cybersecurity analyst writing for a CISO who has roughly 30 seconds
to read this. Given the CVE and the CISO's tech stack below, write 2–3
sentences explaining why this vulnerability matters to THEM specifically.

Rules:
- Reference the matched stack component by name.
- Reference the evidence type (KEV listing, EPSS score, or CVSS).
- End with one concrete thing the CISO should do today.
- No marketing language. No alarm. Just facts.

CVE: {cve_id}
Description: {description}
CVSS: {cvss}
EPSS: {epss}
In KEV: {in_kev}
KEV date added: {kev_date}

User stack matched: {matched_stack}
User sector: {sector}
```

### 10.2 `board-paragraph`

```
Write a single paragraph (3–5 sentences) the CISO can paste into a board
update or email to the executive team. Plain English. Assume the reader is
not technical.

Cover: what the issue is, whether we are exposed, what we are doing about
it (in general terms), and what the residual risk looks like.

Do not use words like "leverage," "robust," "best-in-class," or any other
business jargon. Sound like a person who has done this for 20 years and is
slightly bored.

CVE: {cve_id}
Description: {description}
Matched stack: {matched_stack}
Severity classification: {severity}
```

### 10.3 `team-questions`

```
Generate 3 questions each for three internal teams. The questions should be
specific enough that the team can answer them with a yes/no plus evidence,
not vague enough to spawn a meeting.

Format strictly as JSON:
{
  "infrastructure": ["q1", "q2", "q3"],
  "soc": ["q1", "q2", "q3"],
  "vuln_mgmt": ["q1", "q2", "q3"]
}

CVE: {cve_id}
Description: {description}
Affected products: {products}
Matched stack: {matched_stack}
```

### 10.4 Caching rules

- Cache key: `{cve_id}#{prompt_type}#{user_stack_hash}` for `why-it-matters` and `team-questions` (stack-dependent).
- Cache key: `{cve_id}#{prompt_type}` for `board-paragraph` (stack-independent).
- Invalidate when CVE `last_modified` changes.
- Store in DynamoDB under PK `LLMCACHE`. TTL: 30 days.

---

## 11. Non-functional requirements

- **Performance:** Brief screen loads in <500ms from cold cache. Worker `/brief` endpoint p95 <800ms (includes Lambda + Bedrock when uncached).
- **Offline:** Last brief always readable offline (SwiftData cache). Feedback queues and syncs on reconnect.
- **Privacy:** Stack profile lives on user's device + DynamoDB (keyed by random device ID, no email required v1). No third-party analytics in v1.
- **Reliability:** If a data source is down, the brief still generates from cached data. Small "sources may be stale" indicator in UI.
- **Accessibility:** VoiceOver labels on all interactive elements. Dynamic Type support. Dark mode.
- **Cost ceiling for the demo:** Should run for under $20/month with ~10 testflight users. Bedrock calls are the dominant cost — that's why caching is mandatory, not optional.

---

## 12. Success criteria

### 12.1 For the YouTube demo

- App runs end-to-end on the simulator with real data by the end of the video.
- At least one CVE in the brief is from the actual current week.
- The push notification fires on-camera.
- The "Why this matters" prose for at least one item visibly references the user's stack component by name.
- Total build time under 90 minutes (75 min target + 15 min buffer).

### 12.2 For the product (post-demo)

- Brief precision (% items marked useful) ≥ 70%.
- D7 retention of TestFlight users ≥ 40%.
- Median time spent in app per session: 3–5 min.
- Out-of-band alert acknowledgement rate ≥ 80% (if lower, alerts are too noisy).
- % of priority items where the user copies a team question or board paragraph: aim for ≥ 30% (this proves the killer features land).

---

## 13. Roadmap beyond v1

### Phase 1.5 (next 30 days after v1)

- Email delivery channel.
- Microsoft Teams + Slack copy-block helpers.
- Account creation + multi-device sync (CloudKit or Cognito).
- iOS widget showing today's Act Now count.

### Phase 2 — Attack Surface Management

- User adds owned domains and IP ranges to profile.
- Backend runs daily passive recon:
  - Subdomain enumeration via crt.sh (certificate transparency logs).
  - DNS history.
  - Port observations (Shodan API or similar).
- Change detection: new subdomain, new open port, new SSL cert, new exposed service.
- New section in the daily brief: "What changed in your attack surface."
- Material changes (new internet-facing admin panel, new wildcard cert) fire out-of-band alerts.
- Same brief screen, same alert mechanism, new section. v1 sectioned design absorbs this without rewrite.

### Phase 3 — Passive enrichment

- Import scanner reports (Tenable, Rapid7, Qualys).
- CMDB connection.
- Cloud inventory import (AWS Config, Azure Resource Graph, GCP Asset Inventory).
- EDR asset list import.
- Identity provider directory sync.

### Phase 4 — Team workflow

- Assign items to team members.
- Auto-create Jira / ServiceNow tickets.
- Evidence collection threads per item.
- Slack / Teams item forwarding with context.

### Phase 5 — CISO Copilot

- Natural-language queries: "What changed in our risk posture this week?" / "What should I tell the board?" / "What are the top 5 things I should push my team on today?"
- Monthly executive cyber risk memo, auto-generated.
- Trend analysis: "Are we more exposed than last month?"

---

## 14. Open questions to resolve before recording

1. **Final app name.** "Sentinel Brief" is a placeholder. Should be App-Store-checkable before the demo — viewers will search.
2. **Bedrock model choice.** Claude Sonnet 4.6 is the default. Haiku 4.5 is cheaper and probably good enough for the three prompts. Worth a quick A/B before recording.
3. **D1 vs DynamoDB for CVE storage.** Spec uses D1 (close to ingestion) but DynamoDB would also work. D1 keeps the cost story cleaner for viewers.
4. **Real device vs simulator for the final demo shot.** Real device is more impressive; simulator is more reliable live. Recommend simulator with push fired from laptop terminal.
5. **NVD API key in the public repo.** Use `.env`, mention it on camera, don't commit it. Same for AWS credentials.
6. **Open-source the result?** If yes, license decision (MIT vs Apache 2.0 vs source-available). MIT is best for demo reach.
7. **Whether to record the Bedrock prompts being designed live, or come in with them pre-written.** Pre-written is faster; live is more honest. Lean pre-written; show them on screen briefly.

---

## Appendix A — Glossary for the YouTube audience

- **CVE** — Common Vulnerabilities and Exposures. The unique ID for a published vulnerability (e.g., CVE-2026-1234).
- **CPE** — Common Platform Enumeration. A standardized string identifying an affected product (e.g., `cpe:2.3:a:okta:okta:1.0:*:*:*:*:*:*:*`).
- **CVSS** — Common Vulnerability Scoring System. A 0–10 severity score. Useful for severity, useless for "should I drop everything."
- **EPSS** — Exploit Prediction Scoring System. A 0–1 probability that a CVE will be exploited in the next 30 days. Published by FIRST.org.
- **KEV** — Known Exploited Vulnerabilities catalog. CISA's curated list of CVEs with confirmed in-the-wild exploitation. Inclusion is a strong "act now" signal.
- **APNs** — Apple Push Notification service.
- **Bedrock** — AWS's managed service for foundation models, including Anthropic's Claude.
- **D1** — Cloudflare's serverless SQLite at the edge.
- **R2** — Cloudflare's S3-compatible object storage with zero egress fees.
