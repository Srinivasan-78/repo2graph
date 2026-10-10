"""`repo2graph diff` and the delta behind CHANGELOG.md (#395)."""

from __future__ import annotations

import json
import shutil

import pytest

from repo2graph.cli import main
from repo2graph.graphdiff import diff_graphs

V1 = {
    "pkg/auth.py": (
        "def validate_token(t):\n    return check(t)\n\n\n"
        "def check(t):\n    return bool(t)\n\n\n"
        "def legacy(t):\n    return t\n"
    ),
    "pkg/util.py": "def helper(x):\n    return x * 2\n\n\ndef tweak(x):\n    return x + 1\n",
    "pkg/views.py": "from pkg.auth import validate_token\n\n\ndef login(t):\n    return validate_token(t)\n",
}
V2 = {
    # legacy removed, audit added and calls validate_token
    "pkg/auth.py": (
        "def validate_token(t):\n    return check(t)\n\n\n"
        "def check(t):\n    return bool(t)\n\n\n"
        "def audit(t):\n    return validate_token(t)\n"
    ),
    # util.py moved to helpers.py: helper unchanged, tweak edited
    "pkg/helpers.py": "def helper(x):\n    return x * 2\n\n\ndef tweak(x):\n    return x + 2\n",
    "pkg/views.py": "from pkg.auth import validate_token\n\n\ndef login(t):\n    return validate_token(t)\n",
}


def _index(tmp_path, name, files):
    repo = tmp_path / f"{name}-src"
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf8")
    # Same root name for both builds, so ids line up as they would for one repo.
    work = tmp_path / name / "repo"
    shutil.copytree(repo, work)
    out = tmp_path / f"{name}-idx"
    main(["build", str(work), "-o", str(out), "--formats", "jsonl"])
    return out


@pytest.fixture
def indexes(tmp_path, capsys):
    old, new = _index(tmp_path, "v1", V1), _index(tmp_path, "v2", V2)
    capsys.readouterr()
    return old, new


def test_diff_reports_added_removed_renamed_and_callers(indexes, capsys):
    old, new = indexes
    assert main(["diff", str(old), str(new), "--format", "json"]) == 0
    d = json.loads(capsys.readouterr().out)

    added = {n["id"] for n in d["added_nodes"] if n["type"] == "symbol"}
    removed = {n["id"] for n in d["removed_nodes"] if n["type"] == "symbol"}
    assert added == {"sym:pkg/auth.py::audit"}
    assert removed == {"sym:pkg/auth.py::legacy"}
    assert d["renamed"] == [
        {
            "from": "sym:pkg/util.py::helper",
            "to": "sym:pkg/helpers.py::helper",
            "match": "same body",
        },
        {"from": "file:pkg/util.py", "to": "file:pkg/helpers.py", "match": "its symbols moved"},
    ]
    assert d["possible_renames"] == [
        {
            "from": "sym:pkg/util.py::tweak",
            "to": "sym:pkg/helpers.py::tweak",
            "match": "same name, body changed",
        }
    ]
    gained = {c["node"]: c["gained"] for c in d["callers_changed"]}
    assert gained["sym:pkg/auth.py::validate_token"] == ["sym:pkg/auth.py::audit"]


def test_a_pure_move_does_not_read_as_edge_churn(indexes, capsys):
    old, new = indexes
    main(["diff", str(old), str(new), "--format", "json"])
    d = json.loads(capsys.readouterr().out)
    touched = {e["src"] for e in d["added_edges"] + d["removed_edges"]}
    assert "sym:pkg/helpers.py::helper" not in touched
    assert "sym:pkg/util.py::helper" not in touched


def test_diff_text_output_summarises(indexes, capsys):
    old, new = indexes
    main(["diff", str(old), str(new)])
    out = capsys.readouterr().out
    assert "symbols: +1 -1, 1 renamed, 1 possible renames; 1 files moved" in out
    assert "moved files:" in out and "callers changed:" in out


def test_identical_builds_diff_to_nothing(tmp_path, capsys):
    old = _index(tmp_path, "a", V1)
    new = _index(tmp_path, "b", V1)
    capsys.readouterr()
    main(["diff", str(old), str(new), "--format", "json"])
    d = json.loads(capsys.readouterr().out)
    assert not any(
        d[k] for k in ("added_nodes", "removed_nodes", "renamed", "added_edges", "removed_edges")
    )


def test_confidence_only_changes_are_counted_not_listed():
    node = [{"id": "a", "type": "symbol"}, {"id": "b", "type": "symbol"}]
    old_e = [{"src": "a", "dst": "b", "type": "CALLS", "confidence": 1.0}]
    new_e = [{"src": "a", "dst": "b", "type": "CALLS", "confidence": 0.5}]
    d = diff_graphs(node, old_e, node, new_e)
    assert not d["added_edges"] and not d["removed_edges"]
    assert d["confidence_changed_edges"][0]["was"] == 1.0


def test_diff_requires_two_indexes(tmp_path):
    with pytest.raises(SystemExit, match="no index"):
        main(["diff", str(tmp_path / "x"), str(tmp_path / "y")])
