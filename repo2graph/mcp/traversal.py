"""MCP graph-traversal tools: paths between symbols, impact, and blast radius.

Every traversal here is bounded twice -- by hop count and by visited-node
count -- so a dense graph cannot turn one tool call into an unbounded walk.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from ..query import Index, _fit_lines, count_tokens
from .guardrails import (
    DEFAULT_PATH_EDGE_TYPES,
    MCP_MAX_BUDGET_TOKENS,
    IMPACT_FORMATS,
    MCP_IMPACT_HOPS,
    MCP_IMPACT_LIMIT,
    MCP_MAX_HOPS,
    MCP_MAX_IMPACT_HOPS,
    MCP_MAX_IMPACT_VISITED,
    MCP_MAX_NEIGHBOURS,
    MCP_MAX_NODE_ID_CHARS,
    MCP_MAX_PATH_HOPS,
    MCP_MAX_PATH_VISITED,
    MCP_MAX_PATHS,
    MCP_PATH_HOPS,
    MCP_PATH_PATHS,
    PATH_EDGE_TYPES,
    _clamp,
    _defaulted_notes,
    _scrub_paths,
    _str,
)
from .nodes import _containing_file, _impact_root, _path_secret
from .schemas import ToolError


def _clean_edge_types(value: Any, default=DEFAULT_PATH_EDGE_TYPES) -> tuple[frozenset, str]:
    if not isinstance(value, (list, tuple, set)):
        return frozenset(default), ""
    cleaned = {str(v) for v in value if isinstance(v, str) and v in PATH_EDGE_TYPES}
    if not cleaned:
        return frozenset(default), (
            f"_note: edge_types matched none of {sorted(PATH_EDGE_TYPES)}; "
            f"used the default {list(default)}._\n\n"
        )
    return frozenset(cleaned), ""


def _reconstruct(parents: dict, node: str, root: str, cap: int) -> list[tuple[list, list]]:
    if node == root:
        return [([root], [])]
    out: list[tuple[list, list]] = []
    for parent, etype, direction, edge in parents.get(node, [])[:cap]:
        for nodes, edges in _reconstruct(parents, parent, root, cap):
            out.append((nodes + [node], edges + [(etype, direction, edge)]))
            if len(out) >= cap:
                return out
    return out


def _bidirectional_bfs(
    index: Index,
    from_id: str,
    to_id: str,
    wanted: frozenset,
    max_hops: int,
    visited_cap: int,
):
    if from_id == to_id:
        return {from_id: 0}, {}, {to_id: 0}, {}, from_id, False

    dist_f: dict[str, int] = {from_id: 0}
    parents_f: dict[str, list] = defaultdict(list)
    frontier_f = [from_id]
    dist_b: dict[str, int] = {to_id: 0}
    parents_b: dict[str, list] = defaultdict(list)
    frontier_b = [to_id]
    visited = 2
    meet = None
    hop_f = hop_b = 0
    truncated = False

    while frontier_f and frontier_b and meet is None and hop_f + hop_b < max_hops:
        forward_turn = len(frontier_f) <= len(frontier_b)
        if forward_turn:
            hop_f += 1
            frontier, dist, parents, other = frontier_f, dist_f, parents_f, dist_b
            this_hop = hop_f
        else:
            hop_b += 1
            frontier, dist, parents, other = frontier_b, dist_b, parents_b, dist_f
            this_hop = hop_b
        nxt: list[str] = []
        for nid in frontier:
            for dst, etype, direction, edge in index.adj.get(nid, ()):
                if etype not in wanted or _path_secret(index, dst):
                    continue
                d = dist.get(dst)
                if d is None:
                    dist[dst] = this_hop
                    parents[dst].append((nid, etype, direction, edge))
                    nxt.append(dst)
                    visited += 1
                    if dst in other:
                        meet = dst
                    if visited > visited_cap:
                        truncated = True
                        nxt = []
                        break
                elif d == this_hop:
                    parents[dst].append((nid, etype, direction, edge))
            if truncated:
                break
        if forward_turn:
            frontier_f = nxt
        else:
            frontier_b = nxt

    return dist_f, parents_f, dist_b, parents_b, meet, truncated


def tool_repo_path_between(
    index: Index,
    from_id: str,
    to_id: str,
    max_hops: Any = MCP_PATH_HOPS,
    edge_types: Any = None,
    max_paths: Any = MCP_PATH_PATHS,
) -> str:
    """Bounded, bidirectional path search between `from_id` and `to_id`."""
    from_id = _str(from_id, MCP_MAX_NODE_ID_CHARS)
    to_id = _str(to_id, MCP_MAX_NODE_ID_CHARS)
    if not from_id.strip() or not to_id.strip():
        return ToolError("repo_path_between needs a non-empty `from_id` and `to_id`.")
    missing = [nid for nid in (from_id, to_id) if nid not in index.nodes]
    if missing:
        return ToolError(
            f"node(s) not found: {', '.join(repr(m) for m in missing)}. Ids look like "
            f"file:<path>, sym:<path>::<qualname> or dir:<path>."
        )
    if _path_secret(index, from_id) or _path_secret(index, to_id):
        return ToolError("repo_path_between refuses a secret-excluded node id.")

    note = _defaulted_notes(
        max_hops=(max_hops, MCP_PATH_HOPS), max_paths=(max_paths, MCP_PATH_PATHS)
    )
    hops = _clamp(max_hops, MCP_PATH_HOPS, 1, MCP_MAX_PATH_HOPS)
    paths_limit = _clamp(max_paths, MCP_PATH_PATHS, 1, MCP_MAX_PATHS)
    wanted, type_note = _clean_edge_types(edge_types)
    note += type_note

    dist_f, parents_f, dist_b, parents_b, meet, truncated = _bidirectional_bfs(
        index, from_id, to_id, wanted, hops, MCP_MAX_PATH_VISITED
    )

    if meet is None:
        return note + json.dumps(
            {
                "from": from_id,
                "to": to_id,
                "max_hops": hops,
                "edge_types": sorted(wanted),
                "paths": [],
                "truncated": truncated,
                "message": f"no path found within {hops} hops"
                + (" (search truncated before exhausting the graph)" if truncated else ""),
            },
            indent=2,
        )

    fwd = _reconstruct(parents_f, meet, from_id, paths_limit * 4)
    back = _reconstruct(parents_b, meet, to_id, paths_limit * 4)

    def _min_conf(edges) -> float:
        vals = [
            float(e.get("confidence"))
            for _t, _d, e in edges
            if isinstance(e.get("confidence"), (int, float))
        ]
        return min(vals) if vals else 1.0

    combined: list[tuple[list, list]] = []
    seen_seqs: set[tuple] = set()
    for fnodes, fedges in fwd:
        for bnodes, bedges in back:
            nodes = fnodes + list(reversed(bnodes))[1:]
            key = tuple(nodes)
            if key in seen_seqs:
                continue
            seen_seqs.add(key)
            edges = list(fedges) + [
                (etype, ("in" if direction == "out" else "out"), edge)
                for etype, direction, edge in reversed(bedges)
            ]
            combined.append((nodes, edges))

    combined.sort(key=lambda pe: (len(pe[0]), -_min_conf(pe[1])))
    combined = combined[:paths_limit]

    rendered_paths = []
    for nodes, edges in combined:
        steps = []
        for idx, nid in enumerate(nodes):
            node = index.nodes.get(nid) or {}
            step: dict[str, Any] = {
                "node_id": nid,
                "path": node.get("path") or "",
                "start_line": node.get("start_line"),
                "end_line": node.get("end_line"),
                "via_edge": None,
                "confidence": None,
            }
            if idx > 0:
                etype, direction, edge = edges[idx - 1]
                step["via_edge"] = f"{etype} {direction}"
                conf = edge.get("confidence")
                step["confidence"] = float(conf) if isinstance(conf, (int, float)) else None
            steps.append(step)
        rendered_paths.append(steps)

    return note + json.dumps(
        {
            "from": from_id,
            "to": to_id,
            "max_hops": hops,
            "edge_types": sorted(wanted),
            "paths": rendered_paths,
            "truncated": truncated,
            "min_confidence": [round(_min_conf(e), 3) for _n, e in combined],
        },
        indent=2,
    )


def tool_repo_impact(
    index: Index,
    base: str = "main",
    head: str | None = None,
    diff: str = "",
    max_depth: Any = 2,
    format: str = "markdown",
) -> str:
    """Analyze PR or git diff impact against a base branch using the code graph."""
    base_ref = _str(base, 256).strip() or "main"
    head_ref = _str(head, 256).strip() or None
    diff_text = _str(diff, 1_000_000)
    depth = _clamp(max_depth, 2, 1, MCP_MAX_HOPS)

    from ..impact import (
        analyze_diff_impact,
        format_json,
        format_markdown,
        format_pr_comment,
        format_sarif,
        get_git_diff,
        parse_unified_diff,
    )

    fmt = str(format).lower().strip()
    if fmt == "comment":
        fmt = "pr-comment"
    if fmt not in IMPACT_FORMATS:
        return ToolError(
            f"unknown repo_impact format {str(format)[:40]!r}: expected one of "
            f"{', '.join(IMPACT_FORMATS)}."
        )

    if diff_text.strip() and not parse_unified_diff(diff_text):
        return ToolError(
            "`diff` is not a unified diff: found no `diff --git a/<path> b/<path>` file "
            "header. Pass the output of `git diff` (or omit `diff` to let the server run it)."
        )

    if not diff_text.strip():
        root_path = _impact_root(index)
        try:
            diff_text = get_git_diff(root_path, base=base_ref, head=head_ref)
        except Exception as exc:
            spec = f"{base_ref}...{head_ref}" if head_ref else f"{base_ref} vs the working tree"
            detail = _scrub_paths(str(exc), root_path)
            return ToolError(
                f"Error obtaining git diff ({spec}): {type(exc).__name__}: {detail}\n"
                f"Check that the refs exist in the indexed repository (pass `base` "
                f"if its default branch is not 'main'), or pass the diff directly via `diff`."
            )

    report = analyze_diff_impact(
        index=index,
        diff=diff_text,
        base=base_ref,
        head=head_ref or "HEAD",
        max_depth=depth,
        exclude_secrets=True,
    )

    if fmt == "json":
        rendered = format_json(report)
    elif fmt == "sarif":
        rendered = json.dumps(format_sarif(report), indent=2)
    elif fmt == "pr-comment":
        rendered = format_pr_comment(report)
    else:
        rendered = format_markdown(report)
    if fmt in ("markdown", "pr-comment"):
        rendered = _defaulted_notes(max_depth=(max_depth, 2)) + rendered

    if count_tokens(rendered) <= MCP_MAX_BUDGET_TOKENS:
        return rendered

    if fmt in ("json", "sarif"):
        return json.dumps(
            {
                "truncated": True,
                "reason": (
                    f"report exceeded the {MCP_MAX_BUDGET_TOKENS}-token tool ceiling; "
                    f"per-symbol lists omitted. Narrow the diff, or run "
                    f"`repo2graph impact` for the full report."
                ),
                "base_ref": report.base_ref,
                "head_ref": report.head_ref,
                "risk_level": report.risk_level,
                "blast_radius_score": report.blast_radius_score,
                "metrics": {
                    "files_changed_count": len(report.files_changed),
                    "symbols_changed_count": len(report.symbols_changed),
                    "public_apis_affected_count": len(report.public_apis_affected),
                    "impacted_callers_count": len(report.impacted_callers),
                    "impacted_modules_count": len(report.impacted_modules),
                    "impacted_tests_count": len(report.impacted_tests),
                    "untested_public_apis_count": len(report.untested_public_apis),
                    "suspicious_findings_count": len(report.suspicious_findings),
                },
            },
            indent=2,
        )

    notice = (
        f"\n\n_[truncated to {MCP_MAX_BUDGET_TOKENS} tokens. "
        f"Narrow the diff, or run `repo2graph impact` for the full report.]_"
    )
    room = max(1, MCP_MAX_BUDGET_TOKENS - (len(notice) + 3) // 4)

    head_chunk = rendered[: 4 * room + 4]
    if len(head_chunk) < len(rendered):
        cut = head_chunk.rfind("\n")
        if cut > 0:
            head_chunk = head_chunk[:cut]
    return _fit_lines(head_chunk, room, count_tokens) + notice


def _reverse_closure(
    index: Index, seed: str, etype: str, max_hops: int, visited_cap: int
) -> tuple[dict[str, int], bool]:
    dist: dict[str, int] = {}
    seen = {seed}
    frontier = [seed]
    truncated = False
    hop = 0
    while frontier and hop < max_hops:
        hop += 1
        nxt: list[str] = []
        for nid in frontier:
            for dst, et, direction, _edge in index.adj.get(nid, ()):
                if et != etype or direction != "in" or dst in seen:
                    continue
                if _path_secret(index, dst):
                    continue
                seen.add(dst)
                dist[dst] = hop
                nxt.append(dst)
                if len(seen) > visited_cap:
                    truncated = True
                    nxt = []
                    break
            if truncated:
                break
        frontier = nxt
    return dist, truncated


def tool_repo_blast_radius(
    index: Index,
    node_id: str,
    max_hops: Any = MCP_IMPACT_HOPS,
    include_cochange: Any = True,
    limit: Any = MCP_IMPACT_LIMIT,
) -> str:
    """Reverse reachability analysis: callers, subclasses, importers, and co-changes."""
    node_id = _str(node_id, MCP_MAX_NODE_ID_CHARS)
    if not node_id.strip():
        return ToolError("repo_blast_radius needs a non-empty `node_id`.")
    if node_id not in index.nodes:
        return ToolError(
            f"node not found: {node_id!r}. Ids look like file:<path>, "
            f"sym:<path>::<qualname> or dir:<path>."
        )
    if _path_secret(index, node_id):
        return ToolError("repo_blast_radius refuses a secret-excluded node id.")

    note = _defaulted_notes(max_hops=(max_hops, MCP_IMPACT_HOPS), limit=(limit, MCP_IMPACT_LIMIT))
    hops = _clamp(max_hops, MCP_IMPACT_HOPS, 1, MCP_MAX_IMPACT_HOPS)
    lim = _clamp(limit, MCP_IMPACT_LIMIT, 1, MCP_MAX_NEIGHBOURS)
    want_cochange = include_cochange if isinstance(include_cochange, bool) else True

    def _rows(dist: dict[str, int]) -> list[dict[str, Any]]:
        rows = []
        for nid, hop in sorted(dist.items(), key=lambda kv: (kv[1], kv[0]))[:lim]:
            node = index.nodes.get(nid) or {}
            rows.append(
                {
                    "node_id": nid,
                    "path": node.get("path") or "",
                    "start_line": node.get("start_line"),
                    "end_line": node.get("end_line"),
                    "hop": hop,
                }
            )
        return rows

    callers_dist, callers_trunc = _reverse_closure(
        index, node_id, "CALLS", hops, MCP_MAX_IMPACT_VISITED
    )
    subclasses_dist, subclasses_trunc = _reverse_closure(
        index, node_id, "INHERITS", hops, MCP_MAX_IMPACT_VISITED
    )
    file_id = _containing_file(index, node_id)
    importers_dist: dict[str, int] = {}
    importers_trunc = False
    if file_id is not None:
        importers_dist, importers_trunc = _reverse_closure(
            index, file_id, "IMPORTS", hops, MCP_MAX_IMPACT_VISITED
        )

    cochange_rows: list[dict[str, Any]] = []
    cochange_trunc = False
    if want_cochange and file_id is not None:
        pairs = [
            (dst, edge)
            for dst, etype, _direction, edge in index.adj.get(file_id, ())
            if etype == "CO_CHANGE" and not _path_secret(index, dst)
        ]
        pairs.sort(key=lambda p: -(p[1].get("count") or 0))
        cochange_trunc = len(pairs) > lim
        for dst, edge in pairs[:lim]:
            cochange_rows.append(
                {
                    "path": (index.nodes.get(dst) or {}).get("path") or dst,
                    "count": edge.get("count") or 0,
                }
            )

    callers_rows = _rows(callers_dist)
    subclasses_rows = _rows(subclasses_dist)
    importers_rows = _rows(importers_dist)

    affected_nodes = set(callers_dist) | set(subclasses_dist) | set(importers_dist)
    affected_files = {(index.nodes.get(nid) or {}).get("path") for nid in affected_nodes}
    affected_files |= {row["path"] for row in cochange_rows}
    affected_files.discard(None)
    affected_files.discard("")

    truncated = (
        callers_trunc
        or subclasses_trunc
        or importers_trunc
        or cochange_trunc
        or len(callers_dist) > lim
        or len(subclasses_dist) > lim
        or len(importers_dist) > lim
    )
    max_hop_reached = max(
        [0, *callers_dist.values(), *subclasses_dist.values(), *importers_dist.values()]
    )

    return note + json.dumps(
        {
            "node_id": node_id,
            "callers": callers_rows,
            "subclasses": subclasses_rows,
            "importers": importers_rows,
            "cochange": cochange_rows,
            "summary": {
                "files_affected": len(affected_files),
                "symbols_affected": len(affected_nodes),
                "max_hop_reached": max_hop_reached,
                "truncated": truncated,
            },
        },
        indent=2,
    )
