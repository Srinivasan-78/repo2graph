# GitHub Action

repo2graph is published on the GitHub Marketplace, so it is one step in any
workflow.

```yaml
- uses: actions/checkout@v4
  with: { fetch-depth: 0 }   # full history, so CO_CHANGE edges are meaningful
- uses: Srinivasan-78/repo2graph@v1
  with:
    path: .              # or: repo: some-org/other-repo
    git-history: "500"
    artifact-name: repo-graph
```

`@v1` follows every 1.x release. Pin an exact version (`@v1.6.0`) if you would
rather upgrade by hand.

The action never calls an LLM: `--answer` is deliberately not exposed. It packs
the context and leaves the answering to whatever reads the pack afterwards.

It also writes a structured summary into the job summary page, so the shape of
the map shows up in the run without downloading anything. The summary is built
by a small stdlib-only script (`.github/scripts/summary.py`) straight from the
build's own artifacts — it never truncates a large repo the way printing the
first N lines of a file would. It has:

- an at-a-glance table: files indexed, functions, classes, total edges,
  languages found, chunks, and the short commit SHA the build ran at (omitted
  if the checkout isn't a git repo);
- a "Top 5 hub files" table, ranked by in-degree across every edge type;
- a "CO_CHANGE hotspots" table of the most frequently co-changed file pairs
  (omitted when `git-history` is `0` or no pair crosses the co-change
  threshold);
- a "Graph delta" section condensed from `human/CHANGELOG.md` when a previous
  build's CHANGELOG is present next to this one — just the new/removed node
  and edge counts and the new-hotspot lines, not the full item lists.

Any piece it can't compute (a missing field, a non-git checkout, no
CHANGELOG.md) degrades to "N/A" or an omitted row/section rather than failing
the step.

## Inputs

| Input | Default | What it does |
| --- | --- | --- |
| `repo` | `""` | Map a different project: `owner/repo` or a GitHub URL. Leave blank to map the checked-out one. |
| `path` | `.` | Folder in the workspace to map, used when `repo` is blank. |
| `ref` | `""` | Branch or tag to map, used with `repo`. Blank means the default branch. |
| `out` | `.r2g` | Where the map is written. |
| `formats` | `jsonl,graphml,cypher,overview,html` | Which files to write. Drop the ones you do not need to save time. |
| `git-history` | `0` | Commits to read for `CO_CHANGE` arrows. `0` skips it. Needs `fetch-depth: 0`. |
| `include` | `""` | Space-separated globs to keep, e.g. `"src/**"`. |
| `exclude` | `""` | Space-separated globs to skip, e.g. `"**/test/** vendor/**"`. |
| `query` | `""` | Also pack a cited GraphRAG context for this question. Blank skips it. |
| `query-k` | `8` | Pieces the text search starts with. |
| `query-hops` | `1` | Steps to walk along the arrows. |
| `query-budget` | `24000` | Character budget for the whole pack, map and cite headers included. |
| `query-budget-tokens` | `""` | Token budget for the whole pack. Set it and it replaces `query-budget` as the unit. Blank keeps the character budget. |
| `query-min-conf` | `1.0` | Drop `CALLS` arrows below this confidence. |
| `query-format` | `markdown` | `markdown` or `json`. |
| `query-out` | `""` | File to write the pack to. Blank means `<out>/agent/pack.md` (or `pack.json`). |
| `embed` | `false` | Also embed the chunks for meaning-based search. Installs the `rag` extra and downloads a model, so it is off by default. When `true` the pack is packed with `--vectors`. |
| `embed-model` | `""` | sentence-transformers model for `embed`. Blank uses the built-in default. Both the embed step and the pack step get this model, so the two always agree. |
| `artifact-name` | `repo-graph` | Upload the map under this name. Blank uploads nothing. |
| `commit-branch` | `""` | Push the map to this orphan branch. Blank pushes nothing. |
| `commit-force` | `true` | Whether to force-push when pushing to `commit-branch`. Set to `false` for standard fast-forward push. |
| `token` | `""` | Token that can read `repo` when the target is private. |
| `version` | `""` | pip spec to install repo2graph from, e.g. `repo2graph==1.6.0`. Blank installs the action checkout you pinned with `uses:`, which is what every run did before. |
| `include-secrets` | `false` | Set to `true` to index secret/credential files. By default, sensitive files (.env, keys, certs) are excluded. |
| `secret-policy` | `redact-match` | Policy for inline content secrets: `redact-match`, `exclude-file`, `warn-only`, `off`. |
| `incremental` | `false` | Set to `true` to enable incremental graph builds using the parse cache. |
| `parse-policy` | `best-effort` | Policy for AST parse errors: `best-effort`, `warn`, `strict`. |
| `max-call-candidates` | `5` | Maximum call edge candidates to retain per ambiguous call site. |

### Pinning the package instead of the checkout

By default the action installs itself — the source that came with the `uses:`
ref — so the action and the package can never disagree. Set `version` to install
from PyPI instead, which is the artifact `publish.yml` builds under Trusted
Publishing with attested provenance:

```yaml
- uses: Srinivasan-78/repo2graph@v1
  with:
    version: repo2graph==1.6.0
```

Any pip spec works (`repo2graph>=1.4,<2`, a `git+https://…@<ref>` URL, a local
wheel path). It is passed to `pip install` verbatim, so a bad spec fails the step
loudly rather than falling back to the checkout.

This input used to be accepted and ignored: the install always gated on a
`pyproject.toml` that a composite action always has, so the editable install of
the checkout won every time. If you were already setting `version`, you were
getting the checkout — you now get what you asked for.

## Outputs

| Output | What it holds |
| --- | --- |
| `out` | The output folder: `human/` (`overview.md`, `graph.html`, …) and `agent/` (`chunks.jsonl`, …). |
| `nodes` | How many dots the map has. |
| `edges` | How many arrows. |
| `chunks` | How many code pieces were cut. |
| `pack-file` | Path to the GraphRAG pack written for `query`. Empty when `query` is blank. |
| `pack-chars` | How long that pack is, in characters. `0` when `query` is blank. |

## Permissions & Least Privilege

Follow the principle of least privilege:
- **Default usage (read-only):** When building and uploading artifacts, the action requires only read access to repository contents:
  ```yaml
  permissions:
    contents: read
  ```
- **Branch publishing:** Write permissions are **only** needed if you configure `commit-branch`:
  ```yaml
  permissions:
    contents: write
  ```

## Security Guardrails & Best Practices

### 1. Never use `commit-branch` or `contents: write` on untrusted Pull Requests

Granting `contents: write` to workflows triggered by `pull_request` (or worse, `pull_request_target`) exposes your repository to unauthorized branch updates or token extraction:
- The action automatically **refuses to push** if it detects execution inside a pull request originating from a fork repository (`github.event.pull_request.head.repo.fork == true`).
- For pull requests, always prefer `artifact-name: repo-graph` to inspect artifacts via GitHub Actions summary and artifact downloads without write permissions.

#### Insecure Example (DO NOT USE)
```yaml
# INSECURE: Grants write token on untrusted pull requests and force-pushes
name: Unsafe PR Graph
on: pull_request_target  # DANGEROUS with write permissions!
permissions:
  contents: write
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: Srinivasan-78/repo2graph@v1
        with:
          commit-branch: graph  # DANGEROUS: untrusted code can trigger branch push
```

#### Secure Recommended Example
```yaml
# SECURE: Read-only on PRs; uploads artifacts for inspection
name: Pull Request Graph
on:
  pull_request:
    branches: [main]
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: Srinivasan-78/repo2graph@v1
        with:
          artifact-name: pr-graph
          # commit-branch omitted! No write access required
```

### 2. Use a dedicated orphan branch, never a protected branch

Never point `commit-branch` at a primary or protected branch (e.g. `main`, `develop`, `master`):
- Point it at a dedicated orphan branch (e.g. `graph`, `repo-graph`, or `docs/graph`).
- Configure branch protection rules on your repository to prevent accidental pushes to protected branches.

### 3. Safe Force-Pushes with `commit-force`

By default, `commit-branch` creates an orphan branch with a single root commit and uses `--force` (`commit-force: true`) so the graph branch remains clean and minimal.
If your compliance or security policy disallows force pushes, set `commit-force: false` to require standard fast-forward pushes:
```yaml
- uses: Srinivasan-78/repo2graph@v1
  with:
    commit-branch: graph
    commit-force: "false"
```

### 4. Handling Private Repositories

When indexing private remote repositories via `repo`:
- Never commit personal access tokens in workflow files or CLI arguments.
- Pass repository secrets via the `token` input:
  ```yaml
  - uses: Srinivasan-78/repo2graph@v1
    with:
      repo: my-org/private-repo
      token: ${{ secrets.READ_ONLY_REPO_PAT }}
  ```
- Use fine-grained Personal Access Tokens (PATs) scoped to **read-only contents** on the specific target repository.

For the detailed threat model, permission matrix, and full security guide, see [docs/ACTION_SECURITY.md](ACTION_SECURITY.md).

## Keep a fresh map next to your own code

`.github/workflows/self-index.yml` is the copy this project runs on itself, on
every push to `main` and once a week:

```yaml
name: Index this repository
on:
  push:
    branches: [main]
  schedule:
    - cron: "0 4 * * 1"
  workflow_dispatch:

permissions:
  contents: write

jobs:
  index:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: Srinivasan-78/repo2graph@v1
        with:
          path: .
          git-history: "500"
          artifact-name: repo-graph
          commit-branch: graph   # drop this line to only publish an artifact
```

With `commit-branch: graph`, an AI pipeline can always grab an up-to-date copy of
the code pieces with one request:

```bash
curl -sL https://raw.githubusercontent.com/Srinivasan-78/repo2graph/graph/agent/chunks.jsonl -o chunks.jsonl
```

## Map any project from the Actions tab

`.github/workflows/index-repo.yml` is a button you press. Type a project name, get
a map back. It downloads the project, builds the map, prints the summary into the
job page, and uploads `graph-<owner>__<repo>` as a file you can download. Set
`publish_release: true` and it also attaches a zip to a GitHub Release.

The download includes `graph.html`, so opening that one file gives you the picture
with nothing installed.

Inputs: `repo`, `ref`, `git_history`, `formats`, `exclude`, `publish_release`. For
a private project, add a `TARGET_REPO_TOKEN` secret that can read it. Otherwise
the job's own token is used.

All from the terminal:

```bash
gh workflow run index-repo.yml -f repo=psf/requests
gh run watch
gh run download --name graph-psf__requests --dir ./graph
```
