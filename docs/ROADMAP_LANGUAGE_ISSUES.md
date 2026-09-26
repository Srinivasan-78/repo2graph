# Prioritized Language Roadmap Issues & Tracking

This document turns the findings of the Language Quality Scorecard and Strategic Language Support analysis into explicitly tracked, ready-to-implement issues and RFC work packages.

---

## Issue Matrix & Priority Summary

| Issue ID | Priority | Area / Ecosystem | Scope | Target Version | Related RFC |
|---|---|---|---|---|---|
| **LANG-01** | **P1 (Highest)** | TypeScript / JavaScript | `tsconfig.json` path alias & monorepo workspace module resolution | 2.1.0 | [RFC-TS](rfcs/rfc-language-deep-support-typescript.md) |
| **LANG-02** | **P1 (Highest)** | TypeScript / JavaScript | Express, NestJS, & Next.js HTTP route & controller extraction (`ROUTES_TO`) | 2.1.0 | [RFC-TS](rfcs/rfc-language-deep-support-typescript.md) |
| **LANG-03** | **P1 (Highest)** | TypeScript / JavaScript | Jest & Vitest test-to-implementation graph linking (`TESTS`) | 2.1.0 | [RFC-TS](rfcs/rfc-language-deep-support-typescript.md) |
| **LANG-04** | **P1 (Highest)** | Python | FastAPI, Flask, & Django route-to-handler resolution (`ROUTES_TO`) | 2.1.0 | [RFC-PY](rfcs/rfc-language-deep-support-python.md) |
| **LANG-05** | **P1 (Highest)** | Python | Pytest test-to-implementation linking & fixture injection (`TESTS`) | 2.1.0 | [RFC-PY](rfcs/rfc-language-deep-support-python.md) |
| **LANG-06** | **P1 (Highest)** | Python | SQLAlchemy & Django model relational schema extraction (`MODELS`) | 2.1.0 | [RFC-PY](rfcs/rfc-language-deep-support-python.md) |
| **LANG-07** | **P2 (High)** | Java / Kotlin (JVM) | Spring Boot & Jakarta Dependency Injection resolution (`INJECTS`) | 2.2.0 | [RFC-JVM-GO](rfcs/rfc-language-deep-support-jvm-go.md) |
| **LANG-08** | **P2 (High)** | Java / Kotlin (JVM) | Spring MVC / JAX-RS routes (`ROUTES_TO`) & JUnit test links (`TESTS`) | 2.2.0 | [RFC-JVM-GO](rfcs/rfc-language-deep-support-jvm-go.md) |
| **LANG-09** | **P2 (High)** | Go (Golang) | Anonymous struct embedding & implicit interface satisfaction (`INHERITS`) | 2.2.0 | [RFC-JVM-GO](rfcs/rfc-language-deep-support-jvm-go.md) |
| **LANG-10** | **P2 (High)** | Go (Golang) | Gin / Chi / Echo web routing & `*_test.go` table-driven test linking | 2.2.0 | [RFC-JVM-GO](rfcs/rfc-language-deep-support-jvm-go.md) |
| **LANG-11** | **P2 (High)** | Core Test Suite | Eliminate zero-test coverage for TypeScript, TSX, Java, Scala, Rust, Swift | 2.1.0 | Scorecard Generator |

---

## Detailed Tracked Issue Descriptions

### LANG-01: TypeScript/JavaScript `tsconfig.json` Path Aliases & Monorepos
- **Labels:** `area/graph`, `lang/typescript`, `enhancement`, `priority/p1`
- **Problem:** Paths like `@app/services/user` or `@shared/utils` fail `resolve_import()` and become synthetic `module:@app/...` external nodes, fragmenting internal monorepos.
- **Scope:**
  - Enhance `graph.repo_context(root)` to read `tsconfig.json` and `jsconfig.json`.
  - Extract `compilerOptions.baseUrl` and `compilerOptions.paths`.
  - Parse root `pnpm-workspace.yaml` and `package.json` `"workspaces"`.
  - Map import targets to concrete internal files in `resolve_import()`.
- **Acceptance Criteria:**
  - Indexing a repo with `@components/*: ["src/components/*"]` resolves `@components/Button` to `file:src/components/Button.tsx`.
  - Unit tests in `tests/test_typescript_paths.py` assert `internal=True` on mapped import edges.

---

### LANG-02: Express, NestJS, and Next.js Route Extraction
- **Labels:** `area/graph`, `area/frameworks`, `lang/typescript`, `enhancement`, `priority/p1`
- **Problem:** HTTP routes and API endpoints are invisible in the call graph; queries asking "what handles `/api/checkout`" rely on lexical guesses.
- **Scope:**
  - Implement AST pattern matching for Express/Fastify `app.get()`, `router.post()`.
  - Parse NestJS `@Controller("path")` and method decorators `@Get()`, `@Post()`.
  - Parse Next.js App Router `app/api/**/route.ts` HTTP verb handlers (`GET`, `POST`).
  - Emit `ROUTES_TO` edges conforming to `edgemeta.py`.
- **Acceptance Criteria:**
  - Verified by tests in `tests/test_typescript_routes.py`.
  - Every route node has format `route:<METHOD>:<PATH>` and connects to a handler symbol.

---

### LANG-03: Jest & Vitest Test-to-Implementation Graph Linking
- **Labels:** `area/graph`, `area/testing`, `lang/typescript`, `enhancement`, `priority/p1`
- **Problem:** Tests are not linked to production code, preventing agents from knowing what test suite covers a changed module.
- **Scope:**
  - Identify test files (`*.test.ts`, `*.spec.ts`).
  - Strip suffix to link `file:auth.test.ts` --`TESTS`--> `file:auth.ts`.
  - Extract `describe("<Symbol>")` blocks and link to imported class/function symbols.
- **Acceptance Criteria:**
  - `TESTS` edges carry `method: "test_target_heuristic"` and confidence >= 0.90.
  - Tested in `tests/test_typescript_tests_link.py`.

---

### LANG-04: FastAPI, Flask, & Django Route-to-Handler Resolution
- **Labels:** `area/graph`, `area/frameworks`, `lang/python`, `enhancement`, `priority/p1`
- **Problem:** Python web API endpoints are disconnected from HTTP route specifications.
- **Scope:**
  - Extract FastAPI `@app.get(...)`, `@router.post(...)` with prefix tracking.
  - Extract Flask `@bp.route(...)` and Blueprints.
  - Extract Django `urlpatterns = [path(...)]` mapping to view functions/classes.
- **Acceptance Criteria:**
  - Django benchmark indexing produces >500 `ROUTES_TO` edges connecting URL paths to view symbols.
  - Unit tests in `tests/test_python_routes.py`.

---

### LANG-05: Pytest Test-to-Implementation Linking & Fixture Injection
- **Labels:** `area/graph`, `area/testing`, `lang/python`, `enhancement`, `priority/p1`
- **Problem:** Pytest tests and fixtures are isolated from the code they verify.
- **Scope:**
  - Pair `tests/test_foo.py` with `foo.py`.
  - Link `test_create_user()` -> `create_user()`.
  - Map `@pytest.fixture` parameter injection to calling test functions as `INJECTS` edges.
- **Acceptance Criteria:**
  - Verified on `repo2graph`'s own test suite (`tests/` -> `repo2graph/`).

---

### LANG-06: SQLAlchemy & Django ORM Relational Schema Extraction
- **Labels:** `area/graph`, `area/orm`, `lang/python`, `enhancement`, `priority/p1`
- **Problem:** Database entity models have no relational edges in the graph, losing data architecture insight.
- **Scope:**
  - Parse SQLAlchemy `relationship(...)` and `ForeignKey(...)`.
  - Parse Django `models.ForeignKey`, `models.ManyToManyField`.
  - Emit bidirectional `MODELS` edges with `relation_type`.
- **Acceptance Criteria:**
  - Model entity classes connect to related models with confidence 1.0.

---

### LANG-07: Spring Boot & Jakarta Dependency Injection Resolution
- **Labels:** `area/graph`, `area/di`, `lang/java`, `enhancement`, `priority/p2`
- **Problem:** Spring `@Autowired` turns call sites into abstract interface hits or external misses.
- **Scope:**
  - Detect `@Autowired`, `@Inject`, `@Resource`, and constructor injection on Spring `@Component`/`@Service` classes.
  - Resolve interface injections to concrete implementations via `INHERITS` hierarchy.
  - Emit `INJECTS` edges.
- **Acceptance Criteria:**
  - Single-implementation interface injections resolve to concrete service at confidence 0.95.

---

### LANG-08: Spring MVC & JAX-RS Routes & JUnit Test Links
- **Labels:** `area/graph`, `lang/java`, `enhancement`, `priority/p2`
- **Scope:** Class `@RequestMapping` + method `@GetMapping` -> `ROUTES_TO`; `@Test` methods -> `TESTS`.
- **Acceptance Criteria:**
  - JUnit 5 test classes link to corresponding production services.

---

### LANG-09: Go Struct Embedding & Implicit Interface Satisfaction
- **Labels:** `area/graph`, `lang/go`, `enhancement`, `priority/p2`
- **Problem:** Go has no classes, so `INHERITS` edges are currently 0 in all Go repositories.
- **Scope:**
  - Parse anonymous struct fields as `INHERITS` (subtype: "EMBEDS").
  - Match struct methods against interface declarations to emit `INHERITS` (subtype: "IMPLEMENTS").
- **Acceptance Criteria:**
  - Kubernetes example indexing produces >1,000 embedded struct inheritance edges.

---

### LANG-10: Go Web Routing & Table-Driven Test Linking
- **Labels:** `area/graph`, `lang/go`, `enhancement`, `priority/p2`
- **Scope:** Gin/Chi router calls -> `ROUTES_TO`; `*_test.go` and `TestXxx` functions -> `TESTS`.

---

### LANG-11: Core Test Suite Language Coverage Parity
- **Labels:** `area/testing`, `tech-debt`, `priority/p2`
- **Problem:** Several supported languages have zero dedicated unit tests in `tests/` (TypeScript: 0, Scala: 0, Java: 0 dedicated symbol tests).
- **Scope:** Add targeted unit tests for every language config in `tests/test_language_coverage.py` asserting symbol extraction, calls, and imports.

