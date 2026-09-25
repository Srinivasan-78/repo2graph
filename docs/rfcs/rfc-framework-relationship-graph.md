# RFC: Ecosystem Relationship Graph Expansion (Frameworks, Tests, Routes, and Models)

**Status:** Proposed  
**Scope:** `repo2graph.graph`, `repo2graph.parse`, `repo2graph.edgemeta`, `repo2graph.query`  
**Date:** 2026-09-26  
**Target Version:** 2.1.0  

---

## 1. Executive Summary

Today, `repo2graph` creates six edge types: `CONTAINS`, `DEFINES`, `IMPORTS`, `CALLS`, `CALLS_EXTERNAL`, `INHERITS`, and `CO_CHANGE`. These edges describe pure lexical and syntactic AST relationships. However, modern production codebases are structured around **frameworks and architectural conventions**:
- Web request handlers are bound to URL routes via decorators or route tables (`@app.get("/users")`, `router.get()`, `@GetMapping`), not direct function calls.
- Inversion of Control (IoC) and Dependency Injection (DI) containers (@Autowired, NestJS `@Inject()`, FastAPI `Depends()`) decouple callers from concrete implementations, turning direct calls into abstract interface citations or unresolved external misses.
- Tests exercise implementations through runners (pytest, Jest, Vitest, JUnit, Go test) using naming conventions and imports rather than callers.
- ORM entities (Prisma, SQLAlchemy, Django models, Hibernate) declare relational schemas (foreign keys, one-to-many) that define data flow across boundaries.

This RFC defines five new first-class ecosystem edge types that preserve `repo2graph`'s zero-dependency, static-only guarantee while unlocking deep semantic GraphRAG:

1. `ROUTES_TO`: HTTP route / controller endpoint to request handler.
2. `TESTS`: Test symbol / test file to target implementation symbol / module.
3. `INJECTS`: Dependency injection provider / bean to injection consumer.
4. `MODELS`: ORM model entity to related target model entity (relational schema).
5. `CONFIGURES`: Configuration key / environment reference to consuming symbol.

All new edges strictly conform to `AGENTS.md` and `edgemeta.py` rules: every edge carries `method`, `confidence`, and `evidence`, normalized at `Graph.add_edge`.

---

## 2. Motivation & Problem Statement

### 2.1 The Limits of Pure Name-Based Call Matching

In a modern web service (e.g. FastAPI, Express, Spring Boot), entrypoints are rarely called by internal code:
```python
# app.py
@app.get("/users/{user_id}", response_model=UserResponse)
def get_user_profile(user_id: int):
    return user_service.fetch(user_id)
```
In `repo2graph` today:
- `get_user_profile` is identified as an entrypoint root (`reach` calculated via BFS).
- However, the relationship between the route `/users/{user_id}` and `get_user_profile` is **not indexed as a queryable edge**.
- When an agent or developer asks: *"Which endpoint handles user profile retrieval?"*, lexical BM25 must guess based on identifiers. There is no `ROUTES_TO` edge connecting an endpoint node to `get_user_profile`.

### 2.2 The Dependency Injection Disconnect

In Spring Boot, NestJS, or FastAPI:
```java
@RestController
public class OrderController {
    @Autowired
    private PaymentGateway paymentGateway; // Interface
}
```
- The AST call site invokes `PaymentGateway.process()`.
- Name resolution lands on the interface `PaymentGateway`, or fans out across 5 concrete implementations (`StripeGateway`, `MockGateway`, `PaypalGateway`) at `1/5 = 0.20` confidence.
- The actual injected component (e.g. `@Component StripeGateway`) has an `INHERITS` edge to the interface, but no edge links the injection site to the implementation.

### 2.3 The Test-to-Implementation Gap

Agents frequently need to answer: *"Which tests verify the authentication flow?"* or *"What code does `test_billing.py` cover?"*
- Today, tests are parsed as standard functions (`def test_login()`).
- Because tests call helper functions and mocks, their call graph often misses the production targets.
- A synthetic `TESTS` edge mapping `sym:test_auth.py::test_login` -> `sym:auth.py::login_user` directly connects test fixtures to production code.

---

## 3. Schema & Normalization Design

In compliance with `AGENTS.md` rule:
> **Every edge carries `method`, `confidence` and `evidence` — normalise at the chokepoint.**  
> `Graph.add_edge` is the single chokepoint and runs `edgemeta.normalize`, so a new edge type cannot ship without the standard fields. Add per-type metadata at the call site; never bypass `add_edge`.

### 3.1 Edge Type Definitions

| Edge Type | Source (`src`) | Destination (`dst`) | Description | Allowed Methods | Default Confidence |
|---|---|---|---|---|---|
| `ROUTES_TO` | `route:<method>:<path>` or `file:<rel>` | `sym:<rel>::<qualname>` | HTTP route specification mapped to handler | `framework_route_extractor` | 1.0 (exact route match) |
| `TESTS` | `sym:<rel>::<test_sym>` or `file:<test_rel>` | `sym:<rel>::<impl_sym>` or `file:<impl_rel>` | Test unit verifying an implementation target | `test_target_heuristic`, `test_ast_runner` | 0.95 (name-aligned), 0.70 (import-inferred) |
| `INJECTS` | `sym:<rel>::<provider_class>` | `sym:<rel>::<target_class>` or `sym:<rel>::<field>` | DI provider bound into consumer | `framework_di_resolver` | 0.90 (single candidate), `1/n` (multi-candidate) |
| `MODELS` | `sym:<rel>::<entity_class>` | `sym:<rel>::<target_entity>` | Entity relationship (foreign key / relation) | `orm_schema_parser` | 1.0 (explicit FK), 0.85 (inferred convention) |
| `CONFIGURES` | `config:<key>` or `file:<config_rel>` | `sym:<rel>::<qualname>` | Config variable consumed by code | `config_reference_extractor` | 0.95 |

### 3.2 Metadata Schema Updates (`repo2graph/edgemeta.py`)

New method constants to add:
```python
METHOD_FRAMEWORK_ROUTE = "framework_route_extractor"
METHOD_TEST_HEURISTIC = "test_target_heuristic"
METHOD_FRAMEWORK_DI = "framework_di_resolver"
METHOD_ORM_SCHEMA = "orm_schema_parser"
METHOD_CONFIG_REF = "config_reference_extractor"
```

Edge normalization rules:
- `ROUTES_TO`:
  - Required attrs: `route_path: str`, `http_method: str` (e.g. `GET`, `POST`), `framework: str` (e.g. `fastapi`, `express`, `spring`).
  - Evidence: `f"{rel}:{decorator_line}"`.
- `TESTS`:
  - Required attrs: `test_framework: str` (e.g. `pytest`, `jest`, `junit`, `gotest`), `match_strategy: str` (`exact_name`, `same_module`, `import_call`).
  - Evidence: Line of the test declaration.
- `INJECTS`:
  - Required attrs: `di_container: str` (e.g. `spring`, `nestjs`, `fastapi`), `interface: str | None`.
  - Evidence: Line of `@Autowired` / `@Inject` / constructor param.
- `MODELS`:
  - Required attrs: `orm: str` (e.g. `prisma`, `sqlalchemy`, `django`, `jpa`), `relation_type: str` (`one_to_many`, `many_to_one`, `many_to_many`).
  - Evidence: Line of relationship declaration or schema file.

---

## 4. GraphRAG Traversal & Budget Model Impact

### 4.1 Direction Filtering (`ALL_EDGE_DIRS` vs `DEFAULT_EDGE_DIRS`)

`AGENTS.md` mandates:
> **A new default on a shared traversal helper narrows its existing callers.**  
> When you add a filtering parameter to a helper that already has callers, the safe default for the helper is not the safe default for the callers.  
> Every pre-existing caller must opt out **by name**, not by omission.

In `repo2graph/query.py`:
- `DEFAULT_EDGE_DIRS` currently controls packing traversal:
  ```python
  DEFAULT_EDGE_DIRS: dict[str, tuple[str, ...]] = {
      "DEFINES": ("in",),
      "IMPORTS": ("out",),
      "INHERITS": ("out",),
      "CALLS": ("out", "in"),
  }
  ```
- **Updates for new edge types:**
  ```python
  DEFAULT_EDGE_DIRS.update({
      "ROUTES_TO": ("out",),      # Route node expands into handler
      "TESTS": ("out", "in"),     # Test expands into code; code expands into its tests
      "INJECTS": ("out", "in"),   # Service expands into dependencies and consumers
      "MODELS": ("out", "in"),    # Model entity expands to related entities
      "CONFIGURES": ("out",),     # Config expands into consuming code
  })
  ```
- `ALL_EDGE_DIRS` remains `{}` (unrestricted), preserving 100% backward compatibility for `Index.retrieve()` and any callers opting out by name.

### 4.2 Budget Allocation

In `Index.pack_context()`, new edges do not change the total markdown budget; rather, when a developer queries *"Show me the checkout payment flow and its tests"*, the expander traverses `CALLS` + `TESTS` + `ROUTES_TO`, returning both endpoint definitions, services, and matching test cases within the requested `budget_chars`.

---

## 5. Implementation Phasing

1. **Phase 1 (Edge Infrastructure & Normalization):**
   - Register new methods in `edgemeta.py`.
   - Update `Graph.add_edge` schema validation and `stats.json` counters.
   - Update `docs/OUTPUT_SCHEMA.md` and `docs/reference.md` in lockstep (verified by `test_doc_consistency.py`).
2. **Phase 2 (Test-to-Implementation Linking):**
   - Implement language-agnostic test linking engine in `repo2graph/tests_link.py`.
   - Heuristics: `test_<name>()` -> `<name>()`, `<Name>Test` -> `<Name>`, file pairing (`test_foo.py` -> `foo.py`, `foo_test.go` -> `foo.go`).
3. **Phase 3 (Framework Route & Controller Extraction):**
   - Implement AST pattern matching for priority frameworks:
     - TypeScript: Express, NestJS, Next.js.
     - Python: FastAPI, Flask, Django.
     - Java/Kotlin: Spring MVC, JAX-RS.
     - Go: Gin, Chi, `net/http`.
4. **Phase 4 (Dependency Injection & ORMs):**
   - Spring `@Autowired` / NestJS `@Inject()` resolution.
   - Prisma / SQLAlchemy / Django model schema extraction.

