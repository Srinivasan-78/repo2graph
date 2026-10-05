# SPDX-FileCopyrightText: 2026 Srinivasan Vijayaraghavan
#
# SPDX-License-Identifier: MIT

"""Writing an index must never delete something that is not an index.

`dump_all`'s directory swap renames the target aside and then `rmtree`s it.
`build` hardens its `-o` with `validate_outdir` first, but the *implicit*
builds reached the same swap without it, so `repo2graph rag . -o .` deleted the
working tree. There were three of them; `impact`'s went with that command, and
`rag <src>`'s and the MCP server's are covered below.

Separately, `repo2graph-mcp` with no arguments inferred the repository from the
process's working directory. Claude Desktop and Cursor do not inherit a project
cwd, so a pathless MCP config indexed whatever directory the client launched in
-- a home folder, a desktop, or a OneDrive root.
"""

from pathlib import Path

import pytest
from repo2graph.cli import main

from conftest import write_simple_repo


def _mcp():
    import repo2graph.mcp as mcp

    return mcp


# --------------------------------------------------------------------------
# the implicit builds validate their output directory
# --------------------------------------------------------------------------


def test_rag_refuses_to_auto_build_over_the_repo_itself(tmp_path):
    """`rag . -o .` renamed the working tree aside and deleted it."""
    repo = write_simple_repo(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main(["rag", str(repo), "hello", "-o", str(repo)])
    msg = str(exc.value)
    assert "repository root" in msg or "dedicated" in msg
    # The point of the test: the source survived.
    assert (repo / "app.py").is_file()


def test_rag_refuses_to_auto_build_over_a_foreign_directory(tmp_path):
    repo = write_simple_repo(tmp_path)
    foreign = tmp_path / "important"
    foreign.mkdir()
    (foreign / "notes.txt").write_text("do not delete", encoding="utf8")
    with pytest.raises(SystemExit) as exc:
        main(["rag", str(repo), "hello", "-o", str(foreign)])
    assert "non-repo2graph files" in str(exc.value) or "dedicated" in str(exc.value)
    assert (foreign / "notes.txt").read_text(encoding="utf8") == "do not delete"


def test_mcp_refuses_to_auto_build_over_a_foreign_directory(tmp_path):
    """The server's auto-build is the third implicit build and the last one left.

    It raises `ValueError`, not `SystemExit`: this runs inside a live stdio
    server, which must answer the tool call rather than exit the process. A
    `SystemExit` here would take the client's whole session down.
    """
    repo = write_simple_repo(tmp_path)
    foreign = tmp_path / "docs"
    foreign.mkdir()
    (foreign / "readme.txt").write_text("keep", encoding="utf8")
    with pytest.raises(ValueError):
        _mcp()._build_index(repo, foreign)
    assert (foreign / "readme.txt").read_text(encoding="utf8") == "keep"


def test_mcp_auto_build_into_a_dedicated_directory_still_works(tmp_path):
    """The guard must not break the ordinary path."""
    repo = write_simple_repo(tmp_path)
    out = tmp_path / ".r2g"
    _mcp()._build_index(repo, out)
    assert (out / "agent" / "chunks.jsonl").is_file()


def test_rag_auto_build_into_a_dedicated_directory_still_works(tmp_path):
    """The guard must not break the ordinary path."""
    repo = write_simple_repo(tmp_path)
    out = tmp_path / ".r2g"
    assert main(["rag", str(repo), "hello", "-o", str(out)]) == 0
    assert (out / "agent" / "chunks.jsonl").is_file()


def test_rag_auto_build_can_replace_an_index_it_wrote(tmp_path):
    """Replacing a real index is the whole point; only foreign data is refused."""
    repo = write_simple_repo(tmp_path)
    out = tmp_path / ".r2g"
    assert main(["build", str(repo), "-o", str(out)]) == 0
    assert (out / "agent" / "chunks.jsonl").is_file()
    # A second auto-build over the same index directory is legitimate.
    assert main(["rag", str(repo), "hello", "-o", str(out)]) == 0


# --------------------------------------------------------------------------
# MCP must not infer a repository from an arbitrary working directory
# --------------------------------------------------------------------------


def test_pathless_mcp_refuses_a_cwd_that_is_not_a_project_root(tmp_path, monkeypatch):
    """A home folder or a drive root is not a repository."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Pictures").mkdir()
    with pytest.raises(SystemExit) as exc:
        _mcp().resolve_paths()
    msg = str(exc.value)
    assert "does not look like a project root" in msg
    assert "absolute path" in msg


def test_pathless_mcp_accepts_a_cwd_that_is_a_project_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    out, repo = _mcp().resolve_paths()
    assert repo == Path(".r2g").parent
    assert out == Path(".r2g")


@pytest.mark.parametrize(
    "marker", ["pyproject.toml", "package.json", "go.mod", "Cargo.toml", "CMakeLists.txt"]
)
def test_pathless_mcp_accepts_any_project_marker(tmp_path, monkeypatch, marker):
    monkeypatch.chdir(tmp_path)
    (tmp_path / marker).write_text("", encoding="utf8")
    out, repo = _mcp().resolve_paths()
    assert repo is not None


def test_pathless_mcp_refuses_the_home_directory(tmp_path, monkeypatch):
    """Even with a marker present, $HOME itself is never the intended target."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with pytest.raises(SystemExit) as exc:
        _mcp().resolve_paths()
    assert "home directory" in str(exc.value)


def test_an_explicit_out_is_still_trusted(tmp_path):
    """`--out <repo>/.r2g` names the parent deliberately; the guard is for cwd only."""
    out, repo = _mcp().resolve_paths(out=tmp_path / ".r2g")
    assert out == tmp_path / ".r2g"
    assert repo == tmp_path


def test_an_explicit_repo_is_still_trusted(tmp_path):
    out, repo = _mcp().resolve_paths(repo=str(tmp_path))
    assert repo == tmp_path
