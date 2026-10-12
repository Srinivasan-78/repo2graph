"""`repo2graph embed` and `--verify-rag`: vectors for an index, and a report on its dense path (#315)."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .query import Index
import json
from pathlib import Path
from typing import cast

from .cli import _emit, _require_index
from .export import make_path, path as artifact_path, rel as artifact_rel


def cmd_embed(args: argparse.Namespace) -> int | None:
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
    except (OSError, ValueError):
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
    return None


def _reusable_vectors(npy: Path, model_id: str, hashes: dict[str, Any]) -> dict[str, Any]:
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
    except (OSError, ValueError, KeyError, ImportError):
        return {}
    if not previous or meta.get("model_id") != model_id:
        return {}
    stored = dict(zip(meta.get("chunk_ids") or [], meta.get("text_hashes") or []))
    return {
        cid: vec
        for cid, vec in previous.items()
        if cid in hashes and stored.get(cid) == hashes[cid]
    }


def verify_rag(
    idx: Index, out: Path, embed_model: str | None = None
) -> tuple[dict[str, Any], str | None]:
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
        # Known without loading a model: false/true, never null, so a script
        # can branch on it even when the index has no vectors yet.
        "rag_extra_installed": _rag_extra_installed(),
    }
    report["vectors_unavailable"] = getattr(idx, "vectors_unavailable", None)
    if not idx.vectors:
        why = f" ({report['vectors_unavailable']})" if report["vectors_unavailable"] else ""
        return report, (
            f"no vectors in the index at {out}{why}: dense retrieval is not "
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
    except Exception:  # noqa: BLE001 - resilience boundary: external embedder inspection failure
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


def _rag_extra_installed() -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec("sentence_transformers") is not None
    except (ImportError, ValueError):
        return False


def cmd_verify_rag(args: argparse.Namespace) -> int | None:
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
