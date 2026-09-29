"""Tests for the repo2graph benchmark and regression corpus suite."""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.benchmark_runner import (  # noqa: E402
    CORPUS_DIR,
    TASKS_FILE,
    estimate_tokens,
    evaluate_evidence_presence,
    execute_benchmarks,
)


def test_estimate_tokens():
    assert estimate_tokens(40) == 10
    assert estimate_tokens(0) == 1
    assert estimate_tokens(400) == 100


def test_evaluate_evidence_presence_full_and_partial():
    evidence = ["src/server.ts:16", "src/routes/user.routes.ts:19-22"]

    # Text with both files
    text1 = "See [cite: src/server.ts:16] and [cite: src/routes/user.routes.ts:20]"
    score1, found1 = evaluate_evidence_presence(text1, evidence)
    assert score1 == 1.0
    assert len(found1) == 2

    # Text with one file
    text2 = "See [cite: src/server.ts:16] only"
    score2, found2 = evaluate_evidence_presence(text2, evidence)
    assert score2 == 0.5
    assert len(found2) == 1

    # Text with neither file
    text3 = "Unrelated output"
    score3, found3 = evaluate_evidence_presence(text3, evidence)
    assert score3 == 0.0
    assert len(found3) == 0


FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
FIXTURE_TASKS_FILE = FIXTURES_DIR / "benchmark_tasks.json"


def test_tasks_definition_completeness():
    assert FIXTURE_TASKS_FILE.exists()
    tasks = json.loads(FIXTURE_TASKS_FILE.read_text(encoding="utf-8"))
    assert len(tasks) >= 1

    required_keys = {
        "id",
        "repo",
        "category",
        "query",
        "expected_answer",
        "evidence_locations",
        "acceptable_variants",
    }
    for t in tasks:
        assert required_keys.issubset(t.keys())
        assert len(t["evidence_locations"]) > 0


def test_corpus_fixtures_exist():
    sample_repo = FIXTURES_DIR / "sample_repo"
    assert sample_repo.is_dir()
    files = list(sample_repo.rglob("*.py"))
    assert len(files) >= 3


def test_benchmark_runner_executes_suite(tmp_path):
    import shutil

    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURES_DIR / "sample_repo", corpus_dir / "sample_repo")

    summary = execute_benchmarks(tasks_path=FIXTURE_TASKS_FILE, corpus_path=corpus_dir)
    assert summary.total_tasks == 1
    assert "sample_repo" in summary.repo_stats
    assert "repo2graph" in summary.workflow_metrics
    assert "ripgrep" in summary.workflow_metrics
    assert "agent_baseline" in summary.workflow_metrics

    r2g = summary.workflow_metrics["repo2graph"]
    assert r2g["accuracy_rate_pct"] == 100.0
    assert r2g["mean_citation_accuracy"] == 100.0
    assert r2g["mean_latency_ms"] > 0


def test_bench_real_repos_labels_results_with_the_source_version(tmp_path, monkeypatch):
    """results.json said 2.1.0 for a 2.2.0 run: importlib.metadata read a stale
    editable dist-info. The label must come from the checked-out source."""
    import importlib.metadata
    import re

    from scripts import bench_real_repos as bench

    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "0.0.1-stale")
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf8")
    expected = re.search(r'(?m)^version\s*=\s*"([^"]+)"', pyproject).group(1)
    assert bench.source_version() == expected

    (tmp_path / "pyproject.toml").write_text(
        '[tool.x]\nversion = "no"\n\n[project]\nname = "repo2graph"\nversion = "9.8.7"\n',
        encoding="utf8",
    )
    assert bench.source_version(tmp_path) == "9.8.7"
    only_init = tmp_path / "other"
    (only_init / "repo2graph").mkdir(parents=True)
    (only_init / "repo2graph" / "__init__.py").write_text(
        'try:\n    x = 1\nexcept Exception:\n    __version__ = "1.2.3"\n', encoding="utf8"
    )
    assert bench.source_version(only_init) == "1.2.3"


def test_bench_real_repos_records_the_repo2graph_commit():
    import re
    import shutil

    import pytest

    from scripts import bench_real_repos as bench

    if shutil.which("git") is None or not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    prov = bench.source_commit()
    assert prov["commit"] and re.fullmatch(r"[0-9a-f]{40}", prov["commit"])
    assert isinstance(prov["dirty"], bool)
