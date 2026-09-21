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
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, TextIO

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

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
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
            # Check for stale lock metadata before opening
            if self._try_reclaim_stale():
                pass

            fh = None
            try:
                fh = open(self.lock_file, "a+", encoding="utf8")
                if self._try_os_lock(fh):
                    self._fh = fh
                    self._write_metadata(fh)
                    self._acquired = True
                    return
                # Lock held by another process
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
                    f"Currently held by: {holder_info}"
                )

            time.sleep(LOCK_RETRY_INTERVAL)

    def release(self) -> None:
        """Release the build lock and remove the lock file."""
        if not self._acquired or self._fh is None:
            return

        try:
            self._release_os_lock(self._fh)
        finally:
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None
            self._acquired = False
            try:
                if self.lock_file.exists():
                    self.lock_file.unlink()
            except OSError:
                pass

    def __enter__(self) -> BuildLock:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()

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

    def _read_holder_metadata(self) -> dict:
        """Read owner metadata from lock file if readable."""
        try:
            if self.lock_file.exists():
                text = self.lock_file.read_text(encoding="utf8", errors="replace").strip()
                if text:
                    return json.loads(text)
        except Exception:
            pass
        return {"file": str(self.lock_file)}

    def _try_reclaim_stale(self) -> bool:
        """If lock file has an unalive holder PID or is older than stale threshold, attempt removal."""
        try:
            if not self.lock_file.exists():
                return False
            mtime = self.lock_file.stat().st_mtime
            age = time.time() - mtime
            meta = self._read_holder_metadata()
            pid = meta.get("pid")
            host = meta.get("host")

            is_stale = False
            # If same host and process is dead, it is definitely stale
            if host and host == platform.node() and isinstance(pid, int):
                if not _is_pid_alive(pid):
                    is_stale = True

            # If age exceeds stale threshold
            if age > self.stale_threshold:
                is_stale = True

            if is_stale:
                try:
                    self.lock_file.unlink(missing_ok=True)
                    return True
                except OSError:
                    return False
        except Exception:
            pass
        return False


@contextmanager
def acquire_build_lock(
    outdir: str | Path,
    *,
    timeout: float = DEFAULT_LOCK_TIMEOUT,
    stale_threshold: float = DEFAULT_STALE_THRESHOLD,
) -> Generator[BuildLock, None, None]:
    """Context manager acquiring an exclusive build lock."""
    lock = BuildLock(outdir, timeout=timeout, stale_threshold=stale_threshold)
    lock.acquire()
    try:
        yield lock
    finally:
        lock.release()
