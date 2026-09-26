"""Background index builds and the status handle that polls them.

The state machine is small; the failure handling is the part worth testing. A
build that fails must leave the server *usable* -- every later call answering
with a clear error rather than the process dying, hanging, or retrying a build
that has already proved it cannot succeed.

Builders here are injected stubs. A real tree-sitter build would make these
tests slow and would test `graph.build`, which has its own suite.
"""

import json
import threading
import time

import pytest

from repo2graph import mcp
from repo2graph import tasks as tasks_module
from repo2graph.tasks import BUILDING, FAILED, READY, BuildTask, TaskManager


class Builder:
    """A stub build: blocks until released, then succeeds or raises."""

    def __init__(self, fail=None, block=False):
        self.calls = []
        self.fail = fail
        self.release = threading.Event()
        if not block:
            self.release.set()

    def __call__(self, repo, out):
        self.calls.append((repo, out))
        self.release.wait(timeout=10)
        if self.fail:
            raise self.fail
        from pathlib import Path

        Path(out).mkdir(parents=True, exist_ok=True)


def wait_for(predicate, timeout=5.0):
    """Poll until `predicate` is true, so no test sleeps a fixed duration."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def manager(builder=None, estimate=1.0):
    return TaskManager(builder=builder or Builder(), estimator=lambda repo: estimate)


# ---------------------------------------------------------------- state ----


def test_a_new_task_starts_building_and_then_becomes_ready(tmp_path):
    builder = Builder(block=True)
    tasks = manager(builder)
    task = tasks.start(tmp_path / "repo", tmp_path / "out")

    assert task.status == BUILDING
    assert task.task_id
    assert task.snapshot()["status"] == BUILDING

    builder.release.set()
    assert wait_for(lambda: task.status == READY)
    assert task.snapshot()["progress_pct"] == 100
    assert task.snapshot()["eta_s"] == 0
    assert task.snapshot()["error"] is None


def test_a_failing_build_is_recorded_as_failed_with_its_message(tmp_path):
    tasks = manager(Builder(fail=RuntimeError("grammar exploded")))
    task = tasks.start(tmp_path / "repo", tmp_path / "out")

    assert wait_for(lambda: task.status == FAILED)
    assert "grammar exploded" in task.error
    assert task.snapshot()["status"] == FAILED


def test_a_systemexit_inside_a_build_still_marks_it_failed(tmp_path):
    """Otherwise the task reports "building" until the process dies."""
    tasks = manager(Builder(fail=SystemExit("no such directory")))
    task = tasks.start(tmp_path / "repo", tmp_path / "out")
    assert wait_for(lambda: task.status == FAILED)
    assert "no such directory" in task.error


def test_progress_never_reaches_100_while_still_building(tmp_path):
    """A bar that hits 100 and keeps going is worse than one that admits it guesses."""
    builder = Builder(block=True)
    tasks = manager(builder, estimate=0.001)  # guarantees an overrun
    task = tasks.start(tmp_path / "repo", tmp_path / "out")

    time.sleep(0.05)
    snapshot = task.snapshot()
    assert snapshot["status"] == BUILDING
    assert snapshot["progress_pct"] <= 99
    assert snapshot["progress_is_estimated"] is True
    builder.release.set()


def test_progress_is_never_zero_while_building(tmp_path):
    """A client must be able to tell "starting" from "stuck"."""
    builder = Builder(block=True)
    tasks = manager(builder, estimate=10_000)
    task = tasks.start(tmp_path / "repo", tmp_path / "out")
    assert task.snapshot()["progress_pct"] >= 1
    builder.release.set()


def test_eta_counts_down_and_never_goes_negative(tmp_path):
    task = BuildTask(estimated_s=0.01)
    time.sleep(0.05)
    assert task.eta_s() == 0


def test_two_requests_for_one_directory_share_a_task(tmp_path):
    """Two concurrent builds would race each other through atomic_write."""
    builder = Builder(block=True)
    tasks = manager(builder)
    out = tmp_path / "out"
    first = tasks.start(tmp_path / "repo", out)
    second = tasks.start(tmp_path / "repo", out)

    assert first.task_id == second.task_id
    builder.release.set()
    assert wait_for(lambda: first.status == READY)
    assert len(builder.calls) == 1, "a second build was started for one directory"


def test_a_task_is_retrievable_by_id(tmp_path):
    tasks = manager()
    task = tasks.start(tmp_path / "repo", tmp_path / "out")
    assert tasks.get(task.task_id) is task
    assert tasks.get("not-a-real-id") is None


def test_by_id_evicts_oldest_finished_tasks_past_the_cap(tmp_path, monkeypatch):
    """ISS-150: `_by_id` must not grow without bound. A still-BUILDING task
    must never be evicted; a caller polling an id that *was* evicted gets a
    clean None (the "unknown task" answer), not a crash."""
    monkeypatch.setattr(tasks_module, "MAX_TRACKED_TASKS", 5)

    out0 = tmp_path / "out0"
    hold = threading.Event()

    def builder(repo, out):
        if str(out) == str(out0):
            hold.wait(timeout=10)
        # else: finishes immediately.

    mgr = TaskManager(builder=builder, estimator=lambda repo: 1.0)

    first = mgr.start(tmp_path / "repo", out0)
    assert first.status == BUILDING

    tasks_list = [first]
    for i in range(1, 12):
        t = mgr.start(tmp_path / "repo", tmp_path / f"out{i}")
        assert wait_for(lambda t=t: t.status == READY)
        tasks_list.append(t)

    # 12 tasks were started (1 still building + 11 finished) -- well past
    # the cap of 5 -- so eviction must actually have run.
    assert len(tasks_list) == 12

    assert first.status == BUILDING, "the flood must not have touched the running build"
    assert mgr.get(first.task_id) is first, "a still-building task must never be evicted"
    assert len(mgr._by_id) == 5, "the cap was not enforced on the literal map size"

    # The oldest finished tasks were evicted first...
    assert mgr.get(tasks_list[1].task_id) is None
    # ...while the most recent one survives, and polling an evicted id is a
    # clean miss rather than an error.
    assert mgr.get(tasks_list[-1].task_id) is tasks_list[-1]

    hold.set()
    assert wait_for(lambda: first.status == READY)


class RecordingLock:
    """A drop-in for threading.Lock (which rejects attribute assignment,
    ISS-109's test can't monkeypatch its bound methods) that counts how many
    times it was actually held."""

    def __init__(self):
        self._real = threading.Lock()
        self.acquisitions = 0

    def acquire(self, *a, **k):
        self.acquisitions += 1
        return self._real.acquire(*a, **k)

    def release(self):
        self._real.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


# ISS-109: the terminal write must happen under `self._lock`, like every other
# mutator in TaskManager. Both branches reach that write -- the success path sets
# status/finished_at, the except branch also sets error -- so both are checked,
# and the only thing that differs is which way the builder ends.
@pytest.mark.parametrize(
    "fail, expected_status, fields",
    [
        pytest.param(None, READY, "task.status/finished_at", id="success-path"),
        pytest.param(
            RuntimeError("boom"), FAILED, "task.error/status/finished_at", id="except-branch"
        ),
    ],
)
def test_run_writes_task_state_under_the_lock(tmp_path, fail, expected_status, fields):
    builder = Builder(fail=fail, block=True) if fail else Builder(block=True)
    tasks = manager(builder)
    task = tasks.start(tmp_path / "repo", tmp_path / "out")
    recorder = tasks._lock = RecordingLock()

    builder.release.set()
    assert wait_for(lambda: task.status == expected_status)
    assert recorder.acquisitions >= 1, f"{fields} were set without self._lock"


def test_the_estimator_failing_does_not_stop_the_build(tmp_path):
    def boom(repo):
        raise OSError("cannot walk")

    tasks = TaskManager(builder=Builder(), estimator=boom)
    task = tasks.start(tmp_path / "repo", tmp_path / "out")
    assert wait_for(lambda: task.status == READY)


# ------------------------------------------------------------- dispatch ----


def test_repo_build_status_reports_a_live_task(tmp_path):
    builder = Builder(block=True)
    tasks = manager(builder)
    task = tasks.start(tmp_path / "repo", tmp_path / "out")

    got = json.loads(
        mcp.dispatch(None, "repo_build_status", {"task_id": task.task_id}, tasks=tasks)
    )
    assert got["status"] == BUILDING
    assert got["task_id"] == task.task_id
    assert isinstance(got["progress_pct"], int)
    assert isinstance(got["eta_s"], int)

    builder.release.set()
    assert wait_for(lambda: task.status == READY)
    got = json.loads(
        mcp.dispatch(None, "repo_build_status", {"task_id": task.task_id}, tasks=tasks)
    )
    assert got["status"] == READY and got["error"] is None


def test_repo_build_status_for_an_unknown_id_is_a_clean_answer(tmp_path):
    got = json.loads(mcp.dispatch(None, "repo_build_status", {"task_id": "nope"}, tasks=manager()))
    assert got["status"] == "unknown"
    assert "no build task" in got["error"]


def test_repo_build_status_without_async_build_says_so(mini_index):
    index = mcp.open_index(mini_index)
    got = json.loads(mcp.dispatch(index, "repo_build_status", {"task_id": "x"}))
    assert "synchronously" in got["error"]


def test_repo_build_status_is_never_cached(tmp_path):
    """A cached progress report is the one answer guaranteed to be out of date."""
    from repo2graph.cache import ResultCache

    builder = Builder(block=True)
    tasks = manager(builder)
    task = tasks.start(tmp_path / "repo", tmp_path / "out")
    cache = ResultCache()
    args = {"task_id": task.task_id}

    mcp.dispatch(None, "repo_build_status", args, cache=cache, tasks=tasks)
    builder.release.set()
    assert wait_for(lambda: task.status == READY)
    after = json.loads(mcp.dispatch(None, "repo_build_status", args, cache=cache, tasks=tasks))
    assert after["status"] == READY, "a stale status was served from the cache"


def test_build_status_is_in_the_published_tool_set():
    assert "repo_build_status" in mcp.TOOL_DESCRIPTIONS
    assert mcp.TOOL_SCHEMAS["repo_build_status"]["required"] == ["task_id"]


# --------------------------------------------------------- open_or_task ----


def test_a_missing_index_returns_a_building_message_not_a_block(tmp_path):
    builder = Builder(block=True)
    tasks = manager(builder)
    index, pending = mcp.open_index_or_task(tmp_path / "out", tmp_path / "repo", None, tasks)

    assert index is None
    assert pending and "repo_build_status" in pending
    assert "still being built" in pending
    builder.release.set()


def test_a_failed_build_leaves_the_server_usable(tmp_path, monkeypatch):
    """Every later call gets a clear error; nothing crashes or retries forever."""
    tasks = manager(Builder(fail=RuntimeError("tree-sitter unavailable")))
    out, repo = tmp_path / "out", tmp_path / "repo"
    repo.mkdir()

    mcp.open_index_or_task(out, repo, None, tasks)
    assert wait_for(lambda: tasks.for_dir(out).status == FAILED)

    for _ in range(3):
        index, pending = mcp.open_index_or_task(out, repo, None, tasks)
        assert index is None
        assert "tree-sitter unavailable" in pending
        assert "rebuild" in pending
    assert len(tasks.for_dir(out).task_id) > 0


def test_an_existing_index_is_opened_directly(mini_index, tmp_path):
    """The task machinery must not get in the way when there is nothing to build."""
    tasks = manager()
    index, pending = mcp.open_index_or_task(mini_index, tmp_path, None, tasks)
    assert pending is None and index is not None


def test_without_a_task_manager_the_behaviour_is_unchanged(mini_index):
    index, pending = mcp.open_index_or_task(mini_index, None, None, None)
    assert pending is None and index is not None


def test_async_build_is_off_by_default(mini_repo, monkeypatch):
    """The synchronous build is a considered default, not an oversight.

    A build that outlives its client still writes a real index to disk, so the
    retry is instant -- a failure that heals itself. --async-build exists for
    the case that reasoning does not cover: a very large repo behind a client
    whose tool-call timeout no single build can fit inside.
    """
    seen = {}
    monkeypatch.setattr(mcp, "serve", lambda out, repo=None, **kw: seen.update(kw))

    mcp.main([str(mini_repo)])
    assert seen["tasks"] is None, "background builds are on by default"

    seen.clear()
    mcp.main([str(mini_repo), "--async-build"])
    assert seen["tasks"] is not None, "--async-build did not reach serve()"


def test_iss151_default_estimator_runs_in_background_thread(tmp_path, monkeypatch):
    """Issue 151: TaskManager.start does not run _default_estimator synchronously on calling thread."""
    calling_thread_id = threading.get_ident()
    estimator_thread_ids = []
    real_estimator = tasks_module._default_estimator

    can_estimate = threading.Event()
    estimator_started = threading.Event()

    def wrapped_estimator(repo):
        estimator_thread_ids.append(threading.get_ident())
        estimator_started.set()
        can_estimate.wait(timeout=5)
        return real_estimator(repo)

    monkeypatch.setattr(tasks_module, "_default_estimator", wrapped_estimator)

    builder_block = threading.Event()

    def stub_builder(repo, out):
        builder_block.wait(timeout=5)

    mgr = TaskManager(builder=stub_builder)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "f.py").write_text("x = 1\n")
    out = tmp_path / "out"

    # If start() called the estimator synchronously, this would deadlock/timeout because
    # can_estimate is not yet set.
    task = mgr.start(repo, out)
    assert task.status == BUILDING

    assert wait_for(lambda: estimator_started.is_set())
    assert estimator_thread_ids[0] != calling_thread_id

    can_estimate.set()
    builder_block.set()
    assert wait_for(lambda: task.status == READY)
