"""ISS-237: `--ref` is validated before it can be interpolated into a git argv.

`parse_spec` already refuses option-like owner/repo components; `ref` had no
equivalent check and reached git as a bare argv element in `git fetch ... origin
<ref>` and `git checkout <ref>`, where a leading "-" is parsed as an option.
"""

import shutil
import subprocess

import pytest

from repo2graph import fetch

# A 40-hex SHA is what CI passes far more often than a branch name; it has to
# keep working, and it is the case a "names only" allowlist would break.
SHA40 = "0a1b2c3d4e5f60718293a4b5c6d7e8f901234567"

# Hand-written, not derived from GIT_REF. Each entry names a distinct reason.
REJECTED = [
    "--upload-pack=/bin/false",  # the reported attack: git runs a command of the caller's choosing
    "--depth=1",
    "--exec=id",
    "-x",
    "-",
    "--",
    ".",
    "..",
    "./main",
    "../main",
    "../../etc/passwd",
    "refs/heads/../../evil",
    "a..b",
    "/leading",
    "trailing/",
    "a//b",
    ".hidden",
    "feature/.hidden",
    "feature/-dash",
    "main branch",
    "main;id",
    "main$(id)",
    "main|id",
    "main`id`",
    "HEAD@{1}",
    "main^",
    "main~1",
    "refs/heads/*",
    "main:refs/heads/evil",
    "main\n",  # "$" would have accepted this; GIT_REF anchors with \Z
    "main\nfetch",
    "café",  # conservative: non-ASCII is legal to git, but not needed here
]

ACCEPTED = [
    "main",
    "master",
    "v1.2.3",
    "v1.0.0-rc.1",
    "feature/foo-bar",
    "release/1.0",
    "refs/heads/main",
    "refs/tags/v2",
    "dependabot/npm_and_yarn/left-pad-1.2.3",
    "_internal",
    "HEAD",
    "2",
    SHA40,
]


class _FakeProc:
    """Stand-in for CompletedProcess; every git call in clone() 'succeeds'."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def no_token(monkeypatch):
    """clone() picks a token out of the environment; a real one would change the argv."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


def _existing_checkout(tmp_path):
    """dest/<repo>/.git, so clone() takes the ISS-21 'reuse the checkout' branch."""
    target = tmp_path / "repo"
    (target / ".git").mkdir(parents=True)
    return target


def _no_subprocess(monkeypatch):
    """Make any `git` spawn a test failure, not a recorded call.

    A tripwire rather than a recorder on purpose: a validation check placed
    *after* the first git call then fails with this AssertionError, instead of
    passing on the ValueError that would eventually follow anyway.
    """

    def tripwire(cmd, *args, **kwargs):
        raise AssertionError(f"subprocess spawned before ref validation: {list(cmd)!r}")

    monkeypatch.setattr(fetch.subprocess, "run", tripwire)


def _record_subprocess(monkeypatch, proc=None):
    """Capture every git argv and return success, so the argv can be asserted."""
    calls: list[list[str]] = []

    def recorder(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return proc() if proc else _FakeProc()

    monkeypatch.setattr(fetch.subprocess, "run", recorder)
    return calls


# --------------------------------------------------------------------------- #
# Rejection
# --------------------------------------------------------------------------- #


#: Refs that are not merely option-shaped but degenerate. Kept out of `REJECTED`
#: because that list is also driven through `clone()`, where each entry costs a
#: subprocess-tripwire run; these only need the parser. They used to be a `for`
#: loop inside one test, which reported all three failures under a single test
#: name -- as separate rows, the id says which string got through.
REJECTED_DEGENERATE = ["", "main\x00", "\x00"]


@pytest.mark.parametrize("ref", REJECTED + REJECTED_DEGENERATE)
def test_iss237_parse_ref_rejects(ref):
    with pytest.raises(ValueError, match="not a valid git ref"):
        fetch.parse_ref(ref)


@pytest.mark.parametrize("ref", REJECTED)
def test_iss237_clone_rejects_before_any_subprocess(ref, tmp_path, monkeypatch, no_token):
    """The acceptance condition: ValueError, and git is never spawned.

    See `_no_subprocess` for why that second half is a tripwire.
    """
    _existing_checkout(tmp_path)
    _no_subprocess(monkeypatch)
    with pytest.raises(ValueError, match="not a valid git ref"):
        fetch.clone("owner/repo", tmp_path, ref=ref)


@pytest.mark.parametrize("ref", ["--upload-pack=/bin/false", "-x", "../main"])
def test_iss237_clone_rejects_on_the_fresh_clone_path_too(ref, tmp_path, monkeypatch, no_token):
    """No existing checkout: `git clone --branch` is option-safe, but still refuse."""

    _no_subprocess(monkeypatch)
    with pytest.raises(ValueError, match="not a valid git ref"):
        fetch.clone("owner/repo", tmp_path, ref=ref)


def test_iss237_index_github_rejects(tmp_path, monkeypatch, no_token):
    """The CLI entry point inherits the check through clone()."""

    _no_subprocess(monkeypatch)
    with pytest.raises(ValueError, match="not a valid git ref"):
        fetch.index_github(
            "owner/repo", tmp_path / "out", ref="--upload-pack=/bin/false", formats="jsonl"
        )


# --------------------------------------------------------------------------- #
# Neutrality
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("ref", ACCEPTED)
def test_iss237_parse_ref_accepts_ordinary_refs(ref):
    assert fetch.parse_ref(ref) == ref


@pytest.mark.parametrize("ref", ACCEPTED)
def test_iss237_clone_still_reaches_git_for_ordinary_refs(ref, tmp_path, monkeypatch, no_token):
    target = _existing_checkout(tmp_path)
    calls = _record_subprocess(monkeypatch)
    assert fetch.clone("owner/repo", tmp_path, ref=ref) == target
    assert calls == [
        ["git", "-C", str(target), "fetch", "--depth", "1", "origin", "--", ref],
        ["git", "-C", str(target), "checkout", ref],
    ]


def test_iss237_no_ref_is_unaffected(tmp_path, monkeypatch, no_token):
    """ref=None must stay a full no-op on the reuse path (no fetch, no checkout)."""
    target = _existing_checkout(tmp_path)
    calls = _record_subprocess(monkeypatch)
    assert fetch.clone("owner/repo", tmp_path, ref=None) == target
    assert calls == []


def test_iss237_fetch_gets_double_dash_and_checkout_does_not(tmp_path, monkeypatch, no_token):
    """`git fetch ... -- <ref>` is safe; `git checkout -- <ref>` means a pathspec.

    Pinned separately from the parametrized argv check because the asymmetry is
    the non-obvious half of the fix and would otherwise look like an oversight.
    """
    _existing_checkout(tmp_path)
    calls = _record_subprocess(monkeypatch)
    fetch.clone("owner/repo", tmp_path, ref="feature/foo-bar")

    fetch_cmd = calls[0]
    checkout_cmd = calls[1]
    assert fetch_cmd[-2:] == ["--", "feature/foo-bar"]
    assert "--" not in checkout_cmd
    assert checkout_cmd[-1] == "feature/foo-bar"


def test_iss237_fetch_head_retry_argv_unchanged(tmp_path, monkeypatch, no_token):
    """The shallow-clone FETCH_HEAD fallback must survive the added `--`."""
    target = _existing_checkout(tmp_path)
    calls: list[list[str]] = []

    def recorder(cmd, *args, **kwargs):
        cmd = list(cmd)
        calls.append(cmd)
        if "fetch" in cmd:
            return _FakeProc(0)
        if "FETCH_HEAD" in cmd:
            return _FakeProc(0)
        return _FakeProc(1, stderr="error: pathspec 'main' did not match")

    monkeypatch.setattr(fetch.subprocess, "run", recorder)
    assert fetch.clone("owner/repo", tmp_path, ref="main") == target
    assert calls == [
        ["git", "-C", str(target), "fetch", "--depth", "1", "origin", "--", "main"],
        ["git", "-C", str(target), "checkout", "main"],
        ["git", "-C", str(target), "checkout", "--detach", "FETCH_HEAD"],
    ]


# --------------------------------------------------------------------------- #
# What real git actually does with `--` (no network: a local origin)
# --------------------------------------------------------------------------- #


def _git(*args, cwd=None):
    # Bytes + surrogateescape, per AGENTS.md: a cp1252 locale must not turn a
    # non-ASCII byte in git's output into a UnicodeDecodeError.
    proc = subprocess.run(
        ["git", *args],
        cwd=None if cwd is None else str(cwd),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=120,
    )
    return proc.returncode, proc.stdout.decode("utf8", "surrogateescape")


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_iss237_real_git_double_dash_semantics(tmp_path):
    """Why fetch gets `--` and checkout does not, proved against the real binary."""
    origin = tmp_path / "origin"
    origin.mkdir()
    assert _git("-c", "init.defaultBranch=main", "init", "-q", str(origin))[0] == 0
    (origin / "f.txt").write_text("hi\n", encoding="utf8")
    assert _git("add", "f.txt", cwd=origin)[0] == 0
    assert (
        _git(
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            "init",
            cwd=origin,
        )[0]
        == 0
    )
    assert _git("branch", "feat", cwd=origin)[0] == 0

    work = tmp_path / "work"
    assert _git("clone", "-q", str(origin), str(work))[0] == 0
    start = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=work)[1].strip()

    # fetch: "--" is transparent — the ref still resolves and lands in FETCH_HEAD.
    assert _git("fetch", "--depth", "1", "origin", "--", "feat", cwd=work)[0] == 0
    # fetch: with "--", an option-shaped value is a refspec, not a flag.
    rc, _ = _git("fetch", "--depth", "1", "origin", "--", "--upload-pack=/bin/false", cwd=work)
    assert rc != 0

    # checkout: "--" means "a pathspec follows", so it does NOT switch ref.
    rc, _ = _git("checkout", "--", "feat", cwd=work)
    assert rc != 0
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=work)[1].strip() == start
    # ... while the bare form does.
    assert _git("checkout", "feat", cwd=work)[0] == 0
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=work)[1].strip() == "feat"
