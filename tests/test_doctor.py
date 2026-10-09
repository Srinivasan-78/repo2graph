"""Tests for repo2graph doctor command and diagnostic probes."""

import json
from pathlib import Path
from unittest.mock import patch

from repo2graph.cli import main
from repo2graph.doctor import (
    DoctorReport,
    check_artifact_integrity,
    check_git,
    check_mcp_server,
    check_permissions,
    check_platform_encoding,
    check_platform_support,
    check_python,
    check_tree_sitter,
    run_doctor,
)


def test_doctor_smoke(tmp_path):
    """Basic smoke test for run_doctor on an empty directory."""
    report = run_doctor(tmp_path)
    assert isinstance(report, DoctorReport)
    assert report.ok is True
    text = report.format_text()
    assert "repo2graph doctor" in text
    assert "[OK]" in text

    d = report.to_dict()
    assert d["status"] == "ok"
    assert len(d["checks"]) >= 5


def test_doctor_python_version_failure():
    """Verify that Python < 3.10 produces a FAIL result with remediation."""
    with patch("sys.version_info", (3, 9, 7)):
        res = check_python()
        assert res.status == "fail"
        assert "unsupported" in res.summary
        assert res.remediation is not None
        assert "3.10" in res.remediation


def test_doctor_tree_sitter_missing():
    """Verify tree-sitter configuration failure produces a FAIL result."""
    with patch.dict("sys.modules", {"repo2graph.parse": None}):
        res = check_tree_sitter()
        assert res.status == "fail"
        assert res.remediation is not None


def test_doctor_mcp_server_ok_in_this_environment():
    """The dev extras install a supported SDK, so this must read ok here.

    Pinned against `SDK_SPEC` rather than a literal version: the spec is the
    server's own gate, and a test asserting "mcp 2.x" would have to be edited
    the day the floor moves, which is exactly when it should instead be proving
    that doctor moved with it.
    """
    from repo2graph.mcp.server import SDK_SPEC

    res = check_mcp_server()
    assert res.status == "ok", res.summary
    assert SDK_SPEC in res.summary
    assert any("installed SDK" in d for d in res.details)


def test_doctor_mcp_sdk_missing_is_a_warning_not_a_failure():
    """`repo2graph[mcp]` is an extra. A CLI-only install is a supported setup,
    so an absent SDK must not fail a pipeline gated on `doctor`'s exit code."""
    with patch.dict("sys.modules", {"mcp": None}):
        res = check_mcp_server()
    assert res.status == "warn"
    assert res.remediation is not None
    assert "repo2graph[mcp]" in res.remediation


def test_doctor_rejects_the_sdk_major_the_server_rejects():
    """mcp 1.x hangs on the first tool call (#407). The server refuses to start
    on it; doctor has to say so too, or it certifies a setup that cannot work."""
    import importlib

    mcp_server = importlib.import_module("repo2graph.mcp.server")
    with patch.object(mcp_server, "_sdk_version", return_value="1.30.0"):
        res = check_mcp_server()
    assert res.status == "fail"
    assert "1.30.0" in res.summary
    assert res.remediation is not None


def test_doctor_does_not_read_mcp_client_config_files():
    """`doctor --json` is documented as safe to paste into a bug report, which
    holds only while it stays out of files belonging to other tools.

    Checked over the AST's string *constants*, not the raw source: the probe's
    own docstring names these files in order to say it does not read them, and a
    substring scan cannot tell that apart from opening one.
    """
    import ast

    import repo2graph.doctor as doctor_mod

    tree = ast.parse(Path(doctor_mod.__file__).read_text(encoding="utf-8"))

    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]

    for client_config in (".claude.json", "claude_desktop_config.json", "mcp.json"):
        offenders = [lit for lit in literals if client_config in lit]
        assert not offenders, (
            f"doctor references {client_config} in executable code; that is a "
            f"client's own configuration and must not reach a diagnostic bundle."
        )


def test_doctor_git_missing(tmp_path):
    """Verify git missing from PATH results in a graceful WARN."""
    with patch("shutil.which", return_value=None):
        res = check_git(tmp_path)
        assert res.status == "warn"
        assert "not available" in res.summary
        assert res.remediation is not None


def test_doctor_permissions_failure(tmp_path):
    """Verify unwriteable directory produces a FAIL result."""
    bad_dir = tmp_path / "nonexistent" / "nested"
    with patch("pathlib.Path.mkdir", side_effect=OSError("Permission denied")):
        res = check_permissions(bad_dir)
        assert res.status == "fail"
        assert "Cannot create directory" in res.summary


def test_doctor_permissions_cleans_up_whole_created_chain(tmp_path):
    """A probe against a nested, not-yet-created path must leave no trace."""
    target = tmp_path / "a" / "b" / "c"
    res = check_permissions(target)
    assert res.status == "ok"
    assert not (tmp_path / "a").exists()


def test_doctor_permissions_does_not_remove_preexisting_ancestors(tmp_path):
    """Only directories the probe itself created are removed."""
    (tmp_path / "a").mkdir()
    target = tmp_path / "a" / "b" / "c"
    res = check_permissions(target)
    assert res.status == "ok"
    assert (tmp_path / "a").exists()
    assert not (tmp_path / "a" / "b").exists()


def test_doctor_artifact_integrity_clean(tmp_path):
    """Test artifact integrity on a validly built mini-index."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("def hello(): return 42\n", encoding="utf-8")

    out = tmp_path / "out"
    assert main(["build", str(src), "-o", str(out)]) == 0

    res = check_artifact_integrity(out)
    assert res.status == "ok"
    assert "intact" in res.summary

    report = run_doctor(out)
    assert report.ok is True


def test_doctor_artifact_integrity_corrupt_manifest(tmp_path):
    """Verify corrupt manifest.json produces a FAIL with remediation."""
    agent_dir = tmp_path / ".r2g" / "agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "manifest.json").write_text("NOT_VALID_JSON{{{", encoding="utf-8")
    (agent_dir / "chunks.jsonl").write_text('{"id": "c1", "text": "foo"}\n', encoding="utf-8")

    res = check_artifact_integrity(tmp_path / ".r2g")
    assert res.status == "fail"
    assert any("Corrupt" in d for d in res.details)
    assert res.remediation is not None


def test_doctor_artifact_integrity_corrupt_chunks(tmp_path):
    """Verify corrupt chunks.jsonl produces a FAIL."""
    agent_dir = tmp_path / ".r2g" / "agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "manifest.json").write_text(
        '{"format": "repo2graph/1", "repo": "test"}', encoding="utf-8"
    )
    (agent_dir / "chunks.jsonl").write_text('{"id": "c1"}\nINVALID_CHUNK_JSON\n', encoding="utf-8")

    res = check_artifact_integrity(tmp_path / ".r2g")
    assert res.status == "fail"
    assert any("chunks.jsonl" in d for d in res.details)


def test_doctor_artifact_integrity_ignores_unrelated_agent_dir(tmp_path):
    """A top-level agent/ dir without repo2graph artifacts is ignored."""
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "notes.txt").write_text("unrelated", encoding="utf-8")
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")

    res = check_artifact_integrity(tmp_path)
    assert res.status == "ok"

    report = run_doctor(tmp_path)
    assert report.ok is True


def test_doctor_artifact_integrity_still_catches_corrupt_dot_r2g(tmp_path):
    """A missing manifest.json inside .r2g produces a FAIL."""
    agent_dir = tmp_path / ".r2g" / "agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "chunks.jsonl").write_text('{"id": "c1", "text": "foo"}\n', encoding="utf-8")

    res = check_artifact_integrity(tmp_path)
    assert res.status == "fail"
    assert any("Missing" in d for d in res.details)


def test_doctor_platform_encoding():
    res = check_platform_encoding()
    assert res.status == "ok"
    assert any("stdout encoding" in d for d in res.details)


def test_doctor_cli_text_and_json(tmp_path, capsys):
    """Test CLI repo2graph doctor execution with both human and JSON modes."""
    rc = main(["doctor", str(tmp_path)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "repo2graph doctor:" in captured.out
    assert "[OK]" in captured.out

    rc_json = main(["doctor", str(tmp_path), "--json"])
    assert rc_json == 0
    captured_json = capsys.readouterr()
    data = json.loads(captured_json.out)
    assert data["status"] == "ok"
    assert isinstance(data["checks"], list)


def test_doctor_python_version_warn_newer():
    """Verify that Python > 3.13 produces a WARN result (untested support matrix)."""
    with patch("sys.version_info", (3, 14, 0)):
        res = check_python()
        assert res.status == "warn"
        assert "newer" in res.summary
        assert res.remediation is not None
        assert "3.10 through 3.13" in res.remediation


def test_doctor_platform_support_glibc_failures_and_success():
    """Verify check_platform_support detects glibc < 2.34 as fail and glibc >= 2.34 as ok."""
    with patch("platform.system", return_value="Linux"):
        with patch("pathlib.Path.exists", return_value=False):
            with patch("platform.libc_ver", return_value=("glibc", "2.31")):
                res = check_platform_support()
                assert res.status == "fail"
                assert "below minimum" in res.summary
                assert "2.34" in res.summary
                assert res.remediation is not None

            with patch("platform.libc_ver", return_value=("glibc", "2.35")):
                res = check_platform_support()
                assert res.status == "ok"
                assert "2.35" in res.summary


def test_doctor_platform_support_musl_alpine_warning():
    """Verify check_platform_support warns when musl/Alpine is detected."""
    with patch("platform.system", return_value="Linux"):
        with patch("pathlib.Path.exists", return_value=True):
            res = check_platform_support()
            assert res.status == "warn"
            assert "musl" in res.summary or "Alpine" in res.summary
            assert res.remediation is not None
