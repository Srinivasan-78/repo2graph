"""Automated documentation and interface consistency tests (Issue 60 / #319).

Prevents drift between code and documentation:
- CLI subcommands in repo2graph.cli vs README and docs/cli.md
- Parser language support in LANG_CFG vs README
- Action inputs and outputs in action.yml vs docs/cli.md
- MCP registered tools in repo2graph.mcp vs docs/mcp.md
- CITATION.cff's version vs pyproject.toml's (Issue #404)
- npm/package.json's version vs pyproject.toml's (Issue #399)
"""

import json
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
README_PATH = REPO_ROOT / "README.md"
CLI_DOC_PATH = REPO_ROOT / "docs" / "cli.md"
ARCHITECTURE_PATH = REPO_ROOT / "docs" / "architecture.md"

ACTION_YML_PATH = REPO_ROOT / "action.yml"
ACTION_DOC_PATH = REPO_ROOT / "docs" / "cli.md"
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


def test_language_scorecard_matches_the_generator():
    """The scorecard table in docs/architecture.md is what the generator emits today.

    `scripts/generate_language_scorecard.py` shipped with no consumer: nothing in
    docs/, README or CI referenced it, and its own docstring pointed at a
    `docs/LANGUAGE_SCORECARD.json` that was never committed. A generator whose
    output nobody reads stops being run, and then stops being right -- it scores
    `LANG_CFG` by introspection, so every language added or extended moves these
    numbers silently.

    Pinning the rendered table means adding a language fails here until the table
    is regenerated, which is the step that would otherwise be forgotten.
    """
    import subprocess
    import sys

    generated = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "generate_language_scorecard.py")],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    ).stdout.strip()

    doc = ARCHITECTURE_PATH.read_text(encoding="utf-8")
    begin, end = (
        "<!-- BEGIN GENERATED: language-scorecard -->",
        "<!-- END GENERATED: language-scorecard -->",
    )
    assert begin in doc and end in doc, (
        f"{ARCHITECTURE_PATH.name} lost the generated-scorecard markers"
    )
    embedded = doc.split(begin, 1)[1].split(end, 1)[0].strip()

    assert embedded == generated, (
        "docs/architecture.md's language scorecard is stale. Regenerate it:\n"
        "  python scripts/generate_language_scorecard.py\n"
        "and paste the table between the BEGIN/END GENERATED markers."
    )


def test_action_inputs_and_outputs_documented():
    """Verify every input and output in action.yml is documented in docs/cli.md."""
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
            f"Action input '{input_name}' is not documented in docs/cli.md"
        )

    for output_name in outputs:
        assert f"`{output_name}`" in doc_text, (
            f"Action output '{output_name}' is not documented in docs/cli.md"
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

    README.md renders them and `repo2graph demo` runs them, so a prompt edited
    in one place and not the other would leave the docs telling users to run
    something the demo never exercises. Both the template form (what a reader
    copies onto their own repo) and the one-line rationale beside it are pinned.
    """
    from repo2graph.demo import STARTER_QUESTIONS

    readme_text = README_PATH.read_text(encoding="utf-8")

    assert len(STARTER_QUESTIONS) == 5
    for q in STARTER_QUESTIONS:
        assert f"`{q.template}`" in readme_text, (
            f"starter prompt missing from README.md: {q.template!r}"
        )
        assert q.shows in readme_text, (
            f"starter prompt rationale missing from README.md: {q.shows!r}"
        )


def test_every_exclusion_group_is_documented():
    """`--exclude-group` names come from one table; the docs must list them all.

    A group added to `exclusions.GROUPS` and not documented is a flag nobody
    can discover; a group documented and not implemented is a flag that
    errors.
    """
    from repo2graph.exclusions import GROUPS

    indexing_text = ARCHITECTURE_PATH.read_text(encoding="utf-8")
    cli_text = CLI_DOC_PATH.read_text(encoding="utf-8")

    for name, group in GROUPS.items():
        assert f"`{name}`" in indexing_text, (
            f"exclusion group '{name}' missing from architecture.md"
        )
        assert name in cli_text, f"exclusion group '{name}' missing from docs/cli.md"
        assert group.globs, f"exclusion group '{name}' has no patterns"
        assert group.representative, f"exclusion group '{name}' declares no representative paths"


def test_indexing_doc_names_determinism_tests_that_exist():
    """architecture.md's guarantee table points at specific tests by name.

    A renamed or deleted test would leave the documented guarantee pointing
    at nothing while still reading as though it were enforced.
    """
    indexing_text = ARCHITECTURE_PATH.read_text(encoding="utf-8")
    determinism_src = (REPO_ROOT / "tests" / "test_determinism.py").read_text(encoding="utf-8")

    # architecture.md also cites tests that live next door, in the index-status
    # suite, so both files are searched rather than just the obvious one.
    status_src = (REPO_ROOT / "tests" / "test_index_status.py").read_text(encoding="utf-8")
    haystack = determinism_src + status_src

    named = set(re.findall(r"`(test_[a-z0-9_]+)`", indexing_text))
    assert named, "architecture.md no longer names any test"
    for test_name in named:
        assert f"def {test_name}(" in haystack, (
            f"architecture.md names '{test_name}', which exists in neither "
            "tests/test_determinism.py nor tests/test_index_status.py"
        )


def test_git_metadata_fields_are_documented():
    """Every provenance field `index-status` surfaces is named in architecture.md.

    `manifest.json`'s `source_revision` is a public surface -- `--json`
    prints it verbatim -- so a field added without a doc edit is an
    undocumented API. The *shape* of the report is asserted against a real
    index in tests/test_index_status.py, not by scraping this source.
    """
    indexing_text = ARCHITECTURE_PATH.read_text(encoding="utf-8")
    for field in ("base_branch", "merge_base", "dirty_files", "commits_ahead_of_base"):
        assert field in indexing_text, f"git metadata field '{field}' missing from architecture.md"


def test_cli_doc_names_every_doctor_check_that_exists():
    """docs/cli.md lists the doctor checks by the name doctor actually prints.

    A check renamed or dropped without a doc edit leaves the list pointing at
    output that never appears; a check added without one leaves it undocumented.
    Asserted against the report a real run produces, not against the source.
    """
    from repo2graph.doctor import run_doctor

    cli_text = CLI_DOC_PATH.read_text(encoding="utf-8")
    names = {c.name for c in run_doctor(REPO_ROOT).checks}
    assert names, "doctor ran no checks"
    for check_name in sorted(names):
        assert check_name in cli_text, (
            f"docs/cli.md does not mention the '{check_name}' doctor check"
        )


def test_the_shipped_doc_set_is_the_documented_one():
    """`docs/` is five reference pages, and adding a sixth is a decision.

    The tree previously grew to 29 documents -- compliance theater, speculative
    RFCs, roadmap trackers and three overlapping architecture pages -- with an
    index file to make them findable. Pinning the set means a new page has to be
    added here deliberately, which is where the "should this be a section of an
    existing page" question gets asked.

    `comparison.md` is the deliberate fifth. The consolidation dropped it and
    repointed README's "how it compares with Serena, Aider, ..." link at
    architecture.md, which names none of those tools -- the link resolved, so
    nothing caught it, and the promise went unmet. It is not a section of
    architecture.md (that page describes this system, not other people's) and it
    is too long for a 167-line landing README, which is why it is its own page.
    """
    docs_dir = REPO_ROOT / "docs"
    shipped = {p.name for p in docs_dir.glob("*.md")}
    assert shipped == {
        "architecture.md",
        "cli.md",
        "comparison.md",
        "mcp.md",
        "python-api.md",
    }, f"docs/ no longer holds the documented set: {sorted(shipped)}"


def test_architecture_doc_names_the_standard_edge_fields():
    """docs/architecture.md lists every field an edge can carry.

    It used to say `CALLS_EXTERNAL` carries no `confidence` -- true until every
    edge type gained the standard trio, and wrong afterwards. The edge-metadata
    section and `edgemeta.STANDARD_FIELDS` describe the same records.
    """
    from repo2graph.edgemeta import STANDARD_FIELDS

    schema = ARCHITECTURE_PATH.read_text(encoding="utf-8")
    for field in ("method", "confidence", "evidence"):
        assert field in STANDARD_FIELDS
        assert f"`{field}`" in schema, f"docs/architecture.md does not mention `{field}`"

    assert "no `scope_distance`, `confidence` or `ambiguous`" not in schema, (
        "docs/architecture.md still claims CALLS_EXTERNAL carries no confidence; it does (1.0)"
    )


# --------------------------------------------------------------------------
# Integration guides make claims to an outside audience
# --------------------------------------------------------------------------


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

    text = MCP_DOC_PATH.read_text(encoding="utf-8")

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
            f"docs/mcp.md does not state {label} as {phrase!r}; "
            "the guide publishes these ceilings as a contract with the reader"
        )

    # "The ten tools" is a heading in that guide; every tool must appear under
    # it. The count is spelled out in prose, so it cannot be derived from
    # TOOL_DESCRIPTIONS here -- that would compare the docs to nothing and pass
    # for any number. Bump both together when a tool is added.
    assert len(TOOL_DESCRIPTIONS) == 10, (
        f"mcp.md says 'The ten tools' but mcp.py exposes {len(TOOL_DESCRIPTIONS)}"
    )
    assert "The ten tools" in text, "docs/mcp.md's tool-list heading no longer states the count"
    for tool in TOOL_DESCRIPTIONS:
        assert tool in text, f"MCP tool '{tool}' is missing from docs/mcp.md"


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


def test_cli_doc_tables_cover_every_flag_of_the_retrieval_commands():
    """`build --max-call-candidates` and `--max-nodes` were in --help, not docs/cli.md.

    `rag` was added to this list after its table was found to be missing
    `--secret-policy`, `--secret-keyword` and `--secret-dir`: the three commands
    originally covered here were the ones that had drifted once, and `rag` then
    drifted silently because nothing checked it.
    """
    for command in ("build", "query", "rag", "impact"):
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


def test_build_reports_the_resolved_absolute_out_path(tmp_path, capsys):
    """`build` reports the resolved index path, not the `-o` argument verbatim.

    A relative `out` in the report sends a reader looking in whichever directory
    they happen to be in. This was previously guarded only by a sample pasted
    into the quickstart, so it broke silently when that page moved; asserting it
    against the real report keeps it pinned wherever the docs go.
    """
    import json

    from repo2graph.cli import main

    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "mod.py").write_text(
        "TITLE = 'module-level residue so this file carries a chunk'\n\n\n"
        "def go():\n    return TITLE\n",
        encoding="utf-8",
    )
    assert main(["build", str(repo), "-o", ".r2g"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert Path(report["out"]).is_absolute(), report["out"]
    assert report["out"] != ".r2g"


# Every subcommand with its own flags, plus the two `explain` leaves. Kept
# explicit rather than scraped from the subparser map so that a *new* command
# has to be added here deliberately -- a scrape would silently cover nothing
# when the registration shape changes.
_CLI_COMMANDS = (
    "build",
    "query",
    "rag",
    "impact",
    "github",
    "embed",
    "map",
    "stats",
    "explain-path",
    "doctor",
    "bug-report",
    "index-status",
    "demo",
    "completion",
    "version",
)
_EXPLAIN_LEAVES = (("explain", "edge"), ("explain", "node"), ("explain", "retrieval"))


def _all_real_long_flags() -> set[str]:
    """Every `--flag` the shipped CLI or the MCP server actually accepts."""
    flags = _help_long_flags([])
    for command in _CLI_COMMANDS:
        flags |= _help_long_flags([command])
    for argv in _EXPLAIN_LEAVES:
        flags |= _help_long_flags(list(argv))
    # The MCP server parses its own argv in mcp/server.py rather than through
    # repo2graph.cli, so --help cannot reach it from here.
    server_src = (REPO_ROOT / "repo2graph" / "mcp" / "server.py").read_text(encoding="utf-8")
    flags |= set(re.findall(r'"(--[a-z][\w-]*)"', server_src))
    return flags


def test_unreleased_changelog_does_not_advertise_flags_that_do_not_exist():
    """A removed subsystem left its flags behind in `[Unreleased]` (`647e76f3`).

    The HTTP transport and the OIDC/JWT auth engine were deleted, but the
    section kept advertising `--http-insecure-ok`, `--trust-proxy`,
    `--rate-limit-requests` and six more. That section is not just prose:
    `.github/workflows/publish.yml` reads it and uses it as the GitHub Release
    body, so the next release would have shipped notes describing flags that
    fail as unknown arguments.

    The `### Removed` subsection is exempt by design -- naming a flag that no
    longer exists is exactly its job.
    """
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [Unreleased]" in text
    unreleased = text.split("## [Unreleased]", 1)[1].split("\n## [", 1)[0]
    sections = re.split(r"(?m)^### ", unreleased)
    body = "\n".join(s for s in sections if not s.lower().startswith("removed"))
    # Only backticked tokens: prose such as "the --foo style" is not a claim
    # that the flag exists, and `--` shows up inside URLs and diff output.
    mentioned = set(re.findall(r"`(--[a-zA-Z][\w-]*)", body))
    stale = sorted(mentioned - _all_real_long_flags())
    assert stale == [], (
        f"CHANGELOG [Unreleased] advertises flags that no longer exist: {stale}. "
        "Move them to ### Removed with migration notes, or delete the entry."
    )
