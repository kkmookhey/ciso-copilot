# SP1 — Unified Entity + Edge Model

> **Status:** approved 2026-05-19. First sub-project of the platform-wide
> arc replacing AI-specific data shapes with a domain-agnostic entity +
> edge graph that cloud, AI, and future ASM / attack-path / identity
> scanners all write into.
>
> **Author:** Claude (per KK's direction).
> **Predecessors:** Slice 1a (GitHub App + repo picker) + Slice 1b
> (scanner + 8 detectors + AI Inventory) — both shipped.
> **Sibling sub-projects** (later, see `docs/future_todos.md`): SP2
> Generic Trust Graph + Inventory UI · SP3 Unified Findings + Risk
> Register UX · SP4 Chat-first front door · SP5 Voice on Web · SP6
> Dynamic Dashboards from Chat.

---

## 1. Goal

A single Aurora schema — `entities` + `edges` + `findings.subject_entity_id`
— that every scanner writes into, so any later platform feature
(trust graph, AI risks, cross-domain blast radius, attack paths,
unified inventory) reads from one source of truth.

The AI scanner stops writing to `ai_assets` / `ai_relationships`; cloud
scanners (`shasta_runner_*`) gain entity emission alongside their
existing findings; the cross-domain piece (GitHub Actions → AWS IAM
role) ships as a new detector inside the AI scanner.

## 2. Non-goals

This sub-project does **not**:

- Ship a trust-graph visualization (that's SP2).
- Consolidate the findings/risks UX (that's SP3).
- Touch the chat surface or voice (SP4 / SP5).
- Add ASM / attack-path / threat-intel scanners (later slices).
- Modify the Shasta sub-package at `~/Projects/Shasta`. Shasta is a
  frozen input; all new logic lives in CISO Copilot's
  `platform/lambda/shasta_runner_*` wrappers.
- Enumerate Azure / GCP / Entra cloud entities. Those follow once AWS
  proves the pattern.

## 3. Decisions log (Q1–Q5, with rationale)

| # | Decision | Why |
|---|---|---|
| Q1 | Brand new `entities` + `edges` tables; migrate existing `ai_assets` / `ai_relationships` into them | Clean break beats `domain`-column-on-existing-table; future_todos has 5+ entity-shaped consumers (cloud, ASM, attack paths, identity, AI) that all win from one model |
| Q2 | `shasta_runner` Lambdas derive entities + edges from Shasta's existing finding output AND do 4 targeted boto3 enumeration passes (IAM, storage, compute, network). **No changes to `~/Projects/Shasta` itself.** | KK directive: Shasta is an input building block, not a moving target. All entity logic stays in CISO Copilot. |
| Q3 | Within-domain edges (cloud→cloud, AI→AI) plus one cross-domain heuristic: GitHub Actions deploy detection → `github_repo → deploys_to → aws_iam_role` | Unlocks the "code-secret → cloud-blast-radius" narrative for SP2's demo without inventing LLM-assisted edge inference (which would violate the determinism invariant) |
| Q4 | Add nullable `findings.subject_entity_id UUID REFERENCES entities(id)`, backfilled from `resource_arn` during migration | "All findings on this entity" becomes one indexed join in SP3; FK nullability tolerates pre-backfill rows and resource-less findings |
| Q5 | Big-bang single feature branch, ~7-10 days, with old tables held back for 1-week soak before drop | KK is the only production customer; brief deploy window is acceptable; eliminates shim code that would otherwise rot |

Locked invariants that this sub-project respects:
- Determinism is the spine. No LLM writes to `entities` or `edges`.
- Every emission carries a Trust Evidence Packet.
- AI prefix kept (`ai_framework`, `ai_model`, …) — explicit beats clean.
- Tenant-scoped uniqueness everywhere: `UNIQUE (tenant_id, kind, natural_key)`.

## 4. Schema

```sql
-- platform/sql/005_unified_entities.sql

CREATE TABLE entities (
  id               UUID         PRIMARY KEY,
  tenant_id        UUID         NOT NULL REFERENCES tenants(tenant_id),
  kind             TEXT         NOT NULL,
  natural_key      TEXT         NOT NULL,
  display_name     TEXT         NOT NULL,
  domain           TEXT         NOT NULL
                                CHECK (domain IN ('cloud', 'ai', 'asm', 'identity', 'repo')),
  attributes       JSONB        NOT NULL DEFAULT '{}'::jsonb,
  evidence_packet  JSONB,
  detector_id      TEXT         NOT NULL,
  detector_version TEXT         NOT NULL,
  scan_id          UUID,
  first_seen_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  last_seen_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  UNIQUE (tenant_id, kind, natural_key)
);

CREATE INDEX entities_tenant_kind_idx   ON entities(tenant_id, kind);
CREATE INDEX entities_tenant_domain_idx ON entities(tenant_id, domain);

CREATE TABLE edges (
  id                UUID         PRIMARY KEY,
  tenant_id         UUID         NOT NULL REFERENCES tenants(tenant_id),
  source_entity_id  UUID         NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  target_entity_id  UUID         NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  kind              TEXT         NOT NULL,
  attributes        JSONB        NOT NULL DEFAULT '{}'::jsonb,
  evidence_packet   JSONB        NOT NULL,
  detector_id       TEXT         NOT NULL,
  detector_version  TEXT         NOT NULL,
  scan_id           UUID,
  first_seen_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  last_seen_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  UNIQUE (source_entity_id, target_entity_id, kind)
);

CREATE INDEX edges_tenant_idx ON edges(tenant_id);
CREATE INDEX edges_source_idx ON edges(source_entity_id);
CREATE INDEX edges_target_idx ON edges(target_entity_id);

-- Finding linkage
ALTER TABLE findings ADD COLUMN subject_entity_id UUID REFERENCES entities(id);
CREATE INDEX findings_subject_entity_idx ON findings(subject_entity_id)
  WHERE subject_entity_id IS NOT NULL;
```

Notes:

- `kind` is free-text (no Postgres ENUM). The unique constraint is the
  real guarantee; a misspelled kind produces an orphan node, not data
  corruption. Detector unit tests pin valid kinds.
- `domain` IS a CHECK-constrained enum — coarse enough that the set is
  stable; fine-grained taxonomy lives in `kind`.
- Entities are tenant-scoped. No global rows even for "common" assets
  like `openai/gpt-4o-mini`; multi-tenancy security boundary preserved.
- `ON DELETE CASCADE` on edges → deleting an entity tidies its edges.

## 5. Entity kinds (initial set)

| Domain | Kinds | `natural_key` format |
|---|---|---|
| **ai** | `ai_framework`, `ai_model`, `ai_mcp_server`, `ai_tool`, `ai_agent`, `ai_vector_db`, `ai_embedding`, `ai_prompt` | `ai_model`: `{provider}/{model_id}` (e.g. `openai/gpt-4o-mini`) · `ai_framework`/`ai_vector_db`: the bare name (e.g. `langchain`) · `ai_mcp_server`/`ai_tool`/`ai_agent`/`ai_prompt`: `{repo_url}::{source_path}::{name}` (per-file scope) |
| **repo** | `github_repo`, `github_actions_workflow` | `github.com/{owner}/{name}` · workflow: `github.com/{owner}/{name}/.github/workflows/{file}` |
| **cloud** | `aws_account`, `aws_s3_bucket`, `aws_iam_role`, `aws_iam_user`, `aws_lambda_function`, `aws_ec2_instance`, `aws_vpc`, `aws_subnet`, `aws_security_group` | `aws_account`: account_id · all others: the full ARN |
| **identity** | `entra_tenant`, `entra_user`, `entra_app_registration` *(future)* | tenant: tenant UUID · user: object_id |

Models and frameworks deduplicate **across repos** in the same tenant —
two repos both importing langchain edge into one `ai_framework`
entity. Per-file kinds (`ai_agent`, `ai_prompt`) stay repo-scoped so
the same prompt text in two different files remains two entities (they
have different blast radius).

## 6. Edge kinds (initial set)

| Kind | Typical shape | Source |
|---|---|---|
| `uses` | `github_repo → ai_framework`, `github_repo → ai_vector_db` | AI scanner |
| `calls` | `github_repo → ai_model`, `ai_agent → ai_model` | AI scanner |
| `accesses` | `github_repo → ai_prompt` | AI scanner |
| `generates` | `github_repo → ai_embedding` | AI scanner |
| `deploys` | `github_repo → ai_mcp_server` | AI scanner |
| `invokes` | `ai_agent → ai_mcp_server`, `ai_mcp_server → ai_tool` | AI scanner |
| `orchestrates` | `ai_agent → ai_model` | correlator |
| `retrieves` | `ai_model → ai_vector_db` | correlator |
| `contains` | `aws_account → aws_vpc → aws_subnet → aws_ec2_instance` | shasta_runner |
| `assumes` | `aws_ec2_instance → aws_iam_role`, `aws_lambda_function → aws_iam_role` | shasta_runner |
| `can_access` | `aws_iam_role → aws_s3_bucket` (derived from IAM policy) | shasta_runner (later commits) |
| `member_of` | `aws_iam_user → aws_account` | shasta_runner |
| **`deploys_to`** | `github_repo → aws_iam_role` (the role assumed by the repo's GitHub Actions OIDC trust) | AI scanner (new `crossdomain.py` detector) |

## 7. Cloud entity ingestion in `shasta_runner` (all CISO Copilot, no Shasta edits)

After Shasta returns findings, the runner does **two passes** before writing:

### 7.1 ARN extraction from findings (free)

For each finding with `resource_id` starting with `arn:aws:`:

1. Parse the ARN to infer `kind` (`arn:aws:s3:::bucket` → `aws_s3_bucket`).
2. Upsert an entity row keyed by `natural_key = resource_arn`.
3. Set `finding.subject_entity_id = entity.id`.

Also: the runner already has `account_id`, so it always upserts an
`aws_account` entity at the top of every scan and emits
`aws_account → contains → resource` edges for every parsed ARN.

### 7.2 Targeted boto3 enumeration (modest cost)

Using the same assumed `CISOCopilotReader` credentials Shasta already
uses (no IAM additions), per region:

| Kind | boto3 calls | Entities + edges emitted |
|---|---|---|
| **IAM** | `iam.list_roles`, `iam.list_users`, `iam.get_role` (for trust docs) | every role and user as entities; `aws_iam_role → assumes → aws_iam_role` edges from cross-role trusts |
| **Storage (S3)** | `s3.list_buckets`, `s3.get_bucket_location` | every bucket as entity; `aws_account → contains → aws_s3_bucket` |
| **Compute** | `ec2.describe_instances`, `lambda.list_functions` | all EC2 + Lambda; `aws_ec2_instance → assumes → aws_iam_role` (instance profile); `aws_lambda_function → assumes → aws_iam_role` |
| **Network** | `ec2.describe_vpcs`, `ec2.describe_subnets`, `ec2.describe_security_groups` | VPC topology; `aws_vpc → contains → aws_subnet`, `aws_vpc → contains → aws_security_group` |

The four enum passes run inside the same transaction as the finding
write — one `commit_scan` call per scan.

### 7.3 Remaining Shasta modules (logging, encryption, database, monitoring, secrets, governance, cloudfront, organizations)

These modules don't get explicit enumeration in SP1. Their findings still
flow through ARN extraction (§7.1), so any resource Shasta flags gets an
entity row. Full inventory for these kinds lands in follow-up commits once
SP1's four-module pattern is proven.

## 8. Cross-domain detector — `detectors/crossdomain.py`

A new detector inside the AI scanner Lambda. Same emission shape as the
eight existing detectors (returns a `DetectorResult` with entities +
edges + optional findings).

**Strategy:** the AI scanner already clones the repo, so it can walk
`.github/workflows/*.yml` and `.github/workflows/*.yaml`. For each
workflow:

```yaml
- uses: aws-actions/configure-aws-credentials@v4
  with:
    role-to-assume: arn:aws:iam::$AWS_ACCOUNT_ID:role/GitHubActionsDeployRole
    aws-region: us-east-1
```

Emit:

- Entity (stub if not already present, see §9): `aws_iam_role` with
  `natural_key = arn:aws:iam::$AWS_ACCOUNT_ID:role/GitHubActionsDeployRole`.
- Edge: `github_repo → deploys_to → aws_iam_role`, confidence: `medium`,
  evidence packet cites the workflow file + line + the role ARN string.

Future expansions (out of scope for SP1): Azure `azure/login`, GCP
`google-github-actions/auth`, ECR image pushes, `aws s3 cp` direct
calls. Same pattern, more YAML pattern matching.

## 9. Unified writer (`unified_writer.py` — per-Lambda copy)

A writer module **copied** into each Lambda's source directory rather
than imported across them — same pattern as `anthropic_call.py` lives
in both `platform/lambda/policies/` and `platform/lambda/questionnaires/`.
Lambda zips don't share modules, so the module is duplicated; the
canonical version lives in `platform/lambda/ai_scanner/unified_writer.py`
and is `cp`'d during build into `shasta_runner/` and `entities_api/`
(or the source-of-truth copy moves to the first Lambda touched in the
PR and the others follow). Public surface:

```python
def commit_scan(ctx, *,
                entities: list[EntityEmission],
                edges:    list[EdgeEmission],
                findings: list[FindingEmission]) -> None: ...

def mark_scan_failed(ctx, error_message: str) -> None: ...
```

### 9.1 Transaction shape

```
BEGIN
  for e in entities:
      INSERT ... ON CONFLICT (tenant_id, kind, natural_key)
        DO UPDATE SET last_seen_at = NOW(), ...
        RETURNING id  -- always use returned id, never the assigned one
  for edge in edges:
      src_id = resolve(edge.source_natural_key) or stub_or_skip()
      tgt_id = resolve(edge.target_natural_key) or stub_or_skip()
      INSERT ... ON CONFLICT (source, target, kind) DO UPDATE SET last_seen_at = NOW()
  for f in findings:
      f.subject_entity_id = resolve(f.subject_natural_key)  -- may stay NULL
      INSERT findings ...
  UPDATE ai_scans SET status = 'success', counts = ...
COMMIT
```

### 9.2 Stub entities for cross-domain edges (Q3 resolution)

When an edge references an entity by `natural_key` that's not in the
current scan's emissions, the writer:

1. Looks up the entity in `entities` by `(tenant_id, kind, natural_key)`.
2. If found, links the edge to that id.
3. If not found, INSERTs a stub:
   `attributes = {"_stub": true}`,
   `display_name = natural_key`,
   `detector_id = "manual.stub"`,
   `evidence_packet = NULL`,
   and links the edge to the stub's id.

When the responsible scanner later runs, its `ON CONFLICT DO UPDATE`
hydrates the stub: real `display_name`, real `attributes`, real
`evidence_packet`, and clears `_stub`. The graph UI in SP2 will treat
`_stub=true` as a hint ("not yet scanned").

### 9.3 Regressions covered by writer tests

Two bugs paid in Slice 1b debugging time stay covered:

1. Repo-rooted edges resolve against a pre-seeded entry. The writer
   adds `f"github_repo::{repo_url}" → repo_entity_id` to the resolution
   map at the start of each scan (or pre-upserts the repo entity from
   `ctx`).
2. `ON CONFLICT DO UPDATE RETURNING id::text` — never trust the
   client-side assigned UUID after an UPSERT; the existing row's id
   wins on conflict and the relationship FK requires it.

## 10. API surface

The `ai_scan_api` Lambda is **renamed** to `entities_api`. The CDK
construct + function name update; existing routes either stay (where
they're scan-specific) or get replaced (where they were ai-asset
listings).

| Old route | New route | Behavior |
|---|---|---|
| `POST /v1/ai/scans` | `POST /v1/ai/scans` (unchanged) | Triggers an AI scan. AI scans remain a distinct concept post-unification. |
| `GET  /v1/ai/scans` | `GET  /v1/ai/scans` (unchanged) | — |
| `GET  /v1/ai/scans/{id}` | `GET  /v1/ai/scans/{id}` (unchanged) | — |
| `GET  /v1/ai/assets` | `GET  /v1/entities?domain=ai` | Filterable list; supports `?domain=`, `?kind=`, `?repo=`, `?page=` |
| `GET  /v1/ai/assets/{id}` | `GET  /v1/entities/{id}` | Detail + attributes + evidence packet |
| *(new)* | `GET  /v1/entities/{id}/graph?depth=4&max_nodes=500` | Recursive CTE rooted at the entity, returns cytoscape-shaped JSON. Used by SP2. |
| *(new)* | `GET  /v1/entities/{id}/relationships?direction=both` | Flat list of edges with other-entity stubs joined in. |
| *(new)* | `GET  /v1/findings?entity_id=...` | Findings filtered by `subject_entity_id`. Reuses existing findings handler. |

The graph endpoint uses one recursive CTE capped at depth 4 and 500
nodes, with `meta.truncated = true` flag when limits hit.

## 11. Web + iOS impact

UI components keep their current shape — only the fetch layer changes.

**Web** (`web/src/`):

- `lib/api.ts`: types renamed `AIAsset* → Entity*`. Methods renamed
  `listAIAssets → listEntities`, `getAIAsset → getEntity`. Plus new
  `getEntityGraph` and `getEntityRelationships`.
- `routes/AIInventory.tsx`: fetches `listEntities({domain: 'ai'})`;
  filter chips stay the same (filter by kind chip values).
- `routes/AssetDetail.tsx`: fetches `getEntity(id)`. Cosmetic field
  renames only.
- `routes/RepoPicker.tsx`: no change (still triggers AI scans).

**iOS** (`ios/CISOCopilot/Services/`):

- `APIClient.swift`: `listAIAssets() → listEntities(domain:)`,
  `getAIAsset(_:) → getEntity(_:)`. Types renamed `AIAssetSummary →
  EntitySummary`, `AIAssetDetail → EntityDetail`.
- `Views/AI/*`: literally one `let asset` → `let entity` rename per
  view body. The AI tab keeps its name and icon; under the hood it
  passes `domain: "ai"` to `listEntities`.

No URL changes on either client. No iOS rebuild required unless the
type rename causes a recompile (it will).

## 12. Migration runbook

One feature branch: **`feat/sp1-unified-entities`**.

Commits land in this order on the branch (one PR for commits 1–10,
follow-up PR for commit 11 after a week of soak):

1. **`feat(sql)`** `005_unified_entities` — entities + edges tables +
   `findings.subject_entity_id` column.
2. **`feat(platform)`** unified_writer module (copy into each Lambda)
   + unit tests covering transactional semantics, ON CONFLICT
   RETURNING, repo-root pre-seeding, and stub-upgrade.
3. **`refactor(platform)`** ai_scanner — emission classes renamed
   (`AssetEmission → EntityEmission`, `RelEmission → EdgeEmission`),
   per-detector goldens regenerated, all 32 tests stay green.
4. **`feat(platform)`** ai_scanner — new `detectors/crossdomain.py`
   for GitHub Actions deploy detection, with fixtures.
5. **`feat(platform)`** shasta_runner — ARN extraction + four enum
   passes (IAM, storage, compute, network), unified_writer adoption,
   unit tests with mocked boto3 clients.
6. **`refactor(platform)`** ai_scan_api → entities_api, 5 routes
   rewritten against entities/edges, two new endpoints (`graph`,
   `relationships`).
7. **`feat(platform)`** CDK — rename Lambda function name, route
   shape stays + adds `/v1/entities*`.
8. **`migration`** `platform/scripts/migrate_to_entities.py` — one-off
   data migration. See §13 for details.
9. **`feat(web)`** api.ts type + method renames; AIInventory.tsx and
   AssetDetail.tsx fetch updates; no UX changes.
10. **`feat(ios)`** APIClient + view renames mirroring web.
11. **`chore`** *(separate PR, ~1 week later)* drop `ai_assets` and
    `ai_relationships` tables.

## 13. Data migration script

`platform/scripts/migrate_to_entities.py` — Python script run **once
from KK's laptop** using the `rds-data` CLI under the same credentials
we already use for ad-hoc queries (no Lambda packaging overhead for a
one-shot tool). Idempotent (re-runnable; uses ON CONFLICT DO NOTHING
throughout) so a partial failure can be replayed without dupes.

Logical steps:

1. **Build natural_key per ai_assets row** using the table in §5.
2. **Upsert entities** — INSERT INTO entities … ON CONFLICT DO NOTHING.
   Models and frameworks dedupe automatically because their
   natural_key collapses across repos.
3. **Build remap table** — `old_ai_asset_id → new_entity_id` via
   `SELECT id FROM entities WHERE tenant_id = … AND kind = … AND
   natural_key = …`.
4. **Upsert edges** — INSERT INTO edges using the remapped ids.
   Same kind name (`uses`, `calls`, etc.).
5. **Backfill `findings.subject_entity_id`** — for each finding with
   a resolvable ARN, look up the entity and `UPDATE`. Findings with
   unresolved ARNs stay NULL.
6. **Print summary**: entity counts by kind, edge counts by kind,
   findings linked / total, model-dedup delta (proves cross-repo
   models collapsed).

Smoke test before running on live: count `(tenant, ai_model, name)`
triples in ai_assets — assert the entities count for `ai_model` is ≤
the ai_assets count. Log the delta as proof of unification.

## 14. Testing strategy

**Unit (gate the PR merge):**

- `unified_writer`: regressions for (a) repo-rooted edges resolving
  against the pre-seeded entry, (b) ON CONFLICT RETURNING capturing
  the persisted id. New: stub upgrade test (insert stub, second scan
  upgrades it, confirm `_stub` cleared).
- Per-detector goldens for ai_scanner: regenerated against the new
  emission types; same coverage as today.
- `detectors/crossdomain.py`: fixtures with two synthetic workflow
  YAML files (one with `aws-actions/configure-aws-credentials`, one
  without). Golden output asserts the `deploys_to` edge with the
  right role ARN.
- `shasta_runner`: per-kind tests using `botocore.stub.Stubber` to
  mock `iam.list_roles`, `s3.list_buckets`, `ec2.describe_*`,
  `lambda.list_functions`. Assert entity counts and edge topology.
- `entities_api`: per-route tests covering filtering, pagination,
  graph CTE depth/node caps, relationships direction filtering.

**Integration:**

- `migrate_to_entities.py` against a fixture old-schema database
  snapshot. Assert entity dedup happened, edge count matches,
  finding FK populated where expected.

**E2E live on KK's tenant (the demo gate):**

- Re-run AWS scan → expect 4× more entities than findings (every
  IAM role, bucket, EC2, etc., not just the ones with issues).
- Re-run AI scan on `kkmookhey/ciso-copilot` → existing 3 assets
  migrate cleanly + new `deploys_to` edge if the repo has a GitHub
  Actions workflow that uses an AWS IAM role.
- Verify iOS AI tab still renders against the new endpoint.
- Spot-check `GET /v1/entities/{aws_account_id}/graph?depth=3`
  via curl + the JSON shape matches what SP2 will consume.

## 15. Rollback

The old `ai_assets` + `ai_relationships` tables stay alive for 1 week
post-deploy (commit 11 holds back the DROP). If anything goes
sideways:

1. Revert the Lambda + web + iOS deploys to the prior versions.
2. The old tables still hold pre-migration data; old code reads them
   normally.
3. The new `entities`/`edges` tables can stay empty — they're
   unreferenced in the reverted state.

Soak window observations to watch: scanner Lambda CloudWatch error
rates, `entities` table row counts holding steady (no dupes
appearing), `findings.subject_entity_id` populated for ≥80% of new
findings.

## 16. Effort estimate

**8–10 working days** for one engineer. Detailed split:

| Phase | Days |
|---|---|
| Schema migration + unified_writer module + tests | 1 |
| ai_scanner refactor (rename emissions, regenerate goldens, ship `crossdomain.py`) | 2 |
| shasta_runner (ARN extraction + 4 enum passes + writer adoption) | 2 |
| entities_api Lambda (5 routes including graph + relationships) | 1.5 |
| Data migration script + dry-run on KK's tenant + verify | 1 |
| Web + iOS rewire (api.ts + APIClient.swift) | 1 |
| E2E demo + buffer for surprises | 1 |

## 17. Out of scope

The discipline boundary for SP1:

- Cytoscape graph viz (SP2)
- AI Risks tab consolidation (SP3)
- Chat surface and dynamic dashboards (SP4 / SP6)
- Voice on web (SP5)
- Per-domain inventory pages for cloud / Azure / GCP — SP2
  generalizes the existing `/ai/inventory` page into one filtered
  inventory; SP1 does not add new routes.
- Azure / GCP / Entra enumeration in shasta_runner — follow-up commits
  once AWS proves the pattern.
- LLM-assisted cross-domain edge inference — violates the determinism
  invariant; deferred indefinitely.
- Dropping the legacy `ai_assets` / `ai_relationships` tables — separate
  follow-up commit after 1-week soak.

## 18. Open questions parked for later

- **Entity supersession.** What happens when a scanner emits an entity
  that should *replace* an existing one (e.g., S3 bucket renamed)?
  SP1 treats them as different entities (different ARN ⇒ different
  natural_key). Manual merge workflow comes later.
- **Per-tenant kind taxonomy extensions.** A customer with a custom
  CRM might want a `salesforce_org` kind. The free-text `kind` column
  supports it, but UI conventions (color, icon) live in code. Plugin
  point comes in SP2 / SP4.
- **Edge confidence display.** Today we have `attributes.confidence`
  on the evidence packet. SP2's graph viz should expose it
  prominently. SP1 just preserves it on emissions.

## 19. References

- v2 PRD: `CISOBrief-v2.md`
- Current state: `HANDOFF.md` (last updated 2026-05-19 after Slice 1b)
- Slice 1 design (predecessor): `docs/superpowers/specs/2026-05-18-ai-security-slice-1-design.md`
- Future capabilities roadmap: `docs/future_todos.md`
- Vision invariants: `~/Projects/Denali/denali-vision.md` §II
