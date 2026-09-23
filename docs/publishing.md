# Releasing and listing repo2graph

Publishing is automated — `.github/workflows/publish.yml` ships to PyPI and the
MCP Registry from one GitHub Release, with no long-lived credential anywhere.
But three things have to be set up by hand **once**, because they need you to be
logged in as you. Do those first, then every release is a tag push.

---

## One-time setup

### 1. Register the PyPI Trusted Publisher

PyPI has to be told which workflow is allowed to publish `repo2graph`. Until this
exists, the `pypi` job fails with `invalid-publisher`.

Go to <https://pypi.org/manage/account/publishing/> and add a **pending**
publisher (pending = the project does not exist on PyPI yet, which is the case
here):

| Field | Value |
|---|---|
| PyPI Project Name | `repo2graph` |
| Owner | `Srinivasan-78` |
| Repository name | `repo2graph` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

The environment name matters: the workflow declares `environment: name: pypi`,
and PyPI checks it. A mismatch here is the single most common failure.

> The name `repo2graph` was unclaimed on PyPI as of this writing. If someone
> takes it first, change `[project] name` in `pyproject.toml` *and* the
> `identifier` in `server.json`, and re-register.

### 2. Create the `pypi` environment on GitHub

Settings → Environments → **New environment** → `pypi`. No secrets go in it —
it exists so the publish is gated and shows up in the deployment log. Add a
required reviewer if you want a human to approve each release.

### 3. Nothing to do for the MCP Registry

`mcp-publisher login github-oidc` authenticates as this repository using the
workflow's OIDC token, so there is no account to create and no secret to store.
The namespace `io.github.Srinivasan-78/*` is yours automatically because it
matches the repo owner.

### 4. GitHub Marketplace listing is manual, and does not survive automation

`publish.yml`'s `release` job creates every GitHub Release via `gh release
create`. Neither the GitHub REST API nor the `gh` CLI exposes the "Publish
this Action to the GitHub Marketplace" flag — that checkbox exists only on
GitHub's own **Draft a new release** web page. An automated release can never
create or renew a Marketplace listing, no matter how `publish.yml` is
written; this is a GitHub platform limitation, not a bug in this repo's
pipeline.

Practical consequence: if `repo2graph` needs to be (re-)listed on the
Marketplace, do it by hand, once, against whatever tag is current:

1. Go to **Releases** → find the release for the current tag (e.g. the
   latest `vX.Y.Z` `publish.yml` created) → **Edit release** (pencil icon).
2. Check **"Publish this Action to the GitHub Marketplace"**.
3. Pick a primary category (and a second one if relevant) — required the
   first time a listing is created.
4. Save. This re-publishes the *existing* release; it does not create a new
   tag or trigger `publish.yml`, so it's safe to do at any time independent
   of a version bump.

There is no way to script step 2 onward; it requires being logged in as the
repo owner (or an org member with the right role) in a browser.

---

## Cutting a release

Releasing is fully automated via **`.github/workflows/publish.yml`**, which runs in three sequential stages:
$$\text{prepare-release} \longrightarrow \text{publish} \longrightarrow \text{release}$$

### Option 1: Zero-input release via GitHub Actions (Recommended)
1. In GitHub, go to **Actions** → **Publish** → click **Run workflow** (no typing or inputs needed).
2. The workflow automatically:
   - Scans commits and `CHANGELOG.md` since the previous release tag to auto-determine `major`, `minor`, or `patch`.
   - Rewrites **every** version surface listed in `scripts/version_surfaces.py`:
     `pyproject.toml`, `server.json` (twice), `repo2graph/__init__.py`, `uv.lock`, and the
     documented ones — the `@vN` tag in every `uses:` example, the exact-tag and
     `repo2graph==X.Y.Z` pin examples, and the prose naming the release line the floating
     tag tracks. That table is also what `scripts/check_version.py` verifies and what
     `publish.yml` asks for its `--files` list, so a surface cannot be known to the bump
     and not the check.
   - Promotes `[Unreleased]` in `CHANGELOG.md` to `[<version>] — <date>`.
   - Commits and pushes the version bump to `main` and creates Git tag `v<version>`.
   - Runs tests, builds wheels/sdist, and publishes to PyPI via Trusted Publishing.
   - Waits for PyPI CDN and publishes `server.json` to the MCP Registry.
   - Creates the GitHub Release with changelog notes and advances the floating `vN` tag.

### Option 2: Local bump + Tag push
If you prefer bumping locally before pushing:
```bash
python scripts/bump_version.py 2.0.1   # or patch / minor / major
python scripts/check_version.py             # every surface agrees (CI runs this too)
git commit -am "chore(release): bump version to 2.0.1" -- $(python scripts/version_surfaces.py --files)
git tag v2.0.1
git push origin main --follow-tags
```

`bump_version.py` needs `uv` on PATH and now fails without it, rather than warning: `publish.yml`'s pypi job installs with `uv export --locked`, so a lock left out of sync aborts the release *after* the tag is cut.
Pushing tag `v2.0.1` automatically triggers `publish.yml` to publish and release.

Verify:

```bash
pip index versions repo2graph
curl -s "https://registry.modelcontextprotocol.io/v0/servers?search=repo2graph" | jq .
uvx --from "repo2graph[mcp]" repo2graph-mcp /some/project    # the thing users will run
```

### The ownership marker

The registry proves you own the PyPI package by finding this exact string in the
package description, which setuptools takes from `README.md`:

```
<!-- mcp-name: io.github.Srinivasan-78/repo2graph -->
```

It is near the top of `README.md` and `publish.yml` refuses to run without it.
Do not remove it, and do not let it end up glued to trailing punctuation — the
token must be followed by whitespace, a newline, or the comment close.

---

## Listing it

**Do these after the first successful publish, not before.** Both lists point
people at an install command; submitting while `pip install repo2graph` still
404s wastes the reviewer's time and yours.

### MCP Registry

Automatic — `publish.yml` does it. Nothing to submit.

### punkpeye/awesome-mcp-servers

Accepts pull requests. Fork, add the line below to the **Developer Tools**
section, and open a PR. Their `CONTRIBUTING.md` fast-tracks agent-authored PRs
if the title ends with `🤖🤖🤖`.

```markdown
- [Srinivasan-78/repo2graph](https://github.com/Srinivasan-78/repo2graph) [![Srinivasan-78/repo2graph MCP server](https://glama.ai/mcp/servers/Srinivasan-78/repo2graph/badges/score.svg)](https://glama.ai/mcp/servers/Srinivasan-78/repo2graph) 🐍 🏠 🍎 🪟 🐧 - Ask a codebase questions and get cited code back. Builds a tree-sitter graph of the repo — files, functions, calls, imports, inheritance — then answers with BM25 plus graph expansion, so every hit arrives with its callers and callees attached and a `[cite: path:start-end]` header. Three tools: `repo_map`, `repo_search`, `repo_neighbours` (the graph hop grep cannot do). Indexes the repo itself on the first call, so there is no setup step. Output is hard-capped at 12k tokens and paths that look like credential stores are never returned. `uvx --from "repo2graph[mcp]" repo2graph-mcp /path/to/project`
```

Legend used: 🐍 Python codebase, 🏠 local service, 🍎🪟🐧 all three platforms.
Not 🎖️ — that means an official vendor implementation.

### wong2/awesome-mcp-servers

**Does not accept pull requests.** Its README says so at the top. Submit through
the form instead:

<https://mcpservers.org/submit>

Use the same description and the `uvx` command above.

### Worth considering too

- **Glama** (<https://glama.ai/mcp/servers>) — indexes from the registry and the
  awesome lists; the badge in the entry above is theirs.
- **mcp.so**, **Smithery** (<https://smithery.ai>) — both take direct
  submissions and are where a lot of client UIs pull their directory from.
