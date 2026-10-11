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
by the parent) and, on Linux, address space (`WORKER_MEMORY_MB`, enforced by the
kernel: tree-sitter crashes when an allocation fails, which kills only the
worker).

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
import os
import signal
import sys
import time
from collections.abc import Callable, Sequence
from concurrent.futures.process import BrokenProcessPool
from typing import Any

#: Default wall-clock budget for reading and parsing one file, in seconds.
PARSE_TIMEOUT_S = 10.0
#: Address space a worker may add to what it held at start (Linux only).
WORKER_MEMORY_MB = 2048
#: Files at most this large may be parsed in the calling process when the build
#: is serial. Larger ones always go to a supervised worker: the quadratic input
#: above costs 0.3 s and 200 MB at 16 KB, so this caps what an unsupervised
#: parse can cost.
INPROCESS_MAX_BYTES = 16 * 1024

TIMEOUT = "timeout"
CRASH = "crash"

# Per worker slot in the shared watch array: pid, index of the item being
# parsed, and when it started (time.monotonic, which is system-wide on every
# platform CPython supports). A start of 0 means idle.
_FIELDS = 3
_slot = -1
_watch: Any = None


class PoolUnavailable(RuntimeError):
    """The pool broke before it parsed anything: no workers to supervise."""


def claim_slot(watch: Any) -> None:
    """In a new worker: take a free slot in `watch` and cap this process's memory."""
    global _slot, _watch
    with watch.get_lock():
        for k in range(len(watch) // _FIELDS):
            if watch[k * _FIELDS] == 0:
                watch[k * _FIELDS] = os.getpid()
                _slot, _watch = k, watch
                break
    _cap_memory()


def _cap_memory() -> None:
    """Bound this worker's address space, so a runaway parse kills only it."""
    if not sys.platform.startswith("linux"):
        return  # macOS does not enforce RLIMIT_AS; Windows has no rlimits
    try:
        import resource

        with open("/proc/self/statm", encoding="ascii") as fh:
            current = int(fh.read().split()[0]) * os.sysconf("SC_PAGE_SIZE")
        limit = current + WORKER_MEMORY_MB * 1024 * 1024
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        if hard != resource.RLIM_INFINITY:
            limit = min(limit, hard)
        if soft == resource.RLIM_INFINITY or soft > limit:
            resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
    except (ImportError, OSError, ValueError, IndexError):
        pass  # best effort: the wall-clock budget still applies


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
) -> tuple[dict[int, tuple[Any, float]], set[int], set[int]]:
    """One pool over `todo`. Returns (finished, overrun, lost).

    `overrun` are the items whose worker was killed for exceeding `timeout`.
    `lost` are the items workers were in the middle of when the pool broke for
    any other reason (a crash, the memory cap): one of them did it. Items in
    neither set and not finished did not get to run and can simply be retried.
    """
    import multiprocessing

    watch = multiprocessing.Array("d", _FIELDS * jobs)
    chunk = max(1, len(todo) // (jobs * 8))
    batches = [[(i, items[i]) for i in todo[k : k + chunk]] for k in range(0, len(todo), chunk)]
    finished: dict[int, tuple[Any, float]] = {}
    overrun: set[int] = set()
    lost: set[int] = set()
    poll = min(0.25, timeout / 4) if timeout > 0 else 0.25
    # Accessed as concurrent.futures.ProcessPoolExecutor so tests can substitute it.
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=jobs, initializer=initializer, initargs=(watch,)
    ) as pool:
        pending: set[concurrent.futures.Future[Any]] = set()
        broken = False
        try:
            for b in batches:
                pending.add(pool.submit(run_batch, fn, b))
        except BrokenProcessPool:
            broken = True  # a worker died while the rest were still being queued
        try:
            while True:
                if pending and not broken:
                    done, pending = concurrent.futures.wait(
                        pending, timeout=poll, return_when=concurrent.futures.FIRST_COMPLETED
                    )
                    for fut in done:
                        try:
                            for idx, res, elapsed in fut.result():
                                finished[idx] = (res, elapsed)
                        except BrokenProcessPool:
                            broken = True
                running = _running(watch)
                if timeout > 0:
                    now = time.monotonic()
                    for pid, idx, start in running:
                        if now - start > timeout and idx not in finished:
                            _kill(pid)
                            overrun.add(idx)
                            broken = True
                if broken and not overrun:
                    # Nothing was killed for time, so a worker died on its own
                    # and one of the files in flight did it. (After a kill the
                    # other in-flight files are just collateral: retried as usual.)
                    lost = {idx for _, idx, _ in running if idx not in finished}
                if broken or not pending:
                    break
        finally:
            # On a break or an error (a strict-policy ParseError out of a
            # worker), drop what has not started rather than parse it for nothing.
            for fut in pending:
                fut.cancel()
    return finished, overrun, lost


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
        finished, overrun, lost = _round(fn, items, todo, jobs, timeout, initializer)
        out.update(finished)
        out.update(dict.fromkeys(overrun, TIMEOUT))
        for idx in sorted(lost):
            alone, over, died = _round(fn, items, [idx], 1, timeout, initializer)
            if idx in alone:
                out[idx] = alone[idx]
            elif idx in over:
                out[idx] = TIMEOUT
            elif died:
                out[idx] = CRASH
            # else: the solo pool never ran it either; the next round retries it
        rest = [i for i in todo if i not in out]
        if len(rest) == len(todo):
            raise PoolUnavailable("the parse pool broke before finishing any file")
        todo = rest
    return [out[i] for i in range(len(items))]
