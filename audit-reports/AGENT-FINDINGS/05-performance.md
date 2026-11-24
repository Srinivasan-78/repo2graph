# Agent 5 — Performance, Scalability & Resource Efficiency

Scope: `repo2graph/parse.py`, `parsepool.py`, `limits.py`, `graph.py`, `derived.py`,
`query.py`, `chunks.py`, `embed.py`, `rerank.py`, `store.py`, `export.py`, `viz.py`,
`integrity.py`, `cochange.py`, plus the existing perf-benchmark infrastructure.

Environment for all measurements in this report: Windows 11, Python 3.13.15, a single
developer laptop, nothing isolated, no repeated-trial averaging unless stated. Numbers
here are **not** comparable to `benchmarks/perf/baseline.json`, which was recorded on a
4-core Linux container — see caveats section.

## Summary

The project already has unusually good performance self-awareness: a dedicated
benchmark harness (`scripts/perf_bench.py`) with a committed baseline and CI gate, an
explicit 50,000-file case study (`benchmarks/perf/README.md`) that already identifies
"writing dominates" and "~33 KB/file resident", a supervised process-pool parser with
documented wall-clock and memory budgets (`parsepool.py`, `limits.py`), and a visualizer
that already caps itself (`viz.py` `MAX_NODES = 300`, `MAX_PAGE_BYTES`). Several things I
went looking for as likely problems (brute-force cosine scan over the whole corpus,
N+1 SQL in the sqlite store, unbounded co-change combinatorics) turned out, on reading,
to already be bounded or short-circuited correctly.

Three things I could confirm as real costs:

1. Every file is read from disk **three separate times** during a build (binary-sniff,
   parse, chunk-text) — confirmed via profiling, not just inspection.
2. CLI/MCP cold start pays roughly 120–180 ms of eager import cost before doing any
   work, part of it an `importlib.metadata.version()` fallback scan that runs on every
   invocation.
3. The uncommitted `store.py` diff that drops `SqliteGraphStore`'s connection lock has
   **no measurable performance motivation** — the lock costs ~140 ns against a ~2.7 µs
   query+decode, i.e. it is noise next to the actual per-call cost, while removing it
   reintroduces the thread-safety question Agent 3 is independently assessing on
   correctness grounds. From a pure performance standpoint this trade is not worth
   taking.

Windows build times for the perf-bench default set are far above the committed Linux
baseline (e.g. `many-small`: 17.4 s here vs 1.15 s in `baseline.json`) but profiling
traced this to `_io.open()` syscall latency on this machine (0.7 ms average per open,
likely antivirus/NTFS overhead on a `%TEMP%` path), not an algorithmic regression —
`--jobs 1` showed the same wall time as the default job count, ruling out process-pool
spawn overhead as the explanation.

## Existing benchmark infrastructure & published baselines

Three independent measurement surfaces exist, each answering a different question and
explicitly documented as not comparable to each other (`benchmarks/README.md`):

- `benchmarks/real/` — retrieval quality (tokens-to-answer vs grep), not performance.
- `benchmarks/results.json` + `benchmarks/methodology.md` — build-scale wall-clock
  numbers on five real public repositories (Django, Kubernetes, TensorFlow, VS Code,
  Linux), explicitly "not controlled" for hardware, and explicitly states **peak memory
  is not measured anywhere** in that surface.
- `benchmarks/perf/` — the actual performance regression gate, `scripts/perf_bench.py`
  plus `benchmarks/perf/baseline.json`, run in CI by `.github/workflows/perf.yml` on
  every PR touching `repo2graph/`. It builds six synthetic fixtures (`tiny`, `medium`,
  `many-small`, `deep-tree`, `call-heavy`, `git-history`) each isolating one cost, plus
  an opt-in `large` fixture (50,000 files, #88) documented in
  `benchmarks/perf/README.md`. The CI gate tolerates a 2x slowdown on
  `build_seconds`/divides by a per-run calibration loop, so it is explicitly robust to
  "this runner is slower," which is the right design for a shared CI fleet.

Published 50,000-file numbers (`benchmarks/perf/README.md`, 4-core/16GB Linux,
unverified by me — I did not reproduce this fixture; see below):
graph 252,801 nodes / 602,800 edges; full build 81.0 s wall, of which parse is only
3.4 s (4%) and writing is 46.9 s (>50%); peak RSS 1,639 MB (~33 KB/file); `--incremental`
rebuild is *slower* (98.2 s) because resolution+write still run in full; index load
28.4 s; MCP first call 45.0 s (dominated by that load), warm calls 393 ms.

I did not re-run the `large` fixture myself: 50,000 files synthesized and parsed on a
single Windows dev machine, inside the time budget for this audit, was not practical,
and the existing writeup already states its own methodology and caveats honestly
(hardware not controlled, no repeated trials). I treat those published numbers as
third-party-equivalent (written by this project, not independently verified by me) and
flag that explicitly rather than re-quoting them as my own.

## Measured results

All of mine: Windows 11, Python 3.13.15, single machine, no isolation. `PYTHONPATH` had
to be set explicitly to `E:\Github\repo2graph` to run anything here — the environment's
pip-editable install for `repo2graph` points at a now-deleted directory
(`C:\Users\Srini\AppData\Local\Temp\merge-test2`), so `python -m repo2graph.cli` and
`scripts/perf_bench.py` fail outright with `ModuleNotFoundError` unless the real source
tree is put on the path first. This is an environment-setup defect, not a repo2graph
performance finding, but it means **CI-recorded or "it works for me" claims about this
install should not be trusted without checking `pip show -f repo2graph`** for a similar
dangling editable pointer.

| Command | Repeats | Result |
|---|---|---|
| `python scripts/perf_bench.py` (default 6 fixtures, PYTHONPATH fixed) | 1 full pass | see table below |
| `python -m repo2graph.cli --help` | 3 | 222 ms, 217 ms, 285 ms wall (`time`) |
| `python -c "import repo2graph"` | 4 | 121.2, 122.5, 128.5, 116.5 ms |
| `python -c "from repo2graph.mcp import server"` | 1 | 177.7 ms (cumulative, includes the above) |
| `python -X importtime -m repo2graph.cli --help` | 1 | see breakdown below |
| cProfile of `many-small` build, `--jobs 1` | 1 | 14.27 s total, see breakdown below |

Default perf-bench fixtures, this machine, one pass (no repeats — see caveats):

| fixture | files | build (s), this machine | build (s), committed Linux baseline | peak RSS |
|---|---:|---:|---:|---:|
| tiny | 10 | 0.407–0.469 | 0.234 | null (Windows RSS probe returned nothing) |
| medium | 500 | 3.751 | 1.048 | null |
| many-small | 3,000 | 17.453 | 1.151 | null |
| deep-tree | 300 | 2.305 | 0.614 | null |
| call-heavy | 400 | 4.313 | 1.407 | null |
| git-history | 120 | 1.920 | 0.490 | null |

`peak_rss_mb` came back `null` for every fixture on this machine — `scripts/perf_bench.py`
reads it from the build's own report (`performance.peak_rss_mb`), and the RSS probe that
feeds it apparently does not resolve on this Windows setup even though `parsepool.py` has
a dedicated `_win32_rss_bytes()` path using `GetProcessMemoryInfo`. I did not chase this
further (out of scope: it is the build's own self-report, not something I'm asked to
re-implement), but it means **this machine cannot currently validate the memory side of
the CI gate at all**, only the timing side.

Import-time breakdown (`-X importtime`, cumulative µs, top contributors under the
`repo2graph` package):

```
repo2graph                        120,513 µs cumulative
  repo2graph.graph                 71,224 µs
    repo2graph.derived             47,227 µs
      repo2graph.parse             46,533 µs (6,290 µs self)
        repo2graph.parsepool       30,984 µs
  importlib.metadata                40,643 µs  <- triggered by eager __version__ resolution
    zipfile                        10,383 µs
  repo2graph.cochange               15,605 µs
  repo2graph.embed                   7,720 µs
  repo2graph.export                  7,307 µs
```

cProfile of the `many-small` build (3,000 one-function files, `--jobs 1` to remove
process-pool effects), 14.27 s total, top cumulative:

```
4,376,229 function calls in 14.270 s
graph.py:1380 build                       12.567 s cum
  discovery.py:237 discover (x3001)       11.203 s cum
    {built-in method _io.open} (x15,043)  10.670 s tottime  <- the actual cost
export.py:1568 dump_all                    1.610 s cum
  chunks.py:201 iter_chunks                1.043 s cum
    chunks.py:226 source_of (x6000)          0.740 s cum   <- second full read per file
graph.py:1001 parse_all                     0.753 s cum
  graph.py:782 _read_and_parse (x3000)       0.623 s cum   <- first full read per file
```

15,043 calls to the builtin `open()` for 3,000 files is ~5 opens/file: one
`discovery.is_binary` peek-read, one `_read_and_parse` full read for parsing, one
`chunks.source_of` full re-read for chunk text, plus pathlib stat/lstat calls counted
separately. `tottime` (time inside `open()` itself, not its callees) of 10.67 s across
those 15,043 calls is ~0.71 ms/call average — far above what `open()` costs on a warm
Linux page cache. I attribute this to this specific machine (antivirus real-time
scanning of a `%TEMP%` working directory, or a slow underlying volume), not to
repo2graph's own CPU work, because:

- `--jobs 1` and the default job count produced the same wall time, ruling out
  process-pool spawn overhead as the cause.
- The actual decode/parse/graph-assembly CPU time (`parse_source`, `_process_chunk_content`,
  `scan_content_secrets`) sums to well under 1 s.

This is exactly the kind of number `benchmarks/methodology.md` already warns about
("do not use `build_seconds` here for capacity planning on different hardware") — I'm
reproducing that same caveat from direct measurement rather than contradicting it.

## Confirmed performance findings

### PERF-01 — Every indexed file is read from disk up to three times during a build
- **Severity**: Medium (I/O-bound workloads, many-small-file repos, slow/networked
  filesystems, CI runners with antivirus or network-mounted workspaces)
- **Confidence**: Confirmed-measured
- **Location**: `repo2graph/discovery.py:161` (`is_binary`, 4 KB peek read),
  `repo2graph/graph.py:715` (`_safe_read_bytes`, full read for parsing),
  `repo2graph/chunks.py:226-239` (`source_of`, full re-read for chunk text)
- **Description**: `is_binary()` opens and reads the first 4096 bytes of every
  candidate file during discovery. Later, `_read_and_parse` opens and fully reads the
  same file to parse it. Later still, at write time, `chunks.iter_chunks`'s inner
  `source_of()` opens and fully reads the *same file a third time* to produce chunk
  text — the bytes already read for parsing are not retained; only the parsed
  structure (`ChunkedParsedFile`) survives past the parse step.
- **Measured evidence**: cProfile of the `many-small` fixture (3,000 files) shows
  15,043 calls to `_io.open()`, ~5 per file, with `discover()`'s is_binary calls and
  `chunks.source_of`'s re-reads showing as distinct call sites in the profile
  (`discovery.py:237` cumulative 11.2 s of the 14.27 s total; `chunks.py:226 source_of`
  a further 0.74 s on top of the 0.62 s already spent reading the same files in
  `graph.py:782 _read_and_parse`).
- **Expected impact at scale**: On a filesystem where `open()`/`read()` latency is
  non-trivial (network filesystem, antivirus-scanned volume, cold page cache, or simply
  a very large file count), total build I/O time scales with `3 × file_count`, not
  `file_count`. On a warm Linux page cache (the committed baseline's environment) this
  is likely cheap enough to be invisible, which is consistent with the baseline
  claiming "parsing is 4% of the wall clock" while never separately costing out file
  I/O. It would not be invisible on a slow volume at 100k+ files.
- **Recommendation**: Retain the decoded source string (or raw bytes) from the parse
  step through to chunk emission instead of re-reading from disk in `source_of`.  The
  `is_binary` peek and the full read could also be merged (read the file once; use the
  same buffer for the binary sniff and, if the file turns out small enough, the parse).
- **Effort**: Medium — the parse pipeline is split across a process pool and a
  single-process write phase, so plumbing decoded text through means either keeping it
  in the `ParsedFile`/pool result (memory cost: comparable to the chunk text the build
  already materializes as `chunks.jsonl`, so no net new peak if done carefully) or
  caching per-file text behind a bounded LRU keyed by path.

### PERF-02 — Eager version resolution adds measurable fixed cost to every cold start
- **Severity**: Low-Medium (matters specifically for MCP server / editor-extension
  latency, where a cold process is spawned per session or per workspace)
- **Confidence**: Confirmed-measured
- **Location**: `repo2graph/__init__.py:73-99` (`_resolve_version`, called
  unconditionally at module import: `__version__ = _resolve_version()`)
- **Description**: Importing `repo2graph` at all calls `_resolve_version()`, which
  falls back to `importlib.metadata.version("repo2graph")` whenever the source-checkout
  heuristic (reading `pyproject.toml` nearby) doesn't apply — which is exactly the case
  for an installed package (editable or not) rather than a direct `git checkout` run.
  `importlib.metadata` has to scan installed distribution metadata across `sys.path`.
- **Measured evidence**: `-X importtime` attributes 40,643 µs cumulative to
  `importlib.metadata` inside the `repo2graph` import tree — about a third of the
  120 ms total import cost (confirmed separately: `import repo2graph` measured
  116.5–128.5 ms across 4 runs). CLI `--help` wall time is 217–285 ms; roughly half of
  that is import, not argument parsing or work.
- **Expected impact at scale**: Flat per-process-start tax, independent of repo size.
  For a one-shot CLI invocation (`repo2graph build`) this is noise. For an MCP server
  that an editor extension spawns fresh per workspace-open, or a CLI wrapper invoked
  per-file by some other tool, ~120-180 ms of pure import overhead before any
  repository work starts is the kind of thing users perceive as "slow to start."
- **Recommendation**: Nothing wrong with the correctness reasoning in the docstring
  (source checkout should not report a stale installed version) — but the expensive
  fallback could be made lazy (resolve on first access to `__version__`, e.g. via
  `__getattr__` module-level or caching the installed-metadata lookup) rather than
  running unconditionally at import time for every caller, most of whom never read
  `__version__` at all.
- **Effort**: Low.

### PERF-03 — `many-small` and other fixtures are far slower on this machine than the committed baseline, but not because of an algorithmic regression
- **Severity**: Informational (measurement-environment finding, not a code defect)
- **Confidence**: Confirmed-measured, with a plausible-but-unverified root cause
- **Location**: n/a (environment), observed through `repo2graph/discovery.py` and
  `repo2graph/graph.py` I/O paths
- **Description**: See "Measured results" above. `many-small` (3,000 files) takes
  17.4 s here vs. 1.15 s on the committed 4-core Linux baseline — a ~15x gap, well
  outside what `TOLERANCE["build_seconds"] = 2.0` in `perf_bench.py` would consider
  acceptable machine variance. Switching to `--jobs 1` reproduced the same order of
  magnitude (14.27 s), which rules out process-pool spawn cost (Windows `spawn`, not
  POSIX `forkserver`) as the explanation, and points instead at raw per-`open()` latency
  on this machine/filesystem.
- **Recommendation**: None for the codebase. Worth noting in `benchmarks/perf/README.md`
  or `methodology.md` (which already carries an equivalent caveat for `results.json`)
  that the perf-bench *default set* itself can show large, non-regression deltas on
  Windows/antivirus-scanned/non-local-disk setups, so a contributor running
  `scripts/perf_bench.py --check` locally on such a machine should not trust a failure
  there as a real regression without comparing against CI.
- **Effort**: n/a (documentation only, and only if the maintainers want it).

### PERF-04 — The uncommitted `store.py` lock removal has no performance justification
- **Severity**: N/A as a performance finding (this validates/refutes a motivation, it
  is not itself a new performance bug) — flagged because the task asked me to assess
  whether the correctness trade-off Agent 3 is reviewing has a performance upside.
- **Confidence**: Confirmed-measured
- **Location**: `repo2graph/store.py` (uncommitted diff), every `SqliteGraphStore`
  method that used to do `with self._lock: self._db.execute(...)`
- **Description**: The working-tree diff removes `threading.Lock` entirely from
  `SqliteGraphStore`, which holds one `sqlite3.Connection` opened with
  `check_same_thread=False` and is documented in the module's own docstring as reached
  from `query`/`rag --store sqlite` and by `tests/test_store.py`. Nothing in the diff,
  commit history, or code comments states a performance rationale.
- **Measured evidence**: microbenchmark on this machine, 200,000 iterations each:
  `with threading.Lock(): pass` costs ~141 ns/call (uncontended); one
  `SELECT ... WHERE id = ?` + `fetchone()` + `json.loads()` against an in-memory sqlite
  table with one small row costs ~2,698 ns/call. The lock is ~5% of one call's own cost
  in the *uncontended* case, and the real per-call cost in production is higher still
  (a `node_id`-indexed lookup against a file-backed, not in-memory, database, plus the
  WAL/page-cache path), making the lock's share proportionally smaller. There is no
  realistic access pattern in which removing this lock produces a measurable speedup.
- **Expected impact at scale**: None in the direction the diff moves (removing the lock
  does not help performance by any measurable amount); the actual effect is purely on
  the correctness axis Agent 3 is evaluating (safety of concurrent access to one
  `sqlite3.Connection` from multiple threads, e.g. inside the MCP server handling
  overlapping requests).
- **Recommendation**: From a pure performance standpoint, there is no case for taking
  this change. If a real contention problem is ever measured (it was not, by anyone,
  here), the fix would be per-thread connections or a connection pool, not deleting the
  serialization.
- **Effort**: N/A — recommendation is "don't," not a change to make.

## Theoretical bottlenecks (unmeasured)

### PERF-05 — `.npy` vector I/O writes/reads one row at a time
- **Severity**: Low (embeddings are opt-in, behind `rag`/`rag-static` extras not
  installed in this environment; I did not install anything to test this, per
  constraints)
- **Confidence**: Low — inspection only
- **Location**: `repo2graph/embed.py:84-93` (`_npy_write`, one `fh.write(struct.pack(...))`
  per row) and `:148-150` (`_npy_read`, one `struct.unpack` per row in a Python loop)
- **Description**: Without numpy as a hard dependency (deliberate, per the module
  docstring), vectors are packed/unpacked row-by-row in pure Python. For a corpus with
  hundreds of thousands of chunks this is hundreds of thousands of small `write()`
  syscalls (write) or Python-level loop iterations (read), versus one bulk
  `struct.pack(f"<{rows*cols}f", *flat)` / a single `array.array` fill.
- **Expected impact at scale**: Likely dwarfed by the embedding model's own forward-pass
  cost (sentence-transformers inference is far slower than this I/O), so this is
  probably not the bottleneck it looks like in isolation — flagging as theoretical
  precisely because I did not measure it and suspect it is not dominant.
- **Recommendation**: If ever profiled and found to matter, switch to `array.array("f", flat_list).tobytes()` for the write and bulk-unpack via `array.array` or
  `struct.iter_unpack` for the read.
- **Effort**: Low, if pursued.

### PERF-06 — `Index.nodes` / `.edges` / `.adj` silently materialize the whole graph even under `--store sqlite`
- **Severity**: Low (currently appears unreachable from the CLI's `--store sqlite` path;
  flagged as a latent design trap)
- **Confidence**: Medium — confirmed by reading the code, but I could not confirm any
  currently-reachable call path actually triggers it with a sqlite-backed `Index`
- **Location**: `repo2graph/query.py:686-701` (`Index.nodes`, `.edges`, `.adj`
  properties and `_memory()`); consumers such as `repo2graph/graphdiff.py:455,479,629-630`
  that read `index.nodes` / `index.edges` directly
- **Description**: `store.py`'s own docstring promises `SqliteGraphStore` "answers every
  call from disk" for "graphs too big to keep resident." But `Index._memory()`
  (`query.py:698-701`) says: if the current store isn't already a `MemoryGraphStore`,
  build one by calling `self.store.iter_nodes()` / `iter_edges()` and materializing
  everything — exactly the thing the sqlite backend exists to avoid. Any code that
  accesses `idx.nodes`, `idx.edges`, or `idx.adj` instead of going through
  `self.store.node()` / `.neighbours()` defeats the sqlite backend silently and without
  a warning. `graphdiff.py` does exactly this (`index.nodes.values()`, `index.edges`),
  but I traced its only current callers (`cli_inspect.py`'s diff command,
  `changelog.py`) and neither appears to plumb `--store` through to them — they
  construct their own `Index`/read JSONL directly rather than going through
  `_open_index` (`cli.py:1007`), the only place `--store` is wired up (used by
  `cmd_query` and `cmd_rag` only). So this looks latent rather than live today.
- **Expected impact at scale**: If `--store sqlite` is ever extended to a new command
  (or a new feature reuses `graphdiff` against a sqlite-backed `Index`), the "too big
  to keep resident" promise breaks with no error, just a silent full-memory load —
  exactly the failure mode the sqlite backend was built to prevent.
- **Recommendation**: Either make `.nodes`/`.edges`/`.adj` raise or warn when the
  backing store isn't already a `MemoryGraphStore` (fail loud instead of silently
  degrading), or audit every consumer before wiring `--store` into a new command.
- **Effort**: Low to flag/guard; Medium to audit all consumers if the maintainers want
  the promise to hold everywhere.

### PERF-07 — BM25 index is rebuilt from scratch in Python on every load that lacks a fresh on-disk cache
- **Severity**: Low-Medium (inspection only)
- **Confidence**: Low — inspection only, not measured independently of what's already
  in `index_load_ms` in the baseline
- **Location**: `repo2graph/query.py:703-722` (`_build_bm25`)
- **Description**: `_build_bm25` iterates every chunk, tokenizes text + qualname,
  updates a `Counter`-of-`Counter`s, and builds inverted postings lists, all in pure
  Python, whenever `_load_bm25()` (the precomputed-cache path) doesn't hit. This is
  `O(total chunk text size)` with per-token Python object overhead (dict/Counter
  updates), which the published baseline's `index_load_ms` figures (e.g. 253 ms for
  `call-heavy`'s 5,241 nodes) are consistent with being dominated by, though I did not
  isolate BM25-build time specifically from the rest of index load.
- **Expected impact at scale**: Likely the dominant cost in "index load" at very large
  chunk counts whenever the `bm25.jsonl` cache is stale or absent (e.g., first query
  after a build without `repo2graph query` itself writing the cache). The 50k-file case
  study's 28.4 s index-load figure is consistent with this being non-trivial at that
  scale, though the writeup doesn't attribute that number to BM25 specifically either.
- **Recommendation**: If profiling at real scale shows this dominating, consider
  precomputing and persisting the BM25 cache as part of every build rather than only on
  first query (if not already the case — I did not verify whether `write_bm25` already
  runs during `build`; `query.py:553` suggests it's a available function but I did not
  trace every caller).
- **Effort**: Unknown without measuring.

## Scalability limits

- **Explicit per-file budgets exist and are documented with calibration data**:
  `parsepool.py` enforces `PARSE_TIMEOUT_S = 10.0` (wall clock) and
  `WORKER_MEMORY_MB = 2048` per worker, both enforced by killing the worker process
  (necessary because tree-sitter parses can't be interrupted in-process — documented
  rationale with a concrete pathological case: `x = a<b<a<b<...;` as C++ is quadratic,
  300 KB input OOM'd at 13.8 GB). Calibrated against 18,608 real files from the
  benchmark corpora; defaults leave >10x headroom on both bounds per that calibration.
  I did not independently re-run that calibration.
- **Repo-wide limits** (`limits.py`, not fully read by me line-by-line but referenced
  throughout `discovery.py`/`graph.py`): `--max-files`, `--max-nodes`-style budgets are
  called out in `benchmarks/perf/README.md` ("`--max-files` and `--max-nodes` bound it")
  as the mechanism for capping the 50k-file memory ceiling (~33 KB/file resident); I did
  not verify every code path respects them equally (e.g., whether a build that exceeds
  `--max-nodes` mid-build stops cleanly vs. finishes the current file's symbols first
  — plausible given the "incomplete graph" warning machinery referenced in
  `export.py`, but not traced in depth).
- **Very large single files**: `INPROCESS_MAX_BYTES = 16 * 1024` in `parsepool.py`
  routes anything bigger to a supervised worker unconditionally; `graph.py` has a
  `_chunk_and_parse` / `chunk_large_files` path (seen in `discovery.py:388-393`) for
  files over `config.max_file_bytes`, so very large files are a documented and handled
  case, not a cliff.
- **Deep directory nesting**: the `deep-tree` perf-bench fixture (300 files, 30
  directories deep) is in the default CI-gated set, so this is actively regression-
  tested; I did not find anything suggesting `discover()`'s walk is non-linear in
  nesting depth beyond the ordinary cost of more path components (`rel.parts`,
  `_inside_index`'s prefix-walk is cached per-prefix via `index_dir_cache`, so that part
  is not re-walked per file).
- **Huge edge counts**: `MemoryGraphStore.__init__` builds `self.adj` as a
  `defaultdict(list)` by iterating all edges once — `O(E)`, no quadratic behavior found.
  `SqliteGraphStore.neighbours()` is a single indexed `UNION ALL` query
  (`edges_src`/`edges_dst` indices both exist in `_SCHEMA`), not N+1 — I specifically
  checked this because N+1 SQL was named as a thing to look for, and did not find it
  anywhere in `store.py`.
- **In-memory graph vs. sqlite store ceiling**: the 50k-file case study's own number
  (~33 KB/file, 1,639 MB peak RSS for 50k files) is the best available ceiling estimate
  for the default `MemoryGraphStore` path; `SqliteGraphStore` exists specifically to
  remove that ceiling but (PERF-06) has at least one latent path that would silently
  reintroduce it if ever wired into a new command.
- **Co-change / git-history walk**: already well-bounded by design —
  `MAX_COCHANGE_COMMITS = 5000`, `MAX_COCHANGE_BYTES = 10 MB` streamed (not materialized
  then truncated), and commits touching >25 files are skipped before the
  `itertools.combinations` pairing step, capping worst-case pairs per commit at
  `C(25,2)=300`. I read this file closely looking for the classic "co-change matrix"
  quadratic blowup and did not find one — flagging as a confirmed-safe area, not a
  finding.
- **Viz HTML**: already self-limiting — `MAX_NODES = 300`, `MAX_PAGE_BYTES = 12_000_000`,
  with an explicit shrink loop (`write_html`, `viz.py:413`) that reduces the node cap
  until the serialized page fits. Not a risk of "giant HTML" or "whole graph in memory"
  in the way the task description worried about.

## Limitations & measurement caveats

- **Single machine, single OS, no repeated trials for most numbers.** Where I did
  repeat (CLI import timing, `--help` wall time), variance was modest (≤15%); where I
  did not (the perf-bench fixture table), treat the numbers as one sample, not a
  distribution.
- **This machine's absolute numbers are not meaningful for capacity planning** — see
  PERF-03. The *relative* profiling breakdown within one run (e.g., "discover's `open()`
  calls dominate many-small's wall time, not CPU work") is the trustworthy part of that
  measurement, not the absolute 17.4 s.
- **I did not run the 50,000-file `large` fixture myself.** Reproducing it and
  profiling it properly would have taken the bulk of the time budget for this audit on
  file generation and I/O alone, on a machine already shown (PERF-03) to have unusual
  `open()` latency; I relied on the project's own published writeup for that scale point
  and said so explicitly rather than re-presenting it as independently verified.
- **Peak RSS could not be measured on this machine** at all (see "Measured results") —
  I have no independent memory data point beyond what's already published in
  `benchmarks/perf/README.md`.
- **I did not install `sentence-transformers`, `numpy`, or any RAG extras**, per the
  task's "do not install anything" constraint, so `embed.py`'s actual embedding-path
  cost (as opposed to the pure-Python NPY I/O, PERF-05) is unmeasured by me.
- **The environment's broken editable install** (pointing at a deleted
  `merge-test2` directory) had to be worked around with an explicit `PYTHONPATH` for
  every measurement in this report. I flag this because it means any other agent or CI
  step in this same environment that runs `python -m repo2graph...` or
  `scripts/perf_bench.py` without the same workaround will get a hard
  `ModuleNotFoundError`, not a performance number — worth checking before trusting any
  other agent's perf claims gathered in this same session/environment.
- I did not run the project's pytest suite (per the task constraints) and did not
  independently verify `tests/test_perf_bench.py`'s own assertions beyond reading them
  as documentation of intent.
