"""Demo MCP server held by Hangar (approval_list) in the Podman stack."""
from __future__ import annotations

import asyncio

from mcp.server.mcpserver import MCPServer

server = MCPServer("mockfs")

_NOTES: dict[str, str] = {}


@server.tool()
def write_note(path: str, content: str) -> str:
    """Create/overwrite a note (held → arbiter)."""
    _NOTES[path] = content
    return f"ok: wrote {len(content)} bytes to {path}"


@server.tool()
def append_note(path: str, content: str) -> str:
    """Append to a note (held → arbiter)."""
    _NOTES[path] = _NOTES.get(path, "") + content
    return f"ok: append {len(content)} bytes to {path}"


@server.tool()
def delete_note(path: str) -> str:
    """Delete a note (held → arbiter)."""
    existed = path in _NOTES
    _NOTES.pop(path, None)
    return f"ok: deleted={existed} path={path}"


@server.tool()
def rename_note(src: str, dst: str) -> str:
    """Rename a note (held → arbiter)."""
    if src not in _NOTES:
        return f"err: missing {src}"
    _NOTES[dst] = _NOTES.pop(src)
    return f"ok: renamed {src} -> {dst}"


@server.tool()
def read_note(path: str) -> str:
    """Read a note (also held in deep demo)."""
    if path not in _NOTES:
        return f"err: missing {path}"
    return _NOTES[path]


@server.tool(name="migrate.dry_run")
def migrate_dry_run(migration: str) -> str:
    """Trial a named migration (held). Same ``migration`` as apply."""
    return f"ok: dry_run {migration}"


@server.tool(name="migrate.apply")
def migrate_apply(migration: str) -> str:
    """Apply a named migration (held). Ledger requires a matching dry_run."""
    return f"ok: apply {migration}"


@server.tool()
def contract_test(path: str) -> str:
    """Record a contract test for ``path`` (held). Satisfies require_contract_test."""
    return f"ok: contract_test {path}"


if __name__ == "__main__":
    asyncio.run(server.run_stdio_async())
