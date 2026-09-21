# @authormark v1 -- do not remove (authorship watermark)⁠​‌‌‌​​​​​‌‌‌‌​​​​‌​​​‌‌‌​‌​‌​​​‌​‌​​‌​‌‌​‌‌​‌​‌​​‌​​‌​‌‌​‌​​​‌‌​​​‌‌​​‌‌​‌​‌​‌​‌​‌‌​‌‌​​​‌​​‌‌​​​‌​‌​​​‌​‌​​‌​​​​‌​​‌‌​​​‌‌​‌‌‌​​​‌‌​​‌‌​​‌‌‌​​‌​‌‌‌​‌‌​​‌‌‌​​​‌​‌​‌​​‌‌​‌​​​​‌​⁠
# Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
# Author: https://github.com/Srinivasan-78
# SPDX-License-Identifier: MIT
# Fingerprint: AMK1.pxGQKjKF3UlLQHLn39vqSB
"""Change 4 -- budget in tokens, not characters. AC-22 .. AC-25.

No score, rank or float comparison is asserted anywhere here; every assertion
is a length, a key or an equality between two runs of the same code.
"""

import json

import pytest

from conftest import MINI_QUERY
from repo2graph.cli import main
from repo2graph.query import Index

TOKEN_BUDGETS = [50, 200, 1000, 6000]


@pytest.mark.parametrize("budget", TOKEN_BUDGETS)
def test_ac22_budget_tokens_is_reported_and_respected(big_index, budget):
    """AC-22: tokens_budget == N and tokens_used <= N.

    `big_index` is used rather than `mini_index` so that every budget in the
    list actually binds -- the mini fixture packs to well under 6000 tokens, so
    the two larger budgets would pass on a pack nothing ever trimmed.
    """
    idx = Index(big_index)
    res = idx.pack_context(MINI_QUERY, k=20, budget_tokens=budget)
    assert res["tokens_budget"] == budget
    assert res["tokens_used"] <= budget, (res["tokens_used"], budget)


@pytest.mark.parametrize("budget", TOKEN_BUDGETS)
def test_ac22_token_budget_binds_on_the_big_fixture(big_index, budget):
    """AC-22 (guard): the unbounded pack really is larger than every budget in
    the list, so the assertion above is not vacuous."""
    idx = Index(big_index)
    unbounded = idx.pack_context(MINI_QUERY, k=20, budget_chars=0)
    from repo2graph.query import count_tokens

    assert count_tokens(unbounded["markdown"]) > budget


def test_ac23_char_budget_leaves_tokens_budget_unset(mini_index):
    """AC-23: with budget_chars only, tokens_budget == 0, tokens_used is the
    measure of the markdown, and used_chars keeps its current meaning."""
    from repo2graph.query import count_tokens

    idx = Index(mini_index)
    for budget_chars in (24000, 1200, 0):
        res = idx.pack_context(MINI_QUERY, budget_chars=budget_chars)
        assert res["tokens_budget"] == 0, budget_chars
        assert res["tokens_used"] == count_tokens(res["markdown"]), budget_chars
        assert res["used_chars"] == len(res["markdown"]), budget_chars
        assert res["budget_chars"] == budget_chars


def test_ac23_count_tokens_default_hook():
    """AC-23: the default measure is len(text) // CHARS_PER_TOKEN, floored at
    1 for any non-empty string and 0 for the empty one."""
    from repo2graph.query import CHARS_PER_TOKEN, count_tokens

    assert CHARS_PER_TOKEN == 4
    assert count_tokens("") == 0
    assert count_tokens("a") == 1
    assert count_tokens("a" * 3) == 1
    assert count_tokens("a" * 4) == 1
    assert count_tokens("a" * 400) == 100


def test_ac24_a_custom_count_tokens_drives_all_accounting(big_index):
    """AC-24: with count_tokens=len, a budget_tokens of B bounds the markdown
    at B *characters* -- proving the measure is used for every comparison, not
    just for the reported total."""
    idx = Index(big_index)
    for budget in (500, 2000, 8000):
        res = idx.pack_context(MINI_QUERY, k=20, budget_tokens=budget, count_tokens=len)
        assert len(res["markdown"]) <= budget, (budget, len(res["markdown"]))
        assert res["tokens_budget"] == budget
        assert res["tokens_used"] == len(res["markdown"])


def test_ac24_custom_measure_is_not_ignored(big_index):
    """AC-24 (guard): count_tokens=len must produce a strictly smaller pack
    than the default measure at the same budget, or it was never consulted."""
    idx = Index(big_index)
    default = idx.pack_context(MINI_QUERY, k=20, budget_tokens=2000)
    custom = idx.pack_context(MINI_QUERY, k=20, budget_tokens=2000, count_tokens=len)
    assert len(custom["markdown"]) < len(default["markdown"])


def test_ac25_rag_budget_tokens_bounds_the_written_pack(big_index, capsys):
    """AC-25 (a): `rag --budget-tokens 200` writes a pack measuring <= 200."""
    from repo2graph.query import count_tokens

    main(["rag", MINI_QUERY, "-o", str(big_index), "--budget-tokens", "200"])
    pack = capsys.readouterr().out
    assert count_tokens(pack.rstrip("\n")) <= 200, len(pack)


def test_ac25_budget_tokens_wins_over_budget(big_index, capsys):
    """AC-25 (b): passing both gives the same result as --budget-tokens alone."""
    main(["rag", MINI_QUERY, "-o", str(big_index), "--budget-tokens", "200"])
    alone = capsys.readouterr().out
    main(["rag", MINI_QUERY, "-o", str(big_index), "--budget", "24000", "--budget-tokens", "200"])
    both = capsys.readouterr().out
    assert both == alone

    # ... and it is not simply that --budget was ignored in both runs.
    main(["rag", MINI_QUERY, "-o", str(big_index), "--budget", "24000"])
    chars_only = capsys.readouterr().out
    assert chars_only != alone


def test_ac25_budget_tokens_reaches_the_json_form(big_index, capsys):
    """AC-25 (c): the JSON pack reports the token budget it was given."""
    main(["rag", MINI_QUERY, "-o", str(big_index), "--budget-tokens", "200", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["tokens_budget"] == 200
    assert payload["tokens_used"] <= 200


def test_ac25_budget_tokens_rejects_a_negative(big_index, capsys):
    """AC-25 (d): --budget-tokens uses the shared _nonneg argparse type, so a
    negative is rejected by the validator -- not merely unrecognised."""
    with pytest.raises(SystemExit) as exc:
        main(["rag", MINI_QUERY, "-o", str(big_index), "--budget-tokens", "-1"])
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "--budget-tokens" in err, err
    assert "must be >= 0" in err, err
