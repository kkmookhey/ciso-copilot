-- platform/sql/015_mcp_connectors.sql
-- MCP Connectors Slice 1 — per-user and per-tenant OAuth tokens for productivity tools.
-- Refs: docs/superpowers/specs/2026-05-28-mcp-connectors-design.md §5

-- Per-analyst, per-tool tokens. One active row per (tenant, user, provider).
-- Token encryption uses KMS envelope: each token has its own data key,
-- generated at write time by kms.GenerateDataKey. The Fernet ciphertext
-- lives in *_token_enc; the KMS-encrypted data key lives in *_data_key_ct.
-- Both columns are required to decrypt — losing data_key_ct loses the
-- token. See _shared/mcp_oauth/crypto.py for the envelope shape.
CREATE TABLE IF NOT EXISTS user_connectors (
  conn_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id            UUID NOT NULL REFERENCES tenants(tenant_id),
  user_id              UUID NOT NULL REFERENCES users(user_id),
  oauth_provider       TEXT NOT NULL,
  mcp_server_url       TEXT NOT NULL,
  vendor_user_id       TEXT NOT NULL,
  vendor_workspace_id  TEXT,
  access_token_enc     BYTEA NOT NULL,
  access_data_key_ct   BYTEA NOT NULL,
  refresh_token_enc    BYTEA NOT NULL,
  refresh_data_key_ct  BYTEA NOT NULL,
  access_expires_at    TIMESTAMPTZ NOT NULL,
  scopes               TEXT[] NOT NULL,
  status               TEXT NOT NULL DEFAULT 'active',
  last_error           TEXT,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at         TIMESTAMPTZ,
  revoked_at           TIMESTAMPTZ,
  UNIQUE (tenant_id, user_id, oauth_provider)
);

CREATE INDEX IF NOT EXISTS ix_user_connectors_lookup
  ON user_connectors (tenant_id, user_id, oauth_provider) WHERE status = 'active';

CREATE INDEX IF NOT EXISTS ix_user_connectors_refresh
  ON user_connectors (access_expires_at) WHERE status = 'active';

-- Admin-installed workspace bots. One bot per (tenant, provider).
-- Slice 1 lands the schema; the install flow + autonomous rule ship in Slice 2.
CREATE TABLE IF NOT EXISTS tenant_bot_connectors (
  bot_id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id                    UUID NOT NULL REFERENCES tenants(tenant_id),
  oauth_provider               TEXT NOT NULL,
  mcp_server_url               TEXT NOT NULL,
  vendor_workspace_id          TEXT NOT NULL,
  access_token_enc             BYTEA NOT NULL,
  access_data_key_ct           BYTEA NOT NULL,
  refresh_token_enc            BYTEA,
  refresh_data_key_ct          BYTEA,
  access_expires_at            TIMESTAMPTZ,
  scopes                       TEXT[] NOT NULL,
  broadcast_channel_id         TEXT,
  broadcast_channel_name       TEXT,
  autonomous_rule_enabled      BOOLEAN NOT NULL DEFAULT true,
  installed_by_user_id         UUID NOT NULL REFERENCES users(user_id),
  status                       TEXT NOT NULL DEFAULT 'active',
  created_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at                 TIMESTAMPTZ,
  revoked_at                   TIMESTAMPTZ,
  UNIQUE (tenant_id, oauth_provider)
);

-- pgcrypto is already enabled (used by gen_random_uuid). Verify just in case.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
