"""Backlog E40: `--max-edges`, `--max-bytes` and `--limit-policy`.

`--max-files` already bounded discovery, but nothing bounded the two axes that
actually decide a build's memory ceiling on a large repository: total source
bytes read, and total edges held. A repository can sit well under any file count
and still produce millions of edges, because edges grow with the square of how
interconnected the code is, not with how many files it has.

The property these tests care about most is not that a limit truncates -- that
part is easy -- but that a truncated index *says so*. `limits_hit` is written to
`stats.json` under both policies, because the question a reader cannot otherwise
answer is "are there really no callers of `f`, or was that edge dropped at the
ceiling?". `truncate` suppresses the stderr line, never the record.
"""

import json

import pytest

from repo2graph.chunks import iter_chunks
from repo2graph.export import dump_all
from repo2graph.graph import build
from repo2graph.parse import BuildConfig

# Three modules in a chain, so there are comfortably more edges than the small
# ceilings below -- a fixture that happened to fit under the limit would pass
# every one of these tests while proving nothing.
FILES = {
    "pkg/a.py": "def a():\n    return b_one()\n",
    "pkg/b.py": "def b_one():\n    return b_two()\n\n\ndef b_two():\n    return 1\n",
    "pkg/c.py": "from pkg.b import b_one\n\n\ndef c():\n    return b_one()\n",
}


@pytest.fixture
def repo(tmp_path):
    """Write the fixture with LF endings on every platform.

    `Path.write_text` translates "\\n" to "\\r\\n" on Windows, which would make
    each file two bytes longer per line than `len(body.encode())` says -- and
    the byte-budget tests below derive their budgets from exactly that length.
    Writing bytes keeps the budget arithmetic identical on Windows and POSIX.
    """
    for rel, body in FILES.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body.encode("utf8"))
    return tmp_path


def built(repo, out, **kw):
    """Build and dump; return the parsed stats.json."""
    g = build(repo, config=BuildConfig(), jobs=1, **kw)
    dump_all(g, iter_chunks(g), out, {"jsonl"}, 0)
    return g, json.loads((out / "agent" / "stats.json").read_text(encoding="utf8"))


def test_an_unbounded_build_records_no_limits(repo, tmp_path):
    """The baseline every other test here is measured against.

    Also pins that `limits_hit` is always present rather than conditionally
    absent: a consumer checking it should not have to distinguish "no limit
    fired" from "this index predates the field".
    """
    g, stats = built(repo, tmp_path / "out")
    assert stats["limits_hit"] == {}
    assert len(g.edges) > 6, "fixture must exceed the ceilings used below"


def test_max_edges_caps_the_graph(repo, tmp_path):
    g, stats = built(repo, tmp_path / "out", max_edges=4)
    assert len(g.edges) == 4
    assert stats["limits_hit"]["edges_dropped"] > 0


def test_max_edges_truncation_is_deterministic(repo, tmp_path):
    """Two builds of one tree under one ceiling keep the *same* edges.

    This is the property that makes a bounded index usable at all. Discovery is
    sorted, so edges are added in a fixed order and the prefix the ceiling keeps
    is fixed too. If that ever stopped holding, two machines would hold
    different subsets of the same graph and each would answer questions the
    other could not -- the same failure `--max-files` had before discovery was
    sorted.
    """
    first, _ = built(repo, tmp_path / "a", max_edges=4)
    second, _ = built(repo, tmp_path / "b", max_edges=4)
    keys = [(e["src"], e["dst"], e["type"]) for e in first.edges]
    assert keys == [(e["src"], e["dst"], e["type"]) for e in second.edges]


def test_a_dropped_duplicate_is_not_counted_against_the_ceiling(repo, tmp_path):
    """`add_edge` dedupes before it checks the limit.

    Counting a repeat as a drop would make `edges_dropped` report loss that did
    not happen, and would let a graph with many duplicate edges trip a ceiling
    it never actually reached.
    """
    g = build(repo, config=BuildConfig(), jobs=1, max_edges=1000)
    before = len(g.edges)
    src, dst, etype = (g.edges[0]["src"], g.edges[0]["dst"], g.edges[0]["type"])
    g.add_edge(src, dst, etype)
    assert len(g.edges) == before
    assert "edges_dropped" not in g.limits_hit


def test_max_bytes_stops_at_a_prefix_of_discovery_order(repo, tmp_path):
    """The budget keeps a prefix, never a best-fit selection.

    Skipping an oversized file and continuing would make which small files are
    indexed depend on the sizes of files *after* them, so adding one large file
    in the middle of a repository could silently change the tail of the
    selection. A prefix is the only reproducible answer.
    """
    sizes = {rel: len(body.encode("utf8")) for rel, body in FILES.items()}
    budget = sizes["pkg/a.py"] + sizes["pkg/b.py"]

    g, stats = built(repo, tmp_path / "out", max_bytes=budget)
    indexed = sorted(n["path"] for n in g.nodes.values() if n["type"] == "file")

    assert indexed == ["pkg/a.py", "pkg/b.py"], indexed
    assert stats["limits_hit"]["files_dropped_over_max_bytes"] == 1
    assert stats["bytes_indexed"] == budget


def test_a_budget_that_fits_everything_records_nothing(repo, tmp_path):
    """An inert limit must not report a truncation that did not occur."""
    total = sum(len(b.encode("utf8")) for b in FILES.values())
    _, stats = built(repo, tmp_path / "out", max_bytes=total)
    assert stats["limits_hit"] == {}
    assert stats["bytes_indexed"] == total


def test_truncate_is_quiet_on_stderr_but_still_records(repo, tmp_path, capsys):
    """The whole point of the two policies.

    `truncate` buys silence on stderr and nothing else. An index that was cut
    and cannot say so is the failure this field exists to prevent, so the
    record is not the policy's to suppress.
    """
    _, stats = built(repo, tmp_path / "out", max_edges=4, limit_policy="truncate")
    assert stats["limits_hit"]["edges_dropped"] > 0
    assert capsys.readouterr().err == ""


def test_warn_announces_the_ceiling_once(repo, tmp_path, capsys):
    """Once per limit, not once per dropped edge.

    A ceiling reached early drops thousands of edges; a line each would bury
    the build's real output and make the warning useless.
    """
    built(repo, tmp_path / "out", max_edges=4, limit_policy="warn")
    err = capsys.readouterr().err
    assert err.count("edge ceiling reached") == 1, err


def test_an_unknown_limit_policy_is_refused(repo):
    """A typo must not silently select the quiet policy."""
    with pytest.raises(ValueError, match="limit_policy"):
        build(repo, config=BuildConfig(), jobs=1, limit_policy="truncat")
