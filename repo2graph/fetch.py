"""Fetch a GitHub repository and index it end to end."""

import base64
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any

CLONE_TIMEOUT = 900
GIT_TIMEOUT = 120

# Same hardening as integrity.GIT_HARDENING_ARGS, duplicated rather than
# imported: fetch.py clones arbitrary, not-yet-trusted repositories, so every
# git invocation here -- clone, fetch, checkout, rev-parse -- must disable
# fsmonitor/external-diff/hooks before the clone's own .git/config exists to
# read, not just afterward. core.hooksPath is pointed at the OS null device so
# a cloned repo's hooks directory, even if one somehow ends up at the target
# path, is never consulted.
GIT_HARDENING_ARGS = (
    "-c",
    "core.fsmonitor=false",
    "-c",
    "diff.external=",
    "-c",
    "core.hooksPath=" + os.devnull,
)


def _rmtree(path: Path) -> None:
    """Best-effort recursive delete of a temp clone.

    Git marks pack files under .git/objects read-only; on Windows os.unlink then
    raises PermissionError and shutil.rmtree(ignore_errors=True) would leave the
    whole clone (often hundreds of MB) behind. Clear the bit and retry.
    """

    def _on_error(func: Any, p: str, _exc: Any) -> None:
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass

    # onerror was renamed onexc in 3.12; the callback signature is compatible.
    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_on_error)
        else:
            shutil.rmtree(path, onerror=_on_error)
    except OSError:
        pass


def _github_spec_regex() -> re.Pattern[str]:
    server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    domains = ["github\\.com"]
    if server_url and server_url != "https://github.com":
        host = urllib.parse.urlparse(server_url).netloc
        if host:
            domains.append(re.escape(host))
    domain_pat = "|".join(domains)
    return re.compile(
        rf"^(?:(?:https?://)?(?:www\.)?(?:{domain_pat})/|git@(?:{domain_pat}):)?"
        r"(?P<owner>[\w.\-]+)/(?P<repo>[\w.\-]+?)(?:\.git)?/?$"
    )


GITHUB_SPEC = _github_spec_regex()


# A refname is one or more "/"-separated components. Each component must open
# with an alphanumeric or "_", which is what makes the pattern a security check
# and not just a spelling check: it forbids a leading "-" on the whole value (so
# git cannot read the ref as an option), a leading "." on any component (so the
# value can never be "." or a dotted traversal segment), and a leading/trailing
# "/" or an empty component. Inside a component "-", "." and "_" are ordinary
# refname characters ("v1.2.3", "feature/foo-bar", "release/1.0"), and the
# alphanumeric run alone already covers the 40-hex SHA people pass instead of a
# name. Everything git treats as magic — " ", "~", "^", ":", "?", "*", "[", "\",
# "@{", control bytes — is simply absent from the class, so no shell- or
# refspec-metacharacter can reach the argv.
# The anchor is \Z, not $: "$" also matches immediately before a trailing
# newline, so "main\n" would pass a "$"-anchored pattern and reach the argv.
GIT_REF = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*(?:/[A-Za-z0-9_][A-Za-z0-9._-]*)*\Z")


_git_version_cache: tuple[int, ...] | None = None


def _git_version() -> tuple[int, ...]:
    """Probe `git --version`, memoizing only a successful result.

    A transient failure (fd exhaustion, fork failure, ...) must not be cached
    forever: that would permanently mask the version-gated security check in
    _auth_env for the rest of the process's life. Only a successful probe is
    memoized; a failed probe is retried on the next call.
    """
    global _git_version_cache
    if _git_version_cache is not None:
        return _git_version_cache
    try:
        out = subprocess.run(
            ["git", "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf8",
            errors="replace",
            timeout=10,
        )
        if out.returncode == 0 and out.stdout:
            m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", out.stdout)
            if m:
                _git_version_cache = tuple(int(x) for x in m.groups() if x is not None)
                return _git_version_cache
    except (OSError, subprocess.SubprocessError):
        pass
    return (2, 40, 0)  # assume a conservative baseline when `git --version` won't answer


def parse_spec(spec: str) -> tuple[str, str]:
    """'owner/repo', a GitHub URL or an SSH remote -> (owner, repo)."""
    m = _github_spec_regex().match(spec.strip())
    if not m:
        raise ValueError(f"not a GitHub repo spec: {spec!r}")
    owner, repo = m.group("owner"), m.group("repo")
    # Reject path-traversal and option-like components: "owner/.." would make
    # the clone target dest/".." (the parent of the temp dir), and "-x/-y"
    # smuggles flags into the git argv.
    for part in (owner, repo):
        if not part or part in (".", "..") or part.startswith("-"):
            raise ValueError(f"not a GitHub repo spec: {spec!r}")
    return owner, repo


def parse_ref(ref: str) -> str:
    """Validate a refname that will be interpolated into a git argv."""
    # Same reasoning as parse_spec's component check, for the same reason: `ref`
    # reaches git as a bare argv element (`git fetch ... origin <ref>`, `git
    # checkout <ref>`), so a value beginning with "-" is read by git's parser as
    # an option rather than a refname — "--upload-pack=..." would make git run a
    # command of the caller's choosing. "." and ".." are rejected for the
    # traversal reason too: `git clone --branch` is safe from the option problem
    # but a dotted ref is never a real ref, and letting it through only defers
    # the failure. The allowlist is deliberately narrower than git's own rules
    # (see GIT_REF) — `git check-ref-format` would be authoritative but costs a
    # subprocess on every clone.
    if not GIT_REF.match(ref) or ".." in ref:
        raise ValueError(f"not a valid git ref: {ref!r}")
    return ref


def _redact(msg: str, token: str | None) -> str:
    """Strip the token, its base64 'basic' form, and URL-encoded form from user-facing text."""
    if not token:
        return msg
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    msg = msg.replace(token, "***").replace(basic, "***")
    quoted = urllib.parse.quote(token)
    if quoted != token:
        msg = msg.replace(quoted, "***")
    return msg


# Environment variables that redirect which repository/index git reads or
# writes, independent of `-C`/argv. An ambient `GIT_DIR`/`GIT_WORK_TREE` left
# set by a wrapper script or a prior command can silently point a clone or
# checkout invocation at a different repository than the one just created.
_GIT_ENV_REDIRECT_KEYS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY")


def _auth_env(token: str | None) -> dict[str, str]:
    """Environment carrying the clone credential out of band.

    The token must never be an argv element: it would be visible in
    `ps`/`/proc` to every other user. git reads http.extraheader from
    GIT_CONFIG_* for this one invocation only, so nothing lands on disk either.

    Also strips `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE` inherited from the
    ambient environment, so they cannot override the explicit `-C <target>`
    every call site here passes.
    """
    env = dict(os.environ)
    for key in _GIT_ENV_REDIRECT_KEYS:
        env.pop(key, None)
    # Prevent git from hanging on a terminal credential prompt
    env["GIT_TERMINAL_PROMPT"] = "0"
    if not token:
        return env

    # GIT_CONFIG_* requires git >= 2.31
    if _git_version() < (2, 31):
        raise RuntimeError("git >= 2.31 is required for token-authenticated clone")

    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    # Preserve any existing GIT_CONFIG_COUNT set by the caller
    try:
        count = int(env.get("GIT_CONFIG_COUNT", "0") or "0")
    except ValueError:
        count = 0
    server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    env[f"GIT_CONFIG_KEY_{count}"] = f"http.{server_url}/.extraheader"
    env[f"GIT_CONFIG_VALUE_{count}"] = f"AUTHORIZATION: basic {basic}"
    env["GIT_CONFIG_COUNT"] = str(count + 1)
    return env


def clone(
    spec: str, dest: Path, ref: str | None = None, depth: int = 0, token: str | None = None
) -> Path:
    """Clone a GitHub repo into dest/<repo>. depth=0 means full history."""
    owner, repo = parse_spec(spec)
    # Validate before anything else runs: ensure a hostile ref is refused
    # without git ever being spawned.
    if ref:
        parse_ref(ref)
    token = token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    url = f"{server_url}/{owner}/{repo}.git"
    target = Path(dest) / repo

    # Detect an existing checkout and reuse it
    if target.is_dir() and (target / ".git").exists():
        if ref:
            # Fetch ref before checkout in case existing checkout is shallow or missing ref
            fetch_ok = False
            try:
                # The "--" is belt-and-braces over parse_ref: git fetch treats
                # everything after it as a refspec, so even if GIT_REF is ever
                # loosened an option-shaped ref lands as `fatal: invalid refspec`
                # instead of being honoured as a flag. `git checkout` below gets
                # no "--" on purpose — there it means "what follows is a
                # pathspec", so `git checkout -- main` tries to restore a *file*
                # named main and never switches ref. Only parse_ref guards that
                # call.
                fetch_proc = subprocess.run(
                    [
                        "git",
                        *GIT_HARDENING_ARGS,
                        "-C",
                        str(target),
                        "fetch",
                        "--depth",
                        "1",
                        "origin",
                        "--",
                        ref,
                    ],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    encoding="utf8",
                    errors="replace",
                    timeout=GIT_TIMEOUT,
                    env=_auth_env(token),
                )
                fetch_ok = fetch_proc.returncode == 0
            except subprocess.TimeoutExpired:
                raise RuntimeError("git fetch timed out") from None
            except (OSError, subprocess.SubprocessError) as e:
                raise RuntimeError(f"git fetch failed: {e}") from None

            try:
                proc = subprocess.run(
                    ["git", *GIT_HARDENING_ARGS, "-C", str(target), "checkout", ref],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    encoding="utf8",
                    errors="replace",
                    timeout=GIT_TIMEOUT,
                    env=_auth_env(token),
                )
                if proc.returncode != 0 and fetch_ok:
                    # In a shallow clone, git fetch origin <ref> puts commit in FETCH_HEAD
                    # without creating a local branch or remote tracking ref. Try checking
                    # out FETCH_HEAD — but only when we know the fetch just succeeded,
                    # otherwise FETCH_HEAD may be stale from a prior run on a different ref.
                    proc_detach = subprocess.run(
                        [
                            "git",
                            *GIT_HARDENING_ARGS,
                            "-C",
                            str(target),
                            "checkout",
                            "--detach",
                            "FETCH_HEAD",
                        ],
                        stdin=subprocess.DEVNULL,
                        capture_output=True,
                        encoding="utf8",
                        errors="replace",
                        timeout=GIT_TIMEOUT,
                        env=_auth_env(token),
                    )
                    if proc_detach.returncode == 0:
                        proc = proc_detach
            except subprocess.TimeoutExpired:
                raise RuntimeError("git checkout timed out") from None
            except (OSError, subprocess.SubprocessError) as e:
                raise RuntimeError(f"git checkout failed: {e}") from None
            # A cached clone can be shallow or simply not carry `ref`; a silently
            # ignored failure here indexes whatever was already checked out (the
            # wrong commit) with no error, so surface it like the clone path does.
            if proc.returncode != 0:
                raise RuntimeError(
                    f"git checkout {ref!r} in existing clone failed: "
                    f"{_redact((proc.stderr or '').strip(), token)}"
                )
        return target
    if target.is_dir() and any(target.iterdir()):
        raise RuntimeError(f"destination directory '{target}' exists and is not an empty directory")
    if target.exists() and not target.is_dir():
        raise RuntimeError(f"destination path '{target}' exists and is not a directory")

    cmd = ["git", *GIT_HARDENING_ARGS, "clone", "--quiet"]
    if depth:
        cmd += ["--depth", str(depth)]
    if ref:
        cmd += ["--branch", ref]
    cmd += [url, str(target)]
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf8",
            errors="replace",
            timeout=CLONE_TIMEOUT,
            env=_auth_env(token),
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("git clone timed out") from None
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"git clone failed: {e}") from None
    if proc.returncode != 0:
        # Redact both the raw token and the base64 basic credential
        raise RuntimeError(f"git clone failed: {_redact((proc.stderr or '').strip(), token)}")
    return target


def head_sha(path: Path) -> str:
    try:
        out = subprocess.run(
            ["git", *GIT_HARDENING_ARGS, "-C", str(path), "rev-parse", "HEAD"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf8",
            errors="replace",
            timeout=GIT_TIMEOUT,
            env=_auth_env(None),
        )
    except (subprocess.TimeoutExpired, OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip()[:12] if out.returncode == 0 else "unknown"


def index_github(
    spec: str,
    outdir: Path,
    ref: str | None = None,
    depth: int = 0,
    git_history: int = 0,
    formats: str = "jsonl,graphml,cypher,overview,html",
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    max_files: int = 0,
    keep_clone: Path | None = None,
    token: str | None = None,
    viz_nodes: int = 300,
    jobs: int = 0,
    config: Any = None,
    max_call_candidates: int = 5,
    no_chunks: bool = False,
    cochange_min: int = 3,
    max_bytes: int = 0,
    max_edges: int = 0,
    max_chunks: int = 0,
    max_memory_mb: float = 0.0,
    max_build_seconds: float = 0.0,
    limit_policy: str | None = None,
) -> dict[str, Any]:
    """Clone a GitHub repo, build its graph, write artifacts to outdir."""
    from .chunks import iter_chunks
    from .export import atomic_write, dump_all, make_path
    from .graph import build

    owner, repo = parse_spec(spec)
    workdir = Path(keep_clone) if keep_clone else Path(tempfile.mkdtemp(prefix="r2g-"))
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        src = clone(spec, workdir, ref=ref, depth=depth, token=token)
        sha = head_sha(src)
        g = build(
            src,
            include=include,
            exclude=exclude,
            git_history=git_history,
            max_files=max_files,
            jobs=jobs,
            config=config,
            max_call_candidates=max_call_candidates,
            cochange_min=cochange_min,
            max_bytes=max_bytes,
            max_edges=max_edges,
            limit_policy=limit_policy,
            max_memory_mb=max_memory_mb,
            max_build_seconds=max_build_seconds,
        )
        g.name = f"{owner}/{repo}"
        # The clone is deleted below, so there is no local tree for
        # index-status/doctor to compare against: record the remote instead.
        setattr(g, "source_remote", f"github:{owner}/{repo}@{sha}")  # not a Graph field
        chunks = (
            None if no_chunks else iter_chunks(g, max_chunks=max_chunks)
        )  # a generator, streamed to disk by dump_all
        outdir = Path(outdir)
        # Same cleaning as cli.parse_formats: tolerate "jsonl, html" (spaces,
        # empty items) so a format the caller asked for is not silently dropped.
        fmts = {f.strip() for f in formats.split(",") if f.strip()}
        written, n_chunks = dump_all(g, chunks, outdir, fmts, viz_nodes)
        meta = {
            "repo": f"{owner}/{repo}",
            "ref": ref or "default",
            "commit": sha,
            "nodes": len(g.nodes),
            "edges": len(g.edges),
            "chunks": n_chunks,
            "stats": dict(g.stats),
            "written": written,
            "out": str(outdir),
        }
        with atomic_write(
            make_path(outdir, "index.json"), "w", encoding="utf8", newline="\n"
        ) as fh:
            fh.write(json.dumps(meta, indent=2))
        return meta
    finally:
        if keep_clone is None:
            _rmtree(workdir)
