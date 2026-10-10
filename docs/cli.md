# CLI reference

Every flag `repo2graph` takes, and what it actually counts. See the
[README](architecture.md) for the five-minute version, or
[docs/architecture.md](architecture.md) for the two-minute one.

```
repo2graph demo              index a bundled example repo and answer 5 questions
repo2graph build   <repo>    parse a folder into a graph + RAG chunks
repo2graph github  <repo>    clone a GitHub project, then build
repo2graph query   <question>  fast local search over a built index
repo2graph rag     [target] <question>  pack a cited context for an LLM
repo2graph embed             add meaning-based search to an index
repo2graph map               redraw graph.html from a built index
repo2graph stats             print the index counts
repo2graph index-status      provenance, contents, exclusions and freshness
repo2graph bug-report        privacy-preserving diagnostic bundle for an issue
repo2graph doctor    [path]  diagnose environment, permissions, and index
repo2graph explain-path <path> explain file inclusion/exclusion precedence
repo2graph explain <edge|node|retrieval> explain edges, nodes, or retrieval
repo2graph completion [shell] print shell completion setup script
repo2graph version           print the version (also -v / --version)
```

Run `repo2graph` with no arguments and it prints help and exits 0. Ctrl-C stops
with exit code 130 instead of a traceback, closing a pipe early (`| head`) is not
an error, and an index that is missing, half-written or corrupt gets a sentence
naming the rebuild command that fixes it. All numeric flags reject negatives.

## `demo` — the first command to run

```bash
repo2graph demo [-o DIR] [--keep] [--full]
```

Writes a small bundled repository (an orders service: routes → auth guard →
rules → SQL store, plus a test module and a JS client) to a scratch
directory, builds a real index over it with the same code path `build` uses,
and answers the five starter questions against it. Nothing is downloaded, no
LLM is called, and no repository of your own is needed.

| Flag | Default | Meaning |
|---|---|---|
| `-o`, `--out DIR` | a temp directory | write the demo repo here and keep it |
| `--keep` | off | keep the temp repo and its index instead of deleting them |
| `--full` | off | print every answer in full, not the first cited block |

The fixture is held as source strings inside `repo2graph/demo.py`, not as
package data, so it is present under `uvx`, `pip`, Docker and a git checkout
alike. With no `-o` the repository is materialised into a temp directory and
removed on the way out; `--out` and `--keep` both leave it in place so you
can run further commands against `<dir>/.r2g`.

Each answer prints its citation table — `path:start-end`, the symbol, and
*why* the block is in the pack (`seed`, or the edge that reached it) —
followed by the head of the first cited block. The five questions themselves
are listed in [the README](../README.md#five-questions-to-start-with).

## `build` — make the map

```bash
repo2graph build /path/to/project -o .r2g --git-history 200
```

| Flag | Default | What it does |
| --- | --- | --- |
| `-o`, `--out` | `.r2g` | Where the map is written. |
| `--formats` | `jsonl,graphml,cypher,overview,html` | Which artifacts to write. Drop what you do not need to save time. |
| `--include` | none | Glob(s) to keep, e.g. `'**/*.py'`. |
| `--exclude` | none | Glob(s) to skip, e.g. `'**/test/**'`. Patterns are globs, not gitignore rules: a trailing slash is stripped and a pattern with no `/` left matches a *file name* at any depth, so `--exclude tests/` matches only a file literally named `tests` and still indexes the directory. Spell a directory as `dir/**` (or use `--exclude-dir NAME`). |
| `--parse-policy` | `best-effort` | AST error handling policy: `best-effort` (log and continue), `warn` (emit stderr warnings), `strict` (fail build on syntax error). |
| `--git-history` | `0` | Commits to read for `CO_CHANGE` arrows. Capped at 5000. |
| `--cochange-min` | `3` | Minimum co-edits across git history required to emit a `CO_CHANGE` edge. |
| `--max-files` | `0` (all) | Stop after N files, for very large projects. |
| `--max-bytes` | `0` (no limit) | Stop once discovered files exceed N bytes in total. Keeps a *prefix* of discovery order, so the selection is reproducible; a file that would cross the budget stops the build rather than being skipped over. |
| `--max-total-bytes` | `0` (no limit) | Alias for `--max-bytes`: stop once discovered files exceed N bytes in total. |
| `--max-edges` | `0` (no limit) | Keep at most N edges. Edges grow with how interconnected the code is, not with file count, so this is the bound that matters on a dense repository. |
| `--max-nodes` | `0` (unbounded) | Bound on graph node count before triggering the limit policy (0 = unbounded). |
| `--max-chunks` | `0` (no limit) | Keep at most N retrieval chunks; further chunks are dropped. |
| `--max-memory-mb` | `0` (no limit) | Stop or truncate when estimated memory consumption exceeds N MB. |
| `--max-build-seconds` | `0` (no limit) | Stop or truncate when wall-clock build duration exceeds N seconds. |
| `--limit-policy` | `warn` | What a reached limit does. `warn` (default) says so on stderr once per limit; `truncate` cuts quietly; `fail` aborts immediately with `GraphLimitExceeded`. All record the cut under `limits_hit` in `stats.json` and mark the index as incomplete in `manifest.json`. |
| `--jobs` | `0` (auto) | Parallel workers. Auto means one per core, up to 8. |
| `--viz-nodes` | `300` | Node cap in `graph.html`. `0` draws an empty graph; `all` draws every node. |
| `--no-chunks` | off | Skip the retrieval chunks entirely. |
| `--max-call-candidates` | `5` | When a call's name matches several symbols and none can be picked by scope, it fans out to at most this many `CALLS` edges, each at confidence 1/n (n = the edges kept); further candidates get no edge. Minimum 1. Recorded as `max_call_candidates` in `manifest.json`. |
| `--max-file-mb` | `1.5` | Files larger than this are skipped (or chunked). Minimum is 0.1 MB. |
| `--include-vendor` | off | Index files inside `vendor/` directories (skipped by default). |
| `--exclude-dir` | none | Additional directory name to skip. Repeatable (e.g. `--exclude-dir generated --exclude-dir tmp`). |
| `--exclude-group` | none | Exclude a named group of paths: `generated`, `vendor`, `build`, `dependencies`, `sensitive`, or `all`. Repeatable, composable with `--exclude`. `--exclude-group help` prints what each covers and builds nothing. See **[docs/architecture.md](architecture.md#what-gets-excluded-and-by-which-layer)**. |
| `--chunk-large-files` | off | Instead of skipping, split files larger than `--max-file-mb` into parseable chunks. |
| `--incremental` | off | Reuse parse results for files whose content hash is unchanged. |
| `--include-secrets` | off | Explicitly opt in to indexing secret/credential files (excluded by default). |
| `--secret-policy` | `redact-match` | Inline content secret handling: `redact-match` (default, line-preserving), `exclude-file`, `warn-only`, `off`. |
| `--secret-keyword` | none | Custom substring keyword for secret file matching (repeatable). |
| `--secret-dir` | none | Custom directory name for secret directory matching (repeatable). |
| `--allow-symlink-out` | off | Allow `-o` to point through a symbolic link. Off by default to prevent accidental writes outside the repo tree. |
| `--force` | off | Allow overwriting an existing directory that was not created by repo2graph. Without this flag, build refuses to write into any non-empty directory that does not contain a recognised index. |
| `--lock-timeout` | `60` | Seconds to wait for the per-output-directory build lock before failing. Increase this when several CI jobs share the same network-mounted output path. |
| `--debug` | off | Enable debug mode: re-raise unexpected internal errors instead of swallowing them (also controlled via `REPO2GRAPH_DEBUG=1`). |

**Examples:**
```bash
# Include vendor directories and parse huge files in chunks (useful for monorepos)
repo2graph build /path/to/project --include-vendor --chunk-large-files

# Skip 'generated' and 'tmp' directories, and adjust file limit to 5 MB
repo2graph build /path/to/project --exclude-dir generated --exclude-dir tmp --max-file-mb 5.0
```

Artifacts are staged in a sibling temp directory and atomically swapped into
`--out` on success, so a crash or a full disk never leaves a half-written index
behind. The previous build is restored on failure. Files written by subsequent
commands (`embed`, `github`) are preserved across rebuilds.

### Large repositories and memory

The whole graph is held in memory while it is built and exported, and the
GraphML and Cypher writers each hold their own rendering of it. On a repository
with hundreds of thousands of symbols that reaches gigabytes. If an agent or
`rag` is the only consumer, skip the two export formats:

```bash
repo2graph build . --formats jsonl,overview,html
```

`--max-memory-mb` and `--max-build-seconds` stop a build that grows past a
ceiling, and `--max-files`, `--max-nodes` and `--max-edges` cap its size; with the
default `--limit-policy warn`, every cut is recorded in `stats.json`.

### `--incremental`

Every build writes `agent/parse.cache.json`: each file's sha256 alongside the
symbols and imports parsed out of it. With `--incremental`, the next build into
the same `--out` re-reads every file but only *re-parses* the ones whose hash or
language changed. Parsing is what dominates a build, so on a repo where a handful
of files moved this is close to free.

The result is byte-for-byte identical to a full rebuild, and that is a property
of the design rather than a hope. A file's symbol table depends on its own bytes
and its language and nothing else, so reusing one is exact. Everything that is
*not* per-file — the repo-global name index, `CALLS` confidences, `INHERITS`
edges, entrypoint flags and reach counts — is recomputed from the complete symbol
set on every build, incremental or not. Nothing is spliced, so nothing goes
stale. `tests/test_incremental.py` asserts the byte equality directly across an
added file, a modified file, a deleted file and a no-op.

The build report gains an `incremental` block when the flag is on:

```json
{ "incremental": { "cached": 812, "reparsed": 3 } }
```

Every build report also carries a `performance` block, for tracking build cost
over time. It lives in the report rather than `stats.json`, which stays
byte-identical between a full and an incremental build:

```json
{ "performance": { "build_seconds": 1.09, "write_seconds": 4.55, "peak_rss_mb": 170.0,
                   "source_bytes": 7316338, "output_bytes": 40010607, "chunk_text_ratio": 1.43 } }
```

`peak_rss_mb` covers the parse workers too and is `null` on Windows.
`chunk_text_ratio` is chunk text over source size: chunk overlap and part
headers repeat some lines, and on this repository that costs 43%.

**When a full rebuild is still required.** The cache is keyed on file content, so
it cannot see a change in how content is *interpreted*. Rerun without the flag
after upgrading repo2graph, after a `tree-sitter-language-pack` upgrade that
changes a grammar, or if you ever suspect the cache. Doing so costs only time —
a full build overwrites the cache and puts you back on a known-good footing.
Cache entries written by a different cache format are ignored automatically, as
is a cache that is missing, unreadable or corrupt; each of those degrades to a
full build rather than to a wrong one.

### Config file

Options a project always builds with can live in the repository instead of on
every command line, in CI and for the MCP server alike:

```toml
# pyproject.toml
[tool.repo2graph]
git-history = 500
viz-nodes = 600
exclude = ["vendor/**", "**/generated/**"]
secret-keywords = ["corp-token", "internal-key"]
secret-dirs = ["deploy/secrets"]
```

The same keys can go at the top level of a `.repo2graph.toml` in the repository
root. Precedence, most specific first:

1. a flag on the command line;
2. `[tool.repo2graph]` in the repo's `pyproject.toml`;
3. `.repo2graph.toml` in the repo root;
4. the built-in defaults above.

Keys are spelled like the flags they stand in for: `include`, `exclude`,
`exclude-dir`, `secret-keywords` and `secret-dirs` take lists of strings;
`git-history` and `max-call-candidates` integers; `viz-nodes` an integer or
`"all"`; `max-file-mb` a number; `secret-policy` one of the `--secret-policy`
choices; `chunk-large-files` and `include-vendor` `true`/`false`. A list given
on the command line replaces the file's list rather than adding to it, and a
flag typed at its default value (`--git-history 0`) still beats the file. An
unknown key or a mistyped value stops the build with an error naming the file
and the key.

`build`, the auto-build `rag <source-dir>` runs, and the MCP server's auto-build
all read it; `explain-path` does too, and names the file when one of its rules
decided a path (`Path matches exclude glob 'gen/**' from pyproject.toml
[tool.repo2graph] exclude`). `github` does not read the cloned repository's
file. Reading TOML needs Python 3.11+, or the `tomli` package on 3.10; without
either, a config file is skipped with a one-line note on stderr. With no config
file, nothing changes.

## `github` — map a project you do not have locally

```bash
repo2graph github psf/requests -o out/requests --git-history 200
```

Downloads to a temp directory, builds the map, tidies up, and writes an extra
`agent/index.json` recording exactly which project and which commit it read. `gh`
is an alias, and full web URLs work. Takes every `build` flag, plus:

| Flag | Default | What it does |
| --- | --- | --- |
| `--ref` | default branch | Branch or tag to read. |
| `--depth` | `0` (full) | Shallow-clone depth. |
| `--keep-clone` | temp dir | Clone here and keep it instead. |
| `--token` | `$GH_TOKEN` / `$GITHUB_TOKEN` | Token for a private project. |

## `query` — fast local search

```bash
repo2graph query "how does routing match a path" -o .r2g -k 8 --hops 1
repo2graph query "auth middleware" -o .r2g --format json | jq '.[].path'
```

Finds the best matching pieces, then follows the arrows one step out so the
functions around each answer come along too.

| Flag | Default | What it does |
| --- | --- | --- |
| `-o`, `--out` | `.r2g` | Index folder to read. |
| `-k` | `8` | Pieces the text search starts with. |
| `--hops` | `1` | Steps to walk along the arrows. |
| `--budget` | `24000` | Character budget for the **chunk text only**. |
| `--retrieve-budget-chars` | — | Alias of `--budget` whose name says what it caps: retrieved chunk text, in characters. |
| `--min-conf`, `--min-confidence` | off | Drop `CALLS` arrows below this confidence. |
| `--format` | `text` | `text` or `json`. `--json` is the old spelling of `--format json`. |
| `--include-secrets` | off | Include secret-looking files (`.env`, keys, credentials) in the results. Off by default **even if the index was built with `--include-secrets`** — see [Secrets at query time](#secrets-at-query-time). |
| `--exclude-secrets` | — | Deprecated no-op kept for old scripts; exclusion is the default. |
| `--neighbours`, `--neighbors` | `full` | **Retired no-op.** Measured worse than the default on 40 held-out lexical and 40 held-out structural questions; still parses so existing commands do not break, and emits a `retired_flag_ignored` warning when set. See [benchmarks/real/README.md](../benchmarks/real/README.md). |
| `--conditional-expansion` | off | **Retired no-op.** Measured worse than the default on 40 held-out lexical and 40 held-out structural questions; still parses so existing commands do not break, and emits a `retired_flag_ignored` warning when set. See [benchmarks/real/README.md](../benchmarks/real/README.md). |
| `--vectors` / `--no-vectors` | off | `--vectors` fuses the index's dense vectors into the ranking (an error if they are missing or the model does not match); `--no-vectors` forces word matching only. Same meaning as on `rag`. |
| `--embed-model` | the `embed` default | Model used to embed the query for `--vectors`; must match the index. |

## `rag` — pack cited context for an LLM

```bash
repo2graph rag "how does the context pack stay inside its budget" -o .r2g
```

Picks the best matching pieces, follows the arrows out to their neighbours, puts
the repo map on top, and stamps every block with an exact citation header:

```
# Repo map: repo2graph

files: 41  nodes: 644  edges: 2160
languages: python=15, yml=11, md=7, json=2, toml=2, txt=1

## Most depended-on files
- repo2graph/export.py (in=6)
- repo2graph/parse.py (in=6)
...

---

### [cite: repo2graph/cli.py:22-28] `parse_formats` (CALLS out of cmd_build)
# file: repo2graph/cli.py
# function: parse_formats  (lines 22-28, python)
# called by: repo2graph/cli.py::cmd_build, repo2graph/cli.py::cmd_github
# calls (outside the repo): strip, split, sorted, set, SystemExit, join
def parse_formats(spec: str) -> set[str]:
...
```

The `(CALLS out of cmd_build)` part is the *reason* the block is in the pack:
either `seed` (the search found it) or the arrow that dragged it in.

The first argument is optional. Give it an index folder, a source folder to index
on the spot, or a GitHub project, and it works out which you meant:

```bash
repo2graph rag . "where does the CLI parse arguments"       # index this folder first
repo2graph rag psf/requests "how are redirects followed"    # download, index, ask
```

| Flag | Default | What it does |
| --- | --- | --- |
| `-o`, `--out` | `.r2g` | Index folder to read, or to write when a target has to be indexed first. |
| `-k` | `8` | Pieces the text search starts with. |
| `--hops` | `1` | Steps to walk along the arrows. |
| `--budget` | `24000` | Character budget for the **whole** pack. `0` means no budget. |
| `--budget-tokens` | unset | Token budget for the **whole** pack. When given it replaces `--budget` as the unit. |
| `--tokenizer` | `heuristic` | How `--budget-tokens` counts: `heuristic` (4 characters per token), `conservative` (3 per token, over-counts most text) or `tiktoken[:ENCODING]` (exact for that BPE encoding, default `o200k_base`; needs `pip install tiktoken`, which downloads the encoding on first use). The pack's `token_count_method` names the one used. |
| `--token-margin` | `0` | Fill the pack to `--budget-tokens / (1 + FRACTION)`, headroom for a counter that under-counts a model's real tokenizer. Reported as `token_safety_margin`. |
| `--context-budget-chars` | — | Alias of `--budget`: rendered context, in characters. |
| `--context-budget-tokens` | — | Alias of `--budget-tokens`: rendered context, in estimated tokens. |
| `--min-conf`, `--min-confidence` | `1.0` | Drop `CALLS` arrows the parser was less than this sure about. |
| `--vectors` / `--no-vectors` | off | `--vectors` adds meaning-based search on top of the word matching. An error if the index has no vectors, the `rag` extra is missing, or the model does not match. Off unless you ask: turning it on loads a model and downloads ~90 MB the first time. An index that happens to carry vectors is not permission to go and fetch one. |
| `--embed-model` | the `embed` default | Which sentence-transformers model embeds your question for `--vectors`. Must match the one the index was built with. Not `--model`. |
| `--no-expand` | off | Text search only, no arrow walking. |
| `--expansion` | `rag-default` | Which arrows to walk. `navigation`: calls and imports, both ways. `impact-analysis`: callers, importers, subclasses and co-changed files (co-change links files, so use `--hops 2` or more from a function). `strict`: only edges the resolver was certain of, never git history. `rag-default` is the behaviour with no flag. |
| `--edge-type` | preset's | Walk only this edge type (`CALLS`, `IMPORTS`, `INHERITS`, `DEFINES`, `CO_CHANGE`, `TESTS`). Repeatable; overrides the preset's types. Each neighbour's `why` says which edge admitted it. |
| `--format` | `markdown` | `markdown` for the pack, `json` for the pack plus its parts. |
| `--answer` | off | Send the pack to an LLM and stream the answer. [See the warning](#answer-sends-your-code-elsewhere). |
| `--model` | provider default | Override the best-effort default model, only with `--answer`. |
| `--provider` | auto | `gemini`, `openai`, `anthropic` or `ollama`, only with `--answer`. |
| `--include-secrets` | off | Include secret-looking files in the pack (and, for a source-folder target, index them). See below. |
| `--exclude-secrets` | — | Deprecated no-op kept for old scripts; exclusion is the default. |
| `--neighbours`, `--neighbors` | `full` | **Retired no-op.** Measured worse than the default on 40 held-out lexical and 40 held-out structural questions; still parses so existing commands do not break, and emits a `retired_flag_ignored` warning when set. See [benchmarks/real/README.md](../benchmarks/real/README.md). |
| `--conditional-expansion` | off | **Retired no-op.** Measured worse than the default on 40 held-out lexical and 40 held-out structural questions; still parses so existing commands do not break, and emits a `retired_flag_ignored` warning when set. See [benchmarks/real/README.md](../benchmarks/real/README.md). |
| `--precision-first` | off | **Retired no-op.** Measured worse than the default on 40 held-out lexical and 40 held-out structural questions; still parses so existing commands do not break, and emits a `retired_flag_ignored` warning when set. See [benchmarks/real/README.md](../benchmarks/real/README.md). |
| `--secret-policy` | `redact-match` | Inline content secret handling: `redact-match` (default, line-preserving), `exclude-file`, `warn-only`, `off`. |
| `--secret-keyword` | none | Custom substring keyword for secret file matching (repeatable). |
| `--secret-dir` | none | Custom directory name for secret directory matching (repeatable). |

`--format json` gives you `markdown` plus `chunks`, `seeds`, `neighbors`,
`truncated`, `budget_chars`, `used_chars`, `tokens_budget`, `tokens_used` and
`query`, so a program can see what got left out (a compressed neighbour is
described under the examples below):

```bash
repo2graph rag "how does export write the manifest" -o .r2g --format json \
  | jq '{used: .used_chars, budget: .budget_chars, cut: .truncated}'
```

A neighbour that did not fit whole is **compressed** to its header and first
line. Its cite then names the lines it shows, not the whole symbol or file —
``### [cite: pkg/mod.py:12-12] `Foo.bar` [excerpt of 12-40] (CALLS out of run)``
— and its `chunks` record carries `excerpt_of: [12, 40]` with `start_line` /
`end_line` narrowed to match. Where the shown line cannot be mapped back to a
source line (a file's residual, or a later part of a split chunk) the range is
kept and marked `[header and first line only, of 1-648]` instead.

### Secrets at query time

`query` and `rag` leave secret-looking files — dotenv files, `.pem`/`.key`,
keystores, credential stores — out of what they return unless you pass
`--include-secrets` **on that command**. The build-time flag decides what goes
*into* the index; it does not decide what every later reader gets back, so an
index built with `build --include-secrets` still answers a plain `rag` or
`query` without them. (Previously a plain `rag` only excluded them with
`--answer`; `--exclude-secrets` is now a deprecated no-op that prints a warning.)

## `embed` — meaning-based search on top of the words

Word matching misses a piece of code that says the same thing in different words.
`embed` turns every chunk into a vector once, writes it next to the index, and
`query`/`rag` blend the two rankings from then on.

```bash
pip install "repo2graph[rag]"     # sentence-transformers + numpy, optional
repo2graph embed -o .r2g          # writes agent/vectors.npy + vectors.meta.json
repo2graph rag "how is a request routed" -o .r2g --vectors
```

| Flag | Default | What it does |
| --- | --- | --- |
| `-o`, `--out` | `.r2g` | Index folder to embed. |
| `--model`, `--embed-model` | `sentence-transformers/all-MiniLM-L6-v2` | Which model to use. Two spellings for one flag; the Action uses the long one. Append `@<commit>` to pin the Hugging Face revision; the pin is part of the model id stored with the vectors, so the query side names it the same way. |
| `--batch` | `64` | Texts handed to the model per call. |
| `--force` | off | Re-embed everything instead of reusing unchanged chunks' vectors. |
| `--verify-rag` | off | Check the index's vectors, model, dimensions, and chunk coverage to verify that dense retrieval can engage. Reports failures and exits non-zero if the dense path is broken. `rag_extra_installed` is always `true`/`false`, even for an index with no vectors. |

Three things worth knowing:

- **Re-running it is cheap.** A chunk's vector is reused unless the chunk's own
  text changed, so a rebuild after editing one file re-embeds one file's chunks.
  The report says how many: `{"vectors": 412, "reused": 408, "embedded": 4, ...}`.
- **Reading the vectors needs nothing.** `vectors.npy` is a plain NumPy file, but
  repo2graph reads it with the standard library alone. A machine that only
  *queries* a shipped index does not need the `rag` extra — only the machine that
  *creates* the vectors does.
- **A model mismatch is refused, not papered over.** The model name and vector
  width are stored beside the vectors. `--vectors` stops with an error naming both
  sides rather than fusing two models' geometry into a plausible-looking wrong
  ranking. If you embedded with something else, say so on the query side too:
  `repo2graph rag "..." --vectors --embed-model BAAI/bge-small-en`.

## `map` and `stats`

```bash
repo2graph map -o .r2g --viz-nodes 80    # redraw graph.html with fewer dots
repo2graph stats -o .r2g                 # raw stats.json, verbatim (default)
repo2graph stats -o .r2g --format text   # human-readable quality summary
```

`stats` prints `agent/stats.json` verbatim by default — that has always been the
default, and `--json` is just an explicit way to ask for it. Pass `--format text`
for a formatted summary of the same counts, covering:
- **Calls resolution breakdown**: `calls_scoped` (resolved within class/file/imports), `calls_unique_global`, `calls_ambiguous`, `calls_untyped_receiver` (the subset of ambiguous calls that are builtin method names on an untyped receiver), and `calls_external`.
- **Inheritance metrics**: `unresolved_bases` counting base classes that could not be mapped to an indexed class node.
- **Import resolution**: `imports_resolved` vs `imports_unresolved`.
- **Parsing health**: total files, symbols, chunks, and any `parse_errors` encountered.

## The two `--budget` flags count different things

This surprises people, so it is worth saying plainly. Both commands take
`--budget`, and each means what its own job needs:

- **`query --budget`** bounds the code itself: the sum of the `text` of the pieces
  it hands back. Headers and formatting are not charged.
- **`rag --budget`** bounds the finished markdown: the map on top, the `---`
  separator, every `### [cite: ...]` header and the blank lines between blocks all
  come out of the same budget.

So the same number gives you less code from `rag` than from `query`. That is on
purpose: `query`'s accounting is what it has always done and programs depend on
it, while `rag` has to promise an LLM that the thing it is handed fits.

The longer names say which stage and which unit each one caps:
`query --retrieve-budget-chars`, `rag --context-budget-chars` and
`rag --context-budget-tokens`. The short names keep working.

```
retrieve ──► expand ──► pack ──────────────► clamp (MCP only)
   │                     │                       │
   query --budget        rag --budget /          12k-token ceiling,
   (chunk text, chars)   --budget-tokens         re-measured after
                         (rendered markdown)     rendering
```

`rag --format json` and `Index.pack_context()` report both sides so nothing has to
be inferred from flag names:

| Field | Meaning |
|---|---|
| `source_text_chars` | Characters of chunk text selected. |
| `rendered_context_chars` | Characters actually rendered (`len(markdown)`); larger, because headers and the map count. |
| `tokens_used` | Tokens of the rendered markdown, by `token_count_method`. |
| `token_count_method` | `heuristic` (4 characters per token) unless you passed your own `count_tokens`. An estimate: dense code and CJK run higher. |
| `budget_exhausted` | Something was cut to fit. |
| `omitted_chunk_count` | Candidate chunks left out entirely. |

## Conventions

Every command follows these, so a flag you have not used yet behaves the way the
others do:

- **`0` means unlimited** for every numeric ceiling (`--max-files`, `--max-nodes`,
  `--max-edges`, `--max-bytes`, `--max-chunks`, `--max-memory-mb`,
  `--max-build-seconds`, the budgets). Three flags give `0` a different job, and
  their help says so: `--jobs 0` is one worker per CPU, `github --depth 0` is full
  history, and `--viz-nodes 0` draws an empty map (`--viz-nodes all` is the
  unlimited spelling).
- **Units are part of the meaning.** Budgets are characters unless the flag says
  tokens; `--max-file-mb` is megabytes; `--max-bytes` is bytes; `--lock-timeout`
  and `--max-build-seconds` are seconds. Numbers must be finite and `>= 0`.
- **Lists are repeatable flags** (`--secret-keyword a --secret-keyword b`), except
  `--formats`, which takes one comma-separated value.
- **A renamed flag keeps its old name** as an alias, so scripts do not break.

## How retrieval works

1. **Text search first.** BM25, the standard word-matching score, with one twist:
   if a word in your question is exactly the name of a function or class, that
   piece's score is multiplied. Asking about `parse_formats` finds
   `parse_formats`, not the prose that happens to mention it.
2. **Optional fusion.** Run `repo2graph embed` (or bring your own vectors) and
   `Index.score_rrf()` blends the two rankings with reciprocal rank fusion. No
   extra library is needed to *read* the vectors, and with no vectors it is
   exactly plain BM25.
3. **Then the arrows, by direction.** Expansion is not "everything one step
   away". It follows `CALLS` both ways (what this calls, and what calls it),
   `DEFINES` inwards (the file or function that holds this one), `INHERITS`
   outwards (the base classes) and `IMPORTS` outwards (the modules it borrows
   from).
4. **Then the budget.** Blocks are added best-first until the budget is used up.

<a id="answer-sends-your-code-elsewhere"></a>

## ⚠️ `--answer` sends your code to someone else's computer

`repo2graph rag --answer` is the one command in this project that touches the
network with your source in it. Read this before you use it.

```bash
repo2graph rag "how does session auth work?" -o .r2g --answer --provider openai
```

It POSTs the assembled pack — **real file content from your repository** — to an
LLM provider over HTTPS, and streams the grounded answer back to stdout.

- **It is opt-in and nothing else does it.** The provider code is only imported
  when `--answer` is present. A plain `rag`, a `query` or a `build` makes no DNS
  lookup and opens no socket.
- **It tells you before it sends.** Before the first byte leaves, it prints the
  provider name, the hostname and how many characters are going, to stderr:

  ```
  repo2graph: sending 18423 chars of repository context to provider openai at api.openai.com (selected by OPENAI_API_KEY)
  ```

- **Secret-ish files are dropped from the pack** (with or without `--answer`, unless you pass `--include-secrets`). Dotfiles,
  `.env`, `.pem`, `.key`, keystores and friends are excluded. This is a guard, not
  a guarantee: a secret pasted into an ordinary `.py` file is still ordinary
  source and still goes.
- **Pick the provider deliberately.** With no `--provider`, the one of
  `GEMINI_API_KEY` (or `GOOGLE_API_KEY`), `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
  `OLLAMA_HOST` that is set is used. If several are set it stops and asks for
  `--provider` rather than guess where your code goes. `--provider ollama` with
  `OLLAMA_HOST` pointed at your own machine keeps everything local; a plain-HTTP
  `OLLAMA_HOST` on another host gets a warning.
- **Every request is bounded.** 30 s to connect, 300 s between bytes, 600 s and
  8 MiB for the whole stream, 200,000 characters of answer text. A bound that cuts
  an answer short says so on stderr and makes `rag` exit with status 3, so a
  script cannot mistake a partial answer for a whole one. Provider error bodies
  are echoed with the key and anything secret-shaped redacted.
- **Zero SDKs.** All four providers are spoken to with the standard library's
  `urllib`. Nothing extra to install, and nothing extra with an opinion about your
  credentials.

Default models are best-effort cheap/fast ids (`gemini-3.6-flash`, `gpt-4o-mini`,
`claude-haiku-5-5` and `llama3.1`); pass `--model` to override. On
`claude-haiku-5-5` thinking is turned off, since thinking tokens would come out
of the 2,048-token answer. `--answer` also refuses a pack that leaves no room for
that answer in a default model's context window.

## `index-status` — is this index current, and what is in it?

```bash
repo2graph index-status [-o DIR] [-r REPO] [--json] [--check]
```

Joins `manifest.json`, `stats.json` and the working tree into one report:
the indexed commit and branch, when the index was built, file/symbol/edge
counts, detected languages, what discovery skipped and why, parse failures,
the index's size on disk, and whether the tree has moved since the build.

| Flag | Default | Meaning |
|---|---|---|
| `-o`, `--out DIR` | `.r2g` | The index directory |
| `-r`, `--repo PATH` | the index's parent | The source tree, when the index lives elsewhere |
| `--json` | off | The full report as JSON; every key is public |
| `--check` | off | Exit `1` when freshness is not `current` |

`--check` turns "the committed index matches this commit" into a CI gate:

```yaml
- run: repo2graph index-status -o .r2g --check
```

Freshness is `current`, `stale`, or `unknown` — the last meaning nothing
could be checked (no `index.state.json`, not a git checkout, or past the
50,000-file scan bound), which is deliberately not reported as staleness.

Related: `stats` reports retrieval *quality* (call-resolution tiers,
unresolved imports); `doctor` reports whether anything is *broken*. Full
detail, including the determinism guarantees: **[docs/architecture.md](architecture.md)**.

## `bug-report` — a bundle that is safe to paste into a public issue

```bash
repo2graph bug-report [-o DIR] [-r REPO] [--category CAT] [--include-paths] [--json] [--write FILE]
```

Assembles environment and versions, the non-passing `doctor` checks, the
index's shape and freshness, and an edge-quality histogram (counts by type, by
extraction method, and by confidence bucket) — which is often what explains a
bad answer, since a graph dominated by ambiguous name matches behaves
differently from one that resolves cleanly.

| Flag | Meaning |
|---|---|
| `--category CAT` | One of `incorrect-relationship`, `missing-relationship`, `stale-index`, `parser-failure`, `answer-unhelpful`. Adds a checklist of the extra evidence that category needs. |
| `--include-paths` | Include repo-relative file paths (off by default) |
| `--json` | The raw bundle instead of the markdown |
| `--write FILE` | Write to a file instead of stdout |

**No file content is included, at any setting** — not a chunk body, not a line
of source. Nor are environment variable values, the git remote URL, the
repository or branch name, or absolute paths. File paths are off by default
(a path like `billing/stripe_migration_v2.py` says a lot about a private
repository); changed files appear as 8-character fingerprints so a maintainer
can tell two entries apart without learning what they are.

The commit sha *is* included: it makes a wrong edge reproducible against a
public repo and reveals nothing a private repo's own history does not.

Full contents and the reasoning: **[below](#bug-report--a-bundle-that-is-safe-to-paste-into-a-public-issue)**.

## `doctor` — diagnose the environment and artifacts

```bash
repo2graph doctor [path] [--json]
```

Inspects the runtime environment and, if one is present, the index's artifacts.
`[path]` may be either a repository or an index directory.

**Environment**

- **Python Version**: checks that Python is >= 3.10.
- **Platform Support**: checks operating system, CPU architecture, and C runtime (glibc >= 2.34 on Linux) compatibility.
- **Platform Encoding**: reports the default, preferred and stdout encodings,
  which is what distinguishes a genuine parse failure from a cp1252 console
  mangling the output.
- **Git CLI**: verifies `git` is on `PATH` and reports its version. Absent git
  is a warning, not a failure — discovery falls back to a directory walk, but
  `CO_CHANGE` edges and `--git-history` need it.
- **Tree-sitter Grammars**: loads the language table and reports how many
  grammars are configured.
- **MCP Server**: runs the same SDK preflight `repo2graph-mcp` runs at startup —
  is the `mcp` SDK installed, importable, and on a supported major — and reports
  whether the `repo2graph-mcp` launcher is on `PATH`. A client shows these
  failures only as "server failed to start" or an empty tool list, so this is the
  one place they are legible. A missing SDK is a warning, not a failure: the CLI
  and the Action never import it. It does **not** read your client's
  configuration files; those belong to other tools and `doctor --json` is meant
  to be safe to paste into a bug report.
- **Permissions**: verifies the target directory can be created and written to,
  cleaning up anything it had to create to find out.

**The index**

- **Artifact Integrity**: when an index is present, validates `manifest.json`
  and verifies the artifact checksums. Reports `corrupt` or incomplete
  artifacts with the rebuild command.

Freshness is deliberately not a `doctor` check — `repo2graph index-status`
owns that answer, so the two cannot drift into disagreeing. See
[`index-status`](#index-status--is-this-index-current-and-what-is-in-it).

Pass `--json` for machine-readable output suitable for CI or automation.
Exits `0` when every check passes or only warns, `1` when a check fails
outright — so `repo2graph doctor . --json` is safe to attach to a bug report
and safe to gate a pipeline on.

## `explain-path` — explain file inclusion or exclusion

```bash
repo2graph explain-path <path> [-r REPO] [-o OUT] [--include GLOB] [--exclude GLOB]
                         [--exclude-group NAME] [--include-vendor] [--include-secrets]
                         [--json]
```

Evaluates one path against the same rules `build`'s discovery uses, and reports
the single rule that decided it — not a trace of every rule that was checked.
`<path>` is relative to `-r`/`--repo` (default: the current directory) or
absolute; `--include`/`--exclude` are each repeatable, one glob per occurrence.
`-o`/`--out` (default `.r2g`, resolved exactly as `build`'s) names the output
directory a build would write to: discovery never indexes it, nor any directory
holding a repo2graph `agent/manifest.json` (an earlier build's index), and
`explain-path` reports those as `output_dir` / `index_dir` at step 2 — so
`explain-path .r2g/agent/nodes.jsonl` says EXCLUDED, as the build behaves. It
never opens the index. It also takes no size flags, so it cannot explain a build that used them: the size check
below is evaluated against the 1.5 MB `--max-file-mb` default with
`--chunk-large-files` off, whatever the build was actually run with, unless the
repo's [config file](#config-file) sets them. A decision made by a config-file
setting names that file in `reason` and in an extra `source` field.

```bash
$ repo2graph explain-path repo2graph/cli.py
Path:            E:\Github\repo2graph\repo2graph\cli.py
Relative Path:   repo2graph/cli.py
Decision:        INCLUDED
Precedence Step: 10
Rule:            included
Reason:          Path passed all exclusion checks and is eligible for indexing

$ repo2graph explain-path .git/config
Path:            E:\Github\repo2graph\.git\config
Relative Path:   .git/config
Decision:        EXCLUDED
Precedence Step: 2
Rule:            skip_dir
Reason:          Path component '.git' is in excluded dot-directory filter (DEFAULT_SKIP_DIRS/--exclude-dir)
```

`--json` returns the same facts as data: `path`, `relative_path`, `included`,
`rule`, `reason`, `precedence_step`.

### Precedence order

`explain_path` (`repo2graph/parse.py`) checks rules in this order and stops at
the first match:

| Step | Rule | What it means |
| --- | --- | --- |
| 0 | `outside_root` | The path resolves outside `-r`/`--repo`. |
| 1 | `not_found` | The path does not exist on disk. |
| 2 | `skip_dir` | A path component is a dot-directory, or is in `DEFAULT_SKIP_DIRS` / `--exclude-dir` (`vendor/` only counts here when `--include-vendor` is off). |
| 3 | `internal_lock` | The path is a sibling `.*.r2glock` build-lock file. |
| 4 | `gitignore` | `.gitignore` excludes it, checked with `git check-ignore` — only when `-r` is a git checkout. |
| 5 | `non_regular_file` (or `stat_error`) | Not a regular file — a directory, symlink, device or FIFO — or `lstat` itself failed. |
| 6 | `too_large` | Bigger than `--max-file-mb` (default 1.5 MB) and `--chunk-large-files` is off. |
| 7 | `secret_file` | Matches a secret/credential path pattern and `--include-secrets` is off. |
| 8 | `not_included` | `--include` globs were given and the path matches none of them. |
| 9 | `exclude_glob` | The path matches an `--exclude` glob. |
| 10 | `binary` or `included` | A null byte in the first 4 KB marks it binary; otherwise every check passed. |

Step 10 covers both outcomes of the last check — `rule` (`binary` vs. `included`)
tells them apart, `precedence_step` is `10` either way.


## `explain` — graph and retrieval inspection

Explain connections, node properties, and retrieval decisions:

```bash
# Explain relationship between two nodes
repo2graph explain edge "file:src/main.py" "file:src/util.py" -o .r2g

# Inspect node metadata and incoming/outgoing edges
repo2graph explain node "sym:src/main.py::Runner.run" -o .r2g

# Trace retrieval ranking, candidate seeds, and graph expansion
repo2graph explain retrieval "how does authentication work" -o .r2g -k 8 --hops 1
```

All explain subcommands support `--json` for machine-readable output. `explain
retrieval` defaults to `-k 8`, the same as `rag` and `query`, so it traces the
retrieval they actually run; `--min-conf` is accepted as an alias of
`--min-confidence`. Like `rag` and `query`, `explain retrieval` leaves
secret-looking paths (`.env`, keys, credentials) out of the trace -- they are
neither listed as candidates, walked to, nor retrieved, and the text report
counts how many were hidden. `--include-secrets` opts back in;
`--secret-keyword KEYWORD` and `--secret-dir DIR` (repeatable) extend the rule.


## `completion` — shell tab completion

Prints shell completion configuration for `bash`, `zsh`, or `fish`.
Tab completion relies on `argcomplete`, available via the `completion` extra:

```bash
pip install "repo2graph[completion]"
```

### Setup

**Bash:**
```bash
eval "$(repo2graph completion bash)"
# or: eval "$(register-python-argcomplete repo2graph)"
```

**Zsh:**
```zsh
autoload -U bashcompinit && bashcompinit
eval "$(repo2graph completion zsh)"
```

**Fish:**
```fish
repo2graph completion fish | source
```

## In CI: the GitHub Action

repo2graph is published on the GitHub Marketplace, so it is one step in any
workflow.

```yaml
- uses: actions/checkout@v4
  with: { fetch-depth: 0 }   # full history, so CO_CHANGE edges are meaningful
- uses: Srinivasan-78/repo2graph@v3
  with:
    path: .              # or: repo: some-org/other-repo
    git-history: "500"
    artifact-name: repo-graph
```

`@v3` follows every 3.x release. Pin an exact version (`@v3.0.0`) if you would
rather upgrade by hand.

The action never calls an LLM: `--answer` is deliberately not exposed. It packs
the context and leaves the answering to whatever reads the pack afterwards.

It also writes a structured summary into the job summary page, so the shape of
the map shows up in the run without downloading anything. The summary is built
by a small stdlib-only script (`.github/scripts/summary.py`) straight from the
build's own artifacts — it never truncates a large repo the way printing the
first N lines of a file would. It has:

- an at-a-glance table: files indexed, functions, classes, total edges,
  languages found, chunks, and the short commit SHA the build ran at (omitted
  if the checkout isn't a git repo);
- a "Top 5 hub files" table, ranked by in-degree across every edge type;
- a "CO_CHANGE hotspots" table of the most frequently co-changed file pairs
  (omitted when `git-history` is `0` or no pair crosses the co-change
  threshold);
- a "Graph delta" section condensed from `human/CHANGELOG.md` when a previous
  build's CHANGELOG is present next to this one — just the new/removed node
  and edge counts and the new-hotspot lines, not the full item lists.

Any piece it can't compute (a missing field, a non-git checkout, no
CHANGELOG.md) degrades to "N/A" or an omitted row/section rather than failing
the step.

### Inputs

| Input | Default | What it does |
| --- | --- | --- |
| `repo` | `""` | Map a different project: `owner/repo` or a GitHub URL. Leave blank to map the checked-out one. |
| `path` | `.` | Folder in the workspace to map, used when `repo` is blank. |
| `ref` | `""` | Branch or tag to map, used with `repo`. Blank means the default branch. |
| `out` | `.r2g` | Where the map is written. |
| `formats` | `jsonl,graphml,cypher,overview,html` | Which files to write. Drop the ones you do not need to save time. |
| `git-history` | `0` | Commits to read for `CO_CHANGE` arrows. `0` skips it. Needs `fetch-depth: 0`. |
| `include` | `""` | Space-separated globs to keep, e.g. `"src/**"`. |
| `exclude` | `""` | Space-separated globs to skip, e.g. `"**/test/** vendor/**"`. |
| `query` | `""` | Also pack a cited GraphRAG context for this question. Blank skips it. |
| `query-k` | `8` | Pieces the text search starts with. |
| `query-hops` | `1` | Steps to walk along the arrows. |
| `query-budget` | `24000` | Character budget for the whole pack, map and cite headers included. |
| `query-budget-tokens` | `""` | Token budget for the whole pack. Set it and it replaces `query-budget` as the unit. Blank keeps the character budget. |
| `query-min-conf` | `1.0` | Drop `CALLS` arrows below this confidence. |
| `query-format` | `markdown` | `markdown` or `json`. |
| `query-out` | `""` | File to write the pack to. Blank means `<out>/agent/pack.md` (or `pack.json`). |
| `embed` | `false` | Also embed the chunks for meaning-based search. Installs the `rag` extra and downloads a model, so it is off by default. When `true` the pack is packed with `--vectors`. |
| `embed-model` | `""` | sentence-transformers model for `embed`. Blank uses the built-in default. Both the embed step and the pack step get this model, so the two always agree. |
| `artifact-name` | `repo-graph` | Upload the map under this name. Blank uploads nothing. |
| `commit-branch` | `""` | Push the map to this orphan branch. Blank pushes nothing. |
| `commit-force` | `false` | Whether to force-push when pushing to `commit-branch`. Force-push is only permitted for dedicated graph branches (branches containing 'graph'). |
| `token` | `""` | Token that can read `repo` when the target is private. Note: cross-repo indexing stores the target repository's graph and chunk excerpts in the caller's workflow artifacts. |
| `version` | `""` | pip spec to install repo2graph from, e.g. `repo2graph==3.0.0`. Blank installs the action checkout you pinned with `uses:`, which is what every run did before. |
| `include-secrets` | `false` | Set to `true` to index secret/credential files. By default, sensitive files (.env, keys, certs) are excluded. |
| `secret-policy` | `redact-match` | Policy for inline content secrets: `redact-match`, `exclude-file`, `warn-only`, `off`. |
| `incremental` | `false` | Set to `true` to enable incremental graph builds using the parse cache. |
| `parse-policy` | `best-effort` | Policy for AST parse errors: `best-effort`, `warn`, `strict`. |
| `max-call-candidates` | `5` | Maximum call edge candidates to retain per ambiguous call site. |
| `max-chunks` | `""` | Maximum chunks to build (`0` or blank = no limit). |
| `max-nodes` | `""` | Maximum graph nodes to retain (`0` or blank = no limit). |
| `max-total-bytes` | `""` | Maximum total source bytes to index (`0` or blank = no limit). |
| `max-build-seconds` | `""` | Maximum build duration in seconds before limit policy triggers (`0` or blank = no limit). |
| `limit-policy` | `fail` | Policy when graph limits are reached: `fail`, `truncate`, `warn`. |
| `secret-keywords` | `""` | Additional keywords to exclude as secret file/path (space- or newline-separated). |
| `secret-dirs` | `""` | Additional directory names to exclude as secret path (space- or newline-separated). |
| `smoke-check` | `true` | Fail build if discovery is not `git` or symbol count is 0 (`true` or `false`). |

### Pinning the package instead of the checkout

By default the action installs itself — the source that came with the `uses:`
ref — so the action and the package can never disagree. Set `version` to install
from PyPI instead, which is the artifact `publish.yml` builds under Trusted
Publishing with attested provenance:

```yaml
- uses: Srinivasan-78/repo2graph@v3
  with:
    version: repo2graph==3.0.0
```

Any pip spec works (`repo2graph>=1.4,<2`, a `git+https://…@<ref>` URL, a local
wheel path). It is passed to `pip install` verbatim, so a bad spec fails the step
loudly rather than falling back to the checkout.

This input used to be accepted and ignored: the install always gated on a
`pyproject.toml` that a composite action always has, so the editable install of
the checkout won every time. If you were already setting `version`, you were
getting the checkout — you now get what you asked for.

### Outputs

| Output | What it holds |
| --- | --- |
| `out` | The output folder: `human/` (`overview.md`, `graph.html`, …) and `agent/` (`chunks.jsonl`, …). |
| `nodes` | How many dots the map has. |
| `edges` | How many arrows. |
| `chunks` | How many code pieces were cut. |
| `pack-file` | Path to the GraphRAG pack written for `query`. Empty when `query` is blank. |
| `pack-chars` | How long that pack is, in characters. `0` when `query` is blank. |

### Permissions & Least Privilege

Follow the principle of least privilege:
- **Default usage (read-only):** When building and uploading artifacts, the action requires only read access to repository contents:
  ```yaml
  permissions:
    contents: read
  ```
- **Branch publishing:** Write permissions are **only** needed if you configure `commit-branch`:
  ```yaml
  permissions:
    contents: write
  ```

### Security Guardrails & Best Practices

### 1. Never use `commit-branch` or `contents: write` on untrusted Pull Requests

Granting `contents: write` to workflows triggered by `pull_request` (or worse, `pull_request_target`) exposes your repository to unauthorized branch updates or token extraction:
- The action automatically **refuses to push** if it detects execution inside a pull request originating from a fork repository (`github.event.pull_request.head.repo.fork == true`).
- For pull requests, always prefer `artifact-name: repo-graph` to inspect artifacts via GitHub Actions summary and artifact downloads without write permissions.

#### Insecure Example (DO NOT USE)
```yaml
# INSECURE: Grants write token on untrusted pull requests and force-pushes
name: Unsafe PR Graph
on: pull_request_target  # DANGEROUS with write permissions!
permissions:
  contents: write
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: Srinivasan-78/repo2graph@v3
        with:
          commit-branch: graph  # DANGEROUS: untrusted code can trigger branch push
```

#### Secure Recommended Example
```yaml
# SECURE: Read-only on PRs; uploads artifacts for inspection
name: Pull Request Graph
on:
  pull_request:
    branches: [main]
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: Srinivasan-78/repo2graph@v3
        with:
          artifact-name: pr-graph
          # commit-branch omitted! No write access required
```

### 2. Use a dedicated orphan branch, never a protected branch

Never point `commit-branch` at a primary or protected branch (e.g. `main`, `develop`, `master`):
- Point it at a dedicated orphan branch (e.g. `graph`, `repo-graph`, or `docs/graph`).
- Configure branch protection rules on your repository to prevent accidental pushes to protected branches.

### 3. Safe Force-Pushes with `commit-force`

By default, `commit-branch` creates an orphan branch with a single root commit and uses `--force` (`commit-force: true`) so the graph branch remains clean and minimal.
If your compliance or security policy disallows force pushes, set `commit-force: false` to require standard fast-forward pushes:
```yaml
- uses: Srinivasan-78/repo2graph@v3
  with:
    commit-branch: graph
    commit-force: "false"
```

### 4. Handling Private Repositories

When indexing private remote repositories via `repo`:
- Never commit personal access tokens in workflow files or CLI arguments.
- Pass repository secrets via the `token` input:
  ```yaml
  - uses: Srinivasan-78/repo2graph@v3
    with:
      repo: my-org/private-repo
      token: ${{ secrets.READ_ONLY_REPO_PAT }}
  ```
- Use fine-grained Personal Access Tokens (PATs) scoped to **read-only contents** on the specific target repository.

### Keep a fresh map next to your own code

`.github/workflows/self-index.yml` is the copy this project runs on itself, on
every push to `main` and once a week:

```yaml
name: Index this repository
on:
  push:
    branches: [main]
  schedule:
    - cron: "0 4 * * 1"
  workflow_dispatch:

permissions:
  contents: write # Note for copiers: only needed if pushing to commit-branch; use contents: read if only uploading artifacts

jobs:
  index:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: Srinivasan-78/repo2graph@v3
        with:
          path: .
          git-history: "500"
          artifact-name: repo-graph
          commit-branch: graph   # drop this line to only publish an artifact
```

With `commit-branch: graph`, an AI pipeline can always grab an up-to-date copy of
the code pieces with one request:

```bash
curl -sL https://raw.githubusercontent.com/Srinivasan-78/repo2graph/graph/agent/chunks.jsonl -o chunks.jsonl
```

### Map any project from the Actions tab

`.github/workflows/index-repo.yml` is a button you press. Type a project name, get
a map back. It downloads the project, builds the map, prints the summary into the
job page, and uploads `graph-<owner>__<repo>` as a file you can download. Set
`publish_release: true` and it also attaches a zip to a GitHub Release.

The download includes `graph.html`, so opening that one file gives you the picture
with nothing installed.

Inputs: `repo`, `ref`, `git_history`, `formats`, `exclude`, `publish_release`. For
a private project, add a `TARGET_REPO_TOKEN` secret that can read it. Otherwise
the job's own token is used.

Cross-repo use stores the *target's* source, not just the caller's: the graph
(and, with `--include-secrets` off, redacted source excerpts) ends up in
*this* repository's workflow artifacts or release, not the target's. Only
grant `TARGET_REPO_TOKEN` read access to repositories you are allowed to
re-host copies of in this way, and treat this repo's artifacts/releases
accordingly.

All from the terminal:

```bash
gh workflow run index-repo.yml -f repo=psf/requests
gh run watch
gh run download --name graph-psf__requests --dir ./graph
```
