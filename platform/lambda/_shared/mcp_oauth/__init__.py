"""Per-user OAuth + remote-MCP client wrapper for Shasta connectors.

Public API:
  get_session(subject, kind, *, tenant_id) -> async context manager
  get_admin_session(tenant_id, kind)       -> async context manager (Slice 2)
  discover_tools(subject, *, tenant_id)    -> dict[kind, list[Tool]]

See docs/superpowers/specs/2026-05-28-mcp-connectors-design.md §7.
"""
from .session import (
    get_session,
    discover_tools,
    ConnectorMissingError,
    ConnectorRevokedError,
)
from .crypto import encrypt_token, decrypt_token  # noqa: F401
