"""Explain graph edges, nodes, and retrieval results (Issue #309)."""

from pathlib import Path
from typing import Any

from .query import ALL_EDGE_DIRS, RETRIEVE_BUDGET_CHARS, Index, tokenize


def explain_edge(outdir: Path, src: str, dst: str) -> dict[str, Any]:
    """Explain edges between two nodes in the code graph."""
    idx = Index(outdir)
    src_node = idx.nodes.get(src)
    dst_node = idx.nodes.get(dst)

    matching_edges = []
    # Check both directions
    for e in idx.edges:
        if (e.get("src") == src and e.get("dst") == dst) or (
            e.get("src") == dst and e.get("dst") == src
        ):
            matching_edges.append(e)

    found = len(matching_edges) > 0
    res: dict[str, Any] = {
        "found": found,
        "src": src,
        "dst": dst,
        "src_exists": src_node is not None,
        "dst_exists": dst_node is not None,
        "src_node": src_node,
        "dst_node": dst_node,
        "edges": matching_edges,
    }
    if not found:
        if src_node is None and dst_node is None:
            res["reason"] = f"neither node '{src}' nor '{dst}' exists in graph"
        elif src_node is None:
            res["reason"] = f"source node '{src}' does not exist in graph"
        elif dst_node is None:
            res["reason"] = f"destination node '{dst}' does not exist in graph"
        else:
            res["reason"] = f"both nodes exist, but no direct edge connects '{src}' and '{dst}'"
    return res


def explain_node(outdir: Path, node_id: str) -> dict[str, Any]:
    """Explain a node, its metadata, incoming/outgoing edges, and chunks."""
    idx = Index(outdir)
    node = idx.nodes.get(node_id)
    if node is None:
        return {
            "found": False,
            "node_id": node_id,
            "reason": f"node '{node_id}' does not exist in graph",
        }

    in_edges = []
    out_edges = []
    for other, etype, direction, edge in idx.adj.get(node_id, []):
        if direction == "in":
            in_edges.append(edge)
        else:
            out_edges.append(edge)

    chunks = [
        {
            "id": c.get("id"),
            "kind": c.get("kind"),
            "start_line": c.get("start_line"),
            "end_line": c.get("end_line"),
            "char_len": len(c.get("text") or ""),
        }
        for c in idx.by_node.get(node_id, [])
    ]

    return {
        "found": True,
        "node_id": node_id,
        "node": node,
        "in_degree": len(in_edges),
        "out_degree": len(out_edges),
        "in_edges": in_edges,
        "out_edges": out_edges,
        "chunks": chunks,
    }


def explain_retrieval(
    outdir: Path,
    query: str,
    k: int = 5,
    hops: int = 1,
    min_confidence: float | None = None,
    budget_chars: int = RETRIEVE_BUDGET_CHARS,
) -> dict[str, Any]:
    """Trace query tokenization, seed ranking, graph expansion, and final chunk selection.

    Every step here has to reproduce `Index.retrieve`'s, because the chunks it
    claims to explain come from `Index.retrieve` twenty lines down. Three things
    that must not drift: the seed loop stops on `budget_chars` as well as on
    `k`, the seeds stay in score order (`expand` walks its frontier in order
    under a per-hop cap, so a reordered seed list is a different traversal), and
    the expansion passes `ALL_EDGE_DIRS` — see AGENTS.md, "A new default on a
    shared traversal helper narrows its existing callers".
    """
    idx = Index(outdir)
    query_terms = tokenize(query)

    # BM25 scoring
    scored = idx.score(query)
    seed_chunks = scored[: k * 3]

    seeds_info = []
    seen_seed_nodes: set[str] = set()
    primary_seeds: list[str] = []
    used = 0
    seeding = True
    for rank, (score, chunk_idx) in enumerate(seed_chunks, 1):
        c = idx.chunks[chunk_idx]
        nid = c["node_id"]
        # Find matched terms
        text = (c.get("text") or "").lower()
        qual = (c.get("qualname") or "").lower()
        matched = [t for t in query_terms if t in text or t in qual]
        chunk_len = len(c.get("text") or "")
        is_primary = False
        if seeding and nid not in seen_seed_nodes:
            if primary_seeds and used + chunk_len > budget_chars:
                seeding = False
            else:
                is_primary = True
                seen_seed_nodes.add(nid)
                primary_seeds.append(nid)
                used += chunk_len
                if len(primary_seeds) >= k or used >= budget_chars:
                    seeding = False

        seeds_info.append(
            {
                "rank": rank,
                "score": round(score, 4),
                "chunk_id": c.get("id"),
                "node_id": nid,
                "node_name": c.get("name"),
                "qualname": c.get("qualname"),
                "matched_terms": sorted(set(matched)),
                "selected_as_seed": is_primary,
            }
        )

    # Graph expansion from primary seed nodes, in seed order and with the same
    # direction policy retrieve() uses. Sorting or set-ordering the seeds here
    # would trace a walk that never happened.
    expansion_order = idx.expand(
        primary_seeds,
        hops=hops,
        min_confidence=0.0 if min_confidence is None else min_confidence,
        edge_dirs=ALL_EDGE_DIRS,
    )

    expansion_steps = []
    for dst, etype, direction, src in expansion_order:
        dst_node = idx.nodes.get(dst, {})
        expansion_steps.append(
            {
                "from_node": src,
                "to_node": dst,
                "edge_type": etype,
                "direction": direction,
                "destination_name": dst_node.get("name", dst),
                "destination_kind": dst_node.get("kind"),
            }
        )

    # Retrieve final chunks with provenance
    retrieved_chunks = idx.retrieve(
        query,
        k=k,
        hops=hops,
        budget_chars=budget_chars,
        min_confidence=min_confidence,
    )

    final_chunks = []
    for rank, c in enumerate(retrieved_chunks, 1):
        nid = c["node_id"]
        is_seed = nid in seen_seed_nodes
        final_chunks.append(
            {
                "rank": rank,
                "chunk_id": c.get("id"),
                "node_id": nid,
                "name": c.get("name"),
                "qualname": c.get("qualname"),
                "path": c.get("path"),
                "provenance": "seed" if is_seed else "expanded_neighbor",
                "char_len": len(c.get("text") or ""),
            }
        )

    return {
        "query": query,
        "query_tokens": query_terms,
        "k": k,
        "hops": hops,
        "has_vectors": idx.vectors is not None,
        "vector_model": (idx.vector_meta or {}).get("model_id"),
        "seeds": seeds_info,
        "primary_seeds": primary_seeds,
        "expansion_steps": expansion_steps,
        "retrieved_chunks": final_chunks,
    }


def format_explain_edge(data: dict[str, Any]) -> str:
    """Format edge explanation as human-readable text."""
    if not data.get("found"):
        return f"No edge found between '{data['src']}' and '{data['dst']}': {data.get('reason', 'none')}\n"

    lines = [
        f"Edge explanation: {data['src']} <-> {data['dst']}",
        f"Matching edges ({len(data['edges'])}):",
    ]
    for i, e in enumerate(data["edges"], 1):
        direction = f"{e['src']} -> {e['dst']}"
        etype = e.get("type", "UNKNOWN")
        conf = e.get("confidence")
        conf_str = f" (confidence: {conf})" if conf is not None else ""
        lines.append(f"  {i}. [{etype}] {direction}{conf_str}")
        for k, v in sorted(e.items()):
            if k not in ("src", "dst", "type", "confidence"):
                lines.append(f"       {k}: {v}")

    src_node = data.get("src_node")
    if src_node:
        lines.append(
            f"Source location:      {src_node.get('path', 'unknown')}:{src_node.get('start_line', '?')}-{src_node.get('end_line', '?')}"
        )
    dst_node = data.get("dst_node")
    if dst_node:
        lines.append(
            f"Destination location: {dst_node.get('path', 'unknown')}:{dst_node.get('start_line', '?')}-{dst_node.get('end_line', '?')}"
        )
    return "\n".join(lines) + "\n"


def format_explain_node(data: dict[str, Any]) -> str:
    """Format node explanation as human-readable text."""
    if not data.get("found"):
        return f"Node not found: {data.get('reason', 'unknown')}\n"

    n = data["node"]
    lines = [
        f"Node: {data['node_id']}",
        f"  Type:       {n.get('type')}",
        f"  Name:       {n.get('name')}",
        f"  Qualname:   {n.get('qualname', n.get('name'))}",
        f"  Kind:       {n.get('kind', 'n/a')}",
        f"  Location:   {n.get('path', 'n/a')}:{n.get('start_line', '?')}-{n.get('end_line', '?')}",
        f"  Language:   {n.get('lang', 'n/a')}",
    ]
    if n.get("signature"):
        lines.append(f"  Signature:  {n['signature']}")
    if n.get("docstring"):
        lines.append(f"  Docstring:  {n['docstring'][:80]}...")

    lines.append(f"Incoming edges ({data['in_degree']}):")
    for e in data["in_edges"][:10]:
        lines.append(f"  <- [{e.get('type')}] {e.get('src')}")
    if data["in_degree"] > 10:
        lines.append(f"  ... and {data['in_degree'] - 10} more")

    lines.append(f"Outgoing edges ({data['out_degree']}):")
    for e in data["out_edges"][:10]:
        lines.append(f"  -> [{e.get('type')}] {e.get('dst')}")
    if data["out_degree"] > 10:
        lines.append(f"  ... and {data['out_degree'] - 10} more")

    lines.append(f"Chunks ({len(data['chunks'])}):")
    for c in data["chunks"]:
        lines.append(f"  - [{c['kind']}] {c['id']} ({c['char_len']} chars)")

    return "\n".join(lines) + "\n"


def format_explain_retrieval(data: dict[str, Any]) -> str:
    """Format retrieval explanation as human-readable text."""
    lines = [
        f"Retrieval Explanation for query: {data['query']!r}",
        f"Tokens: {data['query_tokens']}",
        f"Dense vectors: {'Enabled (' + str(data['vector_model']) + ')' if data['has_vectors'] else 'Disabled (lexical only)'}",
        "",
        f"Candidate seeds ({len(data['seeds'])} short-listed):",
    ]
    # The whole short list, not `[:k]`. The header counts every candidate, and
    # the `*` column is the point of the section: a selected seed can sit below
    # rank k (each one has to be the first chunk of a *distinct* node), so
    # truncating to k both contradicted the count above it and hid seeds the
    # expansion below then walks from. The list is `k * 3` long by
    # construction, so printing it all is bounded.
    for s in data["seeds"]:
        selected = "*" if s["selected_as_seed"] else " "
        lines.append(
            f" {selected} Rank {s['rank']:2d} | Score: {s['score']:.4f} | {s['node_id']} | matches: {s['matched_terms']}"
        )

    lines.append("")
    lines.append(f"Graph expansion ({len(data['expansion_steps'])} steps, hops={data['hops']}):")
    if not data["expansion_steps"]:
        lines.append("  (no graph neighbours expanded)")
    else:
        for step in data["expansion_steps"]:
            lines.append(
                f"  {step['from_node']} --[{step['edge_type']} ({step['direction']})]-> {step['to_node']}"
            )

    lines.append("")
    lines.append(f"Retrieved chunks returned ({len(data['retrieved_chunks'])}):")
    for c in data["retrieved_chunks"]:
        lines.append(
            f"  {c['rank']:2d}. [{c['provenance']}] {c['chunk_id']} ({c['name']}) in {c['path']} ({c['char_len']} chars)"
        )

    return "\n".join(lines) + "\n"
