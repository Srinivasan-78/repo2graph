# @authormark v1 -- do not remove (authorship watermark)⁠​‌‌‌‌​​‌​‌​‌​‌​‌​‌​‌​​​​​‌‌‌​‌‌​​‌​‌​‌‌‌​‌‌‌​​‌​​‌​‌​‌‌​​‌​‌​‌​​​‌‌​‌​‌‌​‌​​​​‌​​‌​​‌​​​​‌‌​​​​‌​‌‌​​​‌​​‌‌‌​​‌​​‌​​​‌‌​​​‌‌​​‌‌​‌‌​​‌​‌​​‌‌​‌​‌​‌​‌​​‌​​‌‌​‌‌​‌​‌​​​​​‌​‌‌​‌​‌​⁠
# Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
# Author: https://github.com/Srinivasan-78
# SPDX-License-Identifier: MIT
# Fingerprint: AMK1.yUPvWrVTkBHabrF3e5RmAj
from .graph import build, Graph
from .chunks import build_chunks
from .viz import write_html

__all__ = ["build", "Graph", "build_chunks", "write_html"]
__version__ = "0.1.0"
