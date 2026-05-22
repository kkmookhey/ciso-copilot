# Scan Tiers — Quick / Medium / Deep

How CISO Copilot's posture scanner depth tiers work, and what each tier
covers per cloud.

> Sources of truth: the scanner code (`platform/lambda/shasta_runner*/`)
> and the design specs `docs/superpowers/specs/2026-05-21-scan-performance-design.md`
> (AWS) and `2026-05-21-azure-scanner-uplift-design.md` (Azure). If this
> doc and the code disagree, the code wins — update this doc.

## The model

Every cloud connection is scanned at one of three depth tiers. Two
principles hold across all clouds:

1. **Tiers vary _depth_, never _scope_.** Every tier scans every
   eligible region (AWS) or every selected subscription (Azure). A
   higher tier does not "discover more" — it runs *more checks* against
   the same surface. No tier reintroduces a coverage blind spot.
2. **Quick is two-phase, for time-to-value.** Quick commits a "First
   Signal" result in ~1 minute, then completes a "Crown Jewel" sweep.
   Medium and Deep are single-phase.

| Tier | Wall-clock target | Purpose |
|---|---|---|
| **Quick** | ~3-5 min (First Signal in ~1 min) | Fast triage — is this account/tenant on fire? |
| **Medium** | ~15-25 min | Full compliance posture — the default scan |
| **Deep** | ~1-2 h | Full posture + heavy capability analysis |

Deep is gated behind a Contact-Us flow in the product (it is the
heaviest tier); Quick and Medium are self-service.

---

## AWS

The AWS scanner runs account-global checks plus per-region checks across
every enabled region. Depth is selected jointly by `(tier, region
state)` — a region with no real resources is scanned shallowly even at
Medium.

| Tier | What runs |
|---|---|
| **Quick — Phase 1 (First Signal)** | Account-global, fast: identity / account summary; the region census (active / default-only / empty / unknown counts); public S3 exposure; IAM root / admin / MFA / password-policy basics; security-service presence (CloudTrail, Config, GuardDuty, Security Hub); the most critical public network-exposure signals. Committed in ~30-90 s. |
| **Quick — Phase 2 (Crown Jewel)** | Per-region, in parallel: internet-exposed compute, permissive security groups, public load balancers, RDS public exposure, KMS / key hygiene, CloudTrail / logging posture, default-VPC / default-SG risk. |
| **Medium** | Everything in Quick, plus the full set of regional Shasta posture modules (compute, storage, networking, encryption, databases, backup, logs, VPC endpoints, KMS, data warehouse, serverless, vulnerabilities) on `active`/`unknown` regions, the in-repo coverage engine's Medium-tier checks, and the AI pass (Bedrock / SageMaker / AI-Lambda discovery + AI checks). |
| **Deep** | Everything in Medium, plus heavy capability modules that wrap OSS tooling (e.g. reachability / attack-path analysis, identity-graph). **Note:** the Deep capability modules are largely roadmap — see the scan-performance spec; until they land, Deep ≈ Medium plus the coverage engine's Deep-tier checks. |

The in-repo **coverage engine** (hand-written checks for SQS, Secrets
Manager, ECR) runs at *every* tier; individual checks are filtered by
their `min_tier`.

---

## Azure

The Azure scanner scans every *selected* subscription (the user picks
which, via the web subscription picker — others are skipped). Within a
subscription, the 12 Shasta Azure check modules run, filtered by tier.
Quick is two-phase, mirroring AWS.

| Tier | Shasta Azure modules run |
|---|---|
| **Quick — Phase 1 (First Signal)** | `iam`, `governance` — identity and subscription-governance posture. Committed early. |
| **Quick — Phase 2 (Crown Jewel)** | `storage`, `networking`, `compute`, `encryption` — public-exposure and data-at-rest crown jewels. |
| **Medium** | Quick's six modules, plus `databases`, `appservice`, `monitoring`. |
| **Deep** | Medium's nine modules, plus `backup`, `diagnostic_settings`, `private_endpoints`. |

---

## GCP and Entra

The GCP and Entra scanners are still the legacy single-pass scanners —
they are **not yet tier-aware**. A rescan runs the full module set
regardless of tier. They will adopt the three-tier model when their
scanner uplift lands (the same uplift AWS and Azure have completed).

---

## Where tiers are set in code

- **AWS:** `platform/lambda/shasta_runner/app/scan_policy.py`
  (`build_scan_plan` — the `(tier × region_state)` depth matrix) and
  `main.py` (`GLOBAL_MODULES` / `REGIONAL_MODULES`, the two-phase Quick).
- **Azure:** `platform/lambda/shasta_runner_azure/app/azure_units.py`
  (`modules_for_tier` — the tier → Shasta-module mapping).
- The scan's chosen tier is stored on the `scans.tier` column; progress
  moves through `scans.phase` (`region_discovery` → `first_signal` →
  `crown_jewel` → `done`, or `… → full → done` for Medium/Deep).
