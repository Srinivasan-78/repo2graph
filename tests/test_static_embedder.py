"""`--embed-backend static`: dense retrieval without torch.

Most of this runs with a **fake** embedder, on purpose. `model2vec` is an
optional extra and its model is a ~31 MB download, so a suite that needed
either would be a suite most contributors skip. What actually needs the real
package is the adapter, and that is one test marked `requires_model2vec`.

The part worth testing hardest is not the arithmetic but the **guard**: two
backends write different vectors, so an index embedded by one must be refused
by the other rather than silently fused. That refusal is what keeps a wrong
answer from looking like a working one.
"""

from __future__ import annotations

import importlib.util
import json

import pytest

from repo2graph.cli import main
from repo2graph.embed import (
    DEFAULT_MODEL,
    DEFAULT_STATIC_MODEL,
    EMBED_BACKENDS,
    STATIC_ID_PREFIX,
    make_embedder,
    model_id_of,
    split_model_spec,
)
from repo2graph.export import path as artifact_path
from repo2graph.query import Index

SAMPLE = pytest.importorskip("pathlib").Path(__file__).parent / "fixtures" / "sample_repo"

has_model2vec = importlib.util.find_spec("model2vec") is not None
requires_model2vec = pytest.mark.skipif(
    not has_model2vec, reason="the optional `rag-static` extra is not installed"
)


class FakeEmbedder:
    """Satisfies the `Embedder` protocol, deterministically, with no model."""

    def __init__(self, model_id: str, dim: int = 256) -> None:
        self.model_id = model_id
        self.dim = dim

    def encode(self, texts):
        return [[float((hash(t) >> i) % 7) for i in range(self.dim)] for t in texts]


# --------------------------------------------------------------- selection


def test_both_backends_are_declared():
    assert EMBED_BACKENDS == ("sentence-transformers", "static")
    # The default stays what every published vector number was measured with.
    assert EMBED_BACKENDS[0] == "sentence-transformers"


def test_an_unknown_backend_is_refused_by_name():
    with pytest.raises(ValueError, match="unknown embed backend"):
        make_embedder(None, "onnx")


def test_the_two_backends_have_different_defaults():
    """They are different models, not two ways to run one."""
    assert DEFAULT_STATIC_MODEL != DEFAULT_MODEL
    assert "potion" in DEFAULT_STATIC_MODEL


def test_a_missing_extra_names_the_extra_to_install(monkeypatch):
    """The failure has to say what to install, not just that it failed."""
    import repo2graph.embed as embed_mod

    real = importlib.util.find_spec

    def missing(name, *a, **k):
        if name in ("model2vec", "sentence_transformers"):
            return None
        return real(name, *a, **k)

    monkeypatch.setattr(importlib.util, "find_spec", missing)
    monkeypatch.setitem(__import__("sys").modules, "model2vec", None)
    with pytest.raises(RuntimeError, match=r"rag-static"):
        embed_mod.static_embedder()


# ------------------------------------------------------------- the id guard


def test_the_static_backend_prefixes_its_recorded_model_id():
    """The prefix is what makes the cross-backend guard work at all.

    Without it, pointing both backends at one Hugging Face repo id would write
    two incompatible vector sets under the same recorded name, and the
    mismatch guard compares names.
    """
    assert STATIC_ID_PREFIX == "static:"
    fake = FakeEmbedder(f"{STATIC_ID_PREFIX}{DEFAULT_STATIC_MODEL}")
    assert model_id_of(fake).startswith(STATIC_ID_PREFIX)


def test_a_revision_pin_survives_the_prefix():
    """`static:org/model@sha` must still split into a repo id and a revision."""
    repo_id, revision = split_model_spec("minishlab/potion-base-8M@abc123")
    assert (repo_id, revision) == ("minishlab/potion-base-8M", "abc123")


@pytest.fixture
def static_index(tmp_path):
    """An index whose vectors were written by a fake `static:` embedder."""
    out = tmp_path / "idx"
    main(["build", str(SAMPLE), "-o", str(out), "--formats", "jsonl"])

    from repo2graph.embed import build_vectors, write_vectors
    from repo2graph.query import read_jsonl

    chunks = read_jsonl(artifact_path(out, "chunks.jsonl"))
    embedder = FakeEmbedder(f"{STATIC_ID_PREFIX}{DEFAULT_STATIC_MODEL}")
    vectors = build_vectors(chunks, embedder)
    write_vectors(
        artifact_path(out, "vectors.npy"),
        vectors,
        model_id_of(embedder),
        embedder.dim,
        [c["id"] for c in chunks if c.get("id")],
    )
    return out


@pytest.mark.parametrize(
    "model_id, dim, ok",
    [
        (f"{STATIC_ID_PREFIX}{DEFAULT_STATIC_MODEL}", 256, True),
        # The other backend's default: different model, different width.
        (DEFAULT_MODEL, 384, False),
        # Same backend, a different static model.
        (f"{STATIC_ID_PREFIX}minishlab/potion-base-32M", 256, False),
        # The same repo id without the prefix -- the case the prefix exists for.
        (DEFAULT_STATIC_MODEL, 256, False),
    ],
)
def test_an_index_is_only_fused_with_the_embedder_that_wrote_it(static_index, model_id, dim, ok):
    idx = Index(static_index)
    assert idx.vectors, "the fixture wrote no vectors"
    fused, reason = idx.fuse_ok(FakeEmbedder(model_id, dim))
    assert fused is ok
    if not ok:
        assert "mismatch" in reason.lower() or "dim" in reason.lower()


def test_the_recorded_metadata_names_the_backend(static_index):
    meta = json.loads(artifact_path(static_index, "vectors.meta.json").read_text(encoding="utf-8"))
    assert meta["model_id"].startswith(STATIC_ID_PREFIX)
    assert meta["dim"] == 256


# ------------------------------------------------------- the verify report


def test_verify_rag_reports_the_backend_honestly(static_index, capsys, monkeypatch):
    """`rag_extra_installed` must mean what its name says.

    It used to be overwritten to True whenever *any* embedder loaded, so a
    `static` run reported the sentence-transformers extra as installed when it
    was not. `backend_available` is the field that answers "can dense
    retrieval run here".
    """
    import repo2graph.cli_embed as mod
    import repo2graph.embed as embed_mod

    # `verify_rag` does `from .embed import make_embedder` inside the function,
    # so the patch has to land on the source module, not on `cli_embed`.
    monkeypatch.setattr(
        embed_mod,
        "make_embedder",
        lambda *_a, **_k: FakeEmbedder(f"{STATIC_ID_PREFIX}{DEFAULT_STATIC_MODEL}"),
    )
    monkeypatch.setattr(mod, "_rag_extra_installed", lambda: False)
    rc = main(["embed", "-o", str(static_index), "--verify-rag", "--embed-backend", "static"])
    report = json.loads(capsys.readouterr().out)
    assert rc in (0, None)
    assert report["embed_backend"] == "static"
    assert report["backend_available"] is True
    assert report["rag_extra_installed"] is False
    assert report["ok"] is True


def test_verify_rag_defaults_the_backend_field(static_index, capsys, monkeypatch):
    import repo2graph.embed as embed_mod

    monkeypatch.setattr(
        embed_mod,
        "make_embedder",
        lambda *_a, **_k: FakeEmbedder(f"{STATIC_ID_PREFIX}{DEFAULT_STATIC_MODEL}"),
    )
    main(["embed", "-o", str(static_index), "--verify-rag"])
    report = json.loads(capsys.readouterr().out)
    assert report["embed_backend"] == "sentence-transformers"


# --------------------------------------------------------- the real adapter


@requires_model2vec
def test_the_real_static_model_embeds_without_torch():
    """The one test that needs the extra, and the claim worth proving.

    `import torch` must fail: "dense retrieval without torch" is the entire
    reason this backend exists, and a transitive torch would make it pointless
    while every other test here still passed.
    """
    import sys

    embedder = make_embedder(None, "static")
    vectors = embedder.encode(["def pack_context(budget): ...", "token budget"])
    assert len(vectors) == 2
    assert len(vectors[0]) == len(vectors[1]) > 0
    assert all(isinstance(x, float) for x in vectors[0])
    assert model_id_of(embedder) == f"{STATIC_ID_PREFIX}{DEFAULT_STATIC_MODEL}"
    assert "torch" not in sys.modules, "the static backend pulled torch in"
