<!--
  @authormark v1 -- do not remove (authorship watermark)⁠​​‌‌​​​​​‌​​​‌‌‌​‌‌‌​‌​​​‌​‌​​​​​‌​‌‌‌‌‌​‌​‌‌​​​​‌‌​​‌​‌​‌‌​​‌‌‌​‌‌‌​‌‌‌​​‌‌​​​​​‌​‌‌​‌​​‌‌​​‌​‌​‌‌​‌‌​‌​​‌‌​‌​​​‌​​‌​​​​‌‌​​‌​‌​‌‌‌‌​‌​​‌‌​‌​‌‌​‌‌​​‌‌​​‌​‌​‌​‌​‌‌​​​‌​​‌​‌​‌​‌⁠
  Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
  Author: https://github.com/Srinivasan-78
  SPDX-License-Identifier: MIT
  Fingerprint: AMK1.0GtP_Xegw0Zem4HezkfUbU
-->
# Real-world examples: how the pipeline works

The five repositories under [examples/](../examples/) — Django, Kubernetes, TensorFlow, VS Code,
the Linux kernel — are generated, validated artifacts, not hand-assembled ones. This page is the
mechanics; [examples/README.md](../examples/README.md) is the index of the results themselves, and
[docs/benchmarks.md](benchmarks.md) is the methodology and the numbers.

## Pipeline

```
examples/repositories.yaml
        │
        ▼
scripts/generate_examples.py --repo <id> | --all
        │
        ├─ 1. clone_scoped()      partial + sparse git clone, pinned ref      (network)
        ├─ 2. graph.build()       repo2graph's own parser + resolver         (no network)
        ├─ 3. export.dump_all()   nodes/edges/overview/manifest/stats/html   (no network)
        ├─ 4. run_queries()       real Index.retrieve() calls -> flows/      (no network)
        ├─ 5. validate_example()  JSON validity, id uniqueness, edge         (no network)
        │                         integrity, no leaked local paths
        └─ 6. write_readme()      examples/<id>/README.md, from real numbers
        │
        ▼
examples/<id>/{metadata.json, nodes.jsonl.gz, edges.jsonl.gz, overview.md,
               manifest.json, stats.json, graph.html, flows/*.json, README.md}
```

Steps 2-5 reuse repo2graph's own library functions (`repo2graph.graph.build`,
`repo2graph.export.dump_all`, `repo2graph.query.Index`) directly — the generator is a thin driver
around the same code path `repo2graph build`/`repo2graph query` run, not a separate reimplementation
(see [AGENTS.md](../AGENTS.md) and this project's general aversion to parallel implementations of
the same thing).

## Why a custom clone instead of `repo2graph github`

`repo2graph github owner/repo` (`repo2graph/fetch.py`) is the right tool for indexing a whole
repository you already know is a reasonable size — see [docs/cli.md](cli.md). It does a plain
`git clone`, optionally shallow. The example generator's requirements are different: four of the
five repositories are scoped to specific subtrees (see
[docs/limitations.md#extreme-scale](limitations.md#extreme-scale)), and downloading blobs for the
other few hundred thousand files just to discard them during `repo2graph`'s own discovery filter
would waste most of the clone's bandwidth and time. `clone_scoped()` in
`scripts/generate_examples.py` instead does a blobless partial clone (`--filter=blob:none`) plus
`git sparse-checkout` restricted to the paths the registry entry actually wants, so git itself never
fetches a blob outside the analyzed scope. Once checkout finishes, everything from `graph.build()`
onward is identical to what `repo2graph build`/`repo2graph github` would do against that same local
directory.

## What gets committed, and what does not

See [examples/ATTRIBUTIONS.md](../examples/ATTRIBUTIONS.md#why-chunksjsonl-is-not-committed) for
the full reasoning. Short version: structure and statistics are committed (no source text anywhere);
`chunks.jsonl` — the file that embeds each symbol's actual source — is generated transiently inside
the scratch workdir to answer the example queries, then discarded with the rest of the clone.

## Validation

`validate_example()` in `scripts/generate_examples.py` runs after every generation and fails the
whole run (raising `SystemExit`, non-zero exit) if any of the following hold:

- a line in `nodes.jsonl`/`edges.jsonl` is not valid JSON
- a node id is duplicated
- an edge's `src` or `dst` is not in the node id set (a dangling edge)
- `metadata.json`'s `nodes`/`edges` counts disagree with the actual file contents
- any committed file contains this run's scratch working-directory path or the real machine's home
  directory (a leaked local/temp path) — see the comment in `validate_example()` for why this check
  is scoped to the *actual* scratch path rather than generic substrings like `C:\` or `/home/`,
  which real source code (e.g. VS Code's own `path.ts`) can legitimately contain

## Adding a repository

1. Add an entry to [`examples/repositories.yaml`](../examples/repositories.yaml): `id`, `name`,
   `url`, `ref`, `clone_depth`, `git_history`, `scope` (`full` or `scoped`), `sparse_paths` /
   `include` / `exclude` / `max_files` if scoped, `language_focus`, `category`, a `why:` paragraph
   explaining the choice, and 3-5 architecture-relevant example queries (not "find foo" — see the
   existing entries for the expected specificity).
2. Run `python scripts/generate_examples.py --repo <id>`.
3. Check `examples/<id>/README.md` reads sensibly and the file-count/scope numbers match what you
   expected (a much lower file count than expected usually means a `sparse_paths`/`include` typo).
4. Commit `examples/<id>/` and the updated `benchmarks/results.json`.

No new Python code is needed for a well-behaved public GitHub repository — the framework reads the
registry generically (see `generate_one()` in `scripts/generate_examples.py`); there is
deliberately no per-repository branch of logic to extend.
