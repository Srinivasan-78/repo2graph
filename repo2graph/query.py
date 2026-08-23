"""Graph-aware retrieval over a built index: lexical seeds + k-hop expansion."""
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")


def read_jsonl(path: Path) -> list:
    """Load a JSONL file written by export.write_jsonl.

    newline="\n" matters: json.dumps(ensure_ascii=False) passes U+2028, U+2029
    and U+0085 through verbatim, and both str.splitlines() and universal-newline
    mode treat those as line breaks, which would cut records in half.
    """
    with open(path, encoding="utf8", newline="\n") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def tokenize(text: str) -> list[str]:
    out = []
    for t in TOKEN_RE.findall(text):
        low = t.lower()
        out.append(low)
        parts = re.split(r"_|(?<=[a-z0-9])(?=[A-Z])", t)
        out += [p.lower() for p in parts if len(p) > 2 and p.lower() != low]
    return out


class Index:
    def __init__(self, outdir: Path):
        self.dir = Path(outdir)
        self.chunks = read_jsonl(self.dir / "chunks.jsonl")
        self.nodes = {n["id"]: n for n in read_jsonl(self.dir / "nodes.jsonl")}
        self.edges = read_jsonl(self.dir / "edges.jsonl")
        self.adj = defaultdict(list)
        for e in self.edges:
            self.adj[e["src"]].append((e["dst"], e["type"], "out"))
            self.adj[e["dst"]].append((e["src"], e["type"], "in"))
        self.by_node = defaultdict(list)
        for c in self.chunks:
            self.by_node[c["node_id"]].append(c)
        # inverted index: term -> [(chunk_index, term_count)], so scoring touches
        # only the chunks that contain a query term instead of every chunk.
        self.df: Counter = Counter()
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.lengths: list[int] = []
        for i, c in enumerate(self.chunks):
            counts = Counter(tokenize(c["text"]) + tokenize(c["qualname"]) * 3)
            self.lengths.append(sum(counts.values()) or 1)
            for term, n in counts.items():
                self.postings[term].append((i, n))
            self.df.update(counts.keys())
        self.N = len(self.chunks)

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
                acc[i] += qn * idf * (cnt / (cnt + 1.5 * (0.25 + 0.75 * length / 400)))
        scored = [(s, i) for i, s in acc.items() if s]
        scored.sort(reverse=True)
        return scored

    def expand(self, seed_nodes, hops=1, edge_types=None, per_hop=6):
        edge_types = edge_types or {"CALLS", "IMPORTS", "DEFINES", "INHERITS"}
        seen, frontier, order = set(seed_nodes), list(seed_nodes), []
        for _ in range(hops):
            nxt = []
            for nid in frontier:
                for dst, etype, direction in self.adj.get(nid, [])[:60]:
                    if etype in edge_types and dst not in seen:
                        seen.add(dst)
                        nxt.append(dst)
                        order.append((dst, etype, direction, nid))
                        if len(nxt) >= per_hop * len(frontier):
                            break
            frontier = nxt
        return order

    def retrieve(self, query: str, k: int = 8, hops: int = 1, budget_chars: int = 24000):
        scored = self.score(query)[: k * 3]
        picked, seen_nodes, used = [], [], 0
        for s, i in scored:
            c = self.chunks[i]
            if c["node_id"] in seen_nodes:
                continue
            seen_nodes.append(c["node_id"])
            picked.append({"score": round(s, 3), "why": "lexical", **c})
            used += len(c["text"])
            if len(picked) >= k or used > budget_chars:
                break
        for nid, etype, direction, src in self.expand(seen_nodes, hops=hops):
            if used > budget_chars:
                break
            for c in self.by_node.get(nid, [])[:1]:
                picked.append({"score": 0.0,
                               "why": f"{etype} {direction} of {self.nodes.get(src, {}).get('name', src)}",
                               **c})
                used += len(c["text"])
        return picked


def format_pack(results) -> str:
    out = []
    for r in results:
        out.append(f"--- {r['path']}::{r['qualname']} [{r['why']}]\n{r['text']}")
    return "\n\n".join(out)
