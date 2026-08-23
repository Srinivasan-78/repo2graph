"""Serialize the graph: JSONL, GraphML, Cypher, overview, HTML map."""
import json
from pathlib import Path

from .layout import AGENT_DIR, HUMAN_DIR, make_paths, rels
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


_NEIGHBOR_CELLS = ((1, 0), (1, 1), (0, 1), (-1, 1))


def _grid_pairs(pos, cell):
    """Yield the node pairs sitting within one grid cell of each other.

    Every pair lands in the same bucket or in two adjacent ones, and each
    unordered pair is yielded once: only four of the eight neighbouring cells
    are scanned, the other four see the pair from their own side.
    """
    from collections import defaultdict
    cells = defaultdict(list)
    for nid, (x, y) in pos.items():
        cells[(int(x // cell), int(y // cell))].append(nid)
    for (cx, cy), members in cells.items():
        near = [b for dx, dy in _NEIGHBOR_CELLS
                for b in cells.get((cx + dx, cy + dy), ())]
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                yield a, b
            for b in near:
                yield a, b


def _spring(nodes, adjacency, iterations):
    """Fruchterman-Reingold in pure Python: networkx's needs numpy, we do not.

    Repulsion runs only between nodes less than 2k apart, bucketed on a grid.
    Past that distance the k^2/d term moves a node by a rounding error, while
    the all-pairs form costs O(n^2) per iteration and dominates big exports.
    """
    import math
    import random
    n = len(nodes)
    rng = random.Random(17)
    pos = {nid: [rng.uniform(-1.0, 1.0), rng.uniform(-1.0, 1.0)] for nid in nodes}
    k = 1.0 / math.sqrt(n)
    k2 = k * k
    cutoff = 2.0 * k
    temp = 0.1
    cooling = temp / (iterations + 1)
    for _ in range(iterations):
        disp = {nid: [0.0, 0.0] for nid in nodes}
        for a, b in _grid_pairs(pos, cutoff):
            ax, ay = pos[a]
            dx, dy = ax - pos[b][0], ay - pos[b][1]
            dist2 = dx * dx + dy * dy
            if dist2 < 1e-9:
                dx, dy = rng.uniform(-1e-3, 1e-3), rng.uniform(-1e-3, 1e-3)
                dist2 = dx * dx + dy * dy
            force = k2 / dist2             # repulsion, 1/d scaled by 1/d
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


def _shelf(nodes, sizes, pad: float = 24.0):
    """Row-pack the boxes for graphs too big to force-lay-out in Python.

    Rows are filled in node order, which keeps a file next to the symbols it
    defines, and boxes cannot overlap, so no separation pass is needed.
    """
    import math
    area = sum((sizes[nid][0] + pad) * (sizes[nid][1] + pad) for nid in nodes)
    row_width = max(math.sqrt(area * 1.6),
                    max(sizes[nid][0] for nid in nodes) + pad)
    pos = {}
    x = y = row_height = 0.0
    for nid in nodes:
        width, height = sizes[nid]
        if x and x + width > row_width:
            x, y, row_height = 0.0, y + row_height + pad, 0.0
        pos[nid] = (x + width / 2, y + height / 2)
        x += width + pad
        row_height = max(row_height, height)
    return pos


# Even bucketed, force layout costs seconds once the graph collapses into dense
# clusters, and its clustering stops being readable at that size anyway: past
# this many nodes the packed rows are both faster and easier to look at.
SPRING_MAX_NODES = 1500


def _layout(G, sizes):
    """Node positions in points, spread so labels do not collide."""
    nodes = list(G)
    n = len(nodes)
    if n == 0:
        return {}
    if n == 1:
        return {nodes[0]: (0.0, 0.0)}
    if n > SPRING_MAX_NODES:
        return _shelf(nodes, sizes)
    adjacency = [(u, v) for u, v in G.to_undirected().edges() if u != v]
    pos = _spring(nodes, adjacency, 120 if n <= 400 else 50)
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
    """Push overlapping boxes apart; the spring layout only knows points.

    The grid cell is as wide as the widest box, so two overlapping boxes always
    share a cell or sit in adjacent ones and no pair is missed.
    """
    nodes = list(pos)
    pad = 16.0
    cell = max(max(w, h) for w, h in sizes.values()) + pad
    for _ in range(iterations):
        shift = {nid: [0.0, 0.0] for nid in nodes}
        overlaps = 0
        for a, b in _grid_pairs(pos, cell):
            ax, ay = pos[a]
            aw, ah = sizes[a]
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
}

ID_GRAMMAR = {
    "repo": "repo:<name>",
    "dir": "dir:<path>",
    "file": "file:<path>",
    "symbol": "sym:<path>::<qualname>",
    "module": "module:<import target>",
    "external": "external:<name>",
    "note": "Ids are stable and constructible by hand; paths are relative to the repo root.",
}

FILE_NOTES = {
    "nodes.jsonl": "one JSON object per node; `id` and `type` always present, the rest depends on type",
    "edges.jsonl": "one JSON object per edge: src, dst, type, plus edge attributes",
    "chunks.jsonl": "retrieval chunks, one per symbol (split at ~4000 chars) plus residual and whole-file chunks; `text` opens with a header naming the chunk's neighbours",
    "graph.cypher": "idempotent MERGE script for Neo4j / Memgraph",
    "stats.json": "node, edge and symbol counts, parse errors, entrypoint count",
    "overview.md": "the repo map in prose: languages, most depended-on files, most called symbols",
    "index.json": "repo slug and indexed commit; written by `repo2graph github` only",
    "manifest.json": "this file",
    "graph.html": "the interactive map, for a person in a browser",
    "graph.graphml": "the graph with a layout and yFiles node graphics, for yEd, Gephi, NetworkX or igraph",
}

HOW_TO_READ = [
    "Start with overview.md: it names the languages, the hub files and the most called symbols.",
    "To trace a flow, start at a node with entrypoint: true — nothing in the repo calls it — and follow CALLS edges forward; nodes.jsonl also carries `reach`, the number of symbols an entry point can reach, for the busiest 200 of them.",
    "To answer a question about code, score chunks.jsonl lexically or by embedding, then walk one hop out over CALLS/DEFINES/IMPORTS to pull in the neighbours. repo2graph.query.Index does both.",
    "Chunk `callees` holds in-repo targets as path::qualname; `callees_external` holds bare stdlib and third-party names that were never resolved.",
    "CALLS resolution is name-based, not type-based: an overloaded or shadowed name emits up to 5 candidate edges, each with confidence 1/n. Filter on confidence == 1.0 when a wrong edge would be costly.",
]


def write_manifest(g, path: Path, written: list[str]):
    """Describe the agent-facing output so a reader needs no other docs."""
    entry = sorted((n for n in g.nodes.values() if n.get("entrypoint")),
                   key=lambda n: (-n.get("reach", 0), n["path"], n["qualname"]))
    manifest = {
        "format": "repo2graph/1",
        "repo": g.name,
        "written": written,
        "sections": {
            HUMAN_DIR: "for people: prose map and drawings",
            AGENT_DIR: "for programs: the graph, the chunks, this manifest",
        },
        "files": {name: FILE_NOTES[name] for name in sorted(
            {w.split("/", 1)[1] for w in written} & set(FILE_NOTES))},
        "node_types": NODE_TYPES,
        "edge_types": EDGE_TYPES,
        "id_grammar": ID_GRAMMAR,
        "chunk_fields": ["id", "node_id", "type", "kind", "path", "lang", "name",
                         "qualname", "start_line", "end_line", "entrypoint",
                         "callers", "callees", "callees_external", "text"],
        "counts": dict(g.stats),
        "entrypoints": [{"id": n["id"], "path": n["path"], "qualname": n["qualname"],
                         "kind": n["kind"], "reach": n.get("reach")}
                        for n in entry[:25]],
        "entrypoint_rule": ("a function or method that no CALLS edge points at and that is "
                            "not nested inside another function"),
        "how_to_read": HOW_TO_READ,
        "approximations": [
            "Call resolution is name-based; ambiguous names fan out to up to 5 edges at 1/n confidence.",
            "Dynamic dispatch, reflection and generated code are invisible to a parser.",
            "Absence of an edge is not proof of absence of a call.",
        ],
    }
    path.write_text(json.dumps(manifest, indent=2), encoding="utf8")


def dump_all(g, chunks, outdir: Path, formats: set[str], viz_nodes: int = MAX_NODES):
    outdir.mkdir(parents=True, exist_ok=True)
    written = []

    def out(name: str) -> list[Path]:
        written.extend(rels(name))
        return make_paths(outdir, name)

    if "jsonl" in formats:
        write_jsonl(out("nodes.jsonl")[0], g.nodes.values())
        write_jsonl(out("edges.jsonl")[0], g.edges)
    if chunks is not None:
        write_jsonl(out("chunks.jsonl")[0], chunks)
    if "graphml" in formats:
        write_graphml(g, out("graph.graphml")[0])
    if "cypher" in formats:
        write_cypher(g, out("graph.cypher")[0])
    if "overview" in formats:
        first, *copies = out("overview.md")
        write_overview(g, first)
        for extra in copies:   # the same map, one per section
            extra.write_text(first.read_text(encoding="utf8"), encoding="utf8")
    if "html" in formats:
        write_html(g, out("graph.html")[0], viz_nodes)
    out("stats.json")[0].write_text(json.dumps(dict(g.stats), indent=2), encoding="utf8")
    write_manifest(g, out("manifest.json")[0], list(written))
    return written
