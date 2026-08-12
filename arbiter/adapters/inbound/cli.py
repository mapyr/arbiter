"""CLI entry — serve, coverage check, commit verify, plan gate, hangar-call."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from arbiter.adapters.inbound.mcp_server import create_server
from arbiter.application.services.commit_guard import extract_decision_id
from arbiter.bootstrap import create_application
from arbiter.domain.errors import DomainError


def _with_health_route(app: object) -> object:
    """ASGI wrapper: GET /health → 200; everything else → inner app.

    Hangar docker discovery validates HTTP MCP servers with GET /health
    before registration (default path; overridable via label).
    """

    async def asgi(scope, receive, send):  # type: ignore[no-untyped-def]
        if scope.get("type") == "http" and scope.get("path") == "/health":
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send({"type": "http.response.body", "body": b"ok"})
            return
        await app(scope, receive, send)  # type: ignore[operator]

    return asgi


def serve(
    *,
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8765,
    mcp_path: str = "/mcp",
) -> None:
    server = create_server()
    if transport == "stdio":
        server.run(transport="stdio")
        return
    if transport == "http":
        import uvicorn

        path = mcp_path if mcp_path.startswith("/") else f"/{mcp_path}"
        # Hangar docker discovery builds endpoint as http://host:port (no /mcp).
        # Podman demos therefore often serve at "/" so the discovered URL works.
        mcp_app = server.streamable_http_app(
            streamable_http_path=path,
            host=host,
        )
        secret = os.environ.get("ARBITER_HTTP_SECRET")
        allow_insecure = os.environ.get("ARBITER_ALLOW_INSECURE_HTTP") == "1"
        if secret:
            from arbiter.adapters.inbound.http_secret import SharedSecretASGI

            mcp_app = SharedSecretASGI(mcp_app, secret)
        elif not allow_insecure:
            raise SystemExit(
                "ARBITER_HTTP_SECRET is required for HTTP transport "
                "(or set ARBITER_ALLOW_INSECURE_HTTP=1 on a private network; "
                "see README — not a security model)"
            )
        else:
            print(
                "WARNING: ARBITER_ALLOW_INSECURE_HTTP=1 — HTTP MCP has no shared secret",
                file=sys.stderr,
            )

        # Hangar discovery health probe defaults to GET /health (must be 2xx).
        # Keep it outside the shared-secret wrapper so probes need no header.
        asgi_app = _with_health_route(mcp_app)
        uvicorn.run(asgi_app, host=host, port=port, log_level="info")
        return
    raise SystemExit(f"unsupported transport: {transport!r}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arbiter",
        description="Deterministic MCP decision gateway",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve_p = sub.add_parser("serve", help="Start the MCP server")
    serve_p.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="stdio (default) or streamable HTTP",
    )
    serve_p.add_argument("--host", default="127.0.0.1", help="HTTP bind host")
    serve_p.add_argument("--port", type=int, default=8765, help="HTTP bind port")
    serve_p.add_argument(
        "--mcp-path",
        default=os.environ.get("ARBITER_HTTP_MCP_PATH", "/mcp"),
        help="Streamable HTTP path (default /mcp; use / for Hangar docker discovery)",
    )

    cov = sub.add_parser(
        "check-coverage",
        help="Ask whether paths are covered by a resolved allow decision",
    )
    cov.add_argument("--path", action="append", dest="paths", default=[], required=True)
    cov.add_argument("--tool", default="edit")
    cov.add_argument("--decision-id", default=None)
    cov.add_argument("--actor", default=None)
    cov.add_argument(
        "--break-glass",
        action="store_true",
        help="Emergency bypass (records break_glass.used); also ARBITER_BREAK_GLASS=1",
    )
    cov.add_argument("--break-glass-reason", default=None)
    cov.add_argument("--json", action="store_true")

    pol = sub.add_parser(
        "get-gate-policy",
        help="Print client_gate policy from arbiter.rules.yaml",
    )
    pol.add_argument("--json", action="store_true", default=True)

    ens = sub.add_parser(
        "ensure-plan",
        help="Validate plan JSON, open decision, run model quorum",
    )
    ens.add_argument(
        "--plan-file",
        type=Path,
        required=True,
        help="Path to structured plan JSON",
    )
    ens.add_argument("--ttl-seconds", type=int, default=900)
    ens.add_argument("--criticality", default=None)
    ens.add_argument("--json", action="store_true", default=True)

    hc = sub.add_parser(
        "hangar-call",
        help="Call a tool on an MCP server via Hangar (plugin transport)",
    )
    hc.add_argument(
        "--mcp-server",
        default=os.environ.get("ARBITER_MCP_SERVER", "arbiter"),
    )
    hc.add_argument("--tool", required=True)
    hc.add_argument(
        "--arguments-json",
        default="{}",
        help="JSON object of tool arguments",
    )
    hc.add_argument(
        "--hangar-url",
        default=os.environ.get("HANGAR_URL")
        or os.environ.get("HANGAR_MCP_URL"),
    )
    hc.add_argument(
        "--api-key",
        default=os.environ.get("HANGAR_API_KEY"),
    )
    hc.add_argument("--timeout-seconds", type=float, default=180.0)
    hc.add_argument("--json", action="store_true", default=True)

    ver = sub.add_parser(
        "verify-commit",
        help="Layer-3 gate: critical paths require a covering decision trailer",
    )
    ver.add_argument(
        "--paths-from",
        choices=["staged", "range", "args"],
        default="staged",
        help="Where to read changed paths",
    )
    ver.add_argument("--base", default="origin/main", help="git diff base for --paths-from=range")
    ver.add_argument("--path", action="append", dest="paths", default=[])
    ver.add_argument("--message-file", type=Path, default=None)
    ver.add_argument("--message", default=None)
    ver.add_argument("--decision-id", default=None)
    ver.add_argument(
        "--allow-break-glass",
        action="store_true",
        help="CI human ack for break-glass events (also ARBITER_ALLOW_BREAK_GLASS=1)",
    )
    ver.add_argument("--commit-at", default=None, help="ISO timestamp of the commit")
    ver.add_argument("--json", action="store_true")

    rep = sub.add_parser(
        "report-eval",
        help="Evaluation report from the ledger (and git reversibility)",
    )
    rep.add_argument("--horizon-days", type=int, default=14)
    rep.add_argument("--repo", type=Path, default=None, help="git repo root for reversals")
    rep.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="markdown",
    )
    return parser


def _git_paths(mode: str, base: str) -> list[str]:
    if mode == "staged":
        cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"]
    elif mode == "range":
        cmd = ["git", "diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD"]
    else:
        return []
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"git failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]


def _cmd_check_coverage(args: argparse.Namespace) -> int:
    app = create_application()
    break_glass = bool(args.break_glass) or os.environ.get("ARBITER_BREAK_GLASS") == "1"
    result = app.check_coverage(
        paths=list(args.paths),
        tool=args.tool,
        decision_id=args.decision_id,
        actor=args.actor or os.environ.get("USER") or os.environ.get("GITHUB_ACTOR"),
        break_glass=break_glass,
        break_glass_reason=args.break_glass_reason,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(
            f"{'ALLOW' if result['approved'] else 'DENY'} "
            f"path={result['path']} decision={result.get('decision_id')} "
            f"reason={result['reason']}"
        )
        if result.get("uncovered"):
            print("uncovered:", ", ".join(result["uncovered"]))
    return 0 if result["approved"] else 2


def _cmd_get_gate_policy(args: argparse.Namespace) -> int:
    app = create_application()
    try:
        result = app.get_gate_policy()
    except DomainError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _cmd_ensure_plan(args: argparse.Namespace) -> int:
    app = create_application()
    try:
        plan = json.loads(Path(args.plan_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"plan-file error: {exc}", file=sys.stderr)
        return 2
    try:
        result = asyncio.run(
            app.ensure_plan(
                plan,
                ttl_seconds=args.ttl_seconds,
                criticality=args.criticality,
            )
        )
    except DomainError as exc:
        print(json.dumps({"approved": False, "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("approved") else 2


def _cmd_hangar_call(args: argparse.Namespace) -> int:
    from arbiter.adapters.outbound.hangar_mcp_client import hangar_call_tool

    if not args.hangar_url or not args.api_key:
        print(
            "HANGAR_URL and HANGAR_API_KEY required (or --hangar-url / --api-key)",
            file=sys.stderr,
        )
        return 2
    try:
        arguments = json.loads(args.arguments_json)
    except json.JSONDecodeError as exc:
        print(f"arguments-json: {exc}", file=sys.stderr)
        return 2
    if not isinstance(arguments, dict):
        print("arguments-json must be a JSON object", file=sys.stderr)
        return 2
    try:
        result = asyncio.run(
            hangar_call_tool(
                hangar_url=str(args.hangar_url),
                api_key=str(args.api_key),
                mcp_server=str(args.mcp_server),
                tool=str(args.tool),
                arguments=arguments,
                timeout_seconds=float(args.timeout_seconds),
            )
        )
    except DomainError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _cmd_report_eval(args: argparse.Namespace) -> int:
    from arbiter.application.services.eval_report import render_markdown

    app = create_application()
    report = app.eval_report(
        repo=args.repo or Path.cwd(),
        horizon_days=args.horizon_days,
    )
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(render_markdown(report))
    return 0


def _cmd_verify_commit(args: argparse.Namespace) -> int:
    app = create_application()
    if args.paths_from == "args":
        paths = list(args.paths)
    else:
        paths = _git_paths(args.paths_from, args.base)
        paths.extend(args.paths)

    message = args.message or ""
    if args.message_file is not None:
        message = Path(args.message_file).read_text(encoding="utf-8")
    decision_id = args.decision_id or extract_decision_id(message)
    allow_glass = bool(args.allow_break_glass) or (
        os.environ.get("ARBITER_ALLOW_BREAK_GLASS") == "1"
    )
    commit_at = None
    if args.commit_at:
        from arbiter.domain.timeutil import parse_iso

        commit_at = parse_iso(args.commit_at)

    result = app.verify_commit_paths(
        paths=paths,
        decision_id=decision_id,
        commit_at=commit_at,
        allow_break_glass=allow_glass,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        status = "OK" if result["ok"] else "FAIL"
        print(f"{status} reason={result['reason']} decision={result.get('decision_id')}")
        if result.get("uncovered"):
            print("uncovered critical paths:", ", ".join(result["uncovered"]))
        if result.get("break_glass"):
            print(
                "break_glass events:",
                json.dumps(result["break_glass"], ensure_ascii=False),
            )
    return 0 if result["ok"] else 2


def main(argv: list[str] | None = None) -> None:
    raw = list(sys.argv[1:] if argv is None else argv)
    if not raw or raw[0].startswith("-"):
        raw = ["serve", *raw]
    args = _build_parser().parse_args(raw)
    if args.command == "serve":
        serve(
            transport=args.transport,
            host=args.host,
            port=args.port,
            mcp_path=args.mcp_path,
        )
        return
    if args.command == "check-coverage":
        raise SystemExit(_cmd_check_coverage(args))
    if args.command == "get-gate-policy":
        raise SystemExit(_cmd_get_gate_policy(args))
    if args.command == "ensure-plan":
        raise SystemExit(_cmd_ensure_plan(args))
    if args.command == "hangar-call":
        raise SystemExit(_cmd_hangar_call(args))
    if args.command == "verify-commit":
        raise SystemExit(_cmd_verify_commit(args))
    if args.command == "report-eval":
        raise SystemExit(_cmd_report_eval(args))
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
