# SPDX-FileCopyrightText: 2026 Srinivasan Vijayaraghavan
#
# SPDX-License-Identifier: MIT

"""An unloadable grammar must fail loudly, never produce a quietly empty graph.

`parser_for()` swallows LookupError/ValueError/ImportError/AttributeError and
returns None, and `parse_source` used to answer an unavailable grammar with an
empty-but-valid `ParsedFile`. That made "the grammar did not load" byte-identical
to "this file genuinely declares nothing": a build with no grammars at all
reported `parsed == files`, wrote a graph of files and directories with zero code
edges, exited 0, and was cached by `--incremental` so the next build reproduced it
without retrying. `doctor` agreed everything was fine, because its grammar check
counted keys in the `LANG_CFG` dict literal instead of loading anything.
"""

from pathlib import Path

import pytest
from repo2graph.doctor import check_tree_sitter
from repo2graph.graph import build
from repo2graph.parse import ParseError, parse_source

REPO_ROOT = Path(__file__).resolve().parents[1]

PY_SOURCE = b"import os\n\n\nclass Thing:\n    def run(self):\n        return os.getcwd()\n"


def _write_repo(root: Path) -> Path:
    repo = root / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "mod.py").write_bytes(PY_SOURCE)
    (repo / "pkg" / "other.py").write_bytes(b"def helper():\n    return 1\n")
    return repo


def _break_all_grammars(monkeypatch):
    """Every grammar lookup fails, as it does with no network and no bundled wheel."""
    monkeypatch.setattr("repo2graph.parse.parser_for", lambda lang: None)


# --------------------------------------------------------------------------
# parse_source keeps the two cases distinguishable
# --------------------------------------------------------------------------


def test_unavailable_grammar_is_flagged_not_silently_empty(monkeypatch):
    _break_all_grammars(monkeypatch)
    pf = parse_source(PY_SOURCE, "python")
    assert pf.grammar_unavailable is True
    assert pf.symbols == []


def test_a_genuinely_empty_file_is_not_flagged():
    """The flag must mean "no grammar", not "no symbols" -- or it is useless."""
    pf = parse_source(b"# just a comment\n", "python")
    assert pf.grammar_unavailable is False


def test_an_unsupported_language_is_not_flagged():
    """No LANG_CFG entry is an ordinary outcome, not an environment fault."""
    pf = parse_source(b"nothing here\n", "definitely-not-a-language")
    assert pf.grammar_unavailable is False


def test_a_real_parse_sets_nothing_and_finds_symbols():
    pf = parse_source(PY_SOURCE, "python")
    assert pf.grammar_unavailable is False
    assert [s.name for s in pf.symbols]


def test_parser_for_answers_none_for_a_backend_error_outside_the_old_tuple():
    """1.x raises its own DownloadError, which is not a LookupError/ValueError/etc.

    `parser_for` used to catch only
    `(LookupError, ValueError, ImportError, AttributeError)`. Every
    tree-sitter-language-pack 1.x failure -- DownloadError,
    ChecksumMismatchError, CacheLockError, DynamicLoadError, ParserSetupError --
    derives from a private base outside that tuple, so offline they escaped
    instead of answering None and the file was dropped uncounted.
    """
    from repo2graph import parse as parse_mod

    class BackendError(Exception):
        """Stands in for tree_sitter_language_pack.exceptions.Error."""

    def boom(lang):
        raise BackendError("Download error: grammar unreachable")

    parse_mod.parser_for.cache_clear()
    try:
        monkey = parse_mod._get_parser
        parse_mod._get_parser = boom
        assert parse_mod.parser_for("python") is None
    finally:
        parse_mod._get_parser = monkey
        parse_mod.parser_for.cache_clear()


# --------------------------------------------------------------------------
# build refuses to publish the empty graph
# --------------------------------------------------------------------------


def test_build_fails_when_no_grammar_loads_for_any_source_file(tmp_path, monkeypatch):
    repo = _write_repo(tmp_path)
    _break_all_grammars(monkeypatch)
    with pytest.raises(ParseError) as exc:
        build(repo)
    msg = str(exc.value)
    # The message has to name the cause and the remedy: the whole failure mode
    # was that this looked like an empty repository.
    assert "no tree-sitter grammar could be loaded" in msg
    assert "not a property of the repository" in msg
    assert "doctor" in msg
    assert "tree-sitter-language-pack>=0.7,<1.0" in msg


def test_build_does_not_fail_on_a_repo_with_no_supported_source(tmp_path, monkeypatch):
    """The check must fire on broken grammars, not on a genuinely codeless tree."""
    repo = tmp_path / "docs-only"
    repo.mkdir()
    (repo / "README.md").write_text("# just docs\n", encoding="utf8")
    _break_all_grammars(monkeypatch)
    g = build(repo)  # must not raise
    assert g.stats.get("grammar_unavailable", 0) == 0


def test_build_succeeds_and_counts_zero_unavailable_normally(tmp_path):
    repo = _write_repo(tmp_path)
    g = build(repo)
    assert g.stats.get("grammar_unavailable", 0) == 0
    assert g.stats["parsed"] >= 2


def test_partial_grammar_loss_is_counted_without_failing(tmp_path, monkeypatch):
    """One broken language must not abort a build that parsed something else."""
    repo = _write_repo(tmp_path)
    (repo / "pkg" / "a.go").write_bytes(b"package main\n\nfunc Run() {}\n")
    from repo2graph import parse as parse_mod

    real = parse_mod.parser_for
    monkeypatch.setattr(
        "repo2graph.parse.parser_for", lambda lang: None if lang == "go" else real(lang)
    )
    g = build(repo)
    assert g.stats.get("grammar_unavailable", 0) == 1
    assert g.stats["parsed"] >= 2


# --------------------------------------------------------------------------
# doctor reports what loads, not what is configured
# --------------------------------------------------------------------------


def test_doctor_fails_when_no_grammar_can_be_loaded(monkeypatch):
    _break_all_grammars(monkeypatch)
    result = check_tree_sitter()
    assert result.status == "fail"
    assert "no grammars could be loaded" in result.summary
    assert result.remediation and "tree-sitter-language-pack>=0.7,<1.0" in result.remediation


def test_doctor_warns_when_only_some_grammars_load(monkeypatch):
    from repo2graph import parse as parse_mod

    real = parse_mod.parser_for
    monkeypatch.setattr(
        "repo2graph.parse.parser_for", lambda lang: None if lang == "python" else real(lang)
    )
    result = check_tree_sitter()
    assert result.status == "warn"
    assert "could not be loaded" in result.summary
    assert any("python" in d for d in result.details)


def test_doctor_is_ok_and_reports_loaded_not_configured():
    result = check_tree_sitter()
    assert result.status == "ok"
    # "configured" was the old wording and the old bug: it counted dict keys.
    assert "loaded" in result.summary
    assert "configured" not in result.summary


def test_doctor_grammar_check_survives_a_grammar_that_raises(monkeypatch):
    def boom(lang):
        raise RuntimeError("grammar blew up")

    monkeypatch.setattr("repo2graph.parse.parser_for", boom)
    result = check_tree_sitter()
    assert result.status == "fail"  # reported, not propagated


# --------------------------------------------------------------------------
# the dependency cap is a security boundary, so pin it in a test
# --------------------------------------------------------------------------


def test_language_pack_is_capped_below_1_0():
    """0.x bundles every grammar in the wheel; 1.x downloads them on first use.

    0.13.0 ships a 33 MB wheel. 1.20.0 ships 2.5 MB and an 89 KB sdist, fetching
    ~21 MB of unsigned native code from the network into the process holding the
    user's source. That contradicts the no-network guarantee in README.md and
    .github/SECURITY.md, and makes `uv.lock` and the SBOM describe a loader
    rather than what actually executes. A dependabot bump must not quietly undo
    this, so the bound is asserted rather than merely commented.
    """
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - py3.10
        pytest.skip("tomllib needs Python 3.11+")
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf8"))
    pins = [d for d in data["project"]["dependencies"] if "tree-sitter-language-pack" in d]
    assert pins, "tree-sitter-language-pack must stay a declared dependency"
    assert "<1.0" in pins[0], (
        f"tree-sitter-language-pack must stay capped below 1.0, got {pins[0]!r}. "
        "Raising it means either vendoring grammars or dropping the no-network claim."
    )
