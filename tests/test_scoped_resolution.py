"""Tests for PR 4: Scoped Call Resolution, Evidence Metadata, Import Aliases,
Strictness Modes, Quality Metrics, Base Subtypes, and Path Precedence (Issues #270-#277)."""

import json
from pathlib import Path
import pytest

from repo2graph.cli import main
from repo2graph.graph import build
from repo2graph.parse import BuildConfig, ParseError, explain_path


# ==============================================================================
# Issue 11 (#270) & Issue 12 (#271): Scoped Call Resolution & Evidence Metadata
# ==============================================================================


def test_scoped_call_resolution_tiers(tmp_path: Path):
    """Verify call resolution prefers class, file, and import scopes over global matches."""
    # File A has a class with method `save` and calls it from another method in the same class
    file_a = tmp_path / "service.py"
    file_a.write_text(
        "class DataService:\n"
        "    def save(self):\n"
        "        return 'saved'\n"
        "    def execute(self):\n"
        "        return self.save()\n"
        "\n"
        "def helper():\n"
        "    return 'local_helper'\n"
        "\n"
        "def run():\n"
        "    return helper()\n",
        encoding="utf8",
    )

    # File B also defines `save` and `helper` globally
    file_b = tmp_path / "storage.py"
    file_b.write_text(
        "def save():\n    return 'storage_saved'\n\ndef helper():\n    return 'storage_helper'\n",
        encoding="utf8",
    )

    # File C defines an unambiguous unique global function
    file_c = tmp_path / "unique.py"
    file_c.write_text(
        "def unique_action():\n    return 42\n\ndef caller():\n    return unique_action()\n",
        encoding="utf8",
    )

    g = build(tmp_path)

    # 1. Check same_class resolution: DataService.execute -> DataService.save
    exec_call_edges = [
        e
        for e in g.edges
        if e["type"] == "CALLS" and e["src"] == "sym:service.py::DataService.execute"
    ]
    assert len(exec_call_edges) == 1
    assert exec_call_edges[0]["dst"] == "sym:service.py::DataService.save"
    assert exec_call_edges[0]["resolution_kind"] == "same_class"
    assert exec_call_edges[0]["confidence"] == 1.0
    assert exec_call_edges[0]["candidate_count"] == 2  # DataService.save and storage.py:save

    # 2. Check same_file resolution: service.py:run -> service.py:helper
    run_call_edges = [
        e for e in g.edges if e["type"] == "CALLS" and e["src"] == "sym:service.py::run"
    ]
    assert len(run_call_edges) == 1
    assert run_call_edges[0]["dst"] == "sym:service.py::helper"
    assert run_call_edges[0]["resolution_kind"] == "same_file"
    assert run_call_edges[0]["confidence"] == 1.0

    # 3. Check unique_global_name resolution: unique.py:caller -> unique.py:unique_action
    unique_call_edges = [
        e for e in g.edges if e["type"] == "CALLS" and e["src"] == "sym:unique.py::caller"
    ]
    assert len(unique_call_edges) == 1
    assert unique_call_edges[0]["dst"] == "sym:unique.py::unique_action"
    assert unique_call_edges[0]["resolution_kind"] in ("same_file", "unique_global_name")
    assert unique_call_edges[0]["confidence"] == 1.0


def test_call_edges_carry_evidence_metadata(tmp_path: Path):
    """Every CALLS and CALLS_EXTERNAL edge carries resolution_kind, candidate_count, call_kind."""
    src = tmp_path / "main.py"
    src.write_text(
        "def action():\n    return 1\n\ndef run():\n    action()\n    unknown_ext_func()\n",
        encoding="utf8",
    )

    g = build(tmp_path)
    run_calls = [e for e in g.edges if e["src"] == "sym:main.py::run"]

    # Internal call
    int_call = next(e for e in run_calls if e["type"] == "CALLS")
    assert int_call["dst"] == "sym:main.py::action"
    assert "resolution_kind" in int_call
    assert "candidate_count" in int_call
    assert "call_kind" in int_call
    assert int_call["call_kind"] == "static"

    # External call
    ext_call = next(e for e in run_calls if e["type"] == "CALLS_EXTERNAL")
    assert ext_call["dst"] == "external:unknown_ext_func"
    assert ext_call["resolution_kind"] == "unresolved_external"
    assert ext_call["candidate_count"] == 0
    assert ext_call["call_kind"] == "static"


# ==============================================================================
# Issue 13 (#272): Import Parsing and Alias Resolution
# ==============================================================================


def test_import_alias_resolution(tmp_path: Path):
    """Calls through aliased imports resolve to the target symbol with resolution_kind='import_alias'."""
    (tmp_path / "lib.py").write_text(
        "def perform_calculation(x):\n    return x * 2\n",
        encoding="utf8",
    )
    (tmp_path / "client.py").write_text(
        "from lib import perform_calculation as calc\n\ndef run():\n    return calc(10)\n",
        encoding="utf8",
    )

    g = build(tmp_path)
    run_calls = [e for e in g.edges if e["type"] == "CALLS" and e["src"] == "sym:client.py::run"]
    assert len(run_calls) == 1
    assert run_calls[0]["dst"] == "sym:lib.py::perform_calculation"
    assert run_calls[0]["resolution_kind"] == "import_alias"
    assert run_calls[0]["confidence"] == 1.0


def test_import_resolution_metrics_tracked(tmp_path: Path):
    """Stats track resolved vs unresolved imports."""
    (tmp_path / "local_mod.py").write_text("def ok(): return 1\n", encoding="utf8")
    (tmp_path / "main.py").write_text(
        "import os\nimport sys\nimport local_mod\n",
        encoding="utf8",
    )

    g = build(tmp_path)
    assert g.stats["imports_resolved"] >= 1  # local_mod
    assert g.stats["imports_unresolved"] >= 2  # os, sys


# ==============================================================================
# Issue 14 (#273): Call Categories & Decorators
# ==============================================================================


def test_call_categories_and_decorators(tmp_path: Path):
    """CALLS edges distinguish call_kind: static, decorator, dynamic, possible."""
    code = (
        "def my_decorator(f):\n"
        "    return f\n"
        "\n"
        "@my_decorator\n"
        "def decorated_func():\n"
        "    return 42\n"
        "\n"
        "def dynamic_caller(obj):\n"
        "    getattr(obj, 'run')\n"
        "    return obj\n"
    )
    (tmp_path / "app.py").write_text(code, encoding="utf8")

    g = build(tmp_path)

    # Decorator call on decorated_func
    dec_calls = [
        e
        for e in g.edges
        if e["src"] == "sym:app.py::decorated_func" and e["call_kind"] == "decorator"
    ]
    assert len(dec_calls) >= 1
    assert dec_calls[0]["dst"] == "sym:app.py::my_decorator"

    # Dynamic call inside dynamic_caller
    dyn_calls = [
        e
        for e in g.edges
        if e["src"] == "sym:app.py::dynamic_caller" and e["call_kind"] == "dynamic"
    ]
    assert len(dyn_calls) >= 1


# ==============================================================================
# Issue 15 (#274): Graph-Quality Metrics and Stats Reporting
# ==============================================================================


def test_graph_quality_metrics_in_stats_and_manifest(tmp_path: Path):
    """Quality metrics are populated in g.stats, stats.json, and manifest.json."""
    (tmp_path / "a.py").write_text("def f(): return 1\n", encoding="utf8")
    (tmp_path / "b.py").write_text("from a import f\ndef g(): return f()\n", encoding="utf8")

    out = tmp_path / "out"
    rc = main(["build", str(tmp_path), "-o", str(out), "--formats", "jsonl,overview"])
    assert rc == 0

    manifest = json.loads((out / "agent" / "manifest.json").read_text(encoding="utf8"))
    assert "quality_metrics" in manifest
    qm = manifest["quality_metrics"]
    assert qm["files_discovered"] >= 2
    assert qm["files_parsed"] >= 2
    assert qm["parse_errors"] == 0
    assert qm["calls_scoped"] >= 1

    stats = json.loads((out / "agent" / "stats.json").read_text(encoding="utf8"))
    assert "calls_scoped" in stats
    assert "imports_resolved" in stats


def test_cli_stats_command_human_readable_and_json(tmp_path: Path, capsys):
    """`repo2graph stats` prints formatted summary by default and raw JSON with --json."""
    (tmp_path / "a.py").write_text("def a(): return 1\n", encoding="utf8")
    out = tmp_path / "out"
    main(["build", str(tmp_path), "-o", str(out)])
    capsys.readouterr()

    # Default: human-readable summary
    main(["stats", "-o", str(out), "--format", "text"])
    out_text = capsys.readouterr().out
    assert "repo2graph Index Quality & Coverage Summary" in out_text
    assert "Files Discovered:" in out_text
    assert "Call Resolution Quality:" in out_text

    # JSON output
    main(["stats", "-o", str(out), "--json"])
    out_json = capsys.readouterr().out
    data = json.loads(out_json)
    assert "files" in data
    assert "nodes" in data


# ==============================================================================
# Issue 16 (#275): Parser Strictness Modes
# ==============================================================================


def test_parser_strictness_policies(tmp_path: Path):
    """Test best-effort, warn, and strict parse policies on invalid syntax."""
    bad_code = "def broken(x = ):\n    ???\n"
    (tmp_path / "broken.py").write_text(bad_code, encoding="utf8")

    # 1. best-effort: succeeds, counts parse errors
    g_best = build(tmp_path, config=BuildConfig(parse_policy="best-effort"))
    assert g_best.stats["parse_errors"] > 0
    assert g_best.stats["files_with_parse_errors"] == 1

    # 2. warn: succeeds, records errors
    g_warn = build(tmp_path, config=BuildConfig(parse_policy="warn"))
    assert g_warn.stats["parse_errors"] > 0

    # 3. strict: raises ParseError
    with pytest.raises(ParseError):
        build(tmp_path, config=BuildConfig(parse_policy="strict"))


def test_cli_parse_policy_flag_strict(tmp_path: Path):
    """CLI exits with error under --parse-policy strict when syntax error is present."""
    (tmp_path / "broken.py").write_text("def broken(x = ):\n    ???\n", encoding="utf8")
    out = tmp_path / "out"

    with pytest.raises(SystemExit) as excinfo:
        main(["build", str(tmp_path), "-o", str(out), "--parse-policy", "strict"])
    assert excinfo.value.code != 0


# ==============================================================================
# Issue 17 (#276): Inheritance / Interface Relationship Extraction
# ==============================================================================


def test_inheritance_relationship_subtypes(tmp_path: Path):
    """Java class extending and implementing interfaces produces EXTENDS and IMPLEMENTS subtypes."""
    java_code = (
        "interface Runnable {}\n"
        "interface AutoCloseable {}\n"
        "class BaseTask {}\n"
        "class Worker extends BaseTask implements Runnable, AutoCloseable {}\n"
    )
    (tmp_path / "Tasks.java").write_text(java_code, encoding="utf8")

    g = build(tmp_path)
    worker_edges = [
        e for e in g.edges if e["src"] == "sym:Tasks.java::Worker" and e["type"] == "INHERITS"
    ]
    assert len(worker_edges) == 3

    # Check subtypes
    extends_edge = next(e for e in worker_edges if e["dst"] == "sym:Tasks.java::BaseTask")
    assert extends_edge["subtype"] == "EXTENDS"

    implements_edges = [e for e in worker_edges if e["subtype"] == "IMPLEMENTS"]
    assert len(implements_edges) == 2
    impl_dests = {e["dst"] for e in implements_edges}
    assert "sym:Tasks.java::Runnable" in impl_dests
    assert "sym:Tasks.java::AutoCloseable" in impl_dests


def test_stdlib_base_types_not_falsely_linked(tmp_path: Path):
    """Common standard base names like Object or Exception do not falsely link across files."""
    (tmp_path / "models.py").write_text(
        "class MyModel(Object):\n    pass\n",
        encoding="utf8",
    )
    # Unrelated file that happens to define a function or class named Object
    (tmp_path / "parser.py").write_text(
        "class Object:\n    pass\n",
        encoding="utf8",
    )

    g = build(tmp_path)
    # MyModel in models.py does not import parser.py, so it should NOT link to parser.py:Object
    model_edges = [
        e for e in g.edges if e["src"] == "sym:models.py::MyModel" and e["type"] == "INHERITS"
    ]
    assert len(model_edges) == 0
    assert g.stats["unresolved_bases"] >= 1


# ==============================================================================
# Issue 18 (#277): Exclusion/Inclusion Precedence & Explain-Path
# ==============================================================================


def test_explain_path_precedence_rules(tmp_path: Path):
    """Test explain_path reports the exact determining rule and step."""
    # Setup files
    (tmp_path / "normal.py").write_text("print(1)\n", encoding="utf8")
    (tmp_path / ".env").write_text("KEY=secret\n", encoding="utf8")
    (tmp_path / "binary.dat").write_bytes(b"\x00\x01\x02")

    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    (node_modules / "pkg.js").write_text("console.log(1)\n", encoding="utf8")

    # 1. Normal file -> included
    res = explain_path(tmp_path, tmp_path / "normal.py")
    assert res["included"] is True
    assert res["rule"] == "included"

    # 2. Skip dir -> skip_dir (step 2)
    res_skip = explain_path(tmp_path, node_modules / "pkg.js")
    assert res_skip["included"] is False
    assert res_skip["rule"] == "skip_dir"
    assert res_skip["precedence_step"] == 2

    # 3. Secret file -> secret_file (step 7)
    res_secret = explain_path(tmp_path, tmp_path / ".env")
    assert res_secret["included"] is False
    assert res_secret["rule"] == "secret_file"
    assert res_secret["precedence_step"] == 7

    # 4. Binary file -> binary (step 10)
    res_bin = explain_path(tmp_path, tmp_path / "binary.dat")
    assert res_bin["included"] is False
    assert res_bin["rule"] == "binary"
    assert res_bin["precedence_step"] == 10

    # 5. Non-existent file -> not_found (step 1)
    res_nf = explain_path(tmp_path, tmp_path / "does_not_exist.py")
    assert res_nf["included"] is False
    assert res_nf["rule"] == "not_found"

    # 6. Include glob filter -> not_included (step 8)
    res_inc = explain_path(tmp_path, tmp_path / "normal.py", include_globs=["*.js"])
    assert res_inc["included"] is False
    assert res_inc["rule"] == "not_included"


def test_cli_explain_path_command(tmp_path: Path, capsys):
    """CLI explain-path command outputs decision in human-readable and json formats."""
    (tmp_path / "test.py").write_text("x = 1\n", encoding="utf8")

    # Text output
    rc = main(["explain-path", "test.py", "-r", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Decision:        INCLUDED" in out
    assert "Rule:            included" in out

    # JSON output
    rc_json = main(["explain-path", "test.py", "-r", str(tmp_path), "--json"])
    assert rc_json == 0
    out_json = capsys.readouterr().out
    res = json.loads(out_json)
    assert res["included"] is True
    assert res["rule"] == "included"
