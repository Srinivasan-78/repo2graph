<!--
  @authormark v1 -- do not remove (authorship watermark)⁠​‌‌​​‌​​​​‌‌​​‌​​‌​‌‌​​​​‌‌‌​‌‌‌​‌​‌​​​‌​‌‌​‌‌​​​‌‌​‌​​‌​​‌‌​‌‌​​‌​‌​​‌​​​‌‌‌​​‌​​‌‌​‌‌​​​‌‌​‌​‌​‌‌​‌‌‌‌​​‌‌‌​​‌​‌​‌​​​​​‌‌​​​‌‌​​‌‌‌​​​​‌​​‌​‌​​‌‌‌​‌​‌​‌‌‌​‌​‌​‌‌​​‌‌​​‌​​‌​‌​⁠
  Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
  Author: https://github.com/Srinivasan-78
  SPDX-License-Identifier: MIT
  Fingerprint: AMK1.d2XwQli6R965o9Pc8JuufJ
-->
# Real-world repository examples

Five public repositories, each analyzed by `repo2graph` at a pinned commit, with the generated
graph committed alongside the exact reproduction command. This is evidence, not a demo: every
number below came from an actual run recorded in [`../benchmarks/results.json`](../benchmarks/results.json),
never typed in by hand.

The registry that drives generation is [`repositories.yaml`](repositories.yaml); the generator is
[`../scripts/generate_examples.py`](../scripts/generate_examples.py). See
[`../docs/examples.md`](../docs/examples.md) for how the pipeline works and
[`ATTRIBUTIONS.md`](ATTRIBUTIONS.md) for what is and is not committed from each repository.

## The five repositories

| Repository | Language(s) | Scope | Files | Nodes | Edges | Example |
|---|---|---|---:|---:|---:|---|
| [Django](https://github.com/django/django) | Python | full repository | 5,637 | 54,544 | 228,461 | [examples/django](django/) |
| [Kubernetes](https://github.com/kubernetes/kubernetes) | Go | scoped (controllers, scheduler, API server endpoints) | 1,082 | 14,197 | 83,525 | [examples/kubernetes](kubernetes/) |
| [TensorFlow](https://github.com/tensorflow/tensorflow) | Python, C++ | scoped (Python/C++ framework boundary) | 1,022 | 20,641 | 96,013 | [examples/tensorflow](tensorflow/) |
| [VS Code](https://github.com/microsoft/vscode) | TypeScript | scoped (`src/vs/`, capped at 6,000 files) | 6,000 | 113,115 | 431,453 | [examples/vscode](vscode/) |
| [Linux kernel](https://github.com/torvalds/linux) | C | scoped (`kernel/`, `fs/ext4/`, `drivers/net/.../e1000/`, `include/linux/`) | 3,660 | 136,182 | 257,655 | [examples/linux](linux/) |

"Full repository" means no `--include`/`--exclude` narrowing — every file `repo2graph` would index
on a plain `repo2graph build`. "Scoped" means only the listed subtrees were cloned and indexed; see
each repository's own README.md, "Why this scope," for the reasoning, and
[docs/limitations.md](../docs/limitations.md#extreme-scale) for why the other four are not indexed
whole. Django is the pipeline's full-repository baseline precisely because it is the one repository
in this set small enough for that comparison to mean something.

## Why these five

Chosen for architectural variety, not popularity: a large single-language monorepo (Kubernetes), a
genuinely multi-language repository where Python calls into C++ (TensorFlow), a mature single-
language framework (Django), a large single-language application (VS Code), and an extreme-scale,
maximally heterogeneous C codebase (Linux) — see each `why:` field in
[`repositories.yaml`](repositories.yaml) for the specific reasoning.

## What is in each `examples/<id>/`

```
examples/<id>/
├── README.md          repository, revision, why this repo/scope, stats, example queries, limitations
├── metadata.json       machine-readable: commit, versions, counts, timings (schema below)
├── nodes.jsonl.gz       every graph node: id, type, path, name, lines — gzipped, no source text
├── edges.jsonl.gz       every graph edge: src, dst, type — gzipped
├── overview.md          the prose repo map (languages, most depended-on files, most called symbols)
├── manifest.json        what every field in the other files means
├── stats.json            the raw counters (node/edge/symbol counts, parse errors, entrypoints)
├── graph.html            the interactive map — self-contained, opens in any browser, no network,
│                          capped at the 300 best-connected nodes
└── flows/                real `repo2graph query` results for that repo's example questions —
                           citations only (node id, path, lines, match reason), no source text
```

`metadata.json` schema (every field is measured, never estimated):

```json
{
  "repository": "https://github.com/<owner>/<repo>",
  "id": "kubernetes",
  "commit": "<full 40-char SHA>",
  "branch": "<ref analyzed>",
  "generated_at": "<UTC ISO 8601>",
  "repo2graph_version": "<version that produced this>",
  "graph_schema_version": "repo2graph/1",
  "languages": ["go"],
  "scope": "scoped",
  "include": ["<globs>", "..."],
  "nodes": 0, "edges": 0,
  "stats": { "...": "the same counters as stats.json" },
  "clone_seconds": 0.0, "build_seconds": 0.0,
  "viz_nodes": 300
}
```

## Reproduce any of these

```bash
pip install pyyaml   # dev-only, not a repo2graph runtime dependency
python scripts/generate_examples.py --repo django
python scripts/generate_examples.py --all
```

The generator clones each repository fresh into a scratch directory (network phase), runs
`repo2graph.graph.build()` / `export.dump_all()` against the local checkout only (analysis phase —
no network access from that point on), runs the example queries, validates the result, and writes
`examples/<id>/`. See [docs/examples.md](../docs/examples.md) for the full pipeline and
[docs/benchmarks.md](../docs/benchmarks.md) for the methodology and how staleness is handled.

Because every one of the five repositories moves upstream, re-running `--all` today will index a
newer commit and produce different numbers than the table above — that is expected, and is exactly
why every artifact here is pinned to the commit recorded in its own `metadata.json` rather than to
a branch name.

## Contributing a new example

Add an entry to [`repositories.yaml`](repositories.yaml) — repository URL, ref, clone depth, scope
(`full` or `scoped` with `include`/`exclude`/`max_files`), language focus, category, a `why:`
paragraph, and 3-5 architecture-relevant example queries — then run
`python scripts/generate_examples.py --repo <id>` and commit the result. No new Python code is
needed for a well-behaved repository; the framework is data-driven by design (see
[docs/examples.md](../docs/examples.md#adding-a-repository)).
