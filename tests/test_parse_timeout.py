"""ISS-81 / P2.3: per-file tree-sitter parse timeout.

`MAX_BYTES` bounds size, not time. These tests pin two things the audit
asked for before a timeout could ship:

1. A deeply nested but legitimate file must not false-positive under the
   conservative default (`PARSE_TIMEOUT_MICROS`).
2. A pathological, still size-capped file must abort cleanly — the suite
   does not hang, and a completed parse of the same fixture *would* have
   produced a symbol (so an unbounded `parser.parse` fails this test).

Membership is hand-derived from the fixture source (AGENTS.md): no scores,
no comparison of the implementation to itself.
"""

import threading
import time
from types import SimpleNamespace

from repo2graph.parse import (
    LANG_CFG,
    PARSE_TIMEOUT_MICROS,
    parse_source,
    parser_for,
)


def _legitimate_deep_python() -> bytes:
    """Nested defs a human might write; well under MAX_BYTES and the 5s budget."""
    depth = 40
    lines = [
        "TABLE = ('module', 'residue', 'legitimate-deep')\n",
        "def outer():\n",
    ]
    for i in range(depth):
        lines.append("    " * (i + 1) + f"def nest_{i}():\n")
    lines.append("    " * (depth + 1) + "return TABLE\n")
    return "".join(lines).encode()


def _pathological_nested_python() -> bytes:
    """Size-capped, pathologically nested expression inside a real function.

    A completed parse emits symbol `bomb`. A timed-out parse must not.
    """
    return b"def bomb():\n    return " + b"(" * 80_000 + b"1" + b")" * 80_000 + b"\n"


class _HangingParser:
    """A binding that ignores timeout: bytes parse sleeps; a reader is consumed."""

    def parse(self, source, **_kwargs):
        if callable(source):
            point = SimpleNamespace(row=0, column=0)
            offset = 0
            while True:
                chunk = source(offset, point)
                if not chunk:
                    break
                offset += len(chunk)
            time.sleep(30)
            raise AssertionError("reader timeout should have fired before EOF")
        time.sleep(30)
        raise AssertionError("unbounded bytes parse: native timeout missing, use reader")


def test_iss81_default_timeout_is_five_seconds():
    """Conservative default: 5s, not a sub-second value that clips generated files."""
    assert PARSE_TIMEOUT_MICROS == 5_000_000


def test_iss81_legitimate_deep_file_does_not_false_positive():
    src = _legitimate_deep_python()
    assert len(src) < 1_500_000
    t0 = time.monotonic()
    pf = parse_source(src, "python")
    assert time.monotonic() - t0 < 2.0
    names = {s.name for s in pf.symbols}
    assert "outer" in names
    assert "nest_0" in names
    assert "nest_39" in names
    assert pf.parse_errors == 0


def test_iss81_unbounded_pathological_fixture_emits_bomb():
    """Detector half: the fixture is real. timeout_micros=0 disables the bound."""
    src = _pathological_nested_python()
    assert len(src) < 1_500_000
    pf = parse_source(src, "python", timeout_micros=0)
    assert any(s.name == "bomb" for s in pf.symbols)


def test_iss81_pathological_nested_file_times_out_without_hanging():
    src = _pathological_nested_python()
    t0 = time.monotonic()
    pf = parse_source(src, "python", timeout_micros=1_000)
    assert time.monotonic() - t0 < 2.0
    assert {s.name for s in pf.symbols} == set()
    assert pf.imports == []
    assert pf.parse_errors >= 1


def test_iss81_hanging_parser_is_aborted_via_reader_fallback(monkeypatch):
    """When the binding ignores timeout, the reader fallback must still abort.

    A thread join is the hang detector: if `_parse_tree` goes back to
    `parser.parse(source)` with raw bytes, `_HangingParser` sleeps 30s and
    this fails at 2s. The old `parse_source is parse_source` style of test
    would stay green through that regression.
    """
    import repo2graph.parse as parse_mod

    monkeypatch.setattr(parse_mod, "parser_for", lambda lang: _HangingParser())
    monkeypatch.setattr(parse_mod, "_apply_native_timeout", lambda *_a, **_k: False)

    result = []
    errors = []

    def run():
        try:
            result.append(
                parse_source(_pathological_nested_python(), "python", timeout_micros=1_000)
            )
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(2.0)
    assert not thread.is_alive()
    assert errors == []
    assert result[0].symbols == []
    assert result[0].parse_errors >= 1


def test_iss81_timeout_resets_cached_parser():
    """A timed-out parse must not poison the lru-cached parser for the next file."""
    parse_source(_pathological_nested_python(), "python", timeout_micros=1_000)
    pf = parse_source(b"def greet():\n    return 1\n", "python")
    assert [s.name for s in pf.symbols] == ["greet"]
    assert pf.parse_errors == 0


def test_iss81_cpp_fallback_parse_is_also_bounded(monkeypatch):
    """The cpp second-pass `parser.parse` is the other parse entry point."""
    import repo2graph.parse as parse_mod
    from unittest.mock import MagicMock

    calls = []
    real = parse_mod._parse_tree

    def wrapped(parser, source, timeout_micros, deadline=None):
        calls.append(len(source))
        return real(parser, source, timeout_micros, deadline=deadline)

    monkeypatch.setattr(parse_mod, "_parse_tree", wrapped)

    def mock_run(cmd, **_kwargs):
        if "--version" in cmd:
            return MagicMock(returncode=0)
        return MagicMock(returncode=0, stdout=b"int main() { return 0; }")

    monkeypatch.setattr(parse_mod.subprocess, "run", mock_run)
    source = b"#define MACRO { error \nint main() MACRO }"
    pf = parse_source(source, "c", filepath="test.c")
    assert pf.parse_errors == 0
    assert pf.used_cpp
    assert len(calls) == 2


def test_iss81_grammar_survey_every_lang_cfg_has_a_parser():
    """Survey target: every configured grammar must be loadable to have a bound."""
    for lang in LANG_CFG:
        assert parser_for(lang) is not None, lang
