"""Tests for the repo2graph benchmark and regression corpus suite."""

import json

from scripts.benchmark_runner import (
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


def test_tasks_definition_completeness():
    assert TASKS_FILE.exists()
    tasks = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    assert len(tasks) >= 20
    assert len(tasks) == 25

    required_keys = {
        "id",
        "repo",
        "category",
        "query",
        "expected_answer",
        "evidence_locations",
        "acceptable_variants",
    }
    repos_found = set()

    for t in tasks:
        assert required_keys.issubset(t.keys())
        assert len(t["evidence_locations"]) > 0
        repos_found.add(t["repo"])

    # Must cover all 5 archetypes
    assert repos_found == {
        "ts_app",
        "python_backend",
        "modular_monolith",
        "frontend_app",
        "dynamic_patterns",
    }


def test_corpus_fixtures_exist():
    assert CORPUS_DIR.is_dir()
    expected_repos = [
        "ts_app",
        "python_backend",
        "modular_monolith",
        "frontend_app",
        "dynamic_patterns",
    ]
    for r in expected_repos:
        repo_path = CORPUS_DIR / r
        assert repo_path.is_dir(), f"Missing corpus repo: {r}"
        files = list(repo_path.rglob("*"))
        assert len(files) >= 5, f"Corpus repo {r} has too few files ({len(files)})"


def test_benchmark_runner_executes_suite(tmp_path):
    summary = execute_benchmarks()
    assert summary.total_tasks == 25
    assert len(summary.repo_stats) == 5
    assert "repo2graph" in summary.workflow_metrics
    assert "ripgrep" in summary.workflow_metrics
    assert "agent_baseline" in summary.workflow_metrics

    r2g = summary.workflow_metrics["repo2graph"]
    assert r2g["accuracy_rate_pct"] >= 80.0
    assert r2g["mean_citation_accuracy"] >= 80.0
    assert r2g["mean_latency_ms"] > 0
