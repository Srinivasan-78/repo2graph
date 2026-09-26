# Distribution assets

Working drafts for taking repo2graph outward: a demo recording, a case-study
format, launch copy, and design-partner outreach.

**Nothing here has been published or sent.** Each file carries its own approval
gate; `launch-posts.md` and `design-partners.md` are explicitly drafts awaiting
a human.

| File | What it is | State |
|---|---|---|
| [demo-script.md](demo-script.md) | 75-second demo script, shot by shot, plus the recording plan and a pre-record claim checklist | ready to record |
| [case-study-template.md](case-study-template.md) | Before/after template: old approach, repo2graph approach, evidence, time and context saved, limitations hit | ready to use |
| [launch-posts.md](launch-posts.md) | Show HN, Lobsters, Reddit, long-form and social drafts, with per-community rules and a sequencing plan | **drafts — needs approval** |
| [design-partners.md](design-partners.md) | Selection criteria, where to look, and four outreach drafts | **drafts — nothing sent** |

Client integration guides are user-facing docs and live elsewhere:
[Claude Code](../integrations/claude-code.md) (first-supported),
[Cursor](../integrations/cursor.md) (second).

---

## The constraint all four share

Every claim traces to a committed artifact. Concretely:

| Claim type | Source of truth |
|---|---|
| Build times, file/node/edge counts | `benchmarks/results.json` |
| Per-repository graph stats | `examples/*/stats.json` |
| Ambiguity percentages | `docs/limitations.md` |
| What must never be claimed | [POSITIONING.md §1](../../POSITIONING.md) |
| The eight limitations any long surface must carry | [POSITIONING.md §5](../../POSITIONING.md) |

This is not caution for its own sake. The product claim is *trustworthiness* —
answers you can open and verify, edges that admit when they are guesses. A
launch post that overstates the call graph's completeness does not just risk a
correction; it contradicts the thing being sold. The limitations are the pitch.

## Before publishing anything

- [ ] Claims checked against POSITIONING.md §1.
- [ ] Every number carries its source file, on screen or in text.
- [ ] Limitations stated, not buried.
- [ ] No output from a private repository in any asset.
- [ ] No named individual described as interested, or endorsing, without
      their written agreement to that wording.
- [ ] Published by a human, under their own name, one community at a time.
