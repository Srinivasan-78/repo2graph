"""Guardrail constants, clamping, and token/query bounds for MCP tools."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Formats needed by the served index.
AUTO_BUILD_FORMATS = {"jsonl", "overview"}

# Directory name for default index location.
INDEX_DIRNAME = ".r2g"

# Budget and token bounds.
MCP_BUDGET_TOKENS = 6000
MCP_MAX_BUDGET_TOKENS = 12000

# Neighbour limits.
MCP_NEIGHBOUR_LIMIT = 20
MCP_MAX_NEIGHBOURS = 50
MCP_MAX_SEARCH_NEIGHBOURS = 50
# Rows reachable by paging `repo_neighbours` (#389): pages walk every edge of a
# hop rather than sampling a few per node, so the walk itself needs a bound.
MCP_MAX_PAGED_NEIGHBOURS = 1000

# Graph hop and candidate bounds.
MCP_MAX_HOPS = 4
MCP_MAX_K = 50

# Input string length ceilings.
MCP_MAX_QUERY_CHARS = 4000
MCP_MAX_NODE_ID_CHARS = 2000
MCP_MAX_TASK_ID_CHARS = 200

# Symbol lookup limits.
MCP_FIND_LIMIT = 20
MCP_MAX_FIND_LIMIT = 50

# Read window bounds.
MCP_MAX_READ_CHARS = 20000
MCP_READ_CONTEXT = 0
MCP_MAX_READ_CONTEXT = 500

# Path traversal limits.
MCP_PATH_HOPS = 6
MCP_MAX_PATH_HOPS = 8
MCP_PATH_PATHS = 3
MCP_MAX_PATHS = 10
MCP_MAX_PATH_VISITED = 4000

DEFAULT_PATH_EDGE_TYPES = ("CALLS", "DEFINES", "IMPORTS")
PATH_EDGE_TYPES = frozenset({"CALLS", "DEFINES", "IMPORTS", "INHERITS", "CONTAINS", "CO_CHANGE"})

# Blast radius bounds.
MCP_IMPACT_HOPS = 3
MCP_MAX_IMPACT_HOPS = 6
MCP_IMPACT_LIMIT = 40
MCP_MAX_IMPACT_VISITED = 4000

EMPTY_RESULT = (
    "no content fit in a {budget}-token budget: nothing matched, "
    "or the budget was too small to render a single line. Retry "
    "with a broader query or budget_tokens up to {ceiling}."
)

BUDGET_DEFAULTED = (
    "_note: budget_tokens={given} is not a positive token count; used the default "
    "{default} (max {ceiling})._\n\n"
)

IMPACT_FORMATS = ("markdown", "json", "sarif", "pr-comment")

_ABS_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|(?<![\w.~>-])/)[^'\"\n]*?(?=['\"\n]|: |$)")


def _int(value: Any, fallback: int) -> int:
    """Safely coerce argument to int, falling back to default on error/overflow."""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return fallback


def _defaulted_notes(**args: tuple[Any, int]) -> str:
    """Format diagnostic note lines for non-integer arguments that took defaults."""
    notes = ""
    for name, (value, default) in args.items():
        if value is None or isinstance(value, bool):
            continue
        try:
            int(value)
        except (TypeError, ValueError, OverflowError):
            notes += (
                f"_note: {name}={repr(value)[:40]} is not an integer; "
                f"used the default {default}._\n\n"
            )
    return notes


def _clamp(value: Any, fallback: int, low: int, high: int) -> int:
    """Coerce value to int and clamp between [low, high]."""
    return max(low, min(_int(value, fallback), high))


def _str(value: Any, max_chars: int) -> str:
    """Coerce string argument and clamp length to max_chars."""
    text = "" if value is None else str(value)
    return text[:max_chars]


def _scrub_paths(text: str, root: Path) -> str:
    """Remove absolute filesystem paths from messages."""
    for known in {str(root), str(root.resolve()), root.as_posix(), root.resolve().as_posix()}:
        if known and known not in (".", "/"):
            text = text.replace(known, "<repo>")
    return _ABS_PATH_RE.sub("<path>", text).strip()
