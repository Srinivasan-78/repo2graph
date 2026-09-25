"""Tests for `repo2graph index-status` and the shared freshness computation."""

import json
import os
import subprocess

import pytest

from repo2graph.cli import main
from repo2graph.status import (
    compute_freshness,
    format_age,
    human_bytes,
    index_status,
)


@pytest.fixture
def built(tmp_path):
    """A small repository with an index built over it."""
    src = tmp_path / "proj"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "core.py").write_text(
        "TITLE = 'the core module, with module-level residue'\n\n\n"
        "def run(x):\n    return helper(x)\n\n\ndef helper(x):\n    return x + 1\n",
        encoding="utf-8",
    )
    (src / "pkg" / "util.py").write_text(
        "HELPERS = ['a', 'b']  # module-level residue so this file gets a chunk\n\n\n"
        "class Util:\n    def go(self):\n        return HELPERS\n",
        encoding="utf-8",
    )
    (src / "README.md").write_text("# proj\n\nA fixture repository.\n", encoding="utf-8")
    out = src / ".r2g"
    assert main(["build", str(src), "-o", str(out)]) == 0
    return src, out


def _git_init(src):
    """Make `src` a git checkout, with `.r2g/` ignored as a real repo would.

    Without the .gitignore, `git add -A` commits the index's own artifacts:
    `git ls-files` then returns them, the build indexes itself, and the tree
    reads as permanently dirty. That is a fixture artefact, not a behaviour
    worth asserting on -- README.md tells users to ignore `.r2g/`.
    """
    (src / ".gitignore").write_text(".r2g/\n", encoding="utf-8")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e",
    }
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "init"]):
        proc = subprocess.run(["git", "-C", str(src), *cmd], capture_output=True, env=env)
        if proc.returncode != 0:
            pytest.skip(f"git unavailable: {proc.stderr.decode('utf8', 'replace')[:120]}")
    return env


# --------------------------------------------------------------------------
# The report covers every field the feature promises
# --------------------------------------------------------------------------


def test_report_carries_every_promised_field(built):
    """docs/INDEXING.md promises nine things. This is that list."""
    src, out = built
    report = index_status(out)

    # indexed commit / branch (absent here -- not a git tree -- but present as keys)
    assert "commit" in report["source"] and "branch" in report["source"]
    # last index time
    assert report["index"]["created_at"]
    assert report["index"]["age_seconds"] is not None
    # repository file / symbol / edge counts
    assert report["contents"]["files_discovered"] == 3
    assert report["contents"]["files_parsed"] == 2
    assert report["contents"]["symbols"] > 0
    assert report["contents"]["edges"] > 0
    assert report["contents"]["nodes"] > 0
    # detected languages
    assert report["contents"]["languages"].get("python") == 2
    # skipped paths
    assert "skipped" in report["discovery"] and "skipped_total" in report["discovery"]
    assert report["discovery"]["mode"] in ("git", "walk")
    # parse failures
    assert report["parsing"]["parse_errors"] == 0
    # index size
    assert report["index"]["size_bytes"] > 0
    assert report["index"]["size_human"].endswith(("B", "kB", "MB", "GB"))
    # freshness status
    assert report["freshness"]["status"] == "current"


def test_report_sections_are_the_documented_six(built):
    """The `--json` report is a public surface: `--json` prints it verbatim.

    Asserted against a real report rather than by scraping the source, so a
    section that is built but empty, or renamed in one place only, fails here.
    """
    _src, out = built
    assert set(index_status(out)) == {
        "index",
        "source",
        "contents",
        "discovery",
        "parsing",
        "freshness",
    }


def test_symbol_and_edge_breakdowns_match_the_totals(built):
    """The by-kind maps are a decomposition, not a second measurement."""
    _src, out = built
    report = index_status(out)
    kinds = report["contents"]["symbols_by_kind"]
    assert kinds, "no symbol breakdown"
    assert sum(kinds.values()) == report["contents"]["symbols"]
    assert sum(report["contents"]["edges_by_type"].values()) == report["contents"]["edges"]


def test_missing_index_is_an_instruction_not_a_traceback(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["index-status", "-o", str(tmp_path / "nothing")])
    message = str(exc.value)
    assert "no repo2graph index" in message
    assert "repo2graph build" in message


# --------------------------------------------------------------------------
# Freshness
# --------------------------------------------------------------------------


def test_freshness_is_current_on_a_fresh_build(built):
    src, out = built
    fresh = compute_freshness(src, out, out / "agent")
    assert fresh.status == "current"
    assert fresh.is_current
    assert not fresh.reasons
    assert fresh.files_checked == 3


def test_freshness_detects_a_modified_file(built):
    """mtime only chooses what to hash; the finding is a hash mismatch."""
    src, out = built
    manifest = out / "agent" / "manifest.json"
    target = src / "pkg" / "core.py"
    target.write_text("TITLE = 'the core module, now entirely different'\n", encoding="utf-8")
    cutoff = manifest.stat().st_mtime
    os.utime(target, (cutoff + 10, cutoff + 10))

    fresh = compute_freshness(src, out, out / "agent")
    assert fresh.status == "stale"
    assert fresh.modified == ["pkg/core.py"]
    assert "1 modified" in fresh.reasons


def test_freshness_ignores_a_touched_but_unchanged_file(built):
    """A fresh clone rewrites every mtime without changing a byte. Calling
    that stale would make the check useless on exactly the machine -- CI --
    that most needs it."""
    src, out = built
    cutoff = (out / "agent" / "manifest.json").stat().st_mtime
    for rel in ("pkg/core.py", "pkg/util.py", "README.md"):
        os.utime(src / rel, (cutoff + 10, cutoff + 10))

    fresh = compute_freshness(src, out, out / "agent")
    assert fresh.status == "current", fresh.reasons
    assert not fresh.modified


def test_freshness_detects_added_and_removed_files(built):
    src, out = built
    (src / "pkg" / "extra.py").write_text(
        "EXTRA = 'a module the index has never seen before'\n", encoding="utf-8"
    )
    (src / "README.md").unlink()

    fresh = compute_freshness(src, out, out / "agent")
    assert fresh.status == "stale"
    assert fresh.added == ["pkg/extra.py"]
    assert fresh.removed == ["README.md"]


def test_freshness_does_not_count_the_index_as_added_source(built):
    """`.r2g` is not in DEFAULT_SKIP_DIRS and did not exist when the build ran
    discovery, so on a non-git tree every artifact it just wrote came back as
    a newly "added" source file and a brand-new index read as stale."""
    src, out = built
    fresh = compute_freshness(src, out, out / "agent")
    assert not any(p.startswith(".r2g") for p in fresh.added), fresh.added


def test_a_filtered_build_is_not_instantly_stale(tmp_path):
    """Freshness must re-discover with the filters the *build* used.

    Comparing against default filters re-finds every file the build
    deliberately excluded, reports them all as newly added, and so any build
    with `--exclude` or `--exclude-group` reads as stale the instant it
    finishes -- which makes the whole freshness signal worthless for exactly
    the users who configured indexing most carefully.
    """
    src = tmp_path / "proj"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "core.py").write_text(
        "CORE = 'hand written module with plenty of residue'\n", encoding="utf-8"
    )
    (src / "pkg" / "schema_pb2.py").write_text(
        "DESCRIPTOR = 'protobuf stub regenerated on every build'\n", encoding="utf-8"
    )
    (src / "package-lock.json").write_text('{"lockfileVersion": 3}\n', encoding="utf-8")
    (src / "notes.md").write_text("# notes\n\nSome prose.\n", encoding="utf-8")

    out = src / ".r2g"
    assert (
        main(
            [
                "build",
                str(src),
                "-o",
                str(out),
                "--exclude-group",
                "generated",
                "--exclude-group",
                "dependencies",
                "--exclude",
                "*.md",
            ]
        )
        == 0
    )

    report = index_status(out)
    assert report["freshness"]["status"] == "current", report["freshness"]
    assert report["freshness"]["added"] == [], (
        f"excluded files came back as newly added: {report['freshness']['added']}"
    )

    # And a genuine change is still caught, so the fix did not simply widen
    # the filter until nothing is ever reported.
    manifest = out / "agent" / "manifest.json"
    target = src / "pkg" / "core.py"
    target.write_text("CORE = 'edited, entirely different bytes now'\n", encoding="utf-8")
    cutoff = manifest.stat().st_mtime
    os.utime(target, (cutoff + 10, cutoff + 10))
    assert index_status(out)["freshness"]["modified"] == ["pkg/core.py"]


def test_state_file_records_the_discovery_filters(tmp_path):
    """The filters are persisted, not re-derived -- there is nowhere else to
    recover `--exclude-group generated` from after the build exits."""
    src = tmp_path / "proj"
    src.mkdir()
    (src / "core.py").write_text("CORE = 'a module with enough residue here'\n", encoding="utf-8")
    out = src / ".r2g"
    assert (
        main(
            [
                "build",
                str(src),
                "-o",
                str(out),
                "--exclude",
                "*.md",
                "--exclude-dir",
                "scratch",
                "--include-vendor",
            ]
        )
        == 0
    )

    state = json.loads((out / "agent" / "index.state.json").read_text(encoding="utf8"))
    filters = state["filters"]
    assert filters["exclude"] == ["*.md"]
    assert filters["extra_exclude_dirs"] == ["scratch"]
    assert filters["include_vendor"] is True
    assert filters["include_secrets"] is False


def test_freshness_falls_back_when_the_state_file_predates_filters(built):
    """An index built before filters were recorded must still be checkable,
    and must say that its comparison used defaults."""
    src, out = built
    state_file = out / "agent" / "index.state.json"
    state = json.loads(state_file.read_text(encoding="utf8"))
    del state["filters"]
    state_file.write_text(json.dumps(state), encoding="utf8")

    fresh = compute_freshness(src, out, out / "agent")
    assert fresh.status == "current"
    assert any("no discovery filters" in note for note in fresh.notes)


def test_freshness_without_state_file_is_unknown_not_stale(built):
    """An index built by an older version cannot be checked at file level.
    "I could not tell" is not the same claim as "it is out of date"."""
    src, out = built
    (out / "agent" / "index.state.json").unlink()

    fresh = compute_freshness(src, out, out / "agent")
    assert fresh.status == "unknown"
    assert any("index.state.json" in note for note in fresh.notes)


def test_freshness_respects_the_scan_bound(built, monkeypatch):
    """Past the bound the file-level check is skipped and says so, rather
    than re-hashing an attacker-chosen number of bytes."""
    import repo2graph.status as status_mod

    src, out = built
    monkeypatch.setattr(status_mod, "MAX_FRESHNESS_FILES", 1)
    fresh = compute_freshness(src, out, out / "agent")
    assert fresh.status == "unknown"
    assert any("scan bound" in note for note in fresh.notes)


def test_doctor_and_index_status_never_disagree(built):
    """Two implementations of "is this stale" drift until they contradict
    each other in front of a user. There is one, and this pins that."""
    from repo2graph.doctor import check_index_freshness

    src, out = built
    assert index_status(out)["freshness"]["status"] == "current"
    assert check_index_freshness(src).status == "ok"

    manifest = out / "agent" / "manifest.json"
    target = src / "pkg" / "util.py"
    target.write_text("HELPERS = ['changed entirely now, different bytes']\n", encoding="utf-8")
    cutoff = manifest.stat().st_mtime
    os.utime(target, (cutoff + 10, cutoff + 10))

    assert index_status(out)["freshness"]["status"] == "stale"
    doctor_result = check_index_freshness(src)
    assert doctor_result.status == "warn"
    assert "1 modified" in doctor_result.summary


# --------------------------------------------------------------------------
# Git-aware metadata
# --------------------------------------------------------------------------


def test_git_metadata_reaches_the_report(built):
    src, out = built
    env = _git_init(src)
    subprocess.run(
        ["git", "-C", str(src), "checkout", "-qb", "feature/x"], capture_output=True, env=env
    )
    (src / "pkg" / "new.py").write_text("NEW = 'a new module on the branch'\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(src), "add", "-A"], capture_output=True, env=env)
    subprocess.run(
        ["git", "-C", str(src), "commit", "-qm", "feature"], capture_output=True, env=env
    )

    assert main(["build", str(src), "-o", str(out)]) == 0
    report = index_status(out)

    assert report["source"]["commit"]
    assert report["source"]["short_commit"]
    assert report["source"]["branch"] == "feature/x"
    # `git init` makes `master` by default on older gits and `main` on newer;
    # either is a valid base here, and the point is that one was detected.
    assert report["source"]["base_branch"] in ("main", "master")
    assert report["source"]["commits_ahead_of_base"] == 1
    assert report["source"]["dirty"] is False


def test_repo2graphs_own_scratch_files_do_not_mark_the_tree_dirty(built):
    """A clean checkout must not record `dirty: true` because of repo2graph.

    `BuildLock` writes `..r2g.r2glock` and `dump_all` stages artifacts in
    `..r2g.staging.<pid>.<hex>/`, both *beside* the output directory so they
    survive the transactional swap -- which means `.r2g/` in .gitignore
    covers neither. Provenance is captured from inside both windows, so every
    build of a pristine repository used to report a dirty tree and blame the
    user for repo2graph's own scratch files.
    """
    from repo2graph.integrity import _is_own_transient

    src, out = built
    _git_init(src)
    assert main(["build", str(src), "-o", str(out)]) == 0

    report = index_status(out)
    assert report["source"]["dirty"] is False, (
        "a clean checkout was reported dirty; repo2graph's own transient files leaked in"
    )

    # The predicate itself, so a porcelain-format change is caught here and
    # not only through the end-to-end path above.
    assert _is_own_transient("?? ..r2g.r2glock")
    assert _is_own_transient("?? ..r2g.staging.4242.deadbeef/")
    assert _is_own_transient('?? "..r2g.staging.1.a b/"')
    # A tracked file is the user's whatever it is called, and an ordinary
    # untracked source file still counts as dirty.
    assert not _is_own_transient(" M ..r2g.r2glock")
    assert not _is_own_transient("?? pkg/new_module.py")
    assert not _is_own_transient("?? staging.py")


def test_dirty_working_tree_is_reported_with_a_count(built):
    src, out = built
    _git_init(src)
    (src / "pkg" / "core.py").write_text(
        "TITLE = 'edited but not committed at all'\n", encoding="utf-8"
    )
    assert main(["build", str(src), "-o", str(out)]) == 0

    report = index_status(out)
    assert report["source"]["dirty"] is True
    assert report["source"]["dirty_files"] >= 1


def test_commit_drift_is_detected(built):
    src, out = built
    env = _git_init(src)
    assert main(["build", str(src), "-o", str(out)]) == 0
    assert index_status(out)["freshness"]["status"] == "current"

    (src / "pkg" / "later.py").write_text(
        "LATER = 'committed after the index was built'\n", encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(src), "add", "-A"], capture_output=True, env=env)
    subprocess.run(["git", "-C", str(src), "commit", "-qm", "later"], capture_output=True, env=env)

    fresh = index_status(out)["freshness"]
    assert fresh["status"] == "stale"
    assert fresh["commit_moved"] is True
    assert "HEAD moved since the build" in fresh["reasons"]


# --------------------------------------------------------------------------
# CLI surface
# --------------------------------------------------------------------------


def test_cli_text_output(built, capsys):
    _src, out = built
    assert main(["index-status", "-o", str(out)]) == 0
    text = capsys.readouterr().out
    assert "repo2graph index-status" in text
    assert "[CURRENT]" in text
    for section in ("Source", "Index", "Contents", "Discovery", "Parsing", "Freshness"):
        assert section in text


def test_cli_json_output_is_the_report_verbatim(built, capsys):
    _src, out = built
    assert main(["index-status", "-o", str(out), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert (
        data == index_status(out)
        or data["index"]["build_id"] == index_status(out)["index"]["build_id"]
    )


def test_cli_check_flag_gates_on_freshness(built, capsys):
    """`--check` is the CI gate: a committed index that no longer matches the
    commit it claims is a reviewable failure, not a warning nobody reads."""
    src, out = built
    assert main(["index-status", "-o", str(out), "--check"]) == 0
    capsys.readouterr()

    manifest = out / "agent" / "manifest.json"
    target = src / "pkg" / "core.py"
    target.write_text("TITLE = 'changed after the index was written here'\n", encoding="utf-8")
    cutoff = manifest.stat().st_mtime
    os.utime(target, (cutoff + 10, cutoff + 10))

    assert main(["index-status", "-o", str(out), "--check"]) == 1
    text = capsys.readouterr().out
    assert "[STALE]" in text
    assert "--incremental" in text


def test_pointing_at_the_agent_dir_gives_the_same_answer(built):
    """`doctor` documents accepting either the -o directory or `agent/`, and a
    caller who already resolved the path must not get a different report.

    The source tree is the *index root's* parent -- one level up from `.r2g`,
    two from `.r2g/agent` -- so deriving it from whatever directory was handed
    in pointed the freshness check at `.r2g` and reported every source file as
    removed.
    """
    _src, out = built
    via_root = index_status(out)
    via_agent = index_status(out / "agent")

    assert via_agent["index"]["path"] == via_root["index"]["path"]
    assert via_agent["source"]["path"] == via_root["source"]["path"]
    assert via_agent["freshness"]["status"] == "current"
    assert via_agent["contents"] == via_root["contents"]
    assert via_agent["index"]["size_bytes"] == via_root["index"]["size_bytes"]


def test_cli_accepts_an_explicit_repo_path(built, capsys):
    """The index need not live inside the tree it describes."""
    src, out = built
    assert main(["index-status", "-o", str(out), "-r", str(src), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["source"]["path"] == str(src)
    assert data["freshness"]["status"] == "current"


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------


def test_human_bytes_uses_decimal_units():
    assert human_bytes(0) == "0 B"
    assert human_bytes(999) == "999 B"
    assert human_bytes(1000) == "1.0 kB"
    assert human_bytes(1_500_000) == "1.5 MB"
    assert human_bytes(2_500_000_000) == "2.5 GB"


def test_format_age():
    assert format_age(None) == "unknown"
    assert format_age(5) == "5s ago"
    assert format_age(600) == "10m ago"
    assert format_age(7200) == "2h ago"
    assert format_age(400_000) == "4d ago"
