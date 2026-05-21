-- platform/sql/009_scan_tier.sql
-- AWS scanner uplift, Slice 0: record which depth tier a scan ran at.
--
-- The uplifted scanner runs at one of three tiers (quick | medium | deep).
-- The app shows the tier in scan history; the scanner reads it to filter
-- the check registry. Existing rows predate tiers — backfill to 'quick'
-- (the legacy scan was a shallow single-region pass, closest to quick).
--
-- See: docs/superpowers/specs/2026-05-20-aws-scanner-uplift-design.md §9

BEGIN;

ALTER TABLE scans
  ADD COLUMN tier TEXT NOT NULL DEFAULT 'quick'
  CHECK (tier IN ('quick', 'medium', 'deep'));

COMMIT;
