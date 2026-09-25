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
