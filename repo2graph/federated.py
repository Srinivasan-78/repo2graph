"""One question over several indexes (#396).

`query` and `rag` take `--index DIR` more than once. Each index is searched
on its own, exactly as a single-index call would search it, and the results
are merged by rank: BM25 scores depend on each corpus's own statistics, so a
score from one index says nothing about a score from another, but "third best
here" and "third best there" are comparable. The merged list is then held to
one budget, not one per index, and every result names the repository it came
from -- in a citation, `[cite: <repo>:<path>:<start>-<end>]`.

An index that is missing or unreadable is skipped with a warning; the others
still answer. No graph is merged and no id changes, so nothing is rebuilt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .events import diagnostic
from .query import Index, Record, _cite_block, count_tokens

RRF_K = 60


def open_indexes(dirs: list[str | Path]) -> list[tuple[str, Index]]:
    """(repo name, Index) per readable directory; the rest are skipped, loudly."""
    opened: list[tuple[str, Index]] = []
    seen: dict[str, int] = {}
    for d in dirs:
        try:
            idx = Index(Path(d))
        except (OSError, ValueError) as exc:
            diagnostic(f"warning: skipping index {d}: {exc}")
            continue
        name = str(idx.manifest.get("repo") or Path(d).resolve().parent.name or d)
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:
            name = f"{name}#{seen[name]}"
        opened.append((name, idx))
    if not opened:
        raise SystemExit("error: none of the --index directories could be read")
    return opened


def merge_by_rank(per_index: list[tuple[str, list[Record]]]) -> list[Record]:
    """Interleave each index's ranked list by reciprocal rank, tagging the repo."""
    scored = []
    for order, (repo, records) in enumerate(per_index):
        for rank, r in enumerate(records, 1):
            scored.append((1.0 / (RRF_K + rank), order, rank, {**r, "repo": repo}))
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))
    return [r for *_rest, r in scored]


def _fit(records: list[Record], cost: Callable[[Record], int], budget: int) -> list[Record]:
    if budget <= 0:
        return records
    kept, used = [], 0
    for r in records:
        c = cost(r)
        if used + c <= budget:
            kept.append(r)
            used += c
    return kept


def retrieve(
    indexes: list[tuple[str, Index]], query: str, budget_chars: int, **kw: Any
) -> list[Record]:
    """`Index.retrieve` on each index, merged, under one character budget."""
    per = [(repo, idx.retrieve(query, budget_chars=budget_chars, **kw)) for repo, idx in indexes]
    return _fit(merge_by_rank(per), lambda r: len(r.get("text") or ""), budget_chars)


def pack_context(
    indexes: list[tuple[str, Index]],
    query: str,
    *,
    budget_chars: int = 24000,
    budget_tokens: int | None = None,
    count_tokens: Callable[[str], int] = count_tokens,
    **kw: Any,
) -> dict[str, Any]:
    """A cited pack drawn from every index, under one budget.

    Each index packs as it would alone (no repo map: maps do not merge), and
    the chunks the packs chose are interleaved by rank and re-cut to fit.
    """
    per = []
    for repo, idx in indexes:
        pack = idx.pack_context(
            query,
            budget_chars=budget_chars,
            budget_tokens=budget_tokens,
            count_tokens=count_tokens,
            **kw,
        )
        per.append((repo, pack["chunks"]))
    merged = merge_by_rank(per)

    def block(r: Record) -> str:
        return _cite_block({**r, "path": f"{r['repo']}:{r.get('path') or ''}"}, r.get("text") or "")

    head = f"# Context from {len(indexes)} repositories: {', '.join(n for n, _ in indexes)}\n\n"
    measure: Callable[[str], int] = count_tokens if budget_tokens is not None else len
    budget = budget_tokens if budget_tokens is not None else budget_chars
    picked = _fit(
        merged, lambda r: measure(block(r)), max(0, budget - measure(head)) if budget else 0
    )
    markdown = head + "".join(block(r) for r in picked)
    return {
        "markdown": markdown,
        "chunks": picked,
        "seeds": [c for c in picked if c.get("why") == "seed"],
        "neighbors": [c for c in picked if c.get("why") != "seed"],
        "truncated": len(picked) < len(merged),
        "repos": [n for n, _ in indexes],
        "query": query,
        "used_chars": len(markdown),
        "tokens_used": count_tokens(markdown),
        "budget_chars": budget_chars,
        "tokens_budget": budget_tokens or 0,
    }
