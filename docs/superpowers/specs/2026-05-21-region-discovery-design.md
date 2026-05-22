# AWS Region Discovery — Design

> Status: draft for review · 2026-05-21
> Part of the AWS scanner comprehensiveness uplift; slots between Slice 1
> and Slice 2. Spec for the "region-discovery slice".
> Parent spec: `docs/superpowers/specs/2026-05-20-aws-scanner-uplift-design.md`.

## 1. Problem

The AWS scanner picks the regions it scans badly, in two opposite
directions at once:

- **Too narrow.** `onboarding_aws_complete` hardcodes
  `regions=["us-east-1"]` into the scan event. `main.py` reads
  `regions = event.get("regions") or ["us-east-1"]` and the regional
  Shasta modules + the Slice-1 coverage engine iterate exactly that. A
  customer running workloads in `eu-west-1` is largely unscanned — a
  silent blind spot. (This mistake predates CISO Copilot — the original
  Shasta build had the same `us-east-1` default.)
- **Too wide.** `ai_pass` ignores the scan scope and sweeps **all ~17
  enabled regions** via Shasta's `get_enabled_regions()`, making scans
  slow (and undermining the Quick tier's fast-first-result goal).

The fix: before scanning, **discover which regions the account actually
uses**, and scan exactly those — never the hardcoded default, never the
whole AWS universe.

## 2. Goal & success criteria

Every scan scans the customer's real regional footprint, and that
footprint is auditable.

1. The scanner determines the account's active regions per scan, with no
   hardcoded default.
2. A region with resources is **never silently skipped** — uncertainty
   always resolves toward scanning.
3. Empty regions are skipped, so scans stay fast and scoped.
4. `scans.scope` records the discovered / scanned / skipped breakdown.
5. `ai_pass` is scoped to the discovered regions, not all 17.

Non-goals (v1):

- Unused-region hardening checks (flagging idle regions as soft
  targets) — a future check type, not this slice.
- A connection-level stored "footprint" column / UI region picker — the
  app reads the latest scan's `scope`; no migration.
- Resource Explorer / AWS Config aggregator fast paths — the per-region
  tagging-API sweep is the single mechanism.

## 3. Decisions (from brainstorming, 2026-05-21)

1. **Per-scan pre-pass.** Discovery runs at the start of every scan, not
   at connect time — a customer expanding into a new region is picked up
   on the next scan, no staleness.
2. **Detection = tagging-API sweep.** One
   `resourcegroupstaggingapi:GetResources` call per enabled region;
   the region is active if it returns ≥1 resource. `GetResources`
   returns taggable resources (tagged or not) across hundreds of
   resource types — far broader than probing a fixed handful of
   services, and works on any account with no customer setup.
3. **Skip empty regions** — deep-scan only active regions (v1).
4. **In-scanner pre-pass** — a module inside the scanner, run as the
   handler's step 0. No separate Lambda / Step Functions step.

## 4. Architecture & flow

New module: `platform/lambda/shasta_runner/app/region_discovery.py`.

The scanner `handler` gains a **step 0**, before the global / regional /
AI passes:

1. **Enumerate enabled regions** — `ec2:describe_regions` filtered to
   `opt-in-status ∈ {opt-in-not-required, opted-in}` (the same call
   Shasta's `get_enabled_regions` makes).
2. **Sweep** — for each enabled region, one
   `resourcegroupstaggingapi:GetResources` call requesting a minimal
   page (we only need "≥1 resource or not"). Each call uses the boto
   timeout `Config` (`aws_config.SCAN_BOTO_CONFIG`).
3. **Active set** = regions returning ≥1 resource, **unioned with
   `us-east-1`** — global services (IAM, CloudFront, Route 53, STS)
   anchor in `us-east-1` and must always be scanned.
4. The active set becomes `regions` for: the regional Shasta modules,
   the Slice-1 coverage engine, and `ai_pass`.

```
handler:
  step 0  region_discovery.discover_regions(...) -> active_regions
  global modules   (region-agnostic, anchored us-east-1) — unchanged
  regional modules  iterate active_regions
  coverage engine   scan_tier + active_regions
  ai_pass           scoped to active_regions (see §6)
  commit_scan
```

### 4.1 The anti-blind-spot invariant

A region is skipped **only** on a positive, successful "zero resources"
result. If `GetResources` **errors** in a region (permission denied,
throttling, timeout), that region is treated as **active** and scanned.
Erring toward over-scanning is the safe direction; silent
under-scanning is the exact failure being eliminated.

## 5. Discovery module interface

```python
# region_discovery.py

@dataclass(frozen=True)
class RegionDiscovery:
    active_regions:  list[str]        # to scan (sorted, includes us-east-1)
    enabled_regions: list[str]        # all opted-in regions seen
    skipped_empty:   list[str]        # enabled, swept clean, skipped
    errored_regions: list[str]        # sweep errored — included in active
    method:          str              # "tagging_api" | "degraded_default"

def discover_regions(ec2_client, tagging_client_for_region) -> RegionDiscovery
```

`discover_regions` is the orchestrator. `tagging_client_for_region` is a
callable `region -> boto3 resourcegroupstaggingapi client` so the module
is unit-testable with stubbed clients. A pure helper
(`classify_regions`) takes already-fetched per-region resource counts
and produces the `RegionDiscovery` — unit-tested directly.

## 6. Integration points

- **`main.py` handler** — runs `discover_regions` as step 0 (unless the
  scan event carries an explicit `regions` override — see §7). Uses
  `result.active_regions` as `regions` for the regional-module loop and
  passes it to the coverage engine.
- **`AssumedRoleAWSClient.get_enabled_regions()`** — overridden to
  return the discovered active set. This transparently scopes **both**
  Shasta's `discover_aws_ai_services` and the in-repo
  `discover_bedrock_and_ai_lambdas` (both call `get_enabled_regions()`)
  to active regions — no Shasta edit. The client is given the active set
  via its constructor.
- **`module_stats` / `scans.scope`** — records the discovery result.

## 7. Onboarding & manual override

- **`onboarding_aws_complete`** stops hardcoding `regions=["us-east-1"]`
  — it omits `regions` from the scan event entirely.
- **`regions` becomes an optional override.** If a scan event
  explicitly includes a non-empty `regions`, the scanner honors it
  verbatim and **skips discovery** (an operator re-scanning one specific
  region keeps a clean lever). Absent it, discovery runs.

## 8. Data model

No migration. `scans.scope` (existing JSONB) records the discovery
outcome:

```json
{
  "regions": ["us-east-1", "eu-west-1"],
  "enabled_regions": ["us-east-1", "us-east-2", "...17..."],
  "skipped_empty": ["ap-south-1", "...15..."],
  "discovery": { "method": "tagging_api", "errored_regions": [] }
}
```

The web / iOS app reads the latest scan's `scope` to show "scanned N of
M regions" — coverage becomes auditable per scan.

## 9. Error handling

All failure paths err toward over-scanning, never toward a silent miss:

- `GetResources` errors in a region → region treated active (§4.1);
  the region id is recorded in `errored_regions`.
- `describe_regions` fails entirely (cannot enumerate regions) →
  fall back to a documented default region set, set `method` to
  `degraded_default`, and surface the degradation in `scans.scope`.
  Never silently narrow.
- Account with resources in zero regions → active set = `us-east-1`
  alone (the union floor).

## 10. Reader-role dependency

The sweep requires `tag:GetResources` and `ec2:DescribeRegions` on the
customer `CISOCopilotReader` role. The AWS-managed `ReadOnlyAccess` and
`SecurityAudit` policies both include these. Implementation step:
confirm the onboarding CloudFormation role
(`platform/cfn/aws-onboard.yaml`) grants them; add an explicit grant if
the role is more narrowly scoped.

## 11. Testing

`region_discovery.py` is unit-tested with `botocore.stub.Stubber`:

- `describe_regions` + per-region `GetResources` — a mix of populated,
  empty, and **erroring** regions; assert the erroring region lands in
  `active_regions` (the §4.1 invariant).
- `us-east-1` is always in `active_regions`, even when its sweep is
  empty.
- The pure `classify_regions` helper — populated/empty/errored inputs.
- The explicit-`regions`-override path skips discovery (tested at the
  handler-wiring level structurally, since `main.py` is not importable
  in the test venv — see Slice-1 plan Task 7).

## 12. Phasing

A single implementation plan (one slice, ~6–8 tasks): the
`region_discovery.py` module + tests, the `get_enabled_regions()`
override + `AssumedRoleAWSClient` constructor change, the handler step-0
wiring + override handling, the `onboarding_aws_complete` change, the
`scans.scope` enrichment, and a build + deploy + end-to-end
verification.

The E2E verification doubles as the clean, fast Quick-vs-Medium
re-verification that Slice 1 left outstanding — once `ai_pass` is scoped
to active regions, scans complete quickly and the tier difference is
observed directly.

## 13. Open questions / risks

- **`resourcegroupstaggingapi` coverage** — it supports most but not
  every AWS resource type. A region whose *only* resource is an
  unsupported type could be flagged empty. Accepted for v1; the resource
  set is very broad and the highest-value services are covered. Revisit
  with a secondary probe only if a real miss is observed.
- **Reader-role permissions** — if a customer's `CISOCopilotReader` was
  provisioned before this change with a policy lacking `tag:GetResources`,
  their sweep errors everywhere → every region treated active (safe, but
  back to a wide scan) until the role is updated. The §10 step keeps new
  onboardings correct; existing connections degrade safely.
