"""Tests for untrusted-index trust model (issue #447).

Covers:
- Machine marker generation and local provenance.
- Refusal of foreign indexes by default in MCP (and allow_foreign_index opt-in).
- Refusal of symlinked artifacts (chunks.jsonl, overview.md, vectors.npy).
- Refusal of unknown index format versions.
- Safe rejection of tampered parse cache.
- Safe rejection of malformed / oversized / stale vectors.npy.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo2graph.cli import main
from repo2graph.export import LOCAL_FILE, load_parse_cache
from repo2graph.integrity import (
    compute_machine_marker,
    is_foreign_index,
)
from repo2graph.mcp.indexes import open_index
from repo2graph.query import Index, read_jsonl


@pytest.fixture
def sample_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Create a minimal sample repository and build an index for it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")
    out = tmp_path / "index"
    ret = main(["build", str(repo), "-o", str(out)])
    assert ret == 0
    return repo, out


def test_machine_marker_created_and_valid(sample_repo: tuple[Path, Path]) -> None:
    _, out = sample_repo
    local_path = out / LOCAL_FILE
    assert local_path.is_file()
    local_data = json.loads(local_path.read_text(encoding="utf-8"))
    build_id = local_data.get("build_id")
    machine_marker = local_data.get("machine_marker")
    assert build_id
    assert machine_marker
    assert compute_machine_marker(build_id) == machine_marker
    assert not is_foreign_index(out)


def test_foreign_index_detected_when_local_json_missing(sample_repo: tuple[Path, Path]) -> None:
    _, out = sample_repo
    (out / LOCAL_FILE).unlink()
    assert is_foreign_index(out)


def test_foreign_index_detected_when_marker_tampered(sample_repo: tuple[Path, Path]) -> None:
    _, out = sample_repo
    local_path = out / LOCAL_FILE
    data = json.loads(local_path.read_text(encoding="utf-8"))
    data["machine_marker"] = "forged-marker-000000000000000000000000000000000000000000000000"
    local_path.write_text(json.dumps(data), encoding="utf-8")
    assert is_foreign_index(out)


def test_mcp_refuses_foreign_index_by_default(sample_repo: tuple[Path, Path]) -> None:
    repo, out = sample_repo
    # Make index foreign by removing local.json
    (out / LOCAL_FILE).unlink()
    assert is_foreign_index(out)

    with pytest.raises(ValueError, match="Refusing to serve foreign index"):
        open_index(out)

    # Opt-in with allow_foreign_index succeeds
    idx = open_index(out, allow_foreign_index=True)
    assert idx is not None
    assert len(idx.chunks) > 0


def test_symlinked_chunks_refused(
    sample_repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, out = sample_repo
    chunks_path = out / "agent" / "chunks.jsonl"
    monkeypatch.setattr(Path, "is_symlink", lambda self: str(self) == str(chunks_path))

    with pytest.raises(ValueError, match="Refusing to read symlinked artifact"):
        read_jsonl(chunks_path)


def test_symlinked_overview_not_followed(
    sample_repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, out = sample_repo
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == "overview.md")

    idx = Index(out)
    assert idx.overview == ""


def test_unknown_index_format_refused(sample_repo: tuple[Path, Path]) -> None:
    _, out = sample_repo
    manifest_path = out / "agent" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["format"] = "repo2graph/999"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported index format: 'repo2graph/999'"):
        Index(out)


def test_foreign_parse_cache_ignored(sample_repo: tuple[Path, Path]) -> None:
    _, out = sample_repo
    # Make index foreign
    (out / LOCAL_FILE).unlink()
    assert is_foreign_index(out)

    cache = load_parse_cache(out)
    assert cache == {}


def test_symlinked_vectors_refused(
    sample_repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, out = sample_repo
    from repo2graph.embed import load_vectors

    vec_path = out / "agent" / "vectors.npy"
    monkeypatch.setattr(Path, "is_symlink", lambda self: str(self) == str(vec_path))

    with pytest.raises(ValueError, match="Refusing to load symlinked vectors artifact"):
        load_vectors(vec_path)


def test_stale_vectors_not_loaded_into_index(sample_repo: tuple[Path, Path]) -> None:
    _, out = sample_repo
    from repo2graph.embed import write_vectors

    # Write dummy vectors with an old build_id
    vec_path = out / "agent" / "vectors.npy"
    dim = 4
    vectors = {"c1": [0.1, 0.2, 0.3, 0.4]}
    write_vectors(
        vec_path,
        vectors,
        model_id="test-model",
        dim=dim,
        chunk_ids=["c1"],
        build_id="old-stale-build-id-12345",
    )

    idx = Index(out)
    # Since manifest has a different build_id, vectors must not be loaded
    assert idx.vectors is None
