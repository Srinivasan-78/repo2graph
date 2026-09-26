"""The "Confidence and limitations" segment appended to an LLM answer.

Computed by repo2graph from the pack that was actually sent, never asked of
the model. That distinction is the whole point: a model asked to rate its own
confidence produces a number with no referent, and a model asked what it
might have missed cannot know what the retrieval step never showed it. Every
figure here is counted from the pack.

What it reports, in the order a reader needs it:

1. **What the answer rests on** -- how many cited blocks, from how many files,
   and how many arrived as graph neighbours rather than text matches.
2. **How much of it is certain** -- the confidence of the edges that pulled
   those neighbours in, and how many were ambiguous.
3. **What was cut** -- whether the budget truncated the pack, because an
   answer assembled from a truncated pack can be wrong by omission and
   nothing else in the output says so.
4. **What the graph cannot see** -- the standing limits, stated once so a
   reader does not have to have read docs/limitations.md to calibrate.

The block is plain ASCII on purpose: it is written to a console that may be a
strict cp1252 stream on Windows, and an em dash there is a crash, not a
typographic nicety. See AGENTS.md on encoding.
"""

from __future__ import annotations

import re
from typing import Any

# `why` on a packed chunk is either "seed" or "<EDGE_TYPE> <dir> of <name>",
# produced by query.expand. Parsing it back is not elegant, but the pack is
# the artifact that was actually sent to the provider, and re-deriving the
# traversal instead would describe a walk that may not be the one that
# produced this answer.
_WHY_EDGE = re.compile(r"^([A-Z_]+)\s+(in|out)\s+of\s+(.+)$")

# Stated once, here, rather than left for the reader to infer from silence.
# Kept in step with docs/limitations.md, which carries the long form.
STANDING_LIMITS = (
    "calls made through dynamic dispatch, reflection, or a DI container are not "
    "edges in this graph, so a caller list can be incomplete",
    "the graph models the code as written, not as executed: an edge is not proof the line runs",
    "only files that were indexed are visible -- anything excluded by a filter, "
    "a .gitignore, or a size limit is absent rather than reported as missing",
)


def _edge_stats(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts over the reasons each non-seed block was included."""
    by_type: dict[str, int] = {}
    ambiguous = 0
    for c in chunks:
        why = str(c.get("why") or "")
        m = _WHY_EDGE.match(why)
        if not m:
            continue
        by_type[m.group(1)] = by_type.get(m.group(1), 0) + 1
        conf = c.get("confidence")
        if isinstance(conf, (int, float)) and conf < 1.0:
            ambiguous += 1
    return {"by_type": by_type, "ambiguous": ambiguous}


def confidence_report(pack: dict[str, Any] | None) -> dict[str, Any]:
    """The machine-readable form. `render()` turns this into the prose block."""
    pack = pack or {}
    chunks = [c for c in (pack.get("chunks") or []) if isinstance(c, dict)]
    seeds = [c for c in chunks if c.get("why") == "seed"]
    neighbours = [c for c in chunks if c.get("why") != "seed"]
    files = sorted({str(c.get("path")) for c in chunks if c.get("path")})

    budget = pack.get("budget_chars") or 0
    used = pack.get("used_chars") or 0
    return {
        "blocks": len(chunks),
        "files": len(files),
        "seeds": len(seeds),
        "neighbours": len(neighbours),
        "edges": _edge_stats(neighbours),
        "truncated": bool(pack.get("truncated")),
        "used_chars": used,
        "budget_chars": budget,
        "budget_used_pct": round(100 * used / budget) if budget else None,
        "cited_files": files,
        "limits": list(STANDING_LIMITS),
    }


def render(pack: dict[str, Any] | None, *, heading: str = "Confidence and limitations") -> str:
    """The markdown block, ready to append to an answer.

    Always the same shape, whatever the pack contains -- a section that
    appears only when something is wrong teaches readers to skip it, and an
    answer with nothing to caveat is exactly the case where a reader most
    wants to see that the check ran.
    """
    r = confidence_report(pack)
    lines = ["", "---", f"**{heading}**", ""]

    if not r["blocks"]:
        lines.append(
            "- This answer rests on **no cited source**: retrieval returned nothing. "
            "Treat any specific claim in it as unsupported."
        )
        for limit in r["limits"]:
            lines.append(f"- {limit}")
        return "\n".join(lines)

    lines.append(
        f"- Based on **{r['blocks']} cited block(s) across {r['files']} file(s)** "
        f"-- {r['seeds']} matched the question directly, "
        f"{r['neighbours']} were pulled in by graph edges."
    )

    by_type = r["edges"]["by_type"]
    if by_type:
        kinds = ", ".join(f"{n}x {t}" for t, n in sorted(by_type.items(), key=lambda kv: -kv[1]))
        lines.append(f"- Graph edges used: {kinds}.")
    ambiguous = r["edges"]["ambiguous"]
    if ambiguous:
        lines.append(
            f"- **{ambiguous} of those edge(s) {'is' if ambiguous == 1 else 'are'} ambiguous** "
            "(the called name matched more than one definition, so the block shown may not "
            "be the one that actually runs)."
        )

    if r["truncated"]:
        lines.append(
            f"- **The context was truncated** at {r['used_chars']} of {r['budget_chars']} "
            "characters. Relevant code may have been cut before the model saw it, so an "
            "absence in this answer is not evidence of absence in the repository. "
            "Re-run with a larger `--budget` to check."
        )
    elif r["budget_used_pct"] is not None:
        lines.append(
            f"- The context fit within budget ({r['budget_used_pct']}% of "
            f"{r['budget_chars']} characters), so nothing was cut for space."
        )

    for limit in r["limits"]:
        lines.append(f"- {limit}")

    lines.append("")
    lines.append(
        "Every claim above should carry a `[path:line-line]` citation. "
        "A claim without one was not grounded in the retrieved source -- "
        "open the cited lines before acting on it."
    )
    return "\n".join(lines)
