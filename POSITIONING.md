# Positioning and messaging

The single source of truth for how repo2graph describes itself. Every outward-facing surface — the
README, the PyPI summary, the MCP registry entry, the Action's Marketplace blurb, the GitHub
description and topics, the landing page — should be derivable from this file. When a surface and
this file disagree, this file is the bug report.

Audited and rewritten 2026-09-25 against repo2graph 2.1.0.

---

## 1. The core outcome

> **Give coding agents trustworthy, cited answers about unfamiliar codebases.**

That sentence is the product. Everything else is mechanism.

Read it a word at a time, because each one is load-bearing and each one is a promise something in
the repository has to keep:

| Word | The promise | Where it is kept |
|---|---|---|
| **coding agents** | The primary consumer is a machine with a context window, not a human with a browser. The human-facing `graph.html` is a side artifact. | `repo2graph/mcp.py` — five read-only tools, every numeric argument clamped in the handler |
| **trustworthy** | You can tell when it is wrong, and it tells you what it cannot see. | `confidence` on every `CALLS` edge; [docs/limitations.md](docs/limitations.md) |
| **cited** | Every returned block names the file and line range it came from. | `[cite: path:start-end]` on every block out of `Index.pack_context()` |
| **answers** | The source that answers the question comes back — not a map, not a subgraph, not a list of paths to go read. | `docs/comparison.md#the-axis-that-matters-what-comes-back-from-a-query` |
| **unfamiliar** | The value is highest where your own knowledge is lowest. Zero setup is what makes that true: no config file, no language server, no build step. | `uvx repo2graph build .` on any folder |

### What we are *not* claiming

Stated here so no surface drifts into them:

- **Not** "understands your codebase." It parses it. Those are different, and the difference is the
  entire [limitations](docs/limitations.md) page.
- **Not** "complete call graph." Call resolution is name-based. An absent edge is not proof of an
  absent call, and we say so on the first screen.
- **Not** "replaces grep." `repo2graph query` *runs* BM25 as its first step. A tool that needs its
  predecessor to work cannot claim to replace it.
- **Not** "AI-powered." The default path makes zero network calls and loads no model. The one
  opt-in exception (`rag --answer`) announces its provider and hostname before sending a byte.
- **Not** benchmark numbers we did not measure. Everything numeric in the README traces to
  `benchmarks/results.json` or an `examples/*/stats.json`.

---

## 2. Message hierarchy

What belongs above the fold, and what has to wait.

**First screen (hero, nav, first two paragraphs)**

1. The outcome sentence, verbatim.
2. The failure it replaces, in the reader's language: *the agent greps, floods its context with
   whole files, and still edits the wrong one.*
3. The one differentiator a reader can verify in ten seconds: **every block is cited to
   `path:start-end`.**
4. Where it plugs in: Claude Code / Cursor / any MCP client, the CLI, or CI.

**Second screen**

5. Who it is for (four personas, §3) and the first command for each.
6. Why not grep or vector search — the comparison table.
7. Quickstart.

**Below that**

8. Key features, what it does / does not do, tool-vs-tool comparison, MCP tool contract.
9. Architecture and token economics — **this is where the vocabulary lives**.

### Jargon policy

The terms are accurate and we keep them; they are just not an opening argument. A reader who
already knows what GraphRAG is will not be put off by meeting the word in §9. A reader who does not
will bounce off it in §1.

| Term | Allowed above the fold? | Where it belongs |
|---|---|---|
| **GraphRAG** | No | "Architecture & token economics"; the `rag` row of the CLI table; `pyproject.toml` keywords (search discovery) |
| **AST-driven** | No — say "reads the code rather than searching it" | TECHNICAL.md |
| **tree-sitter** | Only as a linked attribution inside a sentence about behaviour, never as the claim itself | Architecture, TECHNICAL.md |
| **BM25 / RRF / dense fusion** | No | Architecture, `docs/reference.md` |
| **`pack_context()` / `budget_chars`** | No | Architecture, `docs/python-api.md` |
| **MCP / Model Context Protocol** | **Yes** — it is the distribution channel, and the audience searches for it | Everywhere |
| **cited / `[cite: path:start-end]`** | **Yes** — it is the differentiator | Everywhere |
| **token budget / ceiling** | **Yes** — the audience feels this one daily | Everywhere |

Rule of thumb: above the fold, name the *outcome* and the *channel*. Below it, name the
*mechanism*.

**This policy is enforced by tests**, in all six READMEs at once
(`tests/test_i18n_consistency.py`):

- `test_the_first_screen_names_no_mechanism` — nothing from `GraphRAG`, `BM25`, `pack_context()`,
  `AST` or `RRF` may appear before the persona heading (`## 👥`), which is where the first screen
  ends.
- `test_graphrag_stays_below_the_architecture_heading` — `GraphRAG` may not appear anywhere above
  `## 📐`, in any language.

Emoji section markers are what make this checkable across translations: `## 📐` and `## 👥` are
identical in all six files, while every word around them changes. Note that **`BM25` is deliberately
allowed below the fold** — the grep comparison table names it on purpose, because conceding that
lexical search is still the seeding step is part of the argument.

---

## 3. Personas

Four audiences, one product. Each gets: the pain in their own words, the sentence written for them,
the proof they will check, and the first command they should run.

### 3.1 Developer onboarding to an unfamiliar repository

- **Pain:** "Week one is `find`, `grep` and opening files hoping one of them is the entry point. I
  don't know which of these 2,000 files matter."
- **Message:** *Start from the shape of the codebase, not the root directory. Then ask it whole
  questions and read the answer as source.*
- **Proof:** `graph.html` — one self-contained file, hub files visible immediately, click a node to
  see its code and its neighbours. Then a `rag` answer whose citations they can open and verify.
- **First command:** `uvx repo2graph build . -o .r2g && open .r2g/human/graph.html`
- **Objection to pre-empt:** *"Another tool to set up."* — there is no setup. No config file, no
  language server, no API key, ~3 seconds on a 195-file repo.
- **Where they find us:** GitHub search, "how to understand a large codebase" content, the
  `examples/` repositories.

### 3.2 Coding-agent user (Claude Code, Cursor, any MCP client)

- **Pain:** "It greps, pulls three whole files into context, burns 40k tokens, and edits the wrong
  one. And I can't tell whether it read the code or made it up."
- **Message:** *Cited blocks under a hard token ceiling, instead of file dumps. When the agent is
  wrong, the citation shows you where.*
- **Proof:** `MCP_MAX_BUDGET_TOKENS = 12000`, clamped in the handler and re-measured before
  returning, so a caller cannot widen it by asking. All three content tools pass
  `exclude_secrets=True` unconditionally — no flag turns it off.
- **First command:**
  `claude mcp add repo2graph -- uvx --from "repo2graph[mcp]" repo2graph-mcp /path/to/project`
- **Objection to pre-empt:** *"My agent already has search."* — it does, and we seed with BM25 for
  exactly that reason. The `repo_neighbours` hop is the thing search cannot do: one symbol in,
  definer + callers + callees out, with file and line.
- **Where they find us:** MCP registry, Glama, mcpservers.org, Claude/Cursor MCP directories.

### 3.3 Reviewer doing PR blast-radius analysis

- **Pain:** "The diff is 40 lines. I have no idea what else depends on it, and 'looks fine' is how
  regressions ship."
- **Message:** *Ask what touches the changed symbol — callers, importers, subclasses — and what the
  repository's own history says usually changes alongside it.*
- **Proof:** `CO_CHANGE` edges mined from `--git-history` (requires 3+ co-edits), which no parser
  can produce; `repo2graph explain node "<id>"` listing incoming and outgoing edges; the GitHub
  Action's job-summary graph delta since the last build.
- **First command:** `repo2graph build . -o .r2g --git-history 500` then
  `repo2graph explain node "sym:src/auth.py::verify" -o .r2g`
- **Objection to pre-empt:** *"How do I know it found everything?"* — you don't, and we say so.
  `confidence` is on every `CALLS` edge; dynamic dispatch, reflection and DI are enumerated as
  blind spots on the first screen. Treat the result as a floor on impact, never a ceiling.
- **Where they find us:** GitHub Marketplace, CI/code-review tooling comparisons.

### 3.4 Open-source maintainer

- **Pain:** "Every new contributor asks the same 'where do I start' question, and the architecture
  doc went stale two refactors ago."
- **Message:** *Commit a fresh map on every push. Let contributors browse it instead of asking, and
  see architectural drift in the run summary.*
- **Proof:** Published GitHub Action, `commit-branch:` force-pushes `graph.html` to a browsable
  branch (this repository's own `/graph` branch is built that way), job summary reports hub files,
  CO_CHANGE hotspots and the delta since the last build. It never calls an LLM — `--answer` is
  deliberately not exposed to the Action.
- **First step:** add `Srinivasan-78/repo2graph@v2` with `git-history: "500"` and
  `commit-branch: graph`.
- **Objection to pre-empt:** *"I'm not sending my code to anyone."* — the Action makes no LLM call
  at all, and the CLI's default path makes no network call of any kind.
- **Where they find us:** GitHub Marketplace, Action badges on other repositories, the `/graph`
  branch of repos that adopted it.

---

## 4. Why repo2graph instead of grep or search?

The canonical table lives in the [README](README.md#vs-grep) so it is seen; it is reproduced here
so it can be maintained in one place. The argument behind it is
[docs/why-graph.md](docs/why-graph.md); the tool-vs-tool version is
[docs/comparison.md](docs/comparison.md).

| | grep / ripgrep | Embedding search | repo2graph |
|---|---|---|---|
| Finds | the exact string | text that reads similarly | the symbol, then everything wired to it |
| Different words than the code uses | returns nothing | handles it | BM25 seeds, then graph hops reach code the query never named |
| "What calls this?" | can't answer | can't answer | `CALLS` edges, with direction and `confidence` |
| "What breaks if I change this?" | read every hit by hand | not represented | callers, importers, subclasses in one hop |
| What comes back | matching lines, or whole files | top-k similar chunks | the source that answers it, cited per block |
| Token cost | unbounded | unbounded | hard ceiling on the whole pack, re-measured |
| "Which files change together?" | — | — | `CO_CHANGE`, from git history |
| Setup | none | index + ~90 MB model | one parse pass, no model, no key |
| Ranking explainable | n/a | a cosine number | `repo2graph explain retrieval "<q>"` |

The honest concession, which must stay in every version of this table: **for a literal string —
a config key, an error message, a TODO — grep is the right tool and repo2graph will not beat it.**
A comparison that concedes nothing gets believed by nobody.

---

## 5. What it does / does not do

The full table is in the [README](README.md#does-and-doesnt); the measurements are in
[docs/limitations.md](docs/limitations.md). The eight limitations that must appear on *any* surface
long enough to have a limitations section:

1. **Ambiguous call resolution** — name-based, not type-based; up to 5 candidates at
   `confidence = 1/n`; 4.6%–21.3% of `CALLS` edges ambiguous across the five benchmark repositories.
2. **Dynamic dispatch** — string-keyed lookups, plugin registries, `getattr` dispatch, virtual
   calls resolved at runtime. *No arrow does not prove no call.*
3. **Reflection and computed imports** — nothing literal to resolve, so no edge.
4. **Dependency injection** — the edge lands on the interface declaration, or fans out across every
   same-named implementation, never on the class the container injected.
5. **Generated code** — indexed exactly like hand-written code, with no marker distinguishing it.
6. **Stale indexes** — the index is a snapshot; nothing watches the filesystem; `doctor` checks
   integrity and vector drift, not working-tree drift.
7. **Cross-language boundaries** — Python → C++ through generated bindings is a `CALLS_EXTERNAL`
   edge, not a link.
8. **Macro-heavy C/C++** — tree-sitter `ERROR` nodes around unexpanded macros; read `parse_errors`
   as a floor on missed symbols.

Items 4 and 6 were added in this pass; the other six already existed and were promoted from
`docs/limitations.md` onto the README's first-time-reader path.

---

## 6. Surface audit

Status as of this change.

| Surface | Before | After | Where |
|---|---|---|---|
| **README hero** | "AST-driven code graphs & zero-dependency GraphRAG for AI coding agents and humans" | "Give coding agents trustworthy, cited answers about unfamiliar codebases." + a plain-language second line | ✅ `README.md` |
| **README §1** | Opened on grep's token cost, then immediately tree-sitter + edge-type list + "Model Context Protocol" | Opens on the reader's failure mode, then the citation promise, then the channel. Mechanism moved to ¶3. | ✅ `README.md` |
| **README personas** | absent | Four-column "Who it's for" with the first command per persona | ✅ `README.md` |
| **README vs. grep** | only a tool-vs-tool table, grep as one column among four | Dedicated "Why repo2graph instead of grep or vector search?" table, above the quickstart | ✅ `README.md` |
| **README limitations** | one bullet in Architecture; the rest only in `docs/limitations.md` | "What it does — and what it does not", 8 rows, before the tool comparison | ✅ `README.md` |
| **README jargon** | "GraphRAG" in the hero | "GraphRAG" appears in Architecture and the `rag` CLI row only | ✅ `README.md` |
| **PyPI summary** | "Turn any repository into a code graph + graph-aware RAG chunks" | "Give coding agents cited, budget-bounded answers about any codebase — code graph + MCP server" | ✅ `pyproject.toml` |
| **PyPI keywords** | 8, all mechanism | 16 — added `coding-agent`, `claude-code`, `cursor`, `code-navigation`, `code-understanding`, `onboarding`, `impact-analysis`, `citations` | ✅ `pyproject.toml` |
| **PyPI long description** | = README | = README (inherits every change above) | ✅ automatic |
| **MCP registry** | "Ask a codebase questions and get cited code back, from a tree-sitter graph of the repo." | "Ask a codebase a question, get back the source that answers it — every block cited to file:line." (96 chars, under the 100-char registry limit) | ✅ `server.json` |
| **Action / Marketplace** | "Build a code graph and graph-aware RAG chunks for a repository" | "Build a browsable code graph and cited, budget-bounded context for your repo — in CI, no LLM call" | ✅ `action.yml` |
| **docs index** | "When to use repo2graph", good-fit/less-value bullets | Persona routing table (read this → run that) + the good/less-fit bullets, plus a DI and a stale-index entry | ✅ `docs/README.md` |
| **docs/limitations.md** | no DI entry; freshness covered only for the shipped examples | DI bullet added; new "Stale indexes" section about your own `.r2g` | ✅ `docs/limitations.md` |
| **docs/why-graph.md** | grep only | plus an "What about embedding search?" section and a pointer to the README table | ✅ `docs/why-graph.md` |
| **docs/comparison.md** | said "15 languages" (twice); README said 16; actual is 17 | all corrected to 17 | ✅ `docs/comparison.md` |
| **GitHub description** | "Turn any repository into a graph of its files, folders and functions and the links between them." | "Give coding agents trustworthy, cited answers about your codebase. Code graph + MCP server, no LLM required." | ✅ applied to repo metadata (§7.1) |
| **GitHub topics** | 13, mechanism-heavy, missing every agent term | 20 — dropped `graph`, added `ai-agents`, `coding-agent`, `claude-code`, `cursor`, `llm`, `code-search`, `codebase-search`, `developer-tools` | ✅ applied to repo metadata (§7.2) |
| **Translated READMEs** (de/es/fr/ja/zh-CN) | five, all carrying the old hero; all five said "16 grammars / 28 extensions" | hero, intro, personas, grep table and does/doesn't translated into each; counts corrected to 17/29 | ✅ `docs/i18n/` |
| **Translation drift guard** | none — nothing enforced any relationship between the six files | `tests/test_i18n_consistency.py`, 25 cases | ✅ `tests/` |
| **Landing page** | "A GraphRAG engine and MCP server that hands an AI agent the right code — cited, budget-capped, and nothing else." | proposed below — **not applied**, the site lives outside this repository | ⬜ §7.3 |

---

## 7. Copy for surfaces outside this repository

Ready to paste. Nothing here is applied by this PR.

### 7.1 GitHub repository description — **applied**

> Give coding agents trustworthy, cited answers about your codebase. Code graph + MCP server, no LLM required.

(110 chars; GitHub's limit is 350, but the listing truncates around 150.)

The previous description — *"Turn any repository into a graph of its files, folders and functions and
the links between them"* — described the artifact, not the outcome, and omitted both MCP and
citations, which are the two things the audience searches for.

### 7.2 GitHub topics — **applied**

GitHub caps a repository at 20 topics. The previous list was 13; the list below is exactly 20.

**Keep (12):** `mcp`, `model-context-protocol`, `code-graph`, `graphrag`, `rag`, `tree-sitter`,
`static-analysis`, `code-analysis`, `dependency-graph`, `github-action`, `visualization`, `python`

**Drop (1):** `graph` — too generic to generate a qualified visitor; the slot is worth more to a
term below.

**Add (8):** `ai-agents`, `coding-agent`, `claude-code`, `cursor`, `llm`, `code-search`,
`codebase-search`, `developer-tools`

The command that was run (kept here so the list is reproducible, and so a future change edits a
recorded baseline rather than guessing at one):

```bash
gh repo edit Srinivasan-78/repo2graph \
  --description "Give coding agents trustworthy, cited answers about your codebase. Code graph + MCP server, no LLM required." \
  --add-topic ai-agents --add-topic coding-agent --add-topic claude-code \
  --add-topic cursor --add-topic llm --add-topic code-search \
  --add-topic codebase-search --add-topic developer-tools \
  --remove-topic graph
```

### 7.3 Landing page (`srinidevops.com/projects/repo2graph`)

The current hero — *"A GraphRAG engine and MCP server that hands an AI agent the right code —
cited, budget-capped, and nothing else"* — is a well-built sentence aimed at a reader who already
knows what GraphRAG is. It leads with the category, and the outcome ("the right code, cited")
arrives after the reader has had to parse two pieces of jargon.

**Hero**

> # Give coding agents trustworthy, cited answers about unfamiliar codebases.
>
> Ask a repository a question. Get back the source that answers it — every block stamped with the
> file and line it came from, inside a token budget that is enforced rather than requested.
>
> `uvx repo2graph build .` — no config, no language server, no API key.

**Second block — the failure it replaces (keep it concrete)**

> An agent dropped into a codebase it has never seen greps for a word, floods its context with
> three whole files, and still edits the wrong one. Or it guesses from training data and writes
> something confident and wrong. Either way, you can't tell which just happened.
>
> repo2graph reads the code instead of searching it: one parse pass records who calls whom, who
> imports what, and which class extends which — and retrieval follows those links instead of
> matching more text.

**Third block — four cards, one per persona** (headline / one line / one command)

| Card | Headline | Line | Command |
|---|---|---|---|
| 🧭 | Joining a new codebase | Start from the hub files, not the root directory. | `uvx repo2graph build . -o .r2g` |
| 🤖 | Driving a coding agent | Cited blocks under a 12k-token ceiling, not file dumps. | `claude mcp add repo2graph …` |
| 🔍 | Reviewing a pull request | Callers, importers, subclasses — plus what git says changes alongside. | `repo2graph explain node …` |
| 🌱 | Maintaining a project | A fresh, browsable map committed on every push. | `uses: Srinivasan-78/repo2graph@v2` |

**Engineering Highlights** — the existing nine bullets are strong and stay, with two changes:

- Reorder so the two that are outcomes, not features, go first: *"Every retrieved block carries its
  file and line range and the edge that pulled it in"* and *"Token budgets are enforced rather than
  requested."*
- Add a tenth: *"It tells you what it can't see — dynamic dispatch, reflection, DI and generated
  code are enumerated blind spots, not silent gaps."* Publishing the limitations is itself a
  differentiator in this category.

**Architecture section** — unchanged. This is where "GraphRAG", "tree-sitter", "BM25" and
"RRF fusion" belong, and where they read as credibility rather than as a barrier.

---

## 8. Translations, and the guard that keeps them honest

All five translated READMEs (`docs/i18n/README_{zh-CN,ja,fr,es,de}.md`) now carry the repositioned
hero, the rewritten intro, the persona table, the grep/vector comparison and the
does/does-not table, and all five have the corrected 17-grammar / 29-extension counts.

They were **retranslated rather than reduced to a stub link**: they were already condensed versions
of the English README (240–303 lines against its ~600), not one-to-one mirrors, so the positioning
sections could be carried across at the same level of abridgement they already used. Reducing them
to an intro plus a link would have been cheaper to maintain but would have removed working
documentation from every non-English reader.

The reason that trade-off was even a question is that **nothing enforced any relationship between
the six files** — which is exactly why all six drifted together, every one of them still claiming
"16 grammars / 28 extensions" long after Lua made it 17/29. That gap is now closed by
`tests/test_i18n_consistency.py` (25 cases), which pins four things across all six READMEs at once:

| Test | Catches |
|---|---|
| `test_the_translation_set_is_the_one_the_switcher_offers` | a translation added or deleted without updating the language switcher |
| `test_every_readme_documents_every_parsed_grammar` | the exact drift that happened — a new `LANG_CFG` grammar missing from any README, in any language |
| `test_graphrag_stays_below_the_architecture_heading` | a future edit dragging `GraphRAG` back above `## 📐` |
| `test_the_first_screen_names_no_mechanism` | `GraphRAG`, `BM25`, `pack_context()`, `AST` or `RRF` reappearing before the persona heading |
| `test_every_readme_carries_the_positioning_anchors` | a translation losing the persona, comparison or limitations section entirely |

None of these compares one README's prose against another's — a translation legitimately differs
from its source in every sentence. They pin language-independent literals (`explain retrieval`,
`build --incremental`, `[cite:`, `CO_CHANGE`, `repo2graph-mcp`) and the `LANG_CFG` grammar list,
which is the one thing all six must state identically. Each was verified as a real detector by
reintroducing the bug it targets and watching only that test fail.

### Still open

- **The landing page** (§7.3) lives outside this repository. The copy is ready to paste.
- **The translations are abridged on purpose.** They do not carry the Docker section, the four-way
  "ways to run" table, or the full MCP tool contract, and they did not before this change either.
  The guard above ensures they cannot silently lose the *positioning*; it does not make them
  complete translations, and they are not meant to be.

---

## 9. Maintaining this

When you change outward-facing copy:

1. Change it here first, or at least in the same commit.
2. Update the §6 row so the audit does not go stale.
3. If the change adds a claim, add the proof column entry too. A claim with nowhere to verify it
   is the thing this whole document exists to prevent.
