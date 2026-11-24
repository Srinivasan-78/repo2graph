# Performance and Scalability Assessment

Consolidated from `AGENT-FINDINGS/05-performance.md`. Measured findings and
theoretical bottlenecks are kept strictly separate; no speedup or regression is
asserted that was not measured.

## Verdict

No severe performance defects. The project already has real benchmark
infrastructure with committed baselines, and the hot paths show evidence of
deliberate bounding (caps on co-change pairing, viz node counts, parse budgets).
The one measured inefficiency of substance is that **every file is read from disk
about three times per build**.

## Measurement caveat — read this before any number below

Absolute build times measured on this machine are **~15× the committed Linux
baseline** (many-small-files case: 17.4s here vs 1.15s baseline). This was
root-caused to raw `open()` latency on this Windows host — `--jobs 1` produced
the same ratio, ruling out a parallelism or algorithmic regression.

**Consequence:** a local `perf_bench.py --check` failure on this machine is not
evidence of a performance regression in the code. Absolute numbers here are not
comparable to CI baselines; only within-run comparisons are meaningful.

## Findings

| ID | Severity | Confidence | Location | Finding |
|---|---|---|---|---|
| PERF-01 | MEDIUM | **Confirmed-measured** | `discovery.py:161`, `graph.py:715`, `chunks.py:226` | Each file read ~3× per build: binary sniff, parse, chunk-text re-read |
| PERF-02 | LOW-MEDIUM | **Confirmed-measured** | `repo2graph/__init__.py:73-99` | Eager `importlib.metadata.version()` costs ~40ms of a ~120ms import tax |
| PERF-03 | INFO | **Confirmed-measured** | environment, not code | Windows build ~15× the Linux baseline; `open()` latency, not a regression |
| PERF-04 | — | **Confirmed-measured** | `store.py` (uncommitted diff) | Lock removal has **no** performance upside: ~141ns vs ~2,698ns per query (~5%) |
| PERF-05 | LOW | Low (inspection) | `embed.py:84-150` | Row-by-row NPY struct pack/unpack rather than bulk ops; likely dwarfed by inference cost |
| PERF-06 | LOW | Medium (inspection) | `query.py:686-701` | `Index.nodes/.edges/.adj` silently fully materialise a sqlite-backed graph |
| PERF-07 | LOW-MEDIUM | Low (inspection) | `query.py:703-722` | BM25 rebuild is pure-Python with per-token dict overhead on cache miss |

### PERF-01 — Triple file reads per build

**Measured by profiling: 15,043 `open()` calls for 3,000 files** — ~5 per file,
with ~3 being full content reads at three separate stages (binary sniffing in
discovery, parsing in graph construction, chunk-text extraction in chunking).

On a filesystem where `open()` is cheap this is mostly wasted syscalls; on one
where it is not — Windows, network mounts, container overlay filesystems — it
dominates. PERF-03's 15× gap is largely this cost amplified.

**Recommendation:** read each file once and pass the bytes through the pipeline.
The parse pool already moves data between processes, so the plumbing exists. This
is the single highest-value performance change available. Effort: **M**.

### PERF-04 — The removed lock had no performance justification

This finding exists to close off a specific question: was the `threading.Lock`
removed in the working tree for speed?

**Measured:** lock acquisition ~141ns against ~2,698ns for the query-plus-JSON-decode
it guards — roughly 5% overhead. There is no meaningful performance gain from
removing it, and the correctness cost is CRITICAL (`BUGS-AND-DEFECTS.md` BUG-01).
The trade-off has no counterweight. Restore the lock.

If lock contention ever does become material on a hot MCP path, the correct
answer is a per-thread connection via `threading.local()`, which removes
contention *without* reintroducing the race.

### PERF-02 — Cold-start import tax

~120–180ms fixed import cost per process, of which ~40ms is an eager
`importlib.metadata.version()` fallback in `repo2graph/__init__.py:73-99`.

This matters more than it looks: the MCP server and the VS Code extension pay it
on every cold start, and interactive responsiveness is the whole point of the
editor integration. Making the version lookup lazy recovers a third of it.
Effort: **S**.

### PERF-06 — A silent escape hatch in the "doesn't need to fit in memory" promise

`Index.nodes`, `.edges` and `.adj` fully materialise the graph, defeating the
purpose of `SqliteGraphStore`. The specialist found this currently unreachable
via CLI `--store sqlite`, so it is not a live defect — but it is a landmine the
moment `--store` is wired into more commands, and `store.py`'s own
`test_legacy_attributes_still_work_and_materialise_once` shows the materialisation
path is a supported legacy behaviour rather than an accident.

**Recommendation:** emit a warning (or require an explicit opt-in) when a
sqlite-backed `Index` materialises. Effort: **S**.

## Scalability

| Dimension | Assessment |
|---|---|
| Very large repos | `SqliteGraphStore` addresses the memory ceiling, subject to PERF-06 |
| Very large single files | Bounded by `limits.py` parse budgets |
| Huge edge counts | `viz.py` self-limits (300-node cap, 12MB page cap with a shrink loop) |
| Git history walking | Explicitly bounded: ≤25 files/commit, 5,000-commit cap, streamed byte cap |
| Parse worker runaway | Supervised with time and memory caps; `test_parse_budget.py` exercises hang and death paths |

Bounding is applied consistently and deliberately, which is a notable strength —
unbounded resource growth is the usual failure mode for tools in this category.

## Confirmed-efficient areas

Checked rather than assumed:

- `SqliteGraphStore.neighbours()` uses a single indexed `UNION ALL` query — **not**
  an N+1 pattern, which was specifically looked for.
- Dense-vector reranking scores only the BM25 shortlist, not the whole corpus.
- `cochange.py` commit pairing is bounded on three independent axes.
- `viz.py` enforces its own output ceiling with a shrink loop rather than
  emitting an unbounded page.

## Existing benchmark infrastructure

The project ships `benchmarks/` with a documented methodology, a committed
`perf/baseline.json`, a corpus of five synthetic repos, real-repo benchmarks
against 13 well-known projects at pinned commits, and CI gates
(`perf.yml`, `benchmark.yml`, plus two `Benchmark Regression Gate` required
checks). Retrieval-quality claims in README were independently recomputed from
the committed raw results and matched exactly (see `DOCUMENTATION-AUDIT.md`).

This is substantially more rigorous than typical, and it is the reason this
assessment could focus on finding *new* inefficiencies rather than establishing
baselines from scratch.

## Limitations

- Single Windows host; no Linux or macOS measurement. PERF-03 means absolute
  numbers do not transfer.
- Peak memory was not obtainable for all runs (`peak_rss_mb: null` in build
  output on this platform).
- PERF-05, PERF-06 and PERF-07 are **inspection-only** and unmeasured; they are
  rated LOW accordingly and should not be acted on without measurement first.
- Benchmarks ran against `benchmarks/corpus/` and synthetic scratchpad repos, not
  against the large real-world repositories in `benchmarks/real/repos.json`
  (which require cloning, i.e. network).
