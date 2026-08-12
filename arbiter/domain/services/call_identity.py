"""Idempotency key for a held tool call (I1) — from Hangar payload fields.

The key is ``mcp_server_id`` + ``tool_name`` + ``arguments_hash`` as carried on
``ApprovalRequest``. Do not re-hash arguments locally.

Caveat (documented intentionally): ``arguments_hash`` covers arguments only,
not caller/tenant/correlation context. Two identical tool calls in different
situations share the same key. That is correct for decision correlation — the
decision is about what happens, not who is asking — but it is a conscious
choice, not an accident.
"""

from __future__ import annotations

from arbiter.domain.errors import DomainError


def call_identity(
    *,
    mcp_server_id: str,
    tool_name: str,
    arguments_hash: str,
) -> str:
    if not isinstance(arguments_hash, str) or not arguments_hash:
        raise DomainError("arguments_hash must be a non-empty string")
    return f"{mcp_server_id}/{tool_name}/{arguments_hash}"
