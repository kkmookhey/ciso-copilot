# Scan Execution v2 — Three-Stage, Parallel, Tier-Aware Scanning — Design

> Status: draft for review (revision 3) · 2026-05-21
> Part of the AWS scanner comprehensiveness uplift. Implements AWS; the
> architecture is **deliberately cloud-agnostic** — the Azure and GCP
> scanner uplifts will adopt the same three-stage pattern (see §11).
> Related: `2026-05-20-aws-scanner-uplift-design.md` (the tier model
> this refines), `2026-05-21-region-discovery-design.md` (the region
> discovery this extends).

## 1. Problem

Region discovery (shipped) made scans correctly cover the customer's
*real* footprint — and immediately exposed a performance wall. A test
account is active in 16 of 17 regions. The scanner iterates regions
**serially**; sixteen regions × (entity enums + 12 regional Shasta
modules + coverage engine) + a region-iterating `ai_pass` is **hours** —
a verification scan ran 108 minutes.

Three faults:

1. **No parallelism.** Region work is I/O-bound (AWS API calls) yet runs
   one region at a time.
2. **Tier doesn't reduce work.** `scan_tier` only filters the coverage
   engine's checks; every tier — including Quick, which exists to give a
   fast first result — runs the full 12-module Shasta sweep + `ai_pass`
   across every region.
3. **Every region is treated equally.** A region with one default VPC
   gets the same module sweep as the customer's main region. Region
   discovery today classifies only `active` vs `empty`; it cannot say
   "this region has nothing but AWS defaults."

## 2. Goal & success criteria

Scans complete within tier-appropriate wall-clock budgets, on accounts
active in 15-20 regions, with **no loss of region coverage** and
**explicit, auditable** coverage reporting.

| Tier | Wall-clock target | Coverage |
|---|---|---|
| **Quick — Phase 1 (First Signal)** | ~30-90 s | account-global posture + region census |
| **Quick — Phase 2 (Crown Jewel)** | ~3-5 min total | all enabled regions, crown-jewel depth |
| **Medium** | ~15-25 min | all regions, full posture, depth varied by region state |
| **Deep** | ~1-2 h | all regions, full posture + heavy OSS-wrapper capabilities |

Success criteria:

1. Region work runs concurrently; a 16-region scan finishes in roughly
   the wall-clock of the slowest single region's work for that tier.
2. **Every tier scans every enabled region** — tiers and region states
   vary *depth*, never drop a region silently. No region blind spot.
3. Quick Phase 1 returns a first signal in ~30-90 s; full Quick in ~3-5
   min, on a 16-region account.
4. Every scan emits a **coverage map** — per region: state, depth
   scanned, modules run / skipped, errors — so speed trade-offs are
   visible and defensible, never hidden.
5. The scan survives AWS throttling and slow/unreachable regions: a bad
   region yields a `partial` / `unknown` result, never an indefinite
   hang and never an aborted scan.
6. Scan-result correctness (the transactional writer) is preserved.

## 3. Decisions

From the original design + KK's revision-1 review (2026-05-21):

1. **Three-stage scan strategy** — eligibility → footprint probe &
   classification → tier-aware parallel deepening (§4). Region discovery
   is *extended*, not bypassed.
2. **In-task threading.** Regions scanned concurrently via a
   `ThreadPoolExecutor` inside the one Fargate task. I/O-bound work
   parallelizes near-linearly; no new infrastructure; the per-scan
   transactional writer is preserved. Step Functions fan-out rejected —
   it would force partial per-region writes for no gain at this scale.
3. **Depth-only tiers.** Every tier scans every enabled region. Tiers
   vary depth, never region scope. No tier reintroduces a blind spot.
4. **(tier, region_state) jointly select per-region depth** — a region
   classified `default-only` is scanned shallowly even at Medium; an
   `active` region is scanned fully. Speed without hiding risk (§6, §7).
5. **Quick is two phases** — a ~30-90 s First Signal, then the ~3-5 min
   Crown Jewel sweep — for early time-to-value (§7.4).
6. **Resilience is a first-class concern** — adaptive retry, per-service
   concurrency caps, per-unit timeout budgets, a `partial` scan state
   (§8). KK's review correctly flags throttling, not CPU, as the real
   risk.
7. **Coverage map** is a first-class scan output (§9).
8. **Deep capabilities wrap OSS** (AWS Inspector, Trivy, Semgrep,
   gitleaks) — no in-house scanner/SAST engine. The codebase is going
   open-source. (Detail in the AWS uplift spec §7.)

Non-goals (v1):

- Cross-task / Step Functions fan-out.
- **AWS-native inventory accelerators** (Resource Explorer aggregator,
  Config aggregator, CloudTrail Event History). The Stage-2 probe is
  structured so an accelerator can later slot in as an opportunistic
  fast path — but v1 does not build them, and the scanner must never
  *depend* on them (it must work in immature accounts where Config /
  Security Hub / Resource Explorer are not enabled). See §15.
- Building the Deep-tier capability modules — AWS uplift Slices 3-6.

## 4. The three-stage scan strategy

```
Assume role (RefreshableCredentials)
  │
  ├─ Stage 1 — Region eligibility
  │     enumerate enabled / default / disabled regions
  │
  ├─ Stage 2 — Footprint probe & classification
  │     parallel lightweight probe of every enabled region
  │     → classify each: active | default-only | empty | unknown
  │
  └─ Stage 3 — Tier-aware parallel deepening
        build scan units from (scan_tier × region_state × permissions)
        run units in a thread pool, bounded by per-service concurrency
        caps and per-unit timeouts
  │
  merge results → commit transactionally → emit findings + coverage map
```

Stage 1 + Stage 2 together *are* region discovery, extended from the
shipped binary classifier to a four-state one. Stage 3 is the parallel,
tier-aware scan-unit engine.

## 5. Stage 1 — Region eligibility

Enumerate the account's regions and their opt-in status:

- `ec2:DescribeRegions` (filtered to `opt-in-not-required` + `opted-in`)
  — the regions enabled for the account, as today.
- Where available, the Account Management `account:ListRegions` API
  gives explicit opt-in status (`ENABLED`, `ENABLED_BY_DEFAULT`,
  `DISABLED`) — used opportunistically to label regions; the EC2 call
  remains the source of truth so the scanner works without the Account
  API permission.

Output: the set of **enabled** regions (default-enabled + opted-in).
`DISABLED` regions are out of scope — a customer cannot have resources
in a region that is not enabled.

## 6. Stage 2 — Footprint probe & classification

For every enabled region, run a **lightweight probe in parallel** (one
probe unit per region, in the Stage-3 thread pool's sibling pool — or
the same pool, run first). The probe answers: *does this region hold
meaningful resources, only AWS defaults, or nothing?*

### 6.1 Probe signals

A small, fixed set of cheap `list` / `describe` calls per region —
chosen to distinguish real workloads from AWS-created defaults:

- **Network** — `DescribeVpcs` (note `IsDefault`), non-default subnets,
  non-default security groups.
- **Compute / containers** — EC2 instances, Lambda functions, ECS
  clusters, EKS clusters.
- **Data** — RDS instances, ELB/ELBv2 load balancers.
- **Keys** — customer-managed KMS keys.
- **Security services** — CloudTrail trails, Config recorders,
  GuardDuty detectors, Security Hub enablement (presence, not contents).

Each probe call is bounded by the boto timeout `Config` (§8). S3 is a
global listing (bucket region is an attribute) — handled outside the
per-region probe.

### 6.2 Classification

Each region is classified into one of four states:

| State | Meaning |
|---|---|
| `active` | Real resources found — non-default VPC, or any EC2 / RDS / Lambda / ECS / EKS / ELB, or customer KMS keys, etc. |
| `default-only` | Only AWS-created defaults — a default VPC / default security group and nothing else of substance. |
| `empty` | No resource signal at all — not even a default VPC (e.g. default VPC deleted, or a newer region). |
| `unknown` | One or more probe calls failed (AccessDenied, throttle, timeout) — the region's state could not be determined. |

The **anti-blind-spot invariant holds**: `unknown` is never treated as
`empty`. An `unknown` region is scanned conservatively (as if `active`)
and flagged in the coverage map as a visibility gap.

### 6.3 Relationship to the shipped region discovery

This **extends** `platform/lambda/shasta_runner/app/region_discovery.py`
(shipped — RD slice). The binary `active`/`empty` classifier becomes the
four-state model above; the single `resourcegroupstaggingapi:GetResources`
call becomes the multi-signal probe in §6.1. `RegionDiscovery` /
`classify_regions` evolve accordingly. The probe may *additionally* keep
a `GetResources` call as a broad catch-all signal for `active`.

## 7. Stage 3 — Tier-aware parallel deepening

### 7.1 The scan-unit model

All scan work is a flat list of independent **scan units**, each
producing `{entities, edges, findings}`:

- **Global units** — region-agnostic Shasta modules (IAM,
  Organizations, CloudFront, logging) + the IAM / S3 entity enums. Run
  once per scan.
- **Region units** — per (region, module-group). A region's work is
  decomposed into units so the per-service concurrency caps (§8) and
  per-unit timeouts can act at a useful granularity.
- **AI unit** — `ai_pass` (Medium+).
- **Capability units** — `reachability` / `identity_graph` /
  `vuln_state` / `code_review` (AWS uplift Slices 3-6).

Units for the scan are submitted to **one `ThreadPoolExecutor`**. Each
unit runs in a worker thread and returns its own emission lists; the
main thread **merges** after the pool drains, then `commit_scan` writes
once (the existing single transactional write — see §7.4 for Quick's
two commits). Step Functions / partial per-region writes are not used.

### 7.2 Thread-safety

- The shared `RefreshableCredentials` object is thread-safe (botocore
  guards refresh with a lock); all threads share it.
- boto3 clients are **not** shared across threads — each unit builds its
  own clients / `AssumedRoleAWSClient`.
- No shared mutable accumulator — each unit returns its own
  `(entities, edges, findings, stats)`; merge is single-threaded.
- Each unit is wrapped in `try/except`; a failing or timing-out unit is
  recorded in the coverage map and never aborts the scan.

### 7.3 Depth selection — (tier × region_state)

The unit set built for each region is a function of **both** the scan
tier and the region's Stage-2 state:

| Region state | Quick | Medium | Deep |
|---|---|---|---|
| `active` | crown-jewel checks | full posture (all Shasta modules + coverage engine + `ai_pass`) | full posture + heavy capability modules |
| `default-only` | default-VPC / default-SG risk checks only | selected controls (network baseline, defaults, encryption-by-default, region security-service presence) | selected controls |
| `empty` | minimal — region census only | skip heavy modules; baseline region checks | baseline checks unless a full sweep is explicitly requested |
| `unknown` | scan conservatively as `active`; flag gap | scan as `active`; retry; report uncertainty | scan as `active`; retry; report |

Global units run at every tier (account posture is always cheap and
high-signal). `ai_pass` and the regional Shasta sweep run for `active`
(and `unknown`) regions at Medium+. This is the refinement of the AWS
uplift spec §3 tier model: previously tier gated only coverage checks;
now `(tier, region_state)` jointly gate whole units.

### 7.4 Quick is two phases

Quick runs as two sequential phases within one scan, each ending in its
own transactional commit — two clean commits, **not** concurrent partial
writes:

**Phase 1 — First Signal (~30-90 s).** Account-global, fast:
- identity / account summary; the Stage-1/2 region census (counts of
  `active` / `default-only` / `empty` / `unknown`);
- public S3 exposure signals;
- IAM root / admin / MFA / password-policy basics;
- security-service presence (CloudTrail, Config, GuardDuty, Security
  Hub);
- the most critical public network exposure signals.

Phase 1's findings + coverage map are committed immediately, so the app
shows a first signal in under ~90 s.

**Phase 2 — Crown Jewel (~3-5 min total).** Region-parallel crown-jewel
checks: internet-exposed compute, permissive security groups, public
load balancers, RDS public exposure, KMS / key hygiene, CloudTrail /
logging posture, default-VPC / default-SG risk. Committed on completion.

Medium and Deep are single-phase (they are not latency-sensitive in the
same way).

### 7.5 Coverage engine adjustment

`coverage/engine.py`'s `run_coverage` currently loops regions
internally. It gains a per-region entry point so a region unit runs the
coverage checks for *its* region; the handler's pool owns region
concurrency. The engine's check logic and tier filtering are otherwise
unchanged.

### 7.6 New module: `scan_pipeline.py`

`platform/lambda/shasta_runner/app/scan_pipeline.py` owns Stage-3
orchestration — pure orchestration, independently testable, **no AWS
specifics** so it can later be shared with the Azure / GCP scanners:

- `run_units(units, limiter) -> UnitResults` — submits units to the
  thread pool, enforces per-unit timeouts, returns merged emissions +
  per-unit status (`success` / `error` / `timeout`).
- A `ConcurrencyLimiter` — per-service bounded semaphores (§8).
- A `ScanUnit` = (name, service, min_tier, callable).

## 8. Resilience & throttling

KK's review correctly identifies AWS API throttling — not CPU — as the
principal risk of 16-way concurrency. Measures:

- **Adaptive retry.** `SCAN_BOTO_CONFIG` switches from `standard` to
  `adaptive` retry mode — botocore's adaptive mode adds client-side,
  throttle-aware rate limiting (a token bucket that slows down when a
  service returns throttling errors). This is the first line of defence
  and is largely automatic.
- **Per-service concurrency caps.** A global `max_workers` is too blunt
  — IAM, Organizations, CloudTrail, Config, Security Hub, Access
  Analyzer have different limits and global vs regional behaviour. The
  `ConcurrencyLimiter` holds a bounded semaphore per AWS service; a unit
  acquires its service's semaphore around its API-heavy work, so the
  in-flight call count to any one service stays bounded regardless of
  how many region units are running.
- **Per-unit timeout budgets.** Each unit's future is awaited with a
  deadline (`future.result(timeout=...)`); a unit that exceeds it is
  abandoned, its region/module marked `timeout` in the coverage map. One
  slow region never holds the scan hostage.
- **`partial` scan state.** `scans.status` already permits `partial`
  (schema `002_phase_a.sql`). When some units error or time out, the
  scan completes as `partial` and the coverage map records exactly
  which regions/modules were affected — an honest, visible result, not
  a silent gap.

## 9. The coverage map

Every scan emits a **coverage map** — the central transparency
artifact — written to `scans.scope` (extending what region discovery
writes there today). Per region:

```json
{
  "us-east-1": {
    "state": "active",
    "scanned_depth": "medium",
    "modules_run": ["network", "compute", "iam", "logging", "coverage"],
    "modules_skipped": [],
    "errors": []
  },
  "ap-south-1": {
    "state": "default-only",
    "scanned_depth": "baseline",
    "modules_run": ["network_baseline", "default_vpc"],
    "modules_skipped": ["regional_shasta", "ai_pass"],
    "errors": []
  },
  "me-central-1": {
    "state": "unknown",
    "scanned_depth": "partial",
    "modules_run": ["network", "compute"],
    "modules_skipped": [],
    "errors": ["AccessDenied: config:DescribeConfigurationRecorders",
               "timeout: regional_shasta"]
  }
}
```

Plus a top-level summary (tier, phase, region-state counts, overall
`completed` / `partial`). The web / iOS apps read it to show "scanned N
regions fully, M at baseline, K with gaps" — turning every speed
trade-off into a defensible, inspectable engineering decision.

## 10. Scan progress & scan-type UX

A fast two-phase Quick scan and a coverage map are only valuable if the
user can see them. Today, after connecting a cloud and returning to the
app, the user faces a blank screen with no sign a scan is running.

### 10.1 Backend contract

The scan row already carries `tier`, `status`
(`queued|running|partial|completed|failed`) and `scope` (the coverage
map). Two additions:

- A **`phase`** field on the `scans` row — `region_discovery` |
  `first_signal` | `crown_jewel` | `full` | `done` — so the app can show
  *what* the scan is doing, not merely that it is running. The pipeline
  updates `phase` (and the coverage map in `scope`) as it advances.
- A **scan-status API** the app polls — returns `tier`, `status`,
  `phase`, the coverage map, and running finding counts. Either a new
  `GET /v1/scans/{id}` or an extension of the existing scans API (the
  implementation plan confirms which against the current API surface).

### 10.2 Progress visibility — chunked results + a progress view

The two-phase Quick design gives a safe hybrid of the two options KK
raised (stream results vs. progress bar) — with **no risky per-finding
incremental writes**:

- Quick **Phase 1 commits** (~30-90 s) → its findings appear in the app,
  labelled as a partial / in-progress result.
- While Phase 2 runs, the app shows a **progress view** driven by
  polling `phase` + the coverage map: "Discovering regions… → Found 16
  active regions, 1 empty → Phase 1: account posture ✓ → Phase 2:
  crown-jewel checks across 16 regions…".
- Phase 2 commits → the full Quick result renders.

Results populate in two visible chunks *and* a progress view explains
the gap — both of the user's options, without per-finding streaming.

### 10.3 Results labelled by scan type

Every findings / dashboard view shows the scan behind it — "Quick Scan ·
<when>" or "Medium Scan · <when>" — so the user always knows the depth
of the results they are looking at. (`tier` is on the scan row.)

### 10.4 Scan-type picker on rescan

For an already-connected cloud, the **Scan** control becomes a
split-button / dropdown offering **Quick · Medium · Deep**, each with a
short info element on what it implies and roughly how long it takes
(Quick — crown-jewel, ~5 min; Medium — full posture, ~20 min; Deep —
+ code & vulnerability review). Selecting **Deep** routes the user to a
**Contact Us** page — the interim face of the Deep-tier entitlement gate
(AWS uplift spec §11), later replaced by a payment gateway.

### 10.5 Surface scope

The progress view and scan-type picker are **web-app** features. iOS,
per its companion-app direction, shows results with the scan-type label
only — the progress view and picker are not ported. (Assumption — flag
if iOS should do more.)

## 11. Observability & task sizing

- **`PYTHONUNBUFFERED=1`** in the scanner Dockerfile — the scanner
  block-buffers stdout in the container, so a running scan is currently
  unobservable (logs flush only at process end; this cost real
  debugging time). Unbuffered output streams per-unit progress to
  CloudWatch live.
- **Fargate task 2→4 vCPU, 4→8 GB** (`scan-stack.ts`). The work is
  I/O-bound so CPU is not the bottleneck, but ~16 worker threads each
  holding boto3 clients and building JSON want the headroom.
- **Credentials** — `RefreshableCredentials` (shipped); parallelism also
  shortens scans. No further action.

## 12. Cross-cloud applicability

The three-stage architecture is **cloud-agnostic by design**; the Azure
and GCP scanner uplifts will adopt it:

- `scan_pipeline.py` (`run_units`, `ConcurrencyLimiter`, `ScanUnit`) is
  pure orchestration with no AWS in it — intended to be lifted to a
  shared location, each cloud's handler supplying its own units.
- The three stages map cleanly: Stage 1 eligibility → enabled
  subscriptions/regions (Azure) or projects/regions (GCP); Stage 2
  footprint probe → the same four-state classification; Stage 3
  tier-aware deepening → identical.
- AWS-native accelerators have per-cloud equivalents (Azure Resource
  Graph, GCP Cloud Asset Inventory) — opportunistic, never required.

This spec keeps `scan_pipeline.py` AWS-free precisely so that lift is
clean.

## 13. Testing

- **`scan_pipeline.run_units`** — unit-tested: fake units, assert every
  unit runs, the merge is correct, a raising unit is isolated, a unit
  exceeding its timeout is recorded as `timeout` and siblings still
  complete.
- **`ConcurrencyLimiter`** — unit-tested: concurrent acquisitions of one
  service's semaphore never exceed the cap.
- **Stage-2 classification** — unit-tested with `botocore.stub.Stubber`:
  probe responses producing each of `active` / `default-only` / `empty`
  / `unknown` (including an erroring probe → `unknown`, never `empty`).
- **(tier × region_state) depth selection** — unit-tested: the unit set
  for each cell of the §7.3 matrix.
- **Coverage engine per-region entry point** — extend
  `test_coverage_engine.py`.
- `main.py` handler wiring — structural (`ast.parse` + `grep`) plus the
  end-to-end scan in the build/deploy task (`main.py` is not importable
  in the test venv).

## 14. Phasing

A single implementation plan, executed as ordered tasks:

1. `scan_pipeline.py` — `run_units`, `ConcurrencyLimiter`, `ScanUnit` +
   tests.
2. Stage 2 — extend `region_discovery.py` to the four-state probe &
   classification + tests.
3. `(tier × region_state)` depth-selection policy + tests.
4. Coverage engine per-region entry point.
5. `main.py` handler rewrite — the three-stage pipeline, Quick's two
   phases + two commits, coverage-map emission.
6. Resilience — `adaptive` retry config, per-service caps wired,
   per-unit timeouts, `partial` status.
7. Dockerfile `PYTHONUNBUFFERED` + `scan-stack.ts` task resize.
8. Backend progress contract (§10.1) — the `scans.phase` field
   (migration) + the scan-status API the app polls.
9. Web UX (§10) — the in-progress scan view (region census + phase
   progress, chunked Phase-1/Phase-2 results), scan-type labels on
   findings/dashboard views, and the Quick/Medium/Deep scan-type picker
   with info elements + the Deep → Contact Us route.
10. Build + deploy + end-to-end verification.

Tasks 1-8 + 10 are the scanner/backend group; task 9 is the web-app
group — they can be reviewed and executed as two groups (the web group
depends on task 8's API). The E2E verification is the clean, fast scan
that closes the open RD-7 and S1-9 verification loose ends: it confirms
Quick Phase 1 in ~30-90 s, full Quick in budget, a Medium scan covering
all regions with the coverage map populated, `partial` handling on an
induced error, and the web progress view + scan-type picker working
against a live scan.

## 15. Open questions / risks

- **Probe cost.** The Stage-2 probe is ~8-12 `list`/`describe` calls per
  region. Across ~17 regions in parallel that is bounded and cheap, but
  it is more than the single tagging-API call today — measured in the
  E2E verification; trimmed if needed.
- **Per-service cap tuning.** The right semaphore size per service is
  not known a priori; start conservative and tune from observed
  throttling in the coverage-map error data.
- **AWS-native accelerators (future).** When a customer has Resource
  Explorer's aggregator index or a Config aggregator, Stage 2 could
  replace the per-region probe with one cross-region query. The probe
  interface should make this a drop-in fast path later — explicitly out
  of v1 scope, and never a dependency (the scanner must work in accounts
  where none of these are enabled).
- **`ai_pass` internal serialism.** `ai_pass` iterates regions
  internally; as one unit it runs on a single worker thread. Acceptable
  for Medium/Deep budgets; if it dominates Medium wall-clock, splitting
  it into per-region units is a follow-up.
- **Two-commit Quick.** Phase 1 committing before Phase 2 means a Quick
  scan is briefly in a state where Phase-1 findings exist and Phase-2
  ones do not. The scan row's `status` / coverage-map `phase` field
  makes this explicit; consumers must treat a Phase-1-only scan as
  in-progress, not complete.
