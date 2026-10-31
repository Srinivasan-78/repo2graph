"""`build --scip`: exact CALLS / INHERITS from a SCIP index (#380).

Three layers, mirroring `repo2graph/scip.py`:

* the hand-rolled protobuf reader, against the checked-in index scip-python
  wrote for `tests/fixtures/scip/repo` (see the README beside it), a small
  writer below for crafted indexes, and property tests that feed it arbitrary
  and mutated bytes -- the file is untrusted input, so "raises ScipError or
  returns" is the whole contract for garbage;
* the symbol-identity mapping and the merge, on that fixture: the heuristic
  resolver gets `c.area()` ambiguous and both `Shape` bases wrong, SCIP gets
  them exact, and the one file outside the index keeps its heuristic edges;
* the build invariants: no artifact changes without `--scip`, and full and
  `--incremental` builds with it are byte-identical.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from repo2graph import scip as scip_mod
from repo2graph.cli import main
from repo2graph.config import ConfigError, load
from repo2graph.edgemeta import METHOD_SCIP, METHODS
from repo2graph.export import EDGE_FIELDS, make_paths
from repo2graph.graph import build
from repo2graph.parse import BuildConfig
from repo2graph.scip import (
    STAT_KEYS,
    ScipError,
    ScipResolver,
    decode_index,
    index_path,
    parse_descriptors,
    read_index_bytes,
    symbol_name,
)

FIXTURE = Path(__file__).parent / "fixtures" / "scip"
FIXTURE_REPO = FIXTURE / "repo"
FIXTURE_INDEX = FIXTURE / "index.scip"
COVERED = "geometry/"
UNCOVERED = "scripts/uncovered.py"

# Same shape as tests/test_properties.py's profile -- derandomized, so a green
# run is green everywhere -- applied per test rather than loaded globally.
PROPERTY = settings(
    max_examples=100,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# ------------------------------------------------------------ a tiny writer --
# Just enough of the wire format to craft indexes the reader must accept or
# refuse. Field numbers are scip.proto's.


def uvarint(n: int) -> bytes:
    out = bytearray()
    n &= (1 << 64) - 1  # int32/int64 negatives travel sign-extended
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


def tag(number: int, wire: int) -> bytes:
    return uvarint(number << 3 | wire)


def len_field(number: int, payload: bytes) -> bytes:
    return tag(number, 2) + uvarint(len(payload)) + payload


def str_field(number: int, s: str) -> bytes:
    return len_field(number, s.encode("utf-8"))


def int_field(number: int, v: int) -> bytes:
    return tag(number, 0) + uvarint(v)


def occurrence(line, start, end, symbol, roles=0, form="packed"):
    """`line` is 0-based, as in SCIP. `form`: packed / unpacked / single / multi."""
    if form == "packed":
        rng = len_field(1, b"".join(uvarint(v) for v in (line, start, end)))
    elif form == "unpacked":
        rng = b"".join(int_field(1, v) for v in (line, start, line, end))
    elif form == "single":
        rng = len_field(8, int_field(1, line) + int_field(2, start) + int_field(3, end))
    else:
        rng = len_field(
            9, int_field(1, line) + int_field(2, start) + int_field(3, line) + int_field(4, end)
        )
    body = rng + str_field(2, symbol)
    if roles:
        body += int_field(3, roles)
    return len_field(2, body)


def symbol_information(symbol, implementations=(), display_name=""):
    body = str_field(1, symbol)
    for rel in implementations:
        body += len_field(4, str_field(1, rel) + int_field(3, 1))
    if display_name:
        body += str_field(6, display_name)
    return len_field(3, body)


def document(path, *parts, language="python"):
    return len_field(2, str_field(1, path) + b"".join(parts) + str_field(4, language))


def index(*documents):
    metadata = len_field(1, len_field(2, str_field(1, "test-writer") + str_field(2, "0")))
    return metadata + b"".join(documents)


def sym(module: str, desc: str) -> str:
    return f"scip-python python demo 0.1 `{module}`/{desc}"


DEF = scip_mod.ROLE_DEFINITION


# ---------------------------------------------------------- helpers: builds --


def copy_fixture(dest: Path) -> Path:
    repo = dest / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    return repo


def cli_build(repo: Path, out: Path, *extra: str) -> Path:
    assert main(["build", str(repo), "-o", str(out), "--formats", "jsonl", *extra]) in (0, None)
    return out


def edges_of(out: Path) -> list[dict]:
    text = make_paths(out, "edges.jsonl")[0].read_text(encoding="utf8")
    return [json.loads(line) for line in text.splitlines()]


def stats_of(out: Path) -> dict:
    return json.loads(make_paths(out, "stats.json")[0].read_text(encoding="utf8"))


ARTIFACTS = (
    "nodes.jsonl",
    "edges.jsonl",
    "chunks.jsonl",
    "stats.json",
    "index.state.json",
    "parse.cache.json",
)


def snapshot(out: Path) -> dict[str, bytes]:
    return {name: make_paths(out, name)[0].read_bytes() for name in ARTIFACTS}


def ambiguous_from(edges: list[dict], prefix: str) -> list[dict]:
    return [
        e
        for e in edges
        if e["type"] == "CALLS" and e.get("ambiguous") and e["src"].startswith(f"sym:{prefix}")
    ]


def one(edges: list[dict], **want) -> dict:
    hits = [e for e in edges if all(e.get(k) == v for k, v in want.items())]
    assert len(hits) == 1, (want, hits)
    return hits[0]


# ------------------------------------------------------- reader: real index --


def test_the_checked_in_index_decodes_to_the_fixture_package():
    idx = decode_index(read_index_bytes(FIXTURE_INDEX))
    assert idx.paths == [
        "geometry/__init__.py",
        "geometry/legacy.py",
        "geometry/modern.py",
        "geometry/report.py",
        "geometry/shapes.py",
    ]
    shapes = idx.documents["geometry/shapes.py"]
    defs = {symbol_name(s)[0] for _l, _c, s, roles in shapes.occurrences if roles & DEF}
    assert {"Circle", "Square", "area", "unit", "retired"} <= defs
    # scip-python records a class's bases as is_implementation relationships.
    circle = next(s for s in shapes.relationships if s.endswith("`geometry.shapes`/Circle#"))
    assert [symbol_name(r)[0] for r, _f in shapes.relationships[circle]] == ["Shape"]


def test_unwanted_documents_are_framed_but_not_decoded():
    data = read_index_bytes(FIXTURE_INDEX)
    idx = decode_index(data, wanted={"geometry/report.py"})
    assert len(idx.paths) == 5
    assert list(idx.documents) == ["geometry/report.py"]


@pytest.mark.parametrize("form", ["packed", "unpacked", "single", "multi"])
def test_every_range_encoding_gives_the_same_occurrence(form):
    idx = decode_index(index(document("a.py", occurrence(4, 2, 7, "s", DEF, form=form))))
    assert idx.documents["a.py"].occurrences == [(5, 2, "s", DEF)]


def test_a_typed_range_takes_precedence_over_the_deprecated_one():
    occ = len_field(
        2,
        len_field(1, b"".join(uvarint(v) for v in (1, 0, 3)))
        + len_field(8, int_field(1, 9) + int_field(2, 4) + int_field(3, 6))
        + str_field(2, "s"),
    )
    assert decode_index(index(document("a.py", occ))).documents["a.py"].occurrences == [
        (10, 4, "s", 0)
    ]


@pytest.mark.parametrize(
    "occ",
    [
        occurrence(-1, 0, 3, "s"),  # negative line
        len_field(2, len_field(1, uvarint(1) + uvarint(2)) + str_field(2, "s")),  # 2 elements
        occurrence(1, 0, 3, ""),  # no symbol
    ],
    ids=["negative-line", "short-range", "no-symbol"],
)
def test_an_unusable_occurrence_is_skipped_not_fatal(occ):
    idx = decode_index(index(document("a.py", occ, occurrence(0, 0, 1, "t"))))
    assert idx.documents["a.py"].occurrences == [(1, 0, "t", 0)]


def test_a_document_split_across_records_is_merged():
    data = index(
        document("a.py", occurrence(0, 0, 1, "x")), document("./a.py", occurrence(1, 0, 1, "y"))
    )
    idx = decode_index(data)
    assert idx.paths == ["a.py", "a.py"]
    assert [o[2] for o in idx.documents["a.py"].occurrences] == ["x", "y"]


@pytest.mark.parametrize(
    "data, reason",
    [
        (b"\x12", "truncated varint"),  # a length that never arrives
        (b"\x12\x05ab", "runs past the end"),
        (b"\x80" * 11, "longer than 10 bytes"),
        (b"\x08" + b"\xff" * 9 + b"\x02", "overflows 64 bits"),
        (b"\x13", "group wire type"),  # field 2, wire type 3
        (b"\x16", "invalid wire type 6"),
        (b"\x02\x00", "invalid field number 0"),
        (b"\x15\x00\x00", "fixed-width field runs past"),
        (b"\x10\x01", "document has wire type 0"),
        (len_field(2, b"\x0a\x02\xff\xfe"), "not valid UTF-8"),
        (len_field(2, len_field(2, b"\x0a\x05\x80\x80\x80\x80\x80")), "truncated"),
        (len_field(2, len_field(2, len_field(1, uvarint(1 << 40)))), "int32 field out of range"),
        (
            len_field(2, len_field(2, len_field(1, b"".join(uvarint(1) for _ in range(5))))),
            "more than four",
        ),
    ],
)
def test_malformed_bytes_raise_scip_error_naming_the_problem(data, reason):
    with pytest.raises(ScipError, match=reason):
        decode_index(data)


def test_the_size_cap_is_checked_before_reading(tmp_path, monkeypatch):
    big = tmp_path / "index.scip"
    big.write_bytes(b"\x00" * 64)
    with pytest.raises(ScipError, match="over the 63-byte limit"):
        read_index_bytes(big, max_bytes=63)
    monkeypatch.setattr(scip_mod, "MAX_INDEX_BYTES", 63)
    with pytest.raises(ScipError, match="over the 63-byte limit"):
        read_index_bytes(big)
    with pytest.raises(ScipError, match="over the 63-byte limit"):
        decode_index(b"\x00" * 64)


def test_a_directory_is_not_an_index(tmp_path):
    with pytest.raises(ScipError, match="not a regular file"):
        read_index_bytes(tmp_path)


def test_occurrence_and_document_caps(monkeypatch):
    two = index(document("a.py", occurrence(0, 0, 1, "x"), occurrence(1, 0, 1, "y")))
    monkeypatch.setattr(scip_mod, "MAX_OCCURRENCES", 1)
    with pytest.raises(ScipError, match="more than 1 occurrences"):
        decode_index(two)
    decode_index(two, wanted=set())  # skipped documents are not counted
    monkeypatch.setattr(scip_mod, "MAX_DOCUMENTS", 1)
    with pytest.raises(ScipError, match="more than 1 documents"):
        decode_index(index(document("a.py"), document("b.py")))


def test_an_oversized_string_is_refused(monkeypatch):
    monkeypatch.setattr(scip_mod, "MAX_STRING_BYTES", 4)
    with pytest.raises(ScipError, match="relative_path is longer than 4 bytes"):
        decode_index(index(document("abcde.py")))


# --------------------------------------------------- reader: property tests --


@PROPERTY
@given(st.binary(max_size=400))
def test_arbitrary_bytes_decode_or_raise_scip_error(data):
    try:
        decode_index(data)
    except ScipError:
        pass


_FIXTURE_BYTES = FIXTURE_INDEX.read_bytes()


@PROPERTY
@given(
    st.lists(
        st.tuples(
            st.sampled_from(["flip", "insert", "delete", "truncate"]),
            st.integers(0, len(_FIXTURE_BYTES) - 1),
            st.integers(0, 255),
        ),
        min_size=1,
        max_size=4,
    )
)
def test_a_mutated_real_index_decodes_or_raises_scip_error(edits):
    data = bytearray(_FIXTURE_BYTES)
    for op, at, byte in edits:
        at = min(at, max(len(data) - 1, 0))
        if op == "flip" and data:
            data[at] ^= byte or 1
        elif op == "insert":
            data[at:at] = bytes([byte])
        elif op == "delete" and data:
            del data[at]
        else:
            del data[at:]
    try:
        decode_index(bytes(data), wanted={"geometry/report.py", "geometry/shapes.py"})
    except ScipError:
        pass


@PROPERTY
@given(st.integers(0, (1 << 64) - 1))
def test_varints_round_trip_to_64_bits(n):
    raw = uvarint(n)
    assert len(raw) <= 10
    assert scip_mod._varint(raw, 0, len(raw)) == (n, len(raw))


_OCCS = st.lists(
    st.tuples(
        st.integers(0, 2**31 - 2),
        st.integers(0, 2**31 - 2),
        st.text(min_size=1, max_size=12),
        st.integers(0, 0x7F),
        st.sampled_from(["packed", "unpacked", "single", "multi"]),
    ),
    max_size=6,
)


@PROPERTY
@given(
    st.dictionaries(
        st.text(min_size=1, max_size=8).filter(lambda p: "\\" not in p and not p.startswith("./")),
        _OCCS,
        max_size=4,
    )
)
def test_written_documents_read_back_exactly(docs):
    data = index(
        *(
            document(p, *(occurrence(ln, c, c + 1, s, r, form=f) for ln, c, s, r, f in occs))
            for p, occs in docs.items()
        )
    )
    idx = decode_index(data)
    assert sorted(idx.documents) == sorted(docs)
    for p, occs in docs.items():
        assert idx.documents[p].occurrences == [(ln + 1, c, s, r) for ln, c, s, r, _f in occs]


_SIMPLE = "abcXYZ019_+-$"
_NAMES = st.one_of(
    st.text(alphabet=_SIMPLE, min_size=1, max_size=6), st.text(min_size=1, max_size=6)
)
_KINDS = st.sampled_from(
    ["namespace", "type", "term", "meta", "macro", "method", "parameter", "type-parameter"]
)


def _escape(name: str) -> str:
    if name and all(c in _SIMPLE for c in name):
        return name
    return "`" + name.replace("`", "``") + "`"


def _descriptor(name: str, kind: str, disambiguator: str) -> str:
    n = _escape(name)
    return {
        "namespace": f"{n}/",
        "type": f"{n}#",
        "term": f"{n}.",
        "meta": f"{n}:",
        "macro": f"{n}!",
        "method": f"{n}({disambiguator}).",
        "parameter": f"({n})",
        "type-parameter": f"[{n}]",
    }[kind]


@PROPERTY
@given(
    st.lists(
        st.text(min_size=1, max_size=5).filter(lambda t: t != "local"), min_size=4, max_size=4
    ),
    st.lists(
        st.tuples(_NAMES, _KINDS, st.text(alphabet=_SIMPLE, max_size=3)), min_size=1, max_size=5
    ),
)
def test_descriptors_round_trip(package, descriptors):
    head = " ".join(t.replace(" ", "  ") for t in package)
    symbol = head + " " + "".join(_descriptor(*d) for d in descriptors)
    if symbol.startswith("local "):
        return
    assert parse_descriptors(symbol) == [(n, k) for n, k, _d in descriptors]


@PROPERTY
@given(st.text(max_size=60))
def test_descriptor_parsing_never_raises(text):
    result = parse_descriptors(text)
    assert result is None or all(isinstance(n, str) and k for n, k in result)


def test_local_symbols_are_named_only_through_display_name():
    assert parse_descriptors("local 3") is None
    assert symbol_name("local 3") == ("", "")
    assert symbol_name("local 3", {"local 3": "helper"}) == ("helper", "local")
    assert symbol_name(sym("m", "A#run().")) == ("run", "method")
    assert symbol_name(sym("m", "A#run().(self)")) == ("self", "parameter")


# ------------------------------------------------- the merge, on the fixture --


@pytest.fixture(scope="module")
def heuristic_and_scip(tmp_path_factory):
    base = tmp_path_factory.mktemp("scip-fixture")
    repo = copy_fixture(base)
    plain = edges_of(cli_build(repo, base / "plain"))
    exact_out = cli_build(repo, base / "exact", "--scip", str(FIXTURE_INDEX))
    return plain, edges_of(exact_out), stats_of(base / "plain"), stats_of(exact_out)


def test_the_heuristic_alone_is_ambiguous_on_the_fixture(heuristic_and_scip):
    """The premise: without SCIP the fixture's typed-receiver calls fan out."""
    plain, _exact, plain_stats, _ = heuristic_and_scip
    assert len(ambiguous_from(plain, COVERED)) >= 2
    assert plain_stats["calls_ambiguous"] == 5
    # ...and both shapes are attached to the wrong `Shape` at confidence 1.0.
    for cls in ("Circle", "Square"):
        e = one(plain, src=f"sym:geometry/shapes.py::{cls}", type="INHERITS")
        assert e["dst"] == "sym:geometry/legacy.py::Shape" and e["confidence"] == 1.0


def test_scip_leaves_no_ambiguous_call_in_a_covered_file(heuristic_and_scip):
    _plain, exact, plain_stats, stats = heuristic_and_scip
    assert ambiguous_from(exact, COVERED) == []
    assert stats["scip_calls_ambiguous"] == 0
    # The one ambiguous call left is the uncovered file's.
    assert stats["calls_ambiguous"] == 1
    assert plain_stats["calls_ambiguous"] - stats["calls_ambiguous"] == 4


def test_typed_receivers_resolve_to_the_exact_method(heuristic_and_scip):
    _plain, exact, _, _ = heuristic_and_scip
    src = "sym:geometry/report.py::both_areas"
    circle = one(exact, src=src, dst="sym:geometry/shapes.py::Circle.area")
    square = one(exact, src=src, dst="sym:geometry/shapes.py::Square.area")
    for e in (circle, square):
        assert e["method"] == METHOD_SCIP == e["resolution_kind"]
        assert e["confidence"] == 1.0 and e["count"] == 1 and "ambiguous" not in e
        assert e["evidence"] == {"path": "geometry/report.py", "line": 13}
    one(exact, src="sym:geometry/report.py::circle_area", dst="sym:geometry/shapes.py::Circle.area")
    # Inherited through Circle, defined on the base that SCIP knows it is.
    one(exact, src="sym:geometry/report.py::labels", dst="sym:geometry/modern.py::Shape.describe")


def test_scip_corrects_the_inheritance_and_super_calls(heuristic_and_scip):
    plain, exact, _, stats = heuristic_and_scip
    for cls in ("Circle", "Square"):
        e = one(exact, src=f"sym:geometry/shapes.py::{cls}", type="INHERITS")
        assert e["dst"] == "sym:geometry/modern.py::Shape"
        assert e["method"] == METHOD_SCIP and e["confidence"] == 1.0
        assert e["raw_base"] == "modern.Shape" and e["subtype"] == "INHERITS"
        # The heuristic missed the base's __init__ because it had the wrong base.
        init = f"sym:geometry/shapes.py::{cls}.__init__"
        assert one(plain, src=init, type="CALLS_EXTERNAL", dst="external:__init__")
        one(exact, src=init, type="CALLS", dst="sym:geometry/modern.py::Shape.__init__")
    assert stats["scip_bases_resolved"] == 2 and stats["scip_bases_heuristic"] == 0


def test_externals_from_scip_are_calls_external(heuristic_and_scip):
    _plain, exact, _, stats = heuristic_and_scip
    e = one(exact, src="sym:geometry/report.py::summary", dst="external:print")
    assert e["type"] == "CALLS_EXTERNAL" and e["method"] == METHOD_SCIP
    assert e["confidence"] == 1.0 and e["candidate_count"] == 0
    assert stats["scip_call_sites_external"] == 4  # print, super x2, @staticmethod


def test_sites_scip_cannot_name_keep_their_heuristic_edge(heuristic_and_scip):
    plain, exact, _, stats = heuristic_and_scip
    # `fn()` calls a parameter; scip-python names `len` inside an f-string as
    # an anonymous local. Neither is an answer, so neither edge moves.
    for src, dst in (
        ("sym:geometry/report.py::apply", "external:fn"),
        ("sym:geometry/report.py::summary", "external:len"),
    ):
        assert one(exact, src=src, dst=dst) == one(plain, src=src, dst=dst)
    assert stats["scip_call_sites_heuristic"] == 2


def test_an_uncovered_file_keeps_its_heuristic_edges_unchanged(heuristic_and_scip):
    plain, exact, _, stats = heuristic_and_scip
    mine = [e for e in plain if e["src"].startswith(f"sym:{UNCOVERED}")]
    assert mine and [e for e in exact if e["src"].startswith(f"sym:{UNCOVERED}")] == mine
    assert {e["confidence"] for e in mine} == {0.5}
    assert stats["scip_files_uncovered"] == 1 and stats["scip_call_sites_uncovered"] == 1


def test_coverage_is_reported_only_when_asked_for(heuristic_and_scip):
    _plain, _exact, plain_stats, stats = heuristic_and_scip
    assert not [k for k in plain_stats if k.startswith("scip")]
    assert [k for k in stats if k.startswith("scip")] == list(STAT_KEYS)
    assert stats["scip_documents"] == 5 and stats["scip_files_covered"] == 5
    assert stats["scip_symbols_mapped"] > 0
    assert stats["scip_call_sites_resolved"] == 17


def test_every_edge_method_is_a_known_one(heuristic_and_scip):
    _plain, exact, _, _ = heuristic_and_scip
    assert METHOD_SCIP in METHODS
    assert {e["method"].split("/", 1)[0] for e in exact} <= set(METHODS)


def test_the_manifest_explains_scip_only_when_it_is_used(tmp_path):
    repo = copy_fixture(tmp_path)
    plain = cli_build(repo, tmp_path / "plain")
    exact = cli_build(repo, tmp_path / "exact", "--scip", str(FIXTURE_INDEX))

    def fields(out):
        return json.loads(make_paths(out, "manifest.json")[0].read_text("utf8"))["edge_fields"]

    assert fields(plain) == EDGE_FIELDS
    assert "scip" in fields(exact)["method"] and "scip" not in EDGE_FIELDS["method"]


# ---------------------------------------------------------- build invariants --


def test_without_scip_nothing_is_consulted_or_recorded(tmp_path, monkeypatch):
    calls = []
    real = ScipResolver.for_build.__func__

    def spy(cls, source, g, parsed):
        calls.append(source)
        return real(cls, source, g, parsed)

    monkeypatch.setattr(ScipResolver, "for_build", classmethod(spy))
    g = build(copy_fixture(tmp_path), jobs=1, config=BuildConfig())
    assert calls == [None]
    assert not [k for k in g.stats if k.startswith("scip")]
    assert all(e["method"] != METHOD_SCIP for e in g.edges)


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda repo: None, id="no-change"),
        pytest.param(
            lambda repo: (repo / "scripts" / "extra.py").write_text(
                "from geometry.shapes import Circle\n\n\ndef go(c: Circle):\n    return c.area()\n",
                encoding="utf8",
                newline="\n",
            ),
            id="uncovered-added",
        ),
        pytest.param(
            # The index is now stale for this file: both builds must still agree.
            lambda repo: (repo / "geometry" / "report.py").write_text(
                "\n" + (FIXTURE_REPO / "geometry" / "report.py").read_text("utf8"),
                encoding="utf8",
                newline="\n",
            ),
            id="covered-shifted",
        ),
    ],
)
def test_full_and_incremental_builds_with_scip_are_byte_identical(tmp_path, mutate):
    flag = ("--scip", str(FIXTURE_INDEX))
    inc_repo = copy_fixture(tmp_path / "a")
    inc_out = cli_build(inc_repo, tmp_path / "a-idx", *flag)
    mutate(inc_repo)
    cli_build(inc_repo, inc_out, "--incremental", *flag)

    full_repo = copy_fixture(tmp_path / "b")
    mutate(full_repo)
    full_out = cli_build(full_repo, tmp_path / "b-idx", *flag)
    assert snapshot(inc_out) == snapshot(full_out)


def _rejected(capsys) -> dict:
    err = capsys.readouterr().err
    events = [json.loads(line) for line in err.splitlines() if line.startswith("{")]
    hit = [e for e in events if e.get("event") == "scip_index_rejected"]
    assert len(hit) == 1 and hit[0]["level"] == "warning", err
    assert "not used" in err
    return hit[0]


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(_FIXTURE_BYTES[: len(_FIXTURE_BYTES) // 2], id="truncated"),
        pytest.param(b"\xff" * 64, id="garbage"),
        pytest.param(b"\x13" + _FIXTURE_BYTES, id="group-wire-type"),
    ],
)
def test_a_malformed_index_warns_and_keeps_heuristic_edges(tmp_path, capsys, payload):
    repo = copy_fixture(tmp_path)
    bad = tmp_path / "bad.scip"
    bad.write_bytes(payload)
    plain = edges_of(cli_build(repo, tmp_path / "plain"))
    capsys.readouterr()
    out = cli_build(repo, tmp_path / "bad", "--scip", str(bad))
    event = _rejected(capsys)
    assert edges_of(out) == plain
    stats = stats_of(out)
    assert stats["scip_error"] == event["reason"]
    assert stats["scip_files_covered"] == 0 and stats["calls_ambiguous"] == 5


def test_an_oversized_index_is_refused_before_it_is_read(tmp_path, capsys, monkeypatch):
    repo = copy_fixture(tmp_path)
    monkeypatch.setattr(scip_mod, "MAX_INDEX_BYTES", 1024)
    out = cli_build(repo, tmp_path / "big", "--scip", str(FIXTURE_INDEX))
    assert "over the 1024-byte limit" in _rejected(capsys)["reason"]
    assert not [e for e in edges_of(out) if e["method"] == METHOD_SCIP]


def test_a_missing_index_warns_without_leaking_its_path_into_stats(tmp_path, capsys):
    repo = copy_fixture(tmp_path)
    missing = tmp_path / "secret-location" / "index.scip"
    out = cli_build(repo, tmp_path / "out", "--scip", str(missing))
    _rejected(capsys)
    reason = stats_of(out)["scip_error"]
    assert reason and "secret-location" not in reason


def test_an_index_for_another_root_is_noted(tmp_path, capsys):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO / "geometry", repo)  # paths now lack "geometry/"
    out = cli_build(repo, tmp_path / "out", "--scip", str(FIXTURE_INDEX))
    assert "none is a parsed file of this repository" in capsys.readouterr().err
    assert stats_of(out)["scip_files_covered"] == 0


# ------------------------------------------------- crafted indexes: the merge --


def _crafted(
    tmp_path: Path, source: str, *occs: bytes, infos: bytes = b"", uncovered=None
) -> tuple:
    """Build `m.py` = `source` (plus `uncovered` files the index leaves out)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text(source, encoding="utf8", newline="\n")
    for rel, text in (uncovered or {}).items():
        (repo / rel).write_text(text, encoding="utf8", newline="\n")
    idx = tmp_path / "index.scip"
    idx.write_bytes(index(document("m.py", *occs, infos)))
    out = cli_build(repo, tmp_path / "out", "--scip", str(idx))
    return edges_of(out), stats_of(out)


TWO_RUNS = (
    "class A:\n"  # 1
    "    def run(self):\n"  # 2
    "        return 1\n"  # 3
    "\n"  # 4
    "class B:\n"  # 5
    "    def run(self):\n"  # 6
    "        return 2\n"  # 7
    "\n"  # 8
    "def go(a, b):\n"  # 9
    "    return a.run() + b.run()\n"  # 10
    "\n"  # 11
    "def again(a):\n"  # 12
    "    a.run()\n"  # 13
    "    return a.run()\n"  # 14
)
A_RUN, B_RUN = sym("m", "A#run()."), sym("m", "B#run().")
DEFS = (
    occurrence(0, 6, 7, sym("m", "A#"), DEF),
    occurrence(1, 8, 11, A_RUN, DEF),
    occurrence(4, 6, 7, sym("m", "B#"), DEF),
    occurrence(5, 8, 11, B_RUN, DEF),
    occurrence(8, 4, 6, sym("m", "go()."), DEF),
    occurrence(11, 4, 9, sym("m", "again()."), DEF),
)


def test_two_same_named_calls_on_one_line_pair_up_by_column(tmp_path):
    edges, stats = _crafted(
        tmp_path,
        TWO_RUNS,
        *DEFS,
        occurrence(9, 13, 16, A_RUN),
        occurrence(9, 23, 26, B_RUN),
    )
    targets = {e["dst"] for e in edges if e["src"] == "sym:m.py::go" and e["type"] == "CALLS"}
    assert targets == {"sym:m.py::A.run", "sym:m.py::B.run"}
    assert not [e for e in edges if e["src"] == "sym:m.py::go" and e.get("ambiguous")]
    # again()'s two sites have no occurrence here: its one fan-out remains.
    assert stats["scip_calls_ambiguous"] == 1 and stats["scip_call_sites_resolved"] == 2


def test_a_partly_covered_name_splits_its_count(tmp_path):
    # Line 13's call has an occurrence, line 14's does not: one exact edge with
    # count 1, and the heuristic fan-out for the other site citing line 14.
    edges, stats = _crafted(tmp_path, TWO_RUNS, *DEFS, occurrence(12, 6, 9, A_RUN))
    mine = [e for e in edges if e["src"] == "sym:m.py::again"]
    exact = one(mine, method=METHOD_SCIP)
    assert exact["dst"] == "sym:m.py::A.run" and exact["count"] == 1
    assert exact["evidence"]["line"] == 13
    rest = [e for e in mine if e["method"] != METHOD_SCIP]
    assert {e["dst"] for e in rest} == {"sym:m.py::B.run"}  # A.run's edge already exists
    assert all(e["evidence"]["line"] == 14 and e["count"] == 1 for e in rest)
    assert stats["scip_call_sites_resolved"] == 1
    assert stats["scip_call_sites_heuristic"] == 3  # line 14, and both of go()'s
    assert stats["scip_calls_ambiguous"] == 2


def test_a_symbol_defined_twice_is_not_mapped(tmp_path):
    source = (
        "import sys\n"  # 1
        "if sys.argv:\n"  # 2
        "    def f():\n"  # 3
        "        return 1\n"  # 4
        "else:\n"  # 5
        "    def f():\n"  # 6
        "        return 2\n"  # 7
        "\n"  # 8
        "def g():\n"  # 9
        "    return f()\n"  # 10
    )
    f = sym("m", "f().")
    edges, stats = _crafted(
        tmp_path,
        source,
        occurrence(2, 8, 9, f, DEF),
        occurrence(5, 8, 9, f, DEF),
        occurrence(8, 4, 5, sym("m", "g()."), DEF),
        occurrence(9, 11, 12, f),
    )
    mine = [e for e in edges if e["src"] == "sym:m.py::g"]
    assert mine and all(e["method"] != METHOD_SCIP for e in mine)
    assert stats["scip_call_sites_heuristic"] == 1


def test_a_local_symbol_maps_through_its_display_name(tmp_path):
    source = "def outer():\n    def inner():\n        return 1\n    return inner()\n"
    edges, _ = _crafted(
        tmp_path,
        source,
        occurrence(0, 4, 9, sym("m", "outer()."), DEF),
        occurrence(1, 8, 13, "local 0", DEF),
        occurrence(3, 11, 16, "local 0"),
        infos=symbol_information("local 0", display_name="inner"),
    )
    e = one(edges, src="sym:m.py::outer", type="CALLS")
    assert e["dst"] == "sym:m.py::outer.inner" and e["method"] == METHOD_SCIP


def test_a_base_from_the_class_header_when_there_is_no_relationship(tmp_path):
    source = (
        "class Base:\n    pass\n\nclass Base2:\n    pass\n\nclass Kid(\n    Base,\n):\n    x = 1\n"
    )
    edges, stats = _crafted(
        tmp_path,
        source,
        occurrence(0, 6, 10, sym("m", "Base#"), DEF),
        occurrence(3, 6, 11, sym("m", "Base2#"), DEF),
        occurrence(6, 6, 9, sym("m", "Kid#"), DEF),
        occurrence(7, 4, 8, sym("m", "Base#")),
        occurrence(9, 4, 5, sym("m", "Kid#x."), DEF),
    )
    e = one(edges, src="sym:m.py::Kid", type="INHERITS")
    assert e["dst"] == "sym:m.py::Base" and e["method"] == METHOD_SCIP
    assert e["evidence"] == {"path": "m.py", "line": 7}
    assert stats["scip_bases_resolved"] == 1


def test_an_external_base_gets_no_edge(tmp_path):
    source = "from gui import Widget\n\n\nclass Button(Widget):\n    pass\n"
    third_party = "scip-python python gui 1.0 gui/Widget#"
    unrelated = {"other.py": "class Widget:\n    pass\n"}
    edges, stats = _crafted(
        tmp_path,
        source,
        occurrence(0, 16, 22, third_party),
        occurrence(3, 6, 12, sym("m", "Button#"), DEF),
        occurrence(3, 13, 19, third_party),
        infos=symbol_information(sym("m", "Button#"), implementations=[third_party]),
        uncovered=unrelated,
    )
    assert not [e for e in edges if e["type"] == "INHERITS"]
    assert stats["unresolved_bases"] == 1 and stats["scip_bases_resolved"] == 1
    # By name alone, `gui.Widget` is the unrelated in-repo class, at 1.0.
    plain = build(tmp_path / "repo", jobs=1)
    e = one(plain.edges, type="INHERITS")
    assert e["dst"] == "sym:other.py::Widget" and e["confidence"] == 1.0


# ------------------------------------------------------- the config-file key --


def test_the_config_key_is_read_relative_to_the_repo(tmp_path):
    repo = copy_fixture(tmp_path)
    shutil.copy(FIXTURE_INDEX, repo / "index.scip")
    (repo / ".repo2graph.toml").write_text('scip = "index.scip"\n', encoding="utf8")
    assert load(repo).values == {"scip": "index.scip"}
    stats = stats_of(cli_build(repo, tmp_path / "out"))
    assert stats["scip_files_covered"] == 5 and stats["scip_calls_ambiguous"] == 0


@pytest.mark.parametrize(
    "value", ['"/etc/index.scip"', '"../index.scip"', '"a/../../x"', '""', "1"]
)
def test_the_config_key_must_name_a_file_inside_the_repo(tmp_path, value):
    (tmp_path / ".repo2graph.toml").write_text(f"scip = {value}\n", encoding="utf8")
    with pytest.raises(ConfigError, match="'scip'"):
        load(tmp_path)


def test_a_config_path_may_not_escape_through_a_symlink(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.scip"
    outside.write_bytes(_FIXTURE_BYTES)
    try:
        (repo / "index.scip").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(ScipError, match="outside the repository"):
        index_path("index.scip", repo, from_config=True)
    # The same path typed on the command line is the user's own choice.
    assert index_path(str(repo / "index.scip"), repo, from_config=False) == str(repo / "index.scip")


def test_the_flag_beats_the_config_key(tmp_path, capsys):
    repo = copy_fixture(tmp_path)
    (repo / ".repo2graph.toml").write_text('scip = "missing.scip"\n', encoding="utf8")
    stats = stats_of(cli_build(repo, tmp_path / "out", "--scip", str(FIXTURE_INDEX)))
    assert "scip_error" not in stats and stats["scip_files_covered"] == 5
