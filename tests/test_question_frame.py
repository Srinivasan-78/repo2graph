"""The question frame ("how does", "where is") is not evidence (#382)."""

from __future__ import annotations

import pytest

from repo2graph.cli import main
from repo2graph.query import Index, strip_question_frame


@pytest.mark.parametrize(
    "query, body",
    [
        ("how does URL resolution reach a view", " URL resolution reach a view"),
        ("How does URL resolution reach a view?", " URL resolution reach a view"),
        ("where is the cache invalidated", " the cache invalidated"),
        ("what are entrypoints", " entrypoints"),
        ("URL resolution view", "URL resolution view"),
        ("however the parser", "however the parser"),  # a word, not the frame
        ("how", "how"),  # nothing would be left: keep the query
        ("what is?", "what is?"),
    ],
)
def test_strip_question_frame(query, body):
    assert strip_question_frame(query) == body


def test_a_question_and_its_keywords_rank_alike(tmp_path):
    repo = tmp_path / "src"
    repo.mkdir()
    (repo / "urls.py").write_text(
        "def resolve(path):\n    '''URL resolution: find the view for a path.'''\n"
        "    return lookup_view(path)\n\n\ndef lookup_view(path):\n    return path\n",
        encoding="utf8",
    )
    (repo / "test_widgets.py").write_text(
        "def test_html_does_not_mask_exceptions():\n    url = 'x'\n    return url\n",
        encoding="utf8",
    )
    out = tmp_path / "idx"
    main(["build", str(repo), "-o", str(out), "--formats", "jsonl"])
    idx = Index(out)

    def top(q):
        return [idx.chunks[i]["node_id"] for _, i in idx.score(q)[:2]]

    assert top("how does URL resolution reach a view?") == top("URL resolution reach a view")
    assert top("how does URL resolution reach a view?")[0] == "sym:urls.py::resolve"
