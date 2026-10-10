#!/usr/bin/env python3
"""Build-performance benchmarks and their regression gate (#304, #88).

`benchmark_runner.py` measures retrieval *quality*. This measures cost, on
synthetic repositories shaped to stress one thing each:

  tiny             10 files                     -- fixed overhead
  medium           500 files                    -- a typical service
  many-small       3,000 one-function files     -- per-file overhead
  deep-tree        300 files, 30 directories deep
  call-heavy       400 files sharing call names -- call resolution (#456 P2)
  git-history      120 files, 150 commits       -- --git-history 150

For each it records the full-build wall clock, write time, peak RSS (from
`build`'s own report), output size, an unchanged `--incremental` rebuild and
its speed-up, index load time, query latency, and MCP cold and warm call
latency. `--check` compares against `benchmarks/perf/baseline.json` and
fails on a slowdown past the tolerance; `--update-baseline` rewrites it.

`--scale N` multiplies every fixture's file count; `--only large --scale 1`
builds the 50,000-file repository #88 asks for.

    python scripts/perf_bench.py                       # all fixtures, print JSON
    python scripts/perf_bench.py --check               # gate against the baseline
    python scripts/perf_bench.py --only large          # the 50k-file run (#88)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "benchmarks" / "perf" / "baseline.json"

# Time limits are scaled by how much slower this machine is than the one that
# wrote the baseline (`calibrate`), then allowed 2x on top for noise. Memory
# and output size do not depend on the machine, so they are held tighter.
TOLERANCE = {"build_seconds": 2.0, "query_ms": 3.0, "peak_rss_mb": 1.5, "output_bytes": 1.25}
GATED_FLOOR = {"build_seconds": 1.0, "query_ms": 25.0}  # ignore noise below these


def _py(i: int, calls: list[str] | None = None) -> str:
    calls = calls or [f"helper_{(i + 1) % 97}"]
    body = "\n".join(f"    total += {c}(value)" for c in calls)
    return (
        f'"""Module {i}."""\n\n\n'
        f"def helper_{i % 97}(value):\n    return value + {i}\n\n\n"
        f"class Service{i}:\n    def handle(self, value):\n        total = 0\n{body}\n"
        f"        return total\n\n\n"
        f"def entry_{i}(value):\n    return Service{i}().handle(value)\n"
    )


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf8")


def make_tiny(root: Path, scale: float) -> list[str]:
    _write(root, {f"pkg/m{i}.py": _py(i) for i in range(max(1, int(10 * scale)))})
    return []


def make_medium(root: Path, scale: float) -> list[str]:
    _write(root, {f"pkg/sub{i % 20}/m{i}.py": _py(i) for i in range(int(500 * scale))})
    return []


def make_many_small(root: Path, scale: float) -> list[str]:
    _write(
        root,
        {f"f/{i % 50}/s{i}.py": f"def s{i}():\n    return {i}\n" for i in range(int(3000 * scale))},
    )
    return []


def make_deep(root: Path, scale: float) -> list[str]:
    files = {}
    for i in range(int(300 * scale)):
        depth = "/".join(f"d{j}" for j in range(i % 30))
        files[f"{depth}/m{i}.py" if depth else f"m{i}.py"] = _py(i)
    _write(root, files)
    return []


def make_call_heavy(root: Path, scale: float) -> list[str]:
    names = ["get", "run", "process", "handle", "load", "save", "parse", "render"]
    files = {}
    for i in range(int(400 * scale)):
        defs = "\n\n".join(f"def {n}(value):\n    return value" for n in names)
        files[f"svc{i % 40}/m{i}.py"] = defs + "\n\n\n" + _py(i, calls=names * 3)
    _write(root, files)
    return []


def make_git_history(root: Path, scale: float) -> list[str]:
    n_files = int(120 * scale)
    _write(root, {f"app/m{i}.py": _py(i) for i in range(n_files)})
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "bench",
        "GIT_AUTHOR_EMAIL": "bench@example.com",
        "GIT_COMMITTER_NAME": "bench",
        "GIT_COMMITTER_EMAIL": "bench@example.com",
    }

    def git(*a: str) -> None:
        subprocess.run(["git", *a], cwd=root, env=env, check=True, capture_output=True)

    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "init")
    for c in range(150):
        for k in range(5):
            p = root / f"app/m{(c * 7 + k * 13) % n_files}.py"
            p.write_text(p.read_text(encoding="utf8") + f"# edit {c}\n", encoding="utf8")
        git("commit", "-qam", f"c{c}")
    return ["--git-history", "150"]


def make_large(root: Path, scale: float) -> list[str]:
    _write(root, {f"pkg{i % 200}/sub{i % 13}/m{i}.py": _py(i) for i in range(int(50_000 * scale))})
    return []


FIXTURES: dict[str, Callable[[Path, float], list[str]]] = {
    "tiny": make_tiny,
    "medium": make_medium,
    "many-small": make_many_small,
    "deep-tree": make_deep,
    "call-heavy": make_call_heavy,
    "git-history": make_git_history,
    "large": make_large,  # #88; not in the default set, which CI runs
}
DEFAULT_SET = [n for n in FIXTURES if n != "large"]


def _build(src: Path, out: Path, extra: list[str]) -> dict[str, Any]:
    cmd = [
        sys.executable,
        "-m",
        "repo2graph.cli",
        "build",
        str(src),
        "-o",
        str(out),
        "--formats",
        "jsonl,overview",
        *extra,
    ]
    started = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding="utf8")
    report = json.loads(proc.stdout)
    report["wall_seconds"] = round(time.perf_counter() - started, 3)
    return report


def measure(name: str, scale: float) -> dict[str, Any]:
    from repo2graph.mcp.tools import dispatch
    from repo2graph.query import Index

    work = Path(tempfile.mkdtemp(prefix=f"r2g-perf-{name}-"))
    try:
        src, out = work / "src", work / "out"
        src.mkdir()
        extra = FIXTURES[name](src, scale)
        full = _build(src, out, extra)
        again = _build(src, out, [*extra, "--incremental"])
        perf = full["performance"]

        t = time.perf_counter()
        idx = Index(out)
        load_ms = (time.perf_counter() - t) * 1000
        queries = ["handle value", "entry service", "helper total", "how is a value handled"]
        t = time.perf_counter()
        for q in queries:
            idx.pack_context(q, budget_tokens=4000, exclude_secrets=True)
        query_ms = (time.perf_counter() - t) * 1000 / len(queries)
        t = time.perf_counter()
        dispatch(Index(out), "repo_search", {"query": "handle value"})
        mcp_cold_ms = (time.perf_counter() - t) * 1000
        t = time.perf_counter()
        dispatch(idx, "repo_search", {"query": "entry service"})
        mcp_warm_ms = (time.perf_counter() - t) * 1000

        return {
            "files": full["stats"].get("files", 0),
            "nodes": full["stats"].get("nodes", 0),
            "edges": full["stats"].get("edges", 0),
            "build_seconds": round(full["wall_seconds"], 3),
            "write_seconds": perf["write_seconds"],
            "peak_rss_mb": perf["peak_rss_mb"],
            "output_bytes": perf["output_bytes"],
            "chunk_text_ratio": perf["chunk_text_ratio"],
            "incremental_seconds": again["wall_seconds"],
            "cache_speedup": round(full["wall_seconds"] / again["wall_seconds"], 2)
            if again["wall_seconds"]
            else None,
            "index_load_ms": round(load_ms, 1),
            "query_ms": round(query_ms, 1),
            "mcp_cold_ms": round(mcp_cold_ms, 1),
            "mcp_warm_ms": round(mcp_warm_ms, 1),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


TIMED = ("build_seconds", "query_ms")


def calibrate() -> float:
    """Seconds this machine takes for a fixed pure-Python workload.

    Stored with the baseline, so a slower CI runner scales the time limits
    instead of failing the gate for being a slower machine.
    """
    best = float("inf")
    for _ in range(3):
        t = time.perf_counter()
        acc: dict[str, int] = {}
        for i in range(400_000):
            key = f"k{i % 997}"
            acc[key] = acc.get(key, 0) + i * i
        best = min(best, time.perf_counter() - t)
    return round(best, 4)


def regressions(
    results: dict[str, dict], baseline: dict[str, dict], machine: float = 1.0
) -> list[str]:
    """Metrics past their tolerance; time limits are scaled by `machine`."""
    out = []
    for name, got in results.items():
        base = baseline.get(name)
        if not base or name.startswith("_"):
            continue
        for metric, factor in TOLERANCE.items():
            b, g = base.get(metric), got.get(metric)
            if not isinstance(b, (int, float)) or not isinstance(g, (int, float)) or b <= 0:
                continue
            if g < GATED_FLOOR.get(metric, 0):
                continue
            limit = b * factor * (machine if metric in TIMED else 1.0)
            if g > limit:
                out.append(f"{name}: {metric} {g} > limit {limit:.3g} (baseline {b})")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--only", action="append", choices=sorted(FIXTURES), help="run just these fixtures"
    )
    p.add_argument("--scale", type=float, default=1.0, help="multiply every fixture's file count")
    p.add_argument("--check", action="store_true", help="fail on a regression against the baseline")
    p.add_argument("--update-baseline", action="store_true")
    p.add_argument("--output", type=Path, help="also write the results JSON here")
    args = p.parse_args(argv)

    results: dict[str, Any] = {"_machine": {"calibration_seconds": calibrate()}}
    for name in args.only or DEFAULT_SET:
        results[name] = measure(name, args.scale)
        print(f"{name}: {json.dumps(results[name])}", file=sys.stderr, flush=True)
    text = json.dumps(results, indent=2) + "\n"
    print(text, end="")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf8")
    if args.update_baseline:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(text, encoding="utf8")
    if args.check:
        if not BASELINE.exists():
            print("no baseline yet: run with --update-baseline", file=sys.stderr)
            return 1
        baseline = json.loads(BASELINE.read_text(encoding="utf8"))
        base_cal = baseline.get("_machine", {}).get("calibration_seconds") or 0
        machine = (
            max(1.0, results["_machine"]["calibration_seconds"] / base_cal) if base_cal else 1.0
        )
        found = regressions(results, baseline, machine)
        for line in found:
            print(f"REGRESSION {line}", file=sys.stderr)
        return 1 if found else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
