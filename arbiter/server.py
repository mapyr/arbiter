"""Compatibility entry — implementation lives in adapters.inbound."""

from arbiter.adapters.inbound.cli import main, serve
from arbiter.adapters.inbound.mcp_server import (
    TOOL_DESCRIPTIONS,
    create_server,
    package_name,
    package_version,
)
from arbiter.bootstrap import create_application as create_ledger

__all__ = [
    "TOOL_DESCRIPTIONS",
    "create_ledger",
    "create_server",
    "main",
    "package_name",
    "package_version",
    "serve",
]

if __name__ == "__main__":
    main()
