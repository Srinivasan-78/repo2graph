"""Graph storage behind one small interface (#306).

Traversal and retrieval read the graph through `GraphStore`: a node by id, a
node's incident edges, and the nodes and edges in file order. That is the whole
surface the call sites in `query.py`, `explain.py` and `mcp/` actually use, so
a different backend is one implementation rather than a change at every site.

`MemoryGraphStore` is the default and the reference: dicts and lists built from
`nodes.jsonl` / `edges.jsonl`, exactly what `Index` always held.
`SqliteGraphStore` is a prototype for graphs too big to keep resident. It uses
only the standard library's `sqlite3`, so the zero-dependency promise holds,
and it answers every call from disk.

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

import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any, Protocol

Record = dict[str, Any]
#: (other node id, edge type, "out" | "in", the edge record)
Incident = tuple[str, str, str, Record]


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
        self.nodes: dict[str, Record] = {n["id"]: n for n in nodes}
        self.edges: list[Record] = list(edges)
        self.adj: dict[str, list[Incident]] = defaultdict(list)
        for e in self.edges:
            # The edge record itself (not a copy) rides along: traversal needs
            # `confidence` on CALLS, and `count` on CO_CHANGE, without a lookup.
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
"""


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
        cls, path: Path | str, nodes: Iterable[Record], edges: Iterable[Record]
    ) -> SqliteGraphStore:
        path = Path(path)
        tmp = path.with_name(path.name + ".tmp")
        tmp.unlink(missing_ok=True)
        db = sqlite3.connect(tmp)
        try:
            db.executescript(_SCHEMA)
            db.executemany(
                "INSERT INTO nodes (id, rec) VALUES (?, ?)",
                ((n["id"], json.dumps(n, ensure_ascii=False)) for n in nodes),
            )
            db.executemany(
                "INSERT INTO edges (src, dst, type, rec) VALUES (?, ?, ?, ?)",
                ((e["src"], e["dst"], e["type"], json.dumps(e, ensure_ascii=False)) for e in edges),
            )
            db.commit()
        finally:
            db.close()
        tmp.replace(path)
        return cls(path)

    @classmethod
    def from_index(cls, index_dir: Path | str, path: Path | str) -> SqliteGraphStore:
        """Load an index's `nodes.jsonl` / `edges.jsonl` into a database at `path`."""
        from .export import path as artifact_path
        from .integrity import MAX_JSONL_LINE_BYTES, _iter_raw_lines

        def records(name: str) -> Iterator[Record]:
            with open(artifact_path(Path(index_dir), name), "rb") as fh:
                for raw in _iter_raw_lines(fh, MAX_JSONL_LINE_BYTES, name):
                    if raw.strip():
                        yield json.loads(raw)

        return cls.build(path, records("nodes.jsonl"), records("edges.jsonl"))

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
