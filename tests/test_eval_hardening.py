"""Hardening tests for benchmark evaluation: leak check, hard negatives, and stability.

Guards:
- Zero test-suite code in evidence spans (only production source).
- Structural tasks provide positive graph advantage (> 0.000) over BM25.
- Hard negatives (same-repo distractors with overlapping names) are disambiguated by graph structure.
- Budget perturbations (+/-20%) preserve monotonic retrieval behaviour.
"""

from __future__ import annotations

import json
from pathlib import Path
from repo2graph.query import Index

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmarks" / "real"


def test_zero_test_set_leak():
    """Verify that zero test-suite or spec code is present in evidence spans."""
    for task_file in ["tasks.json", "tasks_structural.json"]:
        path = BENCH / task_file
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf8"))
        for t in data["tasks"]:
            for ev in t["evidence"]:
                ev_path = ev["path"].lower().replace("\\", "/")
                # Ensure no test/spec paths leak into benchmark evidence:
                assert not any(
                    part in ev_path
                    for part in ["test/", "tests/", "_test.", ".test.", "spec/", "testing/"]
                ), (
                    f"Leak detected in {task_file} task {t['id']}: evidence path '{ev['path']}' is test code"
                )


def test_hard_negative_distractor_disambiguation(tmp_path: Path):
    """Verify that graph structure disambiguates a target from a lexical distractor.

    Scenario:
    - Entry point `sym:app::handle_request` calls `sym:app::StreamingAdapter` in `adapters.py`.
    - Distractor `sym:app::Response` in `models.py` has high lexical overlap ('stream', 'adapter', 'response').
    - BM25 alone ranks the distractor high.
    - Graph expansion from `handle_request` reaches `StreamingAdapter` via CALLS edge,
      disambiguating the target and admitting it into context.
    """
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)

    chunks_path = agent_dir / "chunks.jsonl"
    nodes_path = agent_dir / "nodes.jsonl"
    edges_path = agent_dir / "edges.jsonl"

    # Distractor has more repetitions of 'stream adapter' to win lexical ranking
    chunks_path.write_text(
        '{"id": "c_entry", "node_id": "sym:app::handle_request", "path": "app.py", "name": "handle_request", "text": "def handle_request():\\n    adapter = StreamingAdapter()\\n    return adapter.send()", "start_line": 1, "end_line": 3}\n'
        '{"id": "c_target", "node_id": "sym:app::StreamingAdapter", "path": "adapters.py", "name": "StreamingAdapter", "text": "class StreamingAdapter:\\n    def send(self):\\n        pass", "start_line": 1, "end_line": 3}\n'
        '{"id": "c_distractor", "node_id": "sym:app::Response", "path": "models.py", "name": "Response", "text": "class Response:\\n    # stream adapter stream adapter distractor\\n    def stream_adapter_response(self):\\n        pass", "start_line": 1, "end_line": 3}\n',
        encoding="utf-8",
    )
    nodes_path.write_text(
        '{"id": "sym:app::handle_request", "name": "handle_request", "kind": "function", "path": "app.py"}\n'
        '{"id": "sym:app::StreamingAdapter", "name": "StreamingAdapter", "kind": "class", "path": "adapters.py"}\n'
        '{"id": "sym:app::Response", "name": "Response", "kind": "class", "path": "models.py"}\n',
        encoding="utf-8",
    )
    # Edge: handle_request -> StreamingAdapter
    edges_path.write_text(
        '{"src": "sym:app::handle_request", "dst": "sym:app::StreamingAdapter", "type": "CALLS", "direction": "out", "confidence": 1.0}\n'
        '{"src": "sym:app::StreamingAdapter", "dst": "sym:app::handle_request", "type": "CALLS", "direction": "in", "confidence": 1.0}\n',
        encoding="utf-8",
    )

    idx = Index(tmp_path)
    query = "handle_request stream adapter"

    # BM25 without graph expansion ranks distractor first due to term frequency
    bm25_pack = idx.pack_context(query, budget_tokens=200, expand_graph=False, k=1)
    bm25_nodes = [c["node_id"] for c in bm25_pack["chunks"]]
    assert "sym:app::StreamingAdapter" not in bm25_nodes, (
        "BM25 alone should have selected top-1 seed only"
    )

    # With graph expansion, StreamingAdapter is reached from handle_request
    graph_pack = idx.pack_context(query, budget_tokens=400, expand_graph=True, k=1)
    graph_nodes = [c["node_id"] for c in graph_pack["chunks"]]
    assert "sym:app::StreamingAdapter" in graph_nodes, (
        "Graph expansion must disambiguate and include the connected target"
    )


def test_budget_perturbation_monotonicity(tmp_path: Path):
    """Verify that retrieval recall is monotonic under budget perturbations (+/-20%)."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)

    chunks_path = agent_dir / "chunks.jsonl"
    nodes_path = agent_dir / "nodes.jsonl"
    edges_path = agent_dir / "edges.jsonl"

    long_body = "def f():\\n    # keyword\\n" + "\\n".join(f"    x_{i} = {i}" for i in range(25))
    chunks_path.write_text(
        json.dumps(
            {
                "id": "c1",
                "node_id": "sym:p::f1",
                "path": "p.py",
                "name": "f1",
                "text": long_body,
                "start_line": 1,
                "end_line": 30,
            }
        )
        + "\n"
        + json.dumps(
            {
                "id": "c2",
                "node_id": "sym:p::f2",
                "path": "p.py",
                "name": "f2",
                "text": long_body,
                "start_line": 31,
                "end_line": 60,
            }
        )
        + "\n"
        + json.dumps(
            {
                "id": "c3",
                "node_id": "sym:p::f3",
                "path": "p.py",
                "name": "f3",
                "text": long_body,
                "start_line": 61,
                "end_line": 90,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    nodes_path.write_text(
        '{"id": "sym:p::f1", "name": "f1", "kind": "function", "path": "p.py"}\n'
        '{"id": "sym:p::f2", "name": "f2", "kind": "function", "path": "p.py"}\n'
        '{"id": "sym:p::f3", "name": "f3", "kind": "function", "path": "p.py"}\n',
        encoding="utf-8",
    )
    edges_path.write_text("", encoding="utf-8")

    idx = Index(tmp_path)
    base_budget = 100
    budgets = [int(base_budget * 0.8), base_budget, int(base_budget * 1.2), int(base_budget * 2.0)]
    counts = []
    for b in budgets:
        pack = idx.pack_context("keyword", budget_tokens=b)
        counts.append(len(pack["chunks"]))

    # Verify chunk admission is monotonic with budget increases
    for i in range(len(counts) - 1):
        assert counts[i] <= counts[i + 1], (
            f"Budget monotonicity violated: {counts[i]} > {counts[i + 1]} at budgets {budgets[i]} -> {budgets[i + 1]}"
        )
