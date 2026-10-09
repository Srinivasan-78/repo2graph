"""Stdio MCP server lifecycle, command line interface, and protocol adapters."""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path
from typing import Any

from .. import __version__
from ..audit import timer as audit_timer
from ..cache import DEFAULT_MAX_SIZE, DEFAULT_TTL, ResultCache
from .guardrails import INDEX_DIRNAME
from .tools import (
    TOOL_DESCRIPTIONS,
    TOOL_SCHEMAS,
    ToolError,
    _staleness_note,
    dispatch,
    open_index,
    open_index_or_task,
    tool_annotations,
    tool_build_status,
    tool_cache_stats,
    tool_repo_blast_radius,
    tool_repo_find_symbol,
    tool_repo_map,
    tool_repo_neighbours,
    tool_repo_path_between,
    tool_repo_read,
    tool_repo_search,
)

MISSING_SDK = 'the MCP server needs the optional `mcp` extra: pip install "repo2graph[mcp]"'
SDK_SPEC = "mcp>=2.0,<3.0"


def _sdk_version(module: Any) -> str:
    """Best-effort version of the installed SDK."""
    version = getattr(module, "__version__", None)
    if version:
        return str(version)
    try:
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as _dist_version

        return str(_dist_version("mcp"))
    except (PackageNotFoundError, OSError, ValueError):
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


def get_tools(types_module: Any = None, auto_build: bool = False) -> list[Any]:
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
                except (TypeError, ValueError):
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

    def add_tool(self, name: str, handler: Any, schema: Any = None) -> None:
        self.tools[name] = {"handler": handler, "schema": schema}


server = ServerWrapper("repo2graph", version=__version__)
server.add_tool("repo_map", tool_repo_map, TOOL_SCHEMAS["repo_map"])
server.add_tool("repo_search", tool_repo_search, TOOL_SCHEMAS["repo_search"])
server.add_tool("repo_neighbours", tool_repo_neighbours, TOOL_SCHEMAS["repo_neighbours"])
server.add_tool("repo_find_symbol", tool_repo_find_symbol, TOOL_SCHEMAS["repo_find_symbol"])
server.add_tool("repo_read", tool_repo_read, TOOL_SCHEMAS["repo_read"])
server.add_tool("repo_path_between", tool_repo_path_between, TOOL_SCHEMAS["repo_path_between"])
server.add_tool("repo_blast_radius", tool_repo_blast_radius, TOOL_SCHEMAS["repo_blast_radius"])
server.add_tool("repo_cache_stats", tool_cache_stats, TOOL_SCHEMAS["repo_cache_stats"])
server.add_tool("repo_build_status", tool_build_status, TOOL_SCHEMAS["repo_build_status"])


class ToolCallFailed(Exception):
    """Carries a ToolError message to the transport layer."""


def run_tool(
    index_dir: str | Path,
    repo: str | Path | None,
    name: str,
    arguments: dict[str, Any] | None,
    cache: Any = None,
    tasks: Any = None,
    audit: Any = None,
    *,
    allow_foreign_index: bool = False,
) -> str:
    """Execute a single stdio tool call: ensure index exists, then dispatch.

    Args:
        index_dir: Directory holding the index to answer from.
        repo: Repository to auto-build from, or None to require an existing index.
        name: Tool name the caller asked for.
        arguments: The caller's arguments, sanitized downstream before logging.
        cache: Optional result cache.
        tasks: Optional background build registry.
        audit: Optional `AuditLogger`; one record is written per call when given.
        allow_foreign_index: Whether to permit opening an index built elsewhere.

    Returns:
        The tool's rendered text, or a `ToolError` when the call failed.
    """
    name = name or ""
    args = arguments or {}
    with audit_timer() as elapsed:
        try:
            text = _dispatch_tool(
                index_dir, repo, name, args, cache, tasks, allow_foreign_index=allow_foreign_index
            )
        except (SystemExit, ValueError) as exc:
            # open_index() exits the process when it finds no index and has no
            # repo to build one from, or raises ValueError when the index is foreign.
            # That is right for the startup preflight and for a direct call, but this
            # is the per-request path: an index deleted or rejected while the server runs
            # should fail the tool call with a ToolError rather than crashing the server.
            text = ToolError(str(exc) or "the index is no longer available")
            _audit(audit, name, args, elapsed, text=text, error=exc)
            return text
        except Exception as exc:
            # The record has to survive a failing tool, and it is the only
            # place the failure is reported when stderr is the sole sink.
            _audit(audit, name, args, elapsed, error=exc)
            raise
    _audit(audit, name, args, elapsed, text=text)
    return text


def _dispatch_tool(
    index_dir: str | Path,
    repo: str | Path | None,
    name: str,
    args: dict[str, Any],
    cache: Any,
    tasks: Any,
    *,
    allow_foreign_index: bool = False,
) -> str:
    if name == "repo_build_status" or name not in TOOL_DESCRIPTIONS:
        return dispatch(None, name, args, cache=cache, tasks=tasks)
    index, pending = open_index_or_task(
        index_dir, repo, cache, tasks, allow_foreign_index=allow_foreign_index
    )
    if pending is not None:
        return pending
    text = dispatch(index, name, args, cache=cache, tasks=tasks)
    # repo_map already leads with this note. Every other tool that cites line
    # numbers or reports edges is just as wrong on a stale index -- "no callers"
    # reads as fact -- so it carries the same warning.
    if (
        index is not None
        and name in _STALENESS_NOTED_TOOLS
        and isinstance(text, str)
        and not isinstance(text, ToolError)
    ):
        note = _staleness_note(index)
        if note:
            text = note + text
    return text


_STALENESS_NOTED_TOOLS = frozenset(
    {
        "repo_search",
        "repo_neighbours",
        "repo_find_symbol",
        "repo_read",
        "repo_path_between",
        "repo_impact",
        "repo_blast_radius",
    }
)


def _audit(
    audit: Any,
    name: str,
    args: dict[str, Any],
    elapsed: Any,
    text: Any = None,
    error: Any = None,
) -> None:
    """Write one audit record, never letting the logger break the tool call."""
    if audit is None:
        return
    try:
        audit.record(
            name,
            args,
            outcome="error" if error is not None else "success",
            duration_ms=elapsed.ms,
            result_tokens=len(text) // 4 if isinstance(text, str) else 0,
            error=f"{type(error).__name__}: {error}" if error is not None else None,
        )
    except Exception:  # noqa: BLE001 - resilience boundary: audit sink failure must not abort tool execution
        from ..events import reraise_if_debug

        reraise_if_debug()
        # An unwritable sink must not turn a working tool call into a failure;
        # AuditLogger.record already sanitizes and degrades internally, so
        # reaching here means the sink itself is gone.
        pass


def serve(
    out: str | Path,
    repo: str | Path | None = None,
    cache: ResultCache | None = None,
    tasks: Any = None,
    audit: Any = None,
    *,
    allow_foreign_index: bool = False,
) -> None:
    """Run stdio MCP server over JSON-RPC.

    Args:
        out: Index directory to answer from.
        repo: Repository to auto-build from, or None to require an existing index.
        cache: Optional result cache.
        tasks: Optional background build registry.
        audit: Optional `AuditLogger`; one record per tool call when given.
        allow_foreign_index: Whether to permit opening an index built elsewhere.
    """
    mcp = _require_sdk()
    index_dir = Path(out)
    if repo is None:
        try:
            open_index(index_dir, allow_foreign_index=allow_foreign_index)
        except ValueError as exc:
            raise SystemExit(f"error: {exc}") from None

    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent

    async def _list_tools_handler(*args: Any, **kwargs: Any) -> list[Any]:
        return get_tools(mcp.types, auto_build=(repo is not None))

    async def _call_tool_handler(ctx: Any, params: Any) -> str:
        name = params.name
        arguments = params.arguments
        return run_tool(
            index_dir,
            repo,
            name,
            arguments,
            cache=cache,
            tasks=tasks,
            audit=audit,
            allow_foreign_index=allow_foreign_index,
        )

    server_cls: Any = Server

    async def _list_tools_2x(ctx: Any, params: Any) -> Any:
        return mcp.types.ListToolsResult(tools=await _list_tools_handler())

    async def _call_tool_2x(ctx: Any, params: Any) -> Any:
        text = await _call_tool_handler(ctx, params)
        return mcp.types.CallToolResult(
            content=[TextContent(type="text", text=text)],
            isError=isinstance(text, ToolError),
        )

    try:
        mcp_server = server_cls(
            server.name,
            version=server.version,
            on_list_tools=_list_tools_2x,
            on_call_tool=_call_tool_2x,
        )
    except TypeError:
        mcp_server = server_cls(server.name, version=server.version)

    async def _run() -> None:
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
        inferred = out_path.parent
        if out is None:
            # Neither `repo` nor `--out` was given, so `out_path` is a bare
            # `.r2g` and `inferred` is the process's working directory. That is
            # how `repo2graph-mcp` with no arguments came to index whatever
            # directory the MCP client happened to launch it in: Claude Desktop
            # and Cursor do not inherit a project cwd, so for a client started
            # in the user's home folder, a desktop, a mounted drive or a
            # OneDrive root, the first tool call walked that entire tree.
            #
            # An explicit `--out <repo>/.r2g` is a deliberate choice of parent
            # and is left alone; so is an explicit `repo`. Only the directory
            # nobody named gets checked.
            _assert_inferable_repo_root(inferred)
        return out_path, inferred
    return out_path, None


#: Files that mark a directory as the root of a project, as opposed to a home
#: folder or a drive that merely contains projects.
_PROJECT_MARKERS = (
    ".git",
    "pyproject.toml",
    "setup.py",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "composer.json",
    "Gemfile",
    "CMakeLists.txt",
    "mix.exs",
    ".hg",
    ".svn",
)


def _assert_inferable_repo_root(path: Path) -> None:
    """Refuse to infer a repository from a directory that is not a project root."""
    try:
        resolved = path.resolve()
    except OSError:  # pragma: no cover - unresolvable cwd cannot be served
        resolved = path

    hint = (
        "Pass the repository as an absolute path instead:\n"
        '       repo2graph-mcp /abs/path/to/repo   (or "args": ["/abs/path/to/repo"] '
        "in your MCP client config)"
    )
    if resolved.parent == resolved:
        raise SystemExit(
            f"error: refusing to index the filesystem root ({resolved}), inferred from "
            f"the working directory.\n       {hint}"
        )
    if resolved == Path.home().resolve():
        raise SystemExit(
            f"error: refusing to index your home directory ({resolved}), inferred from "
            f"the working directory.\n       {hint}"
        )
    if not any((resolved / marker).exists() for marker in _PROJECT_MARKERS):
        raise SystemExit(
            f"error: {resolved} does not look like a project root (no .git, pyproject.toml, "
            f"package.json, go.mod, Cargo.toml or similar), and was only inferred from the "
            f"working directory -- indexing it could walk an entire home folder or drive.\n"
            f"       {hint}"
        )


def main(argv: list[str] | None = None) -> int:
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
        "--allow-foreign-index",
        action="store_true",
        help="permit opening an index not built on this machine (e.g. from an artifact or PR)",
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
    p.add_argument(
        "--secret-keyword",
        action="append",
        default=[],
        dest="extra_secret_keywords",
        help="additional keyword marking a file/path as secret, as `repo2graph build` "
        "takes it; applied to auto-builds and to every pack (repeatable)",
    )
    p.add_argument(
        "--secret-dir",
        action="append",
        default=[],
        dest="extra_secret_dirs",
        help="additional directory name to treat as secret (repeatable)",
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
        # `all` records every query's text and up to 512 chars of arguments,
        # on stderr that MCP clients often keep in their own logs. Recording
        # successful calls is an opt-in for operators who want that trail.
        default="errors",
        help="which tool calls produce an audit record (default: errors)",
    )
    log.add_argument(
        "--audit-log-fsync",
        action="store_true",
        help="sync each audit record to disk before returning",
    )
    args = p.parse_args(argv)
    index_dir, repo = resolve_paths(args.repo, args.out)
    from .indexes import SECRET_RULES

    SECRET_RULES["keywords"] = list(args.extra_secret_keywords)
    SECRET_RULES["dirs"] = list(args.extra_secret_dirs)
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
        serve_fn(
            index_dir,
            build_from,
            cache=cache,
            tasks=tasks,
            audit=audit,
            allow_foreign_index=args.allow_foreign_index,
        )
    finally:
        audit.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
