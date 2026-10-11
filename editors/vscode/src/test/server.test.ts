// The data layer against a real `repo2graph-mcp`, serving an index the real
// CLI built: the same two processes the extension runs.

import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { after, before, describe, test } from "node:test";
import { McpClient, McpClosedError } from "../core/mcpClient";
import { SymbolNavigator, ToolCallError } from "../core/neighbourhood";
import { run } from "../core/cli";
import { indexPresent } from "../core/paths";
import { parseStatus, serverArgs, statusArgs, summarizeStatus } from "../core/status";
import { buildFixture, executable, type Fixture } from "./fixture";

const HELPER = "sym:pkg/shapes.py::helper";
const SQUARE_AREA = "sym:pkg/shapes.py::Square.area";
const SQUARE = "sym:pkg/shapes.py::Square";

describe("against a real repo2graph-mcp", () => {
  let fx: Fixture;
  let client: McpClient;
  let nav: SymbolNavigator;

  before(async () => {
    fx = buildFixture();
    assert.ok(indexPresent(fx.index));
    client = await McpClient.start({
      command: executable("repo2graph-mcp"),
      args: serverArgs(fx.repo, fx.index, 10),
      cwd: fx.repo,
    });
    nav = new SymbolNavigator(client);
  });

  after(() => {
    client?.dispose();
    fx?.cleanup();
  });

  test("the handshake reports the repo2graph server", () => {
    assert.equal(client.serverInfo.name, "repo2graph");
    assert.ok(client.protocolVersion, "a negotiated protocol version");
  });

  test("a position resolves to the enclosing symbols, innermost first", async () => {
    const { nodes } = await nav.enclosing("pkg/shapes.py", 14);
    assert.deepEqual(
      nodes.map((n) => n.node_id),
      [SQUARE_AREA, SQUARE, "file:pkg/shapes.py"],
    );
    assert.equal(nodes[0].start_line, 13);
    assert.equal(nodes[0].end_line, 14);
  });

  test("the cursor on a function shows its callers and callees with file and line", async () => {
    const model = await nav.at("pkg/shapes.py", 26); // inside main()
    assert.ok(model);
    assert.equal(model.node.node_id, "sym:pkg/shapes.py::main");
    const callees = model.sections.find((s) => s.label === "Callees");
    assert.ok(callees, "a Callees section");
    const helper = callees.rows.find((r) => r.node_id === HELPER);
    assert.ok(helper, "main calls helper");
    assert.equal(helper.path, "pkg/shapes.py");
    assert.equal(helper.start_line, 17);
    const callers = model.sections.find((s) => s.label === "Callers");
    assert.ok(callers?.rows.some((r) => r.node_id === "sym:pkg/app.py::run" && r.start_line === 4));
    assert.ok(model.sections.some((s) => s.label === "Defined in"));
  });

  test("base classes are their own section", async () => {
    const model = await nav.at("pkg/shapes.py", 9); // class Square(Shape)
    assert.ok(model);
    const bases = model.sections.find((s) => s.label === "Base classes");
    assert.deepEqual(bases?.rows.map((r) => r.node_id), ["sym:pkg/shapes.py::Shape"]);
  });

  test("a line outside every symbol falls back to the file's neighbourhood", async () => {
    const model = await nav.at("pkg/app.py", 1);
    assert.ok(model);
    assert.equal(model.node.node_id, "file:pkg/app.py");
    assert.ok(model.sections.some((s) => s.label === "Imports"));
  });

  test("an unindexed file is undefined, not an error", async () => {
    assert.equal(await nav.at("pkg/nope.py", 3), undefined);
  });

  test("impact lists callers by hop", async () => {
    const impact = await nav.impact(HELPER);
    const hops = new Map(impact.callers.map((c) => [c.node_id, c.hop]));
    assert.equal(hops.get("sym:pkg/app.py::run"), 1);
    assert.equal(hops.get("sym:pkg/shapes.py::main"), 1);
    assert.ok(impact.summary.files_affected >= 2);
  });

  test("a tool error is a ToolCallError carrying the server's sentence", async () => {
    await assert.rejects(nav.neighbours("sym:does/not/exist.py::nope"), (error: unknown) => {
      assert.ok(error instanceof ToolCallError);
      assert.match(error.message, /node not found/);
      return true;
    });
  });

  test("a cursor move round-trips in well under 100 ms on a warm server", async () => {
    // Warm up, then time the two calls a cursor move makes when it changes symbol.
    await nav.at("pkg/shapes.py", 18, true);
    const lines = [26, 14, 18, 22, 5, 10, 27, 13];
    const samples: number[] = [];
    for (let round = 0; round < 5; round++) {
      for (const line of lines) {
        const started = process.hrtime.bigint();
        const model = await nav.at("pkg/shapes.py", line, true);
        samples.push(Number(process.hrtime.bigint() - started) / 1e6);
        assert.ok(model);
      }
    }
    samples.sort((a, b) => a - b);
    const median = samples[Math.floor(samples.length / 2)];
    const p95 = samples[Math.floor(samples.length * 0.95)];
    console.log(
      `cursor-move round trip (repo_find_symbol + repo_neighbours), ${samples.length} samples: ` +
        `median ${median.toFixed(1)} ms, p95 ${p95.toFixed(1)} ms, max ${samples[samples.length - 1].toFixed(1)} ms`,
    );
    // Generous: CI runners are slow and noisy. The measured number is printed above.
    assert.ok(median < 100, `median ${median} ms`);
  });

  test("moving within the same symbol skips the neighbours call", async () => {
    const calls: string[] = [];
    const spying = new SymbolNavigator({
      callTool: (name, args) => {
        calls.push(name);
        return client.callTool(name, args);
      },
    });
    await spying.at("pkg/shapes.py", 25);
    await spying.at("pkg/shapes.py", 27);
    assert.deepEqual(calls, ["repo_find_symbol", "repo_neighbours", "repo_find_symbol"]);
  });

  test("index-status from the CLI summarises freshness, and notices an edit", async () => {
    const cli = executable("repo2graph");
    const fresh = await run(cli, statusArgs(fx.index, fx.repo));
    assert.equal(fresh.code, 0, fresh.stderr);
    const summary = summarizeStatus(parseStatus(fresh.stdout));
    assert.equal(summary.status, "current");
    assert.match(summary.headline, /current/);

    const file = path.join(fx.repo, "pkg", "app.py");
    fs.appendFileSync(file, "\n# edited after the build\n");
    const future = new Date(Date.now() + 5000);
    fs.utimesSync(file, future, future);
    const stale = summarizeStatus(parseStatus((await run(cli, statusArgs(fx.index, fx.repo))).stdout));
    assert.equal(stale.status, "stale");
    assert.ok(stale.reasons.some((r) => /modified/.test(r)), stale.reasons.join("; "));
  });
});

describe("server lifecycle", () => {
  test("a server with no index exits, and the client says why", async () => {
    const fx = buildFixture();
    try {
      const missing = path.join(fx.root, "no-index-here");
      await assert.rejects(
        McpClient.start({
          command: executable("repo2graph-mcp"),
          args: serverArgs(fx.repo, missing, 0),
          cwd: fx.repo,
          initTimeoutMs: 60_000,
        }),
        (error: unknown) => {
          assert.ok(error instanceof McpClosedError);
          assert.match(error.message, /no repo2graph index/);
          return true;
        },
      );
    } finally {
      fx.cleanup();
    }
  });

  test("a missing executable fails to start with a clear error", async () => {
    await assert.rejects(
      McpClient.start({ command: "repo2graph-mcp-definitely-not-installed", args: [] }),
      (error: unknown) => error instanceof McpClosedError && /could not start/.test(error.message),
    );
  });
});
