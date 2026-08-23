"""Where each artifact lands inside the output directory.

The output is split in two: `human/` holds what a person opens — the prose map,
the drawn graph — and `agent/` holds what a program reads: the JSONL graph, the
retrieval chunks, the Cypher load script, the manifest that describes them.
`overview.md` is written to both, because the repo-level map is the cheapest
orientation either audience can get.
"""
from pathlib import Path

HUMAN_DIR = "human"
AGENT_DIR = "agent"

SECTIONS: dict[str, tuple[str, ...]] = {
    "overview.md": (HUMAN_DIR, AGENT_DIR),
    "graph.html": (HUMAN_DIR,),
    "graph.graphml": (HUMAN_DIR,),
    "nodes.jsonl": (AGENT_DIR,),
    "edges.jsonl": (AGENT_DIR,),
    "chunks.jsonl": (AGENT_DIR,),
    "graph.cypher": (AGENT_DIR,),
    "stats.json": (AGENT_DIR,),
    "index.json": (AGENT_DIR,),
    "manifest.json": (AGENT_DIR,),
}


def rels(name: str) -> list[str]:
    """Every path an artifact is written to, relative to the output directory."""
    return [f"{section}/{name}" for section in SECTIONS[name]]


def rel(name: str) -> str:
    """'nodes.jsonl' -> 'agent/nodes.jsonl'. The path readers should use."""
    return rels(name)[0]


def path(outdir, name) -> Path:
    """The path an artifact is read back from."""
    return Path(outdir) / rel(name)


def paths(outdir, name) -> list[Path]:
    return [Path(outdir) / r for r in rels(name)]


def make_path(outdir, name) -> Path:
    """Like path(), but creates the section directory first."""
    p = path(outdir, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def make_paths(outdir, name) -> list[Path]:
    out = paths(outdir, name)
    for p in out:
        p.parent.mkdir(parents=True, exist_ok=True)
    return out
