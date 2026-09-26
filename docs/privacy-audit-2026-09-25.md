# Privacy and data-handling audit — 2026-09-25

A pass over every path by which repo2graph can emit a byte to the network or write one to disk,
against the claims in [docs/PRIVACY.md](PRIVACY.md) and [.github/SECURITY.md](../.github/SECURITY.md).

Method: enumerate network-capable imports across the package, trace each to its trigger, enumerate
every write path, and check each documented claim against the code rather than against the previous
version of the document.

**Headline: the central claim holds. Two documented claims were incomplete, and both have been
corrected in this pass.** No telemetry exists, and the default path is genuinely offline.

---

## 1. Does source code leave the machine?

**Not by default, and the default is verified by execution rather than by inspection.**

`repo2graph build`, `query`, `rag` (without `--answer`), `map`, `stats`, `explain`, `doctor` and
`repo2graph-mcp` over stdio open no socket. Two tests assert this at the socket layer, which is the
only way to assert it that a future refactor cannot quietly break:

- `tests/test_rag_path.py:279::test_the_default_rag_path_opens_no_socket`
- `tests/test_http_transport.py:587::test_the_server_opens_no_outbound_socket_without_oidc`

### Every outbound path in the package

Four modules can originate a connection. Found by grepping every `import` of `urllib`,
`http.client`, `socket`, `requests`, `httpx`, `ssl`, `smtplib`, `ftplib` and `asyncio` across
`repo2graph/`, then tracing each to the flag that reaches it.

| # | Module | Trigger | What leaves | Destination |
|---|---|---|---|---|
| 1 | `answer.py` | `rag --answer` | **Repository source text** — the assembled pack out of `chunks.jsonl` | Gemini / OpenAI / Anthropic / Ollama, by env-var precedence or `--provider` |
| 2 | `auth.py` | `repo2graph-mcp --auth-oidc-issuer <url>` | Nothing of the repository — an HTTPS GET for the issuer's public JWKS | The issuer URL you configured |
| 3 | `fetch.py` | `repo2graph github <owner/repo>` | Nothing of a local repository — a `git clone` subprocess | GitHub |
| 4 | `embed.py` | `repo2graph embed`, or `rag --vectors` on an index without vectors | Nothing of the repository — a model download | **huggingface.co**, on first use |

**Only path 1 transmits your source.** Paths 2–4 are outbound connections that carry none of it,
which is a distinction worth keeping precise: "makes a network call" and "uploads your code" are
different claims and the second is much narrower.

Each is gated at import, not at call time. `answer` is imported inside `if args.answer:`; a plain
`rag` does not resolve the module, let alone a DNS name.

### Finding P-1 — paths 3 and 4 were undocumented — **fixed**

`docs/PRIVACY.md` listed two exceptions (`--answer`, `--auth-oidc-issuer`) and described them as
exhaustive. It did not mention that:

- **`repo2graph embed` downloads an embedding model from Hugging Face on first use.**
  `embed.py:302-307` constructs `SentenceTransformer(model_id)` with no `cache_folder`, so
  sentence-transformers fetches `sentence-transformers/all-MiniLM-L6-v2` (~90 MB) over the network
  and caches it under the Hugging Face default, **outside the output directory**.
- **`repo2graph github` clones over the network.** Obvious from the command's purpose, but it was
  absent from the table of network paths, which read as complete.

Neither leaks repository content, so the top-line guarantee was not wrong — the enumeration was.
Both are now in PRIVACY.md's network table.

---

## 2. What is written to disk, and where

The `-o` output directory (`.r2g` by default) holds the index:

```
.r2g/
├── human/   overview.md  graph.html  graph.graphml  CHANGELOG.md
└── agent/   overview.md  manifest.json  chunks.jsonl  nodes.jsonl  edges.jsonl
            graph.cypher  stats.json  index.state.json  parse.cache.json
            index.json*  vectors.npy*  vectors.meta.json*
```

**`agent/chunks.jsonl` contains your source code**, in full, as text. It is the retrieval unit;
everything else is structure. Treat the directory with the access control you give the repository.

### Finding P-2 — three write locations sit outside `-o` — **fixed**

PRIVACY.md said:

> Every disk artifact is written **only inside the output directory you named** — `<repo>/.r2g` by
> default, never outside the tree you pointed the tool at.

That is true of `export.py`'s writers and is the right guarantee to make about them. It is not true
of the package as a whole:

| Location | Written by | Contains | Cleaned up? |
|---|---|---|---|
| `<outdir>.parent/.<outdir>.r2glock` — e.g. `<repo>/..r2g.r2glock` for `-o .r2g` | `lock.py:79` | A build lock: holder pid and host. No repository content. | Released and unlinked on exit; a stale lock is recovered after `stale_threshold` (3600 s). |
| System temp, `r2g-*` | `fetch.py:325` | **A full clone of the remote repository.** | `shutil.rmtree` in a `finally` — **unless `--keep-clone <path>` is passed**, which is the point of that flag. |
| Hugging Face cache (`HF_HOME` / `SENTENCE_TRANSFORMERS_HOME`, else `~/.cache/huggingface/`) | sentence-transformers, via `embed.py` | The embedding model. No repository content. | Never — it is a dependency's cache, persisting across runs by design. |

The lock file is the surprising one: for `-o .r2g` it lands in the **repository root**, as
`..r2g.r2glock`, not inside `.r2g/`. That is deliberate — a lock guarding the creation of a
directory cannot live inside it — but it means `rm -rf .r2g` does not remove everything repo2graph
wrote. PRIVACY.md's "Deleting everything" section now lists all four locations.

---

## 3. Telemetry

**None, and there is nothing to disable.**

Grepped `repo2graph/` for `telemetry`, `analytics`, `posthog`, `sentry`, `mixpanel`, `segment`,
`amplitude`, `google-analytics`, `phone home`, `usage stat` and `opt out`. Three hits, all
unrelated: two are the word "segment" in `auth.py` (a JWT segment) and `parse.py` (a name segment),
one is "opt out" in a comment about document comparison.

There is no crash reporter, no update check, no install-time ping, no first-run banner, and no
config file in which an analytics key could hide — the package reads no config file at all
(issue #391). The socket-level tests in §1 are the standing guarantee: telemetry would be a network
call, and in the default path no socket is opened.

**If analytics are ever proposed**, the bar recorded in PRIVACY.md is: opt-in by an explicit flag
or environment variable that defaults to off; disclosed on stderr before the first byte, as
`answer._disclose()` already does for the LLM call; and covered by a test that the default path
still opens no socket. Anything that ships on by default would contradict a guarantee this project
has already made in writing.

---

## 4. Excluding sensitive content

Four independent mechanisms. They compose, and the first two are on by default.

| Layer | Default | What it does |
|---|---|---|
| **Path exclusion** | **On** | `.env*`, private keys, certificates, `.ssh`, `.aws`, `.gnupg`, `.kube`, `credentials/`, `secrets/` and more are never read. `--include-secrets` opts out. |
| **Content scanning** | **On** (`--secret-policy redact-match`) | Chunk text is scanned for 8 vendor key formats, JWTs, DB URLs with inline credentials, PEM private keys and high-entropy assignments; matches are redacted line-preservingly. |
| **`.gitignore`** | **On in a git checkout** | Discovery uses `git ls-files` (`parse.py:309`), so ignored files are never candidates. Not available in a plain-folder build. |
| **`--include` / `--exclude`** | Off | Explicit globs, for anything the defaults do not know about. |

Exact tables, verified by enumeration:

- **`SECRET_DIR_NAMES`** (6): `.aws`, `.gnupg`, `.kube`, `.ssh`, `credentials`, `secrets`
- **`SECRET_EXACT_NAMES`** (10): `.dockercfg`, `.git-credentials`, `.htpasswd`, `.netrc`, `.npmrc`,
  `.pgpass`, `id_dsa`, `id_ecdsa`, `id_ed25519`, `id_rsa`
- **`SECRET_EXTS`** (17): `.asc`, `.cer`, `.crt`, `.der`, `.gpg`, `.jks`, `.kdbx`, `.key`,
  `.keystore`, `.ovpn`, `.p12`, `.p8`, `.pem`, `.pfx`, `.pkcs12`, `.secret`, `.secrets`
- **`SECRET_KEYWORDS`** (10), matched within a filename: `credential`, `password`, `secret`,
  `token`, `service-account`, `service_account`, `id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519`
- **`DEFAULT_SKIP_DIRS`** (28): `.cache`, `.direnv`, `.eggs`, `.git`, `.gradle`, `.hg`, `.idea`,
  `.mypy_cache`, `.next`, `.nuxt`, `.pytest_cache`, `.ruff_cache`, `.svn`, `.terraform`, `.tox`,
  `.venv`, `.vscode`, `.yarn`, `__pycache__`, `build`, `coverage`, `dist`, `env`, `node_modules`,
  `site-packages`, `target`, `vendor`, `venv`
- **`CONTENT_SECRET_PATTERNS`** (8): `aws_access_key`, `github_token`, `slack_token`, `openai_key`,
  `google_key`, `private_key`, `jwt`, `basic_auth_url`

`repo2graph explain-path <path>` reports whether a specific path would be indexed **and which rule
decided**, without building anything. That is the verification step, and it belongs in any
onboarding checklist for a sensitive repository.

---

## 3a. Environment variables

PRIVACY.md listed four provider variables plus `PYTHONIOENCODING`, and concluded "No other
environment variable is read by `repo2graph/`."

### Finding P-5 — four more are read — **fixed**

Enumerated from every `os.environ` / `getenv` reference in the package:

| Variable | Read by | Was documented? |
|---|---|---|
| `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OLLAMA_HOST` | `answer.py` | Yes |
| `GOOGLE_API_KEY` | `answer.py` — a fallback for the Gemini provider | **No** |
| The five above | `doctor.py` — **presence only**, never the value or its length | **No** |
| `GH_TOKEN`, `GITHUB_TOKEN` | `fetch.py`, for a private-repo clone | **No** |
| `R2G_AUTH_TOKEN` | `mcp.py`, the HTTP bearer token | **No** |
| `SystemRoot`, `ProgramFiles` | `integrity.py`, Windows path checks | **No** |

None of the omissions is a leak — the guarantee that `os.environ` is never iterated or dumped still
holds, and `doctor`'s provider check is careful in a way worth noting (it reports "Configured" and
nothing else, because a tail leaks key entropy and a length fingerprints the key). But a reader
hardening a deployment needed to know that `R2G_AUTH_TOKEN` is the intended way to pass the HTTP
bearer token, precisely because `--auth-token` puts it in argv where any local user can read it
from `ps`. The table is now complete.

### Finding P-3 — AGENTS.md's open follow-up SH-5 is already closed

AGENTS.md records, under "Discovery indexes dot-directories, git or not":

> Consequence: on non-git builds, tool caches like `.ruff_cache/`, `.eggs/`, `.cache/` are picked up
> unless their names are added to `DEFAULT_SKIP_DIRS` (open follow-up SH-5).

All three **are** in `DEFAULT_SKIP_DIRS` today. The follow-up is done and the note is stale.
Not changed here — AGENTS.md is the source-editing contract and correcting it is a separate change
— but it should be, because a reader currently believes a gap exists that does not.

### Finding P-4 — the known residual gap

Content scanning runs on **chunk text**, which means it protects what retrieval returns. Path
exclusion is what keeps a file out of the index entirely. A secret in a file that is neither
secret-named nor gitignored — an API key pasted into `config/settings.py`, say — is indexed, and
protected only by the content scanner's pattern coverage. That coverage is 8 vendor formats plus
entropy heuristics, and [issue #368](https://github.com/Srinivasan-78/repo2graph/issues/368) tracks
extending it to more vendor prefixes.

This is a real limit, stated rather than smoothed over: **path exclusion is the strong control,
content scanning is the backstop.** For a repository with secrets in ordinary source files, use
`--exclude` and verify with `explain-path`.

---

## 5. Claims checked

| Claim | Verdict |
|---|---|
| Default path makes no network call | **Holds** — asserted at the socket layer by two tests |
| No telemetry of any kind | **Holds** — no code exists to make such a call |
| `rag --answer` is the only path that transmits source | **Holds** |
| `--answer` discloses provider and hostname before sending | **Holds** — `answer._disclose()`, stderr, host only |
| No credential in a URL | **Holds** — the Gemini key moved to the `x-goog-api-key` header |
| `os.environ` is never dumped | **Holds** — no iteration of `os.environ` anywhere in the package |
| The audit log never contains chunk text or raw credentials | **Holds** — redaction is on shape as well as field name |
| MCP content tools exclude secrets unconditionally | **Holds** — all three handlers pass `exclude_secrets=True`, with no flag to disable |
| Nothing is written to `$HOME` or `~/.config` by repo2graph itself | **Holds** — no `Path.home()`, `expanduser`, `XDG_*` or `APPDATA` reference anywhere in the package |
| "Two explicit, opt-in exceptions" to network use | **Incomplete** — four outbound paths exist; two carry no repository content (**P-1, fixed**) |
| "Every disk artifact is written only inside the output directory" | **Incomplete** — true of `export.py`, not of the package (**P-2, fixed**) |
| "No other environment variable is read" | **Incomplete** — four more are (**P-5, fixed**) |

### Summary

| | |
|---|---|
| Claims checked | 12 |
| Holding as written | 9 |
| Incomplete, now corrected | 3 (P-1, P-2, P-5) |
| Wrong | 0 |
| Stale notes found elsewhere | 1 (P-3, in AGENTS.md — not changed here) |
| Known residual gaps, already tracked | 1 (P-4, [#368](https://github.com/Srinivasan-78/repo2graph/issues/368)) |

No claim was found to be false. All three corrections were **under**-statements of scope — paths
and variables that existed but were not listed — rather than guarantees that failed.

---

## 6. What this is not

A description of what the code does, verifiable by reading it. Not a formal audit, a penetration
test, or a certification (SOC 2, ISO 27001, or otherwise), and not a claim that the secret scanner
catches every secret — §4 states plainly where it does not. If your organization needs one of
those, this document is an input to that process, not a substitute for it.

Related: [PRIVACY.md](PRIVACY.md) (what happens), [THREAT_MODEL.md](THREAT_MODEL.md) (what an
attacker could try), [secure-configuration.md](secure-configuration.md) (copy-paste hardened
configs), [SECURITY-AUDIT.md](SECURITY-AUDIT.md) (the previous whole-repository security pass).
