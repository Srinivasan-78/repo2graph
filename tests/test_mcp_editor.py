"""#400 -- what an editor needs from the MCP server.

The VS Code extension in `editors/vscode/` asks two questions on every cursor
move: "which symbol encloses this line" (`repo_find_symbol` with `path` and
`line`) and "what is around it" (`repo_neighbours` with `format: "json"`). It
also starts the server with `--staleness-ttl`, so the working-tree scan behind
the stale-index note is not paid twice per keystroke.

Literal expectations are hand-derived from the fixtures, in the style of
`tests/test_mcp_tools.py`, not read back from the code under test.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import FILE_GATEWAY, SYM_AUDIT, SYM_ROUTE
from repo2graph.cli import main as cli_main
from repo2graph.query import Index

FILE_AUDIT = "file:pkg/audit.py"
FILE_NOTES = "file:docs/notes.md"
FILE_ENV = "file:.env"

NESTED = '''class Outer:
    """Holds one method."""

    def inner(self):
        return 1


def free():
    return Outer().inner()
'''
SYM_OUTER = "sym:pkg/nested.py::Outer"
SYM_INNER = "sym:pkg/nested.py::Outer.inner"
SYM_FREE = "sym:pkg/nested.py::free"
FILE_NESTED = "file:pkg/nested.py"


def mcp_module():
    from repo2graph import mcp as mcp_mod

    return mcp_mod


@pytest.fixture
def nested_index(tmp_path):
    repo = tmp_path / "nested_src"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "nested.py").write_text(NESTED, encoding="utf8", newline="\n")
    out = tmp_path / "nested_idx"
    assert cli_main(["build", str(repo), "-o", str(out), "--formats", "jsonl,overview"]) == 0
    return out


def _ids(text: str) -> list[str]:
    return [row["node_id"] for row in json.loads(text)]


# ==========================================================================
# repo_find_symbol(path, line): the symbol under the cursor
# ==========================================================================


def test_position_lookup_returns_the_enclosing_symbol_then_its_file(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    # route_request spans 5-13 of pkg/gateway.py; line 9 is in its body.
    out = mcp.tool_repo_find_symbol(idx, "", path="pkg/gateway.py", line=9)
    assert _ids(out) == [SYM_ROUTE, FILE_GATEWAY]
    first = json.loads(out)[0]
    assert (first["path"], first["start_line"], first["end_line"]) == ("pkg/gateway.py", 5, 13)


def test_position_lookup_orders_nested_symbols_innermost_first(nested_index):
    mcp = mcp_module()
    idx = Index(nested_index)
    # Outer spans 1-5 and its method inner 4-5: line 5 is inside both.
    assert _ids(mcp.tool_repo_find_symbol(idx, "", path="pkg/nested.py", line=5)) == [
        SYM_INNER,
        SYM_OUTER,
        FILE_NESTED,
    ]
    assert _ids(mcp.tool_repo_find_symbol(idx, "", path="pkg/nested.py", line=9)) == [
        SYM_FREE,
        FILE_NESTED,
    ]


def test_a_line_outside_every_symbol_is_the_file_alone(mini_index, nested_index):
    mcp = mcp_module()
    # gateway.py line 2 is the import; nested.py line 7 is between definitions.
    assert _ids(
        mcp.tool_repo_find_symbol(Index(mini_index), "", path="pkg/gateway.py", line=2)
    ) == [FILE_GATEWAY]
    assert _ids(
        mcp.tool_repo_find_symbol(Index(nested_index), "", path="pkg/nested.py", line=7)
    ) == [FILE_NESTED]


def test_position_lookup_limit_keeps_the_innermost(nested_index):
    mcp = mcp_module()
    idx = Index(nested_index)
    out = mcp.tool_repo_find_symbol(idx, "", path="pkg/nested.py", line=5, limit=1)
    assert _ids(out) == [SYM_INNER]


def test_position_lookup_honours_kind_and_name(nested_index):
    mcp = mcp_module()
    idx = Index(nested_index)
    assert _ids(mcp.tool_repo_find_symbol(idx, "", kind="class", path="pkg/nested.py", line=5)) == [
        SYM_OUTER
    ]
    # A name narrows the enclosing set rather than searching the whole repo.
    assert _ids(mcp.tool_repo_find_symbol(idx, "Outer", path="pkg/nested.py", line=5)) == [
        SYM_OUTER
    ]
    assert json.loads(mcp.tool_repo_find_symbol(idx, "free", path="pkg/nested.py", line=5)) == []


def test_path_alone_narrows_a_name_lookup_to_one_file(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    assert _ids(mcp.tool_repo_find_symbol(idx, "audit_event", path="pkg/audit.py")) == [SYM_AUDIT]
    assert json.loads(mcp.tool_repo_find_symbol(idx, "audit_event", path="pkg/gateway.py")) == []


def test_position_lookup_normalises_the_path_spelling(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    for spelling in ("./pkg/gateway.py", "pkg\\gateway.py", " pkg/gateway.py "):
        out = mcp.tool_repo_find_symbol(idx, "", path=spelling, line=9)
        assert _ids(out) == [SYM_ROUTE, FILE_GATEWAY], spelling


def test_position_lookup_never_returns_a_secret_file(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    assert FILE_ENV in idx.nodes, "the fixture is built with --include-secrets"
    out = mcp.tool_repo_find_symbol(idx, "", path=".env", line=2)
    assert json.loads(out) == []
    assert "abc123deadbeef" not in out


def test_an_unindexed_path_is_an_empty_answer_not_an_error(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    out = mcp.tool_repo_find_symbol(idx, "", path="pkg/nope.py", line=1)
    assert not isinstance(out, mcp.ToolError)
    assert json.loads(out) == []


@pytest.mark.parametrize("line", [0, -3, "abc", True, 1.5e400])
def test_a_line_that_is_not_a_line_number_is_an_error(mini_index, line):
    mcp = mcp_module()
    idx = Index(mini_index)
    out = mcp.tool_repo_find_symbol(idx, "", path="pkg/gateway.py", line=line)
    assert isinstance(out, mcp.ToolError)
    assert "1-based" in out


def test_a_line_without_a_path_is_an_error_that_says_so(mini_index):
    mcp = mcp_module()
    out = mcp.tool_repo_find_symbol(Index(mini_index), "", line=9)
    assert isinstance(out, mcp.ToolError)
    assert "`path`" in out


def test_neither_name_nor_position_is_an_error_naming_both(mini_index):
    mcp = mcp_module()
    out = mcp.tool_repo_find_symbol(Index(mini_index), "")
    assert isinstance(out, mcp.ToolError)
    assert "`name`" in out and "`line`" in out


def test_dispatch_forwards_path_and_line(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    out = mcp.dispatch(idx, "repo_find_symbol", {"path": "pkg/gateway.py", "line": 9})
    assert _ids(out) == [SYM_ROUTE, FILE_GATEWAY]


def test_dispatch_sends_no_position_arguments_a_client_did_not_set(mini_index, monkeypatch):
    """A name-only call reaches the handler exactly as it did before #400."""
    mcp = mcp_module()
    seen = {}

    def _spy(index, name, kind="", path_prefix="", limit=20):
        seen.update(name=name, kind=kind, path_prefix=path_prefix, limit=limit)
        return "[]"

    monkeypatch.setattr(mcp, "tool_repo_find_symbol", _spy)
    assert mcp.dispatch(Index(mini_index), "repo_find_symbol", {"name": "route_request"}) == "[]"
    assert seen["name"] == "route_request"


# ==========================================================================
# repo_neighbours(format="json")
# ==========================================================================


def _markdown_rows(text: str) -> list[tuple[str, str, str]]:
    """(edge, direction, node_id) for each `- EDGE dir: ... [node_id]` line."""
    rows = []
    for line in text.splitlines():
        m = re.match(r"^- (\w+) (in|out): .*\[([^\]]+)\]", line)
        if m:
            rows.append((m.group(1), m.group(2), m.group(3)))
    return rows


def test_json_neighbours_carry_what_an_editor_needs_to_jump(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    out = json.loads(mcp.tool_repo_neighbours(idx, SYM_ROUTE, format="json"))
    assert out["node"]["node_id"] == SYM_ROUTE
    assert (out["node"]["path"], out["node"]["start_line"], out["node"]["end_line"]) == (
        "pkg/gateway.py",
        5,
        13,
    )
    calls = [r for r in out["neighbours"] if (r["edge"], r["direction"]) == ("CALLS", "out")]
    assert len(calls) == 1
    callee = calls[0]
    assert callee["node_id"] == SYM_AUDIT
    assert (callee["path"], callee["start_line"], callee["end_line"]) == ("pkg/audit.py", 2, 7)
    assert callee["name"] == "audit_event" and callee["kind"] == "function"
    # The call site, as the markdown's "-- at path:line" gives it.
    assert callee["at"] == "pkg/gateway.py:12"
    assert callee["confidence"] == 1.0
    assert callee["via"] == SYM_ROUTE
    assert out["truncated"] is False
    assert out["cochange"] == []


def test_json_and_markdown_list_the_same_rows_in_the_same_order(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    for node_id in (SYM_ROUTE, SYM_AUDIT, FILE_GATEWAY, "dir:pkg"):
        md = mcp.tool_repo_neighbours(idx, node_id)
        js = json.loads(mcp.tool_repo_neighbours(idx, node_id, format="json"))
        assert [(r["edge"], r["direction"], r["node_id"]) for r in js["neighbours"]] == (
            _markdown_rows(md)
        ), node_id


def test_explicit_markdown_is_byte_identical_to_the_default(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    assert mcp.tool_repo_neighbours(idx, SYM_ROUTE, format="markdown") == (
        mcp.tool_repo_neighbours(idx, SYM_ROUTE)
    )


def test_an_unknown_format_is_markdown_with_a_note(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    out = mcp.tool_repo_neighbours(idx, SYM_ROUTE, format="xml")
    assert out.startswith("_note: format='xml' is not one of markdown, json")
    assert out.endswith(mcp.tool_repo_neighbours(idx, SYM_ROUTE))


def test_json_neighbours_report_truncation(mini_index):
    mcp = mcp_module()
    out = json.loads(mcp.tool_repo_neighbours(Index(mini_index), SYM_ROUTE, limit=1, format="json"))
    assert len(out["neighbours"]) == 1
    assert out["truncated"] is True


def test_json_neighbours_never_list_a_secret_node(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    root = next(n for n in idx.nodes if n.startswith("repo:"))
    out = mcp.tool_repo_neighbours(idx, root, format="json", limit=50)
    assert FILE_ENV not in [r["node_id"] for r in json.loads(out)["neighbours"]]
    assert ".env" not in out


def _wire_cochange(idx: Index, a: str, b: str, count: int) -> None:
    edge = {"src": a, "dst": b, "type": "CO_CHANGE", "count": count}
    idx.adj[a].append((b, "CO_CHANGE", "out", edge))
    idx.adj[b].append((a, "CO_CHANGE", "in", edge))


def test_json_neighbours_give_a_symbol_its_files_co_change_history(mini_index):
    """A symbol has no CO_CHANGE edge of its own; its file's history answers for it."""
    mcp = mcp_module()
    idx = Index(mini_index)
    _wire_cochange(idx, FILE_AUDIT, FILE_NOTES, 5)
    _wire_cochange(idx, FILE_AUDIT, FILE_GATEWAY, 8)
    _wire_cochange(idx, FILE_AUDIT, FILE_ENV, 99)  # secret: never named

    out = json.loads(mcp.tool_repo_neighbours(idx, SYM_AUDIT, format="json"))
    assert out["cochange"] == [
        {"node_id": FILE_GATEWAY, "path": "pkg/gateway.py", "count": 8},
        {"node_id": FILE_NOTES, "path": "docs/notes.md", "count": 5},
    ]
    capped = json.loads(mcp.tool_repo_neighbours(idx, SYM_AUDIT, format="json", limit=1))
    assert [c["node_id"] for c in capped["cochange"]] == [FILE_GATEWAY]


def test_paged_json_is_json_with_the_cursor_inside_it(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    seen: list[tuple[str, str, str]] = []
    cursor: str | None = ""
    pages = 0
    while cursor is not None:
        text = mcp.tool_repo_neighbours(idx, SYM_ROUTE, limit=1, cursor=cursor, format="json")
        page = json.loads(text)  # no trailing `next_cursor:` line to break parsing
        assert getattr(text, "next_cursor", None) == page["next_cursor"]
        seen += [(r["edge"], r["direction"], r["node_id"]) for r in page["neighbours"]]
        cursor = page["next_cursor"]
        pages += 1
        assert pages < 20
    markdown_pages = mcp.tool_repo_neighbours(idx, SYM_ROUTE, limit=50, cursor="")
    assert seen == _markdown_rows(markdown_pages)
    assert pages == len(seen) + (0 if seen else 1)


def test_dispatch_forwards_format_and_caches_each_format_apart(mini_index):
    from repo2graph.cache import ResultCache

    mcp = mcp_module()
    idx = Index(mini_index)
    cache = ResultCache(max_size=16, ttl=60)
    md = mcp.dispatch(idx, "repo_neighbours", {"node_id": SYM_ROUTE}, cache=cache)
    js = mcp.dispatch(idx, "repo_neighbours", {"node_id": SYM_ROUTE, "format": "json"}, cache=cache)
    assert md.startswith("neighbours of")
    assert json.loads(js)["node"]["node_id"] == SYM_ROUTE


def test_dispatch_sends_no_format_a_client_did_not_set(mini_index, monkeypatch):
    mcp = mcp_module()
    seen = {}

    def _spy(index, node_id, hops=1, limit=20, min_confidence=0.0):
        seen.update(node_id=node_id)
        return "ok"

    monkeypatch.setattr(mcp, "tool_repo_neighbours", _spy)
    assert mcp.dispatch(Index(mini_index), "repo_neighbours", {"node_id": SYM_ROUTE}) == "ok"
    assert seen == {"node_id": SYM_ROUTE}


def test_the_new_arguments_are_advertised_and_accepted():
    import inspect

    from repo2graph.mcp.schemas import TOOL_SCHEMAS

    mcp = mcp_module()
    find = TOOL_SCHEMAS["repo_find_symbol"]
    assert find["properties"]["path"]["type"] == "string"
    assert find["properties"]["line"]["type"] == "integer"
    assert find.get("required", []) == []
    fmt = TOOL_SCHEMAS["repo_neighbours"]["properties"]["format"]
    assert fmt["enum"] == ["markdown", "json"]
    for name, handler in (
        ("repo_find_symbol", mcp.tool_repo_find_symbol),
        ("repo_neighbours", mcp.tool_repo_neighbours),
    ):
        accepted = set(inspect.signature(handler).parameters) - {"index"}
        assert set(TOOL_SCHEMAS[name]["properties"]) <= accepted, name


# ==========================================================================
# --staleness-ttl
# ==========================================================================


def _touch_after_build(path: Path) -> None:
    path.write_text(path.read_text(encoding="utf8") + "\n# changed after the build\n")
    future = time.time() + 5
    os.utime(path, (future, future))


def test_without_a_ttl_every_call_rechecks_the_tree(mini_repo, mini_index, monkeypatch):
    from repo2graph.mcp import nodes

    monkeypatch.setitem(nodes.STALENESS_TTL, "seconds", 0.0)
    idx = mcp_module().open_index(mini_index, repo=mini_repo)
    assert nodes._staleness_note(idx) == ""
    _touch_after_build(mini_repo / "pkg" / "gateway.py")
    assert "index may be stale" in nodes._staleness_note(idx)


def test_a_ttl_reuses_the_check_until_it_expires(mini_repo, mini_index, monkeypatch):
    from repo2graph.mcp import nodes

    monkeypatch.setitem(nodes.STALENESS_TTL, "seconds", 60.0)
    calls = []
    real = nodes._compute_staleness_note

    def _counting(index):
        calls.append(index)
        return real(index)

    monkeypatch.setattr(nodes, "_compute_staleness_note", _counting)
    idx = mcp_module().open_index(mini_index, repo=mini_repo)
    assert nodes._staleness_note(idx) == ""
    _touch_after_build(mini_repo / "pkg" / "gateway.py")
    # Within the TTL the earlier answer stands: one scan for three calls.
    assert nodes._staleness_note(idx) == ""
    assert nodes._staleness_note(idx) == ""
    assert len(calls) == 1

    # A rebuilt index is a new Index object, which never inherits the memo.
    fresh = Index(mini_index)
    fresh.repo_root = Path(mini_repo).resolve()
    assert "index may be stale" in nodes._staleness_note(fresh)
    assert len(calls) == 2

    # And an expired entry is recomputed.
    monkeypatch.setitem(nodes.STALENESS_TTL, "seconds", 1e-9)
    assert "index may be stale" in nodes._staleness_note(idx)
    assert len(calls) == 3


def test_the_server_flag_sets_the_ttl(mini_repo, monkeypatch):
    from repo2graph.mcp import nodes

    mcp = mcp_module()
    monkeypatch.setitem(nodes.STALENESS_TTL, "seconds", 0.0)
    monkeypatch.setattr(mcp, "serve", lambda out, repo=None, **kw: None)
    mcp.main([str(mini_repo), "--no-auto-build", "--staleness-ttl", "7.5"])
    assert nodes.STALENESS_TTL["seconds"] == 7.5
    mcp.main([str(mini_repo), "--no-auto-build"])
    assert nodes.STALENESS_TTL["seconds"] == 0.0


@pytest.mark.parametrize("bad", ["-1", "nan", "inf", "301", "soon"])
def test_the_server_refuses_a_ttl_that_is_not_a_bounded_duration(mini_repo, monkeypatch, bad):
    from repo2graph.mcp import nodes

    mcp = mcp_module()
    monkeypatch.setitem(nodes.STALENESS_TTL, "seconds", 0.0)
    monkeypatch.setattr(mcp, "serve", lambda out, repo=None, **kw: None)
    with pytest.raises(SystemExit) as exc:
        mcp.main([str(mini_repo), "--staleness-ttl", bad])
    assert exc.value.code == 2
    assert nodes.STALENESS_TTL["seconds"] == 0.0


# ==========================================================================
# The editor's two calls over a real stdio server
# ==========================================================================


def test_stdio_position_lookup_then_json_neighbours(mini_repo, mini_index):
    """The exact pair of calls the extension makes on a cursor move."""
    pytest.importorskip("mcp")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "repo2graph.mcp",
            str(mini_repo),
            "--out",
            str(mini_index),
            "--no-auto-build",
            "--staleness-ttl",
            "5",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf8",
    )

    def send(req):
        proc.stdin.write(json.dumps(req) + "\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())

    def call(rid, name, arguments):
        reply = send(
            {
                "jsonrpc": "2.0",
                "id": rid,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        result = reply["result"]
        assert result.get("isError") is not True, result
        return result["content"][0]["text"]

    try:
        init = send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "clientInfo": {"name": "editor-test", "version": "1"},
                    "capabilities": {},
                },
            }
        )
        assert init["result"]["serverInfo"]["name"] == "repo2graph"
        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        )
        proc.stdin.flush()

        found = json.loads(call(2, "repo_find_symbol", {"path": "pkg/gateway.py", "line": 9}))
        assert [r["node_id"] for r in found] == [SYM_ROUTE, FILE_GATEWAY]
        around = json.loads(
            call(3, "repo_neighbours", {"node_id": SYM_ROUTE, "format": "json", "cursor": ""})
        )
        assert SYM_AUDIT in [r["node_id"] for r in around["neighbours"]]
        assert around["next_cursor"] is None
    finally:
        proc.stdin.close()
        proc.terminate()
        proc.wait()
