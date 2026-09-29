import argparse
import os
import stat as statmod
from collections import defaultdict
from pathlib import Path

import pytest
from repo2graph.cli import _max_file_mb
from repo2graph.graph import build
from repo2graph.parse import BuildConfig, discover


def test_max_file_mb_validator():
    assert _max_file_mb("1.5") == 1.5
    assert _max_file_mb("0.1") == 0.1
    with pytest.raises(argparse.ArgumentTypeError, match="must be at least 0.1"):
        _max_file_mb("0.09")
    with pytest.raises(argparse.ArgumentTypeError, match="expected a number, got 'abc'"):
        _max_file_mb("abc")


def test_the_documented_default_size_ceiling():
    """The 1.5 MB default is a documented number, so it is pinned as a literal
    rather than re-read from the config that would move with it."""
    from repo2graph.parse import MAX_BYTES

    assert MAX_BYTES == 1_500_000
    assert BuildConfig().max_file_bytes == 1_500_000


def test_a_file_over_the_size_ceiling_is_skipped_unless_chunking_is_on(tmp_path):
    """Over the ceiling the file gets no node; `chunk_large_files` rescues it.

    Run against a small `max_file_bytes` rather than the 1.5 MB default: the
    branch is a single `st.st_size > config.max_file_bytes` comparison, identical
    at any threshold, and the default version wrote 3 MB to disk and built the
    graph twice for 5.7s -- the slowest test in the suite by 5x. The default
    itself is pinned by the test above.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    limit = 2_000

    under = b"a = 1\n" * 300  # 1,800 bytes
    over = b"b = 2\n" * 400  # 2,400 bytes
    assert len(under) < limit < len(over), (len(under), len(over))
    (repo / "under_limit.py").write_bytes(under)
    (repo / "over_limit.py").write_bytes(over)

    g = build(repo, config=BuildConfig(max_file_bytes=limit))
    assert "file:under_limit.py" in g.nodes
    assert "file:over_limit.py" not in g.nodes

    g2 = build(repo, config=BuildConfig(max_file_bytes=limit, chunk_large_files=True))
    assert "file:under_limit.py" in g2.nodes
    assert "file:over_limit.py" in g2.nodes
    assert g2.nodes["file:over_limit.py"].get("chunked") is True


# Issue #248: the size check and the file-type check used to share one branch,
# and the `chunk_large_files` escape hatch dropped out of *both* -- its inner
# test looked only at the size half. So an entry that was non-regular AND over
# max_file_bytes skipped the S_ISREG rejection entirely and was yielded into
# _chunk_and_parse -> _safe_open -> a blocking read() on a FIFO or device.
#
# Windows has no mkfifo, and on Linux lstat reports st_size 0 for a FIFO, so
# neither platform can build the offending entry on disk. lstat is patched
# instead: only the one path answers "non-regular and huge", every other path
# gets the real stat, so the walk, the binary sniff and the yield are otherwise
# untouched.
_HUGE_SIZE = 50_000_000


def _fake_stat(mode: int, size: int):
    class _St:
        st_mode = mode
        st_size = size

    return _St()


@pytest.fixture
def oversized_fifo_repo(tmp_path, monkeypatch):
    """A plain (non-git) folder where `special` lstats as an oversized FIFO."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "regular.py").write_text("REGULAR = 1\n", encoding="utf8")
    (repo / "special").write_text("", encoding="utf8")
    special = (repo.resolve()) / "special"

    real_lstat = Path.lstat

    def fake_lstat(self, *args, **kwargs):
        if os.path.normcase(str(self)) == os.path.normcase(str(special)):
            return _fake_stat(statmod.S_IFIFO | 0o644, _HUGE_SIZE)
        return real_lstat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    return repo


def test_oversized_non_regular_file_is_never_yielded(oversized_fifo_repo):
    """A non-regular entry must be rejected even when it is also too large and
    `chunk_large_files` says large files are welcome. The type check is not a
    thing a size flag gets to waive."""
    config = BuildConfig(chunk_large_files=True)
    stats = defaultdict(int)
    got = {rel for rel, _abspath in discover(oversized_fifo_repo, stats=stats, config=config)}
    assert "regular.py" in got, got
    assert "special" not in got, got


def test_skipped_too_large_counts_only_oversized_regular_files(oversized_fifo_repo):
    """The other half of #248: the counter used to be bumped from inside a
    branch that also handled non-regular files, so a FIFO was reported to the
    human overview as a file skipped for its size."""
    config = BuildConfig(chunk_large_files=False)
    stats = defaultdict(int)
    got = {rel for rel, _abspath in discover(oversized_fifo_repo, stats=stats, config=config)}
    assert got == {"regular.py"}, got
    assert stats["skipped_too_large"] == 0, dict(stats)


def test_exclude_dir(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    normal_dir = repo / "normal"
    normal_dir.mkdir()
    (normal_dir / "a.py").write_text("a = 1")

    excluded_dir = repo / "my_excluded"
    excluded_dir.mkdir()
    (excluded_dir / "b.py").write_text("b = 2")

    # default
    g = build(repo)
    assert "file:normal/a.py" in g.nodes
    assert "file:my_excluded/b.py" in g.nodes

    # with exclude
    config = BuildConfig(extra_exclude_dirs=["my_excluded"])
    g2 = build(repo, config=config)
    assert "file:normal/a.py" in g2.nodes
    assert "file:my_excluded/b.py" not in g2.nodes
