"""Tests for repo2graph doctor command and diagnostic probes."""

import json
from unittest.mock import patch


from repo2graph.cli import main
from repo2graph.doctor import (
    DoctorReport,
    check_artifact_integrity,
    check_git,
    check_permissions,
    check_platform_encoding,
    check_provider_env,
    check_python,
    check_tree_sitter,
    check_vectors,
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
    assert len(d["checks"]) >= 8


def test_doctor_python_version_failure():
    """Verify that Python < 3.10 produces a FAIL result with remediation."""
    with patch("sys.version_info", (3, 9, 7)):
        res = check_python()
        assert res.status == "fail"
        assert "unsupported" in res.summary
        assert res.remediation is not None
        assert "3.10" in res.remediation


def test_doctor_tree_sitter_missing():
    """Verify tree-sitter import failure produces a FAIL result."""
    with patch.dict("sys.modules", {"tree_sitter": None}):
        with patch("builtins.__import__", side_effect=ImportError("No tree_sitter")):
            res = check_tree_sitter()
            assert res.status == "fail"
            assert "missing" in res.summary
            assert res.remediation is not None


def test_doctor_git_missing(tmp_path):
    """Verify git missing from PATH results in a graceful WARN."""
    with patch("subprocess.run", side_effect=FileNotFoundError("git not found")):
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
    assert any("Corrupt manifest.json" in d for d in res.details)
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


def test_doctor_vector_checks(tmp_path):
    """Verify vector presence, missing companions, and desync checks."""
    agent_dir = tmp_path / ".r2g" / "agent"
    agent_dir.mkdir(parents=True)

    # 1. No vectors -> OK
    res = check_vectors(tmp_path / ".r2g")
    assert res.status == "ok"

    # 2. Missing companion (vectors.npy without vectors.meta.json)
    (agent_dir / "vectors.npy").write_bytes(b"\x93NUMPY\x01\x00")
    res = check_vectors(tmp_path / ".r2g")
    assert res.status == "warn"
    assert "missing companion" in res.summary

    # 3. Vector count desync with chunks.jsonl
    (agent_dir / "vectors.meta.json").write_text(
        json.dumps({"model_id": "test-model", "dim": 384, "chunk_ids": ["c1", "c2"]}),
        encoding="utf-8",
    )
    (agent_dir / "chunks.jsonl").write_text('{"id": "c1", "text": "one"}\n', encoding="utf-8")
    res = check_vectors(tmp_path / ".r2g")
    assert res.status == "warn"
    assert "out of sync" in res.summary


def test_doctor_provider_env_never_leaks_secrets(monkeypatch):
    """Verify provider environment probe never prints secret values."""
    secret_value = "AIzaSySuperSecretKey1234567890abcdef"
    monkeypatch.setenv("GEMINI_API_KEY", secret_value)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-AnotherSecretTokenVal999")

    res = check_provider_env()
    assert res.status == "ok"
    assert "2 provider(s) configured" in res.summary

    report = run_doctor()
    rendered = report.format_text()

    # The actual secret substrings MUST NEVER appear
    assert "SuperSecretKey" not in rendered
    assert "AnotherSecretToken" not in rendered
    assert secret_value not in rendered
    # But configuration status is reported
    assert "GEMINI_API_KEY" in rendered
    assert "Configured" in rendered


def test_doctor_platform_encoding():
    """Verify platform encoding probe executes without error."""
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
