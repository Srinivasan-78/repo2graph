"""A class field holding a function is a definition, and must be indexed as one.

`class C { json = (c) => ... }` defines `C.json` exactly as `json() {}` would.
The JS/TS `kind_map` recognised `method_definition` and, for module scope,
`variable_declarator`, but neither `public_field_definition` (TypeScript) nor
`field_definition` (JavaScript) -- so this whole idiom produced no symbol.

It is not a corner case. In Hono 4.9.0 it accounts for 22 missing symbols in
`src/`, and they are the library's public surface: `Context.json`,
`.text`, `.html`, `.body`, `.redirect`, `.header`, `.status`, `.get`, `.set`,
`.newResponse`, `.notFound` and `Hono.fetch`. `context.ts` indexed its
constructor, its getters and one private method, and nothing a caller uses.
No retrieval ranking can return a symbol that was never extracted, which is
why three of the benchmark's hono questions were unanswerable by construction.

A field whose value is *not* a function (`limit = 42`) must stay unindexed:
`maybe_function` exists to make that distinction, and these tests pin both
sides of it.
"""

from __future__ import annotations

import pytest

from repo2graph.parse import parse_source

TS_SOURCE = """class Ctx {
  constructor(req) { this.req = req }

  plain(a) { return a }

  json = (body) => { return new Response(body) }

  text: TextRespond = function (body) { return body }

  html: HTMLRespond = <T extends string>(content: T) => { return content }

  #newResponse = (body) => { return body }

  limit = 42

  name: string = "ctx"
}

const topLevel = (x) => x
"""

JS_SOURCE = """class Ctx {
  plain(a) { return a }
  json = (body) => { return body }
  text = function (body) { return body }
  limit = 42
}
"""


def _qualnames(source: str, lang: str) -> dict[str, str]:
    """{qualname: kind} for every symbol the parser extracted."""
    pf = parse_source(source.encode(), lang)
    assert not pf.grammar_unavailable, f"{lang} grammar did not load"
    return {s.qualname: s.kind for s in pf.symbols}


@pytest.mark.parametrize(
    "field",
    ["Ctx.json", "Ctx.text", "Ctx.html", "Ctx.#newResponse"],
)
def test_typescript_class_field_functions_are_indexed(field: str) -> None:
    """Arrow functions, function expressions, generics and `#private` alike."""
    syms = _qualnames(TS_SOURCE, "typescript")
    assert field in syms, f"{field} missing; got {sorted(syms)}"
    assert syms[field] == "function"


@pytest.mark.parametrize("field", ["Ctx.json", "Ctx.text"])
def test_javascript_class_field_functions_are_indexed(field: str) -> None:
    """`field_definition` is the JavaScript grammar's name for the same thing."""
    syms = _qualnames(JS_SOURCE, "javascript")
    assert field in syms, f"{field} missing; got {sorted(syms)}"
    assert syms[field] == "function"


@pytest.mark.parametrize("lang,source", [("typescript", TS_SOURCE), ("javascript", JS_SOURCE)])
def test_non_function_class_fields_stay_unindexed(lang: str, source: str) -> None:
    """A data field is not a definition, and must not become a symbol."""
    syms = _qualnames(source, lang)
    assert "Ctx.limit" not in syms
    if lang == "typescript":
        assert "Ctx.name" not in syms


@pytest.mark.parametrize("lang,source", [("typescript", TS_SOURCE), ("javascript", JS_SOURCE)])
def test_methods_and_module_scope_still_indexed(lang: str, source: str) -> None:
    """The paths that already worked keep working."""
    syms = _qualnames(source, lang)
    assert syms.get("Ctx.plain") == "method"
    assert "Ctx" in syms
    if lang == "typescript":
        assert syms.get("Ctx.constructor") == "method"
        assert syms.get("topLevel") == "function"


def test_field_function_carries_its_own_line_range() -> None:
    """A field's symbol must span its own definition, not the class body.

    Retrieval cites `path:start-end`, so a field whose range collapsed to the
    class header would be cited to the wrong place even once it is indexed.
    """
    pf = parse_source(TS_SOURCE.encode(), "typescript")
    json_sym = next(s for s in pf.symbols if s.qualname == "Ctx.json")
    cls = next(s for s in pf.symbols if s.qualname == "Ctx")
    assert json_sym.start_line > cls.start_line
    assert json_sym.end_line <= cls.end_line
    assert json_sym.start_line <= json_sym.end_line
    body = TS_SOURCE.split("\n")[json_sym.start_line - 1]
    assert "json" in body
