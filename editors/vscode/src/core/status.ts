// Index status and staleness, from `repo2graph index-status --json`, plus the
// argument lists for every CLI and server invocation the extension makes.
// No `vscode` import.

export type Freshness = "current" | "stale" | "unknown";

/** The subset of `repo2graph index-status --json` the extension reads. */
export interface IndexStatusReport {
  index: {
    path: string;
    created_at: string | null;
    age_seconds: number | null;
    size_human?: string;
    tool_version?: string | null;
  };
  source: { path: string; commit: string | null; short_commit: string | null; branch?: string | null };
  contents: { files_parsed: number; symbols: number; nodes: number; edges: number };
  freshness: {
    status: Freshness;
    reasons: string[];
    notes: string[];
    counts: { added: number; removed: number; modified: number };
  };
}

export interface StatusSummary {
  status: Freshness;
  age: string;
  builtAt: string | null;
  commit: string | null;
  reasons: string[];
  notes: string[];
  /** One line for a notification. */
  headline: string;
}

/** "42s ago", "5m ago", "3h ago", "2d ago" -- the same buckets as `status.format_age`. */
export function formatAge(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
    return "unknown";
  }
  const s = Math.max(0, Math.floor(seconds));
  if (s < 90) return `${s}s ago`;
  if (s < 5400) return `${Math.floor(s / 60)}m ago`;
  if (s < 172800) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function parseStatus(stdout: string): IndexStatusReport {
  const report = JSON.parse(stdout) as IndexStatusReport;
  if (!report || typeof report !== "object" || !report.index || !report.freshness) {
    throw new Error("repo2graph index-status --json returned an unexpected shape");
  }
  return report;
}

export function summarizeStatus(report: IndexStatusReport): StatusSummary {
  const status = report.freshness.status;
  const age = formatAge(report.index.age_seconds);
  const commit = report.source.short_commit ?? (report.source.commit ? report.source.commit.slice(0, 10) : null);
  const contents = report.contents;
  const what = `${contents.symbols} symbols in ${contents.files_parsed} files`;
  const at = commit ? ` at ${commit}` : "";
  let headline: string;
  if (status === "current") {
    headline = `repo2graph index is current: built ${age}${at}, ${what}.`;
  } else if (status === "stale") {
    headline = `repo2graph index is stale (${report.freshness.reasons.join("; ") || "the tree moved"}): built ${age}${at}.`;
  } else {
    headline = `repo2graph index built ${age}${at}, ${what}; freshness could not be checked.`;
  }
  return {
    status,
    age,
    builtAt: report.index.created_at,
    commit,
    reasons: report.freshness.reasons,
    notes: report.freshness.notes,
    headline,
  };
}

/** `repo2graph build <repo> -o <index> [extra] [--incremental]`. */
export function buildArgs(
  repoRoot: string,
  indexDir: string,
  extra: readonly string[],
  incremental: boolean,
): string[] {
  return ["build", repoRoot, "-o", indexDir, ...extra, ...(incremental ? ["--incremental"] : [])];
}

/** `repo2graph index-status -o <index> -r <repo> --json`. */
export function statusArgs(indexDir: string, repoRoot: string, json = true): string[] {
  return ["index-status", "-o", indexDir, "-r", repoRoot, ...(json ? ["--json"] : [])];
}

/** `repo2graph map -o <index>`: draw graph.html from an existing index. */
export function mapArgs(indexDir: string): string[] {
  return ["map", "-o", indexDir];
}

/**
 * `repo2graph-mcp <repo> --out <index> --no-auto-build --staleness-ttl <s>`.
 * Never auto-builds: building is an explicit action with its own progress,
 * not a side effect of moving the cursor.
 */
export function serverArgs(repoRoot: string, indexDir: string, stalenessTtl: number): string[] {
  const ttl = Math.max(0, Math.min(300, Number.isFinite(stalenessTtl) ? stalenessTtl : 0));
  return [repoRoot, "--out", indexDir, "--no-auto-build", "--staleness-ttl", String(ttl)];
}
