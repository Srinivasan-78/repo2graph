"""Serialize the graph: JSONL, GraphML, Cypher, overview, HTML map."""
import json
from pathlib import Path

from .viz import MAX_NODES, write_html

SCALAR = (str, int, float, bool)


def _flat(d: dict) -> dict:
    return {k: (v if isinstance(v, SCALAR) else json.dumps(v))
            for k, v in d.items() if v is not None}


def write_jsonl(path: Path, rows):
    with open(path, "w", encoding="utf8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


# yEd draws whatever geometry the file carries, and networkx writes none, so a
# plain export opens as one stack of boxes at the origin. Lay the graph out here
# and ship yFiles node/edge graphics alongside the data keys.
Y_NS = "http://www.yworks.com/xml/graphml"
GRAPHML_NS = "http://graphml.graphdrawing.org/xmlns"
NODE_HEIGHT = 26.0
CHAR_WIDTH = 7.0
LABEL_CHARS = 40


def _spring(nodes, adjacency, iterations):
    """Fruchterman-Reingold in pure Python: networkx's needs numpy, we do not."""
    import math
    import random
    n = len(nodes)
    rng = random.Random(17)
    pos = {nid: [rng.uniform(-1.0, 1.0), rng.uniform(-1.0, 1.0)] for nid in nodes}
    k = 1.0 / math.sqrt(n)
    temp = 0.1
    cooling = temp / (iterations + 1)
    for _ in range(iterations):
        disp = {nid: [0.0, 0.0] for nid in nodes}
        for i, a in enumerate(nodes):
            ax, ay = pos[a]
            for b in nodes[i + 1:]:
                dx, dy = ax - pos[b][0], ay - pos[b][1]
                dist2 = dx * dx + dy * dy
                if dist2 < 1e-9:
                    dx, dy = rng.uniform(-1e-3, 1e-3), rng.uniform(-1e-3, 1e-3)
                    dist2 = dx * dx + dy * dy
                force = k * k / dist2          # repulsion, 1/d scaled by 1/d
                disp[a][0] += dx * force
                disp[a][1] += dy * force
                disp[b][0] -= dx * force
                disp[b][1] -= dy * force
        for a, b in adjacency:
            dx, dy = pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]
            dist = math.hypot(dx, dy) or 1e-6
            force = dist * dist / k            # attraction along the edge
            ux, uy = dx / dist * force, dy / dist * force
            disp[a][0] -= ux
            disp[a][1] -= uy
            disp[b][0] += ux
            disp[b][1] += uy
        for nid in nodes:
            dx, dy = disp[nid]
            dist = math.hypot(dx, dy) or 1e-6
            step = min(dist, temp)
            pos[nid][0] += dx / dist * step
            pos[nid][1] += dy / dist * step
        temp -= cooling
    return {nid: (xy[0], xy[1]) for nid, xy in pos.items()}


def _grid(nodes):
    """Deterministic fallback for graphs too big to force-lay-out in Python."""
    import math
    side = max(1, math.ceil(math.sqrt(len(nodes))))
    return {nid: ((i % side) / side - 0.5, (i // side) / side - 0.5)
            for i, nid in enumerate(nodes)}


# Force layout is O(n^2) per iteration in Python, so past a few thousand nodes
# a grid beats waiting minutes for the export.
SPRING_MAX_NODES = 1200


def _layout(G, sizes):
    """Node positions in points, spread so labels do not collide."""
    nodes = list(G)
    n = len(nodes)
    if n == 0:
        return {}
    if n == 1:
        return {nodes[0]: (0.0, 0.0)}
    if n <= SPRING_MAX_NODES:
        adjacency = [(u, v) for u, v in G.to_undirected().edges() if u != v]
        iterations = 120 if n <= 400 else 50
        pos = _spring(nodes, adjacency, iterations)
    else:
        pos = _grid(nodes)
    # Scale the unit layout so the median node box fits between neighbours.
    span = max(sum(w for w, _ in sizes.values()) / n * 2.0, 160.0) * (n ** 0.5)
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    width = (max(xs) - min(xs)) or 1.0
    height = (max(ys) - min(ys)) or 1.0
    pos = {nid: ((x - min(xs)) / width * span, (y - min(ys)) / height * span)
           for nid, (x, y) in pos.items()}
    return _separate(pos, sizes, 200 if n <= 400 else 60 if n <= 800 else 25)


def _node_size(label: str, degree: int) -> tuple[float, float]:
    """Box a node needs: wide enough for its label, bigger for hubs."""
    scale = min(2.5, 1.0 + degree / 40.0)
    return max(60.0, len(label) * CHAR_WIDTH + 16.0) * scale, NODE_HEIGHT * scale


def _separate(pos, sizes, iterations):
    """Push overlapping boxes apart; the spring layout only knows points."""
    nodes = list(pos)
    pad = 16.0
    for _ in range(iterations):
        shift = {nid: [0.0, 0.0] for nid in nodes}
        overlaps = 0
        for i, a in enumerate(nodes):
            ax, ay = pos[a]
            aw, ah = sizes[a]
            for b in nodes[i + 1:]:
                bx, by = pos[b]
                bw, bh = sizes[b]
                gap_x = (aw + bw) / 2 + pad - abs(ax - bx)
                gap_y = (ah + bh) / 2 + pad - abs(ay - by)
                if gap_x <= 0 or gap_y <= 0:
                    continue
                overlaps += 1
                # Separate along the axis that needs the smaller move.
                if gap_x < gap_y:
                    push = gap_x / 2 * (1.0 if ax >= bx else -1.0)
                    shift[a][0] += push
                    shift[b][0] -= push
                else:
                    push = gap_y / 2 * (1.0 if ay >= by else -1.0)
                    shift[a][1] += push
                    shift[b][1] -= push
        if not overlaps:
            break
        # Apply every pair's push at once, so a node squeezed by two
        # neighbours settles between them instead of ping-ponging.
        for nid in nodes:
            dx, dy = shift[nid]
            pos[nid] = (pos[nid][0] + dx, pos[nid][1] + dy)
    return pos


def _graphml_label(n: dict) -> str:
    text = n.get("qualname") or n.get("name") or n.get("path") or n["id"]
    text = " ".join(str(text).split())
    return text if len(text) <= LABEL_CHARS else text[: LABEL_CHARS - 1] + "\u2026"


def write_graphml(g, path: Path):
    import xml.etree.ElementTree as ET

    import networkx as nx

    from .viz import NODE_COLORS, OTHER_COLOR

    G = nx.MultiDiGraph()
    for nid, n in g.nodes.items():
        G.add_node(nid, **_flat(n))
    for e in g.edges:
        attrs = _flat({k: v for k, v in e.items() if k not in ("src", "dst")})
        G.add_edge(e["src"], e["dst"], **attrs)

    degree = dict(G.degree())
    labels = {nid: _graphml_label(n) for nid, n in g.nodes.items()}
    sizes = {nid: _node_size(labels[nid], degree.get(nid, 0)) for nid in G}
    pos = _layout(G, sizes)

    ET.register_namespace("", GRAPHML_NS)
    ET.register_namespace("y", Y_NS)
    root = ET.Element(f"{{{GRAPHML_NS}}}graphml")

    # One data key per attribute name, typed from the values it carries.
    keys: dict[tuple[str, str], str] = {}

    def key_for(scope: str, name: str, value) -> str:
        ident = keys.get((scope, name))
        if ident is None:
            ident = f"d{len(keys)}"
            keys[(scope, name)] = ident
            kind = ("boolean" if isinstance(value, bool) else
                    "long" if isinstance(value, int) else
                    "double" if isinstance(value, float) else "string")
            ET.SubElement(root, f"{{{GRAPHML_NS}}}key", {
                "id": ident, "for": scope, "attr.name": name, "attr.type": kind})
        return ident

    graph = ET.Element(f"{{{GRAPHML_NS}}}graph",
                       {"id": str(g.name), "edgedefault": "directed"})

    def add_data(parent, scope: str, attrs: dict):
        for name, value in attrs.items():
            data = ET.SubElement(parent, f"{{{GRAPHML_NS}}}data",
                                 {"key": key_for(scope, name, value)})
            data.text = "true" if value is True else "false" if value is False else str(value)

    for nid, attrs in G.nodes(data=True):
        node = ET.SubElement(graph, f"{{{GRAPHML_NS}}}node", {"id": nid})
        add_data(node, "node", attrs)
        label = labels[nid]
        x, y = pos.get(nid, (0.0, 0.0))
        width, height = sizes[nid]
        gfx = ET.SubElement(node, f"{{{GRAPHML_NS}}}data",
                            {"key": key_for("node", "nodegraphics", "")})
        shape = ET.SubElement(gfx, f"{{{Y_NS}}}ShapeNode")
        ET.SubElement(shape, f"{{{Y_NS}}}Geometry", {
            "x": f"{x - width / 2:.2f}", "y": f"{y - height / 2:.2f}",
            "width": f"{width:.2f}", "height": f"{height:.2f}"})
        ET.SubElement(shape, f"{{{Y_NS}}}Fill", {
            "color": NODE_COLORS.get(attrs.get("type"), OTHER_COLOR),
            "transparent": "false"})
        ET.SubElement(shape, f"{{{Y_NS}}}BorderStyle",
                      {"color": "#4a4f57", "type": "line", "width": "1.0"})
        text = ET.SubElement(shape, f"{{{Y_NS}}}NodeLabel", {
            "alignment": "center", "fontSize": "11", "textColor": "#1c2330",
            "visible": "true"})
        text.text = label
        ET.SubElement(shape, f"{{{Y_NS}}}Shape", {
            "type": "ellipse" if attrs.get("type") == "symbol" else "roundrectangle"})

    for src, dst, attrs in G.edges(data=True):
        edge = ET.SubElement(graph, f"{{{GRAPHML_NS}}}edge",
                             {"source": src, "target": dst})
        add_data(edge, "edge", attrs)
        gfx = ET.SubElement(edge, f"{{{GRAPHML_NS}}}data",
                            {"key": key_for("edge", "edgegraphics", "")})
        poly = ET.SubElement(gfx, f"{{{Y_NS}}}PolyLineEdge")
        ET.SubElement(poly, f"{{{Y_NS}}}LineStyle",
                      {"color": "#a5adba", "type": "line", "width": "1.0"})
        ET.SubElement(poly, f"{{{Y_NS}}}Arrows",
                      {"source": "none", "target": "standard"})
        ET.SubElement(poly, f"{{{Y_NS}}}BendStyle", {"smoothed": "false"})

    # yFiles keys carry graphics, not data, and take yfiles.type instead of
    # attr.name/attr.type; fix them up now that every key exists.
    for element in root.findall(f"{{{GRAPHML_NS}}}key"):
        name = element.get("attr.name")
        if name in ("nodegraphics", "edgegraphics"):
            del element.attrib["attr.name"]
            del element.attrib["attr.type"]
            element.set("yfiles.type", name)

    root.append(graph)
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return G


def _cy(v):
    return json.dumps(v if isinstance(v, SCALAR) else json.dumps(v))


def write_cypher(g, path: Path):
    lines = ["CREATE CONSTRAINT r2g_id IF NOT EXISTS "
             "FOR (n:R2G) REQUIRE n.id IS UNIQUE;"]
    for nid, n in g.nodes.items():
        lab = n["type"].capitalize()
        props = ", ".join(f"{k}: {_cy(v)}" for k, v in n.items() if k != "type")
        lines.append(f"MERGE (n:R2G:{lab} {{id: {_cy(nid)}}}) SET n += {{{props}}};")
    for e in g.edges:
        props = {k: v for k, v in e.items() if k not in ("src", "dst", "type")}
        pstr = (" {" + ", ".join(f"{k}: {_cy(v)}" for k, v in props.items()) + "}") if props else ""
        lines.append(
            f"MATCH (a:R2G {{id: {_cy(e['src'])}}}), (b:R2G {{id: {_cy(e['dst'])}}}) "
            f"MERGE (a)-[:{e['type']}{pstr}]->(b);")
    path.write_text("\n".join(lines), encoding="utf8")


def write_overview(g, path: Path, top: int = 25):
    """Human/LLM-readable repo map: top directories, hub files, entry points."""
    from collections import Counter
    indeg, outdeg = Counter(), Counter()
    for e in g.edges:
        if e["type"] in ("IMPORTS", "CALLS"):
            indeg[e["dst"]] += 1
            outdeg[e["src"]] += 1
    files = [n for n in g.nodes.values() if n["type"] == "file"]
    langs = Counter(n.get("lang") for n in files)
    hubs = sorted((n for n in g.nodes.values() if n["type"] == "file"),
                  key=lambda n: -indeg[n["id"]])[:top]
    key_syms = sorted((n for n in g.nodes.values() if n["type"] == "symbol"),
                      key=lambda n: -indeg[n["id"]])[:top]
    out = [f"# Repo map: {g.name}", "",
           f"files: {len(files)}  nodes: {len(g.nodes)}  edges: {len(g.edges)}",
           "languages: " + ", ".join(f"{k}={v}" for k, v in langs.most_common(12) if k), "",
           "## Most depended-on files"]
    out += [f"- {n['path']} (in={indeg[n['id']]})" for n in hubs if indeg[n["id"]]]
    out += ["", "## Most called symbols"]
    out += [f"- {n['path']}::{n['qualname']} ({n['kind']}, in={indeg[n['id']]})"
            for n in key_syms if indeg[n["id"]]]
    path.write_text("\n".join(out), encoding="utf8")


def dump_all(g, chunks, outdir: Path, formats: set[str], viz_nodes: int = MAX_NODES):
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    if "jsonl" in formats:
        write_jsonl(outdir / "nodes.jsonl", g.nodes.values())
        write_jsonl(outdir / "edges.jsonl", g.edges)
        written += ["nodes.jsonl", "edges.jsonl"]
    if chunks is not None:
        write_jsonl(outdir / "chunks.jsonl", chunks)
        written.append("chunks.jsonl")
    if "graphml" in formats:
        write_graphml(g, outdir / "graph.graphml")
        written.append("graph.graphml")
    if "cypher" in formats:
        write_cypher(g, outdir / "graph.cypher")
        written.append("graph.cypher")
    if "overview" in formats:
        write_overview(g, outdir / "overview.md")
        written.append("overview.md")
    if "html" in formats:
        write_html(g, outdir / "graph.html", viz_nodes)
        written.append("graph.html")
    (outdir / "stats.json").write_text(json.dumps(dict(g.stats), indent=2),
                                       encoding="utf8")
    return written + ["stats.json"]
