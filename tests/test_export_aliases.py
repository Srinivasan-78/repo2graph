"""An exported alias is the name consumers import, so it must be a symbol.

A JS/TS library that keeps a private implementation behind a public façade
binds the public name in a second statement, and neither form produced a
symbol:

    const _getQueryParam = (url, key) => { ... }          # indexed
    export const getQueryParam: (...) = _getQueryParam as (...)   # was not

    class Hono { ... }                                     # indexed
    export { Hono as HonoBase }                            # was not

So the name every consumer writes in its `import` had no node, no chunk and
no line range -- `hono/src/request.ts` calls `getQueryParam(this.url, key)`
and the callee resolved to nothing. This is D7 in `benchmarks/real/README.md`,
and `scripts/validate_tasks.py` reported it on every run as the one parser gap
in the held-out set (`ho-struct-hono-03` cites `utils/url.ts::getQueryParam`
at lines 295-301, the alias declaration's own lines).

Two boundaries are deliberate and pinned below:

* An alias is only a symbol when the statement **exports** it. A file-local
  `const b = a` renames nothing a consumer can reach, and indexing every one
  would add nodes that compete for the retrieval budget without being
  anybody's entry point.
* `export { x as y } from './mod'` -- a re-export with a `source` -- is **not**
  indexed. Nothing is defined at that line, and a barrel file full of them
  would turn into dozens of one-line nodes bidding against real definitions.
  Resolving a re-export to the file it forwards to is a separate feature.

The value must be a *reference* (an identifier or a member expression, bare or
wrapped in `as` / `satisfies` / parentheses). `export const LIMIT = 42` stays
out for the same reason `limit = 42` does: it is data, not a definition worth
retrieving under its own name.
"""

from __future__ import annotations

import pytest

from repo2graph.parse import parse_source

TS_SOURCE = """const _getQueryParam = (url, key) => { return url }

export const getQueryParam: (url: string, key?: string) => string = _getQueryParam as (
  url: string,
  key?: string,
) => string

export const decodeURIComponent_ = decodeURIComponent

export const satisfied = _getQueryParam satisfies Thing

export const wrapped = (_getQueryParam)

export const viaMember = utils.helper

class Hono {
  constructor() {}
}

export { Hono as HonoBase }

export { Hono }

export { jsxDEV as jsx } from './jsx-dev-runtime'

export const LIMIT = 42

export const TEMPLATE = `a${b}c`

const localOnly = _getQueryParam

export default Hono
"""

JS_SOURCE = """const _impl = (a) => a

export const publicName = _impl

class Widget {}

export { Widget as PublicWidget }

export const COUNT = 7
"""


def _syms(source: str, lang: str) -> dict[str, str]:
    """{qualname: kind} for every symbol the parser extracted."""
    pf = parse_source(source.encode(), lang)
    assert not pf.grammar_unavailable, f"{lang} grammar did not load"
    return {s.qualname: s.kind for s in pf.symbols}


def _sym(source: str, lang: str, qualname: str):
    pf = parse_source(source.encode(), lang)
    assert not pf.grammar_unavailable, f"{lang} grammar did not load"
    return next(s for s in pf.symbols if s.qualname == qualname)


@pytest.mark.parametrize(
    "alias",
    [
        "getQueryParam",  # `= target as <type>`
        "decodeURIComponent_",  # bare identifier, target is a global
        "satisfied",  # `= target satisfies <type>`
        "wrapped",  # parenthesised
        "viaMember",  # member expression
    ],
)
def test_exported_const_alias_is_indexed(alias: str) -> None:
    """Every reference-valued exported binding gets a node of kind `alias`."""
    syms = _syms(TS_SOURCE, "typescript")
    assert alias in syms, f"{alias} missing; got {sorted(syms)}"
    assert syms[alias] == "alias"


def test_export_specifier_alias_is_indexed() -> None:
    """`export { Hono as HonoBase }` binds HonoBase, so HonoBase is a symbol."""
    syms = _syms(TS_SOURCE, "typescript")
    assert "HonoBase" in syms, f"HonoBase missing; got {sorted(syms)}"
    assert syms["HonoBase"] == "alias"


def test_javascript_aliases_are_indexed() -> None:
    """The JavaScript grammar reaches the same two forms."""
    syms = _syms(JS_SOURCE, "javascript")
    assert syms.get("publicName") == "alias"
    assert syms.get("PublicWidget") == "alias"


def test_alias_cites_its_own_declaration_lines() -> None:
    """The range must be the alias statement, which is what the evidence cites.

    `ho-struct-hono-03` marks `utils/url.ts::getQueryParam` at 295-301 -- the
    seven lines of the export, not the implementation's 215-293. A node whose
    range collapsed onto the target would be cited to the wrong place.
    """
    alias = _sym(TS_SOURCE, "typescript", "getQueryParam")
    impl = _sym(TS_SOURCE, "typescript", "_getQueryParam")
    assert alias.start_line == 3
    assert alias.end_line == 6
    assert alias.start_line > impl.end_line
    assert "getQueryParam" in TS_SOURCE.split("\n")[alias.start_line - 1]

    hono_alias = _sym(TS_SOURCE, "typescript", "HonoBase")
    assert hono_alias.start_line == hono_alias.end_line
    assert "HonoBase" in TS_SOURCE.split("\n")[hono_alias.start_line - 1]


def test_alias_is_module_scoped_and_not_nested() -> None:
    """An alias binds at module scope: no parent, and it owns no children."""
    for name in ("getQueryParam", "HonoBase"):
        sym = _sym(TS_SOURCE, "typescript", name)
        assert sym.parent is None, f"{name} should be module-scoped, got {sym.parent}"
        assert sym.parent_key == ""


def test_alias_signature_names_the_target() -> None:
    """A one-line signature is all the node carries, so it must show the target."""
    assert "_getQueryParam" in (_sym(TS_SOURCE, "typescript", "getQueryParam").signature or "")
    assert "Hono" in (_sym(TS_SOURCE, "typescript", "HonoBase").signature or "")


@pytest.mark.parametrize("name", ["LIMIT", "TEMPLATE", "localOnly", "jsx", "default"])
def test_non_aliases_stay_unindexed(name: str) -> None:
    """Data, file-local renames, and `from` re-exports must not become symbols.

    `LIMIT`/`TEMPLATE` are values, `localOnly` is not exported, and `jsx` comes
    from `export { jsxDEV as jsx } from './jsx-dev-runtime'` where nothing is
    defined at that line.
    """
    assert name not in _syms(TS_SOURCE, "typescript")


def test_non_alias_exported_constant_stays_unindexed_in_javascript() -> None:
    assert "COUNT" not in _syms(JS_SOURCE, "javascript")


def test_existing_symbols_are_unaffected() -> None:
    """The paths that already worked keep working, with no duplicate nodes."""
    pf = parse_source(TS_SOURCE.encode(), "typescript")
    syms = {s.qualname: s.kind for s in pf.symbols}
    assert syms.get("_getQueryParam") == "function"
    assert syms.get("Hono") == "class"
    assert syms.get("Hono.constructor") == "method"

    quals = [s.qualname for s in pf.symbols]
    assert len(quals) == len(set(quals)), f"duplicate symbols: {quals}"


def test_plain_export_of_a_local_name_adds_nothing() -> None:
    """`export { Hono }` re-exports an already-indexed name under that name."""
    pf = parse_source(TS_SOURCE.encode(), "typescript")
    assert sum(1 for s in pf.symbols if s.qualname == "Hono") == 1
