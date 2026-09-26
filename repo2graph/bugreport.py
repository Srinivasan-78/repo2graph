"""`repo2graph bug-report`: a bundle that is useful to a maintainer and safe to post.

The two requirements pull against each other. A maintainer diagnosing "this
edge is wrong" wants the code; a user filing from a private repository cannot
give it. So the rule here is absolute and stated once:

**No file content ever leaves this module.** Not a chunk body, not a line of
source, not a snippet around the edge being reported. Everything in the bundle
is a count, a version, a status, or a name the user explicitly opted into
including.

That makes the bundle weaker than a repro, and it is meant to be: it answers
"what is your environment and what does your index look like" completely,
which is most of the round trips, and it leaves "what does the code say" to
the user's judgement on a case-by-case basis.

## Privacy levels

Paths are the interesting case, because a repo-relative path is not secret in
an open-source project and is competitively sensitive in a private one
(`billing/stripe_migration_v2.py` says a lot). So paths are **off by
default**:

- default -- counts, extensions and languages only. No path, no filename.
- `--include-paths` -- repo-relative paths included. The user is opting in.

Absolute paths are never included at either level: they carry usernames, home
directory layout and employer directory conventions, and they are of no
diagnostic value once the repo-relative path is known.

Never included at any level: environment variable *values*, the git remote
URL, the repository name, author names or emails from git, and anything read
out of a source file.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import sys
from pathlib import Path
from typing import Any

# The feedback categories, matching the GitHub issue templates one-to-one so
# `--category` and the template chooser cannot drift apart.
# tests/test_bugreport.py asserts the pairing against .github/ISSUE_TEMPLATE/.
CATEGORIES: dict[str, str] = {
    "incorrect-relationship": "An edge exists that should not, or points at the wrong target",
    "missing-relationship": "An edge that should exist does not",
    "stale-index": "The index does not match the working tree, or will not refresh",
    "parser-failure": "A file in a supported language failed to parse, or produced no symbols",
    "answer-unhelpful": "`rag`/`query`/an MCP tool returned an answer that did not help",
}

# What each category needs beyond the common bundle, so the report tells the
# user what to add by hand rather than leaving the maintainer to ask.
_CATEGORY_ASKS: dict[str, tuple[str, ...]] = {
    "incorrect-relationship": (
        "the output of `repo2graph explain edge <src-id> <dst-id> -o <out>`",
        "what you expected the edge to be, and why",
    ),
    "missing-relationship": (
        "the two node ids that should be connected (`repo2graph explain node <id> -o <out>`)",
        "the line of code that should have produced the edge, if you can share it",
        "whether the call is dynamic -- see docs/limitations.md, which makes most of these expected",
    ),
    "stale-index": (
        "the output of `repo2graph index-status -o <out>`",
        "whether the index was built by CI, the Action, or by hand",
    ),
    "parser-failure": (
        "the language and the file extension",
        "a *minimal* snippet that reproduces it, if the code can be shared",
        "the output of `repo2graph doctor .` (grammar versions)",
    ),
    "answer-unhelpful": (
        "the exact question you asked",
        "what you expected to be cited that was not",
        "the `--budget` you used, if not the default",
    ),
}


# Any whitespace-delimited token that looks like a path: it has a separator
# and something either side of one.
_PATHISH_TOKEN = re.compile(r"\S*[\\/]\S*")


def _scrub(text: str) -> str:
    """Redact anything path-shaped out of free-text prose bound for the bundle.

    Notes and check summaries are prose assembled elsewhere -- several of them
    interpolate an exception message, and an `OSError` carries the absolute
    path that failed. `compute_freshness`'s "could not re-discover the source
    tree ({exc})" was a live example: it put
    `C:\\Users\\<name>\\proj\\pkg\\secret_roadmap.py` into a bundle that
    promises no absolute paths and no filenames.

    Scrubbing here rather than fixing each producer is deliberate. The
    guarantee has to hold for prose written by code that has never heard of
    this module, including code added later; a filter that depends on every
    upstream message staying path-free is a promise waiting to be broken.
    """
    return _PATHISH_TOKEN.sub("<path redacted>", text)


def _scrub_all(items: Any) -> Any:
    """`_scrub` over a list of strings, preserving order."""
    return [_scrub(str(i)) for i in (items or [])]


def _fingerprint(value: str) -> str:
    """A short, stable, non-reversible tag for a string we will not print.

    Lets a maintainer see that two entries are the same path without learning
    what it is, and lets a user confirm a specific file is represented. Eight
    hex characters: enough to distinguish the handful of paths in a report,
    far too few to brute-force a filesystem from.
    """
    return hashlib.sha256(value.encode("utf8", "surrogateescape")).hexdigest()[:8]


def _safe_environment() -> dict[str, Any]:
    """Versions and platform. No environment variable values, ever."""
    try:
        from . import __version__
    except Exception:
        __version__ = "unknown"

    versions: dict[str, Any] = {"repo2graph": __version__, "python": sys.version.split()[0]}
    for dist in (
        "tree-sitter",
        "tree-sitter-language-pack",
        "mcp",
        "numpy",
        "sentence-transformers",
    ):
        try:
            import importlib.metadata

            versions[dist] = importlib.metadata.version(dist)
        except Exception:
            versions[dist] = None

    return {
        "versions": versions,
        "platform": {
            # platform.platform() can embed a hostname on some systems;
            # system/release/machine cannot, and answer the same question.
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python_implementation": platform.python_implementation(),
            "stdout_encoding": getattr(sys.stdout, "encoding", None),
            "filesystem_encoding": sys.getfilesystemencoding(),
        },
    }


def _index_summary(out: Path, repo: Path | None, include_paths: bool) -> dict[str, Any]:
    """Shape and health of the index, with provenance narrowed to what helps."""
    try:
        from .status import index_status

        report = index_status(out, repo=repo)
    except Exception as exc:
        return {"available": False, "error": _scrub(f"{type(exc).__name__}: {exc}")}

    src = report["source"]
    # The commit is kept: it is what makes "wrong edge" reproducible against a
    # public repo, and it reveals nothing about a private one that its own
    # history does not. The remote URL, repo name and branch name are dropped
    # -- a branch like `feat/acquire-northwind` is a leak, and none of the
    # three changes the diagnosis.
    return {
        "available": True,
        "commit": src.get("commit"),
        "dirty_at_build": bool(src.get("dirty")),
        "index": {
            "tool_version": report["index"].get("tool_version"),
            "schema_version": report["index"].get("schema_version"),
            "age_seconds": report["index"].get("age_seconds"),
            "size_bytes": report["index"].get("size_bytes"),
            "has_vectors": report["index"].get("has_vectors"),
        },
        "contents": report["contents"],
        "discovery": report["discovery"],
        "parsing": report["parsing"],
        "freshness": {
            "status": report["freshness"]["status"],
            "reasons": _scrub_all(report["freshness"]["reasons"]),
            "notes": _scrub_all(report["freshness"]["notes"]),
            "counts": report["freshness"]["counts"],
            # The paths of changed files are exactly the kind of thing that
            # leaks a roadmap, so they are fingerprints unless opted in.
            "changed": (
                {k: report["freshness"][k] for k in ("added", "removed", "modified")}
                if include_paths
                else {
                    k: [_fingerprint(p) for p in report["freshness"][k]]
                    for k in ("added", "removed", "modified")
                }
            ),
        },
    }


def _edge_quality(out: Path) -> dict[str, Any]:
    """How much of the graph is confident, and how it was extracted.

    This is the single most useful block for an "incorrect relationship"
    report: it says whether the repository as a whole resolves cleanly or is
    dominated by ambiguous name matches, which changes what the reported edge
    means. Counts only -- no ids, no paths.
    """
    from .edgemeta import EDGE_SCHEMA_VERSION

    edges_file = (
        (out / "agent" / "edges.jsonl") if (out / "agent").is_dir() else (out / "edges.jsonl")
    )
    if not edges_file.exists():
        return {"available": False}

    by_type: dict[str, int] = {}
    by_method: dict[str, int] = {}
    confidence_buckets = {"1.0": 0, "0.5-0.99": 0, "0.25-0.49": 0, "<0.25": 0}
    with_evidence = 0
    ambiguous = 0
    total = 0
    try:
        with open(edges_file, encoding="utf8", errors="surrogateescape", newline="\n") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                total += 1
                by_type[e.get("type", "?")] = by_type.get(e.get("type", "?"), 0) + 1
                by_method[str(e.get("method"))] = by_method.get(str(e.get("method")), 0) + 1
                if e.get("evidence"):
                    with_evidence += 1
                if e.get("ambiguous"):
                    ambiguous += 1
                conf = e.get("confidence")
                if isinstance(conf, (int, float)):
                    if conf >= 1.0:
                        confidence_buckets["1.0"] += 1
                    elif conf >= 0.5:
                        confidence_buckets["0.5-0.99"] += 1
                    elif conf >= 0.25:
                        confidence_buckets["0.25-0.49"] += 1
                    else:
                        confidence_buckets["<0.25"] += 1
    except OSError as exc:
        return {"available": False, "error": _scrub(str(exc))}

    return {
        "available": True,
        "edge_schema_version": EDGE_SCHEMA_VERSION,
        "total": total,
        "by_type": by_type,
        "by_method": by_method,
        "confidence": confidence_buckets,
        "with_evidence": with_evidence,
        "ambiguous": ambiguous,
    }


def _doctor_summary() -> dict[str, Any]:
    """Every check's name and status, and remediations. No detail lines.

    The detail lines are where doctor prints filesystem paths and config file
    locations, so they are dropped wholesale rather than filtered one by one
    -- a filter that has to be kept in step with another module's output is a
    leak waiting for the next check to be added.
    """
    try:
        from .doctor import run_doctor

        report = run_doctor(".")
    except Exception as exc:
        return {"available": False, "error": _scrub(f"{type(exc).__name__}: {exc}")}
    return {
        "available": True,
        "ok": report.ok,
        "checks": [
            {"name": c.name, "status": c.status, "summary": _scrub(c.summary)}
            for c in report.checks
            if c.status != "ok"
        ],
        "passing": [c.name for c in report.checks if c.status == "ok"],
    }


def build_report(
    out: Path | str = ".r2g",
    *,
    repo: Path | str | None = None,
    category: str | None = None,
    include_paths: bool = False,
) -> dict[str, Any]:
    """Assemble the bundle. Never reads a source file."""
    out = Path(out)
    repo_path = Path(repo) if repo is not None else None

    report: dict[str, Any] = {
        "bundle": "repo2graph/bug-report-1",
        "category": category,
        "privacy": {
            "paths_included": include_paths,
            "contains_source_code": False,
            "contains_env_values": False,
            "contains_remote_url": False,
        },
        "environment": _safe_environment(),
        "doctor": _doctor_summary(),
        "index": _index_summary(out, repo_path, include_paths),
        "edges": _edge_quality(out),
    }
    if category and category in _CATEGORY_ASKS:
        report["please_also_attach"] = list(_CATEGORY_ASKS[category])
    return report


def format_report(report: dict[str, Any]) -> str:
    """The markdown a user pastes into an issue."""
    env = report["environment"]
    lines = [
        "<!-- repo2graph bug-report bundle. No source code, no environment",
        "     variable values, no remote URL, no absolute paths. -->",
        "",
        "### repo2graph bug report",
        "",
    ]
    if report.get("category"):
        lines += [
            f"**Category:** `{report['category']}` -- {CATEGORIES.get(report['category'], '')}",
            "",
        ]

    lines += ["**Environment**", "", "```json", json.dumps(env, indent=2), "```", ""]

    doc = report["doctor"]
    lines.append("**Diagnostics**")
    lines.append("")
    if not doc.get("available"):
        lines.append(f"- doctor could not run: {doc.get('error')}")
    elif not doc["checks"]:
        lines.append(f"- all {len(doc['passing'])} checks passed")
    else:
        for c in doc["checks"]:
            lines.append(f"- **{c['status'].upper()}** {c['name']}: {c['summary']}")
    lines.append("")

    idx = report["index"]
    lines.append("**Index**")
    lines.append("")
    if not idx.get("available"):
        lines.append(f"- no readable index: {idx.get('error')}")
    else:
        lines += [
            "```json",
            json.dumps({k: v for k, v in idx.items() if k != "available"}, indent=2),
            "```",
        ]
    lines.append("")

    edges = report["edges"]
    if edges.get("available"):
        lines += [
            "**Edge quality**",
            "",
            "```json",
            json.dumps({k: v for k, v in edges.items() if k != "available"}, indent=2),
            "```",
            "",
        ]

    if report.get("please_also_attach"):
        lines.append("**Please also attach, by hand:**")
        lines.append("")
        for ask in report["please_also_attach"]:
            lines.append(f"- [ ] {ask}")
        lines.append("")

    if not report["privacy"]["paths_included"]:
        lines.append(
            "_File paths are omitted from this bundle. Re-run with `--include-paths` "
            "to include repo-relative paths if your repository is public._"
        )
    return "\n".join(lines)
