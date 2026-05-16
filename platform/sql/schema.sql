-- CISO Copilot v2 — Phase 0 schema (tenancy + identity + audit only).
-- Findings, scans, events tables come in Phase A. See CISOBrief-v2.md §8.

CREATE TABLE IF NOT EXISTS tenants (
  tenant_id              UUID PRIMARY KEY,
  display_name           TEXT NOT NULL,
  email_domain           TEXT NOT NULL UNIQUE,
  plan                   TEXT NOT NULL DEFAULT 'beta',
  status                 TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'approved' | 'rejected' | 'suspended'
  approved_at            TIMESTAMPTZ,
  approval_token_nonces  JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
  user_id       UUID PRIMARY KEY,
  tenant_id     UUID NOT NULL REFERENCES tenants(tenant_id),
  email         TEXT NOT NULL,
  sso_provider  TEXT NOT NULL,         -- 'microsoft' | 'google'
  sso_subject   TEXT NOT NULL,         -- 'sub' claim from IdP
  role          TEXT NOT NULL DEFAULT 'member',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (sso_provider, sso_subject)
);

CREATE TABLE IF NOT EXISTS audit_events (
  event_id      UUID PRIMARY KEY,
  tenant_id     UUID,
  user_id       UUID,
  action        TEXT NOT NULL,
  target        TEXT,
  payload       JSONB,
  ip            INET,
  user_agent    TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_users_tenant         ON users(tenant_id);
CREATE INDEX IF NOT EXISTS idx_audit_tenant_created ON audit_events(tenant_id, created_at DESC);
