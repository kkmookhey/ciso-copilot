# platform/lambda/chat_session/messages.py
"""Append and update conversation messages."""
from __future__ import annotations

import json
import uuid

from _db import _q


VALID_ROLES = {"user", "assistant", "tool", "system"}


def append(conversation_id: str, role: str, content: dict) -> dict:
    if role not in VALID_ROLES:
        raise ValueError(f"bad role {role}")
    mid = str(uuid.uuid4())
    _q(
        "INSERT INTO conversation_messages (id, conversation_id, role, content) "
        "VALUES (:id::uuid, :cid::uuid, :role, :content::jsonb)",
        {"id": mid, "cid": conversation_id, "role": role,
         "content": json.dumps(content)},
    )
    _q(
        "UPDATE conversations SET last_activity_at = NOW(), updated_at = NOW() "
        "WHERE id = :cid::uuid",
        {"cid": conversation_id},
    )
    return {"message_id": mid}


def update_content(conversation_id: str, message_id: str, content: dict) -> bool:
    """Replace a message's JSONB content in-place.

    Scoped to conversation_id so callers cannot update messages that belong
    to a different conversation. Returns True if a row was updated.
    """
    rows = _q(
        "UPDATE conversation_messages SET content = :content::jsonb "
        "WHERE id = :mid::uuid AND conversation_id = :cid::uuid "
        "RETURNING id::text",
        {"content": json.dumps(content), "mid": message_id, "cid": conversation_id},
    )
    return bool(rows)
