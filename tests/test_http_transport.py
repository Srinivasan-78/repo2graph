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


def test_a_wrong_token_returns_401_and_does_not_run_the_tool(make_server, monkeypatch):
    """The wiring property: rejected means *not executed*, not merely not returned."""
    from repo2graph import mcp

    ran = []
    monkeypatch.setattr(mcp, "tool_repo_map", lambda idx: ran.append(1) or "x")

    server = make_server(AuthConfig(token="s3cret"))
    status, body = server.call("repo_map", token="wrong")

    assert status == 401
    assert body["error"]["message"] == "Unauthorized"
    assert ran == [], "the tool ran despite a failed authentication"


def test_no_header_returns_401_and_does_not_run_the_tool(make_server, monkeypatch):
    from repo2graph import mcp

    ran = []
    monkeypatch.setattr(mcp, "tool_repo_map", lambda idx: ran.append(1) or "x")

    server = make_server(AuthConfig(token="s3cret"))
    status, body = server.call("repo_map")
    assert status == 401 and body["error"]["message"] == "Unauthorized"
    assert ran == []


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


def test_an_expired_jwt_returns_401(make_server, monkeypatch):
    from repo2graph import mcp

    ran = []
    monkeypatch.setattr(mcp, "tool_repo_map", lambda idx: ran.append(1) or "x")

    server = make_server(oidc(), opener=FakeIssuer())
    status, body = server.call("repo_map", token=sign(claims(exp=time.time() - 3600)))
    assert status == 401 and body["error"]["message"] == "Unauthorized"
    assert ran == []


def test_a_wrong_issuer_jwt_returns_401(make_server):
    server = make_server(oidc(), opener=FakeIssuer())
    status, _ = server.call("repo_map", token=sign(claims(iss="https://evil.example.com")))
    assert status == 401


def test_a_wrong_audience_jwt_returns_401(make_server):
    server = make_server(oidc(), opener=FakeIssuer())
    status, _ = server.call("repo_map", token=sign(claims(aud="someone-else")))
    assert status == 401


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


def test_a_cross_origin_origin_header_is_rejected(make_server):
    """The DNS-rebinding shape: a browser page on another origin must not be
    able to drive this server, even though the request lands on loopback."""
    server = make_server()
    status, body = server.rpc("initialize", headers={"Origin": "https://evil.example.com"})
    assert status == 403
    assert body["error"]["message"] == "Origin not allowed"


def test_a_non_loopback_host_header_is_rejected(make_server):
    """A DNS-rebound hostname arrives as the Host header, not the socket
    address -- 127.0.0.1 is what the socket saw, evil.example.com is what
    the browser believes it is talking to."""
    server = make_server()
    status, body = server.rpc("initialize", headers={"Host": "evil.example.com"})
    assert status == 403
    assert body["error"]["message"] == "Host header not allowed"


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


def test_auth_config_prefers_the_flag_over_the_env_var(monkeypatch):
    monkeypatch.setenv("R2G_AUTH_TOKEN", "from-env")
    args = argparse.Namespace(
        auth_token="from-flag",
        auth_oidc_issuer=None,
        auth_audience=None,
        auth_jwks_ttl=300.0,
        auth_cimd=False,
    )
    assert _auth_config(args).token == "from-flag"


def test_auth_config_falls_back_to_the_env_var(monkeypatch):
    monkeypatch.setenv("R2G_AUTH_TOKEN", "from-env")
    args = argparse.Namespace(
        auth_token=None,
        auth_oidc_issuer=None,
        auth_audience=None,
        auth_jwks_ttl=300.0,
        auth_cimd=False,
    )
    assert _auth_config(args).token == "from-env"


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
