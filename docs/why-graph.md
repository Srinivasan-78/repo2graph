# Why not just grep?

Keyword search and graph traversal answer different questions. Neither replaces the other.

```
Search:  keyword -> files -> manual traversal
Graph:   symbol  -> relationships -> callers -> dependencies -> impact
```

## What search is good at

`grep -r "login"` finds every file where the word "login" appears — including a comment, a log
message, a variable named `loginAttempts`, and the function that actually authenticates a user. It
is fast, needs no setup, and works on anything text-shaped. When you know roughly what string you
are looking for and just need every occurrence, search is the right tool and repo2graph does not
try to be a better version of it — `repo2graph query` still runs a lexical (BM25) search as its
first step, for exactly this reason.

## What search is bad at

Search cannot tell you which of those "login" matches is the function that does the work, which
functions call it, or what breaks if you change its signature. It has no notion of "this file
imports that one" or "this class inherits from that one" — every one of those facts has to come
from you re-reading the surrounding code, file by file, by hand. On an unfamiliar codebase, that
manual traversal is most of the time cost of onboarding.

## What the graph adds

repo2graph parses the code once and records the relationships search cannot see: which function
calls which (`CALLS`), which file imports which (`IMPORTS`), which class extends which
(`INHERITS`), which symbol is defined where (`DEFINES`). `repo2graph query` finds the same lexical
matches search would, then walks one hop along those edges — so asking "how does export write a
manifest" does not just return the function named `export`, it returns the functions that call it
and the functions it calls, in the same answer, each one labeled with *why* it is there (the actual
edge type, e.g. `CALLS out of cmd_build`).

That is the concrete difference measured across this project's own real-repository examples
(see [examples/](../examples/)): a query like "how does a request travel through Django middleware"
(see [examples/django/flows/](../examples/django/)) returns the middleware dispatch function *and*
its neighbours in the call graph — the pieces a keyword match on "middleware" alone would not tell
you were connected.

## Where the graph is worse than search

- **A one-off literal string.** If you need every place a specific config key or error string
  appears, `grep` answers that directly; the graph has no special knowledge of string literals.
- **Anything the parser cannot see.** Dynamic dispatch, reflection, and generated code all limit
  what edges exist to walk — see [docs/limitations.md](limitations.md). Search does not have this
  blind spot, because it never claimed to understand structure in the first place.
- **A tiny codebase.** Building an index for a 10-file script costs more than it returns — see
  "When to use repo2graph" in [the documentation index](README.md#when-to-use-repo2graph).

## The honest summary

Search finds occurrences of a string. The graph finds relationships between named things. Most real
questions about a codebase ("what calls this," "what would changing this break," "how does a
request get from A to B") are relationship questions, which is why repo2graph builds a graph instead
of a better search index — but it keeps search as the graph's own entry point, because "find me the
starting point" is still a search question.
