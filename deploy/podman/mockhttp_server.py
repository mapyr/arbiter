"""Demo MCP server — HTTP fetch held by Hangar → arbiter voter quorum.

Local OpenCode ``bash curl`` is denied in the demo project; agents must call
this tool via Hangar so three voters see the request.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from mcp.server.mcpserver import MCPServer

server = MCPServer("mockhttp")

_MAX_BODY = 4096


@server.tool()
def curl(url: str, method: str = "GET", timeout_seconds: float = 10.0) -> str:
    """Fetch a URL (held → three-model quorum before execution).

    Args:
        url: Absolute http(s) URL to request.
        method: HTTP method (GET/HEAD only in this demo).
        timeout_seconds: Socket timeout.
    """
    method_u = (method or "GET").upper()
    if method_u not in ("GET", "HEAD"):
        return json.dumps(
            {"ok": False, "error": f"method {method_u!r} not allowed in demo"}
        )
    if not (url.startswith("http://") or url.startswith("https://")):
        return json.dumps({"ok": False, "error": "url must be http(s)"})

    req = Request(url, method=method_u, headers={"User-Agent": "arbiter-mockhttp/1"})
    try:
        with urlopen(req, timeout=float(timeout_seconds)) as resp:
            raw = resp.read(_MAX_BODY + 1)
            truncated = len(raw) > _MAX_BODY
            body = raw[:_MAX_BODY].decode("utf-8", errors="replace")
            payload: dict[str, Any] = {
                "ok": True,
                "status": getattr(resp, "status", None),
                "url": url,
                "method": method_u,
                "truncated": truncated,
                "body": body,
            }
            return json.dumps(payload, ensure_ascii=False)
    except HTTPError as exc:
        raw = exc.read(_MAX_BODY) if exc.fp else b""
        return json.dumps(
            {
                "ok": False,
                "status": exc.code,
                "url": url,
                "error": str(exc.reason),
                "body": raw.decode("utf-8", errors="replace"),
            },
            ensure_ascii=False,
        )
    except URLError as exc:
        return json.dumps(
            {"ok": False, "url": url, "error": str(exc.reason)},
            ensure_ascii=False,
        )
    except Exception as exc:  # noqa: BLE001 — surface to agent
        return json.dumps(
            {"ok": False, "url": url, "error": f"{type(exc).__name__}:{exc}"},
            ensure_ascii=False,
        )


if __name__ == "__main__":
    asyncio.run(server.run_stdio_async())
