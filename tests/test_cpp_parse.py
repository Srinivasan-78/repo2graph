import logging
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from repo2graph.chunks import _lines, build_chunks
from repo2graph.graph import build
from repo2graph.parse import parse_source


def test_cpp_parse_pass_1():
    # Simple #define handled fine by tree-sitter (0 errors)
    source = b"#define FOO 1\nint main() { return FOO; }"
    pf = parse_source(source, "c", filepath=Path("test.c"))
    assert pf.parse_errors == 0
    assert not pf.used_cpp


@patch("subprocess.run")
def test_cpp_parse_pass_2(mock_run):
    # A macro that tree-sitter cannot handle produces an ERROR node
    # e.g., an unclosed bracket or weird token in macro
    source = b"#define MACRO { error \nint main() MACRO }"

    # Mock subprocess.run to first succeed for 'cpp --version', then for 'cpp -w ...'
    def mock_run_impl(cmd, **kwargs):
        if "--version" in cmd:
            return MagicMock(returncode=0)
        else:
            return MagicMock(returncode=0, stdout=b"int main() { return 0; }")

    mock_run.side_effect = mock_run_impl

    pf = parse_source(source, "c", filepath="test.c")
    # used_cpp records that the preprocessed parse was cleaner. ISS-126
    # stopped adopting that tree, so parse_errors may still be Pass 1's
    # count; the flag itself is unchanged.
    assert pf.used_cpp
    assert mock_run.call_count == 2


@patch("subprocess.run")
def test_cpp_parse_pass_2_non_ascii_output(mock_run):
    # ISS-164: cpp's stdout can contain raw UTF-8 bytes (e.g. a Unicode string
    # literal or non-ASCII comment preserved by -P). Previously `subprocess.run`
    # was called with text=True, which decodes using the platform locale (cp1252
    # on Windows) and raises UnicodeDecodeError on bytes like b"\xc3\xa9" (an
    # UTF-8 encoded "e"). With text=True removed, subprocess.run always
    # returns bytes here, so decoding never happens and the crash cannot occur.
    source = b"#define MACRO { error \nint main() MACRO }"
    non_ascii_stdout = b'int main() { char *s = "caf\xc3\xa9"; return 0; }'

    def mock_run_impl(cmd, **kwargs):
        if "--version" in cmd:
            return MagicMock(returncode=0)
        return MagicMock(returncode=0, stdout=non_ascii_stdout)

    mock_run.side_effect = mock_run_impl

    pf = parse_source(source, "c", filepath="test.c")

    assert pf.used_cpp
    assert mock_run.call_count == 2


@patch("subprocess.run")
def test_cpp_parse_cpp_unavailable(mock_run):
    source = b"#define MACRO { error \nint main() MACRO }"

    # Mock subprocess.run to raise FileNotFoundError for cpp
    mock_run.side_effect = FileNotFoundError

    pf = parse_source(source, "c", filepath="test.c")
    # Should fall back to Pass 1 result silently
    assert pf.parse_errors > 0
    assert not pf.used_cpp


@patch("subprocess.run")
def test_cpp_parse_cpp_too_large(mock_run, caplog):
    source = b"#define MACRO { error \nint main() MACRO }"

    def mock_run_impl(cmd, **kwargs):
        if "--version" in cmd:
            return MagicMock(returncode=0)
        else:
            # Return output larger than 2x original size
            return MagicMock(returncode=0, stdout=b"int main() { return 0; } " * 10)

    mock_run.side_effect = mock_run_impl

    with caplog.at_level(logging.WARNING):
        pf = parse_source(source, "c", filepath="test.c")

    # Should skip cpp and use Pass 1
    assert pf.parse_errors > 0
    assert not pf.used_cpp
    assert "is too large, skipping" in caplog.text


# X-macro at file scope that tree-sitter cannot parse (ERROR on the later
# `int`), plus comment lines cpp -P strips. After expansion the function sits
# at preprocessed rows 2-4; those same row numbers in the *original* file are
# the DECLARE / blank / FOREACH lines — not `real_fn`. Restoring
# `source = cpp_bytes` without translating rows fails the slice assertions.
_ISS126_MACRO_C = """\
#define FOREACH_ITEM(X) X(a) X(b) X(c)
#define DECLARE(x) int x;

FOREACH_ITEM(DECLARE)

/* padding comment 1 */
/* padding comment 2 */
/* padding comment 3 */
/* padding comment 4 */
/* padding comment 5 */

int real_fn(void) {
    return 0x126;
}
"""


def test_iss126_cpp_fallback_line_numbers_match_original(tmp_path):
    """ISS-126: used_cpp=True must still cite the on-disk file, not cpp -P rows."""
    if shutil.which("cpp") is None:
        pytest.skip("cpp preprocessor not available")

    path = tmp_path / "macro.c"
    path.write_text(_ISS126_MACRO_C, encoding="utf8")
    orig_lines = _lines(_ISS126_MACRO_C)
    # Fixture is a detector only if the preprocessed span (rows 2-4) is not
    # the function in the original file. Hand-counted, not from parse_source.
    trap = "\n".join(orig_lines[1:4])
    assert "real_fn" not in trap
    assert "0x126" not in trap

    pf = parse_source(path.read_bytes(), "c", filepath=path)
    assert pf.used_cpp
    real = next(s for s in pf.symbols if s.name == "real_fn")
    assert 1 <= real.start_line <= real.end_line <= len(orig_lines)
    claimed = "\n".join(orig_lines[real.start_line - 1 : real.end_line])
    assert "int real_fn" in claimed
    assert "0x126" in claimed
    assert "int a; int b; int c;" not in claimed

    g = build(tmp_path)
    assert g.stats.get("cpp_fallback_files", 0) >= 1
    node = next(n for n in g.nodes.values() if n.get("name") == "real_fn")
    node_claimed = "\n".join(orig_lines[node["start_line"] - 1 : node["end_line"]])
    assert "int real_fn" in node_claimed
    assert "0x126" in node_claimed

    chunk = next(c for c in build_chunks(g) if c["name"] == "real_fn")
    assert chunk["start_line"] == node["start_line"]
    assert chunk["end_line"] == node["end_line"]
    assert "int real_fn" in chunk["text"]
    assert "0x126" in chunk["text"]
