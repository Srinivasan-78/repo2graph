"""Index lifecycle for MCP tools: open, cache, lock, and auto-build.

One `Index` instance is reused per output directory and reopened when the
index on disk is rewritten underneath it.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

from ..cache import ResultCache
from ..export import path as artifact_path
from ..query import Index
from .guardrails import AUTO_BUILD_FORMATS


_INDEXES: dict[str, Index] = {}
_INDEX_MTIMES: dict[str, float] = {}
_INDEX_LOCKS_GUARD = threading.Lock()
_INDEX_LOCKS: dict[str, threading.Lock] = {}


def _index_lock(key: str) -> threading.Lock:
    """The lock covering one index directory, created on first use."""
    with _INDEX_LOCKS_GUARD:
        lock = _INDEX_LOCKS.get(key)
        if lock is None:
            lock = _INDEX_LOCKS[key] = threading.Lock()
        return lock


def _index_mtime(out_path: Path) -> float:
    manifest = artifact_path(out_path, "manifest.json")
    target = manifest if manifest.is_file() else artifact_path(out_path, "chunks.jsonl")
    try:
        return target.stat().st_mtime if target.is_file() else 0.0
    except OSError:
        return 0.0


def _has_index(out_path: Path) -> bool:
    """True when `out_path` holds an index a tool can actually read."""
    return out_path.exists() and artifact_path(out_path, "chunks.jsonl").is_file()


#: An organisation's own secret rules (`repo2graph-mcp --secret-keyword/--secret-dir`).
#: Process-wide, set once by `server.main` before the first tool call: one
#: server serves one repository under one policy.
SECRET_RULES: dict[str, list[str]] = {"keywords": [], "dirs": []}

#: Written into an index the MCP server built on its own initiative.
AUTO_BUILD_GITIGNORE = (
    "# written by repo2graph-mcp: an auto-built index holds full source text\n*\n"
)


def _build_index(repo: Path, out: Path) -> None:
    """Index `repo` into `out`, in-process, silent on stdout."""
    from ..chunks import iter_chunks
    from ..export import dump_all
    from ..graph import build
    from ..export import _LOCAL_GITIGNORE, atomic_write
    from ..integrity import validate_outdir
    from ..lock import BuildLock
    from ..config import build_options, load as load_repo_config
    from ..parse import BuildConfig

    # `build -o` is hardened by `validate_outdir`; this auto-build reached the
    # same `dump_all` without it, and that swap renames the target aside and
    # deletes it. The server picks `out` itself (`<repo>/.r2g`) or takes it from
    # `--out`, so a misconfigured client could have it replace a real directory.
    # Raised as ValueError rather than SystemExit: this runs inside a live stdio
    # server, which must answer the tool call rather than exit the process.
    validate_outdir(out, repo_root=repo)
    # The repo's own config file (#391), as `repo2graph build` there would read
    # it. A ConfigError is a ValueError, answered like the one above. The
    # server's --secret-keyword/--secret-dir are its CLI flags: when given they
    # replace the file's lists, as on the command line.
    config_kw, build_kw = build_options(load_repo_config(repo).values)
    if SECRET_RULES["keywords"]:
        config_kw["extra_secret_keywords"] = list(SECRET_RULES["keywords"])
    if SECRET_RULES["dirs"]:
        config_kw["extra_secret_dirs"] = list(SECRET_RULES["dirs"])
    # The same lock `repo2graph build` takes: without it two servers (or a
    # server and a CLI build) pointed at one index interleave their swaps.
    with BuildLock(out):
        graph = build(repo, config=BuildConfig(output_dir=str(out), **config_kw), **build_kw)
        dump_all(graph, iter_chunks(graph), out, AUTO_BUILD_FORMATS)
    # Nobody asked for this index, so nobody decided to commit it: chunks.jsonl
    # carries full source text, and `git add -A` would publish it. An explicit
    # `repo2graph build` keeps the narrower ignore file, because committing an
    # index on purpose is a supported workflow (the Action's commit-branch).
    ignore = out / ".gitignore"
    if not ignore.exists() or ignore.read_text(encoding="utf8") == _LOCAL_GITIGNORE:
        with atomic_write(ignore, "w", encoding="utf8", newline="\n") as fh:
            fh.write(AUTO_BUILD_GITIGNORE)


def open_index(
    out: str | Path,
    repo: str | Path | None = None,
    cache: ResultCache | None = None,
    *,
    allow_foreign_index: bool = False,
) -> Index:
    """Open or build an index for `out`, reusing instances across calls."""
    out_path = Path(out)
    key = str(out_path.resolve())

    with _index_lock(key):
        if _has_index(out_path) and not allow_foreign_index:
            from ..integrity import is_foreign_index

            if is_foreign_index(out_path):
                raise ValueError(
                    f"Refusing to serve foreign index at '{out_path}'. "
                    "The index was not built on this machine. "
                    "Rebuild it with 'repo2graph build' or pass --allow-foreign-index."
                )

        current_mtime = _index_mtime(out_path)

        index = _INDEXES.get(key)
        if index is not None and key in _INDEX_MTIMES and current_mtime <= _INDEX_MTIMES[key]:
            return index

        if index is not None and _has_index(out_path):
            if cache is not None:
                cache.clear()
            index = _INDEXES[key] = Index(out_path)
            _INDEX_MTIMES[key] = current_mtime
            if repo is not None:
                index.repo_root = Path(repo).resolve()
            return index

        if index is not None:
            return index

        if not _has_index(out_path):
            if repo is None:
                raise SystemExit(
                    f"error: no repo2graph index found at '{out}'. "
                    f"Build one first with: repo2graph build <path> -o {out}"
                )
            build_fn = getattr(sys.modules.get("repo2graph.mcp"), "_build_index", _build_index)
            build_fn(Path(repo), out_path)
            if cache is not None:
                cache.clear()
            current_mtime = _index_mtime(out_path)
        index = _INDEXES[key] = Index(out_path)
        _INDEX_MTIMES[key] = current_mtime
        if repo is not None:
            index.repo_root = Path(repo).resolve()
        return index


def open_index_or_task(
    out: str | Path,
    repo: str | Path | None = None,
    cache: ResultCache | None = None,
    tasks: Any = None,
    *,
    allow_foreign_index: bool = False,
) -> tuple[Index | None, str | None]:
    """Open index or launch a background build task if asynchronous builds are enabled."""
    from ..tasks import BUILDING, BUILDING_MESSAGE, FAILED, FAILED_MESSAGE

    out_path = Path(out)
    if tasks is None or _has_index(out_path) or repo is None:
        return open_index(out, repo, cache, allow_foreign_index=allow_foreign_index), None

    task = tasks.for_dir(out_path)
    if task is None:
        task = tasks.start(repo, out_path)

    if task.status == BUILDING:
        return None, BUILDING_MESSAGE.format(**task.snapshot())
    if task.status == FAILED:
        return None, FAILED_MESSAGE.format(error=task.error)
    if cache is not None:
        cache.clear()
    return open_index(out, repo, cache, allow_foreign_index=allow_foreign_index), None
