# RFC: incremental indexing — where the time actually goes

**Status:** proposal, with measurements
**Scope:** `repo2graph build --incremental`
**Date:** 2026-09-25

---

## Summary

`--incremental` already exists and its parse cache works: it removes **96–97%
of parsing cost** on a rebuild. But end-to-end it only buys **1.4×**, and it
buys the same 1.4× whether zero, one or ten files changed — which is the
signature of a fixed cost that dominates everything the cache eliminated.

Profiling locates it precisely. In a no-op incremental rebuild of this
repository:

| Stage | Share of the rebuild |
|---|---|
| `build()` — discover, read, parse, resolve | **7%** |
| `iter_chunks()` — **secret scanning** | **74%** |
| `iter_chunks()` — everything else | 11% |
| `dump_all()` — write the artifacts | 7% |

**The proposal is therefore not to cache parsing harder, and not to make
resolution incremental.** Both are already cheap. It is to cache the
per-chunk secret-scan verdict, which is a pure function of the chunk body and
the policy and is recomputed in full for every unchanged file on every build.

Expected outcome: a no-op incremental rebuild drops from 1.4× to roughly
**5×** faster than cold, and the speedup finally becomes a function of how
much changed rather than a constant.

## Method

- Machine: Windows 11 (10.0.26200), Python 3.13.15, 16 logical CPUs.
- Times are **best of 3** wall-clock runs, not the mean: the floor the
  machine can do, with scheduler noise excluded.
- Two trees, to check that the conclusion is not an artifact of scale.
- End-to-end numbers invoke the real CLI (`python -m repo2graph.cli build
  ... --formats jsonl`) as a subprocess. Stage numbers come from a separate
  in-process profile, so they include grammar loading and are *not* directly
  comparable to the end-to-end figures — the ratios between stages are the
  finding, not the absolute values.
- Scripts are in the PR that introduced this document; they are not shipped,
  because they measure one machine and a committed number from one machine
  reads as a promise.
- The tree grew by six modules between the first measurement and the edge
  metadata work (`edge_schema_version: "2"`, which added a normalisation step
  per edge). The stage profile was re-run afterwards to check that addition
  had not moved the bottleneck: `build()` 8.44 s → 0.36 s, `iter_chunks()`
  3.22 s → 3.26 s, `dump_all()` 0.29 s → 0.31 s — the same shape, within
  noise of the figures below. The absolute seconds in this document are from
  the earlier, slightly smaller tree; the **ratios**, which are what the
  proposal rests on, are unchanged.

### Trees

| | Representative | Large |
|---|---|---|
| What | this repository | this repository plus a large tree of agent-session JSON/Markdown |
| Files discovered | 234 | 2,802 |
| Files parsed | 85 | 965 |
| Nodes / edges | 2,992 / 13,226 | 28,960 / 170,784 |
| Chunks | 3,467 | 41,233 |

The large tree is 92% `.json` and `.md` by count. It is not a realistic
*codebase*, and it is reported only to show that the conclusion holds two
orders of magnitude up — not as a claim about how repo2graph performs on a
real repository of that size.

## Measurements

### End-to-end: the speedup is constant

Representative tree:

| Scenario | Best (s) | vs cold | Cache |
|---|---|---|---|
| cold (no cache) | 6.07 | 1.00× | — |
| `--incremental`, nothing changed | 4.12 | **1.47×** | 234 cached, 0 reparsed |
| `--incremental`, 1 file changed | 4.15 | 1.46× | 233 cached, 1 reparsed |
| `--incremental`, 10 files changed | 4.29 | 1.42× | 224 cached, 10 reparsed |

Large tree:

| Scenario | Best (s) | vs cold | Cache |
|---|---|---|---|
| cold | 66.84 | 1.00× | — |
| `--incremental`, nothing changed | 46.99 | **1.42×** | 2,802 cached, 0 reparsed |
| `--incremental`, 1 file changed | 47.26 | 1.41× | 2,801 cached, 1 reparsed |
| `--incremental`, 10 files changed | 47.60 | 1.40× | 2,792 cached, 10 reparsed |

The interesting column is the last one. The cache is doing its job — 2,802 of
2,802 files reused — and the wall clock barely moves. Re-parsing ten files
costs 0.6 s out of 47. Whatever the remaining 46 s is, it is not parsing.

### Stage profile: it is not parsing

| Stage | Representative: cold → incremental | Large: cold → incremental |
|---|---|---|
| `build()` | 8.34 s → **0.28 s** (−97%) | 99.42 s → **3.86 s** (−96%) |
| `iter_chunks()` | 3.34 s → **3.23 s** (−3%) | 40.15 s → **40.42 s** (0%) |
| `dump_all()` | 0.29 s → 0.28 s | 2.69 s → 2.47 s |

The parse cache is close to perfect. `iter_chunks()` gets **no benefit at
all** from `--incremental`, and is 85% of what remains.

### Inside `iter_chunks()`: it is secret scanning

Same tree, same graph, same chunk count — only `--secret-policy` differs:

| `secret_policy` | `iter_chunks()` | Chunks |
|---|---|---|
| `redact-match` (default) | **3.22 s** | 3,467 |
| `off` | **0.42 s** | 3,467 |

Content-aware secret scanning is **2.8 s of 3.2 s — 87% of chunking, and
~74% of the whole incremental rebuild.**

This is not an argument for turning it off. Redaction is the control that
keeps a credential in an indexed file out of the artifacts and out of an
agent's context, and `--secret-policy off` is a deliberate opt-out, not a
performance knob. It is an argument for **not recomputing it for bytes that
have not changed.**

## Why the current design leaves this on the table

`--incremental` caches at exactly one layer:

```
discover -> [parse: CACHED] -> resolve -> chunk -> dump
```

`parse.cache.json` stores a `ParsedFile` per file keyed by sha256 + language.
That is sound and exact — a `ParsedFile` is a pure function of the bytes and
the language, which is why an incremental build is byte-identical to a full
one rather than merely close (`test_incremental_rebuild_equals_a_full_rebuild`).

But `iter_chunks()` then, for every file including every cached one:

1. re-reads the source from disk (`chunks.py`, `source_of`) — a second read,
   after `build()` already read the same bytes to hash them;
2. re-splits it into lines and re-slices every symbol span;
3. renders the header block (callers, callees, doc);
4. **runs the secret scanner over every chunk body.**

Steps 1–4 are a pure function of `(file bytes, symbol spans, edge
neighbourhood, secret policy)`. For an unchanged file with an unchanged
neighbourhood, every one of them produces the byte-identical result it
produced last build.

## Proposal

### P1 — cache the secret-scan verdict per file (the whole win)

Persist, next to `parse.cache.json`, a map:

```
{file sha256 + policy fingerprint} -> {scan verdict, redaction spans}
```

On an incremental build, a file whose sha256 and policy fingerprint both
match reuses the verdict instead of re-scanning. The policy fingerprint must
cover `--secret-policy`, `--secret-keyword` and `--secret-dir`, or a user who
adds a keyword gets the old verdict — that is the one way this can be
*wrong*, as opposed to merely slow, so it needs a test that changing each
input invalidates the entry.

- **Effort:** small. The cache file, the fingerprint, the lookup.
- **Expected:** removes ~74% of a no-op incremental rebuild.
- **Risk:** a stale verdict silently un-redacts a secret. Mitigated by
  fingerprinting every policy input, and by the existing byte-equality test,
  which would fail loudly if a cached verdict diverged from a fresh one.

### P2 — cache rendered chunk bodies for unchanged files

Extend the same cache to hold the rendered chunk text. Subsumes the second
disk read, the line splitting and the span slicing (the remaining 11%).

The subtlety: a chunk's header embeds its **callers and callees**, which are
global. A file can be unchanged while its caller set changes because some
*other* file started calling it. So the cache key must include a hash of the
node's edge neighbourhood, not just the file's sha256 — otherwise a stale
header claims a function has callers it no longer has, which is worse than
slow, because the header is what an agent reads as ground truth.

- **Effort:** medium, entirely because of that key.
- **Expected:** ~11% more.
- **Risk:** higher than P1. Do it after P1 and only if P1's win is not enough.

### P3 — skip rewriting unchanged artifacts

`dump_all()` is 7%. Not worth the complexity of partial artifact rewriting,
and it would compromise the transactional swap that makes an interrupted
build safe. **Rejected.**

### Explicitly rejected: incremental resolution

The obvious proposal — make the global name index, CALLS resolution and reach
incremental so they do not recompute over the whole symbol set — targets a
stage that already costs **0.28 s of a 3.79 s rebuild**. It is the most
complex change available and the smallest win, and it would trade away the
property that makes incremental builds trustworthy: resolution being global
and unconditional is *why* an incremental index is byte-identical to a full
one. **Rejected on the measurements.**

This is the finding worth recording. Intuition says a code indexer's
incremental path is bottlenecked on parsing and resolution. In this one, both
are already solved, and three quarters of the time goes to a security control
running over bytes it has already cleared.

## Expected results after P1

| Scenario | Today | After P1 (projected) |
|---|---|---|
| No-op incremental (representative) | 4.12 s (1.47×) | **~1.3 s (~4.7×)** |
| No-op incremental (large) | 46.99 s (1.42×) | **~12 s (~5.6×)** |
| 10 files changed (representative) | 4.29 s (1.42×) | **~1.4 s (~4.3×)** |

Projected by removing the measured secret-scan share from the incremental
total and leaving every other stage unchanged. They are arithmetic on
measurements, not predictions from a model, and they assume the verdict cache
achieves a hit rate comparable to the parse cache's — which it should, since
it is keyed on the same sha256.

### Acceptance criteria

1. A no-op incremental rebuild is **≥ 4×** faster than cold on the
   representative tree.
2. The speedup becomes a **function of how much changed**: ten files changed
   must be measurably slower than zero. Today it is not, and that is the
   clearest symptom of the problem.
3. `test_incremental_rebuild_equals_a_full_rebuild` still passes unchanged —
   byte equality is not negotiable for a performance change.
4. Changing `--secret-policy`, `--secret-keyword` or `--secret-dir`
   invalidates the cache, with a test per input.
5. A corrupt or unreadable verdict cache falls back to scanning, never to
   skipping. Same rule as the vector sidecar: the safe path is always
   available and never silent about being taken.

## Open questions

- **Cache file or one file?** P1 could extend `parse.cache.json` rather than
  add a sibling. Extending it means one fewer file to keep in sync but
  changes `PARSE_CACHE_FORMAT`, invalidating every existing cache on upgrade
  — acceptable once, worth doing deliberately.
- **Does the scan cost scale with file size or chunk count?** The two trees
  differ in both at once (3,467 vs 41,233 chunks; 234 vs 2,802 files), so
  this measurement cannot separate them. It does not change P1, but it does
  change whether the projection above holds for a repository of large files.
- **Is the scanner itself improvable?** 0.9 ms per chunk is not obviously
  fast. Profiling `secrets.py` directly might find a cheaper win than caching
  it — and unlike caching, it would also speed up **cold** builds, which P1
  does nothing for.

## See also

- [INDEXING.md](INDEXING.md) — the pipeline, the determinism guarantees, and
  what `--incremental` already promises
- [PERFORMANCE.md](PERFORMANCE.md) — build-time benchmarks across repositories
- [cli.md](cli.md#--incremental) — the flag
