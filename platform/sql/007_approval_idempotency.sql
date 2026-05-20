-- platform/sql/007_approval_idempotency.sql
-- SP4 Phase 4d — approval-card idempotency.
-- Adds source_approval_id (nullable UUID) to risks and policies so the
-- same approval-card UUID can never create two rows in the same tenant.
-- Spec: docs/superpowers/specs/2026-05-19-sp4-chat-first-design.md §8

-- The Aurora Data API executes each statement individually and does not
-- support multi-statement transactions. Each ALTER + CREATE INDEX is its
-- own call. No BEGIN/COMMIT wrapper needed (and they would be no-ops).

ALTER TABLE risks
  ADD COLUMN IF NOT EXISTS source_approval_id UUID;

ALTER TABLE policies
  ADD COLUMN IF NOT EXISTS source_approval_id UUID;

CREATE UNIQUE INDEX IF NOT EXISTS idx_risks_tenant_approval
  ON risks(tenant_id, source_approval_id)
  WHERE source_approval_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_policies_tenant_approval
  ON policies(tenant_id, source_approval_id)
  WHERE source_approval_id IS NOT NULL;
