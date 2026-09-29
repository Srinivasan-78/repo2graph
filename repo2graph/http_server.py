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
* **Bounded request bodies, in bytes *and* in seconds.** A JSON-RPC frame is
  small; an unbounded read is a memory exhaustion primitive, and an unbounded
  *wait* is the cheaper one -- it costs a client nothing to announce a
  `Content-Length` and then hold the socket open. `MAX_BODY_BYTES` caps the
  first, `MCPRequestHandler.timeout` the second.
* **`.well-known` documents are public, tool calls are not.** Discovery that
  requires the credential it describes how to obtain is useless, so those two
  paths skip auth -- and therefore disclose nothing but the server's shape.
* **`Host`/`Origin` are checked on every request.** Loopback-with-no-auth is
  exactly the configuration a page open in a browser on the same machine can
  reach via `fetch()`/XHR -- including via DNS rebinding, where a public
  hostname resolves to 127.0.0.1. Only loopback Host values (plus the bind
  host and anything in `--http-allow-hosts`) and same-set Origin values are
  accepted; everything else is refused with 403 before the body is read.
"""

import ipaddress
import json
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Literal
from urllib.parse import urlsplit

from . import __version__
from .audit import AuditLogger, timer
from .auth import AuthConfig, Authenticator, AuthError, client_metadata_document
from .cache import cache_metadata
from .events import emit

# The largest JSON-RPC frame this server will read. A tool call is a few hundred
# bytes; a megabyte is already absurd and anything unbounded is a DoS primitive.
MAX_BODY_BYTES = 1 << 20

# How long one connection may take to deliver its request, in seconds.
# `socketserver.StreamRequestHandler.setup()` calls `settimeout()` only when the
# handler's `timeout` is not None, and the base class leaves it None -- so
# without this a client that announces a Content-Length and then sends nothing
# blocks its handler thread forever, and `ThreadingHTTPServer` caps neither
# threads nor connections. Thirty seconds is orders of magnitude longer than a
# JSON-RPC frame needs on loopback, and finite.
REQUEST_TIMEOUT_SECONDS = 30.0

# Loopback addresses, where serving without a credential is defensible.
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})

# Bare hostnames (no port, no brackets) accepted in the Host/Origin headers by
# default. A browser page -- including one that DNS-rebinds a public hostname
# to 127.0.0.1 -- can only reach this server if the Host/Origin it sends matches
# something here or in the deployment's explicit --http-allow-hosts allowlist;
# anything else is refused before the body is even read.
DEFAULT_ALLOWED_HOSTNAMES = frozenset({"127.0.0.1", "::1", "localhost"})

# ------------- header value sanitisation (ISS-84 / CodeQL alerts 8,9) --------
# User-controlled values reflected into HTTP response headers must never contain
# CR (\r), LF (\n) or NUL (\0) — otherwise an attacker can inject arbitrary
# headers or body content ("HTTP Response Splitting").  The helper below is
# applied to the values do_OPTIONS reflects, and -- structurally, so no future
# call site has to remember -- to every extra header `_send_json` is handed.
_HEADER_BAD_CHARS = frozenset("\r\n\0")


def _sanitize_header_value(value: str) -> str:
    """Strip CR/LF/NUL from a value about to be placed in a response header."""
    # Note: explicit .replace("\r", "").replace("\n", "") satisfies CodeQL's
    # ReplaceLineBreaksSanitizer barrier for py/http-response-splitting.
    return str(value).replace("\0", "").replace("\r", "").replace("\n", "")


def _hostname_from_host_header(value: str | None) -> str | None:
    """The bare hostname of a `Host` header, with `:port` and `[...]` stripped.

    Returns None for anything empty or unparseable -- fail closed rather than
    guess.
    """
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    if value.startswith("["):
        end = value.find("]")
        if end == -1:
            return None
        return value[1:end].lower() or None
    head, sep, tail = value.rpartition(":")
    if sep and tail.isdigit():
        return head.lower() or None
    return value.lower()


def _hostname_from_origin(value: str | None) -> str | None:
    """The hostname of an `Origin` header, or None if absent/unparseable.

    `null` (sandboxed iframes, `file://` pages) is treated as unparseable: it
    never matches an allowlist of real hostnames, so it is rejected.
    """
    if not value:
        return None
    value = value.strip()
    if not value or value.lower() == "null":
        return None
    try:
        hostname = urlsplit(value).hostname
    except ValueError:
        return None
    return hostname.lower() if hostname else None


def _host_header_allowed(value: str | None, allowed_hostnames: frozenset[str]) -> bool:
    """Whether a `Host` header names one of the accepted hostnames.

    Fails closed: a missing or unparseable header is not allowed.
    """
    hostname = _hostname_from_host_header(value)
    return hostname is not None and hostname in allowed_hostnames


def _origin_header_allowed(value: str | None, allowed_hostnames: frozenset[str]) -> bool:
    """Whether an `Origin` header, if present, names an accepted hostname.

    No `Origin` header at all is allowed -- that is the common case for
    non-browser clients (curl, an MCP client library) and carries none of the
    cross-origin-browser risk this check exists for. A present-but-unparseable
    or cross-origin value is refused.
    """
    if value is None:
        return True
    hostname = _hostname_from_origin(value)
    return hostname is not None and hostname in allowed_hostnames


def _content_type_ok(value: str | None) -> bool:
    """Whether a `Content-Type` header names JSON-RPC's media type (#293).

    Accepts `application/json` case-insensitively, with or without
    parameters (`; charset=utf-8` is the common one a client adds) -- only
    the media type before the first `;` is compared, per RFC 9110 §8.3.

    A **missing** header is refused, not accepted. `urllib.request.Request(
    data=<bytes>)` silently defaults to `application/x-www-form-urlencoded`
    when the caller sets no header at all, which is exactly the shape a
    misconfigured client produces; treating "no header" as "assume JSON"
    would accept that misconfiguration instead of catching it. This mirrors
    the fail-closed posture `_origin_header_allowed`'s sibling `Host` check
    already takes for a missing/unparseable value, and matches the MCP
    Streamable HTTP transport spec, which requires `Content-Type:
    application/json` on every POST.
    """
    if not value:
        return False
    media_type = value.split(";", 1)[0].strip().lower()
    return media_type == "application/json"


def _json_nesting_exceeds(text: str, limit: int) -> bool:
    """Whether `text` contains `{`/`[` nested deeper than `limit`, string-aware.

    A single linear pass, bracket characters inside JSON string literals are
    skipped (tracking `\\"` escapes) so a message body like `{"note": "[[[["}`
    is not mistaken for nesting. Runs before `json.loads` so a hostile
    `"[" * 20000` is refused in a few hundred character reads rather than
    parsed and then measured -- and short-circuits the moment the limit is
    crossed, so the cost of the attack case is bounded by `limit`, not by the
    body length.
    """
    depth = 0
    in_string = False
    escape = False
    for ch in text:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{" or ch == "[":
            depth += 1
            if depth > limit:
                return True
        elif ch == "}" or ch == "]":
            depth -= 1
    return False


def _dedup_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """`object_pairs_hook` that refuses a JSON object with a repeated key.

    Plain `json.loads` silently keeps the last value for a duplicate key,
    which is exactly the ambiguity RFC 8259 §4 calls out and leaves to the
    implementation: two parsers disagreeing about which `"id"` or `"method"`
    won is a request-smuggling shape, not merely untidy input (#293). Raising
    here lands in the same `except (ValueError, RecursionError)` PARSE_ERROR
    path every other structurally-invalid body already takes.
    """
    seen: set[str] = set()
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate key {key!r} in JSON object")
        seen.add(key)
        out[key] = value
    return out


def _reject_json_constant(name: str) -> Any:
    """`parse_constant` hook refusing `NaN`/`Infinity`/`-Infinity` (#293).

    `json.loads` accepts these three bare tokens by default -- a CPython
    extension to the JSON grammar, not standard JSON -- and a non-finite
    JSON-RPC `id` would round-trip into this server's own `json.dumps` calls
    as the same non-standard token, which a stricter downstream parser (or a
    SIEM ingesting the audit log) is not obliged to accept.
    """
    raise ValueError(f"non-finite JSON constant {name!r} is not accepted")


def _parse_rpc_body(text: str) -> Any:
    """Parse one JSON-RPC frame with every #293 structural guard applied.

    Args:
        text: The decoded request body; empty string means no body was sent.

    Returns:
        The parsed value, or None for an empty body.

    Raises:
        ValueError: Invalid JSON, a duplicate object key, a non-finite
            numeric constant, or nesting past `MAX_JSON_DEPTH`.
        RecursionError: A vanishingly small residual risk -- the depth scan
            above already refuses anything past `MAX_JSON_DEPTH`, which is
            far below CPython's default recursion ceiling -- kept here as a
            backstop rather than trusted to have covered every path.
    """
    if not text:
        return None
    if _json_nesting_exceeds(text, MAX_JSON_DEPTH):
        raise ValueError("request exceeds the maximum JSON nesting depth")
    return json.loads(
        text, object_pairs_hook=_dedup_object_pairs, parse_constant=_reject_json_constant
    )


# Bool is an int subclass in Python -- `isinstance(True, int)` is True -- so it
# needs its own exclusion below rather than falling out of `_ID_TYPES`.
_ID_TYPES = (str, int, type(None))


def _validate_envelope(request: dict[str, Any]) -> tuple[int, int, str] | None:
    """Structural JSON-RPC 2.0 checks beyond "is this a dict" (#293).

    Runs after the body has parsed as a JSON object and before any field is
    trusted for dispatch, authentication or logging.

    Args:
        request: The parsed JSON-RPC request object.

    Returns:
        `(http_status, jsonrpc_code, message)` for the first violation found,
        or None when the envelope is well-formed. Checked in a fixed order so
        the same malformed request always reports the same reason.
    """
    if request.get("jsonrpc") != "2.0":
        return 400, INVALID_REQUEST, 'jsonrpc must be exactly "2.0"'

    if "id" in request:
        rpc_id = request["id"]
        if isinstance(rpc_id, bool) or not isinstance(rpc_id, _ID_TYPES):
            return 400, INVALID_REQUEST, "id must be a string, a number, or null"
        if isinstance(rpc_id, int) and abs(rpc_id) > MAX_RPC_ID_MAGNITUDE:
            return 400, INVALID_REQUEST, "id is outside the representable numeric range"

    method = request.get("method")
    if not isinstance(method, str) or not method or len(method) > MAX_METHOD_LENGTH:
        return 400, INVALID_REQUEST, "method must be a non-empty string"

    if "params" in request and request["params"] is not None:
        params = request["params"]
        if not isinstance(params, dict):
            return 400, INVALID_PARAMS, "params must be an object"
        if len(params) > MAX_PARAMS_FIELDS:
            return 400, INVALID_PARAMS, "params has too many top-level fields"

    return None


@dataclass
class RateLimitConfig:
    """Configurable ceilings for #264 -- per-client rate, concurrency, size.

    Attributes:
        requests_per_window: Requests one client identity may make in
            `window_seconds` before being throttled.
        window_seconds: The sliding window's width, in seconds.
        max_concurrent_requests: Server-wide in-flight `tools/call`/
            `tools/list`/`initialize` requests admitted at once.
        max_queue_size: Requests allowed to wait for a concurrency slot once
            `max_concurrent_requests` is saturated; beyond this, a request is
            refused immediately rather than queued.
        queue_wait_seconds: How long a queued request waits for a slot before
            being refused as overloaded.
        max_concurrent_builds: Server-wide concurrent `open_index_fn` calls
            (which may trigger an auto-build) admitted at once.
        max_response_bytes: A JSON-RPC success response larger than this is
            replaced with a bounded error rather than sent.
        max_tracked_clients: How many distinct client identities the sliding
            window keeps state for. This bounds the limiter's own memory
            against a flood of one-shot identities (spoofed peers, single-use
            bearer attempts). Its own knob rather than a multiple of
            `max_concurrent_requests`: how many clients a server *has* and how
            many it serves *at once* are unrelated numbers, and deriving one
            from the other silently evicts real clients -- which drops their
            history and hands them a fresh allowance -- on any server whose
            client count exceeds its concurrency.
    """

    requests_per_window: int = 300
    window_seconds: float = 60.0
    max_concurrent_requests: int = 64
    max_queue_size: int = 128
    queue_wait_seconds: float = 5.0
    max_concurrent_builds: int = 4
    max_response_bytes: int = 8 << 20
    max_tracked_clients: int = 4096


class RateLimiter:
    """Per-client rate limiting plus server-wide concurrency quotas (#264).

    One instance is shared across every request the transport serves --
    `MCPRequestHandler` is instantiated per request by `BaseHTTPRequestHandler`,
    so the limiter has to live on the class, not the instance, to see traffic
    across connections at all.

    Args:
        config: The configured ceilings.
        clock: Monotonic time source, injected so a test can move the sliding
            window without sleeping -- the same reasoning `JWKSCache` and
            `ResultCache` use their own injected clocks for.
    """

    def __init__(
        self, config: RateLimitConfig, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.config = config
        self._clock = clock
        self._lock = threading.Lock()
        self._windows: dict[str, list[float]] = {}
        self._concurrency = threading.Semaphore(max(1, config.max_concurrent_requests))
        self._queue_lock = threading.Lock()
        self._queue_waiting = 0
        self._build_gate = threading.Semaphore(max(1, config.max_concurrent_builds))

    def check_rate(self, client_id: str) -> bool:
        """Record one request from `client_id`; False if it must be throttled."""
        now = self._clock()
        cutoff = now - self.config.window_seconds
        with self._lock:
            window = self._windows.setdefault(client_id, [])
            i = 0
            for i, ts in enumerate(window):  # noqa: B007 -- index used after the loop
                if ts >= cutoff:
                    break
            else:
                i = len(window)
            del window[:i]
            if len(window) >= self.config.requests_per_window:
                return False
            window.append(now)
            # Bound the map itself: a flood of distinct client identities
            # (spoofed IPs, one-shot bearer attempts) must not grow this
            # dict without limit. Stale-only entries are dropped first;
            # if that alone does not fit under the ceiling, oldest by last
            # activity go next. Eviction is a memory guard, not a policy:
            # an evicted client's history is gone, so it gets a fresh
            # allowance -- which is why the ceiling is sized for the number
            # of clients a deployment has, not for its concurrency.
            ceiling = max(1, self.config.max_tracked_clients)
            if len(self._windows) > ceiling:
                for key in [k for k, v in self._windows.items() if not v]:
                    del self._windows[key]
                if len(self._windows) > ceiling:
                    doomed = sorted(
                        self._windows,
                        key=lambda k: self._windows[k][-1] if self._windows[k] else 0.0,
                    )[: len(self._windows) - ceiling]
                    for key in doomed:
                        del self._windows[key]
            return True

    def acquire_slot(self) -> bool:
        """Reserve a concurrency slot, honouring the queue-size cap.

        Returns:
            True if a slot was reserved -- the caller must call
            `release_slot()` exactly once when done. False if the server is
            at capacity and the queue itself is full: refused immediately,
            never silently dropped into an unbounded wait.
        """
        if self._concurrency.acquire(blocking=False):
            return True
        with self._queue_lock:
            if self._queue_waiting >= self.config.max_queue_size:
                return False
            self._queue_waiting += 1
        try:
            return self._concurrency.acquire(timeout=self.config.queue_wait_seconds)
        finally:
            with self._queue_lock:
                self._queue_waiting -= 1

    def release_slot(self) -> None:
        self._concurrency.release()

    def acquire_build_slot(self) -> bool:
        """Non-blocking: True if a build/open slot was reserved."""
        return self._build_gate.acquire(blocking=False)

    def release_build_slot(self) -> None:
        self._build_gate.release()


def _first_forwarded_ip(value: str | None) -> str | None:
    """The left-most address in an `X-Forwarded-For` header, if it parses.

    Only called when the immediate peer is in the operator's `trusted_proxies`
    allowlist (#267) -- this function itself does no trust decision, it only
    refuses to hand back a value that is not a syntactically real address, so
    a malformed or absent header degrades to "use the socket peer" rather
    than to a fabricated identity.
    """
    if not value:
        return None
    candidate = value.split(",", 1)[0].strip()
    if not candidate:
        return None
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return candidate


WELL_KNOWN_METADATA = "/.well-known/mcp-server-metadata"
WELL_KNOWN_CLIENT = "/.well-known/oauth-client-metadata"

# JSON-RPC 2.0 error codes, plus the HTTP status each maps to.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
# -32000..-32099 is the JSON-RPC-reserved range for implementation-defined
# server errors (#264): rate limiting and overload are exactly that -- neither
# is a malformed request, both are this server's own capacity policy.
RATE_LIMITED_CODE = -32000
OVERLOADED_CODE = -32001
# Not a JSON-RPC code: the MCP auth spec carries HTTP semantics, and 401 is what
# a client acts on. Kept numerically distinct from the reserved range.
UNAUTHORIZED = 401
# Also not a JSON-RPC code: a rejected Host/Origin is an HTTP-layer refusal,
# not a malformed RPC frame. 403 is what a client acts on.
FORBIDDEN = 403
# HTTP statuses with no JSON-RPC reserved code of their own, used the same way
# UNAUTHORIZED/FORBIDDEN are: the code above travels in the JSON-RPC error
# body, the HTTP status is what a generic client acts on without parsing it.
TOO_MANY_REQUESTS = 429
SERVICE_UNAVAILABLE = 503
INTERNAL_ERROR_STATUS = 500
# Also not a JSON-RPC code: a rejected Content-Type is an HTTP-layer refusal
# of the request's framing, same class as FORBIDDEN above for Host/Origin.
UNSUPPORTED_MEDIA_TYPE = 415

# #390: the protocol revision this transport actually implements. `2025-06-18`
# -- what this endpoint used to advertise -- names Streamable HTTP: a single
# endpoint serving both POST and a GET SSE stream, `Mcp-Session-Id` binding a
# session, `Last-Event-ID` for resumability. None of that exists here --
# Transfer-Encoding is refused outright, HTTP/1.0 closes every connection
# after one response, and GET serves only the two `.well-known` documents plus
# `/healthz`. `2024-11-05` is the honest floor: plain JSON-RPC request/response
# framing, no batching contract, no server-initiated stream. Advertising it is
# option 1 from #390 -- say what is served, rather than implement Streamable
# HTTP (option 2, tracked separately and gated on the connection cap in #264).
MCP_PROTOCOL_VERSION = "2024-11-05"

# #293: a JSON-RPC frame nested deeper than this is refused before it is even
# parsed. 32 is generous for this server's actual shapes (initialize/
# tools-list/tools-call params nest at most a few levels) and small enough
# that the depth scan below terminates in a handful of characters against a
# hostile `"[" * 20000`.
MAX_JSON_DEPTH = 32
# JSON-RPC ids are compared and logged as plain values; a numeric id outside
# the IEEE-754 safe-integer range is not a value any real client would send
# and is refused rather than silently truncated or mis-logged.
MAX_RPC_ID_MAGNITUDE = 1 << 53
# Bound on the number of top-level fields in `params`, independent of the
# tool-specific argument clamps `mcp.py`'s handlers already apply (#264's
# per-tool bounds are a different, downstream concern) -- this one guards the
# JSON-RPC envelope itself against an absurd fan-out of keys.
MAX_PARAMS_FIELDS = 64
# Longest a JSON-RPC `method` name this server would ever define could
# plausibly be; anything past this is refused before the (cheap) method-name
# comparison even runs.
MAX_METHOD_LENGTH = 128

# What a caller is told when there is no index to serve. Actionable, per #265,
# but with placeholders where the underlying SystemExit puts the absolute
# index directory: that message is written for an operator at a terminal, and
# a caller over HTTP must not be handed the host's filesystem layout. The real
# directory goes to the audit log, which is server-side.
INDEX_UNAVAILABLE = (
    "No repo2graph index is available on this server. "
    "Build one first with: repo2graph build <repo> -o <index-dir>. "
    "The server log names the directory that was checked."
)


def _public_repo_label(repo: Any) -> str | None:
    """Basename of `repo` for the unauthenticated discovery document.

    `GET /.well-known/mcp-server-metadata` skips auth, so an absolute host
    path must never appear. Both `/` and `\\` count as separators so a
    Windows-style path cannot leak on a POSIX host either.
    """
    if not repo:
        return None
    name = str(repo).rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1]
    return name or None


def server_metadata(
    repo: Any,
    index_present: bool,
    auth_modes: Any,
    index_built_at: str | None = None,
    tools: Any = None,
) -> dict[str, Any]:
    """The `.well-known/mcp-server-metadata` document.

    Lets a registry or client learn what this server does without opening a
    session. Deliberately says nothing about repository *content* -- only that
    an index exists and when it was built -- because this endpoint is
    unauthenticated by necessity. `repo` is the basename only; the absolute
    path would locate the project on the host.

    Args:
        repo: Repository path the server was pointed at, or None. Only the
            basename is published.
        index_present: Whether a readable index exists right now.
        auth_modes: The modes from `AuthConfig.modes`.
        index_built_at: ISO-8601 build time, or None when there is no index.
        tools: Tool descriptors; defaults to this server's three-plus-one.

    Returns:
        The metadata document.
    """
    from .mcp import TOOL_DESCRIPTIONS, TOOL_SCHEMAS

    if tools is None:
        tools = [
            {"name": name, "description": description, "inputSchema": TOOL_SCHEMAS[name]}
            for name, description in TOOL_DESCRIPTIONS.items()
        ]
    return {
        "name": "repo2graph",
        "version": __version__,
        "description": (
            "A tree-sitter code graph over a repository, answering "
            "questions from it with BM25 plus graph expansion."
        ),
        "tools": tools,
        "auth_modes": list(auth_modes),
        "repo": _public_repo_label(repo),
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
    # Applied to the connection by StreamRequestHandler.setup(); see
    # REQUEST_TIMEOUT_SECONDS for why it must not be left at None.
    timeout = REQUEST_TIMEOUT_SECONDS
    # HTTP/1.0 is chosen here, not inherited by omission. The base class also
    # defaults to it, but the choice carries weight: HTTP/1.0 makes
    # `close_connection` true after every response, so one connection carries
    # exactly one request and a body this server declined to read -- an
    # oversized Content-Length, a refused Transfer-Encoding, a read that timed
    # out part-way -- cannot be left in the socket for the *next* request to be
    # parsed out of. Moving to HTTP/1.1 would be safe on framing grounds (every
    # response below sets Content-Length, including the 204 from do_OPTIONS),
    # but it would buy keep-alive for clients that send one small frame and go
    # away, at the price of making that desync reachable again.
    protocol_version = "HTTP/1.0"
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
    allowed_hostnames: frozenset[str] = DEFAULT_ALLOWED_HOSTNAMES
    # #264/#267. None means "no rate limiting configured" -- make_handler
    # always sets a real RateLimiter unless the transport was built with
    # rate_limiting=False.
    rate_limiter: "RateLimiter | None" = None
    # #267: forwarded-identity headers are ignored unless both are set --
    # trust_proxy alone is not enough, the connecting peer must also appear in
    # trusted_proxies. Neither ever feeds authentication; only client-identity
    # derivation for rate limiting reads them.
    trust_proxy = False
    trusted_proxies: frozenset[str] = frozenset()

    # ---------------------------------------------------------- plumbing --

    def log_message(self, fmt: str, *args: Any) -> None:
        """Silence the default stderr access log.

        BaseHTTPRequestHandler writes an apache-style line per request. The
        audit log is the structured record of the same events, and two
        overlapping logs on one stream is worse than either alone.
        """
        return

    def _send_json(
        self,
        status: int,
        payload: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
        send_body: bool = True,
        enforce_response_limit: bool = False,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf8")
        # #264: a response cap independent of anything `query.py`'s own
        # budgets already do -- defense in depth, not a claim that this is the
        # normal path. Only opted into by the one call site that can return an
        # arbitrarily large tool result; discovery documents and error bodies
        # are always small and never pass this flag.
        if (
            enforce_response_limit
            and self.rate_limiter is not None
            and len(body) > self.rate_limiter.config.max_response_bytes
        ):
            status = INTERNAL_ERROR_STATUS
            payload = _rpc_error(
                payload.get("id") if isinstance(payload, dict) else None,
                INTERNAL_ERROR,
                "response exceeds the configured size limit",
            )
            body = json.dumps(payload, ensure_ascii=False).encode("utf8")
        # The whole send is guarded, not just the body write. `end_headers()`
        # flushes the header block down the same socket, so a client that has
        # already hung up raises there just as readily as at the write -- and
        # on Windows it raises ConnectionAbortedError, a third sibling beside
        # BrokenPipeError and ConnectionResetError. Anything that escapes here
        # reaches socketserver, which answers a routine disconnect with a
        # multi-line Python traceback on stderr -- the stream `events.py`
        # promises is strict JSON-lines, one record per line, to whatever SIEM
        # is tailing it. A client that went away is not worth that.
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            # This server answers tools, not browsers: no page should be able to
            # frame it, sniff it, or reach it cross-origin by default.
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            # Sanitised here rather than at each call site. Response splitting
            # is prevented by an invariant over *every* extra header this
            # server ever sends, and an invariant each caller has to remember
            # is one the next caller will not -- which is exactly how
            # `_challenge()`'s oidc_issuer came to be interpolated raw.
            for key, value in (extra_headers or {}).items():
                self.send_header(key, _sanitize_header_value(value))
            self.end_headers()
            if send_body and self.command != "HEAD":
                self.wfile.write(body)
        except (ConnectionError, OSError):
            return

    def _read_body(self) -> bytes:
        """Read the request body, refusing anything this server will not read.

        Raises:
            AuthError: Carrying the status to refuse with -- 411 for a framing
                this server does not speak, 400 for an unparseable
                Content-Length, 413 for an oversized one, 408 for a body that
                never fully arrived.
        """
        # Content-Length is the only framing accepted. A chunked body would be
        # measured as zero bytes and left sitting unread in the socket, so a
        # spec-legal client gets a baffling "expected a JSON-RPC object" and a
        # connection whose remaining bytes are somebody's idea of the next
        # request. A JSON-RPC frame is small and known-length, so refusing the
        # encoding in words costs nothing and says what is wrong.
        if (self.headers.get("Transfer-Encoding") or "").strip():
            raise AuthError("chunked request bodies are not supported", status=411)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise AuthError("invalid Content-Length", status=400) from None
        if length < 0 or length > MAX_BODY_BYTES:
            raise AuthError(f"request body must be at most {MAX_BODY_BYTES} bytes", status=413)
        if not length:
            return b""
        # The handler `timeout` turns a withheld body from a hang into an
        # exception -- but an exception out of do_POST is a handler thread that
        # dies with the client still waiting, so it has to become an answer
        # here. A short read is the same situation arriving politely: the
        # client promised `length` bytes and closed before sending them. Both
        # are 408. (TimeoutError is what `socket.timeout` has been since 3.10;
        # both it and the disconnect errors are OSError subclasses, named here
        # so a reader can see which cases are meant.)
        try:
            data = self.rfile.read(length)
        except (TimeoutError, ConnectionError, OSError):
            raise AuthError("timed out reading the request body", status=408) from None
        if len(data) != length:
            raise AuthError("request body ended before Content-Length", status=408)
        return data

    def _drain_body(self) -> None:
        """Discard the request body before refusing without having read it.

        Closing a socket that still holds unread bytes makes the OS send an RST
        rather than a FIN. On Windows the client then loses the response it was
        about to read and raises `ConnectionAbortedError` (WinError 10053)
        instead -- so a refusal that goes to the trouble of naming its reason
        ("Origin not allowed") arrives as an opaque connection error. The Host
        and Origin checks run before `_read_body` by design, which is what left
        them in that position.

        Best-effort and silent by construction: this runs on the way to a 403
        that has already been decided, so there is no failure it could report
        and nothing it could usefully do about one. `Content-Length` is bounded
        by MAX_BODY_BYTES exactly as in `_read_body` -- the point is to unblock
        one small frame, not to let an unauthenticated caller name a size.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return
        if length <= 0 or (self.headers.get("Transfer-Encoding") or "").strip():
            return
        try:
            self.rfile.read(min(length, MAX_BODY_BYTES))
        except (TimeoutError, ConnectionError, OSError):
            pass

    def _origin_ok(self, drain: bool = True) -> bool:
        if not _host_header_allowed(self.headers.get("Host"), self.allowed_hostnames):
            if drain:
                self._drain_body()
            self._send_json(FORBIDDEN, _rpc_error(None, INVALID_REQUEST, "Host header not allowed"))
            return False
        if not _origin_header_allowed(self.headers.get("Origin"), self.allowed_hostnames):
            if drain:
                self._drain_body()
            self._send_json(FORBIDDEN, _rpc_error(None, INVALID_REQUEST, "Origin not allowed"))
            return False
        return True

    # ------------------------------------------------------------- routes --

    def do_HEAD(self) -> None:
        """Serve headers for GET paths without sending response body."""
        self.do_GET(send_body=False)

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight requests."""
        # DNS-rebinding: validate Host before doing anything, same as do_POST.
        # A preflight does not normally carry a body, but if one did the refusal
        # would race the close exactly as it does there -- see `_drain_body`.
        if not self._origin_ok(drain=True):
            return
        origin = self.headers.get("Origin")
        self.send_response(204)
        if origin:
            self.send_header(
                "Access-Control-Allow-Origin",
                _sanitize_header_value(origin).replace("\r", "").replace("\n", ""),
            )
            self.send_header("Vary", "Origin")
        else:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, HEAD, OPTIONS")
        req_headers = self.headers.get(
            "Access-Control-Request-Headers", "Authorization, Content-Type"
        )
        self.send_header(
            "Access-Control-Allow-Headers",
            _sanitize_header_value(req_headers).replace("\r", "").replace("\n", ""),
        )
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self, send_body: bool = True) -> None:
        """Serve the unauthenticated discovery documents, and nothing else."""
        if not self._origin_ok(drain=False):
            return
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == WELL_KNOWN_METADATA.rstrip("/"):
            present = bool(self.open_index_fn and self._index_present())
            self._send_json(
                200,
                server_metadata(
                    self.repo,
                    present,
                    self.authenticator.config.modes,
                    index_built_at(self.index_dir),
                ),
                send_body=send_body,
            )
            return
        if path == WELL_KNOWN_CLIENT.rstrip("/"):
            if not self.publish_cimd:
                self._send_json(
                    404,
                    {
                        "error": "client metadata is not published; "
                        "start the server with --auth-cimd"
                    },
                    send_body=send_body,
                )
                return
            self._send_json(200, client_metadata_document(self.base_url), send_body=send_body)
            return
        if path == "/healthz":
            self._send_json(200, {"status": "ok", "version": __version__}, send_body=send_body)
            return
        self._send_json(404, {"error": f"no such path: {path}"}, send_body=send_body)

    def _index_present(self) -> bool:
        from .mcp import _has_index
        from pathlib import Path

        try:
            return _has_index(Path(str(self.index_dir)))
        except (OSError, TypeError, ValueError):
            return False

    def do_POST(self) -> None:
        """Handle one JSON-RPC request on /mcp."""
        if not self._origin_ok(drain=True):
            return
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path not in ("/mcp", "/"):
            self._send_json(404, {"error": f"no such path: {path}"})
            return

        # #293: Content-Type, checked before the body is read -- same
        # ordering rationale as the Host/Origin gate above: an unread body
        # left in the socket at refusal time is drained rather than left for
        # the next request to be parsed out of, and a wrong media type costs
        # nothing beyond the header parse rather than a full JSON parse.
        if not _content_type_ok(self.headers.get("Content-Type")):
            self._drain_body()
            self._send_json(
                UNSUPPORTED_MEDIA_TYPE,
                _rpc_error(None, INVALID_REQUEST, "Content-Type must be application/json"),
            )
            return
        try:
            raw = self._read_body()
        except AuthError as exc:
            self._send_json(exc.status, _rpc_error(None, INVALID_REQUEST, exc.message))
            return

        # #293: strict UTF-8. A lossy decode ("replace") would corrupt a
        # structurally-valid frame's content rather than refuse it -- the
        # digit-for-digit wrong body silently becomes the thing that gets
        # authenticated, logged and dispatched. Invalid encoding is treated
        # the same as any other unparseable body: PARSE_ERROR.
        try:
            text = raw.decode("utf8") if raw else ""
        except UnicodeDecodeError:
            self._send_json(400, _rpc_error(None, PARSE_ERROR, "request body is not valid UTF-8"))
            return

        try:
            request = _parse_rpc_body(text)
        except (ValueError, RecursionError):
            # RecursionError, not just ValueError: `json.loads` blows the stack
            # rather than raising JSONDecodeError on a deeply nested document,
            # and `b"[" * 20000 + b"]" * 20000` is ~40 KB -- comfortably inside
            # MAX_BODY_BYTES, and reachable here by an unauthenticated caller,
            # since authenticate() is still several lines away. A body Python
            # cannot parse -- including one that fails a #293 structural guard
            # (nesting depth, a duplicate key, a non-finite numeric constant)
            # -- is invalid JSON as far as this server is concerned, whatever
            # the specific reason was.
            self._send_json(400, _rpc_error(None, PARSE_ERROR, "invalid JSON"))
            return
        if not isinstance(request, dict):
            self._send_json(400, _rpc_error(None, INVALID_REQUEST, "expected a JSON-RPC object"))
            return

        # #293: method/jsonrpc-version/id-type/params-schema, checked before
        # any field is trusted for auth, dispatch or the audit log.
        envelope_error = _validate_envelope(request)
        if envelope_error is not None:
            status, code, message = envelope_error
            self._send_json(status, _rpc_error(request.get("id"), code, message))
            return

        rpc_id = request.get("id")
        method = str(request.get("method") or "")
        params = request.get("params") or {}

        # Authentication happens before the method is dispatched and before any
        # index is opened, so a rejected call costs nothing and touches nothing.
        try:
            identity = self.authenticator.authenticate(self.headers.get("Authorization"))
        except AuthError as exc:
            self._reject(rpc_id, method, params, exc)
            return

        # #264: per-client rate limit, then a bounded concurrency slot. Both
        # run after authentication so the identity used to key them is the
        # verified subject where one exists, per #264's requirement -- an
        # anonymous/static-bearer caller falls back to network identity inside
        # `_client_identity`.
        limiter = self.rate_limiter
        if limiter is not None:
            client_id = self._client_identity(identity)
            if not limiter.check_rate(client_id):
                self._send_rate_limited(rpc_id, client_id)
                return
            if not limiter.acquire_slot():
                self._send_overloaded(rpc_id, client_id)
                return
            try:
                self._dispatch_guarded(rpc_id, method, params, identity)
            finally:
                limiter.release_slot()
        else:
            self._dispatch_guarded(rpc_id, method, params, identity)

    def _dispatch_guarded(self, rpc_id: Any, method: str, params: Any, identity: Any) -> None:
        try:
            self._dispatch(rpc_id, method, params, identity)
        except Exception as exc:  # pragma: no cover - guard
            self.audit.record(
                tool=method,
                params=params,
                identity=identity.subject,
                outcome="error",
                error=str(exc),
            )
            self._send_json(500, _rpc_error(rpc_id, INTERNAL_ERROR, "Internal server error"))

    # ------------------------------------------------------ #264 identity --

    def _client_identity(self, identity: Any) -> str:
        """A stable key for rate limiting: verified subject, else network identity.

        A real OIDC subject (`sub` claim) distinguishes callers sharing one
        server; the shared static bearer token cannot distinguish its holders
        from each other by design (it names no one in particular), so every
        `bearer`-mode caller collapses to one bucket keyed on the mode itself
        -- a known, documented limitation, not an oversight. No auth
        configured at all falls back to network identity.
        """
        subject = getattr(identity, "subject", None)
        mode = getattr(identity, "mode", "none")
        if mode == "oidc" and subject:
            return f"oidc:{subject}"
        if mode == "bearer":
            return "bearer:shared"
        return f"ip:{self._remote_ip()}"

    def _remote_ip(self) -> str:
        """The caller's address, honouring `X-Forwarded-For` only when both
        `trust_proxy` is enabled AND the connecting peer is itself in
        `trusted_proxies` (#267). Neither condition alone is enough -- an
        operator that turns on `trust_proxy` without also naming which peers
        may set the header would let any direct, untrusted caller claim to be
        someone else purely by sending the header itself.
        """
        peer = self.client_address[0] if self.client_address else "unknown"
        if self.trust_proxy and peer in self.trusted_proxies:
            forwarded = _first_forwarded_ip(self.headers.get("X-Forwarded-For"))
            if forwarded:
                return forwarded
        return peer

    def _send_rate_limited(self, rpc_id: Any, client_id: str) -> None:
        limiter = self.rate_limiter
        window = int(limiter.config.window_seconds) if limiter is not None else 60
        self.audit.record(
            tool="<rate_limit>",
            params={},
            identity=client_id,
            outcome="rate_limited",
            error="per-client rate limit exceeded",
        )
        # #264: observable without exposing secrets -- client_id is either an
        # OIDC subject, the fixed literal "bearer:shared", or a bare IP; never
        # a token or a claim body.
        emit("rate_limited", level="warning", client=client_id, kind="request_rate")
        self._send_json(
            TOO_MANY_REQUESTS,
            _rpc_error(rpc_id, RATE_LIMITED_CODE, "rate limit exceeded; retry later"),
            {"Retry-After": str(window)},
        )

    def _send_overloaded(
        self, rpc_id: Any, client_id: str, reason: str = "server is at capacity"
    ) -> None:
        self.audit.record(
            tool="<rate_limit>",
            params={},
            identity=client_id,
            outcome="rate_limited",
            error=reason,
        )
        emit("rate_limited", level="warning", client=client_id, kind="overload", reason=reason)
        self._send_json(
            SERVICE_UNAVAILABLE,
            _rpc_error(rpc_id, OVERLOADED_CODE, f"{reason}; retry later"),
            {"Retry-After": "1"},
        )

    def _reject(self, rpc_id: Any, method: str, params: Any, exc: AuthError) -> None:
        """Refuse a call, record it, and execute nothing."""
        tool = str((params or {}).get("name") or method)
        self.audit.record(
            tool=tool,
            params=(params or {}).get("arguments") or {},
            identity="anonymous",
            outcome="auth_rejected",
            error=exc.message,
        )
        emit(
            "auth_rejected",
            level="warning",
            tool=tool,
            remote=self.client_address[0] if self.client_address else None,
            reason=exc.message,
        )
        self._send_json(
            exc.status,
            _rpc_error(rpc_id, UNAUTHORIZED, "Unauthorized"),
            {"WWW-Authenticate": self._challenge()},
        )

    def _challenge(self) -> str:
        config = self.authenticator.config
        if config.oidc_issuer:
            return f'Bearer realm="repo2graph", authorization_uri="{config.oidc_issuer}"'
        return 'Bearer realm="repo2graph"'

    def _dispatch(self, rpc_id: Any, method: str, params: Any, identity: Any) -> None:
        """Route one authenticated JSON-RPC method."""
        if method == "initialize":
            self._send_json(
                200,
                _rpc_result(
                    rpc_id,
                    {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "repo2graph", "version": __version__},
                    },
                ),
            )
            return
        if method in ("notifications/initialized", "ping"):
            self._send_json(200, _rpc_result(rpc_id, {}))
            return
        if method == "tools/list":
            from .mcp import TOOL_DESCRIPTIONS, TOOL_SCHEMAS, tool_annotations

            # #292: `self.repo` is this server's build-from root, the same
            # signal stdio's `serve()` passes as `auto_build`. When it is set,
            # a tool's first call may parse the whole repository and write
            # `.r2g/**`, and `readOnlyHint: true` would be a lie to a client
            # that has no other way to find out.
            auto_build = self.repo is not None
            result: dict[str, Any] = {
                "tools": [
                    {
                        "name": name,
                        "description": description,
                        "inputSchema": TOOL_SCHEMAS[name],
                        "annotations": tool_annotations(name, auto_build),
                    }
                    for name, description in TOOL_DESCRIPTIONS.items()
                ]
            }
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
        self._send_json(404, _rpc_error(rpc_id, METHOD_NOT_FOUND, f"unknown method: {method!r}"))

    def _call_tool(self, rpc_id: Any, params: Any, identity: Any) -> None:
        """Run one tool, timing it and recording the outcome."""
        from .mcp import ToolError
        from .query import count_tokens

        # #293: `tools/call` params have a schema of their own, one level
        # below the generic envelope check in `_validate_envelope` -- `name`
        # must be a real tool-name string and `arguments`, if present, must be
        # an object. This runs before authentication has any bearing on cost
        # (the request is already authenticated by the time `_dispatch`
        # reaches here) but before the index is touched or a build considered.
        raw_name = (params or {}).get("name")
        if not isinstance(raw_name, str) or not raw_name:
            self._send_json(
                400, _rpc_error(rpc_id, INVALID_PARAMS, "params.name must be a non-empty string")
            )
            return
        raw_arguments = (params or {}).get("arguments")
        if raw_arguments is not None and not isinstance(raw_arguments, dict):
            self._send_json(
                400, _rpc_error(rpc_id, INVALID_PARAMS, "params.arguments must be an object")
            )
            return

        name = raw_name
        arguments = raw_arguments or {}
        with timer() as elapsed:
            try:
                if name == "repo_build_status":
                    # The one tool answerable without an index: asking for build
                    # progress must not itself wait on the build.
                    text = self.dispatch_fn(
                        None, name, arguments, cache=self.cache, tasks=self.tasks
                    )
                    index = None
                else:
                    # #264: a server-wide cap on concurrent `open_index_fn`
                    # calls, independent of the general concurrency slot
                    # acquired in `do_POST` -- that one bounds *all* in-flight
                    # requests, this one specifically bounds how many may be
                    # inside index-open-or-auto-build at once, which is the
                    # path that can trigger an actual build. Non-blocking: a
                    # caller arriving while the gate is full is told to retry
                    # rather than piling up behind a build that may take a
                    # while.
                    limiter = self.rate_limiter
                    if limiter is not None and not limiter.acquire_build_slot():
                        client_id = self._client_identity(identity)
                        self._send_overloaded(
                            rpc_id, client_id, reason="index build concurrency limit reached"
                        )
                        return
                    try:
                        index, pending = self.open_index_fn(
                            self.index_dir, self.repo, self.cache, self.tasks
                        )
                    finally:
                        if limiter is not None:
                            limiter.release_build_slot()
                    text = (
                        pending
                        if pending is not None
                        else self.dispatch_fn(
                            index, name, arguments, cache=self.cache, tasks=self.tasks
                        )
                    )
            except SystemExit as exc:
                # open_index exits rather than raises when there is no index and
                # nothing safe to build from. Over HTTP that is a 503, not a
                # dead process: the server stays up and says what is wrong.
                #
                # What it says is not str(exc). That message is written for the
                # operator at a terminal and names the absolute index directory
                # -- twice -- so relaying it verbatim hands a caller the host's
                # filesystem layout, and hands an agent's context window the
                # same. It is the exact disclosure `_public_repo_label` exists
                # to prevent one endpoint over. The detail stays in the audit
                # log, which is server-side and is where an operator looks.
                self.audit.record(
                    tool=name,
                    params=arguments,
                    identity=identity.subject,
                    outcome="error",
                    duration_ms=elapsed.ms,
                    error=str(exc).strip() or "Index unavailable",
                )
                self._send_json(503, _rpc_error(rpc_id, INTERNAL_ERROR, INDEX_UNAVAILABLE))
                return
            except Exception as exc:
                self.audit.record(
                    tool=name,
                    params=arguments,
                    identity=identity.subject,
                    outcome="error",
                    duration_ms=elapsed.ms,
                    error=str(exc),
                )
                self._send_json(500, _rpc_error(rpc_id, INTERNAL_ERROR, "Internal server error"))
                return
        self.audit.record(
            tool=name,
            params=arguments,
            identity=identity.subject,
            outcome="success",
            duration_ms=elapsed.ms,
            result_tokens=count_tokens(text),
        )
        self._send_json(
            200,
            _rpc_result(
                rpc_id,
                {
                    "content": [{"type": "text", "text": text}],
                    # A ToolError (bad input, unknown node/tool, git failure)
                    # is still answered with its sentence, but flagged.
                    "isError": isinstance(text, ToolError),
                },
            ),
            enforce_response_limit=True,
        )


def _rpc_result(rpc_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _rpc_error(rpc_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def make_handler(
    index_dir: Any,
    repo: Any = None,
    auth_config: AuthConfig | None = None,
    audit: AuditLogger | None = None,
    cache: Any = None,
    base_url: str = "http://127.0.0.1:8719",
    publish_cimd: bool = False,
    opener: Callable[[str], Any] | None = None,
    tasks: Any = None,
    allowed_hostnames: frozenset[str] | None = None,
    rate_limiter: "RateLimiter | None" = None,
    trust_proxy: bool = False,
    trusted_proxies: frozenset[str] | None = None,
) -> type[MCPRequestHandler]:
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
        allowed_hostnames: Bare hostnames accepted in Host/Origin headers;
            defaults to `DEFAULT_ALLOWED_HOSTNAMES` (loopback only).
        rate_limiter: A `RateLimiter` shared by every request this handler
            class serves, or None to disable rate limiting entirely (#264).
        trust_proxy: Whether `X-Forwarded-For` may ever be consulted (#267).
        trusted_proxies: The peers allowed to set it, when `trust_proxy` is
            True. Both must hold -- see `MCPRequestHandler._remote_ip`.

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
    _Handler.allowed_hostnames = allowed_hostnames or DEFAULT_ALLOWED_HOSTNAMES
    _Handler.rate_limiter = rate_limiter
    _Handler.trust_proxy = trust_proxy
    _Handler.trusted_proxies = trusted_proxies or frozenset()
    return _Handler


class MCPHTTPServer(ThreadingHTTPServer):
    """`ThreadingHTTPServer` whose error reporting stays on one line.

    socketserver's `handle_error` answers any exception that escapes a request
    with a dashed banner and a full Python traceback on stderr. stderr is the
    stream `repo2graph.events` promises is strict JSON-lines -- a SIEM tailing
    it parses one record per line -- so the default printer turns every
    escaping error into a dozen lines that parse as none. A handler failure is
    precisely when an operator most needs the record to be machine-readable.

    This lives on the server and not on the handler because socketserver calls
    `handle_error` on the server object: `ThreadingMixIn.process_request_thread`
    catches the handler's exception and has only the server to report it to.
    There is no per-handler hook to override.
    """

    def handle_error(self, request: Any, client_address: Any) -> None:
        """Report one escaping handler exception as a single JSON event."""
        exc = sys.exc_info()[1]
        emit(
            "http_handler_error",
            level="error",
            remote=client_address[0] if client_address else None,
            error=f"{type(exc).__name__}: {exc}",
        )


#: How often `serve_forever` checks whether it has been asked to stop.
#:
#: `shutdown()` sets a flag and then waits for the serving loop to notice it on
#: its next `selector.select(poll_interval)`, so this value *is* the shutdown
#: latency -- and socketserver's default is 0.5s. That is the delay on Ctrl-C and
#: on every `stop()`, paid for a loop that is otherwise idle. 0.05s wakes the
#: selector 20 times a second instead of twice, which is an unmeasurable amount
#: of work for a tenfold faster stop.
SHUTDOWN_POLL_SECONDS = 0.05


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
        allow_hosts: Extra bare hostnames to accept in Host/Origin headers,
            beyond the loopback default and `host` itself -- for a
            deliberate non-loopback deployment (e.g. behind a reverse proxy
            that rewrites Host to a public domain name). Everything else is
            refused with 403, even when `host` is itself non-loopback.
        rate_limiting: Whether #264's per-client rate limit, concurrency slot
            and build-concurrency gate are active at all. True by default;
            pass False only to disable the feature outright (tests
            isolating some other behaviour, mainly).
        rate_limit_config: Overrides the default ceilings when
            `rate_limiting` is True. None uses `RateLimitConfig()`'s
            defaults, generous enough not to throttle ordinary use.
        trust_proxy: #267. False by default -- `X-Forwarded-For` is ignored
            regardless of `trusted_proxies` unless this is explicitly True.
            Never affects authentication, only the network identity used to
            key rate limiting.
        trusted_proxies: The bare peer addresses allowed to set
            `X-Forwarded-For` when `trust_proxy` is True. A peer not in this
            set has its header ignored even with `trust_proxy=True` -- both
            conditions must hold.
        insecure_transport_ack: #267. Suppresses the startup warning emitted
            when `host` is non-loopback and authentication is configured:
            this server does not terminate TLS itself, so a bearer or OIDC
            credential sent to a non-loopback bind travels in clear unless a
            TLS-terminating reverse proxy sits in front. Passing True is the
            operator's explicit acknowledgement that one does.

    Raises:
        ValueError: If asked to bind beyond loopback with no credential set,
            which would publish the whole indexed repository to the network.
    """

    def __init__(
        self,
        index_dir: Any,
        repo: Any = None,
        host: str = "127.0.0.1",
        port: int = 8719,
        auth_config: AuthConfig | None = None,
        audit: AuditLogger | None = None,
        cache: Any = None,
        publish_cimd: bool = False,
        opener: Callable[[str], Any] | None = None,
        tasks: Any = None,
        allow_hosts: Any = None,
        rate_limiting: bool = True,
        rate_limit_config: RateLimitConfig | None = None,
        trust_proxy: bool = False,
        trusted_proxies: Any = None,
        insecure_transport_ack: bool = False,
    ) -> None:
        auth_config = auth_config or AuthConfig()
        if host not in LOOPBACK and not auth_config.enabled:
            raise ValueError(
                f"refusing to bind {host}:{port} with no authentication: a "
                f"repo2graph index is the whole repository in searchable form. "
                f"Pass --auth-token or --auth-oidc-issuer, or bind 127.0.0.1."
            )
        # #267: a credential is now configured (the check above passed), but
        # this server still does not terminate TLS -- so the credential
        # travels in clear across whatever network reaches this bind unless a
        # reverse proxy in front of it does. Warning rather than refusing:
        # `insecure_transport_ack=False` (the default) still starts the
        # server, because a real deployment behind a TLS-terminating proxy is
        # exactly this shape and must not be blocked by it -- the warning is
        # the "high-visibility" half of #267's "error or explicit warning".
        if host not in LOOPBACK and not insecure_transport_ack:
            banner = (
                "\n" + "!" * 78 + "\nrepo2graph-mcp: binding to a non-loopback address "
                f"({host}:{port}) with authentication configured.\n"
                "This server does NOT terminate TLS. Bearer/OIDC credentials sent to this\n"
                "bind travel in clear text unless a TLS-terminating reverse proxy sits in\n"
                "front of it -- see docs/ENTERPRISE_DEPLOYMENT.md, 'Recommended architecture'.\n"
                "If a proxy is already in place, pass insecure_transport_ack=True (wired\n"
                "from the CLI as --http-insecure-ok) to silence this warning.\n" + "!" * 78 + "\n"
            )
            try:
                sys.stderr.write(banner)
                sys.stderr.flush()
            except Exception:
                pass
            emit(
                "http_transport_no_tls_termination",
                level="error",
                host=host,
                port=port,
            )
        self.host, self.port = host, port
        self.auth_config = auth_config
        self.rate_limiter = (
            RateLimiter(rate_limit_config or RateLimitConfig()) if rate_limiting else None
        )
        self.trust_proxy = bool(trust_proxy)
        self.trusted_proxies = frozenset(
            str(p).strip() for p in (trusted_proxies or ()) if str(p).strip()
        )
        # The bind host itself is always trusted -- it's already gated by the
        # auth-required check above when non-loopback -- plus the loopback
        # defaults, plus anything the deployment explicitly allowlists. A
        # wildcard bind (0.0.0.0, ::) names no real hostname a client would
        # ever send, so it is not added.
        allowed_hostnames = set(DEFAULT_ALLOWED_HOSTNAMES)
        if host not in ("0.0.0.0", "::", ""):
            allowed_hostnames.add(host.lower())
        for extra in allow_hosts or ():
            hostname = str(extra).strip().lower()
            if hostname:
                allowed_hostnames.add(hostname)
        self.allowed_hostnames = frozenset(allowed_hostnames)
        self._handler = make_handler(
            index_dir,
            repo,
            auth_config,
            audit,
            cache,
            base_url=f"http://{host}:{port}",
            publish_cimd=publish_cimd,
            opener=opener,
            tasks=tasks,
            allowed_hostnames=self.allowed_hostnames,
            rate_limiter=self.rate_limiter,
            trust_proxy=self.trust_proxy,
            trusted_proxies=self.trusted_proxies,
        )
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        """Start serving on a daemon thread.

        Returns:
            The port actually bound, which differs from the requested one when
            port 0 asked the OS to choose.
        """
        httpd = MCPHTTPServer((self.host, self.port), self._handler)
        self._httpd = httpd
        httpd.daemon_threads = True
        self.port = httpd.server_address[1]
        self._handler.base_url = f"http://{self.host}:{self.port}"
        # The thread body closes over the local `httpd`, not `self._httpd`: the
        # attribute is Optional and `stop()` sets it to None, so reading it from
        # inside the thread is both unprovable to a type checker and an actual
        # AttributeError if a caller stops the transport before the thread is
        # scheduled.
        self._thread = threading.Thread(
            target=lambda: httpd.serve_forever(poll_interval=SHUTDOWN_POLL_SECONDS),
            name="repo2graph-http",
            daemon=True,
        )
        self._thread.start()
        emit(
            "http_transport_started",
            level="info",
            host=self.host,
            port=self.port,
            auth_modes=self.auth_config.modes,
        )
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
