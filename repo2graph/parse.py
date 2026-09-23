"""Discovery, language configs, and tree-sitter based symbol/call extraction."""

import os
import re
import stat as statmod
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TypedDict, cast

from tree_sitter import Node, Parser


class ParseError(RuntimeError):
    """Raised when parser strictness policy encounters parse errors."""

    pass


@dataclass
class ImportDetail:
    """Structured information about an import statement."""

    raw: str
    module: str
    name: str | None = None
    alias: str | None = None


EXT_LANG = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".mts": "typescript",
    ".cts": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".cs": "csharp",
    ".php": "php",
    ".kt": "kotlin",
    ".scala": "scala",
    ".swift": "swift",
    ".sh": "bash",
    ".bash": "bash",
    ".lua": "lua",
}

DOC_EXT = {".md", ".mdx", ".rst", ".txt", ".adoc"}
CONFIG_EXT = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"}


class LangConfig(TypedDict):
    kind_map: dict[str, str]
    call_types: set[str]
    import_types: set[str]
    doc: str


LANG_CFG: dict[str, LangConfig] = {
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
            "function_item": "function",
            "struct_item": "struct",
            "enum_item": "enum",
            "trait_item": "trait",
            "impl_item": "impl",
            "mod_item": "module",
        },
        "call_types": {"call_expression", "macro_invocation"},
        "import_types": {"use_declaration"},
        "doc": "line",
    },
    "java": {
        "kind_map": {
            "method_declaration": "method",
            "constructor_declaration": "method",
            "class_declaration": "class",
            "interface_declaration": "interface",
            "enum_declaration": "enum",
        },
        "call_types": {"method_invocation", "object_creation_expression"},
        "import_types": {"import_declaration"},
        "doc": "jsdoc",
    },
    "ruby": {
        "kind_map": {
            "method": "method",
            "singleton_method": "method",
            "class": "class",
            "module": "module",
        },
        "call_types": {"call"},
        "import_types": set(),
        "doc": "line",
    },
    "c": {
        "kind_map": {
            "function_definition": "function",
            "struct_specifier": "struct",
            "enum_specifier": "enum",
        },
        "call_types": {"call_expression"},
        "import_types": {"preproc_include"},
        "doc": "line",
    },
    "csharp": {
        "kind_map": {
            "method_declaration": "method",
            "class_declaration": "class",
            "interface_declaration": "interface",
            "struct_declaration": "struct",
        },
        "call_types": {"invocation_expression", "object_creation_expression"},
        "import_types": {"using_directive"},
        "doc": "line",
    },
    "php": {
        "kind_map": {
            "function_definition": "function",
            "method_declaration": "method",
            "class_declaration": "class",
            "interface_declaration": "interface",
        },
        "call_types": {
            "function_call_expression",
            "member_call_expression",
            "object_creation_expression",
        },
        "import_types": {"namespace_use_declaration"},
        "doc": "jsdoc",
    },
    "kotlin": {
        "kind_map": {
            "function_declaration": "function",
            "class_declaration": "class",
            "object_declaration": "object",
        },
        "call_types": {"call_expression"},
        "import_types": {"import_header"},
        "doc": "jsdoc",
    },
    "swift": {
        "kind_map": {
            "function_declaration": "function",
            "class_declaration": "class",
            "protocol_declaration": "protocol",
        },
        "call_types": {"call_expression"},
        "import_types": {"import_declaration"},
        "doc": "line",
    },
    "scala": {
        "kind_map": {
            "function_definition": "function",
            "class_definition": "class",
            "object_definition": "object",
            "trait_definition": "trait",
        },
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
    "lua": {
        "kind_map": {"function_declaration": "function"},
        "call_types": {"function_call"},
        "import_types": set(),
        "doc": "line",
    },
}
LANG_CFG["typescript"] = cast(LangConfig, dict(LANG_CFG["javascript"]))
LANG_CFG["typescript"]["kind_map"] = dict(
    LANG_CFG["javascript"]["kind_map"],
    interface_declaration="interface",
    type_alias_declaration="type",
    enum_declaration="enum",
    abstract_class_declaration="class",
)
LANG_CFG["tsx"] = LANG_CFG["typescript"]
LANG_CFG["cpp"] = cast(LangConfig, dict(LANG_CFG["c"]))
LANG_CFG["cpp"]["kind_map"] = dict(
    LANG_CFG["c"]["kind_map"], class_specifier="class", namespace_definition="namespace"
)

# Callee names that mean a dynamic/reflective call, scoped per language. A bare
# "send", "call" or "apply" is an ordinary method name in most codebases
# (queue.send, handler.call, ...), so this must never be one global set --
# `_callee_name` reduces `queue.send(msg)` to "send" regardless of language.
DYNAMIC_CALLEES: dict[str, frozenset[str]] = {
    "python": frozenset({"getattr", "setattr", "eval", "exec", "__import__"}),
    "javascript": frozenset({"eval", "apply", "call"}),
    "typescript": frozenset({"eval", "apply", "call"}),
    "tsx": frozenset({"eval", "apply", "call"}),
    "ruby": frozenset({"send", "public_send", "instance_eval", "eval"}),
    "php": frozenset({"call_user_func", "call_user_func_array", "eval"}),
}

DEFAULT_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "venv",
    ".venv",
    "env",
    "__pycache__",
    "dist",
    "build",
    "target",
    ".next",
    ".nuxt",
    "vendor",
    ".idea",
    ".vscode",
    "site-packages",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    "coverage",
    ".terraform",
    ".ruff_cache",
    ".eggs",
    ".cache",
    ".gradle",
    ".direnv",
    ".yarn",
}
MAX_BYTES = 1_500_000


@dataclass
class BuildConfig:
    max_file_bytes: int = 1_500_000
    extra_exclude_dirs: list[str] = field(default_factory=list)
    include_vendor: bool = False
    chunk_large_files: bool = False
    max_nodes: int = 0
    include_secrets: bool = False
    secret_policy: str = "redact-match"
    extra_secret_keywords: list[str] = field(default_factory=list)
    extra_secret_dirs: list[str] = field(default_factory=list)
    parse_policy: str = "best-effort"


def _git_files(root: Path):
    try:
        # stdin=DEVNULL: capture_output redirects the child's stdout and stderr
        # only, so without this git inherits *our* stdin. Under the MCP server
        # that handle is the client's JSON-RPC pipe: git blocks reading it until
        # the timeout below fires -- a 60s stall before the silent _walk_files
        # fallback -- and a child holding that pipe can swallow frames meant for
        # us. Nothing here ever has anything to say to git on stdin.
        out = subprocess.run(
            [
                "git",
                "-c",
                "core.quotepath=false",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "-co",
                "--exclude-standard",
            ],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
        if out.returncode != 0:
            return None
        names = out.stdout.decode("utf8", "surrogateescape").split("\0")
        return [root / p for p in names if p]
    except (OSError, subprocess.SubprocessError):
        return None


def _walk_files(root: Path, skip_dirs=None):
    if skip_dirs is None:
        skip_dirs = DEFAULT_SKIP_DIRS
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
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
            cls = pat[i + 1 : j].replace("\\", "\\\\") if j != -1 else ""
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


def _count_gitignored(root: Path) -> int:
    """How many untracked files .gitignore (or another exclude-standard rule)
    kept out of _git_files' listing.

    Purely a count for the human overview's "what was skipped" section --
    `_git_files` already applies `--exclude-standard` itself, so these files
    never reach `discover()`'s loop below and this never changes what is
    yielded. Same subprocess pattern as `_git_files`: quotepath=false, bytes
    decoded with surrogateescape (never text=True -- see AGENTS.md), bounded
    timeout.
    """
    try:
        out = subprocess.run(
            [
                "git",
                "-c",
                "core.quotepath=false",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--others",
                "--ignored",
                "--exclude-standard",
            ],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
        if out.returncode != 0:
            return 0
        names = out.stdout.decode("utf8", "surrogateescape").split("\0")
        return sum(1 for n in names if n)
    except (OSError, subprocess.SubprocessError):
        return 0


def discover(
    root: Path,
    include_globs=None,
    exclude_globs=None,
    stats=None,
    config: BuildConfig | None = None,
):
    """Yield (relative_path, absolute_path) for candidate source files."""
    if config is None:
        config = BuildConfig()
    skip_dirs = set(DEFAULT_SKIP_DIRS) | set(config.extra_exclude_dirs)
    if config.include_vendor:
        skip_dirs.discard("vendor")

    root = root.resolve()
    files = _git_files(root)
    if files is not None:
        if stats is not None:
            stats["discovery"] = "git"
            stats["skipped_gitignore"] = _count_gitignored(root)
    else:
        files = _walk_files(root, skip_dirs=skip_dirs)
        if stats is not None:
            stats["discovery"] = "walk"
    for abspath in files:
        try:
            rel = abspath.relative_to(root)
        except ValueError:
            continue
        # A skip_dirs hit is either a hidden/dot directory (.git, .idea, ...)
        # or a vendor/build directory (node_modules, dist, target, ...); tell
        # the two apart for the human overview's "what was skipped" section,
        # without changing which paths get skipped.
        skip_part = next((part for part in rel.parts if part in skip_dirs), None)
        if skip_part is not None:
            if stats is not None:
                key = "skipped_dotfile" if skip_part.startswith(".") else "skipped_vendor"
                stats[key] += 1
            continue
        # BuildLock (lock.py) deliberately places its lock file as a *sibling*
        # of the output directory, not inside it, so the file survives the
        # transactional dir-swap in export.py's dump_all. That means a build
        # whose outdir lives directly under the scanned root sees its own
        # in-progress lock file on disk -- skip it like any other dotfile
        # rather than indexing a "r2glock"-language node for it.
        if abspath.name.startswith(".") and abspath.name.endswith(".r2glock"):
            if stats is not None:
                stats["skipped_dotfile"] += 1
            continue
        try:
            st = abspath.lstat()
        except OSError:
            continue
        # Two independent rejections, deliberately not one branch. When they
        # shared a condition the `chunk_large_files` escape hatch dropped out of
        # *both*, so an entry that was non-regular AND over max_file_bytes
        # skipped the S_ISREG check and was yielded into _chunk_and_parse ->
        # _safe_open -> a blocking read() on a FIFO or device. A size flag must
        # never be able to waive the file-type check; splitting them also ties
        # skipped_too_large to the reason the path was actually skipped.
        if not statmod.S_ISREG(st.st_mode):
            continue
        if st.st_size > config.max_file_bytes and not config.chunk_large_files:
            if stats is not None:
                stats["skipped_too_large"] += 1
            continue
        rp = rel.as_posix()
        if not config.include_secrets:
            from .secrets import _is_secret_path

            if _is_secret_path(
                rp,
                extra_keywords=config.extra_secret_keywords,
                extra_dirs=config.extra_secret_dirs,
            ):
                if stats is not None:
                    stats["skipped_secret"] += 1
                continue
        if include_globs and not matches_any(rp, include_globs):
            continue
        if exclude_globs and matches_any(rp, exclude_globs):
            continue
        if is_binary(abspath):
            if stats is not None:
                stats["skipped_binary"] += 1
            continue
        yield rp, abspath


_get_parser: Callable[[str], Parser] | None
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
    call_details: list[dict] = field(default_factory=list)
    base_details: list[dict] = field(default_factory=list)


@dataclass
class ParsedFile:
    lang: str
    symbols: list[Symbol]
    imports: list[str]
    parse_errors: int = 0
    used_cpp: bool = False
    is_chunked: bool = False
    import_details: list[ImportDetail] = field(default_factory=list)


def _text(src: bytes, node) -> str:
    return src[node.start_byte : node.end_byte].decode("utf8", "replace")


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
    fn = (
        node.child_by_field_name("function")
        or node.child_by_field_name("name")
        or node.child_by_field_name("method")
        or node.child_by_field_name("constructor")
        or node.child_by_field_name("macro")
        or node.child_by_field_name("type")
    )
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
    # ISS-159: resolve the rightmost member-access segment *before* stripping
    # "(" / "<" noise. A chained call's `function` field text is the whole
    # member expression, e.g. `obj.get_user().save` for `obj.get_user().save()`
    # — the "(" that closes the inner `get_user()` call sits in the middle of
    # that string. Splitting on "(" first (old order) chopped everything from
    # that "(" onward, including the outer ".save", and left "obj.get_user".
    # Splitting on the separator first isolates "save" so the parens/generic
    # stripping below only ever runs on the final, already-resolved segment.
    for sep in ("::", ".", "->"):
        if sep in txt:
            txt = txt.split(sep)[-1]
    txt = txt.split("(")[0].split("<")[0]
    # ISS-05: Strip only leading pointer/deref and trailing macro !
    txt = txt.strip().lstrip("*& \t\n").removesuffix("!").strip()
    return txt or None


_ATTR_OR_COMMENT_TYPES = (
    "comment",
    "line_comment",
    "block_comment",
    "doc_comment",
    "attribute_item",
    "attribute",
    "decorator",
    "annotation",
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
                    if (
                        body_txt.startswith(q)
                        and body_txt.endswith(q)
                        and len(body_txt) >= 2 * len(q)
                    ):
                        body_txt = body_txt[len(q) : -len(q)]
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
    return src[node.start_byte : end].decode("utf8", "replace").strip()[:300]


# Node types that hold a class's supertypes. Grammars differ: some expose them
# through a field, others only as an unnamed child clause.
_BASE_NODES = {
    "class_heritage",
    "extends_clause",
    "implements_clause",
    "superclass",
    "super_interfaces",
    "type_list",
    "base_list",
    "base_clause",
    "class_interface_clause",
    "delegation_specifier",
    "inheritance_specifier",
    "base_class_clause",
}
# Keywords and access specifiers that sit inside those clauses.
_BASE_WORDS = {
    "extends",
    "implements",
    "with",
    "public",
    "private",
    "protected",
    "internal",
    "virtual",
    "open",
    "abstract",
    "final",
    "sealed",
    "override",
    "case",
    "class",
    "interface",
    "struct",
    "typename",
}


def _split_bases(text: str) -> list[str]:
    """Split a base-class list on top-level commas only.

    A generic's type arguments (`Generic[T, U]`, `Handler<Request, Response>`)
    contain commas that must not split the base list itself, so track bracket
    depth and only split where it is zero.
    """
    opens, closes = "([<", ")]>"
    parts = []
    depth = 0
    start = 0
    for i, ch in enumerate(text):
        if ch in opens:
            depth += 1
        elif ch in closes:
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts


def _clean_base(text: str) -> str:
    """'public B', 'extends B', '< B' -> 'B'."""
    words = [
        w
        for w in text.replace(":", " ").replace("<", " <").split()
        if w and w not in _BASE_WORDS and w not in ("<", ">", "&", "*", ",")
    ]
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


def _bases_with_details(src: bytes, node, lang: str) -> tuple[list[str], list[dict]]:
    """Extract supertype names along with their relationship subtype and raw expression."""
    out: list[str] = []
    details: list[dict] = []
    for fname in ("superclasses", "bases", "trait"):
        n = node.child_by_field_name(fname)
        if n is not None:
            raw_text = _text(src, n).strip("(): ")
            for t in _split_bases(raw_text):
                clean = t.strip()
                if clean:
                    out.append(clean)
                    details.append({"name": clean, "subtype": "INHERITS", "raw": t})
    for clause in _base_clauses(node):
        ctype = clause.type
        ptype = clause.parent.type if clause.parent is not None else ""
        raw = _text(src, clause)
        default_sub = "INHERITS"
        if ctype in (
            "implements_clause",
            "super_interfaces",
            "class_interface_clause",
        ) or ptype in ("implements_clause", "super_interfaces", "class_interface_clause"):
            default_sub = "IMPLEMENTS"
        elif ctype in ("extends_clause", "superclass", "base_class_clause") or ptype in (
            "extends_clause",
            "superclass",
            "base_class_clause",
        ):
            default_sub = "EXTENDS"

        # Check for scala "with" mixin or comma separated clauses
        if " with " in raw:
            parts = raw.split(" with ")
            first_c = _clean_base(parts[0])
            if first_c:
                out.append(first_c)
                details.append({"name": first_c, "subtype": default_sub, "raw": parts[0]})
            for p in parts[1:]:
                mix_c = _clean_base(p)
                if mix_c:
                    out.append(mix_c)
                    details.append({"name": mix_c, "subtype": "MIXES_IN", "raw": p})
        else:
            raw_clean = raw.replace(" with ", ",")
            for t in _split_bases(raw_clean):
                clean = _clean_base(t)
                if clean:
                    out.append(clean)
                    details.append({"name": clean, "subtype": default_sub, "raw": t})
    seen: set[str] = set()
    uniq_names: list[str] = []
    uniq_details: list[dict] = []
    for b, d in zip(out, details):
        if b not in seen:
            seen.add(b)
            uniq_names.append(b)
            uniq_details.append(d)
    return uniq_names[:8], uniq_details[:8]


_cpp_available_cache: bool | None = None


def _cpp_available() -> bool:
    """Is a usable `cpp` on PATH? Memoizing only a successful probe.

    `cpp --version` answers the same thing for the life of the process, but it
    used to be re-run per erroring C/C++ file -- two spawns per file instead of
    one, which dominates the macro-aware retry's cost on a large C tree and on
    Windows, where spawn is expensive and parse_all may be fanning this out
    across a ProcessPoolExecutor.

    Same shape as fetch._git_version, for the same reason: a transient failure
    (fd exhaustion, fork failure, ...) must not be cached forever, or one bad
    moment disables the cpp fallback for the rest of the process's life.
    functools.lru_cache would cache exactly that, so this is a module global
    with an explicit None sentinel -- only a successful probe is memoized, a
    failed probe is retried on the next call.

    stdin=DEVNULL for the reason spelled out at _git_files above: capture_output
    redirects the child's stdout and stderr only, so cpp would otherwise inherit
    our stdin, which under the MCP server is the client's JSON-RPC pipe.
    """
    global _cpp_available_cache
    if _cpp_available_cache:
        return True
    try:
        subprocess.run(
            ["cpp", "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    _cpp_available_cache = True
    return True


def parse_import_details(raw: str, lang: str) -> list[ImportDetail]:
    """Extract structured import metadata (modules, names, aliases)."""
    raw_clean = raw.strip()
    if not raw_clean:
        return []

    details: list[ImportDetail] = []
    if lang == "python":
        # from ... import ...
        m = re.match(r"^from\s+(\.*[\w.]*)\s+import\s+([\w\s,*()]+)", raw_clean)
        if m:
            module = m.group(1) or ""
            names_part = m.group(2).replace("(", " ").replace(")", " ")
            for p in names_part.split(","):
                p = p.strip()
                if not p:
                    continue
                if " as " in p:
                    orig, alias = p.split(" as ", 1)
                    orig = orig.strip()
                    alias = alias.strip()
                else:
                    orig = p
                    alias = None
                if orig:
                    details.append(
                        ImportDetail(
                            raw=raw_clean,
                            module=module,
                            name=orig,
                            alias=alias,
                        )
                    )
            return details
        # import a as b, c as d
        m2 = re.match(r"^import\s+([\w\.,\s]+)", raw_clean)
        if m2:
            for p in m2.group(1).split(","):
                p = p.strip()
                if not p:
                    continue
                if " as " in p:
                    orig, alias = p.split(" as ", 1)
                    orig = orig.strip()
                    alias = alias.strip()
                else:
                    orig = p
                    alias = None
                if orig:
                    details.append(
                        ImportDetail(
                            raw=raw_clean,
                            module=orig,
                            name=orig,
                            alias=alias,
                        )
                    )
            return details

    elif lang in ("javascript", "typescript", "tsx"):
        m = re.search(r"""(?:from\s+)?['"]([^'"]+)['"]""", raw_clean)
        module = m.group(1) if m else ""
        named_m = re.search(r"\{([^}]+)\}", raw_clean)
        if named_m:
            for p in named_m.group(1).split(","):
                p = p.strip()
                if not p:
                    continue
                if " as " in p:
                    orig, alias = p.split(" as ", 1)
                    orig = orig.strip()
                    alias = alias.strip()
                else:
                    orig = p
                    alias = None
                if orig:
                    details.append(
                        ImportDetail(
                            raw=raw_clean,
                            module=module,
                            name=orig,
                            alias=alias,
                        )
                    )
        ns_m = re.search(r"\*\s+as\s+(\w+)", raw_clean)
        if ns_m:
            details.append(
                ImportDetail(
                    raw=raw_clean,
                    module=module,
                    name="*",
                    alias=ns_m.group(1),
                )
            )
        def_m = re.match(r"^import\s+(\w+)\s+from", raw_clean)
        if def_m:
            details.append(
                ImportDetail(
                    raw=raw_clean,
                    module=module,
                    name="default",
                    alias=def_m.group(1),
                )
            )
        if not details and module:
            details.append(
                ImportDetail(
                    raw=raw_clean,
                    module=module,
                    name=None,
                    alias=None,
                )
            )
        return details

    elif lang == "go":
        m = re.search(r"""(?:(\w+|\.)\s+)?['"]([^'"]+)['"]""", raw_clean)
        if m:
            alias = m.group(1)
            module = m.group(2)
            details.append(
                ImportDetail(
                    raw=raw_clean,
                    module=module,
                    alias=alias,
                )
            )
            return details

    elif lang in ("java", "kotlin", "scala"):
        m = re.search(r"import\s+(?:static\s+)?([\w\.\*]+)", raw_clean)
        if m:
            full = m.group(1)
            parts = full.rsplit(".", 1)
            if len(parts) == 2:
                module, name = parts
            else:
                module, name = full, None
            details.append(
                ImportDetail(
                    raw=raw_clean,
                    module=module,
                    name=name,
                )
            )
            return details

    elif lang in ("csharp", "php"):
        # C# writes `alias = target` (using Foo = Bar.Baz); PHP writes
        # `target as alias` (use Foo\Bar as Baz) -- the alias sits on opposite
        # sides of the statement, so each language needs its own pattern.
        body = re.sub(r"^(?:using|use)\s+(?:function\s+|const\s+|static\s+)?", "", raw_clean)
        body = body.rstrip(";").strip()
        if lang == "csharp":
            m_eq = re.match(r"^(\w+)\s*=\s*([\w.\\]+)$", body)
            module = m_eq.group(2) if m_eq else body
            alias = m_eq.group(1) if m_eq else None
        else:
            m_as = re.match(r"^([\w.\\]+)\s+as\s+(\w+)$", body)
            module = m_as.group(1) if m_as else body
            alias = m_as.group(2) if m_as else None
        details.append(
            ImportDetail(
                raw=raw_clean,
                module=module,
                name=module.split("\\")[-1].split(".")[-1],
                alias=alias,
            )
        )
        return details

    elif lang in ("c", "cpp"):
        m = re.search(r"""([<"])([^>"]+)[>"]""", raw_clean)
        if m:
            module = m.group(2)
            details.append(
                ImportDetail(
                    raw=raw_clean,
                    module=module,
                )
            )
            return details

    elif lang == "rust":
        m = re.search(r"use\s+([\w:]+)(?:::\{([^}]+)\})?(?:\s+as\s+(\w+))?", raw_clean)
        if m:
            base_mod = m.group(1)
            group_items = m.group(2)
            alias = m.group(3)
            if group_items:
                for item in group_items.split(","):
                    item = item.strip()
                    if not item:
                        continue
                    if " as " in item:
                        orig, it_alias = item.split(" as ", 1)
                        orig = orig.strip()
                        it_alias = it_alias.strip()
                    else:
                        orig = item
                        it_alias = None
                    details.append(
                        ImportDetail(
                            raw=raw_clean,
                            module=base_mod,
                            name=orig,
                            alias=it_alias,
                        )
                    )
            else:
                parts = base_mod.rsplit("::", 1)
                mod_name = parts[0] if len(parts) == 2 else base_mod
                sym_name = parts[1] if len(parts) == 2 else None
                details.append(
                    ImportDetail(
                        raw=raw_clean,
                        module=mod_name,
                        name=sym_name,
                        alias=alias,
                    )
                )
            return details

    elif lang == "swift":
        m = re.match(
            r"^(?:@\w+\s+)?import\s+(?:(?:typealias|struct|class|enum|protocol|let|var|func)\s+)?([\w.]+)",
            raw_clean,
        )
        if m:
            mod = m.group(1)
            name = mod.rsplit(".", 1)[-1] if "." in mod else None
            details.append(ImportDetail(raw=raw_clean, module=mod, name=name))
            return details

    elif lang == "ruby":
        m = re.search(r"""(?:require|require_relative|load)\s*\(?\s*['"]([^'"]+)['"]""", raw_clean)
        if m:
            mod = m.group(1)
            details.append(ImportDetail(raw=raw_clean, module=mod, name=mod.rsplit("/", 1)[-1]))
            return details

    elif lang == "bash":
        m = re.match(r"""^(?:source|\.)\s+['"]?([^'"\s]+)['"]?""", raw_clean)
        if m:
            mod = m.group(1)
            details.append(ImportDetail(raw=raw_clean, module=mod, name=mod.rsplit("/", 1)[-1]))
            return details

    # Fallback default
    details.append(ImportDetail(raw=raw_clean, module=raw_clean))
    return details


def parse_source(source: bytes, lang: str, filepath: Path | str | None = None) -> ParsedFile:
    cfg = LANG_CFG.get(lang)
    parser = parser_for(lang)
    if cfg is None or parser is None:
        return ParsedFile(lang=lang, symbols=[], imports=[])
    tree = parser.parse(source)

    def _count_errors(node):
        errs = 0
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type == "ERROR":
                errs += 1
            stack.extend(n.children)
        return errs

    errors = _count_errors(tree.root_node)
    used_cpp = False

    if errors > 0 and lang in ("c", "cpp") and filepath is not None:
        try:
            if Path(filepath).suffix.lower() in (".c", ".cc", ".cpp", ".h", ".hpp") and (
                _cpp_available()
            ):
                try:
                    out = subprocess.run(
                        ["cpp", "-w", "-P", "-undef", str(filepath)],
                        stdin=subprocess.DEVNULL,
                        capture_output=True,
                        timeout=10,
                    )
                    if out.returncode == 0:
                        # Never pass text=True to a subprocess reading git/cpp output on
                        # Windows -- it decodes with the cp1252 locale and raises
                        # UnicodeDecodeError on UTF-8 source. Capture raw bytes instead;
                        # tree-sitter's parser.parse() wants bytes anyway (AGENTS.md).
                        cpp_bytes = out.stdout
                        if len(cpp_bytes) <= 2 * len(source):
                            cpp_tree = parser.parse(cpp_bytes)
                            cpp_errors = _count_errors(cpp_tree.root_node)
                            if cpp_errors < errors:
                                # ISS-126 (approach a): do not adopt cpp_bytes
                                # or cpp_tree. cpp is invoked with -P, which
                                # strips `# <linenum> "<file>"` markers, so
                                # preprocessed row numbers cannot be mapped
                                # back to the on-disk file. chunks.py always
                                # slices the original, and storing cpp rows
                                # desyncs every citation. used_cpp still
                                # records that a macro-aware retry produced
                                # fewer ERROR nodes.
                                used_cpp = True
                        else:
                            import logging

                            logging.warning(f"cpp output for {filepath} is too large, skipping")
                except (OSError, subprocess.SubprocessError):
                    pass
        except Exception:
            pass

    kind_map, call_types, import_types = cfg["kind_map"], cfg["call_types"], cfg["import_types"]
    symbols: list[Symbol] = []
    imports: list[str] = []
    # `imports` is capped per-entry below to bound the exported artifact size.
    # parse_import_details needs the untruncated text -- a multi-binding import
    # (`import { a, ..., z } from "mod"`) longer than the cap loses its closing
    # brace and module path, so every regex in parse_import_details misses and
    # import_details silently comes back empty for that statement.
    imports_full: list[str] = []
    final_errors = 0

    # Explicit stack rather than recursion: tree-sitter trees nest deeply enough
    # (long chained expressions, big literals) to blow the interpreter's limit.
    stack: list[tuple[Node, tuple[str, ...], Symbol | None]] = [(tree.root_node, (), None)]
    while stack:
        node, scope, owner = stack.pop()
        ntype = node.type
        if ntype == "ERROR":
            final_errors += 1
        if ntype in import_types:
            raw = _text(source, node).strip()
            if raw:
                imports.append(raw[:300])
                imports_full.append(raw)
        elif lang == "ruby" and ntype == "call":
            callee = _callee_name(source, node)
            if callee in ("require", "require_relative", "load"):
                raw = _text(source, node).strip()
                if raw:
                    imports.append(raw[:300])
                    imports_full.append(raw)
        elif lang == "bash" and ntype == "command":
            cmd_name = _callee_name(source, node)
            if not cmd_name and node.children:
                first = _text(source, node.children[0]).strip()
                if first == ".":
                    cmd_name = "."
            if cmd_name in ("source", "."):
                raw = _text(source, node).strip()
                if raw:
                    imports.append(raw[:300])
                    imports_full.append(raw)
        if ntype in call_types:
            callee = _callee_name(source, node)
            # file-scope calls (owner is None) produce no edge in graph.build,
            # so drop them here rather than accumulating dead data (ISS-02).
            if callee and owner is not None:
                call_kind = "static"
                if ntype in ("macro_invocation", "macro_call"):
                    call_kind = "possible"
                elif callee in DYNAMIC_CALLEES.get(lang, frozenset()):
                    call_kind = "dynamic"
                owner.calls.append(callee)
                owner.call_details.append({"name": callee, "kind": call_kind})
        kind = kind_map.get(ntype)
        child_scope, child_owner = scope, owner
        if kind is not None:
            name = _name_of(source, node, lang)
            if kind == "maybe_function":
                value = node.child_by_field_name("value")
                if value is None or value.type not in (
                    "arrow_function",
                    "function",
                    "function_expression",
                ):
                    kind = None
                else:
                    kind = "function"
            if kind and name:
                bases_list, base_details = _bases_with_details(source, node, lang)
                sym = Symbol(
                    name=name,
                    qualname=".".join(scope + (name,)),
                    kind=kind,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    parent=".".join(scope) or None,
                    signature=_signature(source, node),
                    docstring=_docstring(source, node, lang),
                    bases=bases_list,
                    base_details=base_details,
                )
                # Check for decorators on Python decorated_definition. This and
                # the prev-sibling branch below must stay mutually exclusive:
                # in tree-sitter-python a decorated function_definition's
                # prev_sibling IS its decorator node, so running both branches
                # recorded every Python decorator twice -- Counter(sym.calls)
                # then reported count=2 on a single decorator CALLS edge.
                if node.parent is not None and node.parent.type == "decorated_definition":
                    for child in node.parent.children:
                        if child.type == "decorator":
                            dec_text = (
                                _callee_name(source, child)
                                or _text(source, child).strip().lstrip("@").split("(")[0].strip()
                            )
                            if dec_text:
                                sym.calls.append(dec_text)
                                sym.call_details.append({"name": dec_text, "kind": "decorator"})
                else:
                    # Java annotations or JS/TS decorators sit as the previous sibling.
                    prev = node.prev_sibling
                    if prev is not None and prev.type in ("annotation", "decorator"):
                        ann_text = _text(source, prev).strip().lstrip("@").split("(")[0].strip()
                        if ann_text:
                            sym.calls.append(ann_text)
                            sym.call_details.append({"name": ann_text, "kind": "decorator"})

                symbols.append(sym)
                child_scope, child_owner = scope + (name,), sym
        # named_children skips punctuation and keyword tokens: no configured
        # kind/call/import type is anonymous, and half the tree is those tokens.
        # reversed: the stack pops last-pushed first, so this keeps source order
        for c in reversed(node.named_children):
            stack.append((c, child_scope, child_owner))

    # Parse import details from the untruncated text -- see imports_full above.
    import_details: list[ImportDetail] = []
    for raw in imports_full:
        import_details.extend(parse_import_details(raw, lang))

    return ParsedFile(
        lang=lang,
        symbols=symbols,
        imports=imports,
        parse_errors=final_errors,
        used_cpp=used_cpp,
        import_details=import_details,
    )


def explain_path(
    root: Path | str,
    target: Path | str,
    config: BuildConfig | None = None,
    include_globs=None,
    exclude_globs=None,
) -> dict:
    """Evaluate a path against the 10 inclusion/exclusion precedence rules.

    Returns a dict with:
        path: target path
        relative_path: relative path to root
        included: bool (True if the file would be indexed)
        rule: string identifier of the determining rule
        reason: human-readable explanation
        precedence_step: step number (1-10) where the decision was reached
    """
    if config is None:
        config = BuildConfig()
    root_path = Path(root).resolve()
    target_path = Path(target)
    if not target_path.is_absolute():
        target_path = root_path / target_path
    # Resolve the parent only, not the leaf -- same fix as 8ddd010 ("check
    # symlink before resolving output path"). discover() lstats the path as
    # discovered (no resolution), so a symlink fails S_ISREG there and is
    # dropped. Fully resolving here would follow the leaf symlink to its
    # target before the lstat below ever runs, making that lstat see the
    # target's stat (often a regular file) instead of the link's -- answering
    # INCLUDED for a path discover() never indexes. ".."/"." still normalise
    # and relative_to(root_path) still works because the parent is resolved.
    target_path = target_path.parent.resolve() / target_path.name

    try:
        rel = target_path.relative_to(root_path)
        rel_str = rel.as_posix()
    except ValueError:
        return {
            "path": str(target_path),
            "relative_path": str(target_path),
            "included": False,
            "rule": "outside_root",
            "reason": f"Path '{target_path}' is outside repository root '{root_path}'",
            "precedence_step": 0,
        }

    # Step 1: Target existence
    if not target_path.exists():
        return {
            "path": str(target_path),
            "relative_path": rel_str,
            "included": False,
            "rule": "not_found",
            "reason": f"Path '{target_path}' does not exist on disk",
            "precedence_step": 1,
        }

    # Step 2: Skip directories (DEFAULT_SKIP_DIRS | extra_exclude_dirs)
    skip_dirs = set(DEFAULT_SKIP_DIRS) | set(config.extra_exclude_dirs)
    if config.include_vendor:
        skip_dirs.discard("vendor")
    skip_part = next((part for part in rel.parts if part in skip_dirs), None)
    if skip_part is not None:
        cat = "dot-directory" if skip_part.startswith(".") else "vendor/build directory"
        return {
            "path": str(target_path),
            "relative_path": rel_str,
            "included": False,
            "rule": "skip_dir",
            "reason": f"Path component '{skip_part}' is in excluded {cat} filter (DEFAULT_SKIP_DIRS/--exclude-dir)",
            "precedence_step": 2,
        }

    # Step 3: Sibling .r2glock internal lock files
    if target_path.name.startswith(".") and target_path.name.endswith(".r2glock"):
        return {
            "path": str(target_path),
            "relative_path": rel_str,
            "included": False,
            "rule": "internal_lock",
            "reason": "Internal BuildLock artifact file",
            "precedence_step": 3,
        }

    # Step 4: Git-ignore check (if in git repo)
    git_dir = root_path / ".git"
    if git_dir.exists():
        try:
            p = subprocess.run(
                [
                    "git",
                    "-c",
                    "core.quotepath=false",
                    "-C",
                    str(root_path),
                    "check-ignore",
                    "-q",
                    str(target_path),
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=5,
            )
            if p.returncode == 0:
                return {
                    "path": str(target_path),
                    "relative_path": rel_str,
                    "included": False,
                    "rule": "gitignore",
                    "reason": "Path is matched and ignored by .gitignore rules",
                    "precedence_step": 4,
                }
        except Exception:
            pass

    # Step 5: Regular file check (stat.S_ISREG)
    try:
        st = target_path.lstat()
    except OSError as exc:
        return {
            "path": str(target_path),
            "relative_path": rel_str,
            "included": False,
            "rule": "stat_error",
            "reason": f"Cannot stat file: {exc}",
            "precedence_step": 5,
        }
    if not statmod.S_ISREG(st.st_mode):
        ftype = "directory" if statmod.S_ISDIR(st.st_mode) else "special file (symlink/device/fifo)"
        return {
            "path": str(target_path),
            "relative_path": rel_str,
            "included": False,
            "rule": "non_regular_file",
            "reason": f"Path is a {ftype}, not a regular file",
            "precedence_step": 5,
        }

    # Step 6: File size check
    if st.st_size > config.max_file_bytes and not config.chunk_large_files:
        return {
            "path": str(target_path),
            "relative_path": rel_str,
            "included": False,
            "rule": "too_large",
            "reason": f"File size ({st.st_size} bytes) exceeds limit ({config.max_file_bytes} bytes); --chunk-large-files is disabled",
            "precedence_step": 6,
        }

    # Step 7: Secret detection
    if not config.include_secrets:
        from .secrets import _is_secret_path

        if _is_secret_path(
            rel_str,
            extra_keywords=config.extra_secret_keywords,
            extra_dirs=config.extra_secret_dirs,
        ):
            return {
                "path": str(target_path),
                "relative_path": rel_str,
                "included": False,
                "rule": "secret_file",
                "reason": "Path matches secret credential file pattern (use --include-secrets to override)",
                "precedence_step": 7,
            }

    # Step 8: Include globs
    if include_globs and not matches_any(rel_str, include_globs):
        return {
            "path": str(target_path),
            "relative_path": rel_str,
            "included": False,
            "rule": "not_included",
            "reason": f"Path does not match any --include glob: {include_globs}",
            "precedence_step": 8,
        }

    # Step 9: Exclude globs
    if exclude_globs and matches_any(rel_str, exclude_globs):
        return {
            "path": str(target_path),
            "relative_path": rel_str,
            "included": False,
            "rule": "exclude_glob",
            "reason": f"Path matches --exclude glob: {exclude_globs}",
            "precedence_step": 9,
        }

    # Step 10: Binary check
    if is_binary(target_path):
        return {
            "path": str(target_path),
            "relative_path": rel_str,
            "included": False,
            "rule": "binary",
            "reason": "File content detected as binary (contains null bytes in header)",
            "precedence_step": 10,
        }

    # All criteria passed -> Included!
    return {
        "path": str(target_path),
        "relative_path": rel_str,
        "included": True,
        "rule": "included",
        "reason": "Path passed all exclusion checks and is eligible for indexing",
        "precedence_step": 10,
    }
