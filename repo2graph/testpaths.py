"""Which repo-relative paths are test code.

One predicate, two consumers: retrieval (`query.py`) demotes test chunks as
seeds, and the graph builder (`graph.py`) derives `TESTS` edges from the
symbols defined in test files. It lives in its own dependency-free module
because `query.py` imports `export.py`, and the builder must not pull the
retrieval layer in just to classify a path.
"""

from __future__ import annotations

__all__ = ["is_test_path"]

_TEST_DIR_NAMES = frozenset({"tests", "test", "__tests__", "spec", "specs"})
# Matched against the lowercased basename.
_TEST_BASENAME_SUFFIXES = (
    "_test.py",
    "_test.go",
    "_test.rb",
    "_spec.rb",
    ".test.ts",
    ".test.js",
    ".test.tsx",
    ".test.jsx",
    ".spec.ts",
    ".spec.js",
    ".spec.tsx",
    ".spec.jsx",
)
# Matched against the basename as written. JVM convention is a capitalised
# `FooTest.java`; lowercasing first would also claim `Latest.java` and
# `Contest.java`, which is the suffix bug this module exists to avoid.
_TEST_BASENAME_SUFFIXES_CASED = (
    "Test.java",
    "Tests.java",
    "Test.kt",
    "Tests.kt",
)


def is_test_path(path: str) -> bool:
    """Whether a repo-relative path is test code, matched per path component.

    `endswith("test.py")` is true of `latest.py`, `fastest.py` and
    `manifest.py`, so the check is on components and known suffixes -- the bug
    the original version of this predicate was written to fix, which is why the
    property test in `tests/test_properties.py` pins separator invariance.
    """
    raw = str(path or "").replace("\\", "/").split("/")
    parts = [p.lower() for p in raw]
    if any(p in _TEST_DIR_NAMES or p.startswith("test_") for p in parts[:-1]):
        return True
    base = parts[-1]
    return (
        base in ("test.py", "conftest.py")
        or base.startswith("test_")
        or base.endswith(_TEST_BASENAME_SUFFIXES)
        or raw[-1].endswith(_TEST_BASENAME_SUFFIXES_CASED)
    )
