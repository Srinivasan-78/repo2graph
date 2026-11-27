"""Optional native Rust acceleration loader (`repo2graph._rust`).

When the compiled PyO3 extension (`_rust.pyd` / `_rust.so`) is present and
`REPO2GRAPH_NO_RUST` is not set, hot-path operations (parallel file discovery,
single-open LFS/binary sniffing, SHA-256 hashing, BM25 tokenization/indexing,
and hidden-Unicode scanning) delegate to Rust with the GIL released. When it is
not built, every caller falls back transparently to pure Python.
"""

from __future__ import annotations

import importlib
import os
from typing import Protocol, cast


class RustCore(Protocol):
    """Typed surface of the compiled `repo2graph._rust` PyO3 module."""

    def inspect_files(
        self,
        paths: list[str],
        max_file_bytes: int,
        chunk_large_files: bool,
        read_max_bytes: int,
    ) -> list[tuple[int, int, int, str, bytes | None]]: ...

    def hash_files(self, paths: list[str]) -> list[tuple[int, int, str] | None]: ...

    def read_files_bytes(self, paths: list[str]) -> list[bytes | None]: ...

    def walk_files(self, root: str, skip_dirs: list[str]) -> list[str]: ...

    def tokenize(self, text: str) -> list[str]: ...

    def build_bm25(
        self,
        texts: list[str],
        qualnames: list[str],
        names: list[str],
        default_avg_len: float,
    ) -> tuple[dict[str, int], dict[str, list[tuple[int, int]]], list[int], float]: ...

    def count_hidden_unicode(self, text: str) -> int: ...


_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _load_rust() -> RustCore | None:
    if os.environ.get("REPO2GRAPH_NO_RUST", "").strip().lower() in _TRUTHY:
        return None
    try:
        mod = importlib.import_module("repo2graph._rust")
    except (ImportError, OSError):
        return None
    return cast(RustCore, mod)


RUST_CORE: RustCore | None = _load_rust()


def get_rust_core() -> RustCore | None:
    """Return the active Rust extension module, or None if unavailable/disabled."""
    if os.environ.get("REPO2GRAPH_NO_RUST", "").strip().lower() in _TRUTHY:
        return None
    return RUST_CORE
