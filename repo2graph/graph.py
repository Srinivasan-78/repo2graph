"""Build the repository graph: nodes + edges."""

import hashlib
import os
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Container, Iterable, Sequence
from pathlib import Path
from typing import IO, Any

# Split out of this module by responsibility (#315). Re-exported so every
# `from repo2graph.graph import X` keeps working.
from .cochange import (  # noqa: F401
    COCHANGE_TIMEOUT,
    MAX_COCHANGE_BYTES,
    MAX_COCHANGE_COMMITS,
    MAX_FILE_AUTHORS,
    add_cochange,
)
from .derived import (  # noqa: F401
    ENTRY_KINDS,
    REFERENCE_EDGES,
    TESTS_MAX_HOPS,
    add_framework_edges,
    add_reference_edges,
    add_tests_edges,
    mark_entrypoints,
)
from .discovery import discover
from .edgemeta import (
    METHOD_FILESYSTEM,
    METHOD_NAME_RESOLVER,
    SCOPED_CALL_KINDS,
    tree_sitter_method,
)
from .edgemeta import (
    evidence as make_evidence,
)
from .edgemeta import (
    normalize as normalize_edge,
)
from .events import diagnostic
from .imports import import_targets, path_index, repo_context, resolve_import
from .parse import (
    CONFIG_EXT,
    DOC_EXT,
    EMBEDDED_LANGS,
    EXT_LANG,
    SUPER_RECEIVERS,
    BuildConfig,
    ParsedFile,
    ParseError,
    Symbol,
    disambiguate_key,
    parse_source,
    refine_lang,
    symbol_key,
    symbol_parent_key,
)
from .parsecache import PARSE_CACHE_FORMAT, ChunkedParsedFile, cache_entry, entry_read  # noqa: F401
from .parsepool import (
    INPROCESS_MAX_BYTES,
    PARSE_TIMEOUT_S,
    TIMEOUT,
    claim_slot,
    supervised_map,
)
from .scip import ScipResolver

# Under this many files a process pool costs more to start than it saves.
PARALLEL_MIN_FILES = 64
#: One node or edge as this module builds it, before `export.py` serialises it.
#: Spelled the same way as `store.Record`, which is what reads these back off
#: disk -- the two are the same shape at opposite ends of the pipeline. `Any`
#: rather than a union because the attribute set is open by design: `add_node`
#: and `add_edge` take `**attrs` and every stage adds its own keys.
Record = dict[str, Any]

#: The measurements `_read_and_parse` takes while it holds a file's bytes:
#: `(size, line count, the parse result, sha256 of exactly those bytes)`.
#: `build()` never sees the bytes, so anything derived from them is computed
#: here or not at all. `ParsedFile` covers the chunked reader too, since
#: `ChunkedParsedFile` subclasses it.
ParseMeasurement = tuple[int, int, ParsedFile | None, str]

#: One file's outcome from either reader: `(relative path, language, …)`, with
#: a `None` payload for a file that could not be read, and a `None` language
#: for one with no grammar. Both readers run in a process pool, so this is
#: also the shape that has to pickle.
ReadResult = tuple[str, str | None, ParseMeasurement | None]

# Soft warning threshold -- past this many nodes or edges a build alerts on stderr.
LARGE_GRAPH_WARN_THRESHOLD = 50_000
# How many CALLS edges an ambiguous name is allowed to fan out to, each at 1/n
# confidence. Overridable per build (`build(max_call_candidates=)`, the
# `--max-call-candidates` flag), so it is a property of a *particular* index,
# not of repo2graph -- which is why the Graph carries the value it was built
# with and manifest.json reports that value rather than this default (#245).
DEFAULT_MAX_CALL_CANDIDATES = 5

# What happens when `--max-edges` or `--max-bytes` binds. Both policies cut at
# the limit -- a bound that does not bound is not one -- and both record the cut
# in `stats.json`. They differ only in whether the cut is announced on stderr.
# `warn` is the default because a partial index that looks complete is the
# expensive failure here: the next reader cannot tell a repository with no
# callers of `f` from one whose caller was dropped at the ceiling. `truncate` is
# for callers that have already decided to bound the build and do not want the
# noise on every run.
LIMIT_POLICIES = ("warn", "truncate", "fail")
DEFAULT_LIMIT_POLICY = "warn"
# Method names of the built-in collection, string and promise types across the
# indexed languages. `x.get()` on a receiver whose type the parser cannot see is
# far more often `dict.get` / `Map.get` than the repository's own `get`, so an
# in-repo match for one of these names on an untyped receiver is recorded at
# UNTYPED_RECEIVER_CONFIDENCE (split 1/n across candidates) instead of 1.0.
# `self.get()`, `this.get()` and a bare `get()` are unaffected.
UNTYPED_RECEIVER_BUILTIN_METHODS = frozenset(
    {
        # mappings / sets
        "get", "set", "setdefault", "pop", "popitem", "update", "keys", "values",
        "items", "clear", "copy", "add", "discard", "has", "delete", "put",
        "containsKey", "getOrDefault", "putIfAbsent", "entries",
        # sequences
        "append", "extend", "insert", "remove", "index", "count", "sort",
        "reverse", "push", "shift", "unshift", "slice", "splice", "concat",
        "map", "filter", "reduce", "forEach", "find", "findIndex", "some",
        "every", "includes", "indexOf", "contains", "size", "isEmpty",
        # strings
        "join", "split", "strip", "lstrip", "rstrip", "replace", "format",
        "startswith", "endswith", "startsWith", "endsWith", "lower", "upper",
        "trim", "encode", "decode", "toString", "toLowerCase", "toUpperCase",
        # I/O, streams, regex, concurrency
        "read", "write", "close", "flush", "start", "stop", "end",
        "group", "groups", "seek", "tell", "poll", "terminate", "kill",
        # promises / objects
        "then", "catch", "finally", "equals", "hashCode",
        # JVM / Kotlin / Swift collections and strings
        "addAll", "removeAll", "containsAll", "removeAt", "removeLast",
        "removeFirst", "lastIndexOf", "substring", "charAt", "flatMap",
        "sorted", "toList",
        # .NET: collections, strings, LINQ and Task are PascalCase
        "Add", "AddRange", "Remove", "RemoveAt", "RemoveAll", "Insert", "Clear",
        "Contains", "ContainsKey", "ContainsValue", "TryGetValue", "TryAdd",
        "TryRemove", "GetValueOrDefault", "IndexOf", "Count", "Any", "All",
        "Where", "Select", "First", "FirstOrDefault", "Last", "LastOrDefault",
        "Single", "SingleOrDefault", "ToList", "ToArray", "ToDictionary",
        "OrderBy", "Sort", "Reverse", "Find", "Exists", "Push", "Pop", "Peek",
        "Enqueue", "Dequeue", "CopyTo", "ToString", "Equals", "GetHashCode",
        "Split", "Join", "Trim", "Replace", "StartsWith", "EndsWith", "ToLower",
        "ToUpper", "Substring", "Format", "Wait", "ContinueWith",
        "ConfigureAwait", "GetAwaiter", "Read", "Write", "Close", "Flush",
        "Start", "Stop",
    }
)  # fmt: skip
UNTYPED_RECEIVER_CONFIDENCE = 0.2

# The same problem for *free* functions. A bare `super()`, `len()` or `sorted()`
# names the language's builtin, not a method the repository happens to define
# under that name -- but the set above only fires on a receiver
# (`receiver == "other"`), and a bare call has none, so these stayed at
# confidence 1.0. On Django that gave `template/loader_tags.py::BlockNode.super`
# -- the helper behind `{{ block.super }}` -- 1,805 incoming CALLS edges sourced
# from `db/models/fields/`, `forms/fields.py` and `db/models/expressions.py`,
# none of which touch block inheritance, and second place in the repo map's
# most-called list.
#
# Applied only when the name resolved *globally*. A definition in the calling
# file, in its class, in a base, or one the file imports by name is a real
# target that happens to shadow a builtin, and `edgemeta.SCOPED_CALL_KINDS` is
# exactly that set -- so a project with its own `filter()` helper, called bare
# where it is defined or imported where it is used, is untouched. What is left
# is a cross-file global name match, which for one of these names is a
# coincidence rather than a call.
BUILTIN_FREE_FUNCTIONS = frozenset(
    {
        # Python builtins
        "abs", "aiter", "all", "anext", "any", "ascii", "bin", "bool", "breakpoint",
        "bytearray", "bytes", "callable", "chr", "classmethod", "compile", "complex",
        "delattr", "dict", "dir", "divmod", "enumerate", "eval", "exec", "filter",
        "float", "format", "frozenset", "getattr", "globals", "hasattr", "hash",
        "help", "hex", "id", "input", "int", "isinstance", "issubclass", "iter",
        "len", "list", "locals", "map", "max", "memoryview", "min", "next",
        "object", "oct", "open", "ord", "pow", "print", "property", "range",
        "repr", "reversed", "round", "set", "setattr", "slice", "sorted",
        "staticmethod", "str", "sum", "super", "tuple", "type", "vars", "zip",
        # Go builtins (`len`, `max`, `min`, `print` overlap with the above)
        "append", "cap", "clear", "close", "copy", "delete", "imag", "make",
        "new", "panic", "println", "real", "recover",
        # JS/TS globals reached as bare calls
        "decodeURI", "decodeURIComponent", "encodeURI", "encodeURIComponent",
        "isFinite", "isNaN", "parseFloat", "parseInt", "queueMicrotask",
        "require", "setInterval", "setTimeout", "structuredClone",
    }
)  # fmt: skip

# Tiers that reached the candidate through the calling file's own imports.
IMPORT_RESOLUTION_KINDS = ("import_alias", "imported_symbol")
# File stems that stand for their directory (`pkg/__init__.py` is `pkg`).
PACKAGE_STEMS = frozenset({"__init__", "index", "mod", "lib"})


class _AllExcept:
    """A container holding every symbol id but one (or, given None, every one)."""

    __slots__ = ("excluded",)

    def __init__(self, excluded: str | None) -> None:
        self.excluded = excluded

    def __contains__(self, item: object) -> bool:
        return item != self.excluded


def _module_leaves(target: str) -> set[str]:
    """Last-segment names an import target can be referred to by.

    `pkg.store` -> {"store"}; `./store.js` -> {"store"}; a Go path
    `example.com/m/cache` -> {"cache"}.
    """
    seg = target.strip().rstrip("/").rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lstrip(".")
    return {x for x in (seg.split(".")[0], seg.rsplit(".", 1)[-1], seg.rsplit("::", 1)[-1]) if x}


def _home_names(node: Record) -> set[str]:
    """Names a receiver could use for the module/type a candidate lives in."""
    path = node.get("path", "")
    stem = Path(path).stem
    names = {stem}
    if stem in PACKAGE_STEMS or node.get("lang") == "go":
        names.add(path.rpartition("/")[0].rsplit("/", 1)[-1])
    owner = node.get("qualname", "").rpartition(".")[0]
    if owner:
        names.add(owner.rsplit(".", 1)[-1])
    return names


# Base names common enough -- language roots (object/Exception/Error/...) and
# the framework bases that a third of a codebase can inherit from (Django's
# Model, React's Component, SQLAlchemy's Base) -- that a same-named class
# anywhere in the repo would false-link unrelated hierarchies together. A
# repository's own `Model` is still linked from its own file and from every
# file that imports it: this only blocks the repo-wide fallback tier, the one
# that would otherwise tie every `models.Model` subclass to it.
COMMON_STDLIB_BASES = frozenset(
    {
        "object",
        "Object",
        "Exception",
        "BaseException",
        "Error",
        "StandardError",
        "Throwable",
        "Record",
        "Any",
        "Interface",
        "Model",
        "Component",
        "Base",
    }
)


class GraphLimitExceeded(RuntimeError):
    """Raised when the graph exceeds a configured resource limit."""

    pass


class Graph:
    def __init__(
        self,
        root: Path,
        name: str,
        max_files: int = 0,
        max_call_candidates: int = DEFAULT_MAX_CALL_CANDIDATES,
        max_edges: int = 0,
        limit_policy: str | None = None,
        max_memory_mb: float = 0.0,
        max_build_seconds: float = 0.0,
    ):
        self.root, self.name = root, name
        self.max_files = max_files
        self.max_edges = max_edges
        self.limit_policy = limit_policy
        self.max_memory_mb = max_memory_mb
        self.max_build_seconds = max_build_seconds
        self.start_time = time.monotonic()
        self.bytes_indexed: int = 0
        # Which resource limits actually bound this build, and by how much.
        # Recorded whatever the policy is: `truncate` chooses to stay quiet on
        # stderr, never to produce an index that cannot say it is partial. An
        # artifact that was cut and does not admit it is the failure every other
        # bound in this file is written to avoid.
        self.limits_hit: dict[str, Any] = {}
        # The ambiguous-call fan-out limit this graph was resolved under.
        # Carried on the Graph purely so the writers can report it: export's
        # manifest.json describes the artifacts it ships beside, and a manifest
        # claiming the default 5 for an index built with 2 is a wrong answer to
        # the one question the manifest exists to answer (#245). A Graph built
        # by hand keeps the default, which is what build() would have used.
        self.max_call_candidates = max_call_candidates
        self.config: BuildConfig | None = None
        # The discovery filters this build actually used, recorded so
        # index.state.json can persist them. Without them, anything that
        # re-runs discovery to compare against the index -- `index-status`,
        # `doctor`'s freshness check -- re-discovers with *default* filters
        # and reports every deliberately excluded file as newly added, i.e. a
        # build with any --exclude reads as permanently stale.
        self.include_globs: list[str] | None = None
        self.exclude_globs: list[str] | None = None
        # Where the build came from when there is no local tree to point at:
        # `repo2graph github` clones, builds and deletes the clone, so
        # `index-status` and `doctor` have nothing to compare against and the
        # remote is recorded instead. `manifest.json` carries it as
        # `source_remote` (docs/architecture.md section 3), so it is part of the
        # artifact contract rather than an incidental attribute -- it was being
        # set on the instance by `fetch.py` under the comment "not a Graph
        # field", and read back through `getattr(g, "source_remote", None)`,
        # which is how a documented field stayed invisible to the type checker.
        self.source_remote: str | None = None
        self.nodes: dict[str, Record] = {}
        self.edges: list[Record] = []
        self._edge_seen: set[tuple[Any, ...]] = set()
        self.stats: Counter[str] = Counter()
        # {relative path: sha256 of the bytes that were indexed}, written to
        # index.state.json so a later build can tell what actually changed.
        self.file_hashes: dict[str, str] = {}
        # {relative path: cache entry}, written to parse.cache.json so a later
        # `--incremental` build can skip re-parsing files that did not change.
        # Populated by every build, full or incremental, so the first full build
        # is what makes the next incremental one possible.
        self.parse_cache: dict[str, Record] = {}
        # Filled in by an incremental build only: {"cached": n, "reparsed": m}.
        # Deliberately *not* in `stats`, which is written to stats.json -- an
        # incremental build must produce byte-identical artifacts to a full one,
        # and a hit/miss count differs by construction between the two.
        self.incremental: dict[str, int] | None = None
        self._warned_large = False
        self._limits_announced: set[str] = set()
        self.build_id: str | None = None

    def _check_memory_and_duration(self) -> None:
        if (
            self.max_build_seconds > 0
            and (time.monotonic() - self.start_time) > self.max_build_seconds
        ):
            if self.limit_policy == "fail":
                raise GraphLimitExceeded(
                    f"Build duration limit exceeded: reached {time.monotonic() - self.start_time:.1f}s "
                    f"(max_build_seconds={self.max_build_seconds}). Use --max-build-seconds to increase."
                )
            self.limits_hit["build_duration_exceeded"] = round(
                time.monotonic() - self.start_time, 2
            )
            self._note_limit(
                "build_duration",
                f"repo2graph: warning: build duration ceiling reached at {self.max_build_seconds}s; "
                f"further elements are dropped and the graph is partial",
            )
        if self.max_memory_mb > 0:
            est_mb = (
                len(self.nodes) * 500 + len(self.edges) * 200 + getattr(self, "bytes_indexed", 0)
            ) / 1_000_000
            if est_mb >= self.max_memory_mb:
                if self.limit_policy == "fail":
                    raise GraphLimitExceeded(
                        f"Graph memory limit exceeded: estimated {est_mb:.1f} MB "
                        f"(max_memory_mb={self.max_memory_mb}). Use --max-memory-mb to increase."
                    )
                self.limits_hit["memory_dropped"] = self.limits_hit.get("memory_dropped", 0) + 1
                self._note_limit(
                    "memory",
                    f"repo2graph: warning: memory ceiling reached at {self.max_memory_mb} MB; "
                    f"further elements are dropped and the graph is partial",
                )

    def add_node(self, nid: str, **attrs: Any) -> str:
        self._check_memory_and_duration()
        if "build_duration_exceeded" in self.limits_hit or "memory_dropped" in self.limits_hit:
            return nid
        if nid in self.nodes:
            # Preserve legitimate 0 and False values on re-add:
            self.nodes[nid].update(
                {
                    k: v
                    for k, v in attrs.items()
                    if v is not None
                    and v != ""
                    and (not isinstance(v, (list, tuple)) or len(v) > 0)
                }
            )
        else:
            max_nodes = getattr(self.config, "max_nodes", 0) if self.config else 0
            if max_nodes > 0 and len(self.nodes) >= max_nodes:
                if self.limit_policy == "fail" or self.limit_policy is None:
                    raise GraphLimitExceeded(
                        f"Graph node limit exceeded: graph reached {len(self.nodes)} nodes (max_nodes={max_nodes}). "
                        "Use --max-nodes to increase the limit or filter with --include/--exclude."
                    )
                self.limits_hit["nodes_dropped"] = self.limits_hit.get("nodes_dropped", 0) + 1
                self._note_limit(
                    "nodes",
                    f"repo2graph: warning: node ceiling reached at {max_nodes} nodes; "
                    f"further nodes are dropped and the graph is partial",
                )
                return nid
            self.nodes[nid] = dict(id=nid, **attrs)
        self._warn_if_large()
        return nid

    def add_edge(self, src: str, dst: str, etype: str, **attrs: Any) -> None:
        key = (src, dst, etype)
        if key in self._edge_seen:
            return
        # Recorded before the ceiling test, so a *dropped* edge proposed a second
        # time is not counted twice. `edges_dropped` is published in stats.json
        # and read as the number of distinct edges the graph is missing; leaving
        # the key unrecorded here counted attempts instead and overstated the
        # loss for every edge discovered more than once. `_edge_seen` means
        # "already decided", which is what both callers of it want.
        #
        # The cost is that past the ceiling this set keeps growing, one tuple per
        # distinct *rejected* edge, where before it only ever held accepted ones.
        # That is inherent -- counting distinct losses means remembering them --
        # and it is the cheaper half of the pair: a retained key is three pointers
        # against the full attribute dict `self.edges` would have held, so
        # `--max-edges` still bounds the thing it was added to bound.
        self._edge_seen.add(key)
        self._check_memory_and_duration()
        if "build_duration_exceeded" in self.limits_hit or "memory_dropped" in self.limits_hit:
            return
        # Checked after the duplicate test, so a repeated edge is not counted as
        # one the ceiling dropped. Discovery order is deterministic (see
        # CONTRIBUTING's "Deterministic Discovery Order"), so the same build
        # with the same limit keeps the same edges -- which is what lets
        # `test_max_edges_truncation_is_deterministic` compare two runs.
        if self.max_edges > 0 and len(self.edges) >= self.max_edges:
            if self.limit_policy == "fail":
                raise GraphLimitExceeded(
                    f"Graph edge limit exceeded: graph reached {len(self.edges)} edges (max_edges={self.max_edges}). "
                    "Use --max-edges to increase the limit or filter with --include/--exclude."
                )
            self.limits_hit["edges_dropped"] = self.limits_hit.get("edges_dropped", 0) + 1
            self._note_limit(
                "edges",
                f"repo2graph: warning: edge ceiling reached at {self.max_edges} edges; "
                f"further edges are dropped and the graph is partial",
            )
            return
        # Every edge leaves here carrying the standard trust metadata, whatever
        # the caller remembered to pass. Normalising at the one chokepoint is
        # what stops a newly added edge type shipping as a bare triple, which
        # is how four of the six ended up that way.
        self.edges.append(normalize_edge(dict(src=src, dst=dst, type=etype, **attrs)))
        self.stats[f"edge:{etype}"] += 1
        self._warn_if_large()

    def _note_limit(self, which: str, message: str) -> None:
        """Announce a limit the first time it binds, if the policy says to.

        Once per limit per build, not once per dropped item: a ceiling reached
        early would otherwise print a line for every remaining edge and bury
        the build's real output.
        """
        # No policy given means the documented default, `warn`. Checking for
        # "warn" alone made the default silently cut files, bytes and edges.
        if (self.limit_policy or DEFAULT_LIMIT_POLICY) != "warn":
            return
        if which in self._limits_announced:
            return
        self._limits_announced.add(which)
        diagnostic(message)
        try:
            from .events import emit

            emit("resource_limit_reached", level="warning", limit=which)
        except Exception:  # noqa: BLE001 - resilience boundary: event emission failure
            pass

    def _warn_if_large(self) -> None:
        if self._warned_large:
            return
        if (
            len(self.nodes) > LARGE_GRAPH_WARN_THRESHOLD
            or len(self.edges) > LARGE_GRAPH_WARN_THRESHOLD
        ):
            self._warned_large = True
            msg = (
                f"repo2graph: warning: graph has grown past {LARGE_GRAPH_WARN_THRESHOLD} "
                f"nodes/edges ({len(self.nodes)} nodes, {len(self.edges)} edges)"
            )
            if self.max_files > 0:
                msg += f" (max_files={self.max_files})."
            else:
                msg += " with no size limit set; pass max_files= to build() to bound memory use."
            diagnostic(msg)


# ---------- parsing ----------
# The longest UTF-8 sequence is four bytes, so at most three bytes of one can be
# left dangling at the end of a slice cut at an arbitrary byte offset.
_UTF8_MAX_SEQ = 4


def _incomplete_utf8_tail(buf: bytes) -> int:
    """Length of the *truncated* UTF-8 sequence at the end of `buf`, else 0.

    Slices are taken at raw byte offsets, so a multi-byte character can
    straddle a boundary -- its lead byte ends slice N and its continuation bytes
    begin slice N+1. Both then fail to decode and *both* were dropped, losing up
    to 2 x max_file_bytes of source with nothing recording it. Reporting the
    length of the dangling prefix lets the caller carry those bytes into the next
    slice so the boundary lands on a character boundary instead.

    Only a genuinely truncated sequence counts. A complete character, a stray
    continuation byte with no lead, and a byte that can never start a sequence
    (0xF8..0xFF) all return 0, so invalid bytes are still reported as invalid
    rather than carried forward forever.
    """
    for back in range(1, _UTF8_MAX_SEQ):
        if back > len(buf):
            return 0
        b = buf[-back]
        if b < 0x80:
            return 0  # ASCII: nothing is dangling
        if b < 0xC0:
            continue  # continuation byte: its lead byte is further back
        need = 2 if b < 0xE0 else 3 if b < 0xF0 else 4 if b < 0xF8 else 0
        return back if need > back else 0
    return 0


def _chunk_and_parse(
    rel: str, abspath: Path, lang: str | None, config: BuildConfig, size: int
) -> ReadResult:
    chunk_size = config.max_file_bytes
    # One list per parsed slice: keys are only unique within a slice.
    slice_symbols: list[list[Symbol]] = []
    all_imports = []
    total_parse_errors = 0
    used_cpp = False
    undecodable_slices = 0
    grammar_unavailable = False
    # The #377 `.h` sniff, on the one path that never holds the whole file:
    # this reader streams slices, so the sniff runs against the first decodable
    # one instead of the full bytes `_read_and_parse` has. A header's C++
    # signals (`#include <string>`, `namespace`, `class`, `::`) are in its
    # opening lines, and a slice is `max_file_bytes` -- 1.5 MB by default --
    # so the first slice is the whole preamble of any real header. Without
    # this, a `.h` over the chunking threshold (an amalgamated single-header
    # C++ library is the ordinary case) took the C grammar regardless of
    # content, unlike every smaller `.h` in the same build.
    # `.m` (Objective-C or MATLAB, #398) is settled the same way.
    sniff_header = (lang, abspath.suffix.lower()) in (("c", ".h"), ("objc", ".m"))

    line_offset = 0
    # Streamed, not accumulated: `raw_content = bytearray()` held the entire
    # file for the digest and the line count, so the one path max_file_bytes
    # exists to bound had no memory bound at all. sha256 over the raw
    # bytes in file order and a running newline count are exactly the values the
    # buffered version produced, byte for byte.
    hasher = hashlib.sha256()
    total_bytes = 0
    newlines = 0
    # Bytes of a character whose sequence ran off the end of the previous slice.
    # Never more than three, and never a newline (a newline is ASCII and cannot
    # be part of a multi-byte sequence), so `line_offset` accounting is unaffected.
    carry = b""
    eof = False

    with _safe_open(abspath) as f:
        while not eof:
            # Read a slice short by whatever is carried, so each parsed slice is
            # still exactly chunk_size bytes. max(1, ...) only matters for a
            # chunk_size of three or less: it keeps the loop making progress
            # instead of reading zero bytes forever.
            chunk = f.read(max(1, chunk_size - len(carry)))
            if chunk:
                hasher.update(chunk)
                total_bytes += len(chunk)
                newlines += chunk.count(b"\n")
            else:
                eof = True
            buf, carry = carry + chunk, b""
            if not eof:
                tail = _incomplete_utf8_tail(buf)
                if tail:
                    buf, carry = buf[:-tail], buf[-tail:]
            # At EOF a dangling sequence is genuinely truncated rather than
            # straddling, so it stays in `buf` and is reported below, once.
            if not buf:
                continue

            try:
                buf.decode("utf-8")
            except UnicodeDecodeError:
                # Not a boundary artefact -- these bytes are not UTF-8 at all.
                # Count them so a file that quietly loses most of itself is
                # visible in stats.json instead of looking healthy.
                undecodable_slices += 1
                line_offset += buf.count(b"\n")
                continue

            if sniff_header:
                lang = refine_lang(lang, abspath.suffix.lower(), buf)[0]
                sniff_header = False

            pf = (
                parse_source(
                    buf,
                    lang,
                    filepath=None,
                    refs=config.reference_edges,
                    frameworks=config.framework_edges,
                )
                if lang
                else None
            )
            if pf is None:
                line_offset += buf.count(b"\n")
                continue

            for sym in pf.symbols:
                sym.start_line += line_offset
                sym.end_line += line_offset
            slice_symbols.append(pf.symbols)

            all_imports.extend(pf.imports)
            total_parse_errors += pf.parse_errors
            if pf.used_cpp:
                used_cpp = True
            # Carried out of the slice loop, like `used_cpp`. Without it the
            # `ChunkedParsedFile` below inherited the field's False default, so
            # a file too large to parse whole counted as `parsed` with zero
            # symbols and the total-failure guard in `build` -- which fires only
            # on `_unavailable and not parsed` -- could never see it. A repo
            # whose only supported sources exceed `max_file_bytes` (an
            # amalgamated single-header library, a big generated file) then
            # exited 0 with an empty graph and `--incremental` cached it.
            if pf.grammar_unavailable:
                grammar_unavailable = True

            line_offset += buf.count(b"\n")

    # `(name, start_line)` -> the global key the winning definition was given.
    # A value, not just membership, because a symbol skipped as a duplicate still
    # has to be rekeyable: recording nothing for it left a later child whose
    # `symbol_parent_key` named it resolving through
    # `rekeyed.get(old_parent, old_parent)` to a slice-local key that no longer
    # exists file-wide, and `build` then fell back to `owner = fid` -- the
    # child's DEFINES edge came from the file instead of its parent, silently.
    seen: dict[tuple[str, int], str] = {}
    deduped_symbols = []
    # Each slice was keyed on its own; re-key across the whole file with the
    # same `@L<line>` scheme `parse_source` uses, so a chunked file and a
    # normally parsed one name a duplicate definition the same way (this used
    # to rewrite the *qualname* to `<qualname>_<n>` instead).
    used_keys: set[str] = set()

    for symbols in slice_symbols:
        # slice-local key -> global key. Per slice, because a slice's second
        # `class A` is keyed plain `A` inside that slice, and a child whose
        # empty `parent_key` means "same as parent" must follow *its* `A`, not
        # the first file-wide one.
        rekeyed: dict[str, str] = {}
        for sym in symbols:
            old_key = symbol_key(sym)
            old_parent = symbol_parent_key(sym)
            seen_key = (sym.name, sym.start_line)
            if seen_key in seen:
                rekeyed[old_key] = seen[seen_key]
                continue

            sym.key = disambiguate_key(sym.qualname, sym.start_line, used_keys)
            new_key = symbol_key(sym)
            seen[seen_key] = new_key
            rekeyed[old_key] = new_key
            if old_parent is not None:
                new_parent = rekeyed.get(old_parent, old_parent)
                sym.parent_key = "" if new_parent == sym.parent else new_parent

            deduped_symbols.append(sym)

    # `lang or ""`, matching `_abandoned` below: `ParsedFile.lang` is `str`,
    # and `None` is reachable here. A file over `max_file_bytes` whose
    # extension no grammar claims is routed to this reader by
    # `_read_and_parse` -- its guard tests `lang not in EMBEDDED_LANGS`, which
    # `None` satisfies -- every slice then fails the `if lang` test around
    # `parse_source`, and this runs with no symbols and no language. `""` is
    # already the value `_abandoned` stores for the same condition, so this is
    # the existing convention rather than a new one, and every reader of
    # `pf.lang` (`== "go"`, `in IMPLICIT_THIS_LANGS`, the cache writer) treats
    # it exactly as it treated `None`.
    #
    # What is *not* fixed here: the same file under the threshold returns
    # `pf=None`, so the file node's `chunked` attribute is `True` on this path
    # and `False` on the other for one non-code file. That is a behaviour
    # difference in byte-identical output rather than a typing question, so it
    # is recorded in .github/CONTRIBUTING.md's backlog instead.
    pf = ChunkedParsedFile(
        lang=lang or "",
        symbols=deduped_symbols,
        imports=sorted(set(all_imports)),
        parse_errors=total_parse_errors,
        used_cpp=used_cpp,
        is_chunked=True,
        undecodable_slices=undecodable_slices,
        grammar_unavailable=grammar_unavailable,
    )

    return rel, lang, (total_bytes, newlines + 1, pf, hasher.hexdigest())


def _safe_read_bytes(path: Path) -> bytes:
    """Read file bytes using O_NOFOLLOW where supported to avoid symlink TOCTOU races.

    On POSIX systems, O_NOFOLLOW causes open() to fail if the trailing component is a
    symlink (protecting against an attacker replacing a discovered regular file with a
    symlink to a sensitive file before open). On Windows, O_NOFOLLOW is not supported
    by the OS open(), so standard read flags are used.
    """
    o_nofollow = getattr(os, "O_NOFOLLOW", None)
    if o_nofollow is not None:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | o_nofollow
        fd = os.open(path, flags)
        try:
            with os.fdopen(fd, "rb") as fh:
                return fh.read()
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
    return path.read_bytes()


def _safe_open(path: Path, mode: str = "rb") -> IO[bytes]:
    """Open a file with O_NOFOLLOW where supported.

    Like _safe_read_bytes, but returns a file object for chunked reading
    (used by _chunk_and_parse for files larger than config.max_file_bytes).
    """
    o_nofollow = getattr(os, "O_NOFOLLOW", None)
    if o_nofollow is not None:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | o_nofollow
        fd = os.open(path, flags)
        try:
            return os.fdopen(fd, mode)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
    return open(path, mode)


def _redact_metadata(text: str, g: Graph) -> str:
    """Redact secrets in a signature or docstring before it enters the graph.

    These two fields are copied into chunk headers, `nodes.jsonl`, `graph.html`,
    GraphML and Cypher without passing through the chunk-body scan, so this is
    the one place that covers every consumer. `warn-only` and `off` deliberately
    leave the text alone, matching what those policies mean for chunk bodies --
    `Index._served` is the serve-time net for them.
    """
    if not text:
        return text
    policy = getattr(getattr(g, "config", None), "secret_policy", "redact-match")
    if policy not in ("redact-match", "exclude-file"):
        return text
    from .security import redact_content

    redacted, n = redact_content(text, policy="redact-match")
    if n and hasattr(g, "stats"):
        g.stats["redacted_secret_metadata"] += n
    return redacted


def _read_and_parse(item: tuple[str, Path, str | None, BuildConfig | None]) -> ReadResult:
    """Read one file and parse it if it is code.

    Top level, and returns only counts plus the ParsedFile, so a process pool
    can pickle both the call and its result. The fourth element of the read
    tuple is the sha256 of the bytes just parsed: it is computed here because
    this is the only place that holds them, and build() never keeps them.
    """
    rel, abspath, lang, config = item
    if config is None:
        from .parse import BuildConfig

        config = BuildConfig()

    try:
        st = abspath.lstat()
        size = st.st_size
        if size > config.max_file_bytes and config.chunk_large_files and lang not in EMBEDDED_LANGS:
            return _chunk_and_parse(rel, abspath, lang, config, size)
    except OSError:
        pass

    try:
        raw = _safe_read_bytes(abspath)
    except OSError:
        return rel, lang, None
    lang, code = refine_lang(lang, abspath.suffix.lower(), raw)
    policy = getattr(config, "parse_policy", "best-effort")
    pf = None
    try:
        pf = (
            parse_source(
                code,
                lang,
                filepath=abspath,
                refs=config.reference_edges,
                frameworks=config.framework_edges,
            )
            if lang
            else None
        )
    except Exception as exc:
        if policy == "strict":
            raise ParseError(
                f"Strict parse policy: failed to parse '{rel}' as {lang}: {exc}"
            ) from exc
        pf = None

    if pf is not None and getattr(pf, "parse_errors", 0) > 0:
        if policy == "strict":
            raise ParseError(
                f"Strict parse policy: '{rel}' produced {pf.parse_errors} syntax error(s) under language '{lang}'"
            )
        if policy == "warn":
            diagnostic(
                f"repo2graph: warning: '{rel}' produced {pf.parse_errors} syntax error(s) ({lang})"
            )

    return rel, lang, (len(raw), raw.count(b"\n") + 1, pf, hashlib.sha256(raw).hexdigest())


# Languages where a bare call inside a method is a call on the implicit
# `this` (`g()` inside `A.g` is `this.g()`). Elsewhere (Python, JS, Go, Rust,
# PHP) a bare call inside a method names a free function, never the method.
IMPLICIT_THIS_LANGS = frozenset({"java", "csharp", "kotlin", "swift", "scala", "cpp", "ruby"})


def _last_segment(name: str) -> str:
    """`pkg.mod.Base` / `ns::Base` / `Base<T>` -> `Base`."""
    return name.rsplit(".", 1)[-1].rsplit("::", 1)[-1].split("<", 1)[0].split("[", 1)[0].strip()


def parse_incremental(
    files: Iterable[tuple[str, Path]],
    jobs: int,
    cache: dict[str, Record],
    counts: dict[str, int],
    config: BuildConfig | None = None,
) -> list[ReadResult]:
    """Read every file, but re-parse only the ones whose bytes changed.

    A file's `ParsedFile` is a pure function of its bytes and its language and
    nothing else, so reusing one for a file whose sha256 still matches is exact
    -- not an approximation. Everything downstream of parsing (the global name
    index, CALLS confidences, INHERITS, entrypoints and reach) is then recomputed
    from scratch over the full symbol set by `build()`, which is what makes an
    incremental build byte-identical to a full one instead of merely close.

    Reading is still done for every file: the hash *is* the bytes, so there is
    no cheaper way to know a file is unchanged, and reading is the small half of
    the cost. Parsing is what this skips, and parsing is what dominates a build.

    Args:
        files: The `(relpath, abspath)` pairs discovery produced, in order.
        jobs: Parser process count, passed through to `parse_all`.
        cache: `{relpath: entry}` loaded from a previous build's parse cache.
        counts: Mutated in place with "cached" and "reparsed" tallies.
        config: BuildConfig

    Returns:
        The same list of `(rel, lang, read)` tuples `parse_all` returns, in
        discovery order, so the build loop cannot tell the two apart.
    """
    results: dict[str, ReadResult] = {}
    order: list[str] = []
    stale: list[tuple[str, Path]] = []
    for rel, abspath in files:
        order.append(rel)
        lang = EXT_LANG.get(abspath.suffix.lower())
        try:
            raw = _safe_read_bytes(abspath)
        except OSError:
            results[rel] = (rel, lang, None)
            continue
        lang = refine_lang(lang, abspath.suffix.lower(), raw)[0]
        digest = hashlib.sha256(raw).hexdigest()
        entry = cache.get(rel)
        read = None
        # The language must match too: the same bytes parsed as a different
        # language yield different symbols, and a renamed extension changes the
        # language without changing the content hash.
        if (
            isinstance(entry, dict)
            and entry.get("sha256") == digest
            and entry.get("lang") == (lang or "")
        ):
            read = entry_read(entry)
        if read is None:
            stale.append((rel, abspath))
        else:
            results[rel] = (rel, lang, read)
            counts["cached"] = counts.get("cached", 0) + 1
    counts["reparsed"] = len(stale)
    for rel, lang, read in parse_all(stale, jobs, config=config):
        results[rel] = (rel, lang, read)
    return [results[rel] for rel in order]


def resolve_jobs(jobs: int) -> int:
    """0 means one worker per core, capped so the parent keeps up with results."""
    if jobs > 0:
        return jobs
    return max(1, min(os.cpu_count() or 1, 8))


# Win32 GetStdHandle/SetStdHandle slot numbers. os.dup2 rewrites the CRT's fd
# table, which is what `print`, `sys.stdout` and any C extension writing to the
# CRT `stdout` go through -- but it does *not* touch the process-wide Win32
# standard handles, so a library that calls `WriteFile(GetStdHandle(...))`
# directly would still reach the handle the worker inherited. These are the two
# slots `silence_worker_io` repoints for that last route.
_STD_INPUT_HANDLE = 0xFFFFFFF6  # (DWORD)-10
_STD_OUTPUT_HANDLE = 0xFFFFFFF5  # (DWORD)-11


def silence_worker_io(watch: Any = None) -> None:
    """Detach a parse worker from the stdin/stdout it inherited (#90).

    Runs as the `ProcessPoolExecutor` initializer, i.e. *inside* the worker and
    once per worker. It exists because of who the parent may be: when the pool
    is started from `repo2graph-mcp`, fd 0 and fd 1 are the client's JSON-RPC
    pipes. Measured on Windows (spawn), a worker inherits both for real --
    `os.write(1, ...)` lands in the parent's stdout pipe, and `os.read(0, 1)`
    *consumes a byte of the client's request stream*, which is a request the
    server then waits for forever. Parsing writes to neither today; this makes
    that a property of the pool rather than of the current worker body.

    Redirection is done with `os.dup2` onto `os.devnull`, not by rebinding
    `sys.stdout`, because a Python-only swap leaves fd 1 -- and therefore every
    C-level write and anything that cached `fileno()` -- pointing at the pipe.
    `sys.stdin`/`sys.stdout` are rebound afterwards so their buffers are not
    holding anything from before the swap; on Windows the Win32 std handles are
    repointed too (see above).

    Every step is best effort: a worker that cannot open devnull must still
    parse files. Nothing here raises, because an initializer that raises breaks
    the pool for every task.

    `watch` is the supervisor's shared array (parsepool.py, #81): the worker
    claims a slot in it, so the parent can see which file it is on and kill it
    when that file runs over the parse budget.
    """
    if watch is not None:
        claim_slot(watch)
    try:
        devnull = os.open(os.devnull, os.O_RDWR)
    except OSError:
        return
    try:
        for fd in (0, 1):
            try:
                os.dup2(devnull, fd)
            except OSError:
                pass
    finally:
        if devnull > 2:
            try:
                os.close(devnull)
            except OSError:
                pass
    for name, fd, mode in (("stdin", 0, "r"), ("stdout", 1, "w")):
        try:
            setattr(sys, name, open(fd, mode, closefd=False))
        except OSError:
            pass
    if sys.platform == "win32":
        try:
            import ctypes
            import msvcrt

            kernel32 = ctypes.windll.kernel32
            kernel32.SetStdHandle.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
            kernel32.SetStdHandle.restype = ctypes.c_int
            kernel32.SetStdHandle(_STD_INPUT_HANDLE, msvcrt.get_osfhandle(0))
            kernel32.SetStdHandle(_STD_OUTPUT_HANDLE, msvcrt.get_osfhandle(1))
        except (AttributeError, OSError, ValueError):
            pass


def parse_all(
    files: Iterable[tuple[str, Path]], jobs: int, config: BuildConfig | None = None
) -> list[ReadResult]:
    """Read and parse every file, in discovery order, across `jobs` processes.

    tree-sitter parsing is CPU bound and dominates a large build, so this is
    the difference between one core and all of them. Order is preserved, which
    keeps node ids and edge order identical to a serial run.

    Every worker runs `silence_worker_io` first, so the pool is safe to start
    from a process whose stdin/stdout are a protocol stream rather than a
    terminal -- which is what `repo2graph-mcp`'s auto-build is (#90).

    Workers are supervised (parsepool.py, #81): a file whose parse runs over
    `config.parse_timeout`, or takes its worker down, is skipped and counted
    rather than allowed to stall or exhaust the build. A serial build parses
    small files in-process and still sends anything over INPROCESS_MAX_BYTES to
    a worker, because that is where a parse can no longer be cheap -- unless
    the budget is off (`parse_timeout=0`), when there is nothing to enforce.
    """
    jobs = resolve_jobs(jobs)
    timeout = PARSE_TIMEOUT_S if config is None else config.parse_timeout
    items = [(rel, abspath, EXT_LANG.get(abspath.suffix.lower()), config) for rel, abspath in files]
    if jobs > 1 and len(items) >= PARALLEL_MIN_FILES:
        pooled = list(range(len(items)))
    elif timeout > 0:
        pooled = [
            i for i, (_, abspath, _, _) in enumerate(items) if _size(abspath) > INPROCESS_MAX_BYTES
        ]
    else:
        pooled = []  # no budget to enforce: a serial build stays in-process
    results: list[Any] = [None] * len(items)
    if pooled:
        try:
            outcomes = supervised_map(
                _read_and_parse,
                [items[i] for i in pooled],
                min(jobs, len(pooled)),
                timeout,
                silence_worker_io,
            )
            # strict: `supervised_map` returns one outcome per input item in
            # input order or raises `PoolUnavailable`, so an unequal length is
            # a broken pool contract rather than an input condition. Silently
            # truncating here would drop files from the index without a count.
            for i, outcome in zip(pooled, outcomes, strict=True):
                results[i] = _settle(items[i], outcome, timeout)
        except ParseError:
            # A strict-policy failure is the worker doing its job, not a broken
            # pool -- falling through to the serial fallback below would just
            # re-parse every file a second time and raise this same error again.
            raise
        except Exception as exc:  # noqa: BLE001 - resilience boundary: process pool execution failure falls back to serial
            from .events import reraise_if_debug

            reraise_if_debug(exc)
            # No fork / no POSIX semaphores to build on, a pool that broke before
            # parsing anything, a worker ImportError, or a pickling failure on
            # the call or its result: parse in-process rather than abort the
            # build -- but say so, because in-process the budget cannot stop a
            # parse, only discard it afterwards.
            diagnostic(
                f"repo2graph: warning: parse workers unavailable ({type(exc).__name__}); "
                "parsing in-process, where the per-file parse budget cannot interrupt a parse"
            )
            results = [None] * len(items)
    for i, item in enumerate(results):
        if item is None:
            started = time.monotonic()
            res = _read_and_parse(items[i])
            results[i] = _settle(items[i], (res, time.monotonic() - started), timeout)
    if getattr(config, "parse_policy", "best-effort") == "strict":
        for rel, _lang, read in results:
            if read is not None and read[2] is not None and read[2].parse_skipped:
                raise ParseError(
                    f"Strict parse policy: {_skip_reason(read[2], timeout)} on '{rel}'"
                )
    return results


def _size(path: Path) -> int:
    try:
        return path.lstat().st_size
    except OSError:
        return 0


def _settle(
    item: tuple[str, Path, str | None, BuildConfig | None],
    outcome: tuple[ReadResult, float] | str,
    timeout: float,
) -> ReadResult:
    """`parse_all`'s result for one file, from what its parse came back with.

    `outcome` is `(result, seconds)` or parsepool's TIMEOUT / CRASH marker. A
    parse that finished but over budget is discarded the same as one that had
    to be killed, so where the deadline is noticed never changes the output.
    """
    if isinstance(outcome, str):
        # The only strings parsepool returns are its TIMEOUT / CRASH markers.
        return _abandoned(item, outcome)
    res, elapsed = outcome
    rel, lang, read = res
    if timeout > 0 and elapsed > timeout and read is not None and read[2] is not None:
        skipped = ParsedFile(lang=read[2].lang, symbols=[], imports=[], parse_skipped=TIMEOUT)
        return rel, lang, (read[0], read[1], skipped, read[3])
    return res


def _abandoned(item: tuple[str, Path, str | None, BuildConfig | None], reason: str) -> ReadResult:
    """The result for a file whose worker was killed or died under it (#81).

    Read again here because the bytes died with the worker. Size, line count
    and digest come out exactly as a finished parse reports them, so the file
    node is the one a parse would have made; the language is settled from the
    first slice, as `_chunk_and_parse` settles it for a file too big to hold.
    """
    rel, abspath, lang, config = item
    block = getattr(config, "max_file_bytes", 0) or 1_500_000
    hasher = hashlib.sha256()
    size = newlines = 0
    head = b""
    try:
        with _safe_open(abspath) as fh:
            while chunk := fh.read(block):
                head = head or chunk
                hasher.update(chunk)
                size += len(chunk)
                newlines += chunk.count(b"\n")
    except OSError:
        return rel, lang, None
    lang = refine_lang(lang, abspath.suffix.lower(), head)[0]
    pf = ParsedFile(lang=lang or "", symbols=[], imports=[], parse_skipped=reason)
    return rel, lang, (size, newlines + 1, pf, hasher.hexdigest())


def _skip_reason(pf: ParsedFile, timeout: float) -> str:
    if pf.parse_skipped == TIMEOUT:
        return f"parse exceeded the {timeout:g}s budget (--parse-timeout)"
    return "parse crashed or exhausted its worker's memory"


def _within_byte_budget(
    files: Sequence[tuple[str, Path]], budget: int, g: "Graph"
) -> list[tuple[str, Path]]:
    """The leading files whose cumulative size fits in `budget` bytes.

    Stops at the first file that would exceed the budget rather than skipping
    it and continuing: skipping would make the selection depend on the sizes of
    files *after* the one that did not fit, so inserting a large file in the
    middle of a repository could silently change which small files at the end
    were indexed. Stopping keeps the selection a prefix of the discovery order,
    which is the property that makes it reproducible.

    A single file larger than the whole budget therefore yields nothing, which
    is correct and is why the limit is announced: a budget too small to admit
    the first file is a misconfiguration, not an empty repository.
    """
    kept = []
    used = 0
    for rel, absolute in files:
        try:
            size = absolute.stat().st_size
        except OSError:
            # Unreadable here means unparseable later; let the normal path
            # record it rather than charging it against the budget.
            kept.append((rel, absolute))
            continue
        if used + size > budget:
            if g.limit_policy == "fail":
                raise GraphLimitExceeded(
                    f"Graph byte budget exceeded: {used + size} > {budget} bytes (max_bytes={budget}). "
                    "Use --max-bytes to increase the limit or filter with --include/--exclude."
                )
            dropped = len(files) - len(kept)
            g.limits_hit["files_dropped_over_max_bytes"] = dropped
            g._note_limit(
                "bytes",
                f"repo2graph: warning: byte budget reached at {used} of {budget} bytes; "
                f"{dropped} discovered file(s) were not indexed and the graph is partial",
            )
            break
        used += size
        kept.append((rel, absolute))
    g.stats["bytes_indexed"] = used
    return kept


# ---------- build ----------
def build(
    root: Path,
    include: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
    git_history: int = 0,
    max_files: int = 0,
    jobs: int = 0,
    cache: dict[str, Record] | None = None,
    max_call_candidates: int = DEFAULT_MAX_CALL_CANDIDATES,
    config: BuildConfig | None = None,
    cochange_min: int = 3,
    max_bytes: int = 0,
    max_edges: int = 0,
    limit_policy: str | None = None,
    max_memory_mb: float = 0.0,
    max_build_seconds: float = 0.0,
    git_authors: bool = False,
) -> Graph:
    """Parse `root` into a Graph.

    Args:
        root: Repository directory to index.
        include: Optional glob(s) restricting discovery.
        exclude: Optional glob(s) removing paths from discovery.
        git_history: When non-zero, add CO_CHANGE edges from the last N commits.
        git_authors: With git_history, also record on each file node who
            changed it in those commits (display names, never emails).
        max_files: When positive, index only the first N discovered files.
        jobs: Parser processes; 0 means one per core, 1 means serial.
        cache: A previous build's `{relpath: entry}` parse cache. When given,
            files whose sha256 and language both still match are not re-parsed.
            Resolution is recomputed in full either way, so the resulting Graph
            is identical to one built with `cache=None`.
        max_call_candidates: An ambiguous call name fans out to at most this
            many CALLS edges, each at 1/n confidence. Clamped to >= 1, and the
            clamped value is recorded on the returned Graph so the writers can
            state the limit this build actually used.
        config: BuildConfig
        max_bytes: When positive, stop adding files once their cumulative
            on-disk size would exceed this many bytes. Applied over the
            deterministic discovery order, so the same repository and the same
            limit select the same files.
        max_edges: When positive, the graph holds at most this many edges;
            later ones are dropped.
        limit_policy: `warn` (default) announces a bound on stderr the first
            time it binds; `truncate` cuts silently; `fail` aborts immediately
            with GraphLimitExceeded. All record the cut in `stats.json`.
        max_memory_mb: Maximum estimated RAM usage in MB before triggering limit_policy.
        max_build_seconds: Maximum wall-clock build time in seconds before triggering limit_policy.

    Returns:
        The populated Graph. `parse_cache` holds the cache for the *next*
        build; `incremental` holds hit/miss counts when `cache` was supplied.
    """
    if config:
        if max_edges == 0 and getattr(config, "max_edges", 0) > 0:
            max_edges = config.max_edges
        if max_bytes == 0 and getattr(config, "max_bytes", 0) > 0:
            max_bytes = config.max_bytes
        if max_memory_mb == 0.0 and getattr(config, "max_memory_mb", 0.0) > 0:
            max_memory_mb = config.max_memory_mb
        if max_build_seconds == 0.0 and getattr(config, "max_build_seconds", 0.0) > 0:
            max_build_seconds = config.max_build_seconds
        if limit_policy is None and getattr(config, "limit_policy", None) in LIMIT_POLICIES:
            limit_policy = config.limit_policy

    max_call_candidates = max(1, max_call_candidates)
    root = Path(root).resolve()
    if limit_policy is not None and limit_policy not in LIMIT_POLICIES:
        raise ValueError(f"limit_policy must be one of {LIMIT_POLICIES}, not {limit_policy!r}")
    g = Graph(
        root,
        root.name,
        max_files=max_files,
        max_call_candidates=max_call_candidates,
        max_edges=max_edges,
        limit_policy=limit_policy,
        max_memory_mb=max_memory_mb,
        max_build_seconds=max_build_seconds,
    )
    g.config = config
    g.include_globs = list(include) if include else None
    g.exclude_globs = list(exclude) if exclude else None
    repo_id = f"repo:{root.name}"
    g.add_node(repo_id, type="repo", name=root.name, path=".")

    files = list(discover(root, include, exclude, stats=g.stats, config=config))
    if max_files > 0 and len(files) > max_files:
        if limit_policy == "fail":
            raise GraphLimitExceeded(
                f"Graph file limit exceeded: found {len(files)} files (max_files={max_files}). "
                "Use --max-files to increase the limit or filter with --include/--exclude."
            )
        dropped = len(files) - max_files
        g.limits_hit["files_dropped_over_max_files"] = dropped
        g._note_limit(
            "files",
            f"repo2graph: warning: file ceiling reached at {max_files} files; "
            f"further files are dropped and the graph is partial",
        )
        files = files[:max_files]
    if max_bytes > 0:
        files = _within_byte_budget(files, max_bytes, g)
    file_index = {rel for rel, _ in files}
    ctx = repo_context(root)
    ctx.update(path_index(file_index))
    # Only ParsedFile, never the raw bytes -- keeping those too would hold the
    # whole repo in memory.
    parsed: dict[str, ParsedFile] = {}

    if cache is None:
        results = parse_all(files, resolve_jobs(jobs), config=config)
    else:
        counts: dict[str, int] = {"cached": 0, "reparsed": 0}
        results = parse_incremental(files, resolve_jobs(jobs), cache, counts, config=config)
        g.incremental = counts

    for rel, lang, read in results:
        if "build_duration_exceeded" in g.limits_hit:
            break
        if read is None:  # unreadable file
            continue
        size, lines, pf, digest = read
        g.file_hashes[rel] = digest
        skipped = pf is not None and pf.parse_skipped
        if not skipped:
            # Never cached: a later build, maybe with a bigger budget, retries it.
            g.parse_cache[rel] = cache_entry(lang, size, lines, pf, digest)
        ext = Path(rel).suffix.lower()
        if ext == ".h":
            # Auditable record of the #377 content sniff: how many `.h` files
            # were kept on the C grammar vs. promoted to cpp.
            g.stats["header_files_as_cpp" if lang == "cpp" else "header_files_as_c"] += 1
        ftype = (
            "code"
            if lang
            else ("doc" if ext in DOC_EXT else "config" if ext in CONFIG_EXT else "other")
        )
        fid = f"file:{rel}"
        is_chunked = getattr(pf, "is_chunked", False) if pf else False
        g.add_node(
            fid,
            type="file",
            name=Path(rel).name,
            path=rel,
            lang=lang or ext.lstrip("."),
            file_type=ftype,
            size=size,
            lines=lines,
            parse_errors=pf.parse_errors if pf else 0,
            chunked=is_chunked,
        )
        g.stats["files"] += 1

        # directory chain
        parent = repo_id
        parts = Path(rel).parts[:-1]
        for i in range(len(parts)):
            dpath = "/".join(parts[: i + 1])
            did = f"dir:{dpath}"
            g.add_node(did, type="dir", name=parts[i], path=dpath)
            g.add_edge(parent, did, "CONTAINS", method=METHOD_FILESYSTEM)
            parent = did
        g.add_edge(parent, fid, "CONTAINS", method=METHOD_FILESYSTEM)

        if pf is None:
            continue
        if skipped:
            # Like an oversized file: a file node, no symbols, and a count (#81).
            g.stats[f"skipped_parse_{pf.parse_skipped}"] += 1
            diagnostic(
                f"repo2graph: warning: '{rel}' indexed without symbols: "
                + _skip_reason(pf, getattr(config, "parse_timeout", PARSE_TIMEOUT_S))
            )
            continue
        if getattr(pf, "grammar_unavailable", False):
            # Counted, not parsed: a supported language whose grammar would not
            # load contributed no symbols and no edges. Tallied separately so the
            # total-failure check below can tell an empty graph caused by a broken
            # install from one caused by an empty repository.
            g.stats["grammar_unavailable"] += 1
            continue
        parsed[rel] = pf
        g.stats["parsed"] += 1
        g.stats["parse_errors"] += pf.parse_errors
        if pf.parse_errors > 0:
            g.stats["files_with_parse_errors"] += 1
        if getattr(pf, "used_cpp", False):
            g.stats["cpp_fallback_files"] += 1
        # A chunked file whose slices do not decode still gets a node,
        # a `chunked: true` flag and a correct line count, so the index looks
        # healthy while the file is simply unqueryable. This is the only signal
        # that any of it was lost.
        undecodable = getattr(pf, "undecodable_slices", 0)
        if undecodable:
            g.stats["chunk_slices_undecodable"] += undecodable
            g.stats["files_with_undecodable_chunks"] += 1

        file_keys = {symbol_key(s_) for s_ in pf.symbols}
        for sym in pf.symbols:
            sid = f"sym:{rel}::{symbol_key(sym)}"
            # Signatures and docstrings are secret-bearing text like any other,
            # and only the chunk *body* was ever scanned. A credential in a
            # docstring or a default argument therefore shipped verbatim in five
            # places at once: the `# doc:` line of every chunk header
            # (chunks.py), `nodes.jsonl`, the JSON payload embedded in
            # `graph.html` (viz.py), GraphML, and Cypher -- none of which redact.
            # Redacting here, at the one point the fields enter the graph, fixes
            # all five rather than four of them; `redact_content` is
            # line-preserving, so citation line numbers are unaffected.
            g.add_node(
                sid,
                type="symbol",
                name=sym.name,
                qualname=sym.qualname,
                kind=sym.kind,
                path=rel,
                lang=lang,
                start_line=sym.start_line,
                end_line=sym.end_line,
                signature=_redact_metadata(sym.signature, g),
                docstring=_redact_metadata(sym.docstring, g),
            )
            g.stats[f"symbol:{sym.kind}"] += 1
            # A Go method / Kotlin extension names a receiver type that may be
            # declared in another file: DEFINES then comes from the file.
            pkey = symbol_parent_key(sym)
            owner = f"sym:{rel}::{pkey}" if pkey and pkey in file_keys else fid
            g.add_edge(
                owner,
                sid,
                "DEFINES",
                method=tree_sitter_method(lang),
                evidence=make_evidence(rel, sym.start_line),
            )

        # `pf` being non-None does not imply a language: the chunked reader
        # returns a `ChunkedParsedFile` for a file over `max_file_bytes` whose
        # extension no grammar claims, and such a file parsed no slice, so
        # `pf.imports` is empty and everything below was already a no-op.
        # Skipping explicitly makes that a fact the type checker can see rather
        # than one that merely happens to hold -- `import_targets` and
        # `resolve_import` both dispatch on a real language.
        if not lang:
            continue
        import_lines = getattr(pf, "import_lines", None) or []
        for i, raw_imp in enumerate(pf.imports):
            # Index-aligned with `imports` by construction; a cache written by
            # an older format has no lines at all, so fall back to None rather
            # than guessing a line the reader would find nothing at.
            imp_line = import_lines[i] if i < len(import_lines) else None
            imp_evidence = make_evidence(rel, imp_line)
            for target in import_targets(raw_imp, lang):
                resolved = resolve_import(target, rel, lang, file_index, ctx)
                if resolved:
                    g.add_edge(
                        fid,
                        f"file:{resolved}",
                        "IMPORTS",
                        target=target,
                        internal=True,
                        # The statement is a parse fact; mapping the module
                        # name onto a file in this repo is the resolver's
                        # judgement, so that is the method that gets named.
                        method=METHOD_NAME_RESOLVER,
                        evidence=imp_evidence,
                    )
                    g.stats["imports_resolved"] += 1
                else:
                    mid = f"module:{target}"
                    g.add_node(mid, type="module", name=target, external=True)
                    g.add_edge(
                        fid,
                        mid,
                        "IMPORTS",
                        target=target,
                        internal=False,
                        method=tree_sitter_method(lang),
                        evidence=imp_evidence,
                    )
                    g.stats["imports_unresolved"] += 1

    # ----- grammars did not load at all: fail, do not publish an empty graph -----
    # A build that discovered supported source files but could obtain a grammar for
    # none of them is a broken environment, not a repository with nothing in it.
    # Left alone it exited 0 with a graph of files and directories and zero code
    # edges, `doctor` reported ok, `impact` rated every PR LOW because nothing was
    # reachable, and `--incremental` cached the empty result so the next build
    # reproduced it without even retrying. This is the one parse failure that is
    # never best-effort: the tool did not do its job.
    _unavailable = g.stats.get("grammar_unavailable", 0)
    if _unavailable and not parsed:
        raise ParseError(
            f"no tree-sitter grammar could be loaded for any of the {_unavailable} "
            "supported source file(s) found, so the graph would contain no symbols, "
            "calls or imports. This is an installation or network problem, not a "
            "property of the repository.\n"
            "       Check it with `repo2graph doctor`, and reinstall grammars with "
            "`pip install -U 'tree-sitter-language-pack>=0.7,<1.0'` -- the 1.x line "
            "ships a loader that downloads grammars on first use, which cannot work "
            "offline or behind a restricted proxy."
        )

    # ----- name index for call/inheritance resolution -----
    imported_files: dict[str, set[str]] = defaultdict(set)
    for e in g.edges:
        if e["type"] == "IMPORTS" and e["src"].startswith("file:") and e["dst"].startswith("file:"):
            caller_rel = e["src"].split(":", 1)[1]
            callee_rel = e["dst"].split(":", 1)[1]
            imported_files[caller_rel].add(callee_rel)

    # Import aliases per file: alias -> (module, original name).
    import_aliases: dict[str, dict[str, tuple[str, str | None]]] = defaultdict(dict)
    for rel, pf in parsed.items():
        for imp in getattr(pf, "import_details", []):
            if imp.alias:
                import_aliases[rel][imp.alias] = (imp.module, imp.name)

    # Per file: name bound by an import -> the module leaf names it can mean
    # (`import store`, `from pkg import store`, `import pkg.store as s`, Go's
    # package name). `store.get()` is then a module-qualified call when the
    # candidate lives in `store.py`; `_cv_request.get()` (a from-imported
    # value) is not, because no candidate lives in a module of that name.
    import_bound: dict[str, dict[str, set[str]]] = defaultdict(dict)
    for rel, pf in parsed.items():
        for imp in getattr(pf, "import_details", []):
            target = imp.name if imp.name and imp.name not in ("*", "default") else imp.module
            if not target:
                continue
            leaves = _module_leaves(target)
            bound = import_bound[rel]
            for b in {imp.alias} if imp.alias else {target, *leaves}:
                bound.setdefault(b, set()).update(leaves)

    by_name: dict[str, list[str]] = defaultdict(list)
    # class id -> (file, raw base names), for `super().m()` resolution.
    class_bases: dict[str, tuple[str, list[str]]] = {}
    for rel, pf in parsed.items():
        for s_ in pf.symbols:
            if s_.bases:
                class_bases[f"sym:{rel}::{symbol_key(s_)}"] = (rel, list(s_.bases))
    # member scope -> member name -> every member id with that name. The scope
    # is the enclosing *qualname* in the file (not its `@L` key), so a Rust
    # type's methods spread over several `impl A` blocks -- or a class defined
    # twice under an `if` -- share one scope, exactly as they did when those
    # blocks collapsed into one node. For Go it is the receiver type within
    # the package directory, because Go methods of one type may live in any
    # file of the package. Holds *every* same-named member, so an overload set
    # is found whole (fan-out at 1/n) rather than just its first definition.
    members: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))

    def scope_of(rel: str, qualname: str | None) -> str | None:
        if not qualname:
            return None
        if parsed[rel].lang == "go":
            return f"go:{rel.rpartition('/')[0]}::{qualname}"
        return f"sym:{rel}::{qualname}"

    for rel, pf in parsed.items():
        for s_ in pf.symbols:
            if (scope_id := scope_of(rel, s_.parent)) is not None:
                members[scope_id][s_.name].append(f"sym:{rel}::{symbol_key(s_)}")
    # Same-named symbols per file and per directory, and each symbol's place in
    # `by_name` order. The same-file, imported and same-module tiers below used
    # to filter every same-named symbol in the repository on each call, which
    # made a name defined in every file quadratic in the file count (#456 P2).
    by_name_path: dict[tuple[str, str], list[str]] = defaultdict(list)
    by_name_dir: dict[tuple[str, str], list[str]] = defaultdict(list)
    sym_order: dict[str, int] = {}
    for nid, n in g.nodes.items():
        if n["type"] == "symbol":
            by_name[n["name"]].append(nid)
            path = n.get("path", "")
            by_name_path[(n["name"], path)].append(nid)
            by_name_dir[(n["name"], path.rpartition("/")[0])].append(nid)
            sym_order[nid] = len(sym_order)

    def named_in(name: str, paths: Iterable[str]) -> list[str]:
        """Symbols called `name` defined in any of `paths`, in `by_name` order."""
        found = [c for p in paths for c in by_name_path.get((name, p), ())]
        found.sort(key=sym_order.__getitem__)
        return found

    def match_bases(rel: str, base: str) -> tuple[list[str], list[str]]:
        """(matched base class ids, all same-named candidates) for one base."""
        # #341: strip C++/Rust "::" and PHP "\" scope/namespace separators
        # too, not just Python/Java "." -- otherwise a namespaced base like
        # `NS::Base` or `\App\Models\Base` never matches the bare name
        # `by_name` indexes symbols under.
        clean_base = (
            base.split("[")[0].split("<")[0].split("::")[-1].split("\\")[-1].split(".")[-1].strip()
        )
        base_cands = by_name.get(clean_base, [])
        local_base = [c for c in base_cands if g.nodes[c].get("path") == rel]
        imp_base = [
            c for c in base_cands if g.nodes[c].get("path") in imported_files.get(rel, set())
        ]
        if local_base:
            return local_base[:1], base_cands
        if imp_base:
            return imp_base[:1], base_cands
        if clean_base not in COMMON_STDLIB_BASES:
            return base_cands[:max_call_candidates], base_cands
        return [], base_cands

    def base_methods(cls_id: str, callee: str) -> list[str]:
        """Same-named methods on `cls_id`'s resolved bases, nearest level first."""
        seen, level = {cls_id}, [cls_id]
        for _depth in range(10):
            nxt: list[str] = []
            for cid in level:
                crel, bases = class_bases.get(cid, ("", []))
                for b in bases:
                    for bid in match_bases(crel, b)[0]:
                        if bid not in seen:
                            seen.add(bid)
                            nxt.append(bid)
            found = [
                m
                for b in nxt
                for m in members.get(
                    scope_of(g.nodes[b]["path"], g.nodes[b]["qualname"]) or "", {}
                ).get(callee, [])
            ]
            if found or not nxt:
                return found
            level = nxt
        return []

    def names_home(rel: str, cd: Record, cand: str) -> bool:
        """Whether a call's receiver visibly names where `cand` lives.

        `Util.remove()` -> a method of `Util`; `store.get()` with `store`
        imported -> a function in `store.py`; `Config::get()` -> a `::` scope.
        """
        if cd.get("receiver_static"):
            return True
        head = cd.get("receiver_head", "")
        if not head:
            return False
        home = _home_names(g.nodes[cand])
        if head.rsplit(".", 1)[-1] in home and head.rsplit(".", 1)[-1][:1].isupper():
            return True  # a type name: Util.remove(), models.User.get()
        return bool(import_bound.get(rel, {}).get(head, set()) & home)

    def tier3_imported(rel: str, callee: str, pool: Container[str]) -> tuple[list[str], str]:
        """Candidates reachable through `rel`'s own imports, and how they matched."""
        imported = imported_files.get(rel)
        if not imported:
            return [], ""
        alias = import_aliases.get(rel, {}).get(callee)
        if alias is not None:
            cands = named_in(alias[1] or callee, imported)
            return (cands, "import_alias") if cands else ([], "")
        cands = [c for c in named_in(callee, imported) if c in pool]
        return (cands, "imported_symbol") if cands else ([], "")

    # `build --scip` (#380): exact targets for the call sites and bases a SCIP
    # index covers; None (the default) leaves every edge below name-resolved.
    scip = ScipResolver.for_build(getattr(config, "scip_index", None), g, parsed)

    for rel, pf in parsed.items():
        rel_dir = rel.rpartition("/")[0]
        by_key = {symbol_key(s): s for s in pf.symbols}
        implicit_this = pf.lang in IMPLICIT_THIS_LANGS
        for sym in pf.symbols:
            sid = f"sym:{rel}::{symbol_key(sym)}"
            scope_id = scope_of(rel, sym.parent)
            parent_sym = by_key.get(symbol_parent_key(sym) or "")
            # A method (its enclosing symbol is a type, or is not in this file:
            # a Go method's receiver type) vs a top-level or nested function.
            in_type = bool(sym.parent) and not (
                parent_sym is not None and parent_sym.kind in ("function", "method")
            )
            # Last segments of the enclosing type's bases: `dict.__repr__(self)`
            # inside `Config(dict).__repr__` names the base's implementation.
            parent_bases = (
                {_last_segment(b) for b in parent_sym.bases}
                if in_type and parent_sym is not None
                else set()
            )
            call_kinds: dict[str, str] = {}
            # name -> the first line this symbol calls that name on. One edge
            # carries a `count` for every call of the same name, so a single
            # line cannot represent all of them; the first is cited because it
            # is the one a reader checking the claim will find, and `count`
            # says how many more there are.
            call_lines: dict[str, int] = {}
            # Every call_details entry per name. One edge covers every call of a
            # name, so the untyped-receiver check below asks whether *all* of
            # them are on a receiver of unknown type (`d.get()`, never
            # `self.get()`, a bare `get()`, or `store.get()` on a module).
            details: dict[str, list[Record]] = defaultdict(list)
            # Names called through super/base/parent and never on self/bare:
            # the caller itself is then never the target.
            super_calls: set[str] = set()
            self_calls: set[str] = set()
            # Names with at least one call that can target the caller itself:
            # a bare call in a function (or in a method, where the language
            # has implicit `this`), or a non-super self/this receiver. A call
            # with an explicit receiver (`current_app.url_for()` inside
            # `url_for`, `cli.main()` inside `main`) never is.
            may_recurse: set[str] = set()
            for cd in getattr(sym, "call_details", []):
                call_kinds[cd["name"]] = cd.get("kind", "static")
                details[cd["name"]].append(cd)
                head = cd.get("receiver_head", "")
                recv = cd.get("receiver")
                if recv == "self" and (head in SUPER_RECEIVERS or head.startswith("super(")):
                    super_calls.add(cd["name"])
                elif recv == "other" and parent_bases and _last_segment(head) in parent_bases:
                    # `Base.method(self)`: an explicit base-class call is a
                    # super call spelled out, and is resolved the same way.
                    super_calls.add(cd["name"])
                elif recv != "other":
                    self_calls.add(cd["name"])
                    if recv == "self" or not in_type or implicit_this:
                        may_recurse.add(cd["name"])
                cd_line = cd.get("line")
                if isinstance(cd_line, int) and cd_line > 0:
                    prev = call_lines.get(cd["name"])
                    if prev is None or cd_line < prev:
                        call_lines[cd["name"]] = cd_line

            for callee, count in Counter(sym.calls).items():
                call_kind = call_kinds.get(callee, "static")
                call_evidence = make_evidence(rel, call_lines.get(callee))
                if scip is not None:
                    # Emits the sites SCIP resolves; the tiers get the rest.
                    count, rest = scip.calls(
                        rel, sid, callee, count, call_kind, details.get(callee, [])
                    )
                    if not count:
                        continue
                    call_evidence = make_evidence(rel, rest) if rest else call_evidence
                all_cands = by_name.get(callee, [])
                if callee not in details:
                    # no receiver information (a call recorded without details):
                    # the historical rule -- only a top-level function recurses.
                    recurse_ok = not sym.parent
                else:
                    recurse_ok = callee in may_recurse
                # The candidate pool for the name tiers: the caller is only a
                # candidate when one of its calls can actually be a self-call.
                # Kept as a set test so no tier walks every same-named symbol.
                pool = _AllExcept(None if recurse_ok else sid)

                chosen_cands: list[str] = []
                res_kind = ""
                scope_dist = 0
                if callee in super_calls and callee not in self_calls:
                    # `super().__init__()` names an ancestor's implementation:
                    # never the caller itself or its own class's method (that
                    # was a self-loop CALLS edge on every overriding method).
                    # Only a same-named method on a resolved base class is a
                    # candidate. The name tiers are not consulted: they bind a
                    # stdlib/third-party base's method to an unrelated class
                    # -- even a subclass -- at 1.0. No in-repo ancestor defines
                    # it -> CALLS_EXTERNAL, which is exactly what is known.
                    if (pkey := symbol_parent_key(sym)) and (
                        inherited := base_methods(f"sym:{rel}::{pkey}", callee)
                    ):
                        chosen_cands, res_kind, scope_dist = inherited, "base_class", 0
                elif recurse_ok and sid in all_cands:
                    # Tier 0: direct recursion. `sid` is in `all_cands` only when
                    # the callee is this symbol's own name, and `recurse_ok` says
                    # some call of it is bare or on self/this -- a receiver-ful
                    # call (`current_app.url_for()`) never is. Without it the
                    # tiers below hand the call to a same-named method elsewhere
                    # in the file at confidence 1.0 and the recursion edge is lost.
                    # Inside a type an overload set comes back whole, at 1/n.
                    tier1 = members.get(scope_id, {}).get(callee, []) if scope_id else []
                    if sym.parent and len(tier1) > 1 and sid in tier1:
                        chosen_cands, res_kind, scope_dist = tier1, "same_class", 0
                    else:
                        chosen_cands, res_kind, scope_dist = [sid], "self_recursive", 0
                elif scope_id and (
                    tier1 := [c for c in members.get(scope_id, {}).get(callee, []) if c != sid]
                ):
                    # Tier 1: same class / enclosing scope, via `members`: every
                    # member of the caller's enclosing symbol (for Go: every
                    # method of the receiver type in the package) named `callee`.
                    # An overload set comes back whole and fans out at 1/n.
                    #
                    # The caller is excluded: a possible self-call was taken by
                    # tier 0, so what reaches here has a non-self receiver
                    # (`c.to_dict()` inside `DoctorReport.to_dict`) or names a
                    # sibling overload.
                    chosen_cands = tier1
                    res_kind, scope_dist = "same_class", 0
                elif tier2 := [c for c in by_name_path.get((callee, rel), ()) if c in pool]:
                    chosen_cands, res_kind, scope_dist = tier2, "same_file", 1
                elif (tier3 := tier3_imported(rel, callee, pool))[0]:
                    chosen_cands, res_kind, scope_dist = tier3[0], tier3[1], 2
                elif tier4 := [c for c in by_name_dir.get((callee, rel_dir), ()) if c in pool]:
                    chosen_cands, res_kind, scope_dist = tier4, "same_module", 3
                elif len(globals_ := [c for c in all_cands if c in pool]) == 1:
                    chosen_cands, res_kind, scope_dist = globals_, "unique_global_name", 4
                elif len(globals_) > 1:
                    chosen_cands, res_kind, scope_dist = globals_, "ambiguous_global_name", 5

                # `os.environ.get(k)` reduces to the name "get", and without
                # this the tiers above bind it to any `get` method in the same
                # directory at confidence 1.0 -- on Flask that made
                # `_AppCtxGlobals.get` the second most-called symbol from dict
                # lookups alone. When every call of a builtin-collection method
                # name is on a receiver of unknown type, the in-repo candidate
                # is kept (it may be right) but priced as the guess it is.
                untyped_builtin = (
                    bool(chosen_cands)
                    and res_kind != "self_recursive"
                    and callee in UNTYPED_RECEIVER_BUILTIN_METHODS
                    # every call of the name is on a receiver of unknown type,
                    # none of which names the candidate's module or type
                    and all(
                        cd.get("receiver") == "other"
                        and not any(names_home(rel, cd, c) for c in chosen_cands)
                        for cd in details.get(callee, [{}])
                    )
                    # A top-level function reached through this file's imports
                    # can only be called on its module, never on a `dict`.
                    and not (
                        res_kind in IMPORT_RESOLUTION_KINDS
                        and all(
                            g.nodes[c].get("kind") == "function"
                            and "." not in g.nodes[c].get("qualname", "")
                            for c in chosen_cands
                        )
                    )
                )

                # The free-function half of the same problem: a bare call to a
                # name the language itself defines. Gated on a *global* match
                # (see BUILTIN_FREE_FUNCTIONS) so a shadowing definition this
                # file owns, inherits or imports by name keeps confidence 1.0;
                # what is left is a cross-file name collision with a builtin.
                shadowed_builtin = (
                    bool(chosen_cands)
                    and callee in BUILTIN_FREE_FUNCTIONS
                    and res_kind not in SCOPED_CALL_KINDS
                    # every call of the name is bare -- `x.len()` is a method
                    # call and the receiver rules above own it
                    and all(cd.get("receiver") == "none" for cd in details.get(callee, [{}]))
                )
                guessed_call = untyped_builtin or shadowed_builtin

                # Add edges
                if not chosen_cands:
                    eid = f"external:{callee}"
                    g.add_node(eid, type="external", name=callee)
                    g.add_edge(
                        sid,
                        eid,
                        "CALLS_EXTERNAL",
                        count=count,
                        resolution_kind="unresolved_external",
                        candidate_count=0,
                        call_kind=call_kind,
                        method=METHOD_NAME_RESOLVER,
                        evidence=call_evidence,
                        # `dst` is a synthetic external:<name> node meaning
                        # "no definition for this name in the repository",
                        # which is exactly what was determined -- so the
                        # target is certain even though the callee is not
                        # ours. See edgemeta on what confidence measures.
                        confidence=1.0,
                    )
                    g.stats["calls_external"] += 1
                elif len(chosen_cands) == 1 and not guessed_call:
                    g.add_edge(
                        sid,
                        chosen_cands[0],
                        "CALLS",
                        count=count,
                        confidence=1.0,
                        resolution_kind=res_kind,
                        candidate_count=len(all_cands),
                        scope_distance=scope_dist,
                        call_kind=call_kind,
                        method=METHOD_NAME_RESOLVER,
                        evidence=call_evidence,
                    )
                    if res_kind == "unique_global_name":
                        g.stats["calls_unique_global"] += 1
                    else:
                        g.stats["calls_scoped"] += 1
                else:
                    limit = min(len(chosen_cands), max_call_candidates)
                    # Both guesses are priced at the same magnitude on purpose:
                    # each means "kept as a possibility, not asserted", and
                    # `edgemeta.counts_as_call` excludes both from the repo map.
                    ceiling = UNTYPED_RECEIVER_CONFIDENCE if guessed_call else 1.0
                    # Confidence reflects candidate set size, never artificially inflated by capping (W12).
                    conf = round(ceiling / len(chosen_cands), 3) if len(chosen_cands) > 0 else 0.0
                    extra: dict[str, bool] = {}
                    if len(chosen_cands) > limit:
                        extra["capped_candidates"] = True
                    if untyped_builtin:
                        extra["untyped_receiver"] = True
                    if shadowed_builtin:
                        extra["shadowed_builtin"] = True
                    for c in chosen_cands[:limit]:
                        g.add_edge(
                            sid,
                            c,
                            "CALLS",
                            count=count,
                            confidence=conf,
                            ambiguous=True,
                            resolution_kind=res_kind,
                            candidate_count=len(all_cands),
                            scope_distance=scope_dist,
                            call_kind=call_kind,
                            method=METHOD_NAME_RESOLVER,
                            evidence=call_evidence,
                            **extra,
                        )
                    g.stats["calls_ambiguous"] += 1
                    g.stats["ambiguous_calls"] += 1
                    if scip is not None:
                        scip.note_ambiguous(rel)
                    if untyped_builtin:
                        g.stats["calls_untyped_receiver"] += 1
                    if shadowed_builtin:
                        g.stats["calls_shadowed_builtin"] += 1

            # Inheritance resolution
            base_details_map = {bd["name"]: bd for bd in getattr(sym, "base_details", [])}
            for base in sym.bases:
                bd = base_details_map.get(base, {})
                raw_base = bd.get("raw", base)
                subtype = bd.get("subtype", "INHERITS")
                if scip is not None and scip.base(rel, sid, sym, base, subtype, raw_base):
                    continue
                matched_base, base_cands = match_bases(rel, base)

                if matched_base:
                    for c in matched_base:
                        g.add_edge(
                            sid,
                            c,
                            "INHERITS",
                            subtype=subtype,
                            raw_base=raw_base,
                            method=METHOD_NAME_RESOLVER,
                            candidate_count=len(base_cands),
                            confidence=round(1.0 / len(matched_base), 3)
                            if len(matched_base) > 1
                            else 1.0,
                            ambiguous=len(matched_base) > 1,
                            # The class header line, from base_details. Falls
                            # back to the symbol's own start line, which is the
                            # same line for every language whose base clause
                            # sits in the class header.
                            evidence=make_evidence(rel, bd.get("line") or sym.start_line),
                        )
                else:
                    g.stats["unresolved_bases"] += 1

    if getattr(config, "reference_edges", False):
        add_reference_edges(g, parsed, imported_files, import_aliases, named_in)

    if getattr(config, "framework_edges", False):
        add_framework_edges(g, parsed, imported_files, import_aliases, named_in)

    if git_history:
        add_cochange(g, root, git_history, file_index, min_pairs=cochange_min, authors=git_authors)

    # Every edge endpoint must be a node. A file can be in file_index (so an
    # IMPORTS target resolves to it, and git log pairs it) yet have no file:
    # node because the main loop skipped it as unreadable — that would leave a
    # dangling edge that turns into a phantom node in the GraphML export.
    before = len(g.edges)
    g.edges = [e for e in g.edges if e["src"] in g.nodes and e["dst"] in g.nodes]
    if len(g.edges) != before:
        g.stats["edges_pruned_dangling"] += before - len(g.edges)

    # After the dangling-edge prune, so every CALLS edge walked has both ends.
    add_tests_edges(g)
    mark_entrypoints(g)
    g.stats["nodes"] = len(g.nodes)
    g.stats["edges"] = len(g.edges)
    # `stats` is a Counter so the 18 `stats[k] += 1` sites get a 0 default, and
    # this is the one entry that holds prose rather than a count. Retyping it as
    # `dict[str, Any]` would take that default away from every one of them, so
    # the narrower suppression is the cheaper trade.
    parse_errors_summary = (
        f"Files with parse errors: {g.stats.get('files_with_parse_errors', 0)}  "
        f"({g.stats.get('cpp_fallback_files', 0)} C/C++ files used cpp fallback)"
    )
    g.stats["parse_errors_summary"] = parse_errors_summary  # type: ignore[assignment]
    return g
