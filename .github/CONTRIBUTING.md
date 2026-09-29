# Contributing

Thanks for helping out.

**Start here depending on what you're doing:**

| | |
|---|---|
| Looking for something to work on | **[docs/good-first-issues.md](../docs/good-first-issues.md)** — seven tasks with acceptance criteria and code pointers |
| Need to find your way around the code | **[docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)** — module map, dependency direction, where a change of each kind goes |
| Adding a language | **[docs/parser-development.md](../docs/parser-development.md)** |
| About to edit `query.py`, `chunks.py`, `graph.py` or `parse.py` | **[Architecture & OS Compatibility Invariants](#architecture--os-compatibility-invariants)** below |
| Wondering how issues get labelled | **[docs/TRIAGE.md](../docs/TRIAGE.md)** |
| Want to ask rather than file | **[docs/COMMUNITY.md](../docs/COMMUNITY.md)** |

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
to `main` as a single PR when a release is cut (see [docs/publishing.md](../docs/publishing.md)). A
PR opened against `main` will be asked to retarget, which is a wasted round trip for you.

When you open a PR, the `prod-igy` bot inspects your branch against its base, applies type/size/area
labels, and reports whether the branch is up to date and conflict-free. If it has drifted or
conflicts, the bot comments with rebase instructions so the CI result stays meaningful.

### What CI will check

| Gate | Command |
|---|---|
| Tests, 9-cell matrix (3 OSes × 3 Python versions) | `pytest` |
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

**[docs/good-first-issues.md](../docs/good-first-issues.md)** has seven, each with a file and line
to start from, acceptance criteria, and the specific thing that makes it trickier than it looks —
because every one of them has one. They range from a one-regex fix to a small refactor that has to
break an import cycle.

For anything larger, **[docs/BACKLOG.md](../docs/BACKLOG.md)** records deliberately deferred work
with the reason for each deferral — which usually changes how you would approach it.
How issues are classified and labelled is in **[docs/TRIAGE.md](../docs/TRIAGE.md)**.

## Real-world examples and benchmarks

`examples/` holds five public repositories analyzed at a pinned commit each, and `benchmarks/`
holds the machine-readable results those examples' numbers come from — see
[docs/examples.md](../docs/examples.md) and [docs/benchmarks.md](../docs/benchmarks.md) for the
full pipeline. Two things worth knowing before touching either directory:

- **Never hand-edit `examples/<id>/`.** Every file in it — `metadata.json`, `nodes.jsonl.gz`,
  `edges.jsonl.gz`, `README.md`, `flows/*.json` — is generated by
  `scripts/generate_examples.py` from `examples/repositories.yaml`. Edit the registry and
  regenerate (`python scripts/generate_examples.py --repo <id>`), the same way `.r2g/` output is
  never hand-edited.
- **`chunks.jsonl` (source text) never gets committed there.** See
  [examples/ATTRIBUTIONS.md](../examples/ATTRIBUTIONS.md#why-chunksjsonl-is-not-committed) before
  changing what `generate_one()` copies out of the scratch build directory — the boundary between
  "structure, safe to commit" and "source text, not ours to redistribute" is the whole reason that
  function copies files individually instead of copying the build directory wholesale.

Adding a new example repository is data-only — see
[docs/examples.md#adding-a-repository](../docs/examples.md#adding-a-repository) — and does not need
a new Python branch in the generator.

## Registry and Quality Score

repo2graph is published to two places that are not PyPI, and both are part of a
release rather than an afterthought.

### MCP Registry

The canonical entry is `io.github.Srinivasan-78/repo2graph`, generated from
`server.json` at the repo root and published by `.github/workflows/publish.yml`
on a version tag. `server.json` is the source of truth — never edit the registry
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
entry; a client reads those from `tools/list`, or from
`/.well-known/mcp-server-metadata` when the server runs with `--http-port`.
Both are generated from `TOOL_DESCRIPTIONS`/`TOOL_SCHEMAS` in
`repo2graph/mcp.py`, so there is one definition and nothing to keep in sync.

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

- `README.md`, in the "Listed on" column of the badge table — **done**; the
  badge resolves, so a score has been assigned.
- `pyproject.toml`, as `project.urls."Quality Score"` — **done**; it shows on
  the PyPI sidebar from the next release onward.

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

