#!/usr/bin/env python3
"""Validate a benchmark task file against the built indexes of the pinned repos.

A benchmark question is only as good as its evidence. A line range typed from
memory, a symbol that was renamed between releases, or a path relative to the
wrong root all produce a task that scores every retriever at zero and looks
like a retrieval failure. This script refuses such a file before it is ever
measured against.

Every evidence entry is checked against `nodes.jsonl` from the index built for
that repo, which is the same symbol table retrieval itself scores over:

* the repo is one of the pinned four
* `path` resolves under the repo's configured `root`
* a symbol node exists at that path whose qualname (or name) matches
* that node's `start_line` is within ``--slack`` lines of ``lines[0]``
* ``lines`` is a two-element, non-inverted range inside the file
* ids are unique, and no query duplicates another task's query

A symbol that exists in the file but not in the index is reported separately
from one that is absent entirely: the first is a parser gap, the second is a
bad question, and they call for different fixes.

Usage:
    python scripts/validate_tasks.py --cache DIR TASKS_JSON [TASKS_JSON ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmarks" / "real"


def load_nodes(index_dir: Path) -> list[dict[str, Any]]:
    """Symbol nodes from a built index, or [] when the index is absent."""
    path = index_dir / "agent" / "nodes.jsonl"
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("type") == "symbol":
                out.append(rec)
    return out


def validate(
    tasks: list[dict[str, Any]],
    repos: dict[str, dict[str, Any]],
    cache: Path,
    slack: int,
) -> tuple[list[str], list[str]]:
    """Check every task's evidence against the built indexes.

    Returns:
        (problems, gaps). `problems` are defects in the task file itself -- a
        wrong path, an impossible line range, a duplicate -- and make the file
        unusable. `gaps` are evidence symbols that exist in the source but not
        in the index: a parser limitation, not a bad question, and the file
        stays usable with them recorded.
    """
    problems: list[str] = []
    gaps: list[str] = []
    nodes_by_repo: dict[str, list[dict[str, Any]]] = {}
    seen_ids: dict[str, str] = {}
    seen_queries: dict[str, str] = {}

    for t in tasks:
        tid = str(t.get("id") or "<no id>")

        for field in ("id", "repo", "kind", "query", "evidence"):
            if not t.get(field):
                problems.append(f"{tid}: missing or empty field {field!r}")
        if not t.get("evidence"):
            continue

        if tid in seen_ids:
            problems.append(f"{tid}: duplicate id")
        seen_ids[tid] = tid

        q_norm = " ".join(str(t.get("query", "")).lower().split())
        if q_norm and q_norm in seen_queries:
            problems.append(f"{tid}: query duplicates {seen_queries[q_norm]}")
        seen_queries[q_norm] = tid

        repo_name = str(t.get("repo"))
        repo = repos.get(repo_name)
        if repo is None:
            problems.append(f"{tid}: unknown repo {repo_name!r}, expected one of {sorted(repos)}")
            continue

        src_root = cache / repo_name / repo["root"]
        if repo_name not in nodes_by_repo:
            nodes_by_repo[repo_name] = load_nodes(cache / f"{repo_name}.r2g")
        nodes = nodes_by_repo[repo_name]
        if not nodes:
            problems.append(
                f"{tid}: no built index at {cache / (repo_name + '.r2g')} -- "
                "run scripts/bench_real_repos.py with this --cache first"
            )
            continue

        for ev in t["evidence"]:
            ev_path = str(ev.get("path") or "")
            symbol = str(ev.get("symbol") or "")
            lines = ev.get("lines")
            label = f"{tid}: {ev_path}::{symbol}"

            if not isinstance(lines, list) or len(lines) != 2:
                problems.append(f"{label}: lines must be a two-element [start, end]")
                continue
            start, end = lines
            if not isinstance(start, int) or not isinstance(end, int):
                problems.append(f"{label}: lines must be integers, got {lines!r}")
                continue
            if start < 1 or end < start:
                problems.append(f"{label}: inverted or non-positive range {lines!r}")
                continue

            abs_path = src_root / ev_path
            if not abs_path.is_file():
                problems.append(
                    f"{label}: path does not exist under the repo root "
                    f"({src_root.name}/) -- paths are relative to root, not the clone"
                )
                continue

            n_lines = abs_path.read_text(encoding="utf8", errors="replace").count("\n") + 1
            if end > n_lines:
                problems.append(f"{label}: end line {end} is past EOF ({n_lines} lines)")
                continue

            matches = [
                n
                for n in nodes
                if n.get("path") == ev_path
                and (n.get("qualname") == symbol or n.get("name") == symbol)
            ]
            if not matches:
                in_file = [n for n in nodes if n.get("path") == ev_path]
                hint = ""
                if in_file:
                    near = sorted(
                        in_file, key=lambda n: abs(int(n.get("start_line") or 0) - start)
                    )[:3]
                    hint = " nearest indexed symbols: " + ", ".join(
                        f"{n.get('qualname')}@{n.get('start_line')}" for n in near
                    )
                # Not indexed splits into two very different things, and calling
                # both a "parser gap" once let a mistyped symbol through as a
                # finding about the tool:
                #
                #   gap     -- the definition really is at these lines and the
                #              extractor skipped it (a class field holding an
                #              arrow function, an export alias). A fair question;
                #              keep it and measure the loss.
                #   problem -- the evidence names something not defined here at
                #              all. A defect in the task, and it must not be
                #              laundered into a complaint about the parser.
                #
                # Discriminator: for a dotted `Container.member`, the container
                # must itself be an indexed symbol in this file -- that is what
                # separates `Context.json` (Context is indexed; the field was
                # skipped) from `HonoBase.constructor` (nothing named HonoBase is
                # defined here; the class is `Hono`, re-exported under an alias).
                # For a bare name, require it on the declaration line, where a
                # definition's own name always appears.
                if "." in symbol:
                    container = symbol.rsplit(".", 1)[0]
                    plausible = any(
                        n.get("path") == ev_path
                        and (n.get("qualname") == container or n.get("name") == container)
                        for n in nodes
                    )
                else:
                    head = "\n".join(
                        abs_path.read_text(encoding="utf8", errors="replace").split("\n")[
                            start - 1 : start + 1
                        ]
                    )
                    plausible = symbol in head

                if plausible:
                    gaps.append(f"{label}: defined here but not indexed.{hint}")
                else:
                    problems.append(
                        f"{label}: nothing by that name is defined at these lines, and it is "
                        f"not a parser gap -- check the symbol name.{hint}"
                    )
                continue

            # The evidence range is the span a human marked as the answer, so an
            # indexed symbol that begins anywhere inside it is consistent -- not
            # only one that begins at its first line. TypeScript overloads are
            # why: `HonoRequest.param` declares four signatures and then the
            # implementation, and the index records the implementation, several
            # lines after the declaration a reader would point at.
            if not any(
                start <= int(m.get("start_line") or 0) <= end
                or abs(int(m.get("start_line") or 0) - start) <= slack
                for m in matches
            ):
                got = ", ".join(f"{m.get('start_line')}" for m in matches)
                problems.append(
                    f"{label}: evidence is lines {start}-{end} but the indexed symbol "
                    f"starts at {got}, outside that range and beyond slack {slack}"
                )

    return problems, gaps


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("tasks", nargs="+", type=Path, help="task JSON file(s) to validate")
    ap.add_argument("--cache", type=Path, required=True, help="bench cache: clones + .r2g indexes")
    ap.add_argument(
        "--strict-index",
        action="store_true",
        help="also fail on evidence symbols that are in the source but not in the index "
        "(off by default: a parser gap is a finding about repo2graph, not about the task)",
    )
    ap.add_argument(
        "--slack",
        type=int,
        default=3,
        help="allowed drift between lines[0] and the indexed start_line (default: 3)",
    )
    args = ap.parse_args(argv)

    repos = {r["name"]: r for r in json.loads((BENCH / "repos.json").read_text())["repos"]}

    failed = False
    for path in args.tasks:
        if not path.exists():
            print(f"MISSING  {path}")
            failed = True
            continue
        tasks = json.loads(path.read_text(encoding="utf8"))["tasks"]
        problems, gaps = validate(tasks, repos, args.cache, args.slack)
        if args.strict_index:
            problems, gaps = problems + gaps, []
        if problems:
            failed = True
            print(f"FAIL     {path}  ({len(tasks)} tasks, {len(problems)} problems)")
            for p in problems:
                print(f"  - {p}")
        else:
            print(f"OK       {path}  ({len(tasks)} tasks, every evidence range verified)")
        if gaps:
            print(f"  PARSER GAPS ({len(gaps)}) -- these questions are kept and measured:")
            for g in gaps:
                print(f"  ! {g}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
