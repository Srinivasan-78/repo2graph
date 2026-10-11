// A tiny repository, indexed by the real `repo2graph build`, for the tests
// that talk to a real `repo2graph-mcp`.
//
// The executables come from $REPO2GRAPH_BIN (a directory), else the
// repository's own .venv, else PATH. A missing install fails the tests with
// that explanation rather than skipping them.

import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

const WIN = process.platform === "win32";

function binDir(): string | undefined {
  if (process.env.REPO2GRAPH_BIN) {
    return process.env.REPO2GRAPH_BIN;
  }
  // out/test -> editors/vscode -> repository root
  const venv = path.resolve(__dirname, "..", "..", "..", "..", ".venv", WIN ? "Scripts" : "bin");
  return fs.existsSync(venv) ? venv : undefined;
}

export function executable(name: "repo2graph" | "repo2graph-mcp"): string {
  const dir = binDir();
  if (!dir) {
    return name;
  }
  const file = path.join(dir, WIN ? `${name}.exe` : name);
  return fs.existsSync(file) ? file : name;
}

export const SHAPES = `"""Shapes."""


class Shape:
    def area(self):
        return 0


class Square(Shape):
    def __init__(self, side):
        self.side = side

    def area(self):
        return helper(self.side) * self.side


def helper(x):
    return x


def describe(shape):
    return f"area={shape.area()}"


def main():
    sq = Square(2)
    print(describe(sq))
    return helper(3)
`;

export const APP = `from .shapes import main, helper


def run():
    main()
    return helper(1)
`;

export interface Fixture {
  root: string;
  repo: string;
  index: string;
  cleanup(): void;
}

/** Write the repository and build its index with the CLI. */
export function buildFixture(): Fixture {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "r2g-vscode-"));
  const repo = path.join(root, "repo");
  fs.mkdirSync(path.join(repo, "pkg"), { recursive: true });
  fs.writeFileSync(path.join(repo, "pyproject.toml"), "[project]\nname = 'fixture'\n");
  fs.writeFileSync(path.join(repo, "pkg", "__init__.py"), "");
  fs.writeFileSync(path.join(repo, "pkg", "shapes.py"), SHAPES);
  fs.writeFileSync(path.join(repo, "pkg", "app.py"), APP);
  const index = path.join(repo, ".r2g");
  try {
    execFileSync(executable("repo2graph"), ["build", repo, "-o", index], { stdio: "pipe" });
  } catch (error) {
    throw new Error(
      `could not build the fixture index with ${executable("repo2graph")}: ${error}. ` +
        'Install repo2graph (pip install -e ".[mcp]") or point REPO2GRAPH_BIN at its bin directory.',
    );
  }
  return {
    root,
    repo,
    index,
    cleanup: () => fs.rmSync(root, { recursive: true, force: true }),
  };
}
