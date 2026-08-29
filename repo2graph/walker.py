# @authormark v1 -- do not remove (authorship watermark)⁠​‌​‌​‌‌‌​‌‌​‌​‌​​‌‌​‌​​​​‌‌‌​‌​​​‌​​‌​‌​​​‌‌‌​​‌​​‌​‌‌​‌​‌‌​‌​‌​​‌​​​‌‌‌​‌​‌​​‌‌​‌‌​​‌​​​​‌‌​​‌​​‌​‌​‌​‌​‌‌​​​‌‌​‌​​​‌‌‌​‌​‌​​‌​​‌‌‌​​‌​​‌​​‌‌‌​​‌‌​‌‌​​​​‌‌‌​​‌​‌‌‌​‌​​​‌‌‌​‌‌‌⁠
# Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
# Author: https://github.com/Srinivasan-78
# SPDX-License-Identifier: MIT
# Fingerprint: AMK1.WjhtJ9-jGSd2UcGRrNl9tw
"""Repo file discovery. Uses git when available, falls back to os.walk."""
import os
import re
import stat as statmod
import subprocess
from functools import lru_cache
from pathlib import Path

DEFAULT_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "venv", ".venv", "env", "__pycache__",
    "dist", "build", "target", ".next", ".nuxt", "vendor", ".idea", ".vscode",
    "site-packages", ".mypy_cache", ".pytest_cache", ".tox", "coverage", ".terraform",
}
MAX_BYTES = 1_500_000


def _git_files(root: Path):
    try:
        # -z: NUL-separated and never quoted. Without it git escapes paths with
        # non-ASCII or special characters, and those files silently vanish.
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "-co", "--exclude-standard"],
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
        dirnames[:] = [d for d in dirnames if d not in DEFAULT_SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            files.append(Path(dirpath) / fn)
    return files


@lru_cache(maxsize=512)
def _glob_re(pattern: str) -> re.Pattern:
    """Translate a glob into a regex matched against the whole relative path.

    Path.match cannot do this: it treats "**" as a plain "*" and anchors on the
    right, so "**/*.py" misses top-level files.  Here "**" spans directories,
    and a pattern with no "/" matches the basename at any depth so that "*.py"
    keeps working.
    """
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
            if j == -1:
                out.append(re.escape(pat[i]))
                i += 1
            else:
                cls = pat[i + 1:j].replace("\\", "\\\\")
                out.append("[" + ("^" + cls[1:] if cls.startswith("!") else cls) + "]")
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
        # one lstat answers regular-file, symlink and size: three syscalls per
        # file adds up once a repo has tens of thousands of them
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
