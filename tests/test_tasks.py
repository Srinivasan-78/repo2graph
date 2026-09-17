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

from repo2graph import mcp
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
    return TaskManager(builder=builder or Builder(),
                       estimator=lambda repo: estimate)


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
    tasks = manager(builder, estimate=0.001)     # guarantees an overrun
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

    got = json.loads(mcp.dispatch(None, "repo_build_status",
                                  {"task_id": task.task_id}, tasks=tasks))
    assert got["status"] == BUILDING
    assert got["task_id"] == task.task_id
    assert isinstance(got["progress_pct"], int)
    assert isinstance(got["eta_s"], int)

    builder.release.set()
    assert wait_for(lambda: task.status == READY)
    got = json.loads(mcp.dispatch(None, "repo_build_status",
                                  {"task_id": task.task_id}, tasks=tasks))
    assert got["status"] == READY and got["error"] is None


def test_repo_build_status_for_an_unknown_id_is_a_clean_answer(tmp_path):
    got = json.loads(mcp.dispatch(None, "repo_build_status",
                                  {"task_id": "nope"}, tasks=manager()))
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
    after = json.loads(mcp.dispatch(None, "repo_build_status", args,
                                    cache=cache, tasks=tasks))
    assert after["status"] == READY, "a stale status was served from the cache"


def test_build_status_is_in_the_published_tool_set():
    assert "repo_build_status" in mcp.TOOL_DESCRIPTIONS
    assert mcp.TOOL_SCHEMAS["repo_build_status"]["required"] == ["task_id"]


# --------------------------------------------------------- open_or_task ----

def test_a_missing_index_returns_a_building_message_not_a_block(tmp_path):
    builder = Builder(block=True)
    tasks = manager(builder)
    index, pending = mcp.open_index_or_task(
        tmp_path / "out", tmp_path / "repo", None, tasks)

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
    monkeypatch.setattr(mcp, "serve",
                        lambda out, repo=None, **kw: seen.update(kw))

    mcp.main([str(mini_repo)])
    assert seen["tasks"] is None, "background builds are on by default"

    seen.clear()
    mcp.main([str(mini_repo), "--async-build"])
    assert seen["tasks"] is not None, "--async-build did not reach serve()"
