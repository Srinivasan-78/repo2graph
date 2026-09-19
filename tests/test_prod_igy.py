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
    assert "module.exports.escapeMdRef = escapeMdRef;" in content
    assert "module.exports.isTrustedCommenter = isTrustedCommenter;" in content
    assert "module.exports.TRUSTED_ASSOCIATIONS = TRUSTED_ASSOCIATIONS;" in content
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


def test_iss208_issue_comment_job_requires_trusted_author_association():
    """ISS-208: outsider PR comments must not start the privileged triage job.

    The gate lives in the workflow `if:` so no runner is allocated. Trusted
    associations are the three GitHub values for people who can change the
    repo; CONTRIBUTOR / NONE / FIRST_TIME_CONTRIBUTOR are not among them.
    """
    content = "\n".join(_read_lines(WORKFLOW_PATH))

    assert "github.event.comment.author_association == 'OWNER'" in content
    assert "github.event.comment.author_association == 'MEMBER'" in content
    assert "github.event.comment.author_association == 'COLLABORATOR'" in content

    # The association check is on the issue_comment clause only — not a
    # job-wide filter that would skip pull_request_target / workflow_dispatch.
    issue_comment_clause = content.split("github.event_name == 'issue_comment'", 1)[1]
    dispatch_clause = content.split("github.event_name == 'workflow_dispatch'", 1)[0]
    assert "author_association" in issue_comment_clause
    assert "author_association" not in dispatch_clause

    # Exact ==, not contains(): 'CONTRIBUTOR' must not match 'COLLABORATOR'.
    assert "contains(github.event.comment.author_association" not in content


def test_iss208_script_issue_comment_skips_untrusted_association():
    """ISS-208: script defence if the workflow `if:` is ever widened."""
    content = "\n".join(_read_lines(SCRIPT_PATH))

    match = re.search(
        r"const TRUSTED_ASSOCIATIONS = \[([^\]]+)\]",
        content,
    )
    assert match, "TRUSTED_ASSOCIATIONS allowlist not found"
    allowed = [part.strip().strip("'\"") for part in match.group(1).split(",") if part.strip()]
    assert allowed == ["OWNER", "MEMBER", "COLLABORATOR"]
    assert "CONTRIBUTOR" not in allowed
    assert "NONE" not in allowed
    assert "FIRST_TIME_CONTRIBUTOR" not in allowed
    assert "FIRST_TIMER" not in allowed

    assert "function isTrustedCommenter(association)" in content
    assert "context.payload?.comment?.author_association" in content

    # Early return sits on the issue_comment path, before triagePullRequest.
    start = content.index("if (eventName === 'issue_comment')")
    end = content.index("await triagePullRequest", start)
    branch = content[start:end]
    assert "isTrustedCommenter(association)" in branch
    assert "return;" in branch


def test_iss208_format_bot_comment_strips_backticks_from_refs():
    """ISS-208: a fork branch name must not break out of a markdown code span.

    git check-ref-format permits `` ` ``. The Head Commit line (and every
    other `` `${headRef}` `` / `` `${baseRef}` `` interpolation) wraps the
    ref in backticks; closing that span plants attacker markdown under the
    bot identity. SHA slices stay raw — they are not attacker-controlled
    the same way.
    """
    content = "\n".join(_read_lines(SCRIPT_PATH))

    assert "function escapeMdRef(ref)" in content
    assert ".replace(/`/g, '')" in content

    start = content.index("function formatBotComment")
    head_assign = content.index("headRef = escapeMdRef(headRef)", start)
    base_assign = content.index("baseRef = escapeMdRef(baseRef)", start)
    first_head = content.index("${headRef}", start)
    first_base = content.index("${baseRef}", start)
    assert head_assign < first_head
    assert base_assign < first_base

    # SHAs are not passed through escapeMdRef (not a fork-controlled ref).
    assert "escapeMdRef(baseSha)" not in content
    assert "escapeMdRef(headSha)" not in content
    assert "escapeMdRef(shortBaseSha)" not in content
    assert "escapeMdRef(shortHeadSha)" not in content

    # Contract pinned by the issue: String(ref).replace(/`/g, '').
    # Unescaped: `x`](https://evil.example)` closes the span after x.
    raw = "x`](https://evil.example)"
    escaped = str(raw).replace("`", "")
    rendered = f"`{escaped}`"
    unescaped = f"`{raw}`"
    assert unescaped == "`x`](https://evil.example)`"
    assert rendered == "`x](https://evil.example)`"
    assert rendered.count("`") == 2
    assert "`" not in escaped
    assert f"`{raw}`".count("`") == 3
