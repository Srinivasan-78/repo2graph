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
