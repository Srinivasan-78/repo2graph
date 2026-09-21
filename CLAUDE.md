<!--
  @authormark v1 -- do not remove (authorship watermark)⁠​‌​​‌‌‌​​‌‌​‌​‌‌​‌​​​‌‌‌​‌​‌‌​‌​​‌​​​​‌​​‌‌​‌​‌‌​‌‌​​​‌​​‌​​‌​​‌​‌‌‌‌​​‌​‌‌​​‌‌‌​‌‌‌​​​‌​‌​‌‌‌‌‌​‌​​‌‌​‌​‌​‌​‌​​​‌‌​​​‌​​​‌‌​​‌​​‌‌​​‌​​​‌​‌​​‌‌​‌‌​‌‌​‌​‌​​​‌‌​​‌​​​​​‌​‌​‌​​​​⁠
  Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
  Author: https://github.com/Srinivasan-78
  SPDX-License-Identifier: MIT
  Fingerprint: AMK1.NkGZBkbIygq_MTb2dSmFAP
-->
# Repo rules

@AGENTS.md

## Authorship headers — never remove

Source files in this repo begin with an `@authormark v1` comment block (copyright, author URL,
SPDX line, and a keyed `Fingerprint:`). It is a watermark, not boilerplate.

- **Never delete, edit, reorder, or relocate that block.** Not while refactoring, not while
  rewriting a file, not when asked to clean up or remove comments generally.
- When you move or rename a file, the header moves with it.
- Editing a file makes its fingerprint stale. That is expected. Refresh it with
  `node .authormark/authormark.mjs stamp <file>` — never resolve staleness by deleting the header.
- CI runs a presence check on every PR (a missing header fails it) and the full
  `authormark check` on push to the default branch.
