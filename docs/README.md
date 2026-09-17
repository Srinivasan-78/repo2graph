# Documentation

The main [README](../README.md) is the front door; this is the index for everything past it.

## Getting started

- **[Install and quick start](../README.md#install)** — the main README covers this directly;
  there is no separate copy here to keep in sync.
- **[CLI reference](cli.md)** — every flag, what it counts, budget accounting.
- **[Python API](python-api.md)** — `build()`, `dump_all()`, `Index`, the same objects the CLI uses.
- **[GitHub Action](github-action.md)** — inputs, outputs, CI wiring.

## Understanding repo2graph

- **[TECHNICAL.md](../TECHNICAL.md)** — the tree-sitter pipeline, the graph model, confidence
  scoring, budget accounting, "where it guesses and why."
- **[Reference: what is in the index](reference.md)** — every file, node type and edge type the
  output can contain.
- **[MCP server](mcp.md)** — the three tools, their argument bounds, client configs.
- **[Why a graph, not just search](why-graph.md)** — what each is actually good at, with the
  citation-following behavior [examples/django](../examples/django/) demonstrates as the concrete
  case.
- **[Limitations](limitations.md)** — static-analysis limitations generally, plus what parsing five
  real repositories actually showed (parse-error rates, call ambiguity, cross-language resolution).

## When to use repo2graph

Good fit:

- **An unfamiliar codebase.** The graph turns "read every file" into "start at an entry point and
  follow the edges" — see [docs/why-graph.md](why-graph.md).
- **A large monorepo.** [examples/kubernetes](../examples/kubernetes/) is the concrete case:
  "what calls the pod controller" is a graph-traversal question, not a grep pattern.
- **Architecture discovery and impact analysis.** "What would changing this interface break" is
  exactly what `CALLS`/`IMPORTS`/`INHERITS` edges answer.
- **Cross-module tracing.** [examples/django](../examples/django/)'s middleware-dispatch and
  URL-resolution queries are this in a mature, real framework.
- **Feeding an AI coding agent.** This is the reason `repo2graph rag` and the MCP server exist at
  all — citation-carrying, budget-bounded context beats an ungrounded paste.

Less value:

- **A tiny repository.** Building an index for a 10-file script costs more setup than reading the
  files directly would.
- **Mostly generated or vendored code.** The graph indexes it exactly like hand-written code, with
  no marker distinguishing the two — see [docs/limitations.md](limitations.md).
- **A language repo2graph does not parse deeply.** Unsupported languages still appear as file nodes
  (nothing goes missing from the map), but get no function/class/call-level structure — see
  [the language list](../README.md#languages).
- **Highly dynamic, runtime-decided architecture.** Plugin registries and reflection-heavy dispatch
  are invisible to a static reader — see [docs/limitations.md](limitations.md).

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

- **[docs/benchmarks.md](benchmarks.md)** — real numbers from the five repositories above: clone
  and build time, node/edge counts, methodology, staleness.
- **[docs/PERFORMANCE.md](PERFORMANCE.md)** — controlled, hardware-comparable numbers on a synthetic
  fixture and this project's own self-hosted graph.
- **[benchmarks/](../benchmarks/)** — the machine-readable `results.json` those tables are generated
  from, and the methodology behind it.

## Security and privacy

- **[SECURITY.md](../SECURITY.md)** — the security model: what makes network calls, what is
  opt-in, how secrets are excluded.
- **[docs/PRIVACY.md](PRIVACY.md)** — what leaves your machine, what's cached, what's logged.
- **[docs/SECURITY-AUDIT.md](SECURITY-AUDIT.md)** — the most recent whole-repository security audit.
- **[docs/ENTERPRISE_DEPLOYMENT.md](ENTERPRISE_DEPLOYMENT.md)** — running the CLI, Action or MCP
  server inside an organization.
- **[docs/PRODUCTION_READINESS.md](PRODUCTION_READINESS.md)** — the audit findings, classified.

## Development

- **[.github/CONTRIBUTING.md](../.github/CONTRIBUTING.md)** — how to run tests, add a language,
  submit a change.
- **[AGENTS.md](../AGENTS.md)** — repo-specific rules that override default behavior (encoding,
  text slicing, budget models) — read before editing source under `repo2graph/`.
- **[docs/publishing.md](publishing.md)** — how a release ships to PyPI and the Marketplace.
- **[docs/BACKLOG.md](BACKLOG.md)** — known gaps, deliberately-not-done items, and why.
