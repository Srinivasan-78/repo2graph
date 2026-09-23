"""Robust cross-platform file locking for concurrent builds and readers.

Implements exclusive build locking, owner metadata, timeout with backoff,
stale lock detection, and clean recovery.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import time
from pathlib import Path
from types import TracebackType
from typing import Any, TextIO

DEFAULT_LOCK_TIMEOUT = 60.0
DEFAULT_STALE_THRESHOLD = 3600.0  # 1 hour
LOCK_RETRY_INTERVAL = 0.1


def _is_pid_alive(pid: int) -> bool:
    """Return True if a process with `pid` is currently running on the local host."""
    if pid <= 0:
        return False
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
    else:
        # Windows process check
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = getattr(ctypes, "windll").kernel32
            # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            SYNCHRONIZE = 0x00100000
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, wintypes.DWORD(pid)
            )
            if not handle:
                return False
            exit_code = wintypes.DWORD()
            # STILL_ACTIVE = 259
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                kernel32.CloseHandle(handle)
                return bool(exit_code.value == 259)
            kernel32.CloseHandle(handle)
            return False
        except Exception:
            return True


class LockTimeoutError(TimeoutError):
    """Raised when an index build lock cannot be acquired within the timeout."""


class BuildLock:
    """Cross-platform advisory file lock for index builds."""

    def __init__(
        self,
        outdir: str | Path,
        *,
        timeout: float = DEFAULT_LOCK_TIMEOUT,
        stale_threshold: float = DEFAULT_STALE_THRESHOLD,
    ):
        target = Path(outdir).resolve()
        # Sibling lock file: persists across directory swaps and protects clean targets
        self.lock_file = target.parent / f".{target.name}.r2glock"
        self.timeout = float(timeout)
        self.stale_threshold = float(stale_threshold)
        self._fh: TextIO | None = None
        self._acquired = False

    def acquire(self) -> None:
        """Acquire the build lock, retrying until timeout."""
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        start_time = time.monotonic()

        while True:
            fh = None
            try:
                fh = open(self.lock_file, "a+", encoding="utf8")
                if self._try_os_lock(fh) and self._still_the_lock_file(fh):
                    self._fh = fh
                    self._write_metadata(fh)
                    self._acquired = True
                    return
                # Held by another process, or the name stopped pointing at the
                # file we locked. Either way this attempt did not win.
                self._release_os_lock(fh)
                fh.close()
            except OSError:
                if fh is not None:
                    try:
                        fh.close()
                    except Exception:
                        pass

            elapsed = time.monotonic() - start_time
            if elapsed >= self.timeout:
                holder_info = self._read_holder_metadata()
                raise LockTimeoutError(
                    f"Timed out after {self.timeout:.1f}s waiting for build lock on {self.lock_file}. "
                    f"Currently held by: {holder_info}{self._staleness_hint()}"
                )

            time.sleep(LOCK_RETRY_INTERVAL)

    def release(self) -> None:
        """Release the build lock and remove the lock file."""
        if not self._acquired or self._fh is None:
            return

        # Bound before the `try`, because the `finally` reads it: an exception
        # raised on the line that assigns it (`fileno()` on a handle somebody
        # else closed raises ValueError, which `_still_the_lock_file` does not
        # catch) would otherwise turn into a NameError in the `finally` and
        # mask it. False is the safe value -- it leaves a lock file behind,
        # which no longer means anything now the OS lock is the only authority.
        ours = False
        try:
            # Whether the name still points at our file has to be decided
            # while the descriptor is open; afterwards there is nothing left
            # to compare it against.
            ours = self._still_the_lock_file(self._fh)
            self._release_os_lock(self._fh)
        finally:
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None
            self._acquired = False
            try:
                # Only unlink a name that still refers to the file we locked.
                # Unlinking unconditionally deletes whatever is at the path,
                # which after a reclaim is somebody else's live lock -- and
                # removing it lets a third builder in alongside them.
                if ours:
                    self.lock_file.unlink(missing_ok=True)
            except OSError:
                pass

    def __enter__(self) -> BuildLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.release()

    def _still_the_lock_file(self, fh: TextIO) -> bool:
        """Whether `fh` is still the file this lock's *name* refers to.

        `flock`/`msvcrt.locking` attach to an open file, not to a path, so a
        lock survives its own name being unlinked -- and a lock taken on an
        already-unlinked file conflicts with nobody. That gives two ways for
        two builders to both believe they hold this lock:

        * `release()` unlinks the path. A waiter that opened the path just
          before that unlink then locks the now-nameless file successfully,
          while the next process along creates a fresh file at the same path
          and locks that one too.
        * any reclaim that unlinks a lock file out from under a live holder
          has the same effect (which is why the age-based reclaim is gone).

        Since `dump_all`'s directory swap runs under this lock, two holders
        means two concurrent swaps of one index. Comparing the descriptor's
        identity against the path closes the window: a mismatch means we
        locked something that is no longer the lock, so the attempt is
        discarded and retried.
        """
        try:
            locked = os.fstat(fh.fileno())
            named = os.stat(self.lock_file)
        except OSError:
            return False
        return (locked.st_dev, locked.st_ino) == (named.st_dev, named.st_ino)

    def _try_os_lock(self, fh: TextIO) -> bool:
        """Attempt non-blocking OS lock."""
        if sys.platform != "win32":
            try:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except (OSError, ImportError):
                return False
        else:
            try:
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except (OSError, ImportError):
                return False

    def _release_os_lock(self, fh: TextIO) -> None:
        """Release OS lock."""
        if sys.platform != "win32":
            try:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except (OSError, ImportError):
                pass
        else:
            try:
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except (OSError, ImportError):
                pass

    def _write_metadata(self, fh: TextIO) -> None:
        """Write current process info into lock file for diagnostics."""
        try:
            meta = {
                "pid": os.getpid(),
                "host": platform.node(),
                "created_at": time.time(),
                "command": sys.argv,
            }
            fh.seek(0)
            fh.truncate()
            fh.write(json.dumps(meta, indent=2) + "\n")
            fh.flush()
        except OSError:
            pass

    def _read_holder_metadata(self) -> dict[str, Any]:
        """Read owner metadata from lock file if readable."""
        try:
            if self.lock_file.exists():
                text = self.lock_file.read_text(encoding="utf8", errors="replace").strip()
                if text:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        return parsed
        except Exception:
            pass
        return {"file": str(self.lock_file)}

    def _staleness_hint(self) -> str:
        """A diagnostic suffix for the timeout message, or "".

        `stale_threshold` used to drive an unlink: a lock file older than the
        threshold was deleted and re-created, which displaced a *live* holder
        and let two builders run `dump_all`'s directory swap over one index.
        Nothing here reclaims any more -- the OS lock is the only authority on
        whether this lock is held, and it is released by the kernel when its
        holder dies, so a lock file left by a crash is already acquirable.
        What the threshold is still good for is telling an operator that the
        holder has been sitting on it implausibly long.
        """
        try:
            age = time.time() - self.lock_file.stat().st_mtime
        except OSError:
            return ""
        if age <= self.stale_threshold:
            return ""
        meta = self._read_holder_metadata()
        pid = meta.get("pid")
        alive = (
            _is_pid_alive(pid)
            if isinstance(pid, int) and meta.get("host") == platform.node()
            else None
        )
        # Three states, not two: `alive is None` means the holder is on another
        # host (or wrote no pid), which this process cannot check. Reporting
        # that as "not running" invites exactly the manual deletion the rest of
        # this message argues against.
        if alive is None:
            state = "on another host, so its liveness cannot be checked from here"
        else:
            state = "still running" if alive else "no longer running"
        return (
            f" The lock has been held for {age / 60:.0f} minutes and its holder is {state}; "
            f"if that process is wedged, stop it rather than deleting the lock file -- "
            f"deleting it while a build is live allows a second build into the same index."
        )
