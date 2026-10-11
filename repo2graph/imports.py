"""Import resolution: an import statement's text to the in-repo file it names (#315)."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .parse import JAVA_IMPORT_RE, PY_FROM_IMPORT_RE, PY_IMPORT_RE, RUBY_REQUIRE_RE

_IMPORT_RE = {
    # `from` branch splits module (group 1) from the imported-names list (group
    # 2): a dots-only module ("from . import X") has no name of its own, so
    # import_targets() below appends each imported name to the dots instead of
    # discarding it (#160). The bare `import a, b` form is group 3, unchanged.
    "python": re.compile(
        rf"^(?:{PY_FROM_IMPORT_RE.pattern.lstrip('^')}|{PY_IMPORT_RE.pattern.lstrip('^')})"
    ),
    "js": re.compile(r"""['"]([^'"]+)['"]"""),
    "go": re.compile(r"""['"]([^'"]+)['"]"""),
    "rust": re.compile(r"use\s+([\w:]+)"),
    "java": JAVA_IMPORT_RE,
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
    "ruby": RUBY_REQUIRE_RE,
    # Bash `source ./lib.sh` / `. ./lib.sh`
    "bash": re.compile(r"""(?:source|\.)\s+['"]?([^'"\s]+)['"]?"""),
    # Terraform `source = "./modules/vpc"` inside a `module` block
    "hcl": re.compile(r'source\s*=\s*"([^"]+)"'),
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
        "objc": "c",
    }.get(lang, lang)
    rx = _IMPORT_RE.get(key)
    if rx is None:
        return []
    targets = [m.group(1) for m in rx.finditer(raw)][:4]
    if lang == "rust":
        targets = [t.rstrip(":") for t in targets]
    return targets


def path_index(file_index: Iterable[str]) -> dict[str, Any]:
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


def _go_dir_by_tail(ctx: dict[str, Any]) -> dict[str, list[str]]:
    """Last directory name -> the first .go file under it, directories in sorted order.

    Built once per `ctx` rather than once per import: without go.mod, every Go
    import used to sort and walk all directories (#456 P2).
    """
    table = ctx.get("go_dir_by_tail")
    if table is None:
        table = {}
        for d, paths in sorted(ctx["by_dir"].items()):
            first = next((p for p in paths if p.endswith(".go")), None)
            if first is not None:
                table.setdefault(d.split("/")[-1], [first])
        ctx["go_dir_by_tail"] = table
    return table


def resolve_import(
    target: str, from_path: str, lang: str, file_index: set[str], ctx: dict[str, Any] | None = None
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
            cands += [(src_dir / c).as_posix() for c in list(cands)]
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
                parent_cands = [f"{parent}.py", f"{parent}/__init__.py"]
                cands += parent_cands
                cands += [(src_dir / c).as_posix() for c in parent_cands]
                cands += [f"src/{c}" for c in parent_cands]
                parent_tail = parent.split("/")[-1]
                cands += [p for p in by_name.get(f"{parent_tail}.py", []) if "/" in p][:1]
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
                for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".d.ts", ".vue", ".svelte"):
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
            cands = _go_dir_by_tail(ctx).get(target.split("/")[-1], [])[:1]
    elif lang in ("c", "cpp", "objc"):
        cands = by_name.get(target.split("/")[-1], [])[:1]
    elif lang == "hcl":
        # A local module is a directory: its `main.tf`, else its first `.tf`.
        if target.startswith("."):
            base = Path(src_dir, target).as_posix()
            base = re.sub(r"/\./", "/", base)
            while "/../" in base:
                base = re.sub(r"[^/]+/\.\./", "", base, count=1)
            base = base.removeprefix("./").rstrip("/")
            cands = [f"{base}/main.tf"]
            cands += [p for p in by_dir.get(base, []) if p.endswith(".tf")][:1]
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


def repo_context(root: Path) -> dict[str, Any]:
    """Repo-level facts used to resolve imports (Go module path, Rust crate name)."""
    ctx: dict[str, Any] = {}
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
