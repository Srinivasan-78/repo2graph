"""MCP retrieval tools: repo map, search, neighbours, symbol lookup, and read."""

from __future__ import annotations

import json
import re
import weakref
from collections import defaultdict
from typing import Any

from ..query import (
    ALL_EDGE_DIRS,
    DEFAULT_EDGE_TYPES,
    QUALNAME_SEP_RE,
    Index,
    _fit_lines,
    _header_len,
    count_tokens,
)
from .guardrails import (
    BUDGET_DEFAULTED,
    EMPTY_RESULT,
    MCP_BUDGET_TOKENS,
    MCP_FIND_LIMIT,
    MCP_MAX_BUDGET_TOKENS,
    MCP_MAX_FIND_LIMIT,
    MCP_MAX_HOPS,
    MCP_MAX_K,
    MCP_MAX_NEIGHBOURS,
    MCP_MAX_NODE_ID_CHARS,
    MCP_MAX_QUERY_CHARS,
    MCP_MAX_READ_CHARS,
    MCP_MAX_READ_CONTEXT,
    MCP_MAX_SEARCH_NEIGHBOURS,
    MCP_NEIGHBOUR_LIMIT,
    MCP_READ_CONTEXT,
    _clamp,
    _defaulted_notes,
    _int,
    _str,
)
from .nodes import _edge_note, _label, _staleness_note
from .schemas import ToolError


def tool_repo_map(index: Index) -> str:
    """The repo map, plus a staleness note when the working tree has moved."""
    return _staleness_note(index) + index.map_prepend()


def tool_repo_search(
    index: Index,
    query: str,
    k: Any = 8,
    hops: Any = 1,
    budget_tokens: Any = None,
    neighbours: Any = "full",
    max_neighbours: Any = None,
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
    nbr_mode = _str(neighbours, 10).lower()
    if nbr_mode not in ("full", "cite"):
        nbr_mode = "full"
    max_nbrs = (
        _clamp(max_neighbours, 10, 0, MCP_MAX_SEARCH_NEIGHBOURS)
        if max_neighbours is not None
        else None
    )
    pack = index.pack_context(
        query,
        k=_clamp(k, 8, 1, MCP_MAX_K),
        hops=_clamp(hops, 1, 0, MCP_MAX_HOPS),
        budget_tokens=room,
        exclude_secrets=True,
        neighbours=nbr_mode,
        max_neighbours=max_nbrs,
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
    # Count the generated header by its own shape rather than deriving it from
    # the citation range. `len(lines) - (end - start + 1)` was off by one for
    # every non-final part of a split chunk: such a part ends with the newline
    # of its last body line, so `split("\n")` yields a trailing "" that is not
    # a body line, and the window started one line late -- `repo_read` served
    # line 2 onward under a citation that said line 1, dropping the `def`.
    # The range is not a reliable line count either: an over-long line is cut
    # into several parts that all sit on one source line, and redaction can
    # change the count. Only the header's shape is dependable, which is why
    # `query._excerpt_record` reads it the same way.
    header_len = _header_len(lines)
    body = lines[header_len:]
    if body and body[-1] == "":
        body.pop()
    if not body:
        return None
    return body


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

    # Serve-time redaction, matching what `pack_context`/`retrieve` get from
    # `Index._served`. `repo_read` sliced `index.chunks` straight back to the
    # caller, so an index built with `--secret-policy off` or `warn-only` --
    # whose stored chunk text is deliberately unredacted -- served credentials
    # in clear here while `repo_search` over the very same bytes redacted them.
    # docs/mcp.md claimed without qualification that chunks "have already passed
    # secret-path exclusion and content redaction": true of the search path,
    # false of this one.
    #
    # Applied to the assembled slice rather than inside `_aux` so only the lines
    # actually served are scanned, and so building the path cache stays free of
    # both the work and the manifest read. The manifest lookup is guarded
    # because `repo_read` is required to answer without touching the
    # filesystem; if the policy cannot be determined, redact rather than guess.
    try:
        policy = index.manifest.get("secret_filter_policy")
    except Exception:  # noqa: BLE001 - unreadable manifest must fail safe, not open
        policy = None
    if policy not in ("redact-match", "exclude-file"):
        from ..security import redact_content

        text, _ = redact_content(text)

    if len(text) > MCP_MAX_READ_CHARS:
        text = _fit_lines(text, MCP_MAX_READ_CHARS, len)
        note += (
            f"_note: result exceeded the {MCP_MAX_READ_CHARS}-character tool "
            f"ceiling; truncated at a line boundary. Narrow the range._\n\n"
        )

    return note + f"### [cite: {norm}:{want_start}-{hi}]\n\n{text}"
