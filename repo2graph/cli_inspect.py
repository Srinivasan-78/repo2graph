"""`repo2graph` commands that inspect an index or explain a result: map, stats, diff, explain-path, explain, index-status, bug-report, doctor, demo, completion (#315)."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .cli import _effective_exclude, _emit, _require_index, _with_repo_config
from .events import diagnostic
from .export import make_path, register_written
from .export import rel as artifact_rel


def cmd_map(args: argparse.Namespace) -> int | None:
    """Redraw graph.html from an index that is already on disk."""
    from .viz import LoadedGraph, write_html

    out = Path(args.out)
    _require_index(out, "nodes.jsonl")
    _require_index(out, "edges.jsonl")
    html = make_path(out, "graph.html")
    data = write_html(LoadedGraph(out), html, args.viz_nodes)
    register_written(out, [artifact_rel("graph.html")])
    _emit(
        json.dumps(
            {
                "html": str(html),
                "nodes": len(data["nodes"]),
                "edges": len(data["edges"]),
                "of": data["totals"],
            },
            indent=2,
        )
    )
    return None


def format_stats_summary(data: dict[str, Any]) -> str:
    lines = [
        "repo2graph Index Quality & Coverage Summary",
        "===========================================",
        f"Files Discovered:        {data.get('files', 0)}",
        f"Files Parsed:            {data.get('parsed', 0)}",
        f"Parse Errors:            {data.get('parse_errors', 0)} (in {data.get('files_with_parse_errors', 0)} files)",
        "",
        "Graph Structure:",
        f"  Nodes:                 {data.get('nodes', 0)}",
        f"  Edges:                 {data.get('edges', 0)}",
        "",
        "Call Resolution Quality:",
        f"  Scoped / Tiered:       {data.get('calls_scoped', 0)}",
        f"  Unique Global:         {data.get('calls_unique_global', 0)}",
        f"  Ambiguous:             {data.get('calls_ambiguous', data.get('ambiguous_calls', 0))}",
        f"  External / Unresolved: {data.get('calls_external', 0)}",
        f"  Untyped Receiver:      {data.get('calls_untyped_receiver', 0)}",
        "",
        "Imports & Bases:",
        f"  Imports Resolved:      {data.get('imports_resolved', 0)}",
        f"  Imports External:      {data.get('imports_unresolved', 0)}",
        f"  Unresolved Bases:      {data.get('unresolved_bases', 0)}",
        "",
        "Exclusions:",
        f"  Dotfiles/Dirs:         {data.get('skipped_dotfile', 0)}",
        f"  Vendor Dirs:           {data.get('skipped_vendor', 0)}",
        f"  Secrets:               {data.get('skipped_secret', 0)}",
        f"  Binary Files:          {data.get('skipped_binary', 0)}",
        f"  Too Large:             {data.get('skipped_too_large', 0)}",
        f"  Gitignored:            {data.get('skipped_gitignore', 0)}",
    ]
    return "\n".join(lines)


def cmd_stats(args: argparse.Namespace) -> int | None:
    raw = _require_index(Path(args.out), "stats.json").read_text(encoding="utf8")
    if getattr(args, "json", False) or getattr(args, "format", "json") == "json":
        _emit(raw)
    else:
        try:
            data = json.loads(raw)
            _emit(format_stats_summary(data))
        except (ValueError, TypeError, KeyError):
            _emit(raw)
    return None


def _explain_index_dir(repo: Path, rel: str, out: str | None) -> dict[str, Any] | None:
    """The rule discover() applies *first*: never index a repo2graph output dir.

    `explain_path` does not model it, so `explain-path .r2g/agent/nodes.jsonl`
    answered INCLUDED for a file no build ever indexes. Two shapes, checked in
    discover()'s order: the `-o` directory this build would write to, and any
    directory holding a repo2graph `agent/manifest.json` (an earlier build).
    """
    from .discovery import _is_index_dir, _output_rel_prefix
    from .parse import BuildConfig

    root = repo.resolve()
    parts = tuple(Path(rel).parts)
    out_parts = _output_rel_prefix(root, BuildConfig(output_dir=out)) if out else None
    if out_parts is not None and parts[: len(out_parts)] == out_parts:
        return {
            "included": False,
            "rule": "output_dir",
            "reason": f"Path is inside the output directory -o {out}; a build never "
            "indexes its own artifacts",
            "precedence_step": 2,
        }
    for i in range(1, len(parts)):
        if _is_index_dir(root.joinpath(*parts[:i])):
            return {
                "included": False,
                "rule": "index_dir",
                "reason": f"Path is inside '{'/'.join(parts[:i])}', a repo2graph index "
                "(it holds agent/manifest.json); builds never index an index",
                "precedence_step": 2,
            }
    return None


def cmd_diff(args: argparse.Namespace) -> int | None:
    """What changed structurally between two index directories or PR blast radius (#395, #508)."""
    base_commit = getattr(args, "base_commit", None)
    pr_mode = getattr(args, "pr_mode", False)
    affected_tests = getattr(args, "affected_tests", False)

    if base_commit or pr_mode or affected_tests:
        from .graphdiff import (
            find_affected_tests,
            render_pr_summary_markdown,
            render_pr_summary_text,
        )

        repo_path = Path(getattr(args, "repo", "."))
        index_dir: Path | None = None
        if getattr(args, "to_dir", None):
            index_dir = Path(args.to_dir)
        elif getattr(args, "from_dir", None):
            p = Path(args.from_dir)
            if (p / "agent" / "nodes.jsonl").exists() or (p / "nodes.jsonl").exists():
                index_dir = p
        if index_dir is None and getattr(args, "out", None):
            index_dir = Path(args.out)
        if index_dir is None:
            index_dir = repo_path / ".r2g"

        _require_index(index_dir, "nodes.jsonl")
        _require_index(index_dir, "edges.jsonl")

        old_idx = (
            Path(args.from_dir)
            if (getattr(args, "from_dir", None) and getattr(args, "to_dir", None))
            else None
        )

        try:
            pr_result = find_affected_tests(
                index_dir,
                base_commit=base_commit,
                repo_path=repo_path,
                old_index_dir=old_idx,
                max_hops=getattr(args, "affected_tests_max_hops", 4),
                min_confidence=getattr(args, "affected_tests_min_confidence", 0.0),
                follow_shadowed_builtins=getattr(
                    args, "affected_tests_follow_shadowed_builtins", False
                ),
            )
        except ValueError as exc:
            raise SystemExit(f"error: {exc}") from None

        if getattr(args, "github_step_summary", False):
            summary_md = render_pr_summary_markdown(pr_result)
            step_summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
            if step_summary_file:
                with open(step_summary_file, "a", encoding="utf-8") as f:
                    f.write(summary_md + "\n")
            else:
                _emit(summary_md)

        if affected_tests and args.format == "text" and not pr_mode:
            # This is the shape CI pipes straight into a test runner, so an
            # incomplete selection has to be loud on stderr -- stdout stays
            # exactly the bare file list the caller is parsing.
            if pr_result.get("truncated"):
                diagnostic(
                    "warning: this affected-test selection is incomplete "
                    f"({pr_result.get('incomplete_reason', '')}); run the full suite "
                    "rather than only these files"
                )
            _emit(" ".join(pr_result["affected_tests"]))
        elif args.format == "json":
            _emit(json.dumps(pr_result, indent=2))
        else:
            _emit(render_pr_summary_text(pr_result))
        return 0

    if not getattr(args, "from_dir", None) or not getattr(args, "to_dir", None):
        raise SystemExit(
            "error: the following arguments are required: FROM, TO (or specify --base-commit / --pr-mode / --affected-tests)"
        )

    from .graphdiff import diff_indexes, render_text

    old, new = Path(args.from_dir), Path(args.to_dir)
    for index_dir in (old, new):
        _require_index(index_dir, "nodes.jsonl")
        _require_index(index_dir, "edges.jsonl")
    try:
        delta = diff_indexes(old, new)
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from None
    delta["confidence_changed_edges_count"] = len(delta["confidence_changed_edges"])
    if args.format == "json" and not args.include_confidence:
        delta["confidence_changed_edges"] = []
    if args.format == "json":
        _emit(json.dumps(delta, indent=2, default=str))
    else:
        _emit(
            f"graph diff: {old} -> {new}\n"
            + render_text(delta, limit=args.limit, show_confidence=args.include_confidence)
        )
    return 0


def cmd_explain_path(args: argparse.Namespace) -> int | None:
    from .discovery import explain_path
    from .parse import BuildConfig

    repo_path = Path(args.repo)
    if not repo_path.is_dir():
        raise SystemExit(f"error: repository directory does not exist: {repo_path}")

    args, applied = _with_repo_config(args, repo_path)
    config = BuildConfig(
        include_vendor=args.include_vendor,
        include_secrets=args.include_secrets,
        chunk_large_files=args.chunk_large_files,
        max_file_bytes=int(args.max_file_mb * 1_000_000),
        extra_exclude_dirs=args.extra_exclude_dirs or [],
        extra_secret_keywords=args.extra_secret_keywords or [],
        extra_secret_dirs=args.extra_secret_dirs or [],
    )
    res = explain_path(
        repo_path,
        args.path,
        config=config,
        include_globs=args.include or None,
        exclude_globs=_effective_exclude(args),
        repo_config=applied,
    )
    if res["rule"] not in ("outside_root", "not_found"):
        index_hit = _explain_index_dir(repo_path, res["relative_path"], getattr(args, "out", None))
        if index_hit is not None:
            res.update(index_hit)
    if getattr(args, "json", False):
        _emit(json.dumps(res, indent=2))
    else:
        status_str = "INCLUDED" if res["included"] else "EXCLUDED"
        lines = [
            f"Path:            {res['path']}",
            f"Relative Path:   {res['relative_path']}",
            f"Decision:        {status_str}",
            f"Precedence Step: {res['precedence_step']}",
            f"Rule:            {res['rule']}",
            f"Reason:          {res['reason']}",
        ]
        _emit("\n".join(lines))
    return 0


def cmd_bug_report(args: argparse.Namespace) -> int:
    """Assemble a diagnostic bundle that is safe to paste into a public issue."""
    from .bugreport import build_report, format_report

    report = build_report(
        Path(args.out),
        repo=getattr(args, "repo", None),
        category=getattr(args, "category", None),
        include_paths=getattr(args, "include_paths", False),
    )
    text = json.dumps(report, indent=2) if getattr(args, "json", False) else format_report(report)

    dest = getattr(args, "write", None)
    if dest:
        try:
            Path(dest).write_text(text + "\n", encoding="utf8", newline="\n")
        except OSError as exc:
            raise SystemExit(f"error: could not write {dest}: {exc}") from None
        _emit(f"wrote {dest}")
        _emit("Review it before posting -- it is yours to check, not ours to promise.")
    else:
        _emit(text)
    return 0


def cmd_index_status(args: argparse.Namespace) -> int:
    """Report what an index contains and whether it still matches the tree."""
    from .status import format_status, index_status

    try:
        report = index_status(Path(args.out), repo=getattr(args, "repo", None))
    except FileNotFoundError as exc:
        raise SystemExit(f"error: {exc}") from None
    except (OSError, ValueError) as exc:
        raise SystemExit(
            f"error: could not read the index at {args.out}: {exc}\n"
            f"       check it with `repo2graph doctor {args.out}`"
        ) from None

    if getattr(args, "json", False):
        _emit(json.dumps(report, indent=2))
    else:
        _emit(format_status(report))

    # --check makes this a CI gate: "the committed index matches this commit"
    # is a reviewable property, and a non-zero exit is how a workflow asserts
    # it. Without the flag a stale index is a fact to report, not a failure.
    if getattr(args, "check", False) and report["freshness"]["status"] != "current":
        return 1
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Index the bundled demo repository and answer the five starter questions."""
    from .demo import run_demo

    try:
        run_demo(
            outdir=getattr(args, "out", None),
            keep=getattr(args, "keep", False),
            brief=not getattr(args, "full", False),
            emit=_emit,
        )
    except OSError as exc:
        # The only failure a first-run user actually hits here: no writable
        # temp directory, or a --out they cannot create. Say which path and
        # what to do, rather than a traceback on their first command.
        raise SystemExit(
            f"error: the demo could not write its scratch repository: {exc}\n"
            "       pass a writable directory explicitly: repo2graph demo --out ./r2g-demo"
        ) from None
    return 0


def cmd_doctor(args: argparse.Namespace) -> int | None:
    from .doctor import run_doctor

    report = run_doctor(args.path)
    if getattr(args, "json", False):
        _emit(json.dumps(report.to_dict(), indent=2))
    else:
        _emit(report.format_text())
    return 0 if report.ok else 1


def cmd_completion(args: argparse.Namespace) -> int:
    """Print shell completion script or setup instructions."""
    shell = getattr(args, "shell", "bash")
    if shell == "bash":
        _emit(
            "# Bash completion for repo2graph\n"
            '# Prerequisite: pip install "repo2graph[completion]"\n'
            'eval "$(register-python-argcomplete repo2graph)"\n'
        )
    elif shell == "zsh":
        _emit(
            "# Zsh completion for repo2graph\n"
            '# Prerequisite: pip install "repo2graph[completion]"\n'
            "autoload -U bashcompinit && bashcompinit\n"
            'eval "$(register-python-argcomplete repo2graph)"\n'
        )
    elif shell == "fish":
        _emit(
            "# Fish completion for repo2graph\n"
            '# Prerequisite: pip install "repo2graph[completion]"\n'
            "register-python-argcomplete --shell fish repo2graph | source\n"
        )
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    """Explain edges, nodes, or retrieval results."""
    from .explain import (
        explain_edge,
        explain_node,
        explain_retrieval,
        format_explain_edge,
        format_explain_node,
        format_explain_retrieval,
    )

    outdir = Path(getattr(args, "out", ".r2g"))
    subcmd = getattr(args, "explain_cmd", None)
    is_json = getattr(args, "json", False)

    if subcmd == "edge":
        res = explain_edge(outdir, args.src, args.dst)
        _emit(json.dumps(res, indent=2) if is_json else format_explain_edge(res))
        return 0 if res.get("found") else 1
    if subcmd == "node":
        res = explain_node(outdir, args.node_id)
        _emit(json.dumps(res, indent=2) if is_json else format_explain_node(res))
        return 0 if res.get("found") else 1
    # `retrieval`: the explain subparsers are required, so nothing else reaches here.
    res = explain_retrieval(
        outdir,
        args.query,
        k=args.k,
        hops=args.hops,
        min_confidence=args.min_confidence,
        # The same query-time default as rag/query: a trace must not name
        # a `.env` that the retrieval it explains would never return.
        exclude_secrets=not args.include_secrets,
        extra_secret_keywords=args.extra_secret_keywords or None,
        extra_secret_dirs=args.extra_secret_dirs or None,
    )
    _emit(json.dumps(res, indent=2) if is_json else format_explain_retrieval(res))
    return 0
