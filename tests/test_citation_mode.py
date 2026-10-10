"""Tests for Phase 1: citation-mode neighbours, conditional expansion, and precision-first packing."""

from pathlib import Path
from repo2graph.mcp.guardrails import MCP_MAX_SEARCH_NEIGHBOURS
from repo2graph.mcp.retrieval import tool_repo_search
from repo2graph.query import Index, is_lexical_weak


def _build_test_graph_index(tmp_path: Path) -> Index:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    chunks_path = agent_dir / "chunks.jsonl"
    nodes_path = agent_dir / "nodes.jsonl"
    edges_path = agent_dir / "edges.jsonl"

    chunks_path.write_text(
        '{"id": "c1", "node_id": "sym:pkg::caller", "path": "pkg/caller.py", "start_line": 10, "end_line": 20, "name": "caller", "qualname": "caller", "text": "def caller():\\n    # call callee\\n    callee()\\n"}\n'
        '{"id": "c2", "node_id": "sym:pkg::callee", "path": "pkg/callee.py", "start_line": 1, "end_line": 30, "name": "callee", "qualname": "callee", "text": "# file: pkg/callee.py\\n# function: callee\\ndef callee():\\n    # line 1 of body\\n    # line 2 of body\\n    return 42\\n"}\n',
        encoding="utf-8",
    )
    nodes_path.write_text(
        '{"id": "sym:pkg::caller", "name": "caller", "qualname": "caller", "kind": "function", "path": "pkg/caller.py"}\n'
        '{"id": "sym:pkg::callee", "name": "callee", "qualname": "callee", "kind": "function", "path": "pkg/callee.py"}\n',
        encoding="utf-8",
    )
    edges_path.write_text(
        '{"src": "sym:pkg::caller", "dst": "sym:pkg::callee", "type": "CALLS", "confidence": 1.0}\n',
        encoding="utf-8",
    )
    return Index(tmp_path)


def test_citation_mode_neighbours_emits_signature_without_body(tmp_path: Path):
    """Citation mode must emit node_id + one-line signature + file:line cite without the body."""
    idx = _build_test_graph_index(tmp_path)

    # 1. Default (neighbours="full") contains full chunk body of neighbour callee
    pack_full = idx.pack_context("caller", neighbours="full")
    assert "# line 1 of body" in pack_full["markdown"]
    assert "# line 2 of body" in pack_full["markdown"]
    assert len(pack_full["neighbors"]) == 1
    assert not pack_full["neighbors"][0].get("citation_only")

    # 2. Citation mode (neighbours="cite") emits signature line and node_id without full body
    pack_cite = idx.pack_context("caller", neighbours="cite")
    md = pack_cite["markdown"]
    assert "### [cite: pkg/callee.py:1-30] `callee` [sym:pkg::callee] (CALLS out of caller)" in md
    assert "def callee():" in md
    assert "# line 1 of body" not in md
    assert "# line 2 of body" not in md
    assert len(pack_cite["neighbors"]) == 1
    assert pack_cite["neighbors"][0].get("citation_only") is True
    assert pack_cite["neighbors"][0]["text"] == "def callee():"


def test_retrieve_citation_mode(tmp_path: Path):
    """retrieve() with neighbours='cite' must emit signature only for neighbours."""
    idx = _build_test_graph_index(tmp_path)
    res = idx.retrieve("caller", neighbours="cite")
    # caller is seed (why='lexical') with full text
    assert res[0]["why"] == "lexical"
    assert "def caller():" in res[0]["text"]
    # callee is neighbour with signature only and citation_only=True
    assert res[1]["why"] == "CALLS out of caller"
    assert res[1]["citation_only"] is True
    assert res[1]["text"] == "def callee():"


def test_conditional_expansion_skips_when_lexical_strong(tmp_path: Path):
    """When conditional_expansion=True and lexical match is strong, graph expansion is skipped."""
    idx = _build_test_graph_index(tmp_path)
    # Strong lexical match for caller
    pack = idx.pack_context("caller", conditional_expansion=True)
    # Strong match -> no expansion -> 0 neighbours
    assert len(pack["neighbors"]) == 0
    assert "pkg/callee.py:1-30" not in pack["markdown"]

    # When conditional_expansion=False (default), expansion runs
    pack_default = idx.pack_context("caller", conditional_expansion=False)
    assert len(pack_default["neighbors"]) == 1
    assert "pkg/callee.py:1-30" in pack_default["markdown"]


def test_is_lexical_weak_detection():
    """Verify is_lexical_weak logic for flat, low, or strong scores."""
    # 1. Empty ranked scores -> weak
    assert is_lexical_weak([]) is True
    # 2. Top score below floor (e.g. 10.0 < 18.0) -> weak
    assert is_lexical_weak([(10.0, 0), (9.0, 1), (8.0, 2)], k=3, min_top_score=18.0) is True
    # 3. Top score high (30.0 >= 18.0), but flat across top-k (30.0 / 25.0 = 1.2 < 1.35) -> weak (flat)
    assert (
        is_lexical_weak([(30.0, 0), (28.0, 1), (25.0, 2)], k=3, min_top_score=18.0, flat_ratio=1.35)
        is True
    )
    # 4. Top score high (35.0 >= 18.0), sharp drop (35.0 / 10.0 = 3.5 >= 1.35) -> NOT weak (strong)
    assert (
        is_lexical_weak([(35.0, 0), (20.0, 1), (10.0, 2)], k=3, min_top_score=18.0, flat_ratio=1.35)
        is False
    )


def test_precision_first_packing_ego_expansion(tmp_path: Path):
    """precision_first=True must expand per-seed ego-graph within budget."""
    idx = _build_test_graph_index(tmp_path)
    pack = idx.pack_context("caller", precision_first=True, neighbours="cite")
    assert len(pack["seeds"]) == 1
    assert len(pack["neighbors"]) == 1
    assert pack["neighbors"][0].get("citation_only") is True
    assert "def callee():" in pack["markdown"]


def test_mcp_repo_search_neighbours_and_clamping(tmp_path: Path):
    """tool_repo_search must accept neighbours and clamp max_neighbours."""
    idx = _build_test_graph_index(tmp_path)
    res = tool_repo_search(idx, "caller", neighbours="cite", max_neighbours=5)
    assert "[sym:pkg::callee]" in res
    assert "def callee():" in res
    assert "# line 1 of body" not in res

    # Clamping test with large max_neighbours
    res_clamped = tool_repo_search(
        idx, "caller", neighbours="cite", max_neighbours=MCP_MAX_SEARCH_NEIGHBOURS + 100
    )
    assert "[sym:pkg::callee]" in res_clamped


def test_a_split_symbol_says_which_part_each_chunk_is_and_cites_its_own_lines(tmp_path):
    """#287: parts of a symbol too large for one chunk carry `split`
    metadata, keep the symbol's qualname in their header, prefer to end at a
    blank line, and cite exactly the source lines they hold."""
    from repo2graph.chunks import MAX_CHARS, iter_chunks
    from repo2graph.graph import build

    blocks = []
    for b in range(60):
        blocks.append(
            "\n".join(f"        total_{b}_{k} = value * {k}  # step {k}" for k in range(4))
        )
    body = "\n\n".join(blocks)
    src = f"class Ledger:\n    def reconcile(self, value):\n{body}\n        return value\n"
    (tmp_path / "ledger.py").write_text(src, encoding="utf8")
    lines = src.split("\n")
    g = build(tmp_path, jobs=1)
    parts = [c for c in iter_chunks(g) if c["qualname"] == "Ledger.reconcile"]
    assert len(parts) > 1 and len(src) > MAX_CHARS
    for n, c in enumerate(parts, 1):
        assert c["split"] == {"part": n, "of": len(parts), "by": "size"}
        assert "Ledger.reconcile" in c["text"].split("\n")[1]
        assert lines[c["start_line"] - 1] in c["text"]
    # Every part but the last ends on a block boundary.
    for c in parts[:-1]:
        assert lines[c["end_line"] - 1].strip() == "" or lines[c["end_line"]].strip() == ""


def test_a_whole_symbol_has_no_split_field(tmp_path):
    from repo2graph.chunks import iter_chunks
    from repo2graph.graph import build

    (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf8")
    g = build(tmp_path, jobs=1)
    assert all("split" not in c for c in iter_chunks(g))
