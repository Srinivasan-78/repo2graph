"""Background index builds, so a first tool call need not block for a minute.

The default is still to build synchronously on the first tool call, and that is
a considered position rather than inertia: nothing this server does means
anything without an index, so there is no *other* call worth serving while one
is missing, and a synchronous build that outlives its client still leaves a real
index on disk, so the retry is instant. A failure that heals itself beats one
that does not.

`--async-build` exists for the case that reasoning does not cover: a very large
repository behind a client with a short tool-call timeout, where the synchronous
build is killed and restarted forever because no single call ever completes.
There the caller wants a handle it can poll, which is what this provides.

Progress is an estimate and is labelled as one. `graph.build()` has no progress
callback -- adding one would thread a callable through the parse pool and into
worker processes for a cosmetic number -- so the percentage here is elapsed time
against a rate estimated from the file count. It is deliberately capped below
100 until the build genuinely finishes, because a progress bar that reaches 100
and then keeps going is worse than one that admits it is guessing.
"""
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

# Rough parse rate used to estimate a build's duration, in files per second.
# Measured on this repository on a mid-range laptop; it exists to turn a file
# count into an eta with the right order of magnitude, nothing more.
FILES_PER_SECOND = 120.0
# Progress never reports beyond this until the build actually completes.
MAX_REPORTED_PROGRESS = 99
# A build that has produced no estimate yet still reports something non-zero,
# so a client can tell "starting" from "stuck".
MIN_REPORTED_PROGRESS = 1

BUILDING = "building"
READY = "ready"
FAILED = "failed"


@dataclass
class BuildTask:
    """One background index build.

    Attributes:
        task_id: Opaque handle the caller polls with.
        status: "building", "ready" or "failed".
        error: Human-readable failure message when status is "failed".
        started_at: Monotonic start time.
        finished_at: Monotonic completion time, or None.
        estimated_s: Predicted duration, used for progress and eta.
    """

    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = BUILDING
    error: str | None = None
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    estimated_s: float = 1.0

    def _elapsed(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return max(0.0, end - self.started_at)

    def progress_pct(self) -> int:
        """Completion estimate, 0-100. Never reaches 100 while still building."""
        if self.status == READY:
            return 100
        if self.status == FAILED:
            return 0
        fraction = self._elapsed() / max(self.estimated_s, 0.001)
        pct = int(fraction * 100)
        return max(MIN_REPORTED_PROGRESS, min(pct, MAX_REPORTED_PROGRESS))

    def eta_s(self) -> int:
        """Estimated seconds remaining; 0 once the build has settled."""
        if self.status != BUILDING:
            return 0
        return max(0, int(round(self.estimated_s - self._elapsed())))

    def snapshot(self) -> dict[str, Any]:
        """The status document `repo_build_status` returns."""
        return {
            "task_id": self.task_id,
            "status": self.status,
            "progress_pct": self.progress_pct(),
            "eta_s": self.eta_s(),
            "error": self.error,
            # Named so nobody mistakes the number above for a measurement.
            "progress_is_estimated": True,
        }


class TaskManager:
    """Owns at most one in-flight build per index directory.

    One per directory, not one per request: two concurrent builds writing the
    same artifacts would race each other through `atomic_write`, and the loser's
    partial work would be silently discarded. A second request for a directory
    already building joins the existing task instead of starting another.

    Args:
        builder: Callable taking `(repo, out)` that performs the build. Injected
            so tests need not parse a real repository.
        estimator: Callable taking `repo` and returning an estimated duration in
            seconds. Injected for the same reason.
    """

    def __init__(self, builder: Callable[[Any, Any], None] | None = None,
                 estimator: Callable[[Any], float] | None = None) -> None:
        self._builder = builder or _default_builder
        self._estimator = estimator or _default_estimator
        self._lock = threading.Lock()
        self._by_dir: dict[str, BuildTask] = {}
        self._by_id: dict[str, BuildTask] = {}

    def start(self, repo: Any, out: Any) -> BuildTask:
        """Begin (or join) a background build for `out`.

        Args:
            repo: Repository to index.
            out: Index directory to write.

        Returns:
            The task covering this build, new or already running.
        """
        key = str(out)
        with self._lock:
            existing = self._by_dir.get(key)
            if existing is not None and existing.status == BUILDING:
                return existing
            task = BuildTask(estimated_s=self._estimate(repo))
            self._by_dir[key] = task
            self._by_id[task.task_id] = task

        thread = threading.Thread(target=self._run, args=(task, repo, out),
                                  name=f"repo2graph-build-{task.task_id[:8]}",
                                  daemon=True)
        thread.start()
        return task

    def _estimate(self, repo: Any) -> float:
        try:
            return max(1.0, float(self._estimator(repo)))
        except Exception:
            return 1.0

    def _run(self, task: BuildTask, repo: Any, out: Any) -> None:
        try:
            self._builder(repo, out)
        except BaseException as exc:
            # BaseException, not Exception: a build killed by a SystemExit from
            # deep in the stack must still mark the task failed rather than
            # leaving it reporting "building" until the process dies.
            task.error = f"{type(exc).__name__}: {exc}"
            task.status = FAILED
            task.finished_at = time.monotonic()
            from .events import emit
            emit("index_build_failed", level="error", task_id=task.task_id,
                 error=task.error)
            return
        task.status = READY
        task.finished_at = time.monotonic()

    def get(self, task_id: str) -> BuildTask | None:
        """The task with this id, or None if it was never issued."""
        with self._lock:
            return self._by_id.get(task_id)

    def for_dir(self, out: Any) -> BuildTask | None:
        """The most recent task for this index directory, or None."""
        with self._lock:
            return self._by_dir.get(str(out))

    def forget(self, out: Any) -> None:
        """Drop the task recorded for `out`, so a later failure can be retried."""
        with self._lock:
            self._by_dir.pop(str(out), None)


def _default_estimator(repo: Any) -> float:
    """Guess a build's duration from how many files discovery finds.

    Discovery is cheap next to parsing -- it is a `git ls-files` or one walk --
    so paying for it up front to produce an honest eta is worth it.

    Args:
        repo: Repository directory.

    Returns:
        Estimated seconds, never less than one.
    """
    try:
        from .parse import discover
        count = sum(1 for _ in discover(repo))
    except Exception:
        return 1.0
    return max(1.0, count / FILES_PER_SECOND)


def _default_builder(repo: Any, out: Any) -> None:
    """Build an index the same way the synchronous path does."""
    from .mcp import _build_index
    from pathlib import Path
    _build_index(Path(repo), Path(out))


BUILDING_MESSAGE = (
    "the index for this repository is still being built. Call "
    "repo_build_status with task_id {task_id!r} to check; roughly {eta_s}s "
    "remaining ({progress_pct}% done, estimated).")

FAILED_MESSAGE = (
    "the index build failed and no index is available: {error}. Fix the cause "
    "and rebuild with `repo2graph build <repo> -o <out>`, or restart this "
    "server to retry.")
