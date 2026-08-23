"""Build the repository graph: nodes + edges."""
import itertools
import os
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

from .langs import CONFIG_EXT, DOC_EXT, EXT_LANG
from .parse import parse_source
from .walker import discover

MAX_CALL_CANDIDATES = 5
# Under this many files a process pool costs more to start than it saves.
PARALLEL_MIN_FILES = 64


class Graph:
    def __init__(self, root: Path, name: str):
        self.root, self.name = root, name
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []
        self._edge_seen: set[tuple] = set()
        self.stats: Counter = Counter()

    def add_node(self, nid: str, **attrs):
        if nid in self.nodes:
            self.nodes[nid].update({k: v for k, v in attrs.items() if v not in (None, "", [])})
        else:
            self.nodes[nid] = dict(id=nid, **attrs)
        return nid

    def add_edge(self, src: str, dst: str, etype: str, **attrs):
        key = (src, dst, etype)
        if key in self._edge_seen:
            return
        self._edge_seen.add(key)
        self.edges.append(dict(src=src, dst=dst, type=etype, **attrs))
        self.stats[f"edge:{etype}"] += 1


# ---------- import parsing ----------
_IMPORT_RE = {
    "python": re.compile(r"^(?:from\s+([\w\.]+)\s+import|import\s+([\w\.,\s]+))"),
    "js": re.compile(r"""['"]([^'"]+)['"]"""),
    "go": re.compile(r"""['"]([^'"]+)['"]"""),
    "rust": re.compile(r"use\s+([\w:]+)"),
    "java": re.compile(r"import\s+(?:static\s+)?([\w\.\*]+)"),
    "c": re.compile(r"""[<"]([^>"]+)[>"]"""),
}


def import_targets(raw: str, lang: str) -> list[str]:
    if lang == "python":
        m = _IMPORT_RE["python"].match(raw.strip())
        if not m:
            return []
        if m.group(1):
            return [m.group(1)]
        return [p.strip().split(" as ")[0].strip() for p in m.group(2).split(",") if p.strip()]
    key = {"javascript": "js", "typescript": "js", "tsx": "js", "php": "js", "kotlin": "rust",
           "swift": "java", "scala": "java", "csharp": "java", "cpp": "c"}.get(lang, lang)
    rx = _IMPORT_RE.get(key)
    if rx is None:
        return []
    return [m.group(1) for m in rx.finditer(raw)][:4]


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


def resolve_import(target: str, from_path: str, lang: str, file_index: set[str],
                   ctx: dict | None = None) -> str | None:
    """Map an import target to an in-repo file path when possible."""
    ctx = ctx or {}
    if "by_name" not in ctx:
        ctx = dict(ctx, **path_index(file_index))
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
        module = (ctx or {}).get("go_module")
        if module and (target == module or target.startswith(module + "/")):
            pkg_dir = target[len(module):].strip("/")
            cands = [p for p in by_dir.get(pkg_dir, [])
                     if p.endswith(".go") and not p.endswith("_test.go")][:1]
        elif module:
            cands = []  # module path known: anything outside it is a third-party package
        else:
            tail = target.split("/")[-1]
            cands = [p for d, paths in sorted(by_dir.items()) if d.split("/")[-1] == tail
                     for p in paths if p.endswith(".go")][:1]
    elif lang in ("c", "cpp"):
        cands = by_name.get(target.split("/")[-1], [])[:1]
    elif lang == "java":
        rel = target.replace(".", "/") + ".java"
        cands = [rel]
        cands += [p for p in by_name.get(rel.split("/")[-1], []) if p.endswith(rel)][:1]
    for c in cands:
        c = Path(c).as_posix().removeprefix("./")
        if c in file_index:
            return c
    return None


def repo_context(root: Path) -> dict:
    """Repo-level facts used to resolve imports (currently the Go module path)."""
    ctx: dict = {}
    gomod = root / "go.mod"
    if gomod.exists():
        for line in gomod.read_text("utf8", "replace").splitlines():
            if line.startswith("module "):
                ctx["go_module"] = line.split(None, 1)[1].strip()
                break
    return ctx


# ---------- parsing ----------
def _read_and_parse(item):
    """Read one file and parse it if it is code.

    Top level, and returns only counts plus the ParsedFile, so a process pool
    can pickle both the call and its result.
    """
    rel, abspath, lang = item
    try:
        raw = abspath.read_bytes()
    except OSError:
        return rel, lang, None
    return rel, lang, (len(raw), raw.count(b"\n") + 1,
                       parse_source(raw, lang) if lang else None)


def resolve_jobs(jobs: int) -> int:
    """0 means one worker per core, capped so the parent keeps up with results."""
    if jobs > 0:
        return jobs
    return max(1, min(os.cpu_count() or 1, 8))


def parse_all(files, jobs: int):
    """Read and parse every file, in discovery order, across `jobs` processes.

    tree-sitter parsing is CPU bound and dominates a large build, so this is
    the difference between one core and all of them. Order is preserved, which
    keeps node ids and edge order identical to a serial run.
    """
    items = [(rel, abspath, EXT_LANG.get(abspath.suffix.lower()))
             for rel, abspath in files]
    if jobs == 1 or len(items) < PARALLEL_MIN_FILES:
        return [_read_and_parse(i) for i in items]
    from concurrent.futures import ProcessPoolExecutor
    try:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            return list(pool.map(_read_and_parse, items,
                                 chunksize=max(1, len(items) // (jobs * 8))))
    except (OSError, ValueError):  # no fork, or no POSIX semaphores to build on
        return [_read_and_parse(i) for i in items]


# ---------- build ----------
def build(root: Path, include=None, exclude=None, git_history: int = 0,
          max_files: int = 0, jobs: int = 0) -> Graph:
    root = Path(root).resolve()
    g = Graph(root, root.name)
    repo_id = f"repo:{root.name}"
    g.add_node(repo_id, type="repo", name=root.name, path=".")

    files = list(discover(root, include, exclude))
    if max_files:
        files = files[:max_files]
    file_index = {rel for rel, _ in files}
    ctx = repo_context(root)
    ctx.update(path_index(file_index))
    parsed: dict[str, object] = {}  # ParsedFile only: keeping raw bytes here
                                    # would hold the whole repo in memory

    for rel, lang, read in parse_all(files, resolve_jobs(jobs)):
        if read is None:  # unreadable file
            continue
        size, lines, pf = read
        ext = Path(rel).suffix.lower()
        ftype = "code" if lang else ("doc" if ext in DOC_EXT else
                                     "config" if ext in CONFIG_EXT else "other")
        fid = f"file:{rel}"
        g.add_node(fid, type="file", name=Path(rel).name, path=rel, lang=lang or ext.lstrip("."),
                   file_type=ftype, size=size, lines=lines)
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

        for sym in pf.symbols:
            sid = f"sym:{rel}::{sym.qualname}"
            g.add_node(sid, type="symbol", name=sym.name, qualname=sym.qualname, kind=sym.kind,
                       path=rel, lang=lang, start_line=sym.start_line, end_line=sym.end_line,
                       signature=sym.signature, docstring=sym.docstring)
            g.stats[f"symbol:{sym.kind}"] += 1
            owner = f"sym:{rel}::{sym.parent}" if sym.parent else fid
            g.add_edge(owner, sid, "DEFINES")

        for raw_imp in pf.imports:
            for target in import_targets(raw_imp, lang):
                resolved = resolve_import(target, rel, lang, file_index, ctx)
                if resolved:
                    g.add_edge(fid, f"file:{resolved}", "IMPORTS", target=target, internal=True)
                else:
                    mid = f"module:{target}"
                    g.add_node(mid, type="module", name=target, external=True)
                    g.add_edge(fid, mid, "IMPORTS", target=target, internal=False)

    # ----- name index for call/inheritance resolution -----
    by_name: dict[str, list[str]] = defaultdict(list)
    for nid, n in g.nodes.items():
        if n["type"] == "symbol":
            by_name[n["name"]].append(nid)

    for rel, pf in parsed.items():
        for sym in pf.symbols:
            sid = f"sym:{rel}::{sym.qualname}"
            for callee, count in Counter(sym.calls).items():
                cands = by_name.get(callee, [])
                local = [c for c in cands if c.startswith(f"sym:{rel}::")]
                pick = local or cands
                if not pick:
                    eid = f"external:{callee}"
                    g.add_node(eid, type="external", name=callee)
                    g.add_edge(sid, eid, "CALLS_EXTERNAL", count=count)
                elif len(pick) == 1:
                    g.add_edge(sid, pick[0], "CALLS", count=count, confidence=1.0)
                elif len(pick) <= MAX_CALL_CANDIDATES:
                    for c in pick:
                        g.add_edge(sid, c, "CALLS", count=count,
                                   confidence=round(1 / len(pick), 3))
                else:
                    g.stats["ambiguous_calls"] += 1
            for base in sym.bases:
                base = base.split("[")[0].split("<")[0].split(".")[-1].strip()
                for c in by_name.get(base, [])[:MAX_CALL_CANDIDATES]:
                    g.add_edge(sid, c, "INHERITS")

    if git_history:
        add_cochange(g, root, git_history, file_index)
    mark_entrypoints(g)
    g.stats["nodes"] = len(g.nodes)
    g.stats["edges"] = len(g.edges)
    return g


ENTRY_KINDS = ("function", "method")
SCORED_ENTRYPOINTS = 200   # exact reach is a BFS each, so only rank the busiest


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
            called.add(e["dst"])
            out[e["src"]].append(e["dst"])
    nested = {e["dst"] for e in g.edges if e["type"] == "DEFINES"
              and g.nodes.get(e["src"], {}).get("kind") in ENTRY_KINDS}
    roots = [nid for nid, n in g.nodes.items()
             if n["type"] == "symbol" and n.get("kind") in ENTRY_KINDS
             and nid not in called and nid not in nested]
    for nid in roots:
        g.nodes[nid]["entrypoint"] = True
    roots.sort(key=lambda nid: -len(out[nid]))
    for nid in roots[:SCORED_ENTRYPOINTS]:
        g.nodes[nid]["reach"] = _reach(nid, out)
    g.stats["entrypoints"] = len(roots)


def _reach(start: str, out: dict) -> int:
    """How many distinct symbols `start` reaches through CALLS edges."""
    seen, stack = {start}, [start]
    while stack:
        for dst in out[stack.pop()]:
            if dst not in seen:
                seen.add(dst)
                stack.append(dst)
    return len(seen) - 1


def add_cochange(g: Graph, root: Path, commits: int, file_index: set[str], min_pairs: int = 3):
    """CO_CHANGE edges from files edited together in the last N commits."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", f"-n{commits}", "--name-only",
             "--pretty=format:%H", "--no-merges"],
            capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            return
    except (OSError, subprocess.SubprocessError):
        return
    pairs: Counter = Counter()
    current: list[str] = []
    for line in out.stdout.splitlines() + [""]:
        line = line.strip()
        if not line:
            if 1 < len(current) <= 25:
                for a, b in itertools.combinations(sorted(set(current)), 2):
                    pairs[(a, b)] += 1
            current = []
        elif line in file_index:
            current.append(line)
    for (a, b), n in pairs.items():
        if n >= min_pairs:
            g.add_edge(f"file:{a}", f"file:{b}", "CO_CHANGE", count=n)
