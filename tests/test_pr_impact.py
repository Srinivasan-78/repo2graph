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
from pathlib import Path

import pytest

from repo2graph.impact import (
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


# ---------------------------------------------------------------------------
# 4. Formatters Tests
# ---------------------------------------------------------------------------


def test_formatters():
    report = ImpactReport(
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
