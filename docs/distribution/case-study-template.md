# Before/after case-study template

Copy this file, fill it in, delete the guidance blocks. One case study per
file, under `docs/case-studies/<slug>.md`.

**The rule that makes these worth publishing:** every number is measured, and
every measurement says how it was taken. A case study with an unsourced "10×
faster" is worth less than one with a modest number and a reproducible method,
because the second one survives a reader who tries it.

If you cannot measure something, say so. "We did not measure this" is a
publishable sentence. An invented number is not.

---

## Guidance: what makes a usable case study

**Pick a task with a definite end state.** "Onboarding felt easier" cannot be
checked. "Found every caller of `Session.execute` before changing its
signature" can — and the finish line is *did the change break anything you
missed*.

**Record the before-state before you start.** Retrospective "it used to take a
day" numbers are guesses. If the before-state is already gone, mark the field
`not measured (retrospective estimate)` and use it only as narrative, never as
a headline.

**Show the limitation you hit.** Every real use hits one of the eight in
[POSITIONING.md §5](../../POSITIONING.md). A case study with an empty
limitations section reads as either a toy task or an edited one, and a technical
reader discounts the whole thing.

---

## Template

```markdown
# <Task>, in <repository>

**One line:** <what was being done, and the outcome — no adjectives>

| | |
|---|---|
| Repository | <name, public URL or "private, <language>, ~<N> files"> |
| Commit | <sha, or "not recorded"> |
| repo2graph version | <x.y.z> |
| Task | <the concrete thing being attempted> |
| Who | <role, not name, unless they asked to be named> |
| Date | <YYYY-MM-DD> |

---

## 1. The old approach

<What was actually done before. Commands, tools, and the order they were tried
in. Include the dead ends — the wasted passes are usually most of the cost, and
a case study that shows only the happy path is not describing the before-state.>

**Measured:**

| Metric | Value | How it was measured |
|---|---|---|
| Wall-clock | <e.g. 2h 40m> | <e.g. "timestamps on the branch's first and last commit"> |
| Files opened | <N> | <e.g. "editor recent-files list, deduplicated"> |
| Agent context consumed | <N tokens> | <e.g. "sum of tool-result tokens in the session transcript"> |
| Result | <e.g. "missed 2 of 9 callers; caught in review"> | <how you know> |

> **Guidance:** the "how it was measured" column is not optional. A metric
> without it is an anecdote wearing a table.

## 2. The repo2graph approach

<The exact commands. Copy-pasteable, with the flags actually used — including
the ones that mattered, like `--exclude-group generated` or `--git-history`.>

```bash
repo2graph build . -o .r2g --git-history 500
repo2graph rag "<the real question>" -o .r2g
```

**Measured:** same table, same rules.

| Metric | Value | How it was measured |
|---|---|---|
| Index build | <N s> | `build`'s own JSON summary |
| Wall-clock | <N m> | <> |
| Files opened | <N> | <> |
| Agent context consumed | <N tokens> | <> |
| Result | <> | <> |

## 3. Evidence

<The part that makes this checkable. Not a screenshot of a conclusion — the
artifacts a reader can reproduce or inspect.>

**Commands a reader can run against the same public commit:**

```bash
repo2graph github <owner/repo> -o /tmp/cs --ref <sha>
repo2graph rag "<the same question>" -o /tmp/cs
```

**What came back** (trimmed, with the citation headers intact — never
paraphrase a citation):

```
### [cite: <path>:<start>-<end>] `<symbol>` (seed)
<the real lines>
```

**The specific finding:**

| Claim | Evidence | How to check it |
|---|---|---|
| <e.g. "9 callers, not the 7 grep found"> | `repo2graph explain node "sym:<path>::<name>" -o .r2g` | <the two grep missed, and why — e.g. aliased import> |

> **Guidance — the evidence rules:**
> - Paste real output. If you must trim, mark the trim.
> - Keep `[cite: path:line-line]` markers verbatim. They are the product.
> - If a claim rests on an edge, give its `confidence`. An ambiguous edge
>   supporting a headline finding needs saying:
>   `repo2graph explain edge <src> <dst> -o .r2g` prints it.
> - Prefer a public repository at a pinned commit. A private-repo case study
>   cannot be checked by anyone, which caps what it can honestly claim.

## 4. Time and context saved

| | Before | After | Delta |
|---|---|---|---|
| Wall-clock | <> | <> | <> |
| Files opened | <> | <> | <> |
| Agent context (tokens) | <> | <> | <> |

**Method:** <state it once, plainly. e.g. "Both runs done by the same engineer,
same week, on two comparable refactors in the same subsystem. Not a controlled
experiment — n=1, and the second run benefited from familiarity with the
subsystem gained in the first.">

> **Guidance:** that last clause is mandatory when it is true. Learning effect
> is the single largest confound in a before/after on the *same* codebase, and
> a reader who has run a case study before will look for whether you named it.
> Naming it costs you a weaker number and buys you a credible one.
>
> **Token counting:** count tool-result tokens from the session transcript, not
> a model's self-report. State the tokeniser. If you used repo2graph's own
> estimate, say so — it is an estimate, and `pack_context`'s
> `tokens_used` documents itself as one.
>
> **Do not extrapolate.** "Saved 2 hours on this task" is a finding. "Would
> save 40 hours a quarter" is a projection, and belongs in a sales deck, not a
> case study.

## 5. Limitations hit

<Which of the eight applied, and what it cost. Be specific.>

| Limitation | How it showed up | What we did |
|---|---|---|
| <e.g. Dynamic dispatch> | <e.g. "the plugin registry's `handlers[name]()` produced no CALLS edge, so 1 of the 9 callers was invisible"> | <e.g. "found it by grepping the registry keys — repo2graph did not help here"> |

**What we would still do by hand:** <>

**Where it did not help at all:** <state this even if the answer is "nothing".
A case study that claims the tool helped with everything is not describing a
real task.>

---

## Reviewer checklist

- [ ] Every number has a "how it was measured".
- [ ] No number is extrapolated beyond the task measured.
- [ ] Learning effect named, if before and after were the same codebase.
- [ ] Citations pasted verbatim, trims marked.
- [ ] Limitations section is non-empty and specific.
- [ ] Nothing claims completeness of the call graph.
- [ ] If the repo is private, no path or identifier in the study leaks
      structure the owner would not publish. (`repo2graph bug-report` has the
      same problem and solves it by redacting paths by default — apply the same
      judgement here.)
- [ ] The subject consented to how they are described.
```

---

## Which case studies to write first

Ordered by how much a skeptical reader learns from them:

1. **Blast radius before a signature change**, on a public repo at a pinned
   commit. Fully reproducible, and the finish line is objective: did the change
   break something the before-approach missed.
2. **Onboarding to an unfamiliar subsystem**, measured in files opened and
   agent context rather than feeling.
3. **A case where it did not help.** Most valuable of the three and the one
   nobody writes. A macro-heavy C codebase, or a DI-heavy Java service where
   the edges land on interfaces — published honestly, this buys more
   credibility than the first two combined, and it pre-empts the objection a
   reader was already forming.
