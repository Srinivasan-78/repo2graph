"""`repo2graph index-status`: what is in this index, and does it still hold?

The index is three JSONL files, a manifest and a stats blob, and until now
answering "is this current, and what did it skip" meant opening all of them.
This module joins them into one report and adds the two facts none of them
records: how much disk the index occupies, and whether the working tree has
moved since the build.

Freshness lives here rather than in `doctor.py` because both commands need
the same answer and two implementations of "is this stale" is exactly the
kind of pair that drifts until they disagree in front of a user.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Re-hashing the whole tree is not free, and `index-status` is meant to be
# cheap enough to put in a prompt. Past this many recorded files the
# file-level comparison is skipped and the report says so -- the commit
# comparison still applies and costs one `git rev-parse`.
MAX_FRESHNESS_FILES = 50_000

# How many changed paths to name before summarising. The count is always
# exact; this bounds only the listing.
MAX_LISTED_PATHS = 10


@dataclass
class Freshness:
    """Whether an index still describes the tree it was built from.

    `status` is one of:
      "current"  -- every signal checked agrees the index is up to date
      "stale"    -- at least one signal says it is not
      "unknown"  -- nothing could be checked (no state file, no git, bound hit)
    """

    status: str = "unknown"
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    indexed_commit: str | None = None
    head_commit: str | None = None
    commit_moved: bool = False
    built_dirty: bool = False
    files_checked: int = 0
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)

    @property
    def is_current(self) -> bool:
        return self.status == "current"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reasons": self.reasons,
            "notes": self.notes,
            "indexed_commit": self.indexed_commit,
            "head_commit": self.head_commit,
            "commit_moved": self.commit_moved,
            "built_dirty": self.built_dirty,
            "files_checked": self.files_checked,
            "added": self.added[:MAX_LISTED_PATHS],
            "removed": self.removed[:MAX_LISTED_PATHS],
            "modified": self.modified[:MAX_LISTED_PATHS],
            "counts": {
                "added": len(self.added),
                "removed": len(self.removed),
                "modified": len(self.modified),
            },
        }


def _git_head(repo: Path) -> str | None:
    """The tree's current HEAD sha, or None when `repo` is not a git checkout."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    # Never text=True on git output: a cp1252 console decodes a non-ASCII
    # path with UnicodeDecodeError, sometimes inside the handler. See AGENTS.md.
    return proc.stdout.decode("utf8", "surrogateescape").strip() or None


def compute_freshness(repo: Path, idx_dir: Path, agent_dir: Path) -> Freshness:
    """Three independent signals that the index no longer matches the tree.

    Cheapest first, and each one degrades to a note rather than an error:

    1. The commit recorded in `manifest.json` against the tree's `HEAD`. One
       subprocess, exact for committed state, silent on a non-git tree.
    2. The file set discovery finds now against the one `index.state.json`
       recorded -- a set difference over paths, no file reads.
    3. A real sha256 for each file whose mtime is newer than `manifest.json`.
       The mtime *only chooses what to hash*: a file touched but not changed
       hashes equal and is reported unchanged, so a fresh clone (which
       rewrites every mtime) does not read as stale. The converse -- a file
       edited with its mtime preserved -- is missed, which is why signal 1
       exists and why `--incremental` re-hashes everything rather than
       trusting this.
    """
    fresh = Freshness()

    manifest_file = agent_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf8", errors="replace"))
    except Exception:
        manifest = {}
    revision = manifest.get("source_revision") or {}
    fresh.indexed_commit = str(revision.get("commit") or "") or None
    fresh.built_dirty = bool(revision.get("dirty"))

    # --- Signal 1: HEAD ---
    fresh.head_commit = _git_head(repo)
    if fresh.indexed_commit and fresh.head_commit:
        if fresh.indexed_commit != fresh.head_commit:
            fresh.commit_moved = True
            fresh.reasons.append("HEAD moved since the build")
    elif not fresh.head_commit:
        fresh.notes.append("not a git repository (commit comparison skipped)")
    if fresh.built_dirty:
        fresh.notes.append("the tree had uncommitted changes when the index was built")

    # --- Signals 2 and 3 need index.state.json ---
    state_file = agent_dir / "index.state.json"
    if not state_file.exists():
        fresh.notes.append(
            "no index.state.json (built by an older version); file-level check skipped"
        )
        fresh.status = "stale" if fresh.reasons else "unknown"
        return fresh

    try:
        state = json.loads(state_file.read_text(encoding="utf8", errors="replace"))
        recorded: dict[str, str] = dict(state.get("files") or {})
    except Exception as exc:
        fresh.notes.append(f"index.state.json is unreadable ({exc}); file-level check skipped")
        fresh.reasons.append("change-detection state is unreadable")
        fresh.status = "stale"
        return fresh

    if len(recorded) > MAX_FRESHNESS_FILES:
        fresh.notes.append(
            f"{len(recorded)} indexed files exceeds the {MAX_FRESHNESS_FILES} scan bound; "
            "file-level check skipped"
        )
        fresh.status = "stale" if fresh.reasons else "unknown"
        return fresh

    # Re-discover with the filters the *build* used, not the defaults. A build
    # with any `--exclude` or `--exclude-group` otherwise re-discovers every
    # file it deliberately left out, reports them all as newly added, and so
    # reads as permanently stale the instant it finishes.
    filters = state.get("filters") if isinstance(state.get("filters"), dict) else None
    if filters is None:
        fresh.notes.append(
            "index.state.json records no discovery filters (built by an older version); "
            "comparing against defaults, so any --exclude may show as added files"
        )
    try:
        from .parse import BuildConfig, discover

        filter_dict = filters or {}
        max_bytes = int(filter_dict.get("max_file_bytes") or 0)
        if max_bytes > 0:
            config = BuildConfig(
                max_file_bytes=max_bytes,
                include_vendor=bool(filter_dict.get("include_vendor")),
                include_secrets=bool(filter_dict.get("include_secrets")),
                extra_exclude_dirs=list(filter_dict.get("extra_exclude_dirs") or []),
                extra_secret_keywords=list(filter_dict.get("extra_secret_keywords") or []),
                extra_secret_dirs=list(filter_dict.get("extra_secret_dirs") or []),
            )
        else:
            config = BuildConfig(
                include_vendor=bool(filter_dict.get("include_vendor")),
                include_secrets=bool(filter_dict.get("include_secrets")),
                extra_exclude_dirs=list(filter_dict.get("extra_exclude_dirs") or []),
                extra_secret_keywords=list(filter_dict.get("extra_secret_keywords") or []),
                extra_secret_dirs=list(filter_dict.get("extra_secret_dirs") or []),
            )
        current = {
            rel
            for rel, _abs in discover(
                repo,
                include_globs=(filters or {}).get("include") or None,
                exclude_globs=(filters or {}).get("exclude") or None,
                config=config,
            )
        }
    except Exception as exc:
        fresh.notes.append(f"could not re-discover the source tree ({exc}); file check skipped")
        fresh.status = "stale" if fresh.reasons else "unknown"
        return fresh

    # Drop the index's own artifacts. `.r2g` is not in DEFAULT_SKIP_DIRS, and
    # it did not exist when the build ran discovery -- so on a non-git tree
    # (where `git ls-files` is not filtering it out via .gitignore) every
    # artifact the build just wrote comes back as a newly "added" source file,
    # and a brand-new index reports as stale.
    try:
        idx_prefix = idx_dir.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        idx_prefix = ""
    if idx_prefix:
        current = {r for r in current if r != idx_prefix and not r.startswith(idx_prefix + "/")}

    fresh.files_checked = len(recorded)
    fresh.added = sorted(current - set(recorded))
    fresh.removed = sorted(set(recorded) - current)

    try:
        cutoff = manifest_file.stat().st_mtime
    except OSError:
        cutoff = 0.0

    modified: list[str] = []
    for rel in sorted(set(recorded) & current):
        abspath = repo / rel
        try:
            if abspath.stat().st_mtime <= cutoff:
                continue
            digest = hashlib.sha256(abspath.read_bytes()).hexdigest()
        except OSError:
            continue
        if digest != recorded[rel]:
            modified.append(rel)
    fresh.modified = modified

    for label, items in (
        ("added", fresh.added),
        ("removed", fresh.removed),
        ("modified", fresh.modified),
    ):
        if items:
            fresh.reasons.append(f"{len(items)} {label}")

    fresh.status = "stale" if fresh.reasons else "current"
    return fresh


def _dir_size(path: Path) -> tuple[int, int]:
    """(total bytes, file count) under `path`. Symlinks are not followed."""
    total = 0
    count = 0
    for item in path.rglob("*"):
        try:
            if item.is_symlink() or not item.is_file():
                continue
            total += item.stat().st_size
        except OSError:
            continue
        count += 1
    return total, count


def human_bytes(n: int) -> str:
    """'1.4 MB'. Decimal units, because that is what `ls -lh --si` and every
    cloud storage bill use, and this number is most often compared to one."""
    step = 1000.0
    value = float(n)
    for unit in ("B", "kB", "MB", "GB"):
        if abs(value) < step or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= step
    return f"{value:.1f} GB"


# stats.json key -> label, same categories export._SKIP_STAT_LABELS uses for
# the human overview. Duplicated as a local table only because the order here
# is "most likely to surprise you first" rather than the overview's order.
_SKIP_LABELS: tuple[tuple[str, str], ...] = (
    ("skipped_gitignore", "ignored by .gitignore"),
    ("skipped_vendor", "vendor/build directories"),
    ("skipped_dotfile", "dot-directories"),
    ("skipped_binary", "binary"),
    ("skipped_too_large", "over the size ceiling"),
    ("skipped_secret", "secret/credential paths"),
)


def index_status(out: Path | str, repo: Path | str | None = None) -> dict[str, Any]:
    """The full report. Raises FileNotFoundError when there is no index.

    Args:
        out: The `-o` output directory (or the `agent/` directory itself).
        repo: The source tree. Defaults to the index directory's parent,
            which is right for the conventional `<repo>/.r2g` layout.

    Returns:
        A JSON-serialisable dict. `repo2graph index-status --json` prints it
        verbatim, so every key here is public.
    """
    out = Path(out)
    agent = out / "agent" if (out / "agent").is_dir() else out
    manifest_file = agent / "manifest.json"
    if not manifest_file.exists():
        raise FileNotFoundError(
            f"no repo2graph index at {out}: run `repo2graph build <repo> -o {out}` first"
        )

    manifest = json.loads(manifest_file.read_text(encoding="utf8", errors="replace"))
    try:
        stats = json.loads((agent / "stats.json").read_text(encoding="utf8", errors="replace"))
    except Exception:
        stats = {}

    # `out` is normally the -o directory, with artifacts under `agent/`. It may
    # also *be* the agent directory: `doctor` documents pointing at either, and
    # a caller who already resolved the path should not get a different answer.
    # Resolve the index root once, then derive everything from it -- the source
    # tree is the index root's parent in the conventional `<repo>/.r2g` layout,
    # which is one level up from `out` in the first case and two in the second.
    index_root = out.parent if (agent == out and out.name == "agent") else out
    repo_path = Path(repo) if repo is not None else index_root.parent

    revision = manifest.get("source_revision") or {}
    size_bytes, artifact_count = _dir_size(index_root)
    fresh = compute_freshness(repo_path, index_root, agent)

    skipped = {key: int(stats.get(key) or 0) for key, _label in _SKIP_LABELS if stats.get(key)}

    return {
        "index": {
            "path": str(index_root),
            "build_id": manifest.get("build_id"),
            "tool_version": manifest.get("tool_version") or manifest.get("version"),
            "schema_version": stats.get("index_schema_version"),
            "created_at": manifest.get("created_at"),
            "age_seconds": _age_seconds(manifest.get("created_at")),
            "size_bytes": size_bytes,
            "size_human": human_bytes(size_bytes),
            "artifact_files": artifact_count,
            "has_vectors": bool(stats.get("has_vectors")),
        },
        "source": {
            "repo": manifest.get("repo"),
            "path": str(repo_path),
            "commit": revision.get("commit"),
            "short_commit": revision.get("short_commit"),
            "branch": revision.get("branch"),
            "base_branch": revision.get("base_branch"),
            "merge_base": revision.get("merge_base"),
            "commits_ahead_of_base": revision.get("commits_ahead_of_base"),
            "tag": revision.get("tag"),
            "dirty": bool(revision.get("dirty")),
            "dirty_files": revision.get("dirty_files"),
            "remote_url": revision.get("remote_url"),
        },
        "contents": {
            "files_discovered": int(stats.get("files") or 0),
            "files_parsed": int(stats.get("parsed") or 0),
            "nodes": int(stats.get("nodes") or 0),
            "edges": int(stats.get("edges") or 0),
            "symbols": int(stats.get("symbol:function") or 0) + int(stats.get("symbol:class") or 0),
            "symbols_by_kind": {
                key.split(":", 1)[1]: int(value)
                for key, value in sorted(stats.items())
                if key.startswith("symbol:") and isinstance(value, int)
            },
            "edges_by_type": {
                key.split(":", 1)[1]: int(value)
                for key, value in sorted(stats.items())
                if key.startswith("edge:") and isinstance(value, int)
            },
            "languages": dict(stats.get("languages") or {}),
        },
        "discovery": {
            "mode": stats.get("discovery"),
            "skipped": skipped,
            "skipped_total": sum(skipped.values()),
        },
        "parsing": {
            "parse_errors": int(stats.get("parse_errors") or 0),
            "files_with_parse_errors": int(stats.get("files_with_parse_errors") or 0),
            "cpp_fallback_files": int(stats.get("cpp_fallback_files") or 0),
        },
        "freshness": fresh.to_dict(),
    }


def _age_seconds(created_at: Any) -> int | None:
    """Seconds since `created_at`, or None when it is absent or unparseable."""
    if not isinstance(created_at, str) or not created_at:
        return None
    try:
        when = datetime.fromisoformat(created_at)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - when).total_seconds()))


def format_age(seconds: int | None) -> str:
    if seconds is None:
        return "unknown"
    if seconds < 90:
        return f"{seconds}s ago"
    if seconds < 5400:
        return f"{seconds // 60}m ago"
    if seconds < 172800:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def format_status(report: dict[str, Any]) -> str:
    """The human rendering. `--json` prints `index_status()` instead."""
    idx = report["index"]
    src = report["source"]
    con = report["contents"]
    dis = report["discovery"]
    par = report["parsing"]
    fresh = report["freshness"]

    mark = {"current": "[CURRENT]", "stale": "[STALE]", "unknown": "[UNKNOWN]"}[fresh["status"]]
    lines = [
        f"repo2graph index-status  {mark}",
        "=" * 62,
        "",
        "Source",
    ]
    if src["commit"]:
        head = f"  commit         {src['short_commit'] or src['commit'][:10]}"
        if src["tag"]:
            head += f"  tag={src['tag']}"
        lines.append(head)
        branch = f"  branch         {src['branch'] or '(detached)'}"
        if src["base_branch"]:
            branch += f"  base={src['base_branch']}"
            if src["commits_ahead_of_base"] is not None:
                branch += f" (+{src['commits_ahead_of_base']} commits)"
        lines.append(branch)
        dirty = "yes" if src["dirty"] else "no"
        if src["dirty"] and src["dirty_files"]:
            dirty = f"yes ({src['dirty_files']} file(s) uncommitted at build time)"
        lines.append(f"  dirty at build {dirty}")
    else:
        lines.append("  commit         (not a git repository)")
    lines.append(f"  path           {src['path']}")

    lines += [
        "",
        "Index",
        f"  built          {format_age(idx['age_seconds'])}  ({idx['created_at'] or 'unknown'})",
        f"  size           {idx['size_human']} across {idx['artifact_files']} artifact file(s)",
        f"  tool version   {idx['tool_version'] or 'unknown'}",
        f"  dense vectors  {'yes' if idx['has_vectors'] else 'no (BM25 only)'}",
        "",
        "Contents",
        f"  files          {con['files_parsed']} parsed of {con['files_discovered']} discovered",
        f"  symbols        {con['symbols']}"
        + (
            "  (" + ", ".join(f"{k}={v}" for k, v in con["symbols_by_kind"].items()) + ")"
            if con["symbols_by_kind"]
            else ""
        ),
        f"  nodes / edges  {con['nodes']} / {con['edges']}",
    ]
    if con["edges_by_type"]:
        by_type = ", ".join(f"{k}={v}" for k, v in con["edges_by_type"].items())
        lines.append(f"  edge types     {by_type}")
    if con["languages"]:
        langs = ", ".join(f"{k}={v}" for k, v in con["languages"].items())
        lines.append(f"  languages      {langs}")

    lines += ["", "Discovery"]
    lines.append(
        f"  mode           {dis['mode']}"
        + ("  (git ls-files)" if dis["mode"] == "git" else "  (os.walk; .gitignore not consulted)")
    )
    if dis["skipped"]:
        lines.append(f"  skipped        {dis['skipped_total']} file(s)")
        for key, label in _SKIP_LABELS:
            if dis["skipped"].get(key):
                lines.append(f"                   {dis['skipped'][key]:>6}  {label}")
    else:
        lines.append("  skipped        nothing")

    lines += ["", "Parsing"]
    if par["files_with_parse_errors"]:
        lines.append(
            f"  parse errors   {par['parse_errors']} error(s) in "
            f"{par['files_with_parse_errors']} file(s)"
        )
    else:
        lines.append("  parse errors   none")
    if par["cpp_fallback_files"]:
        lines.append(f"  cpp fallback   {par['cpp_fallback_files']} file(s)")

    lines += ["", "Freshness"]
    if fresh["status"] == "current":
        lines.append(f"  up to date     {fresh['files_checked']} indexed file(s) match the tree")
    else:
        for reason in fresh["reasons"]:
            lines.append(f"  out of date    {reason}")
        for label in ("added", "removed", "modified"):
            shown = fresh[label]
            total = fresh["counts"][label]
            if shown:
                more = f" ... and {total - len(shown)} more" if total > len(shown) else ""
                lines.append(f"    {label}: " + ", ".join(shown) + more)
    for note in fresh["notes"]:
        lines.append(f"  note           {note}")

    if fresh["status"] == "stale":
        lines += [
            "",
            "Refresh it (only re-parses what changed):",
            f"  repo2graph build {src['path']} -o {idx['path']} --incremental",
        ]
    return "\n".join(lines)
