# Build State

Status: DONE
Iteration: 3
Scenario: feature
Baseline: 4a3ba03674ef9c23898233bb787a28e5199e0bc4
Started: 2026-09-11

## Request

Turn repo2graph into a GraphRAG engine (steps 1-5 of the user's blueprint). Scope, in order:

STEP 1 — repo2graph/query.py, Index.__init__ + Index.expand:
- Retain edge attributes in adjacency: store (dst, type, direction, edge_record) instead of the current 3-tuple at query.py:61-62, so `confidence` and `count` are reachable during traversal.
- Load and cache agent/overview.md and agent/manifest.json on Index init (via layout.path). Missing files must degrade gracefully, not raise.
- expand(): add min_confidence param (default 1.0) applied ONLY to CALLS edges — IMPORTS/DEFINES/INHERITS carry no `confidence` key and must never be dropped by it. Support directional selectivity: CALLS out (callees), CALLS in (callers), DEFINES in (parent), INHERITS out.

STEP 2 — repo2graph/query.py, scoring + packing:
- Index.score(): exact-identifier boost on chunk `qualname`/`name` for pinpoint queries (e.g. `normalize_provider`). Keep BM25 as-is otherwise.
- Optional embedding/RRF layer: pluggable embedder or precomputed vectors; RRF = 1/(60+rank_bm25) + 1/(60+rank_vec). Must fall back to BM25+boost with ZERO new dependencies when absent.
- retrieve(): seeds keep full text; graph neighbors keep full text if budget allows, else compress to metadata header + signature line.
- New Index.pack_context(query, k, hops, budget_chars, min_confidence) returning a dict with a "markdown" key. Sort retrieved chunks by (path, start_line) before formatting. Citation headers: `### [cite: path/to/file.py:45-82] `symbol` (seed | CALLS out of caller)`. Prepend the repo map (overview + top entrypoints) then `---` then retrieved context.
- BUDGET SEMANTICS (resolve explicitly in PLAN and state it in the acceptance criteria): budget_chars must bound the WHOLE returned markdown — map prepend and citation headers included — not just chunk text. Seeds are prioritized over neighbors.

STEP 3 — repo2graph/cli.py: `rag` subcommand.
- KNOWN DEFECT to fix in PLAN: the blueprint's parser has two positionals (target, query) but its own usage examples pass only one (`repo2graph rag -o .r2g "how does session auth work?"`). Make `target` nargs="?" and resolve: index dir if it contains agent/manifest.json; source repo dir -> auto build() into -o first; owner/repo or GitHub URL -> fetch.index_github(); when only one positional is given it is the query.
- Flags: -k (default 8), --hops (1), --budget (24000), --min-conf (1.0), --no-expand, --format {markdown,json}, --answer, --model.
- --min-conf needs a float validator clamped to [0.0, 1.0]; the existing _nonneg is int-only and a bare float() accepts nan/inf.
- Reuse the existing _require_index() error messages.

STEP 4 — new repo2graph/answer.py (~60-80 lines, optional, only on --answer):
- Grounded system prompt: answer strictly from the provided map + chunks, never invent APIs, cite every claim as [path/file.py:start-end].
- Dispatch on env: GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, OLLAMA_HOST. stdlib urllib.request only — no openai/anthropic packages in core install.
- Stream to stdout. WINDOWS: the console is cp1252; write sys.stdout.buffer or wrap with errors="replace" or non-ASCII model output raises UnicodeEncodeError.

STEP 5 — new tests/test_rag.py:
- Graph expansion characterization: intent query finds the seed, 1-hop retrieves caller/callee across files.
- Confidence filtering: edges with confidence < 1.0 are pruned from expansion.
- Ablation: --no-expand (lexical only) vs default (GraphRAG) shows graph expansion captures deps lexical search misses. MUST use a fixed synthetic fixture repo and assert set membership of required node ids — never score values or ranks, which drift.
- Budget compliance: pack_context never exceeds budget_chars and prioritizes seeds over neighbors.
- answer.py: mock the LLM endpoint with a localhost http.server or a monkeypatched urllib opener — no live API key, no network in CI. Assert the grounded prompt and citation instructions reach the endpoint.

STEP 6 — also pyproject.toml optional-dependencies: add `rag = ["sentence-transformers>=3.0", "numpy>=1.24"]` (strictly optional); keep core pure-Python. And extend export.py's HOW_TO_READ / FILE_NOTES to document the GraphRAG retrieval protocol (1-hop expansion + confidence filtering) for external agents reading manifest.json.

REPO RULES that every phase must respect (from AGENTS.md — read it):
- Never delete/edit/reorder the `@authormark v1` header block in any file. Editing query.py/cli.py/export.py leaves a stale `Fingerprint:` — that is EXPECTED; the canonical stamp tool in CI refreshes it. Do NOT resolve staleness by deleting the header, and do NOT re-stamp with any locally recovered .authormark/authormark.mjs (it strips the watermark). New files (answer.py, tests/test_rag.py) need headers too — flag re-stamp as a pre-merge gate in the final report.
- Text slicing: use src.split("\n"), NEVER splitlines() (U+2028/2029/0085/\x0b/\x0c desync rows from tree-sitter). chunks.py has a _lines(src) helper to reuse.
- Any subprocess reading git output: no text=True/encoding=<locale>; use -c core.quotepath=false, capture bytes, .decode("utf8","surrogateescape"), always set timeout=.

Branch is masterbot-provenance, clean. Do not commit or push unless asked.

_Scenario call:_ feature — repo2graph already has a working build/query/export
pipeline and a passing suite. This adds a retrieval layer (`pack_context`), a
CLI subcommand (`rag`), and one optional module (`answer.py`) on top of existing
modules and conventions. Existing `build`/`query`/`export` behavior must be
preserved, so PLAN maps onto existing modules before proposing anything.

## Plan

### Goal

Add a GraphRAG retrieval layer on top of the existing repo2graph index: `query.Index` keeps
edge attributes in its adjacency so traversal can filter CALLS edges by `confidence`, gains an
exact-identifier boost and an optional (zero-dependency-by-default) embedding/RRF layer, and
gains a new `pack_context()` that returns an agent-ready markdown pack — repo map prepend,
`---`, then citation-headed chunks sorted by (path, start_line) — whose *total* size is bounded
by `budget_chars`. A new `rag` CLI subcommand drives it, an optional `answer.py` streams a
grounded, citation-required answer to a hosted or local LLM over stdlib `urllib` only, and
`tests/test_rag.py` pins the behavior with no network and no score/rank assertions. Existing
`build` / `github` / `query` / `map` / `stats` behavior is unchanged.

### Non-goals

- No change to graph building, parsing, chunking, or artifact layout. `graph.py`, `chunks.py`,
  `parse.py`, `walker.py`, `viz.py`, `fetch.py` are **not** edited (fetch is only *called*).
- No new required runtime dependency. `sentence-transformers`/`numpy` land in an optional
  `rag` extra and are never imported at module import time.
- No re-ranking model, no chunk re-embedding pipeline, no vector store, no persistence of
  embeddings to disk in this slice (embeddings are supplied by the caller or computed by an
  injected embedder object).
- No provider SDKs (`openai`, `anthropic`, `google-genai`) — `urllib.request` only.
- No change to `retrieve()`'s observable output for existing callers (see Decision D1).
- Not re-stamping authormark fingerprints locally; that is a CI/pre-merge gate.

### Decisions taken now (these were open questions)

**D1 — `retrieve()` keeps its signature and its behavior; `pack_context()` layers on top.**
`tests/test_repo2graph.py:322` (`test_index_retrieves_and_expands`) and `:1135`
(`test_iss25_query_constants_and_budget_bounds`) pin `retrieve(query, k=, hops=, budget_chars=)`
returning a list of chunk dicts with `score` and `why`, budgeted on chunk `text` only, and
`cmd_query` at `cli.py:89` consumes exactly that. Therefore:
- `retrieve()` keeps positional/keyword names `query, k=8, hops=1, budget_chars=24000` and its
  current text-only budget accounting and `why` strings, unchanged.
- It gains ONE new keyword-only param `min_confidence: float | None = None`, where `None` means
  "do not filter on confidence" — i.e. today's behavior exactly. `cmd_query` does not pass it.
- `expand()` gets `min_confidence: float = 1.0` per the blueprint, but `retrieve()` calls it
  with `min_confidence=0.0` when its own param is `None`, so no CALLS edge that reaches a
  neighbor today disappears from `query` output.
- `pack_context()` is a new method that calls `retrieve()`-style selection with the *new*
  whole-markdown budget accounting (Decision D2). The two budget models therefore coexist:
  text-only inside `retrieve()`, whole-markdown inside `pack_context()`. This is stated in the
  docstrings so a later reader does not "unify" them and break the pinned tests.

**D2 — budget accounting migration.** `pack_context(budget_chars=N)` guarantees
`len(result["markdown"]) <= N` for `N > 0`. It does not reuse `retrieve()`'s accounting. It
builds the pack incrementally and charges every emitted character: the map prepend, the `---`
separator, each `### [cite: ...]` header line, the blank-line separators, and the chunk text.
Order of spending: (a) map prepend, capped at `MAP_BUDGET_FRAC = 0.2` of the budget and
truncated on a line boundary; (b) seed chunks in score order, full text; (c) graph neighbors in
expansion order, full text if it fits, else the compressed form; a neighbor that does not fit
even compressed is skipped, not truncated mid-line. `budget_chars <= 0` means unbounded
(documented). If not everything fit, `result["truncated"] is True`.

**D3 — the map prepend's "top entrypoints ranked by reach" exists on disk.** `export.py:462`
already writes `manifest["entrypoints"]` = up to 25 objects `{id, path, qualname, kind, reach}`
sorted by `(-reach, path, qualname)` (`export.py:443`). No derivation from the graph is needed
and nothing is dropped. The map prepend is: `agent/overview.md` text (if present) + a
`## Top entry points` list rendered from `manifest["entrypoints"][:MAP_ENTRYPOINTS]`
(MAP_ENTRYPOINTS = 10). Both sources are optional; either missing degrades to the other, and
both missing degrades to an empty map with no exception.

**D4 — compressed neighbor form.** Chunk records have no `signature` field
(`export.py:458` `chunk_fields`), but `chunks.py:131-155` guarantees `text` starts with `# file:`
/ `# <kind>:` comment header lines followed by the body. Compression = keep the leading `#`
header lines plus the first following non-blank line (the `def`/`class`/`func` signature line),
via `text.split("\n")` — never `splitlines()` (AGENTS.md; chunk text can contain U+2028).

### Files touched

| File | Change |
|---|---|
| `repo2graph/query.py` | edge-attr adjacency, overview/manifest load, `expand(min_confidence, edge_dirs)`, identifier boost, optional RRF, `pack_context()`, `format_pack` untouched |
| `repo2graph/cli.py` | new `_unit_float` argparse type, `cmd_rag`, `rag` subparser; `_require_index`, `_nonneg`, all existing subparsers untouched |
| `repo2graph/answer.py` | NEW — provider dispatch + grounded prompt + byte-safe streaming |
| `repo2graph/export.py` | 2 new `HOW_TO_READ` entries, 1 new `FILE_NOTES`-adjacent note; no code-path change |
| `pyproject.toml` | `[project.optional-dependencies] rag = [...]` |
| `tests/test_rag.py` | NEW — all criteria below |

Conventions to follow: module docstring under the authormark block; `from .layout import path as
artifact_path`; named module-level constants (BM25_* precedent) instead of magic numbers;
`SystemExit(str)` for user-facing CLI errors; lazy imports inside `cmd_*` functions; ruff
line-length 100, select E/F/W. New files need an `@authormark v1` header block — copy the shape
from an existing file, leave the `Fingerprint:` line present (CI's canonical stamp tool refreshes
it; never hand-write or delete it).

### Ordered tasks

1. **query.py — adjacency + artifacts.** Replace the 3-tuples at `query.py:61-62` with
   `(dst, type, direction, edge)` 4-tuples (the full edge record, not a copy). Add
   `self.overview: str` and `self.manifest: dict`, loaded from `artifact_path(dir,
   "overview.md")` / `"manifest.json"` inside try/except `(OSError, ValueError,
   json.JSONDecodeError)` → `""` / `{}`. Read with `encoding="utf8", newline="\n"`.
2. **query.py — `expand()`.** Signature
   `expand(seed_nodes, hops=1, edge_types=None, per_hop=6, min_confidence=1.0, edge_dirs=None)`.
   Confidence gate applies **only** when `etype == "CALLS"`: `edge.get("confidence", 1.0) <
   min_confidence` → skip. All other types pass untouched. `edge_dirs` maps edge type → allowed
   directions, defaulting to `DEFAULT_EDGE_DIRS = {"CALLS": ("out", "in"), "DEFINES": ("in",),
   "INHERITS": ("out",), "IMPORTS": ("out",)}`. Return shape `(dst, etype, direction, src)`
   stays the same so `retrieve()` is unaffected.
3. **query.py — scoring.** `score(query)` adds an exact-identifier boost: for each query token
   that matches a chunk's `name` or the last dotted/`::` segment of its `qualname` exactly
   (case-sensitive first, case-insensitive fallback), multiply that chunk's BM25 score by
   `IDENT_BOOST = 2.5`. Ordering stays descending, ties broken by chunk index for determinism.
   Add `score_rrf(query, vectors=None, embedder=None)`: when neither is supplied, return
   `score()` unchanged; otherwise fuse BM25 ranks and vector-similarity ranks with
   `RRF_K = 60`, `1/(RRF_K+rank_bm25) + 1/(RRF_K+rank_vec)`. `numpy` is imported lazily inside
   this branch only.
4. **query.py — `retrieve()`.** Add keyword-only `min_confidence=None` and thread it to
   `expand()` (None → 0.0). No other change (D1).
5. **query.py — `pack_context()`.** `pack_context(query, k=8, hops=1, budget_chars=24000,
   min_confidence=1.0, expand_graph=True, vectors=None, embedder=None) -> dict` with keys
   `markdown, chunks, seeds, neighbors, truncated, budget_chars, used_chars, query`. Selection:
   seeds by score (one chunk per node id, as `retrieve` does), then neighbors from `expand()`.
   Formatting per D2/D4; chunk blocks sorted by `(path, start_line, chunk id)` before rendering;
   header format exactly
   `### [cite: {path}:{start_line}-{end_line}] \`{qualname or name}\` ({why})` with
   `why` = `seed` for seeds and `{TYPE} {direction} of {src_name}` for neighbors.
6. **cli.py — `_unit_float`.** argparse type: `float(value)`, rejecting `nan`/`inf`
   (`math.isfinite`) and anything outside `[0.0, 1.0]`, raising `argparse.ArgumentTypeError`
   with the offending value.
7. **cli.py — `cmd_rag` + `rag` subparser.** `target` is `nargs="?"`, `query` is the last
   positional. Resolution order per the Request: 1 positional → it is the query and the index is
   `-o`; 2 positionals → resolve `target` as (a) index dir if `agent/manifest.json` exists under
   it, (b) existing source dir → `build()` + `dump_all()` into `-o` first, (c) a valid
   `fetch.parse_spec` GitHub spec → `fetch.index_github(target, out)`, else `SystemExit` naming
   the three accepted forms. Then `_require_index(out, ...)` for chunks/nodes/edges (reusing the
   existing messages verbatim), build `Index`, call `pack_context`, print markdown or
   `json.dumps(result, indent=2)`. `--answer` imports `answer` lazily and streams.
8. **answer.py.** `build_prompt(pack) -> (system, user)`, `pick_provider(env) -> spec|None`,
   `stream_answer(pack, model=None, env=None, out=None) -> str`. Provider precedence
   GEMINI → OPENAI → ANTHROPIC → OLLAMA_HOST; no key → `SystemExit` naming the four env vars.
   Output written through a byte-safe writer (Task 9). `urllib.request` only, explicit
   `timeout=`.
9. **answer.py — Windows stdout.** `_writer(out)`: if `out` is None use
   `sys.stdout.buffer.write(chunk.encode("utf8", "replace"))` + flush when
   `sys.stdout.buffer` exists, else `sys.stdout.write` with a `errors="replace"`-style fallback
   (`chunk.encode(enc,"replace").decode(enc,"replace")`). Never let a non-ASCII token raise.
10. **export.py docs.** Append to `HOW_TO_READ`: (a) the GraphRAG protocol — score chunks,
    expand 1 hop over CALLS out/in + DEFINES in + INHERITS out, filter CALLS on
    `confidence >= 1.0`, pack under a char budget, cite `path:start-end`; (b) that
    `repo2graph.query.Index.pack_context` implements it and `repo2graph rag` exposes it. No
    change to `write_manifest`'s structure.
11. **pyproject.toml.** `rag = ["sentence-transformers>=3.0", "numpy>=1.24"]` under
    `[project.optional-dependencies]`. Core `dependencies` untouched.

### Acceptance criteria

Adjacency / init

1. `Index.adj[nid]` entries are 4-tuples `(dst, type, direction, edge)` and
   `edge["confidence"]` is readable for at least one CALLS entry in the ambiguity fixture.
2. `Index(out).overview` is the text of `agent/overview.md` when the build wrote it, and `""`
   when the file is absent — constructing `Index` on a `--formats jsonl` build (no overview.md)
   raises nothing.
3. `Index(out).manifest` is the parsed `agent/manifest.json` dict, and `{}` when the file is
   absent **or** unparseable (truncate the file to `"{"` → `Index(out)` still constructs and
   `manifest == {}`).

Expansion / confidence

4. `expand(seeds, min_confidence=1.0)` returns no `(dst, "CALLS", ...)` tuple whose edge record
   has `confidence < 1.0`, on a fixture with a deliberately overloaded callee name.
5. The same call still returns the IMPORTS / DEFINES / INHERITS neighbors of those seeds — for a
   fixture where the *only* edge to node X is a DEFINES or INHERITS edge (no `confidence` key),
   X is in the result at `min_confidence=1.0`.
6. `expand(seeds, min_confidence=0.5)` returns strictly more nodes than
   `expand(seeds, min_confidence=1.0)` on the ambiguous fixture (set comparison, superset +
   non-equal).
7. Default directions hold: for a caller→callee pair, expanding from the callee yields the
   caller with `direction == "in"`, expanding from the caller yields the callee with
   `direction == "out"`; expanding from a symbol yields its defining file via a `DEFINES`
   tuple with `direction == "in"`.

Scoring

8. For fixture symbols `normalize_provider` and a decoy chunk that mentions the words
   "normalize" and "provider" many times but is not that symbol, `score("normalize_provider")`
   ranks the chunk whose `node_id` ends `::normalize_provider` first.
9. `score()` output is still sorted descending and every returned index still contains a query
   term (the existing `test_score_matches_bruteforce` invariant still passes unmodified).
10. With no `vectors` and no `embedder`, `score_rrf(q) == score(q)` exactly, and importing
    `repo2graph.query` succeeds in an environment with no `numpy` and no
    `sentence_transformers` (assert via `sys.modules` after import that neither was imported).
11. With a stub embedder object supplied, `score_rrf` returns results and does not import numpy
    if the stub returns plain lists.

Backward compatibility

12. `retrieve("double a value helper", k=3, hops=1)` on `sample_repo` still returns a
    `pkg/util.py` hit and at least one hit whose `why != "lexical"` (existing test unchanged and
    still green).
13. `inspect.signature(Index.retrieve)` still accepts `(query, k, hops, budget_chars)`
    positionally in that order, and `main(["query", ...])` output is byte-identical before and
    after for the sample repo (characterization test capturing stdout).

pack_context

14. `pack_context(q, budget_chars=N)["markdown"]` satisfies `len(markdown) <= N` for
    N in {200, 1000, 4000, 24000} on the fixture repo — the *whole* string, map and headers
    included.
15. Every emitted chunk block is preceded by a header matching
    `^### \[cite: (?P<path>[^\]]+):(?P<start>\d+)-(?P<end>\d+)\] ` and the path/start/end equal
    that chunk's `path`/`start_line`/`end_line`.
16. Blocks appear in non-decreasing `(path, start_line)` order in the markdown.
17. The markdown contains the map prepend followed by a line that is exactly `---` before the
    first `### [cite:` header, when an overview exists; with `agent/overview.md` and
    `agent/manifest.json` both deleted, `pack_context` still returns markdown containing at
    least one `### [cite:` header and no exception.
18. The map's entry-point list is rendered from `manifest["entrypoints"]` and its first listed
    qualname equals `manifest["entrypoints"][0]["qualname"]`.
19. Seeds are prioritized: at a budget that fits exactly one full block, the surviving block's
    `why` is `seed`; at a budget where a neighbor cannot fit in full but fits compressed, that
    neighbor's block text is the compressed form (ends with the signature line, contains no line
    from the body beyond it) and `result["truncated"] is True`.
20. `pack_context(..., expand_graph=False)` returns a `neighbors` list of length 0 and every
    entry of `chunks` has `why == "seed"`.
21. Ablation, set membership only: on the fixed synthetic fixture, the set of `node_id` values
    from `expand_graph=True` is a strict superset of the `expand_graph=False` set and contains
    the required id `sym:pkg/<dep>.py::<dep_symbol>` that lexical-only retrieval misses. No
    assertion on any score, rank or ordering of scores.

CLI

22. `main(["rag", "-o", str(out), "how does session auth work?"])` (ONE positional) succeeds and
    prints markdown containing `### [cite:`.
23. `main(["rag", str(index_dir), "some query"])` (TWO positionals, target is an existing index)
    uses that index without rebuilding; `main(["rag", str(source_repo), "some query", "-o",
    str(new_out)])` builds first and creates `agent/manifest.json` under `new_out`.
24. `main(["rag", "not/a real spec/x", "q"])` raises `SystemExit` whose message names all three
    accepted target forms; `main(["rag", "q", "-o", str(empty)])` raises `SystemExit` with the
    existing `no index at {out}: run \`repo2graph build ...\`` text.
25. `--min-conf` rejects `nan`, `inf`, `-0.1`, `1.1` and `abc` with a non-zero exit
    (`SystemExit` from argparse), and accepts `0`, `0.5`, `1.0`.
26. `rag --format json` prints a JSON object with the keys `markdown`, `chunks`, `truncated`,
    `used_chars`; defaults are `-k 8 --hops 1 --budget 24000 --min-conf 1.0`, verified by
    reading the parser defaults.
27. Existing subcommands are untouched: `main(["query", ...])`, `build`, `map`, `stats` all
    behave as before (full existing suite green).

answer.py

28. With a `http.server`-backed localhost endpoint (or a monkeypatched `urllib.request` opener)
    and `OPENAI_API_KEY` set to a dummy value, `stream_answer` sends a request whose captured
    body contains the grounded-instruction sentence, the phrase requiring every claim to carry a
    `[path/file.py:start-end]` citation, and the pack's markdown. No real network call is made
    (asserted by the mock recording exactly the expected host).
29. With every provider env var unset, `stream_answer` raises `SystemExit` naming
    `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` and `OLLAMA_HOST`.
30. Streaming a chunk containing `"café — ✓"` into a writer whose encoding is cp1252 produces
    output and raises no `UnicodeEncodeError` (test uses a `io.TextIOWrapper(io.BytesIO(),
    encoding="cp1252")` and a cp1252 buffer stand-in for `sys.stdout`).

Docs / packaging / repo rules

31. `pyproject.toml` has `optional-dependencies.rag == ["sentence-transformers>=3.0",
    "numpy>=1.24"]` and `project.dependencies` is unchanged (still exactly tree-sitter +
    tree-sitter-language-pack).
32. `export.HOW_TO_READ` contains an entry mentioning both `pack_context` and confidence
    filtering, and a freshly built `agent/manifest.json` carries it under `how_to_read`.
33. Every new/edited source file still begins with the `@authormark v1` block: a test asserts
    lines 1-5 of `repo2graph/answer.py` and `tests/test_rag.py` match the header shape
    (`@authormark v1`, `Copyright (c)`, `Author:`, `SPDX-License-Identifier: MIT`,
    `Fingerprint: AMK1.`).
34. No new code calls `str.splitlines()`: a test greps `repo2graph/query.py` and
    `repo2graph/answer.py` for `.splitlines(` and asserts zero matches.

### Test strategy

- **Level:** unit for `expand`/`score`/`_unit_float`/`answer._writer`; integration through
  `cli.main([...])` for `rag` (the established pattern in `test_repo2graph.py` — build with
  `main(["build", str(repo), "-o", str(out), "--formats", "jsonl"])`, then act); no e2e, no
  network.
- **File:** all new tests in `tests/test_rag.py` (there is no `conftest.py`; the fixtures below
  are defined locally in that file, mirroring `sample_repo`/`sample_graph` in
  `test_repo2graph.py`). Do not edit `tests/test_repo2graph.py` except where a criterion says a
  test must remain unchanged — it must keep passing as-is.
- **Fixtures (fixed and synthetic, criterion 21):** `rag_repo(tmp_path)` writes a deterministic
  4-file Python package:
  - `pkg/config.py` — `def normalize_provider(name): ...` plus a decoy module docstring using
    the words "normalize" and "provider" repeatedly (criterion 8);
  - `pkg/session.py` — `def authenticate(user)` calling `normalize_provider` and
    `verify_token`, with the words "session auth" only here (the lexical seed);
  - `pkg/tokens.py` — `def verify_token(t)` whose text deliberately shares no vocabulary with
    the query (the dep lexical search misses — the required node id in criterion 21);
  - `pkg/ambig.py` — two classes each defining a method named `handle`, and a caller calling
    `handle()`, forcing `confidence == 0.5` CALLS edges (criteria 4/6);
  - plus `class Base` / `class Child(Base)` for the INHERITS case (criterion 5).
  `rag_index(rag_repo, tmp_path)` builds it once per test via `main(["build", ...,
  "--formats", "jsonl,overview"])` so both `overview.md` and `manifest.json` exist; a second
  fixture builds with `--formats jsonl` only, for the graceful-degradation criteria (2, 17).
- **Mocking:** `answer.py` is tested two ways — (a) `monkeypatch.setattr(answer.urllib.request,
  "urlopen", recorder)` where `recorder` captures the `Request` object and returns a fake
  file-like yielding SSE/JSON-lines bytes; (b) one test spins a `http.server.HTTPServer` on
  `127.0.0.1:0` in a daemon thread with `OLLAMA_HOST` pointed at it, to prove the real urllib
  path works. Env vars are set/cleared with `monkeypatch.setenv`/`delenv(..., raising=False)`
  so a developer's real key never leaks into the test. Assert on the captured request body, not
  on model output.
- **Forbidden assertions:** no test may assert a score value, a score ordering across different
  chunks beyond criterion 8's single "is first" check, a rank number, or a floating-point
  similarity. Ablation and expansion tests assert `set` membership / superset relations on
  `node_id` strings.
- **Run command:** `python -m pytest tests/ -q` from the repo root (pytest `testpaths = ["tests"]`
  is already configured); the phase subset is `python -m pytest tests/test_rag.py -q`.
- **"Fails for the right reason":** at TEST time every new test must fail with
  `AttributeError: 'Index' object has no attribute 'pack_context'`,
  `TypeError: expand() got an unexpected keyword argument 'min_confidence'`,
  `ModuleNotFoundError: No module named 'repo2graph.answer'`, or an argparse
  `invalid choice: 'rag'` / `unrecognized arguments` SystemExit — plus plain assertion failures
  for the docs/packaging criteria. Any `ImportError` on a *fixture* helper, `FileNotFoundError`
  from a mis-built fixture, or a typo-driven `NameError` means the test is wrong, not the
  implementation missing; fix the test.
- **Regression guard:** the whole existing 1658-line `tests/test_repo2graph.py` must stay green
  unmodified — that is the backward-compat proof for criteria 12, 13 and 27.

### Risks

- **R1 (medium):** tightening `expand()`'s default to `min_confidence=1.0` would silently change
  `query` results. Mitigated by D1 (retrieve passes 0.0 unless told otherwise) — VERIFY must
  diff `repo2graph query` output before/after on the sample repo.
- **R2 (medium):** whole-markdown budget accounting is easy to get off-by-a-separator. Mitigated
  by criterion 14 sweeping four budgets including a very small one, and by asserting on
  `len(markdown)` rather than on an internal counter.
- **R3 (low/medium):** the exact-identifier boost could perturb the existing
  `test_score_matches_bruteforce` invariant if the boost is applied to chunks with zero BM25
  score. Constrain the boost to *multiply an existing* score; never introduce a new chunk index.
- **R4 (low):** `pack_context` reading `manifest.json` on every construction adds I/O to `Index`,
  which `query` also constructs. Files are small and read once in `__init__`; failures are
  swallowed.
- **R5 (low):** the localhost `http.server` test can be flaky in constrained CI. Keep it to one
  test, bind `127.0.0.1:0`, set a socket timeout, and mark the monkeypatched-opener test as the
  primary coverage so a firewall-blocked runner still proves the contract.
- **R6 (process):** editing `query.py`, `cli.py`, `export.py`, `pyproject.toml` and the two new
  files leaves stale `Fingerprint:` lines. That is expected. Do not delete or hand-edit the
  headers and do not run any locally recovered `.authormark/authormark.mjs`; the canonical
  `authormark check` action re-stamps as a pre-merge gate. The final report must flag it.
- **R7 (low):** `fetch.index_github` in the `rag` target-resolution path would hit the network.
  It is only reached for a GitHub-spec target; the CLI tests only exercise the index-dir and
  source-dir branches, and assert the GitHub branch by monkeypatching `fetch.index_github`.

## Tests

### Files

| File | Change |
|---|---|
| `tests/test_rag.py` | NEW — 54 tests covering acceptance criteria 1-34. Authormark header bytes copied verbatim (zero-width payload intact) from `tests/test_repo2graph.py`; the `Fingerprint:` line is present and un-edited — CI's canonical stamp tool must refresh it pre-merge. |
| `tests/test_repo2graph.py` | UNTOUCHED (regression guard for AC-12/13/27; 150 passed, 2 skipped). |

No implementation code was written. `repo2graph/answer.py` deliberately does **not** exist.

### Run commands

```
python -m pytest tests/test_rag.py -q      # phase subset  -> 48 failed, 6 passed
python -m pytest tests/ -q                 # full suite    -> existing 150 still pass
python -m ruff check tests/test_rag.py     # All checks passed
```

### Fixtures (fixed + synthetic, no network, no git)

`rag_repo` writes a deterministic 7-file python package under `tmp_path/src`:

- `pkg/config.py::normalize_provider` — long-ish docstring body (chunk length 162 tokens).
- `pkg/decoy.py` — module docstring repeating the identifier `normalize_provider` 30x. At
  HEAD BM25 ranks the decoy **first** (3.891 vs 3.434, ~13% margin), so AC-8 fails today and
  `IDENT_BOOST = 2.5` flips it with ~2.2x headroom. Never assert the numbers — only "is first".
- `pkg/session.py::authenticate` — the only place the words *login handshake credential* occur;
  calls `normalize_provider` and `verify_token`.
- `pkg/tokens.py::verify_token` — shares no vocabulary with the ablation query. Verified at HEAD:
  `score("login handshake credential")` returns exactly `{sym:pkg/session.py::authenticate}`,
  and `expand()` from it yields `file:pkg/session.py` (DEFINES in), `::normalize_provider` and
  `::verify_token` (CALLS out). That is the AC-21 ablation, as set membership.
- `pkg/ambig.py` — `AlphaHandler.handle` / `BetaHandler.handle` + `dispatch()` calling `handle()`
  → two CALLS edges at `confidence 0.5` (AC-4/6).
- `pkg/base.py` — `class Child(Base)` → an INHERITS edge with no `confidence` key (AC-5).

`rag_out` builds `--formats jsonl,overview` (overview.md + manifest.json present);
`bare_out` builds `--formats jsonl` (no overview.md) for the graceful-degradation criteria.
Manifest entrypoints are `[dispatch, authenticate, Base.ping, Child.pong]` and none of those
names appear in `overview.md`, so AC-18's "first listed qualname" probe cannot alias.

### Criterion → test map

| AC | Test |
|---|---|
| 1 | `test_ac1_adjacency_entries_are_four_tuples_with_edge_records` |
| 2 | `test_ac2_overview_is_loaded_and_absence_is_graceful` |
| 3 | `test_ac3_manifest_is_loaded_and_unparseable_degrades` (truncate to `{`, then unlink) |
| 4 | `test_ac4_min_confidence_prunes_ambiguous_calls` |
| 5 | `test_ac5_confidence_gate_never_drops_non_calls_edges` |
| 6 | `test_ac6_lower_min_confidence_is_a_strict_superset` |
| 7 | `test_ac7_default_edge_directions` |
| 8 | `test_ac8_exact_identifier_boost_beats_a_lexical_decoy` |
| 9 | `test_ac9_score_invariants_survive_the_boost` (passes now — invariant guard) |
| 10 | `test_ac10_score_rrf_without_vectors_equals_score`, `test_ac10_importing_query_pulls_in_no_optional_dependency` (out-of-process `sys.modules` probe so a pytest plugin cannot mask it) |
| 11 | `test_ac11_score_rrf_with_a_stub_embedder` — pins the embedder contract: `embedder.encode(list[str]) -> list[list[float]]`, plain lists, no numpy |
| 12 | `test_ac12_retrieve_still_finds_seeds_and_graph_neighbours` (passes now) |
| 13 | `test_ac13_retrieve_signature_and_cmd_query_output_are_unchanged` (passes now — `inspect.signature` positional order, positional==keyword call, and `cmd_query` stdout == `format_pack(retrieve(...))`; a byte-golden file was rejected because `os.walk` order is not guaranteed identical on the linux CI leg) |
| 14 | `test_ac14_whole_markdown_respects_the_budget[200/1000/4000/24000]` |
| 15 | `test_ac15_every_block_has_a_citation_header_matching_its_chunk` |
| 16 | `test_ac16_blocks_are_sorted_by_path_then_start_line` |
| 17 | `test_ac17_map_prepend_then_separator_then_context` (also deletes both `overview.md` copies via `layout.paths` + `manifest.json`) |
| 18 | `test_ac18_map_entrypoints_come_from_the_manifest` |
| 19 | `test_ac19_seeds_are_prioritised_over_neighbours`, `test_ac19_a_squeezed_neighbour_is_compressed_not_truncated` (sweeps budgets 400..8000 until a compressed neighbour appears; asserts the D4 form — leading `#` lines + first non-blank line kept, every later non-blank source line absent — and `truncated is True`) |
| 20 | `test_ac20_expand_graph_false_is_seeds_only` |
| 21 | `test_ac21_ablation_graph_expansion_finds_what_lexical_misses` — set membership only |
| 22 | `test_ac22_rag_with_one_positional_is_the_query` |
| 23 | `test_ac23_target_index_dir_is_used_as_is` (manifest mtime_ns + bytes unchanged), `test_ac23_target_source_dir_is_built_first` |
| 24 | `test_ac24_unresolvable_target_names_all_three_forms`, `test_ac24_missing_index_reuses_the_existing_message` |
| 25 | `test_ac25_unit_float_validator` (unit), `test_ac25_bad_min_conf_exits_non_zero[nan/inf/-0.1/1.1/abc]` (asserts stderr names `--min-conf` and the offending value, so it cannot pass on an unrelated argparse error), `test_ac25_good_min_conf_is_accepted[0/0.5/1.0]` |
| 26 | `test_ac26_format_json_prints_the_pack_object`, `test_ac26_rag_defaults` (recorder on `Index.pack_context`, `raising=True`), `test_ac26_no_expand_flag_reaches_pack_context` |
| 27 | `test_ac27_existing_subcommands_are_untouched` (passes now) + the untouched 150-test file |
| 28 | `test_ac28_grounded_prompt_and_citations_reach_the_endpoint` (monkeypatched `urllib.request.urlopen`, dummy `OPENAI_API_KEY`, asserts host `api.openai.com`, that the body contains `path/file.py:start-end`, `cite`, `invent` and the pack markdown, and that OpenAI SSE deltas are streamed out), `test_ac28_real_urllib_path_against_a_localhost_server` (`HTTPServer` on `127.0.0.1:0`, daemon thread, `OLLAMA_HOST`, ndjson response) |
| 29 | `test_ac29_no_provider_env_names_all_four_variables` (also pins `pick_provider(env) -> None`) |
| 30 | `test_ac30_cp1252_stdout_never_raises_unicodeencodeerror` (`_writer` against a cp1252 `TextIOWrapper` and against a `sys.stdout` stand-in with a `.buffer`) |
| 31 | `test_ac31_optional_rag_extra_and_untouched_core_dependencies` |
| 32 | `test_ac32_how_to_read_documents_the_graphrag_protocol` |
| 33 | `test_ac33_new_files_keep_the_authormark_header[repo2graph/answer.py, tests/test_rag.py]` |
| 34 | `test_ac34_no_splitlines_in_new_code[repo2graph/query.py, repo2graph/answer.py]` — uses `ast.walk` for real `Attribute` calls, not a grep (the AGENTS.md rule is quoted verbatim in `query.py`'s docstring, which a grep flagged) |

### API surface the tests pin (IMPLEMENT must match)

- `Index.adj[nid] -> list[(dst, type, direction, edge_record)]`; `Index.overview: str`;
  `Index.manifest: dict`.
- `Index.expand(seeds, hops=1, edge_types=None, per_hop=6, min_confidence=1.0, edge_dirs=None)`
  returning `(dst, etype, direction, src)`.
- `Index.score_rrf(query, vectors=None, embedder=None)`; embedder duck type `.encode(texts)`.
- `Index.pack_context(query, k=8, hops=1, budget_chars=24000, min_confidence=1.0,
  expand_graph=True, ...) -> {markdown, chunks, seeds, neighbors, truncated, budget_chars,
  used_chars, query}`; `used_chars == len(markdown)`; `budget_chars <= 0` = unbounded;
  every chunk dict carries `why` (`"seed"` or `"{TYPE} {dir} of {src}"`) and keeps `id`,
  `node_id`, `path`, `start_line`, `end_line`.
- Header line exactly `### [cite: {path}:{start}-{end}] \`{qual}\` ({why})`, map then a bare
  `---` line, then blocks sorted by `(path, start_line)`.
- `cli._unit_float(str) -> float` raising `argparse.ArgumentTypeError`; `rag` subparser with
  `target` `nargs="?"`, `-k/--hops/--budget/--min-conf/--no-expand/--format`.
- `answer.pick_provider(env)`, `answer.stream_answer(pack, model=None, env=None, out=None) -> str`,
  `answer._writer(out) -> callable`; `urlopen` looked up as `urllib.request.urlopen` **at call
  time** (module attribute), or the monkeypatch in AC-28 cannot intercept it.

### Failing output (right-reason census)

`python -m pytest tests/test_rag.py -q --tb=line | grep '^E ' | sort | uniq -c`:

```
     11 AttributeError: 'Index' object has no attribute 'pack_context'
      7 SystemExit: 2                                   (argparse: no `rag` subcommand)
      7 argparse.ArgumentError: argument cmd: invalid choice: 'rag'
      5 AssertionError: usage: repo2graph [-h] {build,github,gh,query,map,stats} ...
      4 TypeError: Index.expand() got an unexpected keyword argument 'min_confidence'
      4 ModuleNotFoundError: No module named 'repo2graph.answer'
      2 AttributeError: 'Index' object has no attribute 'score_rrf'
      2 AttributeError: 'Index' object has no attribute 'manifest'
      2 AttributeError: <class 'repo2graph.query.Index'> has no attribute 'pack_context'
      2 AssertionError: repo2graph/answer.py does not exist
      1 AttributeError: module 'repo2graph.cli' has no attribute '_unit_float'
      1 AttributeError: 'Index' object has no attribute 'overview'
      1 AssertionError: Traceback ...  (AC-10 subprocess: score_rrf missing -> rc != 0)
      1 AssertionError: Start with overview.md: ...      (AC-32: HOW_TO_READ lacks pack_context)
      1 AssertionError: file:pkg/decoy.py                (AC-8: BM25 alone ranks the decoy first)
      1 AssertionError: 'no index at ...' in '2'         (AC-24b: rag not parsed yet)
      1 AssertionError: 2                                (AC-24a: rag not parsed yet)
      1 AssertionError: {'dev': ['pytest>=7', 'ruff>=0.5']}   (AC-31: no `rag` extra)
      1 AssertionError: [('file:pkg/ambig.py','DEFINES','in'), ...]  (AC-1: 3-tuples today)
```

Every failure is absent implementation. No fixture error, no import error at collection
(`repo2graph.answer` is imported *inside* the answer tests so its absence cannot abort the
module), no typo-driven `NameError`.

### The 6 tests that pass today (by design)

AC-9, AC-12, AC-13, AC-27 are backward-compatibility characterizations (D1) — they must be green
before *and* after. `test_ac33...[tests/test_rag.py]` passes because this file already carries the
authormark block. `test_ac34...[repo2graph/query.py]` passes because `query.py` has no
`splitlines()` call today; it must stay that way after the edit.

### Notes for IMPLEMENT

- `layout.path(out, "overview.md")` resolves to **`human/overview.md`** (first entry in
  `SECTIONS`), not `agent/`. Both copies are byte-identical, so either read satisfies AC-2, but
  AC-17 unlinks *both* via `layout.paths`, so the loader must tolerate either being absent.
- Re-stamping the `@authormark` fingerprints of every edited/new file is a pre-merge CI gate
  (`authormark check`), not a local step. Do not hand-edit or delete a header.

### Iteration 2

Loop-back from IMPLEMENT iter 1. **Minimal, surgical: two assertions in one test file.**
No implementation file was touched; no other test was touched.

#### The defect (confirmed)

`tests/test_rag.py:747` and `:790` asserted `PACK["markdown"] in body`, where `body` is the
decoded HTTP request payload. That payload is `application/json`; RFC 8259 §7 forbids a raw
U+000A inside a JSON string, so a multi-line `PACK["markdown"]` can never appear verbatim in
the wire bytes for *any* correct provider body. The test was wrong, not `answer.py`.

#### The fix

Both assertions now decode the payload and assert against the user turn the model actually
reads. Read from `repo2graph/answer.py:65-89`, both exercised providers (OpenAI
`/v1/chat/completions` and Ollama `/api/chat`) build the same shape — `messages` =
`[{system}, {user}]` with the whole pack in the user turn — so one key path covers both:

```python
sent = json.loads(req.data or b"{}")          # AC-28 primary  (was: body)
assert PACK["markdown"] in sent["messages"][-1]["content"]

sent = json.loads(captured[0])                # AC-28 localhost socket
assert PACK["markdown"] in sent["messages"][-1]["content"]
```

Strength preserved, not weakened: the assertion still requires the **entire** pack markdown to
arrive intact at the endpoint, now via the transported value rather than the escaped bytes. The
surrounding `GROUNDING_PHRASES` loop (`path/file.py:start-end`, `cite`, `invent`) is unchanged
and still runs against the raw `body` — those phrases contain no newlines, so they survive JSON
escaping verbatim. The host assertion (`api.openai.com`) and the "no real network" proof are
untouched, so AC-28's full contract still holds.

#### Result

```
python -m pytest tests/ -q             -> 204 passed, 2 skipped   (was 202 passed / 2 failed)
python -m ruff check tests/test_rag.py -> All checks passed!
```

The 2 failures resolved are exactly the 2 that were fixed. No other test changed status.
The `@authormark v1` block in `tests/test_rag.py` is untouched (its `Fingerprint:` is now stale
— canonical CI re-stamp remains the pre-merge gate).

#### Second-wrong-test survey (reported, not fixed)

Re-read the AC-28/29/30 block plus every assertion that inspects a request body: no other test
asserts a raw multi-line string against encoded bytes, and no other test is unsatisfiable. No
out-of-scope change was made.

#### Note on this iteration's exit condition

The normal TEST rule "every test must fail only because implementation is absent" does not
apply here: the implementation from IMPLEMENT iter 1 already exists, so the corrected tests are
green. The corrections are non-trivial (they still demand the full pack reach the endpoint),
not assertions defanged to pass.

### Iteration 3 (AC-13 hardening)

Scoped follow-up inside iteration 3, not a loop-back. One test file touched
(`tests/test_rag.py`), no implementation file changed, no other test edited.

#### The weakness

`tests/test_rag.py` AC-13 asserted `cmd_query` stdout `== format_pack(retrieve(...)) + "\n"`,
i.e. the implementation against itself. Any traversal change moves both sides together, so the
assertion stayed green through REVIEW iter 1's BLOCKING bug (`DEFAULT_EDGE_DIRS` leaking into
`retrieve()` and dropping DEFINES-out / IMPORTS-in / INHERITS-in neighbours). The existing
assertion is kept — it still pins the `cmd_query` → `format_pack` contract — and a value-pinned
guard is added beside it.

#### Why a second fixture was needed

`rag_repo` **cannot** express the three dropped directions through `retrieve()`. Its imported
modules (`pkg/config.py`, `pkg/tokens.py`) leave under 40 chars of non-symbol residue, so
`chunks.py:185` emits no file-level chunk for them; a node with no chunk can be neither a seed
nor a retrievable neighbour, so no IMPORTS pair can ever appear in `retrieve()` output on that
fixture. New fixture `dirs_out` (constants `DIRS_LEAF` / `DIRS_ROOT` / `DIRS_SHAPES`) gives each
module a module-level table so every file node carries a chunk. `rag_repo` itself is untouched.

#### The new test

`test_ac13_retrieve_keeps_every_edge_direction(dirs_out)` — literal `(node_id, why)` membership,
hand-written from the fixture source, never computed from the code under test. No score, no
rank, no float, no ordering:

```
retrieve("widget inventory ledger snapshot", k=3, hops=1) contains
    ("file:pkg2/leaf.py",                  "lexical")
    ("sym:pkg2/leaf.py::leaf_helper",      "DEFINES out of leaf.py")   <- dropped by the bug
    ("file:pkg2/root.py",                  "IMPORTS in of leaf.py")    <- dropped by the bug
retrieve("tessellating quadrilateral primitive", k=3, hops=1) contains
    ("sym:pkg2/shapes.py::Polygon",        "lexical")
    ("sym:pkg2/shapes.py::Polygon.area",   "DEFINES out of Polygon")   <- dropped by the bug
    ("sym:pkg2/shapes.py::Square",         "INHERITS in of Polygon")   <- dropped by the bug
```

#### Regression proof (revert / restore)

`repo2graph/query.py:307` `edge_dirs=ALL_EDGE_DIRS` was temporarily reverted to
`edge_dirs=DEFAULT_EDGE_DIRS` in the working copy:

```
with DEFAULT_EDGE_DIRS -> 1 failed, 1 passed   (the OLD self-referential AC-13 test still PASSES,
                                                confirming it can never catch this class)
E   AssertionError: {('file:pkg2/leaf.py', 'lexical')}
    assert ('sym:pkg2/leaf.py::leaf_helper', 'DEFINES out of leaf.py') in {('file:pkg2/leaf.py', 'lexical')}
with ALL_EDGE_DIRS restored -> 2 passed
```

`repo2graph/query.py` was restored byte-identically: `git hash-object` is
`0bf469cba14b80ff7b41a2ac5d8ec153f8e9bb50` before and after.

#### Result

```
python -m pytest tests/ -q   -> 205 passed, 2 skipped   (was 204; +1 new test)
python -m ruff check .       -> All checks passed!
```

The `@authormark v1` block in `tests/test_rag.py` is untouched; its `Fingerprint:` remains stale
(canonical CI re-stamp is still the pre-merge gate). No `splitlines()` added.

## Implement

### Iteration 1

**Status handed off: TEST** — all 11 planned tasks are implemented and 52 of the 54 new
tests are green, but two assertions in `tests/test_rag.py` are unsatisfiable by any correct
implementation (see "Blocking test defect" below). Per contract §3 the tests were not edited.

#### Files created / changed

| File | Change | Why |
|---|---|---|
| `repo2graph/query.py` | 4-tuple adjacency `(dst, type, direction, edge)`; `_load_text` / `_load_manifest` + `self.overview` / `self.manifest`; `_boost_identifiers` + `IDENT_BOOST`; `score_rrf` + `_vectors_for` + `_cosine`; `expand(min_confidence, edge_dirs)`; `retrieve(*, min_confidence=None)`; `map_prepend()` + `pack_context()`; helpers `_first`, `_fit_lines`, `_compress`, `_cite_block`; constants `IDENT_BOOST`, `RRF_K`, `RRF_CANDIDATES`, `DEFAULT_EDGE_DIRS`, `DEFAULT_EDGE_TYPES`, `MAP_BUDGET_FRAC`, `MAP_ENTRYPOINTS`, `PACK_SEPARATOR`, `QUALNAME_SEP_RE` | Tasks 1-5 |
| `repo2graph/cli.py` | `import math`; `_unit_float`; `RAG_TARGET_HELP`; `_rag_index_dir`; `cmd_rag`; `rag` subparser | Tasks 6-7 |
| `repo2graph/answer.py` | NEW — `pick_provider`, `build_prompt`, `_request`, `_delta`, `_writer`, `_flush`, `stream_answer` | Tasks 8-9 |
| `repo2graph/export.py` | 2 new `HOW_TO_READ` entries (GraphRAG protocol; `pack_context` / `repo2graph rag`) | Task 10 |
| `pyproject.toml` | `optional-dependencies.rag = ["sentence-transformers>=3.0", "numpy>=1.24"]` | Task 11 |

#### Test output

```
python -m pytest tests/test_rag.py -q   -> 2 failed, 52 passed
python -m pytest tests/ -q              -> 2 failed, 202 passed, 2 skipped
python -m ruff check .                  -> All checks passed!
```

The 150 pre-existing tests in `tests/test_repo2graph.py` are untouched and all still pass
(AC-12 / AC-13 / AC-27 back-compat proof, and risk R1 is clear: `retrieve()` passes
`min_confidence=0.0` so no CALLS neighbour it reached before disappears).

Green by criterion: AC-1..AC-27 and AC-29..AC-34 all pass. AC-28 is the only gap.

#### Blocking test defect (why Status is TEST, not REVIEW)

`tests/test_rag.py:747` and `tests/test_rag.py:790`:

```python
assert PACK["markdown"] in body      # body = req.data.decode("utf8", "replace")
```

`PACK["markdown"]` contains literal `\n` newline characters. `body` is the HTTP request
body of an LLM chat-completions call, which is `application/json`. JSON **must** escape a
newline inside a string as the two characters `\` + `n` (RFC 8259 §7 — U+000A is a control
character and cannot appear raw). So the raw multi-line markdown can never be a substring of
any valid JSON body, for OpenAI, Ollama, Gemini or Anthropic alike. The only way to make the
assertion pass would be to send a non-JSON body, which no provider accepts.

Everything else those two tests check already passes on the current implementation; the
failure is at that one line, after the host and grounding-phrase assertions have succeeded.
Verified manually:

```
answer.stream_answer(PACK, out=sink)
  -> returns 'Hello world', sink == 'Hello world'
  -> seen[0].full_url == 'https://api.openai.com/v1/chat/completions'
  -> json.loads(seen[0].data)['messages'][1]['content'] contains PACK['markdown']  # True
```

Suggested minimal test fix (TEST agent's call, not made here):

```python
sent = json.loads(body)                       # or json.loads(captured[0])
assert PACK["markdown"] in json.dumps(sent)   # escaped form
# or, decoding the transported value:
assert any(PACK["markdown"] in json.dumps(sent)[0:0] or PACK["markdown"] in m["content"]
           for m in sent["messages"])
```

i.e. assert the markdown survives *transport* (`in json.dumps(payload)` after re-encoding, or
`in sent["messages"][-1]["content"]`) rather than appearing raw in the wire bytes. AC-28's
intent ("the pack's markdown reaches the endpoint") is fully met either way.

#### Deviations from the plan

- **No numpy import at all** (plan task 3 said "numpy imported lazily inside this branch").
  Cosine similarity is 12 lines of pure Python (`query._cosine`) that works on plain lists and
  on numpy arrays alike, so the optional dependency is never needed even on the RRF path. This
  strengthens AC-10/AC-11 rather than weakening them; `rag` extra still ships numpy because
  `sentence-transformers` needs it.
- **`pack_context` never compresses a seed.** Per D2 seeds are full-text-or-skipped. Added one
  rule the plan left implicit: a neighbour is only ever emitted if at least one seed was
  emitted, otherwise a tiny budget could produce a pack of pure neighbours (AC-19a).
- `map_prepend()` was extracted as a public method rather than an inline block, so the map can
  be inspected without building a whole pack. Uses `-` not an em dash in its entry-point list
  to avoid adding new non-ASCII to a cp1252 console path.
- `--min-conf -0.1` is accepted by argparse as a value (not an option) because argparse's
  `_negative_number_matcher` matches `-0.1` and the `rag` parser defines no negative-number-like
  option string; the `_unit_float` range check then rejects it, so AC-25 holds.

#### Repo rules

- No `@authormark v1` block was deleted, edited, reordered or relocated. `query.py`, `cli.py`
  and `export.py` now have **stale `Fingerprint:` lines** — expected; the canonical
  `authormark check` CI action re-stamps them. **Pre-merge gate.**
- `repo2graph/answer.py` is new: its 5-line header was copied byte-exact (zero-width payload
  intact) from `repo2graph/layout.py`, so it currently carries layout.py's fingerprint. It
  must be re-stamped by the canonical tool before merge. No local `.authormark/authormark.mjs`
  was used or recovered.
- No `splitlines()` anywhere in the new code — `_fit_lines`, `_compress` and `split_pack`'s
  contract all use `src.split("\n")`; AC-34's `ast.walk` probe is green for both files.
- No subprocess reads git output in this diff.

### Iteration 3

Loop-back from REVIEW iter 1. Fixed the one BLOCKING finding plus the four SHOULDs the
orchestrator named. No test file was edited. No other scope.

#### B-1 — `retrieve()` no longer inherits `DEFAULT_EDGE_DIRS` (RESOLVED)

`repo2graph/query.py`: new module constant next to `DEFAULT_EDGE_DIRS`

```python
ALL_EDGE_DIRS: dict = {}   # expand() reads dirs.get(etype); missing == no filter
```

and `retrieve()` now calls
`self.expand(seen_nodes_list, hops=hops, min_confidence=conf, edge_dirs=ALL_EDGE_DIRS)`.
`expand()`'s own default is untouched (`DEFAULT_EDGE_DIRS` — the blueprint asks for it, and
`pack_context()` still gets it).

Verified against the baseline, not against the implementation: `git worktree add <tmp> 4a3ba03`,
built one shared index over this repo, then ran the *same* seed list through both trees:

```
baseline  idx.expand(seeds, hops=1)
head      idx.expand(seeds, hops=1, min_confidence=0.0, edge_dirs=ALL_EDGE_DIRS)
-> identical: True   (30 tuples, same order, same (dst, etype, direction, src) values)
```

Before the fix the head side of that comparison returned the restricted set, dropping every
DEFINES-out / IMPORTS-in / INHERITS-in neighbour (the reviewer's 41 -> 28). The reviewer's own
probe query now keeps `sym:repo2graph/export.py::_flat` ("DEFINES out of export.py") in the
`query` neighbour set.

Note for VERIFY: `repo2graph query` output is *not* byte-identical to `4a3ba03` for every
query, and cannot be — AC-8's `IDENT_BOOST` deliberately re-ranks seeds whose `name`/`qualname`
matches a query token (`"how does export write manifest"` now seeds the chunks actually named
`write`). That is the planned scoring change, orthogonal to B-1; the *traversal* half, which is
what B-1 was about, is now provably identical.

#### S-1 — cp1252 stdout (RESOLVED, both call sites)

New `cli._emit(text)`: folds to `sys.stdout.encoding` with `errors="replace"` only when the
text does not already encode, then `print()`s. `cmd_rag` uses it for both the markdown and the
JSON branch; `cmd_query` uses it for both of its branches too — it is the identical one-line
change and it does **not** move `cmd_query`'s pinned output, because on a UTF-8-capable stdout
`_emit(x)` is exactly `print(x)` (AC-13's `printed == format_pack(...) + "\n"` still holds; full
suite green).

Reviewer's reproduction, re-run: `sys.stdout` swapped for a cp1252 `TextIOWrapper`,
`main(["rag","-o",idx,"never remove the authormark watermark header"])` on this repo's own
index — was `UnicodeEncodeError: 'charmap' codec can't encode characters in position
3557-3734`, now writes 24257 bytes containing `### [cite:`.

#### S-2/S-3/S-4 — `answer.py` error paths (RESOLVED)

- `_ollama_base(value)`: defaults a missing scheme to `http://`, requires `http`/`https` plus a
  netloc, else `SystemExit`. `OLLAMA_HOST=127.0.0.1:11434` -> `http://127.0.0.1:11434/api/chat`;
  `file:///etc/passwd` -> `OLLAMA_HOST must be an http(s) URL or host:port, got '...'`.
- `stream_answer` wraps the request: `urllib.error.HTTPError` -> `_http_error()` ->
  `SystemExit("openai returned HTTP 401: {\"error\":{\"message\":\"bad key\"}}")`; other
  `URLError`/`OSError`/`ValueError` -> `SystemExit("openai request failed: <urlopen error
  connection refused>")`. `HTTPError.url` is never echoed (S-5's leak vector).
- Empty answer: unparsed lines are kept (`ERROR_SNIFF_LINES = 8`) and, if no text was streamed,
  `_empty_answer()` surfaces a top-level `error` key —
  `SystemExit("ollama returned an error: model 'x' not found")` — or otherwise says no answer
  text arrived and names the provider env var. No more silent `""` with exit code 0.

#### S-5 — Gemini key out of the URL (RESOLVED)

`?alt=sse&key=<KEY>` -> `?alt=sse` plus the `x-goog-api-key: <KEY>` header, which Google
supports. Verified: the key no longer appears in `Request.full_url`. The model name is also now
`urllib.parse.quote(model, safe="")` (this was N-4, but it is one expression on the same line
and removing the key made the truncation risk moot to leave half-fixed).

#### S-6 — provider/endpoint disclosure (RESOLVED, disclosure half only)

`_disclose()` prints, to **stderr**, before the request:
`repo2graph: sending 149 chars of repository context to provider openai at api.openai.com
(selected by OPENAI_API_KEY)`. Only the hostname, never the URL (no credential). stdout stays
the answer payload and stays pipeable. The other halves of S-6 (a `--provider` flag, skipping
dotfile/secret-ish paths from the pack) are **not** done — IMPROVE backlog.

#### Required TEST follow-up (not done here — implement may not edit tests)

AC-13's `tests/test_rag.py:414-417` asserts `cmd_query` stdout `== format_pack(Index(rag_out).
retrieve(...)) + "\n"`, i.e. the implementation against itself. It stayed green through B-1 and
would stay green through any future traversal-default change. It needs a value-pinning
assertion: capture the `(node_id, why)` list for two fixture queries as literals in the test —
e.g. that a `DEFINES out of <file>` and an `IMPORTS in of <module>` neighbour are present for a
seed file node — so narrowing `retrieve()`'s traversal fails loudly. Same weakness, smaller,
in `tests/test_rag.py:212-225` (`compressed_form` mirrors `query._compress`).

#### Deferred to IMPROVE (untouched, per the loop-back instruction)

S-7 (github/`--answer` branch coverage), S-8 (non-numeric `confidence` TypeError), S-9
(`vectors=` path), S-10 (vacuous AC-19a), S-11 (duplicated authormark payloads — pre-merge
gate, must not be hand-fixed), N-1..N-3, N-5..N-8.

#### Test output

```
python -m pytest tests/ -q   -> 204 passed, 2 skipped
python -m ruff check .       -> All checks passed!
```

Same counts as before the fixes; no test changed status, none was edited.

#### Repo rules

No `@authormark v1` block was deleted, edited, reordered or relocated; no local re-stamp was
run. `query.py`, `cli.py`, `answer.py` now carry stale `Fingerprint:` lines on top of the
already-flagged staleness — the canonical `authormark check` re-stamp remains the pre-merge gate
for five files (`query.py`, `cli.py`, `export.py`, `answer.py`, `tests/test_rag.py`). No
`splitlines()` added. No subprocess reads git output in this iteration's diff.

## Review

### Iteration 1

Reviewed the real `git diff 4a3ba03..worktree` for `pyproject.toml`, `repo2graph/cli.py`,
`repo2graph/export.py`, `repo2graph/query.py` plus the two untracked new files
`repo2graph/answer.py` and `tests/test_rag.py`. Every claim below was reproduced by running
code, not read off the `## Implement` summary.

**Unresolved BLOCKING findings: 1**

#### AGENTS.md bug-class sweep (all four)

1. **Authormark headers — no violation, one item to carry.** Diffed lines 1-5 of `query.py`,
   `cli.py`, `export.py` against `4a3ba03`: byte-identical, nothing deleted, edited, reordered
   or relocated. (`query.py`/`cli.py` carry a 56-char line-1 payload vs 234 elsewhere — that is
   **pre-existing at the baseline**, not caused by this diff.) Stale `Fingerprint:` lines on the
   three edited files are expected and are *not* flagged. See S-11 for the new-file duplication.
2. **`splitlines()` — clean.** No `.splitlines(` call in `query.py`, `answer.py`, `cli.py` or
   `tests/test_rag.py`. `_fit_lines`, `_compress` and the test helper `split_pack` all use
   `text.split("\n")`, each with the AGENTS.md reference in a comment. AC-34's `ast.walk` probe
   is the right shape (a grep would false-positive on the quoted rule).
3. **git subprocess decoding — clean, nothing new.** The diff adds no `subprocess` call at all.
   `cmd_rag` shells out only *indirectly*: `_rag_index_dir` → `graph.build()` (→
   `walker._git_files`) and → `fetch.index_github()`. Both are the established
   `-c core.quotepath=false` + bytes + `.decode("utf8","surrogateescape")` + `timeout=` call
   sites, unmodified. No `text=True`, no locale `encoding=` anywhere in the diff.
4. **Windows / cp1252 — one real defect (S-1).** All new file I/O is correct:
   `query.py:125` and `:134` open with `encoding="utf8", newline="\n"`, matching `read_jsonl`.
   `answer.py:_writer` is genuinely byte-safe. But `cli.py:141`'s `print(pack["markdown"])` is
   not — see S-1, reproduced.

---

#### BLOCKING

**B-1 — `retrieve()` is NOT behaviour-identical: `expand()`'s new default direction filter
silently drops neighbours from `repo2graph query`. `repo2graph/query.py:239` (+ call site
`repo2graph/query.py:296-297`).**

`expand()` now applies `dirs = DEFAULT_EDGE_DIRS if edge_dirs is None else edge_dirs`, i.e.
`DEFINES: ("in",)`, `IMPORTS: ("out",)`, `INHERITS: ("out",)`. Before the diff `expand()`
followed **both** directions of every edge type. `retrieve()` threads `min_confidence=conf`
(correctly neutralised to `0.0`) but passes **no** `edge_dirs`, so it inherits the new
restrictive default. This contradicts the plan's Non-goal ("No change to `retrieve()`'s
observable output for existing callers"), D1 ("No other change"), and AC-13 ("`main(["query",
...])` output is byte-identical before and after"). Risk R1 predicted exactly this class and
asked VERIFY to diff `query` output; the confidence half was mitigated, the direction half was
not.

Concrete scenario, reproduced on repo2graph's own index (`build . -o idx --formats jsonl`,
then `Index(idx).retrieve(q, k=8, hops=1)`), comparing HEAD against the pre-diff traversal
(simulated exactly by `expand(..., min_confidence=0.0, edge_dirs={})`):

```
repo2graph query "how does export write manifest" -o idx
  lost from output: sym:repo2graph/export.py::_flat   ("DEFINES out of export.py")
repo2graph query "double a value helper" -o idx
  lost from output: sym:tests/test_repo2graph.py::sample_graph ("DEFINES out of test_repo2graph.py")
```

At the `expand()` level the loss is much larger — for the seed set of
`"how does export write manifest"`, 41 neighbour nodes before vs 28 after; for
`"double a value helper"`, 48 before vs 27 after. Every symbol defined in a seed **file** node
(DEFINES out), every importer of a seed module (IMPORTS in) and every subclass of a seed class
(INHERITS in) is now unreachable from `query`.

Why the suite is green anyway: AC-13's test
(`tests/test_rag.py:397-417`) asserts `cmd_query` stdout `== format_pack(idx.retrieve(...))` —
that compares the implementation to itself and can never detect a change in `retrieve`'s
behaviour. AC-12 only asserts `any(h["why"] != "lexical")`, which survives.

Fix (both halves needed):
- `retrieve()` must call `expand(..., min_confidence=conf, edge_dirs=ALL_EDGE_DIRS)` where
  `ALL_EDGE_DIRS = {}` is a named module constant (an empty mapping makes `dirs.get(etype)`
  return `None`, which `query.py:254` already treats as "no direction filter"). Verified: with
  `edge_dirs={}` the pre-diff neighbour sets are reproduced exactly on all four probe queries.
  `pack_context()` keeps the `DEFAULT_EDGE_DIRS` default — the blueprint asks for it there.
- Replace the self-referential AC-13 assertion with one that pins the *values*: capture the
  neighbour `node_id`/`why` set for two fixture queries as literals in the test, so a future
  traversal-default change fails loudly.

---

#### SHOULD

**S-1 — `cli.py:141` `print(pack["markdown"])` raises `UnicodeEncodeError` on a redirected
Windows stdout.** `answer.py` goes to real trouble to be cp1252-safe; the default (no
`--answer`) output path does not. On Windows a redirected/piped stdout is a cp1252
`TextIOWrapper` with `errors="strict"`. Reproduced verbatim by swapping `sys.stdout` for
`io.TextIOWrapper(io.BytesIO(), encoding="cp1252")` and running
`main(["rag","-o",idx,"never remove the authormark watermark header"])` on **this repo's own
index**:
`UnicodeEncodeError: 'charmap' codec can't encode characters in position 3557-3734`.
So `repo2graph rag "..." > pack.md` — the documented agent workflow — dies on Windows for any
repo containing a non-ASCII source byte. Note this defect is **pre-existing** in
`cmd_query` (`cli.py:92` `print(format_pack(res))` crashes the same way at the baseline), which
is why it is SHOULD rather than BLOCKING; one shared helper (`sys.stdout.reconfigure` /
`errors="replace"` write, or reuse `answer._writer`) fixes both call sites. The `windows-latest`
CI leg exists for exactly this class and does not catch it because pytest captures in UTF-8.

**S-2 — `answer.py:85` `OLLAMA_HOST` without a URL scheme gives a raw traceback.** Ollama's own
documentation and `ollama serve` use `OLLAMA_HOST=127.0.0.1:11434`. That value produces
`"127.0.0.1:11434/api/chat"`, and `urlopen` raises `URLError: unknown url type: 127.0.0.1`
(confirmed with the equivalent `localhost:11434`). The only test uses
`f"http://127.0.0.1:{port}"`, so the common form is untested. Default a missing scheme to
`http://` and validate the scheme is `http`/`https` (that also closes `file://`/`ftp://` as an
accidental urlopen target).

**S-3 — `answer.py:165` has no error handling around `urlopen`.** An expired key (401), a wrong
model (404), a proxy failure or a 300 s timeout all propagate as a bare traceback out of
`main()`. Every other user-facing failure in this CLI is a `SystemExit(str)` (`parse_formats`,
`_require_index`, `_unit_float`, `_rag_index_dir`). Wrap in
`except (urllib.error.URLError, OSError, ValueError)` → `SystemExit` naming the provider and
status, and make sure the message does **not** include `req.full_url` (see S-5).

**S-4 — silent empty answer: `answer.py:92-114` + `cli.py:135-138`.** `_delta` returns `""` for
anything it cannot parse, and `stream_answer` prints nothing and returns `""`. A provider that
answers HTTP 200 with an error object (Ollama does this for an unknown model:
`{"error":"model 'x' not found"}`) therefore produces **no output and exit code 0** — the user
cannot tell an empty answer from a failed one. Emit a `SystemExit`/stderr note when `parts` is
empty, or surface a top-level `"error"` key from the payload.

**S-5 — `answer.py:81` puts the Gemini API key in the URL query string.** `?alt=sse&key=<KEY>`
lands in every intermediate proxy/CDN access log, in `Request.full_url`, and in
`HTTPError.url` — so any future `except ... as e: raise SystemExit(f"{e.url}")` (see S-3) would
print the key. Google supports the `x-goog-api-key` header; use it. The other three providers
correctly keep the credential in a header. No provider endpoint is redirectable by untrusted
input (three hosts are hardcoded constants; only `ollama`'s comes from the env, which is the
user's own configuration, not repository content) — that part of the design is sound.

**S-6 — `rag --answer` uploads whatever is in `chunks.jsonl`, including secrets, with no
disclosure of where.** Reproduced: build an index over a repo containing `.env` and
`config.yml` and `chunks.jsonl` carries
`"# file: .env (, 2 lines)\nAWS_SECRET_ACCESS_KEY=hunter2supersecret"` verbatim — non-source
files are chunked whole. A query like `rag "where is the aws secret key set?" --answer` will
seed that chunk and POST it to a third-party endpoint. Nothing prints which provider/endpoint
was selected, so the user cannot see that `GEMINI_API_KEY` left over in their shell won the
precedence race and received their repository. Minimum: print the chosen provider + host to
stderr before the first byte is sent; better: a `--provider` flag, and skip dotfile/secret-ish
paths from the pack when `--answer` is on. (`--answer` itself is correctly gated — `answer` is
imported lazily and only inside `if args.answer:`; no request is possible without the flag.)

**S-7 — two new branches ship with zero test coverage.**
(a) `cli.py:118-121`, the GitHub-spec target branch of `_rag_index_dir`. Plan risk R7 promised
"assert the GitHub branch by monkeypatching `fetch.index_github`" and no such test exists
(`grep index_github tests/test_rag.py` → no hits). A wrong kwarg there would ship silently;
`index_github(spec, outdir, ..., formats=...)` happens to line up, but nothing proves it.
(b) `cli.py:135-138`, the `--answer` wiring. `stream_answer` is tested directly, but nothing
tests that `--answer` reaches it, and — more important for a command that POSTs source code —
there is **no negative test** proving that *without* `--answer` no `urlopen` happens. Add a
`monkeypatch.setattr(urllib.request, "urlopen", boom)` around a plain `rag` invocation.

**S-8 — `query.py:256` crashes on a non-numeric `confidence`.** `edge.get("confidence", 1.0) <
min_confidence` raises `TypeError: '<' not supported between 'NoneType' and 'float'` for an
`edges.jsonl` line carrying `"confidence": null` (a hand-edited, older-version or
third-party-produced index). Everything else in `Index.__init__` degrades gracefully
(`_load_manifest` deliberately swallows a truncated manifest); this one path aborts the whole
query. Coerce with a small `_conf(edge)` helper that falls back to `1.0` on a non-`(int,float)`
value. The **default itself is right**: `graph.py:318,322` always writes `confidence` on
`CALLS`, so the `1.0` default only fires for foreign/legacy records, where "keep the edge" is
the back-compatible choice. The gate is also correctly scoped — `etype == "CALLS"` guards it,
`CALLS_EXTERNAL` is a different type and is not in `DEFAULT_EDGE_TYPES` at all, and AC-5 pins
that DEFINES/INHERITS survive `min_confidence=1.0`.

**S-9 — `score_rrf(vectors=...)` is untested, unreachable and ambiguous.** AC-11 exercises only
the `embedder=` path. The `vectors=` path (`query.py:_vectors_for`) has no test, no CLI route
(`cmd_rag` never passes `vectors`/`embedder`), and an undocumented dual-keyspace contract: it
indexes `vectors[i]` by *chunk index* while also probing `vectors.get("query")` by string key —
a caller passing a `list` silently degrades to `return base` with no signal. Either test it or
drop it from this slice; as written it is speculative surface.

**S-10 — `tests/test_rag.py:480-486` (AC-19a) can pass vacuously.** The assertion lives inside
`if any(w != "seed" for w in whys):`; if no budget in `range(400, 6000, 200)` ever admits a
neighbour, the test asserts nothing and passes. And when it does fire, `assert "seed" in whys`
is nearly tautological. Add a pre-assertion that at least one budget in the sweep produced a
mixed pack (its sibling `test_ac19_a_squeezed_neighbour...` does this correctly with a
`pytest.fail` fallthrough).

**S-11 — new-file watermarks duplicate another file's payload and fingerprint (pre-merge
gate).** Verified byte-for-byte: `repo2graph/answer.py` line 1 zero-width payload and
`Fingerprint: AMK1.Ys7vixE7rqEUQP1KKMrDG6` are identical to `repo2graph/layout.py`'s;
`tests/test_rag.py` carries `AMK1.pdfhGbDrl5PDge0OEgTSnS`, identical to
`tests/test_repo2graph.py`'s. Two distinct files now assert the same fingerprint, so the header
is currently *misleading* rather than merely stale, and AC-33's shape-only test cannot detect
it. Per AGENTS.md this must not be hand-fixed and the header must not be deleted. The pre-merge
canonical `authormark check` re-stamp must cover **five** files: `repo2graph/answer.py` and
`tests/test_rag.py` (new, currently bearing a foreign payload) and `repo2graph/query.py`,
`repo2graph/cli.py`, `repo2graph/export.py` (edited, stale — expected).

---

#### NICE

- **N-1 `cli.py:243` `--budget 0` means "unbounded".** `_nonneg` accepts `0`, and
  `pack_context` treats `budget_chars <= 0` as no limit. The help text says "character budget
  for the whole pack", so a user typing `--budget 0` expecting minimal output gets the entire
  pack — and with `--answer`, uploads it. Say so in the help string.
- **N-2 mirrored-implementation tests.** `tests/test_rag.py:212-225` (`compressed_form`)
  re-implements `query._compress` and then asserts the code matches the re-implementation; the
  same-shaped bug in both would pass. Same pattern in AC-13 (see B-1). Both would be stronger
  with a literal expected string.
- **N-3 citation forgery.** A source line that itself begins with `### [cite: fake.py:1-1]`
  lands verbatim in the pack, and `SYSTEM_PROMPT` tells the model to copy paths out of
  `### [cite: ...]` headers. Indent or fence chunk bodies, or prefix body lines.
- **N-4 `answer.py:80-81` interpolates `--model` unescaped into the Gemini URL path.** Cannot
  change the host (it is after the authority), but `--model 'x#'` truncates the query string and
  drops the key. `urllib.parse.quote(model, safe="")` costs nothing.
- **N-5 `cli.py:117-121`** calls `parse_spec(target)` purely for validation and throws the
  result away; `index_github` parses it a second time. Harmless duplication.
- **N-6 `answer.py:126-131`** writes UTF-8 bytes straight to `sys.stdout.buffer`, bypassing the
  text layer. On a cp1252 console that is mojibake (`cafÃ©`) rather than the `caf?` the
  `errors="replace"` branch would give, and it can interleave ahead of anything already buffered
  in `sys.stdout`. AC-30 only requires "no exception", which it meets.
- **N-7 target-resolution ambiguities** (`cli.py:103-122`), all benign but worth a doc line: a
  local directory literally named `owner/repo` wins over the GitHub spec (dir check precedes the
  spec check); a source repo that happens to contain its own `agent/manifest.json` is taken for
  an index and then fails with the `has no chunks.jsonl` message rather than being built. A
  trailing separator is fine (`Path` normalises it), and a repo containing a `.r2g/`
  sub-directory resolves correctly to the build branch (verified).
- **N-8 `Index.__init__`** now always reads `overview.md` + `manifest.json`, including for
  `query`/`stats`, which never use them. Accepted as R4; a lazy `@property` would remove the
  cost entirely.

---

#### Verified sound (checked, no finding)

- **Budget accounting holds.** Fuzzed `len(pack_context(q, budget_chars=b)["markdown"]) <= b`
  for **every** `b` in `1..599` plus `{1000, 2000, 5000, 24000, 100000}` on the repo2graph index:
  zero violations, and `used_chars == len(markdown)` throughout. The arithmetic is right at the
  edges too: `budget=1` → `int(0.2) - len(PACK_SEPARATOR) = -7` → `_fit_lines` returns `""` →
  `head == ""` → no block fits → `markdown == ""`. An empty `full_map` does not falsely set
  `truncated` (`_fit_lines("", n) == ""`). A single oversized chunk is skipped, never truncated
  mid-line, and sets `truncated`. `budget_chars <= 0` is unbounded and documented. Blocks are
  re-rendered with the same `_cite_block` after the sort, so the charged and emitted lengths
  cannot diverge.
- **Confidence gate scoping** — see S-8; correctly `CALLS`-only, correct default.
- **`--min-conf` validation** — `_unit_float` rejects `nan`/`inf`/`-inf`/out-of-range/garbage/
  empty; the `nan` case matters exactly as the docstring says (`x < nan` is always False, i.e. a
  silently disabled filter).
- **`--answer` gating** — lazy import, only under `if args.answer:`.
- **`export.py` / `pyproject.toml`** — additive only; `HOW_TO_READ` entries are accurate about
  the protocol they document, core `dependencies` untouched.
- **No dead code of substance.** `_first` has exactly one caller, `_cosine`/`_fit_lines`/
  `_compress`/`_cite_block` each one or two; `map_prepend` is deliberately public.

Status set to IMPLEMENT for B-1 (one focused change in `retrieve()` plus a non-self-referential
AC-13 assertion). S-1 is the strongest non-blocking item and is cheap to fix in the same pass.

### Iteration 2

Scoped re-review, not a fresh pass. Reviewed the real `git diff 4a3ba03..worktree` and, within
it, only what changed since REVIEW iteration 1: `repo2graph/query.py` (`ALL_EDGE_DIRS` +
`retrieve()` call site), `repo2graph/cli.py` (`_emit` and its two call sites),
`repo2graph/answer.py` (`_ollama_base`, `_http_error`, `_empty_answer`, `_disclose`, the Gemini
header move, the `stream_answer` try/except) and `tests/test_rag.py` (`dirs_out` fixture +
`test_ac13_retrieve_keeps_every_edge_direction`). Every claim below was reproduced by running
code. Suite re-run here: `205 passed, 2 skipped`; `ruff check .` clean.

**Unresolved BLOCKING findings: 0**

---

#### B-1 — RESOLVED, root cause, verified independently

`repo2graph/query.py:48` adds `ALL_EDGE_DIRS: dict = {}` and `:305-307` `retrieve()` now calls
`self.expand(..., min_confidence=conf, edge_dirs=ALL_EDGE_DIRS)`. This is the root-cause shape,
not a patch that relocates the problem: `expand()`'s own default (`DEFAULT_EDGE_DIRS`) and the
`dirs.get(etype) is None → no filter` semantics at `query.py:259-261` are untouched, so
`pack_context()` still gets the blueprint's directional selectivity while `retrieve()` — the
only pre-existing caller — opts out explicitly and by name. No second caller of `expand()` was
left inheriting the narrowed default (`grep`: `expand(` has exactly two call sites,
`retrieve()` and `pack_context()`).

Reproduced the regression guard myself, in-process, by rebinding `query.ALL_EDGE_DIRS` to
`DEFAULT_EDGE_DIRS` (no file edited) on a freshly built `dirs_out`-equivalent index:

```
HEAD      file query  -> {('file:pkg2/leaf.py','lexical'),
                          ('sym:pkg2/leaf.py::leaf_helper','DEFINES out of leaf.py'),
                          ('file:pkg2/root.py','IMPORTS in of leaf.py')}
REVERTED  file query  -> {('file:pkg2/leaf.py','lexical')}
HEAD      class query -> {Polygon lexical, Polygon.area DEFINES out, Square INHERITS in}
REVERTED  class query -> {Polygon lexical}
```

So all three previously-dropped directions are back, and the new test is a genuine detector, not
a restatement of the implementation: `tests/test_rag.py:504-514` asserts six literal
`(node_id, why)` pairs hand-derived from `DIRS_LEAF`/`DIRS_ROOT`/`DIRS_SHAPES`, with no score,
rank, ordering or float, and nothing computed from the code under test. The `dirs_out` fixture
is justified — `rag_repo`'s imported modules leave under `chunks.py:185`'s residue threshold, so
no file chunk exists there and an `IMPORTS in` pair is unreachable by construction. The old
self-referential AC-13 assertion was kept alongside, which is correct: it still pins the
`cmd_query → format_pack` contract that the new test does not cover.

#### S-1 .. S-6 — verified fixed

- **S-1 `_emit`** (`cli.py:32-46`, used by `cmd_rag` markdown+json and `cmd_query` markdown+json).
  Reproduced: `sys.stdout` swapped for a cp1252 `TextIOWrapper`, `_emit("café — ✓ 你好")` writes
  `b'caf\xe9 \x97 ? ??\n'` instead of raising. Pinned `cmd_query` output is not moved — under
  pytest's UTF-8 `capsys` the `text.encode(enc)` probe succeeds and `_emit(x)` is exactly
  `print(x)`; `tests/test_repo2graph.py` and AC-13 are green unchanged. Two narrow defects it
  introduces are S-12/S-13 below; neither is blocking.
- **S-2 `_ollama_base`** (`answer.py:99-114`): `127.0.0.1:11434 → http://127.0.0.1:11434`,
  `localhost → http://localhost`, `http://h:1/ → http://h:1`; `file:///etc/passwd`, `''` and
  `//h:3` all `SystemExit` with the value echoed. Uppercase `HTTP://X:2` passes (urlsplit
  lower-cases the scheme) — correct.
- **S-3 HTTPError/URLError** (`answer.py:252-255`): `HTTPError(401)` →
  `SystemExit("openai returned HTTP 401: {\"error\":\"bad key\"}")`, and the API key is **not**
  in the message (asserted `"sk-leak" not in str(exc)`). `HTTPError` is caught before its
  `URLError`/`OSError` superclasses — ordering is right.
- **S-4 `_empty_answer`** (`answer.py:191-212`): a 200 carrying `{"error":"model 'x' not found"}`
  now exits with `ollama returned an error: model 'x' not found` instead of printing nothing at
  exit 0.
- **S-5 Gemini key** (`answer.py:83-91`): URL is `...:streamGenerateContent?alt=sse`, key only in
  the `x-goog-api-key` header; `Request.full_url` contains no key (verified). urllib's header
  capitalisation to `X-goog-api-key` is HTTP-case-insensitive and fine.
- **S-6 `_disclose`** (`answer.py:215-225`): stderr only, hostname only, before the request —
  `repo2graph: sending 149 chars ... to provider openai at api.openai.com (selected by
  OPENAI_API_KEY)`. stdout stays the answer payload. The remaining halves of S-6 stay deferred.

---

#### SHOULD (new this iteration)

**S-12 — the `LookupError` branch in `_emit`/`_writer` re-raises the exception it catches.**
`cli.py:40-44` and `answer.py:160-163`: `except (UnicodeEncodeError, LookupError): text =
text.encode(enc, "replace")...` — if `enc` is an unknown codec, the handler runs `encode(enc, ...)`
again and raises the same `LookupError`, so catching it buys nothing. Reproduced: a stdout stand-in
with `encoding = "cp0"` (the value CPython reports on Windows when the ANSI code page is 0) gives
`LookupError('unknown encoding: cp0')` out of `cli._emit`. Fix is one line — fall back to
`"utf8"` (or ASCII+replace) inside the handler instead of reusing `enc`.

**S-13 — `_emit` discards the stream's own error handler, losing byte fidelity on POSIX.**
`cli.py:38-45` folds on a *strict* `text.encode(enc)` probe regardless of `sys.stdout.errors`.
Under `LC_ALL=C` CPython gives stdout `encoding='ansi_x3.4-1968', errors='surrogateescape'`, so a
surrogate-escaped path (walker decodes with `surrogateescape` by design) regresses:

```
baseline print(...) -> b'caf\xe9/x.py\n'      # byte round-trips
_emit(...)          -> b'caf?/x.py\n'         # replaced
```

`repo2graph query -o idx > out.txt` on a Linux box with a non-UTF-8 filename therefore loses the
original bytes it used to preserve. Guard the fold with
`if getattr(sys.stdout, "errors", "strict") in (None, "strict")`.

**S-14 — `quote(model, safe="")` breaks the fully-qualified Gemini model form.** `answer.py:88`.
Google's own listing API returns names as `models/gemini-2.5-flash`, and the endpoint path is
`/v1beta/models/{model}:streamGenerateContent`. `--model models/gemini-2.5-flash` now yields
`/v1beta/models/models%2Fgemini-2.5-flash:...` → HTTP 404 (surfaced by S-3's new handler, so it
fails loudly, which is why this is not blocking). `safe="/"` keeps N-4's `#`/`?` truncation fix
and accepts both forms.

**S-15 — everything fixed this iteration except B-1 ships with zero tests.** `grep -n
"_ollama_base\|_emit\|_disclose\|_empty_answer\|_http_error\|goog" tests/` → no hits. That covers
five new error paths and a security fix (the key moving out of the URL), all of which are exactly
the kind of code that silently rots. `_emit` is the sharpest gap: it sits on `cmd_query`'s pinned
output path, and because pytest's `capsys` is UTF-8 the fold branch is never entered by any test,
so a cp1252 regression there is invisible to the whole suite including the `windows-latest` leg —
the same blind spot that let S-1 ship. Cheap additions: `_emit` against a cp1252 `TextIOWrapper`,
`_ollama_base` parametrised over the schemeless/`file://` cases, an `x-goog-api-key`-present /
`key=` absent assertion on the Gemini `Request`, and a `stream_answer` 200-with-error case.

#### NICE (new this iteration)

- **N-9 `query.py:48`** — `ALL_EDGE_DIRS: dict = {}` is a mutable module global read at call time;
  anything that mutated it would silently re-narrow `repo2graph query`. `MappingProxyType({})` or
  a local `{}` literal removes the hazard at zero cost.
- **N-10 `tests/test_rag.py:126, :496`** — the comment and docstring name the fixture `dirs_repo`;
  it is `dirs_out`. Stale name in otherwise excellent documentation.
- **N-11 `answer.py:243-255`** — the `except ... (OSError, ValueError)` wraps the whole streaming
  loop, including `write(piece)`, so a broken pipe on stdout is reported as
  `openai request failed: ...`. Narrow the try to the `urlopen(...)`/iteration, or re-raise when
  the failure came from the writer.

#### Deferred, unchanged (IMPROVE backlog — not re-litigated)

S-7 (github/`--answer` branch coverage), S-8 (non-numeric `confidence` TypeError), S-9
(`vectors=` path), S-10 (vacuous AC-19a), S-11 (duplicated authormark payloads — pre-merge gate,
must not be hand-fixed), N-1, N-2 (`compressed_form` mirror; the AC-13 half of N-2 is now fixed),
N-3, N-4 (superseded by S-14), N-5, N-6, N-7, N-8. Plus the unfinished halves of S-6
(`--provider` flag, skipping secret-ish paths from the pack when `--answer` is on).

#### Repo-rule sweep on the new code

Headers untouched (lines 1-5 of `query.py`/`cli.py`/`answer.py` byte-identical to their state at
REVIEW iter 1); no `splitlines()` added; no new subprocess; `_emit` and `_writer` are the only new
encode paths and both are covered above. The canonical `authormark check` re-stamp remains a
pre-merge gate for five files (`query.py`, `cli.py`, `export.py`, `answer.py`, `tests/test_rag.py`).

Status set to VERIFY: zero unresolved BLOCKING findings.

## Verify

### Iteration 1

Independent end-to-end check. Nothing below is taken from an earlier phase's report: every
command was re-run here, and every acceptance criterion was re-proved by running code against
**this repository's own index**, not only against the fixtures. Two scratch probes were used
(`verify_ac.py`, `verify_ac2.py`, in the session scratchpad — no repo file was written except
this one).

#### 1. Gate commands

| Command | Result |
|---|---|
| `python -m pytest tests/ -q` | **205 passed, 2 skipped** in 8.28s |
| `python -m ruff check .` | **All checks passed!** |
| skip reasons (`-rs`) | both are pre-existing: `could not import 'networkx'` at `tests/test_repo2graph.py:475` and `:483` |
| typecheck | **N/A** — the repo configures no typechecker (`pyproject.toml` has ruff + pytest only; `dev = ["pytest>=7","ruff>=0.5"]`; `python -c "import mypy"` → ModuleNotFoundError) |
| install / console script | `pip show repo2graph` → 0.1.0 installed; `repo2graph --help` → `usage: repo2graph [-h] {build,github,gh,query,rag,map,stats} ...` (the `rag` subcommand is live on the installed entry point) |
| wheel build | `python -m pip wheel . --no-deps` **fails** — setuptools flat-layout discovery sees both `repo2graph` and `tests`. **Pre-existing, not a regression:** reproduced identically from a `git worktree` at `4a3ba03` (`ERROR: Failed to build 'file:///.../r2gbase'`). Noted for the IMPROVE backlog, not counted against this diff. |

**Optional-dependency import check (the hard requirement).** Two ways, both clean:

```
python -c "import repo2graph.query, repo2graph.answer, repo2graph.cli, repo2graph.export; ..."
  -> optional modules imported: []          # numpy IS installed in this env and is still not imported
  -> sentence_transformers installed? False # so the package genuinely imports without it

# and with the imports hard-blocked at the builtins.__import__ level:
BLOCK = {'numpy','sentence_transformers','torch'}   -> raise ImportError
  -> "imports OK with numpy/sentence_transformers blocked"
```

`repo2graph.answer` and `repo2graph.query` both import clean with the optional extras
unavailable. (IMPLEMENT's deviation — a 12-line pure-Python `_cosine` instead of numpy — means
the RRF path never needs numpy either; confirmed at AC-11 below: `numpy not in sys.modules`
after `score_rrf(embedder=stub)`.)

#### 2. Smoke test — the real CLI on this repository

```
$ repo2graph build . -o $TMP/.r2g --formats jsonl,overview
  files 41  nodes 592  edges 1925  chunks 484   (0.48s)
  -> agent/ human/ chunks.jsonl edges.jsonl manifest.json nodes.jsonl overview.md stats.json

$ repo2graph rag -o $TMP/.r2g "where is the CALLS confidence set"      rc=0
  line 0 : "# Repo map: repo2graph"          <- map first
  line 59: "---"                             <- bare separator
  line 61: "### [cite: BUILD_STATE.md:1-1306] `BUILD_STATE.md` (seed)"
  headers, in emission order:
    BUILD_STATE.md:1, README.md:1, repo2graph/answer.py:215,
    repo2graph/export.py:443, repo2graph/graph.py:352,
    repo2graph/query.py:85, repo2graph/query.py:234        <- sorted by (path, start_line)
  len(markdown) = 23891 <= 24000 (default budget); stderr empty
```

Budget sweep on the same real index (`pack_context`, `used_chars == len(markdown)` throughout):
`200 -> 29`, `1000 -> 180`, `4000 -> 3973`, `24000 -> 23891`. `--budget 3000` via the CLI gives
`used_chars 2862 / budget 3000, truncated True`.

```
$ repo2graph rag -o $TMP/.r2g "..." --format json
  keys = budget_chars, chunks, markdown, neighbors, query, seeds, truncated, used_chars   (parses)

$ repo2graph rag /e/Github/repo2graph "how does pack_context bound the budget" -o $TMP/r2gauto/idx
  rc=0, no pre-existing index -> auto-built: $TMP/r2gauto/idx/agent/{manifest.json,...} created,
  pack printed. (Console is cp1252 and the em dash rendered as "?" via cli._emit -- no crash.)

$ repo2graph rag -o $TMP/.r2g "..." --no-expand --format json
  neighbors = 0, whys = ['seed']            <- strictly lexical seeds

$ repo2graph query -o $TMP/.r2g "double a value helper"
  unchanged shape: "--- tests/...::test_iss25_query_constants_and_budget_bounds [lexical]" ...
```

Ablation on the real repo (set membership, no scores), `k=3, budget 40000`:

| query | lexical-only ids | with 1-hop graph | superset | examples the graph adds |
|---|---|---|---|---|
| `pick_provider` | 3 | 17 | strict | `sym:repo2graph/answer.py::_delta`, `::_disclose`, `file:repo2graph/answer.py` |
| `mark_entrypoints reach` | 3 | 7 | strict | `sym:repo2graph/graph.py::build`, `file:repo2graph/walker.py` |
| `normalize_provider` | 3 | 5 | strict | `sym:repo2graph/query.py::Index.score`, `::tokenize` |

**`--answer` with no provider env, and the no-network proof.**

```
$ env -u GEMINI_API_KEY -u OPENAI_API_KEY -u ANTHROPIC_API_KEY -u OLLAMA_HOST \
    repo2graph rag -o $TMP/.r2g "..." --answer
  rc=1, stdout empty, stderr:
  no LLM provider configured: set one of GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY or OLLAMA_HOST
```

Network was blocked at the socket layer for the whole process (`socket.socket.connect`,
`connect_ex`, `socket.getaddrinfo` and `socket.create_connection` all replaced with raisers):

```
plain rag (no --answer)  -> rc 0, 240 chars of markdown, network calls: []
rag --answer, env clear  -> SystemExit naming all four vars, network calls: []
```

So a plain `rag` makes **no** DNS lookup and **no** connect, and the no-provider path exits
before any socket is touched.

#### 3. Behaviour comparison vs baseline `4a3ba03` (risk R1 / D1)

A `git worktree` at the baseline was used to run the *old* code against the *same* index, with
`PYTHONIOENCODING=utf8` so both sides could print.

On the pinned `sample_repo` fixture (materialised from `PKG_INIT`/`PKG_UTIL`/`PKG_MAIN` in
`tests/test_repo2graph.py`), `repo2graph query` stdout is **byte-identical baseline vs HEAD** for
all 10 probes: `double a value helper`, `main entry point`, `sample repository`, `util`,
`double`, `greet`, `run`, `add numbers`, `helper function`, `package init` — `0 differ`.

On repo2graph's own (much larger) index, 3 of 5 probes are byte-identical
(`double a value helper`, `confidence calls edge`, `session auth`); the two that differ
(`how does export write manifest`, `build the graph`) differ **only** because `IDENT_BOOST`
re-ranks a seed whose `name` equals a query token (`write`, `build`) — which is exactly what
AC-8 requires. The traversal half is unchanged: neighbours of the new seeds are the same kinds
(`DEFINES in/out`, `CALLS in/out`, `IMPORTS in`), i.e. `ALL_EDGE_DIRS` is doing its job and B-1
stays fixed. Independently re-confirmed at AC-13 below.

Incidental but real finding, in HEAD's favour: running the **baseline** `repo2graph query` on
this repo's index with a native cp1252 stdout dies with
`UnicodeEncodeError: 'charmap' codec can't encode characters in position 4427-4604` at
`cli.py:93 print(format_pack(res))`. HEAD does not — S-1's `cli._emit` fixed a pre-existing
crash as well as the new one.

#### 4. Acceptance criteria — AC-1 .. AC-34

Every line below is a value observed in this session. "probe" = the scratch script;
"suite" = the named test, which I also re-ran individually.

| AC | Verdict | Proof |
|---|---|---|
| 1 | **Met** | probe: `adj["sym:repo2graph/answer.py::pick_provider"]` entry = `(dst=sym:...::stream_answer, type=CALLS, dir=in, conf=1.0)` — a 4-tuple whose 4th element is the edge record with a readable `confidence` |
| 2 | **Met** | probe: `Index(out).overview` len 1935, starts `'# Repo map: repo2graph'`; after unlinking overview.md from **every** `layout.paths(...)` copy, `Index(dir).overview == ""` and no exception |
| 3 | **Met** | probe: `Index.manifest` keys `['approximations','chunk_fields','counts','edge_types','entrypoint_rule','entrypoints']`; manifest truncated to `"{"` → `manifest == {}`, constructs fine; manifest deleted → `{}` |
| 4 | **Met** | probe on 80 real seeds: `expand(min_confidence=1.0)` = 26 tuples, **0** CALLS tuples backed by an edge record with `confidence < 1.0` |
| 5 | **Met** | probe: the same `min_confidence=1.0` call still yields 2 DEFINES/INHERITS/IMPORTS neighbours (those records carry no `confidence` key and are untouched); suite `test_ac5_...` green on the INHERITS fixture |
| 6 | **Met** | probe: `min_conf=0.5` → 36 distinct nodes vs `1.0` → 26; superset `True`, non-equal |
| 7 | **Met** | probe: observed `(type,direction)` pairs `[('CALLS','in'),('CALLS','out'),('DEFINES','in')]` with `DEFAULT_EDGE_DIRS = {'CALLS':('out','in'),'DEFINES':('in',),'INHERITS':('out',),'IMPORTS':('out',)}`; suite `test_ac7_default_edge_directions` green |
| 8 | **Met** | probe on the real repo: `score("pick_provider")[0]` → `sym:repo2graph/answer.py::pick_provider` (score 21.32 vs 7.21 runner-up); suite `test_ac8_..._beats_a_lexical_decoy` green on the decoy fixture |
| 9 | **Met** | probe: `score("confidence calls edge")` → 414 results, strictly descending, **every** returned index shares a token with the query; `test_score_matches_bruteforce` unmodified and green |
| 10 | **Met** | probe: `score_rrf(q) == score(q)` exactly with no vectors/embedder; `sys.modules` contains no `numpy` / `sentence_transformers` after importing the package (and the blocked-`__import__` run above imports clean) |
| 11 | **Met** | probe: stub `.encode(list[str]) -> list[list[float]]` → 59 fused results, `'numpy' not in sys.modules` still True |
| 12 | **Met** | probe: `retrieve("how does export write the manifest", k=3, hops=1)` → 6 hits, whys `['CALLS out of write','DEFINES in of write','lexical']` (≥1 non-lexical); `test_index_retrieves_and_expands` unmodified and green |
| 13 | **Met** | probe: `inspect.signature(Index.retrieve)` positional order `['self','query','k','hops','budget_chars']`, `ALL_EDGE_DIRS == {}`; **and** `repo2graph query` stdout byte-identical baseline-vs-HEAD on the `sample_repo` fixture for 10 queries (§3). On a large repo the ranking of *seeds* moves for identifier queries by AC-8's design — see the note under §3; traversal is identical, which is what D1/B-1 concern |
| 14 | **Met** | probe on the real index: `200→29, 1000→180, 4000→3973, 24000→23891`, all `<= N`, `used_chars == len(markdown)` in every case |
| 15 | **Met** | probe: 7 cite headers matched `^### \[cite: ([^\]]+):(\d+)-(\d+)\] `; every `(path,start,end)` is present in `result["chunks"]` |
| 16 | **Met** | probe: header keys `[('BUILD_STATE.md',1),('README.md',1),('repo2graph/answer.py',215),('repo2graph/export.py',443),...]` == `sorted(keys)` |
| 17 | **Met** | probe: line 0 `'# Repo map: repo2graph'`, a bare `---` at line 59, first `### [cite:` at line 61; with overview.md (all copies) **and** manifest.json deleted, `pack_context` still returns markdown containing `### [cite:` and raises nothing |
| 18 | **Met** | probe: `map_prepend()` `## Top entry points` first line = ``- `cmd_rag` - repo2graph/cli.py (reach 98)``; `manifest["entrypoints"][0]["qualname"] == "cmd_rag"` |
| 19 | **Met** | probe: at `budget=800` every emitted block has `why=="seed"` and `truncated is True` (seeds prioritised). Compression: at `budget=1500` the neighbour `sym:repo2graph/answer.py::build_prompt` is emitted as its `#` header lines + `def build_prompt(pack) -> tuple[str, str]:` and **0** further non-blank body lines, `truncated is True`. (This is the real check REVIEW's S-10 said the suite's AC-19a could do vacuously — done here non-vacuously, on the real repo.) |
| 20 | **Met** | probe: `expand_graph=False` → `neighbors == []`, `{why} == {'seed'}`; CLI `--no-expand --format json` → same |
| 21 | **Met** | probe, set membership only: `pick_provider` lexical set (3) ⊂ graph set (17), strict; the extras include `sym:repo2graph/answer.py::_delta` reached over CALLS, not lexically. Suite `test_ac21_...` green on the fixed synthetic fixture. No score/rank asserted anywhere |
| 22 | **Met** | probe: `main(["rag","-o",out,"how does session auth work?"])` (one positional) → rc 0, 5869 chars, contains `### [cite:`; CLI smoke same |
| 23 | **Met** | probe: `main(["rag", <index dir>, "some query"])` → rc 0 and `agent/manifest.json` `st_mtime_ns` unchanged (`1789070172448178500` before and after — no rebuild); `main(["rag", <source dir>, ..., "-o", new])` → rc 0 and `new/agent/manifest.json` created |
| 24 | **Met** | probe: `["rag","not/a real spec/x","q"]` → `SystemExit: cannot resolve target 'not/a real spec/x': expected one of: a repo2graph index directory (one holding agent/manifest.json), a source repository directory to ind...` — names all three forms. `["rag","q","-o",<empty>]` → `SystemExit: no index at <empty>: run \`repo2graph build <repo> -o <empty>\`` — the existing message verbatim |
| 25 | **Met** | probe: `_unit_float` accepts `0/0.5/1.0` → `[0.0,0.5,1.0]`; rejects `nan, inf, -inf, -0.1, 1.1, abc, ""` with `ArgumentTypeError` each (→ argparse exit 2); suite parametrised `test_ac25_bad_min_conf_exits_non_zero[nan/inf/-0.1/1.1/abc]` green and asserts stderr names `--min-conf` |
| 26 | **Met** | probe with a spy on `Index.pack_context`: a bare `rag` call passes `{'k': 8, 'hops': 1, 'budget_chars': 24000, 'min_confidence': 1.0, 'expand_graph': True}`; `--format json` prints a parseable object with `markdown`, `chunks`, `truncated`, `used_chars` (plus `seeds`, `neighbors`, `budget_chars`, `query`) |
| 27 | **Met** | probe: `query` rc 0 (24662 ch), `stats` rc 0 (301 ch), `map` rc 0 (174 ch), `build` exercised by the smoke build; the unmodified 150-test `tests/test_repo2graph.py` is green inside the 205 |
| 28 | **Met** | probe with `urllib.request.urlopen` swapped for a recorder and `env={"OPENAI_API_KEY":"sk-dummy"}`: captured host `api.openai.com` (no real socket — the recorder is the only exit), the **entire** 2829-char `pack["markdown"]` present in `body["messages"][-1]["content"]`, and the system turn contains `path/file.py:start-end`, `cite` and `invent`. SSE deltas streamed to the sink (`'café — hi'`, folded to cp1252 by the writer). Suite additionally runs the real urllib path against a `127.0.0.1:0` `HTTPServer` |
| 29 | **Met** | probe: `pick_provider({}) is None`; `stream_answer(pack, env={})` → `SystemExit: no LLM provider configured: set one of GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY or OLLAMA_HOST` — all four named. Confirmed end-to-end through the installed CLI (rc 1, §2) |
| 30 | **Met** | probe: `_writer(TextIOWrapper(BytesIO(), encoding="cp1252"))("café — ✓ 你好")` → `b'caf\xe9 \x97 ? ??'`, no exception; `_writer(None)` against a `sys.stdout` stand-in with a `.buffer` → `b'caf\xc3\xa9 \xe2\x80\x94 \xe2\x9c\x93 \xe4\xbd\xa0\xe5\xa5\xbd'`, no `UnicodeEncodeError` |
| 31 | **Met** | probe: `pyproject.toml` contains exactly `rag = ["sentence-transformers>=3.0", "numpy>=1.24"]`; `project.dependencies` is still exactly `tree-sitter>=0.23` + `tree-sitter-language-pack>=0.7` (`git diff 4a3ba03 -- pyproject.toml` = 3 added lines, all under `[project.optional-dependencies]`) |
| 32 | **Met** | probe: `export.HOW_TO_READ` mentions both `pack_context` and `confidence`; the **freshly built** `$TMP/.r2g/agent/manifest.json` carries it under `how_to_read` |
| 33 | **Met** | probe: lines 1-5 of `repo2graph/answer.py` and `tests/test_rag.py` match the shape — `@authormark v1` / `Copyright (c)` / `Author:` / `SPDX-License-Identifier: MIT` / `Fingerprint: AMK1.…`. See §5 for the pre-merge re-stamp gate |
| 34 | **Met** | probe: `ast.walk` over `query.py`, `answer.py`, **plus** `cli.py` and `tests/test_rag.py` → zero `Call`/`Attribute` nodes named `splitlines`. The only textual hits are the AGENTS.md rule quoted in comments/docstrings |

**34 / 34 Met. 0 Not met.**

#### 5. AGENTS.md repo-rule sweep across the whole diff vs `4a3ba03`

1. **Authormark blocks — intact.** `git diff 4a3ba03 -- repo2graph pyproject.toml | grep
   "authormark\|Fingerprint\|SPDX\|Copyright (c)"` → **zero hits**: not one header line appears
   in the diff, so nothing was deleted, edited, reordered or relocated. (A naive
   `git show 4a3ba03:<f> | head -5` byte-compare "differs" only by a trailing `\r` — a
   `core.autocrlf` working-tree artifact, absent from the tracked diff.) Both new files carry
   the 5-line block.
   **Carried forward (pre-merge gate, must NOT be hand-fixed):** five files need the canonical
   `authormark check` re-stamp — `query.py`, `cli.py`, `export.py` (edited → stale
   `Fingerprint:`, expected) and `answer.py`, `tests/test_rag.py` (new, currently bearing a
   copied payload/fingerprint from `layout.py` / `test_repo2graph.py` — REVIEW S-11). No local
   `.authormark/authormark.mjs` was used or recovered.
2. **`splitlines()` — clean.** No `.splitlines(` call anywhere in the added code; `query._fit_lines`,
   `query._compress` and the test helper all use `text.split("\n")`, each annotated
   `# never splitlines(): see AGENTS.md`. Verified by `ast.walk`, not grep (AC-34).
3. **git subprocess decoding — clean.** The diff adds **no** `subprocess` call at all
   (`git diff … | grep '^+.*subprocess\|text=True\|encoding=<locale>'` → only the two
   `open(..., encoding="utf8", newline="\n")` artifact reads in `query.py`). `cmd_rag` reaches
   git only indirectly via the unmodified `graph.build()`/`walker._git_files` and
   `fetch.index_github`, which already use `-c core.quotepath=false` + bytes +
   `decode("utf8","surrogateescape")` + `timeout=`.
4. **Windows / cp1252 — safe, and better than baseline.** Every new decode uses
   `errors="replace"`; `cli._emit` and `answer._writer` are the only new encode paths and both
   were exercised: HEAD writes 24k of pack to a cp1252 stdout where **baseline `query` crashes**
   (§3). `urlopen` carries an explicit `timeout=HTTP_TIMEOUT`.

#### 6. Known-deferred, confirmed not regressions

REVIEW's `S-7 … S-15` and `N-1 … N-11` are the deliberate IMPROVE backlog and were not counted
against any criterion. Two I spot-checked and confirm are still *latent, not live*: S-10's
vacuous AC-19a is covered non-vacuously by my own AC-19 probe above, and S-12/S-13's `_emit`
fallback edge cases do not fire on any supported console encoding (`cp1252` folds correctly;
`cp0`/`errors=surrogateescape` remain untested code paths). One item to add to the backlog from
this phase: **`pip wheel .` fails on setuptools flat-layout discovery — pre-existing at
`4a3ba03`, so `pip install repo2graph` from source is broken today** (editable installs work).

RESULT: PASS

## Remember

Durable knowledge in this repo lives in `AGENTS.md` (`CLAUDE.md` is a one-line `@AGENTS.md`
include; there is no `docs/adr/`, no memory dir, and the README is user-facing). All six notes
below were appended to `AGENTS.md`, matching its existing shape — heading, the rule, the why, the
concrete precedent with `file:line`. Nothing that the diff or `git log` already shows was written
down (no file lists, no criteria, no phase history).

| Note (new `AGENTS.md` section) | What it preserves |
|---|---|
| **Two budget models coexist — do not unify them** | Decision D1/D2. `retrieve()` budgets chunk `text` only (`query.py:272`), `pack_context()` budgets the whole markdown (`query.py:349`). Looks like an inconsistency; unifying it breaks the two pinned tests in `test_repo2graph.py` and every `cmd_query` caller. Includes the rule that new retrieval surface goes on `pack_context()` and that `retrieve()` may only gain keyword-only params defaulting to today's behaviour. |
| **A new default on a shared traversal helper narrows its existing callers** | The B-1 bug class, named. `expand(edge_dirs=None → DEFAULT_EDGE_DIRS)` silently cost `retrieve()` every DEFINES-out / IMPORTS-in / INHERITS-in neighbour (41 → 28). The rule: pre-existing callers opt out **by name** (`ALL_EDGE_DIRS`, `query.py:48`), the confidence gate stays `CALLS`-only, and neutrality is proved against a baseline `git worktree`, not against the new code. |
| **Tests must pin values, not compare the implementation to itself** | Why AC-13 could not catch B-1: it asserted `cmd_query` stdout `== format_pack(retrieve(...))`. Rule: literal `(node_id, why)` membership hand-derived from the fixture; no score/rank/float/ordering; prove a new test is a detector by reverting the fix and restoring byte-identically. Also names the surviving `compressed_form` mirror. |
| **A file with little residue emits no file-level chunk** | The `chunks.py:185` 40-char threshold gotcha found while building fixtures: a module that is all imports and defs has a node but no chunk, so it can be neither seed nor neighbour — an IMPORTS edge is then unreachable through `retrieve()` by construction. Explains why `dirs_out` exists and `rag_repo` was left alone. |
| **`layout.path(out, "overview.md")` is `human/overview.md`, not `agent/`** | `overview.md` is the only two-section artifact (`layout.py:45`) and `rel()` returns the first entry. Easy to assume `agent/`; readers must tolerate either copy being absent and use `layout.paths()` for both. |
| **`rag --answer` uploads repository source to a third-party endpoint** | The security posture, not a fix log: what leaves the machine, the provider precedence race, that a `.env` chunk can be seeded and shipped whole, the invariants to keep (lazy import under `if args.answer:`, the socket-level no-network test, host-only stderr disclosure, no credential in a URL, never echo `HTTPError.url`), and the two still-open halves of S-6. |

Deliberately **not** recorded: the AGENTS.md rules already present (headers, `splitlines()`, git
subprocess decoding, dot-dir discovery) — all four were swept clean this run and need no edit; the
acceptance-criteria list; per-iteration history; and the individual S-/N- fixes, which the diff and
`## Review` already carry (they belong to IMPROVE's backlog, not to durable memory).

**Pre-merge gate — now six files, not five.** Editing `AGENTS.md` staled its own `Fingerprint:`
(`AMK1.nh6A0n67aea43Sl8Y0SyxT`), so the canonical `authormark check` re-stamp must cover
`repo2graph/query.py`, `repo2graph/cli.py`, `repo2graph/export.py` (edited, stale),
`repo2graph/answer.py` and `tests/test_rag.py` (new, bearing payloads copied from `layout.py` /
`test_repo2graph.py` — REVIEW S-11), **plus `AGENTS.md`**. Its `@authormark v1` block was not
deleted, edited, reordered or relocated; only body text below it was appended. No local
`.authormark/authormark.mjs` was used.

## Improve

### Loop retrospective

- **What caught the most**:
  The REVIEW phase was the most valuable detector in the loop:
  1. Caught **B-1** (the traversal narrowing bug where `expand()`'s new `DEFAULT_EDGE_DIRS` default leaked into `retrieve()`, silently dropping DEFINES-out, IMPORTS-in, and INHERITS-in neighbours from `repo2graph query`), violating architectural decision D1 and AC-13.
  2. Caught critical operational bugs: cp1252 stdout crash in `print(pack["markdown"])` (S-1), schemeless `OLLAMA_HOST` (S-2), unhandled HTTP/URL errors and empty-200 responses (S-3, S-4), and Gemini API key exposure in query strings (S-5).
  VERIFY phase also provided high value by performing live independent execution against this repo's real index and comparing behavior byte-for-byte against a `4a3ba03` baseline worktree.

- **What missed the most**:
  The TEST phase missed the most across iterations:
  1. Iteration 1 wrote an RFC-8259-impossible assertion (`PACK["markdown"] in body` on a JSON wire payload with escaped newlines), blocking IMPLEMENT iter 1.
  2. Iteration 1 wrote a self-referential test for AC-13 (`cmd_query` output `== format_pack(retrieve(...))`), which masked bug B-1 completely because both sides shifted together under the traversal narrowing.

- **Skill change proposals (do not apply automatically)**:
  1. *Rule for TEST phase*: Test assertions must pin hand-derived literal expectations (e.g. `(node_id, why)` pairs) for traversal/retrieval rather than comparing one layer of the system to another.
  2. *Rule for TEST phase*: When testing network requests carrying JSON payloads, decode the wire JSON before asserting content properties rather than substring-matching raw encoded bytes.

### Code retrospective & quick wins applied

Five low-risk quick wins and cleanups were applied and verified:
1. **S-12**: Fallback in `cli._emit` and `answer._writer` when `stream.encoding` causes a `LookupError` (e.g. `cp0` or unknown codec), falling back to UTF-8 rather than re-raising `LookupError`.
2. **S-13**: In `cli._emit`, preserve stream error handlers when `getattr(sys.stdout, "errors", "strict") not in (None, "strict")` (e.g. `surrogateescape` under `LC_ALL=C`), preserving byte fidelity for non-UTF-8 paths.
3. **S-14**: Support both bare (`gemini-2.0-flash`) and fully-qualified (`models/gemini-2.5-flash`) Gemini model identifiers in `answer._request` without duplicate path segments, using `safe="/"`.
4. **S-15**: Added unit tests in `tests/test_rag.py` covering error and security paths (`_emit` surrogateescape/cp0, `_ollama_base` validation, Gemini header key and model path formatting, `_writer` LookupError, and `stream_answer` error response handling).
5. **N-10**: Fixed doc comment typo in `tests/test_rag.py` referencing `dirs_repo` instead of `dirs_out`.

Suite after quick wins: **210 passed, 2 skipped**, ruff clean.

#### Ranked backlog (All completed)

1. **[P1] Fix `pip wheel . --no-deps` flat-layout packaging** — RESOLVED: Added `[tool.setuptools.packages.find]` directive with `where = ["."]` and `include = ["repo2graph*"]` in `pyproject.toml`. Verified wheel build succeeds.
2. **[P1] S-6: CLI `--provider` flag & secret-filtering in `--answer`** — RESOLVED: Added `--provider` flag to `cli.py`, implemented explicit provider targeting in `answer.py`, and added `exclude_secrets=True` path filtering in `query.py` triggered when `--answer` is active.
3. **[P2] S-8: Robust handling of non-numeric `confidence` values** — RESOLVED: Safely coerce `edge.get("confidence", 1.0)` with `try ... except (ValueError, TypeError) -> 0.0` in `Index.expand`.
4. **[P2] N-3: Citation forgery protection** — RESOLVED: Prefix line-initial `### [cite:` in chunk text with `\` in `query._cite_block()`.
5. **[P2] N-11: Narrow exception handling in `stream_answer`** — RESOLVED: Writer exceptions in `stream_answer()` propagate directly without being wrapped as provider request failures.
6. **[P3] S-7: Integration tests for `repo2graph rag <github-spec>` and end-to-end `--answer` CLI execution** — RESOLVED: Added `test_rag_target_resolution_github_spec` and `test_rag_cli_answer_integration`.
7. **[P3] S-9: Explicit test for precomputed `vectors=` parameter in `Index.score_rrf`** — RESOLVED: Added `test_score_rrf_with_precomputed_vectors`.
8. **[P3] N-1 / N-2: Harden `_compress` test assertions** — RESOLVED: Added `test_compress_pinned_literal_output` pinning exact literal strings without helper mirroring.
9. **[P3] N-8: Make `Index.overview` and `Index.manifest` lazy `@property` accessors** — RESOLVED: Converted both to cached lazy properties on `Index`.

## Loop log

- 2026-09-11 START scenario=feature baseline=4a3ba03 (prior DONE run archived to BUILD_STATE.prev-2026-09-09.md)
- 2026-09-11 PLAN iter=1 PASS — 34 acceptance criteria, 11 ordered tasks, 7 blueprint defects resolved (D1 retrieve back-compat, D2 whole-markdown budget, D3 map from manifest["entrypoints"]); next=TEST
- 2026-09-11 TEST iter=1 PASS — tests/test_rag.py: 54 tests across AC 1-34, 48 fail on absent implementation (pack_context/score_rrf/expand kwarg/answer module/`rag` subcommand), 6 are green-by-design back-compat characterizations; existing 150 tests untouched and passing; next=IMPLEMENT
- 2026-09-11 IMPLEMENT iter=1 BLOCKED -> TEST — all 11 tasks implemented; 202 passed / 2 failed / 2 skipped, ruff clean; AC-28's `assert PACK["markdown"] in body` (test_rag.py:747, :790) is unsatisfiable: a JSON request body must escape newlines, so raw multi-line markdown can never be a substring of the wire bytes; next=TEST
- 2026-09-11 LOOP-BACK IMPLEMENT -> TEST (iteration 2): tests/test_rag.py:747 and :790 assert PACK["markdown"] in body, but body is a JSON request payload where U+000A is escaped as backslash-n per RFC 8259 §7 — unsatisfiable by any correct implementation. Orchestrator confirmed the defect by reading both assertions.
- 2026-09-11 TEST iter=2 PASS — fixed only tests/test_rag.py:747 and :790: decode the JSON payload and assert `PACK["markdown"] in sent["messages"][-1]["content"]` (the user turn both OpenAI and Ollama bodies carry), keeping AC-28 at full strength; no implementation file and no other test touched; full suite 204 passed / 2 skipped, ruff clean; next=IMPLEMENT
- 2026-09-11 ORCHESTRATOR iter=2 IMPLEMENT gate satisfied without a re-spawn: the loop-back fixed only the two bad assertions, and all 11 implementation tasks were already complete from iteration 1. Verified independently: `python -m pytest tests/ -q` -> 204 passed, 2 skipped; `python -m ruff check .` -> All checks passed. Status advanced to REVIEW.
- 2026-09-11 REVIEW iter=1 BLOCKED — 1 BLOCKING: expand()'s new DEFAULT_EDGE_DIRS default leaks into retrieve() (query.py:239 + 296), so `repo2graph query` loses DEFINES-out / IMPORTS-in / INHERITS-in neighbours (reproduced: "how does export write manifest" drops sym:repo2graph/export.py::_flat; 41 -> 28 expand neighbours), violating D1/AC-13 — AC-13's test compares cmd_query to format_pack(retrieve(...)), which cannot detect it. 11 SHOULD (cp1252 crash in `print(pack["markdown"])`, schemeless OLLAMA_HOST, no urlopen error handling, silent empty answer, Gemini key in query string, secrets in the uploaded pack, untested github/--answer branches, non-numeric confidence TypeError, untested vectors= path, vacuous AC-19a, duplicated authormark payloads in answer.py/test_rag.py) + 8 NICE. AGENTS.md classes all clean (headers untouched, no splitlines, no new subprocess, new file I/O uses encoding="utf8" newline="\n"). Budget invariant fuzz-verified for budgets 1..599 + large: zero violations. next=IMPLEMENT
- 2026-09-11 LOOP-BACK REVIEW -> IMPLEMENT (iteration 3): 1 BLOCKING (query.py:239,:296-297 DEFAULT_EDGE_DIRS leaks into retrieve(), breaking D1/AC-13) plus the highest-value SHOULDs (cp1252 stdout in cmd_rag, answer.py error paths and provider disclosure). Remaining SHOULD/NICE items deferred to the IMPROVE backlog.
- 2026-09-11 IMPLEMENT iter=3 PASS — B-1 resolved: new `ALL_EDGE_DIRS = {}` constant, `retrieve()` passes it explicitly so `DEFAULT_EDGE_DIRS` never narrows `repo2graph query` (proved against a 4a3ba03 worktree: same seeds -> identical 30 expand tuples, same order). Four SHOULDs fixed: `cli._emit` byte-safe stdout for `cmd_rag` + `cmd_query` (cp1252 repro now writes 24257 bytes instead of raising), `answer._ollama_base` scheme defaulting/validation, HTTPError/URLError -> `SystemExit`, empty-200 -> `SystemExit` surfacing the provider `error` key, Gemini key moved to `x-goog-api-key`, and a stderr disclosure of provider+host before any upload. 204 passed / 2 skipped, ruff clean. Required TEST follow-up reported (AC-13 is self-referential and needs value-pinned neighbour assertions). next=REVIEW
- 2026-09-11 TEST iter=3 (AC-13 hardening, scoped follow-up) PASS — added `dirs_out` fixture + `test_ac13_retrieve_keeps_every_edge_direction` pinning six literal `(node_id, why)` pairs including `DEFINES out of leaf.py`, `IMPORTS in of leaf.py` and `INHERITS in of Polygon`; a second fixture was required because `rag_repo`'s imported modules leave <40 chars of residue so chunks.py emits no file chunk for them (an IMPORTS pair is unreachable there). Regression proof: reverting query.py:307 to `edge_dirs=DEFAULT_EDGE_DIRS` fails the new test while the old self-referential AC-13 assertion still passes; `ALL_EDGE_DIRS` restored, query.py byte-identical (hash 0bf469c). Existing AC-13 coverage kept; no implementation file and no other test touched. 205 passed / 2 skipped, ruff clean. next=REVIEW
- 2026-09-11 REVIEW iter=2 PASS — 0 BLOCKING. B-1 confirmed fixed at the root: `ALL_EDGE_DIRS = {}` (query.py:48) passed explicitly by `retrieve()` (query.py:305-307) while `expand()`'s default and `pack_context()`'s directional selectivity are untouched; independently reproduced by rebinding `query.ALL_EDGE_DIRS` to `DEFAULT_EDGE_DIRS` in-process — the three pinned neighbours (DEFINES out, IMPORTS in, INHERITS in) vanish, so `test_ac13_retrieve_keeps_every_edge_direction` is a genuine value-pinned detector, not a mirror. All four SHOULDs verified by execution: `_emit` cp1252 (writes `b'caf\xe9 \x97 ? ??'`, and is a no-op `print` on UTF-8 stdout so cmd_query's pinned output holds), `_ollama_base` (schemeless -> http, file:// rejected), HTTPError/URLError -> SystemExit with no key in the message, `_empty_answer` surfacing Ollama's 200-error, Gemini key out of `full_url` into `x-goog-api-key`, `_disclose` host-only on stderr. 4 new SHOULD (S-12 LookupError branch re-raises itself for a bogus codec e.g. cp0; S-13 `_emit` ignores `sys.stdout.errors`, so a surrogate-escaped path regresses from `b'caf\xe9'` to `caf?` under LC_ALL=C; S-14 `quote(model, safe="")` 404s on `models/gemini-2.5-flash`; S-15 all six new error/security paths ship untested, `_emit` invisibly so because capsys is UTF-8) + 3 new NICE. Prior S-7..S-11 and N-1..N-8 deferred, unchanged. Suite 205 passed / 2 skipped, ruff clean. next=VERIFY
- 2026-09-11 VERIFY iter=1 PASS — 34/34 acceptance criteria Met, re-proved independently against repo2graph's own index (not just fixtures) with two scratch probes; nothing taken from earlier phases' numbers. Gates: `pytest tests/ -q` 205 passed / 2 skipped (both pre-existing networkx skips), `ruff check .` clean, no typechecker configured, `repo2graph --help` shows the `rag` subcommand on the installed entry point. Optional-dependency import proved twice: numpy IS installed yet never enters `sys.modules`, and all four modules import with `numpy`/`sentence_transformers`/`torch` blocked at `builtins.__import__`. CLI smoke on this repo: build 41 files/592 nodes/1925 edges; `rag` pack = map at line 0, bare `---` at 59, first `### [cite:` at 61, headers sorted by (path,start_line), len 23891<=24000; budgets 200/1000/4000/24000 -> 29/180/3973/23891 all within bound with used_chars==len(markdown); `--format json` parses (8 keys); source-dir target with no index auto-builds; `--no-expand` -> neighbors=0, whys={'seed'}; ablation strict superset (pick_provider 3 -> 17 ids, adds `sym:repo2graph/answer.py::_delta`). `--answer` with all four env vars unset exits 1 naming GEMINI_API_KEY/OPENAI_API_KEY/ANTHROPIC_API_KEY/OLLAMA_HOST, and with `socket.connect`/`getaddrinfo`/`create_connection` all replaced by raisers a plain `rag` records ZERO network calls. D1/R1 diffed against a `4a3ba03` worktree: `repo2graph query` stdout byte-identical on the pinned sample_repo for all 10 probe queries; the two differing large-repo probes differ only where IDENT_BOOST re-ranks a seed named by the query (AC-8 by design), traversal identical. AC-19 compression verified non-vacuously (S-10's gap): at budget 1500 `::build_prompt` emits header lines + `def build_prompt(pack) -> tuple[str, str]:` and 0 further body lines, truncated=True. AGENTS.md sweep clean: zero header lines in the tracked diff, zero `splitlines()` (ast.walk over query/answer/cli/test_rag), zero new subprocess, all new decodes `errors="replace"` — and HEAD's `cli._emit` fixes a pre-existing baseline crash (`UnicodeEncodeError` at `cli.py:93` on a native cp1252 stdout). Pre-merge gate carried forward: canonical `authormark check` re-stamp for five files (query.py, cli.py, export.py stale; answer.py, tests/test_rag.py bearing copied payloads — S-11). New backlog item: `pip wheel .` fails on setuptools flat-layout discovery — reproduced at 4a3ba03, pre-existing, not a regression. next=REMEMBER
- 2026-09-11 REMEMBER iter=1 PASS — six durable notes appended to AGENTS.md (the repo's only knowledge store; CLAUDE.md just includes it), in its existing rule/why/precedent voice: (1) the two coexisting budget models and why unifying retrieve()'s text-only accounting with pack_context()'s whole-markdown accounting breaks pinned back-compat (D1/D2); (2) the B-1 bug class named — a new default on a shared traversal helper (DEFAULT_EDGE_DIRS) silently narrows existing callers, so pre-existing callers must opt out by name via ALL_EDGE_DIRS and neutrality is proved against a baseline worktree; (3) tests must pin literal (node_id, why) values, never compare the implementation to itself, and a new test is proved a detector by reverting the fix; (4) chunks.py:185's 40-char residue threshold means a defs-only module has a node but no chunk, so IMPORTS edges are unreachable through retrieve() on such a fixture (why dirs_out exists); (5) layout.path(out,"overview.md") resolves to human/, not agent/; (6) `rag --answer` uploads repository source (including a whole-chunked .env) to a third-party endpoint — the gating and disclosure invariants to keep. Nothing already in AGENTS.md, in the diff or in git history was restated. Editing AGENTS.md stales its own Fingerprint, so the canonical authormark re-stamp gate is now SIX files (adds AGENTS.md); its header block was not touched. next=IMPROVE
- 2026-09-11 IMPROVE iter=1 PASS — retrospective and ranked backlog recorded; 5 low-risk quick wins/cleanups applied: S-12 (LookupError fallback to UTF-8 in cli._emit and answer._writer), S-13 (preserve sys.stdout.errors handler in cli._emit), S-14 (accept bare and models/-prefixed Gemini model strings with safe="/"), S-15 (added unit tests for error/security paths in tests/test_rag.py), N-10 (fixed stale fixture name dirs_repo -> dirs_out in test comments). Gates clean: 210 passed / 2 skipped, ruff clean. Status -> DONE.
- 2026-09-11 BACKLOG TANDEM SUBAGENTS PASS — All 9 backlog items executed in parallel across 3 subagent tracks: Track 1 (pyproject.toml wheel build fix, S-8 confidence coercion, N-3 citation forgery disarming, N-8 lazy overview/manifest, S-6 exclude_secrets path filter), Track 2 (cli.py --provider flag + exclude_secrets wiring, answer.py provider selection + N-11 writer exception isolation), Track 3 (test hardening for S-7, S-9, N-1/N-2, S-8, N-3, S-6, N-11). Full suite: 224 passed / 2 skipped, ruff clean, pip wheel cleanly built. Status -> DONE.

