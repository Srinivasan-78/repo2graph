"""repo2graph CLI: build a code graph, query it, export for RAG."""
import argparse
import json
import sys
from pathlib import Path

from .chunks import build_chunks
from .export import dump_all
from .graph import build
from .layout import make_path
from .layout import path as artifact_path
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
    formats = parse_formats(args.formats)
    g = build(Path(args.repo), include=args.include, exclude=args.exclude,
              git_history=args.git_history, max_files=args.max_files, jobs=args.jobs)
    chunks = None if args.no_chunks else build_chunks(g)
    outdir = Path(args.out)
    written = dump_all(g, chunks, outdir, formats, args.viz_nodes)
    print(json.dumps({"out": str(outdir), "written": written,
                      "stats": dict(g.stats),
                      "chunks": len(chunks) if chunks else 0}, indent=2))


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
        raise SystemExit(f"no index at {out}: run `repo2graph build <repo> -o {out}` first")
    return path


def cmd_query(args):
    from .query import Index, format_pack
    _require_index(Path(args.out), "chunks.jsonl")
    idx = Index(Path(args.out))
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


def main(argv=None):
    p = argparse.ArgumentParser(prog="repo2graph", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="parse a repo into a graph + RAG chunks")
    b.add_argument("repo")
    b.add_argument("-o", "--out", default=".r2g")
    b.add_argument("--formats", default="jsonl,graphml,cypher,overview,html",
                   help="comma list: jsonl,graphml,cypher,overview,html")
    b.add_argument("--viz-nodes", type=int, default=MAX_NODES,
                   help="best-connected nodes to draw in graph.html")
    b.add_argument("--include", nargs="*", default=None, help="glob(s) to include")
    b.add_argument("--exclude", nargs="*", default=None, help="glob(s) to exclude")
    b.add_argument("--git-history", type=int, default=0,
                   help="add CO_CHANGE edges from the last N commits")
    b.add_argument("--max-files", type=int, default=0)
    b.add_argument("--jobs", type=int, default=0,
                   help="parser processes; 0 = one per core (capped at 8), 1 = serial")
    b.add_argument("--no-chunks", action="store_true")
    b.set_defaults(func=cmd_build)

    gh = sub.add_parser("github", aliases=["gh"],
                        help="clone a GitHub repo (owner/repo or URL) and index it")
    gh.add_argument("repo", help="owner/repo, https://github.com/owner/repo or git@... remote")
    gh.add_argument("-o", "--out", default=".r2g")
    gh.add_argument("--ref", default=None, help="branch or tag (default: default branch)")
    gh.add_argument("--depth", type=int, default=0,
                    help="shallow clone depth; 0 = full history (needed for --git-history)")
    gh.add_argument("--formats", default="jsonl,graphml,cypher,overview,html")
    gh.add_argument("--viz-nodes", type=int, default=MAX_NODES,
                    help="best-connected nodes to draw in graph.html")
    gh.add_argument("--include", nargs="*", default=None)
    gh.add_argument("--exclude", nargs="*", default=None)
    gh.add_argument("--git-history", type=int, default=0)
    gh.add_argument("--max-files", type=int, default=0)
    gh.add_argument("--jobs", type=int, default=0,
                    help="parser processes; 0 = one per core (capped at 8), 1 = serial")
    gh.add_argument("--keep-clone", default=None, help="clone here instead of a temp dir")
    gh.add_argument("--token", default=None,
                    help="GitHub token for private repos (else $GH_TOKEN/$GITHUB_TOKEN)")
    gh.set_defaults(func=cmd_github)

    q = sub.add_parser("query", help="graph-aware retrieval over a built index")
    q.add_argument("query")
    q.add_argument("-o", "--out", default=".r2g")
    q.add_argument("-k", type=int, default=8)
    q.add_argument("--hops", type=int, default=1)
    q.add_argument("--budget", type=int, default=24000)
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_query)

    m = sub.add_parser("map", help="redraw the HTML graph map from a built index")
    m.add_argument("-o", "--out", default=".r2g")
    m.add_argument("--viz-nodes", type=int, default=MAX_NODES,
                   help="how many of the best-connected nodes to draw")
    m.set_defaults(func=cmd_map)

    s = sub.add_parser("stats", help="print index stats")
    s.add_argument("-o", "--out", default=".r2g")
    s.set_defaults(func=cmd_stats)

    args = p.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
