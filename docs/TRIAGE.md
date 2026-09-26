# Issue triage and labels

How an issue gets classified here, what each label means, and what has to be true before an issue
is workable. The most recent full pass is
[docs/issue-triage-2026-09-25.md](issue-triage-2026-09-25.md).

---

## 1. What makes an issue workable

An issue is ready when a contributor who has never seen the codebase can start on it without
asking a question first. Concretely, three things:

1. **The code.** `path/to/file.py:line`, with the relevant lines quoted. "The parser mishandles X"
   is a lead; `parse.py:_callee_name` splits on `("::", ".", "->")` and PHP uses `\` is an issue.
2. **The trigger.** A command, an input or a failing test. For a missing capability rather than a
   defect: the call you wanted to make and what happens instead.
3. **One acceptance criterion that can be false.** "Audit every `except Exception`" cannot be
   checked; "no bare `except Exception` remains in `repo2graph/mcp.py`, and `ruff` enforces it"
   can.

A fourth, specific to this repository:

4. **Check it against [`AGENTS.md`](../AGENTS.md) first.** Several documented invariants look like
   bugs and are not. The two budget models in `query.py` are deliberately different and must not be
   unified; `splitlines()` is banned in favour of `split("\n")` for a reason that recurs; the
   40-character `file_residual` threshold in `chunks.py:185` is intended; call resolution is
   name-based on purpose. An issue proposing to change one of these needs to argue against the
   recorded reason, not merely notice the behaviour.

Issues that assert a defect without (1) get `needs-reproduction`. That is not a rejection — it
names the next step, which is establishing whether the problem is real.

---

## 2. Triage buckets

Every open issue belongs to exactly one **type** bucket.

| Bucket | Label | Means |
|---|---|---|
| Bug | `bug` | Behaviour contradicts documented or obviously-intended behaviour, **with evidence**. |
| Enhancement | `enhancement` | New capability or a deliberate change to existing behaviour. |
| Documentation | `documentation` | The code is right and the docs are wrong, missing or misleading. |
| Chore / CI / refactor | `type/chore`, `type/ci`, `type/refactor` | No user-visible behaviour change. |
| Question | `question` | A request for information. Usually belongs in Discussions → Q&A instead. |
| Duplicate | `duplicate` | The same work as another open issue. The *older or better-specified* one survives. |
| Superseded | `superseded` | Replaced by a differently-framed issue, not strictly a duplicate. |
| Needs reproduction | `needs-reproduction` | Asserts a defect without naming the code that exhibits it. |
| Out of scope | `out-of-scope` | Deliberately not doing it. The reply must say **why**, and what to do instead. |
| Epic | `type/epic` | A tracking issue. Children carry the real type labels; the epic carries none. |

Orthogonal overlays, any number of which may apply:

| Label | Means |
|---|---|
| `good first issue` | Small, self-contained, has a code pointer and a testable outcome. See §3. |
| `help wanted` | The maintainer is not going to get to this; a contributor would be welcome. |
| `security` | Security-relevant defect or hardening. **Not** for undisclosed vulnerabilities — those go through [the advisory flow](../.github/SECURITY.md). |
| `performance` | Measurable speed or memory effect. |
| `backlog` | Deferred by a recorded audit, with the route back written down. |
| `needs-decision` | Blocked on a maintainer call, not on work. |
| `area/*` | Which part of the codebase. One per issue where possible. |
| `priority/P1..P3` | P1 correctness or security; P2 meaningful; P3 nice to have. |

### Rules that make the taxonomy mean something

- **`bug` and `enhancement` are mutually exclusive.** If it is both, it is two issues.
- **One priority scale.** `priority/P1..P3` only. (`priority/high` is the retired duplicate — §4.)
- **One type label.** Four type labels on one issue means the issue has not been triaged.
- **An epic carries no type label.** Its children carry them.
- **`question` is rarely right.** If someone is asking how something works, that is Discussions →
  Q&A; if the docs failed to answer it, that is `documentation`.

---

## 3. What qualifies as `good first issue`

All five, not three of five:

1. **A code pointer in the issue** — file and line, not "somewhere in the parser".
2. **Bounded blast radius** — one or two files. Nothing that changes an artifact format, a public
   signature or a documented invariant.
3. **A testable outcome** — you can say in advance which test will be added and what it asserts.
4. **No unresolved design decision.** If the first task is choosing between two approaches, it is
   not a first issue.
5. **It does not need the whole pipeline in your head.** Changing `TOKEN_RE` qualifies; changing
   how chunks are cut does not, because chunk boundaries interact with citation offsets.

Current starter tasks with full acceptance criteria and code pointers:
**[docs/good-first-issues.md](good-first-issues.md)**.

---

## 4. Proposed label taxonomy

The repository has **61 labels** with four duplicated axes. Nothing below has been applied; the
commands are in [issue-triage-2026-09-25.md §8](issue-triage-2026-09-25.md#8-applying-this).

### 4.1 To create

| Label | Colour | Description |
|---|---|---|
| `needs-reproduction` | `#d876e3` | Asserts a defect without naming the code that exhibits it |
| `out-of-scope` | `#ffffff` | Deliberately not doing this; see the issue reply for why |
| `needs-decision` | `#fbca04` | Blocked on a maintainer decision, not on work |
| `superseded` | `#cfd3d7` | Replaced by a better-specified issue |
| `type/epic` | `#5319e7` | Tracking issue; children carry the real type labels |

### 4.2 To retire, after migrating the issues that carry them

Each of these duplicates an axis that already has a canonical label. Deleting a label removes it
from every issue irreversibly, so this is the last step, not the first.

| Retire | Keep | Why |
|---|---|---|
| `fix`, `type/fix` | `bug` | Three labels for one concept. `bug` is GitHub's default and what search suggests. |
| `feat`, `type/feat` | `enhancement` | Same. |
| `docs`, `type/docs` | `documentation` | Same. `area/docs` stays — it is the *area*, not the type. |
| `chore` | `type/chore` | Keep the namespaced one; `type/*` is the convention for the rest. |
| `test` | `type/test` | Same. `area/tests` stays. |
| `priority/high` | `priority/P1` | Two parallel priority scales. 12 issues carry both. |
| `invalid` | `needs-reproduction` / `out-of-scope` | "This doesn't seem right" says nothing actionable. |
| `wontfix` | `out-of-scope` | Same meaning, clearer name, and it reads less like a dismissal. |

That is 61 → 51, with every remaining label on exactly one axis: **type**, **area**, **priority**,
**state**, or **PR metadata** (`size/*`, `needs-rebase`, `has-conflicts`, and the rest, which
`prod-igy` applies to pull requests and which no issue should carry).

### 4.3 The axes, after consolidation

| Axis | Labels | Rule |
|---|---|---|
| Type | `bug`, `enhancement`, `documentation`, `type/chore`, `type/ci`, `type/refactor`, `type/test`, `type/epic`, `question` | Exactly one |
| Area | `area/cli`, `area/docs`, `area/embed`, `area/graph`, `area/mcp`, `area/query`, `area/tests`, `area/walker`, `area/workflows`, `area/action` | One, ideally |
| Priority | `priority/P1`, `priority/P2`, `priority/P3` | Exactly one |
| State | `needs-reproduction`, `needs-decision`, `duplicate`, `superseded`, `out-of-scope`, `backlog`, `help wanted`, `good first issue` | Any number |
| Flags | `security`, `performance`, `accessibility` | Any number |

---

## 5. Closing an issue

- **Never close an issue without a reply that says why.** A closed issue with no comment is
  indistinguishable from an abandoned one, and it is the fastest way to lose a contributor.
- **Duplicates keep the better-specified issue, not the older one.** Copy anything the closing
  issue said that the survivor does not, then link both ways.
- **Out of scope needs an alternative.** "We are not doing X" should be followed by "because Y,
  and the thing that would make X easy for someone else is Z."
- **Stale is not a reason.** This project has no stale bot and should not get one. An issue that
  has gone quiet is either still true or was never reproducible; decide which.

For contributor-facing conduct expectations during triage, see
[CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) — it applies to issue and discussion threads exactly as
it does to pull requests.
