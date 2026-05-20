-- platform/sql/008_findings_natural_key.sql
-- Per-scan finding dedup.
--
-- unified_writer._insert_finding INSERTed a fresh findings row on every
-- scan, so the table accumulated one copy of every finding per scan. Add a
-- natural-key unique index so the writer can UPSERT (ON CONFLICT) instead —
-- refreshing last_seen + state, keeping first_seen.
--
-- Existing rows are unrecoverably wrong (domain was hardcoded 'ai', status
-- hardcoded 'fail') and carry cross-scan duplicates that would block the
-- unique index. Nothing FK-references findings and no risks row links to a
-- finding, so a clean purge is safe; the next scan repopulates correctly.
--
-- See: docs/superpowers/specs/2026-05-20-ai-discovery-connectors-design.md

BEGIN;

DELETE FROM findings;

CREATE UNIQUE INDEX findings_natural_key_idx ON findings
  (tenant_id, conn_id, check_id, COALESCE(resource_arn, ''), COALESCE(region, ''));

COMMIT;
