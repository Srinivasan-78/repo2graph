# Performance

Measured with `scripts/perf_bench.py`, which builds synthetic repositories
shaped to stress one cost each and reports wall time, write time, peak RSS,
output size, an unchanged `--incremental` rebuild, index load, query latency
and MCP cold/warm call latency. CI runs the default set on every pull request
that touches `repo2graph/` and fails on a regression past the tolerance
against `benchmarks/perf/baseline.json` (`.github/workflows/perf.yml`).

    python scripts/perf_bench.py                 # default set, JSON on stdout
    python scripts/perf_bench.py --check         # the CI gate
    python scripts/perf_bench.py --only large    # the 50,000-file run below

## Default set

The committed baseline, on a 4-core Linux container. Times scale with the
machine; the gate divides by a calibration loop recorded with the baseline,
so a slower runner raises the limit instead of failing.

| fixture | files | build (s) | peak RSS (MB) | output (MB) | query (ms) |
|---|---:|---:|---:|---:|---:|
| tiny | 10 | 0.23 | 27 | 0.1 | 1.4 |
| medium | 500 | 1.05 | 47 | 7.1 | 5.1 |
| many-small | 3,000 | 1.15 | 88 | 7.6 | 1.2 |
| deep-tree | 300 | 0.61 | 90 | 5.1 | 2.4 |

See `benchmarks/perf/baseline.json` for every metric and fixture.

## 50,000 files (#88)

`perf_bench.py --only large`: 50,000 Python files across 2,600 directories,
each with a class, a method and two functions. 4 cores, 16 GB.

| metric | value |
|---|---:|
| graph | 252,801 nodes, 602,800 edges |
| full build, wall | 81.0 s |
| — parse (4 workers) | 3.4 s |
| — discovery, call resolution, graph assembly | 26.6 s |
| — write (chunk text + every artifact) | 46.9 s |
| peak RSS | 1,639 MB |
| output | 696 MB (`chunks.jsonl` text is 4.6x the source) |
| unchanged `--incremental` rebuild | 98.2 s |
| index load | 28.4 s |
| query, warm | 431 ms |
| MCP first call (load + query) | 45.0 s |
| MCP call, warm | 393 ms |

What it says, in order of cost:

- **Writing dominates.** More than half the build is serialising chunks and
  graph artifacts, not parsing. Parsing is 4% of the wall clock.
- **`--incremental` saves nothing at this size.** The parse cache skips the
  3.4 s of parsing; resolution and writing run in full either way, and
  loading and checking the cache costs more than it saves. Use it for its
  correctness guarantee (identical output), not for speed.
- **The MCP server's first call pays for the index load.** 28 s here; every
  call after it is under half a second. Start the server before it is needed
  for a repository this size.
- **Memory is about 33 KB per file**, almost all of it the in-memory graph.
  Budget 2 GB for a 50,000-file build; `--max-files` and `--max-nodes` bound
  it ([docs/cli.md](../../docs/cli.md)).
