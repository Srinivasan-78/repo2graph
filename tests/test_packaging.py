"""The packaging facts that are invisible until someone installs the package.

Everything here is a static assertion about the source tree rather than a
wheel build: the suite runs in a uv-managed venv with no build backend
installed, and both of these break in the source tree first. CI's `packaging`
job builds the real wheel.
"""

from __future__ import annotations

import itertools

import pytest
from conftest import REPO_ROOT

PACKAGE = REPO_ROOT / "repo2graph"


def _pyproject() -> dict:
    """Parse pyproject.toml, or skip where the stdlib cannot.

    `tomllib` is 3.11+ and `requires-python` is `>=3.10`, so a module-level
    `import tomllib` made the whole file an import error on the declared floor
    -- collection failed before any test ran. The rest of the suite already
    handles this by importing inside the function and skipping (see
    `test_compat.py` and `test_grammar_availability.py`); adding `tomli` just
    for this would be a new dependency on the floor the package promises.
    """
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - only on 3.10
        pytest.skip("tomllib needs Python 3.11+")
    with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def test_the_py_typed_marker_exists():
    """PEP 561: without this file, inline annotations are invisible downstream.

    `[tool.mypy] strict = true` checks this package's own source. It says
    nothing about what a *consumer* of `docs/python-api.md`'s API sees, because
    a type checker ignores annotations in an installed package that ships no
    marker -- so every public function read as returning `Any` however strictly
    it was checked here.
    """
    marker = PACKAGE / "py.typed"
    assert marker.is_file(), "repo2graph/py.typed is missing; see PEP 561"
    # The marker is a flag, not a config file. A non-empty one is usually
    # someone's note, which `mypy` ignores and which misleads the next reader.
    assert marker.read_bytes() == b""


def test_py_typed_is_declared_as_package_data():
    """The marker has to be in the wheel, not merely in the repository.

    `[tool.setuptools.packages.find]` collects `.py` files only, so a `py.typed`
    sitting in the source tree is silently dropped from the built distribution
    without this declaration -- the failure mode is invisible locally and only
    shows up for someone who `pip install`s the package.
    """
    data = _pyproject()["tool"]["setuptools"]["package-data"]
    assert "py.typed" in data["repo2graph"]


def test_requires_python_matches_the_declared_classifiers():
    """A classifier promising a version `requires-python` excludes is a lie.

    Both are read by installers and by PyPI's own filtering, and they drift in
    opposite directions: a new interpreter gets added to the CI matrix and the
    classifiers, and the floor gets raised, and nothing cross-checks them.
    """
    project = _pyproject()["project"]
    floor = project["requires-python"]
    assert floor.startswith(">=")
    major, minor = (int(p) for p in floor.removeprefix(">=").split("."))

    declared = sorted(
        tuple(int(p) for p in c.rsplit(" :: ", 1)[1].split("."))
        for c in project["classifiers"]
        if c.startswith("Programming Language :: Python :: 3.")
    )
    assert declared, "no per-version Python classifiers declared"
    assert declared[0] == (major, minor), (
        f"lowest classifier {declared[0]} does not match requires-python {floor}"
    )
    # Contiguous: a gap means a version was dropped from one surface only.
    for earlier, later in itertools.pairwise(declared):
        assert later[1] == earlier[1] + 1, f"gap in Python classifiers: {earlier} -> {later}"
