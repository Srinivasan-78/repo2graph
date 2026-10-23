"""Tests for MCP HTTP / Server-Sent Events (SSE) transport (#506)."""

import asyncio
import socket
from typing import Any

import pytest
from starlette.testclient import TestClient

from repo2graph.mcp.server import (
    build_mcp_server,
    create_sse_app,
    main,
)

try:
    from mcp.client.session import ClientSession
    from mcp.client.sse import sse_client

    HAS_MCP_CLIENT = True
except ImportError:
    HAS_MCP_CLIENT = False


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_sse_probes_unauthenticated(mini_index):
    """Health probes must return 200 OK without any authentication."""
    mcp_server = build_mcp_server(mini_index)
    app = create_sse_app(mcp_server)
    client = TestClient(app)

    res_health = client.get("/healthz")
    assert res_health.status_code == 200
    assert res_health.text == "OK"

    res_ready = client.get("/readyz")
    assert res_ready.status_code == 200
    assert res_ready.text == "OK"


def test_sse_auth_middleware(mini_index):
    """When an auth token is required, unauthenticated SSE/messages requests are rejected."""
    mcp_server = build_mcp_server(mini_index)
    app = create_sse_app(mcp_server, auth_token="super-secret")
    client = TestClient(app)

    # Health and readiness probes remain open
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200

    # SSE endpoint requires Bearer token
    res_no_auth = client.get("/sse")
    assert res_no_auth.status_code == 401
    assert res_no_auth.headers.get("WWW-Authenticate") == "Bearer"

    res_bad_auth = client.get("/sse", headers={"Authorization": "Bearer wrong-token"})
    assert res_bad_auth.status_code == 401

    # Messages endpoint requires Bearer token
    res_msg_no_auth = client.post("/messages?session_id=123")
    assert res_msg_no_auth.status_code == 401

    res_msg_bad_auth = client.post(
        "/messages?session_id=123", headers={"Authorization": "Bearer wrong-token"}
    )
    assert res_msg_bad_auth.status_code == 401


def test_sse_cli_argument_parsing(mini_index, monkeypatch):
    """Verify --transport sse, --host, --port, --auth-token and env var fallback."""
    captured: dict[str, Any] = {}

    def fake_serve(out, repo=None, **kw):
        captured.update(out=out, repo=repo, **kw)

    from repo2graph import mcp as mcp_mod

    monkeypatch.setattr(mcp_mod, "serve", fake_serve)

    # Test explicit CLI arguments
    main(
        [
            str(mini_index),
            "--transport",
            "sse",
            "--host",
            "0.0.0.0",
            "--port",
            "8888",
            "--auth-token",
            "tok-123",
        ]
    )
    assert captured["transport"] == "sse"
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 8888
    assert captured["auth_token"] == "tok-123"

    # Test env var fallback for auth token
    captured.clear()
    monkeypatch.setenv("REPO2GRAPH_MCP_AUTH_TOKEN", "env-tok-456")
    main(
        [
            str(mini_index),
            "--transport",
            "sse",
        ]
    )
    assert captured["transport"] == "sse"
    assert captured["auth_token"] == "env-tok-456"


@pytest.mark.skipif(not HAS_MCP_CLIENT, reason="mcp client extra required")
def test_sse_roundtrip_with_real_client(mini_index):
    """End-to-end test connecting over HTTP SSE, listing tools and calling a tool."""
    import uvicorn

    mcp_server = build_mcp_server(mini_index)
    app = create_sse_app(mcp_server)
    port = _get_free_port()

    async def _run():
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        uv_server = uvicorn.Server(config)
        server_task = asyncio.create_task(uv_server.serve())
        await asyncio.sleep(0.4)

        try:
            async with sse_client(f"http://127.0.0.1:{port}/sse") as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    init = await session.initialize()
                    assert init.server_info.name == "repo2graph"
                    tools = await session.list_tools()
                    names = {t.name for t in tools.tools}
                    assert "repo_map" in names
                    assert "repo_cache_stats" in names
                    assert "repo_find_symbol" in names

                    res = await session.call_tool("repo_cache_stats", {})
                    assert res.content
                    assert not res.is_error
                    assert "enabled" in res.content[0].text
        finally:
            uv_server.should_exit = True
            await server_task

    asyncio.run(_run())


@pytest.mark.skipif(not HAS_MCP_CLIENT, reason="mcp client extra required")
def test_sse_authenticated_roundtrip(mini_index):
    """Verify that clients with the valid Bearer token can establish sessions and execute tools."""
    import uvicorn
    from httpx2 import HTTPStatusError

    token = "secret-pass-key"
    mcp_server = build_mcp_server(mini_index)
    app = create_sse_app(mcp_server, auth_token=token)
    port = _get_free_port()

    async def _run():
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        uv_server = uvicorn.Server(config)
        server_task = asyncio.create_task(uv_server.serve())
        await asyncio.sleep(0.4)

        try:
            # 1. Unauthenticated client connection fails with HTTP 401
            failed = False
            try:
                async with sse_client(f"http://127.0.0.1:{port}/sse") as streams:
                    async with ClientSession(streams[0], streams[1]) as session:
                        await session.initialize()
            except HTTPStatusError as err:
                failed = True
                assert err.response.status_code == 401
            assert failed, "Unauthenticated client connection should have been rejected"

            # 2. Authenticated client connects successfully
            async with sse_client(
                f"http://127.0.0.1:{port}/sse",
                headers={"Authorization": f"Bearer {token}"},
            ) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    init = await session.initialize()
                    assert init.server_info.name == "repo2graph"
                    tools = await session.list_tools()
                    assert len(tools.tools) > 0
        finally:
            uv_server.should_exit = True
            await server_task

    asyncio.run(_run())


@pytest.mark.skipif(not HAS_MCP_CLIENT, reason="mcp client extra required")
def test_sse_concurrent_sessions(mini_index):
    """Verify multiple concurrent client sessions over SSE."""
    import uvicorn

    mcp_server = build_mcp_server(mini_index)
    app = create_sse_app(mcp_server)
    port = _get_free_port()

    async def _client_call(client_id: int):
        async with sse_client(f"http://127.0.0.1:{port}/sse") as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                res = await session.call_tool("repo_cache_stats", {})
                return client_id, res.content[0].text

    async def _run():
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        uv_server = uvicorn.Server(config)
        server_task = asyncio.create_task(uv_server.serve())
        await asyncio.sleep(0.4)

        try:
            r1, r2 = await asyncio.gather(_client_call(1), _client_call(2))
            assert r1[0] == 1 and "enabled" in r1[1]
            assert r2[0] == 2 and "enabled" in r2[1]
        finally:
            uv_server.should_exit = True
            await server_task

    asyncio.run(_run())


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "", "*", "192.168.1.10", "10.0.0.5", "[::]", "::", "mcp.internal"],
)
def test_sse_refuses_unauthenticated_non_loopback_bind(host):
    """Binding past loopback without a token must refuse, not serve silently.

    `create_sse_app` installs its auth middleware only `if auth_token`, so the
    no-token case was not "open with a warning" -- it was an unauthenticated
    index on a routable port with nothing said about it.
    """
    from repo2graph.mcp.server import require_sse_auth

    with pytest.raises(SystemExit) as exc:
        require_sse_auth(host, None)
    assert "no\nauthentication" in str(exc.value) or "no authentication" in str(exc.value)
    assert "--auth-token" in str(exc.value)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.1.2.3", "::ffff:127.0.0.1"])
def test_sse_allows_loopback_without_a_token(host):
    """The documented port-forward case stays open: loopback is as private as stdin."""
    from repo2graph.mcp.server import require_sse_auth

    require_sse_auth(host, None)  # must not raise


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", ""])
def test_sse_allows_non_loopback_with_a_token(host):
    from repo2graph.mcp.server import require_sse_auth

    require_sse_auth(host, "s3cret")  # must not raise


def test_sse_auth_rejects_wrong_and_prefix_tokens(mini_index):
    """A token that is a correct *prefix* must be rejected like any other.

    Pins the `hmac.compare_digest` comparison: `==` would also reject these,
    but these are the cases whose timing it leaked.
    """
    mcp_server = build_mcp_server(mini_index)
    app = create_sse_app(mcp_server, auth_token="supersecrettoken")
    client = TestClient(app)

    # Health probes stay open.
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200

    for bad in ("", "s", "supersecrettoke", "supersecrettokenX", "SUPERSECRETTOKEN"):
        r = client.get("/sse", headers={"Authorization": f"Bearer {bad}"})
        assert r.status_code == 401, f"token {bad!r} was accepted"
    assert client.get("/sse").status_code == 401
    assert client.get("/sse", headers={"Authorization": "supersecrettoken"}).status_code == 401


def test_sse_auth_rejects_non_ascii_bearer_without_crashing(mini_index):
    """A garbage byte in the Authorization header must 401, not raise.

    `hmac.compare_digest` raises TypeError on a `str` carrying any non-ASCII
    character, and `latin-1` decodes every byte 0x80-0xFF into one. Because
    `AuthMiddleware` wraps the Starlette app from *outside*, Starlette's
    `ServerErrorMiddleware` never saw that exception -- it surfaced as an
    unhandled ASGI error an unauthenticated caller could trigger at will. The
    comparison is on bytes now, so this is an ordinary rejection.
    """
    import asyncio

    mcp_server = build_mcp_server(mini_index)
    app = create_sse_app(mcp_server, auth_token="supersecrettoken")

    async def _call(header: bytes) -> int:
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/sse",
            "headers": [(b"authorization", header)],
            "query_string": b"",
        }
        sent: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await app(scope, receive, send)
        return next(m["status"] for m in sent if m["type"] == "http.response.start")

    # Raw high bytes, a UTF-8 multibyte sequence, and a non-ASCII token body.
    for header in (b"Bearer \xc3\xa9", b"Bearer \xff\xfe", b"\x80\x81", b"Bearer caf\xe9"):
        assert asyncio.run(_call(header)) == 401, f"{header!r} did not cleanly 401"


def test_create_sse_app_warns_when_built_without_a_token(mini_index, capsys):
    """The exported constructor must not hand back an open index in silence.

    ...but it must also stay quiet for the documented loopback default, or every
    ordinary local `--transport sse` user is warned for doing the sanctioned
    thing. An unknown bind host is treated as exposed, since that is the safe
    direction for a check whose only effect is a warning.
    """
    build = build_mcp_server(mini_index)

    # Bind host unknown: assume exposure and warn.
    capsys.readouterr()
    create_sse_app(build)
    assert "no auth token" in capsys.readouterr().err

    # A token silences it regardless of host.
    create_sse_app(build, auth_token="t")
    assert "no auth token" not in capsys.readouterr().err
    create_sse_app(build, auth_token="t", bind_host="0.0.0.0")
    assert "no auth token" not in capsys.readouterr().err

    # The documented default is silent.
    for host in ("127.0.0.1", "localhost", "::1"):
        create_sse_app(build, bind_host=host)
        assert "no auth token" not in capsys.readouterr().err, host

    # A real exposure with no token still warns.
    for host in ("0.0.0.0", "", "192.168.1.10"):
        create_sse_app(build, bind_host=host)
        assert "no auth token" in capsys.readouterr().err, host
