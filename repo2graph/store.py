"""Graph storage behind one small interface (#306).

Traversal and retrieval read the graph through `GraphStore`: a node by id, a
node's incident edges, and the nodes and edges in file order. That is the whole
surface the call sites in `query.py`, `explain.py` and `mcp/` actually use, so
a different backend is one implementation rather than a change at every site.

`MemoryGraphStore` is the default and the reference: dicts and lists built from
`nodes.jsonl` / `edges.jsonl`, exactly what `Index` always held.
`SqliteGraphStore` is for graphs too big to keep resident. It uses only the
standard library's `sqlite3`, so the zero-dependency promise holds, and it
answers every call from disk.

Reach it with `query`/`rag --store sqlite`, which goes through
`open_graph_store` below. It was described here as "a prototype" and had no
entry point at all for several releases -- no CLI flag, no MCP option, nothing
outside `tests/test_store.py` constructing it -- so the interface and the
implementation both existed while the feature did not.

**Order is part of the contract.** Artifacts and tool output are byte-stable,
so every backend must return nodes in `nodes.jsonl` order, edges in
`edges.jsonl` order, and a node's incident edges in the order `Index.adj` has
always listed them: by edge position, the outgoing side of an edge before its
incoming side (they differ only for a self-loop). A SQL backend without an
`ORDER BY` would break that silently, so each query here carries one, and
`tests/test_store.py` runs the same traversals over both backends and compares
the output byte for byte.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import secrets
import sqlite3
import sys
import tempfile
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any, Protocol

Record = dict[str, Any]
#: (other node id, edge type, "out" | "in", the edge record)
Incident = tuple[str, str, str, Record]


class StoreUnusable(ValueError):
    """Raised when a store cache cannot be read, written, or replaced."""


class GraphStore(Protocol):
    """Read-only graph access with a deterministic iteration order."""

    def node(self, node_id: str) -> Record | None: ...

    def has(self, node_id: str) -> bool: ...

    def neighbours(self, node_id: str) -> Sequence[Incident]: ...

    def iter_nodes(self) -> Iterator[Record]: ...

    def iter_edges(self) -> Iterator[Record]: ...

    def node_count(self) -> int: ...


class MemoryGraphStore:
    """The default: everything resident, as `Index` has always held it."""

    def __init__(self, nodes: Iterable[Record], edges: Iterable[Record]) -> None:
        self.nodes: dict[str, Record] = {}
        for n in nodes:
            if "id" not in n:
                raise ValueError(f"corrupt node record missing 'id': {n}")
            self.nodes[n["id"]] = n
        self.edges: list[Record] = list(edges)
        self.adj: dict[str, list[Incident]] = defaultdict(list)
        for e in self.edges:
            # The edge record itself (not a copy) rides along: traversal needs
            # `confidence` on CALLS, and `count` on CO_CHANGE, without a lookup.
            if "src" not in e or "dst" not in e or "type" not in e:
                raise ValueError(f"corrupt edge record missing required fields: {e}")
            self.adj[e["src"]].append((e["dst"], e["type"], "out", e))
            self.adj[e["dst"]].append((e["src"], e["type"], "in", e))

    def node(self, node_id: str) -> Record | None:
        return self.nodes.get(node_id)

    def has(self, node_id: str) -> bool:
        return node_id in self.nodes

    def neighbours(self, node_id: str) -> Sequence[Incident]:
        return self.adj.get(node_id, ())

    def iter_nodes(self) -> Iterator[Record]:
        return iter(self.nodes.values())

    def iter_edges(self) -> Iterator[Record]:
        return iter(self.edges)

    def node_count(self) -> int:
        return len(self.nodes)


_SCHEMA = """
CREATE TABLE nodes (seq INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, rec TEXT NOT NULL);
CREATE TABLE edges (seq INTEGER PRIMARY KEY, src TEXT NOT NULL, dst TEXT NOT NULL,
                    type TEXT NOT NULL, rec TEXT NOT NULL);
CREATE INDEX edges_src ON edges (src, seq);
CREATE INDEX edges_dst ON edges (dst, seq);
CREATE TABLE chunks (seq INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, node_id TEXT NOT NULL, path TEXT NOT NULL, rec TEXT NOT NULL);
CREATE INDEX chunks_node_id ON chunks (node_id, seq);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""

#: Bumped when the schema changes, so a database written by an older version is
#: rebuilt rather than queried through a reader that expects a new table.
SQLITE_STORE_FORMAT = 2


class SqliteGraphStore:
    """A prototype on-disk store: one SQLite file, nothing resident but the page cache.

    `build()` writes the database from records (streamed, so a graph larger
    than memory can be loaded); opening it afterwards reads nothing up front.
    Records come back as fresh dicts, so a caller cannot mutate the store.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        # Read-only: a query must never write to the index it reads.
        self._db = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)

    @classmethod
    def build(
        cls,
        path: Path | str,
        nodes: Iterable[Record],
        edges: Iterable[Record],
        chunks: Iterable[Record] | None = None,
        build_id: str | None = None,
    ) -> SqliteGraphStore:
        """Write a database from records, recording which build it came from.

        `build_id` is the index's own id out of `manifest.json`. It is stored
        so `open_graph_store` can tell a current cache from one left over from
        a previous build without hashing the JSONL it came from.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{secrets.token_hex(4)}")
        try:
            db = sqlite3.connect(tmp)
            try:
                db.executescript(_SCHEMA)
                db.executemany(
                    "INSERT INTO nodes (id, rec) VALUES (?, ?)",
                    ((n["id"], json.dumps(n, ensure_ascii=False)) for n in nodes),
                )
                db.executemany(
                    "INSERT INTO edges (src, dst, type, rec) VALUES (?, ?, ?, ?)",
                    (
                        (e["src"], e["dst"], e["type"], json.dumps(e, ensure_ascii=False))
                        for e in edges
                    ),
                )
                if chunks is not None:
                    db.executemany(
                        "INSERT INTO chunks (id, node_id, path, rec) VALUES (?, ?, ?, ?)",
                        (
                            (
                                c.get("id") or "",
                                c.get("node_id") or "",
                                c.get("path") or "",
                                json.dumps(c, ensure_ascii=False),
                            )
                            for c in chunks
                        ),
                    )
                db.executemany(
                    "INSERT INTO meta (key, value) VALUES (?, ?)",
                    [("format", str(SQLITE_STORE_FORMAT)), ("build_id", build_id or "")],
                )
                db.commit()
            finally:
                db.close()
            tmp.replace(path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
        return cls(path)

    @classmethod
    def from_index(
        cls, index_dir: Path | str, path: Path | str, build_id: str | None = None
    ) -> SqliteGraphStore:
        """Load an index's `nodes.jsonl` / `edges.jsonl` / `chunks.jsonl` into a database at `path`."""
        from .export import path as artifact_path
        from .integrity import MAX_JSONL_LINE_BYTES, _iter_raw_lines

        def records(name: str) -> Iterator[Record]:
            file_path = artifact_path(Path(index_dir), name)
            if not file_path.exists():
                return
            with open(file_path, "rb") as fh:
                for raw in _iter_raw_lines(fh, MAX_JSONL_LINE_BYTES, name):
                    if raw.strip():
                        yield json.loads(raw)

        return cls.build(
            path,
            records("nodes.jsonl"),
            records("edges.jsonl"),
            chunks=records("chunks.jsonl"),
            build_id=build_id,
        )

    def meta(self, key: str) -> str | None:
        """One `meta` value, or None when the table or the key is absent.

        Tolerant of both, because a database written before `meta` existed is a
        stale cache to be rebuilt, not a corrupt file to fail on.
        """
        try:
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        except sqlite3.DatabaseError:
            return None
        return None if row is None else str(row[0])

    def close(self) -> None:
        self._db.close()

    def node(self, node_id: str) -> Record | None:
        row = self._db.execute("SELECT rec FROM nodes WHERE id = ?", (node_id,)).fetchone()
        return None if row is None else json.loads(row[0])

    def has(self, node_id: str) -> bool:
        return (
            self._db.execute("SELECT 1 FROM nodes WHERE id = ?", (node_id,)).fetchone() is not None
        )

    def neighbours(self, node_id: str) -> Sequence[Incident]:
        rows = self._db.execute(
            "SELECT other, type, dir, rec FROM ("
            " SELECT seq, 0 AS side, dst AS other, type, 'out' AS dir, rec FROM edges WHERE src = ?"
            " UNION ALL"
            " SELECT seq, 1 AS side, src AS other, type, 'in' AS dir, rec FROM edges WHERE dst = ?"
            ") ORDER BY seq, side",
            (node_id, node_id),
        )
        return [(other, etype, d, json.loads(rec)) for other, etype, d, rec in rows]

    def iter_nodes(self) -> Iterator[Record]:
        for (rec,) in self._db.execute("SELECT rec FROM nodes ORDER BY seq"):
            yield json.loads(rec)

    def iter_edges(self) -> Iterator[Record]:
        for (rec,) in self._db.execute("SELECT rec FROM edges ORDER BY seq"):
            yield json.loads(rec)

    def node_count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0])

    def has_chunks(self) -> bool:
        try:
            return (
                self._db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunks'"
                ).fetchone()
                is not None
            )
        except sqlite3.DatabaseError:
            return False

    def chunk_count(self) -> int:
        try:
            return int(self._db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
        except sqlite3.DatabaseError:
            return 0

    def get_chunk(self, chunk_id: str) -> Record | None:
        try:
            row = self._db.execute("SELECT rec FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
            return None if row is None else json.loads(row[0])
        except sqlite3.DatabaseError:
            return None

    def get_chunks_by_node(self, node_id: str) -> list[Record]:
        try:
            rows = self._db.execute(
                "SELECT rec FROM chunks WHERE node_id = ? ORDER BY seq", (node_id,)
            )
            return [json.loads(rec) for (rec,) in rows]
        except sqlite3.DatabaseError:
            return []

    def get_chunk_by_seq(self, seq: int) -> Record | None:
        try:
            row = self._db.execute("SELECT rec FROM chunks WHERE seq = ?", (seq + 1,)).fetchone()
            return None if row is None else json.loads(row[0])
        except sqlite3.DatabaseError:
            return None

    def iter_chunk_ids(self) -> Iterator[str]:
        try:
            for (cid,) in self._db.execute("SELECT id FROM chunks ORDER BY seq"):
                yield cid
        except sqlite3.DatabaseError:
            return

    def iter_chunks(self) -> Iterator[Record]:
        try:
            for (rec,) in self._db.execute("SELECT rec FROM chunks ORDER BY seq"):
                yield json.loads(rec)
        except sqlite3.DatabaseError:
            return


#: Where a sqlite store lives for an index. At the index *root*, beside
#: `local.json`, rather than inside `agent/`: it is a derived local cache, not
#: a published artifact. Registering it in `export.SECTIONS` would make the
#: Action upload it and the determinism tests byte-compare it, and neither is
#: right for a file that can be regenerated from `nodes.jsonl` at any time.
SQLITE_CACHE_NAME = "graph.sqlite"

#: The store backends `--store` accepts.
STORE_KINDS = ("memory", "sqlite")


def _cache_file_name(index_dir: Path) -> str:
    """The cache file's name when it cannot live beside the index.

    The digest disambiguates two index directories with the same basename; it
    is not a secret, and the whole name is deliberately reproducible, because
    the point of a relocated cache is to be found again on the next run.
    """
    resolved = index_dir.resolve()
    digest = hashlib.sha256(str(resolved).encode()).hexdigest()[:8]
    return f"{resolved.name}_{digest}_{SQLITE_CACHE_NAME}"


def _restrict_to_owner(path: Path) -> None:
    """Narrow `path` to owner-only, best effort.

    The cache holds the graph *and*, since the `chunks` table, source text. In
    the read-only-container fallback that lands in the shared system temp
    directory, where the default mode would leave it world-readable.

    Windows has no POSIX mode bits -- the ACL inherited from the parent governs
    -- so `chmod` there only toggles the read-only flag and is skipped. The
    guard is on `sys.platform` rather than `os.name` because that is the form
    mypy narrows on, and `os.getuid` below does not exist in the win32 stubs.
    """
    if sys.platform == "win32":
        return
    try:
        os.chmod(path, 0o600 if path.is_file() else 0o700)
    except OSError:
        pass


def _private_cache_dir(base: Path) -> Path:
    """A cache directory under `base` that only this user can read or write.

    `base` is the system temp directory, which is world-writable and shared, so
    a single fixed `repo2graph_cache` name under it is a classic insecure-temp
    pattern (CWE-377/CWE-668): another local user can pre-create the directory
    with loose permissions, or plant entries in it, and every cache path here is
    computable from the index path.

    Two things close that. The directory is per-uid, so two users never contend
    for the same name and neither can own the other's. And an existing directory
    is verified rather than trusted -- not a symlink, owned by this uid, and
    tightened to 0700 if anything else can reach it.

    `os.getuid` is POSIX-only, hence the `sys.platform` guards: on Windows the
    name carries no uid and the ownership check is skipped, because the per-user
    `%LOCALAPPDATA%\\Temp` that `gettempdir()` returns there is already private.
    """
    if sys.platform == "win32":
        target = base / "repo2graph_cache"
        if target.is_symlink():
            raise StoreUnusable(
                f"{target} is a symlink; refusing to place the store cache through it"
            )
        target.mkdir(parents=True, exist_ok=True)
        # Re-check after creating. The check above races with `mkdir`, and on
        # POSIX the uid comparison below closes that window because a swapped-in
        # link would not be ours. Windows has no uid to compare, so the only
        # defence is to look again and refuse a directory that turned into a
        # link (or a reparse point) in between.
        if target.is_symlink() or not target.is_dir():
            raise StoreUnusable(
                f"{target} is not a real directory; refusing to place the store cache there"
            )
        return target

    uid = os.getuid()
    target = base / f"repo2graph_cache_{uid}"
    if target.is_symlink():
        raise StoreUnusable(f"{target} is a symlink; refusing to place the store cache through it")
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    st = target.lstat()
    if st.st_uid != uid:
        raise StoreUnusable(
            f"{target} is owned by uid {st.st_uid}, not {uid}; "
            "refusing to write the store cache into another user's directory"
        )
    # `mkdir(mode=...)` applies only on creation, and umask narrows it further,
    # so an already-present directory needs tightening here.
    if st.st_mode & 0o077:
        _restrict_to_owner(target)
    return target


def open_graph_store(index_dir: Path | str, kind: str = "memory") -> GraphStore | None:
    """The store for an index, or None to let `Index` build the default.

    `memory` returns None rather than a `MemoryGraphStore`, so the default path
    is byte-for-byte the one `Index` has always taken -- selecting the default
    explicitly must not route through new code.

    `sqlite` materialises `graph.sqlite` beside the index on first use and
    reuses it while it matches. "Matches" is the index's own `build_id` from
    `manifest.json`, not an mtime: the same test `Index._load_bm25` applies to
    `bm25.meta.json`, and for the same reason. An mtime comparison rebuilt the
    cache on *every* query whenever a source file carried a future timestamp --
    clock skew between a build host and a query host, or a restored archive --
    because the freshly written database was still "older" than its input.
    A `build_id` is an identity rather than an ordering, so it cannot be
    outrun by a clock.

    An unreadable or absent manifest leaves `build_id` empty. An unknown or
    empty id never proves freshness -- only a matching, non-empty build_id
    can be treated as fresh.

    Raises:
        StoreUnusable: the cache path is a symlink or unremovable non-file.
        ValueError: `kind` is not one of `STORE_KINDS`.
        FileNotFoundError: the index has no `nodes.jsonl` / `edges.jsonl`.
    """
    if kind not in STORE_KINDS:
        raise ValueError(f"unknown store {kind!r}; choose from {', '.join(STORE_KINDS)}")
    if kind == "memory":
        return None

    from .events import diagnostic
    from .export import path as artifact_path
    from .lock import BuildLock

    index_dir = Path(index_dir)
    # The pre-checks in `cmd_query` / `cmd_rag` fire first for CLI invocations,
    # but `open_graph_store` is also a standalone entry point.
    for name in ("nodes.jsonl", "edges.jsonl"):
        if not artifact_path(index_dir, name).exists():
            raise FileNotFoundError(
                f"{artifact_path(index_dir, name)} is missing; "
                "build the index before --store sqlite"
            )

    build_id = ""
    manifest_p = artifact_path(index_dir, "manifest.json")
    if manifest_p.exists():
        try:
            loaded = json.loads(manifest_p.read_text(encoding="utf-8"))
            build_id = str(loaded.get("build_id") or "")
        except (OSError, ValueError):
            build_id = ""

    env_cache = os.environ.get("REPO2GRAPH_CACHE_DIR")
    if env_cache:
        target_dir = Path(env_cache)
        # An operator-supplied path, so it can be anything: a file standing
        # where a directory must go, a device, somewhere unwritable. This
        # `mkdir` sits outside the `OSError` handling further down, so any of
        # those surfaced as a raw traceback -- the same defect #501 fixed for
        # the primary path, reached through the env var instead.
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StoreUnusable(
                f"REPO2GRAPH_CACHE_DIR={env_cache!r} is not a usable directory: {exc}"
            ) from None
        db = target_dir / _cache_file_name(index_dir)
    else:
        db = index_dir / SQLITE_CACHE_NAME

    def _is_fresh(s: SqliteGraphStore) -> bool:
        return (
            bool(build_id)
            and s.meta("format") == str(SQLITE_STORE_FORMAT)
            and s.meta("build_id") == build_id
        )

    # A symlinked cache would let a writable link redirect where this writes,
    # so refuse rather than follow it.
    if db.is_symlink():
        raise StoreUnusable(f"{db} is a symlink; refusing to read or write a store through it")

    # Fast path: already exists and is fresh
    if db.is_file():
        try:
            existing = SqliteGraphStore(db)
            if _is_fresh(existing):
                return existing
            existing.close()
        except (sqlite3.DatabaseError, OSError):
            # `OSError` as well as a SQLite error: opening the cache can fail
            # for reasons SQLite never sees, such as a file another process has
            # locked at the OS level on Windows, or one whose permissions were
            # changed since it was written. Either way the right move is the
            # same as for a corrupt file -- fall through and rebuild -- rather
            # than letting it escape, which `_open_index` does not catch and so
            # became a traceback.
            pass

    def _materialize_at(target_db: Path) -> SqliteGraphStore:
        with BuildLock(target_db.parent):
            if target_db.exists():
                try:
                    existing = SqliteGraphStore(target_db)
                    if _is_fresh(existing):
                        return existing
                    existing.close()
                except sqlite3.DatabaseError:
                    pass
                try:
                    target_db.unlink(missing_ok=True)
                except OSError as exc:
                    raise StoreUnusable(
                        f"{target_db} exists but is not a usable store and could not be "
                        f"replaced ({exc}); remove it and retry"
                    ) from None
            return SqliteGraphStore.from_index(index_dir, target_db, build_id=build_id)

    try:
        return _materialize_at(db)
    except OSError as exc:
        if exc.errno in (errno.EROFS, errno.EACCES, errno.EPERM) or isinstance(
            exc, PermissionError
        ):
            try:
                fallback_db = _private_cache_dir(Path(tempfile.gettempdir())) / _cache_file_name(
                    index_dir
                )
                # The same refusal the primary path applies to `db`. This path
                # needs it more: the parent is world-writable, so the cache file
                # is a name another user can plant a symlink at before this
                # process first writes it.
                if fallback_db.is_symlink():
                    raise StoreUnusable(
                        f"{fallback_db} is a symlink; refusing to read or write a store through it"
                    )
                store = _materialize_at(fallback_db)
                _restrict_to_owner(fallback_db)
                return store
            except OSError:
                diagnostic(
                    "warning: cannot write sqlite store cache to disk; falling back to memory store"
                )
                return None
        raise
