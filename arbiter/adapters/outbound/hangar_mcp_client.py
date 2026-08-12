"""Call a tool on an upstream MCP server via Hangar (streamable HTTP)."""

from __future__ import annotations

import json
from typing import Any

import httpx2

from arbiter.domain.errors import DomainError


async def hangar_call_tool(
    *,
    hangar_url: str,
    api_key: str,
    mcp_server: str,
    tool: str,
    arguments: dict[str, Any] | None = None,
    timeout_seconds: float = 180.0,
    start_server: bool = True,
) -> dict[str, Any]:
    """Invoke ``hangar_call`` against Hangar HTTP MCP; return parsed tool payload."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    base = hangar_url.rstrip("/")
    mcp_endpoint = base if base.endswith("/mcp") else f"{base}/mcp"
    headers = {"X-API-Key": api_key}
    args = dict(arguments or {})

    async with httpx2.AsyncClient(headers=headers, timeout=timeout_seconds) as http:
        async with streamable_http_client(mcp_endpoint, http_client=http) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                if start_server:
                    try:
                        await session.call_tool(
                            "hangar_start", {"mcp_server": mcp_server}
                        )
                    except Exception:  # noqa: BLE001 — already warm is fine
                        pass
                result = await session.call_tool(
                    "hangar_call",
                    {
                        "calls": [
                            {
                                "mcp_server": mcp_server,
                                "tool": tool,
                                "arguments": args,
                            }
                        ],
                        "timeout": timeout_seconds,
                    },
                )
    texts = [
        getattr(block, "text", None)
        for block in (result.content or [])
        if getattr(block, "text", None)
    ]
    if not texts:
        raise DomainError("hangar_call returned empty content")
    try:
        batch = json.loads(texts[0])
    except json.JSONDecodeError as exc:
        raise DomainError(f"hangar_call returned non-JSON: {texts[0][:200]}") from exc

    if not isinstance(batch, dict):
        raise DomainError("hangar_call batch must be an object")
    results = batch.get("results")
    if not isinstance(results, list) or not results:
        raise DomainError(f"hangar_call failed: {batch!r}"[:500])
    row = results[0]
    if not isinstance(row, dict):
        raise DomainError("hangar_call result row invalid")
    if not row.get("success"):
        err = row.get("error") or batch
        raise DomainError(f"hangar_call tool error: {err}")
    inner = row.get("result")
    return _unwrap_tool_result(inner)


def _unwrap_tool_result(inner: Any) -> dict[str, Any]:
    """Normalize Hangar/MCP nested tool result into a plain dict."""
    if isinstance(inner, dict):
        if "structuredContent" in inner and isinstance(inner["structuredContent"], dict):
            sc = inner["structuredContent"]
            if "result" in sc and isinstance(sc["result"], str):
                try:
                    parsed = json.loads(sc["result"])
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    pass
            if all(isinstance(k, str) for k in sc):
                # Prefer flattened tool return when present.
                if set(sc.keys()) == {"result"} and isinstance(sc.get("result"), dict):
                    return sc["result"]
                return sc
        content = inner.get("content")
        if isinstance(content, list) and content:
            text = content[0].get("text") if isinstance(content[0], dict) else None
            if isinstance(text, str):
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    return {"text": text}
        return inner
    if isinstance(inner, str):
        try:
            parsed = json.loads(inner)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {"text": inner}
    raise DomainError(f"cannot unwrap hangar tool result: {type(inner)}")
