# @authormark v1 -- do not remove (authorship watermark)⁠​‌‌​​​‌​​‌‌‌​‌​​​​‌‌​​​​​‌‌​​​​‌​‌​​​​‌​​​‌‌​​‌‌​‌‌​‌​‌​​‌​​​‌​​​‌​​​​‌​​‌​‌‌​​‌​‌​‌​‌‌​​‌​‌​‌​​​‌​‌​​​​​​‌‌​​​​​‌‌​‌​‌‌​‌‌​‌‌‌​​‌‌‌​​​​​‌‌​​​‌​​‌​​‌‌‌‌​‌‌‌​‌‌‌​‌​‌​​​‌​‌‌​‌​‌​⁠
# Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
# Author: https://github.com/Srinivasan-78
# SPDX-License-Identifier: MIT
# Fingerprint: AMK1.bt0aB3jDBYVTP0knpbOwQj
"""The two places `graph.py` reads something too big to hold.

Both had the same shape of bug: a bound that was written down but not enforced
where it was promised. `_chunk_and_parse` sliced a large file at raw byte
offsets and threw away every slice that did not decode -- two of them per
straddling multi-byte character (ISS-196) -- while still buffering the whole
file for the digest. `add_cochange` capped `git log` output only after
`capture_output=True` had already read it to EOF (ISS-236).

Everything here is derived from the fixture bytes by hand: byte offsets counted
out in the test, sha256 taken with the stdlib, line counts from `count(b"\\n")`.
Nothing asserts a value the code under test produced.
"""

import hashlib
import subprocess
import sys
from pathlib import Path

from repo2graph.graph import Graph, add_cochange, build
from repo2graph.parse import BuildConfig


def _repo(tmp_path: Path, name: str, data: bytes) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / name).write_bytes(data)
    return repo


def _symbol_qualnames(g: Graph) -> set[str]:
    return {n["qualname"] for n in g.nodes.values() if n["type"] == "symbol"}


# ---------- ISS-196: a character straddling a slice boundary ----------
# Small enough to reason about by hand; `chunk_large_files` makes discovery
# yield the file anyway, and `_read_and_parse` chunks anything larger than this.
_LIMIT = 300


def _straddling_source() -> bytes:
    """A module whose `é` has its lead byte at offset _LIMIT - 1, exactly.

    So slice 1 (bytes 0.._LIMIT) ends on the lone lead byte 0xC3 and slice 2
    begins on the orphaned continuation byte 0xA9: before the fix, *both* failed
    to decode and both were dropped, taking alpha and beta with the first and
    gamma with the second.
    """
    head = b"def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n\n\n"
    pad = _LIMIT - 1 - len(head) - len(b"# ")
    assert pad > 0, "head no longer fits before the boundary"
    src = head + b"# " + b"x" * pad + "é".encode("utf8")
    src += b"\n\n\ndef gamma():\n    return 3\n"
    # The point of the fixture, pinned: the character sits on the boundary.
    assert src[_LIMIT - 1] == 0xC3
    assert src[_LIMIT] == 0xA9
    assert len(src) > _LIMIT
    return src


def test_iss196_symbols_on_both_sides_of_a_straddling_character_survive(tmp_path):
    repo = _repo(tmp_path, "big.py", _straddling_source())
    g = build(repo, config=BuildConfig(chunk_large_files=True, max_file_bytes=_LIMIT))

    assert g.nodes["file:big.py"]["chunked"] is True, "fixture did not take the chunked path"
    assert {"alpha", "beta", "gamma"} <= _symbol_qualnames(g)
    # Nothing was lost, so nothing is reported lost either.
    assert g.stats["chunk_slices_undecodable"] == 0


def test_iss196_chunked_read_reports_the_same_digest_size_and_lines_as_a_whole_read(tmp_path):
    """The digest and the line count used to come from a bytearray holding the
    entire file -- the one thing max_file_bytes exists to prevent. They are
    streamed now, and must still be the same numbers, both against the fixture
    bytes and against the whole-file reader on the same bytes."""
    data = ("# café\n" * 200).encode("utf8") + b"def solo():\n    return 1\n"
    repo = _repo(tmp_path, "big.py", data)

    # 64-byte slices through a file of two-byte characters: boundaries land
    # mid-character repeatedly, which is the interesting case for the carry.
    chunked = build(repo, config=BuildConfig(chunk_large_files=True, max_file_bytes=64))
    whole = build(repo, config=BuildConfig(max_file_bytes=len(data) + 1))
    assert chunked.nodes["file:big.py"]["chunked"] is True
    assert whole.nodes["file:big.py"]["chunked"] is False

    assert chunked.file_hashes["big.py"] == hashlib.sha256(data).hexdigest()
    assert chunked.nodes["file:big.py"]["size"] == len(data)
    assert chunked.nodes["file:big.py"]["lines"] == data.count(b"\n") + 1
    assert chunked.file_hashes == whole.file_hashes
    for key in ("size", "lines"):
        assert chunked.nodes["file:big.py"][key] == whole.nodes["file:big.py"][key]
    # And the content did survive the many mid-character boundaries.
    assert "solo" in _symbol_qualnames(chunked)


def _invalid_byte_source() -> bytes:
    """Three exactly-_LIMIT-byte slices, the middle one not UTF-8 at all.

    0xFF can never begin a UTF-8 sequence, so it is not a truncated character to
    carry forward -- it must still be skipped, must be counted, and must not
    stall the loop or cost the slices around it.
    """
    slices = []
    for body in (
        b"def alpha():\n    return 1\n",
        b"# " + b"\xff" * 10 + b"\n",
        b"def gamma():\n    return 3\n",
    ):
        pad = _LIMIT - len(body) - len(b"#\n")
        assert pad > 0
        slices.append(body + b"#" + b"x" * pad + b"\n")
    src = b"".join(slices)
    assert len(src) == 3 * _LIMIT
    return src


def test_iss196_bytes_that_are_not_utf8_are_counted_and_cost_only_their_own_slice(tmp_path):
    repo = _repo(tmp_path, "big.py", _invalid_byte_source())
    g = build(repo, config=BuildConfig(chunk_large_files=True, max_file_bytes=_LIMIT))

    assert g.nodes["file:big.py"]["chunked"] is True
    assert {"alpha", "gamma"} <= _symbol_qualnames(g)
    assert g.stats["chunk_slices_undecodable"] == 1
    assert g.stats["files_with_undecodable_chunks"] == 1


# ---------- ISS-236: the git-log read stops at the cap ----------
# Written to disk and run with sys.executable: a fake `git` on PATH cannot work
# on Windows, where CreateProcess only ever appends .exe when it searches PATH.
_FAKE_GIT = """\
import pathlib, sys

total, sentinel = int(sys.argv[1]), pathlib.Path(sys.argv[2])
block = b"H\\na.py\\nb.py\\nc.py\\n\\n"
page = block * 256
out = sys.stdout.buffer
written = 0
while written < total:
    out.write(page)
    out.flush()
    written += len(page)
# Only reached if the parent read every byte we had. A parent that stops at its
# cap kills us while we are blocked writing into a full pipe, so this file is
# the witness that the read did *not* run to EOF.
sentinel.write_text("finished")
"""


def _fake_git(tmp_path: Path, args: list[str], monkeypatch):
    """Point graph's Popen at a python child, and hand back the started process."""
    import repo2graph.graph as graphmod

    script = tmp_path / "fake_git.py"
    script.write_text(_FAKE_GIT, encoding="utf8")
    real_popen = subprocess.Popen
    started: list = []

    def popen(cmd, **kwargs):
        assert cmd[0] == "git", cmd
        proc = real_popen([sys.executable, str(script), *args], **kwargs)
        started.append(proc)
        return proc

    monkeypatch.setattr(graphmod.subprocess, "Popen", popen)
    return started


def test_iss236_the_byte_cap_stops_the_read_and_the_child_is_reaped(tmp_path, monkeypatch):
    """The cap has to bind *during* the read. A child with far more to say than
    the cap allows must be cut off mid-write, killed and waited for -- not read
    to EOF and trimmed afterwards, which is what capture_output=True did."""
    import repo2graph.graph as graphmod

    cap = 64 * 1024
    monkeypatch.setattr(graphmod, "MAX_COCHANGE_BYTES", cap)
    sentinel = tmp_path / "child_finished"
    started = _fake_git(tmp_path, [str(8 * 1024 * 1024), str(sentinel)], monkeypatch)

    g = Graph(tmp_path, "x")
    add_cochange(g, tmp_path, 1, {"a.py", "b.py", "c.py"}, min_pairs=1)

    assert g.stats["cochange_output_capped"] == cap
    assert not sentinel.exists(), "the whole 8 MB was read; the cap did not bind on the read"
    proc = started[0]
    assert proc.returncode is not None, "child was never waited for"
    assert proc.stdout.closed, "the pipe was left open"
    # What did get read is still used: the commits inside the cap pair up.
    assert ("file:a.py", "file:b.py") in {(e["src"], e["dst"]) for e in g.edges}


def test_iss236_a_timeout_is_still_a_silent_return(tmp_path, monkeypatch):
    """`subprocess.run(timeout=...)` raised TimeoutExpired into a bare `return`;
    the streamed read enforces the same deadline itself and must behave the
    same way -- no exception, no edges, and no surviving child."""
    import repo2graph.graph as graphmod

    monkeypatch.setattr(graphmod, "COCHANGE_TIMEOUT", 0.5)
    script = tmp_path / "hanging_git.py"
    script.write_text("import time\ntime.sleep(120)\n", encoding="utf8")
    real_popen = subprocess.Popen
    started: list = []

    def popen(cmd, **kwargs):
        proc = real_popen([sys.executable, str(script)], **kwargs)
        started.append(proc)
        return proc

    monkeypatch.setattr(graphmod.subprocess, "Popen", popen)

    g = Graph(tmp_path, "x")
    add_cochange(g, tmp_path, 1, {"a.py", "b.py"}, min_pairs=1)

    assert g.edges == []
    assert "cochange_output_capped" not in g.stats
    assert started[0].returncode is not None, "the hung child outlived the call"
