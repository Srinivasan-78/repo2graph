"""Tests for PR 4: Scoped Call Resolution, Evidence Metadata, Import Aliases,
Strictness Modes, Quality Metrics, Base Subtypes, and Path Precedence (Issues #270-#277)."""

import json
from pathlib import Path
import pytest

from repo2graph import graph as graph_mod
from repo2graph.cli import main
from repo2graph.export import load_parse_cache, make_paths
from repo2graph.graph import PARALLEL_MIN_FILES, build
from repo2graph.parse import (
    BuildConfig,
    ParseError,
    explain_path,
    parse_import_details,
    parse_source,
)


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
    """`repo2graph stats` still prints raw JSON by default; `--format text` summarises.

    The default is asserted with no `--format` at all, because that is the half
    nothing pinned: the original test passed `--format text` explicitly while
    its own name claimed to be testing the default. Bare `stats` printing the
    raw `stats.json` is the pre-PR contract -- there was no `--format` flag
    before this feature -- and `test_ac27_existing_subcommands_are_untouched`
    in tests/test_rag.py depends on it, as does any caller piping it to jq.
    """
    (tmp_path / "a.py").write_text("def a(): return 1\n", encoding="utf8")
    out = tmp_path / "out"
    main(["build", str(tmp_path), "-o", str(out)])
    capsys.readouterr()

    # Default, no --format: unchanged from before the flag existed.
    main(["stats", "-o", str(out)])
    data = json.loads(capsys.readouterr().out)
    assert "files" in data
    assert "nodes" in data

    # --format text is the opt-in the flag was added for.
    main(["stats", "-o", str(out), "--format", "text"])
    out_text = capsys.readouterr().out
    assert "repo2graph Index Quality & Coverage Summary" in out_text
    assert "Files Discovered:" in out_text
    assert "Call Resolution Quality:" in out_text

    # Both JSON spellings agree.
    main(["stats", "-o", str(out), "--json"])
    assert "nodes" in json.loads(capsys.readouterr().out)

    main(["stats", "-o", str(out), "--format", "json"])
    assert "nodes" in json.loads(capsys.readouterr().out)


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


# ==============================================================================
# Regression tests for the PR #325 review findings.
#
# Each of these was written against a reproduced failure, so each is a detector:
# reverting its fix turns exactly this test red. Edge assertions are literal
# `(src, dst, kind)` tuples hand-derived from the fixture above them -- never a
# value the code under test computed (AGENTS.md).
# ==============================================================================


def test_self_recursive_call_resolves_to_itself(tmp_path: Path):
    """A top-level function calling its own name recurses; it does not bind elsewhere.

    Every tier filtered the calling symbol out with `c != sid`, so `helper`'s
    recursive call was handed to the same-named *method* at confidence 1.0 and
    the recursion edge disappeared: one edge, confidently wrong.
    """
    (tmp_path / "m.py").write_text(
        "class X:\n"
        "    def helper(self):\n"
        "        return 0\n"
        "\n"
        "def helper(n):\n"
        "    return helper(n - 1)\n",
        encoding="utf8",
    )

    g = build(tmp_path)
    got = {
        (e["src"], e["dst"], e["resolution_kind"])
        for e in g.edges
        if e["type"] == "CALLS" and e["src"] == "sym:m.py::helper"
    }
    assert got == {("sym:m.py::helper", "sym:m.py::helper", "self_recursive")}

    edge = next(e for e in g.edges if e["type"] == "CALLS" and e["src"] == "sym:m.py::helper")
    assert edge["confidence"] == 1.0
    assert edge["scope_distance"] == 0


def test_unambiguous_method_recursion_keeps_its_self_edge(tmp_path: Path):
    """A method whose name is unique in its file recurses onto itself, confidently."""
    (tmp_path / "p.py").write_text(
        "class Z:\n    def only(self, n):\n        return self.only(n - 1)\n",
        encoding="utf8",
    )

    g = build(tmp_path)
    got = {
        (e["src"], e["dst"], e["resolution_kind"], e["confidence"])
        for e in g.edges
        if e["type"] == "CALLS" and e["src"] == "sym:p.py::Z.only"
    }
    assert got == {("sym:p.py::Z.only", "sym:p.py::Z.only", "same_file", 1.0)}


def test_a_methods_own_name_is_never_bound_confidently(tmp_path: Path):
    """`self.to_dict()` and `c.to_dict()` are the same string once the receiver is gone.

    `_callee_name` keeps only the rightmost member-access segment, so inside
    `Report.to_dict` a self-call and a call to a sibling class's identically
    named method are indistinguishable. Neither reading may be asserted at
    confidence 1.0: tier 1 declines a self-target and tier 2 -- which now
    *includes* the caller -- prices both readings at 1/n.

    This is the shape of repo2graph's own doctor.py:61,
    `[c.to_dict() for c in self.checks]`, which an earlier version of this fix
    resolved to `Report.to_dict` itself and got confidently wrong.
    """
    (tmp_path / "r.py").write_text(
        "class Item:\n"
        "    def to_dict(self):\n"
        "        return {}\n"
        "\n"
        "\n"
        "class Report:\n"
        "    def to_dict(self):\n"
        "        return {'items': [c.to_dict() for c in self.items]}\n",
        encoding="utf8",
    )

    g = build(tmp_path)
    got = {
        (e["dst"], e["resolution_kind"], e["confidence"])
        for e in g.edges
        if e["type"] == "CALLS" and e["src"] == "sym:r.py::Report.to_dict"
    }
    # Both readings are on the table, neither is claimed outright, and the true
    # target is in the set whichever reading holds.
    assert got == {
        ("sym:r.py::Item.to_dict", "same_file", 0.5),
        ("sym:r.py::Report.to_dict", "same_file", 0.5),
    }


def test_method_recursion_survives_a_same_named_sibling(tmp_path: Path):
    """Recursion is never dropped, even when the name is ambiguous in the file.

    The bug this guards: every tier excluded the caller with `c != sid`, so the
    self-edge could not be emitted at all and the call was handed wholesale to
    the sibling at confidence 1.0.
    """
    (tmp_path / "n.py").write_text(
        "def work(n):\n"
        "    return n\n"
        "\n"
        "\n"
        "class Y:\n"
        "    def work(self, n):\n"
        "        return self.work(n - 1)\n",
        encoding="utf8",
    )

    g = build(tmp_path)
    got = {
        (e["dst"], e["confidence"])
        for e in g.edges
        if e["type"] == "CALLS" and e["src"] == "sym:n.py::Y.work"
    }
    assert ("sym:n.py::Y.work", 0.5) in got, "the recursion edge must survive"
    assert got == {("sym:n.py::Y.work", 0.5), ("sym:n.py::work", 0.5)}


def test_decorator_call_counted_once(tmp_path: Path):
    """A Python decorator is one call, not two.

    `decorated_definition` and `prev_sibling` both matched the same decorator
    node -- in tree-sitter-python the function_definition's prev_sibling *is*
    the decorator -- so `count` came out 2 on every decorated def in the repo.
    """
    (tmp_path / "app.py").write_text(
        "def deco(f):\n    return f\n\n\n@deco\ndef target():\n    return 1\n",
        encoding="utf8",
    )

    g = build(tmp_path)
    edges = [e for e in g.edges if e["type"] == "CALLS" and e["src"] == "sym:app.py::target"]
    assert len(edges) == 1
    assert edges[0]["dst"] == "sym:app.py::deco"
    assert edges[0]["call_kind"] == "decorator"
    assert edges[0]["count"] == 1


def test_stacked_decorators_each_counted_once(tmp_path: Path):
    """Two decorators produce two edges of one call each, not two of two."""
    (tmp_path / "stack.py").write_text(
        "def first(f):\n    return f\n\n\n"
        "def second(f):\n    return f\n\n\n"
        "@first\n@second\ndef target():\n    return 1\n",
        encoding="utf8",
    )

    g = build(tmp_path)
    got = {
        (e["dst"], e["count"])
        for e in g.edges
        if e["type"] == "CALLS" and e["src"] == "sym:stack.py::target"
    }
    assert got == {("sym:stack.py::first", 1), ("sym:stack.py::second", 1)}


def test_dynamic_call_kind_is_language_scoped():
    """`send` is an ordinary Python method name, not a dynamic invocation.

    The heuristic matched a flat name list against the bare callee, so
    `queue.send(msg)`, `channel.send(x)` and `fn.apply(...)` were all published
    as `call_kind='dynamic'` evidence in every language.
    """
    pf = parse_source(
        b"class Q:\n"
        b"    def send(self, m):\n"
        b"        return m\n"
        b"\n"
        b"def push(q, m):\n"
        b"    q.send(m)\n"
        b"    return getattr(q, 'send')\n",
        "python",
    )
    push = next(s for s in pf.symbols if s.qualname == "push")
    kinds = {d["name"]: d["kind"] for d in push.call_details}
    assert kinds["send"] == "static"
    assert kinds["getattr"] == "dynamic"

    # ... while `apply` in JS, where it really is the dynamic-invocation idiom,
    # still reports dynamic.
    js = parse_source(b"function run(fn) {\n  return fn.apply(this, []);\n}\n", "javascript")
    run = next(s for s in js.symbols if s.qualname == "run")
    assert {d["name"]: d["kind"] for d in run.call_details}["apply"] == "dynamic"


def test_csharp_and_php_import_alias_fields():
    """C# writes `alias = target`; PHP writes `target as alias`. Both were inverted.

    `using Foo = Bar.Baz;` parsed to `module='Foo', alias='Bar'`, so a call to
    `Bar(...)` resolved at confidence 1.0 to a symbol named `Foo` while the real
    alias resolved to nothing.
    """
    (cs_alias,) = parse_import_details("using Foo = Bar.Baz;", "csharp")
    assert (cs_alias.module, cs_alias.name, cs_alias.alias) == ("Bar.Baz", "Baz", "Foo")

    (cs_plain,) = parse_import_details("using System.Text;", "csharp")
    assert (cs_plain.module, cs_plain.name, cs_plain.alias) == ("System.Text", "Text", None)

    (cs_static,) = parse_import_details("using static System.Math;", "csharp")
    assert (cs_static.module, cs_static.name, cs_static.alias) == ("System.Math", "Math", None)

    (php_alias,) = parse_import_details("use Foo\\Bar as Baz;", "php")
    assert (php_alias.module, php_alias.name, php_alias.alias) == ("Foo\\Bar", "Bar", "Baz")

    (php_plain,) = parse_import_details("use Foo\\Bar;", "php")
    assert (php_plain.module, php_plain.name, php_plain.alias) == ("Foo\\Bar", "Bar", None)


def test_long_named_import_still_yields_details():
    """Import details parse the full text, not the 300-char artifact cap.

    `imports` is capped for artifact size and `parse_import_details` consumed
    that capped string, so a barrel import lost its closing brace and its
    `from "./mod"` and produced zero details -- every binding invisible to
    tier-3 resolution, with nothing recording the loss.
    """
    names = ", ".join(f"name{i:03d}" for i in range(40))
    src = f'import {{ {names} }} from "./mod";\n'
    assert len(src) > 300, "fixture must exceed the truncation cap to be a detector"

    pf = parse_source(src.encode("utf8"), "typescript")
    got = {d.name for d in pf.import_details}
    assert "name000" in got
    assert "name039" in got
    assert {d.module for d in pf.import_details} == {"./mod"}

    # The artifact cap itself is deliberate and stays.
    assert len(pf.imports[0]) == 300


def test_inherits_edges_do_not_duplicate_their_own_dst(tmp_path: Path):
    """`resolved_target` was a verbatim copy of the edge's `dst`, in every export."""
    (tmp_path / "h.py").write_text(
        "class Base:\n    pass\n\n\nclass Child(Base):\n    pass\n",
        encoding="utf8",
    )

    g = build(tmp_path)
    inherits = [e for e in g.edges if e["type"] == "INHERITS"]
    assert inherits, "fixture must produce an INHERITS edge to be a detector"
    assert all("resolved_target" not in e for e in inherits)
    assert inherits[0]["dst"] == "sym:h.py::Base"
    assert inherits[0]["raw_base"] == "Base"


def test_explain_path_reports_a_symlink_as_non_regular(tmp_path: Path):
    """`explain-path` must answer for the link, not its target.

    It resolved the path before `lstat`, so a symlink stat'd as its regular-file
    target and was reported INCLUDED -- for a path `discover()` drops, because
    discovery lstats what it walks. Same class as commit 8ddd010.
    """
    target = tmp_path / "real.py"
    target.write_text("REAL = 1\n", encoding="utf8")
    link = tmp_path / "link.py"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not permitted on this platform")

    res = explain_path(tmp_path, link)
    assert res["included"] is False
    assert res["rule"] == "non_regular_file"

    # ... and that verdict matches what the build actually indexes.
    g = build(tmp_path)
    assert "file:real.py" in g.nodes
    assert "file:link.py" not in g.nodes


def test_cache_entry_round_trip_keeps_resolution_evidence():
    """A cache entry must carry every field call resolution reads back out of it.

    `import_details`, `call_details` and `base_details` were all missing from the
    serialised entry, so a cache hit reconstructed them empty: an aliased import
    became an external call, and every decorator call came back `static`.
    """
    src = (
        b"from lib import perform as run\n"
        b"\n"
        b"\n"
        b"class Child(Base):\n"
        b"    @deco\n"
        b"    def go(self):\n"
        b"        return run()\n"
    )
    pf = parse_source(src, "python")
    assert [d.alias for d in pf.import_details] == ["run"], "fixture must carry an alias"

    restored = graph_mod.entry_read(graph_mod.cache_entry("python", len(src), 7, pf, "d" * 64))
    assert restored is not None
    _, _, pf2, _ = restored
    assert pf2 is not None
    assert [(d.module, d.name, d.alias) for d in pf2.import_details] == [("lib", "perform", "run")]
    assert pf2.symbols == pf.symbols
    assert pf2 == pf


def test_a_cache_without_resolution_evidence_is_refused(tmp_path: Path):
    """A cache written before this PR must be rejected, not half-read.

    Its entries carry no `import_details`, `call_details` or `base_details`, so
    every cached symbol would come back with `call_kind='static'` and every
    aliased import unresolved. `PARSE_CACHE_FORMAT` is the only thing standing
    between such a cache and a silently wrong incremental build, which is why
    the constant has to move whenever an entry's shape does.
    """
    (tmp_path / "a.py").write_text(
        "TABLE = {'a': 1}\n\n\ndef f():\n    return TABLE\n", encoding="utf8"
    )
    out = tmp_path / "out"
    assert main(["build", str(tmp_path), "-o", str(out), "--formats", "jsonl"]) == 0
    assert load_parse_cache(out), "the cache this build just wrote must be usable"

    cache_path = make_paths(out, "parse.cache.json")[0]
    data = json.loads(cache_path.read_text(encoding="utf8"))
    for entry in data["files"].values():
        parsed = entry.get("parsed")
        if parsed:
            parsed.pop("import_details", None)
            for sym in parsed.get("symbols", []):
                sym.pop("call_details", None)
                sym.pop("base_details", None)
    # 2 is the format that shipped exactly this shape.
    data["cache_format"] = 2
    cache_path.write_text(json.dumps(data), encoding="utf8")
    assert load_parse_cache(out) == {}


def test_strict_parse_error_is_not_retried_serially(tmp_path: Path, monkeypatch):
    """A strict-policy ParseError out of the pool is not a broken pool.

    `parse_all`'s blanket `except Exception` caught it and re-parsed every file
    serially before raising the same error, doing the whole build's parse twice
    on the way to failing.
    """
    import concurrent.futures

    files = []
    for i in range(PARALLEL_MIN_FILES):
        p = tmp_path / f"f{i}.py"
        p.write_text("X = 1\n", encoding="utf8")
        files.append((f"f{i}.py", p))

    serial_reads: list[str] = []
    real_read = graph_mod._read_and_parse

    def counting_read(item):
        serial_reads.append(item[0])
        return real_read(item)

    monkeypatch.setattr(graph_mod, "_read_and_parse", counting_read)

    class StrictFailurePool:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def map(self, fn, items, chunksize=1):
            raise ParseError("f0.py: 3 syntax error(s) under --parse-policy strict")

    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", StrictFailurePool)

    with pytest.raises(ParseError):
        graph_mod.parse_all(files, 2)
    assert serial_reads == []
