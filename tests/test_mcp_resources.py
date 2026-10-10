"""Resources and prompts, without the SDK (#388)."""

from __future__ import annotations

import pytest

from repo2graph.mcp.resources import get_prompt, list_prompts, list_resources, read_resource
from repo2graph.mcp.server import run_resource_read
from repo2graph.mcp.tools import dispatch
from repo2graph.query import Index


class Audit:
    def __init__(self):
        self.records = []

    def record(self, name, args, **kw):
        self.records.append((name, args, kw))


def test_the_map_resource_is_what_repo_map_returns(mini_index):
    idx = Index(mini_index)
    text, mime = read_resource(idx, "repo2graph://map")
    assert mime == "text/markdown" and text == dispatch(idx, "repo_map", {})


def test_resources_list_only_what_the_index_has(mini_index):
    names = {r["name"] for r in list_resources(mini_index)}
    assert names == {"map", "stats", "manifest"}


def test_a_resource_read_is_audited_like_a_tool_call(mini_index):
    audit = Audit()
    run_resource_read(mini_index, "repo2graph://stats", audit=audit)
    with pytest.raises(ValueError):
        run_resource_read(mini_index, "repo2graph://nope", audit=audit)
    assert [r[0] for r in audit.records] == ["resources/read", "resources/read"]
    assert audit.records[1][2]["outcome"] == "error"


def test_no_index_yet_is_a_clear_error(tmp_path):
    with pytest.raises(ValueError, match="no index to read yet"):
        run_resource_read(tmp_path / "missing", "repo2graph://stats")


def test_prompts_need_their_required_arguments():
    assert {p["name"] for p in list_prompts()} >= {"explain-symbol", "orient"}
    with pytest.raises(ValueError, match="needs symbol"):
        get_prompt("explain-symbol", {})
    with pytest.raises(ValueError, match="unknown prompt"):
        get_prompt("nope", {})
    _desc, text = get_prompt("orient", None)
    assert "repo2graph://map" in text
