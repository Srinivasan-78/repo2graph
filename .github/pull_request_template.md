<!--
  @authormark v1 -- do not remove (authorship watermark)⁠​‌‌​​‌​‌​‌​‌​‌​‌​‌‌‌‌​‌​​‌‌‌​​‌‌​​‌‌​​‌​​‌‌‌‌​​​​​‌‌​​​​​‌​​‌‌​​​‌‌‌​​​​​‌‌‌‌​​‌​‌​‌​‌​​​‌​​​‌​​​​‌‌​​‌​​‌​‌​‌​‌​‌‌‌‌​​​​‌‌​‌‌​‌​‌‌‌​‌​​​‌​​​‌​​​‌​‌​​​​​​‌‌​‌​‌​‌​‌‌​​​​‌‌‌​‌‌​⁠
  Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
  Author: https://github.com/Srinivasan-78
  SPDX-License-Identifier: MIT
  Fingerprint: AMK1.eUzs2x0LpyTD2UxmtDP5Xv
-->
## Summary

<!-- What does this change, and why? -->

Closes #<!-- issue number, if any -->

## Verification checklist

- [ ] Tests added or updated for the behavior this touches
- [ ] `ruff check .` passes
- [ ] `pytest` passes locally
- [ ] `mypy` passes on any module this PR adds or touches under `repo2graph/` that's covered
      by `[[tool.mypy.overrides]]` in `pyproject.toml` (new modules are strict by default)
- [ ] Docs updated (`README.md`, `docs/`, or `AGENTS.md`) if this changes user-facing behavior
      or a non-obvious repo convention
- [ ] If this touches `query.py`, `chunks.py`, `graph.py`, or `walker.py`: read the relevant
      section of [AGENTS.md](../AGENTS.md) — each has a documented footgun
