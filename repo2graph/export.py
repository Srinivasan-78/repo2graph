"""Serialize the graph: JSONL, GraphML, Cypher, Mermaid summary."""
import json
from pathlib import Path

SCALAR = (str, int, float, bool)


def _flat(d: dict) -> dict:
    return {k: (v if isinstance(v, SCALAR) else json.dumps(v))
            for k, v in d.items() if v is not None}


def write_jsonl(path: Path, rows):
    with open(path, "w", encoding="utf8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_graphml(g, path: Path):
    import networkx as nx
    G = nx.MultiDiGraph()
    for nid, n in g.nodes.items():
        G.add_node(nid, **_flat(n))
    for e in g.edges:
        attrs = _flat({k: v for k, v in e.items() if k not in ("src", "dst")})
        G.add_edge(e["src"], e["dst"], **attrs)
    nx.write_graphml(G, path)
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


def dump_all(g, chunks, outdir: Path, formats: set[str]):
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
    (outdir / "stats.json").write_text(json.dumps(dict(g.stats), indent=2))
    return written + ["stats.json"]
