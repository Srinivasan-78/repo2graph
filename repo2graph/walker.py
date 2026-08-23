"""Repo file discovery. Uses git when available, falls back to os.walk."""
import os
import subprocess
from pathlib import Path

DEFAULT_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "venv", ".venv", "env", "__pycache__",
    "dist", "build", "target", ".next", ".nuxt", "vendor", ".idea", ".vscode",
    "site-packages", ".mypy_cache", ".pytest_cache", ".tox", "coverage", ".terraform",
}
MAX_BYTES = 1_500_000


def _git_files(root: Path):
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard"],
            capture_output=True, text=True, timeout=60,
        )
        if out.returncode != 0:
            return None
        return [root / p for p in out.stdout.splitlines() if p]
    except (OSError, subprocess.SubprocessError):
        return None


def _walk_files(root: Path):
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in DEFAULT_SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            files.append(Path(dirpath) / fn)
    return files


def is_binary(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return b"\0" in fh.read(4096)
    except OSError:
        return True


def discover(root: Path, include_globs=None, exclude_globs=None):
    """Yield (relative_path, absolute_path) for candidate source files."""
    root = root.resolve()
    files = _git_files(root)
    if files is None:
        files = _walk_files(root)
    for abspath in files:
        try:
            rel = abspath.relative_to(root)
        except ValueError:
            continue
        if any(part in DEFAULT_SKIP_DIRS for part in rel.parts):
            continue
        if not abspath.is_file() or abspath.is_symlink():
            continue
        rp = rel.as_posix()
        if include_globs and not any(rel.match(g) for g in include_globs):
            continue
        if exclude_globs and any(rel.match(g) for g in exclude_globs):
            continue
        try:
            if abspath.stat().st_size > MAX_BYTES:
                continue
        except OSError:
            continue
        if is_binary(abspath):
            continue
        yield rp, abspath
