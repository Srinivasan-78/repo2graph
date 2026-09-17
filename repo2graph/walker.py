"""Compatibility shim — walker is merged into parse.py."""
from .parse import (
    DEFAULT_SKIP_DIRS,
    MAX_BYTES,
    _git_files,
    _glob_re,
    _walk_files,
    discover,
    is_binary,
    matches_any,
)

__all__ = [
    "DEFAULT_SKIP_DIRS",
    "MAX_BYTES",
    "_git_files",
    "_glob_re",
    "_walk_files",
    "discover",
    "is_binary",
    "matches_any",
]
