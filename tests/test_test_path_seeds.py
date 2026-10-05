"""A test file must not take a seed slot from the code it exercises.

A test that exercises middleware mentions middleware constantly, so it matches a
question about middleware at least as well as the implementation does -- and
often better, because a test names the behaviour in the words a person would use
to ask about it. It is still the wrong answer: the question is how the thing
works, not how it is checked.

Measured on the pinned `hono` checkout, whose indexed `src/` is 49% test chunks
(1,250 of 2,555, because the suite lives beside the source as `*.test.ts`):
at a 4,000-token budget, test paths took **45% of seed slots and 37% of the
token budget** across the held-out lexical questions, and 8 of 10 packs held at
least one test seed. The other three benchmark repositories index almost no
tests at all, so this is the one corpus where the defect is visible -- not a
sign that it is rare.

Two things must survive the demotion:

* "What tests cover X" is a documented use case and one of the five questions
  the README leads with. A question that asks about tests must still get them,
  which is what the `tests` query shape is for.
* Tests must stay reachable as graph neighbours either way, so the IMPORTS edge
  from a test module back to the code under test still answers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import build_mini_index  # type: ignore[import-not-found]

from repo2graph.query import Index, classify_query

IMPL = '''"""Middleware chaining."""


def compose_middleware(handlers):
    """Chain every middleware handler into one callable in order."""
    def composed(request):
        for handler in handlers:
            request = handler(request)
        return request
    return composed
'''

# Deliberately wordier about "middleware chaining" than the implementation, the
# way a real test is.
TEST = '''"""Tests for middleware chaining."""

from impl import compose_middleware


def test_middleware_chaining_runs_handlers_in_order():
    """Middleware chaining must run each middleware handler in order."""
    calls = []
    composed = compose_middleware([lambda r: calls.append("a") or r])
    composed("request")
    assert calls == ["a"]


def test_middleware_chaining_with_no_handlers():
    """Middleware chaining with no middleware handler returns the request."""
    assert compose_middleware([])("request") == "request"
'''

QUERY = "how are middleware handlers chained together"


# Reproducing the defect needs both halves of what hono has. One test file
# cannot crowd anything out, and nor can twelve if there is no other source for
# a demotion to promote: hono's 1,250 test chunks displace 1,305 *source* ones.
# So the fixture carries secondary source modules that rank just under the tests
# and should take those slots back.
N_TEST_FILES = 12
N_SOURCE_FILES = 6

# As on-topic as the tests are, which is the point: in hono the suite and the
# source discuss the same concepts in the same words, so the scores are close
# and a mild penalty decides them. Secondary source written as *less* relevant
# than the tests would make the fixture unfalsifiable -- there would be nothing
# for a demotion to promote.
SECONDARY_SOURCE = '''"""Middleware {n} for the handler chain."""


def middleware_handler_{n}(request):
    """Chain middleware handler {n} together with the other middleware handlers.

    Each middleware handler in the chain receives the request from the
    middleware handler before it, so chaining middleware handlers together is
    how a request is handled.
    """
    return request
'''


@pytest.fixture
def mw_index(tmp_path: Path) -> Index:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "impl.py").write_text(IMPL, encoding="utf8")
    (repo / "test_impl.py").write_text(TEST, encoding="utf8")
    for n in range(N_SOURCE_FILES):
        (repo / f"middleware_{n}.py").write_text(
            SECONDARY_SOURCE.format(n=n), encoding="utf8"
        )
    for n in range(N_TEST_FILES):
        (repo / f"test_middleware_{n}.py").write_text(
            TEST.replace("test_middleware_chaining", f"test_middleware_chaining_{n}"),
            encoding="utf8",
        )
    return Index(build_mini_index(repo, tmp_path / "idx"))


def _seeds(pack) -> list[str]:
    return [str(c.get("path")) for c in pack["chunks"] if c.get("why") == "seed"]


@pytest.mark.parametrize(
    "query,expected",
    [
        ("what tests cover compose_middleware", "tests"),
        ("which test exercises middleware chaining", "tests"),
        ("where are the specs for the router", "tests"),
        ("how are middleware handlers chained together", "concept"),
        ("what calls compose_middleware", "callers"),
    ],
)
def test_classify_recognises_test_questions(query: str, expected: str) -> None:
    assert classify_query(query) == expected


def test_implementation_outranks_its_test(mw_index: Index) -> None:
    ranked = [str(mw_index.chunks[i].get("path")) for _s, i in mw_index.score(QUERY)]
    assert "impl.py" in ranked, f"the implementation did not score; got {ranked}"


def test_demotion_lowers_test_scores_and_leaves_source_alone(mw_index: Index) -> None:
    """The mechanism, asserted directly rather than through an emergent ranking.

    Whether source then *wins* a slot depends on how close the two were, which
    is a property of the corpus and belongs to the benchmark
    (`benchmarks/real/`), not to a fixture. A unit test tuned to sit in the
    narrow band where the penalty decides the order would be testing its own
    calibration: make the source slightly more on-topic and it passes without
    any fix at all, which is exactly what happened while writing this.
    """
    ranked = mw_index.score(QUERY)
    before = {i: s for s, i in ranked}
    after = {i: s for s, i in mw_index._demote_test_seeds(ranked)}

    assert set(before) == set(after), "demotion must re-rank, never drop a candidate"
    moved, held = [], []
    for i, s in before.items():
        path = str(mw_index.chunks[i].get("path") or "")
        (moved if path.startswith("test_") else held).append((path, s, after[i]))

    assert moved, "the fixture produced no test candidates"
    assert held, "the fixture produced no source candidates"
    for path, old, new in moved:
        assert new < old, f"{path} was not demoted ({old} -> {new})"
    for path, old, new in held:
        assert new == old, f"{path} is not a test and must be untouched ({old} -> {new})"


def test_demotion_is_stable_for_equal_scores(mw_index: Index) -> None:
    """Two candidates the penalty does not separate keep BM25's order."""
    ranked = mw_index.score(QUERY)
    source_order = [i for _s, i in ranked if not str(mw_index.chunks[i].get("path")).startswith("test_")]
    after = mw_index._demote_test_seeds(ranked)
    after_source = [i for _s, i in after if not str(mw_index.chunks[i].get("path")).startswith("test_")]
    assert source_order == after_source, "relative order of non-test chunks changed"


def test_a_test_shaped_question_is_not_demoted(mw_index: Index) -> None:
    """The penalty must be off entirely when the question is about tests."""
    assert classify_query("what tests cover compose_middleware") == "tests"
    pack = mw_index.pack_context("what tests cover compose_middleware", budget_tokens=4000)
    seeds = _seeds(pack)
    assert any(p.startswith("test_") for p in seeds), (
        f"a test-shaped question got no test seeds: {seeds}"
    )


def test_a_test_question_still_gets_the_tests(mw_index: Index) -> None:
    """Demotion must not break the documented "what tests cover X" use case."""
    pack = mw_index.pack_context("what tests cover compose_middleware", budget_tokens=2000)
    paths = {str(c.get("path")) for c in pack["chunks"]}
    assert "test_impl.py" in paths, f"a question about tests returned none: {sorted(paths)}"


def test_tests_remain_reachable_as_neighbours(mw_index: Index) -> None:
    """The IMPORTS edge from a test back to the code under test must still work."""
    pack = mw_index.pack_context(QUERY, budget_tokens=4000)
    paths = {str(c.get("path")) for c in pack["chunks"]}
    assert "impl.py" in paths
    assert "test_impl.py" in paths, (
        f"the test became unreachable rather than merely demoted: {sorted(paths)}"
    )
