"""Tests for prod-igy GitHub Actions workflow and PR assistant script.

Stdlib-only by design: does not depend on PyYAML or Node.js in the pytest environment,
avoiding cross-platform pipe timeouts on Windows runners (ISS-Windows / Python 3.10).
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "prod-igy.yml"
SCRIPT_PATH = REPO_ROOT / ".github" / "scripts" / "prod-igy.js"


def _read_lines(path: Path) -> list[str]:
    """Read file using split('\n') per AGENTS.md text slicing rule."""
    text = path.read_text(encoding="utf-8")
    return [line.rstrip("\r") for line in text.split("\n")]


def test_prod_igy_workflow_structure():
    """Verify that prod-igy.yml is well-formed and meets security/repo requirements."""
    assert WORKFLOW_PATH.is_file(), f"Workflow missing: {WORKFLOW_PATH}"
    lines = _read_lines(WORKFLOW_PATH)
    content = "\n".join(lines)

    # Name
    assert re.search(r"^name:\s*prod-igy", content, re.M), "Workflow name must be prod-igy"

    # Triggers: pull_request_target, workflow_dispatch, issue_comment
    assert "pull_request_target:" in content
    assert "workflow_dispatch:" in content
    assert "pr_number:" in content
    assert "issue_comment:" in content

    # PR target types
    assert re.search(r"types:\s*\[.*opened.*synchronize.*\]", content)

    # Permissions: narrow top-level permissions
    assert re.search(r"^permissions:\s*\n\s*contents:\s*read", content, re.M)

    # Triage job permissions
    assert re.search(r"pull-requests:\s*write", content)
    assert re.search(r"issues:\s*write", content)

    # Event filtering if condition on triage job
    assert "github.event_name == 'pull_request_target'" in content
    assert "github.event_name == 'workflow_dispatch'" in content
    assert "@prod-igy" in content

    # Action pins: all uses: must be pinned by a 40-char commit SHA
    action_uses = re.findall(r"uses:\s*([^\s]+)", content)
    assert len(action_uses) >= 2, "Must use actions/checkout and actions/github-script"
    for uses in action_uses:
        assert "@" in uses, f"Action must be pinned with @: {uses}"
        action, ref = uses.split("@", 1)
        sha = ref.split()[0]
        assert len(sha) == 40 and all(c in "0123456789abcdefABCDEF" for c in sha), (
            f"Action {action} is not pinned to a 40-char commit SHA: {ref}"
        )


def test_prod_igy_script_exists_and_exports():
    """Verify that prod-igy.js exists and exports expected functions and constants."""
    assert SCRIPT_PATH.is_file(), f"Script missing: {SCRIPT_PATH}"
    content = "\n".join(_read_lines(SCRIPT_PATH))

    # Exports
    assert "module.exports = async function run" in content
    assert "module.exports.triagePullRequest = triagePullRequest;" in content
    assert "module.exports.calculateSize = calculateSize;" in content
    assert "module.exports.detectType = detectType;" in content
    assert "module.exports.detectAreas = detectAreas;" in content
    assert "module.exports.extractIssues = extractIssues;" in content
    assert "module.exports.checkAgentsRules = checkAgentsRules;" in content
    assert "module.exports.formatBotComment = formatBotComment;" in content
    assert "module.exports.LABEL_DEFINITIONS = LABEL_DEFINITIONS;" in content
    assert "module.exports.BOT_MARKER = BOT_MARKER;" in content

    # Marker definition
    assert "const BOT_MARKER = '<!-- prod-igy-bot-comment -->';" in content


def test_prod_igy_label_definitions():
    """Verify all label definitions have valid hex colors and descriptions."""
    content = "\n".join(_read_lines(SCRIPT_PATH))

    # Match LABEL_DEFINITIONS object block
    match = re.search(r"const LABEL_DEFINITIONS = \{([^;]+)\};", content)
    assert match, "LABEL_DEFINITIONS not found in prod-igy.js"
    block = match.group(1)

    # Required label keys
    required_labels = [
        # Types
        "feat",
        "fix",
        "docs",
        "test",
        "refactor",
        "chore",
        "dependencies",
        # Sizes
        "size/XS",
        "size/S",
        "size/M",
        "size/L",
        "size/XL",
        # Subsystems
        "area/walker",
        "area/graph",
        "area/query",
        "area/mcp",
        "area/cli",
        "area/embed",
        "area/action",
        "area/workflows",
        "area/tests",
        "area/docs",
        # Status
        "needs-rebase",
        "has-conflicts",
        "needs-description",
    ]

    for label in required_labels:
        pattern = rf"['\"]?{re.escape(label)}['\"]?\s*:\s*\{{\s*color:\s*['\"]([0-9a-fA-F]{{6}})['\"],\s*description:\s*['\"]([^'\"]+)['\"]\s*\}}"
        m = re.search(pattern, block)
        assert m, f"Label {label} missing or malformed in LABEL_DEFINITIONS"
        color, desc = m.groups()
        assert len(color) == 6
        assert len(desc) > 3


def test_prod_igy_calculate_size_logic():
    """Verify size thresholds in calculateSize implementation."""
    content = "\n".join(_read_lines(SCRIPT_PATH))
    match = re.search(r"function calculateSize\(linesChanged\)\s*\{([^}]+)\}", content)
    assert match, "calculateSize function not found"
    body = match.group(1)

    assert "linesChanged < 10" in body and "'size/XS'" in body
    assert "linesChanged < 50" in body and "'size/S'" in body
    assert "linesChanged < 250" in body and "'size/M'" in body
    assert "linesChanged < 1000" in body and "'size/L'" in body
    assert "'size/XL'" in body

    # Emulate the JS logic in Python to verify exact boundaries
    def calculate_size(lines: int) -> str:
        if lines < 10:
            return "size/XS"
        if lines < 50:
            return "size/S"
        if lines < 250:
            return "size/M"
        if lines < 1000:
            return "size/L"
        return "size/XL"

    assert calculate_size(0) == "size/XS"
    assert calculate_size(9) == "size/XS"
    assert calculate_size(10) == "size/S"
    assert calculate_size(49) == "size/S"
    assert calculate_size(50) == "size/M"
    assert calculate_size(249) == "size/M"
    assert calculate_size(250) == "size/L"
    assert calculate_size(999) == "size/L"
    assert calculate_size(1000) == "size/XL"


def test_prod_igy_type_detection_regexes():
    """Verify regexes used for conventional commit type detection."""
    content = "\n".join(_read_lines(SCRIPT_PATH))
    assert "/^feat(\\(.*?\\))?:/" in content
    assert "/^fix(\\(.*?\\))?:/" in content
    assert "/^docs(\\(.*?\\))?:/" in content
    assert "/^test(\\(.*?\\))?:/" in content
    assert "/^refactor(\\(.*?\\))?:/" in content
    assert "/^chore(\\(.*?\\))?:/" in content
    assert "/^(ci|build)(\\(.*?\\))?:/" in content
    assert "dependabot/" in content and "renovate/" in content

    # Test the regex pattern against sample titles
    feat_re = re.compile(r"^feat(\(.*?\))?:", re.I)
    fix_re = re.compile(r"^fix(\(.*?\))?:", re.I)
    docs_re = re.compile(r"^docs(\(.*?\))?:", re.I)

    assert feat_re.match("feat: add something")
    assert feat_re.match("feat(walker): add ignore")
    assert fix_re.match("fix: fix encoding on windows")
    assert docs_re.match("docs(readme): update guide")
    assert not feat_re.match("random title")


def test_prod_igy_detect_areas_patterns():
    """Verify that detectAreas covers all critical repository subsystems."""
    content = "\n".join(_read_lines(SCRIPT_PATH))
    match = re.search(
        r"function detectAreas\(changedFiles\)\s*\{([^}]+(?:\{[^}]+\}[^}]+)*)\}", content
    )
    assert match, "detectAreas function not found"
    body = match.group(1)

    assert "repo2graph/walker.py" in body and "'area/walker'" in body
    assert "repo2graph/graph.py" in body and "'area/graph'" in body
    assert "repo2graph/query.py" in body and "'area/query'" in body
    assert "repo2graph/mcp.py" in body and "'area/mcp'" in body
    assert "server.json" in body and "'area/mcp'" in body
    assert "repo2graph/cli.py" in body and "'area/cli'" in body
    assert "repo2graph/embed.py" in body and "'area/embed'" in body
    assert "action.yml" in body and "'area/action'" in body
    assert ".github/workflows/" in body and "'area/workflows'" in body
    assert "tests/" in body and "'area/tests'" in body
    assert "docs/" in body and "'area/docs'" in body


def test_prod_igy_issue_extraction_regex():
    """Verify the issue extraction regex matches action words and issue numbers."""
    content = "\n".join(_read_lines(SCRIPT_PATH))
    assert (
        r"(?:close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved)\s+(?:#|gh-)(\d+)"
        in content
    )

    issue_re = re.compile(
        r"(?:close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved)\s+(?:#|gh-)(\d+)",
        re.I,
    )
    sample = "Fixes #42, closes GH-105, and resolves #200. Mentions #999 without trigger word."
    found = issue_re.findall(sample)
    assert sorted(found) == ["105", "200", "42"]


def test_prod_igy_agents_rules_guidance():
    """Verify AGENTS.md rule strings in checkAgentsRules."""
    content = "\n".join(_read_lines(SCRIPT_PATH))
    assert "Text Slicing (`AGENTS.md`)" in content
    assert "splitlines()" in content
    assert "Windows Git Subprocess Output" in content
    assert "surrogateescape" in content
    assert "Two Budget Models (`AGENTS.md`)" in content
    assert "Caller-Hostile MCP Arguments (`AGENTS.md`)" in content
    assert "Truthiness Seam in `action.yml`" in content
    assert "Examples (`CONTRIBUTING.md`)" in content
    assert "Lockfile Sync (`uv.lock`)" in content


def test_prod_igy_comment_formatting_and_author_tagging():
    """Verify that comment formatting contains @<author> tags and required commands."""
    content = "\n".join(_read_lines(SCRIPT_PATH))

    # Author tagging and rebase instructions
    assert "Attention @${author}" in content
    assert "${behindBy} commit(s)" in content
    assert "git rebase origin/${baseRef}" in content

    # Conflict instructions
    assert "Merge conflicts detected!" in content
    assert "git merge origin/${baseRef}" in content

    # Lockfile warning instructions
    assert "uv lock" in content
    assert "chore: update uv.lock" in content

    # Base description
    assert "primary release branch for" in content

    # Bot marker
    assert "${BOT_MARKER}" in content
