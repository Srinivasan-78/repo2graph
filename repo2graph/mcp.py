# @authormark v1 -- do not remove (authorship watermark)⁠​‌‌​‌​​‌​​‌‌​​​​​​‌‌​‌‌​​‌​​‌‌‌‌​‌‌‌‌​​​​‌‌‌‌​​​​​‌‌‌​​‌​‌​​‌‌​​​‌​​‌‌​​​‌​​‌‌‌​​‌‌‌‌​‌​​‌​​‌‌​​​‌‌​​​​‌​‌‌‌​‌‌​​‌‌‌​‌​‌​‌​‌‌​​‌​​‌‌‌​​​​‌​​​​‌​​‌‌​‌​​‌​‌‌​​​​‌​​‌‌​​​​​‌‌‌‌​​‌⁠
# Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
# Author: https://github.com/Srinivasan-78
# SPDX-License-Identifier: MIT
# Fingerprint: AMK1.i06Oxx9LLNzLavuY8Bia0y
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
import sys
from pathlib import Path

from . import __version__
from .cache import DEFAULT_MAX_SIZE, DEFAULT_TTL, ResultCache
from .export import path as artifact_path
from .query import Index, _fit_lines, count_tokens

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

TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

TOOL_TITLES = {
    "repo_map": "Repository Map",
    "repo_search": "Search Codebase",
    "repo_neighbours": "Traverse Graph Neighbors",
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
        "and entry points."
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
                    f"max {MCP_MAX_BUDGET_TOKENS})."
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
                "description": (
                    f"Traversal depth from node_id (default 1, max {MCP_MAX_HOPS})."
                ),
            },
            "limit": {
                "type": "integer",
                "description": (
                    f"Maximum neighbor rows to return (default {MCP_NEIGHBOUR_LIMIT}, "
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
EMPTY_RESULT = ("no content fit in a {budget}-token budget: nothing matched, "
                "or the budget was too small to render a single line. Retry "
                "with a broader query or budget_tokens up to {ceiling}.")

_INDEXES: dict[str, Index] = {}


def _has_index(out_path: Path) -> bool:
    """True when `out_path` holds an index a tool can actually read."""
    return out_path.exists() and artifact_path(out_path, "chunks.jsonl").is_file()


def _build_index(repo: Path, out: Path) -> None:
    """Index `repo` into `out`, in-process, single-process, silent on stdout.

    Two constraints, both of them about the transport rather than about graphs:

    * Nothing may reach stdout. It carries the JSON-RPC stream, and one stray
      `print` ends the session. `graph.build` and `export.dump_all` write no
      console output of their own -- the CLI's `cmd_build` is what emits the
      JSON report -- so calling them directly is what keeps the wire clean.
    * `jobs=1`, always. `build()` reaches for a process pool above
      PARALLEL_MIN_FILES files, and spawning one from inside the running stdio
      server hangs: the workers inherit the parent's stdin and stdout, which are
      the client's pipes, and the first call never returns. It is not a
      throughput loss worth mourning -- at the threshold the pool costs more to
      start than it saves -- but on a large repo this build is slower than the
      CLI's. `repo2graph build` is still the way to index one quickly.
    """
    from .chunks import iter_chunks
    from .export import dump_all
    from .graph import build

    graph = build(repo, jobs=1)
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
    """
    out_path = Path(out)
    key = str(out_path.resolve())
    index = _INDEXES.get(key)
    if index is not None:
        return index
    if not _has_index(out_path):
        if repo is None:
            raise SystemExit(
                f"error: no repo2graph index found at '{out}'. "
                f"Build one first with: repo2graph build <path> -o {out}"
            )
        _build_index(Path(repo), out_path)
        # A freshly built index invalidates everything computed from whatever
        # was there before. Dropping the cache here rather than at the call
        # sites means no path can rebuild and forget to.
        if cache is not None:
            cache.clear()
    index = _INDEXES[key] = Index(out_path)
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
    """The repo map, verbatim: stable, cacheable, no query argument."""
    return index.map_prepend()


def tool_repo_search(index: Index, query: str, k: int = 8, hops: int = 1,
                     budget_tokens=None) -> str:
    """Cited markdown for `query`, never wider than MCP_MAX_BUDGET_TOKENS."""
    budget = MCP_BUDGET_TOKENS if budget_tokens is None else _int(budget_tokens,
                                                                  MCP_BUDGET_TOKENS)
    budget = max(1, min(budget, MCP_MAX_BUDGET_TOKENS))
    pack = index.pack_context(query, k=_clamp(k, 8, 1, MCP_MAX_K),
                              hops=_clamp(hops, 1, 0, MCP_MAX_HOPS),
                              budget_tokens=budget, exclude_secrets=True)
    text = pack["markdown"]
    if count_tokens(text) > budget:
        # pack_context measures the text it assembles, but the ceiling is the
        # promise made to the caller: re-check it here rather than trust it.
        text = _fit_lines(text, budget, count_tokens)
    if not text.strip():
        # A budget clamped to the floor renders nothing, and an empty tool
        # result is the one answer an agent cannot act on: it reads the same
        # as "no such code". The note deliberately overruns a floor-sized
        # budget -- the promise this handler makes is the MCP_MAX ceiling, and
        # a sentence is cheaper than a retry loop against a blank string.
        return EMPTY_RESULT.format(budget=budget, ceiling=MCP_MAX_BUDGET_TOKENS)
    return text


def tool_repo_neighbours(index: Index, node_id: str, hops: int = 1,
                         limit: int = MCP_NEIGHBOUR_LIMIT) -> str:
    """One graph hop from `node_id` — the thing grep cannot do."""
    node = index.nodes.get(node_id)
    if node is None:
        return (f"node not found: {node_id!r}. Ids look like "
                f"file:<path>, sym:<path>::<qualname> or dir:<path>.")
    limit = _clamp(limit, MCP_NEIGHBOUR_LIMIT, 1, MCP_MAX_NEIGHBOURS)
    lines = [f"neighbours of {_label(index, node_id)}:"]
    truncated = False
    for dst, etype, direction, _src in index.expand(
            [node_id], hops=_clamp(hops, 1, 0, MCP_MAX_HOPS)):
        target = index.nodes.get(dst, {})
        if index._is_secret_path(target.get("path") or ""):
            continue
        # lines[0] is the header, so len(lines) - 1 is the number of neighbours.
        if len(lines) - 1 >= limit:
            truncated = True
            break
        lines.append(f"- {etype} {direction}: {_label(index, dst)}")
    if truncated:
        lines.append(f"... (truncated at {limit} neighbours)")
    if len(lines) == 1:
        lines.append("- (none)")
    return "\n".join(lines)


def _label(index: Index, node_id: str) -> str:
    node = index.nodes.get(node_id) or {}
    name = node.get("qualname") or node.get("name") or node_id
    where = node.get("path") or ""
    start = node.get("start_line")
    where = f"{where}:{start}" if where and start else where
    return f"`{name}` ({where}) [{node_id}]" if where else f"`{name}` [{node_id}]"


def _int(value, fallback: int) -> int:
    """An MCP client's arguments are JSON a model wrote: coerce, never raise."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _clamp(value, fallback: int, low: int, high: int) -> int:
    """_int, then held inside [low, high]: no argument may cost unbounded time."""
    return max(low, min(_int(value, fallback), high))


def tool_cache_stats(cache) -> str:
    """Cache counters as JSON. Diagnostics only: no repository content."""
    import json as _json
    if cache is None:
        return _json.dumps({"enabled": False, "hits": 0, "misses": 0, "size": 0,
                            "max_size": 0, "ttl_s": 0}, indent=2)
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
    if tasks is None:
        return _json.dumps({
            "error": "this server builds synchronously; there are no build "
                     "tasks to report. Start it with --async-build to use "
                     "repo_build_status.",
        }, indent=2)
    task = tasks.get(str(task_id))
    if task is None:
        return _json.dumps({
            "task_id": task_id,
            "status": "unknown",
            "error": f"no build task with id {task_id!r}. Ids are issued by the "
                     f"tool call that starts a build and do not survive a "
                     f"server restart.",
        }, indent=2)
    return _json.dumps(task.snapshot(), indent=2)


def dispatch(index: "Index | None", name: str, arguments: dict, cache=None,
             tasks=None) -> str:
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
        on bad arguments: every numeric one is coerced and clamped.
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
        return ("no index is open, so this tool cannot answer. Use "
                "repo_build_status to check whether one is still being built.")
    if name == "repo_map":
        result = tool_repo_map(index)
    elif name == "repo_search":
        result = tool_repo_search(index, str(args.get("query") or ""),
                                  k=_int(args.get("k"), 8),
                                  hops=_int(args.get("hops"), 1),
                                  budget_tokens=args.get("budget_tokens"))
    elif name == "repo_neighbours":
        result = tool_repo_neighbours(index, str(args.get("node_id") or ""),
                                      hops=_int(args.get("hops"), 1),
                                      limit=_int(args.get("limit"),
                                                 MCP_NEIGHBOUR_LIMIT))
    else:
        # Not cached: an unknown-tool message is cheap, and caching it would
        # fill the cache with whatever names a confused caller invents.
        return f"unknown tool: {name!r}. Available: {', '.join(TOOL_DESCRIPTIONS)}."

    if key is not None:
        cache.put(key, result)
    return result


# -------------------------------------------------------------- server ----

MISSING_SDK = ('the MCP server needs the optional `mcp` extra: '
               'pip install "repo2graph[mcp]"')

# The SDK range serve() is written against, and the API it needs from it.
# serve() uses the 1.x decorator API (`@server.list_tools()` /
# `@server.call_tool()`); mcp 2.x removed both methods from `Server`, so a 2.x
# install *imports* perfectly and then dies mid-serve() with
# `AttributeError: 'Server' object has no attribute 'list_tools'`. A successful
# `import mcp` is therefore not evidence the SDK is usable -- the guard has to
# look at the API surface, or the user gets exactly the traceback it exists to
# prevent, one SDK major later.
SDK_SPEC = "mcp>=1.0,<2"
REQUIRED_SERVER_API = ("list_tools", "call_tool")


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


def _unusable_sdk(version: str, detail: str) -> str:
    return (f"the installed mcp SDK ({version}) is not supported by "
            f"repo2graph-mcp: {detail}. Install a 1.x SDK instead: "
            f'pip install "{SDK_SPEC}" '
            '(or `pip install "repo2graph[mcp]"` in a clean environment).')


def _require_sdk():
    """Turn a missing *or unusable* optional dependency into an instruction.

    Two distinct failures, both of which must end in a sentence a user can act
    on rather than a traceback: the SDK is absent, or the SDK is present but
    speaks an API serve() cannot drive.
    """
    try:
        import mcp
    except ImportError:
        raise SystemExit(MISSING_SDK) from None
    if mcp is None:
        raise SystemExit(MISSING_SDK)
    try:
        from mcp.server import Server
    except ImportError as exc:
        raise SystemExit(_unusable_sdk(
            _sdk_version(mcp), f"`from mcp.server import Server` failed ({exc})")) from None
    missing = [name for name in REQUIRED_SERVER_API if not hasattr(Server, name)]
    if missing:
        raise SystemExit(_unusable_sdk(
            _sdk_version(mcp),
            "its Server has no " + "/".join(missing)
            + " decorator (removed in mcp 2.x)"))
    return mcp


def get_tools(types_module=None):
    """Construct Tool instances with descriptions, schemas, and annotations."""
    if types_module is None:
        try:
            import mcp.types as types_module
        except ImportError:
            return []
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
            ann = dict(TOOL_ANNOTATIONS)
            if name in TOOL_TITLES:
                ann["title"] = TOOL_TITLES[name]
            if tool_ann_cls is not None:
                try:
                    kwargs["annotations"] = tool_ann_cls(**ann)
                except Exception:
                    kwargs["annotations"] = ann
            else:
                kwargs["annotations"] = ann
        tools.append(tool_cls(**kwargs))
    return tools


def serve(out, repo=None, cache=None, tasks=None) -> None:
    """Run the stdio MCP server against the index at `out`.

    Deliberately thin: every answer comes from dispatch(), which is tested
    without the SDK, so SDK API drift can break the wiring but nothing else.

    When `repo` is given and no index exists yet, the build happens on the first
    tool call rather than here, and that placement is the whole point. A client
    spawns this process and waits for the `initialize` response; blocking that
    handshake for the minute a large repo takes to parse makes the server look
    dead and the client gives up. Building on first call instead means the
    handshake is instant, the tools list, and the one slow call writes a real
    index to disk -- so even if *that* call times out, the work is not lost and
    the retry is instant. A failure that heals itself beats one that does not.
    """
    mcp = _require_sdk()
    index_dir = Path(out)
    if repo is None:
        # No repo to build from: the index must already exist, so say so now
        # rather than at the first call.
        open_index(index_dir)
    import asyncio

    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent

    # version= is not optional in practice. Left unset, the SDK fills serverInfo
    # with *its own* version, so every client is told repo2graph is whatever
    # release of `mcp` happens to be installed -- 1.30.0 against a 1.4.0 package.
    # The HTTP transport reports __version__ correctly, so omitting it here also
    # made the two transports disagree about what they are.
    server = Server("repo2graph", version=__version__)

    @server.list_tools()
    async def list_tools():
        return get_tools(mcp.types)

    @server.call_tool()
    async def call_tool(name, arguments):
        if (name or "") == "repo_build_status":
            # Answerable without an index, and the only tool that is: asking
            # for build progress must not itself wait on the build.
            return [TextContent(type="text",
                                text=dispatch(None, name, arguments or {},
                                              cache=cache, tasks=tasks))]
        index, pending = open_index_or_task(index_dir, repo, cache, tasks)
        if pending is not None:
            return [TextContent(type="text", text=pending)]
        text = dispatch(index, name, arguments or {}, cache=cache, tasks=tasks)
        return [TextContent(type="text", text=text)]

    async def _run():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream,
                             server.create_initialization_options())

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
                f"error: repository directory does not exist or is not a "
                f"directory: {repo_path}")
        return (Path(out) if out else repo_path / INDEX_DIRNAME), repo_path
    out_path = Path(out) if out else Path(INDEX_DIRNAME)
    if out_path.name == INDEX_DIRNAME and out_path.parent.is_dir():
        return out_path, out_path.parent
    return out_path, None


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="repo2graph-mcp",
        description="Serve a repo2graph index over MCP on stdio")
    p.add_argument("repo", nargs="?", default=None,
                   help="repository to serve; its index is built on the first "
                        "tool call if one does not exist yet "
                        "(default: the directory holding --out)")
    p.add_argument("-o", "--out", default=None,
                   help="index directory (default: <repo>/.r2g)")
    p.add_argument("--no-auto-build", action="store_true",
                   help="never build: exit unless the index already exists")
    p.add_argument("--async-build", action="store_true",
                   help="build a missing index on a background thread and "
                        "return a task_id immediately, instead of blocking the "
                        "first tool call until it finishes. Poll it with "
                        "repo_build_status (default: off, build synchronously)")
    p.add_argument("--cache-size", type=int, default=DEFAULT_MAX_SIZE,
                   metavar="N",
                   help=f"cached tool results before the least recently used "
                        f"is evicted; 0 disables the cache "
                        f"(default: {DEFAULT_MAX_SIZE})")
    p.add_argument("--cache-ttl", type=float, default=DEFAULT_TTL,
                   metavar="SECONDS",
                   help=f"seconds a cached result is served before it is "
                        f"recomputed (default: {DEFAULT_TTL:g})")
    _add_auth_args(p)
    args = p.parse_args(argv)
    index_dir, repo = resolve_paths(args.repo, args.out)
    cache = ResultCache(max_size=args.cache_size, ttl=args.cache_ttl)
    build_from = None if args.no_auto_build else repo
    # --well-known-port is the spelling the discovery spec uses; it and
    # --http-port name the same HTTP transport, since serving the metadata
    # document from a second server would be two ports for one job.
    if args.http_port is None and args.well_known_port is not None:
        args.http_port = args.well_known_port

    from .audit import AuditConfig, AuditLogger
    audit = AuditLogger(AuditConfig(level=args.audit_log_level,
                                    path=args.audit_log))

    tasks = None
    if args.async_build:
        from .tasks import TaskManager
        tasks = TaskManager()

    auth_config = _auth_config(args)
    transport = None
    if args.http_port is not None or args.auth_cimd:
        from .http_server import HTTPTransport
        transport = HTTPTransport(
            index_dir, build_from, host=args.http_host,
            port=args.http_port if args.http_port is not None else 8719,
            auth_config=auth_config, audit=audit, cache=cache,
            publish_cimd=args.auth_cimd, tasks=tasks)
        transport.start()
        if args.http_only:
            # No stdio peer: block on the HTTP thread instead of returning,
            # which would tear the daemon thread down on the way out.
            try:
                thread = transport._thread
                if thread is not None:
                    thread.join()
            except KeyboardInterrupt:
                pass
            finally:
                transport.stop()
            return 0
    elif auth_config.enabled:
        # Credentials with nowhere to be presented. Refusing beats starting a
        # server the operator believes is protected and is not: stdio has no
        # headers, so every one of these flags would be inert.
        raise SystemExit(
            "error: --auth-token/--auth-oidc-issuer need a transport that "
            "carries headers. stdio has none, so the credential could never be "
            "checked. Add --http-port to serve over HTTP as well.")

    try:
        serve(index_dir, build_from, cache=cache, tasks=tasks)
    finally:
        if transport is not None:
            transport.stop()
        audit.close()
    return 0


def _add_auth_args(p) -> None:
    """Register the HTTP-transport, authentication and audit flags."""
    http = p.add_argument_group(
        "http transport",
        "Serve MCP over HTTP as well as stdio. Required for authentication: "
        "stdio carries no headers, so a bearer token has nowhere to travel.")
    http.add_argument("--http-port", type=int, default=None, metavar="PORT",
                      help="serve JSON-RPC on this port in addition to stdio "
                           "(default: off)")
    http.add_argument("--http-host", default="127.0.0.1", metavar="HOST",
                      help="bind address for --http-port. Binding beyond "
                           "loopback without authentication is refused "
                           "(default: 127.0.0.1)")
    http.add_argument("--http-only", action="store_true",
                      help="serve HTTP only, without the stdio transport "
                           "(default: off)")
    http.add_argument("--well-known-port", type=int, default=None, metavar="PORT",
                      help="alias for --http-port; the discovery documents are "
                           "served by the same HTTP transport (default: off)")

    auth = p.add_argument_group("authentication")
    auth.add_argument("--auth-token", default=None, metavar="TOKEN",
                      help="require `Authorization: Bearer <TOKEN>` on every "
                           "HTTP tool call (default: no authentication)")
    auth.add_argument("--auth-oidc-issuer", default=None, metavar="URL",
                      help="validate bearer tokens as JWTs against this OIDC "
                           "issuer's JWKS, enforcing iss, aud and exp "
                           "(default: off)")
    auth.add_argument("--auth-audience", default=None, metavar="AUD",
                      help="expected `aud` claim for --auth-oidc-issuer tokens "
                           "(default: the claim is not checked)")
    auth.add_argument("--auth-jwks-ttl", type=float, default=300.0,
                      metavar="SECONDS",
                      help="seconds a fetched JWKS is trusted before refetch "
                           "(default: 300)")
    auth.add_argument("--auth-cimd", action="store_true",
                      help="publish an RFC 7591 client metadata document at "
                           "/.well-known/oauth-client-metadata (default: off)")

    log = p.add_argument_group("audit logging")
    log.add_argument("--audit-log", default=None, metavar="PATH",
                     help="append audit records to this file as well as stderr "
                          "(default: stderr only)")
    log.add_argument("--audit-log-level", choices=("none", "errors", "all"),
                     default="all",
                     help="which tool calls produce an audit record "
                          "(default: all)")


def _auth_config(args):
    """Build an AuthConfig from parsed arguments."""
    from .auth import AuthConfig
    return AuthConfig(token=args.auth_token,
                      oidc_issuer=args.auth_oidc_issuer,
                      audience=args.auth_audience,
                      jwks_ttl=args.auth_jwks_ttl,
                      cimd=args.auth_cimd)


if __name__ == "__main__":
    sys.exit(main())
