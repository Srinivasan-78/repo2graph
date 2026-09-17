"""graph.html safety: the repo name is attacker-influenced, so treat it that way.

`repo2graph github <owner>/<repo>` takes the repo name from a GitHub spec, and
`Graph.name` is the directory name otherwise -- neither is under the reader's
control at the moment the page is opened. Three of these properties were already
true when this file was written; they are pinned here because nothing was
asserting them, and an unasserted security property is one refactor from gone.
"""
import json
import re

import pytest

from repo2graph.viz import TEMPLATE, payload, select, write_html


class FakeGraph:
    """The three attributes `write_html` reads off a Graph."""

    def __init__(self, name, nodes=None, edges=None):
        self.name = name
        self.nodes = nodes or {"file:a.py": {"id": "file:a.py", "type": "file",
                                            "name": "a.py", "path": "a.py"}}
        self.edges = edges or []


def render(tmp_path, name, **kw):
    """Write a page for a graph called `name` and hand back its HTML."""
    out = tmp_path / "graph.html"
    write_html(FakeGraph(name), out, **kw)
    return out.read_text(encoding="utf8")


# ------------------------------------------------------------------ (a) ----

def chrome(html):
    """The two HTML contexts the repo name is interpolated into."""
    return (re.search(r"<title>(.*?)</title>", html, re.S).group(1),
            re.search(r"<h1>(.*?)</h1>", html, re.S).group(1))


def test_a_script_tag_in_the_repo_name_is_escaped(tmp_path):
    """A repo literally named `<script>alert(1)</script>` must not execute."""
    html = render(tmp_path, "<script>alert(1)</script>")

    for spot in chrome(html):
        assert "<script>" not in spot
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in spot
    # The name also travels inside the JSON payload, which is a different
    # context with a different escape: there it is a quoted JS string literal,
    # and "<" is escaped so it cannot close the enclosing <script> block. What
    # must never happen is an *executable* tag appearing in the document.
    assert "<script>alert(1)</script>" not in html


@pytest.mark.parametrize("name", [
    '"><img src=x onerror=alert(1)>',
    "</title><script>alert(1)</script>",
    "' onmouseover='alert(1)",
    "</h1><iframe src=javascript:alert(1)>",
])
def test_attribute_and_tag_breakouts_in_the_repo_name_are_escaped(tmp_path, name):
    """Neither the <title> nor the <h1> may be escaped out of."""
    html = render(tmp_path, name)
    for spot in chrome(html):
        # html.escape() covers <, >, & and both quote characters, so no
        # breakout sequence survives into either context.
        for char in "<>\"'":
            assert char not in spot, f"{char!r} survived unescaped in {spot!r}"
    # And no new element was introduced anywhere in the document chrome.
    assert "<iframe" not in html
    assert "<img" not in html


def test_the_data_placeholder_in_a_repo_name_is_not_expanded(tmp_path):
    """A repo named `__R2G_DATA__` must not pull the JSON payload into <h1>.

    The template is filled by a single regex pass over both placeholders, so a
    replacement's *output* is never rescanned. A naive sequence of `.replace`
    calls -- title first, then data -- would substitute the whole graph payload
    into the title and the heading.
    """
    html = render(tmp_path, "__R2G_DATA__")

    title = re.search(r"<title>(.*?)</title>", html, re.S).group(1)
    h1 = re.search(r"<h1>(.*?)</h1>", html, re.S).group(1)
    for spot in (title, h1):
        assert "__R2G_DATA__" in spot, "the literal name should survive"
        assert '"nodes"' not in spot, "the JSON payload was injected"
        assert len(spot) < 200, f"payload leaked into the page chrome: {spot[:120]}"


def test_the_payload_placeholder_still_receives_the_real_data(tmp_path):
    """The single-pass guard must not break the substitution it protects."""
    html = render(tmp_path, "ordinary-repo")
    assert "__R2G_DATA__" not in html, "the data placeholder was never filled"
    assert '"nodes":' in html


def test_angle_brackets_in_the_payload_cannot_close_the_script_block(tmp_path):
    """`</script>` inside indexed content must not terminate the block early."""
    nodes = {"file:x.py": {"id": "file:x.py", "type": "file", "path": "x.py",
                           "name": "</script><script>alert(1)</script>"}}
    out = tmp_path / "g.html"
    write_html(FakeGraph("repo", nodes=nodes), out)
    html = out.read_text(encoding="utf8")

    assert "</script><script>alert(1)" not in html
    assert "\\u003c" in html, "'<' must be escaped inside the JSON blob"


# ------------------------------------------------------------------ (b) ----

def test_viz_nodes_zero_renders_no_nodes(tmp_path):
    """0 means an empty graph, not every node."""
    nodes = {f"file:{i}.py": {"id": f"file:{i}.py", "type": "file",
                              "name": f"{i}.py", "path": f"{i}.py"}
             for i in range(12)}
    edges = [{"src": "file:0.py", "dst": f"file:{i}.py", "type": "IMPORTS"}
             for i in range(1, 12)]

    kept, kept_edges = select(nodes, edges, max_nodes=0)
    assert kept == [] and kept_edges == []

    data = payload(FakeGraph("r", nodes=nodes, edges=edges), 0)
    assert data["nodes"] == [] and data["edges"] == []
    # The totals still report the real graph, so the page can say what it hid.
    assert data["totals"]["nodes"] == 12


def test_viz_nodes_none_is_the_no_cap_spelling(tmp_path):
    """`all` on the CLI arrives here as None and keeps every node."""
    nodes = {f"file:{i}.py": {"id": f"file:{i}.py", "type": "file",
                              "name": f"{i}.py", "path": f"{i}.py"}
             for i in range(12)}
    kept, _ = select(nodes, [], max_nodes=None)
    assert len(kept) == 12


# ------------------------------------------------------------------ (c) ----

def test_the_settle_loop_is_incremental_not_a_blocking_while(tmp_path):
    """Layout must yield to the browser between batches.

    A synchronous `while (alpha > ...) step()` blocks the main thread for the
    whole settle: no paint, no input, no scroll. Bounding the iteration count
    bounds the freeze but does not remove it.
    """
    assert "requestAnimationFrame(settle)" in TEMPLATE
    assert not re.search(r"while\s*\(\s*alpha\s*>", TEMPLATE), \
        "the blocking settle loop is back"
    assert "cancelAnimationFrame(settleRaf)" in TEMPLATE, \
        "overlapping relayouts must not fight over alpha"


def test_the_iteration_cap_is_configurable_from_the_container(tmp_path):
    """maxIterations defaults to 500 and is overridable per page."""
    assert "DEFAULT_MAX_ITERATIONS = 500" in TEMPLATE
    assert 'data-max-iterations="500"' in TEMPLATE
    assert "stage.dataset.maxIterations" in TEMPLATE


def test_a_progress_indicator_exists_and_is_hidden_when_idle(tmp_path):
    """The user must be able to tell a laying-out page from a frozen one."""
    html = render(tmp_path, "repo")
    assert re.search(r'<div id="progress"[^>]*\bhidden\b', html), \
        "the progress indicator must start hidden"
    assert "progress.hidden = false" in TEMPLATE, "…and be shown while settling"
    assert "progress.hidden = true" in TEMPLATE, "…and hidden again when done"


# ------------------------------------------------------------------ (d) ----

def test_pointer_capture_is_released_on_both_pointerup_and_pointercancel():
    """A capture never released swallows every later pointer event on the page.

    This is a source-level assertion rather than a DOM test: a real one needs a
    JS runtime (jsdom or playwright), and this repo has no Node toolchain in its
    dev extra. It pins the property that actually regressed -- a handler losing
    its release call -- but it cannot prove the released id matches the captured
    one. Noted in DONE.md as the one untested-in-a-browser property here.
    """
    for event in ("pointerup", "pointercancel"):
        handler = re.search(
            r'svg\.addEventListener\("%s",.*?\n\}\);' % event, TEMPLATE, re.S)
        assert handler, f"no {event} handler found"
        assert "releasePointerCapture(ev.pointerId)" in handler.group(0), \
            f"{event} never releases the pointer capture"


def test_pointer_capture_release_is_guarded():
    """releasePointerCapture throws if the element does not hold the capture."""
    assert TEMPLATE.count("try { svg.releasePointerCapture(ev.pointerId); }") >= 2


# --------------------------------------------------------------- shape ----

def test_the_page_is_self_contained(tmp_path):
    """No network at render time and none at view time: one file, no fetches."""
    html = render(tmp_path, "repo")
    assert "http://" not in html.replace("http://www.w3.org", "")
    assert "https://" not in html
    assert "fetch(" not in html and "XMLHttpRequest" not in html


def test_payload_is_valid_json(tmp_path):
    """Whatever escaping is applied, the blob must still parse."""
    html = render(tmp_path, "repo")
    blob = re.search(r"const DATA = (\{.*?\});", html, re.S).group(1)
    parsed = json.loads(blob.replace("\\u003c", "<"))
    assert "nodes" in parsed and "edges" in parsed
