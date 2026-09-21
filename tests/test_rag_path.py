# @authormark v1 -- do not remove (authorship watermark)⁠​‌​‌​‌‌​​‌​​​‌‌‌​‌‌‌​​​‌​‌‌​​​​‌​‌‌​‌​​​​‌​​‌‌‌‌​‌​‌​‌‌‌​‌​​​​​‌​‌‌‌​​‌‌​​‌​‌‌​‌​‌​‌​​​‌​‌​​​​‌​​‌​‌​​‌‌​‌‌​‌‌‌‌​‌‌​‌​​​​‌​‌​‌​​​‌‌‌​​​‌​‌​‌​‌​​​‌​‌​​‌​​‌​​‌‌‌‌​‌​‌​​‌​​​‌‌​‌‌​⁠
# Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
# Author: https://github.com/Srinivasan-78
# SPDX-License-Identifier: MIT
# Fingerprint: AMK1.VGqahOWAs-QBSohTqTROR6
"""End-to-end guards on the dense-retrieval path.

Before v1.4.0 `score_rrf()` accepted `vectors=`/`embedder=`, pyproject declared a
`rag` extra, and `cmd_rag()` passed neither -- so `repo2graph rag` was BM25-only
and the extra installed a dependency nothing called. Every test in that era
passed, because every one of them either called `score_rrf` directly with
vectors it supplied itself, or asserted on a pack that BM25 alone could produce.

The tests here are written to fail against that bug specifically. They assert on
the seam the bug lived in -- what `cmd_rag` actually hands to `score_rrf` -- and
on the observable difference dense fusion makes, rather than on either side's
internal agreement with itself.
"""

import json

import pytest

from repo2graph import embed as embed_mod
from repo2graph.cli import main
from repo2graph.export import make_paths
from repo2graph.query import Index

from conftest import MINI_QUERY, StubEmbedder, build_mini_index, write_mini_repo


@pytest.fixture
def embedded_index(tmp_path, monkeypatch):
    """A mini index with real vectors on disk, built through the CLI."""
    repo = write_mini_repo(tmp_path)
    out = build_mini_index(repo, tmp_path / "idx")
    stub = StubEmbedder()
    monkeypatch.setattr(embed_mod, "default_embedder", lambda name=None: stub)
    main(["embed", "-o", str(out)])
    return out


# ------------------------------------------------------------------ (b) ----


def test_cmd_rag_hands_score_rrf_a_real_vectors_argument(embedded_index, monkeypatch, capsys):
    """The regression test for the original silent failure.

    `cmd_rag --vectors` must reach `score_rrf` with a non-None `vectors=`. The
    pre-v1.4.0 bug was precisely that it did not, while every surface around it
    reported success.
    """
    seen = {}
    real = Index.score_rrf

    def spy(self, query, vectors=None, embedder=None):
        seen["vectors"] = vectors
        seen["embedder"] = embedder
        return real(self, query, vectors=vectors, embedder=embedder)

    monkeypatch.setattr(Index, "score_rrf", spy)
    main(["rag", MINI_QUERY, "-o", str(embedded_index), "--vectors"])
    capsys.readouterr()

    assert seen, "score_rrf was never called"
    assert seen["vectors"] is not None, "cmd_rag passed vectors=None"
    assert seen["embedder"] is not None, "cmd_rag passed embedder=None"


def test_embed_writes_npy_and_meta_to_disk(embedded_index):
    """A rag-enabled build leaves real .npy bytes behind, not just a report."""
    npy = make_paths(embedded_index, "vectors.npy")[0]
    meta = make_paths(embedded_index, "vectors.meta.json")[0]
    assert npy.is_file() and npy.stat().st_size > 0
    assert meta.is_file()

    # NPY v1.0 magic, so this is a real array file rather than an empty stub.
    assert npy.read_bytes()[:6] == b"\x93NUMPY"
    parsed = json.loads(meta.read_text(encoding="utf8"))
    assert parsed["model_id"] and parsed["dim"] > 0
    assert parsed["chunk_ids"], "meta must record row order by chunk id"


def test_index_reads_the_npy_back(embedded_index):
    """The vectors a later process loads are the ones that were written."""
    idx = Index(embedded_index)
    assert idx.vectors, "Index did not load vectors.npy"
    assert idx.vector_meta["dim"] == len(next(iter(idx.vectors.values())))


def test_dense_ranking_differs_from_bm25_only(embedded_index):
    """Fusion must actually change the ranking, or it is not doing anything.

    A rigged embedder puts the BM25 *last* candidate nearest the query. If the
    vectors reach the ranker the fused order differs from the lexical one; if
    they are dropped on the way, the two are identical -- which is exactly what
    the pre-v1.4.0 path produced.
    """
    idx = Index(embedded_index)
    lexical = idx.score(MINI_QUERY)
    assert len(lexical) > 1, "need at least two candidates to reorder"

    # Rig `vectors` so the query vector matches the worst lexical candidate.
    worst = lexical[-1][1]
    dim = idx.vector_meta["dim"]
    rigged = {i: [0.0] * (dim - 1) + [1.0] for _s, i in lexical}
    rigged[worst] = [1.0] + [0.0] * (dim - 1)
    rigged["query"] = [1.0] + [0.0] * (dim - 1)

    fused = idx.score_rrf(MINI_QUERY, vectors=rigged)
    assert [i for _s, i in fused] != [i for _s, i in lexical], (
        "fused ranking is identical to BM25 -- vectors never reached the ranker"
    )


def test_no_vectors_means_plain_bm25(embedded_index):
    """The zero-dependency floor is untouched: no vectors, no change."""
    idx = Index(embedded_index)
    assert idx.score_rrf(MINI_QUERY) == idx.score(MINI_QUERY)


# ------------------------------------------------------------------ (d) ----


def test_fusion_that_turns_itself_off_emits_a_structured_warning(embedded_index, capsys):
    """The silent degrade BACKLOG item 2 describes must now announce itself.

    `fuse_ok` passes -- model and width agree -- and fusion still abandons
    itself inside `_vectors_for` because one shortlisted chunk has no vector.
    That used to be silent, which meant a lexical answer to a question the
    caller explicitly asked to be answered densely.
    """
    idx = Index(embedded_index)
    lexical = idx.score(MINI_QUERY)
    dim = idx.vector_meta["dim"]
    # Every candidate vectorised except one: all-or-nothing, so fusion drops.
    holed = {i: [1.0] * dim for _s, i in lexical}
    holed.pop(lexical[0][1])
    holed["query"] = [1.0] * dim

    fused = idx.score_rrf(MINI_QUERY, vectors=holed)
    err = capsys.readouterr().err.strip().splitlines()

    assert fused == idx.score(MINI_QUERY), "expected a BM25 fallback here"
    record = json.loads(err[-1])
    assert record["event"] == "rag_fusion_disabled"
    assert record["level"] == "warning"
    assert record["fused"] == 0
    assert "embed" in record["action"]
    assert idx.fusion_coverage == (0, record["candidates"])


def test_successful_fusion_records_full_coverage(embedded_index, capsys):
    """The happy path sets coverage and says nothing on stderr."""
    idx = Index(embedded_index)
    lexical = idx.score(MINI_QUERY)
    dim = idx.vector_meta["dim"]
    full = {i: [1.0] * dim for _s, i in lexical}
    full["query"] = [1.0] * dim

    idx.score_rrf(MINI_QUERY, vectors=full)
    fused, candidates = idx.fusion_coverage
    assert fused == candidates > 0
    assert capsys.readouterr().err == "", "a working fusion must be silent"


# ------------------------------------------------------------------ (c) ----


def test_verify_rag_reports_a_healthy_index(embedded_index, monkeypatch, capsys):
    """--verify-rag exits 0 and names the model and dimension."""
    monkeypatch.setattr(embed_mod, "default_embedder", lambda name=None: StubEmbedder())
    assert main(["embed", "-o", str(embedded_index), "--verify-rag"]) == 0
    report = json.loads(capsys.readouterr().out)

    assert report["ok"] is True and report["error"] is None
    assert report["vectors_present"] is True
    assert report["model_id"] == report["embedder_model_id"]
    assert report["dim"] == report["embedder_dim"] > 0
    assert report["unvectorised_chunks"] == 0


def test_verify_rag_fails_when_the_index_has_no_vectors(tmp_path, capsys):
    """No vectors is a non-zero exit with an actionable message."""
    repo = write_mini_repo(tmp_path)
    out = build_mini_index(repo, tmp_path / "idx")
    capsys.readouterr()  # discard the build report
    with pytest.raises(SystemExit) as exc:
        main(["embed", "-o", str(out), "--verify-rag"])
    assert exc.value.code == 1

    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert report["vectors_present"] is False
    assert "repo2graph embed" in report["error"]


def test_verify_rag_fails_on_a_model_mismatch(embedded_index, monkeypatch, capsys):
    """A different active embedder is a refusal naming both sides."""
    other = StubEmbedder()
    other.model_id = "some/other-model"
    monkeypatch.setattr(embed_mod, "default_embedder", lambda name=None: other)

    with pytest.raises(SystemExit) as exc:
        main(["embed", "-o", str(embedded_index), "--verify-rag"])
    assert exc.value.code == 1

    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert "some/other-model" in report["error"]
    assert report["model_id"] in report["error"]


def test_verify_rag_fails_on_a_dimension_mismatch(embedded_index, monkeypatch, capsys):
    """Same model id, different width: still a refusal, never a best effort."""
    narrow = StubEmbedder()
    narrow.dim = 3
    monkeypatch.setattr(embed_mod, "default_embedder", lambda name=None: narrow)

    with pytest.raises(SystemExit) as exc:
        main(["embed", "-o", str(embedded_index), "--verify-rag"])
    assert exc.value.code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert report["embedder_dim"] == 3


def test_verify_rag_reports_partial_chunk_coverage(embedded_index, monkeypatch, capsys):
    """The failure fuse_ok cannot see: some chunks have no vector at all."""
    monkeypatch.setattr(embed_mod, "default_embedder", lambda name=None: StubEmbedder())
    # The real-world shape of this failure: chunks.jsonl is rebuilt (gaining a
    # chunk) without re-running `embed`, so the new chunk has no vector while
    # the model id and width still agree and fuse_ok still passes.
    chunks_path = make_paths(embedded_index, "chunks.jsonl")[0]
    with open(chunks_path, "a", encoding="utf8", newline="\n") as fh:
        fh.write(
            json.dumps(
                {
                    "id": "chunk:added-after-embed",
                    "node_id": "file:pkg/gateway.py",
                    "path": "pkg/gateway.py",
                    "text": "a chunk added after the embed ran",
                }
            )
            + "\n"
        )
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc:
        main(["embed", "-o", str(embedded_index), "--verify-rag"])
    assert exc.value.code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["unvectorised_chunks"] >= 1
    assert "no vector" in report["error"]


def test_verify_rag_never_defaults_the_model_to_the_index_claim(embedded_index, monkeypatch):
    """The guard must compare against the *configured* model, not the index's.

    Defaulting the query-side model id to `vector_meta["model_id"]` makes
    fuse_ok compare a value with itself: permanently true, and the guard is
    gone. This asserts the default that reaches `default_embedder` is the
    built-in one, never the index's claim.
    """
    names = []

    def fake(name=None):
        names.append(name)
        return StubEmbedder()

    monkeypatch.setattr(embed_mod, "default_embedder", fake)
    main(["embed", "-o", str(embedded_index), "--verify-rag"])

    index_model = json.loads(make_paths(embedded_index, "vectors.meta.json")[0].read_text("utf8"))[
        "model_id"
    ]
    assert names == [None], names
    assert index_model not in [n for n in names if n]


# ------------------------------------------------------- no network -------


def test_the_default_rag_path_opens_no_socket(embedded_index, monkeypatch, capsys):
    """A plain `rag` must not touch the network, with or without vectors on disk."""
    import socket

    def boom(*a, **kw):
        raise AssertionError("the default rag path opened a socket")

    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    main(["rag", MINI_QUERY, "-o", str(embedded_index)])
    assert capsys.readouterr().out
