#!/usr/bin/env python3
"""Multi-call agent loop simulation: search -> read -> answer.

Measures the agent-centric metrics that single-shot retrieval cannot see:
- turns_to_answer: tool calls required to localize full evidence (fewer is better)
- tokens_to_answer: total tokens consumed across search + read calls (fewer is better)
- precision_per_read: fraction of read tokens that belong to evidence definitions (higher is better)

Usage:
    python scripts/agent_eval.py [--tasks benchmarks/real/tasks_structural.json] [--max-turns 6]
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from repo2graph.query import Index, count_tokens  # noqa: E402
from scripts.bench_real_repos import (  # noqa: E402
    RG_TYPES,
    WINDOW,
    checkout,
    covered_lines_r2g,
    found,
    terms_of,
)

BENCH = ROOT / "benchmarks" / "real"


def ripgrep_windows(
    rg_cmd: list[str], root: Path, query: str, language: str
) -> list[tuple[str, int, int]]:
    """Return ranked window spans (path, lo, hi) from ripgrep hits."""
    terms = terms_of(query)
    cmd = [*rg_cmd, "-n", "-i", "-F", "--no-heading", "-t", RG_TYPES[language]]
    for t in terms:
        cmd += ["-e", t]
    res = subprocess.run(cmd + ["."], cwd=root, capture_output=True)
    hits: dict[str, dict[int, str]] = defaultdict(dict)
    for raw in res.stdout.decode("utf8", "replace").split("\n"):
        m = re.match(r"^(.+?):(\d+):(.*)$", raw)
        if m:
            hits[m.group(1).replace("\\", "/").removeprefix("./")][int(m.group(2))] = m.group(
                3
            ).lower()

    windows = []
    for path, lines in hits.items():
        for ln in lines:
            near = [lines[x] for x in lines if abs(x - ln) <= WINDOW]
            score = sum(1 for t in terms if any(t in s for s in near))
            windows.append((score, len(near), path, ln))
    windows.sort(key=lambda w: (-w[0], -w[1], w[2], w[3]))

    out = []
    seen: dict[str, set[int]] = defaultdict(set)
    for _s, _n, path, ln in windows:
        if ln in seen[path]:
            continue
        lo, hi = max(1, ln - WINDOW), ln + WINDOW
        out.append((path, lo, hi))
        seen[path].update(range(lo, hi + 1))
    return out


def simulate_agent_loop(
    tasks: list[dict],
    repos: dict[str, dict],
    indexes: dict[str, Index],
    roots: dict[str, Path],
    rg_cmd: list[str],
    max_turns: int = 6,
    budget_search: int = 2000,
) -> dict[str, dict]:
    """Simulate an agent loop (search -> read -> answer) for repo2graph vs ripgrep."""
    results = {}

    for method in ("repo2graph", "ripgrep"):
        solved = 0
        total_turns = 0
        total_tokens = 0
        evidence_tokens = 0
        read_tokens = 0
        per_task_stats = []

        for t in tasks:
            repo = repos[t["repo"]]
            idx = indexes[t["repo"]]
            root = roots[t["repo"]]

            file_cache: dict[str, list[str]] = {}

            def read_span(path: str, start: int, end: int) -> tuple[str, dict[str, set[int]]]:
                if path not in file_cache:
                    raw = (root / path).read_text(encoding="utf8", errors="replace")
                    file_cache[path] = [ln.rstrip("\r") for ln in raw.split("\n")]
                src = file_cache[path]
                s = max(1, start)
                e = min(len(src), end)
                txt = "\n".join(src[s - 1 : e])
                lines = {path: set(range(s, e + 1))}
                return txt, lines

            task_solved = False
            task_turns = 0
            task_tokens = 0
            task_ev_tok = 0
            task_read_tok = 0
            accumulated_lines: dict[str, set[int]] = defaultdict(set)

            if method == "repo2graph":
                # Turn 1: Search with citation mode (breadth-first navigation)
                pack = idx.pack_context(
                    t["query"],
                    budget_tokens=budget_search,
                    neighbours="cite",
                    max_neighbours=4,
                    precision_first=True,
                )
                task_turns += 1
                task_tokens += pack["tokens_used"]
                task_read_tok += pack["tokens_used"]

                # Check if initial search pack already covers evidence
                cov = covered_lines_r2g(pack, root)
                for p, lns in cov.items():
                    accumulated_lines[p].update(lns)

                if all(found(t["evidence"], accumulated_lines)):
                    task_solved = True
                    for ev in t["evidence"]:
                        s, e = ev["lines"]
                        txt, _ = read_span(ev["path"], s, e)
                        task_ev_tok += count_tokens(txt)
                else:
                    # Multi-turn: agent inspects citations and reads referenced symbols
                    candidates = []
                    for c in pack["chunks"]:
                        if c.get("citation_only") or c.get("excerpt_of"):
                            span = c.get("excerpt_of") or [c.get("start_line"), c.get("end_line")]
                            if span[0] and span[1]:
                                candidates.append((c["path"], span[0], span[1]))

                    for path, s, e in candidates:
                        if task_turns >= max_turns:
                            break
                        task_turns += 1
                        txt, lines = read_span(path, s, e)
                        toks = count_tokens(txt)
                        task_tokens += toks
                        task_read_tok += toks
                        for p, lns in lines.items():
                            accumulated_lines[p].update(lns)
                        if all(found(t["evidence"], accumulated_lines)):
                            task_solved = True
                            for ev in t["evidence"]:
                                ev_txt, _ = read_span(ev["path"], ev["lines"][0], ev["lines"][1])
                                task_ev_tok += count_tokens(ev_txt)
                            break
            else:
                # Ripgrep loop: run rg, inspect match windows
                windows = ripgrep_windows(rg_cmd, root, t["query"], repo["language"])
                task_turns += 1
                task_tokens += 150  # Search command response overhead
                task_read_tok += 150

                for path, lo, hi in windows[: max_turns - 1]:
                    task_turns += 1
                    txt, lines = read_span(path, lo, hi)
                    toks = count_tokens(txt)
                    task_tokens += toks
                    task_read_tok += toks
                    for p, lns in lines.items():
                        accumulated_lines[p].update(lns)
                    if all(found(t["evidence"], accumulated_lines)):
                        task_solved = True
                        for ev in t["evidence"]:
                            ev_txt, _ = read_span(ev["path"], ev["lines"][0], ev["lines"][1])
                            task_ev_tok += count_tokens(ev_txt)
                        break

            if task_solved:
                solved += 1
                total_turns += task_turns
                total_tokens += task_tokens
                evidence_tokens += task_ev_tok
                read_tokens += task_read_tok

            per_task_stats.append(
                {
                    "task": t["id"],
                    "solved": task_solved,
                    "turns": task_turns,
                    "tokens": task_tokens,
                }
            )

        n = max(1, solved)
        precision = evidence_tokens / max(1, read_tokens)
        results[method] = {
            "solved": solved,
            "total_tasks": len(tasks),
            "success_rate": round(solved / len(tasks), 3),
            "mean_turns_to_answer": round(total_turns / n, 2),
            "mean_tokens_to_answer": round(total_tokens / n),
            "precision_per_read": round(precision, 4),
            "per_task": per_task_stats,
        }

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--cache", type=Path, default=Path(tempfile.gettempdir()) / "r2g-bench-real"
    )
    parser.add_argument("--tasks", type=Path, default=BENCH / "tasks_structural.json")
    parser.add_argument("--max-turns", type=int, default=6)
    parser.add_argument("--out", type=Path, default=BENCH / "agent_results.json")
    parser.add_argument("--rg", default="rg", help="ripgrep command (default: rg)")
    args = parser.parse_args()

    rg_cmd = shlex.split(args.rg)
    if shutil.which(rg_cmd[0]) is None:
        raise SystemExit("ripgrep (rg) is required for baseline comparison")

    repos = {r["name"]: r for r in json.loads((BENCH / "repos.json").read_text())["repos"]}
    tasks = json.loads(args.tasks.read_text())["tasks"]
    args.cache.mkdir(parents=True, exist_ok=True)

    indexes: dict[str, Index] = {}
    roots: dict[str, Path] = {}
    for name, repo in repos.items():
        roots[name] = checkout(repo, args.cache)
        out = args.cache / f"{name}.r2g"
        if not out.exists():
            from repo2graph.cli import main as cli_main

            if cli_main(["build", str(roots[name]), "-o", str(out)]) != 0:
                raise SystemExit(f"{name}: repo2graph build failed")
        indexes[name] = Index(out)

    results = simulate_agent_loop(tasks, repos, indexes, roots, rg_cmd, max_turns=args.max_turns)

    args.out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf8")

    print(
        f"\n=== Agent Simulation Results ({args.tasks.name}, {len(tasks)} tasks, max_turns={args.max_turns}) ==="
    )
    print("| Method | Success Rate | Mean Turns | Mean Tokens | Precision/Read |")
    print("|---|---:|---:|---:|---:|")
    for method, res in results.items():
        print(
            f"| {method} | {res['solved']}/{res['total_tasks']} ({res['success_rate']:.0%}) | "
            f"{res['mean_turns_to_answer']:.1f} | {res['mean_tokens_to_answer']:,} | "
            f"{res['precision_per_read']:.1%} |"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
