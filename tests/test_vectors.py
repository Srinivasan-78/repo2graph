"""Change 1 -- make vectors real. AC-10 .. AC-21, plus AC-36 .. AC-38.

Nothing here imports sentence-transformers, and nothing here needs numpy: every
embedder is a stub from conftest.py, and the one criterion that is *about*
numpy (AC-13) blocks the import rather than requiring it.

No score value, rank number or float comparison is asserted. AC-21 proves
fusion is live purely by chunk identity, with the dense ranking supplied
explicitly by the test.
"""

import json
import math
import os
import sys
from pathlib import Path

import pytest

from conftest import MINI_QUERY, StubEmbedder, build_mini_index
from repo2graph.cli import main
from repo2graph.export import path as artifact_path
from repo2graph.query import Index, read_jsonl

VEC_NPY = "vectors.npy"
VEC_META = "vectors.meta.json"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def vec_paths(outdir: Path):
    agent = Path(outdir) / "agent"
    return agent / VEC_NPY, agent / VEC_META


def run_embed(outdir: Path, capsys, *extra):
    """`repo2graph embed` -> the JSON report it printed."""
    main(["embed", "-o", str(outdir), *extra])
    return json.loads(capsys.readouterr().out)


def chunk_records(outdir: Path):
    return read_jsonl(artifact_path(outdir, "chunks.jsonl"))


def read_meta(outdir: Path) -> dict:
    _npy, meta = vec_paths(outdir)
    with open(meta, encoding="utf8", newline="\n") as fh:
        return json.load(fh)


def read_manifest(outdir: Path) -> dict:
    with open(artifact_path(outdir, "manifest.json"), encoding="utf8", newline="\n") as fh:
        return json.load(fh)


def sim_vector(sim: float) -> list[float]:
    """A 2-D unit vector whose cosine against [1.0, 0.0] is exactly `sim`."""
    return [sim, math.sqrt(max(0.0, 1.0 - sim * sim))]


def rigged_order(candidates):
    """The dense ranking this suite rigs: BM25 rank 2 first, rank 1 third.

    Swapping only ranks 1 and 2 is not enough -- reciprocal rank fusion gives
    the two chunks identical fused scores and the outcome turns on a tie-break.
    Demoting the BM25 winner to dense rank 3 makes the intended chunk win
    outright, whatever the tie-break rule is.
    """
    assert len(candidates) >= 4, "the fixture must offer at least 4 candidates"
    rest = [c for c in candidates if c not in (candidates[0], candidates[1], candidates[2])]
    return [candidates[1], candidates[2], candidates[0], *rest]


def rigged_sims(candidates):
    """{chunk index: vector} giving `rigged_order` against the query vector."""
    order = rigged_order(candidates)
    step = 1.0 / (len(order) + 1)
    return {idx: sim_vector(1.0 - n * step) for n, idx in enumerate(order)}


# ==========================================================================
# AC-10 / AC-11 -- the `embed` subcommand and its metadata
# ==========================================================================


def test_ac10_embed_writes_both_artifacts_and_reports_the_count(
    mini_index, use_stub_embedder, capsys
):
    """AC-10: vectors.npy + vectors.meta.json, and `vectors` == chunk count."""
    npy, meta = vec_paths(mini_index)
    assert not npy.exists() and not meta.exists()

    report = run_embed(mini_index, capsys)

    assert npy.exists(), "vectors.npy was not written"
    assert meta.exists(), "vectors.meta.json was not written"
    assert report["vectors"] == len(chunk_records(mini_index))
    assert npy.stat().st_size > 0


def test_ac11_vector_meta_fields(mini_index, use_stub_embedder, capsys):
    """AC-11: model_id, dim, count, chunk_ids and text_hashes, all consistent."""
    run_embed(mini_index, capsys)
    meta = read_meta(mini_index)

    assert isinstance(meta["model_id"], str) and meta["model_id"]
    assert isinstance(meta["dim"], int) and meta["dim"] > 0
    assert isinstance(meta["count"], int)
    assert isinstance(meta["chunk_ids"], list)
    assert isinstance(meta["text_hashes"], list)
    assert len(meta["chunk_ids"]) == meta["count"]
    assert len(meta["text_hashes"]) == meta["count"]
    assert all(isinstance(c, str) for c in meta["chunk_ids"])
    assert all(isinstance(h, str) for h in meta["text_hashes"])

    on_disk = {c["id"] for c in chunk_records(mini_index)}
    assert set(meta["chunk_ids"]) <= on_disk
    assert meta["count"] == len(chunk_records(mini_index))


def test_ac11_text_hashes_track_the_chunk_text(mini_index, use_stub_embedder, capsys):
    """AC-11: text_hashes[row] is embed.text_hash of the chunk at that row."""
    from repo2graph import embed

    run_embed(mini_index, capsys)
    meta = read_meta(mini_index)
    by_id = {c["id"]: c for c in chunk_records(mini_index)}
    for cid, digest in zip(meta["chunk_ids"], meta["text_hashes"], strict=True):
        assert embed.text_hash(by_id[cid]) == digest, cid


# ==========================================================================
# AC-12 / AC-13 -- the .npy reader/writer
# ==========================================================================

SAMPLE_VECTORS = {
    "sym:a.py::one": [0.5, -0.25, 0.125, 1.0],
    "sym:b.py::two": [-1.0, 0.0, 0.75, -0.0625],
    "file:c.md#0": [0.0, 0.0, 0.0, 0.0],
}
SAMPLE_IDS = list(SAMPLE_VECTORS)


def test_ac12_write_then_load_round_trips_exactly(tmp_path):
    """AC-12: same ids, same dim, every float equal after float32 rounding."""
    import struct

    from repo2graph import embed

    target = tmp_path / VEC_NPY
    n = embed.write_vectors(target, SAMPLE_VECTORS, "stub/mini-v1", 4, SAMPLE_IDS)
    assert n == len(SAMPLE_IDS)

    loaded, meta = embed.load_vectors(target)
    assert set(loaded) == set(SAMPLE_VECTORS)
    assert meta["dim"] == 4
    assert meta["model_id"] == "stub/mini-v1"
    for cid, want in SAMPLE_VECTORS.items():
        got = loaded[cid]
        assert len(got) == 4
        for a, b in zip(got, want, strict=True):
            # float32 rounding is the only transformation allowed
            assert a == struct.unpack("<f", struct.pack("<f", b))[0], cid


def test_ac12_meta_is_a_sibling_json_file(tmp_path):
    """AC-12: the pair is <name>.npy + vectors.meta.json next to it."""
    from repo2graph import embed

    target = tmp_path / VEC_NPY
    embed.write_vectors(target, SAMPLE_VECTORS, "stub/mini-v1", 4, SAMPLE_IDS)
    assert (tmp_path / VEC_META).exists()
    assert target.exists()
    # binary, opened "wb": a text-mode write on Windows corrupts every 0x0A
    assert b"\r\n" not in target.read_bytes()[:128]


def block_numpy(monkeypatch):
    """Make `import numpy` fail, the way a no-extras install would."""
    monkeypatch.setitem(sys.modules, "numpy", None)


def test_ac13_round_trip_works_with_numpy_unimportable(tmp_path, monkeypatch):
    """AC-13 (a): the stdlib reader/writer stands alone."""
    from repo2graph import embed

    block_numpy(monkeypatch)
    with pytest.raises(ImportError):
        import numpy  # noqa: F401

    target = tmp_path / VEC_NPY
    embed.write_vectors(target, SAMPLE_VECTORS, "stub/mini-v1", 4, SAMPLE_IDS)
    loaded, meta = embed.load_vectors(target)
    assert set(loaded) == set(SAMPLE_VECTORS)
    assert meta["dim"] == 4


def test_ac13_numpy_can_still_read_what_the_stdlib_writer_wrote(tmp_path, monkeypatch):
    """AC-13 (b): the file stays a real .npy, not a private format."""
    from repo2graph import embed

    block_numpy(monkeypatch)
    target = tmp_path / VEC_NPY
    embed.write_vectors(target, SAMPLE_VECTORS, "stub/mini-v1", 4, SAMPLE_IDS)
    monkeypatch.undo()

    numpy = pytest.importorskip("numpy")
    arr = numpy.load(str(target))
    assert arr.shape == (len(SAMPLE_IDS), 4)
    assert arr.dtype == numpy.dtype("<f4")
    for row, cid in enumerate(SAMPLE_IDS):
        for col, want in enumerate(SAMPLE_VECTORS[cid]):
            assert float(arr[row][col]) == pytest.approx(want, rel=0, abs=1e-6)


# ==========================================================================
# AC-14 -- manifest registration
# ==========================================================================


def test_ac14_embed_appends_to_the_manifest_without_disturbing_it(
    mini_index, use_stub_embedder, capsys
):
    """AC-14: the two artifacts appear in `written` and `files`; every other
    manifest key is byte-identical to what `build` wrote."""
    before = read_manifest(mini_index)
    run_embed(mini_index, capsys)
    after = read_manifest(mini_index)

    assert f"agent/{VEC_NPY}" in after["written"], after["written"]
    assert f"agent/{VEC_META}" in after["written"], after["written"]
    assert VEC_NPY in after["files"], after["files"]
    assert VEC_META in after["files"], after["files"]
    assert after["files"][VEC_NPY].strip()
    assert after["files"][VEC_META].strip()

    assert set(before["written"]) <= set(after["written"])
    for key in before:
        if key in ("written", "files", "checksums"):
            # `embed` is allowed to extend `written`, `files`, and `checksums`
            # with the two new vector artifacts; all other keys must be stable.
            continue
        assert after[key] == before[key], key
    for name, note in before["files"].items():
        assert after["files"][name] == note, name
    # Checksums can only grow: any entry that existed before embed must not change
    for rel, ck in before.get("checksums", {}).items():
        assert after.get("checksums", {}).get(rel) == ck, (rel, ck)


# ==========================================================================
# AC-15 / AC-16 -- Index auto-load
# ==========================================================================


def test_ac15_index_loads_vectors_keyed_by_chunk_list_index(mini_index, use_stub_embedder, capsys):
    """AC-15: idx.vectors keys are valid chunk list indices and the stored
    model id is reported on idx.vector_meta."""
    run_embed(mini_index, capsys)
    idx = Index(mini_index)

    assert isinstance(idx.vectors, dict) and idx.vectors
    for key in idx.vectors:
        assert isinstance(key, int), key
        assert 0 <= key < len(idx.chunks), key
    assert idx.vector_meta["model_id"] == read_meta(mini_index)["model_id"]
    assert len(idx.vectors) == len(idx.chunks)

    # the row really is this chunk's vector, not the neighbouring row's
    emb = use_stub_embedder.last
    for i, chunk in enumerate(idx.chunks):
        want = emb.vector_for(chunk.get("text") or "")
        for a, b in zip(idx.vectors[i], want, strict=True):
            assert a == pytest.approx(b, rel=0, abs=1e-6), chunk["id"]


def test_ac15_index_without_vectors_reports_none(mini_index):
    """AC-15 (b): the attributes exist even when nothing was embedded."""
    idx = Index(mini_index)
    assert idx.vectors is None
    assert idx.vector_meta in (None, {})


CORRUPTIONS = {
    "truncated_npy": lambda npy, meta: npy.write_bytes(npy.read_bytes()[:11]),
    "empty_npy": lambda npy, meta: npy.write_bytes(b""),
    "garbage_npy": lambda npy, meta: npy.write_bytes(b"not an npy file at all"),
    "invalid_meta": lambda npy, meta: meta.write_text("{", encoding="utf8"),
    "empty_meta": lambda npy, meta: meta.write_text("", encoding="utf8"),
    "missing_meta": lambda npy, meta: meta.unlink(),
    "missing_npy": lambda npy, meta: npy.unlink(),
}


@pytest.mark.parametrize("corruption", sorted(CORRUPTIONS))
def test_ac16_corrupt_vectors_degrade_silently(mini_index, use_stub_embedder, capsys, corruption):
    """AC-16: a bad pair leaves idx.vectors None, raises nothing, and
    pack_context still answers."""
    run_embed(mini_index, capsys)
    npy, meta = vec_paths(mini_index)
    CORRUPTIONS[corruption](npy, meta)

    idx = Index(mini_index)
    assert idx.vectors is None, corruption
    pack = idx.pack_context(MINI_QUERY)
    assert "### [cite:" in pack["markdown"], corruption


def test_ac16_vectors_for_unknown_chunk_ids_are_dropped(mini_index, use_stub_embedder, capsys):
    """AC-16 (b): ids that chunks.jsonl no longer holds must not become
    wrongly-aligned rows -- they are dropped, and the rest still load."""
    run_embed(mini_index, capsys)
    _npy, meta_path = vec_paths(mini_index)
    meta = read_meta(mini_index)
    meta["chunk_ids"] = ["sym:gone.py::vanished"] + meta["chunk_ids"][1:]
    with open(meta_path, "w", encoding="utf8", newline="\n") as fh:
        json.dump(meta, fh)

    idx = Index(mini_index)
    assert idx.vectors is not None
    assert len(idx.vectors) == len(idx.chunks) - 1
    assert all(0 <= k < len(idx.chunks) for k in idx.vectors)


# ==========================================================================
# ISS-242 -- the on-disk format marker is read back
# ==========================================================================
#
# `write_vectors` has stamped `"format": "repo2graph/vectors-1"` since the
# commit that introduced embed.py, but `load_vectors` only ever validated
# chunk_ids, the row count and the width. A future layout that kept those key
# names while changing what they denote would therefore load clean, and
# because `query.Index._load_vectors` swallows every exception in favour of
# BM25, the damage would surface as plausible, wrong rankings rather than an
# error. These pin the marker check itself, and the degradation it produces.
#
# The expected marker is written out as a literal here on purpose: asserting
# against `embed.VECTORS_FORMAT` would compare the implementation with itself
# and stay green through a silent rename of the on-disk value.

VECTORS_FORMAT_ON_DISK = "repo2graph/vectors-1"


def rewrite_meta(meta_file: Path, mutate) -> None:
    """Load a vectors.meta.json, hand it to `mutate`, write it back."""
    with open(meta_file, encoding="utf8", newline="\n") as fh:
        meta = json.load(fh)
    mutate(meta)
    with open(meta_file, "w", encoding="utf8", newline="\n") as fh:
        json.dump(meta, fh)


def test_iss242_write_vectors_stamps_the_literal_format_marker(tmp_path):
    """ISS-242 (a): the value on disk is the one this suite checks against."""
    from repo2graph import embed

    target = tmp_path / VEC_NPY
    embed.write_vectors(target, SAMPLE_VECTORS, "stub/mini-v1", 4, SAMPLE_IDS)
    with open(tmp_path / VEC_META, encoding="utf8", newline="\n") as fh:
        assert json.load(fh)["format"] == VECTORS_FORMAT_ON_DISK


def test_iss242_load_vectors_accepts_a_correctly_stamped_pair(tmp_path):
    """ISS-242 (b), neutrality: the good case must still load, or the gate is
    just 'vectors are off'."""
    from repo2graph import embed

    target = tmp_path / VEC_NPY
    embed.write_vectors(target, SAMPLE_VECTORS, "stub/mini-v1", 4, SAMPLE_IDS)

    loaded, meta = embed.load_vectors(target)
    assert set(loaded) == set(SAMPLE_VECTORS)
    assert meta["format"] == VECTORS_FORMAT_ON_DISK


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m.__setitem__("format", "repo2graph/vectors-2"),
        lambda m: m.__setitem__("format", "repo2graph/vectors-1 "),
        lambda m: m.__setitem__("format", VECTORS_FORMAT_ON_DISK.upper()),
        lambda m: m.__setitem__("format", None),
        lambda m: m.__setitem__("format", 1),
        lambda m: m.pop("format"),
    ],
    ids=["later", "trailing_space", "upper", "null", "int", "absent"],
)
def test_iss242_load_vectors_refuses_an_unrecognised_format(tmp_path, mutate):
    """ISS-242 (c): anything but the exact marker -- including no marker at all
    -- is a ValueError, the same shape as the chunk_ids and row-count checks."""
    from repo2graph import embed

    target = tmp_path / VEC_NPY
    embed.write_vectors(target, SAMPLE_VECTORS, "stub/mini-v1", 4, SAMPLE_IDS)
    rewrite_meta(tmp_path / VEC_META, mutate)

    with pytest.raises(ValueError):
        embed.load_vectors(target)


def test_iss242_a_bad_format_degrades_to_bm25_end_to_end(mini_index, use_stub_embedder, capsys):
    """ISS-242 (d): through query.Index, a format bump is indistinguishable
    from having no vectors -- nothing raises, and the pack still answers.

    The correctly-stamped load is asserted first so this cannot pass by
    fusion being dead in the fixture for some unrelated reason.
    """
    run_embed(mini_index, capsys)
    _npy, meta_file = vec_paths(mini_index)
    assert Index(mini_index).vectors, "the fixture must load vectors before it is broken"

    rewrite_meta(meta_file, lambda m: m.__setitem__("format", "repo2graph/vectors-2"))
    idx = Index(mini_index)
    assert idx.vectors is None
    assert idx.vector_meta in (None, {})
    assert "### [cite:" in idx.pack_context(MINI_QUERY)["markdown"]

    rewrite_meta(meta_file, lambda m: m.pop("format"))
    idx = Index(mini_index)
    assert idx.vectors is None
    assert "### [cite:" in idx.pack_context(MINI_QUERY)["markdown"]


# ==========================================================================
# AC-17 / AC-18 / AC-19 / AC-20 -- the mismatch guard
# ==========================================================================


def test_ac17_model_id_of_reads_the_embedder(mini_index):
    """AC-17 (a): the identity fuse_ok compares comes off the embedder."""
    from repo2graph import embed

    assert embed.model_id_of(StubEmbedder(model_id="stub/alpha")) == "stub/alpha"
    assert embed.DEFAULT_MODEL == "sentence-transformers/all-MiniLM-L6-v2"


def test_ac17_fuse_ok_rejects_a_model_mismatch(mini_index, use_stub_embedder, capsys):
    """AC-17 (b): (False, reason) naming the index model and the query model."""
    use_stub_embedder.model_id = "stub/alpha"
    run_embed(mini_index, capsys)
    idx = Index(mini_index)

    ok, reason = idx.fuse_ok(StubEmbedder(model_id="stub/beta", dim=8))
    assert ok is False
    assert "stub/alpha" in reason, reason
    assert "stub/beta" in reason, reason


def test_ac17_fuse_ok_rejects_a_dim_mismatch(mini_index, use_stub_embedder, capsys):
    """AC-17 (c): same model id, different width, still refused -- and the
    reason names both widths."""
    use_stub_embedder.model_id = "stub/alpha"
    use_stub_embedder.dim = 8
    run_embed(mini_index, capsys)
    idx = Index(mini_index)

    ok, reason = idx.fuse_ok(StubEmbedder(model_id="stub/alpha", dim=16))
    assert ok is False
    assert "8" in reason and "16" in reason, reason


def test_ac17_fuse_ok_accepts_a_match(mini_index, use_stub_embedder, capsys):
    """AC-17 (d): the matching case is accepted, or the guard is just 'off'."""
    use_stub_embedder.model_id = "stub/alpha"
    run_embed(mini_index, capsys)
    idx = Index(mini_index)
    ok, _reason = idx.fuse_ok(StubEmbedder(model_id="stub/alpha", dim=8))
    assert ok is True


@pytest.fixture
def mismatched_index(mini_index, use_stub_embedder, capsys):
    """An index embedded with `stub/alpha`, with `stub/beta` now active."""
    use_stub_embedder.model_id = "stub/alpha"
    run_embed(mini_index, capsys)
    use_stub_embedder.model_id = "stub/beta"
    return mini_index


def test_ac18_rag_vectors_on_a_mismatch_exits_and_emits_no_pack(mismatched_index, capsys):
    """AC-18: explicit --vectors fails loudly, names both model ids, and does
    not print a pack."""
    with pytest.raises(SystemExit) as exc:
        main(["rag", MINI_QUERY, "-o", str(mismatched_index), "--vectors"])
    assert exc.value.code not in (0, None)
    message = str(exc.value)
    assert "stub/alpha" in message, message
    assert "stub/beta" in message, message
    assert "### [cite:" not in capsys.readouterr().out


def test_ac19_rag_without_a_vector_flag_degrades_to_bm25(mismatched_index, capsys):
    """AC-19: auto mode exits 0, emits a pack, and does not fuse -- proved by
    the pack being identical to the same query with vectors switched off."""
    rc = main(["rag", MINI_QUERY, "-o", str(mismatched_index)])
    auto = capsys.readouterr().out
    assert rc == 0
    assert "### [cite:" in auto

    main(["rag", MINI_QUERY, "-o", str(mismatched_index), "--no-vectors"])
    off = capsys.readouterr().out
    assert auto == off


def test_ac20_no_vectors_equals_an_index_with_the_files_deleted(
    mini_index, use_stub_embedder, capsys
):
    """AC-20: `rag --no-vectors` against a *matching* vectorised index gives
    exactly what the same command gives once the two files are removed."""
    run_embed(mini_index, capsys)
    main(["rag", MINI_QUERY, "-o", str(mini_index), "--no-vectors"])
    with_files = capsys.readouterr().out

    npy, meta = vec_paths(mini_index)
    npy.unlink()
    meta.unlink()
    main(["rag", MINI_QUERY, "-o", str(mini_index), "--no-vectors"])
    without_files = capsys.readouterr().out

    assert with_files == without_files
    assert "### [cite:" in with_files


def test_ac20_no_vectors_matches_the_baseline_golden(mini_index, use_stub_embedder, capsys):
    """AC-20 / AC-2: --no-vectors is the baseline BM25 pack, byte for byte."""
    from conftest import golden_text

    run_embed(mini_index, capsys)
    main(["rag", MINI_QUERY, "-o", str(mini_index), "--no-vectors"])
    assert capsys.readouterr().out == golden_text("rag_markdown.md")


# ==========================================================================
# AC-21 -- fusion is live
# ==========================================================================


def test_ac21_a_rigged_dense_ranking_changes_the_top_chunk(big_index):
    """AC-21 (a): via the in-memory `vectors=` contract Index will populate.

    The dense ranking is supplied by the test, so the only thing asserted is
    which chunk *id* comes first -- no score, no rank number, no float.
    """
    from repo2graph.query import RRF_CANDIDATES

    idx = Index(big_index)
    base = idx.score(MINI_QUERY)
    candidates = [i for _s, i in base[:RRF_CANDIDATES]]
    vectors = rigged_sims(candidates)
    vectors["query"] = sim_vector(1.0)

    fused = idx.score_rrf(MINI_QUERY, vectors=vectors)
    bm25_top = idx.chunks[base[0][1]]["id"]
    fused_top = idx.chunks[fused[0][1]]["id"]
    expected = idx.chunks[candidates[1]]["id"]

    assert bm25_top != expected, "the rig must point at a different chunk"
    assert fused_top == expected, (fused_top, expected, bm25_top)
    assert fused_top != bm25_top


def test_ac21_a_rigged_embedder_changes_the_top_chunk(big_index):
    """AC-21 (b): the same through the `embedder=` path, with a scripted
    embedder that never looks at the text it is handed."""
    from conftest import ScriptedEmbedder
    from repo2graph.query import RRF_CANDIDATES

    idx = Index(big_index)
    base = idx.score(MINI_QUERY)
    candidates = [i for _s, i in base[:RRF_CANDIDATES]]
    rig = rigged_sims(candidates)
    scripted = [sim_vector(1.0)] + [rig[i] for i in candidates]

    fused = idx.score_rrf(MINI_QUERY, embedder=ScriptedEmbedder(scripted))
    assert idx.chunks[fused[0][1]]["id"] == idx.chunks[candidates[1]]["id"]
    assert idx.chunks[fused[0][1]]["id"] != idx.chunks[base[0][1]]["id"]


def test_iss157_mismatched_vector_dims_degrade_to_bm25(big_index):
    """ISS-157 regression: a candidate vector whose width does not match the
    query vector's must not silently truncate through zip() and produce a
    meaningless cosine score. It must disable fusion and fall back to BM25,
    same as any other malformed vector (AC-16) -- never raise out of
    score_rrf, and never fuse a truncated, meaningless similarity in."""
    from repo2graph.query import RRF_CANDIDATES

    idx = Index(big_index)
    base = idx.score(MINI_QUERY)
    candidates = [i for _s, i in base[:RRF_CANDIDATES]]
    vectors = rigged_sims(candidates)
    # sim_vector() is 2-D; give exactly one candidate a 3-D vector so its
    # width no longer matches the query vector's.
    mismatched = candidates[0]
    vectors[mismatched] = vectors[mismatched] + [0.5]
    vectors["query"] = sim_vector(1.0)

    fused = idx.score_rrf(MINI_QUERY, vectors=vectors)

    assert fused == base, "one bad-width vector must abandon fusion entirely"
    assert idx.fusion_coverage == (0, len(candidates))


def test_ac21_rag_vectors_uses_the_persisted_vectors(mini_index, use_stub_embedder, capsys):
    """AC-21 (c): `rag --vectors` on a matching index succeeds and consults the
    embedder (so the flag is wired through, not silently a no-op)."""
    run_embed(mini_index, capsys)
    before = len(use_stub_embedder.made)
    rc = main(["rag", MINI_QUERY, "-o", str(mini_index), "--vectors"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "### [cite:" in out
    assert len(use_stub_embedder.made) > before, "no query embedder was built"
    assert use_stub_embedder.last.calls, "the query was never embedded"


# ==========================================================================
# AC-36 / AC-37 / AC-38 -- vector reuse
# ==========================================================================


def test_ac36_second_embed_reuses_everything(mini_index, use_stub_embedder, capsys):
    """AC-36: reused == vectors, embedded == 0, and vectors.npy is byte-identical."""
    first = run_embed(mini_index, capsys)
    npy, _meta = vec_paths(mini_index)
    first_bytes = npy.read_bytes()

    second = run_embed(mini_index, capsys)
    assert second["vectors"] == first["vectors"]
    assert second["reused"] == second["vectors"]
    assert second["embedded"] == 0
    assert npy.read_bytes() == first_bytes


def test_ac37_only_changed_chunks_are_re_embedded(mini_repo, tmp_path, use_stub_embedder, capsys):
    """AC-37: embedded >= 1, reused >= 1, and `encode` saw only the texts of
    the chunks whose text actually changed."""
    out = build_mini_index(mini_repo, tmp_path / "reuse_idx")
    capsys.readouterr()  # the build report is not ours to parse
    run_embed(out, capsys)
    before = {c["id"]: c.get("text") or "" for c in chunk_records(out)}

    # The edit must land INSIDE audit_event's body: pkg/audit.py's only chunk
    # is the symbol chunk for that function, so a module-level append would
    # change no chunk text at all and prove nothing.
    target = mini_repo / "pkg" / "audit.py"
    original = target.read_text(encoding="utf8")
    edited = original.replace(
        '    return {"event": name}',
        '    journal_version = 2\n    return {"event": name, "v": journal_version}',
    )
    assert edited != original, "the fixture's audit_event body is not the shape this edit expects"
    with open(target, "w", encoding="utf8", newline="\n") as fh:
        fh.write(edited)
    build_mini_index(mini_repo, out)
    capsys.readouterr()  # ditto for the rebuild

    after = {c["id"]: c.get("text") or "" for c in chunk_records(out)}
    changed = {text for cid, text in after.items() if before.get(cid) != text}
    unchanged = {text for cid, text in after.items() if before.get(cid) == text}
    assert changed, "the edit produced no changed chunk"
    assert unchanged, "the edit changed every chunk; the fixture proves nothing"

    fresh = len(use_stub_embedder.made)
    report = run_embed(out, capsys)
    assert report["embedded"] >= 1
    assert report["reused"] >= 1
    assert report["embedded"] + report["reused"] == report["vectors"]
    assert report["embedded"] == len(changed)

    seen = set()
    for emb in use_stub_embedder.made[fresh:]:
        seen.update(emb.embedded_texts)
    assert changed <= seen, "a changed chunk was not re-embedded"
    assert not (unchanged & seen), "an unchanged chunk was re-embedded"


def test_ac38_force_disables_reuse(mini_index, use_stub_embedder, capsys):
    """AC-38: `embed --force` after a successful embed reports reused == 0."""
    first = run_embed(mini_index, capsys)
    forced = run_embed(mini_index, capsys, "--force")
    assert forced["reused"] == 0
    assert forced["embedded"] == forced["vectors"] == first["vectors"]


def test_ac10_embed_reports_model_and_dim(mini_index, use_stub_embedder, capsys):
    """AC-10 / AC-36: the report carries every key the three reuse criteria
    read, so a partial implementation fails here rather than with a KeyError."""
    report = run_embed(mini_index, capsys)
    assert set(report) >= {"vectors", "reused", "embedded", "model", "dim"}
    assert report["model"] == read_meta(mini_index)["model_id"]
    assert report["dim"] == read_meta(mini_index)["dim"]


# ==========================================================================
# Regressions -- REVIEW iteration 2
# ==========================================================================
#
# R-1..R-4 pin the fix for a defect that every AC above missed: `cli.py` read
# `args.embed_model`, a dest no parser defined, so `query`/`rag` could only
# ever resolve the built-in default model. Exiting 0 was not enough to catch
# it -- these assert the *resolved model name that reaches default_embedder*,
# which is the observation the original suite never made.


def test_r1_rag_embed_model_reaches_the_query_embedder(mini_index, use_stub_embedder, capsys):
    """R-1: `rag --vectors --embed-model X` embeds the query with X."""
    run_embed(mini_index, capsys, "--embed-model", "stub/custom")
    assert read_meta(mini_index)["model_id"] == "stub/custom"

    use_stub_embedder.names.clear()
    rc = main(
        ["rag", MINI_QUERY, "-o", str(mini_index), "--vectors", "--embed-model", "stub/custom"]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "### [cite:" in out
    assert use_stub_embedder.names == ["stub/custom"], use_stub_embedder.names
    assert use_stub_embedder.last.model_id == "stub/custom"
    assert use_stub_embedder.last.calls, "the query was never embedded"


def test_r1_query_embed_model_reaches_the_query_embedder(mini_index, use_stub_embedder, capsys):
    """R-1 (b): the same on `query`, which shares the helper but not the test."""
    run_embed(mini_index, capsys, "--embed-model", "stub/custom")

    use_stub_embedder.names.clear()
    rc = main(
        ["query", MINI_QUERY, "-o", str(mini_index), "--vectors", "--embed-model", "stub/custom"]
    )
    capsys.readouterr()
    assert rc == 0
    assert use_stub_embedder.names == ["stub/custom"], use_stub_embedder.names


def test_r2_without_embed_model_a_custom_model_index_is_refused(
    mini_index, use_stub_embedder, capsys
):
    """R-2: the flag is load-bearing, not cosmetic -- omit it against an index
    embedded with a non-default model and the guard fires, naming both."""
    run_embed(mini_index, capsys, "--embed-model", "stub/custom")
    with pytest.raises(SystemExit) as exc:
        main(["rag", MINI_QUERY, "-o", str(mini_index), "--vectors"])
    message = str(exc.value)
    assert "stub/custom" in message, message
    assert "stub/mini-v1" in message, message
    assert "### [cite:" not in capsys.readouterr().out


def test_r3_rag_model_stays_the_llm_model(mini_index, use_stub_embedder, monkeypatch, capsys):
    """R-3: `--model` and `--embed-model` are two dests, not one. The LLM name
    must never be handed to default_embedder, nor the checkpoint to the LLM."""
    import repo2graph.answer as answer_mod

    run_embed(mini_index, capsys)
    seen = {}
    monkeypatch.setattr(
        answer_mod,
        "stream_answer",
        lambda pack, model=None, provider=None: seen.update(model=model, provider=provider),
    )

    use_stub_embedder.names.clear()
    rc = main(
        [
            "rag",
            MINI_QUERY,
            "-o",
            str(mini_index),
            "--answer",
            "--model",
            "gpt-4o",
            "--vectors",
            "--embed-model",
            "stub/mini-v1",
        ]
    )
    assert rc == 0
    assert seen["model"] == "gpt-4o", seen
    assert use_stub_embedder.names == ["stub/mini-v1"], use_stub_embedder.names


def test_r4_the_default_path_builds_no_embedder(mini_index, use_stub_embedder, capsys):
    """R-4: dense fusion is opt-in, so neither `rag` nor `query` constructs an
    embedder without the flag -- constructing one downloads ~90 MB on a cold
    cache, and the default path promises no network."""
    run_embed(mini_index, capsys)
    before = len(use_stub_embedder.made)

    assert main(["rag", MINI_QUERY, "-o", str(mini_index)]) == 0
    assert "### [cite:" in capsys.readouterr().out
    assert main(["query", MINI_QUERY, "-o", str(mini_index)]) == 0
    capsys.readouterr()

    assert len(use_stub_embedder.made) == before, use_stub_embedder.names


def test_r4_explicit_vectors_still_builds_one(mini_index, use_stub_embedder, capsys):
    """R-4 (b): the guard above must not pass by fusion being dead everywhere."""
    run_embed(mini_index, capsys)
    before = len(use_stub_embedder.made)
    assert main(["rag", MINI_QUERY, "-o", str(mini_index), "--vectors"]) == 0
    capsys.readouterr()
    assert len(use_stub_embedder.made) == before + 1


@pytest.mark.skipif(
    not os.environ.get("R2G_TEST_REAL_EMBEDDER"),
    reason="Real embedder smoke test requires R2G_TEST_REAL_EMBEDDER=1",
)
def test_real_sentence_transformers_smoke_test(mini_index):
    """Smoke test against the real sentence-transformers wrapper."""
    from repo2graph import embed
    from repo2graph.query import Index

    try:
        real_emb = embed.default_embedder()
    except RuntimeError:
        pytest.skip("rag extra is not installed")

    idx_before = Index(mini_index)
    chunks = idx_before.chunks[:2]
    vectors = embed.build_vectors(chunks, real_emb, batch=2)

    model_id = embed.model_id_of(real_emb)
    dim = embed.dim_of(real_emb)
    chunk_ids = [c["id"] for c in chunks]
    text_hashes = [embed.text_hash(c) for c in chunks]

    agent_dir = mini_index / "agent"
    agent_dir.mkdir(exist_ok=True)
    target = agent_dir / VEC_NPY

    embed.write_vectors(target, vectors, model_id, dim, chunk_ids, text_hashes)

    idx = Index(mini_index)
    assert idx.vectors is not None

    ok, reason = idx.fuse_ok(real_emb)
    assert ok is True
    assert reason == ""


# --------------------------------------------------------------------------
# Bounded reads: an index may have been built elsewhere
#
# `doctor` on a received index is the documented way to check one, and indexes
# travel on a `graph` branch, as Action artifacts and in examples/. That makes
# every byte count in these files attacker-chosen, and a plain `.read()` an
# allocation somebody else picks.
# --------------------------------------------------------------------------


def test_read_bounded_refuses_a_file_over_the_limit(tmp_path):
    from repo2graph.integrity import read_bounded

    f = tmp_path / "big.bin"
    f.write_bytes(b"x" * 100)

    assert read_bounded(f, 100) == b"x" * 100  # exactly at the limit is fine
    with pytest.raises(ValueError, match="over the 99-byte limit"):
        read_bounded(f, 99)


def test_an_oversized_vectors_npy_is_refused_before_it_is_read(tmp_path, monkeypatch):
    """The ceiling must bind before the bytes are materialised."""
    from repo2graph import embed, integrity

    npy = tmp_path / "vectors.npy"
    embed.write_vectors(npy, {"a": [0.1, 0.2], "b": [0.3, 0.4]}, "stub-model", 2, ["a", "b"])
    assert npy.stat().st_size > 16

    monkeypatch.setattr(integrity, "MAX_VECTORS_BYTES", 16)
    with pytest.raises(ValueError, match="over the 16-byte limit"):
        embed._npy_read(npy)


def test_an_oversized_vectors_meta_is_refused(tmp_path, monkeypatch):
    """The sidecar is read *before* the array, so it needs its own ceiling.

    A limit on vectors.npy alone would leave the whole allocation reachable
    through this file instead.
    """
    from repo2graph import embed, integrity

    npy = tmp_path / "vectors.npy"
    embed.write_vectors(npy, {"a": [0.1, 0.2]}, "stub-model", 2, ["a"])

    monkeypatch.setattr(integrity, "MAX_METADATA_BYTES", 8)
    with pytest.raises(ValueError, match="over the 8-byte limit"):
        embed.load_vectors(npy)


def test_an_oversized_manifest_is_a_corrupt_index_not_a_crash(tmp_path, monkeypatch):
    from repo2graph import integrity

    idx = tmp_path / "idx"
    (idx / "agent").mkdir(parents=True)
    for name in ("nodes.jsonl", "edges.jsonl", "chunks.jsonl"):
        (idx / "agent" / name).write_text("", encoding="utf8")
    (idx / "agent" / "manifest.json").write_text(
        json.dumps({"format": "repo2graph/1", "checksums": {}}), encoding="utf8"
    )

    monkeypatch.setattr(integrity, "MAX_METADATA_BYTES", 8)
    report = integrity.verify_artifacts(idx)

    assert report.status == "corrupt"
    assert any("Corrupt manifest.json" in e for e in report.errors), report.errors


def test_a_normal_vectors_pair_still_round_trips_under_the_real_limits(tmp_path):
    """The guard must reject oversized files only -- not every file."""
    from repo2graph import embed

    npy = tmp_path / "vectors.npy"
    embed.write_vectors(npy, {"a": [0.1, 0.2], "b": [0.3, 0.4]}, "stub-model", 2, ["a", "b"])

    vectors, meta = embed.load_vectors(npy)

    assert sorted(vectors) == ["a", "b"]
    assert meta["dim"] == 2
