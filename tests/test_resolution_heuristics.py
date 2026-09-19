import pytest
from repo2graph.graph import build


@pytest.fixture
def repo(tmp_path):
    repo_dir = tmp_path / "test_repo"
    repo_dir.mkdir()

    (repo_dir / "main.py").write_text("""\
import lib_imported

def call_unique():
    unique_func()

def call_same_file():
    same_file_func()

def same_file_func():
    pass

def call_import():
    import_func()
""")

    (repo_dir / "lib_imported.py").write_text("""\
def import_func():
    pass
""")

    (repo_dir / "lib_not_imported.py").write_text("""\
def import_func():
    pass

def same_file_func():
    pass
""")

    dir2 = repo_dir / "other"
    dir2.mkdir()
    (dir2 / "distant.py").write_text("""\
def unique_func():
    pass

def import_func():
    pass

def same_file_func():
    pass

def call_ambiguous():
    ambiguous_func()
""")

    (dir2 / "dist1.py").write_text("""\
def ambiguous_func():
    pass
""")

    (dir2 / "dist2.py").write_text("""\
def ambiguous_func():
    pass
""")

    return repo_dir


def test_unique_name(repo):
    g = build(repo)
    edges = [e for e in g.edges if e["type"] == "CALLS" and "sym:main.py::call_unique" in e["src"]]
    assert len(edges) == 1
    assert edges[0]["confidence"] == 1.0
    assert "ambiguous" not in edges[0]


def test_same_file_beats_cross_file(repo):
    g = build(repo)
    edges = [
        e for e in g.edges if e["type"] == "CALLS" and "sym:main.py::call_same_file" in e["src"]
    ]
    edges.sort(key=lambda e: e.get("confidence", 0), reverse=True)
    assert edges[0]["dst"] == "sym:main.py::same_file_func"
    assert edges[0]["confidence"] >= 0.4  # higher than 0.33 threshold


def test_import_guided_beats_directory(repo):
    g = build(repo)
    edges = [e for e in g.edges if e["type"] == "CALLS" and "sym:main.py::call_import" in e["src"]]
    edges.sort(key=lambda e: e.get("confidence", 0), reverse=True)
    assert edges[0]["dst"] == "sym:lib_imported.py::import_func"
    assert edges[0]["confidence"] >= 0.5


def test_ambiguous_flag(repo):
    g = build(repo)
    edges = [
        e
        for e in g.edges
        if e["type"] == "CALLS" and "sym:other/distant.py::call_ambiguous" in e["src"]
    ]
    assert len(edges) == 2
    for e in edges:
        assert e.get("ambiguous") is True


def test_iss127_max_call_candidates_fan_out(tmp_path):
    """Issue 127: call resolution fan-out respects max_call_candidates instead of hard-capping at 3."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "caller.py").write_text("def run():\n    worker()\n")
    # 5 distant candidate files with worker()
    for i in range(5):
        d = repo / f"d{i}"
        d.mkdir()
        (d / "worker.py").write_text("def worker():\n    pass\n")

    # With default max_call_candidates=5
    g5 = build(repo, max_call_candidates=5)
    edges5 = [e for e in g5.edges if e["type"] == "CALLS" and "caller.py" in e["src"]]
    assert len(edges5) == 5
    for e in edges5:
        assert e["confidence"] == 0.2
        assert e.get("ambiguous") is True

    # With max_call_candidates=2
    g2 = build(repo, max_call_candidates=2)
    edges2 = [e for e in g2.edges if e["type"] == "CALLS" and "caller.py" in e["src"]]
    assert len(edges2) == 2
    for e in edges2:
        assert e["confidence"] == 0.5
        assert e.get("ambiguous") is True
