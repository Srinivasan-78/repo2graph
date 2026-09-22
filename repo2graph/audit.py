"""One JSON line per tool call: who asked what, when, and how it went.

Written to stderr, never stdout. On the stdio transport stdout *is* the JSON-RPC
stream and a single stray line ends the session; on the HTTP transport stdout is
still where a human piping the process expects its output. stderr is the only
channel that is safe in both.

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

`exclude_secrets` path patterns are reused from `query._is_secret_path`, so a
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
from .secrets import (
    CONTENT_SECRET_PATTERNS,
    ENTROPY_MIN_LEN,
    MAX_SANITIZE_DEPTH,
    MAX_VALUE_CHARS,
    REDACTION_HASH_CHARS,
    SECRET_KEY_RE,
    _fingerprint,
    _looks_like_a_secret,
    redact,
    sanitize_params,
    sanitize_value,
)

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
    "_fingerprint",
    "_looks_like_a_secret",
    "redact",
    "sanitize_params",
    "sanitize_value",
    "timer",
]


# Whether this process has already said that advisory locking is unavailable.
# The condition is a property of the filesystem, not of the record, so warning
# per write would reproduce the audit log on stderr at the same rate and bury
# the one line an operator needs to see. Guarded by its own lock because two
# _LockedAppenders on different files have different instance locks.
_lock_warning_lock = threading.Lock()
_lock_warning_sent = False


def _warn_locking_unavailable(path: str, exc: BaseException | None) -> None:
    """Say once that records are being appended without an advisory lock.

    Records are still written -- that is the point -- but they are no longer
    serialised against other processes sharing the file, and an operator
    reading a shredded line later deserves to know why rather than to guess.

    Args:
        path: The audit sink the lock could not be taken on.
        exc: What the locking call raised, when it raised at all.
    """
    global _lock_warning_sent
    with _lock_warning_lock:
        if _lock_warning_sent:
            return
        _lock_warning_sent = True
    emit(
        "audit_lock_unavailable",
        level="warning",
        path=path,
        error=None if exc is None else f"{type(exc).__name__}: {exc}",
        detail="audit records are still written, but concurrent writers may interleave lines",
    )


class _LockedAppender:
    """Append-only writer that survives several processes sharing one file.

    Locking is advisory and per-write: the lock is taken, one whole line is
    written and flushed, and the lock is released. Two servers configured with
    the same `--audit-log` therefore interleave whole records rather than
    shredding each other's lines.

    A sink that cannot be opened at all leaves `_fh` None and every method a
    no-op. The constructor holds the same line `write` does: an audit file the
    operator mistyped is a degraded log, not a server that refuses to start,
    and the stderr copy of every record still flows.

    Args:
        path: File to append to; created if absent, as is its parent directory.
        fsync: Force each record to stable storage before returning. Off by
            default -- see `write`.
    """

    def __init__(self, path: Any, fsync: bool = False) -> None:
        self.path = str(path)
        self.fsync = fsync
        self._lock = threading.Lock()
        # File offset of the byte locked by _acquire, so _release unlocks the
        # same one. None when no OS-level lock is held.
        self._locked_at: int | None = None
        self._fh: TextIO | None = None
        try:
            parent = os.path.dirname(os.path.abspath(self.path))
            if parent:
                # `--audit-log logs/audit.log` on a fresh checkout is a typo
                # only in the sense that the directory has not been made yet.
                os.makedirs(parent, exist_ok=True)
            self._fh = open(self.path, "a", encoding="utf8", errors="replace", newline="\n")
        except OSError as exc:
            # Once, at construction: there is nothing to retry, and the caller
            # is about to start a server that will otherwise look healthy.
            emit(
                "audit_sink_unavailable",
                level="warning",
                path=self.path,
                error=f"{type(exc).__name__}: {exc}",
                detail="audit records go to stderr only",
            )

    def write(self, line: str) -> None:
        """Append one line, holding an OS-level lock for the write."""
        fh = self._fh
        if fh is None:
            return
        with self._lock:
            try:
                self._acquire(fh)
                try:
                    fh.write(line + "\n")
                    # flush(), not fsync(), by default. flush() hands the whole
                    # line to the OS, which is all the interleaving guarantee
                    # above needs: another process reading or appending sees a
                    # complete record. fsync() additionally waits for the disk,
                    # and it was being paid per record while holding both this
                    # thread lock and the OS-level file lock -- so on the
                    # ThreadingHTTPServer transport every concurrent request
                    # queued behind a disk sync. What that buys is durability
                    # across a machine crash, and the record is written to
                    # stderr unconditionally anyway, so a crash between flush
                    # and fsync loses the file copy and not the record. A
                    # deployment whose file sink *is* the record of last resort
                    # turns it back on with AuditConfig(fsync=True).
                    fh.flush()
                    if self.fsync:
                        os.fsync(fh.fileno())
                finally:
                    self._release(fh)
            except Exception:
                # An audit sink that cannot be written must not take the server
                # with it; the stderr copy is still emitted by the caller.
                return

    def _acquire(self, fh: TextIO) -> None:
        # sys.platform branches, not a bare try/except ImportError: mypy checks
        # each platform's CI job against that job's own sys.platform, so it
        # statically knows the other branch is unreachable there and needs no
        # ignore comment on either platform.
        failure: BaseException | None = None
        if sys.platform != "win32":
            try:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                return
            except (ImportError, OSError) as exc:
                # OSError, not just ImportError, and for the same reason the
                # win32 branch below has always caught it: flock fails on a
                # filesystem with no advisory locking -- NFS without lockd,
                # several FUSE and overlay mounts. Letting that escape into
                # write()'s `except Exception: return` dropped the record from
                # the file sink entirely while the stderr copy still appeared,
                # so the two sinks disagreed and nothing said so.
                failure = exc
        if sys.platform == "win32":
            try:
                import msvcrt

                # msvcrt.locking locks a byte range starting at the *current*
                # position, so the offset has to be remembered: the write moves
                # the file pointer, and unlocking at the new position would
                # leave the original byte locked forever -- which on Windows
                # makes the file unreadable by every other process, including
                # the one auditing it.
                fh.seek(0, os.SEEK_END)
                self._locked_at = fh.tell()
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                return
            except (ImportError, OSError) as exc:
                failure = exc
        # No lock available: still write. An interleaved line is a far
        # smaller problem than a dropped audit record.
        self._locked_at = None
        _warn_locking_unavailable(self.path, failure)

    def _release(self, fh: TextIO) -> None:
        if sys.platform != "win32":
            try:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                return
            except (ImportError, OSError):
                # Symmetric with _acquire: on a filesystem that refused the
                # lock, releasing it refuses too, and that must not reach
                # write() -- the line is already on disk by now.
                pass
        if self._locked_at is None:
            return
        if sys.platform == "win32":
            try:
                import msvcrt

                fh.seek(self._locked_at)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                fh.seek(0, os.SEEK_END)
            except (ImportError, OSError):
                pass
        self._locked_at = None

    def close(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            fh.close()
        except Exception:
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
            identity: `sub` claim under OIDC, else "bearer" or "anonymous".
            outcome: "success", "auth_rejected" or "error".
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
        except Exception:
            duration, tokens = 0, 0
        record: dict[str, Any]
        # Sanitisation is inside the try, not just the dump. Every input to it
        # is caller-controlled, and this method is called from places that have
        # no `except` of their own -- http_server._reject runs on an auth
        # failure, outside any guard, so anything raising here takes the
        # handler thread down with no response at all. Broad on purpose: the
        # contract is that an awkward argument costs the record's contents,
        # never the record and never the request.
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
        except Exception:
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
