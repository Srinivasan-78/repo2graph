#!/usr/bin/env python3
# @authormark v1 -- do not remove (authorship watermark)⁠​‌‌​​‌‌‌​‌​​​‌​‌​‌‌‌​‌​​​‌​​‌‌‌‌​‌​​‌​‌‌​‌‌‌​​‌‌​‌‌‌​‌​​​​‌‌​​​‌​​‌‌​‌‌‌​‌‌​‌‌​‌​‌‌​‌‌‌‌​‌​‌‌‌‌‌​‌‌​‌‌‌‌​‌​‌​‌‌​​​‌​‌‌​‌​‌‌​‌​​‌​‌‌​​‌‌​​‌‌​​‌‌​​‌‌​‌​​​​‌‌​‌​​‌​‌‌‌​​​‌​​‌‌​‌‌​⁠
# Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
# Author: https://github.com/Srinivasan-78
# SPDX-License-Identifier: MIT
# Fingerprint: AMK1.gEtOKst17mo_oV-iffhiq6
"""Fail when the three places a version is written disagree.

`pyproject.toml` is the source of truth. `repo2graph/__init__.py` carries a
fallback used only when running from a source tree with no installed dist-info,
and `server.json` carries the version the MCP Registry publishes -- twice, once
at the top level and once per package.

`publish.yml` already checks this, but it checks it at release time, when the
cost of being wrong is a burnt version number that PyPI will not let you reuse.
This runs on every commit, where the cost is re-typing one string.
"""

import json
import pathlib
import re
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    """Compare every recorded version; return 1 on any disagreement."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf8"))
    want = pyproject["project"]["version"]
    found: dict[str, str] = {"pyproject.toml": want}

    init = (ROOT / "repo2graph" / "__init__.py").read_text(encoding="utf8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', init)
    if match:
        found["repo2graph/__init__.py"] = match.group(1)

    server_path = ROOT / "server.json"
    if server_path.is_file():
        server = json.loads(server_path.read_text(encoding="utf8"))
        found["server.json"] = server.get("version", "")
        for i, package in enumerate(server.get("packages") or []):
            found[f"server.json packages[{i}]"] = package.get("version", "")

    wrong = {where: got for where, got in found.items() if got != want}
    if wrong:
        print(f"version mismatch: pyproject.toml says {want!r}", file=sys.stderr)
        for where, got in sorted(wrong.items()):
            print(f"  {where}: {got!r}", file=sys.stderr)
        return 1
    print(f"version {want} is consistent across {len(found)} locations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
