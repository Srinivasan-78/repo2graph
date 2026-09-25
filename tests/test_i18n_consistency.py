"""The five translated READMEs must not drift away from the English one.

Nothing enforced this before, and all six files drifted together: every one of
them still advertised "16 grammars / 28 file extensions" after Lua brought the
count to 17/29, and every translated hero still opened on "AST-driven code
graphs & zero-dependency GraphRAG" after the English hero had been repositioned
around the product's outcome.

Per AGENTS.md's "tests must pin values, not compare the implementation to
itself": none of the assertions below compare one README against another's
prose, because a translation legitimately differs from its source in every
sentence. They pin *language-independent literals* instead -- command
fragments, edge kinds and the citation marker -- each hand-derived from the
English README, plus the `LANG_CFG` grammar list, which is the one thing all
six files must state identically.

See POSITIONING.md for the messaging contract these tests guard.
"""

import re
from pathlib import Path

import pytest

from test_doc_consistency import LANGUAGE_TOKENS

REPO_ROOT = Path(__file__).resolve().parents[1]
README_PATH = REPO_ROOT / "README.md"
I18N_DIR = REPO_ROOT / "docs" / "i18n"

TRANSLATIONS = sorted(I18N_DIR.glob("README_*.md"))

# Every README -- English included -- headings its architecture section with
# 📐 and its persona section with 👥. Emoji are the only section markers that
# survive translation, which is what makes "above the fold" and "below the
# architecture heading" checkable in six languages at once.
ARCHITECTURE_MARKER = "## 📐"
PERSONAS_MARKER = "## 👥"

# "GraphRAG" is not wrong -- the architecture section keeps it, and so do the
# PyPI keywords. It just cannot be the opening argument. The boundary is the
# architecture heading, which is the strongest form of the rule and the one
# the current copy actually satisfies.
ARCHITECTURE_ONLY_JARGON = ("GraphRAG",)

# The first screen is stricter: hero plus the "what is it" section, ending at
# the persona heading. Nothing mechanical belongs there at all. BM25 is
# deliberately absent from this rule below the fold -- the grep comparison
# table names it on purpose, because conceding that lexical search is still
# the seeding step is part of the argument.
FIRST_SCREEN_JARGON = (r"GraphRAG", r"BM25", r"pack_context\(\)", r"\bAST\b", r"\bRRF\b")

# Literals that prove the three positioning sections survived translation.
# Each appears exactly once per README, inside the section named beside it,
# and each is a code fragment rather than prose, so it reads identically in
# every language.
POSITIONING_ANCHORS = {
    "explain retrieval": "the grep / vector-search comparison table",
    "build --incremental": "the stale-index row of what-it-does-not-do",
    "[cite:": "the citation promise",
    "CO_CHANGE": "the git-history edge kind",
    "repo2graph-mcp": "the MCP client configuration",
}


def _all_readmes():
    return [README_PATH, *TRANSLATIONS]


def test_the_translation_set_is_the_one_the_switcher_offers():
    """Five translations, each linked from the English language switcher."""
    assert len(TRANSLATIONS) == 5, f"expected 5 translations, found {[p.name for p in TRANSLATIONS]}"

    readme_text = README_PATH.read_text(encoding="utf-8")
    for path in TRANSLATIONS:
        assert f"docs/i18n/{path.name}" in readme_text, (
            f"{path.name} exists but is not linked from README.md's language switcher"
        )


@pytest.mark.parametrize("path", _all_readmes(), ids=lambda p: p.name)
def test_every_readme_documents_every_parsed_grammar(path):
    """The 16-grammars drift hit all six files; guard all six."""
    from repo2graph.parse import LANG_CFG

    assert set(LANGUAGE_TOKENS) == set(LANG_CFG), (
        f"LANGUAGE_TOKENS is out of sync with LANG_CFG: "
        f"missing={set(LANG_CFG) - set(LANGUAGE_TOKENS)}, "
        f"extra={set(LANGUAGE_TOKENS) - set(LANG_CFG)}"
    )

    text = path.read_text(encoding="utf-8")
    for key, pattern in LANGUAGE_TOKENS.items():
        assert re.search(pattern, text), (
            f"Language '{key}' (pattern {pattern!r}) missing from {path.name}"
        )


@pytest.mark.parametrize("path", _all_readmes(), ids=lambda p: p.name)
def test_graphrag_stays_below_the_architecture_heading(path):
    """No "GraphRAG" in a hero, in any language.

    The point of the term is not that it is wrong -- it is accurate and the
    architecture section keeps it. The point is that it cannot be the opening
    argument, in English or in translation.
    """
    text = path.read_text(encoding="utf-8")
    marker = text.find(ARCHITECTURE_MARKER)
    assert marker != -1, (
        f"{path.name} has no '{ARCHITECTURE_MARKER}' architecture heading, so the "
        f"jargon boundary cannot be located"
    )

    for term in ARCHITECTURE_ONLY_JARGON:
        first = text.find(term)
        if first == -1:
            continue
        assert first > marker, (
            f"{path.name}: {term!r} appears at offset {first}, above the architecture "
            f"heading at {marker}. Demoted vocabulary belongs in the architecture "
            f"section and below -- see POSITIONING.md's jargon policy."
        )


@pytest.mark.parametrize("path", _all_readmes(), ids=lambda p: p.name)
def test_the_first_screen_names_no_mechanism(path):
    """Hero plus "what is it" -- everything before the persona heading."""
    text = path.read_text(encoding="utf-8")
    end = text.find(PERSONAS_MARKER)
    assert end != -1, (
        f"{path.name} has no '{PERSONAS_MARKER}' persona heading, so the first screen "
        f"has no lower boundary"
    )

    first_screen = text[:end]
    for pattern in FIRST_SCREEN_JARGON:
        hit = re.search(pattern, first_screen)
        assert hit is None, (
            f"{path.name}: {hit.group(0)!r} appears on the first screen (offset "
            f"{hit.start()}). Above the fold, name the outcome and the channel; "
            f"the mechanism goes in the architecture section -- see POSITIONING.md."
        )


@pytest.mark.parametrize("path", _all_readmes(), ids=lambda p: p.name)
def test_every_readme_carries_the_positioning_anchors(path):
    """The persona / comparison / limitations sections survived translation."""
    text = path.read_text(encoding="utf-8")
    for anchor, where in POSITIONING_ANCHORS.items():
        assert anchor in text, f"{path.name} is missing {anchor!r} ({where})"
