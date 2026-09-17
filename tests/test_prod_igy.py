"""Tests for prod-igy GitHub Actions workflow and PR assistant script."""

import json
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "prod-igy.yml"
SCRIPT_PATH = REPO_ROOT / ".github" / "scripts" / "prod-igy.js"


def test_prod_igy_workflow_structure():
    """Verify that prod-igy.yml is well-formed and meets security/repo requirements."""
    assert WORKFLOW_PATH.is_file(), f"Workflow missing: {WORKFLOW_PATH}"

    # Text slicing rule from AGENTS.md: split on \n
    content = WORKFLOW_PATH.read_text(encoding="utf-8")
    data = yaml.safe_load(content)

    assert data.get("name") == "prod-igy"

    # Must trigger on pull_request_target
    on_trigger = data.get("on") or data.get(True, {})
    assert "pull_request_target" in on_trigger
    types = on_trigger["pull_request_target"].get("types", [])
    assert "opened" in types
    assert "synchronize" in types

    # Must support workflow_dispatch with pr_number input
    assert "workflow_dispatch" in on_trigger
    assert "pr_number" in on_trigger["workflow_dispatch"].get("inputs", {})

    # Top-level permissions must be narrow (contents: read)
    assert data.get("permissions") == {"contents": "read"}

    # Triage job permissions
    jobs = data.get("jobs", {})
    assert "triage" in jobs
    triage = jobs["triage"]
    job_perms = triage.get("permissions", {})
    assert job_perms.get("pull-requests") == "write"
    assert job_perms.get("issues") == "write"
    assert job_perms.get("contents") == "read"

    # Action pins: all uses: must be pinned by SHA
    steps = triage.get("steps", [])
    for step in steps:
        uses = step.get("uses")
        if uses:
            assert "@" in uses
            action, ref = uses.split("@", 1)
            # Ref must be a full 40-character hex SHA
            sha = ref.split()[0]  # strip any trailing comment
            assert len(sha) == 40 and all(
                c in "0123456789abcdefABCDEF" for c in sha
            ), f"Action {action} is not pinned to a commit SHA: {ref}"


def _run_node_script(js_code: str) -> str:
    """Helper to run a Node snippet requiring prod-igy.js."""
    script_forward = str(SCRIPT_PATH).replace("\\", "/")
    full_code = f"""
    const prodIgy = require('{script_forward}');
    {js_code}
    """
    proc = subprocess.run(
        ["node", "-e", full_code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        timeout=10,
    )
    assert (
        proc.returncode == 0
    ), f"Node script failed (exit {proc.returncode}):\nSTDOUT:\n{proc.stdout.decode('utf-8', 'replace')}\nSTDERR:\n{proc.stderr.decode('utf-8', 'replace')}"
    return proc.stdout.decode("utf-8", "replace")


def test_prod_igy_calculate_size():
    """Verify lines-changed size classification."""
    code = """
    const sizes = [
      prodIgy.calculateSize(0),
      prodIgy.calculateSize(9),
      prodIgy.calculateSize(10),
      prodIgy.calculateSize(49),
      prodIgy.calculateSize(50),
      prodIgy.calculateSize(249),
      prodIgy.calculateSize(250),
      prodIgy.calculateSize(999),
      prodIgy.calculateSize(1000)
    ];
    console.log(JSON.stringify(sizes));
    """
    out = _run_node_script(code)
    sizes = json.loads(out)
    assert sizes == [
        "size/XS",
        "size/XS",
        "size/S",
        "size/S",
        "size/M",
        "size/M",
        "size/L",
        "size/L",
        "size/XL",
    ]


def test_prod_igy_detect_type():
    """Verify PR type detection from title, branch, and touched files."""
    code = """
    const types = [
      prodIgy.detectType('feat: add vector embeddings', 'main', []),
      prodIgy.detectType('feat(walker): add gitignore support', 'feature/git', []),
      prodIgy.detectType('fix: handle cp1252 encoding on windows', 'fix/enc', []),
      prodIgy.detectType('docs: update backlog', 'docs/edit', []),
      prodIgy.detectType('test: add unit tests', 'test/rag', []),
      prodIgy.detectType('refactor: simplify traversal', 'refactor/graph', []),
      prodIgy.detectType('chore: bump uv lock', 'chore/lock', []),
      prodIgy.detectType('ci: pin actions', 'ci/pins', []),
      prodIgy.detectType('Update pytest', 'dependabot/pip/pytest-8.0', []),
      prodIgy.detectType('Random title', 'my-branch', ['docs/index.md', 'README.md']),
      prodIgy.detectType('Another title', 'my-branch', ['tests/test_cli.py']),
      prodIgy.detectType('Unrecognized title', 'my-branch', ['repo2graph/core.py'])
    ];
    console.log(JSON.stringify(types));
    """
    out = _run_node_script(code)
    types = json.loads(out)
    assert types == [
        "feat",
        "feat",
        "fix",
        "docs",
        "test",
        "refactor",
        "chore",
        "chore",
        "dependencies",
        "docs",
        "test",
        None,
    ]


def test_prod_igy_detect_areas():
    """Verify subsystem area mapping from changed file paths."""
    code = """
    const files = [
      'repo2graph/walker.py',
      'repo2graph/graph.py',
      'repo2graph/query.py',
      'repo2graph/mcp.py',
      'repo2graph/cli.py',
      'repo2graph/embed.py',
      'action.yml',
      '.github/workflows/ci.yml',
      'tests/test_graph.py',
      'docs/architecture.md'
    ];
    const areas = prodIgy.detectAreas(files).sort();
    console.log(JSON.stringify(areas));
    """
    out = _run_node_script(code)
    areas = json.loads(out)
    expected = [
        "area/action",
        "area/cli",
        "area/docs",
        "area/embed",
        "area/graph",
        "area/mcp",
        "area/query",
        "area/tests",
        "area/walker",
        "area/workflows",
    ]
    assert areas == expected


def test_prod_igy_extract_issues():
    """Verify issue extraction from PR text."""
    code = """
    const text = 'This PR fixes #42, closes GH-105, and resolves #200. Also mentions #999 without action word.';
    const issues = prodIgy.extractIssues(text).sort();
    console.log(JSON.stringify(issues));
    """
    out = _run_node_script(code)
    issues = json.loads(out)
    assert issues == ["105", "200", "42"]


def test_prod_igy_check_agents_rules():
    """Verify AGENTS.md rule advisor highlights sensitive files."""
    code = """
    const walkerGraphRules = prodIgy.checkAgentsRules(['repo2graph/walker.py']);
    const queryRules = prodIgy.checkAgentsRules(['repo2graph/query.py']);
    const mcpRules = prodIgy.checkAgentsRules(['repo2graph/mcp.py']);
    const actionRules = prodIgy.checkAgentsRules(['action.yml']);
    const examplesRules = prodIgy.checkAgentsRules(['examples/django/metadata.json']);
    console.log(JSON.stringify({
      walkerGraphRules,
      queryRules,
      mcpRules,
      actionRules,
      examplesRules
    }));
    """
    out = _run_node_script(code)
    rules = json.loads(out)

    assert any(
        "Text Slicing" in r and "splitlines" in r for r in rules["walkerGraphRules"]
    )
    assert any("Windows Git" in r for r in rules["walkerGraphRules"])
    assert any("Two Budget Models" in r for r in rules["queryRules"])
    assert any("Caller-Hostile MCP Arguments" in r for r in rules["mcpRules"])
    assert any("Truthiness Seam" in r for r in rules["actionRules"])
    assert any("Never hand-edit" in r for r in rules["examplesRules"])


def test_prod_igy_format_comment_reminders():
    """Verify that formatBotComment tags the author when outdated or conflicted."""
    code = """
    const outdatedComment = prodIgy.formatBotComment({
      author: 'alice',
      baseRef: 'main',
      baseSha: 'abcdef123456',
      headRef: 'alice/feature',
      headSha: '123456abcdef',
      behindBy: 5,
      aheadBy: 2,
      status: 'diverged',
      mergeable: true,
      mergeableState: 'clean',
      linkedIssues: ['42'],
      linesChanged: 80,
      changedFiles: ['repo2graph/walker.py'],
      guidance: ['- Some guidance'],
      labelsApplied: ['feat', 'size/M', 'area/walker', 'needs-rebase']
    });

    const conflictedComment = prodIgy.formatBotComment({
      author: 'bob',
      baseRef: 'main',
      baseSha: 'abcdef123456',
      headRef: 'bob/fix',
      headSha: '654321fedcba',
      behindBy: 0,
      aheadBy: 1,
      status: 'ahead',
      mergeable: false,
      mergeableState: 'dirty',
      linkedIssues: [],
      linesChanged: 15,
      changedFiles: ['repo2graph/query.py'],
      guidance: [],
      labelsApplied: ['fix', 'size/S', 'area/query', 'has-conflicts']
    });

    const cleanComment = prodIgy.formatBotComment({
      author: 'carol',
      baseRef: 'main',
      baseSha: 'abcdef123456',
      headRef: 'carol/docs',
      headSha: '999999abcdef',
      behindBy: 0,
      aheadBy: 1,
      status: 'ahead',
      mergeable: true,
      mergeableState: 'clean',
      linkedIssues: [],
      linesChanged: 5,
      changedFiles: ['docs/index.md'],
      guidance: [],
      labelsApplied: ['docs', 'size/XS', 'area/docs']
    });

    console.log(JSON.stringify({ outdatedComment, conflictedComment, cleanComment }));
    """
    out = _run_node_script(code)
    comments = json.loads(out)

    outdated = comments["outdatedComment"]
    assert "<!-- prod-igy-bot-comment -->" in outdated
    assert "@alice" in outdated
    assert "behind `main` by 5 commit(s)" in outdated
    assert "git rebase origin/main" in outdated

    conflicted = comments["conflictedComment"]
    assert "@bob" in conflicted
    assert "Merge conflicts detected!" in conflicted
    assert "git merge origin/main" in conflicted

    clean = comments["cleanComment"]
    assert "@carol" in clean
    assert "Attention @carol" not in clean  # no warning alert for clean PR
    assert "[x] Branch is up to date with `main`" in clean
    assert "[x] No merge conflicts" in clean


def test_prod_igy_end_to_end_mock_execution():
    """Verify run({ github, context, core }) end-to-end with mock Octokit objects."""
    code = """
    const addedLabels = [];
    const removedLabels = [];
    let createdComment = null;
    let updatedComment = null;
    const createdRepoLabels = [];

    const mockGithub = {
      rest: {
        pulls: {
          get: async () => ({
            data: {
              number: 101,
              title: 'feat(walker): add ignore support',
              body: 'This PR adds ignore support. Fixes #50.',
              user: { login: 'octocat' },
              base: { ref: 'main', sha: '1111111111111111111111111111111111111111' },
              head: { ref: 'octocat/ignore', sha: '2222222222222222222222222222222222222222' },
              additions: 30,
              deletions: 5,
              mergeable: true,
              mergeable_state: 'clean',
              labels: [{ name: 'size/L' }]
            }
          }),
          listFiles: async () => [
            { filename: 'repo2graph/walker.py' },
            { filename: 'tests/test_walker.py' }
          ]
        },
        repos: {
          compareCommits: async () => ({
            data: {
              behind_by: 2,
              ahead_by: 1,
              status: 'diverged'
            }
          })
        },
        issues: {
          listComments: async () => [],
          listLabelsForRepo: async () => [
            { name: 'feat' }
          ],
          createLabel: async ({ name }) => {
            createdRepoLabels.push(name);
          },
          addLabels: async ({ labels }) => {
            addedLabels.push(...labels);
          },
          removeLabel: async ({ name }) => {
            removedLabels.push(name);
          },
          createComment: async ({ body }) => {
            createdComment = body;
          },
          updateComment: async ({ body }) => {
            updatedComment = body;
          }
        }
      },
      paginate: async (fn, args) => {
        return await fn(args);
      }
    };

    const mockContext = {
      repo: { owner: 'Srinivasan-78', repo: 'repo2graph' },
      payload: {
        pull_request: {
          number: 101,
          user: { login: 'octocat' }
        }
      }
    };

    const mockCore = {
      info: () => {},
      warning: () => {},
      debug: () => {}
    };

    prodIgy({ github: mockGithub, context: mockContext, core: mockCore }).then(() => {
      console.log(JSON.stringify({
        addedLabels,
        removedLabels,
        createdRepoLabels,
        hasCreatedComment: Boolean(createdComment),
        commentContainsAuthor: createdComment && createdComment.includes('@octocat'),
        commentContainsRebase: createdComment && createdComment.includes('git rebase origin/main')
      }));
    });
    """
    out = _run_node_script(code)
    res = json.loads(out)

    assert "feat" in res["addedLabels"]
    assert "size/S" in res["addedLabels"]  # 35 lines changed
    assert "area/walker" in res["addedLabels"]
    assert "area/tests" in res["addedLabels"]
    assert "needs-rebase" in res["addedLabels"]  # behind_by = 2
    assert "size/L" in res["removedLabels"]  # old size label removed
    assert res["hasCreatedComment"] is True
    assert res["commentContainsAuthor"] is True
    assert res["commentContainsRebase"] is True


def test_prod_igy_workflow_dispatch_sweep():
    """Verify workflow_dispatch sweeps all open PRs when pr_number is 'all'."""
    code = """
    const inspectedPrNumbers = [];
    const mockGithub = {
      rest: {
        pulls: {
          list: async () => [
            { number: 10 },
            { number: 20 }
          ],
          get: async ({ pull_number }) => {
            inspectedPrNumbers.push(pull_number);
            return {
              data: {
                number: pull_number,
                title: 'chore: update deps',
                body: 'Routine updates',
                user: { login: 'dependabot' },
                base: { ref: 'main', sha: '1111' },
                head: { ref: 'deps', sha: '2222' },
                additions: 2,
                deletions: 1,
                mergeable: true,
                mergeable_state: 'clean',
                labels: []
              }
            };
          },
          listFiles: async () => []
        },
        repos: {
          compareCommits: async () => ({
            data: { behind_by: 0, ahead_by: 1, status: 'ahead' }
          })
        },
        issues: {
          listComments: async () => [],
          listLabelsForRepo: async () => [{ name: 'chore' }, { name: 'size/XS' }],
          createLabel: async () => {},
          addLabels: async () => {},
          removeLabel: async () => {},
          createComment: async () => {},
          updateComment: async () => {}
        }
      },
      paginate: async (fn, args) => {
        return await fn(args);
      }
    };

    const mockContext = {
      eventName: 'workflow_dispatch',
      repo: { owner: 'Srinivasan-78', repo: 'repo2graph' },
      payload: {
        inputs: { pr_number: 'all' }
      }
    };

    const mockCore = {
      info: () => {},
      warning: () => {},
      debug: () => {}
    };

    prodIgy({ github: mockGithub, context: mockContext, core: mockCore }).then(() => {
      console.log(JSON.stringify({ inspectedPrNumbers }));
    });
    """
    out = _run_node_script(code)
    res = json.loads(out)
    assert res["inspectedPrNumbers"] == [10, 20]

