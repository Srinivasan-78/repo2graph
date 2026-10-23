"""Graph storage behind one interface (#306): every backend answers alike, in the same order."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from repo2graph.cli import main
from repo2graph.export import path as artifact_path
from repo2graph.mcp.retrieval import tool_repo_find_symbol, tool_repo_neighbours
from repo2graph.mcp.traversal import tool_repo_blast_radius, tool_repo_path_between
from repo2graph.query import Index
from repo2graph.store import (
    SQLITE_CACHE_NAME,
    MemoryGraphStore,
    SqliteGraphStore,
    open_graph_store,
)

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
    yield Index(out), disk
    if isinstance(disk.store, SqliteGraphStore):
        disk.store.close()


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
    store = SqliteGraphStore.from_index(out, tmp_path / "g.sqlite")
    idx = Index(out, store=store)
    nodes = idx.nodes
    store.close()
    assert isinstance(idx.store, MemoryGraphStore) and idx.nodes is nodes
    assert set(idx.adj) <= set(nodes) | {e["dst"] for e in idx.edges}


# --------------------------------------------------------------------------
# `--store`, and the cache's freshness test
#
# `SqliteGraphStore` existed, was tested, and had no entry point: no CLI flag,
# no MCP option, nothing outside this file constructed it. These cover the
# resolver that now reaches it.
# --------------------------------------------------------------------------


@pytest.fixture
def cached(tmp_path):
    """A fresh index of its own, since these tests mutate its manifest."""
    out = tmp_path / "idx"
    main(["build", str(SAMPLE), "-o", str(out), "--formats", "jsonl,overview"])
    return out


def test_memory_returns_no_store_so_the_default_path_is_unchanged(cached):
    """Selecting the default explicitly must not route through new code."""
    assert open_graph_store(cached, "memory") is None


def test_an_unknown_store_is_refused_by_name(cached):
    with pytest.raises(ValueError, match="unknown store"):
        open_graph_store(cached, "lmdb")


def test_sqlite_materialises_once_and_is_reused(cached):
    db = cached / SQLITE_CACHE_NAME
    assert not db.exists()
    store = open_graph_store(cached, "sqlite")
    assert isinstance(store, SqliteGraphStore)
    store.close()
    stamp = db.stat().st_mtime_ns

    again = open_graph_store(cached, "sqlite")
    assert isinstance(again, SqliteGraphStore)
    again.close()
    assert db.stat().st_mtime_ns == stamp, "a current cache was rebuilt"


def test_a_future_dated_source_does_not_rebuild_the_cache(cached):
    """Freshness is the index's `build_id`, never an mtime comparison.

    The first version of this resolver compared the database's mtime with
    `nodes.jsonl`'s, which rebuilt on *every* query whenever a source file
    carried a future timestamp -- clock skew between a build host and a query
    host, or a restored archive -- because the newly written database was
    still "older" than its own input. A `build_id` is an identity rather than
    an ordering, so a clock cannot outrun it.
    """
    db = cached / SQLITE_CACHE_NAME
    open_graph_store(cached, "sqlite").close()  # type: ignore[union-attr]
    stamp = db.stat().st_mtime_ns

    nodes = artifact_path(cached, "nodes.jsonl")
    ahead = time.time() + 3600
    os.utime(nodes, (ahead, ahead))

    open_graph_store(cached, "sqlite").close()  # type: ignore[union-attr]
    assert db.stat().st_mtime_ns == stamp


def test_a_different_build_id_rebuilds_the_cache(cached):
    db = cached / SQLITE_CACHE_NAME
    open_graph_store(cached, "sqlite").close()  # type: ignore[union-attr]
    stamp = db.stat().st_mtime_ns

    manifest = artifact_path(cached, "manifest.json")
    loaded = json.loads(manifest.read_text(encoding="utf-8"))
    loaded["build_id"] = "a-different-build"
    manifest.write_text(json.dumps(loaded, indent=2), encoding="utf-8")

    rebuilt = open_graph_store(cached, "sqlite")
    assert isinstance(rebuilt, SqliteGraphStore)
    assert rebuilt.meta("build_id") == "a-different-build"
    rebuilt.close()
    assert db.stat().st_mtime_ns != stamp


def test_a_corrupt_cache_is_replaced_rather_than_fatal(cached):
    db = cached / SQLITE_CACHE_NAME
    db.write_bytes(b"not a database at all")
    store = open_graph_store(cached, "sqlite")
    assert isinstance(store, SqliteGraphStore)
    assert store.node_count() > 0
    store.close()


def test_a_symlinked_cache_is_refused(cached, tmp_path):
    db = cached / SQLITE_CACHE_NAME
    target = tmp_path / "elsewhere.sqlite"
    target.write_bytes(b"")
    try:
        db.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable (Windows without developer mode)")
    with pytest.raises(ValueError, match="symlink"):
        open_graph_store(cached, "sqlite")


def test_a_missing_index_names_the_flag(tmp_path):
    with pytest.raises(FileNotFoundError, match="--store sqlite"):
        open_graph_store(tmp_path / "nothing", "sqlite")


@pytest.mark.parametrize("command", ["query", "rag"])
def test_both_backends_answer_identically_through_the_cli(cached, capsys, command):
    """The flag is only worth having if it cannot change an answer."""
    capsys.readouterr()  # drop the fixture's build report
    main([command, "service", "-o", str(cached), "--store", "memory"])
    memory = capsys.readouterr().out
    main([command, "service", "-o", str(cached), "--store", "sqlite"])
    sqlite_out = capsys.readouterr().out
    assert memory == sqlite_out
    assert memory.strip()


def test_empty_or_corrupt_manifest_rebuilds_cache_on_change(cached):
    """An empty or corrupt build_id must never prove freshness."""
    manifest = artifact_path(cached, "manifest.json")
    manifest.write_text("not json at all", encoding="utf-8")

    db = cached / SQLITE_CACHE_NAME
    if db.exists():
        db.unlink()
    store1 = open_graph_store(cached, "sqlite")
    assert store1 is not None
    initial_count = store1.node_count()
    store1.close()

    # Append a fake node to nodes.jsonl
    nodes = artifact_path(cached, "nodes.jsonl")
    with open(nodes, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "sym:extra::node", "type": "symbol", "name": "extra"}) + "\n")

    store2 = open_graph_store(cached, "sqlite")
    assert store2 is not None
    assert store2.node_count() == initial_count + 1
    assert store2.has("sym:extra::node")
    store2.close()

    # Also test with manifest removed entirely
    manifest.unlink()
    store3 = open_graph_store(cached, "sqlite")
    assert store3 is not None
    assert store3.node_count() == initial_count + 1
    store3.close()


def test_directory_at_sqlite_path_exits_diagnosed(cached, capsys):
    """A directory at graph.sqlite exits with a clear error rather than unhandled traceback."""
    db = cached / SQLITE_CACHE_NAME
    if db.exists():
        db.unlink()
    db.mkdir()
    try:
        with pytest.raises(SystemExit) as exc:
            main(["query", "service", "-o", str(cached), "--store", "sqlite"])
        assert exc.value.code is not None and exc.value.code != 0
        assert "error: cannot use --store at" in str(exc.value)
    finally:
        db.rmdir()


def test_concurrent_store_materialization(cached):
    """Concurrent threads materializing the store race safely on BuildLock."""
    import concurrent.futures

    db = cached / SQLITE_CACHE_NAME
    if db.exists():
        db.unlink()

    def _open():
        s = open_graph_store(cached, "sqlite")
        assert s is not None
        assert s.node_count() > 0
        s.close()
        return True

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_open) for _ in range(16)]
        for f in concurrent.futures.as_completed(futures):
            assert f.result() is True


def test_relocatable_and_fallback_cache(cached, monkeypatch, tmp_path):
    """REPO2GRAPH_CACHE_DIR moves the SQLite database."""
    custom = tmp_path / "custom_cache"
    monkeypatch.setenv("REPO2GRAPH_CACHE_DIR", str(custom))
    s = open_graph_store(cached, "sqlite")
    assert s is not None
    assert s.node_count() > 0
    s.close()
    assert list(custom.glob("*.sqlite")), "cache was not written to REPO2GRAPH_CACHE_DIR"


def test_sqlite_chunk_list_lazy_chunks(cached):
    """SqliteChunkList serves chunks on demand without resident chunk dicts."""
    from repo2graph.query import SqliteChunkList

    store = open_graph_store(cached, "sqlite")
    assert store is not None
    idx = Index(cached, store=store)
    assert isinstance(idx.chunks, SqliteChunkList)
    assert len(idx.chunks) > 0
    c0 = idx.chunks[0]
    assert "id" in c0 and "text" in c0
    assert idx.by_node.get(c0["node_id"])
    store.close()
