# platform/lambda/chat_session/messages.py
"""Append a fully-formed message to a conversation."""
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
