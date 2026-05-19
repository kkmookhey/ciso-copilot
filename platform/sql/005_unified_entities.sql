-- platform/sql/005_unified_entities.sql
-- SP1 — Unified entity + edge model. Spec: docs/superpowers/specs/2026-05-19-sp1-unified-entity-model-design.md §4.

BEGIN;

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

-- Findings linkage
ALTER TABLE findings ADD COLUMN subject_entity_id UUID REFERENCES entities(id);
CREATE INDEX findings_subject_entity_idx ON findings(subject_entity_id)
  WHERE subject_entity_id IS NOT NULL;

COMMIT;
