// The neighbourhood of the symbol at a position: the model the view renders.
//
// Two tool calls per cursor move -- `repo_find_symbol` with `path` and `line`
// for the enclosing symbol, `repo_neighbours` with `format: "json"` for what
// is around it -- and the second is skipped while the cursor stays inside the
// same symbol. No `vscode` import.

import type { ToolCaller, ToolResult } from "./mcpClient";
import { isStaleNote, parseJsonReply, ToolOutputError } from "./toolText";

export interface GraphNode {
  node_id: string;
  type?: string;
  name: string;
  qualname: string;
  kind: string;
  path: string;
  start_line: number | null;
  end_line: number | null;
  lang: string;
}

export interface NeighbourRow extends GraphNode {
  edge: string;
  direction: "in" | "out";
  via: string;
  at: string | null;
  confidence: number | null;
  candidate_count: number | null;
  count: number | null;
}

export interface CochangeRow {
  node_id: string;
  path: string;
  count: number;
}

export interface NeighboursPayload {
  node: GraphNode;
  neighbours: NeighbourRow[];
  cochange: CochangeRow[];
  truncated: boolean;
  next_cursor?: string | null;
}

export interface Section {
  edge: string;
  direction: string;
  label: string;
  rows: NeighbourRow[];
}

export interface Neighbourhood {
  node: GraphNode;
  /** Every node enclosing the position, innermost first (the file last). */
  enclosing: GraphNode[];
  sections: Section[];
  cochange: CochangeRow[];
  truncated: boolean;
  nextCursor: string | null;
  notes: string[];
  stale: boolean;
}

export interface ImpactRow {
  node_id: string;
  path: string;
  start_line: number | null;
  end_line: number | null;
  hop: number;
}

export interface Impact {
  node_id: string;
  callers: ImpactRow[];
  subclasses: ImpactRow[];
  importers: ImpactRow[];
  cochange: Array<{ path: string; count: number }>;
  summary: {
    files_affected: number;
    symbols_affected: number;
    max_hop_reached: number;
    truncated: boolean;
  };
  notes: string[];
}

/** A tool answered with `isError: true`. */
export class ToolCallError extends Error {
  constructor(
    readonly tool: string,
    message: string,
  ) {
    super(message);
    this.name = "ToolCallError";
  }
}

// Section labels, in display order. Every edge type the core defines must
// have both directions here (tables.test.ts checks it against generated.ts).
export const SECTION_LABELS: ReadonlyArray<readonly [string, string, string]> = [
  ["CALLS", "in", "Callers"],
  ["CALLS", "out", "Callees"],
  ["INHERITS", "out", "Base classes"],
  ["INHERITS", "in", "Subclasses"],
  ["DEFINES", "in", "Defined in"],
  ["DEFINES", "out", "Defines"],
  ["TESTS", "in", "Tested by"],
  ["TESTS", "out", "Tests"],
  ["IMPORTS", "out", "Imports"],
  ["IMPORTS", "in", "Imported by"],
  ["CO_CHANGE", "out", "Co-changed with"],
  ["CO_CHANGE", "in", "Co-changed with"],
  ["CONTAINS", "in", "Contained in"],
  ["CONTAINS", "out", "Contains"],
  ["CALLS_EXTERNAL", "out", "External calls"],
  ["CALLS_EXTERNAL", "in", "Called from"],
  ["READS", "out", "Reads"],
  ["READS", "in", "Read by"],
  ["WRITES", "out", "Writes"],
  ["WRITES", "in", "Written by"],
  ["REFERENCES", "out", "References"],
  ["REFERENCES", "in", "Referenced by"],
];

/** Group rows into labelled sections, in SECTION_LABELS order; unknown edges last. */
export function groupSections(rows: readonly NeighbourRow[]): Section[] {
  const byKey = new Map<string, Section>();
  const order: string[] = [];
  const labelFor = new Map(SECTION_LABELS.map(([e, d, l]) => [`${e} ${d}`, l] as const));
  for (const row of rows) {
    const label = labelFor.get(`${row.edge} ${row.direction}`) ?? `${row.edge} ${row.direction}`;
    // Both CO_CHANGE directions mean the same thing, so they share a section.
    const key = label;
    let section = byKey.get(key);
    if (!section) {
      section = { edge: row.edge, direction: row.direction, label, rows: [] };
      byKey.set(key, section);
      order.push(key);
    }
    section.rows.push(row);
  }
  const rank = (label: string): number => {
    const i = SECTION_LABELS.findIndex(([, , l]) => l === label);
    return i < 0 ? SECTION_LABELS.length : i;
  };
  return order
    .map((key) => byKey.get(key) as Section)
    .sort((a, b) => rank(a.label) - rank(b.label));
}

function check(tool: string, result: ToolResult): string {
  if (result.isError) {
    throw new ToolCallError(tool, result.text.trim() || `${tool} failed`);
  }
  return result.text;
}

export interface NavigatorOptions {
  /** Rows per `repo_neighbours` page (1-50). */
  limit?: number;
}

export class SymbolNavigator {
  private last: Neighbourhood | undefined;

  constructor(
    private readonly tools: ToolCaller,
    private readonly options: NavigatorOptions = {},
  ) {}

  /** Forget the cached neighbourhood (after a rebuild, or on demand). */
  invalidate(): void {
    this.last = undefined;
  }

  /** Symbols enclosing `line` of repo-relative `path`, innermost first. */
  async enclosing(path: string, line: number): Promise<{ nodes: GraphNode[]; notes: string[] }> {
    const text = check(
      "repo_find_symbol",
      await this.tools.callTool("repo_find_symbol", { path, line, limit: 10 }),
    );
    const { notes, value } = parseJsonReply<GraphNode[]>(text);
    if (!Array.isArray(value)) {
      throw new ToolOutputError("repo_find_symbol did not return an array");
    }
    return { nodes: value, notes };
  }

  /** One page of the neighbourhood of `nodeId`. */
  async neighbours(nodeId: string, cursor = ""): Promise<{ payload: NeighboursPayload; notes: string[] }> {
    const text = check(
      "repo_neighbours",
      await this.tools.callTool("repo_neighbours", {
        node_id: nodeId,
        format: "json",
        cursor,
        limit: Math.max(1, Math.min(50, this.options.limit ?? 50)),
      }),
    );
    const { notes, value } = parseJsonReply<NeighboursPayload>(text);
    if (!value || typeof value !== "object" || !Array.isArray(value.neighbours)) {
      throw new ToolOutputError("repo_neighbours did not return a neighbourhood");
    }
    return { payload: value, notes };
  }

  /**
   * The neighbourhood at a position, or undefined when nothing in the index
   * encloses it (a file that was never indexed).
   */
  async at(path: string, line: number, force = false): Promise<Neighbourhood | undefined> {
    const found = await this.enclosing(path, line);
    const target = found.nodes[0];
    if (!target) {
      return undefined;
    }
    if (!force && this.last && this.last.node.node_id === target.node_id) {
      return { ...this.last, enclosing: found.nodes, notes: found.notes, stale: found.notes.some(isStaleNote) };
    }
    const { payload, notes } = await this.neighbours(target.node_id);
    const allNotes = [...new Set([...found.notes, ...notes])];
    this.last = {
      node: payload.node,
      enclosing: found.nodes,
      sections: groupSections(payload.neighbours),
      cochange: payload.cochange ?? [],
      truncated: payload.truncated,
      nextCursor: payload.next_cursor ?? null,
      notes: allNotes,
      stale: allNotes.some(isStaleNote),
    };
    return this.last;
  }

  /** Append the next page to a neighbourhood. */
  async more(current: Neighbourhood): Promise<Neighbourhood> {
    if (!current.nextCursor) {
      return current;
    }
    const { payload } = await this.neighbours(current.node.node_id, current.nextCursor);
    const rows = [...current.sections.flatMap((s) => s.rows), ...payload.neighbours];
    const next: Neighbourhood = {
      ...current,
      sections: groupSections(rows),
      truncated: payload.truncated,
      nextCursor: payload.next_cursor ?? null,
    };
    if (this.last?.node.node_id === next.node.node_id) {
      this.last = next;
    }
    return next;
  }

  /** What depends on `nodeId` (`repo_blast_radius`). */
  async impact(nodeId: string): Promise<Impact> {
    const text = check(
      "repo_blast_radius",
      await this.tools.callTool("repo_blast_radius", { node_id: nodeId }),
    );
    const { notes, value } = parseJsonReply<Omit<Impact, "notes">>(text);
    if (!value || !Array.isArray(value.callers)) {
      throw new ToolOutputError("repo_blast_radius did not return an impact report");
    }
    return { ...value, notes };
  }
}
