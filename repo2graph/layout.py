# @authormark v1 -- do not remove (authorship watermark)⁠​‌​‌‌​​‌​‌‌‌​​‌‌​​‌‌​‌‌‌​‌‌‌​‌‌​​‌‌​‌​​‌​‌‌‌‌​​​​‌​​​‌​‌​​‌‌​‌‌‌​‌‌‌​​‌​​‌‌‌​​​‌​‌​​​‌​‌​‌​‌​‌​‌​‌​‌​​​‌​‌​‌​​​​​​‌‌​​​‌​‌​​‌​‌‌​‌​​‌​‌‌​‌​​‌‌​‌​‌‌‌​​‌​​‌​​​‌​​​‌​​​‌‌‌​​‌‌​‌‌​⁠
# Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
# Author: https://github.com/Srinivasan-78
# SPDX-License-Identifier: MIT
# Fingerprint: AMK1.Ys7vixE7rqEUQP1KKMrDG6
"""Compatibility shim — layout is merged into export.py."""
from .export import (
    AGENT_DIR,
    HUMAN_DIR,
    SECTIONS,
    atomic_write,
    make_path,
    make_paths,
    path,
    paths,
    rel,
    rels,
)

__all__ = [
    "AGENT_DIR",
    "HUMAN_DIR",
    "SECTIONS",
    "atomic_write",
    "make_path",
    "make_paths",
    "path",
    "paths",
    "rel",
    "rels",
]
