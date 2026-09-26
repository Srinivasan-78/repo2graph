# PR / Diff Impact Analysis Workflow

> **Flagship Architectural Risk & Impact Assessment Powered by Code Graphs**

Modern pull requests frequently introduce subtle architectural regressions: modifying a public method signature breaks a downstream caller three directories away; an unexported helper changes behaviour without any test coverage; or an unrelated script is accidentally modified. Standard `git diff` shows *what lines changed*, but cannot answer *what software architecture was impacted*.

`repo2graph impact` computes the exact structural blast radius of any pull request or working tree diff against a base branch using the AST code graph.

---

## Key Capabilities & Core Questions

| Question | Code Graph Technique | Output Evidence |
| :--- | :--- | :--- |
| **"What symbols changed?"** | Intersects diff hunks with AST symbol spans (`start_line`..`end_line`). | Symbol name, path, line numbers, change type (`added`, `modified`, `deleted`), signature delta. |
| **"Which public APIs may be affected?"** | Language-aware visibility heuristics (Python naming, Go capitalization, TS exports). | Flagged public symbols with signature diff status. |
| **"Which callers, modules, and tests are impacted?"** | Reverse call graph traversal (`CALLS in`) up to `N` hops and module import edges (`IMPORTS in`). | Impacted callers with depth, path, line citations, confidence rating, and tests exercising them. |
| **"Which dependency paths cross the changed area?"** | Connects upstream callers through the changed symbol to downstream callees/imports. | End-to-end dependency path chains. |
| **"Which changes look disconnected or suspicious?"** | Graph isolation detection, untested public API rules, high blast radius rules, ambiguous call rules. | Structured rule findings (`R2G-IMP-001` through `R2G-IMP-004`). |
| **"Summarize architectural impact with evidence?"** | Synthesizes risk level, blast radius score, test recommendations, and grounded citations. | Formatted Markdown, PR Comment, JSON, or SARIF v2.1.0. |

---

## Static Analysis Guardrails & Uncertainty Model

> [!IMPORTANT]
> **Definite Breakage vs Static Exposure:**
> Static analysis identifies structural reachability and potential call exposure. It does **not** prove runtime breakage.
> Dynamic dispatch (Python `getattr`, reflection, dependency injection containers, monkey-patching), runtime conditionals (`if False:`), and runtime environment flags may alter actual execution paths.
>
> `repo2graph impact` follows strict guardrails:
> 1. **No unsubstantiated breakage claims:** Reports state "potential exposure" or "impacted caller", citing concrete line numbers.
> 2. **Confidence-weighted call chains:** Direct AST call sites have confidence `1.0`. Ambiguous symbol resolutions (e.g. methods with multiple candidate implementations) carry fractional confidence and trigger `R2G-IMP-004`.
> 3. **Actionable test recommendations:** Direct test callers are surfaced so CI or developers can run targeted regression tests first.

---

## CLI Usage

### Basic Command

```bash
# Analyze current working branch against main
repo2graph impact -i .repo2graph-index --base main

# Compare two branches
repo2graph impact -i .repo2graph-index --base origin/main --head feature-branch

# Feed a unified diff directly from stdin or file
git diff main...HEAD | repo2graph impact -i .repo2graph-index --diff -
repo2graph impact -i .repo2graph-index --diff path/to/patch.diff
```

### Formats & Options

| Flag | Description | Default |
| :--- | :--- | :--- |
| `-o, -i, --out, --index <dir>` | Path to the repo2graph index directory (must contain `chunks.jsonl`, `nodes.jsonl`, `edges.jsonl`). Built from the repo if absent. | `.r2g` |
| `--base <ref>` | Base git ref to compare against. | `main` |
| `--head <ref>` | Head git ref or commit to compare. | `HEAD` |
| `--diff <file>` | Path to unified diff file, or `-` for stdin. | None (runs `git diff`) |
| `--format <type>` | Output format: `markdown`, `json`, `sarif`, `pr-comment`. | `markdown` |
| `--json` | Convenience alias for `--format json`. | False |
| `--sarif` | Convenience alias for `--format sarif`. | False |
| `--max-depth <n>` | Traversal depth for reverse callers (`CALLS in`). | `2` |
| `--min-confidence <f>` | Minimum edge confidence filter (`0.0` - `1.0`). | `None` |
| `--write <file>` | Write output to file instead of stdout. | None |
| `--no-auto-build` | Fail instead of building an index when `--index` holds none. | False (builds) |

### Examples

#### 1. Generate Markdown PR Summary for Reviewers
```bash
repo2graph impact -i .index --base main --format markdown
```

#### 2. Machine-Readable JSON for CI Automation
```bash
repo2graph impact -i .index --base main --json --write report.json
```

#### 3. SARIF v2.1.0 for GitHub Code Scanning
```bash
repo2graph impact -i .index --base main --sarif --write impact.sarif
```

#### 4. Collapsible Markdown Comment for GitHub PR Comments
```bash
repo2graph impact -i .index --base main --format pr-comment --write pr-comment.md
```

---

## Machine-Readable Schemas

### JSON Schema Structure

```json
{
  "base_ref": "main",
  "head_ref": "feature/auth",
  "risk_level": "MEDIUM",
  "blast_radius_score": 18,
  "metrics": {
    "files_changed_count": 2,
    "symbols_changed_count": 3,
    "public_apis_affected_count": 1,
    "direct_callers_count": 4,
    "transitive_callers_count": 2,
    "impacted_modules_count": 2,
    "impacted_tests_count": 1,
    "untested_public_apis_count": 0,
    "suspicious_findings_count": 1
  },
  "files_changed": [
    {
      "path": "src/auth/service.py",
      "old_path": null,
      "status": "modified",
      "added_lines": [45, 46, 47],
      "deleted_lines_count": 1
    }
  ],
  "symbols_changed": [
    {
      "id": "sym:src/auth/service.py::AuthService.refresh_token",
      "name": "refresh_token",
      "qualname": "AuthService.refresh_token",
      "kind": "method",
      "path": "src/auth/service.py",
      "start_line": 45,
      "end_line": 68,
      "signature": "def refresh_token(self, token: str) -> TokenPair:",
      "is_public": true,
      "signature_changed": true,
      "changed_lines_count": 3,
      "change_type": "modified"
    }
  ],
  "public_apis_affected": [...],
  "impacted_callers": [
    {
      "id": "sym:src/api/routes.py::login_route",
      "name": "login_route",
      "qualname": "login_route",
      "kind": "function",
      "path": "src/api/routes.py",
      "line": 84,
      "target_symbol_id": "sym:src/auth/service.py::AuthService.refresh_token",
      "target_symbol_name": "AuthService.refresh_token",
      "depth": 1,
      "confidence": 1.0,
      "evidence": "src/api/routes.py:84",
      "is_test": false
    }
  ],
  "impacted_modules": ["src/api/routes.py"],
  "impacted_tests": [
    {
      "test_file": "tests/test_auth.py",
      "test_node_id": "sym:tests/test_auth.py::test_refresh_token",
      "test_name": "test_refresh_token",
      "line": 15,
      "target_symbol_id": "sym:src/auth/service.py::AuthService.refresh_token",
      "target_symbol_name": "AuthService.refresh_token",
      "confidence": 1.0,
      "evidence": "tests/test_auth.py:15"
    }
  ],
  "suspicious_findings": [
    {
      "rule_id": "R2G-IMP-001",
      "category": "orphan_change",
      "severity": "warning",
      "path": "scripts/deploy.py",
      "line": 1,
      "title": "Disconnected modified file: scripts/deploy.py",
      "description": "File was modified in this diff, but has no graph relationships connecting it to other changes."
    }
  ],
  "guardrails": {
    "analysis_type": "static_ast_code_graph",
    "uncertainty_notice": "Static analysis identifies structural reachability and potential call exposure...",
    "confidence_tiers": {"high": "...", "plausible": "...", "import_only": "..."},
    "index_coverage": {
      "changed_files": 2,
      "files_absent_from_index": []
    }
  }
}
```

`guardrails.stale_index_notice` is present only when `files_absent_from_index` is
non-empty — see *Index coverage is part of the report* below.

### Static Impact Rules

| Rule ID | Name | Severity | Description |
| :--- | :--- | :--- | :--- |
| **`R2G-IMP-001`** | `DisconnectedOrphanChange` | `warning` | A modified file shares no graph connections (calls, imports, defs) with any other changed file in the diff. |
| **`R2G-IMP-002`** | `UntestedPublicApiChange` | `warning` | A public function or class method changed, but no test node in the graph reaches it. |
| **`R2G-IMP-003`** | `HighBlastRadiusModification`| `warning` | A modified symbol has \(\ge 8\) direct callers or spans \(\ge 3\) separate modules. |
| **`R2G-IMP-004`** | `AmbiguousCallSite` | `note` | Downstream caller resolved with fractional confidence (< 0.70) due to dynamic or polymorphic naming. |

Each rule is scoped so that a finding is a claim the graph can actually support:

- **`R2G-IMP-001`** only judges files the graph can relate at all — a file with at
  least one parsed symbol or one import edge. A workflow YAML, a lockfile or a
  Markdown page has neither in any index, so "no graph relationships connect it to
  the other changes" would be true of every such file in every multi-file diff.
  For the same reason the rule needs **two** relatable files before it fires: a
  lone source file has nothing in the change it could have been connected to.
- **`R2G-IMP-003`** only judges *modified* symbols outside test paths. An added
  symbol has no pre-existing dependents — its callers arrived with it in the same
  change — so counting them measures how well new code is wired in, not what the
  edit puts at risk. A shared test helper with many callers is how a suite is
  meant to look.

### Index coverage is part of the report

`analyze_diff_impact` intersects diff hunks, whose line numbers are **new-file**
coordinates, with symbol spans read out of the index. The index must therefore be
built on the **head commit being analyzed**. An index built on the base ref cannot
contain a file the diff adds, and holds base-side line numbers for every file the
diff modifies — so added files read as unreachable orphans and modified symbols
resolve against the wrong lines, with no error raised and plausible-looking
numbers in the report.

Every report states its own coverage under `guardrails.index_coverage`
(`changed_files`, `files_absent_from_index`), and adds
`guardrails.stale_index_notice` — rendered into the Markdown and PR-comment output
— whenever a changed file is missing from the index.

---

## Model Context Protocol (MCP) Integration

Coding agents interacting with repository knowledge can invoke the `repo_impact` tool via the Model Context Protocol:

### Tool Schema: `repo_impact`
```json
{
  "name": "repo_impact",
  "description": "Analyze PR or git diff impact against a base branch using the code graph. Detects changed symbols, affected public APIs, impacted callers, test coverage, and architectural blast radius with grounded citations. Read-only, deterministic, zero side effects.",
  "parameters": {
    "type": "object",
    "properties": {
      "base": {"type": "string", "description": "Base ref or branch to compare against (default 'main')."},
      "head": {"type": "string", "description": "Head ref or branch to compare (default 'HEAD')."},
      "diff": {"type": "string", "description": "Optional raw unified diff text. If provided, overrides git diff."},
      "max_depth": {"type": "integer", "description": "Caller traversal hops around changed symbols (default 2, max 5)."},
      "format": {"type": "string", "enum": ["markdown", "json", "pr-comment"], "description": "Report format."}
    }
  }
}
```

### Security & Bounding Invariants
- **Unconditional Secret Filtering:** All MCP requests enforce `exclude_secrets=True`. Files such as `.env`, `.pem`, and credential paths are stripped before impact processing.
- **Clamped Numeric Arguments:** `max_depth` is strictly clamped within `[1, MCP_MAX_HOPS]`. Arbitrary client values cannot induce unbounded recursion.

---

## GitHub Actions CI Workflow

`.github/workflows/pr-impact.yml` in this repository is the maintained reference
implementation — read it rather than a copy pasted here, which is how the recipe
below came to disagree with the tool it drives. The three properties any adaptation
needs:

**1. Build the index on the head commit, not the base ref.** See *Index coverage is
part of the report* above. On a `pull_request` event `actions/checkout` leaves `HEAD`
at the merge commit — the tree that will actually land — so indexing the checkout as
it stands is both correct and one build instead of two:

```yaml
      - name: "Checkout PR merge commit"
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 0 # so origin/<base> exists for the three-dot diff
          persist-credentials: false

      - name: "Build graph index on the analyzed head tree"
        run: repo2graph build . -o .repo2graph-head --formats jsonl,overview

      - name: "Run repo2graph impact analysis"
        env:
          BASE_REF: ${{ github.base_ref }}
        run: |
          mkdir -p impact-reports
          run_impact() {
            repo2graph impact -i .repo2graph-head               --base "origin/$BASE_REF" --head HEAD               --format "$1" --write "impact-reports/$2"
          }
          run_impact markdown   impact-report.md
          run_impact json       impact-report.json
          run_impact sarif      impact-report.sarif
          run_impact pr-comment pr-comment.md
```

**2. Update one comment instead of posting another.** `format_pr_comment` leads with
`repo2graph.impact.PR_COMMENT_MARKER` (`<!-- repo2graph-impact-comment -->`) for
exactly this. A plain `gh pr comment` appends a fresh comment on every push:

```yaml
        run: |
          existing=$(gh api --paginate "repos/$GH_REPO/issues/$PR_NUMBER/comments"             --jq '[.[] | select(.body | startswith("<!-- repo2graph-impact-comment -->")) | .id] | last // empty')
          jq -Rs '{body: .}' < impact-reports/pr-comment.md > comment-body.json
          if [ -n "$existing" ]; then
            gh api -X PATCH "repos/$GH_REPO/issues/comments/$existing" --input comment-body.json
          else
            gh api -X POST "repos/$GH_REPO/issues/$PR_NUMBER/comments" --input comment-body.json
          fi
```

**3. Do not hand a privileged credential to a job that runs PR code.** This job
installs the package from the PR head, so a GitHub App key or any write-scoped
identity must be minted only when the pull request comes from a branch of the
repository itself:

```yaml
    env:
      SAME_REPO: ${{ github.event.pull_request.head.repo.full_name == github.repository }}
    # ...
      - name: "Generate app token"
        id: app-token
        if: ${{ env.SAME_REPO == 'true' && env.HAS_APP_ID != '' && env.HAS_PRIVATE_KEY != '' }}
```

A fork pull request still gets the whole analysis in the job summary and the
uploaded artifact. The comment and the SARIF upload are what GitHub already
withholds there: `GITHUB_TOKEN` is read-only for fork pull requests regardless of
the workflow's `permissions:` block, which is why the SARIF upload step carries
`continue-on-error: true`.

---

## Verification & Self-Check Checklist

- [x] Strict text slicing using `split("\n")` (zero `.splitlines()` usage).
- [x] Non-ASCII safe subprocess handling (`-c core.quotepath=false`, bytes decoding with `utf8` + `surrogateescape`, timeout bounds).
- [x] AST symbol matching against diff line intervals.
- [x] Language-aware public API detection for Python, Go, TypeScript/JavaScript, Java/Kotlin, Rust.
- [x] Multi-hop reverse caller expansion with confidence propagation.
- [x] Suspicious change detection (isolated diffs, untested public APIs, high blast radius, ambiguous calls).
- [x] Machine-readable outputs: JSON, Markdown, PR-comment, and SARIF v2.1.0.
- [x] Full MCP tool exposure (`repo_impact`) with secret filtering and depth clamping.
- [x] GitHub Action CI recipe with security-hardened action pins and step summaries.
- [x] Index coverage reported rather than assumed, so a base-built index cannot
      produce a confident wrong answer.
- [x] Findings scoped to claims the graph can support: no orphan finding for a file
      the parser never read, no blast-radius finding for a symbol nothing depended on.
- [x] One PR comment, updated in place, posted as the repository's PR assistant.
- [x] No write-scoped credential in a job that executes pull-request code.

