# Benchmarks

How `repo2graph build` performs against five real, large, public repositories — not estimated, not
extrapolated. For synthetic-fixture and self-hosted numbers (isolating parser throughput from a
real repository's specific shape), see [docs/PERFORMANCE.md](PERFORMANCE.md); this page is about
the [examples/](../examples/) corpus specifically.

## Methodology

- **What ran:** `python scripts/generate_examples.py --all`, on this project's own development
  machine (Windows, Python 3.13.15), on 2026-09-17. Every number below is read straight out of
  [`benchmarks/results.json`](../benchmarks/results.json) and each example's own `metadata.json` —
  nothing here is hand-typed or estimated.
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
| [Django](../examples/django/) | full | 5,637 | 8.3s | 46.5s | 54,544 | 228,461 |
| [Kubernetes](../examples/kubernetes/) | scoped | 1,082 | 4.7s | 14.5s | 14,197 | 83,525 |
| [TensorFlow](../examples/tensorflow/) | scoped | 1,022 | 4.2s | 13.0s | 20,641 | 96,013 |
| [VS Code](../examples/vscode/) | scoped, capped at 6,000 files | 6,000 | 14.6s | 86.9s | 113,115 | 431,453 |
| [Linux kernel](../examples/linux/) | scoped | 3,660 | 8.0s | 62.2s | 136,182 | 257,655 |

Files-per-second on the build phase alone ranges from ~70 (VS Code — TypeScript, high `CALLS`
ambiguity, more edges per file) to ~120 (Django — Python, moderate ambiguity) to ~260
(Kubernetes/TensorFlow — Go and a scoped C++/Python slice) on this machine; see
[docs/limitations.md](limitations.md) for why file count alone does not predict build time (parse
error rate and call-name ambiguity both matter more than raw file count).

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

Regenerating all five repositories on every pull request would be slow, bandwidth-heavy, and
non-deterministic against upstream's moving `main`/`master` — the task this repository's CI exists
to avoid. Instead:

- **Pull request:** the existing unit/integration test suite (`pytest`), which exercises
  `graph.build()`, `export.dump_all()` and `query.Index` against the small fixtures already in
  `tests/`. No external clone happens on a PR.
- **Manual (`workflow_dispatch`):** `.github/workflows/examples.yml` regenerates one or all five
  real-world examples and re-runs `validate_example()` against the result, on demand — see that
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
