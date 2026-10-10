"""Generated hostile input for the parsers and protocol surfaces #316 names.

`test_properties.py` covers slicing, encodings and paths. This file covers the
rest of the list: import strings, MCP tool arguments, pack budgets, malformed
JSONL artifacts, and paging cursors. Each property is "no crash, no hang, and
the output stays inside its stated bound" -- never a particular answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from repo2graph.cli import main
from repo2graph.mcp.guardrails import MCP_MAX_BUDGET_TOKENS
from repo2graph.mcp.pagination import CursorError, decode_cursor
from repo2graph.mcp.schemas import TOOL_DESCRIPTIONS, ToolError
from repo2graph.mcp.tools import dispatch
from repo2graph.parse import LANG_CFG, parse_import_details
from repo2graph.query import Index, count_tokens, read_jsonl

FUZZ = settings(
    max_examples=100,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

import_lines = st.one_of(
    st.text(max_size=200),
    st.builds(
        lambda kw, body: f"{kw} {body}",
        st.sampled_from(
            ["import", "from", "use", "require", "#include", "using", "load", "require_relative"]
        ),
        st.text(alphabet=st.characters(codec="utf-8"), max_size=120),
    ),
)

json_scalar = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**70), max_value=2**70),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=300),
)
json_value = st.recursive(
    json_scalar,
    lambda inner: (
        st.lists(inner, max_size=4) | st.dictionaries(st.text(max_size=12), inner, max_size=4)
    ),
    max_leaves=12,
)
tool_args = st.dictionaries(
    st.sampled_from(
        ["query", "node_id", "k", "hops", "limit", "budget_tokens", "cursor", "path", "start",
         "end", "context", "name", "src", "dst", "min_confidence", "edge_types", "task_id"]
    ) | st.text(max_size=10),
    json_value,
    max_size=6,
)  # fmt: skip


@pytest.fixture(scope="module")
def fuzz_index(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("fuzz")
    repo = root / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "core.py").write_text(
        "import os\n\n\ndef load(path):\n    return parse(read(path))\n\n\n"
        "def read(path):\n    return open(path).read()\n\n\n"
        "def parse(text):\n    return text.split()\n",
        encoding="utf8",
    )
    (repo / "pkg" / "test_core.py").write_text(
        "from pkg.core import load\n\n\ndef test_load():\n    assert load('x')\n", encoding="utf8"
    )
    out = root / "idx"
    main(["build", str(repo), "-o", str(out), "--formats", "jsonl,overview"])
    return out


@FUZZ
@given(raw=import_lines, lang=st.sampled_from(sorted(LANG_CFG)))
def test_parse_import_details_never_raises(raw, lang):
    details = parse_import_details(raw, lang)
    assert isinstance(details, list)
    for d in details:
        assert isinstance(d.module, str) and (d.name is None or isinstance(d.name, str))


@FUZZ
@given(name=st.sampled_from(sorted(TOOL_DESCRIPTIONS)), args=tool_args)
def test_every_tool_answers_any_arguments_with_bounded_text(fuzz_index, name, args):
    """Caller-hostile MCP arguments: a string comes back, never an exception,
    and never more than the token ceiling the tools promise (plus the notes)."""
    out = dispatch(Index(fuzz_index), name, args)
    assert isinstance(out, str)
    if not isinstance(out, ToolError):
        assert count_tokens(out) <= MCP_MAX_BUDGET_TOKENS + 400, (name, args)


@FUZZ
@given(
    query=st.text(max_size=200),
    k=st.integers(min_value=0, max_value=50),
    hops=st.integers(min_value=0, max_value=4),
    budget=st.integers(min_value=1, max_value=4000),
    margin=st.floats(min_value=0, max_value=1),
)
def test_a_token_bounded_pack_never_exceeds_its_budget(fuzz_index, query, k, hops, budget, margin):
    pack = Index(fuzz_index).pack_context(
        query, k=k, hops=hops, budget_tokens=budget, token_margin=margin, exclude_secrets=True
    )
    assert pack["tokens_used"] <= budget
    assert pack["markdown"] == "" or pack["tokens_used"] >= 1


@FUZZ
@given(lines=st.lists(st.binary(max_size=120), max_size=8))
def test_malformed_jsonl_raises_value_error_or_loads(tmp_path_factory, lines):
    path = tmp_path_factory.mktemp("jsonl") / "x.jsonl"
    path.write_bytes(b"\n".join(lines))
    try:
        rows = read_jsonl(path)
    except ValueError:
        return
    assert isinstance(rows, list)


@FUZZ
@given(cursor=st.one_of(json_scalar, st.text(alphabet="ABCDEFabcdef0123456789-_.=", max_size=600)))
def test_a_forged_cursor_is_refused_cleanly(cursor):
    try:
        offset = decode_cursor(cursor, "repo_search", "digest", "identity")
    except CursorError:
        return
    # Only the empty cursor (and None) is accepted without a signature.
    assert offset == 0 and cursor in (None, "")
