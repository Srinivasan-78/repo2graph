"""`repo2graph build --watch`: rebuild incrementally when the tree changes (#392).

A polling watcher, standard library only. Each poll lists the tree through
`discovery.candidate_files` -- the same enumeration a build uses, so the
watched set is the indexed set by construction -- and compares names, sizes
and modification times. A change starts a quiet period; further changes
restart it; the rebuild runs once the tree has been still for that long. An
editor save, a `git checkout` or a formatter run touches many files at once
and costs one rebuild, not one per file.

The rebuild is `build --incremental`, so it holds the same build lock and
writes through the same atomic directory swap: interrupting it leaves the
previous index in place and no lock behind.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from pathlib import Path

Snapshot = dict[str, tuple[int, int]]

#: Seconds between polls, and of stillness before a rebuild, by default.
DEFAULT_INTERVAL = 1.0
DEFAULT_QUIET = 1.0
#: Files per second of poll interval: polling is a stat per file, so a large
#: tree is polled less often rather than kept busy (100,000 files -> 2 s).
FILES_PER_INTERVAL_SECOND = 50_000


def snapshot(root: Path, skip_dirs: Iterable[str], out_dir: Path | None = None) -> Snapshot:
    """{relative path: (mtime_ns, size)} for every file a build could look at.

    The candidate set is discovery's, not a second approximation of it (#483):
    this used to walk the tree itself and skip every dotfile, which meant it
    missed `.pre-commit-config.yaml` and the other dotfiles a build *does*
    index -- an edit to one never triggered a rebuild -- while following the
    `.gitignore`d directories a build never sees, so on this repository alone
    it watched 747 files to the build's 386, 305 of them in `.hypothesis`,
    which every `pytest` run rewrites.

    The path-level exclusions discovery applies are repeated here. The
    content-level ones (binary, over-size, secret path) deliberately are not:
    they only ever narrow the set, so including such a file costs one needless
    rebuild, where leaving out a file discovery does index costs a rebuild that
    never happens -- and repeating them would mean a read per file per poll.
    """
    from .discovery import candidate_files
    from .parse import _SWAP_DIR_RE

    root = root.resolve()
    skip = set(skip_dirs)
    # The output directory as a path prefix, computed once, the way
    # `discovery._output_rel_prefix` does it. Comparing resolved parents
    # instead would cost a `resolve()` syscall per file per poll, and
    # `relative_to` on a resolved path would also drop a symlink pointing out
    # of the tree -- which `discover` indexes, because it does not resolve.
    out_parts: tuple[str, ...] | None = None
    if out_dir is not None:
        try:
            out_parts = out_dir.resolve().relative_to(root).parts
        except ValueError:
            out_parts = None  # an outdir outside the tree excludes nothing in it
    found: Snapshot = {}
    for p in candidate_files(root, skip):
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        parts = rel.parts
        if any(part in skip for part in parts[:-1]):
            continue
        if out_parts is not None and parts[: len(out_parts)] == out_parts:
            continue
        # BuildLock's sibling lock file, and dump_all's staging and backup
        # directories -- the three things under the root that a build writes
        # and never indexes.
        if rel.name.startswith(".") and rel.name.endswith(".r2glock"):
            continue
        if any(_SWAP_DIR_RE.match(part) for part in parts[:-1]):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        found[rel.as_posix()] = (st.st_mtime_ns, st.st_size)
    return found


def poll_interval(n_files: int, requested: float = DEFAULT_INTERVAL) -> float:
    return max(requested, n_files / FILES_PER_INTERVAL_SECOND)


def watch(
    take_snapshot: Callable[[], Snapshot],
    rebuild: Callable[[], object],
    *,
    interval: float = DEFAULT_INTERVAL,
    quiet: float = DEFAULT_QUIET,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], object] = time.sleep,
    max_rebuilds: int | None = None,
) -> int:
    """Poll until interrupted (or `max_rebuilds` rebuilds); return how many ran."""
    last = take_snapshot()
    changed_at: float | None = None
    rebuilds = 0
    while max_rebuilds is None or rebuilds < max_rebuilds:
        sleep(interval)
        current = take_snapshot()
        if current != last:
            last, changed_at = current, clock()
            continue
        if changed_at is not None and clock() - changed_at >= quiet:
            changed_at = None
            rebuild()
            rebuilds += 1
            last = take_snapshot()
    return rebuilds
