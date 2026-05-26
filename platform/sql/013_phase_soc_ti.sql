-- 013_phase_soc_ti.sql — SOC Slice 1c: threat-intel substrate
-- Refs: docs/superpowers/specs/2026-05-25-ai-powered-soc-design.md §6
--       docs/superpowers/plans/2026-05-25-ai-powered-soc-slice-1c.md

-- Global (tenant-independent) IOC table. IOCs are public knowledge;
-- no tenant_id intentionally — keeps the table small and writes cheap.
CREATE TABLE IF NOT EXISTS threat_indicators (
  indicator_value   TEXT        NOT NULL,
  kind              TEXT        NOT NULL,   -- 'ip' | 'domain' | 'url' | 'sha256' | 'cve'
  source            TEXT        NOT NULL,   -- 'abusech_feodo' | 'abusech_threatfox' | 'kev' | 'tor' | 'greynoise_community'
  first_seen        TIMESTAMPTZ NOT NULL,
  last_seen         TIMESTAMPTZ NOT NULL,
  confidence        INTEGER,                -- 0-100, source-dependent (NULL when source has no native confidence)
  tags              JSONB       NOT NULL DEFAULT '[]'::jsonb,
  raw               JSONB,                  -- source-specific extras (malware family, CVE id, etc.)
  PRIMARY KEY (indicator_value, kind, source)
);

-- Fast lookup by value+kind across all sources for the enrichment Lambda.
CREATE INDEX IF NOT EXISTS idx_threat_indicators_value
  ON threat_indicators (indicator_value, kind);

-- Calling-side IP from CloudTrail mgmt events. Populated by event_router on INSERT.
-- Nullable: Config events have no sourceIPAddress; alert-kind events from
-- GuardDuty/Inspector also don't carry this shape.
ALTER TABLE events ADD COLUMN IF NOT EXISTS source_ip TEXT;
