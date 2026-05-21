# Scan Performance — Parallel, Tier-Aware Scanning — Design

> Status: draft for review · 2026-05-21
> Part of the AWS scanner comprehensiveness uplift. Implements AWS; the
> pattern is **deliberately cloud-agnostic** — the Azure and GCP scanner
> uplifts will adopt the same parallel + tier-aware architecture (see §8).
> Related: `2026-05-20-aws-scanner-uplift-design.md` (the tier model this
> refines), `2026-05-21-region-discovery-design.md`.

## 1. Problem

Region discovery (just shipped) made scans correctly cover the
customer's *real* footprint — and immediately exposed a performance
wall. A test account is active in 16 of 17 regions. The scanner iterates
regions **serially**: for each region it runs entity enums + 12 regional
Shasta modules + the coverage engine, plus a region-iterating `ai_pass`.
Sixteen regions, serial, is **hours** — a verification scan was still
running at 108 minutes.

Two faults:

1. **No parallelism.** Region work is I/O-bound (AWS API calls) yet runs
   one region at a time. 16 regions take ~16× one region.
2. **Tier doesn't reduce work.** `scan_tier` only filters the coverage
   engine's checks. Every tier — including Quick, which exists to give a
   fast first-login result — runs the full 12-module Shasta sweep +
   `ai_pass` across every region.

## 2. Goal & success criteria

Scans complete within tier-appropriate wall-clock budgets, on accounts
active in 15-20 regions, with no loss of region coverage.

| Tier | Wall-clock target | Coverage |
|---|---|---|
| **Quick** | ~3-5 min | all active regions, crown-jewel depth |
| **Medium** | ~15-25 min | all active regions, full posture |
| **Deep** | ~1-2 h | all active regions, full posture + heavy capabilities |

Success criteria:

1. Region work runs concurrently; a 16-region scan finishes in roughly
   the wall-clock of one region's work (for the modules that tier runs).
2. **Every tier scans every active region** — tiers differ in *depth*,
   never in region coverage. No tier reintroduces a region blind spot.
3. Quick finishes in its budget on a 16-region account.
4. The transactional writer and scan-result correctness are unchanged.

Non-goals:

- Cross-task / Step Functions fan-out — rejected; in-task threading is
  sufficient at one-account / ~16-region scale (§3).
- Changes to region discovery — unchanged (§6.1).
- Building the Deep-tier capability modules — they are the AWS uplift's
  Slices 3-6; this spec only ensures the tier framework gates them.

## 3. Decisions (from brainstorming, 2026-05-21)

1. **In-task threading.** Regions scanned concurrently via a
   `ThreadPoolExecutor` inside the one Fargate task. The work is
   I/O-bound, so threads release the GIL on every AWS call and
   parallelize near-linearly. No new infrastructure; the per-scan
   transactional writer is untouched. Step Functions Map fan-out was
   rejected — it would force a rework of the transactional writer for
   partial/per-region writes, for no gain at this scale.
2. **Depth-only tiers.** Every tier scans every active region. Tiers
   differ in which *modules* and *checks* run, not in region scope —
   consistent with the no-blind-spots principle behind region discovery.
3. **Quick fast, Medium/Deep relaxed.** Quick must hit ~3-5 min by doing
   less; Medium/Deep favour completeness, made tolerable by parallelism.
4. **Deep capabilities wrap OSS.** `vuln_state` / `code_review` wrap
   best-of-breed open source (AWS Inspector, Trivy, Semgrep, gitleaks) —
   no in-house scanner/SAST engine. (Restated here because the codebase
   is going open-source; detail in the AWS uplift spec §7.)

## 4. Parallel region scanning

### 4.1 The scan-unit model

The handler's work is expressed as a flat list of independent **scan
units**, each producing `{entities, edges, findings}`:

- **Global units** — one per region-agnostic Shasta module (IAM,
  Organizations, CloudFront, logging) + the IAM/S3 entity enums. Run
  once per scan.
- **Region units** — one per active region. A region unit runs that
  region's compute/network entity enums + (tier-gated) the 12 regional
  Shasta modules + the coverage engine's checks for that region.
- **AI unit** — `ai_pass` (tier-gated to Medium+).
- **Capability units** — `reachability` / `identity_graph` /
  `vuln_state` / `code_review` (AWS uplift Slices 3-6; tier-gated).

All units for the scan's tier are submitted to **one
`ThreadPoolExecutor`** (`max_workers = min(unit_count, 16)`). Each unit
runs in a worker thread, returns its own emission lists; the main thread
**merges** all results after every unit completes, then `commit_scan`
writes once — the existing single transactional write, unchanged.

### 4.2 Thread-safety

- **Credentials** — the shared `RefreshableCredentials` object is
  thread-safe (botocore guards refresh with a lock). All threads share
  the one credentials object.
- **boto3 clients are NOT shared across threads.** Each worker builds
  its own clients / its own `AssumedRoleAWSClient` (all backed by the
  shared credentials). `AssumedRoleAWSClient` is constructed per unit.
- **No shared mutable accumulators.** Each unit returns its own
  `(entities, edges, findings, stats)`; merging happens single-threaded
  after the pool drains. Nothing appends to a shared list from a thread.
- **Per-unit failure isolation.** Each unit is wrapped in `try/except`
  (as the modules are today); a unit that raises is recorded in
  `module_stats` and does not abort the scan or sibling units.

### 4.3 Coverage engine adjustment

`coverage/engine.py`'s `run_coverage` currently loops regions
internally. It gains a per-region entry point so a region unit runs the
coverage checks for *its* region; the all-regions loop is removed (the
handler's pool now owns region concurrency). The engine's check logic,
tier filtering, and emissions are otherwise unchanged.

### 4.4 New unit: `scan_pipeline.py`

A new module `platform/lambda/shasta_runner/app/scan_pipeline.py` owns
the parallel runner — a pure-orchestration unit, independently testable:

```python
def run_units(units: list[ScanUnit], max_workers: int = 16) -> UnitResults
```

where a `ScanUnit` pairs a name with a zero-arg callable returning
`{entities, edges, findings}`, and `UnitResults` is the merged emissions
+ per-unit stats (success / error). The handler builds the unit list,
calls `run_units`, and passes the merged emissions to `commit_scan`.

## 5. Tier-aware module selection

Today `scan_tier` filters only the coverage engine's checks. It will now
also select which **units** the handler builds. Each unit type carries a
minimum tier:

| Unit | Quick | Medium | Deep |
|---|---|---|---|
| IAM / S3 entity enums | ✓ | ✓ | ✓ |
| Global Shasta modules (IAM, Orgs, CloudFront, logging) | ✓ | ✓ | ✓ |
| Per-region compute/network enums | ✓ | ✓ | ✓ |
| Coverage engine checks | crown-jewel (`min_tier=quick`) | + medium | all |
| Regional Shasta modules (the 12) | — | ✓ | ✓ |
| `ai_pass` | — | ✓ | ✓ |
| Light capability modules (reachability, identity, vuln-read) | — | ✓ | ✓ |
| Heavy capability modules (code_review, vuln-full) | — | — | ✓ |

So **Quick** = entity enums + global Shasta modules + crown-jewel
coverage checks, across all active regions, parallelized → ~3-5 min,
full region coverage, shallow. **Medium** adds the regional Shasta
sweep + `ai_pass` + the full coverage engine. **Deep** adds the heavy
OSS-wrapper capabilities.

Mechanism: the handler consults a `scan_tier`→unit-set policy when
building the unit list. The coverage engine keeps its existing
check-level `min_tier` filter (Slice 1); this spec lifts the same idea
to whole units. This **refines** the AWS uplift spec §3 tier model —
previously tier gated only coverage checks; now it gates units.

## 6. Small wins

### 6.1 Region discovery — unchanged

With regions scanned concurrently, scanning a near-empty (default-VPC-
only) region costs almost nothing — so region discovery's "active"
classification no longer needs to be precise for speed. And scanning a
default-VPC region is *correct*: a permissive default VPC / default
security group is a real finding. Region discovery stays exactly as
shipped.

### 6.2 Scan observability — fix stdout buffering

The scanner block-buffers stdout in the container (non-TTY → logs flush
only at process end), so a running scan is unobservable — this cost
real debugging time. Add `ENV PYTHONUNBUFFERED=1` to the scanner
Dockerfile so per-unit progress streams to CloudWatch live.

### 6.3 Task sizing

The Fargate task goes 2→4 vCPU and 4→8 GB (`scan-stack.ts`). The work is
I/O-bound so CPU is not the bottleneck, but ~16 worker threads each
holding boto3 clients and building JSON want the headroom.

### 6.4 Credentials — already handled

`RefreshableCredentials` (shipped) means long scans never expire;
parallelism also shortens scans. No further action — noted for
completeness.

## 7. Testing

- **`scan_pipeline.run_units`** — unit-tested directly: fake units,
  assert every unit runs, the merge is correct (entities/edges/findings
  concatenated), and a unit that raises is isolated (recorded in stats,
  siblings still complete).
- **Tier→unit policy** — unit-tested: Quick excludes regional Shasta /
  `ai_pass` units; Medium includes them; Deep includes heavy
  capabilities.
- **Coverage engine per-region entry point** — extend the existing
  `test_coverage_engine.py` for the per-region call.
- `main.py` handler wiring — structural (`ast.parse` + `grep`) plus the
  end-to-end scan in the build/deploy task; `main.py` is not importable
  in the test venv.

## 8. Cross-cloud applicability

This is an AWS spec, but the architecture is **cloud-agnostic by
design** and the Azure and GCP scanner uplifts will adopt it verbatim:

- `scan_pipeline.run_units` is pure orchestration — no AWS in it.
- The scan-unit model (global units, region/location units, capability
  units) maps directly onto Azure (subscriptions × regions) and GCP
  (projects × regions/zones).
- The `scan_tier`→unit-set policy is the same three-tier shape.
- Region discovery has an equivalent per cloud (Azure Resource Graph,
  GCP Asset Inventory) — a future per-cloud spec.

When the Azure / GCP scanners are uplifted, `scan_pipeline.py` is
intended to be shared (lifted to a common location), with each cloud's
handler supplying its own units. This spec deliberately keeps the
parallel runner free of AWS specifics so that lift is clean.

## 9. Phasing

A single implementation plan (~6-8 tasks): the `scan_pipeline.py`
parallel runner + tests, the coverage engine per-region entry point, the
tier→unit policy, the `main.py` handler rewrite to build + run units,
the Dockerfile `PYTHONUNBUFFERED` line, the `scan-stack.ts` task
resizing, and a build + deploy + end-to-end verification.

The E2E verification is the clean, fast scan that finally closes the
loose ends: it confirms a Quick scan finishes in budget, a Medium scan
covers all active regions in budget, and the credential fix + region
discovery all hold together. It supersedes the still-open RD-7 / S1-9
verification tasks.

## 10. Open questions / risks

- **Thread-pool size vs Fargate vCPU.** 16 I/O-bound threads on 4 vCPU
  is comfortable, but a region with very many resources could make one
  unit CPU-heavy (JSON building, ARN parsing). If profiling shows CPU
  saturation, lower `max_workers` or raise vCPU — tunable, not
  structural.
- **AWS API rate limits.** 16 regions hitting the same service APIs
  concurrently raises throttling risk. The boto `SCAN_BOTO_CONFIG`
  already retries on throttle (standard mode, 3 attempts) — but if
  throttling becomes material, a per-service concurrency cap may be
  needed. Watched in the E2E verification, not pre-built.
- **Coverage engine refactor** — exposing a per-region entry point
  touches Slice-1 code (`engine.py`); its tests must be updated in step.
- **`ai_pass` internal threading** — `ai_pass` itself iterates regions
  (`discover_bedrock_and_ai_lambdas`). As a single unit it runs on one
  worker thread and stays internally serial. Acceptable for Medium/Deep
  budgets; if `ai_pass` dominates Medium wall-clock, splitting it into
  per-region units is a follow-up.
