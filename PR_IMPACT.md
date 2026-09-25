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
| `-i, --index <dir>` | Path to built repo2graph index directory (must contain `chunks.jsonl`, `nodes.jsonl`, `edges.jsonl`). | Required |
| `--base <ref>` | Base git ref to compare against. | `main` |
| `--head <ref>` | Head git ref or commit to compare. | `HEAD` |
| `--diff <file>` | Path to unified diff file, or `-` for stdin. | None (runs `git diff`) |
| `--format <type>` | Output format: `markdown`, `json`, `sarif`, `pr-comment`. | `markdown` |
| `--json` | Convenience alias for `--format json`. | False |
| `--sarif` | Convenience alias for `--format sarif`. | False |
| `--max-depth <n>` | Traversal depth for reverse callers (`CALLS in`). | `2` |
| `--min-confidence <f>` | Minimum edge confidence filter (`0.0` - `1.0`). | `None` |
| `-w, --write <file>` | Write output to file instead of stdout. | None |

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
    "uncertainty_notice": "Static analysis identifies structural reachability and potential call exposure..."
  }
}
```

### Static Impact Rules

| Rule ID | Name | Severity | Description |
| :--- | :--- | :--- | :--- |
| **`R2G-IMP-001`** | `DisconnectedOrphanChange` | `warning` | A modified file shares no graph connections (calls, imports, defs) with any other changed file in the diff. |
| **`R2G-IMP-002`** | `UntestedPublicApiChange` | `warning` | A public function or class method changed, but no test node in the graph reaches it. |
| **`R2G-IMP-003`** | `HighBlastRadiusModification`| `warning` | A modified symbol has \(\ge 8\) direct callers or spans \(\ge 3\) separate modules. |
| **`R2G-IMP-004`** | `AmbiguousCallSite` | `note` | Downstream caller resolved with fractional confidence (< 0.70) due to dynamic or polymorphic naming. |

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

Add the following workflow to `.github/workflows/pr-impact.yml` to automatically analyze every PR:

```yaml
name: "PR Architectural Impact Analysis"

on:
  pull_request:
    types: [opened, synchronize, reopened]
    paths-ignore:
      - "docs/**"
      - "*.md"

permissions:
  contents: read

jobs:
  impact:
    name: "Analyze Diff Impact"
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
      security-events: write
    steps:
      - name: "Checkout base and head"
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 0
          persist-credentials: false

      - name: "Set up Python"
        uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: "3.12"
          cache: "pip"

      - name: "Install repo2graph"
        run: |
          python -m pip install --upgrade pip
          pip install .

      - name: "Build Graph Index on Base Branch"
        env:
          BASE_REF: ${{ github.base_ref }}
          HEAD_REF: ${{ github.head_ref }}
        run: |
          git checkout "$BASE_REF"
          repo2graph build . -o .repo2graph-base --formats jsonl,overview
          git checkout "$HEAD_REF"

      - name: "Run repo2graph Impact Analysis"
        id: impact
        env:
          BASE_REF: ${{ github.base_ref }}
        run: |
          mkdir -p .impact-reports
          repo2graph impact -i .repo2graph-base --base "origin/$BASE_REF" --head "HEAD" --format markdown --write .impact-reports/impact-report.md
          repo2graph impact -i .repo2graph-base --base "origin/$BASE_REF" --head "HEAD" --format json --write .impact-reports/impact-report.json
          repo2graph impact -i .repo2graph-base --base "origin/$BASE_REF" --head "HEAD" --format sarif --write .impact-reports/impact-report.sarif
          repo2graph impact -i .repo2graph-base --base "origin/$BASE_REF" --head "HEAD" --format pr-comment --write .impact-reports/pr-comment.md

      - name: "Publish Job Summary"
        run: |
          cat .impact-reports/pr-comment.md >> "$GITHUB_STEP_SUMMARY"

      - name: "Upload Impact Artifacts"
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: pr-impact-analysis
          path: .impact-reports/
          retention-days: 14

      - name: "Upload SARIF Results to GitHub Code Scanning"
        uses: github/codeql-action/upload-sarif@2892aa5e19bbd11bc0cff5427e3b750a04d9e3c2 # v4.38.2
        continue-on-error: true
        with:
          sarif_file: .impact-reports/impact-report.sarif
          category: repo2graph-impact

      - name: "Post PR Comment"
        if: github.event_name == 'pull_request'
        continue-on-error: true
        env:
          GH_TOKEN: ${{ github.token }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
        run: |
          gh pr comment "$PR_NUMBER" --body-file .impact-reports/pr-comment.md || true
```

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

