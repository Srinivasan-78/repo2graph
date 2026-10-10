"""Interactive knowledge-graph map: one self-contained HTML file, no CDN, no build step."""

import heapq
import html
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .security import HIDDEN_UNICODE_RE


# The Neo4j browser palette, so the map reads the way their graph view does.
NODE_COLORS = {
    "repo": "#F16667",
    "dir": "#D9C8AE",
    "file": "#F79767",
    "symbol": "#57C7E3",
    "module": "#8DCC93",
    "external": "#ECB5C9",
}
OTHER_COLOR = "#C9CBCF"

# How strongly an edge argues for keeping its endpoints when the graph is too
# big to draw. Directory scaffolding is cheap; calls and imports are the point.
EDGE_WEIGHT = {
    "CALLS": 3.0,
    "IMPORTS": 3.0,
    "INHERITS": 3.0,
    "CO_CHANGE": 2.0,
    "DEFINES": 1.0,
    "CALLS_EXTERNAL": 0.75,
    "CONTAINS": 0.5,
    # Mostly a shortcut over CALLS edges already drawn, so it should not be
    # what decides which nodes make the cut.
    "TESTS": 0.5,
}
MAX_NODES = 300
LABEL_CHARS = 15
# A hard ceiling on the JSON embedded in graph.html (#305). Past it a browser
# struggles to open the page at all, so the detail view is halved until the
# page fits, and the page says that it was.
MAX_PAGE_BYTES = 12_000_000
# Relationships the directory summary adds up. Containment is the hierarchy
# being summarised, not a relationship between directories.
AGG_EDGE_TYPES = (
    "CALLS",
    "IMPORTS",
    "INHERITS",
    "CO_CHANGE",
    "TESTS",
    "READS",
    "WRITES",
    "REFERENCES",
)
AGG_TOP_MEMBERS = 8
# Directory groups in the summary: past this a summary stops being one.
AGG_MAX_DIRS = 60
# Stdlib and third-party call targets triple the edge count and tell you little
# about the repo itself, so the map opens without them; the legend turns them on.
HIDDEN_NODE_TYPES = ["external"]
# TESTS hides for the same reason: a test's direct calls are already drawn as
# CALLS, so the map opens without the duplicate arrows.
HIDDEN_EDGE_TYPES = ["CALLS_EXTERNAL", "TESTS"]

# Node/edge type descriptions for the map's legend panel and (via export.py's
# import of these names) manifest.json's node_types/edge_types.
#
# Issue #349: these used to be defined twice -- once here, worded for the
# legend, once in export.py, worded for manifest.json -- and the two copies
# had already drifted (punctuation, phrasing, markdown). export.py imports
# from this module already (`from .viz import ... write_html`), so this is
# the one direction that does not create a cycle: define them here, once, and
# let export.py import them rather than keep its own copy.
NODE_TYPES = {
    "repo": "the repository itself; one per index",
    "dir": "a directory",
    "file": "a source, doc or config file",
    "symbol": "a function, method, class, struct, trait, interface, type or module",
    "module": "an import target that is not a file in this repo",
    "external": "a call target that could not be resolved in this repo (stdlib or third-party)",
}

EDGE_TYPES = {
    "CONTAINS": "repo -> dir -> file",
    "DEFINES": "file -> symbol, and symbol -> symbol nested inside it",
    "IMPORTS": "file -> file (internal: true) or file -> module",
    "CALLS": "symbol -> symbol in this repo; carries count and confidence",
    "CALLS_EXTERNAL": "symbol -> external, a name that resolved to nothing in-repo",
    "INHERITS": "symbol -> base class or interface",
    "CO_CHANGE": "file <-> file, edited together in 3+ of the commits read by --git-history",
    "TESTS": (
        "test symbol -> non-test symbol it reaches through at most 2 CALLS hops; "
        "reachability, not assertion"
    ),
}

# Only with `build --reference-edges` (#397), and only listed in manifest.json
# when the graph has them, so a default build's manifest is unchanged.
REFERENCE_EDGE_TYPES = {
    "READS": "symbol -> module-level variable/constant or class field it reads",
    "WRITES": "symbol -> module-level variable (Python: under `global`) or class field it assigns",
    "REFERENCES": "symbol -> class/interface/type it names in a type annotation",
}


def _trim(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def node_label(n: dict[str, Any]) -> str:
    text = n.get("qualname") or n.get("name") or n.get("path") or n["id"]
    return _trim(text, LABEL_CHARS)


def _scores(edges: list[dict[str, Any]]) -> dict[str, float]:
    """Weighted degree per node id, by EDGE_WEIGHT."""
    # Edge weights are fractional (EDGE_WEIGHT), so this accumulates float
    # scores -- a plain dict, not Counter, which typeshed pins to int counts.
    score: dict[str, float] = defaultdict(float)
    for e in edges:
        w = EDGE_WEIGHT.get(e["type"], 1.0)
        score[e["src"]] += w
        score[e["dst"]] += w
    return score


def select(
    nodes: dict[str, Any], edges: list[dict[str, Any]], max_nodes: int | None = MAX_NODES
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep the max_nodes best-connected nodes, and the edges between them.

    A force layout stops being readable long before a real repo stops having
    nodes, so the map shows the hubs: rank by edge weight, drop the tail.
    """
    score = _scores(edges)
    # `None` means no cap; an integer is taken literally, 0 included. 0 used to
    # mean "no cap" as well, which made the two most opposite intentions a
    # caller could have -- "draw everything" and "draw nothing" -- share one
    # spelling, and made a mistyped or defaulted-to-zero argument silently
    # render the largest possible page. `all` is now the way to say no cap.
    if max_nodes is None:
        keep = set(nodes)
    elif max_nodes <= 0:
        keep = set()
    elif len(nodes) > max_nodes:
        ranked = sorted(nodes.values(), key=lambda n: (-score[n["id"]], n["id"]))
        keep = {n["id"] for n in ranked[:max_nodes]}
    else:
        keep = set(nodes)
    kept_nodes = [n for n in nodes.values() if n["id"] in keep]
    kept_edges = [e for e in edges if e["src"] in keep and e["dst"] in keep]
    return kept_nodes, kept_edges


def _home_dir(n: dict[str, Any]) -> str | None:
    """The directory a node lives in; None for repo/module/external nodes."""
    if n["type"] == "dir":
        return str(n.get("path") or "")
    if n["type"] in ("file", "symbol"):
        return str(n.get("path") or "").rpartition("/")[0]
    return None


def _parent_dir(d: str) -> str | None:
    if not d:
        return None
    return d.rpartition("/")[0]


def _groups(dirs: Counter[str], max_dirs: int) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Partition directories into at most `max_dirs` groups, biggest directory first.

    Starts with the whole tree as one group and repeatedly carves out the
    biggest directory not yet carved (by node count) into a group of its own,
    until the cap. A directory it is carved from keeps a group for what is
    left in it, its own files and the subdirectories not carved out. So the
    big parts of the tree are shown in detail wherever they are, small ones
    stay whole, and every node is counted in exactly one group.

    Returns:
        (directory -> its group, group -> the subdirectories carved out of it).
    """
    under: Counter[str] = Counter()
    children: dict[str, set[str]] = defaultdict(set)
    for d, n in dirs.items():
        cur: str | None = d
        while cur is not None:
            under[cur] += n
            parent = _parent_dir(cur)
            if parent is not None:
                children[parent].add(cur)
            cur = parent
    groups = {""}
    left = {"": under[""]}  # nodes each group still holds
    carved: dict[str, list[str]] = defaultdict(list)

    def group_of(d: str) -> str:
        cur: str | None = d
        while cur is not None and cur not in groups:
            cur = _parent_dir(cur)
        return cur if cur is not None else ""

    heap = [(-under[k], k) for k in children.get("", ())]
    heapq.heapify(heap)
    shown = 1
    while heap and shown < max_dirs:
        _neg, d = heapq.heappop(heap)
        host = group_of(_parent_dir(d) or "")
        groups.add(d)
        left[d] = under[d]
        left[host] -= under[d]
        carved[host].append(d)
        # Carving everything out of a group replaces it rather than adding one.
        shown += 0 if left[host] == 0 else 1
        for k in children.get(d, ()):
            heapq.heappush(heap, (-under[k], k))
    return {d: group_of(d) for d in dirs}, {
        g: sorted(kids) for g, kids in carved.items() if left[g] > 0
    }


def aggregate(g: Any, max_dirs: int) -> dict[str, Any] | None:
    """The summary view (#305): one node per directory group, relationships added up.

    Every node in the graph is counted in exactly one group, so nothing is
    sampled; see `_groups` for how the tree is cut. None when the result is a
    single group, which summarises nothing.
    """
    home = {nid: d for nid, n in g.nodes.items() if (d := _home_dir(n)) is not None}
    to_group, carved = _groups(Counter(home.values()), max_dirs)
    group = {nid: to_group[d] for nid, d in home.items()}
    members: dict[str, list[str]] = defaultdict(list)
    for nid in sorted(group):
        members[group[nid]].append(nid)
    if len(members) < 2:
        return None

    score = _scores(g.edges)
    weight: Counter[tuple[str, str, str]] = Counter()
    for e in g.edges:
        if e["type"] not in AGG_EDGE_TYPES:
            continue
        a, b = group.get(e["src"]), group.get(e["dst"])
        if a is not None and b is not None and a != b:
            weight[(a, b, e["type"])] += 1
    names = sorted(members)
    index = {d: i for i, d in enumerate(names)}
    deg: Counter[str] = Counter()
    for a, b, _t in weight:
        deg[a] += 1
        deg[b] += 1

    out_nodes = []
    for d in names:
        ns = [g.nodes[m] for m in members[d]]
        files = [n for n in ns if n["type"] == "file"]
        langs = Counter(
            n["lang"] for n in files if n.get("lang") and n.get("file_type", "code") == "code"
        )
        top = sorted(
            (n for n in ns if n["type"] in ("file", "symbol")),
            key=lambda n: (-score.get(n["id"], 0.0), n["id"]),
        )[:AGG_TOP_MEMBERS]
        label = d.rsplit("/", 1)[-1] if d else "(root)"
        item: dict[str, Any] = {
            "id": f"agg:{d}",
            "label": _trim(label + ("/…" if d in carved else ""), LABEL_CHARS),
            "type": "dir",
            "path": d,
            "deg": deg[d],
            "files": len(files),
            "symbols": sum(1 for n in ns if n["type"] == "symbol"),
            "lines": sum(int(n.get("lines") or 0) for n in files),
            "top": [n.get("qualname") or n.get("path") or n["id"] for n in top],
        }
        if d in carved:
            # These subdirectories have groups of their own; this one is the rest.
            item["except"] = carved[d]
        if langs:
            item["lang"] = min(langs, key=lambda k: (-langs[k], k))
        out_nodes.append(item)
    edges = [
        {"s": index[a], "t": index[b], "type": t, "w": w}
        for (a, b, t), w in sorted(weight.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return {
        "nodes": out_nodes,
        "edges": edges,
        "nodeTypes": [["dir", len(out_nodes)]],
        "edgeTypes": sorted(Counter(e["type"] for e in edges).items()),
    }


def payload(
    g: Any, max_nodes: int | None = MAX_NODES, shrunk_from: int | None = None
) -> dict[str, Any]:
    """The JSON the page draws: nodes, edges by index, legend counts.

    When the cap leaves nodes out, it also carries `sampled` (what was left
    out and why, for the page's banner) and, where the tree has more than one
    directory, `dirs`: the directory summary every node is counted in.
    """
    nodes, edges = select(g.nodes, g.edges, max_nodes)
    index = {n["id"]: i for i, n in enumerate(nodes)}
    deg: Counter[str] = Counter()
    for e in edges:
        deg[e["src"]] += 1
        deg[e["dst"]] += 1

    out_nodes = []
    for n in nodes:
        item = {"id": n["id"], "label": node_label(n), "type": n["type"], "deg": deg[n["id"]]}
        for key in ("path", "kind", "lang", "start_line", "end_line", "lines"):
            if n.get(key) not in (None, "", []):
                item[key] = n[key]
        if n.get("signature"):
            item["sig"] = _trim(n["signature"], 300)
        if n.get("docstring"):
            item["doc"] = _trim(n["docstring"], 400)
        out_nodes.append(item)

    data: dict[str, Any] = {
        "name": g.name,
        "nodes": out_nodes,
        "edges": [{"s": index[e["src"]], "t": index[e["dst"]], "type": e["type"]} for e in edges],
        "colors": {**NODE_COLORS, "_": OTHER_COLOR},
        "nodeDesc": NODE_TYPES,
        "edgeDesc": EDGE_TYPES,
        "hidden": {"nodes": HIDDEN_NODE_TYPES, "edges": HIDDEN_EDGE_TYPES},
        "nodeTypes": sorted(Counter(n["type"] for n in out_nodes).items()),
        "edgeTypes": sorted(Counter(e["type"] for e in edges).items()),
        "totals": {"nodes": len(g.nodes), "edges": len(g.edges)},
    }
    if len(out_nodes) < len(g.nodes):
        data["sampled"] = {
            "nodes": len(out_nodes),
            "edges": len(edges),
            "rule": "the best-connected nodes, by weighted degree",
        }
        if shrunk_from is not None:
            data["sampled"]["shrunk_from"] = shrunk_from
            data["sampled"]["page_limit_bytes"] = MAX_PAGE_BYTES
        if max_nodes:
            agg = aggregate(g, min(max_nodes, AGG_MAX_DIRS))
            if agg is not None:
                data["dirs"] = agg
    return data


def _blob(data: dict[str, Any]) -> str:
    # Escape every "<": json.dumps only emits it inside a string literal, and
    # "\u003c" parses back to "<" through JSON.parse. This stops not just a
    # literal "</script>" but "<!--" followed by "<script" — which drive the
    # HTML tokeniser into "script data double escaped" state, where the
    # template's own "</script>" no longer closes the block and every source
    # file that mentions both tokens (web frameworks, this repo) silently kills
    # the map with a SyntaxError.
    blob = (
        json.dumps(data, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    # Bidi overrides and zero-width characters are invisible on the page, which
    # is the whole problem with them. Spell them out as the visible text
    # `\u202e` (an escaped backslash, so JSON.parse yields the six characters).
    return HIDDEN_UNICODE_RE.sub(lambda m: f"\\\\u{ord(m.group(0)):04x}", blob)


def write_html(g: Any, path: Path, max_nodes: int | None = MAX_NODES) -> dict[str, Any]:
    data = payload(g, max_nodes)
    blob = _blob(data)
    # Never a page too big to open (#305): halve the detail view until the
    # page fits, and record that it was cut for size, not by the cap asked for.
    asked = len(g.nodes) if max_nodes is None else max_nodes
    cap = asked
    while len(blob.encode("utf8")) > MAX_PAGE_BYTES and cap > 0:
        cap //= 2
        data = payload(g, cap, shrunk_from=asked)
        blob = _blob(data)
    # Single-pass replace prevents __R2G_DATA__ in repo title from expanding
    replacements = {
        "__R2G_DATA__": blob,
        "__R2G_TITLE__": html.escape(str(g.name)),
    }
    pattern = re.compile("|".join(re.escape(k) for k in replacements))
    page = pattern.sub(lambda m: replacements[m.group(0)], TEMPLATE)
    # newline="\n": keep graph.html byte-identical across a Linux CI run and a
    # local Windows rebuild, so the commit-branch push carries no CRLF churn.
    # atomic_write: a crash mid-write never leaves a half-rendered page.
    from .export import atomic_write

    with atomic_write(Path(path), "w", encoding="utf8", newline="\n") as fh:
        fh.write(page)
    return data


class LoadedGraph:
    """The parts of Graph the map needs, read back from nodes/edges.jsonl."""

    def __init__(self, outdir: Path):
        from .export import path as artifact_path
        from .query import read_jsonl

        outdir = Path(outdir)
        self.name = outdir.name
        self.nodes = {n["id"]: n for n in read_jsonl(artifact_path(outdir, "nodes.jsonl"))}
        self.edges = read_jsonl(artifact_path(outdir, "edges.jsonl"))
        overview = artifact_path(outdir, "overview.md")
        if overview.exists():
            # artifact_path resolves to human/overview.md, whose first line is
            # write_overview_human's "# Repo overview: <name>" heading.
            first = overview.read_text(encoding="utf8").split("\n", 1)[0]
            self.name = first.removeprefix("# Repo overview:").strip() or self.name
        index = artifact_path(outdir, "index.json")
        if index.exists():
            try:
                self.name = json.loads(index.read_text(encoding="utf8")).get("repo", self.name)
            except (json.JSONDecodeError, OSError):
                pass


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__R2G_TITLE__ · repo2graph</title>
<style>
  :root {
    --bg: #f7f8fa; --panel: #ffffff; --line: #dfe3e8; --ink: #1c2330;
    --muted: #667085; --edge: #a5adba; --accent: #2f6fed;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body {
    font: 13px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: var(--ink); background: var(--bg); overflow: hidden;
  }
  #app { display: flex; height: 100%; }
  #side {
    width: 310px; flex: 0 0 310px; background: var(--panel); border-right: 1px solid var(--line);
    display: flex; flex-direction: column; overflow: hidden;
  }
  #side h1 { font-size: 14px; margin: 0; letter-spacing: .2px; }
  #side h2 { font-size: 13px; margin: 0 0 6px; }
  .pad { padding: 12px 14px; border-bottom: 1px solid var(--line); }
  .sub { color: var(--muted); font-size: 11.5px; margin-top: 4px; }
  #search-wrap { position: relative; margin-top: 9px; }
  #search {
    width: 100%; padding: 7px 9px; border: 1px solid var(--line); border-radius: 6px;
    font: inherit; background: #fff; color: inherit;
  }
  #search:focus { outline: 2px solid rgba(47,111,237,.35); border-color: var(--accent); }
  #search-results {
    position: absolute; left: 0; right: 0; top: 100%; margin-top: 4px;
    background: var(--panel); border: 1px solid var(--line); border-radius: 6px;
    max-height: 220px; overflow: auto; z-index: 20; box-shadow: 0 6px 18px rgba(0,0,0,.15);
  }
  #search-results[hidden] { display: none; }
  .search-result {
    display: flex; align-items: center; gap: 7px; padding: 6px 9px; cursor: pointer; font-size: 12px;
  }
  .search-result:hover, .search-result:focus { background: #f0f3fa; outline: none; }
  .search-result .swatch { border-radius: 50%; }
  .legend-head {
    display: flex; align-items: center; justify-content: space-between; cursor: pointer;
    user-select: none;
  }
  .legend-head h2 { margin: 0; }
  #legend-chevron { color: var(--muted); font-size: 10px; transition: transform .15s; }
  #legend-body.collapsed { display: none; }
  .legend { display: flex; flex-direction: column; gap: 4px; }
  .legend label { display: flex; align-items: center; gap: 7px; cursor: pointer; }
  .legend input { margin: 0; }
  .legend-desc { margin: -2px 0 4px 19px; color: var(--muted); font-size: 10.5px; }
  .swatch { width: 12px; height: 12px; border-radius: 50%; flex: 0 0 12px; }
  .bar { width: 12px; height: 3px; border-radius: 2px; background: var(--edge); flex: 0 0 12px; }
  .count { margin-left: auto; color: var(--muted); font-size: 11px; }
  .btns { display: flex; gap: 6px; flex-wrap: wrap; }
  button {
    font: inherit; font-size: 12px; padding: 5px 10px; border: 1px solid var(--line);
    background: #fff; border-radius: 6px; cursor: pointer; color: inherit;
  }
  button:hover { border-color: var(--accent); color: var(--accent); }
  #details { padding: 12px 14px; overflow: auto; flex: 1; }
  #details .kv { display: grid; grid-template-columns: 74px 1fr; gap: 3px 8px; margin: 8px 0; }
  #details .kv span:first-child { color: var(--muted); }
  #details .kv span:last-child { overflow-wrap: anywhere; }
  #details pre {
    margin: 6px 0; padding: 8px; background: #f3f5f8; border-radius: 6px;
    font: 11.5px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    white-space: pre-wrap; overflow-wrap: anywhere;
  }
  #details .nb { display: block; width: 100%; text-align: left; margin: 3px 0; }
  #details .nb .count { font-size: 10.5px; }
  .chip {
    display: inline-block; padding: 1px 7px; border-radius: 10px; font-size: 11px;
    color: #2b2b2b; font-weight: 600;
  }
  #stage { flex: 1; position: relative; }
  #progress { position: absolute; top: 12px; left: 50%; transform: translateX(-50%);
              z-index: 5; background: #1c2333; color: #c9d1d9; border-radius: 6px;
              padding: 6px 10px; font-size: 12px; display: flex; align-items: center;
              gap: 8px; box-shadow: 0 2px 8px rgba(0,0,0,.35); }
  #progress[hidden] { display: none; }
  #progress-track { width: 120px; height: 4px; background: #30363d; border-radius: 2px; }
  #progress-bar { height: 4px; width: 0%; background: #58a6ff; border-radius: 2px;
                  transition: width .08s linear; }
  svg { width: 100%; height: 100%; display: block; cursor: grab; touch-action: none; }
  svg.panning { cursor: grabbing; }
  .link { stroke: var(--edge); stroke-width: 1.1px; }
  .link-label {
    fill: var(--muted); font-size: 8.8px; letter-spacing: .3px; text-anchor: middle;
    paint-order: stroke; stroke: var(--bg); stroke-width: 3px; pointer-events: none;
    user-select: none;
  }
  .node { cursor: pointer; }
  .node circle { stroke: rgba(0,0,0,.16); stroke-width: 1px; }
  .node text {
    text-anchor: middle; dominant-baseline: central; font-size: 9.5px; fill: #23262b;
    pointer-events: none; user-select: none;
  }
  .node.pinned circle { stroke: #23262b; stroke-width: 1.6px; stroke-dasharray: 3 2; }
  .node.hit circle { stroke: #b8860b; stroke-width: 2.5px; }
  .node.focused circle { stroke: var(--accent); stroke-width: 2.5px; }
  .dim { opacity: .18; }
  #hud {
    position: absolute; right: 12px; bottom: 10px; color: var(--muted); font-size: 11px;
    background: rgba(255,255,255,.85); padding: 4px 8px; border-radius: 6px;
  }
  #banner {
    position: absolute; top: 10px; left: 12px; right: 12px; z-index: 4; padding: 7px 10px;
    background: #fff8e1; border: 1px solid #f0d58a; border-radius: 6px; font-size: 12px;
  }
  #banner[hidden] { display: none; }
  #banner button { font-size: 11px; padding: 2px 8px; margin-left: 6px; }
  button.on { border-color: var(--accent); color: var(--accent); font-weight: 600; }
  #lang-pad[hidden], #view-btns[hidden] { display: none; }
</style>
</head>
<body>
<div id="app">
  <aside id="side">
    <div class="pad">
      <h1>__R2G_TITLE__</h1>
      <div class="sub" id="counts"></div>
      <div id="search-wrap">
        <input id="search" type="search" placeholder="Search nodes (Enter = focus, Ctrl/Cmd+F)"
               autocomplete="off">
        <div class="legend" id="search-results" hidden></div>
      </div>
    </div>
    <div class="pad">
      <div class="legend-head" id="legend-toggle">
        <h2>Legend</h2>
        <span id="legend-chevron">&#9662;</span>
      </div>
    </div>
    <div id="legend-body">
      <div class="pad">
        <h2>Nodes</h2>
        <div class="legend" id="node-legend"></div>
      </div>
      <div class="pad">
        <h2>Relationships</h2>
        <div class="legend" id="edge-legend"></div>
      </div>
      <div class="pad" id="lang-pad" hidden>
        <h2>Languages</h2>
        <div class="legend" id="lang-legend"></div>
      </div>
    </div>
    <div class="pad btns" id="view-btns" hidden>
      <button id="btn-dirs">Directories</button>
      <button id="btn-detail">Detail</button>
    </div>
    <div class="pad btns">
      <button id="btn-fit">Fit</button>
      <button id="btn-relayout">Re-layout</button>
      <button id="btn-unpin">Unpin all</button>
      <button id="btn-clear-focus">Clear focus</button>
    </div>
    <div id="details"></div>
  </aside>
  <div id="stage" data-max-iterations="500">
    <div id="banner" hidden></div>
    <div id="progress" hidden><span>laying out…</span>
      <div id="progress-track"><div id="progress-bar"></div></div></div>
    <svg id="svg">
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6"
                markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="#a5adba"></path>
        </marker>
      </defs>
      <g id="view">
        <g id="g-edges"></g>
        <g id="g-edge-labels"></g>
        <g id="g-nodes"></g>
      </g>
    </svg>
    <div id="hud">drag to pan · scroll to zoom · drag a node to pin it · double-click to release</div>
  </div>
</div>
<script>
"use strict";
const DATA = __R2G_DATA__;
const NS = "http://www.w3.org/2000/svg";
// The graph on screen: DATA itself (detail) or DATA.dirs (the directory summary).
let nodes = [], links = [], current = DATA;
let mode = "detail";
// Detail view only: show just the nodes under this directory (null = all).
let dirFilter = null;
const svg = document.getElementById("svg");
const view = document.getElementById("view");
const gEdges = document.getElementById("g-edges");
const gEdgeLabels = document.getElementById("g-edge-labels");
const gNodes = document.getElementById("g-nodes");
const panel = document.getElementById("details");
const stage = document.getElementById("stage");
const progress = document.getElementById("progress");
const progressBar = document.getElementById("progress-bar");

const colorOf = n => DATA.colors[n.type] || DATA.colors._;
const radius = n => 13 + Math.min(20, Math.sqrt(n.deg || 1) * 3.2);
const hiddenNodeTypes = new Set(DATA.hidden.nodes);
const hiddenEdgeTypes = new Set(DATA.hidden.edges);
const hiddenLangs = new Set();
// The directory a node lives in, as the summary groups it (viz._home_dir).
function homeOf(n) {
  if (n.path === undefined) return null;
  if (n.type === "dir") return n.path;
  const i = n.path.lastIndexOf("/");
  return i < 0 ? "" : n.path.slice(0, i);
}
// A summary group: everything under its path, minus subdirectories carved out.
function underGroup(n, group) {
  const h = homeOf(n);
  if (h === null) return false;
  const under = p => p === "" || h === p || h.startsWith(p + "/");
  return under(group.path) && !(group.except || []).some(under);
}
function inDir(n) {
  return dirFilter === null || mode !== "detail" || underGroup(n, dirFilter);
}
const nodeShown = n =>
  !hiddenNodeTypes.has(n.type) && !(n.lang && hiddenLangs.has(n.lang)) && inDir(n);
const linkShown = l => !hiddenEdgeTypes.has(l.type) && nodeShown(l.source) && nodeShown(l.target);

let tx = 0, ty = 0, scale = 1, alpha = 1, raf = null, drag = null, pan = null, selected = null;
// The in-flight incremental settle, so a second relayout can cancel the first.
let settleRaf = null;

// ---------- model ----------
const adjacency = new Map();
function neighbours(id) {
  let list = adjacency.get(id);
  if (!list) { list = []; adjacency.set(id, list); }
  return list;
}
function model() {
  adjacency.clear();
  for (const l of links) {
    l.source = nodes[l.s];
    l.target = nodes[l.t];
    neighbours(l.source.id).push({ other: l.target, type: l.type, dir: "out" });
    neighbours(l.target.id).push({ other: l.source, type: l.type, dir: "in" });
  }
  for (const n of nodes) n.hay = (n.id + " " + n.label + " " + (n.path || "")).toLowerCase();
}

// A fixed seed keeps two runs of the same graph looking the same.
let seed = 20260823;
function rnd() { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; }
function scatter() {
  const ring = Math.max(180, Math.sqrt(nodes.length) * 55);
  nodes.forEach((n, i) => {
    const a = (i / nodes.length) * Math.PI * 2;
    const r = ring * (0.35 + rnd() * 0.65);
    n.x = Math.cos(a) * r; n.y = Math.sin(a) * r; n.vx = 0; n.vy = 0;
    n.fx = null; n.fy = null;
  });
}

// ---------- drawing surface ----------
function draw() {
gEdges.replaceChildren(); gEdgeLabels.replaceChildren(); gNodes.replaceChildren();
for (const l of links) {
  l.line = document.createElementNS(NS, "line");
  l.line.setAttribute("class", "link");
  l.line.setAttribute("marker-end", "url(#arrow)");
  // A summary edge stands for `w` relationships; its width says how many.
  if (l.w) l.line.style.strokeWidth = (1 + Math.log2(l.w)).toFixed(2) + "px";
  gEdges.appendChild(l.line);
  l.text = document.createElementNS(NS, "text");
  l.text.setAttribute("class", "link-label");
  l.text.textContent = l.w ? l.type + " ×" + l.w : l.type;
  gEdgeLabels.appendChild(l.text);
}
for (const n of nodes) {
  const g = document.createElementNS(NS, "g");
  g.setAttribute("class", "node");
  const circle = document.createElementNS(NS, "circle");
  circle.setAttribute("r", radius(n));
  circle.setAttribute("fill", colorOf(n));
  const text = document.createElementNS(NS, "text");
  text.textContent = n.label;
  const title = document.createElementNS(NS, "title");
  title.textContent = n.id;
  g.append(circle, text, title);
  g.addEventListener("pointerdown", ev => startDrag(ev, n));
  g.addEventListener("click", ev => { ev.stopPropagation(); selectNode(n); });
  g.addEventListener("dblclick", ev => {
    ev.stopPropagation();
    n.fx = null; n.fy = null; g.classList.remove("pinned"); reheat(0.4);
  });
  gNodes.appendChild(g);
  n.el = g;
}
}

// ---------- force layout ----------
const REPEL = 22000, SPRING = 0.028, DAMP = 0.82, GRAVITY = 0.004, MAX_STEP = 30;
function step() {
  const active = nodes.filter(nodeShown);
  for (let i = 0; i < active.length; i++) {
    const a = active[i];
    for (let j = i + 1; j < active.length; j++) {
      const b = active[j];
      let dx = a.x - b.x, dy = a.y - b.y;
      let d = Math.hypot(dx, dy);
      if (d < 0.01) { dx = rnd() - 0.5; dy = rnd() - 0.5; d = 0.01; }
      let f = REPEL / (d * d);
      const touch = radius(a) + radius(b) + 10;
      if (d < touch) f += (touch - d) * 0.5;   // collision: circles never overlap
      const ux = dx / d * f * alpha, uy = dy / d * f * alpha;
      a.vx += ux; a.vy += uy; b.vx -= ux; b.vy -= uy;
    }
  }
  for (const l of links) {
    if (!linkShown(l)) continue;
    const a = l.source, b = l.target;
    const dx = b.x - a.x, dy = b.y - a.y;
    const d = Math.hypot(dx, dy) || 0.01;
    const rest = radius(a) + radius(b) + (l.type === "CONTAINS" ? 100 : 180);
    const f = (d - rest) * SPRING * alpha;
    const ux = dx / d * f, uy = dy / d * f;
    a.vx += ux; a.vy += uy; b.vx -= ux; b.vy -= uy;
  }
  for (const n of active) {
    if (n.fx !== null && n.fx !== undefined) {
      n.x = n.fx; n.y = n.fy; n.vx = 0; n.vy = 0; continue;
    }
    n.vx = (n.vx - n.x * GRAVITY * alpha) * DAMP;
    n.vy = (n.vy - n.y * GRAVITY * alpha) * DAMP;
    n.x += Math.max(-MAX_STEP, Math.min(MAX_STEP, n.vx));
    n.y += Math.max(-MAX_STEP, Math.min(MAX_STEP, n.vy));
  }
  alpha *= 0.985;
}

function render() {
  const shownLinks = links.reduce((n, l) => n + (linkShown(l) ? 1 : 0), 0);
  const showLabels = scale >= 0.45 && shownLinks <= 420;
  for (const l of links) {
    const on = linkShown(l);
    l.line.style.display = on ? "" : "none";
    l.text.style.display = on && showLabels ? "" : "none";
    if (!on) continue;
    const a = l.source, b = l.target;
    const dx = b.x - a.x, dy = b.y - a.y;
    const d = Math.hypot(dx, dy) || 0.01;
    const ux = dx / d, uy = dy / d;
    const x1 = a.x + ux * (radius(a) + 2), y1 = a.y + uy * (radius(a) + 2);
    const x2 = b.x - ux * (radius(b) + 7), y2 = b.y - uy * (radius(b) + 7);
    l.line.setAttribute("x1", x1.toFixed(1)); l.line.setAttribute("y1", y1.toFixed(1));
    l.line.setAttribute("x2", x2.toFixed(1)); l.line.setAttribute("y2", y2.toFixed(1));
    if (showLabels) {
      let ang = Math.atan2(dy, dx) * 180 / Math.PI;
      if (ang > 90 || ang < -90) ang += 180;   // labels stay right way up
      l.text.setAttribute("transform",
        "translate(" + ((x1 + x2) / 2).toFixed(1) + "," + ((y1 + y2) / 2).toFixed(1) +
        ") rotate(" + ang.toFixed(1) + ")");
    }
  }
  for (const n of nodes) {
    n.el.style.display = nodeShown(n) ? "" : "none";
    n.el.setAttribute("transform",
      "translate(" + n.x.toFixed(1) + "," + n.y.toFixed(1) + ")");
  }
  view.setAttribute("transform",
    "translate(" + tx.toFixed(1) + "," + ty.toFixed(1) + ") scale(" + scale.toFixed(3) + ")");
}

function frame() {
  step(); render();
  raf = (alpha > 0.02 || drag) ? requestAnimationFrame(frame) : null;
}
function reheat(a) {
  alpha = Math.max(alpha, a === undefined ? 0.6 : a);
  if (!raf) raf = requestAnimationFrame(frame);
}
function fit() {
  const active = nodes.filter(nodeShown);
  if (!active.length) return;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const n of active) {
    const r = radius(n) + 26;
    x0 = Math.min(x0, n.x - r); y0 = Math.min(y0, n.y - r);
    x1 = Math.max(x1, n.x + r); y1 = Math.max(y1, n.y + r);
  }
  const box = svg.getBoundingClientRect();
  scale = Math.max(0.15, Math.min(box.width / (x1 - x0), box.height / (y1 - y0), 1.6));
  tx = box.width / 2 - ((x0 + x1) / 2) * scale;
  ty = box.height / 2 - ((y0 + y1) / 2) * scale;
  render();
}
// How many settle iterations a relayout may run before it gives up and fits
// whatever it has. Overridable per page with data-max-iterations on #stage, so
// a very large graph can be given a longer settle without a rebuild.
const DEFAULT_MAX_ITERATIONS = 500;
// Iterations per animation frame. The browser gets control back between
// batches, so the page stays responsive and the progress bar actually paints.
const SETTLE_BATCH = 20;

function maxIterations() {
  const raw = parseInt(stage.dataset.maxIterations, 10);
  return (Number.isFinite(raw) && raw > 0) ? raw : DEFAULT_MAX_ITERATIONS;
}

function showProgress(done, total) {
  progress.hidden = false;
  progressBar.style.width = Math.round((done / total) * 100) + "%";
}

function relayout() {
  scatter();
  document.querySelectorAll(".node.pinned").forEach(el => el.classList.remove("pinned"));
  alpha = 1;
  // The settle used to run as one synchronous `while` loop, which blocks the
  // browser's main thread for its whole duration: on a large graph the page is
  // frozen -- no paint, no input, no scroll -- and looks hung. Iteration count
  // was capped to bound that, but a bounded freeze is still a freeze.
  //
  // Now it runs in small batches across animation frames. The thread is handed
  // back between batches, so the layout is visibly converging and the page
  // stays interactive while it does. Cancelling an in-flight settle matters
  // too: two overlapping relayouts would otherwise fight over `alpha`.
  if (settleRaf) cancelAnimationFrame(settleRaf);
  const cap = maxIterations();
  let iters = 0;
  function settle() {
    for (let i = 0; i < SETTLE_BATCH && alpha > 0.02 && iters < cap; i++) {
      step(); iters++;
    }
    showProgress(iters, cap);
    render();
    if (alpha > 0.02 && iters < cap) {
      settleRaf = requestAnimationFrame(settle);
      return;
    }
    settleRaf = null;
    progress.hidden = true;
    fit();
  }
  settleRaf = requestAnimationFrame(settle);
}

// ---------- selection + details ----------
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}
function kv(parent, key, value) {
  if (value === undefined || value === null || value === "") return;
  const row = el("div", "kv");
  row.append(el("span", "", key), el("span", "", String(value)));
  parent.appendChild(row);
}
function emptyDetails() {
  panel.replaceChildren(el("p", "sub", "Click a node to see what it is and what it touches."));
}
function showDetails(n) {
  panel.replaceChildren();
  const chip = el("span", "chip", n.type);
  chip.style.background = colorOf(n);
  panel.append(chip, el("h2", "", n.label));
  const box = el("div");
  kv(box, "id", n.id);
  kv(box, "path", n.path);
  kv(box, "kind", n.kind);
  kv(box, "lang", n.lang);
  kv(box, "lines", n.start_line ? n.start_line + "-" + n.end_line : n.lines);
  kv(box, "files", n.files);
  kv(box, "symbols", n.symbols);
  kv(box, "degree", n.deg);
  panel.appendChild(box);
  if (mode === "dirs") {
    kv(box, "holds", n.except ? "all of it except " + n.except.join(", ") : "everything under it");
    const drawn = DATA.nodes.filter(m => underGroup(m, n)).length;
    if (drawn) {
      const open = el("button", "nb", "Show its " + drawn + " drawn nodes in the detail view");
      open.addEventListener("click", () => { dirFilter = n; show("detail"); });
      panel.appendChild(open);
    } else {
      panel.appendChild(el("p", "sub", "None of its nodes made the " + DATA.nodes.length +
        "-node detail view. Raise --viz-nodes, or ask the index: repo2graph query."));
    }
    if (n.top && n.top.length) {
      panel.append(el("h2", "", "best-connected inside"), el("pre", "", n.top.join("\n")));
    }
  }
  if (n.sig) { panel.append(el("h2", "", "signature"), el("pre", "", n.sig)); }
  if (n.doc) { panel.append(el("h2", "", "doc"), el("pre", "", n.doc)); }

  const list = neighbours(n.id);
  panel.appendChild(el("h2", "", "connections (" + list.length + ")"));
  for (const link of list.slice(0, 80)) {
    const b = el("button", "nb");
    const dot = el("span", "swatch");
    dot.style.background = colorOf(link.other);
    dot.style.display = "inline-block";
    dot.style.marginRight = "6px";
    b.append(dot, document.createTextNode(
      (link.dir === "out" ? "-[" : "<-[") + link.type + (link.dir === "out" ? "]-> " : "]- ") +
      link.other.label));
    b.addEventListener("click", () => selectNode(link.other, true));
    panel.appendChild(b);
  }
}
// ---------- focus mode + search: opacity combines, most restrictive wins ----------
// focus mode: clicked node 1, 1-hop .8, 2-hop .4, everything else .1
// search mode: matching 1, non-matching .15
// A node under both dims to whichever number is lower -- Math.min, per node.
const FOCUS_OPACITY = [1, 0.8, 0.4];
const FOCUS_FAR_OPACITY = 0.1;
const SEARCH_MISS_OPACITY = 0.15;
let focusNode = null;
let focusDist = new Map();
let searchQuery = "";

function computeFocusDistances(n) {
  const dist = new Map([[n.id, 0]]);
  const frontier = neighbours(n.id).map(l => l.other);
  for (const m of frontier) if (!dist.has(m.id)) dist.set(m.id, 1);
  for (const m of frontier) {
    for (const link of neighbours(m.id)) {
      if (!dist.has(link.other.id)) dist.set(link.other.id, 2);
    }
  }
  return dist;
}
function opacityFor(n) {
  let o = 1;
  if (focusNode) {
    const d = focusDist.get(n.id);
    o = Math.min(o, d === undefined ? FOCUS_FAR_OPACITY : FOCUS_OPACITY[d]);
  }
  if (searchQuery) {
    o = Math.min(o, n.hay.includes(searchQuery) ? 1 : SEARCH_MISS_OPACITY);
  }
  return o;
}
function applyOpacity() {
  for (const n of nodes) {
    n.el.style.opacity = opacityFor(n);
    n.el.classList.toggle("hit", searchQuery !== "" && n.hay.includes(searchQuery));
    n.el.classList.toggle("focused", focusNode === n);
  }
}
function clearFocus() {
  focusNode = null;
  focusDist = new Map();
  applyOpacity();
}

function selectNode(n, centre) {
  selected = n;
  focusNode = n;
  focusDist = computeFocusDistances(n);
  applyOpacity();
  for (const l of links) {
    const on = l.source.id === n.id || l.target.id === n.id;
    l.line.classList.toggle("dim", !on);
    l.text.classList.toggle("dim", !on);
  }
  if (centre) centreOn(n);
  showDetails(n);
}
function clearSelection() {
  selected = null;
  clearFocus();
  for (const l of links) { l.line.classList.remove("dim"); l.text.classList.remove("dim"); }
  emptyDetails();
}
function centreOn(n) {
  const box = svg.getBoundingClientRect();
  tx = box.width / 2 - n.x * scale;
  ty = box.height / 2 - n.y * scale;
  render();
}

// ---------- controls ----------
function legendRow(host, key, count, colour, kind, desc) {
  const wrap = el("div");
  const label = el("label");
  const box = el("input");
  box.type = "checkbox";
  box.checked = !(kind === "node" ? hiddenNodeTypes : hiddenEdgeTypes).has(key);
  box.addEventListener("change", () => {
    const hidden = kind === "node" ? hiddenNodeTypes : hiddenEdgeTypes;
    if (box.checked) hidden.delete(key); else hidden.add(key);
    reheat(0.5);
  });
  const mark = el("span", kind === "node" ? "swatch" : "bar");
  if (colour) mark.style.background = colour;
  label.append(box, mark, el("span", "", key), el("span", "count", count));
  wrap.appendChild(label);
  if (desc) wrap.appendChild(el("div", "legend-desc", desc));
  host.appendChild(wrap);
}
function langRow(host, lang, count) {
  const label = el("label");
  const box = el("input");
  box.type = "checkbox";
  box.checked = !hiddenLangs.has(lang);
  box.addEventListener("change", () => {
    if (box.checked) hiddenLangs.delete(lang); else hiddenLangs.add(lang);
    reheat(0.5);
  });
  label.append(box, el("span", "", lang), el("span", "count", count));
  host.appendChild(label);
}
function legends() {
  const nodeLegend = document.getElementById("node-legend");
  const edgeLegend = document.getElementById("edge-legend");
  const langLegend = document.getElementById("lang-legend");
  nodeLegend.replaceChildren(); edgeLegend.replaceChildren(); langLegend.replaceChildren();
  for (const [type, count] of current.nodeTypes) {
    legendRow(nodeLegend, type, count, DATA.colors[type] || DATA.colors._, "node",
              DATA.nodeDesc[type]);
  }
  for (const [type, count] of current.edgeTypes) {
    legendRow(edgeLegend, type, count, null, "edge", DATA.edgeDesc[type]);
  }
  const langs = new Map();
  for (const n of nodes) if (n.lang) langs.set(n.lang, (langs.get(n.lang) || 0) + 1);
  const sorted = [...langs].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1));
  for (const [lang, count] of sorted) langRow(langLegend, lang, count);
  document.getElementById("lang-pad").hidden = sorted.length < 2;
}
function counts() {
  document.getElementById("counts").textContent = mode === "dirs"
    ? nodes.length + " directories summarising " + DATA.totals.nodes + " nodes · " +
      links.length + " links"
    : nodes.length + " of " + DATA.totals.nodes + " nodes · " +
      links.length + " of " + DATA.totals.edges + " edges";
}
// Said on the page itself, not only by the CLI: a sampled map that looks
// complete is a wrong answer (#305).
const banner = document.getElementById("banner");
function bannerText() {
  banner.replaceChildren();
  const s = DATA.sampled;
  if (mode === "detail" && dirFilter !== null) {
    banner.append("Only the drawn nodes in " + (dirFilter.path || "(root)") +
      (dirFilter.except ? ", except its subdirectories shown separately" : "") + ".");
    const clear = el("button", "", "Show all");
    clear.addEventListener("click", () => { dirFilter = null; show("detail"); });
    banner.appendChild(clear);
  } else if (mode === "dirs") {
    banner.append("Summary: every node, counted in its directory. A link is the " +
      "relationships between two directories; its width is how many. A name ending /… " +
      "is the rest of a directory whose biggest parts are shown separately. " +
      "Click a directory to see inside it.");
  } else if (s) {
    banner.append("Sampled: drawing " + s.nodes + " of " + DATA.totals.nodes +
      " nodes — " + s.rule + "." + (s.shrunk_from !== undefined
        ? " Cut from " + s.shrunk_from + " to fit the page size limit." : "") +
      (DATA.dirs ? " The Directories view counts all of them." : ""));
  }
  banner.hidden = !banner.childNodes.length;
}
const btnDirs = document.getElementById("btn-dirs");
const btnDetail = document.getElementById("btn-detail");
function show(next) {
  if (settleRaf) { cancelAnimationFrame(settleRaf); settleRaf = null; }
  if (raf) { cancelAnimationFrame(raf); raf = null; }
  mode = next;
  current = mode === "dirs" ? DATA.dirs : DATA;
  nodes = current.nodes; links = current.edges;
  selected = null; focusNode = null; focusDist = new Map();
  model(); draw(); legends(); counts(); bannerText();
  btnDirs.classList.toggle("on", mode === "dirs");
  btnDetail.classList.toggle("on", mode === "detail");
  emptyDetails();
  relayout();
}
document.getElementById("view-btns").hidden = !DATA.dirs;
btnDirs.addEventListener("click", () => show("dirs"));
btnDetail.addEventListener("click", () => { dirFilter = null; show("detail"); });

// ---------- legend collapse, persisted per-viewer ----------
const legendToggle = document.getElementById("legend-toggle");
const legendBody = document.getElementById("legend-body");
const legendChevron = document.getElementById("legend-chevron");
const LEGEND_COLLAPSE_KEY = "r2g-legend-collapsed";
function setLegendCollapsed(collapsed) {
  legendBody.classList.toggle("collapsed", collapsed);
  legendChevron.textContent = collapsed ? "▸" : "▾";
  try { localStorage.setItem(LEGEND_COLLAPSE_KEY, collapsed ? "1" : "0"); } catch (_) {}
}
let legendCollapsed = false;
try { legendCollapsed = localStorage.getItem(LEGEND_COLLAPSE_KEY) === "1"; } catch (_) {}
setLegendCollapsed(legendCollapsed);
legendToggle.addEventListener("click", () => {
  setLegendCollapsed(!legendBody.classList.contains("collapsed"));
});

// ---------- search: highlight, dropdown, pan/zoom to a result ----------
const search = document.getElementById("search");
const searchWrap = document.getElementById("search-wrap");
const searchResults = document.getElementById("search-results");
function matchesFor(q) {
  return q ? nodes.filter(n => n.hay.includes(q)).slice(0, 30) : [];
}
function renderSearchResults(q) {
  searchResults.replaceChildren();
  const matches = matchesFor(q);
  if (!matches.length) { searchResults.hidden = true; return; }
  for (const n of matches) {
    const row = el("div", "search-result");
    row.tabIndex = 0;
    const dot = el("span", "swatch");
    dot.style.background = colorOf(n);
    row.append(dot, document.createTextNode(n.label + "  ·  " + n.type));
    row.addEventListener("click", () => {
      searchResults.hidden = true;
      selectNode(n, true);
    });
    searchResults.appendChild(row);
  }
  searchResults.hidden = false;
}
function applySearch(q) {
  searchQuery = q;
  applyOpacity();
  renderSearchResults(q);
}
search.addEventListener("input", () => applySearch(search.value.trim().toLowerCase()));
search.addEventListener("keydown", ev => {
  if (ev.key !== "Enter") return;
  const q = search.value.trim().toLowerCase();
  const hit = q && nodes.find(n => nodeShown(n) && n.hay.includes(q));
  if (hit) { searchResults.hidden = true; selectNode(hit, true); }
});
document.addEventListener("pointerdown", ev => {
  if (!searchWrap.contains(ev.target)) searchResults.hidden = true;
});
// Cmd/Ctrl+F focuses the search box instead of the browser's own find-in-page;
// Escape clears whichever of search / focus mode is active, search box first.
window.addEventListener("keydown", ev => {
  if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === "f") {
    ev.preventDefault();
    search.focus();
    search.select();
    return;
  }
  if (ev.key !== "Escape") return;
  if (document.activeElement === search && search.value) {
    search.value = "";
    applySearch("");
    searchResults.hidden = true;
  } else if (focusNode) {
    clearSelection();
  }
});

document.getElementById("btn-fit").addEventListener("click", fit);
document.getElementById("btn-relayout").addEventListener("click", relayout);
document.getElementById("btn-unpin").addEventListener("click", () => {
  for (const n of nodes) { n.fx = null; n.fy = null; n.el.classList.remove("pinned"); }
  reheat(0.6);
});
document.getElementById("btn-clear-focus").addEventListener("click", clearSelection);

// ---------- pointer: drag nodes, pan and zoom the canvas ----------
function toGraph(ev) {
  const box = svg.getBoundingClientRect();
  return [(ev.clientX - box.left - tx) / scale, (ev.clientY - box.top - ty) / scale];
}
function startDrag(ev, n) {
  ev.stopPropagation();
  drag = n;
  n.fx = n.x; n.fy = n.y;
  n.el.classList.add("pinned");
  svg.setPointerCapture(ev.pointerId);
  reheat(0.4);
}
svg.addEventListener("pointerdown", ev => {
  if (ev.target.closest(".node")) return;
  pan = { x: ev.clientX, y: ev.clientY, tx: tx, ty: ty, moved: false };
  svg.classList.add("panning");
  svg.setPointerCapture(ev.pointerId);
});
svg.addEventListener("pointermove", ev => {
  if (drag) {
    const p = toGraph(ev);
    drag.fx = p[0]; drag.fy = p[1];
    reheat(0.3);
  } else if (pan) {
    tx = pan.tx + (ev.clientX - pan.x);
    ty = pan.ty + (ev.clientY - pan.y);
    if (Math.abs(ev.clientX - pan.x) + Math.abs(ev.clientY - pan.y) > 3) pan.moved = true;
    render();
  }
});
svg.addEventListener("pointerup", ev => {
  if (pan && !pan.moved && selected) clearSelection();
  drag = null; pan = null;
  svg.classList.remove("panning");
  try { svg.releasePointerCapture(ev.pointerId); } catch (_) {}
});
svg.addEventListener("pointercancel", ev => {
  drag = null; pan = null;
  svg.classList.remove("panning");
  try { svg.releasePointerCapture(ev.pointerId); } catch (_) {}
});
svg.addEventListener("wheel", ev => {
  ev.preventDefault();
  const box = svg.getBoundingClientRect();
  const mx = ev.clientX - box.left, my = ev.clientY - box.top;
  const next = Math.max(0.12, Math.min(4, scale * Math.exp(-ev.deltaY * 0.0015)));
  tx = mx - (mx - tx) * (next / scale);
  ty = my - (my - ty) * (next / scale);
  scale = next;
  render();
}, { passive: false });
window.addEventListener("resize", render);

show(DATA.dirs ? "dirs" : "detail");
</script>
</body>
</html>
"""
