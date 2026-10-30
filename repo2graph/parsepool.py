"""Supervised parse workers: a per-file parse budget that can actually stop a parse (#81).

tree-sitter cannot be interrupted from Python. py-tree-sitter 0.25 dropped
`Parser.timeout_micros`; 0.26 ignores `progress_callback` when given bytes and
segfaults when given one alongside a read callback; and `Parser.parse` holds the
GIL, so a watchdog thread in the same process never gets to run. The only bound
that holds is a process that can be killed, so a file that may be expensive is
parsed in a worker the parent watches, and the parent kills the worker when the
file runs over budget.

The budget is not hypothetical. `x = a<b<a<b<...;` as C++ is quadratic in both
time and memory: 32 KB takes 1 s and 640 MB, 300 KB was killed by the OOM killer
at 13.8 GB. Hence two bounds per worker -- wall clock (`PARSE_TIMEOUT_S`, enforced
by the parent) and memory (`WORKER_MEMORY_MB`, enforced by supervisor RSS polling
across macOS, Linux and Windows, supplemented on Linux by `RLIMIT_AS` and on Windows
by Job Objects).

Calibration: across the five benchmark corpora (Django, and the indexed scopes of
Kubernetes, TensorFlow, VS Code and Linux; 18,608 files) the slowest single
parse was 0.74 s (linux/kernel/bpf/verifier.c, 657 KB) and the largest peak RSS
under 100 MB (a 1.2 MB VS Code test file), so the defaults leave more than 10x
headroom on both.

A file that runs over its budget, or takes its worker down, is skipped the way
an oversized file is: it keeps its file node, contributes no symbols, and is
counted (`skipped_parse_timeout` / `skipped_parse_crash`) rather than silently
truncated.
"""

from __future__ import annotations

import concurrent.futures
import ctypes
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from concurrent.futures.process import BrokenProcessPool
from ctypes import wintypes
from typing import Any

#: Default wall-clock budget for reading and parsing one file, in seconds.
PARSE_TIMEOUT_S = 10.0
#: Memory budget in MB per worker process across platforms.
WORKER_MEMORY_MB = 2048
#: Files at most this large may be parsed in the calling process when the build
#: is serial. Larger ones always go to a supervised worker: the quadratic input
#: above costs 0.3 s and 200 MB at 16 KB, so this caps what an unsupervised
#: parse can cost.
INPROCESS_MAX_BYTES = 16 * 1024

#: A pool with work queued in which no worker starts up, finishes an item or
#: starts one for this long (or three parse budgets, if longer) is abandoned as
#: wedged. Generous: a starved Windows runner can take a minute to spawn workers.
STALL_S = 120.0

TIMEOUT = "timeout"
CRASH = "crash"

# Per worker slot in the shared watch array: pid, index of the item being
# parsed, and when it started (time.monotonic, which is system-wide on every
# platform CPython supports). A start of 0 means idle.
_FIELDS = 3
_slot = -1
_watch: Any = None


def _context() -> Any:
    """Where workers come from: forkserver on POSIX, the platform default elsewhere.

    Not plain fork: the parent is often multi-threaded (pytest-xdist, the MCP
    server, a previous pool's management threads), and a child forked while
    another thread holds a lock can wedge before it runs a line. forkserver
    forks from a clean single-threaded server instead, and preloading the
    parser module keeps each worker's start-up cheap.
    """
    import multiprocessing

    if sys.platform != "win32" and "forkserver" in multiprocessing.get_all_start_methods():
        ctx = multiprocessing.get_context("forkserver")
        ctx.set_forkserver_preload(["repo2graph.graph"])
        return ctx
    return multiprocessing.get_context()


class PoolUnavailable(RuntimeError):
    """The pool broke before it parsed anything: no workers to supervise."""


def claim_slot(watch: Any) -> None:
    """In a new worker: take a free slot in `watch` and cap this process's memory.

    `watch` is `(shared array, memory cap in MB)` as `_round` builds it: the cap
    travels with it because a worker started by forkserver or spawn sees this
    module's defaults, not whatever the parent set.
    """
    global _slot, _watch
    watch, memory_mb = watch
    with watch.get_lock():
        for k in range(len(watch) // _FIELDS):
            if watch[k * _FIELDS] == 0:
                watch[k * _FIELDS] = os.getpid()
                _slot, _watch = k, watch
                break
    _cap_memory(memory_mb)


_worker_job: int | None = None


def _cap_memory(memory_mb: int) -> None:
    """Bound this worker's address space, so a runaway parse kills only it."""
    global _worker_job
    if sys.platform == "win32":
        try:
            from .parse import _win_cap_child_memory

            _worker_job = _win_cap_child_memory(os.getpid(), memory_mb * 1024 * 1024)
        except (ImportError, OSError, ValueError, AttributeError):
            pass
        return
    if not sys.platform.startswith("linux"):
        return  # macOS does not enforce RLIMIT_AS; bounded via supervisor RSS polling
    try:
        import resource

        with open("/proc/self/statm", encoding="ascii") as fh:
            current = int(fh.read().split()[0]) * os.sysconf("SC_PAGE_SIZE")
        limit = current + memory_mb * 1024 * 1024
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        if hard != resource.RLIM_INFINITY:
            limit = min(limit, hard)
        if soft == resource.RLIM_INFINITY or soft > limit:
            resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
    except (ImportError, OSError, ValueError, IndexError):
        pass  # best effort: the wall-clock budget still applies


class _ProcTaskInfo(ctypes.Structure):
    _fields_ = [
        ("pti_virtual_size", ctypes.c_uint64),
        ("pti_resident_size", ctypes.c_uint64),
        ("pti_total_user", ctypes.c_uint64),
        ("pti_total_system", ctypes.c_uint64),
        ("pti_threads_user", ctypes.c_uint64),
        ("pti_threads_system", ctypes.c_uint64),
        ("pti_policy", ctypes.c_int32),
        ("pti_faults", ctypes.c_int32),
        ("pti_pageins", ctypes.c_int32),
        ("pti_cow_faults", ctypes.c_int32),
        ("pti_messages_sent", ctypes.c_int32),
        ("pti_messages_received", ctypes.c_int32),
        ("pti_syscalls_mach", ctypes.c_int32),
        ("pti_syscalls_unix", ctypes.c_int32),
        ("pti_csw", ctypes.c_int32),
        ("pti_threadnum", ctypes.c_int32),
        ("pti_numrunning", ctypes.c_int32),
        ("pti_priority", ctypes.c_int32),
    ]


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


_PROC_PIDTASKINFO = 4
_darwin_libproc_fn: Any = None


def _get_darwin_proc_pidinfo() -> Any:
    global _darwin_libproc_fn
    if _darwin_libproc_fn is not None:
        return _darwin_libproc_fn
    try:
        import ctypes.util

        path = ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib"
        try:
            lib = ctypes.CDLL(path)
        except OSError:
            lib = ctypes.CDLL("libproc.dylib")
        fn = getattr(lib, "proc_pidinfo", None)
        if fn is None:
            return None
        fn.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        fn.restype = ctypes.c_int
        _darwin_libproc_fn = fn
        return _darwin_libproc_fn
    except (OSError, AttributeError):
        return None


def _darwin_rss_bytes(pid: int) -> int | None:
    fn = _get_darwin_proc_pidinfo()
    if fn is not None:
        try:
            info = _ProcTaskInfo()
            nb = fn(
                pid,
                _PROC_PIDTASKINFO,
                ctypes.c_uint64(0),
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if nb >= ctypes.sizeof(info):
                return int(info.pti_resident_size)
        except (OSError, ValueError):
            pass
    # Fallback to /bin/ps
    try:
        out = subprocess.check_output(  # noqa: S603
            ["/bin/ps", "-o", "rss=", "-p", str(pid)],
            timeout=1,
            stderr=subprocess.DEVNULL,
        )
        val = out.strip()
        if val:
            return int(val) * 1024
    except (subprocess.SubprocessError, OSError, ValueError):
        pass
    return None


def _linux_rss_bytes(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/statm", encoding="ascii") as fh:
            pages = int(fh.read().split()[1])
        page_size = getattr(os, "sysconf", lambda _: 4096)("SC_PAGE_SIZE")
        return pages * page_size
    except (OSError, ValueError, IndexError):
        pass
    try:
        with open(f"/proc/{pid}/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _win32_rss_bytes(pid: int) -> int | None:
    try:
        windll = getattr(ctypes, "windll", None)
        if windll is None:
            return None
        kernel32 = windll.kernel32
        psapi = windll.psapi
        h = kernel32.OpenProcess(0x0410, False, pid)
        if not h:
            h = kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return None
        try:
            counters = _PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(counters)
            if psapi.GetProcessMemoryInfo(h, ctypes.byref(counters), counters.cb):
                return int(counters.WorkingSetSize)
        finally:
            kernel32.CloseHandle(h)
    except (OSError, AttributeError, ValueError):
        pass
    return None


def _rss_bytes(pid: int) -> int | None:
    """Return resident set size (RSS) in bytes for process `pid`, or None if unavailable."""
    if sys.platform == "darwin":
        return _darwin_rss_bytes(pid)
    if sys.platform.startswith("linux"):
        return _linux_rss_bytes(pid)
    if sys.platform == "win32":
        return _win32_rss_bytes(pid)
    try:
        out = subprocess.check_output(  # noqa: S603
            ["ps", "-o", "rss=", "-p", str(pid)],  # noqa: S607
            timeout=1,
            stderr=subprocess.DEVNULL,
        )
        val = out.strip()
        if val:
            return int(val) * 1024
    except (subprocess.SubprocessError, OSError, ValueError):
        pass
    return None


def run_batch(
    fn: Callable[[Any], Any], batch: list[tuple[int, Any]]
) -> list[tuple[int, Any, float]]:
    """In a worker: `fn` over each item, publishing which one is running and since when."""
    out = []
    for idx, item in batch:
        started = time.monotonic()
        if _slot >= 0:
            with _watch.get_lock():
                _watch[_slot * _FIELDS + 1] = idx
                _watch[_slot * _FIELDS + 2] = started
        try:
            out.append((idx, fn(item), time.monotonic() - started))
        finally:
            if _slot >= 0:
                _watch[_slot * _FIELDS + 2] = 0.0
    return out


def _claimed(watch: Any) -> int:
    """How many workers have started up and claimed a slot."""
    with watch.get_lock():
        snap = watch[:]
    return sum(1 for k in range(0, len(snap), _FIELDS) if snap[k])


def _running(watch: Any) -> list[tuple[int, int, float]]:
    """(pid, item index, start) for every worker that is mid-item."""
    with watch.get_lock():
        snap = watch[:]
    return [
        (int(snap[k]), int(snap[k + 1]), snap[k + 2])
        for k in range(0, len(snap), _FIELDS)
        if snap[k] and snap[k + 2]
    ]


def _kill(pid: int) -> None:
    try:
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    except OSError:
        pass  # already gone


def _round(
    fn: Callable[[Any], Any],
    items: Sequence[Any],
    todo: list[int],
    jobs: int,
    timeout: float,
    initializer: Callable[[Any], None],
) -> tuple[dict[int, tuple[Any, float]], set[int], set[int], set[int]]:
    """One pool over `todo`. Returns (finished, overrun, lost, crashed).

    `overrun` are the items whose worker was killed for exceeding `timeout`.
    `crashed` are the items whose worker was killed for exceeding `WORKER_MEMORY_MB`.
    `lost` are the items workers were in the middle of when the pool broke for
    any other reason (an unhandled crash, an OS kill): one of them did it. Items in
    none of these sets and not finished did not get to run and can simply be retried.
    """
    ctx = _context()
    watch = ctx.Array("d", _FIELDS * jobs)
    chunk = max(1, len(todo) // (jobs * 8))
    batches = [[(i, items[i]) for i in todo[k : k + chunk]] for k in range(0, len(todo), chunk)]
    finished: dict[int, tuple[Any, float]] = {}
    overrun: set[int] = set()
    crashed: set[int] = set()
    lost: set[int] = set()
    poll = min(0.25, timeout / 4) if timeout > 0 else 0.25
    stall_after = max(STALL_S, 3 * timeout)
    stalled = False
    # Accessed as concurrent.futures.ProcessPoolExecutor so tests can substitute it.
    # Not a `with`: its exit joins the workers, which is exactly what must not
    # happen when one of them is wedged.
    pool = concurrent.futures.ProcessPoolExecutor(
        max_workers=jobs,
        mp_context=ctx,
        initializer=initializer,
        initargs=((watch, WORKER_MEMORY_MB),),
    )
    pending: set[concurrent.futures.Future[Any]] = set()
    broken = False
    try:
        try:
            for b in batches:
                pending.add(pool.submit(run_batch, fn, b))
        except BrokenProcessPool:
            broken = True  # a worker died while the rest were still being queued
        progress = time.monotonic()
        claimed = 0
        while True:
            if pending and not broken:
                done, pending = concurrent.futures.wait(
                    pending, timeout=poll, return_when=concurrent.futures.FIRST_COMPLETED
                )
                if done:
                    progress = time.monotonic()
                for fut in done:
                    try:
                        for idx, res, elapsed in fut.result():
                            finished[idx] = (res, elapsed)
                    except (BrokenProcessPool, MemoryError):
                        broken = True
            running = _running(watch)
            now = time.monotonic()
            started_up = _claimed(watch)
            if started_up > claimed:  # a slow start-up is not a wedge
                claimed, progress = started_up, now
            if running:
                progress = max(progress, max(start for _, _, start in running))
            if timeout > 0:
                for pid, idx, start in running:
                    if now - start > timeout and idx not in finished:
                        _kill(pid)
                        overrun.add(idx)
                        broken = True
            if WORKER_MEMORY_MB > 0 and not broken:
                limit_bytes = WORKER_MEMORY_MB * 1024 * 1024
                for pid, idx, _ in running:
                    if idx in finished or idx in overrun or idx in crashed:
                        continue
                    rss = _rss_bytes(pid)
                    if rss is not None and rss > limit_bytes:
                        _kill(pid)
                        crashed.add(idx)
                        broken = True
            if broken and not overrun and not crashed:
                # Nothing was killed for time or memory, so a worker died on its own
                # and one of the files in flight did it. (After a kill the
                # other in-flight files are just collateral: retried as usual.)
                lost = {idx for _, idx, _ in running if idx not in finished}
            if pending and not broken and not running and now - progress > stall_after:
                # Work is queued, nothing finishes and no worker is on a file:
                # the workers never got going (e.g. wedged at start-up). No
                # per-file deadline can see that, so give up on the pool.
                stalled = True
            if broken or stalled or not pending:
                break
    finally:
        # On a break or an error (a strict-policy ParseError out of a
        # worker), drop what has not started rather than parse it for nothing.
        for fut in pending:
            fut.cancel()
        if stalled or broken:
            for proc in list(getattr(pool, "_processes", {}).values()):
                proc.kill()
        pool.shutdown(wait=not stalled, cancel_futures=True)
    if stalled:
        raise PoolUnavailable(f"parse workers made no progress for {stall_after:g}s")
    return finished, overrun, lost, crashed


def supervised_map(
    fn: Callable[[Any], Any],
    items: Sequence[Any],
    jobs: int,
    timeout: float,
    initializer: Callable[[Any], None],
) -> list[tuple[Any, float] | str]:
    """`fn` over `items` in killable worker processes, results in input order.

    Each result is `(fn(item), seconds)`, or `TIMEOUT` / `CRASH` for an item
    whose worker had to be killed or died under it. `initializer` runs once per
    worker with the shared watch array and must call `claim_slot` on it.

    A pool that breaks with an item in flight is retried without it: the items
    other workers were running are re-run one at a time in a pool of their own,
    so a crash is pinned on the file that causes it, not on its neighbours.

    Raises:
        PoolUnavailable: the pool broke without finishing or blaming anything,
            i.e. it cannot run here at all; the caller falls back to serial.
    """
    out: dict[int, tuple[Any, float] | str] = {}
    todo = list(range(len(items)))
    while todo:
        finished, overrun, lost, crashed = _round(fn, items, todo, jobs, timeout, initializer)
        out.update(finished)
        out.update(dict.fromkeys(overrun, TIMEOUT))
        out.update(dict.fromkeys(crashed, CRASH))
        for idx in sorted(lost):
            alone, over, died, mem_died = _round(fn, items, [idx], 1, timeout, initializer)
            if idx in alone:
                out[idx] = alone[idx]
            elif idx in over:
                out[idx] = TIMEOUT
            elif died or mem_died:
                out[idx] = CRASH
            # else: the solo pool never ran it either; the next round retries it
        rest = [i for i in todo if i not in out]
        if len(rest) == len(todo):
            raise PoolUnavailable("the parse pool broke before finishing any file")
        todo = rest
    return [out[i] for i in range(len(items))]
