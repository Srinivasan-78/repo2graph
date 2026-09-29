"""Compatibility module re-exporting from repo2graph.security."""

from . import security
from .security import *  # noqa: F401, F403

# Re-export all public and internal attributes for backward compatibility
for _k, _v in list(security.__dict__.items()):
    if not _k.startswith("__"):
        globals()[_k] = _v
