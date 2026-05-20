-- platform/sql/006_conversations.sql
-- SP4 — chat-first front door. Spec: docs/superpowers/specs/2026-05-19-sp4-chat-first-design.md §7.1

BEGIN;

CREATE TABLE conversations (
  id                UUID PRIMARY KEY,
  tenant_id         UUID NOT NULL REFERENCES tenants(tenant_id),
  user_id           UUID NOT NULL REFERENCES users(user_id),
  title             TEXT NOT NULL DEFAULT 'New conversation',
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_activity_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at        TIMESTAMPTZ
);

CREATE INDEX conversations_tenant_user_recent_idx
  ON conversations(tenant_id, user_id, last_activity_at DESC)
  WHERE deleted_at IS NULL;

CREATE TABLE conversation_messages (
  id              UUID PRIMARY KEY,
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role            TEXT NOT NULL
                   CHECK (role IN ('user','assistant','tool','system')),
  content         JSONB NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX conversation_messages_conv_created_idx
  ON conversation_messages(conversation_id, created_at);

COMMIT;
