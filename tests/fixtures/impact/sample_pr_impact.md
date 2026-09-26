## 📐 Architectural PR Impact Report: `main`...`feature`

**Overall Assessment**: **🟡 MEDIUM RISK** (Blast Radius Score: `15`)

### 📊 Executive Summary

| Metric | Count | Details |
| :--- | :---: | :--- |
| **Files Changed** | `1` | Diffs inspected across PR |
| **Symbols Changed** | `1` | Functions, classes, and methods modified |
| **Public APIs Affected** | `1` | Exported / external surface changes |
| **Impacted Callers** | `1` direct / `0` transitive | Upstream callers reachable in graph |
| **Dependent Modules** | `1` | Modules importing changed files |
| **Impacted Tests** | `1` | Test files exercising changed code |
| **Suspicious Findings** | `1` | Orphan changes, untested APIs, blast alerts |

### ⚠️ Architectural & Risk Findings

- 🟡 **[R2G-IMP-002] Untested public API change: `bar`** ([cite: pkg/foo.py:10])
  Public API bar modified without direct test coverage.

### 🌐 Affected Public APIs

| Symbol | Kind | File:Line | Signature Changed | Direct Callers |
| :--- | :--- | :--- | :---: | :---: |
| `bar` | `function` | [cite: pkg/foo.py:10] | ⚠️ Yes | `1` |

<details><summary><strong>🔍 All Changed Symbols (1)</strong></summary>

| Symbol | Change | Scope | Location | Lines Touched |
| :--- | :---: | :---: | :--- | :---: |
| `bar` | `modified` | Public | [cite: pkg/foo.py:10-20] | `5` |

</details>

### 🧪 Impacted Test Coverage

| Test File / Suite | Exercised Target | Confidence | Evidence Citation |
| :--- | :--- | :---: | :--- |
| `tests/test_foo.py` | `bar` | `1.00` | [cite: tests/test_foo.py:12] |

### 🔗 Dependency Paths Crossing Changed Code

- `pkg/caller.py -> sym:pkg/foo.py::bar -> pkg/foo.py`

> [!IMPORTANT]
> **Static Analysis Guardrail & Uncertainty Notice**
> Static analysis cannot prove dynamic runtime breakage.
