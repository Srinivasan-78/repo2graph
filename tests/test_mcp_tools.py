"""#384/#385/#386/#387 -- repo_find_symbol, repo_read, repo_path_between,
repo_blast_radius.

#387 named its tool `repo_impact`; that name is already the PR/diff-impact
tool `repo2graph/mcp.py` shipped earlier, so the reverse-reachability tool
below ships as `repo_blast_radius` instead (see its docstring and
`docs/mcp.md`).

Follows the shape of `tests/test_mcp.py`: no `mcp` SDK import required for
the handler-level tests, `_flood()`-style monkeypatching to prove a ceiling
actually binds rather than merely not being exceeded on a tiny fixture, and
literal `(node_id, ...)` assertions hand-derived from the `mini_repo` fixture
rather than a value the code under test also computed.
"""

import json

from conftest import SYM_AUDIT, SYM_ROUTE, build_mini_index
from repo2graph.query import Index

FILE_GATEWAY = "file:pkg/gateway.py"
FILE_AUDIT = "file:pkg/audit.py"
FILE_INIT = "file:pkg/__init__.py"
FILE_NOTES = "file:docs/notes.md"
FILE_ENV = "file:.env"


def mcp_module():
    from repo2graph import mcp as mcp_mod

    return mcp_mod


# ==========================================================================
# #384 -- repo_find_symbol
# ==========================================================================


def test_find_symbol_exact_name_returns_the_node(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    out = json.loads(mcp.tool_repo_find_symbol(idx, "route_request"))
    assert out == [
        {
            "node_id": SYM_ROUTE,
            "name": "route_request",
            "qualname": "route_request",
            "kind": "function",
            "path": "pkg/gateway.py",
            "start_line": 5,
            "end_line": 13,
            "lang": "python",
        }
    ]


def test_find_symbol_result_feeds_repo_neighbours_with_no_repo_search(mini_index):
    """Acceptance: the id it returns works in repo_neighbours directly."""
    mcp = mcp_module()
    idx = Index(mini_index)
    node_id = json.loads(mcp.tool_repo_find_symbol(idx, "route_request"))[0]["node_id"]
    out = mcp.tool_repo_neighbours(idx, node_id)
    assert "audit_event" in out and "not found" not in out.lower()


def test_find_symbol_is_case_insensitive_on_the_second_tier(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    out = json.loads(mcp.tool_repo_find_symbol(idx, "ROUTE_REQUEST"))
    assert [r["node_id"] for r in out] == [SYM_ROUTE]


def test_find_symbol_unknown_name_returns_empty_list_not_an_error(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    out = mcp.tool_repo_find_symbol(idx, "totally_nonexistent_symbol_xyz")
    assert not isinstance(out, mcp.ToolError)
    assert json.loads(out) == []


def test_find_symbol_empty_name_is_an_error(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    out = mcp.tool_repo_find_symbol(idx, "")
    assert isinstance(out, mcp.ToolError)


def test_find_symbol_never_returns_a_secret_path_node(mini_index):
    """the secret filter rule's rule extended to the new tool: a name inside `.env` must not
    surface a secret-path node id."""
    mcp = mcp_module()
    idx = Index(mini_index)
    out = json.loads(mcp.tool_repo_find_symbol(idx, ".env"))
    assert out == []
    out2 = json.loads(mcp.tool_repo_find_symbol(idx, "notes.md"))
    assert all(not idx._is_secret_path(r["path"]) for r in out2)


def test_find_symbol_ambiguous_name_returns_every_candidate(mini_index):
    """Two functions named the same thing in different files must both come
    back, not just one -- the caller disambiguates, not the server."""
    mcp = mcp_module()
    idx = Index(mini_index)
    # Synthesize an ambiguous name by pointing two node ids at "audit_event":
    # a fixture with a real second definition would require a second module,
    # so the aux cache is monkeypatched directly (it is index-local state
    # rebuilt on demand, not query.py) to prove the *handler* returns every
    # candidate in a tier rather than only the first.
    extra_id = "sym:pkg/other.py::audit_event"
    idx.nodes[extra_id] = {
        "id": extra_id,
        "name": "audit_event",
        "qualname": "audit_event",
        "kind": "function",
        "path": "pkg/other.py",
        "lang": "python",
        "start_line": 1,
        "end_line": 2,
    }
    out = json.loads(mcp.tool_repo_find_symbol(idx, "audit_event"))
    assert {r["node_id"] for r in out} == {SYM_AUDIT, extra_id}


def test_find_symbol_limit_is_clamped_in_the_handler(mini_index, tmp_path):
    """Detector proof lives in the flood test below; this pins the ceiling
    constant relationship the way test_ac28_ceiling_is_above_the_default does."""
    mcp = mcp_module()
    assert 0 < mcp.MCP_FIND_LIMIT <= mcp.MCP_MAX_FIND_LIMIT


def _flood_symbol_index(idx, mcp, n):
    """Make repo_find_symbol's own lookup see far more than any ceiling
    allows -- the same shape as test_mcp.py's `_flood()` for repo_neighbours."""
    extra = [f"sym:pkg/flood.py::dup{i}" for i in range(n)]
    for nid in extra:
        idx.nodes[nid] = {
            "id": nid,
            "name": "dup",
            "qualname": "dup",
            "kind": "function",
            "path": "pkg/flood.py",
            "lang": "python",
            "start_line": 1,
            "end_line": 2,
        }
    mcp._AUX_CACHE.pop(idx, None)


def test_find_symbol_limit_ceiling_actually_binds_under_flood(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    _flood_symbol_index(idx, mcp, 5000)
    out = json.loads(mcp.tool_repo_find_symbol(idx, "dup", limit=10**9))
    assert len(out) <= mcp.MCP_MAX_FIND_LIMIT, len(out)
    assert len(out) < 5000


def test_find_symbol_default_limit_still_applies_under_flood(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    _flood_symbol_index(idx, mcp, 5000)
    out = json.loads(mcp.tool_repo_find_symbol(idx, "dup"))
    assert len(out) <= mcp.MCP_FIND_LIMIT
    assert mcp.MCP_MAX_FIND_LIMIT > mcp.MCP_FIND_LIMIT


def test_find_symbol_reachable_via_dispatch(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    out = json.loads(mcp.dispatch(idx, "repo_find_symbol", {"name": "route_request"}))
    assert out and out[0]["node_id"] == SYM_ROUTE


def test_find_symbol_is_in_the_served_tool_set():
    mcp = mcp_module()
    assert "repo_find_symbol" in mcp.TOOL_DESCRIPTIONS
    assert "repo_find_symbol" in mcp.TOOL_SCHEMAS
    assert "repo_find_symbol" in mcp.server.tools


# ==========================================================================
# #385 -- repo_read
# ==========================================================================


def test_read_widens_a_citation_window_from_chunks_not_disk(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    out = mcp.tool_repo_read(idx, "pkg/gateway.py", start_line=6, end_line=8)
    assert not isinstance(out, mcp.ToolError)
    assert out.startswith("### [cite: pkg/gateway.py:6-8]")
    assert "Dispatch one inbound request to its handler" in out
    assert "def route_request" not in out  # line 6-8 only, not the def line


def test_read_context_widens_the_window_on_each_side(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    out = mcp.tool_repo_read(idx, "pkg/gateway.py", start_line=7, end_line=7, context=1)
    assert out.startswith("### [cite: pkg/gateway.py:6-8]")


def test_read_works_with_no_filesystem_access(mini_index, monkeypatch):
    """Acceptance: reads from the chunk text, never opens the source file."""
    mcp = mcp_module()
    idx = Index(mini_index)

    def _boom(*a, **kw):
        raise AssertionError("repo_read must not touch the filesystem")

    monkeypatch.setattr("builtins.open", _boom)
    out = mcp.tool_repo_read(idx, "pkg/gateway.py", start_line=6, end_line=8)
    assert "Dispatch one inbound request" in out


def test_read_refuses_an_absolute_path(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    for bad in ("/etc/passwd", "C:/Windows/win.ini", "C:\\Windows\\win.ini", "//server/share"):
        out = mcp.tool_repo_read(idx, bad)
        assert isinstance(out, mcp.ToolError), bad
        assert "absolute" in out.lower()


def test_read_refuses_a_dotdot_segment(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    out = mcp.tool_repo_read(idx, "pkg/../../../etc/passwd")
    assert isinstance(out, mcp.ToolError)
    assert ".." in out


def test_read_secret_excluded_path_returns_not_indexed_never_content(mini_index):
    """Negative assertion, per #385's acceptance: the secret content must
    never appear in the reply, under any path spelling."""
    mcp = mcp_module()
    idx = Index(mini_index)
    out = mcp.tool_repo_read(idx, ".env", start_line=1, end_line=4)
    assert isinstance(out, mcp.ToolError)
    assert "not indexed" in out
    assert "ACME_DEPLOYMENT_LEDGER_TOKEN" not in out
    assert "abc123deadbeef" not in out


def test_read_a_span_not_covered_by_any_chunk_says_not_indexed(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    out = mcp.tool_repo_read(idx, "pkg/gateway.py", start_line=10000, end_line=10001)
    assert isinstance(out, mcp.ToolError)
    assert "not indexed" in out


def test_read_unknown_path_says_not_indexed(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    out = mcp.tool_repo_read(idx, "pkg/does_not_exist.py")
    assert isinstance(out, mcp.ToolError)
    assert "not indexed" in out


def test_read_output_is_clamped_and_the_ceiling_actually_binds(tmp_path):
    """Flood proof: many small, back-to-back functions (each well under
    chunks.py's own 4000-char per-chunk split, so each is a single,
    trustworthy chunk -- AGENTS.md's own note that a file must have real
    content to carry a chunk is why they are not one giant function) whose
    *combined* widened read exceeds MCP_MAX_READ_CHARS."""
    mcp = mcp_module()
    repo = tmp_path / "src"
    pkg = repo / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf8", newline="\n")
    # No blank lines between defs: every line belongs to some symbol's span,
    # so the whole file is covered with no file_residual gap to trip on.
    funcs = [f"def f{i}():\n    return {i}\n" for i in range(1200)]
    src = "".join(funcs)
    (pkg / "many.py").write_text(src, encoding="utf8", newline="\n")
    out_dir = build_mini_index(repo, tmp_path / "idx")
    idx = Index(out_dir)
    total_lines = src.count("\n")
    assert len(src) > mcp.MCP_MAX_READ_CHARS, "fixture too small to exercise the ceiling"
    out = mcp.tool_repo_read(idx, "pkg/many.py", start_line=1, end_line=total_lines)
    assert not isinstance(out, mcp.ToolError), out[:200]
    assert len(out) <= mcp.MCP_MAX_READ_CHARS + 500  # header + truncation notice slack
    assert len(out) < len(src)


def test_read_reachable_via_dispatch(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    out = mcp.dispatch(idx, "repo_read", {"path": "pkg/gateway.py", "start_line": 6, "end_line": 8})
    assert "Dispatch one inbound request" in out


def test_read_is_in_the_served_tool_set():
    mcp = mcp_module()
    assert "repo_read" in mcp.TOOL_DESCRIPTIONS
    assert "repo_read" in mcp.TOOL_SCHEMAS
    assert "repo_read" in mcp.server.tools


# ==========================================================================
# #386 -- repo_path_between
# ==========================================================================


def test_path_between_finds_the_direct_calls_edge(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    out = json.loads(mcp.tool_repo_path_between(idx, SYM_ROUTE, SYM_AUDIT))
    assert out["paths"], out
    ids = [step["node_id"] for step in out["paths"][0]]
    assert ids == [SYM_ROUTE, SYM_AUDIT]
    assert out["paths"][0][1]["via_edge"] == "CALLS out"
    assert out["paths"][0][1]["confidence"] == 1.0


def test_path_between_finds_the_imports_edge_between_files(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    out = json.loads(mcp.tool_repo_path_between(idx, FILE_GATEWAY, FILE_AUDIT))
    ids = [step["node_id"] for step in out["paths"][0]]
    assert ids == [FILE_GATEWAY, FILE_AUDIT]
    assert out["paths"][0][1]["via_edge"] == "IMPORTS out"


def test_path_between_is_symmetric_in_direction(mini_index):
    """The same edge found walking the graph the other way, direction flipped."""
    mcp = mcp_module()
    idx = Index(mini_index)
    out = json.loads(mcp.tool_repo_path_between(idx, SYM_AUDIT, SYM_ROUTE))
    ids = [step["node_id"] for step in out["paths"][0]]
    assert ids == [SYM_AUDIT, SYM_ROUTE]
    assert out["paths"][0][1]["via_edge"] == "CALLS in"


def test_path_between_same_node_is_a_trivial_one_node_path(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    out = json.loads(mcp.tool_repo_path_between(idx, SYM_ROUTE, SYM_ROUTE))
    assert len(out["paths"]) == 1
    assert [s["node_id"] for s in out["paths"][0]] == [SYM_ROUTE]


def test_path_between_unconnected_nodes_returns_empty_paths_not_a_timeout(mini_index):
    """Distinct from an unknown id: both nodes exist, there is simply no
    path within the default edge types and hop budget."""
    mcp = mcp_module()
    idx = Index(mini_index)
    out = json.loads(mcp.tool_repo_path_between(idx, FILE_NOTES, FILE_INIT))
    assert out["paths"] == []
    assert "no path" in out["message"]


def test_path_between_unknown_node_id_is_an_error_distinct_from_no_path(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    out = mcp.tool_repo_path_between(idx, "sym:nowhere.py::nothing", SYM_ROUTE)
    assert isinstance(out, mcp.ToolError)
    assert "not found" in out


def test_path_between_cochange_is_opt_in_not_followed_by_default(mini_index):
    """CO_CHANGE must never appear in a default-edge-types path (#386)."""
    mcp = mcp_module()
    idx = Index(mini_index)
    # Wire a synthetic CO_CHANGE edge directly into adjacency (no git history
    # in the mini fixture) between two otherwise-unconnected files.
    edge = {"src": FILE_NOTES, "dst": FILE_INIT, "type": "CO_CHANGE", "count": 9}
    idx.adj[FILE_NOTES].append((FILE_INIT, "CO_CHANGE", "out", edge))
    idx.adj[FILE_INIT].append((FILE_NOTES, "CO_CHANGE", "in", edge))

    default_out = json.loads(mcp.tool_repo_path_between(idx, FILE_NOTES, FILE_INIT))
    assert default_out["paths"] == []

    opt_in_out = json.loads(
        mcp.tool_repo_path_between(idx, FILE_NOTES, FILE_INIT, edge_types=["CO_CHANGE"])
    )
    assert opt_in_out["paths"], opt_in_out
    assert [s["node_id"] for s in opt_in_out["paths"][0]] == [FILE_NOTES, FILE_INIT]


def test_path_between_reports_minimum_confidence_along_the_path(mini_index):
    """An ambiguous 1-of-3 CALLS edge on the path must show as the minimum,
    not be hidden by a later confident hop."""
    mcp = mcp_module()
    idx = Index(mini_index)
    ambiguous_id = "sym:pkg/ambiguous.py::ambiguous_target"
    idx.nodes[ambiguous_id] = {
        "id": ambiguous_id,
        "name": "ambiguous_target",
        "qualname": "ambiguous_target",
        "kind": "function",
        "path": "pkg/ambiguous.py",
        "lang": "python",
        "start_line": 1,
        "end_line": 2,
    }
    weak_edge = {
        "src": SYM_ROUTE,
        "dst": ambiguous_id,
        "type": "CALLS",
        "confidence": 0.33,
        "candidate_count": 3,
    }
    idx.adj[SYM_ROUTE].append((ambiguous_id, "CALLS", "out", weak_edge))
    idx.adj[ambiguous_id].append((SYM_ROUTE, "CALLS", "in", weak_edge))

    out = json.loads(mcp.tool_repo_path_between(idx, SYM_ROUTE, ambiguous_id))
    assert out["min_confidence"] == [0.33]
    assert out["paths"][0][1]["confidence"] == 0.33


def test_path_between_max_hops_is_clamped_to_its_own_ceiling(mini_index):
    mcp = mcp_module()
    assert 0 < mcp.MCP_PATH_HOPS <= mcp.MCP_MAX_PATH_HOPS
    # #386 explicitly asked that this be a separate ceiling from MCP_MAX_HOPS
    # (4) rather than a silent raise of it.
    assert mcp.MCP_MAX_PATH_HOPS != mcp.MCP_MAX_HOPS


def _flood_star(idx, n, hub_id):
    """`n` direct CALLS neighbours hanging off `hub_id`, none of them the
    eventual target -- so a *single* hop already exceeds MCP_MAX_PATH_VISITED,
    proving the visited cap binds independently of the hop budget."""
    for i in range(n):
        nid = f"sym:pkg/star.py::s{i}"
        idx.nodes[nid] = {
            "id": nid,
            "name": f"s{i}",
            "qualname": f"s{i}",
            "kind": "function",
            "path": "pkg/star.py",
            "lang": "python",
            "start_line": 1,
            "end_line": 2,
        }
        edge = {"src": hub_id, "dst": nid, "type": "CALLS", "confidence": 1.0}
        idx.adj[hub_id].append((nid, "CALLS", "out", edge))
        idx.adj[nid].append((hub_id, "CALLS", "in", edge))


def test_path_between_visited_ceiling_actually_binds_under_flood(mini_index):
    """A hub with far more direct neighbours than MCP_MAX_PATH_VISITED, none
    of them the (unreachable) target, must report `truncated` rather than
    silently exploring the whole fan-out -- proving the visited cap binds
    on its own, not merely the hop cap (AGENTS.md: a hop bound alone does
    not bound work on a dense graph)."""
    mcp = mcp_module()
    idx = Index(mini_index)
    _flood_star(idx, mcp.MCP_MAX_PATH_VISITED + 500, SYM_ROUTE)
    isolated = "sym:pkg/isolated.py::isolated_fn"
    idx.nodes[isolated] = {
        "id": isolated,
        "name": "isolated_fn",
        "qualname": "isolated_fn",
        "kind": "function",
        "path": "pkg/isolated.py",
        "lang": "python",
        "start_line": 1,
        "end_line": 2,
    }
    out = json.loads(
        mcp.tool_repo_path_between(idx, SYM_ROUTE, isolated, max_hops=mcp.MCP_MAX_PATH_HOPS)
    )
    assert out["paths"] == []
    assert out["truncated"] is True


def test_path_between_reachable_via_dispatch(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    out = json.loads(
        mcp.dispatch(idx, "repo_path_between", {"from_id": SYM_ROUTE, "to_id": SYM_AUDIT})
    )
    assert [s["node_id"] for s in out["paths"][0]] == [SYM_ROUTE, SYM_AUDIT]


def test_path_between_is_in_the_served_tool_set():
    mcp = mcp_module()
    assert "repo_path_between" in mcp.TOOL_DESCRIPTIONS
    assert "repo_path_between" in mcp.TOOL_SCHEMAS
    assert "repo_path_between" in mcp.server.tools


# ==========================================================================
# #387 -- repo_blast_radius (ships under this name; see module docstring)
# ==========================================================================


def test_blast_radius_finds_the_direct_caller(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    out = json.loads(mcp.tool_repo_blast_radius(idx, SYM_AUDIT))
    assert [(c["node_id"], c["hop"]) for c in out["callers"]] == [(SYM_ROUTE, 1)]
    assert out["subclasses"] == []
    assert [(i["node_id"], i["hop"]) for i in out["importers"]] == [(FILE_GATEWAY, 1)]
    assert out["cochange"] == []
    assert out["summary"] == {
        "files_affected": 1,
        "symbols_affected": 2,
        "max_hop_reached": 1,
        "truncated": False,
    }


def test_blast_radius_include_cochange_false_is_purely_ast_derived(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    edge = {"src": FILE_AUDIT, "dst": FILE_NOTES, "type": "CO_CHANGE", "count": 5}
    idx.adj[FILE_AUDIT].append((FILE_NOTES, "CO_CHANGE", "out", edge))
    idx.adj[FILE_NOTES].append((FILE_AUDIT, "CO_CHANGE", "in", edge))

    with_cochange = json.loads(mcp.tool_repo_blast_radius(idx, SYM_AUDIT, include_cochange=True))
    assert with_cochange["cochange"] == [{"path": "docs/notes.md", "count": 5}]

    without = json.loads(mcp.tool_repo_blast_radius(idx, SYM_AUDIT, include_cochange=False))
    assert without["cochange"] == []
    assert without["callers"] == with_cochange["callers"]


def test_blast_radius_reverse_calls_closure_goes_multiple_hops(mini_index):
    """Callers of callers, not just one hop -- the distinction from
    repo_neighbours (#387)."""
    mcp = mcp_module()
    idx = Index(mini_index)
    caller2 = "sym:pkg/outer.py::outer_caller"
    idx.nodes[caller2] = {
        "id": caller2,
        "name": "outer_caller",
        "qualname": "outer_caller",
        "kind": "function",
        "path": "pkg/outer.py",
        "lang": "python",
        "start_line": 1,
        "end_line": 2,
    }
    edge = {"src": caller2, "dst": SYM_ROUTE, "type": "CALLS", "confidence": 1.0}
    idx.adj[caller2].append((SYM_ROUTE, "CALLS", "out", edge))
    idx.adj[SYM_ROUTE].append((caller2, "CALLS", "in", edge))

    out = json.loads(mcp.tool_repo_blast_radius(idx, SYM_AUDIT, max_hops=3))
    assert (SYM_ROUTE, 1) in [(c["node_id"], c["hop"]) for c in out["callers"]]
    assert (caller2, 2) in [(c["node_id"], c["hop"]) for c in out["callers"]]


def test_blast_radius_unknown_node_is_an_error(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    out = mcp.tool_repo_blast_radius(idx, "sym:nowhere.py::nothing")
    assert isinstance(out, mcp.ToolError)


def test_blast_radius_never_returns_a_secret_neighbour(mini_index):
    """the secret filter rule's rule extended: a caller/importer that lives at a secret path
    must not appear even though it is graph-reachable."""
    mcp = mcp_module()
    idx = Index(mini_index)
    secret_caller = "sym:.env::fake"
    idx.nodes[secret_caller] = {
        "id": secret_caller,
        "name": "fake",
        "qualname": "fake",
        "kind": "function",
        "path": ".env",
        "lang": "python",
        "start_line": 1,
        "end_line": 2,
    }
    edge = {"src": secret_caller, "dst": SYM_AUDIT, "type": "CALLS", "confidence": 1.0}
    idx.adj[secret_caller].append((SYM_AUDIT, "CALLS", "out", edge))
    idx.adj[SYM_AUDIT].append((secret_caller, "CALLS", "in", edge))

    out = json.loads(mcp.tool_repo_blast_radius(idx, SYM_AUDIT))
    assert secret_caller not in [c["node_id"] for c in out["callers"]]


def _flood_callers(idx, n):
    for i in range(n):
        nid = f"sym:pkg/flood.py::caller{i}"
        idx.nodes[nid] = {
            "id": nid,
            "name": f"caller{i}",
            "qualname": f"caller{i}",
            "kind": "function",
            "path": "pkg/flood.py",
            "lang": "python",
            "start_line": 1,
            "end_line": 2,
        }
        edge = {"src": nid, "dst": SYM_AUDIT, "type": "CALLS", "confidence": 1.0}
        idx.adj[nid].append((SYM_AUDIT, "CALLS", "out", edge))
        idx.adj[SYM_AUDIT].append((nid, "CALLS", "in", edge))


def test_blast_radius_limit_ceiling_actually_binds_under_flood(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    _flood_callers(idx, mcp.MCP_MAX_NEIGHBOURS + 500)
    out = json.loads(mcp.tool_repo_blast_radius(idx, SYM_AUDIT, limit=10**9))
    assert len(out["callers"]) <= mcp.MCP_MAX_NEIGHBOURS
    assert out["summary"]["truncated"] is True


def test_blast_radius_a_hub_symbol_reports_truncated_not_a_partial_set_as_complete(mini_index):
    """Acceptance: hitting the visited ceiling says so rather than silently
    returning a subset that reads as the full picture."""
    mcp = mcp_module()
    idx = Index(mini_index)
    _flood_callers(idx, mcp.MCP_MAX_IMPACT_VISITED + 500)
    out = json.loads(mcp.tool_repo_blast_radius(idx, SYM_AUDIT))
    assert out["summary"]["truncated"] is True


def test_blast_radius_reachable_via_dispatch(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    out = json.loads(mcp.dispatch(idx, "repo_blast_radius", {"node_id": SYM_AUDIT}))
    assert out["callers"] and out["callers"][0]["node_id"] == SYM_ROUTE


def test_blast_radius_is_in_the_served_tool_set():
    mcp = mcp_module()
    assert "repo_blast_radius" in mcp.TOOL_DESCRIPTIONS
    assert "repo_blast_radius" in mcp.TOOL_SCHEMAS
    assert "repo_blast_radius" in mcp.server.tools


# ==========================================================================
# Cross-cutting: exclude_secrets is unconditional on all four new tools
# ==========================================================================


def test_no_new_tool_ever_leaks_the_env_secret(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    outs = [
        mcp.tool_repo_find_symbol(idx, "ACME_DEPLOYMENT_LEDGER_TOKEN"),
        mcp.tool_repo_read(idx, ".env", start_line=1, end_line=4),
        mcp.tool_repo_path_between(idx, FILE_ENV, SYM_ROUTE),
    ]
    for out in outs:
        assert "abc123deadbeef" not in str(out)
        assert "zzz999notreal" not in str(out)
