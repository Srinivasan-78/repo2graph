"""Tests for repo2graph doctor command and diagnostic probes."""

import json
from unittest.mock import patch


from repo2graph.cli import main
from repo2graph.doctor import (
    DoctorReport,
    check_artifact_integrity,
    check_git,
    check_mcp_sdk,
    check_package,
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


def test_doctor_permissions_cleans_up_whole_created_chain(tmp_path):
    """A probe against a nested, not-yet-created path must leave no trace.

    mkdir(parents=True) creates every missing ancestor; the probe must
    remove every one of them, not just the leaf it wrote the test file
    into.
    """
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


def test_doctor_artifact_integrity_ignores_unrelated_agent_dir(tmp_path):
    """A top-level agent/ dir that isn't ours must not be treated as a
    broken repo2graph index.

    "agent/" and "chunks.jsonl" are generic names used by unrelated
    projects (agent frameworks, ML repos with their own chunk files). Only
    a directory carrying repo2graph's own manifest.json -- or a ".r2g"
    subdirectory, which nothing else names -- counts as ours.
    """
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "notes.txt").write_text("unrelated", encoding="utf-8")
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")

    res = check_artifact_integrity(tmp_path)
    assert res.status == "ok"
    assert "No existing index" in res.summary

    report = run_doctor(tmp_path)
    assert report.ok is True


def test_doctor_artifact_integrity_still_catches_corrupt_dot_r2g(tmp_path):
    """A corrupt manifest.json *inside* a `.r2g` dir must still FAIL.

    `.r2g` is repo2graph's own default -o name and nothing else uses it, so
    it's still strong enough evidence to report corruption on, even without
    a manifest that parses.
    """
    agent_dir = tmp_path / ".r2g" / "agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "chunks.jsonl").write_text('{"id": "c1", "text": "foo"}\n', encoding="utf-8")
    # No manifest.json at all this time -- still corroborated by .r2g/agent/.

    res = check_artifact_integrity(tmp_path)
    assert res.status == "fail"
    assert any("Missing agent/manifest.json" in d for d in res.details)


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
    """Verify provider environment probe never prints secret values.

    Regression guard: the original masking (`val[:3]}...{val[-2:]}`, plus a
    `length:` field) leaked the key's tail characters and exact length --
    neither is caught by only checking the *middle* of the fixture key is
    absent, so this asserts on the tail and on "length:" directly.
    """
    secret_value = "AIzaSySuperSecretKey1234567890abcdef"
    monkeypatch.setenv("GEMINI_API_KEY", secret_value)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-AnotherSecretTokenVal999")

    res = check_provider_env()
    assert res.status == "ok"
    assert "2 provider(s) configured" in res.summary

    # Check the probe's own (path-free) details for the tail-character and
    # length leak directly -- the full rendered report below also contains
    # filesystem paths that can coincidentally contain a 2-char tail like
    # "ef", which would make that assertion meaningless against a random
    # tmp_path.
    detail_text = "\n".join(res.details)
    assert secret_value[-2:] not in detail_text
    assert "length:" not in detail_text

    report = run_doctor()
    rendered = report.format_text()

    # The actual secret substrings MUST NEVER appear
    assert "SuperSecretKey" not in rendered
    assert "AnotherSecretToken" not in rendered
    assert secret_value not in rendered
    # But configuration status is reported
    assert "GEMINI_API_KEY" in rendered
    assert "Configured" in rendered


def test_doctor_mcp_sdk_reports_fail_when_unusable(monkeypatch):
    """A successful `import mcp` alone is not evidence the SDK is usable --
    the check must actually probe `mcp.server.Server`, the thing serve()
    (mcp.py) needs, and FAIL when that's unavailable rather than reporting
    OK on the bare import.
    """
    import repo2graph.mcp as r2g_mcp

    def _boom():
        raise SystemExit("the installed mcp SDK is not supported by repo2graph-mcp")

    monkeypatch.setattr(r2g_mcp, "_require_sdk", _boom)

    res = check_mcp_sdk()
    assert res.status == "fail"
    assert "not usable" in res.summary
    assert res.remediation is not None


def test_doctor_mcp_sdk_ok_when_usable():
    """When mcp is absent this is a no-op OK; when installed in this dev
    env it must actually be usable by repo2graph-mcp (pyproject.toml pins
    `mcp` extra to a range serve() is written against)."""
    res = check_mcp_sdk()
    assert res.status == "ok"


def test_doctor_package_version_mismatch_warns(monkeypatch):
    """A module version that disagrees with the installed dist-info is
    worth a WARN, not a silent OK -- it usually means a shadowed editable
    install or a stale dist-info from a partial upgrade."""
    monkeypatch.setattr("importlib.metadata.version", lambda name: "999.999.999")
    res = check_package()
    assert res.status == "warn"
    assert "!=" in res.summary
    assert res.remediation is not None


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
