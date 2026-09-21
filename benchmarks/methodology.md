<!--
  @authormark v1 -- do not remove (authorship watermark)⁠​‌​‌‌​‌​​‌‌‌​‌​‌​‌​​​​‌​​‌​​​​‌​​‌​​‌​​‌​‌‌​‌‌‌‌​​‌‌​​‌​​‌​‌‌​​​​‌​​‌​‌​​‌‌​‌‌​​​‌​‌‌‌‌‌​‌​‌​​‌​​‌​​​​‌​​‌‌​​‌​‌​‌​​​​‌​​‌‌​​​​‌​‌​​‌‌‌‌​‌‌‌‌​‌​​‌‌​‌​​‌​‌‌‌​​​‌​‌‌‌​‌‌​​‌​‌​‌‌‌⁠
  Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
  Author: https://github.com/Srinivasan-78
  SPDX-License-Identifier: MIT
  Fingerprint: AMK1.ZuBBIo2XJl_RBeBaOziqvW
-->
# Methodology

How the numbers in [`results.json`](results.json) and [docs/benchmarks.md](../docs/benchmarks.md)
were produced, and what would have to match for you to reproduce them exactly.

## What is controlled

- **Repository revision.** Every result is pinned to the exact commit SHA `scripts/generate_examples.py`
  checked out, recorded in both `results.json` and that repository's `examples/<id>/metadata.json`.
  Re-running against the same `ref` (usually `main`/`master`) later will check out a *different*,
  newer commit — see [docs/benchmarks.md#staleness](../docs/benchmarks.md#staleness).
- **repo2graph version.** Recorded per result (`repo2graph_version`, from `repo2graph.__version__`
  at generation time).
- **Scope.** For the four scoped repositories, the exact `include`/`exclude`/`max_files` parameters
  are recorded in `metadata.json` and pinned in `examples/repositories.yaml` — the same repository
  at the same commit with different scope parameters is a different, not-comparable measurement.
- **Analysis parameters.** `git_history` (CO_CHANGE window), `jobs` (parser parallelism, left at the
  default: one process per core), and the format set written (`jsonl,overview,html`) are the same
  for every repository — see `generate_one()` in `scripts/generate_examples.py`.

## What is not controlled, and is not claimed to be

- **Hardware.** These are single-machine numbers (this project's own development machine, Windows,
  Python 3.13.15, whatever else happened to be running at the time) with no isolation, warm-up
  discipline, or repeated-trial averaging. Do not use `build_seconds` here for capacity planning on
  different hardware — see [docs/PERFORMANCE.md](../docs/PERFORMANCE.md) for numbers that were
  measured with that goal in mind, on a controlled synthetic fixture.
- **Network conditions.** `clone_seconds` reflects this run's actual bandwidth and GitHub's response
  time at that moment; it is the least reproducible number in the file for exactly that reason.
- **Peak memory.** Not measured — see [docs/benchmarks.md](../docs/benchmarks.md) for why.

## How results.json is updated

`scripts/generate_examples.py` merges new results into the existing file by repository URL (a
re-run of one repository replaces only that repository's entry, keeping the others as they were the
last time they were generated) — see `main()` in that script. This means `results.json` as committed
can hold results from *different generation runs*, potentially days apart, each internally
consistent but not a single synchronized snapshot. Each entry's own `generated_at` timestamp is the
source of truth for when that specific number was measured; do not assume every entry in the file
was produced by the same invocation.

## Reproducing a specific number

1. Find the repository's entry in `results.json` or `examples/<id>/metadata.json` for the exact
   `commit` and scope parameters.
2. `git clone --filter=blob:none <repository url>`, `git checkout <commit>` (and, for a scoped
   repository, `git sparse-checkout set` the same paths from `examples/repositories.yaml`).
3. Time `repo2graph build . -o .r2g --include <same include globs> --max-files <same max_files>
   --git-history <same git_history>` yourself.

A different number under otherwise-identical parameters most likely means different hardware or
network conditions, not a regression — cross-check against `docs/PERFORMANCE.md`'s controlled
synthetic-fixture numbers if you need a hardware-independent comparison.
