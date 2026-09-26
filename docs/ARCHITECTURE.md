# Architecture

A map of the codebase for people changing it. [TECHNICAL.md](../TECHNICAL.md) explains how
repo2graph works for people *using* it — the pipeline, the graph model, where it guesses. This page
is the other half: which module owns what, which direction dependencies run, and where a change of
a given kind belongs.

Read [AGENTS.md](../AGENTS.md) alongside it. This page says where the code is; AGENTS.md says which
parts of it will bite you.

---

## 1. The shape of it

27 modules, ~15,500 lines, two required third-party packages (`tree-sitter`,
`tree-sitter-language-pack`). No graph library — degree counting, force-directed layout and
GraphML/Cypher generation are all plain Python, on purpose.

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
   ┌────▼───┐  ┌────▼───┐  ┌────▼────┐  ┌──────▼──────┐
   │ cli.py │  │ mcp.py │  │explain  │  │  answer.py  │
   └────────┘  └────┬───┘  └─────────┘  └─────────────┘
                    │
              ┌─────▼────────┐
              │http_server.py│  ── auth.py, audit.py, cache.py, tasks.py
              └──────────────┘
```

The rule the layout enforces: **data flows one way**. Parse knows nothing about graphs; graph knows
nothing about chunks or retrieval; retrieval knows nothing about the CLI or MCP. Anything that
breaks that direction is a bug in the change, not a missing feature.

---

## 2. Module reference

Sizes are a rough guide to where the complexity is, not a target.

### Core pipeline

| Module | Lines | Owns |
|---|---:|---|
| `parse.py` | 1,517 | File discovery (`discover`, `_git_files`, `_walk_files`, `explain_path`), the language table (`LANG_CFG`, `EXT_LANG`), tree-sitter invocation, symbol and import extraction, `_callee_name`. |
| `graph.py` | 1,679 | Node and edge construction; call, import and inheritance resolution including the scoped-resolution tiers; `CO_CHANGE` from git history; entrypoint marking. |
| `chunks.py` | 329 | Cutting source into retrieval units, one per symbol plus a file residual; the `_lines()` helper every slicer must use. |
| `query.py` | 904 | `Index`: BM25 scoring, RRF fusion, `expand()` graph traversal, `retrieve()`, `pack_context()`. The whole retrieval layer, used identically by CLI, MCP and the Action. |
| `export.py` | 1,269 | Every artifact writer — JSONL, GraphML, Cypher, manifest, overview — plus the `.r2g` directory layout (`SECTIONS`, `path()`, `atomic_write`). |
| `viz.py` | 887 | `graph.html`: force-directed layout, the self-contained HTML template, escaping. |

### Surfaces

| Module | Lines | Owns |
|---|---:|---|
| `cli.py` | 1,416 | Argument parsing and every subcommand. The widest module by fan-out — it imports 15 others. |
| `mcp.py` | 1,148 | The MCP server: tool schemas, handlers, argument clamping, both SDK generations. |
| `http_server.py` | 883 | HTTP transport for MCP, including the Host/Origin checks. |
| `explain.py` | 324 | `explain edge` / `node` / `retrieval`. |
| `answer.py` | 507 | `rag --answer` only — the one network path in the package. |

### Support

| Module | Lines | Owns |
|---|---:|---|
| `auth.py` | 753 | Bearer and OIDC, JWKS fetching, `rsa_verify`. No intra-package imports. |
| `doctor.py` | 698 | Environment and index diagnostics. |
| `secrets.py` | 531 | Credential patterns and content scanning. A leaf — imported by five modules, imports none. |
| `audit.py` | 417 | Audit log sink. |
| `integrity.py` | 374 | Index verification: `valid` / `corrupt` / `stale` / `incompatible` / `partial`. |
| `fetch.py` | 366 | `repo2graph github` — clone, build, clean up. |
| `embed.py` | 307 | Vectors, and a stdlib-only `.npy` reader/writer. |
| `lock.py` | 299 | Build locking and stale-lock recovery. No intra-package imports. |
| `tasks.py` | 268 | Background `--async-build` state. |
| `changelog.py` | 234 | The per-push structural graph diff in `human/CHANGELOG.md`. |
| `cache.py` | 205 | The MCP result cache. No intra-package imports. |
| `events.py` | 157 | Structured event sink. |

### Compatibility shims — not modules

`langs.py` (5 lines), `walker.py` (25), `layout.py` (27) are **re-export shims**, not
implementations:

```python
"""Compatibility shim — walker is merged into parse.py."""
from .parse import DEFAULT_SKIP_DIRS, MAX_BYTES, _git_files, discover, ...
```

`langs` → `parse`, `walker` → `parse`, `layout` → `export`. Documentation and AGENTS.md still refer
to `walker.discover()` and `layout.path()` because those are the names the behaviour is known by;
the code lives in `parse.py` and `export.py`. **New code should import from the real module.**
These three are what issue #318 means by "stale compatibility scaffolding" — deleting them is a
breaking change for any external caller, so they are deliberate, not forgotten.

---

## 3. Two import cycles you will meet

Both are real, both are worked around with local imports, and both are the reason a "just import
it" refactor fails.

### `export.py` ↔ `viz.py`

`export.py` does `from .viz import ... write_html` to emit `graph.html` as one of its artifacts.
`viz.py` needs the node and edge descriptions that live in `export.py` — and cannot import them.
So `viz.py:42` keeps a hand-synced copy (`NODE_TYPE_DESC`, `EDGE_TYPE_DESC`) with a comment saying
exactly this.

They have already drifted. Fixing it means a **third leaf module** both can import, not deleting
one copy — see issue #349 and starter task 6 in [good-first-issues.md](good-first-issues.md).

### `mcp.py` ↔ `http_server.py` ↔ `tasks.py`

`mcp.py` imports `http_server` for the HTTP transport; `http_server` imports `mcp` for the
handlers. `tasks` imports `mcp`, and `mcp` imports `tasks`. This is the concrete reason issues
#295 and #315 propose splitting `mcp.py` into `server.py` / `tools.py` / `transport.py`: the tool
handlers and the transports are separable, and once they are, the cycle disappears.

**If you add a module, make it a leaf.** `secrets.py`, `auth.py`, `cache.py` and `lock.py` import
nothing from the package, which is why they are the easiest modules here to change safely.

---

## 4. Where a change goes

| You want to… | Start at | Also touch |
|---|---|---|
| Add a language | `parse.py` — `LANG_CFG`, `EXT_LANG` | A fixture, and the language row in all six READMEs (a test enforces this) — see [parser-development.md](parser-development.md) |
| Change how a call resolves | `graph.py` — the scoped-resolution tiers | `tests/test_scoped_resolution.py`, `tests/test_resolution_heuristics.py` |
| Add an edge kind | `graph.py`, then `export.py`'s `EDGE_TYPES`, then `viz.py`'s copy | `docs/reference.md`, and `query.py`'s `DEFAULT_EDGE_DIRS` if it should be traversable |
| Change retrieval | `query.py` — **`pack_context()`, not `retrieve()`** | AGENTS.md "Two budget models coexist"; `retrieve()` is a pinned back-compat surface |
| Add an MCP tool | `mcp.py` — `TOOL_DESCRIPTIONS`, `TOOL_SCHEMAS`, a handler | Clamp every numeric argument **in the handler**; `docs/mcp.md` (a test enforces the doc) |
| Add a CLI flag | `cli.py` | `docs/cli.md` and the README command table (both enforced by `tests/test_doc_consistency.py`) |
| Add an artifact | `export.py` — a writer plus a `SECTIONS` entry | `manifest.json`'s description map; `docs/reference.md` |
| Add an Action input | `action.yml` | `docs/github-action.md` (enforced); mind the shell/expression truthiness trap in AGENTS.md |
| Touch anything reading git output | — | AGENTS.md "Decoding git subprocess output" — bytes plus `surrogateescape`, never `text=True` |
| Slice source text by line | — | AGENTS.md "use `split("\n")`, never `splitlines()`" — reuse `chunks._lines()` |

---

## 5. The invariants worth knowing before you start

Each of these has cost someone a debugging session. Full versions in [AGENTS.md](../AGENTS.md).

1. **`split("\n")`, never `splitlines()`** when slicing source against parser row numbers.
   `splitlines()` also breaks on U+2028/U+2029/U+0085/`\x0b`/`\x0c`, desynchronising every
   subsequent line number.
2. **Never `text=True` on a git subprocess.** Windows is cp1252; a non-ASCII path raises inside the
   error handler. Capture bytes, `.decode("utf8", "surrogateescape")`, always set `timeout=`.
3. **`retrieve()` and `pack_context()` mean different things by `budget_chars`, on purpose.** Do not
   unify them. New surface goes on `pack_context()`.
4. **A new default on a shared traversal helper narrows its existing callers.** Every pre-existing
   caller must opt out *by name* — that is what `ALL_EDGE_DIRS = {}` exists for.
5. **Tests pin literal values, never a value the code under test computed.** Prove a new test is a
   detector: revert the fix, watch it fail, restore, confirm `git hash-object` is unchanged.
6. **Every MCP tool argument is caller-hostile.** Bounds go in the handler, not in `serve()`, so
   direct callers, `dispatch()` and the stdio server all inherit them.
7. **A file with little residue emits no file-level chunk** (`chunks.py:185`, 40-character floor).
   A synthetic fixture of pure `def`s cannot express an IMPORTS edge through `retrieve()`.

---

## 6. Tests

`tests/` is 37 files. The ones that will fail on a change you did not expect to be load-bearing:

| File | Guards |
|---|---|
| `test_doc_consistency.py` | Every CLI subcommand and every `LANG_CFG` grammar appears in the README and `docs/cli.md`; every Action input in `docs/github-action.md`; every MCP tool in `docs/mcp.md`. |
| `test_i18n_consistency.py` | The same grammar list across all six READMEs, plus the positioning contract in [POSITIONING.md](../POSITIONING.md). |
| `test_compat.py` | Byte-identical `query`/`rag` output against a pinned baseline; the Action's input/output contract; the `action.yml` shell-vs-expression truthiness gate. |
| `test_version_surfaces.py` | The version string in every surface that carries it, and that the bump script covers all of them. |
| `test_encoding.py` | The cp1252 / non-ASCII path class that has caused every historical regression here. |

Run the suite with `python -m pytest`. CI additionally runs `ruff check .`, **`ruff format --check .`**
and `mypy repo2graph` — the format check is a separate gate from the lint check and is easy to miss
locally.
