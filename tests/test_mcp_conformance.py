"""Stdio MCP conformance, driven by the official MCP client (#298).

The other stdio tests in test_mcp.py hand-write JSON-RPC frames. These use the
SDK's own `ClientSession` over `stdio_client`, so they see the server exactly as
a third-party host does: discovery, annotations, a normal call, the error
shape, the output ceiling at the wire, concurrent calls, and a server that
survives a garbage frame. HTTP transport and auth cases from the original issue
target code that was deleted and are out of scope.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time

import pytest

mcp = pytest.importorskip("mcp")

import anyio  # noqa: E402  (the SDK's own dependency)
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from conftest import MINI_QUERY  # noqa: E402
from repo2graph.mcp.guardrails import MCP_MAX_BUDGET_TOKENS  # noqa: E402
from repo2graph.mcp.schemas import (  # noqa: E402
    TOOL_ANNOTATIONS,
    TOOL_DESCRIPTIONS,
    TOOL_TITLES,
)
from repo2graph.query import count_tokens  # noqa: E402

# Per session, like test_mcp.SERVE_TIMEOUT: a cold Windows runner under xdist is
# many times slower than a local run, but the bound stays finite, so a real hang
# still fails.
TIMEOUT = 240
CALL_TIMEOUT = 200


def _params(index_dir, *extra: str) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "repo2graph.mcp", "--out", str(index_dir), "--no-auto-build", *extra],
    )


def _run(index_dir, body, *extra: str):
    """Open a client session against a fresh server and run `body(session)`."""

    async def main():
        with anyio.fail_after(TIMEOUT):
            async with stdio_client(_params(index_dir, *extra)) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await body(session)

    return anyio.run(main)


def _text(result) -> str:
    return "".join(getattr(c, "text", "") for c in result.content)


def test_discovery_lists_every_tool_with_its_schema_and_annotations(mini_index):
    async def body(session):
        return (await session.list_tools()).tools

    tools = {t.name: t for t in _run(mini_index, body)}
    assert set(tools) == set(TOOL_DESCRIPTIONS)
    for name, tool in tools.items():
        assert tool.input_schema.get("type") == "object", name
        ann = tool.annotations
        assert ann is not None, name
        # --no-auto-build: nothing can write, so every tool is honestly read-only
        # and closed-world.
        for key, want in TOOL_ANNOTATIONS.items():
            attr = re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()  # readOnlyHint -> read_only_hint
            assert getattr(ann, attr) is want, (name, key)
        if name in TOOL_TITLES:
            assert ann.title == TOOL_TITLES[name]


def test_auto_build_annotations_admit_side_effects(mini_repo, mini_index):
    """With auto-build on, a tool call may write an index; the hints must say so."""

    async def body(session):
        return (await session.list_tools()).tools

    async def main():
        with anyio.fail_after(TIMEOUT):
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "repo2graph.mcp", str(mini_repo), "--out", str(mini_index)],
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await body(session)

    tools = {t.name: t for t in anyio.run(main)}
    assert tools["repo_search"].annotations.read_only_hint is False
    assert tools["repo_build_status"].annotations.read_only_hint is True


def test_normal_call_returns_text_content_and_no_error(mini_index):
    async def body(session):
        return await session.call_tool("repo_search", {"query": MINI_QUERY})

    result = _run(mini_index, body)
    assert not result.is_error
    assert "[cite:" in _text(result)


@pytest.mark.parametrize(
    "name,args",
    [
        ("repo_neighbours", {"node_id": "sym:does/not/exist.py::nope"}),
        ("repo_read", {"path": "../../etc/passwd"}),
    ],
)
def test_bad_arguments_come_back_as_a_result_not_a_dead_server(mini_index, name, args):
    """A refused call is an answer; the next call on the same session must work."""

    async def body(session):
        bad = await session.call_tool(name, args)
        good = await session.call_tool("repo_map", {})
        return bad, good

    bad, good = _run(mini_index, body)
    assert _text(bad).strip()
    assert not good.is_error and "# Repo map:" in _text(good)


def test_unknown_tool_is_reported_as_an_error(mini_index):
    async def body(session):
        return await session.call_tool("repo_no_such_tool", {})

    result = _run(mini_index, body)
    assert result.is_error


def test_output_is_clamped_at_the_wire(big_index):
    """guardrails clamps internally; this checks what actually crosses stdio."""

    async def body(session):
        return await session.call_tool(
            "repo_search", {"query": "handler request", "budget_tokens": 10**9, "k": 50}
        )

    text = _text(_run(big_index, body))
    assert text.strip()
    assert count_tokens(text) <= MCP_MAX_BUDGET_TOKENS


def test_concurrent_calls_on_one_session_all_complete(mini_index):
    """The mcp 1.x hang (#407) was on concurrent tools/call; pin it for 2.x."""

    async def body(session):
        results: dict[int, object] = {}
        took: dict[int, float] = {}
        start = time.monotonic()

        async def one(i):
            # Bounded per call, so a stall names the request that never got
            # an answer instead of surfacing as a bare session timeout.
            with anyio.move_on_after(CALL_TIMEOUT):
                results[i] = await session.call_tool("repo_search", {"query": f"{MINI_QUERY} {i}"})
                took[i] = round(time.monotonic() - start, 1)

        async with anyio.create_task_group() as tg:
            for i in range(8):
                tg.start_soon(one, i)
        return results, took

    results, took = _run(mini_index, body)
    missing = sorted(set(range(8)) - set(results))
    assert not missing, f"calls {missing} got no response within {CALL_TIMEOUT}s; answered: {took}"
    assert all(not r.is_error for r in results.values())


def test_a_garbage_frame_does_not_kill_the_server(mini_index):
    """Stdio framing: a line that is not JSON must not take the server down."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "repo2graph.mcp", "--out", str(mini_index), "--no-auto-build"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf8",
    )
    try:

        def send(obj):
            proc.stdin.write((obj if isinstance(obj, str) else json.dumps(obj)) + "\n")
            proc.stdin.flush()

        def reply(want_id):
            while True:
                line = proc.stdout.readline()
                assert line, "server closed stdout"
                msg = json.loads(line)
                if msg.get("id") == want_id:
                    return msg

        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "clientInfo": {"name": "t", "version": "1"},
                    "capabilities": {},
                },
            }
        )
        assert "result" in reply(1)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send("this is not json {")
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        assert reply(2)["result"]["tools"]
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait(timeout=10)


# Paging over the real transport (#389).
import os  # noqa: E402

from conftest import REPO_ROOT  # noqa: E402
from test_mcp_pagination import HUB, build_index, write_fan_repo  # noqa: E402


def _server_params_this_checkout(index_dir):
    from mcp import StdioServerParameters

    # The child must import this checkout, not whatever copy is installed.
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(REPO_ROOT), env.get("PYTHONPATH")]))
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "repo2graph.mcp", "--out", str(index_dir)],
        env=env,
        cwd=str(REPO_ROOT),
    )


def test_official_client_pages_repo_neighbours_to_the_end(tmp_path):
    import anyio
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    index_dir = build_index(write_fan_repo(tmp_path), tmp_path / "idx")

    async def scenario():
        rows: list[str] = []
        pages = 0
        async with stdio_client(_server_params_this_checkout(index_dir)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                cursor: str | None = ""
                while cursor is not None:
                    result = await session.call_tool(
                        "repo_neighbours", {"node_id": HUB, "limit": 50, "cursor": cursor}
                    )
                    assert not result.is_error, _text(result)
                    text = _text(result)
                    pages += 1
                    rows += [ln for ln in text.split("\n") if ln.startswith("- ")]
                    meta = result.meta or {}
                    cursor = meta.get("nextCursor")
                    last = text.rsplit("\n", 1)[-1]
                    if cursor is None:
                        assert not last.startswith("next_cursor: ")
                    else:
                        assert last == f"next_cursor: {cursor}"
                    assert pages < 20

                bad = await session.call_tool(
                    "repo_neighbours", {"node_id": HUB, "cursor": "not-a-cursor"}
                )
                assert bad.is_error and "cursor" in _text(bad)
                again = await session.call_tool("repo_neighbours", {"node_id": HUB})
                assert not again.is_error and "next_cursor" not in _text(again)
                assert not (again.meta or {}).get("nextCursor")
        return rows, pages

    rows, pages = anyio.run(scenario)
    assert pages == 2
    assert len(rows) == len(set(rows)) == 61
    assert any("caller_59" in r for r in rows)
