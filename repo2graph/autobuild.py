"""Building an index on the project's own initiative, rather than on request.

This is the build `repo2graph build` performs, minus the CLI: hardened output
path, the repo's own config file, the same `BuildLock`, and a `.gitignore` that
keeps an index nobody asked for out of a commit.

It lives here rather than under `repo2graph/mcp/` because it is not an MCP
concept. `tasks.py` -- the async build-task runner, core by any reading --
needed exactly this and had to reach *up* into the interface layer to get it
(`from .mcp.tools import _build_index`), which made the project's central
operation a function the MCP package owned. The MCP layer is one caller of
this module now, and `tasks.py` is another.
"""

from __future__ import annotations

from pathlib import Path

#: Formats the served index needs: retrieval reads `chunks.jsonl`, and
#: `repo_map` reads the overview. Anything else is cost with no reader.
AUTO_BUILD_FORMATS = {"jsonl", "overview"}

#: Written into an index built on the tool's own initiative.
AUTO_BUILD_GITIGNORE = (
    "# written by repo2graph-mcp: an auto-built index holds full source text\n*\n"
)

#: An organisation's own secret rules (`repo2graph-mcp --secret-keyword/--secret-dir`).
#: Process-wide, set once by `mcp.server.main` before the first tool call: one
#: server serves one repository under one policy. A dict rather than a pair of
#: module globals so that every importer shares the one object and sees a later
#: assignment through it.
SECRET_RULES: dict[str, list[str]] = {"keywords": [], "dirs": []}


def build_index(repo: Path, out: Path) -> None:
    """Index `repo` into `out`, in-process, silent on stdout."""
    from .chunks import iter_chunks
    from .config import build_options
    from .config import load as load_repo_config
    from .export import _LOCAL_GITIGNORE, atomic_write, dump_all
    from .graph import build
    from .integrity import validate_outdir
    from .lock import BuildLock
    from .parse import BuildConfig

    # `build -o` is hardened by `validate_outdir`; this auto-build reached the
    # same `dump_all` without it, and that swap renames the target aside and
    # deletes it. The caller picks `out` itself (`<repo>/.r2g`) or takes it from
    # `--out`, so a misconfigured client could have it replace a real directory.
    # Raised as ValueError rather than SystemExit: this runs inside a live stdio
    # server, which must answer the tool call rather than exit the process.
    validate_outdir(out, repo_root=repo)
    # The repo's own config file (#391), as `repo2graph build` there would read
    # it. A ConfigError is a ValueError, answered like the one above. The
    # server's --secret-keyword/--secret-dir are its CLI flags: when given they
    # replace the file's lists, as on the command line.
    config_kw, build_kw = build_options(load_repo_config(repo).values)
    if SECRET_RULES["keywords"]:
        config_kw["extra_secret_keywords"] = list(SECRET_RULES["keywords"])
    if SECRET_RULES["dirs"]:
        config_kw["extra_secret_dirs"] = list(SECRET_RULES["dirs"])
    # The same lock `repo2graph build` takes: without it two servers (or a
    # server and a CLI build) pointed at one index interleave their swaps.
    with BuildLock(out):
        graph = build(repo, config=BuildConfig(output_dir=str(out), **config_kw), **build_kw)
        dump_all(graph, iter_chunks(graph), out, AUTO_BUILD_FORMATS)
    # Nobody asked for this index, so nobody decided to commit it: chunks.jsonl
    # carries full source text, and `git add -A` would publish it. An explicit
    # `repo2graph build` keeps the narrower ignore file, because committing an
    # index on purpose is a supported workflow (the Action's commit-branch).
    ignore = out / ".gitignore"
    if not ignore.exists() or ignore.read_text(encoding="utf8") == _LOCAL_GITIGNORE:
        with atomic_write(ignore, "w", encoding="utf8", newline="\n") as fh:
            fh.write(AUTO_BUILD_GITIGNORE)
