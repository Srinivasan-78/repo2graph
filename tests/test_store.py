"""Graph storage behind one interface (#306): every backend answers alike, in the same order."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo2graph.cli import main
from repo2graph.mcp.retrieval import tool_repo_find_symbol, tool_repo_neighbours
from repo2graph.mcp.traversal import tool_repo_blast_radius, tool_repo_path_between
from repo2graph.query import Index
from repo2graph.store import MemoryGraphStore, SqliteGraphStore

SAMPLE = Path(__file__).parent / "fixtures" / "sample_repo"


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    out = tmp_path_factory.mktemp("store") / "idx"
    main(["build", str(SAMPLE), "-o", str(out), "--formats", "jsonl"])
    return out


@pytest.fixture(scope="module")
def pair(out, tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "graph.sqlite"
    disk = Index(out, store=SqliteGraphStore.from_index(out, db))
    return Index(out), disk


def test_backends_hold_the_same_graph_in_the_same_order(pair):
    mem, disk = pair
    assert isinstance(mem.store, MemoryGraphStore)
    assert list(disk.store.iter_nodes()) == list(mem.store.iter_nodes())
    assert list(disk.store.iter_edges()) == list(mem.store.iter_edges())
    assert disk.store.node_count() == mem.store.node_count() > 0
    for node in mem.store.iter_nodes():
        nid = node["id"]
        assert disk.store.node(nid) == node and disk.store.has(nid)
        assert list(disk.store.neighbours(nid)) == list(mem.store.neighbours(nid))
    assert disk.store.node("sym:nope") is None and not disk.store.has("sym:nope")
    assert list(disk.store.neighbours("sym:nope")) == []


def test_traversal_and_retrieval_answer_byte_for_byte_alike(pair, out):
    mem, disk = pair
    symbols = [n["id"] for n in mem.store.iter_nodes() if n["type"] == "symbol"]
    assert len(symbols) >= 2
    for idx in (mem, disk):
        idx.answers = [
            *(tool_repo_neighbours(idx, nid) for nid in symbols),
            *(tool_repo_blast_radius(idx, nid) for nid in symbols),
            tool_repo_path_between(idx, symbols[0], symbols[-1]),
            tool_repo_find_symbol(idx, symbols[0].rsplit("::", 1)[-1]),
            idx.pack_context("how is the config loaded", budget_chars=4000)["markdown"],
            str(idx.retrieve("config", k=4, hops=1)),
        ]
    assert disk.answers == mem.answers
    # Nothing above fell back to loading the whole graph into memory.
    assert isinstance(disk.store, SqliteGraphStore)


def test_the_store_is_opened_read_only(pair):
    import sqlite3

    _, disk = pair
    with pytest.raises(sqlite3.OperationalError):
        disk.store._db.execute("DELETE FROM nodes")


def test_legacy_attributes_still_work_and_materialise_once(out, tmp_path):
    idx = Index(out, store=SqliteGraphStore.from_index(out, tmp_path / "g.sqlite"))
    nodes = idx.nodes
    assert isinstance(idx.store, MemoryGraphStore) and idx.nodes is nodes
    assert set(idx.adj) <= set(nodes) | {e["dst"] for e in idx.edges}
