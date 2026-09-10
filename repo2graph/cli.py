# @authormark v1 -- do not remove (authorship watermark)
# Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
# Author: https://github.com/Srinivasan-78
# SPDX-License-Identifier: MIT
# Fingerprint: AMK1.fiIfsnUsnmQ0uPwuhZ-WrZ
"""repo2graph CLI: build a code graph, query it, export for RAG."""
import argparse
import json
import sys
from pathlib import Path

from .chunks import iter_chunks
from .export import dump_all, make_path, path as artifact_path
from .graph import build
from .viz import MAX_NODES

FORMATS = ("jsonl", "graphml", "cypher", "overview", "html")


def parse_formats(spec: str) -> set[str]:
    wanted = {f.strip() for f in spec.split(",") if f.strip()}
    unknown = sorted(wanted - set(FORMATS))
    if unknown:
        raise SystemExit(
            f"unknown format(s): {', '.join(unknown)}; choose from {', '.join(FORMATS)}")
    return wanted


def cmd_build(args):
    repo_path = Path(args.repo)
    if not repo_path.is_dir():
        raise SystemExit(f"error: repository directory does not exist or is not a directory: {repo_path}")
    formats = parse_formats(args.formats)
    g = build(repo_path, include=args.include, exclude=args.exclude,
              git_history=args.git_history, max_files=args.max_files, jobs=args.jobs)
    chunks = None if args.no_chunks else iter_chunks(g)   # a generator, streamed to disk
    outdir = Path(args.out)
    written, n_chunks = dump_all(g, chunks, outdir, formats, args.viz_nodes)
    print(json.dumps({"out": str(outdir), "written": written,
                      "stats": dict(g.stats), "chunks": n_chunks}, indent=2))


def cmd_github(args):
    from .fetch import index_github
    parse_formats(args.formats)  # fail before the clone, not after
    meta = index_github(
        args.repo, Path(args.out), ref=args.ref, depth=args.depth,
        git_history=args.git_history, formats=args.formats,
        include=args.include, exclude=args.exclude, max_files=args.max_files,
        keep_clone=args.keep_clone, token=args.token, viz_nodes=args.viz_nodes,
        jobs=args.jobs)
    print(json.dumps(meta, indent=2))


def _require_index(out: Path, name: str) -> Path:
    path = artifact_path(out, name)
    if not path.exists():
        # A directory holding some artifacts but not this one is a different
        # problem from an empty one: the build ran, it just did not write the
        # jsonl format. Say which, so the fix is not a guess.
        partial = out.exists() and any(out.rglob("*.jsonl"))
        if partial:
            raise SystemExit(
                f"index at {out} has no {name}: rebuild with "
                f"`repo2graph build <repo> -o {out} --formats jsonl`"
            )
        raise SystemExit(f"no index at {out}: run `repo2graph build <repo> -o {out}` first")
    # manifest.json is written last by dump_all; its absence next to real
    # artifacts means the build was interrupted before it finished.
    if name != "manifest.json" and not artifact_path(out, "manifest.json").exists():
        raise SystemExit(
            f"index at {out} has no manifest.json — the last build was interrupted "
            f"and the index may be incomplete; rebuild it")
    return path


def cmd_query(args):
    from .query import Index, format_pack
    out = Path(args.out)
    # Index reads all three, and `build --formats overview` writes chunks.jsonl
    # without the graph files -- checking only chunks turned that combination
    # into a FileNotFoundError traceback instead of this message.
    _require_index(out, "chunks.jsonl")
    _require_index(out, "nodes.jsonl")
    _require_index(out, "edges.jsonl")
    idx = Index(out)
    res = idx.retrieve(args.query, k=args.k, hops=args.hops, budget_chars=args.budget)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(format_pack(res))


def cmd_map(args):
    """Redraw graph.html from an index that is already on disk."""
    from .viz import LoadedGraph, write_html

    out = Path(args.out)
    _require_index(out, "nodes.jsonl")
    _require_index(out, "edges.jsonl")
    html = make_path(out, "graph.html")
    data = write_html(LoadedGraph(out), html, args.viz_nodes)
    print(json.dumps({"html": str(html),
                      "nodes": len(data["nodes"]), "edges": len(data["edges"]),
                      "of": data["totals"]}, indent=2))


def cmd_stats(args):
    print(_require_index(Path(args.out), "stats.json").read_text(encoding="utf8"))


def _nonneg(value: str) -> int:
    """argparse type: a base-10 int >= 0 (0 has a defined meaning for every
    numeric flag here; a negative silently mis-slices or breaks a subprocess)."""
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an integer, got {value!r}") from None
    if n < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {n}")
    return n


def main(argv=None):
    p = argparse.ArgumentParser(prog="repo2graph", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-o", "--out", default=".r2g")
    common.add_argument("--formats", default="jsonl,graphml,cypher,overview,html",
                        help="comma list: jsonl,graphml,cypher,overview,html")
    common.add_argument("--viz-nodes", type=_nonneg, default=MAX_NODES,
                        help="best-connected nodes to draw in graph.html (0 = no cap)")
    common.add_argument("--include", nargs="*", default=None, help="glob(s) to include")
    common.add_argument("--exclude", nargs="*", default=None, help="glob(s) to exclude")
    common.add_argument("--git-history", type=_nonneg, default=0,
                        help="add CO_CHANGE edges from the last N commits")
    common.add_argument("--max-files", type=_nonneg, default=0)
    common.add_argument("--jobs", type=_nonneg, default=0,
                        help="parser processes; 0 = one per core (capped at 8), 1 = serial")

    b = sub.add_parser("build", parents=[common], help="parse a repo into a graph + RAG chunks")
    b.add_argument("repo")
    b.add_argument("--no-chunks", action="store_true")
    b.set_defaults(func=cmd_build)

    gh = sub.add_parser("github", aliases=["gh"], parents=[common],
                        help="clone a GitHub repo (owner/repo or URL) and index it")
    gh.add_argument("repo", help="owner/repo, https://github.com/owner/repo or git@... remote")
    gh.add_argument("--ref", default=None, help="branch or tag (default: default branch)")
    gh.add_argument("--depth", type=_nonneg, default=0,
                    help="shallow clone depth; 0 = full history (needed for --git-history)")
    gh.add_argument("--keep-clone", default=None, help="clone here instead of a temp dir")
    gh.add_argument("--token", default=None,
                    help="GitHub token for private repos (else $GH_TOKEN/$GITHUB_TOKEN)")
    gh.set_defaults(func=cmd_github)

    q = sub.add_parser("query", help="graph-aware retrieval over a built index")
    q.add_argument("query")
    q.add_argument("-o", "--out", default=".r2g")
    q.add_argument("-k", type=_nonneg, default=8)
    q.add_argument("--hops", type=_nonneg, default=1)
    q.add_argument("--budget", type=_nonneg, default=24000)
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_query)

    m = sub.add_parser("map", help="redraw the HTML graph map from a built index")
    m.add_argument("-o", "--out", default=".r2g")
    m.add_argument("--viz-nodes", type=_nonneg, default=MAX_NODES,
                   help="how many of the best-connected nodes to draw (0 = no cap)")
    m.set_defaults(func=cmd_map)

    s = sub.add_parser("stats", help="print index stats")
    s.add_argument("-o", "--out", default=".r2g")
    s.set_defaults(func=cmd_stats)

    args = p.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
