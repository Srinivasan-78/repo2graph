"""Tests for BM25 inverted index persistence (issue #379).

Verifies:
- BM25 artifacts (agent/bm25.jsonl, agent/bm25.meta.json) are written at build time.
- Index() loads BM25 from disk quickly without re-tokenization.
- Scored results from disk-loaded BM25 match dynamic recomputation identically.
- Missing, corrupted, or stale BM25 artifacts fall back silently to dynamic recomputation.
- Mismatched chunk_ids fall back silently to dynamic recomputation.
- Symlinked BM25 artifacts are safely refused.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo2graph.cli import main
from repo2graph.export import path as artifact_path
from repo2graph.query import BM25_FORMAT, Index


@pytest.fixture
def indexed_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "math_ops.py").write_text(
        "def add(a, b):\n    '''Add two numbers.'''\n    return a + b\n\n"
        "def subtract(a, b):\n    '''Subtract b from a.'''\n    return a - b\n",
        encoding="utf-8",
    )
    (repo / "string_ops.py").write_text(
        "def concat(s1, s2):\n    '''Concatenate two strings.'''\n    return s1 + s2\n",
        encoding="utf-8",
    )
    out = tmp_path / "index"
    ret = main(["build", str(repo), "-o", str(out)])
    assert ret == 0
    return repo, out


def test_bm25_artifacts_generated_on_build(indexed_repo: tuple[Path, Path]) -> None:
    _, out = indexed_repo
    bm25_file = artifact_path(out, "bm25.jsonl")
    meta_file = artifact_path(out, "bm25.meta.json")

    assert bm25_file.is_file()
    assert meta_file.is_file()

    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    assert meta["format"] == BM25_FORMAT
    assert meta["count"] > 0
    assert len(meta["chunk_ids"]) == meta["count"]
    assert len(meta["lengths"]) == meta["count"]
    assert meta["avgdl"] > 0

    lines = [ln.strip() for ln in bm25_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) > 0
    sample = json.loads(lines[0])
    assert "t" in sample
    assert "p" in sample


def test_index_loads_persisted_bm25(
    indexed_repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, out = indexed_repo

    # Spy on _build_bm25 to verify it is NOT called when persisted BM25 is loaded
    built_called = False
    orig_build = Index._build_bm25

    def mock_build(self):
        nonlocal built_called
        built_called = True
        return orig_build(self)

    monkeypatch.setattr(Index, "_build_bm25", mock_build)

    idx = Index(out)
    assert not built_called
    assert len(idx.postings) > 0
    assert len(idx.lengths) == len(idx.chunks)


def test_persisted_scoring_matches_dynamic_scoring(indexed_repo: tuple[Path, Path]) -> None:
    _, out = indexed_repo

    # 1. Scored using persisted BM25
    idx_loaded = Index(out)
    query = "add two numbers"
    scores_loaded = idx_loaded.score(query)

    # 2. Force dynamic rebuild on a second Index instance
    idx_dynamic = Index(out)
    idx_dynamic._build_bm25()
    scores_dynamic = idx_dynamic.score(query)

    assert scores_loaded == scores_dynamic


def test_missing_bm25_falls_back_silently(indexed_repo: tuple[Path, Path]) -> None:
    _, out = indexed_repo
    artifact_path(out, "bm25.jsonl").unlink()

    idx = Index(out)
    assert len(idx.postings) > 0
    assert len(idx.lengths) == len(idx.chunks)
    scores = idx.score("add two numbers")
    assert len(scores) > 0


def test_stale_build_id_falls_back_silently(indexed_repo: tuple[Path, Path]) -> None:
    _, out = indexed_repo
    meta_path = artifact_path(out, "bm25.meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["build_id"] = "stale-fake-build-id-999"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    idx = Index(out)
    assert len(idx.postings) > 0
    scores = idx.score("add two numbers")
    assert len(scores) > 0


def test_chunk_ids_mismatch_falls_back_silently(indexed_repo: tuple[Path, Path]) -> None:
    _, out = indexed_repo
    meta_path = artifact_path(out, "bm25.meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["chunk_ids"][0] = "corrupted_or_different_chunk_id"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    idx = Index(out)
    assert len(idx.postings) > 0
    scores = idx.score("add two numbers")
    assert len(scores) > 0


def test_symlinked_bm25_falls_back_silently(
    indexed_repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, out = indexed_repo
    bm25_file = artifact_path(out, "bm25.jsonl")
    monkeypatch.setattr(Path, "is_symlink", lambda self: str(self) == str(bm25_file))

    idx = Index(out)
    assert len(idx.postings) > 0
    scores = idx.score("add two numbers")
    assert len(scores) > 0
