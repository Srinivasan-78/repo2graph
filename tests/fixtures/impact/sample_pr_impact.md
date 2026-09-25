# PR Impact Analysis Report

**Base Ref:** `main`  
**Head Ref:** `feature/auth-refresh`  
**Files Changed:** 3 | **Lines Added:** 42 | **Lines Deleted:** 8  
**Analysis Timestamp:** 2026-09-26T00:00:00Z  

---

## ⚠️ Architectural & Risk Summary
- **Public APIs Affected:** 1
- **Direct & Transitive Callers Impacted:** 3 (across 2 depth hops)
- **Dependent Modules Impacted:** 2
- **Direct Test Coverage:** 1 test file(s) reaching changed symbols
- **Suspicious / Disconnected Changes:** 1 finding(s)

> [!NOTE]
> **Static Analysis Guardrail & Uncertainty Notice:**
> This impact analysis is grounded solely in static code-graph relationships (AST symbols, static calls, module imports).
> It does not constitute proof of dynamic runtime breakage. Dynamic dispatch, reflection, monkey-patching, or environment variables may affect real runtime behavior.

---

## 🔍 Changed Symbols
| Symbol | Path | Lines | Kind | Public API? | Sig Changed? | Diff |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `AuthService.refresh_token` | `src/auth/service.py` | 45-68 | method | **YES** | **YES** | 12 added lines |
| `_parse_jwt_claims` | `src/auth/service.py` | 110-125 | function | NO | NO | 5 added lines |

---

## 🚨 Affected Public APIs
- **`AuthService.refresh_token`** (`src/auth/service.py:45`)
  - *Signature Changed:* Yes
  - *Current Signature:* `def refresh_token(self, token: str, client_id: str | None = None) -> TokenPair:`
  - *Potential Impact:* Downstream callers passing positional arguments or assuming the old signature may be broken.

---

## 📞 Impacted Callers & Blast Radius
- `api/routes/auth.py:82` calls `AuthService.refresh_token`
  - *Caller:* `handle_refresh_request`
  - *Depth:* 1 hop | *Confidence:* 1.0 (certain)
  - *Evidence:* `api/routes/auth.py:82`
- `api/middleware/auth.py:34` calls `handle_refresh_request`
  - *Caller:* `AuthMiddleware.authenticate`
  - *Depth:* 2 hops | *Confidence:* 0.9 (static resolution)
  - *Evidence:* `api/middleware/auth.py:34`

---

## 🧪 Test Coverage & Impacted Tests
- `tests/test_auth.py:12` calls `AuthService.refresh_token`
  - *Test:* `test_refresh_token_valid`
  - *Confidence:* 1.0 | *Evidence:* `tests/test_auth.py:12`

> [!TIP]
> **Recommended Test Execution:**
> Run `pytest tests/test_auth.py` to validate direct symbol changes.

---

## 📦 Dependent Modules Crossing Diff Boundary
- `api/routes/auth.py` imports `src/auth/service.py`
- `api/middleware/auth.py` imports `api/routes/auth.py`

---

## 🚩 Suspicious & Disconnected Findings
- **[R2G-IMP-001] Disconnected file change:** `scripts/deploy_helper.py` has changes (+25 lines) but has no callers, imports, or definitions connected to the rest of the PR's modified symbols.
  - *Suggestion:* Verify if this change belongs in an independent pull request.

