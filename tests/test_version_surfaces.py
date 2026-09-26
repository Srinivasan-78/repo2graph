"""The version-bump surface table: that it is complete, and that a bump moves all of it.

The bug these encode: `bump_version.py` knew about `pyproject.toml`,
`server.json` and `repo2graph/__init__.py`, and `check_version.py` knew about the
same three. Nothing knew about the *documented* surfaces -- the `@vN` tag every
Action consumer is told to put in `uses:`, and the `repo2graph==X.Y.Z` pin
examples -- so after 2.0.0 shipped, the README still said `@v1` and described it
as following "every 1.x release", pointing users at a dead release line.

Per AGENTS.md, values here are hand-derived from the repo's own files, never
recomputed by the code under test: the expected version comes from
`pyproject.toml` read directly, and the things that must *not* move are literal
strings.
"""

import re
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]

import pytest

from conftest import REPO_ROOT

SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import version_surfaces as vs  # noqa: E402


def declared_version() -> str:
    content = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf8")
    if tomllib is not None:
        return tomllib.loads(content)["project"]["version"]
    m = re.search(r'(?m)^version\s*=\s*["\']([^"\']+)["\']', content)
    assert m is not None, "could not find version in pyproject.toml"
    return m.group(1)


def test_every_surface_matches_something_in_every_file_it_claims():
    """A pattern that matches nothing is a check that has become a no-op, which
    is exactly how the documented `@v1` survived the 2.0.0 release. `findings`
    reports that as a `<no match>` sentinel rather than silently passing."""
    results = vs.findings(declared_version())
    assert results, "the surface table produced no findings at all"
    sentinels = [(p, pat) for p, pat, got, _ in results if got.startswith("<")]
    assert not sentinels, sentinels


def test_the_repo_is_currently_consistent():
    """check_version.py is a required CI step; this is the same assertion at
    unit level so a failure names the surface rather than a subprocess exit."""
    want = declared_version()
    wrong = [(p, pat, got, exp) for p, pat, got, exp in vs.findings(want) if got != exp]
    assert not wrong, wrong


def test_check_version_script_agrees():
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "check_version.py")],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_release_commit_paths_cover_every_bumped_path_plus_the_changelog():
    """publish.yml passes this list to `--files`. A path `rewrite()` touches but
    the list omits produces a release commit that bumps some surfaces and not
    others, on a tag PyPI will not let you reuse."""
    release = set(vs.release_commit_paths())
    assert set(vs.bumped_paths()) <= release
    # CHANGELOG.md is rewritten by bump_changelog but is deliberately not a
    # checked surface: before a bump its only header is [Unreleased].
    assert "CHANGELOG.md" in release
    assert "CHANGELOG.md" not in set(vs.bumped_paths())


def test_files_mode_prints_exactly_those_paths():
    """The `--files` CLI is what publish.yml shells out to; if its output stops
    being a plain space-separated list, `read -ra` silently builds the wrong
    array."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "version_surfaces.py"), "--files"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().split(" ") == list(vs.release_commit_paths())


def test_every_surface_path_exists():
    """A renamed doc silently drops its surface from both the bump and the
    check, so the table naming a file that is gone must fail loudly."""
    missing = [p for p in vs.bumped_paths() if not (REPO_ROOT / p).is_file()]
    assert not missing, missing


# --------------------------------------------------------------------------
# What a bump must NOT touch
# --------------------------------------------------------------------------

# Literal strings, hand-copied from the files. Each is a version that records
# history or illustrates a *form*, and a pattern loose enough to reach one of
# them would rewrite the past or break an example.
MUST_SURVIVE = (
    # The CITATION.cff surface anchors on `^version:`; `cff-version` is the
    # citation-file-format spec version, not ours. A pattern that reached it
    # would claim the repo emits CFF 9.9.9, which no parser accepts -- and
    # test_bump_moves_every_surface_to_the_new_version could not see it, since
    # both lines would agree on the new value.
    ("CITATION.cff", "cff-version: 1.2.0"),
    ("docs/github-action.md", "repo2graph>=1.4,<2"),
    ("docs/github-action.md", "actions/checkout@v4"),
    ("README.md", "actions/checkout@v4"),
    ("docs/ACTION_SECURITY.md", "actions/checkout@v4"),
)

# Files that record which version produced an artifact. Not surfaces, and a
# bump that edited them would falsify a measurement.
HISTORICAL = ("benchmarks/results.json", "examples/django/manifest.json")


@pytest.mark.parametrize("path,literal", MUST_SURVIVE)
def test_bump_leaves_unrelated_version_strings_alone(tmp_path, path, literal):
    before = (REPO_ROOT / path).read_text(encoding="utf8")
    assert literal in before, f"fixture is stale: {literal!r} not in {path}"
    rewritten = _rewrite_in_sandbox(tmp_path, "9.9.9")
    assert literal in rewritten[path], (path, literal)


def test_bump_does_not_touch_files_that_record_history(tmp_path):
    present = [p for p in HISTORICAL if (REPO_ROOT / p).is_file()]
    assert present, "no historical fixture files found"
    for path in present:
        assert path not in vs.bumped_paths(), path


def test_bump_moves_every_surface_to_the_new_version(tmp_path):
    """The whole point: after a bump, nothing still reads the old version."""
    rewritten = _rewrite_in_sandbox(tmp_path, "9.9.9")
    for surface in vs.SURFACES:
        want = surface.expected("9.9.9")
        for path in surface.paths:
            found = [m.group("v") for m in surface.regex.finditer(rewritten[path])]
            assert found, (path, surface.pattern)
            assert set(found) == {want}, (path, surface.pattern, found, want)


def test_bump_rewrites_the_documented_major_tag(tmp_path):
    """Named separately because it is the surface that was missed. `@v9` and
    "every 9.x release", hand-derived from a 9.9.9 target."""
    rewritten = _rewrite_in_sandbox(tmp_path, "9.9.9")
    assert "Srinivasan-78/repo2graph@v9" in rewritten["README.md"]
    assert "`@v9` follows every 9.x release" in rewritten["README.md"]
    assert "`@v9.9.9`" in rewritten["README.md"]
    assert 'repo2graph[mcp]==9.9.9"' in rewritten["docs/ENTERPRISE_DEPLOYMENT.md"]


def _rewrite_in_sandbox(tmp_path: Path, version: str) -> dict[str, str]:
    """Run `rewrite(version)` against copies, never the real checkout.

    `version_surfaces.ROOT` is module state, so it is swapped for the duration
    and restored -- a test that bumped the working tree and failed halfway would
    leave the repo claiming a version it never released.
    """
    for path in vs.bumped_paths():
        dst = tmp_path / path
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text((REPO_ROOT / path).read_text(encoding="utf8"), encoding="utf8")
    real_root = vs.ROOT
    vs.ROOT = tmp_path
    try:
        vs.rewrite(version)
    finally:
        vs.ROOT = real_root
    return {p: (tmp_path / p).read_text(encoding="utf8") for p in vs.bumped_paths()}


def test_the_sandbox_helper_leaves_the_real_tree_alone(tmp_path):
    """The guard on the guard: if `_rewrite_in_sandbox` ever edited the checkout,
    every other test here would still pass while corrupting the repo."""
    before = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf8")
    _rewrite_in_sandbox(tmp_path, "9.9.9")
    assert (REPO_ROOT / "pyproject.toml").read_text(encoding="utf8") == before
    assert vs.ROOT == REPO_ROOT


def test_major_surfaces_carry_only_the_major():
    """`kind="major"` exists because publish.yml's floating tag is `vN`. If a
    major surface started expecting a full version, every bump would write
    `@v2.1.0` into a `uses:` line and point users at a tag that does not move."""
    for surface in vs.SURFACES:
        if surface.kind == "major":
            assert surface.expected("4.5.6") == "4"
            assert not re.search(r"\\d\+\\\.", surface.pattern), surface.pattern
        else:
            assert surface.expected("4.5.6") == "4.5.6"
