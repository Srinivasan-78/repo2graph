"""Typed shapes for repo2graph's public records: nodes, edges, chunks, retrieval
results, and the two build-time reports (`manifest.json`, `IntegrityReport`).

This module documents a contract; it does not enforce one at runtime. The
functions that actually build these records (`graph.Graph.add_node`,
`graph.Graph.add_edge`, `chunks.iter_chunks`, `export.write_manifest`,
`query.Index.retrieve`/`pack_context`) keep returning plain `dict[str, Any]` —
see the `Record = dict[str, Any]` comment at the top of `query.py` for why that
stays deliberate: a node's dict grows type-specific keys (a `file` node carries
`lang`/`lines`; a `symbol` node carries `kind`/`qualname`/`reach`), and closing
that over a single `TypedDict` would either reject legitimate shapes or make
every field `NotRequired`, which is what `total=False` below already says
without an extra keyword only available on Python 3.11+.

Import from here when you want static checking of code that *consumes*
`nodes.jsonl`/`edges.jsonl`/`chunks.jsonl`/`manifest.json` or the return value
of `Index.retrieve()`/`Index.pack_context()` — annotate your own variables
with these types; nothing in repo2graph itself is retyped to produce them
structurally, so a mismatch here is a documentation bug, not a runtime one.
`tests/test_schema.py` is the drift detector: it builds a real index and
checks every field this module declares required actually appears on a real
record, so a field renamed in `chunks.py`/`export.py` without a matching edit
here fails CI.

Every name below is public API, imported as `from repo2graph.schema import
ChunkRecord, ...` (this module is not re-exported from the package root, so
`repo2graph.schema` is the one import path). Anything named with a leading
underscore anywhere else in the package (`query._compress`,
`query._is_secret_path`, `export._stats_extra`, ...) is internal: it may
change shape or disappear between releases without that counting as a
breaking change to the public API described here and in `docs/python-api.md`.
"""

from typing import Any, Literal, TypedDict

__all__ = [
    "NodeType",
    "EdgeType",
    "NodeRecord",
    "EdgeRecord",
    "ChunkRecord",
    "RetrievalResult",
    "PackResult",
    "EntrypointRecord",
    "ManifestRecord",
    "StatsRecord",
]

# `export.NODE_TYPES` / `export.EDGE_TYPES` are the runtime source of truth
# (and what `manifest.json`'s `node_types`/`edge_types` keys echo); these
# Literals exist purely so a type checker can flag a typo'd kind string in
# code written against this module. They are not re-derived from NODE_TYPES /
# EDGE_TYPES at import time on purpose — a Literal built from a dict's keys at
# runtime is not something mypy can narrow against.
NodeType = Literal["repo", "dir", "file", "symbol", "module", "external"]
EdgeType = Literal[
    "CONTAINS",
    "DEFINES",
    "IMPORTS",
    "CALLS",
    "CALLS_EXTERNAL",
    "INHERITS",
    "CO_CHANGE",
]


class NodeRecord(TypedDict, total=False):
    """One line of `nodes.jsonl`. Only `id` and `type` are on every node —
    see `export.FILE_NOTES["nodes.jsonl"]`, which says the same thing.
    """

    id: str
    type: NodeType
    path: str
    qualname: str
    name: str
    kind: str
    lang: str
    lines: int
    reach: int
    entrypoint: bool
    docstring: str
    file_type: str


class EdgeRecord(TypedDict, total=False):
    """One line of `edges.jsonl`. `src`/`dst`/`type` are on every edge; the
    rest is edge-type-specific — see `export.EDGE_TYPES` for which type
    carries what (`CALLS` carries `count`/`confidence`, `IMPORTS` carries
    `target`/`internal`, `CO_CHANGE` carries `count`).
    """

    src: str
    dst: str
    type: EdgeType
    count: int
    confidence: float
    internal: bool
    target: str


class _NeighbourEdgeRequired(TypedDict):
    target: str
    edge_type: EdgeType
    edge_direction: Literal["inbound", "outbound"]


class NeighbourEdge(_NeighbourEdgeRequired, total=False):
    """One entry of a chunk's `caller_edges`/`callee_edges`/`base_edges` —
    see `chunks._neighbour_edge`. `confidence` is present only when the edge
    is an ambiguous `CALLS` edge scoring below 1.0; `IMPORTS`/`DEFINES`/
    `INHERITS` neighbours never carry it (they have no `confidence` key to
    read in the first place — see the `min_confidence` note in `AGENTS.md`).
    """

    confidence: float


class ChunkRecord(TypedDict):
    """One line of `chunks.jsonl` — exactly the field list `manifest.json`
    publishes as `chunk_fields` (`export.write_manifest`), which is the
    authoritative list this TypedDict mirrors.
    """

    id: str
    node_id: str
    type: str  # "symbol" | "file" | "file_residual"
    kind: str
    path: str
    lang: str | None
    name: str
    qualname: str
    start_line: int | None
    end_line: int | None
    entrypoint: bool
    callers: list[str]
    callees: list[str]
    callees_external: list[str]
    caller_edges: list[NeighbourEdge]
    callee_edges: list[NeighbourEdge]
    base_edges: list[NeighbourEdge]
    text: str


class RetrievalResult(ChunkRecord):
    """A `ChunkRecord` as returned by `Index.retrieve()` or found in
    `pack_context()`'s `chunks`/`seeds`/`neighbors` lists: the chunk plus why
    it was picked. `score` is 0.0 for a graph neighbour, never meaningful to
    compare across `why` values — see `Index.retrieve`'s docstring.
    """

    score: float
    why: str  # "lexical" | "seed" | "<EDGE_TYPE> <direction> of <name>"


class PackResult(TypedDict):
    """The return value of `Index.pack_context()`. `budget_chars` is 0 when
    `budget_tokens` was used instead — see `pack_context`'s docstring for the
    accounting rules; this type only documents the shape, not the budget
    semantics.
    """

    markdown: str
    chunks: list[RetrievalResult]
    seeds: list[RetrievalResult]
    neighbors: list[RetrievalResult]
    truncated: bool
    budget_chars: int
    used_chars: int
    tokens_used: int
    tokens_budget: int
    query: str


class EntrypointRecord(TypedDict):
    """One entry of `manifest.json`'s `entrypoints` list."""

    id: str
    path: str
    qualname: str
    kind: str
    reach: int | None


class ManifestRecord(TypedDict, total=False):
    """`manifest.json`'s shape — see `export.write_manifest`, the single
    place that writes it. Every key here is written unconditionally by that
    function today; `total=False` is precautionary rather than a claim that
    any of them are actually optional in a current build.
    """

    format: str
    build_id: str
    tool_version: str
    schema_version: int
    created_at: str
    source_revision: dict[str, Any]
    checksums: dict[str, str]
    repo: str
    written: list[str]
    secret_filter_policy: str
    sections: dict[str, str]
    files: dict[str, str]
    node_types: dict[str, str]
    edge_types: dict[str, str]
    id_grammar: dict[str, str]
    chunk_fields: list[str]
    counts: dict[str, int]
    quality_metrics: dict[str, int]
    entrypoints: list[EntrypointRecord]
    entrypoint_rule: str
    max_call_candidates: int
    how_to_read: list[str]
    approximations: list[str]
    usage_hints: dict[str, Any]


# `stats.json` is `{**dict(g.stats), **export._stats_extra(g)}` — a Counter
# with open-ended keys (`"files"`, `"parsed"`, `"edge:CALLS"`, one per edge
# type actually emitted, ...) merged with a handful of fixed keys. The
# Counter half has no closed key set to declare, so unlike ManifestRecord this
# is a plain alias rather than a TypedDict — the same honesty `query.Record`
# already documents for the same reason.
StatsRecord = dict[str, Any]
"""`stats.json`'s shape: dynamic `Counter` keys plus `export._stats_extra`'s
fixed keys (`top_hub_nodes`, `languages`, `has_vectors`, `index_schema_version`,
optionally `built_at_commit`, `co_change_hotspots`)."""
