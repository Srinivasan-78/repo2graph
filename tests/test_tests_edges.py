"""TESTS edges (#393): tests linked to the non-test symbols they reach over CALLS."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from repo2graph import export, viz
from repo2graph.cli import main
from repo2graph.edgemeta import METHOD_CALL_GRAPH, METHODS
from repo2graph.graph import TESTS_MAX_HOPS, Graph, add_tests_edges
from repo2graph.query import Index, is_test_path

AUTH = "sym:src/auth.py::"
TEST = "sym:tests/test_auth.py::"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    src = tmp_path_factory.mktemp("tests_edges") / "proj"
    (src / "src").mkdir(parents=True)
    (src / "tests").mkdir()
    (src / "src" / "auth.py").write_text(
        "def hash_password(pw):\n"
        "    return pw[::-1]\n"
        "\n\n"
        "def login(user, pw):\n"
        "    return check(user, hash_password(pw))\n"
        "\n\n"
        "def check(user, hashed):\n"
        "    return bool(user) and bool(hashed)\n"
        "\n\n"
        "def logout(user):\n"
        "    return None\n"
        "\n\n"
        "def untested():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    # Two definitions of one name: a bare call to it resolves ambiguously.
    for mod, val in (("left", 1), ("right", 2)):
        (src / "src" / f"{mod}.py").write_text(
            f"def shared():\n    return {val}\n", encoding="utf-8"
        )
    (src / "tests" / "test_auth.py").write_text(
        "from src.auth import login, logout\n"  # 1
        "\n\n"  # 2-3
        "def make_user():\n"  # 4
        "    logout('x')\n"  # 5
        "    return 'alice'\n"  # 6
        "\n\n"  # 7-8
        "def test_login():\n"  # 9
        "    assert login(make_user(), 'pw')\n"  # 10
        "\n\n"  # 11-12
        "def test_logout():\n"  # 13
        "    assert logout(make_user()) is None\n"  # 14
        "\n\n"  # 15-16
        "def test_shared():\n"  # 17
        "    assert shared() in (1, 2)\n",  # 18
        encoding="utf-8",
    )
    out = src / ".r2g"
    assert main(["build", str(src), "-o", str(out), "--formats", "jsonl"]) == 0
    edges = [
        json.loads(line)
        for line in (out / "agent" / "edges.jsonl").read_text(encoding="utf8").split("\n")
        if line.strip()
    ]
    nodes = {
        json.loads(line)["id"]: json.loads(line)
        for line in (out / "agent" / "nodes.jsonl").read_text(encoding="utf8").split("\n")
        if line.strip()
    }
    return src, out, edges, nodes


def _tests(edges):
    return {(e["src"], e["dst"]): e for e in edges if e["type"] == "TESTS"}


def test_tests_link_test_functions_to_what_they_reach(built):
    _src, _out, edges, _nodes = built
    tests = _tests(edges)

    # Direct calls.
    assert (TEST + "test_login", AUTH + "login") in tests
    assert (TEST + "test_logout", AUTH + "logout") in tests
    # Through the test helper: test_login -> make_user -> logout.
    via_helper = tests[(TEST + "test_login", AUTH + "logout")]
    assert via_helper["hops"] == 2
    # Through production code: test_login -> login -> hash_password.
    assert tests[(TEST + "test_login", AUTH + "hash_password")]["hops"] == 2
    assert (TEST + "test_login", AUTH + "check") in tests
    # The helper lives in a test file, so it is a test symbol in its own right.
    assert (TEST + "make_user", AUTH + "logout") in tests

    assert not any(dst == AUTH + "untested" for _s, dst in tests)


def test_tests_never_point_at_test_code_or_start_in_non_test_code(built):
    _src, _out, edges, nodes = built
    tests = [e for e in edges if e["type"] == "TESTS"]
    assert tests
    for e in tests:
        assert is_test_path(nodes[e["src"]]["path"]), e
        assert not is_test_path(nodes[e["dst"]]["path"]), e
        assert nodes[e["dst"]]["type"] == "symbol"


def test_tests_edges_carry_standard_metadata(built):
    _src, _out, edges, _nodes = built
    e = _tests(edges)[(TEST + "test_login", AUTH + "logout")]
    assert e["method"] == METHOD_CALL_GRAPH and METHOD_CALL_GRAPH in METHODS
    assert e["confidence"] == 1.0
    # Evidence is the call in the test where the best path starts: the
    # `make_user()` call on line 10, which is what leads to `logout`.
    assert e["evidence"] == {"path": "tests/test_auth.py", "line": 10}
    keys = list(e)
    assert keys[:6] == ["src", "dst", "type", "method", "confidence", "evidence"]
    assert keys[6:] == sorted(keys[6:])


def test_tests_edges_are_deduplicated_per_pair(built):
    _src, _out, edges, _nodes = built
    pairs = [(e["src"], e["dst"]) for e in edges if e["type"] == "TESTS"]
    assert len(pairs) == len(set(pairs))


def test_ambiguous_call_yields_a_tests_edge_below_one(built):
    _src, _out, edges, _nodes = built
    tests = _tests(edges)
    for mod in ("left", "right"):
        e = tests[(TEST + "test_shared", f"sym:src/{mod}.py::shared")]
        assert e["confidence"] < 1.0
        assert e["confidence"] == 0.5


def test_repo_neighbours_surfaces_tests_and_filters_by_confidence(built):
    from repo2graph.mcp import dispatch

    _src, out, _edges, _nodes = built
    idx = Index(out)

    text = dispatch(idx, "repo_neighbours", {"node_id": AUTH + "logout", "limit": 50})
    rows = [ln for ln in text.split("\n") if ln.startswith("- TESTS in:")]
    assert any("test_login" in r for r in rows), text
    assert any("test_logout" in r for r in rows), text
    assert any("make_user" in r for r in rows), text

    left = "sym:src/left.py::shared"
    loose = dispatch(idx, "repo_neighbours", {"node_id": left, "limit": 50})
    assert any(
        ln.startswith("- TESTS in:") and "test_shared" in ln and "AMBIGUOUS 0.5" in ln
        for ln in loose.split("\n")
    ), loose

    strict = dispatch(idx, "repo_neighbours", {"node_id": left, "limit": 50, "min_confidence": 1.0})
    assert "TESTS" not in strict, strict
    assert "test_shared" not in strict, strict


def test_paged_repo_neighbours_lists_tests_and_honours_min_confidence(built):
    """A cursor pages the same answer: TESTS rows included, the same floor applied."""
    from repo2graph.mcp import dispatch

    _src, out, _edges, _nodes = built
    idx = Index(out)
    left = "sym:src/left.py::shared"
    loose = dispatch(idx, "repo_neighbours", {"node_id": left, "limit": 50, "cursor": ""})
    assert any(ln.startswith("- TESTS in:") and "test_shared" in ln for ln in loose.split("\n")), (
        loose
    )

    args = {"node_id": left, "limit": 50, "min_confidence": 1.0, "cursor": ""}
    strict = dispatch(idx, "repo_neighbours", args)
    assert "TESTS" not in strict, strict
    assert "test_shared" not in strict, strict


def test_rag_expansion_does_not_follow_tests_unless_asked(built):
    _src, out, _edges, _nodes = built
    idx = Index(out)
    default = idx.pack_context("hash_password", k=1, hops=1, min_confidence=0.0)
    assert not any(c["why"].startswith("TESTS") for c in default["neighbors"])

    opted = idx.pack_context("hash_password", k=1, hops=1, min_confidence=0.0, edge_types=["TESTS"])
    assert opted["seeds"][0]["node_id"] == AUTH + "hash_password"
    whys = [c["why"] for c in opted["neighbors"]]
    assert whys and all(w.startswith("TESTS in of") for w in whys), whys


def test_stats_report_tests_edges_and_the_tested_fraction(built):
    _src, out, edges, _nodes = built
    stats = json.loads((out / "agent" / "stats.json").read_text(encoding="utf8"))
    assert stats["edge:TESTS"] == sum(1 for e in edges if e["type"] == "TESTS")
    # Non-test functions: hash_password, login, check, logout, untested, and
    # the two `shared` -- every one but `untested` is reached by a test.
    assert stats["testable_symbols"] == 7
    assert stats["tested_symbols"] == 6
    assert stats["tested_symbol_fraction"] == round(6 / 7, 3)


def test_tests_edges_are_byte_identical_across_builds_and_hash_seeds(built, tmp_path):
    src, _out, _edges, _nodes = built
    digests = set()
    for seed in ("0", "1", "2"):
        out = tmp_path / f"out{seed}"
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; from repo2graph.cli import main; sys.exit(main(sys.argv[1:]))",
                "build",
                str(src),
                "-o",
                str(out),
                "--formats",
                "jsonl",
            ],
            capture_output=True,
            text=True,
            env=dict(os.environ, PYTHONHASHSEED=seed),
        )
        assert proc.returncode == 0, proc.stderr
        digests.add(hashlib.sha256((out / "agent" / "edges.jsonl").read_bytes()).hexdigest())
    assert len(digests) == 1


def test_confidence_is_the_product_along_the_best_path(tmp_path):
    g = Graph(Path(tmp_path), "t")
    for nid, path in (
        ("sym:tests/test_x.py::test_a", "tests/test_x.py"),
        ("sym:x.py::mid", "x.py"),
        ("sym:x.py::leaf", "x.py"),
        ("sym:x.py::far", "x.py"),
    ):
        g.add_node(nid, type="symbol", kind="function", path=path)
    g.add_edge("sym:tests/test_x.py::test_a", "sym:x.py::mid", "CALLS", confidence=0.5)
    g.add_edge("sym:x.py::mid", "sym:x.py::leaf", "CALLS", confidence=0.5)
    # A second, better route to leaf: direct and certain.
    g.add_edge("sym:tests/test_x.py::test_a", "sym:x.py::leaf", "CALLS", confidence=1.0)
    # Beyond the hop bound.
    g.add_edge("sym:x.py::leaf", "sym:x.py::far", "CALLS", confidence=1.0)
    g.add_edge("sym:x.py::mid", "sym:x.py::far", "CALLS", confidence=1.0, untyped_receiver=True)
    add_tests_edges(g)

    tests = _tests(g.edges)
    assert tests[("sym:tests/test_x.py::test_a", "sym:x.py::mid")]["confidence"] == 0.5
    leaf = tests[("sym:tests/test_x.py::test_a", "sym:x.py::leaf")]
    assert leaf["confidence"] == 1.0 and leaf["hops"] == 1
    # far is 2 hops away via leaf (certain), and the untyped-receiver guess
    # through mid is not walked at all.
    far = tests[("sym:tests/test_x.py::test_a", "sym:x.py::far")]
    assert far["confidence"] == 1.0 and far["hops"] == 2
    assert TESTS_MAX_HOPS == 2


@pytest.mark.parametrize(
    "path",
    [
        "tests/unit/foo.py",
        "test/foo.py",
        "web/__tests__/foo.js",
        "spec/models/user.rb",
        "pkg/test_foo.py",
        "pkg/foo_test.py",
        "pkg/foo_test.go",
        "src/foo.test.ts",
        "src/foo.spec.ts",
        "src/main/java/FooTest.java",
        "app/models/user_spec.rb",
    ],
)
def test_test_path_conventions(path):
    assert is_test_path(path)
    assert is_test_path(path.replace("/", "\\"))


@pytest.mark.parametrize(
    "path", ["src/latest.py", "src/Latest.java", "src/Contest.java", "lib/inspect.rb", "a.py"]
)
def test_non_test_paths(path):
    assert not is_test_path(path)


def test_tests_edge_type_is_described_everywhere_types_are_listed():
    assert "TESTS" in viz.EDGE_TYPES and export.EDGE_TYPES is viz.EDGE_TYPES
    assert "not assertion" in viz.EDGE_TYPES["TESTS"]
    assert f"at most {TESTS_MAX_HOPS} CALLS hops" in viz.EDGE_TYPES["TESTS"]
