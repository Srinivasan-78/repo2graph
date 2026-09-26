"""The HTTP transport end to end: auth, audit, discovery, and what it refuses.

Real sockets on an ephemeral port, real JSON-RPC frames, real Authorization
headers. The auth unit tests in test_auth.py prove the token logic; these prove
the logic is actually *reached* -- that a rejected call returns 401 and the tool
never runs, which is a property of the wiring rather than of the validator.

Every OIDC test injects a fake issuer through `opener`, so nothing here opens a
socket to the outside world. One test asserts that directly.
"""

import argparse
import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from conftest import MINI_QUERY, build_mini_index, write_mini_repo
from repo2graph.audit import AuditConfig, AuditLogger
from repo2graph.auth import AuthConfig
from repo2graph.http_server import HTTPTransport, server_metadata
from repo2graph.mcp import _auth_config

from test_auth import FakeIssuer, ISSUER, AUDIENCE, claims, sign

import io


@pytest.fixture
def index(tmp_path):
    return build_mini_index(write_mini_repo(tmp_path), tmp_path / "idx")


class Server:
    """A running transport plus the audit buffer it writes to."""

    def __init__(self, transport, audit_stream):
        self.transport = transport
        self.audit_stream = audit_stream
        self.port = transport.port

    def url(self, path="/mcp"):
        return f"http://127.0.0.1:{self.port}{path}"

    def rpc(self, method, params=None, token=None, rpc_id=1, headers=None):
        """POST one JSON-RPC frame; returns (status, parsed body).

        `headers` lets a test override transport-level headers (Host, Origin)
        that `urllib.request` would otherwise set from the URL; anything
        passed here replaces the default of the same name.
        """
        body = json.dumps(
            {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params or {}}
        ).encode()
        request = urllib.request.Request(
            self.url(),
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        if token is not None:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def get(self, path):
        try:
            with urllib.request.urlopen(self.url(path), timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def call(self, tool="repo_map", arguments=None, token=None):
        return self.rpc("tools/call", {"name": tool, "arguments": arguments or {}}, token=token)

    def audit_lines(self):
        return [
            json.loads(line) for line in self.audit_stream.getvalue().splitlines() if line.strip()
        ]


@pytest.fixture
def make_server(index, tmp_path):
    """Factory starting a transport on an ephemeral port; stopped on teardown."""
    started = []

    def _make(
        auth_config=None,
        opener=None,
        cache=None,
        publish_cimd=False,
        level="all",
        audit_path=None,
        repo=None,
    ):
        stream = io.StringIO()
        audit = AuditLogger(AuditConfig(level=level, path=audit_path), stream=stream)
        transport = HTTPTransport(
            index,
            repo,
            host="127.0.0.1",
            port=0,
            auth_config=auth_config,
            audit=audit,
            cache=cache,
            publish_cimd=publish_cimd,
            opener=opener,
        )
        transport.start()
        server = Server(transport, stream)
        started.append(transport)
        return server

    yield _make
    for transport in started:
        transport.stop()


# ------------------------------------------------------------- no auth ----


def test_with_no_auth_every_tool_call_succeeds(make_server):
    server = make_server()
    status, body = server.call("repo_map")
    assert status == 200
    assert body["result"]["content"][0]["text"].strip()


def test_with_no_auth_a_supplied_token_is_simply_ignored(make_server):
    server = make_server()
    status, _ = server.call("repo_map", token="irrelevant")
    assert status == 200


def test_initialize_and_tools_list(make_server):
    server = make_server()
    status, body = server.rpc("initialize")
    assert status == 200 and body["result"]["serverInfo"]["name"] == "repo2graph"

    status, body = server.rpc("tools/list")
    names = {t["name"] for t in body["result"]["tools"]}
    assert {"repo_map", "repo_search", "repo_neighbours"} <= names


def test_tools_list_carries_ttl_and_cache_scope(make_server):
    """MCP cache metadata rides in _meta, where an older client ignores it."""
    server = make_server()
    _status, body = server.rpc("tools/list")
    meta = body["result"]["_meta"]
    assert meta["ttlMs"] == 3_600_000
    assert meta["cacheScope"] == "global"


def test_an_unknown_method_is_a_clean_error(make_server):
    server = make_server()
    status, body = server.rpc("does/not/exist")
    assert status == 404 and body["error"]["code"] == -32601


def test_malformed_json_is_a_clean_error(make_server):
    server = make_server()
    request = urllib.request.Request(
        server.url(), data=b"{not json", method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(request, timeout=10)
        raise AssertionError("expected a 400")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400
        assert json.loads(exc.read())["error"]["code"] == -32700


# -------------------------------------------------------- static token ----


def test_the_right_token_is_admitted(make_server):
    server = make_server(AuthConfig(token="s3cret"))
    status, body = server.call("repo_map", token="s3cret")
    assert status == 200 and body["result"]["content"][0]["text"].strip()


@pytest.mark.parametrize(
    "token",
    [pytest.param("wrong", id="wrong-token"), pytest.param(None, id="no-header")],
)
def test_a_bad_static_credential_returns_401_and_does_not_run_the_tool(
    make_server, monkeypatch, token
):
    """The wiring property: rejected means *not executed*, not merely not returned."""
    from repo2graph import mcp

    ran = []
    monkeypatch.setattr(mcp, "tool_repo_map", lambda idx: ran.append(1) or "x")

    server = make_server(AuthConfig(token="s3cret"))
    status, body = server.call("repo_map", token=token)

    assert status == 401
    assert body["error"]["message"] == "Unauthorized"
    assert ran == [], "the tool ran despite a failed authentication"


def test_a_401_carries_a_www_authenticate_challenge(make_server):
    server = make_server(AuthConfig(token="s3cret"))
    request = urllib.request.Request(
        server.url(), data=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}', method="POST"
    )
    try:
        urllib.request.urlopen(request, timeout=10)
        raise AssertionError("expected a 401")
    except urllib.error.HTTPError as exc:
        assert exc.code == 401
        assert "Bearer" in exc.headers.get("WWW-Authenticate", "")


def test_a_rejection_never_echoes_the_attempted_token(make_server):
    server = make_server(AuthConfig(token="s3cret"))
    _status, body = server.call("repo_map", token="hunter2-attempt")
    assert "hunter2-attempt" not in json.dumps(body)


# ---------------------------------------------------------------- oidc ----


def oidc(**over):
    config = {"oidc_issuer": ISSUER, "audience": AUDIENCE}
    config.update(over)
    return AuthConfig(**config)


def test_a_valid_jwt_is_admitted(make_server):
    server = make_server(oidc(), opener=FakeIssuer())
    status, body = server.call("repo_map", token=sign(claims()))
    assert status == 200 and body["result"]["content"][0]["text"].strip()


# `tests/test_auth.py` already pins *why* each of these claims is refused, at the
# `decode_jwt` level. What this file adds is that the refusal reaches the HTTP
# surface as a 401 and that the tool never runs -- so every row asserts the tool
# did not execute, which the issuer and audience rows previously did not check.
@pytest.mark.parametrize(
    "over",
    [
        pytest.param({"exp": time.time() - 3600}, id="expired"),
        pytest.param({"iss": "https://evil.example.com"}, id="wrong-issuer"),
        pytest.param({"aud": "someone-else"}, id="wrong-audience"),
    ],
)
def test_a_bad_jwt_returns_401_and_does_not_run_the_tool(make_server, monkeypatch, over):
    from repo2graph import mcp

    ran = []
    monkeypatch.setattr(mcp, "tool_repo_map", lambda idx: ran.append(1) or "x")

    server = make_server(oidc(), opener=FakeIssuer())
    status, body = server.call("repo_map", token=sign(claims(**over)))

    assert status == 401
    assert body["error"]["message"] == "Unauthorized"
    assert ran == []


def test_the_oidc_challenge_names_the_issuer(make_server):
    server = make_server(oidc(), opener=FakeIssuer())
    request = urllib.request.Request(
        server.url(), data=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}', method="POST"
    )
    try:
        urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as exc:
        assert ISSUER in exc.headers.get("WWW-Authenticate", "")


# --------------------------------------------------------------- audit ----


def test_a_successful_call_is_audited(make_server):
    server = make_server()
    server.call("repo_search", {"query": MINI_QUERY})
    (record,) = [r for r in server.audit_lines() if r["event"] == "tool_call"]

    assert record["tool"] == "repo_search"
    assert record["outcome"] == "success"
    assert record["duration_ms"] >= 1
    assert record["result_tokens"] > 0
    assert record["params"]["query"] == MINI_QUERY


def test_a_rejected_call_is_audited_as_auth_rejected(make_server):
    server = make_server(AuthConfig(token="s3cret"))
    server.call("repo_search", {"query": "x"}, token="wrong")
    records = [r for r in server.audit_lines() if r["event"] == "tool_call"]
    assert records and records[0]["outcome"] == "auth_rejected"


def test_the_oidc_subject_becomes_the_audit_identity(make_server):
    server = make_server(oidc(), opener=FakeIssuer())
    server.call("repo_map", token=sign(claims(sub="alice@example.com")))
    (record,) = [r for r in server.audit_lines() if r["event"] == "tool_call"]
    assert record["identity"] == "alice@example.com"


def test_audit_level_none_produces_no_records(make_server):
    server = make_server(level="none")
    server.call("repo_map")
    assert [r for r in server.audit_lines() if r["event"] == "tool_call"] == []


def test_a_secret_argument_is_redacted_in_the_audit_line(make_server):
    server = make_server()
    server.call("repo_search", {"query": "deploy using ghp_" + "z" * 36})
    raw = server.audit_stream.getvalue()
    assert "ghp_" + "z" * 36 not in raw
    assert "redacted" in raw


# ----------------------------------------------------------- discovery ----


def test_the_metadata_document_has_every_required_field(make_server):
    server = make_server()
    status, doc = server.get("/.well-known/mcp-server-metadata")
    assert status == 200
    for field in (
        "name",
        "version",
        "description",
        "tools",
        "auth_modes",
        "repo",
        "index_present",
        "index_built_at",
    ):
        assert field in doc, field
    assert doc["name"] == "repo2graph"
    assert {t["name"] for t in doc["tools"]} >= {"repo_map", "repo_search"}
    assert all("inputSchema" in t for t in doc["tools"])


def test_index_present_is_false_before_a_build_and_true_after(tmp_path):
    """The document must describe the index that exists, not the one configured."""
    repo = write_mini_repo(tmp_path)
    out = tmp_path / ".r2g"
    transport = HTTPTransport(
        out, repo, port=0, audit=AuditLogger(AuditConfig(level="none"), stream=io.StringIO())
    )
    transport.start()
    try:
        server = Server(transport, io.StringIO())
        _status, before = server.get("/.well-known/mcp-server-metadata")
        assert before["index_present"] is False
        assert before["index_built_at"] is None

        server.call("repo_map")  # auto-builds on the first call
        _status, after = server.get("/.well-known/mcp-server-metadata")
        assert after["index_present"] is True
        assert after["index_built_at"]
    finally:
        transport.stop()


@pytest.mark.parametrize(
    "config,expected",
    [
        (None, ["none"]),
        (AuthConfig(token="x"), ["bearer"]),
        (AuthConfig(oidc_issuer=ISSUER), ["oidc"]),
        (AuthConfig(token="x", oidc_issuer=ISSUER), ["bearer", "oidc"]),
    ],
)
def test_auth_modes_reflect_the_flags(make_server, config, expected):
    server = make_server(config, opener=FakeIssuer())
    _status, doc = server.get("/.well-known/mcp-server-metadata")
    assert doc["auth_modes"] == expected


def test_discovery_is_reachable_without_a_credential(make_server):
    """Discovery that needs the credential it describes obtaining is useless."""
    server = make_server(AuthConfig(token="s3cret"))
    status, doc = server.get("/.well-known/mcp-server-metadata")
    assert status == 200 and doc["auth_modes"] == ["bearer"]


def test_unauthenticated_metadata_omits_absolute_repo_path(make_server):
    """ISS-198: discovery is public; the host path must not appear even with auth configured."""
    abs_repo = "/srv/repos/acme-billing-service"
    server = make_server(AuthConfig(token="s3cret"), repo=abs_repo)
    status, doc = server.get("/.well-known/mcp-server-metadata")
    assert status == 200
    raw = json.dumps(doc)
    assert abs_repo not in raw
    assert "/srv/repos" not in raw
    assert doc["repo"] == "acme-billing-service"
    assert not str(doc["repo"]).startswith("/")
    assert "\\" not in str(doc["repo"])
    assert doc["auth_modes"] == ["bearer"]
    assert "index_present" in doc
    assert "index_built_at" in doc


def test_the_metadata_document_discloses_no_repository_content(make_server):
    """It is unauthenticated, so it must describe shape and nothing else."""
    server = make_server()
    _status, doc = server.get("/.well-known/mcp-server-metadata")
    raw = json.dumps(doc)
    assert "ACME_DEPLOYMENT_LEDGER_TOKEN" not in raw
    assert "abc123deadbeef" not in raw
    assert "def route_request" not in raw


def test_cimd_is_served_only_when_asked_for(make_server):
    assert make_server().get("/.well-known/oauth-client-metadata")[0] == 404

    server = make_server(publish_cimd=True)
    status, doc = server.get("/.well-known/oauth-client-metadata")
    assert status == 200
    assert doc["client_id"].endswith("/.well-known/oauth-client-metadata")
    assert "authorization_code" in doc["grant_types"]


def test_healthz(make_server):
    status, body = make_server().get("/healthz")
    assert status == 200 and body["status"] == "ok"


def test_an_unknown_path_is_404(make_server):
    assert make_server().get("/admin")[0] == 404


# ------------------------------------------------------------- refusals ----


def test_binding_beyond_loopback_without_auth_is_refused(index):
    """A code index is the whole repository in searchable form."""
    with pytest.raises(ValueError, match="refusing to bind"):
        HTTPTransport(index, host="0.0.0.0", port=0)


def test_binding_beyond_loopback_with_auth_is_allowed(index):
    transport = HTTPTransport(
        index,
        host="0.0.0.0",
        port=0,
        auth_config=AuthConfig(token="s3cret"),
        audit=AuditLogger(AuditConfig(level="none"), stream=io.StringIO()),
    )
    transport.start()
    transport.stop()


# Both rows are the DNS-rebinding shape: a browser page on another origin must
# not be able to drive this server even though the request lands on loopback.
# The two headers carry it differently -- a rebound hostname arrives as `Host`
# (127.0.0.1 is what the socket saw, evil.example.com is what the browser
# believes it is talking to), a cross-origin page as `Origin` -- so each gets
# its own refusal message, which is what the row pins.
@pytest.mark.parametrize(
    "headers, message",
    [
        pytest.param(
            {"Origin": "https://evil.example.com"}, "Origin not allowed", id="cross-origin"
        ),
        pytest.param(
            {"Host": "evil.example.com"}, "Host header not allowed", id="non-loopback-host"
        ),
    ],
)
def test_a_rebinding_header_is_rejected(make_server, headers, message):
    server = make_server()
    status, body = server.rpc("initialize", headers=headers)
    assert status == 403
    assert body["error"]["message"] == message


@pytest.mark.parametrize(
    "headers, message",
    [
        pytest.param(
            {"Origin": "https://evil.example.com"}, "Origin not allowed", id="cross-origin"
        ),
        pytest.param(
            {"Host": "evil.example.com"}, "Host header not allowed", id="non-loopback-host"
        ),
    ],
)
def test_a_refused_request_still_gets_its_reason_with_a_large_body(make_server, headers, message):
    """The refusal has to reach the client, not just be sent.

    Host and Origin are checked before the body is read, so the handler used to
    return with the body still sitting in the socket. Closing a socket holding
    unread bytes sends an RST instead of a FIN, and on Windows the client then
    raises ConnectionAbortedError (WinError 10053) *instead of* reading the 403 --
    so the one thing this response exists to say was the thing that got lost.

    The 512 KB body is what makes this deterministic rather than load-dependent.
    It exceeds the socket buffers, so the client is guaranteed to still be
    writing when the server decides to refuse; the plain-sized case above only
    reproduced under CPU contention, roughly one run in six.
    """
    server = make_server()
    status, body = server.rpc("initialize", params={"pad": "x" * 512_000}, headers=headers)
    assert status == 403
    assert body["error"]["message"] == message


def test_a_loopback_request_with_a_same_origin_origin_header_is_accepted(make_server):
    server = make_server()
    status, body = server.rpc("initialize", headers={"Origin": f"http://127.0.0.1:{server.port}"})
    assert status == 200
    assert body["result"]["serverInfo"]["name"] == "repo2graph"


def test_a_rejected_host_never_reaches_the_tool(make_server):
    """The check runs before auth and before dispatch: nothing is audited."""
    server = make_server()
    status, _ = server.rpc("initialize", headers={"Host": "evil.example.com"})
    assert status == 403
    assert server.audit_lines() == []


def test_an_oversized_body_is_refused(make_server):
    server = make_server()
    request = urllib.request.Request(server.url(), data=b"x" * 10, method="POST")
    request.add_header("Content-Length", str(1 << 30))
    try:
        urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as exc:
        assert exc.code == 413
    except (urllib.error.URLError, OSError):
        pass  # the server may close the connection first; also fine


def test_the_transport_runs_on_a_daemon_thread(make_server):
    """It must never hold the process open or block the stdio loop."""
    make_server()
    names = [t.name for t in threading.enumerate()]
    assert "repo2graph-http" in names
    thread = next(t for t in threading.enumerate() if t.name == "repo2graph-http")
    assert thread.daemon is True


def test_the_token_ceiling_still_holds_over_http(make_server, index):
    """The 12k cap is a transport-independent promise."""
    from repo2graph.mcp import MCP_MAX_BUDGET_TOKENS
    from repo2graph.query import count_tokens

    server = make_server()
    _status, body = server.call(
        "repo_search", {"query": MINI_QUERY, "k": 50, "budget_tokens": 10**9}
    )
    text = body["result"]["content"][0]["text"]
    assert count_tokens(text) <= MCP_MAX_BUDGET_TOKENS


def test_exclude_secrets_still_holds_over_http(make_server):
    """The other unconditional promise, re-checked at this surface."""
    from conftest import SECRET_QUERY

    server = make_server()
    _status, body = server.call("repo_search", {"query": SECRET_QUERY})
    text = body["result"]["content"][0]["text"]
    assert "abc123deadbeef" not in text
    assert "ACME_DEPLOYMENT_LEDGER_TOKEN" not in text


# --------------------------------------------------------- env var token ----


def _auth_args(**over):
    """The `serve` namespace `_auth_config` reads, with auth switched off.

    Spelled once because every field but the one under test has to be present
    and `None` for the call to mean anything, and a reader should not have to
    diff five identical lines to find which one a test varies.
    """
    fields = {
        "auth_token": None,
        "auth_oidc_issuer": None,
        "auth_audience": None,
        "auth_jwks_ttl": 300.0,
        "auth_cimd": False,
    }
    fields.update(over)
    return argparse.Namespace(**fields)


@pytest.mark.parametrize(
    "flag, expected",
    [
        pytest.param("from-flag", "from-flag", id="flag-wins-over-env"),
        pytest.param(None, "from-env", id="env-is-the-fallback"),
    ],
)
def test_auth_config_precedence_between_the_flag_and_the_env_var(monkeypatch, flag, expected):
    monkeypatch.setenv("R2G_AUTH_TOKEN", "from-env")
    assert _auth_config(_auth_args(auth_token=flag)).token == expected


def test_the_env_var_token_authenticates_a_real_http_call(make_server, monkeypatch):
    monkeypatch.setenv("R2G_AUTH_TOKEN", "env-secret")
    args = argparse.Namespace(
        auth_token=None,
        auth_oidc_issuer=None,
        auth_audience=None,
        auth_jwks_ttl=300.0,
        auth_cimd=False,
    )
    server = make_server(auth_config=_auth_config(args))
    status, _ = server.call("repo_map", token="env-secret")
    assert status == 200
    status, _ = server.call("repo_map", token="wrong")
    assert status == 401


# -------------------------------------------------------------- network ----


def test_the_server_opens_no_outbound_socket_without_oidc(make_server, monkeypatch):
    """The standing constraint: no network unless an auth issuer is configured."""
    real_connect = socket.socket.connect

    def guard(self, address):
        host = address[0] if isinstance(address, tuple) else ""
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"outbound connection attempted to {address}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guard)
    server = make_server()
    assert server.call("repo_map")[0] == 200
    assert server.call("repo_search", {"query": MINI_QUERY})[0] == 200
    assert server.get("/.well-known/mcp-server-metadata")[0] == 200


def test_server_metadata_is_pure_and_needs_no_server():
    """The document builder is callable without starting anything."""
    doc = server_metadata("/tmp/repo", True, ["bearer"], "2026-09-16T00:00:00Z")
    assert doc["repo"] == "repo"
    assert "/tmp/repo" not in json.dumps(doc)
    assert doc["index_present"] is True
    assert doc["auth_modes"] == ["bearer"]
    json.dumps(doc)


def test_server_metadata_never_includes_an_absolute_repo_path():
    """ISS-198: unauthenticated discovery used to return str(repo) verbatim."""
    from pathlib import Path

    abs_repo = Path("/srv/repos/acme-billing-service")
    doc = server_metadata(abs_repo, True, ["bearer"], "2026-09-16T00:00:00Z")
    raw = json.dumps(doc)
    assert str(abs_repo) not in raw
    assert doc["repo"] == "acme-billing-service"
    assert not str(doc["repo"]).startswith("/")
    assert doc["index_present"] is True
    assert doc["auth_modes"] == ["bearer"]

    win = server_metadata(r"C:\srv\repos\acme-billing-service", False, ["none"])
    assert win["repo"] == "acme-billing-service"
    assert r"C:\srv" not in json.dumps(win)
    assert win["index_present"] is False
    assert win["auth_modes"] == ["none"]


def test_iss152_head_healthz(make_server):
    """Issue 152: HEAD requests return headers without response body."""
    server = make_server()
    req = urllib.request.Request(server.url("/healthz"), method="HEAD")
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.status == 200
        assert resp.headers.get("Content-Type") == "application/json"
        assert int(resp.headers.get("Content-Length") or 0) > 0
        body = resp.read()
        assert len(body) == 0

    req_404 = urllib.request.Request(server.url("/nonexistent"), method="HEAD")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_404, timeout=10)
    assert exc_info.value.code == 404
    assert len(exc_info.value.read()) == 0


def test_iss152_options_cors(make_server):
    """Issue 152: OPTIONS preflight requests return CORS headers."""
    server = make_server()

    # Allowed loopback origin
    req = urllib.request.Request(
        server.url("/mcp"),
        method="OPTIONS",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.status == 204
        assert resp.headers.get("Access-Control-Allow-Origin") == "http://localhost:3000"
        allow_methods = resp.headers.get("Access-Control-Allow-Methods")
        assert "POST" in allow_methods
        assert "OPTIONS" in allow_methods
        assert "Authorization" in resp.headers.get("Access-Control-Allow-Headers", "")

    # Disallowed origin -> 403 Forbidden
    req_bad = urllib.request.Request(
        server.url("/mcp"),
        method="OPTIONS",
        headers={"Origin": "http://evil.com"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_bad, timeout=10)
    assert exc_info.value.code == 403


def test_iss84_internal_500_error_is_sanitized(make_server, monkeypatch):
    """Issue 84: HTTP 500 responses do not leak internal exception details to network callers."""
    server = make_server()

    # 1. Exception during tool execution (handled in line 524)
    from repo2graph import mcp

    def exploding_tool(*args, **kwargs):
        raise RuntimeError("database secret /path/to/private/key exploded")

    monkeypatch.setattr(mcp, "tool_repo_map", exploding_tool)

    status, body = server.call("repo_map")
    assert status == 500
    assert body["error"]["code"] == -32603
    assert body["error"]["message"] == "Internal server error"
    assert "private" not in json.dumps(body)

    # The audit logger records the internal error message for operators
    audit = server.audit_lines()
    assert any("database secret" in line.get("error", "") for line in audit)

    # 2. Exception during dispatch (handled in line 396)
    from repo2graph.http_server import MCPRequestHandler

    def exploding_dispatch(*args, **kwargs):
        raise ValueError("unhandled internal crash at /etc/passwd")

    monkeypatch.setattr(MCPRequestHandler, "_dispatch", exploding_dispatch)
    status2, body2 = server.rpc("any_method")
    assert status2 == 500
    assert body2["error"]["code"] == -32603
    assert body2["error"]["message"] == "Internal server error"
    assert "/etc/passwd" not in json.dumps(body2)


def test_crlf_injection_in_cors_headers_is_sanitized(make_server):
    """CodeQL alerts #8/#9: CRLF in Origin and Access-Control-Request-Headers
    must be stripped so an attacker cannot inject arbitrary response headers.

    Python's http.client rejects CRLF in headers on the *client* side, so we
    must use a raw socket to actually deliver the malicious header to the server.
    """
    import socket

    server = make_server()
    # Parse host/port from server URL
    from urllib.parse import urlsplit

    parsed = urlsplit(server.url("/mcp"))
    host, port = parsed.hostname, parsed.port

    # Build a raw HTTP OPTIONS request with CRLF injected into
    # Access-Control-Request-Headers
    raw_request = (
        f"OPTIONS /mcp HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Origin: http://localhost:3000\r\n"
        f"Access-Control-Request-Headers: Authorization\r\nX-Injected: evil\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    )

    sock = socket.create_connection((host, port), timeout=10)
    try:
        sock.sendall(raw_request.encode("ascii"))
        response_bytes = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response_bytes += chunk
    finally:
        sock.close()

    response_text = response_bytes.decode("ascii", errors="replace")
    # The server must not have reflected X-Injected as a real header
    # Split response into lines and verify no line starts with "X-Injected:"
    lines = response_text.split("\r\n")
    for line in lines:
        assert not line.startswith("X-Injected:"), (
            f"CRLF injection succeeded: server reflected injected header: {line!r}"
        )


# ----------------------------------------------------- transport framing ----
#
# The tests below drive the socket directly. Everything above speaks through
# urllib, which is a well-behaved client by construction -- it always sends the
# body it announced, never sends a header containing CRLF, and hangs up only
# when it is finished. Each bug here is a *misbehaving* client, so none of them
# is reachable through a client that refuses to misbehave.


def raw_exchange(server, request_bytes, timeout=20):
    """Send raw bytes at the server and read the whole response back.

    Returned as latin-1 so the bytes survive byte-for-byte: these tests inspect
    header framing, and a decode that repairs anything would repair exactly the
    damage they are looking for.
    """
    sock = socket.create_connection(("127.0.0.1", server.port), timeout=timeout)
    try:
        sock.sendall(request_bytes)
        chunks = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        sock.close()
    return b"".join(chunks).decode("latin-1")


def head_and_body(response):
    """Split a raw response into its header lines and its body."""
    head, _, body = response.partition("\r\n\r\n")
    return head.split("\r\n"), body


def status_of(response):
    """The numeric status of a raw response, as an int."""
    return int(response.split("\r\n", 1)[0].split(" ")[1])


def post_headers(server, extra="", version="HTTP/1.0"):
    return (
        f"POST /mcp {version}\r\n"
        f"Host: 127.0.0.1:{server.port}\r\n"
        f"Content-Type: application/json\r\n"
        f"{extra}"
        f"\r\n"
    ).encode("ascii")


def test_iss197_a_withheld_request_body_is_answered_rather_than_waited_on(make_server, monkeypatch):
    """Issue 197: headers promising a megabyte, then silence.

    Without a handler `timeout`, socketserver never arms settimeout() and
    `rfile.read(Content-Length)` blocks for as long as the client cares to hold
    the socket -- one pinned thread per connection, on a server that caps
    neither. The shipped 30s is far too long to sit through here, so the test
    pins the attribute's existence separately and then shrinks it to prove the
    second half: that the read failing becomes a 408 rather than a handler
    thread dying with the client still waiting.
    """
    from repo2graph.http_server import MCPRequestHandler

    assert MCPRequestHandler.timeout is not None, "socketserver only arms settimeout() when set"
    assert 0 < MCPRequestHandler.timeout <= 60

    server = make_server()
    monkeypatch.setattr(server.transport._handler, "timeout", 1.0)

    started = time.monotonic()
    response = raw_exchange(server, post_headers(server, "Content-Length: 1000000\r\n"))
    elapsed = time.monotonic() - started

    assert elapsed < 15, "the server sat on a half-sent request instead of timing it out"
    assert status_of(response) == 408

    # The thread came back: the server still serves the next caller.
    assert server.rpc("initialize")[0] == 200


def test_iss197_a_body_shorter_than_content_length_is_408_not_a_hang(make_server, monkeypatch):
    """The polite version of the same thing: the client closes early.

    `rfile.read(length)` returns short at EOF rather than raising, so this path
    never reaches the timeout at all -- it has to be noticed by comparing what
    arrived against what was promised.
    """
    server = make_server()
    monkeypatch.setattr(server.transport._handler, "timeout", 1.0)

    sock = socket.create_connection(("127.0.0.1", server.port), timeout=20)
    try:
        sock.sendall(post_headers(server, "Content-Length: 500\r\n") + b'{"jsonrpc":')
        sock.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        sock.close()

    assert status_of(b"".join(chunks).decode("latin-1")) == 408


class AbortingWriter:
    """A `wfile` that raises ConnectionAbortedError from the nth write on.

    The real shape is a client that hangs up mid-response, which Windows
    reports as ConnectionAbortedError -- the sibling of BrokenPipeError and
    ConnectionResetError that the old guard did not name. Forcing it beats
    racing a real socket close, which lands on a different write every run.

    Write 1 is the header flush from `end_headers()`; write 2 is the body.
    """

    def __init__(self, inner, fail_from):
        self._inner = inner
        self._fail_from = fail_from
        self._writes = 0

    def write(self, data):
        self._writes += 1
        if self._writes >= self._fail_from:
            raise ConnectionAbortedError(10053, "simulated client abort")
        return self._inner.write(data)

    def flush(self):
        return None

    @property
    def closed(self):
        return self._inner.closed

    def close(self):
        return self._inner.close()


def abort_writes_from(monkeypatch, server, fail_from):
    handler_cls = server.transport._handler
    base_setup = handler_cls.setup

    def setup(self):
        base_setup(self)
        self.wfile = AbortingWriter(self.wfile, fail_from)

    monkeypatch.setattr(handler_cls, "setup", setup)


@pytest.mark.parametrize(
    "fail_from,what",
    [(1, "the end_headers() flush"), (2, "the body write")],
)
def test_iss199_a_disconnect_during_a_response_reaches_no_error_reporter(
    make_server, monkeypatch, capsys, fail_from, what
):
    """Issue 199: a client that hangs up mid-response is not an error.

    `_send_json` guarded only the body write and only two of ConnectionError's
    three subclasses, so a disconnect at `end_headers()` (every platform) or a
    ConnectionAbortedError at the body write (Windows) escaped into the
    server's error reporter. Either way the operator gets a record for
    something that is not a fault, and before this package owned that reporter
    the record was a multi-line traceback in a stream promised to be JSON-lines.
    """
    server = make_server()
    abort_writes_from(monkeypatch, server, fail_from)
    capsys.readouterr()

    # Reading to EOF is the synchronisation point: socketserver closes the
    # connection only after its error hook has run, so whatever stderr holds
    # once recv() returns empty is the complete record for this request.
    frame = b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
    raw_exchange(server, post_headers(server, f"Content-Length: {len(frame)}\r\n") + frame)
    err = capsys.readouterr().err

    assert "Traceback (most recent call last)" not in err, f"a disconnect at {what} raised"
    assert "http_handler_error" not in err, f"a disconnect at {what} escaped _send_json"

    monkeypatch.undo()
    assert server.rpc("initialize")[0] == 200


def test_iss199_an_escaping_handler_error_is_one_json_line(make_server, monkeypatch, capsys):
    """Issue 199, second half: whatever does escape stays machine-readable.

    socketserver's default `handle_error` prints a dashed banner plus a full
    traceback to stderr, which `events.py` promises is one JSON object per
    line. A SIEM parsing it line by line gets a dozen lines that parse as none,
    at exactly the moment the record matters most.
    """
    server = make_server()

    def exploding_get(self, send_body=True):
        raise RuntimeError("boom")

    monkeypatch.setattr(server.transport._handler, "do_GET", exploding_get)
    capsys.readouterr()

    raw_exchange(
        server,
        f"GET /healthz HTTP/1.0\r\nHost: 127.0.0.1:{server.port}\r\n\r\n".encode("ascii"),
    )
    err = capsys.readouterr().err

    assert "Traceback (most recent call last)" not in err
    lines = [line for line in err.split("\n") if line.strip()]
    assert len(lines) == 1, f"expected exactly one stderr line, got {lines!r}"
    record = json.loads(lines[0])
    assert record["event"] == "http_handler_error"
    assert "RuntimeError" in record["error"]


def test_iss233_a_deeply_nested_json_body_is_a_clean_400(make_server):
    """Issue 233: `json.loads` raises RecursionError, which is not a ValueError.

    ~40 KB of nesting is well inside MAX_BODY_BYTES, and this runs before
    `authenticate()`, so any caller that can reach POST /mcp could kill a
    handler thread outright on a server that is otherwise fully locked down.
    """
    server = make_server()
    depth = 20_000
    body = b"[" * depth + b"]" * depth
    assert len(body) < 1 << 20, "the point is that the size cap does not stand in the way"

    request = urllib.request.Request(
        server.url(), data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(request, timeout=20)
        raise AssertionError("expected a 400")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400
        assert json.loads(exc.read())["error"]["code"] == -32700

    # The handler thread survived: a fresh connection is still served.
    assert server.rpc("initialize")[0] == 200


def test_iss243_a_crlf_in_the_oidc_issuer_cannot_inject_a_response_header(make_server):
    """Issue 243: `_challenge()` interpolates oidc_issuer into WWW-Authenticate.

    Operator-supplied, so not remotely exploitable -- but the module states a
    rule for header values and this path did not follow it. The fix is
    structural: `_send_json` sanitises every extra header, so the rule holds
    for call sites nobody has written yet.
    """
    poisoned = "https://issuer.example.com/\r\nX-Injected: evil"
    server = make_server(AuthConfig(oidc_issuer=poisoned), opener=FakeIssuer())

    body = b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
    request = post_headers(server, f"Content-Length: {len(body)}\r\n") + body
    response = raw_exchange(server, request)
    lines, _body = head_and_body(response)

    assert status_of(response) == 401
    challenges = [line for line in lines if line.startswith("WWW-Authenticate:")]
    assert len(challenges) == 1, f"no challenge to sanitise: {lines!r}"
    # Flattened into the one header rather than split into two: the value is
    # still reported, it just cannot be a header of its own any more.
    assert "X-Injected: evil" in challenges[0]
    for line in lines:
        assert not line.startswith("X-Injected:"), f"response splitting succeeded: {line!r}"


def test_iss249_a_chunked_request_body_is_refused_with_411(make_server):
    """Issue 249: Content-Length is the only framing `_read_body` understands.

    A chunked body measured as zero bytes was never read, so a spec-legal
    client got "expected a JSON-RPC object" and a socket still holding its
    request. Refusing the encoding by name says what is actually wrong.
    """
    server = make_server()
    frame = b'{"jsonrpc":"2.0","id":1,"method":"initialize"}'
    request = (
        post_headers(server, "Transfer-Encoding: chunked\r\n", version="HTTP/1.1")
        + f"{len(frame):x}\r\n".encode("ascii")
        + frame
        + b"\r\n0\r\n\r\n"
    )
    response = raw_exchange(server, request)
    lines, body = head_and_body(response)

    assert status_of(response) == 411
    assert "chunked" in json.loads(body)["error"]["message"]
    # The precondition that keeps protocol_version a free choice.
    assert any(line.lower().startswith("content-length:") for line in lines)


def test_iss249_the_handler_pins_its_protocol_version_deliberately():
    """Issue 249: HTTP/1.0 here is a decision, not an inheritance.

    It is also the base class's default, so only the presence of the attribute
    on this class distinguishes "chosen" from "never thought about" -- and the
    choice is what keeps an unread request body (a refused Transfer-Encoding, a
    413'd Content-Length, a read that timed out part-way) from being left in a
    socket that the next request would be parsed out of.
    """
    from repo2graph.http_server import MCPRequestHandler

    assert "protocol_version" in MCPRequestHandler.__dict__
    assert MCPRequestHandler.protocol_version == "HTTP/1.0"


def test_missing_index_without_repo_returns_503_actionable_error(tmp_path):
    """When no index exists and auto-build is off, HTTP mode returns 503 with instructions (#265)."""
    empty_idx = tmp_path / "absent_index"
    transport = HTTPTransport(empty_idx, repo=None, host="127.0.0.1", port=0)
    transport.start()
    try:
        url = f"http://127.0.0.1:{transport.port}/mcp"
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "repo_map", "arguments": {}},
            }
        ).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            status = exc.code
            payload = json.loads(exc.read())
        assert status == 503
        assert "error" in payload
        assert "Build one first with: repo2graph build" in payload["error"]["message"]
    finally:
        transport.stop()


# ------------------------------------------------- host path disclosure ----


def test_a_missing_index_does_not_tell_the_caller_where_it_looked(tmp_path):
    """503 must not carry the host's filesystem layout.

    `open_index` raises SystemExit with a message written for an operator at a
    terminal, naming the absolute index directory twice. Relaying str(exc) put
    that in a JSON-RPC error -- and therefore into the context window of any
    agent driving this server -- which is the disclosure `_public_repo_label`
    already refuses one endpoint over. The operator's detail belongs in the
    audit log, which is server-side.
    """
    from repo2graph.http_server import INDEX_UNAVAILABLE

    missing = tmp_path / "host-layout" / "no_index_here"
    missing.mkdir(parents=True)
    stream = io.StringIO()
    transport = HTTPTransport(
        missing,
        None,
        host="127.0.0.1",
        port=0,
        audit=AuditLogger(AuditConfig(level="all"), stream=stream),
    )
    transport.start()
    try:
        server = Server(transport, stream)
        status, body = server.call("repo_map")
    finally:
        transport.stop()

    assert status == 503
    message = body["error"]["message"]
    assert message == INDEX_UNAVAILABLE
    # Neither the full path nor either distinctive segment of it.
    assert str(missing) not in message
    assert "host-layout" not in message
    assert "no_index_here" not in message

    # The operator still gets the detail, server-side.
    audited = [r for r in server.audit_lines() if r.get("outcome") == "error"]
    assert len(audited) == 1
    assert "no_index_here" in audited[0]["error"]
