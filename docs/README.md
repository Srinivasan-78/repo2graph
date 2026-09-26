# Documentation

The main [README](../README.md) is the front door; this is the index for everything past it.

repo2graph exists to give coding agents — and the humans driving them — **trustworthy, cited
answers about unfamiliar codebases**. Every page below is in service of one of the three words in
that sentence: *trustworthy* (what it does and does not know), *cited* (how a block is anchored to
source), *unfamiliar* (getting oriented without reading everything).

## Getting started

- **[2-minute quickstart](quickstart.md)** — nothing installed to a cited answer, with the
  expected output at each step, the five starter questions, the MCP one-liner, and a
  symptom → `doctor` check → fix table.
- **[MCP client configuration](mcp.md#client-configuration)** — Claude Code, Claude Desktop,
  Cursor, and any other stdio client.
- **[Claude Code integration](integrations/claude-code.md)** — the first-supported client, end to
  end: install, verify, the six tools and their bounds, the five questions to start with, the
  `CLAUDE.md` block that gets the agent to actually use them, and what not to rely on.
- **[Cursor integration](integrations/cursor.md)** — the same server, and the three things that
  differ: the config file, the scope model, and the rules file that is load-bearing here because
  Cursor's own search is good enough to answer without calling a tool.
- **[CLI reference](cli.md)** — every flag, what it counts, budget accounting.
- **[Python API](python-api.md)** — `build()`, `dump_all()`, `Index`, the same objects the CLI uses.
- **[GitHub Action](github-action.md)** — inputs, outputs, CI wiring.

## Understanding repo2graph

- **[TECHNICAL.md](../TECHNICAL.md)** — the tree-sitter pipeline, the graph model, confidence
  scoring, budget accounting, "where it guesses and why."
- **[Reference: what is in the index](reference.md)** — every file, node type and edge type the
  output can contain.
- **[Output schema](OUTPUT_SCHEMA.md)** — the contract for edge records: what `method`,
  `confidence` and `evidence` mean, what `confidence` deliberately does *not* encode, where each
  surface puts its citations, and what the bug-report bundle does and does not carry.
- **[Indexing](INDEXING.md)** — how the graph is built, the determinism guarantees and the tests
  that hold them, the four exclusion layers, and how staleness is computed.
- **[MCP server](mcp.md)** — the five tools, their argument bounds, client configs.
- **[Why a graph, not just search](why-graph.md)** — what each is actually good at, with the
  citation-following behavior [examples/django](../examples/django/) demonstrates as the concrete
  case.
- **[Limitations](limitations.md)** — static-analysis limitations generally, plus what parsing five
  real repositories actually showed (parse-error rates, call ambiguity, cross-language resolution).
- **[How it compares](comparison.md)** — repo2graph against Graphify, the Obsidian Code Graph
  plugin, grep and embedding RAG, including where each of them is the better answer.

## Start here, by what you're trying to do

| You are… | Read | Then run |
|---|---|---|
| **Joining an unfamiliar codebase** and want to stop reading files at random | [why-graph.md](why-graph.md) · [examples/django](../examples/django/) for a worked cross-module trace | `repo2graph build . -o .r2g` then open `.r2g/human/graph.html`, then `repo2graph rag "<your question>" -o .r2g` |
| **Driving a coding agent** (Claude Code, Cursor, any MCP client) and tired of it grepping badly | [mcp.md](mcp.md) — the five tools, their argument ceilings, client configs | `claude mcp add repo2graph -- uvx --from "repo2graph[mcp]" repo2graph-mcp .` |
| **Reviewing a PR** and need the blast radius of a changed symbol | [reference.md](reference.md) for what each edge kind means · [limitations.md](limitations.md) for what an absent edge does *not* prove | `repo2graph build . -o .r2g --git-history 500` then `repo2graph explain node "sym:<path>::<name>" -o .r2g` |
| **Maintaining an open-source project** and answering "where do I start" for the tenth time | [github-action.md](github-action.md) — inputs, outputs, publishing the map to a branch | add `Srinivasan-78/repo2graph@v2` to a workflow with `commit-branch: graph` |

The positioning behind those four framings — the message hierarchy, the proof point for each
claim, and the copy for every surface — is in [POSITIONING.md](../POSITIONING.md).

Also a good fit:

- **A large monorepo.** [examples/kubernetes](../examples/kubernetes/) is the concrete case:
  "what calls the pod controller" is a graph-traversal question, not a grep pattern.
- **Architecture discovery and impact analysis.** "What would changing this interface break" is
  exactly what `CALLS`/`IMPORTS`/`INHERITS` edges answer.

Less value:

- **A tiny repository.** Building an index for a 10-file script costs more setup than reading the
  files directly would.
- **Mostly generated or vendored code.** The graph indexes it exactly like hand-written code, with
  no marker distinguishing the two — see [docs/limitations.md](limitations.md).
- **A language repo2graph does not parse deeply.** Unsupported languages still appear as file nodes
  (nothing goes missing from the map), but get no function/class/call-level structure — see
  [the language list](../README.md#languages) and [reference.md](reference.md#languages).
- **Highly dynamic, runtime-decided architecture.** Plugin registries, reflection-heavy dispatch and
  dependency-injection containers are invisible to a static reader — see
  [docs/limitations.md](limitations.md).
- **A tree you are actively rewriting.** The index is a snapshot and nothing watches the
  filesystem; if you are not going to rebuild, its citations will point at lines that have moved —
  see [docs/limitations.md#stale-indexes](limitations.md#stale-indexes).

## Real-world examples

Five public repositories, each indexed at a pinned commit, with the generated graph committed and
the reproduction command documented:

- **[examples/README.md](../examples/README.md)** — the index: what each example contains, the
  summary table, how to reproduce or add one.
- **[Kubernetes](../examples/kubernetes/)** · **[TensorFlow](../examples/tensorflow/)** ·
  **[Django](../examples/django/)** · **[VS Code](../examples/vscode/)** ·
  **[Linux kernel](../examples/linux/)**
- **[docs/examples.md](examples.md)** — the generation pipeline itself, how to add a new repository.
- **[examples/ATTRIBUTIONS.md](../examples/ATTRIBUTIONS.md)** — license and attribution for every
  repository analyzed, and exactly what was and was not copied from each.

## Performance and benchmarks

- **[BENCHMARK.md](../BENCHMARK.md)** — canonical evaluation across 25 reproducible tasks and 5 archetypes: query correctness (100% vs ripgrep 80% / agent 56%), citation accuracy, context footprint, and query latency.
- **[docs/benchmarks.md](benchmarks.md)** — real numbers from the five large-scale public repositories (Kubernetes, TensorFlow, Django, VS Code, Linux kernel): clone/build time, node/edge counts, methodology, staleness.
- **[docs/PERFORMANCE.md](PERFORMANCE.md)** — controlled, hardware-comparable numbers on a synthetic
  fixture and this project's own self-hosted graph.
- **[benchmarks/](../benchmarks/)** — the machine-readable `results.json` (scale corpus) and `results_v2.json` / `tasks.json` (evaluation corpus), plus reproduction runners.

## Security and privacy

- **[.github/SECURITY.md](../.github/SECURITY.md)** — the policy and how to report a vulnerability
  privately. (The root `SECURITY.md` is a redirect stub kept only so old links don't 404.)
- **[docs/THREAT_MODEL.md](THREAT_MODEL.md)** — assets, trust boundaries, what an attacker could
  try against each surface, what stops it, and what is explicitly out of scope. Every open security
  gap is named with its issue number.
- **[docs/deployment-security.md](deployment-security.md)** — a trust boundary and a
  supported/not-recommended verdict for each of the six deployment shapes (trusted-local CLI
  through multi-tenant HTTP), plus a worked hardened reverse-proxy example, token/OIDC rotation,
  and artifact retention.
- **[docs/PRIVACY.md](PRIVACY.md)** — whether source ever leaves the machine (one path does, three
  others open a connection carrying none of it), what is written where — including the three
  locations outside `-o` — what is logged, and how to delete all of it.
- **[docs/secure-configuration.md](secure-configuration.md)** — copy-paste configurations:
  exclusion patterns worth adding, a provably offline build, stdio and HTTP MCP, CI, and how to
  forbid `rag --answer` in a shared environment.
- **[docs/privacy-audit-2026-09-25.md](privacy-audit-2026-09-25.md)** — the data-handling audit:
  every outbound path and every write location enumerated from the code, with what the pass
  corrected.
- **[docs/ACTION_SECURITY.md](ACTION_SECURITY.md)** — the GitHub Action's own guardrails:
  permission hardening, the threat model for a workflow that runs on untrusted input, and what
  the Action deliberately does not expose (`--answer` among them).
- **[docs/SECURITY-AUDIT.md](SECURITY-AUDIT.md)** — the most recent whole-repository security audit.
- **[docs/ENTERPRISE_DEPLOYMENT.md](ENTERPRISE_DEPLOYMENT.md)** — running the CLI, Action or MCP
  server inside an organization.
- **[docs/PRODUCTION_READINESS.md](PRODUCTION_READINESS.md)** — the audit findings, classified.

## Development

- **[.github/CONTRIBUTING.md](../.github/CONTRIBUTING.md)** — setup, the three CI gates, the branch
  model, and the test house style reviewers hold you to.
- **[docs/ARCHITECTURE.md](ARCHITECTURE.md)** — the module map for people changing the code: what
  each module owns, which way dependencies run, the two import cycles, and where a change of each
  kind goes.
- **[docs/parser-development.md](parser-development.md)** — adding a language, end to end:
  `LANG_CFG`, `EXT_LANG`, inheritance clauses, import resolution, the two tests, the six docs that
  a test will fail without.
- **[docs/good-first-issues.md](good-first-issues.md)** — seven starter tasks, each with a code
  pointer, acceptance criteria, and the catch that makes it harder than it looks.
- **[LANGUAGE_SUPPORT.md](../LANGUAGE_SUPPORT.md)** — strategic language support, parser failure analysis, priority tiers (TypeScript, Python, JVM, Go), and ecosystem relationship opportunities.
- **[Language RFCs](rfcs/rfc-framework-relationship-graph.md)** — proposals for ecosystem relationship expansion, deep TypeScript, deep Python, and JVM vs Go support.
- **[Roadmap Issues](ROADMAP_LANGUAGE_ISSUES.md)** — prioritized tracking issues for language and ecosystem features.
- **[AGENTS.md](../AGENTS.md)** — repo-specific rules that override default behavior (encoding,
  text slicing, budget models) — read before editing source under `repo2graph/`.
- **[docs/publishing.md](publishing.md)** — the branch model, the pre-release checklist, and how a
  release ships to PyPI, the MCP Registry and the Marketplace.
- **[npm/README.md](../npm/README.md)** — the `npx`-installable MCP launcher: what it is, its
  fallback order, and the release story that pairs it with the PyPI release.
- **[docs/rfc-incremental-indexing.md](rfc-incremental-indexing.md)** — where an incremental
  rebuild actually spends its time, measured, and the proposal that follows from it. The
  conclusion is not the intuitive one.
- **[docs/BACKLOG.md](BACKLOG.md)** — known gaps, deliberately-not-done items, and why.

## Community and governance

- **[docs/COMMUNITY.md](COMMUNITY.md)** — where to ask what: issues vs. Discussions, and what each
  Discussions category is for.
- **[docs/TRIAGE.md](TRIAGE.md)** — how an issue gets classified, what makes one workable, what
  qualifies as `good first issue`, and the label taxonomy.
- **[docs/issue-triage-2026-09-25.md](issue-triage-2026-09-25.md)** — the most recent full pass over
  every open issue: classification, duplicates, what is already shipped, and drafted replies.
- **[POSITIONING.md](../POSITIONING.md)** — what repo2graph claims, what it deliberately does not,
  and the copy for every outward-facing surface.
- **[CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md)** — Contributor Covenant 2.1, applying to issues,
  pull requests and discussion threads alike.
