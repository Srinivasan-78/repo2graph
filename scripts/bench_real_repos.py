#!/usr/bin/env python3
"""Retrieval benchmark on pinned third-party repositories.

Asks each question in `benchmarks/real/tasks.json` of three retrievers, each
held to the same token budget, and scores what came back against the
definitions that answer it:

* ``repo2graph``      -- ``Index.pack_context`` with the MCP server's defaults
                         (k=8, hops=1, secrets excluded).
* ``repo2graph-bm25`` -- the same call with ``expand_graph=False``: the BM25
                         seeds alone, so the difference is what the graph adds.
* ``ripgrep``         -- ``rg`` for the question's words, then read +/-15 lines
                         around the best-scoring hits until the budget is spent.
                         This is the grep-then-read loop a coding agent runs,
                         without the model choosing better search terms.

An evidence definition counts as *found* only when its first line and the next
nine lines (or the whole definition, if shorter) are all in the returned text.
A file path in passing, or a compressed signature-only neighbour, is not enough.

This measures retrieval, not end-to-end agent success: no model reads the
output. Tokens are ``len(text) // 4`` for every retriever.

Usage:
    python scripts/bench_real_repos.py [--cache DIR] [--budgets 2000,4000,8000]
"""

from __future__ import annotations

import argparse
import contextlib
import io
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

from repo2graph.cli import main as cli_main  # noqa: E402
from repo2graph.query import Index, count_tokens  # noqa: E402

BENCH = ROOT / "benchmarks" / "real"
WINDOW = 15
HEAD_LINES = 10
STOPWORDS = frozenset(
    "a an and are as at be by does do for from get gets how in into is it its like "
    "of on or the their then to up what when where which who why with".split()
)
RG_TYPES = {"python": "py", "typescript": "ts", "javascript": "js"}


def source_version(root: Path = ROOT) -> str:
    """The version of the *checked-out* source, never of an installed dist-info.

    `repo2graph.__version__` asks importlib.metadata first, and an editable
    install's dist-info keeps whatever version it was installed at -- a 2.2.0
    run was labelled 2.1.0. pyproject.toml's `[project] version` is what the
    code on disk is; the `__init__` fallback constant is the second source.
    (Regex, not tomllib: Python 3.10 is supported.)
    """
    try:
        text = (root / "pyproject.toml").read_text(encoding="utf8")
        section = re.search(r"(?ms)^\[project\]\s*$(.*?)(?=^\[|\Z)", text)
        m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', section.group(1) if section else "")
        if m:
            return m.group(1)
    except OSError:
        pass
    try:
        init = (root / "repo2graph" / "__init__.py").read_text(encoding="utf8")
        m = re.search(r'(?m)^\s*__version__\s*=\s*"([^"]+)"', init)
        if m:
            return m.group(1)
    except OSError:
        pass
    return "unknown"


def source_commit(root: Path = ROOT) -> dict:
    """The repo2graph commit the benchmark ran from, and whether it was dirty."""

    def _git(*args: str) -> str | None:
        try:
            res = subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if res.returncode != 0:
            return None
        # bytes + surrogateescape, never text=True (CONTRIBUTING.md)
        return res.stdout.decode("utf8", "surrogateescape").strip()

    sha = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain", "--untracked-files=no")
    return {"commit": sha, "dirty": None if status is None else bool(status)}


def checkout(repo: dict, cache: Path) -> Path:
    dest = cache / repo["name"]
    if not dest.exists():
        subprocess.run(
            ["git", "clone", "-q", "--depth", "1", "--branch", repo["ref"], repo["url"], str(dest)],
            check=True,
        )
    sha = (
        subprocess.run(
            ["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, check=True
        )
        .stdout.decode()
        .strip()
    )
    if sha != repo["sha"]:
        raise SystemExit(
            f"{repo['name']}: {repo['ref']} is {sha}, tasks were written at {repo['sha']}"
        )
    return dest / repo["root"]


def covered_lines_r2g(pack: dict, root: Path) -> dict[str, set[int]]:
    """Source lines whose text is actually in the pack, per path.

    A chunk's `start_line`/`end_line` describe the whole symbol, not the text
    returned: a split symbol's later parts, a file residual with its symbols cut
    out, and a neighbour compressed to its signature all return less than that
    range. So each returned line is aligned, in order, to the next source line
    in the symbol's range with the same text. Synthetic header lines
    (`# file:`, `# calls:`, ...) match nothing and earn nothing.
    """
    out: dict[str, set[int]] = defaultdict(set)
    sources: dict[str, list[str]] = {}
    for c in pack["chunks"]:
        path = c["path"]
        src = sources.get(path)
        if src is None:
            raw = (root / path).read_text(encoding="utf8", errors="replace")
            src = sources[path] = [ln.rstrip("\r") for ln in raw.split("\n")]
        start = max(1, c.get("start_line") or 1)
        end = min(len(src), c.get("end_line") or len(src))
        cursor = start
        for line in (c.get("text") or "").split("\n"):
            line = line.rstrip("\r")
            for n in range(cursor, end + 1):
                if src[n - 1] == line:
                    out[path].add(n)
                    cursor = n + 1
                    break
    return out


def terms_of(query: str) -> list[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_\-]*", query.lower())
    seen: list[str] = []
    for w in words:
        if len(w) >= 3 and w not in STOPWORDS and w not in seen:
            seen.append(w)
    return seen


def ripgrep(
    rg: list[str], root: Path, query: str, language: str, budget: int
) -> tuple[str, dict[str, set[int]]]:
    terms = terms_of(query)
    cmd = [*rg, "-n", "-i", "-F", "--no-heading", "-t", RG_TYPES[language]]
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

    # Score every hit line by the distinct query terms within its window.
    windows = []
    for path, lines in hits.items():
        for ln in lines:
            near = [lines[x] for x in lines if abs(x - ln) <= WINDOW]
            score = sum(1 for t in terms if any(t in s for s in near))
            windows.append((score, len(near), path, ln))
    windows.sort(key=lambda w: (-w[0], -w[1], w[2], w[3]))

    text, taken = "", defaultdict(set)
    file_cache: dict[str, list[str]] = {}
    for _score, _n, path, ln in windows:
        if ln in taken[path]:
            continue
        src = file_cache.setdefault(
            path, (root / path).read_text(encoding="utf8", errors="replace").split("\n")
        )
        lo, hi = max(1, ln - WINDOW), min(len(src), ln + WINDOW)
        new = [x for x in range(lo, hi + 1) if x not in taken[path]]
        block = f"\n### {path}:{new[0]}-{new[-1]}\n" + "\n".join(src[x - 1] for x in new) + "\n"
        if count_tokens(text + block) > budget:
            continue
        text += block
        taken[path].update(new)
    return text, taken


def found(evidence: list[dict], lines: dict[str, set[int]]) -> list[bool]:
    out = []
    for ev in evidence:
        s, e = ev["lines"]
        need = range(s, min(e, s + HEAD_LINES - 1) + 1)
        out.append(all(x in lines.get(ev["path"], ()) for x in need))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", type=Path, default=Path(tempfile.gettempdir()) / "r2g-bench-real")
    ap.add_argument("--budgets", default="2000,4000,8000")
    ap.add_argument("--out", type=Path, default=BENCH / "results.json")
    ap.add_argument("--rg", default="rg", help="ripgrep command, shell-split (default: rg)")
    ap.add_argument(
        "--repos",
        type=Path,
        default=None,
        help="repository set (default: benchmarks/real/repos.json)",
    )
    ap.add_argument(
        "--tasks",
        type=Path,
        default=None,
        help="task set (default: benchmarks/real/tasks.json; disables the structural set)",
    )
    args = ap.parse_args(argv)
    rg = shlex.split(args.rg)
    if shutil.which(rg[0]) is None:
        raise SystemExit("ripgrep (rg) is required for the baseline")
    budgets = [int(b) for b in args.budgets.split(",")]

    repos_path = args.repos or (BENCH / "repos.json")
    tasks_path = args.tasks or (BENCH / "tasks.json")
    repos = {r["name"]: r for r in json.loads(repos_path.read_text(encoding="utf8"))["repos"]}
    tasks = json.loads(tasks_path.read_text(encoding="utf8"))["tasks"]
    args.cache.mkdir(parents=True, exist_ok=True)

    indexes: dict[str, Index] = {}
    roots: dict[str, Path] = {}
    for name, repo in repos.items():
        roots[name] = checkout(repo, args.cache)
        out = args.cache / f"{name}.r2g"
        if out.exists():
            shutil.rmtree(out)
        with contextlib.redirect_stdout(io.StringIO()):
            if cli_main(["build", str(roots[name]), "-o", str(out)]) != 0:
                raise SystemExit(f"{name}: repo2graph build failed")
        indexes[name] = Index(out)

    rows = []
    structural_tasks_path = BENCH / "tasks_structural.json"
    # The structural set names symbols in repos.json's repositories, so it is
    # only meaningful alongside the default task set. An explicit --tasks is a
    # different corpus; pairing it with these would score them against an index
    # that does not contain their evidence.
    structural_tasks = (
        json.loads(structural_tasks_path.read_text(encoding="utf8"))["tasks"]
        if args.tasks is None and structural_tasks_path.exists()
        else []
    )
    structural_rows = []

    for t in tasks:
        repo, idx, root = repos[t["repo"]], indexes[t["repo"]], roots[t["repo"]]
        for budget in budgets:
            full = idx.pack_context(t["query"], budget_tokens=budget, exclude_secrets=True)
            cite = idx.pack_context(
                t["query"],
                budget_tokens=budget,
                exclude_secrets=True,
                k=10,
                neighbours="cite",
                max_neighbours=2,
                precision_first=True,
            )
            bm25 = idx.pack_context(
                t["query"], budget_tokens=budget, exclude_secrets=True, expand_graph=False
            )
            rg_text, rg_lines = ripgrep(rg, root, t["query"], repo["language"], budget)
            for method, lines, used in (
                ("repo2graph", covered_lines_r2g(full, root), full["tokens_used"]),
                ("repo2graph-cite", covered_lines_r2g(cite, root), cite["tokens_used"]),
                ("repo2graph-bm25", covered_lines_r2g(bm25, root), bm25["tokens_used"]),
                ("ripgrep", rg_lines, count_tokens(rg_text)),
            ):
                hit = found(t["evidence"], lines)
                rows.append(
                    {
                        "task": t["id"],
                        "repo": t["repo"],
                        "kind": t["kind"],
                        "budget": budget,
                        "method": method,
                        "tokens_used": used,
                        "found": sum(hit),
                        "evidence": len(hit),
                        "missed": [ev["symbol"] for ev, h in zip(t["evidence"], hit) if not h],
                    }
                )

    for t in structural_tasks:
        repo, idx, root = repos[t["repo"]], indexes[t["repo"]], roots[t["repo"]]
        for budget in budgets:
            full = idx.pack_context(t["query"], budget_tokens=budget, exclude_secrets=True)
            cite = idx.pack_context(
                t["query"],
                budget_tokens=budget,
                exclude_secrets=True,
                k=10,
                neighbours="cite",
                max_neighbours=2,
                precision_first=True,
            )
            bm25 = idx.pack_context(
                t["query"], budget_tokens=budget, exclude_secrets=True, expand_graph=False
            )
            rg_text, rg_lines = ripgrep(rg, root, t["query"], repo["language"], budget)
            for method, lines, used in (
                ("repo2graph", covered_lines_r2g(full, root), full["tokens_used"]),
                ("repo2graph-cite", covered_lines_r2g(cite, root), cite["tokens_used"]),
                ("repo2graph-bm25", covered_lines_r2g(bm25, root), bm25["tokens_used"]),
                ("ripgrep", rg_lines, count_tokens(rg_text)),
            ):
                hit = found(t["evidence"], lines)
                structural_rows.append(
                    {
                        "task": t["id"],
                        "repo": t["repo"],
                        "kind": t["kind"],
                        "budget": budget,
                        "method": method,
                        "tokens_used": used,
                        "found": sum(hit),
                        "evidence": len(hit),
                        "missed": [ev["symbol"] for ev, h in zip(t["evidence"], hit) if not h],
                    }
                )

    summary = []
    for budget in budgets:
        for method in ("repo2graph", "repo2graph-cite", "repo2graph-bm25", "ripgrep"):
            sel = [r for r in rows if r["budget"] == budget and r["method"] == method]
            summary.append(
                {
                    "budget": budget,
                    "method": method,
                    "evidence_recall": round(
                        sum(r["found"] for r in sel) / sum(r["evidence"] for r in sel), 3
                    ),
                    "tasks_fully_answered": sum(r["found"] == r["evidence"] for r in sel),
                    "tasks_any_evidence": sum(r["found"] > 0 for r in sel),
                    "tasks": len(sel),
                    "mean_tokens_used": round(sum(r["tokens_used"] for r in sel) / len(sel)),
                }
            )

    structural_summary = []
    if structural_rows:
        for budget in budgets:
            for method in ("repo2graph", "repo2graph-cite", "repo2graph-bm25", "ripgrep"):
                sel = [
                    r for r in structural_rows if r["budget"] == budget and r["method"] == method
                ]
                structural_summary.append(
                    {
                        "budget": budget,
                        "method": method,
                        "evidence_recall": round(
                            sum(r["found"] for r in sel) / sum(r["evidence"] for r in sel), 3
                        ),
                        "tasks_fully_answered": sum(r["found"] == r["evidence"] for r in sel),
                        "tasks_any_evidence": sum(r["found"] > 0 for r in sel),
                        "tasks": len(sel),
                        "mean_tokens_used": round(sum(r["tokens_used"] for r in sel) / len(sel)),
                    }
                )

    provenance = source_commit()
    out_data = {
        "repo2graph_version": source_version(),
        "repo2graph_commit": provenance["commit"],
        "repo2graph_dirty": provenance["dirty"],
        "summary": summary,
        "rows": rows,
    }
    if structural_summary:
        out_data["structural_summary"] = structural_summary
        out_data["structural_rows"] = structural_rows

    args.out.write_text(
        json.dumps(out_data, indent=1) + "\n",
        encoding="utf8",
        newline="\n",
    )
    print("=== Lexical Benchmark (tasks.json, 35 tasks) ===")
    print("| budget | method | evidence recall | fully answered | any evidence | mean tokens |")
    print("|---:|---|---:|---:|---:|---:|")
    for s in summary:
        print(
            f"| {s['budget']:,} | {s['method']} | {s['evidence_recall']:.0%} | "
            f"{s['tasks_fully_answered']}/{s['tasks']} | {s['tasks_any_evidence']}/{s['tasks']} | "
            f"{s['mean_tokens_used']:,} |"
        )

    if structural_summary:
        print("\n=== Structural Benchmark (tasks_structural.json, 10 tasks) ===")
        print("| budget | method | evidence recall | fully answered | any evidence | mean tokens |")
        print("|---:|---|---:|---:|---:|---:|")
        for s in structural_summary:
            print(
                f"| {s['budget']:,} | {s['method']} | {s['evidence_recall']:.0%} | "
                f"{s['tasks_fully_answered']}/{s['tasks']} | {s['tasks_any_evidence']}/{s['tasks']} | "
                f"{s['mean_tokens_used']:,} |"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
