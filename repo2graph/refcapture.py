"""Reference capture for `--reference-edges` (#397): reads, writes and type references per symbol (#315)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tree_sitter import Node

if TYPE_CHECKING:
    from .parse import Symbol


def _text(src: bytes, node: Node) -> str:
    return src[node.start_byte : node.end_byte].decode("utf8", "replace")


REF_LANGS = frozenset({"python", "javascript", "typescript", "tsx"})
# Symbol kinds a READS / WRITES edge can point at.
VALUE_KINDS = ("variable", "constant", "field")
_PY_SELF = frozenset({"self", "cls"})
# Parents under which an identifier is a binding being introduced (a
# parameter, a definition's own name, an import), never a read of a name.
_PY_BINDING_PARENTS = frozenset(
    {
        "function_definition",
        "class_definition",
        "parameters",
        "lambda_parameters",
        "typed_parameter",
        "list_splat_pattern",
        "dictionary_splat_pattern",
        "aliased_import",
        "dotted_name",
        "import_statement",
        "import_from_statement",
        "global_statement",
        "nonlocal_statement",
    }
)
_JS_PARAM_PARENTS = frozenset(
    {"formal_parameters", "required_parameter", "optional_parameter", "rest_pattern"}
)
_JS_SKIP_PARENTS = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "class_declaration",
        "method_definition",
        "import_specifier",
        "import_clause",
        "namespace_import",
        "export_specifier",
        "labeled_statement",
        "break_statement",
        "continue_statement",
    }
)


def _same(a: Node, b: Node | None) -> bool:
    return b is not None and a.start_byte == b.start_byte and a.end_byte == b.end_byte


def _value_kind(name: str, in_class: bool) -> str:
    if in_class:
        return "field"
    return "constant" if name.isupper() else "variable"


def _py_value_symbol(src: bytes, node: Node, owner: Symbol | None) -> tuple[str | None, str | None]:
    """(kind, name) when an `assignment` binds one module- or class-level name."""
    # Some grammar versions wrap the assignment in an expression_statement.
    stmt = (
        node.parent
        if node.parent is not None and node.parent.type == "expression_statement"
        else node
    )
    left = node.child_by_field_name("left")
    if left is None or left.type != "identifier":
        return None, None
    name = _text(src, left)
    home = stmt.parent
    if owner is None and home is not None and home.type == "module":
        return _value_kind(name, False), name
    if (
        owner is not None
        and owner.kind == "class"
        and home is not None
        and home.type == "block"
        and home.parent is not None
        and home.parent.type == "class_definition"
    ):
        return "field", name
    return None, None


def _js_value_kind(node: Node, owner: Symbol | None, name: str) -> str | None:
    """The value kind of a non-function JS/TS binding, if it is module- or class-level."""
    if node.type in ("field_definition", "public_field_definition"):
        return "field" if owner is not None and owner.kind == "class" else None
    name_node = node.child_by_field_name("name")
    decl = node.parent
    if (
        owner is None
        and name_node is not None
        and name_node.type == "identifier"
        and decl is not None
        and decl.parent is not None
        and decl.parent.type in ("program", "export_statement")
    ):
        return _value_kind(name, False)
    return None


class _RefState:
    """Per-file bookkeeping for reference capture: locals, globals, type ids."""

    def __init__(self) -> None:
        self.locals: dict[int, set[str]] = {}
        self.globals: dict[int, set[str]] = {}
        self.typed: set[int] = set()  # start bytes of identifiers inside a type

    def add(self, owner: Symbol, name: str, kind: str, line: int, field_ref: bool = False) -> None:
        ref = {"name": name, "kind": kind, "line": line}
        if field_ref:
            ref["field"] = True
        owner.refs.append(ref)


def _py_ref(src: bytes, node: Node, owner: Symbol, st: _RefState) -> None:
    ntype = node.type
    oid = id(owner)
    line = node.start_point[0] + 1
    if ntype in ("global_statement", "nonlocal_statement"):
        names = st.globals.setdefault(oid, set())
        names.update(_text(src, c) for c in node.named_children if c.type == "identifier")
        return
    if ntype == "type":
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type == "identifier":
                st.typed.add(n.start_byte)
                st.add(owner, _text(src, n), "type", line)
            stack.extend(n.named_children)
        return
    if owner.kind not in ("function", "method"):
        return
    if ntype == "attribute":
        obj = node.child_by_field_name("object")
        attr = node.child_by_field_name("attribute")
        if obj is None or attr is None or obj.type != "identifier":
            return
        if _text(src, obj) not in _PY_SELF:
            return
        p = node.parent
        write = (
            p is not None
            and p.type in ("assignment", "augmented_assignment")
            and _same(node, p.child_by_field_name("left"))
        )
        st.add(owner, _text(src, attr), "write" if write else "read", line, field_ref=True)
        return
    if ntype != "identifier" or node.start_byte in st.typed:
        return
    p = node.parent
    if p is None:
        return
    pt = p.type
    name = _text(src, node)
    if pt in _PY_BINDING_PARENTS:
        if pt in ("parameters", "lambda_parameters", "typed_parameter"):
            st.locals.setdefault(oid, set()).add(name)
        return
    if pt in ("default_parameter", "typed_default_parameter", "keyword_argument"):
        if _same(node, p.child_by_field_name("name")):
            if pt != "keyword_argument":
                st.locals.setdefault(oid, set()).add(name)
            return
    elif pt == "attribute":
        if not _same(node, p.child_by_field_name("object")) or name in _PY_SELF:
            return
    elif pt == "call" and _same(node, p.child_by_field_name("function")):
        return
    target = p
    while target.type in ("pattern_list", "tuple_pattern", "list_pattern"):
        if target.parent is None:
            break
        target = target.parent
    if target.type in ("assignment", "augmented_assignment", "for_statement", "for_in_clause"):
        if _same(node, p.child_by_field_name("left")) or target is not p:
            if name in st.globals.get(oid, ()):
                st.add(owner, name, "write", line)
            else:
                st.locals.setdefault(oid, set()).add(name)
            return
    if pt in ("as_pattern_target", "with_item"):
        st.locals.setdefault(oid, set()).add(name)
        return
    st.add(owner, name, "read", line)


def _js_ref(src: bytes, node: Node, owner: Symbol, st: _RefState) -> None:
    ntype = node.type
    oid = id(owner)
    line = node.start_point[0] + 1
    if ntype == "type_identifier":
        p = node.parent
        if p is None or not _same(node, p.child_by_field_name("name")):  # not `class Box`
            st.add(owner, _text(src, node), "type", line)
        return
    if owner.kind not in ("function", "method"):
        return
    if ntype == "member_expression":
        obj = node.child_by_field_name("object")
        prop = node.child_by_field_name("property")
        if obj is None or prop is None or obj.type != "this":
            return
        p = node.parent
        write = p is not None and (
            p.type == "update_expression"
            or (
                p.type in ("assignment_expression", "augmented_assignment_expression")
                and _same(node, p.child_by_field_name("left"))
            )
        )
        st.add(owner, _text(src, prop), "write" if write else "read", line, field_ref=True)
        return
    if ntype not in ("identifier", "shorthand_property_identifier"):
        return
    p = node.parent
    if p is None:
        return
    pt = p.type
    name = _text(src, node)
    if pt in _JS_SKIP_PARENTS:
        return
    if pt in _JS_PARAM_PARENTS or pt == "catch_clause":
        # `max = LIMIT` in a TS parameter: the pattern binds, the value is read.
        if not _same(node, p.child_by_field_name("value")):
            st.locals.setdefault(oid, set()).add(name)
            return
    if pt == "arrow_function" and _same(node, p.child_by_field_name("parameter")):
        st.locals.setdefault(oid, set()).add(name)
        return
    if pt == "variable_declarator" and _same(node, p.child_by_field_name("name")):
        st.locals.setdefault(oid, set()).add(name)
        return
    if pt == "assignment_pattern" and _same(node, p.child_by_field_name("left")):
        st.locals.setdefault(oid, set()).add(name)
        return
    if pt in ("call_expression", "new_expression") and _same(
        node, p.child_by_field_name("function") or p.child_by_field_name("constructor")
    ):
        return
    if (
        pt in ("assignment_expression", "augmented_assignment_expression")
        and _same(node, p.child_by_field_name("left"))
    ) or pt == "update_expression":
        st.add(owner, name, "write", line)
        return
    st.add(owner, name, "read", line)


def _finish_refs(symbols: list[Symbol], st: _RefState) -> None:
    """Drop references to a symbol's own locals; merge repeats, keeping the first line."""
    for sym in symbols:
        if not sym.refs:
            continue
        own = st.locals.get(id(sym), set()) - st.globals.get(id(sym), set())
        merged: dict[tuple[Any, ...], dict[str, Any]] = {}
        for r in sym.refs:
            if r["kind"] != "type" and not r.get("field") and r["name"] in own:
                continue
            key = (r["kind"], r["name"], bool(r.get("field")))
            if key in merged:
                merged[key]["count"] += 1
            else:
                merged[key] = {**r, "count": 1}
        sym.refs = list(merged.values())
