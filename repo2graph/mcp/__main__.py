"""Entry point for python -m repo2graph.mcp."""

from __future__ import annotations

import sys
from .server import main

if __name__ == "__main__":
    sys.exit(main())
