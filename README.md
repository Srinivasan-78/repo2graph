<!--
  @authormark v1 -- do not remove (authorship watermark)⁠​‌‌​‌​​​​​‌‌​​​‌​‌‌‌‌​‌​​‌​​​​‌​​‌‌‌​​​​​‌‌​‌‌‌‌​‌​‌​​​‌​‌​​‌​‌​​‌‌​​‌‌‌​‌​‌‌​‌​​‌‌​​​​‌​‌‌‌​‌​‌​‌‌​‌‌‌‌​‌​‌‌​​​​​‌‌​​​​​‌‌‌​‌‌​​‌‌‌‌​​​​‌‌​​‌​‌​​‌‌​​​‌​‌‌​​‌‌‌​‌​‌​‌​‌​‌​​‌‌‌​⁠
  Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
  Author: https://github.com/Srinivasan-78
  SPDX-License-Identifier: MIT
  Fingerprint: AMK1.h1zBpoQJgZauoX0vxe1gUN
-->
# repo2graph

repo2graph reads a folder full of code and draws you a map of it.

![The whole map of a project: 300 dots and the arrows between them](docs/images/graph-overview.png)

*One project, drawn by `repo2graph`. Each dot is a folder, file, function or library. Each arrow is
a real connection found in the code.*

## The idea

Imagine you get handed a big box of Lego that someone else already built things with. You want to
know what connects to what. You could look at every brick one at a time, or someone could hand you
a map.

Code is like that box. A project has hundreds of files, and the files use each other in ways you
cannot see by looking at one file at a time.

repo2graph makes the map. On the map:

- Every **thing** is a dot. A folder is a dot. A file is a dot. A function (a small named piece of
  code that does a job) is a dot. We call these dots **nodes**.
- Every **connection** is an arrow. "This file is inside that folder." "This function uses that
  function." "This file borrows code from that library." We call these arrows **edges**.

Dots joined by arrows are called a **graph**. That is the whole idea.

## Why a map helps

If you search a project for the word "login", you get every file that happens to say "login",
including comments and typos.

The map is better, because it knows which function actually does the login work, and it also knows
which functions call it and which functions it calls. So you get the real answer plus its
neighbours.

That matters most when a chatbot or AI helper is reading the code for you. Giving it the right
piece of code plus the pieces around it is usually what it was missing.

## How it works, in three steps

```mermaid
flowchart LR
    A[your code] --> B[tree-sitter<br/>reads the code]
    B --> C[graph<br/>dots + arrows]
    C --> D[graph.html<br/>the picture]
    C --> E[overview.md<br/>the words]
    C --> F[chunks.jsonl<br/>pieces for an AI]
    C --> G[graph.graphml / graph.cypher<br/>other tools, Neo4j]
```

1. **It reads the code.** It uses a tool called tree-sitter, the same one code editors use to
   colour your code. So it understands real code structure instead of guessing from words. It
   needs no setup and works on a project it has never seen.
2. **It builds the map.** Folders, files, functions, classes and imports become dots. "contains",
   "defines", "calls", "imports", "inherits" become arrows.
3. **It cuts the code into small pieces.** Roughly one piece per function or class. Each piece gets
   a few lines at the top saying who calls this function, what it calls, and what its description
   says. Those little pieces are what you feed to an AI when you want it to answer questions about
   the code.

## Install it

You need Python 3.10 or newer.

```bash
git clone https://github.com/Srinivasan-78/repo2graph
cd repo2graph
python3 -m venv .venv
.venv/bin/pip install -e .
```

Now the command lives at `.venv/bin/repo2graph`. If you run `source .venv/bin/activate` first, you
can type just `repo2graph`.

## Use it

### Step 1: make the map

```bash
repo2graph build /path/to/your/project -o .r2g --git-history 200
```

That is it. It walks the project, reads it, and puts everything in a folder called `.r2g`. A
medium project takes seconds. A very big one takes a minute or two.

`--git-history 200` is optional. It looks at the last 200 saves (commits) in the project's history
and adds links between files that keep getting changed together. Those links are a good clue about
which files secretly depend on each other.

### Step 2: look at the map

```bash
open .r2g/human/graph.html   # the picture
cat .r2g/human/overview.md   # the same thing written out in words
repo2graph stats -o .r2g     # how many dots, arrows and functions there are
```

`graph.html` is one single file. No internet needed, nothing to install. Open it in a browser and
you get the picture: drag to move around, scroll to zoom, drag a dot to pin it in place, click a
dot to see what that function looks like and everything it is connected to. The panel on the side
lets you hide kinds of dots and arrows, and the search box jumps to a name.

Zoom in and every dot is named, so you can read the real call paths:

![Zoomed into the map: named functions, files and libraries joined by arrows](docs/images/graph-zoom.png)

The side panel counts what is on screen and lets you switch each kind of dot and arrow on or off:

![Side panel with search box, node kinds and relationship kinds, each with a count](docs/images/graph-sidebar.png)

By default the picture shows the 300 busiest dots, and hides calls that go out to other people's
code, because those triple the number of arrows and tell you little about your own project. Tick
`external` and `CALLS_EXTERNAL` in the side panel to show them.

Want a simpler picture? Redraw it with fewer dots:

```bash
repo2graph map -o .r2g --viz-nodes 80
```

### Step 3: ask it questions

A search tool is built in. No AI account, no password, no extra setup:

```bash
repo2graph query "how does routing match a path" -o .r2g -k 8 --hops 1
```

It finds the best matching pieces of code, then follows the arrows one step out, so the functions
around each answer come along too.

Want the answer as data instead of text?

```bash
repo2graph query "auth middleware" -o .r2g --json | jq '.[].path'
```

### Step 4: map a project you do not have on your computer

```bash
repo2graph github psf/requests -o out/requests --git-history 200
```

It downloads the project to a temporary spot, builds the map, tidies up after itself, and writes an
extra file, `agent/index.json`, saying exactly which project and which version it read. You can
write `gh` instead of `github`, and full web links work too.

For a private project, pass `--token` or set `$GH_TOKEN` or `$GITHUB_TOKEN`. Use `--ref` to pick a
branch or tag, and `--keep-clone DIR` if you want to keep the downloaded copy.

### Running the tests

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

## What you get in the `.r2g` folder

The output is split in two, because people and programs want different things.

### `human/` — for you

| File | What it is |
|---|---|
| `overview.md` | the map written out in words. Read this first. |
| `graph.html` | the picture. Open it in a browser. |
| `graph.graphml` | the map in a format drawing programs understand (yEd, Gephi). It opens already laid out, so it does not look like a hairball. |

### `agent/` — for programs and AI helpers

| File | What it is |
|---|---|
| `overview.md` | the same words as above, so an AI can read the whole project summary cheaply |
| `manifest.json` | the instruction sheet: what every other file is, what the dots and arrows mean, how names are built, and where the code starts. A program needs nothing else to make sense of this folder. |
| `chunks.jsonl` | the small pieces of code, each with its "who calls me" header |
| `nodes.jsonl` | one line of data per dot |
| `edges.jsonl` | one line of data per arrow |
| `graph.cypher` | a script that loads the map into a graph database (Neo4j or Memgraph). Running it twice is safe. |
| `stats.json` | the counts: dots, arrows, functions, reading errors, starting points |
| `index.json` | which project and version was read. Only written by `repo2graph github`. |

`graph.graphml` also works in the Python libraries NetworkX and igraph. It lives under `human/`
because the layout it carries is there for a person looking at a picture.

Useful extras for `build`: `--include '**/*.py'` and `--exclude '**/test/**'` to pick files,
`--max-files N` to stop early, `--formats jsonl,cypher` to skip outputs you do not want,
`--viz-nodes N` to change how many dots the picture draws, `--no-chunks` to skip the code pieces,
and `--jobs N` to say how many files to read at once (default: one per processor core, up to 8).
More cores means a faster build, and the result is exactly the same either way.

## Where the code starts

Some functions are called by other functions. Some are called by nobody, because they are the door
into the project: the commands you type, the handlers that answer web requests, the tests.

repo2graph marks those with `entrypoint: true`. If you want to follow how the program actually
runs, start at one of those and follow the `CALLS` arrows forward.

For the 200 busiest ones it also counts `reach`: how many other functions that door can eventually
get to. A big `reach` means a main path through the project. `agent/manifest.json` lists the top 25.

## Giving the map to an AI

`chunks.jsonl` is the file you hand to an AI system. Each piece already carries its neighbours in
the header, which is what makes the answers good.

If you use a vector database, keep each piece's `node_id`. That is the handle that lets you jump
back onto the map after a search.

The pattern that works well: search for a few pieces, then follow the arrows one step to pull in
the code around them, and put `overview.md` at the top as background.

```python
import json
from repo2graph.query import Index

chunks = [json.loads(l) for l in open(".r2g/agent/chunks.jsonl")]
# store each chunk's "text" in your search system, and keep "node_id" and "path" alongside it

idx = Index(".r2g")                       # the map plus the pieces, no AI account needed
hits = ["sym:app/auth.py::login"]         # node_ids your search returned
for node_id, edge_type, direction, src in idx.expand(hits, hops=1):
    for extra in idx.by_node.get(node_id, [])[:1]:
        print(edge_type, direction, extra["path"], extra["qualname"])
```

### Loading it into Neo4j

```bash
repo2graph build /path/to/project -o .r2g --formats jsonl,cypher
cypher-shell -u neo4j -p password -f .r2g/agent/graph.cypher
```

## What the dots and arrows mean

**Dots (nodes):**

| Kind | Colour on the picture | Meaning |
|---|---|---|
| `repo` | 🔴 red | the project itself |
| `dir` | 🟤 tan | a folder |
| `file` | 🟠 orange | a file |
| `symbol` | 🔵 blue | a function, method, class, struct, trait, interface, type or module |
| `module` | 🟢 green | something the project borrows that is not one of its own files |
| `external` | 🩷 pink | a name the project calls that could not be found anywhere in the project |

A dot is drawn bigger when more arrows touch it, so the busiest parts of the project stand out
without you looking for them.

**Arrows (edges):**

| Kind | Meaning |
|---|---|
| `CONTAINS` | project holds folder, folder holds file |
| `DEFINES` | a file creates a function or class, or one function creates another inside it |
| `IMPORTS` | a file borrows from another file (`internal: true`) or from an outside library |
| `CALLS` | one function uses another. Carries `count` and `confidence`. |
| `CALLS_EXTERNAL` | a function uses something from outside the project |
| `INHERITS` | a class is built on top of another class |
| `CO_CHANGE` | two files keep getting edited together (needs `--git-history`, 3 times or more) |

A small corner of a real map looks like this:

```mermaid
flowchart LR
    R((repo)) -->|CONTAINS| D((app))
    D -->|CONTAINS| F((auth.py))
    F -->|DEFINES| L((login))
    F -->|IMPORTS| M((requests))
    L -->|CALLS| H((hash_password))
    L -->|CALLS_EXTERNAL| G((get))
    F -.->|CO_CHANGE| F2((routes.py))
```

Names on the map are built the same way every time, so you can write one yourself:
`file:pkg/mod.py`, `sym:pkg/mod.py::Class.method`, `module:requests`, `dir:pkg`.

## What one piece of code looks like

One piece per function or class, cut at about 4000 characters with 8 lines of overlap so nothing
gets lost at the seam. Files also get a piece for whatever code no function claimed, and documents
and settings files get one piece each.

Every piece starts with a few lines describing its neighbourhood:

```
# file: repo2graph/graph.py
# function: resolve_import  (lines 66-99, python)
# called by: repo2graph/graph.py::build
# calls: repo2graph/graph.py::path_index
# calls (outside the repo): Path, replace, str, list, startswith, sub, append
# doc: Map an import target to an in-repo file path when possible.
def resolve_import(...):
    ...
```

Each piece carries these fields: `id`, `node_id`, `type`, `kind`, `path`, `lang`, `name`,
`qualname`, `start_line`, `end_line`, `entrypoint`, `callers`, `callees`, `callees_external`,
`text`.

`callees` lists functions inside the project, written as `path::qualname`. `callees_external` lists
plain names from outside it. If a call could not be pinned to one place, the header says so, like
`helper (confidence 0.5)`, so nobody treats a guess as a fact.

## Languages it understands

Python, JavaScript, TypeScript and TSX, Go, Rust, Java, Ruby, C, C++, C#, PHP, Kotlin, Swift, Scala
and Bash get the full treatment: functions, classes and calls.

Files in any other language still appear on the map as files in their folders, so nothing goes
missing. Teaching it a new language means adding one entry to `LANG_CFG` in `repo2graph/langs.py`.

## Where it guesses

The map is very good, but it is not perfect. Worth knowing before you trust it:

- **It matches calls by name, not by type.** If two functions share a name, repo2graph draws up to
  5 possible arrows and marks each one `1/n` sure. A function defined in the same file wins ties.
  If you need certainty, keep only the arrows where `confidence` is `1.0`.
- **It works out imports by path, one language at a time.** Python packages and relative imports,
  JavaScript and TypeScript relative paths (including `.js` standing in for `.ts`), Go through
  `go.mod`, Java package folders, C and C++ include names. Anything it cannot place becomes an
  outside `module` dot.
- **Some files are skipped:** pictures and other non-text files, anything bigger than 1.5 MB, and
  the usual vendor and build folders. If the project is a git checkout, `.gitignore` is respected.
- **No arrow does not prove no call.** Code that decides while running which function to call is
  invisible to a reader like this one.

## Doing it automatically with GitHub Actions

### Map any project from the Actions tab

`.github/workflows/index-repo.yml` is a button you press. Type a project name, get a map back. It
downloads the project, builds the map, prints the summary into the job page, and uploads
`graph-<owner>__<repo>` as a file you can download. Set `publish_release: true` and it also
attaches a zip to a GitHub Release.

The download includes `graph.html`, so opening that one file gives you the picture with nothing
installed.

Inputs: `repo`, `ref`, `git_history`, `formats`, `exclude`, `publish_release`. For a private
project, add a `TARGET_REPO_TOKEN` secret that can read it. Otherwise the job's own token is used.

All from the terminal:

```bash
gh workflow run index-repo.yml -f repo=psf/requests
gh run watch
gh run download --name graph-psf__requests --dir ./graph
```

### Keep a fresh map next to your own code

repo2graph is published on the GitHub Marketplace, so it is one step in any workflow:

```yaml
- uses: actions/checkout@v4
  with: { fetch-depth: 0 }   # full history, so CO_CHANGE edges are meaningful
- uses: Srinivasan-78/repo2graph@v1
  with:
    path: .              # or: repo: some-org/other-repo
    git-history: "500"
    artifact-name: repo-graph
```

`@v1` follows every 1.x release. Pin an exact version (`@v1.0.0`) if you would rather upgrade by
hand.

#### Inputs

| Input | Default | What it does |
| --- | --- | --- |
| `repo` | `""` | Map a different project: `owner/repo` or a GitHub URL. Leave blank to map the checked-out one. |
| `path` | `.` | Folder in the workspace to map, used when `repo` is blank. |
| `ref` | `""` | Branch or tag to map, used with `repo`. Blank means the default branch. |
| `out` | `.r2g` | Where the map is written. |
| `formats` | `jsonl,graphml,cypher,overview,html` | Which files to write. Drop the ones you do not need to save time. |
| `git-history` | `0` | Commits to read for CO_CHANGE arrows. `0` skips it. Needs `fetch-depth: 0`. |
| `include` | `""` | Space-separated globs to keep, e.g. `"src/**"`. |
| `exclude` | `""` | Space-separated globs to skip, e.g. `"**/test/** vendor/**"`. |
| `artifact-name` | `repo-graph` | Upload the map under this name. Blank uploads nothing. |
| `commit-branch` | `""` | Also force-push the map to this orphan branch. Blank pushes nothing. |
| `token` | `""` | Token that can read `repo` when the target is private. |
| `version` | `git+…@v1` | Version spec passed to pip. Only used if the action folder has no source next to it. |

#### Outputs

| Output | What it holds |
| --- | --- |
| `out` | The output folder: `human/` (`overview.md`, `graph.html`, …) and `agent/` (`chunks.jsonl`, …). |
| `nodes` | How many dots the map has. |
| `edges` | How many arrows. |
| `chunks` | How many code pieces were cut. |

The action also writes the first 40 lines of `overview.md` into the job summary page, so the map
shows up in the run without downloading anything.

#### Permissions

Reading is enough for the default setup. `commit-branch` pushes a branch, so that one needs write:

```yaml
permissions:
  contents: write
```

#### A whole workflow

`.github/workflows/self-index.yml` is the copy this project runs on itself, on every push to
`main` and once a week:

```yaml
name: Index this repository
on:
  push:
    branches: [main]
  schedule:
    - cron: "0 4 * * 1"

permissions:
  contents: write

jobs:
  index:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: Srinivasan-78/repo2graph@v1
        with:
          path: .
          git-history: "500"
          artifact-name: repo-graph
          commit-branch: graph   # drop this line to only publish an artifact
```

With `commit-branch: graph`, an AI pipeline can always grab an up-to-date copy of the code pieces
with one request:

```bash
curl -sL https://raw.githubusercontent.com/Srinivasan-78/repo2graph/graph/agent/chunks.jsonl -o chunks.jsonl
```

## Licence

MIT. See [LICENSE](LICENSE).
