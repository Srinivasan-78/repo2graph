# Security

This is the canonical security policy for repo2graph — what the tool does and doesn't send over
the network, how the repository itself is protected, and how to report a vulnerability. See
[TECHNICAL.md](../TECHNICAL.md) for how the code works, and [README.md](../README.md) for how to
use it.

## What never leaves your machine

`repo2graph build`, `repo2graph query`, `repo2graph rag`, and the `repo2graph-mcp` server make
**no network calls**. They read your local files with tree-sitter, write the graph to `.r2g`, and
answer questions from that local index. Nothing about your source is sent anywhere by default.

The one exception is explicit and opt-in: `repo2graph rag --answer` sends the assembled context
pack (real file content) to whichever LLM provider it resolves — Gemini, OpenAI, Anthropic, or a
local Ollama host — to generate a natural-language answer. It:

- only runs when you pass `--answer`; a plain `rag` or `query` call makes no DNS lookup and opens
  no socket;
- prints the provider and **hostname only** to stderr before sending anything, so you see the
  destination before the request goes out;
- never puts a credential in a URL — API keys go in request headers;
- can be pinned to a specific provider with `--provider` instead of letting key-presence pick one
  for you.

If you never pass `--answer`, this code path is not reachable.

## How credential files are excluded

`repo2graph rag --answer` also enables `pack_context(exclude_secrets=True)`, which drops dotfiles
and secret-shaped paths (`.env`, credential stores, etc.) from the pack before it's sent anywhere.

The **MCP server goes further and makes this unconditional**. Of its five tools, the three that
can return repository content — `repo_map`, `repo_search`, `repo_neighbours` — exclude secrets
always, with no flag to turn it off. (The remaining two, `repo_cache_stats` and
`repo_build_status`, report on the server itself and never read a chunk.) A human running the CLI
directly chose to see `.env` in local output; an agent calling the MCP server unattended does not
get that choice, so the server doesn't offer it. See
[docs/mcp.md](../docs/mcp.md#three-promises-the-server-keeps-that-the-cli-leaves-to-you) for the
other two guarantees the server holds itself to (hard-capped output, hard-capped work per call) —
relevant if you're running it where an untrusted caller can pick the arguments.

## Why this is safe for enterprise use

- **Local-first by construction, not by configuration.** The graph, the chunks, and the search
  index all live in a `.r2g` directory next to your code. There's no account, no upload step, and
  no telemetry to opt out of, because none exists.
- **The MCP server never calls an LLM itself.** It serves graph data — file/function/call
  structure and cited source snippets — over stdio (or `--http-port`, if you enable it). Whatever
  client you point at it (Claude Code, Claude Desktop, Cursor) makes its own model calls under its
  own data policy; repo2graph doesn't add a second one.
- **Zero-dependency by design where it matters.** Degree counting, graph layout, and GraphML/Cypher
  export are pure Python — no NetworkX, no vendored graph library with its own supply chain. The
  only required third-party dependencies are `tree-sitter` and `tree-sitter-language-pack`, both
  parsers; `sentence-transformers`/`numpy` (the `rag` extra) and the `mcp` SDK (the `mcp` extra) are
  optional and never load unless you ask for them.
- **Auto-build only writes where you pointed it.** The MCP server's first-call index build writes
  exclusively into `<repo_path>/.r2g`, never outside the tree you gave it.

None of this is a claim of a formal audit or certification — it's a description of what the code
does, verifiable by reading it (it's small, pure-Python, and has no hidden network layer to trust
blindly).

## How the repository itself is protected

This section is about the supply chain — what stops a bad commit from reaching the `main` branch
or a released package, independent of anything the tool does at runtime:

- **Force-pushes and branch deletion are blocked on `main`.** The `default-branch-protection`
  ruleset carries `non_fast_forward` and `deletion`, so history on `main` is append-only and the
  branch cannot be removed.
- **Every change reaches `main` through a pull request**, with review threads required to be
  resolved and stale approvals dismissed on push.
- **Twelve status checks gate every merge**, all of which must pass before the PR is mergeable:
  `tests` across the full matrix (`ubuntu-latest`, `windows-latest`, `macos-latest` × Python 3.10,
  3.11, 3.12), plus `packaging` (the no-extra refusal and a real stdio MCP round trip),
  `action` (the composite Action run against this repository) and `windows-cp1252-pipe` (the
  non-UTF-8 console regression leg). `reuse` — SPDX/licence-header compliance via
  [REUSE.toml](../REUSE.toml) — runs on every push in `provenance.yml` but is not one of the
  required contexts.

  Commit signing is *not* currently enforced by the ruleset. It was, until 2026-09-21; treat an
  unsigned commit on `main` as expected rather than as evidence of a bypass, and verify the live
  rule set rather than this list if you are relying on it:
  `gh api repos/Srinivasan-78/repo2graph/rules/branches/main --jq '[.[].type]'`.
- **GitHub Actions are pinned to full commit SHAs, not tags**, across every workflow in
  `.github/workflows/`, so a compromised or re-tagged upstream action can't silently change what CI
  runs. The reverse is not true for consumers of *this* repository's own Action: `@v2` is a moving
  convenience pointer (`publish.yml` force-pushes it to the latest release on every tag), not an
  integrity pin — enterprise consumers who want a SHA-level guarantee should pin
  `Srinivasan-78/repo2graph@<commit-sha>` rather than `@v2`, the same way this repo's own workflows
  pin their dependencies.
- **Dependabot** watches `pyproject.toml`/`uv.lock` and the pinned Action SHAs for known
  vulnerabilities; `uv.lock` is committed, so every install — local, CI, or a hosted MCP build — is
  reproducible from the exact dependency graph that was reviewed.
- **A dependency-review workflow** runs on every PR that touches dependencies, blocking new
  packages with a disallowed license or a known advisory before merge.

## Further reading

- [docs/SECURITY-AUDIT.md](../docs/SECURITY-AUDIT.md) — architecture, threat model, trust
  boundaries, and a prioritized findings list with `file:line` evidence for every claim.
- [docs/PRIVACY.md](../docs/PRIVACY.md) — exactly what leaves the machine, what's cached, and what's
  logged.
- [docs/ENTERPRISE_DEPLOYMENT.md](../docs/ENTERPRISE_DEPLOYMENT.md) — container hardening, network
  scoping, and package-pinning guidance for a shared or regulated deployment.
- [docs/PRODUCTION_READINESS.md](../docs/PRODUCTION_READINESS.md) — an area-by-area readiness rating
  with evidence and remaining risk for each.

## Reporting a Vulnerability

Please report security issues privately to [@Srinivasan-78](https://github.com/Srinivasan-78) via a GitHub Security Advisory or email. Do not open a public issue for undisclosed vulnerabilities.

We aim to acknowledge reports within 72 hours. Only the latest release on PyPI is supported;
please confirm the issue reproduces there before reporting.
