"""Property-based tests for the three places hand-written examples kept missing.

Every test in this file is here because an enumerated example suite had already
been written for the same code and still let a bug through. The pattern is
always the same: the example picks the separator, the offset or the character
the author happened to think of, and the bug lives one character to the left.
Hypothesis picks them all.

Three areas, each with its own history in this repository:

* **UTF-8 and surrogates.** `tests/test_encoding.py` enumerates a 5x7x5 matrix
  of encoding x error handler x character on the *output* side. Nothing covered
  the *input* side -- arbitrary bytes off disk, sliced at arbitrary offsets by
  `graph._chunk_and_parse`, decoded and then counted for line numbers.
* **Chunk line slicing.** `.github/CONTRIBUTING.md` invariant 1 forbids
  `splitlines()` because it breaks on U+2028/U+2029/U+0085/\\x0b/\\x0c, which
  tree-sitter's `Point.row` does not. An example only detects that if the author
  thought to put one of those five characters in the fixture.
* **Path normalization.** Separators, `..`, case and unicode. The verdicts here
  are security boundaries (a `.env` that dodges `_is_secret_path` gets indexed),
  and a boundary tested only on the spellings someone imagined is a boundary
  with unknown holes.

**Determinism.** The profile below is `derandomize=True`: Hypothesis derives its
inputs from a hash of the test rather than from entropy, so a green run here is
green on every machine and every rerun, and a failure is reproducible without a
`.hypothesis` database. `max_examples=100` keeps the whole file well under a
second of a ~3-minute suite; `deadline=None` because a cold first example on a
loaded CI runner is not a performance regression.
"""

from __future__ import annotations

import posixpath
import sys
from pathlib import Path, PurePosixPath

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from repo2graph.chunks import MAX_CHARS, _keepends_lf, _lines, _split
from repo2graph.events import encodable
from repo2graph.fetch import parse_ref, parse_spec
from repo2graph.graph import _incomplete_utf8_tail
from repo2graph.query import is_test_path
from repo2graph.parse import explain_path
from repo2graph.security import _is_secret_path, redact_content

settings.register_profile(
    "repo2graph-properties",
    max_examples=100,
    deadline=None,
    derandomize=True,
    # The session-scoped `property_root` fixture below is reused across
    # examples on purpose -- it is read-only, so there is nothing to reset.
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
settings.load_profile("repo2graph-properties")


# ---------------------------------------------------------------- alphabets --

# Every character Python's splitlines() treats as a line break but tree-sitter
# does not, plus the two that both agree on, plus a lone surrogate (what
# `.decode("utf8", "surrogateescape")` produces from a stray byte) and two
# ordinary non-ASCII characters.
HAZARD_ALPHABET = [
    "a",
    "b",
    " ",
    "\t",
    "\r",  # both agree
    "\n",  # both agree
    "\x0b",
    "\x0c",
    "\x1c",
    "\x1d",
    "\x1e",
    "\x85",
    " ",
    " ",
    "\udce9",  # lone surrogate
    "\udcff",  # lone surrogate
    "é",
    "中",
]

hazard_text = st.text(alphabet=st.sampled_from(HAZARD_ALPHABET), max_size=120)

# Path segments drawn from the shapes `_is_secret_path`, `is_test_path` and
# `explain_path` actually decide on, plus the traversal and unicode spellings.
PATH_SEGMENTS = [
    "src",
    "pkg",
    "app.py",
    "test_app.py",
    "tests",
    "latest.py",
    "manifest.py",
    "secrets",
    ".env",
    ".env.production",
    "id_rsa",
    "server.pem",
    "node_modules",
    "appsettings.Production.json",
    "appsettings.json",
    ".ssh",
    "Config",
    "café",
    "中",
    "..",
    ".",
    "deploy",
]

path_segment_lists = st.lists(st.sampled_from(PATH_SEGMENTS), min_size=1, max_size=5)

# Lines whose *content* the secret scanner has an opinion about, mixed with
# ordinary code and with the line-break hazards, so a generated file exercises
# multi-line PEM spans and single-line assignments in the same body.
REDACTION_LINES = [
    'password = "hunter2xyz"',
    "api_key: sk-abcdef0123456789abcdef0123456789",
    "AWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE",
    '"token": "ghp_0123456789abcdef0123456789abcdef0123"',
    "-----BEGIN RSA PRIVATE KEY-----",
    "MIIEowIBAAKCAQEA0123456789abcdefGHIJ0123456789",
    "-----END RSA PRIVATE KEY-----",
    "postgres://user:s3cr3tpw@db.example.com:5432/app",
    "def handler(request):",
    "    return request",
    "",
    "# a comment   with a line separator",
    "\x0bvertical tab opens this line",
    "trailing carriage return\r",
]


class StrictStream:
    """A stream that really refuses what its codec cannot represent.

    The honest model of a Windows `TextIOWrapper`, copied in spirit from
    `tests/test_encoding.py`: a stream that accepts everything cannot catch a
    guard which merely *believes* it made the text safe.
    """

    def __init__(self, encoding: str, errors: str) -> None:
        self.encoding = encoding
        self.errors = errors
        self.buf: list[str] = []

    def write(self, text: str) -> int:
        text.encode(self.encoding, self.errors)  # raises exactly as the real one does
        self.buf.append(text)
        return len(text)

    @property
    def text(self) -> str:
        return "".join(self.buf)


@pytest.fixture(scope="session")
def property_root(tmp_path_factory):
    """A read-only repo tree for the `explain_path` containment properties.

    Session scoped deliberately: `explain_path` only ever stats, so there is no
    per-example state to reset, and a function-scoped `tmp_path` would be rebuilt
    once per *test* while Hypothesis reused it across 100 examples anyway.
    """
    root = tmp_path_factory.mktemp("property_root")
    for rel in (
        "src/app.py",
        "src/test_app.py",
        "tests/test_app.py",
        "secrets/server.pem",
        # Three root-level files refused by *filename*, which is the rule the
        # trailing-`..` laundering test below defeats.
        ".env",
        "id_rsa",
        "key.pem",
        "node_modules/x/index.js",
        "café/中.py",
    ):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("CONSTANT = 42\n", encoding="utf8")
    # A sibling of the root, so a `..` escape has something real to land on.
    (root.parent / "outside.py").write_text("OUTSIDE = 1\n", encoding="utf8")
    return root


# ======================================================= UTF-8 and surrogates


@given(raw=st.binary(max_size=256))
def test_decoding_arbitrary_bytes_never_raises_and_never_moves_a_line_boundary(raw):
    """Detects a decode that invents or swallows a newline, desyncing every citation.

    `graph._chunk_and_parse` counts newlines on the *raw bytes* (`newlines +=
    chunk.count(b"\\n")`) and then hands the *decoded text* to the parser, whose
    rows are what every `start_line`/`end_line` in nodes.jsonl is built from. If
    those two counts can ever disagree -- a replacement character that is a
    newline, a stray 0x0A consumed as part of a multi-byte sequence -- every
    symbol below that point is cited at the wrong line and nothing in the
    pipeline notices.

    An example test can only assert this for the byte strings someone wrote
    down; the guarantee has to hold for whatever is actually in the file,
    including truncated sequences, stray continuation bytes and 0xF8..0xFF,
    which is the whole input space Hypothesis walks here.
    """
    for errors in ("replace", "surrogateescape"):
        text = raw.decode("utf8", errors)  # must not raise
        assert text.count("\n") == raw.count(b"\n")
        assert len(_lines(text)) == raw.count(b"\n") + 1


@given(raw=st.binary(max_size=64))
def test_the_carried_utf8_tail_is_bounded_and_never_holds_a_newline(raw):
    """Detects a carry that would double-count a line across a slice boundary.

    `_incomplete_utf8_tail`'s docstring promises two things the caller depends
    on and neither is checked anywhere else: the carry is at most three bytes
    (the longest truncated prefix of a four-byte sequence), and it "never
    [contains] a newline ... so `line_offset` accounting is unaffected". If a
    newline could ride along in the carry it would be counted once in the slice
    that produced it and again in the slice that consumed it, shifting
    `line_offset` -- and so every symbol in a chunked file -- by one.

    Examples missed this because the interesting inputs are the *invalid* ones:
    a stray continuation byte with no lead, a 0xF8..0xFF byte, a buffer shorter
    than the sequence it claims to start.
    """
    n = _incomplete_utf8_tail(raw)
    assert 0 <= n < 4, "a carry longer than a UTF-8 sequence can be"
    assert n <= len(raw), "the carry claims more bytes than the buffer holds"
    carry = raw[len(raw) - n :] if n else b""
    assert b"\n" not in carry, "a newline in the carry is counted twice"


@given(text=st.text(max_size=200), cut=st.integers(min_value=0, max_value=1000))
def test_carrying_the_utf8_tail_across_a_slice_boundary_loses_no_characters(text, cut):
    """Detects the two-slices-both-dropped bug `_incomplete_utf8_tail` exists to fix.

    Slices are cut at raw byte offsets, so a multi-byte character straddles the
    boundary: its lead byte ends slice N and its continuations begin slice N+1.
    Before the carry, *both* slices failed to decode and both were discarded --
    up to 2 x max_file_bytes of source lost silently. The property is that
    re-splitting at the carry-adjusted offset reproduces the original text
    exactly, for every cut point in every string.

    An example can only pick one cut point in one string, and the cut points
    that matter depend on how many bytes each character happens to occupy --
    precisely the arithmetic a hand-written fixture gets right by accident.
    """
    raw = text.encode("utf8")
    cut = min(cut, len(raw))
    head, tail = raw[:cut], raw[cut:]
    n = _incomplete_utf8_tail(head)
    kept, carried = head[: len(head) - n], head[len(head) - n :]
    # `kept` must decode strictly: the whole point of the carry is that what is
    # left behind ends on a character boundary.
    assert kept.decode("utf8") + (carried + tail).decode("utf8") == text


@given(
    text=hazard_text,
    encoding=st.sampled_from(["cp1252", "ascii", "utf8", "cp932", "latin-1"]),
    errors=st.sampled_from(["strict", "surrogateescape", "replace", "backslashreplace"]),
)
def test_encodable_output_is_writable_for_arbitrary_text(text, encoding, errors):
    """Detects a guard that lets an unrepresentable character reach a strict stream.

    `tests/test_encoding.py` runs this cross product over five hand-picked
    characters. Those five were each added after a crash report, which is the
    tell: the matrix covers the failures that already happened. Hypothesis
    covers the ones that have not, including strings mixing a lone surrogate
    with U+2028 and CJK in one line -- the combination no reporter has sent in
    yet and the one where `encodable`'s encode/decode round trip is most likely
    to produce something the codec still refuses.
    """
    stream = StrictStream(encoding, errors)
    stream.write(encodable(text, stream))  # must not raise
    if text:
        assert stream.text, "the guard produced nothing at all from non-empty text"


# ================================================= chunk line slicing (split)


@given(src=hazard_text)
def test_lines_counts_rows_exactly_the_way_tree_sitter_does(src):
    """Detects a reintroduced `splitlines()` in the one helper that must not use it.

    CONTRIBUTING invariant 1: tree-sitter advances `Point.row` strictly on
    "\\n", so `_lines` must too. The property that pins it is arithmetic rather
    than exemplary -- the row count is always `src.count("\\n") + 1`, never
    anything derived from the characters in between -- and the second assertion
    states the failure mode directly: a source with no "\\n" at all is one line,
    however many U+2028s or form feeds it contains.

    An example only detects a `splitlines()` regression if its fixture happens
    to contain one of the five exotic break characters. Most fixtures are
    ordinary Python and would stay green through the regression.
    """
    lines = _lines(src)
    assert len(lines) == src.count("\n") + 1
    assert all("\n" not in ln for ln in lines)
    if "\n" not in src:
        assert len(lines) == 1, "a non-newline character was treated as a row break"


@given(text=hazard_text)
def test_keepends_lf_is_lossless_and_breaks_only_on_lf(text):
    """Detects a lossy chunk split -- source text silently dropped from an artifact.

    `_split` rebuilds every chunk by joining `_keepends_lf` output, so
    `"".join(_keepends_lf(t)) == t` is the invariant that stands between the
    chunker and quietly losing a line of the user's code. It is stated in the
    docstring and was never tested against anything but LF-only ASCII.

    The exotic break characters matter here for a second reason beyond line
    numbering: `splitlines(keepends=True)` would make a chunk's *pieces* depend
    on whichever stray separators the source happens to hold, so the same symbol
    chunks differently depending on its comments.
    """
    parts = _keepends_lf(text)
    assert "".join(parts) == text
    assert all(p.endswith("\n") for p in parts[:-1])
    assert all(p.count("\n") == 1 for p in parts[:-1])
    if parts:
        assert parts[-1].count("\n") <= 1
    assert len(parts) in (text.count("\n"), text.count("\n") + 1)


@given(src=hazard_text, data=st.data())
def test_a_one_indexed_inclusive_line_slice_round_trips(src, data):
    """Detects an off-by-one in the `[start_line, end_line]` slice every chunk uses.

    `chunks.iter_chunks` cuts a symbol body with
    `"\\n".join(lines[start_line - 1 : end_line])` -- 1-indexed, inclusive at
    both ends. The property is that the slice re-splits into exactly the lines
    it was cut from and into exactly `end - start + 1` of them, for every valid
    range in every text. A fixture with a 3-line function and a hand-counted
    expectation tests one range; the boundary cases (a single line, the first
    line, the last line, a range ending on a trailing empty line) are where the
    off-by-one lives, and Hypothesis shrinks straight to them.

    The slice is re-split with a bare `split("\\n")` rather than with `_lines`,
    and that is not a convenience. `_lines` strips *one* trailing "\\r" per line,
    which is right for CRLF and makes the helper non-idempotent on a line that
    genuinely ends in a carriage return: `_lines("\\r\\r")` is `["\\r"]` and
    `_lines("\\r")` is `[""]`. Nothing in the pipeline ever re-runs `_lines` over
    a chunk body (`mcp/retrieval._chunk_body_lines` splits on "\\n"), so this is
    a property of the helper rather than a defect -- but asserting it the other
    way round would have encoded the double-strip as intended behaviour.
    """
    lines = _lines(src)
    start = data.draw(st.integers(min_value=1, max_value=len(lines)), label="start_line")
    end = data.draw(st.integers(min_value=start, max_value=len(lines)), label="end_line")
    body = "\n".join(lines[start - 1 : end])
    assert body.split("\n") == lines[start - 1 : end]
    assert len(body.split("\n")) == end - start + 1
    assert len(_lines(body)) == end - start + 1


@given(
    fillers=st.lists(st.text(alphabet="abc ", max_size=12), min_size=1, max_size=60),
    max_chars=st.integers(min_value=24, max_value=120),
)
def test_split_covers_every_line_with_overlap_and_no_gap(fillers, max_chars):
    """Detects a line dropped between two chunk slices, or a slicer that never ends.

    `_split`'s packer rewinds by `OVERLAP_LINES` between slices
    (`i = max(start + 1, i - OVERLAP_LINES)`). That one expression has to do two
    incompatible things at once: rewind far enough to overlap, and still
    guarantee forward progress when a slice held fewer lines than the overlap.
    Get it wrong one way and a line vanishes from every artifact; get it wrong
    the other and the loop never terminates.

    Each generated line is prefixed with its own index, so a slice can be mapped
    back to the line range it came from without re-deriving anything from the
    implementation. Examples missed this because the failure needs a slice
    shorter than `OVERLAP_LINES`, which only happens at specific
    `max_chars`/line-length ratios nobody picks by hand.
    """
    lines = [f"L{i:04d}{f}" for i, f in enumerate(fillers)]
    text = "\n".join(lines)
    pieces = _split(text, max_chars)

    assert pieces, "a non-empty text produced no slices"
    assert text.startswith(pieces[0]), "the first slice is not the start of the text"
    assert text.endswith(pieces[-1]), "the last slice is not the end of the text"

    cursor = 0  # first line index not yet covered
    previous_start = -1
    for piece in pieces:
        assert len(piece) < 2 * max_chars, "a slice overshot its budget by more than one line"
        begins = int(piece[1:5])
        n_piece_lines = piece.count("\n") + (0 if piece.endswith("\n") else 1)
        ends = begins + n_piece_lines  # exclusive
        assert begins > previous_start, "a slice did not advance: _split would never terminate"
        assert begins <= cursor, f"lines {cursor}..{begins - 1} fell between two slices"
        previous_start, cursor = begins, max(cursor, ends)
    assert cursor == len(lines), "the tail of the text was never emitted"


@given(
    prefix_lines=st.lists(st.text(alphabet="ab", max_size=8), max_size=6),
    blob_len=st.integers(min_value=1, max_value=600),
    max_chars=st.integers(min_value=24, max_value=120),
)
def test_split_bounds_a_line_that_cannot_be_split_on_a_line_boundary(
    prefix_lines, blob_len, max_chars
):
    """Detects an unbounded chunk from minified JS, base64 or one-line JSON.

    A single line longer than `max_chars` cannot be shrunk by grouping on line
    boundaries, so `_split` pre-breaks it into `max_chars`-sized pieces. Without
    that step the packer emits the whole line as one slice, unbounded -- the
    retrieval budget is then silently exceeded by a chunk nobody can page past.

    The two halves of the guarantee are tested together because they trade off:
    bounding every slice is easy if you are allowed to lose characters, and
    keeping every character is easy if you are allowed to emit one huge slice.
    Hand-written examples pinned one blob length; the ratio of blob length to
    `max_chars` is what decides whether the pre-break and the packer interact
    correctly.
    """
    text = "\n".join([*prefix_lines, "x" * blob_len])
    pieces = _split(text, max_chars)
    assert pieces
    for piece in pieces:
        assert len(piece) < 2 * max_chars
    assert "".join(pieces).count("x") >= blob_len, "part of the unsplittable line was lost"


@given(chosen=st.lists(st.sampled_from(REDACTION_LINES), max_size=25))
def test_redaction_never_changes_the_line_count(chosen):
    """Detects redaction that shifts every citation below a secret.

    `chunks.iter_chunks` scans and redacts the whole body *before* splitting,
    and its comment states the reason that is safe: "Redaction is
    line-preserving, so splitting afterwards keeps every citation line number
    intact." `redact_content` implements that by re-appending `text.count("\\n",
    start, end)` newlines to each replacement. If a single span ever lost or
    gained one, every `[cite: path:start-end]` below it in the file would point
    at the wrong code, and the citation would still look well-formed.

    Examples covered single-line assignments. The multi-line case -- a PEM block
    whose span crosses several newlines, two spans on one line, a span that
    overlaps another and is dropped by the dedupe filter -- is combinatorial,
    which is why it is generated rather than enumerated.
    """
    text = "\n".join(chosen)
    redacted, _ = redact_content(text, policy="redact-match")
    assert len(_lines(redacted)) == len(_lines(text))
    # And redacting the already-redacted text is still line-preserving, which is
    # what makes `--secret-policy` safe to apply at both build and serve time.
    twice, _ = redact_content(redacted, policy="redact-match")
    assert len(_lines(twice)) == len(_lines(text))


@given(text=hazard_text)
def test_redaction_of_arbitrary_text_preserves_the_line_count(text):
    """Same invariant, but over text whose line breaks are the exotic ones.

    Split out from the generated-secrets test because it fails differently: here
    the risk is not the newline arithmetic inside a span but a scanner regex
    that treats U+2028 as a line terminator while `_lines` does not, so the two
    disagree about which line a finding is on.
    """
    redacted, _ = redact_content(text, policy="redact-match")
    assert len(_lines(redacted)) == len(_lines(text))


@given(text=hazard_text, policy=st.sampled_from(["off", "warn-only"]))
def test_a_non_redacting_policy_returns_the_text_untouched(text, policy):
    """Detects a policy switch that quietly rewrites text it was told to leave alone.

    `off` and `warn-only` exist so an operator can index a tree verbatim; a
    single mangled character under those policies is a silent corruption of the
    artifact rather than a visible refusal.
    """
    out, count = redact_content(text, policy=policy)
    assert out == text
    assert count == 0


# ======================================================== path normalization


@given(
    parts=path_segment_lists,
    sep=st.sampled_from(["/", "\\"]),
    lead=st.sampled_from(["", "/", "./", "\\", ".\\"]),
)
def test_secret_path_detection_is_separator_invariant(parts, sep, lead):
    """Detects a Windows-spelled path that dodges the secret-file refusal.

    `_is_secret_path` is the gate that keeps `.env`, `*.pem` and `secrets/` out
    of an index, and it normalizes with `str(path).replace("\\\\", "/")` before
    deciding. If any later comparison in that function is made against the
    un-normalized string, the same file is refused when written `secrets/db.key`
    and indexed when written `secrets\\db.key` -- a credential in chunks.jsonl,
    reachable from every MCP tool, on exactly one platform.

    Example tests spell their fixtures with forward slashes because that is what
    the rest of the codebase produces, which is precisely why the backslash
    spelling went unexercised.
    """
    posix = lead.replace("\\", "/") + "/".join(parts)
    other = lead + sep.join(parts)
    assert _is_secret_path(posix) == _is_secret_path(other)


@given(
    parts=path_segment_lists,
    sep=st.sampled_from(["/", "\\"]),
    lead=st.sampled_from(["", "/", "//", "./"]),
)
def test_secret_path_detection_is_idempotent_under_renormalization(parts, sep, lead):
    """Detects a verdict that depends on how many times a path has been normalized.

    Paths reach this function from several directions -- `parse.discover` hands
    it `rel.as_posix()`, the MCP `repo_read` path hands it a caller-supplied
    string it has already slash-normalized itself -- so the same file is checked
    in more than one spelling within a single request. Normalizing again must
    not change the answer; if it does, which spelling arrived first decides
    whether a secret is served.

    This is the `normalize(normalize(p)) == normalize(p)` property stated at the
    level that matters: not that the string is stable, but that the *decision*
    is.
    """
    raw = lead + sep.join(parts)
    once = raw.replace("\\", "/")
    twice = once.replace("\\", "/").strip("/")
    verdict = _is_secret_path(raw)
    assert verdict == _is_secret_path(once)
    assert verdict == _is_secret_path(twice)


@given(parts=path_segment_lists)
def test_secret_path_detection_is_case_invariant(parts):
    """Detects a case-sensitive comparison in a check that claims to lowercase.

    Windows and macOS filesystems are case-insensitive by default, so `.ENV` and
    `.env` are the same file. `_is_secret_path` lowercases up front for exactly
    that reason, and the risk is a later branch comparing against the original.
    `appsettings.Production.json` is in the segment pool on purpose: it is
    matched by a regex rather than by an exact name, which is the branch most
    likely to be written against the un-lowercased text.
    """
    path = "/".join(parts)
    assert _is_secret_path(path) == _is_secret_path(path.upper())
    assert _is_secret_path(path) == _is_secret_path(path.lower())


@given(parts=path_segment_lists, sep=st.sampled_from(["/", "\\"]))
def test_test_path_detection_is_separator_invariant(parts, sep):
    """Detects `latest.py` being re-classified as a test, or a test file missed.

    `is_test_path`'s docstring records the bug it was written to fix: a suffix
    match on `"test.py"` classified `latest.py`, `fastest.py` and `manifest.py`
    as tests, which excluded them from `is_public_symbol` and credited them as
    their own coverage. The fix matches per path *component*, which makes the
    function depend entirely on splitting the path correctly -- so the separator
    spelling is now load-bearing, and both spellings reach it (`impact` reads
    paths from a diff, which on Windows can carry either).

    The segment pool keeps `latest.py` and `manifest.py` alongside `test_app.py`
    so the property covers both directions of the misclassification.
    """
    assert is_test_path("/".join(parts)) == is_test_path(sep.join(parts))


@given(
    parts=st.lists(st.sampled_from(PATH_SEGMENTS), min_size=1, max_size=6),
    sep=st.sampled_from(["/", "\\"]),
)
def test_explain_path_never_reports_an_escaping_path_as_included(parts, sep, property_root):
    """Detects a `..` that walks out of the repo and still answers INCLUDED.

    `explain_path` is the documented answer to "would repo2graph index this
    file", and `repo2graph explain-path` feeds it whatever the user typed. It
    resolves the *parent* only (so a leaf symlink is still rejected by
    `discover`'s lstat) and relies on `relative_to(root)` to catch everything
    else. The property is the one that makes it a boundary rather than a
    convenience: if the verdict is INCLUDED, the reported relative path must not
    be absolute, and must still land under the root once resolved.

    Examples test `../../etc/passwd`. They do not test `src/../../outside.py`,
    `café/../..`, or a mixed-separator spelling of either, and the interesting
    failures are the ones where a `..` is cancelled by a real directory on the
    way out.

    Note what this deliberately does *not* assert: that `relative_path` is free
    of `..` segments. It is not, and the reason is a real defect -- see
    `test_a_trailing_dotdot_cannot_launder_an_excluded_path` below. Containment
    survives it (`.env/src/..` still resolves inside the root); the verdict does
    not.
    """
    target = sep.join(parts)
    result = explain_path(property_root, target)  # must not raise
    assert isinstance(result["included"], bool)
    if result["included"]:
        rel = result["relative_path"]
        assert not Path(rel).is_absolute()
        resolved = (Path(property_root) / rel).resolve()
        assert resolved.is_relative_to(Path(property_root).resolve())


@given(parts=path_segment_lists)
def test_explain_path_verdict_is_idempotent_on_its_own_output(parts, property_root):
    """Detects a normalizer whose output it would itself answer differently about.

    `explain_path` returns `relative_path` as the canonical spelling of what it
    was asked about. Feeding that back in has to produce the same verdict and
    the same rule, or the CLI's own output is not a valid input to the CLI --
    which is how a user reports "it says excluded, but when I paste the path it
    says included".

    This is `normalize(normalize(p)) == normalize(p)` for the one function in
    the codebase that publishes its normalized form.
    """
    first = explain_path(property_root, "/".join(parts))
    second = explain_path(property_root, first["relative_path"])
    assert (first["included"], first["rule"]) == (second["included"], second["rule"])
    assert first["relative_path"] == second["relative_path"]


@pytest.mark.xfail(
    sys.platform == "win32",
    strict=True,
    reason=(
        "Real defect, found by this property, left unfixed deliberately. "
        "`parse.explain_path` normalizes only the *parent* of the target "
        "(`target_path.parent.resolve() / target_path.name`), so a trailing "
        "`..` survives into `rel_str` and every later rule -- the secret-file "
        "refusal included -- is matched against the literal string. On Windows "
        "the Win32 API normalizes `..` lexically before the stat, so "
        "`target_path.exists()` succeeds against the *different* file the path "
        "collapses to. Minimal repro, with a root holding a `.env`: "
        "explain_path(root, '.env') -> included=False, rule='secret_file'; "
        "explain_path(root, '.env/src/..') -> included=True, rule='included'. "
        "`repo2graph explain-path` therefore reports a credential file as "
        "indexable. Not reachable on POSIX, where stat is physical and "
        "'.env/src' is ENOTDIR, which is why the xfail is Windows-only and "
        "strict -- an xpass here means the normalization was fixed and this "
        "marker must come off."
    ),
)
@given(
    excluded=st.sampled_from([".env", "id_rsa", "key.pem"]),
    filler=st.sampled_from(["src", "pkg", "anything"]),
)
def test_a_trailing_dotdot_cannot_launder_an_excluded_path(excluded, filler, property_root):
    """Detects an exclusion rule defeated by respelling the path.

    The verdict `explain_path` gives for a file must not depend on which of the
    infinitely many spellings of that file the caller typed. `F`, `F/x/..` and
    `F/x/y/../..` all name `F`, so all three must get `F`'s answer -- otherwise
    the rule that refuses to index credentials is advisory rather than binding,
    and the function that exists to explain the rules misreports them.

    Hand-written examples missed this for the ordinary reason: they spell paths
    the way the rest of the codebase produces them (`rel.as_posix()` out of
    `discover`, already normalized), so no fixture ever carried a trailing `..`.
    The generated input does, and only because `..` was put in the segment pool
    as a traversal probe rather than as a laundering one.
    """
    direct = explain_path(property_root, excluded)
    laundered = explain_path(property_root, f"{excluded}/{filler}/..")
    assert direct["included"] == laundered["included"], (
        f"{excluded!r} is {direct['rule']} but {excluded}/{filler}/.. is {laundered['rule']}"
    )


@given(
    spec=st.one_of(
        st.text(max_size=40),
        st.sampled_from(
            [
                "owner/..",
                "../owner/repo",
                "owner/repo/../..",
                "-upload-pack/repo",
                "owner/-x",
                "https://github.com/owner/../repo",
                "git@github.com:owner/..",
                "owner/repo\n",
                "owner//repo",
                "中/репо",
            ]
        ),
    )
)
def test_parse_spec_never_yields_a_component_that_escapes_the_clone_directory(spec):
    """Detects a repo spec that makes `git clone` write outside its temp directory.

    `fetch.parse_spec` feeds `owner` and `repo` straight into a clone
    destination and into a `git` argv, so a component of `..` targets the parent
    of the temp dir and a component starting with `-` is read by git as an
    option. The function's own comment names both. The property asserts the
    consequence rather than the implementation: whatever comes back, joining it
    under a base directory and normalizing must stay under that base, and
    neither component may be option-shaped.

    A validator built from a regex is the canonical case for generated input --
    the inputs that defeat it are the ones nobody thought to write in the
    `choices` list.
    """
    try:
        owner, repo = parse_spec(spec)
    except ValueError:
        return  # a refusal is always a correct answer here
    base = PurePosixPath("/clone/base")
    for part in (owner, repo):
        assert part, "an empty component would collapse the destination path"
        assert not part.startswith("-"), "an option-shaped component reaches the git argv"
        assert "/" not in part and "\\" not in part
    landed = PurePosixPath(posixpath.normpath(str(base / owner / repo)))
    assert landed.is_relative_to(base), "the clone destination escaped its base directory"


@given(
    ref=st.one_of(
        st.text(max_size=30),
        st.sampled_from(
            [
                "--upload-pack=touch",
                "main\n",
                "refs/heads/../../main",
                "..",
                "-main",
                "a/../b",
                "main ",
                "feature/foo-bar",
                "v1.2.3",
            ]
        ),
    )
)
def test_parse_ref_never_yields_a_ref_git_would_read_as_an_option(ref):
    """Detects a refname that smuggles a flag or a traversal into a `git` argv.

    `parse_ref`'s comment is explicit that the pattern is "a security check and
    not just a spelling check", and names the exact hazards: a leading `-`, a
    dotted traversal component, a trailing newline that a `$`-anchored pattern
    would have let through. Each of those was a separate hand-written example.
    The property covers them as one statement -- whatever is returned contains
    no `..`, no separator-adjacent dot component, no whitespace and no leading
    dash -- and keeps covering the ones not yet thought of.
    """
    try:
        out = parse_ref(ref)
    except ValueError:
        return
    assert out == ref.strip() or out == ref
    assert ".." not in out
    assert not out.startswith("-")
    assert not any(ch.isspace() for ch in out), "whitespace reached a git argv"
    assert all(part and not part.startswith(".") for part in out.split("/"))


def test_max_chars_default_is_what_the_split_properties_assume():
    """A guard so the generated `max_chars` range stays below the real default.

    The `_split` properties drive `max_chars` between 24 and 120 to keep the
    examples small. If `MAX_CHARS` ever drops into that range the production
    default would be inside the generated band rather than above it, and the
    properties would stop saying anything about the shipped configuration.
    """
    assert MAX_CHARS > 120


# ------------------------------------------- identifiers, paths, cpp input --
# #316, covering the sanitizers added for #454 and #475.

_IDENT_ALPHABET = st.characters(
    codec="utf-8", categories=("L", "N", "P", "S", "Z", "Cc", "Cf")
) | st.sampled_from(["‮", "⁦", "​", "﻿", "\n", "\r", " ", "\x00"])


@given(name=st.text(_IDENT_ALPHABET, max_size=600))
def test_clean_identifier_is_capped_single_line_and_free_of_hidden_chars(name):
    """Whatever a hostile repository names a symbol, the cleaned name fits the
    cap, cannot break a line or a header, and carries no invisible override."""
    from repo2graph.security import (
        HIDDEN_UNICODE_RE,
        MAX_SYMBOL_NAME_CHARS,
        clean_identifier,
        has_unsafe_path_chars,
    )

    out = clean_identifier(name)
    assert len(out) <= MAX_SYMBOL_NAME_CHARS
    assert not has_unsafe_path_chars(out)
    assert not HIDDEN_UNICODE_RE.search(out)
    # Idempotent: cleaning a clean name changes nothing.
    assert clean_identifier(out) == out


@given(text=st.text(_IDENT_ALPHABET, max_size=200))
def test_escape_hidden_unicode_round_trips_through_json(text):
    """The JSONL escape is lossless: the reader gets back exactly what was there."""
    import json

    from repo2graph.security import HIDDEN_UNICODE_RE, escape_hidden_unicode

    line = escape_hidden_unicode(json.dumps(text, ensure_ascii=False))
    assert not HIDDEN_UNICODE_RE.search(line)
    assert json.loads(line) == text


_DIRECTIVE = st.sampled_from(["include", "include_next", "import", "embed"])
_HASH = st.sampled_from(["#", "%:", "  #", "#/* x */", "#\\\n", "\t# "])


@given(
    lines=st.lists(
        st.tuples(_HASH, _DIRECTIVE, st.sampled_from(['"/dev/zero"', "<x.h>"])), max_size=8
    ),
    filler=st.lists(st.sampled_from(["int x;", "#define A 1", "// include me", ""]), max_size=8),
)
def test_strip_cpp_includes_leaves_no_file_opening_directive(lines, filler):
    """No spelling cpp accepts for a file-opening directive survives the strip,
    and ordinary lines (including a comment that says "include") are kept."""
    import re

    from repo2graph.parse import _strip_cpp_includes

    src = "\n".join([*(f"{h}{d} {t}" for h, d, t in lines), *filler]).encode()
    out = _strip_cpp_includes(src).decode()
    joined = out.replace("\\\n", "")
    assert not re.search(
        r"(?m)^[ \t]*(?:#|%:)(?:[ \t]|/\*.*?\*/)*(?:include|include_next|import|embed)\b", joined
    )
    for f in filler:
        if f:
            assert f in out
