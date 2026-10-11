"""Opt-in cross-encoder reranking (#381): it re-orders candidates, never the budget.

No model is downloaded here: a fake reranker stands in for the cross-encoder,
and `sentence_transformers` is forced absent wherever the real loader runs.
"""

from __future__ import annotations

import json
import sys
import types

import pytest

from repo2graph.cli import main
from repo2graph.query import Index
from repo2graph.rerank import CROSS_ENCODER_CANDIDATES, default_reranker, rerank

QUERY = "how is a session token refreshed"

FILES = {
    "auth/session.py": (
        "def refresh_session_token(session):\n"
        '    """Swap an expiring session token for a fresh one."""\n'
        "    session.token = issue(session.user)\n"
        "    return session.token\n"
    ),
    "auth/tokens.py": "def issue(user):\n    return f'token-{user}'\n",
    "telemetry/session_stats.py": (
        "def session_token_refresh_counter(events):\n"
        "    # counts how often a session token refresh event was logged\n"
        "    return sum(1 for e in events if e == 'session token refreshed')\n"
    ),
}


class Prefers:
    """Scores a chunk by whether its text contains `needle`."""

    model_id = "fake/prefers"

    def __init__(self, needle: str) -> None:
        self.needle = needle
        self.calls: list[int] = []

    def score(self, query, texts):
        self.calls.append(len(texts))
        return [1.0 if self.needle in t else 0.0 for t in texts]


class Broken:
    model_id = "fake/broken"

    def __init__(self, result=None, exc: Exception | None = None) -> None:
        self.result, self.exc = result, exc

    def score(self, query, texts):
        if self.exc:
            raise self.exc
        return self.result


@pytest.fixture
def out(tmp_path):
    repo = tmp_path / "src"
    for rel, text in FILES.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf8")
    idx = tmp_path / "idx"
    main(["build", str(repo), "-o", str(idx), "--formats", "jsonl"])
    return idx


def test_rerank_reorders_seeds_but_keeps_the_score_scale(out):
    idx = Index(out)
    ranked = idx.score(QUERY)
    moved = idx.rerank(QUERY, ranked, Prefers("f'token-"))
    assert sorted(s for s, _ in moved) == sorted(s for s, _ in ranked)  # same values, new order
    assert [s for s, _ in moved] == sorted((s for s, _ in moved), reverse=True)
    assert idx.chunks[moved[0][1]]["node_id"] == "sym:auth/tokens.py::issue"
    # 600 characters hold one seed: the budget is spent on whichever ranks first.
    plain = idx.pack_context(QUERY, budget_chars=600, expand_graph=False)
    packed = idx.pack_context(
        QUERY, budget_chars=600, expand_graph=False, rerank=Prefers("f'token-")
    )
    assert [c["node_id"] for c in plain["chunks"]] == [
        "sym:telemetry/session_stats.py::session_token_refresh_counter"
    ]
    assert [c["node_id"] for c in packed["chunks"]] == ["sym:auth/tokens.py::issue"]
    assert packed["budget_chars"] == plain["budget_chars"] == 600


def test_only_the_candidate_window_is_reordered():
    ranked = [(float(100 - i), i) for i in range(CROSS_ENCODER_CANDIDATES + 10)]

    class Reverse:
        model_id = "fake/reverse"

        def score(self, query, texts):
            return list(range(len(texts)))  # last candidate scores highest

    got, why = rerank("q", ranked, str, Reverse())
    assert why == ""
    head = [i for _, i in got[:CROSS_ENCODER_CANDIDATES]]
    assert head == list(range(CROSS_ENCODER_CANDIDATES))[::-1]
    assert got[CROSS_ENCODER_CANDIDATES:] == ranked[CROSS_ENCODER_CANDIDATES:]


@pytest.mark.parametrize(
    "bad, reason",
    [
        (Broken(exc=RuntimeError("cuda out of memory")), "RuntimeError: cuda out of memory"),
        (Broken(result=[1.0]), "reranker returned 1 scores"),
        (Broken(result=None), "TypeError"),
    ],
)
def test_a_failing_reranker_falls_through_loudly(out, capsys, bad, reason):
    idx = Index(out)
    plain = idx.pack_context(QUERY, budget_chars=4000)
    capsys.readouterr()
    packed = idx.pack_context(QUERY, budget_chars=4000, rerank=bad)
    assert packed == plain
    (line,) = capsys.readouterr().err.strip().splitlines()
    record = json.loads(line)
    assert record["event"] == "rag_rerank_disabled" and record["level"] == "warning"
    assert reason in record["reason"]


def test_a_non_finite_score_is_refused():
    got, why = rerank("q", [(2.0, 0), (1.0, 1)], str, Broken(result=[float("nan"), 1.0]))
    assert got == [(2.0, 0), (1.0, 1)] and "non-finite" in why


def test_retrieve_takes_the_reranker_too(out):
    idx = Index(out)
    plain = idx.retrieve(QUERY, k=2, hops=0)
    target = plain[-1]["node_id"]
    got = idx.retrieve(QUERY, k=2, hops=0, rerank=Prefers(target.split("::")[-1]))
    assert got[0]["node_id"] == target


def test_cli_without_the_extra_is_byte_identical_and_says_why(out, capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    main(["rag", str(out), QUERY])
    plain = capsys.readouterr()
    main(["rag", str(out), QUERY, "--rerank"])
    reranked = capsys.readouterr()
    assert reranked.out == plain.out
    (line,) = [ln for ln in reranked.err.splitlines() if "rag_rerank_disabled" in ln]
    record = json.loads(line)
    assert "pip install 'repo2graph[rag]'" in record["reason"]
    main(["query", "-o", str(out), QUERY, "--rerank"])
    assert "rag_rerank_disabled" in capsys.readouterr().err


def test_default_reranker_passes_a_pinned_revision(monkeypatch):
    seen = {}

    class FakeCE:
        def __init__(self, repo_id, **kw):
            seen.update(repo_id=repo_id, **kw)

        def predict(self, pairs):
            return [len(t) for _q, t in pairs]

    monkeypatch.setitem(
        sys.modules, "sentence_transformers", types.SimpleNamespace(CrossEncoder=FakeCE)
    )
    r = default_reranker("org/ce@0123abc")
    assert seen == {"repo_id": "org/ce", "revision": "0123abc"}
    assert r.model_id == "org/ce@0123abc"
    assert list(r.score("q", ["a", "abc"])) == [1.0, 3.0]
