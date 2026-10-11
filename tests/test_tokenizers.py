"""Token counters and the safety margin for pack budgets (#290)."""

from __future__ import annotations

import json
import sys
import types

import pytest

from repo2graph.cli import main
from repo2graph.query import Index
from repo2graph.tokenizers import conservative, get_token_counter, heuristic


def test_each_counter_names_how_exact_it_is():
    assert get_token_counter(None).token_count_method == "heuristic"
    assert get_token_counter("heuristic") is heuristic
    assert get_token_counter("conservative").token_count_method == "estimate:conservative-3cpt"


def test_conservative_never_counts_below_the_heuristic():
    for text in ("", "a", "abcd", "x" * 1001, "def f(): return 1\n" * 50):
        assert conservative(text) >= heuristic(text)
    assert conservative("abcd") == 2


def test_an_unknown_tokenizer_is_refused():
    with pytest.raises(ValueError, match="unknown tokenizer"):
        get_token_counter("bpe")


def test_tiktoken_without_the_package_says_how_to_get_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "tiktoken", None)
    with pytest.raises(ValueError, match="pip install tiktoken"):
        get_token_counter("tiktoken")


def test_tiktoken_counts_with_the_named_encoding(monkeypatch):
    class Enc:
        def encode(self, text, disallowed_special=()):
            return text.split()

    seen = []
    fake = types.SimpleNamespace(get_encoding=lambda name: seen.append(name) or Enc())
    monkeypatch.setitem(sys.modules, "tiktoken", fake)
    count = get_token_counter("tiktoken:cl100k_base")
    assert count("three short words") == 3
    assert count.token_count_method == "exact:tiktoken/cl100k_base"
    assert seen == ["cl100k_base"]


def test_a_margin_fills_the_pack_to_the_reduced_budget(mini_index):
    idx = Index(mini_index)
    full = idx.pack_context("parse config", budget_tokens=120)
    spare = idx.pack_context("parse config", budget_tokens=120, token_margin=1.0)
    assert full["token_safety_margin"] == 0.0 and spare["token_safety_margin"] == 1.0
    assert spare["tokens_used"] <= 60 < 120
    assert spare["tokens_used"] <= full["tokens_used"]


def test_rag_takes_the_tokenizer_and_margin_flags(mini_index, capsys):
    rc = main(
        [
            "rag",
            "parse config",
            "-o",
            str(mini_index),
            "--format",
            "json",
            "--budget-tokens",
            "200",
            "--tokenizer",
            "conservative",
            "--token-margin",
            "0.25",
        ]
    )
    pack = json.loads(capsys.readouterr().out)
    assert rc in (0, None)
    assert pack["token_count_method"] == "estimate:conservative-3cpt"
    assert pack["token_safety_margin"] == 0.25
    assert pack["tokens_used"] <= 160


def test_rag_refuses_an_unknown_tokenizer(mini_index):
    with pytest.raises(SystemExit, match="unknown tokenizer"):
        main(["rag", "x", "-o", str(mini_index), "--tokenizer", "nope"])
