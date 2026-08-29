# @authormark v1 -- do not remove (authorship watermark)⁠​‌​​‌‌‌‌​​‌‌‌​​‌​‌‌‌‌​‌​​​‌‌​‌‌​​‌​​​‌‌‌​‌​‌‌​​‌​​‌‌​​‌‌​‌‌‌‌​‌​​‌​​​​‌‌​‌​​​‌‌‌​‌‌​‌‌​​​‌​‌​​‌​​‌‌‌​​​​​‌‌​‌​​‌​‌​‌‌​​​​‌‌‌​‌​​​‌‌​‌‌​​​​‌‌​‌​‌​‌‌‌‌​​‌​‌​‌‌​​‌​‌​​​​‌‌​‌‌‌‌​​‌⁠
# Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
# Author: https://github.com/Srinivasan-78
# SPDX-License-Identifier: MIT
# Fingerprint: AMK1.O9z6GY3zCGlRpiXtl5yYCy
"""Interactive knowledge-graph map: one self-contained HTML file, no CDN, no build step."""
import html
import json
from collections import Counter
from pathlib import Path

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
    "CALLS": 3.0, "IMPORTS": 3.0, "INHERITS": 3.0, "CO_CHANGE": 2.0,
    "DEFINES": 1.0, "CALLS_EXTERNAL": 0.75, "CONTAINS": 0.5,
}
MAX_NODES = 300
LABEL_CHARS = 15
# Stdlib and third-party call targets triple the edge count and tell you little
# about the repo itself, so the map opens without them; the legend turns them on.
HIDDEN_NODE_TYPES = ["external"]
HIDDEN_EDGE_TYPES = ["CALLS_EXTERNAL"]


def _trim(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def node_label(n: dict) -> str:
    text = n.get("qualname") or n.get("name") or n.get("path") or n["id"]
    return _trim(text, LABEL_CHARS)


def select(nodes: dict, edges: list, max_nodes: int = MAX_NODES):
    """Keep the max_nodes best-connected nodes, and the edges between them.

    A force layout stops being readable long before a real repo stops having
    nodes, so the map shows the hubs: rank by edge weight, drop the tail.
    """
    score: Counter = Counter()
    for e in edges:
        w = EDGE_WEIGHT.get(e["type"], 1.0)
        score[e["src"]] += w
        score[e["dst"]] += w
    if max_nodes and len(nodes) > max_nodes:
        ranked = sorted(nodes.values(), key=lambda n: (-score[n["id"]], n["id"]))
        keep = {n["id"] for n in ranked[:max_nodes]}
    else:
        keep = set(nodes)
    kept_nodes = [n for n in nodes.values() if n["id"] in keep]
    kept_edges = [e for e in edges if e["src"] in keep and e["dst"] in keep]
    return kept_nodes, kept_edges


def payload(g, max_nodes: int = MAX_NODES) -> dict:
    """The JSON the page draws: nodes, edges by index, legend counts."""
    nodes, edges = select(g.nodes, g.edges, max_nodes)
    index = {n["id"]: i for i, n in enumerate(nodes)}
    deg: Counter = Counter()
    for e in edges:
        deg[e["src"]] += 1
        deg[e["dst"]] += 1

    out_nodes = []
    for n in nodes:
        item = {"id": n["id"], "label": node_label(n), "type": n["type"],
                "deg": deg[n["id"]]}
        for key in ("path", "kind", "lang", "start_line", "end_line", "lines"):
            if n.get(key) not in (None, "", []):
                item[key] = n[key]
        if n.get("signature"):
            item["sig"] = _trim(n["signature"], 300)
        if n.get("docstring"):
            item["doc"] = _trim(n["docstring"], 400)
        out_nodes.append(item)

    return {
        "name": g.name,
        "nodes": out_nodes,
        "edges": [{"s": index[e["src"]], "t": index[e["dst"]], "type": e["type"]}
                  for e in edges],
        "colors": {**NODE_COLORS, "_": OTHER_COLOR},
        "hidden": {"nodes": HIDDEN_NODE_TYPES, "edges": HIDDEN_EDGE_TYPES},
        "nodeTypes": sorted(Counter(n["type"] for n in out_nodes).items()),
        "edgeTypes": sorted(Counter(e["type"] for e in edges).items()),
        "totals": {"nodes": len(g.nodes), "edges": len(g.edges)},
    }


def write_html(g, path: Path, max_nodes: int = MAX_NODES) -> dict:
    data = payload(g, max_nodes)
    blob = (json.dumps(data, ensure_ascii=False)
            .replace("</", "<\\/")            # never close the <script> early
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
    page = (TEMPLATE
            .replace("__R2G_TITLE__", html.escape(str(g.name)))
            .replace("__R2G_DATA__", blob))
    Path(path).write_text(page, encoding="utf8")
    return data


class LoadedGraph:
    """The parts of Graph the map needs, read back from nodes/edges.jsonl."""

    def __init__(self, outdir: Path):
        from .layout import path as artifact_path
        from .query import read_jsonl
        outdir = Path(outdir)
        self.name = outdir.name
        self.nodes = {n["id"]: n
                      for n in read_jsonl(artifact_path(outdir, "nodes.jsonl"))}
        self.edges = read_jsonl(artifact_path(outdir, "edges.jsonl"))
        overview = artifact_path(outdir, "overview.md")
        if overview.exists():
            first = overview.read_text(encoding="utf8").split("\n", 1)[0]
            self.name = first.removeprefix("# Repo map:").strip() or self.name
        index = artifact_path(outdir, "index.json")
        if index.exists():
            self.name = json.loads(index.read_text(encoding="utf8")).get("repo", self.name)


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
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
  #search {
    width: 100%; padding: 7px 9px; border: 1px solid var(--line); border-radius: 6px;
    font: inherit; margin-top: 9px; background: #fff; color: inherit;
  }
  #search:focus { outline: 2px solid rgba(47,111,237,.35); border-color: var(--accent); }
  .legend { display: flex; flex-direction: column; gap: 4px; }
  .legend label { display: flex; align-items: center; gap: 7px; cursor: pointer; }
  .legend input { margin: 0; }
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
  .dim { opacity: .18; }
  .fade { opacity: .18; }
  #hud {
    position: absolute; right: 12px; bottom: 10px; color: var(--muted); font-size: 11px;
    background: rgba(255,255,255,.85); padding: 4px 8px; border-radius: 6px;
  }
</style>
</head>
<body>
<div id="app">
  <aside id="side">
    <div class="pad">
      <h1>__R2G_TITLE__</h1>
      <div class="sub" id="counts"></div>
      <input id="search" type="search" placeholder="Search nodes (Enter = focus)" autocomplete="off">
    </div>
    <div class="pad">
      <h2>Nodes</h2>
      <div class="legend" id="node-legend"></div>
    </div>
    <div class="pad">
      <h2>Relationships</h2>
      <div class="legend" id="edge-legend"></div>
    </div>
    <div class="pad btns">
      <button id="btn-fit">Fit</button>
      <button id="btn-relayout">Re-layout</button>
      <button id="btn-unpin">Unpin all</button>
    </div>
    <div id="details"></div>
  </aside>
  <div id="stage">
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
const nodes = DATA.nodes, links = DATA.edges;
const svg = document.getElementById("svg");
const view = document.getElementById("view");
const gEdges = document.getElementById("g-edges");
const gEdgeLabels = document.getElementById("g-edge-labels");
const gNodes = document.getElementById("g-nodes");
const panel = document.getElementById("details");

const colorOf = n => DATA.colors[n.type] || DATA.colors._;
const radius = n => 13 + Math.min(20, Math.sqrt(n.deg || 1) * 3.2);
const hiddenNodeTypes = new Set(DATA.hidden.nodes);
const hiddenEdgeTypes = new Set(DATA.hidden.edges);
const nodeShown = n => !hiddenNodeTypes.has(n.type);
const linkShown = l => !hiddenEdgeTypes.has(l.type) && nodeShown(l.source) && nodeShown(l.target);

let tx = 0, ty = 0, scale = 1, alpha = 1, raf = null, drag = null, pan = null, selected = null;

// ---------- model ----------
const adjacency = new Map();
function neighbours(id) {
  let list = adjacency.get(id);
  if (!list) { list = []; adjacency.set(id, list); }
  return list;
}
for (const l of links) {
  l.source = nodes[l.s];
  l.target = nodes[l.t];
  neighbours(l.source.id).push({ other: l.target, type: l.type, dir: "out" });
  neighbours(l.target.id).push({ other: l.source, type: l.type, dir: "in" });
}
for (const n of nodes) n.hay = (n.id + " " + n.label + " " + (n.path || "")).toLowerCase();

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
for (const l of links) {
  l.line = document.createElementNS(NS, "line");
  l.line.setAttribute("class", "link");
  l.line.setAttribute("marker-end", "url(#arrow)");
  gEdges.appendChild(l.line);
  l.text = document.createElementNS(NS, "text");
  l.text.setAttribute("class", "link-label");
  l.text.textContent = l.type;
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
function relayout() {
  scatter();
  document.querySelectorAll(".node.pinned").forEach(el => el.classList.remove("pinned"));
  alpha = 1;
  // Settle the whole layout off-screen, so the first paint is the final one and
  // nothing drifts out of the frame after fit() has measured it.
  while (alpha > 0.02) step();
  fit();
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
  kv(box, "degree", n.deg);
  panel.appendChild(box);
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
function selectNode(n, centre) {
  selected = n;
  const near = new Set([n.id]);
  for (const link of neighbours(n.id)) near.add(link.other.id);
  for (const m of nodes) {
    m.el.classList.remove("fade");   // selection dimming replaces search dimming
    m.el.classList.toggle("dim", !near.has(m.id));
  }
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
  for (const m of nodes) m.el.classList.remove("dim");
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
function legendRow(host, key, count, colour, kind) {
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
  host.appendChild(label);
}
for (const [type, count] of DATA.nodeTypes) {
  legendRow(document.getElementById("node-legend"), type, count,
            DATA.colors[type] || DATA.colors._, "node");
}
for (const [type, count] of DATA.edgeTypes) {
  legendRow(document.getElementById("edge-legend"), type, count, null, "edge");
}
document.getElementById("counts").textContent =
  nodes.length + " of " + DATA.totals.nodes + " nodes · " +
  links.length + " of " + DATA.totals.edges + " edges";

const search = document.getElementById("search");
search.addEventListener("input", () => {
  const q = search.value.trim().toLowerCase();
  for (const n of nodes) {
    const hit = q !== "" && n.hay.includes(q);
    n.el.classList.toggle("hit", hit);
    n.el.classList.toggle("fade", q !== "" && !hit);
  }
});
search.addEventListener("keydown", ev => {
  if (ev.key !== "Enter") return;
  const q = search.value.trim().toLowerCase();
  const hit = q && nodes.find(n => nodeShown(n) && n.hay.includes(q));
  if (hit) selectNode(hit, true);
});
document.getElementById("btn-fit").addEventListener("click", fit);
document.getElementById("btn-relayout").addEventListener("click", relayout);
document.getElementById("btn-unpin").addEventListener("click", () => {
  for (const n of nodes) { n.fx = null; n.fy = null; n.el.classList.remove("pinned"); }
  reheat(0.6);
});

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
});
svg.addEventListener("pointercancel", () => { drag = null; pan = null; });
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

emptyDetails();
relayout();
</script>
</body>
</html>
"""
