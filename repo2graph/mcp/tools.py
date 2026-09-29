"""Tool definitions, execution handlers, and index management for MCP."""

from __future__ import annotations

import json
import re
import sys
import threading
import weakref
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..cache import CACHEABLE_TOOLS, ResultCache, make_key
from ..edgemeta import cite as edge_cite
from ..export import path as artifact_path
from ..query import (
    ALL_EDGE_DIRS,
    DEFAULT_EDGE_TYPES,
    QUALNAME_SEP_RE,
    Index,
    _fit_lines,
    count_tokens,
)
from .guardrails import (
    AUTO_BUILD_FORMATS,
    BUDGET_DEFAULTED,
    DEFAULT_PATH_EDGE_TYPES,
    EMPTY_RESULT,
    IMPACT_FORMATS,
    MCP_BUDGET_TOKENS,
    MCP_FIND_LIMIT,
    MCP_IMPACT_HOPS,
    MCP_IMPACT_LIMIT,
    MCP_MAX_BUDGET_TOKENS,
    MCP_MAX_FIND_LIMIT,
    MCP_MAX_HOPS,
    MCP_MAX_IMPACT_HOPS,
    MCP_MAX_IMPACT_VISITED,
    MCP_MAX_K,
    MCP_MAX_NEIGHBOURS,
    MCP_MAX_NODE_ID_CHARS,
    MCP_MAX_PATH_HOPS,
    MCP_MAX_PATH_VISITED,
    MCP_MAX_PATHS,
    MCP_MAX_QUERY_CHARS,
    MCP_MAX_READ_CHARS,
    MCP_MAX_READ_CONTEXT,
    MCP_MAX_TASK_ID_CHARS,
    MCP_NEIGHBOUR_LIMIT,
    MCP_PATH_HOPS,
    MCP_PATH_PATHS,
    MCP_READ_CONTEXT,
    PATH_EDGE_TYPES,
    _clamp,
    _defaulted_notes,
    _int,
    _scrub_paths,
    _str,
)


class ToolError(str):
    """A tool result that reports a failure rather than an answer.

    Behaves as a plain string, but transports inspect it to set `isError: true`.
    """

    __slots__ = ()


TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

TOOL_ANNOTATIONS_AUTO_BUILD = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}

TOOL_TITLES = {
    "repo_map": "Repository Map",
    "repo_search": "Search Codebase",
    "repo_neighbours": "Traverse Graph Neighbors",
    "repo_find_symbol": "Find Symbol by Name",
    "repo_read": "Read Cited Source Window",
    "repo_path_between": "Find Path Between Nodes",
    "repo_impact": "Analyze PR Impact",
    "repo_blast_radius": "Analyze Blast Radius",
    "repo_cache_stats": "Cache Statistics",
    "repo_build_status": "Build Task Status",
}

TOOL_DESCRIPTIONS = {
    "repo_map": (
        "Retrieve a high-level structural map of the repository: languages, hub files, "
        "and top entry points. Read-only, deterministic, zero side effects. "
        "When to use: call this first at session start to understand codebase layout and "
        "identify entry points before detailed queries. Use when deciding where to investigate. "
        "When NOT to use: do not use to search code (use repo_search) or inspect call "
        "graphs (use repo_neighbours). Output: markdown summary of languages, hub files, "
        "and entry points, prefixed with a staleness note when the indexed working tree "
        "has changed since the index was built -- treat citations as suspect until rebuilt."
    ),
    "repo_search": (
        "Search repository code for answers to questions using BM25 lexical ranking "
        "expanded with graph neighbours. Read-only, no side effects, secret files (.env) "
        "excluded. When to use: use for open-ended queries, locating implementations, "
        "or finding error strings. When NOT to use: do not use when you already have a "
        "symbol node_id and want callers/callees (use repo_neighbours); do not use for broad "
        "repo layout (use repo_map). Output: markdown citation blocks `[cite: path:start-end]` "
        "bounded by budget_tokens."
    ),
    "repo_neighbours": (
        "Traverse code graph relationships from a known symbol or file node_id (callers, "
        "callees, base classes, definitions). Read-only, deterministic traversal, no side effects. "
        "When to use: use with a specific node_id (e.g. from repo_search citations) to inspect "
        "callers (CALLS in), callees (CALLS out), inheritance, or definitions. When NOT to use: "
        "do not use for text search across code (use repo_search) or repo overview (use repo_map). "
        "Output: markdown list formatted as `- <EDGE_TYPE> <in|out>: <name> (<path:line>) [<node_id>]`."
    ),
    "repo_impact": (
        "Analyze PR or git diff impact against a base branch using the code graph. "
        "Detects changed symbols, affected public APIs, impacted callers, test coverage, "
        "and architectural blast radius with grounded citations. Read-only, deterministic, "
        "zero side effects. When to use: use when assessing PR risk, planning test execution, "
        "evaluating breaking API changes, or investigating diff blast radius. When NOT to use: "
        "do not use for generic lexical code search (use repo_search), or to see what depends "
        "on a single symbol with no diff in hand (use repo_blast_radius). "
        "Output: structured markdown impact report or PR summary."
    ),
    "repo_find_symbol": (
        "Look up a symbol or file's node_id by name, for feeding into repo_neighbours, "
        "repo_read, repo_path_between or repo_blast_radius. Read-only, deterministic, zero "
        "side effects, secret files (.env) excluded. Matches an exact name first, then "
        "case-insensitively, then the last segment of a qualname; an ambiguous name returns "
        "every candidate with its path so you disambiguate rather than the server guessing. "
        "When to use: you already know a name (from a traceback, a grep, a review comment) "
        "and want its node_id with no repo_search round trip. When NOT to use: do not use for "
        "open-ended text search (use repo_search) or for repo layout (use repo_map). "
        "Output: JSON array of {node_id, name, qualname, kind, path, start_line, end_line, lang}."
    ),
    "repo_read": (
        "Read a widened window of source text around a citation, from the indexed chunks "
        "rather than the filesystem -- works over HTTP, from a different machine, with no "
        "shared filesystem, because chunks have already passed secret-path exclusion and "
        "redaction. Read-only, deterministic, zero side effects. When to use: use after "
        "repo_search or repo_neighbours to see more lines around a `[cite: path:start-end]` "
        "citation, with optional `context` lines each side. When NOT to use: do not use for a "
        "path never indexed, or an absolute or '..' path (both are refused); use repo_search "
        "or repo_find_symbol to find a valid path first. "
        "Output: `[cite: path:start-end]` header plus the text, bounded in size."
    ),
    "repo_path_between": (
        "Find a bounded, bidirectional path between two node_ids over CALLS/DEFINES/IMPORTS "
        "edges (CO_CHANGE only if named explicitly), reporting the minimum confidence along "
        "each path. Read-only, deterministic traversal, no side effects. When to use: use for "
        "'how does X reach Y' questions a text search cannot answer, e.g. the call chain from "
        "an HTTP handler to a database write. When NOT to use: do not use for one-hop "
        "neighbours (use repo_neighbours) or open-ended search (use repo_search). "
        "Output: JSON array of paths, each an ordered array of {node_id, path, start_line, "
        "end_line, via_edge, confidence}."
    ),
    "repo_blast_radius": (
        "Reverse reachability from a node_id: callers of callers, subclasses of subclasses, "
        "importers of importers of its file, and historically co-edited files, by hop "
        "distance -- the blast radius of changing it. Read-only, deterministic, zero side "
        "effects, secret files excluded. When to use: use before editing a symbol to see what "
        "depends on it, or to answer 'what is the impact radius of changing X'. When NOT to "
        "use: do not use for PR/diff-level risk analysis (use repo_impact) or generic search "
        "(use repo_search). Output: JSON object with callers, subclasses, importers, "
        "cochange, and a summary."
    ),
    "repo_cache_stats": (
        "Retrieve runtime diagnostic counters for the tool result cache (hits, misses, "
        "size, max_size, ttl_s). Read-only, in-memory diagnostics, zero side effects. "
        "When to use: use when evaluating cache hit rate or debugging server performance. "
        "When NOT to use: do not use to search repository contents or inspect code structure; "
        "use repo_map or repo_search instead. Output: JSON object with cache metrics."
    ),
    "repo_build_status": (
        "Query progress and status of a background index build task under --async-build. "
        "Read-only check of in-memory background worker. When to use: use when polling "
        "build progress after an async index build was started. When NOT to use: do not use "
        "when building synchronously or when queries already succeed. Once completed, use "
        "repo_search or repo_map to query code. Output: JSON object with task_id, status, "
        "parsed file progress, and error details."
    ),
}

BUILD_CAPABLE_TOOLS = frozenset(TOOL_DESCRIPTIONS) - {"repo_build_status"}

TOOL_SCHEMAS = {
    "repo_map": {"type": "object", "properties": {}},
    "repo_search": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Natural language question, search terms, or symbol identifier to search for "
                    "(e.g. 'pack_context' or 'how does export work')."
                ),
            },
            "k": {
                "type": "integer",
                "description": (
                    f"Number of initial seed chunks retrieved via BM25 lexical scoring "
                    f"(default 8, max {MCP_MAX_K})."
                ),
            },
            "hops": {
                "type": "integer",
                "description": (
                    f"Graph traversal depth around seed chunks (default 1, max {MCP_MAX_HOPS}; "
                    f"0 returns seeds only)."
                ),
            },
            "budget_tokens": {
                "type": "integer",
                "description": (
                    f"Maximum token ceiling for returned markdown pack (default {MCP_BUDGET_TOKENS}, "
                    f"max {MCP_MAX_BUDGET_TOKENS}; zero or negative uses the default)."
                ),
            },
        },
        "required": ["query"],
    },
    "repo_neighbours": {
        "type": "object",
        "properties": {
            "node_id": {
                "type": "string",
                "description": (
                    "Target graph node identifier to expand from (e.g. 'sym:pkg/mod.py::func', "
                    "'file:pkg/mod.py', 'dir:pkg')."
                ),
            },
            "hops": {
                "type": "integer",
                "description": (f"Traversal depth from node_id (default 1, max {MCP_MAX_HOPS})."),
            },
            "limit": {
                "type": "integer",
                "description": (
                    f"Maximum neighbor rows to return (default {MCP_NEIGHBOUR_LIMIT}, "
                    f"min 1, max {MCP_MAX_NEIGHBOURS})."
                ),
            },
        },
        "required": ["node_id"],
    },
    "repo_impact": {
        "type": "object",
        "properties": {
            "base": {
                "type": "string",
                "description": "Base ref or branch to compare against (default 'main').",
            },
            "head": {
                "type": "string",
                "description": (
                    "Head ref or branch to compare. Omit to compare the working tree "
                    "(including uncommitted changes) against base."
                ),
            },
            "diff": {
                "type": "string",
                "description": "Optional raw unified diff text. If provided, overrides git diff.",
            },
            "max_depth": {
                "type": "integer",
                "description": f"Caller traversal hops around changed symbols (default 2, max {MCP_MAX_HOPS}).",
            },
            "format": {
                "type": "string",
                "enum": ["markdown", "json", "sarif", "pr-comment"],
                "description": (
                    "Report format: 'markdown' (full report), 'pr-comment' (compact PR "
                    "summary), 'json', or 'sarif' (SARIF v2.1.0). Anything else is an error."
                ),
            },
        },
    },
    "repo_find_symbol": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Symbol or file name to look up (e.g. 'validate_token').",
            },
            "kind": {
                "type": "string",
                "description": (
                    "Optional exact node kind filter (e.g. 'function', 'class', 'method')."
                ),
            },
            "path_prefix": {
                "type": "string",
                "description": "Optional path prefix filter, e.g. 'pkg/'.",
            },
            "limit": {
                "type": "integer",
                "description": (
                    f"Maximum candidates returned (default {MCP_FIND_LIMIT}, "
                    f"min 1, max {MCP_MAX_FIND_LIMIT})."
                ),
            },
        },
        "required": ["name"],
    },
    "repo_read": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Repo-relative path exactly as it appears in a [cite: path:start-end] "
                    "header. Absolute paths and '..' segments are refused."
                ),
            },
            "start_line": {
                "type": "integer",
                "description": "First line to read (default 1).",
            },
            "end_line": {
                "type": "integer",
                "description": "Last line to read (default: start_line).",
            },
            "context": {
                "type": "integer",
                "description": (
                    f"Extra lines of context on each side of the range "
                    f"(default {MCP_READ_CONTEXT}, max {MCP_MAX_READ_CONTEXT})."
                ),
            },
        },
        "required": ["path"],
    },
    "repo_path_between": {
        "type": "object",
        "properties": {
            "from_id": {
                "type": "string",
                "description": "Starting graph node id.",
            },
            "to_id": {
                "type": "string",
                "description": "Target graph node id.",
            },
            "max_hops": {
                "type": "integer",
                "description": (
                    f"Maximum path length in hops (default {MCP_PATH_HOPS}, "
                    f"max {MCP_MAX_PATH_HOPS})."
                ),
            },
            "edge_types": {
                "type": "array",
                "items": {"type": "string", "enum": sorted(PATH_EDGE_TYPES)},
                "description": (
                    f"Edge types to traverse (default {list(DEFAULT_PATH_EDGE_TYPES)}). "
                    f"CO_CHANGE is a statistical correlation, not a call/definition path, "
                    f"and is only followed when named here explicitly."
                ),
            },
            "max_paths": {
                "type": "integer",
                "description": (
                    f"Maximum distinct paths returned (default {MCP_PATH_PATHS}, "
                    f"max {MCP_MAX_PATHS})."
                ),
            },
        },
        "required": ["from_id", "to_id"],
    },
    "repo_blast_radius": {
        "type": "object",
        "properties": {
            "node_id": {
                "type": "string",
                "description": (
                    "Graph node id to analyze (e.g. from repo_find_symbol or a "
                    "repo_search citation)."
                ),
            },
            "max_hops": {
                "type": "integer",
                "description": (
                    f"Reverse-closure depth (default {MCP_IMPACT_HOPS}, max {MCP_MAX_IMPACT_HOPS})."
                ),
            },
            "include_cochange": {
                "type": "boolean",
                "description": "Include historically co-edited files (default true).",
            },
            "limit": {
                "type": "integer",
                "description": (
                    f"Maximum rows per section (default {MCP_IMPACT_LIMIT}, "
                    f"max {MCP_MAX_NEIGHBOURS})."
                ),
            },
        },
        "required": ["node_id"],
    },
    "repo_cache_stats": {"type": "object", "properties": {}},
    "repo_build_status": {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": (
                    "Task ID string returned by a previous tool call when an asynchronous build "
                    "was initiated."
                ),
            },
        },
        "required": ["task_id"],
    },
}


def tool_annotations(name: str, auto_build: bool) -> dict[str, object]:
    """Return annotations dictionary for the specified tool name."""
    base = (
        TOOL_ANNOTATIONS_AUTO_BUILD
        if (auto_build and name in BUILD_CAPABLE_TOOLS)
        else TOOL_ANNOTATIONS
    )
    ann: dict[str, object] = dict(base)
    if name in TOOL_TITLES:
        ann["title"] = TOOL_TITLES[name]
    return ann


_INDEXES: dict[str, Index] = {}
_INDEX_MTIMES: dict[str, float] = {}
_INDEX_LOCKS_GUARD = threading.Lock()
_INDEX_LOCKS: dict[str, threading.Lock] = {}


def _index_lock(key: str) -> threading.Lock:
    """The lock covering one index directory, created on first use."""
    with _INDEX_LOCKS_GUARD:
        lock = _INDEX_LOCKS.get(key)
        if lock is None:
            lock = _INDEX_LOCKS[key] = threading.Lock()
        return lock


def _index_mtime(out_path: Path) -> float:
    manifest = artifact_path(out_path, "manifest.json")
    target = manifest if manifest.is_file() else artifact_path(out_path, "chunks.jsonl")
    try:
        return target.stat().st_mtime if target.is_file() else 0.0
    except OSError:
        return 0.0


def _has_index(out_path: Path) -> bool:
    """True when `out_path` holds an index a tool can actually read."""
    return out_path.exists() and artifact_path(out_path, "chunks.jsonl").is_file()


def _build_index(repo: Path, out: Path) -> None:
    """Index `repo` into `out`, in-process, silent on stdout."""
    from ..chunks import iter_chunks
    from ..export import dump_all
    from ..graph import build
    from ..parse import BuildConfig

    graph = build(repo, config=BuildConfig(output_dir=str(out)))
    dump_all(graph, iter_chunks(graph), out, AUTO_BUILD_FORMATS)


def open_index(out: str | Path, repo: str | Path | None = None, cache: ResultCache | None = None) -> Index:
    """Open or build an index for `out`, reusing instances across calls."""
    out_path = Path(out)
    key = str(out_path.resolve())

    with _index_lock(key):
        current_mtime = _index_mtime(out_path)

        index = _INDEXES.get(key)
        if index is not None and key in _INDEX_MTIMES and current_mtime <= _INDEX_MTIMES[key]:
            return index

        if index is not None and _has_index(out_path):
            if cache is not None:
                cache.clear()
            index = _INDEXES[key] = Index(out_path)
            _INDEX_MTIMES[key] = current_mtime
            if repo is not None:
                index.repo_root = Path(repo).resolve()
            return index

        if index is not None:
            return index

        if not _has_index(out_path):
            if repo is None:
                raise SystemExit(
                    f"error: no repo2graph index found at '{out}'. "
                    f"Build one first with: repo2graph build <path> -o {out}"
                )
            build_fn = getattr(sys.modules.get("repo2graph.mcp"), "_build_index", _build_index)
            build_fn(Path(repo), out_path)
            if cache is not None:
                cache.clear()
            current_mtime = _index_mtime(out_path)
        index = _INDEXES[key] = Index(out_path)
        _INDEX_MTIMES[key] = current_mtime
        if repo is not None:
            index.repo_root = Path(repo).resolve()
        return index


def open_index_or_task(out: str | Path, repo: str | Path | None = None, cache: ResultCache | None = None, tasks=None):
    """Open index or launch a background build task if asynchronous builds are enabled."""
    from ..tasks import BUILDING, BUILDING_MESSAGE, FAILED, FAILED_MESSAGE

    out_path = Path(out)
    if tasks is None or _has_index(out_path) or repo is None:
        return open_index(out, repo, cache), None

    task = tasks.for_dir(out_path)
    if task is None:
        task = tasks.start(repo, out_path)

    if task.status == BUILDING:
        return None, BUILDING_MESSAGE.format(**task.snapshot())
    if task.status == FAILED:
        return None, FAILED_MESSAGE.format(error=task.error)
    if cache is not None:
        cache.clear()
    return open_index(out, repo, cache), None


def tool_repo_map(index: Index) -> str:
    """The repo map, plus a staleness note when the working tree has moved."""
    return _staleness_note(index) + index.map_prepend()


def tool_repo_search(
    index: Index, query: str, k: Any = 8, hops: Any = 1, budget_tokens: Any = None
) -> str:
    """Cited markdown for `query`, bounded by budget_tokens."""
    query = _str(query, MCP_MAX_QUERY_CHARS)
    if not query.strip():
        return ToolError(
            "repo_search needs a non-empty `query`: a question, search terms or a "
            "symbol name (e.g. 'pack_context' or 'how does export work')."
        )
    note = _defaulted_notes(
        k=(k, 8), hops=(hops, 1), budget_tokens=(budget_tokens, MCP_BUDGET_TOKENS)
    )
    budget = MCP_BUDGET_TOKENS if budget_tokens is None else _int(budget_tokens, MCP_BUDGET_TOKENS)
    if budget <= 0:
        note += BUDGET_DEFAULTED.format(
            given=budget, default=MCP_BUDGET_TOKENS, ceiling=MCP_MAX_BUDGET_TOKENS
        )
        budget = MCP_BUDGET_TOKENS
    budget = max(1, min(budget, MCP_MAX_BUDGET_TOKENS))
    room = max(1, budget - (len(note) + 3) // 4)
    pack = index.pack_context(
        query,
        k=_clamp(k, 8, 1, MCP_MAX_K),
        hops=_clamp(hops, 1, 0, MCP_MAX_HOPS),
        budget_tokens=room,
        exclude_secrets=True,
    )
    text = pack["markdown"]
    if count_tokens(text) > room:
        text = _fit_lines(text, room, count_tokens)
    if not text.strip():
        return note + EMPTY_RESULT.format(budget=budget, ceiling=MCP_MAX_BUDGET_TOKENS)
    return note + text


def tool_repo_neighbours(
    index: Index, node_id: str, hops: Any = 1, limit: Any = MCP_NEIGHBOUR_LIMIT
) -> str:
    """Graph traversal from `node_id`."""
    node_id = _str(node_id, MCP_MAX_NODE_ID_CHARS)
    if not node_id.strip():
        return ToolError(
            "repo_neighbours needs a `node_id`. Ids look like file:<path>, "
            "sym:<path>::<qualname> or dir:<path>; repo_search results cite them."
        )
    node = index.nodes.get(node_id)
    if node is None:
        return ToolError(
            f"node not found: {node_id!r}. Ids look like "
            f"file:<path>, sym:<path>::<qualname> or dir:<path>."
        )
    note = _defaulted_notes(hops=(hops, 1), limit=(limit, MCP_NEIGHBOUR_LIMIT))
    limit = _clamp(limit, MCP_NEIGHBOUR_LIMIT, 1, MCP_MAX_NEIGHBOURS)
    lines = [f"neighbours of {_label(index, node_id)}:"]
    truncated = False
    for dst, etype, direction, src in index.expand(
        [node_id],
        hops=_clamp(hops, 1, 0, MCP_MAX_HOPS),
        edge_types=frozenset(DEFAULT_EDGE_TYPES | {"CONTAINS", "CO_CHANGE"}),
        edge_dirs=ALL_EDGE_DIRS,
        min_confidence=0.0,
    ):
        target = index.nodes.get(dst, {})
        if index._is_secret_path(target.get("path") or ""):
            continue
        if len(lines) - 1 >= limit:
            truncated = True
            break
        lines.append(
            f"- {etype} {direction}: {_label(index, dst)}{_edge_note(index, src, dst, etype)}"
        )
    if truncated:
        lines.append(f"... (truncated at {limit} neighbours)")
    if len(lines) == 1:
        lines.append("- (none)")
    return note + "\n".join(lines)


_AUX_CACHE: weakref.WeakKeyDictionary[Index, dict[str, Any]] = weakref.WeakKeyDictionary()


def _chunk_body_lines(c: dict[str, Any]) -> list[str] | None:
    """Extract physical source lines covering [start_line, end_line]."""
    if c.get("type") == "file_residual":
        return None
    start, end = c.get("start_line"), c.get("end_line")
    if not isinstance(start, int) or not isinstance(end, int) or end < start:
        return None
    lines = (c.get("text") or "").split("\n")
    body_len = end - start + 1
    header_len = len(lines) - body_len
    if header_len < 0:
        return None
    return lines[header_len:]


def _aux(index: Index) -> dict[str, Any]:
    """Build and cache symbol name lookups and path chunks for `index`."""
    cached = _AUX_CACHE.get(index)
    if cached is not None:
        return cached
    exact: dict[str, list[str]] = defaultdict(list)
    ci: dict[str, list[str]] = defaultdict(list)
    last_seg: dict[str, list[str]] = defaultdict(list)
    for node_id, node in index.nodes.items():
        name = node.get("name") or ""
        qual = node.get("qualname") or ""
        if name:
            exact[name].append(node_id)
            ci[name.lower()].append(node_id)
        if qual:
            seg = QUALNAME_SEP_RE.split(qual)[-1]
            if seg and seg != name:
                last_seg[seg].append(node_id)
    by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in index.chunks:
        p = c.get("path")
        if not p:
            continue
        body_lines = _chunk_body_lines(c)
        if body_lines is None:
            continue
        by_path[p].append(
            {"start_line": c["start_line"], "end_line": c["end_line"], "lines": body_lines}
        )
    for entries in by_path.values():
        entries.sort(key=lambda e: (e["start_line"], e["end_line"]))
    built = {
        "exact": dict(exact),
        "ci": dict(ci),
        "last_seg": dict(last_seg),
        "by_path": dict(by_path),
    }
    _AUX_CACHE[index] = built
    return built


def tool_repo_find_symbol(
    index: Index,
    name: str,
    kind: str = "",
    path_prefix: str = "",
    limit: Any = MCP_FIND_LIMIT,
) -> str:
    """Look up a symbol's node_id candidates by name."""
    name = _str(name, MCP_MAX_QUERY_CHARS)
    if not name.strip():
        return ToolError(
            "repo_find_symbol needs a non-empty `name`: a function, class or "
            "method name to look up (e.g. 'validate_token')."
        )
    kind_filter = _str(kind, 100).strip()
    prefix_filter = _str(path_prefix, MCP_MAX_NODE_ID_CHARS).strip().replace("\\", "/")
    note = _defaulted_notes(limit=(limit, MCP_FIND_LIMIT))
    lim = _clamp(limit, MCP_FIND_LIMIT, 1, MCP_MAX_FIND_LIMIT)

    lookup = _aux(index)
    candidates = (
        lookup["exact"].get(name)
        or lookup["ci"].get(name.lower())
        or lookup["last_seg"].get(name)
        or []
    )

    results = []
    for node_id in candidates:
        node = index.nodes.get(node_id) or {}
        path = node.get("path") or ""
        if index._is_secret_path(path):
            continue
        if kind_filter and (node.get("kind") or "") != kind_filter:
            continue
        if prefix_filter and not path.replace("\\", "/").startswith(prefix_filter):
            continue
        results.append(
            {
                "node_id": node_id,
                "name": node.get("name") or "",
                "qualname": node.get("qualname") or "",
                "kind": node.get("kind") or "",
                "path": path,
                "start_line": node.get("start_line"),
                "end_line": node.get("end_line"),
                "lang": node.get("lang") or "",
            }
        )
        if len(results) >= lim:
            break

    return note + json.dumps(results, indent=2)


def tool_repo_read(
    index: Index,
    path: str,
    start_line: Any = 1,
    end_line: Any = None,
    context: Any = 0,
) -> str:
    """Read indexed source window around a citation from chunks."""
    raw_path = _str(path, MCP_MAX_NODE_ID_CHARS)
    if not raw_path.strip():
        return ToolError("repo_read needs a non-empty `path`.")
    norm = raw_path.replace("\\", "/").strip()
    if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm) or norm.startswith("//"):
        return ToolError(
            f"repo_read refuses an absolute path: {raw_path!r}. Use a path "
            f"relative to the repo root, exactly as it appears in a "
            f"[cite: path:start-end] header."
        )
    if ".." in norm.split("/"):
        return ToolError(f"repo_read refuses a path with a '..' segment: {raw_path!r}.")
    if index._is_secret_path(norm):
        return ToolError(f"not indexed: {raw_path!r} is excluded (secret-looking path).")

    note = _defaulted_notes(start_line=(start_line, 1), context=(context, MCP_READ_CONTEXT))
    if end_line is not None:
        note += _defaulted_notes(end_line=(end_line, 0))
    start = max(1, _int(start_line, 1))
    ctx = _clamp(context, MCP_READ_CONTEXT, 0, MCP_MAX_READ_CONTEXT)
    end = start if end_line is None else max(start, _int(end_line, start))
    want_start = max(1, start - ctx)
    want_end = end + ctx

    chunks = _aux(index)["by_path"].get(norm) or []
    covering: list[dict[str, Any]] = []
    cursor = want_start
    for c in chunks:
        c_start, c_end = c.get("start_line"), c.get("end_line")
        if not isinstance(c_start, int) or not isinstance(c_end, int):
            continue
        if c_end < cursor:
            continue
        if c_start > cursor:
            break
        covering.append(c)
        cursor = c_end + 1
        if cursor > want_end:
            break

    if not covering or cursor <= want_end:
        return ToolError(
            f"not indexed: {norm}:{want_start}-{want_end} is not fully covered by any "
            f"chunk (the file may not be indexed, the path may be excluded, or the "
            f"span may fall in an under-40-character residual that emits no chunk)."
        )

    pieces = []
    for c in covering:
        c_start, c_end = c["start_line"], c["end_line"]
        seg_lo, seg_hi = max(want_start, c_start), min(want_end, c_end)
        if seg_lo > seg_hi:
            continue
        pieces.append("\n".join(c["lines"][seg_lo - c_start : seg_hi - c_start + 1]))
    hi = min(cursor - 1, want_end)
    text = "\n".join(pieces)

    if len(text) > MCP_MAX_READ_CHARS:
        text = _fit_lines(text, MCP_MAX_READ_CHARS, len)
        note += (
            f"_note: result exceeded the {MCP_MAX_READ_CHARS}-character tool "
            f"ceiling; truncated at a line boundary. Narrow the range._\n\n"
        )

    return note + f"### [cite: {norm}:{want_start}-{hi}]\n\n{text}"


def _path_secret(index: Index, node_id: str) -> bool:
    node = index.nodes.get(node_id) or {}
    return index._is_secret_path(node.get("path") or "")


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


def _containing_file(index: Index, node_id: str) -> str | None:
    if node_id.startswith("file:"):
        return node_id if node_id in index.nodes else None
    node = index.nodes.get(node_id) or {}
    path = node.get("path") or ""
    if not path:
        return None
    fid = f"file:{path}"
    return fid if fid in index.nodes else None


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


def tool_cache_stats(cache: ResultCache | None) -> str:
    """Return cache metrics as JSON."""
    if cache is None:
        return json.dumps(
            {"enabled": False, "hits": 0, "misses": 0, "size": 0, "max_size": 0, "ttl_s": 0},
            indent=2,
        )
    return json.dumps(cache.stats(), indent=2)


def tool_build_status(tasks, task_id: str) -> str:
    """Return status of a background build task as JSON."""
    task_id = _str(task_id, MCP_MAX_TASK_ID_CHARS)
    if tasks is None:
        return ToolError(
            json.dumps(
                {
                    "error": "this server builds synchronously; there are no build "
                    "tasks to report. Start it with --async-build to use "
                    "repo_build_status.",
                },
                indent=2,
            )
        )
    if not task_id.strip():
        return ToolError(
            json.dumps(
                {
                    "task_id": "",
                    "status": "unknown",
                    "error": "repo_build_status needs a `task_id`: the id a tool call "
                    "returned when it started a background build.",
                },
                indent=2,
            )
        )
    task = tasks.get(task_id)
    if task is None:
        return ToolError(
            json.dumps(
                {
                    "task_id": task_id,
                    "status": "unknown",
                    "error": f"no build task with id {task_id!r}. Ids are issued by the "
                    f"tool call that starts a build and do not survive a "
                    f"server restart.",
                },
                indent=2,
            )
        )
    return json.dumps(task.snapshot(), indent=2)


def dispatch(
    index: Index | None,
    name: str,
    arguments: dict[str, Any] | None,
    cache: ResultCache | None = None,
    tasks=None,
) -> str:
    """Route tool call by name to its corresponding handler."""
    args = arguments or {}
    if name == "repo_build_status":
        return tool_build_status(tasks, str(args.get("task_id") or ""))
    if name == "repo_cache_stats":
        return tool_cache_stats(cache)
    if name not in TOOL_DESCRIPTIONS:
        return ToolError(f"unknown tool: {name!r}. Available: {', '.join(TOOL_DESCRIPTIONS)}.")

    key = None
    if cache is not None and name in CACHEABLE_TOOLS:
        key = make_key(name, args)
        hit = cache.get(key)
        if hit is not None:
            return hit

    if index is None:
        return ToolError(
            "no index is open, so this tool cannot answer. Use "
            "repo_build_status to check whether one is still being built."
        )

    import sys
    _mod = sys.modules.get("repo2graph.mcp")
    _map = getattr(_mod, "tool_repo_map", tool_repo_map) if _mod else tool_repo_map
    _search = getattr(_mod, "tool_repo_search", tool_repo_search) if _mod else tool_repo_search
    _neighbours = getattr(_mod, "tool_repo_neighbours", tool_repo_neighbours) if _mod else tool_repo_neighbours
    _impact = getattr(_mod, "tool_repo_impact", tool_repo_impact) if _mod else tool_repo_impact
    _find = getattr(_mod, "tool_repo_find_symbol", tool_repo_find_symbol) if _mod else tool_repo_find_symbol

    if name == "repo_map":
        result = _map(index)
    elif name == "repo_search":
        result = _search(
            index,
            str(args.get("query") or ""),
            k=args.get("k", 8),
            hops=args.get("hops", 1),
            budget_tokens=args.get("budget_tokens"),
        )
    elif name == "repo_neighbours":
        result = _neighbours(
            index,
            str(args.get("node_id") or ""),
            hops=args.get("hops", 1),
            limit=args.get("limit", MCP_NEIGHBOUR_LIMIT),
        )
    elif name == "repo_impact":
        result = _impact(
            index,
            base=str(args.get("base") or "main"),
            head=str(args.get("head") or "") or None,
            diff=str(args.get("diff") or ""),
            max_depth=args.get("max_depth", 2),
            format=str(args.get("format") or "markdown"),
        )
    elif name == "repo_find_symbol":
        result = _find(
            index,
            str(args.get("name") or ""),
            kind=str(args.get("kind") or ""),
            path_prefix=str(args.get("path_prefix") or ""),
            limit=args.get("limit", MCP_FIND_LIMIT),
        )
    elif name == "repo_read":
        result = tool_repo_read(
            index,
            str(args.get("path") or ""),
            start_line=args.get("start_line", 1),
            end_line=args.get("end_line"),
            context=args.get("context", MCP_READ_CONTEXT),
        )
    elif name == "repo_path_between":
        result = tool_repo_path_between(
            index,
            str(args.get("from_id") or ""),
            str(args.get("to_id") or ""),
            max_hops=args.get("max_hops", MCP_PATH_HOPS),
            edge_types=args.get("edge_types"),
            max_paths=args.get("max_paths", MCP_PATH_PATHS),
        )
    elif name == "repo_blast_radius":
        result = tool_repo_blast_radius(
            index,
            str(args.get("node_id") or ""),
            max_hops=args.get("max_hops", MCP_IMPACT_HOPS),
            include_cochange=args.get("include_cochange", True),
            limit=args.get("limit", MCP_IMPACT_LIMIT),
        )
    else:
        return ToolError(f"unknown tool: {name!r}. Available: {', '.join(TOOL_DESCRIPTIONS)}.")

    if key is not None and not isinstance(result, ToolError):
        cache.put(key, result)
    return result
