"""Round-3 HTTP hardening: #367, #372, #293, #264, #267, #390.

Reuses `Server` (the thin real-socket JSON-RPC client) from
`test_http_transport.py`, but defines its own `index`/`make_server`
fixtures rather than importing those by name -- importing a fixture and then
using it as a test's parameter name (the pattern the other file's own tests
rely on) makes every such test read as ruff `F811` (`make_server` "redefined"
by the parameter that is exactly how pytest is meant to receive it). Same
transport, same defaults, no shadowing.

Every test here pins a literal value hand-derived from the fixture/config
under test, per `AGENTS.md`: never a value computed by the code under test.
"""

import io
import json
import socket
import threading
import urllib.error
import urllib.request

import pytest

from conftest import build_mini_index, write_mini_repo
from repo2graph import auth as auth_mod
from repo2graph.audit import AuditConfig, AuditLogger
from repo2graph.auth import AuthConfig
from repo2graph.http_server import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    MCP_PROTOCOL_VERSION,
    OVERLOADED_CODE,
    PARSE_ERROR,
    RATE_LIMITED_CODE,
    UNSUPPORTED_MEDIA_TYPE,
    HTTPTransport,
    RateLimitConfig,
)

from test_auth import AUDIENCE, ISSUER, FakeIssuer, claims, sign
from test_http_transport import Server


@pytest.fixture
def index(tmp_path):
    return build_mini_index(write_mini_repo(tmp_path), tmp_path / "idx")


@pytest.fixture
def make_server(index, tmp_path):
    """Factory starting a transport on an ephemeral port; stopped on teardown.

    Mirrors `test_http_transport.py`'s fixture of the same name/behaviour,
    plus `**extra` so a test here can pass the round-3 kwargs
    (`rate_limit_config`, `trust_proxy`, `trusted_proxies`, ...) that
    fixture predates.
    """
    started = []

    def _make(
        auth_config=None,
        opener=None,
        cache=None,
        publish_cimd=False,
        level="all",
        audit_path=None,
        repo=None,
        **extra,
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
            **extra,
        )
        transport.start()
        server = Server(transport, stream)
        started.append(transport)
        return server

    yield _make
    for transport in started:
        transport.stop()


# --------------------------------------------------------------------- #390


def test_protocol_version_matches_what_this_transport_implements(make_server):
    """#390: advertise the revision whose semantics are actually served.

    Pinned as a literal, not compared against the module constant the
    implementation itself uses -- `AGENTS.md`'s "never compare the
    implementation to itself" rule. This transport is plain JSON-RPC
    request/response over POST (Content-Length framing, HTTP/1.0,
    connection-close, no SSE, no Mcp-Session-Id): `2024-11-05` is the
    revision that describes, not `2025-06-18` (Streamable HTTP).
    """
    server = make_server()
    _status, body = server.rpc("initialize")
    assert body["result"]["protocolVersion"] == "2024-11-05"
    assert MCP_PROTOCOL_VERSION == "2024-11-05"


# --------------------------------------------------------------------- #367


def test_rsa_verify_weak_key_and_bad_signature_are_indistinguishable():
    """#367: a caller must not be able to tell "weak key" from "bad signature".

    Both fail the *same* way inside `decode_jwt` -- a plain `AuthError("token
    signature is invalid")` -- because `rsa_verify` returns False for either
    rather than raising something distinct. Asserted directly against
    `rsa_verify`, matching the acceptance criteria in #367.
    """
    from repo2graph.auth import rsa_verify

    assert auth_mod.MIN_RSA_BITS == 2048
    assert auth_mod.MAX_RSA_EXPONENT_BITS == 64

    # A trivially small (512-bit-ish) modulus: below the floor regardless of
    # whether the arithmetic would otherwise have verified.
    weak_n = (1 << 511) | 1
    assert weak_n.bit_length() < auth_mod.MIN_RSA_BITS
    assert rsa_verify(weak_n, 65537, b"\x00" * 64, b"msg", "sha256") is False

    # A real 2048-bit-class modulus (from test_auth's fixture key) but an
    # exponent well past the 64-bit ceiling.
    from test_auth import KEY

    huge_e = 1 << 70
    assert huge_e.bit_length() > auth_mod.MAX_RSA_EXPONENT_BITS
    assert rsa_verify(KEY["n"], huge_e, b"\x00" * 256, b"msg", "sha256") is False


# --------------------------------------------------------------------- #372


def test_get_and_head_reject_a_rebound_host_the_same_way_post_does(make_server):
    """#372: GET/HEAD get the same Host/Origin gate POST already had.

    `test_http_transport.py` already pins the individual GET/HEAD/healthz
    rows; this is the cross-check that the three verbs agree on one request.
    """
    server = make_server()
    status_get, body_get = server.get(
        "/.well-known/mcp-server-metadata", headers={"Host": "evil.example.com"}
    )
    assert status_get == 403
    assert body_get["error"]["message"] == "Host header not allowed"

    req = urllib.request.Request(
        server.url("/.well-known/mcp-server-metadata"),
        method="HEAD",
        headers={"Host": "evil.example.com"},
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        raise AssertionError("expected a 403")
    except urllib.error.HTTPError as exc:
        assert exc.code == 403


# --------------------------------------------------------------------- #293


def _post_raw(server, body: bytes, headers=None):
    request = urllib.request.Request(
        server.url(),
        data=body,
        method="POST",
        headers=headers or {"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_jsonrpc_version_must_be_exactly_2_0(make_server):
    server = make_server()
    status, raw = _post_raw(server, b'{"jsonrpc":"1.0","id":1,"method":"initialize"}')
    body = json.loads(raw)
    assert status == 400
    assert body["error"]["code"] == INVALID_REQUEST
    assert "jsonrpc" in body["error"]["message"]


def test_method_must_be_a_non_empty_string(make_server):
    server = make_server()
    status, raw = _post_raw(server, b'{"jsonrpc":"2.0","id":1,"method":5}')
    body = json.loads(raw)
    assert status == 400
    assert body["error"]["code"] == INVALID_REQUEST


@pytest.mark.parametrize(
    "id_literal",
    [
        pytest.param("{}", id="object"),
        pytest.param("[1]", id="array"),
        pytest.param("true", id="bool"),
    ],
)
def test_id_must_be_string_number_or_null(make_server, id_literal):
    server = make_server()
    frame = ('{"jsonrpc":"2.0","id":%s,"method":"initialize"}' % id_literal).encode()
    status, raw = _post_raw(server, frame)
    body = json.loads(raw)
    assert status == 400
    assert body["error"]["code"] == INVALID_REQUEST


def test_id_numeric_range_is_bounded(make_server):
    server = make_server()
    huge = 1 << 60
    frame = ('{"jsonrpc":"2.0","id":%d,"method":"initialize"}' % huge).encode()
    status, raw = _post_raw(server, frame)
    body = json.loads(raw)
    assert status == 400
    assert body["error"]["code"] == INVALID_REQUEST


def test_params_must_be_an_object(make_server):
    server = make_server()
    status, raw = _post_raw(
        server, b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":[1,2]}'
    )
    body = json.loads(raw)
    assert status == 400
    assert body["error"]["code"] == INVALID_PARAMS


def test_tools_call_params_name_and_arguments_are_typed(make_server):
    server = make_server()
    status, body = server.rpc("tools/call", {"name": 5, "arguments": {}})
    assert status == 400 and body["error"]["code"] == INVALID_PARAMS

    status, body = server.rpc("tools/call", {"name": "repo_map", "arguments": [1, 2]})
    assert status == 400 and body["error"]["code"] == INVALID_PARAMS


def test_duplicate_top_level_keys_are_refused(make_server):
    server = make_server()
    status, raw = _post_raw(server, b'{"jsonrpc":"2.0","id":1,"id":2,"method":"initialize"}')
    body = json.loads(raw)
    assert status == 400
    assert body["error"]["code"] == PARSE_ERROR


def test_nonfinite_numeric_constants_are_refused(make_server):
    server = make_server()
    status, raw = _post_raw(server, b'{"jsonrpc":"2.0","id":NaN,"method":"initialize"}')
    body = json.loads(raw)
    assert status == 400
    assert body["error"]["code"] == PARSE_ERROR


def test_nesting_past_the_depth_ceiling_is_a_clean_parse_error(make_server):
    """#293: a body that fits comfortably inside MAX_BODY_BYTES but nests
    past MAX_JSON_DEPTH (32) is refused before json.loads ever runs, not
    merely before it overflows the interpreter stack (that shape is already
    covered by test_http_transport's ISS-233 test at depth 20,000)."""
    server = make_server()
    body = b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":' + b"[" * 40 + b"]" * 40 + b"}"
    status, raw = _post_raw(server, body)
    result = json.loads(raw)
    assert status == 400
    assert result["error"]["code"] == PARSE_ERROR

    # The handler thread survived: a fresh connection is still served.
    assert server.rpc("initialize")[0] == 200


def test_invalid_utf8_body_is_a_clean_parse_error_not_a_crash(make_server):
    server = make_server()
    status, raw = _post_raw(server, b"\xff\xfe\x00\x01not utf8 at all\x80\x81")
    body = json.loads(raw)
    assert status == 400
    assert body["error"]["code"] == PARSE_ERROR

    assert server.rpc("initialize")[0] == 200


def test_chunked_transfer_encoding_is_refused_cleanly(make_server):
    """The #249 behaviour this issue's acceptance criteria also names:
    chunked bodies are refused with a structured error, not a hang or a
    desynced connection. `test_http_transport.py`'s
    `test_iss249_a_chunked_request_body_is_refused_with_411` already proves
    this at the wire level; this is the same property via urllib."""
    sock = socket.create_connection(("127.0.0.1", make_server().port), timeout=10)
    request = (
        b"POST /mcp HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Transfer-Encoding: chunked\r\n"
        b"Content-Type: application/json\r\n"
        b"Connection: close\r\n\r\n"
        b"10\r\n"
        b'{"jsonrpc":"2.0"}\r\n'
        b"0\r\n\r\n"
    )
    try:
        sock.sendall(request)
        chunks = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        sock.close()
    response = b"".join(chunks).decode("latin-1")
    status_line = response.split("\r\n", 1)[0]
    assert " 411 " in status_line


_INITIALIZE_FRAME = b'{"jsonrpc":"2.0","id":1,"method":"initialize"}'


def test_a_correct_content_type_is_accepted(make_server):
    server = make_server()
    status, raw = _post_raw(server, _INITIALIZE_FRAME, headers={"Content-Type": "application/json"})
    body = json.loads(raw)
    assert status == 200
    assert body["result"]["protocolVersion"] == MCP_PROTOCOL_VERSION


def test_a_content_type_with_charset_parameter_is_accepted(make_server):
    """`application/json; charset=utf-8` is the same media type, just with an
    optional parameter a well-behaved client may add -- only the part before
    `;` is the media type per RFC 9110 8.3."""
    server = make_server()
    status, raw = _post_raw(
        server, _INITIALIZE_FRAME, headers={"Content-Type": "application/json; charset=utf-8"}
    )
    body = json.loads(raw)
    assert status == 200
    assert body["result"]["protocolVersion"] == MCP_PROTOCOL_VERSION


def test_form_urlencoded_content_type_is_refused(make_server):
    """`urllib.request.Request(data=<bytes>)` silently defaults to this media
    type when a caller sets no Content-Type at all -- the exact shape a
    misconfigured client produces, and the reason the check treats a missing
    header the same way (see `_content_type_ok`'s docstring)."""
    server = make_server()
    status, raw = _post_raw(
        server,
        _INITIALIZE_FRAME,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    body = json.loads(raw)
    assert status == UNSUPPORTED_MEDIA_TYPE
    assert body["error"]["code"] == INVALID_REQUEST
    assert "Content-Type" in body["error"]["message"]


def test_a_missing_content_type_is_refused(make_server):
    """No header at all is refused, not treated as an implicit application/json."""
    server = make_server()
    request = urllib.request.Request(server.url(), data=_INITIALIZE_FRAME, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    body = json.loads(raw)
    assert status == UNSUPPORTED_MEDIA_TYPE
    assert body["error"]["code"] == INVALID_REQUEST


def test_a_wrong_content_type_does_not_crash_the_process(make_server):
    """The refusal is a clean, structured error -- not a dropped connection or
    a dead handler thread. Proven the same way the #293 fuzz suite proves it:
    a fresh request on the same server still gets served afterward."""
    server = make_server()
    status, raw = _post_raw(server, _INITIALIZE_FRAME, headers={"Content-Type": "text/plain"})
    body = json.loads(raw)
    assert status == UNSUPPORTED_MEDIA_TYPE
    assert "Traceback" not in body["error"]["message"]

    # The handler thread survived: a fresh connection is still served.
    assert server.rpc("initialize")[0] == 200


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"not json", id="not-json"),
        pytest.param(b"[1,2,3]", id="json-array-not-object"),
        pytest.param(b'{"id":1,"method":"initialize"}', id="missing-jsonrpc"),
        pytest.param(b'{"jsonrpc":"2.0","id":1,"method":""}', id="empty-method"),
        pytest.param(
            b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":"x"}', id="params-string"
        ),
        pytest.param(b"\x00\x01\x02\xff\xfe" * 20, id="binary-garbage"),
        pytest.param(b'{"jsonrpc": "2.0", "id": 1, "method": "initialize"', id="truncated"),
        pytest.param(b'{"a":' * 500 + b"1" + b"}" * 500, id="deep-object-nesting"),
        pytest.param(
            b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":null}}', id="null-name"
        ),
    ],
)
def test_fuzzed_malformed_payloads_never_crash_the_process(make_server, body):
    """#293 acceptance: fuzzed malformed payloads must never crash the process.

    Every row gets a bounded, structured 4xx response and the server keeps
    serving afterward -- the actual property under test, since a crashed
    handler thread would otherwise be invisible until the next request fails.
    """
    server = make_server()
    status, raw = _post_raw(server, body)
    assert 400 <= status < 500, f"body {body!r} produced status {status}"
    parsed = json.loads(raw)
    assert "error" in parsed
    assert "code" in parsed["error"]
    # Never a stack trace.
    assert "Traceback" not in parsed["error"]["message"]
    assert "line " not in parsed["error"]["message"]

    assert server.rpc("initialize")[0] == 200


# --------------------------------------------------------------------- #264


def test_a_client_over_its_rate_limit_gets_429_with_retry_after(make_server, index):
    stream = io.StringIO()
    audit = AuditLogger(AuditConfig(level="all"), stream=stream)
    transport = HTTPTransport(
        index,
        None,
        host="127.0.0.1",
        port=0,
        audit=audit,
        rate_limit_config=RateLimitConfig(requests_per_window=2, window_seconds=60.0),
    )
    transport.start()
    try:
        server = Server(transport, stream)
        assert server.rpc("initialize")[0] == 200
        assert server.rpc("initialize")[0] == 200
        status, body = server.rpc("initialize")
        assert status == 429
        assert body["error"]["code"] == RATE_LIMITED_CODE

        request = urllib.request.Request(
            server.url(),
            data=json.dumps({"jsonrpc": "2.0", "id": 9, "method": "initialize"}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(request, timeout=10)
            raise AssertionError("expected 429")
        except urllib.error.HTTPError as exc:
            assert exc.code == 429
            assert exc.headers.get("Retry-After") == "60"

        events = [r for r in server.audit_lines() if r.get("outcome") == "rate_limited"]
        assert events, "rate-limit event must be observable in the audit log"
    finally:
        transport.stop()


def test_concurrent_requests_beyond_capacity_and_queue_are_overloaded(
    make_server, index, monkeypatch
):
    """#264 acceptance: tests cover concurrent callers and queue exhaustion.

    One slot, no queue: a second caller arriving while the first is still
    being served must be refused immediately with a protocol-appropriate
    overload response, not blocked or dropped silently.
    """
    from repo2graph import mcp

    entered = threading.Event()
    release = threading.Event()

    def slow_dispatch(index, name, arguments, cache=None, tasks=None):
        entered.set()
        release.wait(5)
        return "slow-ok"

    monkeypatch.setattr(mcp, "dispatch", slow_dispatch)

    stream = io.StringIO()
    audit = AuditLogger(AuditConfig(level="all"), stream=stream)
    transport = HTTPTransport(
        index,
        None,
        host="127.0.0.1",
        port=0,
        audit=audit,
        rate_limit_config=RateLimitConfig(
            requests_per_window=100,
            max_concurrent_requests=1,
            max_queue_size=0,
            queue_wait_seconds=0.2,
            max_concurrent_builds=100,
        ),
    )
    transport.start()
    try:
        server = Server(transport, stream)

        blocker = threading.Thread(target=lambda: server.call("repo_map"))
        blocker.start()
        assert entered.wait(5), "the blocking call never reached dispatch"

        status, body = server.call("repo_map")
        assert status == 503
        assert body["error"]["code"] == OVERLOADED_CODE

        release.set()
        blocker.join(timeout=5)
        assert not blocker.is_alive()
    finally:
        release.set()
        transport.stop()


def test_index_build_concurrency_is_gated_independently(make_server, monkeypatch, tmp_path):
    """#264: the build-concurrency limit is a distinct gate from the general
    concurrency slot -- proven by setting general concurrency high and the
    build gate to 1, then showing a second concurrent open still overloads."""
    from repo2graph import mcp

    entered = threading.Event()
    release = threading.Event()
    real_open = mcp.open_index_or_task

    def slow_open(index_dir, repo, cache, tasks):
        entered.set()
        release.wait(5)
        return real_open(index_dir, repo, cache, tasks)

    monkeypatch.setattr(mcp, "open_index_or_task", slow_open)

    repo = write_mini_repo(tmp_path)
    out = tmp_path / ".r2g"
    build_mini_index(repo, out)

    stream = io.StringIO()
    audit = AuditLogger(AuditConfig(level="all"), stream=stream)
    transport = HTTPTransport(
        out,
        None,
        host="127.0.0.1",
        port=0,
        audit=audit,
        rate_limit_config=RateLimitConfig(
            requests_per_window=100,
            max_concurrent_requests=100,
            max_queue_size=100,
            max_concurrent_builds=1,
        ),
    )
    transport.start()
    try:
        server = Server(transport, stream)

        blocker = threading.Thread(target=lambda: server.call("repo_map"))
        blocker.start()
        assert entered.wait(5), "the blocking open_index call never ran"

        status, body = server.call("repo_map")
        assert status == 503
        assert body["error"]["code"] == OVERLOADED_CODE
        assert "build concurrency" in body["error"]["message"]

        release.set()
        blocker.join(timeout=5)
    finally:
        release.set()
        transport.stop()


def test_a_response_over_the_configured_size_limit_is_replaced_with_a_bounded_error(
    make_server, index
):
    stream = io.StringIO()
    audit = AuditLogger(AuditConfig(level="all"), stream=stream)
    transport = HTTPTransport(
        index,
        None,
        host="127.0.0.1",
        port=0,
        audit=audit,
        rate_limit_config=RateLimitConfig(max_response_bytes=16),
    )
    transport.start()
    try:
        server = Server(transport, stream)
        status, body = server.call("repo_map")
        assert status == 500
        assert body["error"]["code"] == INTERNAL_ERROR
        assert "size limit" in body["error"]["message"]
        assert len(json.dumps(body)) < 4096
    finally:
        transport.stop()


def test_the_oidc_subject_is_the_rate_limit_identity_not_the_shared_token(make_server):
    """#264: identity for rate limiting comes from the verified subject where
    one exists. Two different OIDC subjects each get their own bucket."""
    server = make_server(AuthConfig(oidc_issuer=ISSUER, audience=AUDIENCE), opener=FakeIssuer())
    alice = sign(claims(sub="alice@example.com"))
    bob = sign(claims(sub="bob@example.com"))
    assert server.call("repo_map", token=alice)[0] == 200
    assert server.call("repo_map", token=bob)[0] == 200


# --------------------------------------------------------------------- #267


def test_forwarded_for_is_ignored_by_default(make_server, index):
    """#267: trust_proxy defaults False -- X-Forwarded-For never changes the
    client identity used for rate limiting, so two "different" forwarded
    values from the same real peer share one bucket."""
    stream = io.StringIO()
    audit = AuditLogger(AuditConfig(level="all"), stream=stream)
    transport = HTTPTransport(
        index,
        None,
        host="127.0.0.1",
        port=0,
        audit=audit,
        rate_limit_config=RateLimitConfig(requests_per_window=1, window_seconds=60.0),
    )
    transport.start()
    try:
        server = Server(transport, stream)
        status1, _ = server.rpc("initialize", headers={"X-Forwarded-For": "1.2.3.4"})
        status2, _ = server.rpc("initialize", headers={"X-Forwarded-For": "5.6.7.8"})
        assert status1 == 200
        assert status2 == 429, "forwarded header must not have created a second identity bucket"
    finally:
        transport.stop()


def test_forwarded_for_is_honoured_only_for_a_trusted_peer(index):
    """With trust_proxy on AND the connecting peer allowlisted, distinct
    X-Forwarded-For values get distinct rate-limit buckets."""
    stream = io.StringIO()
    audit = AuditLogger(AuditConfig(level="all"), stream=stream)
    transport = HTTPTransport(
        index,
        None,
        host="127.0.0.1",
        port=0,
        audit=audit,
        rate_limit_config=RateLimitConfig(requests_per_window=1, window_seconds=60.0),
        trust_proxy=True,
        trusted_proxies=["127.0.0.1"],
    )
    transport.start()
    try:
        server = Server(transport, stream)
        status1, _ = server.rpc("initialize", headers={"X-Forwarded-For": "1.2.3.4"})
        status2, _ = server.rpc("initialize", headers={"X-Forwarded-For": "5.6.7.8"})
        assert status1 == 200
        assert status2 == 200, "trusted-proxy forwarding must separate the two identities"
    finally:
        transport.stop()


def test_forwarded_for_is_ignored_when_the_peer_is_not_in_the_allowlist(index):
    """trust_proxy=True alone is not enough -- the peer must also be listed."""
    stream = io.StringIO()
    audit = AuditLogger(AuditConfig(level="all"), stream=stream)
    transport = HTTPTransport(
        index,
        None,
        host="127.0.0.1",
        port=0,
        audit=audit,
        rate_limit_config=RateLimitConfig(requests_per_window=1, window_seconds=60.0),
        trust_proxy=True,
        trusted_proxies=["10.0.0.1"],  # not the loopback peer the test connects from
    )
    transport.start()
    try:
        server = Server(transport, stream)
        status1, _ = server.rpc("initialize", headers={"X-Forwarded-For": "1.2.3.4"})
        status2, _ = server.rpc("initialize", headers={"X-Forwarded-For": "5.6.7.8"})
        assert status1 == 200
        assert status2 == 429, "an unlisted peer's forwarded header must still be ignored"
    finally:
        transport.stop()


def test_forwarded_headers_never_affect_authentication(make_server):
    """#267: forwarded identity headers are ignored for authentication too --
    trivially true here since Authenticator only ever reads Authorization,
    but pinned so a future change that starts consulting them is caught."""
    server = make_server(AuthConfig(token="s3cret"))
    status, body = server.rpc(
        "tools/call",
        {"name": "repo_map", "arguments": {}},
        token="s3cret",
        headers={"X-Forwarded-User": "admin", "X-Forwarded-For": "10.0.0.1"},
    )
    assert status == 200
    (record,) = [r for r in server.audit_lines() if r["event"] == "tool_call"]
    assert record["identity"] == "bearer"


def test_non_loopback_bind_with_auth_warns_unless_acknowledged(index, capsys):
    """#267 acceptance: binding to 0.0.0.0 without a TLS/reverse-proxy
    acknowledgement produces a high-visibility warning. Existing behaviour
    (`test_binding_beyond_loopback_with_auth_is_allowed` in
    test_http_transport.py) requires this to still be non-fatal, so the
    property under test is the warning's presence, not a refusal to start.
    """
    capsys.readouterr()
    transport = HTTPTransport(
        index,
        host="0.0.0.0",
        port=0,
        auth_config=AuthConfig(token="s3cret"),
        audit=AuditLogger(AuditConfig(level="none"), stream=io.StringIO()),
    )
    err = capsys.readouterr().err
    assert "does NOT terminate TLS" in err
    assert '"event": "http_transport_no_tls_termination"' in err
    assert transport.host == "0.0.0.0"


def test_non_loopback_bind_warning_is_suppressed_with_explicit_ack(index, capsys):
    capsys.readouterr()
    transport = HTTPTransport(
        index,
        host="0.0.0.0",
        port=0,
        auth_config=AuthConfig(token="s3cret"),
        audit=AuditLogger(AuditConfig(level="none"), stream=io.StringIO()),
        insecure_transport_ack=True,
    )
    err = capsys.readouterr().err
    assert "does NOT terminate TLS" not in err
    assert "http_transport_no_tls_termination" not in err
    assert transport.host == "0.0.0.0"


def test_the_client_identity_map_is_bounded_by_its_own_ceiling():
    """#264: the sliding window keeps per-identity state, so a flood of
    one-shot identities is itself a memory attack.

    The ceiling is `max_tracked_clients` and nothing else. It used to be
    derived as `8 * max_concurrent_requests`, which conflates two unrelated
    numbers: a server tuned for 64 concurrent requests would evict real
    clients after 512 distinct identities -- and eviction is not a throttle,
    it *clears* a client's history and hands it a fresh allowance. So the
    two knobs are set in opposition here: a tiny tracking ceiling alongside
    a large concurrency setting. Under the old derivation the map would be
    allowed 8 * 200 = 1600 entries and this would fail.
    """
    from repo2graph.http_server import RateLimiter

    limiter = RateLimiter(
        RateLimitConfig(
            requests_per_window=1000,
            window_seconds=3600.0,  # nothing ages out during the test
            max_concurrent_requests=200,
            max_tracked_clients=16,
        )
    )
    for i in range(500):
        assert limiter.check_rate(f"client-{i}") is True

    assert len(limiter._windows) <= 16, (
        f"tracked {len(limiter._windows)} identities against a ceiling of 16"
    )


class TestRateLimiterPrimitives:
    """#264's concurrency primitives, exercised directly.

    Every other test here drives them through a live HTTP request, which is the
    right shape for the policy but leaves the accounting inside `acquire_slot`
    untested on its own -- and that accounting is the part that can leak. A
    queue counter that is not decremented on the timeout path degrades the
    server permanently: every later caller sees a full queue and is refused,
    while the concurrency semaphore itself sits idle.
    """

    def _limiter(self, **kw):
        from repo2graph.http_server import RateLimiter

        return RateLimiter(RateLimitConfig(**kw))

    def test_slots_are_handed_out_up_to_capacity_then_refused(self):
        limiter = self._limiter(max_concurrent_requests=2, max_queue_size=0)
        assert limiter.acquire_slot() is True
        assert limiter.acquire_slot() is True
        # Capacity reached and no queue allowed: refused immediately rather
        # than dropped into a wait.
        assert limiter.acquire_slot() is False

    def test_releasing_a_slot_admits_the_next_caller(self):
        limiter = self._limiter(max_concurrent_requests=1, max_queue_size=0)
        assert limiter.acquire_slot() is True
        assert limiter.acquire_slot() is False
        limiter.release_slot()
        assert limiter.acquire_slot() is True

    def test_a_timed_out_queued_waiter_does_not_leak_a_queue_slot(self):
        """The accounting bug this test exists for: if `_queue_waiting` is not
        decremented when the wait times out, the queue stays permanently full."""
        limiter = self._limiter(
            max_concurrent_requests=1, max_queue_size=1, queue_wait_seconds=0.05
        )
        assert limiter.acquire_slot() is True  # capacity taken

        # Queue depth 1: this one waits, then times out and gives up.
        assert limiter.acquire_slot() is False
        assert limiter._queue_waiting == 0, "a timed-out waiter left the queue counter raised"

        # Proof it was not a one-off: the queue is reusable, not poisoned.
        assert limiter.acquire_slot() is False
        assert limiter._queue_waiting == 0

        limiter.release_slot()
        assert limiter.acquire_slot() is True

    def test_the_build_gate_is_independent_of_the_request_gate(self):
        """Builds are capped separately: saturating one must not close the
        other, or a single in-flight build would stop the server answering
        from an index that is already on disk."""
        limiter = self._limiter(max_concurrent_requests=1, max_concurrent_builds=2)

        assert limiter.acquire_build_slot() is True
        assert limiter.acquire_build_slot() is True
        assert limiter.acquire_build_slot() is False
        # Request capacity is untouched by a saturated build gate.
        assert limiter.acquire_slot() is True

        limiter.release_build_slot()
        assert limiter.acquire_build_slot() is True
