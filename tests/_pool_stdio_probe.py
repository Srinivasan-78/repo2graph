# @authormark v1 -- do not remove (authorship watermark)⁠​​‌‌​‌​​​‌​​​​‌​​‌‌​​‌‌​​‌‌​‌‌​​​‌​​‌​‌‌​​‌‌​‌‌‌​‌​‌​‌​​​​‌‌​​​‌​‌‌​‌‌​​​​‌‌​‌‌‌​‌‌​​‌​​​‌​​​‌‌‌​‌​​​​​‌​‌​​​‌‌​​‌‌​​​‌‌​‌‌​​​‌​​‌​​​‌‌‌​‌‌‌​​​‌​‌‌​‌​‌‌​‌​​​‌​‌​​‌‌​‌‌​​‌​‌​​​​⁠
# Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
# Author: https://github.com/Srinivasan-78
# SPDX-License-Identifier: MIT
# Fingerprint: AMK1.4BflK7T1l7dGAFcbGqkE6P
"""Child process for the #90 worker-stdio detector. Not a test module.

Run me with stdout on a pipe and exactly one byte (`S`) on stdin, then closed:

    subprocess.run([sys.executable, __file__], input=b"S", stdout=PIPE, stderr=PIPE)

I build a `ProcessPoolExecutor` with *exactly the keyword arguments
`graph.parse_all` hands one* -- captured by standing in for the class while
`parse_all` runs, so this cannot drift away from what the real build does --
and run one task that tries to reach fd 1 and fd 0.

Outcomes, in terms of what the parent observes:

* workers detached (fixed): SENTINEL never appears on the parent's stdout
  pipe, the worker's `os.read(0, 1)` returns b"" off devnull, and the `S` the
  parent wrote is still on the pipe for *this* process to read.
* workers inherited (regressed): SENTINEL lands in the middle of the parent's
  stdout -- which for `repo2graph-mcp` is the client's JSON-RPC stream -- and
  the worker eats the `S` this process was supposed to read, which is how a
  server ends up waiting forever for a request a worker already consumed.
"""

import concurrent.futures
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SENTINEL = b"POOL-WORKER-REACHED-THE-TRANSPORT\n"
REPORT_PREFIX = "POOL-STDIO-REPORT "


def probe(_):
    """Runs in a pool worker: try both halves of the transport."""
    report = {}
    try:
        os.write(1, SENTINEL)
        report["wrote_fd1"] = True
    except OSError as exc:
        report["wrote_fd1"] = type(exc).__name__
    try:
        report["read_fd0"] = os.read(0, 1).decode("ascii", "replace")
    except OSError as exc:
        report["read_fd0"] = type(exc).__name__
    return report


def captured_pool_kwargs(workdir):
    """The kwargs `graph.parse_all` really passes ProcessPoolExecutor.

    The stand-in raises from `__init__`, so `parse_all` falls through to its
    serial path and no pool is actually started here -- the point is the
    arguments, not the parse.
    """
    from repo2graph import graph

    files = []
    for n in range(graph.PARALLEL_MIN_FILES + 2):
        path = Path(workdir) / f"m{n}.py"
        path.write_text(f"VALUE_{n} = {n}\n", encoding="utf8")
        files.append((f"m{n}.py", path))

    seen = {}

    class Capture:
        def __init__(self, *args, **kwargs):
            seen.update(kwargs)
            raise RuntimeError("kwargs captured; parse_all falls back to serial")

    real = concurrent.futures.ProcessPoolExecutor
    concurrent.futures.ProcessPoolExecutor = Capture
    try:
        graph.parse_all(files, jobs=2)
    finally:
        concurrent.futures.ProcessPoolExecutor = real
    return seen


def main():
    with tempfile.TemporaryDirectory() as workdir:
        kwargs = dict(captured_pool_kwargs(workdir))
    kwargs.pop("max_workers", None)
    with concurrent.futures.ProcessPoolExecutor(max_workers=1, **kwargs) as pool:
        workers = list(pool.map(probe, [0]))
    # Whatever the worker did not swallow. Reading our own fd 0, which is the
    # real pipe: no initializer ever touched this process's handles.
    try:
        leftover = os.read(0, 16).decode("ascii", "replace")
    except OSError as exc:
        leftover = type(exc).__name__
    sys.stderr.write(
        REPORT_PREFIX
        + json.dumps(
            {
                "initializer": getattr(kwargs.get("initializer"), "__name__", None),
                "workers": workers,
                "stdin_left": leftover,
            }
        )
        + "\n"
    )
    sys.stderr.flush()


if __name__ == "__main__":
    main()
