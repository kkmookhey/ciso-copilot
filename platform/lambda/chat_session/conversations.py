# platform/lambda/chat_session/conversations.py
"""Conversation CRUD. All queries tenant+user scoped."""
from __future__ import annotations

import uuid

from _db import _q, _claim_value


def create(tenant_id: str, user_id: str, title: str = "New conversation") -> dict:
    cid = str(uuid.uuid4())
    _q(
        "INSERT INTO conversations (id, tenant_id, user_id, title) "
        "VALUES (:id::uuid, :tenant_id::uuid, :user_id::uuid, :title)",
        {"id": cid, "tenant_id": tenant_id, "user_id": user_id, "title": title},
    )
    return {"conversation_id": cid}


def list_for(tenant_id: str, user_id: str) -> dict:
    rows = _q(
        "SELECT id::text, title, last_activity_at::text "
        "FROM conversations "
        "WHERE tenant_id = :tenant_id::uuid AND user_id = :user_id::uuid "
        "AND deleted_at IS NULL "
        "ORDER BY last_activity_at DESC LIMIT 100",
        {"tenant_id": tenant_id, "user_id": user_id},
    )
    return {
        "conversations": [
            {
                "id": _claim_value(r[0]),
                "title": _claim_value(r[1]),
                "last_activity_at": _claim_value(r[2]),
            }
            for r in rows
        ]
    }


def get(tenant_id: str, conversation_id: str) -> dict | None:
    """Return conversation + ordered messages, or None if not in tenant."""
    head = _q(
        "SELECT id::text, title FROM conversations "
        "WHERE id = :id::uuid AND tenant_id = :tenant_id::uuid "
        "AND deleted_at IS NULL",
        {"id": conversation_id, "tenant_id": tenant_id},
    )
    if not head:
        return None
    msgs = _q(
        "SELECT role, content::text, created_at::text "
        "FROM conversation_messages "
        "WHERE conversation_id = :id::uuid ORDER BY created_at",
        {"id": conversation_id},
    )
    import json

    return {
        "id": _claim_value(head[0][0]),
        "title": _claim_value(head[0][1]),
        "messages": [
            {
                "role": _claim_value(m[0]),
                "content": json.loads(_claim_value(m[1])),
                "created_at": _claim_value(m[2]),
            }
            for m in msgs
        ],
    }


def patch_title(tenant_id: str, conversation_id: str, title: str) -> bool:
    rows = _q(
        "UPDATE conversations SET title = :title, updated_at = NOW() "
        "WHERE id = :id::uuid AND tenant_id = :tenant_id::uuid "
        "RETURNING id::text",
        {"title": title, "id": conversation_id, "tenant_id": tenant_id},
    )
    return bool(rows)


def soft_delete(tenant_id: str, conversation_id: str) -> bool:
    rows = _q(
        "UPDATE conversations SET deleted_at = NOW() "
        "WHERE id = :id::uuid AND tenant_id = :tenant_id::uuid "
        "AND deleted_at IS NULL RETURNING id::text",
        {"id": conversation_id, "tenant_id": tenant_id},
    )
    return bool(rows)
