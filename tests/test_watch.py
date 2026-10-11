"""`repo2graph build --watch` (#392)."""

from __future__ import annotations

import itertools

import pytest

from repo2graph import watch as watch_mod
from repo2graph.cli import main
from repo2graph.export import path as artifact_path
from repo2graph.parse import DEFAULT_SKIP_DIRS, BuildConfig, discover
from repo2graph.watch import poll_interval, snapshot, watch


def test_a_burst_of_changes_costs_one_rebuild():
    snaps = iter(
        [{"a": (1, 1)}, {"a": (2, 1)}, {"a": (3, 1)}, {"a": (3, 1)}, {"a": (3, 1)}]
        + [{"a": (3, 1)}] * 10
    )
    ticks = itertools.count(step=0.6)
    rebuilt = []
    n = watch(
        lambda: next(snaps),
        lambda: rebuilt.append(1),
        interval=0,
        quiet=1.0,
        clock=lambda: next(ticks),
        sleep=lambda _s: None,
        max_rebuilds=1,
    )
    assert n == 1 and rebuilt == [1]


def test_no_change_means_no_rebuild():
    calls = []
    steps = iter(range(20))

    def sleep(_s):
        if next(steps) == 19:
            raise KeyboardInterrupt

    try:
        watch(lambda: {"a": (1, 1)}, lambda: calls.append(1), sleep=sleep)
    except KeyboardInterrupt:
        pass
    assert calls == []


def test_snapshot_watches_what_a_build_indexes_and_nothing_else(tmp_path):
    """The watched set is discovery's set, not an approximation of it (#483).

    This asserted `["src/a.py"]` while `discover` on the same tree yields
    `.pre-commit-config.yaml`, `src/.a.py.swp` and `src/a.py~` as well -- a
    walk-fallback build indexes every file that is not in a skipped directory,
    whatever its name begins or ends with. Ignoring a file the build indexes is
    a rebuild that never happens, which is the whole failure `--watch` exists
    to avoid, so the dotfile and `~` rules are gone and the two sets agree.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("A = 1\n")
    (tmp_path / "src" / ".a.py.swp").write_text("x")
    (tmp_path / "src" / "a.py~").write_text("x")
    (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "m.js").write_text("x")
    (tmp_path / ".r2g").mkdir()
    (tmp_path / ".r2g" / "nodes.jsonl").write_text("x")
    (tmp_path / ".idx.r2glock").write_text("x")

    snap = snapshot(tmp_path, {"node_modules"}, tmp_path / ".r2g")
    assert sorted(snap) == [
        ".pre-commit-config.yaml",
        "src/.a.py.swp",
        "src/a.py",
        "src/a.py~",
    ]

    indexed = {
        rel
        for rel, _ in discover(tmp_path, config=BuildConfig(extra_exclude_dirs=["node_modules"]))
    }
    assert not indexed - set(snap) - {".r2g/nodes.jsonl"}


def test_snapshot_ignores_gitignored_paths(tmp_path):
    """A build never sees them, so a change to one must not trigger a rebuild.

    `snapshot` walked the tree itself, honouring no `.gitignore`: on
    repo2graph's own checkout that meant watching 305 files under
    `.hypothesis`, which every `pytest` run rewrites, so running the suite
    under `--watch` rebuilt continuously (#483).
    """
    import subprocess

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("A = 1\n")
    (tmp_path / ".gitignore").write_text("generated/\n")
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated" / "big.py").write_text("B = 1\n")
    for args in (
        ["init", "-q"],
        ["add", "-A"],
        ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"],
    ):
        if subprocess.run(["git", "-C", str(tmp_path), *args], capture_output=True).returncode:
            pytest.skip("git unavailable")

    snap = snapshot(tmp_path, DEFAULT_SKIP_DIRS)
    assert "src/a.py" in snap and ".gitignore" in snap
    assert "generated/big.py" not in snap
    assert not any(p.startswith(".git/") for p in snap)


def test_large_trees_are_polled_less_often():
    assert poll_interval(1_000) == 1.0
    assert poll_interval(100_000) == 2.0


def test_a_watched_rebuild_matches_a_fresh_build(tmp_path, capsys, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def a():\n    return 1\n")
    (repo / "b.py").write_text("def b():\n    return a()\n")
    out = tmp_path / "idx"

    sleeps = itertools.count()

    def edit_twice_then_wait(_s):
        # Two saves in quick succession, then stillness: one rebuild.
        n = next(sleeps)
        if n == 0:
            (repo / "a.py").write_text("def a():\n    return 2\n\n\ndef c():\n    return a()\n")
        elif n == 1:
            (repo / "b.py").write_text("def b():\n    return c()\n")

    ticks = itertools.count(step=0.6)
    rebuilds = []
    real = watch_mod.watch

    def counted(take, rebuild, **kw):
        return real(
            take,
            lambda: (rebuilds.append(1), rebuild()),
            **{
                **kw,
                "sleep": edit_twice_then_wait,
                "clock": lambda: next(ticks),
                "max_rebuilds": 1,
            },
        )

    monkeypatch.setattr(watch_mod, "watch", counted)
    main(
        [
            "build",
            str(repo),
            "-o",
            str(out),
            "--formats",
            "jsonl",
            "--watch",
            "--watch-interval",
            "0",
        ]
    )
    assert rebuilds == [1]

    fresh = tmp_path / "fresh"
    main(["build", str(repo), "-o", str(fresh), "--formats", "jsonl"])
    capsys.readouterr()
    for name in ("nodes.jsonl", "edges.jsonl", "chunks.jsonl"):
        assert artifact_path(out, name).read_bytes() == artifact_path(fresh, name).read_bytes(), (
            name
        )


def test_an_interrupted_rebuild_leaves_the_index_and_no_lock(tmp_path, capsys, monkeypatch):
    from repo2graph import export

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def a():\n    return 1\n")
    out = tmp_path / "idx"
    main(["build", str(repo), "-o", str(out), "--formats", "jsonl"])
    before = artifact_path(out, "nodes.jsonl").read_bytes()

    def edit_then_wait(_s, n=itertools.count()):
        if next(n) == 0:
            (repo / "a.py").write_text("def a():\n    return 22\n")

    ticks = itertools.count(step=0.6)
    real = watch_mod.watch
    monkeypatch.setattr(
        watch_mod,
        "watch",
        lambda take, rebuild, **kw: real(
            take, rebuild, **{**kw, "sleep": edit_then_wait, "clock": lambda: next(ticks)}
        ),
    )
    calls = itertools.count()
    real_dump = export.dump_all

    def first_then_interrupt(*a, **k):
        if next(calls) == 0:
            return real_dump(*a, **k)
        raise KeyboardInterrupt

    monkeypatch.setattr("repo2graph.cli.dump_all", first_then_interrupt)
    main(["build", str(repo), "-o", str(out), "--formats", "jsonl", "--watch"])
    assert "stopped watching" in capsys.readouterr().err
    assert artifact_path(out, "nodes.jsonl").read_bytes() == before
    assert not list(tmp_path.glob(".*.r2glock"))
