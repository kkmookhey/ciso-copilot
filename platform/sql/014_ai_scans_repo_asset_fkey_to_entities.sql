-- platform/sql/014_ai_scans_repo_asset_fkey_to_entities.sql
-- Repoint ai_scans.repo_asset_id FK from the retired ai_assets table to the
-- unified entities table (introduced in migration 005).
--
-- The SP1 unified-entity migration replaced the ai_assets table with entities
-- for application reads/writes, but the foreign key on ai_scans was never
-- updated. The result was that every POST /ai/scans after the entities_api
-- Lambda redeploy on 2026-05-19 silently failed with FK constraint violation
-- 23503 — caught by the handler's blanket try/except and returned to the SPA
-- as a generic 500 with no CloudWatch breadcrumb. Detected 2026-05-27 during
-- ICICI Lombard demo prep.
--
-- We use NOT VALID so existing pre-migration ai_scans rows (whose
-- repo_asset_id values reference the retired ai_assets) are not re-validated.
-- They become historical orphans referencing nothing in entities; the
-- repo_asset_id column is NOT NULL so we cannot null them out. Acceptable
-- given ai_assets is retired and the affected rows are demo-tier artifacts.

BEGIN;

ALTER TABLE ai_scans DROP CONSTRAINT ai_scans_repo_asset_id_fkey;

ALTER TABLE ai_scans
  ADD CONSTRAINT ai_scans_repo_asset_id_fkey
  FOREIGN KEY (repo_asset_id) REFERENCES entities(id) NOT VALID;

COMMIT;
