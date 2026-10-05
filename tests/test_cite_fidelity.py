"""A chunk must not cite a line range it does not contain.

`[cite: path:start-end]` is the promise the README leads with: an agent uses it
to go and check the answer, so a range that does not describe the text beside it
is worse than no citation at all.

A symbol longer than `chunks.MAX_CHARS` is split into parts, and every part was
emitted carrying the *parent symbol's* whole range. So `jsonable_encoder`, 243
lines of FastAPI, became three chunks all citing `encoders.py:102-344`, two of
which begin somewhere in the middle of the body with no `def` line in sight.
When BM25 preferred part two -- which it does, because a slice of a long body
matches ordinary English words -- the pack said "lines 102-344" and showed the
middle of the function.

The file-residual path in the same module already computes a per-slice
`span_start`/`span_end`; only the symbol path inherited the parent's.

These tests assert the invariant directly: for every chunk, the first real line
of text is the source line at `start_line`, and parts tile their symbol in order
without overlapping.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repo2graph.chunks import MAX_CHARS, build_chunks
from repo2graph.graph import build

# A function long enough to split into several parts: each statement line is
# padded so the body passes MAX_CHARS without needing hundreds of lines.
_PAD = "x" * 90
_BODY = "\n".join(f"    v{i} = {i}  # {_PAD}" for i in range(140))
LONG_PY = f'''def long_function(a, b, c):
    """A docstring."""
{_BODY}
    return a


def after_it():
    return 1
'''


def _first_code_line(text: str) -> str:
    """The first line of a chunk's text that is not a generated `#` header."""
    for line in text.split("\n"):
        if line.startswith("#") or not line.strip():
            continue
        return line
    return ""


def _chunks_for(tmp_path: Path, name: str, source: str) -> tuple[list[dict], list[str]]:
    (tmp_path / name).write_text(source, encoding="utf8")
    g = build(tmp_path, git_history=0)
    chunks = list(build_chunks(g, tmp_path))
    lines = source.split("\n")
    return chunks, lines


def test_long_symbol_actually_splits(tmp_path: Path) -> None:
    """Guard the fixture: if it stopped splitting, the tests below prove nothing."""
    assert len(_BODY) > MAX_CHARS
    chunks, _ = _chunks_for(tmp_path, "m.py", LONG_PY)
    parts = [c for c in chunks if c.get("qualname") == "long_function"]
    assert len(parts) > 1, f"expected a split symbol, got {len(parts)} chunk(s)"


def test_every_chunk_starts_at_the_line_it_cites(tmp_path: Path) -> None:
    """start_line must name the line the chunk's text actually begins on."""
    chunks, lines = _chunks_for(tmp_path, "m.py", LONG_PY)
    offenders = []
    for c in chunks:
        first = _first_code_line(c["text"])
        if not first:
            continue
        cited = lines[c["start_line"] - 1] if c["start_line"] - 1 < len(lines) else "<past EOF>"
        if first.rstrip("\r") != cited.rstrip("\r"):
            offenders.append(
                f"{c['id']} cites {c['path']}:{c['start_line']}-{c['end_line']}\n"
                f"      text begins: {first!r}\n"
                f"      line {c['start_line']}:   {cited!r}"
            )
    assert not offenders, "chunks citing a line they do not begin on:\n  " + "\n  ".join(offenders)


def test_split_parts_tile_their_symbol_in_order(tmp_path: Path) -> None:
    """Parts must be contiguous, ordered, and inside the parent's range."""
    chunks, _ = _chunks_for(tmp_path, "m.py", LONG_PY)
    parts = [c for c in chunks if c.get("qualname") == "long_function"]
    parts.sort(key=lambda c: c["id"])
    assert len(parts) > 1

    first, last = parts[0], parts[-1]
    assert first["start_line"] == 1, "the first part must start where the symbol does"
    assert last["end_line"] >= last["start_line"]

    for earlier, later in zip(parts, parts[1:]):
        assert earlier["end_line"] <= earlier["end_line"]
        assert later["start_line"] > earlier["start_line"], "parts must advance"
        assert later["start_line"] <= earlier["end_line"] + 1, "parts must not skip lines"
        assert later["end_line"] >= later["start_line"]


def test_a_short_symbol_keeps_its_full_range(tmp_path: Path) -> None:
    """The unsplit path must be untouched: one chunk, the symbol's own range."""
    chunks, _ = _chunks_for(tmp_path, "m.py", LONG_PY)
    short = [c for c in chunks if c.get("qualname") == "after_it"]
    assert len(short) == 1
    assert _first_code_line(short[0]["text"]) == "def after_it():"


LONG_TS = (
    "class C {\n"
    + "\n".join(f"  m{i}() {{ return {i} /* {_PAD} */ }}" for i in range(80))
    + "\n}\n"
)


# An explicit id: the default would be the whole fixture source, which turns a
# one-line failure into a screenful.
@pytest.mark.parametrize("name,source", [("m.ts", LONG_TS)], ids=["typescript-class"])
def test_cite_fidelity_holds_for_other_languages(tmp_path: Path, name: str, source: str) -> None:
    """The invariant is about chunking, not about Python."""
    chunks, lines = _chunks_for(tmp_path, name, source)
    for c in chunks:
        first = _first_code_line(c["text"])
        if not first:
            continue
        cited = lines[c["start_line"] - 1] if c["start_line"] - 1 < len(lines) else "<past EOF>"
        assert first.rstrip("\r") == cited.rstrip("\r"), (
            f"{c['id']} cites {c['path']}:{c['start_line']} but begins {first!r}"
        )
