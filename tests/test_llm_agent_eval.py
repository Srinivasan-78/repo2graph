"""`scripts/llm_agent_eval.py`: the loop, the dispatch and the scoring.

Everything here runs through `--dry-run`'s scripted model, so the suite needs
no API key, makes no request and spends nothing -- which is the whole reason
the scripted provider exists. What is *not* tested is any real provider's
wire format; that cannot be checked without spending money, and pretending
otherwise with a mocked HTTP layer would only test the mock.

The two things worth pinning are that the harness cannot silently become
vacuous: a solvable task has to score solved, and an unsolvable one has to
score unsolved. A scorer that always returns False looks like a model problem
and would survive a long time.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT))

from scripts.llm_agent_eval import (
    EVAL_TOOLS,
    ScriptedProvider,
    cited_lines,
    main,
    pick,
)

SAMPLE = Path(__file__).parent / "fixtures" / "sample_repo"


def test_the_tool_specs_come_from_the_shipped_schemas():
    """The eval must measure the descriptions this project actually ships."""
    from repo2graph.mcp.schemas import TOOL_DESCRIPTIONS, TOOL_SCHEMAS
    from scripts.llm_agent_eval import _tool_specs

    for provider, key in (("anthropic", "input_schema"), ("openai", "function")):
        specs = _tool_specs(provider)
        assert len(specs) == len(EVAL_TOOLS)
        for spec in specs:
            name = spec["name"] if provider == "anthropic" else spec["function"]["name"]
            assert name in TOOL_SCHEMAS
            if provider == "anthropic":
                assert spec[key] == TOOL_SCHEMAS[name]
                assert spec["description"] == TOOL_DESCRIPTIONS[name]
            else:
                assert spec[key]["parameters"] == TOOL_SCHEMAS[name]


def test_every_eval_tool_is_a_real_dispatchable_tool():
    """A typo here would silently shrink the measured surface."""
    from repo2graph.mcp.schemas import TOOL_SCHEMAS

    assert set(EVAL_TOOLS) <= set(TOOL_SCHEMAS)
    # The two operational tools are excluded on purpose, not by accident.
    assert "repo_cache_stats" not in EVAL_TOOLS
    assert "repo_build_status" not in EVAL_TOOLS


def test_cited_lines_reads_the_citation_headers_the_tools_emit():
    text = "## hit\n[cite: pkg/a.py:10-12]\nbody\n[cite: pkg/b.py:3-3]\nmore\n"
    assert cited_lines(text, Path(".")) == {
        "pkg/a.py": {10, 11, 12},
        "pkg/b.py": {3},
    }


@pytest.mark.parametrize(
    "bad",
    [
        "[cite: a.py:12-10]",  # end before start
        "[cite: a.py:1-99999]",  # absurd span
        "no citations at all",
    ],
)
def test_cited_lines_refuses_nonsense_without_raising(bad):
    assert cited_lines(bad, Path(".")) == {}


def test_a_missing_provider_names_the_way_out(monkeypatch):
    for env in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OLLAMA_HOST"):
        monkeypatch.delenv(env, raising=False)
    with pytest.raises(SystemExit, match="--dry-run"):
        pick(None, dry_run=False)


def test_dry_run_needs_no_key_at_all(monkeypatch):
    for env in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    assert isinstance(pick(None, dry_run=True), ScriptedProvider)


def test_a_provider_with_no_tool_calling_path_is_refused(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    for env in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    # Configured but unsupported here, and not guessed at.
    with pytest.raises(SystemExit):
        pick("gemini", dry_run=False)


def _tasks(tmp_path, evidence):
    p = tmp_path / "tasks.json"
    p.write_text(
        json.dumps(
            {
                "_comment": "test",
                "tasks": [
                    {
                        "id": f"t-{i}",
                        "repo": "local",
                        "kind": "structural",
                        "query": "what does the service do with the store",
                        "evidence": [ev],
                    }
                    for i, ev in enumerate(evidence)
                ],
            }
        ),
        encoding="utf-8",
    )
    return p


def _run(tmp_path, tasks_path, capsys, index_out=None):
    """Run the harness over `tasks_path`, indexing the sample fixture.

    `--index-out` is passed explicitly so the index lands under `tmp_path`.
    Without it the script used to default beside the tree it indexed, which
    left a `.r2g-llmeval/` inside `tests/fixtures/` after every run.
    """
    out = tmp_path / "report.json"
    rc = main(
        [
            "--dry-run",
            "--tasks",
            str(tasks_path),
            "--index-root",
            str(SAMPLE),
            "--index-out",
            str(index_out or tmp_path / "idx"),
            "--out",
            str(out),
        ]
    )
    capsys.readouterr()
    assert rc == 0
    return json.loads(out.read_text(encoding="utf-8"))


def test_the_loop_dispatches_a_real_tool_against_a_real_index(tmp_path, capsys):
    """End to end: scripted model -> dispatch -> scoring -> report."""
    tasks = _tasks(tmp_path, [{"path": "nope.py", "symbol": "z", "lines": [1, 2]}])
    report = _run(tmp_path, tasks, capsys)
    row = report["rows"][0]
    assert row["tool_calls"] == 1, "the scripted model must actually call a tool"
    assert row["turns"] == 2
    assert row["stopped"] == "answered"
    assert row["answer_chars"] > 0, "the tool result reached the model"


def test_the_scorer_is_not_vacuous(tmp_path, capsys):
    """A solvable task scores solved and an unsolvable one does not.

    Without this, a scorer that always returned False would look exactly like
    a weak model and could sit here indefinitely.
    """
    # Find a line the scripted single-search run genuinely cites, so the
    # "solvable" case is solvable for a real reason rather than by assertion.
    shared = tmp_path / "idx"
    probe = _tasks(tmp_path, [{"path": "nope.py", "symbol": "z", "lines": [1, 2]}])
    _run(tmp_path, probe, capsys, index_out=shared)

    from repo2graph.mcp.tools import dispatch
    from repo2graph.query import Index

    idx = Index(shared)
    shown = cited_lines(
        dispatch(idx, "repo_search", {"query": "what does the service do with the store"}),
        SAMPLE,
    )
    assert shown, "repo_search returned no citations; the fixture or the tool changed"
    path, lines = next(iter(sorted(shown.items())))
    first = min(lines)

    tasks = _tasks(
        tmp_path,
        [
            {"path": path, "symbol": "s", "lines": [first, first]},
            {"path": "definitely/not/here.py", "symbol": "n", "lines": [1, 2]},
        ],
    )
    report = _run(tmp_path, tasks, capsys)
    by_id = {r["id"]: r for r in report["rows"]}
    assert by_id["t-0"]["solved"] is True
    assert by_id["t-1"]["solved"] is False
    assert report["solved"] == 1
    assert report["success_rate"] == 0.5


def test_a_tool_error_is_handed_back_rather_than_crashing_the_run(tmp_path, capsys, monkeypatch):
    """A refused argument is something the model has to recover from."""
    import scripts.llm_agent_eval as mod

    def boom(*_a, **_k):
        raise ValueError("refused: budget_tokens above the ceiling")

    monkeypatch.setattr(mod, "dispatch", boom)
    tasks = _tasks(tmp_path, [{"path": "nope.py", "symbol": "z", "lines": [1, 2]}])
    report = _run(tmp_path, tasks, capsys)
    row = report["rows"][0]
    assert row["stopped"] == "answered"
    assert row["tool_calls"] == 1


def test_the_report_carries_the_cost_of_the_run(tmp_path, capsys):
    """A success rate with no cost beside it is not a usable number."""
    tasks = _tasks(tmp_path, [{"path": "nope.py", "symbol": "z", "lines": [1, 2]}])
    report = _run(tmp_path, tasks, capsys)
    for key in (
        "provider",
        "model",
        "tasks",
        "solved",
        "success_rate",
        "mean_turns",
        "mean_tool_calls",
        "input_tokens",
        "output_tokens",
        "hit_max_turns",
        "wall_seconds",
    ):
        assert key in report, key


def test_max_turns_is_enforced(tmp_path, capsys, monkeypatch):
    """A model that never stops calling tools must not loop forever."""
    import scripts.llm_agent_eval as mod

    class Forever(ScriptedProvider):
        def call(self, messages):
            return {
                "text": "",
                "tool_calls": [{"id": "c", "name": "repo_map", "arguments": {}}],
                "usage": {},
                "raw": [],
            }

    monkeypatch.setattr(mod, "pick", lambda *_a, **_k: Forever())
    tasks = _tasks(tmp_path, [{"path": "nope.py", "symbol": "z", "lines": [1, 2]}])
    out = tmp_path / "r.json"
    rc = main(
        [
            "--dry-run",
            "--tasks",
            str(tasks),
            "--index-root",
            str(SAMPLE),
            "--index-out",
            str(tmp_path / "idx"),
            "--out",
            str(out),
            "--max-turns",
            "3",
        ]
    )
    capsys.readouterr()
    assert rc == 0
    row = json.loads(out.read_text(encoding="utf-8"))["rows"][0]
    assert row["stopped"] == "max_turns"
    assert row["turns"] == 3
