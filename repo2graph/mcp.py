"""A stdio MCP server over an existing .r2g index: three tools, one engine.

This is an *additional* surface, not a replacement: every tool is a thin call
into `repo2graph.query.Index`, the same object the CLI and the GitHub Action
use, over the same artifacts. The `mcp` SDK is an optional extra and is
imported only inside serve(), so importing this module costs nothing and the
handlers below are testable without the SDK installed.

Two rules apply to every handler and are not negotiable per call:

* `exclude_secrets=True`, always. The CLI sets it only for `--answer`, but a
  tool an agent can call unattended returning `.env` contents is a different
  class of problem from a human deliberately grepping their own checkout.
* Output is hard-bounded. An agent-facing tool that *can* return 50k tokens
  eventually will, so the caller's budget is clamped and the rendered result
  is re-measured and trimmed rather than trusted.
* Work is hard-bounded too. `k` and `hops` are clamped before they reach the
  engine: serve() awaits every call on one asyncio loop, so a single argument
  that costs minutes wedges the whole server for every client, not just the
  caller that sent it.

The index itself is built on demand. Pointed at a repo with no `.r2g` yet, the
first tool call parses it and writes one, because "add the server, restart, ask
a question, read an error" is an onboarding step users do not complete. That
build is the one unbounded piece of work here and it deliberately blocks the
loop: nothing this server does means anything without an index, so there is no
other call worth serving first. It happens once per process, and once on disk.
"""

import argparse
import os
import re
import sys
import threading
import weakref
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import __version__
from .cache import DEFAULT_MAX_SIZE, DEFAULT_TTL, ResultCache
from .export import path as artifact_path
from .edgemeta import cite as edge_cite
from .query import (
    ALL_EDGE_DIRS,
    DEFAULT_EDGE_TYPES,
    QUALNAME_SEP_RE,
    Index,
    _fit_lines,
    count_tokens,
)


# The formats a served index actually needs: `jsonl` carries the chunks, nodes
# and edges every tool reads, `overview` is what repo_map hands back. The other
# three (`html`, `graphml`, `cypher`) are for humans and other tools, and cost
# real time on a large repo, so an index built to be served skips them. Same
# choice `cli._rag_index_dir` makes when `rag` has to index a target on the spot.
AUTO_BUILD_FORMATS = {"jsonl", "overview"}

# The directory name that means "this index belongs to the repo above it".
INDEX_DIRNAME = ".r2g"

# The budget a call gets when it asks for nothing, and the ceiling no call can
# raise: roughly a quarter of a small model's context, and half of it.
MCP_BUDGET_TOKENS = 6000
MCP_MAX_BUDGET_TOKENS = 12000

# Neighbours listed by repo_neighbours when the caller names no limit, and the
# ceiling no call can raise. `hops` bounds the *time* that tool costs but not
# the *size* of its answer -- the 4-hop reachable set of a mid-size graph is
# most of the graph -- and it is the one tool whose output no token budget
# measures, so the row count is the only thing standing between an agent and a
# 35k-character reply. 50 rows is the same order as MCP_MAX_K.
MCP_NEIGHBOUR_LIMIT = 20
MCP_MAX_NEIGHBOURS = 50

# Ceilings on the two arguments that cost *time* rather than output size.
# `Index.expand` runs `for _ in range(hops)` with no empty-frontier exit, so an
# unclamped `hops=10**9` pins the asyncio loop serve() runs on for ~a minute and
# wedges the whole server -- every tool, every client -- on one bad JSON value a
# model wrote. Four hops already crosses the width of any real call graph, and
# 50 seeds is well past what any budget can render.
MCP_MAX_HOPS = 4
MCP_MAX_K = 50

# Ceilings on the string arguments, for symmetry with the numeric ones above.
# Nothing downstream crashes on an overlong query/id -- pack_context and a
# dict lookup both handle it -- but a model can hand this dispatcher an
# arbitrarily long string, and tokenising or scoring against megabytes of it
# is wasted CPU for no real query or node id this long. Every MCP argument is
# caller-hostile by default; this is the string-typed half of that rule.
MCP_MAX_QUERY_CHARS = 4000
MCP_MAX_NODE_ID_CHARS = 2000
MCP_MAX_TASK_ID_CHARS = 200

# repo_find_symbol: candidates returned when the caller names no `limit`, and
# the ceiling no call can raise -- same shape as
# MCP_NEIGHBOUR_LIMIT/MCP_MAX_NEIGHBOURS.
MCP_FIND_LIMIT = 20
MCP_MAX_FIND_LIMIT = 50

# repo_read: character ceiling on the returned window, enforced at a line
# boundary via _fit_lines(text, MCP_MAX_READ_CHARS, len) -- the same
# after-the-fact re-measure repo_search and repo_impact use, because a
# generous `context` can ask for more than the ceiling allows.
MCP_MAX_READ_CHARS = 20000
# `context` extra lines on each side of [start_line, end_line], and the
# ceiling no call can raise -- unclamped, a caller could ask for the whole
# file a hundred lines of context at a time.
MCP_READ_CONTEXT = 0
MCP_MAX_READ_CONTEXT = 500

# repo_path_between: hop and path-count ceilings, deliberately a *separate*
# constant from MCP_MAX_HOPS (4) rather than a reuse or a silent raise --
# MCP_MAX_HOPS bounds the cost of repo_search/repo_neighbours/repo_impact,
# and a bidirectional path search has a different cost shape (roughly
# max_hops/2 layers deep on each side, not max_hops deep on one). See #386.
MCP_PATH_HOPS = 6
MCP_MAX_PATH_HOPS = 8
MCP_PATH_PATHS = 3
MCP_MAX_PATHS = 10
# Nodes visited across *both* frontiers before the bidirectional BFS gives up
# and reports truncation -- the AGENTS.md rule that a hop bound alone does
# not bound work on a dense graph.
MCP_MAX_PATH_VISITED = 4000
# CALLS/DEFINES/IMPORTS by default; CO_CHANGE is a statistical correlation
# and must be named explicitly to be treated as a "path" (see #386).
DEFAULT_PATH_EDGE_TYPES = ("CALLS", "DEFINES", "IMPORTS")
PATH_EDGE_TYPES = frozenset({"CALLS", "DEFINES", "IMPORTS", "INHERITS", "CONTAINS", "CO_CHANGE"})

# repo_blast_radius: reverse-closure hop and visited-node ceilings. `limit`
# reuses MCP_MAX_NEIGHBOURS (not a new constant) per #387's own bound.
MCP_IMPACT_HOPS = 3
MCP_MAX_IMPACT_HOPS = 6
MCP_IMPACT_LIMIT = 40
MCP_MAX_IMPACT_VISITED = 4000

TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

# #292: the annotations above are honest only for a server that *cannot*
# auto-build -- an index that already exists (any mode), or a server started
# with auto-build off (`--no-auto-build`, or HTTP's own default of off unless
# `--allow-auto-build` is passed, #265). When a server *can* build, its first
# call to any tool but `repo_build_status` may run `graph.build()`: parse
# every file, execute a git subprocess, and write `.r2g/**` to disk. That is
# not read-only, and MCP has no per-call annotation -- `tools/list` answers
# once for the server's whole lifetime -- so the honest move is a second,
# per-server-instance annotation set rather than one constant applied
# everywhere regardless of whether *this* server can build.
#
# `get_tools(auto_build=True)` applies this to every tool in
# `BUILD_CAPABLE_TOOLS`; `repo_build_status` keeps `TOOL_ANNOTATIONS`
# unconditionally -- it only ever reads `TaskManager` state, on every path
# (`run_tool` special-cases it before `open_index_or_task`).
TOOL_ANNOTATIONS_AUTO_BUILD = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}


def tool_annotations(name: str, auto_build: bool) -> dict[str, object]:
    """The annotation block for one tool on a server with this build capability.

    The single place that decides between `TOOL_ANNOTATIONS` and
    `TOOL_ANNOTATIONS_AUTO_BUILD`, including the title. Both transports call it
    -- `get_tools()` for stdio and `http_server`'s `tools/list` handler -- for
    the reason #349 exists: the HTTP handler used to assemble this block
    itself, from the flat `TOOL_ANNOTATIONS` constant, so an HTTP server
    explicitly started with auto-build on still advertised `readOnlyHint:
    true`. Two copies of one decision drift, and this one drifts into a
    dishonest protocol answer.
    """
    base = (
        TOOL_ANNOTATIONS_AUTO_BUILD
        if (auto_build and name in BUILD_CAPABLE_TOOLS)
        else TOOL_ANNOTATIONS
    )
    ann: dict[str, object] = dict(base)
    if name in TOOL_TITLES:
        ann["title"] = TOOL_TITLES[name]
    return ann


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

# Tool descriptions structured to satisfy Glama TDQS (Tool Definition Quality
# Standard): explicit purpose with active verbs, sibling differentiation,
# concrete when-to-use / when-not-to-use guidance, read-only behavioral disclosure,
# and output shape descriptions while staying strictly bounded in context cost.
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

# Every tool whose first call, on a server that can auto-build, may build the
# index before answering -- i.e. every tool `run_tool` routes through
# `open_index_or_task` rather than answering directly. `repo_build_status` is
# the one exception: `run_tool` special-cases it *before*
# `open_index_or_task`, on every path, because asking for build progress must
# not itself wait on a build. `repo_cache_stats` is *not* exempt despite never
# touching the index itself once open -- `run_tool` still opens (and so may
# build) the index for it exactly like every other tool.
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

# What repo_search says instead of handing back an empty string.
EMPTY_RESULT = (
    "no content fit in a {budget}-token budget: nothing matched, "
    "or the budget was too small to render a single line. Retry "
    "with a broader query or budget_tokens up to {ceiling}."
)

# What repo_search prepends when a non-positive budget_tokens fell back to the default.
BUDGET_DEFAULTED = (
    "_note: budget_tokens={given} is not a positive token count; used the default "
    "{default} (max {ceiling})._\n\n"
)


#: What `repo_impact`'s `format` accepts (`comment` is an alias of `pr-comment`).
IMPACT_FORMATS = ("markdown", "json", "sarif", "pr-comment")


class ToolError(str):
    """A tool result that reports a failure rather than an answer.

    Still a plain string to every caller that only reads text (dispatch() has
    always returned one), but the stdio and HTTP transports check for it and
    set the MCP result's `isError: true`, so a client can tell "bad input /
    unknown node / git failed" from a real, possibly empty, answer.
    """

    __slots__ = ()


_INDEXES: dict[str, Index] = {}
_INDEX_MTIMES: dict[str, float] = {}

# One lock per index directory, and one lock over the dict that holds them.
#
# stdio serves one request at a time, but the HTTP transport is a
# `ThreadingHTTPServer` with `daemon_threads = True`, so every tool call arrives
# on its own thread and they all funnel into `open_index`. Its check-then-act --
# `if not _has_index(...)` then `_build_index(...)` -- is therefore racy in
# exactly the way `TaskManager` already documents for the `--async-build` path:
# two first calls both find no index, both parse the whole repository, and then
# race each other through `atomic_write` so the loser's work is silently
# discarded. `--async-build` is off by default and `open_index_or_task` returns
# straight to `open_index` when it is, so the default path had none of that
# protection.
#
# Per directory rather than one global lock, because a build is the one
# unbounded piece of work this server does: serialising it against a *different*
# index's cached read would turn a minute of parsing into a minute of stall for
# every client. The small global lock exists only to hand out the per-directory
# ones; it is never held across a build.
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
    """Index `repo` into `out`, in-process, silent on stdout.

    One constraint, and it is about the transport rather than about graphs:
    nothing may reach stdout. It carries the JSON-RPC stream, and one stray
    `print` ends the session. `graph.build` and `export.dump_all` write no
    console output of their own -- the CLI's `cmd_build` is what emits the JSON
    report -- so calling them directly is what keeps the wire clean.

    That constraint used to be met with `jobs=1`, because `build()` reaches for
    a process pool above PARALLEL_MIN_FILES files and the workers inherit the
    parent's fd 0 and fd 1, which here are the client's pipes. The pin is gone:
    `graph.silence_worker_io` now runs in every worker and dup2s both onto
    devnull, so the pool no longer has a route to the transport (#90). Default
    `jobs` (one worker per core) therefore applies, and a large repo's first
    tool call costs what `repo2graph build` costs rather than several times it.
    """
    from .chunks import iter_chunks
    from .export import dump_all
    from .graph import build
    from .parse import BuildConfig

    # output_dir: the default auto-build target (<repo>/.repo2graph) lives
    # inside the repo, so it must be kept out of discovery explicitly.
    graph = build(repo, config=BuildConfig(output_dir=str(out)))
    dump_all(graph, iter_chunks(graph), out, AUTO_BUILD_FORMATS)


def open_index(out, repo=None, cache=None) -> Index:
    """One Index per output directory, reused for the life of the process.

    Building an Index reads and inverts every chunk; doing that per tool call
    would make the second call as expensive as the first.

    `repo` is the opt-in half. Without it this is what it has always been: open
    an index that exists, or exit naming the command that creates one. With it,
    an absent index is built from that directory first -- so an agent that was
    pointed at a repo gets an answer instead of an error it cannot act on. It
    stays opt-in because inferring a repo to index from an output path alone is
    a guess, and the cost of guessing wrong is parsing the wrong tree.

    The whole body runs under this directory's lock -- the mtime read, the
    `_has_index` test, the build, the `Index(...)` load and both cache stores --
    because every one of those is half of a check-then-act that a second HTTP
    worker thread can land in the middle of. Holding it across the build is the
    point: the second caller waits and then finds the index the first one just
    made, rather than starting a duplicate parse of the same tree. An exception
    from `_build_index` propagates with the lock released by the `with` and
    nothing written to `_INDEXES`/`_INDEX_MTIMES`, so a failed build leaves no
    half-state behind for the next call to trust.
    """
    out_path = Path(out)
    key = str(out_path.resolve())

    with _index_lock(key):
        current_mtime = _index_mtime(out_path)

        index = _INDEXES.get(key)
        if index is not None and key in _INDEX_MTIMES and current_mtime <= _INDEX_MTIMES[key]:
            return index

        # If the index is already loaded but its on-disk artifacts have been
        # updated, reload the Index and invalidate the result cache.
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
            _build_index(Path(repo), out_path)
            # A freshly built index invalidates everything computed from
            # whatever was there before. Dropping the cache here rather than at
            # the call sites means no path can rebuild and forget to.
            if cache is not None:
                cache.clear()
            current_mtime = _index_mtime(out_path)
        index = _INDEXES[key] = Index(out_path)
        _INDEX_MTIMES[key] = current_mtime
        if repo is not None:
            # The server's indexed root: repo_impact runs git here, not in cwd.
            index.repo_root = Path(repo).resolve()
        return index


def open_index_or_task(out, repo=None, cache=None, tasks=None):
    """Open the index, or start a background build and say so.

    Args:
        out: Index directory.
        repo: Repository to build from, or None.
        cache: A `ResultCache` to clear on a completed rebuild, or None.
        tasks: A `TaskManager` when `--async-build` is on, else None.

    Returns:
        `(index, None)` when an index is available, or `(None, message)` when a
        build is in flight or has failed -- the message being what the caller
        should hand back to the agent verbatim.
    """
    from pathlib import Path as _Path
    from .tasks import BUILDING, BUILDING_MESSAGE, FAILED, FAILED_MESSAGE

    out_path = _Path(out)
    if tasks is None or _has_index(out_path) or repo is None:
        return open_index(out, repo, cache), None

    task = tasks.for_dir(out_path)
    if task is None:
        task = tasks.start(repo, out_path)

    # Each status is read once and dispatched on in order. An earlier version
    # re-tested `status == BUILDING` after starting the task and fell through to
    # a synchronous open_index() when it had already moved on -- so a build that
    # failed *quickly* both swallowed its own error and then performed the
    # blocking build this flag exists to avoid.
    if task.status == BUILDING:
        return None, BUILDING_MESSAGE.format(**task.snapshot())
    if task.status == FAILED:
        # The server stays usable: every call gets a clear error until someone
        # rebuilds, rather than the process dying or retrying a build that has
        # already proved it cannot succeed.
        return None, FAILED_MESSAGE.format(error=task.error)
    if cache is not None:
        cache.clear()
    return open_index(out, repo, cache), None


# --------------------------------------------------------------- tools ----


def tool_repo_map(index: Index) -> str:
    """The repo map, plus a staleness note (#383) when the source tree has
    moved since the index was built -- see `_staleness_note`."""
    return _staleness_note(index) + index.map_prepend()


def tool_repo_search(
    index: Index, query: str, k: Any = 8, hops: Any = 1, budget_tokens=None
) -> str:
    """Cited markdown for `query`, never wider than MCP_MAX_BUDGET_TOKENS."""
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
        # Zero or negative is not a budget anyone means, and answering it with
        # "nothing fit in a 1-token budget" is true of the clamp but useless.
        # Use the default instead and say so; the note is charged to the budget.
        note += BUDGET_DEFAULTED.format(
            given=budget, default=MCP_BUDGET_TOKENS, ceiling=MCP_MAX_BUDGET_TOKENS
        )
        budget = MCP_BUDGET_TOKENS
    budget = max(1, min(budget, MCP_MAX_BUDGET_TOKENS))
    # count_tokens is len // 4 and so not additive: reserve the note's rounded-up
    # cost, the same arithmetic tool_repo_impact uses for its truncation notice.
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
        # pack_context measures the text it assembles, but the ceiling is the
        # promise made to the caller: re-check it here rather than trust it.
        text = _fit_lines(text, room, count_tokens)
    if not text.strip():
        # A budget clamped to the floor renders nothing, and an empty tool
        # result is the one answer an agent cannot act on: it reads the same
        # as "no such code". The note deliberately overruns a floor-sized
        # budget -- the promise this handler makes is the MCP_MAX ceiling, and
        # a sentence is cheaper than a retry loop against a blank string.
        return note + EMPTY_RESULT.format(budget=budget, ceiling=MCP_MAX_BUDGET_TOKENS)
    return note + text


def tool_repo_neighbours(
    index: Index, node_id: str, hops: Any = 1, limit: Any = MCP_NEIGHBOUR_LIMIT
) -> str:
    """One graph hop from `node_id` — the thing grep cannot do."""
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
        # lines[0] is the header, so len(lines) - 1 is the number of neighbours.
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


# ------------------------------------------------------------- aux cache ----

# Per-Index lookup structures for repo_find_symbol and repo_read, built once
# and kept only as long as the Index itself is: a WeakKeyDictionary, so when
# open_index() replaces an Index after a newer mtime, the old entry is
# collected instead of leaking one dict per rebuild for the life of the
# process.
_AUX_CACHE: "weakref.WeakKeyDictionary[Index, dict[str, Any]]" = weakref.WeakKeyDictionary()


def _chunk_body_lines(c: dict[str, Any]) -> list[str] | None:
    """`c["text"]` with its `chunks.iter_chunks` header stripped, one entry per
    physical source line covering `[start_line, end_line]` -- or None when
    that cannot be trusted.

    `text` is always `"\\n".join(header) + "\\n" + body`
    (chunks.py:257,332), and `header`'s own length varies chunk to chunk
    (entry-point/inherits/callers/callees/doc lines are each conditional), so
    there is no fixed offset to strip. But for a `symbol` chunk, or a `file`
    chunk with no symbols carved out of it, `body` is *exactly*
    `end_line - start_line + 1` physical lines (chunks.py:190,294) -- so that
    count, subtracted from the total, proves where the header ends without
    ever having to reconstruct it.

    A `file_residual` chunk breaks this: its body is the *non-contiguous*
    concatenation of the spans left after every symbol was carved out
    (chunks.py:273-286), so `end_line - start_line + 1` overcounts the actual
    body and the same arithmetic would silently slice into the header. Rather
    than guess at the gaps, repo_read treats a residual chunk as not covering
    anything -- a span that only a residual chunk holds answers "not
    indexed" (see tool_repo_read), which is honest; wrong lines are not.
    """
    if c.get("type") == "file_residual":
        return None
    start, end = c.get("start_line"), c.get("end_line")
    if not isinstance(start, int) or not isinstance(end, int) or end < start:
        return None
    lines = (c.get("text") or "").split("\n")  # AGENTS.md: never splitlines()
    body_len = end - start + 1
    header_len = len(lines) - body_len
    if header_len < 0:
        return None
    return lines[header_len:]


def _aux(index: Index) -> dict[str, Any]:
    """Build (once) and cache {"exact", "ci", "last_seg", "by_path"} for `index`.

    "exact"/"ci"/"last_seg" are name -> [node_id, ...] lookups for
    repo_find_symbol, in the same precedence `_boost_identifiers` uses
    (query.py:356-376): exact identifier, then case-insensitive, then the
    last `::`/`.`-separated segment of qualname. "by_path" is
    path -> [{start_line, end_line, lines}, ...] for repo_read, pre-stripped
    of their chunk header (`_chunk_body_lines`) and sorted by line range;
    chunks whose body cannot be trusted for exact-line reconstruction are
    left out, not guessed at.
    """
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
    """Name -> node_id(s): the lookup repo_neighbours/repo_read/repo_path_between
    /repo_blast_radius have no other way to get, short of a repo_search round trip.
    """
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

    import json as _json

    return note + _json.dumps(results, indent=2)


def tool_repo_read(
    index: Index,
    path: str,
    start_line: Any = 1,
    end_line: Any = None,
    context: Any = 0,
) -> str:
    """Widen a `[cite: path:start-end]` citation, from chunks.jsonl, not disk.

    Reading chunks rather than the filesystem is the load-bearing choice
    (#385): chunks have already passed secret-path exclusion and content
    redaction, the filesystem has not, and a served index may have no source
    tree beside it at all (the `graph` branch / Action-artifact case). A span
    not covered by any chunk -- including the `file_residual`
    under-40-character case, AGENTS.md -- answers "not indexed" rather than
    falling back to disk.
    """
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
            f"span may fall in an under-40-character residual that emits no chunk -- "
            f"see AGENTS.md)."
        )

    pieces = []
    for c in covering:
        c_start, c_end = c["start_line"], c["end_line"]
        seg_lo, seg_hi = max(want_start, c_start), min(want_end, c_end)
        if seg_lo > seg_hi:
            continue
        pieces.append("\n".join(c["lines"][seg_lo - c_start : seg_hi - c_start + 1]))
    # cursor overshoots to the end of whichever chunk satisfied the last line
    # of the window (chunks are not sliced to want_end while checking
    # coverage), so the header must report min(cursor - 1, want_end), not
    # cursor - 1 -- the citation must name only what was asked for.
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
    """Coerce repo_path_between's `edge_types`, defaulting on anything unusable."""
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
    """Every (node-id path, edge-tuple path) from `root` to `node`, capped at `cap`.

    `parents[node]` holds every `(parent, etype, direction, edge)` that
    reaches `node` at its shortest distance from `root` -- more than one
    entry when several edges tie for shortest, which is how several distinct
    shortest paths get reconstructed rather than just one.
    """
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
    """Meet-in-the-middle BFS over index.adj, alternating the smaller frontier.

    Real work is roughly `max_hops / 2` layers deep on each side rather than
    `max_hops` deep on one -- the difference between tractable and not on a
    dense call graph (#386). Returns
    `(dist_f, parents_f, dist_b, parents_b, meet, truncated)`; `dist_f`/
    `parents_f` are rooted at `from_id`, `dist_b`/`parents_b` at `to_id`, and
    `meet` is the node where the two frontiers first touched (or None).
    """
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
    """Bounded, bidirectional path(s) between `from_id` and `to_id`, with the
    minimum edge confidence along each -- the question a graph answers that
    grep cannot approximate at all (#386).
    """
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

    import json as _json

    if meet is None:
        return note + _json.dumps(
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

    return note + _json.dumps(
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
    """Analyze PR or git diff impact against a base branch using the code graph.

    With no `head`, the working tree (committed *and* uncommitted changes) is
    compared against `base` -- what `repo2graph impact` does. A `head` gives the
    three-dot `base...head` comparison of two refs instead.

    Enforces exclude_secrets=True unconditionally and clamps numeric arguments.
    """
    base_ref = _str(base, 256).strip() or "main"
    head_ref = _str(head, 256).strip() or None
    diff_text = _str(diff, 1_000_000)
    depth = _clamp(max_depth, 2, 1, MCP_MAX_HOPS)

    from .impact import (
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
        # Falling back to markdown answered a request for a machine-readable
        # format with prose the caller then fails to parse.
        return ToolError(
            f"unknown repo_impact format {str(format)[:40]!r}: expected one of "
            f"{', '.join(IMPACT_FORMATS)}."
        )

    if diff_text.strip() and not parse_unified_diff(diff_text):
        # A caller-supplied diff with no file header is not "a PR that changed
        # nothing": answering it LOW RISK is a confident wrong result.
        return ToolError(
            "`diff` is not a unified diff: found no `diff --git a/<path> b/<path>` file "
            "header. Pass the output of `git diff` (or omit `diff` to let the server run it)."
        )

    if not diff_text.strip():
        root_path = _impact_root(index)
        try:
            diff_text = get_git_diff(root_path, base=base_ref, head=head_ref)
        except Exception as exc:
            # git's own message is what tells a caller *why* ("unknown
            # revision", "not a git repository"), so it is relayed -- but with
            # every absolute path scrubbed first: `git -C <root>` names <root>
            # in its failures, and the server's filesystem layout is exactly
            # what `http_server`'s INDEX_UNAVAILABLE and `_public_repo_label`
            # keep server-side for every other route. The refs are the
            # caller's own input, so echoing those discloses nothing.
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

    import json as _json

    if fmt == "json":
        rendered = format_json(report)
    elif fmt == "sarif":
        rendered = _json.dumps(format_sarif(report), indent=2)
    elif fmt == "pr-comment":
        rendered = format_pr_comment(report)
    else:
        rendered = format_markdown(report)
    if fmt in ("markdown", "pr-comment"):
        # Machine-readable formats get no prose prefix: it would stop parsing.
        rendered = _defaulted_notes(max_depth=(max_depth, 2)) + rendered

    # Bound the rendered result, the second half of the AGENTS.md MCP rule: a
    # clamped *input* does not bound the *output*. The report grows with the
    # number of impacted symbols, not with `max_depth`, and `diff` is accepted up
    # to 1 MB -- a diff naming every indexed path rendered 17,927 tokens as JSON,
    # already 1.5x the ceiling `repo_search` enforces on itself. Measured after
    # rendering rather than predicted, for the same reason `repo_search`
    # re-measures `pack_context`'s own output instead of trusting it.
    if count_tokens(rendered) <= MCP_MAX_BUDGET_TOKENS:
        return rendered

    if fmt in ("json", "sarif"):
        # `_fit_lines` would cut mid-structure and hand back a string that is no
        # longer JSON, which for a machine-readable format is worse than the
        # overrun: the caller gets a parse error instead of a result. Return the
        # scalar summary -- which is what does not grow with the diff -- as a
        # valid document, and say plainly that the lists were dropped.
        return _json.dumps(
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

    # Markdown and pr-comment are line-oriented, so a line-boundary cut degrades
    # into a shorter report rather than a malformed one.
    #
    # The notice's cost is paid for *before* fitting -- appending it afterwards
    # put the result back over the ceiling by its own length (12,013 against a
    # 12,000 bound). A truncation notice that breaks the limit it announces is
    # the one thing it must not do.
    #
    # Subtracting the notice's own `count_tokens` is not enough, because
    # `count_tokens` is `len // 4` and so is not additive. `_fit_lines` only
    # guarantees `len(fit) // 4 <= room`, i.e. up to `4 * room + 3` characters, so
    # a notice whose length is not a multiple of 4 can carry the sum's floor one
    # token over. Reserving `(len(notice) + 3) // 4` closes it arithmetically:
    #
    #     len(fit) + len(notice) <= 4 * room + 3 + len(notice)
    #     => count_tokens(total) <= room + (len(notice) + 3) // 4 == ceiling
    #
    # -- exact, in one pass. Iterating `room` downward until it fit would also
    # work but re-runs `_fit_lines`, which rebuilds its candidate string on every
    # line and is therefore quadratic in line count; one call is the difference
    # between ~0.3s and ~27s on a 60k-line report.
    notice = (
        f"\n\n_[truncated to {MCP_MAX_BUDGET_TOKENS} tokens. "
        f"Narrow the diff, or run `repo2graph impact` for the full report.]_"
    )
    room = max(1, MCP_MAX_BUDGET_TOKENS - (len(notice) + 3) // 4)

    # Hand `_fit_lines` only the prefix that could possibly survive. Every line it
    # keeps lies inside the first `4 * room + 3` characters, so cutting to the
    # last newline at or beyond that bound is loss-free -- and it stops the
    # quadratic candidate rebuild from walking a report that may be megabytes of
    # lines past the point where the budget was already spent.
    head = rendered[: 4 * room + 4]
    if len(head) < len(rendered):
        cut = head.rfind("\n")
        if cut > 0:
            head = head[:cut]
    return _fit_lines(head, room, count_tokens) + notice


def _reverse_closure(
    index: Index, seed: str, etype: str, max_hops: int, visited_cap: int
) -> tuple[dict[str, int], bool]:
    """{node_id: hop_distance} walking `etype` edges backward from `seed`.

    An adjacency entry `(dst, et, "in", edge)` on `seed` means the edge's
    real source is `dst` -- something pointing *at* `seed` -- which is
    exactly "what would break if `seed` changed": callers of callers for
    CALLS, subclasses of subclasses for INHERITS, importers of importers for
    IMPORTS. Capped by `visited_cap` as well as `max_hops`, and returns
    `truncated=True` rather than a partial set presented as complete (#387).
    """
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
    """`node_id`'s own file node, or `node_id` itself when it already is one."""
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
    """Reverse reachability from `node_id`: what would break if it changed.

    The reverse of repo_neighbours' one hop in both directions: this is
    specifically the reverse *closure* -- callers of callers, subclasses of
    subclasses, importers of importers of the containing file -- by hop
    distance, plus CO_CHANGE files kept visually separate because they are
    historical correlation, not an edge the AST produced (#387). Note: #387
    named this tool `repo_impact`, but that name already belongs to the
    PR/diff-impact tool above (a fully built, different, existing feature);
    it ships here as `repo_blast_radius` instead so neither breaks the other.
    """
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

    import json as _json

    return note + _json.dumps(
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
    """The source tree repo_impact runs git in: the server's indexed root.

    Never `Path.cwd()` -- an MCP client launches the server from wherever it
    likes. In order: the repo the server was started for (`open_index` sets
    `repo_root`), the absolute root the build recorded in manifest.json,
    and finally the index directory's parent (the `<repo>/.r2g` layout).
    """
    raw = getattr(index, "repo_root", None)
    if raw:
        return Path(str(raw))
    index_dir = Path(getattr(index, "dir", ".") or ".")
    from .status import stored_source_root

    stored = stored_source_root(artifact_path(index_dir, "manifest.json").parent)
    if stored is not None:
        return stored
    return index_dir.resolve().parent


def _staleness_note(index: Index) -> str:
    """A `_note:` line when the index no longer matches the working tree (#383).

    `_index_mtime`/`open_index`'s reload check both compare the index against
    *itself at an earlier moment* -- neither ever looks at the source tree, so
    a long-running server answers from a stale graph indefinitely and every
    `[cite: path:start-end]` anchor it hands out is confidently wrong. The
    fix is `status.compute_freshness`: it already does the real comparison
    (HEAD commit, then the per-file sha256 `index.state.json` records against
    a fresh `discover()`) for `index-status`/`doctor`, and nothing at query
    time read it back. This does.

    Bounded, not run on every call: `repo_map` is in `CACHEABLE_TOOLS`
    (cache.py), so under the default cache this only actually executes once
    per `cache.ttl` seconds per index rather than on every request --
    `compute_freshness` itself additionally gates its file hashing on mtime
    and gives up past `MAX_FRESHNESS_FILES` files, but the *call frequency*
    bound is this cache, reused rather than a second interval mechanism
    invented here. With the cache disabled (`--cache-size 0`) it runs every
    call, same as any other repo_map work.

    Silent, not just quiet, on anything this cannot answer: no `repo_root`
    (never told which tree the index came from), an unreadable or absent
    `index.state.json`, no git, any exception from `compute_freshness` itself
    -- all degrade to "" rather than a false "current" or a broken call. Only
    an actual "stale" verdict produces a line, same rule the vectors path
    uses (AGENTS.md).
    """
    try:
        repo_root = _impact_root(index)
        if not repo_root.is_dir():
            return ""
        index_dir = Path(getattr(index, "dir", ".") or ".")
        agent_dir = artifact_path(index_dir, "index.state.json").parent

        from .status import compute_freshness

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


# An absolute path inside an error message: `C:\...`, `C:/...`, `\\server\...`
# or a POSIX `/...` (not the `/` inside a ref like `origin/main`). It runs to
# the next quote, newline or ": " -- the separators git's messages use.
_ABS_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|(?<![\w.~>-])/)[^'\"\n]*?(?=['\"\n]|: |$)")


def _scrub_paths(text: str, root: Path) -> str:
    """Remove absolute filesystem paths from an error message."""
    for known in {str(root), str(root.resolve()), root.as_posix(), root.resolve().as_posix()}:
        if known and known not in (".", "/"):
            text = text.replace(known, "<repo>")
    return _ABS_PATH_RE.sub("<path>", text).strip()


def _edge_note(index: "Index", src: str, dst: str, etype: str) -> str:
    """Where the relationship is written, and how sure repo2graph is of it.

    The node label already says where the *neighbour* is defined. For "what
    calls this", the citation an agent actually needs is the call site, which
    lives on the edge -- and without the confidence, an ambiguous name match
    reads exactly like a certain one. `compute_freshness -> ResultCache.get`
    is a real example from this repository: a `.get()` on a dict resolved to
    one of three candidates at 0.5, and the tool presented it as fact.

    Certain edges get only their evidence, so the common case stays terse.
    """
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


def _int(value, fallback: int) -> int:
    """An MCP client's arguments are JSON a model wrote: coerce, never raise.

    `OverflowError` too: Python's `json` parses `1e999` as `float("inf")` (and
    `NaN`/`Infinity` literally), and `int(inf)` raises OverflowError rather than
    ValueError. NaN already lands in ValueError. Both are malformed input and
    take the fallback, like a string or a null -- a caller asking for an
    infinite budget gets the default, not the ceiling.
    """
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return fallback


def _defaulted_notes(**args: tuple[Any, int]) -> str:
    """One `_note:` line per argument that was given but is not a number.

    Such a value takes the default rather than failing the call (docs/mcp.md,
    "Argument bounds"), but it did so silently: `k: "abc"` looked honoured.
    Charged to the output like BUDGET_DEFAULTED; empty when all were usable.
    """
    notes = ""
    for name, (value, default) in args.items():
        if value is None or isinstance(value, bool):
            continue
        try:
            int(value)
        except (TypeError, ValueError, OverflowError):
            notes += (
                f"_note: {name}={repr(value)[:40]} is not an integer; "
                f"used the default {default}._\n\n"
            )
    return notes


def _clamp(value, fallback: int, low: int, high: int) -> int:
    """_int, then held inside [low, high]: no argument may cost unbounded time."""
    return max(low, min(_int(value, fallback), high))


def _str(value, max_chars: int) -> str:
    """Coerce a caller-supplied string argument and cap its length.

    A model can hand this dispatcher an arbitrarily long value; this is the
    string-typed counterpart to `_clamp` for the numeric arguments.
    """
    text = "" if value is None else str(value)
    return text[:max_chars]


def tool_cache_stats(cache) -> str:
    """Cache counters as JSON. Diagnostics only: no repository content."""
    import json as _json

    if cache is None:
        return _json.dumps(
            {"enabled": False, "hits": 0, "misses": 0, "size": 0, "max_size": 0, "ttl_s": 0},
            indent=2,
        )
    return _json.dumps(cache.stats(), indent=2)


def tool_build_status(tasks, task_id: str) -> str:
    """Status of one background build, as JSON.

    Args:
        tasks: The `TaskManager`, or None when builds are synchronous.
        task_id: The id handed out when the build started.

    Returns:
        A JSON status document, or a sentence naming why there is none.
    """
    import json as _json

    task_id = _str(task_id, MCP_MAX_TASK_ID_CHARS)
    # Every "no status to report" answer is a ToolError (isError: true), still
    # carrying its JSON body: a client must be able to tell "there is no such
    # build" from a status document without parsing prose (docs/mcp.md).
    if tasks is None:
        return ToolError(
            _json.dumps(
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
            _json.dumps(
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
            _json.dumps(
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
    return _json.dumps(task.snapshot(), indent=2)


def dispatch(index: "Index | None", name: str, arguments: dict, cache=None, tasks=None) -> str:
    """Route one tool call to its handler. Pure, so serve() holds no logic.

    Args:
        index: The open index every tool answers from. None is allowed only
            for `repo_build_status`, which reports on a build and therefore
            must be answerable while there is still no index to open.
        name: Tool name the caller asked for.
        arguments: The caller's arguments, which are JSON a model wrote and are
            treated as hostile throughout.
        cache: Optional `ResultCache`. When given, repeated identical calls are
            served from it instead of re-scoring the index.

    Returns:
        The tool's text result, or a sentence naming the problem. Never raises
        on bad arguments: every numeric one is coerced and clamped. A problem
        (missing required argument, unknown node or tool, git failure) comes
        back as a `ToolError` -- still a `str` -- so the transports can mark
        the MCP result `isError: true`.
    """
    args = arguments or {}
    if name == "repo_build_status":
        # Never cached, for the same reason repo_cache_stats is not: a cached
        # progress report is the one answer guaranteed to be out of date.
        return tool_build_status(tasks, str(args.get("task_id") or ""))
    if name == "repo_cache_stats":
        # Never cached: a cached cache-stats call reports the counters as they
        # were when it was stored, which is the one answer that is always wrong.
        return tool_cache_stats(cache)
    if name not in TOOL_DESCRIPTIONS:
        # Not cached: an unknown-tool message is cheap, and caching it would
        # fill the cache with whatever names a confused caller invents.
        return ToolError(f"unknown tool: {name!r}. Available: {', '.join(TOOL_DESCRIPTIONS)}.")

    from .cache import CACHEABLE_TOOLS, make_key

    key = None
    if cache is not None and name in CACHEABLE_TOOLS:
        key = make_key(name, args)
        hit = cache.get(key)
        if hit is not None:
            return hit

    if index is None:
        # Only repo_build_status and repo_cache_stats are answerable without an
        # index, and both returned above. Reaching here with none is a caller
        # bug rather than a user error, but it must still be a sentence.
        return ToolError(
            "no index is open, so this tool cannot answer. Use "
            "repo_build_status to check whether one is still being built."
        )
    if name == "repo_map":
        result = tool_repo_map(index)
    elif name == "repo_search":
        result = tool_repo_search(
            index,
            str(args.get("query") or ""),
            # Raw, not `_int`-ed: the handler coerces, and notes a non-number.
            k=args.get("k", 8),
            hops=args.get("hops", 1),
            budget_tokens=args.get("budget_tokens"),
        )
    elif name == "repo_neighbours":
        result = tool_repo_neighbours(
            index,
            str(args.get("node_id") or ""),
            hops=args.get("hops", 1),
            limit=args.get("limit", MCP_NEIGHBOUR_LIMIT),
        )
    elif name == "repo_impact":
        result = tool_repo_impact(
            index,
            base=str(args.get("base") or "main"),
            head=str(args.get("head") or "") or None,
            diff=str(args.get("diff") or ""),
            max_depth=args.get("max_depth", 2),
            format=str(args.get("format") or "markdown"),
        )
    elif name == "repo_find_symbol":
        result = tool_repo_find_symbol(
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
    else:  # unreachable: unknown names returned above
        return ToolError(f"unknown tool: {name!r}. Available: {', '.join(TOOL_DESCRIPTIONS)}.")

    if key is not None and not isinstance(result, ToolError):
        cache.put(key, result)
    return result


# -------------------------------------------------------------- server ----

MISSING_SDK = 'the MCP server needs the optional `mcp` extra: pip install "repo2graph[mcp]"'

# The SDK range serve() is written against, and the API it needs from it.
#
# 1.x is no longer supported (#407/#291). serve() used to branch on
# `hasattr(Server, "list_tools")` to drive either the 1.x decorator API
# (`@server.list_tools()` / `@server.call_tool()`) or the 2.x registration API
# (`on_list_tools=`/`on_call_tool=` constructor kwargs) -- but the 1.x branch
# was never exercised by CI (which always resolved 2.x fresh from PyPI) and,
# when it finally was, hung forever on the first `tools/call`
# (test_iss90_tools_call_over_serve_completes_on_the_parallel_path,
# deterministic, ~4 min to time out under mcp 1.30.0; 1.9s under 2.2.0). The
# branch is gone; serve() now speaks only the 2.x registration API.
SDK_SPEC = "mcp>=2.0,<3.0"


def _sdk_version(module) -> str:
    """Best-effort version of the installed SDK, for the error message."""
    version = getattr(module, "__version__", None)
    if version:
        return str(version)
    try:
        from importlib.metadata import version as _dist_version

        return str(_dist_version("mcp"))
    except Exception:
        return "unknown"


def _sdk_major(version: str) -> int | None:
    """The leading integer of a version string, or None when it cannot be read.

    `_sdk_version` can return "unknown" (no `__version__`, no distribution
    metadata) or an unparsed string from a non-PEP 440 build; either way this
    must degrade to "cannot tell" rather than raise or guess.
    """
    match = re.match(r"\s*(\d+)", version or "")
    return int(match.group(1)) if match else None


def _unusable_sdk(version: str, detail: str) -> str:
    return (
        f"the installed mcp SDK ({version}) is not supported by "
        f"repo2graph-mcp: {detail}. Install a compatible SDK instead: "
        f'pip install "{SDK_SPEC}" '
        '(or `pip install "repo2graph[mcp]"` in a clean environment).'
    )


def _require_sdk() -> Any:
    """Turn a missing *or unusable* optional dependency into an instruction.

    Three distinct failures, each of which must end in a sentence a user can
    act on rather than a traceback or a silent hang: the SDK is absent, the
    SDK is present but speaks an API this module cannot import against, or the
    SDK is present and importable but is the unsupported 1.x generation
    (#407) -- `pip install "repo2graph[mcp]"` cannot silently install that
    generation any more (#291), because the version is checked here, not
    inferred from what imported successfully.

    On success this also announces the installed vs. required version on
    stderr (#291's "startup diagnostics" acceptance criterion), once per
    process -- so a mismatch a user did hit anyway (an unpinned `pip install`
    resolving something outside SDK_SPEC via a private index, a downgrade)
    is visible instead of only manifesting as a later, unrelated failure.
    """
    try:
        import mcp
    except ImportError:
        raise SystemExit(MISSING_SDK) from None
    if mcp is None:
        raise SystemExit(MISSING_SDK)
    try:
        from mcp.server import Server as _Server  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            _unusable_sdk(_sdk_version(mcp), f"`from mcp.server import Server` failed ({exc})")
        ) from None
    installed = _sdk_version(mcp)
    major = _sdk_major(installed)
    if major is not None and major < 2:
        raise SystemExit(
            _unusable_sdk(
                installed,
                f"mcp {installed} is a 1.x release, and 1.x hangs on the first "
                f"tool call under repo2graph-mcp (#407) -- repo2graph-mcp requires {SDK_SPEC}",
            )
        )
    from .events import emit

    emit(
        "mcp_sdk_version",
        level="info",
        installed=installed,
        required=SDK_SPEC,
    )
    return mcp


def get_tools(types_module=None, auto_build: bool = False):
    """Construct Tool instances with descriptions, schemas, and annotations.

    Args:
        types_module: `mcp.types`, or a stand-in for testing.
        auto_build: Whether this server instance can build a missing index
            on a tool call -- i.e. it was started with a repo to build from
            and auto-build was not disabled (`repo is not None`). When True,
            every tool in `BUILD_CAPABLE_TOOLS` gets `TOOL_ANNOTATIONS_AUTO_BUILD`
            instead of the read-only set: a client inspecting annotations must
            not be told a tool is read-only when its first call may parse the
            whole repository, run git, and write `.r2g/**` to disk.
            `repo_build_status` always keeps the read-only set -- it is the one
            tool that never triggers a build.
    """
    if types_module is None:
        try:
            import mcp.types as _types_module
        except ImportError:
            return []
        types_module = _types_module
    tool_cls = getattr(types_module, "Tool", None)
    if tool_cls is None:
        return []
    tool_ann_cls = getattr(types_module, "ToolAnnotations", None)
    tools = []
    for name, description in TOOL_DESCRIPTIONS.items():
        kwargs = {
            "name": name,
            "description": description,
            "inputSchema": TOOL_SCHEMAS[name],
        }
        tool_fields = getattr(tool_cls, "model_fields", None)
        if tool_fields is None:
            tool_fields = getattr(tool_cls, "__annotations__", {})
        if "annotations" in tool_fields or hasattr(tool_cls, "annotations"):
            ann = tool_annotations(name, auto_build)
            if tool_ann_cls is not None:
                try:
                    kwargs["annotations"] = tool_ann_cls(**ann)
                except Exception:
                    kwargs["annotations"] = ann
            else:
                kwargs["annotations"] = ann
        tools.append(tool_cls(**kwargs))
    return tools


class ServerWrapper:
    def __init__(self, name, version=None):
        self.name = name
        self.version = version
        self.tools = {}

    def add_tool(self, name, handler, schema=None):
        self.tools[name] = {"handler": handler, "schema": schema}


server = ServerWrapper("repo2graph", version=__version__)
server.add_tool("repo_map", tool_repo_map, TOOL_SCHEMAS["repo_map"])
server.add_tool("repo_search", tool_repo_search, TOOL_SCHEMAS["repo_search"])
server.add_tool("repo_neighbours", tool_repo_neighbours, TOOL_SCHEMAS["repo_neighbours"])
server.add_tool("repo_find_symbol", tool_repo_find_symbol, TOOL_SCHEMAS["repo_find_symbol"])
server.add_tool("repo_read", tool_repo_read, TOOL_SCHEMAS["repo_read"])
server.add_tool("repo_path_between", tool_repo_path_between, TOOL_SCHEMAS["repo_path_between"])
server.add_tool("repo_impact", tool_repo_impact, TOOL_SCHEMAS["repo_impact"])
server.add_tool("repo_blast_radius", tool_repo_blast_radius, TOOL_SCHEMAS["repo_blast_radius"])
server.add_tool("repo_cache_stats", tool_cache_stats, TOOL_SCHEMAS["repo_cache_stats"])
server.add_tool("repo_build_status", tool_build_status, TOOL_SCHEMAS["repo_build_status"])


class ToolCallFailed(Exception):
    """Carries a ToolError's text out of a 1.x SDK handler as isError."""


def run_tool(index_dir, repo, name, arguments, cache=None, tasks=None) -> str:
    """One stdio tool call: open (or start building) the index, then dispatch.

    Returns a `ToolError` for anything the caller got wrong, which the
    transport turns into `isError: true`.
    """
    name = name or ""
    if name == "repo_build_status":
        # Answerable without an index, and the only tool that is: asking
        # for build progress must not itself wait on the build.
        return dispatch(None, name, arguments or {}, cache=cache, tasks=tasks)
    if name not in TOOL_DESCRIPTIONS:
        # Checked before open_index: an unknown name must not trigger a build.
        return dispatch(None, name, arguments or {}, cache=cache, tasks=tasks)
    index, pending = open_index_or_task(index_dir, repo, cache, tasks)
    if pending is not None:
        return pending
    return dispatch(index, name, arguments or {}, cache=cache, tasks=tasks)


def serve(out, repo=None, cache=None, tasks=None) -> None:
    """Run the stdio MCP server against the index at `out`.

    Deliberately thin: every answer comes from dispatch(), which is tested
    without the SDK, so SDK API drift can break the wiring but nothing else.
    """
    mcp = _require_sdk()
    index_dir = Path(out)
    if repo is None:
        open_index(index_dir)
    import asyncio

    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent

    async def list_tools_handler(*args, **kwargs):
        # #292: `repo is not None` is exactly stdio's auto-build signal --
        # `main()` sets `build_from = None if args.no_auto_build else repo`
        # and passes that as `serve()`'s own `repo` argument, so this reads
        # the same "can this server build" fact `open_index_or_task` acts on,
        # not a second guess at it.
        return get_tools(mcp.types, auto_build=(repo is not None))

    async def call_tool_handler(ctx, params):
        name = params.name
        arguments = params.arguments
        return run_tool(index_dir, repo, name, arguments, cache=cache, tasks=tasks)

    server_cls: Any = Server

    async def list_tools_2x(ctx, params):
        return mcp.types.ListToolsResult(tools=await list_tools_handler())

    async def call_tool_2x(ctx, params):
        text = await call_tool_handler(ctx, params)
        return mcp.types.CallToolResult(
            content=[TextContent(type="text", text=text)],
            isError=isinstance(text, ToolError),
        )

    try:
        mcp_server = server_cls(
            server.name,
            version=server.version,
            on_list_tools=list_tools_2x,
            on_call_tool=call_tool_2x,
        )
    except TypeError:
        mcp_server = server_cls(server.name, version=server.version)

    async def _run():
        async with stdio_server() as (read_stream, write_stream):
            await mcp_server.run(
                read_stream, write_stream, mcp_server.create_initialization_options()
            )

    asyncio.run(_run())


def resolve_paths(repo=None, out=None):
    """Work out (index_dir, repo_to_build_from) from what the user passed.

    Returns a repo of None when there is nothing safe to infer, which turns
    auto-build off and leaves the "build one first" error in place.

    Only one convention is trusted: an index directory named `.r2g` belongs to
    the directory above it, which is how every command, doc and example in this
    project lays it out. Any other `--out` name and the repo is not guessed --
    `--out /var/cache/indexes/myproj` must not end up parsing `/var/cache/indexes`.
    Name the repo positionally to index something that is not laid out that way.
    """
    if repo is not None:
        repo_path = Path(repo)
        if not repo_path.is_dir():
            raise SystemExit(
                f"error: repository directory does not exist or is not a directory: {repo_path}"
            )
        return (Path(out) if out else repo_path / INDEX_DIRNAME), repo_path
    out_path = Path(out) if out else Path(INDEX_DIRNAME)
    if out_path.name == INDEX_DIRNAME and out_path.parent.is_dir():
        return out_path, out_path.parent
    return out_path, None


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="repo2graph-mcp", description="Serve a repo2graph index over MCP on stdio"
    )
    p.add_argument(
        "repo",
        nargs="?",
        default=None,
        help="repository to serve; its index is built on the first "
        "tool call if one does not exist yet "
        "(default: the directory holding --out)",
    )
    p.add_argument("-o", "--out", default=None, help="index directory (default: <repo>/.r2g)")
    p.add_argument(
        "--no-auto-build",
        action="store_true",
        help="never build: exit unless the index already exists",
    )
    p.add_argument(
        "--async-build",
        action="store_true",
        help="build a missing index on a background thread and "
        "return a task_id immediately, instead of blocking the "
        "first tool call until it finishes. Poll it with "
        "repo_build_status (default: off, build synchronously)",
    )
    p.add_argument(
        "--cache-size",
        type=int,
        default=DEFAULT_MAX_SIZE,
        metavar="N",
        help=f"cached tool results before the least recently used "
        f"is evicted; 0 disables the cache "
        f"(default: {DEFAULT_MAX_SIZE})",
    )
    p.add_argument(
        "--cache-ttl",
        type=float,
        default=DEFAULT_TTL,
        metavar="SECONDS",
        help=f"seconds a cached result is served before it is "
        f"recomputed (default: {DEFAULT_TTL:g})",
    )
    log = p.add_argument_group("audit logging")
    log.add_argument(
        "--audit-log",
        default=None,
        metavar="PATH",
        help="append audit records to this file as well as stderr (default: stderr only)",
    )
    log.add_argument(
        "--audit-log-level",
        choices=("none", "errors", "all"),
        default="all",
        help="which tool calls produce an audit record (default: all)",
    )
    log.add_argument(
        "--audit-log-fsync",
        action="store_true",
        help="sync each audit record to disk before returning (slower; stderr "
        "already carries every record, so this only hardens the file copy "
        "against a crash)",
    )
    args = p.parse_args(argv)
    index_dir, repo = resolve_paths(args.repo, args.out)
    cache = ResultCache(max_size=args.cache_size, ttl=args.cache_ttl)
    build_from = None if args.no_auto_build else repo

    from .audit import AuditConfig, AuditLogger

    audit = AuditLogger(
        AuditConfig(
            level=args.audit_log_level,
            path=args.audit_log,
            fsync=args.audit_log_fsync,
        )
    )

    tasks = None
    if args.async_build:
        from .tasks import TaskManager

        tasks = TaskManager()

    try:
        serve(index_dir, build_from, cache=cache, tasks=tasks)
    finally:
        audit.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
