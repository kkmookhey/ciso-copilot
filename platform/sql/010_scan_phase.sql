-- platform/sql/010_scan_phase.sql
-- Scan execution v2: a `phase` field so the app can show what a running
-- scan is doing, not merely that it is running.
--
-- Values: region_discovery | first_signal | crown_jewel | full | done.
-- Existing rows predate phases — backfill to 'done' (they are historical
-- completed/failed scans, not in-flight).
--
-- See: docs/superpowers/specs/2026-05-21-scan-performance-design.md §10.1

BEGIN;

ALTER TABLE scans
  ADD COLUMN phase TEXT NOT NULL DEFAULT 'done'
  CHECK (phase IN ('region_discovery', 'first_signal', 'crown_jewel',
                   'full', 'done'));

COMMIT;
