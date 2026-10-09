"""Change 2 -- the stdio MCP server. verification.

The `mcp` SDK is an optional extra and is deliberately never imported here:
`repo2graph.mcp`'s three handlers are plain functions taking an `Index`, so
tests need no SDK at all, and SDK absence guard blocks the import on purpose to
assert the error message a user without the extra actually sees.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import (
    MINI_QUERY,
    REPO_ROOT,
    SECRET_QUERY,
    SYM_AUDIT,
    SYM_ROUTE,
    build_mini_index,
    write_mini_repo,
)
from repo2graph.query import Index, _is_secret_path

PYPROJECT = REPO_ROOT / "pyproject.toml"


def _has_mcp():
    """A real, currently-*supported* SDK is importable.

    #407/#291 dropped mcp 1.x: `serve()` now speaks only the 2.x registration
    API (`on_list_tools=`/`on_call_tool=`), so a test that drives a live
    subprocess needs mcp>=2.0. This used to check
    `hasattr(Server, "list_tools")`, which is true only for the *unsupported*
    1.x generation -- so on any environment with the (now required) 2.x SDK
    installed, every test gated on it silently skipped forever. Detector:
    reverting this to the old hasattr check makes
    test_stdio_roundtrip_against_a_repo_with_no_index and
    test_stdio_server_roundtrip skip again against this repo's own
    installed SDK.
    """
    try:
        import mcp
        from mcp.server import Server  # noqa: F401
        from mcp.server.stdio import stdio_server  # noqa: F401

        # Not `mcp_module()`: that helper is defined further down this file,
        # after this module-level call runs at import time -- a `NameError`
        # here was swallowed by the broad `except Exception` below and every
        # gated test skipped regardless of what was installed.
        from repo2graph.mcp import _sdk_major, _sdk_version

        major = _sdk_major(_sdk_version(mcp))
        return major is None or major >= 2
    except (ImportError, AttributeError, ValueError, OSError):
        return False


HAS_REAL_MCP = _has_mcp()


def _has_any_mcp():
    """Any `mcp` SDK is importable at all, supported or not.

    Only `test_tools_call_over_serve_completes_on_the_parallel_path` uses this -- it exercises `serve()`'s
    own real behaviour (including refusing an unsupported SDK), so it must
    run whenever *some* SDK is installed, not only a supported one.
    """
    try:
        from mcp.server import Server  # noqa: F401
        from mcp.server.stdio import stdio_server  # noqa: F401

        return True
    except (ImportError, AttributeError):
        return False


HAS_ANY_MCP = _has_any_mcp()


def mcp_module():
    from repo2graph import mcp as mcp_mod

    return mcp_mod


def tokens(text: str) -> int:
    from repo2graph.query import count_tokens

    return count_tokens(text)


# ==========================================================================
# repo_map
# ==========================================================================


def test_repo_map_is_exactly_map_prepend(mini_index):
    """Verify no reformatting, no truncation, no query argument."""
    mcp = mcp_module()
    idx = Index(mini_index)
    assert mcp.tool_repo_map(idx) == idx.map_prepend()
    assert idx.map_prepend().strip(), "the fixture produced an empty map"


def test_repo_map_is_silent_when_the_tree_is_unchanged(mini_repo, mini_index):
    """#383: an index whose tree has not moved reports no staleness note --
    the check must not become a false positive on every call."""
    mcp = mcp_module()
    idx = mcp.open_index(mini_index, repo=mini_repo)
    assert "index may be stale" not in mcp.tool_repo_map(idx)


def test_repo_map_warns_when_the_working_tree_moved(mini_repo, mini_index):
    """#383: `_index_mtime`/`open_index`'s reload check both compare the index
    against itself at an earlier moment -- neither ever looks at the source
    tree, so a long-running server answered from a stale graph indefinitely
    and every `[cite: path:start-end]` anchor was then confidently wrong.
    `tool_repo_map` must surface the mismatch using the per-file sha256
    `index.state.json` already records (`status.compute_freshness`), not stay
    silent forever.

    Detector: reverting `tool_repo_map` to `index.map_prepend()` (its
    pre-#383 body) makes this fail while
    test_repo_map_is_silent_when_the_tree_is_unchanged keeps passing.
    """
    import os
    import time

    mcp = mcp_module()
    idx = mcp.open_index(mini_index, repo=mini_repo)

    gateway = mini_repo / "pkg" / "gateway.py"
    gateway.write_text(gateway.read_text(encoding="utf8") + "\n# changed after the build\n")
    # index.state.json's hashing is mtime-gated against manifest.json's own
    # mtime (status.compute_freshness): force the edit to look unambiguously
    # newer than a build that may have finished in the same filesystem tick.
    future = time.time() + 5
    os.utime(gateway, (future, future))

    stale_map = mcp.tool_repo_map(idx)
    assert "index may be stale" in stale_map
    assert "repo2graph build" in stale_map
    # The map itself is still appended verbatim after the note.
    assert idx.map_prepend() in stale_map


# ==========================================================================
# repo_search and its hard budget ceiling
# ==========================================================================


def test_repo_search_returns_cited_markdown_within_the_default_budget(big_index):
    """Verify at least one `### [cite: path:start-end]` header, and the result
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
def test_an_absurd_budget_is_clamped_to_the_ceiling(big_index, budget):
    """Verify the ceiling is enforced, not advisory."""
    mcp = mcp_module()
    idx = Index(big_index)
    out = mcp.tool_repo_search(idx, MINI_QUERY, k=20, budget_tokens=budget)
    assert tokens(out) <= mcp.MCP_MAX_BUDGET_TOKENS, tokens(out)

    unbounded = idx.pack_context(MINI_QUERY, k=20, budget_chars=0)
    assert tokens(unbounded["markdown"]) > mcp.MCP_MAX_BUDGET_TOKENS, (
        "the fixture no longer exceeds the ceiling"
    )


@pytest.mark.parametrize("budget", [0, -5, -(10**9)])
def test_a_zero_or_negative_budget_is_clamped_to_the_floor(big_index, budget):
    """Verify no crash, no traceback, and still a bounded string."""
    mcp = mcp_module()
    idx = Index(big_index)
    out = mcp.tool_repo_search(idx, MINI_QUERY, k=20, budget_tokens=budget)
    assert isinstance(out, str)
    assert tokens(out) <= mcp.MCP_MAX_BUDGET_TOKENS


def test_ceiling_is_above_the_default():
    """Verify the two constants are ordered the way the plan says."""
    mcp = mcp_module()
    assert 0 < mcp.MCP_BUDGET_TOKENS <= mcp.MCP_MAX_BUDGET_TOKENS


def test_truncation_happens_on_a_line_boundary(big_index):
    """Verify a clamped result is still parseable markdown -- no half line."""
    mcp = mcp_module()
    idx = Index(big_index)
    out = mcp.tool_repo_search(idx, MINI_QUERY, k=20, budget_tokens=10**9)
    full = idx.pack_context(MINI_QUERY, k=20, budget_chars=0)["markdown"]
    full_lines = set(full.split("\n"))  # never splitlines(): see CONTRIBUTING.md
    body = out.split("\n")
    for line in body[:-1]:
        assert line in full_lines, line[:120]


# ==========================================================================
# the secret filter rule -- secrets never leave through an agent tool
# ==========================================================================


def test_repo_search_never_returns_a_secret_chunk(mini_index):
    """Verify the fixture's `.env` chunk is BM25 rank 1 for SECRET_QUERY and
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


def test_repo_map_and_neighbours_also_exclude_secrets(mini_index):
    """Verify the other two tools must not become the leak instead."""
    mcp = mcp_module()
    idx = Index(mini_index)
    for text in (mcp.tool_repo_map(idx), mcp.tool_repo_neighbours(idx, "file:.env")):
        assert "abc123deadbeef" not in text
        assert "zzz999notreal" not in text


# ==========================================================================
# repo_neighbours
# ==========================================================================


def test_neighbours_names_a_reachable_node_its_edge_and_direction(mini_index):
    """Verify route_request -> audit_event over CALLS out is in the fixture
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
    # "out" in out alone would pass on "route_request" (r-out-e) regardless of
    # direction, so assert the actual rendered edge line instead.
    assert "CALLS out:" in out, out


def test_an_unknown_node_id_returns_a_short_message(mini_index):
    """Verify not found, not a traceback, and not a wall of text."""
    mcp = mcp_module()
    idx = Index(mini_index)
    out = mcp.tool_repo_neighbours(idx, "sym:nowhere.py::nothing")
    assert isinstance(out, str)
    assert "not found" in out.lower(), out
    assert len(out) <= 400, len(out)


def test_neighbours_respects_its_limit(mini_index):
    """Verify an agent-facing tool must be bounded here too."""
    mcp = mcp_module()
    idx = Index(mini_index)
    short = mcp.tool_repo_neighbours(idx, SYM_ROUTE, limit=1)
    longer = mcp.tool_repo_neighbours(idx, SYM_ROUTE, limit=50)
    assert len(short) <= len(longer)


# ==========================================================================
# tool descriptions are context an agent pays for every session
# ==========================================================================


def test_tool_descriptions_stay_under_budget():
    """Verify the published tool set, capped under a combined character budget.

    The character budget balances agent context overhead against Glama TDQS
    (Tool Definition Quality Standard) requirements. Tool descriptions are loaded
    into every agent's context every turn, so they must stay tightly bounded;
    however, they must also provide explicit usage guidance, sibling disambiguation,
    and safety disclosures.

    Round 1 of MCP hardening added four tools (repo_find_symbol, repo_read,
    repo_path_between, repo_blast_radius) to the original six, each of which
    needs its own sibling cross-references and when/when-not guidance to pass
    the tests below -- so the combined budget below is deliberately raised
    from the original 3000 (six tools) to 6000: the per-tool [100, 800] cap is
    unchanged and is what actually keeps any *one* description honest.

    `repo_impact` was removed with the rest of the diff-analysis surface, so
    the set is nine. The combined budget is left at 6000 rather than tightened
    to fit, because it exists to stop descriptions growing, not to pin a total.
    """
    mcp = mcp_module()
    assert set(mcp.TOOL_DESCRIPTIONS) == {
        "repo_map",
        "repo_search",
        "repo_neighbours",
        "repo_find_symbol",
        "repo_read",
        "repo_path_between",
        "repo_blast_radius",
        "repo_cache_stats",
        "repo_build_status",
    }
    for name, text in mcp.TOOL_DESCRIPTIONS.items():
        assert isinstance(text, str) and text.strip(), name
        assert 100 <= len(text) <= 800, (
            f"{name} length {len(text)} out of expected [100, 800] range"
        )
    total = sum(len(d) for d in mcp.TOOL_DESCRIPTIONS.values())
    assert total <= 6000, f"Combined tool descriptions ({total} chars) exceed 6000-char budget"


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
        "repo_find_symbol": {"repo_search", "repo_map"},
        "repo_read": {"repo_search", "repo_neighbours"},
        "repo_path_between": {"repo_neighbours", "repo_search"},
        # `repo_blast_radius` pointed at `repo_impact` for diff-level work; with
        # that tool removed it points at the two it can actually be confused
        # with -- an open-ended search, and reading a symbol's own body.
        "repo_blast_radius": {"repo_read", "repo_search"},
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
        "repo_impact": [],
        "repo_find_symbol": ["name"],
        "repo_read": ["path"],
        "repo_path_between": ["from_id", "to_id"],
        "repo_blast_radius": ["node_id"],
        "repo_cache_stats": [],
        "repo_build_status": ["task_id"],
    }
    bounded_params = {"k", "hops", "budget_tokens", "limit", "max_depth"}

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


def _field(obj, *names):
    """The first of `names` found on `obj`, as an attribute or a dict key.

    The installed SDK's pydantic models expose fields under their snake_case
    Python name (`input_schema`, `read_only_hint`); the wire/constructor
    spelling stays camelCase (`inputSchema`, `readOnlyHint`) as an alias. A
    fake `Tool`/`ToolAnnotations` built by this file's own stand-ins (or an
    older SDK) may only have the camelCase one, and `TOOL_ANNOTATIONS` itself
    is a plain dict keyed camelCase -- so a real test here has to accept
    either rather than assume one SDK generation's naming.
    """
    for name in names:
        if isinstance(obj, dict):
            if name in obj:
                return obj[name]
        elif hasattr(obj, name):
            return getattr(obj, name)
    return None


@pytest.mark.skipif(not HAS_REAL_MCP, reason="needs repo2graph[mcp] (mcp>=2.0,<3.0)")
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
        schema = _field(tool, "input_schema", "inputSchema")
        assert schema == mcp.TOOL_SCHEMAS[tool.name]
        ann = getattr(tool, "annotations", None)
        assert ann is not None, f"Tool {tool.name} missing annotations"
        read_only = _field(ann, "read_only_hint", "readOnlyHint")
        assert read_only is True, f"{tool.name} readOnlyHint must be True"


@pytest.mark.skipif(not HAS_REAL_MCP, reason="needs repo2graph[mcp] (mcp>=2.0,<3.0)")
def test_annotations_are_honest_about_auto_build():
    """#292: a client inspecting annotations must not be told a tool is
    read-only when its first call, on a server that can auto-build, may
    parse the whole repository, run git, and write `.r2g/**` to disk.

    `get_tools(auto_build=False)` -- an already-indexed server, or one
    started with `--no-auto-build` -- keeps every tool read-only: that
    annotation is true in that mode. `get_tools(auto_build=True)` -- stdio
    with a repo to build from and auto-build not disabled -- must mark every
    build-capable tool `readOnlyHint=False`, except `repo_build_status`,
    which `run_tool` special-cases before it would ever trigger a build.
    """
    mcp = mcp_module()

    honest = {t.name: t for t in mcp.get_tools(auto_build=False)}
    building = {t.name: t for t in mcp.get_tools(auto_build=True)}
    assert set(honest) == set(building) == set(mcp.TOOL_DESCRIPTIONS)

    for name in mcp.TOOL_DESCRIPTIONS:
        still_read_only = _field(honest[name].annotations, "read_only_hint", "readOnlyHint")
        assert still_read_only is True, name

        maybe_building = _field(building[name].annotations, "read_only_hint", "readOnlyHint")
        if name in mcp.BUILD_CAPABLE_TOOLS:
            assert maybe_building is False, (
                f"{name} can trigger an auto-build via open_index_or_task and "
                f"must not claim readOnlyHint=True"
            )
        else:
            assert maybe_building is True, (
                f"{name} never triggers a build (run_tool special-cases it) "
                f"and should stay read-only even when auto-build is enabled"
            )


def test_repo_build_status_is_the_only_non_build_capable_tool():
    """Pins BUILD_CAPABLE_TOOLS against `run_tool`'s own special case, so a
    future tool added to TOOL_DESCRIPTIONS without updating the annotation
    set fails loudly here rather than silently miscategorising a new tool.
    """
    mcp = mcp_module()
    assert mcp.BUILD_CAPABLE_TOOLS == frozenset(mcp.TOOL_DESCRIPTIONS) - {"repo_build_status"}


# ==========================================================================
# Packaging and the console entry point
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


def test_mcp_is_an_optional_extra_with_a_console_script():
    """Verify `mcp` extra + `repo2graph-mcp` entry point."""
    data = load_pyproject()
    extras = data["project"]["optional-dependencies"]
    assert "mcp" in extras, sorted(extras)
    assert requirement_names(extras["mcp"]) == {"mcp"}, extras["mcp"]
    scripts = data["project"]["scripts"]
    assert scripts.get("repo2graph-mcp") == "repo2graph.mcp:main", scripts
    assert scripts.get("repo2graph") == "repo2graph.cli:main", scripts


def test_runtime_dependencies_are_still_only_tree_sitter():
    """Verify a bare `pip install repo2graph` brings in nothing new."""
    data = load_pyproject()
    assert requirement_names(data["project"]["dependencies"]) == {
        "tree-sitter",
        "tree-sitter-language-pack",
    }


def test_entry_point_without_the_sdk_explains_the_extra(mini_index, monkeypatch):
    """Verify a user without the extra gets an actionable message and a
    non-zero exit, never an ImportError traceback."""
    mcp = mcp_module()
    monkeypatch.setitem(sys.modules, "mcp", None)
    with pytest.raises(SystemExit) as exc:
        mcp.main(["--out", str(mini_index)])
    message = str(exc.value)
    assert 'pip install "repo2graph[mcp]"' in message, message
    assert exc.value.code not in (0, None)


def test_serve_without_the_sdk_raises_the_same_systemexit(mini_index, monkeypatch):
    """Verify the guard lives at the import site, not only in main()."""
    mcp = mcp_module()
    monkeypatch.setitem(sys.modules, "mcp", None)
    with pytest.raises(SystemExit) as exc:
        mcp.serve(Path(mini_index))
    assert 'pip install "repo2graph[mcp]"' in str(exc.value)


def test_open_index_caches_one_index_per_directory(mini_index):
    """Verify the server must not re-read the whole index per tool call."""
    mcp = mcp_module()
    first = mcp.open_index(mini_index)
    second = mcp.open_index(mini_index)
    assert first is second
    assert isinstance(first, Index)


# ==========================================================================
# live stdio server round-trip
# ==========================================================================


@pytest.mark.skipif(not HAS_REAL_MCP, reason="needs repo2graph[mcp] (mcp>=2.0,<3.0)")
def test_stdio_server_roundtrip(mini_index):
    """Verify automated round-trip against the live stdio server."""
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
# Regression: unclamped k/hops on repo_neighbours and repo_search
# ==========================================================================
#
# `k` and `hops` were coerced but never clamped. `Index.expand` runs
# `for _ in range(hops)` with no empty-frontier exit, so `hops=10**9` blocked
# serve()'s single event loop for ~a minute. Output size was already bounded;
# time was not. These assert the value that reaches the engine, not the value
# that comes back, because a clamp that only trims the answer is not a clamp.


@pytest.mark.parametrize("hops", [10**9, 10**12, 5, "99999", None, -3])
def test_neighbours_hops_never_exceeds_the_ceiling(mini_index, hops):
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
def test_search_k_and_hops_never_exceed_their_ceilings(mini_index, k, hops):
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


def test_dispatch_clamps_too(mini_index):
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


def test_sane_arguments_are_left_alone(mini_index):
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
# Regression: unceilinged limit on repo_neighbours
# ==========================================================================
#
# `limit` on repo_neighbours had a floor and no ceiling, so the size of
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
def test_neighbours_limit_never_exceeds_the_ceiling(mini_index, limit):
    """R-7 (a): whatever a model writes into `limit`, the reply is bounded."""
    mcp = mcp_module()
    idx = Index(mini_index)
    _flood(idx)
    out = mcp.tool_repo_neighbours(idx, SYM_ROUTE, limit=limit)
    assert len(_rows(out)) <= mcp.MCP_MAX_NEIGHBOURS, len(_rows(out))


def test_the_default_limit_still_applies(mini_index):
    """R-7 (b): the ceiling did not become the default."""
    mcp = mcp_module()
    idx = Index(mini_index)
    _flood(idx)
    out = mcp.tool_repo_neighbours(idx, SYM_ROUTE)
    assert len(_rows(out)) <= mcp.MCP_NEIGHBOUR_LIMIT, len(_rows(out))
    assert mcp.MCP_MAX_NEIGHBOURS > mcp.MCP_NEIGHBOUR_LIMIT, "ceiling below default"


def test_dispatch_inherits_the_limit_clamp(mini_index):
    """R-7 (c): the clamp lives in the handler, so the JSON route gets it too."""
    mcp = mcp_module()
    idx = Index(mini_index)
    _flood(idx)
    out = mcp.dispatch(idx, "repo_neighbours", {"node_id": SYM_ROUTE, "limit": 10**6})
    assert len(_rows(out)) <= mcp.MCP_MAX_NEIGHBOURS, len(_rows(out))


def test_a_sane_limit_is_left_alone(mini_index):
    """R-7 (d): the ceiling must not quietly rewrite an ordinary request."""
    mcp = mcp_module()
    idx = Index(mini_index)
    _flood(idx)
    assert len(_rows(mcp.tool_repo_neighbours(idx, SYM_ROUTE, limit=7))) == 7
    assert len(_rows(mcp.tool_repo_neighbours(idx, SYM_ROUTE, limit=1))) == 1


# ==========================================================================
# Regression: the mcp extra's version bound
# ==========================================================================
#
# The extra declared `mcp>=1.0`, which resolves to mcp 2.x today. mcp 2.x
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


def test_a_broken_server_module_is_also_an_instruction(mini_index, monkeypatch):
    """R-8 (c): the other way a future SDK can move -- `mcp` imports but
    `mcp.server` does not."""
    mcp = mcp_module()
    _fake_sdk(monkeypatch, decorators=False, with_server_module=False)
    with pytest.raises(SystemExit) as exc:
        mcp._require_sdk()
    assert mcp.SDK_SPEC in str(exc.value), str(exc.value)


def test_a_supported_sdk_passes_the_guard(monkeypatch):
    """R-8 (d): the guard must not become a blanket refusal -- an SDK that
    does carry the API serve() needs, and is 2.x or newer, is accepted."""
    mcp = mcp_module()
    fake = _fake_sdk(monkeypatch, decorators=True, version="2.2.0")
    assert mcp._require_sdk() is fake


def test_a_1x_sdk_is_refused_at_startup_not_hung(monkeypatch):
    """#407: mcp 1.x hangs on the server's first tool call rather than
    erroring, deterministically (~4 min timeout under 1.30.0). Dropping 1.x
    support (#291) means the version is checked here, at startup, instead of
    only the API surface -- so the failure is a sentence, not a hang.

    Detector: before the fix, `_require_sdk()` only checked
    `hasattr(Server, "list_tools")`, which mcp 1.x satisfies, so this SDK
    passed the guard and `serve()` drove the (hanging) 1.x branch.
    """
    mcp = mcp_module()
    _fake_sdk(monkeypatch, decorators=True, version="1.30.0")
    with pytest.raises(SystemExit) as exc:
        mcp._require_sdk()
    assert "1.30.0" in str(exc.value)
    assert mcp.SDK_SPEC in str(exc.value)
    assert "#407" in str(exc.value)


def test_the_missing_sdk_message_is_still_the_missing_sdk_message(mini_index, monkeypatch):
    """R-8 (e): absent and unusable are different problems with different
    instructions; the new branch must not swallow SDK absence guard's."""
    mcp = mcp_module()
    monkeypatch.setitem(sys.modules, "mcp", None)
    with pytest.raises(SystemExit) as exc:
        mcp.serve(Path(mini_index))
    assert 'pip install "repo2graph[mcp]"' in str(exc.value)
    assert "not supported" not in str(exc.value)


def test_the_extra_is_bounded_below_the_unsupported_major():
    """R-8 (f): the declared extra and the guard's advice are one string."""
    mcp = mcp_module()
    spec = load_pyproject()["project"]["optional-dependencies"]["mcp"]
    assert spec == [mcp.SDK_SPEC], (spec, mcp.SDK_SPEC)
    assert "<3.0" in mcp.SDK_SPEC


# ==========================================================================
# A floor-clamped budget must not hand an agent an empty string
# ==========================================================================


@pytest.mark.parametrize("budget", [0, -5, -(10**9), 1])
def test_a_floor_clamped_budget_explains_itself(big_index, budget):
    """R-10 (a): the budget limit only requires "no crash"; an empty tool result reads to
    an agent exactly like "no such code", so say which it was."""
    mcp = mcp_module()
    idx = Index(big_index)
    out = mcp.tool_repo_search(idx, MINI_QUERY, k=20, budget_tokens=budget)
    assert out.strip(), "an empty string is the thing this test exists to stop"
    assert "budget_tokens" in out and str(mcp.MCP_MAX_BUDGET_TOKENS) in out
    assert tokens(out) <= mcp.MCP_MAX_BUDGET_TOKENS


def test_the_note_is_short_and_never_the_normal_answer(big_index):
    """R-10 (b): a real pack must not be replaced by the note, and the note
    must stay far below the default budget so it cannot itself be trimmed."""
    mcp = mcp_module()
    idx = Index(big_index)
    real = mcp.tool_repo_search(idx, MINI_QUERY)
    assert "### [cite:" in real
    assert "no content fit in" not in real
    assert tokens(mcp.EMPTY_RESULT.format(budget=1, ceiling=12000)) < 200


def test_dispatch_inherits_the_note(big_index):
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
    """No index and no repo to build one from: fail fast, naming the path.

    `test_serve_without_a_repo_still_preflights` asserted this same call and the
    same message further down the file. Since `serve()` is invoked here with no
    `repo` argument either, "without a repo" was already this test's condition,
    not a second one.
    """
    mcp = mcp_module()
    _fake_sdk(monkeypatch, decorators=True, version="2.2.0")
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
    _fake_sdk(monkeypatch, decorators=True, version="2.2.0")
    monkeypatch.setattr(
        mcp, "_build_index", lambda *a, **k: pytest.fail("built during the handshake")
    )
    monkeypatch.setattr(asyncio, "run", lambda coro: coro.close())

    out = tmp_path / "deferred_idx"
    mcp.serve(out, repo=mini_repo)  # must not raise, must not build
    assert not out.exists()


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


@pytest.mark.skipif(not HAS_REAL_MCP, reason="needs repo2graph[mcp] (mcp>=2.0,<3.0)")
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

        target, attr = mod, "run"
        run = lambda: mod._git_files(tmp_path)
    else:
        # Popen, not run: since log streaming add_cochange streams the log so the byte
        # cap can bind during the read. The stdin=DEVNULL requirement is the
        # same either way -- capture_output was never what redirected it.
        from repo2graph import graph as mod

        target, attr = mod, "Popen"
        run = lambda: mod.add_cochange(mod.Graph(tmp_path, "t"), tmp_path, 10, set())
    monkeypatch.setattr(target.subprocess, attr, spy)
    run()  # the OSError is caught by the caller; we only want the kwargs

    assert seen["kwargs"].get("stdin") is sp.DEVNULL, (
        f"{call} would inherit the MCP transport on stdin: {seen['kwargs']}"
    )


# ==========================================================================
# #90 -- the auto-build's process pool, and the transport it must not touch
# ==========================================================================
#
# `_build_index` used to pin `jobs=1` because a pool started from inside the
# running stdio server put the client's JSON-RPC pipes in the hands of every
# worker. The pin is gone; `graph.silence_worker_io` is what replaced it.
#
# Every test below carries its own hard timeout. The failure this guards is an
# indefinite hang, so a regression has to *fail* rather than wedge a CI job --
# which is also why the old test asserted `jobs == 1` instead of proving
# anything end to end.

POOL_STDIO_PROBE = Path(__file__).resolve().parent / "_pool_stdio_probe.py"
MCP_POOL_SERVER = Path(__file__).resolve().parent / "_mcp_pool_server.py"

# Bounds on the two child processes below. Generous next to the ~2s each costs
# locally, because a cold 9-cell CI matrix is slower -- but finite, which is
# the whole point.
POOL_PROBE_TIMEOUT = 180
SERVE_TIMEOUT = 240


def test_auto_build_reaches_the_pool_and_detaches_its_workers(wide_repo, tmp_path, monkeypatch):
    """The pin is gone: above PARALLEL_MIN_FILES the auto-build really forks
    out, and the pool it builds carries the stdio initializer.

    `wide_repo` is the fixture #67 asks for -- every other fixture in the
    suite is under the threshold, so before it existed this branch could not
    be reached from a test at all.
    """
    import concurrent.futures

    from repo2graph import graph

    mcp = mcp_module()
    real = concurrent.futures.ProcessPoolExecutor
    seen = []

    class Recording(real):
        def __init__(self, *args, **kwargs):
            seen.append(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", Recording)
    out = tmp_path / "wide_idx"
    mcp._build_index(Path(wide_repo), out)

    assert seen, "auto-build stayed serial: the jobs=1 pin is back"
    assert seen[0].get("initializer") is graph.silence_worker_io, (
        f"the pool workers would inherit the transport: {seen[0]}"
    )
    # The build is real, not just attempted: a node hand-derived from the
    # fixture source has to be in the index it wrote.
    idx = Index(out)
    assert "sym:widepkg/mod0.py::dispatch_0" in idx.nodes


def test_pool_workers_cannot_reach_the_parents_stdin_or_stdout():
    """The mechanism itself, in a child process whose stdio is a pipe.

    `_pool_stdio_probe.py` starts a pool with the exact kwargs `parse_all`
    passes, and has one worker write a sentinel to fd 1 and read a byte from
    fd 0. Measured on Windows (spawn) before the fix, a worker did both for
    real: the sentinel landed in the parent's stdout pipe and the worker
    consumed the byte the parent had queued -- which under `repo2graph-mcp`
    is a frame of the client's request stream the server then waits for
    forever.

    `timeout=` is the hard bound: a regression that hangs a worker on fd 0
    fails this test instead of wedging the run.
    """
    from _pool_stdio_probe import REPORT_PREFIX, SENTINEL

    proc = subprocess.run(
        [sys.executable, str(POOL_STDIO_PROBE)],
        input=b"S",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(REPO_ROOT),
        timeout=POOL_PROBE_TIMEOUT,
    )
    stderr = proc.stderr.decode("utf8", "replace")
    assert proc.returncode == 0, f"probe failed: {stderr}"
    line = next(
        (ln for ln in stderr.splitlines() if ln.startswith(REPORT_PREFIX)),
        None,
    )
    assert line is not None, f"probe wrote no report: {stderr}"
    report = json.loads(line[len(REPORT_PREFIX) :])

    assert SENTINEL not in proc.stdout, (
        "a pool worker wrote to the parent's stdout -- under the MCP stdio "
        f"server that is the JSON-RPC stream: {proc.stdout!r}"
    )
    assert report["workers"][0]["read_fd0"] == "", (
        f"a pool worker read the parent's stdin: {report}"
    )
    assert report["stdin_left"] == "S", (
        f"the byte on the parent's stdin was consumed by a worker: {report}"
    )


@pytest.mark.skipif(not HAS_ANY_MCP, reason="needs the mcp extra (either SDK major)")
def test_tools_call_over_serve_completes_on_the_parallel_path(wide_repo, tmp_path):
    """the golden baseline, end to end: a real `serve()` over real pipes against a repo above
    PARALLEL_MIN_FILES answers its first tool call.

    The marker file is what makes this about the *parallel* path: the launcher
    records every ProcessPoolExecutor the server process builds, so a silent
    fall back to serial fails here rather than passing quietly.

    Timeouts everywhere -- a queue-fed reader thread, not a bare readline --
    because the bug being guarded is a hang, and a test that reproduces it
    with `proc.stdout.readline()` would never return.
    """
    import queue
    import threading

    marker = tmp_path / "pools.txt"
    out = tmp_path / "wide_idx"
    proc = subprocess.Popen(
        [sys.executable, str(MCP_POOL_SERVER), str(marker), str(wide_repo), str(out)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(REPO_ROOT),
    )
    lines: "queue.Queue" = queue.Queue()

    def pump():
        try:
            for line in proc.stdout:
                lines.put(line)
        finally:
            lines.put(None)

    threading.Thread(target=pump, daemon=True).start()

    def send(payload):
        proc.stdin.write((json.dumps(payload) + "\n").encode("utf8"))
        proc.stdin.flush()

    def recv(timeout):
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            assert remaining > 0, f"no JSON-RPC response within {timeout}s (the #90 hang)"
            try:
                line = lines.get(timeout=remaining)
            except queue.Empty:
                raise AssertionError(f"no JSON-RPC response within {timeout}s (the #90 hang)")
            assert line is not None, "the server closed stdout"
            try:
                return json.loads(line.decode("utf8"))
            except ValueError:
                continue  # a non-JSON line on stdout is caught by its own test

    try:
        send(
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
        assert "result" in recv(SERVE_TIMEOUT)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "repo_neighbours",
                    "arguments": {"node_id": "sym:widepkg/mod0.py::dispatch_0"},
                },
            }
        )
        resp = recv(SERVE_TIMEOUT)
    finally:
        proc.kill()
        proc.wait(timeout=30)
        if proc.stdin:
            proc.stdin.close()

    assert "result" in resp, resp
    text = resp["result"]["content"][0]["text"]
    # Hand-derived from the fixture source: dispatch_N calls handle_N.
    assert "handle_0" in text, text
    assert marker.is_file(), "the auto-build never started a process pool"
    assert "initializer=silence_worker_io" in marker.read_text(encoding="utf8")


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


# ==========================================================================
# String arguments (query, node_id, task_id) had no length ceiling.
# Every numeric MCP argument was clamped in the handler, but a model can hand
# this dispatcher an arbitrarily long string and nothing bounded it -- the
# string-typed half of "every MCP tool argument is caller-hostile".
# ==========================================================================


def test_an_absurdly_long_query_is_capped_before_it_reaches_the_index(mini_index):
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


def test_dispatch_caps_query_too(mini_index):
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


def test_an_absurdly_long_node_id_is_clamped_before_it_reaches_the_error(mini_index):
    """The ceiling has to bind, not merely fail to crash.

    `_str` clamps by slicing, so a 10 MB id produces a `str` and a "node not
    found" either way -- asserting only those two things passed with the clamp
    deleted. The refusal echoes the id it looked up, so the clamped length is
    observable in the output.
    """
    mcp = mcp_module()
    idx = Index(mini_index)
    huge = "sym:" + "z" * 10_000_000
    out = mcp.dispatch(idx, "repo_neighbours", {"node_id": huge})
    assert "node not found" in out
    echoed = out.split("node not found: ", 1)[1].split("'")[1]
    assert len(echoed) == mcp.MCP_MAX_NODE_ID_CHARS, len(echoed)


def test_an_absurdly_long_task_id_is_clamped_before_it_reaches_the_error():
    """Same ceiling, same reasoning, on the task-id path.

    A real (empty) TaskManager is needed to reach the branch that echoes the id:
    with `tasks=None` the server answers "builds synchronously" and never looks
    at the id at all, which is why the previous `isinstance(out, str)` assertion
    could not see the clamp.
    """
    from repo2graph.tasks import TaskManager

    mcp = mcp_module()
    huge = "t" * 10_000_000
    out = mcp.dispatch(None, "repo_build_status", {"task_id": huge}, tasks=TaskManager())
    echoed = out.split("no build task with id ", 1)[1].split("'")[1]
    assert len(echoed) == mcp.MCP_MAX_TASK_ID_CHARS, len(echoed)


def test_sane_string_arguments_are_left_alone(mini_index):
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


def test_mcp_open_index_detects_external_rebuild(tmp_path):
    """Issue 125: open_index detects when index was rebuilt externally and reloads."""
    import os
    import time
    from repo2graph.export import path as artifact_path
    from repo2graph.cache import ResultCache

    mcp = mcp_module()
    repo = write_mini_repo(tmp_path)
    out = tmp_path / "idx"
    build_mini_index(repo, out)

    cache = ResultCache()
    idx1 = mcp.open_index(out, cache=cache)
    assert idx1 is not None
    assert mcp.open_index(out, cache=cache) is idx1

    cache.put("test_key", "cached_val")
    assert cache.get("test_key") == "cached_val"

    time.sleep(0.05)
    manifest = artifact_path(out, "manifest.json")
    if manifest.exists():
        new_mtime = manifest.stat().st_mtime + 10.0
        os.utime(manifest, (new_mtime, new_mtime))
    chunks = artifact_path(out, "chunks.jsonl")
    if chunks.exists():
        new_mtime = chunks.stat().st_mtime + 10.0
        os.utime(chunks, (new_mtime, new_mtime))

    idx2 = mcp.open_index(out, cache=cache)
    assert idx2 is not idx1
    assert cache.get("test_key") is None


def test_repo_neighbours_explores_dir_file_and_calls(mini_index):
    """Issue 124: repo_neighbours explores CONTAINS edges for dirs and files, and includes low-confidence calls."""
    mcp = mcp_module()
    idx = Index(mini_index)

    # For dir: node, CONTAINS edges should return contained files
    dir_id = "dir:pkg"
    out_dir = mcp.tool_repo_neighbours(idx, dir_id)
    assert "CONTAINS" in out_dir
    assert "pkg/gateway.py" in out_dir or "gateway.py" in out_dir
    assert "- (none)" not in out_dir

    # For file: node, CONTAINS edges should return contained symbols
    file_id = "file:pkg/gateway.py"
    out_file = mcp.tool_repo_neighbours(idx, file_id)
    assert "CONTAINS" in out_file
    assert "route_request" in out_file
    assert "- (none)" not in out_file


# ==========================================================================
# Issue 235 -- open_index is reached from several HTTP worker threads at once
# ==========================================================================


def test_concurrent_open_index_builds_the_index_exactly_once(
    mini_index, mini_repo, tmp_path, monkeypatch
):
    """Two simultaneous first calls must cost one build and share one Index.

    The HTTP transport is a `ThreadingHTTPServer` with `daemon_threads = True`,
    so two clients pointed at a server whose index does not exist yet both
    reach `open_index` before either has written anything. Unlocked, both pass
    the `not _has_index(...)` test, both parse the whole repository and then
    race each other through `atomic_write`, so the loser's work is discarded.
    The invocation count below is the detector: it reads 2 without the
    per-directory lock and 1 with it.

    `_build_index` is replaced by a slow copy of an already-built index rather
    than a real parse, both to keep the test quick and to make the overlap
    deterministic -- the barrier puts both threads inside `open_index` and the
    sleep is far wider than the window an unsynchronised second caller needs to
    make its own `_has_index` decision.
    """
    import shutil
    import threading
    import time

    mcp = mcp_module()
    mcp._INDEXES.clear()
    mcp._INDEX_MTIMES.clear()

    out = tmp_path / "raced_idx"
    builds = []
    at_the_door = threading.Barrier(2, timeout=30)

    def slow_build(repo, target):
        builds.append(str(repo))
        time.sleep(0.3)
        shutil.copytree(mini_index, target)

    monkeypatch.setattr(mcp, "_build_index", slow_build)

    results: dict[int, object] = {}
    failures: dict[int, BaseException] = {}

    def call(slot):
        try:
            at_the_door.wait()
            results[slot] = mcp.open_index(out, repo=mini_repo)
        except BaseException as exc:  # noqa: BLE001 - test concurrency helper captures thread failure
            failures[slot] = exc

    threads = [threading.Thread(target=call, args=(slot,)) for slot in (0, 1)]
    for thread in threads:
        thread.start()
    # Each join is bounded so a lock that deadlocks fails the test instead of
    # hanging the suite until the runner's own timeout (or forever, locally).
    for thread in threads:
        thread.join(timeout=60)
    alive = [thread for thread in threads if thread.is_alive()]
    assert not alive, "open_index did not return within 60s -- deadlock?"

    assert not failures, failures
    assert len(builds) == 1, f"_build_index ran {len(builds)} times, expected 1"
    assert results[0] is results[1], "each caller loaded its own Index of the same directory"


# ==========================================================================
# Non-finite numbers: JSON `1e999` is float("inf"), and int(inf) overflows
# ==========================================================================


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_int_coerces_non_finite_floats_to_the_fallback(bad):
    mcp = mcp_module()
    assert mcp._int(bad, 7) == 7
    assert mcp._clamp(bad, 7, 1, 10) == 7


@pytest.mark.parametrize(
    "arguments",
    [
        {"k": 1e999},
        {"budget_tokens": 1e999},
        {"hops": -1e999},
        {"k": float("nan"), "budget_tokens": float("nan")},
    ],
)
def test_a_non_finite_numeric_argument_is_bad_input_not_a_tool_error(mini_index, arguments):
    """`json.loads('{"k": 1e999}')` yields inf; the handler used to raise
    OverflowError out of `_int` and the call became a tool error."""
    mcp = mcp_module()
    idx = Index(mini_index)
    parsed = json.loads(json.dumps({"query": MINI_QUERY, **arguments}))
    out = mcp.dispatch(idx, "repo_search", parsed)
    # Still the default answer, not an error -- but no longer silently: each
    # unusable argument is named in a leading one-line note (docs/mcp.md).
    assert not isinstance(out, mcp.ToolError)
    for name in arguments:
        assert f"_note: {name}=" in out
    body = out.split("._\n\n")[-1]
    assert body == mcp.dispatch(idx, "repo_search", {"query": MINI_QUERY})


def test_a_non_finite_limit_or_depth_is_also_bad_input(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    mcp.dispatch(idx, "repo_neighbours", {"node_id": SYM_ROUTE, "limit": 1e999, "hops": 1e999})
    assert mcp._int(json.loads("1e999"), 2) == 2


# ==========================================================================
# Review round 2: bad arguments are named, not silently absorbed
# ==========================================================================


def test_a_non_numeric_k_is_defaulted_with_a_note(mini_index):
    mcp = mcp_module()
    idx = Index(mini_index)
    out = mcp.dispatch(idx, "repo_search", {"query": MINI_QUERY, "k": "abc"})
    assert not isinstance(out, mcp.ToolError)
    assert out.startswith("_note: k='abc' is not an integer; used the default 8._")
    plain = mcp.dispatch(idx, "repo_neighbours", {"node_id": SYM_ROUTE, "limit": "lots"})
    assert plain.startswith("_note: limit='lots' is not an integer")
    ok = mcp.dispatch(idx, "repo_search", {"query": MINI_QUERY, "k": "3"})
    assert "_note:" not in ok


def test_repo_build_status_problems_are_tool_errors(mini_index):
    from repo2graph.tasks import TaskManager

    mcp = mcp_module()
    for args, tasks in (
        ({}, None),
        ({"task_id": "nope"}, None),
        ({}, TaskManager()),
        ({"task_id": "nope"}, TaskManager()),
    ):
        out = mcp.dispatch(None, "repo_build_status", args, tasks=tasks)
        assert isinstance(out, mcp.ToolError), (args, tasks)
        assert json.loads(out)["error"]


def test_tool_call_failed_exception():
    mcp = mcp_module()
    err = mcp.ToolCallFailed("something went wrong")
    assert isinstance(err, Exception)
    assert str(err) == "something went wrong"


# --------------------------------------------------------------------------
# Audit logging reaches the tool-call path
# --------------------------------------------------------------------------


def test_run_tool_writes_one_audit_record_per_call(mini_index, tmp_path):
    """The --audit-log flags have to produce records, not just be accepted.

    `serve()` built an `AuditLogger`, closed it, and never handed it to the
    tool-call path, so `record()` was unreachable in production while its own
    583 lines of tests passed. An operator pointing --audit-log at a file got an
    empty file. Nothing covered serve() -> record(), which is why it survived.
    """
    import json

    from repo2graph.audit import AuditConfig, AuditLogger
    from repo2graph.mcp.server import run_tool

    log_path = tmp_path / "audit.jsonl"
    audit = AuditLogger(AuditConfig(level="all", path=str(log_path)))
    try:
        out = run_tool(mini_index, None, "repo_map", {}, audit=audit)
    finally:
        audit.close()

    assert "# Repo map:" in out, out[:200]
    lines = [ln for ln in log_path.read_text(encoding="utf-8").split("\n") if ln.strip()]
    assert len(lines) == 1, lines
    record = json.loads(lines[0])
    assert record["event"] == "tool_call"
    assert record["tool"] == "repo_map"
    assert record["outcome"] == "success"
    assert record["duration_ms"] >= 1
    assert "error" not in record or record["error"] is None


def test_an_audit_record_is_written_when_the_tool_fails(mini_index, tmp_path):
    """A failure is the case an audit trail exists for, so it must be recorded.

    `errors` level keeps only non-success outcomes, which makes this the exact
    configuration an operator would run in and the one that must not be silent.
    """
    import json

    from repo2graph.audit import AuditConfig, AuditLogger
    from repo2graph.mcp.server import run_tool

    log_path = tmp_path / "audit.jsonl"
    audit = AuditLogger(AuditConfig(level="errors", path=str(log_path)))
    try:
        # No index at this path and no repo to build one from: open_index exits,
        # which run_tool turns into a ToolError rather than killing the server.
        out = run_tool(tmp_path / "absent", None, "repo_map", {}, audit=audit)
    finally:
        audit.close()

    assert "no repo2graph index found" in out
    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert record["tool"] == "repo_map"
    assert record["outcome"] == "error"
    assert "SystemExit" in record["error"]


def test_a_missing_index_mid_session_costs_one_call_not_the_server(tmp_path):
    """One bad request must not take the stdio server down.

    `open_index` raises SystemExit when it has no index and no repo, which is
    right for the startup preflight and for a direct call. On the per-request
    path it meant an index deleted while the server ran killed the process and
    the client saw a closed pipe instead of an error. SystemExit is a
    BaseException, so an ordinary `except Exception` around the handler does not
    hold it -- this pins the conversion explicitly.
    """
    from repo2graph.mcp.server import ToolError, run_tool

    out = run_tool(tmp_path / "gone", None, "repo_map", {})
    assert isinstance(out, ToolError)
    assert "no repo2graph index found" in out


def test_audit_logging_is_off_by_default(mini_index):
    """No logger means no record and no cost -- the default stays silent."""
    from repo2graph.mcp.server import run_tool

    out = run_tool(mini_index, None, "repo_map", {}, audit=None)
    assert "# Repo map:" in out


def test_auto_build_takes_the_build_lock(mini_repo, tmp_path, monkeypatch):
    """#451 D3: two writers on one index must serialise, as `build` does."""
    from repo2graph import lock as lockmod
    from repo2graph.mcp import indexes

    seen = []
    real = lockmod.BuildLock

    class Spy(real):
        def acquire(self):
            seen.append(self.lock_file)
            return super().acquire()

    monkeypatch.setattr(lockmod, "BuildLock", Spy)
    out = tmp_path / "locked_idx"
    indexes._build_index(mini_repo, out)
    assert seen == [out.resolve().parent / ".locked_idx.r2glock"]


def test_auto_built_index_ignores_everything(mini_repo, tmp_path):
    """#453 S16: an index nobody asked for must not ride along on `git add -A`."""
    from repo2graph.mcp import indexes

    out = tmp_path / "auto_idx"
    indexes._build_index(mini_repo, out)
    lines = (out / ".gitignore").read_text(encoding="utf8").splitlines()
    assert "*" in lines


def test_auto_build_keeps_a_user_edited_gitignore(mini_repo, tmp_path):
    from repo2graph.mcp import indexes

    out = tmp_path / "auto_idx"
    indexes._build_index(mini_repo, out)
    (out / ".gitignore").write_text("local.json\nmine\n", encoding="utf8")
    indexes._build_index(mini_repo, out)
    assert (out / ".gitignore").read_text(encoding="utf8") == "local.json\nmine\n"


@pytest.mark.parametrize(
    "tool,args", [("repo_search", {"query": "gateway"}), ("repo_find_symbol", {"name": "gateway"})]
)
def test_content_tools_carry_the_staleness_note_too(mini_repo, mini_index, tool, args):
    """#454 AI4: only repo_map warned, so "no callers" from any other tool read
    as fact against an index the working tree had moved past."""
    import os
    import time

    from repo2graph.mcp.server import run_tool

    fresh = run_tool(mini_index, mini_repo, tool, args)
    assert "index may be stale" not in fresh

    gateway = mini_repo / "pkg" / "gateway.py"
    gateway.write_text(gateway.read_text(encoding="utf8") + "\n# changed after the build\n")
    future = time.time() + 5
    os.utime(gateway, (future, future))
    stale = run_tool(mini_index, mini_repo, tool, args)
    assert stale.startswith("_note: index may be stale")


def test_auto_build_applies_the_servers_secret_rules(mini_repo, tmp_path, monkeypatch):
    """#453 S10: an org's --secret-keyword/--secret-dir rules never reached the
    MCP auto-build, so files they name were indexed anyway."""
    from repo2graph.mcp import indexes

    (mini_repo / "vaultcfg").mkdir()
    (mini_repo / "vaultcfg" / "settings.py").write_text("X = 1\n", encoding="utf8")
    (mini_repo / "acme_internal.py").write_text("Y = 2\n", encoding="utf8")
    monkeypatch.setitem(indexes.SECRET_RULES, "keywords", ["acme_internal"])
    monkeypatch.setitem(indexes.SECRET_RULES, "dirs", ["vaultcfg"])
    out = tmp_path / "idx"
    indexes._build_index(mini_repo, out)
    nodes = (out / "agent" / "nodes.jsonl").read_text(encoding="utf8")
    assert "acme_internal.py" not in nodes
    assert "vaultcfg/settings.py" not in nodes
