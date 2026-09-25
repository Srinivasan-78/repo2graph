# Secure configuration

Copy-paste configurations for the cases that come up, with the reasoning attached. Everything here
is verified against the flags the CLI actually accepts.

Background: [PRIVACY.md](PRIVACY.md) (what happens to your data),
[THREAT_MODEL.md](THREAT_MODEL.md) (what an attacker could try),
[.github/SECURITY.md](../.github/SECURITY.md) (the policy and how to report).

> **There is no config file.** repo2graph reads none — not `pyproject.toml`, not `.r2grc`, not an
> env var for options ([#391](https://github.com/Srinivasan-78/repo2graph/issues/391) tracks
> adding one). Every setting below is a command-line flag, which is why these examples are shell
> snippets and wrapper scripts rather than a config schema.

---

## 1. The safe default is the default

```bash
repo2graph build . -o .r2g
```

No network call. Credential files excluded. Chunk text scanned and inline secrets redacted.
`.gitignore` respected in a git checkout. You have to opt *out* of each of those.

The rest of this page is for cases where the default is not enough.

---

## 2. Excluding sensitive content

### What is already excluded

Verified by enumeration against `repo2graph/secrets.py` and `repo2graph/parse.py`.

**Directories, anywhere in the tree** — `.aws`, `.gnupg`, `.kube`, `.ssh`, `credentials`, `secrets`

**Exact filenames** — `.dockercfg`, `.git-credentials`, `.htpasswd`, `.netrc`, `.npmrc`, `.pgpass`,
`id_dsa`, `id_ecdsa`, `id_ed25519`, `id_rsa`

**Extensions** — `.asc`, `.cer`, `.crt`, `.der`, `.gpg`, `.jks`, `.kdbx`, `.key`, `.keystore`,
`.ovpn`, `.p12`, `.p8`, `.pem`, `.pfx`, `.pkcs12`, `.secret`, `.secrets`

**Keywords in a filename** — `credential`, `password`, `secret`, `token`, `service-account`,
`service_account`, plus the `id_*` key names

**Skipped directories** (28) — `.cache`, `.direnv`, `.eggs`, `.git`, `.gradle`, `.hg`, `.idea`,
`.mypy_cache`, `.next`, `.nuxt`, `.pytest_cache`, `.ruff_cache`, `.svn`, `.terraform`, `.tox`,
`.venv`, `.vscode`, `.yarn`, `__pycache__`, `build`, `coverage`, `dist`, `env`, `node_modules`,
`site-packages`, `target`, `vendor`, `venv`

**Content patterns scanned in chunk text** (8) — AWS access keys, GitHub tokens, Slack tokens,
OpenAI keys, Google keys, PEM private keys, JWTs, and URLs carrying basic-auth credentials — plus
high-entropy values assigned to a secret-looking name.

### Adding your own

`--exclude` takes one or more globs. Useful additions the defaults do not know about:

```bash
repo2graph build . -o .r2g --exclude \
  '**/*.tfvars' '**/*.tfstate' '**/*.tfstate.backup' \
  '**/.env.*' '**/*.local.yml' '**/*.local.yaml' \
  '**/charts/**/values-prod.yaml' \
  '**/fixtures/**/*dump*' '**/testdata/**/*real*' \
  '**/*.sqlite' '**/*.db' '**/*.bak' \
  '**/*.kubeconfig' '**/kubeconfig*' \
  '**/.vault-token' '**/*.jks.b64'
```

Why these, specifically:

- **`*.tfvars` / `*.tfstate`** — Terraform state routinely holds plaintext secrets and matches none
  of the default patterns. `.terraform/` is skipped but state files are usually committed elsewhere.
- **`.env.*`** — `.env` itself is covered; `.env.production` is a different filename and worth
  naming explicitly if your layout uses it.
- **`values-prod.yaml`** — Helm values files are ordinary YAML and look like nothing special.
- **Test fixtures with real data** — the most common way real credentials end up in a repository
  that "has no secrets in it".
- **`*.sqlite` / `*.db`** — binary, so they are skipped as non-text, but naming them makes the
  intent visible to whoever reads the command next.

### Verify, never assume

```bash
repo2graph explain-path config/production.yml
repo2graph explain-path secrets/api.key
repo2graph explain-path .env.production
```

It reports whether the path would be indexed **and which rule decided**, without building anything.
Run it on three or four paths you care about before the first real build. It takes no `-o` — it
never opens an index.

### Tightening the content scanner

```bash
# Default: redact the matched span, keep the file and its line numbers
repo2graph build . -o .r2g --secret-policy redact-match

# Stricter: drop the whole file from the index if any secret matches
repo2graph build . -o .r2g --secret-policy exclude-file

# Report only, index unchanged — useful for a first look at a new repository
repo2graph build . -o .r2g --secret-policy warn-only
```

`--secret-policy off` exists and disables content scanning. Path exclusion stays on.

### The maximal-paranoia build

For a repository you know contains credentials in ordinary source files:

```bash
repo2graph build . -o /secure/scratch/.r2g \
  --secret-policy exclude-file \
  --exclude '**/config/**' '**/deploy/**' '**/*.tfvars' '**/*.env*' \
  --include '**/*.py' '**/*.ts' '**/*.go'
```

`--include` is the strong move: an allowlist of source extensions means anything you did not think
of is out by construction, which is the opposite failure mode from a denylist.

**What this does not do:** content scanning protects what retrieval *returns*; path exclusion keeps
a file out of the index entirely. A secret pasted into `settings.py` is caught only if it matches a
pattern. Path exclusion is the control that does not depend on pattern coverage.

---

## 3. Guaranteeing no network access

The default already makes no network call. To prove it rather than trust it:

```bash
# Linux: no network namespace at all
unshare -rn repo2graph build . -o .r2g

# Docker: the supported hardened invocation
docker run --rm \
  --network=none \
  --read-only \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --user 10000:10000 \
  -v "$PWD":/repo:ro \
  -v repo2graph-index:/repo/.r2g \
  repo2graph build /repo -o /repo/.r2g
```

`--network=none` is the assertion. If a future change introduced a phone-home, this fails loudly
instead of succeeding quietly.

**Two commands will fail under `--network=none`, correctly:** `repo2graph github` (it clones) and
`repo2graph embed` (it downloads a model on first use). Pre-download the model in a separate,
networked step and mount the cache read-only if you need vectors in an offline build.

---

## 4. MCP: stdio

The default, and the right choice for a local agent. No listening socket exists.

```jsonc
{
  "mcpServers": {
    "repo2graph": {
      "command": "uvx",
      "args": ["--from", "repo2graph[mcp]", "repo2graph-mcp", "/path/to/project"]
    }
  }
}
```

Two properties you get without asking:

- **Secrets are excluded from every content tool unconditionally.** There is no flag to disable it.
  A human running the CLI may choose `--include-secrets`; an agent tool returning `.env` is a
  different class of problem, so the MCP path has no equivalent.
- **Every numeric argument is clamped in the handler** — `k`, `hops`, `limit`, `budget_tokens` —
  so a caller cannot widen a bound by asking.

To serve a pre-built index and refuse to build anything:

```jsonc
{
  "mcpServers": {
    "repo2graph": {
      "command": "uvx",
      "args": ["--from", "repo2graph[mcp]", "repo2graph-mcp",
               "/path/to/project", "--no-auto-build"]
    }
  }
}
```

---

## 5. MCP: HTTP

**Read [THREAT_MODEL.md §3.5](THREAT_MODEL.md#35-the-http-mcp-surface) before exposing this.** TLS
enforcement, rate limiting and a few auth hardening items are open issues. The deployment shape
below is not a workaround for that — it is the shape the server is designed for.

### Loopback plus a reverse proxy — the recommended shape

```bash
# The token goes in the environment, never in argv.
export R2G_AUTH_TOKEN="$(cat /run/secrets/r2g-token)"

repo2graph-mcp /srv/repo \
  --http-port 8848 \
  --http-host 127.0.0.1 \
  --http-only \
  --no-auto-build \
  --http-allow-hosts mcp.internal.example.com \
  --audit-log /var/log/repo2graph/audit.jsonl \
  --audit-log-level info
```

**Use `R2G_AUTH_TOKEN`, not `--auth-token`.** Both work and the flag wins when both are set, but a
value in argv is readable by any local user through `ps` or `/proc`. The flag's own help says so.
There is no `--auth-token-file`.

Then terminate TLS, authenticate and rate-limit in front of it — nginx, Caddy, an ingress
controller, whatever you already run. The proxy does the three things the server does not yet do
for itself.

- `--http-host 127.0.0.1` — the server refuses to bind beyond loopback without authentication
  configured; binding loopback explicitly means a misconfiguration cannot widen it.
- `--no-auto-build` — a network caller should never trigger parsing, file writes and git
  invocations. In HTTP mode this is already the default; passing it makes the intent explicit and
  survives a future default change.
- `--audit-log` — one JSON line per tool call: timestamp, tool, sanitized arguments, identity,
  outcome, duration, result size. Values are redacted on *shape* as well as field name, so a
  credential-looking argument never lands in the log.

### With OIDC instead of a static token

```bash
repo2graph-mcp /srv/repo \
  --http-port 8848 --http-host 127.0.0.1 --http-only \
  --auth-oidc-issuer https://issuer.example.com \
  --auth-audience repo2graph-prod \
  --auth-jwks-ttl 300 \
  --http-allow-hosts mcp.internal.example.com \
  --audit-log /var/log/repo2graph/audit.jsonl
```

`--auth-oidc-issuer` is the one flag that makes the *server* an outbound network client — it
fetches the issuer's public JWKS. Nothing of your repository goes with it.

`--http-allow-hosts` is the `Host`/`Origin` allowlist. Set it to the hostname clients actually use;
it is the DNS-rebinding defence and it currently applies to `POST`/`OPTIONS` but not `GET`/`HEAD`
([#372](https://github.com/Srinivasan-78/repo2graph/issues/372)) — another reason for the proxy.

---

## 6. CI: GitHub Action

```yaml
- uses: actions/checkout@v4
  with: { fetch-depth: 0 }

- uses: Srinivasan-78/repo2graph@v2
  with:
    path: .
    git-history: "500"
    exclude: "**/*.tfvars **/*.tfstate **/.env.* **/values-prod.yaml"
    artifact-name: repo-graph
```

Three things that are true of the Action specifically:

- **It never calls an LLM.** `--answer` is deliberately not exposed as an input. There is no
  configuration that makes the Action send your source anywhere.
- **The artifact contains your source code.** `agent/chunks.jsonl` is chunked source text, so the
  artifact is a copy of your repository in another form. Give it the repository's own access
  control, and set retention on the `actions/upload-artifact` step if you keep the artifact
  yourself — the Action exposes `artifact-name`, not a retention input.
- **`commit-branch:` force-pushes a browsable graph to a branch.** On a public repository, that
  publishes the index. Fine for open source; think twice otherwise.

Full inputs and the private-repo token flow: [github-action.md](github-action.md). Workflow-level
hardening: [ACTION_SECURITY.md](ACTION_SECURITY.md).

---

## 7. `rag --answer` — the one path that sends your code

```bash
repo2graph rag "how does auth work" -o .r2g --answer --provider anthropic
```

What happens, in order:

1. The pack is assembled, with dotfile and secret-ish paths **additionally excluded** —
   `pack_context(exclude_secrets=True)` is applied when `--answer` is on, beyond the build-time
   exclusion.
2. The provider and **hostname only** are printed to stderr, before the first byte.
3. The pack — real source text — is POSTed.

`--provider` pins the destination. Without it, the first of `GEMINI_API_KEY` → `OPENAI_API_KEY` →
`ANTHROPIC_API_KEY` → `OLLAMA_HOST` that is set wins, which means adding a key to your environment
for an unrelated tool can silently change where your code goes. **Always pass `--provider` in a
script.**

Entirely local, if you want an answer without a third party:

```bash
OLLAMA_HOST=http://127.0.0.1:11434 \
  repo2graph rag "how does auth work" -o .r2g --answer --provider ollama
```

To forbid the path outright in a shared environment, wrap the entry point:

```bash
#!/usr/bin/env bash
# /usr/local/bin/repo2graph — refuses the one flag that transmits source.
for arg in "$@"; do
  case "$arg" in
    --answer) echo "repo2graph: --answer is disabled by policy" >&2; exit 2 ;;
  esac
done
exec /usr/local/lib/repo2graph/bin/repo2graph "$@"
```

Or run everything under `--network=none` (§3), which forecloses it without a wrapper.

---

## 8. Deleting generated data

```bash
rm -rf .r2g                # the index — the only artifact containing source
rm -f ..r2g.r2glock        # the build lock: beside the index, not inside it
rm -rf /tmp/r2g-*          # clones left by `github --keep-clone`
                           # (%TEMP%\r2g-* on Windows)
```

Plus, only if you ran `repo2graph embed`, the model cache — shared with every other Hugging Face
tool, so check before removing it wholesale:

```bash
rm -rf ~/.cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2
```

Nothing is registered in a system location: no service, no scheduled task, nothing under
`~/.config`, no dotfile in `$HOME`. `pip uninstall repo2graph` plus the above removes every trace.

---

## 9. Checklist for a sensitive repository

- [ ] `repo2graph explain-path` on four or five paths you care about, **before** the first build.
- [ ] `--include` allowlist of source extensions, rather than only a denylist.
- [ ] `--secret-policy exclude-file` if the repository has ever had a credential committed.
- [ ] Output directory outside the repository, or `.r2g/` in `.gitignore`, so the index is not
      committed by accident.
- [ ] First build under `--network=none` — proves the offline claim on your own machine.
- [ ] `grep -c . .r2g/agent/chunks.jsonl` and spot-check a few records. It is your source; look at
      it once.
- [ ] If CI: confirm artifact retention and whether the repository is public.
- [ ] If HTTP MCP: loopback bind, reverse proxy for TLS and rate limiting, audit log on.
