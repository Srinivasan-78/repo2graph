"""Unit tests for repo2graph explain command and module (Issue #309)."""

import json
from pathlib import Path
import pytest

from repo2graph.chunks import iter_chunks
from repo2graph.cli import main
from repo2graph.export import dump_all
from repo2graph.graph import build
from repo2graph.explain import (
    explain_edge,
    explain_node,
    explain_retrieval,
    format_explain_edge,
    format_explain_node,
    format_explain_retrieval,
)


@pytest.fixture
def indexed_repo(tmp_path: Path):
    """Build and export a small sample repo into an index."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    util = src_dir / "util.py"
    util.write_text(
        'def helper(x: int) -> int:\n    """Compute helper value."""\n    return x + 42\n',
        encoding="utf8",
    )
    main_py = src_dir / "main.py"
    main_py.write_text(
        "from util import helper\n\n"
        "class Runner:\n"
        '    """Executes the runner."""\n'
        "    def run(self) -> int:\n"
        "        return helper(10)\n",
        encoding="utf8",
    )
    out = tmp_path / "out"
    g = build(src_dir)
    dump_all(g, list(iter_chunks(g)), out, {"jsonl", "overview"})
    return out


def test_explain_edge_found(indexed_repo: Path):
    """Verify explain_edge locates existing edges and produces source locations."""
    res = explain_edge(indexed_repo, "file:main.py", "file:util.py")
    assert res["found"] is True
    assert len(res["edges"]) >= 1
    assert res["edges"][0]["type"] == "IMPORTS"
    text = format_explain_edge(res)
    assert "file:main.py -> file:util.py" in text
    assert "[IMPORTS]" in text


def test_explain_edge_missing(indexed_repo: Path):
    """Verify explain_edge handles unconnected nodes or non-existent nodes cleanly."""
    # Nodes exist but not directly connected
    res = explain_edge(indexed_repo, "sym:main.py::Runner", "sym:util.py::helper")
    assert res["found"] is False
    assert res["src_exists"] is True
    assert res["dst_exists"] is True
    assert "no direct edge" in res["reason"]

    # Non-existent node
    res2 = explain_edge(indexed_repo, "file:nonexistent.py", "file:util.py")
    assert res2["found"] is False
    assert res2["src_exists"] is False
    assert "does not exist" in res2["reason"]


def test_explain_node_found(indexed_repo: Path):
    """Verify explain_node details symbol metadata, degree, and chunks."""
    res = explain_node(indexed_repo, "sym:util.py::helper")
    assert res["found"] is True
    assert res["node"]["name"] == "helper"
    assert res["node"]["path"] == "util.py"
    assert len(res["chunks"]) >= 1
    assert res["in_degree"] >= 1  # DEFINES from file, CALLS from Runner.run

    text = format_explain_node(res)
    assert "Node: sym:util.py::helper" in text
    assert "helper" in text


def test_explain_node_missing(indexed_repo: Path):
    """Verify explain_node handles missing node ID."""
    res = explain_node(indexed_repo, "sym:ghost.py::vanished")
    assert res["found"] is False
    assert "does not exist" in res["reason"]
    text = format_explain_node(res)
    assert "Node not found" in text


def test_explain_retrieval(indexed_repo: Path):
    """Verify explain_retrieval traces query tokens, BM25 seeds, and expansion steps."""
    res = explain_retrieval(indexed_repo, "run helper", k=3, hops=1)
    assert "helper" in res["query_tokens"]
    assert len(res["seeds"]) >= 1
    assert len(res["primary_seeds"]) >= 1
    assert len(res["retrieved_chunks"]) >= 1
    # Check that provenance is marked
    provenances = {c["provenance"] for c in res["retrieved_chunks"]}
    assert "seed" in provenances or "expanded_neighbor" in provenances

    text = format_explain_retrieval(res)
    assert "Retrieval Explanation" in text
    assert "Candidate seeds" in text
    assert "Retrieved chunks returned" in text


def test_cli_explain_commands(indexed_repo: Path, capsys):
    """Verify CLI integration for explain edge, node, retrieval (text & json)."""
    out_arg = str(indexed_repo)

    # 1. explain edge
    assert main(["explain", "edge", "file:main.py", "file:util.py", "-o", out_arg]) == 0
    out = capsys.readouterr().out
    assert "file:main.py -> file:util.py" in out

    assert main(["explain", "edge", "file:main.py", "file:util.py", "-o", out_arg, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["found"] is True

    # 2. explain node
    assert main(["explain", "node", "sym:util.py::helper", "-o", out_arg]) == 0
    out = capsys.readouterr().out
    assert "sym:util.py::helper" in out

    assert main(["explain", "node", "sym:util.py::helper", "-o", out_arg, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["found"] is True
    assert data["node"]["name"] == "helper"

    # 3. explain retrieval
    assert main(["explain", "retrieval", "helper", "-o", out_arg]) == 0
    out = capsys.readouterr().out
    assert "Retrieval Explanation" in out

    assert main(["explain", "retrieval", "helper", "-o", out_arg, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert "query" in data
    assert len(data["retrieved_chunks"]) >= 1
