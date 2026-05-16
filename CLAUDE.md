# CISO Copilot

A daily threat & vulnerability briefing for CISOs, filtered through their own
tech stack. iOS-first, ~5-minute morning read, with push alerts for things
that actually matter.

**Source of truth for the build:** `CISOBrief.md` in this directory. That is
the PRD and the executable spec. Where this file and the PRD disagree, this
file's locked-in decisions win — they reflect choices made after the PRD was
written. The PRD wins on everything else.

## Status

Deployed at **https://ciso-copilot.kkmookhey.workers.dev** · repo at
**https://github.com/kkmookhey/ciso-copilot** (MIT, public).

End-to-end verified: KEV (1,592 entries) + NVD 7-day window (~5k CVEs) + EPSS
scores in D1 · synthetic user's brief produced 5 LLM-enriched items
(why-it-matters, board paragraph, team questions) in 13.8s cold / 0.33s with
LLM cache hits. iOS app builds clean, onboarding renders on simulator.

Phase 8 (real-iPhone APNs) is wired in the Worker but unverified — needs the
app installed on a physical iPhone to register a device token and fire a push.

## Locked-in decisions (these override the PRD)

| Topic | Decision | Why |
|---|---|---|
| **App name** | **CISO Copilot** | Replaces the PRD's "Sentinel Brief" placeholder (`CISOBrief.md` §14.1). |
| **Cloud** | **Cloudflare only** — Workers + Cron + R2 + D1. No AWS. | One CLI (`wrangler`), no cross-cloud auth, faster live iteration. Overrides the PRD's split CF + AWS architecture in §7. |
| **LLM access** | **Anthropic API direct** (Sonnet 4.6) from a Worker. Not Bedrock. | Single-cloud follow-through. Key lives in Workers Secrets. |
| **Model** | **Claude Sonnet 4.6** (`claude-sonnet-4-6`) for all three prompts. | Decided over Haiku 4.5; prose quality matters more than per-call cost at demo scale. |
| **Brief generation** | **Pre-computed nightly cron**. API only reads. | Hits the PRD's <500ms read target trivially. Bedrock/LLM latency stays off the request path. |
| **APNs** | Push fired from a Cloudflare Worker. JWT signed in-Worker with the `.p8` key. Triggered from laptop to real iPhone for the final demo shot. | Replaces SNS Mobile Push from the PRD. |
| **Demo target** | **Real iPhone**, not simulator, for the final push shot. Simulator used during build. | Apple Developer Program is active, `.p8` key is in hand. |
| **License** | **MIT**, public GitHub repo from phase 0. | Demo reach. |
| **Data storage** | **D1 for everything** — `cves`, `kev`, `epss`, `advisories`, plus `users`, `briefs`, `feedback`, `llm_cache`. | Single store. Drops the DynamoDB single-table design from PRD §8.2. |
| **Raw archives** | R2 (timestamped blobs of each pulled feed). | As in PRD. |

## Architecture (single-cloud Cloudflare)

```
Public sources (CISA KEV · NVD CVE 2.0 · EPSS CSV · CISA + vendor RSS)
        │
        ▼
Cloudflare Cron Workers
  cron-kev   (hourly)      ─┐
  cron-nvd   (every 2h)    ─┤──►  R2 (raw timestamped blobs)
  cron-epss  (daily 06 UTC)─┤
  cron-rss   (every 30m)   ─┘
                            └──►  D1 (parsed CVE/KEV/EPSS/advisories)

cron-brief (nightly, per user)
  └─► read D1 for user stack
      run matcher (CPE + keyword)
      call api.anthropic.com (Sonnet 4.6)
         · why-it-matters · board-paragraph · team-questions
         · cached by {cve_id}#{prompt_type}[#{stack_hash}]
      write brief into D1
      for Act Now items: sign APNs JWT, POST to api.push.apple.com

api Worker
  GET  /brief                ─┐
  POST /feedback             ─┤── reads/writes D1, no LLM on the path
  POST /register-token       ─┤
  GET  /history              ─┘

iOS app (SwiftUI · iOS 17+ · SwiftData cache · MVVM with @Observable)
  └─► HTTPS to api Worker only.
      Never talks to KEV/NVD/EPSS/Anthropic/APNs directly.
```

## Data sources (v1, all free, all rate-limited at the Worker layer)

CISA KEV (hourly), NVD CVE API 2.0 (every 2h, incremental via
`lastModStartDate`, free API key), EPSS daily CSV (one pull/day), CISA
Advisories RSS + 5 vendor RSS feeds (MSRC, AWS Security, Okta Trust, Cisco
PSIRT, Citrix) every 30 min. Detail in `CISOBrief.md` §6.

## Project layout (to be created at phase 0)

- `ios/` — SwiftUI app, SwiftData models, `project.yml` (xcodegen)
- `workers/` — `wrangler.toml`, `src/api/`, `src/cron-kev/`, `src/cron-nvd/`,
  `src/cron-epss/`, `src/cron-rss/`, `src/cron-brief/`, `schema.sql` (D1)
- `prompts/` — the three Bedrock-style prompt templates (`CISOBrief.md` §10),
  pinned to a model version for reproducibility
- `LICENSE` (MIT), `README.md`, `.gitignore` (Workers Secrets, `.env`,
  `*.p8`, `terraform.tfvars`, build artifacts)

## Secrets

All live in **Workers Secrets** (`wrangler secret put`), never in the repo:

- `ANTHROPIC_API_KEY`
- `NVD_API_KEY`
- `APNS_KEY_P8` (the contents of the `.p8` file, multi-line)
- `APNS_KEY_ID`, `APNS_TEAM_ID`, `APNS_BUNDLE_ID`

`.gitignore` must cover `.p8`, `.env`, `terraform.tfvars` (legacy), and any
file matching `*secret*` or `*credential*` from minute one.

## Commands

All wrangler commands must run from `workers/` (where `wrangler.toml` lives).
All xcodebuild / simctl commands from `ios/`.

```bash
# Backend
cd workers
wrangler deploy                              # ship
wrangler d1 execute ciso_copilot --remote \
  --file=src/schema.sql                       # apply schema
wrangler d1 execute ciso_copilot --remote \
  --command "SELECT COUNT(*) FROM kev"        # ad-hoc query
wrangler tail                                # live logs
wrangler secret put <NAME>                   # via stdin, one-off

# Trigger ingestion / brief gen (debug endpoints, no auth in v1)
curl https://ciso-copilot.kkmookhey.workers.dev/__debug/run/kev
curl https://ciso-copilot.kkmookhey.workers.dev/__debug/run/nvd
curl https://ciso-copilot.kkmookhey.workers.dev/__debug/run/epss
curl "https://ciso-copilot.kkmookhey.workers.dev/__debug/run/brief-for?device=<id>"
curl "https://ciso-copilot.kkmookhey.workers.dev/__debug/run/push-test?device=<id>"
curl  https://ciso-copilot.kkmookhey.workers.dev/__debug/counts

# iOS (simulator)
cd ios
xcodegen                                     # regenerate xcodeproj from project.yml
xcodebuild build -project CISOCopilot.xcodeproj -scheme CISOCopilot \
  -destination "platform=iOS Simulator,name=iPhone 17" \
  -derivedDataPath build CODE_SIGNING_ALLOWED=NO
xcrun simctl install booted \
  build/Build/Products/Debug-iphonesimulator/CISOCopilot.app
xcrun simctl launch booted ai.transilience.cisocopilot
```

## Known limits / things to know before changing

- **`/__debug/run/brief-for` is synchronous** (not `waitUntil`) because LLM
  calls for N items exceed the post-response budget. Production cron uses
  the scheduled handler which has 15-min wall-clock.
- **`TOP_N = 5` in `cron/brief.ts`** (down from PRD's 10) to keep cold LLM
  fan-out under the per-request budget. Raise once we move brief gen
  entirely behind the scheduled cron and let it run as long as it needs.
- **All vendor/product strings are normalized to CPE-style** (lowercase,
  spaces→underscores) in KEV ingestion, NVD ingestion, and `lib/stack.ts`
  chip aliases. If you add a chip alias, keep this convention.
- **LLM cache invalidates on `cve.last_modified` change** per `CISOBrief.md`
  §10.4. KEV's `dateAdded` is the proxy for KEV-only entries. If you change
  how `lastModified` is derived, cache will mass-miss on next run.
- **Anthropic API key (not Bedrock)** — set via `wrangler secret put
  ANTHROPIC_API_KEY`. Must start with `sk-ant-`; `sk-proj-` is OpenAI.

## Verifying push on a real iPhone

1. Install the app on the iPhone via Xcode (Team `2G875YX5NV` is in
   `ios/project.yml`).
2. First launch: app calls `requestAuthorization` → iOS prompts → on
   acceptance, APNs registers the device → `AppDelegate` hands token to
   `PushManager` → `APIClient.registerToken` POSTs to `/register-token`.
3. From the laptop:
   `curl "…/__debug/run/push-test?device=<deviceId>"` fires a test push.
4. For an end-to-end demo: complete onboarding with a stack chip that
   matches a current KEV entry's vendor+product (Apple, Adobe, or
   Cisco-targeted chips tend to land `act_now`), then
   `curl "…/__debug/run/brief-for?device=<id>"` — push fires for Act Now
   items via the normal code path.

## Working principles for this build

- **The demo contract is `CISOBrief.md` §4.3.** Anything not on that list is
  cuttable if we run long.
- **Determinism first, LLM second.** Matching is CPE + keyword. LLM is prose
  only. Do not introduce embeddings or classifiers for relevance in v1.
- **LLM cache is mandatory.** `{cve_id}#{prompt_type}` for stack-independent
  prompts, `{cve_id}#{prompt_type}#{stack_hash}` for stack-dependent ones.
  Invalidate when the CVE `last_modified` changes (`CISOBrief.md` §10.4).
- **Prompt caching on the Anthropic API.** Use the `cache_control` field on
  the static system prompt + few-shot blocks so cache hits cost ~10% of full
  calls. This is the dominant variable cost in production.
- **One model version, pinned.** The model ID lives in one config value;
  don't hardcode `claude-sonnet-4-6` in multiple call sites.
- **The iOS app never calls upstream sources.** Only the api Worker. This is
  non-negotiable — it's our rate-limit and key-protection boundary.

## See also

- `CISOBrief.md` — full PRD. Build spec.
