-- 011_phase_soc.sql — AI-powered SOC sub-project Slice 1 schema migration
-- Refs: docs/superpowers/specs/2026-05-25-ai-powered-soc-design.md §6

-- AI enrichment fields on events (populated async by soc_enrichment Lambda)
ALTER TABLE events ADD COLUMN IF NOT EXISTS ai_narrative      TEXT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS ai_anomaly_class  TEXT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS ai_anomaly_score  INTEGER;
ALTER TABLE events ADD COLUMN IF NOT EXISTS ai_next_steps     JSONB;
ALTER TABLE events ADD COLUMN IF NOT EXISTS ai_features       JSONB;
ALTER TABLE events ADD COLUMN IF NOT EXISTS ai_model_version  TEXT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS ai_enriched_at    TIMESTAMPTZ;

-- Kill-chain pre-commitments (nullable; future correlator populates)
ALTER TABLE events ADD COLUMN IF NOT EXISTS mitre_technique   TEXT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS incident_id       UUID;

-- Idempotency: provider-native event ID + unique constraint
ALTER TABLE events ADD COLUMN IF NOT EXISTS source_event_id   TEXT;
-- Unique only when source_event_id is present (legacy rows have NULL)
CREATE UNIQUE INDEX IF NOT EXISTS uq_events_tenant_source_sei
  ON events (tenant_id, source, source_event_id)
  WHERE source_event_id IS NOT NULL;

-- Drift graph-shape pre-commitment (redundant with events.resource_arn — explicit for future entity graph)
ALTER TABLE drift_events ADD COLUMN IF NOT EXISTS target_resource_arn TEXT;

-- Query indices for /soc (idx_events_tenant_kind_fired already exists in 002_phase_a.sql:114)
CREATE INDEX IF NOT EXISTS idx_events_tenant_anomaly
  ON events (tenant_id, ai_anomaly_class, fired_at DESC)
  WHERE ai_anomaly_class IN ('unusual','suspicious');

CREATE INDEX IF NOT EXISTS idx_events_incident
  ON events (incident_id) WHERE incident_id IS NOT NULL;
