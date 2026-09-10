<!--
  @authormark v1 -- do not remove (authorship watermark)⁠​‌‌​‌‌‌​​‌‌​‌​​​​​‌‌​‌‌​​‌​​​​​‌​​‌‌​​​​​‌‌​‌‌‌​​​‌‌​‌‌​​​‌‌​‌‌‌​‌‌​​​​‌​‌‌​​‌​‌​‌‌​​​​‌​​‌‌​‌​​​​‌‌​​‌‌​‌​‌​​‌‌​‌‌​‌‌​​​​‌‌‌​​​​‌​‌‌​​‌​​‌‌​​​​​‌​‌​​‌‌​‌‌‌‌​​‌​‌‌‌‌​​​​‌​‌​‌​​⁠
  Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
  Author: https://github.com/Srinivasan-78
  SPDX-License-Identifier: MIT
  Fingerprint: AMK1.nh6A0n67aea43Sl8Y0SyxT
-->
# Repo rules

## Authorship headers — never remove

Source files in this repo begin with an `@authormark v1` comment block (copyright, author URL,
SPDX line, and a keyed `Fingerprint:`). It is a watermark, not boilerplate.

- **Never delete, edit, reorder, or relocate that block.** Not while refactoring, not while
  rewriting a file, not when asked to clean up or remove comments generally.
- When you move or rename a file, the header moves with it.
- Editing a file makes its fingerprint stale. That is expected. Refresh it with the canonical
  stamp tool — never resolve staleness by deleting the header.
- The stamp tool is **not vendored here** (de-vendored in `b14ce2e`; there is no `.authormark/`
  directory). It lives in `Srinivasan-78/authormark-watch` and runs as the CI `authormark check`
  action on every PR. Do **not** use a locally-recovered copy of the old
  `.authormark/authormark.mjs`: it rewrites header line 1 without the zero-width watermark
  payload — i.e. it strips the watermark. Editing any `repo2graph/*.py`, `tests/*.py` or these
  `.md` rule files leaves a stale `Fingerprint:` that only the canonical tool can refresh; treat
  that re-stamp as a pre-merge gate (`.github/**` YAML is on the ignore list and needs none).
- CI runs `authormark check` on every PR and fails if a watermark is missing.

## Text slicing — use `split("\n")`, never `splitlines()`

tree-sitter advances `Point.row` on `\n` only. `str.splitlines()` (and universal-newline mode)
*also* break on U+2028, U+2029, U+0085, `\x0b` and `\x0c` — so any source file containing one of
those desyncs Python's line list from the parser's row numbers, and every later symbol's chunk
text gets sliced from the wrong lines. This bug class keeps recurring: ISS-22 (`chunks.py`), the
`query.py` comment near its `read_jsonl`, and still-open at `graph.py:380` (git-log output is
`.splitlines()` — a raw U+2028 in a path splits the line and the file drops out of `CO_CHANGE`).

- Slice source-against-parser with `src.split("\n")`, dropping a trailing `"\r"` per line for CRLF.
- `chunks.py` already has a `_lines(src)` helper that does exactly this — reuse it.
- `splitlines()` is fine only on content that is guaranteed `\n`-only (e.g. `graph.py:162`
  reading `go.mod`).

## Decoding git subprocess output (Windows / non-UTF-8 locales)

Never pass `text=True` (or `encoding=<locale>`) to a `subprocess` call that reads **git** output.
The Windows locale is cp1252, so a non-ASCII path raises `UnicodeDecodeError` — sometimes inside
the error handler, masking the real failure. Every historical regression in this repo is a
Windows encoding bug (ISS-06/17/22/27); CI now has a `windows-latest` leg specifically to catch
the class.

The established pattern — originated in `walker._git_files`, now also in `graph.add_cochange`
and `fetch.py`:

- run git with `-c core.quotepath=false` so non-ASCII paths return raw, not `"caf\303\251.py"`
  (the quoted form never matches a path/file index, so edges silently vanish);
- capture bytes and `.decode("utf8", "surrogateescape")`;
- always set `timeout=` and convert `TimeoutExpired` into that call's normal error type;
- `fetch.py` may instead use `encoding="utf8", errors="replace"` for user-facing text — same
  intent, no crash on a stray byte.

## Discovery indexes dot-directories, git or not

`walker.discover()` applies one `DEFAULT_SKIP_DIRS` filter to both sources (`git ls-files` and
the `os.walk` fallback) — ISS-13. The `os.walk` path no longer drops every dot-directory, so a
plain-folder build now indexes `.github/**` and any dot-dir not in `DEFAULT_SKIP_DIRS`, matching
what a git checkout always did. Consequence: on non-git builds, tool caches like `.ruff_cache/`,
`.eggs/`, `.cache/` are picked up unless their names are added to `DEFAULT_SKIP_DIRS`
(open follow-up SH-5).

## Two budget models coexist — do not unify them

`Index.retrieve()` and `Index.pack_context()` mean different things by `budget_chars`, on purpose.
`retrieve()` (`query.py:272`) bounds the sum of the returned chunks' `text` only; `pack_context()`
(`query.py:349`) bounds the **whole returned markdown** — map prepend, the `---` separator, every
`### [cite: ...]` header and the blank lines between blocks all charge against it. This reads like
an inconsistency and it is not one: `retrieve()`'s text-only accounting is pinned by
`tests/test_repo2graph.py` (`test_index_retrieves_and_expands`,
`test_iss25_query_constants_and_budget_bounds`) and consumed by `cmd_query`, so "harmonising" the
two silently changes `repo2graph query`'s output for every existing caller.

- New retrieval surface goes on `pack_context()`. `retrieve()` is a back-compat surface: keep its
  positional order `(query, k, hops, budget_chars)` and add only keyword-only params that default
  to today's behaviour (`min_confidence=None` means "no confidence filter", which is what the
  pre-GraphRAG code did).
- `pack_context(budget_chars <= 0)` is unbounded; `retrieve()` has no such convention.

## A new default on a shared traversal helper narrows its existing callers

When you add a filtering parameter to a helper that already has callers, the *safe* default for
the helper is not the safe default for the callers. `expand()` gained
`edge_dirs=None → DEFAULT_EDGE_DIRS` (`query.py:36,245`) — `DEFINES: ("in",)`, `IMPORTS: ("out",)`,
`INHERITS: ("out",)` — which the new packing path wants. `retrieve()` passed no `edge_dirs`, so it
inherited the narrowing and silently lost every DEFINES-out, IMPORTS-in and INHERITS-in neighbour
(41 → 28 neighbours on one probe query; `repo2graph query "how does export write manifest"` dropped
`sym:repo2graph/export.py::_flat`). Same shape as the confidence gate, which *was* neutralised.

- Every pre-existing caller must opt out **by name**, not by omission. `ALL_EDGE_DIRS: dict = {}`
  (`query.py:48`) exists solely so `retrieve()` (`query.py:302-307`) says "no direction filter" in
  the source, where a reader and a `grep` can both see it.
- The confidence gate is `CALLS`-only by design: IMPORTS / DEFINES / INHERITS records carry no
  `confidence` key, and `min_confidence` must never drop them.
- Prove the neutrality against the **baseline**, not against the new code: `git worktree add <tmp>
  <baseline-sha>`, run the same seed list through both trees, compare the tuple lists.

## Tests must pin values, not compare the implementation to itself

`cmd_query`'s output test asserted stdout `== format_pack(Index(out).retrieve(...))`. Both sides
move together under any traversal change, so it stayed green straight through the narrowing bug
above and would stay green through the next one. Same shape in `tests/test_rag.py`'s
`compressed_form` helper, which re-implements `query._compress` and then asserts they agree.

- For retrieval/traversal behaviour, assert literal `(node_id, why)` membership hand-derived from
  the fixture source — never a value computed by the code under test. Set membership only; no
  score, rank, float or ordering (those drift with any scoring tweak).
- Prove a new test is a detector: revert the fix in the working copy, watch the new test fail and
  the old one pass, then restore and confirm `git hash-object` is unchanged.

## A file with little residue emits no file-level chunk

`chunks.py:185` drops a `file_residual` chunk whose body is under 40 characters after every symbol
span is carved out. So a small module that is *all* imports and defs has a node but no chunk — and
a node with no chunk can be neither a seed nor a retrievable neighbour. Consequence for fixtures:
a synthetic package whose modules are pure `def`s physically cannot express an IMPORTS edge through
`retrieve()`, no matter what the traversal does. Give each module a module-level constant/table so
its file node carries a chunk (the `dirs_out` fixture in `tests/test_rag.py` exists only for this;
`rag_repo` hits the threshold and was left alone).

## `layout.path(out, "overview.md")` is `human/overview.md`, not `agent/`

`overview.md` is the one artifact written to two sections — `SECTIONS["overview.md"] = (HUMAN_DIR,
AGENT_DIR)` (`layout.py:45`) — and `layout.rel()` returns the **first**, so `path()` resolves to
`human/`. Everything else agent-facing resolves to `agent/`, which makes the wrong assumption easy.
Readers that must tolerate a deleted overview should treat either copy as optional; use
`layout.paths()` when you need both.

## `rag --answer` uploads repository source to a third-party endpoint

`repo2graph rag --answer` POSTs the assembled pack — real file content out of `chunks.jsonl` — to
whichever provider wins the `GEMINI_API_KEY` → `OPENAI_API_KEY` → `ANTHROPIC_API_KEY` →
`OLLAMA_HOST` precedence race. Non-source files are chunked whole, so a `.env` in the indexed repo
can be seeded by a query and shipped verbatim. Treat this as the sensitive path:

- It is gated: `answer` is imported lazily and only under `if args.answer:`; a plain `rag` makes no
  DNS lookup and no connect. Keep it that way, and keep the socket-level no-network test.
- `answer._disclose()` prints provider + **hostname only** to stderr before the first byte. Never
  put a credential in a URL (the Gemini key moved to the `x-goog-api-key` header for exactly this
  reason) and never echo `HTTPError.url` in an error message.
- Closed: a `--provider` flag forces a specific provider, and dotfile/secret-ish paths are
  automatically excluded from the pack via `pack_context(exclude_secrets=args.answer)` when
  `--answer` is on.
