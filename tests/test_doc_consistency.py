"""Automated documentation and interface consistency tests (Issue 60 / #319).

Prevents drift between code and documentation:
- CLI subcommands in repo2graph.cli vs README and docs/cli.md
- Parser language support in LANG_CFG vs README
- Action inputs and outputs in action.yml vs docs/github-action.md
- MCP registered tools in repo2graph.mcp vs docs/mcp.md
- CITATION.cff's version vs pyproject.toml's (Issue #404)
- npm/package.json's version vs pyproject.toml's (Issue #399)
"""

import json
import re
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
README_PATH = REPO_ROOT / "README.md"
CLI_DOC_PATH = REPO_ROOT / "docs" / "cli.md"
QUICKSTART_PATH = REPO_ROOT / "docs" / "quickstart.md"
INDEXING_PATH = REPO_ROOT / "docs" / "INDEXING.md"
ACTION_YML_PATH = REPO_ROOT / "action.yml"
ACTION_DOC_PATH = REPO_ROOT / "docs" / "github-action.md"
MCP_DOC_PATH = REPO_ROOT / "docs" / "mcp.md"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
CITATION_PATH = REPO_ROOT / "CITATION.cff"
NPM_PACKAGE_PATH = REPO_ROOT / "npm" / "package.json"


def _pyproject_version() -> str:
    """`pyproject.toml`'s `[project] version`, read by regex like
    `scripts/version_surfaces.py` does -- `tomllib` is 3.11+ only and this
    project's floor is 3.10, so a regex match on the one line that matters
    avoids adding a TOML-parsing dependency just for this test.
    """
    text = PYPROJECT_PATH.read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"(?P<v>[^"]+)"', text, re.MULTILINE)
    assert m, "pyproject.toml has no top-level version= line"
    return m.group("v")


# Map internal grammar keys to the exact token the READMEs use for them
# (JS/TS/TSX are documented abbreviated). One regex per LANG_CFG key: every
# family repo2graph actually parses must have a matching entry here.
#
# Module-level rather than local to test_languages_documented() because
# tests/test_i18n_consistency.py runs the same check against the five
# translated READMEs -- the language list drifted in all six files at once
# (all said 16 grammars / 28 extensions after Lua landed), so one mapping
# guarding one file was exactly the gap.
LANGUAGE_TOKENS = {
    "python": r"\bPython\b",
    "javascript": r"\bJS\b",
    "typescript": r"\bTS\b",
    "tsx": r"\bTSX\b",
    "go": r"\bGo\b",
    "rust": r"\bRust\b",
    "java": r"\bJava\b",
    "ruby": r"\bRuby\b",
    "c": r"\bC\b(?!\+\+|#)",
    "cpp": r"C\+\+",
    "csharp": r"C#",
    "php": r"\bPHP\b",
    "kotlin": r"\bKotlin\b",
    "swift": r"\bSwift\b",
    "scala": r"\bScala\b",
    "bash": r"\bBash\b",
    "lua": r"\bLua\b",
}


def test_cli_commands_documented():
    """Verify all CLI subcommands are documented in README.md and docs/cli.md."""
    # Inspect cli.py source / command table
    cli_py = (REPO_ROOT / "repo2graph" / "cli.py").read_text(encoding="utf-8")
    subparsers = re.findall(r'sub\.add_parser\(\s*["\']([a-zA-Z0-9_\-]+)["\']', cli_py)
    assert len(subparsers) >= 7

    readme_text = README_PATH.read_text(encoding="utf-8")
    cli_doc_text = CLI_DOC_PATH.read_text(encoding="utf-8")

    # Commands that must appear in docs (version is often a flag, but build, query, rag, map, stats, embed, doctor, github are primary)
    primary_commands = [cmd for cmd in subparsers if cmd not in ("gh",)]  # gh is alias for github

    for cmd in primary_commands:
        if cmd == "version":
            continue
        assert f"repo2graph {cmd}" in readme_text, f"CLI subcommand '{cmd}' missing from README.md"
        assert f"repo2graph {cmd}" in cli_doc_text or f"repo2graph {cmd:<8}" in cli_doc_text, (
            f"CLI subcommand '{cmd}' missing from docs/cli.md"
        )


def test_languages_documented():
    """Verify every LANG_CFG grammar key is documented in README.md.

    Word-boundary regexes, not plain substrings: `"c" in readme_text.lower()`
    or `"go" in ...` is true of nearly any English prose regardless of
    whether the language is mentioned, so those checks passed even with the
    language name deleted from README.md. `\\bC\\b` (with a negative
    lookahead so it doesn't also match the "C" inside "C++"/"C#") actually
    requires the token to appear.
    """
    from repo2graph.parse import LANG_CFG

    readme_text = README_PATH.read_text(encoding="utf-8")

    families = LANGUAGE_TOKENS

    assert set(families) == set(LANG_CFG), (
        f"families mapping is out of sync with LANG_CFG: "
        f"missing={set(LANG_CFG) - set(families)}, extra={set(families) - set(LANG_CFG)}"
    )

    for key, pattern in families.items():
        assert re.search(pattern, readme_text), (
            f"Language '{key}' (pattern {pattern!r}) missing from README.md"
        )


def test_action_inputs_and_outputs_documented():
    """Verify every input and output in action.yml is documented in docs/github-action.md."""
    assert ACTION_YML_PATH.exists()
    assert ACTION_DOC_PATH.exists()

    action_spec = yaml.safe_load(ACTION_YML_PATH.read_text(encoding="utf-8"))
    doc_text = ACTION_DOC_PATH.read_text(encoding="utf-8")

    inputs = action_spec.get("inputs", {})
    outputs = action_spec.get("outputs", {})

    assert len(inputs) > 0
    assert len(outputs) > 0

    for input_name in inputs:
        assert f"`{input_name}`" in doc_text, (
            f"Action input '{input_name}' is not documented in docs/github-action.md"
        )

    for output_name in outputs:
        assert f"`{output_name}`" in doc_text, (
            f"Action output '{output_name}' is not documented in docs/github-action.md"
        )


def test_mcp_tools_documented():
    """Verify every registered tool in repo2graph.mcp is documented in docs/mcp.md."""
    from repo2graph.mcp import TOOL_DESCRIPTIONS

    assert MCP_DOC_PATH.exists()
    mcp_doc_text = MCP_DOC_PATH.read_text(encoding="utf-8")

    for tool_name in TOOL_DESCRIPTIONS:
        assert f"`{tool_name}`" in mcp_doc_text, (
            f"MCP tool '{tool_name}' is not documented in docs/mcp.md"
        )


def test_citation_cff_matches_pyproject():
    """Issue #404: CITATION.cff must parse and stay in lockstep with pyproject.toml."""
    assert CITATION_PATH.exists(), "CITATION.cff is missing from the repo root"
    cff = yaml.safe_load(CITATION_PATH.read_text(encoding="utf-8"))

    assert cff["cff-version"] == "1.2.0"
    assert cff["title"] == "repo2graph"
    assert cff["license"] == "MIT"
    assert cff["repository-code"] == "https://github.com/Srinivasan-78/repo2graph"

    authors = cff["authors"]
    assert len(authors) >= 1
    assert authors[0]["given-names"] == "Srinivasan"
    assert authors[0]["family-names"] == "Vijayaraghavan"

    assert cff["version"] == _pyproject_version(), (
        "CITATION.cff's version has drifted from pyproject.toml's -- bump both together"
    )


def test_npm_launcher_version_matches_pyproject():
    """Issue #399: the npx launcher's package.json version stays paired with the PyPI release.

    `npm/README.md`'s "Release story" section promises the two are published from the same
    tag; this is the machine-checkable half of that promise.
    """
    assert NPM_PACKAGE_PATH.exists(), "npm/package.json is missing"
    package = json.loads(NPM_PACKAGE_PATH.read_text(encoding="utf-8"))
    assert package["name"] == "repo2graph-mcp"
    assert package["version"] == _pyproject_version(), (
        "npm/package.json's version has drifted from pyproject.toml's -- bump both together"
    )
    assert "bin" in package and "repo2graph-mcp" in package["bin"]
    bin_path = REPO_ROOT / "npm" / package["bin"]["repo2graph-mcp"]
    assert bin_path.is_file(), f"npm package.json's bin entry points at a missing file: {bin_path}"




def test_starter_questions_match_the_docs_verbatim():
    """The five starter prompts are authored once, in repo2graph/demo.py.

    README.md and docs/quickstart.md both render them, and `repo2graph demo`
    runs them -- so a prompt edited in one place and not the others would
    leave the docs telling users to run something the demo never exercises.
    Both the template form (what a reader copies onto their own repo) and the
    demo form (what actually runs) are pinned.
    """
    from repo2graph.demo import STARTER_QUESTIONS

    readme_text = README_PATH.read_text(encoding="utf-8")
    quickstart_text = QUICKSTART_PATH.read_text(encoding="utf-8")

    assert len(STARTER_QUESTIONS) == 5
    for q in STARTER_QUESTIONS:
        assert f"`{q.template}`" in readme_text, (
            f"starter prompt missing from README.md: {q.template!r}"
        )
        assert f"`{q.template}`" in quickstart_text, (
            f"starter prompt missing from docs/quickstart.md: {q.template!r}"
        )
        # `shows` is the one-line explanation the table's right-hand column
        # carries; it drifts just as easily as the prompt itself.
        assert q.shows in readme_text, (
            f"starter prompt rationale missing from README.md: {q.shows!r}"
        )
        assert q.shows in quickstart_text, (
            f"starter prompt rationale missing from docs/quickstart.md: {q.shows!r}"
        )


def test_every_exclusion_group_is_documented():
    """`--exclude-group` names come from one table; the docs must list them all.

    A group added to `exclusions.GROUPS` and not documented is a flag nobody
    can discover; a group documented and not implemented is a flag that
    errors.
    """
    from repo2graph.exclusions import GROUPS

    indexing_text = INDEXING_PATH.read_text(encoding="utf-8")
    cli_text = CLI_DOC_PATH.read_text(encoding="utf-8")

    for name, group in GROUPS.items():
        assert f"`{name}`" in indexing_text, f"exclusion group '{name}' missing from INDEXING.md"
        assert name in cli_text, f"exclusion group '{name}' missing from docs/cli.md"
        assert group.globs, f"exclusion group '{name}' has no patterns"
        assert group.representative, f"exclusion group '{name}' declares no representative paths"


def test_indexing_doc_names_determinism_tests_that_exist():
    """INDEXING.md's guarantee table points at specific tests by name.

    A renamed or deleted test would leave the documented guarantee pointing
    at nothing while still reading as though it were enforced.
    """
    indexing_text = INDEXING_PATH.read_text(encoding="utf-8")
    determinism_src = (REPO_ROOT / "tests" / "test_determinism.py").read_text(encoding="utf-8")

    # INDEXING.md also cites tests that live next door, in the index-status
    # suite, so both files are searched rather than just the obvious one.
    status_src = (REPO_ROOT / "tests" / "test_index_status.py").read_text(encoding="utf-8")
    haystack = determinism_src + status_src

    named = set(re.findall(r"`(test_[a-z0-9_]+)`", indexing_text))
    assert named, "INDEXING.md no longer names any test"
    for test_name in named:
        assert f"def {test_name}(" in haystack, (
            f"INDEXING.md names '{test_name}', which exists in neither "
            "tests/test_determinism.py nor tests/test_index_status.py"
        )



def test_git_metadata_fields_are_documented():
    """Every provenance field `index-status` surfaces is named in INDEXING.md.

    `manifest.json`'s `source_revision` is a public surface -- `--json`
    prints it verbatim -- so a field added without a doc edit is an
    undocumented API. The *shape* of the report is asserted against a real
    index in tests/test_index_status.py, not by scraping this source.
    """
    indexing_text = INDEXING_PATH.read_text(encoding="utf-8")
    for field in ("base_branch", "merge_base", "dirty_files", "commits_ahead_of_base"):
        assert field in indexing_text, f"git metadata field '{field}' missing from INDEXING.md"


def test_quickstart_names_the_doctor_checks_it_promises():
    """The quickstart's troubleshooting table routes each symptom to a named
    doctor check. A check renamed or dropped without a doc edit leaves the
    table pointing at output that never appears."""
    quickstart_text = QUICKSTART_PATH.read_text(encoding="utf-8")
    for check_name in (
        "uv / pip Availability",
        "Index Freshness",
        "Parser Coverage",
        "Ignored Paths",
        "Generated / Vendored Code",
        "MCP Client Configuration",
        "Platform & Encoding",
    ):
        assert check_name in quickstart_text, (
            f"docs/quickstart.md no longer mentions the '{check_name}' doctor check"
        )


def test_every_shipped_doc_is_listed_in_the_docs_index():
    """A doc nobody can reach from docs/README.md is a doc nobody reads.

    Caught four at once: quickstart, INDEXING, OUTPUT_SCHEMA and the
    incremental RFC were all written and none was linked.
    """
    docs_dir = REPO_ROOT / "docs"
    index_text = (docs_dir / "README.md").read_text(encoding="utf-8")

    # Dated working notes and per-run reports are deliberately unlisted: they
    # are a record of one investigation, not a page to navigate to.
    unlisted_by_design = {
        "README.md",
    }
    for path in sorted(docs_dir.glob("*.md")):
        if path.name in unlisted_by_design or re.search(r"\d{4}-\d{2}-\d{2}", path.name):
            continue
        assert path.name in index_text, (
            f"docs/{path.name} is not linked from docs/README.md; add it to the section "
            "it belongs in, or to unlisted_by_design here if it is a working note"
        )


def test_reference_and_output_schema_agree_on_the_standard_edge_fields():
    """`docs/reference.md` claims to list every field an edge can carry.

    It said `CALLS_EXTERNAL` carries no `confidence` -- true until every edge
    type gained the standard trio, and wrong afterwards. Both pages describe
    the same records, so both must name the same three fields.
    """
    from repo2graph.edgemeta import STANDARD_FIELDS

    reference = (REPO_ROOT / "docs" / "reference.md").read_text(encoding="utf-8")
    schema = (REPO_ROOT / "docs" / "OUTPUT_SCHEMA.md").read_text(encoding="utf-8")

    for field in ("method", "confidence", "evidence"):
        assert field in STANDARD_FIELDS
        assert f"`{field}`" in reference, f"docs/reference.md does not mention `{field}`"
        assert f"`{field}`" in schema, f"docs/OUTPUT_SCHEMA.md does not mention `{field}`"

    # The specific claim that went stale.
    assert "no\n  `scope_distance` or `ambiguous`" in reference or (
        "`scope_distance` or `ambiguous`" in reference
    ), "reference.md's CALLS_EXTERNAL field list no longer parses as expected"
    assert "no `scope_distance`, `confidence` or `ambiguous`" not in reference, (
        "docs/reference.md still claims CALLS_EXTERNAL carries no confidence; it does (1.0)"
    )


# --------------------------------------------------------------------------
# Integration guides make claims to an outside audience
# --------------------------------------------------------------------------

INTEGRATIONS_DIR = REPO_ROOT / "docs" / "integrations"


def test_integration_guides_quote_the_real_mcp_bounds():
    """The guides publish the argument ceilings as a contract with the reader.

    A clamp loosened in mcp.py without a doc edit leaves a guide promising a
    bound the server no longer enforces.
    """
    from repo2graph.mcp import (
        MCP_BUDGET_TOKENS,
        MCP_MAX_BUDGET_TOKENS,
        MCP_MAX_HOPS,
        MCP_MAX_K,
        MCP_MAX_NEIGHBOURS,
        TOOL_DESCRIPTIONS,
    )

    text = (INTEGRATIONS_DIR / "claude-code.md").read_text(encoding="utf-8")

    # In context, not as a bare substring. `str(MCP_MAX_HOPS) in text` passes
    # for any value whose digits appear anywhere in the prose -- "4" is in
    # "doctor.py:1075" -- so that form is not a detector at all. Same trap
    # test_languages_documented documents for its word-boundary regexes.
    # Verified as a detector by raising MCP_MAX_HOPS and watching this fail.
    for label, phrase in (
        ("MCP_MAX_K", f"`k` ≤ {MCP_MAX_K}"),
        ("MCP_MAX_HOPS", f"`hops` ≤ {MCP_MAX_HOPS}"),
        ("MCP_MAX_NEIGHBOURS", f"`limit` ≤ {MCP_MAX_NEIGHBOURS}"),
        ("MCP_MAX_BUDGET_TOKENS", f"budget ≤ {MCP_MAX_BUDGET_TOKENS:,} tokens"),
        ("MCP_BUDGET_TOKENS", f"default {MCP_BUDGET_TOKENS:,}"),
    ):
        assert phrase in text, (
            f"docs/integrations/claude-code.md does not state {label} as {phrase!r}; "
            "the guide publishes these ceilings as a contract with the reader"
        )

    # "The ten tools" is a heading in that guide; every tool must appear under
    # it. The count is spelled out in prose, so it cannot be derived from
    # TOOL_DESCRIPTIONS here -- that would compare the docs to nothing and pass
    # for any number. Bump both together when a tool is added.
    assert len(TOOL_DESCRIPTIONS) == 10, (
        f"claude-code.md says 'The ten tools' but mcp.py exposes {len(TOOL_DESCRIPTIONS)}"
    )
    assert "The ten tools" in text, (
        "docs/integrations/claude-code.md's tool-list heading no longer states the count"
    )
    for tool in TOOL_DESCRIPTIONS:
        assert tool in text, f"MCP tool '{tool}' is missing from docs/integrations/claude-code.md"


def _cli_doc_section(command: str) -> str:
    """The `## `command` ...` section of docs/cli.md, up to the next `## `` heading."""
    text = (REPO_ROOT / "docs" / "cli.md").read_text(encoding="utf-8")
    parts = re.split(r"(?m)^## (?=`)", text)
    hits = [p for p in parts if p.startswith(f"`{command}`")]
    assert hits, f"docs/cli.md has no section for {command}"
    return hits[0]


def _help_long_flags(argv: list[str]) -> set[str]:
    import contextlib
    import io

    from repo2graph.cli import main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            main([*argv, "--help"])
        except SystemExit:
            pass
    flags = set(re.findall(r"(?<![\w-])(--[a-zA-Z][\w-]*)", buf.getvalue()))
    return flags - {"--help"}


def test_cli_doc_tables_cover_every_flag_of_build_query_and_impact():
    """`build --max-call-candidates` and `--max-nodes` were in --help, not docs/cli.md."""
    for command in ("build", "query", "impact"):
        section = _cli_doc_section(command)
        missing = sorted(
            f
            for f in _help_long_flags([command])
            if not re.search(re.escape(f) + r"(?![\w-])", section)
        )
        assert missing == [], f"docs/cli.md `{command}` section lacks {missing}"
    explain = _cli_doc_section("explain")
    for flag in _help_long_flags(["explain", "retrieval"]) - {"--out", "--json"}:
        assert flag in explain, flag


def test_quickstart_build_output_shows_the_real_out_field():
    """`build` prints the resolved, absolute index path, not the `-o` argument."""
    text = QUICKSTART_PATH.read_text(encoding="utf-8")
    assert '"out": ".r2g"' not in text
    assert '"out": "/path/to/your/project/.r2g"' in text
