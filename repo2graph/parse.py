# @authormark v1 -- do not remove (authorship watermark)⁠​​‌‌‌​​​​‌‌‌‌​​​​‌​​​‌‌‌​‌​‌‌‌‌‌​‌‌‌​​‌​​‌‌​‌‌‌‌​‌‌​​‌​​​‌​‌​‌‌‌​‌​‌​​​​​‌​‌​​​​​‌‌‌‌​​​​‌‌‌​​‌​​‌​‌​​​‌​‌​‌‌​‌​​‌​​‌‌​‌​‌​‌​​​​​‌​​​​‌​​‌​‌​‌‌‌​‌​​​​‌‌​‌‌​​‌‌‌​‌​‌​‌​​​‌‌‌​‌‌​⁠
# Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
# Author: https://github.com/Srinivasan-78
# SPDX-License-Identifier: MIT
# Fingerprint: AMK1.8xG_rodWPPxrQZMPBWCgTv
"""Discovery, language configs, and tree-sitter based symbol/call extraction."""
import os
import re
import stat as statmod
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

EXT_LANG = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".c": "c", ".h": "c",
    ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".cs": "csharp",
    ".php": "php",
    ".kt": "kotlin",
    ".scala": "scala",
    ".swift": "swift",
    ".sh": "bash", ".bash": "bash",
}

DOC_EXT = {".md", ".mdx", ".rst", ".txt", ".adoc"}
CONFIG_EXT = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"}

LANG_CFG = {
    "python": {
        "kind_map": {"function_definition": "function", "class_definition": "class"},
        "call_types": {"call"},
        "import_types": {"import_statement", "import_from_statement"},
        "doc": "python",
    },
    "javascript": {
        "kind_map": {
            "function_declaration": "function",
            "generator_function_declaration": "function",
            "method_definition": "method",
            "class_declaration": "class",
            "variable_declarator": "maybe_function",
        },
        "call_types": {"call_expression", "new_expression"},
        "import_types": {"import_statement", "export_statement"},
        "doc": "jsdoc",
    },
    "go": {
        "kind_map": {
            "function_declaration": "function",
            "method_declaration": "method",
            "type_spec": "type",
        },
        "call_types": {"call_expression"},
        "import_types": {"import_spec"},
        "doc": "line",
    },
    "rust": {
        "kind_map": {
            "function_item": "function", "struct_item": "struct", "enum_item": "enum",
            "trait_item": "trait", "impl_item": "impl", "mod_item": "module",
        },
        "call_types": {"call_expression", "macro_invocation"},
        "import_types": {"use_declaration"},
        "doc": "line",
    },
    "java": {
        "kind_map": {
            "method_declaration": "method", "constructor_declaration": "method",
            "class_declaration": "class", "interface_declaration": "interface",
            "enum_declaration": "enum",
        },
        "call_types": {"method_invocation", "object_creation_expression"},
        "import_types": {"import_declaration"},
        "doc": "jsdoc",
    },
    "ruby": {
        "kind_map": {"method": "method", "singleton_method": "method",
                     "class": "class", "module": "module"},
        "call_types": {"call"},
        "import_types": set(),
        "doc": "line",
    },
    "c": {
        "kind_map": {"function_definition": "function", "struct_specifier": "struct",
                     "enum_specifier": "enum"},
        "call_types": {"call_expression"},
        "import_types": {"preproc_include"},
        "doc": "line",
    },
    "csharp": {
        "kind_map": {"method_declaration": "method", "class_declaration": "class",
                     "interface_declaration": "interface", "struct_declaration": "struct"},
        "call_types": {"invocation_expression", "object_creation_expression"},
        "import_types": {"using_directive"},
        "doc": "line",
    },
    "php": {
        "kind_map": {"function_definition": "function", "method_declaration": "method",
                     "class_declaration": "class", "interface_declaration": "interface"},
        "call_types": {"function_call_expression", "member_call_expression", "object_creation_expression"},
        "import_types": {"namespace_use_declaration"},
        "doc": "jsdoc",
    },
    "kotlin": {
        "kind_map": {"function_declaration": "function", "class_declaration": "class",
                     "object_declaration": "object"},
        "call_types": {"call_expression"},
        "import_types": {"import_header"},
        "doc": "jsdoc",
    },
    "swift": {
        "kind_map": {"function_declaration": "function", "class_declaration": "class",
                     "protocol_declaration": "protocol"},
        "call_types": {"call_expression"},
        "import_types": {"import_declaration"},
        "doc": "line",
    },
    "scala": {
        "kind_map": {"function_definition": "function", "class_definition": "class",
                     "object_definition": "object", "trait_definition": "trait"},
        "call_types": {"call_expression"},
        "import_types": {"import_declaration"},
        "doc": "line",
    },
    "bash": {
        "kind_map": {"function_definition": "function"},
        "call_types": {"command"},
        "import_types": set(),
        "doc": "line",
    },
}
LANG_CFG["typescript"] = dict(LANG_CFG["javascript"])
LANG_CFG["typescript"]["kind_map"] = dict(
    LANG_CFG["javascript"]["kind_map"],
    interface_declaration="interface", type_alias_declaration="type",
    enum_declaration="enum", abstract_class_declaration="class",
)
LANG_CFG["tsx"] = LANG_CFG["typescript"]
LANG_CFG["cpp"] = dict(LANG_CFG["c"])
LANG_CFG["cpp"]["kind_map"] = dict(LANG_CFG["c"]["kind_map"],
                                   class_specifier="class", namespace_definition="namespace")

DEFAULT_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "venv", ".venv", "env", "__pycache__",
    "dist", "build", "target", ".next", ".nuxt", "vendor", ".idea", ".vscode",
    "site-packages", ".mypy_cache", ".pytest_cache", ".tox", "coverage", ".terraform",
    ".ruff_cache", ".eggs", ".cache", ".gradle", ".direnv", ".yarn",
}
MAX_BYTES = 1_500_000


def _git_files(root: Path):
    try:
        out = subprocess.run(
            ["git", "-c", "core.quotepath=false", "-C", str(root),
             "ls-files", "-z", "-co", "--exclude-standard"],
            capture_output=True, timeout=60,
        )
        if out.returncode != 0:
            return None
        names = out.stdout.decode("utf8", "surrogateescape").split("\0")
        return [root / p for p in names if p]
    except (OSError, subprocess.SubprocessError):
        return None


def _walk_files(root: Path):
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in DEFAULT_SKIP_DIRS]
        for fn in filenames:
            files.append(Path(dirpath) / fn)
    return files


@lru_cache(maxsize=512)
def _glob_re(pattern: str) -> re.Pattern:
    pat = pattern.strip("/")
    out = [] if "/" in pat else ["(?:[^/]+/)*"]
    i = 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        elif pat[i] == "[":
            j = pat.find("]", i + 1)
            cls = pat[i + 1:j].replace("\\", "\\\\") if j != -1 else ""
            body = ("^/" + cls[1:]) if cls.startswith("!") else cls
            if j == -1 or body in ("", "^", "^/"):
                out.append(re.escape(pat[i]))
                i += 1
            else:
                out.append("[" + body + "]")
                i = j + 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def matches_any(rel: str, patterns) -> bool:
    return any(_glob_re(p).match(rel) for p in patterns)


def is_binary(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return b"\0" in fh.read(4096)
    except OSError:
        return True


def discover(root: Path, include_globs=None, exclude_globs=None, stats=None):
    """Yield (relative_path, absolute_path) for candidate source files."""
    root = root.resolve()
    files = _git_files(root)
    if files is not None:
        if stats is not None:
            stats["discovery"] = "git"
    else:
        files = _walk_files(root)
        if stats is not None:
            stats["discovery"] = "walk"
    for abspath in files:
        try:
            rel = abspath.relative_to(root)
        except ValueError:
            continue
        if any(part in DEFAULT_SKIP_DIRS for part in rel.parts):
            continue
        try:
            st = abspath.lstat()
        except OSError:
            continue
        if not statmod.S_ISREG(st.st_mode) or st.st_size > MAX_BYTES:
            continue
        rp = rel.as_posix()
        if include_globs and not matches_any(rp, include_globs):
            continue
        if exclude_globs and matches_any(rp, exclude_globs):
            continue
        if is_binary(abspath):
            continue
        yield rp, abspath

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
    except (LookupError, ValueError, ImportError, AttributeError):
        return None


@dataclass
class Symbol:
    name: str
    qualname: str
    kind: str
    start_line: int
    end_line: int
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
    # "method": Ruby's `call` node keeps the receiver and the method in separate
    # fields, so named_children[0] is the receiver — `logger.info(x)` would be
    # recorded as a call to `logger`. Read the method field directly instead.
    # "macro": Rust `macro_invocation` nodes expose the macro name in `macro`.
    fn = (node.child_by_field_name("function") or node.child_by_field_name("name")
          or node.child_by_field_name("method") or node.child_by_field_name("constructor")
          or node.child_by_field_name("macro") or node.child_by_field_name("type"))
    if fn is None:
        if node.named_child_count:
            fn = node.named_children[0]
        else:
            return None
    txt = _text(src, fn).strip()
    if not txt:
        return None
    # Strip wrapping parens for function-pointer / expression invocations e.g. (*fn)(arg) or (cb)(arg)
    while txt.startswith("(") and txt.endswith(")") and len(txt) >= 2:
        txt = txt[1:-1].strip()
    txt = txt.split("(")[0].split("<")[0]
    for sep in ("::", ".", "->"):
        if sep in txt:
            txt = txt.split(sep)[-1]
    # ISS-05: Strip only leading pointer/deref and trailing macro !
    txt = txt.strip().lstrip("*& \t\n").removesuffix("!").strip()
    return txt or None


_ATTR_OR_COMMENT_TYPES = (
    "comment", "line_comment", "block_comment", "doc_comment",
    "attribute_item", "attribute", "decorator", "annotation",
)
_COMMENT_TYPES = ("comment", "line_comment", "block_comment", "doc_comment")


def _docstring(src: bytes, node, lang: str) -> str:
    if lang == "python":
        body = node.child_by_field_name("body")
        if body is not None and body.named_child_count:
            first = body.named_children[0]
            # functions wrap the docstring in an expression_statement, classes do not
            if first.type == "expression_statement" and first.named_child_count:
                first = first.named_children[0]
            if first.type == "string":
                # ISS-03: Strip only the matching outer quote delimiter (handling r/u/b prefixes)
                raw = _text(src, first).strip()
                pfx = 0
                while pfx < len(raw) and raw[pfx] in "rRuUbB":
                    pfx += 1
                body_txt = raw[pfx:]
                for q in ('"""', "'''", '"', "'"):
                    if body_txt.startswith(q) and body_txt.endswith(q) and len(body_txt) >= 2 * len(q):
                        body_txt = body_txt[len(q):-len(q)]
                        break
                return body_txt.strip()[:600]
        return ""
    # otherwise: comment lines immediately above the definition, skipping attributes/annotations
    out, prev = [], node.prev_sibling
    while prev is not None and prev.type in _ATTR_OR_COMMENT_TYPES:
        if prev.type in _COMMENT_TYPES:
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
        return ParsedFile(lang=lang, symbols=[], imports=[])
    tree = parser.parse(source)
    kind_map, call_types, import_types = cfg["kind_map"], cfg["call_types"], cfg["import_types"]
    symbols: list[Symbol] = []
    imports: list[str] = []
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
            # file-scope calls (owner is None) produce no edge in graph.build,
            # so drop them here rather than accumulating dead data (ISS-02).
            if callee and owner is not None:
                owner.calls.append(callee)
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
                      parse_errors=errors)
