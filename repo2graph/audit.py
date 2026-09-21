# @authormark v1 -- do not remove (authorship watermark)⁠​‌‌​‌‌‌‌​‌‌​‌​​​​‌‌​‌‌‌‌​​‌‌​​‌‌​‌​​‌‌‌​​‌‌‌​​‌‌​‌​‌​​‌‌​‌​‌​‌‌‌​‌‌​‌​​​​‌​​‌‌​‌​‌‌​​​‌​​‌​‌​​​​​‌‌​‌​​‌​‌‌​‌‌​​​‌​​​‌‌‌​‌​‌​​‌‌​​‌‌​‌​​​‌‌‌​‌‌​​‌​‌​‌​​​‌‌​​‌‌​​​‌‌​‌‌​​‌​‌​​‌‌⁠
# Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
# Author: https://github.com/Srinivasan-78
# SPDX-License-Identifier: MIT
# Fingerprint: AMK1.oho3NsSWhMbPilGS4vTf6S
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

import hashlib
import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal, TextIO

from .events import emit, timestamp, write_safe

# How much of a redacted value's hash is kept. Enough to correlate two
# occurrences, far too little to attack the original.
REDACTION_HASH_CHARS = 8
# Values longer than this are truncated in the log regardless of content: a
# model can paste a whole file into an argument, and an audit line is a record
# of the call, not a copy of its payload.
MAX_VALUE_CHARS = 512
# Strings at least this long made only of token-ish characters are treated as
# credentials even if nothing about their key says so.
ENTROPY_MIN_LEN = 24
# How deep sanitisation follows nested containers before it stops and says so.
# No real tool argument is a dict twelve levels down; a caller-supplied one
# that is has stopped being an argument and become a way to exhaust the
# interpreter's C stack. Recursion here is driven entirely by untrusted input,
# and the RecursionError it raises escapes `record()` into call paths that do
# not expect an audit write to fail -- an auth rejection has no `try` around it
# at all -- so the bound is part of the contract, not an optimisation.
MAX_SANITIZE_DEPTH = 12

LEVELS = ("none", "errors", "all")

# Field names whose *value* is a credential whatever it looks like.
SECRET_KEY_RE = re.compile(
    r"(pass(word|wd)?|secret|token|api[-_]?key|auth|credential|private[-_]?key"
    r"|session|cookie|bearer|signature|access[-_]?key)",
    re.I,
)

# Value shapes that are credentials wherever they appear. Ordered most specific
# first; the first match wins and names what was found.
SECRET_VALUE_PATTERNS = (
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "jwt",
        re.compile(
            r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
            r"[A-Za-z0-9_-]{8,}\b"
        ),
    ),
    ("basic_auth_url", re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")),
    (
        "assignment",
        re.compile(
            r"(?i)\b(?:pass(?:word|wd)?|secret|token|api[-_]?key)"
            r"\s*[=:]\s*\S{6,}"
        ),
    ),
)


def _fingerprint(value: str) -> str:
    """A short, stable, non-reversible tag for a redacted value."""
    digest = hashlib.blake2b(value.encode("utf8", "surrogateescape"), digest_size=16).hexdigest()
    return digest[:REDACTION_HASH_CHARS]


def redact(value: str, why: str) -> str:
    """Replace a secret with a tag that is still useful in an investigation.

    Args:
        value: The secret.
        why: What matched, e.g. "github_token" or "key:password".

    Returns:
        e.g. `"[redacted:github_token len=40 fp=1a2b3c4d]"`. The length and
        fingerprint let an investigator correlate occurrences and spot a
        rotation without the log ever holding the value itself.
    """
    return f"[redacted:{why} len={len(value)} fp={_fingerprint(value)}]"


def _looks_like_a_secret(value: str) -> str | None:
    """Name the credential shape `value` matches, or None."""
    for name, pattern in SECRET_VALUE_PATTERNS:
        if pattern.search(value):
            return name
    # A long unbroken run of token characters with no whitespace is the generic
    # shape of a credential — but only when the mix is stronger than an
    # ordinary identifier. `_` is in the class, so a snake_case name of
    # ENTROPY_MIN_LEN+ with one digit (`iss25`, `python3`) used to match.
    # Those are the pinpoint `repo_search` queries worth logging. No
    # base64/hex credential is lowercase-and-underscore only: those use
    # mixed case or the `+/=` padding alphabet. Vendor prefixes (AKIA,
    # ghp_, sk-, eyJ…) are caught above, before this gate.
    if len(value) >= ENTROPY_MIN_LEN and re.fullmatch(r"[A-Za-z0-9+/=_-]+", value):
        if re.fullmatch(r"[a-z0-9_]+", value):
            return None
        digits = sum(c.isdigit() for c in value)
        letters = sum(c.isalpha() for c in value)
        if digits and letters:
            return "high_entropy"
    return None


def sanitize_value(key: str, value: Any, *, depth: int = 0) -> Any:
    """Redact one parameter value, recursing into containers.

    Args:
        key: The field name this value arrived under; matched against
            SECRET_KEY_RE so a credential in an obviously-named field is caught
            even when its shape is unremarkable.
        value: Any JSON-compatible value.
        depth: How many containers deep this call already is. Callers leave it
            at 0; only the recursion below passes anything else.

    Returns:
        The value with anything secret-looking replaced, and long strings cut.
        A container nested past MAX_SANITIZE_DEPTH becomes
        `"[truncated:depth]"` -- the record still exists and still says that
        something was there, which is the whole point of the log.
    """
    # The ceiling is tested only where the recursion actually happens, so a
    # scalar sitting at the limit is still redacted normally. Truncating those
    # too would throw away the one thing at that depth worth reading.
    if isinstance(value, dict):
        if depth >= MAX_SANITIZE_DEPTH:
            return "[truncated:depth]"
        return {k: sanitize_value(str(k), v, depth=depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        if depth >= MAX_SANITIZE_DEPTH:
            return "[truncated:depth]"
        return [sanitize_value(key, v, depth=depth + 1) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value
    else:
        # str() runs caller-supplied __str__/__repr__, which can raise. An
        # audit logger that dies on an awkward argument loses the record of
        # exactly the call worth having a record of.
        try:
            text = str(value)
        except Exception:
            return f"[unprintable:{type(value).__name__}]"

    if SECRET_KEY_RE.search(key or ""):
        return redact(text, f"key:{key}")
    shape = _looks_like_a_secret(text)
    if shape:
        return redact(text, shape)
    # A path the retrieval layer would refuse to return must not be logged
    # either: the same definition governs both, so they cannot drift apart.
    from .query import _is_secret_path

    if ("/" in text or "\\" in text) and _is_secret_path(text):
        return f"[redacted:secret_path fp={_fingerprint(text)}]"
    if len(text) > MAX_VALUE_CHARS:
        return text[:MAX_VALUE_CHARS] + f"…[+{len(text) - MAX_VALUE_CHARS} chars]"
    return text


def sanitize_params(params: Any) -> dict[str, Any]:
    """Sanitize a whole tool-argument mapping.

    Args:
        params: The arguments a caller sent, or anything else.

    Returns:
        A dict safe to write to a log that will be kept and shipped onward.
    """
    if not isinstance(params, dict):
        return {"_": sanitize_value("", params)} if params else {}
    return {str(k): sanitize_value(str(k), v) for k, v in params.items()}


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
