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

1. **Discover** (`parse.discover`) — enumerate candidate files from `git ls-files`, falling back to
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

### Edge metadata

Every edge goes through `Graph.add_edge()`, which normalises three fields:

- **`method`** — how the edge was derived (for example `same_file`, `import_resolved`,
  `receiver_typed`, `name_only`). It records the evidence class, not a quality score.
- **`confidence`** — a float on `CALLS` edges only, reflecting how much the resolution narrowed the
  candidate set. It deliberately does **not** encode "how likely is this call at runtime": a
  dynamically dispatched call that resolved to exactly one candidate scores high.
- **`evidence`** — the source line the reader is shown. Structural edges (`CONTAINS`) and
  historical ones (`CO_CHANGE`) legitimately carry `evidence: null`; never synthesise a line for a
  relation that has no syntactic site.

The confidence threshold gate applies to `CALLS` edges only. `DEFINES`, `IMPORTS` and `INHERITS`
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
`chunks.jsonl`. `parse.discover()` therefore sorts by `Path.as_posix()` rather than returning
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

### Staleness

`status.compute_freshness()` is the single implementation of "is this index current", used by
`repo2graph index-status`. It compares the working tree against the manifest: mtime chooses which
files to hash, and the finding is always a hash mismatch — so a fresh clone, which rewrites every
mtime without changing a byte, does not read as stale. `doctor` deliberately has no freshness probe
of its own; two implementations of this answer drift until they contradict each other in front of a
user.

---

## 4. Module reference

Sizes are a rough guide to where the complexity is, not a target.

### Core pipeline

| Module | Lines | Owns |
|---|---:|---|
| `graph.py` | 2,117 | Node and edge construction; call, import and inheritance resolution including the scoped-resolution tiers; `CO_CHANGE` from git history; entrypoint marking. |
| `parse.py` | 1,943 | File discovery (`discover`, `_git_files`, `_walk_files`, `explain_path`), the language table (`LANG_CFG`, `EXT_LANG`), tree-sitter invocation, symbol and import extraction, `_callee_name`. |
| `export.py` | 1,440 | Every artifact writer — JSONL, GraphML, Cypher, manifest, overview — plus the `.r2g` directory layout. |
| `query.py` | 1,039 | `Index`: BM25 scoring, RRF fusion, `expand()` graph traversal, `retrieve()`, `pack_context()`. The whole retrieval layer, used identically by CLI, MCP and the Action. |
| `viz.py` | 894 | `graph.html`: force-directed layout, the self-contained HTML template, escaping, and the `NODE_TYPES`/`EDGE_TYPES` descriptions `export.py` imports. |
| `chunks.py` | 331 | Cutting source into retrieval units; the `_lines()` helper every slicer must use. |

### Surfaces

| Module | Lines | Owns |
|---|---:|---|
| `cli.py` | 1,985 | Argument parsing and every subcommand. The widest module by fan-out. |
| `impact.py` | 1,435 | PR and diff blast-radius analysis. |
| `mcp/` | 2,421 | The MCP server, split by concern — see below. |
| `answer.py` | 521 | `rag --answer` only — the one network path in the package. |
| `explain.py` | 364 | `explain edge` / `node` / `retrieval`. |

### `mcp/` package

| Module | Lines | Owns |
|---|---:|---|
| `traversal.py` | 493 | `repo_path_between`, `repo_impact`, `repo_blast_radius`, and the doubly bounded graph walks behind them. |
| `schemas.py` | 408 | Tool annotations, titles, descriptions and JSON schemas. Declaration only. |
| `server.py` | 340 | Server lifecycle, stdio connection, SDK version gate. |
| `retrieval.py` | 314 | `repo_map`, `repo_search`, `repo_neighbours`, `repo_find_symbol`, `repo_read`. |
| `tools.py` | 290 | `dispatch()`, the cache and build-status tools, and the re-export surface. |
| `indexes.py` | 123 | Index open/reopen, per-directory locking, auto-build. |
| `guardrails.py` | 113 | Budget clamping, argument ceilings (`MCP_MAX_*`), path scrubbing. |
| `nodes.py` | 96 | Node labelling and staleness helpers both retrieval and traversal need. |

### Support

| Module | Lines | Owns |
|---|---:|---|
| `security.py` | 771 | Credential path classification (`_is_secret_path`), content scanning and redaction, event/URL/header sanitisation. A leaf. |
| `demo.py` | 660 | The bundled demo repository and its five starter questions. |
| `status.py` | 613 | `repo2graph index-status` and the shared freshness computation. |
| `integrity.py` | 556 | Output path hardening, artifact checksums, provenance, index verification (`valid` / `corrupt` / `stale` / `incompatible` / `partial`). |
| `bugreport.py` | 395 | The redacted diagnostic bundle. |
| `fetch.py` | 368 | `repo2graph github` — clone, build, clean up. |
| `exclusions.py` | 311 | The named exclusion groups and their glob/reason tables. |
| `embed.py` | 307 | Vectors, and a stdlib-only `.npy` reader/writer. |
| `lock.py` | 299 | Build locking and stale-lock recovery. A leaf. |
| `audit.py` | 274 | Audit log sink. |
| `schema.py` | 269 | The record shapes the artifacts promise. |
| `tasks.py` | 268 | Background `--async-build` state. |
| `doctor.py` | 254 | Environment and artifact diagnostics. |
| `changelog.py` | 239 | The per-push structural graph diff in `human/CHANGELOG.md`. |
| `cache.py` | 223 | The MCP result cache. A leaf. |
| `events.py` | 160 | Structured event sink. |
| `edgemeta.py` | 158 | Edge citation formatting. |
| `limits.py` | 141 | Shared numeric ceilings. |

**If you add a module, make it a leaf.** `security.py`, `cache.py` and `lock.py` import nothing
from the package, which is why they are the easiest modules here to change safely.

### One import cycle you will meet

**`export.py` ↔ `viz.py`.** `export.py` does `from .viz import write_html` to emit `graph.html` as
one of its artifacts; only the `viz.py → export.py` direction is a cycle. The node and edge
descriptions therefore live once in `viz.py` (`NODE_TYPES`, `EDGE_TYPES`) and `export.py` imports
them alongside `write_html`. They used to be a hand-synced copy on each side, and the two had
already drifted.

---

## 5. Where a change goes

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

## 6. Tests

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
