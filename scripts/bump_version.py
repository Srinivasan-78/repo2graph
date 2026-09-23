#!/usr/bin/env python3
"""Bump version across pyproject.toml, server.json, repo2graph/__init__.py, and CHANGELOG.md.

Can automatically scan git commits and CHANGELOG.md since the latest release tag
to determine the semantic bump (major, minor, or patch), or accept an explicit argument.

Usage:
    python scripts/bump_version.py             # auto-detect bump from changes
    python scripts/bump_version.py auto        # auto-detect bump from changes
    python scripts/bump_version.py patch       # explicit patch bump
    python scripts/bump_version.py minor       # explicit minor bump
    python scripts/bump_version.py major       # explicit major bump
    python scripts/bump_version.py 1.5.2       # explicit target version
"""

import datetime
import pathlib
import re
import shutil
import subprocess
import sys

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from version_surfaces import ROOT, bumped_paths, rewrite  # noqa: E402

__all__ = ["bumped_paths"]  # re-exported for `--files`; see docs/publishing.md


def parse_semver(v: str) -> tuple[int, int, int]:
    clean = v.lstrip("v").strip()
    parts = clean.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"Invalid semver version: {v!r}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def get_latest_tag() -> str | None:
    """Find the most recent semver tag in the repository."""
    try:
        proc = subprocess.run(
            [
                "git",
                "-c",
                "core.quotepath=false",
                "describe",
                "--tags",
                "--abbrev=0",
                "--match",
                "v[0-9]*.[0-9]*.[0-9]*",
            ],
            cwd=str(ROOT),
            capture_output=True,
            timeout=10,
        )
        if proc.returncode == 0:
            tag = proc.stdout.decode("utf8", "surrogateescape").strip()
            if tag:
                return tag
    except Exception:
        pass

    try:
        proc = subprocess.run(
            [
                "git",
                "-c",
                "core.quotepath=false",
                "tag",
                "-l",
                "v[0-9]*.[0-9]*.[0-9]*",
                "--sort=-v:refname",
            ],
            cwd=str(ROOT),
            capture_output=True,
            timeout=10,
        )
        if proc.returncode == 0:
            lines = proc.stdout.decode("utf8", "surrogateescape").split("\n")
            tags = [t.strip() for t in lines if t.strip()]
            if tags:
                return tags[0]
    except Exception:
        pass

    return None


def get_commits_since(tag: str | None) -> list[str]:
    """Retrieve full commit messages since tag (or all if tag is None)."""
    cmd = ["git", "-c", "core.quotepath=false", "log", "--format=%s%n%b%x1e"]
    if tag:
        cmd.append(f"{tag}..HEAD")
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, timeout=15)
        if proc.returncode == 0:
            raw = proc.stdout.decode("utf8", "surrogateescape")
            return [e.strip() for e in raw.split("\x1e") if e.strip()]
    except Exception:
        pass
    return []


def get_unreleased_changelog() -> str:
    """Extract content under ## [Unreleased] in CHANGELOG.md."""
    path = ROOT / "CHANGELOG.md"
    if not path.is_file():
        return ""
    content = path.read_text(encoding="utf8")
    unreleased_re = re.compile(r"^## \[Unreleased\](.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
    m = unreleased_re.search(content)
    return m.group(1).strip() if m else ""


def detect_bump_type() -> str:
    """Scan git commits and CHANGELOG.md since the latest tag to decide semver bump."""
    latest_tag = get_latest_tag()
    commits = get_commits_since(latest_tag)
    changelog_unreleased = get_unreleased_changelog()

    print(f"Scanning changes since latest tag ({latest_tag or 'initial commit'}):")
    print(f"- Found {len(commits)} commit(s)")
    print(f"- Found {len(changelog_unreleased)} characters of unreleased changelog notes")

    if not commits and not changelog_unreleased:
        raise RuntimeError(
            f"No changes or unreleased changelog notes detected since {latest_tag or 'repository start'}."
        )

    combined_text = "\n".join(commits) + "\n" + changelog_unreleased

    # 1. Check for MAJOR breaking changes:
    # - Conventional commit header with '!' (e.g. feat!: or fix(scope)!:)
    # - "BREAKING CHANGE:" or "BREAKING-CHANGE:" in commit header or body
    # - "### Breaking" or "### Removed" in changelog [Unreleased]
    breaking_header_re = re.compile(r"^[a-zA-Z]+(\([^)]+\))?!:", re.MULTILINE)
    breaking_keyword_re = re.compile(r"BREAKING[ -]CHANGE:", re.IGNORECASE)
    breaking_changelog_re = re.compile(r"^### (Breaking|Removed)", re.MULTILINE | re.IGNORECASE)

    if (
        breaking_header_re.search(combined_text)
        or breaking_keyword_re.search(combined_text)
        or breaking_changelog_re.search(changelog_unreleased)
    ):
        print("Decision: MAJOR bump (breaking changes detected)")
        return "major"

    # 2. Check for MINOR new features:
    # - User-facing feature commits: feat: or feat(scope): (ignoring internal ci/test/docs/chore scopes)
    # - "### Added" in changelog [Unreleased]
    feat_re = re.compile(
        r"^feat(?:\((?!(ci|test|docs|chore)\b)[^)]*\))?:", re.MULTILINE | re.IGNORECASE
    )
    added_changelog_re = re.compile(r"^### Added", re.MULTILINE | re.IGNORECASE)

    if feat_re.search(combined_text) or added_changelog_re.search(changelog_unreleased):
        print("Decision: MINOR bump (new feature detected)")
        return "minor"

    # 3. Default to PATCH:
    # Bug fixes, security hardening, internal tooling, performance, chore, docs
    print("Decision: PATCH bump (fixes, security, documentation, or maintenance)")
    return "patch"


def compute_next_version(current: str, bump_type: str) -> str:
    major, minor, patch = parse_semver(current)
    b = bump_type.lower().strip()
    if b == "patch":
        return f"{major}.{minor}.{patch + 1}"
    elif b == "minor":
        return f"{major}.{minor + 1}.0"
    elif b == "major":
        return f"{major + 1}.0.0"
    else:
        # User specified an explicit version string
        parse_semver(b)  # validate
        return b.lstrip("v")


def bump_changelog(new_ver: str) -> None:
    path = ROOT / "CHANGELOG.md"
    if not path.is_file():
        return
    content = path.read_text(encoding="utf8")
    # Check if section for new_ver already exists
    ver_header_re = re.compile(rf"^## \[?{re.escape(new_ver)}\]?", re.MULTILINE)
    if ver_header_re.search(content):
        return  # Section already exists

    today = datetime.date.today().isoformat()
    release_header = f"## [{new_ver}] — {today}"

    unreleased_re = re.compile(r"^## \[Unreleased\](.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
    match = unreleased_re.search(content)

    if match:
        body = match.group(1).strip()
        if body:
            # Promote existing unreleased section
            replacement = f"## [Unreleased]\n\n{release_header}\n\n{body}\n\n"
        else:
            # Empty unreleased section, provide a minimal section so publish.yml extractor passes
            replacement = (
                f"## [Unreleased]\n\n"
                f"{release_header}\n\n"
                f"### Changed\n\n"
                f"- Release version {new_ver}.\n\n"
            )
        new_content = content[: match.start()] + replacement + content[match.end() :]
    else:
        # No unreleased header; prepend release section
        new_content = (
            f"# Changelog\n\n## [Unreleased]\n\n{release_header}\n\n"
            f"### Changed\n\n- Release version {new_ver}.\n\n" + content
        )

    path.write_text(new_content, encoding="utf8", newline="\n")


def sync_lockfile() -> None:
    """Regenerate `uv.lock` for the bumped version. Fatal on failure.

    This used to warn and carry on. It cannot any more: `publish.yml`'s pypi job
    installs with `uv export --locked`, which fails rather than re-resolving when
    the lock disagrees with `pyproject.toml` -- and it runs *after* the tag is
    cut and the bump commit is merged. A warning here buys a hard failure at the
    one point in the pipeline where backing out is expensive. `rewrite()` has
    already put the new version in the lock, so this normalises a file that is
    otherwise generated, rather than being the only thing that writes it.
    """
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError(
            "`uv` is not on PATH, so uv.lock cannot be regenerated. publish.yml's "
            "pypi job installs with `uv export --locked` and will fail after the tag "
            "is cut. Install uv (`pip install uv`) and re-run."
        )
    try:
        subprocess.run([uv, "lock"], cwd=str(ROOT), check=True, capture_output=True, timeout=300)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"`uv lock` failed, so uv.lock is out of sync with the bumped pyproject.toml: "
            f"{exc.stderr.decode('utf8', 'replace').strip()}"
        ) from None
    except subprocess.TimeoutExpired:
        raise RuntimeError("`uv lock` timed out after 300s") from None
    print("Synchronized uv.lock")


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1].lower() in ("auto", "--auto"):
        target_arg = detect_bump_type()
    else:
        target_arg = sys.argv[1]

    content = (ROOT / "pyproject.toml").read_text(encoding="utf8")
    if tomllib is not None:
        pyproject = tomllib.loads(content)
        current_ver = pyproject["project"]["version"]
    else:
        m = re.search(r'(?m)^version\s*=\s*["\']([^"\']+)["\']', content)
        if not m:
            print("error: could not determine version from pyproject.toml", file=sys.stderr)
            return 1
        current_ver = m.group(1)

    new_ver = compute_next_version(current_ver, target_arg)
    print(f"Bumping version: {current_ver} -> {new_ver}")

    # Every version surface at once, from the table check_version.py reads, so
    # the two cannot disagree about what a bump covers. This replaces three
    # per-file bumpers that knew about pyproject, server.json and __init__.py
    # and nothing else -- which is why the documented `@vN` tag and the
    # `repo2graph==X.Y.Z` pin examples stayed a major version behind.
    changed = rewrite(new_ver)
    print(f"Rewrote {len(changed)} file(s): {', '.join(changed)}")
    bump_changelog(new_ver)
    sync_lockfile()

    # Validate with check_version
    check_script = ROOT / "scripts" / "check_version.py"
    if check_script.is_file():
        res = subprocess.run(
            [sys.executable, str(check_script)], cwd=str(ROOT), capture_output=True
        )
        if res.returncode != 0:
            print("check_version.py failed after bump:", file=sys.stderr)
            print(res.stderr.decode("utf8", "replace"), file=sys.stderr)
            return 1
        print(res.stdout.decode("utf8", "replace").strip())

    print(f"Successfully bumped all files to {new_ver}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
