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


def test_parse_git_diff_ranges():
    from repo2graph.graphdiff import parse_git_diff_ranges

    diff_text = """diff --git a/pkg/auth.py b/pkg/auth.py
index 111..222 100644
--- a/pkg/auth.py
+++ b/pkg/auth.py
@@ -10,3 +10,5 @@ def validate(t):
+    extra()
@@ -25 +27,0 @@ def old():
-    pass
diff --git a/tests/test_auth.py b/tests/test_auth.py
index 333..444 100644
--- a/tests/test_auth.py
+++ b/tests/test_auth.py
@@ -5 +5,2 @@ def test_auth():
+    assert True
"""
    spans = parse_git_diff_ranges(diff_text)
    assert spans["pkg/auth.py"] == [(10, 14), (27, 27)]
    assert spans["tests/test_auth.py"] == [(5, 6)]


def test_find_affected_tests_synthetic_closure(tmp_path):
    from repo2graph.graphdiff import find_affected_tests
    from repo2graph.query import Index

    repo = tmp_path / "pr_repo"
    repo.mkdir()

    (repo / "auth").mkdir()
    (repo / "auth" / "jwt.py").write_text(
        "def verify_token(t):\n    return check_token(t)\n\n"
        "def check_token(t):\n    return bool(t)\n",
        encoding="utf-8",
    )

    (repo / "api").mkdir()
    (repo / "api" / "routes.py").write_text(
        "from auth.jwt import verify_token\n\n"
        "def get_current_user():\n    return verify_token('tok')\n",
        encoding="utf-8",
    )

    (repo / "api" / "endpoints").mkdir()
    (repo / "api" / "endpoints" / "billing.py").write_text(
        "from api.routes import get_current_user\n\n"
        "def charge_invoice():\n    return get_current_user()\n",
        encoding="utf-8",
    )

    (repo / "tests").mkdir()
    (repo / "tests" / "test_auth.py").write_text(
        "from auth.jwt import verify_token\n\ndef test_verify():\n    assert verify_token('ok')\n",
        encoding="utf-8",
    )

    (repo / "tests" / "test_billing.py").write_text(
        "from api.endpoints.billing import charge_invoice\n\n"
        "def test_charge():\n    assert charge_invoice()\n",
        encoding="utf-8",
    )

    (repo / "tests" / "test_unrelated.py").write_text(
        "def test_math():\n    assert 1 + 1 == 2\n",
        encoding="utf-8",
    )

    idx_out = tmp_path / "pr_idx"
    main(["build", str(repo), "-o", str(idx_out), "--formats", "jsonl"])
    idx = Index(idx_out)

    res = find_affected_tests(idx, modified_spans={"auth/jwt.py": [(2, 3)]})

    assert "auth/jwt.py::verify_token" in res["modified_symbols"]
    assert "api/routes.py::get_current_user" in res["impacted_callers"]
    assert "api/endpoints/billing.py::charge_invoice" in res["impacted_callers"]
    assert res["affected_tests"] == ["tests/test_auth.py", "tests/test_billing.py"]
    assert "tests/test_unrelated.py" not in res["affected_tests"]
    assert res["blast_radius_nodes"] > 0
    assert isinstance(res["risk_score"], float)
    assert isinstance(res["high_risk"], bool)


def test_cli_diff_base_commit_affected_tests_golden(tmp_path, capsys):
    import subprocess

    repo = tmp_path / "git_repo"
    repo.mkdir()

    # Initialize real git repo
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), check=True)

    (repo / "auth").mkdir()
    jwt_file = repo / "auth" / "jwt.py"
    jwt_file.write_text(
        "def verify_token(t):\n    return check_token(t)\n\n"
        "def check_token(t):\n    return bool(t)\n",
        encoding="utf-8",
    )

    (repo / "api").mkdir()
    (repo / "api" / "routes.py").write_text(
        "from auth.jwt import verify_token\n\n"
        "def get_current_user():\n    return verify_token('tok')\n",
        encoding="utf-8",
    )

    (repo / "tests").mkdir()
    (repo / "tests" / "test_auth.py").write_text(
        "from auth.jwt import verify_token\n\ndef test_verify():\n    assert verify_token('ok')\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_unrelated.py").write_text(
        "def test_other():\n    assert True\n",
        encoding="utf-8",
    )

    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=str(repo), check=True, capture_output=True
    )

    # Modify verify_token in auth/jwt.py
    jwt_file.write_text(
        "def verify_token(t):\n    # modified logic\n    return check_token(t)\n\n"
        "def check_token(t):\n    return bool(t)\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "update jwt"], cwd=str(repo), check=True, capture_output=True
    )

    # Build index for the current state
    idx_dir = tmp_path / "git_idx"
    main(["build", str(repo), "-o", str(idx_dir), "--formats", "jsonl"])
    capsys.readouterr()

    # Test --affected-tests outputs minimal test set
    rc = main(
        [
            "diff",
            "--repo",
            str(repo),
            "-o",
            str(idx_dir),
            "--base-commit",
            "HEAD~1",
            "--affected-tests",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out == "tests/test_auth.py"
    assert "tests/test_unrelated.py" not in out

    # Test --pr-mode with --format json
    rc_json = main(
        [
            "diff",
            "--repo",
            str(repo),
            "-o",
            str(idx_dir),
            "--base-commit",
            "HEAD~1",
            "--pr-mode",
            "--format",
            "json",
        ]
    )
    assert rc_json == 0
    data = json.loads(capsys.readouterr().out)
    assert "auth/jwt.py::verify_token" in data["modified_symbols"]
    assert "api/routes.py::get_current_user" in data["impacted_callers"]
    assert data["affected_tests"] == ["tests/test_auth.py"]
    assert data["blast_radius_nodes"] > 0
    assert not data["high_risk"]


def test_cli_diff_github_step_summary(tmp_path, capsys, monkeypatch):
    import subprocess

    repo = tmp_path / "git_summary_repo"
    repo.mkdir()

    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), check=True)

    (repo / "core.py").write_text("def core_fn():\n    return 42\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_core.py").write_text(
        "from core import core_fn\ndef test_fn():\n    assert core_fn() == 42\n",
        encoding="utf-8",
    )

    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=str(repo), check=True, capture_output=True
    )

    (repo / "core.py").write_text("def core_fn():\n    return 43\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "update core"], cwd=str(repo), check=True, capture_output=True
    )

    idx_dir = tmp_path / "summary_idx"
    main(["build", str(repo), "-o", str(idx_dir), "--formats", "jsonl"])
    capsys.readouterr()

    summary_file = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

    rc = main(
        [
            "diff",
            "--repo",
            str(repo),
            "-o",
            str(idx_dir),
            "--base-commit",
            "HEAD~1",
            "--pr-mode",
            "--github-step-summary",
        ]
    )
    assert rc == 0
    assert summary_file.exists()
    content = summary_file.read_text(encoding="utf-8")
    assert "### 🎯 Semantic PR Blast Radius & Test Selection" in content
    assert "core.py::core_fn" in content
    assert "tests/test_core.py" in content


def _ambiguous_deep_repo(tmp_path):
    """A test reachable from the edit only through a 3-way-ambiguous call.

    `target` is defined in three modules, so the call in `inner_call` resolves
    to all three at `confidence = 1/3` -- below the 0.5 floor that used to
    prune it. The test is 3 hops from the edit, past `derived.py`'s
    pre-materialized `TESTS` edges (`TESTS_MAX_HOPS = 2`), so the live reverse
    CALLS walk is the only way to reach it.
    """
    repo = tmp_path / "amb_repo"
    (repo / "a").mkdir(parents=True)
    (repo / "b").mkdir(parents=True)
    (repo / "c").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)

    # Three same-named definitions -> every call to `target` is 1/3 confident.
    for pkg in ("a", "b", "c"):
        (repo / pkg / "mod.py").write_text(
            f"def target(x):\n    return x + 1  # {pkg}\n", encoding="utf-8"
        )

    # Deliberately *not* `from a.mod import target`: an import resolves the call
    # to one definition at confidence 1.0, which is the case that always worked.
    # Leaving it unimported is what makes the scoped resolver emit three
    # candidate edges at 0.33 each.
    (repo / "caller.py").write_text(
        "def inner_call(x):\n    return target(x)\n",
        encoding="utf-8",
    )
    (repo / "wrapper.py").write_text(
        "from caller import inner_call\n\ndef mid_call(x):\n    return inner_call(x)\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_deep.py").write_text(
        "from wrapper import mid_call\n\ndef test_deep():\n    assert mid_call(1) == 2\n",
        encoding="utf-8",
    )
    return repo


def test_affected_tests_keeps_tests_behind_an_ambiguous_call(tmp_path):
    """#508: a test reached only through a low-confidence CALLS edge must not vanish.

    A 0.5 confidence floor severed the walk at the ambiguous hop and returned
    `affected_tests: []` -- a silent false negative, which in CI means a green
    run over code nothing exercised.
    """
    from repo2graph.graphdiff import find_affected_tests
    from repo2graph.query import Index

    repo = _ambiguous_deep_repo(tmp_path)
    idx_out = tmp_path / "amb_idx"
    main(["build", str(repo), "-o", str(idx_out), "--formats", "jsonl"])

    res = find_affected_tests(Index(idx_out), modified_spans={"a/mod.py": [(1, 2)]})

    assert "a/mod.py::target" in res["modified_symbols"]
    assert "tests/test_deep.py" in res["affected_tests"], (
        f"the test behind the ambiguous call was dropped: {res['affected_tests']!r}"
    )


def test_affected_tests_reports_when_narrowing_drops_edges(tmp_path):
    """Asking for the old 0.5 floor still narrows -- but says that it did.

    The point is that the trade is reported rather than implicit: the same walk
    that loses the test also sets `truncated`, so a CI caller can tell a
    provably complete subset from a lossy one.
    """
    from repo2graph.graphdiff import find_affected_tests, render_pr_summary_markdown
    from repo2graph.query import Index

    repo = _ambiguous_deep_repo(tmp_path)
    idx_out = tmp_path / "amb_idx2"
    main(["build", str(repo), "-o", str(idx_out), "--formats", "jsonl"])
    idx = Index(idx_out)

    narrowed = find_affected_tests(idx, modified_spans={"a/mod.py": [(1, 2)]}, min_confidence=0.5)
    assert narrowed["truncated"] is True
    assert "min_confidence=0.5" in narrowed["incomplete_reason"]
    assert "WARNING" in render_pr_summary_markdown(narrowed)

    # The recall-first default is complete for this graph, so it must not cry wolf.
    full = find_affected_tests(idx, modified_spans={"a/mod.py": [(1, 2)]})
    assert full["truncated"] is False
    assert full["incomplete_reason"] == ""


def test_affected_tests_flags_a_walk_cut_off_by_max_hops(tmp_path):
    """A hop limit that stops mid-expansion marks the selection incomplete."""
    from repo2graph.graphdiff import find_affected_tests
    from repo2graph.query import Index

    repo = _ambiguous_deep_repo(tmp_path)
    idx_out = tmp_path / "amb_idx3"
    main(["build", str(repo), "-o", str(idx_out), "--formats", "jsonl"])

    res = find_affected_tests(Index(idx_out), modified_spans={"a/mod.py": [(1, 2)]}, max_hops=1)
    assert res["truncated"] is True
    assert "max_hops=1" in res["incomplete_reason"]


def test_affected_tests_sees_through_a_test_helper(tmp_path):
    """A test reached only via a fixture/helper module must not be dropped.

    `is_test_path` is true of every module under `tests/` -- `conftest.py`,
    fixtures, assertion helpers -- and `derived.py` seeds a `TESTS` edge from
    the helper nearest the modified code. The walk used to mark that helper
    visited without ever expanding it, so the real `test_*.py` callers one hop
    further out were never reached, and the frontier still drained, so nothing
    reported the answer as incomplete. Fixtures and helpers are the common case,
    which made this the same silent false negative in a new place.
    """
    from repo2graph.graphdiff import find_affected_tests
    from repo2graph.query import Index

    repo = tmp_path / "helper_repo"

    def w(rel, text):
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    w("core/m.py", "def modified_fn(x):\n    return x + 1\n")
    w(
        "core/middle.py",
        "from core.m import modified_fn\n\ndef middle_fn(x):\n    return modified_fn(x)\n",
    )
    w(
        "tests/helper2.py",
        "from core.middle import middle_fn\n\ndef h2(x):\n    return middle_fn(x)\n",
    )
    w("tests/helper1.py", "from tests.helper2 import h2\n\ndef h1(x):\n    return h2(x)\n")
    for n in ("a", "b", "c"):
        w(
            f"tests/test_{n}.py",
            f"from tests.helper1 import h1\n\ndef test_{n}():\n    assert h1(1) == 2\n",
        )

    idx_out = tmp_path / "helper_idx"
    main(["build", str(repo), "-o", str(idx_out), "--formats", "jsonl"])

    res = find_affected_tests(Index(idx_out), modified_spans={"core/m.py": [(1, 2)]})

    for n in ("a", "b", "c"):
        assert f"tests/test_{n}.py" in res["affected_tests"], (
            f"test_{n}.py was hidden behind the helper chain: {res['affected_tests']!r}"
        )


def test_affected_tests_does_not_follow_a_shadowed_builtin(tmp_path):
    """Editing a function that shadows a builtin must not select the whole suite.

    A `shadowed_builtin` edge is not an ambiguous *candidate* the way a 1/n
    name-resolution edge is -- the call almost certainly went to the builtin and
    the user symbol merely shares its name. Following them turned a two-line
    edit into "run every test that calls `len()`", which is the feature failing
    in the over-selection direction.
    """
    from repo2graph.graphdiff import find_affected_tests
    from repo2graph.query import Index

    repo = tmp_path / "shadow_repo"

    def w(rel, text):
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    w("shadow.py", "def len(x):\n    return 42\n")
    for i in range(12):
        w(f"mod{i}.py", f"def use{i}(xs):\n    return len(xs)\n")
        w(
            f"tests/test_m{i}.py",
            f"from mod{i} import use{i}\n\ndef test_m{i}():\n    assert use{i}([1]) == 1\n",
        )

    idx_out = tmp_path / "shadow_idx"
    main(["build", str(repo), "-o", str(idx_out), "--formats", "jsonl"])

    res = find_affected_tests(Index(idx_out), modified_spans={"shadow.py": [(1, 2)]})

    assert res["affected_tests"] == [], (
        f"a shadowed builtin pulled in unrelated tests: {res['affected_tests']!r}"
    )
    assert res["high_risk"] is False

    # Skipping is not the same as being sure. The classification is a heuristic,
    # so the count is reported and the result admits to being incomplete --
    # otherwise this precision fix would reintroduce the silent drop the
    # recall fix exists to prevent, just under a different name.
    assert res["shadowed_builtin_skipped"] == 12, res["shadowed_builtin_skipped"]
    assert res["truncated"] is True
    assert "shadowed builtin" in res["incomplete_reason"]
