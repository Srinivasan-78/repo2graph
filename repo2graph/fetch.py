# @authormark v1 -- do not remove (authorship watermark)⁠​‌‌‌​‌​​​‌‌​‌‌​​​‌‌​​‌​​​‌​​‌​​‌​‌​‌​​‌​​‌​​‌​‌‌​​‌‌​‌‌‌​‌‌‌​‌‌​​‌‌​‌‌​​​‌‌​‌​​‌​‌‌​‌‌​​​‌​​‌‌‌​​‌‌​‌​‌​​‌​​​​‌‌​‌‌‌​‌​‌​‌​‌​‌‌‌​‌​​‌‌​‌​‌‌‌‌​​‌​​‌‌​‌​‌​‌‌​‌​​​​‌​​​‌​‌​‌​‌​‌​‌⁠
# Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
# Author: https://github.com/Srinivasan-78
# SPDX-License-Identifier: MIT
# Fingerprint: AMK1.tldIRK7vlilNjCuWMy5hEU
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
from functools import lru_cache
from pathlib import Path

CLONE_TIMEOUT = 900
GIT_TIMEOUT = 120


def _rmtree(path: Path) -> None:
    """Best-effort recursive delete of a temp clone.

    Git marks pack files under .git/objects read-only; on Windows os.unlink then
    raises PermissionError and shutil.rmtree(ignore_errors=True) would leave the
    whole clone (often hundreds of MB) behind. Clear the bit and retry.
    """
    def _on_error(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass

    # onerror was renamed onexc in 3.12; the callback signature is compatible.
    key = "onexc" if sys.version_info >= (3, 12) else "onerror"
    try:
        shutil.rmtree(path, **{key: _on_error})
    except OSError:
        pass

GITHUB_SPEC = re.compile(
    r"^(?:(?:https?://)?(?:www\.)?github\.com/|git@github\.com:)?"
    r"(?P<owner>[\w.\-]+)/(?P<repo>[\w.\-]+?)(?:\.git)?/?$"
)


@lru_cache(maxsize=1)
def _git_version() -> tuple[int, ...]:
    try:
        out = subprocess.run(["git", "--version"], capture_output=True,
                             encoding="utf8", errors="replace", timeout=10)
        if out.returncode == 0 and out.stdout:
            m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", out.stdout)
            if m:
                return tuple(int(x) for x in m.groups() if x is not None)
    except (OSError, subprocess.SubprocessError):
        pass
    return (2, 40, 0)  # assume a conservative baseline when `git --version` won't answer


def parse_spec(spec: str) -> tuple[str, str]:
    """'owner/repo', a GitHub URL or an SSH remote -> (owner, repo)."""
    m = GITHUB_SPEC.match(spec.strip())
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


def _redact(msg: str, token: str | None) -> str:
    """Strip the token, its base64 'basic' form, and URL-encoded form from user-facing text (SH-3)."""
    if not token:
        return msg
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    msg = msg.replace(token, "***").replace(basic, "***")
    quoted = urllib.parse.quote(token)
    if quoted != token:
        msg = msg.replace(quoted, "***")
    return msg


def _auth_env(token: str | None) -> dict:
    """Environment carrying the clone credential out of band.

    The token must never be an argv element (ISS-16): it would be visible in
    `ps`/`/proc` to every other user. git reads http.extraheader from
    GIT_CONFIG_* for this one invocation only, so nothing lands on disk either.
    """
    env = dict(os.environ)
    # NC-4: Prevent git from hanging on a terminal credential prompt
    env["GIT_TERMINAL_PROMPT"] = "0"
    if not token:
        return env

    # SH-2: GIT_CONFIG_* requires git >= 2.31
    if _git_version() < (2, 31):
        raise RuntimeError("git >= 2.31 is required for token-authenticated clone")

    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    # NC-5: preserve any existing GIT_CONFIG_COUNT set by the caller
    try:
        count = int(env.get("GIT_CONFIG_COUNT", "0") or "0")
    except ValueError:
        count = 0
    env[f"GIT_CONFIG_KEY_{count}"] = "http.https://github.com/.extraheader"
    env[f"GIT_CONFIG_VALUE_{count}"] = f"AUTHORIZATION: basic {basic}"
    env["GIT_CONFIG_COUNT"] = str(count + 1)
    return env


def clone(spec: str, dest: Path, ref: str | None = None, depth: int = 0,
          token: str | None = None) -> Path:
    """Clone a GitHub repo into dest/<repo>. depth=0 means full history."""
    owner, repo = parse_spec(spec)
    token = token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    url = f"https://github.com/{owner}/{repo}.git"
    target = Path(dest) / repo

    # ISS-21: detect an existing checkout and reuse it
    if target.is_dir() and (target / ".git").exists():
        if ref:
            try:
                proc = subprocess.run(["git", "-C", str(target), "checkout", ref],
                                      capture_output=True, encoding="utf8", errors="replace",
                                      timeout=GIT_TIMEOUT, env=_auth_env(token))
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
                    f"{_redact((proc.stderr or '').strip(), token)}")
        return target
    if target.is_dir() and any(target.iterdir()):
        raise RuntimeError(f"destination directory '{target}' exists and is not an empty directory")
    if target.exists() and not target.is_dir():
        raise RuntimeError(f"destination path '{target}' exists and is not a directory")

    cmd = ["git", "clone", "--quiet"]
    if depth:
        cmd += ["--depth", str(depth)]
    if ref:
        cmd += ["--branch", ref]
    cmd += [url, str(target)]
    try:
        proc = subprocess.run(cmd, capture_output=True, encoding="utf8",
                              errors="replace", timeout=CLONE_TIMEOUT,
                              env=_auth_env(token))
    except subprocess.TimeoutExpired:
        raise RuntimeError("git clone timed out") from None
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"git clone failed: {e}") from None
    if proc.returncode != 0:
        # SH-3: redact both the raw token and the base64 basic credential
        raise RuntimeError(f"git clone failed: {_redact((proc.stderr or '').strip(), token)}")
    return target


def head_sha(path: Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                             capture_output=True, encoding="utf8",
                             errors="replace", timeout=GIT_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip()[:12] if out.returncode == 0 else "unknown"


def index_github(spec: str, outdir: Path, ref: str | None = None, depth: int = 0,
                 git_history: int = 0,
                 formats: str = "jsonl,graphml,cypher,overview,html",
                 include=None, exclude=None, max_files: int = 0,
                 keep_clone: Path | None = None, token: str | None = None,
                 viz_nodes: int = 300, jobs: int = 0) -> dict:
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
        g = build(src, include=include, exclude=exclude,
                  git_history=git_history, max_files=max_files, jobs=jobs)
        g.name = f"{owner}/{repo}"
        chunks = iter_chunks(g)   # a generator, streamed to disk by dump_all
        outdir = Path(outdir)
        # Same cleaning as cli.parse_formats: tolerate "jsonl, html" (spaces,
        # empty items) so a format the caller asked for is not silently dropped.
        fmts = {f.strip() for f in formats.split(",") if f.strip()}
        written, n_chunks = dump_all(g, chunks, outdir, fmts, viz_nodes)
        meta = {"repo": f"{owner}/{repo}", "ref": ref or "default", "commit": sha,
                "nodes": len(g.nodes), "edges": len(g.edges), "chunks": n_chunks,
                "stats": dict(g.stats), "written": written, "out": str(outdir)}
        with atomic_write(make_path(outdir, "index.json"), "w",
                          encoding="utf8", newline="\n") as fh:
            fh.write(json.dumps(meta, indent=2))
        return meta
    finally:
        if keep_clone is None:
            _rmtree(workdir)
