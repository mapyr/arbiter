#!/usr/bin/env python3
"""Cursor hook: map the event to a hold notification; Arbiter decides allow/deny.

Copy this file to ``.cursor/hooks/arbiter-hold.py`` and ``hooks.json`` beside it.
Requires ``arbiter`` on PATH and ``ARBITER_DATA_DIR`` / intercept / voters / rules.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Any

WRITE_TOOLS = {
    "write",
    "strreplace",
    "tabwrite",
    "edit",
    "apply_patch",
    "applypatch",
}


def _tool_name(event: dict[str, Any]) -> str:
    for key in ("tool_name", "tool", "toolName"):
        val = event.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _input(event: dict[str, Any]) -> dict[str, Any]:
    for key in ("tool_input", "arguments", "input", "toolInput"):
        val = event.get(key)
        if isinstance(val, dict):
            return dict(val)
    return {}


def map_event(event: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
    """Return (mcp_server, tool, arguments), or None to skip (control plane)."""
    name = _tool_name(event)
    lower = name.lower()
    payload = _input(event)
    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else os.getcwd()

    server = event.get("mcp_server") or event.get("server") or event.get("mcpServer")
    if isinstance(server, str) and server.strip():
        srv = server.strip()
        if srv.startswith("hangar") or srv == "arbiter":
            return None
        tool = str(event.get("mcp_tool") or event.get("tool") or name or "call")
        if tool.startswith("hangar"):
            return None
        return srv, tool, payload

    if lower.startswith("hangar") or lower.startswith("mcp: hangar"):
        return None

    if "command" in event and isinstance(event.get("command"), str) and not name:
        return "cursor", "shell", {"command": event["command"]}

    if lower in WRITE_TOOLS or lower.endswith("write") or lower.endswith("edit"):
        args = dict(payload)
        path = args.get("path") or args.get("file_path") or args.get("filePath")
        if isinstance(path, str) and path.strip() and cwd:
            args["path"] = _rel(path.strip(), cwd)
        return "cursor", "write", args

    if lower in {"shell", "bash"} or event.get("command"):
        cmd = payload.get("command") if isinstance(payload.get("command"), str) else event.get("command")
        return "cursor", "shell", {"command": cmd or ""}

    tool = lower.replace(" ", "_") or "unknown"
    return "cursor", tool, payload


def _rel(path: str, cwd: str) -> str:
    abs_path = path if os.path.isabs(path) else os.path.normpath(os.path.join(cwd, path))
    try:
        rel = os.path.relpath(abs_path, cwd)
    except ValueError:
        return path.replace("\\", "/")
    if rel.startswith(".."):
        return path.replace("\\", "/")
    return rel.replace("\\", "/")


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        print(json.dumps({"permission": "deny", "agent_message": "invalid hook JSON"}))
        return 0
    if not isinstance(event, dict):
        print(json.dumps({"permission": "deny", "agent_message": "hook event must be an object"}))
        return 0
    mapped = map_event(event)
    if mapped is None:
        print(json.dumps({"permission": "allow"}))
        return 0
    server, tool, arguments = mapped
    arbiter = shutil.which("arbiter")
    if not arbiter:
        print(json.dumps({"permission": "deny", "agent_message": "arbiter CLI not on PATH"}))
        return 0
    proc = subprocess.run(
        [
            arbiter,
            "hold",
            "--mcp-server",
            server,
            "--tool",
            tool,
            "--arguments-json",
            json.dumps(arguments, separators=(",", ":")),
        ],
        capture_output=True,
        text=True,
        env=os.environ,
    )
    try:
        result = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        print(
            json.dumps(
                {
                    "permission": "deny",
                    "agent_message": (proc.stderr or proc.stdout or "arbiter hold failed")[:300],
                }
            )
        )
        return 0
    allowed = bool(result.get("approved"))
    reason = str(result.get("reason") or "")
    print(
        json.dumps(
            {
                "permission": "allow" if allowed else "deny",
                "agent_message": reason,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
