"""graph.html on graphs too big to draw whole (#305).

Wide (many directories, shallow) and deep synthetic graphs: the payload stays
under the page ceiling, the page says when it is sampled and not otherwise,
the directory summary counts every node, and two runs write the same bytes.
"""

from __future__ import annotations

import json
import re
from collections import Counter

from repo2graph import viz
from repo2graph.viz import AGG_MAX_DIRS, TEMPLATE, aggregate, payload, write_html


class FakeGraph:
    def __init__(self, name, nodes, edges):
        self.name, self.nodes, self.edges = name, nodes, edges


def _graph(paths: list[str], lang: str = "python") -> FakeGraph:
    nodes, edges = {}, []
    dirs = set()
    for i, p in enumerate(paths):
        fid, sid = f"file:{p}", f"sym:{p}::f{i}"
        nodes[fid] = {"id": fid, "type": "file", "path": p, "lang": lang, "lines": 10}
        nodes[sid] = {
            "id": sid,
            "type": "symbol",
            "path": p,
            "qualname": f"f{i}",
            "kind": "function",
        }
        edges.append({"src": fid, "dst": sid, "type": "DEFINES"})
        d = p.rpartition("/")[0]
        while d:
            dirs.add(d)
            d = d.rpartition("/")[0]
    for d in sorted(dirs):
        nodes[f"dir:{d}"] = {"id": f"dir:{d}", "type": "dir", "path": d}
    syms = sorted(k for k in nodes if k.startswith("sym:"))
    for i, s in enumerate(syms):  # a ring of calls, plus a hub everyone calls
        edges.append({"src": s, "dst": syms[(i + 1) % len(syms)], "type": "CALLS"})
        edges.append({"src": s, "dst": syms[0], "type": "CALLS"})
    return FakeGraph("big", nodes, edges)


def wide() -> FakeGraph:
    return _graph([f"pkg{i % 150}/m{i}.py" for i in range(1500)])


def deep() -> FakeGraph:
    return _graph(["/".join(f"d{j}" for j in range(i % 12)) + f"/m{i}.py" for i in range(900)])


def _data(page: str) -> dict:
    m = re.search(r"const DATA = (.*?);\nconst NS", page, re.S)
    assert m
    return json.loads(m.group(1))


def test_a_sampled_graph_says_so_and_carries_a_summary_of_everything():
    g = wide()
    data = payload(g, 300)
    assert data["sampled"]["nodes"] == 300 == len(data["nodes"])
    assert data["totals"]["nodes"] == len(g.nodes)
    agg = data["dirs"]
    assert 2 <= len(agg["nodes"]) <= AGG_MAX_DIRS
    # Every file is counted in exactly one directory group: nothing sampled.
    assert sum(n["files"] for n in agg["nodes"]) == sum(
        1 for n in g.nodes.values() if n["type"] == "file"
    )
    assert sum(n["symbols"] for n in agg["nodes"]) == sum(
        1 for n in g.nodes.values() if n["type"] == "symbol"
    )
    assert all(0 <= e["s"] < len(agg["nodes"]) and e["w"] >= 1 for e in agg["edges"])
    assert {e["type"] for e in agg["edges"]} <= set(viz.AGG_EDGE_TYPES)


def test_a_graph_that_fits_has_no_banner_data_and_no_summary():
    g = _graph([f"a/m{i}.py" for i in range(10)] + [f"b/m{i}.py" for i in range(10)])
    data = payload(g, 300)
    assert "sampled" not in data and "dirs" not in data


def test_deep_trees_split_the_biggest_directory_first():
    g = deep()
    agg = aggregate(g, 12)
    assert agg is not None and len(agg["nodes"]) <= 12
    paths = [n["path"] for n in agg["nodes"]]
    assert len(set(paths)) == len(paths)
    # A carved directory keeps a group for the rest of it.
    rest = [n for n in agg["nodes"] if n.get("except")]
    assert rest and all(n["label"].endswith("/…") for n in rest)
    assert sum(n["files"] for n in agg["nodes"]) == 900


def test_more_top_level_directories_than_the_cap_still_summarise():
    agg = aggregate(wide(), 20)  # 150 top-level packages
    assert agg is not None and len(agg["nodes"]) == 20
    root = next(n for n in agg["nodes"] if n["path"] == "")
    assert len(root["except"]) == 19  # the 19 biggest carved out; the rest stay in root
    assert sum(n["files"] for n in agg["nodes"]) == 1500


def test_one_directory_is_not_summarised():
    assert aggregate(_graph([f"m{i}.py" for i in range(50)]), 60) is None


def test_the_page_is_byte_identical_across_runs(tmp_path):
    for make in (wide, deep):
        a, b = tmp_path / "a.html", tmp_path / "b.html"
        write_html(make(), a, 200)
        write_html(make(), b, 200)
        assert a.read_bytes() == b.read_bytes()


def test_the_page_never_exceeds_the_byte_ceiling(tmp_path, monkeypatch):
    monkeypatch.setattr(viz, "MAX_PAGE_BYTES", 200_000)
    out = tmp_path / "g.html"
    data = write_html(wide(), out, None)  # `--viz-nodes all`
    blob = re.search(r"const DATA = (.*?);\nconst NS", out.read_text(encoding="utf8"), re.S)
    assert blob and len(blob.group(1).encode("utf8")) <= 200_000
    assert data["sampled"]["shrunk_from"] == len(wide().nodes)
    assert data["sampled"]["page_limit_bytes"] == 200_000
    assert _data(out.read_text(encoding="utf8"))["sampled"]["nodes"] == len(data["nodes"])


def test_the_page_has_the_banner_view_switch_and_language_filter():
    for marker in ('id="banner"', 'id="btn-dirs"', 'id="btn-detail"', 'id="lang-legend"'):
        assert marker in TEMPLATE
    assert 'show(DATA.dirs ? "dirs" : "detail")' in TEMPLATE


def test_summary_language_is_the_dominant_code_language():
    g = _graph([f"web/m{i}.ts" for i in range(5)] + [f"api/m{i}.py" for i in range(5)])
    g.nodes["file:web/README.md"] = {
        "id": "file:web/README.md",
        "type": "file",
        "path": "web/README.md",
        "lang": "md",
        "file_type": "doc",
    }
    for n in g.nodes.values():
        if n["type"] == "file" and n["path"].endswith(".ts"):
            n["lang"] = "typescript"
    agg = aggregate(g, 10)
    assert agg is not None
    langs = {n["path"]: n.get("lang") for n in agg["nodes"]}
    assert langs == {"api": "python", "web": "typescript"}
    assert Counter(n["type"] for n in agg["nodes"]) == {"dir": 2}
