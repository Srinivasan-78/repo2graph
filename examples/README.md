# Real-world repository examples

Five public repositories, each analyzed by `repo2graph` at a pinned commit, with the exact
reproduction command for every one. This is evidence, not a demo: every number below came from an
actual run recorded in [`../benchmarks/results.json`](../benchmarks/results.json), never typed in
by hand.

The registry that drives generation is [`repositories.yaml`](repositories.yaml); the generator is
[`../scripts/generate_examples.py`](../scripts/generate_examples.py). See
[`../docs/architecture.md`](../docs/architecture.md) for how the pipeline works and
[`ATTRIBUTIONS.md`](ATTRIBUTIONS.md) for what is and is not committed from each repository.

## The five repositories

| Repository | Language(s) | Scope | Files | Nodes | Edges | Example |
|---|---|---|---:|---:|---:|---|
| [Django](https://github.com/django/django) | Python | full repository | 5,629 | 55,810 | 303,339 | [examples/django](django/) |
| [Kubernetes](https://github.com/kubernetes/kubernetes) | Go | scoped (controllers, scheduler, API server endpoints) | 1,084 | 14,451 | 110,246 | [examples/kubernetes](kubernetes/) |
| [TensorFlow](https://github.com/tensorflow/tensorflow) | Python, C++ | scoped (Python/C++ framework boundary) | 1,022 | 21,380 | 115,984 | [examples/tensorflow](tensorflow/) |
| [VS Code](https://github.com/microsoft/vscode) | TypeScript | scoped (`src/vs/`, capped at 6,000 files) | 6,000 | 113,080 | 656,158 | [examples/vscode](vscode/) |
| [Linux kernel](https://github.com/torvalds/linux) | C | scoped (`kernel/`, `fs/ext4/`, `drivers/net/.../e1000/`, `include/linux/`) | 3,660 | 136,219 | 256,413 | [examples/linux](linux/) |

"Full repository" means no `--include`/`--exclude` narrowing — every file `repo2graph` would index
on a plain `repo2graph build`. "Scoped" means only the listed subtrees were cloned and indexed; see
each repository's own README.md, "Why this scope," for the reasoning, and
[what it can't do](../README.md#what-it-cant-do) for why the other four are not indexed
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
└── overview.md        the prose repo map (languages, most depended-on files, most called symbols)
```

Two files, both prose. The generator writes a full artifact set beside them — `nodes.jsonl.gz`,
`edges.jsonl.gz`, `manifest.json`, `stats.json`, `graph.html` and `flows/*.json` — but none of it is
committed: it was about 28 MB of generated binary and boilerplate that every clone of this
repository had to carry, and it was dropped in `4e96b628`. Run the generator yourself (below) if you
want the graphs; nothing about them is secret, only large. Each example's own `README.md` carries the
numbers and the pinned commit, which is what the tables here and in `../benchmarks/results.json`
are checked against.

## Reproduce any of these

```bash
pip install pyyaml   # dev-only, not a repo2graph runtime dependency
python scripts/generate_examples.py --repo django
python scripts/generate_examples.py --all
```

The generator clones each repository fresh into a scratch directory (network phase), runs
`repo2graph.graph.build()` / `export.dump_all()` against the local checkout only (analysis phase —
no network access from that point on), runs the example queries, validates the result, and writes
`examples/<id>/`. See [docs/architecture.md](../docs/architecture.md) for the full pipeline and
[../README.md](../README.md) for the methodology and how staleness is handled.

Because every one of the five repositories moves upstream, re-running `--all` today will index a
newer commit and produce different numbers than the table above — that is expected, and is exactly
why every example records the commit it was built from, under "Revision" in its own `README.md`,
rather than a branch name.

## Contributing a new example

Add an entry to [`repositories.yaml`](repositories.yaml) — repository URL, ref, clone depth, scope
(`full` or `scoped` with `include`/`exclude`/`max_files`), language focus, category, a `why:`
paragraph, and 3-5 architecture-relevant example queries — then run
`python scripts/generate_examples.py --repo <id>` and commit the `README.md` and `overview.md` it
writes, leaving the graph artifacts uncommitted as above. No new Python code is needed for a
well-behaved repository; the framework is data-driven by design (see
[examples/repositories.yaml](repositories.yaml)).
