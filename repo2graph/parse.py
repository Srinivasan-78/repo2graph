"""Discovery, language configs, and tree-sitter based symbol/call extraction."""

import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache, lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict, cast

from tree_sitter import Node, Parser

from .events import diagnostic
from .frameworks import (
    FRAMEWORK_LANGS,
    injected_dependencies,
    model_relations,
    mount_prefix,
    route_from_decorator,
)
from .parsepool import PARSE_TIMEOUT_S
from .refcapture import (  # split out by responsibility (#315)
    REF_LANGS,
    VALUE_KINDS,
    _finish_refs,
    _js_ref,
    _js_value_kind,
    _py_ref,
    _py_value_symbol,
    _RefState,
    _text,
)
from .security import MAX_QUALNAME_CHARS, clean_identifier

if TYPE_CHECKING:
    pass


# Import-statement patterns shared with graph._IMPORT_RE, which composes its
# table from these, so the two readers of an import line cannot drift (#461 #37).
PY_FROM_IMPORT_RE = re.compile(r"^from\s+(\.*[\w.]*)\s+import\s+([\w\s,*()]+)")
PY_IMPORT_RE = re.compile(r"^import\s+([\w\.,\s]+)")
JAVA_IMPORT_RE = re.compile(r"import\s+(?:static\s+)?([\w\.\*]+)")
RUBY_REQUIRE_RE = re.compile(r"""(?:require_relative|require|load)\s*\(?\s*['"]([^'"]+)['"]""")


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
    # 1-based line of the import statement, for the IMPORTS edge's `evidence`.
    # tree-sitter's Point.row advances on a newline only -- the same convention
    # chunks._lines() slices by -- so this indexes the same line the reader
    # sees. See .github/CONTRIBUTING.md on splitlines().
    line: int | None = None


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
    # `.m` is also MATLAB: `refine_lang` keeps it only with an Objective-C signal.
    ".m": "objc",
    ".mm": "objc",
    ".tf": "hcl",
    ".hcl": "hcl",
    # Not grammars of their own: `refine_lang` hands the code inside them to
    # the JavaScript/TypeScript or Python parser (#398).
    ".vue": "vue",
    ".svelte": "svelte",
    ".ipynb": "ipynb",
}

# `.h` is the single ambiguous extension in EXT_LANG: it defaults to "c" there
# (a decode-free, allocation-free lookup that every other extension can use
# unconditionally), but `.h` is also the most common C++ header extension in
# the wild -- LLVM, Chromium and most Google-style C++ use it. A positive
# content signal from `sniff_header_lang` overrides that default so a C++
# header's classes, templates, namespaces and member functions are parsed
# with the cpp grammar instead of silently mis-parsed or dropped by the C one
# (#377). `::` and `std::` are the least ambiguous signals; `template` and
# `#include <string>` almost never appear in a plain C header, either.
CPP_HEADER_HINTS = (
    b"class ",
    b"namespace ",
    b"template",
    b"public:",
    b"private:",
    b"protected:",
    b"#include <string>",
    b"#include <vector>",
    b"::",
    b"std::",
)


# Directives no C, C++ or MATLAB file contains. `@interface`/`@protocol`
# settle a `.h` as Objective-C before the C++ hints are consulted, and any of
# these keeps a `.m` file as Objective-C rather than MATLAB (#398).
OBJC_HINTS = (b"@interface", b"@implementation", b"@protocol", b"#import")


def sniff_header_lang(raw: bytes) -> str:
    """Guess "c", "cpp" or "objc" for a `.h` file from its content (#377).

    Cheap and best-effort: any single C++ signal anywhere in `raw` wins,
    since a plain C header contains none of `CPP_HEADER_HINTS` in ordinary
    (non-comment, non-string) code. Callers pass the bytes they already read
    for hashing/parsing -- this never opens the file itself.
    """
    if b"@interface" in raw or b"@protocol" in raw:
        return "objc"
    return "cpp" if any(h in raw for h in CPP_HEADER_HINTS) else "c"


# The end tag may carry whitespace or junk before its `>` (`</script\t\n x>`);
# HTML still closes the block there, so the code must stop there too.
_SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script\b[^>]*>", re.S | re.I)
_TS_LANG_ATTR_RE = re.compile(r"""\blang\s*=\s*["']?(tsx|ts|typescript)\b""", re.I)


def component_script(raw: bytes) -> tuple[str, bytes]:
    """The `<script>` blocks of a Vue or Svelte component, at their own lines.

    Everything outside them becomes empty lines, so every line number the
    JavaScript/TypeScript parser reports is the line in the `.vue`/`.svelte`
    file. A `lang="ts"` on any block parses them all as TypeScript.
    """
    text = raw.decode("utf-8", "replace")
    parts: list[str] = []
    lang, pos = "javascript", 0
    for m in _SCRIPT_RE.finditer(text):
        parts.append("\n" * text.count("\n", pos, m.start(2)))
        parts.append(m.group(2))
        pos = m.end(2)
        attr = _TS_LANG_ATTR_RE.search(m.group(1))
        if attr:
            lang = "tsx" if attr.group(1).lower() == "tsx" else "typescript"
    parts.append("\n" * text.count("\n", pos))
    return lang, "".join(parts).encode("utf8")


# nbformat writes one cell key per line, sorted, so `cell_type` comes before
# `source` and both sit at the same indent; a key at any other indent is
# inside an output or metadata and is not the cell's.
_NB_CELL_TYPE_RE = re.compile(r'^(\s*)"cell_type"\s*:\s*"(\w+)"')
_NB_SOURCE_RE = re.compile(r'^(\s*)"source"\s*:\s*\[\s*$')
_NB_STRING_RE = re.compile(r'^\s*("(?:[^"\\]|\\.)*")\s*,?\s*$')
_NB_MAGIC_RE = re.compile(r"^\s*[%!?]")


def notebook_code(raw: bytes) -> bytes:
    """A Jupyter notebook's code cells, each at the lines it occupies in the file.

    Every other line becomes empty, so the Python parser's line numbers are
    the notebook's own and a citation opens on the right line of the `.ipynb`.
    The `"source": [` line of each code cell becomes `# cell N` (N counts
    every cell from 1, as Jupyter shows them), so a chunk names its cell.
    IPython magics and shell escapes (`%time`, `!pip`) become empty lines.
    A notebook not written one source line per JSON line (minified) yields
    nothing rather than wrong line numbers.
    """
    lines = raw.decode("utf-8", "replace").split("\n")
    out = [""] * len(lines)
    cell, cell_type, indent, in_source = 0, "", None, False
    for i, line in enumerate(lines):
        if in_source:
            m = _NB_STRING_RE.match(line)
            if m is None:
                in_source = False
            elif cell_type == "code":
                try:
                    code = json.loads(m.group(1))
                except ValueError:
                    code = ""
                code = code.rstrip("\r\n").replace("\r", "").replace("\n", " ")
                out[i] = "" if _NB_MAGIC_RE.match(code) else code
            continue
        if m := _NB_CELL_TYPE_RE.match(line):
            if indent is None or len(m.group(1)) == indent:
                indent = len(m.group(1))
                cell += 1
                cell_type = m.group(2)
        elif (m := _NB_SOURCE_RE.match(line)) and len(m.group(1)) == indent:
            in_source = True
            if cell_type == "code":
                out[i] = f"# cell {cell}"
    return "\n".join(out).encode("utf8")


def refine_lang(lang: str | None, suffix: str, raw: bytes) -> tuple[str | None, bytes]:
    """The language to parse a file as, and the bytes to give that parser.

    `EXT_LANG` maps an extension; this settles what the extension cannot:
    a `.h` header's C, C++ or Objective-C (#377), a `.m` file's Objective-C
    or MATLAB (None: not parsed), and the code inside a Vue or Svelte
    component or a notebook (#398). Line numbers are always the file's own.
    """
    if lang == "c" and suffix == ".h":
        return sniff_header_lang(raw), raw
    if lang == "objc" and suffix == ".m":
        return ("objc" if any(h in raw for h in OBJC_HINTS) else None), raw
    if lang in ("vue", "svelte"):
        return component_script(raw)
    if lang == "ipynb":
        return "python", notebook_code(raw)
    return lang, raw


# Languages read through `refine_lang` as a whole file: their code cannot be
# found in an arbitrary slice, so `--chunk-large-files` does not slice them.
EMBEDDED_LANGS = frozenset({"vue", "svelte", "ipynb"})


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
            # `class C { json = (body) => ... }` defines C.json as surely as
            # `json() {}` does, and the idiom is everywhere in modern JS/TS
            # because a field-bound arrow captures `this`. Without this entry the
            # whole form produced no symbol: Hono's `context.ts` indexed its
            # constructor and getters and not one method a caller invokes.
            # `maybe_function` is what keeps `limit = 42` out.
            "field_definition": "maybe_function",
            # `export { Hono as HonoBase }` binds HonoBase, and HonoBase is the
            # name consumers import. `maybe_alias` is what keeps the bare
            # `export { Hono }` -- already indexed under that name -- and the
            # `from './mod'` re-exports out. See _export_specifier_alias.
            "export_specifier": "maybe_alias",
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
            "scoped_call_expression",
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
            "secondary_constructor": "method",
            "getter": "method",
            "setter": "method",
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
    "objc": {
        "kind_map": {
            # A category (`@interface Cache (Extra)`) is the same node type
            # and the same first identifier, so its methods join the class.
            "class_interface": "class",
            "class_implementation": "class",
            "protocol_declaration": "protocol",
            "method_definition": "method",
            "function_definition": "function",
        },
        "call_types": {"call_expression", "message_expression"},
        "import_types": {"preproc_include"},
        "doc": "line",
    },
    "hcl": {
        # Resolved per node by `_hcl_symbol`: a top-level block is named by its
        # Terraform address (`aws_s3_bucket.logs`, `var.env`, `module.vpc`),
        # and each attribute of a top-level `locals` block is `local.<name>`.
        "kind_map": {"block": "hcl", "attribute": "hcl"},
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
    # TypeScript's grammar names the class-field node differently from
    # JavaScript's `field_definition`, so inheriting the map is not enough.
    public_field_definition="maybe_function",
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
    max_edges: int = 0
    max_chunks: int = 0
    max_bytes: int = 0
    max_memory_mb: float = 0.0
    max_build_seconds: float = 0.0
    limit_policy: str | None = None
    include_secrets: bool = False
    secret_policy: str = "redact-match"  # noqa: S105 - a policy name, not a credential
    extra_secret_keywords: list[str] = field(default_factory=list)
    extra_secret_dirs: list[str] = field(default_factory=list)
    parse_policy: str = "best-effort"
    # Opt-in READS / WRITES / REFERENCES edges (#397). Off by default, so the
    # default graph, and every artifact made from it, is unchanged.
    reference_edges: bool = False
    framework_edges: bool = False
    # Wall-clock budget for one file's parse, in seconds; 0 = none (#81). See
    # parsepool.py for why it needs a worker process and how it was calibrated.
    parse_timeout: float = PARSE_TIMEOUT_S
    # Opt-in SCIP index (#380), read after parsing to make CALLS/INHERITS
    # exact where it covers a file. None, the default, changes nothing; it is
    # not part of the parse cache's identity because no parse result uses it.
    scip_index: str | None = None
    # The directory this build writes its artifacts to. When it lies inside
    # the indexed root, discovery must never index it: `git ls-files -co`
    # lists untracked files and os.walk sees everything, so without this a
    # second `build . -o .r2g` cites its own chunks.jsonl / manifest.json.
    output_dir: str | None = None


# `<out>/agent/manifest.json` whose "format" starts with this marks a repo2graph index.
INDEX_MARKER_FORMAT = "repo2graph/"


#: `.<out>.staging.<pid>.<hex8>` and `.<out>.backup.<pid>`, from export.dump_all.
_SWAP_DIR_RE = re.compile(r"^\..+\.(?:staging\.\d+\.[0-9a-f]{8}|backup\.\d+)$")


# `Callable[..., Parser]`, not `Callable[[str], Parser]`: the two supported
# language-pack lines type this differently. 0.x declares `get_parser` over a
# `Literal[...]` of every known language name, 1.x over a plain `str`. A `[str]`
# parameter list is incompatible with the Literal one (parameters are
# contravariant), so pinning the narrower signature made mypy fail against the
# version actually in `uv.lock`. `parser_for` validates the name at run time by
# catching the failure, which is the only check that holds across both.
_get_parser: Callable[..., Parser] | None
try:
    from tree_sitter_language_pack import get_parser as _get_parser
except ImportError:  # pragma: no cover
    _get_parser = None


@lru_cache(maxsize=1)
def grammar_fingerprint() -> str:
    """Identity of the installed grammars, for the incremental parse cache key.

    `PARSE_CACHE_FORMAT` is bumped by hand whenever *our* extraction changes, so
    it catches every change we make and none that we don't. A tree-sitter
    grammar upgrade is the second kind: the same source, parsed by a newer
    grammar, can yield different node types and therefore different symbols,
    calls and qualnames -- with `PARSE_CACHE_FORMAT` untouched, because nothing
    in this repository changed. `--incremental` would then reuse entries
    produced by the old grammar for every file whose bytes did not change, and
    the resulting index would be a silent mix of two grammar versions that no
    full rebuild could reproduce. Determinism is the property the incremental
    path is tested against (`test_incremental_is_byte_identical_to_a_full_rebuild`),
    and that test cannot see this because it never changes grammars mid-run.

    Both distributions matter: `tree-sitter-language-pack` ships the grammars and
    `tree-sitter` is the runtime that walks them. An unknown version reads as
    "unknown" rather than raising -- a cache that cannot be keyed is one that
    must not be trusted, and an "unknown" token simply never matches a recorded
    one, so the next build is a full build.
    """
    from importlib.metadata import version as _dist_version

    parts: list[str] = []
    for dist in ("tree-sitter", "tree-sitter-language-pack"):
        try:
            parts.append(f"{dist}={_dist_version(dist)}")
        except Exception:  # noqa: BLE001 - any lookup failure is "unknown"; see docstring
            parts.append(f"{dist}=unknown")
    return " ".join(parts)


@lru_cache(maxsize=1)
def lang_config_fingerprint() -> str:
    """Digest of `LANG_CFG`, the node-type tables extraction is driven by.

    Editing a kind map or a call-node list changes what every cached parse
    would now contain, exactly like a grammar upgrade does, and is just as easy
    to make without remembering to bump `PARSE_CACHE_FORMAT`.
    """
    import hashlib
    import json

    def _norm(o: object) -> str | list[str]:
        if isinstance(o, (set, frozenset)):
            return sorted(o, key=repr)
        return repr(o)

    blob = json.dumps(LANG_CFG, sort_keys=True, default=_norm)
    return hashlib.sha256(blob.encode("utf8")).hexdigest()[:16]


def parse_cache_identity(config: "BuildConfig | None") -> dict[str, str]:
    """Everything besides a file's bytes that decides what its cached parse holds.

    Compared field by field on load, so a mismatch can say which one changed.
    """
    config = config or BuildConfig()
    return {
        "grammars": grammar_fingerprint(),
        "lang_config": lang_config_fingerprint(),
        "max_file_bytes": str(config.max_file_bytes),
        "chunk_large_files": str(bool(config.chunk_large_files)),
        "secret_policy": str(config.secret_policy),
        # Only when on, so a default build's cache identity is what it was.
        **({"reference_edges": "True"} if config.reference_edges else {}),
        **({"framework_edges": "True"} if config.framework_edges else {}),
    }


@cache
def parser_for(lang: str) -> Parser | None:
    """The parser for `lang`, or None if one cannot be obtained for any reason.

    The except clause is deliberately broad. It used to name
    `(LookupError, ValueError, ImportError, AttributeError)`, which covers what
    tree-sitter-language-pack 0.x raises but not 1.x: that line raises its own
    `DownloadError`, `ChecksumMismatchError`, `CacheLockError`,
    `DynamicLoadError`, `ParserSetupError` and friends, all deriving from a
    private `tree_sitter_language_pack.exceptions.Error` rather than from
    anything in the tuple. Offline, every one of those escaped `parser_for`
    instead of answering None -- so the caller could not tell "no grammar" from
    "no symbols", and the file was dropped without ever being counted as
    unparsed.

    Catching the base class by import would couple us to a private module, and
    narrowing the tuple again would re-break on the next exception they add.
    Every failure here means exactly one thing to every caller -- there is no
    parser -- so collapse them all to None and let the `grammar_unavailable`
    tally in `parse_source` make the condition loud upstream.
    """
    if _get_parser is None:
        return None
    try:
        return _get_parser(lang)
    except Exception:  # noqa: BLE001 - any failure means "no parser"; see docstring
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
    call_details: list[dict[str, Any]] = field(default_factory=list)
    base_details: list[dict[str, Any]] = field(default_factory=list)
    # Unique-in-file id key when `qualname` alone is not unique (overloads,
    # conditional redefinitions): "" means "same as qualname", which is every
    # first definition, so the common-case node id never changes. A later
    # duplicate gets `<qualname>@L<start_line>` -- see `symbol_key`.
    key: str = ""
    # The enclosing symbol's `key` when that differs from `parent` (a child of
    # a duplicate definition); "" means "same as parent".
    parent_key: str = ""
    # `--reference-edges` only (#397): the module/class-level names this
    # symbol reads, writes or names as a type, before resolution. Empty, and
    # left out of the parse cache, otherwise.
    refs: list[dict[str, Any]] = field(default_factory=list)
    # `--framework-edges` only: route declarations, injected dependency names
    # and ORM relation targets read off this symbol's decorators and class
    # body, before resolution. Empty, and left out of the parse cache,
    # otherwise -- so a default build's artifacts are byte-identical.
    frameworks: list[dict[str, Any]] = field(default_factory=list)


def symbol_key(sym: Symbol) -> str:
    """The part of a symbol's node id after `sym:<path>::`.

    Equal to `qualname` for every first definition of a name in a file; a later
    definition with the same qualname (Java/C#/Kotlin/Swift/C++ overloads, a
    Python conditional redefinition) carries `@L<line>` so it gets a node and
    a chunk of its own instead of silently collapsing into the first one.
    """
    return sym.key or sym.qualname


def symbol_parent_key(sym: Symbol) -> str | None:
    """The `symbol_key` of the enclosing symbol, or None for a file-level one."""
    return sym.parent_key or sym.parent


def disambiguate_key(qualname: str, line: int, used: set[str]) -> str:
    """Empty string if `qualname` is still free in `used`, else a stable `@L<line>` key.

    Deterministic: depends only on source order and line numbers. Two same-named
    definitions on one line (minified code) fall back to `@L<line>~<n>`.
    """
    if qualname not in used:
        used.add(qualname)
        return ""
    key = f"{qualname}@L{line}"
    n = 2
    while key in used:
        key = f"{qualname}@L{line}~{n}"
        n += 1
    used.add(key)
    return key


@dataclass
class ParsedFile:
    lang: str
    symbols: list[Symbol]
    imports: list[str]
    parse_errors: int = 0
    used_cpp: bool = False
    is_chunked: bool = False
    # True when no grammar could be obtained for `lang`, so this file was never
    # actually parsed. Without it, the unavailable-grammar case is byte-identical
    # to "parsed fine, genuinely declares nothing" -- both are an empty
    # ParsedFile -- and a build with every grammar missing reported
    # stats["parsed"] == stats["files"] and exited 0 with an empty graph.
    grammar_unavailable: bool = False
    import_details: list[ImportDetail] = field(default_factory=list)
    # 1-based line per entry of `imports`, index-aligned with it by
    # construction (both are appended in the same step of the walk). The
    # IMPORTS edge iterates `imports`, not `import_details`, and matching the
    # two back up by text is unsafe: `imports` entries are truncated at 300
    # chars and `import_details[].raw` is a cleaned form, so the strings do
    # not always agree.
    import_lines: list[int] = field(default_factory=list)
    # Why this file has no symbols although its grammar loaded: "timeout" when
    # its parse ran over BuildConfig.parse_timeout, "crash" when it took its
    # worker down (#81). Empty for every file that was actually parsed.
    parse_skipped: str = ""


def _name_of(src: bytes, node: Node, lang: str) -> str | None:
    n = node.child_by_field_name("name")
    if n is not None:
        return _text(src, n).strip()
    if lang == "kotlin":
        if node.type == "secondary_constructor":
            return "constructor"
        if node.type == "getter":
            return "get"
        if node.type == "setter":
            return "set"
        if node.type == "function_declaration":
            # tree-sitter-kotlin exposes no `name` field and names a function with
            # a `simple_identifier` child, which the generic fallback below does not
            # recognise -- so no Kotlin `fun` was ever indexed and every member's
            # calls were absorbed by its class.
            for c in node.children:
                if c.type == "simple_identifier":
                    return _text(src, c).strip()
            return None
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


def _callee_name(src: bytes, node: Node) -> str | None:
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
    # Resolve the rightmost member-access segment *before* stripping
    # "(" / "<" noise. A chained call's `function` field text is the whole
    # member expression, e.g. `obj.get_user().save` for `obj.get_user().save()`
    # — the "(" that closes the inner `get_user()` call sits in the middle of
    # that string. Splitting on "(" first (old order) chopped everything from
    # that "(" onward, including the outer ".save", and left "obj.get_user".
    # Splitting on the separator first isolates "save" so the parens/generic
    # stripping below only ever runs on the final, already-resolved segment.
    # "\\": PHP's namespace separator (`\App\Utils\compute()`, `Sub\helper()`).
    # Without it a namespaced call's callee text keeps its leading namespace
    # segments and never matches the bare symbol name `graph.py` indexes by (#344).
    for sep in ("::", ".", "->", "\\"):
        if sep in txt:
            txt = txt.split(sep)[-1]
    txt = txt.split("(")[0].split("<")[0]
    # Strip only leading pointer/deref and trailing macro !
    txt = txt.strip().lstrip("*& \t\n").removesuffix("!").strip()
    return txt or None


# Receivers that name the calling object itself. A call on one of these can be
# resolved against the enclosing class; a call on anything else has a receiver
# whose type the parser does not know.
_SELF_RECEIVERS = frozenset(
    {"self", "cls", "this", "super", "super()", "$this", "Self", "static", "@"}
)

# `base` / `parent` are keywords in exactly one language each and ordinary
# identifiers everywhere else: a Python/JS/Go local named `parent`
# (`parent.add(self)`) is a value of unknown type, not the base class. So they
# only classify as "self" in their own language -- C#'s `base.F()` and PHP's
# static `parent::f()`.
_LANG_SELF_RECEIVERS: dict[str, frozenset[str]] = {"csharp": frozenset({"base"})}
_LANG_STATIC_SELF_RECEIVERS: dict[str, frozenset[str]] = {"php": frozenset({"parent"})}


# Receivers that name the *base* implementation, not the calling object:
# `super().__init__()` never means the calling method itself. graph.build
# consults this only for calls already classified "self", which `base` and
# `parent` are only in C# and PHP respectively (see above).
SUPER_RECEIVERS = frozenset({"super", "super()", "base", "parent"})


def _split_receiver_text(txt: str) -> tuple[str, bool]:
    """Split callee text like `a.b.get` into (`a.b`, static_scope).

    `static_scope` is True when the last separator is `::` (`Config::get`,
    `std::find`, Ruby `Foo::bar`): the head names a type or namespace, never a
    value of unknown type. Returns ("", False) for a bare name.
    """
    cut, sep = -1, ""
    for s in (".", "->", "::"):
        i = txt.rfind(s)
        if i > cut:
            cut, sep = i, s
    if cut <= 0:
        return "", False
    return txt[:cut].strip().rstrip("?").strip(), sep == "::"


def _receiver_of(src: bytes, node: Node) -> tuple[str, bool]:
    """(receiver head text, static_scope) of a call node; ("", False) if bare."""
    recv = (
        node.child_by_field_name("object")
        or node.child_by_field_name("receiver")
        # PHP `scoped_call_expression`: `parent::f()`, `Foo::bar()`
        or node.child_by_field_name("scope")
    )
    if recv is None and node.child_by_field_name("function") is None and node.named_child_count:
        # Kotlin/Swift: `call_expression` has no `function` field; a member
        # call's first child is a `navigation_expression` whose first child is
        # the target (`list` in `list.add(1)`).
        first = node.named_children[0]
        if first.type == "navigation_expression" and first.named_child_count:
            recv = first.child_by_field_name("target") or first.named_children[0]
    if recv is not None:
        head = _text(src, recv).strip().rstrip("?").strip()
        # Ruby's `call` keeps `::` / `.` as a bare token between the receiver
        # and the method field.
        meth = node.child_by_field_name("method") or node.child_by_field_name("name")
        gap = src[recv.end_byte : meth.start_byte] if meth is not None else b""
        return head, b"::" in gap
    fn = node.child_by_field_name("function")
    if fn is None:
        return "", False
    return _split_receiver_text(_text(src, fn).strip())


def _classify_receiver(
    head: str,
    self_names: frozenset[str] = frozenset(),
    lang: str = "",
    static: bool = False,
) -> str:
    """Classify what a call is made on: "none", "self" or "other".

    "none" is a bare call (`helper()`), "self" is a call on the calling object
    (`self.x()`, `this.x()`, `super().x()`), and "other" is a call on anything
    else (`d.get()`, `os.environ.get()`, `self.cache.get()`). `_callee_name`
    drops the receiver, so without this `graph.build` cannot tell `self.get()`
    from `some_dict.get()`.
    """
    if not head:
        return "none"
    if head in _SELF_RECEIVERS or head in self_names or head.startswith("super("):
        return "self"
    if head in _LANG_SELF_RECEIVERS.get(lang, ()):
        return "self"
    if static and head in _LANG_STATIC_SELF_RECEIVERS.get(lang, ()):
        return "self"
    return "other"


def _receiver_fields(
    head: str, static: bool, self_names: frozenset[str] = frozenset(), lang: str = ""
) -> dict[str, Any]:
    """The receiver keys of one `call_details` entry.

    "receiver" is always present (none/self/other). "receiver_head" is the
    receiver's source text when there is one, so `graph.build` can tell an
    imported module (`store.get()`) or a type (`Util.remove()`) from a value of
    unknown type (`d.get()`); "receiver_static" marks a `::`-scoped call.
    """
    out: dict[str, Any] = {"receiver": _classify_receiver(head, self_names, lang, static)}
    if head:
        out["receiver_head"] = head[:200]
    if static:
        out["receiver_static"] = True
    return out


def _go_receiver_name(src: bytes, node: Node) -> str | None:
    """The receiver identifier of a Go `func (c *T) M()` -- Go's `self`."""
    params = node.child_by_field_name("receiver")
    if params is None:
        return None
    for p in params.named_children:
        name = p.child_by_field_name("name")
        if name is not None:
            return _text(src, name).strip() or None
    return None


def _clean_type_name(txt: str) -> str | None:
    """`*List[T]` / `Map<K, V>?` / `(T)` -> `List` / `Map` / `T`."""
    txt = txt.strip().lstrip("*&(").rstrip(")?").strip()
    txt = txt.split("[")[0].split("<")[0].strip().lstrip("*").strip()
    return txt or None


def _go_receiver_type(src: bytes, node: Node) -> str | None:
    """The receiver type of a Go `func (c *T[K]) M()`, pointer/generics stripped."""
    params = node.child_by_field_name("receiver")
    if params is None:
        return None
    for p in params.named_children:
        t = p.child_by_field_name("type")
        if t is not None:
            return _clean_type_name(_text(src, t))
    return None


def _kotlin_receiver_type(src: bytes, node: Node) -> str | None:
    """The receiver type of a Kotlin extension `fun Foo<T>.bar()`, or None."""
    for c in node.children:
        if c.type == "receiver_type":
            return _clean_type_name(_text(src, c))
    return None


def _decorator_receiver(src: bytes, node: Node) -> dict[str, Any]:
    """Receiver keys for a decorator/annotation entry.

    `@router.get("/x")` is a call of `get` on `router`. Without these keys the
    entry looked receiver-less -- a typed call -- and kept every untyped
    `d.get()` in the decorated function bound at 1.0.
    """
    txt = _text(src, node).strip().lstrip("@").split("(")[0].strip()
    return _receiver_fields(*_split_receiver_text(txt))


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


# Terraform's address prefix per block type; `resource` has none.
_HCL_PREFIX = {"variable": "var", "module": "module", "output": "output", "provider": "provider"}
# Heads of references that name no block: `count.index`, `each.value`, ...
_HCL_NOT_BLOCKS = frozenset({"count", "each", "path", "self", "terraform"})


def _hcl_words(src: bytes, block: Node) -> list[str]:
    """A block's type and labels: `resource "aws_s3_bucket" "logs"` -> 3 words."""
    words = []
    for c in block.children:
        if c.type == "identifier":
            words.append(_text(src, c))
        elif c.type == "string_lit":
            words.append(_text(src, c).strip('"'))
        elif c.type == "block_start":
            break
    return words


def _hcl_symbol(src: bytes, node: Node) -> tuple[str | None, str | None]:
    """(kind, Terraform address) of a top-level block or local; else (None, None).

    Only top-level blocks are symbols: a nested `lifecycle {}` or `ingress {}`
    belongs to its resource. Every attribute of a top-level `locals` block is
    its own symbol, `local.<name>`, since that is how it is referenced.
    """
    body = node.parent
    if node.type == "attribute":
        block = body.parent if body is not None else None
        if (
            block is None
            or block.type != "block"
            or _hcl_words(src, block) != ["locals"]
            or block.parent is None
            or block.parent.parent is None
            or block.parent.parent.type != "config_file"
        ):
            return None, None
        ident = next((c for c in node.children if c.type == "identifier"), None)
        return ("local", f"local.{_text(src, ident)}") if ident is not None else (None, None)
    if body is None or body.parent is None or body.parent.type != "config_file":
        return None, None
    words = _hcl_words(src, node)
    if not words:
        return None, None
    btype, labels = words[0], words[1:]
    if btype == "resource" and len(labels) >= 2:
        return "resource", f"{labels[0]}.{labels[1]}"
    if btype == "data" and len(labels) >= 2:
        return "data", f"data.{labels[0]}.{labels[1]}"
    if btype in _HCL_PREFIX and labels:
        return btype, f"{_HCL_PREFIX[btype]}.{labels[0]}"
    if labels:
        return "block", ".".join(words)
    return None, None


def _hcl_reference(src: bytes, node: Node) -> str | None:
    """The address a `variable_expr` refers to: `aws_s3_bucket.logs.arn` -> `aws_s3_bucket.logs`."""
    head = _text(src, node)
    if head in _HCL_NOT_BLOCKS:
        return None
    attrs: list[str] = []
    sib = node.next_named_sibling
    while sib is not None and sib.type == "get_attr" and len(attrs) < 2:
        attrs.append(_text(src, sib).lstrip(".").strip())
        sib = sib.next_named_sibling
    need = 2 if head == "data" else 1
    return ".".join([head, *attrs[:need]]) if len(attrs) >= need else None


def _docstring(src: bytes, node: Node, lang: str) -> str:
    if lang == "python":
        body = node.child_by_field_name("body")
        if body is not None and body.named_child_count:
            first = body.named_children[0]
            # functions wrap the docstring in an expression_statement, classes do not
            if first.type == "expression_statement" and first.named_child_count:
                first = first.named_children[0]
            if first.type == "string":
                # Strip only the matching outer quote delimiter (handling r/u/b prefixes)
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


def _signature(src: bytes, node: Node) -> str:
    body = node.child_by_field_name("body")
    end = body.start_byte if body is not None else min(node.end_byte, node.start_byte + 300)
    return src[node.start_byte : end].decode("utf8", "replace").strip()[:300]


# ---------- JS/TS export aliases ----------
# A library with a private implementation behind a public façade binds the
# public name in a second statement, and the name consumers import is the alias:
#
#   const _getQueryParam = (url, key) => { ... }
#   export const getQueryParam: (...) = _getQueryParam as (...)
#   export { Hono as HonoBase }
#
# Neither form produced a symbol, so `request.ts`'s `getQueryParam(this.url,
# key)` named a callee with no node and the edge was dropped. See D7 in
# `benchmarks/real/README.md` and `tests/test_export_aliases.py`.

# `as` and `satisfies` keep the aliased expression as their first named child;
# a parenthesised or non-null expression has only that child.
_ALIAS_WRAPPERS = frozenset(
    {"as_expression", "satisfies_expression", "parenthesized_expression", "non_null_expression"}
)
# A value that is only a *reference* renames something. Anything else -- a
# literal, a call, a template -- is data or a computation, and `export const
# LIMIT = 42` must stay out for the same reason `limit = 42` does.
_ALIAS_REFERENCES = frozenset({"identifier", "member_expression", "nested_identifier"})


def _alias_target(src: bytes, value: Node | None) -> str | None:
    """The name an exported binding's value refers to, or None if it is not a reference."""
    # Bounded rather than recursive: `((x as T)!)` nests, but only a little, and
    # a malformed tree must not spin here.
    for _ in range(8):
        if value is None:
            return None
        if value.type in _ALIAS_REFERENCES:
            return _text(src, value).strip() or None
        if value.type in _ALIAS_WRAPPERS:
            value = value.named_children[0] if value.named_children else None
            continue
        return None
    return None


def _exported_declarator_alias(src: bytes, node: Node) -> str | None:
    """The target of `export const NAME = TARGET`, or None.

    Only a declarator whose own statement carries the `export` keyword counts.
    A file-local `const b = a` renames nothing a consumer can reach, and the
    direct parent chain -- never an ancestor search -- is what keeps the fields
    of an `export class C { foo = bar }` out of this path.
    """
    parent = node.parent
    if parent is None or parent.type not in ("lexical_declaration", "variable_declaration"):
        return None
    grandparent = parent.parent
    if grandparent is None or grandparent.type != "export_statement":
        return None
    return _alias_target(src, node.child_by_field_name("value"))


def _export_specifier_alias(src: bytes, node: Node) -> str | None:
    """The new name bound by `export { TARGET as NAME }`, or None.

    `export { x as y } from './mod'` is excluded: nothing is defined at that
    line, and a barrel file of them would become dozens of one-line nodes
    bidding against real definitions. Resolving a re-export to the file it
    forwards to is a separate feature.
    """
    alias = node.child_by_field_name("alias")
    if alias is None:
        return None
    stmt = node.parent
    for _ in range(3):
        if stmt is None or stmt.type == "export_statement":
            break
        stmt = stmt.parent
    if stmt is not None and stmt.child_by_field_name("source") is not None:
        return None
    return _text(src, alias).strip() or None


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


# The C++/Kotlin base-clause colon (`class A : public B`) is punctuation and
# must become whitespace; the C++/Rust "::" scope operator inside a qualified
# base name (`NS::Base`, `std::runtime_error`) is part of the name and must
# not. A blanket `.replace(":", " ")` turns "::" into two spaces and leaves
# only the namespace segment as `words[0]` -- the class name after it is lost
# entirely, not merely left unresolved (#341). This matches a lone ":" only.
_SINGLE_COLON_RE = re.compile(r"(?<!:):(?!:)")


def _clean_base(text: str) -> str:
    """'public B', 'extends B', '< B' -> 'B'; 'public NS::Base' -> 'NS::Base'."""
    words = [
        w
        for w in _SINGLE_COLON_RE.sub(" ", text).replace("<", " <").split()
        if w and w not in _BASE_WORDS and w not in ("<", ">", "&", "*", ",")
    ]
    return words[0].split("(")[0].strip(",;") if words else ""


def _base_clauses(node: Node) -> list[Node]:
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


def _bases_with_details(
    src: bytes, node: Node, lang: str
) -> tuple[list[str], list[dict[str, Any]]]:
    """Extract supertype names along with their relationship subtype and raw expression."""
    out: list[str] = []
    details: list[dict[str, Any]] = []
    if lang == "objc" and node.type == "class_interface":
        # `@interface Cache : NSObject <Store, Codable>`
        sup = node.child_by_field_name("superclass")
        if sup is not None:
            out.append(_text(src, sup))
            details.append({"name": out[-1], "subtype": "EXTENDS", "raw": out[-1]})
        for c in node.children:
            if c.type == "parameterized_arguments":
                for arg in c.named_children:
                    out.append(_text(src, arg).strip())
                    details.append({"name": out[-1], "subtype": "IMPLEMENTS", "raw": out[-1]})
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
    uniq_details: list[dict[str, Any]] = []
    # strict: `out` and `details` are appended in lockstep in the loop above,
    # one entry each per accepted name, so they cannot diverge without a bug
    # in that loop.
    for b, d in zip(out, details, strict=True):
        if b not in seen:
            seen.add(b)
            uniq_names.append(b)
            uniq_details.append(d)
    return uniq_names[:8], uniq_details[:8]


_cpp_available_cache: bool | None = None

# Backslash-newline splices are joined before directives are recognised, so
# "#\<newline>include" is still an include; drop them first.
_CPP_SPLICE_RE = re.compile(rb"\\\r?\n")
# `#` or its `%:` digraph, then blanks or /* */ comments, then any directive
# that makes cpp open another file. `#embed` is C23.
_CPP_INCLUDE_RE = re.compile(
    rb"^[ \t]*(?:#|%:)(?:[ \t]|/\*[^*]*\*+(?:[^/*][^*]*\*+)*/)*(?:include|include_next|import|embed)\b[^\n]*",
    re.MULTILINE,
)


CPP_TIMEOUT = 10
_CPP_READ_BLOCK = 1 << 16

#: Memory the `cpp` child may commit. `_run_cpp` bounds the output *this*
#: process reads, which is not the same thing: cpp allocates while expanding
#: and before it prints anything, so a bomb can be killed for its output while
#: having already taken the machine's memory to get there. The 40-deep doubling
#: macro bomb in `tests/test_cpp_parse.py` drove `cc1` to **32.6 GB** on a CI
#: runner whose commit limit is 33.6 GB. That never failed the parse -- it
#: failed whatever else on the machine next asked for memory, which is how it
#: was eventually found: unrelated tests dying with `MemoryError`,
#: `WinError 1455` and `can't start new thread`.
#:
#: 1 GB against a 0.74 s / <100 MB worst case across the five benchmark corpora
#: (see `parsepool`'s module docstring for that calibration).
CPP_MEMORY_MB = 1024

# Job Object plumbing: Windows has no rlimits, which is why `parsepool`'s
# `_cap_memory` is Linux-only, but a job is the equivalent and -- unlike an
# rlimit set on `cpp` itself -- covers the `cc1` it spawns, which is the
# process that actually allocates.
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


def _win_cap_child_memory(pid: int, cap_bytes: int) -> int | None:
    """Put `pid` and its descendants under a job capping per-process memory.

    Returns the job handle, which the caller must keep alive: the job dies with
    its last handle, and `KILL_ON_JOB_CLOSE` makes that take any surviving cpp
    with it rather than leaving one behind holding memory.

    Best effort. On any failure this returns None and the wall-clock bound in
    `_run_cpp` is what remains, which is the behaviour this had before.
    """
    if sys.platform != "win32":  # pragma: no cover - platform-guarded
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rt", "wt", "ot")]

        class _BASIC(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_void_p),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _EXTENDED(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BASIC),
                ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]

        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _EXTENDED()
        info.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_PROCESS_MEMORY | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        info.ProcessMemoryLimit = cap_bytes
        if not k32.SetInformationJobObject(
            wintypes.HANDLE(job),
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            k32.CloseHandle(wintypes.HANDLE(job))
            return None
        handle = k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not handle:
            k32.CloseHandle(wintypes.HANDLE(job))
            return None
        try:
            if not k32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(handle)):
                k32.CloseHandle(wintypes.HANDLE(job))
                return None
        finally:
            k32.CloseHandle(wintypes.HANDLE(handle))
        # `int(...)`: `CreateJobObjectW` is declared `restype = wintypes.HANDLE`,
        # so on win32 ctypes hands back `Any` and this function's `int | None`
        # was unprovable there -- visible only under `mypy --platform win32`,
        # since `wintypes` is a stub-level no-op elsewhere. A Windows HANDLE is
        # an integer, and `_win_close_job` already takes it as `int | None`.
        return int(job)
    except (OSError, AttributeError, ValueError):  # pragma: no cover - defensive
        return None


def _win_close_job(job: int | None) -> None:
    if job is None or sys.platform != "win32":  # pragma: no cover - platform-guarded
        return
    try:
        import ctypes
        from ctypes import wintypes

        ctypes.WinDLL("kernel32").CloseHandle(wintypes.HANDLE(job))
    except (OSError, AttributeError, ValueError):  # pragma: no cover - defensive
        pass


def _run_cpp(source: bytes, limit: int, timeout: float = CPP_TIMEOUT) -> tuple[int, bytes | None]:
    """Preprocess `source` on stdin; return (returncode, output or None if over `limit`).

    Streamed rather than `run(capture_output=True)`: macros expand
    exponentially (`#define A B B`, `#define B C C`, ...), so a few hundred
    bytes of hostile source can make cpp print gigabytes, all of which
    `capture_output` would hold in this process before any size check ran.
    Here the read stops one byte past `limit` and cpp is killed.

    Never pass text=True to a subprocess reading cpp output on Windows -- it
    decodes with the cp1252 locale and raises UnicodeDecodeError on UTF-8
    source. tree-sitter's parser.parse() wants bytes anyway (.github/CONTRIBUTING.md).
    """
    import threading

    proc = subprocess.Popen(
        ["cpp", "-w", "-P", "-undef", "-"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    # Capped as early as the pid exists. cpp has to start, read stdin and begin
    # expanding before it can allocate anything worth capping, so assigning it
    # here rather than spawning it suspended is enough in practice.
    job = _win_cap_child_memory(proc.pid, CPP_MEMORY_MB * 1024 * 1024)
    assert proc.stdin is not None and proc.stdout is not None
    stdin, stdout = proc.stdin, proc.stdout

    def _feed() -> None:
        try:
            stdin.write(source)
        except OSError:
            pass  # cpp exited early or was killed: nothing left to feed
        finally:
            try:
                stdin.close()
            except OSError:
                pass

    got: list[bytes] = []

    def _drain() -> None:
        buf = bytearray()
        try:
            while len(buf) <= limit:
                block = stdout.read(min(_CPP_READ_BLOCK, limit + 1 - len(buf)))
                if not block:
                    break
                buf.extend(block)
        except (OSError, ValueError):
            pass
        got.append(bytes(buf))

    writer = threading.Thread(target=_feed, daemon=True)
    reader = threading.Thread(target=_drain, daemon=True)
    writer.start()
    reader.start()
    reader.join(timeout)
    timed_out = reader.is_alive()
    over = bool(got) and len(got[0]) > limit
    if timed_out or over:
        try:
            proc.kill()
        except OSError:
            pass
    reader.join(1)
    writer.join(1)
    try:
        stdout.close()
    except OSError:
        pass
    try:
        rc = proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        rc = -1
    # After the wait: closing the job kills anything still in it, which is the
    # point, but doing it earlier would race the kill above.
    _win_close_job(job)
    if timed_out:
        raise subprocess.TimeoutExpired(proc.args, timeout)
    if over:
        return 0, None
    return rc, got[0] if got else b""


def _strip_cpp_includes(source: bytes) -> bytes:
    """`source` with every file-opening directive blanked, for the cpp retry.

    The retry only needs this file's own macros expanded, and its output is
    only scored, never adopted. Following includes would let an untrusted file
    point cpp at `/dev/zero`, a FIFO or any readable path on the machine.
    Line numbers are irrelevant here (`-P` drops them anyway).
    """
    return _CPP_INCLUDE_RE.sub(b"", _CPP_SPLICE_RE.sub(b"", source))


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
        m = PY_FROM_IMPORT_RE.match(raw_clean)
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
        m2 = PY_IMPORT_RE.match(raw_clean)
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
        m = JAVA_IMPORT_RE.search(raw_clean)
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

    elif lang == "hcl":
        m = re.search(r'source\s*=\s*"([^"]+)"', raw_clean)
        if m:
            details.append(ImportDetail(raw=raw_clean, module=m.group(1)))
            return details

    elif lang in ("c", "cpp", "objc"):
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
        m = RUBY_REQUIRE_RE.search(raw_clean)
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


def normalize_source_bytes(source: bytes) -> bytes:
    """If source has UTF BOM (UTF-16/UTF-32), decode and re-encode to UTF-8 for tree-sitter (W10)."""
    if source.startswith(
        (b"\xef\xbb\xbf", b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff", b"\xff\xfe", b"\xfe\xff")
    ):
        for enc in ("utf-8-sig", "utf-16", "utf-32"):
            try:
                return source.decode(enc).encode("utf-8")
            except (UnicodeDecodeError, LookupError):
                continue
    return source


def parse_source(
    source: bytes,
    lang: str,
    filepath: Path | str | None = None,
    refs: bool = False,
    frameworks: bool = False,
) -> ParsedFile:
    cfg = LANG_CFG.get(lang)
    parser = parser_for(lang)
    if cfg is None or parser is None:
        # `cfg is None` means the language is not one we extract from -- an
        # ordinary, expected outcome. `parser is None` with a cfg present means a
        # grammar we *do* support could not be loaded, which is an environment
        # fault and must stay distinguishable from a file that simply declares
        # nothing. See `grammar_unavailable` on ParsedFile.
        return ParsedFile(
            lang=lang,
            symbols=[],
            imports=[],
            grammar_unavailable=cfg is not None and parser is None,
        )
    source = normalize_source_bytes(source)
    tree = parser.parse(source)

    def _count_errors(node: Node) -> int:
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
        if Path(filepath).suffix.lower() in (".c", ".cc", ".cpp", ".h", ".hpp") and (
            _cpp_available()
        ):
            try:
                rc, cpp_bytes = _run_cpp(_strip_cpp_includes(source), 2 * len(source))
                if rc == 0 and cpp_bytes is None:
                    diagnostic(
                        f"repo2graph: warning: cpp output for {filepath} is too large, skipping"
                    )
                elif rc == 0 and cpp_bytes is not None:
                    cpp_tree = parser.parse(cpp_bytes)
                    cpp_errors = _count_errors(cpp_tree.root_node)
                    if cpp_errors < errors:
                        # Do not adopt cpp_bytes or cpp_tree. cpp is invoked
                        # with -P, which strips `# <linenum> "<file>"` markers,
                        # so preprocessed row numbers cannot be mapped back to
                        # the on-disk file. chunks.py always slices the
                        # original, and storing cpp rows desyncs every
                        # citation. used_cpp still records that a macro-aware
                        # retry produced fewer ERROR nodes.
                        used_cpp = True
            except (OSError, subprocess.SubprocessError):
                pass

    kind_map, call_types, import_types = cfg["kind_map"], cfg["call_types"], cfg["import_types"]
    symbols: list[Symbol] = []
    imports: list[str] = []
    # `imports` is capped per-entry below to bound the exported artifact size.
    # parse_import_details needs the untruncated text -- a multi-binding import
    # (`import { a, ..., z } from "mod"`) longer than the cap loses its closing
    # brace and module path, so every regex in parse_import_details misses and
    # import_details silently comes back empty for that statement.
    imports_full: list[tuple[str, int]] = []
    final_errors = 0

    # Explicit stack rather than recursion: tree-sitter trees nest deeply enough
    # (long chained expressions, big literals) to blow the interpreter's limit.
    stack: list[tuple[Node, tuple[str, ...], Symbol | None]] = [(tree.root_node, (), None)]
    # id(Go method symbol) -> its receiver identifier: `c.find()` inside
    # `func (c *Cache) Lookup()` is a call on the calling object.
    go_self: dict[int, frozenset[str]] = {}
    # symbol keys already taken in this file -- see `disambiguate_key`.
    used_keys: set[str] = set()
    rs = _RefState() if refs and lang in REF_LANGS else None
    want_fw = frameworks and lang in FRAMEWORK_LANGS
    ref_of = _py_ref if lang == "python" else _js_ref
    while stack:
        node, scope, owner = stack.pop()
        ntype = node.type
        if ntype == "ERROR":
            final_errors += 1
        if ntype in import_types:
            raw = _text(source, node).strip()
            if raw:
                imports.append(raw[:300])
                imports_full.append((raw, node.start_point[0] + 1))
        elif lang == "ruby" and ntype == "call":
            callee = _callee_name(source, node)
            if callee in ("require", "require_relative", "load"):
                raw = _text(source, node).strip()
                if raw:
                    imports.append(raw[:300])
                    imports_full.append((raw, node.start_point[0] + 1))
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
                    imports_full.append((raw, node.start_point[0] + 1))
        elif lang == "hcl" and owner is not None:
            if ntype == "variable_expr" and (ref := _hcl_reference(source, node)):
                # A reference between blocks is Terraform's dependency edge;
                # it is recorded as a call so it resolves like one.
                owner.calls.append(ref)
                owner.call_details.append(
                    {"name": ref, "kind": "static", "line": node.start_point[0] + 1}
                    | _receiver_fields("", False)
                )
            elif (
                ntype == "attribute"
                and owner.kind == "module"
                and node.named_children
                and _text(source, node.named_children[0]) == "source"
            ):
                raw = _text(source, node).strip()
                imports.append(raw[:300])
                imports_full.append((raw, node.start_point[0] + 1))
        if ntype in call_types:
            callee = _callee_name(source, node)
            # file-scope calls (owner is None) produce no edge in graph.build,
            # so drop them here rather than accumulating dead data.
            if callee and owner is not None:
                call_kind = "static"
                if ntype in ("macro_invocation", "macro_call"):
                    call_kind = "possible"
                elif callee in DYNAMIC_CALLEES.get(lang, frozenset()):
                    call_kind = "dynamic"
                owner.calls.append(callee)
                recv_head, recv_static = _receiver_of(source, node)
                owner.call_details.append(
                    {
                        "name": callee,
                        "kind": call_kind,
                        "line": node.start_point[0] + 1,
                        **_receiver_fields(
                            recv_head, recv_static, go_self.get(id(owner), frozenset()), lang
                        ),
                    }
                )
        if rs is not None and owner is not None:
            ref_of(source, node, owner, rs)
        kind = kind_map.get(ntype)
        value_name = None
        if rs is not None and kind is None and ntype == "assignment" and lang == "python":
            kind, value_name = _py_value_symbol(source, node, owner)
        child_scope, child_owner = scope, owner
        if kind is not None:
            name = value_name or _name_of(source, node, lang)
            if kind == "maybe_function":
                value = node.child_by_field_name("value")
                if value is None or value.type not in (
                    "arrow_function",
                    "function",
                    "function_expression",
                ):
                    # Not a function, but an exported binding whose value is a
                    # bare reference is an alias for an existing definition and
                    # is the name consumers import.
                    kind = "alias" if _exported_declarator_alias(source, node) else None
                    if kind is None and rs is not None and name:
                        kind = _js_value_kind(node, owner, name)
                else:
                    kind = "function"
            elif kind == "maybe_alias":
                alias_name = _export_specifier_alias(source, node)
                # The specifier's `name` field is the target; the symbol is the
                # alias, so `name` has to be replaced, not just the kind.
                name, kind = (alias_name, "alias") if alias_name else (name, None)
            elif kind == "hcl":
                kind, name = _hcl_symbol(source, node) if owner is None else (None, None)
            if kind and lang == "kotlin" and ntype == "class_declaration":
                if any(c.type == "interface" for c in node.children):
                    kind = "interface"
            if kind and name:
                name = clean_identifier(name)
                bases_list, base_details = _bases_with_details(source, node, lang)
                # Every base in one class header cites that header's line.
                for _bd in base_details:
                    _bd["line"] = node.start_point[0] + 1
                # Receiver-qualified top-level methods: a Go method and a Kotlin
                # extension function are declared outside their type, so the
                # lexical scope alone would give `func (a *A) Run()` and
                # `func (b B) Run()` the same qualname `Run` -- one node id, one
                # chunk, and the other body gone. Qualify them as
                # `<ReceiverType>.<name>`, like every other language's methods,
                # so the same_class tier can resolve `a.step()` to `A.step`.
                sym_scope = scope
                recv_type = None
                if lang == "go" and ntype == "method_declaration":
                    recv_type = _go_receiver_type(source, node)
                elif lang == "kotlin" and ntype == "function_declaration" and not scope:
                    recv_type = _kotlin_receiver_type(source, node)
                if recv_type:
                    sym_scope = (*scope, clean_identifier(recv_type))
                qualname = clean_identifier(".".join((*sym_scope, name)), MAX_QUALNAME_CHARS)
                start_line = node.start_point[0] + 1
                sym = Symbol(
                    name=name,
                    qualname=qualname,
                    kind=kind,
                    start_line=start_line,
                    end_line=node.end_point[0] + 1,
                    parent=".".join(sym_scope) or None,
                    key=disambiguate_key(qualname, start_line, used_keys),
                    parent_key=(
                        owner.key if owner is not None and owner.key and not recv_type else ""
                    ),
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
                                sym.call_details.append(
                                    {
                                        "name": dec_text,
                                        "kind": "decorator",
                                        "line": child.start_point[0] + 1,
                                        **_decorator_receiver(source, child),
                                    }
                                )
                            if want_fw:
                                sym.frameworks.extend(route_from_decorator(source, child, lang))
                                prefix = mount_prefix(source, child)
                                if prefix is not None:
                                    sym.frameworks.append(
                                        {
                                            "kind": "mount",
                                            "path": prefix,
                                            "line": child.start_point[0] + 1,
                                        }
                                    )
                else:
                    # Java annotations or JS/TS decorators sit as the previous sibling.
                    prev = node.prev_sibling
                    if prev is not None and prev.type in ("annotation", "decorator"):
                        ann_text = _text(source, prev).strip().lstrip("@").split("(")[0].strip()
                        if ann_text:
                            sym.calls.append(ann_text)
                            sym.call_details.append(
                                {
                                    "name": ann_text,
                                    "kind": "decorator",
                                    "line": prev.start_point[0] + 1,
                                    **_decorator_receiver(source, prev),
                                }
                            )
                    if want_fw:
                        # Every consecutive preceding decorator, not just the
                        # nearest. NestJS stacks them -- `@UseGuards(AuthGuard)`
                        # above `@Get(':id')` is the ordinary shape of a guarded
                        # route -- so reading only `prev_sibling` found the
                        # route on some methods and not others depending on
                        # which decorator the author happened to put last.
                        walk = node.prev_sibling
                        while walk is not None:
                            if not walk.is_named:
                                # A keyword token, not a node: `export` sits
                                # between `@Controller(...)` and the
                                # `class_declaration` it decorates, so stopping
                                # at the first non-decorator sibling found the
                                # routes on a NestJS controller's methods and
                                # never the controller's own mount prefix --
                                # every route came out unprefixed.
                                walk = walk.prev_sibling
                                continue
                            if walk.type not in ("annotation", "decorator"):
                                break
                            sym.frameworks.extend(route_from_decorator(source, walk, lang))
                            prefix = mount_prefix(source, walk)
                            if prefix is not None:
                                sym.frameworks.append(
                                    {
                                        "kind": "mount",
                                        "path": prefix,
                                        "line": walk.start_point[0] + 1,
                                    }
                                )
                            walk = walk.prev_sibling

                if want_fw:
                    # Read off the definition itself rather than its
                    # decorators: a `Depends(...)` sits in the parameter list
                    # and an ORM relation in the class body.
                    sym.frameworks.extend(injected_dependencies(source, node))
                    if kind not in VALUE_KINDS:
                        sym.frameworks.extend(model_relations(source, node, lang))

                if lang == "go" and ntype == "method_declaration":
                    go_recv = _go_receiver_name(source, node)
                    if go_recv:
                        go_self[id(sym)] = frozenset({go_recv})
                symbols.append(sym)
                if kind not in VALUE_KINDS:
                    child_scope, child_owner = (*sym_scope, name), sym
        # named_children skips punctuation and keyword tokens: no configured
        # kind/call/import type is anonymous, and half the tree is those tokens.
        # reversed: the stack pops last-pushed first, so this keeps source order
        for c in reversed(node.named_children):
            stack.append((c, child_scope, child_owner))

    if rs is not None:
        _finish_refs(symbols, rs)

    # Parse import details from the untruncated text -- see imports_full above.
    import_details: list[ImportDetail] = []
    for raw, raw_line in imports_full:
        for detail in parse_import_details(raw, lang):
            # One statement can bind several names; they all cite the same line.
            detail.line = raw_line
            import_details.append(detail)

    return ParsedFile(
        import_lines=[line for _raw, line in imports_full],
        lang=lang,
        symbols=symbols,
        imports=imports,
        parse_errors=final_errors,
        used_cpp=used_cpp,
        import_details=import_details,
    )


# `discover` and `explain_path` moved to discovery.py (#315). Kept importable
# from here, lazily, because discovery builds on the definitions above.
_MOVED_TO_DISCOVERY = frozenset(
    {
        "_count_gitignored",
        "_git_files",
        "_glob_re",
        "_is_index_dir",
        "_output_rel_prefix",
        "_walk_files",
        "discover",
        "explain_path",
        "is_binary",
        "is_lfs_pointer",
        "matches_any",
    }
)


def __getattr__(name: str) -> object:
    if name in _MOVED_TO_DISCOVERY:
        from . import discovery

        return getattr(discovery, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
