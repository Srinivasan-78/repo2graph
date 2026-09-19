"""`human/CHANGELOG.md`: a per-push structural diff of the graph.

Per AGENTS.md's "tests must pin values, not compare the implementation to
itself": every assertion below is a literal string hand-derived from the
fixture source, never a value recomputed by `repo2graph.changelog` itself.
"""

import json
from pathlib import Path

from repo2graph.cli import main
from repo2graph.export import path as artifact_path

FORMATS = "jsonl,overview"

FILES = {
    "pkg/__init__.py": "VERSION = '1.0'\n",
    "pkg/alpha.py": (
        "ALPHA_TABLE = {'a': 1, 'b': 2}\n\n\n"
        "def handle(payload):\n"
        "    return ALPHA_TABLE.get(payload)\n"
    ),
}


def write_repo(root: Path, files=None) -> Path:
    repo = root / "src"
    for rel, text in (FILES if files is None else files).items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf8", newline="\n") as fh:
            fh.write(text)
    return repo


def build(repo, out):
    main(["build", str(repo), "-o", str(out), "--formats", FORMATS])


def changelog_text(out) -> str:
    return artifact_path(out, "CHANGELOG.md").read_text(encoding="utf8")


def test_first_build_is_the_initial_message(tmp_path):
    """No previous agent/nodes.jsonl to diff against: the file is exactly one line."""
    repo = write_repo(tmp_path)
    out = tmp_path / "idx"
    build(repo, out)

    assert changelog_text(out) == "## Initial build — no previous index to diff against.\n"


def test_changelog_only_written_for_human_facing_builds(tmp_path):
    """No 'overview' in --formats: CHANGELOG.md is not written at all."""
    repo = write_repo(tmp_path)
    out = tmp_path / "idx"
    main(["build", str(repo), "-o", str(out), "--formats", "jsonl"])

    assert not artifact_path(out, "CHANGELOG.md").exists()


def test_second_build_lists_the_new_symbol_and_file(tmp_path):
    """A function and file added between builds show up under 'New nodes'."""
    repo = write_repo(tmp_path)
    out = tmp_path / "idx"
    build(repo, out)

    (repo / "pkg" / "beta.py").write_text(
        "BETA_TABLE = {'q': 9}\n\n\ndef greet(name):\n    return BETA_TABLE.get(name)\n",
        encoding="utf8",
        newline="\n",
    )
    build(repo, out)

    text = changelog_text(out)
    assert "### New nodes" in text
    assert "- sym:pkg/beta.py::greet  (symbol)" in text
    assert "- file:pkg/beta.py  (file)" in text
    # Nothing existing was removed between the two builds.
    assert "### Removed nodes" not in text
    assert "### Removed edges" not in text


def test_second_build_lists_a_new_edge(tmp_path):
    """A new CALLS edge introduced between builds shows up under 'New edges'."""
    repo = write_repo(tmp_path)
    out = tmp_path / "idx"
    build(repo, out)

    (repo / "pkg" / "caller.py").write_text(
        "from pkg.alpha import handle\n\nCALLER_TABLE = {'x': 1}\n\n\n"
        "def entry(payload):\n    return handle(payload)\n",
        encoding="utf8",
        newline="\n",
    )
    build(repo, out)

    text = changelog_text(out)
    assert "### New edges" in text
    assert "- CALLS: sym:pkg/caller.py::entry → sym:pkg/alpha.py::handle  (confidence: 1.0)" in text
    assert "- IMPORTS: file:pkg/caller.py → file:pkg/alpha.py" in text


def test_unchanged_repo_produces_no_delta_sections(tmp_path):
    """Rebuilding an unchanged repo: a delta header, but every section empty."""
    repo = write_repo(tmp_path)
    out = tmp_path / "idx"
    build(repo, out)
    build(repo, out)

    text = changelog_text(out)
    assert text.startswith("## Graph delta")
    for heading in (
        "### New nodes",
        "### Removed nodes",
        "### New edges",
        "### Removed edges",
        "### New hotspots",
    ):
        assert heading not in text


def test_iss144_changelog_registered_in_manifest(tmp_path, capsys):
    """Issue 144: CHANGELOG.md is registered in manifest.json and the CLI report.

    dump_all writes manifest.json before write_changelog runs, so validators
    that trust manifest.written miss human/CHANGELOG.md unless register_written
    is called afterwards. Pin the relative path, not a value from dump_all.
    """
    repo = write_repo(tmp_path)
    out = tmp_path / "idx"
    main(["build", str(repo), "-o", str(out), "--formats", FORMATS])

    changelog = artifact_path(out, "CHANGELOG.md")
    assert changelog.is_file()
    assert changelog == out / "human" / "CHANGELOG.md"

    manifest_path = artifact_path(out, "manifest.json")
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf8"))
    assert "human/CHANGELOG.md" in manifest["written"]

    report = json.loads(capsys.readouterr().out)
    assert "human/CHANGELOG.md" in report["written"]
