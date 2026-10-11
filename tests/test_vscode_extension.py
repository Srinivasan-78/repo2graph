"""#400: the VS Code extension in `editors/vscode/` cannot drift from the core.

It ships from this repository so its tables, the tools it calls and the flags
it passes are checked against the Python they describe on every run. The
extension's own behaviour is tested by `npm test` there (against a real
`repo2graph-mcp`); these are the cross-language checks that need Python.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXT = REPO_ROOT / "editors" / "vscode"
GENERATOR = REPO_ROOT / "scripts" / "generate_vscode_tables.py"


def _package() -> dict:
    return json.loads((EXT / "package.json").read_text(encoding="utf8"))


def _core_sources() -> str:
    return "\n".join(
        p.read_text(encoding="utf8")
        for p in sorted((EXT / "src").rglob("*.ts"))
        if "test" not in p.parts
    )


def test_generated_tables_match_the_core_ones():
    """Adding a language, node type or edge type fails here until regenerated."""
    generated = subprocess.run(
        [sys.executable, str(GENERATOR)], capture_output=True, text=True, check=True, cwd=REPO_ROOT
    ).stdout
    committed = (EXT / "src" / "core" / "generated.ts").read_text(encoding="utf8")
    assert committed == generated, (
        "editors/vscode/src/core/generated.ts is stale. Regenerate it:\n"
        "  python scripts/generate_vscode_tables.py --write"
    )


def test_the_extension_calls_only_tools_the_server_has():
    from repo2graph.mcp.schemas import TOOL_DESCRIPTIONS, TOOL_SCHEMAS

    called = set(re.findall(r'"(repo_[a-z_]+)"', _core_sources()))
    assert called, "found no tool calls in the extension"
    assert called <= set(TOOL_DESCRIPTIONS), called - set(TOOL_DESCRIPTIONS)
    # The arguments the cursor path depends on are advertised.
    assert {"path", "line"} <= set(TOOL_SCHEMAS["repo_find_symbol"]["properties"])
    assert {"format", "cursor", "limit"} <= set(TOOL_SCHEMAS["repo_neighbours"]["properties"])


def _help(argv: list[str]) -> str:
    from repo2graph.cli import main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            main([*argv, "--help"])
        except SystemExit:
            pass
    return buf.getvalue()


def test_the_flags_the_extension_passes_exist():
    status_src = (EXT / "src" / "core" / "status.ts").read_text(encoding="utf8")
    server_src = (REPO_ROOT / "repo2graph" / "mcp" / "server.py").read_text(encoding="utf8")
    for flag in ("--out", "--no-auto-build", "--staleness-ttl"):
        assert f'"{flag}"' in status_src and f'"{flag}"' in server_src, flag
    for command, flags in (
        ("build", ("--incremental",)),
        ("index-status", ("--json", "--repo")),
        ("map", ()),
    ):
        assert f'"{command}"' in status_src, command
        text = _help([command])
        for flag in flags:
            assert flag in text, (command, flag)
    # The default buildArgs are real build flags.
    default = _package()["contributes"]["configuration"]["properties"]["repo2graph.buildArgs"][
        "default"
    ]
    for token in default:
        if token.startswith("--"):
            assert token in _help(["build"]), token


def test_the_package_has_no_runtime_dependencies_and_pins_its_tools():
    pkg = _package()
    assert pkg.get("private") is True, "the extension is not an npm package"
    assert not pkg.get("dependencies"), "the extension ships with no runtime dependencies"
    dev = pkg["devDependencies"]
    assert set(dev) == {"@types/node", "@types/vscode", "typescript"}
    assert all(re.fullmatch(r"\d+\.\d+\.\d+", v) for v in dev.values()), dev
    assert pkg["engines"]["vscode"].lstrip("^") == dev["@types/vscode"]
    lock = json.loads((EXT / "package-lock.json").read_text(encoding="utf8"))
    assert lock["packages"][""]["devDependencies"] == dev
    ignored = (EXT / ".gitignore").read_text(encoding="utf8").split()
    assert "node_modules/" in ignored and "out/" in ignored


def test_executables_cannot_be_set_by_a_workspace():
    """A cloned repository's .vscode/settings.json must not choose what runs."""
    props = _package()["contributes"]["configuration"]["properties"]
    for key in ("repo2graph.executable", "repo2graph.mcpExecutable", "repo2graph.buildArgs"):
        assert props[key]["scope"] == "machine", key
    assert _package()["capabilities"]["untrustedWorkspaces"]["supported"] is False


def test_the_readme_documents_every_command_and_setting():
    readme = (EXT / "README.md").read_text(encoding="utf8")
    contributes = _package()["contributes"]
    for command in contributes["commands"]:
        assert command["title"] in readme, command["command"]
    for key in contributes["configuration"]["properties"]:
        assert f"`{key}`" in readme, key
