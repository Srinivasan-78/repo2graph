"""Which files a build indexes: git or directory walk, exclusions, and `explain-path` (#315)."""

from __future__ import annotations

import os
import re
import stat as statmod
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any, cast

from .events import diagnostic
from .integrity import GIT_HARDENING_ARGS, _clean_git_env
from .parse import (
    _SWAP_DIR_RE,
    DEFAULT_SKIP_DIRS,
    EXT_LANG,
    INDEX_MARKER_FORMAT,
    BuildConfig,
)
from .security import has_unsafe_path_chars

if TYPE_CHECKING:
    from .config import RepoConfig


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


def _git_files(root: Path) -> list[Path] | None:
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


def _walk_files(root: Path, skip_dirs: Iterable[str] | None = None) -> list[Path]:
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
def _glob_re(pattern: str) -> re.Pattern[str]:
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


def matches_any(rel: str, patterns: Iterable[str]) -> bool:
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


def candidate_files(root: Path, skip_dirs: Iterable[str]) -> list[Path]:
    """Every path `discover` will consider, before it reads any file's content.

    The two sources `discover` uses, in its precedence: `git ls-files -co
    --exclude-standard` where git answers, a `skip_dirs`-pruned walk where it
    does not. `skip_dirs` only reaches the walk, because the git listing
    honours `.gitignore` instead and `discover` prunes it afterwards -- which
    is why a caller of this must prune too.

    Exposed for `watch.snapshot` (#483), which has to watch the set a build
    indexes and no other: its own walk honoured neither `.gitignore` nor the
    dotfiles a build does index, so it both missed rebuilds and ran them for
    files that could never enter an index.
    """
    files = _git_files(root)
    return files if files is not None else _walk_files(root, skip_dirs=skip_dirs)


def discover(
    root: Path,
    include_globs: Iterable[str] | None = None,
    exclude_globs: Iterable[str] | None = None,
    stats: dict[str, Any] | None = None,
    config: BuildConfig | None = None,
) -> Iterator[tuple[str, Path]]:
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


def explain_path(
    root: Path | str,
    target: Path | str,
    config: BuildConfig | None = None,
    include_globs: Iterable[str] | None = None,
    exclude_globs: Iterable[str] | None = None,
    repo_config: "RepoConfig | None" = None,
) -> dict[str, Any]:
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

    # Still step 1: the two rules `discover()` applies to the relative path the
    # moment it has one, before any of the checks below. Modelled here because
    # an explainer that does not know a drop rule answers INCLUDED for a file
    # no build will ever index -- the same divergence `_explain_index_dir`
    # exists to patch up for the output directory. Sharing step 1 with
    # `not_found` rather than renumbering: both are properties of the path
    # itself, `rule` tells them apart, and docs/cli.md already documents two
    # rules sharing a step.
    try:
        rel_str.encode("utf8")
    except UnicodeEncodeError:
        return {
            "path": str(target_path),
            "relative_path": rel_str,
            "included": False,
            "rule": "undecodable_path",
            "reason": (
                "Path is not valid UTF-8, so no JSONL, overview or HTML writer downstream "
                "can encode it"
            ),
            "precedence_step": 1,
        }
    if has_unsafe_path_chars(rel_str):
        return {
            "path": str(target_path),
            "relative_path": rel_str,
            "included": False,
            "rule": "unsafe_path",
            "reason": (
                "Path holds a control, line-separator or hidden character, which forges "
                "structure wherever the path is printed"
            ),
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
