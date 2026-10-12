"""CO_CHANGE edges (and --git-authors) from a bounded read of `git log` (#315)."""

from __future__ import annotations

import itertools
import re
import subprocess
import threading
from collections import Counter, defaultdict
from pathlib import Path
from typing import IO, TYPE_CHECKING

from .edgemeta import METHOD_GIT_LOG
from .integrity import GIT_HARDENING_ARGS, _clean_git_env

if TYPE_CHECKING:
    from .graph import Graph

# A request for millions of commits makes git walk the whole history and emit
# every path in it. Co-change signal saturates long before this, so cap the
# window and record when we did.
MAX_COCHANGE_COMMITS = 5000
# Independent of MAX_COCHANGE_COMMITS: that bounds how many commits
# are requested, but a single commit -- e.g. a vendor import touching
# hundreds of thousands of files -- can still emit an unbounded blob of paths
# within that commit count. Enforced during the read: `add_cochange` streams the
# pipe and stops at this many bytes, then terminates git.
MAX_COCHANGE_BYTES = 10 * 1024 * 1024  # 10 MB
# Wall clock for the whole `git log` read.
COCHANGE_TIMEOUT = 120
# One read() per block. Big enough that a 10 MB cap is ~160 reads, small enough
# that the buffer never jumps far past the cap.
_COCHANGE_READ_BLOCK = 64 * 1024
# How long to wait for a killed child (and the thread reading it) to go away.
_COCHANGE_REAP_TIMEOUT = 10


def _read_capped(stream: IO[bytes], limit: int) -> tuple[bytes, bool]:
    """Read at most `limit` bytes from `stream`, and report whether there were more.

    Reads one byte past the cap deliberately: that byte is the only way to tell
    "the output was exactly `limit` bytes" from "the output was larger and we
    stopped early" without reading the rest of it.
    """
    buf = bytearray()
    while len(buf) <= limit:
        block = stream.read(min(_COCHANGE_READ_BLOCK, limit + 1 - len(buf)))
        if not block:
            return bytes(buf), False
        buf.extend(block)
    return bytes(buf[:limit]), True


def _reap_child(proc: subprocess.Popen[bytes], reader: threading.Thread | None = None) -> None:
    """Kill a child, let go of its pipe and collect its exit status.

    Order matters. The child is killed *first*: a reader parked in read() only
    comes back when the write end disappears, and closing the read end out from
    under it is unsafe on both platforms. Every step is best-effort -- a build
    must not fail because a doomed `git log` was slow to die.
    """
    try:
        if proc.poll() is None:
            proc.kill()
    except OSError:
        pass
    if reader is not None:
        reader.join(_COCHANGE_REAP_TIMEOUT)
    if proc.stdout is not None:
        try:
            proc.stdout.close()
        except OSError:
            pass
    try:
        proc.wait(timeout=_COCHANGE_REAP_TIMEOUT)
    except subprocess.TimeoutExpired:
        pass


# One `--pretty=format:%H` header line: a SHA-1 or SHA-256 object name.
_COMMIT_HASH = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


# Authors kept per file by --git-authors: the most frequent, then by name.
MAX_FILE_AUTHORS = 5


def add_cochange(
    g: Graph,
    root: Path,
    commits: int,
    file_index: set[str],
    min_pairs: int = 3,
    authors: bool = False,
) -> None:
    """CO_CHANGE edges from files edited together in the last N commits.

    Formula and semantics:
    - History depth: Scans up to `commits` commits (capped at MAX_COCHANGE_COMMITS = 5000).
    - Merge handling: `--no-merges` skips merge commits to avoid false co-change correlations.
    - Noise filter: Commits touching > 25 files are skipped as bulk refactors/noise.
    - Path filtering: Only paths matching `file_index` (current indexed tree) are paired.
    - Pair counting: Each commit touching 2..25 files increments pair count by +1 for all pairs.
    - Threshold: Emits an edge if pair count >= `min_pairs` (default: 3, configurable).
    """
    # "requested" is what --git-history asked for; "sampled" is overwritten
    # below with the number of commits git actually returned -- a shallow clone
    # with one commit must not report 50.
    g.stats["cochange_requested_commits"] = commits
    g.stats["cochange_sampled_commits"] = 0
    g.stats["cochange_min_pairs"] = min_pairs
    if commits > MAX_COCHANGE_COMMITS:
        g.stats["cochange_history_capped"] = commits
        commits = MAX_COCHANGE_COMMITS
    try:
        # Popen, not run(capture_output=True): run() reads the child's stdout to
        # EOF before it returns, so a byte cap applied to its result bounds only
        # the decode and the pair counting -- the blob is already resident by
        # then. Streaming the pipe is what makes MAX_COCHANGE_BYTES a
        # memory bound rather than a post-hoc trim.
        #
        # -c core.quotepath=false: without it git backslash-escapes any
        # non-ASCII path ("caf\303\251.py"), which never matches file_index and
        # the CO_CHANGE edge silently vanishes. No text=True: decode the bytes
        # as UTF-8 ourselves, so a non-ASCII path cannot raise UnicodeDecodeError.
        # stdin=DEVNULL: prevents git from inheriting server stdin.
        proc = subprocess.Popen(
            [
                "git",
                *GIT_HARDENING_ARGS,
                "-C",
                str(root),
                "log",
                f"-n{commits}",
                "--name-only",
                # %aN: the author's display name after .mailmap. Never %ae:
                # an email address is personal data, and these artifacts get
                # committed and uploaded (#394).
                "--pretty=format:%H%x00%aN" if authors else "--pretty=format:%H",
                "--no-merges",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            env=_clean_git_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return

    # The read runs in a thread so the timeout can be enforced on a blocking
    # read(): select() does not work on pipes on Windows, and this is how
    # Popen.communicate(timeout=...) does it there too.
    read: list[tuple[bytes, bool]] = []

    def _drain() -> None:
        try:
            assert proc.stdout is not None  # stdout=PIPE
            read.append(_read_capped(proc.stdout, MAX_COCHANGE_BYTES))
        except (OSError, ValueError):
            pass  # pipe torn down mid-read: same outcome as no output at all

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()
    reader.join(COCHANGE_TIMEOUT)
    timed_out = reader.is_alive()
    try:
        _reap_child(proc, reader)
    except (OSError, subprocess.SubprocessError):
        pass
    # A timeout is silent, exactly as the TimeoutExpired from the old
    # run(timeout=120) was: no co-change edges, no error, the build goes on.
    if timed_out or not read:
        return
    stdout, capped = read[0]
    # Capping kills git mid-write, so its exit status then says "killed" and
    # means nothing. Only a read that reached EOF can report a real git failure.
    if not capped and proc.returncode not in (0, None):
        return
    # MAX_COCHANGE_COMMITS bounds how many commits are requested, not
    # how many bytes a single commit's file list can emit.
    if capped:
        g.stats["cochange_output_capped"] = len(stdout)
        # Drop the trailing partial commit: git log delimits commits with a blank
        # line ("\n\n" or "\r\n\r\n").
        m = None
        for m in re.finditer(rb"(\r?\n){2}", stdout):
            pass
        stdout = stdout[: m.end()] if m else b""
    pairs: Counter[tuple[str, str]] = Counter()
    current: list[str] = []
    sampled = 0
    author = ""
    file_authors: dict[str, Counter[str]] = defaultdict(Counter)
    # split("\n"), not splitlines(): with core.quotepath=false git emits paths
    # containing U+2028/U+2029/U+0085 raw, and splitlines() would cut such a path
    # in two so it never matches file_index.
    for line in stdout.decode("utf8", "surrogateescape").split("\n") + [""]:
        line = line.rstrip("\r")
        if not line:
            if 1 < len(current) <= 25:
                for a, b in itertools.combinations(sorted(set(current)), 2):
                    pairs[(a, b)] += 1
            elif len(current) > 25:
                g.stats["cochange_commits_skipped"] += 1
            current = []
        elif line in file_index:
            current.append(line)
            if authors and author:
                file_authors[line][author] += 1
        elif _COMMIT_HASH.fullmatch(line.partition("\x00")[0]):
            sampled += 1
            author = line.partition("\x00")[2].strip()
    g.stats["cochange_sampled_commits"] = sampled
    commits = sampled
    for path, counts in file_authors.items():
        node = g.nodes.get(f"file:{path}")
        if node is not None:
            ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            node["authors"] = [{"name": n, "commits": c} for n, c in ranked[:MAX_FILE_AUTHORS]]
    if authors:
        g.stats["files_with_authors"] = sum(1 for p in file_authors if f"file:{p}" in g.nodes)
    for (a, b), n in pairs.items():
        if n >= min_pairs:
            g.add_edge(
                f"file:{a}",
                f"file:{b}",
                "CO_CHANGE",
                count=n,
                cochange_count=n,
                sampled_commits=commits,
                min_pairs=min_pairs,
                method=METHOD_GIT_LOG,
                # Correlational, never causal: these two files changed in the
                # same commits. Confidence is the share of the sampled commits
                # that touched both, so a pair co-edited 3 times in 500
                # commits does not read the same as one co-edited 200 times.
                confidence=round(n / commits, 3) if commits else 0.0,
                evidence=None,
            )
