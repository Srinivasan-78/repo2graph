# PYTHON_ARGCOMPLETE_OK
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
from .exclusions import GROUP_NAMES as EXCLUSION_GROUP_NAMES
from .graph import GraphLimitExceeded as _GraphLimitExceeded, build
from .parse import ParseError
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


def _effective_exclude(args) -> list[str] | None:
    """`--exclude` globs plus whatever `--exclude-group` expands to.

    Every caller that decides whether a path is indexed goes through this --
    `build`, `github` and `explain-path` alike. `explain-path` sharing it is
    the point: an "explain" that answers from a different rule set than the
    build is worse than no explain at all.

    `--exclude-group help` prints the table and exits 0 without building,
    which is what a user asking what the groups cover wants.
    """
    from .exclusions import describe, globs_for

    groups = list(getattr(args, "exclude_group", None) or [])
    if "help" in groups:
        _emit(describe())
        raise SystemExit(0)

    explicit = list(getattr(args, "exclude", None) or [])
    if not groups:
        # None, not [], so discovery keeps its "no exclude globs at all" path
        # rather than running every candidate through an empty matcher.
        return explicit or None
    return explicit + globs_for(groups)


def cmd_build(args):
    repo_path = Path(args.repo)
    if not repo_path.is_dir():
        raise SystemExit(
            f"error: repository directory does not exist or is not a directory: {repo_path}"
        )
    formats = parse_formats(args.formats)
    outdir = Path(args.out)
    # Resolved before validate_outdir and the build lock: `--exclude-group
    # help` prints a table and exits, and it should not have taken a lock on
    # somebody else's output directory to do it.
    exclude_globs = _effective_exclude(args)

    # --- Harden the output path before any work begins ---
    from .integrity import validate_outdir
    from .lock import BuildLock, LockTimeoutError

    try:
        outdir = validate_outdir(
            outdir,
            repo_root=repo_path,
            allow_symlink=getattr(args, "allow_symlink_out", False),
            force=getattr(args, "force", False),
        )
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from None

    from .parse import BuildConfig, ParseError

    include_secrets = getattr(args, "include_secrets", False)
    if include_secrets:
        from .events import emit

        emit("secrets_inclusion_enabled", level="warning")
        if sys.stderr.isatty():
            sys.stderr.write(
                "warning: --include-secrets is enabled; sensitive credentials may be indexed into artifacts\n"
            )

    config = BuildConfig(
        max_file_bytes=int(args.max_file_mb * 1_000_000),
        extra_exclude_dirs=args.extra_exclude_dirs or [],
        include_vendor=args.include_vendor,
        chunk_large_files=args.chunk_large_files,
        max_nodes=getattr(args, "max_nodes", 0),
        include_secrets=include_secrets,
        secret_policy=getattr(args, "secret_policy", "redact-match"),
        extra_secret_keywords=getattr(args, "extra_secret_keywords", None) or [],
        extra_secret_dirs=getattr(args, "extra_secret_dirs", None) or [],
        parse_policy=getattr(args, "parse_policy", "best-effort"),
    )
    cache = load_parse_cache(outdir) if getattr(args, "incremental", False) else None

    # Snapshot the previous build's nodes/edges before dump_all overwrites
    # them below -- CHANGELOG.md (written after dump_all, when "overview" is
    # requested) diffs the graph just built against this. previous_state
    # materialises both jsonl files; skip it when no changelog will be written
    # so a `--formats jsonl` rebuild does not hold a second copy of the graph
    # for the duration of build() + dump_all() (#205).
    from .changelog import previous_state, resolve_shas, write_changelog

    write_human_changelog = "overview" in formats
    prev_state = None
    short_sha = prev_short_sha = None
    if write_human_changelog:
        prev_state = previous_state(outdir)
        short_sha, prev_short_sha = resolve_shas(repo_path, outdir)

    lock_timeout = getattr(args, "lock_timeout", 60.0)
    try:
        with BuildLock(outdir, timeout=lock_timeout):
            g = build(
                repo_path,
                include=args.include,
                exclude=exclude_globs,
                git_history=args.git_history,
                max_files=args.max_files,
                jobs=args.jobs,
                cache=cache,
                max_call_candidates=args.max_call_candidates,
                config=config,
                cochange_min=getattr(args, "cochange_min", 3),
            )
            chunks = None if args.no_chunks else iter_chunks(g)
            written, n_chunks = dump_all(g, chunks, outdir, formats, args.viz_nodes)
    except LockTimeoutError as exc:
        raise SystemExit(f"error: {exc}") from None
    except ParseError as exc:
        raise SystemExit(f"error: {exc}") from None

    if write_human_changelog:
        from datetime import date

        write_changelog(outdir, g, prev_state, short_sha, prev_short_sha, date.today().isoformat())
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
    from .parse import BuildConfig

    parse_formats(args.formats)  # fail before the clone, not after
    exclude_globs = _effective_exclude(args)  # and `--exclude-group help` before it too

    outdir = Path(args.out)

    # --- Harden the output path before any work begins ---
    from .integrity import validate_outdir
    from .lock import BuildLock, LockTimeoutError

    try:
        outdir = validate_outdir(
            outdir,
            allow_symlink=getattr(args, "allow_symlink_out", False),
            force=getattr(args, "force", False),
        )
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from None

    include_secrets = getattr(args, "include_secrets", False)
    if include_secrets:
        from .events import emit

        emit("secrets_inclusion_enabled", level="warning")
        if sys.stderr.isatty():
            sys.stderr.write(
                "warning: --include-secrets is enabled; sensitive credentials may be indexed into artifacts\n"
            )

    config = BuildConfig(
        max_file_bytes=int(args.max_file_mb * 1_000_000),
        extra_exclude_dirs=args.extra_exclude_dirs or [],
        include_vendor=args.include_vendor,
        chunk_large_files=args.chunk_large_files,
        max_nodes=getattr(args, "max_nodes", 0),
        include_secrets=include_secrets,
        secret_policy=getattr(args, "secret_policy", "redact-match"),
        extra_secret_keywords=getattr(args, "extra_secret_keywords", None) or [],
        extra_secret_dirs=getattr(args, "extra_secret_dirs", None) or [],
        parse_policy=getattr(args, "parse_policy", "best-effort"),
    )
    lock_timeout = getattr(args, "lock_timeout", 60.0)
    try:
        with BuildLock(outdir, timeout=lock_timeout):
            meta = index_github(
                args.repo,
                outdir,
                ref=args.ref,
                depth=args.depth,
                git_history=args.git_history,
                formats=args.formats,
                include=args.include,
                exclude=exclude_globs,
                max_files=args.max_files,
                keep_clone=args.keep_clone,
                token=args.token,
                viz_nodes=args.viz_nodes,
                jobs=args.jobs,
                config=config,
                max_call_candidates=args.max_call_candidates,
                no_chunks=args.no_chunks,
                cochange_min=getattr(args, "cochange_min", 3),
            )
    except LockTimeoutError as exc:
        raise SystemExit(f"error: {exc}") from None
    except ParseError as exc:
        raise SystemExit(f"error: {exc}") from None
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

    # Read build_id from manifest so vectors.meta.json records which build they
    # correspond to; verify_artifacts and doctor use this to detect staleness.
    build_id: str | None = None
    try:
        _mpath = artifact_path(out, "manifest.json")
        if _mpath.exists():
            _mdata = json.loads(_mpath.read_text(encoding="utf8", errors="replace"))
            if isinstance(_mdata, dict):
                build_id = _mdata.get("build_id") or None
    except Exception:
        pass

    n = write_vectors(
        npy,
        vectors,
        model_id,
        dim,
        chunk_ids,
        [hashes[cid] for cid in chunk_ids],
        build_id=build_id,
    )
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
        from .parse import BuildConfig

        cfg = BuildConfig(
            include_secrets=getattr(args, "include_secrets", False),
            secret_policy=getattr(args, "secret_policy", "redact-match"),
            extra_secret_keywords=getattr(args, "extra_secret_keywords", None) or [],
            extra_secret_dirs=getattr(args, "extra_secret_dirs", None) or [],
        )
        g = build(tpath, config=cfg)
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
    if getattr(args, "exclude_secrets", False):
        if sys.stderr.isatty():
            sys.stderr.write(
                "warning: --exclude-secrets is deprecated; secret exclusion is now enabled by default\n"
            )

    include_secrets = getattr(args, "include_secrets", False)
    exclude_secrets = (
        args.answer
        or getattr(args, "exclude_secrets", False)
        or bool(
            getattr(args, "extra_secret_keywords", None) or getattr(args, "extra_secret_dirs", None)
        )
    ) and not include_secrets

    pack = idx.pack_context(
        args.query,
        k=args.k,
        hops=args.hops,
        budget_chars=args.budget,
        min_confidence=args.min_conf,
        expand_graph=not args.no_expand,
        exclude_secrets=exclude_secrets,
        vectors=vectors,
        embedder=embedder,
        budget_tokens=getattr(args, "budget_tokens", None),
        extra_secret_keywords=getattr(args, "extra_secret_keywords", None) or None,
        extra_secret_dirs=getattr(args, "extra_secret_dirs", None) or None,
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


def format_stats_summary(data: dict) -> str:
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


def cmd_stats(args):
    raw = _require_index(Path(args.out), "stats.json").read_text(encoding="utf8")
    if getattr(args, "json", False) or getattr(args, "format", "json") == "json":
        _emit(raw)
    else:
        try:
            data = json.loads(raw)
            _emit(format_stats_summary(data))
        except Exception:
            _emit(raw)


def cmd_explain_path(args):
    from .parse import BuildConfig, explain_path

    repo_path = Path(args.repo)
    if not repo_path.is_dir():
        raise SystemExit(f"error: repository directory does not exist: {repo_path}")

    config = BuildConfig(
        include_vendor=getattr(args, "include_vendor", False),
        include_secrets=getattr(args, "include_secrets", False),
        chunk_large_files=getattr(args, "chunk_large_files", False),
        extra_exclude_dirs=getattr(args, "extra_exclude_dirs", None) or [],
        extra_secret_keywords=getattr(args, "extra_secret_keywords", None) or [],
        extra_secret_dirs=getattr(args, "extra_secret_dirs", None) or [],
    )
    res = explain_path(
        repo_path,
        args.path,
        config=config,
        include_globs=args.include or None,
        exclude_globs=_effective_exclude(args),
    )
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


def cmd_bug_report(args) -> int:
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


def cmd_index_status(args) -> int:
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


def cmd_demo(args) -> int:
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


def cmd_doctor(args):
    from .doctor import run_doctor

    report = run_doctor(args.path)
    if getattr(args, "json", False):
        _emit(json.dumps(report.to_dict(), indent=2))
    else:
        _emit(report.format_text())
    return 0 if report.ok else 1


def cmd_completion(args) -> int:
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


def cmd_explain(args) -> int:
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
    elif subcmd == "node":
        res = explain_node(outdir, args.node_id)
        _emit(json.dumps(res, indent=2) if is_json else format_explain_node(res))
        return 0 if res.get("found") else 1
    elif subcmd == "retrieval":
        k = getattr(args, "k", 5)
        hops = getattr(args, "hops", 1)
        conf = getattr(args, "min_confidence", None)
        res = explain_retrieval(outdir, args.query, k=k, hops=hops, min_confidence=conf)
        _emit(json.dumps(res, indent=2) if is_json else format_explain_retrieval(res))
        return 0
    else:
        print(f"repo2graph: error: unknown explain command '{subcmd}'", file=sys.stderr)
        return 1


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
    p = argparse.ArgumentParser(
        prog="repo2graph",
        description=__doc__,
        epilog="first time here? run `repo2graph demo` -- it indexes a bundled example "
        "repo and answers five questions about it. Then `repo2graph build . -o .r2g`. "
        "If something looks wrong, `repo2graph doctor .` says what and how to fix it.",
    )
    p.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd")

    # No early `if not argv: print_help()` here. Bare `repo2graph` used to
    # return before a single `sub.add_parser(...)` ran, so the help page a
    # first-run user sees listed no commands at all -- "positional arguments:
    # {}" -- while `repo2graph --help` listed all fourteen. The fall-through
    # at the bottom of this function already prints help for an argv that
    # names no subcommand, and by then the subparsers are registered.

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
        "--exclude-group",
        action="append",
        default=[],
        choices=[*EXCLUSION_GROUP_NAMES, "all", "help"],
        metavar="NAME",
        help=(
            "exclude a named group of paths (repeatable, composable with --exclude): "
            + ", ".join(EXCLUSION_GROUP_NAMES)
            + ", all. Use `--exclude-group help` to print what each one covers."
        ),
    )
    common.add_argument(
        "--git-history", type=_nonneg, default=0, help="add CO_CHANGE edges from the last N commits"
    )
    common.add_argument(
        "--cochange-min",
        type=_nonneg,
        default=3,
        help="minimum co-edits across git history required to emit a CO_CHANGE edge (default: 3)",
    )
    common.add_argument("--max-files", type=_nonneg, default=0)
    common.add_argument(
        "--jobs",
        type=_nonneg,
        default=0,
        help="parser processes; 0 = one per core (capped at 8), 1 = serial",
    )
    common.add_argument(
        "--include-secrets",
        action="store_true",
        default=False,
        help="index sensitive secret/credential files (default: off)",
    )
    common.add_argument(
        "--secret-policy",
        choices=("redact-match", "exclude-file", "warn-only", "off"),
        default="redact-match",
        help="content-aware secret scanning policy for chunks (default: redact-match)",
    )
    common.add_argument(
        "--secret-keyword",
        action="append",
        default=[],
        dest="extra_secret_keywords",
        metavar="KEYWORD",
        help="additional keyword to exclude as secret file/path (repeatable)",
    )
    common.add_argument(
        "--secret-dir",
        action="append",
        default=[],
        dest="extra_secret_dirs",
        metavar="DIR",
        help="additional directory name to exclude as secret path (repeatable)",
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
    b.add_argument(
        "--max-nodes",
        type=_nonneg,
        default=0,
        help="maximum graph node count before raising GraphLimitExceeded (default: 0, unbounded)",
    )
    b.add_argument(
        "--allow-symlink-out",
        action="store_true",
        default=False,
        dest="allow_symlink_out",
        help="allow -o to point through a symlink (default: off)",
    )
    b.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="allow overwriting a non-repo2graph output directory (default: off)",
    )
    b.add_argument(
        "--lock-timeout",
        type=float,
        default=60.0,
        dest="lock_timeout",
        metavar="SECONDS",
        help="seconds to wait for the build lock before failing (default: 60)",
    )
    b.add_argument(
        "--parse-policy",
        choices=("best-effort", "warn", "strict"),
        default="best-effort",
        help="how to handle tree-sitter parser failures: best-effort (default), warn, strict",
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
    gh.add_argument("--no-chunks", action="store_true")
    gh.add_argument(
        "--max-call-candidates",
        type=_posint,
        default=5,
        help="maximum number of candidates to keep for ambiguous calls",
    )
    gh.add_argument(
        "--max-file-mb",
        type=_max_file_mb,
        default=1.5,
        help="max file size in MB before skipping or chunking (default: 1.5, min: 0.1)",
    )
    gh.add_argument(
        "--include-vendor",
        action="store_true",
        default=False,
        help="index files in vendor directories (default: off)",
    )
    gh.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        dest="extra_exclude_dirs",
        metavar="NAME",
        help="additional directory name to exclude (repeatable)",
    )
    gh.add_argument(
        "--chunk-large-files",
        action="store_true",
        default=False,
        help="chunk and parse files exceeding max-file-mb instead of skipping them (default: off)",
    )
    gh.add_argument(
        "--max-nodes",
        type=_nonneg,
        default=0,
        help="maximum graph node count before raising GraphLimitExceeded (default: 0, unbounded)",
    )
    gh.add_argument(
        "--allow-symlink-out",
        action="store_true",
        default=False,
        dest="allow_symlink_out",
        help="allow -o to point through a symlink (default: off)",
    )
    gh.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="allow overwriting a non-repo2graph output directory (default: off)",
    )
    gh.add_argument(
        "--lock-timeout",
        type=float,
        default=60.0,
        dest="lock_timeout",
        metavar="SECONDS",
        help="seconds to wait for the build lock before failing (default: 60)",
    )
    gh.add_argument(
        "--parse-policy",
        choices=("best-effort", "warn", "strict"),
        default="best-effort",
        help="how to handle tree-sitter parser failures: best-effort (default), warn, strict",
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
    q.add_argument(
        "--include-secrets",
        action="store_true",
        default=False,
        help="include secret files in query results",
    )
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
    r.add_argument(
        "--include-secrets",
        action="store_true",
        default=False,
        help="include sensitive secret/credential files (default: off)",
    )
    r.add_argument(
        "--secret-policy",
        choices=("redact-match", "exclude-file", "warn-only", "off"),
        default="redact-match",
        help="content-aware secret scanning policy for chunks (default: redact-match)",
    )
    r.add_argument(
        "--exclude-secrets",
        action="store_true",
        default=False,
        help="exclude secret files from pack even when --answer is not set",
    )
    r.add_argument(
        "--secret-keyword",
        action="append",
        default=[],
        dest="extra_secret_keywords",
        metavar="KEYWORD",
        help="additional keyword to exclude as secret file/path (repeatable)",
    )
    r.add_argument(
        "--secret-dir",
        action="append",
        default=[],
        dest="extra_secret_dirs",
        metavar="DIR",
        help="additional directory name to exclude as secret path (repeatable)",
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

    s = sub.add_parser("stats", help="print index stats and quality summary")
    s.add_argument("-o", "--out", default=".r2g")
    s.add_argument("--json", action="store_true", help="output stats as JSON")
    s.add_argument(
        "--format",
        choices=("text", "json"),
        default="json",
        help="output format: json (default) or text (human-readable quality summary)",
    )
    s.set_defaults(func=cmd_stats)

    ep = sub.add_parser("explain-path", help="explain why a file is included or excluded")
    ep.add_argument("path", help="file path to evaluate")
    ep.add_argument(
        "-r", "--repo", default=".", help="repository root (default: current directory)"
    )
    ep.add_argument("--include", action="append", default=[], help="glob to include")
    ep.add_argument("--exclude", action="append", default=[], help="glob to exclude")
    ep.add_argument(
        "--exclude-group",
        action="append",
        default=[],
        choices=[*EXCLUSION_GROUP_NAMES, "all", "help"],
        metavar="NAME",
        help="exclude a named group, exactly as `build` would (repeatable)",
    )
    ep.add_argument("--include-vendor", action="store_true", default=False)
    ep.add_argument("--include-secrets", action="store_true", default=False)
    ep.add_argument("--json", action="store_true", help="output explanation as JSON")
    ep.set_defaults(func=cmd_explain_path)

    from .bugreport import CATEGORIES as _BUG_CATEGORIES

    br = sub.add_parser(
        "bug-report",
        help="assemble a privacy-preserving diagnostic bundle to attach to an issue",
    )
    br.add_argument("-o", "--out", default=".r2g", help="index directory (default: .r2g)")
    br.add_argument("-r", "--repo", default=None, help="source tree the index describes")
    br.add_argument(
        "--category",
        choices=sorted(_BUG_CATEGORIES),
        default=None,
        help="what went wrong; adds the extra evidence that category needs",
    )
    br.add_argument(
        "--include-paths",
        action="store_true",
        help="include repo-relative file paths (off by default; a path can leak a roadmap)",
    )
    br.add_argument("--json", action="store_true", help="emit the raw bundle as JSON")
    br.add_argument("--write", metavar="FILE", default=None, help="write to FILE instead of stdout")
    br.set_defaults(func=cmd_bug_report)

    ist = sub.add_parser(
        "index-status",
        help="report index provenance, contents, exclusions and freshness",
    )
    ist.add_argument("-o", "--out", default=".r2g", help="index directory (default: .r2g)")
    ist.add_argument(
        "-r",
        "--repo",
        default=None,
        help="source tree the index describes (default: the index directory's parent)",
    )
    ist.add_argument("--json", action="store_true", help="output the report as JSON")
    ist.add_argument(
        "--check",
        action="store_true",
        help="exit 1 when the index is not current, for use as a CI gate",
    )
    ist.set_defaults(func=cmd_index_status)

    dm = sub.add_parser(
        "demo",
        help="index a bundled example repo and answer the five starter questions",
    )
    dm.add_argument(
        "-o",
        "--out",
        default=None,
        metavar="DIR",
        help="write the demo repo here and keep it (default: a temp dir, removed on exit)",
    )
    dm.add_argument(
        "--keep",
        action="store_true",
        help="keep the temp demo repo and its index instead of deleting them",
    )
    dm.add_argument(
        "--full",
        action="store_true",
        help="print each answer in full instead of the first few lines",
    )
    dm.set_defaults(func=cmd_demo)

    d = sub.add_parser(
        "doctor",
        help="diagnose environment, dependencies, permissions, and index integrity",
    )
    d.add_argument(
        "path",
        nargs="?",
        default=".",
        help="repository or index path to diagnose (default: current directory)",
    )
    d.add_argument("--json", action="store_true", help="output report as JSON")
    d.set_defaults(func=cmd_doctor)

    comp = sub.add_parser(
        "completion",
        help="print shell completion setup script (bash, zsh, fish)",
    )
    comp.add_argument(
        "shell",
        nargs="?",
        default="bash",
        choices=["bash", "zsh", "fish"],
        help="target shell (default: bash)",
    )
    comp.set_defaults(func=cmd_completion)

    exp = sub.add_parser(
        "explain",
        help="explain graph edges, nodes, and retrieval results",
    )
    explain_sp = exp.add_subparsers(dest="explain_cmd", required=True)

    exp_edge = explain_sp.add_parser("edge", help="explain connection between two nodes")
    exp_edge.add_argument("src", help="source node ID")
    exp_edge.add_argument("dst", help="destination node ID")
    exp_edge.add_argument(
        "-o", "--out", default=".r2g", help="path to index directory (default: .r2g)"
    )
    exp_edge.add_argument("--json", action="store_true", help="output explanation as JSON")
    exp_edge.set_defaults(func=cmd_explain)

    exp_node = explain_sp.add_parser("node", help="explain a node and its connections")
    exp_node.add_argument("node_id", help="node ID to inspect")
    exp_node.add_argument(
        "-o", "--out", default=".r2g", help="path to index directory (default: .r2g)"
    )
    exp_node.add_argument("--json", action="store_true", help="output explanation as JSON")
    exp_node.set_defaults(func=cmd_explain)

    exp_ret = explain_sp.add_parser(
        "retrieval", help="explain retrieval ranking and graph expansion"
    )
    exp_ret.add_argument("query", help="query to trace")
    exp_ret.add_argument(
        "-o", "--out", default=".r2g", help="path to index directory (default: .r2g)"
    )
    exp_ret.add_argument("-k", type=_nonneg, default=5, help="number of seed chunks (default: 5)")
    exp_ret.add_argument(
        "--hops", type=_nonneg, default=1, help="graph traversal hops (default: 1)"
    )
    exp_ret.add_argument(
        "--min-confidence",
        type=float,
        default=None,
        help="confidence threshold for CALLS edges",
    )
    exp_ret.add_argument("--json", action="store_true", help="output explanation as JSON")
    exp_ret.set_defaults(func=cmd_explain)

    if argv is None:
        try:
            import argcomplete

            argcomplete.autocomplete(p)
        except Exception:
            pass

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
    except _GraphLimitExceeded as exc:
        print(f"repo2graph: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
