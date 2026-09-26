# Design partners: selection criteria and outreach drafts

> ## DRAFTS — NOTHING SENT
>
> No outreach in this file has been sent, and none should be sent by
> automation. Every message goes out individually, from a human account, after
> that human has read the project's current contribution norms.
>
> The drafts below make factual claims — ambiguity percentages, "no network
> calls on the default path", the licence. They are written inside
> [POSITIONING.md §1](../../POSITIONING.md) "What we are *not* claiming", and
> the numbers trace to `docs/limitations.md`. Check both before editing any of
> them: a cold message to a maintainer is the worst possible place to be caught
> overstating.

---

## Rules that are not negotiable

These come first because the rest of the document is useless if they are broken
— a badly run outreach campaign does not merely fail, it makes the project
unwelcome in the communities it needs.

1. **Public channels only.** A project's Discussions board, an issue tracker
   that invites feature conversation, a maintainer's stated contact address in
   `FUNDING.yml` or a README "get in touch" section. **Do not** harvest commit
   emails from git history — those are published for attribution, not for
   outreach, and using them that way is the thing that gets a project blocked.
   Do not DM anyone who has not invited it.
2. **Read the project's rules first, every time.** `CONTRIBUTING.md`, the
   Discussions categories, any `SUPPORT.md`. Several projects explicitly forbid
   tool-promotion issues. If so, that project is not a candidate — full stop,
   not "post it anyway carefully".
3. **Disclose in the first sentence.** Who you are, that you wrote the tool,
   and that you are asking for their time. No "I noticed you might be
   interested in a solution to X" framing that hides the ask.
4. **Never claim familiarity you do not have.** Do not say "I've been following
   your work for years" unless it is true and you can name what you followed.
   Fabricated rapport is transparent and it is the fastest way to be
   dismissed.
5. **One message. No follow-up unless they reply.** A single unanswered message
   is outreach; a second one is spam. Silence is an answer.
6. **Lead with the limitations.** Counter-intuitive and correct: a maintainer
   evaluating a code-analysis tool is already thinking "this will not handle
   our dynamic dispatch". Saying it first is the only way to get past it, and it
   is the claim this project is built on.
7. **Cap it.** Five to eight conversations. A design partner relationship is
   hours of someone else's time; more than eight and you cannot honour it,
   which is worse than not asking.
8. **No incentives.** No paid placement, no "we'll feature you", no free
   licences (it is MIT anyway). An endorsement obtained with a benefit is not
   evidence and must not be published as one.

---

## What a design partner is for

Not promotion. The tool has specific, known weaknesses, and the useful partner
is one whose codebase **stresses** them:

| What we need to learn | The codebase that teaches it |
|---|---|
| How bad is name-based ambiguity in practice? | large, many same-named methods, no type annotations |
| Does the graph survive dependency injection? | Java/Spring, .NET, or a Python app with a DI container |
| Does `--exclude-group generated` catch real generated code? | protobuf/gRPC, OpenAPI codegen, a monorepo with `packages/*/node_modules` |
| Is the MCP output shaped right for an agent? | a team already using Claude Code or Cursor daily |
| Does macro-heavy C actually produce usable output? | an embedded or kernel-adjacent C codebase |
| Do the freshness signals match how people actually rebuild? | a repo that commits its index, or builds it in CI |

A partner who says "this was useless on our codebase, here is why" is worth more
than one who says it was great. Make that explicit when you ask — it changes
what they tell you.

## Selection criteria

Score a candidate before writing anything. Three or more of:

- [ ] **Stresses a known weakness** (the table above). This is the main one.
- [ ] **Public repository**, so any resulting case study is reproducible at a
      pinned commit.
- [ ] **Has an inviting public channel** — Discussions enabled, or a
      `CONTRIBUTING.md` that welcomes tooling conversation.
- [ ] **Already uses coding agents**, visible from their own docs or issues.
      Someone who has never used an MCP client will spend their time on setup
      rather than on the thing you need evaluated.
- [ ] **Active** — commits in the last 90 days. A dormant project cannot
      evaluate anything.
- [ ] **Not a direct competitor.** Asking the maintainer of a rival code-graph
      tool to evaluate yours is an imposition dressed as collaboration.

Disqualifiers, regardless of score: any project whose contribution norms forbid
this kind of contact; any project where the only available contact is a personal
email you found in git history; any project currently dealing with a public
incident or a maintainer burnout thread.

## Finding candidates

I am not naming specific maintainers here, deliberately — I cannot verify any
individual's current availability, interest, or contact preferences, and a list
of names in a repository reads as a target list. Search by *characteristic*
instead, and evaluate each hit against the criteria above:

| Where | What to look for |
|---|---|
| The MCP ecosystem — server registries, `awesome-mcp-servers` lists, the MCP Discord/Discussions | maintainers already building MCP servers; they will evaluate the tool surface, not the concept |
| GitHub search: repos with `.cursor/rules/` or a committed `CLAUDE.md` | teams demonstrably using agents on their own code, and willing to say so publicly |
| Language communities where the weakness is worst — Spring/Java, .NET, protobuf-heavy Go | the DI and generated-code questions |
| Projects whose `CONTRIBUTING.md` mentions onboarding difficulty | the onboarding persona, self-identified |
| Conference talks / blog posts about "understanding a large codebase" | authors who have already thought about the problem and will push back usefully |

The `examples/` directory already indexes Django, Kubernetes, TensorFlow, VS Code
and the Linux kernel at pinned commits. Those are **evidence**, not partners:
nobody from those projects has been contacted, and the READMEs there should not
imply otherwise.

---

## Draft A — public Discussions post

For a project with Discussions enabled and a relevant category. Preferred over
any private channel: it is visible, it can be ignored without awkwardness, and
other people can weigh in.

**Title:** `Would a call-graph index be useful here? (author asking, honest limits inside)`

```
I wrote an open-source tool (repo2graph) that parses a repo with tree-sitter
into a call/import/inheritance graph and serves cited source to coding agents
over MCP. I am looking for 5–8 codebases to evaluate it honestly, and <project>
looks like it would stress it in a way my own benchmarks do not — <one specific,
verifiable reason: e.g. "the handler registry in <dir> is exactly the
string-keyed dispatch my call resolution cannot follow">.

What I am asking for: 30–45 minutes, once. Run it on your checkout, tell me
where the output is wrong or useless. That is the whole ask — no ongoing
commitment, and I am not asking you to adopt anything.

What you would likely find wrong, so you can decide without installing it:

- Call resolution is name-based, not type-based. Across five benchmark repos,
  4.6%–21.3% of CALLS edges are ambiguous. Ambiguous ones are marked with a
  confidence, but they are still ambiguous.
- Dynamic dispatch, reflection and DI containers produce no edges at all. An
  absent edge is not proof of an absent call.
- Generated code is indexed exactly like hand-written code unless you exclude
  it explicitly.

It runs locally, makes no network calls on the default path, and loads no
model. MIT.

If it is useful to try:  uvx repo2graph demo   (bundled example, no repo of
yours needed)

If this is the wrong place to ask, say so and I will not follow up here.
```

That last line is not politeness, it is the point: it gives the maintainer a
zero-cost way to decline, which is what makes the ask reasonable.

## Draft B — issue on a repo that invites tooling discussion

Shorter. An issue is more intrusive than a Discussion, so only where
`CONTRIBUTING.md` indicates it is welcome.

```
**This is not a bug report** — close it freely if it is off-topic here.

I maintain repo2graph, a tree-sitter code-graph tool that serves cited source
to coding agents over MCP. I am looking for a handful of codebases to test it
honestly against, and <project> would stress the case my benchmarks miss:
<specific reason>.

Ask: run it once, tell me where the output is wrong.

  uvx repo2graph demo                      # see the output shape, no setup
  uvx repo2graph build . -o .r2g           # then on your checkout

Known limits up front: name-based call resolution (4.6%–21.3% of CALLS edges
ambiguous across five benchmark repos); no edges for dynamic dispatch,
reflection or DI. Local-only, no network on the default path, MIT.

Repo: https://github.com/Srinivasan-78/repo2graph
```

## Draft C — email, only to a stated public contact

Only where the project publishes a contact address for this purpose. Subject
lines matter more than the body; this one states the ask.

**Subject:** `30 min: would you tell me where my code-graph tool fails on <project>?`

```
Hi,

I'm <name>, author of repo2graph — an MIT tree-sitter tool that builds a
call/import/inheritance graph of a repository and serves cited source to
coding agents over MCP. I got your address from <where, exactly>.

I'm looking for 5–8 codebases to evaluate it against, and I think <project>
would break it in an instructive way: <specific, verifiable reason>.

The ask is 30–45 minutes, once: run it, tell me what's wrong. I am not asking
you to adopt it or endorse it, and if a case study came out of it you'd
approve every word or it wouldn't be published.

What I already know is weak, so you can decide without installing anything:
call resolution is name-based, so 4.6%–21.3% of call edges are ambiguous
across my benchmark repos; dynamic dispatch, reflection and DI containers
produce no edges at all.

  uvx repo2graph demo

runs against a bundled example, no repository of yours involved.

If you're not interested, no reply needed — I won't follow up.

<name>
<repo link>
```

## Draft D — a peer already building MCP servers

Different ask: they will evaluate the *tool surface*, not the concept, and they
are the only group who will tell you whether the output shape is right.

```
You've built <their MCP server>, so you'll have opinions on this that I can't
get elsewhere.

repo2graph exposes six read-only MCP tools over a tree-sitter code graph.
The design decisions I'd most like torn apart:

1. Every numeric argument is clamped in the handler rather than validated at
   the edge, so direct callers, dispatch() and the stdio server all inherit
   the bound. Right call, or hiding errors from the model?
2. Tool results are markdown strings, not structured JSON. Citations are
   `path:line` in prose. Agents seem to handle it, but I've not tested widely.
3. Edges the tool is unsure of are marked inline:
   `AMBIGUOUS 0.5 of 3 candidates`. Does that help a model or confuse it?
4. Retrieval tools exclude secret-shaped paths unconditionally, with no flag
   to disable — a human on the CLI can choose to see a .env, an agent tool
   returning one felt different. Too paternalistic?

30 minutes of your reactions would be worth more to me than any number of
users. Happy to return the favour on yours.

https://github.com/Srinivasan-78/repo2graph
```

Draft D is the highest-value outreach in this file and the least likely to
annoy anyone, because it asks for expertise rather than attention — and the four
questions are real open design questions, not rhetorical ones.

---

## Tracking

Keep this out of the repository — a public file recording who was contacted and
whether they replied is a privacy problem regardless of intent. A private
spreadsheet with: project, channel used, date sent, their stated norms, reply,
outcome. Fields that matter:

- **Their norms** — so a future contributor does not re-approach a project that
  asked not to be.
- **Declined / no reply** — treated identically. Do not contact again.

## If someone says yes

- Their time is the scarce resource. Send the one-command path
  (`uvx repo2graph demo`), not a setup guide.
- Ask for the failure first: "where did it give you something wrong or
  useless?" — before "was it helpful?". The second question invites politeness.
- Anything published needs their explicit approval of the exact wording, and a
  private repo means [the case-study template's](case-study-template.md)
  privacy checklist applies.
- If they found nothing wrong, that is a finding about the test, not a
  validation. Ask what they tried.
