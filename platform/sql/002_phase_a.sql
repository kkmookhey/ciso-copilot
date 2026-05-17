-- CISO Copilot v2 — Phase A schema additions.
-- Adds cloud connections, scans, findings/assets, real-time events, scores.
-- See CISOBrief-v2.md §8 for the canonical definitions.
--
-- Apply order: 001_phase0.sql first (tenants, users, audit_events), then this.
-- Each statement is idempotent (IF NOT EXISTS) so re-running is safe.

-- ===== Cloud connections =====

CREATE TABLE IF NOT EXISTS cloud_connections (
  conn_id                 UUID PRIMARY KEY,
  tenant_id               UUID NOT NULL REFERENCES tenants(tenant_id),
  cloud_type              TEXT NOT NULL,                  -- 'aws' | 'azure' | 'entra' | 'gcp'
  display_name            TEXT NOT NULL,
  status                  TEXT NOT NULL DEFAULT 'pending',-- 'pending' | 'active' | 'error' | 'revoked'
  signals                 JSONB NOT NULL DEFAULT '{}'::jsonb,  -- {pull_scan:bool, alerts:bool, drift:bool}
  credentials_secret_arn  TEXT NOT NULL,                  -- ref into Secrets Manager
  scope                   JSONB NOT NULL DEFAULT '{}'::jsonb,  -- {regions:[], subscriptions:[], projects:[]}
  external_id             TEXT,                            -- AWS sts:ExternalId for trust policy
  account_identifier      TEXT,                            -- AWS account_id, Azure tenant+sub, GCP project, etc.
  last_scan_at            TIMESTAMPTZ,
  last_error              TEXT,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_connections_tenant ON cloud_connections(tenant_id, cloud_type);

-- ===== Scans =====

CREATE TABLE IF NOT EXISTS scans (
  scan_id      UUID PRIMARY KEY,
  tenant_id    UUID NOT NULL REFERENCES tenants(tenant_id),
  conn_id      UUID NOT NULL REFERENCES cloud_connections(conn_id),
  trigger      TEXT NOT NULL,                              -- 'scheduled' | 'manual' | 'onboarding'
  status       TEXT NOT NULL DEFAULT 'queued',             -- 'queued' | 'running' | 'completed' | 'failed' | 'partial'
  scope        JSONB NOT NULL DEFAULT '{}'::jsonb,         -- regions/subs actually scanned
  step_fn_arn  TEXT,                                       -- ARN of running execution, if any
  started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at  TIMESTAMPTZ,
  error        TEXT,
  stats        JSONB NOT NULL DEFAULT '{}'::jsonb          -- {checks_run, findings, errors, ...}
);

CREATE INDEX IF NOT EXISTS idx_scans_tenant_started ON scans(tenant_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_scans_conn_started   ON scans(conn_id, started_at DESC);

-- ===== Findings (Shasta output) =====

CREATE TABLE IF NOT EXISTS findings (
  finding_id     UUID PRIMARY KEY,
  tenant_id      UUID NOT NULL REFERENCES tenants(tenant_id),
  conn_id        UUID NOT NULL REFERENCES cloud_connections(conn_id),
  scan_id        UUID NOT NULL REFERENCES scans(scan_id),
  check_id       TEXT NOT NULL,
  title          TEXT NOT NULL,
  description    TEXT NOT NULL,
  severity       TEXT NOT NULL,                            -- 'critical' | 'high' | 'medium' | 'low' | 'info'
  status         TEXT NOT NULL,                            -- 'fail' | 'pass' | 'not_assessed' | 'not_applicable'
  resource_arn   TEXT,
  resource_type  TEXT,
  region         TEXT,
  domain         TEXT NOT NULL,                            -- Shasta CheckDomain
  frameworks     JSONB NOT NULL DEFAULT '{}'::jsonb,       -- {soc2:[...], cis_aws:[...], ...}
  remediation    TEXT,
  first_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen      TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_findings_tenant_severity_lastseen
  ON findings(tenant_id, severity, last_seen DESC) WHERE status = 'fail';
CREATE INDEX IF NOT EXISTS idx_findings_resource   ON findings(resource_arn);
CREATE INDEX IF NOT EXISTS idx_findings_scan       ON findings(scan_id);
CREATE INDEX IF NOT EXISTS idx_findings_check      ON findings(tenant_id, check_id);

-- ===== Assets (resource inventory, deduped across scans) =====

CREATE TABLE IF NOT EXISTS assets (
  asset_id     UUID PRIMARY KEY,
  tenant_id    UUID NOT NULL REFERENCES tenants(tenant_id),
  conn_id      UUID NOT NULL REFERENCES cloud_connections(conn_id),
  identifier   TEXT NOT NULL,                              -- ARN or cloud-native ID
  type         TEXT NOT NULL,                              -- 'aws_ec2', 'azure_storage_account', ...
  region       TEXT,
  properties   JSONB NOT NULL DEFAULT '{}'::jsonb,
  first_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, conn_id, identifier)
);

CREATE INDEX IF NOT EXISTS idx_assets_tenant_type ON assets(tenant_id, type);

-- ===== Real-time events (alerts + drift) =====

CREATE TABLE IF NOT EXISTS events (
  event_id      UUID PRIMARY KEY,
  tenant_id     UUID NOT NULL REFERENCES tenants(tenant_id),
  conn_id       UUID NOT NULL REFERENCES cloud_connections(conn_id),
  kind          TEXT NOT NULL,                             -- 'alert' | 'drift'
  source        TEXT NOT NULL,                             -- 'guardduty' | 'inspector' | 'cloudtrail' | 'config' | 'securityhub' | 'defender' | 'entra_risk'
  severity      TEXT NOT NULL,                             -- normalized to our scale
  title         TEXT NOT NULL,
  description   TEXT,
  resource_arn  TEXT,
  actor         TEXT,                                      -- IAM user / role / OAuth app for drift
  raw_s3_key    TEXT NOT NULL,                             -- pointer to full payload in S3
  normalized    JSONB NOT NULL,
  fired_at      TIMESTAMPTZ NOT NULL,
  ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  push_sent     BOOLEAN NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_events_tenant_kind_fired ON events(tenant_id, kind, fired_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_resource          ON events(resource_arn);
CREATE INDEX IF NOT EXISTS idx_events_source_fired     ON events(source, fired_at DESC);

-- 1:1 extension for drift_events.kind='drift'
CREATE TABLE IF NOT EXISTS drift_events (
  event_id      UUID PRIMARY KEY REFERENCES events(event_id) ON DELETE CASCADE,
  action        TEXT NOT NULL,                             -- 'AuthorizeSecurityGroupIngress', 'PutBucketPolicy', ...
  before_state  JSONB,                                     -- nullable (CloudTrail-only events lack before/after)
  after_state   JSONB
);

CREATE INDEX IF NOT EXISTS idx_drift_action ON drift_events(action);

-- ===== Compliance scores =====

CREATE TABLE IF NOT EXISTS scores (
  score_id      UUID PRIMARY KEY,
  tenant_id     UUID NOT NULL REFERENCES tenants(tenant_id),
  conn_id       UUID,                                      -- nullable for tenant-wide composite
  framework     TEXT NOT NULL,                             -- 'soc2' | 'iso27001' | 'cis_aws' | 'cis_azure' | 'mcsb' | ...
  score         INTEGER NOT NULL,                          -- 0-100
  scan_id       UUID REFERENCES scans(scan_id),
  computed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_scores_tenant_framework_computed ON scores(tenant_id, framework, computed_at DESC);

-- ===== User feedback (thumbs on findings + events) =====

CREATE TABLE IF NOT EXISTS feedback (
  feedback_id   UUID PRIMARY KEY,
  tenant_id     UUID NOT NULL REFERENCES tenants(tenant_id),
  user_id       UUID NOT NULL REFERENCES users(user_id),
  target_kind   TEXT NOT NULL,                             -- 'finding' | 'event'
  target_id     UUID NOT NULL,
  sentiment     TEXT NOT NULL,                             -- 'up' | 'down'
  reason        TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_feedback_tenant_target ON feedback(tenant_id, target_kind, target_id);

-- ===== LLM response cache =====

CREATE TABLE IF NOT EXISTS llm_cache (
  cache_key             TEXT PRIMARY KEY,
  tenant_id             UUID,                              -- nullable for tenant-independent prompts
  prompt_type           TEXT NOT NULL,                     -- 'finding_why' | 'finding_board' | 'finding_team_q' | 'drift_narr' | 'alert_narr'
  response              TEXT NOT NULL,
  model_version         TEXT NOT NULL,
  generated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  source_last_modified  TEXT
);

CREATE INDEX IF NOT EXISTS idx_llm_cache_tenant_type ON llm_cache(tenant_id, prompt_type);
