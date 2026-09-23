# GitHub Action Security Guide & Guardrails

This document details security guardrails, threat model considerations, and permission hardening for the `repo2graph` GitHub Action (`Srinivasan-78/repo2graph`).

## Key Principles

1. **Principle of Least Privilege**: Grant only `contents: read` unless pushing to a graph branch.
2. **Never push from untrusted triggers**: Do not enable `commit-branch` with write permissions on `pull_request` or `pull_request_target`.
3. **Use Dedicated Orphan Branches**: Keep graph data on an orphan branch (e.g. `graph`), never on protected branches (`main`, `develop`).
4. **Guard Against Force Pushes**: Use `commit-force: false` if branch history preservation or strict branch protection is enforced.

---

## Permission Matrix

| Workflow Objective | Required Permissions | Recommended Trigger | Outputs |
| :--- | :--- | :--- | :--- |
| **Inspect PRs / CI Verification** | `contents: read` | `pull_request` | GitHub Actions Summary, `artifact-name: repo-graph` |
| **Publish Graph on Merge** | `contents: write` | `push` (to main/release branch) | `commit-branch: graph` |
| **Scheduled Background Indexing** | `contents: write` | `schedule` (cron) / `workflow_dispatch` | `commit-branch: graph` |
| **External/Private Repo Indexing** | `contents: read` | `workflow_dispatch` | `artifact-name`, uses fine-grained `token` |

---

## Guardrails Built Into the Action

### 1. Fork PR Push Refusal
The action automatically inspects the execution environment:
```bash
if [ "${IS_FORK:-}" = "true" ]; then
  echo "::error::Refusing to push graph to branch from a fork pull request. Use artifact-name instead or run on push events."
  exit 1
fi
```
If an untrusted fork submits a pull request and the workflow attempts to execute `commit-branch`, the action **fails immediately** to prevent arbitrary modification of the target branch.

### 2. Event Context Warning
When run under `pull_request` or `pull_request_target` with `commit-branch` specified, the action emits an explicit workflow warning:
```bash
Pushing graph to branch during pull_request events may expose tokens or overwrite branches unintentionally.
```

### 3. Safe Credential Handling (Anti-Token Leakage)
Credentials are never passed via `git` command-line arguments or remote URLs (which are readable via `ps` or `/proc` on shared runners). Instead, authentication is configured via local git config headers:
```bash
git config --local http.https://github.com/.extraheader "AUTHORIZATION: basic ..."
```

### 4. Configurable Force-Pushes (`commit-force`)
By default, `commit-branch` creates an orphan branch and forces the push (`commit-force: true`) so that old history does not accumulate bloat.
To enforce non-destructive fast-forward pushes, specify:
```yaml
with:
  commit-branch: graph
  commit-force: "false"
```

---

## Workflow Examples

### Recommended: Two-Tier Architecture (PR Inspection + Post-Merge Publishing)

#### Step 1: Safe Pull Request Check (`.github/workflows/graph-pr.yml`)
```yaml
name: Code Graph Check
on:
  pull_request:
    branches: [main]

permissions:
  contents: read  # Read-only!

jobs:
  graph:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: Srinivasan-78/repo2graph@v2
        with:
          artifact-name: pr-graph  # Uploads artifact for review
          # commit-branch is NOT set here
```

#### Step 2: Post-Merge Graph Publisher (`.github/workflows/graph-publish.yml`)
```yaml
name: Publish Repository Graph
on:
  push:
    branches: [main]

permissions:
  contents: write  # Safe here because only merged commits run on main

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: Srinivasan-78/repo2graph@v2
        with:
          git-history: "500"
          commit-branch: graph
          commit-force: "true"
```

---

## Anti-Patterns and High-Risk Configurations

### ❌ DANGEROUS: `pull_request_target` with `contents: write`
```yaml
# HIGH VULNERABILITY: Do NOT do this
on: pull_request_target
permissions:
  contents: write
jobs:
  index:
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}
      - uses: Srinivasan-78/repo2graph@v2
        with:
          commit-branch: graph
```
**Risk**: In this configuration, an attacker can submit a PR containing arbitrary code changes or scripts that execute with write access to the base repository.

### ❌ DANGEROUS: Pushing to Protected Branches
```yaml
with:
  commit-branch: main  # DO NOT target protected or production branches!
```
**Risk**: Overwrites application source code or requires disabling branch protection. Always use a dedicated orphan branch such as `graph`.
