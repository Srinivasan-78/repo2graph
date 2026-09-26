"""Console output must never raise, whatever the platform decided stdout is.

Every historical regression in this project is a Windows encoding bug
(ISS-06/17/22/27), and they share a shape: code assumes stdout can represent
what it was handed. It usually can. Under a redirected Windows stdout it is a
cp1252 TextIOWrapper, and under Git Bash a *piped* one carries
`errors='surrogateescape'` -- which still raises on any character cp1252 lacks
that is not a lone surrogate, so the obvious "non-strict means safe" guard is
wrong.

The matrix below is the cross product that matters: encoding x error handler x
character. Each cell asserts the same two things -- no exception, and something
came out -- because a command that prints nothing is as broken as one that
crashes, just quieter.
"""

import io
import json
import sys

import pytest

from repo2graph.events import SAFE_ERRORS, encodable, write_safe

# Characters outside cp1252, each from a different failure report.
ARROW = "→"  # U+2192, the one that started ISS-17
ACCENT = "é"  # U+00E9, inside cp1252 -- the control case
CJK = "中"  # U+4E2D, outside every single-byte codec
LINE_SEP = " "  # the splitlines() hazard from AGENTS.md
LONE_SURROGATE = "\udce9"  # what surrogateescape produces from a stray byte

TRICKY = (ARROW, ACCENT, CJK, LINE_SEP, LONE_SURROGATE)


class FakeStream:
    """A stream that reports an encoding and handler but accepts anything.

    Deliberately does *not* enforce its own declared encoding on write: these
    tests are about what `encodable` decides to hand over, not about
    re-implementing TextIOWrapper.
    """

    def __init__(self, encoding="cp1252", errors="strict"):
        self.encoding = encoding
        self.errors = errors
        self.buf = []

    def write(self, text):
        self.buf.append(text)
        return len(text)

    def flush(self):
        pass

    @property
    def text(self):
        return "".join(self.buf)


class StrictStream(FakeStream):
    """A stream that really does refuse what its codec cannot represent.

    This is the honest model of a Windows TextIOWrapper, and the one that
    catches a guard which merely *believes* it made the text safe.
    """

    def write(self, text):
        text.encode(self.encoding, self.errors)  # raises exactly as the real one does
        return super().write(text)


# ------------------------------------------------------------ the matrix ----
#
# 5 encodings x 7 handlers x 5 characters = 175 cases, and it is exhaustive on
# purpose. Do not thin it to "the interesting rows": which cells actually raise
# is not predictable from the codec, so there is no slice that is safely
# redundant. `utf8` looks like the trivial row and is not -- it refuses a lone
# surrogate under both `strict` and `surrogateescape`. `cp932` accepts the arrow
# but not a surrogate; `latin-1` the reverse. `surrogatepass` rescues a surrogate
# only on the UTF codecs and raises on all four narrow ones.
#
# The cost of keeping it is about 0.2s of a 35s suite. The cost of getting it
# wrong is the bug class AGENTS.md calls the source of every historical
# regression in this repo (ISS-06/17/22/27), which is why the product is
# enumerated rather than sampled.
@pytest.mark.parametrize("encoding", ["cp1252", "ascii", "utf8", "cp932", "latin-1"])
@pytest.mark.parametrize(
    "errors",
    [
        "strict",
        "surrogateescape",
        "replace",
        "backslashreplace",
        "xmlcharrefreplace",
        "namereplace",
        "surrogatepass",
    ],
)
@pytest.mark.parametrize("char", TRICKY)
def test_encodable_output_is_always_writable(encoding, errors, char):
    """The core promise: whatever comes back, the real stream accepts it."""
    stream = StrictStream(encoding=encoding, errors=errors)
    safe = encodable(f"path/to/caf{char}.py", stream)
    stream.write(safe)  # must not raise
    assert stream.text.strip(), "the guard produced nothing at all"


@pytest.mark.parametrize("char", TRICKY)
def test_write_safe_never_raises_on_a_strict_cp1252_stream(char):
    """The reported bug: `repo2graph rag ... > out.md` on Windows."""
    stream = StrictStream("cp1252", "strict")
    write_safe(stream, f"heading {char} tail")
    assert stream.text.endswith("\n")


@pytest.mark.parametrize("char", TRICKY)
def test_write_safe_never_raises_under_git_bash_surrogateescape(char):
    """The subtler bug: a piped Git Bash stdout is cp1252 + surrogateescape.

    surrogateescape is not in SAFE_ERRORS precisely because it round-trips lone
    surrogates while still raising on an ordinary character the codec lacks.
    """
    assert "surrogateescape" not in SAFE_ERRORS
    stream = StrictStream("cp1252", "surrogateescape")
    write_safe(stream, f"heading {char} tail")
    assert stream.text


def test_emit_with_a_cp1252_surrogateescape_stdout(monkeypatch):
    """4.1(c): U+2192 through a mocked Git Bash stdout, no exception."""
    from repo2graph.cli import _emit

    stream = StrictStream("cp1252", "surrogateescape")
    monkeypatch.setattr(sys, "stdout", stream)

    _emit(f"budget {ARROW} exceeded")  # must not raise

    out = stream.text
    assert out, "nothing was written"
    assert ARROW not in out, "an unrepresentable character reached a cp1252 stream"
    assert "budget" in out and "exceeded" in out
    assert "?" in out or "\\u2192" in out or "&#" in out


def test_emit_preserves_a_surrogate_escaped_path(monkeypatch):
    """S-13: a surrogateescape-decoded path stays byte-identical on the way out."""
    from repo2graph.cli import _emit

    stream = FakeStream("ascii", "surrogateescape")
    monkeypatch.setattr(sys, "stdout", stream)
    _emit("caf\udce9.py")
    assert stream.text == "caf\udce9.py\n"


def test_emit_survives_an_encoding_python_does_not_have(monkeypatch):
    """S-12: Windows can report "cp0". That must not flatten ordinary text."""
    from repo2graph.cli import _emit

    stream = FakeStream("cp0", "strict")
    monkeypatch.setattr(sys, "stdout", stream)
    _emit("café.py")
    assert stream.text == "café.py\n", (
        "an unknown encoding must not replace characters that were never a problem"
    )


def test_a_stream_with_no_encoding_attribute_at_all(monkeypatch):
    """Some wrappers report neither encoding nor errors."""

    class Bare:
        def __init__(self):
            self.buf = []

        def write(self, text):
            self.buf.append(text)

        def flush(self):
            pass

    stream = Bare()
    write_safe(stream, f"x {CJK} y")
    assert "".join(stream.buf)


def test_encoding_is_read_at_call_time_not_import_time(monkeypatch):
    """Tests replace sys.stdout after import; a cached handler would miss it."""
    stream = StrictStream("ascii", "strict")
    first = encodable(ARROW, stream)
    stream.encoding = "utf8"
    second = encodable(ARROW, stream)
    assert first != second, "the stream's encoding was cached"
    assert second == ARROW


def test_write_safe_swallows_a_broken_pipe():
    class Broken:
        encoding, errors = "utf8", "strict"

        def write(self, text):
            raise BrokenPipeError("downstream closed")

        def flush(self):
            pass

    write_safe(Broken(), "anything")  # must not raise


def test_write_safe_swallows_a_closed_stream():
    stream = io.StringIO()
    stream.close()
    write_safe(stream, "anything")  # must not raise


def test_a_stream_that_lies_about_its_encoding_still_does_not_crash():
    """encodable() cleared it, the stream rejects it anyway: still no traceback."""

    class Liar(StrictStream):
        def __init__(self):
            super().__init__("utf8", "strict")

        def write(self, text):
            self.encoding = "ascii"  # changes its mind mid-write
            return super().write(text)

    write_safe(Liar(), f"x {CJK} y")


# ------------------------------------------------------- audit + events ----


@pytest.mark.parametrize("char", TRICKY)
def test_an_audit_record_survives_an_unencodable_argument(char):
    """The audit line must be written even when the query cannot be rendered."""
    from repo2graph.audit import AuditConfig, AuditLogger

    stream = StrictStream("cp1252", "strict")
    AuditLogger(AuditConfig(), stream=stream).record(
        "repo_search", {"query": f"find {char} handler"}
    )
    assert stream.text.strip(), "no audit record was emitted"


def test_an_audit_file_sink_accepts_unencodable_text(tmp_path):
    from repo2graph.audit import AuditConfig, AuditLogger

    path = tmp_path / "audit.log"
    log = AuditLogger(AuditConfig(path=str(path)), stream=io.StringIO())
    log.record("repo_search", {"query": f"{ARROW}{CJK}{LINE_SEP}"})
    log.close()
    assert json.loads(path.read_text(encoding="utf8").strip())


@pytest.mark.parametrize("char", TRICKY)
def test_a_structured_event_survives_an_unencodable_field(char):
    from repo2graph.events import emit

    stream = StrictStream("cp1252", "strict")
    record = emit("test_event", stream=stream, detail=f"x{char}y")
    assert record["event"] == "test_event"
    assert stream.text.strip()
