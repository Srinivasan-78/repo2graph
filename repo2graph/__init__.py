from .graph import build, Graph
from .chunks import build_chunks, iter_chunks
from .viz import write_html

import importlib.metadata

__all__ = ["build", "Graph", "build_chunks", "iter_chunks", "write_html"]
try:
    __version__ = importlib.metadata.version("repo2graph")
except Exception:
    # Only reached when running from a source tree with no installed dist-info;
    # keep it equal to [project] version in pyproject.toml.
    __version__ = "2.1.0"
