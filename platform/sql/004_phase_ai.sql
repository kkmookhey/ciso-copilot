-- CISO Copilot v2 — Phase AI schema additions.
-- AI-security schema for Slice 1 (1a + 1b + 1c).
-- Adds: ai_connections, ai_assets, ai_relationships, ai_scans tables
--       findings.evidence_packet column
-- See: docs/superpowers/specs/2026-05-18-ai-security-slice-1-design.md §6

BEGIN;

-- 1. AI provider connections (parallel to cloud_connections)
CREATE TABLE ai_connections (
  id                      UUID         PRIMARY KEY,
  tenant_id               UUID         NOT NULL REFERENCES tenants(tenant_id),
  provider                TEXT         NOT NULL
                                       CHECK (provider IN ('github', 'openai', 'anthropic')),
  status                  TEXT         NOT NULL
                                       CHECK (status IN ('pending', 'active', 'failed', 'revoked')),
  github_installation_id  BIGINT,
  github_org_name         TEXT,
  github_account_type     TEXT,
  secret_arn              TEXT,
  external_id             TEXT,
  created_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  updated_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  CONSTRAINT one_provider_id_present CHECK (
    (provider = 'github' AND github_installation_id IS NOT NULL)
    OR (provider IN ('openai', 'anthropic') AND secret_arn IS NOT NULL)
  ),
  UNIQUE (tenant_id, provider, github_installation_id)
);

CREATE INDEX ai_connections_tenant_idx ON ai_connections(tenant_id);

-- 2. AI entities discovered in scans (used in 1b)
CREATE TABLE ai_assets (
  id                UUID          PRIMARY KEY,
  tenant_id         UUID          NOT NULL REFERENCES tenants(tenant_id),
  connection_id     UUID          REFERENCES ai_connections(id),
  asset_type        TEXT          NOT NULL,
  name              TEXT          NOT NULL,
  source_repo_id    UUID          REFERENCES ai_assets(id),
  source_path       TEXT,
  attributes        JSONB         NOT NULL DEFAULT '{}'::jsonb,
  evidence_packet   JSONB         NOT NULL,
  detector_id       TEXT          NOT NULL,
  detector_version  TEXT          NOT NULL,
  scan_id           UUID          NOT NULL,
  first_seen_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
  last_seen_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
  UNIQUE (tenant_id, asset_type, source_repo_id, source_path, name)
);

CREATE INDEX ai_assets_tenant_idx     ON ai_assets(tenant_id);
CREATE INDEX ai_assets_repo_idx       ON ai_assets(source_repo_id);
CREATE INDEX ai_assets_type_idx       ON ai_assets(asset_type);
CREATE INDEX ai_assets_connection_idx ON ai_assets(connection_id);

-- 3. Edges between AI entities (used in 1c)
CREATE TABLE ai_relationships (
  id                  UUID         PRIMARY KEY,
  tenant_id           UUID         NOT NULL REFERENCES tenants(tenant_id),
  source_asset_id     UUID         NOT NULL REFERENCES ai_assets(id) ON DELETE CASCADE,
  target_asset_id     UUID         NOT NULL REFERENCES ai_assets(id) ON DELETE CASCADE,
  relationship_type   TEXT         NOT NULL,
  attributes          JSONB        NOT NULL DEFAULT '{}'::jsonb,
  evidence_packet     JSONB        NOT NULL,
  detector_id         TEXT         NOT NULL,
  detector_version    TEXT         NOT NULL,
  scan_id             UUID         NOT NULL,
  first_seen_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  last_seen_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  UNIQUE (source_asset_id, target_asset_id, relationship_type)
);

CREATE INDEX ai_rel_tenant_idx ON ai_relationships(tenant_id);
CREATE INDEX ai_rel_source_idx ON ai_relationships(source_asset_id);
CREATE INDEX ai_rel_target_idx ON ai_relationships(target_asset_id);

-- 4. Scan lifecycle (used in 1b)
CREATE TABLE ai_scans (
  id                                UUID          PRIMARY KEY,
  tenant_id                         UUID          NOT NULL REFERENCES tenants(tenant_id),
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

-- 5. Add evidence_packet column to existing findings table (populated by AI scanner in 1b)
ALTER TABLE findings ADD COLUMN evidence_packet JSONB;

COMMIT;
