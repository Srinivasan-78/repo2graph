# Synthetic regression suite

`benchmarks/corpus/` holds five small repositories written by this project, and
[`../tasks.json`](../tasks.json) holds 25 questions about them — five per repository.
[`../../scripts/benchmark_runner.py`](../../scripts/benchmark_runner.py) runs the questions on
every PR ([`benchmark.yml`](../../.github/workflows/benchmark.yml), Ubuntu and Windows) and fails
the build if repo2graph's hit rate drops below 80%.

**It is a regression gate, not a benchmark.** The corpus and the questions were written by the
same people who wrote the retriever, so a high score here shows that nothing broke, not that
repo2graph is good. An earlier version of this page presented its scores (100% vs ripgrep's 80%)
as a comparison. That was withdrawn for three reasons: the corpus was written with the tool in
mind, a task counted as correct when a file *path* appeared anywhere in the output, and the
"agent" baseline read the first 80 lines of files whose *names* matched the question. For how
repo2graph does on code it did not write, see the
**[retrieval benchmark on real repositories](../real/README.md)**.

## What the corpus covers

Five archetypes, chosen so that a regression in one language or one shape of codebase cannot hide
behind the others:

| Directory | What it exercises |
|---|---|
| `ts_app/` | Express-style TypeScript service: routes → controllers → services → models, with auth middleware and a crypto util |
| `python_backend/` | FastAPI-style layering: router → dependencies → service → model/schema, plus a payment collaborator |
| `modular_monolith/` | Cross-domain structure: billing, catalog, identity, shipping and notifications over a shared kernel with an event bus |
| `frontend_app/` | React/TSX component, hook and context hierarchy; service-to-page data flow |
| `dynamic_patterns/` | The cases static analysis cannot resolve: `getattr` dispatch, string-keyed plugin registries, five unrelated `execute()` methods, `__init_subclass__` registration, barrel re-exports |

`ts_app/`, `python_backend/` and `modular_monolith/` were removed in `4e96b628` along with their
15 tasks, and restored afterwards. `benchmark_runner.py` hard-fails on a task naming a repository
the corpus does not have, which is what makes the pairing safe: silently dropping those tasks used
to shrink the gate while still reporting the original count as a pass.

## Known failure cases it pins

These are kept in the suite on purpose, so a change that claims to fix one has to show it:

- **Reflection dispatch** (`dynamic_patterns/dynamic_repo/handlers/base_handler.py`):
  `getattr(self, f"on_{action}")` builds the target at runtime, so no `CALLS` edge is drawn.
  A true static-analysis limit — see "What it can't do" in [the README](../../README.md).
- **String-keyed registries** (`dynamic_patterns/dynamic_repo/dispatcher.py`):
  `plugin.execute(payload)` fans out to all five `execute()` definitions at
  `confidence = 0.2`, flagged `ambiguous`.
- **Barrel re-exports** (`dynamic_patterns/dynamic_repo/barrel/index.py`): an import of the
  barrel resolves to the barrel file, not through it to `plugins/alpha.py`.

## Run it

```bash
python scripts/benchmark_runner.py            # all 25 tasks, summary table
python scripts/benchmark_runner.py --ci       # exit 1 if the hit rate drops below 80%
```
