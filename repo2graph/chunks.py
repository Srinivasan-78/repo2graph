"""Turn graph nodes into retrieval chunks: code text + graph context header."""

from collections import Counter, defaultdict
from typing import Any

MAX_CHARS = 4000
OVERLAP_LINES = 8

# ISS-26: Context caps for headers
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
    if policy == "exclude-file":
        from .secrets import scan_content_secrets

        if scan_content_secrets(text):
            if hasattr(g, "stats"):
                g.stats["skipped_secret_chunks"] += 1
            return None, 0
        return text, 0
    elif policy == "redact-match":
        from .secrets import redact_content

        redacted, r_count = redact_content(text, policy=policy)
        if r_count > 0 and hasattr(g, "stats"):
            g.stats["redacted_secret_chunks"] += r_count
        return redacted, r_count
    elif policy == "warn-only":
        from .events import emit
        from .secrets import scan_content_secrets

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
    tree-sitter's row numbers do not; using it here slices every later symbol's
    chunk from the wrong lines (ISS-22). Drop a trailing "\\r" per line so
    CRLF files still index cleanly.
    """
    return [ln[:-1] if ln.endswith("\r") else ln for ln in src.split("\n")]


def _keepends_lf(text: str) -> list[str]:
    """Split on "\\n" only, keeping the newline, so "".join(result) == text.

    str.splitlines(keepends=True) also breaks on U+2028/U+2029/U+0085/\\x0b/\\x0c,
    which tree-sitter does not treat as row breaks; splitting a chunk there makes
    its pieces depend on whichever stray separators the source happens to hold
    (the ISS-22 bug class). Same "\\n"-only rule as _lines, but lossless.
    """
    parts = text.split("\n")
    lines = [p + "\n" for p in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _split(text: str, max_chars: int = MAX_CHARS):
    if len(text) <= max_chars:
        return [text]
    lines = _keepends_lf(text)
    # ISS-153: a single line longer than max_chars (minified JS/CSS, a long SVG
    # path, base64, one-line JSON, ...) can't be shrunk by grouping on line
    # boundaries alone -- the packer below would emit it whole, unbounded.
    # Break any such line into max_chars-sized pieces first so every entry the
    # packer sees is already within budget; "".join(pieces) still == the
    # original line, so no text is lost or reordered.
    if any(len(ln) > max_chars for ln in lines):
        bounded: list[str] = []
        for ln in lines:
            if len(ln) > max_chars:
                bounded.extend(ln[j : j + max_chars] for j in range(0, len(ln), max_chars))
            else:
                bounded.append(ln)
        lines = bounded
    out: list[str] = []
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
        out.append("".join(buf))
        if i < len(lines):
            i = max(start + 1, i - OVERLAP_LINES)
    return out


def _conf(text: str, edge: dict) -> str:
    """Label a CALLS edge with its confidence when the call was ambiguous."""
    c = edge.get("confidence", 1.0)
    return text if c >= 1.0 else f"{text} (confidence {c})"


def _neighbour_edge(target: str, edge: dict, direction: str) -> dict:
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


def build_chunks(g, include_files: bool = True) -> list[dict]:
    """All retrieval chunks as a list (stable public API)."""
    return list(iter_chunks(g, include_files))


def iter_chunks(g, include_files: bool = True):
    """Yield chunk dicts ready for embedding, one at a time.

    A generator, not a list: on a large repo the chunk text is the single
    biggest allocation, so write_jsonl streams it straight to disk instead of
    holding every chunk in memory at once. Source text is dropped from the cache
    as soon as the last symbol on a file has been emitted.
    """
    out_edges, in_edges = defaultdict(list), defaultdict(list)
    for e in g.edges:
        out_edges[e["src"]].append(e)
        in_edges[e["dst"]].append(e)

    policy = getattr(getattr(g, "config", None), "secret_policy", "redact-match")
    if getattr(getattr(g, "config", None), "include_secrets", False):
        policy = "off"

    src_cache: dict[str, str] = {}

    def source_of(path: str) -> str:
        if path not in src_cache:
            try:
                src_cache[path] = (g.root / path).read_text("utf8", "replace")
            except OSError:
                src_cache[path] = ""
        return src_cache[path]

    def label(nid: str) -> str:
        n = g.nodes.get(nid)
        if not n:
            return nid
        if n["type"] == "symbol":
            return f"{n['path']}::{n['qualname']}"
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
            header.append("# doc: " + n["docstring"].replace("\n", " ")[:300])
        for i, part in enumerate(_split(body)):
            proc_part, _ = _process_chunk_content(part, nid, n["path"], policy, g)
            if proc_part is None:
                continue
            yield {
                "id": f"{nid}#{i}" if i else nid,
                "node_id": nid,
                "type": "symbol",
                "kind": n["kind"],
                "path": n["path"],
                "lang": n["lang"],
                "name": n["name"],
                "qualname": n["qualname"],
                "start_line": n["start_line"],
                "end_line": n["end_line"],
                "entrypoint": bool(n.get("entrypoint")),
                "callers": callers,
                "callees": callees,
                "callees_external": ext,
                "caller_edges": caller_edges,
                "callee_edges": callee_edges,
                "base_edges": base_edges,
                "text": "\n".join(header) + "\n" + proc_part,
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
            body = "\n".join(keep).strip()
            if len(body) < 40:
                continue
            label_kind = "file_residual"
            # ISS-23: emit real span for residual chunks
            span_start = line_indices[0] if line_indices else None
            span_end = line_indices[-1] if line_indices else None
        else:
            body, label_kind = src, "file"
            span_start, span_end = 1, n.get("lines", 0)
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
        # ISS-141: same id rule as symbols — chunk 0 is unsuffixed `nid`.
        for i, part in enumerate(_split(body)):
            proc_part, _ = _process_chunk_content(part, nid, n["path"], policy, g)
            if proc_part is None:
                continue
            yield {
                "id": f"{nid}#{i}" if i else nid,
                "node_id": nid,
                "type": label_kind,
                "kind": n.get("file_type", "other"),
                "path": n["path"],
                "lang": n.get("lang"),
                "name": n["name"],
                "qualname": n["path"],
                "start_line": span_start,
                "end_line": span_end,
                "entrypoint": False,
                "callers": [],
                "callees": [],
                "callees_external": [],
                "caller_edges": [],
                "callee_edges": [],
                "base_edges": [],
                "text": "\n".join(header) + "\n" + proc_part,
            }
