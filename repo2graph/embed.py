"""Chunk vectors: build them, persist them as a real .npy, read them back.

Nothing here imports numpy or sentence-transformers at module scope, and the
reader/writer is deliberately stdlib-only: a machine that merely *queries* a
shipped index must not need the `rag` extra, or the zero-dependency promise
breaks for exactly the case vectors were added for. The file written is a
plain NPY v1.0 array (C-order, '<f4', 2-D), so `numpy.load` opens it.

Vectors are keyed on disk by chunk `id`, never by row index: chunks.jsonl is
rebuilt independently of `embed`, and a row-keyed file would silently point at
the wrong text after any rebuild. `query.Index` translates id -> current list
index at load time and drops ids the current chunks.jsonl no longer holds.
"""
import ast
import hashlib
import json
import struct
from pathlib import Path
from typing import Protocol

from .export import atomic_write

# Small (384-dim), CPU-friendly, and the de-facto default for this kind of
# retrieval; it is also what lands in vectors.meta.json, so the mismatch guard
# always compares two resolved names and never against None.
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Embedding is dominated by model forward passes; batching amortises the call
# overhead without holding the whole corpus in memory.
DEFAULT_BATCH = 64

VECTORS_FORMAT = "repo2graph/vectors-1"

_NPY_MAGIC = b"\x93NUMPY"
# NPY v1.0 pads the header with spaces so the data starts on a 64-byte boundary.
_NPY_ALIGN = 64
_NPY_DESCR = "<f4"
_FLOAT_BYTES = 4


class Embedder(Protocol):
    """Anything that turns texts into vectors. sentence-transformers satisfies it."""

    def encode(self, texts: list[str]) -> list[list[float]]:
        ...


# ---------------------------------------------------------------- .npy ----

def _npy_header(rows: int, cols: int) -> bytes:
    body = ("{'descr': '" + _NPY_DESCR + "', 'fortran_order': False, "
            f"'shape': ({rows}, {cols}), }}")
    prefix = len(_NPY_MAGIC) + 2 + 2          # magic + version + uint16 length
    pad = -(prefix + len(body) + 1) % _NPY_ALIGN
    body = body + " " * pad + "\n"
    return (_NPY_MAGIC + bytes((1, 0)) + struct.pack("<H", len(body))
            + body.encode("latin1"))


def _npy_write(path: Path, rows: list, cols: int) -> None:
    """Write `rows` (a list of equal-length float sequences) as NPY v1.0.

    Opened "wb" with no encoding: a text-mode write on Windows rewrites every
    0x0A inside the binary payload and corrupts the file silently.
    """
    with atomic_write(path, "wb") as fh:
        fh.write(_npy_header(len(rows), cols))
        for row in rows:
            fh.write(struct.pack(f"<{cols}f", *row))


def _npy_read(path: Path) -> tuple[list, int]:
    """Read a 2-D C-order '<f4' NPY file; returns (rows, cols).

    Anything else -- a different dtype, Fortran order, a truncated header or a
    short payload -- is a ValueError, which load_vectors turns into "no
    vectors" rather than an exception in the middle of a query.
    """
    with open(path, "rb") as fh:
        raw = fh.read()
    if len(raw) < 10 or not raw.startswith(_NPY_MAGIC):
        raise ValueError(f"{path}: not an .npy file")
    major = raw[6]
    if major == 1:
        hlen, start = struct.unpack("<H", raw[8:10])[0], 10
    elif major == 2:
        if len(raw) < 12:
            raise ValueError(f"{path}: truncated .npy header")
        hlen, start = struct.unpack("<I", raw[8:12])[0], 12
    else:
        raise ValueError(f"{path}: unsupported .npy version {major}")
    header = raw[start:start + hlen]
    if len(header) != hlen:
        raise ValueError(f"{path}: truncated .npy header")
    try:
        info = ast.literal_eval(header.decode("latin1").strip())
    except (ValueError, SyntaxError) as exc:
        raise ValueError(f"{path}: unreadable .npy header: {exc}") from None
    if not isinstance(info, dict):
        raise ValueError(f"{path}: unreadable .npy header")
    if info.get("descr") != _NPY_DESCR or info.get("fortran_order"):
        raise ValueError(f"{path}: expected a C-order {_NPY_DESCR} array")
    shape = info.get("shape")
    if (not isinstance(shape, tuple) or len(shape) != 2
            or not all(isinstance(n, int) and n >= 0 for n in shape)):
        raise ValueError(f"{path}: expected a 2-D shape, got {shape!r}")
    nrows, cols = shape
    body_at = start + hlen
    if len(raw) - body_at < nrows * cols * _FLOAT_BYTES:
        raise ValueError(f"{path}: truncated .npy payload")
    out = []
    for r in range(nrows):
        off = body_at + r * cols * _FLOAT_BYTES
        out.append(list(struct.unpack_from(f"<{cols}f", raw, off)))
    return out, cols


# ------------------------------------------------------------ metadata ----

def meta_path(path) -> Path:
    """'.../vectors.npy' -> '.../vectors.meta.json' (a sibling, same stem)."""
    path = Path(path)
    return path.with_name(path.stem + ".meta.json")


def text_hash(chunk) -> str:
    """sha256 of a chunk's text. A chunk's vector depends on nothing else, so
    this is a sound reuse key even when the surrounding graph changed."""
    text = chunk if isinstance(chunk, str) else (chunk.get("text") or "")
    return hashlib.sha256(text.encode("utf8", "surrogateescape")).hexdigest()


def model_id_of(embedder) -> str:
    """A stable identity for whatever produced an index's vectors."""
    for attr in ("model_id", "name_or_path"):
        value = getattr(embedder, attr, None)
        if isinstance(value, str) and value:
            return value
    return f"{type(embedder).__module__}.{type(embedder).__qualname__}"


def dim_of(embedder, probe: str = "dimension probe") -> int:
    """The width of an embedder's output, without assuming an attribute."""
    value = getattr(embedder, "dim", None)
    if isinstance(value, int) and value > 0:
        return value
    encoded = list(embedder.encode([probe]))
    return len(encoded[0]) if encoded else 0


# ------------------------------------------------------------ building ----

def build_vectors(chunks, embedder, batch: int = DEFAULT_BATCH,
                  reuse=None) -> dict[str, list[float]]:
    """{chunk id: vector} for every chunk, embedding in batches.

    `reuse` is an optional {chunk id: vector} of vectors already known to be
    current (see cli.cmd_embed); those chunks are never handed to the embedder.
    """
    reuse = reuse or {}
    out: dict[str, list[float]] = {}
    todo = []
    for c in chunks:
        cid = c.get("id")
        if not cid:
            continue
        known = reuse.get(cid)
        if known is not None:
            out[cid] = [float(x) for x in known]
        else:
            todo.append(c)
    step = max(1, int(batch))
    for i in range(0, len(todo), step):
        part = todo[i:i + step]
        encoded = list(embedder.encode([c.get("text") or "" for c in part]))
        if len(encoded) != len(part):
            raise ValueError(
                f"embedder returned {len(encoded)} vectors for {len(part)} texts")
        for c, vec in zip(part, encoded, strict=True):
            out[c["id"]] = [float(x) for x in vec]
    return out


def write_vectors(path, vectors, model_id: str, dim: int, chunk_ids,
                  text_hashes=None) -> int:
    """Write vectors.npy plus its sibling vectors.meta.json; return the count.

    Row order is `chunk_ids` order, which is chunks.jsonl order, so re-running
    `embed` over an unchanged index reproduces the file byte for byte.
    """
    path = Path(path)
    ids = list(chunk_ids)
    hashes = list(text_hashes) if text_hashes is not None else [""] * len(ids)
    if len(hashes) != len(ids):
        raise ValueError("text_hashes must line up with chunk_ids")
    keep = [(cid, h) for cid, h in zip(ids, hashes, strict=True) if cid in vectors]
    rows = []
    for cid, _h in keep:
        vec = [float(x) for x in vectors[cid]]
        if len(vec) != dim:
            raise ValueError(f"chunk {cid} has width {len(vec)}, expected {dim}")
        rows.append(vec)
    _npy_write(path, rows, dim)
    meta = {
        "format": VECTORS_FORMAT,
        "model_id": model_id,
        "dim": int(dim),
        "count": len(rows),
        "chunk_ids": [cid for cid, _h in keep],
        "text_hashes": [h for _cid, h in keep],
    }
    with atomic_write(meta_path(path), "w", encoding="utf8", newline="\n") as fh:
        fh.write(json.dumps(meta, indent=2) + "\n")
    return len(rows)


def load_vectors(path) -> tuple[dict[str, list[float]], dict]:
    """Inverse of write_vectors: ({chunk id: vector}, meta).

    Raises ValueError/OSError on anything malformed; callers that must not fail
    (query.Index) catch both and fall back to BM25.
    """
    path = Path(path)
    with open(meta_path(path), encoding="utf8", newline="\n") as fh:
        meta = json.load(fh)
    if not isinstance(meta, dict):
        raise ValueError(f"{meta_path(path)}: expected a JSON object")
    ids = meta.get("chunk_ids")
    if not isinstance(ids, list):
        raise ValueError(f"{meta_path(path)}: chunk_ids is missing")
    rows, cols = _npy_read(path)
    if len(rows) != len(ids):
        raise ValueError(
            f"{path}: {len(rows)} rows for {len(ids)} chunk ids")
    dim = meta.get("dim")
    if isinstance(dim, int) and rows and dim != cols:
        raise ValueError(f"{path}: rows are {cols} wide, meta says {dim}")
    return dict(zip(ids, rows, strict=True)), meta


# ----------------------------------------------------------- embedders ----

class _SentenceTransformerEmbedder:
    """Adapter: sentence-transformers' ndarray output -> plain float lists."""

    def __init__(self, model, model_id: str):
        self._model = model
        self.model_id = model_id

    def encode(self, texts):
        encoded = self._model.encode(list(texts))
        return [[float(x) for x in vec] for vec in encoded]


def default_embedder(name: str | None = None):
    """Load `name` (default DEFAULT_MODEL) through sentence-transformers.

    The import is deliberately here and not at module scope: `repo2graph.embed`
    must import on a bare install, and only this function needs the extra.
    """
    model_id = name or DEFAULT_MODEL
    try:
        from sentence_transformers import SentenceTransformer
    except Exception:
        raise RuntimeError(
            "embedding needs the optional `rag` extra: "
            'pip install "repo2graph[rag]"') from None
    return _SentenceTransformerEmbedder(SentenceTransformer(model_id), model_id)
