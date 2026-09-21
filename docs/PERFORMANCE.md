<!--
  @authormark v1 -- do not remove (authorship watermark)⁠​​‌‌‌​​​​​‌‌​‌​‌​‌​​​‌‌‌​‌​​​‌​‌​‌‌‌​‌‌​​​‌‌​​​​​​‌‌​​‌‌​‌‌​​‌​‌​‌‌​‌​‌​​‌​‌‌​​​​‌​​‌​‌​​‌​​​‌‌‌​‌‌​​‌‌​​‌‌​​​‌​​‌​​​‌​​​‌​‌​​‌‌​‌‌‌‌​​‌​‌‌‌​‌​‌​‌‌‌​​‌‌​‌‌‌‌​‌​​‌‌​​‌‌​​‌​​​‌‌​⁠
  Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
  Author: https://github.com/Srinivasan-78
  SPDX-License-Identifier: MIT
  Fingerprint: AMK1.85GEv03ejXJGfbDSyuszfF
-->
# Performance

Measured, not estimated. Every number below came from actually running `repo2graph build`,
`query`, and `rag` on this machine (Windows, Python 3.13.15) against real inputs, on
2026-09-17. Re-run the commands under each table yourself before relying on these numbers for
capacity planning on different hardware.

## What was measured

| Repo | Files | Discovery | Wall time (full build) | Nodes | Edges | Chunks |
|---|---:|---|---:|---:|---:|---:|
| repo2graph itself | 90 (39 parsed as Python; rest non-code) | `git`, `--git-history 200` | 4.3s | 1,472 | 6,350 | 1,653 |
| Synthetic (generated) | 3,000 Python files | `os.walk` (no git) | 18.6s | 18,033 | 33,030 | 15,000 |

**How the synthetic repo was built:** 3,000 small, uniform Python files (one class with two
methods, one module-level function, two imports, one constant, spread across 30 flat package
directories) — see the generation script referenced in this audit's change log. This is a
worst-case-for-node-count, best-case-for-parse-time shape: every file parses cleanly on the first
try, so the number isolates pipeline throughput from parser-error handling cost. A real 3,000-file
repository with a more varied file-size distribution will likely build faster (few files near the
1.5MB cap) or slower (more non-trivial cross-file `CALLS` ambiguity) depending on its shape.

### Incremental rebuild

| Change | `cached` | `reparsed` | Wall time |
|---|---:|---:|---:|
| One file edited (of 3,000) | 2,999 | 1 | 6.0s |
| No changes (no-op rebuild) | 3,000 | 0 | 6.1s |
| *(for comparison)* full rebuild | — | 3,000 | 18.6s |

Incremental rebuild on this fixture is ~3x faster than a full rebuild, but not free: every file is
still *read* to compute its content hash (`docs/BACKLOG.md` explains why — there is no cheaper way
to know a file is unchanged), and the entire resolution phase (global name index, `CALLS`
confidences, `INHERITS`, entrypoints, reach) is recomputed from the cached per-file symbol tables on
every build, by design (see `docs/BACKLOG.md`, "Incremental rebuild, as shipped" — this is what
makes the incremental output byte-identical to a full rebuild rather than merely close). On this
3,000-file fixture, reading+hashing dominates the incremental-rebuild wall time; resolution itself
is the "negligible beside parsing" cost the design doc predicted.

### Query / retrieval latency

Measured against the 3,000-file / 18,033-node / 15,000-chunk index above, BM25-only (no `--vectors`):

| Command | Wall time |
|---|---:|
| `repo2graph query "<question>"` | 1.19s |
| `repo2graph rag "<question>"` | 0.84s |

Both include process startup (interpreter init, `tree-sitter-language-pack` import) — the actual
`Index.pack_context()` call is a small fraction of that, but this table reports what a caller
actually experiences from a cold CLI invocation, which is the number that matters for an MCP
server's first-call latency before its index is warm in memory.

## What was not measured, and why

The brief asked for 10k/50k/100k/500k-file and 1M+-LOC benchmarks. Those were not run in this
pass:

- **Time budget.** A 100k-file synthetic run at the ~6ms/file rate observed at 3,000 files
  extrapolates to roughly 10 minutes of wall time for one data point, which this audit's time box
  did not accommodate alongside the security findings above. Extrapolating throughput linearly from
  a single 3,000-file measurement is not something to plan capacity around — see the next bullet.
- **Linear scaling is an assumption, not a finding.** `graph.py` switches to a `ProcessPoolExecutor`
  above `PARALLEL_MIN_FILES` (64 files) — the 3,000-file run above exercised that path — but the
  *resolution* phase (global name index lookups, `CALLS` disambiguation) is not obviously linear in
  file count; a repository with many files sharing common function names produces more ambiguous
  `CALLS` candidates per call site, which costs more than a repository of the same size with unique
  names. Nothing in this audit measured that effect at scale.
- **Memory was not profiled.** No `tracemalloc`/RSS-ceiling measurement was taken at any repo size.
  Given `Graph.nodes`/`edges` are plain in-memory Python dicts/lists (see
  `docs/SECURITY-AUDIT.md`, P3.5), memory scales with node/edge count with no built-in ceiling —
  what that means in absolute megabytes at 100k+ files is unmeasured.

**Do not claim, from this document, that repo2graph is validated at 50k+ files.** It has not been
run at that scale by this audit. The BACKLOG item "a fixture above `PARALLEL_MIN_FILES`" (already
tracked in `docs/BACKLOG.md` before this audit) is exactly this gap from the test-coverage side —
no test in the suite exercises more than the 3,000-file fixture generated for this benchmark either.

## Recommendations for someone about to run this at scale

1. Run `repo2graph build --incremental` in CI rather than a full rebuild on every push once the
   `parse.cache.json` artifact can be preserved between runs — the ~3x incremental speedup measured
   above should compound further on a repository where most files don't change between commits.
2. If indexing a monorepo north of a few thousand files, measure your own first data point with
   `time repo2graph build <path> -o .r2g` before assuming the numbers above transfer — file-size
   distribution and cross-file name collision rate both plausibly matter more than raw file count.
3. The MCP server's `--async-build` exists specifically so a large first-call build doesn't block
   an agent's tool-call timeout; see `docs/mcp.md`.

## Reproducing these numbers

```bash
# Against this repo itself:
time repo2graph build . -o /tmp/bench --git-history 200

# Synthetic fixture (adjust N):
python - <<'PY'
import os
N = 3000
root = "/tmp/bench_synth"
for i in range(N):
    d = os.path.join(root, f"pkg{i//100}")
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, f"mod{i}.py"), "w").write(f"""
class Service{i}:
    def __init__(self): self.value = {i}
    def run(self): return self.helper()
    def helper(self): return {i} * 2

def entry_{i}():
    return Service{i}().run()
""")
PY
time repo2graph build /tmp/bench_synth -o /tmp/bench_synth_out
time repo2graph build /tmp/bench_synth -o /tmp/bench_synth_out --incremental   # no-op
touch /tmp/bench_synth/pkg0/mod5.py
time repo2graph build /tmp/bench_synth -o /tmp/bench_synth_out --incremental   # one file changed
time repo2graph query "how does Service run" -o /tmp/bench_synth_out
time repo2graph rag "how does Service run" -o /tmp/bench_synth_out
```
