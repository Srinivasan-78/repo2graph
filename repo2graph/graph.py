"""Build the repository graph: nodes + edges."""

import hashlib
import itertools
import os
import re
import subprocess
import sys
import threading
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from .parse import (
    CONFIG_EXT,
    DOC_EXT,
    EXT_LANG,
    ImportDetail,
    ParseError,
    ParsedFile,
    Symbol,
    discover,
    parse_source,
    sniff_header_lang,
)

# Under this many files a process pool costs more to start than it saves.
PARALLEL_MIN_FILES = 64
# A request for millions of commits makes git walk the whole history and emit
# every path in it. Co-change signal saturates long before this, so cap the
# window and record when we did.
MAX_COCHANGE_COMMITS = 5000
# Independent of MAX_COCHANGE_COMMITS (ISS-82): that bounds how many commits
# are requested, but a single pathological commit -- a vendor import touching
# hundreds of thousands of files -- can still emit an unbounded blob of paths
# within that commit count. Enforced during the read, not after a full
# capture_output() buffer has already grown past it: `add_cochange` streams the
# pipe and stops at this many bytes, then kills git (ISS-236).
MAX_COCHANGE_BYTES = 10 * 1024 * 1024  # 10 MB
# Wall clock for the whole `git log` read. `subprocess.run(timeout=...)` used to
# provide this; a streamed read has to enforce it itself.
COCHANGE_TIMEOUT = 120
# One read() per block. Big enough that a 10 MB cap is ~160 reads, small enough
# that the buffer never jumps far past the cap.
_COCHANGE_READ_BLOCK = 64 * 1024
# How long to wait for a killed child (and the thread reading it) to go away.
# Only a kernel in trouble takes this long; the build carries on regardless.
_COCHANGE_REAP_TIMEOUT = 10
# max_files bounds file count and is opt-in; nobody has to remember to pass
# it. This is not a hard cap (ISS-85 asks for a soft one) -- past this many
# nodes or edges a build just tells the operator on stderr, once, that memory
# use is growing unbounded and how to bound it.
LARGE_GRAPH_WARN_THRESHOLD = 50_000
# How many CALLS edges an ambiguous name is allowed to fan out to, each at 1/n
# confidence. Overridable per build (`build(max_call_candidates=)`, the
# `--max-call-candidates` flag), so it is a property of a *particular* index,
# not of repo2graph -- which is why the Graph carries the value it was built
# with and manifest.json reports that value rather than this default (#245).
DEFAULT_MAX_CALL_CANDIDATES = 5
# Base types common enough across languages (object/Exception/Error/...) that a
# same-named class anywhere in the repo would false-link unrelated hierarchies
# together. A same-file or imported definition still wins over this filter --
# it only blocks the repo-wide fallback tier from matching one of these names.
COMMON_STDLIB_BASES = frozenset(
    {
        "object",
        "Object",
        "Exception",
        "BaseException",
        "Error",
        "StandardError",
        "Throwable",
        "Record",
        "Any",
        "Interface",
        "Model",
        "Component",
        "Base",
    }
)


class GraphLimitExceeded(RuntimeError):
    """Raised when the graph exceeds a configured resource limit (ISS-85, ISS-156)."""

    pass


class Graph:
    def __init__(
        self,
        root: Path,
        name: str,
        max_files: int = 0,
        max_call_candidates: int = DEFAULT_MAX_CALL_CANDIDATES,
    ):
        self.root, self.name = root, name
        self.max_files = max_files
        # The ambiguous-call fan-out limit this graph was resolved under.
        # Carried on the Graph purely so the writers can report it: export's
        # manifest.json describes the artifacts it ships beside, and a manifest
        # claiming the default 5 for an index built with 2 is a wrong answer to
        # the one question the manifest exists to answer (#245). A Graph built
        # by hand keeps the default, which is what build() would have used.
        self.max_call_candidates = max_call_candidates
        self.config = None
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []
        self._edge_seen: set[tuple] = set()
        self.stats: Counter = Counter()
        # {relative path: sha256 of the bytes that were indexed}, written to
        # index.state.json so a later build can tell what actually changed.
        self.file_hashes: dict[str, str] = {}
        # {relative path: cache entry}, written to parse.cache.json so a later
        # `--incremental` build can skip re-parsing files that did not change.
        # Populated by every build, full or incremental, so the first full build
        # is what makes the next incremental one possible.
        self.parse_cache: dict[str, dict] = {}
        # Filled in by an incremental build only: {"cached": n, "reparsed": m}.
        # Deliberately *not* in `stats`, which is written to stats.json -- an
        # incremental build must produce byte-identical artifacts to a full one,
        # and a hit/miss count differs by construction between the two.
        self.incremental: dict[str, int] | None = None
        self._warned_large = False

    def add_node(self, nid: str, **attrs):
        if nid in self.nodes:
            # ISS-11: preserve legitimate 0 and False values on re-add
            self.nodes[nid].update(
                {
                    k: v
                    for k, v in attrs.items()
                    if v is not None
                    and v != ""
                    and (not isinstance(v, (list, tuple)) or len(v) > 0)
                }
            )
        else:
            max_nodes = getattr(self.config, "max_nodes", 0) if self.config else 0
            if max_nodes > 0 and len(self.nodes) >= max_nodes:
                raise GraphLimitExceeded(
                    f"Graph node limit exceeded: graph reached {len(self.nodes)} nodes (max_nodes={max_nodes}). "
                    "Use --max-nodes to increase the limit or filter with --include/--exclude."
                )
            self.nodes[nid] = dict(id=nid, **attrs)
        self._warn_if_large()
        return nid

    def add_edge(self, src: str, dst: str, etype: str, **attrs):
        key = (src, dst, etype)
        if key in self._edge_seen:
            return
        self._edge_seen.add(key)
        self.edges.append(dict(src=src, dst=dst, type=etype, **attrs))
        self.stats[f"edge:{etype}"] += 1
        self._warn_if_large()

    def _warn_if_large(self) -> None:
        if self._warned_large:
            return
        if (
            len(self.nodes) > LARGE_GRAPH_WARN_THRESHOLD
            or len(self.edges) > LARGE_GRAPH_WARN_THRESHOLD
        ):
            self._warned_large = True
            msg = (
                f"repo2graph: warning: graph has grown past {LARGE_GRAPH_WARN_THRESHOLD} "
                f"nodes/edges ({len(self.nodes)} nodes, {len(self.edges)} edges)"
            )
            if self.max_files > 0:
                msg += f" (max_files={self.max_files})."
            else:
                msg += " with no size limit set; pass max_files= to build() to bound memory use."
            print(msg, file=sys.stderr)


# ---------- import parsing ----------
_IMPORT_RE = {
    # `from` branch splits module (group 1) from the imported-names list (group
    # 2): a dots-only module ("from . import X") has no name of its own, so
    # import_targets() below appends each imported name to the dots instead of
    # discarding it (#160). The bare `import a, b` form is group 3, unchanged.
    "python": re.compile(r"^(?:from\s+(\.*[\w.]*)\s+import\s+([\w\s,*()]+)|import\s+([\w\.,\s]+))"),
    "js": re.compile(r"""['"]([^'"]+)['"]"""),
    "go": re.compile(r"""['"]([^'"]+)['"]"""),
    "rust": re.compile(r"use\s+([\w:]+)"),
    "java": re.compile(r"import\s+(?:static\s+)?([\w\.\*]+)"),
    "c": re.compile(r"""[<"]([^>"]+)[>"]"""),
    # C# `using System.Text;` / `using static System.Math;` / `using J = A.B.C;`
    "csharp": re.compile(r"using\s+(?:static\s+)?(?:[\w.]+\s*=\s*)?([\w.]+)"),
    # PHP `use App\Models\User;` / `use function App\f;` / `use App\U as U;`
    "php": re.compile(r"use\s+(?:function\s+|const\s+)?([\w\\]+)"),
    # Swift `import Foundation` / `import class UIKit.UIView`
    "swift": re.compile(
        r"import\s+(?:typealias|struct|class|enum|protocol|let|var|func\s+)?([\w.]+)"
    ),
    # Ruby `require "foo"` / `require_relative "bar"` / `load "baz.rb"`
    "ruby": re.compile(r"""(?:require_relative|require|load)\s*\(?\s*['"]([^'"]+)['"]"""),
    # Bash `source ./lib.sh` / `. ./lib.sh`
    "bash": re.compile(r"""(?:source|\.)\s+['"]?([^'"\s]+)['"]?"""),
}


def import_targets(raw: str, lang: str) -> list[str]:
    if lang == "python":
        m = _IMPORT_RE["python"].match(raw.strip())
        if not m:
            return []
        module = m.group(1)
        if module is not None:
            names = [
                p.strip().split(" as ")[0].strip()
                for p in m.group(2).replace("(", " ").replace(")", " ").split(",")
            ]
            names = [n for n in names if n and n != "*" and re.fullmatch(r"\w+", n)]
            if module and set(module) <= {"."}:
                # "from . import X" / "from .. import X, Y": no module name after
                # the dots, so the imported names ARE the submodule targets (#160).
                return [module + n for n in names] or [module]
            if module.startswith("."):
                # "from .mod import x": the dotted module already names a file;
                # neither #160 nor #161 changes this form.
                return [module]
            # "from pkg import a, b as c" -- capture the imported names so
            # resolve_import() can prefer pkg/a.py over pkg/__init__.py (#161).
            if names:
                return [f"{module}.{n}" for n in names]
            return [module]
        return [p.strip().split(" as ")[0].strip() for p in m.group(3).split(",") if p.strip()]
    if lang == "ruby":
        raw_stripped = raw.strip()
        m = _IMPORT_RE["ruby"].search(raw_stripped)
        if not m:
            m_quote = re.search(r"""['"]([^'"]+)['"]""", raw_stripped)
            if not m_quote:
                return []
            mod = m_quote.group(1)
        else:
            mod = m.group(1)
        if raw_stripped.startswith("require_relative") and not mod.startswith((".", "/")):
            return [f"./{mod}"]
        return [mod]
    if lang in ("bash", "sh"):
        m = _IMPORT_RE["bash"].search(raw.strip())
        if not m:
            return []
        return [m.group(1)]
    # Kotlin/Scala import with `import a.b.C`, like Java; C# uses
    # `using`, PHP uses `use A\B` — both need their own pattern, not Java's.
    key = {
        "javascript": "js",
        "typescript": "js",
        "tsx": "js",
        "kotlin": "java",
        "scala": "java",
        "cpp": "c",
    }.get(lang, lang)
    rx = _IMPORT_RE.get(key)
    if rx is None:
        return []
    targets = [m.group(1) for m in rx.finditer(raw)][:4]
    if lang == "rust":
        targets = [t.rstrip(":") for t in targets]
    return targets


def path_index(file_index) -> dict:
    """Lookup tables so import resolution never rescans the whole file list.

    by_name: basename -> sorted paths.  by_dir: directory -> sorted paths.
    Sorted so a repo with several same-named files resolves deterministically.
    """
    by_name: dict[str, list[str]] = defaultdict(list)
    by_dir: dict[str, list[str]] = defaultdict(list)
    for p in sorted(file_index):
        pp = Path(p)
        parent = pp.parent.as_posix()
        by_name[pp.name].append(p)
        by_dir["" if parent == "." else parent].append(p)
    return {"by_name": by_name, "by_dir": by_dir}


def resolve_import(
    target: str, from_path: str, lang: str, file_index: set[str], ctx: dict | None = None
) -> str | None:
    """Map an import target to an in-repo file path when possible."""
    if ctx is None:
        ctx = path_index(file_index)
    by_name, by_dir = ctx["by_name"], ctx["by_dir"]
    src_dir = Path(from_path).parent
    cands: list[str] = []
    if lang == "python":
        dots = len(target) - len(target.lstrip("."))
        if dots:  # relative import: walk up (dots - 1) packages from the source dir
            base_dir = src_dir
            for _ in range(dots - 1):
                base_dir = base_dir.parent
            rest = target[dots:].replace(".", "/")
            base = (base_dir / rest).as_posix() if rest else base_dir.as_posix()
            cands = [f"{base}.py", f"{base}/__init__.py"]
        else:
            base = target.replace(".", "/")
            cands = [f"{base}.py", f"{base}/__init__.py"]
            cands += [str(src_dir / c) for c in list(cands)]
            # also try src/ and package-rooted layouts
            cands += [f"src/{c}" for c in [f"{base}.py", f"{base}/__init__.py"]]
            tail = base.split("/")[-1]
            cands += [p for p in by_name.get(f"{tail}.py", []) if "/" in p][:1]
            # `from pkg import name` (#161): if `name` isn't a submodule file
            # (or package), it's a symbol defined directly in `pkg` -- either
            # `pkg.py` (pkg is itself a module, e.g. `from pkg.alpha import
            # handle`) or `pkg/__init__.py` (pkg is a package). Try both last,
            # only after every submodule-file candidate above.
            if "/" in base:
                parent = base.rsplit("/", 1)[0]
                cands.append(f"{parent}.py")
                cands.append(f"{parent}/__init__.py")
    elif lang in ("javascript", "typescript", "tsx"):
        if target.startswith("."):
            base = Path(src_dir, target).as_posix()
            base = re.sub(r"/\./", "/", base)
            while "/../" in base:
                base = re.sub(r"[^/]+/\.\./", "", base, count=1)
            stems = [base]
            for js in (".js", ".jsx", ".mjs", ".cjs"):
                if base.endswith(js):  # TS sources are imported with .js specifiers
                    stems.append(base[: -len(js)])
            for stem in stems:
                for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".d.ts"):
                    cands += [stem + ext, f"{stem}/index{ext}"]
            cands.append(base)
        else:
            cands = [f"src/{target}.ts", f"src/{target}.js"]
    elif lang == "go":
        module = ctx.get("go_module")
        if module and (target == module or target.startswith(module + "/")):
            pkg_dir = target[len(module) :].strip("/")
            cands = [
                p
                for p in by_dir.get(pkg_dir, [])
                if p.endswith(".go") and not p.endswith("_test.go")
            ][:1]
        elif module:
            cands = []  # module path known: anything outside it is a third-party package
        else:
            tail = target.split("/")[-1]
            cands = [
                p
                for d, paths in sorted(by_dir.items())
                if d.split("/")[-1] == tail
                for p in paths
                if p.endswith(".go")
            ][:1]
    elif lang in ("c", "cpp"):
        cands = by_name.get(target.split("/")[-1], [])[:1]
    elif lang == "java":
        rel = target.replace(".", "/") + ".java"
        cands = [rel]
        cands += [p for p in by_name.get(rel.split("/")[-1], []) if p.endswith(rel)][:1]
    elif lang == "rust":
        clean = target.rstrip(":")
        parts = clean.split("::")
        crate_name = ctx.get("rust_crate")
        if parts[0] == "crate" or (crate_name and parts[0] == crate_name):
            sub = parts[1:]
            if sub:
                rel = "/".join(sub)
                cands = [
                    f"src/{rel}.rs",
                    f"src/{rel}/mod.rs",
                    f"{rel}.rs",
                    f"{rel}/mod.rs",
                ]
                if len(sub) > 1:
                    parent = "/".join(sub[:-1])
                    cands += [
                        f"src/{parent}.rs",
                        f"src/{parent}/mod.rs",
                        f"{parent}.rs",
                        f"{parent}/mod.rs",
                    ]
        elif parts[0] == "super":
            base_dir = src_dir
            idx = 0
            while idx < len(parts) and parts[idx] == "super":
                base_dir = base_dir.parent
                idx += 1
            rest = "/".join(parts[idx:])
            if rest:
                base = (base_dir / rest).as_posix()
                cands = [f"{base}.rs", f"{base}/mod.rs"]
                if len(parts[idx:]) > 1:
                    p_base = (base_dir / "/".join(parts[idx:-1])).as_posix()
                    cands += [f"{p_base}.rs", f"{p_base}/mod.rs"]
            else:
                cands = [f"{base_dir.as_posix()}.rs", f"{base_dir.as_posix()}/mod.rs"]
        elif parts[0] == "self":
            rest = "/".join(parts[1:])
            if rest:
                base = (src_dir / rest).as_posix()
                cands = [f"{base}.rs", f"{base}/mod.rs"]
                if len(parts[1:]) > 1:
                    p_base = (src_dir / "/".join(parts[1:-1])).as_posix()
                    cands += [f"{p_base}.rs", f"{p_base}/mod.rs"]
        else:
            rel = "/".join(parts)
            cands = [
                f"src/{rel}.rs",
                f"src/{rel}/mod.rs",
                f"{rel}.rs",
                f"{rel}/mod.rs",
                str(src_dir / f"{rel}.rs"),
                str(src_dir / f"{rel}/mod.rs"),
            ]
            if len(parts) > 1:
                parent = "/".join(parts[:-1])
                cands += [
                    f"src/{parent}.rs",
                    f"src/{parent}/mod.rs",
                    f"{parent}.rs",
                    f"{parent}/mod.rs",
                ]
            tail = parts[-1]
            cands += [p for p in by_name.get(f"{tail}.rs", []) if "/" in p][:1]
            if len(parts) > 1:
                p_tail = parts[-2]
                cands += [p for p in by_name.get(f"{p_tail}.rs", []) if "/" in p][:1]
    elif lang == "csharp":
        parts = target.split(".")
        rel = target.replace(".", "/")
        cands = [f"{rel}.cs", f"src/{rel}.cs"]
        if len(parts) > 1:
            parent = rel.rsplit("/", 1)[0]
            cands += [f"{parent}.cs", f"src/{parent}.cs"]
        tail = parts[-1]
        cands += [p for p in by_name.get(f"{tail}.cs", []) if "/" in p][:1]
        if len(parts) > 1:
            p_tail = parts[-2]
            cands += [p for p in by_name.get(f"{p_tail}.cs", []) if "/" in p][:1]
    elif lang == "php":
        clean = target.replace("\\", "/").strip("/")
        parts = clean.split("/")
        cands = [f"{clean}.php", f"src/{clean}.php"]
        if clean.startswith("App/"):
            rest = clean[4:]
            cands += [f"app/{rest}.php", f"src/{rest}.php"]
        if len(parts) > 1:
            parent = clean.rsplit("/", 1)[0]
            cands += [f"{parent}.php", f"src/{parent}.php"]
            if parent.startswith("App/"):
                rest = parent[4:]
                cands += [f"app/{rest}.php", f"src/{rest}.php"]
        tail = parts[-1]
        cands += [p for p in by_name.get(f"{tail}.php", []) if "/" in p][:1]
    elif lang == "kotlin":
        parts = target.split(".")
        rel = target.replace(".", "/")
        cands = [
            f"{rel}.kt",
            f"src/main/kotlin/{rel}.kt",
            f"src/{rel}.kt",
        ]
        if len(parts) > 1:
            parent = rel.rsplit("/", 1)[0]
            cands += [
                f"{parent}.kt",
                f"src/main/kotlin/{parent}.kt",
                f"src/{parent}.kt",
            ]
        tail = parts[-1]
        cands += [p for p in by_name.get(f"{tail}.kt", []) if p.endswith(f"{tail}.kt")][:1]
        if len(parts) > 1:
            p_tail = parts[-2]
            cands += [p for p in by_name.get(f"{p_tail}.kt", []) if p.endswith(f"{p_tail}.kt")][:1]
    elif lang == "scala":
        parts = target.split(".")
        rel = target.replace(".", "/")
        cands = [
            f"{rel}.scala",
            f"src/main/scala/{rel}.scala",
            f"src/{rel}.scala",
        ]
        if len(parts) > 1:
            parent = rel.rsplit("/", 1)[0]
            cands += [
                f"{parent}.scala",
                f"src/main/scala/{parent}.scala",
                f"src/{parent}.scala",
            ]
        tail = parts[-1]
        cands += [p for p in by_name.get(f"{tail}.scala", []) if p.endswith(f"{tail}.scala")][:1]
        if len(parts) > 1:
            p_tail = parts[-2]
            cands += [
                p for p in by_name.get(f"{p_tail}.scala", []) if p.endswith(f"{p_tail}.scala")
            ][:1]
    elif lang == "swift":
        clean = target.split(".")[0]
        cands = [
            f"{target}.swift",
            f"{clean}.swift",
            f"Sources/{clean}/{clean}.swift",
            f"Sources/{clean}.swift",
            f"Sources/{clean}/main.swift",
        ]
        mod_files = [p for p in by_dir.get(f"Sources/{clean}", []) if p.endswith(".swift")]
        if mod_files:
            cands.append(mod_files[0])
        tail = target.split(".")[-1]
        cands += [p for p in by_name.get(f"{tail}.swift", []) if "/" in p][:1]
    elif lang == "ruby":
        if target.startswith("."):
            base = Path(src_dir, target).as_posix()
            base = re.sub(r"/\./", "/", base)
            while "/../" in base:
                base = re.sub(r"[^/]+/\.\./", "", base, count=1)
            cands = [f"{base}.rb", base]
        else:
            stem = target[: -len(".rb")] if target.endswith(".rb") else target
            cands = [
                str(src_dir / f"{stem}.rb"),
                f"lib/{stem}.rb",
                f"{stem}.rb",
                f"src/{stem}.rb",
            ]
            tail = stem.split("/")[-1]
            cands += [p for p in by_name.get(f"{tail}.rb", []) if "/" in p][:1]
    elif lang in ("bash", "sh"):
        if target.startswith((".", "/")):
            base = Path(src_dir, target).as_posix()
            base = re.sub(r"/\./", "/", base)
            while "/../" in base:
                base = re.sub(r"[^/]+/\.\./", "", base, count=1)
            cands = [base]
        else:
            cands = [
                str(src_dir / target),
                target,
                f"bin/{target}",
                f"scripts/{target}",
            ]
            tail = target.split("/")[-1]
            cands += [p for p in by_name.get(tail, []) if "/" in p][:1]
    for c in cands:
        c = Path(c).as_posix().removeprefix("./")
        if c in file_index:
            return c
    return None


def repo_context(root: Path) -> dict:
    """Repo-level facts used to resolve imports (Go module path, Rust crate name)."""
    ctx: dict = {}
    gomod = root / "go.mod"
    if gomod.exists():
        for line in gomod.read_text("utf8", "replace").split("\n"):
            if line.startswith("module "):
                ctx["go_module"] = line.split(None, 1)[1].strip()
                break
    cargo = root / "Cargo.toml"
    if cargo.exists():
        in_pkg = False
        for line in cargo.read_text("utf8", "replace").split("\n"):
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                in_pkg = stripped == "[package]"
            elif in_pkg and stripped.startswith("name"):
                parts = stripped.split("=", 1)
                if len(parts) == 2:
                    crate_name = parts[1].strip().strip('"').strip("'")
                    ctx["rust_crate"] = crate_name.replace("-", "_")
                    break
    return ctx


# ---------- parsing ----------
@dataclass
class ChunkedParsedFile(ParsedFile):
    """What `_chunk_and_parse` produces: a `ParsedFile` plus what it could not read.

    `undecodable_slices` does not belong on `ParsedFile` itself -- it is
    meaningless for the whole-file reader, which either decodes a file or does
    not. Every reader of the field uses `getattr(pf, ..., 0)`, the same way
    `build()` already reads `is_chunked` and `used_cpp`.
    """

    undecodable_slices: int = 0


# The longest UTF-8 sequence is four bytes, so at most three bytes of one can be
# left dangling at the end of a slice cut at an arbitrary byte offset.
_UTF8_MAX_SEQ = 4


def _incomplete_utf8_tail(buf: bytes) -> int:
    """Length of the *truncated* UTF-8 sequence at the end of `buf`, else 0.

    ISS-196: slices are taken at raw byte offsets, so a multi-byte character can
    straddle a boundary -- its lead byte ends slice N and its continuation bytes
    begin slice N+1. Both then fail to decode and *both* were dropped, losing up
    to 2 x max_file_bytes of source with nothing recording it. Reporting the
    length of the dangling prefix lets the caller carry those bytes into the next
    slice so the boundary lands on a character boundary instead.

    Only a genuinely truncated sequence counts. A complete character, a stray
    continuation byte with no lead, and a byte that can never start a sequence
    (0xF8..0xFF) all return 0, so invalid bytes are still reported as invalid
    rather than carried forward forever.
    """
    for back in range(1, _UTF8_MAX_SEQ):
        if back > len(buf):
            return 0
        b = buf[-back]
        if b < 0x80:
            return 0  # ASCII: nothing is dangling
        if b < 0xC0:
            continue  # continuation byte: its lead byte is further back
        need = 2 if b < 0xE0 else 3 if b < 0xF0 else 4 if b < 0xF8 else 0
        return back if need > back else 0
    return 0


def _chunk_and_parse(rel, abspath, lang, config, size):
    chunk_size = config.max_file_bytes
    all_symbols = []
    all_imports = []
    total_parse_errors = 0
    used_cpp = False
    undecodable_slices = 0

    line_offset = 0
    # Streamed, not accumulated: `raw_content = bytearray()` held the entire
    # file for the digest and the line count, so the one path max_file_bytes
    # exists to bound had no memory bound at all (ISS-196). sha256 over the raw
    # bytes in file order and a running newline count are exactly the values the
    # buffered version produced, byte for byte.
    hasher = hashlib.sha256()
    total_bytes = 0
    newlines = 0
    # Bytes of a character whose sequence ran off the end of the previous slice.
    # Never more than three, and never a newline (a newline is ASCII and cannot
    # be part of a multi-byte sequence), so `line_offset` accounting is unaffected.
    carry = b""
    eof = False

    with _safe_open(abspath) as f:
        while not eof:
            # Read a slice short by whatever is carried, so each parsed slice is
            # still exactly chunk_size bytes. max(1, ...) only matters for a
            # chunk_size of three or less: it keeps the loop making progress
            # instead of reading zero bytes forever.
            chunk = f.read(max(1, chunk_size - len(carry)))
            if chunk:
                hasher.update(chunk)
                total_bytes += len(chunk)
                newlines += chunk.count(b"\n")
            else:
                eof = True
            buf, carry = carry + chunk, b""
            if not eof:
                tail = _incomplete_utf8_tail(buf)
                if tail:
                    buf, carry = buf[:-tail], buf[-tail:]
            # At EOF a dangling sequence is genuinely truncated rather than
            # straddling, so it stays in `buf` and is reported below, once.
            if not buf:
                continue

            try:
                buf.decode("utf-8")
            except UnicodeDecodeError:
                # Not a boundary artefact -- these bytes are not UTF-8 at all.
                # Count them so a file that quietly loses most of itself is
                # visible in stats.json instead of looking healthy.
                undecodable_slices += 1
                line_offset += buf.count(b"\n")
                continue

            pf = parse_source(buf, lang, filepath=None)
            if pf is None:
                line_offset += buf.count(b"\n")
                continue

            for sym in pf.symbols:
                sym.start_line += line_offset
                sym.end_line += line_offset
                all_symbols.append(sym)

            all_imports.extend(pf.imports)
            total_parse_errors += pf.parse_errors
            if pf.used_cpp:
                used_cpp = True

            line_offset += buf.count(b"\n")

    seen = set()
    deduped_symbols = []
    qualname_counts: Counter[str] = Counter()

    for sym in all_symbols:
        key = (sym.name, sym.start_line)
        if key in seen:
            continue
        seen.add(key)

        original_qualname = sym.qualname
        count = qualname_counts[original_qualname]
        if count > 0:
            sym.qualname = f"{original_qualname}_{count}"
        qualname_counts[original_qualname] += 1

        deduped_symbols.append(sym)

    pf = ChunkedParsedFile(
        lang=lang,
        symbols=deduped_symbols,
        imports=list(set(all_imports)),
        parse_errors=total_parse_errors,
        used_cpp=used_cpp,
        is_chunked=True,
        undecodable_slices=undecodable_slices,
    )

    return rel, lang, (total_bytes, newlines + 1, pf, hasher.hexdigest())


_ORIGINAL_READ_BYTES = Path.read_bytes


def _safe_read_bytes(path: Path) -> bytes:
    """Read file bytes using O_NOFOLLOW where supported to avoid symlink TOCTOU races (ISS-87).

    On POSIX systems, O_NOFOLLOW causes open() to fail if the trailing component is a
    symlink (protecting against an attacker replacing a discovered regular file with a
    symlink to a sensitive file before open). On Windows, O_NOFOLLOW is not supported
    by the OS open(), so standard read flags are used.
    """
    if Path.read_bytes is not _ORIGINAL_READ_BYTES:
        return path.read_bytes()
    o_nofollow = getattr(os, "O_NOFOLLOW", None)
    if o_nofollow is not None:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | o_nofollow
        fd = os.open(path, flags)
        try:
            with os.fdopen(fd, "rb") as fh:
                return fh.read()
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
    return path.read_bytes()


def _safe_open(path: Path, mode: str = "rb"):
    """Open a file with O_NOFOLLOW where supported (ISS-87).

    Like _safe_read_bytes, but returns a file object for chunked reading
    (used by _chunk_and_parse for files larger than config.max_file_bytes).
    """
    if Path.read_bytes is not _ORIGINAL_READ_BYTES:
        return open(path, mode)  # noqa: SIM115 -- test harness monkey-patched read_bytes
    o_nofollow = getattr(os, "O_NOFOLLOW", None)
    if o_nofollow is not None:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | o_nofollow
        fd = os.open(path, flags)
        try:
            return os.fdopen(fd, mode)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
    return open(path, mode)  # noqa: SIM115


def _read_and_parse(item):
    """Read one file and parse it if it is code.

    Top level, and returns only counts plus the ParsedFile, so a process pool
    can pickle both the call and its result. The fourth element of the read
    tuple is the sha256 of the bytes just parsed: it is computed here because
    this is the only place that holds them, and build() never keeps them.
    """
    rel, abspath, lang, config = item
    if config is None:
        from .parse import BuildConfig

        config = BuildConfig()

    try:
        st = abspath.lstat()
        size = st.st_size
        if size > config.max_file_bytes and config.chunk_large_files:
            return _chunk_and_parse(rel, abspath, lang, config, size)
    except OSError:
        pass

    try:
        raw = _safe_read_bytes(abspath)
    except OSError:
        return rel, lang, None
    if lang == "c" and abspath.suffix.lower() == ".h":
        lang = sniff_header_lang(raw)
    policy = getattr(config, "parse_policy", "best-effort")
    pf = None
    try:
        pf = parse_source(raw, lang, filepath=abspath) if lang else None
    except Exception as exc:
        if policy == "strict":
            raise ParseError(
                f"Strict parse policy: failed to parse '{rel}' as {lang}: {exc}"
            ) from exc
        pf = None

    if pf is not None and getattr(pf, "parse_errors", 0) > 0:
        if policy == "strict":
            raise ParseError(
                f"Strict parse policy: '{rel}' produced {pf.parse_errors} syntax error(s) under language '{lang}'"
            )
        if policy == "warn":
            sys.stderr.write(
                f"repo2graph: warning: '{rel}' produced {pf.parse_errors} syntax error(s) ({lang})\n"
            )

    return rel, lang, (len(raw), raw.count(b"\n") + 1, pf, hashlib.sha256(raw).hexdigest())


# ---------- parse cache (incremental builds) ----------
# Bumped whenever a cache entry's shape changes. A cache written by an older
# repo2graph is ignored wholesale rather than half-read: a `Symbol` that gained
# a field would otherwise reconstruct with a silently wrong default, and a wrong
# symbol is exactly the "wrong in a way nothing detects" failure this feature
# was cut for in the first place.
# 2: chunked entries gained "undecodable_slices" (ISS-196). A cache written by
# format 1 has no way to report it, and an incremental build restoring one would
# report a 0 where a full build reports the real count -- the one thing an
# incremental build is not allowed to do.
# 3: `Symbol` gained "call_details"/"base_details" and `ParsedFile` gained
# "import_details". A format-2 entry has none of the three: every cached call
# would silently reconstruct as call_kind='static' even where the original was
# a decorator/dynamic call, every cached base would reconstruct with
# subtype='INHERITS' regardless of what it actually was, and every cached
# file's import aliases would come back as [] -- an aliased call that should
# resolve `import_alias` would instead fall through to unresolved_external.
PARSE_CACHE_FORMAT = 3


def cache_entry(lang: str | None, size: int, lines: int, pf, digest: str) -> dict:
    """Serialise one file's parse result for `parse.cache.json`.

    Args:
        lang: Language id the file was parsed as, or None for a non-code file.
        size: Length in bytes of the file as indexed.
        lines: Newline count + 1, as recorded on the file node.
        pf: The `ParsedFile` for this file, or None if it was not parsed.
        digest: sha256 hex digest of the bytes this entry describes.

    Returns:
        A JSON-serialisable dict holding everything `build()` needs to rebuild
        this file's nodes and edges without re-reading or re-parsing it.
    """
    return {
        "sha256": digest,
        "lang": lang or "",
        "size": size,
        "lines": lines,
        "parsed": None
        if pf is None
        else {
            "lang": pf.lang,
            "parse_errors": pf.parse_errors,
            "used_cpp": pf.used_cpp,
            "is_chunked": getattr(pf, "is_chunked", False),
            # Only the chunked reader can lose a slice, and only it sets this.
            "undecodable_slices": getattr(pf, "undecodable_slices", 0),
            "imports": list(pf.imports),
            "symbols": [asdict(s) for s in pf.symbols],
            # Without this, entry_read() rebuilds import_details as [] and an
            # incremental cache hit loses every import alias -- an aliased call
            # that a full build resolves as `import_alias` falls back to
            # unresolved_external on the next incremental build instead.
            "import_details": [asdict(d) for d in getattr(pf, "import_details", [])],
        },
    }


def entry_read(entry: dict) -> tuple | None:
    """Rebuild `_read_and_parse`'s result tuple from a cache entry.

    Args:
        entry: One record out of `parse.cache.json`.

    Returns:
        The `(size, lines, ParsedFile | None, digest)` tuple the build loop
        consumes, or None if the entry is malformed. A malformed entry is a
        cache miss, never an exception: a corrupt cache must cost a re-parse,
        not the build.
    """
    try:
        size, lines = int(entry["size"]), int(entry["lines"])
        digest = str(entry["sha256"])
        raw = entry.get("parsed")
        if raw is None:
            return size, lines, None, digest
        symbols = [Symbol(**s) for s in raw["symbols"]]
        import_details = [ImportDetail(**d) for d in raw.get("import_details", [])]
        pf: ParsedFile
        # A chunked entry restores as the same class a fresh chunked read
        # produces, so a cached file and a re-parsed one are indistinguishable
        # -- including to `==`, which a dataclass only answers true for within
        # one class.
        if raw.get("is_chunked"):
            pf = ChunkedParsedFile(
                lang=str(raw["lang"]),
                symbols=symbols,
                imports=[str(i) for i in raw["imports"]],
                parse_errors=int(raw.get("parse_errors") or 0),
                used_cpp=bool(raw.get("used_cpp") or False),
                is_chunked=True,
                undecodable_slices=int(raw.get("undecodable_slices") or 0),
                import_details=import_details,
            )
        else:
            pf = ParsedFile(
                lang=str(raw["lang"]),
                symbols=symbols,
                imports=[str(i) for i in raw["imports"]],
                parse_errors=int(raw.get("parse_errors") or 0),
                used_cpp=bool(raw.get("used_cpp") or False),
                is_chunked=False,
                import_details=import_details,
            )
        return size, lines, pf, digest
    except (KeyError, TypeError, ValueError):
        return None


def parse_incremental(files, jobs: int, cache: dict, counts: dict, config=None):
    """Read every file, but re-parse only the ones whose bytes changed.

    A file's `ParsedFile` is a pure function of its bytes and its language and
    nothing else, so reusing one for a file whose sha256 still matches is exact
    -- not an approximation. Everything downstream of parsing (the global name
    index, CALLS confidences, INHERITS, entrypoints and reach) is then recomputed
    from scratch over the full symbol set by `build()`, which is what makes an
    incremental build byte-identical to a full one instead of merely close.

    Reading is still done for every file: the hash *is* the bytes, so there is
    no cheaper way to know a file is unchanged, and reading is the small half of
    the cost. Parsing is what this skips, and parsing is what dominates a build.

    Args:
        files: The `(relpath, abspath)` pairs discovery produced, in order.
        jobs: Parser process count, passed through to `parse_all`.
        cache: `{relpath: entry}` loaded from a previous build's parse cache.
        counts: Mutated in place with "cached" and "reparsed" tallies.
        config: BuildConfig

    Returns:
        The same list of `(rel, lang, read)` tuples `parse_all` returns, in
        discovery order, so the build loop cannot tell the two apart.
    """
    results: dict[str, tuple] = {}
    order: list[str] = []
    stale: list[tuple] = []
    for rel, abspath in files:
        order.append(rel)
        lang = EXT_LANG.get(abspath.suffix.lower())
        try:
            raw = _safe_read_bytes(abspath)
        except OSError:
            results[rel] = (rel, lang, None)
            continue
        if lang == "c" and abspath.suffix.lower() == ".h":
            lang = sniff_header_lang(raw)
        digest = hashlib.sha256(raw).hexdigest()
        entry = cache.get(rel)
        read = None
        # The language must match too: the same bytes parsed as a different
        # language yield different symbols, and a renamed extension changes the
        # language without changing the content hash.
        if (
            isinstance(entry, dict)
            and entry.get("sha256") == digest
            and entry.get("lang") == (lang or "")
        ):
            read = entry_read(entry)
        if read is None:
            stale.append((rel, abspath))
        else:
            results[rel] = (rel, lang, read)
            counts["cached"] = counts.get("cached", 0) + 1
    counts["reparsed"] = len(stale)
    for rel, lang, read in parse_all(stale, jobs, config=config):
        results[rel] = (rel, lang, read)
    return [results[rel] for rel in order]


def resolve_jobs(jobs: int) -> int:
    """0 means one worker per core, capped so the parent keeps up with results."""
    if jobs > 0:
        return jobs
    return max(1, min(os.cpu_count() or 1, 8))


# Win32 GetStdHandle/SetStdHandle slot numbers. os.dup2 rewrites the CRT's fd
# table, which is what `print`, `sys.stdout` and any C extension writing to the
# CRT `stdout` go through -- but it does *not* touch the process-wide Win32
# standard handles, so a library that calls `WriteFile(GetStdHandle(...))`
# directly would still reach the handle the worker inherited. These are the two
# slots `silence_worker_io` repoints for that last route.
_STD_INPUT_HANDLE = 0xFFFFFFF6  # (DWORD)-10
_STD_OUTPUT_HANDLE = 0xFFFFFFF5  # (DWORD)-11


def silence_worker_io() -> None:
    """Detach a parse worker from the stdin/stdout it inherited (#90).

    Runs as the `ProcessPoolExecutor` initializer, i.e. *inside* the worker and
    once per worker. It exists because of who the parent may be: when the pool
    is started from `repo2graph-mcp`, fd 0 and fd 1 are the client's JSON-RPC
    pipes. Measured on Windows (spawn), a worker inherits both for real --
    `os.write(1, ...)` lands in the parent's stdout pipe, and `os.read(0, 1)`
    *consumes a byte of the client's request stream*, which is a request the
    server then waits for forever. Parsing writes to neither today; this makes
    that a property of the pool rather than of the current worker body.

    Redirection is done with `os.dup2` onto `os.devnull`, not by rebinding
    `sys.stdout`, because a Python-only swap leaves fd 1 -- and therefore every
    C-level write and anything that cached `fileno()` -- pointing at the pipe.
    `sys.stdin`/`sys.stdout` are rebound afterwards so their buffers are not
    holding anything from before the swap; on Windows the Win32 std handles are
    repointed too (see above).

    Every step is best effort: a worker that cannot open devnull must still
    parse files. Nothing here raises, because an initializer that raises breaks
    the pool for every task.
    """
    try:
        devnull = os.open(os.devnull, os.O_RDWR)
    except OSError:
        return
    try:
        for fd in (0, 1):
            try:
                os.dup2(devnull, fd)
            except OSError:
                pass
    finally:
        if devnull > 2:
            try:
                os.close(devnull)
            except OSError:
                pass
    for name, fd, mode in (("stdin", 0, "r"), ("stdout", 1, "w")):
        try:
            setattr(sys, name, open(fd, mode, closefd=False))
        except OSError:
            pass
    if sys.platform == "win32":
        try:
            import ctypes
            import msvcrt

            kernel32 = getattr(ctypes, "windll").kernel32
            kernel32.SetStdHandle.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
            kernel32.SetStdHandle.restype = ctypes.c_int
            kernel32.SetStdHandle(_STD_INPUT_HANDLE, msvcrt.get_osfhandle(0))
            kernel32.SetStdHandle(_STD_OUTPUT_HANDLE, msvcrt.get_osfhandle(1))
        except Exception:
            pass


def parse_all(files, jobs: int, config=None):
    """Read and parse every file, in discovery order, across `jobs` processes.

    tree-sitter parsing is CPU bound and dominates a large build, so this is
    the difference between one core and all of them. Order is preserved, which
    keeps node ids and edge order identical to a serial run.

    Every worker runs `silence_worker_io` first, so the pool is safe to start
    from a process whose stdin/stdout are a protocol stream rather than a
    terminal -- which is what `repo2graph-mcp`'s auto-build is (#90).
    """
    jobs = resolve_jobs(jobs)
    items = [(rel, abspath, EXT_LANG.get(abspath.suffix.lower()), config) for rel, abspath in files]
    if jobs == 1 or len(items) < PARALLEL_MIN_FILES:
        return [_read_and_parse(i) for i in items]
    import concurrent.futures

    try:
        # Note: accessed as concurrent.futures.ProcessPoolExecutor to allow monkeypatching in tests (NC-6)
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=jobs, initializer=silence_worker_io
        ) as pool:
            return list(
                pool.map(_read_and_parse, items, chunksize=max(1, len(items) // (jobs * 8)))
            )
    except ParseError:
        # A strict-policy failure is the worker doing its job, not a broken
        # pool -- falling through to the serial fallback below would just
        # re-parse every file a second time and raise this same error again.
        raise
    except Exception:
        # No fork / no POSIX semaphores to build on, a BrokenProcessPool, a
        # worker ImportError, or a pickling failure on the call or its result:
        # the comment above promises a serial fallback, so honour it for all of
        # them rather than aborting the whole build.
        return [_read_and_parse(i) for i in items]


# ---------- build ----------
def build(
    root: Path,
    include=None,
    exclude=None,
    git_history: int = 0,
    max_files: int = 0,
    jobs: int = 0,
    cache: dict | None = None,
    max_call_candidates: int = DEFAULT_MAX_CALL_CANDIDATES,
    config=None,
    cochange_min: int = 3,
) -> Graph:
    """Parse `root` into a Graph.

    Args:
        root: Repository directory to index.
        include: Optional glob(s) restricting discovery.
        exclude: Optional glob(s) removing paths from discovery.
        git_history: When non-zero, add CO_CHANGE edges from the last N commits.
        max_files: When positive, index only the first N discovered files.
        jobs: Parser processes; 0 means one per core, 1 means serial.
        cache: A previous build's `{relpath: entry}` parse cache. When given,
            files whose sha256 and language both still match are not re-parsed.
            Resolution is recomputed in full either way, so the resulting Graph
            is identical to one built with `cache=None`.
        max_call_candidates: An ambiguous call name fans out to at most this
            many CALLS edges, each at 1/n confidence. Clamped to >= 1, and the
            clamped value is recorded on the returned Graph so the writers can
            state the limit this build actually used.
        config: BuildConfig

    Returns:
        The populated Graph. `parse_cache` holds the cache for the *next*
        build; `incremental` holds hit/miss counts when `cache` was supplied.
    """
    max_call_candidates = max(1, max_call_candidates)
    root = Path(root).resolve()
    g = Graph(root, root.name, max_files=max_files, max_call_candidates=max_call_candidates)
    g.config = config
    repo_id = f"repo:{root.name}"
    g.add_node(repo_id, type="repo", name=root.name, path=".")

    files = list(discover(root, include, exclude, stats=g.stats, config=config))
    if max_files > 0:  # a negative limit must not become files[:-n] and drop the tail
        files = files[:max_files]
    file_index = {rel for rel, _ in files}
    ctx = repo_context(root)
    ctx.update(path_index(file_index))
    # Only ParsedFile, never the raw bytes -- keeping those too would hold the
    # whole repo in memory.
    parsed: dict[str, ParsedFile] = {}

    if cache is None:
        results = parse_all(files, resolve_jobs(jobs), config=config)
    else:
        counts: dict[str, int] = {"cached": 0, "reparsed": 0}
        results = parse_incremental(files, resolve_jobs(jobs), cache, counts, config=config)
        g.incremental = counts

    for rel, lang, read in results:
        if read is None:  # unreadable file
            continue
        size, lines, pf, digest = read
        g.file_hashes[rel] = digest
        g.parse_cache[rel] = cache_entry(lang, size, lines, pf, digest)
        ext = Path(rel).suffix.lower()
        if ext == ".h":
            # Auditable record of the #377 content sniff: how many `.h` files
            # were kept on the C grammar vs. promoted to cpp.
            g.stats["header_files_as_cpp" if lang == "cpp" else "header_files_as_c"] += 1
        ftype = (
            "code"
            if lang
            else ("doc" if ext in DOC_EXT else "config" if ext in CONFIG_EXT else "other")
        )
        fid = f"file:{rel}"
        is_chunked = getattr(pf, "is_chunked", False) if pf else False
        g.add_node(
            fid,
            type="file",
            name=Path(rel).name,
            path=rel,
            lang=lang or ext.lstrip("."),
            file_type=ftype,
            size=size,
            lines=lines,
            parse_errors=pf.parse_errors if pf else 0,
            chunked=is_chunked,
        )
        g.stats["files"] += 1

        # directory chain
        parent = repo_id
        parts = Path(rel).parts[:-1]
        for i in range(len(parts)):
            dpath = "/".join(parts[: i + 1])
            did = f"dir:{dpath}"
            g.add_node(did, type="dir", name=parts[i], path=dpath)
            g.add_edge(parent, did, "CONTAINS")
            parent = did
        g.add_edge(parent, fid, "CONTAINS")

        if pf is None:
            continue
        parsed[rel] = pf
        g.stats["parsed"] += 1
        g.stats["parse_errors"] += pf.parse_errors
        if pf.parse_errors > 0:
            g.stats["files_with_parse_errors"] += 1
        if getattr(pf, "used_cpp", False):
            g.stats["cpp_fallback_files"] += 1
        # ISS-196: a chunked file whose slices do not decode still gets a node,
        # a `chunked: true` flag and a correct line count, so the index looks
        # healthy while the file is simply unqueryable. This is the only signal
        # that any of it was lost.
        undecodable = getattr(pf, "undecodable_slices", 0)
        if undecodable:
            g.stats["chunk_slices_undecodable"] += undecodable
            g.stats["files_with_undecodable_chunks"] += 1

        for sym in pf.symbols:
            sid = f"sym:{rel}::{sym.qualname}"
            g.add_node(
                sid,
                type="symbol",
                name=sym.name,
                qualname=sym.qualname,
                kind=sym.kind,
                path=rel,
                lang=lang,
                start_line=sym.start_line,
                end_line=sym.end_line,
                signature=sym.signature,
                docstring=sym.docstring,
            )
            g.stats[f"symbol:{sym.kind}"] += 1
            owner = f"sym:{rel}::{sym.parent}" if sym.parent else fid
            g.add_edge(owner, sid, "DEFINES")

        for raw_imp in pf.imports:
            for target in import_targets(raw_imp, lang):
                resolved = resolve_import(target, rel, lang, file_index, ctx)
                if resolved:
                    g.add_edge(fid, f"file:{resolved}", "IMPORTS", target=target, internal=True)
                    g.stats["imports_resolved"] += 1
                else:
                    mid = f"module:{target}"
                    g.add_node(mid, type="module", name=target, external=True)
                    g.add_edge(fid, mid, "IMPORTS", target=target, internal=False)
                    g.stats["imports_unresolved"] += 1

    # ----- name index for call/inheritance resolution -----
    imported_files: dict[str, set[str]] = defaultdict(set)
    for e in g.edges:
        if e["type"] == "IMPORTS" and e["src"].startswith("file:") and e["dst"].startswith("file:"):
            caller_rel = e["src"].split(":", 1)[1]
            callee_rel = e["dst"].split(":", 1)[1]
            imported_files[caller_rel].add(callee_rel)

    # Import aliases per file: alias -> (module, original name).
    import_aliases: dict[str, dict[str, tuple[str, str | None]]] = defaultdict(dict)
    for rel, pf in parsed.items():
        for imp in getattr(pf, "import_details", []):
            if imp.alias:
                import_aliases[rel][imp.alias] = (imp.module, imp.name)

    by_name: dict[str, list[str]] = defaultdict(list)
    # node id -> its file's directory. Tier 4 below used to rebuild
    # `Path(...).parent` once per candidate per callee inside the build's hot
    # loop; precomputing it here turns that into a dict lookup.
    sym_dir: dict[str, str] = {}
    for nid, n in g.nodes.items():
        if n["type"] == "symbol":
            by_name[n["name"]].append(nid)
            sym_dir[nid] = n.get("path", "").rpartition("/")[0]

    def tier3_imported(rel: str, callee: str, all_cands: list[str]) -> tuple[list[str], str]:
        """Candidates reachable through `rel`'s own imports, and how they matched."""
        imported = imported_files.get(rel)
        if not imported:
            return [], ""
        alias = import_aliases.get(rel, {}).get(callee)
        if alias is not None:
            target_name = alias[1] or callee
            cands = [c for c in by_name.get(target_name, []) if g.nodes[c].get("path") in imported]
            return (cands, "import_alias") if cands else ([], "")
        cands = [c for c in all_cands if g.nodes[c].get("path") in imported]
        return (cands, "imported_symbol") if cands else ([], "")

    for rel, pf in parsed.items():
        rel_dir = rel.rpartition("/")[0]
        for sym in pf.symbols:
            sid = f"sym:{rel}::{sym.qualname}"
            call_kinds: dict[str, str] = {}
            for cd in getattr(sym, "call_details", []):
                call_kinds[cd["name"]] = cd.get("kind", "static")

            for callee, count in Counter(sym.calls).items():
                call_kind = call_kinds.get(callee, "static")
                all_cands = by_name.get(callee, [])

                chosen_cands: list[str] = []
                res_kind = ""
                scope_dist = 0

                if not sym.parent and sid in all_cands:
                    # Tier 0: direct recursion. `sid` is in `all_cands` only when
                    # the callee is this symbol's own name, and for a top-level
                    # function that is unambiguously a self-call. Without it the
                    # tiers below hand the call to a same-named method elsewhere
                    # in the file at confidence 1.0 and the recursion edge is lost.
                    chosen_cands, res_kind, scope_dist = [sid], "self_recursive", 0
                elif (
                    sym.parent
                    and (tier1 := f"sym:{rel}::{sym.parent}.{callee}") in g.nodes
                    and tier1 != sid
                ):
                    # Tier 1: same class / enclosing scope. A symbol's id is
                    # f"sym:{path}::{parent}.{name}" by construction -- parse.py
                    # builds qualname as parent + "." + name -- so this exact
                    # lookup already finds every same-file same-parent candidate;
                    # the scan it replaces could only rediscover this same id, and
                    # tested a `parent` attribute symbol nodes never carry.
                    #
                    # `tier1 != sid` is not the self-exclusion this rewrite
                    # removed from the tiers below. `_callee_name` drops the
                    # receiver, so inside `DoctorReport.to_dict` the calls
                    # `self.to_dict()` and `c.to_dict()` are the same string
                    # "to_dict" here -- self-recursion and a call to a sibling
                    # class's identically-named method are indistinguishable.
                    # Binding that to `sid` at confidence 1.0 gets doctor.py:61
                    # (`[c.to_dict() for c in self.checks]`) confidently wrong,
                    # so a self-target falls through to tier 2 instead, which
                    # *includes* the caller: whichever reading is right, the
                    # true target is in the candidate set and the ambiguity is
                    # priced at 1/n rather than hidden.
                    chosen_cands = [tier1]
                    res_kind, scope_dist = "same_class", 0
                elif tier2 := [c for c in all_cands if g.nodes[c].get("path") == rel]:
                    chosen_cands, res_kind, scope_dist = tier2, "same_file", 1
                elif (tier3 := tier3_imported(rel, callee, all_cands))[0]:
                    chosen_cands, res_kind, scope_dist = tier3[0], tier3[1], 2
                elif tier4 := [c for c in all_cands if sym_dir[c] == rel_dir]:
                    chosen_cands, res_kind, scope_dist = tier4, "same_module", 3
                elif len(all_cands) == 1:
                    chosen_cands, res_kind, scope_dist = all_cands, "unique_global_name", 4
                elif len(all_cands) > 1:
                    chosen_cands, res_kind, scope_dist = all_cands, "ambiguous_global_name", 5

                # Add edges
                if not chosen_cands:
                    eid = f"external:{callee}"
                    g.add_node(eid, type="external", name=callee)
                    g.add_edge(
                        sid,
                        eid,
                        "CALLS_EXTERNAL",
                        count=count,
                        resolution_kind="unresolved_external",
                        candidate_count=0,
                        call_kind=call_kind,
                    )
                    g.stats["calls_external"] += 1
                elif len(chosen_cands) == 1:
                    g.add_edge(
                        sid,
                        chosen_cands[0],
                        "CALLS",
                        count=count,
                        confidence=1.0,
                        resolution_kind=res_kind,
                        candidate_count=len(all_cands),
                        scope_distance=scope_dist,
                        call_kind=call_kind,
                    )
                    if res_kind == "unique_global_name":
                        g.stats["calls_unique_global"] += 1
                    else:
                        g.stats["calls_scoped"] += 1
                else:
                    limit = min(len(chosen_cands), max_call_candidates)
                    conf = round(1.0 / limit, 3) if limit > 0 else 0.0
                    for c in chosen_cands[:limit]:
                        g.add_edge(
                            sid,
                            c,
                            "CALLS",
                            count=count,
                            confidence=conf,
                            ambiguous=True,
                            resolution_kind=res_kind,
                            candidate_count=len(all_cands),
                            scope_distance=scope_dist,
                            call_kind=call_kind,
                        )
                    g.stats["calls_ambiguous"] += 1
                    g.stats["ambiguous_calls"] += 1

            # Inheritance resolution
            base_details_map = {bd["name"]: bd for bd in getattr(sym, "base_details", [])}
            for base in sym.bases:
                bd = base_details_map.get(base, {})
                raw_base = bd.get("raw", base)
                subtype = bd.get("subtype", "INHERITS")
                # #341: strip C++/Rust "::" and PHP "\" scope/namespace
                # separators too, not just Python/Java "." -- otherwise a
                # namespaced base like `NS::Base` or `\App\Models\Base` never
                # matches the bare name `by_name` indexes symbols under.
                clean_base = (
                    base.split("[")[0]
                    .split("<")[0]
                    .split("::")[-1]
                    .split("\\")[-1]
                    .split(".")[-1]
                    .strip()
                )

                base_cands = by_name.get(clean_base, [])
                local_base = [c for c in base_cands if g.nodes[c].get("path") == rel]
                imp_base = [
                    c
                    for c in base_cands
                    if g.nodes[c].get("path") in imported_files.get(rel, set())
                ]

                if local_base:
                    matched_base = local_base[:1]
                elif imp_base:
                    matched_base = imp_base[:1]
                elif clean_base not in COMMON_STDLIB_BASES:
                    matched_base = base_cands[:max_call_candidates]
                else:
                    matched_base = []

                if matched_base:
                    for c in matched_base:
                        g.add_edge(
                            sid,
                            c,
                            "INHERITS",
                            subtype=subtype,
                            raw_base=raw_base,
                        )
                else:
                    g.stats["unresolved_bases"] += 1

    if git_history:
        add_cochange(g, root, git_history, file_index, min_pairs=cochange_min)

    # Every edge endpoint must be a node. A file can be in file_index (so an
    # IMPORTS target resolves to it, and git log pairs it) yet have no file:
    # node because the main loop skipped it as unreadable — that would leave a
    # dangling edge that turns into a phantom node in the GraphML export.
    before = len(g.edges)
    g.edges = [e for e in g.edges if e["src"] in g.nodes and e["dst"] in g.nodes]
    if len(g.edges) != before:
        g.stats["edges_pruned_dangling"] += before - len(g.edges)

    mark_entrypoints(g)
    g.stats["nodes"] = len(g.nodes)
    g.stats["edges"] = len(g.edges)
    g.stats["parse_errors_summary"] = (
        f"Files with parse errors: {g.stats.get('files_with_parse_errors', 0)}  ({g.stats.get('cpp_fallback_files', 0)} C/C++ files used cpp fallback)"  # type: ignore[assignment]
    )
    return g


ENTRY_KINDS = ("function", "method")
SCORED_ENTRYPOINTS = 200  # exact reach is a BFS each, so only rank the busiest


def mark_entrypoints(g: Graph):
    """Flag the call-graph roots: symbols nothing else in the repo calls.

    Those are the doors into a codebase — CLI commands, request handlers, test
    bodies, public API — and they are where a reader tracing a flow has to
    start. A symbol nested inside a function is skipped: an uncalled closure is
    dead weight, not a door. `reach` (how many symbols the root can reach
    through CALLS) is filled in for the busiest roots only, so ranking them
    stays cheap on a big repo.
    """
    called, out = set(), defaultdict(list)
    for e in g.edges:
        if e["type"] == "CALLS":
            # #342: a self-recursive function's own CALLS edge (src == dst)
            # must not disqualify it from being an entrypoint root -- nothing
            # *else* calls it. `out` still records the self-edge so `_reach`
            # sees it; it is just harmless there since `start` is already
            # in `seen` before the BFS looks at its own outgoing edges.
            if e["src"] != e["dst"]:
                called.add(e["dst"])
            out[e["src"]].append(e["dst"])
    nested = {
        e["dst"]
        for e in g.edges
        if e["type"] == "DEFINES" and g.nodes.get(e["src"], {}).get("kind") in ENTRY_KINDS
    }
    roots = [
        nid
        for nid, n in g.nodes.items()
        if n["type"] == "symbol"
        and n.get("kind") in ENTRY_KINDS
        and nid not in called
        and nid not in nested
    ]
    for nid in roots:
        g.nodes[nid]["entrypoint"] = True
    roots.sort(key=lambda nid: (-len(out.get(nid, ())), nid))
    for nid in roots[:SCORED_ENTRYPOINTS]:
        g.nodes[nid]["reach"] = _reach(nid, out)
    g.stats["entrypoints"] = len(roots)


def _reach(start: str, out: dict) -> int:
    """How many distinct symbols `start` reaches through CALLS edges."""
    seen, stack = {start}, [start]
    while stack:
        for dst in out.get(stack.pop(), ()):
            if dst not in seen:
                seen.add(dst)
                stack.append(dst)
    return len(seen) - 1


def _read_capped(stream, limit: int) -> tuple[bytes, bool]:
    """Read at most `limit` bytes from `stream`, and report whether there were more.

    Reads one byte past the cap deliberately: that byte is the only way to tell
    "the output was exactly `limit` bytes" from "the output was larger and we
    stopped early" without reading the rest of it -- and not reading the rest of
    it is the entire point (ISS-236).
    """
    buf = bytearray()
    while len(buf) <= limit:
        block = stream.read(min(_COCHANGE_READ_BLOCK, limit + 1 - len(buf)))
        if not block:
            return bytes(buf), False
        buf.extend(block)
    return bytes(buf[:limit]), True


def _reap_child(proc, reader=None) -> None:
    """Kill a child, let go of its pipe and collect its exit status.

    Order matters. The child is killed *first*: a reader parked in read() only
    comes back when the write end disappears, and closing the read end out from
    under it is unsafe on both platforms. Every step is best-effort -- a build
    must not fail because a doomed `git log` was slow to die.
    """
    try:
        if proc.poll() is None:
            proc.kill()
    except OSError:
        pass
    if reader is not None:
        reader.join(_COCHANGE_REAP_TIMEOUT)
    if proc.stdout is not None:
        try:
            proc.stdout.close()
        except OSError:
            pass
    try:
        proc.wait(timeout=_COCHANGE_REAP_TIMEOUT)
    except subprocess.TimeoutExpired:
        pass


def add_cochange(g: Graph, root: Path, commits: int, file_index: set[str], min_pairs: int = 3):
    """CO_CHANGE edges from files edited together in the last N commits.

    Formula and semantics:
    - History depth: Scans up to `commits` commits (capped at MAX_COCHANGE_COMMITS = 5000).
    - Merge handling: `--no-merges` skips merge commits to avoid false co-change correlations.
    - Noise filter: Commits touching > 25 files are skipped as bulk refactors/noise.
    - Path filtering: Only paths matching `file_index` (current indexed tree) are paired.
    - Pair counting: Each commit touching 2..25 files increments pair count by +1 for all pairs.
    - Threshold: Emits an edge if pair count >= `min_pairs` (default: 3, configurable).
    """
    g.stats["cochange_sampled_commits"] = commits
    g.stats["cochange_min_pairs"] = min_pairs
    if commits > MAX_COCHANGE_COMMITS:
        g.stats["cochange_history_capped"] = commits
        commits = MAX_COCHANGE_COMMITS
        g.stats["cochange_sampled_commits"] = commits
    try:
        # Popen, not run(capture_output=True): run() reads the child's stdout to
        # EOF before it returns, so a byte cap applied to its result bounds only
        # the decode and the pair counting -- the blob is already resident by
        # then (ISS-236). Streaming the pipe is what makes MAX_COCHANGE_BYTES a
        # memory bound rather than a post-hoc trim.
        #
        # -c core.quotepath=false: without it git backslash-escapes any
        # non-ASCII path ("caf\303\251.py"), which never matches file_index and
        # the CO_CHANGE edge silently vanishes. No text=True: decode the bytes
        # as UTF-8 ourselves, exactly as walker._git_files does, so a non-ASCII
        # path cannot raise UnicodeDecodeError under a cp1252 locale.
        # stdin=DEVNULL for the same reason as parse._git_files: without it git
        # inherits *our* stdin, and a git that blocks on the MCP server's
        # JSON-RPC pipe stalls until the timeout and can eat client frames.
        proc = subprocess.Popen(
            [
                "git",
                "-c",
                "core.quotepath=false",
                "-C",
                str(root),
                "log",
                f"-n{commits}",
                "--name-only",
                "--pretty=format:%H",
                "--no-merges",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return

    # The read runs in a thread so the timeout can be enforced on a blocking
    # read(): select() does not work on pipes on Windows, and this is how
    # Popen.communicate(timeout=...) does it there too.
    read: list[tuple[bytes, bool]] = []

    def _drain() -> None:
        try:
            read.append(_read_capped(proc.stdout, MAX_COCHANGE_BYTES))
        except (OSError, ValueError):
            pass  # pipe torn down mid-read: same outcome as no output at all

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()
    reader.join(COCHANGE_TIMEOUT)
    timed_out = reader.is_alive()
    try:
        _reap_child(proc, reader)
    except (OSError, subprocess.SubprocessError):
        pass
    # A timeout is silent, exactly as the TimeoutExpired from the old
    # run(timeout=120) was: no co-change edges, no error, the build goes on.
    if timed_out or not read:
        return
    stdout, capped = read[0]
    # Capping kills git mid-write, so its exit status then says "killed" and
    # means nothing. Only a read that reached EOF can report a real git failure.
    if not capped and proc.returncode not in (0, None):
        return
    # ISS-82: MAX_COCHANGE_COMMITS bounds how many commits are requested, not
    # how many bytes a single pathological commit's file list can still emit
    # within that count. Record that the cap bound -- same "cap and record when
    # we did" idiom as MAX_COCHANGE_COMMITS above. The value is the number of
    # bytes read, i.e. the cap itself: how large the output would have been is
    # exactly the thing we no longer pay to find out.
    if capped:
        g.stats["cochange_output_capped"] = len(stdout)
        # Drop the trailing partial commit: git log delimits commits with a blank
        # line ("\n\n" or "\r\n\r\n"). Stopping at an arbitrary byte count cuts into the oldest
        # commit block, and flushing whatever is in current at end-of-input can turn
        # a >25 file noise commit into a small (<25) co-change signal.
        m = None
        for m in re.finditer(rb"(\r?\n){2}", stdout):
            pass
        stdout = stdout[: m.end()] if m else b""
    pairs: Counter = Counter()
    current: list[str] = []
    # split("\n"), not splitlines(): with core.quotepath=false git emits paths
    # containing U+2028/U+2029/U+0085 raw, and splitlines() would cut such a path
    # in two so it never matches file_index (same bug class as ISS-22).
    for line in stdout.decode("utf8", "surrogateescape").split("\n") + [""]:
        line = line.rstrip("\r")
        if not line:
            if 1 < len(current) <= 25:
                for a, b in itertools.combinations(sorted(set(current)), 2):
                    pairs[(a, b)] += 1
            elif len(current) > 25:
                g.stats["cochange_commits_skipped"] += 1
            current = []
        elif line in file_index:
            current.append(line)
    for (a, b), n in pairs.items():
        if n >= min_pairs:
            g.add_edge(
                f"file:{a}",
                f"file:{b}",
                "CO_CHANGE",
                count=n,
                cochange_count=n,
                sampled_commits=commits,
                min_pairs=min_pairs,
            )
