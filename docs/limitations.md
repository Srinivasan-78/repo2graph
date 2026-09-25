# Limitations

What repo2graph gets wrong, on purpose or otherwise, and how to tell when it has. Most of the
static-analysis limitations here are also covered in
[TECHNICAL.md#where-it-guesses-and-why](../TECHNICAL.md#where-it-guesses-and-why); this page adds
what running the pipeline against five real, large, public repositories
([examples/](../examples/)) actually surfaced, with the measured numbers, and the limitations that
only show up at scale.

The short version — one row per limitation, no numbers — is the
["What it does — and what it does not"](../README.md#does-and-doesnt) table in the README. This page
is the long version, with the measurements behind each row.

## Static analysis, generally

- **`CALLS` is matched by name, not by type.** Two functions sharing a name are
  disambiguated using heuristics (same-file, same-directory, and explicit imports)
  to boost the confidence of the most likely candidates. If heuristics isolate a
  strong match, it gets a high confidence score; if they fail to break a tie,
  repo2graph falls back to producing up to `max_call_candidates` possible edges
  (default 5) with equal confidence and flags them as `ambiguous=True`. Filter to
  `confidence == 1.0` if you need certainty over recall. That boosting is current
  code; the VS Code / Django ambiguity table below was measured on 2026-09-17,
  before it existed, and is the raw name-match fan-out.
- **No arrow does not prove no call.** Dynamic dispatch — a string-keyed lookup, a plugin registry,
  `getattr`-style dispatch, a virtual call resolved only at runtime — is invisible to a reader that
  never executes anything.
- **Dependency injection resolves to the declaration, not the implementation.** A DI container —
  Spring's `@Autowired`, .NET's `IServiceCollection`, a NestJS provider, a hand-rolled registry —
  binds an interface to a concrete class at startup. The call site only ever names the interface's
  method, so that is what the name match sees: the `CALLS` edge lands on the abstract declaration,
  or fans out across every same-named implementation at `1/n` confidence, and never on the class the
  container actually injected. The candidates are still enumerable — walk `INHERITS` *into* the
  interface node to list every type that implements it — but which one runs is a runtime fact, and
  repo2graph never runs anything.
- **Reflection and dynamic imports are invisible.** `importlib.import_module(some_variable)`,
  Java reflection, JavaScript's dynamic `import()` with a computed specifier — none of these name a
  literal string tree-sitter can resolve, so no `IMPORTS`/`CALLS` edge is drawn for them.
- **Generated code is indexed like any other code**, with no marker distinguishing it. A `.pb.go` or
  a webpack bundle produces nodes and edges exactly as if a human had written it, which can dominate
  a repository's symbol count without representing a line anyone actually maintains by hand.
- **Framework magic is invisible unless it is also literal syntax.** Django's URL routing being
  resolvable by `repo2graph query` (see [examples/django](../examples/django/)) works because
  routes are declared as literal `path(...)` calls tree-sitter can see; a framework that builds
  equivalent routing purely from runtime metaprogramming would not be.

## What parsing five real repositories actually showed

Numbers below are from [`benchmarks/results.json`](../benchmarks/results.json) and each example's
`metadata.json`/`stats.json`, generated 2026-09-22 (`generated_at: 2026-09-22T08:12:20Z`,
`repo2graph_version: 1.6.0`) on a GitHub-hosted `ubuntu-latest` runner — not estimated. Unlike the
previous corpus, these artifacts *do* exercise the `cpp` preprocessor fallback and scoped call
resolution, so the tables below measure the mitigations rather than the raw behaviour they replaced.

### Macro-heavy C/C++ produces real tree-sitter parse errors

Tree-sitter parses raw source, which means C/C++ macros can produce syntax it cannot handle. `parse_errors` counts individual tree-sitter `ERROR` nodes inside a file's parse tree (see `repo2graph/parse.py`), not "files that failed to parse" — a single file can contribute many. This value is now visible as a `parse_errors` field on each file node and in the `stats.json` summary.

Current code uses a two-pass strategy for C/C++ files:
1. Parse raw source (Pass 1).
2. If errors are found, optionally run the system's `cpp` preprocessor (Pass 2) and parse the expanded output. If it yields fewer errors and output size constraints are met, `used_cpp=True` is recorded as a signal that macros were the problem. Symbol extraction and `start_line`/`end_line` stay on the original file — cpp is invoked with `-P`, which drops `# <linenum> "<file>"` markers, so adopting the preprocessed tree would make `chunks.py` slice the wrong on-disk rows.

Two counts matter and they answer different questions. `parse_errors` counts individual `ERROR`
nodes, so one pathological file can contribute hundreds; `files_with_parse_errors` counts how much
of the repository is affected at all. Across the five examples:

| Example | Files indexed | Files with errors | `parse_errors` | Used `cpp` fallback | Language |
|---|---:|---:|---:|---:|---|
| [Linux kernel](../examples/linux/) (`kernel/`, `fs/ext4/`, e1000 driver, `include/linux/`) | 3,660 | 1,356 (37%) | 14,631 | 65 | C |
| [TensorFlow](../examples/tensorflow/) (Python/C++ framework boundary) | 1,022 | 325 (32%) | 12,473 | 6 | C++ / Python |
| [Kubernetes](../examples/kubernetes/) (controllers/scheduler/API server) | 1,084 | 30 (2.8%) | 260 | 0 | Go |
| [Django](../examples/django/) (full repository) | 5,629 | 2 (0.04%) | 11 | 0 | Python |
| [VS Code](../examples/vscode/) (`src/vs/`) | 6,000 | 4 (0.07%) | 6 | 0 | TypeScript |

The `cpp` fallback fires on a small minority of the C/C++ files that have errors — 65 of Linux's
1,356, 6 of TensorFlow's 325. It is a targeted mitigation, not a general fix: it only engages when
the preprocessed parse yields *fewer* errors and the expanded output stays within the size cap, and
the remaining ~95% are files whose errors the preprocessor does not resolve.

The pattern is exactly what the C/C++ grammar's known weak spot predicts: the kernel and TensorFlow
lean heavily on preprocessor macros (`SYSCALL_DEFINE`, `EXPORT_SYMBOL`, conditional compilation,
C++ template metaprogramming) that tree-sitter's grammar does not expand, so it emits `ERROR` nodes
around syntax it cannot classify — it still recovers and extracts the symbols around the error, but
a macro-defined function or a heavily templated declaration can be missed entirely. Python, Go and
TypeScript — languages without a text-substitution macro system — show parse errors two to three
orders of magnitude lower on comparable file counts. If you are indexing a C or C++ codebase, expect
a non-trivial `parse_errors` count in `stats.json` and treat it as a floor on missed symbols, not a
crash.

### Call-name ambiguity scales with symbol reuse conventions, not repository size

These rates are from the same 2026-09-22 run, and they measure what survives scoped resolution: a
call is counted ambiguous only once tiers 0–5 have all failed to isolate a single candidate and
confidence is split `1/n`. `calls_scoped` counts the sites resolved by the same-class, same-file,
import and same-module tiers before that point.

| Example | `CALLS` edges | Resolved by scope | Ambiguous (matched >1 candidate) | Ambiguous rate |
|---|---:|---:|---:|---:|
| [TensorFlow](../examples/tensorflow/) | 58,556 | 24,989 | 12,487 | 21.3% |
| [Django](../examples/django/) | 189,381 | 35,938 | 35,338 | 18.7% |
| [VS Code](../examples/vscode/) | 450,921 | 137,142 | 82,225 | 18.2% |
| [Kubernetes](../examples/kubernetes/) | 62,630 | 17,059 | 10,948 | 17.5% |
| [Linux kernel](../examples/linux/) | 68,785 | 47,205 | 3,155 | 4.6% |

**This table used to tell a different story, and the change is the point.** On the pre-scoped-
resolution corpus, VS Code sat at 33% — an order of magnitude above the Linux kernel's 0.3% — and
the honest conclusion then was that ambiguity is a property of language convention: TypeScript's
habit of putting `dispose()`, `getId()` and `register()` on dozens of unrelated types against C's
flat, prefix-disciplined naming (`ext4_*`, `e1000_*`).

Scoped resolution collapsed that spread. VS Code fell from 33% to 18.2%, because most of those
same-name method calls are now resolved by the same-class or same-file tier before they can fan out.
Four of the five repositories now sit within four points of each other regardless of language, which
means the *residual* ambiguity — what is left after scope is exhausted — is a fairly uniform
property of name-based resolution rather than a per-language trait. Two rates moved the other way
(TensorFlow 13%→21.3%, Linux 0.3%→4.6%) for the same underlying reason: far more call sites now
resolve to in-repo candidates at all instead of becoming a single `CALLS_EXTERNAL` edge, so calls
that were previously invisible to this metric are now inside it, some of them ambiguously.

The Linux kernel remains the outlier at 4.6%, and its `calls_scoped` share is the highest of the
five — 69% of its `CALLS` edges are settled by scope alone. That is C with no method dispatch
behaving exactly as the original analysis predicted; it is the only part of that analysis the new
numbers leave standing.

### Windows filename-length limits can silently shrink a checkout

Reproducing the VS Code example's full (unscoped) `src/vs/` tree on Windows hits `git checkout`
errors like `Filename too long` for paths that exceed Windows' historical ~260-character `MAX_PATH`
(observed on `src/vs/platform/agentHost/test/**` and `src/vs/workbench/contrib/**/__snapshots__/**`
when this project generated its examples on a Windows workstation — the reason the corpus is now
generated on a Linux CI runner instead). This is a Windows/git limitation, not a repo2graph
one — repo2graph never sees the files git failed to write to disk — but it means a checkout done on
Windows can legitimately discover fewer files than the same commit checked out on Linux or macOS.
`examples/vscode/README.md` documents the file cap this project applied on top of that; if you hit
this yourself, `git config core.longpaths true` (Windows) is the standard workaround, applied before
cloning, not by repo2graph.

## Cross-language resolution

TensorFlow ([examples/tensorflow](../examples/tensorflow/)) is this project's test case for a
repository where Python genuinely calls into C++. What actually holds: within-language `IMPORTS` and
`CALLS` resolution works exactly as it does for a single-language repository — the Python-side
`framework`/`eager` modules resolve their own imports and calls, and the C++-side `framework`/
`common_runtime` sources resolve theirs. What does **not** hold: repo2graph draws no edge *across*
the Python/C++ boundary. The actual boundary crossing in TensorFlow happens through generated
pybind11 bindings and the SWIG-era `_pywrap_*` extension modules, which are build artifacts, not
source repo2graph indexes — the Python call site names an imported native module, which becomes a
`CALLS_EXTERNAL` edge (an edge to a named-but-unresolved external symbol), not a link to the C++
function it actually reaches at runtime. This is not a bug to be fixed by better name matching; it
is a structural limit of static, source-only analysis against a build-generated boundary, and the
tensorflow example's `flows/` queries were chosen specifically to make this limit visible rather
than to paper over it.

## Extreme-scale

Kubernetes, TensorFlow, VS Code and the Linux kernel are not indexed whole in
[examples/](../examples/) — see each one's `README.md`, "Why this scope," and
[`examples/repositories.yaml`](../examples/repositories.yaml). Kubernetes' and TensorFlow's full
trees run to hundreds of thousands of files once vendored dependencies and generated bindings are
counted; VS Code's `extensions/` directory alone bundles dozens of independent npm dependency trees;
the Linux kernel's full tree is every architecture-specific subsystem and every merged driver at
once. None of that is a size a single, reproducible, CI-friendly run should attempt whole — indexing
it would measure disk and clone bandwidth more than repo2graph itself. Each of the four scoped
examples instead indexes one architecturally coherent, representative slice, chosen and documented
per repository (see `why:` in `repositories.yaml`), with the exact commit and paths pinned so the
result is reproducible. This is a genuine trade-off, not a way of hiding a failure: repo2graph did
not fail to index the rest of any of these repositories — it was deliberately not asked to, for the
reasons stated per repository.

## Stale indexes

An index under `.r2g` is a **snapshot of the tree it was built from**, and nothing in repo2graph
watches the filesystem. Edit a function, and every artifact — `graph.json`, `chunks.jsonl`,
`graph.html`, the vectors — keeps describing the version that existed at build time. A query will
answer confidently from it, and the `[cite: path:start-end]` anchor will point at line numbers that
have since moved. There is no timestamp check in the query path, by design: adding one would mean
stat-ing every indexed file on every call.

What that means in practice:

- **Rebuild after you change code.** `repo2graph build <path> -o .r2g --incremental` re-parses only
  the files whose content hash moved (recorded per file node at build time), so the cost is
  proportional to the diff, not to the repository.
- **In CI, rebuild on every push.** That is what the [GitHub Action](github-action.md) is for; a
  graph committed next to the code cannot drift from it by more than one commit.
- **Over MCP, the server auto-builds only a *missing* index**, never an outdated one — and in HTTP
  mode not even that, without `--allow-auto-build`. A long-lived `repo2graph-mcp` process serving a
  branch you keep committing to will go stale; rebuild the index out-of-band, or restart the server
  after a large change.
- **`repo2graph doctor` does not detect this.** It reports index *integrity* — a corrupt
  `chunks.jsonl`, a missing `vectors.meta.json`, vectors whose `build_id` or per-chunk text hashes
  no longer match the chunks they were computed from (`status: "stale"` in
  `repo2graph/integrity.py`). That is vector-vs-chunk drift *inside* the index. It says nothing
  about whether your working tree has moved on since the build.
- **The symptom to watch for** is a citation whose line range no longer contains what the answer
  claimed, or a symbol the answer references that no longer exists. Both mean rebuild, not a bug.

## Graph freshness of the shipped examples

Every artifact under `examples/` is pinned to a specific commit recorded in that example's
`metadata.json`. None of them update themselves, and none should be read as describing the
repository's current `main`/`master` — see
[docs/benchmarks.md#staleness](benchmarks.md#staleness) for how re-running the generator against a
newer commit is expected to (and will) produce different numbers.
