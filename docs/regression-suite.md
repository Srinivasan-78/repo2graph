# Synthetic regression suite

`benchmarks/corpus/` holds five small repositories written by this project, and
[`benchmarks/tasks.json`](../benchmarks/tasks.json) holds 25 questions about them.
[`scripts/benchmark_runner.py`](../scripts/benchmark_runner.py) runs the questions on every PR
([`.github/workflows/benchmark.yml`](../.github/workflows/benchmark.yml), Ubuntu and Windows) and
fails the build if repo2graph's hit rate drops below 80%.

**It is a regression gate, not a benchmark.** The corpus and the questions were written by the
same people who wrote the retriever, so a high score here shows that nothing broke, not that
repo2graph is good. An earlier version of this page presented its scores (100% vs ripgrep's 80%)
as a comparison. That was withdrawn for three reasons: the corpus was written with the tool in
mind, a task counted as correct when a file *path* appeared anywhere in the output, and the
"agent" baseline read the first 80 lines of files whose *names* matched the question. For how
repo2graph does on code it did not write, see the
**[retrieval benchmark on real repositories](retrieval-benchmark.md)**.

## What the corpus covers

| Directory | What it exercises |
|---|---|
| `ts_app/` | TypeScript layered service: server → routes → controller → service, JWT middleware |
| `python_backend/` | FastAPI-style router and service layer, multi-hop order and refund flows |
| `modular_monolith/` | Python domains that talk only through an event bus |
| `frontend_app/` | React/TSX component, hook and context hierarchy |
| `dynamic_patterns/` | The cases static analysis cannot resolve: `getattr` dispatch, string-keyed plugin registries, five unrelated `execute()` methods, `__init_subclass__` registration, barrel re-exports |

## Known failure cases it pins

These are kept in the suite on purpose, so a change that claims to fix one has to show it:

- **Reflection dispatch** (`dynamic_patterns/.../handlers/base_handler.py`):
  `getattr(self, f"on_{action}")` builds the target at runtime, so no `CALLS` edge is drawn.
  A true static-analysis limit; see [limitations.md](limitations.md).
- **String-keyed registries** (`dynamic_patterns/.../dispatcher.py`): `plugin.execute(payload)`
  fans out to all five `execute()` definitions at `confidence = 0.2`, flagged `ambiguous`.
- **Barrel re-exports** (`dynamic_patterns/.../barrel/index.py`): an import of the barrel resolves
  to the barrel file, not through it to `plugins/alpha.py`. Tracked in the
  [TypeScript/JavaScript RFC](rfcs/rfc-language-deep-support-typescript.md).

## Run it

```bash
python scripts/benchmark_runner.py            # all 25 tasks, summary table
python scripts/benchmark_runner.py --ci       # exit 1 if the hit rate drops below 80%
```
