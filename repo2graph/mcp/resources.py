"""MCP resources and prompts, beside the tools rather than inside them (#388).

Resources are the index's own stable documents, for a client to attach once
instead of paying for them in every tool result: the repo map that
`repo_map` returns, `stats.json` and `manifest.json`. They are read-only
artifact reads, so they sit beside `dispatch` rather than in it. None of them
carries source text: the map is names, paths and counts, already redacted the
way every content tool's output is.

Prompts are starting points that name the tools to use, so a client that
lists prompts can offer the common questions without the user knowing the
tool names.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..export import path as artifact_path

URI_PREFIX = "repo2graph://"

# uri name -> (artifact, MIME type, description). `map` is rendered, not read.
RESOURCES: dict[str, tuple[str | None, str, str]] = {
    "map": (
        None,
        "text/markdown",
        "The repository map: entry points, most-depended-on files and symbols. "
        "The same text repo_map returns, stable between builds.",
    ),
    "stats": (
        "stats.json",
        "application/json",
        "Graph shape and quality counters for the current index.",
    ),
    "manifest": (
        "manifest.json",
        "application/json",
        "Build provenance: schema version, build id, what each artifact holds.",
    ),
}


def list_resources(index_dir: str | Path) -> list[dict[str, str]]:
    """The resources this index can serve, as {uri, name, description, mime_type}."""
    out = []
    for name, (artifact, mime, desc) in RESOURCES.items():
        if artifact is not None and not artifact_path(index_dir, artifact).is_file():
            continue
        out.append({"uri": URI_PREFIX + name, "name": name, "description": desc, "mime_type": mime})
    return out


def read_resource(index: Any, uri: str) -> tuple[str, str]:
    """(text, MIME type) for one resource URI.

    Raises:
        ValueError: A URI this server does not serve.
    """
    name = uri[len(URI_PREFIX) :] if uri.startswith(URI_PREFIX) else ""
    if name not in RESOURCES:
        known = ", ".join(URI_PREFIX + n for n in RESOURCES)
        raise ValueError(f"unknown resource {uri!r}; this server serves {known}")
    artifact, mime, _desc = RESOURCES[name]
    if artifact is None:
        from .tools import dispatch

        return str(dispatch(index, "repo_map", {})), mime
    path = artifact_path(index.dir, artifact)
    if not path.is_file():
        raise ValueError(f"{uri}: this index has no {artifact}")
    return path.read_text(encoding="utf8", errors="replace"), mime


# name -> (description, [(argument, description, required)], message template)
PROMPTS: dict[str, tuple[str, list[tuple[str, str, bool]], str]] = {
    "explain-symbol": (
        "Explain what a function or class does and who uses it.",
        [("symbol", "the function, method or class name", True)],
        "Explain `{symbol}` in this repository. Find it with repo_find_symbol, read it "
        "with repo_read, list its callers, callees and tests with repo_neighbours, and "
        "answer only from what those tools return, citing [cite: path:start-end].",
    ),
    "trace-flow": (
        "Trace how execution gets from one symbol to another.",
        [("source", "where the flow starts", True), ("target", "where it should end", True)],
        "Trace how `{source}` reaches `{target}`. Resolve both with repo_find_symbol, "
        "then use repo_path_between, and read each hop with repo_read. Say so plainly "
        "if no path exists in the graph.",
    ),
    "what-breaks": (
        "List what depends on a symbol before changing it.",
        [("symbol", "the symbol about to change", True)],
        "Before `{symbol}` changes: resolve it with repo_find_symbol, then list what "
        "depends on it with repo_blast_radius and the tests that reach it with "
        "repo_neighbours (TESTS in). Group the result by file.",
    ),
    "orient": (
        "Get oriented in an unfamiliar repository.",
        [],
        "Give me a tour of this repository: start from the repo2graph://map resource "
        "or repo_map, then use repo_search on the two or three most central areas and "
        "summarise what each does, citing [cite: path:start-end].",
    ),
}


def list_prompts() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": desc,
            "arguments": [{"name": a, "description": d, "required": r} for a, d, r in args],
        }
        for name, (desc, args, _template) in PROMPTS.items()
    ]


def get_prompt(name: str, arguments: dict[str, Any] | None) -> tuple[str, str]:
    """(description, user message) for one prompt.

    Raises:
        ValueError: An unknown prompt or a missing required argument.
    """
    if name not in PROMPTS:
        raise ValueError(f"unknown prompt {name!r}; this server has {', '.join(PROMPTS)}")
    desc, args, template = PROMPTS[name]
    given = {k: str(v)[:500] for k, v in (arguments or {}).items()}
    missing = [a for a, _d, required in args if required and not given.get(a, "").strip()]
    if missing:
        raise ValueError(f"prompt {name!r} needs {', '.join(missing)}")
    return desc, template.format(**{a: given.get(a, "") for a, _d, _r in args})
