"""Stdio MCP server lifecycle, command line interface, and protocol adapters."""

from __future__ import annotations

import argparse
import asyncio
import hmac
import ipaddress
import os
import re
import sys
from pathlib import Path
from typing import Any, Protocol, cast

from .. import __version__
from ..audit import timer as audit_timer
from ..cache import DEFAULT_MAX_SIZE, DEFAULT_TTL, ResultCache
from ..events import diagnostic
from .guardrails import INDEX_DIRNAME
from .pagination import PagedText
from .tools import (
    SERVER_INSTRUCTIONS,
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


class _ASGIResponse(Protocol):
    """Structural stand-in for `starlette.responses.Response`, typing-only.

    starlette is loaded with `importlib` in `create_sse_app` below, never
    imported at module scope: `tests/test_http_surface_removed.py` asserts
    repo2graph has no static dependency on an HTTP framework, including under
    `TYPE_CHECKING`.
    """


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
            # `note + text` is a plain str; keep a page's cursor for _meta.nextCursor.
            cursor = getattr(text, "next_cursor", None)
            text = note + text
            if cursor:
                text = PagedText(text)
                text.next_cursor = cursor
    return text


_STALENESS_NOTED_TOOLS = frozenset(
    {
        "repo_search",
        "repo_neighbours",
        "repo_find_symbol",
        "repo_read",
        "repo_path_between",
        "repo_blast_radius",
    }
)


def run_resource_read(
    index_dir: str | Path,
    uri: str,
    audit: Any = None,
    *,
    allow_foreign_index: bool = False,
) -> tuple[str, str]:
    """Read one MCP resource from an existing index, audited like a tool call (#388).

    Raises:
        ValueError: An unknown URI, or no index to read it from yet.
    """
    from .resources import read_resource

    args = {"uri": uri}
    with audit_timer() as elapsed:
        try:
            try:
                index = open_index(index_dir, allow_foreign_index=allow_foreign_index)
            except SystemExit as exc:
                raise ValueError(
                    f"no index to read yet ({exc}); a tool call builds one when the server "
                    "was given a repository"
                ) from None
            text, mime = read_resource(index, uri)
        except ValueError as exc:
            _audit(audit, "resources/read", args, elapsed, error=exc)
            raise
    _audit(audit, "resources/read", args, elapsed, text=text)
    return text, mime


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


def build_mcp_server(
    out: str | Path,
    repo: str | Path | None = None,
    cache: ResultCache | None = None,
    tasks: Any = None,
    audit: Any = None,
    *,
    allow_foreign_index: bool = False,
) -> Any:
    """Build and configure the MCP Server instance."""
    mcp = _require_sdk()
    index_dir = Path(out)
    if repo is None:
        try:
            open_index(index_dir, allow_foreign_index=allow_foreign_index)
        except ValueError as exc:
            raise SystemExit(f"error: {exc}") from None

    from mcp.server import Server
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
        content = [TextContent(type="text", text=text)]
        is_error = isinstance(text, ToolError)
        next_cursor = getattr(text, "next_cursor", None)
        if next_cursor:
            # The cursor is also the text's last line, so a client that drops
            # `_meta` loses nothing; an SDK without `_meta` just goes without it.
            try:
                return mcp.types.CallToolResult(
                    content=content, isError=is_error, _meta={"nextCursor": next_cursor}
                )
            except TypeError:
                pass
        return mcp.types.CallToolResult(content=content, isError=is_error)

    from .resources import get_prompt, list_prompts, list_resources

    async def _list_resources(ctx: Any, params: Any) -> Any:
        return mcp.types.ListResourcesResult(
            resources=[mcp.types.Resource(**r) for r in list_resources(index_dir)]
        )

    async def _read_resource(ctx: Any, params: Any) -> Any:
        uri = str(params.uri)
        text, mime = run_resource_read(
            index_dir, uri, audit=audit, allow_foreign_index=allow_foreign_index
        )
        return mcp.types.ReadResourceResult(
            contents=[mcp.types.TextResourceContents(uri=uri, mime_type=mime, text=text)]
        )

    async def _list_prompts(ctx: Any, params: Any) -> Any:
        return mcp.types.ListPromptsResult(
            prompts=[
                mcp.types.Prompt(
                    name=p["name"],
                    description=p["description"],
                    arguments=[mcp.types.PromptArgument(**a) for a in p["arguments"]],
                )
                for p in list_prompts()
            ]
        )

    async def _get_prompt(ctx: Any, params: Any) -> Any:
        description, message = get_prompt(params.name, params.arguments)
        return mcp.types.GetPromptResult(
            description=description,
            messages=[
                mcp.types.PromptMessage(role="user", content=TextContent(type="text", text=message))
            ],
        )

    tool_handlers = {"on_list_tools": _list_tools_2x, "on_call_tool": _call_tool_2x}
    extra_handlers = {
        "on_list_resources": _list_resources,
        "on_read_resource": _read_resource,
        "on_list_prompts": _list_prompts,
        "on_get_prompt": _get_prompt,
    }
    # Newest registration first. An SDK without the resource and prompt hooks
    # still gets the tools; only one with no keyword handlers at all falls back
    # to a bare server.
    # `instructions` is tried before the same handler set without it: it is the
    # one place a server-wide output convention can be stated once instead of
    # once per tool. Every JSON reply can be led by `_note: ..._` paragraphs,
    # and TOOL_DESCRIPTIONS cannot absorb that sentence four times -- they are
    # capped at 800 chars each because they ship on every session.
    mcp_server: Any = None
    attempts: list[dict[str, Any]] = []
    for handlers in ({**tool_handlers, **extra_handlers}, tool_handlers):
        attempts.append({**handlers, "instructions": SERVER_INSTRUCTIONS})
        attempts.append(dict(handlers))
    for kwargs in attempts:
        try:
            mcp_server = server_cls(server.name, version=server.version, **kwargs)
            break
        except TypeError:
            continue
    else:
        mcp_server = server_cls(server.name, version=server.version)
    return mcp_server


def _binds_beyond_loopback(host: str) -> bool:
    """Whether `--host` exposes the index past this machine.

    An empty host and `*` are uvicorn's spellings of "every interface". A name
    rather than a literal cannot be classified without resolving it, and this
    runs before the socket is opened, so it is treated as exposed -- the safe
    direction for a check whose only effect is to demand a token.
    """
    if host in ("", "*"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host.lower() != "localhost"
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return not ip.is_loopback


def require_sse_auth(host: str, auth_token: str | None) -> None:
    """Refuse to serve the index to a network without a bearer token.

    `--transport sse` turns a local, single-client stdio tool into a server,
    and `/sse` and `/messages` hand out the same cited file content the stdio
    transport does. Binding `0.0.0.0` without `--auth-token` therefore serves
    the whole index to anything that can route to the port -- and because
    `create_sse_app` installs its middleware only `if auth_token`, the
    unauthenticated case was silent rather than refused.

    Loopback stays open with no token: that is the documented container-local
    and port-forward case, where the socket is already as private as stdin.
    """
    if auth_token or not _binds_beyond_loopback(host):
        return
    raise SystemExit(
        f"error: --transport sse with --host {host or '(all interfaces)'} would serve the "
        "index, including cited file content, to anyone who can reach the port, with no "
        "authentication.\n"
        "Pass --auth-token (or set REPO2GRAPH_MCP_AUTH_TOKEN), or bind --host 127.0.0.1 "
        "and reach it through a port-forward."
    )


def create_sse_app(
    mcp_server: Any, auth_token: str | None = None, bind_host: str | None = None
) -> Any:
    """Create a Starlette ASGI application serving MCP over Server-Sent Events (SSE).

    `bind_host` is only used to decide whether an absent `auth_token` deserves a
    warning. This function does not open the socket, so it cannot otherwise know
    whether the app is about to be exposed: pass the host you are going to bind,
    and loopback stays silent because loopback with no token is the documented
    container-local and port-forward case. Leave it unset and an absent token
    warns, since an unknown bind has to be assumed to be a public one.
    """
    import importlib

    sse_mod = importlib.import_module("mcp.server.sse")
    starlette_apps = importlib.import_module("starlette.applications")
    starlette_resp = importlib.import_module("starlette.responses")
    starlette_routing = importlib.import_module("starlette.routing")

    SseServerTransport = sse_mod.SseServerTransport
    Starlette = starlette_apps.Starlette
    PlainTextResponse = starlette_resp.PlainTextResponse
    Response = starlette_resp.Response
    Mount = starlette_routing.Mount
    Route = starlette_routing.Route

    sse = SseServerTransport("/messages")

    async def handle_sse(request: Any) -> _ASGIResponse:
        async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
            await mcp_server.run(streams[0], streams[1], mcp_server.create_initialization_options())
        return cast(_ASGIResponse, Response())

    routes = [
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
        Mount("/messages", app=sse.handle_post_message),
        Route("/healthz", endpoint=lambda r: PlainTextResponse("OK"), methods=["GET"]),
        Route("/readyz", endpoint=lambda r: PlainTextResponse("OK"), methods=["GET"]),
    ]
    app = Starlette(routes=routes)
    if not auth_token and (bind_host is None or _binds_beyond_loopback(bind_host)):
        # `create_sse_app` is exported from `repo2graph.mcp`, so an embedder can
        # reach it without going through `serve()` and therefore without
        # `require_sse_auth`'s refusal. It must not hand back an
        # unauthenticated index in silence -- but it must also stay quiet for
        # the documented loopback default, or every ordinary local user is
        # warned for doing the sanctioned thing.
        diagnostic(
            "warning: the SSE app was built with no auth token; every caller that can reach "
            "its port may read the index, including cited file content. Bind it to loopback "
            "only, or pass auth_token."
        )
    if auth_token:

        class AuthMiddleware:
            def __init__(self, inner: Any, token: str) -> None:
                self.inner = inner
                self.token = token

            async def __call__(self, scope: Any, receive: Any, send: Any) -> Any:
                if scope["type"] != "http":
                    return await self.inner(scope, receive, send)
                path = scope.get("path", "")
                if path in ("/healthz", "/readyz"):
                    return await self.inner(scope, receive, send)
                headers = dict(scope.get("headers", []))
                raw = headers.get(b"authorization", b"")
                # Compare *bytes*, never str. `compare_digest` raises TypeError
                # on a str holding any non-ASCII character, and `latin-1`
                # decodes every byte 0x80-0xFF into one -- so a single garbage
                # byte in the header crashed the middleware instead of being
                # rejected. This wrapper sits outside Starlette's
                # ServerErrorMiddleware, so nothing turned that into a clean
                # 500 either: it surfaced as an unhandled ASGI exception, which
                # an unauthenticated caller could trigger on every request.
                #
                # `compare_digest`, not `==`: this is the only credential
                # gating the SSE surface, and `==` returns as soon as two bytes
                # differ, leaking the token's matching prefix length to anyone
                # who can time the 401s.
                presented = raw[7:].strip() if raw.startswith(b"Bearer ") else b""
                if not hmac.compare_digest(presented, self.token.encode("utf-8")):
                    resp = Response(
                        "Unauthorized",
                        status_code=401,
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                    return await resp(scope, receive, send)
                return await self.inner(scope, receive, send)

        return AuthMiddleware(app, token=auth_token)
    return app


def serve(
    out: str | Path,
    repo: str | Path | None = None,
    cache: ResultCache | None = None,
    tasks: Any = None,
    audit: Any = None,
    *,
    allow_foreign_index: bool = False,
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8080,
    auth_token: str | None = None,
) -> None:
    """Run MCP server over stdio or SSE.

    Args:
        out: Index directory to answer from.
        repo: Repository to auto-build from, or None to require an existing index.
        cache: Optional result cache.
        tasks: Optional background build registry.
        audit: Optional `AuditLogger`; one record per tool call when given.
        allow_foreign_index: Whether to permit opening an index built elsewhere.
        transport: Transport protocol ('stdio' or 'sse').
        host: Host to bind to when transport='sse'.
        port: Port to bind to when transport='sse'.
        auth_token: Optional bearer token for SSE endpoints.
    """
    mcp_server = build_mcp_server(
        out,
        repo,
        cache=cache,
        tasks=tasks,
        audit=audit,
        allow_foreign_index=allow_foreign_index,
    )

    if transport == "stdio":
        from mcp.server.stdio import stdio_server

        async def _run() -> None:
            async with stdio_server() as (read_stream, write_stream):
                await mcp_server.run(
                    read_stream, write_stream, mcp_server.create_initialization_options()
                )

        asyncio.run(_run())
    elif transport == "sse":
        import importlib

        require_sse_auth(host, auth_token)
        uvicorn = importlib.import_module("uvicorn")

        app = create_sse_app(mcp_server, auth_token=auth_token, bind_host=host)
        uvicorn.run(app, host=host, port=port)
    else:
        raise ValueError(f"unsupported transport: {transport!r}")


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


#: Longest `--staleness-ttl` accepted. A stale-index warning held for longer
#: than this stops being a warning about the tree as it is now.
MAX_STALENESS_TTL = 300.0


def _staleness_ttl(text: str) -> float:
    """argparse type for `--staleness-ttl`: finite seconds in [0, MAX_STALENESS_TTL]."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number of seconds: {text!r}") from None
    if not (0.0 <= value <= MAX_STALENESS_TTL):
        raise argparse.ArgumentTypeError(
            f"must be between 0 and {MAX_STALENESS_TTL:g} seconds, got {text!r}"
        )
    return value


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
        "--staleness-ttl",
        type=_staleness_ttl,
        default=0.0,
        metavar="SECONDS",
        help="reuse the working-tree staleness check behind each result's "
        "stale-index note for this long, instead of re-scanning the tree on "
        "every tool call; for clients such as editors that call on every "
        f"cursor move (default: 0, check every call; max {MAX_STALENESS_TTL:g})",
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
    net = p.add_argument_group("transport & network")
    net.add_argument(
        "--transport",
        choices=("stdio", "sse"),
        default="stdio",
        help="transport protocol: stdio (default) or sse (HTTP Server-Sent Events)",
    )
    net.add_argument(
        "--host",
        default="127.0.0.1",
        help="host to bind the SSE HTTP server to (default: 127.0.0.1)",
    )
    net.add_argument(
        "--port",
        type=int,
        default=8080,
        help="port to bind the SSE HTTP server to (default: 8080)",
    )
    net.add_argument(
        "--auth-token",
        default=None,
        help="bearer token required to access SSE endpoints (or set REPO2GRAPH_MCP_AUTH_TOKEN)",
    )
    args = p.parse_args(argv)
    index_dir, repo = resolve_paths(args.repo, args.out)
    from .indexes import SECRET_RULES
    from .nodes import STALENESS_TTL

    SECRET_RULES["keywords"] = list(args.extra_secret_keywords)
    SECRET_RULES["dirs"] = list(args.extra_secret_dirs)
    STALENESS_TTL["seconds"] = args.staleness_ttl
    cache = ResultCache(max_size=args.cache_size, ttl=args.cache_ttl)
    build_from = None if args.no_auto_build else repo
    auth_token = args.auth_token or os.environ.get("REPO2GRAPH_MCP_AUTH_TOKEN")

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
            transport=args.transport,
            host=args.host,
            port=args.port,
            auth_token=auth_token,
        )
    finally:
        audit.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
