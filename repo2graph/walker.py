# @authormark v1 -- do not remove (authorship watermark)⁠​‌​‌​‌‌‌​‌‌​‌​‌​​‌‌​‌​​​​‌‌‌​‌​​​‌​​‌​‌​​​‌‌‌​​‌​​‌​‌‌​‌​‌‌​‌​‌​​‌​​​‌‌‌​‌​‌​​‌‌​‌‌​​‌​​​​‌‌​​‌​​‌​‌​‌​‌​‌‌​​​‌‌​‌​​​‌‌‌​‌​‌​​‌​​‌‌‌​​‌​​‌​​‌‌‌​​‌‌​‌‌​​​​‌‌‌​​‌​‌‌‌​‌​​​‌‌‌​‌‌‌⁠
# Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
# Author: https://github.com/Srinivasan-78
# SPDX-License-Identifier: MIT
# Fingerprint: AMK1.WjhtJ9-jGSd2UcGRrNl9tw
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
