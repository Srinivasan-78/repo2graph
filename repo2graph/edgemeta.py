"""Edge metadata schema, confidence scoring constants, and evidence formatting."""

from __future__ import annotations

from typing import Any

EDGE_SCHEMA_VERSION = "2"
OVERVIEW_MIN_CALL_CONFIDENCE = 0.5
SCOPED_CALL_KINDS = frozenset(
    {"self_recursive", "same_class", "base_class", "same_file", "import_alias", "imported_symbol"}
)


def counts_as_call(e: dict[str, Any]) -> bool:
    """Determine whether a CALLS edge represents a confident call target.

    Args:
        e: Edge dictionary containing confidence and resolution attributes.

    Returns:
        True if the edge represents a scoped or sufficiently confident call.
    """
    if e.get("untyped_receiver"):
        return False
    if e.get("resolution_kind") in SCOPED_CALL_KINDS:
        return True
    conf = e.get("confidence", 1.0)
    return not isinstance(conf, (int, float)) or conf >= OVERVIEW_MIN_CALL_CONFIDENCE


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

STANDARD_FIELDS: tuple[str, ...] = ("src", "dst", "type", "method", "confidence", "evidence")


def tree_sitter_method(lang: str | None) -> str:
    """Format tree-sitter extraction method string.

    Args:
        lang: Programming language identifier, or None.

    Returns:
        Method string formatted as 'tree-sitter/<lang>' or 'tree-sitter'.
    """
    return f"{METHOD_TREE_SITTER}/{lang}" if lang else METHOD_TREE_SITTER


def evidence(path: str | None, line: int | None) -> dict[str, Any] | None:
    """Construct an evidence record for a relationship.

    Args:
        path: File path where the relationship is declared.
        line: 1-based line number.

    Returns:
        Dictionary with 'path' and 'line', or None if invalid or absent.
    """
    if not path or not line or line < 1:
        return None
    return {"path": path, "line": int(line)}


def file_of(node_id: str) -> str | None:
    """Extract relative source file path from a graph node identifier.

    Args:
        node_id: Graph node identifier (e.g. 'sym:pkg/mod.py::fn', 'file:pkg/mod.py').

    Returns:
        Relative file path string, or None if the identifier has no file component.
    """
    if node_id.startswith("sym:"):
        return node_id[4:].split("::", 1)[0] or None
    if node_id.startswith("file:"):
        return node_id[5:] or None
    return None


def normalize(edge: dict[str, Any]) -> dict[str, Any]:
    """Populate default edge metadata fields in standard key order.

    Args:
        edge: Edge dictionary to normalize.

    Returns:
        Edge dictionary with standard fields and sorted attributes.
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
    """Format an edge's evidence as 'path:line'.

    Args:
        edge: Edge dictionary.

    Returns:
        'path:line' string, or None if evidence is absent.
    """
    ev = edge.get("evidence")
    if not isinstance(ev, dict):
        return None
    path, line = ev.get("path"), ev.get("line")
    if not path or not line:
        return None
    return f"{path}:{line}"


def describe(edge: dict[str, Any]) -> str:
    """Format a summary string of an edge's confidence, method, and evidence.

    Args:
        edge: Edge dictionary.

    Returns:
        Single-line human-readable summary.
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
