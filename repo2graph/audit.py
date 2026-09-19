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
    # shape of a credential. Deliberately conservative: real queries are prose
    # and contain spaces, so this does not fire on ordinary arguments.
    if len(value) >= ENTROPY_MIN_LEN and re.fullmatch(r"[A-Za-z0-9+/=_-]+", value):
        digits = sum(c.isdigit() for c in value)
        letters = sum(c.isalpha() for c in value)
        if digits and letters:
            return "high_entropy"
    return None


def sanitize_value(key: str, value: Any) -> Any:
    """Redact one parameter value, recursing into containers.

    Args:
        key: The field name this value arrived under; matched against
            SECRET_KEY_RE so a credential in an obviously-named field is caught
            even when its shape is unremarkable.
        value: Any JSON-compatible value.

    Returns:
        The value with anything secret-looking replaced, and long strings cut.
    """
    if isinstance(value, dict):
        return {k: sanitize_value(str(k), v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_value(key, v) for v in value]
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


class _LockedAppender:
    """Append-only writer that survives several processes sharing one file.

    Locking is advisory and per-write: the lock is taken, one whole line is
    written and flushed, and the lock is released. Two servers configured with
    the same `--audit-log` therefore interleave whole records rather than
    shredding each other's lines.

    Args:
        path: File to append to; created if absent.
    """

    # One warning per process: unlockable filesystems (NFS without lockd,
    # some FUSE and container overlay mounts) fall through to an unlocked
    # write, and repeating that on every record is noise, not signal.
    _lock_unavailable_warned: bool = False

    def __init__(self, path: Any) -> None:
        self.path = str(path)
        self._lock = threading.Lock()
        # File offset of the byte locked by _acquire, so _release unlocks the
        # same one. None when no OS-level lock is held.
        self._locked_at: int | None = None
        self._fh = open(self.path, "a", encoding="utf8", errors="replace", newline="\n")

    def write(self, line: str) -> None:
        """Append one line, holding an OS-level lock for the write."""
        with self._lock:
            try:
                self._acquire()
                try:
                    self._fh.write(line + "\n")
                    self._fh.flush()
                    os.fsync(self._fh.fileno())
                finally:
                    self._release()
            except Exception:
                # An audit sink that cannot be written must not take the server
                # with it; the stderr copy is still emitted by the caller.
                return

    def _acquire(self) -> None:
        # sys.platform branches, not a bare try/except ImportError: mypy checks
        # each platform's CI job against that job's own sys.platform, so it
        # statically knows the other branch is unreachable there and needs no
        # ignore comment on either platform.
        if sys.platform != "win32":
            try:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
                return
            except (ImportError, OSError) as exc:
                # Matching win32: an unlockable file must fall through to
                # the unlocked write below. fcntl.flock raises OSError
                # (ENOTSUP) on filesystems without advisory locks; catching
                # only ImportError let that escape into write()'s
                # except Exception and silently drop the record.
                self._warn_lock_unavailable(exc)
        if sys.platform == "win32":
            try:
                import msvcrt

                # msvcrt.locking locks a byte range starting at the *current*
                # position, so the offset has to be remembered: the write moves
                # the file pointer, and unlocking at the new position would
                # leave the original byte locked forever -- which on Windows
                # makes the file unreadable by every other process, including
                # the one auditing it.
                self._fh.seek(0, os.SEEK_END)
                self._locked_at = self._fh.tell()
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
                return
            except (ImportError, OSError):
                pass
        # No lock available: still write. An interleaved line is a far
        # smaller problem than a dropped audit record.
        self._locked_at = None

    def _warn_lock_unavailable(self, exc: ImportError | OSError) -> None:
        """Emit one process-wide warning when advisory locking is unavailable."""
        if _LockedAppender._lock_unavailable_warned:
            return
        _LockedAppender._lock_unavailable_warned = True
        errno: int | None = exc.errno if isinstance(exc, OSError) else None
        emit(
            "audit_lock_unavailable",
            path=self.path,
            errno=errno,
            error=str(exc),
            action="writing unlocked; records kept rather than dropped",
        )

    def _release(self) -> None:
        if sys.platform != "win32":
            try:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                return
            except (ImportError, OSError):
                pass
        if self._locked_at is None:
            return
        if sys.platform == "win32":
            try:
                import msvcrt

                self._fh.seek(self._locked_at)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                self._fh.seek(0, os.SEEK_END)
            except (ImportError, OSError):
                pass
        self._locked_at = None

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


@dataclass
class AuditConfig:
    """Where audit records go and which ones are kept.

    Attributes:
        level: "none", "errors" (rejections and failures only) or "all".
        path: Optional file to append to in addition to stderr.
    """

    level: str = "all"
    path: str | None = None


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
        self._file = _LockedAppender(self.config.path) if self.config.path else None

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
        record: dict[str, Any] = {
            "ts": timestamp(),
            "event": event,
            "tool": tool,
            "params": sanitize_params(params),
            "identity": identity,
            "outcome": outcome,
            "duration_ms": int(duration_ms),
            "result_tokens": int(result_tokens),
            "error": sanitize_value("error", error) if error is not None else None,
        }
        try:
            line = json.dumps(record, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            record = {
                "ts": record["ts"],
                "event": event,
                "tool": tool,
                "params": {},
                "identity": identity,
                "outcome": outcome,
                "duration_ms": int(duration_ms),
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
