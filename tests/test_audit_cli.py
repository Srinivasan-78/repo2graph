"""Regression tests for the round-1 CLI/MCP audit findings."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from repo2graph import mcp as mcp_mod
from repo2graph.cli import main
from repo2graph.query import Index

SRC = 'def greet(name):\n    """Say hello."""\n    return "hello " + name\n\n\nTABLE = {"a": 1, "b": 2, "c": 3, "d": 4}\n'


def _nodes(out: Path) -> list[dict]:
    with open(out / "agent" / "nodes.jsonl", encoding="utf8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
    )


def _make_repo(tmp_path: Path, git: bool) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(SRC, encoding="utf8")
    if git:
        _git(repo, "init", "-q")
        _git(repo, "add", "app.py")
        _git(repo, "commit", "-qm", "init")
    return repo


# ---------------------------------------------------------------- item 1


@pytest.mark.parametrize("git", [False, True], ids=["plain-dir", "git-no-gitignore"])
@pytest.mark.parametrize("incremental", [False, True], ids=["full", "incremental"])
def test_build_never_indexes_its_own_output(tmp_path, monkeypatch, capsys, git, incremental):
    repo = _make_repo(tmp_path, git)
    monkeypatch.chdir(repo)
    extra = ["--incremental"] if incremental else []
    for _ in range(2):
        main(["build", ".", "-o", ".r2g", "--formats", "jsonl,overview", *extra])
    capsys.readouterr()
    paths = [n.get("path") or "" for n in _nodes(repo / ".r2g")]
    assert "app.py" in paths
    assert not [p for p in paths if p.startswith(".r2g")], paths
    chunks = (repo / ".r2g" / "agent" / "chunks.jsonl").read_text(encoding="utf8")
    assert ".r2g/" not in chunks


def test_previous_index_elsewhere_in_tree_is_skipped(tmp_path, capsys):
    """An index left by an earlier build under another -o is recognised by its marker."""
    repo = _make_repo(tmp_path, git=False)
    main(["build", str(repo), "-o", str(repo / "old_index")])
    main(["build", str(repo), "-o", str(tmp_path / "out")])
    capsys.readouterr()
    paths = [n.get("path") or "" for n in _nodes(tmp_path / "out")]
    assert "app.py" in paths
    assert not [p for p in paths if p.startswith("old_index")], paths


def test_mcp_auto_build_skips_output_dir(tmp_path):
    from repo2graph.mcp import _build_index

    repo = _make_repo(tmp_path, git=True)
    out = repo / ".repo2graph"
    _build_index(repo, out)
    (out / "stray.py").write_text(SRC, encoding="utf8")  # even without a manifest match
    _build_index(repo, out)
    paths = [n.get("path") or "" for n in _nodes(out)]
    assert not [p for p in paths if p.startswith(".repo2graph")], paths


# ---------------------------------------------------------------- items 2 & 3

CHANGED = SRC.replace('return "hello " + name', 'return "hi " + name.upper()')


@pytest.fixture
def git_index(tmp_path, capsys):
    """A git repo on `main`, indexed into a directory *outside* the repo."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(SRC, encoding="utf8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "init")
    out = tmp_path / "idx"
    main(["build", str(repo), "-o", str(out)])
    capsys.readouterr()
    return repo, out


def test_repo_impact_default_head_sees_uncommitted_changes(git_index):
    repo, out = git_index
    (repo / "app.py").write_text(CHANGED, encoding="utf8")  # not committed
    idx = Index(out)
    # The test process's cwd is some other git checkout: the root must come
    # from the index (manifest's source_root), never Path.cwd().
    text = mcp_mod.dispatch(idx, "repo_impact", {"format": "json"})
    assert not isinstance(text, mcp_mod.ToolError), text
    report = json.loads(text)
    assert "app.py" in json.dumps(report["files_changed"]), report


def test_repo_impact_uses_the_servers_repo_root(git_index, monkeypatch, tmp_path):
    repo, out = git_index
    (repo / "app.py").write_text(CHANGED, encoding="utf8")
    idx = Index(out)
    idx.repo_root = repo  # what open_index(out, repo=...) sets
    monkeypatch.chdir(tmp_path)  # not a git repo
    report = json.loads(mcp_mod.tool_repo_impact(idx, format="json"))
    assert "app.py" in json.dumps(report["files_changed"]), report


def test_repo_impact_git_failure_is_an_error_with_gits_reason(git_index):
    repo, out = git_index
    text = mcp_mod.dispatch(Index(out), "repo_impact", {"base": "no-such-branch"})
    assert isinstance(text, mcp_mod.ToolError)
    assert "no-such-branch" in text
    # git's own words, not just an exception type name
    low = text.lower()
    assert "unknown revision" in low or "bad revision" in low or "ambiguous" in low, text
    assert str(repo) not in text and repo.as_posix() not in text


@pytest.mark.parametrize(
    "name,args",
    [
        ("repo_search", {}),
        ("repo_search", {"query": "   "}),
        ("repo_neighbours", {"node_id": "sym:nope.py::missing"}),
        ("repo_neighbours", {}),
        ("repo_nonexistent", {}),
    ],
)
def test_invalid_tool_calls_are_tool_errors(git_index, name, args):
    _repo, out = git_index
    text = mcp_mod.dispatch(Index(out), name, args)
    assert isinstance(text, mcp_mod.ToolError), text
    assert text.strip()


def test_valid_tool_calls_are_not_errors(git_index):
    _repo, out = git_index
    idx = Index(out)
    for name, args in (("repo_search", {"query": "greet"}), ("repo_map", {})):
        assert not isinstance(mcp_mod.dispatch(idx, name, args), mcp_mod.ToolError)


@pytest.mark.parametrize("budget", [0, -1, -(10**9)])
def test_non_positive_budget_uses_the_default_and_says_so(git_index, budget):
    _repo, out = git_index
    text = mcp_mod.tool_repo_search(Index(out), "greet hello", budget_tokens=budget)
    assert "no content fit" not in text
    assert f"budget_tokens={budget}" in text and str(mcp_mod.MCP_BUDGET_TOKENS) in text
    assert "[cite:" in text  # a real answer, at the default budget


def test_negative_neighbour_limit_clamps_to_the_documented_minimum(git_index):
    _repo, out = git_index
    idx = Index(out)
    idx.expand = lambda seeds, **kw: [  # type: ignore[method-assign]
        (f"sym:flood.py::n{i}", "CALLS", "out", seeds[0]) for i in range(50)
    ]
    node = next(n for n in idx.nodes if n.startswith("file:"))
    for limit in (-3, 0, 1):
        text = mcp_mod.tool_repo_neighbours(idx, node, limit=limit)
        rows = [ln for ln in text.split("\n") if ln.startswith("- ")]
        assert len(rows) == 1, (limit, text)


class _Types:
    """Just enough of `mcp.types` for serve()."""

    class TextContent:
        def __init__(self, type, text):
            self.type, self.text = type, text

    class CallToolResult:
        def __init__(self, content, isError=False):
            self.content, self.isError = content, isError

    class ListToolsResult:
        def __init__(self, tools):
            self.tools = tools

    class Params:
        def __init__(self, name, arguments):
            self.name, self.arguments = name, arguments

    class ToolAnnotations:
        def __init__(self, **fields):
            for key, value in fields.items():
                setattr(self, key, value)

    class Tool:
        # Declared at class level on purpose: `get_tools` decides whether this
        # SDK supports annotations with `hasattr(tool_cls, "annotations")`, and
        # an attribute only ever set in __init__ is invisible to that check --
        # the tools would come back silently unannotated.
        annotations = None

        def __init__(self, name, description=None, inputSchema=None, annotations=None):
            self.name = name
            self.description = description
            self.inputSchema = inputSchema
            self.annotations = annotations


def _install_fake_sdk(monkeypatch):
    """A stand-in `mcp` package exposing only the 2.x registration API.

    legacy server cleanup/#291: there used to be a `Server1x` here too, driven by a
    `generation` parameter, because `serve()` branched on
    `hasattr(Server, "list_tools")` to speak either SDK generation. 1.x is no
    longer supported -- it deadlocks on the first tool call -- so that branch
    is gone and a 1.x-shaped fake would only assert that dead code still
    exists. The guard that actually refuses a 1.x install reads the installed
    distribution's version, not the module's shape, so it cannot be exercised
    by a fake at all; `tests/test_mcp.py::test_iss407_a_1x_sdk_is_refused_at_startup_not_hung`
    covers it against a patched version instead.
    """
    import types as pytypes

    captured: dict = {}

    class Server2x:
        def __init__(self, name, version=None, on_list_tools=None, on_call_tool=None):
            captured["list"], captured["call"] = on_list_tools, on_call_tool

        def create_initialization_options(self):
            return None

        async def run(self, *a):
            return None

    class _Stdio:
        async def __aenter__(self):
            return (None, None)

        async def __aexit__(self, *a):
            return False

    types_mod = pytypes.ModuleType("mcp.types")
    for attr in ("TextContent", "CallToolResult", "ListToolsResult", "Tool", "ToolAnnotations"):
        setattr(types_mod, attr, getattr(_Types, attr))
    root = pytypes.ModuleType("mcp")
    root.types = types_mod  # type: ignore[attr-defined]
    server_mod = pytypes.ModuleType("mcp.server")
    server_mod.Server = Server2x  # type: ignore[attr-defined]
    stdio_mod = pytypes.ModuleType("mcp.server.stdio")
    stdio_mod.stdio_server = lambda: _Stdio()  # type: ignore[attr-defined]
    for name, mod in (
        ("mcp", root),
        ("mcp.types", types_mod),
        ("mcp.server", server_mod),
        ("mcp.server.stdio", stdio_mod),
    ):
        monkeypatch.setitem(sys.modules, name, mod)
    return captured


def test_serve_list_tools_handler_reports_every_tool_and_honest_annotations(git_index, monkeypatch):
    """The registered list handler is what a real client reads the tool set
    and its annotations from, so it is the path #292's honesty has to hold on.

    `_install_fake_sdk` captured this callback but nothing ever invoked it,
    which is why the impact analysis flagged `serve.list_tools_handler` as a
    public API with no test caller -- correctly.
    """
    import asyncio

    _repo, out = git_index

    # No repo to build from: this server can only read an index that exists.
    captured = _install_fake_sdk(monkeypatch)
    mcp_mod.serve(out)
    tools = asyncio.run(captured["list"](None, None)).tools
    names = [t.name for t in tools]
    assert set(names) == set(mcp_mod.TOOL_DESCRIPTIONS)
    assert len(names) == len(set(names)), f"a tool is registered twice: {names}"
    assert all(_ann(t, "read_only_hint", "readOnlyHint") is True for t in tools)

    # Given a repo, the first call to any tool but repo_build_status may build.
    captured = _install_fake_sdk(monkeypatch)
    mcp_mod.serve(out, repo=_repo)
    by_name = {t.name: t for t in asyncio.run(captured["list"](None, None)).tools}
    assert _ann(by_name["repo_search"], "read_only_hint", "readOnlyHint") is False
    assert _ann(by_name["repo_build_status"], "read_only_hint", "readOnlyHint") is True


def _ann(tool, *names):
    """One annotation off a Tool, tolerating either SDK's field naming."""
    annotations = getattr(tool, "annotations", None)
    for name in names:
        if hasattr(annotations, name):
            return getattr(annotations, name)
        if isinstance(annotations, dict) and name in annotations:
            return annotations[name]
    raise AssertionError(f"no annotation named any of {names} on {tool.name}")


def test_serve_flags_tool_errors_as_is_error(git_index, monkeypatch):
    """A failing tool is reported as `isError`, never as a crashed server."""
    import asyncio

    _repo, out = git_index
    captured = _install_fake_sdk(monkeypatch)
    mcp_mod.serve(out)
    call = captured["call"]

    ok = asyncio.run(call(None, _Types.Params("repo_search", {"query": "greet"})))
    assert ok.isError is False and "[cite:" in ok.content[0].text
    bad = asyncio.run(call(None, _Types.Params("repo_neighbours", {"node_id": "sym:x::y"})))
    assert bad.isError is True and "node not found" in bad.content[0].text
    bad = asyncio.run(call(None, _Types.Params("repo_search", {})))
    assert bad.isError is True and "query" in bad.content[0].text


# ---------------------------------------------------------------- item 5


def test_index_outside_repo_is_fresh_right_after_build(git_index, capsys):
    from repo2graph.status import index_status

    repo, out = git_index
    assert index_status(out)["freshness"]["status"] == "current"
    main(["index-status", "-o", str(out), "--json"])
    assert json.loads(capsys.readouterr().out)["freshness"]["status"] == "current"
    # -r still overrides the recorded root.
    other = repo.parent / "other"
    other.mkdir()
    (other / "zzz.py").write_text(SRC, encoding="utf8")
    assert index_status(out, repo=other)["freshness"]["status"] == "stale"


def test_doctor_on_index_outside_repo_is_not_stale(git_index):
    from repo2graph.doctor import check_index_freshness

    _repo, out = git_index
    res = check_index_freshness(out)
    assert res.status == "ok" and "up to date" in res.summary, (res.summary, res.details)


# ---------------------------------------------------------------- item 4


@pytest.fixture
def secret_index(tmp_path, capsys):
    repo = _make_repo(tmp_path, git=False)
    (repo / ".env").write_text(
        "LEDGER_TOKEN=abc123deadbeef  # greet ledger token\n", encoding="utf8"
    )
    out = tmp_path / "idx"
    main(["build", str(repo), "-o", str(out), "--include-secrets"])
    capsys.readouterr()
    chunks = (out / "agent" / "chunks.jsonl").read_text(encoding="utf8")
    assert "abc123deadbeef" in chunks, "fixture: the build must have indexed .env"
    return out


@pytest.mark.parametrize("cmd", ["rag", "query"])
def test_query_time_secret_exclusion_is_the_default(secret_index, capsys, cmd):
    main([cmd, "ledger token greet", "-o", str(secret_index)])
    assert "abc123deadbeef" not in capsys.readouterr().out
    main([cmd, "ledger token greet", "-o", str(secret_index), "--include-secrets"])
    assert "abc123deadbeef" in capsys.readouterr().out


@pytest.mark.parametrize("cmd", ["rag", "query"])
def test_exclude_secrets_is_a_deprecated_noop(secret_index, capsys, cmd):
    main([cmd, "ledger token greet", "-o", str(secret_index), "--exclude-secrets"])
    captured = capsys.readouterr()
    assert "abc123deadbeef" not in captured.out
    assert "deprecated" in captured.err


def test_retrieve_python_api_default_is_unchanged(secret_index):
    """AGENTS.md: retrieve() is a back-compat surface; the new keyword defaults off."""
    idx = Index(secret_index)
    assert any(c["path"] == ".env" for c in idx.retrieve("ledger token"))
    assert not any(c["path"] == ".env" for c in idx.retrieve("ledger token", exclude_secrets=True))


# ---------------------------------------------------------------- item 6

JP = "こんにちは"


def test_piped_stdout_round_trips_non_ascii(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "hello.py").write_text(
        f'def konnichiwa():\n    """Say {JP} to the world."""\n    return "{JP}"\n\n\n'
        'TABLE = {"a": 1, "b": 2, "c": 3, "d": 4}\n',
        encoding="utf8",
    )
    out = tmp_path / "idx"
    main(["build", str(repo), "-o", str(out)])
    capsys.readouterr()
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    for cmd in ("rag", "query"):
        proc = subprocess.run(
            [sys.executable, "-m", "repo2graph.cli", cmd, "konnichiwa", "-o", str(out)],
            capture_output=True,
            env=env,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr
        assert JP in proc.stdout.decode("utf8"), proc.stdout[-400:]


def test_explicit_pythonioencoding_is_respected(tmp_path, capsys):
    """The Windows CP1252 CI job's contract: an explicit encoding wins, and still never crashes."""
    repo = _make_repo(tmp_path, git=False)
    (repo / "app.py").write_text(SRC + f"\n# {JP}\n", encoding="utf8")
    out = tmp_path / "idx"
    main(["build", str(repo), "-o", str(out)])
    capsys.readouterr()
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    env.pop("PYTHONUTF8", None)
    proc = subprocess.run(
        [sys.executable, "-m", "repo2graph.cli", "query", "greet", "-o", str(out)],
        capture_output=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip()


# ---------------------------------------------------------------- item 7


def test_min_confidence_aliases_parse(git_index, capsys):
    _repo, out = git_index
    main(["query", "greet", "-o", str(out), "--min-confidence", "0.5"])
    main(["rag", "greet", "-o", str(out), "--min-confidence", "0.5"])
    main(["explain", "retrieval", "greet", "-o", str(out), "--min-conf", "0.5", "--json"])
    assert capsys.readouterr().out.strip()


def test_explain_retrieval_default_k_matches_rag(git_index, monkeypatch, capsys):
    import repo2graph.explain as explain_mod

    seen = {}
    real = explain_mod.explain_retrieval

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr(explain_mod, "explain_retrieval", spy)
    _repo, out = git_index
    main(["explain", "retrieval", "greet", "-o", str(out), "--json"])
    capsys.readouterr()
    assert seen["k"] == 8


def test_impact_without_main_hints_at_base(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(SRC, encoding="utf8")
    _git(repo, "init", "-q", "-b", "trunk")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "init")
    with pytest.raises(SystemExit) as exc:
        main(["impact", str(repo), "-o", str(tmp_path / "idx")])
    assert "--base" in str(exc.value) and "hint" in str(exc.value)


def test_verify_rag_reports_extra_as_bool_without_vectors(git_index, capsys):
    _repo, out = git_index
    with pytest.raises(SystemExit):
        main(["embed", "-o", str(out), "--verify-rag"])
    report = json.loads(capsys.readouterr().out)
    assert report["rag_extra_installed"] in (True, False)


def test_doctor_names_files_with_parse_errors(tmp_path, capsys):
    from repo2graph.doctor import check_parsers

    repo = _make_repo(tmp_path, git=False)
    (repo / "broken.py").write_text("def broken(:\n    return ((\n", encoding="utf8")
    main(["build", str(repo), "-o", str(repo / ".r2g")])
    capsys.readouterr()
    res = check_parsers(repo)
    assert res.status in ("warn", "fail"), res
    assert any("broken.py" in d for d in res.details), res.details
