#!/usr/bin/env python3
"""Fail when any recorded version disagrees with `pyproject.toml`.

`pyproject.toml` is the source of truth. Everything else -- the `__init__.py`
fallback, `server.json`'s two copies, `uv.lock`'s record of the project, and
every documented `@vN` tag and `repo2graph==X.Y.Z` pin -- follows it. The full
list lives in `version_surfaces.py` and is shared with `bump_version.py`, so a
surface cannot be one script's business and not the other's.

`publish.yml` already checks this, but it checks it at release time, when the
cost of being wrong is a burnt version number that PyPI will not let you reuse.
This runs on every commit (pre-commit) and on every pull request (ci.yml's
`version-surfaces` step), where the cost is re-typing one string.

A surface whose pattern matches nothing is an error, not a pass: a doc reworded
so the pattern stops matching is a check that has silently become a no-op, which
is how the documented `@v1` survived the 2.0.0 release.
"""

import pathlib
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from version_surfaces import ROOT, findings  # noqa: E402


def main() -> int:
    """Compare every recorded version; return 1 on any disagreement."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf8"))
    want = pyproject["project"]["version"]

    results = findings(want)
    wrong = [(path, pattern, got, exp) for path, pattern, got, exp in results if got != exp]

    if wrong:
        print(f"version mismatch: pyproject.toml says {want!r}", file=sys.stderr)
        for path, pattern, got, exp in wrong:
            print(f"  {path}: found {got!r}, expected {exp!r}   [{pattern}]", file=sys.stderr)
        print(
            "\nRun `python scripts/bump_version.py " + want + "` to rewrite every surface,",
            file=sys.stderr,
        )
        print("or fix the ones listed above by hand.", file=sys.stderr)
        return 1

    paths = len({path for path, _, _, _ in results})
    print(f"version {want} is consistent across {len(results)} sites in {paths} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
