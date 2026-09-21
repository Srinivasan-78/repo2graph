#!/usr/bin/env python3
"""Create a GitHub-signed commit and point a branch at it.

`git commit` on an Actions runner is never marked Verified by GitHub, even when
pushed with an authenticated bot/App token. `main` requires signed commits, so
publish.yml's release-bump commit must be created through the API, or every
release PR is permanently blocked from merging (the auto-merge poll in
publish.yml times out at 60 minutes and fails the run).

**Which API matters.** GitHub signs commits it creates through the Contents API
and through the GraphQL `createCommitOnBranch` mutation. It does *not* sign
commits built up out of the Git Data API -- `POST /git/blobs` + `/git/trees` +
`/git/commits` returns `verified: false, reason: unsigned`, because that route
hands GitHub a commit object the caller assembled rather than asking GitHub to
author one. This script used the Git Data route until 2026-09-21 and every
commit it produced was unsigned; the release flow only ever merged because an
admin bypass was available. It now uses `createCommitOnBranch`, which is the
only multi-file route that signs.

**Why the temporary ref.** `createCommitOnBranch` commits *onto* a branch that
already exists, so reproducing "fork from base, then force the branch there"
would mean resetting the branch back to base first. When that branch is the
head of an open PR, the reset leaves the PR with zero commits and GitHub closes
it automatically. So an existing branch is rebuilt on a temporary ref and moved
in a single force-update, and the PR never observes an empty state.

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
GRAPHQL = f"{API}/graphql"

# Rebuild an existing branch here, then move it in one update. See the module
# docstring: a branch reset in place empties an open PR and GitHub closes it.
STAGING_SUFFIX = "-signing-staging"

CREATE_COMMIT = """
mutation ($input: CreateCommitOnBranchInput!) {
  createCommitOnBranch(input: $input) {
    commit { oid }
  }
}
"""


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
            raw = resp.read()
            # DELETE /git/refs answers 204 with an empty body.
            result: dict[str, Any] = json.loads(raw) if raw.strip() else {}
            return result
    except urllib.error.HTTPError as exc:
        sys.stderr.write(f"{method} {path} -> {exc.code}: {exc.read().decode()}\n")
        raise


def graphql(
    query: str,
    variables: dict[str, Any],
    token: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """POST a GraphQL operation and return `data`.

    GraphQL reports failures as HTTP 200 with an `errors` array, so a plain
    `urlopen` success here means nothing on its own -- without this check a
    failed commit would print `None` and the release would carry on.
    """
    req = urllib.request.Request(
        GRAPHQL,
        method="POST",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body: dict[str, Any] = json.load(resp)
    except urllib.error.HTTPError as exc:
        sys.stderr.write(f"POST /graphql -> {exc.code}: {exc.read().decode()}\n")
        raise
    if body.get("errors"):
        sys.stderr.write(f"POST /graphql -> errors: {json.dumps(body['errors'])}\n")
        raise RuntimeError(f"GraphQL error: {body['errors']}")
    data: dict[str, Any] = body["data"]
    return data


def ref_sha(repo: str, branch: str, token: str) -> str | None:
    """Current head of `branch`, or None when the branch does not exist."""
    try:
        ref = api("GET", f"/repos/{repo}/git/ref/heads/{branch}", token)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    sha: str = ref["object"]["sha"]
    return sha


def set_ref(repo: str, branch: str, sha: str, token: str) -> None:
    """Point `branch` at `sha`, creating the branch if it is not there yet."""
    if ref_sha(repo, branch, token) is None:
        api("POST", f"/repos/{repo}/git/refs", token, {"ref": f"refs/heads/{branch}", "sha": sha})
    else:
        api("PATCH", f"/repos/{repo}/git/refs/heads/{branch}", token, {"sha": sha, "force": True})


def split_message(message: str) -> dict[str, str]:
    """Split a git-style message into the headline/body pair GraphQL wants."""
    headline, _, body = message.partition("\n")
    return {"headline": headline.strip(), "body": body.strip()}


def file_additions(paths: list[str]) -> list[dict[str, str]]:
    """Read each file as bytes and base64 it for `fileChanges.additions`.

    Paths are normalised to POSIX (ISS-146): this script also runs on Windows,
    where `dist\\sub\\file.txt` would otherwise be committed as a single file
    with backslashes in its name rather than as `dist/sub/file.txt`.
    """
    additions = []
    for path in paths:
        with open(path, "rb") as fh:
            content = fh.read()
        additions.append(
            {
                "path": Path(path).as_posix().replace("\\", "/"),
                "contents": base64.b64encode(content).decode("ascii"),
            }
        )
    return additions


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

    base_sha = ref_sha(repo, args.base, token)
    if base_sha is None:
        raise SystemExit(f"base branch not found: {args.base}")

    # An absent target branch can be created at base and committed on directly.
    # An existing one is staged elsewhere so its PR never sees zero commits.
    target_exists = ref_sha(repo, args.branch, token) is not None
    commit_branch = f"{args.branch}{STAGING_SUFFIX}" if target_exists else args.branch
    set_ref(repo, commit_branch, base_sha, token)

    try:
        data = graphql(
            CREATE_COMMIT,
            {
                "input": {
                    "branch": {
                        "repositoryNameWithOwner": repo,
                        "branchName": commit_branch,
                    },
                    "expectedHeadOid": base_sha,
                    "message": split_message(args.message),
                    "fileChanges": {"additions": file_additions(args.files)},
                }
            },
            token,
        )
        commit_sha: str = data["createCommitOnBranch"]["commit"]["oid"]

        if target_exists:
            set_ref(repo, args.branch, commit_sha, token)
    finally:
        if commit_branch != args.branch:
            try:
                api("DELETE", f"/repos/{repo}/git/refs/heads/{commit_branch}", token)
            except urllib.error.HTTPError:
                # A leftover staging ref is harmless and the next run resets it;
                # failing the release over the cleanup would not be.
                sys.stderr.write(f"warning: could not delete staging ref {commit_branch}\n")

    print(commit_sha)


if __name__ == "__main__":
    main()
