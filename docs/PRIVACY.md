# Privacy

What leaves your machine, what is written where, and what is logged — verified against the code,
not claimed from memory.

- [docs/privacy-audit-2026-09-25.md](privacy-audit-2026-09-25.md) — the audit behind this page:
  every outbound path and every write location, enumerated from the source.
- [docs/THREAT_MODEL.md](THREAT_MODEL.md) — what an attacker could try, and what is out of scope.
- [docs/secure-configuration.md](secure-configuration.md) — hardened configurations to copy.
- [.github/SECURITY.md](../.github/SECURITY.md) — the policy, and how to report a vulnerability.

## Does source code leave the machine?

**By default, no.** `repo2graph build`, `query`, `rag` (without `--answer`), and `repo2graph-mcp`
make no network calls at all — verified by a real socket-level test
(`tests/test_rag_path.py::*opens_no_socket*`, `tests/test_http_transport.py::*opens_no_outbound_socket*`),
not just by reading the code.

**Exactly one path transmits your source code**, and three others open a connection that carries
none of it. Keeping those apart matters: "makes a network call" and "uploads your code" are
different claims, and only the first applies to three of the four.

| Path | Trigger | Does your source leave? | Destination |
|---|---|---|---|
| `repo2graph rag --answer` | The `--answer` flag | **Yes** — the assembled pack, real file content out of `chunks.jsonl` | Gemini / OpenAI / Anthropic / Ollama, by env-var precedence or `--provider`. Provider **name + hostname** printed to stderr *before* the request is sent. |
| `repo2graph github <owner/repo>` | The subcommand | No — it fetches a remote repository, it does not send a local one | GitHub, via a `git clone` subprocess |
| `repo2graph embed`, or `rag --vectors` against an index with no vectors | The subcommand / flag | No — it downloads a model | **huggingface.co**, on first use only: `sentence-transformers/all-MiniLM-L6-v2`, ~90 MB, then cached (see "Where things are written") |
| `repo2graph-mcp --auth-oidc-issuer <url>` | The flag | No — it fetches the issuer's public JWKS | The issuer URL you configured yourself |

Pass none of those and no code path reaches a socket. This is not a runtime toggle checked on every
call — it is an `if args.answer:` / `if args.auth_oidc_issuer:` gate at the point the
network-capable module is even *imported*, so a plain `rag` does not resolve `answer`, let alone a
DNS name.

The full enumeration, including how each path was found:
**[docs/privacy-audit-2026-09-25.md](privacy-audit-2026-09-25.md)**.

## Where things are written

### The index — everything under `-o`

```
.r2g/                                       # the directory you named with -o; default <repo>/.r2g
├── human/   overview.md  graph.html  graph.graphml  CHANGELOG.md
└── agent/   overview.md  manifest.json  chunks.jsonl  nodes.jsonl  edges.jsonl
            graph.cypher  stats.json  index.state.json  parse.cache.json
            index.json*   vectors.npy*  vectors.meta.json*
```

**`agent/chunks.jsonl` contains your source code, in full, as text.** It is the retrieval unit;
everything else in the directory is structure *about* the code. Give the directory the access
control you give the repository.

| Artifact | Contents | Lifetime |
|---|---|---|
| `agent/chunks.jsonl` | **Source text**, one record per chunk, with its graph neighbourhood in the header | Until the next `build` |
| `agent/parse.cache.json` | Per-file sha256 + parsed symbol/import summaries | Until the next `build` |
| `agent/index.state.json` | Per-file sha256 | Until the next `build` |
| `agent/vectors.npy` + `.meta.json` | Embedding vectors of chunk text — not the text — plus the model id | Until the next `embed` |
| In-memory `ResultCache` | Rendered MCP tool results, keyed on canonical JSON of the arguments | Process memory only; cleared on index rebuild or exit; **never written to disk** |

Every writer in `export.py` resolves `outdir` once and produces `outdir / <fixed relative name>`,
never a caller-supplied or repository-derived absolute path, so nothing in the export layer can
escape the directory you named.

### Three things written outside `-o`

The export layer stays inside `-o`; the package as a whole writes in three other places. This
matters for deletion — `rm -rf .r2g` does not get all of it.

| Location | Written by | Contents | Cleaned up |
|---|---|---|---|
| `<outdir>.parent/.<outdir-name>.r2glock` — for `-o .r2g`, that is **`<repo>/..r2g.r2glock`** | The build lock (`lock.py:79`) | Holder pid and host. No repository content. | Released and unlinked on exit; a lock older than 3600 s is treated as stale and recovered |
| System temp dir, `r2g-*` | `repo2graph github` (`fetch.py:325`) | **A full clone of the remote repository** | `shutil.rmtree` in a `finally` — **except** when `--keep-clone <path>` is passed, which is that flag's purpose |
| `HF_HOME` / `SENTENCE_TRANSFORMERS_HOME`, else `~/.cache/huggingface/` | sentence-transformers, via `repo2graph embed` | The downloaded embedding model. No repository content. | Never — it is a dependency's cache and persists across runs by design |

The lock lands beside the output directory rather than inside it because a lock guarding the
*creation* of a directory cannot live within it.

None of these leave the machine on their own. If you copy `.r2g/` anywhere — a CI artifact upload,
as `action.yml` supports — you are moving a full copy of your chunked source code. Treat it
accordingly.

## Deleting everything repo2graph wrote

```bash
# 1. The index — the only thing containing your source code
rm -rf .r2g

# 2. The build lock, which sits beside the index, not in it
rm -f ..r2g.r2glock            # for -o .r2g; adjust for a different -o

# 3. Any clone left behind by `repo2graph github --keep-clone`
#    (without --keep-clone these are already removed on exit)
rm -rf /tmp/r2g-*              # macOS/Linux; %TEMP%\r2g-* on Windows

# 4. The embedding model cache — only if you ran `repo2graph embed`.
#    Shared with every other tool that uses Hugging Face models, so check
#    before deleting it wholesale.
rm -rf ~/.cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2
```

Nothing is registered in a system location: no service, no scheduled task, no entry in
`~/.config`, no dotfile in `$HOME`. Uninstalling the package (`pip uninstall repo2graph`) plus the
four commands above removes every trace.

To confirm nothing is left, `repo2graph doctor` reports whether an index is present and readable at
a given path.

## Telemetry

**None, and there is nothing to turn off.**

No phone-home, no usage analytics, no crash reporter, no update check, no install-time ping, no
first-run banner. There is not even a config file one could hide an analytics key in — the package
reads no config file at all.

This is verified two ways: the package was grepped for every common analytics SDK and for the words
`telemetry`, `analytics`, `phone home`, `usage stat` and `opt out` (no hits in live code), and the
socket-level tests above are the standing guarantee — telemetry is a network call, and the default
path opens no socket.

### If analytics are ever proposed

The bar, recorded here so it is not relitigated later:

1. **Opt-in by an explicit flag or environment variable that defaults to off.** Not opt-out, not
   "on unless detected in CI", not on for a first run.
2. **Disclosed before the first byte**, on stderr, the way `answer._disclose()` already announces
   provider and hostname for the LLM call.
3. **Covered by a test that the default path still opens no socket** — the existing ones, extended
   rather than weakened.
4. **Documented here, in this section, with what is collected field by field.**

Anything shipping on by default would contradict a guarantee this project has already made in
writing, and the tests in §1 would fail, which is the intent.

## Keeping sensitive files out

Four mechanisms; the first two are on by default and compose with the others.

| Layer | Default | What it does |
|---|---|---|
| **Path exclusion** | **On** | `.env*`, private keys, certificates, `.ssh`, `.aws`, `.gnupg`, `.kube`, `credentials/`, `secrets/` and more are never read. `--include-secrets` opts out. |
| **Content scanning** | **On** (`--secret-policy redact-match`) | Chunk text is scanned for vendor key formats, JWTs, DB URLs with inline credentials, PEM private keys and high-entropy assignments; matches are redacted line-preservingly. |
| **`.gitignore`** | **On in a git checkout** | Discovery runs through `git ls-files`, so ignored files are never candidates. Unavailable in a plain-folder build — there, `DEFAULT_SKIP_DIRS` and your own `--exclude` are the controls. |
| **`--include` / `--exclude`** | Off | Explicit globs, for whatever the defaults do not know about. |

The exact pattern tables, and copy-paste configurations for common cases, are in
**[docs/secure-configuration.md](secure-configuration.md)**.

**Verify rather than assume.** `repo2graph explain-path <path>` says whether a given path would be
indexed *and which rule decided*, without building anything:

```bash
repo2graph explain-path config/production.yml
repo2graph explain-path .env
```

**The one limit worth knowing:** content scanning protects what retrieval *returns*; path exclusion
is what keeps a file out of the index at all. A secret pasted into an ordinary source file —
`config/settings.py`, say — is indexed, and is protected only by the content scanner's pattern
coverage. Path exclusion is the strong control; the scanner is the backstop. For a repository with
secrets in ordinary source files, use `--exclude` and confirm with `explain-path`.

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

The complete list, from every `os.environ` / `getenv` reference in the package.

| Variable | Read by | When | Purpose |
|---|---|---|---|
| `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OLLAMA_HOST` | `answer.py` | Only under `--answer` | Provider selection and authentication for the opt-in LLM call. `GOOGLE_API_KEY` is a fallback for the Gemini provider. |
| The same five | `doctor.py` | `repo2graph doctor` | **Presence only.** It reports "Configured" — never the value, never a length, never trailing characters, deliberately: a tail leaks key entropy and a length fingerprints the key. |
| `GH_TOKEN`, `GITHUB_TOKEN` | `fetch.py` | Only under `repo2graph github` | Authenticating a clone of a private repository. Never placed in a git argv word. |
| `R2G_AUTH_TOKEN` | `mcp.py` | Only in HTTP mode | The bearer token the server requires. **Preferred over `--auth-token`**, whose value is visible to other local users through `ps`/procfs. |
| `SystemRoot`, `ProgramFiles` | `integrity.py` | Windows only | Locating system directories for a path check. Read, never recorded. |

Nothing else is read. `os.environ` is never iterated or dumped anywhere in the package — there is
no `--debug` mode that prints the environment, and no crash path that includes it. `sanitize_value`
in `audit.py` would redact a credential-shaped value if one somehow reached a log field, but the
stronger guarantee is that nothing collects the environment to begin with.

`PYTHONIOENCODING` is honoured by Python itself and exercised by the Windows encoding tests;
repo2graph's own code does not read it.

## What this document does not claim

Same caveat as `.github/SECURITY.md`: this is a description of what the code does, verifiable by reading
it, not a claim of formal audit, certification, or compliance (SOC 2, ISO, or otherwise). If your
organization requires one of those, it requires organizational and technical controls beyond this
repository's scope — this document is an input to that process, not a substitute for it.
