"""Shared fixtures for the vectors / tokens / MCP / compatibility suites.

Nothing here imports numpy, sentence-transformers or the `mcp` SDK: the whole
point of the run these tests guard is that `pip install repo2graph` with no
extras keeps working, so the test suite itself must never need an extra.

`StubEmbedder` is the stand-in for every embedder in these tests. It is
deterministic (vectors are derived from a sha256 of the text, so they are
identical across runs, machines and Python versions), dependency-free, carries
a settable `model_id` and `dim`, and records every `encode()` argument list in
`.calls` so a test can assert *what* was embedded, not just how much.

Do not touch tests/test_rag.py's own fixtures from here: that file has a
vocabulary contract of its own that several of its tests depend on.
"""

import hashlib
import json
from pathlib import Path

import pytest

from repo2graph.cli import main
from repo2graph.graph import PARALLEL_MIN_FILES

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"

# --------------------------------------------------------------------------
# The `mini_repo` fixture: a small synthetic package.
#
# Vocabulary contract, relied on by several tests:
#   * MINI_QUERY hits pkg/gateway.py::route_request lexically; pkg/audit.py::
#     audit_event is reachable from it only over the CALLS edge.
#   * SECRET_QUERY is engineered so BM25 ranks the `.env` chunk FIRST while
#     still matching the two code chunks -- that is what makes AC-29 a real
#     test rather than a tautology.
# --------------------------------------------------------------------------

MINI_GATEWAY = '''
from .audit import audit_event


def route_request(request):
    """Dispatch one inbound request to its handler and record the outcome.

    The router keeps no state of its own, which makes it safe to call from any
    worker thread, and it deliberately never touches a socket or a clock.
    """
    handler = request.get("handler")
    audit_event(handler)
    return handler
'''

MINI_AUDIT = '''
def audit_event(name):
    """Append one entry to the tamper evident journal of handled events.

    The journal is append only so that a later reader can replay it verbatim.
    """
    return {"event": name}
'''

MINI_NOTES = ("# Notes\n\nThe gateway dispatches an inbound request to a handler.\n") * 3

# A fake credential. Nothing here is a real secret; the words are chosen so
# that SECRET_QUERY ranks this chunk above every code chunk.
MINI_ENV = (
    "# deployment ledger credential store\n"
    "ACME_DEPLOYMENT_LEDGER_TOKEN=abc123deadbeef\n"
    "ACME_DEPLOYMENT_LEDGER_SECRET=zzz999notreal\n"
    "DEPLOYMENT_LEDGER_CREDENTIAL=deployment ledger credential inbound request handler\n"
)

MINI_QUERY = "inbound request handler dispatch"
SECRET_QUERY = "deployment ledger credential inbound request handler"

SYM_ROUTE = "sym:pkg/gateway.py::route_request"
SYM_AUDIT = "sym:pkg/audit.py::audit_event"
FILE_GATEWAY = "file:pkg/gateway.py"
FILE_ENV = "file:.env"

MINI_FILES = {
    "pkg/__init__.py": "",
    "pkg/gateway.py": MINI_GATEWAY,
    "pkg/audit.py": MINI_AUDIT,
    "docs/notes.md": MINI_NOTES,
    ".env": MINI_ENV,
}


def write_mini_repo(root: Path) -> Path:
    """Materialise the mini repo under `root`; returns the repo directory."""
    repo = Path(root) / "mini_src"
    for rel, text in MINI_FILES.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf8", newline="\n") as fh:
            fh.write(text)
    return repo


def build_mini_index(repo: Path, out: Path) -> Path:
    """`repo2graph build` with the format set every fixture here relies on."""
    main(["build", str(repo), "-o", str(out), "--formats", "jsonl,overview"])
    return Path(out)


@pytest.fixture
def mini_repo(tmp_path):
    return write_mini_repo(tmp_path)


@pytest.fixture
def mini_index(mini_repo, tmp_path):
    return build_mini_index(mini_repo, tmp_path / "mini_idx")


# --------------------------------------------------------------------------
# A deliberately oversized repo, so that an unbounded pack really does blow
# past the MCP ceiling. Without it, AC-27/28 would pass on a pack that was
# never near the limit and would not notice a ceiling that is not enforced.
# --------------------------------------------------------------------------

BIG_MODULES = 20
BIG_DOC_LINES = 60


def _big_module(n: int) -> str:
    body = "\n".join(
        f"    Paragraph {i} of module {n}: the inbound request handler dispatch "
        f"table routes every payload through the ledger before the worker "
        f"acknowledges it, which keeps the audit trail contiguous."
        for i in range(BIG_DOC_LINES)
    )
    return f'''
def dispatch_{n}(request):
    """Inbound request handler dispatch, variant {n}.

{body}
    """
    return request
'''


def write_big_repo(root: Path) -> Path:
    repo = Path(root) / "big_src"
    pkg = repo / "bigpkg"
    pkg.mkdir(parents=True, exist_ok=True)
    with open(pkg / "__init__.py", "w", encoding="utf8", newline="\n") as fh:
        fh.write("")
    for n in range(BIG_MODULES):
        with open(pkg / f"mod{n}.py", "w", encoding="utf8", newline="\n") as fh:
            fh.write(_big_module(n))
    return repo


@pytest.fixture(scope="session")
def big_index(tmp_path_factory):
    """Read-only: session scoped so the twelve-module build runs once."""
    root = tmp_path_factory.mktemp("big")
    repo = write_big_repo(root)
    out = root / "big_idx"
    build_mini_index(repo, out)
    return out


# --------------------------------------------------------------------------
# A repo *wide* rather than large: more files than PARALLEL_MIN_FILES, so a
# build over it takes `graph.parse_all`'s ProcessPoolExecutor branch (#67).
# Every other fixture here is under the threshold -- `mini_repo` at 5 files,
# `big_repo` at 21 -- which is why the pool branch, and the MCP auto-build
# hang it caused (#90), went unexercised by a green suite.
#
# Sized off the constant rather than hardcoded, so the fixture stays above the
# threshold if the threshold moves. Kept deliberately cheap: ~350 bytes a
# module, no long docstrings, because this generates on a 9-cell CI matrix.
# Each module still carries a module-level table so its *file* node has a
# chunk -- a file that is nothing but `def`s falls under the file_residual
# floor and emits none (see AGENTS.md).
# --------------------------------------------------------------------------

WIDE_MODULES = PARALLEL_MIN_FILES + 6


def _wide_module(n: int) -> str:
    return f'''"""Module {n} of the wide fixture."""

ROUTE_TABLE_{n} = {{
    "name": "module {n}",
    "kind": "inbound request handler dispatch table",
    "note": "every payload goes through the ledger before it is acknowledged",
}}


def dispatch_{n}(request):
    """Inbound request handler dispatch, variant {n}."""
    return handle_{n}(request)


def handle_{n}(request):
    """Record one handled request in module {n}'s ledger."""
    return {{"module": {n}, "request": request}}
'''


def write_wide_repo(root: Path) -> Path:
    """Materialise a repo of WIDE_MODULES + 1 files; returns the repo directory."""
    repo = Path(root) / "wide_src"
    pkg = repo / "widepkg"
    pkg.mkdir(parents=True, exist_ok=True)
    with open(pkg / "__init__.py", "w", encoding="utf8", newline="\n") as fh:
        fh.write('"""The wide fixture package."""\n\nVERSION = "1.0"\n')
    for n in range(WIDE_MODULES):
        with open(pkg / f"mod{n}.py", "w", encoding="utf8", newline="\n") as fh:
            fh.write(_wide_module(n))
    return repo


@pytest.fixture(scope="session")
def wide_repo(tmp_path_factory):
    """Read-only source tree above PARALLEL_MIN_FILES; no index is built here.

    Session scoped and never indexed in place: a test that wants an index
    points `-o` at its own `tmp_path`, so nothing written by one test can be
    discovered as a source file by the next.
    """
    return write_wide_repo(tmp_path_factory.mktemp("wide"))


# --------------------------------------------------------------------------
# Embedders
# --------------------------------------------------------------------------


class StubEmbedder:
    """Deterministic, dependency-free stand-in for a sentence-transformer.

    `model_id` is what `repo2graph.embed.model_id_of()` must report for this
    object (the attribute is the contract the mismatch guard is tested
    through); `dim` is the width of every returned vector; `calls` records one
    entry per `encode()` call, holding the exact list of texts it was given.
    """

    def __init__(self, model_id="stub/mini-v1", dim=8):
        self.model_id = model_id
        self.dim = dim
        self.calls = []

    def encode(self, texts):
        batch = list(texts)
        self.calls.append(list(batch))
        return [self.vector_for(t) for t in batch]

    @property
    def embedded_texts(self):
        """Every text this embedder was ever asked for, flattened."""
        return [t for batch in self.calls for t in batch]

    def vector_for(self, text):
        digest = hashlib.sha256((text or "").encode("utf8", "surrogateescape")).digest()
        return [((digest[i % len(digest)] / 255.0) * 2.0) - 1.0 for i in range(self.dim)]


class ScriptedEmbedder:
    """Returns a pre-computed list of vectors, ignoring the texts it is given.

    `score_rrf(embedder=...)` calls `encode([query] + candidate_texts)` with the
    candidates in BM25 order, so a test that has already computed that order can
    hand back exactly the similarities it wants and reason about the fused
    ranking without asserting on any score value.
    """

    def __init__(self, vectors, model_id="scripted/rigged-v1"):
        self.vectors = [list(v) for v in vectors]
        self.model_id = model_id
        self.dim = len(self.vectors[0]) if self.vectors else 0
        self.calls = []

    def encode(self, texts):
        batch = list(texts)
        self.calls.append(list(batch))
        assert len(batch) == len(self.vectors), (
            f"ScriptedEmbedder was scripted for {len(self.vectors)} texts "
            f"but asked for {len(batch)}"
        )
        return [list(v) for v in self.vectors]


@pytest.fixture
def stub_embedder():
    return StubEmbedder()


@pytest.fixture
def use_stub_embedder(monkeypatch):
    """Make `embed.default_embedder()` hand back a StubEmbedder.

    Returns the factory's record: `.made` is every embedder handed out, `.names`
    every `name` argument it was called with. The patch is applied to
    `repo2graph.embed` (and to `repo2graph.cli` if it re-exports the symbol), so
    it holds whether the CLI reaches the function through the module or through
    a function-local `from .embed import default_embedder`.
    """
    from repo2graph import cli as cli_mod
    from repo2graph import embed as embed_mod

    class Factory:
        def __init__(self):
            self.made = []
            self.names = []
            self.model_id = "stub/mini-v1"
            self.dim = 8

        def __call__(self, name=None):
            self.names.append(name)
            emb = StubEmbedder(model_id=name or self.model_id, dim=self.dim)
            self.made.append(emb)
            return emb

        @property
        def last(self):
            return self.made[-1]

    factory = Factory()
    monkeypatch.setattr(embed_mod, "default_embedder", factory)
    if hasattr(cli_mod, "default_embedder"):
        monkeypatch.setattr(cli_mod, "default_embedder", factory)
    return factory


# --------------------------------------------------------------------------
# Goldens
# --------------------------------------------------------------------------


def golden_text(name: str) -> str:
    with open(GOLDEN_DIR / name, encoding="utf8", newline="\n") as fh:
        return fh.read()


def golden_json(name: str):
    return json.loads(golden_text(name))


def write_golden_text(name: str, text: str) -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    with open(GOLDEN_DIR / name, "w", encoding="utf8", newline="\n") as fh:
        fh.write(text)


def write_golden_json(name: str, data) -> None:
    write_golden_text(name, json.dumps(data, indent=2, sort_keys=True) + "\n")
