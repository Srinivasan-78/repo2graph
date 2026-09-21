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

from .secrets import (
    SECRET_CONFIG_EXTS,
    SECRET_DIR_NAMES,
    SECRET_EXACT_NAMES,
    SECRET_EXTS,
    SECRET_KEYWORDS,
    SECRET_WORD_RE,
    _is_secret_path,
)

__all__ = [
    "Index",
    "SECRET_CONFIG_EXTS",
    "SECRET_DIR_NAMES",
    "SECRET_EXACT_NAMES",
    "SECRET_EXTS",
    "SECRET_KEYWORDS",
    "SECRET_WORD_RE",
    "_is_secret_path",
    "format_pack",
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
QUALNAME_SEP_RE = re.compile(r"::|\.")
SUBTOKEN_RE = re.compile(r"_|(?<=[a-z0-9])(?=[A-Z])")

# BM25 scoring constants: term frequency saturation (K1), length normalization (B).
# BM25_AVG_LEN is the empty-index fallback / documented default; a non-empty
# Index uses corpus avgdl = sum(lengths) / N for the length term in score().
BM25_K1 = 1.5
BM25_B = 0.75
BM25_AVG_LEN = 400.0

# An exact identifier match on a chunk's own name/qualname multiplies its BM25
# score. It never introduces a chunk BM25 did not already score, so `score()`'s
# "every hit contains a query term" invariant survives.
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

# Token accounting. 4 characters per token is the usual English/code rule of
# thumb; it under-counts dense code and CJK, which is why pack_context takes a
# `count_tokens=` hook a caller can point at a real tokenizer.
CHARS_PER_TOKEN = 4

# pack_context layout constants.
MAP_BUDGET_FRAC = 0.2  # at most this share of the budget goes to the map
MAP_ENTRYPOINTS = 10  # entry points listed in the map prepend
PACK_SEPARATOR = "\n\n---\n\n"  # between the map prepend and the first citation


def read_jsonl(path: Path) -> list[Record]:
    """Load a JSONL file written by export.write_jsonl.

    newline="\n" matters: json.dumps(ensure_ascii=False) passes U+2028, U+2029
    and U+0085 through verbatim, and both str.splitlines() and universal-newline
    mode treat those as line breaks, which would cut records in half.
    """
    rows: list[Record] = []
    with open(path, encoding="utf8", errors="surrogateescape", newline="\n") as fh:
        for lineno, line in enumerate(fh, 1):
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


class Index:
    # (fused, candidates) for the most recent score_rrf call, or None if no
    # fused query has run on this Index yet. A class-level default so every
    # Index has the attribute without __init__ having to care.
    fusion_coverage: tuple[int, int] | None = None

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
            counts = Counter(tokenize(c.get("text") or "") + tokenize(c.get("qualname") or "") * 3)
            self.lengths.append(sum(counts.values()) or 1)
            for term, n in counts.items():
                self.postings[term].append((i, n))
            self.df.update(counts.keys())
        self.N = len(self.chunks)
        self.avgdl = (sum(self.lengths) / self.N) if self.N else BM25_AVG_LEN
        # Dense vectors are optional in every sense: absent, unreadable,
        # truncated or stale, the index still answers lexically.
        self.vectors: dict[int, Vector] | None = None
        self.vector_meta: dict[str, Any] | None = None
        self._load_vectors()

    def _load_vectors(self) -> None:
        """Load agent/vectors.npy into chunk-list-index keys, or give up quietly.

        The file is keyed by chunk id; ids the current chunks.jsonl no longer
        holds are dropped rather than shifted onto a neighbouring row, because
        a mis-aligned vector produces plausible-looking garbage rankings that
        nothing downstream can detect.
        """
        npy = artifact_path(self.dir, "vectors.npy")
        if not npy.exists():
            return
        try:
            from .embed import load_vectors

            by_id, meta = load_vectors(npy)
        except Exception:
            # OSError, ValueError, a malformed meta -- all the same answer.
            return
        pos = {c.get("id"): i for i, c in enumerate(self.chunks)}
        vectors: dict[int, Vector] = {pos[cid]: vec for cid, vec in by_id.items() if cid in pos}
        if not vectors:
            return
        self.vectors, self.vector_meta = vectors, meta

    def fuse_ok(self, embedder: "Embedder") -> tuple[bool, str]:
        """May `embedder`'s query vectors be fused with this index's vectors?

        A silent model or width mismatch is worse than no vectors at all: the
        rankings stay plausible while being meaningless, so the answer is a
        refusal naming both sides rather than a best effort.
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

    def score(self, query: str) -> list[tuple[float, int]]:
        q = Counter(tokenize(query))
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
        """Multiply the score of chunks the query names outright.

        A pinpoint query like `normalize_provider` should return the symbol, not
        the prose that happens to repeat the word. Only whole identifiers from
        the query count (not tokenize()'s sub-words), and only chunks BM25
        already scored are touched, so score()'s invariants are unchanged.
        """
        idents = set(TOKEN_RE.findall(query))
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
    ) -> list[tuple[float, int]]:
        """BM25 fused with an optional dense ranking by reciprocal rank fusion.

        With neither `vectors` nor `embedder` this is exactly `score()` — the
        zero-dependency default path. `embedder` is any object with
        `.encode(list[str]) -> list[sequence[float]]` (sentence-transformers
        satisfies it); `vectors` is a mapping of chunk index -> vector, which may
        carry the query vector under the key "query". Similarity is computed in
        plain Python, so no optional dependency is imported here either.
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
        fused: list[tuple[float, int]] = []
        for rank, (_s, i) in enumerate(base, 1):
            score = 1.0 / (RRF_K + rank)
            if i in vec_rank:
                score += 1.0 / (RRF_K + vec_rank[i])
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
        """Walk `hops` edges out from `seed_nodes`, newest frontier first.

        `min_confidence` gates CALLS edges only: call resolution is name-based
        and an overloaded name fans out to several candidates at 1/n confidence,
        while IMPORTS/DEFINES/INHERITS carry no `confidence` key at all and must
        never be dropped by the gate. `edge_dirs` maps an edge type to the
        directions worth following (see DEFAULT_EDGE_DIRS).
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
            # The cap is per hop, not per frontier node: breaking only the inner
            # loop let each later frontier node add another 60 edges after the
            # budget was already spent.
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
        budget_chars: int = 24000,
        *,
        min_confidence: float | None = None,
        vectors: Mapping[Any, Vector] | None = None,
        embedder: "Embedder | None" = None,
    ) -> list[Record]:
        """Lexical seeds plus their graph neighbours, budgeted on chunk text.

        `budget_chars` bounds the sum of the returned chunks' `text` only — it
        says nothing about how a caller renders them. pack_context() uses the
        other model (the whole rendered markdown); do not unify the two.
        `min_confidence=None` means "do not filter CALLS on confidence", which is
        this method's historical behaviour. With `vectors` and `embedder` both
        None -- the default -- seeds come from `score()` exactly as they always
        have; supply either and they come from the fused ranking instead.
        """
        conf = 0.0 if min_confidence is None else min_confidence
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
            if nid in seen_nodes_set:
                continue
            chunk_len = len(c.get("text") or "")
            # ISS-37: test budget before appending so we do not overshoot by a whole chunk
            if picked and used + chunk_len > budget_chars:
                break
            seen_nodes_set.add(nid)
            seen_nodes_list.append(nid)
            picked.append({**c, "score": round(s, 3), "why": "lexical"})
            used += chunk_len
            if len(picked) >= k or used >= budget_chars:
                break
        # ISS-37: Bound the expansion pass by both count and budget
        max_total = k * 2
        # edge_dirs=ALL_EDGE_DIRS, not the default: this method predates
        # DEFAULT_EDGE_DIRS and must keep returning DEFINES-out / IMPORTS-in /
        # INHERITS-in neighbours (D1 — `repo2graph query` output is unchanged).
        for nid, etype, direction, src in self.expand(
            seen_nodes_list, hops=hops, min_confidence=conf, edge_dirs=ALL_EDGE_DIRS
        ):
            if len(picked) >= max_total or used >= budget_chars:
                break
            for c in self.by_node.get(nid, [])[:1]:
                chunk_len = len(c.get("text") or "")
                if used + chunk_len > budget_chars:
                    break
                src_name = self.nodes.get(src, {}).get("name") or src
                picked.append({**c, "score": 0.0, "why": f"{etype} {direction} of {src_name}"})
                used += chunk_len
                if len(picked) >= max_total or used >= budget_chars:
                    break
        return picked

    # ---- context packing -------------------------------------------------

    def map_prepend(self) -> str:
        """The repo map: the prose overview plus the top entry points.

        Entry points come straight off `manifest["entrypoints"]`, which
        export.write_manifest already sorted by reach. Both sources are
        optional; with neither, this is the empty string.
        """
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
    ) -> dict[str, Any]:
        """An agent-ready markdown pack: repo map, `---`, then cited chunks.

        `budget_chars` bounds the WHOLE returned markdown — map prepend, `---`
        separator and every `### [cite: ...]` header included — unlike
        retrieve(), which budgets chunk text only. `budget_chars <= 0` means
        unbounded. Spending order is map (capped at MAP_BUDGET_FRAC of the
        budget), then seeds in score order at full text, then graph neighbours
        at full text or, if that no longer fits, compressed to their header
        lines plus the signature line. Nothing is truncated mid-line.

        `budget_tokens` *replaces* `budget_chars` as the accounting unit when
        it is not None: every fit test then goes through the measure function
        (`count_tokens=`, defaulting to the module-level estimate) instead of
        len(). Accounting is cumulative — the measure is applied to the text
        assembled so far plus the candidate block, never to blocks in
        isolation — so a non-additive measure cannot be talked past.
        `tokens_used` is always reported; `tokens_budget` is 0 when unset.
        """
        measure_tokens = count_tokens if callable(count_tokens) else _DEFAULT_MEASURE
        use_tokens = budget_tokens is not None
        # len is the character measure, and it is additive, so the cumulative
        # accounting below reduces to exactly the arithmetic this method has
        # always done when budget_tokens is None (D1: byte-identical output).
        measure: Callable[[str], int] = measure_tokens if use_tokens else len
        budget = budget_tokens if budget_tokens is not None else budget_chars
        bounded = budget > 0
        seeds: list[Record] = []
        seen_nodes: set[str] = set()
        for s, i in self.score_rrf(query, vectors=vectors, embedder=embedder)[: k * 3]:
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
            seeds.append({**c, "score": round(s, 3), "why": "seed"})
            if len(seeds) >= k:
                break

        neighbours: list[Record] = []
        if expand_graph and seeds:
            for nid, etype, direction, src in self.expand(
                [c["node_id"] for c in seeds], hops=hops, min_confidence=min_confidence
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
                src_name = self.nodes.get(src, {}).get("name") or src
                neighbours.append({**c, "score": 0.0, "why": f"{etype} {direction} of {src_name}"})

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
        for c in neighbours:
            if not picked:
                # A neighbour without a seed is context without a question:
                # seeds always win the budget (and an empty pack is honest).
                truncated = True
                break
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
            block = _cite_block(c, short)
            if fits(block):
                picked.append((c, short))
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
    """The longest whole-line prefix of `text` that fits in `limit` units.

    The candidate is measured whole rather than line by line, so a measure that
    is not additive (any token estimate) is applied to the string that will
    actually be emitted. With the default `len` this is exactly the running
    "len(line) + a newline" arithmetic it replaces.
    """
    if limit <= 0:
        return ""
    kept: list[str] = []
    for line in text.split("\n"):  # never splitlines(): see AGENTS.md
        candidate = "\n".join([*kept, line])
        if measure(candidate) > limit:
            break
        kept.append(line)
    return "\n".join(kept)


def _compress(text: str) -> str:
    """A chunk reduced to its `#` metadata header plus the signature line.

    chunks.build_chunks always emits `# file:` / `# <kind>:` header lines ahead
    of the body, so the first non-blank line after them is the def/class line.
    """
    lines = text.split("\n")  # never splitlines(): see AGENTS.md
    i = 0
    while i < len(lines) and lines[i].startswith("#"):
        i += 1
    kept = lines[:i]
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines):
        kept.append(lines[i])
    return "\n".join(kept)


def _cite_block(chunk: Record, text: str) -> str:
    """One `### [cite: path:start-end] `symbol` (why)` block, trailing blank line."""
    qual = chunk.get("qualname") or chunk.get("name") or ""
    start = chunk.get("start_line") or 1
    end = chunk.get("end_line") or start
    head = (
        f"### [cite: {chunk.get('path') or ''}:{start}-{end}] `{qual}` ({chunk.get('why') or ''})"
    )
    disarmed = "\n".join(
        "\\" + ln if ln.lstrip().startswith("### [cite:") else ln for ln in text.split("\n")
    )
    return f"{head}\n{disarmed}\n\n"


def format_pack(results: Iterable[Record]) -> str:
    out: list[str] = []
    for r in results:
        path = r.get("path") or ""
        qual = r.get("qualname") or r.get("name") or ""
        why = r.get("why") or ""
        text = r.get("text") or ""
        out.append(f"--- {path}::{qual} [{why}]\n{text}")
    return "\n\n".join(out)
