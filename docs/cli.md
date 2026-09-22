# CLI reference

Every flag `repo2graph` takes, and what it actually counts. See the
[README](../README.md) for the five-minute version.

```
repo2graph build   <repo>    parse a folder into a graph + RAG chunks
repo2graph github  <repo>    clone a GitHub project, then build
repo2graph query   <question>  fast local search over a built index
repo2graph rag     [target] <question>  pack a cited context for an LLM
repo2graph embed             add meaning-based search to an index
repo2graph map               redraw graph.html from a built index
repo2graph stats             print the index counts
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

## `build` — make the map

```bash
repo2graph build /path/to/project -o .r2g --git-history 200
```

| Flag | Default | What it does |
| --- | --- | --- |
| `-o`, `--out` | `.r2g` | Where the map is written. |
| `--formats` | `jsonl,graphml,cypher,overview,html` | Which artifacts to write. Drop what you do not need to save time. |
| `--include` | none | Glob(s) to keep, e.g. `'**/*.py'`. |
| `--exclude` | none | Glob(s) to skip, e.g. `'**/test/**'`. |
| `--parse-policy` | `best-effort` | AST error handling policy: `best-effort` (log and continue), `warn` (emit stderr warnings), `strict` (fail build on syntax error). |
| `--git-history` | `0` | Commits to read for `CO_CHANGE` arrows. Capped at 5000. |
| `--max-files` | `0` (all) | Stop after N files, for very large projects. |
| `--jobs` | `0` (auto) | Parallel workers. Auto means one per core, up to 8. |
| `--viz-nodes` | `300` | Node cap in `graph.html`. `0` draws an empty graph; `all` draws every node. |
| `--no-chunks` | off | Skip the retrieval chunks entirely. |
| `--max-file-mb` | `1.5` | Files larger than this are skipped (or chunked). Minimum is 0.1 MB. |
| `--include-vendor` | off | Index files inside `vendor/` directories (skipped by default). |
| `--exclude-dir` | none | Additional directory name to skip. Repeatable (e.g. `--exclude-dir generated --exclude-dir tmp`). |
| `--chunk-large-files` | off | Instead of skipping, split files larger than `--max-file-mb` into parseable chunks. |
| `--incremental` | off | Reuse parse results for files whose content hash is unchanged. |
| `--include-secrets` | off | Explicitly opt in to indexing secret/credential files (excluded by default). |
| `--secret-policy` | `redact-match` | Inline content secret handling: `redact-match` (default, line-preserving), `exclude-file`, `warn-only`, `off`. |
| `--secret-keyword` | none | Custom substring keyword for secret file matching (repeatable). |
| `--secret-dir` | none | Custom directory name for secret directory matching (repeatable). |
| `--allow-symlink-out` | off | Allow `-o` to point through a symbolic link. Off by default to prevent accidental writes outside the repo tree. |
| `--force` | off | Allow overwriting an existing directory that was not created by repo2graph. Without this flag, build refuses to write into any non-empty directory that does not contain a recognised index. |
| `--lock-timeout` | `60` | Seconds to wait for the per-output-directory build lock before failing. Increase this when several CI jobs share the same network-mounted output path. |

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

**When a full rebuild is still required.** The cache is keyed on file content, so
it cannot see a change in how content is *interpreted*. Rerun without the flag
after upgrading repo2graph, after a `tree-sitter-language-pack` upgrade that
changes a grammar, or if you ever suspect the cache. Doing so costs only time —
a full build overwrites the cache and puts you back on a known-good footing.
Cache entries written by a different cache format are ignored automatically, as
is a cache that is missing, unreadable or corrupt; each of those degrades to a
full build rather than to a wrong one.

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
| `--min-conf` | off | Drop `CALLS` arrows below this confidence. |
| `--format` | `text` | `text` or `json`. `--json` is the old spelling of `--format json`. |

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
| `--min-conf` | `1.0` | Drop `CALLS` arrows the parser was less than this sure about. |
| `--vectors` / `--no-vectors` | off | `--vectors` adds meaning-based search on top of the word matching. An error if the index has no vectors, the `rag` extra is missing, or the model does not match. Off unless you ask: turning it on loads a model and downloads ~90 MB the first time. An index that happens to carry vectors is not permission to go and fetch one. |
| `--embed-model` | the `embed` default | Which sentence-transformers model embeds your question for `--vectors`. Must match the one the index was built with. Not `--model`. |
| `--no-expand` | off | Text search only, no arrow walking. |
| `--format` | `markdown` | `markdown` for the pack, `json` for the pack plus its parts. |
| `--answer` | off | Send the pack to an LLM and stream the answer. [See the warning](#answer-sends-your-code-elsewhere). |
| `--model` | provider default | Override the best-effort default model, only with `--answer`. |
| `--provider` | auto | `gemini`, `openai`, `anthropic` or `ollama`, only with `--answer`. |

`--format json` gives you `markdown` plus `chunks`, `seeds`, `neighbors`,
`truncated`, `budget_chars`, `used_chars`, `tokens_budget`, `tokens_used` and
`query`, so a program can see what got left out:

```bash
repo2graph rag "how does export write the manifest" -o .r2g --format json \
  | jq '{used: .used_chars, budget: .budget_chars, cut: .truncated}'
```

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
| `--model`, `--embed-model` | `sentence-transformers/all-MiniLM-L6-v2` | Which model to use. Two spellings for one flag; the Action uses the long one. |
| `--batch` | `64` | Texts handed to the model per call. |
| `--force` | off | Re-embed everything instead of reusing unchanged chunks' vectors. |
| `--verify-rag` | off | Check the index's vectors, model, dimensions, and chunk coverage to verify that dense retrieval can engage. Reports failures and exits non-zero if the dense path is broken. |

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
- **Calls resolution breakdown**: `calls_scoped` (resolved within class/file/imports), `calls_unique_global`, `calls_ambiguous`, and `calls_external`.
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

- **Secret-ish files are dropped from the pack when `--answer` is on.** Dotfiles,
  `.env`, `.pem`, `.key`, keystores and friends are excluded. This is a guard, not
  a guarantee: a secret pasted into an ordinary `.py` file is still ordinary
  source and still goes.
- **Pick the provider deliberately.** With no `--provider`, the first of
  `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OLLAMA_HOST` that is
  set wins. If several are set you may not be sending where you think.
  `--provider ollama` with `OLLAMA_HOST` pointed at your own machine keeps
  everything local.
- **Zero SDKs.** All four providers are spoken to with the standard library's
  `urllib`. Nothing extra to install, and nothing extra with an opinion about your
  credentials.

Default models are best-effort cheap/fast ids (`gemini-3.6-flash`, `gpt-4o-mini`,
`claude-haiku-4-5` and `llama3.1`); pass `--model` to override.

## `doctor` — diagnose the environment and artifacts

```bash
repo2graph doctor [path] [--json]
```

Inspects the runtime environment and index directory for common configuration,
permission, dependency, or artifact integrity issues:

- **Python version**: checks that Python is >= 3.10.
- **Tree-sitter & grammars**: checks that `tree-sitter` and `tree-sitter-language-pack` are installed and verifies all supported language grammars.
- **Git integration**: verifies `git` executable availability and non-ASCII path support.
- **Directory permissions**: verifies write permissions in the target directory.
- **Artifact integrity**: validates `manifest.json`, `chunks.jsonl`, `nodes.jsonl`, and `edges.jsonl` if an index exists.
- **Dense vector integrity**: checks `vectors.npy` and `vectors.meta.json` correspondence with `chunks.jsonl`.
- **MCP SDK**: verifies installed `mcp` version compatibility.
- **LLM providers**: checks if provider environment variables are configured without ever disclosing the secret values.
- **Platform encoding**: checks console and filesystem encoding to detect potential charmap limitations.

Pass `--json` for machine-readable JSON output suitable for CI or automation. Exits with code 0 if all checks pass, or 1 if any critical check fails.

## `explain-path` — explain file inclusion or exclusion

```bash
repo2graph explain-path <path> [-r REPO] [--include GLOB] [--exclude GLOB]
                         [--include-vendor] [--include-secrets] [--json]
```

Evaluates one path against the same rules `build`'s discovery uses, and reports
the single rule that decided it — not a trace of every rule that was checked.
`<path>` is relative to `-r`/`--repo` (default: the current directory) or
absolute; `--include`/`--exclude` are each repeatable, one glob per occurrence.
There is no `-o`/`--out` — `explain-path` never opens an index. It also takes
no size flags, so it cannot explain a build that used them: the size check
below is always evaluated against the 1.5 MB `--max-file-mb` default with
`--chunk-large-files` off, whatever the build was actually run with.

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
repo2graph explain retrieval "how does authentication work" -o .r2g -k 5 --hops 1
```

All explain subcommands support `--json` for machine-readable output.


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
