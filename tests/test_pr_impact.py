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
from dataclasses import asdict
from pathlib import Path

import pytest

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
        record = {"src": src, "dst": dst, "type": etype, **attrs}
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
        evidence="pkg/app.py:12",
    )
    # test_public_func CALLS public_func
    idx.add_edge(
        "sym:tests/test_core.py::test_public_func",
        "sym:pkg/core.py::public_func",
        "CALLS",
        confidence=1.0,
        evidence="tests/test_core.py:14",
    )
    # pkg/app.py IMPORTS pkg/core.py
    idx.add_edge(
        "file:pkg/app.py",
        "file:pkg/core.py",
        "IMPORTS",
        evidence="pkg/app.py:1",
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
            idx.add_edge(caller, target, "CALLS", confidence=1.0, evidence=f"{caller_path}:{i + 2}")
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
