# Starter tasks

Seven tasks that are small, self-contained, and have a testable outcome. Each names the file and
line to start from, what "done" means, and the specific thing that makes it trickier than it looks
— because every one of these has one.

**Before you start anything here**, read [AGENTS.md](../AGENTS.md). It records the conventions that
are not guessable from the code: why `splitlines()` is banned, why git subprocess output is never
decoded with `text=True`, why `query.py` has two different meanings of `budget_chars`, and why a
test must pin a literal value rather than recompute it. Several of the tasks below sit directly on
top of one of those.

Setup, test commands and the PR flow: **[.github/CONTRIBUTING.md](../.github/CONTRIBUTING.md)**.

---

## 1. Single-character identifiers are unsearchable — [#378](https://github.com/Srinivasan-78/repo2graph/issues/378)

**Size:** XS · **Area:** `area/query` · **Type:** bug

`repo2graph/query.py:54`:

```python
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")
```

The `+` requires two or more characters, so a symbol named `f`, `x` or `T` is never indexed and can
never be matched by a query. Generic type parameters (`T`, `K`, `V`) and short helpers vanish from
lexical search entirely.

**Acceptance criteria**

1. `TOKEN_RE` matches single-character identifiers.
2. A test in `tests/test_repo2graph.py` builds a fixture containing a symbol named with one
   character, queries for it, and asserts that chunk is among the seeds — by literal id, not by a
   value recomputed from `tokenize()`.
3. The existing retrieval tests still pass unchanged. If any of them shift, that is the finding,
   not a thing to paper over — say so in the PR.

**The catch:** `tokenize()` (`query.py:141`) also feeds `SUBTOKEN_RE` camelCase/snake_case
splitting. Loosening `TOKEN_RE` changes the document-frequency table and therefore every BM25
score, so scores will move even where the ranking does not. Assert set membership, never a score
or a rank — `AGENTS.md`, "Tests must pin values".

---

## 2. Add `CITATION.cff` — [#404](https://github.com/Srinivasan-78/repo2graph/issues/404)

**Size:** XS · **Area:** `area/docs` · **Type:** documentation

No `CITATION.cff` at the repository root. GitHub renders one as a "Cite this repository" button and
generates APA and BibTeX from it; Zenodo and `cffconvert` consume it too.

**Acceptance criteria**

1. `CITATION.cff` at the repository root, valid against the
   [CFF 1.2.0 schema](https://github.com/citation-file-format/citation-file-format).
2. `version` matches `project.version` in `pyproject.toml`.
3. Author, licence (`MIT`) and repository URL agree with `pyproject.toml`.
4. GitHub shows the "Cite this repository" button on the repo page after merge.

**The catch:** `version` becomes one more place the version string lives. The surface list is
`scripts/version_surfaces.py`, shared by `scripts/check_version.py` and `scripts/bump_version.py`,
and `tests/test_version_surfaces.py` asserts the bump script covers **every** surface — so adding
the file without registering it there will fail CI, and *should*. Add it to
`version_surfaces.py` in the same PR.

---

## 3. Python 3.13 in the CI matrix — [#347](https://github.com/Srinivasan-78/repo2graph/issues/347)

**Size:** XS · **Area:** `area/workflows` · **Type:** ci

`pyproject.toml` advertises `Programming Language :: Python :: 3.13` and `requires-python = ">=3.10"`,
but `.github/workflows/ci.yml:40` tests `["3.10", "3.11", "3.12"]`. The classifier is currently a
claim nothing checks.

**Acceptance criteria**

1. `3.13` added to the matrix at `ci.yml:40`.
2. The full suite passes on 3.13 across all three operating systems.
3. If something fails, the PR fixes it or the issue is updated with what fails and why — do not
   drop the classifier to make the matrix green without saying so.

**The catch:** the matrix is 3 OSes × N versions, so this adds three jobs, and there is a second
matrix at `ci.yml:117` (`["3.10", "3.12"]`) for a different job that may or may not want the same
treatment. Decide deliberately and say which in the PR.

---

## 4. PHP namespace separator in call resolution — [#344](https://github.com/Srinivasan-78/repo2graph/issues/344)

**Size:** S · **Area:** `area/graph` · **Type:** bug

`repo2graph/parse.py:_callee_name()` (line 582) reduces a call expression to a bare name by
splitting on separators:

```python
for sep in ("::", ".", "->"):
    if sep in txt:
        txt = txt.split(sep)[-1]
```

PHP's namespace separator is `\`, which is absent, so `\App\Service\Mailer::send()` does not reduce
correctly and the `CALLS` edge is lost or misattributed.

**Acceptance criteria**

1. `\` is handled alongside the existing three.
2. A PHP fixture under `tests/` with a namespaced call produces the expected `CALLS` edge,
   asserted by literal `(src, dst)` node ids.
3. No existing language's call resolution changes — the C++/Rust `::` and the Python/JS `.` paths
   keep their current edges.

**The catch:** order matters. `\App\Service\Mailer::send` contains both `\` and `::`, and the loop
applies each separator in sequence to the running value, so the sequence you add `\` in decides the
result. Work out which order gives `send` for both `A\B::c()` and `A\B\c()`, and put a comment
saying why — the next person will otherwise "simplify" it back.

---

## 5. Quadratic string joining in `_fit_lines` — [#345](https://github.com/Srinivasan-78/repo2graph/issues/345)

**Size:** S · **Area:** `area/query` · **Type:** refactor

`repo2graph/query.py:845`:

```python
kept: list[str] = []
for line in lines:
    candidate = "\n".join([*kept, line])
    if measure(candidate) <= max_size:
        kept.append(line)
    else:
        break
```

Every iteration rebuilds and re-measures the whole accumulated string, so fitting *n* lines costs
O(n²) in both joining and measuring.

**Acceptance criteria**

1. `_fit_lines` returns **byte-identical** output to the current implementation for every input.
2. The accumulated length is tracked incrementally rather than rejoined each iteration.
3. A test pins the output for a multi-line input against a hand-written expected string.

**The catch:** `measure` is a caller-supplied callable defaulting to `len`, and callers pass a
token-estimating function. You cannot assume `measure(a + b) == measure(a) + measure(b)` for an
arbitrary `measure` — that is true for `len` and not guaranteed otherwise. Either keep the exact
semantics for arbitrary `measure` and optimise only the join, or make the incremental path
conditional on `measure is len`. Either is fine; a version that silently changes results for a
token-based `measure` is not.

---

## 6. Deduplicate the node and edge descriptions — [#349](https://github.com/Srinivasan-78/repo2graph/issues/349)

**Size:** S · **Area:** `area/graph` · **Type:** refactor

`export.py:687` defines `NODE_TYPES`/`EDGE_TYPES`; `viz.py:42` defines `NODE_TYPE_DESC`/
`EDGE_TYPE_DESC` with the same keys and near-identical wording. They have already drifted:
`export.NODE_TYPES["repo"]` is `"the repository itself; one per index"`, `viz.NODE_TYPE_DESC["repo"]`
is `"the repository itself"`.

**Acceptance criteria**

1. One definition, in a new module that neither `viz.py` nor `export.py` is imported by.
2. Both modules import from it; no copy remains.
3. The generated `graph.html` legend and the `reference.md`-facing descriptions both still render,
   with whichever wording you keep — state which you kept and why in the PR.

**The catch — read this before starting.** The duplication is deliberate and there is a comment
saying so at `viz.py:38-41`: `export.py` already does `from .viz import ... write_html`, so
importing the other way round is an import cycle. That is why the issue as filed is *not* a
five-minute change. The fix is a third leaf module (`repo2graph/schema.py` or similar) that imports
nothing from the package — not deleting one copy.

---

## 7. Ship `py.typed` — carved out of [#312](https://github.com/Srinivasan-78/repo2graph/issues/312)

**Size:** XS · **Area:** `area/docs` · **Type:** enhancement

`repo2graph` has no `py.typed` marker, so every downstream project consuming it as a library gets
`Any` from it regardless of the annotations that exist — [PEP 561](https://peps.python.org/pep-0561/)
requires the marker file before a type checker will read a package's inline types.

**Acceptance criteria**

1. `repo2graph/py.typed` exists (empty file).
2. It is included in the wheel — `[tool.setuptools.package-data]` in `pyproject.toml`, then
   confirm with `python -m build && unzip -l dist/*.whl | grep py.typed`.
3. A short note in `docs/python-api.md` saying the package ships types and which modules are
   strict-checked.

**The catch:** `pyproject.toml`'s `[[tool.mypy.overrides]]` lists twelve modules with
`disallow_untyped_defs = false`, so a consumer that turns on strict checking will get real errors
from them. Claiming typing support is a promise — say plainly in the doc note which modules are
covered (`query.py` and `export.py` are done) and link the overrides table, rather than implying
the whole package is annotated.

This is issue #312's smallest tractable piece. The rest of #312 — "define the public API surface
and add typed return models" — is an API-design decision across six entry points and is **not** a
first issue; see [docs/issue-triage-2026-09-25.md §2.5](issue-triage-2026-09-25.md#25-good-first-issue--6-proposed-1-today).

---

## Claiming one

Comment on the issue saying you are taking it. No assignment ceremony — if it has gone quiet for a
couple of weeks, assume it is free.

If you get partway and find the task is bigger than described, **say that on the issue**. Half the
value of these is discovering that the estimate was wrong; a comment explaining what you hit is a
real contribution even without a PR.
