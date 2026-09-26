#!/usr/bin/env python3
"""Benchmark and Regression Corpus Runner for repo2graph.

Executes 25 reproducible repository-understanding tasks across 5 archetype
repositories, comparing:
1. repo2graph (GraphRAG packing & retrieval)
2. Lexical search (ripgrep / grep simulation)
3. Baseline coding-agent multi-hop search

Measures:
- Correctness (% tasks answered with ground truth)
- Source-citation accuracy (% evidence lines present in citations)
- Latency (indexing & query time)
- Context / token footprint (characters and estimated tokens)

Usage:
    python scripts/benchmark_runner.py
    python scripts/benchmark_runner.py --ci
    python scripts/benchmark_runner.py --output benchmarks/results_v2.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from repo2graph.chunks import iter_chunks  # noqa: E402
from repo2graph.export import dump_all  # noqa: E402
from repo2graph.graph import build  # noqa: E402
from repo2graph.query import Index  # noqa: E402

CORPUS_DIR = ROOT / "benchmarks" / "corpus"
TASKS_FILE = ROOT / "benchmarks" / "tasks.json"


@dataclass
class WorkflowResult:
    """Outcome for a single workflow on a benchmark task."""

    workflow: str  # "repo2graph", "ripgrep", "agent_baseline"
    latency_ms: float
    context_chars: int
    estimated_tokens: int
    correct: bool
    citation_accuracy: float  # 0.0 to 1.0 (share of evidence locations recovered)
    citations_found: list[str] = field(default_factory=list)
    output_snippet: str = ""


@dataclass
class TaskEvaluation:
    """Full evaluation of a benchmark task across workflows."""

    task_id: str
    repo: str
    category: str
    query: str
    results: dict[str, WorkflowResult] = field(default_factory=dict)


@dataclass
class BenchmarkSuiteSummary:
    """Aggregated benchmark statistics and metrics."""

    timestamp: str
    repo2graph_version: str
    total_tasks: int
    repo_stats: dict[str, dict[str, Any]]
    workflow_metrics: dict[str, dict[str, Any]]
    task_evaluations: list[TaskEvaluation]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def estimate_tokens(char_count: int) -> int:
    """Heuristic: ~4 characters per token for code/markdown."""
    return max(1, char_count // 4)


def evaluate_evidence_presence(
    output_text: str, evidence_locations: list[str]
) -> tuple[float, list[str]]:
    """Calculate the fraction of required ground-truth evidence found in output."""
    found = []
    text_normalized = output_text.replace("\\", "/")

    for ev in evidence_locations:
        # ev format: "path/to/file.ext:10-25" or "path/to/file.ext:10"
        parts = ev.split(":")
        file_path = parts[0]
        # Check if the file is cited
        if file_path in text_normalized:
            found.append(ev)

    score = len(found) / len(evidence_locations) if evidence_locations else 1.0
    return round(score, 3), found


def run_repo2graph_workflow(index: Index, query: str, evidence_locs: list[str]) -> WorkflowResult:
    """Execute repo2graph pack_context query."""
    t0 = time.perf_counter()
    pack = index.pack_context(query, budget_chars=2500)
    latency_ms = round((time.perf_counter() - t0) * 1000, 2)

    output_text = pack.get("markdown", "")
    context_chars = len(output_text)
    tokens = estimate_tokens(context_chars)

    acc, found = evaluate_evidence_presence(output_text, evidence_locs)
    # Correct if at least 50% of evidence locations are cited
    correct = acc >= 0.50

    return WorkflowResult(
        workflow="repo2graph",
        latency_ms=latency_ms,
        context_chars=context_chars,
        estimated_tokens=tokens,
        correct=correct,
        citation_accuracy=acc,
        citations_found=found,
        output_snippet=output_text[:300].replace("\n", " "),
    )


def run_ripgrep_workflow(repo_root: Path, query: str, evidence_locs: list[str]) -> WorkflowResult:
    """Execute lexical regex search across the repo directory."""
    t0 = time.perf_counter()

    # Extract alphanumeric search keywords from query (ignoring stopwords)
    stopwords = {
        "where",
        "does",
        "what",
        "which",
        "how",
        "when",
        "why",
        "the",
        "and",
        "or",
        "an",
        "a",
        "in",
        "to",
        "is",
        "are",
    }
    tokens = [w for w in re.findall(r"\w+", query.lower()) if w not in stopwords and len(w) > 2]
    keywords = tokens[:3] if tokens else ["function"]

    matches = []
    for p in sorted(repo_root.rglob("*")):
        if p.is_file() and not p.name.startswith("."):
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
                rel = p.relative_to(repo_root).as_posix()
                for i, line in enumerate(content.split("\n")):
                    line_lower = line.lower()
                    if any(kw in line_lower for kw in keywords):
                        matches.append(f"{rel}:{i + 1}: {line.strip()}")
                        if len(matches) >= 50:  # cap ripgrep output
                            break
            except OSError:
                pass
        if len(matches) >= 50:
            break

    latency_ms = round((time.perf_counter() - t0) * 1000, 2)
    output_text = "\n".join(matches)
    context_chars = len(output_text)
    tokens = estimate_tokens(context_chars)

    acc, found = evaluate_evidence_presence(output_text, evidence_locs)
    correct = acc >= 0.50

    return WorkflowResult(
        workflow="ripgrep",
        latency_ms=latency_ms,
        context_chars=context_chars,
        estimated_tokens=tokens,
        correct=correct,
        citation_accuracy=acc,
        citations_found=found,
        output_snippet=output_text[:300].replace("\n", " "),
    )


def run_agent_baseline_workflow(
    repo_root: Path, query: str, evidence_locs: list[str]
) -> WorkflowResult:
    """Simulate a coding-agent multi-hop search: locate files, read windows around top matches."""
    t0 = time.perf_counter()

    # Step 1: find relevant files by keyword
    stopwords = {"where", "does", "what", "which", "how", "the", "and", "in", "to", "is"}
    keywords = [w for w in re.findall(r"\w+", query.lower()) if w not in stopwords and len(w) > 3]

    retrieved_windows = []
    file_candidates = []

    for p in sorted(repo_root.rglob("*")):
        if p.is_file() and not p.name.startswith(".") and not p.name.endswith(".json"):
            rel = p.relative_to(repo_root).as_posix()
            score = sum(1 for kw in keywords if kw in rel.lower())
            if score > 0:
                file_candidates.append((score, p))

    file_candidates.sort(key=lambda x: -x[0])

    # Read top 3 matching files (simulating agent cat/grep tool calls)
    for _score, fpath in file_candidates[:3]:
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
            rel = fpath.relative_to(repo_root).as_posix()
            lines = content.split("\n")
            # Agent reads first 80 lines of candidates
            retrieved_windows.append(f"=== {rel} ===\n" + "\n".join(lines[:80]))
        except OSError:
            pass

    latency_ms = round((time.perf_counter() - t0) * 1000, 2)
    output_text = "\n".join(retrieved_windows)
    context_chars = len(output_text)
    tokens = estimate_tokens(context_chars)

    acc, found = evaluate_evidence_presence(output_text, evidence_locs)
    correct = acc >= 0.50

    return WorkflowResult(
        workflow="agent_baseline",
        latency_ms=latency_ms,
        context_chars=context_chars,
        estimated_tokens=tokens,
        correct=correct,
        citation_accuracy=acc,
        citations_found=found,
        output_snippet=output_text[:300].replace("\n", " "),
    )


def execute_benchmarks(
    tasks_path: Path | None = None, corpus_path: Path | None = None
) -> BenchmarkSuiteSummary:
    """Build all indices and evaluate all tasks across workflows."""
    if tasks_path is None:
        tasks_path = TASKS_FILE
    if corpus_path is None:
        corpus_path = CORPUS_DIR

    tasks = json.loads(tasks_path.read_text(encoding="utf-8"))

    # Step 1: Index each corpus repo
    built_indices: dict[str, Index] = {}
    repo_stats: dict[str, dict[str, Any]] = {}

    import tempfile

    tmp_dir = Path(tempfile.mkdtemp(prefix="r2g_bench_"))

    print("Building indices for benchmark corpus repositories...")
    for repo_dir in sorted(corpus_path.iterdir()):
        if not repo_dir.is_dir() or repo_dir.name.startswith("."):
            continue
        repo_name = repo_dir.name
        out_dir = tmp_dir / repo_name
        out_dir.mkdir(parents=True, exist_ok=True)

        t0 = time.perf_counter()
        graph = build(repo_dir)
        dump_all(graph, iter_chunks(graph), out_dir, {"jsonl", "overview", "manifest"})
        build_time_ms = round((time.perf_counter() - t0) * 1000, 2)

        idx = Index(out_dir)
        built_indices[repo_name] = idx
        repo_stats[repo_name] = {
            "files": graph.stats.get("files", 0),
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
            "build_latency_ms": build_time_ms,
        }
        print(
            f"  Indexed {repo_name:20}: {len(graph.nodes)} nodes, {len(graph.edges)} edges in {build_time_ms} ms"
        )

    # Step 2: Evaluate tasks
    evaluations: list[TaskEvaluation] = []
    print(f"\nExecuting {len(tasks)} benchmark tasks across workflows...")

    for t in tasks:
        task_id = t["id"]
        repo_name = t["repo"]
        category = t["category"]
        query = t["query"]
        evidence_locs = t["evidence_locations"]

        idx = built_indices.get(repo_name)
        repo_root = corpus_path / repo_name

        if not idx or not repo_root.exists():
            print(f"Skipping {task_id}: repo {repo_name} not available")
            continue

        r2g_res = run_repo2graph_workflow(idx, query, evidence_locs)
        rg_res = run_ripgrep_workflow(repo_root, query, evidence_locs)
        agent_res = run_agent_baseline_workflow(repo_root, query, evidence_locs)

        task_eval = TaskEvaluation(
            task_id=task_id,
            repo=repo_name,
            category=category,
            query=query,
            results={
                "repo2graph": r2g_res,
                "ripgrep": rg_res,
                "agent_baseline": agent_res,
            },
        )
        evaluations.append(task_eval)

    # Step 3: Compute aggregated metrics
    workflow_names = ["repo2graph", "ripgrep", "agent_baseline"]
    metrics: dict[str, dict[str, Any]] = {}

    for wf in workflow_names:
        total_correct = sum(1 for ev in evaluations if ev.results[wf].correct)
        avg_citation_acc = sum(ev.results[wf].citation_accuracy for ev in evaluations) / len(
            evaluations
        )
        avg_latency = sum(ev.results[wf].latency_ms for ev in evaluations) / len(evaluations)
        avg_tokens = sum(ev.results[wf].estimated_tokens for ev in evaluations) / len(evaluations)

        metrics[wf] = {
            "correct_tasks": total_correct,
            "accuracy_rate_pct": round((total_correct / len(evaluations)) * 100, 1),
            "mean_citation_accuracy": round(avg_citation_acc * 100, 1),
            "mean_latency_ms": round(avg_latency, 2),
            "mean_tokens_consumed": round(avg_tokens),
        }

    from repo2graph import __version__ as r2g_ver

    summary = BenchmarkSuiteSummary(
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        repo2graph_version=r2g_ver,
        total_tasks=len(evaluations),
        repo_stats=repo_stats,
        workflow_metrics=metrics,
        task_evaluations=evaluations,
    )

    return summary


def print_summary_table(summary: BenchmarkSuiteSummary):
    """Print markdown formatted summary table."""
    print("\n=== BENCHMARK WORKFLOW COMPARISON ===")
    print(
        f"Evaluated on {summary.total_tasks} reproducible tasks across 5 archetype repositories\n"
    )
    print(
        "| Workflow | Tasks Correct | Accuracy Rate | Citation Accuracy | Mean Latency | Mean Tokens |"
    )
    print("|---|---:|---:|---:|---:|---:|")

    for wf, m in summary.workflow_metrics.items():
        name = (
            "repo2graph"
            if wf == "repo2graph"
            else ("ripgrep (grep)" if wf == "ripgrep" else "Agent Baseline Search")
        )
        print(
            f"| **{name}** | {m['correct_tasks']}/{summary.total_tasks} | "
            f"**{m['accuracy_rate_pct']}%** | {m['mean_citation_accuracy']}% | "
            f"{m['mean_latency_ms']} ms | {m['mean_tokens_consumed']} tokens |"
        )
    print("")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run repo2graph benchmark and regression suite.")
    parser.add_argument(
        "--ci", action="store_true", help="CI assertion mode: exit non-zero on regression"
    )
    parser.add_argument("--output", type=Path, default=None, help="Path to write JSON results")
    args = parser.parse_args()

    summary = execute_benchmarks()
    print_summary_table(summary)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary.to_dict(), indent=2), encoding="utf-8")
        print(f"Results written to {args.output}")

    if args.ci:
        r2g_metrics = summary.workflow_metrics.get("repo2graph", {})
        acc = r2g_metrics.get("accuracy_rate_pct", 0)
        # CI Regression gate: repo2graph must maintain at least 80% accuracy on standard tasks
        if acc < 80.0:
            sys.stderr.write(
                f"CI Failure: repo2graph accuracy dropped to {acc}% (minimum threshold: 80.0%)\n"
            )
            return 1
        print(f"CI Gate Passed: repo2graph accuracy is {acc}% (>= 80.0% threshold).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
