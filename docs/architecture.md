# Architecture

How repo2graph turns a source tree into a queryable graph, what that graph contains, and which
module owns which part of it.

Contributor setup, the test commands and the cross-platform invariants are in
[.github/CONTRIBUTING.md](../.github/CONTRIBUTING.md).

---

## 1. The pipeline

```
                      ┌──────────┐
   discovery + parse  │ parse.py │  tree-sitter, file discovery, symbol extraction
                      └────┬─────┘
                           │
                      ┌────▼─────┐
   graph construction │ graph.py │  nodes, edges, call/import/inheritance resolution
                      └────┬─────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
        ┌─────▼────┐ ┌─────▼─────┐ ┌────▼─────┐
        │chunks.py │ │ export.py │ │  viz.py  │   retrieval units, artifacts, graph.html
        └─────┬────┘ └─────┬─────┘ └──────────┘
              │            │
              └─────┬──────┘
                    │
              ┌─────▼─────┐
              │ query.py  │  BM25, graph expansion, pack_context
              └─────┬─────┘
                    │
        ┌───────────┼───────────┬──────────────┐
        │           │           │              │
   ┌────▼───┐  ┌────▼────┐ ┌────▼────┐  ┌──────▼──────┐
   │ cli.py │  │ mcp/    │ │explain  │  │  answer.py  │
   └────────┘  └─────────┘ └─────────┘  └─────────────┘
```

Two required third-party packages (`tree-sitter`, `tree-sitter-language-pack`). No graph library —
degree counting, force-directed layout and GraphML/Cypher generation are plain Python, on purpose.

**Data flows one way.** Parse knows nothing about graphs; graph knows nothing about chunks or
retrieval; retrieval knows nothing about the CLI or MCP. A change that reverses one of those arrows
is a bug in the change, not a missing feature.

The stages, in order:

1. **Discover** (`discovery.discover`, re-exported as `parse.discover`) — enumerate candidate files from `git ls-files`, falling back to
   a directory walk. Applies `DEFAULT_SKIP_DIRS`, the size ceiling, and the exclusion layers below.
2. **Parse** (`parse.py`) — run the tree-sitter grammar for each file's language, extract symbol
   definitions and import statements.
3. **Build** (`graph.py`) — create nodes and edges, resolve call targets against the symbol table,
   resolve imports to in-repo files where possible, and read git history for `CO_CHANGE`.
4. **Chunk** (`chunks.py`) — cut source into retrieval units, one per symbol plus a file residual.
5. **Export** (`export.py`, `viz.py`) — write the `.r2g` artifacts and `graph.html`.
6. **Query** (`query.py`) — BM25 over chunks, RRF fusion, graph expansion, context packing.

---

## 2. Graph representation

### Node types

| Type | Meaning |
|---|---|
| `repo` | the repository itself; one per index |
| `dir` | a directory |
| `file` | a source, doc or config file |
| `symbol` | a function, method, class, struct, trait, interface, type or module |
| `module` | an import target that is not a file in this repo |
| `external` | a call target that could not be resolved in this repo (stdlib or third-party) |

A symbol node's id is `sym:<path>::<qualname>`. When two definitions in one file share a qualname
(overloads, conditional definitions) the id gains an `@L<line>` suffix so it stays unique.

### Edge types

| Type | Meaning |
|---|---|
| `CONTAINS` | repo -> dir -> file |
| `DEFINES` | file -> symbol, and symbol -> symbol nested inside it |
| `IMPORTS` | file -> file (`internal: true`) or file -> module |
| `CALLS` | symbol -> symbol in this repo; carries `count` and `confidence` |
| `CALLS_EXTERNAL` | symbol -> external, a name that resolved to nothing in-repo |
| `INHERITS` | symbol -> base class or interface |
| `CO_CHANGE` | file <-> file, edited together in 3+ of the commits read by `--git-history` |
| `TESTS` | test symbol -> non-test symbol it reaches through at most 2 `CALLS` hops |

`TESTS` means **tests that reach this symbol via calls**, not tests that assert on it: nothing
reads an assertion. A test symbol is any symbol defined in a file `testpaths.is_test_path`
claims (`tests/`, `test/`, `__tests__/`, `spec/`, `test_*.py`, `*_test.py`, `*_test.go`,
`*.test.ts`, `*.spec.ts`, `*Test.java`, `*_spec.rb`, ...), helpers and fixtures included.
`graph.add_tests_edges` walks `CALLS` out of each one up to `TESTS_MAX_HOPS` (2), through test
helpers and production code alike, and adds one edge per non-test symbol reached, never
test -> test. `confidence` is the product of the `CALLS` confidences along the best path (so two
0.5 guesses in a row read as 0.25, not 0.5), `evidence` is the call in the test where that path
starts, and `hops` is its length. `CALLS` edges that are not evidence of a call
(`untyped_receiver`, `shadowed_builtin`) are not walked. `stats.json` reports `edge:TESTS`,
`testable_symbols` / `tested_symbols` (non-test functions and methods, and how many have an
incoming `TESTS` edge) and their ratio `tested_symbol_fraction` -- an upper bound on what the
suite exercises, never a coverage figure. Retrieval does not follow `TESTS` by default; pass
`edge_types` to `pack_context` to opt in. `repo_neighbours` lists a symbol's `TESTS` edges.

### Reference edges (`--reference-edges`, off by default)

`build --reference-edges` adds three edge types, for Python and JS/TS only (#397):

| Edge | Meaning |
| --- | --- |
| `READS` | function/method -> a module-level variable or constant, or a class field (`self.x`, `this.x`), it reads |
| `WRITES` | function/method -> one it assigns: a module-level name (in Python only under `global`; in JS/TS any assignment to a name not declared in the function) or a class field |
| `REFERENCES` | function/method/class -> a class, interface, type or enum named in a type annotation |

It also makes the targets symbols: module-level assignments become `variable` or `constant`
(all-caps names) nodes, and class-level ones `field` nodes. Without the flag none of this
exists and every artifact is byte-identical to a build that never had it.

Precision is preferred to recall, and the limits are plain:

- A name binds only to a symbol defined at the top level of the same file, or one the file
  imports by that name (`from settings import TIMEOUT`, `RETRIES as R`). There is no
  repository-wide fallback, so these edges are not ambiguous the way `CALLS` can be; a name
  that does not bind is counted in `reference_edges_unresolved` and dropped.
- `self.x` binds only to a field **declared in the class body**. An attribute first assigned in
  `__init__` has no node to point at, so it is unresolved, as is a field inherited from a base.
- `module.NAME` (attribute access on an imported module), locals, parameters and closures over
  an enclosing function's locals make no edge. A local that shadows a module-level name is a
  local, so it makes no `READS`.
- Python string annotations (`"Foo"`) are not read.

Volume, measured: on this repository `--reference-edges` adds 26% nodes and 8% edges; on
Django 28% nodes, 8.5% edges and 13.5% output, for 7,688 `READS`, 219 `WRITES` and 9
`REFERENCES` (Django annotates almost nothing). `stats.json` reports
`reference_edges_reads`, `_writes`, `_references` and `_unresolved` when it is on.

### Framework edges (`--framework-edges`, off by default)

`build --framework-edges` adds three edge types and one node type, for Python
and JS/TS only:

| Edge | Meaning |
| --- | --- |
| `ROUTES_TO` | `route` -> the handler symbol whose decorator declares it; exact, confidence 1.0 |
| `INJECTS` | function/method -> a dependency it declares (FastAPI `Depends`, `Security`) |
| `MODELS` | model class -> a related model class, from an ORM relation field |

| Node | Meaning |
| --- | --- |
| `route` | an HTTP endpoint, id `route:<METHOD> <path>`, with the mount prefix already joined on |

**Why a route node exists at all.** Nothing in a repository *calls* a request
handler -- the framework does, at runtime -- so before this a handler was an
entrypoint with no incoming edge and nothing in the graph named the endpoint it
serves. Two of the five questions the README opens with ("where is
authentication enforced?", "trace a request from route to persistence") start
at an endpoint, and `ROUTES_TO` is the edge that makes them answerable. One
`route` node per method-and-path, shared by every handler declaring it: two
handlers on one path is a real thing, and both edges are emitted rather than
one silently winning.

**Only decorator-declared routing is read.** FastAPI, Flask, Starlette, Sanic
and NestJS put the route on the definition, so the handler is the decorated
symbol and the edge is syntactic rather than inferred. Express, Hono and Koa
register with a *call* -- `app.get("/x", handler)` -- where the handler is an
argument, often an inline arrow with no symbol to point at, and the
registration can sit behind a loop or a helper; Django's `urlpatterns` has the
same shape. Those need call-argument dataflow, not a decorator read, so they
get **no edge**, and `stats.json` does not pretend otherwise. A missing route
edge never means "no route", exactly as a missing `CALLS` edge never means
"no call".

The resolution rules differ per edge, deliberately:

- **`ROUTES_TO`** resolves nothing. The handler is the symbol the decorator is
  attached to, so confidence is 1.0 and `resolution_kind` is `decorator`. The
  path is recorded exactly as written -- `{uid}`, `<int:uid>` and `:id` are
  three frameworks' syntax for one idea, and normalising them would stop the
  edge's evidence matching the line it cites. A class-level mount decorator
  (`@Controller('users')`, `@Blueprint`, `@APIRouter`) prefixes the routes its
  methods declare, so NestJS's `@Controller('users')` plus `@Get(':id')` is
  one route, `GET /users/:id`.
- **`INJECTS`** binds a name the way reference edges do: a symbol at the top
  level of the same file, or one the file imports by that name. There is no
  repository-wide fallback, so an unbound dependency is counted in
  `framework_edges_unresolved` and dropped. Only a plain callable name is
  taken: `Depends(get_db)` resolves, `Depends(Factory().build)` does not,
  because what is named there is an expression rather than a symbol.
- **`MODELS`** is the one framework edge with a repository-wide fallback,
  because Django's string form (`ForeignKey("Author")`) exists precisely to
  name a model in another app. A name that binds in-file or by import resolves
  there; otherwise every model class with that name is a candidate, priced at
  `1/n` and marked `ambiguous`, never picked. `"self"` is Django's
  self-reference and becomes an honest self-edge. Several fields relating one
  class to the same model (`author` and `editor` both `ForeignKey(Author)`)
  are **one** edge carrying `count` and every declaring `field` -- edges are
  identified by their endpoints, so this is the same choice `CALLS` makes for
  repeated calls of one name.

`stats.json` reports `framework_edges_routes`, `_injects`, `_models` and
`_unresolved` when the flag is on, and nothing when it is off. Retrieval
follows these edges by default once they exist (`ROUTES_TO` inwards, from a
handler to its endpoint; `INJECTS` outwards; `MODELS` both ways) -- a default
build has none, so this cannot change what an unflagged index returns. Without
the flag nothing is captured and every artifact is byte-identical to a build
that never had the feature, including `parse.cache.json`; flipping the flag
invalidates the parse cache, which records `framework_edges` in its identity.

**Java and Kotlin annotations are not covered yet** (`@GetMapping`,
`@Autowired`). They are the obvious next entry, and they are not a table entry
away: Spring picks the implementation out of its container at runtime and an
`@Autowired` field names an interface, so the edge wants its own design rather
than a name added to `HTTP_METHODS`.

### Exact resolution from SCIP (`--scip`, off by default)

`CALLS` and `INHERITS` are name-resolved (the tiers in `graph.build`). `build --scip index.scip`
(#380) reads a [SCIP](https://github.com/sourcegraph/scip) index, the protobuf a compiler-grade
indexer writes (`scip-python`, `scip-typescript`, `scip-java`, ...), and uses it wherever it has an
answer. `scip.py` owns all of it; `graph.build` calls into it at three points.

- **Reading.** A hand-rolled protobuf wire-format reader for the few `scip.proto` messages used
  (no protobuf dependency, like `embed.py`'s `.npy` reader). The file is untrusted: size capped at
  256 MiB before it is read, varints at most 10 bytes, every length checked against what is left,
  group wire types refused, nesting fixed by the schema with no recursion, and occurrence,
  document and string counts bounded. Malformed input raises `ScipError`; the build then warns
  (a stderr line plus a `scip_index_rejected` event), records `scip_error` in `stats.json`, and
  keeps every heuristic edge. Both range encodings are read (the deprecated packed `range` and the
  typed `single_line_range`/`multi_line_range`, which wins when both are set).
- **Identity.** A SCIP symbol maps to a node through its *definition site*, not by translating its
  moniker: the `Definition` occurrence in document `path` on line `L` whose last descriptor is
  named `n` is the repo2graph symbol of `path` that starts on line `L` and is named `n`
  (parameters and type parameters never map; a `local N` symbol is named by its `display_name`).
  A SCIP symbol that lands on two nodes (a conditional redefinition) is left unmapped.
- **Merge.** Only for files whose document is in the index. For each call site of `callee`, the
  reference occurrences on the call's line whose last descriptor is `callee`'s name decide it
  (several same-named calls on one line pair up by column). A mapped symbol is a `CALLS` edge at
  confidence 1.0 with `method` and `resolution_kind` `scip`; a symbol no indexed file defines is a
  `CALLS_EXTERNAL` edge, also `scip`; anything else -- no occurrence, a parameter, an attribute,
  a symbol that did not map -- leaves that site to the tiers, with the heuristic edge's `count`
  reduced to the sites left. A class's base resolves through its `is_implementation`
  relationships, else the base's occurrence in the class header; a base defined outside the index
  gets no `INHERITS` edge, as an unresolved base never did. A file absent from the index is not
  touched.

The pass runs after parsing over the parsed symbols and the index, so full and `--incremental`
builds stay byte-identical (the parse cache holds parse results only; `scip_index` is not part of
its identity). Without the flag nothing is read, and every artifact is byte-identical to a build
that never had the feature; `manifest.json` describes the `scip` method only when such edges exist.
`stats.json` reports `scip_documents`, `scip_files_covered`, `scip_files_uncovered`,
`scip_symbols_mapped`, `scip_call_sites_resolved` (of which `scip_call_sites_external`),
`scip_call_sites_heuristic` (sites in covered files left to the tiers),
`scip_call_sites_uncovered`, `scip_calls_ambiguous` (ambiguous fan-outs still emitted from covered
files), `scip_bases_resolved` and `scip_bases_heuristic` -- all of them, and only, with `--scip`.

The index is never auto-detected: a repository shipping an `index.scip` does not change how it is
resolved unless the user passes `--scip` or the repository's own config sets `scip`, whose path
must resolve inside the repository. Even a crafted index can only pick among definitions whose
names already sit on the cited line. A stale index degrades to name resolution wherever a name
moved off its recorded line; regenerate it with the code.

### Edge metadata

Every edge goes through `Graph.add_edge()`, which normalises three fields:

- **`method`** — how the edge was derived (for example `same_file`, `import_resolved`,
  `receiver_typed`, `name_only`, or `call-graph` for `TESTS`, which is derived from `CALLS`
  edges rather than read from source). It records the evidence class, not a quality score.
- **`confidence`** — a float on `CALLS` edges (and `TESTS`, which inherits it), reflecting how
  much the resolution narrowed the candidate set. It deliberately does **not** encode "how likely is this call at runtime": a
  dynamically dispatched call that resolved to exactly one candidate scores high.
- **`evidence`** — the source line the reader is shown. Structural edges (`CONTAINS`) and
  historical ones (`CO_CHANGE`) legitimately carry `evidence: null`; never synthesise a line for a
  relation that has no syntactic site.

The confidence threshold gate applies to `CALLS` and `TESTS` edges only. `DEFINES`, `IMPORTS` and `INHERITS`
carry no confidence and must not be dropped by a confidence filter.

### Artifacts

`export.py` owns the `.r2g` layout (`SECTIONS`, `path()`, `atomic_write`). An index directory holds
an `agent/` section for machine consumption and a `human/` section for reading:

| File | Contents |
|---|---|
| `agent/nodes.jsonl` | one JSON object per node |
| `agent/edges.jsonl` | one JSON object per edge, with `method`/`confidence`/`evidence` |
| `agent/chunks.jsonl` | retrieval units with `start_line`/`end_line` and text |
| `agent/manifest.json` | index version, build config, counts, node/edge type descriptions |
| `agent/stats.json` | parse counts, language breakdown, ambiguous-call totals |
| `human/graph.html` | the self-contained force-directed view |
| `human/CHANGELOG.md` | the structural diff against the previous build |
| `local.json` | the absolute source root — machine-local, never published |

`local.json` sits outside `agent/` because it is the one file that must not travel with a published
index; the GitHub Action drops it from both the upload and the branch push.

---

## 3. Indexing behaviour

### Determinism

The discovery order dictates the emitted order of every record in `nodes.jsonl`, `edges.jsonl` and
`chunks.jsonl`. `discovery.discover()` therefore sorts by `Path.as_posix()` rather than returning
filesystem order, and applies `DEFAULT_SKIP_DIRS` identically on both the git-tracked and
directory-walk paths. Two builds of the same commit on different machines produce byte-identical
artifacts.

Each guarantee below is held by a named test, so a guarantee that stops being true fails the suite
rather than going quietly stale:

| Guarantee | Test |
|---|---|
| Two builds of one tree are byte-identical | `test_two_builds_of_one_tree_are_byte_identical` |
| Serial and parallel builds agree | `test_serial_and_parallel_builds_agree` |
| Filesystem enumeration order does not change the index | `test_filesystem_enumeration_order_does_not_change_the_index` |
| The git and walk discovery paths agree on the same tree | `test_git_and_walk_discovery_agree_on_the_same_tree` |
| An incremental rebuild equals a full one | `test_incremental_rebuild_equals_a_full_rebuild` |
| The sort key is POSIX, not filesystem order | `test_discovery_sort_key_is_posix` |
| Truncating at `--max-files` picks the same files every time | `test_max_files_truncation_is_deterministic` |
| Every exclusion glob matches its own representative paths | `test_every_exclusion_glob_matches_its_representative_paths` |
| A fresh clone, whose mtimes all moved, does not read as stale | `test_freshness_ignores_a_touched_but_unchanged_file` |
| A build does not index its own output directory | `test_freshness_does_not_count_the_index_as_added_source` |

### Parse budget

A file's parse can be far costlier than its size suggests: `x = a<b<a<b<...;` as C++ is quadratic
in tree-sitter's time and memory, and 300 KB of it exhausted 13.8 GB. tree-sitter cannot be
interrupted from Python, so `parsepool.py` runs parses that can be expensive in worker processes and
kills a worker whose file runs over `--parse-timeout` (10 s by default; the slowest real file across
the five benchmark corpora took 0.74 s). On Linux a worker's address space is capped too, so a
runaway allocation crashes only that worker, and the parent re-runs the files in flight one at a time
to pin the crash on the file that caused it. Either way the file keeps its node, contributes no
symbols, is counted (`skipped_parse_timeout`, `skipped_parse_crash`) and is never cached. A parallel
build supervises every file; a serial one parses files up to 16 KB in-process, where a parse over
budget is discarded after the fact rather than interrupted. Workers start from a forkserver on
POSIX, never by forking the (often multi-threaded) build process, and a pool that makes no
progress for 120 s (no worker starting up, starting or finishing a file) is abandoned for an in-process parse rather than waited on.

The budget is the one input to the graph that is not a function of the tree: a file that runs over
it on a slow machine and under it on a fast one indexes differently. It is set an order of magnitude
above any real file so that this never happens outside an adversarial input.

### Provenance recorded in the manifest

`manifest.json` records where the index came from. Two top-level keys carry it:

| Key | Contents |
|---|---|
| `source_revision` | an object describing the local checkout the build ran against (below) |
| `source_remote` | set instead of a local root when the build came from `repo2graph github` |

The fields inside `source_revision` are written by `integrity.get_source_provenance()`, and each is
present only when git could answer for it — a non-git tree gets an empty object, and a repository
with no diverged base branch gets no `merge_base`:

| Field | Meaning |
|---|---|
| `commit`, `short_commit` | the revision the build ran at |
| `branch` | the checked-out branch, omitted on a detached `HEAD` |
| `tag` | the tag pointing at this commit, when there is one |
| `dirty`, `dirty_files` | whether the tree had uncommitted edits, and how many files |
| `base_branch` | the branch the working tree was compared against |
| `merge_base` | the merge base between `HEAD` and `base_branch` |
| `commits_ahead_of_base` | how far `HEAD` had moved past `merge_base` |
| `remote_url` | the origin URL, with any credentials stripped |

`repo2graph index-status --json` reports the same facts but reshapes them into its own `source`
section rather than printing the manifest verbatim.

### What gets excluded, and by which layer

Four layers, applied in this order:

1. **`DEFAULT_SKIP_DIRS`** — `.git`, `node_modules`, `__pycache__`, virtualenvs and the like, plus
   the index's own output directory. A build never indexes its own artifacts.
2. **Size and binary ceilings** — `MAX_BYTES` per file; files that do not decode as text are
   skipped and counted.
3. **Secret paths** (`security.py`) — credential-shaped paths are refused by default, and secret
   content inside files that *are* indexed is redacted. `--include-secrets` lifts the path
   exclusion only; content redaction stays on.
4. **Named exclusion groups** (`exclusions.py`) — `generated`, `vendor`, `build`, `dependencies`,
   `sensitive`, selected with `--exclude-group`. These are glob-shaped and independent of layer 3:
   `sensitive` is a second net for a tree that names its credentials something `security.py` does
   not recognise.

### Incremental rebuild

`build(cache=...)` reads a `{path: entry}` map from `agent/parse.cache.json` and reuses a file's
`ParsedFile` when its sha256 *and* its language both still match. Everything downstream of parsing
is then recomputed from the complete symbol set, exactly as a full build does: the global name
index, `CALLS` confidences, `INHERITS`, `mark_entrypoints()` and `reach`.

Nothing is spliced, and that is the whole design. `build()` resolves `CALLS` through a *global* name
index and sets `confidence = 1/len(candidates)`, so adding or deleting a symbol named `run` in file A
changes the confidence — and the count — of `CALLS` edges emitted from files B and C that did not
change at all; `mark_entrypoints()`/`reach` is a whole-graph BFS on top of that. A merge that
reparsed only the changed paths and spliced their nodes and edges in would produce an index that is
wrong in a way nothing detects: stale confidences and stale entrypoint flags flow straight into
`chunks.jsonl` headers and into `pack_context`'s `min_confidence` gate. Rebuilding every caller's
edges from its cached symbols is what makes a repo-wide confidence shift land correctly on an
unchanged caller.

The cost model is what makes exactness cheap rather than a compromise, and it is **scale-dependent
in a way this section used to get wrong**. It claimed "parsing dominates a build and resolution is
O(edges) and negligible". That holds on the small and medium fixtures, and
[`benchmarks/perf/README.md`](../benchmarks/perf/README.md#50000-files-88) refutes it at 50,000
files, where the same build divides as:

| stage | wall |
|---|---:|
| parse (4 workers) | 3.4 s |
| discovery, call resolution, graph assembly | 26.6 s |
| write (chunk text + every artifact) | 46.9 s |

Parsing is 4% of an 81 s build, not the dominant half, and an unchanged `--incremental` rebuild
takes **98.2 s — slower than the 81 s full build it is meant to shortcut**, because it pays to load
and check the parse cache and then re-runs resolution and writing in full anyway.

So the honest statement is narrower than the old one. Re-resolution is cheap *relative to writing*
at every size measured, which is what makes whole-graph re-resolution the right trade against the
stale-confidence bug described above — a partial merge would buy back at most the 26.6 s that
resolution costs, and none of the 46.9 s that dominates. Every file is still *read* — the content
hash is the bytes, and there is no cheaper way to know a file is unchanged. At 50,000 files reading
is no longer "the small half" either; it is folded into the discovery-and-assembly row.

The acceptance test is whole-artifact byte equality against a full rebuild across an add, a modify,
a delete and a no-op (`tests/test_incremental.py`). That is also why the hit/miss tallies live on
`Graph.incremental` rather than in `Graph.stats`: `stats.json` is one of the artifacts compared, so a
counter that differs between the two routes by construction would have had to be special-cased out of
the comparison, weakening the very test that makes the feature trustworthy.

Deliberately not implemented: partial confidence recalculation over "affected symbol namespaces", and
a reverse-edge index for a partial reach BFS. Both compute the same answer as the full re-resolution
above, cost more code and more ways to be subtly wrong, and — now that there is a profile — chase the
smaller of the two costs. The profile says the incremental work worth doing is **incremental
writing**: rewriting only the records whose files changed, rather than re-serialising 696 MB of
`chunks.jsonl` on every rebuild. That is what would make `--incremental` faster than a full build at
50,000 files instead of slower, and it is a change to `export.py`, not to resolution. Until then
`--incremental` is for its correctness guarantee (byte-identical output), not for speed, and the
perf README says so.

### Staleness

`status.compute_freshness()` is the single implementation of "is this index current", used by
`repo2graph index-status`. It compares the working tree against the manifest: mtime chooses which
files to hash, and the finding is always a hash mismatch — so a fresh clone, which rewrites every
mtime without changing a byte, does not read as stale. `doctor` deliberately has no freshness probe
of its own; two implementations of this answer drift until they contradict each other in front of a
user.

---

## 4. Language support

Nineteen languages get symbol-level treatment, plus the code embedded in Vue and Svelte components
and Jupyter notebooks (below); every other file is still indexed as text.
Coverage is not uniform across them, and the differences are large enough that "supported"
on its own would mislead.

The scorecard below is computed from this project's own test corpus. **Only two of the nineteen
are additionally measured on third-party repositories** — Python and TypeScript, in
[`benchmarks/real/`](../benchmarks/real/README.md) — and the gap between those two kinds of
evidence is not small. Both parse defects found in the 2026-10 retrieval round
(class fields holding functions; export aliases having no node) sat in the single TypeScript
repository in that corpus, scored Tier 1 at 82 (B) here, and were invisible to the synthetic
suite. A high row below means "the patterns we thought to write a test for work", not "this
language has been exercised against real code".

<!-- BEGIN GENERATED: language-scorecard -->
| Language | Tier | Overall | Parsing | Symbols | Calls | Imports | Tests Link | Framework | Repo Tests |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **tsx** | Tier 1 | **94 (A)** | 95 (A) | 100 (A) | 100 (A) | 90 (A-) | 100 (A) | 70 (B-) | 1 funcs |
| **typescript** | Tier 1 | **94 (A)** | 95 (A) | 100 (A) | 100 (A) | 90 (A-) | 100 (A) | 70 (B-) | 3 funcs |
| **javascript** | Tier 1 | **90 (A-)** | 95 (A) | 80 (B) | 100 (A) | 90 (A-) | 100 (A) | 70 (B-) | 5 funcs |
| **python** | Tier 1 | **85 (B+)** | 95 (A) | 55 (D) | 100 (A) | 90 (A-) | 100 (A) | 70 (B-) | 10 funcs |
| **java** | Tier 2 | **73 (B-)** | 90 (A-) | 65 (C+) | 75 (B-) | 80 (B) | 92 (A-) | 20 (F) | 1 funcs |
| **go** | Tier 2 | **72 (B-)** | 95 (A) | 65 (C+) | 55 (D) | 100 (A) | 86 (B+) | 0 (F) | 5 funcs |
| **kotlin** | Tier 2 | **70 (B-)** | 90 (A-) | 70 (B-) | 55 (D) | 90 (A-) | 86 (B+) | 0 (F) | 4 funcs |
| **php** | Tier 3 | **75 (B-)** | 90 (A-) | 85 (B+) | 80 (B) | 90 (A-) | 64 (C) | 0 (F) | 5 funcs |
| **rust** | Tier 3 | **70 (B-)** | 90 (A-) | 75 (B-) | 55 (D) | 100 (A) | 56 (D) | 0 (F) | 1 funcs |
| **ruby** | Tier 3 | **68 (C+)** | 90 (A-) | 45 (F) | 80 (B) | 80 (B) | 94 (A) | 0 (F) | 3 funcs |
| **csharp** | Tier 3 | **67 (C+)** | 90 (A-) | 60 (C) | 65 (C+) | 90 (A-) | 60 (C) | 0 (F) | 3 funcs |
| **c** | Tier 3 | **60 (C)** | 75 (B-) | 60 (C) | 55 (D) | 80 (B) | 56 (D) | 0 (F) | 1 funcs |
| **cpp** | Tier 3 | **60 (C)** | 75 (B-) | 60 (C) | 55 (D) | 80 (B) | 56 (D) | 0 (F) | 16 funcs |
| **objc** | Tier 4 | **67 (C+)** | 85 (B+) | 85 (B+) | 55 (D) | 80 (B) | 56 (D) | 0 (F) | 2 funcs |
| **scala** | Tier 4 | **67 (C+)** | 90 (A-) | 70 (B-) | 55 (D) | 90 (A-) | 56 (D) | 0 (F) | 0 funcs |
| **swift** | Tier 4 | **67 (C+)** | 90 (A-) | 70 (B-) | 55 (D) | 90 (A-) | 56 (D) | 0 (F) | 4 funcs |
| **bash** | Tier 4 | **57 (D)** | 85 (B+) | 35 (F) | 55 (D) | 80 (B) | 56 (D) | 0 (F) | 3 funcs |
| **hcl** | Tier 4 | **46 (F)** | 85 (B+) | 10 (F) | 55 (D) | 50 (D) | 56 (D) | 0 (F) | 3 funcs |
| **lua** | Tier 4 | **41 (F)** | 85 (B+) | 35 (F) | 55 (D) | 0 (F) | 56 (D) | 0 (F) | 2 funcs |
<!-- END GENERATED: language-scorecard -->

Regenerate with `python scripts/generate_language_scorecard.py`;
`tests/test_doc_consistency.py::test_language_scorecard_matches_the_generator` fails if this
table and the generator disagree.

**What the numbers are, and are not.** Each dimension is scored by introspecting what the parser
actually declares for that language — the `LANG_CFG` entry's `kind_map`, `call_types`,
`import_types` and `doc` coverage, whether `_BASE_NODES` reaches its inheritance clause, whether
`parse_import_details` and `resolve_import` handle its module syntax, and whether the suite has
tests naming it. Two inputs are expert judgement rather than measurement: grammar robustness
(the macro penalty that holds C and C++ to 75) and ecosystem manifest awareness. Nothing in this
repository measures per-language `parse_errors` rates across real code, so those two columns are
the ones to distrust; `stats.json` reports `parse_errors` per build if you want to check a
language against your own tree. These are **capability** scores, not retrieval accuracy — they say what the
pipeline is equipped to extract, not how often an answer about that language is right. For
measured retrieval quality see [benchmarks/real/README.md](../benchmarks/real/README.md), which
covers Python and TypeScript only.

`Framework` is the dimension that still caps most of this table, but it is no longer a flat zero.
`build --framework-edges` extracts `ROUTES_TO`, `INJECTS` and `MODELS` for Python and JS/TS, which
is what takes TypeScript and TSX to an A overall — the first rows here to reach one. The remaining
cap is real rather than bookkeeping: only *decorator*-declared routing is read, so Express, Hono
and Django's `urlpatterns` contribute nothing, `CONFIGURES` does not exist at all, and Java and
Kotlin annotations (`@GetMapping`, `@Autowired`) are not covered — which is most of why Java sits
at 20 and Go at 0. See *Framework edges* above for what each edge does and does not promise.

**`Tests Link` was scored as a missing feature until it was checked.** `TESTS` edges have shipped
since the edge table in §2 above was written (`derived.add_tests_edges`), but the generator
asserted "TESTS edge type not yet implemented" and gave every language 30 or 50, costing all
nineteen 10% of their weighted total for a feature that was already there. The dimension now
scores the two things the edge actually depends on: whether `testpaths.is_test_path` recognises
the language's test files — directory conventions count for everything, filename suffixes are
read back out of the suffix tables — and the language's own `Calls` score, because a `TESTS` edge
is derived by walking `CALLS` and cannot exist where calls do not resolve. That is why Go and
Kotlin sit at 86 with strong test-file detection and weak call resolution, while Ruby reaches 94
on two filename conventions (`_test.rb`, `_spec.rb`) and a `Calls` score of 80.

**Priority order for deep support.** TypeScript/JavaScript and Python first: they are the two
ecosystems already at Tier 1, the two the retrieval benchmark measures, and the two where
framework-aware edges (routes, DI, ORM models) would pay off most. Go is the third, chosen over
Java/Kotlin because its import resolution already scores 100 while its call resolution is the
weakest part of an otherwise strong entry — the narrowest gap to close. C and C++ are explicitly
not on this list: the macro problem is upstream of anything repo2graph can fix without a
preprocessor.

**Files whose language the extension does not settle.** `parse.refine_lang` decides, from the
bytes already read, before anything is parsed. Line numbers are always the file's own, so a
citation opens on the right line.

| Extension | Parsed as |
| --- | --- |
| `.h` | Objective-C with `@interface`/`@protocol`, C++ with any C++ signal, else C (#377) |
| `.m` | Objective-C with `@interface`, `@implementation`, `@protocol` or `#import`; otherwise MATLAB, which is indexed as text |
| `.mm` | Objective-C (the Objective-C++ in it parses with errors, best-effort) |
| `.vue`, `.svelte` | The `<script>` blocks, as TypeScript when any has `lang="ts"`, else JavaScript. The template is blank lines to the parser, so bindings like `@click="save"` make no edge |
| `.ipynb` | The code cells, as Python, at the JSON lines they occupy. Magics and `!` shell lines are dropped; a chunk names its cell (`# notebook cell 3`) and its text is the code, not the JSON or the outputs. A minified notebook (not one source line per JSON line) yields no symbols |

**HCL / Terraform** (`.tf`, `.hcl`): every top-level block with a label is a symbol named by its
Terraform address — `aws_s3_bucket.logs` (kind `resource`), `data.aws_iam_policy_document.read`,
`var.env`, `module.vpc`, `output.bucket_arn`, `provider.aws`, and `local.<name>` for each
attribute of a `locals` block. A reference from one block to another (`aws_s3_bucket.logs.arn`,
`var.env`, `module.vpc.id`) is a `CALLS` edge, which is Terraform's own dependency graph; a
local module's `source = "./modules/vpc"` is an `IMPORTS` edge to that directory's `main.tf`.
Built-in functions (`merge`, `cidrsubnet`) are external calls.

---

## 5. Module reference

Sizes are a rough guide to where the complexity is, not a target.

### Core pipeline

| Module | Lines | Owns |
|---|---:|---|
| `graph.py` | 1,886 | Node and edge construction; call and inheritance resolution including the scoped-resolution tiers; the parse pass (`parse_all`, `parse_incremental`, chunked reads). |
| `imports.py` | 437 | Import resolution: an import statement's text to the in-repo file it names, per language. |
| `parsecache.py` | 169 | The incremental parse cache: one file's parse result to JSON and back (`PARSE_CACHE_FORMAT`). |
| `derived.py` | 233 | What is derived from the resolved graph: `READS`/`WRITES`/`REFERENCES`, entrypoint marking, `TESTS`. |
| `cochange.py` | 239 | `CO_CHANGE` (and `--git-authors`) from a bounded, timed read of `git log`. |
| `parse.py` | 2,098 | The language table (`LANG_CFG`, `EXT_LANG`), content sniffing, tree-sitter invocation, symbol and import extraction, `_callee_name`. |
| `discovery.py` | 727 | Which files a build indexes (`discover`, `_git_files`, `_walk_files`, exclusions) and `explain_path`. |
| `refcapture.py` | 280 | Reference capture for `--reference-edges`: reads, writes and type references per symbol. A leaf. |
| `frameworks.py` | 366 | `--framework-edges` capture: routes, injected dependencies and ORM relation targets read off a decorated definition. All the framework knowledge lives in its tables. A leaf. |
| `export.py` | 1,621 | Every artifact writer — JSONL, GraphML, Cypher, manifest, overview — plus the `.r2g` directory layout. |
| `query.py` | 2,058 | `Index`: BM25 scoring, RRF fusion, `expand()` graph traversal, `retrieve()`, `pack_context()`. The whole retrieval layer, used identically by CLI, MCP and the Action. |
| `store.py` | 460 | `GraphStore`: the one interface traversal and retrieval read the graph through (node by id, a node's incident edges, nodes and edges in file order). `MemoryGraphStore` is the default; `SqliteGraphStore` is a stdlib-only on-disk backend, selected with `query`/`rag --store sqlite` and cached at `<index>/graph.sqlite` (keyed on the index's `build_id`, never on mtime). It also carries a `chunks` table, so `--store sqlite` answers retrieval without holding `chunks.jsonl` resident. `open_graph_store` owns the cache's lifecycle: one builder at a time behind `BuildLock`, a per-process staging name so concurrent materialisation cannot collide, a symlink refusal on both the primary and relocated paths, and a per-uid `0700` fallback under the system temp directory when the index directory is read-only. Order is part of the contract. |
| `viz.py` | 1,295 | `graph.html`: force-directed layout, the self-contained HTML template, escaping, and the `NODE_TYPES`/`EDGE_TYPES` descriptions `export.py` imports. |
| `testpaths.py` | 59 | `is_test_path`: which paths are test code, shared by retrieval's test demotion and `TESTS` edges. |
| `chunks.py` | 494 | Cutting source into retrieval units; the `_lines()` helper every slicer must use. |
| `rerank.py` | 105 | `--rerank`: opt-in cross-encoder reordering of the first-stage candidates, before any budget is spent. A leaf. |
| `tokenizers.py` | 67 | The token counters `--tokenizer` selects, each carrying the `token_count_method` it reports. A leaf. |
| `federated.py` | 178 | `--index` more than once: per-index retrieval merged by reciprocal rank under one budget. |

### Surfaces

| Module | Lines | Owns |
|---|---:|---|
| `cli.py` | 1,942 | Argument parsing and the build, github, query and rag commands. The widest module by fan-out. |
| `cli_inspect.py` | 341 | Commands that inspect an index or explain a result: map, stats, diff, explain-path, explain, index-status, bug-report, doctor, demo, completion. |
| `cli_embed.py` | 225 | `embed` and `--verify-rag`. |
| `mcp/` | 3,406 | The MCP server, split by concern — see below. |
| `answer.py` | 762 | `rag --answer` only — the one network path in the package. |
| `explain.py` | 363 | `explain edge` / `node` / `retrieval`. |

### `mcp/` package

| Module | Lines | Owns |
|---|---:|---|
| `traversal.py` | 375 | `repo_path_between`, `repo_blast_radius`, and the doubly bounded graph walks behind them. |
| `schemas.py` | 453 | Tool annotations, titles, descriptions and JSON schemas. Declaration only. |
| `server.py` | 711 | Server lifecycle, stdio connection, SDK version gate. |
| `retrieval.py` | 718 | `repo_map`, `repo_search`, `repo_neighbours`, `repo_find_symbol`, `repo_read`. |
| `tools.py` | 304 | `dispatch()`, the cache and build-status tools, and the re-export surface. |
| `indexes.py` | 185 | Index open/reopen, per-directory locking, auto-build. |
| `guardrails.py` | 103 | Budget clamping, argument ceilings (`MCP_MAX_*`), path scrubbing. |
| `nodes.py` | 164 | Node labelling and staleness helpers both retrieval and traversal need. |
| `pagination.py` | 157 | Signed, self-contained continuation cursors for paged tool results. A leaf. |
| `resources.py` | 133 | MCP resources and prompts: the index's own documents, beside the tools. |

### Support

| Module | Lines | Owns |
|---|---:|---|
| `security.py` | 1,181 | Credential path classification (`_is_secret_path`), content scanning and redaction, event/URL/header sanitisation. A leaf. |
| `demo.py` | 687 | The bundled demo repository and its five starter questions. |
| `status.py` | 644 | `repo2graph index-status` and the shared freshness computation. |
| `integrity.py` | 752 | Output path hardening, artifact checksums, provenance, index verification (`valid` / `corrupt` / `stale` / `incompatible` / `partial`). |
| `bugreport.py` | 403 | The redacted diagnostic bundle. |
| `doctor.py` | 466 | Environment and artifact diagnostics. |
| `fetch.py` | 443 | `repo2graph github` — clone, build, clean up. |
| `exclusions.py` | 239 | The named exclusion groups and their glob/reason tables. |
| `embed.py` | 344 | Vectors, and a stdlib-only `.npy` reader/writer. |
| `scip.py` | 850 | `build --scip`: a bounded protobuf reader for SCIP indexes, symbol-to-node mapping, and the exact-edge merge. |
| `lock.py` | 345 | Build locking and stale-lock recovery. A leaf. |
| `audit.py` | 291 | Audit log sink. |
| `schema.py` | 315 | The record shapes the artifacts promise. |
| `tasks.py` | 260 | Background `--async-build` state. |
| `changelog.py` | 230 | The per-push structural graph diff in `human/CHANGELOG.md`. |
| `cache.py` | 223 | The MCP result cache. A leaf. |
| `events.py` | 190 | Structured event sink. |
| `edgemeta.py` | 171 | Edge citation formatting. |
| `limits.py` | 141 | Shared numeric ceilings. |
| `parsepool.py` | 306 | Supervised parse workers: the per-file parse budget, the Linux worker memory cap, and pinning a crash on the file that caused it. A leaf. |
| `config.py` | 247 | `[tool.repo2graph]` and `.repo2graph.toml`: the build options a repository can set for itself, validated, and where each one came from. A leaf. |
| `graphdiff.py` | 639 | The structural diff between two builds, behind `repo2graph diff` and the per-build changelog. Also the semantic PR surface: `get_git_diff_spans` maps a `git diff` to touched line ranges, and `find_affected_tests` walks `CALLS`/`TESTS`/`INHERITS`/`ROUTES_TO` backwards from the touched symbols to the tests covering them. That walk is tuned for recall, not precision — a missed test means CI is green over unexercised code — so it keeps ambiguous (`confidence = 1/n`) edges and reports `truncated` when `max_hops` cut it short, rather than returning a short answer that looks complete. |
| `watch.py` | 110 | `build --watch`: the polling snapshot and the quiet period before a rebuild. |
| `neo4j.py` | 131 | `build --neo4j-uri`: pushing `graph.cypher` to Neo4j's transactional endpoint in batches. A leaf. |

**If you add a module, make it a leaf.** `security.py`, `cache.py` and `lock.py` import nothing
from the package, which is why they are the easiest modules here to change safely.

### One import cycle you will meet

**`export.py` ↔ `viz.py`.** `export.py` does `from .viz import write_html` to emit `graph.html` as
one of its artifacts; only the `viz.py → export.py` direction is a cycle. The node and edge
descriptions therefore live once in `viz.py` (`NODE_TYPES`, `EDGE_TYPES`) and `export.py` imports
them alongside `write_html`. They used to be a hand-synced copy on each side, and the two had
already drifted.

---

## 6. Where a change goes

| You want to… | Start at | Also touch |
|---|---|---|
| Add a language | `parse.py` — `LANG_CFG`, `EXT_LANG` | A fixture, and the language list in the README (a test enforces this) |
| Change how a call resolves | `graph.py` — the scoped-resolution tiers | `tests/test_scoped_resolution.py`, `tests/test_resolution_heuristics.py` |
| Add an edge kind | `graph.py`, then `viz.py`'s `EDGE_TYPES` | The edge table above, and `query.py`'s `DEFAULT_EDGE_DIRS` if it should be traversable |
| Change retrieval | `query.py` — **`pack_context()`, not `retrieve()`** | `retrieve()` is a pinned back-compat surface; see the two-budget-models invariant in CONTRIBUTING |
| Add an MCP tool | `mcp/schemas.py`, then a handler in `retrieval.py` or `traversal.py`, then `dispatch()` | Clamp every numeric argument **in the handler**; `docs/mcp.md` (a test enforces the doc) |
| Add a CLI flag | `cli.py` | `docs/cli.md` and the README command table (both enforced by `tests/test_doc_consistency.py`) |
| Add an artifact | `export.py` — a writer plus a `SECTIONS` entry | `manifest.json`'s description map, and the artifact table above |
| Add an Action input | `action.yml` | `docs/cli.md`'s Action section (enforced); mind the shell/expression truthiness trap in CONTRIBUTING |

---

## 7. Tests

The files that will fail on a change you did not expect to be load-bearing:

| File | Guards |
|---|---|
| `test_doc_consistency.py` | Every CLI subcommand and every `LANG_CFG` grammar appears in the README and `docs/cli.md`; every Action input in `docs/cli.md`; every MCP tool in `docs/mcp.md`. |
| `test_compat.py` | Byte-identical `query`/`rag` output against a pinned baseline; the Action's input/output contract; the `action.yml` shell-vs-expression truthiness gate. |
| `test_determinism.py` | Discovery order, and that every exclusion glob matches its own representative paths. |
| `test_version_surfaces.py` | The version string in every surface that carries it, and that the bump script covers all of them. |
| `test_encoding.py` | The cp1252 / non-ASCII path class that has caused every historical regression here. |

Run the suite with `python -m pytest`. CI additionally runs `ruff check .`,
**`ruff format --check .`** and `mypy repo2graph` — the format check is a separate gate from the
lint check and is easy to miss locally.
