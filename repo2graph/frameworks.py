"""Framework facts read off a decorated definition: routes, DI, ORM relations.

`build --framework-edges` (off by default) turns these into `ROUTES_TO`,
`INJECTS` and `MODELS` edges. This module is the capture half and is a **leaf**:
it imports nothing from the package, takes tree-sitter nodes and returns plain
dicts, so the framework knowledge is all in one readable place and adding a
framework is a table entry rather than a change to the parser.

**Why these three.** `docs/architecture.md` section 4 scored `Framework` at 20
or 0 for every language because no framework edge type existed, and named that
as the one cap keeping every language off an A. Two of the five questions the
README opens with -- "where is authentication enforced?" and "trace a request
from route to persistence" -- are route questions, and a name-resolved call
graph cannot answer them: nothing in the repository *calls* a request handler,
so a handler is an unreachable entrypoint with no edge saying what reaches it.
A `ROUTES_TO` edge is that missing edge.

**What is deliberately not here.** Only *decorator*-shaped routing is read:
FastAPI, Flask, Starlette, APIFlask, Sanic and NestJS declare routes on the
definition, so the handler is the decorated symbol and the edge is syntactic
and exact. Express/Hono/Koa register routes with a *call* --
`app.get("/x", handler)` -- where the handler is an argument, very often an
inline arrow with no symbol to point at, and the registration can sit anywhere
including behind a loop. Django's `urlpatterns` has the same shape. Those need
call-argument dataflow rather than a decorator read, so they get no edge and
`stats.json` does not pretend otherwise. A missing route edge never means
"no route", exactly as a missing `CALLS` edge never means "no call".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tree_sitter import Node

#: Languages whose decorators this module can read. The same four as
#: `refcapture.REF_LANGS`, and for the same reason: these are the ecosystems
#: where decorator-declared routing and DI are idiomatic, and the two the
#: retrieval benchmark measures. Java/Kotlin annotations (`@GetMapping`,
#: `@Autowired`) are the obvious next entry and are not here yet, because
#: Spring resolves the implementation from the container at runtime and a
#: `@Autowired` field names an interface -- so the edge wants its own design
#: rather than this table.
FRAMEWORK_LANGS: frozenset[str] = frozenset({"python", "javascript", "typescript", "tsx"})

__all__ = [
    "DI_MARKERS",
    "FRAMEWORK_LANGS",
    "HTTP_METHODS",
    "MOUNT_DECORATORS",
    "ORM_RELATION_FIELDS",
    "extract_pytest_facts",
    "function_parameters",
    "injected_dependencies",
    "model_relations",
    "mount_prefix",
    "route_from_decorator",
]

#: Decorator names that declare a route, lowercased, mapped to the HTTP method.
#: `route`/`all`/`api_route` carry no method in the name; the method comes from
#: a `methods=[...]` argument when there is one, and otherwise defaults to GET
#: (which is Flask's own default, and the only sound guess for `@app.route`).
HTTP_METHODS: dict[str, str] = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "patch": "PATCH",
    "delete": "DELETE",
    "head": "HEAD",
    "options": "OPTIONS",
    "trace": "TRACE",
    "route": "",
    "api_route": "",
    "all": "",
    "websocket": "WS",
}

#: Class-level decorators that prefix every route the class declares.
#: NestJS `@Controller('users')`; Flask-Smorest / FastAPI-style `@Blueprint`
#: and `@APIRouter` are included because they are the same shape.
MOUNT_DECORATORS: frozenset[str] = frozenset(
    {"controller", "blueprint", "apirouter", "router", "resource"}
)

#: Call markers that declare an injected dependency in a parameter default.
#: FastAPI's `Depends`, and the `Security` variant that wraps it.
DI_MARKERS: frozenset[str] = frozenset({"depends", "security"})

#: ORM field constructors whose first argument names a *related model*, mapped
#: to the relation kind recorded on the edge.
ORM_RELATION_FIELDS: dict[str, str] = {
    # Django
    "foreignkey": "ForeignKey",
    "onetoonefield": "OneToOne",
    "manytomanyfield": "ManyToMany",
    # SQLAlchemy
    "relationship": "relationship",
    # Peewee
    "foreignkeyfield": "ForeignKey",
}

_ARG_LIST_TYPES = ("argument_list", "arguments")
_STRING_TYPES = ("string", "template_string")
_CALL_TYPES = ("call", "call_expression")


def _unquote(text: str) -> str:
    """A string literal's content, without its quotes or an f/r/b prefix."""
    body = text.strip()
    while body[:1].lower() in ("f", "r", "b", "u") and body[1:2] in ("'", '"'):
        body = body[1:]
    if len(body) >= 2 and body[0] == body[-1] and body[0] in ("'", '"'):
        return body[1:-1]
    return body


def _text(src: bytes, node: Node) -> str:
    return src[node.start_byte : node.end_byte].decode("utf8", "replace")


def _call_of(node: Node) -> Node | None:
    """The call inside a decorator, or None for a bare `@name` decorator."""
    if node.type in _CALL_TYPES:
        return node
    for child in node.named_children:
        if child.type in _CALL_TYPES:
            return child
    return None


def _arg_list(call: Node) -> Node | None:
    for child in call.named_children:
        if child.type in _ARG_LIST_TYPES:
            return child
    return None


def _first_string_arg(call: Node, src: bytes) -> str | None:
    args = _arg_list(call)
    if args is None:
        return None
    for child in args.named_children:
        if child.type in _STRING_TYPES:
            return _unquote(_text(src, child))
        if child.type in ("keyword_argument", "pair", "object"):
            return None
    return None


def _methods_kwarg(call: Node, src: bytes) -> list[str]:
    """The `methods=["POST", "PUT"]` argument of a Flask-style route."""
    args = _arg_list(call)
    if args is None:
        return []
    for child in args.named_children:
        if child.type != "keyword_argument":
            continue
        parts = child.named_children
        if len(parts) == 2 and _text(src, parts[0]) == "methods":
            return [
                _unquote(_text(src, item)).upper()
                for item in parts[1].named_children
                if item.type in _STRING_TYPES
            ]
    return []


#: Receivers that never declare a route, however the call is spelled. `patch`
#: is `unittest.mock.patch`, whose first argument is a dotted target and whose
#: name collides with the PATCH method -- it produced `route:PATCH
#: /subprocess.run` from this repository's own test suite.
NON_ROUTE_RECEIVERS: frozenset[str] = frozenset({"mock", "unittest.mock", "mocker", "patch"})


def _decorator_parts(src: bytes, node: Node) -> tuple[str, str]:
    """`(receiver, name)` for a decorator callee: `@app.get` -> `("app", "get")`."""
    head = _text(src, node).strip().lstrip("@").split("(")[0].strip()
    head = head.rsplit("::", 1)[-1]
    receiver, _, name = head.rpartition(".")
    return receiver, name


def _decorator_name(src: bytes, node: Node) -> str:
    """The decorator's callee, last segment only: `@app.get` -> `get`."""
    head = _text(src, node).strip().lstrip("@").split("(")[0].strip()
    return head.rsplit(".", 1)[-1].rsplit("::", 1)[-1]


def route_from_decorator(src: bytes, node: Node, lang: str) -> list[dict[str, Any]]:
    """Route facts for one decorator node, or `[]` if it declares no route.

    A list because one decorator can declare several routes:
    `@app.route("/x", methods=["GET", "POST"])` is two, and they are two
    different things to an agent asking which handler serves a POST.

    The path is kept exactly as written -- `{uid}`, `<int:uid>` and `:id` are
    three frameworks' syntax for the same thing, and normalising them would
    make the edge's own evidence not match the source it cites.
    """
    call = _call_of(node)
    if call is None:
        return []
    receiver, name = _decorator_parts(src, node)
    method = HTTP_METHODS.get(name.lower())
    if method is None:
        return []
    # A bare lowercase HTTP-method call is not a route: every supported Python
    # framework declares on an object (`@app.get`, `@blueprint.route`), and
    # NestJS spells its bare decorators capitalised (`@Get`). Without this,
    # `unittest.mock`'s `@patch("pkg.mod.fn")` became `PATCH /pkg.mod.fn`.
    if not receiver and name.islower() and name not in ("route", "api_route"):
        return []
    if receiver.lower() in NON_ROUTE_RECEIVERS:
        return []
    path = _first_string_arg(call, src)
    if path is None:
        # `@app.post()` with no path is NestJS declaring the controller's own
        # mount point; the prefix alone is the route.
        path = ""
    line = node.start_point[0] + 1
    methods = [method] if method else (_methods_kwarg(call, src) or ["GET"])
    return [
        {
            "kind": "route",
            "method": m,
            "path": path,
            "decorator": name,
            "line": line,
            "lang": lang,
        }
        for m in methods
    ]


def mount_prefix(src: bytes, node: Node) -> str | None:
    """The path prefix a class-level mount decorator contributes, or None.

    `@Controller('users')` makes every route in the class sit under `users`.
    A mount decorator with no string argument still mounts, at no prefix, so
    `""` and `None` mean different things and both are returned faithfully.
    """
    receiver, name = _decorator_parts(src, node)
    if name.lower() not in MOUNT_DECORATORS:
        return None
    if receiver.lower() in NON_ROUTE_RECEIVERS:
        return None
    call = _call_of(node)
    if call is None:
        return ""
    return _first_string_arg(call, src) or ""


def join_path(prefix: str, path: str) -> str:
    """Join a mount prefix and a route path into the path the client calls."""
    left = (prefix or "").strip("/")
    right = (path or "").strip("/")
    joined = "/".join(p for p in (left, right) if p)
    return "/" + joined


def injected_dependencies(src: bytes, node: Node) -> list[dict[str, Any]]:
    """Names this definition's parameters inject, from `Depends(...)` defaults.

    Read off the parameter list rather than the signature *text* so that a
    default spanning lines, or one containing a comma, cannot be mis-split.
    Only a plain callable name is taken: `Depends(get_db)` resolves, and
    `Depends(Factory().build)` does not, because the thing being named is then
    an expression rather than a symbol this repository defines.
    """
    found: list[dict[str, Any]] = []
    params = None
    for child in node.named_children:
        if child.type in ("parameters", "formal_parameters"):
            params = child
            break
    if params is None:
        return found
    for param in params.named_children:
        for call in _descend_calls(param):
            if _decorator_name(src, call).lower() not in DI_MARKERS:
                continue
            args = _arg_list(call)
            if args is None:
                continue
            for arg in args.named_children:
                if arg.type in ("identifier", "attribute", "member_expression"):
                    found.append(
                        {
                            "kind": "inject",
                            "name": _text(src, arg).strip(),
                            "line": call.start_point[0] + 1,
                        }
                    )
                break
    return found


def _descend_calls(node: Node, depth: int = 0) -> list[Node]:
    """Calls inside `node`, bounded. A parameter default is a small tree."""
    if depth > 4:
        return []
    out = []
    for child in node.named_children:
        if child.type in _CALL_TYPES:
            out.append(child)
        out.extend(_descend_calls(child, depth + 1))
    return out


def model_relations(src: bytes, node: Node, lang: str) -> list[dict[str, Any]]:
    """Related-model names this class body declares, from its ORM fields.

    `author = models.ForeignKey("Author")` and
    `books = relationship("Book")` both name another model. The name is taken
    from the first argument whether it is a string (`"Author"`, including
    Django's `"app.Author"` form, whose last segment is the class) or a bare
    identifier (`ForeignKey(Author)`), and resolved later like a reference:
    same file, or imported by that name, never a repository-wide guess.

    `"self"` is Django's self-reference and is returned as such, so a
    self-referential model gets an honest self-edge rather than being dropped.
    """
    body = None
    for child in node.named_children:
        if child.type in ("block", "class_body", "declaration_list"):
            body = child
            break
    if body is None:
        return []
    out = []
    for stmt in body.named_children:
        for assign in _assignments(stmt):
            field_name, call = assign
            relation = ORM_RELATION_FIELDS.get(_decorator_name(src, call).lower())
            if relation is None:
                continue
            args = _arg_list(call)
            if args is None:
                continue
            target = None
            for arg in args.named_children:
                if arg.type in _STRING_TYPES:
                    target = _unquote(_text(src, arg)).rsplit(".", 1)[-1]
                    break
                if arg.type in ("identifier", "type_identifier"):
                    target = _text(src, arg).strip()
                    break
            if not target:
                continue
            out.append(
                {
                    "kind": "model",
                    "name": target,
                    "field": field_name,
                    "relation": relation,
                    "line": call.start_point[0] + 1,
                    "lang": lang,
                }
            )
    return out


def _assignments(stmt: Node) -> list[tuple[str, Node]]:
    """`(assigned name, the call it is assigned)` pairs inside one statement."""
    out = []
    candidates = [stmt]
    for child in stmt.named_children:
        candidates.append(child)
    for cand in candidates:
        if cand.type not in ("assignment", "public_field_definition", "field_definition"):
            continue
        name = ""
        call = None
        for part in cand.named_children:
            if part.type in ("identifier", "property_identifier"):
                if not name:
                    name = _text_of(part)
            elif part.type in _CALL_TYPES:
                call = part
        if name and call is not None:
            out.append((name, call))
    return out


def _text_of(node: Node) -> str:
    if node.text is not None:
        return node.text.decode("utf8", "replace")
    return ""


def function_parameters(src: bytes, node: Node) -> list[str]:
    """Parameter names declared on a function definition, excluding self/cls/*args/**kwargs."""
    params = None
    for child in node.named_children:
        if child.type in ("parameters", "formal_parameters"):
            params = child
            break
    if params is None:
        return []
    out = []
    for param in params.named_children:
        if param.type in ("list_splat_pattern", "dictionary_splat_pattern"):
            continue
        pname = ""
        if param.type == "identifier":
            pname = _text(src, param).strip()
        elif param.type in ("default_parameter", "typed_parameter", "typed_default_parameter"):
            for sub in param.named_children:
                if sub.type == "identifier":
                    pname = _text(src, sub).strip()
                    break
        if pname and pname not in ("self", "cls"):
            out.append(pname)
    return out


def extract_pytest_facts(src: bytes) -> dict[str, Any]:
    """Extract pytest fixture definitions, test functions, and test classes from Python source."""
    from tree_sitter_language_pack import get_parser

    parser = get_parser("python")
    tree = parser.parse(src)

    fixtures: list[dict[str, Any]] = []
    tests: list[dict[str, Any]] = []
    classes: dict[str, list[str]] = {}

    def _get_decorators(node: Node) -> list[Node]:
        if node.parent is not None and node.parent.type == "decorated_definition":
            return [c for c in node.parent.children if c.type == "decorator"]
        return []

    def _get_usefixtures(decorators: list[Node]) -> list[str]:
        usefixtures_names: list[str] = []
        for dec in decorators:
            receiver, name = _decorator_parts(src, dec)
            name_lower = name.lower()
            recv_lower = receiver.lower()
            if name_lower == "usefixtures" and (
                recv_lower in ("pytest.mark", "mark", "") or recv_lower.endswith(".mark")
            ):
                call = _call_of(dec)
                if call is not None:
                    args = _arg_list(call)
                    if args is not None:
                        for a in args.named_children:
                            if a.type in _STRING_TYPES:
                                usefixtures_names.append(_unquote(_text(src, a)))
        return usefixtures_names

    def walk(node: Node, enclosing_class: str | None = None) -> None:
        if node.type == "class_definition":
            cls_name = ""
            for child in node.named_children:
                if child.type == "identifier":
                    cls_name = _text(src, child).strip()
                    break
            decs = _get_decorators(node)
            uf = _get_usefixtures(decs)
            if uf and cls_name:
                classes[cls_name] = uf
            for child in node.named_children:
                walk(child, enclosing_class=cls_name or enclosing_class)
            return

        if node.type == "function_definition":
            fn_name = ""
            for child in node.named_children:
                if child.type == "identifier":
                    fn_name = _text(src, child).strip()
                    break

            decs = _get_decorators(node)
            fixture_dec: Node | None = None
            autouse = False
            custom_name: str | None = None

            for dec in decs:
                receiver, name = _decorator_parts(src, dec)
                name_lower = name.lower()
                recv_lower = receiver.lower()
                if name_lower == "fixture" and recv_lower in ("pytest", ""):
                    fixture_dec = dec
                    call = _call_of(dec)
                    if call is not None:
                        args = _arg_list(call)
                        if args is not None:
                            for a in args.named_children:
                                if a.type == "keyword_argument":
                                    parts = a.named_children
                                    if len(parts) == 2:
                                        kw = _text(src, parts[0]).strip()
                                        if kw == "autouse":
                                            autouse = _text(src, parts[1]).strip().lower() in (
                                                "true",
                                                "1",
                                            )
                                        elif kw == "name":
                                            custom_name = _unquote(_text(src, parts[1]))

            usefixtures_names = _get_usefixtures(decs)

            if fixture_dec is not None:
                fixtures.append(
                    {
                        "name": custom_name or fn_name,
                        "fn_name": fn_name,
                        "autouse": autouse,
                        "params": function_parameters(src, node),
                        "usefixtures": usefixtures_names,
                        "line": fixture_dec.start_point[0] + 1,
                        "start_line": node.start_point[0] + 1,
                    }
                )
            else:
                is_test = fn_name.startswith("test_") or (
                    enclosing_class is not None
                    and (enclosing_class.startswith("Test") or enclosing_class in classes)
                    and fn_name.startswith("test_")
                )
                if is_test:
                    tests.append(
                        {
                            "name": fn_name,
                            "parent": enclosing_class,
                            "params": function_parameters(src, node),
                            "usefixtures": usefixtures_names,
                            "line": node.start_point[0] + 1,
                            "start_line": node.start_point[0] + 1,
                        }
                    )

            for child in node.named_children:
                walk(child, enclosing_class=enclosing_class)
            return

        for child in node.named_children:
            walk(child, enclosing_class=enclosing_class)

    walk(tree.root_node)
    return {"fixtures": fixtures, "tests": tests, "classes": classes}
