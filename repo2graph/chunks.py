# @authormark v1 -- do not remove (authorship watermark)⁠​‌‌‌​‌‌‌​‌‌‌​‌‌​​‌‌​‌‌​​​‌​‌‌​‌​​‌‌‌​‌‌‌​‌‌‌​​‌​​‌​‌​‌‌​​​‌‌​​‌​​‌​‌‌​​​​​‌‌​​‌‌​‌‌‌​​​​​‌​‌​‌‌​​‌‌‌​​​​​‌‌‌‌​​​​‌‌‌‌​​‌​‌​‌​‌​‌​‌‌​​‌​‌​‌​​​‌​​​‌‌​‌​‌‌​‌‌​‌​​​​‌‌‌‌​‌​​​‌‌​​‌‌⁠
# Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
# Author: https://github.com/Srinivasan-78
# SPDX-License-Identifier: MIT
# Fingerprint: AMK1.wvlZwrV2X3pVpxyUeDkhz3
"""Turn graph nodes into retrieval chunks: code text + graph context header."""
from collections import defaultdict

MAX_CHARS = 4000
OVERLAP_LINES = 8


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
        lines = src.splitlines()
        body = "\n".join(lines[n["start_line"] - 1: n["end_line"]])
        covered[n["path"]].append((n["start_line"], n["end_line"]))
        call_out = [e for e in out_edges[nid] if e["type"] == "CALLS"][:12]
        call_in = [e for e in in_edges[nid] if e["type"] == "CALLS"][:12]
        callees = [label(e["dst"]) for e in call_out]
        callers = [label(e["src"]) for e in call_in]
        ext = [g.nodes[e["dst"]]["name"] for e in out_edges[nid] if e["type"] == "CALLS_EXTERNAL"][:12]
        bases = [label(e["dst"]) for e in out_edges[nid] if e["type"] == "INHERITS"][:6]
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
        lines = src.splitlines()
        if spans:
            keep, cur = [], 1
            for s, e in spans:
                if s > cur:
                    keep += lines[cur - 1: s - 1]
                cur = max(cur, e + 1)
            keep += lines[cur - 1:]
            body = "\n".join(keep).strip()
            if len(body) < 40:
                continue
            label_kind = "file_residual"
        else:
            body, label_kind = src, "file"
        imports = [e.get("target", "") for e in out_edges[nid] if e["type"] == "IMPORTS"][:20]
        defines = [g.nodes[e["dst"]]["qualname"] for e in out_edges[nid] if e["type"] == "DEFINES"][:40]
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
                "start_line": 1, "end_line": n.get("lines", 0),
                "entrypoint": False,
                "callers": [], "callees": [], "callees_external": [],
                "text": "\n".join(header) + "\n" + part,
            })
    return chunks
