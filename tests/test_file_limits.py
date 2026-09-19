import argparse
import hashlib

import pytest
from repo2graph.cli import _max_file_mb
from repo2graph.graph import _utf8_cut_incomplete, build
from repo2graph.parse import BuildConfig

# Small enough that the fixture is a few hundred bytes, large enough for a
# prefix + comment pad so the lead byte of `é` lands at offset `limit - 1`.
_ISS196_LIMIT = 300
_ISS196_PREFIX = b"def alpha():\n    return 1\n"
_ISS196_SUFFIX = b"\ndef beta():\n    return 2\n\ndef gamma():\n    return 3\n"


def _iss196_boundary_source(limit: int, boundary: bytes) -> bytes:
    """Python source whose `boundary` bytes begin at offset `limit - 1`.

    Hand-placed so a `--chunk-large-files` slice of `limit` bytes ends on the
    first byte of `boundary` and the next slice begins on the rest. The
    symbols `alpha` / `beta` / `gamma` sit on either side of that split.
    """
    pad_len = (limit - 1) - len(_ISS196_PREFIX)
    pad = b"# " + b"x" * (pad_len - 2)
    body = _ISS196_PREFIX + pad + boundary + _ISS196_SUFFIX
    assert body[limit - 1 : limit - 1 + len(boundary)] == boundary
    return body


def test_max_file_mb_validator():
    assert _max_file_mb("1.5") == 1.5
    assert _max_file_mb("0.1") == 0.1
    with pytest.raises(argparse.ArgumentTypeError, match="must be at least 0.1"):
        _max_file_mb("0.09")
    with pytest.raises(argparse.ArgumentTypeError, match="expected a number, got 'abc'"):
        _max_file_mb("abc")


def test_file_limits(tmp_path):
    # create files
    repo = tmp_path / "repo"
    repo.mkdir()

    under_limit_file = repo / "under_limit.py"
    over_limit_file = repo / "over_limit.py"

    under_content = b"a = 1\n" * (1_499_900 // 6)
    under_content += b"x" * (1_499_900 - len(under_content))
    under_limit_file.write_bytes(under_content)

    over_content = b"b = 2\n" * (1_500_100 // 6)
    over_content += b"y" * (1_500_100 - len(over_content))
    over_limit_file.write_bytes(over_content)

    # default config
    g = build(repo)
    assert "file:under_limit.py" in g.nodes
    assert "file:over_limit.py" not in g.nodes

    # chunk large files config
    config = BuildConfig(chunk_large_files=True)
    g2 = build(repo, config=config)
    assert "file:under_limit.py" in g2.nodes
    assert "file:over_limit.py" in g2.nodes
    assert g2.nodes["file:over_limit.py"].get("chunked") is True


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


def test_iss196_utf8_cut_incomplete_literal_offsets():
    """Hand-derived cut points for 2/3/4-byte tails and a non-carryable 0xFF."""
    assert _utf8_cut_incomplete(b"abc\xc3") == 3
    assert _utf8_cut_incomplete(b"abc\xc3\xa9") == 5
    assert _utf8_cut_incomplete(b"ab\xe2\x82") == 2
    assert _utf8_cut_incomplete(b"ab\xe2\x82\xac") == 5
    assert _utf8_cut_incomplete(b"\xf0\x9f\x98") == 0
    assert _utf8_cut_incomplete(b"ok") == 2
    assert _utf8_cut_incomplete(b"\xff") == 1


def test_iss196_utf8_char_on_chunk_boundary(tmp_path):
    """#196: a multi-byte character on the max_file_bytes boundary must not
    drop symbols on either side of the split.

    `é` is U+00E9 (`\\xc3\\xa9`). The fixture places the lead byte at
    `limit - 1` so the old `chunk.decode(); continue` path discarded *both*
    slices — slice N ended on a lone lead byte, slice N+1 began on the
    orphan continuation. ASCII fixtures never hit this, which is why
    `test_file_limits` stayed green.
    """
    limit = _ISS196_LIMIT
    raw = _iss196_boundary_source(limit, "é".encode("utf-8"))
    assert raw[limit - 1] == 0xC3
    assert raw[limit] == 0xA9
    assert len(raw) > limit
    assert _utf8_cut_incomplete(raw[:limit]) == limit - 1

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "boundary.py").write_bytes(raw)

    g = build(repo, config=BuildConfig(max_file_bytes=limit, chunk_large_files=True))
    names = {n["name"] for n in g.nodes.values() if n.get("type") == "symbol"}
    assert {"alpha", "beta", "gamma"} <= names

    node = g.nodes["file:boundary.py"]
    assert node.get("chunked") is True
    assert node["size"] == len(raw)
    assert node["lines"] == raw.count(b"\n") + 1
    assert g.file_hashes["boundary.py"] == hashlib.sha256(raw).hexdigest()
    assert g.stats.get("chunk_decode_skips", 0) == 0


def test_iss196_corrupt_utf8_chunk_increments_stat(tmp_path, capsys):
    """#196: a slice that is truly not UTF-8 is skipped, but the loss is
    counted (and warned) instead of vanishing into a silent `continue`.
    """
    limit = _ISS196_LIMIT
    raw = _iss196_boundary_source(limit, b"\xff")
    assert raw[limit - 1] == 0xFF
    assert len(raw) > limit

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "corrupt.py").write_bytes(raw)

    g = build(repo, config=BuildConfig(max_file_bytes=limit, chunk_large_files=True))
    names = {n["name"] for n in g.nodes.values() if n.get("type") == "symbol"}
    # First slice ends on 0xFF and cannot be aligned; alpha lives there.
    # The next slice is valid UTF-8 and must still yield beta / gamma.
    assert "alpha" not in names
    assert {"beta", "gamma"} <= names
    assert g.stats["chunk_decode_skips"] == 1
    assert "undecodable UTF-8" in capsys.readouterr().err
