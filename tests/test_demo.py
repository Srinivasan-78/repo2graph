"""Tests for the bundled demo repository and the five starter questions.

The assertions here are hand-derived from the fixture source in
`repo2graph/demo.py` -- never from a value the code under test computed. A
test that compared the demo's output to `Index(...).pack_context(...)` would
move with any traversal change and stay green straight through a regression
that made every starter prompt return nothing (AGENTS.md, "Tests must pin
values, not compare the implementation to itself").
"""

import json

import pytest

from repo2graph.cli import main
from repo2graph.demo import (
    DEMO_FILES,
    STARTER_QUESTIONS,
    build_demo_index,
    materialize,
    run_demo,
)


@pytest.fixture(scope="module")
def demo_index(tmp_path_factory):
    """One materialised + built demo repo, shared by the read-only tests."""
    root = tmp_path_factory.mktemp("demo-repo")
    materialize(root)
    stats = build_demo_index(root, root / ".r2g")
    return root, root / ".r2g", stats


def _records(path):
    return [json.loads(ln) for ln in path.read_text(encoding="utf8").splitlines() if ln.strip()]


def test_materialize_writes_every_bundled_file(tmp_path):
    materialize(tmp_path)
    for rel in DEMO_FILES:
        assert (tmp_path / rel).is_file(), f"{rel} was not written"
    # LF on every platform: the fixture's bytes are the graph's input, and a
    # CRLF checkout would give Windows a different node/edge count from CI.
    assert b"\r\n" not in (tmp_path / "app" / "auth.py").read_bytes()


def test_every_fixture_module_carries_a_file_chunk(demo_index):
    """chunks.py drops a `file_residual` under 40 chars, and a node with no
    chunk can be neither a seed nor a retrievable neighbour.

    Verified as a detector against a module with no module-level text at all
    (`def a(): return 1`), which is the condition that actually trips the
    40-char floor -- a module keeping only its imports stays above it. So the
    thing this guards is a fixture module stripped of its docstring *and* its
    module-level constant, which is exactly how the residue rule gets broken
    by someone tidying up.

    See AGENTS.md, "A file with little residue emits no file-level chunk".
    """
    _root, out, _stats = demo_index
    chunks = _records(out / "agent" / "chunks.jsonl")
    # "file" is a whole small file; "file_residual" is what is left of a code
    # file once every symbol span is carved out. Either one means the file
    # node is reachable; neither means it is not.
    with_file_chunk = {c["path"] for c in chunks if c.get("type") in ("file", "file_residual")}
    for rel in DEMO_FILES:
        assert rel in with_file_chunk, f"{rel} has a node but no file-level chunk"


def test_demo_graph_has_the_edges_the_starter_questions_rely_on(demo_index):
    """Hand-derived from the fixture source: routes guard -> service -> store.

    Each of these is the edge one starter question exists to demonstrate, so
    losing any of them breaks the quickstart, not just this test.
    """
    _root, out, _stats = demo_index
    edges = {(e["src"], e["type"], e["dst"]) for e in _records(out / "agent" / "edges.jsonl")}

    # Q1: the guard is enforced at every write route, not just one.
    for handler in ("create_order", "get_order", "refund_order"):
        assert (
            f"sym:app/routes.py::{handler}",
            "CALLS",
            "sym:app/auth.py::require_token",
        ) in edges

    # Q2: place_order has a caller in the routes and one in the tests.
    assert (
        "sym:app/routes.py::create_order",
        "CALLS",
        "sym:app/service.py::OrderService.place_order",
    ) in edges
    assert (
        "sym:tests/test_orders.py::test_place_order_persists_through_the_store",
        "CALLS",
        "sym:app/service.py::OrderService.place_order",
    ) in edges

    # Q3: the test module reaches the code under test by IMPORTS.
    assert ("file:tests/test_orders.py", "IMPORTS", "file:app/service.py") in edges

    # Q4: the blast radius of OrderStore.insert is a real CALLS edge in.
    assert (
        "sym:app/service.py::OrderService.place_order",
        "CALLS",
        "sym:app/store.py::OrderStore.insert",
    ) in edges

    # Q5: the whole route-to-persistence path, one IMPORTS hop at a time.
    assert ("file:app/routes.py", "IMPORTS", "file:app/service.py") in edges
    assert ("file:app/service.py", "IMPORTS", "file:app/store.py") in edges


def test_demo_has_no_cross_language_call_edges(demo_index):
    """The JS client must not resolve into the Python store.

    `web/client.js` calls the browser global `fetch()`; when the Python store
    had a method of the same name, the global-name fallback produced a
    `web/client.js::createOrder -> app/store.py::OrderStore.fetch` edge. That
    is a real property of the resolver, but a first-run demo is the wrong
    place to display one -- the fixture is named to avoid the collision, and
    this pins it so a future rename cannot quietly bring it back.
    """
    _root, out, _stats = demo_index
    for e in _records(out / "agent" / "edges.jsonl"):
        if e["type"] != "CALLS":
            continue
        if e["src"].startswith("sym:web/"):
            assert e["dst"].startswith("sym:web/"), f"cross-language edge: {e}"


# (question index, a path the answer is worthless without). Hand-derived: each
# is the file that actually holds the answer to that starter question in the
# fixture, read off the source, not off a previous run's output.
_MUST_CITE = [
    (0, "app/auth.py"),
    (1, "app/service.py"),
    (2, "tests/test_orders.py"),
    (3, "app/store.py"),
    (4, "app/routes.py"),
    (4, "app/store.py"),
]


@pytest.mark.parametrize("q_index,required_path", _MUST_CITE)
def test_each_starter_question_cites_the_file_that_answers_it(demo_index, q_index, required_path):
    """The quickstart promises these five prompts work. This is that promise.

    Set membership on paths only -- no score, no rank, no ordering, which all
    drift with any scoring tweak (AGENTS.md).
    """
    from repo2graph.demo import DEMO_BUDGET_CHARS, DEMO_HOPS, DEMO_K
    from repo2graph.query import Index

    _root, out, _stats = demo_index
    question = STARTER_QUESTIONS[q_index]
    pack = Index(out).pack_context(
        question.demo,
        k=DEMO_K,
        hops=DEMO_HOPS,
        budget_chars=DEMO_BUDGET_CHARS,
        exclude_secrets=True,
    )
    cited = {c.get("path") for c in pack["chunks"]}
    assert required_path in cited, (
        f"starter question {q_index + 1} ({question.demo!r}) did not cite "
        f"{required_path}; got {sorted(p for p in cited if p)}"
    )


def test_starter_questions_are_five_and_fully_populated():
    assert len(STARTER_QUESTIONS) == 5
    for q in STARTER_QUESTIONS:
        assert q.template and q.demo and q.shows
        # The template is the copy-paste form for the reader's own repo; the
        # demo form is what actually runs. Only the generic ones carry a
        # placeholder, and a placeholder must never survive into the demo form.
        assert "<" not in q.demo, f"{q.demo!r} still has a placeholder"


def test_run_demo_reports_every_question_and_cleans_up(tmp_path):
    lines: list[str] = []
    report = run_demo(emit=lines.append)

    assert len(report["questions"]) == 5
    for record in report["questions"]:
        assert record["cited_paths"], f"{record['query']!r} cited nothing"
        assert record["used_chars"] > 0

    from pathlib import Path

    assert not Path(report["repo"]).exists(), "the ephemeral demo repo was not removed"

    out = "\n".join(lines)
    for q in STARTER_QUESTIONS:
        assert q.demo in out


def test_run_demo_keeps_the_repo_when_asked(tmp_path):
    target = tmp_path / "kept"
    report = run_demo(outdir=target, emit=lambda _s: None)
    assert target.is_dir()
    assert (target / "app" / "auth.py").is_file()
    assert (target / ".r2g" / "agent" / "chunks.jsonl").is_file()
    assert report["index"] == str(target / ".r2g")


def test_demo_cli_exits_zero(tmp_path, capsys):
    assert main(["demo", "--out", str(tmp_path / "cli-demo")]) == 0
    captured = capsys.readouterr().out
    # The brief renderer must print citations, not five copies of the repo
    # map: `pack_context` prepends the same map to every pack, so truncating
    # the markdown head would show no evidence at all.
    assert "cited blocks" in captured
    assert "[cite:" in captured


def test_bare_repo2graph_lists_every_subcommand(capsys):
    """`repo2graph` with no arguments is the first thing a new user types.

    It used to return from `main()` before a single `sub.add_parser(...)` ran,
    so that help page listed no commands at all -- "positional arguments: {}"
    -- while `repo2graph --help` listed all of them. Asserting on `demo` and
    `build` alone would not have caught it either, since both are absent from
    the broken page *and* the empty one; what pins it is that the two pages
    agree.
    """
    assert main([]) == 0
    bare = capsys.readouterr().out

    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    explicit = capsys.readouterr().out

    assert bare == explicit, "`repo2graph` and `repo2graph --help` print different help"
    for command in ("demo", "build", "rag", "doctor"):
        assert command in bare, f"`repo2graph` help does not list '{command}'"
    # The no-args page is the one place to point a first run at the demo.
    assert "repo2graph demo" in bare


def test_demo_cli_reports_an_unwritable_target_without_a_traceback(tmp_path, monkeypatch):
    """The one first-run failure that actually happens: nowhere to write."""
    import repo2graph.demo as demo_mod

    def _boom(*_a, **_k):
        raise OSError("Read-only file system")

    monkeypatch.setattr(demo_mod, "materialize", _boom)
    with pytest.raises(SystemExit) as exc:
        main(["demo", "--out", str(tmp_path / "nope")])
    message = str(exc.value)
    assert "Read-only file system" in message
    assert "--out" in message
