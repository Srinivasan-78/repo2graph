# RFC: Deep Python Language Support (Priority 1)

**Status:** Proposed  
**Scope:** `repo2graph.parse`, `repo2graph.graph`, Python ecosystem  
**Priority:** 1 (Highest)  
**Date:** 2026-09-26  
**Target Version:** 2.1.0  

---

## 1. Strategic Rationale

Python is both `repo2graph`'s native implementation runtime and the primary language for AI/ML engineering, data infrastructure, and modern backend web APIs (FastAPI, Django, Flask).
- **Parser Reliability:** In the benchmark corpus (`docs/limitations.md`), Django (5,629 files) parsed with **only 2 files with errors (0.04%)**, demonstrating that Python's tree-sitter grammar is among the cleanest and most stable in existence.
- **Syntactic Maturity:** Python already possesses sophisticated decorator capture, docstring extraction, relative import calculation, and dynamic callee tracking (`getattr`, `eval`, `exec`).
- **Semantic Deficit:** Despite strong AST parsing, `repo2graph` treats Python web frameworks as disconnected functions. A FastAPI endpoint decorated with `@router.get("/v1/items")` has an AST decorator call, but no queryable HTTP endpoint edge. A pytest suite testing an authentication service has no explicit test-to-implementation linkage.

Deep Python support will transform `repo2graph` into the premier knowledge graph engine for Python AI and web stacks.

---

## 2. Technical Specification

### 2.1 Web Route & Controller Extraction (`ROUTES_TO` Edges)

#### Target Frameworks
- **FastAPI / Starlette**, **Flask / Blueprints**, **Django URLs**.

#### 1. FastAPI / Starlette
```python
router = APIRouter(prefix="/orders")


@router.post("/{order_id}/refund", response_model=RefundResponse)
async def process_refund(order_id: str, db: Session = Depends(get_db)): ...
```
- **AST Pattern:**
  - Decorator node on `function_definition`: `@router.post(...)`, `@app.get(...)`.
  - Extract HTTP method (`post`) and route subpath (`/{order_id}/refund`).
  - Account for router prefix: Scan module for `APIRouter(prefix="/orders")` or track router inclusion (`app.include_router(router, prefix="/api")`).
- **Graph Output:**
  - Node: `route:POST:/orders/{order_id}/refund`
  - Edge: `route:POST:/orders/{order_id}/refund` --`ROUTES_TO`--> `sym:orders.py::process_refund`
  - `method: "framework_route_extractor"`
  - `evidence: f"{path}:{decorator_line}"`

#### 2. Flask & Blueprints
```python
@bp.route("/login", methods=["POST"])
def handle_login(): ...
```
- Decorator `@<bp>.route(..., methods=[...])`. Default to `GET` if `methods` omitted.

#### 3. Django URL Configuration
```python
# urls.py
urlpatterns = [
    path("articles/<int:year>/", views.year_archive, name="archive"),
    path("authors/", AuthorListView.as_view(), name="authors"),
]
```
- Tree-sitter query: `call` to `path(...)` or `re_path(...)`.
  - First arg: URL pattern string (`"articles/<int:year>/"`).
  - Second arg: view function (`views.year_archive`) or class view (`AuthorListView.as_view()`).
- Map route directly to view symbol (`sym:views.py::year_archive` or `sym:views.py::AuthorListView`).

### 2.2 Pytest & Unittest Test Linking (`TESTS` Edges)

#### Target Frameworks
- **pytest**, **unittest**.

#### Extraction Mechanics
1. **File-Level Pairing:**
   - Detect test files: `tests/**/test_*.py`, `test_*.py`, `*_test.py`.
   - Strip `test_` prefix and match against codebase modules:
     - `tests/test_auth.py` -> `repo2graph/auth.py`
     - Emit: `file:tests/test_auth.py` --`TESTS`--> `file:repo2graph/auth.py` (Confidence: 0.90).
2. **Function-Level Matching:**
   - Rule 1: `def test_<func_name>():`
     - If `<func_name>` is an indexed function in imported modules, link:
       `sym:tests/test_auth.py::test_login` --`TESTS`--> `sym:repo2graph/auth.py::login` (Confidence: 0.95).
   - Rule 2: `class Test<ClassName>:`
     - Link test class to implementation class:
       `sym:tests/test_parser.py::TestPythonParser` --`TESTS`--> `sym:repo2graph/parse.py::PythonParser`.
3. **Fixture Dependency Tracking (`INJECTS`):**
   - In pytest, fixture injection is parameter-based:
     ```python
     @pytest.fixture
     def db_client(): ...


     def test_query(db_client): ...
     ```
   - When a test function parameter matches a `@pytest.fixture` in `conftest.py` or the test file, emit `sym:conftest.py::db_client` --`INJECTS`--> `sym:test_file.py::test_query`.

### 2.3 Dependency Injection in Python (`INJECTS` Edges)

#### Target Pattern: FastAPI `Depends()`
```python
async def get_current_user(token: str = Depends(oauth2_scheme)) -> User: ...


@router.get("/me")
async def read_current_user(current_user: User = Depends(get_current_user)):
    return current_user
```
- In function parameters: detect default values of `Depends(dependency_target)`.
- Emit:
  `sym:dependencies.py::get_current_user` --`INJECTS`--> `sym:routes.py::read_current_user`
  - `method: "framework_di_resolver"`
  - `evidence: line of parameter definition`

### 2.4 ORM & Data Model Schema Extraction (`MODELS` Edges)

#### 1. SQLAlchemy
```python
class Department(Base):
    __tablename__ = "departments"
    id = Column(Integer, primary_key=True)
    employees = relationship("Employee", back_populates="department")


class Employee(Base):
    __tablename__ = "employees"
    id = Column(Integer, primary_key=True)
    department_id = Column(Integer, ForeignKey("departments.id"))
    department = relationship("Department", back_populates="employees")
```
- Tree-sitter query: In `class_definition`, look for assignments where value is a call to `relationship(...)` or `ForeignKey(...)`.
- Emit bidirectional model relationship:
  - `sym:models.py::Department` --`MODELS`--> `sym:models.py::Employee` (`relation: "one_to_many"`)
  - `sym:models.py::Employee` --`MODELS`--> `sym:models.py::Department` (`relation: "many_to_one"`)

#### 2. Django Models
```python
class Order(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    products = models.ManyToManyField(Product)
```
- Look for `models.ForeignKey`, `models.OneToOneField`, `models.ManyToManyField`.
- Map directly to target model class.

#### 3. Pydantic Schemas
- Detect nested schema field types (`items: list[ItemSchema]`) to link data contracts.

### 2.5 Configuration References (`CONFIGURES` Edges)

- **Pydantic `BaseSettings`**:
  ```python
  class AppConfig(BaseSettings):
      database_url: str = Field(..., alias="DATABASE_URL")
  ```
  Link `config:DATABASE_URL` -> `sym:config.py::AppConfig.database_url`.
- **Environment Lookups**:
  - `os.environ.get("PORT")` or `os.getenv("SECRET_KEY")`.
  - Extract configuration keys and link `config:<KEY>` to calling function.

---

## 3. Test & Verification Plan

1. **Test Fixtures:**
   - `tests/test_python_routes.py`: Verify FastAPI `@app.get` and `@router.post` extraction.
   - `tests/test_python_test_links.py`: Verify pytest function and class matching.
   - `tests/test_python_di.py`: Verify FastAPI `Depends()` resolution.
   - `tests/test_python_orm.py`: Verify SQLAlchemy and Django model foreign key links.
2. **Regression Guarantees:**
   - Confirm that Django benchmark example builds without error and produces >500 `ROUTES_TO` and `MODELS` edges.

