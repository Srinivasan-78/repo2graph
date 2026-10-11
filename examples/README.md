# Real-world repository examples

Eleven public repositories, each analyzed by `repo2graph` at a pinned commit, with the exact
reproduction command for every one. This is evidence, not a demo: every number below came from an
actual run recorded in [`../benchmarks/results.json`](../benchmarks/results.json), never typed in
by hand.

Each example records the repo2graph version that produced it, next to the upstream commit it
indexed. A pinned commit fixes the *source* but not the *analyser*, and call resolution does change
between versions. The gallery was generated in two batches, and figures are directly comparable
only within one:

- **The original five** — Django, Kubernetes, TensorFlow, VS Code and Linux — were regenerated
  together on 2026-10-06 by repo2graph 2.2.0.
- **Six added on 2026-10-11** — React, FastAPI, Gin, ripgrep, Guava and Jekyll — were generated
  from the `develop` branch at `23bf953`: version string 3.0.0, plus the changes listed under
  `[Unreleased]` in [`../CHANGELOG.md`](../CHANGELOG.md). Those changes include `TESTS` edges,
  which the 2.2.0 batch does not have, so edge totals in particular do not compare across the two
  batches — on Gin, `TESTS` is 9,187 of 19,552 edges. Their pages also carry three rows the older
  pages lack: files with parse errors, symbols of every kind, and methods.

`python scripts/generate_examples.py --all` regenerates all eleven with one version, which is what
makes them one batch again.

The registry that drives generation is [`repositories.yaml`](repositories.yaml); the generator is
[`../scripts/generate_examples.py`](../scripts/generate_examples.py). See
[`../docs/architecture.md`](../docs/architecture.md) for how the pipeline works and
[`ATTRIBUTIONS.md`](ATTRIBUTIONS.md) for what is and is not committed from each repository.

## The eleven repositories

| Repository | Language(s) | Scope | Files | Nodes | Edges | repo2graph | Example |
|---|---|---|---:|---:|---:|---|---|
| [Django](https://github.com/django/django) | Python | full repository | 5,630 | 56,074 | 301,017 | 2.2.0 | [examples/django](django/) |
| [Kubernetes](https://github.com/kubernetes/kubernetes) | Go | scoped (controllers, scheduler, API server endpoints) | 1,085 | 15,174 | 117,066 | 2.2.0 | [examples/kubernetes](kubernetes/) |
| [TensorFlow](https://github.com/tensorflow/tensorflow) | Python, C++ | scoped (Python/C++ framework boundary) | 1,022 | 24,730 | 145,790 | 2.2.0 | [examples/tensorflow](tensorflow/) |
| [VS Code](https://github.com/microsoft/vscode) | TypeScript | scoped (`src/vs/`, capped at 6,000 files) | 6,000 | 114,070 | 664,312 | 2.2.0 | [examples/vscode](vscode/) |
| [Linux kernel](https://github.com/torvalds/linux) | C | scoped (`kernel/`, `fs/ext4/`, `drivers/net/.../e1000/`, `include/linux/`) | 3,660 | 185,496 | 310,854 | 2.2.0 | [examples/linux](linux/) |
| [React](https://github.com/facebook/react) | JavaScript (Flow) | scoped (core runtime packages, no `__tests__/`) | 396 | 3,468 | 8,093 | 3.0.0+ | [examples/react](react/) |
| [FastAPI](https://github.com/fastapi/fastapi) | Python | full repository, with git history | 2,994 | 10,384 | 46,738 | 3.0.0+ | [examples/fastapi](fastapi/) |
| [Gin](https://github.com/gin-gonic/gin) | Go | full repository, with git history | 128 | 2,106 | 19,552 | 3.0.0+ | [examples/gin](gin/) |
| [ripgrep](https://github.com/BurntSushi/ripgrep) | Rust | full repository, with git history | 229 | 4,857 | 20,381 | 3.0.0+ | [examples/ripgrep](ripgrep/) |
| [Guava](https://github.com/google/guava) | Java | scoped (`guava/src/`, the JRE library's production source) | 611 | 16,268 | 80,339 | 3.0.0+ | [examples/guava](guava/) |
| [Jekyll](https://github.com/jekyll/jekyll) | Ruby | full repository, with git history | 771 | 2,888 | 10,403 | 3.0.0+ | [examples/jekyll](jekyll/) |

`3.0.0+` is the second batch above: `develop` at `23bf953`, not the 3.0.0 release.

"Full repository" means no `--include`/`--exclude` narrowing — every file `repo2graph` would index
on a plain `repo2graph build`. The five full-repository entries — Django, FastAPI, Gin, ripgrep and
Jekyll — are cloned 300 commits deep and mine the last 200 for `CO_CHANGE` edges. "Scoped" means
only the listed subtrees were cloned and indexed, from a one-commit clone with no history and
therefore no `CO_CHANGE` edges; see each repository's own README.md, "Why this scope," for the
reasoning.

## Read React's page before quoting it

React's source is JavaScript with Flow type annotations, and repo2graph parses `.js` with the plain
JavaScript grammar, which has no Flow support. At the pinned commit, 241 of the 386 parsed files
report parse errors. A hand count on two reconciler files shows what that costs: of 234 top-level
`function` declarations in `ReactFiberWorkLoop.js` and `ReactFiberHooks.js`, 101 were indexed as
functions (94 of 125, and 7 of 109) — `renderWithHooks`, the entry point for rendering a function
component, is not among them. The React graph is a lower bound on the code's real structure.
It is in the gallery because a Flow codebase is a common case and this is the honest picture of it
today — not as a showcase.

## Why these eleven

The first five were chosen for architectural variety, not popularity: a large single-language
monorepo (Kubernetes), a genuinely multi-language repository where Python calls into C++
(TensorFlow), a mature single-language framework (Django), a large single-language application (VS
Code), and an extreme-scale, maximally heterogeneous C codebase (Linux).

The next six add recognisability and the languages the first five did not cover: JavaScript
(React), Rust (ripgrep), Java (Guava) and Ruby (Jekyll), plus two small repositories indexed whole
with git history (FastAPI and Gin), so the gallery shows `CO_CHANGE` edges and not only scoped
slices. FastAPI is also one of the four repositories in the
[retrieval benchmark](../benchmarks/real/README.md), and ripgrep is the tool that benchmark's grep
baseline runs. See each `why:` field in [`repositories.yaml`](repositories.yaml) for the specific
reasoning.

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

Because every one of these repositories moves upstream, re-running `--all` today will index a
newer commit and produce different numbers than the table above — that is expected, and is exactly
why every example records the commit it was built from, under "Revision" in its own `README.md`,
rather than a branch name.

## Contributing a new example

Add an entry to [`repositories.yaml`](repositories.yaml) — repository URL, ref, clone depth, scope
(`full` or `scoped` with `include`/`exclude`/`max_files`), language focus, category, a `why:`
paragraph, and 3-5 architecture-relevant example queries — then run
`python scripts/generate_examples.py --repo <id>` and commit the `README.md` and `overview.md` it
writes, leaving the graph artifacts uncommitted as above. Add a row to the table here with the
numbers from `../benchmarks/results.json`; `tests/test_doc_consistency.py` fails if the two
disagree. No new Python code is needed for a well-behaved repository; the framework is data-driven
by design (see [examples/repositories.yaml](repositories.yaml)).
