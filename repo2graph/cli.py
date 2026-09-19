"""repo2graph CLI: build a code graph, query it, export for RAG."""

import argparse
import json
import math
import sys
from pathlib import Path
from typing import cast

from . import __version__
from .chunks import iter_chunks

# The name only: `default_embedder` is imported inside the functions that call
# it, so the heavyweight sentence-transformers import stays off every path that
# does not embed (and stays patchable through the module object).
from .embed import DEFAULT_MODEL as EMBED_DEFAULT_MODEL
from .export import (
    dump_all,
    load_parse_cache,
    make_path,
    path as artifact_path,
    register_written,
    rel as artifact_rel,
)
from .events import SAFE_ERRORS, encodable, write_safe
from .graph import build
from .viz import MAX_NODES

FORMATS = ("jsonl", "graphml", "cypher", "overview", "html")


def parse_formats(spec: str) -> set[str]:
    wanted = {f.strip() for f in spec.split(",") if f.strip()}
    unknown = sorted(wanted - set(FORMATS))
    if unknown:
        raise SystemExit(
            f"unknown format(s): {', '.join(unknown)}; choose from {', '.join(FORMATS)}"
        )
    return wanted


# Kept as a re-export: the canonical definition now lives in events, which both
# the CLI and the server-side loggers share so the rule cannot drift in two
# places. Existing importers of cli._SAFE_ERRORS keep working.
_SAFE_ERRORS = SAFE_ERRORS


def _emit(text: str) -> None:
    """The single stdout write for the whole CLI. Cannot raise on encoding.

    A redirected or piped Windows stdout is a strict cp1252 TextIOWrapper, so
    `repo2graph rag "..." > pack.md` over any repository holding a single
    non-ASCII source byte would otherwise die with 'charmap' codec errors. Git
    Bash is worse: it hands a piped stdout `errors='surrogateescape'`, which
    still raises on any character cp1252 lacks that is not a lone surrogate.

    Both cases are handled by `events.encodable`, which probes the stream's
    *actual* encoding and handler at call time -- not at import, since tests
    replace `sys.stdout` afterwards and a caller may reconfigure it mid-run.

    Args:
        text: The line to print, without a trailing newline.
    """
    stream = sys.stdout
    try:
        print(encodable(text, stream))
    except BrokenPipeError:
        # `repo2graph rag ... | head` closes the pipe early. That is the user
        # getting what they asked for, not an error: exit 0 rather than dumping
        # a traceback over the output they were reading.
        try:
            stream.close()
        except Exception:
            pass
        sys.exit(0)
    except UnicodeEncodeError:
        # encodable() should have prevented this; a stream that misreports its
        # own encoding still must not take the command down.
        write_safe(stream, text)


def cmd_build(args):
    repo_path = Path(args.repo)
    if not repo_path.is_dir():
        raise SystemExit(
            f"error: repository directory does not exist or is not a directory: {repo_path}"
        )
    formats = parse_formats(args.formats)
    outdir = Path(args.out)
    from .parse import BuildConfig

    config = BuildConfig(
        max_file_bytes=int(args.max_file_mb * 1_000_000),
        extra_exclude_dirs=args.extra_exclude_dirs or [],
        include_vendor=args.include_vendor,
        chunk_large_files=args.chunk_large_files,
    )
    cache = load_parse_cache(outdir) if getattr(args, "incremental", False) else None

    # Snapshot the previous build's nodes/edges before dump_all overwrites
    # them below -- CHANGELOG.md (written after dump_all, when "overview" is
    # requested) diffs the graph just built against this.
    from .changelog import previous_state, resolve_shas, write_changelog

    prev_state = previous_state(outdir)
    write_human_changelog = "overview" in formats
    short_sha = prev_short_sha = None
    if write_human_changelog:
        short_sha, prev_short_sha = resolve_shas(repo_path, outdir)

    g = build(
        repo_path,
        include=args.include,
        exclude=args.exclude,
        git_history=args.git_history,
        max_files=args.max_files,
        jobs=args.jobs,
        cache=cache,
        max_call_candidates=args.max_call_candidates,
        config=config,
    )
    chunks = None if args.no_chunks else iter_chunks(g)
    written, n_chunks = dump_all(g, chunks, outdir, formats, args.viz_nodes)
    if write_human_changelog:
        from datetime import date

        write_changelog(outdir, g, prev_state, short_sha, prev_short_sha, date.today().isoformat())
        # dump_all writes manifest.json last; CHANGELOG is produced after that,
        # so register it the same way cmd_embed appends vectors (ISS-144).
        cl_rel = artifact_rel("CHANGELOG.md")
        register_written(outdir, [cl_rel])
        if cl_rel not in written:
            written.append(cl_rel)
    report = {"out": str(outdir), "written": written, "stats": dict(g.stats), "chunks": n_chunks}
    if g.incremental is not None:
        report["incremental"] = g.incremental
    _emit(json.dumps(report, indent=2))


def cmd_github(args):
    from .fetch import index_github

    parse_formats(args.formats)  # fail before the clone, not after
    meta = index_github(
        args.repo,
        Path(args.out),
        ref=args.ref,
        depth=args.depth,
        git_history=args.git_history,
        formats=args.formats,
        include=args.include,
        exclude=args.exclude,
        max_files=args.max_files,
        keep_clone=args.keep_clone,
        token=args.token,
        viz_nodes=args.viz_nodes,
        jobs=args.jobs,
    )
    _emit(json.dumps(meta, indent=2))


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
            f"and the index may be incomplete; rebuild it"
        )
    return path


def _resolve_vectors(idx, args, out=None):
    """(vectors, embedder) for a query, honouring --vectors / --no-vectors.

    Dense fusion is **opt-in**. Without `--vectors` the ranking is lexical and
    no embedder is constructed at all: building one loads sentence-transformers
    and, on a cold cache, downloads ~90 MB of model weights. An unannounced
    network fetch has no business on the default `query`/`rag` path, whose only
    promised dependency is tree-sitter -- and an index is a shippable artifact,
    so "the index happens to carry vectors" is not consent to go fetch a model.

    `--vectors` is a demand: if the index has none, the `rag` extra is missing,
    or the model/width guard refuses, that is an error. Silently answering a
    different question than the one asked for is worse than failing. The
    embedding model is `--embed-model` (a separate surface from `rag --model`,
    which is the *LLM* for `--answer`); omitted, it is the built-in default.
    """
    if not getattr(args, "vectors", None):
        return None, None
    # `rag <dir> <query>` opens a different directory than -o; name the one
    # that was actually opened, not the flag's default.
    where = out if out is not None else getattr(args, "out", ".r2g")
    if idx.vectors is None:
        raise SystemExit(
            f"no vectors in the index at {where}: run `repo2graph embed -o {where}` first"
        )
    from .embed import default_embedder

    try:
        embedder = default_embedder(getattr(args, "embed_model", None))
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
    ok, reason = idx.fuse_ok(embedder)
    if not ok:
        raise SystemExit(reason)
    return idx.vectors, embedder


def cmd_embed(args):
    """Embed an index's chunks, reusing every vector whose text is unchanged."""
    if getattr(args, "verify_rag", False):
        return cmd_verify_rag(args)
    from .embed import (
        build_vectors,
        default_embedder,
        model_id_of,
        text_hash,
        write_vectors,
    )
    from .export import register_written
    from .query import read_jsonl

    out = Path(args.out)
    chunks = read_jsonl(_require_index(out, "chunks.jsonl"))
    try:
        embedder = default_embedder(args.model)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
    model_id = model_id_of(embedder)

    npy = make_path(out, "vectors.npy")
    chunk_ids = [c["id"] for c in chunks if c.get("id")]
    hashes = {c["id"]: text_hash(c) for c in chunks if c.get("id")}
    reuse = {}
    if not args.force:
        reuse = _reusable_vectors(npy, model_id, hashes)
    vectors = build_vectors(chunks, embedder, batch=args.batch, reuse=reuse)
    widths = {len(v) for v in vectors.values()}
    if len(widths) > 1:
        # Same model id, different width: the stored vectors cannot be trusted
        # alongside the new ones, so drop every one of them and re-embed.
        reuse = {}
        vectors = build_vectors(chunks, embedder, batch=args.batch)
        widths = {len(v) for v in vectors.values()}
    dim = widths.pop() if widths else 0

    n = write_vectors(npy, vectors, model_id, dim, chunk_ids, [hashes[cid] for cid in chunk_ids])
    register_written(out, [artifact_rel("vectors.npy"), artifact_rel("vectors.meta.json")])
    reused = len(set(reuse) & set(vectors))
    _emit(
        json.dumps(
            {
                "out": str(out),
                "vectors": n,
                "reused": reused,
                "embedded": n - reused,
                "model": model_id,
                "dim": dim,
            },
            indent=2,
        )
    )


def _reusable_vectors(npy: Path, model_id: str, hashes: dict) -> dict:
    """Stored vectors whose chunk id *and* chunk text are both unchanged.

    A chunk's vector depends on its own text and nothing else, so this is
    correct by construction — unlike reusing anything that depends on the
    graph, whose call confidences are global.
    """
    from .embed import load_vectors

    if not npy.exists():
        return {}
    try:
        previous, meta = load_vectors(npy)
    except Exception:
        return {}
    if not previous or meta.get("model_id") != model_id:
        return {}
    stored = dict(zip(meta.get("chunk_ids") or [], meta.get("text_hashes") or []))
    return {
        cid: vec
        for cid, vec in previous.items()
        if cid in hashes and stored.get(cid) == hashes[cid]
    }


def cmd_query(args):
    from .query import Index, format_pack

    out = Path(args.out)
    # Index reads all three, and `build --formats overview` writes chunks.jsonl
    # without the graph files -- checking only chunks turned that combination
    # into a FileNotFoundError traceback instead of this message.
    _require_index(out, "chunks.jsonl")
    _require_index(out, "nodes.jsonl")
    _require_index(out, "edges.jsonl")
    try:
        idx = Index(out)
    except ValueError as exc:
        raise SystemExit(f"error: corrupt index at {out}: {exc}") from None
    vectors, embedder = _resolve_vectors(idx, args)
    res = idx.retrieve(
        args.query,
        k=args.k,
        hops=args.hops,
        budget_chars=args.budget,
        min_confidence=getattr(args, "min_conf", None),
        vectors=vectors,
        embedder=embedder,
    )
    if getattr(args, "format", "text") == "json" or args.json:
        _emit(json.dumps(res, indent=2))
    else:
        _emit(format_pack(res))


RAG_TARGET_HELP = (
    "expected one of: a repo2graph index directory (one holding "
    "agent/manifest.json), a source repository directory to index first, "
    "or a GitHub spec such as owner/repo or https://github.com/owner/repo"
)


def _rag_index_dir(args) -> Path:
    """Resolve the `rag` target to an index directory, building it if needed."""
    out = Path(args.out)
    target = args.target
    if not target:
        return out
    tpath = Path(target)
    if artifact_path(tpath, "manifest.json").exists():
        return tpath  # already an index: use it as it is, do not rebuild
    if tpath.is_dir():
        g = build(tpath)
        dump_all(g, iter_chunks(g), out, {"jsonl", "overview"})
        return out
    from .fetch import index_github, parse_spec

    try:
        parse_spec(target)
    except ValueError:
        raise SystemExit(f"cannot resolve target {target!r}: {RAG_TARGET_HELP}") from None
    index_github(target, out, formats="jsonl,overview")
    return out


def verify_rag(idx, out, embed_model=None) -> tuple[dict, str | None]:
    """Self-test the dense-retrieval path against one index.

    Answers the four questions that distinguish "dense retrieval is working"
    from "dense retrieval silently is not": are there vectors at all, which
    model and width were they built with, does the active embedder agree, and
    does every chunk actually have one.

    Args:
        idx: An open `Index`.
        out: The index directory, for error messages.
        embed_model: Model id to check against, or None for the built-in
            default. Never defaulted to the index's own `model_id` -- comparing
            a value with itself is what makes a mismatch guard unfalsifiable.

    Returns:
        `(report, error)`. `error` is None when the path is sound, otherwise a
        sentence naming what is broken and how to fix it.
    """
    report = {
        "index": str(out),
        "vectors_present": bool(idx.vectors),
        "chunks": len(idx.chunks),
        "model_id": None,
        "dim": None,
        "vectorised_chunks": len(idx.vectors or {}),
        "unvectorised_chunks": len(idx.chunks) - len(idx.vectors or {}),
        "embedder_model_id": None,
        "embedder_dim": None,
        "rag_extra_installed": None,
    }
    if not idx.vectors:
        return report, (
            f"no vectors in the index at {out}: dense retrieval is not "
            f"available. Run `repo2graph embed -o {out}` to build them."
        )
    meta = idx.vector_meta or {}
    report["model_id"] = meta.get("model_id")
    report["dim"] = meta.get("dim")

    # Chunk coverage: fuse_ok cannot see this, and it is the failure that makes
    # fusion abandon itself at query time with everything else looking healthy.
    missing = cast(int, report["unvectorised_chunks"])

    from .embed import default_embedder

    try:
        embedder = default_embedder(embed_model)
        report["rag_extra_installed"] = True
    except RuntimeError as exc:
        report["rag_extra_installed"] = False
        return report, str(exc)
    from .embed import dim_of, model_id_of

    report["embedder_model_id"] = model_id_of(embedder)
    try:
        report["embedder_dim"] = dim_of(embedder)
    except Exception:
        report["embedder_dim"] = None
    ok, reason = idx.fuse_ok(embedder)
    if not ok:
        return report, reason
    if missing > 0:
        return report, (
            f"{missing} of {report['chunks']} chunks have no vector: a query "
            f"whose BM25 shortlist touches one of them falls back to lexical "
            f"ranking. Re-run `repo2graph embed -o {out}`."
        )
    return report, None


def cmd_verify_rag(args):
    """`--verify-rag`: report on the index's dense path, non-zero if broken."""
    from .query import Index

    out = Path(args.out)
    _require_index(out, "chunks.jsonl")
    try:
        idx = Index(out)
    except ValueError as exc:
        raise SystemExit(f"error: corrupt index at {out}: {exc}") from None
    # `embed` spells it --model/--embed-model; the rag surface spells it
    # --embed-model. Either way it is the *embedding* model, never the LLM.
    model = getattr(args, "embed_model", None) or getattr(args, "model", None)
    report, error = verify_rag(idx, out, model)
    report["ok"] = error is None
    report["error"] = error
    _emit(json.dumps(report, indent=2))
    if error:
        raise SystemExit(1)
    return 0


def cmd_rag(args):
    """Pack an agent-ready, citation-carrying context for one question."""
    from .query import Index

    out = _rag_index_dir(args)
    _require_index(out, "chunks.jsonl")
    _require_index(out, "nodes.jsonl")
    _require_index(out, "edges.jsonl")
    try:
        idx = Index(out)
    except ValueError as exc:
        raise SystemExit(f"error: corrupt index at {out}: {exc}") from None
    vectors, embedder = _resolve_vectors(idx, args, out)
    pack = idx.pack_context(
        args.query,
        k=args.k,
        hops=args.hops,
        budget_chars=args.budget,
        min_confidence=args.min_conf,
        expand_graph=not args.no_expand,
        exclude_secrets=args.answer,
        vectors=vectors,
        embedder=embedder,
        budget_tokens=getattr(args, "budget_tokens", None),
    )
    if args.answer:
        from .answer import stream_answer

        stream_answer(pack, model=args.model, provider=args.provider)
        return 0
    if args.format == "json":
        _emit(json.dumps(pack, indent=2))
    else:
        _emit(pack["markdown"])


def cmd_map(args):
    """Redraw graph.html from an index that is already on disk."""
    from .viz import LoadedGraph, write_html

    out = Path(args.out)
    _require_index(out, "nodes.jsonl")
    _require_index(out, "edges.jsonl")
    html = make_path(out, "graph.html")
    data = write_html(LoadedGraph(out), html, args.viz_nodes)
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


def cmd_stats(args):
    _emit(_require_index(Path(args.out), "stats.json").read_text(encoding="utf8"))


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


def _posint(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an integer, got {value!r}") from None
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {n}")
    return n


def _viz_nodes(value: str):
    """argparse type for --viz-nodes: a non-negative int, or "all" for no cap.

    Args:
        value: The raw command-line string.

    Returns:
        None for "all" (no cap), otherwise the integer, 0 included.

    Raises:
        argparse.ArgumentTypeError: On a negative or non-integer value.
    """
    if str(value).strip().lower() == "all":
        return None
    return _nonneg(value)


def _unit_float(value: str) -> float:
    """argparse type: a finite float in [0.0, 1.0].

    A bare float() would accept nan and inf, and `confidence < nan` is False for
    every edge — the filter would silently stop filtering.
    """
    try:
        f = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number, got {value!r}") from None
    if not math.isfinite(f):
        raise argparse.ArgumentTypeError(f"expected a finite number in [0.0, 1.0], got {value!r}")
    if not 0.0 <= f <= 1.0:
        raise argparse.ArgumentTypeError(f"must be between 0.0 and 1.0, got {f}")
    return f


def _add_vector_flags(parser) -> None:
    """--vectors / --no-vectors / --embed-model, for `query` and `rag`.

    `dest="embed_model"`, deliberately not `dest="model"`: on `rag`, `--model`
    already means the LLM for `--answer`. The two must never share a dest, or
    `rag --answer --model gpt-4o` would try to load an LLM name as a
    sentence-transformers checkpoint (and vice versa).
    """
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--vectors",
        dest="vectors",
        action="store_true",
        default=None,
        help="fuse the index's dense vectors into the ranking "
        "(error if they are missing or do not match)",
    )
    group.add_argument(
        "--no-vectors",
        dest="vectors",
        action="store_false",
        default=None,
        help="lexical ranking only, even when the index has vectors",
    )
    parser.add_argument(
        "--embed-model",
        dest="embed_model",
        default=None,
        help="sentence-transformers model used to embed the query for "
        f"--vectors; must match the index (default: {EMBED_DEFAULT_MODEL})",
    )


def _max_file_mb(value: str) -> float:
    try:
        f = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number, got {value!r}") from None
    if f < 0.1:
        raise argparse.ArgumentTypeError("--max-file-mb must be at least 0.1")
    return f


def main(argv=None):
    p = argparse.ArgumentParser(prog="repo2graph", description=__doc__)
    p.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd")

    effective_argv = sys.argv[1:] if argv is None else argv
    if not effective_argv:
        p.print_help()
        return 0

    v = sub.add_parser("version", help="show repo2graph version")
    v.set_defaults(func=lambda _args: _emit(f"repo2graph {__version__}"))

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-o", "--out", default=".r2g")
    common.add_argument(
        "--formats",
        default="jsonl,graphml,cypher,overview,html",
        help="comma list: jsonl,graphml,cypher,overview,html",
    )
    common.add_argument(
        "--viz-nodes",
        type=_viz_nodes,
        default=MAX_NODES,
        metavar="N|all",
        help=f"best-connected nodes to draw in graph.html "
        f"(default: {MAX_NODES}; 0 draws an empty graph; "
        f"'all' draws every node)",
    )
    common.add_argument("--include", nargs="*", default=None, help="glob(s) to include")
    common.add_argument("--exclude", nargs="*", default=None, help="glob(s) to exclude")
    common.add_argument(
        "--git-history", type=_nonneg, default=0, help="add CO_CHANGE edges from the last N commits"
    )
    common.add_argument("--max-files", type=_nonneg, default=0)
    common.add_argument(
        "--jobs",
        type=_nonneg,
        default=0,
        help="parser processes; 0 = one per core (capped at 8), 1 = serial",
    )

    b = sub.add_parser("build", parents=[common], help="parse a repo into a graph + RAG chunks")
    b.add_argument("repo")
    b.add_argument("--no-chunks", action="store_true")
    b.add_argument(
        "--max-call-candidates",
        type=_posint,
        default=5,
        help="maximum number of candidates to keep for ambiguous calls",
    )
    b.add_argument(
        "--max-file-mb",
        type=_max_file_mb,
        default=1.5,
        help="max file size in MB before skipping or chunking (default: 1.5, min: 0.1)",
    )
    b.add_argument(
        "--include-vendor",
        action="store_true",
        default=False,
        help="index files in vendor directories (default: off)",
    )
    b.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        dest="extra_exclude_dirs",
        metavar="NAME",
        help="additional directory name to exclude (repeatable)",
    )
    b.add_argument(
        "--chunk-large-files",
        action="store_true",
        default=False,
        help="chunk and parse files exceeding max-file-mb instead of skipping them (default: off)",
    )
    b.add_argument(
        "--incremental",
        action="store_true",
        help="reuse parse results for files whose content hash is "
        "unchanged since the last build in --out (default: off, "
        "full rebuild). Safe for edits, adds, deletes and "
        "renames; rerun without it after upgrading repo2graph "
        "or changing a language grammar",
    )
    b.set_defaults(func=cmd_build)

    gh = sub.add_parser(
        "github",
        aliases=["gh"],
        parents=[common],
        help="clone a GitHub repo (owner/repo or URL) and index it",
    )
    gh.add_argument("repo", help="owner/repo, https://github.com/owner/repo or git@... remote")
    gh.add_argument("--ref", default=None, help="branch or tag (default: default branch)")
    gh.add_argument(
        "--depth",
        type=_nonneg,
        default=0,
        help="shallow clone depth; 0 = full history (needed for --git-history)",
    )
    gh.add_argument("--keep-clone", default=None, help="clone here instead of a temp dir")
    gh.add_argument(
        "--token",
        default=None,
        help="GitHub token for private repos (else $GH_TOKEN/$GITHUB_TOKEN)",
    )
    gh.set_defaults(func=cmd_github)

    q = sub.add_parser("query", help="graph-aware retrieval over a built index")
    q.add_argument("query")
    q.add_argument("-o", "--out", default=".r2g")
    q.add_argument("-k", type=_nonneg, default=8)
    q.add_argument("--hops", type=_nonneg, default=1)
    q.add_argument("--budget", type=_nonneg, default=24000)
    q.add_argument(
        "--min-conf",
        type=_unit_float,
        default=None,
        help="drop CALLS edges below this confidence (0.0-1.0)",
    )
    q.add_argument("--format", choices=("text", "json"), default="text")
    q.add_argument("--json", action="store_true")
    _add_vector_flags(q)
    q.set_defaults(func=cmd_query)

    r = sub.add_parser("rag", help="pack a cited, graph-expanded context for a question")
    r.add_argument(
        "target",
        nargs="?",
        default=None,
        help="index dir, source repo dir or GitHub spec; omit to use -o",
    )
    r.add_argument("query")
    r.add_argument("-o", "--out", default=".r2g")
    r.add_argument("-k", type=_nonneg, default=8, help="lexical seed chunks")
    r.add_argument("--hops", type=_nonneg, default=1, help="graph expansion hops")
    r.add_argument(
        "--budget",
        type=_nonneg,
        default=24000,
        help="character budget for the whole pack, map and headers included",
    )
    r.add_argument(
        "--budget-tokens",
        type=_nonneg,
        default=None,
        help="token budget for the whole pack; replaces --budget when given",
    )
    r.add_argument(
        "--min-conf",
        type=_unit_float,
        default=1.0,
        help="drop CALLS edges below this confidence (0.0-1.0)",
    )
    r.add_argument("--no-expand", action="store_true", help="lexical seeds only")
    r.add_argument("--format", choices=("markdown", "json"), default="markdown")
    r.add_argument(
        "--answer",
        action="store_true",
        help="stream a grounded answer from an LLM (needs a provider env var)",
    )
    r.add_argument("--model", default=None, help="model name for --answer")
    r.add_argument(
        "--provider",
        choices=("gemini", "openai", "anthropic", "ollama"),
        default=None,
        help="force a specific LLM provider for --answer",
    )
    _add_vector_flags(r)
    r.set_defaults(func=cmd_rag)

    e = sub.add_parser("embed", help="embed an index's chunks for dense retrieval")
    e.add_argument("-o", "--out", default=".r2g")
    # --embed-model is the spelling action.yml uses: `--model` must not appear
    # in that file, because there it would mean `rag --answer`'s LLM model, the
    # one surface the Action deliberately does not expose.
    e.add_argument(
        "--model",
        "--embed-model",
        dest="model",
        default=None,
        help=f"sentence-transformers model (default: {EMBED_DEFAULT_MODEL})",
    )
    e.add_argument("--batch", type=_nonneg, default=64, help="texts per encode() call")
    e.add_argument(
        "--force",
        action="store_true",
        help="re-embed every chunk instead of reusing unchanged vectors",
    )
    e.add_argument(
        "--verify-rag",
        action="store_true",
        help="self-test this index's dense-retrieval path instead of "
        "embedding: reports whether vectors are present, the "
        "model and dimension they were built with, and whether "
        "the active embedder matches. Exits 1 if the rag path "
        "is broken or misconfigured (default: off)",
    )
    e.set_defaults(func=cmd_embed)

    m = sub.add_parser("map", help="redraw the HTML graph map from a built index")
    m.add_argument("-o", "--out", default=".r2g")
    m.add_argument(
        "--viz-nodes",
        type=_viz_nodes,
        default=MAX_NODES,
        metavar="N|all",
        help=f"how many of the best-connected nodes to draw "
        f"(default: {MAX_NODES}; 0 draws an empty graph; "
        f"'all' draws every node)",
    )
    m.set_defaults(func=cmd_map)

    s = sub.add_parser("stats", help="print index stats")
    s.add_argument("-o", "--out", default=".r2g")
    s.set_defaults(func=cmd_stats)

    try:
        args = p.parse_args(argv)
        if not hasattr(args, "func"):
            p.print_help()
            return 0
        return args.func(args) or 0
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
