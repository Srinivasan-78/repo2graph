"""Tests for silent error prevention and audit fixes (W10-W24 / issue #448)."""

from __future__ import annotations

import json
import os
import subprocess
from unittest.mock import patch

import pytest

from repo2graph.cli import main
from repo2graph.integrity import get_source_provenance
from repo2graph.parse import (
    _is_index_dir,
    _walk_files,
    discover,
    is_binary,
    is_lfs_pointer,
)
from repo2graph.status import index_status


def test_w10_bom_utf16_parsing(tmp_path):
    """W10: UTF-16 files with BOM decode to UTF-8 and are parsed, not skipped as binary."""
    src = tmp_path / "repo"
    src.mkdir()
    py_content = "def special_utf16_func():\n    return 42\n"
    file_bytes = py_content.encode("utf-16")
    test_file = src / "test_mod.py"
    test_file.write_bytes(file_bytes)

    assert not is_binary(test_file)

    out = tmp_path / "idx"
    assert main(["build", str(src), "-o", str(out)]) == 0

    nodes_file = out / "agent" / "nodes.jsonl"
    nodes = [
        json.loads(line)
        for line in nodes_file.read_text(encoding="utf8").splitlines()
        if line.strip()
    ]
    symbol_names = [n.get("name") for n in nodes if n.get("type") == "symbol"]
    assert "special_utf16_func" in symbol_names


def test_w11_explicit_include_rescues_skip_dirs(tmp_path):
    """W11: Explicit --include rescues directories normally pruned by skip_dirs."""
    src = tmp_path / "repo"
    dist_dir = src / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "bundle.py").write_text("def bundled(): pass\n", encoding="utf8")
    (src / "main.py").write_text("import dist.bundle\n", encoding="utf8")

    out = tmp_path / "idx"
    assert main(["build", str(src), "-o", str(out), "--include", "dist/*"]) == 0

    nodes_file = out / "agent" / "nodes.jsonl"
    nodes = [
        json.loads(line)
        for line in nodes_file.read_text(encoding="utf8").splitlines()
        if line.strip()
    ]
    paths = [n.get("path") for n in nodes if n.get("type") == "file"]
    assert "dist/bundle.py" in paths


def test_w12_ambiguous_call_confidence_when_capped(tmp_path):
    """W12: When ambiguous call candidates exceed --max-call-candidates, confidence reflects the true candidate set size."""
    src = tmp_path / "repo"
    src.mkdir()
    for i in range(5):
        (src / f"worker_{i}.py").write_text("def process(): pass\n", encoding="utf8")
    (src / "caller.py").write_text("def run():\n    process()\n", encoding="utf8")

    out = tmp_path / "idx"
    assert main(["build", str(src), "-o", str(out), "--max-call-candidates", "1"]) == 0

    edges_file = out / "agent" / "edges.jsonl"
    edges = [
        json.loads(line)
        for line in edges_file.read_text(encoding="utf8").splitlines()
        if line.strip()
    ]
    call_edges = [e for e in edges if e.get("type") == "CALLS"]
    assert len(call_edges) == 1
    assert call_edges[0]["confidence"] == pytest.approx(0.2)
    assert call_edges[0].get("capped_candidates") is True


def test_w14_submodule_directory_pruned(tmp_path):
    """W14: Submodule directory containing .git is pruned by _walk_files."""
    src = tmp_path / "repo"
    src.mkdir()
    (src / "root.py").write_text("x = 1\n", encoding="utf8")
    submod = src / "vendor_submodule"
    submod.mkdir()
    (submod / ".git").write_text("gitdir: ../.git/modules/sub\n", encoding="utf8")
    (submod / "sub.py").write_text("y = 2\n", encoding="utf8")

    files = list(_walk_files(src, skip_dirs=set()))
    file_names = [f.name for f in files]
    assert "root.py" in file_names
    assert "sub.py" not in file_names


def test_w16_lfs_pointer_skipped(tmp_path):
    """W16: Git LFS pointer files are detected and skipped with skipped_lfs."""
    src = tmp_path / "repo"
    src.mkdir()
    lfs_content = (
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:4d7a214614ab2935c943f9e3eedf42d58624ec753ac960e7bd9e51f53cfb61f8\n"
        "size 12345\n"
    )
    lfs_file = src / "model.py"
    lfs_file.write_text(lfs_content, encoding="utf8")

    assert is_lfs_pointer(lfs_file.read_bytes())

    stats: dict[str, int] = {}
    discovered = list(discover(src, stats=stats))
    assert len(discovered) == 0
    assert stats.get("skipped_lfs") == 1


def test_w20_planted_manifest_does_not_drop_directory(tmp_path):
    """W20: A directory containing manifest.json without nodes.jsonl is not treated as an index directory."""
    pkg = tmp_path / "my_pkg"
    pkg.mkdir()
    (pkg / "manifest.json").write_text('{"name": "my_pkg"}', encoding="utf8")
    assert not _is_index_dir(pkg)


def test_w22_git_status_failure_handles_dirty_cleanly(tmp_path):
    """W22: When git status fails or times out, provenance records dirty: None and status_error: True."""
    with patch("repo2graph.integrity.run_git") as mock_run_git:

        def fake_run_git(root, args, **kwargs):
            if args == ["rev-parse", "HEAD"]:
                return "abcdef123456"
            if args == ["status", "--porcelain"]:
                return None
            return None

        mock_run_git.side_effect = fake_run_git
        prov = get_source_provenance(tmp_path)
        assert prov.get("commit") == "abcdef123456"
        assert prov.get("dirty") is None
        assert prov.get("status_error") is True


def test_w23_committed_index_reports_current(tmp_path):
    """W23: Committing index artifacts advances HEAD without invalidating index freshness."""
    src = tmp_path / "repo"
    src.mkdir()
    (src / "app.py").write_text("def run(): pass\n", encoding="utf8")

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e",
    }
    subprocess.run(["git", "-C", str(src), "init", "-q"], check=True, env=env)
    subprocess.run(["git", "-C", str(src), "add", "app.py"], check=True, env=env)
    subprocess.run(["git", "-C", str(src), "commit", "-qm", "initial"], check=True, env=env)

    out = src / ".r2g"
    assert main(["build", str(src), "-o", str(out)]) == 0

    rep = index_status(out)
    assert rep["freshness"]["status"] == "current"

    subprocess.run(["git", "-C", str(src), "add", ".r2g"], check=True, env=env)
    subprocess.run(["git", "-C", str(src), "commit", "-qm", "commit index"], check=True, env=env)

    rep_after = index_status(out)
    assert rep_after["freshness"]["status"] == "current"
    notes = " ".join(rep_after["freshness"]["notes"])
    assert "only to commit index artifacts" in notes


def test_w24_min_confidence_validation_in_explain_retrieval(tmp_path):
    """W24: explain retrieval rejects out-of-range --min-confidence / --min-conf values."""
    src = tmp_path / "repo"
    src.mkdir()
    (src / "app.py").write_text("def run(): pass\n", encoding="utf8")
    out = tmp_path / "idx"
    assert main(["build", str(src), "-o", str(out)]) == 0

    with pytest.raises(SystemExit) as excinfo:
        main(["explain", "retrieval", "--index", str(out), "--query", "run", "--min-conf", "1.5"])
    assert excinfo.value.code != 0

    with pytest.raises(SystemExit) as excinfo2:
        main(
            [
                "explain",
                "retrieval",
                "--index",
                str(out),
                "--query",
                "run",
                "--min-confidence",
                "-0.1",
            ]
        )
    assert excinfo2.value.code != 0


def test_total_symbols_in_stats_json(tmp_path):
    """stats.json includes total_symbols matching symbol:* counts."""
    src = tmp_path / "repo"
    src.mkdir()
    (src / "app.py").write_text("def a(): pass\ndef b(): pass\nclass C: pass\n", encoding="utf8")
    out = tmp_path / "idx"
    assert main(["build", str(src), "-o", str(out)]) == 0

    stats_file = out / "agent" / "stats.json"
    stats = json.loads(stats_file.read_text(encoding="utf8"))
    assert "total_symbols" in stats
    expected = sum(v for k, v in stats.items() if k.startswith("symbol:") and isinstance(v, int))
    assert stats["total_symbols"] == expected
    assert stats["total_symbols"] >= 3
