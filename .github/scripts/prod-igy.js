/**
 * prod-igy: Pull Request Assistant for repo2graph
 *
 * Responsibilities:
 * - Automatically categorizes and applies labels (type, size, area, status).
 * - Scans PR description and comments to report base branch target, branch status, and linked issues.
 * - Detects outdated branches (behind base) or merge conflicts and tags the PR author with actionable instructions.
 * - Provides repository-specific architecture and review guidance based on AGENTS.md.
 * - Updates comments idempotently using an HTML marker to keep PR threads clean.
 */

// Label configurations with hex colors (without #) and descriptions
const LABEL_DEFINITIONS = {
  // Types
  feat: { color: 'a2eeef', description: 'New feature or enhancement' },
  fix: { color: 'd73a4a', description: 'Bug fix' },
  docs: { color: '0075ca', description: 'Documentation improvements or changes' },
  test: { color: 'bfdadc', description: 'Tests added or updated' },
  refactor: { color: 'fef2c0', description: 'Code refactoring without behavioral change' },
  chore: { color: 'cfd3d7', description: 'Routine maintenance, dependencies, or tooling' },
  dependencies: { color: '0366d6', description: 'Dependency updates' },

  // Sizes (additions + deletions)
  'size/XS': { color: '0075ca', description: 'Extra small changes (< 10 lines)' },
  'size/S': { color: '7057ff', description: 'Small changes (< 50 lines)' },
  'size/M': { color: '008672', description: 'Medium changes (< 250 lines)' },
  'size/L': { color: 'd93f0b', description: 'Large changes (< 1000 lines)' },
  'size/XL': { color: 'b60205', description: 'Very large changes (>= 1000 lines)' },

  // Subsystem Areas
  'area/walker': { color: 'e99695', description: 'File discovery and traversal' },
  'area/graph': { color: 'f9d0c4', description: 'Graph construction, symbols, and edges' },
  'area/query': { color: 'c5def5', description: 'Query engine, retrieval, and context packing' },
  'area/mcp': { color: 'bfd4f2', description: 'Model Context Protocol (MCP) server' },
  'area/cli': { color: 'd4c5f9', description: 'Command-line interface and parsing' },
  'area/embed': { color: 'e6e6fa', description: 'Vector embeddings and fusion' },
  'area/action': { color: 'e11d48', description: 'GitHub Action (action.yml)' },
  'area/workflows': { color: '1d76db', description: 'CI/CD workflows and automation' },
  'area/tests': { color: 'c2e0c6', description: 'Test suite and fixtures' },
  'area/docs': { color: '0e8a16', description: 'Documentation and guides' },

  // Statuses & Alerts
  'needs-rebase': { color: 'e11d48', description: 'Branch is behind base branch and needs rebase' },
  'has-conflicts': { color: 'b60205', description: 'PR has merge conflicts with base branch' },
  'needs-description': { color: 'fbca04', description: 'PR description is empty or requires details' },
};

const BOT_MARKER = '<!-- prod-igy-bot-comment -->';

function calculateSize(linesChanged) {
  if (linesChanged < 10) return 'size/XS';
  if (linesChanged < 50) return 'size/S';
  if (linesChanged < 250) return 'size/M';
  if (linesChanged < 1000) return 'size/L';
  return 'size/XL';
}

function detectType(title, branchName, changedFiles) {
  const lowerTitle = (title || '').toLowerCase();
  const lowerBranch = (branchName || '').toLowerCase();

  if (/^feat(\(.*?\))?:/.test(lowerTitle)) return 'feat';
  if (/^fix(\(.*?\))?:/.test(lowerTitle)) return 'fix';
  if (/^docs(\(.*?\))?:/.test(lowerTitle)) return 'docs';
  if (/^test(\(.*?\))?:/.test(lowerTitle)) return 'test';
  if (/^refactor(\(.*?\))?:/.test(lowerTitle)) return 'refactor';
  if (/^chore(\(.*?\))?:/.test(lowerTitle)) return 'chore';
  if (/^(ci|build)(\(.*?\))?:/.test(lowerTitle)) return 'chore';

  if (lowerBranch.startsWith('dependabot/') || lowerBranch.startsWith('renovate/')) {
    return 'dependencies';
  }

  // Fallback by inspect files
  if (changedFiles.length > 0) {
    if (changedFiles.every(f => f.startsWith('docs/') || f.endsWith('.md'))) return 'docs';
    if (changedFiles.every(f => f.startsWith('tests/'))) return 'test';
    if (changedFiles.every(f => f === 'uv.lock' || f === 'pyproject.toml')) return 'dependencies';
  }

  return null;
}

function detectAreas(changedFiles) {
  const areas = new Set();
  for (const file of changedFiles) {
    if (file.includes('repo2graph/walker.py')) areas.add('area/walker');
    if (file.includes('repo2graph/graph.py')) areas.add('area/graph');
    if (file.includes('repo2graph/query.py')) areas.add('area/query');
    if (file.includes('repo2graph/mcp.py') || file === 'server.json') areas.add('area/mcp');
    if (file.includes('repo2graph/cli.py')) areas.add('area/cli');
    if (file.includes('repo2graph/embed.py')) areas.add('area/embed');
    if (file === 'action.yml') areas.add('area/action');
    if (file.startsWith('.github/workflows/')) areas.add('area/workflows');
    if (file.startsWith('tests/')) areas.add('area/tests');
    if (file.startsWith('docs/') || (file.endsWith('.md') && !file.startsWith('.github/'))) areas.add('area/docs');
  }
  return Array.from(areas);
}

function extractIssues(text) {
  if (!text) return [];
  const issueRegex = /(?:close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved)\s+(?:#|gh-)(\d+)/gi;
  const issues = new Set();
  let match;
  while ((match = issueRegex.exec(text)) !== null) {
    issues.add(match[1]);
  }
  return Array.from(issues);
}

function checkAgentsRules(changedFiles) {
  const guidance = [];

  const touchesWalkerOrGraph = changedFiles.some(
    f => f.includes('repo2graph/walker.py') || f.includes('repo2graph/graph.py')
  );
  if (touchesWalkerOrGraph) {
    guidance.push(
      '- **Text Slicing (`AGENTS.md`)**: Use `src.split("\\n")` (or `_lines()`), **never** `splitlines()`. Special Unicode line terminators break tree-sitter line number sync (ISS-22).\n' +
      '- **Windows Git Subprocess Output**: Always pass `-c core.quotepath=false`, capture bytes, and decode with `utf8, surrogateescape` to avoid cp1252 Windows encoding exceptions.'
    );
  }

  const touchesQuery = changedFiles.some(f => f.includes('repo2graph/query.py'));
  if (touchesQuery) {
    guidance.push(
      '- **Two Budget Models (`AGENTS.md`)**: `retrieve()` bounds chunk text only; `pack_context()` bounds entire rendered Markdown. Do not unify them.\n' +
      '- **Traversal Direction**: Always opt out using `ALL_EDGE_DIRS` explicitly for helpers to avoid narrowing existing callers.'
    );
  }

  const touchesMcp = changedFiles.some(f => f.includes('repo2graph/mcp.py'));
  if (touchesMcp) {
    guidance.push(
      '- **Caller-Hostile MCP Arguments (`AGENTS.md`)**: Numeric inputs must be bounded with `_clamp()` against `MCP_MAX_*` constants in handler logic, and pass `exclude_secrets=True` unconditionally.'
    );
  }

  const touchesAction = changedFiles.some(f => f === 'action.yml');
  if (touchesAction) {
    guidance.push(
      '- **Truthiness Seam in `action.yml`**: GitHub expressions compare case-insensitively (`== \'true\'`), while POSIX shell `[ = ]` is case-sensitive. Always case-fold variables with `tr \'[:upper:]\' \'[:lower:]\'` before POSIX shell string comparisons.'
    );
  }

  const touchesExamples = changedFiles.some(f => f.startsWith('examples/'));
  if (touchesExamples) {
    guidance.push(
      '- **Examples (`CONTRIBUTING.md`)**: Never hand-edit `examples/<id>/` artifacts (use `scripts/generate_examples.py`). Never commit `chunks.jsonl` to examples.'
    );
  }

  return guidance;
}

function formatBotComment({
  author,
  baseRef,
  baseSha,
  headRef,
  headSha,
  behindBy,
  aheadBy,
  status,
  mergeable,
  mergeableState,
  linkedIssues,
  linesChanged,
  changedFiles,
  guidance,
  labelsApplied,
}) {
  const shortBaseSha = baseSha ? baseSha.substring(0, 7) : 'unknown';
  const shortHeadSha = headSha ? headSha.substring(0, 7) : 'unknown';

  let alertBlock = '';
  const warnings = [];

  // Outdated check
  if (behindBy > 0) {
    warnings.push(
      `> ⚠️ **Attention @${author}**: Your branch is **behind \`${baseRef}\` by ${behindBy} commit(s)**.\n` +
      `> Please rebase or merge the latest changes from \`${baseRef}\` into \`${headRef}\` to ensure CI runs against current code:\n` +
      `> \`\`\`bash\n` +
      `> git fetch origin\n` +
      `> git checkout ${headRef}\n` +
      `> git rebase origin/${baseRef}\n` +
      `> git push --force-with-lease\n` +
      `> \`\`\``
    );
  }

  // Conflict check
  if (mergeable === false || mergeableState === 'dirty') {
    warnings.push(
      `> 🛑 **Attention @${author}**: **Merge conflicts detected!** This branch cannot be cleanly merged into \`${baseRef}\`.\n` +
      `> Please resolve the conflicts locally and push the updated branch:\n` +
      `> \`\`\`bash\n` +
      `> git fetch origin\n` +
      `> git checkout ${headRef}\n` +
      `> git merge origin/${baseRef}\n` +
      `> # Resolve conflicts in your editor, then:\n` +
      `> git add <resolved-files>\n` +
      `> git commit -m "Merge latest ${baseRef} and resolve conflicts"\n` +
      `> git push\n` +
      `> \`\`\``
    );
  }

  if (warnings.length > 0) {
    alertBlock = warnings.join('\n\n') + '\n\n---\n\n';
  }

  // Base description
  let baseDesc = '';
  if (baseRef === 'main') {
    baseDesc = `Targets \`main\`, the primary release branch for \`repo2graph\`.`;
  } else {
    baseDesc = `Targets \`${baseRef}\` (non-default branch).`;
  }

  const baseStatusDesc =
    behindBy === 0
      ? `Up to date with tip of \`${baseRef}\` (\`${shortBaseSha}\`).`
      : `Diverged: ${aheadBy} commit(s) ahead, ${behindBy} commit(s) behind tip of \`${baseRef}\`.`;

  let linkedIssuesSection = '';
  if (linkedIssues.length > 0) {
    linkedIssuesSection = `**Linked Issues:** ${linkedIssues.map(i => `#${i}`).join(', ')}\n\n`;
  }

  let guidanceSection = '';
  if (guidance.length > 0) {
    guidanceSection =
      `### 💡 Repository Rule Checklist (\`AGENTS.md\`)\n\n` +
      `Based on the files modified in this PR, please keep these critical repository guidelines in mind:\n\n` +
      guidance.join('\n\n') +
      '\n\n---\n\n';
  }

  const labelsList = labelsApplied.map(l => `\`${l}\``).join(' ') || '_none_';

  return (
    `${BOT_MARKER}\n` +
    `## 🤖 \`prod-igy\` PR Inspector\n\n` +
    `Hello @${author}! I inspected this pull request for base alignment, repository rules, and mergeability.\n\n` +
    `${alertBlock}` +
    `### 🎯 Base & Branch Overview\n\n` +
    `- **Target Base**: \`${baseRef}\` (${baseDesc})\n` +
    `- **Branch State**: ${baseStatusDesc}\n` +
    `- **Head Commit**: \`${shortHeadSha}\` (\`${headRef}\`)\n` +
    `- **Changes**: ${changedFiles.length} file(s) modified (${linesChanged} lines changed: +${linesChanged} diff sum)\n` +
    `- **Assigned Labels**: ${labelsList}\n\n` +
    `${linkedIssuesSection}` +
    `${guidanceSection}` +
    `### 📋 Contributor Quick Checklist\n` +
    `- [${behindBy === 0 ? 'x' : ' '}] Branch is up to date with \`${baseRef}\`\n` +
    `- [${mergeable !== false && mergeableState !== 'dirty' ? 'x' : ' '}] No merge conflicts\n` +
    `- [ ] Tests run and passed locally (\`pytest -q\`)\n` +
    `- [ ] Linters passed (\`ruff check .\`)\n\n` +
    `*Automated by \`prod-igy\` for repo2graph.*`
  );
}

async function triagePullRequest({ github, owner, repo, prNumber, core }) {
  core.info(`[prod-igy] Starting inspection for PR #${prNumber}...`);

  // 1. Fetch fresh PR data to ensure mergeable and state are up to date
  const { data: pr } = await github.rest.pulls.get({
    owner,
    repo,
    pull_number: prNumber,
  });

  const author = pr.user.login;
  const baseRef = pr.base.ref;
  const baseSha = pr.base.sha;
  const headRef = pr.head.ref;
  const headSha = pr.head.sha;

  // 2. Fetch list of changed files
  const changedFilesData = await github.paginate(github.rest.pulls.listFiles, {
    owner,
    repo,
    pull_number: prNumber,
    per_page: 100,
  });
  const changedFiles = changedFilesData.map(f => f.filename);

  // 3. Compare head with base branch
  let behindBy = 0;
  let aheadBy = 0;
  let compStatus = 'unknown';

  try {
    const { data: compare } = await github.rest.repos.compareCommits({
      owner,
      repo,
      base: baseRef,
      head: headSha,
    });
    behindBy = compare.behind_by;
    aheadBy = compare.ahead_by;
    compStatus = compare.status;
  } catch (err) {
    core.warning(`Failed to compare commits for PR #${prNumber}: ${err.message}`);
  }

  // 4. Scan comments & description for issues & context
  const prBody = pr.body || '';
  const comments = await github.paginate(github.rest.issues.listComments, {
    owner,
    repo,
    issue_number: prNumber,
  });

  const allText = prBody + '\n' + comments.map(c => c.body || '').join('\n');
  const linkedIssues = extractIssues(allText);

  // 5. Determine labels
  const labelsToAdd = new Set();
  const labelsToRemove = new Set();

  // Type label
  const typeLabel = detectType(pr.title, headRef, changedFiles);
  if (typeLabel) labelsToAdd.add(typeLabel);

  // Size label
  const linesChanged = (pr.additions || 0) + (pr.deletions || 0);
  const sizeLabel = calculateSize(linesChanged);
  labelsToAdd.add(sizeLabel);

  // Clean old size labels if different
  const allSizeLabels = ['size/XS', 'size/S', 'size/M', 'size/L', 'size/XL'];
  for (const s of allSizeLabels) {
    if (s !== sizeLabel) labelsToRemove.add(s);
  }

  // Area labels
  const areaLabels = detectAreas(changedFiles);
  for (const a of areaLabels) labelsToAdd.add(a);

  // Status labels
  if (behindBy > 0) {
    labelsToAdd.add('needs-rebase');
  } else {
    labelsToRemove.add('needs-rebase');
  }

  if (pr.mergeable === false || pr.mergeable_state === 'dirty') {
    labelsToAdd.add('has-conflicts');
  } else {
    labelsToRemove.add('has-conflicts');
  }

  if (!prBody || prBody.trim().length < 20) {
    labelsToAdd.add('needs-description');
  } else {
    labelsToRemove.add('needs-description');
  }

  // 6. Ensure labels exist in repository before applying
  const repoLabels = await github.paginate(github.rest.issues.listLabelsForRepo, {
    owner,
    repo,
  });
  const existingRepoLabelNames = new Set(repoLabels.map(l => l.name.toLowerCase()));

  for (const labelName of labelsToAdd) {
    if (!existingRepoLabelNames.has(labelName.toLowerCase())) {
      const def = LABEL_DEFINITIONS[labelName] || { color: 'cfd3d7', description: 'PR label' };
      try {
        await github.rest.issues.createLabel({
          owner,
          repo,
          name: labelName,
          color: def.color,
          description: def.description,
        });
        existingRepoLabelNames.add(labelName.toLowerCase());
        core.info(`Created missing label: ${labelName}`);
      } catch (err) {
        core.warning(`Could not create label ${labelName}: ${err.message}`);
      }
    }
  }

  // 7. Apply new labels
  const currentPRLabels = (pr.labels || []).map(l => l.name);
  const finalAdd = Array.from(labelsToAdd).filter(l => !currentPRLabels.includes(l));
  if (finalAdd.length > 0) {
    await github.rest.issues.addLabels({
      owner,
      repo,
      issue_number: prNumber,
      labels: finalAdd,
    });
    core.info(`[PR #${prNumber}] Added labels: ${finalAdd.join(', ')}`);
  }

  // Remove stale labels
  for (const rem of labelsToRemove) {
    if (currentPRLabels.includes(rem)) {
      try {
        await github.rest.issues.removeLabel({
          owner,
          repo,
          issue_number: prNumber,
          name: rem,
        });
        core.info(`[PR #${prNumber}] Removed stale label: ${rem}`);
      } catch (err) {
        core.debug(`Label ${rem} removal skipped or failed: ${err.message}`);
      }
    }
  }

  // 8. Guidance from AGENTS.md
  const guidance = checkAgentsRules(changedFiles);

  // 9. Format bot comment
  const commentBody = formatBotComment({
    author,
    baseRef,
    baseSha,
    headRef,
    headSha,
    behindBy,
    aheadBy,
    status: compStatus,
    mergeable: pr.mergeable,
    mergeableState: pr.mergeable_state,
    linkedIssues,
    linesChanged,
    changedFiles,
    guidance,
    labelsApplied: Array.from(labelsToAdd),
  });

  // 10. Post or update comment idempotently
  const existingBotComment = comments.find(c => c.body && c.body.includes(BOT_MARKER));
  if (existingBotComment) {
    await github.rest.issues.updateComment({
      owner,
      repo,
      comment_id: existingBotComment.id,
      body: commentBody,
    });
    core.info(`[PR #${prNumber}] Updated existing prod-igy comment #${existingBotComment.id}`);
  } else {
    await github.rest.issues.createComment({
      owner,
      repo,
      issue_number: prNumber,
      body: commentBody,
    });
    core.info(`[PR #${prNumber}] Created new prod-igy comment`);
  }
}

module.exports = async function run({ github, context, core }) {
  const { owner, repo } = context.repo;
  const eventName = context.eventName;

  if (eventName === 'workflow_dispatch') {
    const rawInput = (context.payload?.inputs?.pr_number || 'all').trim();
    if (rawInput && rawInput.toLowerCase() !== 'all') {
      const prNumber = parseInt(rawInput, 10);
      if (isNaN(prNumber)) {
        core.setFailed(`Invalid pr_number input: ${rawInput}`);
        return;
      }
      core.info(`Running manual inspection on PR #${prNumber}`);
      await triagePullRequest({ github, owner, repo, prNumber, core });
    } else {
      core.info('Running bulk manual sweep on all open pull requests...');
      const openPRs = await github.paginate(github.rest.pulls.list, {
        owner,
        repo,
        state: 'open',
        per_page: 50,
      });
      core.info(`Found ${openPRs.length} open PR(s) to inspect.`);
      for (const pr of openPRs) {
        try {
          await triagePullRequest({ github, owner, repo, prNumber: pr.number, core });
        } catch (err) {
          core.warning(`Error triaging PR #${pr.number}: ${err.message}`);
        }
      }
    }
    return;
  }

  if (eventName === 'issue_comment') {
    const issue = context.payload?.issue;
    if (!issue || !issue.pull_request) {
      core.info('Comment is not on a pull request. Skipping.');
      return;
    }
    await triagePullRequest({ github, owner, repo, prNumber: issue.number, core });
    return;
  }

  // Default: pull_request or pull_request_target
  const prPayload = context.payload?.pull_request;
  if (!prPayload) {
    core.warning('No pull_request payload found in context.');
    return;
  }
  await triagePullRequest({ github, owner, repo, prNumber: prPayload.number, core });
};

// Export helpers for unit testing
module.exports.triagePullRequest = triagePullRequest;
module.exports.calculateSize = calculateSize;
module.exports.detectType = detectType;
module.exports.detectAreas = detectAreas;
module.exports.extractIssues = extractIssues;
module.exports.checkAgentsRules = checkAgentsRules;
module.exports.formatBotComment = formatBotComment;
module.exports.LABEL_DEFINITIONS = LABEL_DEFINITIONS;
module.exports.BOT_MARKER = BOT_MARKER;
