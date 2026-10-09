"""Graph-aware retrieval over a built index: lexical seeds + k-hop expansion."""

import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from .export import atomic_write, path as artifact_path
from .export import paths as artifact_paths
from .integrity import MAX_JSONL_LINE_BYTES, _iter_raw_lines

from .security import (
    SECRET_CONFIG_EXTS,
    SECRET_DIR_NAMES,
    SECRET_EXACT_NAMES,
    SECRET_EXTS,
    SECRET_KEYWORDS,
    SECRET_WORD_RE,
    _is_secret_path,
    redact_content,
)

__all__ = [
    "BM25_FORMAT",
    "CALLEE_EDGE_DIRS",
    "CALLER_EDGE_DIRS",
    "Index",
    "SECRET_CONFIG_EXTS",
    "SECRET_DIR_NAMES",
    "SECRET_EXACT_NAMES",
    "SECRET_EXTS",
    "SECRET_KEYWORDS",
    "SECRET_WORD_RE",
    "_is_secret_path",
    "asks_about_tests",
    "classify_query",
    "is_test_path",
    "SUPPORTED_INDEX_FORMATS",
    "edge_dirs_for",
    "format_pack",
    "is_lexical_weak",
    "read_jsonl",
    "tokenize",
    "write_bm25",
]

SUPPORTED_INDEX_FORMATS = frozenset({"repo2graph/1"})

if TYPE_CHECKING:
    # Type-only: `embed` pulls the optional `rag` extra, and every runtime use
    # below is a lazy import inside a function so a query-only install never
    # needs it. A TYPE_CHECKING import keeps that promise at runtime.
    from .embed import Embedder

# A JSONL record -- a chunk, node or edge as read off disk. `Any` on the value
# is honest rather than lazy: the files are documented as inspectable and
# hand-editable, so a value's type is whatever the file actually holds, which
# is why the readers below use `.get(...) or <default>` throughout.
Record = dict[str, Any]
# One embedding. A Sequence, not list[float], so a numpy row from a real
# sentence-transformers encode() satisfies it without a conversion.
Vector = Sequence[float]
_T = TypeVar("_T")

TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")
# IDENT_RE permits single-character identifiers (e.g. generic type params).
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
QUALNAME_SEP_RE = re.compile(r"::|\.")
SUBTOKEN_RE = re.compile(r"_|(?<=[a-z0-9])(?=[A-Z])")

# BM25 scoring constants: term frequency saturation (K1), length normalization (B).
BM25_K1 = 1.5
BM25_B = 0.75
BM25_AVG_LEN = 400.0

# Exact identifier match multiplier.
IDENT_BOOST = 2.5

# Reciprocal rank fusion: 1/(RRF_K + rank_bm25) + 1/(RRF_K + rank_vec).
RRF_K = 60
RRF_CANDIDATES = 50

# --- Answerability re-ranking, applied to pack_context's seed candidates -----
#
# BM25 ranks a chunk by how much of the question it matches. A *container* chunk
# matches a lot of any question about its contents -- a class docstring is long
# and names everything the class does -- while being, structurally, the one kind
# of chunk that cannot contain the answer: the chunker emits each method as its
# own chunk and cuts them out of the parent, so Flask's `class Flask` chunk
# spans lines 81-1536, costs ~1,150 tokens, and holds not one method body.
#
# Measured on `benchmarks/real/`: container chunks took 54% of pack tokens while
# every evidence definition in the suite was present in some chunk and an oracle
# packer fit 100% of them inside 8,000 tokens. The loss was never retrieval or
# parsing; it was which chunks got the budget.
#
# Penalty rather than exclusion: when the question really is "where is class X
# defined", the container chunk *is* the answer, and at a budget with room to
# spare it should still arrive. 0.15 is deep enough that it never outranks a
# body chunk that matched anything.
CONTAINER_SEED_PENALTY = 0.15

# ...and only once the container is big enough for its presence to cost
# something. The penalty exists because a container crowds bodies out of the
# budget, which a small one does not do: the demo fixture's `app/store.py`
# residual is 91 tokens, names the module that answers "trace a request to
# persistence", and displaces nothing. Flask's `class Flask` is 1,144 tokens
# and displaces four method bodies. Penalising both equally dropped the cheap
# pointer for no budget saved -- caught by `test_demo.py`, not by the
# benchmark, because a 22-question suite does not ask "which file is this in".
CONTAINER_PENALTY_MIN_TOKENS = 200

# A chunk whose declared name shares content words with the question is more
# likely to be the definition being asked about: "how are two config objects
# merged" against `mergeConfig`. `_boost_identifiers` already rewards an *exact*
# identifier match, which only fires when the asker already knows the symbol's
# name; this rewards partial overlap after subtoken splitting, which is what a
# question phrased in prose actually produces. Capped at two matching terms so a
# long qualname cannot accumulate an unbounded bonus.
NAME_TERM_BOOST = 2.0
NAME_TERM_CAP = 2

# How deep into the fused ranking the re-rank reaches. Everything below this is
# left in its original order: the adjustment is a tie-break among plausible
# candidates, not a licence to haul a chunk up from rank 400 on a name match.
RERANK_CANDIDATES = 120

# `k` stays at 8. Raising it to 40 fills the budget with more lexical seeds and
# buys +2 points on the lexical set, but costs the structural set 100% -> 80% at
# 8,000 tokens: the extra seeds crowd out the graph neighbours those questions
# are answered by. The budget is better spent on one hop of expansion than on
# the ninth-best lexical match.

# Question words carry no evidence about which definition answers a question,
# and several ("get", "set") collide with extremely common method names.
QUERY_STOPWORDS = frozenset(
    """a an and are as at be by can do does for from get has have how in into is it its
    make not of on or out set should that the their them then there these this to up
    use used uses using was what when where which who why will with would you your""".split()
)

# Query-side suffix rules: (suffix, replacements). "validation" -> "validate",
# "validating" -> "validat"/"validate", "parsers" -> "parser". Variants are
# added *beside* the original term and only when the index's own vocabulary
# has them, so an identifier literally named `validates` still matches exactly
# and no term the corpus never uses can enter the score.
_STEM_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ation", ("ate",)),
    ("ing", ("", "e")),
    ("ed", ("", "e")),
    ("es", ("", "e")),
    ("s", ("",)),
)
STEM_VARIANT_WEIGHT = 0.5


def stem_variants(term: str) -> list[str]:
    """Light, query-only stems of `term`. Never applied to the index side."""
    out: list[str] = []
    for suffix, repls in _STEM_RULES:
        if len(term) > len(suffix) + 3 and term.endswith(suffix) and not term.endswith("ss"):
            base = term[: -len(suffix)]
            out.extend(base + r for r in repls if base + r != term)
            break
    return out


# Which directions of an edge are useful when expanding from a node:
# callees and callers for CALLS, the defining parent for DEFINES, the base
# class for INHERITS, the imported module for IMPORTS.
DEFAULT_EDGE_DIRS = {
    "CALLS": ("out", "in"),
    "DEFINES": ("in",),
    "INHERITS": ("out",),
    "IMPORTS": ("out",),
}
DEFAULT_EDGE_TYPES = frozenset(DEFAULT_EDGE_DIRS)

# "Follow every direction of every type" — an empty mapping, because expand()
# reads `dirs.get(etype)` and treats a missing entry as "no direction filter".
# retrieve() passes this explicitly so that DEFAULT_EDGE_DIRS, which exists for
# pack_context(), can never narrow what `repo2graph query` has always returned.
ALL_EDGE_DIRS: dict[str, tuple[str, ...]] = {}

# Direction filters per question shape. `expand()` has accepted `edge_dirs`
# since it was written, but nothing derived one from the question, so every
# query got DEFAULT_EDGE_DIRS and `CALLS` was walked both ways at once. With
# `per_hop` slots to fill, the wrong direction arrives first about half the
# time: "what calls prepare_request" came back with `cookiejar_from_dict` and
# `merge_cookies` -- its callees -- and not `Session.request`, its caller, even
# though that edge sits in the graph at confidence 1.0.
#
# `DEFINES` stays "in" throughout: it is how a symbol reaches its enclosing
# file or class, which is orientation rather than a direction the question
# chose. `IMPORTS` flips with the question because "what imports X" and "what
# does X import" are opposite requests.
CALLER_EDGE_DIRS: dict[str, tuple[str, ...]] = {
    "CALLS": ("in",),
    "DEFINES": ("in",),
    "INHERITS": ("in",),
    "IMPORTS": ("in",),
}
CALLEE_EDGE_DIRS: dict[str, tuple[str, ...]] = {
    "CALLS": ("out",),
    "DEFINES": ("in",),
    "INHERITS": ("out",),
    "IMPORTS": ("out",),
}

# Phrases that name a direction. Matched against the lowered query as whole
# words, longest first, so "what are the callees of X" is not read as a caller
# question by the bare word "call".
_CALLEE_PATTERNS = (
    r"\bcallees?\s+of\b",
    r"\bdoes\s+\w+\s+(?:call|use|invoke|depend\s+on)\b",
    r"\bwhat\s+does\s+.+\s+(?:call|use|invoke|depend\s+on)\b",
    r"\b(?:call|use|invoke)s?\s+what\b",
)
_CALLER_PATTERNS = (
    r"\bcallers?\s+of\b",
    r"\b(?:what|who|which)\s+(?:\w+\s+){0,3}?(?:calls?|uses?|invokes?|imports?|references?)\b",
    r"\bwhere\s+is\s+.+\s+(?:used|called|invoked|referenced)\b",
    r"\bused\s+by\b",
    r"\bdepends?\s+on\s+\w+\b",
)
_TRACE_PATTERNS = (
    r"\btrace\b",
    r"\bflows?\s+(?:from|through|to)\b",
    r"\bfrom\s+.+\s+to\s+.+\b",
    r"\bend\s+to\s+end\b",
)
# Asking *about* the tests. "What tests cover <module>" is one of the five
# questions the README leads with, so this shape exists to switch the demotion
# below back off rather than to turn anything on.
_TEST_PATTERNS = (
    r"\btests?\b",
    r"\bspecs?\b",
    r"\btested\b",
    r"\btest\s+coverage\b",
    r"\bunit\s+test",
)

# A test that exercises a thing mentions it constantly, and names the behaviour
# in the words someone would use to ask about it, so it matches a question at
# least as well as the implementation. On the pinned hono checkout -- whose
# indexed src/ is 49% test chunks, because the suite sits beside the source as
# `*.test.ts` -- test paths took 45% of seed slots and 37% of the token budget
# at 4,000 tokens, and 8 of 10 packs held at least one test seed.
#
# The displacement is in the near-ties rather than the top slot: the
# implementation scored 1.185 and a dozen tests scored 1.132. So this is a
# tie-breaker, deliberately mild -- enough to lose a near-tie to real source,
# never enough to bury a test that is the best answer by a distance.
TEST_SEED_PENALTY = 0.6


def is_test_path(path: str) -> bool:
    """Whether a repo-relative path is test code, matched per path component.

    `endswith("test.py")` is true of `latest.py`, `fastest.py` and
    `manifest.py`, so the check is on components and known suffixes -- the bug
    the original version of this predicate was written to fix, which is why the
    property test in `tests/test_properties.py` pins separator invariance.

    It lived in `impact.py` until that module was removed; retrieval is the only
    remaining caller, so it lives here now.
    """
    parts = str(path or "").replace("\\", "/").lower().split("/")
    if any(p in _TEST_DIR_NAMES or p.startswith("test_") for p in parts[:-1]):
        return True
    base = parts[-1]
    return (
        base in ("test.py", "conftest.py")
        or base.startswith("test_")
        or base.endswith(_TEST_BASENAME_SUFFIXES)
    )


_TEST_DIR_NAMES = frozenset({"tests", "test", "__tests__", "spec", "specs"})
_TEST_BASENAME_SUFFIXES = (
    "_test.py",
    "_test.go",
    ".test.ts",
    ".test.js",
    ".test.tsx",
    ".test.jsx",
    ".spec.ts",
    ".spec.js",
    ".spec.tsx",
    ".spec.jsx",
)


def classify_query(query: str) -> str:
    """What shape of answer the question asks for.

    Returns one of `"callers"`, `"callees"`, `"trace"` or `"concept"`.
    `"concept"` is the default and the one that changes nothing: a question that
    named no direction must keep following both, because narrowing it on a guess
    would lose answers that the old behaviour found.

    Callee phrasings are tested first. "What are the callees of X" contains
    "call", so a caller pattern would otherwise claim it.
    """
    q = " ".join((query or "").lower().split())
    if not q:
        return "concept"
    for pat in _CALLEE_PATTERNS:
        if re.search(pat, q):
            return "callees"
    for pat in _CALLER_PATTERNS:
        if re.search(pat, q):
            return "callers"
    for pat in _TEST_PATTERNS:
        if re.search(pat, q):
            return "tests"
    for pat in _TRACE_PATTERNS:
        if re.search(pat, q):
            return "trace"
    return "concept"


def asks_about_tests(query: str) -> bool:
    """Whether the question is asking *about* the tests.

    Deliberately independent of `classify_query`, because the two answers are:
    "which tests use the parser" is a caller question *and* a question about
    tests, and it needs the caller direction filter and the test seeds left
    alone. Reading it off the single shape gave it neither -- the caller pattern
    `(what|who|which)\\s+(\\w+\\s+){0,3}?uses?` claims "which tests use X"
    before `_TEST_PATTERNS` is reached, so `classify_query` answered "callers"
    and the demotion below fired on exactly the chunks the question asked for.
    Reordering the loops would have traded the bug the other way, since
    `\\btests?\\b` matches "what calls the test runner" too.
    """
    q = " ".join((query or "").lower().split())
    return bool(q) and any(re.search(pat, q) for pat in _TEST_PATTERNS)


def edge_dirs_for(shape: str) -> dict[str, tuple[str, ...]] | None:
    """The traversal filter a question shape implies, or None to keep the default."""
    if shape == "callers":
        return CALLER_EDGE_DIRS
    if shape == "callees":
        return CALLEE_EDGE_DIRS
    # "trace" wants to follow a chain outward, which DEFAULT_EDGE_DIRS already
    # does; it is kept as its own label so hops can be tuned separately later.
    return None


# retrieve()'s default budget, named so a caller that has to reproduce its seed
# loop (explain.explain_retrieval) cannot drift from it. Deliberately *not*
# shared with pack_context's identically valued default: the two mean different
# things by budget_chars and must stay separately adjustable (CONTRIBUTING.md, "Two
# budget models coexist").
RETRIEVE_BUDGET_CHARS = 24000

# Token accounting. 4 characters per token is the usual English/code rule of
# thumb; it under-counts dense code and CJK, which is why pack_context takes a
# `count_tokens=` hook a caller can point at a real tokenizer.
CHARS_PER_TOKEN = 4

# pack_context layout constants.
MAP_BUDGET_FRAC = 0.2  # at most this share of the budget goes to the map
MAP_ENTRYPOINTS = 10  # entry points listed in the map prepend
PACK_SEPARATOR = "\n\n---\n\n"  # between the map prepend and the first citation


def read_jsonl(path: Path) -> list[Record]:
    """Load and parse JSONL records from a file with per-line byte ceilings.

    Args:
        path: Path to the JSONL file.

    Returns:
        List of parsed JSON record dictionaries.

    Raises:
        ValueError: If path is a symlink, or if any line exceeds MAX_JSONL_LINE_BYTES,
            or contains invalid JSON.
    """
    p = Path(path)
    if p.is_symlink():
        raise ValueError(f"Refusing to read symlinked artifact: {p}")
    rows: list[Record] = []
    with open(p, "rb") as fh:
        for lineno, raw in enumerate(_iter_raw_lines(fh, MAX_JSONL_LINE_BYTES, str(path)), 1):
            if len(raw) > MAX_JSONL_LINE_BYTES:
                raise ValueError(
                    f"{path}: line {lineno} is {len(raw)} bytes, over the "
                    f"{MAX_JSONL_LINE_BYTES}-byte per-line limit"
                )
            line = raw.decode("utf8", "surrogateescape")
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}: line {lineno} is not valid JSON: {e}") from None
    return rows


def count_tokens(text: str) -> int:
    """The default token estimate: len(text) // CHARS_PER_TOKEN, never 0 for a
    non-empty string (a block that costs nothing would defeat any budget)."""
    return max(1, len(text) // CHARS_PER_TOKEN) if text else 0


#: Reported as `token_count_method` in every pack: this is an estimate, and a
#: caller comparing it with a model's real limit needs to know that (#290).
count_tokens.token_count_method = "heuristic"  # type: ignore[attr-defined]


# Bound at import so pack_context's `count_tokens=` parameter, which shadows
# the name inside the method, can still reach the default.
_DEFAULT_MEASURE = count_tokens


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for t in TOKEN_RE.findall(text):
        low = t.lower()
        out.append(low)
        parts = SUBTOKEN_RE.split(t)
        out += [p.lower() for p in parts if len(p) > 2 and p.lower() != low]
    return out


def _chunk_signature(text: str) -> str:
    """Extract the first non-header, non-blank declaration line from a chunk's text."""
    lines = text.split("\n")
    i = 0
    while i < len(lines) and lines[i].startswith("#"):
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    return lines[i].strip() if i < len(lines) else ""


def is_lexical_weak(
    ranked_scores: Sequence[tuple[float, int]],
    k: int = 8,
    min_top_score: float = 18.0,
    flat_ratio: float = 1.35,
    num_chunks: int | None = None,
) -> bool:
    """Determine whether lexical evidence for a query is weak or ambiguous.

    A query has weak lexical evidence if:
    1. No candidates were found at all.
    2. The top-ranked BM25 score is below `min_top_score` (no strong keyword matches).
    3. The score distribution across the top-k is flat (`top_score / s_k < flat_ratio`),
       indicating ambiguity among candidates rather than a clear winner.

    `min_top_score` is an absolute BM25 magnitude, so `ranked_scores` must be a
    *lexical* ranking. Fused reciprocal-rank scores (`score_rrf` with vectors or
    an embedder) are around 0.016 at rank 1 and sit below every floor, which
    makes the answer unconditionally True; callers on the hybrid path go through
    `Index._lexical_for_weakness` for that reason.

    Args:
        ranked_scores: Ranked (score, index) pairs from BM25/lexical scoring.
        k: Number of candidates to evaluate for flatness.
        min_top_score: Absolute score floor below which lexical match is considered weak.
        flat_ratio: Ratio of top-1 score to top-k score below which distribution is flat.
        num_chunks: Total chunks in index to scale floor for tiny test fixtures.

    Returns:
        True if lexical evidence is weak and graph expansion is recommended; False otherwise.
    """
    if not ranked_scores:
        return True
    top_score = ranked_scores[0][0]
    floor = min_top_score
    if num_chunks is not None and num_chunks < 100:
        floor = min(min_top_score, 1.0)
    if top_score < floor:
        return True
    if len(ranked_scores) >= k:
        s_k = ranked_scores[k - 1][0]
        if s_k > 0 and (top_score / s_k) < flat_ratio:
            return True
    return False


BM25_FORMAT = "repo2graph/bm25-1"


def write_bm25(
    chunks: Sequence[Record],
    bm25_path: Path,
    meta_path: Path,
    build_id: str | None = None,
) -> None:
    """Serialize the BM25 inverted index and metadata for fast startup.

    Args:
        chunks: Sequence of chunk dictionaries.
        bm25_path: Target path for bm25.jsonl.
        meta_path: Target path for bm25.meta.json.
        build_id: Build UUID binding this lexical index to manifest.json.
    """
    chunk_ids: list[str] = []
    lengths: list[int] = []
    postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for i, c in enumerate(chunks):
        chunk_ids.append(str(c.get("id") or ""))
        terms = tokenize(c.get("text") or "") + tokenize(c.get("qualname") or "") * 3
        name = c.get("name") or ""
        if len(name) == 1 and IDENT_RE.fullmatch(name):
            terms += [name.lower()] * 3
        counts = Counter(terms)
        lengths.append(sum(counts.values()) or 1)
        for term, n in counts.items():
            postings[term].append((i, n))

    n = len(chunks)
    avgdl = (sum(lengths) / n) if n else BM25_AVG_LEN

    meta: dict[str, Any] = {
        "format": BM25_FORMAT,
        "count": n,
        "avgdl": avgdl,
        "chunk_ids": chunk_ids,
        "lengths": lengths,
    }
    if build_id is not None:
        meta["build_id"] = build_id
    with atomic_write(meta_path, "w", encoding="utf8", newline="\n") as fh:
        fh.write(json.dumps(meta, indent=2) + "\n")

    with atomic_write(bm25_path, "w", encoding="utf8", newline="\n") as fh:
        for term in sorted(postings):
            rec = {"t": term, "p": postings[term]}
            line = json.dumps(rec, ensure_ascii=False)
            fh.write(line + "\n")


class Index:
    # (fused, candidates) for the most recent score_rrf call, or None if no
    # fused query has run on this Index yet. A class-level default so every
    # Index has the attribute without __init__ having to care.
    fusion_coverage: tuple[int, int] | None = None
    # The source tree this index describes, when the caller knows it (the MCP
    # server sets it from its --repo). None means "not known here".
    repo_root: Path | None = None

    def __init__(self, outdir: Path):
        self.dir = Path(outdir).resolve()
        self._overview: str | None = None
        self._manifest: dict[str, Any] | None = None
        self._check_manifest_format()
        self.chunks = read_jsonl(artifact_path(self.dir, "chunks.jsonl"))
        self.nodes = {n["id"]: n for n in read_jsonl(artifact_path(self.dir, "nodes.jsonl"))}
        self.edges = read_jsonl(artifact_path(self.dir, "edges.jsonl"))
        # node id -> (other node id, edge type, "in"|"out", the edge record)
        self.adj: dict[str, list[tuple[str, str, str, Record]]] = defaultdict(list)
        for e in self.edges:
            # The edge record itself (not a copy) rides along: traversal needs
            # `confidence` on CALLS, and `count` on CO_CHANGE, without a lookup.
            self.adj[e["src"]].append((e["dst"], e["type"], "out", e))
            self.adj[e["dst"]].append((e["src"], e["type"], "in", e))
        # The repo map, for pack_context()'s prepend. Both are optional: a
        # `--formats jsonl` build writes no overview.md, and a build interrupted
        # before write_manifest leaves no manifest.json. Neither may raise here.
        self.by_node: dict[str, list[Record]] = defaultdict(list)
        for c in self.chunks:
            self.by_node[c["node_id"]].append(c)
        if not self._load_bm25():
            self._build_bm25()
        self.vectors: dict[int, Vector] | None = None
        self.vector_meta: dict[str, Any] | None = None
        # Why a vectors.npy that exists was not used. None when there is no file
        # or it loaded; a dropped vector set must be reportable, never silent.
        self.vectors_unavailable: str | None = None
        self._load_vectors()

    def _build_bm25(self) -> None:
        """Compute the BM25 inverted index dynamically across all chunks."""
        self.df: Counter[str] = Counter()
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.lengths: list[int] = []
        for i, c in enumerate(self.chunks):
            # `or ""`: a hand-edited chunks.jsonl (the manifest says records are
            # inspectable) with a null text/qualname must not TypeError in re.findall.
            terms = tokenize(c.get("text") or "") + tokenize(c.get("qualname") or "") * 3
            # Single-character declared identifier (e.g. generic type param):
            name = c.get("name") or ""
            if len(name) == 1 and IDENT_RE.fullmatch(name):
                terms += [name.lower()] * 3
            counts = Counter(terms)
            self.lengths.append(sum(counts.values()) or 1)
            for term, n in counts.items():
                self.postings[term].append((i, n))
            self.df.update(counts.keys())
        self.N = len(self.chunks)
        self.avgdl = (sum(self.lengths) / self.N) if self.N else BM25_AVG_LEN

    def _load_bm25(self) -> bool:
        """Load precomputed BM25 inverted index from disk if present and matching.

        Returns:
            True if BM25 data was loaded successfully from disk, False otherwise.
        """
        meta_p = artifact_path(self.dir, "bm25.meta.json")
        bm25_p = artifact_path(self.dir, "bm25.jsonl")
        if not meta_p.is_file() or not bm25_p.is_file():
            return False
        if meta_p.is_symlink() or bm25_p.is_symlink():
            return False
        try:
            if not meta_p.resolve().is_relative_to(self.dir.resolve()):
                return False
            if not bm25_p.resolve().is_relative_to(self.dir.resolve()):
                return False
        except OSError:
            return False

        try:
            from .integrity import MAX_METADATA_BYTES, iter_jsonl_bounded, read_bounded

            meta = json.loads(
                read_bounded(meta_p, MAX_METADATA_BYTES, what="bm25.meta.json").decode(
                    "utf8", "replace"
                )
            )
            if not isinstance(meta, dict) or meta.get("format") != BM25_FORMAT:
                return False
            m_build_id = self.manifest.get("build_id")
            v_build_id = meta.get("build_id")
            if m_build_id and v_build_id and m_build_id != v_build_id:
                return False
            if meta.get("count") != len(self.chunks):
                return False
            meta_chunk_ids = meta.get("chunk_ids")
            if not isinstance(meta_chunk_ids, list):
                return False
            for c, cid in zip(self.chunks, meta_chunk_ids):
                if c.get("id") != cid:
                    return False

            lengths = meta.get("lengths")
            if not isinstance(lengths, list) or len(lengths) != len(self.chunks):
                return False

            postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
            df: Counter[str] = Counter()
            for _lineno, rec in iter_jsonl_bounded(bm25_p):
                if not isinstance(rec, dict):
                    continue
                term = rec.get("t")
                raw_p = rec.get("p")
                if not isinstance(term, str) or not isinstance(raw_p, list):
                    continue
                plist = [(int(idx), int(cnt)) for idx, cnt in raw_p]
                postings[term] = plist
                df[term] = len(plist)

            self.postings = postings
            self.df = df
            self.lengths = [int(x) for x in lengths]
            self.N = len(self.chunks)
            self.avgdl = float(meta.get("avgdl", BM25_AVG_LEN))
            return True
        except (OSError, ValueError, KeyError):
            return False

    def _load_vectors(self) -> None:
        """Load dense chunk vectors and metadata from vectors.npy if present."""
        npy = artifact_path(self.dir, "vectors.npy")
        if not npy.exists():
            return
        try:
            from .embed import load_vectors

            by_id, meta = load_vectors(npy)
        except (OSError, ValueError, KeyError, ImportError) as exc:
            self.vectors_unavailable = f"vectors.npy could not be loaded: {exc}"
            return
        pos = {c.get("id"): i for i, c in enumerate(self.chunks)}
        vectors: dict[int, Vector] = {pos[cid]: vec for cid, vec in by_id.items() if cid in pos}
        if not vectors:
            self.vectors_unavailable = "vectors.npy matches none of this index's chunks"
            return
        m_build_id = self.manifest.get("build_id")
        v_build_id = meta.get("build_id")
        if m_build_id and v_build_id and m_build_id != v_build_id:
            self.vectors_unavailable = "vectors.npy was embedded for a different build"
            return
        self.vectors, self.vector_meta = vectors, meta

    def fuse_ok(self, embedder: "Embedder") -> tuple[bool, str]:
        """Verify embedder compatibility with index vector dimensions and model.

        Args:
            embedder: Embedder instance to validate.

        Returns:
            Tuple of (is_compatible, error_message).
        """
        if not self.vectors or not self.vector_meta:
            why = f" ({self.vectors_unavailable})" if self.vectors_unavailable else ""
            return False, (
                f"no vectors in the index at {self.dir}{why}: "
                f"run `repo2graph embed -o {self.dir}` first"
            )
        from .embed import dim_of, model_id_of

        index_model = self.vector_meta.get("model_id")
        query_model = model_id_of(embedder)
        if index_model != query_model:
            return False, (
                f"embedding model mismatch: the index was built with "
                f"{index_model!r} but the active embedder is {query_model!r}; "
                f"re-run `repo2graph embed -o {self.dir} --model {index_model}` "
                f"or query with --no-vectors"
            )
        index_dim = self.vector_meta.get("dim")
        try:
            query_dim = dim_of(embedder)
        except Exception as exc:  # noqa: BLE001 - resilience boundary: external embedder model inference failure
            return False, f"the active embedder could not be measured: {exc}"
        if index_dim != query_dim:
            return False, (
                f"embedding width mismatch: the index vectors are {index_dim} "
                f"wide but the active embedder returns {query_dim}"
            )
        return True, ""

    def _load_text(self, name: str) -> str:
        """Read an optional artifact, trying every section it is written to.

        newline="\n" for the same reason read_jsonl uses it: universal-newline
        mode rewrites U+2028/U+2029/U+0085 line ends and would desync the text
        from what was written.

        overview.md is the one artifact split across two sections with
        different content: human/overview.md is the structured table view for
        a person, agent/overview.md is the terse prose the GraphRAG protocol
        (and this repo map) are built around. Try the agent copy first so
        pack_context/rag output keeps reading the prose; fall back to human/
        only when agent/overview.md is missing, e.g. a hand-built fixture that
        writes just one copy.
        """
        paths = artifact_paths(self.dir, name)
        if name == "overview.md" and len(paths) > 1:
            paths = list(reversed(paths))
        for p in paths:
            if p.is_symlink():
                continue
            try:
                if not p.resolve().is_relative_to(self.dir.resolve()):
                    continue
            except OSError:
                continue
            try:
                with open(p, encoding="utf8", newline="\n") as fh:
                    return fh.read()
            except (OSError, UnicodeDecodeError):
                continue
        return ""

    def _check_manifest_format(self) -> None:
        p = artifact_path(self.dir, "manifest.json")
        if not p.is_file() or p.is_symlink():
            return
        try:
            if not p.resolve().is_relative_to(self.dir.resolve()):
                return
        except OSError:
            return
        try:
            with open(p, encoding="utf8", newline="\n") as fh:
                data = json.load(fh)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return
        if isinstance(data, dict):
            fmt = data.get("format")
            if fmt and fmt not in SUPPORTED_INDEX_FORMATS:
                raise ValueError(
                    f"Unsupported index format: '{fmt}'. Expected one of: {sorted(SUPPORTED_INDEX_FORMATS)}"
                )

    def _load_manifest(self) -> dict[str, Any]:
        p = artifact_path(self.dir, "manifest.json")
        if p.is_symlink():
            return {}
        try:
            if not p.resolve().is_relative_to(self.dir.resolve()):
                return {}
        except OSError:
            return {}
        try:
            with open(p, encoding="utf8", newline="\n") as fh:
                data = json.load(fh)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        if isinstance(data, dict):
            fmt = data.get("format")
            if fmt and fmt not in SUPPORTED_INDEX_FORMATS:
                raise ValueError(
                    f"Unsupported index format: '{fmt}'. Expected one of: {sorted(SUPPORTED_INDEX_FORMATS)}"
                )
            return data
        return {}

    @property
    def overview(self) -> str:
        if self._overview is None:
            self._overview = self._load_text("overview.md")
        return self._overview

    @overview.setter
    def overview(self, value: str) -> None:
        self._overview = value

    @property
    def manifest(self) -> dict[str, Any]:
        if self._manifest is None:
            self._manifest = self._load_manifest()
        return self._manifest

    @manifest.setter
    def manifest(self, value: dict[str, Any]) -> None:
        self._manifest = value

    _is_secret_path = staticmethod(_is_secret_path)

    def _served(self, c: Record) -> Record:
        """Return chunk record with content redacted according to policy."""
        policy = self.manifest.get("secret_filter_policy")
        if policy in ("redact-match", "exclude-file"):
            return c
        text = c.get("text")
        if not isinstance(text, str) or not text:
            return c
        red, n = redact_content(text)
        return {**c, "text": red} if n else c

    def score(self, query: str) -> list[tuple[float, int]]:
        """Rank indexed chunks against query using BM25 with identifier boosting.

        Args:
            query: Search terms or question string.

        Returns:
            Sorted list of (bm25_score, chunk_index) tuples.
        """
        # Support single-character identifiers matching declared symbols:
        terms = tokenize(query) + [t.lower() for t in IDENT_RE.findall(query) if len(t) == 1]
        # Question words stay in (#382 tried dropping them, twice). Their idf
        # is already near zero, and in a code index they are not noise: "from"
        # matches SQL `FROM`, and even "an" alone ("rather than an argument")
        # was the margin that kept app/store.py in the demo's "route to
        # persistence" answer (test_demo.py). Even a set limited to words that
        # are no language's keyword lost it.
        q: Counter[str] = Counter(terms)
        weights: dict[str, float] = dict.fromkeys(q, 1.0)
        for term in list(q):
            for variant in stem_variants(term):
                if variant not in q and variant in self.postings:
                    q[variant] = q[term]
                    weights[variant] = STEM_VARIANT_WEIGHT
        acc: dict[int, float] = defaultdict(float)
        for term, count in q.items():
            qn = count * weights[term]
            posting = self.postings.get(term)
            if not posting:
                continue
            idf = math.log(1 + self.N / (1 + self.df[term]))
            for i, cnt in posting:
                length = self.lengths[i]
                acc[i] += (
                    qn
                    * idf
                    * (cnt / (cnt + BM25_K1 * ((1.0 - BM25_B) + BM25_B * length / self.avgdl)))
                )
        self._boost_identifiers(query, acc)
        scored = [(s, i) for i, s in acc.items() if s]
        scored.sort(reverse=True)
        return scored

    def _demote_test_seeds(self, ranked: list[tuple[float, int]]) -> list[tuple[float, int]]:
        """Re-rank a seed window so test files lose near-ties to real source.

        Applied only when the question was not *about* tests, and only inside
        `pack_context`: `retrieve()` must keep returning what `repo2graph query`
        has always returned, so the penalty never reaches it.

        A stable sort on the scaled score, so two chunks the penalty does not
        separate keep the order BM25 gave them.
        """
        if not ranked:
            return ranked
        rescored = [
            (s * TEST_SEED_PENALTY if is_test_path(self.chunks[i].get("path") or "") else s, i)
            for s, i in ranked
        ]
        rescored.sort(key=lambda si: si[0], reverse=True)
        return rescored

    @staticmethod
    def is_container_chunk(chunk: Mapping[str, Any]) -> bool:
        """Is this a chunk that structurally cannot hold a nested definition?

        Two kinds qualify, and both for the same reason -- the chunker emits
        their members as separate chunks and cuts them out of the parent, so
        what is left is a header, a docstring and metadata:

        * a `class` chunk, whose methods are their own chunks;
        * a `file:` residual, which is whatever a file has left once its
          symbols have been extracted.

        They are genuine navigation aids and they are the right answer to
        "where is class X defined". They are the wrong place to spend a seed
        slot on "how does X work", which is what `CONTAINER_SEED_PENALTY`
        corrects.
        """
        return chunk.get("kind") == "class" or str(chunk.get("node_id", "")).startswith("file:")

    @staticmethod
    def query_content_terms(query: str) -> frozenset[str]:
        """The question's content words, for `_answerability`."""
        return frozenset(t for t in tokenize(query) if len(t) > 2 and t not in QUERY_STOPWORDS)

    def _answerability(
        self,
        query_terms: frozenset[str],
        chunk: Mapping[str, Any],
        *,
        boost_tests: bool = False,
    ) -> float:
        """Multiplier on a chunk's fused score, from how answer-shaped it is.

        Deliberately a re-rank over `score_rrf`'s output rather than a change to
        BM25 itself: RRF maps scores onto `1/(60 + rank)`, which is nearly flat
        across the top of the list, so a modest multiplier here reorders
        candidates that BM25 could not separate -- without touching the lexical
        scoring that `repo2graph query` and every golden test depend on.

        `boost_tests` defaults to False -- the safe case, since a question is
        usually not about tests -- so a caller has to ask for the test boost
        rather than get it by forgetting the argument. `pack_context` passes it
        explicitly from the query shape. See the name-boost comment below.
        """
        # One size test governs both halves of the container rule. A container
        # big enough to displace bodies is penalised *and* withheld from the
        # name boost; a cheap residual is neither. Splitting the two was a
        # defect found by the merge: `CONTAINER_PENALTY_MIN_TOKENS` spared
        # `app/store.py`'s 91-token residual the penalty, but withholding the
        # boost unconditionally still left it to lose its slot to every
        # `*_order` function that could collect 1.5x -- so the demo's "trace an
        # order request from route to persistence" stopped citing the module
        # that holds the persistence. Protecting a chunk from the penalty and
        # then denying it the boost is not protection.
        bulky_container = (
            self.is_container_chunk(chunk)
            and count_tokens(chunk.get("text") or "") > CONTAINER_PENALTY_MIN_TOKENS
        )
        score = 1.0
        if bulky_container:
            score *= CONTAINER_SEED_PENALTY
        # The name boost is for finding an implementation, so it is withheld
        # from bulky containers. Applied to them it rewards a name collision
        # rather than an answer: on the demo fixture, "trace an order *request*"
        # promoted a test helper class literally named `Request` to rank 1,
        # ahead of every route body the question is about. (That class is also a
        # test path, which the rule below withholds from independently.)
        #
        # Export aliases are withheld for the same reason, and it is the sharper
        # case: an alias is *nothing but* a name, so it is a guaranteed maximal
        # name match and a guaranteed non-answer. The seed loop skips aliases
        # outright, so boosting one cannot put it in a pack -- but it can push a
        # real candidate out of the `k * 3` seed window, which is the one way an
        # alias could still cost an answer after being made unseedable.
        #
        # Test definitions are withheld too, and this one was found by the
        # merge: a test *names the behaviour in the question's own words*, so it
        # collects the boost far more reliably than the implementation does.
        # `test_middleware_chaining_runs_handlers_in_order` overlaps "how are
        # middleware handlers chained together" on two terms and takes the full
        # 2.0x; `compose_middleware`, which is the answer, overlaps on one and
        # takes 1.5x. Boosting by name and demoting by path then fight, and the
        # boost wins: 2.0 * TEST_SEED_PENALTY = 1.2 still beats an unboosted
        # source chunk. Withholding it is what makes the two compose -- the
        # name boost is for finding an implementation, and a test is not one.
        if (
            query_terms
            and not bulky_container
            and chunk.get("kind") != "alias"
            and (boost_tests or not is_test_path(chunk.get("path") or ""))
        ):
            names = tokenize(chunk.get("qualname") or chunk.get("name") or "")
            overlap = len(query_terms & set(names))
            if overlap:
                scale = min(overlap, NAME_TERM_CAP) / NAME_TERM_CAP
                score *= 1.0 + (NAME_TERM_BOOST - 1.0) * scale
        return score

    def _boost_identifiers(self, query: str, acc: dict[int, float]) -> None:
        """Apply multiplier to BM25 scores for chunks matching query identifier names."""
        # IDENT_RE supports single-character identifier names:
        idents = set(IDENT_RE.findall(query))
        if not idents:
            return
        lowered = {t.lower() for t in idents}
        for i in list(acc):
            c = self.chunks[i]
            qual = c.get("qualname") or ""
            names = {c.get("name") or "", QUALNAME_SEP_RE.split(qual)[-1] if qual else ""}
            names.discard("")
            if not names:
                continue
            if names & idents or {n.lower() for n in names} & lowered:
                acc[i] *= IDENT_BOOST

    def _lexical_for_weakness(
        self,
        query: str,
        ranked: list[tuple[float, int]],
        vectors: Mapping[Any, Vector] | None,
        embedder: "Embedder | None",
    ) -> list[tuple[float, int]]:
        """The BM25 ranking `is_lexical_weak` has to see, given a possibly fused one.

        `is_lexical_weak`'s floors are absolute BM25 magnitudes
        (`min_top_score=18.0`). Reciprocal-rank fusion scores are `w/(60+rank)`
        -- about 0.016 at rank 1 -- so handing it `score_rrf` output whenever
        vectors or an embedder were supplied put `top_score` below every floor
        and made the answer unconditionally "weak". `conditional_expansion`
        therefore suppressed nothing at all on the hybrid path, silently, while
        working as documented on the lexical one.

        `score_rrf` returns the lexical list unchanged when there is nothing to
        fuse with, so that case reuses `ranked` rather than scoring twice.
        """
        if vectors is None and embedder is None:
            return ranked
        return self.score(query)

    def score_rrf(
        self,
        query: str,
        vectors: Mapping[Any, Vector] | None = None,
        embedder: "Embedder | None" = None,
        weights: tuple[float, float] = (1.0, 1.0),
    ) -> list[tuple[float, int]]:
        """Score chunks combining lexical BM25 and dense vector rankings via RRF.

        Args:
            query: Query string.
            vectors: Optional mapping of chunk indices to precomputed vectors.
            embedder: Optional active embedder instance.
            weights: Optional tuple of (w_bm25, w_vec) multipliers for reciprocal ranks.

        Returns:
            Sorted list of (rrf_score, chunk_index) tuples.
        """
        base = self.score(query)
        if vectors is None and embedder is None:
            return base
        candidates = [i for _s, i in base[:RRF_CANDIDATES]]
        if not candidates:
            return base
        qvec, cvecs, why = self._vectors_for(query, candidates, vectors, embedder)
        if qvec is None or not cvecs:
            # The failure this branch used to hide. `fuse_ok` can pass -- model
            # and width both agree -- and fusion can still turn itself off here,
            # because it needs a vector for *every* candidate and a chunks.jsonl
            # rebuilt without a re-`embed` leaves some without one. Silence then
            # means a lexical answer to a question the caller explicitly asked
            # to be answered densely. Say so instead.
            from .events import emit

            emit(
                "rag_fusion_disabled",
                level="warning",
                reason=why,
                candidates=len(candidates),
                fused=0,
                action="re-run `repo2graph embed` to vectorise every chunk",
            )
            self.fusion_coverage = (0, len(candidates))
            return base
        self.fusion_coverage = (len(cvecs), len(candidates))
        by_sim = sorted(
            range(len(candidates)), key=lambda p: (-_cosine(qvec, cvecs[p]), candidates[p])
        )
        vec_rank = {candidates[p]: r for r, p in enumerate(by_sim, 1)}
        w_bm25, w_vec = weights
        fused: list[tuple[float, int]] = []
        for rank, (_s, i) in enumerate(base, 1):
            score = w_bm25 / (RRF_K + rank)
            if i in vec_rank:
                score += w_vec / (RRF_K + vec_rank[i])
            fused.append((score, i))
        fused.sort(reverse=True)
        return fused

    def _vectors_for(
        self,
        query: str,
        candidates: list[int],
        vectors: Mapping[Any, Vector] | None,
        embedder: "Embedder | None",
    ) -> tuple[Vector | None, list[Vector], str]:
        """Query vector plus one vector per candidate, or a reason there is none.

        Args:
            query: The raw query string.
            candidates: Chunk list-indices BM25 shortlisted, in rank order.
            vectors: Index-keyed vector mapping, or None to embed on the fly.
            embedder: An object with `.encode(list[str])`, or None.

        Returns:
            `(query_vector, candidate_vectors, reason)`. On success `reason` is
            the empty string; on failure the first two are `None`/`[]` and
            `reason` names *why*, so the caller can report a fusion that
            switched itself off instead of degrading in silence.
        """
        if vectors is not None:
            try:
                cvecs = [vectors[i] for i in candidates]
                qvec = vectors.get("query") if hasattr(vectors, "get") else None
            except (KeyError, IndexError, TypeError):
                # All-or-nothing by design: one unvectorised candidate abandons
                # the dense ranking rather than ranking a subset against a
                # different scale. Count how many are actually missing so the
                # message can say whether this is one stale chunk or all of them.
                missing = sum(1 for i in candidates if not _has_vector(vectors, i))
                return (
                    None,
                    [],
                    (
                        f"{missing} of {len(candidates)} BM25 candidates have no "
                        f"vector; chunks.jsonl was likely rebuilt without re-running "
                        f"`repo2graph embed`"
                    ),
                )
            if qvec is None and embedder is not None:
                qvec = _first(embedder.encode([query]))
            if qvec is None:
                return (
                    None,
                    [],
                    ("no query vector: neither the vectors mapping nor an embedder supplied one"),
                )
            reason = _dim_mismatch_reason(qvec, cvecs)
            if reason:
                return None, [], reason
            return qvec, cvecs, ""
        if embedder is None:
            # Unreachable from score_rrf, which returns before calling this when
            # both are None. Stated as a reason rather than an assert because
            # every other failure here is a reason, and a private helper that
            # raises for one caller mistake and returns for the rest is worse.
            return None, [], "no vectors mapping and no embedder to build one with"
        texts = [self.chunks[i].get("text") or "" for i in candidates]
        encoded: list[Vector] = list(embedder.encode([query] + texts))
        if len(encoded) != len(texts) + 1:
            return (
                None,
                [],
                (f"embedder returned {len(encoded)} vectors for {len(texts) + 1} texts"),
            )
        qvec, cvecs = encoded[0], encoded[1:]
        reason = _dim_mismatch_reason(qvec, cvecs)
        if reason:
            return None, [], reason
        return qvec, cvecs, ""

    def expand(
        self,
        seed_nodes: Iterable[str],
        hops: int = 1,
        edge_types: Iterable[str] | None = None,
        per_hop: int = 6,
        min_confidence: float = 1.0,
        edge_dirs: Mapping[str, tuple[str, ...]] | None = None,
    ) -> list[tuple[str, str, str, str]]:
        """Traverse graph outward from seed nodes.

        Args:
            seed_nodes: Starting node IDs.
            hops: Traversal depth in hops.
            edge_types: Allowed edge type names.
            per_hop: Max neighbors admitted per hop.
            min_confidence: Minimum confidence threshold for CALLS edges.
            edge_dirs: Direction filter mapping per edge type.

        Returns:
            List of (dst_node_id, edge_type, direction, src_node_id) tuples.
        """
        wanted = frozenset(edge_types) if edge_types else DEFAULT_EDGE_TYPES
        dirs: Mapping[str, tuple[str, ...]] = DEFAULT_EDGE_DIRS if edge_dirs is None else edge_dirs
        seed_list = list(seed_nodes)
        seen: set[str] = set(seed_list)
        frontier: list[str] = seed_list
        order: list[tuple[str, str, str, str]] = []
        for _ in range(hops):
            if not frontier:
                break
            nxt: list[str] = []
            cap = per_hop * len(frontier)
            for nid in frontier:
                if len(nxt) >= cap:
                    break
                added_for_nid = 0
                for dst, etype, direction, edge in self.adj.get(nid, []):
                    if etype not in wanted or dst in seen:
                        continue
                    allowed = dirs.get(etype)
                    if allowed is not None and direction not in allowed:
                        continue
                    if etype == "CALLS":
                        try:
                            conf = float(edge.get("confidence", 1.0))
                        except (ValueError, TypeError):
                            conf = 0.0
                        if conf < min_confidence:
                            continue
                    seen.add(dst)
                    nxt.append(dst)
                    order.append((dst, etype, direction, nid))
                    added_for_nid += 1
                    if added_for_nid >= per_hop or len(nxt) >= cap:
                        break
            frontier = nxt
        return order

    def retrieve(
        self,
        query: str,
        k: int = 8,
        hops: int = 1,
        budget_chars: int = RETRIEVE_BUDGET_CHARS,
        *,
        min_confidence: float | None = None,
        vectors: Mapping[Any, Vector] | None = None,
        embedder: "Embedder | None" = None,
        exclude_secrets: bool = False,
        extra_secret_keywords: list[str] | None = None,
        extra_secret_dirs: list[str] | None = None,
        neighbours: str = "full",
        conditional_expansion: bool = False,
    ) -> list[Record]:
        """Retrieve ranked seed chunks and graph neighbors within character budget.

        Args:
            query: Search terms or question string.
            k: Number of initial seed chunks.
            hops: Traversal depth around seeds.
            budget_chars: Maximum character budget across returned chunk bodies.
            min_confidence: Minimum confidence threshold for CALLS traversal.
            vectors: Optional precomputed vector mapping.
            embedder: Optional Embedder for hybrid search.
            exclude_secrets: Whether to filter secret-matching paths.
            extra_secret_keywords: Additional sensitive keywords to filter.
            extra_secret_dirs: Additional sensitive directory names to filter.
            neighbours: Neighbour rendering mode: 'full' emits complete chunk text;
                'cite' emits one-line signature citations without chunk body.
            conditional_expansion: If True, only expands graph neighbours when lexical
                evidence is weak or ambiguous.

        Returns:
            List of matching chunk dictionaries.
        """
        conf = 0.0 if min_confidence is None else min_confidence

        def _secret(c: Record, nid: str) -> bool:
            if not exclude_secrets:
                return False
            c_path = c.get("path") or self.nodes.get(nid, {}).get("path") or ""
            return _is_secret_path(
                c_path, extra_keywords=extra_secret_keywords, extra_dirs=extra_secret_dirs
            )

        ranked = (
            self.score(query)
            if vectors is None and embedder is None
            else self.score_rrf(query, vectors=vectors, embedder=embedder)
        )
        scored = ranked[: k * 3]
        picked: list[Record] = []
        seen_nodes_list: list[str] = []
        seen_nodes_set: set[str] = set()
        used = 0
        for s, i in scored:
            c = self.chunks[i]
            nid = c["node_id"]
            if nid in seen_nodes_set or _secret(c, nid):
                continue
            # Redact first, then measure. The headroom test used to read the
            # stored length while `used` accumulated the served one, and
            # `redact_content` replaces a secret with a longer `[REDACTED:...]`
            # marker, so `budget_chars` was overshot by the growth of every
            # redacted chunk. `pack_context` already redacts before it measures.
            if exclude_secrets:
                c = self._served(c)
            chunk_len = len(c.get("text") or "")
            # Verify budget headroom before appending to avoid overshooting:
            if picked and used + chunk_len > budget_chars:
                break
            seen_nodes_set.add(nid)
            seen_nodes_list.append(nid)
            picked.append({**c, "score": round(s, 3), "why": "lexical"})
            used += chunk_len
            if len(picked) >= k or used >= budget_chars:
                break
        # Bound the expansion pass by both count and budget:
        if conditional_expansion and not is_lexical_weak(
            self._lexical_for_weakness(query, ranked, vectors, embedder),
            k=k,
            num_chunks=len(self.chunks),
        ):
            return picked
        max_total = k * 2
        for nid, etype, direction, src in self.expand(
            seen_nodes_list, hops=hops, min_confidence=conf, edge_dirs=ALL_EDGE_DIRS
        ):
            if len(picked) >= max_total or used >= budget_chars:
                break
            for c in self.by_node.get(nid, [])[:1]:
                if _secret(c, nid):
                    break
                if exclude_secrets:
                    c = self._served(c)
                chunk_text = c.get("text") or ""
                rec = c
                if neighbours == "cite":
                    chunk_text = _chunk_signature(chunk_text)
                    rec = {**c, "text": chunk_text, "citation_only": True}
                chunk_len = len(chunk_text)
                if used + chunk_len > budget_chars:
                    break
                src_name = self.nodes.get(src, {}).get("name") or src
                picked.append({**rec, "score": 0.0, "why": f"{etype} {direction} of {src_name}"})
                used += chunk_len
                if len(picked) >= max_total or used >= budget_chars:
                    break
        return picked

    # ---- context packing -------------------------------------------------

    def map_prepend(self) -> str:
        """Render repository overview and top entry points as markdown."""
        parts = []
        if self.overview.strip():
            parts.append(self.overview.strip("\n"))
        entry = self.manifest.get("entrypoints")
        if isinstance(entry, list):
            lines = ["## Top entry points"]
            for e in entry[:MAP_ENTRYPOINTS]:
                if not isinstance(e, dict):
                    continue
                qual = e.get("qualname") or e.get("id") or ""
                where = e.get("path") or ""
                reach = e.get("reach")
                tail = f" (reach {reach})" if isinstance(reach, int) else ""
                lines.append(f"- `{qual}` - {where}{tail}")
            if len(lines) > 1:
                parts.append("\n".join(lines))
        return "\n\n".join(parts)

    def pack_context(
        self,
        query: str,
        k: int = 8,
        hops: int = 1,
        budget_chars: int = 24000,
        min_confidence: float = 1.0,
        expand_graph: bool = True,
        vectors: Mapping[Any, Vector] | None = None,
        embedder: "Embedder | None" = None,
        exclude_secrets: bool = False,
        budget_tokens: int | None = None,
        count_tokens: Callable[[str], int] | None = None,
        extra_secret_keywords: tuple[str, ...] | list[str] | None = None,
        extra_secret_dirs: tuple[str, ...] | list[str] | None = None,
        neighbours: str = "full",
        max_neighbours: int | None = None,
        conditional_expansion: bool = False,
        precision_first: bool = False,
        rerank_answerability: bool = True,
    ) -> dict[str, Any]:
        """Assemble an agent-ready markdown context pack within budget.

        Args:
            query: Question or symbol query string.
            k: Number of seed chunks.
            hops: Graph expansion depth.
            budget_chars: Overall character ceiling for assembled markdown.
            min_confidence: Minimum confidence filter for CALLS edges.
            expand_graph: Whether to expand seeds using graph neighbors.
            vectors: Optional precomputed vectors.
            embedder: Optional Embedder instance.
            exclude_secrets: Whether to omit sensitive files.
            budget_tokens: Optional token ceiling (replaces budget_chars).
            count_tokens: Token counting function (defaults to len // 4).
            extra_secret_keywords: Additional keywords for secret filtering.
            extra_secret_dirs: Additional directory names for secret filtering.
            neighbours: Neighbour rendering mode: 'full' emits complete chunk text;
                'cite' emits one-line signature citations without chunk body.
            max_neighbours: Maximum neighbour chunks to admit into context.
            conditional_expansion: If True, only expands graph neighbours when lexical
                evidence is weak or ambiguous.
            precision_first: If True, prioritizes direct lexical hits in score order and
                only admits neighbours cited by an already-admitted chunk.
            rerank_answerability: Re-rank seed candidates by how answer-shaped
                they are before taking the top `k` -- see `_answerability`.
                Pass False for the pre-2.3 ordering.

        Returns:
            Dictionary containing 'markdown', 'chunks', 'used_chars', 'budget_chars',
            'tokens_used', 'tokens_budget', and 'truncated'.
        """
        measure_tokens = count_tokens if callable(count_tokens) else _DEFAULT_MEASURE
        use_tokens = budget_tokens is not None
        # len is the character measure, and it is additive, so the cumulative
        # accounting below reduces to exactly the arithmetic this method has
        # always done when budget_tokens is None (preserves byte-identical output).
        measure: Callable[[str], int] = measure_tokens if use_tokens else len
        budget = budget_tokens if budget_tokens is not None else budget_chars
        bounded = budget > 0

        ranked = self.score_rrf(query, vectors=vectors, embedder=embedder)
        if conditional_expansion and not is_lexical_weak(
            self._lexical_for_weakness(query, ranked, vectors, embedder),
            k=k,
            num_chunks=len(self.chunks),
        ):
            expand_graph = False
        # One classification drives all three adjustments below: which chunks are
        # answer-shaped, which seeds win slots, and which direction expansion
        # follows.
        shape = classify_query(query)
        # Whether the question is about tests is asked separately from the shape:
        # a caller pattern claims "which tests use X" first, so reading it off
        # `shape` demoted the very chunks such a question asks for.
        asks_tests = asks_about_tests(query)
        # Answerability re-rank, before seeds are taken. `is_lexical_weak` above
        # reads the *lexical* distribution and so must see the unadjusted list.
        if rerank_answerability:
            terms = self.query_content_terms(query)
            boost_tests = asks_tests
            ranked = (
                sorted(
                    (
                        (
                            s * self._answerability(terms, self.chunks[i], boost_tests=boost_tests),
                            i,
                        )
                        for s, i in ranked[:RERANK_CANDIDATES]
                    ),
                    key=lambda si: (-si[0], si[1]),
                )
                + ranked[RERANK_CANDIDATES:]
            )

        seeds: list[Record] = []
        seen_nodes: set[str] = set()
        seed_limit = k if precision_first else k * 3
        seed_window = ranked[:seed_limit]
        if not asks_tests:
            seed_window = self._demote_test_seeds(seed_window)
        for s, i in seed_window:
            c = self.chunks[i]
            nid = c["node_id"]
            if nid in seen_nodes:
                continue
            # An export alias is a rename: a correct thing to *cite* when
            # something points at it, never a place to start reading. Scoring it
            # down was measured and changed nothing, because the cost is not its
            # rank -- `export { module as serveStatic }` is one line, so it fits
            # whatever budget is left after bigger, better-scoring seeds have
            # already been rejected by `fits()`, and a rank it keeps no matter
            # what. So it is skipped as a seed outright.
            #
            # Deliberately *without* `seen_nodes.add(nid)`: graph expansion must
            # still reach it, which is how the alias earns its keep --
            # `utils/url.ts:295-301` enters `ho-struct-hono-03`'s pack as
            # "CALLS out of query", not as a seed.
            if c.get("kind") == "alias":
                continue
            c_path = c.get("path") or self.nodes.get(nid, {}).get("path") or ""
            if exclude_secrets and _is_secret_path(
                c_path, extra_keywords=extra_secret_keywords, extra_dirs=extra_secret_dirs
            ):
                seen_nodes.add(nid)
                continue
            seen_nodes.add(nid)
            if exclude_secrets:
                c = self._served(c)
            seeds.append({**c, "score": round(s, 3), "why": "seed"})
            if len(seeds) >= k:
                break

        graph_neighbours: list[Record] = []
        # Follow the direction the question asked for. `retrieve()` keeps
        # ALL_EDGE_DIRS on purpose, so `repo2graph query` is never narrowed by
        # this; only the packed-context path routes.
        shape_dirs = edge_dirs_for(shape)
        if expand_graph and seeds:
            if precision_first:
                for seed_chunk in seeds:
                    if max_neighbours is not None and len(graph_neighbours) >= max_neighbours:
                        break
                    for nid, etype, direction, src in self.expand(
                        [seed_chunk["node_id"]],
                        hops=hops,
                        min_confidence=min_confidence,
                        edge_dirs=shape_dirs,
                    ):
                        if nid in seen_nodes:
                            continue
                        node_chunks = self.by_node.get(nid, [])
                        if not node_chunks:
                            continue
                        c = node_chunks[0]
                        c_path = c.get("path") or self.nodes.get(nid, {}).get("path") or ""
                        seen_nodes.add(nid)
                        if exclude_secrets and _is_secret_path(
                            c_path,
                            extra_keywords=extra_secret_keywords,
                            extra_dirs=extra_secret_dirs,
                        ):
                            continue
                        if exclude_secrets:
                            c = self._served(c)
                        src_name = self.nodes.get(src, {}).get("name") or src
                        graph_neighbours.append(
                            {**c, "score": 0.0, "why": f"{etype} {direction} of {src_name}"}
                        )
                        if max_neighbours is not None and len(graph_neighbours) >= max_neighbours:
                            break
            else:
                for nid, etype, direction, src in self.expand(
                    [c["node_id"] for c in seeds],
                    hops=hops,
                    min_confidence=min_confidence,
                    edge_dirs=shape_dirs,
                ):
                    if nid in seen_nodes:
                        continue
                    node_chunks = self.by_node.get(nid, [])
                    if not node_chunks:
                        continue
                    c = node_chunks[0]
                    c_path = c.get("path") or self.nodes.get(nid, {}).get("path") or ""
                    seen_nodes.add(nid)
                    if exclude_secrets and _is_secret_path(
                        c_path, extra_keywords=extra_secret_keywords, extra_dirs=extra_secret_dirs
                    ):
                        continue
                    if exclude_secrets:
                        c = self._served(c)
                    src_name = self.nodes.get(src, {}).get("name") or src
                    graph_neighbours.append(
                        {**c, "score": 0.0, "why": f"{etype} {direction} of {src_name}"}
                    )
                    if max_neighbours is not None and len(graph_neighbours) >= max_neighbours:
                        break

        full_map = self.map_prepend()
        shown_map = full_map
        if bounded:
            shown_map = _fit_lines(
                full_map, int(budget * MAP_BUDGET_FRAC) - measure(PACK_SEPARATOR), measure
            )
        head = shown_map.rstrip("\n") + PACK_SEPARATOR if shown_map.strip() else ""
        truncated = shown_map != full_map

        picked: list[tuple[Record, str]] = []
        body = ""  # everything accepted so far, for cumulative measuring

        def fits(block: str) -> bool:
            return measure(head + body + block) <= budget

        for c in seeds:
            text = c.get("text") or ""
            block = _cite_block(c, text)
            if not bounded:
                picked.append((c, text))
            elif fits(block):
                picked.append((c, text))
                body += block
            else:
                truncated = True
        for c in graph_neighbours:
            if not picked:
                # A neighbour without a seed is context without a question:
                # seeds always win the budget (and an empty pack is honest).
                truncated = True
                break
            if neighbours == "cite":
                sig = _chunk_signature(c.get("text") or "")
                c_cite = {**c, "citation_only": True}
                block = _cite_block(c_cite, sig)
                if not bounded:
                    picked.append((c_cite, sig))
                    continue
                if fits(block):
                    picked.append((c_cite, sig))
                    body += block
                else:
                    truncated = True
                continue
            text = c.get("text") or ""
            block = _cite_block(c, text)
            if not bounded:
                picked.append((c, text))
                continue
            if fits(block):
                picked.append((c, text))
                body += block
                continue
            short = _compress(text)
            # The compressed view shows a header and one line, so its cite must
            # not claim the whole chunk: `[cite: JsonReader.kt:1-648]` over a
            # block showing `/*` sent readers to 648 lines nobody quoted.
            excerpt = _excerpt_record(c, text, short)
            block = _cite_block(excerpt, short)
            if fits(block):
                picked.append((excerpt, short))
                body += block
            truncated = True

        picked.sort(
            key=lambda p: (
                p[0].get("path") or "",
                p[0].get("start_line") or 0,
                p[0].get("id") or "",
            )
        )
        markdown = head + "".join(_cite_block(c, text) for c, text in picked)
        chunks = [{**c, "text": text} for c, text in picked]
        return {
            "markdown": markdown,
            "chunks": chunks,
            "seeds": [c for c in chunks if c["why"] == "seed"],
            "neighbors": [c for c in chunks if c["why"] != "seed"],
            "truncated": truncated,
            "budget_chars": budget_chars,
            "used_chars": len(markdown),
            "tokens_used": measure_tokens(markdown),
            "tokens_budget": budget_tokens if use_tokens else 0,
            "query": query,
            # The two budgets, reported apart (#282): what retrieval selected
            # versus what was rendered. Rendered is the larger one -- citation
            # headers and the map prepend add bytes the source never had.
            "source_text_chars": sum(len(text) for _, text in picked),
            "rendered_context_chars": len(markdown),
            "budget_exhausted": truncated,
            "omitted_chunk_count": max(0, len(seeds) + len(graph_neighbours) - len(picked)),
            # `tokens_used` is only as exact as the counter behind it (#290).
            "token_count_method": getattr(measure_tokens, "token_count_method", "caller-supplied"),
        }


def _first(seq: Iterable[_T]) -> _T | None:
    for item in seq:
        return item
    return None


def _has_vector(vectors: Any, i: int) -> bool:
    """True when `vectors` holds a vector for chunk index `i`.

    Used only to count what is missing for a diagnostic message, so it answers
    False for every failure mode rather than distinguishing them.
    """
    try:
        return vectors[i] is not None
    except (KeyError, IndexError, TypeError):
        return False


def _dim_mismatch_reason(qvec: Vector, cvecs: Sequence[Vector]) -> str:
    """Empty when every candidate vector matches `qvec`'s width, else a reason.

    `zip()` truncates to the shorter operand, so a width mismatch (e.g. 768 vs
    1536 from two different embedding models) would otherwise pass straight
    into `_cosine()` and compute a plausible-looking but meaningless score
    instead of raising. Catching it here keeps the promise the rest of
    `_vectors_for` makes: a bad vector turns fusion off and falls back to
    BM25, it never raises out to the caller.
    """
    qdim = len(qvec)
    bad = sum(1 for v in cvecs if len(v) != qdim)
    if not bad:
        return ""
    return (
        f"{bad} of {len(cvecs)} candidate vectors do not match the query "
        f"vector's dimension ({qdim}); vectors.npy likely mixes more than one "
        f"embedding model -- re-run `repo2graph embed` to rebuild it"
    )


def _cosine(a: Vector, b: Vector) -> float:
    """Cosine similarity over any two sequences of floats (no numpy needed).

    Raises `ValueError` on mismatched lengths rather than letting `zip()`
    silently truncate to the shorter vector and compute a meaningless score.
    Callers that accept caller-supplied vectors (`score_rrf` via
    `_vectors_for`) must validate widths themselves and never let this
    exception reach their own caller -- see `_dim_mismatch_reason`.
    """
    if len(a) != len(b):
        raise ValueError(f"cosine similarity: mismatched vector lengths {len(a)} vs {len(b)}")
    num = na = nb = 0.0
    for x, y in zip(a, b, strict=True):
        num += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return num / math.sqrt(na * nb)


def _fit_lines(text: str, limit: int, measure: Callable[[str], int] = len) -> str:
    """Return the longest whole-line prefix of text that fits within limit units.

    Args:
        text: Input string to truncate at line boundary.
        limit: Maximum allowed budget (characters or tokens).
        measure: Measurement function (defaults to len).

    Returns:
        Truncated string with no broken lines.
    """
    if limit <= 0:
        return ""
    lines = text.split("\n")
    if measure is len:
        total = 0
        n_kept = 0
        for line in lines:
            add = len(line) if n_kept == 0 else len(line) + 1
            if total + add > limit:
                break
            total += add
            n_kept += 1
        return "\n".join(lines[:n_kept])
    kept: list[str] = []
    for line in lines:
        candidate = "\n".join(kept + [line]) if kept else line
        if measure(candidate) > limit:
            break
        kept.append(line)
    return "\n".join(kept)


def _compress(text: str) -> str:
    """Compress a chunk to its metadata header lines plus declaration signature."""
    lines = text.split("\n")
    i = 0
    while i < len(lines) and lines[i].startswith("#"):
        i += 1
    kept = lines[:i]
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines):
        kept.append(lines[i])
    return "\n".join(kept)


_HEADER_PREFIXES = (
    "# imports: ",
    "# defines: ",
    "# entry point:",
    "# inherits: ",
    "# called by: ",
    "# calls: ",
    "# calls (outside the repo): ",
    "# doc: ",
)
_KIND_LINE_RE = re.compile(r"^# [\w-]+: .*\(lines \d+-\d+, [^)]*\)$")
_PART_SUFFIX_RE = re.compile(r"#\d+$")


def _header_len(lines: list[str]) -> int:
    """Count leading generated header lines in chunk text."""
    if not lines or not lines[0].startswith("# file: "):
        return 0
    i = 1
    if i < len(lines) and _KIND_LINE_RE.match(lines[i]):
        i += 1
    while i < len(lines) and lines[i].startswith(_HEADER_PREFIXES):
        i += 1
    return i


def _excerpt_record(chunk: Record, full: str, short: str) -> Record:
    """Update chunk citation range to match lines kept by compression."""
    start = chunk.get("start_line")
    end = chunk.get("end_line")
    rec = {**chunk, "excerpt_of": [start, end]}
    if (
        not isinstance(start, int)
        or chunk.get("type") not in ("symbol", "file")
        or _PART_SUFFIX_RE.search(str(chunk.get("id") or ""))
    ):
        return rec
    lines = full.split("\n")
    hdr = _header_len(lines)
    i = 0
    while i < len(lines) and lines[i].startswith("#"):
        i += 1
    shown = list(range(i))
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines):
        shown.append(i)
    body = [x - hdr for x in shown if x >= hdr]
    if body and short.split("\n")[-1] == lines[shown[-1]]:
        rec["start_line"] = start + min(body)
        rec["end_line"] = start + max(body)
        rec["excerpt_exact"] = True
    return rec


def _cite_block(chunk: Record, text: str) -> str:
    """Format markdown citation block header and disarm nested citations."""
    qual = chunk.get("qualname") or chunk.get("name") or ""
    start = chunk.get("start_line") or 1
    end = chunk.get("end_line") or start
    of = chunk.get("excerpt_of")
    mark = ""
    if isinstance(of, (list, tuple)) and len(of) == 2:
        span = f"{of[0] or 1}-{of[1] or of[0] or 1}"
        mark = (
            f" [excerpt of {span}]"
            if chunk.get("excerpt_exact")
            else f" [header and first line only, of {span}]"
        )
    nid = chunk.get("node_id") or chunk.get("id") or ""
    nid_tag = f" [{nid}]" if chunk.get("citation_only") and nid else ""
    head = (
        f"### [cite: {chunk.get('path') or ''}:{start}-{end}] `{qual}`{nid_tag}{mark} "
        f"({chunk.get('why') or ''})"
    )
    disarmed = "\n".join(
        "\\" + ln if ln.lstrip().startswith("### [cite:") else ln for ln in text.split("\n")
    )
    return f"{head}\n{disarmed}\n\n" if disarmed else f"{head}\n\n"


def format_pack(results: Iterable[Record]) -> str:
    """Format retrieval records as delimited text sections."""
    out: list[str] = []
    for r in results:
        path = r.get("path") or ""
        qual = r.get("qualname") or r.get("name") or ""
        why = r.get("why") or ""
        text = r.get("text") or ""
        out.append(f"--- {path}::{qual} [{why}]\n{text}")
    return "\n\n".join(out)
