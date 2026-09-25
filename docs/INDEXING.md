# Indexing: determinism, visibility and staleness

What `repo2graph build` actually does, what it guarantees about the bytes it
writes, how to see the state of an index without opening it, and how to
control what goes in.

- [The pipeline](#the-pipeline)
- [Determinism guarantees](#determinism-guarantees)
- [`index-status`](#index-status)
- [Git-aware metadata](#git-aware-metadata)
- [Controlling what gets indexed](#controlling-what-gets-indexed)
- [Staleness](#staleness)
- [Performance](#performance)
- [Failure modes](#failure-modes)

---

## The pipeline

```
discover  ->  parse  ->  resolve  ->  chunk  ->  dump
```

| Stage | Where | What it produces |
|---|---|---|
| **discover** | `parse.discover` | `(relpath, abspath)` pairs, sorted, after skip-dirs, include/exclude globs, secret paths, binary and size filters |
| **parse** | `graph.parse_all` / `parse_incremental` | one `ParsedFile` per file, across `--jobs` processes, in discovery order |
| **resolve** | `graph.build` | the global name index, then IMPORTS, CALLS (scoped → unique-global → ambiguous), INHERITS, entrypoints, reach |
| **chunk** | `chunks.iter_chunks` | one chunk per symbol, plus a `file_residual` per file with >40 chars left over |
| **dump** | `export.dump_all` | `nodes.jsonl`, `edges.jsonl`, `chunks.jsonl`, the human artifacts, `manifest.json` |

Two properties of the middle of that chain matter more than they look:

- **Discovery order is artifact order.** Nodes are emitted as files are
  parsed; edges and chunks follow the nodes. Anything that perturbs discovery
  order perturbs every artifact.
- **Resolution is global, not per-file.** The name index, CALLS confidences,
  INHERITS and reach are computed over the *whole* symbol set every build,
  including an incremental one. This is why `--incremental` is exact rather
  than approximate — see [the RFC](rfc-incremental-indexing.md).

`dump_all` stages every artifact in a sibling directory and renames it into
place on success, so an interrupted build leaves the previous index intact
rather than a half-written one.

## Determinism guarantees

**The same tree at the same commit produces byte-identical
`agent/nodes.jsonl`, `agent/edges.jsonl` and `agent/chunks.jsonl`** — on any
machine, any filesystem, at any `--jobs`, and whether built from scratch or
with `--incremental`.

Byte equality, not "the same graph up to ordering". Ordering is observable:
line order is the order chunks are scored and cited, and an index is a
committable artifact whose diff a human reviews. "Rebuilt on CI, 4,000 lines
changed, all of them reorderings" is indistinguishable from a real regression.

`tests/test_determinism.py` pins this across five axes:

| Axis | Test |
|---|---|
| Two builds of one tree | `test_two_builds_of_one_tree_are_byte_identical` |
| `--jobs 1` vs `--jobs 4` | `test_serial_and_parallel_builds_agree` |
| Filesystem enumeration order | `test_filesystem_enumeration_order_does_not_change_the_index` |
| git discovery vs `os.walk` | `test_git_and_walk_discovery_agree_on_the_same_tree` |
| Full vs `--incremental` | `test_incremental_rebuild_equals_a_full_rebuild` |

### What is *not* reproducible, and why

Two `manifest.json` fields differ between any two builds, by design:

| Field | Why it must differ |
|---|---|
| `build_id` | A fresh uuid4 per build. `vectors.meta.json` records the `build_id` it was computed against, which is how `integrity.verify_artifacts` detects vectors belonging to a different build. A stable id would make that check compare a value with itself. |
| `created_at` | The wall-clock build time, which `index-status` turns into "built 4h ago". |

`test_manifest_is_identical_apart_from_the_two_provenance_fields` asserts this
as an **allowlist**, so a third non-deterministic field added later fails the
suite rather than quietly joining the exceptions.

The human artifacts (`graph.html`, `overview.md`, `CHANGELOG.md`) are not
covered by the guarantee: they embed dates and are meant to be read, not
diffed.

### The bug this guarantee was written for

`git ls-files` sorts its output. `os.walk` returns whatever order the
filesystem hands back — alphabetical on NTFS, hash order on ext4 with
`dir_index`. Because discovery order is artifact order, a tree indexed on a
developer's Windows machine and the same tree indexed on a Linux CI runner
produced three artifacts that differed byte-for-byte while describing an
identical graph.

It was invisible to any same-machine A/B test: on NTFS the unsorted order and
the sorted order coincide. `discover()` now sorts both sources by the
resolved path's **posix** form — `str(Path)` would sort on `\` on Windows and
`/` elsewhere, which is the same divergence one level down.

`--max-files N` made it worse than untidy: it takes the first N in discovery
order, so two machines indexed *different subsets* of one tree and each could
answer questions the other could not.

## `index-status`

```bash
repo2graph index-status [-o DIR] [-r REPO] [--json] [--check]
```

Joins `manifest.json`, `stats.json` and the working tree into one report, and
adds the two facts no artifact records: how much disk the index occupies, and
whether the tree has moved since the build.

```
repo2graph index-status  [CURRENT]
==============================================================

Source
  commit         e2c0dc11
  branch         integration/combined-413-416  base=main (+45 commits)
  dirty at build no
  path           .

Index
  built          6m ago  (2026-09-25T16:44:22.290546+00:00)
  size           21.0 MB across 10 artifact file(s)
  tool version   2.0.0
  dense vectors  no (BM25 only)

Contents
  files          89 parsed of 243 discovered
  symbols        2244  (class=126, function=2118)
  nodes / edges  3075 / 13656
  edge types     CALLS=5165, CALLS_EXTERNAL=5095, CONTAINS=268, DEFINES=2242, IMPORTS=880, INHERITS=6
  languages      python=87, md=69, json=42, yml=23, html=5, javascript=2

Discovery
  mode           git  (git ls-files)
  skipped        155 file(s)
                      137  ignored by .gitignore
                       12  binary
                        6  over the size ceiling

Parsing
  parse errors   none

Freshness
  up to date     243 indexed file(s) match the tree
```

| Flag | Meaning |
|---|---|
| `-o DIR` | The index directory (default `.r2g`) |
| `-r REPO` | The source tree, when the index does not live inside it |
| `--json` | The full report as JSON; every key is public |
| `--check` | Exit `1` when freshness is not `current`, for a CI gate |

`--check` is what makes "the committed index matches this commit" a reviewable
property:

```yaml
- run: repo2graph index-status -o .r2g --check
```

Without the flag a stale index is a fact to report, not a failure.

### `index-status` vs `stats` vs `doctor`

| Command | Question it answers |
|---|---|
| `index-status` | What is in this index, where did it come from, is it current? |
| `stats` | What is the *retrieval quality* — call resolution tiers, unresolved imports? |
| `doctor` | Is anything broken — environment, dependencies, MCP wiring, corruption? |

`index-status` and `doctor`'s "Index Freshness" check share one implementation
(`status.compute_freshness`) rather than each having their own. Two
implementations of "is this stale" drift until they contradict each other in
front of a user; `test_doctor_and_index_status_never_disagree` pins that they
do not.

## Git-aware metadata

`manifest.json`'s `source_revision`, captured at build time by
`integrity.get_source_provenance`:

| Field | Notes |
|---|---|
| `commit`, `short_commit` | `HEAD` at build time |
| `branch` | Absent when detached |
| `tag` | Only when `HEAD` is exactly on a tag |
| `base_branch` | What this branch would merge into |
| `merge_base` | Where this branch left the base |
| `commits_ahead_of_base` | How far |
| `dirty`, `dirty_files` | Uncommitted changes at build time, and how many |
| `remote_url` | Credentials scrubbed by `secrets.sanitize_url` |

**Base branch detection** prefers `refs/remotes/origin/HEAD`, which is
authoritative but only present when the clone set it up — CI checkouts
routinely lack it. It then falls back to the first of `main`, `master`,
`develop`, `trunk` that exists as a remote or local ref.

**`dirty` excludes repo2graph's own scratch files.** `BuildLock` writes
`..r2g.r2glock` and `dump_all` stages artifacts in
`..r2g.staging.<pid>.<hex>/`, both *beside* the output directory so they
survive the transactional swap — which means `.r2g/` in `.gitignore` covers
neither, and provenance is captured from inside both windows. Every build of
a pristine repository used to record `dirty: true` and blame the user's tree
for repo2graph's own files. Only untracked (`??`) entries are filtered;
anything git is tracking is yours, whatever it is called.

## Controlling what gets indexed

Four layers, applied in this order. `repo2graph explain-path <path> -r .`
reports which single rule decided any given path, using the same rule set
`build` would.

**1. Built-in skip directories** (`parse.DEFAULT_SKIP_DIRS`) — matched as a
path *segment* at any depth: `.git`, `node_modules`, `venv`, `dist`, `build`,
`target`, `vendor`, `__pycache__`, `.next`, tool caches. Add more with
`--exclude-dir NAME`; `--include-vendor` removes `vendor` from the set.

> This is a flat name set, so a legitimate source package literally named
> `build/` or `target/` is invisible to indexing. `index-status`'s skipped
> counts are where that shows up, and `doctor`'s "Ignored Paths" check warns
> when more than two thirds of candidates were skipped.

**2. Secret paths** (`secrets.py`) — credential-shaped paths are refused, and
secret-shaped *content* inside files that are indexed is redacted per
`--secret-policy`. `--include-secrets` disables the path refusal.

**3. `--include` / `--exclude` globs** — yours, applied to the posix relative
path.

**4. `--exclude-group NAME`** — named groups, repeatable and composable with
`--exclude`:

```bash
repo2graph build . -o .r2g --exclude-group generated --exclude-group dependencies
repo2graph build . -o .r2g --exclude-group all
repo2graph build . -o .r2g --exclude-group help     # print the table, build nothing
```

| Group | Covers | Already default? |
|---|---|---|
| `generated` | protobuf/gRPC stubs (`*_pb2.py`, `*.pb.go`), codegen output (`*.g.dart`, `*.designer.cs`), minified bundles (`*.min.js`), `generated/`, `__generated__/`, `autogen/` | no |
| `vendor` | `vendor/`, `third_party/`, `Pods/`, `bower_components/` at any depth | `vendor` only |
| `build` | `dist/`, `build/`, `out/`, `target/`, `.next/`, `coverage/` at any depth | mostly |
| `dependencies` | `node_modules/`, `site-packages/`, `.venv/`, and every common lockfile | dirs only, not lockfiles |
| `sensitive` | `.env*`, `*.pem`, `*.key`, `*.p12`, `secrets/`, `.aws/`, `.ssh/` | mostly, via `secrets.py` |

The "already default?" column is the honest part. `vendor` and `build`
overlap heavily with `DEFAULT_SKIP_DIRS` at the repository root — they earn
their keep in a monorepo, because `DEFAULT_SKIP_DIRS` cannot express depth
and these globs can. The groups that add genuinely new coverage on an
ordinary repository are **`generated`** (a `*_pb2.py` sitting inside a source
package is invisible to a directory-name rule) and the **lockfiles** in
`dependencies`.

### Why bother

Generated code is usually the largest and most repetitive text in a
repository, so it dominates BM25 and crowds hand-written code out of a
bounded pack. `doctor`'s "Generated / Vendored Code" check finds it *after*
the fact and prints the exact `--exclude-group` flags to rebuild with — the
check and the flag classify paths through the same table
(`exclusions.GROUPS`), so a shape the check can flag is always a shape the
flag can exclude.

Every glob is asserted against representative paths in
`test_every_exclusion_glob_matches_its_representative_paths`, because
`parse._glob_re` anchors a pattern containing `/` at the repository root:
`vendor/**` silently misses `packages/web/vendor/...`, which is where a
monorepo keeps all of it. The groups use `**/vendor/**`.

## Staleness

`status.compute_freshness` runs three independent signals, cheapest first,
each degrading to a note rather than an error:

1. **Commit** — `manifest.json`'s recorded commit against the tree's current
   `HEAD`. One `git rev-parse`. Exact for committed state, silent on a
   non-git tree.
2. **File set** — what discovery finds now against `index.state.json`. A set
   difference over paths; no file reads.

   The re-discovery uses the filters the **build** used, not the defaults.
   `index.state.json` records them under `filters` (`--include`,
   `--exclude` — with `--exclude-group` already expanded — `--exclude-dir`,
   `--include-vendor`, `--include-secrets`, the secret keyword/dir additions
   and `--max-file-mb`), because there is nowhere else to recover them from
   once the build exits. Without that, every file a build deliberately
   excluded comes back as newly *added*, and any build with an `--exclude`
   reads as stale the instant it finishes — worthless for exactly the users
   who configured indexing most carefully. An index written before `filters`
   existed falls back to defaults and says so in a note.
3. **Content** — a real sha256 for each file whose mtime is newer than
   `manifest.json`.

The mtime in signal 3 **only chooses what to hash**. A file touched but not
changed hashes equal and is reported unchanged, so a fresh clone — which
rewrites every mtime — does not read as stale. The converse (a file edited
with its mtime preserved) is missed, which is why signal 1 exists and why
`--incremental` re-hashes everything rather than trusting mtimes.

Status values:

| Value | Meaning |
|---|---|
| `current` | Every signal that could be checked agrees |
| `stale` | At least one says otherwise |
| `unknown` | Nothing could be checked — no `index.state.json`, not a git tree, or past the `MAX_FRESHNESS_FILES` scan bound |

`unknown` is deliberately not a warning. "I could not tell" is a different
claim from "it is out of date", and reporting the second when you mean the
first trains people to ignore the field.

Past `MAX_FRESHNESS_FILES` (50,000) the file-level comparison is skipped and
the report says so. `index-status` is meant to be cheap enough to run from a
prompt, and an index is routinely consumed from elsewhere, so its size is not
this process's to trust.

## Performance

Measured on this repository. The figures and the method live in one place
so they cannot drift apart:
**[rfc-incremental-indexing.md](rfc-incremental-indexing.md)**.

What dominates, in order:

1. **Parsing** — tree-sitter over every changed file. CPU-bound, which is why
   `parse_all` spreads it across `--jobs` processes above
   `PARALLEL_MIN_FILES` (64) and why `--incremental` targets exactly this.
2. **Reading** — every file is read every build, even incrementally: the
   hash *is* the bytes, so there is no cheaper way to know a file is
   unchanged.
3. **Resolution** — global, recomputed every build.
4. **Writing** — three JSONL files plus the human artifacts.

`--git-history N` adds a `git log` pass for CO_CHANGE edges and is skipped
entirely at the default `0`.

## Failure modes

| Failure | Behaviour | Surfaced by |
|---|---|---|
| A file fails to parse | Node and text chunks are still created; no CALLS edges from it | `index-status` → Parsing; `doctor` → Parser Coverage |
| A file is unreadable | Skipped; no node | build stats |
| A language has no grammar | Indexed as plain text; no symbols | `doctor` → Parser Coverage |
| The parser pool cannot start | Falls back to serial parsing, same output | (silent by design) |
| `--parse-policy strict` and a syntax error | Build aborts with the file named | `ParseError` |
| Build interrupted | Staging directory discarded; previous index intact | — |
| Two builds race the same `-o` | Second waits on `BuildLock`, then `--lock-timeout` | `LockTimeoutError` |
| Graph exceeds `--max-nodes` | Build aborts | `GraphLimitExceeded` |
| Index corrupt on disk | Reported per artifact | `doctor` → Artifact Integrity |

## See also

- [rfc-incremental-indexing.md](rfc-incremental-indexing.md) — benchmarks and
  the proposal for what incremental indexing should become
- [LANGUAGE_SUPPORT.md](../LANGUAGE_SUPPORT.md) — language scorecard generator, parse quality, symbol and edge extraction rates across 17 grammars
- [BENCHMARK.md](../BENCHMARK.md) — 25-task evaluation across 5 application archetypes with citation accuracy and query latency
- [cli.md](cli.md) — every flag
- [limitations.md](limitations.md) — what the graph does not model
- [quickstart.md](quickstart.md) — two minutes from install to a cited answer
