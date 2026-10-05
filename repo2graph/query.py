"""Graph-aware retrieval over a built index: lexical seeds + k-hop expansion."""

import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from .export import path as artifact_path
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
    "classify_query",
    "edge_dirs_for",
    "format_pack",
    "is_lexical_weak",
    "read_jsonl",
    "tokenize",
]

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
    for pat in _TRACE_PATTERNS:
        if re.search(pat, q):
            return "trace"
    return "concept"


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
        ValueError: If any line exceeds MAX_JSONL_LINE_BYTES or contains invalid JSON.
    """
    rows: list[Record] = []
    with open(path, "rb") as fh:
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


class Index:
    # (fused, candidates) for the most recent score_rrf call, or None if no
    # fused query has run on this Index yet. A class-level default so every
    # Index has the attribute without __init__ having to care.
    fusion_coverage: tuple[int, int] | None = None
    # The source tree this index describes, when the caller knows it (the MCP
    # server sets it from its --repo). None means "not known here".
    repo_root: Path | None = None

    def __init__(self, outdir: Path):
        self.dir = Path(outdir)
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
        self._overview: str | None = None
        self._manifest: dict[str, Any] | None = None
        self.by_node: dict[str, list[Record]] = defaultdict(list)
        for c in self.chunks:
            self.by_node[c["node_id"]].append(c)
        # inverted index: term -> [(chunk_index, term_count)], so scoring touches
        # only the chunks that contain a query term instead of every chunk.
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
        self.vectors: dict[int, Vector] | None = None
        self.vector_meta: dict[str, Any] | None = None
        self._load_vectors()

    def _load_vectors(self) -> None:
        """Load dense chunk vectors and metadata from vectors.npy if present."""
        npy = artifact_path(self.dir, "vectors.npy")
        if not npy.exists():
            return
        try:
            from .embed import load_vectors

            by_id, meta = load_vectors(npy)
        except Exception:
            return
        pos = {c.get("id"): i for i, c in enumerate(self.chunks)}
        vectors: dict[int, Vector] = {pos[cid]: vec for cid, vec in by_id.items() if cid in pos}
        if not vectors:
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
            return False, (
                f"no vectors in the index at {self.dir}: run `repo2graph embed -o {self.dir}` first"
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
        except Exception as exc:
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
            try:
                with open(p, encoding="utf8", newline="\n") as fh:
                    return fh.read()
            except (OSError, UnicodeDecodeError):
                continue
        return ""

    def _load_manifest(self) -> dict[str, Any]:
        try:
            with open(
                artifact_path(self.dir, "manifest.json"), encoding="utf8", newline="\n"
            ) as fh:
                data = json.load(fh)
        except (OSError, UnicodeDecodeError, ValueError):
            # ValueError covers json.JSONDecodeError: a truncated manifest is a
            # degraded map, not a reason to refuse to answer a query.
            return {}
        return data if isinstance(data, dict) else {}

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
        q = Counter(tokenize(query) + [t.lower() for t in IDENT_RE.findall(query) if len(t) == 1])
        acc: dict[int, float] = defaultdict(float)
        for term, qn in q.items():
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
            chunk_len = len(c.get("text") or "")
            # Verify budget headroom before appending to avoid overshooting:
            if picked and used + chunk_len > budget_chars:
                break
            seen_nodes_set.add(nid)
            seen_nodes_list.append(nid)
            if exclude_secrets:
                c = self._served(c)
                chunk_len = len(c.get("text") or "")
            picked.append({**c, "score": round(s, 3), "why": "lexical"})
            used += chunk_len
            if len(picked) >= k or used >= budget_chars:
                break
        # Bound the expansion pass by both count and budget:
        if conditional_expansion and not is_lexical_weak(ranked, k=k, num_chunks=len(self.chunks)):
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

        Returns:
            Dictionary containing 'markdown', 'chunks', 'used_chars', 'budget_chars',
            'tokens_used', 'tokens_budget', and 'truncated'.
        """
        measure_tokens = count_tokens if callable(count_tokens) else _DEFAULT_MEASURE
        use_tokens = budget_tokens is not None
        # len is the character measure, and it is additive, so the cumulative
        # accounting below reduces to exactly the arithmetic this method has
        # always done when budget_tokens is None (D1: byte-identical output).
        measure: Callable[[str], int] = measure_tokens if use_tokens else len
        budget = budget_tokens if budget_tokens is not None else budget_chars
        bounded = budget > 0

        ranked = self.score_rrf(query, vectors=vectors, embedder=embedder)
        if conditional_expansion and not is_lexical_weak(ranked, k=k, num_chunks=len(self.chunks)):
            expand_graph = False

        seeds: list[Record] = []
        seen_nodes: set[str] = set()
        seed_limit = k if precision_first else k * 3
        for s, i in ranked[:seed_limit]:
            c = self.chunks[i]
            nid = c["node_id"]
            if nid in seen_nodes:
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
        shape = classify_query(query)
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
