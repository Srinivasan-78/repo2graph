# Output schema: what every answer carries, and how much to trust it

An edge is a claim about the code, and an answer is a claim built from edges.
This page is the contract for both: what fields exist, what each one means,
and what none of them promise.

- [Edge records](#edge-records)
- [What `confidence` means](#what-confidence-means)
- [Citations](#citations)
- [The "Confidence and limitations" segment](#the-confidence-and-limitations-segment)
- [Feedback and the bug-report bundle](#feedback-and-the-bug-report-bundle)

Schema version: **`edge_schema_version: "2"`**, carried in
`agent/manifest.json` alongside `index_schema_version`. Version 1 edges had no
`method`, `confidence` or `evidence` on four of the six types; a consumer that
sees `"1"` should treat those fields as *absent* rather than null.

---

## Edge records

Every record in `agent/edges.jsonl`, whatever its type, carries these six
fields in this order:

```json
{
  "src": "sym:app/routes.py::create_order",
  "dst": "sym:app/auth.py::require_token",
  "type": "CALLS",
  "method": "name-resolver",
  "confidence": 1.0,
  "evidence": {"path": "app/routes.py", "line": 23},
  "call_kind": "static",
  "candidate_count": 1,
  "count": 1,
  "resolution_kind": "same_file",
  "scope_distance": 1
}
```

| Field | Type | Meaning |
|---|---|---|
| `src`, `dst` | string | Node ids. See `id_grammar` in `manifest.json`. |
| `type` | string | `CONTAINS`, `DEFINES`, `IMPORTS`, `CALLS`, `CALLS_EXTERNAL`, `INHERITS`, `CO_CHANGE` |
| `method` | string | How it was extracted — see below |
| `confidence` | float 0..1 | P(`dst` is the right target) — see below |
| `evidence` | `{path, line}` or `null` | Where the relationship is *written*, 1-based |
| `candidate_count` | int | How many definitions the name could have meant |
| `ambiguous` | bool | Present and true when more than one matched |
| `count` | int | How many times the relationship occurs; `evidence` cites the first |

Type-specific fields (`resolution_kind`, `scope_distance`, `call_kind`,
`target`, `internal`, `subtype`, `raw_base`, `cochange_count`) are unchanged
and documented in `manifest.json`'s `edge_fields`.

### `method`

Names the machinery, so a consumer that distrusts one extraction path can
filter on it rather than on edge type:

| `method` | Means |
|---|---|
| `tree-sitter/<lang>` | Read straight from a parse tree. The `<lang>` matters: the same edge type carries different weight from the Python grammar than from the C fallback. |
| `name-resolver` | The parse tree supplied a *name*; repo2graph matched it to a definition. **The only method whose confidence is routinely below 1.** |
| `filesystem` | Directory structure. No parsing. |
| `git-log` | Commit history. Correlational, never causal. |

### `evidence`

`{"path": "app/routes.py", "line": 23}` — the file and 1-based line where the
relationship is written. `null` where there is genuinely nothing to point at:

| Type | `evidence` | Cites |
|---|---|---|
| `DEFINES` | ✅ | the symbol's definition line |
| `IMPORTS` | ✅ | the import statement |
| `CALLS` / `CALLS_EXTERNAL` | ✅ | the first call site (`count` says how many more) |
| `INHERITS` | ✅ | the class header carrying the base clause |
| `CONTAINS` | `null` | a file being in a directory is not written anywhere |
| `CO_CHANGE` | `null` | a history fact, not a line of code |

**`null` is a real answer, not a gap.** Inventing a line for a `CONTAINS`
edge would be a fabricated citation — precisely the failure this schema
exists to prevent.

Line numbers come from tree-sitter's `Point.row`, which advances on `\n`
only — the same convention `chunks._lines()` slices by, so the line in
`evidence` is the line the reader sees. (See AGENTS.md on `splitlines()`.)

## What `confidence` means

**The probability that `dst` is the correct target, given that the
relationship at `evidence` exists.**

It is *not* a probability that the relationship exists at all. That is what
`evidence` is for, and a syntactic fact read out of a parse tree is not
uncertain. Keeping the two separate is what makes the number usable:

| Case | `confidence` |
|---|---|
| `CALLS` resolved to exactly one candidate | `1.0` |
| `CALLS` whose name matched 4 candidates | `0.25` on each of 4 edges |
| `CALLS_EXTERNAL` | `1.0` — `dst` is a synthetic `external:<name>` node meaning "not found in this repo", which is exactly what was determined |
| `CONTAINS`, `DEFINES` | `1.0` |
| `IMPORTS` | `1.0` |
| `CO_CHANGE` | the share of sampled commits that touched both files |

### What confidence never encodes

**Dynamic dispatch.** A `1.0` CALLS edge means "this name resolves here", not
"this line reaches that function at runtime". `call_kind` carries that
(`static`, `possible`, `dynamic`, `decorator`), and
[docs/limitations.md](limitations.md) carries the rest.

A `confidence: 1.0` edge with `call_kind: "dynamic"` is a confident claim
about a name and a weak claim about behaviour. Read both fields.

## Citations

Every surface that returns code returns `path:line` or `path:start-end`,
in a form terminals and editors already make clickable:

| Surface | Returns | Citation form |
|---|---|---|
| `rag` | markdown | `### [cite: app/auth.py:31-40] \`require_token\` (seed)` |
| `rag --format json` | JSON | `path`, `start_line`, `end_line` per chunk |
| `query` | text | `--- app/auth.py::require_token [seed]` |
| `explain edge` | text or JSON | `open: app/routes.py:23` — the edge's own evidence |
| `explain node` | text or JSON | the node's `path:start-end`, plus each edge's evidence |
| `repo_search` (MCP) | markdown | the same `### [cite: ...]` headers as `rag` |
| `repo_neighbours` (MCP) | markdown | `\`name\` (path:line) [node-id]  -- at path:line` |
| `--answer` | text | the model is instructed to cite `[path:start-end]` on every claim |

**The MCP tools return markdown text, not JSON objects.** Every tool result
is a string; there is no per-result `path` field to read. Agents consume the
citation from the text, which is why the form is stable and why
`repo_neighbours` puts the node id in brackets — it is the argument for the
next call.

`repo_neighbours` cites **two different places**, and the distinction
matters:

```
- CALLS in: `check_index_freshness` (repo2graph/doctor.py:1051) [sym:...]  -- at repo2graph/doctor.py:1075
                                     ^ where the neighbour is defined            ^ where the call is written
- CALLS out: `ResultCache.get` (repo2graph/cache.py:128) [sym:...]  -- at repo2graph/status.py:124, AMBIGUOUS 0.5 of 3 candidates
```

For "what calls this", the useful citation is the **call site** — the edge's
`evidence` — not where the caller happens to be defined. And an edge below
`1.0` is marked `AMBIGUOUS`, because without it a name that matched three
candidates reads exactly like a certain resolution. (That second line is a
real example: a `.get()` on a dict resolving to one of three `get` methods in
the repository.)

`repo2graph.edgemeta.cite(edge)` is the one renderer; the CLI, the MCP tools
and `explain` all call it rather than formatting their own, so the form
cannot drift between surfaces.

### Navigating to a citation

`path:line` is clickable as-is in most terminals, in VS Code's integrated
terminal, and in JetBrains consoles. From a shell:

```bash
code --goto app/auth.py:31
vim +31 app/auth.py
```

Paths are always repo-relative and posix-separated, on every platform, so a
citation produced on Windows resolves on Linux and in a CI log.

## The "Confidence and limitations" segment

`repo2graph rag --answer` appends a segment to every answer. It is **computed
by repo2graph from the pack that was actually sent**, never asked of the
model — a model rating its own confidence produces a number with no referent,
and it cannot know what retrieval never showed it.

```
---
**Confidence and limitations**

- Based on **7 cited block(s) across 4 file(s)** -- 4 matched the question
  directly, 3 were pulled in by graph edges.
- Graph edges used: 2x CALLS, 1x IMPORTS.
- **1 of those edge(s) is ambiguous** (the called name matched more than one
  definition, so the block shown may not be the one that actually runs).
- The context fit within budget (61% of 24000 characters), so nothing was cut
  for space.
- calls made through dynamic dispatch, reflection, or a DI container are not
  edges in this graph, so a caller list can be incomplete
- the graph models the code as written, not as executed: an edge is not proof
  the line runs
- only files that were indexed are visible -- anything excluded by a filter, a
  .gitignore, or a size limit is absent rather than reported as missing
```

It has the **same shape every time**, including when there is nothing to
caveat. A section that appears only when something is wrong teaches readers to
skip it, and an answer with no caveats is exactly the case where a reader most
wants to see that the check ran.

The machine-readable form is `repo2graph.limits.confidence_report(pack)`.

The truncation line is the one worth reading closely: an answer assembled from
a truncated pack can be wrong *by omission*, and nothing else in the output
would say so.

## Feedback and the bug-report bundle

Five categories, each with a GitHub issue template and a `--category` value:

| Category | Template | Use when |
|---|---|---|
| `incorrect-relationship` | Incorrect or missing graph edge | An edge exists that should not, or points at the wrong target |
| `missing-relationship` | Incorrect or missing graph edge | An edge that should exist does not |
| `stale-index` | Stale index | The index does not match the tree, or will not refresh |
| `parser-failure` | Parser failure | A supported language failed to parse or produced no symbols |
| `answer-unhelpful` | Answer unhelpful | `rag` / `query` / an MCP tool returned an answer that did not help |

```bash
repo2graph bug-report -o .r2g --category incorrect-relationship
repo2graph bug-report -o .r2g --category stale-index --write report.md
```

### What the bundle contains

Environment and versions; the non-passing `doctor` checks; index shape,
provenance commit and freshness; and an edge-quality histogram (counts by
type, by method, and by confidence bucket) — which is often what explains a
bad answer, since a graph dominated by ambiguous name matches behaves
differently from one that resolves cleanly.

### What it never contains

**No file content, at any privacy level.** Not a chunk body, not a line of
source, not a snippet around the edge being reported.

Also never included: environment variable *values*, the git remote URL, the
repository name, the branch name, git author names or emails, and absolute
paths.

**File paths are off by default.** A repo-relative path is harmless in an
open-source project and competitively sensitive in a private one
(`billing/stripe_migration_v2.py` says a lot). Changed-file paths appear as
8-character fingerprints so a maintainer can see that two entries are the same
file without learning which. `--include-paths` opts in.

The commit sha *is* included: it makes a wrong edge reproducible against a
public repo, and reveals nothing about a private one that its own history does
not.

This makes the bundle weaker than a reproduction, deliberately. It answers
"what is your environment and what does your index look like" completely —
most of the round trips — and leaves "what does the code say" to your
judgement, case by case. Read it before you post it; it is yours to check.

## See also

- [INDEXING.md](INDEXING.md) — how the graph is built, and its determinism guarantees
- [limitations.md](limitations.md) — what the graph does not model
- [cli.md](cli.md) — every flag
- `repo2graph/edgemeta.py` — the schema in code, with the reasoning inline
