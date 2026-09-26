"""Edge trust metadata, answer citations, and the bug-report bundle.

The assertions about *what an edge claims* are checked against the fixture
source: an evidence line is only worth having if opening that line shows the
relationship, so the tests read the fixture back and look.
"""

import json
import re
from pathlib import Path

import pytest

from repo2graph.bugreport import CATEGORIES, build_report, format_report
from repo2graph.cli import main
from repo2graph.edgemeta import (
    EDGE_SCHEMA_VERSION,
    METHOD_FILESYSTEM,
    METHOD_GIT_LOG,
    METHOD_NAME_RESOLVER,
    METHODS,
    cite,
    describe,
    evidence,
    file_of,
    normalize,
)
from repo2graph.limits import STANDING_LIMITS, confidence_report, render

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def indexed(tmp_path_factory):
    """A fixture repo whose every edge type has a known, checkable location."""
    src = tmp_path_factory.mktemp("schema") / "proj"
    (src / "app").mkdir(parents=True)
    (src / "app" / "base.py").write_text(
        "BASE = 'the base module, with module-level residue'\n"
        "\n"
        "\n"
        "class Handler:\n"
        "    def handle(self):\n"
        "        return 1\n",
        encoding="utf-8",
    )
    # Line numbers below are hand-counted from this text and asserted on.
    (src / "app" / "routes.py").write_text(
        "ROUTES = 'the routes module, with module-level residue'\n"  # 1
        "\n"  # 2
        "from .base import Handler\n"  # 3  <- IMPORTS evidence
        "\n"  # 4
        "\n"  # 5
        "class ApiHandler(Handler):\n"  # 6  <- INHERITS evidence
        "    def serve(self):\n"  # 7
        "        return helper()\n"  # 8  <- CALLS evidence
        "\n"  # 9
        "\n"  # 10
        "def helper():\n"  # 11  <- DEFINES evidence
        "    return len('x')\n",  # 12  <- CALLS_EXTERNAL evidence
        encoding="utf-8",
    )
    out = src / ".r2g"
    assert main(["build", str(src), "-o", str(out), "--formats", "jsonl"]) == 0
    edges = [
        json.loads(line)
        for line in (out / "agent" / "edges.jsonl").read_text(encoding="utf8").split("\n")
        if line.strip()
    ]
    return src, out, edges


def _find(edges, etype, src=None, dst=None):
    for e in edges:
        if e["type"] != etype:
            continue
        if src and e["src"] != src:
            continue
        if dst and e["dst"] != dst:
            continue
        return e
    raise AssertionError(f"no {etype} edge matching src={src} dst={dst}")


# --------------------------------------------------------------------------
# Every edge carries the standard fields
# --------------------------------------------------------------------------


def test_every_edge_has_type_method_confidence_and_evidence(indexed):
    """The failure this schema replaces: four of six types were bare triples."""
    _src, _out, edges = indexed
    assert edges
    for e in edges:
        for field in ("src", "dst", "type", "method", "confidence", "evidence"):
            assert field in e, f"{e['type']} edge is missing '{field}': {e}"
        assert isinstance(e["confidence"], (int, float))
        assert 0.0 <= e["confidence"] <= 1.0
        assert e["evidence"] is None or set(e["evidence"]) == {"path", "line"}


def test_every_edge_type_is_present_and_uses_a_known_method(indexed):
    _src, _out, edges = indexed
    seen = {e["type"] for e in edges}
    assert {"CONTAINS", "DEFINES", "IMPORTS", "CALLS", "CALLS_EXTERNAL", "INHERITS"} <= seen

    for e in edges:
        method = e["method"]
        base = method.split("/", 1)[0]
        assert base in METHODS, f"unknown extraction method {method!r}"


def test_field_order_is_stable(indexed):
    """`edges.jsonl` is a committable artifact whose diff humans read; a field
    that moves between lines makes every edge look changed."""
    _src, _out, edges = indexed
    for e in edges:
        keys = list(e)
        assert keys[:6] == ["src", "dst", "type", "method", "confidence", "evidence"]
        assert keys[6:] == sorted(keys[6:])


# --------------------------------------------------------------------------
# The evidence lines actually point at the relationship
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "etype,src_id,dst_id,expected_line,must_contain",
    [
        ("IMPORTS", "file:app/routes.py", "file:app/base.py", 3, "from .base import Handler"),
        (
            "INHERITS",
            "sym:app/routes.py::ApiHandler",
            "sym:app/base.py::Handler",
            6,
            "class ApiHandler(Handler):",
        ),
        (
            "CALLS",
            "sym:app/routes.py::ApiHandler.serve",
            "sym:app/routes.py::helper",
            8,
            "return helper()",
        ),
        ("DEFINES", "file:app/routes.py", "sym:app/routes.py::helper", 11, "def helper():"),
        (
            "CALLS_EXTERNAL",
            "sym:app/routes.py::helper",
            "external:len",
            12,
            "return len('x')",
        ),
    ],
)
def test_evidence_points_at_the_line_that_proves_the_edge(
    indexed, etype, src_id, dst_id, expected_line, must_contain
):
    """A citation is only worth having if opening it shows the relationship.

    Hand-derived from the fixture source above, then read back off disk --
    never taken from what the builder produced.
    """
    src, _out, edges = indexed
    e = _find(edges, etype, src=src_id, dst=dst_id)

    assert e["evidence"] is not None, f"{etype} edge has no evidence"
    assert e["evidence"]["line"] == expected_line, e["evidence"]

    # split("\n"), never splitlines(): see AGENTS.md.
    text = (src / e["evidence"]["path"]).read_text(encoding="utf8").split("\n")
    line = text[e["evidence"]["line"] - 1]
    assert must_contain in line, (
        f"{etype} cites {cite(e)} but that line reads {line!r}, not containing {must_contain!r}"
    )


def test_structural_edges_have_no_fabricated_evidence(indexed):
    """A file being in a directory is not written anywhere. Claiming a line
    for it would be exactly the fabricated citation this schema prevents."""
    _src, _out, edges = indexed
    for e in edges:
        if e["type"] == "CONTAINS":
            assert e["evidence"] is None
            assert e["method"] == METHOD_FILESYSTEM


def test_call_edges_are_attributed_to_the_resolver(indexed):
    """The parse tree supplies a name; matching it to a definition is the
    resolver's judgement, and that is where ambiguity comes from."""
    _src, _out, edges = indexed
    for e in edges:
        if e["type"] in ("CALLS", "CALLS_EXTERNAL", "INHERITS"):
            assert e["method"] == METHOD_NAME_RESOLVER


def test_defines_records_the_grammar_that_read_it(indexed):
    """`tree-sitter/python` not bare `tree-sitter`: the same edge type carries
    different weight from different grammars."""
    _src, _out, edges = indexed
    e = _find(edges, "DEFINES", dst="sym:app/routes.py::helper")
    assert e["method"] == "tree-sitter/python"


def test_ambiguous_calls_split_confidence_across_candidates(tmp_path):
    """Two definitions of one name: each edge gets 1/n, and says so."""
    src = tmp_path / "proj"
    (src / "pkg").mkdir(parents=True)
    for mod in ("a", "b"):
        (src / "pkg" / f"{mod}.py").write_text(
            f"MOD = 'module {mod} with module-level residue text'\n\n\n"
            "def shared_name():\n    return 1\n",
            encoding="utf-8",
        )
    (src / "pkg" / "caller.py").write_text(
        "CALLER = 'the caller module with residue text here'\n\n\n"
        "def go():\n    return shared_name()\n",
        encoding="utf-8",
    )
    out = src / ".r2g"
    assert main(["build", str(src), "-o", str(out), "--formats", "jsonl"]) == 0
    edges = [
        json.loads(line)
        for line in (out / "agent" / "edges.jsonl").read_text(encoding="utf8").split("\n")
        if line.strip()
    ]
    calls = [e for e in edges if e["type"] == "CALLS" and e["src"] == "sym:pkg/caller.py::go"]
    assert len(calls) == 2, calls
    for e in calls:
        assert e["confidence"] == 0.5
        assert e["ambiguous"] is True
        assert e["candidate_count"] == 2
        # Both cite the same call site: the ambiguity is in the target, not
        # in where the call is written.
        assert e["evidence"] == {"path": "pkg/caller.py", "line": 5}


def test_cochange_is_marked_correlational(tmp_path):
    """git-log edges must not be mistaken for structural ones."""
    import os
    import subprocess

    src = tmp_path / "proj"
    (src / "pkg").mkdir(parents=True)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e",
    }
    if (
        subprocess.run(
            ["git", "-C", str(src), "init", "-q"], capture_output=True, env=env
        ).returncode
        != 0
    ):
        pytest.skip("git unavailable")
    (src / ".gitignore").write_text(".r2g/\n", encoding="utf-8")
    for n in range(4):
        for mod in ("x", "y"):
            (src / "pkg" / f"{mod}.py").write_text(
                f"M = 'module {mod} revision {n} with residue text'\n", encoding="utf-8"
            )
        subprocess.run(["git", "-C", str(src), "add", "-A"], capture_output=True, env=env)
        subprocess.run(
            ["git", "-C", str(src), "commit", "-qm", f"r{n}"], capture_output=True, env=env
        )

    out = src / ".r2g"
    assert (
        main(["build", str(src), "-o", str(out), "--formats", "jsonl", "--git-history", "10"]) == 0
    )
    edges = [
        json.loads(line)
        for line in (out / "agent" / "edges.jsonl").read_text(encoding="utf8").split("\n")
        if line.strip()
    ]
    cochange = [e for e in edges if e["type"] == "CO_CHANGE"]
    if not cochange:
        pytest.skip("no CO_CHANGE edges produced by this git history")
    for e in cochange:
        assert e["method"] == METHOD_GIT_LOG
        assert e["evidence"] is None, "a history fact has no line of code to cite"
        assert 0.0 <= e["confidence"] <= 1.0


# --------------------------------------------------------------------------
# edgemeta helpers
# --------------------------------------------------------------------------


def test_normalize_fills_defaults_without_overriding_a_caller():
    filled = normalize({"src": "a", "dst": "b", "type": "CALLS"})
    assert filled["method"] and filled["confidence"] == 1.0 and filled["evidence"] is None

    supplied = normalize(
        {"src": "a", "dst": "b", "type": "CALLS", "confidence": 0.25, "method": "x"}
    )
    assert supplied["confidence"] == 0.25 and supplied["method"] == "x"


def test_evidence_refuses_to_invent_a_line():
    assert evidence("a.py", 3) == {"path": "a.py", "line": 3}
    assert evidence("a.py", 0) is None
    assert evidence("a.py", None) is None
    assert evidence(None, 3) is None


def test_file_of_maps_node_ids_to_paths():
    assert file_of("sym:pkg/mod.py::Class.method") == "pkg/mod.py"
    assert file_of("file:pkg/mod.py") == "pkg/mod.py"
    for nid in ("dir:pkg", "repo:x", "module:os", "external:len"):
        assert file_of(nid) is None


def test_cite_and_describe_render_one_way():
    e = normalize(
        {
            "src": "a",
            "dst": "b",
            "type": "CALLS",
            "confidence": 0.25,
            "candidate_count": 4,
            "evidence": {"path": "app/x.py", "line": 12},
            "call_kind": "dynamic",
        }
    )
    assert cite(e) == "app/x.py:12"
    text = describe(e)
    assert "0.25" in text and "4 candidate" in text and "app/x.py:12" in text
    assert "dynamic" in text, "a dynamic call must say the runtime target may differ"

    assert cite(normalize({"src": "a", "dst": "b", "type": "CONTAINS"})) is None


def test_schema_version_is_recorded_in_the_manifest(indexed):
    _src, out, _edges = indexed
    manifest = json.loads((out / "agent" / "manifest.json").read_text(encoding="utf8"))
    assert manifest["edge_schema_version"] == EDGE_SCHEMA_VERSION
    # The field meanings ship with the index: a consumer should not have to
    # find the source to learn what `confidence` measures.
    assert "confidence" in manifest["edge_fields"]
    assert "evidence" in manifest["edge_fields"]


# --------------------------------------------------------------------------
# Confidence and limitations segment
# --------------------------------------------------------------------------


def test_limitations_block_has_the_same_shape_even_with_nothing_to_caveat():
    """A section that appears only when something is wrong gets skipped."""
    clean = render(
        {
            "chunks": [{"path": "a.py", "why": "seed"}],
            "truncated": False,
            "used_chars": 100,
            "budget_chars": 24000,
        }
    )
    assert "Confidence and limitations" in clean
    for limit in STANDING_LIMITS:
        assert limit in clean
    assert "nothing was cut for space" in clean


def test_limitations_block_reports_truncation():
    """An answer from a truncated pack can be wrong by omission, and nothing
    else in the output says so."""
    cut = render(
        {
            "chunks": [{"path": "a.py", "why": "seed"}],
            "truncated": True,
            "used_chars": 24000,
            "budget_chars": 24000,
        }
    )
    assert "truncated" in cut
    assert "--budget" in cut


def test_limitations_block_counts_ambiguous_edges():
    text = render(
        {
            "chunks": [
                {"path": "a.py", "why": "seed"},
                {"path": "b.py", "why": "CALLS in of f", "confidence": 0.25},
                {"path": "c.py", "why": "IMPORTS out of a.py", "confidence": 1.0},
            ],
            "truncated": False,
            "used_chars": 10,
            "budget_chars": 100,
        }
    )
    assert "1 of those edge(s) is ambiguous" in text
    assert "1x CALLS" in text and "1x IMPORTS" in text


def test_limitations_block_says_so_when_nothing_was_cited():
    text = render({"chunks": [], "truncated": False})
    assert "no cited source" in text
    assert "unsupported" in text


def test_limitations_block_is_pure_ascii():
    """It is written to a console that may be a strict cp1252 stream on
    Windows, where an em dash is a crash rather than a typographic nicety."""
    text = render(
        {
            "chunks": [{"path": "a.py", "why": "seed"}, {"path": "b.py", "why": "CALLS in of f"}],
            "truncated": True,
            "used_chars": 5,
            "budget_chars": 10,
        }
    )
    assert all(ord(c) < 128 for c in text), [c for c in text if ord(c) >= 128]


def test_confidence_report_is_machine_readable():
    r = confidence_report(
        {
            "chunks": [{"path": "a.py", "why": "seed"}, {"path": "b.py", "why": "CALLS in of f"}],
            "truncated": False,
            "used_chars": 50,
            "budget_chars": 200,
        }
    )
    assert r["blocks"] == 2 and r["seeds"] == 1 and r["neighbours"] == 1
    assert r["edges"]["by_type"] == {"CALLS": 1}
    assert r["budget_used_pct"] == 25
    assert r["cited_files"] == ["a.py", "b.py"]


def test_answer_appends_the_segment(monkeypatch, capsys):
    """The segment is appended by repo2graph, not produced by the model."""
    import repo2graph.answer as answer_mod

    pack = {
        "markdown": "# map\n---\n### [cite: a.py:1-2] `f` (seed)\ncode\n",
        "query": "what does f do?",
        "chunks": [{"path": "a.py", "why": "seed"}],
        "truncated": False,
        "used_chars": 40,
        "budget_chars": 24000,
    }

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        answer_mod,
        "pick_provider",
        lambda *a, **k: {"name": "openai", "env": "OPENAI_API_KEY", "value": "x"},
    )
    monkeypatch.setattr(answer_mod, "_disclose", lambda *a, **k: None)

    class _Lines(list):
        """Stands in for _BoundedLines, which the caller also asks `.truncated`."""

        truncated = False

    monkeypatch.setattr(
        answer_mod,
        "_BoundedLines",
        lambda resp, limit: _Lines([b'data: {"choices":[{"delta":{"content":"f returns 1."}}]}']),
    )

    class _Opener:
        @staticmethod
        def open(req, timeout=None):
            return _FakeResp()

    monkeypatch.setattr(answer_mod, "_OPENER", _Opener)

    text = answer_mod.stream_answer(pack)
    out = capsys.readouterr().out
    assert "f returns 1." in text
    assert "Confidence and limitations" in out, "the segment was not appended to the answer"
    assert "1 cited block(s)" in out


# --------------------------------------------------------------------------
# Bug-report bundle
# --------------------------------------------------------------------------


def test_bundle_never_contains_source_code(indexed):
    """The one absolute rule. A user filing from a private repository must be
    able to post this without reading every line of it first."""
    src, out, _edges = indexed
    report = build_report(out, repo=src, category="incorrect-relationship")
    blob = json.dumps(report) + format_report(report)

    # Distinctive strings that exist only inside the fixture's source files.
    for secret in (
        "the base module, with module-level residue",
        "the routes module, with module-level residue",
        "class ApiHandler(Handler)",
        "def helper():",
    ):
        assert secret not in blob, f"source content leaked into the bundle: {secret!r}"


def test_bundle_omits_paths_by_default_and_includes_them_on_request(indexed):
    src, out, _edges = indexed
    default = json.dumps(build_report(out, repo=src))
    assert "app/routes.py" not in default
    assert "app/base.py" not in default

    opted_in = build_report(out, repo=src, include_paths=True)
    assert opted_in["privacy"]["paths_included"] is True


def test_bundle_contains_no_absolute_paths(indexed):
    """Absolute paths carry usernames, home layout and employer directory
    conventions, and add nothing once the repo-relative path is known."""
    src, out, _edges = indexed
    blob = json.dumps(build_report(out, repo=src, include_paths=True)) + format_report(
        build_report(out, repo=src, include_paths=True)
    )
    assert str(src) not in blob
    assert str(out) not in blob
    # Windows drive letters and posix home paths.
    assert not re.search(r"[A-Za-z]:[\\/]Users[\\/]", blob), "a Windows absolute path leaked"
    assert "/home/" not in blob and "/Users/" not in blob


def test_bundle_omits_env_values_and_remote_url(indexed, monkeypatch):
    secret = "sk-ant-ThisMustNeverAppearInABugReport"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    src, out, _edges = indexed
    report = build_report(out, repo=src)
    blob = json.dumps(report) + format_report(report)
    assert secret not in blob
    assert "ThisMustNeverAppear" not in blob
    assert report["privacy"]["contains_env_values"] is False
    # `contains_remote_url` is the privacy *declaration*; what must be absent
    # is an actual URL, so assert on the shape of one.
    assert "github.com/" not in blob
    assert "git@" not in blob
    assert not re.search(r"https?://[^\s\"]+\.git", blob)


def test_bundle_carries_the_edge_quality_histogram(indexed):
    """Often what explains a bad answer: a graph dominated by ambiguous name
    matches behaves differently from one that resolves cleanly."""
    src, out, _edges = indexed
    edges = build_report(out, repo=src)["edges"]
    assert edges["available"] is True
    assert edges["total"] > 0
    assert edges["by_type"] and edges["by_method"]
    assert sum(edges["confidence"].values()) == edges["total"]
    assert edges["edge_schema_version"] == EDGE_SCHEMA_VERSION


def test_bundle_names_what_each_category_still_needs(indexed):
    src, out, _edges = indexed
    for category in CATEGORIES:
        report = build_report(out, repo=src, category=category)
        assert report["category"] == category
        assert report["please_also_attach"], f"{category} asks for nothing extra"


def test_bundle_survives_a_missing_index(tmp_path):
    """A user whose build failed still needs to file a report."""
    report = build_report(tmp_path / "nope")
    assert report["index"]["available"] is False
    assert report["environment"]["versions"]["python"]
    assert "repo2graph bug report" in format_report(report)


def test_bug_report_cli(indexed, capsys, tmp_path):
    src, out, _edges = indexed
    assert main(["bug-report", "-o", str(out), "-r", str(src)]) == 0
    assert "repo2graph bug report" in capsys.readouterr().out

    assert main(["bug-report", "-o", str(out), "-r", str(src), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["bundle"] == "repo2graph/bug-report-1"

    dest = tmp_path / "report.md"
    assert main(["bug-report", "-o", str(out), "-r", str(src), "--write", str(dest)]) == 0
    assert dest.read_text(encoding="utf8").startswith("<!-- repo2graph bug-report bundle")


# --------------------------------------------------------------------------
# Feedback categories line up with the issue templates
# --------------------------------------------------------------------------


def test_every_category_has_an_issue_template():
    """`--category` and the GitHub template chooser must not drift apart."""
    template_dir = REPO_ROOT / ".github" / "ISSUE_TEMPLATE"
    blob = "\n".join(p.read_text(encoding="utf8") for p in sorted(template_dir.glob("*.yml")))
    for category in CATEGORIES:
        assert category in blob, (
            f"feedback category '{category}' has no issue template mentioning it; "
            "a --category value users cannot file under is a dead end"
        )


def test_the_three_new_templates_exist_and_point_at_the_bundle():
    template_dir = REPO_ROOT / ".github" / "ISSUE_TEMPLATE"
    for name in ("stale_index.yml", "parser_failure.yml", "answer_unhelpful.yml"):
        path = template_dir / name
        assert path.is_file(), f"missing issue template: {name}"
        text = path.read_text(encoding="utf8")
        assert "repo2graph bug-report" in text
        assert "--category" in text


# --------------------------------------------------------------------------
# MCP surfaces carry the same citations
# --------------------------------------------------------------------------


def test_mcp_neighbours_cites_the_call_site_not_just_the_definition(indexed):
    """For "what calls this", the useful citation is the call site.

    The node label says where the *neighbour* is defined; the edge's evidence
    says where the relationship is written. `repo_neighbours` must carry both,
    or an agent asking for callers gets a list of definitions with no way to
    open the lines that actually do the calling.
    """
    from repo2graph.mcp import dispatch
    from repo2graph.query import Index

    src, out, _edges = indexed
    text = dispatch(
        Index(out), "repo_neighbours", {"node_id": "sym:app/routes.py::helper", "limit": 10}
    )

    caller = [ln for ln in text.split("\n") if "CALLS in" in ln and "serve" in ln]
    assert caller, f"no incoming CALLS edge in:\n{text}"
    # helper is defined at routes.py:11 and called from routes.py:8.
    assert "at app/routes.py:8" in caller[0], caller[0]


def test_mcp_neighbours_marks_an_ambiguous_edge(tmp_path):
    """An edge below 1.0 must not read like a certain one.

    Without the marker, a name that matched three candidates is presented to
    the agent exactly as a unique resolution is -- which is the trust problem
    the edge schema exists to fix, on the one surface an agent actually reads.
    """
    from repo2graph.mcp import dispatch
    from repo2graph.query import Index

    src = tmp_path / "proj"
    (src / "pkg").mkdir(parents=True)
    for mod in ("a", "b"):
        (src / "pkg" / f"{mod}.py").write_text(
            f"MOD = 'module {mod} with module-level residue text'\n\n\n"
            "def shared_name():\n    return 1\n",
            encoding="utf-8",
        )
    (src / "pkg" / "caller.py").write_text(
        "CALLER = 'the caller module with residue text here'\n\n\n"
        "def go():\n    return shared_name()\n",
        encoding="utf-8",
    )
    out = src / ".r2g"
    assert main(["build", str(src), "-o", str(out), "--formats", "jsonl"]) == 0

    text = dispatch(
        Index(out), "repo_neighbours", {"node_id": "sym:pkg/caller.py::go", "limit": 10}
    )
    ambiguous = [ln for ln in text.split("\n") if "shared_name" in ln]
    assert ambiguous, f"no shared_name neighbour in:\n{text}"
    for line in ambiguous:
        assert "AMBIGUOUS 0.5 of 2 candidates" in line, line
        assert "at pkg/caller.py:5" in line, line


def test_mcp_search_returns_cited_markdown(indexed):
    """`repo_search` returns the pack's markdown, headers and all."""
    from repo2graph.mcp import dispatch
    from repo2graph.query import Index

    _src, out, _edges = indexed
    text = dispatch(Index(out), "repo_search", {"query": "helper", "k": 3})
    assert "[cite:" in text, f"repo_search returned no citation header:\n{text[:400]}"
    assert re.search(r"\[cite: [^\]]+:\d+-\d+\]", text)


def test_mcp_tools_return_text_not_json(indexed):
    """Pinned because docs/OUTPUT_SCHEMA.md documents the citation *form* per
    surface, and it previously claimed these returned per-result `path` and
    `start_line` fields. They return strings."""
    from repo2graph.mcp import dispatch
    from repo2graph.query import Index

    _src, out, _edges = indexed
    idx = Index(out)
    for name, args in (
        ("repo_search", {"query": "helper"}),
        ("repo_neighbours", {"node_id": "sym:app/routes.py::helper"}),
        ("repo_map", {}),
    ):
        result = dispatch(idx, name, args)
        assert isinstance(result, str), f"{name} returned {type(result)}"
        with pytest.raises(json.JSONDecodeError):
            json.loads(result)


def test_bundle_scrubs_paths_out_of_free_text_notes(indexed, monkeypatch):
    """Prose assembled elsewhere must not be able to smuggle a path in.

    `compute_freshness` interpolates an exception message into a note, and an
    `OSError` carries the absolute path that failed -- so a discovery failure
    put an absolute path to `pkg/secret_roadmap.py` into a bundle that
    promises no absolute paths and no filenames. The happy path produces no
    such note, which is why the other privacy tests did not see it.

    Scrubbed at the boundary rather than in each producer: the guarantee has
    to hold for prose written by code that has never heard of the bundle.
    """
    import repo2graph.parse as parse_mod

    src, out, _edges = indexed
    leak = src / "pkg" / "secret_roadmap.py"

    def _boom(*a, **k):
        raise OSError(f"cannot read {leak}")

    monkeypatch.setattr(parse_mod, "discover", _boom)
    report = build_report(out, repo=src)
    blob = json.dumps(report) + format_report(report)

    assert str(src) not in blob, "an absolute path reached the bundle through a note"
    assert "secret_roadmap" not in blob, "a filename reached the bundle through a note"
    assert "<path redacted>" in blob, "the note was dropped rather than redacted"
    # The note itself is still there, so the maintainer still learns what failed.
    assert any("could not re-discover" in n for n in report["index"]["freshness"]["notes"])


def test_scrub_redacts_path_shapes_but_leaves_prose_alone():
    from repo2graph.bugreport import _scrub

    assert "secret" not in _scrub(r"cannot read C:\Users\me\proj\secret.py")
    assert "secret" not in _scrub("cannot read /home/me/proj/secret.py")
    assert "secret" not in _scrub("failed on pkg/secret_roadmap.py")
    # Ordinary diagnostic prose survives: over-redaction is safe, but a note
    # reduced to nothing tells the maintainer nothing.
    plain = "index.state.json is unreadable; file-level check skipped"
    assert _scrub(plain) == plain
    assert _scrub("not a git repository (commit comparison skipped)") == (
        "not a git repository (commit comparison skipped)"
    )
