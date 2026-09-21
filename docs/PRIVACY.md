<!--
  @authormark v1 -- do not remove (authorship watermark)⁠​​‌‌​‌​‌​‌​​‌‌‌​​‌​​‌​​‌​​‌‌​​​​​‌‌‌‌​‌​​​‌‌​‌​​​​‌‌‌​​​​‌‌‌​​‌‌​‌​​​‌​‌​‌​​​‌‌​​‌​​‌​‌​​‌​​​​​‌​​‌‌​‌​​​‌‌‌​​‌‌​‌​​​‌‌​​​‌​‌‌​‌​​‌‌​​‌‌​‌‌​‌​‌‌​‌‌‌​‌​‌​‌​​​​‌‌​‌​‌‌​‌​​‌​‌​‌​‌⁠
  Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
  Author: https://github.com/Srinivasan-78
  SPDX-License-Identifier: MIT
  Fingerprint: AMK1.5NI0z48sEFJA4sF-3kuCZU
-->
# Privacy

What leaves your machine, what's cached, and what's logged — verified against the code, not
claimed from memory. See [SECURITY.md](../SECURITY.md) for the network/credential-exclusion
guarantees this document assumes, and [docs/SECURITY-AUDIT.md](SECURITY-AUDIT.md) for how those
guarantees were checked.

## Does source code leave the machine?

**By default, no.** `repo2graph build`, `query`, `rag` (without `--answer`), and `repo2graph-mcp`
make no network calls at all — verified by a real socket-level test
(`tests/test_rag_path.py::*opens_no_socket*`, `tests/test_http_transport.py::*opens_no_outbound_socket*`),
not just by reading the code.

Two explicit, opt-in exceptions:

| Path | What leaves | Trigger | Where the destination is disclosed |
|---|---|---|---|
| `repo2graph rag --answer` | The assembled context pack — real file content from `chunks.jsonl` | The `--answer` flag, nothing else | Provider name + hostname printed to stderr *before* the request is sent |
| `repo2graph-mcp --auth-oidc-issuer <url>` | Nothing of your repository — only an HTTPS request to fetch the issuer's JWKS (public key material) | The `--auth-oidc-issuer` flag, nothing else | The issuer URL you configured yourself |

If you never pass `--answer` or `--auth-oidc-issuer`, neither code path is reachable — this isn't a
runtime toggle checked on every call, it's an `if args.answer:`/`if args.auth_oidc_issuer:` gate at
the point the network-capable module is even imported.

## What gets cached, and where

| Artifact | Location | Contents | Lifetime |
|---|---|---|---|
| `agent/parse.cache.json` | `<outdir>/agent/` (wherever you pointed `-o`) | Per-file sha256 + parsed symbol/import summaries | Until the next `build` overwrites it |
| `agent/vectors.npy` + `.meta.json` | Same | Dense embedding vectors of chunk text (not the text itself), model id | Until the next `embed` |
| `agent/index.state.json` | Same | Per-file sha256 | Until the next `build` |
| In-memory `ResultCache` | Process memory only, MCP server | Rendered tool results keyed on canonical JSON of arguments | Cleared on any index rebuild or process exit; never written to disk |

Every disk artifact is written **only inside the output directory you named** — `<repo>/.r2g` by
default, never outside the tree you pointed the tool at. This is enforced by construction: every
writer in `export.py` takes the resolved `outdir` and every path it produces is
`outdir / <fixed relative name>`, never a caller-supplied or repository-derived absolute path.

None of these caches leave the machine on their own. If you copy `.r2g/` somewhere (e.g. a CI
artifact upload, as `action.yml` supports), you are choosing to move a full copy of your chunked
source code — treat that artifact with the same access control you'd give the repository itself.

## What telemetry exists

None. There is no phone-home, no usage analytics, no crash reporter, no update check. Verified by
the same "no network calls in default mode" tests referenced above — a telemetry call would be a
network call, and none exists to make.

## What logs contain, and their retention

**Audit log** (`--audit-log <path>`, opt-in; stderr copy always on unless `--audit-log-level none`):
one JSON line per MCP tool call — timestamp, tool name, sanitized arguments, identity (OIDC `sub`
claim or "anonymous"), outcome, duration, result size in tokens. Every value is redacted on *shape*
(credential-looking strings, secret-path patterns) as well as on field name, before it is written —
see `repo2graph/audit.py` and `tests/test_audit.py`'s redaction tests, which assert the opposite
direction too (ordinary arguments are *not* mangled). Retention is entirely up to you: repo2graph
appends to the file you name and never rotates or deletes it.

**What the audit log never contains:** full source-code chunk text, environment variables, or raw
credentials — only a length + a non-reversible fingerprint for anything redacted, sufficient to
correlate two sightings of the same secret without the log ever holding it.

**Stdout/stderr** (no `--audit-log`): the CLI prints what you asked for (a query's answer, a
`stats` report); it does not print environment variables or credentials at any verbosity level, and
there is no `--debug` mode that dumps `os.environ` or similar.

## Environment variables actually read

| Variable | Read by | Purpose |
|---|---|---|
| `GEMINI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `OLLAMA_HOST` | `answer.py`, only under `--answer` | Provider selection and authentication for the opt-in LLM call |
| `PYTHONIOENCODING` | Python itself, referenced in Windows encoding tests | Not read by repo2graph's own code; documented here because CI exercises it |

No other environment variable is read by `repo2graph/`. None is echoed into a tool result, a log
line, or an exception message — `sanitize_value` in `audit.py` would redact a credential-shaped
value if one somehow appeared in a log field, but the stronger guarantee is that `os.environ` is
simply never iterated or dumped anywhere in the codebase (grepped; no hits).

## What this document does not claim

Same caveat as `SECURITY.md`: this is a description of what the code does, verifiable by reading
it, not a claim of formal audit, certification, or compliance (SOC 2, ISO, or otherwise). If your
organization requires one of those, it requires organizational and technical controls beyond this
repository's scope — this document is an input to that process, not a substitute for it.
