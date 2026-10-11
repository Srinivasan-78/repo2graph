"""Build options read from the repository, so they need not be repeated (#391).

Two sources, most specific first: the `[tool.repo2graph]` table of the repo's
`pyproject.toml`, then the top-level keys of `.repo2graph.toml` in the repo
root. A command-line flag beats both; built-in defaults lose to all three.
Keys are spelled like the CLI flags they stand in for.

No config file means no change at all: `load` returns an empty result and every
caller falls through to exactly the values it used before.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

CONFIG_FILE = ".repo2graph.toml"
PYPROJECT = "pyproject.toml"
PYPROJECT_LABEL = f"{PYPROJECT} [tool.repo2graph]"

SECRET_POLICIES = ("redact-match", "exclude-file", "warn-only", "off")


class ConfigError(ValueError):
    """A config file that cannot be used; the message names the file and key."""


def _str_list(v: Any) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise TypeError("expected a list of strings")
    return list(v)


def _nonneg_int(v: Any) -> int:
    # bool is an int subclass: `git-history = true` is a mistake, not 1.
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise TypeError("expected an integer >= 0")
    return v


def _pos_int(v: Any) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise TypeError("expected an integer >= 1")
    return v


def _viz_nodes(v: Any) -> int | None:
    if isinstance(v, str) and v.strip().lower() == "all":
        return None
    try:
        return _nonneg_int(v)
    except TypeError:
        raise TypeError('expected an integer >= 0 or "all"') from None


def _max_file_mb(v: Any) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not v >= 0.1:
        raise TypeError("expected a number >= 0.1")
    return float(v)


def _bool(v: Any) -> bool:
    if not isinstance(v, bool):
        raise TypeError("expected true or false")
    return v


def _secret_policy(v: Any) -> str:
    if v not in SECRET_POLICIES:
        raise TypeError(f"expected one of {', '.join(SECRET_POLICIES)}")
    return str(v)


# key -> (argparse dest it stands in for, validator)
KEYS: dict[str, tuple[str, Callable[[Any], Any]]] = {
    "include": ("include", _str_list),
    "exclude": ("exclude", _str_list),
    "exclude-dir": ("extra_exclude_dirs", _str_list),
    "git-history": ("git_history", _nonneg_int),
    "git-authors": ("git_authors", _bool),
    "viz-nodes": ("viz_nodes", _viz_nodes),
    "max-call-candidates": ("max_call_candidates", _pos_int),
    "secret-policy": ("secret_policy", _secret_policy),
    "secret-keywords": ("extra_secret_keywords", _str_list),
    "secret-dirs": ("extra_secret_dirs", _str_list),
    "max-file-mb": ("max_file_mb", _max_file_mb),
    "chunk-large-files": ("chunk_large_files", _bool),
    "include-vendor": ("include_vendor", _bool),
    "reference-edges": ("reference_edges", _bool),
}


@dataclass
class RepoConfig:
    """Validated settings keyed by argparse dest, and where each one came from."""

    values: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.values)

    def label(self, dest: str) -> str | None:
        """`'<source> <key>'` for a dest the config set, e.g. for explain-path."""
        src = self.sources.get(dest)
        if src is None:
            return None
        key = next(k for k, (d, _) in KEYS.items() if d == dest)
        return f"{src} {key}"


def _toml_loads() -> Callable[[str], dict[str, Any]] | None:
    if sys.version_info >= (3, 11):
        import tomllib

        return tomllib.loads
    # Python 3.10: tomli is the same parser, but not a dependency of ours.
    try:
        import tomli
    except ImportError:
        return None
    loads: Callable[[str], dict[str, Any]] = tomli.loads
    return loads


def _validate(table: Any, where: str) -> dict[str, Any]:
    if not isinstance(table, dict):
        raise ConfigError(f"{where}: expected a table of settings")
    out: dict[str, Any] = {}
    for key, raw in table.items():
        if key not in KEYS:
            raise ConfigError(
                f"{where}: unknown key {key!r} (known keys: {', '.join(sorted(KEYS))})"
            )
        dest, check = KEYS[key]
        try:
            out[dest] = check(raw)
        except TypeError as exc:
            raise ConfigError(f"{where}: key {key!r}: {exc}, got {raw!r}") from None
    return out


def load(repo: Path | str) -> RepoConfig:
    """Read and validate the repo's config files.

    Raises:
        ConfigError: An unknown key, a mistyped value, or an unparseable
            `.repo2graph.toml`. An unparseable `pyproject.toml` is only noted:
            it belongs to the project, and indexing it did not need it before.
    """
    from .events import diagnostic

    root = Path(repo)
    own = root / CONFIG_FILE
    pyproject = root / PYPROJECT
    own_text = _read(own)
    py_text = _read(pyproject)
    # The cheap textual check keeps every repo without a table from paying
    # for (or, on 3.10 without tomli, being warned about) a TOML parse.
    if own_text is None and (py_text is None or "repo2graph" not in py_text):
        return RepoConfig()

    loads = _toml_loads()
    if loads is None:
        diagnostic(
            "note: repo2graph config file ignored: reading TOML on Python 3.10 "
            "needs `pip install tomli`"
        )
        return RepoConfig()

    cfg = RepoConfig()
    if own_text is not None:
        try:
            data = loads(own_text)
        except ValueError as exc:
            raise ConfigError(f"{own}: not valid TOML: {exc}") from None
        for dest, value in _validate(data, str(own)).items():
            cfg.values[dest] = value
            cfg.sources[dest] = CONFIG_FILE
    if py_text is not None and "repo2graph" in py_text:
        try:
            data = loads(py_text)
        except ValueError as exc:
            diagnostic(f"note: {pyproject} ignored for repo2graph settings: not valid TOML: {exc}")
            return cfg
        tool = data.get("tool")
        if isinstance(tool, dict) and "repo2graph" in tool:
            where = f"{pyproject} [tool.repo2graph]"
            for dest, value in _validate(tool["repo2graph"], where).items():
                cfg.values[dest] = value
                cfg.sources[dest] = PYPROJECT_LABEL
    return cfg


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf8") if path.is_file() else None
    except (OSError, UnicodeDecodeError):
        return None


_BUILD_CONFIG_DESTS = (
    "extra_exclude_dirs",
    "include_vendor",
    "chunk_large_files",
    "reference_edges",
    "secret_policy",
    "extra_secret_keywords",
    "extra_secret_dirs",
)
_BUILD_DESTS = ("include", "exclude", "git_history", "git_authors", "max_call_candidates")


def build_options(values: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split config values into `BuildConfig(...)` and `build(...)` keyword arguments.

    For the in-process builds that have no argparse namespace to merge into
    (`rag <src>`, the MCP auto-build). Only keys the config set appear, so an
    empty config leaves both calls exactly as they were.
    """
    config_kw = {d: values[d] for d in _BUILD_CONFIG_DESTS if d in values}
    if "max_file_mb" in values:
        config_kw["max_file_bytes"] = int(values["max_file_mb"] * 1_000_000)
    build_kw = {d: values[d] for d in _BUILD_DESTS if d in values}
    for d in ("include", "exclude"):
        if d in build_kw:
            build_kw[d] = build_kw[d] or None
    return config_kw, build_kw
