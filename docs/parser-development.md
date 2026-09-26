# Adding a language

How to give repo2graph function-, class- and call-level understanding of a new language. Lua was
the most recent addition (the seventeenth grammar) and is the worked example throughout.

**The short version:** a grammar is already installed for you — `tree-sitter-language-pack` is a
required dependency and ships roughly 165 of them. Adding a language is a `LANG_CFG` entry, an
`EXT_LANG` mapping, two tests and a documentation row. No parser is written, no grammar is
vendored, no build step is added.

---

## 0. Decide whether it needs to be here at all

An unparsed language is **not** invisible. Files in any language still become `file` nodes, still
appear on the map, are still chunked as text and are still retrievable by `repo2graph query`. What
a `LANG_CFG` entry adds is *symbol-level* structure: `symbol` nodes, and `DEFINES` / `CALLS` /
`INHERITS` edges.

So the question is not "is this language supported?" but "does this language's code get asked
relationship questions?" A templating language or a config dialect usually does not. Anything with
functions that call other functions does.

---

## 1. Map the extensions

`repo2graph/parse.py`, `EXT_LANG` (line 26 onward). Extension → grammar key:

```python
EXT_LANG = {
    ...
    ".lua": "lua",
}
```

Two traps here, both of which the repository has already hit:

- **One extension, one language.** `".h": "c"` maps every C++ header to the C grammar, which is
  [issue #377](https://github.com/Srinivasan-78/repo2graph/issues/377) — C++ classes and templates
  in `.h` files are mis-parsed. If your language shares an extension with another, say so in the
  PR; the mapping cannot currently express "sniff the contents".
- **The extension count is asserted.** `EXT_LANG` currently has 29 entries and the READMEs say so.
  A test enforces the grammar list (§5), and the counts in prose need updating by hand.

---

## 2. Write the `LANG_CFG` entry

Same file, `LANG_CFG` (line 75 onward). Four keys, typed by `LangConfig`:

```python
class LangConfig(TypedDict):
    kind_map: dict[str, str]  # tree-sitter node type -> repo2graph symbol kind
    call_types: set[str]  # node types that are a call site
    import_types: set[str]  # node types that are an import
    doc: str  # docstring style: "python" | "jsdoc" | "line"
```

The Python entry, as a minimal example:

```python
"python": {
    "kind_map": {"function_definition": "function", "class_definition": "class"},
    "call_types": {"call"},
    "import_types": {"import_statement", "import_from_statement"},
    "doc": "python",
},
```

### `kind_map`

Keys are **tree-sitter node types for that grammar**, not names you invent. Values are repo2graph
symbol kinds: `function`, `method`, `class`, `struct`, `enum`, `trait`, `impl`, `interface`,
`type`, `module`, and the special `maybe_function`.

`maybe_function` exists for languages where a variable binding may or may not hold a function —
JavaScript's `variable_declarator` covers both `const x = 1` and `const f = () => {}`, and only the
second should become a symbol.

Find the real node types by parsing a sample:

```python
from tree_sitter_language_pack import get_parser

tree = get_parser("lua").parse(b"local function greet(name)\n  print(name)\nend\n")


def walk(n, d=0):
    print("  " * d, n.type)
    for c in n.children:
        walk(c, d + 1)


walk(tree.root_node)
```

Do this before writing the entry. Guessing node-type names from another grammar is the most common
way this goes wrong — grammars disagree (`function_definition` in Python and C,
`function_declaration` in Go and JavaScript, `function_item` in Rust, `method` in Ruby).

### `call_types`

Node types that represent a call site. Include construction where the language treats it as a call
— Java has `{"method_invocation", "object_creation_expression"}`, Rust includes
`macro_invocation`, JavaScript includes `new_expression`.

Whatever you list, `_callee_name` (`parse.py:582`) reduces the expression to a bare name by
splitting on `("::", ".", "->")`. If your language uses a different separator, that tuple needs it
— PHP's `\` is missing, which is
[issue #344](https://github.com/Srinivasan-78/repo2graph/issues/344).

### `import_types`

Node types that declare an import. An empty set is legitimate — Ruby uses `set()` because `require`
is an ordinary method call, not syntax.

This only makes the import *visible*. Resolving it to a file in the repository is separate — §4.

### `doc`

How a doc comment attaches to a symbol. Three styles exist:

| Value | Shape | Used by |
|---|---|---|
| `"python"` | A string literal as the first statement in the body | Python |
| `"jsdoc"` | A `/** ... */` block immediately above the declaration | JS, TS, Java |
| `"line"` | Consecutive line comments immediately above | Go, Rust, Ruby, Lua, and most others |

Pick the closest. Adding a fourth style is a change to the extractor, not to `LANG_CFG`, and wants
its own issue.

### Deriving from an existing language

Where two languages share a grammar family, the config is copied and adjusted rather than rewritten
— `parse.py:220-232`:

```python
LANG_CFG["typescript"] = cast(LangConfig, dict(LANG_CFG["javascript"]))
LANG_CFG["typescript"]["kind_map"] = dict(...)
LANG_CFG["tsx"] = LANG_CFG["typescript"]
LANG_CFG["cpp"] = cast(LangConfig, dict(LANG_CFG["c"]))
```

Note `dict(...)` on both the outer config and `kind_map`. A shallow assignment would share the
mutable `kind_map` between two languages and edits to one would silently alter the other.

---

## 3. Inheritance, if the language has it

`INHERITS` edges come from `_bases_with_details` (`parse.py:764`), which looks for a field named
`superclasses`, `bases` or `trait` on the symbol node, and for any child whose node type is in
`_BASE_NODES` (`parse.py:678`) — `extends_clause`, `implements_clause`, `base_list`,
`delegation_specifier`, `inheritance_specifier`, and the rest.

If your grammar names its inheritance clause something not on that list, add it there. Keywords and
access specifiers inside the clause are filtered by `_BASE_WORDS`, so `public`/`private` in a C++
base clause are already handled.

Base names are normalised in `graph.py:1185`, which strips generics and splits on `.` — it does
**not** handle `::` or `\`, which is
[issue #341](https://github.com/Srinivasan-78/repo2graph/issues/341).

---

## 4. In-repo import resolution, optionally

`LANG_CFG["import_types"]` makes an import visible. Turning `import foo.bar` into an `IMPORTS`
edge pointing at `foo/bar.py` is per-language logic in `graph.py` (the `elif lang == ...` chain
from line 291), because every language's module-to-path convention differs: Rust understands
`crate::` / `super::` / `self::` and the crate name from `Cargo.toml`; PHP maps PSR-4-ish `App\`
prefixes onto `app/` and `src/`; Ruby distinguishes `require_relative` from `require`; Bash
resolves `source` against the sourcing file's directory.

Thirteen languages have this; the rest emit `IMPORTS` to a `module` node instead — an external
dependency rather than a file. **That is a perfectly good first version.** Ship the `LANG_CFG`
entry without path resolution, and add resolution as a separate change with its own tests.

---

## 5. Tests — two of them, and one is enforced

Follow the Lua pair in `tests/test_repo2graph.py:213-254` exactly.

**Unit: the parser sees the symbols.**

```python
def test_parse_lua_extracts_functions_and_calls():
    src = b"""-- Greets a person with a friendly message
local function greet(name)
    print(name)
end
"""
    pf = parse_source(src, "lua")
    if not pf.symbols:
        pytest.skip("lua grammar unavailable")
    assert pf.parse_errors == 0
    names = {s.name: s for s in pf.symbols}
    assert names["greet"].kind == "function"
    assert names["greet"].docstring == "-- Greets a person with a friendly message"
    assert "print" in names["greet"].calls
```

The `pytest.skip` when `pf.symbols` is empty is not optional — a grammar can be missing from the
pack on some platforms, and the suite must skip rather than fail.

**Integration: the build produces the edges.**

```python
def test_build_lua_calls_edge(tmp_path):
    (tmp_path / "main.lua").write_text("...", encoding="utf8")
    g = build(tmp_path)
    assert ("sym:main.lua::run", "sym:main.lua::helper") in edges_of(g, "CALLS")
    assert ("file:main.lua", "sym:main.lua::run") in edges_of(g, "DEFINES")
```

Assert **literal node-id tuples**, hand-derived from the fixture source. Never assert against a
value recomputed by the code under test — see AGENTS.md, "Tests must pin values, not compare the
implementation to itself". A test that asserts `len(calls) > 0` passes with the wrong edges.

**The enforced one:** `tests/test_doc_consistency.py::test_languages_documented` and
`tests/test_i18n_consistency.py::test_every_readme_documents_every_parsed_grammar` assert that
every `LANG_CFG` key appears in `README.md` **and in all five translated READMEs**. Adding a
grammar without documenting it fails CI in six places. The mapping from grammar key to the token
the READMEs use lives in `tests/test_doc_consistency.py`'s `LANGUAGE_TOKENS` — add your language
there too, or the sync assertion fails.

---

## 6. Documentation checklist

All of these, or CI fails or the docs lie:

- [ ] `LANGUAGE_TOKENS` in `tests/test_doc_consistency.py` — the grammar key and its README token.
- [ ] `README.md` — the "*N* grammars, full treatment" row: add the language, bump the grammar and
      extension counts.
- [ ] `docs/i18n/README_{de,es,fr,ja,zh-CN}.md` — same row, same counts, in each.
- [ ] `docs/comparison.md` — the "Languages parsed for symbols" cell.
- [ ] `docs/reference.md` — the language table.
- [ ] `CHANGELOG.md` — under `## [Unreleased]` → `### Added`.

The counts drifted across all six READMEs once already (they said 16 grammars / 28 extensions for
some time after Lua made it 17/29), which is why the assertion in §5 now exists.

---

## 7. What "supported" honestly means

Before claiming a language works, check it against a real repository and look at
`stats.json`'s `parse_errors`. Two things that will not work, and should be said plainly rather
than discovered by a user:

- **Macro-heavy languages parse imperfectly.** C and C++ produce thousands of tree-sitter `ERROR`
  nodes around unexpanded macros; a `cpp` preprocessor fallback recovers a minority. See
  [docs/limitations.md](limitations.md) for the measured rates.
- **Call resolution is name-based for every language, including yours.** A language with heavy
  method-name reuse (TypeScript's `dispose()`, `getId()`) produces more ambiguous `CALLS` edges
  than one with prefix-disciplined naming. That is a property of the language's conventions, not a
  bug in your entry.

If either applies, add a note to `docs/limitations.md` in the same PR. Overstating coverage is
worse than not adding the language — see [POSITIONING.md](../POSITIONING.md) §1, "What we are not
claiming".
