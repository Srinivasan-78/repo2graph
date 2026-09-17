#!/usr/bin/env python3
"""Drive a real stdio MCP session against the installed console script.

This is the check that would have caught the Glama build failure. Glama's build
ran `uv sync` -- base dependencies only -- and then `repo2graph-mcp`, which
exits 1 because the MCP SDK lives behind the `mcp` extra. The server was right;
the install was wrong. Nothing in the test suite noticed, because the suite
imports `repo2graph.mcp` directly and never installs the package *without* an
extra or starts the real console script.

So this deliberately does not import anything from repo2graph. It spawns the
installed executable, speaks JSON-RPC over its pipes the way `mcp-proxy` does,
and checks what a third-party host would actually see.

Usage:
    python scripts/mcp_roundtrip.py [path-to-repo2graph-mcp]
"""
import json
import os
import subprocess
import sys
import tempfile

# Long enough for a cold tree-sitter import plus indexing a three-file repo,
# short enough that a wedged server fails the job instead of hanging it.
TIMEOUT = 120

SAMPLE = {
    "pkg/__init__.py": "VERSION = '1.0'\n",
    "pkg/service.py": (
        "ROUTES = {'/health': 'ok'}\n\n\n"
        "def handle(request):\n"
        "    return ROUTES.get(request)\n"
    ),
}


def sample_repo() -> str:
    """Materialise a tiny repository for the server to index."""
    root = tempfile.mkdtemp(prefix="r2g-roundtrip-")
    for rel, text in SAMPLE.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf8", newline="\n") as fh:
            fh.write(text)
    return root


def default_executable() -> str:
    """Where `uv sync` / `pip install -e .` puts the console script."""
    binary = "Scripts" if os.name == "nt" else "bin"
    name = "repo2graph-mcp.exe" if os.name == "nt" else "repo2graph-mcp"
    local = os.path.join(".venv", binary, name)
    return local if os.path.exists(local) else "repo2graph-mcp"


class Session:
    """One stdio JSON-RPC session against the server process."""

    def __init__(self, executable: str, repo: str):
        self.proc = subprocess.Popen([executable, repo], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE)
        self._id = 0

    def send(self, method: str, params: dict | None = None,
             notify: bool = False) -> dict | None:
        """Send one frame; return the parsed response unless it is a notification."""
        frame: dict = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            self._id += 1
            frame["id"] = self._id
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(frame) + "\n").encode())
        self.proc.stdin.flush()
        if notify:
            return None
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        if not line:
            raise SystemExit(
                f"the server closed stdout without answering {method!r}; "
                f"exit code {self.proc.poll()}")
        return json.loads(line)

    def close(self) -> None:
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
            self.proc.terminate()
            self.proc.wait(timeout=30)
        except Exception:
            self.proc.kill()


def main(argv: list[str]) -> int:
    """Run the round trip; return 0 on success."""
    executable = argv[1] if len(argv) > 1 else default_executable()
    repo = sample_repo()
    print(f"executable: {executable}\nrepo: {repo}")

    session = Session(executable, repo)
    try:
        reply = session.send("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "roundtrip", "version": "1"}})
        info = reply["result"]["serverInfo"]
        print("serverInfo:", info)
        if info["name"] != "repo2graph":
            print(f"::error::serverInfo.name is {info['name']!r}")
            return 1

        # The server must report *its* version. Left unset, the MCP SDK fills
        # this in from its own package, so clients are told repo2graph is
        # whatever release of `mcp` happens to be installed.
        from importlib.metadata import version
        installed = version("repo2graph")
        if info.get("version") != installed:
            print(f"::error::serverInfo.version is {info.get('version')!r} but "
                  f"the installed package is {installed!r} -- the SDK version "
                  f"is leaking through")
            return 1

        session.send("notifications/initialized", notify=True)

        names = [t["name"] for t in session.send("tools/list")["result"]["tools"]]
        print("tools:", names)
        missing = {"repo_map", "repo_search", "repo_neighbours"} - set(names)
        if missing:
            print(f"::error::tools/list is missing {sorted(missing)}")
            return 1

        # repo_map triggers the on-demand build, so this covers the one slow
        # path a host's first call actually takes.
        text = session.send("tools/call", {
            "name": "repo_map", "arguments": {}})["result"]["content"][0]["text"]
        if not text.strip():
            print("::error::repo_map returned nothing")
            return 1
        print("repo_map:", text.splitlines()[0][:70])

        found = session.send("tools/call", {
            "name": "repo_search",
            "arguments": {"query": "how is a request handled"},
        })["result"]["content"][0]["text"]
        if not found.strip():
            print("::error::repo_search returned nothing")
            return 1
        print("repo_search: ok")
    finally:
        session.close()

    print("stdio round trip ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
