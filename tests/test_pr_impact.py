"""Tests for PR / git diff impact analysis workflow.

Validates:
- Unified diff parsing with chunk line mapping (split("\\n") only).
- AST symbol matching against diff line intervals.
- Reverse call graph expansion (CALLS in) and dependent module impact (IMPORTS in).
- Test reachability detection.
- Suspicious findings (disconnected diffs, untested public APIs, high blast radius).
- Formatters (JSON, Markdown, PR comment, SARIF 2.1.0).
- CLI integration via cmd_impact.
- MCP tool integration via tool_repo_impact (including clamping and secret exclusions).
"""

import json
import re
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from repo2graph import edgemeta
from repo2graph.impact import (
    PR_COMMENT_MARKER,
    PR_COMMENT_MAX_APIS,
    DependencyPath,
    FileDiff,
    ImpactReport,
    ImpactedCaller,
    ImpactedTest,
    SuspiciousFinding,
    SymbolChange,
    analyze_diff_impact,
    format_json,
    format_markdown,
    format_pr_comment,
    format_sarif,
    is_public_symbol,
    is_test_path,
    parse_unified_diff,
)
from repo2graph.mcp import tool_repo_impact


# ---------------------------------------------------------------------------
# 1. Diff Parser Tests
# ---------------------------------------------------------------------------


def test_parse_unified_diff_basic():
    diff_text = (
        "diff --git a/pkg/service.py b/pkg/service.py\n"
        "index 1234567..89abcdef 100644\n"
        "--- a/pkg/service.py\n"
        "+++ b/pkg/service.py\n"
        "@@ -10,3 +10,5 @@ class Service:\n"
        "     def handle(self):\n"
        "-        pass\n"
        "+        res = 42\n"
        "+        return res\n"
        "@@ -30,2 +32,3 @@ def helper():\n"
        "+    print('debug')\n"
        "     return True\n"
    )

    diffs = parse_unified_diff(diff_text)
    assert "pkg/service.py" in diffs
    fd = diffs["pkg/service.py"]
    assert fd.path == "pkg/service.py"
    assert fd.status == "modified"
    assert len(fd.hunks) == 2
    assert fd.deleted_lines_count == 1
    # Line numbers added in hunk 1: line 10 + 1 = 11, line 12
    assert 11 in fd.added_lines
    assert 12 in fd.added_lines
    # Line number added in hunk 2: line 32
    assert 32 in fd.added_lines


def test_a_diff_that_quotes_a_diff_does_not_rewrite_the_enclosing_file():
    """Hunk *content* must never be read as a file header.

    Every line inside a hunk carries a `+`/`-`/space prefix, so a source line
    reading `++ b/oops.py` arrives as `+++ b/oops.py` and one reading
    `-- /dev/null` arrives as `--- /dev/null`. Matching those against the
    `+++ `/`--- ` header branches let a diff's own content rename the enclosing
    file, delete its entry, and flip its status -- so every symbol in the
    genuinely-changed file was skipped by `analyze_diff_impact`'s
    `path not in file_diffs` test and the report named a file never touched.
    A diff that merely quotes a diff (a fixture, a doc, this repo's own tests)
    is enough. Line numbers are hand-derived from the hunk headers below.
    """
    # A `+++`-shaped added line: both lines are content, neither is a header.
    quoted = (
        "diff --git a/notes.md b/notes.md\n"
        "--- a/notes.md\n"
        "+++ b/notes.md\n"
        "@@ -5,0 +6,2 @@\n"
        "+++ b/oops.py\n"
        "+real added line\n"
    )
    diffs = parse_unified_diff(quoted)
    assert set(diffs) == {"notes.md"}, f"content invented a file: {sorted(diffs)}"
    assert diffs["notes.md"].status == "modified"
    assert diffs["notes.md"].added_lines == {6, 7}

    # A `--- /dev/null`-shaped deleted line must not flip status to "added",
    # which would mark every symbol in the file as newly added and public.
    devnull = (
        "diff --git a/notes.md b/notes.md\n"
        "--- a/notes.md\n"
        "+++ b/notes.md\n"
        "@@ -5,1 +4,0 @@\n"
        "--- /dev/null\n"
    )
    fd = parse_unified_diff(devnull)["notes.md"]
    assert fd.status == "modified"
    assert fd.added_lines == set()
    assert fd.deleted_lines_count == 1

    # A 4-plus line escapes the `+++ ` header check (no space at index 3) but was
    # then excluded by a `not startswith("+++")` guard, so line 12 vanished from
    # added_lines while the counter kept advancing.
    four = (
        "diff --git a/x.md b/x.md\n"
        "--- a/x.md\n"
        "+++ b/x.md\n"
        "@@ -10,0 +11,3 @@\n"
        "+diff --git a/x.py b/x.py\n"
        "++++ b/x.py\n"
        "+@@ -1 +1 @@\n"
    )
    assert parse_unified_diff(four)["x.md"].added_lines == {11, 12, 13}


def test_an_unparseable_file_header_does_not_bleed_into_the_previous_file():
    """`diff --cc` and a git-quoted path both fail DIFF_GIT_RE -- and must still
    end the previous file's block.

    `current_hunk` used to be reset only on a successful DIFF_GIT_RE match, so a
    header this parser cannot split arrived with the *previous* file's hunk still
    open and its body lines were charged to that file. Phantom `added_lines` are
    load-bearing: `analyze_diff_impact` selects changed symbols from exactly that
    set, so the report names symbols the PR never touched.

    Combined diffs are not supported (`@@@` is not `HUNK_RE`), and that is fine --
    `get_git_diff` never asks git for one. What is not fine is a *supported* file
    being corrupted by an unsupported one that follows it.
    """
    combined_after = (
        "diff --git a/other.py b/other.py\n"
        "index 1..2 100644\n"
        "--- a/other.py\n"
        "+++ b/other.py\n"
        "@@ -1,0 +2,1 @@\n"
        "+y\n"
        "diff --cc m.py\n"
        "index c376d89,45cf141..20b117f\n"
        "--- a/m.py\n"
        "+++ b/m.py\n"
        "@@@ -1,1 -1,1 +1,1 @@@\n"
        "- right\n"
        " -left\n"
        "++merged\n"
    )
    fd = parse_unified_diff(combined_after)["other.py"]
    assert fd.added_lines == {2}, f"combined block leaked into other.py: {sorted(fd.added_lines)}"
    assert fd.deleted_lines_count == 0

    # git quotes a filename containing `"` whatever core.quotepath says.
    quoted_path_after = (
        "diff --git a/real.py b/real.py\n"
        "--- a/real.py\n"
        "+++ b/real.py\n"
        "@@ -1,0 +2,1 @@\n"
        "+ok\n"
        'diff --git "a/we\\"ird.py" "b/we\\"ird.py"\n'
        '--- "a/we\\"ird.py"\n'
        '+++ "b/we\\"ird.py"\n'
        "@@ -1,3 +1,1 @@\n"
        "-a\n"
        "-b\n"
        "-c\n"
        "+z\n"
    )
    fd2 = parse_unified_diff(quoted_path_after)["real.py"]
    assert fd2.added_lines == {2}, f"quoted-path block leaked: {sorted(fd2.added_lines)}"
    assert fd2.deleted_lines_count == 0


def test_no_newline_at_end_of_file_does_not_advance_the_line_counter():
    """`\\ No newline at end of file` is legal *mid*-hunk and is not a line.

    It follows the last line of whichever side lacks the trailing newline, so it
    can land between the `-` lines and the `+` lines. Counting it as a context
    line pushed every later `+` in the hunk down by one.
    """
    diff_text = (
        "diff --git a/n.txt b/n.txt\n"
        "--- a/n.txt\n"
        "+++ b/n.txt\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "\\ No newline at end of file\n"
        "+new\n"
        "\\ No newline at end of file\n"
    )
    fd = parse_unified_diff(diff_text)["n.txt"]
    assert fd.added_lines == {1}, sorted(fd.added_lines)
    assert fd.deleted_lines_count == 1


def test_parse_unified_diff_added_and_deleted_files():
    diff_text = (
        "diff --git a/pkg/new_file.py b/pkg/new_file.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/pkg/new_file.py\n"
        "@@ -0,0 +1,3 @@\n"
        "+def brand_new():\n"
        "+    return 1\n"
        "+\n"
        "diff --git a/pkg/old_file.py b/pkg/old_file.py\n"
        "deleted file mode 100644\n"
        "--- a/pkg/old_file.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-def removed():\n"
        "-    pass\n"
    )

    diffs = parse_unified_diff(diff_text)
    assert len(diffs) == 2
    assert diffs["pkg/new_file.py"].status == "added"
    assert {1, 2, 3}.issubset(diffs["pkg/new_file.py"].added_lines)

    assert diffs["pkg/old_file.py"].status == "deleted"
    assert diffs["pkg/old_file.py"].deleted_lines_count == 2


# ---------------------------------------------------------------------------
# 2. Heuristics Tests
# ---------------------------------------------------------------------------


def test_is_public_symbol():
    assert is_public_symbol({"name": "process_data", "path": "pkg/mod.py"}) is True
    assert is_public_symbol({"name": "_internal_helper", "path": "pkg/mod.py"}) is False
    assert is_public_symbol({"name": "__init__", "path": "pkg/mod.py"}) is False
    assert is_public_symbol({"name": "ExportedGoFunc", "path": "pkg/mod.go"}) is True
    assert is_public_symbol({"name": "unexportedGoFunc", "path": "pkg/mod.go"}) is False
    assert is_public_symbol({"name": "pub_func", "visibility": "public"}) is True
    assert is_public_symbol({"name": "priv_func", "visibility": "private"}) is False


def test_is_test_path():
    assert is_test_path("tests/test_api.py") is True
    assert is_test_path("test/integration_test.go") is True
    assert is_test_path("src/__tests__/button.test.tsx") is True
    assert is_test_path("src/components/card.spec.ts") is True
    assert is_test_path("pkg/service_test.go") is True
    assert is_test_path("repo2graph/query.py") is False
    assert is_test_path("src/index.ts") is False


def test_is_test_path_matches_components_not_a_path_suffix():
    """A production module whose name merely ends in `test.py` is not a test.

    The suffix form (`path.endswith("test.py")`) classified `latest.py` as test
    code, which removed every symbol in it from the public API surface, let the
    file count as its own test coverage, and made every rule that skips tests
    skip it.
    """
    assert is_test_path("pkg/latest.py") is False
    assert is_test_path("pkg/fastest.py") is False
    assert is_test_path("pkg/manifest.py") is False
    assert is_test_path("pkg/contest.py") is False
    # still tests: bare basename, top-level basename (no directory at all),
    # and a test-support directory
    assert is_test_path("pkg/test.py") is True
    assert is_test_path("test_smoke.py") is True
    assert is_test_path("pkg/test_helpers/util.py") is True


# ---------------------------------------------------------------------------
# 3. Synthetic Index & analyze_diff_impact
# ---------------------------------------------------------------------------


class MockIndex:
    """Minimal duck-typed Index for deterministic graph traversal tests."""

    def __init__(self):
        self.nodes = {}
        self.edges = []
        self.adj = {}
        self.dir = Path(".")
        self.repo_root = Path(".")

    def add_node(self, node_id: str, **attrs):
        node = {"id": node_id, **attrs}
        self.nodes[node_id] = node
        if node_id not in self.adj:
            self.adj[node_id] = []
        return node

    def add_edge(self, src: str, dst: str, etype: str, **attrs):
        # Through the real `edgemeta.normalize`, for the same reason
        # `Graph.add_edge` is the only chokepoint in production: a double that
        # invents its own edge shape stops being a detector. This one used to
        # accept `evidence="pkg/app.py:12"` — a string, where a real edge
        # carries an `{"path", "line"}` record — so every assertion about a
        # citation was checking the mock's shape, not the schema's, and
        # `impact.py` emitting raw dicts into its JSON and Markdown stayed green.
        record = edgemeta.normalize({"src": src, "dst": dst, "type": etype, **attrs})
        self.edges.append(record)
        self.adj.setdefault(src, []).append((dst, etype, "out", record))
        self.adj.setdefault(dst, []).append((src, etype, "in", record))

    def _is_secret_path(self, path: str) -> bool:
        p = path.replace("\\", "/").lower()
        return p == ".env" or p.startswith("secrets/") or p.endswith(".pem")


@pytest.fixture
def sample_graph_index():
    idx = MockIndex()
    # Nodes:
    # 1. pkg/core.py: public_func (lines 10-25)
    # 2. pkg/core.py: _private_helper (lines 30-40)
    # 3. pkg/app.py: run_app (calls public_func)
    # 4. tests/test_core.py: test_public_func (calls public_func)
    # 5. scripts/disconnected.py: helper (lines 1-15)

    idx.add_node("file:pkg/core.py", type="file", path="pkg/core.py")
    idx.add_node(
        "sym:pkg/core.py::public_func",
        type="symbol",
        name="public_func",
        qualname="public_func",
        kind="function",
        path="pkg/core.py",
        start_line=10,
        end_line=25,
        signature="def public_func(x: int) -> int:",
    )
    idx.add_node(
        "sym:pkg/core.py::_private_helper",
        type="symbol",
        name="_private_helper",
        qualname="_private_helper",
        kind="function",
        path="pkg/core.py",
        start_line=30,
        end_line=40,
        signature="def _private_helper():",
    )

    idx.add_node("file:pkg/app.py", type="file", path="pkg/app.py")
    idx.add_node(
        "sym:pkg/app.py::run_app",
        type="symbol",
        name="run_app",
        qualname="run_app",
        kind="function",
        path="pkg/app.py",
        start_line=5,
        end_line=15,
    )

    idx.add_node("file:tests/test_core.py", type="file", path="tests/test_core.py")
    idx.add_node(
        "sym:tests/test_core.py::test_public_func",
        type="symbol",
        name="test_public_func",
        qualname="test_public_func",
        kind="function",
        path="tests/test_core.py",
        start_line=8,
        end_line=20,
    )

    idx.add_node("file:scripts/disconnected.py", type="file", path="scripts/disconnected.py")
    idx.add_node(
        "sym:scripts/disconnected.py::helper",
        type="symbol",
        name="helper",
        qualname="helper",
        kind="function",
        path="scripts/disconnected.py",
        start_line=1,
        end_line=15,
    )

    # Edges:
    # run_app CALLS public_func
    idx.add_edge(
        "sym:pkg/app.py::run_app",
        "sym:pkg/core.py::public_func",
        "CALLS",
        confidence=1.0,
        evidence=edgemeta.evidence("pkg/app.py", 12),
    )
    # test_public_func CALLS public_func
    idx.add_edge(
        "sym:tests/test_core.py::test_public_func",
        "sym:pkg/core.py::public_func",
        "CALLS",
        confidence=1.0,
        evidence=edgemeta.evidence("tests/test_core.py", 14),
    )
    # pkg/app.py IMPORTS pkg/core.py
    idx.add_edge(
        "file:pkg/app.py",
        "file:pkg/core.py",
        "IMPORTS",
        evidence=edgemeta.evidence("pkg/app.py", 1),
    )

    return idx


def test_analyze_diff_impact_core_flow(sample_graph_index):
    # Diff modifying line 10 (signature) of public_func in pkg/core.py
    diff_text = (
        "diff --git a/pkg/core.py b/pkg/core.py\n"
        "--- a/pkg/core.py\n"
        "+++ b/pkg/core.py\n"
        "@@ -10,1 +10,1 @@\n"
        "-def public_func(x: int) -> int:\n"
        "+def public_func(x: int, y: int = 0) -> int:\n"
    )

    report = analyze_diff_impact(sample_graph_index, diff=diff_text, base="main", head="HEAD")

    assert report.base_ref == "main"
    assert report.head_ref == "HEAD"
    assert len(report.files_changed) == 1
    assert len(report.symbols_changed) == 1
    assert len(report.public_apis_affected) == 1
    assert len(report.impacted_callers) == 2  # run_app and test_public_func
    assert any(c.name == "run_app" for c in report.impacted_callers)
    assert len(report.impacted_tests) == 1  # test_public_func
    assert len(report.impacted_modules) == 1  # pkg/app.py

    changed = report.symbols_changed[0]
    assert changed.id == "sym:pkg/core.py::public_func"
    assert changed.signature_changed is True
    assert changed.is_public is True

    pub = report.public_apis_affected[0]
    assert pub.id == "sym:pkg/core.py::public_func"

    caller = report.impacted_callers[0]
    assert caller.name == "run_app"
    assert caller.evidence == "pkg/app.py:12"
    assert caller.depth == 1

    test_case = report.impacted_tests[0]
    assert test_case.test_file == "tests/test_core.py"
    assert test_case.test_name == "test_public_func"

    assert "pkg/app.py" in report.impacted_modules

    # Guardrails check
    assert "uncertainty_notice" in report.guardrails
    assert "Static analysis" in report.guardrails["uncertainty_notice"]


def test_analyze_diff_impact_disconnected_finding(sample_graph_index):
    diff_text = (
        "diff --git a/pkg/core.py b/pkg/core.py\n"
        "--- a/pkg/core.py\n"
        "+++ b/pkg/core.py\n"
        "@@ -12,1 +12,1 @@\n"
        "-    return x\n"
        "+    return x + 1\n"
        "diff --git a/scripts/disconnected.py b/scripts/disconnected.py\n"
        "--- a/scripts/disconnected.py\n"
        "+++ b/scripts/disconnected.py\n"
        "@@ -2,1 +2,1 @@\n"
        "-    pass\n"
        "+    print('detached')\n"
    )

    report = analyze_diff_impact(sample_graph_index, diff=diff_text)
    # scripts/disconnected.py has no graph connections to pkg/core.py
    disconnected = [f for f in report.suspicious_findings if f.rule_id == "R2G-IMP-001"]
    assert any(f.path == "scripts/disconnected.py" for f in disconnected)


def test_iss422_disconnected_rule_skips_files_the_graph_cannot_relate(sample_graph_index):
    """R2G-IMP-001 must not fire on a file that has no graph presence at all.

    `.github/workflows/ci.yml` has no symbols and no imports in any index ever
    built, so "no graph relationships connect it to the other changes" is true of
    it in every multi-file diff -- a finding that names an ordinary CI edit and
    can never be resolved. Same for `PR_IMPACT.md`, which the index does not
    contain at all.
    """
    diff_text = (
        "diff --git a/pkg/core.py b/pkg/core.py\n"
        "--- a/pkg/core.py\n"
        "+++ b/pkg/core.py\n"
        "@@ -12,1 +12,1 @@\n"
        "-    return x\n"
        "+    return x + 1\n"
        "diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml\n"
        "--- a/.github/workflows/ci.yml\n"
        "+++ b/.github/workflows/ci.yml\n"
        "@@ -4,1 +4,1 @@\n"
        "-  python-version: 3.11\n"
        "+  python-version: 3.12\n"
        "diff --git a/PR_IMPACT.md b/PR_IMPACT.md\n"
        "--- a/PR_IMPACT.md\n"
        "+++ b/PR_IMPACT.md\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
    )

    report = analyze_diff_impact(sample_graph_index, diff=diff_text)
    flagged = {f.path for f in report.suspicious_findings if f.rule_id == "R2G-IMP-001"}
    assert flagged == set()


def test_iss422_disconnected_rule_needs_two_relatable_files(sample_graph_index):
    """One source file plus any number of unrelatable ones is not an orphan diff.

    pkg/core.py is the only graph-relatable file in this diff, so there is
    nothing in the change it could have been connected to.
    """
    diff_text = (
        "diff --git a/pkg/core.py b/pkg/core.py\n"
        "--- a/pkg/core.py\n"
        "+++ b/pkg/core.py\n"
        "@@ -12,1 +12,1 @@\n"
        "-    return x\n"
        "+    return x + 1\n"
        "diff --git a/README.md b/README.md\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1,1 +1,1 @@\n"
        "-a\n"
        "+b\n"
    )

    report = analyze_diff_impact(sample_graph_index, diff=diff_text)
    assert [f.rule_id for f in report.suspicious_findings if f.rule_id == "R2G-IMP-001"] == []


def test_iss422_report_states_which_changed_files_the_index_is_missing(sample_graph_index):
    """An index that predates the diff produces a silent wrong answer.

    Nothing can be said about a changed file the index has never seen -- no
    caller, no test, no public API -- and the report has to say so rather than
    render zeros that read like evidence of no impact. This is the shape of an
    index built on the base ref while the diff's line numbers are head-side.
    """
    diff_text = (
        "diff --git a/pkg/core.py b/pkg/core.py\n"
        "--- a/pkg/core.py\n"
        "+++ b/pkg/core.py\n"
        "@@ -12,1 +12,1 @@\n"
        "-    return x\n"
        "+    return x + 1\n"
        "diff --git a/pkg/brand_new.py b/pkg/brand_new.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/pkg/brand_new.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+def fresh():\n"
        "+    return 1\n"
    )

    report = analyze_diff_impact(sample_graph_index, diff=diff_text)
    coverage = report.guardrails["index_coverage"]
    assert coverage["changed_files"] == 2
    assert coverage["files_absent_from_index"] == ["pkg/brand_new.py"]
    assert "pkg/core.py" not in coverage["files_absent_from_index"]
    assert "stale_index_notice" in report.guardrails

    # and the notice reaches both human surfaces, not just the JSON
    assert "Index does not cover the whole diff" in format_markdown(report)
    assert report.guardrails["stale_index_notice"] in format_pr_comment(report)


def test_iss422_no_stale_index_notice_when_the_index_covers_the_diff(sample_graph_index):
    diff_text = (
        "diff --git a/pkg/core.py b/pkg/core.py\n"
        "--- a/pkg/core.py\n"
        "+++ b/pkg/core.py\n"
        "@@ -12,1 +12,1 @@\n"
        "-    return x\n"
        "+    return x + 1\n"
    )

    report = analyze_diff_impact(sample_graph_index, diff=diff_text)
    assert report.guardrails["index_coverage"]["files_absent_from_index"] == []
    assert "stale_index_notice" not in report.guardrails
    assert "Index does not cover the whole diff" not in format_markdown(report)


def test_analyze_diff_impact_exclude_secrets(sample_graph_index):
    diff_text = (
        "diff --git a/.env b/.env\n"
        "--- a/.env\n"
        "+++ b/.env\n"
        "@@ -1,1 +1,1 @@\n"
        "-SECRET=123\n"
        "+SECRET=456\n"
        "diff --git a/pkg/core.py b/pkg/core.py\n"
        "--- a/pkg/core.py\n"
        "+++ b/pkg/core.py\n"
        "@@ -15,1 +15,1 @@\n"
        "-    return x\n"
        "+    return x * 2\n"
    )

    report = analyze_diff_impact(sample_graph_index, diff=diff_text, exclude_secrets=True)
    # .env should be filtered out
    assert len(report.files_changed) == 1
    assert all(".env" not in f.path for f in report.suspicious_findings)


@pytest.fixture
def blast_radius_index():
    """Three symbols, each reached from three separate caller files.

    `shared` is modified, `fresh` lives in a file the diff adds, and
    `make_thing` is a test helper. Only the first is a blast-radius risk.
    """
    idx = MockIndex()

    def _sym(path, name, start, end):
        idx.add_node(f"file:{path}", type="file", path=path)
        idx.add_node(
            f"sym:{path}::{name}",
            type="symbol",
            name=name,
            qualname=name,
            kind="function",
            path=path,
            start_line=start,
            end_line=end,
        )
        return f"sym:{path}::{name}"

    targets = {
        "shared": _sym("pkg/hub.py", "shared", 10, 20),
        "fresh": _sym("pkg/newmod.py", "fresh", 1, 8),
        "make_thing": _sym("tests/helpers.py", "make_thing", 5, 15),
    }
    for label, target in targets.items():
        for i, caller_path in enumerate(
            (f"pkg/{label}_a.py", f"pkg/{label}_b.py", f"pkg/{label}_c.py")
        ):
            caller = _sym(caller_path, f"use_{label}", 1, 5)
            idx.add_edge(
                caller,
                target,
                "CALLS",
                confidence=1.0,
                evidence=edgemeta.evidence(caller_path, i + 2),
            )
    return idx


def test_iss422_blast_radius_rule_ignores_added_and_test_symbols(blast_radius_index):
    """R2G-IMP-003 is about what an edit puts at risk.

    An added symbol has no pre-existing dependents -- its callers arrived in the
    same change -- so counting them reports how well new code is wired in, not
    risk. A test helper with many callers is what a suite is supposed to look
    like. Only `shared`, modified in place with three caller modules, qualifies.
    """
    diff_text = (
        "diff --git a/pkg/hub.py b/pkg/hub.py\n"
        "--- a/pkg/hub.py\n"
        "+++ b/pkg/hub.py\n"
        "@@ -12,1 +12,1 @@\n"
        "-    return 1\n"
        "+    return 2\n"
        "diff --git a/pkg/newmod.py b/pkg/newmod.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/pkg/newmod.py\n"
        "@@ -0,0 +1,8 @@\n"
        "+def fresh():\n"
        "+    return 1\n"
        "diff --git a/tests/helpers.py b/tests/helpers.py\n"
        "--- a/tests/helpers.py\n"
        "+++ b/tests/helpers.py\n"
        "@@ -7,1 +7,1 @@\n"
        "-    return {}\n"
        "+    return {'a': 1}\n"
    )

    report = analyze_diff_impact(blast_radius_index, diff=diff_text)
    blast = {(f.path, f.line) for f in report.suspicious_findings if f.rule_id == "R2G-IMP-003"}
    assert blast == {("pkg/hub.py", 10)}


@pytest.fixture
def ambiguous_caller_index():
    """One modified symbol reached by four callers the resolver could not pin.

    `confidence` on a CALLS edge is P(this is the right target | a call exists
    here), which the name resolver splits 1/n across the n symbols sharing a
    name. 0.25 is what four same-named candidates look like -- the shape a
    common method name (`start`, `run`, `get`) produces across a whole repo.
    """
    idx = MockIndex()

    def _sym(path, name, start, end):
        idx.add_node(f"file:{path}", type="file", path=path)
        idx.add_node(
            f"sym:{path}::{name}",
            type="symbol",
            name=name,
            qualname=name,
            kind="function",
            path=path,
            start_line=start,
            end_line=end,
        )
        return f"sym:{path}::{name}"

    target = _sym("pkg/server.py", "start", 40, 60)
    for i, caller_path in enumerate(
        ("pkg/unrelated_a.py", "pkg/unrelated_b.py", "pkg/unrelated_c.py", "pkg/unrelated_d.py")
    ):
        caller = _sym(caller_path, f"uses_start_{i}", 1, 5)
        idx.add_edge(
            caller,
            target,
            "CALLS",
            confidence=0.25,
            evidence=edgemeta.evidence(caller_path, i + 2),
        )
    return idx


AMBIGUOUS_DIFF = (
    "diff --git a/pkg/server.py b/pkg/server.py\n"
    "--- a/pkg/server.py\n"
    "+++ b/pkg/server.py\n"
    "@@ -42,1 +42,1 @@\n"
    "-    poll()\n"
    "+    poll(interval=0.05)\n"
)


def test_ambiguous_call_rule_reports_once_against_the_changed_symbol(ambiguous_caller_index):
    """R2G-IMP-004 is a claim about the symbol, not about each caller.

    A low confidence is the resolver saying it could not tell which same-named
    symbol a call meant. That is a fact about this analysis, so filing it against
    the *caller's* line accuses a file that has nothing to do with the diff --
    and it multiplies: changing `HTTPTransport.start` emitted 64 findings naming
    `secrets.py::_pem_spans` (which calls `match.start()`), `graph.py` and every
    other `.start()` call in the repo. Uploaded as SARIF, those became inline
    review comments on untouched code.

    One finding, anchored on the changed symbol.
    """
    report = analyze_diff_impact(ambiguous_caller_index, diff=AMBIGUOUS_DIFF)
    found = [f for f in report.suspicious_findings if f.rule_id == "R2G-IMP-004"]

    assert len(found) == 1, [f.title for f in found]
    assert (found[0].path, found[0].line) == ("pkg/server.py", 40)
    # Never the callers' files -- that was the whole defect.
    assert "unrelated" not in found[0].path


def test_blast_radius_counts_only_confidently_resolved_callers(ambiguous_caller_index):
    """R2G-IMP-003's count is its entire claim, so a guess must not inflate it.

    Four caller modules would clear the `>= 3 modules` threshold on raw count.
    All four are 0.25 name matches, which is what `.start()` on a regex match or
    a thread looks like to the resolver -- counting them reports that a method
    has a common name, not that an edit is risky.
    """
    report = analyze_diff_impact(ambiguous_caller_index, diff=AMBIGUOUS_DIFF)
    assert [f.title for f in report.suspicious_findings if f.rule_id == "R2G-IMP-003"] == []


def test_untested_public_api_rule_needs_the_graph_to_know_the_caller(sample_graph_index):
    """ "No test edge" only means "untested" if the graph sees any caller at all.

    `scripts/disconnected.py::helper` is public and has no callers of any kind,
    which is the shape of a symbol invoked from somewhere this analysis cannot
    see -- framework dispatch, an entry point, a plugin hook.
    `MCPRequestHandler.do_POST` and `.do_OPTIONS` are the real case: the stdlib
    dispatches them, so no in-repo edge points at either, and both were reported
    as untested public APIs while 64 tests in tests/test_http_transport.py drove
    them over real HTTP.

    It has to be a *public* symbol to be a detector at all: a private one never
    reaches this rule, so asserting on one would pass with the fix reverted.
    """
    diff_text = (
        "diff --git a/scripts/disconnected.py b/scripts/disconnected.py\n"
        "--- a/scripts/disconnected.py\n"
        "+++ b/scripts/disconnected.py\n"
        "@@ -5,1 +5,1 @@\n"
        "-    return 1\n"
        "+    return 2\n"
    )
    report = analyze_diff_impact(sample_graph_index, diff=diff_text)
    assert is_public_symbol({"name": "helper", "path": "scripts/disconnected.py"}) is True
    assert "helper" in {s.name for s in report.public_apis_affected}, "not a public API change"
    assert [f.title for f in report.suspicious_findings if f.rule_id == "R2G-IMP-002"] == []


# ---------------------------------------------------------------------------
# 4. Formatters Tests
# ---------------------------------------------------------------------------


def _sample_report() -> ImpactReport:
    """One fixed report, shared by the formatter assertions and the golden files.

    Hand-built, never derived from `analyze_diff_impact`, so a traversal change
    cannot move both the expectation and the subject at once.
    """
    return ImpactReport(
        base_ref="main",
        head_ref="feature",
        files_changed=[FileDiff(path="pkg/foo.py", status="modified", added_lines={10, 11})],
        symbols_changed=[
            SymbolChange(
                id="sym:pkg/foo.py::bar",
                name="bar",
                qualname="bar",
                kind="function",
                path="pkg/foo.py",
                start_line=10,
                end_line=20,
                signature="def bar():",
                is_public=True,
                signature_changed=True,
                changed_lines_count=5,
                change_type="modified",
            )
        ],
        public_apis_affected=[
            SymbolChange(
                id="sym:pkg/foo.py::bar",
                name="bar",
                qualname="bar",
                kind="function",
                path="pkg/foo.py",
                start_line=10,
                end_line=20,
                signature="def bar():",
                is_public=True,
                signature_changed=True,
                changed_lines_count=5,
                change_type="modified",
            )
        ],
        impacted_callers=[
            ImpactedCaller(
                id="sym:pkg/caller.py::use_bar",
                name="use_bar",
                qualname="use_bar",
                kind="function",
                path="pkg/caller.py",
                line=30,
                target_symbol_id="sym:pkg/foo.py::bar",
                target_symbol_name="bar",
                depth=1,
                confidence=1.0,
                evidence="pkg/caller.py:30",
                is_test=False,
            )
        ],
        impacted_modules=["pkg/caller.py"],
        impacted_tests=[
            ImpactedTest(
                test_file="tests/test_foo.py",
                test_node_id="sym:tests/test_foo.py::test_bar",
                test_name="test_bar",
                line=12,
                target_symbol_id="sym:pkg/foo.py::bar",
                target_symbol_name="bar",
                confidence=1.0,
                evidence="tests/test_foo.py:12",
            )
        ],
        dependency_paths=[
            DependencyPath(
                caller="pkg/caller.py",
                changed_symbol="sym:pkg/foo.py::bar",
                dependency="pkg/foo.py",
                path_str="pkg/caller.py -> sym:pkg/foo.py::bar -> pkg/foo.py",
            )
        ],
        suspicious_findings=[
            SuspiciousFinding(
                rule_id="R2G-IMP-002",
                category="untested_public_api",
                severity="warning",
                title="Untested public API change: `bar`",
                description="Public API bar modified without direct test coverage.",
                path="pkg/foo.py",
                line=10,
            )
        ],
        untested_public_apis=[],
        blast_radius_score=15,
        risk_level="MEDIUM",
        guardrails={"uncertainty_notice": "Static analysis cannot prove dynamic runtime breakage."},
    )


def test_formatters():
    report = _sample_report()

    # 1. JSON formatter
    json_out = format_json(report)
    parsed = json.loads(json_out)
    assert parsed["base_ref"] == "main"
    assert parsed["metrics"]["symbols_changed_count"] == 1
    assert len(parsed["symbols_changed"]) == 1

    # 2. Markdown formatter
    md_out = format_markdown(report)
    assert "Architectural PR Impact Report" in md_out
    assert "Public APIs Affected" in md_out
    assert "`bar`" in md_out
    assert "tests/test_foo.py" in md_out
    assert "Static Analysis Guardrail & Uncertainty Notice" in md_out

    # 3. PR Comment formatter
    comment_out = format_pr_comment(report)
    assert "### 📐 repo2graph Impact Analysis:" in comment_out
    assert "<details>" in comment_out
    assert "</details>" in comment_out

    # 4. SARIF formatter
    sarif_data = format_sarif(report)
    assert sarif_data["version"] == "2.1.0"
    assert sarif_data["runs"][0]["tool"]["driver"]["name"] == "repo2graph-impact"
    assert len(sarif_data["runs"][0]["results"]) == 1
    assert sarif_data["runs"][0]["results"][0]["ruleId"] == "R2G-IMP-002"


def test_iss422_pr_comment_leads_with_the_upsert_marker():
    """The CI job finds its own previous comment by this marker and edits it.

    Without the marker every push appended one more impact comment -- PR #422
    collected three before anyone noticed.
    """
    comment = format_pr_comment(_sample_report())
    assert comment.startswith(PR_COMMENT_MARKER)
    assert comment.count(PR_COMMENT_MARKER) == 1
    # the marker is invisible in rendered Markdown, so the heading still leads
    assert comment.split("\n")[1].startswith("### 📐 repo2graph Impact Analysis:")


def test_iss422_json_reports_old_path_for_renames():
    """`old_path` is in the FileDiff record and in PR_IMPACT.md's documented
    schema; it was the one field format_json dropped, so a rename rendered as an
    unrelated add."""
    report = _sample_report()
    report.files_changed = [
        FileDiff(path="pkg/new_name.py", old_path="pkg/old_name.py", status="renamed")
    ]
    entry = json.loads(format_json(report))["files_changed"][0]
    assert entry["old_path"] == "pkg/old_name.py"
    assert entry["status"] == "renamed"


# ---------------------------------------------------------------------------
# 4b. Golden output fixtures
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "impact"

# Regenerate after an intentional format change:
#   python -c "import tests.test_pr_impact as t; t.regenerate_golden_fixtures()"
GOLDEN = {
    "sample_pr_impact.md": lambda r: format_markdown(r),
    "sample_pr_impact.json": lambda r: format_json(r) + "\n",
    "sample_pr_impact.sarif": lambda r: json.dumps(format_sarif(r), indent=2) + "\n",
}


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_iss422_formatter_output_matches_the_checked_in_fixtures(name):
    """The fixtures under tests/fixtures/impact/ must be what the formatters emit.

    They shipped describing a schema no formatter ever produced -- `analyzed_at`,
    `summary` and `uncertainty_notes` keys against a formatter that emits
    `metrics` and `guardrails`, and a Markdown heading no renderer writes. An
    example output that contradicts the code is worse than none: it is the sort
    of fabricated artifact the citation rules exist to prevent, and nothing read
    it, so nothing caught it.
    """
    expected = (FIXTURE_DIR / name).read_text(encoding="utf-8")
    assert GOLDEN[name](_sample_report()) == expected, (
        f"{name} is stale; regenerate it from the formatters"
    )


def regenerate_golden_fixtures() -> None:
    """Rewrite the golden fixtures from the current formatters. Not a test."""
    report = _sample_report()
    for name, render in GOLDEN.items():
        (FIXTURE_DIR / name).write_text(render(report), encoding="utf-8", newline="\n")


def test_iss422_pr_comment_caps_the_public_api_list():
    """Every list in the PR comment is bounded: GitHub rejects a body over 65536
    characters outright, so one unbounded section makes the comment unpostable
    instead of merely long."""
    report = _sample_report()
    template = report.public_apis_affected[0]
    report.public_apis_affected = [
        SymbolChange(**{**asdict(template), "name": f"api_{i}", "qualname": f"api_{i}"})
        for i in range(PR_COMMENT_MAX_APIS + 7)
    ]

    comment = format_pr_comment(report)
    assert f"Public APIs Affected ({PR_COMMENT_MAX_APIS + 7})" in comment
    assert "`api_0`" in comment
    assert f"`api_{PR_COMMENT_MAX_APIS - 1}`" in comment
    assert f"`api_{PR_COMMENT_MAX_APIS}`" not in comment
    assert "*... and 7 more (see the full report artifact)*" in comment


# ---------------------------------------------------------------------------
# 5. MCP Tool Integration Tests
# ---------------------------------------------------------------------------


def test_mcp_tool_repo_impact(sample_graph_index):
    diff_text = (
        "diff --git a/pkg/core.py b/pkg/core.py\n"
        "--- a/pkg/core.py\n"
        "+++ b/pkg/core.py\n"
        "@@ -10,1 +10,1 @@\n"
        "-def public_func(x: int) -> int:\n"
        "+def public_func(x: int, y: int = 0) -> int:\n"
    )

    # Test markdown output (default)
    res_md = tool_repo_impact(sample_graph_index, diff=diff_text, max_depth=999)
    assert "Architectural PR Impact Report" in res_md
    assert "public_func" in res_md

    # Test json output
    res_json = tool_repo_impact(sample_graph_index, diff=diff_text, format="json")
    obj = json.loads(res_json)
    assert obj["metrics"]["public_apis_affected_count"] == 1

    # Test pr-comment output
    res_comment = tool_repo_impact(sample_graph_index, diff=diff_text, format="pr-comment")
    assert "### 📐 repo2graph Impact Analysis:" in res_comment


def test_mcp_tool_repo_impact_depth_clamping(sample_graph_index):
    diff_text = (
        "diff --git a/pkg/core.py b/pkg/core.py\n"
        "--- a/pkg/core.py\n"
        "+++ b/pkg/core.py\n"
        "@@ -12,1 +12,1 @@\n"
        "-    return x\n"
        "+    return x + 1\n"
    )

    # max_depth clamped to MCP_MAX_HOPS
    res_json = tool_repo_impact(sample_graph_index, diff=diff_text, max_depth=100, format="json")
    obj = json.loads(res_json)
    assert "metrics" in obj


@pytest.fixture
def flood_index():
    """An index wide enough that the tool's output ceiling actually binds.

    A bound asserted against a four-symbol fixture proves nothing (AGENTS.md's
    MCP rule says so explicitly), so this builds 300 files x 4 public symbols,
    each called from the next file, which is what makes the rendered report --
    symbols, callers, modules, dependency paths -- exceed the ceiling.
    """
    idx = MockIndex()
    n_files = 300
    for f in range(n_files):
        path = f"pkg/mod_{f:03d}.py"
        idx.add_node(f"file:{path}", type="file", path=path)
        for s in range(4):
            name = f"public_api_{f:03d}_{s}"
            idx.add_node(
                f"sym:{path}::{name}",
                type="symbol",
                name=name,
                qualname=name,
                kind="function",
                path=path,
                start_line=1 + s * 20,
                end_line=15 + s * 20,
                signature=f"def {name}(a, b, c)",
            )
    for f in range(n_files):
        path = f"pkg/mod_{f:03d}.py"
        caller_path = f"pkg/mod_{(f + 1) % n_files:03d}.py"
        for s in range(4):
            idx.add_edge(
                f"sym:{caller_path}::public_api_{(f + 1) % n_files:03d}_{s}",
                f"sym:{path}::public_api_{f:03d}_{s}",
                "CALLS",
                confidence=1.0,
                evidence={"path": caller_path, "line": 3 + s},
            )
    return idx


def _flood_diff(index) -> str:
    """A diff touching every indexed path, with a wide added-line range per file."""
    paths = sorted({n["path"] for n in index.nodes.values() if n.get("path")})
    parts = []
    for p in paths:
        parts.append(f"diff --git a/{p} b/{p}\n--- a/{p}\n+++ b/{p}\n@@ -1,120 +1,120 @@\n")
        parts.extend(f"+line {i}\n" for i in range(120))
    return "".join(parts)


_IMPACT_RENDERERS = {
    "json": format_json,
    "markdown": format_markdown,
    "pr-comment": format_pr_comment,
}


# Only the two formats that can actually exceed the ceiling. `pr-comment` is
# deliberately NOT parametrized here: it caps every list (PR_COMMENT_MAX_APIS)
# because GitHub rejects a comment body over 65536 chars, so it renders 594 tokens
# on this fixture against a 12000 ceiling and *no* fixture size will make it
# truncate. Including it read as a third truncation case while asserting nothing
# about truncation; its real contract is the separate test below.
@pytest.mark.parametrize("fmt", ["markdown", "json"])
def test_mcp_tool_repo_impact_output_is_bounded(flood_index, fmt):
    """Clamping the *input* does not bound the *output*.

    `max_depth` was clamped from the start, but the rendered report grows with
    the number of impacted symbols, and `diff` is accepted up to 1 MB. A diff
    naming every indexed path rendered ~17.9k tokens as JSON -- 1.5x the ceiling
    `repo_search` enforces on itself -- straight into an agent's context window.
    """
    from repo2graph.mcp import MCP_MAX_BUDGET_TOKENS
    from repo2graph.query import count_tokens

    diff_text = _flood_diff(flood_index)
    report = analyze_diff_impact(flood_index, diff_text, base="main", head="HEAD", max_depth=2)

    # Guard the guard: if the fixture stopped being wide enough to breach the
    # ceiling, every assertion below would pass vacuously.
    untruncated = count_tokens(_IMPACT_RENDERERS[fmt](report))
    assert untruncated > MCP_MAX_BUDGET_TOKENS, (
        f"fixture renders only {untruncated} {fmt} tokens, under the "
        f"{MCP_MAX_BUDGET_TOKENS} ceiling -- this test would prove nothing"
    )

    out = tool_repo_impact(flood_index, diff=diff_text, format=fmt)
    assert count_tokens(out) <= MCP_MAX_BUDGET_TOKENS, (
        f"{fmt} output was {count_tokens(out)} tokens, over the {MCP_MAX_BUDGET_TOKENS} ceiling"
    )

    # Truncation must not destroy the format's own contract: `json` stays
    # parseable (a line-boundary cut would not), and markdown says it was cut.
    if fmt == "json":
        obj = json.loads(out)
        assert obj["truncated"] is True
        assert obj["metrics"]["symbols_changed_count"] > 0
        assert obj["risk_level"] and obj["blast_radius_score"] > 0
    else:
        assert "truncated" in out


def test_mcp_tool_repo_impact_pr_comment_is_self_bounding_and_never_truncated(flood_index):
    """`pr-comment` must come back whole, because it bounds itself.

    It caps every list for GitHub's 65536-char comment limit, so it stays far
    under the tool ceiling even on a diff touching every indexed path. The
    contract is therefore the opposite of the test above: not "gets truncated"
    but "is returned byte-identically, with no truncation notice bolted on".
    """
    from repo2graph.mcp import MCP_MAX_BUDGET_TOKENS
    from repo2graph.query import count_tokens

    diff_text = _flood_diff(flood_index)
    report = analyze_diff_impact(flood_index, diff_text, base="main", head="HEAD", max_depth=2)

    out = tool_repo_impact(flood_index, diff=diff_text, format="pr-comment")
    assert count_tokens(out) <= MCP_MAX_BUDGET_TOKENS
    assert out == format_pr_comment(report), "pr-comment was altered despite fitting the ceiling"
    assert "truncated" not in out
    # The self-bounding it relies on, pinned so a renderer change that drops the
    # cap fails here rather than silently starting to truncate.
    assert len(report.public_apis_affected) > PR_COMMENT_MAX_APIS
    assert out.count("\n- `") <= PR_COMMENT_MAX_APIS * 4


def test_mcp_tool_repo_impact_truncation_notice_never_breaks_its_own_ceiling(
    sample_graph_index, monkeypatch
):
    """The notice is appended, so its own length must be paid for in advance.

    `count_tokens` is a floor division and therefore not additive: `_fit_lines`
    may return up to `4 * room + 3` characters, so simply subtracting the notice's
    token count still let the sum land one token over -- 12001 against a 12000
    bound, while the code claimed that could not happen. Swept across line widths
    and counts because the overrun depends on `len(text) % 4`.

    Uses the small fixture, not `flood_index`: the renderer is stubbed, so the
    report's own size is irrelevant to what is under test, and 16 real
    `analyze_diff_impact` runs over 300 files cost ~17s for nothing.
    """
    import repo2graph.impact as impact_mod

    from repo2graph.mcp import MCP_MAX_BUDGET_TOKENS
    from repo2graph.query import count_tokens

    diff_text = (
        "diff --git a/pkg/core.py b/pkg/core.py\n"
        "--- a/pkg/core.py\n"
        "+++ b/pkg/core.py\n"
        "@@ -10,1 +10,1 @@\n"
        "-def public_func(x: int) -> int:\n"
        "+def public_func(x: int, y: int = 0) -> int:\n"
    )
    # Line *width* is what varies `len(kept) % 4`, which is where the overrun
    # lived -- so the sweep walks four consecutive widths (covering all four
    # residues) rather than four line counts. Wide lines keep the test fast:
    # `_fit_lines` rebuilds its candidate string once per line it keeps, so 240
    # wide lines cost milliseconds where 24,000 single-character lines cost ~1s
    # per call. 400 lines x ~200 chars is ~80KB, comfortably over the 48KB the
    # 12,000-token ceiling allows, so truncation really is reached.
    over = []
    for width in (199, 200, 201, 202):
        monkeypatch.setattr(
            impact_mod,
            "format_markdown",
            lambda _r, _w=width: "\n".join("x" * _w for _ in range(400)),
        )
        out = tool_repo_impact(sample_graph_index, diff=diff_text, format="markdown")
        assert "truncated" in out, f"width={width} did not reach the truncation path"
        if count_tokens(out) > MCP_MAX_BUDGET_TOKENS:
            over.append((width, count_tokens(out)))
    assert over == [], f"truncated output exceeded the ceiling it announces: {over}"


def test_mcp_tool_repo_impact_git_failure_does_not_leak_host_paths(sample_graph_index, monkeypatch):
    """`git -C <root>` names <root> in its stderr; that must not be relayed.

    Every other served route goes out of its way to keep the absolute index path
    server-side (`http_server.INDEX_UNAVAILABLE`, `_public_repo_label`). Relaying
    `get_git_diff`'s exception text handed it back over MCP instead.

    `get_git_diff` is stubbed with the exact RuntimeError shape it really raises
    (it embeds git's stderr, and `git -C <root>` names <root> in its own "cannot
    change to" message). The behaviour under test is whether `tool_repo_impact`
    *relays* that text -- stubbing keeps the test off git's message wording,
    which differs by git version and platform, and out of the `Path.cwd()`
    fallback that would otherwise find this repo's own real `.git`.
    """
    secret = r"C:\Users\victim\private-monorepo"

    def _boom(*_a, **_kw):
        raise RuntimeError(
            f"git diff failed with code 128: fatal: cannot change to '{secret}': "
            f"No such file or directory"
        )

    monkeypatch.setattr("repo2graph.impact.get_git_diff", _boom)

    out = tool_repo_impact(sample_graph_index, base="main", head="HEAD", diff="")

    assert "Error obtaining git diff" in out
    assert "victim" not in out, f"host path leaked: {out!r}"
    assert "private-monorepo" not in out, f"host path leaked: {out!r}"
    assert secret not in out
    # The caller's own refs are their input, so echoing them discloses nothing,
    # and the exception *type* is kept so "bad ref" stays distinguishable from
    # "not a repository".
    assert "main...HEAD" in out
    assert "RuntimeError" in out


# ---------------------------------------------------------------------------
# 6. CLI Command Integration Tests
# ---------------------------------------------------------------------------


def test_cli_cmd_impact(tmp_path, capsys):
    import argparse
    from repo2graph.cli import cmd_impact

    # Write minimal artifacts in tmp_path (artifacts live in agent/)
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "chunks.jsonl").write_text("", encoding="utf-8")
    (agent_dir / "manifest.json").write_text("{}", encoding="utf-8")
    (agent_dir / "nodes.jsonl").write_text(
        json.dumps(
            {
                "id": "sym:pkg/foo.py::bar",
                "type": "symbol",
                "name": "bar",
                "qualname": "bar",
                "kind": "function",
                "path": "pkg/foo.py",
                "start_line": 10,
                "end_line": 20,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (agent_dir / "edges.jsonl").write_text("", encoding="utf-8")

    diff_path = tmp_path / "test.diff"
    diff_path.write_text(
        "diff --git a/pkg/foo.py b/pkg/foo.py\n"
        "--- a/pkg/foo.py\n"
        "+++ b/pkg/foo.py\n"
        "@@ -10,1 +10,1 @@\n"
        "-def bar():\n"
        "+def bar(x: int = 1):\n",
        encoding="utf-8",
    )

    out_json = tmp_path / "report.json"
    args = argparse.Namespace(
        out=str(tmp_path),
        repo=str(tmp_path),
        base="main",
        head="HEAD",
        diff=str(diff_path),
        format="json",
        json=True,
        sarif=False,
        max_depth=2,
        min_confidence=0.0,
        write=str(out_json),
    )

    code = cmd_impact(args)
    assert code == 0
    assert out_json.exists()
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["metrics"]["symbols_changed_count"] == 1
    assert data["metrics"]["public_apis_affected_count"] == 1


def _impact_args(tmp_path, diff_path, out_json, **over):
    import argparse

    base = dict(
        out=str(tmp_path / "ix"),
        repo=str(tmp_path / "src"),
        base="main",
        head="HEAD",
        diff=str(diff_path),
        format="json",
        json=True,
        sarif=False,
        max_depth=2,
        min_confidence=0.0,
        write=str(out_json),
    )
    base.update(over)
    return argparse.Namespace(**base)


def _tiny_repo(tmp_path):
    """A source tree to build an index *from*, plus a diff against it."""
    src = tmp_path / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "core.py").write_text(
        "CONSTANT = 1\n\n\ndef public_func(x):\n    return x + CONSTANT\n",
        encoding="utf-8",
    )
    diff_path = tmp_path / "t.diff"
    diff_path.write_text(
        "diff --git a/pkg/core.py b/pkg/core.py\n"
        "--- a/pkg/core.py\n"
        "+++ b/pkg/core.py\n"
        "@@ -4,1 +4,1 @@\n"
        "-def public_func(x):\n"
        "+def public_func(x, y=0):\n",
        encoding="utf-8",
    )
    return diff_path


def test_impact_builds_a_missing_index_by_default(tmp_path):
    """`--no-auto-build` only means something if building is the default.

    The flag was declared with `dest="auto_build"` but `args.auto_build` was
    never read, so `cmd_impact` always went straight to `_require_index` and a
    first run died with "no index at .r2g" — the documented default behaviour
    could not happen. Asserted through the report, not just an exit code: a
    build that produced no graph would still return 0.
    """
    from repo2graph.cli import cmd_impact

    diff_path = _tiny_repo(tmp_path)
    out_json = tmp_path / "report.json"

    code = cmd_impact(_impact_args(tmp_path, diff_path, out_json, auto_build=True))

    assert code == 0
    assert (tmp_path / "ix").is_dir(), "auto-build wrote no index directory"
    data = json.loads(out_json.read_text(encoding="utf-8"))
    # public_func spans the changed line 4, so the freshly built index resolved
    # the diff to a real symbol rather than to an empty graph.
    assert [s["qualname"] for s in data["symbols_changed"]] == ["public_func"]
    assert [s["qualname"] for s in data["public_apis_affected"]] == ["public_func"]


def test_impact_reads_a_diff_from_stdin(tmp_path, monkeypatch):
    """`--diff -` is the piped form PR_IMPACT.md documents.

    It was documented in two places, including a copy-pasteable
    `git diff main...HEAD | repo2graph impact --diff -`, but `-` went down the
    file branch and exited with "diff file - does not exist". Bytes, not text:
    a piped diff on a cp1252 Windows stdin must not raise before parsing.
    """
    import io

    from repo2graph.cli import cmd_impact

    diff_path = _tiny_repo(tmp_path)
    piped = diff_path.read_bytes()
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(piped), encoding="utf-8"))

    out_json = tmp_path / "report.json"
    code = cmd_impact(_impact_args(tmp_path, "-", out_json, auto_build=True))

    assert code == 0
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert [f["path"] for f in data["files_changed"]] == ["pkg/core.py"]
    assert [s["qualname"] for s in data["symbols_changed"]] == ["public_func"]


def test_impact_no_auto_build_still_refuses_a_missing_index(tmp_path):
    """`--no-auto-build` keeps the old contract: name the command that fixes it."""
    from repo2graph.cli import cmd_impact

    diff_path = _tiny_repo(tmp_path)
    args = _impact_args(tmp_path, diff_path, tmp_path / "r.json", auto_build=False)

    with pytest.raises(SystemExit) as exc:
        cmd_impact(args)

    assert "no index at" in str(exc.value)
    assert not (tmp_path / "ix").exists(), "--no-auto-build built an index anyway"


# ---------------------------------------------------------------------------
# 7. CI workflow wiring
#
# Stdlib text assertions, no PyYAML: it is not a dependency of this project,
# runtime or dev (same rule as tests/test_prod_igy.py).
# ---------------------------------------------------------------------------

WORKFLOW_PATH = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "pr-impact.yml"


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_iss422_workflow_builds_the_index_on_head_not_on_the_base_ref():
    """The analyzed index must come from the commit the diff's line numbers
    belong to.

    `parse_unified_diff` records added lines in *new file* coordinates, and
    `analyze_diff_impact` compares them against `start_line`/`end_line` read out
    of the index. An index built on the base ref therefore matches head-side line
    numbers against base-side symbol spans, and cannot contain a file the PR
    adds at all -- silently, with a green run and plausible-looking numbers.
    """
    text = _workflow_text()
    assert "repo2graph build . -o .repo2graph-head" in text
    assert "-i .repo2graph-head" in text
    # the old recipe: check out the base ref, index it, check the head back out
    assert "git checkout" not in text
    assert ".repo2graph-base" not in text


def test_iss422_workflow_upserts_one_comment_using_the_module_marker():
    """One comment per PR, edited in place. PR #422 collected three identical
    impact comments in four minutes because every run called `gh pr comment`.

    The marker is duplicated by necessity -- Python writes it, shell greps for
    it -- so the two spellings are pinned against each other here.
    """
    text = _workflow_text()
    assert PR_COMMENT_MARKER in text
    assert "-X PATCH" in text
    assert "issues/comments/$existing" in text
    # never the append-only path
    assert "gh pr comment" not in text


def test_iss422_workflow_posts_as_prod_igy():
    """The impact comment is prod-igy's, not github-actions[bot]'s, and the
    minted token is scoped to what the comment step actually does rather than to
    the App installation's whole grant."""
    text = _workflow_text()
    assert "actions/create-github-app-token@" in text
    assert "app-id: ${{ secrets.PRODIGY_APP_ID }}" in text
    assert "private-key: ${{ secrets.PRODIGY_PRIVATE_KEY }}" in text
    assert "GH_TOKEN: ${{ steps.app-token.outputs.token || secrets.GITHUB_TOKEN }}" in text

    # Exactly what the comment step does, and nothing else. `permission-*`
    # requests that exact set: the API answers 422 "The permissions requested are
    # not granted to this installation" if any one entry is absent from the
    # installation's grant, so a well-meant extra mints nothing at all and the
    # comment silently posts as github-actions[bot] instead. prod-igy is
    # installed with pull_requests, contents and metadata -- not issues.
    requested = set(re.findall(r"^\s+(permission-[\w-]+: \w+)$", text, re.M))
    assert requested == {"permission-pull-requests: write"}


def test_iss422_app_key_is_never_minted_for_a_fork_pull_request():
    """This job runs `pip install .` from the PR head, so on a fork PR it has
    already executed contributor-authored build code by the time it would mint
    the App key. Both the token step and the comment step are gated on the PR
    coming from a branch of this repository.

    prod-igy.yml solves the same problem the other way -- a base-only sparse
    checkout, so PR code never runs at all -- and must keep doing so; this job
    cannot, because analyzing the PR is the point.
    """
    text = _workflow_text()
    assert (
        "SAME_REPO: ${{ github.event.pull_request.head.repo.full_name == github.repository }}"
        in text
    )
    gated = [
        line
        for line in text.split("\n")
        if "steps.app-token.outputs.token" in line or "create-github-app-token@" in line
    ]
    assert gated, "no app-token wiring found"

    # every step that mints or spends the token carries the same-repo gate
    steps = text.split("\n      - name:")
    for step in steps:
        if "create-github-app-token@" in step or "steps.app-token.outputs.token" in step:
            assert "env.SAME_REPO == 'true'" in step, step.split("\n")[0]


def test_iss422_workflow_interpolates_no_expression_into_a_run_body():
    """Issue #108's rule: a `${{ }}` inside `run:` is shell injection surface.
    Every value this job needs arrives through `env:`."""
    text = _workflow_text()
    in_run = False
    offenders = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("run:"):
            in_run = True
            if "${{" in stripped:
                offenders.append(line)
            continue
        if in_run:
            # a run: block body is indented past the key; anything at or left of
            # the `- name:`/key indentation ends it
            if stripped and not line.startswith("          "):
                in_run = False
            elif "${{" in line:
                offenders.append(line)
    assert offenders == []


# ---------------------------------------------------------------------------
# 8. PR_IMPACT.md documents the flags that actually exist
#
# Three wrong entries shipped in the options table at once: `-i` as "Required"
# when it defaults to `.r2g`, a `-w` short flag that was never defined, and
# `--diff -` for stdin, which went down the file branch and exited "diff file -
# does not exist". A reader copy-pasting the documented pipeline got an error,
# and nothing in the suite looked at the table.
# ---------------------------------------------------------------------------

PR_IMPACT_DOC = Path(__file__).resolve().parent.parent / "PR_IMPACT.md"


def _impact_help() -> str:
    """`repo2graph impact --help`, straight from the real parser.

    `main()` builds the parser inline, so the help text is the only handle on
    the argument list that does not duplicate it.
    """
    import contextlib
    import io

    from repo2graph.cli import main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), pytest.raises(SystemExit):
        main(["impact", "--help"])
    return buf.getvalue()


def _documented_flags() -> list[str]:
    """Every `-x` / `--long` token in the doc's Formats & Options table."""
    lines = PR_IMPACT_DOC.read_text(encoding="utf-8").split("\n")
    start = next(i for i, ln in enumerate(lines) if ln.startswith("### Formats & Options"))
    flags: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("###"):
            break
        if not line.startswith("|"):
            continue
        first = line.split("|")[1]
        for token in re.findall(r"`([^`]+)`", first):
            for part in token.split(","):
                part = part.strip().split(" ")[0].split("<")[0].strip()
                if part.startswith("-"):
                    flags.append(part)
    return flags


def _real_flags(help_text: str) -> set[str]:
    """Every option string argparse actually renders, as whole tokens.

    Whole tokens because substring matching is not a detector here: `"-w" in
    help_text` is satisfied by `--write`, so a phantom short flag passes a
    containment check. `(?<![\\w-])` is what stops `--write` from also yielding
    `-write`.
    """
    return set(re.findall(r"(?<![\w-])(--?[a-zA-Z][\w-]*)", help_text))


def test_pr_impact_doc_documents_only_real_flags():
    real = _real_flags(_impact_help())
    documented = _documented_flags()
    assert documented, "parsed no flags out of the Formats & Options table"

    phantom = sorted(set(documented) - real)
    assert phantom == [], f"PR_IMPACT.md documents flags the impact parser has not: {phantom}"


def test_pr_impact_doc_covers_every_impact_flag():
    """The table is the flag reference, so a new flag has to land in it."""
    # Long flags only: the table spells short aliases alongside them, and
    # `-h/--help` is argparse's own, which the table has no reason to carry.
    real = {f for f in _real_flags(_impact_help()) if f.startswith("--") and f != "--help"}
    documented = set(_documented_flags())
    undocumented = sorted(real - documented)
    assert undocumented == [], f"impact flags missing from PR_IMPACT.md: {undocumented}"


def test_get_git_diff_ref_validation_prevents_command_injection(tmp_path):
    """A leading-dash revision is a real capability, not a theoretical one.

    The argv is a list and no shell is involved, so `;` and friends are inert --
    but `git diff --output=<path>` writes a file and `--ext-cmd=<cmd>` runs a
    command, and both arrive as an ordinary positional revision. `_validate_ref`
    must reject them before `subprocess.run`.
    """
    from repo2graph.impact import _validate_ref, get_git_diff

    for bad in ("--ext-cmd=evil", "--output=/tmp/pwned", "-x", "main; rm -rf /", ".hidden", ""):
        with pytest.raises(ValueError, match="invalid git ref"):
            _validate_ref(bad)
    with pytest.raises(ValueError, match="invalid git ref"):
        get_git_diff(tmp_path, base="--output=/tmp/pwned")
    assert not (tmp_path / "pwned").exists()


# Two families an allowlist-shaped validator silently breaks:
#
#  - revision *expressions*: `git check-ref-format` rejects `HEAD~1`, `HEAD^` and
#    `main@{u}` as refnames, yet they are the ordinary way to name a diff base,
#    and `HEAD~1` is the form `.github/ISSUE_TEMPLATE/feature_request.yml` puts
#    in front of users;
#  - refnames git *does* accept that a "safe characters" class omits: `+`, `#`,
#    `=`, `,` and non-ASCII are all legal, so `--base feat+1` must not become
#    "invalid git ref" for a branch the user really has.
#
# `main..dev` is here too: an interior `..` is a two-dot range, and it is not a
# traversal risk because `get_git_diff` puts the revision before the `--`.
@pytest.mark.parametrize(
    "rev",
    [
        "HEAD",
        "HEAD~1",
        "HEAD^",
        "HEAD~2",
        "HEAD^^",
        "main@{u}",
        "@",
        "origin/main",
        "refs/heads/main",
        "release/1.0",
        "v1.0.0",
        "feat+1",
        "v1.0+build",
        "fix#123",
        "wip=2",
        "caf,e",
        "naïve",
        "main..dev",
        "main...dev",
    ],
)
def test_validate_ref_accepts_every_revision_git_itself_accepts(rev):
    from repo2graph.impact import _validate_ref

    assert _validate_ref(rev) == rev


def test_get_git_diff_returns_a_real_diff_between_two_refs(tmp_path):
    """The only test that lets `get_git_diff` reach git, and the detector for `--`.

    Everything else in this module feeds `parse_unified_diff` static text, so a
    malformed *argv* was invisible: `git diff -U0 -- main...feat` reads the
    revision as a **pathspec**, matches nothing, and exits **0** with empty
    stdout. `get_git_diff` then returns `""`, `parse_unified_diff` returns `{}`,
    and every impact report claims the PR changed no files -- with a zero exit
    code, so neither the CLI nor `.github/workflows/pr-impact.yml` notices.

    `added_lines == {2}` is hand-derived from the two writes below (line 1 is
    unchanged `def a():`, line 2 becomes `return 2`), per the AGENTS.md rule
    against asserting a value the code under test computed -- which also makes
    this the only coverage of `parse_unified_diff`'s arithmetic against a diff
    git actually produced rather than one a fixture hand-wrote.
    """
    import os
    import subprocess

    from repo2graph.impact import get_git_diff, parse_unified_diff

    if shutil.which("git") is None:
        pytest.skip("git is not on PATH")

    # Identity via env, not `git config`: no dependency on the machine's global
    # config, and nothing written outside tmp_path.
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig-absent"),
        "GIT_CONFIG_SYSTEM": str(tmp_path / "gitconfig-absent"),
    }

    def git(*args: str) -> None:
        # Bytes, never text=True -- the AGENTS.md git-decoding rule.
        proc = subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            env=env,
            timeout=60,
        )
        if proc.returncode != 0:
            pytest.skip(f"git {args[0]} failed: {proc.stderr.decode('utf8', 'replace')[:200]}")

    target = tmp_path / "f.py"
    # newline="\n" so the hunk line numbers are identical on Windows and POSIX.
    target.write_text("def a():\n    return 1\n", encoding="utf-8", newline="\n")
    git("init", "-q", ".")
    git("add", "-A")
    git("commit", "-qm", "one")
    git("branch", "-M", "main")
    git("checkout", "-q", "-b", "feat")
    target.write_text("def a():\n    return 2\n", encoding="utf-8", newline="\n")
    git("add", "-A")
    git("commit", "-qm", "two")

    # Three-dot (merge-base), two-dot, and a revision expression the validator
    # must admit -- all three reach git through a different argv branch.
    for base, head in (("main", "feat"), ("main", None), ("HEAD~1", "HEAD")):
        text = get_git_diff(tmp_path, base=base, head=head)
        assert text.strip(), f"empty diff for base={base!r} head={head!r} between differing refs"
        files = parse_unified_diff(text)
        assert set(files) == {"f.py"}, f"base={base!r} head={head!r}: {sorted(files)}"
        assert files["f.py"].added_lines == {2}, f"base={base!r} head={head!r}"

    # A user's own gitconfig must not reshape what this parses. `diff.noprefix`
    # and `diff.mnemonicPrefix` make git emit `diff --git f.py f.py` and
    # `diff --git c/f.py w/f.py`; DIFF_GIT_RE requires `a/`...` b/`, so either one
    # yields a real diff in which the parser finds nothing -- {} with exit 0.
    for setting in ("diff.noprefix", "diff.mnemonicPrefix"):
        git("config", setting, "true")
    text = get_git_diff(tmp_path, base="main", head="feat")
    assert text.startswith("diff --git a/"), f"gitconfig reshaped the header: {text[:60]!r}"
    assert set(parse_unified_diff(text)) == {"f.py"}


def test_get_git_diff_failure_paths_report_rather_than_returning_empty(tmp_path, monkeypatch):
    """The restructured error paths, which nothing else reaches.

    Three distinct branches, each of which previously could (or now must not)
    quietly produce an empty diff -- the failure mode this whole area exists to
    prevent, because an empty diff reads as "the PR changed nothing".
    """
    import subprocess as sp

    from repo2graph.impact import get_git_diff

    if shutil.which("git") is None:
        pytest.skip("git is not on PATH")

    # 1. git cannot be spawned at all -> RuntimeError, never a silent "".
    def _no_exec(*_a, **_kw):
        raise OSError("cannot spawn git")

    monkeypatch.setattr(sp, "run", _no_exec)
    with pytest.raises(RuntimeError, match="failed to run git diff"):
        get_git_diff(tmp_path, base="main")
    monkeypatch.undo()

    # 2. base-only failure: the fallback command would be identical to the
    #    primary, so it must not run again -- one error, reported once.
    with pytest.raises(RuntimeError) as exc_base:
        get_git_diff(tmp_path, base="no-such-ref-xyz")
    msg = str(exc_base.value)
    assert "git diff failed with code" in msg
    assert "fallback" not in msg, f"base-only path ran the identical fallback: {msg}"

    # 3. base+head failure: both the three-dot and two-dot errors are reported,
    #    because the three-dot one ("bad revision") is the informative one.
    with pytest.raises(RuntimeError) as exc_both:
        get_git_diff(tmp_path, base="no-such-ref-xyz", head="also-missing-xyz")
    both = str(exc_both.value)
    assert "git diff failed with code" in both
    assert "two-dot fallback also failed" in both


def test_get_git_diff_falls_back_to_two_dot_when_there_is_no_merge_base(tmp_path):
    """Unrelated histories have no merge base, so `A...B` fails and `A B` works.

    This is the fallback's reason for existing (a shallow clone is the other), and
    it is the one branch that returns a diff from the *second* subprocess.
    """
    import os
    import subprocess

    from repo2graph.impact import get_git_diff, parse_unified_diff

    if shutil.which("git") is None:
        pytest.skip("git is not on PATH")

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig-absent"),
        "GIT_CONFIG_SYSTEM": str(tmp_path / "gitconfig-absent"),
    }

    def git(*args: str) -> None:
        proc = subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            env=env,
            timeout=60,
        )
        if proc.returncode != 0:
            pytest.skip(f"git {args[0]} failed: {proc.stderr.decode('utf8', 'replace')[:200]}")

    git("init", "-q", ".")
    (tmp_path / "one.py").write_text("A = 1\n", encoding="utf-8", newline="\n")
    git("add", "-A")
    git("commit", "-qm", "one")
    git("branch", "-M", "main")
    # `--orphan` starts a branch with no common ancestor, so `main...other` has
    # no merge base and git exits non-zero on the three-dot form.
    git("checkout", "-q", "--orphan", "other")
    git("rm", "-q", "-rf", ".")
    (tmp_path / "two.py").write_text("B = 2\n", encoding="utf-8", newline="\n")
    git("add", "-A")
    git("commit", "-qm", "two")

    text = get_git_diff(tmp_path, base="main", head="other")
    assert text.strip(), "fallback returned an empty diff for unrelated histories"
    files = parse_unified_diff(text)
    assert set(files) == {"one.py", "two.py"}, sorted(files)
