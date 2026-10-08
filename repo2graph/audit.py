"""One JSON line per tool call: who asked what, when, and how it went.

Written to stderr, never stdout. On the stdio transport stdout *is* the JSON-RPC
stream, and a single stray line ends the session.

The awkward requirement here is redaction, and it cuts against the point of an
audit log. An audit trail that records nothing useful is theatre, but one that
faithfully records `{"query": "AWS_SECRET_ACCESS_KEY=AKIA..."}` has copied a
secret out of a short-lived process and into a file that by design is kept,
shipped to a SIEM, and read by people who did not have it before. Two rules
resolve it:

* Values are redacted on *shape*, not on key name alone. A model can put a
  credential in any field, so a value that looks like a token is redacted
  wherever it appears.
* Redaction preserves enough to investigate with. A redacted value keeps its
  length and a short hash, so two occurrences of the same secret are visibly
  the same secret without the log containing either of them.

`exclude_secrets` path patterns are reused from `security._is_secret_path`, so a
path the retrieval layer refuses to return is also a path this layer refuses to
log -- one definition, not two that drift.
"""

import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal, TextIO

from .events import emit, timestamp, write_safe
from .security import (
    CONTENT_SECRET_PATTERNS,
    MAX_SANITIZE_DEPTH,
    MAX_VALUE_CHARS,
    REDACTION_HASH_CHARS,
    SECRET_KEY_RE,
    _classify_secret_shape,
    _fingerprint,
    redact,
    sanitize_params,
    sanitize_value,
)

ENTROPY_MIN_LEN = 24
SECRET_VALUE_PATTERNS = CONTENT_SECRET_PATTERNS
LEVELS = ("none", "errors", "all")

__all__ = [
    "AuditConfig",
    "AuditLogger",
    "CONTENT_SECRET_PATTERNS",
    "ENTROPY_MIN_LEN",
    "LEVELS",
    "MAX_SANITIZE_DEPTH",
    "MAX_VALUE_CHARS",
    "REDACTION_HASH_CHARS",
    "SECRET_KEY_RE",
    "SECRET_VALUE_PATTERNS",
    "_classify_secret_shape",
    "_fingerprint",
    "redact",
    "sanitize_params",
    "sanitize_value",
    "timer",
]


class _LockedAppender:
    """Thread-safe append-only writer for audit logs."""

    def __init__(self, path: Any, fsync: bool = False) -> None:
        self.path = str(path)
        self.fsync = fsync
        self._lock = threading.Lock()
        self._fh: TextIO | None = None
        self._write_failed = False
        try:
            parent = os.path.dirname(os.path.abspath(self.path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            self._fh = open(self.path, "a", encoding="utf8", errors="replace", newline="\n")
        except OSError as exc:
            emit(
                "audit_sink_unavailable",
                level="warning",
                path=self.path,
                error=f"{type(exc).__name__}: {exc}",
                detail="audit records go to stderr only",
            )

    def write(self, line: str) -> None:
        """Append one line to the audit sink."""
        fh = self._fh
        if fh is None:
            return
        with self._lock:
            try:
                fh.write(line + "\n")
                fh.flush()
                if self.fsync:
                    os.fsync(fh.fileno())
            except (OSError, ValueError) as exc:
                # A sink that stops accepting writes mid-run -- disk full, a
                # revoked permission, a dropped network share -- was previously
                # as silent as a working one, which is the wrong failure mode for
                # the component whose whole job is leaving a record. Reported
                # once: the next call would fail identically, and a per-call
                # warning would bury the stderr copy of the records themselves.
                self._fh = None
                if not self._write_failed:
                    self._write_failed = True
                    emit(
                        "audit_sink_write_failed",
                        level="warning",
                        path=self.path,
                        error=f"{type(exc).__name__}: {exc}",
                        detail="audit records go to stderr only from here on",
                    )

    def close(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            fh.close()
        except OSError:
            pass


@dataclass
class AuditConfig:
    """Where audit records go and which ones are kept.

    Attributes:
        level: "none", "errors" (rejections and failures only) or "all".
        path: Optional file to append to in addition to stderr.
        fsync: Sync the file sink to stable storage after every record. Off by
            default: flushing already makes the line whole for anything else
            reading the file, and the same record is on stderr regardless, so
            the cost of a disk sync per tool call buys only crash durability
            for the file copy. Deployments that need exactly that turn it on.
    """

    level: str = "all"
    path: str | None = None
    fsync: bool = False


class AuditLogger:
    """Emits one structured record per tool call.

    Args:
        config: Level and optional file sink.
        stream: Where the stderr copy goes; resolved at call time when None.
    """

    def __init__(self, config: AuditConfig | None = None, stream: TextIO | None = None) -> None:
        self.config = config or AuditConfig()
        if self.config.level not in LEVELS:
            raise ValueError(
                f"audit level must be one of {', '.join(LEVELS)}, got {self.config.level!r}"
            )
        self._stream = stream
        self._file: _LockedAppender | None = None
        if self.config.path:
            self._file = _LockedAppender(self.config.path, fsync=self.config.fsync)

    @property
    def enabled(self) -> bool:
        """False when the level is "none", in which case nothing is emitted."""
        return self.config.level != "none"

    def _should_emit(self, outcome: str) -> bool:
        if self.config.level == "none":
            return False
        if self.config.level == "errors":
            return outcome != "success"
        return True

    def record(
        self,
        tool: str,
        params: Any,
        identity: str = "anonymous",
        outcome: str = "success",
        duration_ms: int = 0,
        result_tokens: int = 0,
        error: str | None = None,
        event: str = "tool_call",
    ) -> dict[str, Any] | None:
        """Write one audit record.

        Args:
            tool: Tool name the caller asked for.
            params: The caller's arguments; sanitized before they are written.
            identity: Who made the call. The stdio server has no authentication
                step, so this is "anonymous" unless a caller supplies its own label.
            outcome: "success" or "error".
            duration_ms: Wall time the call took, in whole milliseconds.
            result_tokens: Size of the result handed back, in tokens.
            error: Message when `outcome` is "error", else None.
            event: Record type; "tool_call" unless a caller needs another.

        Returns:
            The record written, or None when the level suppressed it.
        """
        if not self._should_emit(outcome):
            return None
        ts = timestamp()
        # Coerced up front so the fallback record below cannot be the thing
        # that raises: an int() over a caller-supplied value belongs outside
        # the except clause that exists to survive caller-supplied values.
        try:
            duration, tokens = int(duration_ms), int(result_tokens)
        except (TypeError, ValueError):
            duration, tokens = 0, 0
        record: dict[str, Any]
        # Sanitisation is inside the try, not just the dump. Every input to it
        # is caller-controlled and this runs on the per-request path, so anything
        # raising here would cost the tool call its answer rather than just its
        # audit record. Broad on purpose: the contract is that an awkward
        # argument costs the record's contents, never the record and never the
        # request.
        try:
            record = {
                "ts": ts,
                "event": event,
                "tool": tool,
                "params": sanitize_params(params),
                "identity": identity,
                "outcome": outcome,
                "duration_ms": duration,
                "result_tokens": tokens,
                "error": sanitize_value("error", error) if error is not None else None,
            }
            line = json.dumps(record, ensure_ascii=False, default=str)
        except Exception as exc:  # noqa: BLE001 - resilience boundary: audit record sanitization/serialization fallback
            from .events import reraise_if_debug

            reraise_if_debug(exc)
            record = {
                "ts": ts,
                "event": event,
                "tool": tool,
                "params": {},
                "identity": identity,
                "outcome": outcome,
                "duration_ms": duration,
                "result_tokens": 0,
                "error": "audit record could not be serialised",
            }
            line = json.dumps(record)
        write_safe(sys.stderr if self._stream is None else self._stream, line)
        if self._file is not None:
            self._file.write(line)
        return record

    def close(self) -> None:
        """Close the file sink, if there is one."""
        if self._file is not None:
            self._file.close()
            self._file = None


class timer:
    """Context manager yielding elapsed milliseconds for an audit record.

    Example:
        >>> with timer() as t:
        ...     pass
        >>> t.ms >= 0
        True
    """

    def __init__(self) -> None:
        self.ms = 0
        self._start = 0.0

    def __enter__(self) -> "timer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc: Any) -> Literal[False]:
        # Never rounds a real call down to 0: a record showing zero duration
        # reads as "never ran", and telling those apart matters in an audit.
        elapsed = (time.perf_counter() - self._start) * 1000.0
        self.ms = max(1, int(round(elapsed)))
        return False
