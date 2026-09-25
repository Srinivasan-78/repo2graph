"""Tests for the npx launcher at `npm/bin/repo2graph-mcp.js` (Issue #399).

Two layers, mirroring `tests/test_prod_igy.py`'s house style:

- Static assertions over the source text, which run everywhere with no Node.js
  in the pytest environment.
- A behavioural layer that runs the real script under `node`, skipped when
  `node` is not on PATH. The GitHub-hosted runners this project's CI matrix
  uses all ship Node, so this layer does run in CI on all three platforms.

The behavioural layer is the detector for the bug it was written for: Node
>= 18.20 / 20.12 refuses to spawn a `.cmd`/`.bat` without `shell: true` and
fails with EINVAL (the CVE-2024-27980 fix). The launcher's PATH walk accepts
`.CMD`/`.BAT` from PATHEXT, so a batch shim on PATH -- how chocolatey and
several version managers install `uvx`/`pipx` -- was detected and then failed
to launch.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER_PATH = REPO_ROOT / "npm" / "bin" / "repo2graph-mcp.js"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")


def _source() -> str:
    return LAUNCHER_PATH.read_text(encoding="utf-8")


def test_launcher_exists_and_is_executable_js():
    assert LAUNCHER_PATH.is_file(), f"launcher missing: {LAUNCHER_PATH}"
    src = _source()
    assert src.startswith("#!/usr/bin/env node"), "launcher needs a node shebang"
    # REUSE-IgnoreStart
    # `reuse lint` reads to end of line after the tag, so the bare literal
    # would be parsed as the expression `MIT" in src` and fail the provenance
    # gate. These markers are REUSE's own documented escape for a file that
    # mentions the tag without carrying it.
    assert "SPDX-License-Identifier: MIT" in src
    # REUSE-IgnoreEnd


def test_launcher_spawns_batch_shims_through_a_shell():
    """Static guard on the EINVAL fix, so a future edit that drops `shell` fails here.

    Asserts the source still pairs a `.cmd`/`.bat` check with a `shell` option
    -- the behavioural test below is the real detector, but this one also runs
    on a machine with no Node.js at all.
    """
    src = _source()
    assert ".cmd" in src and ".bat" in src, (
        "launcher no longer special-cases Windows batch shims; "
        "spawnSync cannot run them without shell: true"
    )
    assert "shell" in src, "launcher must set shell: true for a .cmd/.bat shim"


def _run_launcher(tmp_path: Path, shim_dir: Path, args: list[str]) -> subprocess.CompletedProcess:
    """Run the launcher with PATH pointing only at `shim_dir`.

    Bytes, never `text=True`: the same decoding discipline AGENTS.md requires
    for git subprocesses applies to any non-ASCII a shim might echo on a
    cp1252 Windows locale.
    """
    env = dict(os.environ)
    env["PATH"] = str(shim_dir)
    env["Path"] = str(shim_dir)
    if sys.platform == "win32":
        env["PATHEXT"] = ".COM;.EXE;.BAT;.CMD"
    return subprocess.run(
        [NODE, str(LAUNCHER_PATH), *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=120,
    )


def _write_uvx_shim(shim_dir: Path) -> None:
    """A fake `uvx` on PATH that echoes its argv and exits 0.

    A `.cmd` on Windows -- which is precisely the shape that used to fail --
    and a plain shell script elsewhere.
    """
    shim_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        shim = shim_dir / "uvx.cmd"
        shim.write_bytes(b"@echo off\r\necho UVX-RAN %*\r\nexit /b 0\r\n")
    else:
        shim = shim_dir / "uvx"
        shim.write_bytes(b'#!/bin/sh\necho "UVX-RAN $@"\nexit 0\n')
        shim.chmod(0o755)


@needs_node
def test_launcher_runs_a_uvx_shim_on_path(tmp_path):
    """The launcher finds `uvx` and hands off to it, exiting with its status.

    On Windows the shim is a `.cmd`, so this fails with EINVAL and a non-zero
    exit if `run()` ever goes back to spawning a resolved path directly.
    """
    shim_dir = tmp_path / "bin"
    _write_uvx_shim(shim_dir)

    proc = _run_launcher(tmp_path, shim_dir, ["."])
    stdout = proc.stdout.decode("utf8", "surrogateescape")
    stderr = proc.stderr.decode("utf8", "surrogateescape")

    assert "EINVAL" not in stderr, f"launcher could not spawn the shim: {stderr}"
    assert proc.returncode == 0, f"expected the shim's exit 0, got {proc.returncode}: {stderr}"
    assert "UVX-RAN" in stdout, f"uvx shim was never reached: {stdout!r} / {stderr!r}"
    # The package spec and console script are forwarded, plus our own "." arg.
    assert "repo2graph[mcp]" in stdout
    assert "repo2graph-mcp" in stdout


@needs_node
def test_launcher_propagates_the_child_exit_status(tmp_path):
    """A non-zero exit from the underlying server is the launcher's own exit code."""
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        (shim_dir / "uvx.cmd").write_bytes(b"@echo off\r\nexit /b 7\r\n")
    else:
        shim = shim_dir / "uvx"
        shim.write_bytes(b"#!/bin/sh\nexit 7\n")
        shim.chmod(0o755)

    proc = _run_launcher(tmp_path, shim_dir, ["."])
    assert proc.returncode == 7, (
        f"launcher swallowed the child's exit status: {proc.returncode} "
        f"({proc.stderr.decode('utf8', 'surrogateescape')})"
    )


@needs_node
def test_launcher_forwards_an_argument_containing_spaces(tmp_path):
    """A repository path with a space survives the Windows shell hand-off.

    `shell: true` re-parses the command line, so an unquoted argument would
    arrive at the shim split in two. This is the case quoting exists for.
    """
    shim_dir = tmp_path / "bin"
    _write_uvx_shim(shim_dir)
    target = tmp_path / "my repo"
    target.mkdir()

    proc = _run_launcher(tmp_path, shim_dir, [str(target)])
    stdout = proc.stdout.decode("utf8", "surrogateescape")

    assert proc.returncode == 0, proc.stderr.decode("utf8", "surrogateescape")
    assert "my repo" in stdout, f"path with a space was mangled: {stdout!r}"


@needs_node
def test_launcher_explains_itself_when_no_python_launcher_is_installed(tmp_path):
    """With an empty PATH the launcher must not install anything -- it explains and exits 1."""
    shim_dir = tmp_path / "empty"
    shim_dir.mkdir()

    proc = _run_launcher(tmp_path, shim_dir, ["."])
    stderr = proc.stderr.decode("utf8", "surrogateescape")

    assert proc.returncode == 1
    assert "no working Python launcher found" in stderr
    # The install routes it points at, and nothing that silently installs one.
    assert "uv" in stderr and "pipx" in stderr
    assert 'pip install "repo2graph[mcp]"' in stderr
    # Diagnostics go to stderr only: stdout is the MCP JSON-RPC stream.
    assert proc.stdout == b"", f"launcher wrote to stdout: {proc.stdout!r}"
