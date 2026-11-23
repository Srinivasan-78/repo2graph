"""Graph storage behind one interface (#306): every backend answers alike, in the same order."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from conftest import write_simple_repo

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


def test_sqlite_store_concurrent_thread_queries(pair):
    """Concurrent readers must return the same records a single reader does.

    This is the regression gate for #524, and it asserts *result correctness*
    against a sequentially-computed baseline rather than "nothing raised".
    That distinction is the whole point: an unsynchronised shared `sqlite3`
    connection fails by interleaving cursors and handing one thread another
    thread's row, which raises nothing at all. When the store's lock was
    reverted, the assertion that caught it was on record identity, not an
    exception -- and the chunk accessors convert `DatabaseError` to an empty
    result by design, so corruption there is silent too.

    Iterations are deliberately high. Measured against the unlocked store,
    8 workers x 1 pass reproduced the fault in only ~80% of runs -- too
    unreliable to gate a merge on. Exceptions are collected as data rather
    than propagated so a race reports every disagreement at once instead of
    whichever worker happened to fail first.
    """
    import concurrent.futures

    _, disk = pair
    store = disk.store

    # The baseline: single-threaded, computed before any worker starts. This
    # is what a correct store owes every caller regardless of concurrency.
    nodes = list(store.iter_nodes())
    assert nodes
    probes = [n["id"] for n in nodes]
    baseline = {
        nid: (store.node(nid), list(store.neighbours(nid)), store.has(nid)) for nid in probes
    }
    baseline_count = store.node_count()

    workers, iterations = 8, 60

    def query_task(worker: int) -> list[str]:
        failures: list[str] = []
        for i in range(iterations):
            nid = probes[(worker + i) % len(probes)]
            want_rec, want_nbrs, want_has = baseline[nid]
            try:
                rec = store.node(nid)
                nbrs = list(store.neighbours(nid))
                has = store.has(nid)
                count = store.node_count()
            except Exception as exc:  # noqa: BLE001 - reported below, not swallowed
                failures.append(f"w{worker} i{i} {nid}: raised {type(exc).__name__}: {exc}")
                continue
            if rec != want_rec:
                failures.append(f"w{worker} i{i} {nid}: node() -> {rec!r}, want {want_rec!r}")
            if nbrs != want_nbrs:
                failures.append(f"w{worker} i{i} {nid}: neighbours() disagreed with baseline")
            if has != want_has:
                failures.append(f"w{worker} i{i} {nid}: has() -> {has}, want {want_has}")
            if count != baseline_count:
                failures.append(f"w{worker} i{i}: node_count() -> {count}, want {baseline_count}")
        return failures

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        batches = [f.result() for f in [executor.submit(query_task, w) for w in range(workers)]]

    failures = [msg for batch in batches for msg in batch]
    assert not failures, (
        f"{len(failures)} concurrent reads disagreed with the single-threaded "
        f"baseline ({workers} workers x {iterations} iterations); first 5:\n"
        + "\n".join(failures[:5])
    )


def test_store_context_manager(out, tmp_path):
    import sqlite3

    db_path = tmp_path / "g.sqlite"
    with SqliteGraphStore.from_index(out, db_path) as store:
        assert isinstance(store, SqliteGraphStore)
        assert store.node_count() > 0

    with pytest.raises(sqlite3.ProgrammingError):
        store._db.execute("SELECT 1")

    db_path.unlink()
    assert not db_path.exists()

    with MemoryGraphStore([], []) as mem:
        assert isinstance(mem, MemoryGraphStore)
        assert mem.node_count() == 0


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
    """Concurrent threads materializing the store race safely on BuildLock.

    Asserts every thread observes the *same graph* as an uncontended build,
    not merely that opening did not raise. A half-written or doubly-written
    cache satisfies `node_count() > 0` in all 16 threads while they disagree
    with each other, which is the failure this test exists to exclude.
    """
    import concurrent.futures

    db = cached / SQLITE_CACHE_NAME

    # Baseline from one uncontended materialisation.
    if db.exists():
        db.unlink()
    baseline_store = open_graph_store(cached, "sqlite")
    assert baseline_store is not None
    try:
        want_count = baseline_store.node_count()
        want_ids = sorted(n["id"] for n in baseline_store.iter_nodes())
    finally:
        baseline_store.close()
    assert want_count > 0

    # Force every worker to race on materialising it again from scratch.
    if db.exists():
        db.unlink()

    def _open() -> tuple[int, list[str]]:
        s = open_graph_store(cached, "sqlite")
        assert s is not None
        try:
            return s.node_count(), sorted(n["id"] for n in s.iter_nodes())
        finally:
            s.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        observed = [f.result() for f in [pool.submit(_open) for _ in range(16)]]

    mismatched = [
        f"node_count={count}, {len(ids)} ids"
        for count, ids in observed
        if count != want_count or ids != want_ids
    ]
    assert not mismatched, (
        f"{len(mismatched)}/16 concurrent materialisations disagreed with the "
        f"single-threaded baseline of {want_count} nodes: {mismatched[:5]}"
    )


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


def test_build_lock_falls_back_inside_an_unwritable_parent():
    """The documented read-only container run must be able to take the lock.

    `BuildLock` puts its file beside the target (`/out` -> `/.out.r2glock`) so
    it survives a swap of that directory. The documented container run mounts
    only `/out` writable with `/` read-only, so that sibling is unwritable and
    `repo2graph build -o /out` failed outright with "cannot create build lock
    /.out.r2glock: Read-only file system" -- a deployment `.github/SECURITY.md`
    describes in detail could not actually run. CI never caught it because the
    container job that exercises it is newer than the bug.

    A lock inside the target is the weaker guarantee, and that is the point of
    only reaching for it once the sibling has genuinely failed to open with a
    permanently-unwritable errno.
    """
    import builtins
    import errno
    import tempfile

    from repo2graph.lock import BuildLock

    # Resolved on both sides: macOS hands out `/var/folders/...` while
    # `BuildLock` stores `Path(outdir).resolve()`, which is `/private/var/...`
    # there, so comparing an unresolved path against it fails on macOS only.
    base = Path(tempfile.mkdtemp()).resolve()
    out = base / "out"
    out.mkdir()
    sibling = (base / ".out.r2glock").resolve()

    real_open = builtins.open

    def fake_open(file, *a, **kw):
        if Path(str(file)).resolve() == sibling:
            raise OSError(errno.EROFS, "Read-only file system")
        return real_open(file, *a, **kw)

    builtins.open = fake_open
    try:
        lock = BuildLock(out, timeout=5)
        with lock:
            assert lock.lock_file == out / ".r2glock", lock.lock_file
            assert lock.lock_file.exists()
    finally:
        builtins.open = real_open

    # Released cleanly, so a later build does not see a stale holder.
    assert not (out / ".r2glock").exists()


def test_build_lock_prefers_the_sibling_when_the_parent_is_writable():
    """The fallback must not fire on an ordinary build, or the swap guarantee is lost."""
    import tempfile

    from repo2graph.lock import BuildLock

    # Resolved for the same reason as the test above.
    base = Path(tempfile.mkdtemp()).resolve()
    out = base / "out"
    out.mkdir()

    lock = BuildLock(out, timeout=5)
    with lock:
        assert lock.lock_file == base / ".out.r2glock", lock.lock_file
        assert not (out / ".r2glock").exists()


def test_an_unusable_cache_dir_is_diagnosed_not_a_traceback(tmp_path, monkeypatch, capsys):
    """`REPO2GRAPH_CACHE_DIR` is operator-supplied, so it can be anything.

    Its `mkdir` sits outside the `OSError` handling that guards the primary
    path, so a file standing where the directory must go escaped as a raw
    `FileExistsError` -- `_open_index` catches `(KeyError, ValueError,
    FileNotFoundError, StoreUnusable)`, and that is none of them. Same defect as
    #501, reached through the env var instead.
    """
    repo = write_simple_repo(tmp_path)
    out = tmp_path / "idx"
    main(["build", str(repo), "-o", str(out), "--formats", "jsonl"])
    capsys.readouterr()

    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory", encoding="utf8")
    monkeypatch.setenv("REPO2GRAPH_CACHE_DIR", str(blocker / "cache"))

    with pytest.raises(SystemExit) as exc:
        main(["query", "hello", "-o", str(out), "--store", "sqlite"])
    assert "cannot use --store" in str(exc.value)
    assert "REPO2GRAPH_CACHE_DIR" in str(exc.value)


def test_a_cache_that_cannot_be_opened_rebuilds_instead_of_raising(tmp_path, monkeypatch, capsys):
    """An `OSError` opening a cache must degrade, not escape.

    The fast path caught only `sqlite3.DatabaseError`, but opening the file can
    fail for reasons SQLite never sees: another process holding it open at the
    OS level on Windows, or permissions changed after it was written. Neither is
    in `_open_index`'s catch list, so it surfaced as a traceback. The right
    response is the one a corrupt file already gets -- rebuild.

    Simulated, because an OS-level lock SQLite does not translate is not
    something a test can portably create.
    """
    import repo2graph.store as store_mod

    repo = write_simple_repo(tmp_path)
    out = tmp_path / "idx"
    main(["build", str(repo), "-o", str(out), "--formats", "jsonl"])
    capsys.readouterr()

    # Materialise a cache, so the fast path is the branch under test.
    first = open_graph_store(out, "sqlite")
    assert isinstance(first, SqliteGraphStore)
    first.close()
    assert (out / SQLITE_CACHE_NAME).is_file()

    real_init = store_mod.SqliteGraphStore.__init__
    calls = {"n": 0}

    def flaky_init(self, path):
        calls["n"] += 1
        if calls["n"] == 1:  # only the fast-path open
            raise PermissionError(13, "Access is denied")
        return real_init(self, path)

    monkeypatch.setattr(store_mod.SqliteGraphStore, "__init__", flaky_init)

    store = open_graph_store(out, "sqlite")  # must not raise
    assert store is not None
    assert calls["n"] >= 2, "the fast path swallowed the error but never rebuilt"
