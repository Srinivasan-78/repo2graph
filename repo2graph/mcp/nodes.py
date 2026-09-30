"""Shared node helpers for the MCP tools.

Small lookups that both the retrieval and traversal tools need: how to label a
node for a citation, which file contains it, and whether the index has moved
under the open handle.
"""

from __future__ import annotations

from pathlib import Path

from ..edgemeta import cite as edge_cite
from ..export import path as artifact_path
from ..query import Index


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
    try:
        repo_root = _impact_root(index)
        if not repo_root.is_dir():
            return ""
        index_dir = Path(getattr(index, "dir", ".") or ".")
        agent_dir = artifact_path(index_dir, "index.state.json").parent

        from ..status import compute_freshness

        fresh = compute_freshness(repo_root, index_dir, agent_dir)
    except Exception:
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


def _edge_note(index: Index, src: str, dst: str, etype: str) -> str:
    edge = None
    for other, other_type, _direction, record in index.adj.get(src, ()):
        if other == dst and other_type == etype:
            edge = record
            break
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
