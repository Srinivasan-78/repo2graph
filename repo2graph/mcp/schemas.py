"""Static MCP tool metadata: annotations, titles, descriptions, and JSON schemas.

Declaration only -- no handler logic and no index access, so the tool surface
can be read (and diffed) without walking the implementations.
"""

from __future__ import annotations

from .guardrails import (
    DEFAULT_PATH_EDGE_TYPES,
    MCP_BUDGET_TOKENS,
    MCP_FIND_LIMIT,
    MCP_IMPACT_HOPS,
    MCP_IMPACT_LIMIT,
    MCP_MAX_BUDGET_TOKENS,
    MCP_MAX_FIND_LIMIT,
    MCP_MAX_HOPS,
    MCP_MAX_IMPACT_HOPS,
    MCP_MAX_K,
    MCP_MAX_NEIGHBOURS,
    MCP_MAX_PATH_HOPS,
    MCP_MAX_PATHS,
    MCP_MAX_READ_CONTEXT,
    MCP_MAX_SEARCH_NEIGHBOURS,
    MCP_NEIGHBOUR_LIMIT,
    MCP_PATH_HOPS,
    MCP_PATH_PATHS,
    MCP_READ_CONTEXT,
    PATH_EDGE_TYPES,
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
            "neighbours": {
                "type": "string",
                "enum": ["full", "cite"],
                "description": (
                    "Neighbour rendering mode: 'full' (default) emits complete chunk text; "
                    "'cite' emits one-line signature citations without chunk body."
                ),
            },
            "max_neighbours": {
                "type": "integer",
                "description": (
                    f"Maximum graph neighbour chunks to admit into context (default unbounded, "
                    f"max {MCP_MAX_SEARCH_NEIGHBOURS})."
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
