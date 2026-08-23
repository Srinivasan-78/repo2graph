"""Fetch a GitHub repository and index it end to end."""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

GITHUB_SPEC = re.compile(
    r"^(?:(?:https?://)?(?:www\.)?github\.com/|git@github\.com:)?"
    r"(?P<owner>[\w.\-]+)/(?P<repo>[\w.\-]+?)(?:\.git)?/?$"
)


def parse_spec(spec: str) -> tuple[str, str]:
    """'owner/repo', a GitHub URL or an SSH remote -> (owner, repo)."""
    m = GITHUB_SPEC.match(spec.strip())
    if not m:
        raise ValueError(f"not a GitHub repo spec: {spec!r}")
    return m.group("owner"), m.group("repo")


def clone(spec: str, dest: Path, ref: str | None = None, depth: int = 0,
          token: str | None = None) -> Path:
    """Clone a GitHub repo into dest/<repo>. depth=0 means full history."""
    owner, repo = parse_spec(spec)
    token = token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    auth = f"x-access-token:{token}@" if token else ""
    url = f"https://{auth}github.com/{owner}/{repo}.git"
    target = Path(dest) / repo
    cmd = ["git", "clone", "--quiet"]
    if depth:
        cmd += ["--depth", str(depth)]
    if ref:
        cmd += ["--branch", ref]
    cmd += [url, str(target)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        msg = proc.stderr.strip().replace(token or "\0", "***")
        raise RuntimeError(f"git clone failed: {msg}")
    if token:
        # git records the clone URL, credential and all, in .git/config; with
        # --keep-clone that would leave the token sitting on disk.
        subprocess.run(
            ["git", "-C", str(target), "remote", "set-url", "origin",
             f"https://github.com/{owner}/{repo}.git"],
            capture_output=True, text=True)
    return target


def head_sha(path: Path) -> str:
    out = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                         capture_output=True, text=True)
    return out.stdout.strip()[:12] if out.returncode == 0 else "unknown"


def index_github(spec: str, outdir: Path, ref: str | None = None, depth: int = 0,
                 git_history: int = 0,
                 formats: str = "jsonl,graphml,cypher,overview,html",
                 include=None, exclude=None, max_files: int = 0,
                 keep_clone: Path | None = None, token: str | None = None,
                 viz_nodes: int = 300) -> dict:
    """Clone a GitHub repo, build its graph, write artifacts to outdir."""
    from .chunks import build_chunks
    from .export import dump_all
    from .graph import build

    owner, repo = parse_spec(spec)
    workdir = Path(keep_clone) if keep_clone else Path(tempfile.mkdtemp(prefix="r2g-"))
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        src = clone(spec, workdir, ref=ref, depth=depth, token=token)
        sha = head_sha(src)
        g = build(src, include=include, exclude=exclude,
                  git_history=git_history, max_files=max_files)
        g.name = f"{owner}/{repo}"
        chunks = build_chunks(g)
        outdir = Path(outdir)
        written = dump_all(g, chunks, outdir, set(formats.split(",")), viz_nodes)
        meta = {"repo": f"{owner}/{repo}", "ref": ref or "default", "commit": sha,
                "nodes": len(g.nodes), "edges": len(g.edges), "chunks": len(chunks),
                "stats": dict(g.stats), "written": written, "out": str(outdir)}
        (outdir / "index.json").write_text(json.dumps(meta, indent=2), encoding="utf8")
        return meta
    finally:
        if keep_clone is None:
            shutil.rmtree(workdir, ignore_errors=True)
