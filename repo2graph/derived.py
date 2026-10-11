"""Edges and marks derived from the resolved graph: READS/WRITES/REFERENCES, entrypoints, TESTS (#315)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from .edgemeta import METHOD_CALL_GRAPH, counts_as_call, evidence as make_evidence
from .parse import ParsedFile, symbol_key
from .refcapture import VALUE_KINDS
from .testpaths import is_test_path

if TYPE_CHECKING:
    from .graph import Graph

REFERENCE_EDGES = {"read": "READS", "write": "WRITES", "type": "REFERENCES"}
TYPE_KINDS = frozenset({"class", "interface", "type", "enum", "struct", "protocol", "trait"})
METHOD_REFERENCE_RESOLVER = "reference-resolver"


def add_reference_edges(
    g: Graph,
    parsed: dict[str, ParsedFile],
    imported_files: dict[str, set[str]],
    import_aliases: dict[str, dict[str, tuple[str, str | None]]],
    named_in: Callable[[str, Iterable[str]], list[str]],
) -> None:
    """READS / WRITES / REFERENCES from each symbol's captured refs (#397).

    Precision over recall: a name binds only to a value/type symbol defined at
    the top level of the same file, or one the file imports by that name; a
    `self.x` / `this.x` binds only to a field declared in the enclosing class.
    There is no repository-wide name fallback, which is where CALLS gets its
    ambiguity, so an unresolved reference is counted and dropped, never guessed.
    """
    for key in ("reads", "writes", "references", "unresolved"):
        g.stats[f"reference_edges_{key}"] += 0
    for rel, pf in parsed.items():
        for sym in pf.symbols:
            if not sym.refs:
                continue
            sid = f"sym:{rel}::{symbol_key(sym)}"
            for r in sym.refs:
                etype = REFERENCE_EDGES[r["kind"]]
                wanted = TYPE_KINDS if r["kind"] == "type" else VALUE_KINDS
                name = r["name"]
                if r.get("field"):
                    how = "same_class"
                    cands = [f"sym:{rel}::{sym.parent}.{name}"] if sym.parent else []
                else:
                    how = "same_file"
                    cands = [f"sym:{rel}::{name}"]
                cands = [c for c in cands if g.nodes.get(c, {}).get("kind") in wanted]
                if not cands and not r.get("field"):
                    alias = import_aliases.get(rel, {}).get(name)
                    target = (alias[1] or name) if alias else name
                    how = "import_alias" if alias else "imported_symbol"
                    cands = [
                        c
                        for c in named_in(target, imported_files.get(rel, ()))
                        if g.nodes[c].get("kind") in wanted and g.nodes[c].get("qualname") == target
                    ]
                if not cands:
                    g.stats["reference_edges_unresolved"] += 1
                    continue
                extra = {"ambiguous": True} if len(cands) > 1 else {}
                for c in cands:
                    g.add_edge(
                        sid,
                        c,
                        etype,
                        count=r.get("count", 1),
                        confidence=round(1.0 / len(cands), 3),
                        resolution_kind=how,
                        candidate_count=len(cands),
                        method=METHOD_REFERENCE_RESOLVER,
                        evidence=make_evidence(rel, r.get("line")),
                        **extra,
                    )
                g.stats[f"reference_edges_{etype.lower()}"] += 1


ENTRY_KINDS = ("function", "method")
SCORED_ENTRYPOINTS = 200  # exact reach is a BFS each, so only rank the busiest


def mark_entrypoints(g: Graph) -> None:
    """Flag the call-graph roots: symbols nothing else in the repo calls.

    Those are the doors into a codebase — CLI commands, request handlers, test
    bodies, public API — and they are where a reader tracing a flow has to
    start. A symbol nested inside a function is skipped: an uncalled closure is
    dead weight, not a door. `reach` (how many symbols the root can reach
    through CALLS) is filled in for the busiest roots only, so ranking them
    stays cheap on a big repo.
    """
    called, out = set(), defaultdict(list)
    for e in g.edges:
        if e["type"] == "CALLS":
            # #342: a self-recursive function's own CALLS edge (src == dst)
            # must not disqualify it from being an entrypoint root -- nothing
            # *else* calls it. `out` still records the self-edge so `_reach`
            # sees it; it is just harmless there since `start` is already
            # in `seen` before the BFS looks at its own outgoing edges.
            #
            # A guess (edgemeta.counts_as_call -- an untyped-receiver builtin
            # or a repo-wide name split) does not make its target "called":
            # `d.get()` must not hide the `views.get` handler. An overload
            # fan-out (3 overloads -> 0.333 each) does.
            if e["src"] != e["dst"] and counts_as_call(e):
                called.add(e["dst"])
            out[e["src"]].append(e["dst"])
    nested = {
        e["dst"]
        for e in g.edges
        if e["type"] == "DEFINES" and g.nodes.get(e["src"], {}).get("kind") in ENTRY_KINDS
    }
    roots = [
        nid
        for nid, n in g.nodes.items()
        if n["type"] == "symbol"
        and n.get("kind") in ENTRY_KINDS
        and nid not in called
        and nid not in nested
    ]
    for nid in roots:
        g.nodes[nid]["entrypoint"] = True
    roots.sort(key=lambda nid: (-len(out.get(nid, ())), nid))
    for nid in roots[:SCORED_ENTRYPOINTS]:
        g.nodes[nid]["reach"] = _reach(nid, out)
    g.stats["entrypoints"] = len(roots)


# How many CALLS hops a TESTS edge may span. Two reaches both `test -> helper
# -> target` and `test -> target -> its callee`; every further hop multiplies
# the fan-out of ambiguous names while saying less about what the test is for.
TESTS_MAX_HOPS = 2


def add_tests_edges(g: Graph) -> None:
    """TESTS edges: from each test symbol to every non-test symbol it reaches.

    A test symbol is any `symbol` node defined in a file `is_test_path` claims,
    helpers and fixtures included. From each one, CALLS edges are walked up to
    TESTS_MAX_HOPS deep -- through test helpers and production code alike --
    and a TESTS edge is added to every non-test symbol reached. So TESTS means
    "this test reaches that symbol through calls", never "this test asserts on
    it": nothing here reads an assertion.

    Confidence is the product of the CALLS confidences along the path, the
    best path winning when several reach the same symbol. Each CALLS
    confidence is P(dst is the right target), so the product is the chance
    every hop on the path resolved correctly -- taking the minimum instead
    would let a chain of two 0.5 guesses read as certain as one. Evidence is
    the call site in the test where that path starts, which is a line in the
    test that leads to the symbol; `hops` says how far away it is.

    CALLS edges that are not evidence of a call at all (a builtin method name on
    an untyped receiver, a bare call to a shadowed builtin -- see
    `edgemeta.counts_as_call`) are not walked: `d.get()` in a test does not
    exercise every `get` in the repository.
    """
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in g.edges:
        if e["type"] != "CALLS" or e["src"] == e["dst"]:
            continue
        if e.get("untyped_receiver") or e.get("shadowed_builtin"):
            continue
        out[e["src"]].append(e)

    test_syms: set[str] = set()
    testable: list[str] = []
    for nid, n in g.nodes.items():
        if n["type"] != "symbol":
            continue
        if is_test_path(n.get("path") or ""):
            test_syms.add(nid)
        elif n.get("kind") in ENTRY_KINDS:
            testable.append(nid)

    # Sorted, and frontiers walked in sorted order, so ties between equally
    # good paths break the same way on every run and under every hash seed.
    for test in sorted(test_syms):
        best: dict[str, tuple[float, int, Any]] = {}
        frontier: dict[str, tuple[float, Any]] = {test: (1.0, None)}
        for hop in range(1, TESTS_MAX_HOPS + 1):
            nxt: dict[str, tuple[float, Any]] = {}
            for u in sorted(frontier):
                conf_u, ev_u = frontier[u]
                for e in out.get(u, ()):
                    v = e["dst"]
                    if v == test:
                        continue
                    try:
                        conf = conf_u * float(e.get("confidence", 1.0))
                    except (TypeError, ValueError):
                        continue
                    ev = e.get("evidence") if hop == 1 else ev_u
                    if v not in nxt or conf > nxt[v][0]:
                        nxt[v] = (conf, ev)
                    if v in test_syms:
                        continue
                    prev = best.get(v)
                    if prev is None or conf > prev[0]:
                        best[v] = (conf, hop, ev)
            frontier = nxt
        for v in sorted(best):
            conf, hops, ev = best[v]
            g.add_edge(
                test,
                v,
                "TESTS",
                method=METHOD_CALL_GRAPH,
                confidence=round(conf, 3),
                evidence=ev,
                hops=hops,
            )

    tested = {e["dst"] for e in g.edges if e["type"] == "TESTS"}
    g.stats["testable_symbols"] = len(testable)
    g.stats["tested_symbols"] = sum(1 for nid in testable if nid in tested)


def _reach(start: str, out: dict[str, Any]) -> int:
    """How many distinct symbols `start` reaches through CALLS edges."""
    seen, stack = {start}, [start]
    while stack:
        for dst in out.get(stack.pop(), ()):
            if dst not in seen:
                seen.add(dst)
                stack.append(dst)
    return len(seen) - 1
