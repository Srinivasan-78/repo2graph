# SPDX-License-Identifier: MIT
"""Tests for scripts/check_workflow_summaries.py (issue #460).

Guards against the hardcoded "Passed" table cell regressing into a
workflow's job summary, which previously rendered a green row even when
the step it reported on had just failed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_workflow_summaries import find_violations  # noqa: E402


def test_real_workflows_have_no_hardcoded_status(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    workflows_dir = repo_root / ".github" / "workflows"
    assert find_violations(workflows_dir) == []


def test_flags_hardcoded_table_cell(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yml"
    bad.write_text(
        "jobs:\n"
        "  x:\n"
        "    steps:\n"
        "      - run: |\n"
        "          echo '| **Check** | \u2705 Passed | tool |' >> \"$GITHUB_STEP_SUMMARY\"\n",
        encoding="utf-8",
    )
    violations = find_violations(tmp_path)
    assert len(violations) == 1
    assert "bad.yml" in violations[0]


def test_allows_outcome_derived_status(tmp_path: Path) -> None:
    good = tmp_path / "good.yml"
    good.write_text(
        "jobs:\n"
        "  x:\n"
        "    steps:\n"
        "      - run: |\n"
        '          status() { [ "$1" = "success" ] && echo "\u2705 Passed" || echo "\u274c Failed"; }\n'
        '          printf \'| **Check** | %s | tool |\\n\' "$(status "$OUTCOME")"\n',
        encoding="utf-8",
    )
    assert find_violations(tmp_path) == []
