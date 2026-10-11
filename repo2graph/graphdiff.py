"""What changed structurally between two builds of a graph (#395).

One computation behind three outputs: `repo2graph diff`, the per-build
`human/CHANGELOG.md` (`changelog.py`), and through that file the GitHub
Action's job summary.

Two choices make the output readable rather than merely complete:

- **Renames are paired, not reported as delete + add.** A symbol id is
  `sym:<path>::<qualname>`, so moving a file renames every symbol in it. A
  removed and an added symbol of the same kind with the same body are one
  symbol renamed or moved; with the same qualname but a different body they
  are a *possible* rename, and are labelled so rather than guessed.
- **Confidence-only edge changes are not changes.** Adding one `get` anywhere
  shifts the `1/n` confidence of every ambiguous `get` call in the repository.
  An edge counts as changed when its source, target or type does; confidence
  moves are counted, and listed only on request.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

Record = dict[str, Any]


def body_hashes(chunks: Iterable[Record]) -> dict[str, str]:
    """node id -> sha256 of its source lines, for symbols held in one chunk.

    The chunk header names the file, so it is cut off: the last
    `end_line - start_line + 1` lines are the body. A symbol split across
    several chunks gets no hash and is never paired as an exact rename.
    """
    out: dict[str, str] = {}
    for c in chunks:
        nid = c.get("node_id")
        start, end = c.get("start_line"), c.get("end_line")
        if not nid or c.get("split") or not isinstance(start, int) or not isinstance(end, int):
            continue
        lines = (c.get("text") or "").split("\n")
        body = "\n".join(lines[-(end - start + 1) :]) if end >= start else ""
        out[nid] = hashlib.sha256(body.encode("utf8", "surrogateescape")).hexdigest()
    return out


def _edge_key(e: Record) -> tuple[Any, Any, Any]:
    return (e.get("src"), e.get("dst"), e.get("type"))


def _pair_renames(
    removed: dict[str, Record],
    added: dict[str, Record],
    old_hash: dict[str, str],
    new_hash: dict[str, str],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """(renames, possible renames) between removed and added symbols, one-to-one."""

    def kind(n: Record) -> tuple[Any, Any]:
        return (n.get("type"), n.get("kind"))

    renames: list[dict[str, str]] = []
    by_body: dict[tuple[Any, ...], list[str]] = defaultdict(list)
    for nid, n in added.items():
        if n.get("type") == "symbol" and nid in new_hash:
            by_body[(*kind(n), new_hash[nid])].append(nid)
    for nid in sorted(removed):
        n = removed[nid]
        if n.get("type") != "symbol" or nid not in old_hash:
            continue
        cands = by_body.get((*kind(n), old_hash[nid]), [])
        if len(cands) == 1:
            renames.append({"from": nid, "to": cands.pop(), "match": "same body"})
    paired_old = {r["from"] for r in renames}
    paired_new = {r["to"] for r in renames}

    possible: list[dict[str, str]] = []
    by_qual: dict[tuple[Any, ...], list[str]] = defaultdict(list)
    for nid, n in added.items():
        if n.get("type") == "symbol" and nid not in paired_new:
            by_qual[(*kind(n), n.get("qualname"))].append(nid)
    for nid in sorted(removed):
        n = removed[nid]
        if n.get("type") != "symbol" or nid in paired_old:
            continue
        cands = by_qual.get((*kind(n), n.get("qualname")), [])
        if len(cands) == 1:
            possible.append({"from": nid, "to": cands.pop(), "match": "same name, body changed"})
    return renames, possible


def _pair_files(
    removed: dict[str, Record], added: dict[str, Record], symbol_pairs: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Removed files whose paired symbols all moved to one added file."""
    targets: dict[str, set[str]] = defaultdict(set)
    for r in symbol_pairs:
        old_path, new_path = removed[r["from"]].get("path"), added[r["to"]].get("path")
        if old_path and new_path:
            targets[f"file:{old_path}"].add(f"file:{new_path}")
    taken: set[str] = set()
    out = []
    for fid in sorted(targets):
        (dest,) = targets[fid] if len(targets[fid]) == 1 else (None,)
        if fid in removed and dest in added and dest not in taken:
            taken.add(dest)
            out.append({"from": fid, "to": dest, "match": "its symbols moved"})
    return out


def diff_graphs(
    old_nodes: Iterable[Record],
    old_edges: Iterable[Record],
    new_nodes: Iterable[Record],
    new_edges: Iterable[Record],
    *,
    old_chunks: Iterable[Record] = (),
    new_chunks: Iterable[Record] = (),
    detect_renames: bool = True,
) -> dict[str, Any]:
    """The structural difference from the old graph to the new one.

    Returns a dict of sorted lists: `added_nodes`, `removed_nodes`, `renamed`,
    `possible_renames`, `added_edges`, `removed_edges`,
    `confidence_changed_edges`, and `callers_changed` (per node present in
    both builds, the CALLS sources gained and lost). Node and edge entries are
    the records themselves.
    """
    old_n = {n["id"]: n for n in old_nodes if "id" in n}
    new_n = {n["id"]: n for n in new_nodes if "id" in n}
    removed = {nid: n for nid, n in old_n.items() if nid not in new_n}
    added = {nid: n for nid, n in new_n.items() if nid not in old_n}

    renames: list[dict[str, str]] = []
    possible: list[dict[str, str]] = []
    if detect_renames and removed and added:
        renames, possible = _pair_renames(
            removed, added, body_hashes(old_chunks), body_hashes(new_chunks)
        )
        renames += _pair_files(removed, added, renames + possible)
    # Paired symbols are reported in their own sections, not as added/removed.
    # Only exact renames rewrite ids for the edge comparison: an edge of a
    # moved symbol is the same edge, but a possible rename is a guess and must
    # not hide a change.
    alias: dict[Any, Any] = {r["from"]: r["to"] for r in renames}
    for r in renames + possible:
        removed.pop(r["from"], None)
        added.pop(r["to"], None)

    def moved(e: Record) -> Record:
        src, dst = e.get("src"), e.get("dst")
        if src in alias or dst in alias:
            e = {**e, "src": alias.get(src, src), "dst": alias.get(dst, dst)}
        return e

    old_e = {_edge_key(e): e for e in map(moved, old_edges)}
    new_e = {_edge_key(e): e for e in new_edges}
    added_edges = [e for k, e in new_e.items() if k not in old_e]
    removed_edges = [e for k, e in old_e.items() if k not in new_e]
    conf_changed = [
        {"edge": e, "was": old_e[k].get("confidence"), "now": e.get("confidence")}
        for k, e in new_e.items()
        if k in old_e and old_e[k].get("confidence") != e.get("confidence")
    ]

    def callers(edges: Iterable[Record]) -> dict[str, set[str]]:
        out: dict[str, set[str]] = defaultdict(set)
        for e in edges:
            if e.get("type") == "CALLS":
                out[e["dst"]].add(e["src"])
        return out

    old_callers, new_callers = callers(old_e.values()), callers(new_e.values())
    callers_changed = []
    for nid in sorted(set(old_callers) | set(new_callers)):
        if nid not in new_n:
            continue
        gained = sorted(new_callers.get(nid, set()) - old_callers.get(nid, set()))
        lost = sorted(old_callers.get(nid, set()) - new_callers.get(nid, set()))
        if gained or lost:
            callers_changed.append({"node": nid, "gained": gained, "lost": lost})

    def edge_sort(e: Record) -> tuple[str, str, str]:
        return (e.get("type") or "", e.get("src") or "", e.get("dst") or "")

    return {
        "added_nodes": sorted(added.values(), key=lambda n: n["id"]),
        "removed_nodes": sorted(removed.values(), key=lambda n: n["id"]),
        "renamed": renames,
        "possible_renames": possible,
        "added_edges": sorted(added_edges, key=edge_sort),
        "removed_edges": sorted(removed_edges, key=edge_sort),
        "confidence_changed_edges": sorted(conf_changed, key=lambda c: edge_sort(c["edge"])),
        "callers_changed": callers_changed,
    }


def diff_indexes(old_dir: str | Path, new_dir: str | Path) -> dict[str, Any]:
    """`diff_graphs` over two index directories' nodes, edges and chunks."""
    from .export import path as artifact_path
    from .query import read_jsonl

    def load(d: str | Path, name: str) -> list[Record]:
        p = artifact_path(d, name)
        return read_jsonl(p) if p.exists() else []

    return diff_graphs(
        load(old_dir, "nodes.jsonl"),
        load(old_dir, "edges.jsonl"),
        load(new_dir, "nodes.jsonl"),
        load(new_dir, "edges.jsonl"),
        old_chunks=load(old_dir, "chunks.jsonl"),
        new_chunks=load(new_dir, "chunks.jsonl"),
    )


def render_text(d: dict[str, Any], *, limit: int = 50, show_confidence: bool = False) -> str:
    """The diff as plain text, each list capped at `limit` lines."""

    def capped(lines: list[str]) -> list[str]:
        if len(lines) > limit:
            return lines[:limit] + [f"  ... and {len(lines) - limit} more"]
        return lines

    def edge(e: Record) -> str:
        return f"{e.get('type')}: {e.get('src')} -> {e.get('dst')}"

    syms_add = [n for n in d["added_nodes"] if n.get("type") == "symbol"]
    syms_del = [n for n in d["removed_nodes"] if n.get("type") == "symbol"]
    files_moved = [r for r in d["renamed"] if str(r["from"]).startswith("file:")]
    syms_renamed = [r for r in d["renamed"] if r not in files_moved]
    out = [
        f"symbols: +{len(syms_add)} -{len(syms_del)}, {len(syms_renamed)} renamed, "
        f"{len(d['possible_renames'])} possible renames; {len(files_moved)} files moved",
        f"edges: +{len(d['added_edges'])} -{len(d['removed_edges'])} "
        f"({len(d['confidence_changed_edges'])} confidence-only changes not counted)",
    ]
    sections: list[tuple[str, list[str]]] = [
        ("added symbols", [f"  + {n['id']}" for n in syms_add]),
        ("removed symbols", [f"  - {n['id']}" for n in syms_del]),
        ("moved files", [f"  {r['from']} -> {r['to']}" for r in files_moved]),
        ("renamed (same body)", [f"  {r['from']} -> {r['to']}" for r in syms_renamed]),
        (
            "possible renames (same name, body changed)",
            [f"  {r['from']} -> {r['to']}" for r in d["possible_renames"]],
        ),
        (
            "callers changed",
            [
                f"  {c['node']}: "
                + ", ".join([*("+" + s for s in c["gained"]), *("-" + s for s in c["lost"])])
                for c in d["callers_changed"]
            ],
        ),
        ("added edges", [f"  + {edge(e)}" for e in d["added_edges"]]),
        ("removed edges", [f"  - {edge(e)}" for e in d["removed_edges"]]),
    ]
    if show_confidence:
        sections.append(
            (
                "confidence changed",
                [
                    f"  {edge(c['edge'])}: {c['was']} -> {c['now']}"
                    for c in d["confidence_changed_edges"]
                ],
            )
        )
    for title, lines in sections:
        if lines:
            out += ["", f"{title}:", *capped(lines)]
    return "\n".join(out) + "\n"
