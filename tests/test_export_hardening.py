"""Regression tests for the round-3 MCP/export hardening pass.

Covers:
  - #340: repo2graph.__version__ must not be shadowed by a stale installed
    dist-info when running from a source checkout.
  - #349: NODE_TYPES / EDGE_TYPES must be defined once (in viz.py) and
    imported, not duplicated, by export.py -- so graph.html's legend and
    manifest.json's node_types/edge_types describe the schema identically.

Alerts 792/793 (npm/bin/repo2graph-mcp.js, .github/scripts/prod-igy.js) and
issues #370/#371/#376 are JavaScript or already covered by
tests/test_viz_safety.py, tests/test_repo2graph.py and tests/test_encoding.py
respectively -- nothing new to pin for those here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import repo2graph
from repo2graph import export, viz
from repo2graph.graph import Graph

REPO_ROOT = Path(__file__).resolve().parent.parent


def _declared_pyproject_version() -> str:
    content = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf8")
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', content)
    assert m is not None
    return m.group(1)


def _static_literal_version() -> str:
    """The one quoted `__version__` literal in repo2graph/__init__.py.

    Read, never hardcoded: that literal is a release surface
    (scripts/version_surfaces.py rewrites it on every bump), so a copy of it
    spelled out in a test is red from the moment the bump commit lands --
    inside publish.yml's `pypi` job, which runs the suite against the already
    tagged release commit. Spelling it `"2.2.0"` failed the 3.0.0 release
    exactly there, after the tag was cut and the bump PR merged.
    """
    content = (REPO_ROOT / "repo2graph" / "__init__.py").read_text(encoding="utf8")
    m = re.search(r'(?m)^__version__\s*=\s*"([^"]+)"', content)
    assert m is not None, "no quoted __version__ literal in repo2graph/__init__.py"
    return m.group(1)


# ---------------------------------------------------------------------------
# #340 -- source checkout must be authoritative over a stale installed
# dist-info; the installed-package path must still work when there is no
# local checkout to read from.
# ---------------------------------------------------------------------------


def test_version_matches_pyproject_in_this_checkout():
    """Sanity: importing repo2graph from this tree reports pyproject.toml's
    own version, not whatever happens to be registered as installed."""
    assert repo2graph.__version__ == _declared_pyproject_version()


def test_version_resolves_from_source_checkout_over_stale_dist_info(monkeypatch):
    """The bug: a stale `pip install repo2graph==1.6.0` elsewhere on this
    interpreter's path must not shadow the real version of a source checkout.

    Detector proof: reverting repo2graph/__init__.py to
    `importlib.metadata.version("repo2graph")` with no checkout-first check
    makes this fail (it returns the faked "1.6.0" instead of the real
    version) -- confirmed by hand before landing this test.
    """
    monkeypatch.setattr(repo2graph.importlib.metadata, "version", lambda name: "1.6.0")
    resolved = repo2graph._resolve_version()
    assert resolved == _declared_pyproject_version()
    assert resolved != "1.6.0"


def test_version_falls_back_to_dist_info_outside_a_checkout(monkeypatch):
    """No local pyproject.toml to read (a real installed package, e.g. a
    wheel with no source tree alongside it) -- dist-info must still answer,
    which is the "keep the installed-package path correct" half of #340."""
    monkeypatch.setattr(repo2graph, "_pyproject_version", lambda pyproject: None)
    monkeypatch.setattr(repo2graph.importlib.metadata, "version", lambda name: "9.9.9")
    assert repo2graph._resolve_version() == "9.9.9"


def test_version_falls_back_to_static_literal_when_nothing_resolves(monkeypatch):
    """Neither a checkout's pyproject.toml nor dist-info answers (e.g. a
    frozen/bundled build) -- the static literal at the top of __init__.py is
    the last resort, never an exception escaping import."""
    monkeypatch.setattr(repo2graph, "_pyproject_version", lambda pyproject: None)

    def _raise(name):
        raise repo2graph.importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(repo2graph.importlib.metadata, "version", _raise)
    literal = _static_literal_version()
    # Both halves matter: the literal is what answers, and the bump keeps it
    # equal to pyproject's version (scripts/check_version.py is the other side
    # of that), so neither assertion carries a version number of its own.
    assert repo2graph._resolve_version() == literal
    assert literal == _declared_pyproject_version()


def test_pyproject_version_ignores_a_pyproject_naming_a_different_project(tmp_path):
    """A pyproject.toml that exists but does not name this project (e.g. this
    file happened to be vendored into some other package's tree) must not be
    treated as this checkout's own version file."""
    other = tmp_path / "pyproject.toml"
    other.write_text('[project]\nname = "not-repo2graph"\nversion = "0.0.1"\n', encoding="utf8")
    assert repo2graph._pyproject_version(other) is None


def test_pyproject_version_reads_this_repos_own_file():
    v = repo2graph._pyproject_version(REPO_ROOT / "pyproject.toml")
    assert v == _declared_pyproject_version()


def test_doctor_drift_check_can_fire_now(monkeypatch):
    """doctor.py compares `repo2graph.__version__` (code_ver) against
    `importlib.metadata.version("repo2graph")` (dist_ver) to detect a stale
    install. Before #340's fix, __version__ *was* that same metadata call, so
    code_ver == dist_ver was true by construction and the check could never
    fire. It must be able to fire now."""
    monkeypatch.setattr(repo2graph.importlib.metadata, "version", lambda name: "1.6.0")
    code_ver = repo2graph.__version__
    dist_ver = repo2graph.importlib.metadata.version("repo2graph")
    assert code_ver != dist_ver


# ---------------------------------------------------------------------------
# #349 -- NODE_TYPES / EDGE_TYPES defined once, in viz.py, imported by
# export.py. Not two dicts that happen to agree today: the *same* dict.
# ---------------------------------------------------------------------------


def test_node_and_edge_types_are_the_same_object_in_both_modules():
    assert export.NODE_TYPES is viz.NODE_TYPES
    assert export.EDGE_TYPES is viz.EDGE_TYPES


def test_node_and_edge_types_cover_the_documented_schema():
    assert set(export.NODE_TYPES) == {"repo", "dir", "file", "symbol", "module", "external"}
    assert set(export.EDGE_TYPES) == {
        "CONTAINS",
        "DEFINES",
        "IMPORTS",
        "CALLS",
        "CALLS_EXTERNAL",
        "INHERITS",
        "CO_CHANGE",
    }


def test_legend_and_manifest_describe_types_identically(tmp_path):
    """The end-to-end promise #349 asks for: graph.html's legend and
    manifest.json's node_types/edge_types must say the same thing about the
    same type, because they now read from the one definition.

    Detector proof: before this fix, viz.py's NODE_TYPE_DESC/EDGE_TYPE_DESC
    used shorter, independently-worded copy (e.g. "the repository itself"
    vs. export.py's "the repository itself; one per index") -- this
    assertion would have failed against the old two-dict state.
    """
    g = Graph(tmp_path, "test_repo")
    g.add_node("file:a.py", type="file", path="a.py", lang="python")

    html_payload = viz.payload(g)

    manifest_path = tmp_path / "manifest.json"
    export.write_manifest(g, manifest_path, written=[])
    import json

    manifest = json.loads(manifest_path.read_text(encoding="utf8"))

    assert html_payload["nodeDesc"] == manifest["node_types"]
    assert html_payload["edgeDesc"] == manifest["edge_types"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
