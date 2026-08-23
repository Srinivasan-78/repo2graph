"""tree-sitter based extraction of symbols, calls and imports."""
from dataclasses import dataclass, field
from functools import lru_cache

from .langs import LANG_CFG

try:
    from tree_sitter_language_pack import get_parser as _get_parser
except ImportError:  # pragma: no cover
    _get_parser = None


@lru_cache(maxsize=None)
def parser_for(lang: str):
    if _get_parser is None:
        return None
    try:
        return _get_parser(lang)
    except Exception:
        return None


@dataclass
class Symbol:
    name: str
    qualname: str
    kind: str
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    parent: str | None = None
    signature: str = ""
    docstring: str = ""
    calls: list[str] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)


@dataclass
class ParsedFile:
    lang: str
    symbols: list[Symbol]
    imports: list[str]
    file_calls: list[str]
    parse_errors: int = 0


def _text(src: bytes, node) -> str:
    return src[node.start_byte:node.end_byte].decode("utf8", "replace")


def _name_of(src: bytes, node, lang: str) -> str | None:
    n = node.child_by_field_name("name")
    if n is not None:
        return _text(src, n).strip()
    if lang == "rust" and node.type == "impl_item":
        t = node.child_by_field_name("type")
        return _text(src, t) if t is not None else None
    # C-family: dig through declarators for the identifier
    decl = node.child_by_field_name("declarator")
    while decl is not None:
        if decl.type in ("identifier", "field_identifier", "type_identifier"):
            return _text(src, decl)
        nxt = decl.child_by_field_name("declarator")
        if nxt is None:
            for c in decl.children:
                if c.type in ("identifier", "field_identifier", "qualified_identifier"):
                    return _text(src, c)
            return None
        decl = nxt
    for c in node.children:
        if c.type in ("identifier", "type_identifier", "constant", "property_identifier"):
            return _text(src, c)
    return None


def _callee_name(src: bytes, node) -> str | None:
    fn = node.child_by_field_name("function") or node.child_by_field_name("name") \
        or node.child_by_field_name("constructor") or node.child_by_field_name("type")
    if fn is None:
        if node.named_child_count:
            fn = node.named_children[0]
        else:
            return None
    txt = _text(src, fn).strip()
    if not txt:
        return None
    txt = txt.split("(")[0].split("<")[0]
    for sep in ("::", ".", "->"):
        if sep in txt:
            txt = txt.split(sep)[-1]
    txt = txt.strip("!&* \n\t")
    return txt or None


def _docstring(src: bytes, node, lang: str) -> str:
    if lang == "python":
        body = node.child_by_field_name("body")
        if body is not None and body.named_child_count:
            first = body.named_children[0]
            # functions wrap the docstring in an expression_statement, classes do not
            if first.type == "expression_statement" and first.named_child_count:
                first = first.named_children[0]
            if first.type == "string":
                return _text(src, first).strip("\"'\n ")[:600]
        return ""
    # otherwise: comment lines immediately above the definition
    out, prev = [], node.prev_sibling
    while prev is not None and prev.type in ("comment", "line_comment", "block_comment", "doc_comment"):
        out.append(_text(src, prev))
        prev = prev.prev_sibling
    return "\n".join(reversed(out)).strip()[:600]


def _signature(src: bytes, node) -> str:
    body = node.child_by_field_name("body")
    end = body.start_byte if body is not None else min(node.end_byte, node.start_byte + 300)
    return src[node.start_byte:end].decode("utf8", "replace").strip()[:300]


# Node types that hold a class's supertypes. Grammars differ: some expose them
# through a field, others only as an unnamed child clause.
_BASE_NODES = {
    "class_heritage", "extends_clause", "implements_clause", "superclass",
    "super_interfaces", "type_list", "base_list", "base_clause",
    "class_interface_clause", "delegation_specifier", "inheritance_specifier",
    "base_class_clause",
}
# Keywords and access specifiers that sit inside those clauses.
_BASE_WORDS = {
    "extends", "implements", "with", "public", "private", "protected", "internal",
    "virtual", "open", "abstract", "final", "sealed", "override", "case", "class",
    "interface", "struct", "typename",
}


def _clean_base(text: str) -> str:
    """'public B', 'extends B', '< B' -> 'B'."""
    words = [w for w in text.replace(":", " ").replace("<", " <").split()
             if w and w not in _BASE_WORDS and w not in ("<", ">", "&", "*", ",")]
    return words[0].split("(")[0].strip(",;") if words else ""


def _base_clauses(node):
    """Supertype clauses of a definition, preferring the innermost one.

    TypeScript wraps extends_clause/implements_clause in a class_heritage and
    Java wraps a type_list in super_interfaces; the outer node's text would join
    several names into one string, so descend when a child is a clause too.
    """
    out = []
    for child in node.children:
        if child.type not in _BASE_NODES:
            continue
        inner = [c for c in child.children if c.type in _BASE_NODES]
        out += inner or [child]
    return out


def _bases(src: bytes, node, lang: str) -> list[str]:
    out = []
    for fname in ("superclasses", "bases", "trait"):
        n = node.child_by_field_name(fname)
        if n is not None:
            out += [t.strip() for t in _text(src, n).strip("(): ").split(",") if t.strip()]
    for clause in _base_clauses(node):
        raw = _text(src, clause).replace(" with ", ",")
        out += [c for c in (_clean_base(t) for t in raw.split(",")) if c]
    seen, uniq = set(), []
    for b in out:
        if b not in seen:
            seen.add(b)
            uniq.append(b)
    return uniq[:8]


def parse_source(source: bytes, lang: str) -> ParsedFile:
    cfg = LANG_CFG.get(lang)
    parser = parser_for(lang)
    if cfg is None or parser is None:
        return ParsedFile(lang=lang, symbols=[], imports=[], file_calls=[])
    tree = parser.parse(source)
    kind_map, call_types, import_types = cfg["kind_map"], cfg["call_types"], cfg["import_types"]
    symbols: list[Symbol] = []
    imports: list[str] = []
    file_calls: list[str] = []
    errors = 0

    # Explicit stack rather than recursion: tree-sitter trees nest deeply enough
    # (long chained expressions, big literals) to blow the interpreter's limit.
    stack: list[tuple[object, tuple[str, ...], Symbol | None]] = [(tree.root_node, (), None)]
    while stack:
        node, scope, owner = stack.pop()
        ntype = node.type
        if ntype == "ERROR":
            errors += 1
        if ntype in import_types:
            raw = _text(source, node).strip()
            if raw:
                imports.append(raw[:300])
        if ntype in call_types:
            callee = _callee_name(source, node)
            if callee:
                (owner.calls if owner is not None else file_calls).append(callee)
        kind = kind_map.get(ntype)
        child_scope, child_owner = scope, owner
        if kind is not None:
            name = _name_of(source, node, lang)
            if kind == "maybe_function":
                value = node.child_by_field_name("value")
                if value is None or value.type not in (
                        "arrow_function", "function", "function_expression"):
                    kind = None
                else:
                    kind = "function"
            if kind and name:
                sym = Symbol(
                    name=name, qualname=".".join(scope + (name,)), kind=kind,
                    start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                    start_byte=node.start_byte, end_byte=node.end_byte,
                    parent=".".join(scope) or None,
                    signature=_signature(source, node),
                    docstring=_docstring(source, node, lang),
                    bases=_bases(source, node, lang),
                )
                symbols.append(sym)
                child_scope, child_owner = scope + (name,), sym
        # named_children skips punctuation and keyword tokens: no configured
        # kind/call/import type is anonymous, and half the tree is those tokens.
        # reversed: the stack pops last-pushed first, so this keeps source order
        for c in reversed(node.named_children):
            stack.append((c, child_scope, child_owner))
    return ParsedFile(lang=lang, symbols=symbols, imports=imports,
                      file_calls=file_calls, parse_errors=errors)
