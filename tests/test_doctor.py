"""Tests for repo2graph doctor command and diagnostic probes."""

import json
import os
from unittest.mock import patch

import pytest

from repo2graph.cli import main
from repo2graph.doctor import (
    DoctorReport,
    check_artifact_integrity,
    check_git,
    check_index_freshness,
    check_parsers,
    check_permissions,
    check_platform_encoding,
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


@pytest.fixture
def built_repo(tmp_path):
    src = tmp_path / "repo"
    src.mkdir()
    (src / "pkg").mkdir()
    (src / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (src / "pkg" / "core.py").write_text(
        "TITLE = 'core module'\ndef run():\n    return 42\n", encoding="utf-8"
    )
    (src / "pkg" / "util.py").write_text(
        "from .core import run\ndef helper():\n    return run()\n", encoding="utf-8"
    )
    assert main(["build", str(src), "-o", str(src / ".r2g")]) == 0
    return src


def test_doctor_freshness_is_ok_on_a_freshly_built_index(built_repo):
    res = check_index_freshness(built_repo)
    assert res.status == "ok", res.details
    assert "up to date" in res.summary


def test_doctor_freshness_detects_a_modified_file(built_repo):
    manifest = built_repo / ".r2g" / "agent" / "manifest.json"
    target = built_repo / "pkg" / "core.py"
    target.write_text("TITLE = 'core module, now with different content'\n", encoding="utf-8")
    cutoff = manifest.stat().st_mtime
    os.utime(target, (cutoff + 10, cutoff + 10))

    res = check_index_freshness(built_repo)
    assert res.status == "warn"
    assert "1 modified" in res.summary
    assert any("pkg/core.py" in d for d in res.details)


def test_doctor_freshness_ignores_a_file_that_was_only_touched(built_repo):
    manifest = built_repo / ".r2g" / "agent" / "manifest.json"
    cutoff = manifest.stat().st_mtime
    for rel in ("pkg/core.py", "pkg/util.py"):
        os.utime(built_repo / rel, (cutoff + 10, cutoff + 10))

    res = check_index_freshness(built_repo)
    assert res.status == "ok", res.details
    assert "modified" not in res.summary


def test_doctor_freshness_detects_added_and_removed_files(built_repo):
    (built_repo / "pkg" / "extra.py").write_text(
        "EXTRA = 'a brand new module that the index has never seen'\n", encoding="utf-8"
    )
    (built_repo / "pkg" / "util.py").unlink()

    res = check_index_freshness(built_repo)
    assert res.status == "warn"
    assert "1 added" in res.summary
    assert "1 removed" in res.summary


def test_doctor_freshness_without_an_index_is_ok(tmp_path):
    res = check_index_freshness(tmp_path)
    assert res.status == "ok"
    assert "no index found" in res.summary


def test_doctor_parser_coverage_reports_a_clean_parse(built_repo):
    res = check_parsers(built_repo)
    assert res.status == "ok"
    assert "no syntax errors" in res.summary


def test_doctor_parser_coverage_warns_on_parse_errors(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "ok.py").write_text(
        "GREETING = 'a valid module with enough residue'\n", encoding="utf-8"
    )
    (src / "broken.py").write_text(
        "BANNER = 'this module does not parse'\n\ndef broken(:\n    return [[[\n", encoding="utf-8"
    )
    assert main(["build", str(src), "-o", str(src / ".r2g")]) == 0

    res = check_parsers(src)
    assert res.status in ("warn", "fail")
    assert "parse error" in res.summary
