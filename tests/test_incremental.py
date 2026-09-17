"""`repo2graph build --incremental`: reuse parse results, recompute everything else.

The contract these tests pin is deliberately narrow and deliberately strong:

* A file whose bytes did not change is not re-parsed (`parse_source` is never
  called for it).
* *Nothing else* is reused. The global name index, CALLS confidences, INHERITS
  edges, entrypoint flags and reach counts are recomputed from the full symbol
  set on every build, incremental or not.

The second point is what makes the first safe, and it is why the headline test
here is byte-for-byte artifact equality against a full rebuild. Confidence is
`1/len(candidates)` over a *repo-global* name index, so adding or deleting a
symbol named `run` in one file changes the confidence of CALLS edges emitted
from files that did not change at all. Any test weaker than whole-artifact
equality would pass while that drifted -- which is exactly the "wrong in a way
nothing detects" failure mode incremental rebuild was cut for.
"""
import json

import pytest

from repo2graph import graph as graph_mod
from repo2graph.cli import main
from repo2graph.export import load_parse_cache, make_paths

FORMATS = "jsonl,overview"

# Two modules plus a caller, so there is a genuine cross-file CALLS edge whose
# confidence depends on how many repo-wide symbols share the callee's name.
FILES = {
    "pkg/__init__.py": "VERSION = '1.0'\n",
    "pkg/alpha.py": (
        "ALPHA_TABLE = {'a': 1, 'b': 2}\n\n\n"
        "def handle(payload):\n"
        "    return ALPHA_TABLE.get(payload)\n"
    ),
    "pkg/caller.py": (
        "from pkg.alpha import handle\n\n"
        "CALLER_TABLE = {'x': 1}\n\n\n"
        "def entry(payload):\n"
        "    return handle(payload)\n"
    ),
}


def write_repo(root, files=None):
    """Materialise `files` (default FILES) under `root/src`; returns the dir."""
    repo = root / "src"
    for rel, text in (FILES if files is None else files).items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf8", newline="\n") as fh:
            fh.write(text)
    return repo


def build(repo, out, incremental=False):
    """Run the CLI build, returning its parsed JSON report."""
    argv = ["build", str(repo), "-o", str(out), "--formats", FORMATS]
    if incremental:
        argv.append("--incremental")
    main(argv)
    return out


def artifact(out, name):
    """Read one artifact's bytes, for byte-identity comparisons."""
    return make_paths(out, name)[0].read_bytes()


ARTIFACTS = ("nodes.jsonl", "edges.jsonl", "chunks.jsonl", "stats.json",
             "index.state.json", "parse.cache.json")


def snapshot(out):
    """Every artifact that describes the graph, as raw bytes."""
    return {name: artifact(out, name) for name in ARTIFACTS}


class RecordingParser:
    """Wraps `parse_source` and records which languages/bodies it parsed."""

    def __init__(self, real):
        self.real = real
        self.calls = []

    def __call__(self, raw, lang):
        self.calls.append(raw)
        return self.real(raw, lang)


@pytest.fixture
def recorder(monkeypatch):
    rec = RecordingParser(graph_mod.parse_source)
    monkeypatch.setattr(graph_mod, "parse_source", rec)
    return rec


# ------------------------------------------------------------------ (a) ----

def test_unchanged_files_are_not_reparsed(tmp_path, recorder):
    """The whole point: a second incremental build parses nothing at all."""
    repo = write_repo(tmp_path)
    out = tmp_path / "idx"
    build(repo, out)
    assert recorder.calls, "the first build must actually parse"

    recorder.calls.clear()
    build(repo, out, incremental=True)
    assert recorder.calls == [], "unchanged files were re-parsed"


def test_added_file_is_the_only_one_parsed(tmp_path, recorder):
    """Adding a file re-parses that file and nothing else."""
    repo = write_repo(tmp_path)
    out = tmp_path / "idx"
    build(repo, out)

    added = "BETA_TABLE = {'q': 9}\n\n\ndef handle(payload):\n    return payload\n"
    (repo / "pkg" / "beta.py").write_text(added, encoding="utf8", newline="\n")

    recorder.calls.clear()
    report = json.loads(_run_capture(repo, out))
    assert recorder.calls == [added.encode("utf8")], recorder.calls
    assert report["incremental"] == {"cached": 3, "reparsed": 1}


def _run_capture(repo, out, capsys=None):
    """Run an incremental build and return the CLI's JSON report text."""
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        build(repo, out, incremental=True)
    return buf.getvalue()


def test_modified_file_is_reparsed(tmp_path, recorder):
    """Editing one file re-parses exactly that file."""
    repo = write_repo(tmp_path)
    out = tmp_path / "idx"
    build(repo, out)

    edited = FILES["pkg/alpha.py"] + "\n\ndef extra(x):\n    return x\n"
    (repo / "pkg" / "alpha.py").write_text(edited, encoding="utf8", newline="\n")

    recorder.calls.clear()
    build(repo, out, incremental=True)
    assert recorder.calls == [edited.encode("utf8")], recorder.calls


# ------------------------------------------------------------------ (b) ----

def _calls_edges(out):
    """{(src, dst): confidence} for every CALLS edge in the index."""
    text = artifact(out, "edges.jsonl").decode("utf8")
    edges = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e["type"] == "CALLS":
            edges[(e["src"], e["dst"])] = e.get("confidence")
    return edges


def test_adding_a_duplicate_name_elsewhere_lowers_confidence_repo_wide(tmp_path):
    """The cross-file case the BACKLOG named as the blocker.

    `caller.entry` calls `handle`. With one `handle` in the repo the edge is
    confidence 1.0. Adding a *second* `handle` in a file the caller never
    imports must drop that edge to 0.5 and add a second candidate edge -- even
    though `pkg/caller.py` itself did not change and is served from cache.
    """
    repo = write_repo(tmp_path)
    out = tmp_path / "idx"
    build(repo, out)

    src = "sym:pkg/caller.py::entry"
    before = _calls_edges(out)
    assert before[(src, "sym:pkg/alpha.py::handle")] == 1.0

    (repo / "pkg" / "beta.py").write_text(
        "BETA_TABLE = {'q': 9}\n\n\ndef handle(payload):\n    return payload\n",
        encoding="utf8", newline="\n")
    build(repo, out, incremental=True)

    after = _calls_edges(out)
    assert after[(src, "sym:pkg/alpha.py::handle")] == 0.5
    assert after[(src, "sym:pkg/beta.py::handle")] == 0.5


def test_deleting_a_file_removes_its_nodes_and_restores_confidence(tmp_path):
    """Deleting a symbol's file removes its nodes and re-raises confidences."""
    repo = write_repo(tmp_path)
    out = tmp_path / "idx"
    (repo / "pkg" / "beta.py").write_text(
        "BETA_TABLE = {'q': 9}\n\n\ndef handle(payload):\n    return payload\n",
        encoding="utf8", newline="\n")
    build(repo, out)

    src = "sym:pkg/caller.py::entry"
    assert _calls_edges(out)[(src, "sym:pkg/alpha.py::handle")] == 0.5

    (repo / "pkg" / "beta.py").unlink()
    build(repo, out, incremental=True)

    after = _calls_edges(out)
    # The deleted file's nodes are gone...
    nodes = artifact(out, "nodes.jsonl").decode("utf8")
    assert "pkg/beta.py" not in nodes
    # ...no dangling edge survives to it...
    assert not any("beta.py" in dst for _src, dst in after)
    # ...and the surviving candidate is unambiguous again.
    assert after[(src, "sym:pkg/alpha.py::handle")] == 1.0


def test_renaming_a_symbol_updates_cross_file_calls(tmp_path):
    """Renaming a callee in a changed file rewires the unchanged caller's edge."""
    repo = write_repo(tmp_path)
    out = tmp_path / "idx"
    build(repo, out)

    src = "sym:pkg/caller.py::entry"
    assert (src, "sym:pkg/alpha.py::handle") in _calls_edges(out)

    # `handle` becomes `process` in alpha.py, and caller.py is left untouched,
    # so the caller is served entirely from cache.
    (repo / "pkg" / "alpha.py").write_text(
        FILES["pkg/alpha.py"].replace("def handle(", "def process("),
        encoding="utf8", newline="\n")
    build(repo, out, incremental=True)

    after = _calls_edges(out)
    assert (src, "sym:pkg/alpha.py::handle") not in after
    # Nothing named `handle` remains, so the call resolves as external instead.
    edges = artifact(out, "edges.jsonl").decode("utf8")
    assert "external:handle" in edges


# ------------------------------------------------------------------ (c) ----

def test_entrypoint_flags_are_recomputed_not_spliced(tmp_path):
    """A newly added caller must clear the callee's stale entrypoint flag."""
    repo = write_repo(tmp_path, {
        "pkg/__init__.py": "VERSION = '1'\n",
        "pkg/lonely.py": "TABLE = {'a': 1}\n\n\ndef target(x):\n    return x\n",
    })
    out = tmp_path / "idx"
    build(repo, out)

    def entrypoints():
        text = artifact(out, "nodes.jsonl").decode("utf8")
        return {json.loads(line)["id"] for line in text.splitlines()
                if line.strip() and json.loads(line).get("entrypoint")}

    assert "sym:pkg/lonely.py::target" in entrypoints()

    (repo / "pkg" / "user.py").write_text(
        "from pkg.lonely import target\n\nUSER_TABLE = {'z': 0}\n\n\n"
        "def drive(x):\n    return target(x)\n",
        encoding="utf8", newline="\n")
    build(repo, out, incremental=True)

    # `target` is now called, so it is no longer a root -- a spliced graph that
    # kept the old flag would fail here.
    assert "sym:pkg/lonely.py::target" not in entrypoints()
    assert "sym:pkg/user.py::drive" in entrypoints()


# ------------------------------------------------------------------ (f) ----

@pytest.mark.parametrize("mutate", [
    pytest.param(lambda repo: None, id="no-change"),
    pytest.param(lambda repo: (repo / "pkg" / "beta.py").write_text(
        "BETA = 1\n\n\ndef handle(x):\n    return x\n",
        encoding="utf8", newline="\n"), id="added"),
    pytest.param(lambda repo: (repo / "pkg" / "alpha.py").write_text(
        FILES["pkg/alpha.py"] + "\n\ndef extra(y):\n    return y\n",
        encoding="utf8", newline="\n"), id="modified"),
    pytest.param(lambda repo: (repo / "pkg" / "caller.py").unlink(), id="deleted"),
])
def test_incremental_is_byte_identical_to_a_full_rebuild(tmp_path, mutate):
    """The acceptance test: same bytes out, whatever route got there.

    Two checkouts of the same content, mutated identically. One is built fresh;
    the other is built once, mutated, then rebuilt incrementally. Every artifact
    must match byte for byte -- including `stats.json`, which is why the
    cached/re-parsed tallies live on `Graph.incremental` and not in `stats`.
    """
    inc_repo = write_repo(tmp_path / "a")
    inc_out = tmp_path / "a-idx"
    build(inc_repo, inc_out)          # seed the cache
    mutate(inc_repo)
    build(inc_repo, inc_out, incremental=True)

    full_repo = write_repo(tmp_path / "b")
    full_out = tmp_path / "b-idx"
    mutate(full_repo)
    build(full_repo, full_out)        # never incremental

    assert snapshot(inc_out) == snapshot(full_out)


def test_incremental_against_a_repo_with_no_index_is_a_full_build(tmp_path):
    """`--incremental` on a cold directory works; it simply has nothing to reuse."""
    repo = write_repo(tmp_path)
    out = tmp_path / "idx"
    report = json.loads(_run_capture(repo, out))
    assert report["incremental"] == {"cached": 0, "reparsed": len(FILES)}
    assert snapshot(out)["nodes.jsonl"]


# --------------------------------------------------------------- cache ----

def test_a_corrupt_cache_falls_back_to_a_full_build(tmp_path, recorder):
    """Garbage in `parse.cache.json` must cost a re-parse, never an exception."""
    repo = write_repo(tmp_path)
    out = tmp_path / "idx"
    build(repo, out)
    make_paths(out, "parse.cache.json")[0].write_text("{not json", encoding="utf8")

    assert load_parse_cache(out) == {}
    recorder.calls.clear()
    build(repo, out, incremental=True)
    assert len(recorder.calls) == len(FILES), "a corrupt cache must force a full re-parse"


def test_a_future_cache_format_is_ignored(tmp_path):
    """A cache from a newer repo2graph is dropped rather than half-read."""
    repo = write_repo(tmp_path)
    out = tmp_path / "idx"
    build(repo, out)
    path = make_paths(out, "parse.cache.json")[0]
    data = json.loads(path.read_text(encoding="utf8"))
    data["cache_format"] = 999
    path.write_text(json.dumps(data), encoding="utf8")

    assert load_parse_cache(out) == {}


def test_same_bytes_different_language_is_a_cache_miss(tmp_path, recorder):
    """A rename that changes the language must not reuse the old parse.

    `a.py` and `a.rs` can hold byte-identical content and parse to completely
    different symbol sets, so the content hash alone is not a sufficient key.
    """
    body = "fn main() {}\n"
    repo = write_repo(tmp_path, {"a.py": body})
    out = tmp_path / "idx"
    build(repo, out)

    (repo / "a.py").unlink()
    (repo / "a.rs").write_text(body, encoding="utf8", newline="\n")
    recorder.calls.clear()
    build(repo, out, incremental=True)
    assert recorder.calls == [body.encode("utf8")], recorder.calls
