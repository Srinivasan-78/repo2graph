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

from collections.abc import Callable
from pathlib import Path
from typing import Any

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


def _fit(records: list[Record], cost: Callable[[Record], int], budget: int | None) -> list[Record]:
    """The records that fit, in rank order; `None` is the only unbounded value.

    `None` and `0` have to stay distinguishable (#482). A caller's budget of 0
    means "no ceiling", the convention `query.pack_context` also uses
    (`bounded = budget > 0`), but 0 is equally what is *left* of a positive
    budget the header already spent -- and reading that as unbounded is how a
    budget too small for the header returned a pack 25x over it, with
    `truncated` reporting False.
    """
    if budget is None:
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
    room = None if budget_chars <= 0 else budget_chars
    return _fit(merge_by_rank(per), lambda r: len(r.get("text") or ""), room)


def _head(names: list[str], measure: Callable[[str], int], budget: int | None) -> str:
    """`# Context from N repositories: ...`, listing as many names as fit.

    Byte-identical to the full list whenever it fits, which at any ordinary
    budget it does -- a name costs about two tokens, so it takes hundreds of
    indexes to trouble a 2,000-token pack. Past that the list is cut rather
    than allowed to push the pack over its ceiling, which is what a header
    counted against the budget but never bounded by it used to do (#482). The
    count is the part that survives longest: it says how much was searched,
    which the names only elaborate.
    """
    lead = f"# Context from {len(names)} repositories"
    full = f"{lead}: {', '.join(names)}\n\n"
    if budget is None or measure(full) <= budget:
        return full
    for k in range(len(names) - 1, 0, -1):
        cut = f"{lead}: {', '.join(names[:k])} and {len(names) - k} more\n\n"
        if measure(cut) <= budget:
            return cut
    return f"{lead}\n\n" if measure(f"{lead}\n\n") <= budget else ""


def pack_context(
    indexes: list[tuple[str, Index]],
    query: str,
    *,
    budget_chars: int = 24000,
    budget_tokens: int | None = None,
    count_tokens: Callable[[str], int] = count_tokens,
    token_margin: float = 0.0,
    **kw: Any,
) -> dict[str, Any]:
    """A cited pack drawn from every index, under one budget.

    Each index packs as it would alone (no repo map: maps do not merge), and
    the chunks the packs chose are interleaved by rank and re-cut to fit.

    The budget is accounted for exactly as `Index.pack_context` accounts for
    it, and the result carries the same `ContextPack` keys. The two used to
    diverge: `--token-margin` reached the per-index packs but never the fit
    over the merged list, and the six budget-reporting fields -- including the
    `token_count_method` that says how `tokens_used` was counted -- were
    absent from a federated `rag --json` altogether (#482).
    """
    per = []
    for repo, idx in indexes:
        pack = idx.pack_context(
            query,
            budget_chars=budget_chars,
            budget_tokens=budget_tokens,
            count_tokens=count_tokens,
            token_margin=token_margin,
            **kw,
        )
        per.append((repo, pack["chunks"]))
    merged = merge_by_rank(per)

    def block(r: Record) -> str:
        return _cite_block({**r, "path": f"{r['repo']}:{r.get('path') or ''}"}, r.get("text") or "")

    use_tokens = budget_tokens is not None
    measure: Callable[[str], int] = count_tokens if use_tokens else len
    budget = budget_tokens if budget_tokens is not None else budget_chars
    if use_tokens and token_margin > 0 and budget > 0:
        budget = max(1, int(budget / (1 + token_margin)))
    bounded = budget > 0
    head = _head([n for n, _ in indexes], measure, budget if bounded else None)
    # The header is part of the pack, so it is paid for out of the budget. A
    # header that leaves no room is 0, not unbounded; only the caller's own 0
    # means that.
    room = max(0, budget - measure(head)) if bounded else None
    picked = _fit(merged, lambda r: measure(block(r)), room)
    markdown = head + "".join(block(r) for r in picked)
    truncated = len(picked) < len(merged)
    return {
        "markdown": markdown,
        "chunks": picked,
        "seeds": [c for c in picked if c.get("why") == "seed"],
        "neighbors": [c for c in picked if c.get("why") != "seed"],
        "truncated": truncated,
        "repos": [n for n, _ in indexes],
        "query": query,
        "used_chars": len(markdown),
        "tokens_used": count_tokens(markdown),
        "budget_chars": budget_chars,
        "tokens_budget": budget_tokens or 0,
        "source_text_chars": sum(len(c.get("text") or "") for c in picked),
        "rendered_context_chars": len(markdown),
        "budget_exhausted": truncated,
        "omitted_chunk_count": len(merged) - len(picked),
        "token_count_method": getattr(count_tokens, "token_count_method", "caller-supplied"),
        "token_safety_margin": token_margin if use_tokens else 0.0,
    }
