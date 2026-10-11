"""Shared node helpers for the MCP tools.

Small lookups that both the retrieval and traversal tools need: how to label a
node for a citation, which file contains it, and whether the index has moved
under the open handle.
"""

from __future__ import annotations

import time
import weakref
from pathlib import Path
from typing import Any

from ..edgemeta import cite as edge_cite
from ..export import path as artifact_path
from ..query import Index

#: Seconds one index's staleness check is reused (`repo2graph-mcp --staleness-ttl`).
#: Process-wide, set once by `server.main` before the first tool call. The default,
#: 0, re-checks the working tree on every call, which is what every client had
#: before the option existed.
STALENESS_TTL: dict[str, float] = {"seconds": 0.0}

# index -> (monotonic time computed, note). Keyed by the Index object, so a
# rebuild -- which `open_index` answers with a new Index -- never reuses a note
# computed against the build it replaced.
_STALENESS_MEMO: weakref.WeakKeyDictionary[Index, tuple[float, str]] = weakref.WeakKeyDictionary()


def _path_secret(index: Index, node_id: str) -> bool:
    node = index.nodes.get(node_id) or {}
    return index._is_secret_path(node.get("path") or "")


def _containing_file(index: Index, node_id: str) -> str | None:
    if node_id.startswith("file:"):
        return node_id if node_id in index.nodes else None
    node = index.nodes.get(node_id) or {}
    path = node.get("path") or ""
    if not path:
        return None
    fid = f"file:{path}"
    return fid if fid in index.nodes else None


def _impact_root(index: Index) -> Path:
    raw = getattr(index, "repo_root", None)
    if raw:
        return Path(str(raw))
    index_dir = Path(getattr(index, "dir", ".") or ".")
    from ..status import stored_source_root

    stored = stored_source_root(artifact_path(index_dir, "manifest.json").parent)
    if stored is not None:
        return stored
    return index_dir.resolve().parent


def _staleness_note(index: Index) -> str:
    """The stale-index warning a tool result leads with, or "" when the index is current.

    Checking re-discovers the working tree, which costs tens of milliseconds on
    a small repository and far more on a large one -- per tool call. Under
    `--staleness-ttl` the answer is reused for that long, which is what lets an
    editor call two tools on every cursor move.
    """
    ttl = STALENESS_TTL["seconds"]
    if ttl > 0:
        hit = _STALENESS_MEMO.get(index)
        if hit is not None and time.monotonic() - hit[0] < ttl:
            return hit[1]
    note = _compute_staleness_note(index)
    if ttl > 0:
        _STALENESS_MEMO[index] = (time.monotonic(), note)
    return note


def _compute_staleness_note(index: Index) -> str:
    try:
        index_dir = Path(getattr(index, "dir", ".") or ".")
        from ..integrity import is_foreign_index

        if is_foreign_index(index_dir):
            return ""
        repo_root = _impact_root(index)
        if not repo_root.is_dir():
            return ""
        agent_dir = artifact_path(index_dir, "index.state.json").parent

        from ..status import compute_freshness

        fresh = compute_freshness(repo_root, index_dir, agent_dir)
    except (OSError, ValueError):
        return ""
    if fresh.status != "stale":
        return ""
    bits = "; ".join(fresh.reasons) or "the working tree has moved since the build"
    return (
        f"_note: index may be stale relative to the working tree ({bits}). "
        f"[cite: path:start-end] line numbers may no longer match the source. "
        f"Rebuild with `repo2graph build` before trusting citations, or check "
        f"`repo2graph index-status` for the full report._\n\n"
    )


def _edge_record(index: Index, src: str, dst: str, etype: str) -> dict[str, Any] | None:
    """The `etype` edge between `src` and `dst`, as `src`'s adjacency holds it."""
    for other, other_type, _direction, record in index.adj.get(src, ()):
        if other == dst and other_type == etype:
            return record
    return None


def _node_fields(index: Index, node_id: str) -> dict[str, Any]:
    """The fields a client needs to show a node and jump to it."""
    node = index.nodes.get(node_id) or {}
    return {
        "node_id": node_id,
        "type": node.get("type") or "",
        "name": node.get("name") or "",
        "qualname": node.get("qualname") or "",
        "kind": node.get("kind") or "",
        "path": node.get("path") or "",
        "start_line": node.get("start_line"),
        "end_line": node.get("end_line"),
        "lang": node.get("lang") or "",
    }


def _cochange_pairs(index: Index, file_id: str) -> list[tuple[str, dict[str, Any]]]:
    """`file_id`'s CO_CHANGE partners and their edges, most frequent first, secrets excluded."""
    pairs = [
        (dst, edge)
        for dst, etype, _direction, edge in index.adj.get(file_id, ())
        if etype == "CO_CHANGE" and not _path_secret(index, dst)
    ]
    pairs.sort(key=lambda p: -(p[1].get("count") or 0))
    return pairs


def _edge_note(index: Index, src: str, dst: str, etype: str) -> str:
    edge = _edge_record(index, src, dst, etype)
    if edge is None:
        return ""

    bits = []
    where = edge_cite(edge)
    if where:
        bits.append(f"at {where}")
    conf = edge.get("confidence")
    if isinstance(conf, (int, float)) and conf < 1.0:
        n = edge.get("candidate_count")
        bits.append(f"AMBIGUOUS {conf} of {n} candidates" if n else f"AMBIGUOUS {conf}")
    return f"  -- {', '.join(bits)}" if bits else ""


def _label(index: Index, node_id: str) -> str:
    node = index.nodes.get(node_id) or {}
    name = node.get("qualname") or node.get("name") or node_id
    where = node.get("path") or ""
    start = node.get("start_line")
    where = f"{where}:{start}" if where and start else where
    return f"`{name}` ({where}) [{node_id}]" if where else f"`{name}` [{node_id}]"
