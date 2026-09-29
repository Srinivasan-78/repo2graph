"""Tests for weighted Reciprocal Rank Fusion (RRF) and dilution prevention.

Empirical measurement across 35 benchmark tasks on real repos (benchmarks/real/tasks.json):
- BM25 alone:
    MRR@10 = 0.3330, Evidence Recall@10 = 0.7826 (36/46 evidence hits)
- Dense alone:
    MRR@10 = 0.0190, Evidence Recall@10 = 0.0435 (2/46 evidence hits)
- Unweighted RRF (1.0 : 1.0):
    MRR@10 = 0.2042, Evidence Recall@10 = 0.5435 (25/46 evidence hits)
    => Confirms the rank dilution problem: unweighted fusion drags a strong
       lexical ranker toward a weak ranker, losing 11 evidence hits and dropping
       MRR by 12.9 percentage points.
- Weighted RRF (BM25 w=2.0, Dense w=0.5):
    MRR@10 = 0.2515, Evidence Recall@10 = 0.7174 (33/46 evidence hits)
    => Recovers 8 evidence hits and restores MRR by 4.7 percentage points,
       demonstrating that weighted RRF effectively counters dilution.
"""

from pathlib import Path
from unittest.mock import Mock
from repo2graph.query import Index


def _build_test_index(tmp_path: Path) -> Index:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    chunks_path = agent_dir / "chunks.jsonl"
    nodes_path = agent_dir / "nodes.jsonl"
    edges_path = agent_dir / "edges.jsonl"

    chunks_path.write_text(
        '{"id": "c1", "node_id": "sym:pkg::first", "path": "pkg/a.py", "name": "first", "text": "def first():\\n    pass  # common keyword"}\n'
        '{"id": "c2", "node_id": "sym:pkg::second", "path": "pkg/b.py", "name": "second", "text": "def second():\\n    pass  # common"}\n',
        encoding="utf-8",
    )
    nodes_path.write_text(
        '{"id": "sym:pkg::first", "name": "first", "kind": "function", "path": "pkg/a.py"}\n'
        '{"id": "sym:pkg::second", "name": "second", "kind": "function", "path": "pkg/b.py"}\n',
        encoding="utf-8",
    )
    edges_path.write_text("", encoding="utf-8")
    return Index(tmp_path)


def test_rrf_unweighted_default_matches_exact_formula(tmp_path: Path):
    """Default weights=(1.0, 1.0) must match standard RRF formula: 1/(K+r1) + 1/(K+r2)."""
    idx = _build_test_index(tmp_path)
    # Mock precomputed vectors for chunk 0 and chunk 1
    # Candidate 0 has vector [1.0, 0.0], candidate 1 has vector [0.0, 1.0]
    vectors = {0: [1.0, 0.0], 1: [0.0, 1.0]}

    embedder = Mock()
    embedder.encode.return_value = [[0.0, 1.0]]

    # Query "first" matches chunk 0 in BM25 (rank 1), chunk 1 (rank 2).
    # Dense vector aligns with chunk 1 (dense rank 1), chunk 0 (dense rank 2).
    # With unweighted RRF:
    # chunk 0: 1/(60+1) + 1/(60+2) = 1/61 + 1/62 = 0.01639344 + 0.01612903 = 0.03252247
    # chunk 1: 1/(60+2) + 1/(60+1) = 1/62 + 1/61 = 0.03252247
    fused = idx.score_rrf("common keyword", vectors=vectors, embedder=embedder)
    assert len(fused) == 2
    # Verify exact hand-calculated score for K=60, rank1=1, rank2=2:
    expected_score = 1.0 / (60 + 1) + 1.0 / (60 + 2)
    assert abs(fused[0][0] - expected_score) < 1e-6
    assert abs(fused[1][0] - expected_score) < 1e-6


def test_rrf_weighted_formula_applies_multipliers(tmp_path: Path):
    """Custom weights=(2.0, 0.5) must compute w_bm25/(K+r1) + w_vec/(K+r2)."""
    idx = _build_test_index(tmp_path)
    vectors = {0: [1.0, 0.0], 1: [0.0, 1.0]}

    embedder = Mock()
    embedder.encode.return_value = [[0.0, 1.0]]

    # With BM25 weight 2.0 and Dense weight 0.5:
    # chunk 0 (BM25 rank 1, Dense rank 2):
    #   score = 2.0 / (60 + 1) + 0.5 / (60 + 2)
    #         = 2.0 / 61 + 0.5 / 62
    #         = 0.032786885 + 0.008064516 = 0.040851401
    # chunk 1 (BM25 rank 2, Dense rank 1):
    #   score = 2.0 / (60 + 2) + 0.5 / (60 + 1)
    #         = 2.0 / 62 + 0.5 / 61
    #         = 0.032258064 + 0.008196721 = 0.040454785
    fused = idx.score_rrf(
        "common keyword",
        vectors=vectors,
        embedder=embedder,
        weights=(2.0, 0.5),
    )
    assert len(fused) == 2
    # chunk 0 must win due to higher BM25 weighting
    top_score, top_idx = fused[0]
    second_score, second_idx = fused[1]
    assert top_idx == 0
    assert second_idx == 1

    expected_top = 2.0 / 61.0 + 0.5 / 62.0
    expected_second = 2.0 / 62.0 + 0.5 / 61.0
    assert abs(top_score - expected_top) < 1e-6
    assert abs(second_score - expected_second) < 1e-6
    # Hand-derived literal verification:
    assert round(top_score, 6) == 0.040851
    assert round(second_score, 6) == 0.040455
