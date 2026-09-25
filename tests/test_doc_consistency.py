"""Automated documentation and interface consistency tests (Issue 60 / #319).

Prevents drift between code and documentation:
- CLI subcommands in repo2graph.cli vs README and docs/cli.md
- Parser language support in LANG_CFG vs README
- Action inputs and outputs in action.yml vs docs/github-action.md
- MCP registered tools in repo2graph.mcp vs docs/mcp.md
- CITATION.cff's version vs pyproject.toml's (Issue #404)
- npm/package.json's version vs pyproject.toml's (Issue #399)
- BUILD_STATE.md living at docs/, not the repo root (Issue #403)
- docs/deployment-security.md's numeric claims vs the HTTP/auth transport source (Issue #263)
"""

import json
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
README_PATH = REPO_ROOT / "README.md"
CLI_DOC_PATH = REPO_ROOT / "docs" / "cli.md"
ACTION_YML_PATH = REPO_ROOT / "action.yml"
ACTION_DOC_PATH = REPO_ROOT / "docs" / "github-action.md"
MCP_DOC_PATH = REPO_ROOT / "docs" / "mcp.md"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
CITATION_PATH = REPO_ROOT / "CITATION.cff"
NPM_PACKAGE_PATH = REPO_ROOT / "npm" / "package.json"
THREAT_MODEL_PATH = REPO_ROOT / "docs" / "THREAT_MODEL.md"
# The operator-facing companion: docs/THREAT_MODEL.md enumerates assets, trust
# boundaries and attacks; this one issues a supported/not-recommended verdict
# per deployment shape. The per-mode topics and the transport constants below
# are the second document's job, so they are checked against it.
DEPLOYMENT_SECURITY_PATH = REPO_ROOT / "docs" / "deployment-security.md"


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


def test_build_state_lives_in_docs_not_repo_root():
    """Issue #403: BUILD_STATE.md must not sit at the repository root.

    `docs/BACKLOG.md` documents `docs/BUILD_STATE.md` as the current build-app run's location
    (`docs/BUILD_STATE.graphrag-2026-09.md` is the archived one from a past run) -- this test
    would catch a future change that puts a new BUILD_STATE.md back at the root.
    """
    assert not (REPO_ROOT / "BUILD_STATE.md").exists(), (
        "BUILD_STATE.md is back at the repo root -- it belongs at docs/BUILD_STATE.md (Issue #403)"
    )
    backlog_text = (REPO_ROOT / "docs" / "BACKLOG.md").read_text(encoding="utf-8")
    assert "docs/BUILD_STATE.md" in backlog_text


def test_threat_model_covers_every_deployment_mode():
    """Issue #263: docs/deployment-security.md must exist and name every required mode/topic.

    A loose substring check rather than a hand-derived membership assertion (AGENTS.md's usual
    rule for *behavioural* tests) -- this is a documentation-completeness check, so the thing
    being pinned is "the required topic is discussed somewhere in the file," not a value the
    code under test computes.
    """
    assert THREAT_MODEL_PATH.exists(), "docs/THREAT_MODEL.md is missing"
    assert DEPLOYMENT_SECURITY_PATH.exists(), "docs/deployment-security.md is missing"
    text = DEPLOYMENT_SECURITY_PATH.read_text(encoding="utf-8")

    required_topics = [
        "Trusted-local CLI",
        "CI indexing",
        "Stdio MCP",
        "HTTP MCP on loopback",
        "reverse proxy",
        "Multi-tenant",
        "--answer",
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OLLAMA_HOST",
        "exclude_secrets",
        "Authenticated vs. authorized",
        "ssl_certificate",  # the worked reverse-proxy example is a real TLS config, not prose only
        "rotation",
    ]
    for topic in required_topics:
        assert topic in text, f"docs/deployment-security.md is missing required topic: {topic!r}"


def test_threat_model_numeric_claims_match_the_http_and_auth_source():
    """docs/deployment-security.md cites specific constants from http_server.py/auth.py in prose --
    this pins those constants so a future change to either module without a doc edit fails
    here instead of leaving the threat model quietly wrong.
    """
    from repo2graph import auth, http_server

    assert http_server.MAX_BODY_BYTES == 1 << 20
    assert http_server.REQUEST_TIMEOUT_SECONDS == 30.0
    assert auth.DEFAULT_MIN_REFRESH_INTERVAL == 5.0
    assert set(auth.ALGORITHMS) == {"RS256", "RS384", "RS512"}
