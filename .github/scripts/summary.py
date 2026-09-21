#!/usr/bin/env python3
# @authormark v1 -- do not remove (authorship watermark)⁠​‌‌‌​‌​​​‌​​​‌‌​​‌‌​​​​‌​‌‌‌​​​‌​‌​‌​‌​‌​​‌‌​​‌‌​‌​‌‌​​‌​‌​‌​‌​​​​‌‌​​‌‌​‌‌​​‌‌​​​‌‌​​​​​‌​‌​​​​​‌‌​​‌​‌​‌​‌‌​​​​‌‌​‌​‌‌​‌‌​​‌​‌​‌‌​​‌‌​​‌​‌​​‌​​‌​‌​‌​​​‌‌​‌‌‌‌​​‌‌​​‌​​‌​​‌‌​‌⁠
# Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
# Author: https://github.com/Srinivasan-78
# SPDX-License-Identifier: MIT
# Fingerprint: AMK1.tFaqU3YT3f0PeXkefRTo2M
"""Render a GitHub Actions job-summary Markdown page for a repo2graph build.

GitHub-Actions-only, standalone: stdlib only, no dependency on the
`repo2graph` package being importable (this runs as a CI step, sometimes
before or independent of the pip install). Reads the artifacts a build
already wrote -- `agent/stats.json`, `agent/nodes.jsonl`, `agent/edges.jsonl`,
and optionally `human/CHANGELOG.md` -- and writes Markdown to stdout. The
workflow YAML is responsible for redirecting that stdout to
`$GITHUB_STEP_SUMMARY`; this script never touches that env var itself, which
keeps it runnable and testable standalone.

Degrades everywhere rather than failing: a missing file, an unparsable line,
a stats.json without an expected key, a non-git checkout, or a missing
--changelog all fall back to "N/A" or an omitted row/section. This script
must always exit 0.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

# symbol:* kinds that count as "Functions" in the at-a-glance table. Mirrors
# graph.py's ENTRY_KINDS -- "function" and "method" are the callable symbol
# kinds every supported language maps onto (see parse.py's per-language
# kind_map tables).
FUNCTION_LIKE_KINDS = ("function", "method")

TOP_N = 5


# ---------------------------------------------------------------------------
# Artifact readers -- every one degrades to an empty/absent result rather
# than raising, per the module docstring.
# ---------------------------------------------------------------------------


def load_stats(path: str) -> tuple[dict, bool]:
    """Return (stats dict, ok). ok is False if the file is missing/unreadable
    /not valid JSON/not a JSON object -- the caller uses that to tell "loaded
    but genuinely has no data" apart from "could not be read at all"."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {}, False
    if not isinstance(data, dict):
        return {}, False
    return data, True


def read_jsonl(path: str):
    """Yield decoded JSON objects from a JSONL file. A missing file yields
    nothing; an unparsable line is skipped rather than aborting the read."""
    try:
        text = Path(path).read_text(encoding="utf8")
    except OSError:
        return
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            yield obj


def git_short_sha(cwd: Path) -> str | None:
    """Best-effort `git rev-parse --short HEAD`. None on any failure -- not a
    git checkout, git missing, a timeout, or a non-zero exit.

    Follows the repo's git-subprocess-decoding rule (AGENTS.md): no
    text=True/encoding=, quotepath disabled, bytes decoded with
    surrogateescape, and a timeout that turns into a plain failure rather
    than a hang.
    """
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotepath=false", "rev-parse", "--short", "HEAD"],
            cwd=str(cwd),
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    sha = proc.stdout.decode("utf8", "surrogateescape").strip()
    return sha or None


# ---------------------------------------------------------------------------
# Derived data
# ---------------------------------------------------------------------------


def counts_from_stats(stats: dict, stats_ok: bool):
    """(files, functions, classes, edges), each an int or None ("N/A").

    None only when stats.json itself could not be loaded -- a key that is
    simply absent from a successfully-loaded stats dict means a real zero
    (e.g. a repo with no classes), not a missing value.
    """
    if not stats_ok:
        return None, None, None, None
    files_n = stats.get("files", 0)
    edges_n = stats.get("edges", 0)
    functions_n = sum(
        v
        for k, v in stats.items()
        if k.startswith("symbol:") and k.split(":", 1)[1] in FUNCTION_LIKE_KINDS
    )
    classes_n = stats.get("symbol:class", 0)
    return files_n, functions_n, classes_n, edges_n


def languages_from_nodes(nodes_path: str) -> list[str]:
    """Distinct `lang` values on file-type nodes, most common first."""
    tally: Counter = Counter()
    for node in read_jsonl(nodes_path):
        if node.get("type") != "file":
            continue
        lang = node.get("lang")
        if lang:
            tally[lang] += 1
    return [lang for lang, _ in tally.most_common()]


def chunk_count(nodes_path: str) -> int | None:
    """Count agent/chunks.jsonl, a sibling of nodes.jsonl in the same agent
    directory. None if the file is missing (no chunks were built)."""
    chunks_path = Path(nodes_path).with_name("chunks.jsonl")
    if not chunks_path.is_file():
        return None
    n = 0
    for _ in read_jsonl(str(chunks_path)):
        n += 1
    return n


def top_hub_files(nodes_path: str, edges_path: str, n: int = TOP_N):
    """[(node_id, in_degree), ...] for the top-n `file:`-type edge targets,
    by raw in-degree across every edge type."""
    in_degree: Counter = Counter()
    for edge in read_jsonl(edges_path):
        dst = edge.get("dst")
        if dst:
            in_degree[dst] += 1
    if not in_degree:
        return []

    node_types = {}
    for node in read_jsonl(nodes_path):
        nid = node.get("id")
        if nid:
            node_types[nid] = node.get("type")

    def is_file(nid: str) -> bool:
        return nid.startswith("file:") or node_types.get(nid) == "file"

    hubs = [(nid, count) for nid, count in in_degree.items() if is_file(nid)]
    hubs.sort(key=lambda t: (-t[1], t[0]))
    return hubs[:n]


def cochange_hotspots(edges_path: str, n: int = TOP_N):
    """[(file_a, file_b, count), ...] for the top-n CO_CHANGE edges."""
    rows = []
    for edge in read_jsonl(edges_path):
        if edge.get("type") != "CO_CHANGE":
            continue
        src, dst = edge.get("src"), edge.get("dst")
        count = edge.get("count")
        if src is None or dst is None or not isinstance(count, (int, float)):
            continue
        rows.append((str(src), str(dst), count))
    rows.sort(key=lambda t: (-t[2], t[0], t[1]))
    return rows[:n]


def _strip_file_prefix(node_id: str) -> str:
    return node_id[len("file:") :] if node_id.startswith("file:") else node_id


# ---------------------------------------------------------------------------
# CHANGELOG.md parsing -- lenient by design. The generator is separate,
# in-flight work; a missing section, a missing file, or an unexpected shape
# must never be treated as an error here.
# ---------------------------------------------------------------------------

_SECTION_COUNT_RE = re.compile(
    r"^### (New nodes|Removed nodes|New edges|Removed edges)\s*\(([+-]?\d+)\)\s*$", re.M
)
_HOTSPOTS_HEADER_RE = re.compile(r"^### New hotspots.*$", re.M)
_NEXT_HEADER_RE = re.compile(r"^### ", re.M)
_INITIAL_BUILD_MARKER = "no previous index to diff against"


def parse_changelog(text: str) -> dict:
    """Best-effort extraction of the section-header counts and the "New
    hotspots" lines out of human/CHANGELOG.md -- never the full item lists.
    Returns a dict; every key defaults to "nothing found" rather than raising
    on an unexpected shape.
    """
    result = {"headline": None, "counts": [], "hotspots": [], "initial_build": False}

    if _INITIAL_BUILD_MARKER in text:
        result["initial_build"] = True
        for line in text.split("\n"):
            line = line.strip()
            if line:
                result["headline"] = line.lstrip("#").strip()
                break
        return result

    for line in text.split("\n"):
        if line.startswith("## Graph delta"):
            result["headline"] = line.lstrip("#").strip()
            break

    result["counts"] = [(m.group(1), m.group(2)) for m in _SECTION_COUNT_RE.finditer(text)]

    hs_match = _HOTSPOTS_HEADER_RE.search(text)
    if hs_match:
        rest = text[hs_match.end() :]
        next_header = _NEXT_HEADER_RE.search(rest)
        block = rest[: next_header.start()] if next_header else rest
        result["hotspots"] = [
            line.strip()[2:].strip() for line in block.split("\n") if line.strip().startswith("- ")
        ]

    return result


def render_graph_delta(changelog_path: str | None) -> str | None:
    """The "### Graph delta" section, or None to omit it entirely (no
    --changelog, the file doesn't exist, or it couldn't be read)."""
    if not changelog_path:
        return None
    p = Path(changelog_path)
    if not p.is_file():
        return None
    try:
        text = p.read_text(encoding="utf8")
    except OSError:
        return None

    info = parse_changelog(text)
    lines = ["### Graph delta", ""]

    if info["initial_build"]:
        lines.append(info["headline"] or "Initial build — no previous index to diff against.")
        return "\n".join(lines) + "\n"

    if info["headline"]:
        lines.append(f"**{info['headline']}**")
        lines.append("")

    wrote_body = False
    if info["counts"]:
        for name, count in info["counts"]:
            lines.append(f"- {name} ({count})")
        wrote_body = True
    if info["hotspots"]:
        if wrote_body:
            lines.append("")
        lines.append("New hotspots:")
        for h in info["hotspots"]:
            lines.append(f"- {h}")
        wrote_body = True

    if not wrote_body:
        lines.append("_CHANGELOG.md present but no recognizable sections were found._")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _fmt(value) -> str:
    return "N/A" if value is None else str(value)


def render(args) -> str:
    stats, stats_ok = load_stats(args.stats)
    files_n, functions_n, classes_n, edges_n = counts_from_stats(stats, stats_ok)
    languages = languages_from_nodes(args.nodes)
    chunks_n = chunk_count(args.nodes)
    sha = git_short_sha(Path.cwd())

    out = ["## repo2graph — index summary", ""]
    out.append("| Metric         | Value  |")
    out.append("|----------------|--------|")
    out.append(f"| Files indexed  | {_fmt(files_n)}    |")
    out.append(f"| Functions      | {_fmt(functions_n)}    |")
    out.append(f"| Classes        | {_fmt(classes_n)}    |")
    out.append(f"| Total edges    | {_fmt(edges_n)}    |")
    out.append(f"| Languages      | {', '.join(languages) if languages else 'N/A'} |")
    out.append(f"| Chunks         | {_fmt(chunks_n)}    |")
    out.append(f"| Built at       | {_fmt(sha)}  |")
    out.append("")

    hubs = top_hub_files(args.nodes, args.edges)
    out.append("### Top 5 hub files")
    if hubs:
        out.append("| Rank | Node                        | In-degree |")
        out.append("|------|-----------------------------|-----------|")
        for i, (nid, degree) in enumerate(hubs, 1):
            out.append(f"| {i}    | {nid} | {degree} |")
    else:
        out.append("_No file nodes with incoming edges were found._")
    out.append("")

    hotspots = cochange_hotspots(args.edges)
    if hotspots:
        out.append("### CO_CHANGE hotspots")
        out.append("| File A          | File B          | Co-changes |")
        out.append("|-----------------|-----------------|------------|")
        for a, b, count in hotspots:
            out.append(f"| {_strip_file_prefix(a)} | {_strip_file_prefix(b)} | {count} |")
        out.append("")

    delta = render_graph_delta(args.changelog)
    if delta:
        out.append(delta)

    if args.artifact_name:
        out.append(
            f"> Artifact: download `{args.artifact_name}` for the full interactive graph.html"
        )

    return "\n".join(out).rstrip() + "\n"


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stats", required=True, help="path to agent/stats.json")
    p.add_argument("--nodes", required=True, help="path to agent/nodes.jsonl")
    p.add_argument("--edges", required=True, help="path to agent/edges.jsonl")
    p.add_argument(
        "--changelog",
        default=None,
        help="path to human/CHANGELOG.md (optional; omitted section if absent)",
    )
    p.add_argument("--artifact-name", default="", help="uploaded artifact name to reference")
    return p.parse_args(argv)


def main(argv=None) -> int:
    # $GITHUB_STEP_SUMMARY is UTF-8 and the map/hub labels can carry non-ASCII
    # names; a Windows console's default stdout codec is not UTF-8, so pin it
    # explicitly rather than let this depend on the runner's locale.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    args = parse_args(argv)
    try:
        text = render(args)
    except Exception as exc:  # noqa: BLE001 - this script must never fail CI
        text = (
            "## repo2graph — index summary\n\n"
            f"_Could not render the summary: {type(exc).__name__}: {exc}_\n"
        )
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
