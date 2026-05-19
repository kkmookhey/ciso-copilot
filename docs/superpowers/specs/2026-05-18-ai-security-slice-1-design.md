# AI Security — Slice 1 Design

> Spec for the first vertical slice of AI-security capabilities inside CISO
> Copilot v2. Companion to `CISOBrief-v2.md` (cloud-side PRD) and to the
> Denali vision/MVP docs at `~/Projects/Denali/` (kept as reference, not as
> a separate codebase).
>
> Date: 2026-05-18
> Status: design approved by KK in brainstorm; awaiting written-spec review
> before the implementation plan is written.

---

## 1. What we are building

CISO Copilot is absorbing AI-security capabilities. Today CISO Copilot
discovers and reasons about *cloud* posture (AWS, soon Azure/GCP/Entra).
This slice adds discovery and reasoning over the *AI surface* of a customer
environment: the models they call, the agents they deploy, the MCP servers
they expose, the vector DBs they retrieve from, the prompts they ship, and
the frameworks that hold it all together.

The work mirrors the Denali MVP vision (`~/Projects/Denali/denali-mvp.md`)
but runs on the existing CISO Copilot stack — same Aurora cluster, same CDK
app, same Cognito, same web + iOS surfaces. There is no separate "Denali"
sub-product, no separate repo, no separate brand inside the UI.

## 2. Slice 1 demo target

> **KK installs the CISO Copilot GitHub App on his own GitHub. He picks a
> repo with real AI code. He clicks Scan. Within a minute the web app
> shows the repo's AI inventory — frameworks, models, MCP servers, vector
> DBs, prompts, agents — each with a deterministic evidence packet. He
> opens the trust graph for that repo and sees the agent calling the model
> calling the vector DB. He opens the AI Risks tab and sees the
> "hardcoded credential in AI module" finding. iOS shows the same
> inventory and risks, read-only.**

That is the demo. No MCP server. No OpenAI/Anthropic connectors. No blast
radius. No cryptographic signing of evidence packets. No autonomous
remediation. Those are subsequent slices.

## 3. Locked decisions feeding this slice

These are reaffirmed from `HANDOFF.md` (decisions of 2026-05-18) and
refined in the brainstorm of the same day:

| # | Decision | Notes |
|---|---|---|
| L1 | Graph storage = Aurora Postgres | Recursive CTEs on `ai_relationships`. No Neptune/Neo4j/Redpanda/OpenSearch. |
| L2 | Dedicated `ai_assets` + `ai_relationships` tables | AIBOM export = `SELECT * ... JOIN ... WHERE tenant_id = $1`. No transform layer. |
| L3 | GitHub connector = GitHub App | One App, all tenants. Per-tenant installation_id. Same one-click parity as the AWS CFN onboarding. |
| L4 (revised) | **MCP server deferred** | Original lock was HTTP-SSE on API Gateway with Cognito JWT. Brainstorm 2026-05-18 changed this: MCP comes after Slice 1. Slice 1 is web + iOS only. `HANDOFF.md` is updated to reflect this. |
| L5 | Naming = "CISO Copilot" everywhere | "Denali" survives only in the reference docs at `~/Projects/Denali/`. No source path, UI string, or table name contains "denali". |
| L6 | Detection scope = wide | All 8 detectors from Denali MVP §6.1 ship in Slice 1: frameworks, model usage, MCP servers, agentic workflows, vector DBs, embeddings, prompts, secrets-in-AI-code. |
| L7 | Onboarding flow = install then user-trigger scans | Not auto-scan-all-repos on install. Customer picks repos from a web UI picker and clicks Scan per repo. Avoids the "why is it scanning my dotfiles" problem at install time. |
| L8 | UI scope = web full + iOS read-only + light trust graph | Web: Connect GitHub + repo picker + AI Inventory + AI Risks + cytoscape.js trust graph (per-repo). iOS: AI Inventory + AI Risks, read-only, no onboarding from iOS. |
| L9 | Evidence packets = format-only, no crypto | Design the packet as an open spec (versioned JSON), store inline as JSONB on each emitting row. KMS-signing deferred. AI-side only; cloud findings not retrofitted. |
| L10 | Sequencing = three vertical mini-slices | 1a (auth + picker) → 1b (scanner + inventory) → 1c (relationships + graph + risks). Each ends with a working demo. Total ≈18 days. |

## 4. Denali invariants we are respecting

From `~/Projects/Denali/denali-vision.md` §II — load-bearing for every
design decision in this slice:

1. **Determinism is the spine. AI is the surface.** No LLM mutates the
   graph, declares a violation, or asserts a fact in this slice. All 8
   detectors are deterministic functions of (repo contents, scanner
   version). LLM use is reserved for future contextualization layers and
   is out of scope here.
2. **Every conclusion carries its evidence.** Every emitted `ai_asset`,
   `ai_relationship`, and AI-typed `finding` row in this slice carries a
   complete evidence packet inline. The packet shape is an open spec.
3. **MCP-first** — deferred. Not in this slice. Documented as a future
   surface in `HANDOFF.md`.
4. **Reversibility non-negotiable** — n/a in this slice (no actions
   taken against customer environments; read-only discovery).
5. **Open by default** — the evidence-packet schema, the `ai_assets` /
   `ai_relationships` shapes, and the AIBOM JSON export are all designed
   as if they will be public standards.
6. **Quality before reach** — GitHub is the only connector in Slice 1.
   OpenAI and Anthropic come later, after GitHub is genuinely excellent.

## 5. Architecture overview

The AI surface is a **parallel connector family** to the cloud surface,
not a subsystem of it. Same lifecycle (connect → scan → findings + assets
→ API → UI), separate data.

```
Cloud surface (existing):
  CFN one-click → cloud_connections → AWS scanner Lambda
    → findings → API → web/iOS

AI surface (new in Slice 1):
  GitHub App install → ai_connections
    → (user picks repo in web UI)
    → POST /v1/ai/scans → SQS → ai_scanner Lambda
    → ai_assets + ai_relationships + findings (category='ai')
    → each row carries evidence_packet JSONB inline
    → /v1/ai/* API
    → web AI tabs (Inventory + Risks + Trust Graph)
    → iOS AI tab (read-only)
```

Reuses without changes: Cognito user pool, API Gateway, CDK stacks
(`CisoCopilotApi`, `CisoCopilotScan`, `CisoCopilotData`), CORS/gateway-
response patterns, Aurora cluster, existing `findings` table, existing
finding-render and share components, EventBridge.

New: one SQL migration, one Docker image (`ai_scanner`), one SQS queue,
six API Lambdas, four web routes, one iOS tab.

## 6. Data model

One SQL migration: `platform/sql/004_phase_ai.sql`.

### 6.1 `ai_connections`

```sql
CREATE TABLE ai_connections (
  id                      UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id               UUID         NOT NULL REFERENCES tenants(id),
  provider                TEXT         NOT NULL
                                       CHECK (provider IN ('github', 'openai', 'anthropic')),
  status                  TEXT         NOT NULL
                                       CHECK (status IN ('pending', 'active', 'failed', 'revoked')),
  -- provider-specific columns; only one block is non-null per row
  github_installation_id  BIGINT,
  github_org_name         TEXT,
  github_account_type     TEXT,         -- 'User' | 'Organization'
  secret_arn              TEXT,          -- openai/anthropic future use
  external_id             TEXT,          -- generic provider account id
  created_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  updated_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  CONSTRAINT one_provider_id_present CHECK (
    (provider = 'github' AND github_installation_id IS NOT NULL)
    OR (provider IN ('openai', 'anthropic') AND secret_arn IS NOT NULL)
  ),
  UNIQUE (tenant_id, provider, github_installation_id)
);

CREATE INDEX ai_connections_tenant_idx ON ai_connections(tenant_id);
```

Slice 1 only populates `provider='github'` rows. The `openai`/`anthropic`
shapes are pre-committed in the schema so the next slice doesn't need a
migration.

### 6.2 `ai_assets`

```sql
CREATE TABLE ai_assets (
  id                UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         UUID          NOT NULL REFERENCES tenants(id),
  connection_id     UUID          REFERENCES ai_connections(id),
  asset_type        TEXT          NOT NULL,
  -- valid types: 'repository' | 'model' | 'mcp_server' | 'framework' |
  --              'vector_db' | 'prompt' | 'agent' | 'embedding' | 'tool'
  -- 'tool' represents a callable tool an agent or MCP server can invoke
  -- (declared tools of an MCP server, named function tools wired into an
  -- agent's tool_call schema, etc.). MCP-declared tools also remain
  -- mirrored in mcp_server.attributes.tools[] for convenience; the
  -- dedicated row exists so 'invokes' relationships can be drawn.
  name              TEXT          NOT NULL,
  source_repo_id    UUID          REFERENCES ai_assets(id),  -- NULL for the repo row itself
  source_path       TEXT,                                     -- file path within the repo
  attributes        JSONB         NOT NULL DEFAULT '{}'::jsonb,
  evidence_packet   JSONB         NOT NULL,
  detector_id       TEXT          NOT NULL,
  detector_version  TEXT          NOT NULL,
  scan_id           UUID          NOT NULL,
  first_seen_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
  last_seen_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
  UNIQUE (tenant_id, asset_type, source_repo_id, source_path, name)
);

CREATE INDEX ai_assets_tenant_idx       ON ai_assets(tenant_id);
CREATE INDEX ai_assets_repo_idx         ON ai_assets(source_repo_id);
CREATE INDEX ai_assets_type_idx         ON ai_assets(asset_type);
CREATE INDEX ai_assets_connection_idx   ON ai_assets(connection_id);
```

The repository is itself an `ai_asset` with `asset_type='repository'` and
`source_repo_id=NULL`. Everything else hangs off a repo via
`source_repo_id`. This keeps the schema flat — no separate `repos` table.

### 6.3 `ai_relationships`

```sql
CREATE TABLE ai_relationships (
  id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           UUID         NOT NULL REFERENCES tenants(id),
  source_asset_id     UUID         NOT NULL REFERENCES ai_assets(id) ON DELETE CASCADE,
  target_asset_id     UUID         NOT NULL REFERENCES ai_assets(id) ON DELETE CASCADE,
  relationship_type   TEXT         NOT NULL,
  -- valid: 'calls' | 'accesses' | 'deploys' | 'retrieves' |
  --        'invokes' | 'generates' | 'orchestrates' | 'trusts'
  attributes          JSONB        NOT NULL DEFAULT '{}'::jsonb,
  evidence_packet     JSONB        NOT NULL,
  detector_id         TEXT         NOT NULL,
  detector_version    TEXT         NOT NULL,
  scan_id             UUID         NOT NULL,
  first_seen_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  last_seen_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  UNIQUE (source_asset_id, target_asset_id, relationship_type)
);

CREATE INDEX ai_rel_tenant_idx ON ai_relationships(tenant_id);
CREATE INDEX ai_rel_source_idx ON ai_relationships(source_asset_id);
CREATE INDEX ai_rel_target_idx ON ai_relationships(target_asset_id);
```

### 6.4 `ai_scans`

```sql
CREATE TABLE ai_scans (
  id                                UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id                         UUID          NOT NULL REFERENCES tenants(id),
  connection_id                     UUID          NOT NULL REFERENCES ai_connections(id),
  repo_asset_id                     UUID          NOT NULL REFERENCES ai_assets(id),
  status                            TEXT          NOT NULL
                                                  CHECK (status IN ('queued', 'running', 'success', 'failed')),
  started_at                        TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
  completed_at                      TIMESTAMPTZ,
  error_message                     TEXT,
  assets_discovered_count           INT           NOT NULL DEFAULT 0,
  relationships_discovered_count    INT           NOT NULL DEFAULT 0,
  findings_generated_count          INT           NOT NULL DEFAULT 0,
  scanner_version                   TEXT          NOT NULL
);

CREATE INDEX ai_scans_tenant_idx ON ai_scans(tenant_id);
CREATE INDEX ai_scans_repo_idx   ON ai_scans(repo_asset_id);
CREATE INDEX ai_scans_status_idx ON ai_scans(status);
```

### 6.5 Extension to existing `findings` table

```sql
ALTER TABLE findings ADD COLUMN evidence_packet JSONB;
```

AI findings populate this. Cloud findings leave it NULL in Slice 1.
Backfill is a future slice.

## 7. Evidence packet spec (v0.1)

Stored inline as JSONB on `ai_assets.evidence_packet`,
`ai_relationships.evidence_packet`, and `findings.evidence_packet`. The
shape is the spec — it is designed as if it will be extracted into a
public schema.

```json
{
  "version": "0.1",
  "packet_id": "uuid",
  "produced_at": "2026-05-18T10:00:00Z",
  "detector": {
    "id": "ai.detectors.mcp_server",
    "version": "0.1.0"
  },
  "subject": {
    "kind": "ai_asset" | "ai_relationship" | "finding",
    "id": "uuid",
    "type": "mcp_server",
    "name": "github-mcp"
  },
  "source_events": [
    {
      "kind": "file",
      "repo": "kk/foo",
      "commit_sha": "abc123",
      "path": "/mcp/server.py",
      "snippet_lines": [12, 14],
      "snippet": "from mcp.server import Server\n..."
    }
  ],
  "graph_trace": [],
  "reasoning_chain": [
    "matched `mcp.server.Server` import at line 12",
    "found `@server.list_tools()` decorator at line 18"
  ],
  "model": null,
  "confidence": "high" | "medium" | "low",
  "signature": null
}
```

Field rules:
- `model` is `null` for fully deterministic detectors (everything in
  Slice 1). It becomes `{"id": "...", "version": "..."}` only when an
  LLM is part of the reasoning chain. No detector in this slice uses an
  LLM.
- `signature` is `null` in Slice 1. KMS asymmetric signing is a future
  slice; the field is reserved so consumers can ignore unsigned packets
  later without a schema migration.
- `graph_trace` is populated only for relationship packets and is
  ordered `[source_node_id, edge_id, target_node_id]`.

## 8. Mini-slice 1a — GitHub App + repo picker (≈5 days)

**Demo:** KK clicks "Connect GitHub" → installs the App → lands on a
paginated list of his authorized repos with last-push dates. No
scanning.

### 8.1 GitHub App setup (one-time)

- Single CISO Copilot GitHub App registered in KK's GitHub (move to a
  `transilience-ai` org later).
- Permissions: Contents (R), Metadata (R), Actions (R), Pull requests
  (R), Workflows (R). Account: Email addresses (R).
- Webhook events: none subscribed for Slice 1.
- Private key + client secret stored in a new Secrets Manager secret
  `ciso-copilot/github-app/credentials`.
- Setup URL (post-install redirect):
  `https://app.settlingforless.com/ai/install/callback?installation_id=...&setup_action=install&state=...`

### 8.2 Install flow

1. User clicks "Connect GitHub" on the web app.
2. Web calls `POST /v1/ai/connections/github/install_url`.
3. Lambda mints a signed state JWT (`{tenant_id, user_id, nonce, exp}`,
   5min TTL) and returns
   `https://github.com/apps/ciso-copilot/installations/new?state=...`.
4. Browser navigates to GitHub. User picks org + repos.
5. GitHub redirects to `/ai/install/callback?installation_id=...&state=...`.
6. Web posts `{installation_id, state}` to
   `POST /v1/ai/connections/github/complete`.
7. Lambda verifies state JWT, asserts state.tenant_id matches the caller's
   Cognito JWT tenant_id, inserts an `ai_connections` row
   (`status='active'`), returns `{connection_id}`.
8. Web redirects to `/ai/connections/:connection_id/repos`.

State token guarantees the GitHub callback corresponds to a flow this
exact user initiated; prevents installation_id replay across tenants.

### 8.3 Installation token minting

Every API call needing GitHub access does:
1. Sign a 10-minute JWT with the App's private key
   (`iss=client_id`, `iat`, `exp`).
2. POST to `https://api.github.com/app/installations/{id}/access_tokens`
   to receive a 1-hour installation token.

Both steps in a `lambda/shared/github_app_client.py`. Tokens cached
per-Lambda-warm-container with 50-min TTL. No persistent storage.

### 8.4 Endpoints (1a)

| Method | Path | Body / params | Returns |
|---|---|---|---|
| `POST` | `/v1/ai/connections/github/install_url` | `{}` | `{install_url}` |
| `POST` | `/v1/ai/connections/github/complete` | `{installation_id, state}` | `{connection_id}` |
| `GET` | `/v1/ai/connections` | — | `[{id, provider, status, github_org_name, created_at}, ...]` |
| `GET` | `/v1/ai/connections/{id}/repos?page=N&per_page=30` | — | `{repos: [{full_name, default_branch, last_pushed_at, size_kb, primary_language, is_private}, ...], next_page}` |
| `DELETE` | `/v1/ai/connections/{id}` | — | `204` (flips status='revoked'; does not uninstall on GitHub) |

All Cognito-JWT-authenticated. CORS and gateway-response patterns match
existing endpoints.

### 8.5 Web UI (1a)

- `ConnectClouds.tsx` (modify): add a "Connect GitHub" card next to AWS /
  Azure / GCP. Click → `POST install_url` → `window.location = install_url`.
- `/ai/install/callback` (new route, `InstallCallback.tsx`): parses query
  params, posts to `complete`, redirects to repo picker.
- `/ai/connections/:id/repos` (new route, `RepoPicker.tsx`): fetches the
  authorized-repos list. Table columns: repo name (link to GitHub),
  last-pushed date, primary language, size, "Scan" button (greyed out in
  1a; wired in 1b). Pagination via `?page=N`.

### 8.6 iOS in 1a

Nothing. iOS gets read-only views in 1b/1c.

### 8.7 Failure modes (1a)

- State token expired/tampered → 400, web shows "Install link expired."
- User cancels on GitHub → no callback fires; no stale rows.
- GitHub 429 on repo list → 429 surfaced with `Retry-After`; web shows
  friendly retry timer.
- App uninstalled on GitHub → first call after gets 401 from GitHub;
  Lambda flips `status='revoked'`; web shows "Reconnect" CTA.

### 8.8 Tests (1a)

- Unit: state JWT round-trip, installation token cache, GitHub client
  retry/backoff.
- Integration (Aurora Data API + Cognito): full install→complete→list
  flow in a test tenant.
- E2E (manual): KK installs on his real GitHub, sees real repos.

## 9. Mini-slice 1b — Scanner + AI Inventory (≈8 days)

**Demo:** Click Scan on a repo → 30s later, AI Inventory tab shows
discovered AI assets with their evidence packets. iOS Inventory tab shows
the same list read-only.

### 9.1 Scan trigger flow

1. Web posts `{connection_id, repo_full_name}` to `POST /v1/ai/scans`.
2. Lambda upserts the repo's `ai_assets` row (`asset_type='repository'`),
   inserts an `ai_scans` row (`status='queued'`), enqueues
   `{scan_id, tenant_id, connection_id, repo_asset_id, repo_full_name}`
   to the `ai-scan-queue` SQS queue, returns `{scan_id}`.
3. SQS triggers `ai_scanner` Lambda (max concurrency 5).
4. Lambda mints installation token, shallow-clones the repo, runs the 8
   detectors, runs the correlator pass, upserts rows, updates scan
   counts, sets `status='success'`.

### 9.2 Scanner Lambda (`platform/lambda/ai_scanner/`)

- Container image, base `public.ecr.aws/lambda/python:3.12`. Layered
  with `git`, `ripgrep`, `tree-sitter` Python bindings, `boto3`,
  `psycopg2-binary`.
- Memory 2048 MB. Timeout 600s. Ephemeral storage 4 GB.
- VPC + subnets identical to existing scanners. Direct psycopg2 over the
  VPC Aurora endpoint, not Data API.
- Env vars: `DB_SECRET_ARN`, `GITHUB_APP_SECRET_ARN`, `SCANNER_VERSION`.

Clone strategy: `git clone --depth=1 --single-branch <repo>@<default_branch>`
into `/tmp/scan-<scan_id>`. Repos > 4 GB → fail the scan with a
`clone_too_large` finding (sparse-checkout is a future optimization).

### 9.3 Detector interface

```python
@dataclass(frozen=True)
class DetectorResult:
    assets: list[AssetEmission]
    relationships: list[RelEmission]
    findings: list[FindingEmission]
    # Each emission carries its own evidence_packet.

class Detector(Protocol):
    detector_id: str        # "ai.detectors.mcp_server"
    detector_version: str   # "0.1.0"
    def detect(self, ctx: ScanContext) -> DetectorResult: ...

@dataclass
class ScanContext:
    repo_workdir: Path
    repo_full_name: str
    default_branch: str
    head_commit_sha: str
    scan_id: UUID
    tenant_id: UUID
    connection_id: UUID
    repo_asset_id: UUID
```

Detectors are pure: same input → same emissions, byte-for-byte. No
detector calls an LLM in this slice.

### 9.4 The eight detectors

| # | `detector_id` | Signal | Asset(s) emitted | Relationship(s) | Finding(s) |
|---|---|---|---|---|---|
| 1 | `ai.detectors.framework` | imports of `langchain`, `llama_index`, `crewai`, `autogen`, `semantic_kernel`, `dspy` (ripgrep + AST confirmation) | `framework` per unique framework | `repository`→`uses`→`framework` | none |
| 2 | `ai.detectors.model_usage` | SDK calls: `openai.*`, `OpenAI(...)`, `anthropic.*`, `bedrock-runtime` invocations; model IDs in args | `model` per (provider, model_id) | `repository`→`calls`→`model` | `unapproved_provider` (allowlist-based, MEDIUM) |
| 3 | `ai.detectors.mcp_server` | `mcp.server.Server` imports; `@server.list_tools()` decorators; `mcp.json` files; tool defs parsed from AST | `mcp_server` (also mirrors declared tools in `attributes.tools[]`) + one `tool` asset per declared tool | `repository`→`deploys`→`mcp_server`; `mcp_server`→`invokes`→`tool` (one edge per declared tool) | `mcp_with_broad_perms` (HIGH) if write-scoped tools |
| 4 | `ai.detectors.agentic_workflow` | while-loop or recursive function that (a) calls an LLM AND (b) executes tool calls returned from it | `agent` + one `tool` per distinct tool wired into the agent's tool_call schema | `agent`→`orchestrates`→`model`; `agent`→`invokes`→`tool` (one edge per wired tool) | `autonomous_loop_no_human_in_loop` (MEDIUM, confidence='medium') |
| 5 | `ai.detectors.vector_db` | imports of `chromadb`, `pinecone`, `weaviate`, `qdrant_client`, `faiss`; `pgvector` SQL | `vector_db` | `repository`→`retrieves`→`vector_db` | none |
| 6 | `ai.detectors.embedding` | calls to `text-embedding-*`, `voyage`, `cohere.embed`, `OpenAIEmbeddings()` | `embedding` per provider | `repository`→`generates`→`embedding` | none |
| 7 | `ai.detectors.prompt` | files matching `prompt*.{txt,md}`, `prompts/`, `*.prompt`; multi-line string literals >200 chars inside model SDK call arguments | `prompt` per file/literal | `repository`→`accesses`→`prompt`; `model`→`accesses`→`prompt` (when colocated) | `prompt_with_secret_pattern` (HIGH) if regex match |
| 8 | `ai.detectors.secrets_in_ai_code` | gitleaks-style regex (`sk-...`, `anthropic-...`, `xoxb-...`, `AWS_ACCESS_KEY_ID`) — **only when the file also imports an LLM SDK** | none | none | `hardcoded_credential_in_ai_module` (HIGH) |

Each detector lives in its own file under `lambda/ai_scanner/detectors/`,
has its own snapshot test directory under `tests/fixtures/<detector>/`,
and a golden file per fixture.

### 9.5 Cross-detector correlator

Runs after all 8 detectors. Adds derived relationships:
- `agent` + `mcp_server` colocated in same module → `agent`→`invokes`→`mcp_server`.
- `model` + `vector_db` + `prompt` colocated → `model`→`retrieves`→`vector_db` if the call sequence is RAG-shaped.

Also deterministic. Its own fixture set under
`tests/fixtures/correlator/`.

### 9.6 Write path (per scan)

```python
with conn.transaction():
    upsert_ai_assets(detector_emissions.assets)             # ON CONFLICT → update last_seen_at
    upsert_ai_relationships(detector_emissions.relationships)
    insert_findings(detector_emissions.findings)
    update_scan_counts(scan_id, ...)
    set_scan_status(scan_id, 'success')
```

Any exception inside the transaction rolls back; SQS visibility expires
and the message retries (max 3 attempts → DLQ).

### 9.7 Endpoints (1b)

| Method | Path | Body / params | Returns |
|---|---|---|---|
| `POST` | `/v1/ai/scans` | `{connection_id, repo_full_name}` | `{scan_id}` |
| `GET` | `/v1/ai/scans?connection_id=...&status=...` | — | `[{id, repo_full_name, status, started_at, completed_at, counts, error_message}, ...]` |
| `GET` | `/v1/ai/scans/{id}` | — | same shape, one row |
| `GET` | `/v1/ai/assets?repo=...&type=...&since=...&page=...` | — | paginated `[{id, asset_type, name, source_path, source_repo: {id, full_name}, detector_id, first_seen_at, last_seen_at}, ...]` |
| `GET` | `/v1/ai/assets/{id}` | — | row + full `evidence_packet` |

### 9.8 Web UI (1b)

- `RepoPicker.tsx` (extend): Scan button now calls `POST /v1/ai/scans`.
  Row shows a status pill (`Scanning…` / `Success: N assets` / `Failed`).
  Polls `GET /v1/ai/scans/{id}` every 3s while in-flight.
- `/ai/inventory` (new, `AIInventory.tsx`): tab in main nav. Table with
  asset, type, source repo, detector, first/last seen. Filter chips by
  type. Click row → asset detail.
- `/ai/inventory/:asset_id` (new, `AssetDetail.tsx`): header (name, type,
  source path with GitHub deep-link), attributes JSON, **evidence
  packet** collapsible, source-code snippet rendered from
  `evidence_packet.source_events[0]` via GitHub raw API. "Related
  assets" placeholder, filled in 1c.

### 9.9 iOS in 1b (read-only)

- New "AI" tab in the bottom tab bar between Risks and Settings.
- Single screen: AI assets list from `GET /v1/ai/assets`, grouped by repo.
  Pull-to-refresh.
- Tap row → detail view (attributes + evidence packet raw JSON in a
  `ScrollView`; richer rendering later).
- No onboarding from iOS. When `GET /v1/ai/connections` returns empty,
  the tab shows: "Connect GitHub on the web app to start scanning."

### 9.10 Tests (1b)

- **Detector goldens (load-bearing):** each detector has fixture repos
  under `lambda/ai_scanner/tests/fixtures/<detector_name>/`. CI runs each
  detector against its fixtures and asserts the emissions match the
  golden JSON. Snapshot drift fails the build.
- **Correlator goldens:** same pattern.
- **Scanner integration test:** Docker-compose with Postgres + fixture
  repo on disk → run the full Lambda handler → assert row counts in
  each table.
- **API integration tests:** under `platform/tests/api/ai/`, using the
  existing pattern (`POST scans` → invoke handler directly → `GET assets`
  returns the expected rows).
- **E2E (manual):** KK scans 3 of his real repos with known AI content.

### 9.11 Risks flagged for 1b

- **Detector 4 (`agentic_workflow`) is the fuzziest.** Heuristic ships
  with `confidence='medium'`; tune after real-world runs.
- **Tree-sitter wheel size** may push the Docker image past 1 GB. If so,
  fall back to regex-only detection for Slice 1; revisit AST later.
- **Monorepos > 4 GB** fail with a finding. Sparse-checkout is a future
  optimization.

## 10. Mini-slice 1c — Relationships + Trust Graph + AI Risks (≈5 days)

**Demo:** Open a scanned repo's trust-graph view → nodes for the repo,
its frameworks, models, MCP servers, vector DBs, prompts, agents, with
labeled edges. Click an MCP server → asset detail shows the agent that
invokes it. AI Risks tab shows AI-typed findings with evidence packets.

### 10.1 Trust graph endpoint

`GET /v1/ai/repos/:repo_asset_id/graph` returns:

```json
{
  "nodes": [
    { "data": { "id": "uuid", "label": "github-mcp", "type": "mcp_server",
                "attributes": {} } }
  ],
  "edges": [
    { "data": { "id": "uuid", "source": "uuid", "target": "uuid",
                "label": "invokes", "evidence_packet_id": "uuid" } }
  ],
  "meta": { "repo_full_name": "kk/foo", "scanned_at": "...",
            "node_count": 42, "truncated": false }
}
```

Query: a single recursive CTE rooted at the repo asset, walking outward
through `ai_relationships`, capped at depth 4 and 500 nodes. If
truncated, `meta.truncated=true` and the UI shows a banner.

### 10.2 Cytoscape.js viz

- `/ai/repos/:repo_asset_id/graph` (new, `TrustGraph.tsx`).
- Vite dynamic import of `cytoscape` + `cytoscape-fcose`.
- Node colors by `type` (model = blue, mcp_server = purple, agent =
  orange, vector_db = green, prompt = yellow, framework = grey,
  embedding = teal, repository = white, tool = pink).
- Edge labels show `relationship_type`. Hover edge → tooltip with
  `detector_id` and `confidence`.
- Click node → opens `AssetDetail.tsx` in a side panel (not a new route).
- Layout-stable: fcose RNG seeded with the repo UUID so the same repo
  always renders the same way.
- "Export PNG" via cytoscape's `png()` method.

### 10.3 Per-asset relationships endpoint

`GET /v1/ai/assets/:id/relationships?direction=both` →
`[{ id, relationship_type, other_asset: {id, name, type},
    evidence_packet_id, direction: 'outgoing'|'incoming' }, ...]`

UI: two-column list in `AssetDetail.tsx` —
"Calls / accesses / orchestrates →" and "← Called by / accessed by".

### 10.4 AI Risks tab

- Web `/ai/risks`: reuses `RisksList` with `?category=ai` pre-applied.
  Adds a `category` filter chip on the component that the cloud-side
  Risks tab can opt into later.
- iOS Risks tab gets a segmented control: `All` / `Cloud` / `AI`.
  Tapping `AI` applies the `?category=ai` filter to the existing fetch.
  Existing finding detail screen already handles arbitrary
  `subject_type`; only a label tweak when `subject_type='ai_asset'`.

### 10.5 Endpoints (1c)

| Method | Path | Returns |
|---|---|---|
| `GET` | `/v1/ai/assets/{id}/relationships?direction=both\|outgoing\|incoming` | relationships list |
| `GET` | `/v1/ai/repos/{repo_asset_id}/graph` | cytoscape-shaped payload |

AI Risks reuses the existing `GET /v1/findings?category=ai` — adding the
filter to the existing handler is a 2-line change, not a new endpoint.

### 10.6 Tests (1c)

- Recursive CTE unit test with a fixture graph (one repo, 20 assets, 30
  relationships); assert `node_count`, `truncated`, and topology.
- Web component test for `TrustGraph.tsx` with a stubbed payload (mock
  cytoscape).
- E2E (manual): KK opens the trust graph for a scanned repo and clicks
  through edges.

## 11. Cross-cutting

### 11.1 Naming

- All new UI strings: "AI Inventory", "AI Risks", "Trust Graph". Never
  "Denali."
- Source paths: `lambda/ai_scanner/`, `web/src/routes/ai/*`,
  `web/src/lib/ai-api.ts`, iOS `AIInventoryView.swift`,
  `AIAssetDetailView.swift`. No `denali` anywhere.
- Table/column names: `ai_*` prefix throughout.

### 11.2 Feature flag

- API Lambda env var `AI_FEATURES_ENABLED=true|false` gates `/v1/ai/*`
  endpoints. Returns `404` when off.
- Web reads existing `/v1/config` (already used for the realtime voice
  toggle) and hides AI tabs and the Connect GitHub card when off.
- Defaults to off in prod until 1a is verified end-to-end on KK's
  tenant. Per-tenant override via a new `tenants.features JSONB` column.

### 11.3 Cost / capacity at Slice 1 scale

- One tenant, ~10 scanned repos, ~50 assets/repo → ~500 rows. Negligible.
- SQS standard queue: free at this volume.
- Scanner Lambda: ~3 min × 2048 MB × 10 invocations ≈ $0.06 / scan run.
- GitHub API: well under the 5000 req/hr per-installation limit.
- Aurora: existing cluster; new tables add a few MB.

### 11.4 Security posture

- GitHub App private key in Secrets Manager; rotated annually.
- Installation tokens never persisted; only cached per-warm-container.
- All `/v1/ai/*` endpoints Cognito-JWT-authenticated.
- Tenant isolation enforced at every query — `WHERE tenant_id = $1`.
- Clone directory under `/tmp/scan-<scan_id>` is wiped at handler
  completion (Lambda `/tmp` persists across warm-container invocations,
  so cleanup is explicit, not implicit).
- Scanner Lambda has no outbound IAM permission to write anywhere
  except its own database tables and SQS DLQ.

### 11.5 Decision log

Open questions remaining at design time (decide before the relevant
mini-slice lands):

| Q | Where to decide |
|---|---|
| Allowlist of "approved providers" for `unapproved_provider` finding | 1b detector config |
| Severity scoring of AI findings (reuse cloud severity scoring or its own scale?) | 1b — recommend reuse |
| GitHub App marketplace listing vs. private install URL | 1a — recommend private install URL only, marketplace is later |
| Push-webhook rescan-on-commit | Out of Slice 1 |
| KMS signing of evidence packets | Out of Slice 1 |
| Backfill of evidence packets onto cloud findings | Out of Slice 1 |

## 12. Out of scope for Slice 1 (explicit)

- MCP server (deferred per L4)
- OpenAI / Anthropic provider connectors
- Limited AWS-AI (Bedrock / Lambda / IAM-as-AI) connector
- Blast-radius traces
- KMS-signed evidence packets
- Cloud-finding evidence-packet backfill
- Push-webhook-driven rescan on commit
- Sparse checkout for huge monorepos
- All-repos aggregate trust graph
- Autonomous remediation, policy enforcement, runtime prompt inspection,
  live agent sandboxing, alert-fatigue workflows, compliance workflows
  (all explicitly out of the Denali MVP per `denali-mvp.md` §4)

## 13. Sequencing summary

| Mini-slice | Days | Deliverable |
|---|---|---|
| 1a | ≈5 | GitHub App install + repo picker. Demo: KK sees his repos listed in CISO Copilot. |
| 1b | ≈8 | Scanner Lambda + 8 detectors + AI Inventory tab on web + iOS. Demo: real AI assets visible with evidence packets. |
| 1c | ≈5 | Relationships + trust graph viz + AI Risks tab. Demo: per-repo trust graph, AI risks separately surfaced. |
| **Slice 1 total** | **≈18** | **End-to-end "discover AI inside a repo" demo, integrated into CISO Copilot web + iOS.** |

After Slice 1 ships, the next slice candidates (not part of this spec):
OpenAI/Anthropic connectors, blast radius, MCP server, KMS signing,
cloud-finding evidence-packet backfill, all-repos aggregate trust graph.

---

*Spec ends here. Implementation plan is written separately via the
writing-plans skill once this spec is approved.*
