"""Parity and integration tests for the optional native Rust core (`repo2graph._rust`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo2graph._native import get_rust_core
from repo2graph.chunks import iter_chunks
from repo2graph.discovery import discover
from repo2graph.doctor import check_rust_core
from repo2graph.export import dump_all
from repo2graph.graph import build
from repo2graph.parse import BuildConfig
from repo2graph.query import Index, tokenize
from repo2graph.security import count_hidden_unicode


def test_doctor_reports_rust_core_ok() -> None:
    res = check_rust_core()
    assert res.status == "ok"
    assert res.name == "Rust Core"


def test_tokenize_parity_with_and_without_rust(monkeypatch: pytest.MonkeyPatch) -> None:
    if get_rust_core() is None:
        pytest.skip("native _rust extension not built")
    samples = [
        "getUserById snake_case_fn a1B2 x _private __dunder__ camelCaseHTMLParser",
        "def foo_bar(baz: int) -> str: return 'hello'\n",
        "non_ascii_café_ident a b _ ab",
        "",
    ]
    for s in samples:
        rust_tokens = tokenize(s)
        monkeypatch.setenv("REPO2GRAPH_NO_RUST", "1")
        py_tokens = tokenize(s)
        monkeypatch.delenv("REPO2GRAPH_NO_RUST", raising=False)
        assert rust_tokens == py_tokens


def test_count_hidden_unicode_parity(monkeypatch: pytest.MonkeyPatch) -> None:
    if get_rust_core() is None:
        pytest.skip("native _rust extension not built")
    samples = [
        "plain ascii text 123",
        "café naïve résumé",
        "a\u200bb\u202ec\ufeffd\u061ce",
        "",
    ]
    for s in samples:
        rust_cnt = count_hidden_unicode(s)
        monkeypatch.setenv("REPO2GRAPH_NO_RUST", "1")
        py_cnt = count_hidden_unicode(s)
        monkeypatch.delenv("REPO2GRAPH_NO_RUST", raising=False)
        assert rust_cnt == py_cnt


def test_discovery_and_incremental_build_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if get_rust_core() is None:
        pytest.skip("native _rust extension not built")

    (tmp_path / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf8")
    (tmp_path / "b.py").write_text(
        "from a import alpha\ndef beta():\n    return alpha()\n", encoding="utf8"
    )
    (tmp_path / "bin.dat").write_bytes(b"\x00\x01\x02\x03")
    (tmp_path / "utf8bom.txt").write_bytes(b"\xef\xbb\xbfhello\n")
    (tmp_path / "lfs.bin").write_bytes(
        b"version https://git-lfs.github.com/spec/v1\noid sha256:0000\nsize 10\n"
    )
    (tmp_path / "big.py").write_text("x = 1\n" * 500, encoding="utf8")

    cfg = BuildConfig(max_file_bytes=200)
    rust_stats: dict[str, int] = {
        "skipped_dotfile": 0,
        "skipped_vendor": 0,
        "skipped_too_large": 0,
        "skipped_secret": 0,
        "skipped_binary": 0,
        "skipped_lfs": 0,
    }
    rust_files = [rel for rel, _ in discover(tmp_path, stats=rust_stats, config=cfg)]

    monkeypatch.setenv("REPO2GRAPH_NO_RUST", "1")
    py_stats: dict[str, int] = {
        "skipped_dotfile": 0,
        "skipped_vendor": 0,
        "skipped_too_large": 0,
        "skipped_secret": 0,
        "skipped_binary": 0,
        "skipped_lfs": 0,
    }
    py_files = [rel for rel, _ in discover(tmp_path, stats=py_stats, config=cfg)]
    monkeypatch.delenv("REPO2GRAPH_NO_RUST", raising=False)

    assert rust_files == py_files
    assert rust_stats == py_stats

    g1 = build(tmp_path, jobs=1)
    g2 = build(tmp_path, jobs=1, cache=g1.parse_cache)
    assert g2.incremental == {"cached": len(g1.parse_cache), "reparsed": 0}
    assert set(g1.nodes) == set(g2.nodes)
    assert g1.file_hashes == g2.file_hashes


def test_bm25_parity_and_sqlite_materialisation_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    if get_rust_core() is None:
        pytest.skip("native _rust extension not built")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "service.py").write_text(
        "class OrderService:\n    def calculate_total(self, x):\n        return x + 1\n",
        encoding="utf8",
    )
    out = tmp_path / "out"
    g = build(repo, jobs=1)
    dump_all(g, iter_chunks(g), out, {"jsonl", "chunks"})

    # Remove precomputed bm25 so Index._build_bm25() runs
    for p in (out / "agent" / "bm25.jsonl", out / "agent" / "bm25.meta.json"):
        if p.exists():
            p.unlink()

    idx_rust = Index(out)
    rust_scores = idx_rust.score("calculate_total")

    monkeypatch.setenv("REPO2GRAPH_NO_RUST", "1")
    idx_py = Index(out)
    py_scores = idx_py.score("calculate_total")
    monkeypatch.delenv("REPO2GRAPH_NO_RUST", raising=False)

    assert idx_rust.lengths == idx_py.lengths
    assert idx_rust.avgdl == pytest.approx(idx_py.avgdl)
    assert dict(idx_rust.df) == dict(idx_py.df)
    assert rust_scores == py_scores

    # Verify PERF-06 diagnostic warning when SqliteGraphStore materialises
    from repo2graph.store import open_graph_store

    store = open_graph_store(out, kind="sqlite")
    assert store is not None
    idx_sql = Index(out, store=store)
    _ = idx_sql.nodes
    captured = capsys.readouterr()
    assert "materialising sqlite-backed graph into memory" in captured.err


def test_chunks_and_lazy_version_parity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import repo2graph

    assert isinstance(repo2graph.__version__, str) and repo2graph.__version__

    if get_rust_core() is None:
        pytest.skip("native _rust extension not built")

    (tmp_path / "m1.py").write_text("def foo():\n    return 1\n", encoding="utf8")
    (tmp_path / "m2.py").write_text("def bar():\n    return 2\n", encoding="utf8")
    g = build(tmp_path, jobs=1)
    rust_chunks = list(iter_chunks(g))
    monkeypatch.setenv("REPO2GRAPH_NO_RUST", "1")
    py_chunks = list(iter_chunks(g))
    monkeypatch.delenv("REPO2GRAPH_NO_RUST", raising=False)
    assert rust_chunks == py_chunks
