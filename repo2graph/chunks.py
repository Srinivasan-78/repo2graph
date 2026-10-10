"""Turn graph nodes into retrieval chunks: code text + graph context header."""

from collections import Counter, defaultdict
from collections.abc import Iterator
from typing import Any

MAX_CHARS = 4000
OVERLAP_LINES = 8

# Context caps for headers
MAX_CALLERS = 12
MAX_CALLEES = 12
MAX_EXT_CALLS = 12
MAX_BASES = 6
MAX_IMPORTS = 20
MAX_DEFINES = 40


def _process_chunk_content(
    text: str, nid: str, path: str, policy: str, g: Any
) -> tuple[str | None, int]:
    """Apply secret scanning and redaction policy to chunk text."""
    from .security import count_hidden_unicode

    hidden = count_hidden_unicode(text)
    if hidden and hasattr(g, "stats"):
        g.stats["hidden_unicode_chars"] += hidden
    if policy == "exclude-file":
        from .security import scan_content_secrets

        if scan_content_secrets(text):
            if hasattr(g, "stats"):
                g.stats["skipped_secret_chunks"] += 1
            return None, 0
        return text, 0
    elif policy == "redact-match":
        from .security import redact_content

        redacted, r_count = redact_content(text, policy=policy)
        if r_count > 0 and hasattr(g, "stats"):
            g.stats["redacted_secret_chunks"] += r_count
        return redacted, r_count
    elif policy == "warn-only":
        from .events import emit
        from .security import scan_content_secrets

        findings = scan_content_secrets(text)
        if findings:
            emit(
                "secret_detected_in_chunk",
                level="warning",
                node_id=nid,
                path=path,
                findings=[f[0] for f in findings],
            )
        return text, 0
    return text, 0


def _lines(src: str) -> list[str]:
    """Split source the way tree-sitter counts rows: on "\\n" only.

    str.splitlines() also breaks on U+2028/U+2029/U+0085/\\x0b/\\x0c, which
    tree-sitter's row numbers do not; using it here slices symbol chunks
    from incorrect lines. Drop a trailing "\\r" per line so
    CRLF files still index cleanly.
    """
    return [ln[:-1] if ln.endswith("\r") else ln for ln in src.split("\n")]


def _keepends_lf(text: str) -> list[str]:
    """Split on "\\n" only, keeping the newline, so "".join(result) == text.

    str.splitlines(keepends=True) also breaks on U+2028/U+2029/U+0085/\\x0b/\\x0c,
    which tree-sitter does not treat as row breaks; splitting a chunk there makes
    its pieces depend on whichever stray separators the source happens to hold.
    Same "\\n"-only rule as _lines, but lossless.
    """
    parts = text.split("\n")
    lines = [p + "\n" for p in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _split(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    """The parts of `_split_spans`, without their line offsets."""
    return [part for part, _lo, _hi in _split_spans(text, max_chars)]


def _split_spans(text: str, max_chars: int = MAX_CHARS) -> list[tuple[str, int, int]]:
    """Split on line boundaries, each part with the lines it covers.

    Returns `(part, first, last)` per part, where `first` and `last` are
    inclusive 0-based offsets into the *body's* lines. Callers turn those into
    source line numbers, because only they know where the body started.

    Every part used to be emitted carrying its parent's whole range, so a
    243-line function became three chunks all citing `encoders.py:102-344`,
    two of which begin mid-body with no `def` line in them. `[cite: ...]` is
    how an agent goes and checks an answer, so a range that does not describe
    the text beside it is worse than no citation.

    Two details make the offsets less obvious than counting newlines:
    parts deliberately overlap by `OVERLAP_LINES`, so their spans overlap too;
    and a single line longer than `max_chars` is cut into several parts that
    all sit on that one source line.
    """
    if len(text) <= max_chars:
        lines = _keepends_lf(text)
        return [(text, 0, max(0, len(lines) - 1))]
    lines = _keepends_lf(text)
    # A single line longer than max_chars (minified JS/CSS, a long SVG
    # path, base64, one-line JSON, ...) can't be shrunk by grouping on line
    # boundaries alone -- the packer below would emit it whole, unbounded.
    # Break any such line into max_chars-sized pieces first so every entry the
    # packer sees is already within budget; "".join(pieces) still == the
    # original line, so no text is lost or reordered.
    # `owner[k]` is the body line that piece `k` belongs to. Without it, a
    # minified line cut into ten pieces would look like ten source lines.
    owner: list[int] = list(range(len(lines)))
    if any(len(ln) > max_chars for ln in lines):
        bounded: list[str] = []
        owner = []
        for idx, ln in enumerate(lines):
            if len(ln) > max_chars:
                for j in range(0, len(ln), max_chars):
                    bounded.append(ln[j : j + max_chars])
                    owner.append(idx)
            else:
                bounded.append(ln)
                owner.append(idx)
        lines = bounded
    out: list[tuple[str, int, int]] = []
    buf: list[str] = []
    size = 0
    i = 0
    while i < len(lines):
        buf, size = [], 0
        start = i
        while i < len(lines) and size < max_chars:
            buf.append(lines[i])
            size += len(lines[i])
            i += 1
        if i < len(lines):
            # Prefer to end the part at a logical block boundary -- the last
            # blank line in the final quarter of the window -- over cutting
            # wherever the ceiling fell, so a part does not end one line into
            # the next statement group (#287).
            floor = start + max(1, (i - start) * 3 // 4)
            for j in range(i - 1, floor - 1, -1):
                if not lines[j].strip():
                    i = j + 1
                    buf = lines[start:i]
                    break
        out.append(("".join(buf), owner[start], owner[i - 1]))
        if i < len(lines):
            i = max(start + 1, i - OVERLAP_LINES)
    return out


def _conf(text: str, edge: dict[str, Any]) -> str:
    """Label a CALLS edge with its confidence when the call was ambiguous."""
    c = edge.get("confidence", 1.0)
    return text if c >= 1.0 else f"{text} (confidence {c})"


def _neighbour_edge(target: str, edge: dict[str, Any], direction: str) -> dict[str, Any]:
    """Structured form of a callers/callees/bases entry.

    Additive sibling of the callers/callees/callees_external string lists --
    same targets, plus the edge type, direction and (only when < 1.0, to keep
    the common case small) confidence. IMPORTS/DEFINES/INHERITS edges carry no
    confidence key, so they fall through the 1.0 default and are never flagged.
    """
    d = {"target": target, "edge_type": edge["type"], "edge_direction": direction}
    c = edge.get("confidence", 1.0)
    if c < 1.0:
        d["confidence"] = c
    return d


def build_chunks(g: Any, include_files: bool = True) -> list[dict[str, Any]]:
    """All retrieval chunks as a list (stable public API)."""
    return list(iter_chunks(g, include_files))


def _decode_source(raw: bytes) -> str:
    """Decode source bytes into string, respecting UTF BOMs (UTF-8, UTF-16, UTF-32) (W10)."""
    if raw.startswith(
        (b"\xef\xbb\xbf", b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff", b"\xff\xfe", b"\xfe\xff")
    ):
        for enc in ("utf-8-sig", "utf-16", "utf-32"):
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
    return raw.decode("utf-8", "replace")


def iter_chunks(
    g: Any, include_files: bool = True, max_chunks: int = 0
) -> Iterator[dict[str, Any]]:
    """Yield chunk dicts ready for embedding, one at a time.

    A generator, not a list: on a large repo the chunk text is the single
    biggest allocation, so write_jsonl streams it straight to disk instead of
    holding every chunk in memory at once. Source text is dropped from the cache
    as soon as the last symbol on a file has been emitted.
    """
    if max_chunks == 0 and hasattr(g, "config") and getattr(g.config, "max_chunks", 0) > 0:
        max_chunks = g.config.max_chunks
    yielded = 0
    out_edges, in_edges = defaultdict(list), defaultdict(list)
    for e in g.edges:
        out_edges[e["src"]].append(e)
        in_edges[e["dst"]].append(e)

    # Content redaction is governed by --secret-policy alone. --include-secrets
    # only lifts the secret-PATH refusal (a .env is indexed); it must never turn
    # off scanning of the text that ends up in chunks.jsonl and agent replies.
    policy = getattr(getattr(g, "config", None), "secret_policy", "redact-match")

    src_cache: dict[str, str] = {}

    def source_of(path: str) -> str:
        if path not in src_cache:
            try:
                raw = (g.root / path).read_bytes()
                if path.lower().endswith(".ipynb"):
                    # The code cells at their own lines, not the JSON around
                    # them (#398): outputs can be megabytes of base64 images.
                    from .parse import notebook_code

                    raw = notebook_code(raw)
                src_cache[path] = _decode_source(raw)
            except OSError:
                src_cache[path] = ""
        return src_cache[path]

    def label(nid: str) -> str:
        n = g.nodes.get(nid)
        if not n:
            return nid
        if n["type"] == "symbol":
            # The id minus its `sym:` prefix -- `path::qualname` for every
            # first definition, `path::qualname@L<line>` for a later duplicate,
            # so a label always round-trips to exactly one node.
            return nid.removeprefix("sym:")
        return n.get("path") or n.get("name") or nid

    covered: dict[str, list[tuple[int, int]]] = defaultdict(list)
    # remaining symbol chunks per file, so its source can leave the cache once
    # done (order-independent — does not rely on nodes being grouped by file)
    pending = Counter(n["path"] for n in g.nodes.values() if n["type"] == "symbol")

    for nid, n in g.nodes.items():
        if n["type"] != "symbol":
            continue
        src = source_of(n["path"])
        lines = _lines(src)
        body = "\n".join(lines[n["start_line"] - 1 : n["end_line"]])
        covered[n["path"]].append((n["start_line"], n["end_line"]))
        call_out = [e for e in out_edges[nid] if e["type"] == "CALLS"][:MAX_CALLEES]
        call_in = [e for e in in_edges[nid] if e["type"] == "CALLS"][:MAX_CALLERS]
        callees = [label(e["dst"]) for e in call_out]
        callers = [label(e["src"]) for e in call_in]
        ext = [
            g.nodes.get(e["dst"], {}).get("name", e["dst"])
            for e in out_edges[nid]
            if e["type"] == "CALLS_EXTERNAL"
        ][:MAX_EXT_CALLS]
        base_out = [e for e in out_edges[nid] if e["type"] == "INHERITS"][:MAX_BASES]
        bases = [label(e["dst"]) for e in base_out]
        # a call to an overloaded name fans out to every candidate at 1/n
        # confidence; say so in the header, or a reader follows the wrong edge
        # believing it is the only one.
        # strict=True: callees/callers are built 1:1 from call_out/call_in just
        # above, so a length mismatch is a bug, not something to silently truncate.
        out_conf = [_conf(t, e) for t, e in zip(callees, call_out, strict=True)]
        in_conf = [_conf(t, e) for t, e in zip(callers, call_in, strict=True)]
        callee_edges = [
            _neighbour_edge(t, e, "outbound") for t, e in zip(callees, call_out, strict=True)
        ]
        caller_edges = [
            _neighbour_edge(t, e, "inbound") for t, e in zip(callers, call_in, strict=True)
        ]
        base_edges = [
            _neighbour_edge(t, e, "outbound") for t, e in zip(bases, base_out, strict=True)
        ]
        header = [
            f"# file: {n['path']}",
            f"# {n['kind']}: {n['qualname']}  (lines {n['start_line']}-{n['end_line']}, {n['lang']})",
        ]
        if n["path"].lower().endswith(".ipynb"):
            # A notebook is read by cell, not by line of JSON (#398).
            cell = next(
                (
                    ln
                    for ln in reversed(lines[: n["start_line"]])
                    if ln.startswith("# cell ") and ln[7:].isdigit()
                ),
                None,
            )
            if cell:
                header.append(f"# notebook {cell[2:]}")
        if n.get("entrypoint"):
            header.append("# entry point: nothing in this repo calls it — a flow starts here")
        if bases:
            header.append(f"# inherits: {', '.join(bases)}")
        if in_conf:
            header.append(f"# called by: {', '.join(in_conf)}")
        if out_conf:
            header.append(f"# calls: {', '.join(out_conf)}")
        if ext:
            header.append(f"# calls (outside the repo): {', '.join(ext)}")
        if n.get("docstring"):
            # Already redacted at graph-build time (`graph._redact_metadata`), so
            # this is the stored value and not a second, unscanned copy.
            header.append("# doc: " + n["docstring"].replace("\n", " ")[:300])
        # Scan and redact the whole body *before* splitting. Per-slice scanning
        # let any secret longer than the split escape: `_pem_spans` pairs a
        # BEGIN with the next END within the text it is handed, so a private key
        # straddling the 4,000-character boundary left the first slice matching
        # only its `-----BEGIN ...-----` header line, and the second slice -- the
        # base64 body plus the END line -- matching nothing at all. Under
        # `redact-match` the key shipped almost entirely in clear; under
        # `exclude-file` only the slice holding the BEGIN was dropped while the
        # body was kept. Redaction is line-preserving, so splitting afterwards
        # keeps every citation line number intact, and `exclude-file` now drops
        # the whole symbol instead of one arbitrary slice of it.
        proc_body, _ = _process_chunk_content(body, nid, n["path"], policy, g)
        body_spans = _split_spans(proc_body) if proc_body is not None else []
        for i, (part, lo, hi) in enumerate(body_spans):
            # The body starts at the symbol's first line, so a body offset maps
            # straight onto a source line. Clamp to the symbol's own end: a
            # redaction that changed the line count must not push a citation
            # past the definition it names.
            p_start = min(n["end_line"], n["start_line"] + lo)
            p_end = min(n["end_line"], n["start_line"] + hi)
            part_header = list(header)
            part_header[1] = (
                f"# {n['kind']}: {n['qualname']}  (lines {p_start}-{p_end}, {n['lang']})"
            )
            if max_chunks > 0 and yielded >= max_chunks:
                if getattr(g, "limit_policy", "warn") == "fail":
                    from .graph import GraphLimitExceeded

                    raise GraphLimitExceeded(
                        f"Chunk limit exceeded: reached {yielded} chunks (max_chunks={max_chunks}). "
                        "Use --max-chunks to increase the limit."
                    )
                if hasattr(g, "limits_hit"):
                    g.limits_hit["chunks_dropped"] = g.limits_hit.get("chunks_dropped", 0) + 1
                    if hasattr(g, "_note_limit"):
                        g._note_limit(
                            "chunks",
                            f"repo2graph: warning: chunk ceiling reached at {max_chunks} chunks; "
                            f"further chunks are dropped and the graph is partial",
                        )
                return
            yielded += 1
            yield {
                "id": f"{nid}#{i}" if i else nid,
                "node_id": nid,
                "type": "symbol",
                "kind": n["kind"],
                "path": n["path"],
                "lang": n["lang"],
                "name": n["name"],
                "qualname": n["qualname"],
                "start_line": p_start,
                "end_line": p_end,
                "entrypoint": bool(n.get("entrypoint")),
                "callers": callers,
                "callees": callees,
                "callees_external": ext,
                "caller_edges": caller_edges,
                "callee_edges": callee_edges,
                "base_edges": base_edges,
                "text": "\n".join(part_header) + "\n" + part,
                # Only on a symbol too large for one chunk, so `explain` and a
                # reader can tell part 2 of 3 from a whole definition (#287).
                **(
                    {"split": {"part": i + 1, "of": len(body_spans), "by": "size"}}
                    if len(body_spans) > 1
                    else {}
                ),
            }
        pending[n["path"]] -= 1
        if pending[n["path"]] <= 0:
            src_cache.pop(n["path"], None)  # file pass re-reads only if it needs to

    if not include_files:
        return

    for nid, n in g.nodes.items():
        if n["type"] != "file":
            continue
        src = source_of(n["path"])
        src_cache.pop(n["path"], None)  # nothing else reads this file's text
        if not src.strip():
            continue
        spans = sorted(covered.get(n["path"], []))
        lines = _lines(src)
        if spans:
            keep, cur = [], 1
            line_indices: list[int] = []
            for s, e in spans:
                if s > cur:
                    keep += lines[cur - 1 : s - 1]
                    line_indices.extend(range(cur, s))
                cur = max(cur, e + 1)
            if cur <= len(lines):
                keep += lines[cur - 1 :]
                line_indices.extend(range(cur, len(lines) + 1))
            joined = "\n".join(keep)
            body = joined.strip()
            if len(body) < 40:
                continue
            label_kind = "file_residual"
            # A residual is the file with its symbols cut out, so its lines are
            # not contiguous and `line_indices` is the only way back to source
            # line numbers. `.strip()` above drops leading blank lines, which
            # shifts that correspondence by however many it removed.
            lead = joined[: len(joined) - len(joined.lstrip())].count("\n")
            line_map = line_indices[lead:]
            span_start = line_map[0] if line_map else None
            span_end = line_map[-1] if line_map else None
        else:
            body, label_kind = src, "file"
            span_start, span_end = 1, n.get("lines", 0)
            line_map = list(range(1, body.count("\n") + 2))
        imports = [e.get("target", "") for e in out_edges[nid] if e["type"] == "IMPORTS"][
            :MAX_IMPORTS
        ]
        defines = [
            g.nodes.get(e["dst"], {}).get("qualname", e["dst"])
            for e in out_edges[nid]
            if e["type"] == "DEFINES"
        ][:MAX_DEFINES]
        header = [f"# file: {n['path']} ({n.get('lang')}, {n.get('lines')} lines)"]
        if imports:
            header.append(f"# imports: {', '.join(i for i in imports if i)}")
        if defines:
            header.append(f"# defines: {', '.join(defines)}")
        # Whole-body scan before the split, for the same reason as the symbol
        # pass above: a secret longer than one slice escaped detection entirely.
        proc_body, _ = _process_chunk_content(body, nid, n["path"], policy, g)
        body_spans = _split_spans(proc_body) if proc_body is not None else []
        for i, (part, lo, hi) in enumerate(body_spans):
            if line_map:
                p_start = line_map[min(lo, len(line_map) - 1)]
                p_end = line_map[min(hi, len(line_map) - 1)]
            else:
                p_start, p_end = span_start, span_end
            if max_chunks > 0 and yielded >= max_chunks:
                if getattr(g, "limit_policy", "warn") == "fail":
                    from .graph import GraphLimitExceeded

                    raise GraphLimitExceeded(
                        f"Chunk limit exceeded: reached {yielded} chunks (max_chunks={max_chunks}). "
                        "Use --max-chunks to increase the limit."
                    )
                if hasattr(g, "limits_hit"):
                    g.limits_hit["chunks_dropped"] = g.limits_hit.get("chunks_dropped", 0) + 1
                    if hasattr(g, "_note_limit"):
                        g._note_limit(
                            "chunks",
                            f"repo2graph: warning: chunk ceiling reached at {max_chunks} chunks; "
                            f"further chunks are dropped and the graph is partial",
                        )
                return
            yielded += 1
            yield {
                "id": f"{nid}#{i}" if i else nid,
                "node_id": nid,
                "type": label_kind,
                "kind": n.get("file_type", "other"),
                "path": n["path"],
                "lang": n.get("lang"),
                "name": n["name"],
                "qualname": n["path"],
                "start_line": p_start,
                "end_line": p_end,
                "entrypoint": False,
                "callers": [],
                "callees": [],
                "callees_external": [],
                "caller_edges": [],
                "callee_edges": [],
                "base_edges": [],
                "text": "\n".join(header) + "\n" + part,
            }
