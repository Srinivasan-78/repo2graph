"""Stdio MCP server lifecycle, command line interface, and protocol adapters."""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path
from typing import Any

from .. import __version__
from ..cache import DEFAULT_MAX_SIZE, DEFAULT_TTL, ResultCache
from .guardrails import INDEX_DIRNAME
from .tools import (
    TOOL_DESCRIPTIONS,
    TOOL_SCHEMAS,
    ToolError,
    dispatch,
    open_index,
    open_index_or_task,
    tool_annotations,
    tool_build_status,
    tool_cache_stats,
    tool_repo_blast_radius,
    tool_repo_find_symbol,
    tool_repo_impact,
    tool_repo_map,
    tool_repo_neighbours,
    tool_repo_path_between,
    tool_repo_read,
    tool_repo_search,
)

MISSING_SDK = 'the MCP server needs the optional `mcp` extra: pip install "repo2graph[mcp]"'
SDK_SPEC = "mcp>=2.0,<3.0"


def _sdk_version(module) -> str:
    """Best-effort version of the installed SDK."""
    version = getattr(module, "__version__", None)
    if version:
        return str(version)
    try:
        from importlib.metadata import version as _dist_version

        return str(_dist_version("mcp"))
    except Exception:
        return "unknown"


def _sdk_major(version: str) -> int | None:
    """Extract leading integer of a version string."""
    match = re.match(r"\s*(\d+)", version or "")
    return int(match.group(1)) if match else None


def _unusable_sdk(version: str, detail: str) -> str:
    return (
        f"the installed mcp SDK ({version}) is not supported by "
        f"repo2graph-mcp: {detail}. Install a compatible SDK instead: "
        f'pip install "{SDK_SPEC}" '
        '(or `pip install "repo2graph[mcp]"` in a clean environment).'
    )


def _require_sdk() -> Any:
    """Validate that a compatible MCP SDK is installed and return the module."""
    try:
        import mcp
    except ImportError:
        raise SystemExit(MISSING_SDK) from None
    if mcp is None:
        raise SystemExit(MISSING_SDK)
    try:
        from mcp.server import Server as _Server  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            _unusable_sdk(_sdk_version(mcp), f"`from mcp.server import Server` failed ({exc})")
        ) from None
    installed = _sdk_version(mcp)
    major = _sdk_major(installed)
    if major is not None and major < 2:
        raise SystemExit(
            _unusable_sdk(
                installed,
                f"mcp {installed} is a 1.x release, and 1.x hangs on the first "
                f"tool call under repo2graph-mcp (#407) -- repo2graph-mcp requires {SDK_SPEC}",
            )
        )
    from ..events import emit

    emit(
        "mcp_sdk_version",
        level="info",
        installed=installed,
        required=SDK_SPEC,
    )
    return mcp


def get_tools(types_module=None, auto_build: bool = False):
    """Construct Tool instances with descriptions, schemas, and annotations."""
    if types_module is None:
        try:
            import mcp.types as _types_module
        except ImportError:
            return []
        types_module = _types_module
    tool_cls = getattr(types_module, "Tool", None)
    if tool_cls is None:
        return []
    tool_ann_cls = getattr(types_module, "ToolAnnotations", None)
    tools = []
    for name, description in TOOL_DESCRIPTIONS.items():
        kwargs = {
            "name": name,
            "description": description,
            "inputSchema": TOOL_SCHEMAS[name],
        }
        tool_fields = getattr(tool_cls, "model_fields", None)
        if tool_fields is None:
            tool_fields = getattr(tool_cls, "__annotations__", {})
        if "annotations" in tool_fields or hasattr(tool_cls, "annotations"):
            ann = tool_annotations(name, auto_build)
            if tool_ann_cls is not None:
                try:
                    kwargs["annotations"] = tool_ann_cls(**ann)
                except Exception:
                    kwargs["annotations"] = ann
            else:
                kwargs["annotations"] = ann
        tools.append(tool_cls(**kwargs))
    return tools


class ServerWrapper:
    """Lightweight tool registry wrapper for MCP."""

    def __init__(self, name: str, version: str | None = None):
        self.name = name
        self.version = version
        self.tools: dict[str, dict[str, Any]] = {}

    def add_tool(self, name: str, handler: Any, schema: Any = None):
        self.tools[name] = {"handler": handler, "schema": schema}


server = ServerWrapper("repo2graph", version=__version__)
server.add_tool("repo_map", tool_repo_map, TOOL_SCHEMAS["repo_map"])
server.add_tool("repo_search", tool_repo_search, TOOL_SCHEMAS["repo_search"])
server.add_tool("repo_neighbours", tool_repo_neighbours, TOOL_SCHEMAS["repo_neighbours"])
server.add_tool("repo_find_symbol", tool_repo_find_symbol, TOOL_SCHEMAS["repo_find_symbol"])
server.add_tool("repo_read", tool_repo_read, TOOL_SCHEMAS["repo_read"])
server.add_tool("repo_path_between", tool_repo_path_between, TOOL_SCHEMAS["repo_path_between"])
server.add_tool("repo_impact", tool_repo_impact, TOOL_SCHEMAS["repo_impact"])
server.add_tool("repo_blast_radius", tool_repo_blast_radius, TOOL_SCHEMAS["repo_blast_radius"])
server.add_tool("repo_cache_stats", tool_cache_stats, TOOL_SCHEMAS["repo_cache_stats"])
server.add_tool("repo_build_status", tool_build_status, TOOL_SCHEMAS["repo_build_status"])


class ToolCallFailed(Exception):
    """Carries a ToolError message to the transport layer."""


def run_tool(
    index_dir, repo, name: str, arguments: dict[str, Any] | None, cache=None, tasks=None
) -> str:
    """Execute a single stdio tool call: ensure index exists, then dispatch."""
    name = name or ""
    if name == "repo_build_status":
        return dispatch(None, name, arguments or {}, cache=cache, tasks=tasks)
    if name not in TOOL_DESCRIPTIONS:
        return dispatch(None, name, arguments or {}, cache=cache, tasks=tasks)
    index, pending = open_index_or_task(index_dir, repo, cache, tasks)
    if pending is not None:
        return pending
    return dispatch(index, name, arguments or {}, cache=cache, tasks=tasks)


def serve(
    out: str | Path, repo: str | Path | None = None, cache: ResultCache | None = None, tasks=None
) -> None:
    """Run stdio MCP server over JSON-RPC."""
    mcp = _require_sdk()
    index_dir = Path(out)
    if repo is None:
        open_index(index_dir)

    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent

    async def list_tools_handler(*args, **kwargs):
        return get_tools(mcp.types, auto_build=(repo is not None))

    async def call_tool_handler(ctx, params):
        name = params.name
        arguments = params.arguments
        return run_tool(index_dir, repo, name, arguments, cache=cache, tasks=tasks)

    server_cls: Any = Server

    async def list_tools_2x(ctx, params):
        return mcp.types.ListToolsResult(tools=await list_tools_handler())

    async def call_tool_2x(ctx, params):
        text = await call_tool_handler(ctx, params)
        return mcp.types.CallToolResult(
            content=[TextContent(type="text", text=text)],
            isError=isinstance(text, ToolError),
        )

    try:
        mcp_server = server_cls(
            server.name,
            version=server.version,
            on_list_tools=list_tools_2x,
            on_call_tool=call_tool_2x,
        )
    except TypeError:
        mcp_server = server_cls(server.name, version=server.version)

    async def _run():
        async with stdio_server() as (read_stream, write_stream):
            await mcp_server.run(
                read_stream, write_stream, mcp_server.create_initialization_options()
            )

    asyncio.run(_run())


def resolve_paths(repo: str | None = None, out: str | None = None) -> tuple[Path, Path | None]:
    """Determine (index_dir, repo_to_build_from) from CLI arguments."""
    if repo is not None:
        repo_path = Path(repo)
        if not repo_path.is_dir():
            raise SystemExit(
                f"error: repository directory does not exist or is not a directory: {repo_path}"
            )
        return (Path(out) if out else repo_path / INDEX_DIRNAME), repo_path
    out_path = Path(out) if out else Path(INDEX_DIRNAME)
    if out_path.name == INDEX_DIRNAME and out_path.parent.is_dir():
        return out_path, out_path.parent
    return out_path, None


def main(argv=None) -> int:
    """Main CLI entrypoint for repo2graph-mcp."""
    p = argparse.ArgumentParser(
        prog="repo2graph-mcp", description="Serve a repo2graph index over MCP on stdio"
    )
    p.add_argument(
        "repo",
        nargs="?",
        default=None,
        help="repository to serve; its index is built on the first "
        "tool call if one does not exist yet "
        "(default: the directory holding --out)",
    )
    p.add_argument("-o", "--out", default=None, help="index directory (default: <repo>/.r2g)")
    p.add_argument(
        "--no-auto-build",
        action="store_true",
        help="never build: exit unless the index already exists",
    )
    p.add_argument(
        "--async-build",
        action="store_true",
        help="build a missing index on a background thread and "
        "return a task_id immediately, instead of blocking the "
        "first tool call until it finishes. Poll it with "
        "repo_build_status (default: off, build synchronously)",
    )
    p.add_argument(
        "--cache-size",
        type=int,
        default=DEFAULT_MAX_SIZE,
        metavar="N",
        help=f"cached tool results before the least recently used "
        f"is evicted; 0 disables the cache "
        f"(default: {DEFAULT_MAX_SIZE})",
    )
    p.add_argument(
        "--cache-ttl",
        type=float,
        default=DEFAULT_TTL,
        metavar="SECONDS",
        help=f"seconds a cached result is served before it is "
        f"recomputed (default: {DEFAULT_TTL:g})",
    )
    log = p.add_argument_group("audit logging")
    log.add_argument(
        "--audit-log",
        default=None,
        metavar="PATH",
        help="append audit records to this file as well as stderr (default: stderr only)",
    )
    log.add_argument(
        "--audit-log-level",
        choices=("none", "errors", "all"),
        default="all",
        help="which tool calls produce an audit record (default: all)",
    )
    log.add_argument(
        "--audit-log-fsync",
        action="store_true",
        help="sync each audit record to disk before returning",
    )
    args = p.parse_args(argv)
    index_dir, repo = resolve_paths(args.repo, args.out)
    cache = ResultCache(max_size=args.cache_size, ttl=args.cache_ttl)
    build_from = None if args.no_auto_build else repo

    from ..audit import AuditConfig, AuditLogger

    audit = AuditLogger(
        AuditConfig(
            level=args.audit_log_level,
            path=args.audit_log,
            fsync=args.audit_log_fsync,
        )
    )

    tasks = None
    if args.async_build:
        from ..tasks import TaskManager

        tasks = TaskManager()

    try:
        serve_fn = getattr(sys.modules.get("repo2graph.mcp"), "serve", serve)
        serve_fn(index_dir, build_from, cache=cache, tasks=tasks)
    finally:
        audit.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
