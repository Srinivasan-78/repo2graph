"""The per-file parse budget (#81): it stops a real runaway parse, never a legitimate one.

The runaway input is real, not mocked: `x = a<b<a<b<...;` parsed as C++ grows
quadratically in time and memory (32 KB: ~1 s and ~640 MB). The legitimate
fixture is deep and large but ordinary, and must parse under the default budget.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from repo2graph import parsepool
from repo2graph.cli import main
from repo2graph.graph import PARALLEL_MIN_FILES, build, parse_all
from repo2graph.parse import BuildConfig, ParseError
from repo2graph.parsepool import CRASH, PARSE_TIMEOUT_S, TIMEOUT, supervised_map

# ~5 s to parse unsupervised on a laptop core (quadratic), so a 1 s budget always
# fires. The budget is not tighter because it also covers a fresh worker's first
# parse, grammar load included, which takes a few hundred ms on a slow runner.
RUNAWAY = b"x = " + b"a<b<" * 18_000 + b";\n"


def _repo(tmp_path: Path, extra: int = 0) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "bad.cpp").write_bytes(RUNAWAY)
    (root / "ok.py").write_text("def fine():\n    return 1\n", encoding="utf8")
    for i in range(extra):
        (root / f"m{i}.py").write_text(f"def f{i}():\n    return {i}\n", encoding="utf8")
    return root


def _symbols_in(g, rel: str) -> list[str]:
    return [n for n in g.nodes if n.startswith(f"sym:{rel}::")]


@pytest.mark.parametrize("extra", [0, PARALLEL_MIN_FILES])
def test_a_runaway_parse_is_stopped_and_counted(tmp_path, capsys, monkeypatch, extra):
    root = _repo(tmp_path, extra)
    # The parent kills the worker; record that it did, rather than timing the
    # build -- a CI runner's speed says nothing about whether the parse was
    # interrupted or merely discarded after running to the end.
    killed = []
    real_kill = parsepool._kill
    monkeypatch.setattr(parsepool, "_kill", lambda pid: (killed.append(pid), real_kill(pid)))
    g = build(root, jobs=2, config=BuildConfig(parse_timeout=1.0))
    # At least one, not exactly one: a Windows runner has recorded two kills for
    # this single file. The per-file outcome below is the contract.
    assert killed, "the runaway parse was not interrupted"
    assert g.stats["skipped_parse_timeout"] == 1
    assert "file:bad.cpp" in g.nodes and not _symbols_in(g, "bad.cpp")
    assert g.nodes["file:bad.cpp"]["size"] == len(RUNAWAY)
    assert _symbols_in(g, "ok.py") == ["sym:ok.py::fine"]
    assert g.stats["parsed"] == 1 + extra
    assert "bad.cpp" not in g.parse_cache  # never cached: the next build retries it
    assert "'bad.cpp' indexed without symbols: parse exceeded the 1s budget" in (
        capsys.readouterr().err
    )


def test_a_deep_legitimate_file_is_not_a_false_positive(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    # 425 KB of literals nested 200 deep: ~0.8 s here, against a 10 s budget.
    # (The slowest real file across the five benchmark corpora took 0.74 s.)
    nest = "[" * 200 + "1" + "]" * 200
    body = "".join(f"def f{i}():\n    return {nest}\n" for i in range(1_000))
    (root / "deep.py").write_text(body, encoding="utf8")
    g = build(root, jobs=1)
    assert "skipped_parse_timeout" not in g.stats
    assert len(_symbols_in(g, "deep.py")) == 1_000


def test_strict_policy_fails_instead_of_skipping(tmp_path):
    root = _repo(tmp_path)
    with pytest.raises(ParseError, match=r"exceeded the 1s budget.*bad\.cpp"):
        build(root, jobs=1, config=BuildConfig(parse_timeout=1.0, parse_policy="strict"))


def test_an_in_process_parse_over_budget_is_discarded_too(tmp_path, monkeypatch):
    """Small files in a serial build parse in-process, where the budget can only
    discard the result afterwards; the outcome is the same as a kill."""
    from repo2graph import graph as graph_mod

    p = tmp_path / "small.py"
    p.write_text("def f():\n    return 1\n", encoding="utf8")
    real = graph_mod._read_and_parse

    def slow(item):
        # Sleep well past the budget: Windows' monotonic clock ticks every
        # ~15 ms, so a near-zero budget alone can measure a real parse as 0 s.
        time.sleep(0.2)
        return real(item)

    monkeypatch.setattr(graph_mod, "_read_and_parse", slow)
    ((_rel, _lang, read),) = parse_all([("small.py", p)], 1, BuildConfig(parse_timeout=0.05))
    monkeypatch.setattr(graph_mod, "_read_and_parse", real)
    assert read is not None and read[2].parse_skipped == TIMEOUT and read[2].symbols == []
    ((_, _, read),) = parse_all([("small.py", p)], 1, BuildConfig())
    assert read[2].parse_skipped == "" and read[2].symbols


def test_cli_flag_reaches_the_build(tmp_path, capsys):
    root = _repo(tmp_path)
    out = tmp_path / "out"
    main(["build", str(root), "-o", str(out), "--formats", "jsonl", "--parse-timeout", "1"])
    assert json.loads(capsys.readouterr().out)["stats"]["skipped_parse_timeout"] == 1
    assert PARSE_TIMEOUT_S == BuildConfig().parse_timeout == 10.0


# ---------- the supervisor itself ----------


def _work(item):
    kind, value = item
    if kind == "sleep":
        time.sleep(value)
    elif kind == "exit":
        os._exit(3)  # a worker that dies under its file, like a parser crash
    return value


def _init(watch):
    parsepool.claim_slot(watch)


# A hung item sleeps far longer than the supervisor should ever wait for it;
# the clock bound is far longer than starting the pool can plausibly cost.
# Both numbers are loose on purpose. What these two tests assert is that a hung
# item is *abandoned*, and what proves it is the gap between the two bounds,
# not a tight reading of the clock -- #480 and #481 converted this suite's
# other timing assertions for the same reason and missed these. A 60 s item
# under a 30 s bound then failed on a Windows runner that spent 50 s of that
# budget starting three coverage-traced worker processes, with the supervisor
# behaving correctly throughout.
HUNG_S = 300
ABANDONED_WITHIN_S = 120


def test_supervisor_names_the_item_that_hung_or_died_and_keeps_order():
    items = [("ok", 0), ("sleep", HUNG_S), ("ok", 2), ("exit", 0), ("ok", 4), ("ok", 5)]
    started = time.monotonic()
    got = supervised_map(_work, items, 2, 0.5, _init)
    elapsed = time.monotonic() - started
    assert elapsed < ABANDONED_WITHIN_S, (
        f"waited {elapsed:.1f}s, so the {HUNG_S}s item was waited on rather than abandoned"
    )
    # The real contract: the hung item reports TIMEOUT rather than its own
    # return value, which it could not do if the supervisor had sat through it.
    assert [g if isinstance(g, str) else g[0] for g in got] == [0, TIMEOUT, 2, CRASH, 4, 5]


def test_supervisor_without_a_time_budget_waits():
    got = supervised_map(_work, [("sleep", 0.6), ("ok", 1)], 2, 0, _init)
    assert [g[0] for g in got] == [0.6, 1]  # type: ignore[index]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="RLIMIT_AS is enforced on Linux")
def test_a_parse_that_exhausts_worker_memory_is_a_crash_not_an_oom(tmp_path, monkeypatch):
    # The runaway input needs hundreds of MB, far over a 64 MB cap, and far
    # under the time budget.
    # The cap is read in the parent and handed to each worker, so patching it
    # here reaches workers however they are started.
    monkeypatch.setattr(parsepool, "WORKER_MEMORY_MB", 64)
    root = _repo(tmp_path)
    g = build(root, jobs=1)
    assert g.stats["skipped_parse_crash"] == 1
    assert _symbols_in(g, "ok.py") == ["sym:ok.py::fine"]


def _wedged(watch):
    time.sleep(3600)  # a worker that never gets as far as its first file


def test_a_pool_whose_workers_never_start_is_abandoned_not_waited_on(monkeypatch):
    monkeypatch.setattr(parsepool, "STALL_S", 1.0)
    started = time.monotonic()
    with pytest.raises(parsepool.PoolUnavailable, match="no progress"):
        supervised_map(_work, [("ok", 1)], 1, 0, _wedged)
    elapsed = time.monotonic() - started
    assert elapsed < ABANDONED_WITHIN_S, (
        f"waited {elapsed:.1f}s on a worker that sleeps for an hour before its first file"
    )


def test_windows_cap_memory_invoked_on_win32(monkeypatch):
    """parsepool._cap_memory binds worker process to Windows Job Object with memory limit."""
    calls = []

    def fake_win_cap(pid, cap_bytes):
        calls.append((pid, cap_bytes))
        return 1234

    import repo2graph.parse as parse_mod

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(parse_mod, "_win_cap_child_memory", fake_win_cap)
    parsepool._cap_memory(64)
    assert calls == [(os.getpid(), 64 * 1024 * 1024)]
    assert parsepool._worker_job == 1234
