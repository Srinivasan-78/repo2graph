"""Tests for repo2graph doctor command and diagnostic probes."""

import json
import os
from unittest.mock import patch

import pytest

from repo2graph.cli import main
from repo2graph.doctor import (
    DoctorReport,
    check_artifact_integrity,
    check_generated_code,
    check_git,
    check_ignored_paths,
    check_index_freshness,
    check_mcp_client_config,
    check_mcp_sdk,
    check_package,
    check_parsers,
    check_permissions,
    check_platform_encoding,
    check_provider_env,
    check_python,
    check_tree_sitter,
    check_uv,
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


# --------------------------------------------------------------------------
# uv / pip availability
# --------------------------------------------------------------------------


def test_doctor_uv_present_is_ok():
    with patch("shutil.which", lambda name: f"/usr/bin/{name}"):
        with patch("subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b"uv 0.5.0\n"
            res = check_uv()
    assert res.status == "ok"
    assert "uv is available" in res.summary


def test_doctor_uv_absent_but_pip_present_is_not_an_error():
    """A pip install is a fully supported path; only the uvx one-liner needs uv."""
    with patch("shutil.which", lambda name: None):
        with patch("repo2graph.doctor._pip_importable", return_value=True):
            res = check_uv()
    assert res.status == "ok"
    assert "uv not installed" in res.summary
    assert any("uvx" in d for d in res.details)


def test_doctor_neither_uv_nor_pip_fails_with_both_recovery_paths():
    with patch("shutil.which", lambda name: None):
        with patch("repo2graph.doctor._pip_importable", return_value=False):
            res = check_uv()
    assert res.status == "fail"
    assert res.remediation is not None
    assert "uv" in res.remediation and "ensurepip" in res.remediation


# --------------------------------------------------------------------------
# MCP client configuration
# --------------------------------------------------------------------------


def _write_mcp_config(tmp_path, entry, name=".mcp.json"):
    cfg = tmp_path / name
    cfg.write_text(json.dumps({"mcpServers": {"repo2graph": entry}}), encoding="utf-8")
    return cfg


def test_doctor_mcp_client_config_accepts_the_documented_block(tmp_path):
    """The exact block README.md and docs/mcp.md tell users to paste."""
    _write_mcp_config(
        tmp_path,
        {"command": "uvx", "args": ["--from", "repo2graph[mcp]", "repo2graph-mcp", str(tmp_path)]},
    )
    with patch("shutil.which", lambda name: f"/usr/bin/{name}"):
        res = check_mcp_client_config(tmp_path)
    assert res.status == "ok", res.details
    assert "look correct" in res.summary


def test_doctor_mcp_client_config_catches_uvx_without_the_mcp_extra(tmp_path):
    """`uvx --from repo2graph repo2graph-mcp` installs without the SDK.

    The server then exits telling the client to install an MCP SDK, which the
    user reads as "repo2graph is broken" -- so this is a fail, not a warn.
    """
    _write_mcp_config(
        tmp_path,
        {"command": "uvx", "args": ["--from", "repo2graph", "repo2graph-mcp", str(tmp_path)]},
    )
    with patch("shutil.which", lambda name: f"/usr/bin/{name}"):
        res = check_mcp_client_config(tmp_path)
    assert res.status == "fail"
    assert any("[mcp] extra" in d for d in res.details)
    assert res.remediation and "repo2graph[mcp]" in res.remediation


def test_doctor_mcp_client_config_catches_uvx_with_no_from_at_all(tmp_path):
    _write_mcp_config(tmp_path, {"command": "uvx", "args": ["repo2graph-mcp", str(tmp_path)]})
    with patch("shutil.which", lambda name: f"/usr/bin/{name}"):
        res = check_mcp_client_config(tmp_path)
    assert res.status == "fail"
    assert any("without `--from`" in d for d in res.details)


def test_doctor_mcp_client_config_catches_a_command_that_is_not_on_path(tmp_path):
    _write_mcp_config(tmp_path, {"command": "repo2graph-mcp", "args": [str(tmp_path)]})
    with patch("shutil.which", lambda name: None):
        res = check_mcp_client_config(tmp_path)
    assert res.status == "fail"
    assert any("not on PATH" in d for d in res.details)


def test_doctor_mcp_client_config_warns_on_a_relative_target(tmp_path):
    """MCP clients launch servers from an unspecified working directory."""
    _write_mcp_config(
        tmp_path,
        {"command": "uvx", "args": ["--from", "repo2graph[mcp]", "repo2graph-mcp", "./myrepo"]},
    )
    with patch("shutil.which", lambda name: f"/usr/bin/{name}"):
        res = check_mcp_client_config(tmp_path)
    assert res.status == "warn"
    assert any("relative" in d for d in res.details)


def test_doctor_mcp_client_config_warns_on_a_missing_target(tmp_path):
    _write_mcp_config(
        tmp_path,
        {
            "command": "uvx",
            "args": ["--from", "repo2graph[mcp]", "repo2graph-mcp", str(tmp_path / "gone")],
        },
    )
    with patch("shutil.which", lambda name: f"/usr/bin/{name}"):
        res = check_mcp_client_config(tmp_path)
    assert res.status == "warn"
    assert any("does not exist" in d for d in res.details)


def test_doctor_mcp_client_config_reports_invalid_json(tmp_path):
    """A trailing comma in the config is the most common MCP setup failure,
    and the client reports it only as "server failed to start"."""
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {"repo2graph": {},}}', encoding="utf-8")
    res = check_mcp_client_config(tmp_path)
    assert res.status == "fail"
    assert any("not valid JSON" in d for d in res.details)


def test_doctor_mcp_client_config_never_echoes_env_values(tmp_path):
    """An MCP client config is exactly where an API key lives, and this
    report is meant to be pasteable into a bug report."""
    secret = "sk-ant-SuperSecretFromTheMcpConfig"
    _write_mcp_config(
        tmp_path,
        {
            "command": "uvx",
            "args": ["--from", "repo2graph[mcp]", "repo2graph-mcp", str(tmp_path)],
            "env": {"ANTHROPIC_API_KEY": secret},
        },
    )
    with patch("shutil.which", lambda name: f"/usr/bin/{name}"):
        res = check_mcp_client_config(tmp_path)
    rendered = "\n".join([res.summary, *res.details, res.remediation or ""])
    assert secret not in rendered
    assert "SuperSecret" not in rendered


def test_doctor_mcp_client_config_absent_is_ok(tmp_path, monkeypatch):
    """No MCP client installed is not a problem for someone using the CLI."""
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path / "nohome"))
    monkeypatch.delenv("APPDATA", raising=False)
    res = check_mcp_client_config(tmp_path / "empty")
    assert res.status == "ok"
    assert "no MCP client config file found" in res.summary


# --------------------------------------------------------------------------
# Index freshness, parser coverage, ignored paths, generated code
# --------------------------------------------------------------------------


@pytest.fixture
def built_repo(tmp_path):
    """A small real repository with a real index built over it."""
    src = tmp_path / "proj"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "core.py").write_text(
        "TITLE = 'core module for the freshness fixture'\n\n\ndef run(x):\n    return x + 1\n",
        encoding="utf-8",
    )
    (src / "pkg" / "util.py").write_text(
        "HELPERS = ['a', 'b', 'c']  # module-level residue so this file gets a chunk\n\n\n"
        "def helper():\n    return HELPERS\n",
        encoding="utf-8",
    )
    assert main(["build", str(src), "-o", str(src / ".r2g")]) == 0
    return src


def test_doctor_freshness_is_ok_on_a_freshly_built_index(built_repo):
    res = check_index_freshness(built_repo)
    assert res.status == "ok", res.details
    assert "up to date" in res.summary


def test_doctor_freshness_detects_a_modified_file(built_repo):
    """mtime only chooses what to hash; the finding itself is a hash mismatch."""
    manifest = built_repo / ".r2g" / "agent" / "manifest.json"
    target = built_repo / "pkg" / "core.py"
    target.write_text("TITLE = 'core module, now with different content'\n", encoding="utf-8")
    cutoff = manifest.stat().st_mtime
    os.utime(target, (cutoff + 10, cutoff + 10))

    res = check_index_freshness(built_repo)
    assert res.status == "warn"
    assert "1 modified" in res.summary
    assert any("pkg/core.py" in d for d in res.details)
    assert res.remediation and "--incremental" in res.remediation


def test_doctor_freshness_ignores_a_file_that_was_only_touched(built_repo):
    """A checkout rewrites every mtime without changing a byte. Reporting
    that as a stale index would make the check useless on a fresh clone."""
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
    # Unbalanced brackets: tree-sitter parses it and reports ERROR nodes.
    (src / "broken.py").write_text(
        "BANNER = 'this module does not parse'\n\ndef broken(:\n    return [[[\n", encoding="utf-8"
    )
    assert main(["build", str(src), "-o", str(src / ".r2g")]) == 0

    res = check_parsers(src)
    assert res.status in ("warn", "fail")
    assert "parse errors" in res.summary
    assert res.remediation and "tree-sitter-language-pack" in res.remediation


def test_doctor_ignored_paths_reports_discovery_mode_and_counts(built_repo):
    res = check_ignored_paths(built_repo)
    assert res.status == "ok"
    assert any("Discovery mode" in d for d in res.details)
    assert any("indexed" in d for d in res.details)


def test_doctor_ignored_paths_warns_when_the_index_is_mostly_holes(tmp_path):
    """A repo whose source lives under a DEFAULT_SKIP_DIRS name indexes
    cleanly and answers every question with nothing."""
    idx = tmp_path / ".r2g" / "agent"
    idx.mkdir(parents=True)
    (idx / "manifest.json").write_text('{"format": "repo2graph/1"}', encoding="utf-8")
    (idx / "stats.json").write_text(
        json.dumps({"discovery": "walk", "files": 10, "skipped_vendor": 900}), encoding="utf-8"
    )
    res = check_ignored_paths(tmp_path)
    assert res.status == "warn"
    assert "910" in res.summary
    assert res.remediation and "explain-path" in res.remediation


def test_doctor_generated_code_flags_vendored_and_machine_written_files(tmp_path):
    src = tmp_path / "proj"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "core.py").write_text(
        "TITLE = 'the one hand-written module in this fixture'\n", encoding="utf-8"
    )
    # Three independent signals: a name, a suffix, and a content marker.
    (src / "package-lock.json").write_text(
        '{"name": "demo", "lockfileVersion": 3}\n', encoding="utf-8"
    )
    (src / "pkg" / "schema_pb2.py").write_text(
        "DESCRIPTOR = 'a protobuf stub, regenerated on every build'\n", encoding="utf-8"
    )
    (src / "pkg" / "bindings.py").write_text(
        "# Code generated by wrapgen. DO NOT EDIT.\n"
        + "PAYLOAD = 'x' * 200 + ' padding so this is the biggest source file'\n",
        encoding="utf-8",
    )
    assert main(["build", str(src), "-o", str(src / ".r2g")]) == 0

    res = check_generated_code(src)
    assert res.status == "warn"
    flagged = "\n".join(res.details)
    assert "package-lock.json" in flagged
    assert "schema_pb2.py" in flagged
    assert "bindings.py" in flagged, "the content-marker probe missed a generated header"
    assert res.remediation and "--exclude" in res.remediation


def test_doctor_generated_code_is_quiet_on_a_hand_written_repo(built_repo):
    res = check_generated_code(built_repo)
    assert res.status == "ok"
    assert "no generated or vendored files" in res.summary


def test_doctor_generated_code_suggests_a_fix_that_actually_excludes(tmp_path):
    """A remediation that excludes nothing is worse than none: the user runs
    it, the output does not change, and they conclude the finding was wrong.

    Caught a real one -- a `*_pb2.py` finding was rendered as
    `--exclude '**/_pb2.py'`, which matches only a file literally named
    `_pb2.py`. The check now names an exclusion group instead of
    reconstructing globs, so this expands the printed groups through the real
    `exclusions.globs_for` and matches them with the real `parse.matches_any`
    -- the same two functions `build --exclude-group` goes through.
    """
    import re

    from repo2graph.exclusions import globs_for
    from repo2graph.parse import matches_any

    src = tmp_path / "proj"
    (src / "pkg").mkdir(parents=True)
    (src / "vendor" / "lib").mkdir(parents=True)
    (src / "pkg" / "core.py").write_text(
        "TITLE = 'the one hand-written module in this fixture'\n", encoding="utf-8"
    )
    (src / "pkg" / "schema_pb2.py").write_text(
        "DESCRIPTOR = 'a protobuf stub, regenerated on every build'\n", encoding="utf-8"
    )
    (src / "vendor" / "lib" / "dep.py").write_text(
        "VENDORED = 'a copy of somebody else/s library, checked in'\n", encoding="utf-8"
    )
    (src / "package-lock.json").write_text('{"lockfileVersion": 3}\n', encoding="utf-8")
    assert main(["build", str(src), "-o", str(src / ".r2g"), "--include-vendor"]) == 0

    res = check_generated_code(src)
    assert res.status == "warn"

    named_groups = re.findall(r"--exclude-group (\w+)", res.remediation or "")
    assert named_groups, f"no --exclude-group in remediation: {res.remediation!r}"
    globs = globs_for(named_groups) + re.findall(r"--exclude '([^']+)'", res.remediation or "")

    # Hand-derived from the files written above -- never scraped back out of
    # `res.details`, which also contains the *patterns*, so a glob matching
    # its own pattern text would score as a match and the check would pass
    # while matching no real file.
    flagged_paths = {"pkg/schema_pb2.py", "vendor/lib/dep.py", "package-lock.json"}

    # Every flagged file must be covered, or the remediation only half-works
    # and the warning comes straight back on the next build.
    for path in flagged_paths:
        assert any(matches_any(path, [g]) for g in globs), (
            f"{path} was flagged but the suggested fix does not exclude it: {res.remediation!r}"
        )
    # And it must not take the hand-written module down with it.
    assert not any(matches_any("pkg/core.py", [g]) for g in globs), (
        f"the suggested fix would also exclude hand-written source: {res.remediation!r}"
    )


def test_doctor_bounded_scans_are_real_constants():
    """Every scan over an index built somewhere else has to be bounded."""
    from repo2graph import doctor

    assert doctor.MAX_NODE_LINES > 0
    assert doctor.MAX_FRESHNESS_FILES > 0
    assert doctor.MAX_GENERATED_CONTENT_SCANS > 0


def test_doctor_full_report_includes_every_first_run_check(tmp_path):
    """The checks the quickstart's troubleshooting table points at must all
    actually be in the report."""
    names = {c.name for c in run_doctor(tmp_path).checks}
    for expected in (
        "Python Version",
        "uv / pip Availability",
        "Tree-Sitter & Languages",
        "Index Freshness",
        "Parser Coverage",
        "Ignored Paths",
        "Generated / Vendored Code",
        "MCP SDK Compatibility",
        "MCP Client Configuration",
    ):
        assert expected in names, f"doctor no longer reports '{expected}'"
