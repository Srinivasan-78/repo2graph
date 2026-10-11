"""`build --reference-edges`: READS, WRITES and REFERENCES (#397)."""

from __future__ import annotations

import json

import pytest

from repo2graph.cli import main
from repo2graph.export import path as artifact_path
from repo2graph.graph import build
from repo2graph.parse import BuildConfig

SETTINGS = """\
TIMEOUT = 30
RETRIES = 3


class Config:
    debug = False
"""

CLIENT = """\
from app.settings import TIMEOUT, Config
from app.settings import RETRIES as R

MAX = 5
calls = 0


class Client:
    retries = R

    def run(self, cfg: Config) -> Config:
        global calls
        calls += 1
        self.retries = self.retries - 1
        return fetch(TIMEOUT, MAX)


def fetch(timeout, limit):
    return timeout * limit * R


def shadow():
    MAX = 1
    return MAX
"""

TS = """\
export const LIMIT = 10;
let count = 0;

interface Shape {
  size: number;
}

export class Box {
  size = 3;

  grow(n: number): Shape {
    count++;
    this.size = this.size + LIMIT;
    const local = n;
    return { size: clamp(local) };
  }
}

function clamp(v: number, max = LIMIT): number {
  return Math.min(v, max);
}
"""


def _repo(tmp_path):
    for rel, text in {
        "app/__init__.py": "",
        "app/settings.py": SETTINGS,
        "app/client.py": CLIENT,
        "web/box.ts": TS,
    }.items():
        p = tmp_path / "repo" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf8")
    return tmp_path / "repo"


@pytest.fixture
def g(tmp_path):
    return build(_repo(tmp_path), config=BuildConfig(reference_edges=True))


def _edges(g, etype):
    return {(e["src"][4:], e["dst"][4:]): e for e in g.edges if e["type"] == etype}


def test_module_level_constant_reads_and_global_writes(g):
    reads, writes = _edges(g, "READS"), _edges(g, "WRITES")
    c = "app/client.py::"
    assert (c + "Client.run", c + "MAX") in reads
    assert (c + "Client.run", "app/settings.py::TIMEOUT") in reads
    alias = reads[(c + "fetch", "app/settings.py::RETRIES")]  # `RETRIES as R`
    assert alias["resolution_kind"] == "import_alias"
    assert alias["evidence"] == {
        "path": "app/client.py",
        "line": CLIENT.count("\n", 0, CLIENT.index("* R")) + 1,
    }
    assert (c + "Client.run", c + "calls") in writes  # under `global calls`
    assert g.nodes["sym:app/client.py::MAX"]["kind"] == "constant"
    assert g.nodes["sym:app/client.py::calls"]["kind"] == "variable"


def test_fields_are_read_and_written_through_self(g):
    c = "app/client.py::"
    assert (c + "Client.run", c + "Client.retries") in _edges(g, "READS")
    assert (c + "Client.run", c + "Client.retries") in _edges(g, "WRITES")
    assert g.nodes["sym:app/client.py::Client.retries"]["kind"] == "field"


def test_annotations_reference_types(g):
    e = _edges(g, "REFERENCES")[("app/client.py::Client.run", "app/settings.py::Config")]
    assert e["count"] == 2  # parameter and return annotation


def test_a_local_that_shadows_a_module_name_is_not_a_read(g):
    assert not [k for k in _edges(g, "READS") if k[0] == "app/client.py::shadow"]


def test_typescript_reads_writes_fields_and_types(g):
    t = "web/box.ts::"
    reads, writes, refs = _edges(g, "READS"), _edges(g, "WRITES"), _edges(g, "REFERENCES")
    assert (t + "Box.grow", t + "LIMIT") in reads
    assert (t + "clamp", t + "LIMIT") in reads  # a parameter default is read
    assert (t + "Box.grow", t + "count") in writes  # count++
    assert (t + "Box.grow", t + "Box.size") in writes
    assert (t + "Box.grow", t + "Box.size") in reads
    assert (t + "Box.grow", t + "Shape") in refs
    assert not [k for k in reads if k[1].endswith("::local")]


def test_every_reference_edge_is_exact_and_cited(g):
    for e in g.edges:
        if e["type"] in ("READS", "WRITES", "REFERENCES"):
            assert e["method"] == "reference-resolver"
            assert e["evidence"]["line"] >= 1
            assert e["src"] in g.nodes and e["dst"] in g.nodes
    for key in ("reads", "writes", "references", "unresolved"):
        assert f"reference_edges_{key}" in g.stats


def test_off_by_default_adds_nothing(tmp_path):
    g = build(_repo(tmp_path))
    assert not [e for e in g.edges if e["type"] in ("READS", "WRITES", "REFERENCES")]
    assert not [n for n in g.nodes.values() if n.get("kind") in ("variable", "constant", "field")]
    assert not [k for k in g.stats if k.startswith("reference_edges")]


def test_cli_flag_manifest_and_incremental_match_full(tmp_path, capsys):
    repo = _repo(tmp_path)
    full, inc = tmp_path / "full", tmp_path / "inc"
    main(["build", str(repo), "-o", str(full), "--formats", "jsonl", "--reference-edges"])
    stats = json.loads(capsys.readouterr().out)["stats"]
    assert stats["reference_edges_reads"] > 0
    manifest = json.loads(artifact_path(full, "manifest.json").read_text(encoding="utf8"))
    assert {"READS", "WRITES", "REFERENCES"} <= set(manifest["edge_types"])

    # A cache written without the flag is not reused with it, and vice versa.
    main(["build", str(repo), "-o", str(inc), "--formats", "jsonl"])
    main(
        [
            "build",
            str(repo),
            "-o",
            str(inc),
            "--formats",
            "jsonl",
            "--incremental",
            "--reference-edges",
        ]
    )
    main(
        [
            "build",
            str(repo),
            "-o",
            str(inc),
            "--formats",
            "jsonl",
            "--incremental",
            "--reference-edges",
        ]
    )
    capsys.readouterr()
    for name in ("nodes.jsonl", "edges.jsonl", "chunks.jsonl", "stats.json"):
        assert artifact_path(full, name).read_bytes() == artifact_path(inc, name).read_bytes()

    plain = tmp_path / "plain"
    main(["build", str(repo), "-o", str(plain), "--formats", "jsonl"])
    capsys.readouterr()
    manifest = json.loads(artifact_path(plain, "manifest.json").read_text(encoding="utf8"))
    assert "READS" not in manifest["edge_types"]
