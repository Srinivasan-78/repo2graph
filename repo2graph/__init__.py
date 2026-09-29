import importlib.metadata
import re
from pathlib import Path

from .graph import build, Graph
from .chunks import build_chunks, iter_chunks
from .viz import write_html

__all__ = ["build", "Graph", "build_chunks", "iter_chunks", "write_html"]

# Static fallback: kept in sync with pyproject.toml's [project] version by
# scripts/bump_version.py (see scripts/version_surfaces.py's SURFACES table)
# and checked on every commit by scripts/check_version.py. This is the only
# quoted __version__ literal in this file (tests/test_compat.py's R-9 asserts
# exactly that) -- _resolve_version() below never assigns a second one, only
# the result of a function call.
__version__ = "2.2.0"


def _pyproject_version(pyproject: Path) -> str | None:
    """`[project] version` read straight out of a pyproject.toml, or None.

    stdlib-only and tomllib-with-regex-fallback, matching the same pattern
    scripts/check_version.py and tests/test_version_surfaces.py already use:
    tomllib is 3.11+ and this project supports 3.10.
    """
    try:
        content = pyproject.read_text(encoding="utf8")
    except OSError:
        return None
    try:
        import tomllib
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]
    if tomllib is not None:
        try:
            data = tomllib.loads(content)
        except Exception:
            return None
        project = data.get("project") if isinstance(data, dict) else None
        if isinstance(project, dict) and project.get("name") == "repo2graph":
            v = project.get("version")
            return v if isinstance(v, str) else None
        return None
    if not re.search(r'(?m)^name\s*=\s*"repo2graph"', content):
        return None
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', content)
    return m.group(1) if m else None


def _resolve_version() -> str:
    """Source checkout beats a stale installed dist-info (issue #340).

    `importlib.metadata.version("repo2graph")` answers from whatever
    package this interpreter's environment happens to have registered -- a
    leftover `pip install repo2graph==1.6.0` elsewhere on the path silently
    shadows the real version of a source checkout being run directly
    (`python -m repo2graph.cli`, `pytest`, an editable checkout that ended
    up behind a stale dist-info). When this file's own directory sits
    inside a checkout with a top-level pyproject.toml that names this
    project, that pyproject.toml is unambiguous and wins. Otherwise this
    genuinely is an installed package -- including a normal `pip install
    -e .` whose dist-info pip itself keeps current -- so dist-info answers
    correctly, which is doctor.py's `code_ver != dist_ver` drift check's
    other half.
    """
    checkout_root = Path(__file__).resolve().parent.parent
    v = _pyproject_version(checkout_root / "pyproject.toml")
    if v is not None:
        return v
    try:
        return importlib.metadata.version("repo2graph")
    except Exception:
        return __version__


__version__ = _resolve_version()
