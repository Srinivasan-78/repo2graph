"""Opt-in exact call and inheritance resolution from a SCIP index (#380).

repo2graph resolves a call by its *name*, through the scoped tiers in
`graph.build`. A compiler-grade indexer knows the receiver's type, so it knows
exactly which `area` `shape.area()` means. SCIP (https://github.com/sourcegraph/scip)
is the format those indexers write -- `scip-python`, `scip-typescript`,
`scip-java`, `rust-analyzer scip`, ... -- and `build --scip <index.scip>` reads
one to replace the name-based guess wherever the index has an answer.

Three parts, top to bottom:

* **A protobuf wire-format reader** for the handful of `scip.proto` messages
  used here, by hand and with no protobuf dependency -- the same choice
  `embed.py` makes for `.npy`. The file is untrusted input: its size is capped
  before it is read, every varint is at most 10 bytes, every length is checked
  against the bytes actually left, group wire types are refused, and nesting is
  fixed by the schema (one decoder per message type, none of them recursive),
  so a crafted file can cost at most a linear pass over `MAX_INDEX_BYTES`.
  Anything malformed raises `ScipError`; nothing else escapes `decode_index`.

* **Symbol identity.** A SCIP symbol, e.g. ``scip-python python pkg 1.0 `pkg.mod`/A#run().``,
  maps to a repo2graph node through its *definition site*, not by parsing the
  moniker into a qualname: the definition occurrence in document `path` on line
  `L` whose last descriptor is named `n` is the repo2graph symbol defined in
  `path` that starts on line `L` and is named `n`. A SCIP symbol that lands on
  more than one node (a conditional redefinition) is left unmapped.

* **The merge.** For each call site in a file the index covers, the reference
  occurrence on the call's line whose last descriptor is the callee's name
  decides the target. In-repo -> `CALLS` at confidence 1.0 with
  `method`/`resolution_kind` `scip`; a symbol defined nowhere in the indexed
  files -> `CALLS_EXTERNAL`; anything else (no occurrence, a parameter, an
  attribute, a symbol that did not map) -> the heuristic edge, unchanged. Bases
  work the same way, through the class's SCIP relationships or the occurrence
  of the base's name in the class header. A file the index does not cover is
  never touched.

Everything runs after parsing, over `parsed` and the index alone, so a full and
an `--incremental` build resolve identically: the parse cache holds per-file
parse results only, and this pass is recomputed on every build like the rest of
resolution.
"""

from __future__ import annotations

import stat
from collections import Counter
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from .edgemeta import METHOD_SCIP
from .edgemeta import evidence as make_evidence
from .parse import symbol_key

if TYPE_CHECKING:
    from .parse import ParsedFile, Symbol

__all__ = [
    "MAX_INDEX_BYTES",
    "STAT_KEYS",
    "ScipDocument",
    "ScipError",
    "ScipIndex",
    "ScipResolver",
    "decode_index",
    "index_path",
    "parse_descriptors",
    "read_index_bytes",
    "symbol_name",
]

# --- bounds on untrusted input ----------------------------------------------
# A real index is a few bytes per source token: scip-python writes ~20 MB for a
# Django-sized tree. 256 MiB leaves an order of magnitude of headroom while
# keeping one decode pass bounded in memory and time.
MAX_INDEX_BYTES = 256 * 1024 * 1024
MAX_DOCUMENTS = 1_000_000
# Occurrences actually decoded, i.e. in documents for files this build parsed.
MAX_OCCURRENCES = 20_000_000
# A path or symbol longer than this is not something an indexer wrote.
MAX_STRING_BYTES = 64 * 1024
MAX_VARINT_BYTES = 10
_INT32_MIN, _INT32_MAX = -(1 << 31), (1 << 31) - 1
# How far past a class's first line its base list may run.
MAX_HEADER_LINES = 32

# SymbolRole.Definition: "is the symbol defined here?" -- scip.proto.
ROLE_DEFINITION = 0x1
# Relationship flags, packed into one int per relationship.
REL_REFERENCE, REL_IMPLEMENTATION, REL_TYPE_DEFINITION, REL_DEFINITION = 1, 2, 4, 8

# stats.json keys, written only when an index was asked for -- every one of
# them, zero or not, so a consumer sees the same shape on every --scip build.
STAT_KEYS = (
    "scip_documents",
    "scip_files_covered",
    "scip_files_uncovered",
    "scip_symbols_mapped",
    "scip_call_sites_resolved",
    "scip_call_sites_external",
    "scip_call_sites_heuristic",
    "scip_call_sites_uncovered",
    "scip_calls_ambiguous",
    "scip_bases_resolved",
    "scip_bases_heuristic",
)

RESOLUTION_KIND = "scip"


class ScipError(ValueError):
    """The SCIP index cannot be used; the message says why, without its contents."""


# --- protobuf wire format ----------------------------------------------------
_VARINT, _I64, _LEN, _SGROUP, _EGROUP, _I32 = 0, 1, 2, 3, 4, 5


def _varint(buf: bytes, pos: int, end: int) -> tuple[int, int]:
    """Decode one base-128 varint at `pos`; return `(value, next_pos)`."""
    if pos < end:
        b = buf[pos]
        if b < 0x80:  # the overwhelmingly common one-byte case
            return b, pos + 1
    result = shift = 0
    for i in range(MAX_VARINT_BYTES):
        if pos >= end:
            raise ScipError("truncated varint")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            if i == MAX_VARINT_BYTES - 1 and b > 1:
                raise ScipError("varint overflows 64 bits")
            return result, pos
        shift += 7
    raise ScipError(f"varint longer than {MAX_VARINT_BYTES} bytes")


def _int32(value: int) -> int:
    """A varint read as protobuf `int32`: negatives arrive sign-extended to 64 bits."""
    if value >= 1 << 63:
        value -= 1 << 64
    if not _INT32_MIN <= value <= _INT32_MAX:
        raise ScipError("int32 field out of range")
    return value


def _fields(buf: bytes, start: int, end: int) -> Iterator[tuple[int, int, int, int]]:
    """Walk one message's fields as `(number, wire_type, a, b)`.

    For a varint `a` is the value and `b` is 0; for a length-delimited field
    `buf[a:b]` is the payload; fixed-width fields are skipped over and yielded
    with their byte span. Groups are refused: proto3 has none and SCIP is proto3.
    """
    pos = start
    while pos < end:
        tag, pos = _varint(buf, pos, end)
        number, wire = tag >> 3, tag & 7
        if number == 0 or number > 0x1FFFFFFF:
            raise ScipError(f"invalid field number {number}")
        if wire == _VARINT:
            value, pos = _varint(buf, pos, end)
            yield number, wire, value, 0
        elif wire == _LEN:
            length, pos = _varint(buf, pos, end)
            if length > end - pos:
                raise ScipError("length-delimited field runs past the end of its message")
            yield number, wire, pos, pos + length
            pos += length
        elif wire in (_I64, _I32):
            width = 8 if wire == _I64 else 4
            if width > end - pos:
                raise ScipError("fixed-width field runs past the end of its message")
            yield number, wire, pos, pos + width
            pos += width
        elif wire in (_SGROUP, _EGROUP):
            raise ScipError("group wire type is not used by SCIP")
        else:
            raise ScipError(f"invalid wire type {wire}")


def _expect(wire: int, want: int, what: str) -> None:
    if wire != want:
        raise ScipError(f"{what} has wire type {wire}, expected {want}")


class _Strings:
    """UTF-8 decode with interning: an index repeats each symbol many times."""

    def __init__(self) -> None:
        self._seen: dict[bytes, str] = {}

    def get(self, buf: bytes, a: int, b: int, what: str) -> str:
        if b - a > MAX_STRING_BYTES:
            raise ScipError(f"{what} is longer than {MAX_STRING_BYTES} bytes")
        raw = buf[a:b]
        s = self._seen.get(raw)
        if s is None:
            try:
                s = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise ScipError(f"{what} is not valid UTF-8") from None
            self._seen[raw] = s
        return s


def _packed_int32(buf: bytes, a: int, b: int, out: list[int]) -> None:
    pos = a
    while pos < b:
        value, pos = _varint(buf, pos, b)
        out.append(_int32(value))


def _typed_range(buf: bytes, a: int, b: int) -> list[int]:
    """`SingleLineRange` / `MultiLineRange` -> `[line, start_character]`."""
    line = char = 0
    for number, wire, x, _y in _fields(buf, a, b):
        if number in (1, 2):
            _expect(wire, _VARINT, "range component")
            if number == 1:
                line = _int32(x)
            else:
                char = _int32(x)
    return [line, char]


# An occurrence reduced to what the merge reads: 1-based line, 0-based start
# column (in the document's own position encoding, used only for ordering on a
# line), the symbol, and its SymbolRole bitset.
Occurrence = tuple[int, int, str, int]


def _occurrence(buf: bytes, a: int, b: int, strings: _Strings) -> Occurrence | None:
    legacy: list[int] = []
    typed: list[int] | None = None
    symbol = ""
    roles = 0
    for number, wire, x, y in _fields(buf, a, b):
        if number == 1:  # deprecated `repeated int32 range`, packed or not
            if wire == _LEN:
                _packed_int32(buf, x, y, legacy)
            else:
                _expect(wire, _VARINT, "range")
                legacy.append(_int32(x))
            if len(legacy) > 4:
                raise ScipError("occurrence range has more than four elements")
        elif number == 2:
            _expect(wire, _LEN, "occurrence symbol")
            symbol = strings.get(buf, x, y, "occurrence symbol")
        elif number == 3:
            _expect(wire, _VARINT, "symbol_roles")
            roles = _int32(x)
        elif number in (8, 9):  # single_line_range / multi_line_range
            _expect(wire, _LEN, "typed range")
            typed = _typed_range(buf, x, y)
    # "When both typed_range and the deprecated range field are set,
    # typed_range takes precedence." -- scip.proto
    rng = typed if typed is not None else legacy
    if typed is None and len(legacy) not in (3, 4):
        return None  # no usable range: not wrong enough to refuse the index
    if not symbol or rng[0] < 0 or rng[1] < 0:
        return None
    return rng[0] + 1, rng[1], symbol, roles


def _relationship(buf: bytes, a: int, b: int, strings: _Strings) -> tuple[str, int]:
    symbol, flags = "", 0
    for number, wire, x, y in _fields(buf, a, b):
        if number == 1:
            _expect(wire, _LEN, "relationship symbol")
            symbol = strings.get(buf, x, y, "relationship symbol")
        elif number in (2, 3, 4, 5):
            _expect(wire, _VARINT, "relationship flag")
            if x:
                flags |= {
                    2: REL_REFERENCE,
                    3: REL_IMPLEMENTATION,
                    4: REL_TYPE_DEFINITION,
                    5: REL_DEFINITION,
                }[number]
    return symbol, flags


@dataclass
class ScipDocument:
    """What the merge needs from one `Document`."""

    path: str
    language: str = ""
    occurrences: list[Occurrence] = field(default_factory=list)
    # SymbolInformation.symbol -> [(related symbol, REL_* flags)]
    relationships: dict[str, list[tuple[str, int]]] = field(default_factory=dict)
    # SymbolInformation.symbol -> display_name, when the indexer wrote one
    display_names: dict[str, str] = field(default_factory=dict)


@dataclass
class ScipIndex:
    """A decoded index: every document's path, and the wanted documents in full."""

    paths: list[str] = field(default_factory=list)
    documents: dict[str, ScipDocument] = field(default_factory=dict)
    occurrence_count: int = 0


def _normalise_path(path: str) -> str:
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def _symbol_information(buf: bytes, a: int, b: int, strings: _Strings, doc: ScipDocument) -> None:
    symbol, display = "", ""
    rels: list[tuple[str, int]] = []
    for number, wire, x, y in _fields(buf, a, b):
        if number == 1:
            _expect(wire, _LEN, "symbol")
            symbol = strings.get(buf, x, y, "symbol")
        elif number == 4:
            _expect(wire, _LEN, "relationship")
            rel = _relationship(buf, x, y, strings)
            if rel[0]:
                rels.append(rel)
        elif number == 6:
            _expect(wire, _LEN, "display_name")
            display = strings.get(buf, x, y, "display_name")
    if not symbol:
        return
    if rels:
        doc.relationships.setdefault(symbol, []).extend(rels)
    if display:
        doc.display_names.setdefault(symbol, display)


def _document(
    buf: bytes,
    a: int,
    b: int,
    strings: _Strings,
    wanted: Collection[str] | None,
    index: ScipIndex,
) -> None:
    path = language = ""
    occ_spans: list[tuple[int, int]] = []
    info_spans: list[tuple[int, int]] = []
    # First pass: framing only. Field order is not guaranteed, and the path
    # decides whether the occurrences are worth decoding at all.
    for number, wire, x, y in _fields(buf, a, b):
        if number == 1:
            _expect(wire, _LEN, "relative_path")
            path = strings.get(buf, x, y, "relative_path")
        elif number == 2:
            _expect(wire, _LEN, "occurrence")
            occ_spans.append((x, y))
        elif number == 3:
            _expect(wire, _LEN, "symbol information")
            info_spans.append((x, y))
        elif number == 4:
            _expect(wire, _LEN, "language")
            language = strings.get(buf, x, y, "language")
    path = _normalise_path(path)
    index.paths.append(path)
    if wanted is not None and path not in wanted:
        return
    # A document split across several `Document` records is merged.
    doc = index.documents.get(path)
    if doc is None:
        doc = index.documents[path] = ScipDocument(path=path, language=language)
    index.occurrence_count += len(occ_spans)
    if index.occurrence_count > MAX_OCCURRENCES:
        raise ScipError(f"more than {MAX_OCCURRENCES} occurrences in the indexed files")
    for x, y in occ_spans:
        occ = _occurrence(buf, x, y, strings)
        if occ is not None:
            doc.occurrences.append(occ)
    for x, y in info_spans:
        _symbol_information(buf, x, y, strings, doc)


def decode_index(data: bytes, wanted: Collection[str] | None = None) -> ScipIndex:
    """Decode a serialized `scip.Index`.

    Args:
        data: The raw bytes of an `index.scip` file. Untrusted.
        wanted: Repository-relative paths whose documents should be decoded in
            full; every other document is framed and counted but its
            occurrences are skipped unread. None decodes every document.

    Returns:
        The decoded index.

    Raises:
        ScipError: The bytes are not a well-formed index within the bounds
            above. No other exception escapes for any input.
    """
    if len(data) > MAX_INDEX_BYTES:
        raise ScipError(f"index is {len(data)} bytes, over the {MAX_INDEX_BYTES}-byte limit")
    buf = bytes(data)
    strings = _Strings()
    index = ScipIndex()
    for number, wire, x, y in _fields(buf, 0, len(buf)):
        # 1 metadata and 3 external_symbols carry nothing the merge reads: a
        # symbol with no definition among the wanted documents is external
        # whether or not the indexer described it.
        if number == 2:
            _expect(wire, _LEN, "document")
            if len(index.paths) >= MAX_DOCUMENTS:
                raise ScipError(f"more than {MAX_DOCUMENTS} documents")
            _document(buf, x, y, strings, wanted, index)
        elif number in (1, 3):
            _expect(wire, _LEN, "index field")
    return index


def read_index_bytes(path: Path, max_bytes: int | None = None) -> bytes:
    """Read an index file, refusing anything over `max_bytes` before reading it.

    Args:
        path: The index file.
        max_bytes: Size ceiling; `MAX_INDEX_BYTES` when None.

    Raises:
        ScipError: Not a regular file, or too large.
        OSError: The file could not be read.
    """
    if max_bytes is None:
        max_bytes = MAX_INDEX_BYTES
    st = path.stat()
    if not stat.S_ISREG(st.st_mode):
        raise ScipError("not a regular file")
    if st.st_size > max_bytes:
        raise ScipError(f"index is {st.st_size} bytes, over the {max_bytes}-byte limit")
    with open(path, "rb") as fh:
        data = fh.read(max_bytes + 1)
    if len(data) > max_bytes:  # grew between stat and read
        raise ScipError(f"index is over the {max_bytes}-byte limit")
    return data


def index_path(value: str | None, repo_root: Path, from_config: bool) -> str | None:
    """Where to read the index from, for `build`.

    A `--scip` flag is a path as typed, relative to the working directory. The
    `scip` config key is the repository speaking for itself, so its path is
    relative to the repository root and has to stay inside it -- a checked-in
    config cannot point a build at a file elsewhere on the machine.

    Raises:
        ScipError: A config-file path that resolves outside the repository.
    """
    if not value:
        return None
    if not from_config:
        return value
    root = repo_root.resolve()
    target = (root / value).resolve()
    if target != root and root not in target.parents:
        raise ScipError(f"config key 'scip' points outside the repository: {value!r}")
    return str(target)


# --- symbol monikers -----------------------------------------------------------
_SIMPLE_ID = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_+-$")
_SUFFIXES = {"/": "namespace", "#": "type", ".": "term", ":": "meta", "!": "macro"}


def _read_name(s: str, i: int) -> tuple[str | None, int]:
    n = len(s)
    if i < n and s[i] == "`":
        out: list[str] = []
        i += 1
        while i < n:
            j = s.find("`", i)
            if j < 0:
                return None, n
            out.append(s[i:j])
            if j + 1 < n and s[j + 1] == "`":  # `` is an escaped backtick
                out.append("`")
                i = j + 2
                continue
            name = "".join(out)
            return (name or None), j + 1
        return None, n
    j = i
    while j < n and s[j] in _SIMPLE_ID:
        j += 1
    return (s[i:j] if j > i else None), j


def parse_descriptors(symbol: str) -> list[tuple[str, str]] | None:
    """`[(name, kind), ...]` for a global SCIP symbol, outermost first.

    Follows the grammar in scip.proto: `<scheme> ' ' <manager> ' ' <name> ' '
    <version> ' ' <descriptor>+`, with `  ` escaping a space inside the first
    four. `kind` is namespace/type/term/meta/macro/method/parameter/
    type-parameter. None for a local symbol or anything that does not parse;
    the parse is a single linear scan whatever the input.
    """
    if symbol.startswith("local "):
        return None
    n = len(symbol)
    i = 0
    for _ in range(4):  # scheme, manager, package name, version
        while True:
            if i >= n:
                return None
            if symbol[i] == " ":
                if i + 1 < n and symbol[i + 1] == " ":
                    i += 2
                    continue
                i += 1
                break
            i += 1
    out: list[tuple[str, str]] = []
    while i < n:
        c = symbol[i]
        if c in "[(":
            name, i = _read_name(symbol, i + 1)
            close = "]" if c == "[" else ")"
            if name is None or i >= n or symbol[i] != close:
                return None
            out.append((name, "type-parameter" if c == "[" else "parameter"))
            i += 1
            continue
        name, i = _read_name(symbol, i)
        if name is None or i >= n:
            return None
        suffix = symbol[i]
        if suffix in _SUFFIXES:
            out.append((name, _SUFFIXES[suffix]))
            i += 1
        elif suffix == "(":
            j = symbol.find(").", i + 1)
            if j < 0 or any(ch not in _SIMPLE_ID for ch in symbol[i + 1 : j]):
                return None
            out.append((name, "method"))
            i = j + 2
        else:
            return None
    return out or None


def symbol_name(symbol: str, display_names: Mapping[str, str] | None = None) -> tuple[str, str]:
    """`(name, kind)` of a symbol's last descriptor; `("", "")` when unknown.

    A local symbol (`local 3`) carries no name of its own, so it is only
    nameable through its `SymbolInformation.display_name`.
    """
    descs = parse_descriptors(symbol)
    if descs:
        return descs[-1]
    if display_names and symbol in display_names:
        return display_names[symbol], "local"
    return "", ""


def _last_segment(name: str) -> str:
    """`pkg.mod.Base` / `ns::Base` / `Base<T>` / `Base[T]` -> `Base`."""
    return name.rsplit(".", 1)[-1].rsplit("::", 1)[-1].split("<", 1)[0].split("[", 1)[0].strip()


# --- the merge -------------------------------------------------------------------
class GraphLike(Protocol):
    """The slice of `graph.Graph` this module writes to."""

    nodes: dict[str, dict[str, Any]]
    stats: Counter[str]

    def add_node(self, nid: str, **attrs: Any) -> Any: ...

    def add_edge(self, src: str, dst: str, etype: str, **attrs: Any) -> Any: ...


# Not a node id: the verdict for a symbol no indexed file defines.
_EXTERNAL = "\0external"
_NO_DEF_KINDS = frozenset({"parameter", "type-parameter"})


def _key(path: str, symbol: str) -> str:
    """Local symbols are only unique within their document."""
    return f"{path}\0{symbol}" if symbol.startswith("local ") else symbol


@dataclass
class _Covered:
    """One covered file, reduced to lookups."""

    # 1-based line -> [(column, symbol key, name)] for reference occurrences
    refs: dict[int, list[tuple[int, str, str]]]
    # symbol key -> [(related symbol key, name, REL_* flags)]
    relationships: dict[str, list[tuple[str, str, int]]]
    # lines holding a definition occurrence: where a class header ends
    def_lines: set[int]


class ScipResolver:
    """Exact edges for the files a SCIP index covers; see the module docstring."""

    def __init__(self, index: ScipIndex, g: GraphLike, parsed: Mapping[str, ParsedFile]) -> None:
        self.g = g
        self.stats = g.stats
        self.covered: dict[str, _Covered] = {}
        # symbol key -> the one node it is defined as
        self.mapping: dict[str, str] = {}
        # symbol keys with a definition in some covered file: "in the index",
        # so an unmapped one (a parameter, an attribute) is not external.
        self.defined: set[str] = set()
        owners: dict[str, set[str]] = {}
        self.stats["scip_documents"] += len(index.paths)
        for rel, pf in parsed.items():
            doc = index.documents.get(rel)
            if doc is None:
                self.stats["scip_files_uncovered"] += 1
                continue
            self.stats["scip_files_covered"] += 1
            self.covered[rel] = self._cover(rel, pf, doc, owners)
        for key, nodes in owners.items():
            if len(nodes) == 1:
                self.mapping[key] = next(iter(nodes))
        # node id -> its symbol key, for a class's own relationships
        self.key_of = {node: key for key, node in self.mapping.items()}
        self.stats["scip_symbols_mapped"] += len(self.mapping)

    def _cover(
        self, rel: str, pf: ParsedFile, doc: ScipDocument, owners: dict[str, set[str]]
    ) -> _Covered:
        names = doc.display_names
        defs: dict[int, list[tuple[str, str, str]]] = {}
        refs: dict[int, list[tuple[int, str, str]]] = {}
        for line, col, symbol, roles in sorted(doc.occurrences):
            key = _key(rel, symbol)
            name, kind = symbol_name(symbol, names)
            if roles & ROLE_DEFINITION:
                self.defined.add(key)
                if name and kind not in _NO_DEF_KINDS:
                    defs.setdefault(line, []).append((key, name, kind))
            elif name:
                refs.setdefault(line, []).append((col, key, name))
        for sym in pf.symbols:
            found = {k for k, name, _kind in defs.get(sym.start_line, ()) if name == sym.name}
            if len(found) == 1:
                owners.setdefault(found.pop(), set()).add(f"sym:{rel}::{symbol_key(sym)}")
        relationships = {
            _key(rel, s): [(_key(rel, r), symbol_name(r, names)[0], f) for r, f in rels]
            for s, rels in doc.relationships.items()
        }
        def_lines = {line for line, _c, _s, roles in doc.occurrences if roles & ROLE_DEFINITION}
        return _Covered(refs=refs, relationships=relationships, def_lines=def_lines)

    def _verdict(self, key: str) -> str | None:
        """A node id, `_EXTERNAL`, or None for "leave it to the heuristic"."""
        node = self.mapping.get(key)
        if node is not None:
            return node if node in self.g.nodes else None
        if key in self.defined or "\0" in key:
            return None
        return _EXTERNAL

    def calls(
        self,
        rel: str,
        sid: str,
        callee: str,
        count: int,
        call_kind: str,
        details: list[dict[str, Any]],
    ) -> tuple[int, int | None]:
        """Resolve the call sites of `callee` in `sid` that the index covers.

        Emits the exact edges itself and returns what is left for the
        heuristic tiers: `(remaining call sites, first line among them)`, the
        line None when unchanged or unknown. `(count, None)` for a file the
        index does not cover.
        """
        cov = self.covered.get(rel)
        if cov is None:
            self.stats["scip_call_sites_uncovered"] += count
            return count, None
        name = _last_segment(callee)
        per_line: Counter[int] = Counter()
        for cd in details:
            site = cd.get("line")
            if isinstance(site, int) and site > 0:
                per_line[site] += 1
        if sum(per_line.values()) > count:  # details and calls disagree: not ours to fix
            per_line.clear()
        resolved: list[tuple[int, str]] = []  # (line, node id or _EXTERNAL)
        leftover: list[int] = []
        for line in sorted(per_line):
            sites = per_line[line]
            occ = [key for _col, key, n in sorted(cov.refs.get(line, ())) if n == name]
            if len(occ) == sites:
                targets = occ
            elif occ and len(set(occ)) == 1:
                targets = occ[:1] * sites
            else:
                leftover += [line] * sites
                continue
            for key in targets:
                verdict = self._verdict(key)
                if verdict is None:
                    leftover.append(line)
                else:
                    resolved.append((line, verdict))
        if resolved:
            self._emit_calls(rel, sid, callee, call_kind, resolved)
        remaining = count - len(resolved)
        self.stats["scip_call_sites_resolved"] += len(resolved)
        self.stats["scip_call_sites_heuristic"] += remaining
        return remaining, (min(leftover) if leftover else None)

    def _emit_calls(
        self, rel: str, sid: str, callee: str, call_kind: str, resolved: list[tuple[int, str]]
    ) -> None:
        first: dict[str, int] = {}
        hits: Counter[str] = Counter()
        for line, target in resolved:
            first.setdefault(target, line)
            hits[target] += 1
        for target, line in first.items():
            common: dict[str, Any] = dict(
                count=hits[target],
                confidence=1.0,
                resolution_kind=RESOLUTION_KIND,
                call_kind=call_kind,
                method=METHOD_SCIP,
                evidence=make_evidence(rel, line),
            )
            if target == _EXTERNAL:
                eid = f"external:{callee}"
                self.g.add_node(eid, type="external", name=callee)
                self.g.add_edge(sid, eid, "CALLS_EXTERNAL", candidate_count=0, **common)
                self.stats["calls_external"] += 1
                self.stats["scip_call_sites_external"] += hits[target]
            else:
                self.g.add_edge(sid, target, "CALLS", candidate_count=1, **common)

    def note_ambiguous(self, rel: str) -> None:
        """The heuristic emitted an ambiguous fan-out from `rel`."""
        if rel in self.covered:
            self.stats["scip_calls_ambiguous"] += 1

    def base(self, rel: str, sid: str, sym: Symbol, base: str, subtype: str, raw_base: str) -> bool:
        """Resolve one base of class `sym`; True when the index decided it.

        An in-repo base gets an INHERITS edge at confidence 1.0, carrying the
        same `subtype`/`raw_base`/evidence the heuristic edge would have; a
        base defined in no indexed file gets none, exactly as an unresolved
        base does. False leaves the base to the heuristic.
        """
        cov = self.covered.get(rel)
        if cov is None:
            return False
        name = _last_segment(base)
        found: set[str] = set()
        cls = self.key_of.get(sid)
        if cls is not None:
            found = {
                r
                for r, n, flags in cov.relationships.get(cls, ())
                if flags & REL_IMPLEMENTATION and n == name
            }
        if not found:
            # The base as written in the class header: on the header line or
            # the lines after it, up to the first thing the class body defines.
            last = min(sym.end_line, sym.start_line + MAX_HEADER_LINES)
            for line in range(sym.start_line, last + 1):
                if line > sym.start_line and line in cov.def_lines:
                    break
                found = {k for _c, k, n in cov.refs.get(line, ()) if n == name}
                if found:
                    break
        verdict = self._verdict(found.pop()) if len(found) == 1 else None
        if verdict is None:
            self.stats["scip_bases_heuristic"] += 1
            return False
        self.stats["scip_bases_resolved"] += 1
        if verdict == _EXTERNAL:
            self.stats["unresolved_bases"] += 1
            return True
        # The heuristic's single-match INHERITS shape, with the method naming
        # where the answer came from.
        header = {bd.get("name"): bd for bd in sym.base_details}.get(base, {}).get("line")
        self.g.add_edge(
            sid,
            verdict,
            "INHERITS",
            subtype=subtype,
            raw_base=raw_base,
            method=METHOD_SCIP,
            candidate_count=1,
            confidence=1.0,
            ambiguous=False,
            evidence=make_evidence(rel, header if isinstance(header, int) else sym.start_line),
        )
        return True

    @classmethod
    def for_build(
        cls, source: str | None, g: GraphLike, parsed: Mapping[str, ParsedFile]
    ) -> ScipResolver | None:
        """Load `source` for `graph.build`, or None to resolve by name alone.

        None, and nothing recorded, when no index was asked for. An index that
        cannot be read or decoded is reported -- a warning line and a
        structured `scip_index_rejected` event on stderr, `scip_error` in
        stats.json -- and the build carries on with heuristic edges only.
        """
        if not source:
            return None
        for k in STAT_KEYS:
            g.stats[k] += 0
        try:
            index = decode_index(read_index_bytes(Path(source)), wanted=parsed.keys())
        except ScipError as exc:
            _reject(g, source, str(exc))
            return None
        except OSError as exc:
            _reject(g, source, exc.strerror or type(exc).__name__)
            return None
        resolver = cls(index, g, parsed)
        if parsed and not resolver.covered:
            from .events import diagnostic

            diagnostic(
                f"repo2graph: note: SCIP index {source} names {len(index.paths)} document(s) "
                "but none is a parsed file of this repository; it must be generated at the "
                "repository root. Calls stay name-resolved."
            )
        return resolver


def _reject(g: GraphLike, source: str, reason: str) -> None:
    from .events import diagnostic, emit

    # The stats line ships with the index, so it says why and never where:
    # `source` may be an absolute path on the build machine.
    g.stats["scip_error"] = reason  # type: ignore[assignment]
    diagnostic(
        f"repo2graph: warning: SCIP index {source} not used ({reason}); "
        "call and inheritance edges are name-resolved"
    )
    emit("scip_index_rejected", level="warning", reason=reason)
