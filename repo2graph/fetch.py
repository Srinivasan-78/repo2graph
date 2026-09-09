# @authormark v1 -- do not remove (authorship watermark)⁠​‌‌​‌‌​​​‌​​‌‌‌‌​​‌‌​​‌​​‌​​​‌‌​​‌‌‌‌​​‌​‌​​‌‌​‌​‌‌​‌​‌‌​‌​​​​​‌​‌‌‌​​​‌​‌‌‌​​‌‌​‌​‌​​​​​​‌‌‌​​‌​‌​​‌‌‌‌​​‌‌​‌​​​‌​​​‌‌‌​‌​‌‌​​​​‌‌‌​‌​​​‌‌‌‌​​​​‌‌‌‌​​‌​​‌‌​​‌​​‌‌‌​‌​‌​‌‌‌‌​​‌⁠
# Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
# Author: https://github.com/Srinivasan-78
# SPDX-License-Identifier: MIT
# Fingerprint: AMK1.lO2FyMkAqsP9O4GXtxy2uy
"""Fetch a GitHub repository and index it end to end."""
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

CLONE_TIMEOUT = 900
GIT_TIMEOUT = 120

GITHUB_SPEC = re.compile(
    r"^(?:(?:https?://)?(?:www\.)?github\.com/|git@github\.com:)?"
    r"(?P<owner>[\w.\-]+)/(?P<repo>[\w.\-]+?)(?:\.git)?/?$"
)


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


def _auth_env(token: str | None) -> dict | None:
    """Environment carrying the clone credential out of band.

    The token must never be an argv element (ISS-16): it would be visible in
    `ps`/`/proc` to every other user. git reads http.extraheader from
    GIT_CONFIG_* for this one invocation only, so nothing lands on disk either.
    """
    if not token:
        return None
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
    }


def clone(spec: str, dest: Path, ref: str | None = None, depth: int = 0,
          token: str | None = None) -> Path:
    """Clone a GitHub repo into dest/<repo>. depth=0 means full history."""
    owner, repo = parse_spec(spec)
    token = token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    url = f"https://github.com/{owner}/{repo}.git"
    target = Path(dest) / repo
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
    if proc.returncode != 0:
        msg = (proc.stderr or "").strip()
        if token:
            msg = msg.replace(token, "***")
        raise RuntimeError(f"git clone failed: {msg}")
    return target


def head_sha(path: Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                             capture_output=True, encoding="utf8",
                             errors="replace", timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "unknown"
    return out.stdout.strip()[:12] if out.returncode == 0 else "unknown"


def index_github(spec: str, outdir: Path, ref: str | None = None, depth: int = 0,
                 git_history: int = 0,
                 formats: str = "jsonl,graphml,cypher,overview,html",
                 include=None, exclude=None, max_files: int = 0,
                 keep_clone: Path | None = None, token: str | None = None,
                 viz_nodes: int = 300, jobs: int = 0) -> dict:
    """Clone a GitHub repo, build its graph, write artifacts to outdir."""
    from .chunks import build_chunks
    from .export import dump_all
    from .graph import build
    from .layout import make_path

    owner, repo = parse_spec(spec)
    workdir = Path(keep_clone) if keep_clone else Path(tempfile.mkdtemp(prefix="r2g-"))
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        src = clone(spec, workdir, ref=ref, depth=depth, token=token)
        sha = head_sha(src)
        g = build(src, include=include, exclude=exclude,
                  git_history=git_history, max_files=max_files, jobs=jobs)
        g.name = f"{owner}/{repo}"
        chunks = build_chunks(g)
        outdir = Path(outdir)
        written = dump_all(g, chunks, outdir, set(formats.split(",")), viz_nodes)
        meta = {"repo": f"{owner}/{repo}", "ref": ref or "default", "commit": sha,
                "nodes": len(g.nodes), "edges": len(g.edges), "chunks": len(chunks),
                "stats": dict(g.stats), "written": written, "out": str(outdir)}
        make_path(outdir, "index.json").write_text(json.dumps(meta, indent=2),
                                                   encoding="utf8")
        return meta
    finally:
        if keep_clone is None:
            shutil.rmtree(workdir, ignore_errors=True)
