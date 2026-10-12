"""One question over several indexes: `query`/`rag --index A --index B` (#396)."""

from __future__ import annotations

import json

import pytest

from repo2graph.cli import main
from repo2graph.federated import merge_by_rank


def _index(tmp_path, name, files):
    repo = tmp_path / name
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf8")
    out = repo / ".r2g"
    main(["build", str(repo), "-o", str(out), "--formats", "jsonl"])
    return out


@pytest.fixture
def two(tmp_path, capsys):
    billing = _index(
        tmp_path,
        "billing",
        {"invoice.py": "def charge_invoice(invoice):\n    return invoice.total * 100\n"},
    )
    orders = _index(
        tmp_path,
        "orders",
        {"order.py": "def place_order(order):\n    return charge_invoice(order.invoice)\n"},
    )
    capsys.readouterr()
    return billing, orders


def test_query_answers_from_both_and_names_each_repo(two, capsys):
    billing, orders = two
    main(
        ["query", "charge invoice order", "--index", str(billing), "--index", str(orders), "--json"]
    )
    res = json.loads(capsys.readouterr().out)
    assert {r["repo"] for r in res} == {"billing", "orders"}

    main(["query", "charge invoice order", "--index", str(billing), "--index", str(orders)])
    text = capsys.readouterr().out
    assert "--- billing:invoice.py::charge_invoice" in text
    assert "--- orders:order.py::place_order" in text


def test_the_budget_holds_across_the_merged_set(two, capsys):
    billing, orders = two
    main(
        [
            "query",
            "charge invoice order",
            "--index",
            str(billing),
            "--index",
            str(orders),
            "--budget",
            "80",
            "--json",
        ]
    )
    res = json.loads(capsys.readouterr().out)
    assert sum(len(r.get("text") or "") for r in res) <= 80


def test_rag_cites_the_repo_and_respects_a_token_budget(two, capsys):
    billing, orders = two
    main(
        [
            "rag",
            "how is an order charged",
            "--index",
            str(billing),
            "--index",
            str(orders),
            "--budget-tokens",
            "400",
            "--format",
            "json",
        ]
    )
    pack = json.loads(capsys.readouterr().out)
    assert pack["repos"] == ["billing", "orders"]
    assert "[cite: billing:invoice.py:" in pack["markdown"]
    assert "[cite: orders:order.py:" in pack["markdown"]
    assert pack["tokens_used"] <= 400


def test_a_budget_too_small_for_the_header_packs_nothing(two):
    """A header that does not fit used to switch the budget off entirely (#482).

    `_fit` reads 0 as "no ceiling", which is the caller's sentinel; it was also
    what `budget - len(header)` came to once the header outgrew the budget, so
    every merged chunk came back. Measured at 25x the stated ceiling, with
    `truncated` reporting False.
    """
    from repo2graph.federated import open_indexes, pack_context

    # 40 indexes, so the header alone costs ~100 tokens.
    indexes = open_indexes([str(d) for d in two] * 20)
    head_tokens = len(
        pack_context(indexes, "charge", budget_tokens=1000)["markdown"].split("\n")[0]
    )

    for budget in (1, 10, 50, 90, 100):
        pack = pack_context(indexes, "how is an order charged", budget_tokens=budget)
        assert pack["tokens_used"] <= budget, (budget, pack["tokens_used"])
        assert pack["chunks"] == []
    assert head_tokens  # the header is real; it is the thing that did not fit


@pytest.mark.parametrize("key", ["budget_chars", "budget_tokens"])
def test_a_budget_of_zero_still_means_unbounded(two, key):
    """The caller's 0 is the unlimited sentinel, as in `Index.pack_context`.

    The #482 fix has to keep this distinguishable from a remainder of 0, which
    is why `_fit` takes None rather than reading 0 two ways.
    """
    from repo2graph.federated import open_indexes, pack_context

    indexes = open_indexes([str(d) for d in two])
    assert pack_context(indexes, "how is an order charged", **{key: 0})["chunks"]


def test_the_header_is_bounded_by_the_budget(two):
    """The header is counted against the budget, so it is also cut by it (#482)."""
    from repo2graph.federated import open_indexes, pack_context

    many = open_indexes([str(d) for d in two] * 20)
    full = pack_context(many, "charge", budget_tokens=0)["markdown"].split("\n")[0]
    assert full.endswith("orders#20")  # every name, when there is room

    cut = pack_context(many, "charge", budget_tokens=20)["markdown"].split("\n")[0]
    assert cut.startswith("# Context from 40 repositories: billing")
    assert "more" in cut and not cut.endswith("orders#20")

    # Below even the count, nothing: a pack cannot overrun its ceiling to
    # describe itself.
    assert pack_context(many, "charge", budget_tokens=1)["markdown"] == ""


def test_the_pack_carries_every_declared_packresult_key(two):
    """A federated `rag --json` promises the same shape as a single-index one.

    Six keys were missing, `token_count_method` among them -- so a consumer
    reading how `tokens_used` was counted got nothing on this path (#482).
    """
    from repo2graph.federated import open_indexes, pack_context
    from repo2graph.schema import PackResult

    pack = pack_context(open_indexes([str(d) for d in two]), "charge", budget_tokens=400)
    assert not set(PackResult.__annotations__) - set(pack)


def test_token_margin_bounds_the_merged_pack(two):
    """`--token-margin` reached each index's own pack but never the merged fit (#482)."""
    from repo2graph.federated import open_indexes, pack_context

    indexes = open_indexes([str(d) for d in two])
    wide = pack_context(indexes, "how is an order charged", budget_tokens=200, token_margin=0.0)
    tight = pack_context(indexes, "how is an order charged", budget_tokens=200, token_margin=2.0)
    assert tight["tokens_used"] < wide["tokens_used"]
    assert tight["tokens_used"] <= 200 / 3 + 1
    assert tight["token_safety_margin"] == 2.0


def test_the_tokenizer_that_filled_the_pack_is_named(two):
    from repo2graph.federated import open_indexes, pack_context
    from repo2graph.tokenizers import get_token_counter

    indexes = open_indexes([str(d) for d in two])
    pack = pack_context(
        indexes, "charge", budget_tokens=400, count_tokens=get_token_counter("conservative")
    )
    assert pack["token_count_method"] == "estimate:conservative-3cpt"


def test_a_missing_index_is_skipped_with_a_warning(two, tmp_path, capsys):
    billing, _orders = two
    main(
        [
            "query",
            "charge invoice",
            "--index",
            str(billing),
            "--index",
            str(tmp_path / "gone"),
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert "skipping index" in captured.err
    assert json.loads(captured.out)


def test_no_readable_index_is_an_error(tmp_path):
    with pytest.raises(SystemExit, match="none of the --index"):
        main(["query", "x", "--index", str(tmp_path / "a"), "--index", str(tmp_path / "b")])


def test_vectors_do_not_federate(two):
    billing, orders = two
    with pytest.raises(SystemExit, match="--vectors"):
        main(["query", "x", "--index", str(billing), "--index", str(orders), "--vectors"])


def test_merge_interleaves_by_rank_not_by_raw_score():
    a = [{"id": "a1", "score": 900.0}, {"id": "a2", "score": 800.0}]
    b = [{"id": "b1", "score": 2.0}, {"id": "b2", "score": 1.0}]
    assert [r["id"] for r in merge_by_rank([("a", a), ("b", b)])] == ["a1", "b1", "a2", "b2"]


def test_a_corrupt_index_is_skipped_with_a_warning(two, capsys):
    billing, orders = two
    from repo2graph.export import path as artifact_path

    artifact_path(orders, "chunks.jsonl").write_bytes(b'{"id": "x", "text": \n\x00garbage')
    main(["query", "charge invoice", "--index", str(billing), "--index", str(orders), "--json"])
    captured = capsys.readouterr()
    assert "skipping index" in captured.err
    assert {r["repo"] for r in json.loads(captured.out)} == {"billing"}
