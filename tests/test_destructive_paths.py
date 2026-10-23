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

import errno
import os
from pathlib import Path

import pytest
from conftest import write_simple_repo

from repo2graph.cli import main


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
    _out, repo = _mcp().resolve_paths()
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
    _out, repo = _mcp().resolve_paths(repo=str(tmp_path))
    assert repo == tmp_path


def test_a_build_survives_a_read_only_parent_of_the_output_directory(tmp_path, monkeypatch):
    """The documented read-only container run must actually produce an index.

    `.github/SECURITY.md` describes running the image with a read-only rootfs
    and only `-o /out` mounted writable. Both halves of the write path put
    their scratch directory *beside* the target -- `BuildLock`'s
    `.out.r2glock` and `dump_all`'s `.out.staging.<pid>` -- so with `/`
    read-only the build took the lock failure first and, once that was fixed,
    died at the staging `mkdir` after doing all of its work. Nothing caught it
    because the container CI job is newer than the bug.

    Only the sibling staging `mkdir` is blocked, rather than every write under
    the parent: patching `builtins.open` globally also reaches the parse
    workers and pytest's own temp handling, which on Windows surfaced as an
    unrelated `PermissionError`. `BuildLock`'s own fallback is covered
    directly by `tests/test_store.py`.
    """
    real_mkdir = os.mkdir

    def fake_mkdir(path, *args, **kwargs):
        # Only the *sibling* staging directory, `.out.staging.<pid>.<hex>`.
        # The in-target fallback is `.staging.<pid>.<hex>` -- matching on
        # `.staging.` alone blocks that too, and the build has nowhere left.
        if Path(str(path)).name.startswith(".out.staging."):
            raise OSError(errno.EROFS, "Read-only file system")
        return real_mkdir(path, *args, **kwargs)

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def f():\n    return 1\n", encoding="utf8")
    out = tmp_path / "out"
    out.mkdir()

    monkeypatch.setattr(os, "mkdir", fake_mkdir)
    rc = main(["build", str(repo), "-o", str(out), "--formats", "jsonl"])

    assert rc == 0
    assert (out / "agent" / "nodes.jsonl").is_file()
    assert (out / "agent" / "chunks.jsonl").is_file()
    # The in-target staging directory is scratch, not an artifact.
    assert [q.name for q in out.iterdir() if ".staging" in q.name] == []


def test_the_in_target_merge_swaps_agent_last(tmp_path):
    """`agent/` must be the final rename, so readers never see a half-new index.

    `_merge_staging_into` is the read-only-parent fallback and cannot swap the
    whole directory in one step -- renaming needs write permission on the parent
    of both names. Per-entry replacement is the best available, so the *order*
    carries the guarantee: everything `query`, `rag` and the MCP server read
    lives under `agent/` (including `manifest.json`, and so the `build_id`), and
    that subtree arrives in a single rename. A reader therefore sees the
    previous index, whole, right up to the instant the new one replaces it.
    """
    from repo2graph.export import AGENT_DIR, _merge_staging_into

    target = tmp_path / "out"
    staging = target / ".staging.1.abc"
    staging.mkdir(parents=True)
    for name in ("human", AGENT_DIR, "local.json", ".gitignore"):
        p = staging / name
        if name.endswith(".json") or name.startswith("."):
            p.write_text("{}", encoding="utf8")
        else:
            p.mkdir()
            (p / "f.jsonl").write_text("{}\n", encoding="utf8")

    # `_merge_staging_into` goes through `export._rename` (which retries a
    # Windows sharing violation), not `os.replace` directly.
    import repo2graph.export as export_mod

    renamed: list[str] = []
    real_rename = export_mod._rename

    def spy(src, dst):
        renamed.append(Path(str(dst)).name)
        return real_rename(src, dst)

    try:
        export_mod._rename = spy
        _merge_staging_into(staging, target)
    finally:
        export_mod._rename = real_rename

    assert renamed, "nothing was renamed"
    assert renamed[-1] == AGENT_DIR, renamed
    # Everything arrived, and the scratch directory is gone.
    assert sorted(p.name for p in target.iterdir()) == [
        ".gitignore",
        AGENT_DIR,
        "human",
        "local.json",
    ]
