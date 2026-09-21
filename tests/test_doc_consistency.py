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
    """Verify supported language families in LANG_CFG are documented in README.md."""
    from repo2graph.parse import LANG_CFG

    readme_text = README_PATH.read_text(encoding="utf-8")

    # Map internal grammar keys to user-facing language names mentioned in README
    # e.g., 'python', 'javascript'/'typescript'/'tsx', 'go', 'rust', 'java', 'ruby', 'c', 'cpp', 'csharp', 'php', 'kotlin', 'swift', 'scala', 'bash'
    families = {
        "python": "Python",
        "go": "Go",
        "rust": "Rust",
        "java": "Java",
        "ruby": "Ruby",
        "c": "C",
        "cpp": "C++",
        "csharp": "C#",
        "php": "PHP",
        "kotlin": "Kotlin",
        "swift": "Swift",
        "scala": "Scala",
        "bash": "Bash",
    }

    for key, name in families.items():
        assert key in LANG_CFG, f"Language key '{key}' missing from LANG_CFG"
        assert name.lower() in readme_text.lower(), f"Language '{name}' missing from README.md"


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
