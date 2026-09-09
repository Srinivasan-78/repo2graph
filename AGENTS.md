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
