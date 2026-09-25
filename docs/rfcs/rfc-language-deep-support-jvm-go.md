# RFC: Strategic Tier 2 Language Support — JVM (Java/Kotlin) vs. Go

**Status:** Proposed  
**Scope:** `repo2graph.parse`, `repo2graph.graph`, JVM & Go ecosystems  
**Priority:** 2 (Follow-up to TypeScript and Python)  
**Date:** 2026-09-26  
**Target Version:** 2.2.0  

---

## 1. Executive Summary & Strategic Decision

Following the elevation of TypeScript/JavaScript and Python to Priority 1 (Tier 1), `repo2graph` must allocate its next deep engineering investment between:
1. **The JVM Ecosystem (Java & Kotlin)**: The backbone of enterprise backend systems (Spring Boot, Jakarta EE, Android Jetpack, Micronaut).
2. **The Go Ecosystem**: The backbone of cloud-native infrastructure, networking, and microservices (Kubernetes, Docker, Terraform, Prometheus).

### Strategic Recommendation
**We recommend prioritizing Java and Kotlin as the primary Tier 2 focus, with Go scheduled as immediate Tier 2B.**

#### The Deciding Factor: Semantic Failure Modes
- In **Go**, code is structurally straightforward. Interfaces are explicit in methods, packages are flat, and `go.mod` is already parsed for module-relative imports. Today, querying a Go codebase in `repo2graph` yields acceptable package-level and call-level results.
- In **Enterprise Java / Kotlin**, code is heavily inverted through frameworks. A Spring Boot application is almost entirely wired via runtime Dependency Injection (`@Autowired`, `@Inject`, `@Service`, `@Repository`), Spring MVC routing annotations (`@RestController`, `@GetMapping`), and JPA entity relationships (`@Entity`, `@ManyToOne`). In `repo2graph` today, **these codebases degrade severely**: call sites resolve only to abstract interface nodes, routes are completely invisible, and tests have no linkage to production classes.

Solving the JVM framework semantic gap provides disproportionately higher value for enterprise GraphRAG deployments.

---

## 2. Head-to-Head Comparison

| Metric / Dimension | Java / Kotlin (JVM) | Go (Golang) | Strategic Implication |
|---|---|---|---|
| **Benchmark Parse Stability** | Highly stable ASTs; negligible syntax error rate | **2.8% parse error rate** in Kubernetes (1,084 files) | Both grammars are production-ready. |
| **Current Symbol Kinds** | `method`, `class`, `interface`, `enum`, `object` | `function`, `method`, `type` | Go lacks OOP constructs; Java already models classes/interfaces. |
| **Inheritance Support** | Full `extends` and `implements` | **None** (Go uses struct embedding & interfaces) | Go needs new AST logic for struct embedding. |
| **Import & Package Depth** | `import a.b.C` mapped to directory trees | `go.mod` parsed for module path, `*_test.go` filtered | Go has dedicated manifest parsing; Java needs Maven/Gradle awareness. |
| **Framework Gap** | **Severe**: Spring `@Autowired` turns calls into abstract misses | **Moderate**: Manual constructor wiring is common | Java is paralyzed without DI awareness; Go works with basic call graphs. |
| **Enterprise Value** | Massive: Fortune 500 backends, banking, healthcare | High: DevOps, SRE, cloud infrastructure | Java opens major enterprise code intelligence opportunities. |

---

## 3. Deep Java & Kotlin Specification (Tier 2A)

### 3.1 Spring & Jakarta Dependency Injection (`INJECTS` Edges)
In Spring Boot / Micronaut / Android:
```java
@Service
public class OrderService {
    @Autowired
    private PaymentClient paymentClient; // Interface

    private final InventoryRepository inventoryRepo;
    public OrderService(InventoryRepository repo) { // Constructor injection
        this.inventoryRepo = repo;
    }
}
```
#### Resolution Logic:
1. Identify fields annotated with `@Autowired`, `@Inject`, `@Resource`, or constructor parameters in classes annotated with `@Service`, `@Component`, `@Repository`, or `@RestController`.
2. Inspect target type:
   - If target type is a concrete class with `@Component`/`@Service`, emit direct edge:
     `sym:OrderService.java::OrderService` --`INJECTS`--> `sym:PaymentClient.java::PaymentClient`
   - If target type is an `interface`, scan implementations (`INHERITS` with `subtype: "IMPLEMENTS"`):
     - If exactly one in-repo implementation exists (e.g. `StripePaymentClient`), bind injection to implementation:
       `sym:OrderService.java::OrderService` --`INJECTS`--> `sym:StripePaymentClient.java::StripePaymentClient` (`confidence: 0.95`).
     - If multiple implementations exist, fan out at `1/n` confidence with `ambiguous: true`.

### 3.2 Spring MVC & JAX-RS Routing (`ROUTES_TO` Edges)
```java
@RestController
@RequestMapping("/api/v1/customers")
public class CustomerController {

    @GetMapping("/{id}")
    public CustomerDto getCustomer(@PathVariable String id) { ... }

    @PostMapping
    public ResponseEntity<Void> createCustomer(@Valid @RequestBody CreateCustomerRequest req) { ... }
}
```
- Combine class-level `@RequestMapping` with method-level `@GetMapping`, `@PostMapping`, `@PutMapping`, `@DeleteMapping`, or JAX-RS `@Path`, `@GET`.
- Emit route nodes:
  - `route:GET:/api/v1/customers/{id}` --`ROUTES_TO`--> `sym:CustomerController.java::CustomerController.getCustomer`
  - `route:POST:/api/v1/customers` --`ROUTES_TO`--> `sym:CustomerController.java::CustomerController.createCustomer`

### 3.3 JUnit 5 & Kotlin Test Linking (`TESTS` Edges)
- Match `@Test`, `@ParameterizedTest`, or Kotlin `@Test` methods.
- Heuristic: `CustomerServiceTest` -> `CustomerService`.
- Function match: `testCreateCustomer_Success()` -> `CustomerService.createCustomer()`.

### 3.4 JPA / Hibernate Relational Modeling (`MODELS` Edges)
```java
@Entity
@Table(name = "orders")
public class Order {
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "customer_id")
    private Customer customer;
}
```
- Parse annotations `@ManyToOne`, `@OneToMany`, `@OneToOne`, `@ManyToMany`.
- Emit bidirectional `MODELS` edge linking `Order` entity to `Customer` entity with `relation_type`.

### 3.5 Maven & Gradle Multi-Module Resolution
- Parse root `pom.xml` (`<modules><module>core</module></modules>`) and `settings.gradle` / `settings.gradle.kts` (`include 'core', 'web'`).
- Map Java package namespaces to subproject directory roots.

---

## 4. Deep Go Specification (Tier 2B)

### 4.1 Struct Embedding as Inheritance (`INHERITS` with `subtype: "EMBEDS"`)
In Go, reuse is achieved through composition/embedding:
```go
type BaseController struct {
    logger *zap.Logger
}

type UserController struct {
    BaseController
    db *sql.DB
}
```
- **AST Pattern:** In `type_spec` -> `struct_type` -> `field_declaration_list`, detect anonymous fields (no field identifier, only type identifier).
- **Graph Output:**
  `sym:user.go::UserController` --`INHERITS`--> `sym:base.go::BaseController` (`subtype: "EMBEDS"`, `confidence: 1.0`).

### 4.2 Implicit Interface Satisfaction
```go
type Reader interface {
    Read(p []byte) (n int, err error)
}

type FileReader struct { ... }
func (f *FileReader) Read(p []byte) (n int, err error) { ... }
```
- During graph build post-processing, compare struct method signatures against interface method declarations.
- If a struct implements all methods of an in-repo interface, emit:
  `sym:file_reader.go::FileReader` --`INHERITS`--> `sym:reader.go::Reader` (`subtype: "IMPLEMENTS"`, `confidence: 0.90`).

### 4.3 Web Routing (Gin, Chi, Echo, `net/http`)
```go
r := gin.Default()
r.GET("/healthz", HealthCheck)
r.POST("/api/users", controller.CreateUser)
```
- Match `call_expression` on router variables with methods `GET`, `POST`, `PUT`, `DELETE`.
- Emit route nodes: `route:GET:/healthz` --`ROUTES_TO`--> `sym:health.go::HealthCheck`.

### 4.4 Table-Driven Test Linking
- Scan `*_test.go` files for functions starting with `TestXxx(t *testing.T)`.
- Link `TestCreateUser` -> `CreateUser` in the same package with `TESTS` edge.

---

## 5. Summary & Rollout Roadmap

1. **Sprint 1 (JVM Core):** Spring DI resolver + Spring MVC `@GetMapping` route extraction + JUnit test linking.
2. **Sprint 2 (JVM ORM & Multi-Module):** JPA entity modeling + Maven/Gradle submodule resolution.
3. **Sprint 3 (Go Struct Embedding & Interfaces):** Anonymous struct embedding extraction + interface satisfaction solver.
4. **Sprint 4 (Go Web & Testing):** Gin/Chi router extraction + `*_test.go` table-driven test linking.

