# @authormark v1 -- do not remove (authorship watermark)⁠​‌‌‌​‌‌‌​‌‌‌​‌‌​​‌‌​‌‌​​​‌​‌‌​‌​​‌‌‌​‌‌‌​‌‌‌​​‌​​‌​‌​‌‌​​​‌‌​​‌​​‌​‌‌​​​​​‌‌​​‌‌​‌‌‌​​​​​‌​‌​‌‌​​‌‌‌​​​​​‌‌‌‌​​​​‌‌‌‌​​‌​‌​‌​‌​‌​‌‌​​‌​‌​‌​​​‌​​​‌‌​‌​‌‌​‌‌​‌​​​​‌‌‌‌​‌​​​‌‌​​‌‌⁠
# Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
# Author: https://github.com/Srinivasan-78
# SPDX-License-Identifier: MIT
# Fingerprint: AMK1.wvlZwrV2X3pVpxyUeDkhz3
"""Turn graph nodes into retrieval chunks: code text + graph context header."""
from collections import defaultdict

MAX_CHARS = 4000
OVERLAP_LINES = 8

# ISS-26: Context caps for headers
MAX_CALLERS = 12
MAX_CALLEES = 12
MAX_EXT_CALLS = 12
MAX_BASES = 6
MAX_IMPORTS = 20
MAX_DEFINES = 40


def _lines(src: str) -> list[str]:
    """Split source the way tree-sitter counts rows: on "\\n" only.

    str.splitlines() also breaks on U+2028/U+2029/U+0085/\\x0b/\\x0c, which
    tree-sitter's row numbers do not; using it here slices every later symbol's
    chunk from the wrong lines (ISS-22). Drop a trailing "\\r" per line so
    CRLF files still index cleanly.
    """
    return [ln[:-1] if ln.endswith("\r") else ln for ln in src.split("\n")]


def _split(text: str, max_chars: int = MAX_CHARS):
    if len(text) <= max_chars:
        return [text]
    lines, out, buf, size = text.splitlines(keepends=True), [], [], 0
    i = 0
    while i < len(lines):
        buf, size = [], 0
        start = i
        while i < len(lines) and size < max_chars:
            buf.append(lines[i]); size += len(lines[i]); i += 1
        out.append("".join(buf))
        if i < len(lines):
            i = max(start + 1, i - OVERLAP_LINES)
    return out


def _conf(text: str, edge: dict) -> str:
    """Label a CALLS edge with its confidence when the call was ambiguous."""
    c = edge.get("confidence", 1.0)
    return text if c >= 1.0 else f"{text} (confidence {c})"


def build_chunks(g, include_files: bool = True):
    """Yield chunk dicts ready for embedding."""
    out_edges, in_edges = defaultdict(list), defaultdict(list)
    for e in g.edges:
        out_edges[e["src"]].append(e)
        in_edges[e["dst"]].append(e)

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
    chunks = []

    for nid, n in g.nodes.items():
        if n["type"] != "symbol":
            continue
        src = source_of(n["path"])
        lines = _lines(src)
        body = "\n".join(lines[n["start_line"] - 1: n["end_line"]])
        covered[n["path"]].append((n["start_line"], n["end_line"]))
        call_out = [e for e in out_edges[nid] if e["type"] == "CALLS"][:MAX_CALLEES]
        call_in = [e for e in in_edges[nid] if e["type"] == "CALLS"][:MAX_CALLERS]
        callees = [label(e["dst"]) for e in call_out]
        callers = [label(e["src"]) for e in call_in]
        ext = [g.nodes[e["dst"]]["name"] for e in out_edges[nid] if e["type"] == "CALLS_EXTERNAL"][:MAX_EXT_CALLS]
        bases = [label(e["dst"]) for e in out_edges[nid] if e["type"] == "INHERITS"][:MAX_BASES]
        # a call to an overloaded name fans out to every candidate at 1/n
        # confidence; say so in the header, or a reader follows the wrong edge
        # believing it is the only one.
        out_conf = [_conf(t, e) for t, e in zip(callees, call_out)]
        in_conf = [_conf(t, e) for t, e in zip(callers, call_in)]
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
            chunks.append({
                "id": f"{nid}#{i}" if i else nid,
                "node_id": nid, "type": "symbol", "kind": n["kind"], "path": n["path"],
                "lang": n["lang"], "name": n["name"], "qualname": n["qualname"],
                "start_line": n["start_line"], "end_line": n["end_line"],
                "entrypoint": bool(n.get("entrypoint")),
                "callers": callers, "callees": callees, "callees_external": ext,
                "text": "\n".join(header) + "\n" + part,
            })

    if not include_files:
        return chunks

    for nid, n in g.nodes.items():
        if n["type"] != "file":
            continue
        src = source_of(n["path"])
        if not src.strip():
            continue
        spans = sorted(covered.get(n["path"], []))
        lines = _lines(src)
        if spans:
            keep, cur = [], 1
            line_indices = []
            for s, e in spans:
                if s > cur:
                    keep += lines[cur - 1: s - 1]
                    line_indices.extend(range(cur, s))
                cur = max(cur, e + 1)
            if cur <= len(lines):
                keep += lines[cur - 1:]
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
        imports = [e.get("target", "") for e in out_edges[nid] if e["type"] == "IMPORTS"][:MAX_IMPORTS]
        defines = [g.nodes[e["dst"]]["qualname"] for e in out_edges[nid] if e["type"] == "DEFINES"][:MAX_DEFINES]
        header = [f"# file: {n['path']} ({n.get('lang')}, {n.get('lines')} lines)"]
        if imports:
            header.append(f"# imports: {', '.join(i for i in imports if i)}")
        if defines:
            header.append(f"# defines: {', '.join(defines)}")
        for i, part in enumerate(_split(body)):
            chunks.append({
                "id": f"{nid}#{i}", "node_id": nid, "type": label_kind,
                "kind": n.get("file_type", "other"), "path": n["path"],
                "lang": n.get("lang"), "name": n["name"], "qualname": n["path"],
                "start_line": span_start, "end_line": span_end,
                "entrypoint": False,
                "callers": [], "callees": [], "callees_external": [],
                "text": "\n".join(header) + "\n" + part,
            })
    return chunks
