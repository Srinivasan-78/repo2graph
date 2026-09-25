# Benchmarks

How `repo2graph` performs at scale and in query evaluation:

1. **Large-Scale Public Repository Corpus (this page):** Build performance, indexing throughput, node/edge density, and parser error rates against five real, large, public repositories ([examples/](../examples/)).
2. **Reproducible Archetype Benchmark & Evaluation Suite ([BENCHMARK.md](../BENCHMARK.md)):** End-to-end repository-understanding evaluation across 25 tasks and 5 application archetypes (`benchmarks/corpus/`), measuring query correctness (100%), source-citation precision (97.9%), query latency (1.82ms), and context footprint against `ripgrep` and agent baseline search.
3. **Synthetic Throughput & Self-Hosting ([docs/PERFORMANCE.md](PERFORMANCE.md)):** Hardware-controlled parser throughput isolated from repository structure.

## Methodology

- **What ran:** `python scripts/generate_examples.py --all`, on 2026-09-22, via
  [`.github/workflows/examples.yml`](../.github/workflows/examples.yml) on a GitHub-hosted
  `ubuntu-latest` runner (Python 3.12). Every number below is read straight out of
  [`benchmarks/results.json`](../benchmarks/results.json) and each example's own `metadata.json` —
  nothing here is hand-typed or estimated.
- **Why CI rather than a workstation:** the run before this one was done on a Windows development
  machine, where `git checkout` refuses paths over the historical `MAX_PATH` limit (see
  [docs/limitations.md](limitations.md#windows-filename-length-limits-can-silently-shrink-a-checkout)).
  A Linux runner has no such limit, so the checkout the numbers describe is the complete one. It
  also makes the run reproducible by anyone with a fork, which a single workstation is not.
- **What "clone" measures:** wall-clock time for `git clone --filter=blob:none` (plus
  `sparse-checkout` for the four scoped repositories) through `git checkout` of the pinned commit —
  the network phase. See [examples/repositories.yaml](../examples/repositories.yaml) for each
  repository's exact clone parameters.
- **What "build" measures:** wall-clock time for `repo2graph.graph.build()` +
  `repo2graph.export.dump_all()` against the local checkout only — no network calls happen in this
  phase (see "Network behavior" below).
- **What is *not* measured here:** peak memory. This machine is Windows, where Python's stdlib
  `resource` module (`getrusage`) does not exist; adding a new dependency (`psutil`) for one metric
  in a benchmark script was judged not worth it. If you run the generator yourself on Linux/macOS
  and want peak RSS, wrap the `build()` call with `resource.getrusage(resource.RUSAGE_SELF)` — the
  hook point is `generate_one()` in `scripts/generate_examples.py`.
- **Hardware is not controlled for or claimed comparable.** These are single-machine, single-run
  numbers for one specific commit each. Re-running on different hardware, a different commit, or
  under different load will produce different numbers — that is expected, not a discrepancy to
  reconcile.

## Results

| Repository | Scope | Files indexed | Clone | Build | Nodes | Edges |
|---|---|---:|---:|---:|---:|---:|
| [Django](../examples/django/) | full | 5,629 | 4.6s | 33.9s | 55,810 | 303,339 |
| [Kubernetes](../examples/kubernetes/) | scoped | 1,084 | 2.8s | 12.6s | 14,451 | 110,246 |
| [TensorFlow](../examples/tensorflow/) | scoped | 1,022 | 2.6s | 15.8s | 21,380 | 115,984 |
| [VS Code](../examples/vscode/) | scoped, capped at 6,000 files | 6,000 | 8.3s | 71.3s | 113,080 | 656,158 |
| [Linux kernel](../examples/linux/) | scoped | 3,660 | 3.3s | 77.5s | 136,219 | 256,413 |

Files-per-second on the build phase alone ranges from ~47 (the Linux kernel — C, by far the highest
parse-error rate) through ~65–86 (TensorFlow, VS Code, Kubernetes) to ~166 (Django — Python, clean
parses, no macro expansion); see [docs/limitations.md](limitations.md) for why file count alone does
not predict build time (parse error rate and call-name ambiguity both matter more than raw file
count).

### What changed against the previous run

The corpus before this one was generated on 2026-09-17 with repo2graph 1.5.1, and node counts moved
very little between the two — but **edge counts rose sharply**: Django 228,461 → 303,339, VS Code
431,453 → 656,158, Kubernetes 83,525 → 110,246, TensorFlow 96,013 → 115,984. That is the scoped call
resolution shipped after 1.5.1 doing its job: call sites that previously found no in-repo candidate
and became a single `CALLS_EXTERNAL` edge now resolve through the same-class, same-file,
imported-symbol and same-module tiers into real `CALLS` edges. The Linux kernel is the exception
(257,655 → 256,413, essentially flat), which is what C with no method dispatch and prefix-disciplined
naming should look like.

Two caveats on comparing the two runs directly: each is pinned to a *different* upstream commit, and
this one ran on Linux CI rather than Windows. Neither difference is large enough to explain a 52%
edge increase on VS Code, but they mean these are two measurements, not a controlled A/B.

Full per-repository statistics — node/edge type breakdowns, parse error counts, ambiguous-call
rates — are in each example's `README.md` and `stats.json`; the cross-repository comparison is in
[docs/limitations.md](limitations.md#what-parsing-five-real-repositories-actually-showed).

## Network behavior

The clone phase needs network access; the analysis phase does not. This is structural, not just a
claim: `repo2graph/graph.py`, `repo2graph/parse.py` and `repo2graph/export.py` — everything
`build()` and `dump_all()` touch — import nothing from `socket`, `urllib`, `http` or any HTTP
client, and `scripts/generate_examples.py` clones into a scratch directory *before* calling
`build()`, never during or after. `repo2graph rag --answer` is the one command in this whole project
that makes an outbound network call during analysis, and it is opt-in and separately documented —
see [docs/cli.md](cli.md#answer-sends-your-code-elsewhere). None of the example
generation described here touches it.

## Security of benchmark execution

The generator treats every cloned repository as untrusted input: it runs `git clone` (blobless,
sparse where scoped), `git sparse-checkout`, and `git checkout` — nothing else. It never runs a
target repository's build scripts, test suite, package manager, or git hooks, and never initializes
submodules. See the module docstring and `clone_scoped()` in
[`scripts/generate_examples.py`](../scripts/generate_examples.py) for the exact operations.

## CI tiers

Regenerating all five large repositories on every pull request would be slow, bandwidth-heavy, and
non-deterministic against upstream's moving `main`/`master`. The CI tiers are split intentionally:

- **Pull request & Push (Continuous Verification):**
  - Unit and integration tests (`pytest`).
  - **Automated benchmark regression gate:** [`.github/workflows/benchmark.yml`](../.github/workflows/benchmark.yml) builds indexes for the 5 archetypes in `benchmarks/corpus/`, evaluates all 25 tasks via `python scripts/benchmark_runner.py --ci`, and enforces an accuracy gate (asserting `>= 80%` retrieval accuracy, zero regressions, on both Ubuntu and Windows).
- **Manual (`workflow_dispatch`):** `.github/workflows/examples.yml` regenerates one or all five
  large-scale real-world examples and re-runs `validate_example()` against the result, on demand — see that
  workflow file for the exact trigger. Not scheduled automatically, so it never runs (and never
  consumes CI minutes or bandwidth) without someone asking for it.
- **Release:** left to whoever cuts a release to decide whether to regenerate the corpus and commit
  fresh examples; this repository does not currently automate that decision.

## Staleness

Every artifact under `examples/` records the exact commit it was generated from, in that example's
`metadata.json` (`commit`) and `README.md` ("Revision"). None of the five upstream repositories
stand still — a Kubernetes, TensorFlow, VS Code or Linux checked out today will not be the commit
pinned here. Re-running `python scripts/generate_examples.py --repo <id>` gets whatever that
repository's `ref` (usually `main`/`master`) points to *right now*, which will not match the numbers
on this page, in that example's `README.md`, or in `benchmarks/results.json` as committed. That is
the intended behavior — these are point-in-time evidence of a specific run, not a live dashboard —
and is why every reproduction instruction in this project says "clone at the commit above," not
"clone `main`."
