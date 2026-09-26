# Demo video: script and recording plan

**Target: 75 seconds.** Range 60–90s. Below 60 there is no room to show the
graph hop, which is the only thing a viewer cannot get from grep; above 90 the
completion rate on an embedded README video falls off.

**One claim to land:** *every answer comes back as source you can open, and the
tool tells you when it is guessing.* Everything else is supporting detail.

Every number and every line of output below is reproducible from this
repository at the commit you record on. Nothing is mocked. If a take does not
produce the output in the script, the script is wrong — fix it here, do not
retouch the recording.

---

## Script

Timings are cumulative. Voiceover is optional — the cut should read without
sound, because most embedded plays are muted. Where a claim matters, it goes
on screen as a caption, not only in narration.

### 0:00–0:07 — the problem

> **Caption:** An agent dropped into an unfamiliar repo greps, pulls in three
> whole files, and edits the wrong one.

**Visual:** split screen. Left: a terminal running
`grep -rn "authenticate" .` scrolling past ~40 hits. Right: the same question
typed into an agent, which answers confidently with a function name that does
not exist in the repo.

> The second one is worse, because you cannot tell which just happened.

**Why this shot:** it establishes the failure repo2graph addresses without
naming a competitor. Do not caption the right-hand pane with a product name.

### 0:07–0:18 — zero setup

**Type, do not paste:**

```bash
uvx repo2graph demo
```

**On screen:** the real output.

```
1/3  Writing the bundled demo repository to /tmp/r2g-demo-a1b2c3
     9 files: README.md, app/__init__.py, app/auth.py, app/billing.py, ...

2/3  Building the graph (no network, no API key, no config file)
     8 files parsed -> 67 nodes, 117 edges, 43 chunks
```

> **Caption:** No repo of your own. No API key. No config file.

**Why this shot:** it removes the "I'd have to set something up" objection in
eleven seconds, and it is the one command a viewer can run while the video is
still playing.

### 0:18–0:38 — the graph hop

Let the demo scroll to question 5 and hold on the first cited block.

```
  --- 5/5  Trace an order request from route to persistence.

      [cite: app/auth.py:27-37]
      | # function: require_token  (lines 27-37, python)
      | # called by: app/routes.py::create_order, app/routes.py::get_order,
      |              app/routes.py::refund_order,
      |              tests/test_orders.py::test_require_token_rejects_a_missing_header
      | # calls: app/auth.py::parse_token, app/auth.py::lookup_principal
```

> **Caption:** `called by` and `calls` are edges, not text matches.

> That is the hop grep cannot do. One symbol in; its callers and callees come
> back, each with a file and a line.

**Hold for a beat on the `called by` line.** This is the single most important
frame in the video. If the viewer only remembers one thing, it should be that
the answer arrived with its own provenance attached.

### 0:38–0:55 — your own code

```bash
cd ~/work/your-project
repo2graph build . -o .r2g
repo2graph rag "how does routing match a path" -o .r2g
```

**On screen:** the build's JSON summary, then the pack scrolling with
`### [cite: path:start-end]` headers visible. Do not narrate over the
citations — let them scroll.

> **Caption:** 5,629 files in 34 seconds (Django). Source: `benchmarks/results.json`.

**Why the caption cites its source:** the number is real and the viewer can
check it. Put the file path on screen. A benchmark without a provenance line
reads as marketing.

Then, briefly:

```bash
open .r2g/human/graph.html
```

**Visual:** two seconds of pan-and-zoom on the map. No more. The map is a side
artifact, and dwelling on it invites "so it's a visualiser?" — which is the
positioning this project explicitly rejects.

### 0:55–1:08 — into the agent

```bash
claude mcp add repo2graph -- uvx --from "repo2graph[mcp]" repo2graph-mcp .
```

**Visual:** an agent calling `repo_neighbours` and getting back:

```
- CALLS in: `check_index_freshness` (repo2graph/doctor.py:1051) [sym:...]  -- at repo2graph/doctor.py:1075
- CALLS out: `ResultCache.get` (repo2graph/cache.py:128) [sym:...]  -- at repo2graph/status.py:124, AMBIGUOUS 0.5 of 3 candidates
```

> **Caption:** It marks its own guesses.

> That second edge is a `.get()` on a dictionary that happened to match three
> methods named `get`. repo2graph does not know which one runs, and says so.

**Why this shot is in a 75-second video:** it is the differentiator that
survives contact with a skeptical audience. Anyone can claim citations. Showing
the tool undermine its own confidence — on camera, using its own real output —
is the part a competitor's demo will not have.

### 1:08–1:15 — close

> **Caption, held:**
> Name-based call resolution. An absent edge is not proof of an absent call.
> `docs/limitations.md`

```
pip install repo2graph      github.com/Srinivasan-78/repo2graph
```

**Do not end on the tagline.** Ending on the limitation is the whole brand: the
claim of the product is trustworthiness, and a demo that ends on a boast
contradicts it.

---

## Recording plan

### Before you record

- [ ] `git status` is clean, and you are on a tagged release, not a feature
      branch. The branch name appears in `index-status` output.
- [ ] `repo2graph --version` matches the version you will name in the caption.
- [ ] A throwaway shell with **no history, no aliases, no custom prompt**, and
      `PS1='$ '`. A personalised prompt dates the video and leaks your username.
- [ ] `env | grep -iE 'key|token|secret'` returns nothing in the recording
      shell. `rag --answer` is not in this script; make sure a stray key cannot
      make it into a frame anyway.
- [ ] The "your own project" repo is **public**, or a repo you own and are
      willing to show every visible path of. Frame-by-frame, a private
      monorepo's directory names are a leak.
- [ ] Terminal: 100×30, 16–18pt, high-contrast light theme. Light reads better
      than dark when embedded in a README that may render either way.
- [ ] `/tmp` is empty enough that the demo's scratch path is short on screen.

### Capture

| Setting | Value | Why |
|---|---|---|
| Tool | `asciinema` for terminal, screen capture only for `graph.html` and the agent | asciinema output is text: re-renderable at any size, diffable, and cannot accidentally capture a notification |
| Resolution | 1920×1080, 30fps | the README embed downscales; 60fps buys nothing for terminal text |
| Audio | record separately, or none | a muted autoplay is the common case; do not make sound load-bearing |
| Cursor | visible | viewers use it to follow typing |

Type commands at a human speed. Do not paste — a command that appears
instantly reads as an edit, and the viewer stops trusting the take.

### Post

- Cut the dead time while the build runs, and **caption the cut**
  (`— 34s build, trimmed —`). Silently trimming a wait is the one edit that
  turns an honest demo into a misleading one.
- Burn in captions. Do not rely on a player's subtitle track.
- Export a 3-second silent GIF of the 0:18–0:38 graph-hop shot for the README
  and for social cards. That shot standing alone is the strongest still asset
  the project has.
- Keep the raw `.cast` file. When output changes in a later release, re-render
  rather than re-shoot.

### Do not

- Do not speed up the terminal to make the build look faster than it is. The
  real number is good and it is published.
- Do not record `rag --answer`. It sends repository source to a third-party
  provider, and a video is a bad place to normalise that. If a future cut needs
  it, show the `_disclose()` stderr line that names the provider first.
- Do not show a repository you do not own.
- Do not stage a failure by the competitor tool. The grep shot must be a real
  grep of a real repo.

### Accessibility

- Captions burned in, and a text transcript in the PR description.
- Do not use red/green as the only distinction anywhere in the graph shot.
- Terminal contrast ratio ≥ 7:1 for body text.

---

## Reusable cuts from one recording

One session should yield four assets:

| Asset | Source | Where it goes |
|---|---|---|
| 75s full demo | whole session | README top, landing page, launch posts |
| 3s graph-hop GIF | 0:18–0:38 | social cards, `docs/images/` |
| 10s "zero setup" clip | 0:07–0:18 | integration guides, quickstart |
| 8s "marks its guesses" clip | 0:55–1:08 | the strongest clip for a technical audience; lead with it on Hacker News |

---

## Review gate

This script makes claims on camera. Before recording, check each against
[POSITIONING.md §1](../../POSITIONING.md) "What we are *not* claiming":

- [ ] Nothing in the cut says or implies "understands your codebase".
- [ ] Nothing implies the call graph is complete.
- [ ] Nothing positions the tool as replacing grep (`query` runs BM25 first).
- [ ] Nothing says "AI-powered". The default path makes no network call.
- [ ] Every number on screen carries its source file.
- [ ] The closing frame is a limitation, not a boast.
