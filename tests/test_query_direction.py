"""Expansion must follow the direction the question asks for.

"What calls `prepare_request`" wants that symbol's **callers**. On the pinned
`requests` checkout the pack came back holding `cookiejar_from_dict` and
`merge_cookies` -- labelled `CALLS out of prepare_request`, which is to say its
*callees* -- and never `Session.request`, the caller. The edge
`Session.request -> Session.prepare_request` is in the graph at confidence 1.0.
The answer was one hop away in the opposite direction.

`expand()` has taken an `edge_dirs` filter all along; nothing ever derived one
from the question, so every query got `DEFAULT_EDGE_DIRS` and `CALLS` was
followed both ways at once. With a budget to spend and `per_hop` to fill, the
wrong direction gets there first about half the time.

So this is a routing problem, not a graph problem: classify what the question
asks for, then narrow the traversal to match.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import build_mini_index  # type: ignore[import-not-found]

from repo2graph.query import Index, classify_query

# `caller_one` and `caller_two` call `target`; `target` calls `helper_one` and
# `helper_two`. A direction mistake is therefore visible in the pack.
SOURCE = '''"""Dispatch chain."""


def helper_one(value):
    """Normalise one value for dispatch."""
    return value


def helper_two(value):
    """Annotate one value for dispatch."""
    return value


def target(value):
    """Dispatch a value through the normalising helpers."""
    return helper_two(helper_one(value))


def caller_one(value):
    """Begin a dispatch from the public entry point."""
    return target(value)


def caller_two(value):
    """Begin a dispatch from the retry path."""
    return target(value)
'''


@pytest.fixture
def chain_index(tmp_path: Path) -> Index:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "chain.py").write_text(SOURCE, encoding="utf8")
    return Index(build_mini_index(repo, tmp_path / "idx"))


def _quals(pack) -> set[str]:
    return {str(c.get("qualname") or "") for c in pack["chunks"]}


@pytest.mark.parametrize(
    "query,expected",
    [
        ("what calls prepare_request", "callers"),
        ("who calls target", "callers"),
        ("what uses the dispatch helper", "callers"),
        ("where is target used", "callers"),
        ("what are the callers of target", "callers"),
        ("what imports the chain module", "callers"),
        ("what does target call", "callees"),
        ("what does target depend on", "callees"),
        ("what are the callees of target", "callees"),
        ("how does a value flow from caller_one to helper_two", "trace"),
        ("trace a dispatch from entry to helper", "trace"),
        ("how is a value normalised before dispatch", "concept"),
        ("where is authentication enforced", "concept"),
        ("", "concept"),
    ],
)
def test_classify_query(query: str, expected: str) -> None:
    assert classify_query(query) == expected


def test_a_caller_question_returns_the_caller(chain_index: Index) -> None:
    """The canonical failure: callers asked for, callees returned."""
    pack = chain_index.pack_context("what calls target", budget_tokens=4000)
    quals = _quals(pack)
    assert "caller_one" in quals or "caller_two" in quals, (
        f"no caller of `target` in the pack; got {sorted(quals)}"
    )


def test_a_caller_question_does_not_fill_up_with_callees(chain_index: Index) -> None:
    """Asking for callers must not spend the budget on the other direction."""
    pack = chain_index.pack_context("what calls target", budget_tokens=4000)
    wrong_way = [
        str(c.get("qualname"))
        for c in pack["chunks"]
        if str(c.get("why") or "").startswith("CALLS out of")
    ]
    assert not wrong_way, f"callees admitted for a callers question: {wrong_way}"


def test_a_callee_question_returns_the_callees(chain_index: Index) -> None:
    """The mirror case must keep working."""
    pack = chain_index.pack_context("what does target call", budget_tokens=4000)
    quals = _quals(pack)
    assert "helper_one" in quals or "helper_two" in quals, (
        f"no callee of `target` in the pack; got {sorted(quals)}"
    )


def test_a_concept_question_still_follows_both_directions(chain_index: Index) -> None:
    """Routing must not narrow a question that named no direction."""
    pack = chain_index.pack_context("how is a value normalised before dispatch", budget_tokens=4000)
    assert _quals(pack), "a concept question returned nothing"
    dirs = {
        str(c.get("why") or "").split()[1]
        for c in pack["chunks"]
        if str(c.get("why") or "").startswith("CALLS ")
    }
    assert dirs <= {"out", "in"}
