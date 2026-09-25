"""Reproducibility: the same tree at the same commit must produce the same index.

The property under test is *byte equality of the agent artifacts*, not "the
same graph up to ordering". Ordering is observable: `nodes.jsonl` line order
is the order chunks are scored and cited, an index is a committable artifact
whose diff a human reviews, and "rebuilt on CI, 4,000 lines changed, all of
them reorderings" is indistinguishable from a real regression.

Two fields are deliberately *not* reproducible and are excluded by name:
`build_id` (a fresh uuid4 per build, which is what lets `vectors.meta.json`
detect that it was computed against a different build) and `created_at`.
Everything else is pinned.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from repo2graph.chunks import iter_chunks
from repo2graph.cli import main
from repo2graph.export import dump_all
from repo2graph.graph import build
from repo2graph.parse import BuildConfig, matches_any

# The artifacts that must be byte-identical across two builds of one tree.
REPRODUCIBLE = ("agent/nodes.jsonl", "agent/edges.jsonl", "agent/chunks.jsonl")

# Fields whose whole job is to differ between two builds.
NON_REPRODUCIBLE_MANIFEST_KEYS = ("build_id", "created_at")


@pytest.fixture
def sample_repo(tmp_path):
    """A tree with enough shape that ordering has something to get wrong:
    several directories, several languages, cross-file calls and imports."""
    src = tmp_path / "proj"
    (src / "app").mkdir(parents=True)
    (src / "app" / "sub").mkdir()
    (src / "web").mkdir()

    (src / "app" / "alpha.py").write_text(
        "ALPHA = 'the alpha module, with module-level residue'\n\n"
        "from .beta import beta_helper\n\n\n"
        "def alpha_entry(x):\n    return beta_helper(x)\n",
        encoding="utf-8",
    )
    (src / "app" / "beta.py").write_text(
        "BETA = 'the beta module, with module-level residue'\n\n\n"
        "def beta_helper(x):\n    return x * 2\n",
        encoding="utf-8",
    )
    (src / "app" / "sub" / "zeta.py").write_text(
        "ZETA = 'a nested module so directory order matters too'\n\n\ndef zeta():\n    return 0\n",
        encoding="utf-8",
    )
    (src / "web" / "client.js").write_text(
        "const API = '/v1';\n\nexport function call() {\n  return API;\n}\n",
        encoding="utf-8",
    )
    (src / "README.md").write_text(
        "# sample\n\nA sample tree used by the reproducibility suite.\n", encoding="utf-8"
    )
    return src


def _digests(outdir: Path) -> dict[str, str]:
    return {
        name: hashlib.sha256((outdir / name).read_bytes()).hexdigest()
        for name in REPRODUCIBLE
        if (outdir / name).exists()
    }


def test_two_builds_of_one_tree_are_byte_identical(sample_repo, tmp_path):
    """The baseline property: build twice, same bytes."""
    first, second = tmp_path / "a", tmp_path / "b"
    assert main(["build", str(sample_repo), "-o", str(first), "--formats", "jsonl"]) == 0
    assert main(["build", str(sample_repo), "-o", str(second), "--formats", "jsonl"]) == 0

    da, db = _digests(first), _digests(second)
    assert da, "no reproducible artifacts were written"
    assert da == db, f"artifacts differ between two builds of one tree: {da} != {db}"


def test_serial_and_parallel_builds_agree(sample_repo, tmp_path, monkeypatch):
    """`--jobs N` must not reorder anything.

    `parse_all` uses `pool.map`, which preserves input order, and the build
    loop consumes results in discovery order -- but the fixture is far under
    PARALLEL_MIN_FILES, so the pool never starts for it. Drop the threshold so
    the parallel path is the one actually exercised here.
    """
    import repo2graph.graph as graph_mod

    serial = tmp_path / "serial"
    assert (
        main(["build", str(sample_repo), "-o", str(serial), "--formats", "jsonl", "--jobs", "1"])
        == 0
    )

    monkeypatch.setattr(graph_mod, "PARALLEL_MIN_FILES", 1)
    parallel = tmp_path / "parallel"
    assert (
        main(["build", str(sample_repo), "-o", str(parallel), "--formats", "jsonl", "--jobs", "4"])
        == 0
    )

    assert _digests(serial) == _digests(parallel), "parallel parsing reordered the artifacts"


def _reversed_walk(top, *a, **kw):
    """os.walk with every directory listing reversed.

    This stands in for "the same tree on a different filesystem". A
    same-machine A/B cannot see an enumeration-order bug at all: NTFS returns
    entries alphabetically, so the unsorted order and the sorted order happen
    to coincide, and every assertion passes while the bug is live. ext4 with
    dir_index returns hash order, which does not coincide.
    """
    for dirpath, dirnames, filenames in _REAL_WALK(top, *a, **kw):
        dirnames.sort(reverse=True)
        yield dirpath, dirnames, sorted(filenames, reverse=True)


_REAL_WALK = os.walk


def test_filesystem_enumeration_order_does_not_change_the_index(sample_repo, tmp_path, monkeypatch):
    """The bug this suite exists for.

    Discovery order *is* artifact order. `git ls-files` sorts its output but
    `os.walk` does not, so the same tree indexed on two machines produced
    three artifacts that differed byte-for-byte while describing an identical
    graph.
    """
    digests = {}
    for label, walker in (("forward", _REAL_WALK), ("reversed", _reversed_walk)):
        monkeypatch.setattr(os, "walk", walker)
        g = build(sample_repo, config=BuildConfig(), jobs=1)
        monkeypatch.undo()
        out = tmp_path / label
        dump_all(g, iter_chunks(g), out, {"jsonl"}, 0)
        digests[label] = _digests(out)

    assert digests["forward"] == digests["reversed"], (
        "filesystem enumeration order changed the artifacts; discovery is not sorted"
    )


def test_git_and_walk_discovery_agree_on_the_same_tree(sample_repo, tmp_path):
    """A tree with nothing ignored must index identically with and without git.

    Same content, two discovery sources: `git ls-files` when `.git` is
    present, `os.walk` when it is not. They are only interchangeable if both
    are sorted, which is the invariant `discover()` now enforces in one place
    for both branches.
    """
    walk_out = tmp_path / "walk"
    assert main(["build", str(sample_repo), "-o", str(walk_out), "--formats", "jsonl"]) == 0

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e",
    }
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "init"]):
        proc = subprocess.run(["git", "-C", str(sample_repo), *cmd], capture_output=True, env=env)
        if proc.returncode != 0:
            pytest.skip(f"git unavailable: {proc.stderr.decode('utf8', 'replace')[:120]}")

    git_out = tmp_path / "git"
    assert main(["build", str(sample_repo), "-o", str(git_out), "--formats", "jsonl"]) == 0

    walk_d, git_d = _digests(walk_out), _digests(git_out)
    assert walk_d == git_d, (
        "git and os.walk discovery produced different artifacts for the same tree"
    )


def test_incremental_rebuild_equals_a_full_rebuild(sample_repo, tmp_path):
    """`--incremental` is a speed optimisation, not a different index.

    A reused `ParsedFile` is exact -- it is a pure function of the file's
    bytes and language -- and everything downstream (the global name index,
    CALLS confidences, INHERITS, entrypoints, reach) is recomputed from the
    full symbol set regardless. So the output must be byte-identical, not
    merely close, both when nothing changed and after an edit.
    """
    out = tmp_path / "idx"
    assert main(["build", str(sample_repo), "-o", str(out), "--formats", "jsonl"]) == 0
    baseline = _digests(out)

    assert (
        main(["build", str(sample_repo), "-o", str(out), "--formats", "jsonl", "--incremental"])
        == 0
    )
    assert _digests(out) == baseline, "an incremental no-op rebuild changed the artifacts"

    # Now change one file and compare incremental against a from-scratch build.
    (sample_repo / "app" / "beta.py").write_text(
        "BETA = 'the beta module, edited, with module-level residue'\n\n\n"
        "def beta_helper(x):\n    return x * 3\n\n\ndef beta_extra():\n    return 1\n",
        encoding="utf-8",
    )
    assert (
        main(["build", str(sample_repo), "-o", str(out), "--formats", "jsonl", "--incremental"])
        == 0
    )
    incremental = _digests(out)

    scratch = tmp_path / "scratch"
    assert main(["build", str(sample_repo), "-o", str(scratch), "--formats", "jsonl"]) == 0
    assert incremental == _digests(scratch), (
        "an incremental rebuild after an edit differs from a full rebuild"
    )


def test_manifest_is_identical_apart_from_the_two_provenance_fields(sample_repo, tmp_path):
    """Everything in manifest.json is reproducible except build_id and created_at.

    Pinned as an explicit allowlist rather than "ignore whatever differs", so
    a third non-deterministic field added later fails here instead of quietly
    joining the exceptions.
    """
    first, second = tmp_path / "a", tmp_path / "b"
    assert main(["build", str(sample_repo), "-o", str(first), "--formats", "jsonl"]) == 0
    assert main(["build", str(sample_repo), "-o", str(second), "--formats", "jsonl"]) == 0

    ma = json.loads((first / "agent" / "manifest.json").read_text(encoding="utf8"))
    mb = json.loads((second / "agent" / "manifest.json").read_text(encoding="utf8"))

    differing = sorted(k for k in set(ma) | set(mb) if ma.get(k) != mb.get(k))
    assert differing == sorted(NON_REPRODUCIBLE_MANIFEST_KEYS), (
        f"manifest.json fields differ beyond the known two: {differing}"
    )
    for key in NON_REPRODUCIBLE_MANIFEST_KEYS:
        assert ma[key] != mb[key], f"{key} was expected to differ between builds but did not"


def test_max_files_truncation_is_deterministic(sample_repo, tmp_path, monkeypatch):
    """`--max-files N` takes the first N in discovery order.

    Unsorted discovery made this actively dangerous rather than merely
    untidy: which files landed in a truncated index depended on the
    filesystem, so two machines indexed *different subsets* of the same tree
    and each answered questions the other could not. The reversed walk is
    what makes this a detector -- under the real one, NTFS order and sorted
    order coincide and the assertion holds either way.
    """

    def truncated(out, walker):
        monkeypatch.setattr(os, "walk", walker)
        g = build(sample_repo, config=BuildConfig(), max_files=3, jobs=1)
        monkeypatch.undo()
        dump_all(g, iter_chunks(g), out, {"jsonl"}, 0)
        return [
            json.loads(line)["path"]
            for line in (out / "agent" / "nodes.jsonl").read_text(encoding="utf8").split("\n")
            if line.strip() and json.loads(line).get("type") == "file"
        ]

    forward = truncated(tmp_path / "fwd", _REAL_WALK)
    reverse = truncated(tmp_path / "rev", _reversed_walk)

    assert forward == reverse, (
        f"--max-files kept a different subset under a different enumeration "
        f"order: {forward} vs {reverse}"
    )
    # Hand-derived from the fixture: sorted discovery order puts README.md
    # first, then app/alpha.py, then app/beta.py.
    assert forward == ["README.md", "app/alpha.py", "app/beta.py"], forward


# --------------------------------------------------------------------------
# Exclusion groups
# --------------------------------------------------------------------------


def test_every_exclusion_glob_matches_its_representative_paths():
    """A pattern that matches nothing is worse than no pattern.

    `parse._glob_re` anchors a pattern containing "/" at the repository root,
    so `vendor/**` silently misses `packages/web/vendor/...` -- which is
    where a monorepo keeps all of it. Each group declares the paths it must
    cover; this runs them through the real matcher.
    """
    from repo2graph.exclusions import GROUPS

    for group in GROUPS.values():
        assert group.representative, f"group {group.name!r} declares no representative paths"
        for rel in group.representative:
            assert matches_any(rel, list(group.globs)), (
                f"exclusion group {group.name!r} does not match its own representative path {rel!r}"
            )


def test_exclusion_groups_do_not_match_ordinary_source():
    """The other half: a group that excludes hand-written code is a footgun."""
    from repo2graph.exclusions import GROUPS, globs_for

    everything = globs_for(["all"])
    for rel in (
        "src/app/main.py",
        "pkg/service/handler.go",
        "lib/models.dart",
        "web/src/index.ts",
        "tests/test_api.py",
        "README.md",
        "generator.py",  # "gen" as a substring must not trigger the "generated" group
        "app/keyboard.py",  # nor "key" for the sensitive group
    ):
        assert not matches_any(rel, everything), f"{rel} would be excluded by --exclude-group all"
    assert set(GROUPS) == {"generated", "vendor", "build", "dependencies", "sensitive"}


def test_globs_for_expands_all_and_rejects_an_unknown_group():
    from repo2graph.exclusions import GROUPS, globs_for

    every = globs_for(["all"])
    for group in GROUPS.values():
        for glob in group.globs:
            assert glob in every
    # De-duplicated and stable: "vendor" appears in more than one group's
    # conceptual territory, and the CLI joins these into one --exclude list.
    assert len(every) == len(set(every))
    assert globs_for(["generated", "generated"]) == globs_for(["generated"])

    with pytest.raises(ValueError, match="unknown exclusion group"):
        globs_for(["nope"])


def test_exclude_group_actually_removes_files_from_the_index(tmp_path):
    src = tmp_path / "proj"
    (src / "pkg").mkdir(parents=True)
    (src / "api" / "generated").mkdir(parents=True)
    (src / "pkg" / "core.py").write_text(
        "CORE = 'hand written module with plenty of residue'\n", encoding="utf-8"
    )
    (src / "pkg" / "schema_pb2.py").write_text(
        "DESCRIPTOR = 'protobuf stub regenerated on every build'\n", encoding="utf-8"
    )
    (src / "api" / "generated" / "client.py").write_text(
        "GEN = 'generated client module residue text here'\n", encoding="utf-8"
    )
    (src / "package-lock.json").write_text('{"lockfileVersion": 3}\n', encoding="utf-8")

    def indexed(out, *extra):
        assert main(["build", str(src), "-o", str(out), "--formats", "jsonl", *extra]) == 0
        return {
            json.loads(line)["path"]
            for line in (out / "agent" / "nodes.jsonl").read_text(encoding="utf8").split("\n")
            if line.strip() and json.loads(line).get("type") == "file"
        }

    baseline = indexed(tmp_path / "base")
    assert baseline == {
        "pkg/core.py",
        "pkg/schema_pb2.py",
        "api/generated/client.py",
        "package-lock.json",
    }

    assert indexed(tmp_path / "gen", "--exclude-group", "generated") == {
        "pkg/core.py",
        "package-lock.json",
    }
    assert indexed(
        tmp_path / "both", "--exclude-group", "generated", "--exclude-group", "dependencies"
    ) == {"pkg/core.py"}


def test_explain_path_agrees_with_build_and_names_the_matching_glob(tmp_path, capsys):
    """`explain-path` must answer from the rule set the build would use.

    It also has to name *which* pattern matched: a `--exclude-group` expands
    to as many as 60 globs, and echoing the whole list buries the answer in
    the evidence.
    """
    src = tmp_path / "proj"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "schema_pb2.py").write_text("D = 'stub'\n", encoding="utf-8")

    assert (
        main(
            [
                "explain-path",
                "pkg/schema_pb2.py",
                "-r",
                str(src),
                "--exclude-group",
                "generated",
                "--json",
            ]
        )
        == 0
    )
    res = json.loads(capsys.readouterr().out)
    assert res["included"] is False
    assert res["rule"] == "exclude_glob"
    assert res["matched_glob"] == "*_pb2.py"
    assert res["reason"].count("'") == 2, f"reason should name one glob: {res['reason']}"


def test_exclude_group_help_prints_every_group_and_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["build", ".", "-o", "unused", "--exclude-group", "help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    from repo2graph.exclusions import GROUP_NAMES

    for name in GROUP_NAMES:
        assert name in out
    assert "--exclude-group" in out


@pytest.mark.skipif(sys.platform == "win32", reason="requires a POSIX-only path character")
def test_discovery_sort_key_is_posix(tmp_path):
    """Sorting on str(Path) would sort on "\\" on Windows and "/" elsewhere --
    the same cross-machine divergence one level down from the os.walk bug."""
    from repo2graph.parse import discover

    src = tmp_path / "proj"
    (src / "a-dir").mkdir(parents=True)
    (src / "a-dir" / "z.py").write_text(
        "Z = 'z module residue text right here'\n", encoding="utf-8"
    )
    (src / "a.py").write_text("A = 'a module residue text right here ok'\n", encoding="utf-8")

    rels = [rel for rel, _abs in discover(src, config=BuildConfig())]
    assert rels == sorted(rels), f"discovery is not sorted: {rels}"
