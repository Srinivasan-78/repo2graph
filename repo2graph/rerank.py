"""Opt-in cross-encoder reranking of first-stage candidates (#381).

BM25 (optionally fused with dense vectors) scores each term independently, so a
correct hit and an unrelated one can tie: on the VS Code example, "how does a
command reach its handler" put `Command` at 8.184 and an unrelated telemetry
classification at 8.072. A cross-encoder reads the question and the chunk
together and breaks exactly that kind of tie. It matters more here than in
ordinary search because `pack_context` spends a hard budget in rank order: an
irrelevant chunk at rank 2 takes room the right one at rank 6 then cannot have.

Only the order of the top `CROSS_ENCODER_CANDIDATES` changes. Their score values are
kept and handed out again in the new order, so every later stage -- the
answerability re-rank, `is_lexical_weak`, the budget -- sees the same scale it
always has, and the budget accounting is untouched.

The model runs locally (the `rag` extra's sentence-transformers). Any failure --
the extra missing, the model not loadable, a scoring error -- leaves the
ranking as it was and says so on stderr as one `rag_rerank_disabled` line,
the same rule as `rag_fusion_disabled` for dense fusion.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any, Protocol

#: A small MS MARCO cross-encoder (~90 MB). Pin a commit with `name@<sha>`.
DEFAULT_RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
#: How many first-stage candidates the cross-encoder re-orders.
CROSS_ENCODER_CANDIDATES = 50
#: Characters of each chunk the cross-encoder reads. Its window is 512 tokens,
#: so more than this is truncated by the model anyway; cutting here bounds the
#: tokenizer's work on a very large chunk.
RERANK_TEXT_CHARS = 2_000


class Reranker(Protocol):
    """Scores (query, text) pairs jointly; higher means more relevant."""

    model_id: str

    def score(self, query: str, texts: Sequence[str]) -> Sequence[float]: ...


class _CrossEncoderReranker:
    def __init__(self, model: Any, model_id: str) -> None:
        self.model = model
        self.model_id = model_id

    def score(self, query: str, texts: Sequence[str]) -> Sequence[float]:
        return [float(x) for x in self.model.predict([(query, t) for t in texts])]


def default_reranker(name: str | None = None) -> Reranker:
    """Load a sentence-transformers CrossEncoder, or raise RuntimeError saying why."""
    from .embed import split_model_spec

    spec = name or DEFAULT_RERANK_MODEL
    repo_id, revision = split_model_spec(spec)
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:
        raise RuntimeError(
            "reranking needs the `rag` extra: pip install 'repo2graph[rag]'"
        ) from exc
    kwargs = {"revision": revision} if revision else {}
    try:
        model = CrossEncoder(repo_id, **kwargs)
    # No `noqa: BLE001` needed, and having one would hide its loss: the broad
    # catch is fine here *because* the next line re-raises, so narrowing the
    # catch would not change behaviour but swallowing would -- and a stale
    # directive would suppress the warning that said so.
    except Exception as exc:  # any load failure (network, disk, format) is reported, never fatal
        raise RuntimeError(f"could not load reranker {spec}: {type(exc).__name__}: {exc}") from exc
    return _CrossEncoderReranker(model, spec)


def rerank(
    query: str,
    ranked: list[tuple[float, int]],
    text_of: Callable[[int], str],
    reranker: Reranker,
    limit: int = CROSS_ENCODER_CANDIDATES,
) -> tuple[list[tuple[float, int]], str]:
    """Re-order the top `limit` of `ranked` by `reranker`; `(ranked, reason)`.

    `ranked` is `(score, chunk_index)` in descending score order. On success
    `reason` is empty; on any failure the input comes back unchanged with the
    reason, for the caller to report.
    """
    head = ranked[:limit]
    if len(head) < 2:
        return ranked, ""
    try:
        scores = list(reranker.score(query, [text_of(i)[:RERANK_TEXT_CHARS] for _, i in head]))
    except Exception as exc:  # noqa: BLE001 - a failing reranker degrades to the first-stage order, loudly
        return ranked, f"{type(exc).__name__}: {exc}"
    if len(scores) != len(head):
        return ranked, f"reranker returned {len(scores)} scores for {len(head)} candidates"
    if not all(isinstance(s, (int, float)) and math.isfinite(s) for s in scores):
        return ranked, "reranker returned a non-finite score"
    order = sorted(range(len(head)), key=lambda p: (-scores[p], p))
    values = [s for s, _ in head]
    return [(values[r], head[p][1]) for r, p in enumerate(order)] + ranked[limit:], ""
