"""The HTTP MCP transport and the OIDC/JWT auth engine stay removed (`647e76f3`).

`repo2graph/http_server.py` and `repo2graph/auth.py` -- roughly 4,000 lines of
hand-rolled RSA/JWT verification, JWKS refresh, forwarded-header trust and rate
limiting -- were deleted and `repo2graph-mcp` became stdio-only. CHANGELOG.md's
`### Removed` section promises that "passing any removed flag now fails as an
unknown argument", and that promise is the dangerous half: someone who still has
`--auth-token ...` in their client config must be told it does nothing, not be
left believing their server is authenticated when it is wide open.

Nothing else in tests/ named any of these flags, so three regressions were
invisible to CI: a module re-appearing, an argparse option silently swallowing a
removed flag, and a web framework or JWT library creeping back into the package.
Each test below detects exactly one of them.
"""

import argparse
import ast
import importlib
import importlib.util
from pathlib import Path

import pytest

import repo2graph.mcp
from repo2graph.cli import main as cli_main
from repo2graph.mcp.server import main as mcp_main

PACKAGE_DIR = Path(importlib.import_module("repo2graph").__file__).resolve().parent

# Pinned verbatim from CHANGELOG.md's `### Removed` entry for 647e76f3. Written
# out rather than parsed back out of the changelog so that editing the changelog
# cannot quietly shrink what this file checks.
REMOVED_FLAGS = (
    "--http-only",
    "--http-host",
    "--http-port",
    "--http-allow-hosts",
    "--well-known-port",
    "--auth-token",
    "--auth-oidc-issuer",
    "--auth-audience",
    "--auth-jwks-ttl",
    "--auth-cimd",
)

# HTTP server frameworks and JWT/JOSE implementations. `http.server` is in the
# list because it is the dependency-free way to put the transport back, and a
# re-added HTTP listener that needs no new requirement is the one that would
# slip through a dependency review.
FORBIDDEN_IMPORTS = (
    "uvicorn",
    "fastapi",
    "starlette",
    "aiohttp",
    "flask",
    "jwt",
    "jwcrypto",
    "authlib",
    "jose",
    "http.server",
)

REMOVED_MODULES = ("repo2graph.auth", "repo2graph.http_server")


class _ParserReached(Exception):
    """Sentinel raised in place of `parse_args`, to stop an entrypoint at its parser."""


def _registered_options(main_fn, monkeypatch) -> set[str]:
    """Every option string the entrypoint's parser and its subparsers accept.

    An exit status alone is not a sufficient check here and a first draft of
    this file was fooled by exactly that: a re-added `--http-port` that takes a
    value still makes `main(["--http-port"])` exit 2, because argparse now
    complains that the argument is missing rather than that the option is
    unknown. Reading the registered options instead asks the question directly
    -- is this flag back? -- and cannot be satisfied by a different error.

    The parser is built inside `main`, so it is captured by standing in for
    `parse_args` and raising before any work happens; nothing is served, built
    or written. Subparsers are walked too, since the CLI registers its options
    on seventeen subcommands rather than at top level.
    """
    captured: list[argparse.ArgumentParser] = []

    def _capture(self, args=None, namespace=None):
        captured.append(self)
        raise _ParserReached

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", _capture)
    with pytest.raises(_ParserReached):
        main_fn([])
    monkeypatch.undo()

    options: set[str] = set()
    pending = list(captured)
    seen: set[int] = set()
    while pending:
        parser = pending.pop()
        if id(parser) in seen:
            continue
        seen.add(id(parser))
        for action in parser._actions:
            options.update(action.option_strings)
            choices = getattr(action, "choices", None)
            if isinstance(choices, dict):
                pending.extend(
                    sub for sub in choices.values() if isinstance(sub, argparse.ArgumentParser)
                )
    return options


def _imported_modules(path: Path) -> set[str]:
    """Every module name `path` actually imports, from its AST.

    Over `ast.Import` / `ast.ImportFrom` rather than a substring scan, because
    `answer.py` and `security.py` both contain "http" and "jwt" in contexts that
    have nothing to do with importing one: URL handling in the former, and a
    literal list of secret-bearing key names in the latter (`security.py` has
    `"jwt"` as a credential pattern). A grep for those words over the package
    reports both files and tells you nothing.

    Relative imports are skipped -- `from .auth import ...` cannot name a
    third-party library, and the modules test below is what guards those.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


def test_auth_and_http_server_modules_are_gone_from_disk():
    """The two deleted files must not come back under their old names.

    A restored `auth.py` or `http_server.py` would be dead code at best -- no
    caller is left -- and at worst an unreviewed resurrection of the crypto the
    removal existed to delete. Checked on disk as well as through the import
    system so that a file present but never imported still fails.
    """
    for name in ("auth.py", "http_server.py"):
        assert not (PACKAGE_DIR / name).exists(), (
            f"repo2graph/{name} is back; it was deleted in 647e76f3 and the "
            f"HTTP/OIDC surface it implemented is not supported."
        )


@pytest.mark.parametrize("module", REMOVED_MODULES)
def test_removed_modules_are_not_importable(module):
    """`import repo2graph.auth` must still fail.

    Separate from the on-disk check: a module can be re-introduced as a package
    directory, or re-exported from somewhere else under the old name, and either
    would make the import succeed while no `auth.py` file exists.
    """
    assert importlib.util.find_spec(module) is None, f"{module} resolves to a module again"

    with pytest.raises(ImportError):
        importlib.import_module(module)


def test_mcp_entrypoint_registers_no_removed_flag(monkeypatch):
    """`repo2graph-mcp` must not have any of the ten flags back on its parser.

    This is the regression that matters most, and the one an exit status alone
    cannot see. A config left over from 2.2.0 says `--auth-token hunter2`; if
    the parser ever accepts it again -- even as a deprecated no-op -- the user
    is told the server started and is never told their bearer token guards
    nothing. stdio inherits the parent process's identity, so there is no
    meaning left to give any of these.
    """
    options = _registered_options(mcp_main, monkeypatch)
    back = sorted(set(REMOVED_FLAGS) & options)
    assert not back, (
        f"repo2graph-mcp accepts {', '.join(back)} again; the HTTP transport "
        f"and OIDC auth were removed in 647e76f3 and stdio has nothing to "
        f"configure with them."
    )


def test_cli_registers_no_removed_flag(monkeypatch):
    """Nor may the `repo2graph` CLI, on any of its subcommands.

    The CLI never served MCP over HTTP, but it is the binary users reach for
    first and the obvious place to retry a flag that `repo2graph-mcp` just
    refused. Checked across every subparser rather than at top level only,
    because `build` and `github` are where a plausible-looking `--auth-token`
    would be added.
    """
    options = _registered_options(cli_main, monkeypatch)
    back = sorted(set(REMOVED_FLAGS) & options)
    assert not back, f"the repo2graph CLI accepts {', '.join(back)}, which was removed in 647e76f3"


@pytest.mark.parametrize("flag", REMOVED_FLAGS)
def test_mcp_entrypoint_exits_nonzero_on_a_removed_flag(flag, tmp_path, monkeypatch):
    """Passing a removed flag must fail the process, not be quietly dropped.

    The companion to the registration check: that one proves the option is not
    declared, this one proves the end-to-end behaviour a 2.2.0 user actually
    hits. Exit 2 is argparse's own code for an unrecognised argument, so the
    status is asserted rather than the wording, which argparse is free to
    reword between Python versions. `serve` is stubbed so that an accepted flag
    shows up as a recorded call instead of a server blocking on stdin forever.
    """
    served = []
    monkeypatch.setattr(repo2graph.mcp, "serve", lambda *a, **kw: served.append(a))

    with pytest.raises(SystemExit) as exc:
        mcp_main([str(tmp_path), flag, "1"])

    assert exc.value.code == 2, f"repo2graph-mcp did not reject {flag} as an unknown argument"
    assert not served, f"repo2graph-mcp started a server despite being given {flag}"


@pytest.mark.parametrize("flag", REMOVED_FLAGS)
def test_cli_exits_nonzero_on_a_removed_flag(flag):
    """The CLI fails the same way, so neither binary silently accepts one."""
    with pytest.raises(SystemExit) as exc:
        cli_main([flag, "1"])
    assert exc.value.code == 2, f"repo2graph did not reject {flag} as an unknown argument"


def test_no_module_imports_an_http_framework_or_jwt_library():
    """No file under `repo2graph/` may import a web framework or a JWT library.

    The flag checks above only cover the two parsers. This covers the shape of
    the regression that does not go through a flag at all: a new module that
    imports `fastapi` or `jwt` and brings the transport back beside the stdio
    server rather than in place of it. It also keeps the README's and
    SECURITY.md's "no network calls" claim honest at the dependency level.
    """
    offenders = []
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        for imported in _imported_modules(path):
            for forbidden in FORBIDDEN_IMPORTS:
                if imported == forbidden or imported.startswith(forbidden + "."):
                    offenders.append(f"{path.relative_to(PACKAGE_DIR)} imports {imported}")

    assert not offenders, (
        "repo2graph must not depend on an HTTP server framework or a JWT "
        "implementation; the HTTP transport and OIDC auth were removed in "
        "647e76f3: " + "; ".join(sorted(offenders))
    )
