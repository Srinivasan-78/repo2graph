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
    MCP_MAX_PAGED_NEIGHBOURS,
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
from .indexes import SECRET_RULES
from .nodes import _edge_note, _label, _staleness_note
from .pagination import (
    CURSOR_RESERVE_TOKENS,
    CursorError,
    args_digest,
    decode_cursor,
    encode_cursor,
    index_identity,
    with_next_cursor,
)
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
    cursor: Any = None,
) -> str:
    """Cited markdown for `query`, bounded by budget_tokens.

    A `cursor` (even "") opts into paging: each page holds the next `k` seeds
    and, while more remain, ends with a `next_cursor:` line. None is unpaged.
    """
    query = _str(query, MCP_MAX_QUERY_CHARS)
    if not query.strip():
        return ToolError(
            "repo_search needs a non-empty `query`: a question, search terms or a "
            "symbol name (e.g. 'pack_context' or 'how does export work')."
        )
    paged = cursor is not None
    offset = 0
    digest = identity = ""
    if paged:
        digest = args_digest(
            "repo_search", {"query": query, "hops": _clamp(hops, 1, 0, MCP_MAX_HOPS)}
        )
        identity = index_identity(index)
        try:
            offset = decode_cursor(cursor, "repo_search", digest, identity)
        except CursorError as exc:
            return ToolError(str(exc))
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
    if paged:
        # The continuation line is part of the page, so it is paid for out of
        # the same budget rather than appended on top of it.
        room = max(1, room - CURSOR_RESERVE_TOKENS)
    nbr_mode = _str(neighbours, 10).lower()
    if nbr_mode not in ("full", "cite"):
        nbr_mode = "full"
    max_nbrs = (
        _clamp(max_neighbours, 10, 0, MCP_MAX_SEARCH_NEIGHBOURS)
        if max_neighbours is not None
        else None
    )
    # Passed only when paging, so the unpaged call stays exactly as it was.
    paging: dict[str, Any] = {"seed_offset": offset} if paged else {}
    pack = index.pack_context(
        query,
        k=_clamp(k, 8, 1, MCP_MAX_K),
        hops=_clamp(hops, 1, 0, MCP_MAX_HOPS),
        budget_tokens=room,
        exclude_secrets=True,
        extra_secret_keywords=SECRET_RULES["keywords"] or None,
        extra_secret_dirs=SECRET_RULES["dirs"] or None,
        neighbours=nbr_mode,
        max_neighbours=max_nbrs,
        **paging,
    )
    text: str = str(pack.get("markdown") or "")
    if count_tokens(text) > room:
        text = _fit_lines(text, room, count_tokens)
    if not text.strip():
        text = EMPTY_RESULT.format(budget=budget, ceiling=MCP_MAX_BUDGET_TOKENS)
    if not paged:
        return note + text
    if pack.get("skipped_seeds"):
        note += (
            "_note: a result larger than this page's budget was skipped; "
            "raise budget_tokens to see it._\n\n"
        )
    next_offset = pack.get("next_seed_offset")
    next_cursor = (
        encode_cursor("repo_search", digest, next_offset, identity)
        if next_offset is not None
        else None
    )
    return with_next_cursor(note + text, next_cursor)


def _min_confidence(value: Any) -> float:
    """A 0..1 confidence floor from a client argument; anything else means 0.0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    conf = float(value)
    return min(max(conf, 0.0), 1.0) if conf == conf else 0.0


def tool_repo_neighbours(
    index: Index,
    node_id: str,
    hops: Any = 1,
    limit: Any = MCP_NEIGHBOUR_LIMIT,
    min_confidence: Any = 0.0,
    cursor: Any = None,
) -> str:
    """Graph traversal from `node_id`, then the TESTS edges on `node_id` itself.

    A `cursor` (even "") opts into paging: `limit` rows per page, with a
    `next_cursor:` line while more remain. None is the unpaged answer.
    """
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
    floor = _min_confidence(min_confidence)
    if cursor is not None:
        return _paged_neighbours(
            index, node_id, _clamp(hops, 1, 0, MCP_MAX_HOPS), limit, cursor, note
        )
    lines = [f"neighbours of {_label(index, node_id)}:"]
    truncated = False
    for dst, etype, direction, src in index.expand(
        [node_id],
        hops=_clamp(hops, 1, 0, MCP_MAX_HOPS),
        edge_types=frozenset(DEFAULT_EDGE_TYPES | {"CONTAINS", "CO_CHANGE"}),
        edge_dirs=ALL_EDGE_DIRS,
        min_confidence=floor,
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
    # TESTS is listed from the node's own adjacency rather than followed by
    # `expand`: expansion visits each neighbour once, so a test that also
    # calls the symbol directly would surface only as `CALLS in`, and the
    # per-hop cap would hide the rest behind the structural edges listed first.
    if not truncated:
        for dst, direction, conf in _tests_of(index, node_id, floor):
            if index._is_secret_path(index.nodes.get(dst, {}).get("path") or ""):
                continue
            if len(lines) - 1 >= limit:
                truncated = True
                break
            lines.append(
                f"- TESTS {direction}: {_label(index, dst)}"
                f"{_edge_note(index, node_id, dst, 'TESTS')}"
            )
    if truncated:
        lines.append(f"... (truncated at {limit} neighbours)")
    if len(lines) == 1:
        lines.append("- (none)")
    return note + "\n".join(lines)


def _tests_of(index: Index, node_id: str, floor: float) -> list[tuple[str, str, float]]:
    """`node_id`'s TESTS edges at or above `floor`, most confident first."""
    rows = []
    for dst, etype, direction, edge in index.adj.get(node_id, ()):
        if etype != "TESTS":
            continue
        try:
            conf = float(edge.get("confidence", 1.0))
        except (TypeError, ValueError):
            conf = 0.0
        if conf >= floor:
            rows.append((dst, direction, conf))
    rows.sort(key=lambda r: (-r[2], r[0]))
    return rows


def _paged_neighbours(
    index: Index, node_id: str, hops: int, limit: int, cursor: Any, note: str
) -> str:
    """One page of `repo_neighbours`: rows [offset, offset + limit) of the full walk.

    The unpaged answer samples at most six edges per node per hop, which is
    why a symbol with sixty callers showed six. A page has to be a window on
    one complete, deterministic order or later pages could not reach the rest,
    so this walks every edge of each hop, bounded in total instead.
    """
    digest = args_digest("repo_neighbours", {"node_id": node_id, "hops": hops})
    identity = index_identity(index)
    try:
        offset = decode_cursor(cursor, "repo_neighbours", digest, identity)
    except CursorError as exc:
        return ToolError(str(exc))
    walk = index.expand(
        [node_id],
        hops=hops,
        edge_types=frozenset(DEFAULT_EDGE_TYPES | {"CONTAINS", "CO_CHANGE"}),
        edge_dirs=ALL_EDGE_DIRS,
        min_confidence=0.0,
        per_hop=MCP_MAX_PAGED_NEIGHBOURS,
        max_results=MCP_MAX_PAGED_NEIGHBOURS + 1,
    )
    capped = len(walk) > MCP_MAX_PAGED_NEIGHBOURS
    rows = [
        row
        for row in walk[:MCP_MAX_PAGED_NEIGHBOURS]
        if not index._is_secret_path(index.nodes.get(row[0], {}).get("path") or "")
    ]
    # Rows stop at `limit` or at the token ceiling, whichever comes first, so
    # paging never yields a response larger than an unpaged one may be. The
    # header, the cap note and the cursor line are paid for up front.
    label = _label(index, node_id)
    room = MCP_MAX_BUDGET_TOKENS - CURSOR_RESERVE_TOKENS - count_tokens(note) - 2
    room -= count_tokens(f"neighbours of {label} (rows {offset + 1}-{offset + limit}):")
    room -= count_tokens(f"... (paging stops at {MCP_MAX_PAGED_NEIGHBOURS} neighbours)")
    body: list[str] = []
    end = offset
    for dst, etype, direction, src in rows[offset:]:
        if len(body) >= limit:
            break
        line = f"- {etype} {direction}: {_label(index, dst)}{_edge_note(index, src, dst, etype)}"
        if body and count_tokens("\n".join([*body, line])) > room:
            break
        body.append(line)
        end += 1
    header = (
        f"neighbours of {label} (rows {offset + 1}-{end}):" if body else f"neighbours of {label}:"
    )
    lines = [header, *body] if body else [header, "- (none)"]
    if capped and end >= len(rows):
        lines.append(f"... (paging stops at {MCP_MAX_PAGED_NEIGHBOURS} neighbours)")
    next_cursor = (
        encode_cursor("repo_neighbours", digest, end, identity) if end < len(rows) else None
    )
    return with_next_cursor(note + "\n".join(lines), next_cursor)


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
