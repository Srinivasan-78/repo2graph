# @authormark v1 -- do not remove (authorship watermark)⁠​‌​​‌​​‌​‌‌‌​‌​‌​​‌‌​‌​‌​‌​‌​‌‌‌​‌‌‌​‌‌​​​‌‌​‌‌​​‌‌‌​​‌​​​‌‌​‌​​​‌​​‌​‌‌​‌‌‌​‌​‌​‌​​​​‌‌​‌‌​‌​‌‌​‌​​‌​​‌​‌‌‌​​​‌​‌‌​​‌​‌​‌​‌​​​​​‌​‌​‌​‌​​‌‌​​‌​​​‌​‌‌​‌​‌​‌​​​​​‌​​‌‌‌‌​​‌‌​‌​​⁠
# Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
# Author: https://github.com/Srinivasan-78
# SPDX-License-Identifier: MIT
# Fingerprint: AMK1.pdfhGbDrl5PDge0OEgTSnS
"""End-to-end and unit coverage for graph building, chunking and retrieval."""
import re
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from repo2graph.chunks import _split, build_chunks
from repo2graph.cli import main, parse_formats
from repo2graph.graph import build, import_targets, parse_all, path_index, resolve_import
from repo2graph.layout import path as artifact_path
from repo2graph.parse import parse_source
from repo2graph.query import Index, tokenize
from repo2graph.viz import LoadedGraph, node_label, payload, select
from repo2graph.walker import discover, matches_any

REPO_ROOT = Path(__file__).resolve().parents[1]

PKG_INIT = ""
PKG_UTIL = '''
def helper(value):
    """Double a value."""
    return value * 2
'''
PKG_MAIN = '''
from .util import helper
import os


class Runner:
    """Runs things."""

    def run(self, n):
        return helper(n) + os.getpid()


def entry():
    return Runner().run(3)
'''


@pytest.fixture
def sample_repo(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(PKG_INIT)
    (pkg / "util.py").write_text(PKG_UTIL)
    (pkg / "main.py").write_text(PKG_MAIN)
    (tmp_path / "README.md").write_text("# sample\n\nA sample repository.\n")
    (tmp_path / "conf.yaml").write_text("name: sample\n")
    return tmp_path


@pytest.fixture
def sample_graph(sample_repo):
    return build(sample_repo)


def edges_of(g, etype):
    return [(e["src"], e["dst"]) for e in g.edges if e["type"] == etype]


# ---------- walker ----------

def test_discover_skips_binary_and_vendored(sample_repo):
    (sample_repo / "node_modules").mkdir()
    (sample_repo / "node_modules" / "dep.py").write_text("x = 1\n")
    (sample_repo / "blob.bin").write_bytes(b"\x00\x01\x02")
    found = {rel for rel, _ in discover(sample_repo)}
    assert "pkg/main.py" in found
    assert "node_modules/dep.py" not in found
    assert "blob.bin" not in found


def test_discover_include_exclude(sample_repo):
    only_py = {rel for rel, _ in discover(sample_repo, ["**/*.py"], None)}
    assert only_py and all(rel.endswith(".py") for rel in only_py)
    without_util = {rel for rel, _ in discover(sample_repo, None, ["**/util.py"])}
    assert "pkg/util.py" not in without_util


def test_double_star_spans_zero_directories(sample_repo):
    """'**/*.py' must also pick up top-level files; Path.match does not."""
    (sample_repo / "setup.py").write_text("x = 1\n")
    found = {rel for rel, _ in discover(sample_repo, ["**/*.py"], None)}
    assert {"setup.py", "pkg/main.py"} <= found


def test_glob_patterns():
    assert matches_any("setup.py", ["**/*.py"])
    assert matches_any("a/b/c.py", ["*.py"])          # bare pattern: any depth
    assert matches_any("a/test/x.py", ["**/test/**"])
    assert not matches_any("a/b.py", ["**/test/**"])
    assert not matches_any("src/b/c.ts", ["src/*.ts"])  # a single * stops at "/"


def test_discover_finds_non_ascii_filenames(tmp_path):
    """git ls-files escapes such paths unless asked for NUL-separated output."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "caf\u00e9.py").write_text("x = 1\n")
    assert "caf\u00e9.py" in {rel for rel, _ in discover(tmp_path)}


# ---------- parse ----------

def test_parse_extracts_symbols_calls_and_imports():
    pf = parse_source(PKG_MAIN.encode(), "python")
    kinds = {s.qualname: s.kind for s in pf.symbols}
    assert kinds["Runner"] == "class"
    assert kinds["Runner.run"] == "function"
    assert kinds["entry"] == "function"
    run = next(s for s in pf.symbols if s.qualname == "Runner.run")
    assert "helper" in run.calls
    assert any("from .util import helper" in i for i in pf.imports)
    assert pf.parse_errors == 0


def test_parse_bases_are_names_not_keywords():
    """Grammars wrap supertypes in clauses; 'extends B' is not a usable name."""
    for lang, src, expected in [
        ("python", b"class A(B, C):\n    pass\n", ["B", "C"]),
        ("java", b"class A extends B implements C, D {}\n", ["B", "C", "D"]),
        ("typescript", b"class A extends B implements C {}\n", ["B", "C"]),
        ("ruby", b"class A < B\nend\n", ["B"]),
        ("cpp", b"class A : public B {};\n", ["B"]),
        ("kotlin", b"class A : B(), C\n", ["B", "C"]),
    ]:
        sym = next(s for s in parse_source(src, lang).symbols if s.name == "A")
        assert sym.bases == expected, (lang, sym.bases)


def test_inherits_edges_for_non_python(tmp_path):
    (tmp_path / "A.java").write_text("class A extends B {}\n")
    (tmp_path / "B.java").write_text("class B {}\n")
    g = build(tmp_path)
    assert ("sym:A.java::A", "sym:B.java::B") in edges_of(g, "INHERITS")


def test_parse_records_docstring_and_parent():
    pf = parse_source(PKG_MAIN.encode(), "python")
    runner = next(s for s in pf.symbols if s.qualname == "Runner")
    assert runner.docstring.startswith("Runs things")
    assert next(s for s in pf.symbols if s.qualname == "Runner.run").parent == "Runner"


def test_parse_survives_deep_nesting():
    """The walker must not recurse; deep trees used to raise RecursionError."""
    src = ("def f():\n    return " + " + ".join(["1"] * 4000) + "\n").encode()
    pf = parse_source(src, "python")
    assert [s.qualname for s in pf.symbols] == ["f"]


def test_parse_unknown_language_is_empty():
    pf = parse_source(b"whatever", "cobol")
    assert pf.symbols == [] and pf.imports == []


# ---------- import resolution ----------

def test_import_targets_python():
    assert import_targets("from .util import helper", "python") == [".util"]
    assert import_targets("import os, sys as system", "python") == ["os", "sys"]


def test_resolve_import_relative_and_absolute():
    files = {"pkg/__init__.py", "pkg/util.py", "pkg/main.py"}
    ctx = path_index(files)
    assert resolve_import(".util", "pkg/main.py", "python", files, ctx) == "pkg/util.py"
    assert resolve_import("pkg.util", "pkg/main.py", "python", files, ctx) == "pkg/util.py"
    assert resolve_import("os", "pkg/main.py", "python", files, ctx) is None


def test_resolve_import_keeps_dot_directories():
    """A leading '.' in a real directory name must not be stripped."""
    files = {".github/scripts/deploy.py", "app.py"}
    assert resolve_import(".github.scripts.deploy", "app.py", "python", files) is None
    assert resolve_import("deploy", "app.py", "python", files) == ".github/scripts/deploy.py"


def test_resolve_import_is_deterministic_across_duplicates():
    files = {"b/util.py", "a/util.py", "main.py"}
    picks = {resolve_import("util", "main.py", "python", files) for _ in range(5)}
    assert picks == {"a/util.py"}


def test_resolve_import_javascript_relative():
    files = {"src/index.ts", "src/lib/helper.ts"}
    ctx = path_index(files)
    assert resolve_import("./lib/helper.js", "src/index.ts", "typescript", files, ctx) \
        == "src/lib/helper.ts"
    assert resolve_import("react", "src/index.ts", "typescript", files, ctx) is None


def test_resolve_import_go_uses_module_path():
    files = {"go.mod", "cmd/app/main.go", "internal/store/store.go"}
    ctx = dict(path_index(files), go_module="example.com/m")
    assert resolve_import("example.com/m/internal/store", "cmd/app/main.go", "go", files, ctx) \
        == "internal/store/store.go"
    assert resolve_import("github.com/other/pkg", "cmd/app/main.go", "go", files, ctx) is None


# ---------- graph ----------

def test_build_nodes_and_containment(sample_graph):
    ids = set(sample_graph.nodes)
    assert "file:pkg/main.py" in ids
    assert "sym:pkg/main.py::Runner.run" in ids
    assert "dir:pkg" in ids
    assert ("dir:pkg", "file:pkg/main.py") in edges_of(sample_graph, "CONTAINS")


def test_build_edges(sample_graph):
    assert ("file:pkg/main.py", "file:pkg/util.py") in edges_of(sample_graph, "IMPORTS")
    assert ("file:pkg/main.py", "module:os") in edges_of(sample_graph, "IMPORTS")
    assert ("sym:pkg/main.py::Runner", "sym:pkg/main.py::Runner.run") \
        in edges_of(sample_graph, "DEFINES")
    assert ("sym:pkg/main.py::Runner.run", "sym:pkg/util.py::helper") \
        in edges_of(sample_graph, "CALLS")


def test_build_file_types_and_stats(sample_graph):
    assert sample_graph.nodes["file:README.md"]["file_type"] == "doc"
    assert sample_graph.nodes["file:conf.yaml"]["file_type"] == "config"
    assert sample_graph.stats["parse_errors"] == 0
    assert sample_graph.stats["nodes"] == len(sample_graph.nodes)


def test_max_files_limit(sample_repo):
    assert build(sample_repo, max_files=1).stats["files"] == 1


def test_edges_are_deduplicated(sample_graph):
    keys = [(e["src"], e["dst"], e["type"]) for e in sample_graph.edges]
    assert len(keys) == len(set(keys))


def test_cochange_edges_from_git_history(tmp_path):
    run = lambda *a: subprocess.run(["git", "-C", str(tmp_path), *a], check=True,
                                    capture_output=True)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    for i in range(3):
        (tmp_path / "a.py").write_text(f"a = {i}\n")
        (tmp_path / "b.py").write_text(f"b = {i}\n")
        run("add", "-A")
        run("commit", "-qm", f"c{i}")
    g = build(tmp_path, git_history=10)
    assert ("file:a.py", "file:b.py") in edges_of(g, "CO_CHANGE")


# ---------- chunks ----------

def test_split_respects_size_and_overlaps():
    text = "\n".join(f"line {i}" for i in range(2000))
    parts = _split(text, max_chars=500)
    assert len(parts) > 1
    assert all(len(p) <= 600 for p in parts)
    assert "".join(parts) != text  # overlap duplicates lines


def test_split_terminates_on_one_huge_line():
    assert _split("x" * 10_000 + "\ny\n", max_chars=100)


def test_chunks_carry_graph_context(sample_graph):
    chunks = build_chunks(sample_graph)
    run = next(c for c in chunks if c["node_id"] == "sym:pkg/main.py::Runner.run")
    assert run["text"].startswith("# file: pkg/main.py")
    assert "pkg/util.py::helper" in run["callees"]
    assert "# calls:" in run["text"]
    assert "def run" in run["text"]


def test_chunks_cover_docs_and_residual_code(sample_graph):
    chunks = build_chunks(sample_graph)
    types = {c["type"] for c in chunks}
    assert "symbol" in types
    assert any(c["path"] == "README.md" for c in chunks)
    assert all(c["text"] for c in chunks)


def test_chunk_ids_are_unique(sample_graph):
    chunks = build_chunks(sample_graph)
    assert len({c["id"] for c in chunks}) == len(chunks)


# ---------- query ----------

def test_tokenize_splits_identifiers():
    assert set(tokenize("resolveImport build_chunks")) >= {
        "resolveimport", "resolve", "import", "build_chunks", "build", "chunks"}


def test_index_retrieves_and_expands(tmp_path, sample_repo):
    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out), "--formats", "jsonl"])
    idx = Index(out)
    hits = idx.retrieve("double a value helper", k=3, hops=1)
    assert any(h["path"] == "pkg/util.py" for h in hits)
    assert any(h["why"] != "lexical" for h in hits)  # graph expansion contributed


def test_score_matches_bruteforce(tmp_path, sample_repo):
    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out), "--formats", "jsonl"])
    idx = Index(out)
    scored = idx.score("helper runner")
    assert scored == sorted(scored, reverse=True)
    for _, i in scored:
        text = idx.chunks[i]["text"] + idx.chunks[i]["qualname"]
        assert {"helper", "runner"} & set(tokenize(text))


def test_index_survives_unicode_line_separators(tmp_path, sample_repo):
    """U+2028 is a line break for splitlines() but not for JSON; it must not
    split a chunk record in half."""
    (sample_repo / "pkg" / "sep.py").write_text(
        'MSG = "a\u2028b\u2029c\u0085d"\n\n\ndef uses_sep():\n    return MSG\n',
        encoding="utf-8",
    )
    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out), "--formats", "jsonl"])
    idx = Index(out)
    assert any(c["path"] == "pkg/sep.py" for c in idx.chunks)
    assert idx.retrieve("uses_sep", k=3)
    # ISS-50: the chunk must carry the *body* of uses_sep, not a mis-sliced
    # fragment. splitlines() breaks on U+2028/U+2029/U+0085 but tree-sitter's
    # row numbers do not, so at HEAD the chunk text is sliced from the wrong
    # lines and never contains "return MSG".
    sep_chunk = next(c for c in idx.chunks
                     if c["node_id"] == "sym:pkg/sep.py::uses_sep")
    assert "return MSG" in sep_chunk["text"]
    assert 'MSG = "a' not in sep_chunk["text"]


def test_query_without_an_index_exits_cleanly(tmp_path):
    with pytest.raises(SystemExit):
        main(["query", "anything", "-o", str(tmp_path / "missing")])
    with pytest.raises(SystemExit):
        main(["stats", "-o", str(tmp_path / "missing")])


def test_query_on_a_partial_index_exits_cleanly(tmp_path, sample_repo):
    """A build that excludes the jsonl format still writes chunks.jsonl but no
    nodes/edges. Querying that used to raise FileNotFoundError out of Index,
    because the pre-flight check only looked at chunks.jsonl."""
    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out), "--formats", "overview"])
    assert artifact_path(out, "chunks.jsonl").exists()
    assert not artifact_path(out, "nodes.jsonl").exists()
    with pytest.raises(SystemExit) as exc:
        main(["query", "anything", "-o", str(out)])
    assert "nodes.jsonl" in str(exc.value)


def test_score_unknown_term_returns_nothing(tmp_path, sample_repo):
    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out), "--formats", "jsonl"])
    assert Index(out).score("zzzznonexistentzzzz") == []


# ---------- cli / export ----------

def test_parse_formats_rejects_unknown():
    assert parse_formats("jsonl, cypher") == {"jsonl", "cypher"}
    with pytest.raises(SystemExit):
        parse_formats("jsonl,parquet")


def test_build_writes_all_artifacts(tmp_path, sample_repo, capsys):
    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out)])
    for name in ("nodes.jsonl", "edges.jsonl", "chunks.jsonl", "graph.graphml",
                 "graph.cypher", "overview.md", "stats.json"):
        assert artifact_path(out, name).exists(), name
    report = json.loads(capsys.readouterr().out)
    assert report["chunks"] > 0
    assert json.loads((artifact_path(out, "stats.json")).read_text())["files"] > 0


def test_output_is_split_into_human_and_agent_sections(tmp_path, sample_repo, capsys):
    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out)])
    assert sorted(p.name for p in (out / "human").iterdir()) == [
        "graph.graphml", "graph.html", "overview.md"]
    assert sorted(p.name for p in (out / "agent").iterdir()) == [
        "chunks.jsonl", "edges.jsonl", "graph.cypher", "manifest.json",
        "nodes.jsonl", "overview.md", "stats.json"]
    assert sorted(p.name for p in out.iterdir()) == ["agent", "human"]
    written = json.loads(capsys.readouterr().out)["written"]
    assert "agent/nodes.jsonl" in written and "human/overview.md" in written


def test_entrypoints_are_marked_and_ranked(tmp_path, sample_repo):
    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out), "--formats", "jsonl"])
    nodes = {n["id"]: n for n in
             (json.loads(l) for l in artifact_path(out, "nodes.jsonl").read_text().splitlines())}
    entry = {nid for nid, n in nodes.items() if n.get("entrypoint")}
    assert "sym:pkg/main.py::entry" in entry        # nothing in the repo calls it
    assert "sym:pkg/util.py::helper" not in entry   # Runner.run() calls it
    assert nodes["sym:pkg/main.py::entry"]["reach"] >= 1


def test_manifest_describes_the_agent_output(tmp_path, sample_repo):
    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out)])
    m = json.loads(artifact_path(out, "manifest.json").read_text())
    assert m["format"] == "repo2graph/1"
    assert "agent/chunks.jsonl" in m["written"]
    assert set(m["files"]) >= {"nodes.jsonl", "edges.jsonl", "chunks.jsonl", "manifest.json"}
    assert "CALLS" in m["edge_types"] and "symbol" in m["node_types"]
    assert m["id_grammar"]["symbol"] == "sym:<path>::<qualname>"
    assert any(e["qualname"] == "entry" for e in m["entrypoints"])
    assert m["how_to_read"] and m["approximations"]


def test_chunks_separate_in_repo_and_external_calls(tmp_path, sample_repo):
    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out), "--formats", "jsonl"])
    chunks = [json.loads(l) for l in
              artifact_path(out, "chunks.jsonl").read_text().splitlines()]
    run = next(c for c in chunks if c["qualname"] == "Runner.run")
    assert run["callees"] == ["pkg/util.py::helper"]
    assert run["callees_external"] == ["getpid"]
    assert "# calls: pkg/util.py::helper" in run["text"]
    assert "# calls (outside the repo): getpid" in run["text"]
    assert "# entry point:" in run["text"]


def test_no_chunks_flag(tmp_path, sample_repo):
    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out), "--formats", "jsonl", "--no-chunks"])
    assert not (artifact_path(out, "chunks.jsonl")).exists()


def test_cypher_output_is_quoted(tmp_path, sample_repo):
    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out), "--formats", "cypher"])
    text = (artifact_path(out, "graph.cypher")).read_text()
    assert "CREATE CONSTRAINT" in text
    assert 'MERGE (n:R2G:File {id: "file:pkg/main.py"})' in text
    assert "MERGE (a)-[:CALLS" in text


def test_graphml_is_loadable(tmp_path, sample_repo):
    nx = pytest.importorskip("networkx")
    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out), "--formats", "graphml"])
    G = nx.read_graphml(artifact_path(out, "graph.graphml"))
    assert "sym:pkg/util.py::helper" in G


def test_graphml_carries_yfiles_layout(tmp_path, sample_repo):
    pytest.importorskip("networkx")
    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out), "--formats", "graphml"])
    text = (artifact_path(out, "graph.graphml")).read_text()
    assert 'yfiles.type="nodegraphics"' in text
    assert "<y:ShapeNode>" in text
    coords = re.findall(r'<y:Geometry x="([-\d.]+)" y="([-\d.]+)"', text)
    assert len(coords) > 1
    assert len(set(coords)) == len(coords)   # no stack of boxes at the origin


def test_overview_lists_hubs(tmp_path, sample_repo):
    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out), "--formats", "overview"])
    text = (artifact_path(out, "overview.md")).read_text()
    assert "# Repo map:" in text
    assert "pkg/util.py" in text


# ---------- html map ----------

def test_build_writes_html_map(tmp_path, sample_repo):
    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out)])
    page = (artifact_path(out, "graph.html")).read_text(encoding="utf8")
    assert "<svg" in page and "__R2G_DATA__" not in page
    assert "sym:pkg/util.py::helper" in page
    assert "CALLS" in page


def test_html_map_data_is_self_contained(sample_graph):
    data = payload(sample_graph)
    assert data["nodes"] and data["edges"]
    labels = {n["id"]: n["label"] for n in data["nodes"]}
    assert labels["sym:pkg/util.py::helper"] == "helper"
    for e in data["edges"]:
        assert 0 <= e["s"] < len(data["nodes"]) and 0 <= e["t"] < len(data["nodes"])
    assert set(dict(data["nodeTypes"])) <= set(data["colors"])
    assert data["totals"]["nodes"] == len(sample_graph.nodes)


def test_viz_nodes_caps_the_drawing(tmp_path, sample_repo):
    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out), "--viz-nodes", "5"])
    page = (artifact_path(out, "graph.html")).read_text(encoding="utf8")
    data = json.loads(page.split("const DATA = ", 1)[1].split(";\nconst NS", 1)[0])
    assert len(data["nodes"]) == 5
    assert data["totals"]["nodes"] > 5


def test_select_keeps_the_best_connected_nodes(sample_graph):
    nodes, edges = select(sample_graph.nodes, sample_graph.edges, max_nodes=6)
    assert len(nodes) == 6
    kept = {n["id"] for n in nodes}
    assert all(e["src"] in kept and e["dst"] in kept for e in edges)
    assert "file:pkg/main.py" in kept  # the hub of the sample repo


def test_map_command_redraws_from_a_built_index(tmp_path, sample_repo, capsys):
    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out), "--formats", "jsonl"])
    assert not (artifact_path(out, "graph.html")).exists()
    capsys.readouterr()  # drop the build report; only the map report is asserted
    main(["map", "-o", str(out), "--viz-nodes", "4"])
    assert json.loads(capsys.readouterr().out)["nodes"] == 4
    assert "<svg" in (artifact_path(out, "graph.html")).read_text(encoding="utf8")


def test_map_command_needs_an_index(tmp_path):
    with pytest.raises(SystemExit):
        main(["map", "-o", str(tmp_path / "missing")])


def test_loaded_graph_round_trips(tmp_path, sample_repo):
    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out), "--formats", "jsonl"])
    g = LoadedGraph(out)
    assert "sym:pkg/util.py::helper" in g.nodes
    assert any(e["type"] == "CALLS" for e in g.edges)


def test_html_escapes_a_script_tag_in_the_source(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text('def f():\n    """</script><script>alert(1)</script>"""\n')
    out = tmp_path / "idx"
    main(["build", str(repo), "-o", str(out)])
    page = (artifact_path(out, "graph.html")).read_text(encoding="utf8")
    assert "</script><script>alert(1)" not in page
    assert "<\\/script>" in page


def test_node_label_truncates(sample_graph):
    long = {"id": "sym:a.py::x", "qualname": "SomeVeryLongClassName.method"}
    assert node_label(long).endswith("…") and len(node_label(long)) == 15


# ======================================================================
# Refactor loop: characterization + one regression test per in-scope bug.
# ISS ids and acceptance-criteria numbers are named in each test.
# ======================================================================

# ---------- Level 1: characterization (AC-13) ----------
# Literals generated from the HEAD build (baseline 81519d6a) of the sample_repo
# fixture. The `repo:<root.name>` id is normalised to `repo:<ROOT>` because the
# fixture root is a per-run tmp_path (Risk 1). This test must PASS at HEAD and
# keep passing through the refactor (dataclass field removal, walker
# unification, the add_cochange decode change).

CHAR_NODES = [
    "dir:pkg",
    "external:getpid",
    "file:README.md",
    "file:conf.yaml",
    "file:pkg/__init__.py",
    "file:pkg/main.py",
    "file:pkg/util.py",
    "module:os",
    "repo:<ROOT>",
    "sym:pkg/main.py::Runner",
    "sym:pkg/main.py::Runner.run",
    "sym:pkg/main.py::entry",
    "sym:pkg/util.py::helper",
]

CHAR_TRIPLES = [
    ("dir:pkg", "file:pkg/__init__.py", "CONTAINS"),
    ("dir:pkg", "file:pkg/main.py", "CONTAINS"),
    ("dir:pkg", "file:pkg/util.py", "CONTAINS"),
    ("file:pkg/main.py", "file:pkg/util.py", "IMPORTS"),
    ("file:pkg/main.py", "module:os", "IMPORTS"),
    ("file:pkg/main.py", "sym:pkg/main.py::Runner", "DEFINES"),
    ("file:pkg/main.py", "sym:pkg/main.py::entry", "DEFINES"),
    ("file:pkg/util.py", "sym:pkg/util.py::helper", "DEFINES"),
    ("repo:<ROOT>", "dir:pkg", "CONTAINS"),
    ("repo:<ROOT>", "file:README.md", "CONTAINS"),
    ("repo:<ROOT>", "file:conf.yaml", "CONTAINS"),
    ("sym:pkg/main.py::Runner", "sym:pkg/main.py::Runner.run", "DEFINES"),
    ("sym:pkg/main.py::Runner.run", "external:getpid", "CALLS_EXTERNAL"),
    ("sym:pkg/main.py::Runner.run", "sym:pkg/util.py::helper", "CALLS"),
    ("sym:pkg/main.py::entry", "sym:pkg/main.py::Runner", "CALLS"),
]

CHAR_CHUNK_IDS = [
    "file:README.md#0",
    "file:conf.yaml#0",
    "sym:pkg/main.py::Runner",
    "sym:pkg/main.py::Runner.run",
    "sym:pkg/main.py::entry",
    "sym:pkg/util.py::helper",
]


def test_refactor_preserves_graph_shape(sample_repo):
    """AC-13: node ids, (src, dst, type) triples and chunk ids are byte-identical
    before and after the refactor for the sample repo."""
    g = build(sample_repo)
    token = f"repo:{sample_repo.name}"

    def norm(s: str) -> str:
        return s.replace(token, "repo:<ROOT>")

    nodes = sorted(norm(n) for n in g.nodes)
    triples = sorted((norm(e["src"]), norm(e["dst"]), e["type"]) for e in g.edges)
    chunk_ids = sorted(norm(c["id"]) for c in build_chunks(g))

    assert nodes == CHAR_NODES
    assert triples == CHAR_TRIPLES
    assert chunk_ids == CHAR_CHUNK_IDS


# ---------- Level 2: one regression test per in-scope bug ----------

def test_iss22_symbol_chunk_body_survives_unicode_line_separator(tmp_path):
    """AC-1 (ISS-22): a file whose first line holds U+2028 must still slice each
    later symbol's chunk from the right source lines. At HEAD `splitlines()`
    splits on U+2028 while tree-sitter row numbers do not, so the body comes out
    as "\\ndef uses_sep():" and never contains "return MSG"."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sep.py").write_text(
        'MSG = "a\u2028b"\n\ndef uses_sep():\n    return MSG\n', encoding="utf-8")
    g = build(repo)
    chunk = next(c for c in build_chunks(g)
                 if c["node_id"] == "sym:sep.py::uses_sep")
    assert "return MSG" in chunk["text"]
    assert 'MSG = "a' not in chunk["text"]


def test_iss22_file_residual_excludes_symbol_body(tmp_path):
    """AC-2 (ISS-22): the residual chunk holds only lines no symbol claimed. At
    HEAD the same mis-slice pulls `return MSG` (the body of uses_sep) into the
    residual and drops part of the real residual span."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sep.py").write_text(
        'MSG = "a\u2028b"\n'
        'EXTRA = "padding padding padding padding padding padding"\n'
        '\n'
        'def uses_sep():\n'
        '    return MSG\n',
        encoding="utf-8")
    g = build(repo)
    residual = [c for c in build_chunks(g)
                if c["type"] == "file_residual" and c["path"] == "sep.py"]
    assert residual, "expected a file_residual chunk for sep.py"
    text = residual[0]["text"]
    assert "MSG = " in text
    assert "return MSG" not in text


def test_iss06_cochange_survives_non_ascii_filenames(tmp_path):
    """AC-3/AC-4 (ISS-06): two non-ASCII paths committed together three times
    must yield a CO_CHANGE edge. At HEAD `git log` runs with text=True and
    core.quotepath=true, so the paths come back quoted/locale-decoded, never
    match file_index, and the edge silently vanishes (or raises
    UnicodeDecodeError on a non-UTF-8 locale)."""
    if shutil.which("git") is None:
        pytest.skip("git not available")
    run = lambda *a: subprocess.run(["git", "-C", str(tmp_path), *a], check=True,
                                    capture_output=True)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    a, b = "café.py", "naïve.py"
    for i in range(3):
        (tmp_path / a).write_text(f"a = {i}\n", encoding="utf-8")
        (tmp_path / b).write_text(f"b = {i}\n", encoding="utf-8")
        run("add", "-A")
        run("commit", "-qm", f"c{i}")
    g = build(tmp_path, git_history=10)
    assert (f"file:{a}", f"file:{b}") in edges_of(g, "CO_CHANGE")


def test_sh1_add_cochange_splits_git_log_on_newline_only(monkeypatch):
    """REVIEW SH-1 (ISS-22 bug class, graph.py:380): add_cochange must split
    `git log` output on "\\n" only. With core.quotepath=false git emits a path
    containing a raw U+2028; str.splitlines() would cut that path in two so
    neither fragment matches file_index and the CO_CHANGE edge vanishes."""
    from repo2graph.graph import Graph, add_cochange

    sep = "\u2028"  # U+2028 LINE SEPARATOR, as a source escape not a raw code point
    a, b = f"pkg/a{sep}x.py", "pkg/b.py"
    log = "".join(f"{h}\n{a}\n{b}\n\n" for h in ("H1", "H2", "H3"))
    fake = subprocess.CompletedProcess([], 0, stdout=log.encode("utf8"), stderr=b"")
    monkeypatch.setattr("repo2graph.graph.subprocess.run", lambda *a, **k: fake)

    g = Graph(Path("."), "root")
    add_cochange(g, Path("."), 10, {a, b}, min_pairs=3)
    assert (f"file:{a}", f"file:{b}") in edges_of(g, "CO_CHANGE")


def test_iss27_graphml_roundtrips_with_a_control_char(tmp_path):
    """AC-5 (ISS-27): a C0 control char inside a docstring must not make the
    GraphML unparseable. stdlib only, never skipped. At HEAD ElementTree writes
    the raw \\x0c and ET.parse raises ParseError."""
    import xml.etree.ElementTree as ET
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(
        'def f():\n    "doc with \x0c formfeed"\n    return 1\n', encoding="utf-8")
    out = tmp_path / "idx"
    main(["build", str(repo), "-o", str(out), "--formats", "graphml"])
    gml = artifact_path(out, "graph.graphml")
    ET.parse(gml)  # must not raise
    text = gml.read_text(encoding="utf-8")
    illegal = [hex(ord(c)) for c in text
               if not (c in "\t\n\r"
                       or 0x20 <= ord(c) <= 0xD7FF
                       or 0xE000 <= ord(c) <= 0xFFFD
                       or ord(c) >= 0x10000)]
    assert not illegal, illegal


@pytest.mark.parametrize("spec", ["owner/..", "../evil", "-x/-y", "owner/"])
def test_iss19_parse_spec_rejects_traversal_and_option_specs(spec):
    """AC-6 (ISS-19): traversal / option-like specs must raise. At HEAD
    parse_spec("owner/..") returns ("owner", "..") instead of raising."""
    from repo2graph.fetch import parse_spec
    with pytest.raises(ValueError):
        parse_spec(spec)


@pytest.mark.parametrize("spec", [
    "owner/repo",
    "https://github.com/owner/repo",
    "git@github.com:owner/repo.git",
])
def test_iss19_parse_spec_still_accepts_valid_specs(spec):
    """AC-6 (ISS-19): the hardening must not reject legitimate specs."""
    from repo2graph.fetch import parse_spec
    assert parse_spec(spec) == ("owner", "repo")


class _RunRecorder:
    """Stand-in for subprocess.run that records every call and reports success."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, *args, **kwargs):
        self.calls.append((list(cmd), args, kwargs))

        class _Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Result()


def test_iss16_token_never_appears_in_clone_argv(tmp_path, monkeypatch):
    """AC-7 (ISS-16): no argv element handed to subprocess.run may contain the
    token. At HEAD the token is interpolated into the clone URL argv element."""
    from repo2graph import fetch
    rec = _RunRecorder()
    monkeypatch.setattr(fetch.subprocess, "run", rec)
    token = "s3cr3t-CLONE-token-value"
    fetch.clone("owner/repo", tmp_path, token=token)
    assert rec.calls, "subprocess.run was never called"
    for cmd, _a, _k in rec.calls:
        for part in cmd:
            assert token not in str(part), cmd


def test_iss18_every_fetch_subprocess_call_passes_timeout(tmp_path, monkeypatch):
    """AC-8 (ISS-18, SH-6): every subprocess.run in fetch.py must carry a timeout
    and specify encoding='utf8' and errors='replace'."""
    from repo2graph import fetch
    rec = _RunRecorder()
    monkeypatch.setattr(fetch.subprocess, "run", rec)
    fetch.clone("owner/repo", tmp_path, token="tok")
    fetch.head_sha(tmp_path)
    assert rec.calls, "subprocess.run was never called"
    for cmd, _a, kwargs in rec.calls:
        assert "timeout" in kwargs, cmd
        assert kwargs.get("encoding") == "utf8", cmd
        assert kwargs.get("errors") == "replace", cmd


def test_iss13_discover_matches_between_git_and_walk(tmp_path):
    """AC-9 (ISS-13, NC-2): discover() must return the same relative paths whether or
    not the tree is a git checkout. At HEAD the os.walk fallback drops every
    dot-directory while the git path keeps it, so `.github/**` appears only in a
    git checkout."""
    if shutil.which("git") is None:
        pytest.skip("git not available")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("x = 1\n")
    (tmp_path / "README.md").write_text("# hi\n")
    gh = tmp_path / ".github" / "workflows"
    gh.mkdir(parents=True)
    (gh / "ci.py").write_text("y = 2\n")

    walk_set = {rel for rel, _ in discover(tmp_path)}
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True,
                   capture_output=True)
    git_set = {rel for rel, _ in discover(tmp_path)}
    assert walk_set == git_set
    assert ".github/workflows/ci.py" in git_set
    assert "pkg/mod.py" in git_set
    assert len(git_set) >= 3


def test_iss07_parse_all_falls_back_when_the_pool_breaks(tmp_path, monkeypatch):
    """AC-10 (ISS-07, NC-1): a BrokenProcessPool must fall back to the serial path and
    return the jobs=1 result for all files without raising."""
    import concurrent.futures
    from concurrent.futures.process import BrokenProcessPool

    files = []
    for i in range(70):
        p = tmp_path / f"m{i}.py"
        p.write_text(f"def f{i}():\n    return {i}\n")
        files.append((f"m{i}.py", p))

    serial = parse_all(files, jobs=1)

    class _BoomPool:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            raise BrokenProcessPool("boom")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", _BoomPool)
    got = parse_all(files, jobs=4)

    assert len(got) == 70
    assert all(item[2] is not None for item in got)

    def digest(res):
        return [
            (rel, lang, None if read is None else (
                read[0], read[1],
                None if read[2] is None
                else [(s.qualname, s.kind) for s in read[2].symbols]))
            for rel, lang, read in res
        ]

    assert digest(got) == digest(serial)


def test_iss01_iss02_dead_dataclass_fields_are_gone():
    """AC-11 (ISS-01/ISS-02): Symbol has no start_byte/end_byte and ParsedFile
    has no file_calls. At HEAD all three fields are present."""
    import dataclasses

    from repo2graph.parse import ParsedFile, Symbol

    sym_fields = {f.name for f in dataclasses.fields(Symbol)}
    assert "start_byte" not in sym_fields
    assert "end_byte" not in sym_fields
    assert "file_calls" not in {f.name for f in dataclasses.fields(ParsedFile)}


# ---------- Level 3: workflow-YAML text assertions ----------

def _run_blocks(yaml_text: str):
    """Yield the body of every `run: |` / `run: >` block-scalar in a workflow."""
    lines = yaml_text.splitlines()
    blocks, i = [], 0
    while i < len(lines):
        m = re.match(r"^(\s*)(?:-\s+)?run:\s*[|>][-+]?\s*$", lines[i])
        if not m:
            i += 1
            continue
        indent = len(m.group(1))
        body, i = [], i + 1
        while i < len(lines) and (
                not lines[i].strip()
                or len(lines[i]) - len(lines[i].lstrip()) > indent):
            body.append(lines[i])
            i += 1
        blocks.append("\n".join(body))
    return blocks


def test_iss44_index_repo_workflow_has_no_run_interpolation():
    """AC-14 (ISS-44): no `${{ inputs. }}` or `${{ github.event. }}` inside any
    run: block of index-repo.yml; the slug is computed from "$R2G_REPO". At HEAD
    the "Compute slug" step interpolates ${{ inputs.repo }} straight into bash."""
    text = (REPO_ROOT / ".github" / "workflows" / "index-repo.yml").read_text(
        encoding="utf-8")
    for block in _run_blocks(text):
        assert "${{ inputs." not in block, block
        assert "${{ github.event." not in block, block
    assert '"$R2G_REPO"' in text


def test_iss45_ci_workflow_tests_job_covers_windows():
    """AC-15 (ISS-45): the ci.yml `tests` job runs on ubuntu and windows across
    both Python versions. At HEAD the matrix is ubuntu-latest only."""
    text = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    tests_job = text.split("\n  tests:", 1)[1].split("\n  action:", 1)[0]
    assert "windows-latest" in tests_job
    assert "ubuntu-latest" in tests_job
    assert '"3.10"' in tests_job and '"3.12"' in tests_job


def test_iss26_auth_env_terminal_prompt_and_config_count(monkeypatch):
    """Issue 26 (NC-4, NC-5): GIT_TERMINAL_PROMPT is 0 unconditionally, and
    GIT_CONFIG_COUNT preserves inherited count."""
    from repo2graph.fetch import _auth_env
    env_empty = _auth_env(None)
    assert env_empty.get("GIT_TERMINAL_PROMPT") == "0"
    assert "GIT_CONFIG_KEY_0" not in env_empty

    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "foo.bar")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "val")
    env_with_token = _auth_env("tok123")
    assert env_with_token.get("GIT_TERMINAL_PROMPT") == "0"
    assert env_with_token.get("GIT_CONFIG_COUNT") == "3"
    assert env_with_token.get("GIT_CONFIG_KEY_2") == "http.https://github.com/.extraheader"
    assert "basic" in env_with_token.get("GIT_CONFIG_VALUE_2", "")


def test_iss26_clone_redacts_base64_and_token(tmp_path, monkeypatch):
    """Issue 26 (SH-3): clone failure error message redacts both raw token and basic credential."""
    import base64
    from repo2graph import fetch
    token = "secrettoken123"
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()

    class _FailingClone:
        returncode = 128
        stdout = ""
        stderr = f"fatal: invalid config value AUTHORIZATION: basic {basic} with {token}"

    monkeypatch.setattr(fetch.subprocess, "run", lambda *a, **k: _FailingClone())
    with pytest.raises(RuntimeError) as exc:
        fetch.clone("owner/repo", tmp_path, token=token)
    msg = str(exc.value)
    assert token not in msg
    assert basic not in msg
    assert "***" in msg


def test_iss26_clone_reuses_existing_checkout(tmp_path, monkeypatch):
    """Issue 26 (ISS-21): clone detects an existing checkout and reuses it."""
    from repo2graph import fetch
    target = tmp_path / "repo"
    (target / ".git").mkdir(parents=True)
    (target / "dummy.txt").write_text("hello", encoding="utf-8")

    # Should not call subprocess git clone
    def _fail(*a, **k):
        raise AssertionError("should not run subprocess when repo exists")

    monkeypatch.setattr(fetch.subprocess, "run", _fail)
    res = fetch.clone("owner/repo", tmp_path)
    assert res == target


# ---------- Issue #28: Test coverage round 2 (ISS-52, ISS-53, NC-3) ----------

@pytest.mark.parametrize(
    "spec,expected",
    [
        ("owner/repo", ("owner", "repo")),
        ("org-name/repo-name", ("org-name", "repo-name")),
        ("a_b/c_d", ("a_b", "c_d")),
        ("https://github.com/owner/repo", ("owner", "repo")),
        ("https://github.com/owner/repo.git", ("owner", "repo")),
        ("http://github.com/owner/repo", ("owner", "repo")),
        ("http://github.com/owner/repo.git", ("owner", "repo")),
        ("https://www.github.com/owner/repo", ("owner", "repo")),
        ("git@github.com:owner/repo.git", ("owner", "repo")),
        ("git@github.com:owner/repo", ("owner", "repo")),
        ("github.com/owner/repo", ("owner", "repo")),
        ("owner/repo/", ("owner", "repo")),
    ],
)
def test_iss52_parse_spec_valid_table(spec, expected):
    """ISS-52: table-test parse_spec across all supported URL/SSH/slug formats."""
    from repo2graph.fetch import parse_spec

    assert parse_spec(spec) == expected


@pytest.mark.parametrize(
    "spec",
    [
        "",
        "   ",
        "singleword",
        "owner/",
        "/repo",
        "owner/..",
        "../repo",
        "owner/.",
        "./repo",
        "-option/repo",
        "owner/-option",
        "--repo/bar",
        "owner/repo/extra",
        "https://gitlab.com/owner/repo",
    ],
)
def test_iss52_parse_spec_invalid_table(spec):
    """ISS-52: table-test parse_spec rejection of traversal, options, and invalid URLs."""
    from repo2graph.fetch import parse_spec

    with pytest.raises(ValueError):
        parse_spec(spec)


def test_iss52_clone_argv_construction(tmp_path, monkeypatch):
    """ISS-52: clone argv construction under different options."""
    from repo2graph import fetch

    rec = _RunRecorder()
    monkeypatch.setattr(fetch.subprocess, "run", rec)

    # Default clone
    target1 = fetch.clone("owner/repo", tmp_path)
    assert target1 == tmp_path / "repo"
    assert rec.calls[-1][0] == [
        "git", "clone", "--quiet",
        "https://github.com/owner/repo.git",
        str(tmp_path / "repo"),
    ]

    # With depth and ref
    target2 = fetch.clone("owner/repo", tmp_path, ref="feat", depth=2)
    assert target2 == tmp_path / "repo"
    assert rec.calls[-1][0] == [
        "git", "clone", "--quiet",
        "--depth", "2",
        "--branch", "feat",
        "https://github.com/owner/repo.git",
        str(tmp_path / "repo"),
    ]


def test_iss53_parallel_parse_matches_serial(tmp_path):
    """ISS-53: parallel parse path (>= 64 files) produces identical node ids and
    edge triples to serial."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for i in range(70):
        prev = (i - 1) % 70
        (repo / f"mod_{i:02d}.py").write_text(
            f"from mod_{prev:02d} import f_{prev:02d}\n\n"
            f"def f_{i:02d}():\n"
            f"    return f_{prev:02d}()\n",
            encoding="utf-8",
        )
    g_serial = build(repo, jobs=1)
    g_parallel = build(repo, jobs=2)

    assert len(g_serial.nodes) >= 70
    assert sorted(g_serial.nodes.keys()) == sorted(g_parallel.nodes.keys())
    triples_serial = sorted((e["src"], e["dst"], e["type"]) for e in g_serial.edges)
    triples_parallel = sorted((e["src"], e["dst"], e["type"]) for e in g_parallel.edges)
    assert triples_serial == triples_parallel


def test_nc3_sample_repo_graphml_contains_expected_node_labels(tmp_path, sample_repo):
    """NC-3: GraphML output contains the expected node labels and definitions verbatim."""
    import xml.etree.ElementTree as ET

    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out), "--formats", "graphml"])
    gml = artifact_path(out, "graph.graphml")
    tree = ET.parse(gml)
    root = tree.getroot()
    nodes = [e for e in root.iter() if e.tag.endswith("node")]
    node_ids = {n.attrib.get("id") for n in nodes}
    assert "file:pkg/main.py" in node_ids
    assert "sym:pkg/main.py::Runner" in node_ids
    assert "sym:pkg/util.py::helper" in node_ids
    text = gml.read_text(encoding="utf-8")
    assert "Runner.run" in text
    assert "helper" in text


def test_iss25_query_constants_and_budget_bounds(tmp_path, sample_repo):
    """Issue 25 (ISS-37, ISS-38, ISS-39): BM25 constants are named, char budget
    is checked before appending to prevent overshooting, and expansion is bounded."""
    from repo2graph.query import BM25_K1, BM25_B, BM25_AVG_LEN, Index
    assert BM25_K1 == 1.5
    assert BM25_B == 0.75
    assert BM25_AVG_LEN == 400.0

    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out), "--formats", "jsonl"])
    idx = Index(out)

    # Test budget check before append: small budget should stop adding chunks
    small_budget = 250
    hits = idx.retrieve("double a value helper", k=8, hops=2, budget_chars=small_budget)
    assert len(hits) >= 1
    # If more than 1 chunk was added, the total should not exceed the budget
    if len(hits) > 1:
        total_chars = sum(len(h["text"]) for h in hits)
        assert total_chars <= small_budget

    # Test expansion bound: k=2 means max 2*k=4 hits even with ample budget
    ample_hits = idx.retrieve("double a value helper", k=2, hops=2, budget_chars=100000)
    assert len(ample_hits) <= 4


def test_iss27_skip_dirs_and_discovery_stat(tmp_path):
    """Issue 27 (ISS-15, SH-5): DEFAULT_SKIP_DIRS includes cache dirs (.ruff_cache,
    .eggs, .cache, .gradle, .direnv, .yarn) and discovery method is recorded in stats."""
    from repo2graph.walker import DEFAULT_SKIP_DIRS, discover
    for d in (".ruff_cache", ".eggs", ".cache", ".gradle", ".direnv", ".yarn"):
        assert d in DEFAULT_SKIP_DIRS

    cache_file = tmp_path / ".ruff_cache" / "cached.py"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text("x = 1\n", encoding="utf-8")

    good_file = tmp_path / "valid.py"
    good_file.write_text("y = 2\n", encoding="utf-8")

    stats = {}
    found = {rel for rel, _ in discover(tmp_path, stats=stats)}
    assert "valid.py" in found
    assert not any(rel.startswith(".ruff_cache") for rel in found)
    assert stats.get("discovery") in ("git", "walk")


def test_iss21_docstring_inner_quotes_preserved(tmp_path):
    """Issue 21 (ISS-03): Python docstring outer quote stripping does not strip inner quotes."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "doc.py").write_text('def f():\n    """\'inner\'"""\n    pass\n', encoding="utf-8")
    g = build(repo)
    chunk = next(c for c in build_chunks(g) if c["qualname"] == "f")
    assert "'inner'" in chunk["text"]


def test_iss21_add_node_preserves_zero_and_false():
    """Issue 21 (ISS-11): add_node preserves legitimate 0 and False values on re-add."""
    from repo2graph.graph import Graph
    g = Graph(Path("."), "test")
    g.add_node("n1", count=1, flag=True)
    # Re-add with 0 and False
    g.add_node("n1", count=0, flag=False)
    assert g.nodes["n1"]["count"] == 0
    assert g.nodes["n1"]["flag"] is False


def test_iss21_cochange_commits_skipped_counter(monkeypatch):
    """Issue 21 (ISS-12): commits touching > 25 files increment stats['cochange_commits_skipped']."""
    from repo2graph.graph import Graph, add_cochange
    # 26 files in one commit
    files = [f"f{i}.py" for i in range(26)]
    log = "H1\n" + "\n".join(files) + "\n\n"
    fake = subprocess.CompletedProcess([], 0, stdout=log.encode("utf8"), stderr=b"")
    monkeypatch.setattr("repo2graph.graph.subprocess.run", lambda *a, **k: fake)

    g = Graph(Path("."), "root")
    add_cochange(g, Path("."), 1, set(files))
    assert g.stats["cochange_commits_skipped"] == 1


def test_iss23_write_jsonl_always_uses_lf_newlines(tmp_path):
    """Issue 23 (ISS-28): write_jsonl writes LF newlines on all platforms, including Windows."""
    from repo2graph.export import write_jsonl
    p = tmp_path / "test.jsonl"
    write_jsonl(p, [{"a": 1}, {"b": 2}])
    raw = p.read_bytes()
    assert b"\r\n" not in raw
    assert raw.count(b"\n") == 2


def test_iss23_graphml_node_and_edge_ids_xml_safe(tmp_path):
    """Issue 23 (SH-4): write_graphml applies _xml_safe to node id and edge endpoints."""
    import xml.etree.ElementTree as ET
    from repo2graph.export import write_graphml
    from repo2graph.graph import Graph

    g = Graph(tmp_path, "test")
    # Node id with C0 control character \x0c (form feed)
    nid_bad = "sym:bad\x0cname"
    g.add_node(nid_bad, type="symbol", name="bad", path="x.py", qualname="bad")
    g.add_node("sym:good", type="symbol", name="good", path="x.py", qualname="good")
    g.add_edge(nid_bad, "sym:good", "CALLS")

    out = tmp_path / "graph.graphml"
    write_graphml(g, out)

    # Must parse without XML ParseError
    ET.parse(out)
    # Confirm no \x0c character remains in XML
    assert "\x0c" not in out.read_text(encoding="utf-8")


def test_iss24_write_html_handles_placeholder_in_title(tmp_path):
    """Issue 24 (ISS-33): repo name containing __R2G_DATA__ is not replaced by JSON blob in title."""
    from repo2graph.graph import Graph
    from repo2graph.viz import write_html

    g = Graph(tmp_path, "attacker/__R2G_DATA__/repo")
    g.add_node("n1", type="file", path="a.py", name="a.py")
    out = tmp_path / "map.html"
    write_html(g, out)

    content = out.read_text(encoding="utf-8")
    assert "<title>attacker/__R2G_DATA__/repo · repo2graph</title>" in content
    assert "<h1>attacker/__R2G_DATA__/repo</h1>" in content


def test_iss24_select_zero_or_negative_means_no_cap():
    """Issue 24 (ISS-34): max_nodes <= 0 means no cap (returns all nodes)."""
    from repo2graph.viz import select

    nodes = {f"n{i}": {"id": f"n{i}"} for i in range(10)}
    edges = [{"src": "n0", "dst": f"n{i}", "type": "CALLS"} for i in range(1, 10)]

    kept_nodes, kept_edges = select(nodes, edges, max_nodes=0)
    assert len(kept_nodes) == 10
    assert len(kept_edges) == 9

    kept_nodes_neg, kept_edges_neg = select(nodes, edges, max_nodes=-5)
    assert len(kept_nodes_neg) == 10
    assert len(kept_edges_neg) == 9


def test_iss22_chunk_caps_and_residual_span(tmp_path):
    """Issue 22 (ISS-23, ISS-26): named constants for chunk caps, and real line spans for residuals."""
    from repo2graph.chunks import (
        MAX_CALLERS, MAX_CALLEES, MAX_EXT_CALLS, MAX_BASES, MAX_IMPORTS, MAX_DEFINES
    )
    for cap in (MAX_CALLERS, MAX_CALLEES, MAX_EXT_CALLS, MAX_BASES, MAX_IMPORTS, MAX_DEFINES):
        assert isinstance(cap, int) and cap > 0

    repo = tmp_path / "repo"
    repo.mkdir()
    # File with comments at top (lines 1-3), function on lines 4-6, comments at bottom (lines 7-9)
    (repo / "m.py").write_text(
        "# Header comment line 1\n# Header comment line 2\n# Header comment line 3\n"
        "def f():\n    return 42\n\n"
        "# Footer comment line 7\n# Footer comment line 8\n# Footer comment line 9\n",
        encoding="utf-8"
    )
    g = build(repo)
    chunks = build_chunks(g)
    residual = next((c for c in chunks if c["type"] == "file_residual"), None)
    assert residual is not None
    assert residual["start_line"] == 1
    assert residual["end_line"] == 10

