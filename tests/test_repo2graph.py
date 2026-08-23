"""End-to-end and unit coverage for graph building, chunking and retrieval."""
import json
import subprocess

import pytest

from repo2graph.chunks import _split, build_chunks
from repo2graph.cli import main, parse_formats
from repo2graph.graph import build, import_targets, path_index, resolve_import
from repo2graph.parse import parse_source
from repo2graph.query import Index, tokenize
from repo2graph.viz import LoadedGraph, node_label, payload, select
from repo2graph.walker import discover, matches_any

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
        'MSG = "a\u2028b\u2029c\u0085d"\n\n\ndef uses_sep():\n    return MSG\n')
    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out), "--formats", "jsonl"])
    idx = Index(out)
    assert any(c["path"] == "pkg/sep.py" for c in idx.chunks)
    assert idx.retrieve("uses_sep", k=3)


def test_query_without_an_index_exits_cleanly(tmp_path):
    with pytest.raises(SystemExit):
        main(["query", "anything", "-o", str(tmp_path / "missing")])
    with pytest.raises(SystemExit):
        main(["stats", "-o", str(tmp_path / "missing")])


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
        assert (out / name).exists(), name
    report = json.loads(capsys.readouterr().out)
    assert report["chunks"] > 0
    assert json.loads((out / "stats.json").read_text())["files"] > 0


def test_no_chunks_flag(tmp_path, sample_repo):
    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out), "--formats", "jsonl", "--no-chunks"])
    assert not (out / "chunks.jsonl").exists()


def test_cypher_output_is_quoted(tmp_path, sample_repo):
    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out), "--formats", "cypher"])
    text = (out / "graph.cypher").read_text()
    assert "CREATE CONSTRAINT" in text
    assert 'MERGE (n:R2G:File {id: "file:pkg/main.py"})' in text
    assert "MERGE (a)-[:CALLS" in text


def test_graphml_is_loadable(tmp_path, sample_repo):
    nx = pytest.importorskip("networkx")
    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out), "--formats", "graphml"])
    G = nx.read_graphml(out / "graph.graphml")
    assert "sym:pkg/util.py::helper" in G


def test_overview_lists_hubs(tmp_path, sample_repo):
    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out), "--formats", "overview"])
    text = (out / "overview.md").read_text()
    assert "# Repo map:" in text
    assert "pkg/util.py" in text


# ---------- html map ----------

def test_build_writes_html_map(tmp_path, sample_repo):
    out = tmp_path / "idx"
    main(["build", str(sample_repo), "-o", str(out)])
    page = (out / "graph.html").read_text(encoding="utf8")
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
    page = (out / "graph.html").read_text(encoding="utf8")
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
    assert not (out / "graph.html").exists()
    capsys.readouterr()  # drop the build report; only the map report is asserted
    main(["map", "-o", str(out), "--viz-nodes", "4"])
    assert json.loads(capsys.readouterr().out)["nodes"] == 4
    assert "<svg" in (out / "graph.html").read_text(encoding="utf8")


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
    page = (out / "graph.html").read_text(encoding="utf8")
    assert "</script><script>alert(1)" not in page
    assert "<\\/script>" in page


def test_node_label_truncates(sample_graph):
    long = {"id": "sym:a.py::x", "qualname": "SomeVeryLongClassName.method"}
    assert node_label(long).endswith("…") and len(node_label(long)) == 15
