"""`human/CHANGELOG.md`: a per-push structural diff of the graph.

Written once per `repo2graph build`, comparing the graph just built against
whatever `agent/nodes.jsonl` / `agent/edges.jsonl` were on disk *before* this
build overwrote them. Human-facing only -- this module is never imported by
`query.py`, `mcp.py` or any other retrieval path, and CHANGELOG.md is not
listed in `chunk_fields`/`FILE_NOTES` as something an agent should read.

Call order from `cli.cmd_build` when `"overview"` is in `--formats` (otherwise
`previous_state` is not called and no CHANGELOG.md is written):

1. `previous_state(outdir)` -- BEFORE `dump_all` runs, while the previous
   build's `agent/nodes.jsonl`/`agent/edges.jsonl` are still the files on disk.
2. `dump_all(...)` overwrites those files with the new build.
3. `write_changelog(outdir, g, prev_state, ...)` -- diffs the in-memory graph
   `g` against the snapshot from step 1.
"""

import json
from pathlib import Path
from typing import Any

from .export import atomic_write, make_path, path as artifact_path
from .graph import Graph
from .query import read_jsonl

MAX_ITEMS = 50

INITIAL_MESSAGE = "## Initial build — no previous index to diff against.\n"

# Same edge types as export.write_overview's hub ranking, so a node counted as
# a hub there and a node counted as a "hotspot" here agree with each other.
INDEGREE_EDGE_TYPES = ("IMPORTS", "CALLS")

# A hotspot is a node whose in-degree grew by at least this many since the
# previous build.
HOTSPOT_DELTA = 3


def previous_state(
    outdir: str | Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    """Snapshot the previous build's nodes/edges, or None if there is none.

    Must be called before `export.dump_all` runs: `dump_all` overwrites
    `agent/nodes.jsonl` and `agent/edges.jsonl` with the *new* build's data,
    and by the time this function's return value is used the on-disk files no
    longer hold what it needs to read.

    Returns:
        `(prev_nodes, prev_edges)` read from `agent/nodes.jsonl` and
        `agent/edges.jsonl`, or None when either file is missing or
        unreadable -- the "no previous build" case, which callers must render
        as `INITIAL_MESSAGE` rather than an empty diff.
    """
    nodes_path = artifact_path(outdir, "nodes.jsonl")
    edges_path = artifact_path(outdir, "edges.jsonl")
    if not nodes_path.exists() or not edges_path.exists():
        return None
    try:
        prev_nodes = read_jsonl(nodes_path)
        prev_edges = read_jsonl(edges_path)
    except (OSError, ValueError):
        return None
    return prev_nodes, prev_edges


def _prev_commit_sha(outdir: str | Path) -> str | None:
    """A previously recorded commit sha, if one exists.

    `agent/index.json` is written only by `repo2graph github`/`fetch.py`, so a
    plain `repo2graph build` run over the same `-o` directory has usually never
    recorded one; that is expected, not an error -- the header then shows only
    the current sha.
    """
    idx_path = artifact_path(outdir, "index.json")
    if not idx_path.exists():
        return None
    try:
        data = json.loads(idx_path.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return None
    sha = data.get("commit") if isinstance(data, dict) else None
    return sha if isinstance(sha, str) and sha and sha != "unknown" else None


def resolve_shas(repo_path: str | Path, outdir: str | Path) -> tuple[str | None, str | None]:
    """(current_short_sha, previous_short_sha) for the CHANGELOG.md header.

    Call before `dump_all` writes a new `agent/index.json` (`build` itself
    never writes one, but a directory reused from a prior `repo2graph github`
    run might already carry one) -- same "read the old state first" ordering
    as `previous_state`.
    """
    from .fetch import head_sha

    prev_sha = _prev_commit_sha(outdir)
    sha = head_sha(Path(repo_path))
    current = sha if sha and sha != "unknown" else None
    return current, prev_sha


def _edge_key(e: dict[str, Any]) -> tuple[Any, Any, Any]:
    return (e.get("src"), e.get("dst"), e.get("type"))


def _indegree(edges: list[dict[str, Any]]) -> dict[str, int]:
    deg: dict[str, int] = {}
    for e in edges:
        if e.get("type") in INDEGREE_EDGE_TYPES:
            dst = e.get("dst")
            if dst is not None:
                deg[dst] = deg.get(dst, 0) + 1
    return deg


def _node_line(n: dict[str, Any]) -> str:
    return f"- {n.get('id')}  ({n.get('type', '')})"


def _edge_line(e: dict[str, Any]) -> str:
    conf = e.get("confidence")
    suffix = f"  (confidence: {conf})" if conf is not None else ""
    return f"- {e.get('type')}: {e.get('src')} → {e.get('dst')}{suffix}"


def _section(lines: list[str], count: int) -> list[str]:
    """Cap a section at MAX_ITEMS lines, appending the "... and N more" line."""
    if count > MAX_ITEMS:
        lines = lines[:MAX_ITEMS] + [f"... and {count - MAX_ITEMS} more"]
    return lines


def build_changelog_markdown(
    g: Graph,
    prev_nodes: list[dict[str, Any]],
    prev_edges: list[dict[str, Any]],
    short_sha: str | None,
    prev_short_sha: str | None,
    build_date: str,
) -> str:
    """The CHANGELOG.md body for a build that has a previous index to diff.

    `g` is the just-built `Graph` (its `.nodes`/`.edges` are the *new* state);
    `prev_nodes`/`prev_edges` is the snapshot `previous_state` took before this
    build's `dump_all` overwrote them.
    """
    prev_node_ids = {n["id"] for n in prev_nodes if "id" in n}
    curr_node_ids = set(g.nodes)

    new_nodes = sorted(
        (n for nid, n in g.nodes.items() if nid not in prev_node_ids),
        key=lambda n: n["id"],
    )
    removed_nodes = sorted(
        (n for n in prev_nodes if n.get("id") not in curr_node_ids),
        key=lambda n: n.get("id") or "",
    )

    prev_edge_keys = {_edge_key(e) for e in prev_edges}
    curr_edge_keys = {_edge_key(e) for e in g.edges}
    new_edges = sorted(
        (e for e in g.edges if _edge_key(e) not in prev_edge_keys),
        key=lambda e: (e.get("type") or "", e.get("src") or "", e.get("dst") or ""),
    )
    removed_edges = sorted(
        (e for e in prev_edges if _edge_key(e) not in curr_edge_keys),
        key=lambda e: (e.get("type") or "", e.get("src") or "", e.get("dst") or ""),
    )

    prev_indeg = _indegree(prev_edges)
    curr_indeg = _indegree(g.edges)
    hotspots = sorted(
        (
            (nid, prev_indeg.get(nid, 0), new_deg)
            for nid, new_deg in curr_indeg.items()
            if new_deg - prev_indeg.get(nid, 0) >= HOTSPOT_DELTA
        ),
        key=lambda t: (-(t[2] - t[1]), t[0]),
    )

    if short_sha and prev_short_sha:
        header = f"## Graph delta — {short_sha} vs {prev_short_sha}  ({build_date})"
    elif short_sha:
        header = f"## Graph delta — {short_sha}  ({build_date})"
    else:
        header = f"## Graph delta  ({build_date})"

    out = [header]
    if new_nodes:
        out += ["", f"### New nodes (+{len(new_nodes)})"]
        out += _section([_node_line(n) for n in new_nodes], len(new_nodes))
    if removed_nodes:
        out += ["", f"### Removed nodes (-{len(removed_nodes)})"]
        out += _section([_node_line(n) for n in removed_nodes], len(removed_nodes))
    if new_edges:
        out += ["", f"### New edges (+{len(new_edges)})"]
        out += _section([_edge_line(e) for e in new_edges], len(new_edges))
    if removed_edges:
        out += ["", f"### Removed edges (-{len(removed_edges)})"]
        out += _section([_edge_line(e) for e in removed_edges], len(removed_edges))
    if hotspots:
        out += ["", "### New hotspots (nodes that gained 3+ in-degree since last build)"]
        lines = [f"- {nid}  (was {old}, now {new})" for nid, old, new in hotspots]
        out += _section(lines, len(hotspots))

    return "\n".join(out) + "\n"


def write_changelog(
    outdir: str | Path,
    g: Graph,
    prev_state: tuple[list[dict[str, Any]], list[dict[str, Any]]] | None,
    short_sha: str | None,
    prev_short_sha: str | None,
    build_date: str,
) -> Path:
    """Write `human/CHANGELOG.md` via the shared `atomic_write`.

    `prev_state` is `previous_state(outdir)`'s return value, captured before
    `dump_all` ran; None means "no previous build" and produces the
    `INITIAL_MESSAGE` file verbatim.
    """
    target = make_path(outdir, "CHANGELOG.md")
    if prev_state is None:
        text = INITIAL_MESSAGE
    else:
        prev_nodes, prev_edges = prev_state
        text = build_changelog_markdown(
            g, prev_nodes, prev_edges, short_sha, prev_short_sha, build_date
        )
    with atomic_write(target, "w", encoding="utf8", newline="\n") as fh:
        fh.write(text)
    return target
