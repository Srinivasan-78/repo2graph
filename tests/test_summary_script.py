"""Tests for .github/scripts/summary.py -- the GitHub Actions job-summary
renderer that replaced `head -40 .r2g/human/overview.md >> $GITHUB_STEP_SUMMARY`.

GitHub-Actions-only surface: nothing here imports the `repo2graph` package,
matching the script's own stdlib-only, standalone-runnable contract. Every
test drives the real script as a subprocess so the assertions exercise
exactly what the workflow step invokes.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / ".github" / "scripts" / "summary.py"


def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf8")


def _mock_artifacts(tmp_path: Path):
    """A small, realistic-looking build: 4 files (python/go/markdown), a
    handful of symbols, and edges that give one clear hub file and two
    CO_CHANGE pairs."""
    agent = tmp_path / "agent"
    stats = {
        "files": 12,
        "edges": 45,
        "parse_errors": 0,
        "entrypoints": 3,
        "edge:CALLS": 20,
        "edge:DEFINES": 15,
        "edge:IMPORTS": 8,
        "edge:CO_CHANGE": 2,
        "symbol:function": 10,
        "symbol:method": 4,
        "symbol:class": 3,
    }
    (agent).mkdir(parents=True, exist_ok=True)
    (agent / "stats.json").write_text(json.dumps(stats, indent=2), encoding="utf8")

    nodes = [
        {
            "id": "file:pkg/core.py",
            "type": "file",
            "name": "core.py",
            "path": "pkg/core.py",
            "lang": "python",
        },
        {
            "id": "file:pkg/util.py",
            "type": "file",
            "name": "util.py",
            "path": "pkg/util.py",
            "lang": "python",
        },
        {
            "id": "file:cmd/main.go",
            "type": "file",
            "name": "main.go",
            "path": "cmd/main.go",
            "lang": "go",
        },
        {
            "id": "file:README.md",
            "type": "file",
            "name": "README.md",
            "path": "README.md",
            "lang": "markdown",
        },
        {
            "id": "sym:pkg/core.py::dispatch",
            "type": "symbol",
            "kind": "function",
            "path": "pkg/core.py",
        },
        {
            "id": "sym:pkg/util.py::helper",
            "type": "symbol",
            "kind": "function",
            "path": "pkg/util.py",
        },
    ]
    _write_jsonl(agent / "nodes.jsonl", nodes)

    edges = [
        {"src": "dir:pkg", "dst": "file:pkg/core.py", "type": "CONTAINS"},
        {"src": "file:pkg/util.py", "dst": "file:pkg/core.py", "type": "IMPORTS"},
        {"src": "file:cmd/main.go", "dst": "file:pkg/core.py", "type": "IMPORTS"},
        {
            "src": "sym:pkg/util.py::helper",
            "dst": "sym:pkg/core.py::dispatch",
            "type": "CALLS",
            "confidence": 1.0,
        },
        {"src": "file:pkg/core.py", "dst": "sym:pkg/core.py::dispatch", "type": "DEFINES"},
        {"src": "file:pkg/util.py", "dst": "file:cmd/main.go", "type": "CO_CHANGE", "count": 5},
        {"src": "file:pkg/core.py", "dst": "file:pkg/util.py", "type": "CO_CHANGE", "count": 8},
    ]
    _write_jsonl(agent / "edges.jsonl", edges)
    return agent / "stats.json", agent / "nodes.jsonl", agent / "edges.jsonl"


def _run(tmp_path, *, stats, nodes, edges, changelog=None, artifact_name="repo-graph", cwd=None):
    argv = [
        sys.executable,
        str(SCRIPT_PATH),
        "--stats",
        str(stats),
        "--nodes",
        str(nodes),
        "--edges",
        str(edges),
        "--artifact-name",
        artifact_name,
    ]
    if changelog is not None:
        argv += ["--changelog", str(changelog)]
    proc = subprocess.run(
        argv,
        cwd=str(cwd or tmp_path),
        capture_output=True,
        encoding="utf8",
        timeout=60,
    )
    return proc


# ---------------------------------------------------------------------------
# (c) the at-a-glance stats table and the top-hub-nodes table
# ---------------------------------------------------------------------------


def test_summary_has_at_a_glance_table(tmp_path):
    stats, nodes, edges = _mock_artifacts(tmp_path)
    proc = _run(tmp_path, stats=stats, nodes=nodes, edges=edges, cwd=REPO_ROOT)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout

    assert "## repo2graph — index summary" in out
    assert "| Metric         | Value  |" in out
    assert "| Files indexed  | 12    |" in out
    assert "| Functions      | 14    |" in out  # symbol:function(10) + symbol:method(4)
    assert "| Classes        | 3    |" in out
    assert "| Total edges    | 45    |" in out
    assert "python" in out and "go" in out and "markdown" in out


def test_summary_has_top_hub_files_table(tmp_path):
    stats, nodes, edges = _mock_artifacts(tmp_path)
    proc = _run(tmp_path, stats=stats, nodes=nodes, edges=edges)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout

    assert "### Top 5 hub files" in out
    # file:pkg/core.py has in-degree 3 (CONTAINS + 2x IMPORTS); the highest
    assert re.search(r"\|\s*1\s*\|\s*file:pkg/core\.py\s*\|\s*3\s*\|", out), out


def test_summary_has_cochange_hotspots_sorted_desc(tmp_path):
    stats, nodes, edges = _mock_artifacts(tmp_path)
    proc = _run(tmp_path, stats=stats, nodes=nodes, edges=edges)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout

    assert "### CO_CHANGE hotspots" in out
    core_util = out.index("pkg/core.py | pkg/util.py | 8")
    util_main = out.index("pkg/util.py | cmd/main.go | 5")
    assert core_util < util_main  # higher count listed first


def test_summary_mentions_artifact_name(tmp_path):
    stats, nodes, edges = _mock_artifacts(tmp_path)
    proc = _run(tmp_path, stats=stats, nodes=nodes, edges=edges, artifact_name="my-graph")
    assert proc.returncode == 0, proc.stderr
    assert "download `my-graph`" in proc.stdout


# ---------------------------------------------------------------------------
# (d) graceful degradation
# ---------------------------------------------------------------------------


def test_summary_degrades_built_at_when_not_a_git_checkout(tmp_path):
    """Run with cwd=tmp_path, which is a plain directory, not a git repo."""
    stats, nodes, edges = _mock_artifacts(tmp_path)
    proc = _run(tmp_path, stats=stats, nodes=nodes, edges=edges, cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert re.search(r"\|\s*Built at\s*\|\s*N/A\s*\|", proc.stdout), proc.stdout


# No changelog to diff against, either because none was asked for or because the
# path given is not there. Both must render the rest of the summary and exit 0 --
# a missing optional input is not a failure.
@pytest.mark.parametrize(
    "pass_changelog",
    [pytest.param(False, id="flag-not-passed"), pytest.param(True, id="file-missing")],
)
def test_summary_omits_graph_delta_without_a_changelog(tmp_path, pass_changelog):
    stats, nodes, edges = _mock_artifacts(tmp_path)
    extra = {"changelog": tmp_path / "human" / "CHANGELOG.md"} if pass_changelog else {}
    proc = _run(tmp_path, stats=stats, nodes=nodes, edges=edges, **extra)
    assert proc.returncode == 0, proc.stderr
    assert "Graph delta" not in proc.stdout


def test_summary_survives_missing_stats_file(tmp_path):
    _, nodes, edges = _mock_artifacts(tmp_path)
    proc = _run(tmp_path, stats=tmp_path / "agent" / "nope.json", nodes=nodes, edges=edges)
    assert proc.returncode == 0, proc.stderr
    assert "Traceback" not in proc.stderr
    assert re.search(r"\|\s*Files indexed\s*\|\s*N/A\s*\|", proc.stdout), proc.stdout
    assert re.search(r"\|\s*Functions\s*\|\s*N/A\s*\|", proc.stdout), proc.stdout


def test_summary_survives_missing_nodes_and_edges_files(tmp_path):
    """`jsonl` might not be in `formats`, so nodes.jsonl/edges.jsonl can be
    absent even though stats.json always exists."""
    stats, _, _ = _mock_artifacts(tmp_path)
    proc = _run(
        tmp_path,
        stats=stats,
        nodes=tmp_path / "agent" / "nope-nodes.jsonl",
        edges=tmp_path / "agent" / "nope-edges.jsonl",
    )
    assert proc.returncode == 0, proc.stderr
    assert "Traceback" not in proc.stderr
    assert "N/A" in proc.stdout  # languages and/or chunks fall back
    assert "_No file nodes with incoming edges were found._" in proc.stdout


def test_summary_survives_malformed_jsonl_lines(tmp_path):
    stats, nodes, edges = _mock_artifacts(tmp_path)
    with open(edges, "a", encoding="utf8") as fh:
        fh.write("not valid json at all\n")
        fh.write("\n")  # blank line
    proc = _run(tmp_path, stats=stats, nodes=nodes, edges=edges)
    assert proc.returncode == 0, proc.stderr
    assert "Traceback" not in proc.stderr
    assert "### Top 5 hub files" in proc.stdout


def test_summary_never_crashes_on_completely_missing_everything(tmp_path):
    proc = _run(
        tmp_path,
        stats=tmp_path / "a.json",
        nodes=tmp_path / "n.jsonl",
        edges=tmp_path / "e.jsonl",
        changelog=tmp_path / "CHANGELOG.md",
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Traceback" not in proc.stderr
    assert "## repo2graph — index summary" in proc.stdout


# ---------------------------------------------------------------------------
# human/CHANGELOG.md handling
# ---------------------------------------------------------------------------

FULL_CHANGELOG = """\
## Graph delta — a1b2c3d vs 9f8e7d6  (2026-09-10)

### New nodes (+7)
- sym:pkg/core.py::new_thing
- file:pkg/newmod.py

### Removed nodes (-2)
- sym:pkg/old.py::dead_fn

### New edges (+12)
- file:pkg/core.py -> file:pkg/newmod.py (IMPORTS)

### Removed edges (-1)
- sym:pkg/old.py::dead_fn -> sym:pkg/core.py::helper (CALLS)

### New hotspots (nodes that gained 3+ in-degree since last build)
- sym:pkg/core.py::dispatch  (was 5, now 11)
- file:pkg/core.py  (was 2, now 6)
"""

INITIAL_CHANGELOG = "## Initial build — no previous index to diff against.\n"


def test_summary_condenses_changelog_to_header_counts_and_hotspots(tmp_path):
    stats, nodes, edges = _mock_artifacts(tmp_path)
    changelog = tmp_path / "human" / "CHANGELOG.md"
    changelog.parent.mkdir(parents=True, exist_ok=True)
    changelog.write_text(FULL_CHANGELOG, encoding="utf8")

    proc = _run(tmp_path, stats=stats, nodes=nodes, edges=edges, changelog=changelog)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout

    assert "### Graph delta" in out
    assert "New nodes (+7)" in out
    assert "Removed nodes (-2)" in out
    assert "New edges (+12)" in out
    assert "Removed edges (-1)" in out
    assert "sym:pkg/core.py::dispatch  (was 5, now 11)" in out
    assert "file:pkg/core.py  (was 2, now 6)" in out

    # the full bullet lists must NOT be reproduced -- only the header counts
    # and the hotspot lines
    assert "sym:pkg/core.py::new_thing" not in out
    assert "sym:pkg/old.py::dead_fn -> sym:pkg/core.py::helper" not in out


def test_summary_handles_initial_build_changelog(tmp_path):
    stats, nodes, edges = _mock_artifacts(tmp_path)
    changelog = tmp_path / "human" / "CHANGELOG.md"
    changelog.parent.mkdir(parents=True, exist_ok=True)
    changelog.write_text(INITIAL_CHANGELOG, encoding="utf8")

    proc = _run(tmp_path, stats=stats, nodes=nodes, edges=edges, changelog=changelog)
    assert proc.returncode == 0, proc.stderr
    assert "Initial build" in proc.stdout
    assert "no previous index to diff against" in proc.stdout
