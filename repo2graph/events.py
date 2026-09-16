# @authormark v1 -- do not remove (authorship watermark)
# Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
# Author: https://github.com/Srinivasan-78
# SPDX-License-Identifier: MIT
# Fingerprint: AMK1.ZrtrCdsqUvTL85XgvI0cDl
"""Structured events on stderr: one JSON object per line, never fatal.

stderr, never stdout. stdout carries either the answer a user is piping into a
file or the MCP server's JSON-RPC stream, and one stray line on either ends the
session. Everything here writes to stderr for that reason alone.

Two rules hold for every function in this module:

* **It cannot raise.** These are diagnostics. A logger that takes down the
  command it was reporting on is worse than no logger, so encoding failures,
  closed pipes and unserialisable payloads all degrade to something printable
  or to silence -- never to a traceback reaching the caller.
* **It cannot emit a partial line.** A SIEM tailing this parses line by line, so
  a record is assembled as one string and written once.

`write_safe` is the single stdout/stderr write path for the whole package. A
redirected Windows stdout is a cp1252 TextIOWrapper, and Git Bash hands a piped
one `errors='surrogateescape'` -- which still raises on any character cp1252
lacks. Both are handled here rather than at each call site.
"""
import json
import re
import sys
from datetime import datetime, timezone
from typing import Any, TextIO

# Error handlers that cannot raise: each one maps an unencodable character to a
# substitute. "surrogateescape"/"surrogatepass" are absent on purpose -- they
# round-trip lone surrogates but still raise on, say, U+2192 under cp1252, which
# is exactly the piped-Git-Bash case this guard exists for.
SAFE_ERRORS = frozenset({"replace", "backslashreplace", "xmlcharrefreplace",
                         "namereplace"})


def encodable(text: str, stream: Any) -> str:
    """Return `text` reduced to something `stream` is guaranteed to accept.

    The stream's encoding and error handler are read at call time, not at import
    time: tests replace `sys.stdout` after import, and a caller may reconfigure
    it mid-run.

    Args:
        text: The text about to be written.
        stream: The destination stream, inspected for `.encoding`/`.errors`.

    Returns:
        `text` unchanged when the stream can represent it, otherwise the same
        text with unrepresentable characters replaced.
    """
    errors = getattr(stream, "errors", "strict") or "strict"
    if errors in SAFE_ERRORS:
        return text
    enc = getattr(stream, "encoding", None) or "utf8"
    try:
        text.encode(enc, errors)
        return text
    except UnicodeEncodeError:
        try:
            return text.encode(enc, "replace").decode(enc, "replace")
        except (UnicodeDecodeError, LookupError):
            pass
    except LookupError:
        # The stream named an encoding Python does not have (S-12: Windows can
        # report "cp0"). That says nothing about what the stream can *accept*,
        # so round-trip through UTF-8 rather than flattening to ascii -- which
        # would replace characters like "é" that were never the problem.
        try:
            return text.encode("utf8", "replace").decode("utf8", "replace")
        except UnicodeDecodeError:
            pass
    except Exception:
        # A mock stream whose .encode path misbehaves must not become the
        # caller's problem; fall through to the ascii floor.
        pass
    # The floor: ascii always exists, and "replace" always terminates.
    return text.encode("ascii", "replace").decode("ascii", "replace")


_SENSITIVE_KV_RE = re.compile(
    r'(?i)\b((?:api|access|secret|private|auth|session|refresh)?_?key|token|password|passwd)\b'
    r'(\s*[:=]\s*)'
    r'(".*?"|\'.*?\'|[^,\s}\]]+)'
)
_BEARER_RE = re.compile(r'(?i)\b(Bearer\s+)([A-Za-z0-9._\-+/=]+)')


def _redact_sensitive_text(text: str) -> str:
    """Best-effort masking for common secret shapes in log text.

    This function must never raise; on any internal failure it returns `text`.
    """
    try:
        redacted = _SENSITIVE_KV_RE.sub(r"\1\2***REDACTED***", text)
        redacted = _BEARER_RE.sub(r"\1***REDACTED***", redacted)
        return redacted
    except Exception:
        return text


def write_safe(stream: Any, text: str, newline: str = "\n") -> None:
    """Write `text` to `stream`, replacing anything it cannot encode.

    Never raises: a UnicodeEncodeError, a closed stream or a broken pipe all end
    as silence rather than as an exception in the caller.

    Args:
        stream: Destination, typically `sys.stdout` or `sys.stderr`.
        text: The line to write, without a trailing newline.
        newline: Appended to `text`; pass "" to suppress it.
    """
    if stream is None:
        return
    safe_text = _redact_sensitive_text(text)
    try:
        stream.write(encodable(safe_text, stream) + newline)
    except (UnicodeEncodeError, UnicodeDecodeError):
        # encodable() should have prevented this; if a stream lied about its
        # encoding, fall back to the hardest floor there is.
        try:
            stream.write(safe_text.encode("ascii", "replace").decode("ascii") + newline)
        except Exception:
            return
    except Exception:
        return
    try:
        stream.flush()
    except Exception:
        pass


def timestamp() -> str:
    """An ISO-8601 UTC timestamp with millisecond precision.

    Returns:
        e.g. `"2026-09-16T10:31:07.482Z"`. Milliseconds rather than
        microseconds because that is what the audit record format specifies and
        what most SIEM ingesters expect.
    """
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def emit(event: str, level: str = "warning", stream: TextIO | None = None,
         **fields: Any) -> dict[str, Any]:
    """Write one structured event as a single JSON line on stderr.

    Args:
        event: Machine-readable event name, e.g. `"rag_fusion_disabled"`.
        level: Severity label; "warning" by default.
        stream: Destination; defaults to `sys.stderr` resolved at call time.
        **fields: Additional JSON-serialisable fields merged into the record.

    Returns:
        The record that was emitted, so callers and tests can assert on it
        without re-parsing stderr.
    """
    record: dict[str, Any] = {"ts": timestamp(), "level": level,
                              "event": event}
    record.update(fields)
    try:
        line = json.dumps(record, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        # A field that will not serialise must not lose the whole event.
        line = json.dumps({"ts": record["ts"], "level": level, "event": event,
                           "error": "unserialisable event fields"})
    write_safe(sys.stderr if stream is None else stream, line)
    return record
