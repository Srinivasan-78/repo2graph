# Strategic Language Support & Ecosystem Relationship Architecture

**Document Version:** 2.0.0  
**Status:** Canonical Strategy & Roadmap  
**Date:** 2026-09-26  
**Related RFCs:**  
- [RFC: Ecosystem Relationship Graph Expansion](file:///e:/Github/repo2graph/docs/rfcs/rfc-framework-relationship-graph.md)  
- [RFC: Deep TypeScript / JavaScript Support](file:///e:/Github/repo2graph/docs/rfcs/rfc-language-deep-support-typescript.md)  
- [RFC: Deep Python Support](file:///e:/Github/repo2graph/docs/rfcs/rfc-language-deep-support-python.md)  
- [RFC: Strategic Tier 2 Support — JVM vs. Go](file:///e:/Github/repo2graph/docs/rfcs/rfc-language-deep-support-jvm-go.md)  
- [Prioritized Language Roadmap Issues](file:///e:/Github/repo2graph/docs/ROADMAP_LANGUAGE_ISSUES.md)  

---

## 1. Executive Summary

`repo2graph` supports 17 tree-sitter language grammars, enabling file discovery, text chunking, and lexical AST symbol extraction (`function`, `class`, `method`, `interface`, etc.) across 29 file extensions.

However, existing code intelligence treats all languages with a uniform, purely syntactic approach. In modern software engineering, code is organized around **architectural patterns and framework conventions**:
- Web request handlers are bound to URLs via decorators and routing tables, not internal function calls.
- Inversion of Control (IoC) and Dependency Injection (DI) containers bind interfaces to runtime classes, turning direct calls into abstract interface declarations or ambiguous external misses.
- Tests exercise components via test runners (pytest, Jest, Vitest, JUnit, Go test) rather than caller graphs.
- ORM entities define relational database schemas that declare structural data flow.

This document establishes a **strategic roadmap for language support**:
1. It inspects current repository test coverage, parser failure rates, and feature parity.
2. It establishes a prioritized tiering order: **Tier 1 (TypeScript/JavaScript, Python)** followed by **Tier 2 (Java/Kotlin, then Go)** based on actual repository capabilities.
3. It outlines ecosystem-aware extraction opportunities across routes, test links, package imports, DI, configuration, and ORMs.
4. It delivers the **Language Quality Scorecard** methodology and runnable generator ([`generate_language_scorecard.py`](file:///e:/Github/repo2graph/scripts/generate_language_scorecard.py)).
5. It formalizes weak and missing capabilities into tracked RFCs and roadmap issues.

---

## 2. Current State Inspection & Audit

### 2.1 Parser Failure Rates (Empirical Benchmark Corpus)

From empirical runs across five large open-source repositories ([`docs/limitations.md`](file:///e:/Github/repo2graph/docs/limitations.md) and [`benchmarks/results.json`](file:///e:/Github/repo2graph/benchmarks/results.json)), parser failure rates divide sharply along language paradigms:

| Repository | Primary Language | Files Indexed | Files with Parse Errors | Total `parse_errors` | Preprocessor Fallback | Failure Characterization |
|---|---|---:|---:|---:|---:|---|
| [Linux kernel](file:///e:/Github/repo2graph/examples/linux/) | C | 3,660 | **1,356 (37.0%)** | 14,631 | 65 files | Severe macro preprocessor divergence (`SYSCALL_DEFINE`, conditional compilation). |
| [TensorFlow](file:///e:/Github/repo2graph/examples/tensorflow/) | C++ / Python | 1,022 | **325 (31.8%)** | 12,473 | 6 files | Heavy C++ template metaprogramming & macro headers. |
| [Kubernetes](file:///e:/Github/repo2graph/examples/kubernetes/) | Go | 1,084 | **30 (2.8%)** | 260 | N/A | Clean AST grammar; minor syntax errors in generated clients. |
| [Django](file:///e:/Github/repo2graph/examples/django/) | Python | 5,629 | **2 (0.04%)** | 11 | N/A | Near-zero parser failure; AST grammar matches modern syntax flawlessly. |
| [VS Code](file:///e:/Github/repo2graph/examples/vscode/) | TypeScript | 6,000 | **4 (0.07%)** | 6 | N/A | Near-zero parser failure; handles JSX, generics, and type aliases cleanly. |

**Key Finding:** C and C++ require active preprocessor mitigation and will always exhibit non-trivial tree-sitter `ERROR` nodes. Conversely, **TypeScript, Python, and Go exhibit near-zero parser errors (<0.1%–2.8%)**, making them ideal foundations for high-confidence semantic relationship graphs.

---

### 2.2 Call Ambiguity & Scoped Resolution

Call-name ambiguity rates from the benchmark corpus reveal how symbol reuse affects graph quality:

| Repository | Primary Language | `CALLS` Edges | Resolved by Scope (Tiers 0–4) | Ambiguous (Matched >1 Candidate) | Ambiguity Rate |
|---|---|---:|---:|---:|---:|
| [TensorFlow](file:///e:/Github/repo2graph/examples/tensorflow/) | C++ / Python | 58,556 | 24,989 | 12,487 | 21.3% |
| [Django](file:///e:/Github/repo2graph/examples/django/) | Python | 189,381 | 35,938 | 35,338 | 18.7% |
| [VS Code](file:///e:/Github/repo2graph/examples/vscode/) | TypeScript | 450,921 | 137,142 | 82,225 | **18.2%** |
| [Kubernetes](file:///e:/Github/repo2graph/examples/kubernetes/) | Go | 62,630 | 17,059 | 10,948 | 17.5% |
| [Linux kernel](file:///e:/Github/repo2graph/examples/linux/) | C | 68,785 | 47,205 | 3,155 | 4.6% |

**Key Finding:** In modern object-oriented and functional codebases (TypeScript, Python, Java), **18–21% of calls remain ambiguous** after name matching. In VS Code, method names like `dispose()`, `register()`, and `getId()` appear on hundreds of unrelated types. Resolving this ambiguity requires **type-aware import tracking, interface implementation graphs, and dependency injection linking**.

---

### 2.3 Repository Unit Test Coverage Audit

An audit of `tests/` reveals severe imbalances in dedicated unit tests across the 17 languages:

| Language | Test Files Referencing | Dedicated Test Functions | Status |
|---|---|---|---|
| **Python** | 6 files | 6 functions + all E2E fixtures | Comprehensive E2E coverage; unit tests for specific edge cases. |
| **Go** | 3 files | 15 functions (`_test.go` filtering, `go.mod` module path) | Strong import testing; zero route/struct embedding tests. |
| **C / C++** | 3 files | 10 functions ([`test_cpp_parse.py`](file:///e:/Github/repo2graph/tests/test_cpp_parse.py)) | High coverage of cpp preprocessor fallback and `#377` header sniffing. |
| **PHP** | 3 files | 4 functions (`\` namespace separator, import aliases) | Good targeted regression coverage. |
| **Ruby** | 3 files | 3 functions (method receiver stripping, require/load) | Moderate coverage. |
| **Bash** | 5 files | 3 functions (`source`, `.`, surrogateescape encoding) | Moderate coverage. |
| **Lua** | 2 files | 2 functions ([`test_parse_lua_extracts_functions_and_calls`](file:///e:/Github/repo2graph/tests/test_repo2graph.py#L213)) | Good baseline coverage for a Tier 4 language. |
| **Swift** | 3 files | 2 functions (import details, symbol extraction) | Moderate coverage. |
| **CSharp** | 3 files | 2 functions (`using` directives, import aliases) | Low coverage. |
| **JavaScript** | 3 files | 2 functions (symbol/arrow functions, relative imports) | Low coverage. |
| **TSX** | 2 files | 1 function ([`test_parse_source_tsx_symbols_calls_and_imports`](file:///e:/Github/repo2graph/tests/test_repo2graph.py#L2497)) | Low coverage. |
| **Rust** | 3 files | 1 function (outer doc comments, Cargo.toml context) | Low coverage. |
| **Kotlin** | 3 files | 1 function (import regex) | Low coverage. |
| **TypeScript** | 3 files | **0 dedicated unit test functions** | **Critical Deficit** (relies on generic/TSX tests). |
| **Java** | 2 files | **0 dedicated symbol unit test functions** | **Critical Deficit** (only generic bases & inheritance). |
| **Scala** | 2 files | **0 dedicated unit test functions** | **Critical Deficit**. |

---

### 2.4 Language Feature Parity Matrix

Evaluating all 17 languages across core static analysis features in [`repo2graph/parse.py`](file:///e:/Github/repo2graph/repo2graph/parse.py) and [`repo2graph/graph.py`](file:///e:/Github/repo2graph/repo2graph/graph.py):

| Language | Grammars Active | Extracted Symbol Kinds | Call Types Captured | Dynamic Callees | Decorators / Annotations | Structured Import Details | Specialized Import Resolver | Manifest / Workspace Awareness | Inheritance (`INHERITS`) |
|---|---|---|---|---|---|---|---|---|---|
| **Python** | Yes | `function`, `class` | `call` | 5 callees | Yes (`@decorator`) | Yes | Yes (relative, package, `src/`) | No | Full (`extends`, `evidence`) |
| **TypeScript** | Yes | `function`, `class`, `method`, `interface`, `type`, `enum`, `maybe_function` | `call_expression`, `new_expression` | 3 callees | Yes (`@decorator`) | Yes | Yes (relative, `.js`->`.ts`, `src/`) | **No (`tsconfig` paths missing)** | Full (`extends`, `implements`) |
| **TSX** | Yes | Same as TypeScript | Same as TypeScript | 3 callees | Yes | Yes | Same as TypeScript | No | Full |
| **JavaScript** | Yes | `function`, `class`, `method`, `maybe_function` | `call_expression`, `new_expression` | 3 callees | Yes | Yes | Yes (relative, `src/`) | No | Full (`extends`) |
| **Go** | Yes | `function`, `method`, `type` | `call_expression` | None | No | Yes | Yes (package directory mapping) | **Yes (`go.mod` module path)** | **None (no struct embedding)** |
| **Java** | Yes | `method`, `class`, `interface`, `enum` | `method_invocation`, `object_creation` | None | Yes (`@annotation`) | Yes | Yes (package dot to slash) | No (`pom.xml`/Gradle missing) | Full (`extends`, `implements`) |
| **Kotlin** | Yes | `function`, `class`, `object` | `call_expression` | None | Yes | Yes | Yes (package path) | No | Full (`extends`, `implements`) |
| **Rust** | Yes | `function`, `struct`, `enum`, `trait`, `impl`, `module` | `call_expression`, `macro_invocation` | None | No (outer attributes in doc) | Yes | Yes (`crate::`, `super::`, `self::`) | **Yes (`Cargo.toml` crate name)** | Traits in bases |
| **CSharp** | Yes | `method`, `class`, `interface`, `struct` | `invocation`, `object_creation` | None | Yes | Yes | Yes (`using` alias & namespace) | No | Full (`extends`, `implements`) |
| **PHP** | Yes | `function`, `method`, `class`, `interface` | `function_call`, `member_call`, `object_creation` | 3 callees | Yes | Yes | Yes (`App\` -> `app/`, `src/`) | No | Full (`\` namespace stripping) |
| **Ruby** | Yes | `method`, `singleton_method`, `class`, `module` | `call` | 4 callees | No | Yes | Yes (`require`, `require_relative`) | No | Full (`< Superclass`) |
| **C** | Yes | `function`, `struct`, `enum` | `call_expression` | None | No | Yes | Header file lookup | No | None |
| **C++** | Yes | `function`, `class`, `namespace`, `struct`, `enum` | `call_expression` | None | No | Yes | Header file lookup + sniffing | No | Full (`: public Base`) |
| **Swift** | Yes | `function`, `class`, `protocol` | `call_expression` | None | No | Yes | Yes (`Sources/` path mapping) | No | Full (`: Protocol, Super`) |
| **Scala** | Yes | `function`, `class`, `object`, `trait` | `call_expression` | None | No | Yes | Yes (package mapping) | No | Full (`with` mixin support) |
| **Bash** | Yes | `function` | `command` | None | No | Yes | Yes (`source`, `.`) | No | None |
| **Lua** | Yes | `function` | `function_call` | None | No | **No** | **No** | No | None |

---

## 3. Recommended Priority Order for Deep Support

Based on empirical parser stability, developer adoption, repository feature gaps, and GraphRAG retrieval value:

```
┌────────────────────────────────────────────────────────┐
│  Tier 1: High Priority (Immediate Value & Dominance)   │
│  • TypeScript / JavaScript (Web, Full-stack, VS Code)  │
│  • Python (AI/ML, Native Runtime, FastAPI, Django)     │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│  Tier 2: Enterprise & Cloud-Native (Strategic Decision)│
│  • Java / Kotlin (Enterprise Backend, Spring Boot, DI) │
│  • Go (Cloud-Native Infrastructure, Kubernetes, Gin)   │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│  Tier 3: Systems & Standard Backend                    │
│  • Rust, C#, PHP, C++, C, Ruby                         │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│  Tier 4: Scripting & Specialized                       │
│  • Swift, Scala, Bash, Lua                             │
└────────────────────────────────────────────────────────┘
```

### 3.1 Tier 1: TypeScript / JavaScript & Python

#### TypeScript / JavaScript
- **Strategic Imperative:** Dominates web application and frontend/full-stack development.
- **Current Blocker:** Severe import fragmentation in monorepos because `tsconfig.json` `paths` (`@app/*`, `@components/*`) and npm/pnpm workspace packages fail resolution and become external modules. High call ambiguity (18.2% in VS Code) due to lack of interface/constructor DI linking.
- **Deliverables:** [RFC: Deep TypeScript / JavaScript Support](file:///e:/Github/repo2graph/docs/rfcs/rfc-language-deep-support-typescript.md).

#### Python
- **Strategic Imperative:** Primary ecosystem for modern AI/ML, data pipelines, and backend APIs. Native language of `repo2graph`.
- **Current Blocker:** Extremely clean AST parsing (0.04% error rate in Django), but zero extraction of FastAPI/Django/Flask HTTP routes, no pytest-to-implementation linking, and no SQLAlchemy/Django model relationships.
- **Deliverables:** [RFC: Deep Python Support](file:///e:/Github/repo2graph/docs/rfcs/rfc-language-deep-support-python.md).

---

### 3.2 Tier 2 Strategic Decision: Java/Kotlin vs. Go

The choice for the next deep language investment is between **Enterprise JVM (Java/Kotlin)** and **Cloud-Native Go**.

#### Strategic Comparison:
1. **The Case for Java / Kotlin (Primary Recommendation):**
   - **The Semantic Gap is Severe:** In Spring Boot / Micronaut applications, almost all high-level wiring is done via Dependency Injection (`@Autowired`, `@Inject`) and routing annotations (`@RestController`, `@GetMapping`). In `repo2graph` today, calling an interface method either lands on the abstract declaration or fans out ambiguously. A Spring codebase in `repo2graph` loses its architectural connectivity.
   - **Enterprise Demand:** Large enterprise codebases adopting AI coding agents (banking, healthcare, enterprise SaaS) are disproportionately built on Java/Kotlin.
   - **Recommendation:** **Prioritize Java/Kotlin as Tier 2A.**

2. **The Case for Go (Immediate Tier 2B Follow-up):**
   - **Low Friction:** Go's tree-sitter parser is exceptionally reliable (2.8% error rate in Kubernetes). `go.mod` is already parsed for module path resolution.
   - **Targeted Gaps:** Go has no classes, so `INHERITS` is completely empty. Implementing **struct embedding** (`type Server struct { http.Server }`) and **implicit interface satisfaction** is structurally straightforward and delivers immediate cloud-native value.
   - **Recommendation:** **Schedule Go immediately following Java/Kotlin.**

Detailed design: [RFC: Strategic Tier 2 Support — JVM vs. Go](file:///e:/Github/repo2graph/docs/rfcs/rfc-language-deep-support-jvm-go.md).

---

## 4. Ecosystem-Aware Relationship Extraction Opportunities

To evolve `repo2graph` from a syntactic AST extractor into an ecosystem-aware knowledge graph, we introduce five new edge types defined in [RFC: Ecosystem Relationship Graph Expansion](file:///e:/Github/repo2graph/docs/rfcs/rfc-framework-relationship-graph.md).

### 4.1 Summary of New Edge Capabilities Across Priority Languages

| Relationship Domain | Edge Type | TypeScript / JavaScript | Python | Java / Kotlin | Go |
|---|---|---|---|---|---|
| **Routes & Controllers** | `ROUTES_TO` | Express (`app.get`), NestJS (`@Controller`), Next.js App Router | FastAPI (`@app.get`), Flask (`@bp.route`), Django `urlpatterns` | Spring MVC (`@GetMapping`), JAX-RS (`@Path`) | Gin (`r.GET`), Chi (`r.Route`), `http.HandleFunc` |
| **Test-to-Implementation** | `TESTS` | Jest / Vitest `*.test.ts`, `describe()`, `it()` | Pytest `test_*.py`, `test_*()`, fixture injection | JUnit 5 `@Test`, `*Test.java`, `@Nested` | `*_test.go`, `TestXxx(t *testing.T)` |
| **Imports & Packages** | `IMPORTS` | `tsconfig.json` `paths`, pnpm/yarn workspaces, barrel re-exports | `pyproject.toml` packages, namespace packages | Maven `pom.xml`, Gradle multi-modules | `go.work` workspaces, internal packages |
| **Dependency Injection** | `INJECTS` | NestJS `@Injectable()`, `@Inject()`, Angular DI | FastAPI `Depends()`, dependency-injector | Spring `@Autowired`, `@Component`, `@Service` | Wire, manual constructor injection |
| **Configuration References** | `CONFIGURES` | `process.env.VAR`, `package.json`, `.env.example` | Pydantic `BaseSettings`, Django `settings.py` | `application.properties`, `@Value("${...}")` | Viper config keys, env struct tags |
| **ORM & Model Schemas** | `MODELS` | Prisma (`schema.prisma`), TypeORM (`@Entity`) | SQLAlchemy (`relationship`), Django `ForeignKey` | JPA / Hibernate (`@Entity`, `@ManyToOne`) | GORM struct tags (`gorm:"foreignKey"`) |

---

## 5. Language Quality Scorecards

Generated via [`scripts/generate_language_scorecard.py`](file:///e:/Github/repo2graph/scripts/generate_language_scorecard.py), evaluating each language across 6 standard dimensions:
- **Parsing:** AST grammar availability, benchmark error rates, macro stability. (Weight: 20%)
- **Symbols:** Kind breadth (functions, classes, interfaces, types, enums, arrow funcs), signatures, docstrings. (Weight: 20%)
- **Calls:** Call expressions, dynamic callees, decorators/annotations, scoped resolution tiers. (Weight: 20%)
- **Imports:** Structured import details, candidate heuristics, manifest context (`go.mod`, `Cargo.toml`). (Weight: 20%)
- **Tests Link:** Test file detection, test runner awareness, test-to-target linking. (Weight: 10%)
- **Framework Edges:** Routes, dependency injection, configuration, ORMs. (Weight: 10%)

### 5.1 Comprehensive Scorecard Table

| Language | Tier | Overall | Parsing | Symbols | Calls | Imports | Tests Link | Framework | Repo Tests |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **tsx** | Tier 1 | **82 (B)** | 95 (A) | 100 (A) | 100 (A) | 90 (A-) | 30 (F) | 20 (F) | 1 funcs |
| **typescript** | Tier 1 | **82 (B)** | 95 (A) | 100 (A) | 100 (A) | 90 (A-) | 30 (F) | 20 (F) | 0 funcs |
| **javascript** | Tier 1 | **78 (B)** | 95 (A) | 80 (B) | 100 (A) | 90 (A-) | 30 (F) | 20 (F) | 2 funcs |
| **python** | Tier 1 | **73 (B-)** | 95 (A) | 55 (D) | 100 (A) | 90 (A-) | 30 (F) | 20 (F) | 6 funcs |
| **go** | Tier 2 | **68 (C+)** | 95 (A) | 65 (C+) | 55 (D) | 100 (A) | 50 (D) | 0 (F) | 1 funcs |
| **java** | Tier 2 | **67 (C+)** | 90 (A-) | 65 (C+) | 75 (B-) | 80 (B) | 30 (F) | 20 (F) | 0 funcs |
| **kotlin** | Tier 2 | **61 (C)** | 90 (A-) | 55 (D) | 55 (D) | 90 (A-) | 30 (F) | 0 (F) | 1 funcs |
| **php** | Tier 3 | **72 (B-)** | 90 (A-) | 85 (B+) | 80 (B) | 90 (A-) | 30 (F) | 0 (F) | 4 funcs |
| **rust** | Tier 3 | **67 (C+)** | 90 (A-) | 75 (B-) | 55 (D) | 100 (A) | 30 (F) | 0 (F) | 1 funcs |
| **csharp** | Tier 3 | **64 (C)** | 90 (A-) | 60 (C) | 65 (C+) | 90 (A-) | 30 (F) | 0 (F) | 2 funcs |
| **ruby** | Tier 3 | **62 (C)** | 90 (A-) | 45 (F) | 80 (B) | 80 (B) | 30 (F) | 0 (F) | 3 funcs |
| **c** | Tier 3 | **54 (D)** | 75 (B-) | 60 (C) | 55 (D) | 80 (B) | 0 (F) | 0 (F) | 73 funcs |
| **cpp** | Tier 3 | **54 (D)** | 75 (B-) | 60 (C) | 55 (D) | 80 (B) | 0 (F) | 0 (F) | 10 funcs |
| **scala** | Tier 4 | **61 (C)** | 90 (A-) | 70 (B-) | 55 (D) | 90 (A-) | 0 (F) | 0 (F) | 0 funcs |
| **swift** | Tier 4 | **61 (C)** | 90 (A-) | 70 (B-) | 55 (D) | 90 (A-) | 0 (F) | 0 (F) | 2 funcs |
| **bash** | Tier 4 | **51 (D)** | 85 (B+) | 35 (F) | 55 (D) | 80 (B) | 0 (F) | 0 (F) | 3 funcs |
| **lua** | Tier 4 | **35 (F)** | 85 (B+) | 35 (F) | 55 (D) | 0 (F) | 0 (F) | 0 (F) | 2 funcs |

---

### 5.2 Scorecard Analysis & Key Takeaways

1. **The Syntax Ceiling (B/B-):** Currently, no language scores above 82 (`B`). All Tier 1 and 2 languages receive failing grades (`F` or `D`) in **Tests Link** (0–50%) and **Framework Edges** (0–20%) because `repo2graph` does not yet extract routes, DI, or test relationships. Implementing the RFCs will lift Tier 1 languages into the 90–95 (`A`) range.
2. **Go's Import Strength vs Call Weakness:** Go earns 100 (`A`) on imports due to native `go.mod` module resolution and `*_test.go` filtering, but drops to 55 (`D`) on calls and 65 (`C+`) on symbols due to the absence of struct embedding and dynamic call detection.
3. **The C/C++ Macro Penalty:** Despite extensive work ([`test_cpp_parse.py`](file:///e:/Github/repo2graph/tests/test_cpp_parse.py)), C/C++ score 54 (`D`) due to macro error rates in real-world kernels and frameworks (32–37% files affected).
4. **Test Suite Debt:** TypeScript (0 tests), Java (0 dedicated symbol tests), and Scala (0 tests) suffer from test coverage debt in the repository, despite active grammar support.

---

## 6. Prioritized Roadmap & Milestone Plan

### Milestone 1 (v2.1.0): Foundation & Priority 1 Deep Support
- **Core Schema:** Land [RFC: Ecosystem Relationship Graph Expansion](file:///e:/Github/repo2graph/docs/rfcs/rfc-framework-relationship-graph.md) with metadata normalization for `ROUTES_TO`, `TESTS`, `INJECTS`, `MODELS`, `CONFIGURES`.
- **TypeScript / JS (LANG-01, LANG-02, LANG-03):**
  - Implement `tsconfig.json` path mapping & monorepo workspace resolver.
  - Implement Express, NestJS, and Next.js route extractors (`ROUTES_TO`).
  - Implement Jest/Vitest test-to-implementation linker (`TESTS`).
- **Python (LANG-04, LANG-05, LANG-06):**
  - Implement FastAPI, Flask, and Django route-to-handler extractors (`ROUTES_TO`).
  - Implement Pytest test-to-implementation linker (`TESTS`) and fixture injection (`INJECTS`).
  - Implement SQLAlchemy and Django model relational schema extraction (`MODELS`).
- **Test Parity (LANG-11):** Eliminate zero-test coverage for TypeScript, Java, and Scala in `tests/`.

### Milestone 2 (v2.2.0): Priority 2 Deep Support (JVM & Go)
- **Java / Kotlin (LANG-07, LANG-08):**
  - Implement Spring Boot & Jakarta Dependency Injection resolver (`INJECTS`).
  - Implement Spring MVC & JAX-RS route extraction (`ROUTES_TO`).
  - Implement JUnit 5 test linking (`TESTS`) and JPA entity modeling (`MODELS`).
- **Go (LANG-09, LANG-10):**
  - Implement anonymous struct embedding as `INHERITS` (subtype: "EMBEDS").
  - Implement implicit interface satisfaction detection (`IMPLEMENTS`).
  - Implement Gin/Chi web routing (`ROUTES_TO`) and `*_test.go` table-driven test linking (`TESTS`).

### Milestone 3 (v2.3.0): Ecosystem Hardening & Tier 3 Parity
- Add Rust Cargo workspace multi-crate resolution and `#[test]` linking.
- Add C# ASP.NET Core controller routing and Entity Framework Core relationship extraction.
- Add PHP Laravel/Symfony route and Eloquent model extraction.

---

## 7. Verification & Tooling

To ensure scorecard metrics and language health remain auditable:
- Run the scorecard generator:
  ```bash
  python scripts/generate_language_scorecard.py
  python scripts/generate_language_scorecard.py --json
  python scripts/generate_language_scorecard.py --detail
  ```
- Automated regression tests:
  ```bash
  python -m pytest tests/test_language_scorecard.py
  ```
- Documentation consistency:
  All edge schema additions must pass [`tests/test_doc_consistency.py`](file:///e:/Github/repo2graph/tests/test_doc_consistency.py) and [`tests/test_output_schema.py`](file:///e:/Github/repo2graph/tests/test_output_schema.py).

