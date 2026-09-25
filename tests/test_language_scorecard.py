"""Tests for the Language Quality Scorecard Generator."""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.generate_language_scorecard import (  # noqa: E402
    DIMENSION_WEIGHTS,
    audit_test_coverage,
    evaluate_language,
    format_detail_markdown,
    format_markdown_table,
    generate_all_scorecards,
    score_to_grade,
)


def test_score_to_grade_boundaries():
    assert score_to_grade(95) == "A"
    assert score_to_grade(90) == "A-"
    assert score_to_grade(85) == "B+"
    assert score_to_grade(80) == "B"
    assert score_to_grade(72) == "B-"
    assert score_to_grade(67) == "C+"
    assert score_to_grade(62) == "C"
    assert score_to_grade(52) == "D"
    assert score_to_grade(40) == "F"


def test_dimension_weights_sum_to_one():
    total = sum(DIMENSION_WEIGHTS.values())
    assert abs(total - 1.0) < 1e-6


def test_generate_all_scorecards_covers_all_languages():
    import repo2graph.parse as p

    cards = generate_all_scorecards()
    card_langs = {c.language for c in cards}
    assert card_langs == set(p.LANG_CFG.keys())
    assert len(cards) == 17


def test_tier_prioritization_order():
    cards = generate_all_scorecards()
    # Priority order asserts Tier 1 comes before Tier 2, then Tier 3, then Tier 4
    tiers = [c.tier for c in cards]
    tier_ranks = [
        1 if "Tier 1" in t else 2 if "Tier 2" in t else 3 if "Tier 3" in t else 4 for t in tiers
    ]
    assert tier_ranks == sorted(tier_ranks)

    tier_1_langs = {c.language for c in cards if "Tier 1" in c.tier}
    assert tier_1_langs == {"typescript", "javascript", "tsx", "python"}

    tier_2_langs = {c.language for c in cards if "Tier 2" in c.tier}
    assert tier_2_langs == {"go", "java", "kotlin"}


def test_evaluate_language_dimensions():
    import repo2graph.parse as p

    card = evaluate_language("python", p.LANG_CFG["python"], {"files_count": 5, "funcs_count": 6})
    assert card.language == "python"
    assert "Tier 1" in card.tier
    assert 0 <= card.overall_score <= 100
    assert card.parsing.score >= 90
    assert card.calls.score >= 90
    assert card.imports.score >= 80
    assert card.tests.score == 30
    assert card.framework_edges.score == 20
    assert card.test_coverage_funcs == 6
    assert len(card.priority_actions) > 0


def test_markdown_formatting_is_valid_table():
    cards = generate_all_scorecards()
    md = format_markdown_table(cards)
    lines = md.split("\n")
    assert len(lines) == 19  # Header + separator + 17 languages
    assert lines[0].startswith("| Language |")
    assert lines[1].startswith("| --- |")
    for line in lines[2:]:
        assert line.startswith("| **")
        assert line.endswith(" |")


def test_detail_markdown_contains_all_languages():
    cards = generate_all_scorecards()
    detail = format_detail_markdown(cards)
    for c in cards:
        assert f"### {c.language.capitalize()}" in detail
        assert "Key Findings" in detail


def test_audit_test_coverage_finds_real_tests():
    root = Path(__file__).resolve().parents[1]
    coverage = audit_test_coverage(root / "tests")
    assert "python" in coverage
    assert "javascript" in coverage
    assert coverage["python"]["funcs_count"] >= 1


def test_cli_json_output(tmp_path):
    out_file = tmp_path / "scorecard.json"
    cmd = [
        sys.executable,
        "scripts/generate_language_scorecard.py",
        "--json",
        "--output",
        str(out_file),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0
    assert out_file.exists()

    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) == 17
    lang_names = {item["language"] for item in data}
    assert "typescript" in lang_names
    assert "python" in lang_names
    assert "go" in lang_names
    assert "java" in lang_names
