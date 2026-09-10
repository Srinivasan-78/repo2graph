# @authormark v1 -- do not remove (authorship watermark)
# Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
# Author: https://github.com/Srinivasan-78
# SPDX-License-Identifier: MIT
# Fingerprint: AMK1.9dZTVVmTVd7w95HGye3vt5
"""Graph-aware retrieval over a built index: lexical seeds + k-hop expansion."""
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from .layout import path as artifact_path
from .layout import paths as artifact_paths

TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")
QUALNAME_SEP_RE = re.compile(r"::|\.")
SUBTOKEN_RE = re.compile(r"_|(?<=[a-z0-9])(?=[A-Z])")

# BM25 scoring constants: term frequency saturation (K1), length normalization (B), average doc length (AVG_LEN)
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
ALL_EDGE_DIRS: dict = {}

# pack_context layout constants.
MAP_BUDGET_FRAC = 0.2          # at most this share of the budget goes to the map
MAP_ENTRYPOINTS = 10           # entry points listed in the map prepend
PACK_SEPARATOR = "\n\n---\n\n"  # between the map prepend and the first citation

# Sensitive file detection for pack_context(exclude_secrets=True)
SECRET_EXTS = frozenset({
    ".pem", ".key", ".p12", ".pfx", ".pkcs12", ".p8",
    ".asc", ".gpg", ".der", ".cer", ".crt", ".ovpn",
    ".kdbx", ".keystore", ".jks",
})
SECRET_CONFIG_EXTS = frozenset({
    ".json", ".yaml", ".yml", ".toml", ".xml", ".ini",
    ".env", ".properties", ".conf", ".cfg", ".txt",
})
SECRET_KEYWORDS = (
    "secret", "credential", "token", "service-account", "service_account",
    "password", "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
)
SECRET_EXACT_NAMES = frozenset({
    ".netrc", ".npmrc", ".dockercfg", ".git-credentials", ".pgpass", ".htpasswd",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
})
SECRET_DIR_NAMES = frozenset({
    ".ssh", ".aws", ".kube", "secrets", "credentials",
})


def _is_secret_path(path: str) -> bool:
    """Return True if path points to a sensitive file (secrets, keys, credentials)."""
    if not path:
        return False
    p = str(path).replace("\\", "/").lower()
    parts = p.strip("/").split("/")
    if any(part in SECRET_DIR_NAMES for part in parts[:-1]):
        return True
    name = parts[-1]
    if not name:
        return False
    if name in SECRET_EXACT_NAMES:
        return True
    if name.startswith(".env") or name.endswith(".env") or ".env." in name or "-env" in name:
        return True
    if any(name.endswith(ext) for ext in SECRET_EXTS):
        return True
    if any(kw in name for kw in SECRET_KEYWORDS):
        stem = name.lstrip(".")
        if "." not in stem or any(name.endswith(ext) for ext in SECRET_CONFIG_EXTS):
            return True
    return False


def read_jsonl(path: Path) -> list:
    """Load a JSONL file written by export.write_jsonl.

    newline="\n" matters: json.dumps(ensure_ascii=False) passes U+2028, U+2029
    and U+0085 through verbatim, and both str.splitlines() and universal-newline
    mode treat those as line breaks, which would cut records in half.
    """
    rows = []
    with open(path, encoding="utf8", errors="surrogateescape", newline="\n") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}: line {lineno} is not valid JSON: {e}") from None
    return rows


def tokenize(text: str) -> list[str]:
    out = []
    for t in TOKEN_RE.findall(text):
        low = t.lower()
        out.append(low)
        parts = SUBTOKEN_RE.split(t)
        out += [p.lower() for p in parts if len(p) > 2 and p.lower() != low]
    return out


class Index:
    def __init__(self, outdir: Path):
        self.dir = Path(outdir)
        self.chunks = read_jsonl(artifact_path(self.dir, "chunks.jsonl"))
        self.nodes = {n["id"]: n
                      for n in read_jsonl(artifact_path(self.dir, "nodes.jsonl"))}
        self.edges = read_jsonl(artifact_path(self.dir, "edges.jsonl"))
        self.adj = defaultdict(list)
        for e in self.edges:
            # The edge record itself (not a copy) rides along: traversal needs
            # `confidence` on CALLS, and `count` on CO_CHANGE, without a lookup.
            self.adj[e["src"]].append((e["dst"], e["type"], "out", e))
            self.adj[e["dst"]].append((e["src"], e["type"], "in", e))
        # The repo map, for pack_context()'s prepend. Both are optional: a
        # `--formats jsonl` build writes no overview.md, and a build interrupted
        # before write_manifest leaves no manifest.json. Neither may raise here.
        self._overview: str | None = None
        self._manifest: dict | None = None
        self.by_node = defaultdict(list)
        for c in self.chunks:
            self.by_node[c["node_id"]].append(c)
        # inverted index: term -> [(chunk_index, term_count)], so scoring touches
        # only the chunks that contain a query term instead of every chunk.
        self.df: Counter = Counter()
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.lengths: list[int] = []
        for i, c in enumerate(self.chunks):
            # `or ""`: a hand-edited chunks.jsonl (the manifest says records are
            # inspectable) with a null text/qualname must not TypeError in re.findall.
            counts = Counter(tokenize(c.get("text") or "")
                             + tokenize(c.get("qualname") or "") * 3)
            self.lengths.append(sum(counts.values()) or 1)
            for term, n in counts.items():
                self.postings[term].append((i, n))
            self.df.update(counts.keys())
        self.N = len(self.chunks)

    def _load_text(self, name: str) -> str:
        """Read an optional artifact, trying every section it is written to.

        newline="\n" for the same reason read_jsonl uses it: universal-newline
        mode rewrites U+2028/U+2029/U+0085 line ends and would desync the text
        from what was written.
        """
        for p in artifact_paths(self.dir, name):
            try:
                with open(p, encoding="utf8", newline="\n") as fh:
                    return fh.read()
            except (OSError, UnicodeDecodeError):
                continue
        return ""

    def _load_manifest(self) -> dict:
        try:
            with open(artifact_path(self.dir, "manifest.json"),
                      encoding="utf8", newline="\n") as fh:
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
    def manifest(self) -> dict:
        if self._manifest is None:
            self._manifest = self._load_manifest()
        return self._manifest

    @manifest.setter
    def manifest(self, value: dict) -> None:
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
                acc[i] += qn * idf * (cnt / (cnt + BM25_K1 * ((1.0 - BM25_B) + BM25_B * length / BM25_AVG_LEN)))
        self._boost_identifiers(query, acc)
        scored = [(s, i) for i, s in acc.items() if s]
        scored.sort(reverse=True)
        return scored

    def _boost_identifiers(self, query: str, acc: dict) -> None:
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

    def score_rrf(self, query: str, vectors=None, embedder=None) -> list[tuple[float, int]]:
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
        qvec, cvecs = self._vectors_for(query, candidates, vectors, embedder)
        if qvec is None or not cvecs:
            return base
        by_sim = sorted(range(len(candidates)),
                        key=lambda p: (-_cosine(qvec, cvecs[p]), candidates[p]))
        vec_rank = {candidates[p]: r for r, p in enumerate(by_sim, 1)}
        fused = []
        for rank, (_s, i) in enumerate(base, 1):
            score = 1.0 / (RRF_K + rank)
            if i in vec_rank:
                score += 1.0 / (RRF_K + vec_rank[i])
            fused.append((score, i))
        fused.sort(reverse=True)
        return fused

    def _vectors_for(self, query, candidates, vectors, embedder):
        if vectors is not None:
            try:
                cvecs = [vectors[i] for i in candidates]
                qvec = vectors.get("query") if hasattr(vectors, "get") else None
            except (KeyError, IndexError, TypeError):
                return None, []
            if qvec is None and embedder is not None:
                qvec = _first(embedder.encode([query]))
            return qvec, cvecs
        texts = [self.chunks[i].get("text") or "" for i in candidates]
        encoded = embedder.encode([query] + texts)
        encoded = list(encoded)
        if len(encoded) != len(texts) + 1:
            return None, []
        return encoded[0], encoded[1:]

    def expand(self, seed_nodes, hops=1, edge_types=None, per_hop=6,
               min_confidence=1.0, edge_dirs=None):
        """Walk `hops` edges out from `seed_nodes`, newest frontier first.

        `min_confidence` gates CALLS edges only: call resolution is name-based
        and an overloaded name fans out to several candidates at 1/n confidence,
        while IMPORTS/DEFINES/INHERITS carry no `confidence` key at all and must
        never be dropped by the gate. `edge_dirs` maps an edge type to the
        directions worth following (see DEFAULT_EDGE_DIRS).
        """
        edge_types = edge_types or DEFAULT_EDGE_TYPES
        dirs = DEFAULT_EDGE_DIRS if edge_dirs is None else edge_dirs
        seen, frontier, order = set(seed_nodes), list(seed_nodes), []
        for _ in range(hops):
            nxt = []
            # The cap is per hop, not per frontier node: breaking only the inner
            # loop let each later frontier node add another 60 edges after the
            # budget was already spent.
            cap = per_hop * len(frontier)
            for nid in frontier:
                if len(nxt) >= cap:
                    break
                added_for_nid = 0
                for dst, etype, direction, edge in self.adj.get(nid, []):
                    if etype not in edge_types or dst in seen:
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
                    if added_for_nid >= 60 or len(nxt) >= cap:
                        break
            frontier = nxt
        return order

    def retrieve(self, query: str, k: int = 8, hops: int = 1, budget_chars: int = 24000,
                 *, min_confidence: float | None = None):
        """Lexical seeds plus their graph neighbours, budgeted on chunk text.

        `budget_chars` bounds the sum of the returned chunks' `text` only — it
        says nothing about how a caller renders them. pack_context() uses the
        other model (the whole rendered markdown); do not unify the two.
        `min_confidence=None` means "do not filter CALLS on confidence", which is
        this method's historical behaviour.
        """
        conf = 0.0 if min_confidence is None else min_confidence
        scored = self.score(query)[: k * 3]
        picked, seen_nodes_list, seen_nodes_set, used = [], [], set(), 0
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
        for nid, etype, direction, src in self.expand(seen_nodes_list, hops=hops,
                                                      min_confidence=conf,
                                                      edge_dirs=ALL_EDGE_DIRS):
            if len(picked) >= max_total or used >= budget_chars:
                break
            for c in self.by_node.get(nid, [])[:1]:
                chunk_len = len(c.get("text") or "")
                if used + chunk_len > budget_chars:
                    break
                src_name = self.nodes.get(src, {}).get("name") or src
                picked.append({**c, "score": 0.0,
                               "why": f"{etype} {direction} of {src_name}"})
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

    def pack_context(self, query: str, k: int = 8, hops: int = 1, budget_chars: int = 24000,
                     min_confidence: float = 1.0, expand_graph: bool = True,
                     vectors=None, embedder=None, exclude_secrets: bool = False) -> dict:
        """An agent-ready markdown pack: repo map, `---`, then cited chunks.

        `budget_chars` bounds the WHOLE returned markdown — map prepend, `---`
        separator and every `### [cite: ...]` header included — unlike
        retrieve(), which budgets chunk text only. `budget_chars <= 0` means
        unbounded. Spending order is map (capped at MAP_BUDGET_FRAC of the
        budget), then seeds in score order at full text, then graph neighbours
        at full text or, if that no longer fits, compressed to their header
        lines plus the signature line. Nothing is truncated mid-line.
        """
        bounded = budget_chars > 0
        seeds, seen_nodes = [], set()
        for s, i in self.score_rrf(query, vectors=vectors, embedder=embedder)[: k * 3]:
            c = self.chunks[i]
            nid = c["node_id"]
            if nid in seen_nodes:
                continue
            c_path = c.get("path") or self.nodes.get(nid, {}).get("path") or ""
            if exclude_secrets and _is_secret_path(c_path):
                seen_nodes.add(nid)
                continue
            seen_nodes.add(nid)
            seeds.append({**c, "score": round(s, 3), "why": "seed"})
            if len(seeds) >= k:
                break

        neighbours = []
        if expand_graph and seeds:
            for nid, etype, direction, src in self.expand(
                    [c["node_id"] for c in seeds], hops=hops,
                    min_confidence=min_confidence):
                if nid in seen_nodes:
                    continue
                node_chunks = self.by_node.get(nid, [])
                if not node_chunks:
                    continue
                c = node_chunks[0]
                c_path = c.get("path") or self.nodes.get(nid, {}).get("path") or ""
                seen_nodes.add(nid)
                if exclude_secrets and _is_secret_path(c_path):
                    continue
                src_name = self.nodes.get(src, {}).get("name") or src
                neighbours.append({**c, "score": 0.0,
                                   "why": f"{etype} {direction} of {src_name}"})

        full_map = self.map_prepend()
        shown_map = full_map
        if bounded:
            shown_map = _fit_lines(full_map, int(budget_chars * MAP_BUDGET_FRAC)
                                   - len(PACK_SEPARATOR))
        head = shown_map.rstrip("\n") + PACK_SEPARATOR if shown_map.strip() else ""
        truncated = shown_map != full_map
        remaining = budget_chars - len(head) if bounded else 0

        picked = []
        for c in seeds:
            block = _cite_block(c, c.get("text") or "")
            if not bounded:
                picked.append((c, c.get("text") or ""))
            elif len(block) <= remaining:
                picked.append((c, c.get("text") or ""))
                remaining -= len(block)
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
            if not bounded or len(block) <= remaining:
                picked.append((c, text))
                if bounded:
                    remaining -= len(block)
                continue
            short = _compress(text)
            block = _cite_block(c, short)
            if len(block) <= remaining:
                picked.append((c, short))
                remaining -= len(block)
            truncated = True

        picked.sort(key=lambda p: (p[0].get("path") or "", p[0].get("start_line") or 0,
                                   p[0].get("id") or ""))
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
            "query": query,
        }


def _first(seq):
    for item in seq:
        return item
    return None


def _cosine(a, b) -> float:
    """Cosine similarity over any two sequences of floats (no numpy needed)."""
    num = na = nb = 0.0
    for x, y in zip(a, b, strict=False):
        num += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return num / math.sqrt(na * nb)


def _fit_lines(text: str, limit: int) -> str:
    """The longest whole-line prefix of `text` that fits in `limit` characters."""
    if limit <= 0:
        return ""
    kept, used = [], 0
    for line in text.split("\n"):     # never splitlines(): see AGENTS.md
        cost = len(line) + (1 if kept else 0)
        if used + cost > limit:
            break
        kept.append(line)
        used += cost
    return "\n".join(kept)


def _compress(text: str) -> str:
    """A chunk reduced to its `#` metadata header plus the signature line.

    chunks.build_chunks always emits `# file:` / `# <kind>:` header lines ahead
    of the body, so the first non-blank line after them is the def/class line.
    """
    lines = text.split("\n")          # never splitlines(): see AGENTS.md
    i = 0
    while i < len(lines) and lines[i].startswith("#"):
        i += 1
    kept = lines[:i]
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines):
        kept.append(lines[i])
    return "\n".join(kept)


def _cite_block(chunk: dict, text: str) -> str:
    """One `### [cite: path:start-end] `symbol` (why)` block, trailing blank line."""
    qual = chunk.get("qualname") or chunk.get("name") or ""
    start = chunk.get("start_line") or 1
    end = chunk.get("end_line") or start
    head = (f"### [cite: {chunk.get('path') or ''}:{start}-"
            f"{end}] `{qual}` ({chunk.get('why') or ''})")
    disarmed = "\n".join(
        "\\" + ln if ln.lstrip().startswith("### [cite:") else ln
        for ln in text.split("\n")
    )
    return f"{head}\n{disarmed}\n\n"


def format_pack(results) -> str:
    out = []
    for r in results:
        path = r.get("path") or ""
        qual = r.get("qualname") or r.get("name") or ""
        why = r.get("why") or ""
        text = r.get("text") or ""
        out.append(f"--- {path}::{qual} [{why}]\n{text}")
    return "\n\n".join(out)
