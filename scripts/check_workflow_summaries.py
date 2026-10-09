#!/usr/bin/env python3
"""Reject a hardcoded "Passed" status in a workflow's job summary.

A `GITHUB_STEP_SUMMARY` block written under `if: always()` with a literal
"[check mark] Passed" renders a green row even when the step it describes
just failed. That bug shipped in `.github/workflows/ci.yml` four times
because it was copy-pasted; this check is the two-line grep the audit
(issue #460) asked for, so the pattern cannot be reintroduced the same way.

A workflow is allowed to render "Passed" only when it is computed from a
step's real `outcome` (e.g. via a shell variable). What is never allowed is
the literal checkmark-plus-"Passed" string appearing directly in the YAML,
because that can only mean it was written as a constant.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"

# The literal offending string. Written as a concatenation so this file
# itself is never flagged by the check it implements.
BANNED = "✅" + " Passed"

# Matches the status literally sitting inside a markdown table cell, e.g.
# `| ✅ Passed |`. This is deliberately narrower than "the string appears
# anywhere": a shell variable assignment like `status="✅ Passed"` (guarded
# by an `if [ "$OUTCOME" = success ]`) or a comment that merely mentions the
# phrase while explaining the fix are legitimate and must not be flagged --
# only the table cell written directly as a constant is the bug.
TABLE_CELL = re.compile(r"\|\s*" + re.escape(BANNED) + r"\s*\|")


def find_violations(workflows_dir: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(workflows_dir.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if TABLE_CELL.search(line):
                violations.append(
                    f"{path}:{lineno}: hardcoded '{BANNED}' table cell (not derived from a step outcome)"
                )
    return violations


def main() -> int:
    violations = find_violations(WORKFLOWS_DIR)
    if violations:
        print("Hardcoded step-summary status found (must reflect a real step outcome instead):")
        for v in violations:
            print("  " + v.encode("ascii", "backslashreplace").decode("ascii"))
        return 1
    print("No hardcoded summary statuses found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
