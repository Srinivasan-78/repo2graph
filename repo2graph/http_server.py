# @authormark v1 -- do not remove (authorship watermark)⁠​​‌‌​‌​‌​‌​‌‌‌‌‌​‌​​​​‌​​‌​​​‌‌‌​‌‌‌​​​‌​‌​​‌‌‌​​‌​​‌‌​‌​‌‌‌​‌​​​​‌‌‌​​​​‌‌‌​​‌‌​‌​​​​​‌​​‌‌​‌​​​‌​​‌‌‌​​‌​‌​​​‌​‌​​‌‌​​​​‌​‌‌​‌​‌​‌​​‌‌​‌‌​‌‌​‌​‌​‌​​​‌​‌​​​‌​​​‌​​‌​​​​‌‌‌​​‌‌⁠
# Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
# Author: https://github.com/Srinivasan-78
# SPDX-License-Identifier: MIT
# Fingerprint: AMK1.5_BGqNMt8sA4NQL-SmQDHs
"""An HTTP transport for the MCP server, so authentication can be real.

stdio cannot carry credentials -- see `repo2graph.auth` for why -- so bearer and
OIDC auth need a transport with headers. This is that transport: JSON-RPC over
`POST /mcp`, plus the two `.well-known` documents a registry or client fetches
before it ever opens a session.

Every answer still comes from `mcp.dispatch()`, the same function stdio calls,
so the two transports cannot drift in what they return, what they clamp, or what
they refuse to disclose. This module owns transport concerns only: framing,
headers, authentication, audit records and status codes.

Defaults are chosen so that turning this on is not itself the vulnerability:

* **Binds to 127.0.0.1.** A code index is the whole repository in searchable
  form, and `0.0.0.0` on a developer laptop means it is on the coffee-shop wifi.
  Exposing it beyond the loopback is an explicit `--http-host`.
* **Refuses to serve unauthenticated on a non-loopback bind.** Binding publicly
  with no credential configured is refused at startup rather than served, since
  that combination has no correct use.
* **Bounded request bodies.** A JSON-RPC frame is small; an unbounded read is a
  memory exhaustion primitive.
* **`.well-known` documents are public, tool calls are not.** Discovery that
  requires the credential it describes how to obtain is useless, so those two
  paths skip auth -- and therefore disclose nothing but the server's shape.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Literal

from . import __version__
from .audit import AuditLogger, timer
from .auth import AuthConfig, Authenticator, AuthError, client_metadata_document
from .cache import cache_metadata
from .events import emit

# The largest JSON-RPC frame this server will read. A tool call is a few hundred
# bytes; a megabyte is already absurd and anything unbounded is a DoS primitive.
MAX_BODY_BYTES = 1 << 20

# Loopback addresses, where serving without a credential is defensible.
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})

WELL_KNOWN_METADATA = "/.well-known/mcp-server-metadata"
WELL_KNOWN_CLIENT = "/.well-known/oauth-client-metadata"

# JSON-RPC 2.0 error codes, plus the HTTP status each maps to.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603
# Not a JSON-RPC code: the MCP auth spec carries HTTP semantics, and 401 is what
# a client acts on. Kept numerically distinct from the reserved range.
UNAUTHORIZED = 401


def server_metadata(repo: Any, index_present: bool, auth_modes: Any,
                    index_built_at: str | None = None,
                    tools: Any = None) -> dict[str, Any]:
    """The `.well-known/mcp-server-metadata` document.

    Lets a registry or client learn what this server does without opening a
    session. Deliberately says nothing about repository *content* -- only that
    an index exists and when it was built -- because this endpoint is
    unauthenticated by necessity.

    Args:
        repo: Repository path the server was pointed at, or None.
        index_present: Whether a readable index exists right now.
        auth_modes: The modes from `AuthConfig.modes`.
        index_built_at: ISO-8601 build time, or None when there is no index.
        tools: Tool descriptors; defaults to this server's three-plus-one.

    Returns:
        The metadata document.
    """
    from .mcp import TOOL_DESCRIPTIONS, TOOL_SCHEMAS
    if tools is None:
        tools = [{"name": name, "description": description,
                  "inputSchema": TOOL_SCHEMAS[name]}
                 for name, description in TOOL_DESCRIPTIONS.items()]
    return {
        "name": "repo2graph",
        "version": __version__,
        "description": ("A tree-sitter code graph over a repository, answering "
                        "questions from it with BM25 plus graph expansion."),
        "tools": tools,
        "auth_modes": list(auth_modes),
        "repo": str(repo) if repo else None,
        "index_present": bool(index_present),
        "index_built_at": index_built_at,
    }


def index_built_at(out: Any) -> str | None:
    """When the index at `out` was last written, as ISO-8601, or None.

    Args:
        out: The index directory.

    Returns:
        The manifest's mtime in ISO-8601 UTC, or None when absent or unreadable.
    """
    from datetime import datetime, timezone
    from .export import path as artifact_path
    try:
        stat = artifact_path(out, "manifest.json").stat()
    except (OSError, ValueError):
        return None
    return datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()


class MCPRequestHandler(BaseHTTPRequestHandler):
    """One HTTP request. Configuration arrives via class attributes.

    `BaseHTTPRequestHandler` instantiates the class per request, so there is no
    constructor to pass configuration through; `make_handler` builds a subclass
    carrying it instead.
    """

    server_version = f"repo2graph/{__version__}"
    # Set by make_handler.
    open_index_fn: Any = None
    dispatch_fn: Any = None
    authenticator: Any = None
    audit: Any = None
    cache: Any = None
    tasks: Any = None
    repo: Any = None
    index_dir: Any = None
    base_url = "http://127.0.0.1:8719"
    publish_cimd = False

    # ---------------------------------------------------------- plumbing --

    def log_message(self, fmt: str, *args: Any) -> None:
        """Silence the default stderr access log.

        BaseHTTPRequestHandler writes an apache-style line per request. The
        audit log is the structured record of the same events, and two
        overlapping logs on one stream is worse than either alone.
        """
        return

    def _send_json(self, status: int, payload: dict[str, Any],
                   extra_headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # This server answers tools, not browsers: no page should be able to
        # frame it, sniff it, or reach it cross-origin by default.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _read_body(self) -> bytes:
        """Read the request body, refusing anything implausibly large."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise AuthError("invalid Content-Length", status=400) from None
        if length < 0 or length > MAX_BODY_BYTES:
            raise AuthError(
                f"request body must be at most {MAX_BODY_BYTES} bytes", status=413)
        return self.rfile.read(length) if length else b""

    # ------------------------------------------------------------- routes --

    def do_GET(self) -> None:
        """Serve the unauthenticated discovery documents, and nothing else."""
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == WELL_KNOWN_METADATA.rstrip("/"):
            present = bool(self.open_index_fn and self._index_present())
            self._send_json(200, server_metadata(
                self.repo, present, self.authenticator.config.modes,
                index_built_at(self.index_dir)))
            return
        if path == WELL_KNOWN_CLIENT.rstrip("/"):
            if not self.publish_cimd:
                self._send_json(404, {"error": "client metadata is not published; "
                                               "start the server with --auth-cimd"})
                return
            self._send_json(200, client_metadata_document(self.base_url))
            return
        if path == "/healthz":
            self._send_json(200, {"status": "ok", "version": __version__})
            return
        self._send_json(404, {"error": f"no such path: {path}"})

    def _index_present(self) -> bool:
        from .mcp import _has_index
        from pathlib import Path
        try:
            return _has_index(Path(str(self.index_dir)))
        except (OSError, TypeError, ValueError):
            return False

    def do_POST(self) -> None:
        """Handle one JSON-RPC request on /mcp."""
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path not in ("/mcp", "/"):
            self._send_json(404, {"error": f"no such path: {path}"})
            return
        try:
            raw = self._read_body()
        except AuthError as exc:
            self._send_json(exc.status, _rpc_error(None, INVALID_REQUEST, exc.message))
            return

        try:
            request = json.loads(raw.decode("utf8", "replace")) if raw else None
        except ValueError:
            self._send_json(400, _rpc_error(None, PARSE_ERROR, "invalid JSON"))
            return
        if not isinstance(request, dict):
            self._send_json(400, _rpc_error(None, INVALID_REQUEST,
                                            "expected a JSON-RPC object"))
            return

        rpc_id = request.get("id")
        method = str(request.get("method") or "")
        params = request.get("params") or {}

        # Authentication happens before the method is dispatched and before any
        # index is opened, so a rejected call costs nothing and touches nothing.
        try:
            identity = self.authenticator.authenticate(
                self.headers.get("Authorization"))
        except AuthError as exc:
            self._reject(rpc_id, method, params, exc)
            return

        try:
            self._dispatch(rpc_id, method, params, identity)
        except Exception as exc:                      # pragma: no cover - guard
            self.audit.record(tool=method, params=params,
                              identity=identity.subject, outcome="error",
                              error=str(exc))
            self._send_json(500, _rpc_error(rpc_id, INTERNAL_ERROR, str(exc)))

    def _reject(self, rpc_id: Any, method: str, params: Any,
                exc: AuthError) -> None:
        """Refuse a call, record it, and execute nothing."""
        tool = str((params or {}).get("name") or method)
        self.audit.record(tool=tool, params=(params or {}).get("arguments") or {},
                          identity="anonymous", outcome="auth_rejected",
                          error=exc.message)
        emit("auth_rejected", level="warning", tool=tool,
             remote=self.client_address[0] if self.client_address else None,
             reason=exc.message)
        self._send_json(exc.status,
                        _rpc_error(rpc_id, UNAUTHORIZED, "Unauthorized"),
                        {"WWW-Authenticate": self._challenge()})

    def _challenge(self) -> str:
        config = self.authenticator.config
        if config.oidc_issuer:
            return (f'Bearer realm="repo2graph", '
                    f'authorization_uri="{config.oidc_issuer}"')
        return 'Bearer realm="repo2graph"'

    def _dispatch(self, rpc_id: Any, method: str, params: Any,
                  identity: Any) -> None:
        """Route one authenticated JSON-RPC method."""
        if method == "initialize":
            self._send_json(200, _rpc_result(rpc_id, {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "repo2graph", "version": __version__},
            }))
            return
        if method in ("notifications/initialized", "ping"):
            self._send_json(200, _rpc_result(rpc_id, {}))
            return
        if method == "tools/list":
            from .mcp import TOOL_ANNOTATIONS, TOOL_DESCRIPTIONS, TOOL_SCHEMAS, TOOL_TITLES
            result: dict[str, Any] = {
                "tools": [{"name": name, "description": description,
                           "inputSchema": TOOL_SCHEMAS[name],
                           "annotations": {**TOOL_ANNOTATIONS,
                                           **({"title": TOOL_TITLES[name]}
                                              if name in TOOL_TITLES else {})}}
                          for name, description in TOOL_DESCRIPTIONS.items()]}
            # ttlMs/cacheScope ride in _meta, which is where the MCP spec puts
            # response metadata and where a client that does not know the fields
            # will harmlessly ignore them.
            meta = cache_metadata("tools/list")
            if meta:
                result["_meta"] = meta
            self._send_json(200, _rpc_result(rpc_id, result))
            return
        if method == "tools/call":
            self._call_tool(rpc_id, params, identity)
            return
        self._send_json(404, _rpc_error(rpc_id, METHOD_NOT_FOUND,
                                        f"unknown method: {method!r}"))

    def _call_tool(self, rpc_id: Any, params: Any, identity: Any) -> None:
        """Run one tool, timing it and recording the outcome."""
        from .query import count_tokens
        name = str((params or {}).get("name") or "")
        arguments = (params or {}).get("arguments") or {}
        with timer() as elapsed:
            try:
                if name == "repo_build_status":
                    # The one tool answerable without an index: asking for build
                    # progress must not itself wait on the build.
                    text = self.dispatch_fn(None, name, arguments,
                                            cache=self.cache, tasks=self.tasks)
                    index = None
                else:
                    index, pending = self.open_index_fn(
                        self.index_dir, self.repo, self.cache, self.tasks)
                    text = pending if pending is not None else self.dispatch_fn(
                        index, name, arguments, cache=self.cache,
                        tasks=self.tasks)
            except SystemExit as exc:
                # open_index exits rather than raises when there is no index and
                # nothing safe to build from. Over HTTP that is a 503, not a
                # dead process: the server stays up and says what is wrong.
                self.audit.record(tool=name, params=arguments,
                                  identity=identity.subject, outcome="error",
                                  duration_ms=elapsed.ms, error=str(exc))
                self._send_json(503, _rpc_error(rpc_id, INTERNAL_ERROR, str(exc)))
                return
            except Exception as exc:
                self.audit.record(tool=name, params=arguments,
                                  identity=identity.subject, outcome="error",
                                  duration_ms=elapsed.ms, error=str(exc))
                self._send_json(500, _rpc_error(rpc_id, INTERNAL_ERROR, str(exc)))
                return
        self.audit.record(tool=name, params=arguments, identity=identity.subject,
                          outcome="success", duration_ms=elapsed.ms,
                          result_tokens=count_tokens(text))
        self._send_json(200, _rpc_result(rpc_id, {
            "content": [{"type": "text", "text": text}],
            "isError": False,
        }))


def _rpc_result(rpc_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _rpc_error(rpc_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id,
            "error": {"code": code, "message": message}}


def make_handler(index_dir: Any, repo: Any = None,
                 auth_config: AuthConfig | None = None,
                 audit: AuditLogger | None = None, cache: Any = None,
                 base_url: str = "http://127.0.0.1:8719",
                 publish_cimd: bool = False,
                 opener: Callable[[str], Any] | None = None,
                 tasks: Any = None) -> type[MCPRequestHandler]:
    """Build a request-handler class bound to this server's configuration.

    Args:
        index_dir: Index directory every tool answers from.
        repo: Repository to auto-build from, or None.
        auth_config: An `AuthConfig`; defaults to no authentication.
        audit: An `AuditLogger`; defaults to a fresh one at level "all".
        cache: A `ResultCache`, or None.
        base_url: Externally reachable base URL, used by the CIMD document.
        publish_cimd: Whether `/.well-known/oauth-client-metadata` is served.
        opener: JSON fetcher for OIDC discovery, injected by tests.
        tasks: A `TaskManager` when builds run in the background, else None.

    Returns:
        A `MCPRequestHandler` subclass ready to hand to `ThreadingHTTPServer`.
    """
    from .mcp import dispatch, open_index_or_task

    class _Handler(MCPRequestHandler):
        pass

    _Handler.index_dir = index_dir
    _Handler.repo = repo
    _Handler.open_index_fn = staticmethod(open_index_or_task)
    _Handler.dispatch_fn = staticmethod(dispatch)
    _Handler.authenticator = Authenticator(auth_config or AuthConfig(), opener)
    _Handler.audit = audit or AuditLogger()
    _Handler.cache = cache
    _Handler.tasks = tasks
    _Handler.base_url = base_url
    _Handler.publish_cimd = publish_cimd
    return _Handler


class HTTPTransport:
    """The HTTP server, on a daemon thread so it never blocks stdio.

    Args:
        index_dir: Index directory to serve.
        repo: Repository to auto-build from, or None.
        host: Bind address; loopback unless deliberately widened.
        port: Bind port. 0 asks the OS for a free one, which tests use.
        auth_config: An `AuthConfig`.
        audit: An `AuditLogger`.
        cache: A `ResultCache`, or None.
        publish_cimd: Whether to serve the client metadata document.
        opener: JSON fetcher for OIDC discovery, injected by tests.

    Raises:
        ValueError: If asked to bind beyond loopback with no credential set,
            which would publish the whole indexed repository to the network.
    """

    def __init__(self, index_dir: Any, repo: Any = None,
                 host: str = "127.0.0.1", port: int = 8719,
                 auth_config: AuthConfig | None = None,
                 audit: AuditLogger | None = None, cache: Any = None,
                 publish_cimd: bool = False,
                 opener: Callable[[str], Any] | None = None,
                 tasks: Any = None) -> None:
        auth_config = auth_config or AuthConfig()
        if host not in LOOPBACK and not auth_config.enabled:
            raise ValueError(
                f"refusing to bind {host}:{port} with no authentication: a "
                f"repo2graph index is the whole repository in searchable form. "
                f"Pass --auth-token or --auth-oidc-issuer, or bind 127.0.0.1.")
        self.host, self.port = host, port
        self.auth_config = auth_config
        self._handler = make_handler(
            index_dir, repo, auth_config, audit, cache,
            base_url=f"http://{host}:{port}", publish_cimd=publish_cimd,
            opener=opener, tasks=tasks)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        """Start serving on a daemon thread.

        Returns:
            The port actually bound, which differs from the requested one when
            port 0 asked the OS to choose.
        """
        self._httpd = ThreadingHTTPServer((self.host, self.port), self._handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._handler.base_url = f"http://{self.host}:{self.port}"
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="repo2graph-http", daemon=True)
        self._thread.start()
        emit("http_transport_started", level="info", host=self.host,
             port=self.port, auth_modes=self.auth_config.modes)
        return self.port

    def stop(self) -> None:
        """Stop serving and release the port."""
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "HTTPTransport":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> Literal[False]:
        self.stop()
        return False
