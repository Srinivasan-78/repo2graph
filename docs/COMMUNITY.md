# Community

Where to ask what, and how the Discussions categories are meant to be used.

Conduct expectations are in [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) (Contributor Covenant 2.1)
and apply identically to issues, pull requests and discussion threads.

---

## Where does this go?

| You have… | Goes to |
|---|---|
| A reproducible defect, with a command and an error | **Issue** → Bug report |
| A wrong or missing `CALLS` / `IMPORTS` / `INHERITS` edge | **Issue** → Incorrect or missing graph edge |
| A request for a language repo2graph doesn't parse deeply | **Issue** → Language / parser support |
| A concrete proposal with an acceptance criterion | **Issue** → Feature proposal |
| "How do I…?" | **Discussions → Q&A** |
| "This isn't working and I'm not sure whether it's me" | **Discussions → Support** |
| A half-formed direction, not yet a proposal | **Discussions → Ideas** |
| A graph of something interesting, or an integration you built | **Discussions → Show and tell** |
| An undisclosed vulnerability | **[Private advisory](https://github.com/Srinivasan-78/repo2graph/security/advisories/new)** — never a public issue |

The distinction that matters most is the last row, and the one between **Support** and **Bug
report**: a bug report asserts the software is wrong, which means it needs a reproduction. Support
asks whether it is wrong. Starting in Support costs nothing and a thread that turns out to be a
defect gets an issue opened from it, carrying the diagnosis that the thread already did.

---

## Categories

Four are in use. Three exist; **Support does not yet exist and has to be created by hand** — see
below.

| Category | Format | Purpose |
|---|---|---|
| **Q&A** | Answerable | "How do I…" — usage, configuration, understanding output. Marking an answer is what makes these worth having; an unanswered Q&A thread is the same as a closed door. |
| **Support** | Answerable | "Is this broken, or is it me?" — setup, MCP client wiring, retrieval results that look wrong. The triage step before a bug report. |
| **Ideas** | Open discussion | Directions, not proposals. No acceptance criterion required. Ideas that firm up graduate to a Feature proposal issue. |
| **Show and tell** | Open discussion | Graphs of interesting repositories, agent workflows, integrations. This is the most useful category for deciding what to build next, and the one most likely to stay empty without prompting. |

`Announcements`, `General` and `Polls` are GitHub defaults. `General` overlaps Q&A and Ideas and is
the category things land in when the chooser is unclear — worth watching, and worth removing if it
collects strays.

### Creating the Support category

**There is no API for this.** The GitHub GraphQL schema has no `createDiscussionCategory` mutation
(verified: `Field 'createDiscussionCategory' doesn't exist on type 'Mutation'`), so discussion
categories are web-UI only and cannot be scripted, committed, or reviewed in a pull request.

To create it:

1. **Settings → Discussions → Categories → New category** — or
   <https://github.com/Srinivasan-78/repo2graph/discussions/categories>.
2. Name: `Support`. Emoji: 🛟. Format: **Question / Answer** (so threads can be marked answered).
3. Description:
   > Something isn't working and you're not sure it's a bug. Setup, MCP client configuration,
   > unexpected retrieval results. If it turns out to be a defect, we'll open an issue from the
   > thread.
4. Then point the "Help" contact link in
   [`.github/ISSUE_TEMPLATE/config.yml`](../.github/ISSUE_TEMPLATE/config.yml) at
   `/discussions/categories/support`. It currently points at Q&A so the issue chooser cannot 404
   before the category exists; there is a comment in the file saying so.

### Descriptions worth updating while you are in there

The three existing categories still carry GitHub's defaults, which say nothing about this project:

| Category | Today | Proposed |
|---|---|---|
| Q&A | "Ask the community for help" | "How do I…? Usage, configuration, and making sense of what repo2graph returned. Answers get marked, so the next person finds them." |
| Ideas | "Share ideas for new features" | "Directions, not proposals. Half-formed is fine — no acceptance criterion needed here. Ideas that firm up become feature proposals." |
| Show and tell | "Show off something you've made" | "Graphs of repositories worth looking at, agent workflows, editor and CI integrations. What you build here decides what gets built next." |

---

## Answering

- **Mark the answer.** An answerable thread with no accepted answer is only useful to whoever was
  in it.
- **If a Support thread turns out to be a defect, open the issue yourself** and link both ways
  rather than asking the reporter to refile. They have already done the diagnosis.
- **If the answer is in the docs, say where and why it was hard to find.** A question that the docs
  technically answered is a documentation bug; file it as one.
- **The most common Support answer is index staleness.** The index is a snapshot and nothing
  watches the filesystem — an edited file keeps being described by the old graph until a rebuild.
  It is worth checking first, every time. See
  [docs/limitations.md#stale-indexes](limitations.md#stale-indexes).

---

## Contributing

Setup, tests, and the PR flow: **[.github/CONTRIBUTING.md](../.github/CONTRIBUTING.md)**.
Starter tasks with acceptance criteria and code pointers:
**[docs/good-first-issues.md](good-first-issues.md)**.
How issues are classified: **[docs/TRIAGE.md](TRIAGE.md)**.
