# Synthetic regression suite

`benchmarks/corpus/` holds two small repositories written by this project, and
[`../tasks.json`](../tasks.json) holds 10 questions about them.
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

| Directory | What it exercises |
|---|---|
| `dynamic_patterns/` | The cases static analysis cannot resolve: `getattr` dispatch, string-keyed plugin registries, five unrelated `execute()` methods, `__init_subclass__` registration, barrel re-exports |
| `frontend_app/` | React/TSX component, hook and context hierarchy; service-to-page data flow |

Three further corpora (`ts_app/`, `python_backend/`, `modular_monolith/`) were removed in
`4e96b628`. Their 15 tasks were removed from `tasks.json` at the same time, because
`benchmark_runner.py` now hard-fails on a task naming a repository the corpus does not have —
silently dropping those tasks used to shrink the gate while still reporting the original count as
a pass.

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
python scripts/benchmark_runner.py            # all 10 tasks, summary table
python scripts/benchmark_runner.py --ci       # exit 1 if the hit rate drops below 80%
```
