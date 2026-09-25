# Issue triage report — 2026-09-25

A full pass over all **84 open issues**, classified into the buckets the triage policy
([docs/TRIAGE.md](TRIAGE.md)) defines, plus the duplicates, overlaps and already-shipped work the
pass turned up.

**Nothing here has been applied.** No issue was closed, relabelled or commented on. This report is
the proposal; §8 has the exact commands to enact it once a maintainer agrees. That split is
deliberate — every one of these issues was filed by the maintainer, so a bot-style auto-close would
be destroying the only record of a deliberate decision.

---

## 1. What the backlog actually looks like

84 open issues, **all 84 authored by `Srinivasan-78`**. There are no community reports, so the
usual triage questions ("is the reporter responsive?", "can we reproduce it?") do not apply. This
is a **self-filed engineering backlog**, and the useful triage questions are different: is it
specified well enough to act on, is it already done, and is it the same work as another issue?

Three cohorts, and they are not equal in quality:

| Cohort | Range | Count | Filed | Character |
|---|---|---:|---|---|
| **A — evidence-backed** | #338–#408 | 47 | 2026-09-22 | `file.py:line` citations, quoted code, a stated proposal and often a test plan. Ready to work. |
| **B — audit sweep** | #263–#318 | 34 | 2026-09-21 | Generic `## Problem` / `## Required changes` bullet lists. No file, no line, no reproduction. Hedged verbs: *"may"*, *"appears to"*, *"reportedly"*. |
| **C — audit epic** | #79, #81, #88 | 3 | 2026-09-17 | An epic plus two children, already labelled `backlog` and cross-referenced to `docs/SECURITY-AUDIT.md`. Fine as they are. |

**Cohort B is the finding.** 34 of 84 open issues — 40% — assert a defect without naming the code
that has it. Four of them are demonstrably about behaviour that already exists (§4), and two are
the same work as another issue (§3). They read as a checklist generated *against* the
project rather than *from* it, and working one requires re-deriving the evidence first. That is the
single biggest drag on the backlog, and §5 drafts the replies that fix it.

### Label hygiene is separately broken

Labels are applied in combinations that cannot all be true at once:

| Issue | Labels today | Problem |
|---|---|---|
| #307 | `documentation`, `enhancement`, `question`, `security`, `priority/P2`, `priority/high`, `performance` | Seven labels, four of them type labels. It is one thing: a CLI naming audit. |
| #318 | `bug`, `documentation`, `question`, `type/refactor`, `priority/P3`, `performance` | `bug` + `question` + `refactor` simultaneously. |
| #79 | `bug`, `documentation`, `enhancement`, `security`, `backlog`, `priority/high`, `performance` | It is an epic. None of those are its type. |
| #266, #267, #283, #285, #286, #288, #293, #294, #296, #297, #298, #299, #313, #314, #316 | all carry both `bug` **and** `enhancement` | Mutually exclusive by definition. |

Counting labels: `bug`+`enhancement` co-occur on **16** issues (#79, #266, #267, #283, #285, #286,
#288, #293, #294, #296, #297, #298, #299, #313, #314, #316), and **12 carry both `priority/high`
and a `priority/PN`** (#81, #263, #266, #267, #282, #285, #287, #290, #294, #297, #307, #314) —
two parallel priority scales that disagree about what "high" means. The taxonomy itself has
duplicate axes — `bug`/`fix`/`type/fix`, `enhancement`/`feat`/`type/feat`,
`documentation`/`docs`/`type/docs`/`area/docs`, `chore`/`type/chore`, `test`/`type/test`/`area/tests`,
`priority/high`/`priority/P1`. See [docs/TRIAGE.md](TRIAGE.md) §4 for the proposed consolidation.

---

## 2. Classification

Buckets are the ones the task named. `needs-reproduction` is used in its natural sense for a
self-filed backlog: **the issue asserts a defect but cites no code that exhibits it**, so the first
step is reproducing the claim, not fixing it.

Every issue lands in exactly one bucket, and they sum to 84:

| bug | enhancement | documentation | chore/ci/refactor | duplicate | needs-reproduction | out-of-scope | epic | **total** |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 15 | 29 | 2 | 8 | 2 | 21 | 4 | 3 | **84** |

`good first issue` is an overlay on the bug/chore buckets, not a bucket of its own — see §2.5.

### 2.1 bug — 15

Defect with evidence. All from cohort A.

| # | Title | Note |
|---|---|---|
| 407 | mcp 1.x hangs on the first tool call | P1. Has a failing test name and an SDK version. Supersedes #291. |
| 408 | JSONL index files read unbounded on the untrusted-index path | Security. Explicit follow-up to #405. |
| 390 | HTTP advertises protocolVersion 2025-06-18 without Streamable HTTP | Spec-conformance defect. |
| 378 | `TOKEN_RE` drops single-character identifiers | `query.py:54`. Smallest real bug in the backlog — see [good-first-issues.md](good-first-issues.md). |
| 377 | Every `.h` mapped to C, so C++ headers mis-parse | `EXT_LANG`. |
| 376 | U+2028/U+2029 not escaped in JSONL writers | Same bug class AGENTS.md names as recurring. |
| 344 | PHP `\` separator unhandled in `_callee_name` | `parse.py`. |
| 342 | Self-calls not excluded when marking entrypoint roots | `graph.py:1251-1268`. |
| 341 | `::` and `\` unhandled in inheritance base resolution | `graph.py:1185`. |
| 340 | Stale dist-info shadows source `__version__` | `__init__.py`. |
| 338 | Credential scanner skips lowercase alphanumeric/hex secrets | P1 security. `secrets.py:265-276`. |
| 367 | No minimum RSA modulus in `rsa_verify` | Security. |
| 372 | Host/Origin check not applied to GET and HEAD | Security. DNS-rebinding surface. |
| 373 | Markdown image/link syntax not stripped from prod-igy output | Security. |
| 371 | Cypher labels and relationship types interpolated unquoted | Security, not currently exploitable — author says so. |

### 2.2 enhancement — 29

| # | Area | Title |
|---|---|---|
| 385 | mcp | `repo_read` tool so citations are followable without filesystem access |
| 384 | mcp | `repo_find_symbol` for name→node_id lookup |
| 387 | mcp | `repo_impact` for reverse reachability / blast radius |
| 386 | mcp | `repo_path_between` for reachability between two nodes |
| 383 | mcp | Warn when the index is stale relative to the working tree |
| 389 | mcp | Cursor pagination for tool results |
| 388 | mcp | Expose resources and prompts primitives |
| 379 | query | Persist the BM25 inverted index |
| 382 | query | Stopword removal and light stemming |
| 381 | query | Optional cross-encoder reranking |
| 398 | graph | Language coverage: HCL, Vue, Svelte, Objective-C, `.ipynb` |
| 397 | graph | Reference edges beyond calls (READS/WRITES, type refs) |
| 396 | graph | Multi-repo indexes with cross-repo edges |
| 394 | graph | Authorship edges alongside `CO_CHANGE` |
| 393 | graph | `TESTS` edge linking tests to what they exercise |
| 380 | graph | Optional SCIP ingestion for exact resolution |
| 368 | graph | Credential scanning for modern vendor key prefixes |
| 395 | cli | Expose the graph delta as `repo2graph diff` and an MCP tool |
| 392 | cli | `--watch` mode reindexing incrementally |
| 391 | cli | Config file so build options are not repeated |
| 401 | cli | `--neo4j-uri` to push the Cypher export directly |
| 370 | cli | Content-Security-Policy on generated `graph.html` |
| 305 | viz | Large-repository visualization behaviour |
| 302 | build | Incremental cache invalidation beyond content hash + language |
| 299 | build | Default resource limits for nodes/edges/chunks/memory |
| 304 | ci | Performance benchmarks and regression gates (umbrella for #88) |
| 288 | query | Retrieval evaluation fixtures and metrics |
| 287 | chunks | Chunking for very large symbols and small files — **see §4.4**, half is by design |
| 286 | embed | Dense-retrieval failure modes observable and configurable |
| 289 | query | Graph expansion configurable by edge type and direction — **see §4.1**, exists on the Python API already; the open part is CLI and MCP exposure |

### 2.3 documentation — 2

| # | Title | Note |
|---|---|---|
| 404 | Add `CITATION.cff` | Well-specified, genuinely small. Starter task. |
| 312 | Stable Python API documentation and schema types | Already the only `good first issue`; see §2.5. |

### 2.4 chore / ci / refactor — 8

| # | Title |
|---|---|
| 315 | Split large modules by responsibility (`graph/`, `query/`, `mcp/`) — absorbs #295 |
| 403 | Move `BUILD_STATE.md` out of the repository root (198 KB) |
| 402 | Add zizmor to CI to lint the workflows |
| 375 | Move prod-igy's token-probe env out of the step that consumes it |
| 374 | Drop the `edited` trigger from prod-igy's `pull_request_target` |
| 347 | Add Python 3.13 to the CI test matrix |
| 349 | Consolidate duplicated `NODE_TYPES`/`EDGE_TYPES` between `export.py` and `viz.py` |
| 345 | Eliminate quadratic string joining in `query.py:_fit_lines` |

### 2.5 good first issue — 6 proposed (1 today)

Full acceptance criteria and code pointers: **[docs/good-first-issues.md](good-first-issues.md)**.

| # | Title | Why it qualifies |
|---|---|---|
| 378 | `TOKEN_RE` drops single-character identifiers | One regex, one test, visible behaviour change |
| 404 | Add `CITATION.cff` | New file, no code paths touched |
| 347 | Python 3.13 in the CI matrix | One workflow line + confirming the suite is green |
| 349 | Deduplicate `NODE_TYPES`/`EDGE_TYPES` | Mechanical; one new shared module |
| 345 | Quadratic join in `_fit_lines` | Small, self-contained, measurable |
| 344 | PHP `\` separator in `_callee_name` | One separator added to a tuple, plus a fixture |

`#312` keeps the label today but is **not** a good first issue — "define the public API surface and
add typed return models" is an API-design decision across six entry points. Recommend removing the
label from it and **filing one new issue** for the narrow, tractable piece: ship `repo2graph/py.typed`
so downstream type checkers can see the annotations that already exist. That is written up as
starter task 7 in [good-first-issues.md](good-first-issues.md), ready to paste into a new issue.

### 2.6 question — 0

No issue is a question. `question` is currently on **#318, #307, #288** as label noise; all three
are work items. Remove it from all three.

### 2.7 duplicate — 2

Detail and drafted replies in §3.

| # | Duplicate of | Why |
|---|---|---|
| 295 | #315 | "Isolate HTTP server, auth, and MCP tool logic into modules" is one bullet of #315's module split, which already lists `repo2graph/mcp/{server,tools,transport}.py`. |
| 291 | #407 | "Upgrade MCP SDK compatibility" is the unevidenced version of the confirmed 1.x hang, which names the SDK version and the failing test. |

### 2.8 needs reproduction — 21

Cohort B minus the ones classified elsewhere. Every one asserts a defect or a gap without naming
the code. They are not wrong — several are probably real — but none can be worked without first
re-deriving the evidence, and four turned out to be about behaviour that already exists (§4).

#263, #264, #266, #267, #281, #282, #283, #284, #285, #290, #292, #293, #294, #296, #297, #298,
#307, #313, #314, #316, #318

Drafted reply in §5. It is one template, because they share one defect.

### 2.9 won't do / out of scope — 4 proposed, **maintainer decision required**

These are judgement calls on a solo-maintained project, not defects. Drafted replies in §6.

| # | Title | Argument |
|---|---|---|
| 400 | VS Code extension | A second product in a second language with its own release channel and review process. |
| 399 | npx-installable launcher | A Node packaging surface on a Python project; `uvx` already covers it. |
| 396 | Multi-repo indexes with cross-repo edges | Changes the index format and every id scheme. |
| 306 | Pluggable storage architecture *exploration* | "Explore" with no acceptance criterion cannot be finished, only abandoned. |

### 2.10 epic / tracking — 3

#79 (epic), #81, #88. Already correct. Only change: drop the type labels from #79 and give it
`type/epic`.

---

## 3. Duplicates and overlaps

### 3.1 #295 is contained in #315

#315 ("Split large modules by responsibility") lists the split explicitly, including
`repo2graph/mcp/` → `server.py`, `tools.py`, `transport.py`. #295 ("Isolate HTTP server,
authentication, and MCP tool logic into separate modules") is that one bullet restated.

> **Drafted reply for #295**
>
> Closing as a duplicate of #315, which already lists this exact split — `repo2graph/mcp/` into
> `server.py` / `tools.py` / `transport.py` — as one of its three module groups.
>
> Nothing here is lost: the extra detail this issue carries that #315 does not is the *auth*
> boundary specifically, so I have copied that into #315 rather than leaving it in a second issue.
> If the MCP split ends up being worth doing on its own ahead of `graph/` and `query/`, it is
> better tracked as a task on #315 than as a rival issue, because the two would otherwise disagree
> about what the target module layout is.

### 3.2 #291 is superseded by #407

#291 says the project *"appears to target an MCP SDK 1.x decorator API while allowing a broader
dependency range that may install incompatible versions."* #407 names the version (mcp 1.30.0), the
symptom (never answers the first tool call on the parallel path), and the failing test
(`tests/test_mcp.py::test_iss90_tools_call_over_serv…`). Since #291 was filed, `pyproject.toml`
pinned `mcp>=1.0,<3.0` and `serve()` branches on `hasattr(Server, "list_tools")` to drive either
SDK generation.

> **Drafted reply for #291**
>
> Superseded by #407, and partly already done.
>
> The "decide supported SDK versions explicitly" half shipped: `pyproject.toml` declares
> `mcp>=1.0,<3.0`, `SDK_SPEC` in `repo2graph/mcp.py:653` holds the same string so the error message
> cannot drift from the dependency, and `serve()` branches on `hasattr(Server, "list_tools")` to
> drive the 1.x decorator API or the 2.x registration API. The upper bound is `<3.0` because an
> unreleased 3.x is the untested thing, not 2.x.
>
> What is left is the concrete defect underneath this issue, which #407 now states with evidence:
> under mcp 1.30.0 the server never answers its first tool call on the parallel path. That is a
> reproducible bug with a named failing test, so it is the better place to do the work.
>
> Closing in favour of #407 — say the word if you would rather keep this open as the umbrella.

### 3.3 #88 is a child of #304, and they should say so

#304 asks for benchmark fixtures and regression gates in general; #88 records the specific measured
gap (*"nothing was run at 50k/100k+ scale"*). Neither references the other. Not a duplicate — #88 is
the acceptance criterion #304 is missing. **Action:** cross-link, keep both, and add "a fixture
above 3,000 files, per #88" to #304's criteria.

### 3.4 #282 / #283 / #290 / #307 all circle the budget vocabulary

Four issues about the same surface, none referencing another:

- **#282** — separate retrieval budget from rendered-context budget in all interfaces.
- **#283** — enforce final output limits after MCP/JSON-RPC wrapping.
- **#290** — tokenization strategy transparency (char→token estimates).
- **#307** — standardize CLI flag naming, explicitly citing `--budget` / `--budget-tokens`.

**#282 conflicts with a documented invariant** and must be scoped before it is worked.
`AGENTS.md` ("Two budget models coexist — do not unify them") records that `retrieve()`'s
`budget_chars` bounds chunk text only while `pack_context()`'s bounds the whole rendered markdown,
that this is deliberate, and that `retrieve()`'s accounting is pinned by two tests and consumed by
`cmd_query` — so harmonising them changes `repo2graph query`'s output for every existing caller.
#282's "rename options/API parameters" is therefore partly a won't-do and partly a
documentation/clarity task. **Action:** retitle to the clarity half, link AGENTS.md, and drop the
renaming from the acceptance criteria.

**Recommendation:** make #307 the umbrella for CLI vocabulary, link #282/#290 under it, and keep
#283 separate — it is a distinct enforcement question, not a naming one.

---

## 4. Already shipped, or shipped in part

Working these as written would mean re-implementing something that exists. Each needs a rescope
comment, not a close.

### 4.1 #289 — graph expansion *is* already configurable, at the API level only

> "Add per-edge controls: include/exclude edge kinds; direction; confidence threshold."

All three exist on `Index.expand()` (`repo2graph/query.py:483-501`): `edge_dirs` selects kinds and
directions, defaulting to `DEFAULT_EDGE_DIRS` (`query.py:77`), with `ALL_EDGE_DIRS` (`query.py:89`)
as the explicit "no filter" value; `min_confidence` (`query.py:489`) gates `CALLS` edges.

What does **not** exist is any way to reach them from outside Python. `--min-confidence` is
wired to exactly one subcommand — `explain retrieval` (`cli.py:1380`) — and there is no
`--edge-types` / `--edge-dirs` flag anywhere, nor an MCP argument.

**Rescope to:** "Expose `edge_dirs` and `min_confidence` on `query`, `rag` and the MCP tools."
That is a much smaller, better-defined issue, and it inherits the MCP clamping rule in AGENTS.md
("Every MCP tool argument is caller-hostile") as an acceptance criterion.

### 4.2 #283 — the MCP half is done

> "Add a final serialization-stage cap for CLI JSON, MCP payloads…"

The MCP server already clamps and re-measures the rendered pack before returning
(`MCP_MAX_BUDGET_TOKENS = 12000`, enforced in the handler). The genuinely open part is the **CLI
JSON** path and the error/diagnostic text, which no ceiling covers. **Rescope to those.**

### 4.3 #263 — the deployment security guide exists

> "Add `docs/security-model.md` and update deployment docs."

`docs/ENTERPRISE_DEPLOYMENT.md`, `docs/ACTION_SECURITY.md`, `docs/PRIVACY.md`,
`docs/SECURITY-AUDIT.md` and `.github/SECURITY.md` are all present and current. What is missing is
narrower: a **single threat model** — assets, actors, trust boundaries — that those five pages can
hang off. **Rescope to "write the threat model; link the five existing pages from it".**

### 4.4 #287 — half is by design and is documented

> "Tiny residual file content may become unsearchable."

That is `chunks.py:185` dropping a `file_residual` chunk under 40 characters after symbol spans
are carved out, and AGENTS.md documents it as intended ("A file with little residue emits no
file-level chunk"), including its consequence for fixtures. The **large-symbol** half —
AST-aware chunk boundaries instead of a fixed ~4000-character cut — is a real open question.
**Rescope to the large-symbol half**, and if the residual threshold should change, that is its own
issue with its own argument.

### 4.5 #349 — the duplication is deliberate, and the issue does not say so

> "`NODE_TYPES` and `EDGE_TYPES` … are duplicated across `repo2graph/viz.py` and
> `repo2graph/export.py`."

True, and `viz.py:38-41` explains why in a comment directly above the copy: `export.py` already
does `from .viz import ... write_html`, so importing back the other way would be an import cycle.
The comment ends "Keep both wordings in sync by hand."

The issue is still worth doing — a hand-synced copy is exactly the thing that drifts, and the
wordings have *already* diverged (`export.NODE_TYPES["repo"]` says "the repository itself; one per
index", `viz.NODE_TYPE_DESC["repo"]` says "the repository itself"). But the fix is not "delete one
and import the other": it needs a third module that neither imports from. **Rescope to** "extract
to a new leaf module both can import", and note the cycle in the issue body so nobody rediscovers
it.

### 4.6 #383 — now has a documented behaviour to point at

Not shipped, but the behaviour it describes is now written down: the "Stale indexes" section of
`docs/limitations.md` (added in this PR) states that nothing watches the filesystem, that
`repo2graph doctor` checks index integrity and vector drift rather than working-tree drift, and
that the MCP server auto-builds only a *missing* index. **Action:** link it from #383 so the issue
starts from the documented baseline.

---

## 5. Drafted reply — the 22 underspecified issues

One template, because they share one defect. Fill the bracketed part per issue.

> **Triage note.** Marking this `needs-reproduction` — not because the concern is wrong, but
> because as written it cannot be started.
>
> This issue asserts [*the claim*] without naming the code that exhibits it. There is no file, no
> line and no reproduction, and the wording is hedged ("[*may / appears to / reportedly*]"), so the
> first task for anyone picking it up is to re-derive the evidence and decide whether the problem
> is real — which is research, not the fix, and it is work that gets redone by every person who
> looks at the issue.
>
> That matters here more than usual: of the 34 issues filed in this sweep, **four turned out to
> describe behaviour that already exists or is documented as deliberate** (#289's edge filters,
> #283's MCP ceiling, #263's deployment docs, #287's residual-chunk threshold) and two were
> duplicates of better-specified issues. The evidence step is not a formality.
>
> To move this out of `needs-reproduction`, it needs:
>
> 1. **The code.** `path/to/file.py:line`, with the relevant lines quoted.
> 2. **The trigger.** A command, an input, or a test that shows the behaviour — or, if it is a
>    missing capability rather than a defect, the call that fails and the error it returns.
> 3. **One acceptance criterion** that can be checked as true or false. "Audit every X" cannot;
>    "no `except Exception` remains in `repo2graph/mcp.py`" can.
> 4. **A check against `AGENTS.md`.** Several documented invariants look like bugs and are not —
>    the two budget models, the 40-character `file_residual` threshold, `splitlines()` vs
>    `split("\n")`, name-based call resolution. If the issue proposes changing one of these, it
>    needs to argue against the recorded reason.
>
> Cohort A (#338–#408) is the model: each of those names a file and a line, quotes the code, and
> states what should happen instead.
>
> Not closing this — it is staying open as a lead. Reopening the scope as a question in
> [Discussions → Ideas] is also fine if it turns out to be a direction rather than a defect.

Per-issue additions worth making before posting:

| # | Extra note |
|---|---|
| 313 | *"The project **reportedly** has strict mypy enabled"* — it does, and `pyproject.toml` documents the exact remaining debt (179 errors, concentrated in mcp/graph/parse/cli) with a ranked plan in `DONE.md`. Rescope to "take the next module off the overrides list". |
| 314 | `except Exception` is a real pattern here, but the count and the locations are not in the issue. Start with `rg -n "except Exception" repo2graph/ \| wc -l` and list the files. |
| 318 | Three different tasks (dead code, stale scaffolding, unused imports). `ruff` already enforces the third. Split or narrow. |
| 316 | Overlaps `tests/test_security_mutations.py` and `test_secrets_hardening.py`. Say what those do not cover. |
| 307 | The `--budget` / `--budget-tokens` pair it cites is the documented two-budget split, not an inconsistency — see §3.4 and `AGENTS.md`. Rescope to the rest of the flag surface. |

---

## 6. Drafted replies — proposed out of scope

**These are proposals. None should be posted without a maintainer decision**, because each closes
off a direction rather than resolving a defect.

### #400 — VS Code extension

> This is a good idea and I do not think it belongs in this repository.
>
> A VS Code extension is a second product: TypeScript, its own build and test setup, its own
> release channel, Marketplace review, and a compatibility surface against the editor API that
> moves independently of anything here. The Python package would gain a dependency on a release
> process no CI job in this repo can run.
>
> The part that *is* in scope is making an extension cheap for someone else to build: a stable,
> documented way to ask "what is the neighbourhood of this symbol" without shelling out to the
> CLI. That is #384 (`repo_find_symbol`) plus #385 (`repo_read`) — with both, an extension is a
> thin MCP client over a stdio server and needs nothing further from this side.
>
> Proposing we close this as out of scope and treat #384/#385 as the enabling work. Happy to keep
> it open as a `help wanted` tracking issue instead if you would rather leave the door open —
> your call.

### #399 — npx-installable launcher

> Same shape as #400, smaller: an npm package means a second registry, a second release step, and
> a second place the version can drift from `pyproject.toml` — and `scripts/check_version.py`
> already guards eleven surfaces for exactly that reason.
>
> The premise is fair — most MCP config snippets in the wild are written with `npx`. But `uvx`
> already gives a zero-install one-liner, and the npm package would be a shim that shells out to
> it, so the user still needs `uv` on the machine. That trades a real release-process cost for a
> cosmetic gain.
>
> Proposing out of scope. If MCP client directories ever require an npm entry point to list a
> server, that changes the argument and this should be reopened.

### #396 — multi-repo indexes with cross-repo edges

> The use case is real and this is a much bigger change than it reads as.
>
> Every node id in the index is repo-relative (`sym:<path>::<Qualname>`, `file:<path>`), so
> cross-repo edges need a repo component in the id scheme — which changes the artifact format,
> every consumer of `nodes.jsonl` / `edges.jsonl`, the citation anchors, `graph.graphml` and
> `graph.cypher`, and the five committed `examples/` fixtures. Call resolution would also have to
> decide what a name match *across* repositories means, when it is already name-based and
> ambiguous within one.
>
> Proposing we mark it out of scope for now rather than leave it open as something that looks
> actionable. If it comes back it should come back as a design document first, not an
> implementation issue.

### #306 — pluggable storage architecture exploration

> Closing-as-unactionable proposal, for a reason that is about the issue rather than the idea.
>
> "Explore" with no acceptance criterion has no finish line. As written this can absorb any amount
> of effort and never be closeable, which makes it permanent backlog weight.
>
> The underlying concern — in-memory dict/list storage limits scale — is real and is already
> tracked concretely by #299 (default resource limits for nodes, edges, chunks and memory), which
> has a testable outcome. Suggest closing this and reopening a storage issue when there is a
> specific repository size that fails, with the number that failed.

---

## 7. Proposed labels

Full taxonomy, rationale and the consolidation table: **[docs/TRIAGE.md](TRIAGE.md) §4**.

Summary of what this report needs that does not exist today:

| Label | Colour | Purpose |
|---|---|---|
| `needs-reproduction` | `#d876e3` | Asserts a defect without naming the code that exhibits it. |
| `out-of-scope` | `#ffffff` | Deliberately not doing this; the reply says why. |
| `needs-decision` | `#fbca04` | Blocked on a maintainer call, not on work. |
| `superseded` | `#cfd3d7` | A better-specified issue replaced it. |
| `type/epic` | `#5319e7` | Tracking issue; children carry the real types. |

And six labels that exist but duplicate another axis, proposed for removal after their issues are
migrated: `fix`, `feat`, `docs`, `chore`, `test`, `priority/high`.

---

## 8. Applying this

Nothing below has been run.

**Step 1 — create the new labels** (additive, safe):

```bash
gh label create needs-reproduction --color d876e3 \
  --description "Asserts a defect without naming the code that exhibits it"
gh label create out-of-scope --color ffffff \
  --description "Deliberately not doing this; see the issue reply for why"
gh label create needs-decision --color fbca04 \
  --description "Blocked on a maintainer decision, not on work"
gh label create superseded --color cfd3d7 \
  --description "Replaced by a better-specified issue"
gh label create type/epic --color 5319e7 \
  --description "Tracking issue; children carry the real type labels"
```

**Step 2 — strip the contradictory labels.** 16 issues carry `bug`+`enhancement`; 12 carry both
priority scales; 3 carry `question` wrongly. These are the mechanical fixes and they are
reversible.

**Step 3 — apply `needs-reproduction` to the 21 in §2.8**, and post the §5 reply.

**Step 4 — the duplicates (§3) and the out-of-scope set (§6) need a maintainer decision first.**
That is the whole reason this report exists as a document rather than as 84 API calls.

**Step 5 — retire the six duplicate labels** only after steps 2–3, since deleting a label removes
it from every issue that carries it and that cannot be undone.
