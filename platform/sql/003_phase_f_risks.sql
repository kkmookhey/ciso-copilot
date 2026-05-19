-- Phase F: risk register (lifted from Shasta's risk model)
--
-- A risk is a tracked, owner-assigned, due-dated item — usually instantiated
-- from a finding but free-standing risks are also allowed (e.g., risks
-- entered by a CISO that don't tie to an automated scan).

CREATE TABLE IF NOT EXISTS risks (
  risk_id      UUID PRIMARY KEY,
  tenant_id    UUID NOT NULL REFERENCES tenants(tenant_id),
  title        TEXT NOT NULL,
  description  TEXT,
  severity     TEXT NOT NULL CHECK (severity IN ('critical','high','medium','low','info')),
  status       TEXT NOT NULL DEFAULT 'open'
               CHECK (status IN ('open','mitigated','accepted','transferred','closed')),
  owner        TEXT,                      -- email or display name
  due_date     DATE,
  finding_id   UUID,                       -- optional link to source finding
  notes        TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_risks_tenant_status ON risks(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_risks_finding       ON risks(finding_id);
