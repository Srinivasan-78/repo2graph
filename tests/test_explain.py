"""Unit tests for repo2graph explain command and module (Issue #309)."""

import json
import os
import subprocess
import sys
from pathlib import Path
import pytest

from repo2graph.chunks import iter_chunks
from repo2graph.cli import main
from repo2graph.export import dump_all
from repo2graph.graph import build
from repo2graph.query import Index
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


# ==========================================================================
# `explain retrieval` must trace the retrieval it prints, reproducibly
# ==========================================================================

# Every module carries a module-level table so its *file* node gets a chunk:
# chunks.py drops a file_residual under 40 characters, and a file node with no
# chunk can be neither a seed nor a retrievable neighbour (AGENTS.md).
_TRACE_UTIL = '''\
HELPER_TABLE = {"alpha": 1, "beta": 2, "gamma": 3, "delta": 4, "epsilon": 5}


def helper(x: int) -> int:
    """Compute the helper value for the runner."""
    return x + 42
'''

_TRACE_MAIN = '''\
from util import helper

RUNNER_MODES = ("fast", "slow", "paused", "draining", "stopped")


class Runner:
    """Executes the runner over the helper."""

    def run(self) -> int:
        return helper(10)
'''


@pytest.fixture
def trace_repo(tmp_path: Path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "util.py").write_text(_TRACE_UTIL, encoding="utf8")
    (src_dir / "main.py").write_text(_TRACE_MAIN, encoding="utf8")
    out = tmp_path / "out"
    g = build(src_dir)
    dump_all(g, list(iter_chunks(g)), out, {"jsonl", "overview"})
    return out


def test_expansion_trace_follows_the_same_directions_retrieve_does(trace_repo: Path):
    """`expand()` defaults to DEFAULT_EDGE_DIRS, which is DEFINES-`in` only, but
    `retrieve()` passes ALL_EDGE_DIRS. An explain that takes the default traces
    a walk the retrieval below it never made -- it reported
    `Runner.run -> Runner DEFINES in` while the chunk it printed came back
    labelled `DEFINES out of main.py`.

    The tuple below is hand-derived from the fixture source: `Runner` defines
    the method `run`, so `sym:main.py::Runner` has a DEFINES edge *out* to
    `sym:main.py::Runner.run`. Set membership only -- no rank, score or
    order."""
    res = explain_retrieval(trace_repo, "runner modes helper", k=3, hops=1)

    traced = {
        (s["from_node"], s["to_node"], s["edge_type"], s["direction"])
        for s in res["expansion_steps"]
    }
    assert ("sym:main.py::Runner", "sym:main.py::Runner.run", "DEFINES", "out") in traced

    # ...and the step is not decorative: the chunk retrieve() returned for that
    # node says it arrived the same way.
    idx = Index(trace_repo)
    picked = {(c["node_id"], c["why"]) for c in idx.retrieve("runner modes helper", k=3, hops=1)}
    assert ("sym:main.py::Runner.run", "DEFINES out of Runner") in picked


def test_every_retrieved_neighbour_appears_in_the_expansion_trace(trace_repo: Path):
    """Nothing may arrive in `retrieved_chunks` by a hop the trace does not
    show. This is the property the direction filter broke, stated without
    naming any one edge.

    The query is "modes" and not the three-term one above on purpose: it seeds
    on `file:main.py` alone, so both of the fixture's neighbours really are
    expanded rather than seeded, and the narrowed `DEFINES: ("in",)` leaves one
    of them retrieved but untraced. With a query that seeds every node the
    branch is vacuous."""
    res = explain_retrieval(trace_repo, "modes", k=3, hops=1)
    traced_nodes = {s["to_node"] for s in res["expansion_steps"]}
    seeds = set(res["primary_seeds"])
    for c in res["retrieved_chunks"]:
        if c["provenance"] == "expanded_neighbor":
            assert c["node_id"] in traced_nodes, (c, res["expansion_steps"])
        else:
            assert c["node_id"] in seeds, c


def test_primary_seeds_keep_the_short_list_order(trace_repo: Path):
    """`primary_seeds` was `list(a set)`. `expand()` walks its frontier in
    order under a per-hop cap, so seed order is part of the traversal, and a
    set makes it depend on PYTHONHASHSEED."""
    res = explain_retrieval(trace_repo, "runner modes helper", k=3, hops=1)
    assert len(res["primary_seeds"]) >= 2, res["primary_seeds"]
    assert res["primary_seeds"] == [s["node_id"] for s in res["seeds"] if s["selected_as_seed"]]


def test_explain_retrieval_output_is_reproducible(trace_repo: Path):
    """Two runs of the same command must print the same bytes. Run in
    subprocesses with different PYTHONHASHSEEDs: set iteration order is fixed
    *within* a process, so a same-process comparison cannot see this."""
    cmd = [
        sys.executable,
        "-c",
        "import sys; from repo2graph.cli import main; sys.exit(main(sys.argv[1:]))",
        "explain",
        "retrieval",
        "runner modes helper",
        "-o",
        str(trace_repo),
        "--json",
    ]
    outputs = []
    for seed in ("1", "2", "3"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
        assert proc.returncode == 0, proc.stderr
        outputs.append(proc.stdout)
    assert outputs[0] == outputs[1] == outputs[2], outputs


def test_selected_seeds_are_all_visible_in_the_text_report(trace_repo: Path):
    """The `*` column is what the seeds section is for, and a selected seed can
    sit below rank k -- each one has to be the first chunk of a *distinct*
    node. Truncating the printed list to k hid those and contradicted the
    "N short-listed" count printed directly above it."""
    res = explain_retrieval(trace_repo, "runner modes helper", k=1, hops=1)
    text = format_explain_retrieval(res)
    assert f"{len(res['seeds'])} short-listed" in text
    assert text.count("Rank ") == len(res["seeds"])
    for nid in res["primary_seeds"]:
        assert any(line.lstrip().startswith("*") and nid in line for line in text.splitlines()), (
            nid,
            text,
        )
