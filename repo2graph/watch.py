"""`repo2graph build --watch`: rebuild incrementally when the tree changes (#392).

A polling watcher, standard library only. Each poll lists the tree the way
discovery does (same skipped directories, the output directory and lock files
left out) and compares names, sizes and modification times. A change starts a
quiet period; further changes restart it; the rebuild runs once the tree has
been still for that long. An editor save, a `git checkout` or a formatter run
touches many files at once and costs one rebuild, not one per file.

The rebuild is `build --incremental`, so it holds the same build lock and
writes through the same atomic directory swap: interrupting it leaves the
previous index in place and no lock behind.
"""

from __future__ import annotations

import os
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
    """{relative path: (mtime_ns, size)} for every file a build could look at."""
    root = root.resolve()
    skip = set(skip_dirs)
    out = out_dir.resolve() if out_dir is not None else None
    found: Snapshot = {}
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames if d not in skip and (out is None or here / d != out)
        )
        for name in filenames:
            # Dotfiles (lock files, editor swap files) and `~` backups are never
            # indexed, so they never trigger a rebuild either.
            if name.startswith(".") or name.endswith("~"):
                continue
            p = here / name
            try:
                st = p.stat()
            except OSError:
                continue
            found[p.relative_to(root).as_posix()] = (st.st_mtime_ns, st.st_size)
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
