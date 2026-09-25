"""Automated documentation and interface consistency tests (Issue 60 / #319).

Prevents drift between code and documentation:
- CLI subcommands in repo2graph.cli vs README and docs/cli.md
- Parser language support in LANG_CFG vs README
- Action inputs and outputs in action.yml vs docs/github-action.md
- MCP registered tools in repo2graph.mcp vs docs/mcp.md
"""

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
README_PATH = REPO_ROOT / "README.md"
CLI_DOC_PATH = REPO_ROOT / "docs" / "cli.md"
ACTION_YML_PATH = REPO_ROOT / "action.yml"
ACTION_DOC_PATH = REPO_ROOT / "docs" / "github-action.md"
MCP_DOC_PATH = REPO_ROOT / "docs" / "mcp.md"

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
