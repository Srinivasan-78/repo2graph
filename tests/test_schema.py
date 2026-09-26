"""Drift detector for repo2graph/schema.py (Issue #312).

`repo2graph/schema.py` documents the shapes of nodes.jsonl/edges.jsonl/
chunks.jsonl/manifest.json and the return values of `Index.retrieve()` /
`Index.pack_context()` as TypedDicts, but nothing at runtime keeps those
declarations honest -- the actual code keeps returning plain dicts (see the
module docstring for why). This file builds a real index and checks that
every field a TypedDict here declares *required* actually appears on a real
record, so a field renamed in chunks.py/export.py without a matching edit to
schema.py fails CI instead of drifting silently.

Uses the `mini_index` fixture from tests/conftest.py -- the same fixture the
MCP and vector suites build on -- rather than inventing a second fixture repo.
"""

import json

from repo2graph.integrity import verify_artifacts
from repo2graph.query import Index, read_jsonl
from repo2graph.schema import (
    ChunkRecord,
    ManifestRecord,
    PackResult,
    RetrievalResult,
)

# tests/conftest.py's own vocabulary contract for `mini_repo`/`mini_index`:
# this phrase hits pkg/gateway.py::route_request lexically. Reusing it rather
# than inventing a new query term keeps this file honest about what the
# shared fixture actually contains.
from conftest import MINI_QUERY


def test_chunk_records_carry_every_required_schema_field(mini_index):
    chunks = read_jsonl(str(mini_index / "agent" / "chunks.jsonl"))
    assert chunks, "fixture produced no chunks -- test is vacuous"
    required = ChunkRecord.__required_keys__
    for chunk in chunks:
        missing = required - chunk.keys()
        assert not missing, f"chunk {chunk.get('id')!r} is missing {missing}"


def test_node_and_edge_records_carry_their_documented_minimum(mini_index):
    """`export.FILE_NOTES` promises `id`/`type` on every node and
    `src`/`dst`/`type` on every edge -- NodeRecord/EdgeRecord are `total=False`
    (every field is type-specific), so this checks the one invariant that
    actually holds for all of them rather than a TypedDict's required set.
    """
    nodes = read_jsonl(str(mini_index / "agent" / "nodes.jsonl"))
    edges = read_jsonl(str(mini_index / "agent" / "edges.jsonl"))
    assert nodes and edges, "fixture produced no nodes/edges -- test is vacuous"
    for node in nodes:
        assert {"id", "type"} <= node.keys()
    for edge in edges:
        assert {"src", "dst", "type"} <= edge.keys()


def test_every_emitted_node_and_edge_field_is_declared_in_schema(mini_index):
    """The direction the test above cannot see: a field emitted but undeclared.

    `NodeRecord`/`EdgeRecord` are `total=False`, so `__required_keys__` is the
    empty set and any "does the record carry what we declared" check is
    vacuously true for them. That left the *other* drift undetected, and it had
    accumulated: `start_line`, `end_line` and `signature` were emitted on every
    symbol node and declared nowhere -- the three keys `impact.py` reads -- along
    with `size`/`chunked`/`parse_errors`/`external` on nodes and the seven
    resolution/inheritance fields on edges.

    schema.py exists so a consumer can annotate `node: NodeRecord` and typecheck
    against the artifact. An undeclared field makes that annotation *wrong* under
    a strict checker, which is the same class of bug as a missing one.
    """
    from repo2graph.schema import EdgeRecord, NodeRecord

    nodes = read_jsonl(str(mini_index / "agent" / "nodes.jsonl"))
    edges = read_jsonl(str(mini_index / "agent" / "edges.jsonl"))
    assert nodes and edges, "fixture produced no nodes/edges -- test is vacuous"

    node_fields: set[str] = set().union(*(n.keys() for n in nodes))
    edge_fields: set[str] = set().union(*(e.keys() for e in edges))

    undeclared_nodes = sorted(node_fields - set(NodeRecord.__annotations__))
    undeclared_edges = sorted(edge_fields - set(EdgeRecord.__annotations__))

    assert undeclared_nodes == [], (
        f"nodes.jsonl emits fields NodeRecord does not declare: {undeclared_nodes}. "
        f"Add them to repo2graph/schema.py (and docs/OUTPUT_SCHEMA.md)."
    )
    assert undeclared_edges == [], (
        f"edges.jsonl emits fields EdgeRecord does not declare: {undeclared_edges}. "
        f"Add them to repo2graph/schema.py (and docs/OUTPUT_SCHEMA.md)."
    )


def test_inherits_and_external_records_are_declared(tmp_path):
    """The edge and node types `mini_index` never produces.

    `mini_repo` has no class hierarchy and no call out of the tree, so the drift
    check above never sees an `INHERITS` edge (`raw_base`, `subtype`), a
    `CALLS_EXTERNAL` edge (`ambiguous`) or an `external` node -- 8 of the fields
    this module declares. A drift detector blind to a third of the surface is the
    kind of test that passes while the contract rots, so this builds a repo that
    does emit them rather than asserting against the same narrow fixture twice.
    """
    from repo2graph.chunks import iter_chunks
    from repo2graph.export import dump_all
    from repo2graph.graph import build
    from repo2graph.parse import BuildConfig
    from repo2graph.schema import EdgeRecord, NodeRecord

    src = tmp_path / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "__init__.py").write_text("VERSION = '1'\n", encoding="utf-8", newline="\n")
    (src / "pkg" / "base.py").write_text(
        "REGISTRY = {}\n\n\nclass Base:\n    def handle(self):\n        return 1\n",
        encoding="utf-8",
        newline="\n",
    )
    (src / "pkg" / "impl.py").write_text(
        # `Base` subclass -> INHERITS; `json.dumps` -> CALLS_EXTERNAL + external node.
        "import json\n\nfrom .base import Base\n\nSETTINGS = {'mode': 'live'}\n\n\n"
        "class Impl(Base):\n"
        "    def handle(self):\n"
        "        return json.dumps(SETTINGS)\n",
        encoding="utf-8",
        newline="\n",
    )

    out = tmp_path / "idx"
    g = build(src, config=BuildConfig(), jobs=1)
    dump_all(g, iter_chunks(g), out, {"jsonl"}, 0)

    edges = read_jsonl(str(out / "agent" / "edges.jsonl"))
    nodes = read_jsonl(str(out / "agent" / "nodes.jsonl"))
    types = {e["type"] for e in edges}
    assert "INHERITS" in types, f"fixture emitted no INHERITS edge: {sorted(types)}"
    assert "CALLS_EXTERNAL" in types, f"fixture emitted no CALLS_EXTERNAL edge: {sorted(types)}"

    undeclared_e = sorted(set().union(*(e.keys() for e in edges)) - set(EdgeRecord.__annotations__))
    undeclared_n = sorted(set().union(*(n.keys() for n in nodes)) - set(NodeRecord.__annotations__))
    assert undeclared_e == [], f"undeclared edge fields: {undeclared_e}"
    assert undeclared_n == [], f"undeclared node fields: {undeclared_n}"

    # The specific fields this fixture exists to reach, so a future change that
    # stops emitting them fails here rather than quietly shrinking the check.
    inherits = [e for e in edges if e["type"] == "INHERITS"]
    # `ambiguous` rides on INHERITS (and on CALLS, which `mini_index` covers) --
    # not on CALLS_EXTERNAL, whose target is unresolved by definition and so
    # carries `resolution_kind`/`candidate_count` instead.
    for field in ("raw_base", "subtype", "ambiguous", "candidate_count"):
        assert any(field in e for e in inherits), f"no INHERITS edge carries {field!r}"
    external_calls = [e for e in edges if e["type"] == "CALLS_EXTERNAL"]
    for field in ("resolution_kind", "candidate_count", "call_kind"):
        assert any(field in e for e in external_calls), f"no CALLS_EXTERNAL edge carries {field!r}"
    assert any(n.get("external") for n in nodes), "no external node emitted"


def test_manifest_carries_every_required_schema_field(mini_index):
    manifest_path = mini_index / "agent" / "manifest.json"
    manifest: ManifestRecord = json.loads(manifest_path.read_text(encoding="utf8"))
    # ManifestRecord is `total=False` (precautionary, per its docstring), but
    # export.write_manifest writes every one of these keys unconditionally --
    # assert against the module's own field list so this test fails the day
    # write_manifest stops writing one of them, or renames it.
    for field in ManifestRecord.__annotations__:
        assert field in manifest, f"manifest.json is missing {field!r}"


def test_retrieve_results_carry_every_required_schema_field(mini_index):
    idx = Index(str(mini_index))
    results = idx.retrieve(MINI_QUERY, k=5)
    assert results, "fixture query returned nothing -- test is vacuous"
    required = RetrievalResult.__required_keys__
    for r in results:
        missing = required - r.keys()
        assert not missing, f"retrieve() result {r.get('id')!r} is missing {missing}"


def test_pack_context_result_carries_every_required_schema_field(mini_index):
    idx = Index(str(mini_index))
    pack = idx.pack_context(MINI_QUERY)
    required = PackResult.__required_keys__
    missing = required - pack.keys()
    assert not missing, f"pack_context() result is missing {missing}"
    for chunk in pack["chunks"]:
        chunk_missing = RetrievalResult.__required_keys__ - chunk.keys()
        assert not chunk_missing, f"pack_context() chunk is missing {chunk_missing}"


def test_verify_artifacts_reports_valid_on_a_fresh_index(mini_index):
    report = verify_artifacts(str(mini_index))
    assert report.status == "valid"
    assert report.is_valid is True
