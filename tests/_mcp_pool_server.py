# @authormark v1 -- do not remove (authorship watermark)⁠​​‌‌​‌‌‌​‌​​​‌​‌​‌‌‌​‌​‌​‌​​‌​​‌​​‌‌​​‌‌​‌​‌‌‌‌‌​‌‌‌​​‌‌​‌‌​​‌​‌​‌​​‌‌​​​‌​​‌‌‌‌​‌‌​‌​‌‌​‌‌​‌​​​​‌​‌​‌‌​​‌‌​‌​​‌​‌​​‌​​‌​‌‌‌​‌‌‌​‌​​​​‌​​‌​‌​​​​​‌‌‌​‌‌‌​‌‌‌​‌​‌​‌​​​‌‌‌​‌‌​‌‌​​⁠
# Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
# Author: https://github.com/Srinivasan-78
# SPDX-License-Identifier: MIT
# Fingerprint: AMK1.7EuI3_seLOkhViIwBPwuGl
"""Launcher for the #90 end-to-end test. Not a test module.

Runs the real `repo2graph.mcp.main()` -- real stdio transport, real auto-build
-- with one instrument attached: every `ProcessPoolExecutor` this process
constructs appends a line to a marker file first. That is how the parent test
proves the tool call it got an answer to went down the *parallel* build path
rather than quietly falling back to serial.

    argv: <marker-file> <repo> <index-out>
"""

import concurrent.futures
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    marker, repo, out = sys.argv[1], sys.argv[2], sys.argv[3]
    real = concurrent.futures.ProcessPoolExecutor

    class Recording(real):  # type: ignore[valid-type, misc]
        def __init__(self, *args, **kwargs):
            with open(marker, "a", encoding="utf8") as fh:
                fh.write(
                    "max_workers=%r initializer=%s\n"
                    % (
                        kwargs.get("max_workers"),
                        getattr(kwargs.get("initializer"), "__name__", None),
                    )
                )
            super().__init__(*args, **kwargs)

    concurrent.futures.ProcessPoolExecutor = Recording

    from repo2graph.mcp import main as mcp_main

    sys.exit(mcp_main([repo, "-o", out]))


if __name__ == "__main__":
    main()
