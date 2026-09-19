#!/usr/bin/env python3
"""Create a GitHub-Verified commit via the REST API and point a branch at it.

`git commit` on an Actions runner is never marked Verified by GitHub, even when
pushed with an authenticated bot/App token -- signature verification only
happens for commits GitHub itself creates through the Contents/Git-Data API.
main requires signed commits, so publish.yml's release-bump commit must be
created this way, or every release PR is permanently blocked from merging
(the auto-merge poll in publish.yml times out at 60 minutes and fails the run).

stdlib-only: this runs in CI before any project dependency is guaranteed
installed, same reasoning as embed.py's numpy-optional .npy reader.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API = "https://api.github.com"


def api(
    method: str,
    path: str,
    token: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{API}{path}",
        method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result: dict[str, Any] = json.load(resp)
            return result
    except urllib.error.HTTPError as exc:
        sys.stderr.write(f"{method} {path} -> {exc.code}: {exc.read().decode()}\n")
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--branch", required=True, help="branch to create or force-update")
    parser.add_argument("--base", required=True, help="base branch to fork the commit from")
    parser.add_argument("--message", required=True)
    parser.add_argument(
        "--files", nargs="+", required=True, help="paths to include, read from disk as-is"
    )
    args = parser.parse_args()

    token = os.environ["GH_TOKEN"]
    repo = args.repo

    base_ref = api("GET", f"/repos/{repo}/git/ref/heads/{args.base}", token)
    base_sha = base_ref["object"]["sha"]
    base_commit = api("GET", f"/repos/{repo}/git/commits/{base_sha}", token)
    base_tree_sha = base_commit["tree"]["sha"]

    tree_entries = []
    for path in args.files:
        with open(path, "rb") as fh:
            content = fh.read()
        blob = api(
            "POST",
            f"/repos/{repo}/git/blobs",
            token,
            {"content": base64.b64encode(content).decode("ascii"), "encoding": "base64"},
        )
        tree_entries.append(
            {"path": Path(path).as_posix(), "mode": "100644", "type": "blob", "sha": blob["sha"]}
        )

    tree = api(
        "POST",
        f"/repos/{repo}/git/trees",
        token,
        {"base_tree": base_tree_sha, "tree": tree_entries},
    )

    commit = api(
        "POST",
        f"/repos/{repo}/git/commits",
        token,
        {"message": args.message, "tree": tree["sha"], "parents": [base_sha]},
    )
    commit_sha = commit["sha"]

    try:
        api("GET", f"/repos/{repo}/git/ref/heads/{args.branch}", token)
        branch_exists = True
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        branch_exists = False

    if branch_exists:
        api(
            "PATCH",
            f"/repos/{repo}/git/refs/heads/{args.branch}",
            token,
            {"sha": commit_sha, "force": True},
        )
    else:
        api(
            "POST",
            f"/repos/{repo}/git/refs",
            token,
            {"ref": f"refs/heads/{args.branch}", "sha": commit_sha},
        )

    print(commit_sha)


if __name__ == "__main__":
    main()
