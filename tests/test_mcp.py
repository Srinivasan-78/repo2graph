"""Change 2 -- the stdio MCP server. AC-26 .. AC-33.

The `mcp` SDK is an optional extra and is deliberately never imported here:
`repo2graph.mcp`'s three handlers are plain functions taking an `Index`, so
AC-26..AC-31 need no SDK at all, and AC-33 blocks the import on purpose to
assert the error message a user without the extra actually sees.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import MINI_QUERY, REPO_ROOT, SECRET_QUERY, SYM_AUDIT, SYM_ROUTE
from repo2graph.query import Index, _is_secret_path

PYPROJECT = REPO_ROOT / "pyproject.toml"


def _has_mcp():
    try:
        import mcp  # noqa: F401
        from mcp.server import Server

        return hasattr(Server, "list_tools") and hasattr(Server, "call_tool")
    except Exception:
        return False


HAS_REAL_MCP = _has_mcp()


def mcp_module():
    from repo2graph import mcp as mcp_mod

    return mcp_mod


def tokens(text: str) -> int:
    from repo2graph.query import count_tokens

    return count_tokens(text)


# ==========================================================================
# AC-26 -- repo_map
# ==========================================================================


def test_ac26_repo_map_is_exactly_map_prepend(mini_index):
    """AC-26: no reformatting, no truncation, no query argument."""
    mcp = mcp_module()
    idx = Index(mini_index)
    assert mcp.tool_repo_map(idx) == idx.map_prepend()
    assert idx.map_prepend().strip(), "the fixture produced an empty map"


# ==========================================================================
# AC-27 / AC-28 -- repo_search and its hard budget ceiling
# ==========================================================================


def test_ac27_repo_search_returns_cited_markdown_within_the_default_budget(big_index):
    """AC-27: at least one `### [cite: path:start-end]` header, and the result
    measures no more than MCP_BUDGET_TOKENS.

    `big_index` packs to well over the default budget when unbounded, so the
    default really is enforced here rather than merely not exceeded.
    """
    mcp = mcp_module()
    idx = Index(big_index)
    out = mcp.tool_repo_search(idx, MINI_QUERY)

    assert isinstance(out, str)
    assert "### [cite: " in out, out[:400]
    assert "-" in out.split("### [cite: ", 1)[1].split("]", 1)[0]
    assert tokens(out) <= mcp.MCP_BUDGET_TOKENS, tokens(out)

    unbounded = idx.pack_context(MINI_QUERY, budget_chars=0)
    assert tokens(unbounded["markdown"]) > mcp.MCP_BUDGET_TOKENS, (
        "the fixture no longer exceeds the default budget"
    )


@pytest.mark.parametrize("budget", [10**9, 10**6, 100000])
def test_ac28_an_absurd_budget_is_clamped_to_the_ceiling(big_index, budget):
    """AC-28: the ceiling is enforced, not advisory."""
    mcp = mcp_module()
    idx = Index(big_index)
    out = mcp.tool_repo_search(idx, MINI_QUERY, k=20, budget_tokens=budget)
    assert tokens(out) <= mcp.MCP_MAX_BUDGET_TOKENS, tokens(out)

    unbounded = idx.pack_context(MINI_QUERY, k=20, budget_chars=0)
    assert tokens(unbounded["markdown"]) > mcp.MCP_MAX_BUDGET_TOKENS, (
        "the fixture no longer exceeds the ceiling"
    )


@pytest.mark.parametrize("budget", [0, -5, -(10**9)])
def test_ac28_a_zero_or_negative_budget_is_clamped_to_the_floor(big_index, budget):
    """AC-28: no crash, no traceback, and still a bounded string."""
    mcp = mcp_module()
    idx = Index(big_index)
    out = mcp.tool_repo_search(idx, MINI_QUERY, k=20, budget_tokens=budget)
    assert isinstance(out, str)
    assert tokens(out) <= mcp.MCP_MAX_BUDGET_TOKENS


def test_ac28_ceiling_is_above_the_default(big_index):
    """AC-28 (guard): the two constants are ordered the way the plan says."""
    mcp = mcp_module()
    assert 0 < mcp.MCP_BUDGET_TOKENS <= mcp.MCP_MAX_BUDGET_TOKENS


def test_ac28_truncation_happens_on_a_line_boundary(big_index):
    """AC-28: a clamped result is still parseable markdown -- no half line."""
    mcp = mcp_module()
    idx = Index(big_index)
    out = mcp.tool_repo_search(idx, MINI_QUERY, k=20, budget_tokens=10**9)
    full = idx.pack_context(MINI_QUERY, k=20, budget_chars=0)["markdown"]
    full_lines = set(full.split("\n"))  # never splitlines(): see AGENTS.md
    body = out.split("\n")
    for line in body[:-1]:
        assert line in full_lines, line[:120]


# ==========================================================================
# AC-29 -- secrets never leave through an agent tool
# ==========================================================================


def test_ac29_repo_search_never_returns_a_secret_chunk(mini_index):
    """AC-29: the fixture's `.env` chunk is BM25 rank 1 for SECRET_QUERY and
    still must not appear; the same query with exclude_secrets=False does
    return it, which is what makes this a real test."""
    mcp = mcp_module()
    idx = Index(mini_index)

    leaky = idx.pack_context(SECRET_QUERY, budget_chars=0, exclude_secrets=False)
    leaky_paths = {c.get("path") or "" for c in leaky["chunks"]}
    assert any(_is_secret_path(p) for p in leaky_paths), leaky_paths
    assert ".env" in leaky["markdown"]

    top = idx.chunks[idx.score(SECRET_QUERY)[0][1]]
    assert _is_secret_path(top.get("path") or ""), top.get("path")

    out = mcp.tool_repo_search(idx, SECRET_QUERY)
    for header in out.split("### [cite: ")[1:]:
        path = header.split(":", 1)[0]
        assert not _is_secret_path(path), path
    assert "ACME_DEPLOYMENT_LEDGER_TOKEN" not in out
    assert "abc123deadbeef" not in out


def test_ac29_repo_map_and_neighbours_also_exclude_secrets(mini_index):
    """AC-29 (b): the other two tools must not become the leak instead."""
    mcp = mcp_module()
    idx = Index(mini_index)
    for text in (mcp.tool_repo_map(idx), mcp.tool_repo_neighbours(idx, "file:.env")):
        assert "abc123deadbeef" not in text
        assert "zzz999notreal" not in text


# ==========================================================================
# AC-30 -- repo_neighbours
# ==========================================================================


def test_ac30_neighbours_names_a_reachable_node_its_edge_and_direction(mini_index):
    """AC-30: route_request -> audit_event over CALLS out is in the fixture
    graph, so it must be named, with its edge type and its direction."""
    mcp = mcp_module()
    idx = Index(mini_index)

    expected = {
        (dst, etype, direction) for dst, etype, direction, _src in idx.expand([SYM_ROUTE], hops=1)
    }
    assert (SYM_AUDIT, "CALLS", "out") in expected, expected

    out = mcp.tool_repo_neighbours(idx, SYM_ROUTE)
    assert isinstance(out, str) and out.strip()
    assert "audit_event" in out, out
    assert "CALLS" in out, out
    assert "out" in out, out


def test_ac30_an_unknown_node_id_returns_a_short_message(mini_index):
    """AC-30 (b): not found, not a traceback, and not a wall of text."""
    mcp = mcp_module()
    idx = Index(mini_index)
    out = mcp.tool_repo_neighbours(idx, "sym:nowhere.py::nothing")
    assert isinstance(out, str)
    assert "not found" in out.lower(), out
    assert len(out) <= 400, len(out)


def test_ac30_neighbours_respects_its_limit(mini_index):
    """AC-30 (c): an agent-facing tool must be bounded here too."""
    mcp = mcp_module()
    idx = Index(mini_index)
    short = mcp.tool_repo_neighbours(idx, SYM_ROUTE, limit=1)
    longer = mcp.tool_repo_neighbours(idx, SYM_ROUTE, limit=50)
    assert len(short) <= len(longer)


# ==========================================================================
# AC-31 -- tool descriptions are context an agent pays for every session
# ==========================================================================


def test_ac31_tool_descriptions_stay_under_budget():
    """AC-31: the published tool set, capped under 2500 characters (~500 tokens).

    The character budget balances agent context overhead against Glama TDQS
    (Tool Definition Quality Standard) requirements. Tool descriptions are loaded
    into every agent's context every turn, so they must stay tightly bounded;
    however, they must also provide explicit usage guidance, sibling disambiguation,
    and safety disclosures.
    """
    mcp = mcp_module()
    assert set(mcp.TOOL_DESCRIPTIONS) == {
        "repo_map",
        "repo_search",
        "repo_neighbours",
        "repo_cache_stats",
        "repo_build_status",
    }
    for name, text in mcp.TOOL_DESCRIPTIONS.items():
        assert isinstance(text, str) and text.strip(), name
        assert 100 <= len(text) <= 800, (
            f"{name} length {len(text)} out of expected [100, 800] range"
        )
    total = sum(len(d) for d in mcp.TOOL_DESCRIPTIONS.values())
    assert total <= 2500, f"Combined tool descriptions ({total} chars) exceed 2500-char budget"


test_ac31_tool_descriptions_stay_under_600_chars = test_ac31_tool_descriptions_stay_under_budget


def test_tool_descriptions_contain_usage_guidance_and_siblings():
    """Glama TDQS: Every tool description must contain explicit usage guidance
    ('when to use' or 'use when') and cross-reference alternative sibling tools
    to prevent agent mis-routing.
    """
    mcp = mcp_module()
    all_tools = set(mcp.TOOL_DESCRIPTIONS)

    expected_siblings = {
        "repo_map": {"repo_search", "repo_neighbours"},
        "repo_search": {"repo_map", "repo_neighbours"},
        "repo_neighbours": {"repo_search", "repo_map"},
        "repo_cache_stats": {"repo_map", "repo_search"},
        "repo_build_status": {"repo_search", "repo_map"},
    }

    for name, desc in mcp.TOOL_DESCRIPTIONS.items():
        desc_lower = desc.lower()
        assert "when to use" in desc_lower or "use when" in desc_lower, (
            f"Tool {name!r} missing explicit usage guidance: {desc}"
        )
        siblings = all_tools - {name}
        referenced = {s for s in siblings if s in desc}
        assert referenced, f"Tool {name!r} does not mention any alternative sibling tools: {desc}"
        for expected in expected_siblings[name]:
            assert expected in desc, (
                f"Tool {name!r} should explicitly cross-reference sibling {expected!r}: {desc}"
            )


def test_tool_descriptions_disclose_read_only_behavior():
    """Glama TDQS / Safety: Every tool description must explicitly disclose
    read-only behavior so models know the operation cannot mutate repository state.
    """
    mcp = mcp_module()
    for name, desc in mcp.TOOL_DESCRIPTIONS.items():
        desc_lower = desc.lower()
        has_read_only = (
            "read-only" in desc_lower
            or "read only" in desc_lower
            or "does not modify" in desc_lower
        )
        assert has_read_only, f"Tool {name!r} does not disclose read-only behavior: {desc}"


def test_tool_schemas_have_informative_parameter_descriptions():
    """Glama TDQS: Tool schemas must provide clear, informative descriptions
    for all parameters including types, defaults, and constraints where applicable.
    """
    mcp = mcp_module()
    assert set(mcp.TOOL_SCHEMAS) == set(mcp.TOOL_DESCRIPTIONS)

    expected_required = {
        "repo_map": [],
        "repo_search": ["query"],
        "repo_neighbours": ["node_id"],
        "repo_cache_stats": [],
        "repo_build_status": ["task_id"],
    }
    bounded_params = {"k", "hops", "budget_tokens", "limit"}

    for name, schema in mcp.TOOL_SCHEMAS.items():
        assert schema.get("type") == "object", f"{name} schema type must be 'object'"
        properties = schema.get("properties", {})
        assert isinstance(properties, dict), f"{name} properties must be a dict"

        required = schema.get("required", [])
        assert required == expected_required[name], (
            f"{name} required fields mismatch: got {required}, expected {expected_required[name]}"
        )

        for param_name, param_meta in properties.items():
            assert "type" in param_meta, f"{name}.{param_name} missing 'type'"
            desc = param_meta.get("description", "")
            assert isinstance(desc, str) and desc.strip(), (
                f"{name}.{param_name} missing description"
            )
            assert len(desc) >= 20, (
                f"{name}.{param_name} description too short ({len(desc)} chars): {desc!r}"
            )
            if param_name in bounded_params:
                desc_lower = desc.lower()
                assert "max" in desc_lower or "default" in desc_lower, (
                    f"{name}.{param_name} should document max/default bounds: {desc!r}"
                )


def test_tool_annotations_constant_defined():
    """TOOL_ANNOTATIONS constant is defined at module level and declares
    read-only, non-destructive, and idempotent hints without needing the SDK.
    """
    mcp = mcp_module()
    assert hasattr(mcp, "TOOL_ANNOTATIONS"), "mcp module missing TOOL_ANNOTATIONS"
    assert mcp.TOOL_ANNOTATIONS.get("readOnlyHint") is True
    assert mcp.TOOL_ANNOTATIONS.get("destructiveHint") is False
    assert mcp.TOOL_ANNOTATIONS.get("idempotentHint") is True


@pytest.mark.skipif(not HAS_REAL_MCP, reason="needs repo2graph[mcp] with 1.x Server API")
def test_list_tools_returns_quality_annotations():
    """When running with the MCP SDK, get_tools() returns tools decorated
    with quality annotations (readOnlyHint=True, destructiveHint=False, idempotentHint=True).
    """
    mcp = mcp_module()
    tools = mcp.get_tools()
    assert len(tools) == len(mcp.TOOL_DESCRIPTIONS)

    for tool in tools:
        assert tool.name in mcp.TOOL_DESCRIPTIONS
        assert tool.description == mcp.TOOL_DESCRIPTIONS[tool.name]
        assert tool.inputSchema == mcp.TOOL_SCHEMAS[tool.name]
        assert getattr(tool, "annotations", None) is not None, (
            f"Tool {tool.name} missing annotations"
        )
        ann = tool.annotations
        read_only = getattr(ann, "readOnlyHint", None) or (
            ann.get("readOnlyHint") if isinstance(ann, dict) else None
        )
        assert read_only is True, f"{tool.name} readOnlyHint must be True"


# ==========================================================================
# AC-32 / AC-33 -- packaging and the console entry point
# ==========================================================================


def load_pyproject() -> dict:
    # tomllib is stdlib from 3.11; requires-python is >=3.10 and CI runs 3.10,
    # so guard it the way tests/test_rag.py already does rather than taking a
    # dependency on tomli just to read our own metadata.
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - py3.10
        pytest.skip("tomllib needs Python 3.11+")
    with open(PYPROJECT, "rb") as fh:
        return tomllib.load(fh)


def requirement_names(specs):
    out = set()
    for spec in specs:
        name = spec.split(";")[0].split("[")[0]
        for sep in ("==", ">=", "<=", "~=", "!=", ">", "<"):
            name = name.split(sep)[0]
        out.add(name.strip())
    return out


def test_ac32_mcp_is_an_optional_extra_with_a_console_script():
    """AC-32: `mcp` extra + `repo2graph-mcp` entry point."""
    data = load_pyproject()
    extras = data["project"]["optional-dependencies"]
    assert "mcp" in extras, sorted(extras)
    assert requirement_names(extras["mcp"]) == {"mcp"}, extras["mcp"]
    scripts = data["project"]["scripts"]
    assert scripts.get("repo2graph-mcp") == "repo2graph.mcp:main", scripts
    assert scripts.get("repo2graph") == "repo2graph.cli:main", scripts


def test_ac32_runtime_dependencies_are_still_only_tree_sitter():
    """AC-32: a bare `pip install repo2graph` brings in nothing new."""
    data = load_pyproject()
    assert requirement_names(data["project"]["dependencies"]) == {
        "tree-sitter",
        "tree-sitter-language-pack",
    }


def test_ac33_entry_point_without_the_sdk_explains_the_extra(mini_index, monkeypatch):
    """AC-33: a user without the extra gets an actionable message and a
    non-zero exit, never an ImportError traceback."""
    mcp = mcp_module()
    monkeypatch.setitem(sys.modules, "mcp", None)
    with pytest.raises(SystemExit) as exc:
        mcp.main(["--out", str(mini_index)])
    message = str(exc.value)
    assert 'pip install "repo2graph[mcp]"' in message, message
    assert exc.value.code not in (0, None)


def test_ac33_serve_without_the_sdk_raises_the_same_systemexit(mini_index, monkeypatch):
    """AC-33 (b): the guard lives at the import site, not only in main()."""
    mcp = mcp_module()
    monkeypatch.setitem(sys.modules, "mcp", None)
    with pytest.raises(SystemExit) as exc:
        mcp.serve(Path(mini_index))
    assert 'pip install "repo2graph[mcp]"' in str(exc.value)


def test_ac33_open_index_caches_one_index_per_directory(mini_index):
    """AC-33 (c): the server must not re-read the whole index per tool call."""
    mcp = mcp_module()
    first = mcp.open_index(mini_index)
    second = mcp.open_index(mini_index)
    assert first is second
    assert isinstance(first, Index)


# ==========================================================================
# AC-34 -- live stdio server round-trip
# ==========================================================================


@pytest.mark.skipif(not HAS_REAL_MCP, reason="needs repo2graph[mcp] with 1.x Server API")
def test_ac34_stdio_server_roundtrip(mini_index):
    """AC-34: automated round-trip against the live stdio server."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "repo2graph.mcp", "--out", str(mini_index)],
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

    try:
        init_resp = send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "clientInfo": {"name": "test", "version": "1.0"},
                    "capabilities": {},
                },
            }
        )
        assert "result" in init_resp
        assert init_resp["result"]["serverInfo"]["name"] == "repo2graph"

        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        )
        proc.stdin.flush()

        tools_resp = send(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            }
        )
        assert "result" in tools_resp
        tool_names = {t["name"] for t in tools_resp["result"]["tools"]}
        assert "repo_map" in tool_names
        assert "repo_search" in tool_names
        assert "repo_neighbours" in tool_names

        map_resp = send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "repo_map",
                    "arguments": {},
                },
            }
        )
        assert "result" in map_resp
        map_text = map_resp["result"]["content"][0]["text"]
        assert map_text.strip()
        assert "# Repo map:" in map_text

        search_resp = send(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "repo_search",
                    "arguments": {"query": MINI_QUERY},
                },
            }
        )
        assert "result" in search_resp
        search_text = search_resp["result"]["content"][0]["text"]
        assert "[cite:" in search_text

        neigh_resp = send(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "repo_neighbours",
                    "arguments": {"node_id": SYM_ROUTE},
                },
            }
        )
        assert "result" in neigh_resp
        neigh_text = neigh_resp["result"]["content"][0]["text"]
        assert "audit_event" in neigh_text
    finally:
        proc.stdin.close()
        proc.terminate()
        proc.wait()


# ==========================================================================
# Regressions -- REVIEW iteration 2
# ==========================================================================
#
# R-5: `k` and `hops` were coerced but never clamped. `Index.expand` runs
# `for _ in range(hops)` with no empty-frontier exit, so `hops=10**9` blocked
# serve()'s single event loop for ~a minute. Output size was already bounded;
# time was not. These assert the value that reaches the engine, not the value
# that comes back, because a clamp that only trims the answer is not a clamp.


@pytest.mark.parametrize("hops", [10**9, 10**12, 5, "99999", None, -3])
def test_r5_neighbours_hops_never_exceeds_the_ceiling(mini_index, hops):
    """R-5 (a): whatever a model writes into `hops`, expand() sees 0..MAX."""
    mcp = mcp_module()
    idx = Index(mini_index)
    seen = []
    real = idx.expand
    idx.expand = lambda seeds, **kw: (seen.append(kw.get("hops")), real(seeds, **kw))[1]

    out = mcp.tool_repo_neighbours(idx, SYM_ROUTE, hops=hops)
    assert isinstance(out, str) and out.strip()
    assert seen and all(0 <= h <= mcp.MCP_MAX_HOPS for h in seen), seen


@pytest.mark.parametrize("k,hops", [(10**9, 10**9), ("nonsense", -1), (0, 7)])
def test_r5_search_k_and_hops_never_exceed_their_ceilings(mini_index, k, hops):
    """R-5 (b): the same on the search path, where both arguments cost time."""
    mcp = mcp_module()
    idx = Index(mini_index)
    seen = {}
    real = idx.pack_context

    def spy(query, **kw):
        seen.update(kw)
        return real(query, **kw)

    idx.pack_context = spy
    out = mcp.tool_repo_search(idx, MINI_QUERY, k=k, hops=hops)
    assert isinstance(out, str)
    assert 1 <= seen["k"] <= mcp.MCP_MAX_K, seen
    assert 0 <= seen["hops"] <= mcp.MCP_MAX_HOPS, seen


def test_r5_dispatch_clamps_too(mini_index):
    """R-5 (c): the clamp lives in the handlers, so the JSON route inherits it."""
    mcp = mcp_module()
    idx = Index(mini_index)
    seen = {}
    real = idx.pack_context

    def spy(query, **kw):
        seen.update(kw)
        return real(query, **kw)

    idx.pack_context = spy
    mcp.dispatch(idx, "repo_search", {"query": MINI_QUERY, "k": 10**6, "hops": 10**6})
    assert seen["k"] == mcp.MCP_MAX_K
    assert seen["hops"] == mcp.MCP_MAX_HOPS


def test_r5_sane_arguments_are_left_alone(mini_index):
    """R-5 (d): the ceilings must not quietly rewrite ordinary calls."""
    mcp = mcp_module()
    idx = Index(mini_index)
    seen = {}
    real = idx.pack_context

    def spy(query, **kw):
        seen.update(kw)
        return real(query, **kw)

    idx.pack_context = spy
    mcp.tool_repo_search(idx, MINI_QUERY, k=3, hops=2)
    assert (seen["k"], seen["hops"]) == (3, 2)
    assert mcp.MCP_MAX_K > 8 and mcp.MCP_MAX_HOPS > 1, "a ceiling below the default"


# ==========================================================================
# Regression -- REVIEW iteration 3
# ==========================================================================
#
# R-7: `limit` on repo_neighbours had a floor and no ceiling, so the size of
# that answer was caller-controlled -- 35 414 characters at hops=4 limit=10**9
# against a 1 532-character default on an 895-node index. It is the one tool
# whose output no token budget measures, so the row count is the bound. The
# fixture graph is far smaller than any ceiling, so these drive expand() with a
# synthetic frontier: a clamp asserted against four real neighbours would pass
# with no clamp at all.


def _flood(idx, n=5000):
    """Make expand() yield more neighbours than any ceiling allows."""
    idx.expand = lambda seeds, **kw: [
        (f"sym:pkg/flood.py::n{i}", "CALLS", "out", seeds[0]) for i in range(n)
    ]


def _rows(text):
    return [ln for ln in text.split("\n") if ln.startswith("- ")]


@pytest.mark.parametrize("limit", [10**9, 10**12, "99999", 51])
def test_r7_neighbours_limit_never_exceeds_the_ceiling(mini_index, limit):
    """R-7 (a): whatever a model writes into `limit`, the reply is bounded."""
    mcp = mcp_module()
    idx = Index(mini_index)
    _flood(idx)
    out = mcp.tool_repo_neighbours(idx, SYM_ROUTE, limit=limit)
    assert len(_rows(out)) <= mcp.MCP_MAX_NEIGHBOURS, len(_rows(out))


def test_r7_the_default_limit_still_applies(mini_index):
    """R-7 (b): the ceiling did not become the default."""
    mcp = mcp_module()
    idx = Index(mini_index)
    _flood(idx)
    out = mcp.tool_repo_neighbours(idx, SYM_ROUTE)
    assert len(_rows(out)) <= mcp.MCP_NEIGHBOUR_LIMIT, len(_rows(out))
    assert mcp.MCP_MAX_NEIGHBOURS > mcp.MCP_NEIGHBOUR_LIMIT, "ceiling below default"


def test_r7_dispatch_inherits_the_limit_clamp(mini_index):
    """R-7 (c): the clamp lives in the handler, so the JSON route gets it too."""
    mcp = mcp_module()
    idx = Index(mini_index)
    _flood(idx)
    out = mcp.dispatch(idx, "repo_neighbours", {"node_id": SYM_ROUTE, "limit": 10**6})
    assert len(_rows(out)) <= mcp.MCP_MAX_NEIGHBOURS, len(_rows(out))


def test_r7_a_sane_limit_is_left_alone(mini_index):
    """R-7 (d): the ceiling must not quietly rewrite an ordinary request."""
    mcp = mcp_module()
    idx = Index(mini_index)
    _flood(idx)
    assert len(_rows(mcp.tool_repo_neighbours(idx, SYM_ROUTE, limit=7))) == 7
    assert len(_rows(mcp.tool_repo_neighbours(idx, SYM_ROUTE, limit=1))) == 1


# ==========================================================================
# Regressions -- VERIFY iteration 4
# ==========================================================================
#
# R-8: the extra declared `mcp>=1.0`, which resolves to mcp 2.x today. mcp 2.x
# removed the `Server.list_tools` / `Server.call_tool` decorators that serve()
# is written against, so `repo2graph-mcp` died with
# `AttributeError: 'Server' object has no attribute 'list_tools'` -- a raw
# traceback that the old `_require_sdk()` could not catch, because `import mcp`
# succeeds perfectly well on 2.x. The extra is now bounded below 2, and the
# guard checks the API surface rather than the mere importability of the
# package. The fake SDK below is what makes this testable without installing
# any SDK at all: the suite still never imports the real `mcp`.


def _fake_sdk(monkeypatch, *, decorators: bool, version="2.2.0", with_server_module=True):
    """Install a minimal stand-in for the `mcp` package in sys.modules.

    `decorators=False` reproduces the 2.x shape: importable, with a `Server`
    class that has no `list_tools`/`call_tool`.
    """
    import types as _types

    mcp_pkg = _types.ModuleType("mcp")
    mcp_pkg.__version__ = version

    def _noop_decorator(self):
        def register(fn):
            return fn

        return register

    class Server:
        # Mirrors the real SDK's signature. `version` matters: left unset there,
        # the SDK reports *its own* version as the server's, so the fake has to
        # be able to record what it was actually given.
        last: dict = {}

        def __init__(self, name, version=None, **kw):
            self.name = name
            self.version = version
            Server.last = {"name": name, "version": version, **kw}

        def create_initialization_options(self):
            return {}

    if decorators:
        Server.list_tools = _noop_decorator
        Server.call_tool = _noop_decorator

    modules = {"mcp": mcp_pkg}
    if with_server_module:
        server_mod = _types.ModuleType("mcp.server")
        server_mod.Server = Server
        stdio_mod = _types.ModuleType("mcp.server.stdio")
        stdio_mod.stdio_server = None

        class FakeTool:
            def __init__(
                self, name=None, description=None, inputSchema=None, annotations=None, **kw
            ):
                self.name = name
                self.description = description
                self.inputSchema = inputSchema
                self.annotations = annotations

        types_mod = _types.ModuleType("mcp.types")
        types_mod.TextContent = object
        types_mod.Tool = FakeTool
        types_mod.ToolAnnotations = dict
        mcp_pkg.server = server_mod
        server_mod.stdio = stdio_mod
        mcp_pkg.types = types_mod
        modules.update(
            {"mcp.server": server_mod, "mcp.server.stdio": stdio_mod, "mcp.types": types_mod}
        )
    else:
        monkeypatch.setitem(sys.modules, "mcp.server", None)
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return mcp_pkg


def test_r8_a_broken_server_module_is_also_an_instruction(mini_index, monkeypatch):
    """R-8 (c): the other way a future SDK can move -- `mcp` imports but
    `mcp.server` does not."""
    mcp = mcp_module()
    _fake_sdk(monkeypatch, decorators=False, with_server_module=False)
    with pytest.raises(SystemExit) as exc:
        mcp._require_sdk()
    assert "mcp>=1.0,<3.0" in str(exc.value), str(exc.value)


def test_r8_a_supported_sdk_passes_the_guard(monkeypatch):
    """R-8 (d): the guard must not become a blanket refusal -- an SDK that
    does carry the API serve() needs is accepted."""
    mcp = mcp_module()
    fake = _fake_sdk(monkeypatch, decorators=True, version="1.9.0")
    assert mcp._require_sdk() is fake


def test_r8_the_missing_sdk_message_is_still_the_missing_sdk_message(mini_index, monkeypatch):
    """R-8 (e): absent and unusable are different problems with different
    instructions; the new branch must not swallow AC-33's."""
    mcp = mcp_module()
    monkeypatch.setitem(sys.modules, "mcp", None)
    with pytest.raises(SystemExit) as exc:
        mcp.serve(Path(mini_index))
    assert 'pip install "repo2graph[mcp]"' in str(exc.value)
    assert "not supported" not in str(exc.value)


def test_r8_the_extra_is_bounded_below_the_unsupported_major():
    """R-8 (f): the declared extra and the guard's advice are one string."""
    mcp = mcp_module()
    spec = load_pyproject()["project"]["optional-dependencies"]["mcp"]
    assert spec == [mcp.SDK_SPEC], (spec, mcp.SDK_SPEC)
    assert "<3.0" in mcp.SDK_SPEC


# ==========================================================================
# R-10 -- a floor-clamped budget must not hand an agent an empty string
# ==========================================================================


@pytest.mark.parametrize("budget", [0, -5, -(10**9), 1])
def test_r10_a_floor_clamped_budget_explains_itself(big_index, budget):
    """R-10 (a): AC-28 only requires "no crash"; an empty tool result reads to
    an agent exactly like "no such code", so say which it was."""
    mcp = mcp_module()
    idx = Index(big_index)
    out = mcp.tool_repo_search(idx, MINI_QUERY, k=20, budget_tokens=budget)
    assert out.strip(), "an empty string is the thing this test exists to stop"
    assert "budget_tokens" in out and str(mcp.MCP_MAX_BUDGET_TOKENS) in out
    assert tokens(out) <= mcp.MCP_MAX_BUDGET_TOKENS


def test_r10_the_note_is_short_and_never_the_normal_answer(big_index):
    """R-10 (b): a real pack must not be replaced by the note, and the note
    must stay far below the default budget so it cannot itself be trimmed."""
    mcp = mcp_module()
    idx = Index(big_index)
    real = mcp.tool_repo_search(idx, MINI_QUERY)
    assert "### [cite:" in real
    assert "no content fit in" not in real
    assert tokens(mcp.EMPTY_RESULT.format(budget=1, ceiling=12000)) < 200


def test_r10_dispatch_inherits_the_note(big_index):
    """R-10 (c): the note lives in the handler, so the JSON route gets it too."""
    mcp = mcp_module()
    idx = Index(big_index)
    out = mcp.dispatch(idx, "repo_search", {"query": MINI_QUERY, "budget_tokens": -5})
    assert out.strip() and "budget_tokens" in out


# ==========================================================================
# Index preflight and neighbours truncation
# ==========================================================================


def test_open_index_missing_directory_raises_clean_systemexit(tmp_path):
    mcp = mcp_module()
    missing = tmp_path / "nonexistent"
    with pytest.raises(SystemExit) as exc:
        mcp.open_index(missing)
    msg = str(exc.value)
    assert f"error: no repo2graph index found at '{missing}'" in msg
    assert f"Build one first with: repo2graph build <path> -o {missing}" in msg


def test_open_index_missing_chunks_jsonl_raises_clean_systemexit(tmp_path):
    mcp = mcp_module()
    empty = tmp_path / "empty_idx"
    empty.mkdir()
    with pytest.raises(SystemExit) as exc:
        mcp.open_index(empty)
    msg = str(exc.value)
    assert f"error: no repo2graph index found at '{empty}'" in msg


def test_serve_preflight_checks_index(monkeypatch, tmp_path):
    mcp = mcp_module()
    _fake_sdk(monkeypatch, decorators=True, version="1.9.0")
    missing = tmp_path / "missing_idx"
    with pytest.raises(SystemExit) as exc:
        mcp.serve(missing)
    assert f"error: no repo2graph index found at '{missing}'" in str(exc.value)


def test_neighbours_truncation_indicates_limit_when_exceeded(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    _flood(idx)
    out = mcp.tool_repo_neighbours(idx, SYM_ROUTE, limit=5)
    rows = _rows(out)
    assert len(rows) == 5
    assert "... (truncated at 5 neighbours)" in out


def test_neighbours_no_truncation_when_within_limit(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    out = mcp.tool_repo_neighbours(idx, SYM_ROUTE, limit=50)
    assert "truncated" not in out


# ==========================================================================
# Auto-build: a server pointed at a repo indexes it rather than erroring out
# ==========================================================================


def test_auto_build_indexes_a_repo_that_has_no_index_yet(mini_repo, tmp_path):
    """The onboarding fix: point it at a repo, get a working Index, no error."""
    mcp = mcp_module()
    out = tmp_path / "built_idx"
    assert not out.exists()

    index = mcp.open_index(out, repo=mini_repo)

    assert (out / "agent" / "chunks.jsonl").is_file()
    assert index.nodes, "the built index should carry nodes"
    assert mcp.tool_repo_map(index).strip(), "repo_map should answer off it"


def test_auto_build_only_writes_the_formats_a_server_reads(mini_repo, tmp_path):
    """html/graphml/cypher cost real time and no tool reads them."""
    mcp = mcp_module()
    out = tmp_path / "lean_idx"
    mcp.open_index(out, repo=mini_repo)

    written = {p.name for p in out.rglob("*") if p.is_file()}
    assert "chunks.jsonl" in written
    assert "overview.md" in written
    assert not {"graph.html", "graph.graphml", "graph.cypher"} & written, written


def test_auto_build_writes_nothing_to_stdout(mini_repo, tmp_path, capsys):
    """stdout is the JSON-RPC transport: one stray print drops the connection."""
    mcp = mcp_module()
    mcp._build_index(mini_repo, tmp_path / "quiet_idx")
    captured = capsys.readouterr()
    assert captured.out == "", repr(captured.out)


def test_auto_build_is_opt_in_so_a_bare_open_index_still_refuses(tmp_path):
    """Without a repo to build from, the old error is still the behaviour."""
    mcp = mcp_module()
    missing = tmp_path / "nope"
    with pytest.raises(SystemExit) as exc:
        mcp.open_index(missing)
    assert f"error: no repo2graph index found at '{missing}'" in str(exc.value)


def test_an_existing_index_is_never_rebuilt(mini_index, mini_repo, monkeypatch):
    """Passing a repo must not cost a rebuild when the index is already there."""
    mcp = mcp_module()
    mcp._INDEXES.clear()

    def _boom(*a, **k):
        raise AssertionError("rebuilt an index that already existed")

    monkeypatch.setattr(mcp, "_build_index", _boom)
    assert mcp.open_index(mini_index, repo=mini_repo).nodes


def test_serve_does_not_build_during_the_handshake(mini_repo, tmp_path, monkeypatch):
    """The build belongs on the first tool call, not before `initialize`.

    Blocking the handshake for the minute a large repo takes to parse is what
    makes a client declare the server dead, so serve() must return to its event
    loop without having touched the repo.
    """
    import asyncio

    mcp = mcp_module()
    mcp._INDEXES.clear()
    _fake_sdk(monkeypatch, decorators=True, version="1.9.0")
    monkeypatch.setattr(
        mcp, "_build_index", lambda *a, **k: pytest.fail("built during the handshake")
    )
    monkeypatch.setattr(asyncio, "run", lambda coro: coro.close())

    out = tmp_path / "deferred_idx"
    mcp.serve(out, repo=mini_repo)  # must not raise, must not build
    assert not out.exists()


def test_serve_without_a_repo_still_preflights(monkeypatch, tmp_path):
    """--no-auto-build territory: nothing to build from, so fail fast."""
    mcp = mcp_module()
    _fake_sdk(monkeypatch, decorators=True, version="1.9.0")
    missing = tmp_path / "missing_idx"
    with pytest.raises(SystemExit) as exc:
        mcp.serve(missing)
    assert f"error: no repo2graph index found at '{missing}'" in str(exc.value)


# ----------------------------------------------------------- resolve_paths --


def test_resolve_paths_positional_repo_defaults_the_index_inside_it(tmp_path):
    mcp = mcp_module()
    out, repo = mcp.resolve_paths(repo=tmp_path)
    assert out == tmp_path / ".r2g"
    assert repo == tmp_path


def test_resolve_paths_infers_the_repo_from_a_conventional_out(tmp_path):
    """`<repo>/.r2g` is the convention every doc and example uses."""
    mcp = mcp_module()
    out, repo = mcp.resolve_paths(out=tmp_path / ".r2g")
    assert out == tmp_path / ".r2g"
    assert repo == tmp_path


def test_resolve_paths_refuses_to_guess_from_an_unconventional_out(tmp_path):
    """--out /var/cache/indexes/myproj must not end up parsing the cache dir."""
    mcp = mcp_module()
    elsewhere = tmp_path / "indexes" / "myproj"
    elsewhere.mkdir(parents=True)
    out, repo = mcp.resolve_paths(out=elsewhere)
    assert out == elsewhere
    assert repo is None, "an out path that is not named .r2g is not a repo hint"


def test_resolve_paths_an_explicit_repo_that_is_not_a_directory_is_an_error(tmp_path):
    mcp = mcp_module()
    with pytest.raises(SystemExit) as exc:
        mcp.resolve_paths(repo=tmp_path / "not-there")
    assert "not a directory" in str(exc.value)


def test_resolve_paths_an_explicit_out_wins_over_the_default(tmp_path):
    mcp = mcp_module()
    out, repo = mcp.resolve_paths(repo=tmp_path, out=tmp_path / "custom")
    assert out == tmp_path / "custom"
    assert repo == tmp_path


def test_no_auto_build_flag_turns_the_repo_off(mini_repo, monkeypatch):
    """--no-auto-build must reach serve() as repo=None."""
    mcp = mcp_module()
    seen = {}
    monkeypatch.setattr(
        mcp, "serve", lambda out, repo=None, **kw: seen.update(out=out, repo=repo, **kw)
    )

    mcp.main([str(mini_repo), "--no-auto-build"])
    assert seen["repo"] is None

    seen.clear()
    mcp.main([str(mini_repo)])
    assert seen["repo"] == Path(mini_repo)


def test_http_only_without_port_errors_and_skips_stdio(mini_repo, monkeypatch):
    """#203: --http-only with no HTTP transport must not fall through to stdio."""
    mcp = mcp_module()
    served = []
    closed = []
    monkeypatch.setattr(mcp, "serve", lambda *a, **k: served.append(True))
    from repo2graph.audit import AuditLogger

    real_close = AuditLogger.close

    def _close(self):
        closed.append(True)
        return real_close(self)

    monkeypatch.setattr(AuditLogger, "close", _close)

    with pytest.raises(SystemExit, match="--http-only needs --http-port") as exc:
        mcp.main([str(mini_repo), "--http-only"])
    assert exc.value.code not in (0, None)
    assert served == []
    assert closed == [True]


@pytest.mark.parametrize(
    "flags",
    (
        ["--http-only", "--http-port", "8765"],
        ["--http-only", "--well-known-port", "8765"],
    ),
)
def test_http_only_with_port_starts_http_and_closes_audit(mini_repo, monkeypatch, flags):
    """#203: HTTP-only with a port never reaches stdio and still closes audit."""
    mcp = mcp_module()
    served = []
    transports = []
    closed = []
    monkeypatch.setattr(mcp, "serve", lambda *a, **k: served.append(True))

    class FakeTransport:
        def __init__(self, *args, **kwargs):
            self._thread = None
            self.started = False
            self.stopped = False
            transports.append(self)

        def start(self):
            self.started = True
            return 8765

        def stop(self):
            self.stopped = True

    monkeypatch.setattr("repo2graph.http_server.HTTPTransport", FakeTransport)
    from repo2graph.audit import AuditLogger

    real_close = AuditLogger.close

    def _close(self):
        closed.append(True)
        return real_close(self)

    monkeypatch.setattr(AuditLogger, "close", _close)

    assert mcp.main([str(mini_repo), *flags]) == 0
    assert served == []
    assert len(transports) == 1
    assert transports[0].started and transports[0].stopped
    assert closed == [True]


@pytest.mark.skipif(not HAS_REAL_MCP, reason="needs repo2graph[mcp] with 1.x Server API")
def test_stdio_roundtrip_against_a_repo_with_no_index(mini_repo):
    """The onboarding journey, end to end: add the server, ask, get an answer.

    Before auto-build this sequence ended at `error: no repo2graph index found`.
    It also proves the build does not corrupt the transport -- the handshake and
    every later frame have to parse as JSON-RPC with a build interleaved.
    """
    proc = subprocess.Popen(
        [sys.executable, "-m", "repo2graph.mcp", str(mini_repo)],
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

    try:
        init_resp = send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "clientInfo": {"name": "test", "version": "1.0"},
                    "capabilities": {},
                },
            }
        )
        assert "result" in init_resp, init_resp
        assert not (mini_repo / ".r2g").exists(), "the handshake must not have waited on a build"

        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        )
        proc.stdin.flush()

        search_resp = send(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "repo_search", "arguments": {"query": MINI_QUERY}},
            }
        )
        assert "result" in search_resp, search_resp
        assert "[cite:" in search_resp["result"]["content"][0]["text"]

        # The build landed on disk, so the next process starts instantly.
        assert (mini_repo / ".r2g" / "agent" / "chunks.jsonl").is_file()
    finally:
        proc.stdin.close()
        proc.terminate()
        proc.wait()


# ==========================================================================
# The git subprocesses must never inherit this process's stdin
# ==========================================================================
#
# `capture_output=True` redirects the child's stdout and stderr and leaves
# stdin inherited. Under the MCP server that handle is the client's JSON-RPC
# pipe, so git blocked on it until its own timeout expired: auto-build took
# 60.7s instead of 0.6s and then fell back to _walk_files as if git were
# absent. A child holding that pipe can also consume frames addressed to us.
# These assert the redirect at both call sites, because the symptom is a
# silent stall on one platform rather than a failure anywhere.


@pytest.mark.parametrize("call", ["ls_files", "log"])
def test_git_subprocesses_never_inherit_stdin(monkeypatch, tmp_path, call):
    import subprocess as sp

    seen = {}

    def spy(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        raise OSError("not actually running git")

    if call == "ls_files":
        from repo2graph import parse as mod

        target, run = mod, lambda: mod._git_files(tmp_path)
    else:
        from repo2graph import graph as mod

        target, run = mod, lambda: mod.add_cochange(mod.Graph(tmp_path, "t"), tmp_path, 10, set())
    monkeypatch.setattr(target.subprocess, "run", spy)
    run()  # the OSError is caught by the caller; we only want the kwargs

    assert seen["kwargs"].get("stdin") is sp.DEVNULL, (
        f"{call} would inherit the MCP transport on stdin: {seen['kwargs']}"
    )


def test_auto_build_never_spawns_a_process_pool(monkeypatch, tmp_path):
    """`build()` reaches for a ProcessPoolExecutor above PARALLEL_MIN_FILES
    files, and spawning one from inside the running stdio server hangs: the
    workers inherit the parent's stdin/stdout, which are the client's pipes,
    and the tool call never returns.

    Asserted on the argument rather than end to end on purpose -- the failure
    mode is an indefinite hang, and a test that reproduces it would wedge CI
    rather than fail it. The fixtures elsewhere in this file are all under the
    threshold, which is exactly why this went unnoticed until a 65-file repo.
    """
    mcp = mcp_module()
    seen = {}

    def spy(repo, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop here; the kwargs are the whole assertion")

    monkeypatch.setattr("repo2graph.graph.build", spy)
    with pytest.raises(RuntimeError):
        mcp._build_index(tmp_path, tmp_path / "idx")

    assert seen.get("jobs") == 1, f"auto-build must stay single-process inside the server: {seen}"


# ==========================================================================
# serverInfo -- what the server says it is
# ==========================================================================


def test_serve_reports_its_own_version_not_the_sdks(mini_index, monkeypatch):
    """`Server(name)` without `version=` makes the SDK report *its* version.

    The MCP SDK fills serverInfo.version from its own package when the server
    does not supply one, so every client was told repo2graph was whatever
    release of `mcp` happened to be installed -- 1.30.0 against a 1.4.0
    package. It also put the two transports in disagreement, since the HTTP
    one has always reported __version__ correctly.
    """
    mcp = mcp_module()
    from repo2graph import __version__

    sdk = _fake_sdk(monkeypatch, decorators=True, version="99.99.99")
    Server = sdk.server.Server

    # stdio_server is None in the fake, so serve() gets as far as constructing
    # the Server and wiring the handlers, then fails on the transport. That is
    # exactly far enough to see what it passed.
    with pytest.raises(Exception):
        mcp.serve(Path(mini_index))

    assert Server.last["name"] == "repo2graph"
    assert Server.last["version"] == __version__, Server.last
    assert Server.last["version"] != "99.99.99", (
        "serverInfo is reporting the SDK's version as the server's"
    )


def test_both_transports_agree_on_the_version():
    """Coherence: stdio and HTTP must not describe themselves differently."""
    from repo2graph import __version__
    from repo2graph.http_server import server_metadata

    assert server_metadata(None, False, ["none"])["version"] == __version__


# ==========================================================================
# R-9: string arguments (query, node_id, task_id) had no length ceiling.
# Every numeric MCP argument was clamped in the handler, but a model can hand
# this dispatcher an arbitrarily long string and nothing bounded it -- the
# string-typed half of "every MCP tool argument is caller-hostile".
# ==========================================================================


def test_r9_an_absurdly_long_query_is_capped_before_it_reaches_the_index(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    seen = {}
    real = idx.pack_context

    def spy(query, **kw):
        seen["query"] = query
        return real(query, **kw)

    idx.pack_context = spy
    huge = "x" * 10_000_000
    out = mcp.tool_repo_search(idx, huge)
    assert isinstance(out, str)
    assert len(seen["query"]) <= mcp.MCP_MAX_QUERY_CHARS, len(seen["query"])


def test_r9_dispatch_caps_query_too(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    seen = {}
    real = idx.pack_context

    def spy(query, **kw):
        seen["query"] = query
        return real(query, **kw)

    idx.pack_context = spy
    mcp.dispatch(idx, "repo_search", {"query": "y" * 10_000_000})
    assert len(seen["query"]) <= mcp.MCP_MAX_QUERY_CHARS


def test_r9_an_absurdly_long_node_id_does_not_raise(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    huge = "sym:" + "z" * 10_000_000
    out = mcp.dispatch(idx, "repo_neighbours", {"node_id": huge})
    assert isinstance(out, str) and "node not found" in out


def test_r9_an_absurdly_long_task_id_does_not_raise():
    mcp = mcp_module()
    huge = "t" * 10_000_000
    out = mcp.dispatch(None, "repo_build_status", {"task_id": huge})
    assert isinstance(out, str)


def test_r9_sane_string_arguments_are_left_alone(mini_index):
    """The ceiling must not quietly rewrite ordinary calls."""
    mcp = mcp_module()
    idx = Index(mini_index)
    seen = {}
    real = idx.pack_context

    def spy(query, **kw):
        seen["query"] = query
        return real(query, **kw)

    idx.pack_context = spy
    mcp.tool_repo_search(idx, MINI_QUERY)
    assert seen["query"] == MINI_QUERY
