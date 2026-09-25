"""The standard metadata every graph edge carries, and what each field means.

An edge is a claim about the code. Before this module, only `CALLS` carried
enough to audit one: `CONTAINS`, `DEFINES`, `IMPORTS` and `INHERITS` were bare
`(src, dst, type)` triples, so "why do you think this file imports that one"
had no answer in the artifact — the reader had to re-derive it by reading the
source and trusting that repo2graph had read it the same way.

Five fields, on every edge, so an answer can be checked instead of believed:

| Field | Meaning |
|---|---|
| `type` | The relationship: CONTAINS, DEFINES, IMPORTS, CALLS, CALLS_EXTERNAL, INHERITS, CO_CHANGE |
| `evidence` | `{"path", "line"}` — where the relationship is *written*, or None |
| `method` | How it was extracted, so a whole class of edge can be distrusted at once |
| `confidence` | P(`dst` is the correct target), 0..1 |
| `candidate_count` / `ambiguous` | How many targets the name could have meant |

## What `confidence` means, exactly

**The probability that `dst` is the right target, given that the relationship
at `evidence` exists.** It is not a probability that the relationship exists
at all — that is what `evidence` is for, and a syntactic fact read straight
out of a parse tree is not uncertain.

Keeping those two separate is what makes the number usable:

- A `CALLS` resolved to one candidate is `1.0`: the call is there, and there
  was only one thing it could mean.
- A `CALLS` whose name matched 4 candidates is `0.25` each. The call is still
  certainly there; repo2graph just cannot tell which one it reaches.
- A `CALLS_EXTERNAL` is `1.0`. `dst` is a synthetic `external:<name>` node
  meaning "not found in this repository", and that is exactly what was
  determined — the uncertainty is in `call_kind`, not in the target.
- `CONTAINS` and `DEFINES` are `1.0`. A file is in a directory, and a symbol
  is where the parser found it.

What `confidence` never encodes is dynamic dispatch. A `1.0` CALLS edge means
"this name resolves here", not "this line reaches that function at runtime".
`call_kind` carries that (`static` / `possible` / `dynamic` / `decorator`),
and docs/limitations.md carries the rest.

## Reading `method`

`method` names the machinery, so a consumer that distrusts one extraction
path can filter on it rather than on edge type:

- `tree-sitter/<lang>` — read from a parse tree. The `<lang>` matters: the
  same edge type is more reliable from the Python grammar than from the C
  fallback.
- `name-resolver` — the parse tree supplied a *name*, and repo2graph matched
  it to a definition. This is where ambiguity comes from, and it is the only
  method whose confidence is routinely below 1.
- `filesystem` — directory structure. No parsing involved.
- `git-log` — commit history. Correlational, never causal: a CO_CHANGE edge
  says two files changed together, not that either depends on the other.
"""

from __future__ import annotations

from typing import Any

# Bumped when the shape of an edge record changes. `manifest.json` carries it
# so a consumer reading an index built by an older version can tell that
# `evidence` will be absent rather than null.
EDGE_SCHEMA_VERSION = "2"

METHOD_TREE_SITTER = "tree-sitter"
METHOD_NAME_RESOLVER = "name-resolver"
METHOD_FILESYSTEM = "filesystem"
METHOD_GIT_LOG = "git-log"

METHODS: dict[str, str] = {
    METHOD_TREE_SITTER: "read directly from a tree-sitter parse tree",
    METHOD_NAME_RESOLVER: "a parsed name matched against the repository's definitions",
    METHOD_FILESYSTEM: "directory structure; no parsing involved",
    METHOD_GIT_LOG: "commit history; correlational, never causal",
}

# The fields every edge carries, in the order they are written.
STANDARD_FIELDS: tuple[str, ...] = ("src", "dst", "type", "method", "confidence", "evidence")


def tree_sitter_method(lang: str | None) -> str:
    """`tree-sitter/python`, or plain `tree-sitter` when the language is unknown.

    The language is part of the method on purpose: the same edge type carries
    different weight from different grammars, and a consumer that has learned
    to distrust, say, the C fallback needs to be able to say so.
    """
    return f"{METHOD_TREE_SITTER}/{lang}" if lang else METHOD_TREE_SITTER


def evidence(path: str | None, line: int | None) -> dict[str, Any] | None:
    """An `{"path", "line"}` record, or None when there is no line to point at.

    None is a real answer, not a gap to paper over: a CONTAINS edge between a
    directory and a file is not written down anywhere, so claiming a line for
    it would be a fabricated citation — the exact failure this module exists
    to prevent. Line numbers are 1-based, matching every editor and every
    `path:line` convention.
    """
    if not path or not line or line < 1:
        return None
    return {"path": path, "line": int(line)}


def file_of(node_id: str) -> str | None:
    """The source path a node id refers to, or None for ids without one.

    `sym:pkg/mod.py::Class.method` -> `pkg/mod.py`
    `file:pkg/mod.py`              -> `pkg/mod.py`
    `dir:pkg`, `repo:x`, `module:os`, `external:len` -> None
    """
    if node_id.startswith("sym:"):
        return node_id[4:].split("::", 1)[0] or None
    if node_id.startswith("file:"):
        return node_id[5:] or None
    return None


def normalize(edge: dict[str, Any]) -> dict[str, Any]:
    """Fill in the standard fields an edge is missing, in a stable key order.

    Called from one place (`Graph.add_edge`) so that an edge type added later
    cannot ship without them — the failure mode this replaces was four of the
    six edge types quietly carrying nothing but a triple. A caller that
    supplied a value always wins; this only supplies defaults.

    Key order is fixed rather than insertion-ordered because `edges.jsonl` is
    a committable artifact whose diff humans read, and a field that moves
    between lines makes every edge look changed.
    """
    edge.setdefault("method", METHOD_TREE_SITTER)
    edge.setdefault("confidence", 1.0)
    edge.setdefault("evidence", None)

    ordered: dict[str, Any] = {}
    for key in STANDARD_FIELDS:
        if key in edge:
            ordered[key] = edge[key]
    for key in sorted(k for k in edge if k not in ordered):
        ordered[key] = edge[key]
    return ordered


def cite(edge: dict[str, Any]) -> str | None:
    """`path/file.py:123` for an edge's evidence, or None when it has none.

    The one rendering of a citation, so the CLI, the MCP tools and the
    explain output cannot each invent their own and drift. `path:line` is the
    form terminals and editors already make clickable.
    """
    ev = edge.get("evidence")
    if not isinstance(ev, dict):
        return None
    path, line = ev.get("path"), ev.get("line")
    if not path or not line:
        return None
    return f"{path}:{line}"


def describe(edge: dict[str, Any]) -> str:
    """One human line explaining how much to trust this edge and why.

    Used by `explain`, by the MCP tools and by the `rag` limitations block,
    so the same edge is characterised identically wherever it surfaces.
    """
    conf = edge.get("confidence")
    method = str(edge.get("method") or "unknown")
    bits = []
    if isinstance(conf, (int, float)):
        if conf >= 1.0:
            bits.append("confidence 1.0 (unambiguous)")
        else:
            n = edge.get("candidate_count")
            bits.append(
                f"confidence {conf} (1 of {n} candidate targets)"
                if n
                else f"confidence {conf} (ambiguous)"
            )
    bits.append(f"via {method}")
    where = cite(edge)
    if where:
        bits.append(f"evidence {where}")
    kind = edge.get("call_kind")
    if kind and kind != "static":
        bits.append(f"call is {kind}, so the runtime target may differ")
    return "; ".join(bits)
