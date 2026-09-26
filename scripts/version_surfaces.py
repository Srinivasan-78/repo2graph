#!/usr/bin/env python3
"""Every place this project's own version is written, in one table.

Imported by both `bump_version.py` (which rewrites these) and
`check_version.py` (which verifies them). One table rather than a list in each
script, because two lists drift: the bump knew about five files and the check
knew about three, so the documented surfaces -- the `@vN` tag every Action
consumer is told to use, and the `repo2graph==X.Y.Z` pin examples -- were
bumped by neither. After the 2.0.0 release the README still told users to write
`@v1` and described it as following "every 1.x release", which is a live
instruction to pin to a dead line.

Two kinds of surface:

* `version` -- carries the full `X.Y.Z`.
* `major` -- carries only the major, because it is the *floating* tag
  (`publish.yml`'s "Advance floating major tag" step moves `vN` to each new
  release, so `vN` is a promise about the major and nothing more).

`paths` is an allowlist on purpose, never a glob. `benchmarks/results.json`,
`examples/*/manifest.json` and `docs/BUILD_STATE.md` all record the version that
*produced* some artifact -- history, not a claim about the current release --
and a pattern loose enough to reach them would rewrite the past. For the same
reason the pin pattern matches `==` only: `docs/github-action.md`'s
`repo2graph>=1.4,<2` illustrates the *form* of a range spec and must survive
every bump unchanged.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]

SEMVER = r"\d+\.\d+\.\d+"


@dataclasses.dataclass(frozen=True)
class Surface:
    """One pattern, and the files it must appear in.

    `pattern` must capture the version text as the named group `v`; both
    consumers splice on that group's span rather than re-rendering the whole
    match, so neither has to know the surrounding syntax.
    """

    kind: str  # "version" or "major"
    pattern: str
    paths: tuple[str, ...]
    why: str

    @property
    def regex(self) -> re.Pattern[str]:
        return re.compile(self.pattern, re.MULTILINE)

    def expected(self, version: str) -> str:
        return version if self.kind == "version" else version.split(".", 1)[0]


SURFACES: tuple[Surface, ...] = (
    # ---- machine-readable -------------------------------------------------
    Surface(
        kind="version",
        pattern=r'^version\s*=\s*"(?P<v>[^"]+)"',
        paths=("pyproject.toml",),
        why="the version PyPI publishes; the source of truth every other surface follows",
    ),
    Surface(
        kind="version",
        pattern=r'__version__\s*=\s*"(?P<v>[^"]+)"',
        paths=("repo2graph/__init__.py",),
        why=(
            "answers repo2graph.__version__ in a source checkout, where there is no "
            "dist-info to read it out of"
        ),
    ),
    Surface(
        kind="version",
        pattern=r'"version"\s*:\s*"(?P<v>[^"]+)"',
        paths=("server.json",),
        why="the version the MCP Registry publishes -- at the top level and once per package",
    ),
    Surface(
        kind="version",
        # The only `"version":` key in the file; `engines.node` is `">=18"`, which
        # this pattern cannot reach because the key name differs.
        pattern=r'"version"\s*:\s*"(?P<v>[^"]+)"',
        paths=("npm/package.json",),
        why=(
            "npm/README.md's 'Release story' promises the npx launcher and the PyPI "
            "release ship from one tag; it was outside this table for 2.1.0 and drifted"
        ),
    ),
    Surface(
        kind="version",
        # `^version:` only -- `cff-version: 1.2.0` names the file *format* and must
        # survive every bump, so the anchor is what keeps this off it.
        pattern=rf"^version:\s*(?P<v>{SEMVER})\s*$",
        paths=("CITATION.cff",),
        why=(
            "what GitHub's 'Cite this repository' box and every downstream .bib render; "
            "it was outside this table for the 2.1.0 release and stayed on 2.0.0 while "
            "every other surface moved"
        ),
    ),
    Surface(
        kind="version",
        # uv writes LF; a Windows checkout with autocrlf may hand us CRLF.
        pattern=r'name = "repo2graph"\r?\nversion = "(?P<v>[^"]+)"',
        paths=("uv.lock",),
        why=(
            "publish.yml's pypi job installs with `uv export --locked`, which fails rather "
            "than re-resolving if the lock disagrees with pyproject -- and it runs after the "
            "tag is cut, so a stale lock aborts a release that is already half-published"
        ),
    ),
    # ---- documented ------------------------------------------------------
    Surface(
        kind="major",
        pattern=r"repo2graph@v(?P<v>\d+)(?![.\d])",
        paths=(
            "README.md",
            "docs/github-action.md",
            "docs/ACTION_SECURITY.md",
            "docs/SECURITY-AUDIT.md",
        ),
        why="the `uses:` line every copy-pasted workflow starts from",
    ),
    Surface(
        kind="major",
        pattern=r"`@v(?P<v>\d+)`",
        paths=(
            "README.md",
            "docs/github-action.md",
            ".github/SECURITY.md",
            "docs/PRODUCTION_READINESS.md",
            "docs/SECURITY-AUDIT.md",
        ),
        why="prose about the floating tag, which is wrong about a tag that no longer moves",
    ),
    Surface(
        kind="version",
        pattern=rf"`@v(?P<v>{SEMVER})`",
        paths=("README.md", "docs/github-action.md"),
        why="the worked example of pinning an exact tag instead of the floating one",
    ),
    Surface(
        kind="version",
        pattern=rf"repo2graph(?:\[[a-z,]+\])?==(?P<v>{SEMVER})",
        paths=("docs/github-action.md", "docs/ENTERPRISE_DEPLOYMENT.md"),
        why=(
            "install pins an operator is meant to copy; ENTERPRISE_DEPLOYMENT tells them to "
            "pin rather than float, so the pinned number had better be a real release"
        ),
    ),
    Surface(
        kind="major",
        pattern=r"every (?P<v>\d+)\.x release",
        paths=("README.md", "docs/github-action.md"),
        why="states which release line the floating tag tracks",
    ),
)


def read(path: str) -> str:
    """File text, or "" when the file is absent."""
    p = ROOT / path
    return p.read_text(encoding="utf8") if p.is_file() else ""


def findings(version: str) -> list[tuple[str, str, str, str]]:
    """Every surface occurrence, as (path, pattern, found, expected)."""
    out: list[tuple[str, str, str, str]] = []
    for surface in SURFACES:
        want = surface.expected(version)
        for path in surface.paths:
            text = read(path)
            if not text:
                out.append((path, surface.pattern, "<file missing>", want))
                continue
            matches = list(surface.regex.finditer(text))
            if not matches:
                # A surface that stops matching is a check that has quietly
                # become a no-op -- the reason this is an error and not a skip.
                out.append((path, surface.pattern, "<no match>", want))
                continue
            for m in matches:
                out.append((path, surface.pattern, m.group("v"), want))
    return out


def rewrite(version: str) -> list[str]:
    """Point every surface at `version`. Returns the paths actually changed."""
    changed: list[str] = []
    for path in sorted({p for s in SURFACES for p in s.paths}):
        target = ROOT / path
        if not target.is_file():
            continue
        text = original = target.read_text(encoding="utf8")
        for surface in SURFACES:
            if path not in surface.paths:
                continue
            want = surface.expected(version)
            # Right to left, so an earlier replacement cannot move a later span.
            for m in reversed(list(surface.regex.finditer(text))):
                text = text[: m.start("v")] + want + text[m.end("v") :]
        if text != original:
            target.write_text(text, encoding="utf8", newline="\n")
            changed.append(path)
    return changed


def bumped_paths() -> tuple[str, ...]:
    """Every path `rewrite()` may touch."""
    return tuple(sorted({p for s in SURFACES for p in s.paths}))


def release_commit_paths() -> tuple[str, ...]:
    """What `publish.yml` must include in the release commit.

    `bumped_paths()` plus `CHANGELOG.md`, which `bump_version.bump_changelog`
    rewrites but which is deliberately *not* a checked surface: before a bump its
    only version header is `[Unreleased]`, so a check demanding the current
    version would be red on every ordinary commit.

    publish.yml reads this rather than restating the list, because a hand-typed
    `--files` that misses a path produces the worst outcome available here -- a
    release commit that bumps some surfaces and not others, on a tag that cannot
    be reused.
    """
    return tuple(sorted({*bumped_paths(), "CHANGELOG.md"}))


if __name__ == "__main__":
    # `python scripts/version_surfaces.py --files` -> the release commit's paths.
    import sys as _sys

    if "--files" in _sys.argv:
        print(" ".join(release_commit_paths()))
    else:
        for _s in SURFACES:
            print(f"[{_s.kind}] {_s.pattern}")
            for _p in _s.paths:
                print(f"    {_p}")
