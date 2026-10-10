"""Discovery, language configs, and tree-sitter based symbol/call extraction."""

import json
import os
import re
import stat as statmod
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast

from tree_sitter import Node, Parser

from .events import diagnostic
from .integrity import GIT_HARDENING_ARGS, _clean_git_env
from .security import MAX_QUALNAME_CHARS, clean_identifier, has_unsafe_path_chars

if TYPE_CHECKING:
    from .config import RepoConfig


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


_SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.S | re.I)
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
    secret_policy: str = "redact-match"
    extra_secret_keywords: list[str] = field(default_factory=list)
    extra_secret_dirs: list[str] = field(default_factory=list)
    parse_policy: str = "best-effort"
    # The directory this build writes its artifacts to. When it lies inside
    # the indexed root, discovery must never index it: `git ls-files -co`
    # lists untracked files and os.walk sees everything, so without this a
    # second `build . -o .r2g` cites its own chunks.jsonl / manifest.json.
    output_dir: str | None = None


# `<out>/agent/manifest.json` whose "format" starts with this marks a repo2graph index.
INDEX_MARKER_FORMAT = "repo2graph/"


#: `.<out>.staging.<pid>.<hex8>` and `.<out>.backup.<pid>`, from export.dump_all.
_SWAP_DIR_RE = re.compile(r"^\..+\.(?:staging\.\d+\.[0-9a-f]{8}|backup\.\d+)$")


def _is_index_dir(d: Path) -> bool:
    """True when `d` is a repo2graph output directory (any build, any -o)."""
    manifest = d / "agent" / "manifest.json"
    nodes = d / "agent" / "nodes.jsonl"
    try:
        # A valid index has both manifest.json and nodes.jsonl.
        # Requiring nodes.jsonl stops a planted agent/manifest.json from dropping subtrees (W20).
        if not manifest.is_file() or not nodes.is_file() or manifest.stat().st_size > 5_000_000:
            return False
        import json

        with open(manifest, encoding="utf8", errors="replace") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and str(data.get("format", "")).startswith(INDEX_MARKER_FORMAT)


def _output_rel_prefix(root: Path, config: "BuildConfig") -> tuple[str, ...] | None:
    """The configured output dir's path parts relative to `root`, if inside it."""
    if not config.output_dir:
        return None
    try:
        out = Path(config.output_dir).resolve()
        rel = out.relative_to(root)
    except (OSError, ValueError):
        return None
    return rel.parts or None


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
                *GIT_HARDENING_ARGS,
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
            env=_clean_git_env(),
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
        dirpath_p = Path(dirpath)
        # Exclude skip_dirs and submodules (.git inside subdirectories) so walk matches git discovery (W14).
        dirnames[:] = [
            d for d in dirnames if d not in skip_dirs and not (dirpath_p / d / ".git").exists()
        ]
        for fn in filenames:
            files.append(dirpath_p / fn)
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
    # On Windows, path matching is case-insensitive so globs don't silently no-op (W13).
    flags = re.IGNORECASE if os.name == "nt" else 0
    return re.compile("".join(out) + r"\Z", flags)


def matches_any(rel: str, patterns) -> bool:
    return any(_glob_re(p).match(rel) for p in patterns)


def is_lfs_pointer(header: bytes) -> bool:
    """True if header matches Git LFS pointer format (W16)."""
    return header.startswith(b"version https://git-lfs.github.com/spec/v1\n") or header.startswith(
        b"version https://git-lfs.github.com/spec/v1\r\n"
    )


def is_binary(path: Path) -> bool:
    """True if file contains null bytes (binary) and is not BOM-encoded text (W10).
    Raises OSError if unreadable (e.g. permission or missing sparse file) (W15).
    """
    p_str = str(path)
    if os.name == "nt" and len(p_str) >= 250 and not p_str.startswith("\\\\?\\"):
        p_str = "\\\\?\\" + os.path.abspath(p_str)
    with open(p_str, "rb") as fh:
        header = fh.read(4096)
    if not header:
        return False
    # Check for UTF BOMs (UTF-8, UTF-16, UTF-32) (W10)
    if header.startswith(
        (b"\xef\xbb\xbf", b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff", b"\xff\xfe", b"\xfe\xff")
    ):
        for enc in ("utf-8-sig", "utf-16", "utf-32"):
            try:
                header.decode(enc)
                return False  # Decodes as valid text with BOM
            except (UnicodeDecodeError, LookupError):
                continue
    return b"\0" in header


def _count_gitignored(root: Path) -> int:
    """How many untracked files .gitignore (or another exclude-standard rule)
    kept out of _git_files' listing.

    Purely a count for the human overview's "what was skipped" section --
    `_git_files` already applies `--exclude-standard` itself, so these files
    never reach `discover()`'s loop below and this never changes what is
    yielded. Same subprocess pattern as `_git_files`: quotepath=false, bytes
    decoded with surrogateescape (never text=True -- see .github/CONTRIBUTING.md), bounded
    timeout.
    """
    try:
        out = subprocess.run(
            [
                "git",
                *GIT_HARDENING_ARGS,
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--others",
                "--ignored",
                "--exclude-standard",
                # One entry per ignored directory rather than one per file in
                # it: a node_modules or venv otherwise costs a full walk and a
                # multi-megabyte listing on every build, just to be counted.
                "--directory",
            ],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=60,
            env=_clean_git_env(),
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

    walk_skip_dirs = set(skip_dirs)
    if include_globs:
        for pat in include_globs:
            parts = pat.strip("/").split("/")
            for part in parts:
                if "*" in part or "?" in part or "[" in part:
                    break
                walk_skip_dirs.discard(part)

    root = root.resolve()
    secret_dir_source = 0
    files = _git_files(root)
    if files is not None:
        if stats is not None:
            stats["discovery"] = "git"
            stats["skipped_gitignore"] = _count_gitignored(root)
    else:
        files = _walk_files(root, skip_dirs=walk_skip_dirs)
        if stats is not None:
            stats["discovery"] = "walk"
        # Not a git checkout is ordinary. A checkout where git itself failed
        # (git missing, dubious ownership, a corrupt index) is not: the walk
        # ignores .gitignore, so untracked and ignored files -- build output,
        # local secrets -- are indexed with nothing saying the file set changed.
        if (root / ".git").exists():
            if stats is not None:
                stats["discovery_git_failed"] = 1
            diagnostic(
                f"warning: {root} is a git checkout but `git ls-files` failed; "
                "falling back to a directory walk that does not honour .gitignore, "
                "so untracked and ignored files will be indexed"
            )

    # Discovery order *is* artifact order: node ids are emitted in the order
    # files are parsed, and edges and chunks follow the nodes. `git ls-files`
    # happens to sort its output, but `os.walk` yields whatever order the
    # filesystem hands back -- alphabetical on NTFS, hash order on ext4 with
    # dir_index. So the same tree indexed on two machines produced nodes.jsonl,
    # edges.jsonl and chunks.jsonl that differed byte-for-byte while describing
    # exactly the same graph.
    #
    # Sorting here rather than in _walk_files covers both sources with one
    # rule, so the invariant holds no matter which branch ran, and a future
    # third discovery source inherits it. The key is the resolved path's posix
    # form: str(Path) would sort on "\" on Windows and "/" elsewhere, which is
    # the same cross-machine divergence one level down.
    files = sorted(files, key=lambda p: p.as_posix())

    out_parts = _output_rel_prefix(root, config)
    index_dir_cache: dict[tuple[str, ...], bool] = {}

    def _inside_index(parts: tuple[str, ...]) -> bool:
        # Never index a repo2graph output directory: the one this build writes
        # to (explicit), or any directory an earlier build left behind (marker).
        if out_parts is not None and parts[: len(out_parts)] == out_parts:
            return True
        for i in range(1, len(parts)):
            prefix = parts[:i]
            hit = index_dir_cache.get(prefix)
            if hit is None:
                hit = index_dir_cache[prefix] = _is_index_dir(root.joinpath(*prefix))
            if hit:
                return True
        return False

    seen_case: set[str] = set()

    for abspath in files:
        try:
            rel = abspath.relative_to(root)
        except ValueError:
            continue
        if _inside_index(rel.parts):
            continue
        rp = rel.as_posix()
        # A filename that is not valid UTF-8 decodes to lone surrogates, which
        # no UTF-8 writer downstream (JSONL, overview, HTML) can encode. Skip it
        # and count it rather than crash the whole build on one file.
        try:
            rp.encode("utf8")
        except UnicodeEncodeError:
            if stats is not None:
                stats["skipped_undecodable_path"] = stats.get("skipped_undecodable_path", 0) + 1
            continue
        # A control or hidden character in a path forges structure wherever the
        # path is printed: a newline starts a fake `[cite: ...]` header in a pack.
        if has_unsafe_path_chars(rp):
            if stats is not None:
                stats["skipped_unsafe_path"] = stats.get("skipped_unsafe_path", 0) + 1
            continue
        # Case collision deduplication on case-insensitive filesystems (W17)
        if os.name == "nt" or sys.platform == "darwin":
            rp_lower = rp.lower()
            if rp_lower in seen_case:
                if stats is not None:
                    stats["skipped_case_collision"] = stats.get("skipped_case_collision", 0) + 1
                continue
            seen_case.add(rp_lower)

        # Precedence: explicit --include rescues directories otherwise dropped by skip_dirs (W11)
        rescued = bool(include_globs and matches_any(rp, include_globs))
        skip_part = next((part for part in rel.parts if part in skip_dirs), None)
        if skip_part is not None and not rescued:
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
        # dump_all's staging and backup siblings. A crash mid-swap leaves one
        # behind holding a full copy of an index, which is not source; when the
        # output lives under the root, discovery would otherwise index it.
        if any(_SWAP_DIR_RE.match(part) for part in rel.parts[:-1]):
            if stats is not None:
                stats["skipped_dotfile"] += 1
            continue
        try:
            st = abspath.lstat()
        except OSError:
            if stats is not None:
                stats["skipped_unreadable"] = stats.get("skipped_unreadable", 0) + 1
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
        if not config.include_secrets:
            from .security import _is_secret_path

            if _is_secret_path(
                rp,
                extra_keywords=config.extra_secret_keywords,
                extra_dirs=config.extra_secret_dirs,
            ):
                if stats is not None:
                    stats["skipped_secret"] += 1
                    # Source code dropped only for living under a `secrets/`-
                    # style directory is often the secret-handling code itself,
                    # not a secret: count it apart so the drop is visible.
                    if Path(rp).suffix.lower() in EXT_LANG and not _is_secret_path(
                        rel.name, extra_keywords=config.extra_secret_keywords
                    ):
                        secret_dir_source += 1
                continue
        if include_globs and not matches_any(rp, include_globs):
            continue
        if exclude_globs and matches_any(rp, exclude_globs):
            continue

        # Check LFS pointer file (W16)
        if st.st_size < 512:
            try:
                with open(abspath, "rb") as fh:
                    header = fh.read(256)
                if is_lfs_pointer(header):
                    if stats is not None:
                        stats["skipped_lfs"] = stats.get("skipped_lfs", 0) + 1
                    continue
            except OSError:
                pass

        try:
            if is_binary(abspath):
                if stats is not None:
                    stats["skipped_binary"] += 1
                continue
        except OSError:
            if stats is not None:
                stats["skipped_unreadable"] = stats.get("skipped_unreadable", 0) + 1
            continue
        yield rp, abspath

    if secret_dir_source:
        if stats is not None:
            stats["skipped_secret_dir_source"] = secret_dir_source
        diagnostic(
            f"note: {secret_dir_source} source file(s) under secret-named directories "
            "were not indexed; pass --include-secrets to index them"
        )


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

    def _norm(o):
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
    }


@lru_cache(maxsize=None)
def parser_for(lang: str):
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
    call_details: list[dict] = field(default_factory=list)
    base_details: list[dict] = field(default_factory=list)
    # Unique-in-file id key when `qualname` alone is not unique (overloads,
    # conditional redefinitions): "" means "same as qualname", which is every
    # first definition, so the common-case node id never changes. A later
    # duplicate gets `<qualname>@L<start_line>` -- see `symbol_key`.
    key: str = ""
    # The enclosing symbol's `key` when that differs from `parent` (a child of
    # a duplicate definition); "" means "same as parent".
    parent_key: str = ""


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


def _text(src: bytes, node) -> str:
    return src[node.start_byte : node.end_byte].decode("utf8", "replace")


def _name_of(src: bytes, node, lang: str) -> str | None:
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


def _receiver_of(src: bytes, node) -> tuple[str, bool]:
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
) -> dict:
    """The receiver keys of one `call_details` entry.

    "receiver" is always present (none/self/other). "receiver_head" is the
    receiver's source text when there is one, so `graph.build` can tell an
    imported module (`store.get()`) or a type (`Util.remove()`) from a value of
    unknown type (`d.get()`); "receiver_static" marks a `::`-scoped call.
    """
    out: dict = {"receiver": _classify_receiver(head, self_names, lang, static)}
    if head:
        out["receiver_head"] = head[:200]
    if static:
        out["receiver_static"] = True
    return out


def _go_receiver_name(src: bytes, node) -> str | None:
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


def _go_receiver_type(src: bytes, node) -> str | None:
    """The receiver type of a Go `func (c *T[K]) M()`, pointer/generics stripped."""
    params = node.child_by_field_name("receiver")
    if params is None:
        return None
    for p in params.named_children:
        t = p.child_by_field_name("type")
        if t is not None:
            return _clean_type_name(_text(src, t))
    return None


def _kotlin_receiver_type(src: bytes, node) -> str | None:
    """The receiver type of a Kotlin extension `fun Foo<T>.bar()`, or None."""
    for c in node.children:
        if c.type == "receiver_type":
            return _clean_type_name(_text(src, c))
    return None


def _decorator_receiver(src: bytes, node) -> dict:
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


def _hcl_words(src: bytes, block) -> list[str]:
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


def _hcl_symbol(src: bytes, node) -> tuple[str | None, str | None]:
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


def _hcl_reference(src: bytes, node) -> str | None:
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


def _docstring(src: bytes, node, lang: str) -> str:
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


def _signature(src: bytes, node) -> str:
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


def _alias_target(src: bytes, value) -> str | None:
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


def _exported_declarator_alias(src: bytes, node) -> str | None:
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


def _export_specifier_alias(src: bytes, node) -> str | None:
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
    if lang == "objc" and node.type == "class_interface":
        # `@interface Cache : NSObject <Store, Codable>`
        sup = node.child_by_field_name("superclass")
        if sup is not None:
            out.append(_text(src, sup))
            details.append({"name": out[-1], "subtype": "EXTENDS", "raw": out[-1]})
        for c in node.children:
            if c.type == "parameterized_arguments":
                for t in c.named_children:
                    out.append(_text(src, t).strip())
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
    uniq_details: list[dict] = []
    for b, d in zip(out, details):
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


def parse_source(source: bytes, lang: str, filepath: Path | str | None = None) -> ParsedFile:
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
                    # Not a function, but an exported binding whose value is a
                    # bare reference is an alias for an existing definition and
                    # is the name consumers import.
                    kind = "alias" if _exported_declarator_alias(source, node) else None
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
                    sym_scope = scope + (clean_identifier(recv_type),)
                qualname = clean_identifier(".".join(sym_scope + (name,)), MAX_QUALNAME_CHARS)
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

                if lang == "go" and ntype == "method_declaration":
                    go_recv = _go_receiver_name(source, node)
                    if go_recv:
                        go_self[id(sym)] = frozenset({go_recv})
                symbols.append(sym)
                child_scope, child_owner = sym_scope + (name,), sym
        # named_children skips punctuation and keyword tokens: no configured
        # kind/call/import type is anonymous, and half the tree is those tokens.
        # reversed: the stack pops last-pushed first, so this keeps source order
        for c in reversed(node.named_children):
            stack.append((c, child_scope, child_owner))

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


def explain_path(
    root: Path | str,
    target: Path | str,
    config: BuildConfig | None = None,
    include_globs=None,
    exclude_globs=None,
    repo_config: "RepoConfig | None" = None,
) -> dict:
    """Evaluate a path against the 10 inclusion/exclusion precedence rules.

    `repo_config` holds the settings a config file supplied (#391); a decision
    one of them made names that file instead of the CLI flag, and carries it
    as `source`.

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
    file_values = repo_config.values if repo_config is not None else {}
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
        if skip_part not in DEFAULT_SKIP_DIRS and skip_part in file_values.get(
            "extra_exclude_dirs", ()
        ):
            source = cast("RepoConfig", repo_config).label("extra_exclude_dirs")
            return {
                "path": str(target_path),
                "relative_path": rel_str,
                "included": False,
                "rule": "skip_dir",
                "reason": f"Path component '{skip_part}' is excluded by {source}",
                "source": source,
                "precedence_step": 2,
            }
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
                    *GIT_HARDENING_ARGS,
                    "-C",
                    str(root_path),
                    "check-ignore",
                    "-q",
                    str(target_path),
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=5,
                env=_clean_git_env(),
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
        except (OSError, subprocess.SubprocessError):
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
        from .security import _is_secret_path

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
        res = {
            "path": str(target_path),
            "relative_path": rel_str,
            "included": False,
            "rule": "not_included",
            "reason": f"Path does not match any --include glob: {include_globs}",
            "precedence_step": 8,
        }
        if "include" in file_values:
            source = cast("RepoConfig", repo_config).label("include")
            res["reason"] = f"Path does not match any glob in {source}: {include_globs}"
            res["source"] = source
        return res

    # Step 9: Exclude globs
    if exclude_globs and matches_any(rel_str, exclude_globs):
        # Name the one pattern that matched, not the whole list. `explain-path`
        # exists to report the single rule that decided a path, and a
        # `--exclude-group` expands to as many as 60 globs -- echoing all of
        # them buries the answer in the evidence.
        matched = next((g for g in exclude_globs if matches_any(rel_str, [g])), None)
        res = {
            "path": str(target_path),
            "relative_path": rel_str,
            "included": False,
            "rule": "exclude_glob",
            "reason": f"Path matches exclude glob {matched!r}",
            "matched_glob": matched,
            "precedence_step": 9,
        }
        if matched in file_values.get("exclude", ()):
            source = cast("RepoConfig", repo_config).label("exclude")
            res["reason"] = f"Path matches exclude glob {matched!r} from {source}"
            res["source"] = source
        return res

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
