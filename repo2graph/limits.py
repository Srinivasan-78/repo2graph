"""Confidence and limitations reporting for LLM answer packs.

Provides structured provenance metrics and graph limitation disclosures
calculated directly from retrieved context packs.
"""

from __future__ import annotations

import re
from typing import Any

# Matches graph traversal reason on packed chunk: "<EDGE_TYPE> <dir> of <name>".
_WHY_EDGE = re.compile(r"^([A-Z_]+)\s+(in|out)\s+of\s+(.+)$")

STANDING_LIMITS = (
    "calls made through dynamic dispatch, reflection, or a DI container are not "
    "edges in this graph, so a caller list can be incomplete",
    "the graph models the code as written, not as executed: an edge is not proof the line runs",
    "only files that were indexed are visible -- anything excluded by a filter, "
    "a .gitignore, or a size limit is absent rather than reported as missing",
)


def _edge_stats(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """Count edge types and ambiguous edges for non-seed chunks.

    Args:
        chunks: List of chunk metadata dictionaries.

    Returns:
        Dictionary with 'by_type' count mapping and 'ambiguous' edge count.
    """
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
    """Generate structured confidence metrics from a context pack.

    Args:
        pack: Packed context dictionary containing chunks and token/character usage.

    Returns:
        Dictionary detailing block counts, seed/neighbor breakdown, and budget usage.
    """
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
    """Format confidence metrics and standing limitations as a markdown section.

    Args:
        pack: Packed context dictionary.
        heading: Title for the limitations markdown section.

    Returns:
        Rendered markdown string ready to append to model responses.
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
