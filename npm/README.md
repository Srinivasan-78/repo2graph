# repo2graph-mcp (npx launcher)

An `npx`-installable launcher for the [repo2graph](https://github.com/Srinivasan-78/repo2graph)
MCP server. This package ships **no server code** — `repo2graph-mcp` is a Python console script
published on PyPI as part of the `repo2graph[mcp]` extra; this package's `bin` entry finds a way to
run that script and hands off to it, so the real implementation stays single-sourced.

## Why this exists

Installation was `uvx` or `pip` only, which is right for Python users but reads as "needs a Python
toolchain" to the share of MCP client users who reach for `npx` first. This package closes that gap
without porting anything: it is a launcher, not a reimplementation.

## Usage

```json
{
  "mcpServers": {
    "repo2graph": {
      "command": "npx",
      "args": ["-y", "repo2graph-mcp", "/path/to/project"]
    }
  }
}
```

Or run it directly:

```bash
npx -y repo2graph-mcp /path/to/project
```

## Resolution order

`bin/repo2graph-mcp.js` tries, in order, and hands off to the first one it finds on `PATH`:

1. **`uvx`** — `uvx --from "repo2graph[mcp]" repo2graph-mcp <args>`. Fetches and runs the server on
   demand; nothing to install ahead of time. This is the path documented as primary in
   [`docs/mcp.md`](../docs/mcp.md), so a working `uvx` gives you the identical behavior either way.
2. **`repo2graph-mcp`** already on `PATH` — an existing `pip install "repo2graph[mcp]"` or a
   virtualenv script, run directly with no extra process in between.
3. **`pipx`** — `pipx run --spec "repo2graph[mcp]" repo2graph-mcp <args>`, pipx's own on-demand-run
   equivalent of `uvx`.
4. **None of the above** — prints the actual install instructions to stderr (never stdout, which
   would corrupt the stdio JSON-RPC stream a real MCP client is reading) and exits non-zero. This
   package never installs a Python toolchain behind your back.

Every argument after `repo2graph-mcp` on the command line is forwarded verbatim to whichever
launcher it resolves to — `/path/to/project`, `--out`, `--http-port`, `--auth-token`, all of it. See
[`docs/mcp.md`](../docs/mcp.md) for the full flag reference.

## What this does not do

- **No bundled Python, no bundled `uv`.** Node has no story for shipping a Python interpreter
  inside an npm package that would not itself be a supply-chain and platform-support liability;
  this launcher finds an existing one instead.
- **No silent install.** If none of `uvx`/`repo2graph-mcp`/`pipx` is found, the failure is loud and
  actionable, not a fallback that quietly runs `pip install` on your behalf.
- **No version pin of its own by default.** `uvx --from "repo2graph[mcp]" repo2graph-mcp` resolves
  the latest compatible release each time it runs, exactly like invoking `uvx` yourself — see
  [`docs/ENTERPRISE_DEPLOYMENT.md`](../docs/ENTERPRISE_DEPLOYMENT.md) if you need a pinned,
  reproducible deployment; pin there, not in this launcher.

## Release story

This package's `version` in `package.json` is kept in lockstep with the `repo2graph` version on
PyPI — both are meant to be published from the same tag, so a `repo2graph-mcp@X.Y.Z` npm release
always pairs with a `repo2graph==X.Y.Z` PyPI release. The launcher itself has no runtime dependency
on that number (the resolution order above always reaches for whatever `uvx`/`pipx` resolves as
current), so the version match is a release-process discipline rather than something the script
checks.

Publishing is a manual `npm publish --provenance` from this directory today (`publishConfig` in
`package.json` already requests provenance attestations, matching the `attestations: true` this
repository's PyPI publish step already sets — see
[`docs/publishing.md`](../docs/publishing.md)); wiring it into the same `publish.yml` tag-triggered
job that ships the PyPI release is tracked as follow-up work rather than done in this change, so
that a bad npm publish cannot block or partially complete the PyPI release it is meant to pair with.

## Local development

```bash
cd npm
node bin/repo2graph-mcp.js --help   # exercises whichever of uvx/repo2graph-mcp/pipx is on PATH
npm pack                             # builds the tarball that would be published
```

There is no build step and no dependency to install — the whole package is `bin/repo2graph-mcp.js`
plus this file.
