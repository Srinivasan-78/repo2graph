import logging
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from repo2graph import parse as parse_mod
from repo2graph.chunks import _lines, build_chunks
from repo2graph.graph import build
from repo2graph.parse import parse_source


@pytest.fixture(autouse=True)
def _reset_cpp_probe_cache():
    """`_cpp_available()` memoizes a successful probe for the life of the
    process (issue #239), so a test that let the real probe through would
    otherwise decide how many times the *next* test sees `cpp --version`.
    Every test in this file starts from an unprobed state."""
    parse_mod._cpp_available_cache = None
    yield
    parse_mod._cpp_available_cache = None


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


# ==========================================================================
# Issues #239 / #240 -- the cost and the stdin handle of the cpp fallback
# ==========================================================================

# Pass 1 leaves an ERROR node on this, which is what arms the macro-aware
# retry. Every file below is a copy of it, so each one reaches the probe.
_ERRORING_C = b"#define MACRO { error \nint main() MACRO }"


def _cpp_calls(mock_run):
    """(version-probe argv list, preprocess argv list) out of a patched run."""
    argvs = [call.args[0] for call in mock_run.call_args_list]
    return (
        [a for a in argvs if "--version" in a],
        [a for a in argvs if "--version" not in a],
    )


@patch("subprocess.run")
def test_iss239_the_cpp_version_probe_runs_once_across_many_erroring_files(mock_run):
    """#239: `cpp --version` answers the same thing for the life of the
    process, so a macro-heavy tree must not pay a second spawn per file. The
    count asserted is a hand-derived literal -- one probe, one preprocess per
    file -- not a number the parser computed."""

    def mock_run_impl(cmd, **kwargs):
        if "--version" in cmd:
            return MagicMock(returncode=0)
        return MagicMock(returncode=0, stdout=b"int main() { return 0; }")

    mock_run.side_effect = mock_run_impl

    for name in ("a.c", "b.c", "c.cpp", "d.h"):
        parse_source(_ERRORING_C, "c", filepath=name)

    probes, preprocesses = _cpp_calls(mock_run)
    assert len(probes) == 1, probes
    assert len(preprocesses) == 4, preprocesses


@patch("subprocess.run")
def test_iss239_a_failed_probe_is_retried_rather_than_cached(mock_run):
    """#239: only a *successful* probe is memoized, exactly as
    fetch._git_version does it. A transient fd-exhaustion or fork failure must
    not disable the cpp fallback for the rest of the process's life -- which is
    precisely what functools.lru_cache would do here."""
    attempts = []

    def mock_run_impl(cmd, **kwargs):
        if "--version" in cmd:
            attempts.append(cmd)
            if len(attempts) == 1:
                raise OSError("fork: Resource temporarily unavailable")
            return MagicMock(returncode=0)
        return MagicMock(returncode=0, stdout=b"int main() { return 0; }")

    mock_run.side_effect = mock_run_impl

    first = parse_source(_ERRORING_C, "c", filepath="a.c")
    assert not first.used_cpp  # the probe failed, so no preprocess was run
    assert _cpp_calls(mock_run)[1] == []

    second = parse_source(_ERRORING_C, "c", filepath="b.c")
    assert second.used_cpp
    probes, preprocesses = _cpp_calls(mock_run)
    assert len(probes) == 2, probes
    assert len(preprocesses) == 1, preprocesses


@patch("subprocess.run")
def test_iss240_both_cpp_invocations_close_stdin(mock_run):
    """#240: capture_output redirects the child's stdout and stderr only, so
    without stdin=DEVNULL cpp inherits ours -- under repo2graph-mcp on stdio
    that handle is the client's JSON-RPC pipe. Asserted at the call site as
    well as in test_compat's whole-package AST sweep."""

    def mock_run_impl(cmd, **kwargs):
        if "--version" in cmd:
            return MagicMock(returncode=0)
        return MagicMock(returncode=0, stdout=b"int main() { return 0; }")

    mock_run.side_effect = mock_run_impl

    parse_source(_ERRORING_C, "c", filepath="a.c")

    assert mock_run.call_args_list, "the cpp fallback never ran"
    for call in mock_run.call_args_list:
        assert call.kwargs.get("stdin") is subprocess.DEVNULL, call
