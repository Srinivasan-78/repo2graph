"""MCP tool dispatch, plus the cache and build-status tools.

The tool surface is split by concern: `schemas` declares it, `indexes` opens
the index, `retrieval` and `traversal` implement the handlers, and `dispatch`
below routes a call to one of them. Every name those modules define is
re-exported here, so `repo2graph.mcp.tools` remains the single import point.
"""

from __future__ import annotations

import json
from typing import Any

from ..cache import CACHEABLE_TOOLS, ResultCache, make_key
from ..query import Index
from .guardrails import (
    BUDGET_DEFAULTED,
    EMPTY_RESULT,
    MCP_FIND_LIMIT,
    MCP_IMPACT_HOPS,
    MCP_IMPACT_LIMIT,
    MCP_MAX_TASK_ID_CHARS,
    MCP_NEIGHBOUR_LIMIT,
    MCP_PATH_HOPS,
    MCP_PATH_PATHS,
    MCP_READ_CONTEXT,
    _str,
)
from .indexes import (
    _INDEX_LOCKS,
    _INDEX_LOCKS_GUARD,
    _INDEX_MTIMES,
    _INDEXES,
    _build_index,
    _has_index,
    _index_lock,
    _index_mtime,
    open_index,
    open_index_or_task,
)
from .retrieval import (
    _AUX_CACHE,
    _aux,
    _chunk_body_lines,
    tool_repo_find_symbol,
    tool_repo_map,
    tool_repo_neighbours,
    tool_repo_read,
    tool_repo_search,
)
from .schemas import (
    BUILD_CAPABLE_TOOLS,
    TOOL_ANNOTATIONS,
    TOOL_ANNOTATIONS_AUTO_BUILD,
    TOOL_DESCRIPTIONS,
    TOOL_SCHEMAS,
    TOOL_TITLES,
    ToolError,
    tool_annotations,
)
from .nodes import (
    _containing_file,
    _edge_note,
    _impact_root,
    _label,
    _path_secret,
    _staleness_note,
)
from .traversal import (
    _bidirectional_bfs,
    _clean_edge_types,
    _reconstruct,
    _reverse_closure,
    tool_repo_blast_radius,
    tool_repo_impact,
    tool_repo_path_between,
)

__all__ = [
    "BUDGET_DEFAULTED",
    "BUILD_CAPABLE_TOOLS",
    "EMPTY_RESULT",
    "TOOL_ANNOTATIONS",
    "TOOL_ANNOTATIONS_AUTO_BUILD",
    "TOOL_DESCRIPTIONS",
    "TOOL_SCHEMAS",
    "TOOL_TITLES",
    "ToolError",
    "_AUX_CACHE",
    "_INDEXES",
    "_INDEX_LOCKS",
    "_INDEX_LOCKS_GUARD",
    "_INDEX_MTIMES",
    "_aux",
    "_bidirectional_bfs",
    "_build_index",
    "_chunk_body_lines",
    "_clean_edge_types",
    "_containing_file",
    "_edge_note",
    "_has_index",
    "_impact_root",
    "_index_lock",
    "_index_mtime",
    "_label",
    "_path_secret",
    "_reconstruct",
    "_reverse_closure",
    "_staleness_note",
    "dispatch",
    "open_index",
    "open_index_or_task",
    "tool_annotations",
    "tool_build_status",
    "tool_cache_stats",
    "tool_repo_blast_radius",
    "tool_repo_find_symbol",
    "tool_repo_impact",
    "tool_repo_map",
    "tool_repo_neighbours",
    "tool_repo_path_between",
    "tool_repo_read",
    "tool_repo_search",
]


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
    _neighbours = (
        getattr(_mod, "tool_repo_neighbours", tool_repo_neighbours)
        if _mod
        else tool_repo_neighbours
    )
    _impact = getattr(_mod, "tool_repo_impact", tool_repo_impact) if _mod else tool_repo_impact
    _find = (
        getattr(_mod, "tool_repo_find_symbol", tool_repo_find_symbol)
        if _mod
        else tool_repo_find_symbol
    )

    if name == "repo_map":
        result = _map(index)
    elif name == "repo_search":
        result = _search(
            index,
            str(args.get("query") or ""),
            k=args.get("k", 8),
            hops=args.get("hops", 1),
            budget_tokens=args.get("budget_tokens"),
            # `neighbours` and `max_neighbours` are advertised in
            # TOOL_SCHEMAS["repo_search"] and accepted by tool_repo_search, but
            # were never forwarded here: a client setting either got the default
            # and no error. That made `neighbours="cite"` unreachable over MCP --
            # the mode the README's own benchmark table reports as the best
            # retriever at an 8k budget (5,460 vs 5,561 mean tokens). Defaults
            # match the function's own, so omitting them stays a no-op.
            neighbours=args.get("neighbours", "full"),
            max_neighbours=args.get("max_neighbours"),
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

    if key is not None and cache is not None and not isinstance(result, ToolError):
        cache.put(key, result)
    return result
