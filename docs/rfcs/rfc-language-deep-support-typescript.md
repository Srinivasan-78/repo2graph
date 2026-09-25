# RFC: Deep TypeScript / JavaScript Language Support (Priority 1)

**Status:** Proposed  
**Scope:** `repo2graph.parse`, `repo2graph.graph`, TypeScript/JavaScript ecosystem  
**Priority:** 1 (Highest)  
**Date:** 2026-09-26  
**Target Version:** 2.1.0  

---

## 1. Strategic Rationale

TypeScript and JavaScript form the foundation of modern web, frontend, and full-stack cloud applications. In the `repo2graph` benchmark suite (`docs/limitations.md`), the VS Code repository (`src/vs/`, 6,000 files, 450k `CALLS` edges) showed the highest raw ambiguity and symbol reuse in the corpus:
- **18.2% of calls remain ambiguous** even after scoped resolution tiers 0–5.
- Common method names (`dispose()`, `getId()`, `register()`, `init()`) appear across hundreds of decoupled classes and interfaces.
- Modern enterprise TypeScript repositories rely heavily on path aliases (`@core/services/auth`), monorepo packages (`@org/shared-utils`), and bundler conventions that defeat simple relative path imports.

By elevating TypeScript and JavaScript to **Deep Support (Tier 1)**, `repo2graph` unlocks comprehensive architectural indexing for Node.js, Next.js, Express, NestJS, and frontend frameworks.

---

## 2. Capability Audit & Identified Gaps

| Capability | Current State | Target State (Deep Support) | Gap Severity |
|---|---|---|---|
| **Tree-sitter Grammar** | Mature `javascript`, `typescript`, `tsx` grammars | Unchanged (<0.1% parse error rate) | Low |
| **Symbol Extraction** | Functions, classes, methods, interfaces, types, enums, arrow functions (`maybe_function`) | Add namespace, export declaration bindings, JSX functional components | Medium |
| **Import Resolution** | Relative paths (`./helper.js` -> `./helper.ts`) | `tsconfig.json` `paths`/`baseUrl`, monorepo workspaces, package.json `exports`, barrel files | **High (P0)** |
| **Call Resolution** | 5-tier scoped resolution, dynamic `call`/`apply`/`eval`, decorator capture | Enhanced constructor injection resolution, chaining attribution | Medium |
| **Test Linking** | None | Jest/Vitest/Mocha `*.test.ts`, `*.spec.ts`, `describe()` -> implementation symbol | **High (P0)** |
| **Route Extraction** | None | Express, Fastify, NestJS controllers, Next.js App/Pages routers -> `ROUTES_TO` | **High (P0)** |
| **Dependency Injection** | Annotations captured as calls | NestJS `@Injectable()`, `@Inject()`, Angular DI provider binding -> `INJECTS` | **High (P1)** |
| **ORM / Data Models** | Classes extracted as standard symbols | Prisma `schema.prisma` models, TypeORM `@Entity` relations -> `MODELS` | **Medium (P2)** |

---

## 3. Technical Specification

### 3.1 Module & Import Path Aliasing (`tsconfig.json` & Monorepos)

#### Problem
In modern TS codebases:
```typescript
import { AuthService } from "@app/core/auth";
import { formatCurrency } from "@shared/utils";
```
`resolve_import()` checks only relative `./` and `src/` fallbacks. As a result, `@app/core/auth` fails resolution and is tagged as `module:@app/core/auth` (unresolved external), losing the internal graph connection.

#### Solution: `tsconfig.json` Reader in `repo_context()`
Extend `graph.repo_context(root)` to parse root and package-level `tsconfig.json` (and `jsconfig.json`):
1. Extract `compilerOptions.baseUrl` (defaults to root or tsconfig location).
2. Extract `compilerOptions.paths`:
   ```json
   {
     "compilerOptions": {
       "baseUrl": ".",
       "paths": {
         "@app/*": ["src/app/*"],
         "@shared/*": ["packages/shared/src/*"]
       }
     }
   }
   ```
3. Map wildcard and prefix path specs to target directory trees during `resolve_import()`.
4. Support Monorepo Workspaces: Parse root `pnpm-workspace.yaml`, `lerna.json`, or root `package.json` `"workspaces"` field to map internal package names (`@org/pkg-a` -> `packages/pkg-a`).

### 3.2 Barrel File & Re-export Flattening

#### Problem
```typescript
// src/components/index.ts
export * from "./Button";
export { Card } from "./Card";
```
When code imports `import { Button } from "@/components"`, the `IMPORTS` edge terminates at `index.ts`. If `index.ts` is just a pass-through barrel, the direct caller-to-definition chain is obscured.

#### Solution
When an imported file contains re-export AST nodes (`export_statement` with `from` clause), record export re-mappings in `ParsedFile.import_details` so `resolve_import()` can draw both the file-level import to `index.ts` and the symbol-level `DEFINES`/`CALLS` resolution to the real source file `Button.tsx`.

### 3.3 Test-to-Implementation Graph (`TESTS` Edges)

#### Target Frameworks
- **Jest**, **Vitest**, **Mocha**, **Playwright**, **Cypress**.

#### Extraction Engine
1. **File Convention Matching:**
   - Detect files matching `*.test.ts`, `*.test.tsx`, `*.spec.ts`, `*.spec.js`, or within `__tests__/`.
   - Target heuristic: Strip `.test.` / `.spec.` and check for corresponding implementation:
     - `src/services/auth.service.test.ts` -> `src/services/auth.service.ts` (Confidence: 0.95).
2. **AST-Level Symbol Linking:**
   - Match `describe("AuthService", () => { ... })`:
     - If `AuthService` matches an imported class symbol, emit:
       `sym:auth.service.test.ts::describe_AuthService` --`TESTS`--> `sym:auth.service.ts::AuthService`.
   - Match test cases `it("validates password", () => { authService.validatePassword(...) })`:
     - Emit edge from test block to `AuthService.validatePassword`.

### 3.4 Web Routing & Controller Extraction (`ROUTES_TO` Edges)

#### Target Frameworks
1. **Express / Fastify:**
   ```typescript
   app.get("/api/v1/orders/:id", authenticate, getOrderHandler);
   router.post("/checkout", processCheckout);
   ```
   - Tree-sitter query: `call_expression` where function is `(member_expression object: (identifier) property: (property_identifier) @method)` and `@method` is one of `get`, `post`, `put`, `delete`, `patch`.
   - Emit:
     - Route node: `route:GET:/api/v1/orders/:id`
     - Edge: `route:GET:/api/v1/orders/:id` --`ROUTES_TO`--> `sym:orders.controller.ts::getOrderHandler`
     - Evidence: line of `app.get(...)` call.
2. **NestJS Controllers:**
   ```typescript
   @Controller("payments")
   export class PaymentController {
       @Post("charge")
       chargeCard(@Body() dto: ChargeDto) { ... }
   }
   ```
   - Combine class decorator `@Controller("payments")` with method decorator `@Post("charge")` to resolve route `POST /payments/charge`.
   - Emit:
     - `route:POST:/payments/charge` --`ROUTES_TO`--> `sym:payment.controller.ts::PaymentController.chargeCard`.
3. **Next.js App Router & Pages Router:**
   - File-based convention:
     - `app/api/auth/[...nextauth]/route.ts` -> functions `GET`, `POST`.
     - Emit route nodes automatically from file path: `route:GET:/api/auth/*` -> `sym:app/api/auth/[...nextauth]/route.ts::GET`.

### 3.5 Dependency Injection Extraction (`INJECTS` Edges)

#### Target Frameworks
- **NestJS**, **Angular**, **InversifyJS**, **TSyringe**.

#### Pattern
```typescript
@Injectable()
export class OrderService {
    constructor(
        private readonly paymentService: PaymentService,
        @Inject("NOTIFICATION_CLIENT") private readonly notifier: ClientProxy
    ) {}
}
```
- In TypeScript AST, constructor parameters with type annotations (`type_annotation`) indicate injected dependencies.
- Emit:
  `sym:order.service.ts::OrderService` --`INJECTS`--> `sym:payment.service.ts::PaymentService`
  - `method: "framework_di_resolver"`
  - `evidence: line of constructor parameter`
  - `confidence: 1.0` if type resolves to single class in repo.

### 3.6 ORM & Relational Schema Modeling (`MODELS` Edges)

#### Target Frameworks
- **Prisma** (`schema.prisma`) and **TypeORM** (`@Entity()`).

#### Prisma Parser
1. Parse `.prisma` files (declarative schema):
   ```prisma
   model User {
     id    Int     @id @default(autoincrement())
     posts Post[]
   }

   model Post {
     id       Int  @id @default(autoincrement())
     author   User @relation(fields: [authorId], references: [id])
     authorId Int
   }
   ```
2. Emit model symbols and `MODELS` relation edges:
   - `sym:schema.prisma::User` --`MODELS`--> `sym:schema.prisma::Post` (`relation_type: "one_to_many"`)
   - `sym:schema.prisma::Post` --`MODELS`--> `sym:schema.prisma::User` (`relation_type: "many_to_one"`)

---

## 4. Test Strategy & Verification

To eliminate the current test deficit (0 tests for TS, 1 for TSX):
1. **Unit Fixtures:**
   - `test_typescript_tsconfig_paths.py`: Test wildcard `@/*`, scoped `@org/*`, and nested baseUrl resolution.
   - `test_typescript_routes.py`: Test Express, NestJS, and Next.js route extractors.
   - `test_typescript_di.py`: Test NestJS constructor injection.
   - `test_typescript_tests_link.py`: Test Jest and Vitest `describe()`/`it()` mapping.
2. **Benchmark Verification:**
   - Measure against VS Code example: verify that ambiguous call rate does not regress and tsconfig path resolution increases resolved internal imports by >15%.

