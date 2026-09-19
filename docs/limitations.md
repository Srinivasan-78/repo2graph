# Limitations

What repo2graph gets wrong, on purpose or otherwise, and how to tell when it has. Most of the
static-analysis limitations here are also covered in
[TECHNICAL.md#where-it-guesses-and-why](../TECHNICAL.md#where-it-guesses-and-why); this page adds
what running the pipeline against five real, large, public repositories
([examples/](../examples/)) actually surfaced, with the measured numbers, and the limitations that
only show up at scale.

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
`metadata.json`/`stats.json`, generated 2026-09-17 (`generated_at: 2026-09-17T15:36:04Z`,
`repo2graph_version: 1.5.1`) — not estimated. Those artifacts predate the `cpp`
preprocessor fallback and CALLS heuristic-confidence work (merged 2026-09-18). Until
the five-example corpus is regenerated, the tables are the raw tree-sitter
parse-error rate and the pre-heuristic CALLS ambiguity rates, not a measurement of
those later mitigations.

### Macro-heavy C/C++ produces real tree-sitter parse errors

Tree-sitter parses raw source, which means C/C++ macros can produce syntax it cannot handle. `parse_errors` counts individual tree-sitter `ERROR` nodes inside a file's parse tree (see `repo2graph/parse.py`), not "files that failed to parse" — a single file can contribute many. This value is now visible as a `parse_errors` field on each file node and in the `stats.json` summary.

Current code uses a two-pass strategy for C/C++ files (this path did not exist when
the table below was measured):
1. Parse raw source (Pass 1).
2. If errors are found, optionally run the system's `cpp` preprocessor (Pass 2) and parse the expanded output. If it yields fewer errors and output size constraints are met, `used_cpp=True` is recorded as a signal that macros were the problem. Symbol extraction and `start_line`/`end_line` stay on the original file — cpp is invoked with `-P`, which drops `# <linenum> "<file>"` markers, so adopting the preprocessed tree would make `chunks.py` slice the wrong on-disk rows.

The published counts are the 2026-09-17 raw-parse figures. Across the five examples:

| Example | Files indexed | `parse_errors` | Language |
|---|---:|---:|---|
| [Linux kernel](../examples/linux/) (`kernel/`, `fs/ext4/`, e1000 driver, `include/linux/`) | 3,660 | 14,663 | C |
| [TensorFlow](../examples/tensorflow/) (Python/C++ framework boundary) | 1,022 | 12,469 | C++ / Python |
| [Django](../examples/django/) (full repository) | 5,637 | 11 | Python |
| [Kubernetes](../examples/kubernetes/) (controllers/scheduler/API server) | 1,082 | 260 | Go |
| [VS Code](../examples/vscode/) (`src/vs/`) | 6,000 | 6 | TypeScript |

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

These rates are from the same 2026-09-17 run: every same-name fan-out counts as
ambiguous. Current CALLS heuristics may narrow some of those sites; they are not
in these numbers.

| Example | `CALLS` edges | Ambiguous (matched >1 candidate) | Ambiguous rate |
|---|---:|---:|---:|
| [VS Code](../examples/vscode/) | 223,594 | 73,028 | 33% |
| [Django](../examples/django/) | 104,418 | 18,797 | 18% |
| [TensorFlow](../examples/tensorflow/) | 42,395 | 5,631 | 13% |
| [Kubernetes](../examples/kubernetes/) | 37,612 | 5,192 | 14% |
| [Linux kernel](../examples/linux/) | 70,079 | 227 | 0.3% |

VS Code's ambiguity rate is an order of magnitude above the others, consistent with TypeScript's
convention of many small classes implementing a shared interface (`dispose()`, `getId()`,
`register()` — the same method name on dozens of unrelated types). The Linux kernel's near-zero rate
is consistent with C having no method dispatch at all — every call site names one free function, and
C's flat, prefix-disciplined naming convention (`ext4_*`, `e1000_*`) means two unrelated functions
rarely share a bare name. Ambiguity is a property of the *language and codebase convention*, not of
scale: Kubernetes and TensorFlow, similar in file count, land within a point of each other.

### Windows filename-length limits can silently shrink a checkout

Reproducing the VS Code example's full (unscoped) `src/vs/` tree on Windows hits `git checkout`
errors like `Filename too long` for paths that exceed Windows' historical ~260-character `MAX_PATH`
(observed on `src/vs/platform/agentHost/test/**` and `src/vs/workbench/contrib/**/__snapshots__/**`
during this project's own example generation). This is a Windows/git limitation, not a repo2graph
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

## Graph freshness

Every artifact under `examples/` is pinned to a specific commit recorded in that example's
`metadata.json`. None of them update themselves, and none should be read as describing the
repository's current `main`/`master` — see
[docs/benchmarks.md#staleness](benchmarks.md#staleness) for how re-running the generator against a
newer commit is expected to (and will) produce different numbers.
