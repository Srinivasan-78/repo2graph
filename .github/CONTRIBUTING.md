# Contributing

Thanks for helping out.

**Start here depending on what you're doing:**

| | |
|---|---|
| Looking for something to work on | **[Good first issues](#good-first-issues)** below |
| Need to find your way around the code | **[docs/architecture.md](../docs/architecture.md)** — module map, dependency direction, where a change of each kind goes |
| Adding a language | **[Adding a language](#adding-a-language)** below |
| About to edit `query.py`, `chunks.py`, `graph.py` or `parse.py` | **[Architecture & OS Compatibility Invariants](#architecture--os-compatibility-invariants)** below |
| Wondering how issues get labelled | **[Issue triage](#issue-triage)** below |
| Want to ask rather than file | **[Where to ask what](#where-to-ask-what)** below |

## Local setup

```bash
git clone https://github.com/Srinivasan-78/repo2graph
cd repo2graph
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

`.[dev]` pulls in `pytest`, `ruff`, `mypy`, and `pre-commit`; it does not pull in the `rag` or
`mcp` extras, which are separate for a reason (see [docs/mcp.md](../docs/mcp.md) and the
"Hosted builds" section below). Install those too if your change touches embeddings or the MCP
server:

```bash
.venv/bin/pip install -e ".[dev,rag,mcp]"
```

## Running tests

```bash
.venv/bin/python -m pytest
```

Before opening a PR, run **all three** gates CI runs:

```bash
.venv/bin/ruff check .           # lint
.venv/bin/ruff format --check .  # formatting — a SEPARATE gate from the line above
.venv/bin/mypy repo2graph
```

`ruff format --check` is the one people miss. `ruff check` passing says nothing about it: a
101-character assertion passes lint (`E501` is ignored) and fails formatting, which turns into a
red CI run on an otherwise-finished PR.

The Makefile has one target per gate, so the full set is:

```bash
make lint format-check typecheck test
```

Note that `make lint` alone is only `ruff check .` — it does **not** include the format check.

Two things about `ruff format` that surprise people here:

- It formats **Python code blocks inside Markdown**, so a `python` fence in a doc you add is
  subject to the same rules as the source. Run the check after editing docs, not just code.
- `E501` is in the ignore list, so an over-long line passes `ruff check` and fails
  `ruff format --check`. The two gates disagree on purpose; satisfy both.

`mypy` is strict but only on the modules listed in `pyproject.toml`'s `[[tool.mypy.overrides]]` —
legacy modules are excluded by name on purpose (see the comment above that table), so a new module
is strict-checked by default.

## Code style

- Keep changes focused; add or update tests for behavior you touch — see the
  [Architecture & OS Compatibility Invariants](#architecture--os-compatibility-invariants)
  before editing `query.py`, `chunks.py`, `graph.py`, or `parse.py`.
- `ruff` (line length 100) and `mypy --strict` on the modules it covers are both CI gates.

## Submitting a PR

1. Fork, and branch from **`develop`** — not `main`.
2. Keep changes focused; add or update tests for behaviour you touch.
3. Open the pull request **against `develop`**.
4. Add a `CHANGELOG.md` entry under `## [Unreleased]`, in the right subsection
   (`Added` / `Changed` / `Fixed` / `Security` / `Removed`).

`main` is the release branch. Feature and fix branches merge into `develop`; `develop` is promoted
to `main` as a single PR when a release is cut (see [Releasing](#releasing)). A
PR opened against `main` will be asked to retarget, which is a wasted round trip for you.

When you open a PR, the `prod-igy` bot inspects your branch against its base, applies type/size/area
labels, and reports whether the branch is up to date and conflict-free. If it has drifted or
conflicts, the bot comments with rebase instructions so the CI result stays meaningful.

### What CI will check

| Gate | Command |
|---|---|
| Tests, 12-cell matrix (3 OSes × 4 Python versions) | `pytest` |
| Lint | `ruff check .` |
| **Formatting — separate gate** | `ruff format --check .` |
| Types | `mypy repo2graph/` |
| Packaging + a real MCP stdio round trip | `scripts/mcp_roundtrip.py` |
| Licence/provenance | `reuse lint` |
| Windows cp1252 pipe behaviour | a dedicated job — see Git subprocess decoding invariants |

### Tests have a house style, and it is not the usual one

- **Pin literal values; never assert against something the code under test computed.** A test
  asserting `stdout == format_pack(Index(out).retrieve(...))` moves with the implementation and
  can mask traversal regressions. Hand-derive the expected `(node_id, why)` tuples from the fixture
  source, and assert set membership — no scores, ranks or ordering, which drift with scoring changes.
- **Prove a new test is a detector.** Revert the fix in your working copy, confirm the new test
  fails and existing tests pass, then restore.

## Good first issues

Issues labelled
[`good first issue`](https://github.com/Srinivasan-78/repo2graph/labels/good%20first%20issue)
carry a file and line to start from and acceptance criteria in the issue body.

This section used to duplicate that list in the repository, which is how it came to advertise seven
tasks that had all since been closed. The label is the one copy that cannot go stale.

For anything larger, the [Backlog](#backlog) records deliberately deferred work with the reason for
each deferral — which usually changes how you would approach it. How issues are classified and
labelled is under [Issue triage](#issue-triage).

## Real-world examples and benchmarks

`examples/` holds five public repositories analyzed at a pinned commit each, and `benchmarks/`
holds the machine-readable results those examples' numbers come from — see
[docs/architecture.md](../docs/architecture.md) for the full pipeline and
[benchmarks/README.md](../benchmarks/README.md) for what each results file measures. Two things worth knowing before touching either directory:

- **Never hand-edit `examples/<id>/`.** Both committed files — `README.md` and `overview.md` — are
  generated by `scripts/generate_examples.py` from `examples/repositories.yaml`. Edit the registry
  and regenerate (`python scripts/generate_examples.py --repo <id>`), the same way `.r2g/` output is
  never hand-edited. The generator also writes the graph artifacts (`nodes.jsonl.gz`,
  `edges.jsonl.gz`, `manifest.json`, `stats.json`, `graph.html`, `flows/*.json`); those are not
  committed — see [examples/README.md](../examples/README.md) — so leave them out of the commit.
- **`chunks.jsonl` (source text) never gets committed there.** See
  [examples/ATTRIBUTIONS.md](../examples/ATTRIBUTIONS.md#why-chunksjsonl-is-not-committed) before
  changing what `generate_one()` copies out of the scratch build directory — the boundary between
  "structure, safe to commit" and "source text, not ours to redistribute" is the whole reason that
  function copies files individually instead of copying the build directory wholesale.

Adding a new example repository is data-only — see
[examples/README.md](../examples/README.md) — and does not need
a new Python branch in the generator.

## Registry and Quality Score

repo2graph is published to two places that are not PyPI, and both are part of a
release rather than an afterthought.

### MCP Registry

The canonical entry is `io.github.Srinivasan-78/repo2graph`, generated from
`server.json` at the repo root and published by `.github/workflows/publish.yml`
when that workflow is dispatched. `server.json` is the source of truth — never edit the registry
entry by hand, or the next release will silently revert it.

Audit it against the live entry with:

```bash
curl -s "https://registry.modelcontextprotocol.io/v0/servers?search=repo2graph" | jq .
```

Four fields drift most easily, so check each one after a release:

| Field | Must be |
| --- | --- |
| `version` and `packages[].version` | identical to `project.version` in `pyproject.toml` |
| `packages[].identifier` | `repo2graph` — the PyPI name, not the module name |
| runtime + package arguments | `uvx --from "repo2graph[mcp]" repo2graph-mcp <repo_path>` |
| `repository.id` | the numeric GitHub repo id, which does *not* change on rename |

Tool names, descriptions and input schemas are **not** carried in the registry
entry; a client reads those from `tools/list`. They are generated from
`TOOL_DESCRIPTIONS`/`TOOL_SCHEMAS` in `repo2graph/mcp/schemas.py`, so there is
one definition and nothing to keep in sync.

Licence, homepage and author live in `pyproject.toml` (`license`,
`project.urls.Homepage`, `authors`) and reach PyPI from there.

### Hosted builds (Glama, and anything else that containerises this)

A host that builds the server itself needs to install the `mcp` extra. This is
the one thing that is easy to get wrong, and it fails in a way that looks like a
server bug and is not.

The working build spec:

```json
{
  "buildSteps": ["uv sync --extra mcp"],
  "cmdArguments": ["mcp-proxy", "--", "uv", "run", "repo2graph-mcp", "/app"]
}
```

Three things about it:

- **`--extra mcp` is required.** Plain `uv sync` installs the base dependencies
  only — `tree-sitter` and `tree-sitter-language-pack` — because the MCP SDK is
  deliberately an extra: the CLI and the GitHub Action never import it, so it is
  not a runtime dependency of the package. Without it `repo2graph-mcp` exits 1
  with `the MCP server needs the optional 'mcp' extra`, the proxy sees the child
  die, and the build reports `Connection closed`.
- **Do not use `--all-extras`.** That pulls `rag`, and with it
  `sentence-transformers` and torch: a multi-gigabyte image and a build likely
  to time out, for a dependency the MCP server never calls.
- **Name the repository explicitly.** With no positional argument the server
  falls back to the working directory, so it happens to index its own checkout.
  That works, but it makes the behaviour depend on where the container starts.

The index is built on the *first tool call*, not at startup, so `initialize`
answers immediately and a host's readiness ping will not time out.

CI's `packaging` job runs both halves of this on every push — the install
without the extra, asserting the refusal stays a legible sentence on stderr with
nothing on stdout, and the install with it, driving a real stdio round trip
through `scripts/mcp_roundtrip.py`. Run that script locally against any
installed copy:

```bash
uv sync --extra mcp
uv run python scripts/mcp_roundtrip.py
```

`uv.lock` is committed so these builds are reproducible. It is checked with
`uv lock --check` in the same job, because a stale lockfile makes `uv sync` fail
outright and would break the hosts it exists to help. Regenerate it with
`uv lock` whenever `pyproject.toml`'s dependencies change.

### Glama quality score

[Glama](https://glama.ai/mcp/servers) indexes public MCP servers and assigns a
quality score from the repository: tests, docs, licence, release hygiene and
whether the server actually starts.

To submit:

1. Confirm the server is listed in the MCP Registry (above). Glama discovers
   most servers from there and from `awesome-mcp-servers`.
2. If it has not appeared within a week, submit the repository URL directly at
   <https://glama.ai/mcp/servers> using the "Add server" flow.
3. Glama builds the server in a sandbox, so the build spec must install the
   `mcp` extra — see "Hosted builds" above, which is the single most common way
   this fails. CI's `packaging` job runs that exact install and round trip.
4. `glama.json` at the repo root records the maintainers Glama recognises. It is
   validated against <https://glama.ai/mcp/schemas/server.json>, where
   `maintainers` is the only required field.

Once a score is assigned, Glama issues a badge URL containing the server's
generated slug. It belongs in two places:

- `pyproject.toml`, as `project.urls."Quality Score"` — **done**; it shows on
  the PyPI sidebar. This is the only place the URL is recorded: the badge table it
  also used to sit in was one of the pages folded into the four-document set, and
  was not carried over. If you want the badge back, README.md's badge block is
  where it goes.

The reason to check both rather than assume: a badge pointing at a nonexistent
score renders as a broken image, which is worse than no badge. Confirm the URL
returns 200 before adding it anywhere:

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  https://glama.ai/mcp/servers/Srinivasan-78/repo2graph/badges/score.svg
```

The same rule applies to the other two directory badges in that column
([mcpservers.org](https://mcpservers.org/servers/srinivasan-78/repo2graph) and
the [MCP Registry](https://registry.modelcontextprotocol.io/v0/servers?search=repo2graph)).
The registry entry is published by `publish.yml` from `server.json`; if the
version it reports lags the current release, that is a release step that did not
run, not a documentation problem.

## Architecture & OS Compatibility Invariants

When contributing to `repo2graph`, adhere to the following architectural and cross-platform engineering invariants:

### 1. Text Slicing: Use `split("\n")`, Never `splitlines()`
Tree-sitter advances `Point.row` strictly on newline (`\n`). Python's `str.splitlines()` (and universal-newline reading) also splits on line-separator characters such as U+2028, U+2029, U+0085, `\x0b`, and `\x0c`. In source files containing these characters, `splitlines()` causes Python's line indexing to desynchronize from the parser's row numbers, resulting in corrupted symbol chunk offsets.
- Always slice source text against parser rows using `src.split("\n")` (stripping trailing `\r` for CRLF).
- Re-use the existing `chunks._lines(src)` helper for this operation.

### 2. Git Subprocess Decoding (Windows / Non-UTF-8 Locales)
Subprocess calls that invoke `git` must never pass `text=True` or rely on the default platform locale encoding (such as Windows `cp1252`), which raises `UnicodeDecodeError` when processing paths or diffs containing non-ASCII characters:
- Run `git` commands with `-c core.quotepath=false` so non-ASCII paths are output verbatim rather than escaped.
- Capture raw subprocess bytes and decode explicitly with `.decode("utf8", "surrogateescape")` or `errors="replace"`.
- Always specify an explicit `timeout=` parameter on subprocess invocations.

### 3. Deterministic Discovery Order
The order in which files are discovered dictates the emitted order of nodes, edges, and chunks across `nodes.jsonl`, `edges.jsonl`, and `chunks.jsonl`.
- `parse.discover()` must return paths in a stable, deterministic sort order keyed by `Path.as_posix()` rather than filesystem-dependent directory order.
- Discovery applies `DEFAULT_SKIP_DIRS` consistently across both git-tracked files and fallback directory scans.

### 4. Retrieval and Context Packing Budget Models
`Index.retrieve()` and `Index.pack_context()` serve different operational requirements and intentionally calculate budgets differently:
- `Index.retrieve(query, k, hops, budget_chars)` bounds only the sum of the returned code chunks' `text`.
- `Index.pack_context(query, ...)` bounds the total size of the rendered Markdown context, including directory maps, separators, citation headers, and surrounding blank lines.
- Passing `budget_chars <= 0` in `pack_context()` designates an unbounded budget.

### 5. Graph Traversal and Edge Direction Defaults
When adding or modifying graph traversal filtering options:
- Default parameters for shared traversal helpers (e.g., `expand()`) must not silently narrow existing callers. Callers with broad retrieval needs (such as `retrieve()`) must explicitly specify all edge directions via `ALL_EDGE_DIRS`.
- The confidence threshold gate applies exclusively to `CALLS` edges; relationship edges without confidence scores (`DEFINES`, `IMPORTS`, `INHERITS`) must not be dropped by confidence filters.

### 6. Edge Normalization and Citations
Every edge in `repo2graph` represents a verifiable claim about codebase structure:
- All edges pass through `Graph.add_edge()` where metadata (`method`, `confidence`, `evidence`) is normalized.
- Edges that describe structural hierarchy (like `CONTAINS`) or historical co-occurrence (`CO_CHANGE`) legitimately carry `evidence: null`. Never synthesize fake line evidence for non-syntactic relations.

### 7. MCP Argument Guardrails
MCP tool inputs must be handled defensively:
- Numeric parameters (`k`, `hops`, `limit`, `budget_tokens`) must always be clamped against `MCP_MAX_*` constants in tool entry handlers.
- Secret and sensitive file exclusion (`exclude_secrets=True`) is applied unconditionally across MCP tool responses.

### 8. Testing Conventions
- **Pin literal values**: Test assertions for graph traversal and retrieval should check explicit set membership against hand-derived fixture values (e.g., node IDs and relationship types), rather than asserting against dynamic scores or values recomputed by the implementation under test.
- **Detector proof**: When fixing bugs or adding regression tests, ensure the test fails when the change is reverted.

### 9. File Residue and Chunk Emission
`chunks.py` applies a 40-character `file_residual` floor: a file whose body is entirely `def`s and
imports gets a node but no file-level chunk, and a node with no chunk can be neither a retrieval
seed nor a reachable neighbour.
- A synthetic fixture of pure definitions cannot express an `IMPORTS` edge through `retrieve()`.
  Give each fixture module a module-level constant or docstring so it carries real residue.

### 10. Action Input Truthiness (`action.yml`)
GitHub's expression language and the shell disagree about what is false. An input that arrives as
the string `"false"` is truthy to `test`/`[ ]` but falsy to `${{ }}`.
- Compare explicitly (`if: inputs.flag == 'true'`) rather than relying on either language's
  coercion, and do the same for every casing an input can arrive in.


## Adding a language

How to give repo2graph function-, class- and call-level understanding of a new language. Lua was
the most recent addition (the seventeenth grammar) and is the worked example throughout.

**The short version:** a grammar is already installed for you — `tree-sitter-language-pack` is a
required dependency and ships roughly 165 of them. Adding a language is a `LANG_CFG` entry, an
`EXT_LANG` mapping, two tests and a documentation row. No parser is written, no grammar is
vendored, no build step is added.

---

### 0. Decide whether it needs to be here at all

An unparsed language is **not** invisible. Files in any language still become `file` nodes, still
appear on the map, are still chunked as text and are still retrievable by `repo2graph query`. What
a `LANG_CFG` entry adds is *symbol-level* structure: `symbol` nodes, and `DEFINES` / `CALLS` /
`INHERITS` edges.

So the question is not "is this language supported?" but "does this language's code get asked
relationship questions?" A templating language or a config dialect usually does not. Anything with
functions that call other functions does.

---

### 1. Map the extensions

`repo2graph/parse.py`, `EXT_LANG` (line 26 onward). Extension → grammar key:

```python
EXT_LANG = {
    ...
    ".lua": "lua",
}
```

Two traps here, both of which the repository has already hit:

- **One extension, one language.** `".h": "c"` maps every C++ header to the C grammar, which is
  [issue #377](https://github.com/Srinivasan-78/repo2graph/issues/377) — C++ classes and templates
  in `.h` files are mis-parsed. If your language shares an extension with another, say so in the
  PR; the mapping cannot currently express "sniff the contents".
- **The extension count is asserted.** `EXT_LANG` currently has 29 entries and the READMEs say so.
  A test enforces the grammar list (§5), and the counts in prose need updating by hand.

---

### 2. Write the `LANG_CFG` entry

Same file, `LANG_CFG` (line 75 onward). Four keys, typed by `LangConfig`:

```python
class LangConfig(TypedDict):
    kind_map: dict[str, str]  # tree-sitter node type -> repo2graph symbol kind
    call_types: set[str]  # node types that are a call site
    import_types: set[str]  # node types that are an import
    doc: str  # docstring style: "python" | "jsdoc" | "line"
```

The Python entry, as a minimal example:

```python
"python": {
    "kind_map": {"function_definition": "function", "class_definition": "class"},
    "call_types": {"call"},
    "import_types": {"import_statement", "import_from_statement"},
    "doc": "python",
},
```

### `kind_map`

Keys are **tree-sitter node types for that grammar**, not names you invent. Values are repo2graph
symbol kinds: `function`, `method`, `class`, `struct`, `enum`, `trait`, `impl`, `interface`,
`type`, `module`, and the special `maybe_function`.

`maybe_function` exists for languages where a variable binding may or may not hold a function —
JavaScript's `variable_declarator` covers both `const x = 1` and `const f = () => {}`, and only the
second should become a symbol.

Find the real node types by parsing a sample:

```python
from tree_sitter_language_pack import get_parser

tree = get_parser("lua").parse(b"local function greet(name)\n  print(name)\nend\n")


def walk(n, d=0):
    print("  " * d, n.type)
    for c in n.children:
        walk(c, d + 1)


walk(tree.root_node)
```

Do this before writing the entry. Guessing node-type names from another grammar is the most common
way this goes wrong — grammars disagree (`function_definition` in Python and C,
`function_declaration` in Go and JavaScript, `function_item` in Rust, `method` in Ruby).

### `call_types`

Node types that represent a call site. Include construction where the language treats it as a call
— Java has `{"method_invocation", "object_creation_expression"}`, Rust includes
`macro_invocation`, JavaScript includes `new_expression`.

Whatever you list, `_callee_name` (`parse.py:582`) reduces the expression to a bare name by
splitting on `("::", ".", "->")`. If your language uses a different separator, that tuple needs it
— PHP's `\` is missing, which is
[issue #344](https://github.com/Srinivasan-78/repo2graph/issues/344).

### `import_types`

Node types that declare an import. An empty set is legitimate — Ruby uses `set()` because `require`
is an ordinary method call, not syntax.

This only makes the import *visible*. Resolving it to a file in the repository is separate — §4.

### `doc`

How a doc comment attaches to a symbol. Three styles exist:

| Value | Shape | Used by |
|---|---|---|
| `"python"` | A string literal as the first statement in the body | Python |
| `"jsdoc"` | A `/** ... */` block immediately above the declaration | JS, TS, Java |
| `"line"` | Consecutive line comments immediately above | Go, Rust, Ruby, Lua, and most others |

Pick the closest. Adding a fourth style is a change to the extractor, not to `LANG_CFG`, and wants
its own issue.

### Deriving from an existing language

Where two languages share a grammar family, the config is copied and adjusted rather than rewritten
— `parse.py:220-232`:

```python
LANG_CFG["typescript"] = cast(LangConfig, dict(LANG_CFG["javascript"]))
LANG_CFG["typescript"]["kind_map"] = dict(...)
LANG_CFG["tsx"] = LANG_CFG["typescript"]
LANG_CFG["cpp"] = cast(LangConfig, dict(LANG_CFG["c"]))
```

Note `dict(...)` on both the outer config and `kind_map`. A shallow assignment would share the
mutable `kind_map` between two languages and edits to one would silently alter the other.

---

### 3. Inheritance, if the language has it

`INHERITS` edges come from `_bases_with_details` (`parse.py:764`), which looks for a field named
`superclasses`, `bases` or `trait` on the symbol node, and for any child whose node type is in
`_BASE_NODES` (`parse.py:678`) — `extends_clause`, `implements_clause`, `base_list`,
`delegation_specifier`, `inheritance_specifier`, and the rest.

If your grammar names its inheritance clause something not on that list, add it there. Keywords and
access specifiers inside the clause are filtered by `_BASE_WORDS`, so `public`/`private` in a C++
base clause are already handled.

Base names are normalised in `graph.py:1185`, which strips generics and splits on `.` — it does
**not** handle `::` or `\`, which is
[issue #341](https://github.com/Srinivasan-78/repo2graph/issues/341).

---

### 4. In-repo import resolution, optionally

`LANG_CFG["import_types"]` makes an import visible. Turning `import foo.bar` into an `IMPORTS`
edge pointing at `foo/bar.py` is per-language logic in `graph.py` (the `elif lang == ...` chain
from line 291), because every language's module-to-path convention differs: Rust understands
`crate::` / `super::` / `self::` and the crate name from `Cargo.toml`; PHP maps PSR-4-ish `App\`
prefixes onto `app/` and `src/`; Ruby distinguishes `require_relative` from `require`; Bash
resolves `source` against the sourcing file's directory.

Thirteen languages have this; the rest emit `IMPORTS` to a `module` node instead — an external
dependency rather than a file. **That is a perfectly good first version.** Ship the `LANG_CFG`
entry without path resolution, and add resolution as a separate change with its own tests.

---

### 5. Tests — two of them, and one is enforced

Follow the Lua pair in `tests/test_repo2graph.py:213-254` exactly.

**Unit: the parser sees the symbols.**

```python
def test_parse_lua_extracts_functions_and_calls():
    src = b"""-- Greets a person with a friendly message
local function greet(name)
    print(name)
end
"""
    pf = parse_source(src, "lua")
    if not pf.symbols:
        pytest.skip("lua grammar unavailable")
    assert pf.parse_errors == 0
    names = {s.name: s for s in pf.symbols}
    assert names["greet"].kind == "function"
    assert names["greet"].docstring == "-- Greets a person with a friendly message"
    assert "print" in names["greet"].calls
```

The `pytest.skip` when `pf.symbols` is empty is not optional — a grammar can be missing from the
pack on some platforms, and the suite must skip rather than fail.

**Integration: the build produces the edges.**

```python
def test_build_lua_calls_edge(tmp_path):
    (tmp_path / "main.lua").write_text("...", encoding="utf8")
    g = build(tmp_path)
    assert ("sym:main.lua::run", "sym:main.lua::helper") in edges_of(g, "CALLS")
    assert ("file:main.lua", "sym:main.lua::run") in edges_of(g, "DEFINES")
```

Assert **literal node-id tuples**, hand-derived from the fixture source. Never assert against a
value recomputed by the code under test — see [the invariants](#architecture--os-compatibility-invariants), "Tests must pin values, not compare the
implementation to itself". A test that asserts `len(calls) > 0` passes with the wrong edges.

**The enforced one:** `tests/test_doc_consistency.py::test_languages_documented` asserts that
every `LANG_CFG` key appears in `README.md`. Adding a grammar without documenting it fails CI. The mapping from grammar key to the token
the READMEs use lives in `tests/test_doc_consistency.py`'s `LANGUAGE_TOKENS` — add your language
there too, or the sync assertion fails.

---

### 6. Documentation checklist

All of these, or CI fails or the docs lie:

- [ ] `LANGUAGE_TOKENS` in `tests/test_doc_consistency.py` — the grammar key and its README token.
- [ ] `README.md` — the language list under "What it can't do" (the `<a id="languages">`
      paragraph; `test_languages_documented` is what fails if you skip this).
- [ ] `docs/comparison.md` — the "Languages parsed for symbols" count.
- [ ] `CHANGELOG.md` — under `## [Unreleased]` → `### Added`.

The counts drifted once already (the docs said 16 grammars / 28 extensions for some time after
Lua made it 17/29), which is why the assertion in §5 now exists.

---

### 7. What "supported" honestly means

Before claiming a language works, check it against a real repository and look at
`stats.json`'s `parse_errors`. Two things that will not work, and should be said plainly rather
than discovered by a user:

- **Macro-heavy languages parse imperfectly.** C and C++ produce thousands of tree-sitter `ERROR`
  nodes around unexpanded macros; a `cpp` preprocessor fallback recovers a minority. See
  [../architecture.md](../docs/architecture.md) for the measured rates.
- **Call resolution is name-based for every language, including yours.** A language with heavy
  method-name reuse (TypeScript's `dispose()`, `getId()`) produces more ambiguous `CALLS` edges
  than one with prefix-disciplined naming. That is a property of the language's conventions, not a
  bug in your entry.

If either applies, add a note to [`architecture.md`](../docs/architecture.md) in the same PR. Overstating coverage is
worse than not adding the language.


## Issue triage

How an issue gets classified here, what each label means, and what has to be true before an issue
is workable.

---

### 1. What makes an issue workable

An issue is ready when a contributor who has never seen the codebase can start on it without
asking a question first. Concretely, three things:

1. **The code.** `path/to/file.py:line`, with the relevant lines quoted. "The parser mishandles X"
   is a lead; `parse.py:_callee_name` splits on `("::", ".", "->")` and PHP uses `\` is an issue.
2. **The trigger.** A command, an input or a failing test. For a missing capability rather than a
   defect: the call you wanted to make and what happens instead.
3. **One acceptance criterion that can be false.** "Audit every `except Exception`" cannot be
   checked; "no bare `except Exception` remains in `repo2graph/mcp/`, and `ruff` enforces it"
   can.

A fourth, specific to this repository:

4. **Check it against [the invariants](#architecture--os-compatibility-invariants) first.** Several documented invariants look like
   bugs and are not. The two budget models in `query.py` are deliberately different and must not be
   unified; `splitlines()` is banned in favour of `split("\n")` for a reason that recurs; the
   40-character `file_residual` threshold in `chunks.py:185` is intended; call resolution is
   name-based on purpose. An issue proposing to change one of these needs to argue against the
   recorded reason, not merely notice the behaviour.

Issues that assert a defect without (1) get `needs-reproduction`. That is not a rejection — it
names the next step, which is establishing whether the problem is real.

---

### 2. Triage buckets

Every open issue belongs to exactly one **type** bucket.

| Bucket | Label | Means |
|---|---|---|
| Bug | `bug` | Behaviour contradicts documented or obviously-intended behaviour, **with evidence**. |
| Enhancement | `enhancement` | New capability or a deliberate change to existing behaviour. |
| Documentation | `documentation` | The code is right and the docs are wrong, missing or misleading. |
| Chore / CI / refactor | `type/chore`, `type/ci`, `type/refactor` | No user-visible behaviour change. |
| Question | `question` | A request for information. Usually belongs in Discussions → Q&A instead. |
| Duplicate | `duplicate` | The same work as another open issue. The *older or better-specified* one survives. |
| Superseded | `superseded` | Replaced by a differently-framed issue, not strictly a duplicate. |
| Needs reproduction | `needs-reproduction` | Asserts a defect without naming the code that exhibits it. |
| Out of scope | `out-of-scope` | Deliberately not doing it. The reply must say **why**, and what to do instead. |
| Epic | `type/epic` | A tracking issue. Children carry the real type labels; the epic carries none. |

Orthogonal overlays, any number of which may apply:

| Label | Means |
|---|---|
| `good first issue` | Small, self-contained, has a code pointer and a testable outcome. See §3. |
| `help wanted` | The maintainer is not going to get to this; a contributor would be welcome. |
| `security` | Security-relevant defect or hardening. **Not** for undisclosed vulnerabilities — those go through [the advisory flow](../.github/SECURITY.md). |
| `performance` | Measurable speed or memory effect. |
| `backlog` | Deferred by a recorded audit, with the route back written down. |
| `needs-decision` | Blocked on a maintainer call, not on work. |
| `area/*` | Which part of the codebase. One per issue where possible. |
| `priority/P1..P3` | P1 correctness or security; P2 meaningful; P3 nice to have. |

### Rules that make the taxonomy mean something

- **`bug` and `enhancement` are mutually exclusive.** If it is both, it is two issues.
- **One priority scale.** `priority/P1..P3` only. (`priority/high` is the retired duplicate — §4.)
- **One type label.** Four type labels on one issue means the issue has not been triaged.
- **An epic carries no type label.** Its children carry them.
- **`question` is rarely right.** If someone is asking how something works, that is Discussions →
  Q&A; if the docs failed to answer it, that is `documentation`.

---

### 3. What qualifies as `good first issue`

All five, not three of five:

1. **A code pointer in the issue** — file and line, not "somewhere in the parser".
2. **Bounded blast radius** — one or two files. Nothing that changes an artifact format, a public
   signature or a documented invariant.
3. **A testable outcome** — you can say in advance which test will be added and what it asserts.
4. **No unresolved design decision.** If the first task is choosing between two approaches, it is
   not a first issue.
5. **It does not need the whole pipeline in your head.** Changing `TOKEN_RE` qualifies; changing
   how chunks are cut does not, because chunk boundaries interact with citation offsets.

Current starter tasks carry their acceptance criteria and code pointers in the issue body:
**[`good first issue`](https://github.com/Srinivasan-78/repo2graph/labels/good%20first%20issue)**.

---

### 4. Proposed label taxonomy

The repository has **61 labels** with four duplicated axes. Nothing below has been applied yet.

### 4.1 To create

| Label | Colour | Description |
|---|---|---|
| `needs-reproduction` | `#d876e3` | Asserts a defect without naming the code that exhibits it |
| `out-of-scope` | `#ffffff` | Deliberately not doing this; see the issue reply for why |
| `needs-decision` | `#fbca04` | Blocked on a maintainer decision, not on work |
| `superseded` | `#cfd3d7` | Replaced by a better-specified issue |
| `type/epic` | `#5319e7` | Tracking issue; children carry the real type labels |

### 4.2 To retire, after migrating the issues that carry them

Each of these duplicates an axis that already has a canonical label. Deleting a label removes it
from every issue irreversibly, so this is the last step, not the first.

| Retire | Keep | Why |
|---|---|---|
| `fix`, `type/fix` | `bug` | Three labels for one concept. `bug` is GitHub's default and what search suggests. |
| `feat`, `type/feat` | `enhancement` | Same. |
| `docs`, `type/docs` | `documentation` | Same. `area/docs` stays — it is the *area*, not the type. |
| `chore` | `type/chore` | Keep the namespaced one; `type/*` is the convention for the rest. |
| `test` | `type/test` | Same. `area/tests` stays. |
| `priority/high` | `priority/P1` | Two parallel priority scales. 12 issues carry both. |
| `invalid` | `needs-reproduction` / `out-of-scope` | "This doesn't seem right" says nothing actionable. |
| `wontfix` | `out-of-scope` | Same meaning, clearer name, and it reads less like a dismissal. |

That is 61 → 51, with every remaining label on exactly one axis: **type**, **area**, **priority**,
**state**, or **PR metadata** (`size/*`, `needs-rebase`, `has-conflicts`, and the rest, which
`prod-igy` applies to pull requests and which no issue should carry).

### 4.3 The axes, after consolidation

| Axis | Labels | Rule |
|---|---|---|
| Type | `bug`, `enhancement`, `documentation`, `type/chore`, `type/ci`, `type/refactor`, `type/test`, `type/epic`, `question` | Exactly one |
| Area | `area/cli`, `area/docs`, `area/embed`, `area/graph`, `area/mcp`, `area/query`, `area/tests`, `area/walker`, `area/workflows`, `area/action` | One, ideally |
| Priority | `priority/P1`, `priority/P2`, `priority/P3` | Exactly one |
| State | `needs-reproduction`, `needs-decision`, `duplicate`, `superseded`, `out-of-scope`, `backlog`, `help wanted`, `good first issue` | Any number |
| Flags | `security`, `performance`, `accessibility` | Any number |

---

### 5. Closing an issue

- **Never close an issue without a reply that says why.** A closed issue with no comment is
  indistinguishable from an abandoned one, and it is the fastest way to lose a contributor.
- **Duplicates keep the better-specified issue, not the older one.** Copy anything the closing
  issue said that the survivor does not, then link both ways.
- **Out of scope needs an alternative.** "We are not doing X" should be followed by "because Y,
  and the thing that would make X easy for someone else is Z."
- **Stale is not a reason.** This project has no stale bot and should not get one. An issue that
  has gone quiet is either still true or was never reproducible; decide which.

For contributor-facing conduct expectations during triage, see
[CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) — it applies to issue and discussion threads exactly as
it does to pull requests.


## Where to ask what

Where to ask what, and how the Discussions categories are meant to be used.

Conduct expectations are in [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) (Contributor Covenant 2.1)
and apply identically to issues, pull requests and discussion threads.

---

### Where does this go?

| You have… | Goes to |
|---|---|
| A reproducible defect, with a command and an error | **Issue** → Bug report |
| A wrong or missing `CALLS` / `IMPORTS` / `INHERITS` edge | **Issue** → Incorrect or missing graph edge |
| A request for a language repo2graph doesn't parse deeply | **Issue** → Language / parser support |
| A concrete proposal with an acceptance criterion | **Issue** → Feature proposal |
| "How do I…?" | **Discussions → Q&A** |
| "This isn't working and I'm not sure whether it's me" | **Discussions → Support** |
| A half-formed direction, not yet a proposal | **Discussions → Ideas** |
| A graph of something interesting, or an integration you built | **Discussions → Show and tell** |
| An undisclosed vulnerability | **[Private advisory](https://github.com/Srinivasan-78/repo2graph/security/advisories/new)** — never a public issue |

The distinction that matters most is the last row, and the one between **Support** and **Bug
report**: a bug report asserts the software is wrong, which means it needs a reproduction. Support
asks whether it is wrong. Starting in Support costs nothing and a thread that turns out to be a
defect gets an issue opened from it, carrying the diagnosis that the thread already did.

---

### Categories

Four are in use. Three exist; **Support does not yet exist and has to be created by hand** — see
below.

| Category | Format | Purpose |
|---|---|---|
| **Q&A** | Answerable | "How do I…" — usage, configuration, understanding output. Marking an answer is what makes these worth having; an unanswered Q&A thread is the same as a closed door. |
| **Support** | Answerable | "Is this broken, or is it me?" — setup, MCP client wiring, retrieval results that look wrong. The triage step before a bug report. |
| **Ideas** | Open discussion | Directions, not proposals. No acceptance criterion required. Ideas that firm up graduate to a Feature proposal issue. |
| **Show and tell** | Open discussion | Graphs of interesting repositories, agent workflows, integrations. This is the most useful category for deciding what to build next, and the one most likely to stay empty without prompting. |

`Announcements`, `General` and `Polls` are GitHub defaults. `General` overlaps Q&A and Ideas and is
the category things land in when the chooser is unclear — worth watching, and worth removing if it
collects strays.

### Creating the Support category

**There is no API for this.** The GitHub GraphQL schema has no `createDiscussionCategory` mutation
(verified: `Field 'createDiscussionCategory' doesn't exist on type 'Mutation'`), so discussion
categories are web-UI only and cannot be scripted, committed, or reviewed in a pull request.

To create it:

1. **Settings → Discussions → Categories → New category** — or
   <https://github.com/Srinivasan-78/repo2graph/discussions/categories>.
2. Name: `Support`. Emoji: 🛟. Format: **Question / Answer** (so threads can be marked answered).
3. Description:
   > Something isn't working and you're not sure it's a bug. Setup, MCP client configuration,
   > unexpected retrieval results. If it turns out to be a defect, we'll open an issue from the
   > thread.
4. Then point the "Help" contact link in
   [`.github/ISSUE_TEMPLATE/config.yml`](../.github/ISSUE_TEMPLATE/config.yml) at
   `/discussions/categories/support`. It currently points at Q&A so the issue chooser cannot 404
   before the category exists; there is a comment in the file saying so.

### Descriptions worth updating while you are in there

The three existing categories still carry GitHub's defaults, which say nothing about this project:

| Category | Today | Proposed |
|---|---|---|
| Q&A | "Ask the community for help" | "How do I…? Usage, configuration, and making sense of what repo2graph returned. Answers get marked, so the next person finds them." |
| Ideas | "Share ideas for new features" | "Directions, not proposals. Half-formed is fine — no acceptance criterion needed here. Ideas that firm up become feature proposals." |
| Show and tell | "Show off something you've made" | "Graphs of repositories worth looking at, agent workflows, editor and CI integrations. What you build here decides what gets built next." |

---

### Answering

- **Mark the answer.** An answerable thread with no accepted answer is only useful to whoever was
  in it.
- **If a Support thread turns out to be a defect, open the issue yourself** and link both ways
  rather than asking the reporter to refile. They have already done the diagnosis.
- **If the answer is in the docs, say where and why it was hard to find.** A question that the docs
  technically answered is a documentation bug; file it as one.
- **The most common Support answer is index staleness.** The index is a snapshot and nothing
  watches the filesystem — an edited file keeps being described by the old graph until a rebuild.
  It is worth checking first, every time. See
  [docs/architecture.md#staleness](../docs/architecture.md#staleness).

---

### Contributing

Setup, tests, and the PR flow: [Local setup](#local-setup) and [Submitting a PR](#submitting-a-pr).
Starter tasks: [`good first issue`](https://github.com/Srinivasan-78/repo2graph/labels/good%20first%20issue).
How issues are classified: [Issue triage](#issue-triage).


## Releasing

Publishing is automated — `.github/workflows/publish.yml` ships to PyPI and the
MCP Registry from one GitHub Release, with no long-lived credential anywhere.
But three things have to be set up by hand **once**, because they need you to be
logged in as you. Do those first, then every release is one dispatch of `publish.yml`.

---

### One-time setup

### 1. Register the PyPI Trusted Publisher

PyPI has to be told which workflow is allowed to publish `repo2graph`. Until this
exists, the `pypi` job fails with `invalid-publisher`.

Go to <https://pypi.org/manage/account/publishing/> and add a **pending**
publisher (pending = the project does not exist on PyPI yet, which is the case
here):

| Field | Value |
|---|---|
| PyPI Project Name | `repo2graph` |
| Owner | `Srinivasan-78` |
| Repository name | `repo2graph` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

The environment name matters: the workflow declares `environment: name: pypi`,
and PyPI checks it. A mismatch here is the single most common failure.

> The name `repo2graph` was unclaimed on PyPI as of this writing. If someone
> takes it first, change `[project] name` in `pyproject.toml` *and* the
> `identifier` in `server.json`, and re-register.

### 2. Create the `pypi` environment on GitHub

Settings → Environments → **New environment** → `pypi`. No secrets go in it —
it exists so the publish is gated and shows up in the deployment log. Add a
required reviewer if you want a human to approve each release.

### 3. Nothing to do for the MCP Registry

`mcp-publisher login github-oidc` authenticates as this repository using the
workflow's OIDC token, so there is no account to create and no secret to store.
The namespace `io.github.Srinivasan-78/*` is yours automatically because it
matches the repo owner.

### 4. GitHub Marketplace listing is manual, and does not survive automation

`publish.yml`'s `release` job creates every GitHub Release via `gh release
create`. Neither the GitHub REST API nor the `gh` CLI exposes the "Publish
this Action to the GitHub Marketplace" flag — that checkbox exists only on
GitHub's own **Draft a new release** web page. An automated release can never
create or renew a Marketplace listing, no matter how `publish.yml` is
written; this is a GitHub platform limitation, not a bug in this repo's
pipeline.

Practical consequence: if `repo2graph` needs to be (re-)listed on the
Marketplace, do it by hand, once, against whatever tag is current:

1. Go to **Releases** → find the release for the current tag (e.g. the
   latest `vX.Y.Z` `publish.yml` created) → **Edit release** (pencil icon).
2. Check **"Publish this Action to the GitHub Marketplace"**.
3. Pick a primary category (and a second one if relevant) — required the
   first time a listing is created.
4. Save. This re-publishes the *existing* release; it does not create a new
   tag or trigger `publish.yml`, so it's safe to do at any time independent
   of a version bump.

There is no way to script step 2 onward; it requires being logged in as the
repo owner (or an org member with the right role) in a browser.

---

### The branch model, and what has to happen first

Two long-lived branches:

- **`develop`** — where feature and fix PRs land. This is the base contributors target; see
  [Submitting a PR](#submitting-a-pr).
- **`main`** — the release branch. Only ever updated by a **promotion PR** with `develop` as the
  head branch, or by the release bump itself.

Two consequences worth knowing before your first release:

- **`develop` is protected from deletion** by a ruleset scoped to `refs/heads/develop` with no
  bypass actors. The repository has `delete_branch_on_merge: true`, and because promotion PRs use
  `develop` as the *head*, every promotion merge would otherwise auto-delete it — which in turn
  auto-closes every open PR targeting it. A refused post-merge deletion of `develop` is the
  protection working, not a failure to fix.
- **Auto-delete only ever removes the head branch**, so PRs *into* `develop` were never at risk.

### Pre-release checklist

- [ ] `develop` is green on CI.
- [ ] `develop` has been promoted to `main` and merged (a PR with `develop` as head).
- [ ] `CHANGELOG.md`'s `## [Unreleased]` section describes everything in the release, in the right
      subsections. `publish.yml` reads this section verbatim as the GitHub Release body, which
      makes it a release-blocking step rather than a good intention.
- [ ] `python scripts/check_version.py` passes — every surface in `scripts/version_surfaces.py`
      agrees. If a new surface was added this cycle, confirm it is registered there;
      `tests/test_version_surfaces.py` asserts the bump script covers all of them.
- [ ] `uv lock --check` passes. A stale lockfile aborts the release *after* the tag is cut, which
      is the worst point to find out.
- [ ] `server.json`'s `description` and `pyproject.toml`'s `description` still say what the project
      currently claims, and nothing in either contradicts [architecture.md](../docs/architecture.md).

### Cutting a release

Releasing is fully automated via **`.github/workflows/publish.yml`**, which runs in three sequential stages:
$$\text{prepare-release} \longrightarrow \text{publish} \longrightarrow \text{release}$$

`publish.yml` runs on **`workflow_dispatch` only**. That is deliberate, and the reasoning is in the
comment at the top of the workflow: `prepare-release` pushes the tag and the `release` job creates
the GitHub Release, so a `push: tags:` or `release:` trigger made every successful release
re-trigger itself twice for the same tag — which the PyPI job tolerated (`skip-existing`) but the
MCP Registry job did not ("cannot publish duplicate version", a hard failure). **Pushing a tag
therefore publishes nothing.** There is one route, and it is the one below.

### Releasing via GitHub Actions
1. In GitHub, go to **Actions** → **Publish** → click **Run workflow** (no typing or inputs needed).
2. The workflow automatically:
   - Scans commits and `CHANGELOG.md` since the previous release tag to auto-determine `major`, `minor`, or `patch`.
   - Rewrites **every** version surface listed in `scripts/version_surfaces.py`:
     `pyproject.toml`, `server.json` (twice), `repo2graph/__init__.py`, `uv.lock`, and the
     documented ones — the `@vN` tag in every `uses:` example, the exact-tag and
     `repo2graph==X.Y.Z` pin examples, and the prose naming the release line the floating
     tag tracks. That table is also what `scripts/check_version.py` verifies and what
     `publish.yml` asks for its `--files` list, so a surface cannot be known to the bump
     and not the check.
   - Promotes `[Unreleased]` in `CHANGELOG.md` to `[<version>] — <date>`.
   - Commits and pushes the version bump to `main` and creates Git tag `v<version>`.
   - Runs tests, builds wheels/sdist, and publishes to PyPI via Trusted Publishing.
   - Waits for PyPI CDN and publishes `server.json` to the MCP Registry.
   - Creates the GitHub Release with changelog notes and advances the floating `vN` tag.

### Checking the bump locally first

`publish.yml` does the bump itself, so this is a dry run, not a release step — useful when you want
to see which surfaces a version change touches before handing it to the workflow. Do it on a scratch
branch and do **not** commit or tag the result:

```bash
python scripts/bump_version.py 2.0.1   # or patch / minor / major
python scripts/check_version.py        # every surface agrees (CI runs this too)
git diff -- $(python scripts/version_surfaces.py --files)
git checkout -- .                      # throw it away; the workflow redoes it
```

`bump_version.py` needs `uv` on PATH and fails without it rather than warning: `publish.yml`'s pypi
job installs with `uv export --locked`, so a lock left out of sync aborts the release *after* the tag
is cut. Running it locally is how you find that out before the tag exists.

Verify:

```bash
pip index versions repo2graph
curl -s "https://registry.modelcontextprotocol.io/v0/servers?search=repo2graph" | jq .
uvx --from "repo2graph[mcp]" repo2graph-mcp /some/project    # the thing users will run
```

### The ownership marker

The registry proves you own the PyPI package by finding this exact string in the
package description, which setuptools takes from `README.md`:

```
<!-- mcp-name: io.github.Srinivasan-78/repo2graph -->
```

It is near the top of `README.md` and `publish.yml` refuses to run without it.
Do not remove it, and do not let it end up glued to trailing punctuation — the
token must be followed by whitespace, a newline, or the comment close.

---

### Listing it

**Do these after the first successful publish, not before.** Both lists point
people at an install command; submitting while `pip install repo2graph` still
404s wastes the reviewer's time and yours.

### MCP Registry

Automatic — `publish.yml` does it. Nothing to submit.

### punkpeye/awesome-mcp-servers

Accepts pull requests. Fork, add the line below to the **Developer Tools**
section, and open a PR. Their `CONTRIBUTING.md` fast-tracks agent-authored PRs
if the title ends with `🤖🤖🤖`.

```markdown
- [Srinivasan-78/repo2graph](https://github.com/Srinivasan-78/repo2graph) [![Srinivasan-78/repo2graph MCP server](https://glama.ai/mcp/servers/Srinivasan-78/repo2graph/badges/score.svg)](https://glama.ai/mcp/servers/Srinivasan-78/repo2graph) 🐍 🏠 🍎 🪟 🐧 - Ask a codebase questions and get cited code back. Builds a tree-sitter graph of the repo — files, functions, calls, imports, inheritance — then answers with BM25 plus graph expansion, so every hit arrives with its callers and callees attached and a `[cite: path:start-end]` header. Three tools: `repo_map`, `repo_search`, `repo_neighbours` (the graph hop grep cannot do). Indexes the repo itself on the first call, so there is no setup step. Output is hard-capped at 12k tokens and paths that look like credential stores are never returned. `uvx --from "repo2graph[mcp]" repo2graph-mcp /path/to/project`
```

Legend used: 🐍 Python codebase, 🏠 local service, 🍎🪟🐧 all three platforms.
Not 🎖️ — that means an official vendor implementation.

### wong2/awesome-mcp-servers

**Does not accept pull requests.** Its README says so at the top. Submit through
the form instead:

<https://mcpservers.org/submit>

Use the same description and the `uvx` command above.

### Worth considering too

- **Glama** (<https://glama.ai/mcp/servers>) — indexes from the registry and the
  awesome lists; the badge in the entry above is theirs.
- **mcp.so**, **Smithery** (<https://smithery.ai>) — both take direct
  submissions and are where a lot of client UIs pull their directory from.


## Backlog

Work that was found, understood, and deliberately not done — each entry with the
reason. This is the closest thing the project has to a roadmap, and the place to
look for a first contribution: an item here has already been scoped and argued
for, so picking one up starts from a decision rather than a blank page.

### Deferred engineering items

Found during earlier security and architecture reviews and deliberately deferred. Every row here was
re-checked against the code on 2026-09-30; a row that had shipped was deleted rather than annotated,
because a backlog that lists finished work stops being read. Still verify before you start — this is
a record of past state, like any other document.

| Item | Size | Why deferred |
|---|---|---|
| **No per-file tree-sitter parse timeout** | M | `MAX_BYTES` bounds file size, not parse time. `tree_sitter.Parser.set_timeout_micros` support varies across grammar bindings in `tree-sitter-language-pack`; a wrong per-language timeout risks truncated parses on legitimately large generated files, with no fixture to prove the value is well-calibrated. |
| **Secret-path denylist (`security.py` `SECRET_KEYWORDS`/`SECRET_DIR_NAMES`, re-exported from `query.py`) is not user-configurable** | S | Solid and independent of `.gitignore`, but hardcoded — an org with nonstandard secret-file naming can't extend it without a code change. Needs a CLI flag / config file design, not a quick patch. |
| **No enforced cap on total graph nodes/edges/files** | M | `graph.py`'s `max_files` is opt-in and defaults unbounded. `Graph.nodes`/`edges` are fully in-memory with no size guard, unlike the already-streamed chunk emission path. |
| **TOCTOU symlink race between `discover()`'s `lstat()` and the later `open()`** | — | Documented as a known limitation, not fixed: exploiting it requires local code execution on the same host, and `O_NOFOLLOW` is POSIX-only, so no fix closes it cross-platform. |
| **No benchmark above 6,000 files** | L | The largest measured tree is VS Code at 6,000 files (`benchmarks/results.json`, and each `examples/<id>/README.md`). Nothing has been run at 50k/100k+, so the scaling curve past that point is unmeasured rather than known. |
| **A real `sentence-transformers` smoke test, opt-in and network-gated** | S | Every embedder in the suite is `StubEmbedder`. `default_embedder()` is tested only for its *failure* message, so nothing proves the real wrapper's `model_id`/`dim` agree with what `vectors.meta.json` records — the exact pair `fuse_ok` compares. |

Issues routing the work deferred during earlier stabilization passes:

**Epic:** [#31 — Epic: post-audit backlog](https://github.com/Srinivasan-78/repo2graph/issues/31)

| Issue | Scope | Priority |
|-------|-------|----------|
| [#26](https://github.com/Srinivasan-78/repo2graph/issues/26) | fetch.py hardening and subprocess timeouts | P1 |
| [#21](https://github.com/Srinivasan-78/repo2graph/issues/21) | parse.py & cross-module string/correctness improvements | P2 |
| [#23](https://github.com/Srinivasan-78/repo2graph/issues/23) | export.py correctness & GraphML export hardening | P2 |
| [#24](https://github.com/Srinivasan-78/repo2graph/issues/24) | viz.py visualization safety and UX | P2 |
| [#25](https://github.com/Srinivasan-78/repo2graph/issues/25) | query.py retrieval budget and scoring hygiene | P2 |
| [#27](https://github.com/Srinivasan-78/repo2graph/issues/27) | walker / discovery hygiene | P2 |
| [#28](https://github.com/Srinivasan-78/repo2graph/issues/28) | Test coverage improvements | P2 |
| [#29](https://github.com/Srinivasan-78/repo2graph/issues/29) | CI, supply-chain & workflow hygiene | P2 |
| [#22](https://github.com/Srinivasan-78/repo2graph/issues/22) | chunks.py line-span accuracy and symbol IDs | P3 |

All child issues carry the `backlog` label.

### Language support and ecosystem relationships (2026-09)

Strategic priorities; see [`architecture.md`](../docs/architecture.md) for the pipeline these extend
and "Adding a language" above for the mechanics.

| Item | Scope | Tier / Priority |
|---|---|---|
| TypeScript support | `tsconfig.json` path aliases & monorepo workspace module resolution | Tier 1 / P1 |
| TypeScript web routes | Express, NestJS, Next.js HTTP route & controller extraction (`ROUTES_TO`) | Tier 1 / P1 |
| TypeScript tests | Jest & Vitest test-to-implementation linking (`TESTS`) | Tier 1 / P1 |
| Python routes | FastAPI, Flask, & Django route-to-handler resolution (`ROUTES_TO`) | Tier 1 / P1 |
| Python tests | Pytest test-to-implementation linking & fixture injection (`TESTS`) | Tier 1 / P1 |
| Python models | SQLAlchemy & Django model relational schema extraction (`MODELS`) | Tier 1 / P1 |
| JVM injection | Spring Boot & Jakarta Dependency Injection resolution (`INJECTS`) | Tier 2 / P2 |
| JVM routes & tests | Spring MVC & JAX-RS routes (`ROUTES_TO`) & JUnit test links (`TESTS`) | Tier 2 / P2 |
| Go interfaces | Anonymous struct embedding & interface satisfaction (`INHERITS`) | Tier 2 / P2 |
| Go routing & tests | Gin/Chi web routing (`ROUTES_TO`) & table-driven test linking (`TESTS`) | Tier 2 / P2 |
| Test suite parity | Core test suite language coverage parity (TS, TSX, Java, Scala, Rust, Swift) | Core / P2 |
