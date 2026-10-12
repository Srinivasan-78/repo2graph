"""Cursor pagination for `repo_search` and `repo_neighbours` (#389).

The fixture is a symbol with sixty callers: more than `MCP_MAX_NEIGHBOURS`
(50), which is the point -- neighbour 51 must be reachable by paging and was
not reachable at all before.
"""

from __future__ import annotations

import base64
import importlib
import json
from pathlib import Path

import pytest

from repo2graph.cli import main
from repo2graph.mcp.guardrails import MCP_MAX_BUDGET_TOKENS, MCP_MAX_NEIGHBOURS
from repo2graph.mcp.pagination import NEXT_CURSOR_PREFIX, PagedText
from repo2graph.mcp.schemas import TOOL_SCHEMAS, ToolError
from repo2graph.mcp.server import run_tool
from repo2graph.mcp.tools import dispatch
from repo2graph.query import Index, count_tokens

FAN_CALLERS = 60
HUB = "sym:pkg/fan.py::hub_target"
FAN_QUERY = "caller number value"


def write_fan_repo(root: Path, callers: int = FAN_CALLERS) -> Path:
    """One function called by `callers` others, all in one module."""
    repo = root / "fan"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pyproject.toml").write_text("", encoding="utf8")
    (repo / "pkg" / "__init__.py").write_text("", encoding="utf8")
    parts = ["def hub_target(x):\n    return x + 1\n\n"]
    for i in range(callers):
        parts.append(
            f'\ndef caller_{i:02d}(value):\n    """Caller number {i}."""\n'
            f"    return hub_target(value) * {i}\n\n"
        )
    (repo / "pkg" / "fan.py").write_text("".join(parts), encoding="utf8", newline="\n")
    return repo


def build_index(repo: Path, out: Path) -> Path:
    main(["build", str(repo), "-o", str(out), "--formats", "jsonl,overview"])
    return out


@pytest.fixture(scope="module")
def fan_index(tmp_path_factory):
    root = tmp_path_factory.mktemp("fan")
    return build_index(write_fan_repo(root), root / "idx")


def _rows(text: str) -> list[str]:
    return [ln for ln in text.split("\n") if ln.startswith("- ")]


def _page_all(index, name, args, max_pages=200):
    """Follow cursors from the first page to the last; return every page."""
    pages = []
    cursor = ""
    while cursor is not None:
        page = dispatch(index, name, {**args, "cursor": cursor})
        assert not isinstance(page, ToolError), page
        pages.append(page)
        cursor = getattr(page, "next_cursor", None)
        assert len(pages) <= max_pages, "paging never terminated"
    return pages


def _last_line_cursor(text: str) -> str | None:
    last = text.rsplit("\n", 1)[-1]
    return last[len(NEXT_CURSOR_PREFIX) :] if last.startswith(NEXT_CURSOR_PREFIX) else None


# ---------------------------------------------------------------- neighbours


def test_paging_reaches_neighbour_51_and_covers_every_neighbour_once(fan_index):
    index = Index(fan_index)
    pages = _page_all(index, "repo_neighbours", {"node_id": HUB, "limit": MCP_MAX_NEIGHBOURS})
    rows = [row for page in pages for row in _rows(page)]

    assert len(pages) >= 2
    assert len(rows) == len(set(rows)), "a neighbour appeared on two pages"
    assert len(rows) > MCP_MAX_NEIGHBOURS
    callers = {f"sym:pkg/fan.py::caller_{i:02d}" for i in range(FAN_CALLERS)}
    assert callers <= {r.split("[", 1)[1].split("]", 1)[0] for r in rows if "[" in r}
    # Row 51 onwards is on the second page, and the header says so.
    assert "(rows 51-" in pages[1]


def test_page_size_does_not_change_what_paging_covers(fan_index):
    index = Index(fan_index)
    big = [
        r
        for p in _page_all(index, "repo_neighbours", {"node_id": HUB, "limit": 50})
        for r in _rows(p)
    ]
    small_pages = _page_all(index, "repo_neighbours", {"node_id": HUB, "limit": 7})
    small = [r for p in small_pages for r in _rows(p)]
    assert small == big
    assert all(len(_rows(p)) <= 7 for p in small_pages)


def test_every_neighbour_page_is_within_the_ceilings(fan_index):
    index = Index(fan_index)
    for limit in (1, 13, MCP_MAX_NEIGHBOURS, 10**9):
        for page in _page_all(index, "repo_neighbours", {"node_id": HUB, "limit": limit}):
            assert len(_rows(page)) <= MCP_MAX_NEIGHBOURS
            assert count_tokens(page) <= MCP_MAX_BUDGET_TOKENS


def test_cursor_is_in_the_text_and_on_the_result(fan_index):
    index = Index(fan_index)
    pages = _page_all(index, "repo_neighbours", {"node_id": HUB, "limit": 20})
    for page in pages[:-1]:
        assert isinstance(page, PagedText)
        assert page.next_cursor and _last_line_cursor(page) == page.next_cursor
        assert "\n\n" + NEXT_CURSOR_PREFIX in page
    # The last page has nothing to continue to, and says nothing about it.
    assert pages[-1].next_cursor is None
    assert NEXT_CURSOR_PREFIX not in pages[-1]


def test_a_stale_index_note_keeps_the_cursor_on_the_result(fan_index, monkeypatch):
    # `repo2graph.mcp.server` the attribute is a ServerWrapper; patch the module.
    server = importlib.import_module("repo2graph.mcp.server")
    monkeypatch.setattr(server, "_staleness_note", lambda index: "_note: stale._\n\n")
    page = run_tool(fan_index, None, "repo_neighbours", {"node_id": HUB, "cursor": ""})
    assert page.startswith("_note: stale._")
    assert isinstance(page, PagedText)
    assert page.next_cursor and _last_line_cursor(page) == page.next_cursor


def test_a_page_that_is_complete_has_no_cursor(fan_index):
    index = Index(fan_index)
    out = dispatch(index, "repo_neighbours", {"node_id": "file:pkg/__init__.py", "cursor": ""})
    assert not isinstance(out, ToolError)
    assert getattr(out, "next_cursor", None) is None
    assert NEXT_CURSOR_PREFIX not in out


# ---------------------------------------------------------------- search


def test_search_pages_are_bounded_and_never_repeat_a_seed(fan_index):
    index = Index(fan_index)
    budget = 400
    pages = _page_all(index, "repo_search", {"query": FAN_QUERY, "k": 4, "budget_tokens": budget})
    assert len(pages) >= 3
    for page in pages:
        assert count_tokens(page) <= budget
        assert "[cite:" in page

    # Seeds, read straight off the engine at the offsets the cursors named.
    seen: list[str] = []
    offset: int | None = 0
    while offset is not None:
        pack = index.pack_context(
            FAN_QUERY, k=4, hops=1, budget_tokens=300, exclude_secrets=True, seed_offset=offset
        )
        seen += [c["node_id"] for c in pack["seeds"]]
        offset = pack["next_seed_offset"]
    assert len(seen) == len(set(seen)), "a seed appeared on two pages"
    assert len(seen) == FAN_CALLERS + 1


def test_search_pages_respect_the_ceiling_when_the_budget_is_absurd(fan_index):
    index = Index(fan_index)
    for page in _page_all(
        index, "repo_search", {"query": FAN_QUERY, "k": 50, "budget_tokens": 10**9}
    ):
        assert count_tokens(page) <= MCP_MAX_BUDGET_TOKENS


def test_only_the_first_search_page_carries_the_map(fan_index):
    index = Index(fan_index)
    pages = _page_all(index, "repo_search", {"query": FAN_QUERY, "k": 8, "budget_tokens": 800})
    assert "# Repo map:" in pages[0]
    assert all("# Repo map:" not in p for p in pages[1:])


# ---------------------------------------------------------------- no cursor


def test_no_cursor_is_byte_identical_to_the_unpaged_answer(fan_index):
    index = Index(fan_index)
    out = dispatch(index, "repo_neighbours", {"node_id": HUB, "limit": 50})
    # The pre-#389 answer, written out: six sampled edges, no cursor line.
    expected = "\n".join(
        [
            "neighbours of `hub_target` (pkg/fan.py:1) [sym:pkg/fan.py::hub_target]:",
            "- DEFINES in: `fan.py` (pkg/fan.py) [file:pkg/fan.py]  -- at pkg/fan.py:1",
            *(
                f"- CALLS in: `caller_{i:02d}` (pkg/fan.py:{5 * i + 5}) "
                f"[sym:pkg/fan.py::caller_{i:02d}]  -- at pkg/fan.py:{5 * i + 7}"
                for i in range(5)
            ),
        ]
    )
    assert out == expected
    assert type(out) is str
    assert dispatch(index, "repo_neighbours", {"node_id": HUB, "limit": 50, "cursor": None}) == out

    search = dispatch(index, "repo_search", {"query": FAN_QUERY})
    pack = index.pack_context(
        FAN_QUERY, k=8, hops=1, budget_tokens=6000, exclude_secrets=True, max_neighbours=None
    )
    assert search == pack["markdown"]
    assert type(search) is str and NEXT_CURSOR_PREFIX not in search


# ---------------------------------------------------------------- refusals


def test_a_cursor_from_before_a_rebuild_is_refused(tmp_path):
    repo = write_fan_repo(tmp_path)
    out = build_index(repo, tmp_path / "idx")
    first = dispatch(Index(out), "repo_neighbours", {"node_id": HUB, "cursor": ""})
    search = dispatch(Index(out), "repo_search", {"query": FAN_QUERY, "k": 2, "cursor": ""})
    assert first.next_cursor and search.next_cursor

    build_index(repo, out)
    rebuilt = Index(out)
    for name, args in (
        ("repo_neighbours", {"node_id": HUB, "cursor": first.next_cursor}),
        ("repo_search", {"query": FAN_QUERY, "k": 2, "cursor": search.next_cursor}),
    ):
        refused = dispatch(rebuilt, name, args)
        assert isinstance(refused, ToolError)
        assert "index was rebuilt; re-run the query without cursor" in refused


def _tamper(cursor: str) -> str:
    body, sig = cursor.split(".")
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    payload["o"] = 0
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=") + "." + sig


@pytest.mark.parametrize(
    "bad",
    [
        "garbage",
        "a.b",
        "!!!.???",
        "x" * 5000,
        5,
        ["list"],
        {"o": 1},
        "tamper",
        "other-tool",
        "other-node",
    ],
)
def test_a_bad_cursor_is_a_tool_error_and_the_server_keeps_answering(fan_index, bad):
    index = Index(fan_index)
    good = dispatch(index, "repo_neighbours", {"node_id": HUB, "cursor": ""}).next_cursor
    if bad == "tamper":
        bad = _tamper(good)
    elif bad == "other-tool":
        bad = dispatch(index, "repo_search", {"query": FAN_QUERY, "k": 1, "cursor": ""}).next_cursor
    elif bad == "other-node":
        bad = dispatch(
            index, "repo_neighbours", {"node_id": "file:pkg/fan.py", "limit": 1, "cursor": ""}
        ).next_cursor

    refused = run_tool(fan_index, None, "repo_neighbours", {"node_id": HUB, "cursor": bad})
    assert isinstance(refused, ToolError)
    assert "cursor" in refused and "Traceback" not in refused

    after = run_tool(fan_index, None, "repo_neighbours", {"node_id": HUB, "cursor": good})
    assert not isinstance(after, ToolError) and "(rows 21-" in after


def test_an_expired_cursor_is_refused(fan_index, monkeypatch):
    from repo2graph.mcp import pagination

    index = Index(fan_index)
    cursor = dispatch(index, "repo_neighbours", {"node_id": HUB, "cursor": ""}).next_cursor
    now = pagination.time.time()
    monkeypatch.setattr(pagination.time, "time", lambda: now + pagination.CURSOR_TTL_S + 1)
    refused = dispatch(index, "repo_neighbours", {"node_id": HUB, "cursor": cursor})
    assert isinstance(refused, ToolError) and "expired" in refused


def test_pages_are_not_cached(fan_index):
    from repo2graph.cache import ResultCache

    cache = ResultCache()
    index = Index(fan_index)
    dispatch(index, "repo_neighbours", {"node_id": HUB, "cursor": ""}, cache=cache)
    assert cache.stats()["size"] == 0


def test_cursor_is_advertised_on_both_paged_tools():
    for name in ("repo_search", "repo_neighbours"):
        prop = TOOL_SCHEMAS[name]["properties"]["cursor"]
        assert prop["type"] == "string" and NEXT_CURSOR_PREFIX.strip() in prop["description"]
        assert "cursor" not in TOOL_SCHEMAS[name].get("required", [])
