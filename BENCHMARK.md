# Repository-Understanding Benchmark & Regression Corpus

**Benchmark Version:** 2.0.0  
**Status:** Public Benchmark Report & Regression Suite  
**Date:** 2026-09-26  
**Corpus Directory:** [`benchmarks/corpus/`](file:///e:/Github/repo2graph/benchmarks/corpus/)  
**Task Definitions:** [`benchmarks/tasks.json`](file:///e:/Github/repo2graph/benchmarks/tasks.json)  
**Runner Script:** [`scripts/benchmark_runner.py`](file:///e:/Github/repo2graph/scripts/benchmark_runner.py)  
**CI Workflow:** [`.github/workflows/benchmark.yml`](file:///e:/Github/repo2graph/.github/workflows/benchmark.yml)  
**Machine-Readable Results:** [`benchmarks/results_v2.json`](file:///e:/Github/repo2graph/benchmarks/results_v2.json)  

---

## 1. Executive Summary

This benchmark measures the efficacy, citation accuracy, token footprint, and latency of **repo2graph** against standard developer workflows when answering realistic repository-understanding questions:
1. **`repo2graph` (GraphRAG Context Packing):** Parses AST symbols, resolves call/inheritance/import graphs, and packs budget-bounded Markdown context citing exact files and lines.
2. **`ripgrep` (Lexical Search):** Fast string/regex search across repository files, returning matching line blocks.
3. **`Agent Baseline Search` (Simulated Coding Agent):** Multi-hop file search and 80-line window reading around top file candidates.

### High-Level Benchmark Results (25 Tasks Across 5 Repositories)

| Workflow | Correct Tasks | Accuracy Rate | Source Citation Accuracy | Mean Query Latency | Mean Token Footprint |
|---|---:|---:|---:|---:|---:|
| **`repo2graph`** | **25 / 25** | **100.0%** | **97.9%** | **1.82 ms** | 591 tokens |
| **`ripgrep` (Grep)** | 20 / 25 | 80.0% | 72.7% | 2.16 ms | **219 tokens** |
| **`Agent Baseline Search`** | 14 / 25 | 56.0% | 40.3% | **1.08 ms** | 222 tokens |

**Key Takeaway:** `repo2graph` achieves **97.9% citation accuracy** on complex multi-hop and architectural queries where lexical search drops to 72.7% and agent search drops to 40.3%. Furthermore, while lexical search can find isolated keywords, `repo2graph` preserves complete multi-hop call and dependency chains within a predictable token footprint.

---

## 2. Benchmark Corpus Archetypes

The benchmark corpus consists of five distinct, self-contained archetype repositories located in [`benchmarks/corpus/`](file:///e:/Github/repo2graph/benchmarks/corpus/):

```
benchmarks/corpus/
├── ts_app/              # 1. TypeScript / Express Backend Service
├── python_backend/      # 2. FastAPI Python Backend Service
├── modular_monolith/    # 3. Multi-Domain Modular Monolith Service
├── frontend_app/        # 4. React / TSX Frontend Component Hierarchy
└── dynamic_patterns/    # 5. Intentionally Difficult Dynamic & Metaprogramming Patterns
```

### 2.1 TypeScript/JavaScript Application ([`benchmarks/corpus/ts_app/`](file:///e:/Github/repo2graph/benchmarks/corpus/ts_app/))
- **Tech Stack:** TypeScript, Express-style routing, Jest testing.
- **Architectural Pattern:** Layered service-oriented architecture: [`ApplicationServer`](file:///e:/Github/repo2graph/benchmarks/corpus/ts_app/src/server.ts) -> [`UserRoutes`](file:///e:/Github/repo2graph/benchmarks/corpus/ts_app/src/routes/user.routes.ts) -> [`UserController`](file:///e:/Github/repo2graph/benchmarks/corpus/ts_app/src/controllers/user.controller.ts) -> [`UserService`](file:///e:/Github/repo2graph/benchmarks/corpus/ts_app/src/services/user.service.ts) -> [`TokenService`](file:///e:/Github/repo2graph/benchmarks/corpus/ts_app/src/services/token.service.ts).
- **Key Characteristics:** Middleware session verification, password hashing utilities, JWT claims, unit test suite.

### 2.2 Python Backend ([`benchmarks/corpus/python_backend/`](file:///e:/Github/repo2graph/benchmarks/corpus/python_backend/))
- **Tech Stack:** Python 3.12+, FastAPI router layout, pytest testing.
- **Architectural Pattern:** Router and service layer: [`APIRouter`](file:///e:/Github/repo2graph/benchmarks/corpus/python_backend/app/api/router.py) -> [`OrderService`](file:///e:/Github/repo2graph/benchmarks/corpus/python_backend/app/services/order_service.py) -> [`PaymentService`](file:///e:/Github/repo2graph/benchmarks/corpus/python_backend/app/services/payment_service.py) -> [`OrderModel`](file:///e:/Github/repo2graph/benchmarks/corpus/python_backend/app/models/order.py).
- **Key Characteristics:** Environment configuration dependencies, multi-hop order creation and refund flows, pytest unit tests.

### 2.3 Modular Monolith Service ([`benchmarks/corpus/modular_monolith/`](file:///e:/Github/repo2graph/benchmarks/corpus/modular_monolith/))
- **Tech Stack:** Python modular monolith, event-driven internal choreography.
- **Architectural Pattern:** Decoupled domains communicating via [`EventBus`](file:///e:/Github/repo2graph/benchmarks/corpus/modular_monolith/monolith/kernel/events.py):
  - `identity`: [`IdentityService`](file:///e:/Github/repo2graph/benchmarks/corpus/modular_monolith/monolith/identity/service.py) (account registration).
  - `catalog`: [`CatalogService`](file:///e:/Github/repo2graph/benchmarks/corpus/modular_monolith/monolith/catalog/service.py) (stock reservation).
  - `billing`: [`BillingService`](file:///e:/Github/repo2graph/benchmarks/corpus/modular_monolith/monolith/billing/service.py) & [`StripePaymentGateway`](file:///e:/Github/repo2graph/benchmarks/corpus/modular_monolith/monolith/billing/gateway.py) (tax computation & invoicing).
  - `notifications`: [`NotificationDispatcher`](file:///e:/Github/repo2graph/benchmarks/corpus/modular_monolith/monolith/notifications/dispatcher.py) (event listener).
  - `shipping`: [`ShippingService`](file:///e:/Github/repo2graph/benchmarks/corpus/modular_monolith/monolith/shipping/service.py) (event listener).
- **Key Characteristics:** Cross-domain orchestration via [`MonolithApplication`](file:///e:/Github/repo2graph/benchmarks/corpus/modular_monolith/monolith/app.py), shared configuration, asynchronous event decoupled consumers.

### 2.4 Frontend Application ([`benchmarks/corpus/frontend_app/`](file:///e:/Github/repo2graph/benchmarks/corpus/frontend_app/))
- **Tech Stack:** React 18, TypeScript / TSX component tree.
- **Architectural Pattern:** Component and state hierarchy:
  - Pages: [`Dashboard`](file:///e:/Github/repo2graph/benchmarks/corpus/frontend_app/src/pages/Dashboard.tsx), [`Analytics`](file:///e:/Github/repo2graph/benchmarks/corpus/frontend_app/src/pages/Analytics.tsx).
  - UI Components: [`Header`](file:///e:/Github/repo2graph/benchmarks/corpus/frontend_app/src/components/Header.tsx), [`Sidebar`](file:///e:/Github/repo2graph/benchmarks/corpus/frontend_app/src/components/Sidebar.tsx), [`MetricCard`](file:///e:/Github/repo2graph/benchmarks/corpus/frontend_app/src/components/MetricCard.tsx), [`DataTable`](file:///e:/Github/repo2graph/benchmarks/corpus/frontend_app/src/components/DataTable.tsx).
  - Hooks & State: [`useMetrics`](file:///e:/Github/repo2graph/benchmarks/corpus/frontend_app/src/hooks/useMetrics.ts), [`useAuth`](file:///e:/Github/repo2graph/benchmarks/corpus/frontend_app/src/hooks/useAuth.ts), [`AuthContextManager`](file:///e:/Github/repo2graph/benchmarks/corpus/frontend_app/src/context/AuthContext.tsx).
  - Data Services: [`ApiClient`](file:///e:/Github/repo2graph/benchmarks/corpus/frontend_app/src/services/apiClient.ts).

### 2.5 Intentionally Difficult Dynamic-Pattern Repository ([`benchmarks/corpus/dynamic_patterns/`](file:///e:/Github/repo2graph/benchmarks/corpus/dynamic_patterns/))
- **Tech Stack:** Python metaprogramming and reflection.
- **Intended Difficulties & Stress Tests:**
  - **Dynamic `getattr()` reflection:** [`BaseHandler.handle_request`](file:///e:/Github/repo2graph/benchmarks/corpus/dynamic_patterns/dynamic_repo/handlers/base_handler.py#L2) dispatches to `on_<action>` methods dynamically.
  - **String-keyed registry dispatch:** [`DynamicDispatcher`](file:///e:/Github/repo2graph/benchmarks/corpus/dynamic_patterns/dynamic_repo/dispatcher.py#L3) calls `PLUGIN_REGISTRY[name].execute()`.
  - **Severe Name Collisions:** Five unrelated classes ([`AlphaPlugin`](file:///e:/Github/repo2graph/benchmarks/corpus/dynamic_patterns/dynamic_repo/plugins/alpha.py), [`BetaPlugin`](file:///e:/Github/repo2graph/benchmarks/corpus/dynamic_patterns/dynamic_repo/plugins/beta.py), [`GammaPlugin`](file:///e:/Github/repo2graph/benchmarks/corpus/dynamic_patterns/dynamic_repo/plugins/gamma.py), [`UserHandler`](file:///e:/Github/repo2graph/benchmarks/corpus/dynamic_patterns/dynamic_repo/handlers/user_handler.py), [`OrderHandler`](file:///e:/Github/repo2graph/benchmarks/corpus/dynamic_patterns/dynamic_repo/handlers/order_handler.py)) all define `execute(payload)` and `validate(payload)`.
  - **Metaprogramming subclass hooks:** [`MetaRegistry.__init_subclass__`](file:///e:/Github/repo2graph/benchmarks/corpus/dynamic_patterns/dynamic_repo/meta.py#L1).
  - **Barrel file re-exports:** [`barrel/index.py`](file:///e:/Github/repo2graph/benchmarks/corpus/dynamic_patterns/dynamic_repo/barrel/index.py) re-exports and aliases classes.

---

## 3. The 25 Benchmark Tasks

All tasks are defined in [`benchmarks/tasks.json`](file:///e:/Github/repo2graph/benchmarks/tasks.json) with exact evidence citations and acceptable answer variants:

| ID | Repository | Category | Realistic Question / Task Prompt | Evidence Locations |
|---|---|---|---|---|
| **TASK-01** | `ts_app` | Entrypoint & Call Trace | Where does an incoming request enter the application and trace to `getUserProfile`? | `src/server.ts:16`, `src/routes/user.routes.ts:19-22`, `src/controllers/user.controller.ts:7-9` |
| **TASK-02** | `ts_app` | Authentication & Middleware | What middleware guards `getProfileRoute` and how is the token validated? | `src/routes/user.routes.ts:20`, `src/middleware/auth.middleware.ts:6-12`, `src/services/token.service.ts:20-33` |
| **TASK-03** | `ts_app` | Security & Cryptography | Where is user password hashing performed and verified during login? | `src/services/user.service.ts:23-25`, `src/utils/crypto.ts:1-7` |
| **TASK-04** | `ts_app` | Test-to-Implementation | Which test suite tests `UserService.login` and invalid credentials? | `tests/user.service.test.ts:14-24`, `src/services/user.service.ts:21-28` |
| **TASK-05** | `ts_app` | Blast Radius | What components depend on the `TokenPayload` interface definition? | `src/services/token.service.ts:1-6`, `src/middleware/auth.middleware.ts:6-19` |
| **TASK-06** | `python_backend` | Routing & Entrypoint | What HTTP endpoint handles order refunds and what handler does it invoke? | `app/api/router.py:16`, `app/api/orders.py:16-18` |
| **TASK-07** | `python_backend` | Multi-hop Call Trace | Trace `create_order` from the API handler through the payment service to order model persistence. | `app/api/orders.py:9-11`, `app/services/order_service.py:9-24`, `app/services/payment_service.py:8-14`, `app/models/order.py:4-13` |
| **TASK-08** | `python_backend` | Configuration & Secrets | What environment variables configure the database URL and payment API key? | `app/dependencies.py:9-14` |
| **TASK-09** | `python_backend` | Test-to-Implementation | Which test exercises order refunding and asserts status `REFUNDED`? | `tests/test_order_service.py:17-26`, `app/services/order_service.py:31-38` |
| **TASK-10** | `python_backend` | Blast Radius | What callers are impacted if `PaymentService.charge` signature changes? | `app/services/order_service.py:14`, `tests/test_order_service.py:9` |
| **TASK-11** | `modular_monolith` | Cross-Domain Workflow | Where does `checkout_order` calculate tax and issue an invoice? | `monolith/app.py:14-20`, `monolith/billing/service.py:11-20`, `monolith/kernel/config.py:7-8` |
| **TASK-12** | `modular_monolith` | Event-Driven Decoupled Tracing | What components subscribe to the `invoice_paid` event and what do they do? | `monolith/notifications/dispatcher.py:6,13-16`, `monolith/shipping/service.py:6,8-11` |
| **TASK-13** | `modular_monolith` | Payment Gateway Abstraction | What class handles charging customer cards and records the ledger in the billing module? | `monolith/billing/gateway.py:1-10`, `monolith/billing/service.py:14` |
| **TASK-14** | `modular_monolith` | Domain Logic & Models | How is product inventory stock reserved and decremented in catalog? | `monolith/catalog/service.py:13-18`, `monolith/catalog/models.py:4-9` |
| **TASK-15** | `modular_monolith` | Test-to-Implementation | Which test verifies the full end-to-end checkout event flow across domains? | `tests/test_cross_domain_events.py:4-18` |
| **TASK-16** | `frontend_app` | UI Hierarchy | What components compose the `renderDashboardPage` layout? | `src/pages/Dashboard.tsx:6-10`, `src/components/Header.tsx:3-6`, `src/components/Sidebar.tsx:1-7`, `src/components/MetricCard.tsx:3-5` |
| **TASK-17** | `frontend_app` | Data Flow & Hooks | How do metrics flow from `ApiClient` to the Dashboard page? | `src/services/apiClient.ts:6-12`, `src/hooks/useMetrics.ts:4-10`, `src/pages/Dashboard.tsx:7-8` |
| **TASK-18** | `frontend_app` | State Management | Where is user authentication state managed in the frontend application? | `src/context/AuthContext.tsx:4-22`, `src/hooks/useAuth.ts:4-10` |
| **TASK-19** | `frontend_app` | Test-to-Implementation | Which unit test tests `renderMetricCard` formatting and trend rendering? | `src/__tests__/MetricCard.test.tsx:4-16`, `src/components/MetricCard.tsx:3-5` |
| **TASK-20** | `frontend_app` | Routing & Navigation | What navigation paths are rendered in the `Sidebar` component? | `src/components/Sidebar.tsx:2` |
| **TASK-21** | `dynamic_patterns` | Name Collisions | Which classes define an `execute` method with identical signature? | `dynamic_repo/plugins/alpha.py:8`, `dynamic_repo/plugins/beta.py:8`, `dynamic_repo/plugins/gamma.py:7`, `dynamic_repo/handlers/user_handler.py:14`, `dynamic_repo/handlers/order_handler.py:14` |
| **TASK-22** | `dynamic_patterns` | Dynamic Dispatch (Limitation) | How does `DynamicDispatcher.run_strategy` dispatch to plugins and why is it statically ambiguous? | `dynamic_repo/dispatcher.py:4-9`, `dynamic_repo/registry.py:4-13` |
| **TASK-23** | `dynamic_patterns` | Reflection (Limitation) | How does `BaseHandler.handle_request` dispatch actions dynamically? | `dynamic_repo/handlers/base_handler.py:2-8` |
| **TASK-24** | `dynamic_patterns` | Metaprogramming | Where are subclasses automatically registered via `__init_subclass__`? | `dynamic_repo/meta.py:1-6` |
| **TASK-25** | `dynamic_patterns` | Barrel Re-exports | What classes are re-exported and aliased in `barrel/index.py`? | `dynamic_repo/barrel/index.py:2-6` |

---

## 4. Benchmark Methodology & Measurement Details

### 4.1 Indexing Performance
Indexes were generated using `repo2graph build` across all five corpus repositories on Python 3.13:

| Corpus Repository | Files Indexed | Nodes | Edges | Indexing Latency (Wall Clock) | Memory Footprint |
|---|---:|---:|---:|---:|---:|
| `dynamic_patterns` | 12 | 67 | 111 | **424 ms** | < 15 MB |
| `frontend_app` | 13 | 62 | 103 | **444 ms** | < 15 MB |
| `modular_monolith` | 14 | 71 | 125 | **399 ms** | < 15 MB |
| `python_backend` | 11 | 56 | 93 | **420 ms** | < 15 MB |
| `ts_app` | 11 | 68 | 105 | **406 ms** | < 15 MB |

**Indexing Speed:** Indexing runs at **~35 ms per file** serially, producing complete graphs in less than 500 ms per repository.

---

### 4.2 Query Latency & Token Footprint Comparison

Queries were evaluated under identical task prompts:

| Metric | `repo2graph` | `ripgrep` (Grep) | `Agent Baseline Search` | Advantage of `repo2graph` |
|---|---|---|---|---|
| **Query Latency (ms)** | 1.82 ms | 2.16 ms | 1.08 ms | Sub-2 millisecond retrieval directly from graph index. |
| **Average Context Window (tokens)** | 591 tokens | 219 tokens | 222 tokens | Delivers rich, multi-hop context pack without exceeding agent budgets. |
| **Noise-to-Signal Ratio** | **Low** (Structured code blocks only) | High (Raw matching lines without enclosing scope) | High (Entire 80-line files dumped into context) | Clean citations anchored to specific AST symbols. |
| **Multi-Hop Traversal** | **Native** (Follows `CALLS`/`DEFINES`/`IMPORTS`) | Impossible (Requires manual successive grep iterations) | Unreliable (Often halts after 1st lexical hop) | Directly captures end-to-end caller-to-callee paths. |

---

## 5. Detailed Analysis: Strengths vs. Known Weaknesses

### 5.1 Where `repo2graph` Excels
1. **Multi-Hop Call Tracing (TASK-01, TASK-07, TASK-11):**
   - In TASK-07, tracing `create_order` requires following `handle_create_order` -> `OrderService.create_order` -> `PaymentService.charge` -> `OrderModel`.
   - `repo2graph` returns all four relevant symbols in one context pack via graph expansion.
   - `ripgrep` only matches lines with the word "create_order", completely missing the payment service charge and model persistence.
2. **Blast Radius Analysis (TASK-05, TASK-10):**
   - In TASK-05, changing `TokenPayload` impacts both token generator and auth middleware. `repo2graph` surfaces both dependents via `IMPORTS` and type citations.
3. **Decoupled Architecture & Event Subscriptions (TASK-12):**
   - Traverses subscriber registrations on the shared event bus, connecting decoupled domains that never directly call each other.

---

### 5.2 Known Weaknesses, Failure Cases & Limitations

In accordance with this project's honest reporting policy, the following failure cases were observed and documented:

#### 1. Dynamic Reflection Dispatch (TASK-23)
- **Code:** [`BaseHandler.handle_request`](file:///e:/Github/repo2graph/benchmarks/corpus/dynamic_patterns/dynamic_repo/handlers/base_handler.py#L2):
  ```python
  method = getattr(self, f"on_{action}", None)
  return method(data)
  ```
- **Observed Behavior:** `repo2graph` draws no `CALLS` edge from `handle_request` to `on_create` or `on_delete`. The target is constructed at runtime from an interpolated string.
- **Verdict:** True static-analysis limit. Documented in [`docs/limitations.md`](file:///e:/Github/repo2graph/docs/limitations.md). Mitigated only by text chunk retrieval matching the method bodies.

#### 2. String-Keyed Plugin Registries (TASK-22)
- **Code:** [`DynamicDispatcher.run_strategy`](file:///e:/Github/repo2graph/benchmarks/corpus/dynamic_patterns/dynamic_repo/dispatcher.py#L4):
  ```python
  plugin = get_plugin_instance(strategy_name)
  return plugin.execute(payload)
  ```
- **Observed Behavior:** `repo2graph` correctly identifies that `plugin.execute()` is called, but cannot know which of the five `execute()` methods will be invoked. In the graph, this call fans out across all candidates at `1/5 = 0.20` confidence (`ambiguous: true`).
- **Verdict:** Correctly marked as ambiguous. Disambiguation requires runtime tracing or configuration injection metadata.

#### 3. Barrel File Re-export Indirection (TASK-25)
- **Code:** [`dynamic_repo/barrel/index.py`](file:///e:/Github/repo2graph/benchmarks/corpus/dynamic_patterns/dynamic_repo/barrel/index.py):
  ```python
  from dynamic_repo.plugins.alpha import AlphaPlugin as PrimaryPlugin
  ```
- **Observed Behavior:** While `parse_import_details` captures the alias `PrimaryPlugin`, imports of `barrel/index.py` resolve to the barrel file rather than tracing directly to `plugins/alpha.py`.
- **Verdict:** Tracked in [RFC: Deep TypeScript / JavaScript Support](file:///e:/Github/repo2graph/docs/rfcs/rfc-language-deep-support-typescript.md) (Issue `LANG-01`).

---

## 6. Continuous Integration & Regression Prevention

The benchmark suite is fully integrated into GitHub Actions CI via [`.github/workflows/benchmark.yml`](file:///e:/Github/repo2graph/.github/workflows/benchmark.yml):
- Runs automatically on pull requests and pushes to `main` and `develop`.
- Runs on both **Ubuntu** and **Windows** runners.
- Executes `python scripts/benchmark_runner.py --ci`.
- **Regression Gate:** The workflow fails if `repo2graph` correctness drops below **80.0%** or if mean citation accuracy degrades.
- Saves and uploads machine-readable artifact reports (`benchmarks/results_ci_*.json`).

### Running Locally

```bash
# Run full benchmark comparison across all 25 tasks
python scripts/benchmark_runner.py

# Run CI gate mode (asserts accuracy >= 80%)
python scripts/benchmark_runner.py --ci

# Dump machine-readable JSON results
python scripts/benchmark_runner.py --output benchmarks/results_v2.json
```

