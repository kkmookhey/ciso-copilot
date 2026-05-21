# AWS Scanner Comprehensiveness Uplift — Design

> Status: draft for review · 2026-05-20
> Scope: **AWS only.** Azure / GCP / Entra get their own follow-on specs
> that reuse this blueprint (per the roadmap item "Scanner
> comprehensiveness uplift").
> Supersedes nothing; extends the existing `shasta_runner` AWS scanner.

## 1. Goal & success criteria

Make CISO Copilot's AWS pull scanner *comprehensive* and *accurate*.

- **Comprehensive** = wide service breadth (~40 AWS services, up from
  Shasta's ~16 areas) + deep per-service checks + four capability
  analyses that go beyond config posture: network reachability, IAM
  identity-graph, vulnerability state, and deployed-code review.
- **Accurate** = reachability-verified exposure, contextual severity,
  zero `not_assessed` noise, an evidence packet on every finding.
- **Measurable** = a living coverage scorecard anchored to the union of
  public benchmarks (CIS AWS Foundations Benchmark, AWS FSBP, PCI DSS
  v4, NIST 800-53 Rev 5).

Success criteria:

1. A Quick scan runs automatically on AWS connect and returns crown-jewel
   findings within a few minutes of first login.
2. The coverage scorecard reports a tracked coverage % per benchmark and
   is regenerated + asserted-current by a test.
3. Medium and Deep tiers add breadth and the capability modules
   respectively, each demoable end-to-end in the web + iOS apps.
4. All new checks are deterministic and ship an evidence packet.

Non-goals (explicitly out of scope for this spec):

- Azure / GCP / Entra uplift (own specs).
- Payment / metering / per-scan-pricing plumbing — this spec adds the
  `scan_tier` input and a default-deny **entitlement gate**; granting
  entitlements is a separate monetization spec.
- Crown-jewel graph traversal / attack-path analysis (roadmap #5). This
  spec emits the *primitives* — effective reachability, effective
  permissions, priv-esc edges — that #5 will traverse.
- Editing Shasta. Shasta stays a frozen, read-only dependency; all new
  checks live in *this* repo.

## 2. Background — current state

`platform/lambda/shasta_runner/` (the AWS scanner) today:

- Runs 16 Shasta cloud modules + `ai_pass` (Shasta AI discovery + checks).
- Has in-repo entity enumeration (`enumerate_iam/storage/compute/network`)
  and the `ai_pass.py` precedent for adding capability outside Shasta.
- Writes transactionally via `unified_writer.commit_scan` — entities and
  findings UPSERT on natural keys, evidence packets persist, framework
  data persists.
- Runs as a **Lambda container image**, ~13 min wall-clock.

Gaps this spec closes: whole services never inspected; thin depth within
covered services; no code/identity/reachability/vuln analysis; no
coverage measurement; a 15-min compute ceiling that the uplift exceeds.

## 3. Tiered scan model

Three tiers. Every `Check` and capability module declares a **minimum
tier**; the registry filters by the scan's `scan_tier`. One scanner
image, one registry, three behaviours.

| Tier | Trigger | Contents | Target wall-clock |
|---|---|---|---|
| **Quick** | auto on AWS connect; first-login result | ~40–60 crown-jewel posture checks (`min_tier=quick`), cheap describe-calls only, no capability modules | a few minutes |
| **Medium** | scheduled (monthly / quarterly) | full coverage engine — all ~40 services, every posture check, all enabled regions — + full Shasta modules + **light** capability passes (reachability, identity-graph, vuln-state read) | ~10–20 min |
| **Deep** | on-demand, behind the entitlement gate | everything in Medium + **heavy** capabilities: `code_review`, full `vuln_state` (image scanning), exhaustive `identity_graph` priv-esc enumeration | ~30–90 min |

Crown-jewel Quick checks are cherry-picked across services (public
S3/RDS/snapshots, security groups open to `0.0.0.0/0`, root-account MFA,
CloudTrail disabled, no default encryption, weak IAM password policy,
public AMIs, …).

## 4. Compute model

**Uniform ECS Fargate task.** The existing scanner container image runs
as a Fargate task for all three tiers; `scan_tier` and scan parameters
are passed as container env / `RunTask` overrides.

Rationale: one image, one invocation path, **no 15-minute ceiling ever**.
Medium scans brush against Lambda's cap on large accounts; Deep scans
exceed it outright. Per-scan compute cost is pennies (~$0.004 Quick,
~$0.012 Medium, ~$0.20 Deep) and Fargate is marginally cheaper than
Lambda for long CPU-bound runs — cost is not the driver, the ceiling is.

Region fan-out stays *inside* the task (one task scans all enabled
regions) — Fargate's lack of a time limit removes the reason to fan out
across Step Functions.

## 5. Architecture & module layout

Shasta's 16 modules + `ai_pass` keep running **unchanged**. Two new
packages are added under `shasta_runner/app/`:

```
shasta_runner/app/
  coverage/                   ← posture check engine (new)
    benchmarks/*.json          vendored CIS / FSBP / PCI / NIST control catalogs
    collectors/*.py            one per AWS service: paginated, error-wrapped
                               API fetch → normalized resource snapshot, memoized
    checks/*.py                declarative Check objects grouped by service
    registry.py                every Check registered — single source of truth
    engine.py                  per region: run collectors, run checks, emit
    scorecard.py               generate the coverage scorecard from the registry
  capabilities/               ← 4 standalone analysis modules (new)
    reachability.py
    identity_graph.py
    vuln_state.py
    code_review.py
```

Each capability module exposes `run(session, ctx, tier) -> {entities,
edges, findings}` — the `ai_pass.run_ai_pass` shape. `main.py`'s handler
gains one new block that runs the coverage engine + the capability
modules (tier-filtered), merges their emissions with the Shasta path,
and commits through the existing transactional
`unified_writer.commit_scan`. `FindingEmission` / `EntityEmission` are
unchanged — they already carry frameworks, evidence, domain, status,
region, confidence.

**Deconfliction rule:** a check is owned by exactly one of {Shasta,
coverage engine}. The engine targets the *gap* — services Shasta ignores
entirely + depth Shasta misses within services it does cover. The
Slice-0 gap analysis maps Shasta's existing checks against the benchmark
union so the engine never duplicates a check.

## 6. Posture coverage engine

**Gap analysis (Slice-0 deliverable).** Vendor the benchmark-union
control catalogs as JSON in `coverage/benchmarks/` (CIS AWS Foundations
Benchmark, AWS FSBP, PCI DSS v4, NIST 800-53 Rev 5). Map Shasta's
existing ~289 cloud checks against the union. The uncovered set *is* the
engine's check backlog.

**Collectors** (`coverage/collectors/<service>.py`) — each takes the
assumed-role session + region, calls the service's list/describe APIs
(paginated, every call wrapped: an API or permission failure marks that
resource type `not_assessed` in scan stats and is **never** emitted as a
finding), returns a normalized list of resource dicts. Memoized per
(service, region) so many checks share one fetch.

**Checks** (`coverage/checks/<service>.py`) — a `Check` dataclass:

- `check_id` — stable, unique, non-colliding with Shasta check IDs.
- `service`, `title`, base `severity`, `resource_type`.
- `min_tier` — `quick` | `medium` | `deep`.
- `frameworks` — control IDs across the benchmark union.
- `evaluate(resource, account_ctx) -> Outcome` — pure, deterministic
  (no LLM); returns `pass` / `fail` / `partial` + per-resource evidence
  + remediation.

**Registry** (`coverage/registry.py`) — every check registered; the
single source of truth that both the tier filter and the scorecard read.

**Engine** (`coverage/engine.py`) — given session + regions +
`scan_tier`: filter the registry by tier → run the collectors those
checks need → run each check over each resource → emit an
`EntityEmission` per discovered resource (new kinds: `ecs_cluster`,
`sqs_queue`, `secretsmanager_secret`, `apigateway_api`, …) + `aws_account
contains` edges + a `FindingEmission` per outcome. `pass`/`fail`/
`partial` are emitted; `not_assessed` is dropped.

**Breadth target** — the ~40 services FSBP touches that Shasta skips:
ECS/EKS/ECR, API Gateway, SNS, SQS, Secrets Manager, ElastiCache, Step
Functions, WAF, DynamoDB, EFS, Glue, Athena, Route 53, CloudFormation,
SSM, OpenSearch, Kinesis, EMR, DocumentDB, MQ, CodeBuild, Elastic
Beanstalk, and others surfaced by the gap analysis.

## 7. Capability modules

Each exposes `run(session, ctx, tier) -> {entities, edges, findings}`.

### 7.1 `reachability.py` — Medium-light · Deep-full

Collects VPCs, subnets, route tables, IGW/NAT, NACLs, security groups,
ENIs, ELB/ALB/NLB, EC2 + public IPs, plus resource-policy exposure (S3
Block Public Access, RDS `publicly_accessible`, …). Computes whether each
internet-capable resource is *actually* reachable from `0.0.0.0/0` and on
which ports — intersecting SG ingress ∩ NACL ∩ route-to-IGW ∩ public-IP.

Emits: `reachability` attributes on the resource entity,
`route_to_internet` edges, reachability-verified `internet_exposed`
findings with severity scaled by port sensitivity (22 / 3389 / database
ports rank higher). In-repo computed — no clean OSS drop-in, and it is
the primitive layer roadmap #5 builds on.

- **Medium** — SG + route-table set-math.
- **Deep** — NACL precision + effective-path edges.

### 7.2 `identity_graph.py` — Medium-light · Deep-full

Collects IAM users / roles / groups / policies (managed + inline),
instance profiles, trust policies, access keys + last-used, account
password policy + credential report. Computes effective permissions per
principal, wildcard / over-broad policy detection, the ~20 classic
privilege-escalation patterns (`iam:CreatePolicyVersion`, `iam:PassRole`
+ `lambda:CreateFunction`, `AttachUserPolicy`, …), cross-account trust to
non-allowlisted accounts, unused credentials older than 90 days.

Emits: principal entities, `can_assume` / `can_escalate_to` edges,
privilege-escalation findings graded by `confidence`.

- **Medium** — wildcard + unused credentials + cross-account trust.
- **Deep** — exhaustive priv-esc path enumeration.

### 7.3 `vuln_state.py` — Medium-read · Deep-full

- **Medium** — read AWS Inspector v2 findings if Inspector is enabled
  (native EC2 + ECR image + Lambda CVE coverage — best leverage). If
  Inspector is off: emit a high-severity "enable Inspector" finding and
  fall back to deprecated Lambda runtimes / EOL AMIs.
- **Deep** — actively Trivy-scan ECR images.

Emits CVE findings linked to the resource entity, severity from CVSS /
EPSS.

### 7.4 `code_review.py` — Deep only

For each Lambda: `GetFunction` → download the deployment package → run
**Semgrep** (security ruleset) + a secrets scanner (gitleaks /
detect-secrets) + flag deprecated runtimes. EC2 user-data, Step Functions
state-machine definitions, and deployed CloudFormation templates are
scanned similarly. Wraps OSS rule engines — all deterministic, no LLM.
Budgeted: caps on package count and per-package size to bound scan time.
Emits findings keyed to the function / instance entity with
code-location evidence.

## 8. Coverage scorecard

`coverage/scorecard.py` reads three inputs — the check registry, a static
manifest of what Shasta's modules cover, and the capability-module
manifests — and maps every check to the vendored benchmark catalogs via
each check's declared `frameworks`.

Output: `docs/coverage/aws-scorecard.md` (+ a `.json` sibling) reporting,
per benchmark (CIS / FSBP / PCI / NIST): total controls, covered, coverage
%, and the explicit uncovered list. Regenerated by
`scripts/gen_scorecard.py`; a unit test asserts the committed file is
current so coverage % cannot silently rot.

## 9. Data model

- **No migration** for `findings` / `entities` — `FindingEmission` /
  `EntityEmission` already carry frameworks, evidence, domain, status,
  region, confidence.
- New entity `kind`s — one per newly covered service.
- New edge `kind`s — `route_to_internet`, `can_assume`,
  `can_escalate_to`.
- **One migration** — add a `tier` column to the `scans` table so the
  app can show which depth ran.

## 10. Accuracy

Three concrete levers:

1. `not_assessed` results (permission denied / API error) never become
   findings — recorded in `module_stats` only.
2. Severity is contextual — a check declares a base severity, and
   `reachability.py` may *escalate* an exposure finding when the resource
   is genuinely internet-reachable.
3. Every finding ships a structured evidence packet (the resource
   snapshot subset + the rule that fired + remediation) — the
   "every conclusion carries evidence" invariant.

## 11. Entitlement gate

A Deep scan dispatch calls `entitlements.check(tenant_id, "deep_scan")`.
For now that is a single function with a simple backing (a
`tenant_entitlements` row, **default-deny**). The plumbing that *grants*
the entitlement (payment, metering, per-scan pricing) is the separate
monetization spec. Quick and Medium are ungated. The gate lives at the
dispatch boundary (the API / onboarding code that issues `RunTask`), not
inside the scanner — the scanner trusts its `scan_tier` input.

## 12. Triggering

- **Quick** — onboarding completion auto-dispatches a Quick Fargate task.
- **Medium** — dispatched on a schedule (a thin EventBridge rule per
  tenant cadence; richer scheduling UX is a follow-up).
- **Deep** — dispatched on-demand from the app, behind the gate.

## 13. Infrastructure (CDK)

`platform/lib/scan-stack.ts` gains an ECS cluster + a Fargate task
definition pointing at the existing scanner image; `scan_tier` + scan
params arrive as container env / `RunTask` overrides. The Lambda invoke
path is replaced by `ecs:RunTask`. Semgrep + Trivy are added to the
scanner Dockerfile for the Deep path.

## 14. Testing

TDD per `CLAUDE.md`. The engine, each collector, and each check are
unit-tested with `moto` or recorded fixtures; capability modules are
tested with fixtures; the golden-fixture pattern matches the existing
detector tests. A scorecard-freshness test asserts the committed
scorecard file is current.

## 15. Phasing — vertical slices

Each slice is demoable end-to-end and gets its own implementation plan.

- **Slice 0** — benchmark catalogs + gap analysis + scorecard skeleton +
  `scans.tier` column + Fargate task in CDK. *Demo:* scorecard committed
  showing today's baseline %; a Quick scan runs on Fargate.
- **Slice 1** — check engine + collector / registry framework + first ~3
  new services end-to-end + tier filtering. *Demo:* new findings in the
  app; Quick vs Medium visibly differ.
- **Slice 2** — broad posture fill (remaining ~35 services). *Demo:*
  scorecard coverage jumps.
- **Slice 3** — `reachability.py`. *Demo:* reachability-verified exposure
  findings.
- **Slice 4** — `identity_graph.py`. *Demo:* priv-esc findings + edges.
- **Slice 5** — `vuln_state.py`. *Demo:* CVE findings.
- **Slice 6** — `code_review.py` + entitlement gate enforced. *Demo:* a
  Deep scan reviews Lambda code, gated.

Slices 0–2 are the comprehensiveness core; 3–6 add the capability
modules.

## 16. Open questions / risks

- **Scan-time budget for Deep** — `code_review` downloading every Lambda
  package can be slow on large accounts; the package-count / size caps in
  §7.4 need real-world tuning. Mitigated by Fargate having no time limit.
- **Benchmark catalog maintenance** — vendored CIS / FSBP / PCI / NIST
  JSON drifts as AWS publishes new versions; the scorecard test catches
  *our* drift, not the upstream catalogs'. Refresh is a manual periodic
  task.
- **Shasta-vs-engine deconfliction** — depends on an accurate Shasta
  coverage manifest; if Shasta's coverage is mis-stated the engine may
  duplicate or leave a gap. The gap analysis in Slice 0 must be done
  carefully against Shasta source.
