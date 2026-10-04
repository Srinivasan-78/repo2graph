"""Seed re-ranking: container penalty and name-term boost (`_answerability`).

The retrieval benchmark in `benchmarks/real/` measured the problem these solve.
Every evidence definition in that suite was present in some chunk, and an oracle
packer fit 100% of them inside an 8,000-token budget -- but 54% of the budget
went to *container* chunks, which the chunker builds by cutting their members
out. Flask's `class Flask` chunk spans lines 81-1536, costs ~1,150 tokens and
contains no method body at all, yet it outranked the methods because its long
docstring matched many question words.

These tests pin the two corrections and, as importantly, their limits: the
penalty must not fire on a cheap container, and the boost must not fire on a
container at all.
"""

import json
from pathlib import Path

import pytest

from repo2graph.cli import main
from repo2graph.query import (
    CONTAINER_PENALTY_MIN_TOKENS,
    CONTAINER_SEED_PENALTY,
    NAME_TERM_BOOST,
    Index,
    count_tokens,
)

# Two of this question's content words ("order", "refund") are also subtokens of
# `OrderService.refund_order`, which is what makes the boost measurable at its
# cap. "refunded" would share only "order" -- the tokenizer does not stem.
QUERY = "how is an order refund handled"


@pytest.fixture
def index(tmp_path):
    """A repo with one expensive container and the method that answers QUERY."""
    src = tmp_path / "src"
    (src / "app").mkdir(parents=True)
    # A class whose docstring matches the question many times over, and whose
    # members are emitted as their own chunks -- the shape that wins seeds.
    filler = "\n".join(f"    #: ledger note {i} about an order refund" for i in range(120))
    (src / "app" / "service.py").write_text(
        '"""Order service."""\n\n\n'
        "class OrderService:\n"
        '    """Owns order refund rules.\n\n'
        "    An order refund is an order refund is an order refund.\n"
        f'    """\n\n{filler}\n\n'
        "    def refund_order(self, order_id):\n"
        '        """Refund one order."""\n'
        "        return {'refunded': order_id}\n\n"
        "    def place_order(self, sku):\n"
        "        return {'placed': sku}\n",
        encoding="utf8",
    )
    out = tmp_path / "idx"
    assert main(["build", str(src), "-o", str(out)]) == 0
    return Index(out)


def _by_qualname(index, needle):
    for c in index.chunks:
        if needle in (c.get("qualname") or ""):
            return c
    raise AssertionError(f"no chunk with qualname containing {needle!r}")


def test_an_expensive_container_is_penalised(index):
    """The case the benchmark measured: a big class header must not win a seed."""
    container = _by_qualname(index, "OrderService")
    assert index.is_container_chunk(container)
    assert count_tokens(container["text"]) > CONTAINER_PENALTY_MIN_TOKENS, (
        "fixture must build a container above the cost floor, or this proves nothing"
    )
    terms = index.query_content_terms(QUERY)
    assert index._answerability(terms, container) == pytest.approx(CONTAINER_SEED_PENALTY)


def test_a_cheap_container_is_not_penalised(tmp_path):
    """The penalty is about crowding bodies out of the budget, which a small
    container does not do.

    Penalising by kind alone dropped the demo fixture's 91-token `app/store.py`
    residual -- the chunk that answered "trace a request to persistence" -- for
    no budget saved. `test_demo.py` caught it; the benchmark did not.
    """
    src = tmp_path / "small"
    src.mkdir()
    (src / "tiny.py").write_text("X = 1\n", encoding="utf8")
    out = tmp_path / "small-idx"
    assert main(["build", str(src), "-o", str(out)]) == 0
    small = Index(out)
    cheap = [c for c in small.chunks if small.is_container_chunk(c)]
    assert cheap, "fixture must produce a container chunk"
    for c in cheap:
        assert count_tokens(c["text"]) <= CONTAINER_PENALTY_MIN_TOKENS
        assert small._answerability(frozenset(), c) == pytest.approx(1.0)


def test_a_matching_name_is_boosted(index):
    """Two shared terms is the cap, so this is the full `NAME_TERM_BOOST`."""
    method = _by_qualname(index, "refund_order")
    assert not index.is_container_chunk(method)
    terms = index.query_content_terms(QUERY)
    assert {"order", "refund"} <= terms, sorted(terms)
    assert index._answerability(terms, method) == pytest.approx(NAME_TERM_BOOST)


def test_one_shared_term_earns_half_the_boost(index):
    """The boost scales with overlap rather than firing on any single match.

    "refunded" shares only "order" with `refund_order`: the tokenizer splits
    subtokens but does not stem, so this is the common case for a question
    phrased in natural English.
    """
    method = _by_qualname(index, "refund_order")
    terms = index.query_content_terms("how is an order refunded")
    assert terms & {"order", "refund"} == {"order"}, sorted(terms)
    expected = 1.0 + (NAME_TERM_BOOST - 1.0) / 2
    assert index._answerability(terms, method) == pytest.approx(expected)


def test_an_unrelated_name_is_not_boosted(index):
    method = _by_qualname(index, "place_order")
    terms = index.query_content_terms("how is a payment captured")
    assert index._answerability(terms, method) == pytest.approx(1.0)


def test_a_container_is_never_name_boosted(index):
    """Withheld from containers because it would reward a name collision.

    On the demo fixture, "trace an order *request* from route to persistence"
    promoted a test helper class literally named `Request` to rank 1, ahead of
    every route body the question is about.
    """
    container = _by_qualname(index, "OrderService")
    terms = index.query_content_terms("how does the order service refund an order")
    # Shares "order" and "service" with the qualname, so the boost would fire
    # if containers were eligible for it.
    assert {"order", "service"} <= terms
    assert index._answerability(terms, container) == pytest.approx(CONTAINER_SEED_PENALTY)


def test_question_words_are_dropped_but_content_words_are_kept():
    """`get`/`set` are the load-bearing exclusions: both are extremely common
    method names, so leaving them in would boost every accessor in the repo on
    any question phrased "how do I get ...". Ordinary nouns must survive."""
    terms = Index.query_content_terms("how do I get the value and set it")
    assert "value" in terms
    assert not ({"get", "set", "how", "the", "and"} & terms), sorted(terms)


def _seed_scores(pack):
    """{qualname: score} for the pack's seeds.

    `seeds` is emitted in a stable order regardless of ranking, so the score is
    the observable that carries the re-rank -- not the position in the list.
    """
    return {c.get("qualname"): c.get("score") for c in pack["seeds"]}


def test_the_rerank_inverts_container_and_method_ranking(index):
    """End to end, and the whole point of the change.

    Without the re-rank the class header outscores the method that answers the
    question: its docstring repeats the question's words and it is long enough
    to match many of them. With the re-rank the order inverts, so under a
    budget that cannot hold both it is the method that survives.
    """
    off = _seed_scores(index.pack_context(QUERY, budget_tokens=4000, rerank_answerability=False))
    on = _seed_scores(index.pack_context(QUERY, budget_tokens=4000, rerank_answerability=True))

    assert off["OrderService"] > off["OrderService.refund_order"], off
    assert on["OrderService.refund_order"] > on["OrderService"], on


def test_rerank_can_be_turned_off(index):
    """The pre-2.3 ranking stays reachable, so a caller comparing against an
    older pack is not left guessing which change moved their results."""
    terms = index.query_content_terms(QUERY)
    container = _by_qualname(index, "OrderService")
    method = _by_qualname(index, "refund_order")
    assert index._answerability(terms, container) < index._answerability(terms, method)

    off = _seed_scores(index.pack_context(QUERY, budget_tokens=4000, rerank_answerability=False))
    on = _seed_scores(index.pack_context(QUERY, budget_tokens=4000, rerank_answerability=True))
    assert off != on
    # The flag changes ranking only -- it must not change which chunks exist.
    assert set(off) == set(on)


def test_the_benchmark_task_files_stay_disjoint():
    """The held-out set only validates a change if it shares no repository with
    the set the change was developed against."""
    bench = Path(__file__).resolve().parents[1] / "benchmarks" / "real"
    dev = {
        r["name"] for r in json.loads((bench / "repos.json").read_text(encoding="utf8"))["repos"]
    }
    held = {
        r["name"]
        for r in json.loads((bench / "repos_holdout.json").read_text(encoding="utf8"))["repos"]
    }
    assert dev and held
    assert not (dev & held), f"held-out set shares repositories with the dev set: {dev & held}"
