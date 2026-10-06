# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries for v1.0.0 through v1.4.0 were reconstructed from git history and the
published GitHub Releases after the fact, so they summarise what shipped rather
than being contemporaneous notes. `.github/workflows/publish.yml` now reads the
section for a version out of this file and uses it as the Release body, which
makes keeping it current a release-blocking step rather than a good intention.

## [Unreleased]

### Added

- **Seeds are ranked on whether a chunk can plausibly hold an answer.** On the held-out 40+40,
  structural recall 19/25/62% -> 29/60/79% at 2k/4k/8k against grep's 14/21/38, and repo2graph
  now spends *fewer* tokens than grep reaching it. Lexical 14/24/40% -> 17/29/45% against grep's
  28/48/59: better, still losing. On the published 35+10 (a regression set, visible while these
  fixes were made) lexical reads 44/63/80% against grep's 35/61/72% and structural 60/80/100%
  against 20/20/70%.

  The cause was never parsing or retrieval. Measured on the 35-question set at 8,000 tokens:
  every one of the 46 evidence definitions was present in some chunk, an oracle packer fit 100%
  of them inside the budget, and yet **54% of pack tokens went to container chunks**. The
  chunker emits each method as its own chunk and cuts it from the parent, so Flask's
  `class Flask` chunk spans lines 81-1536, costs ~1,150 tokens and holds no method body at all
  -- while ranking highly on almost any Flask question, because a long class docstring names
  everything the class does.

  Two corrections, applied as a re-rank over `score_rrf`'s output rather than as a change to
  BM25: RRF maps scores onto `1/(60 + rank)`, nearly flat across the top of the list, so a
  modest multiplier separates candidates BM25 could not -- without disturbing the lexical
  scoring `repo2graph query` and the goldens pin. `CONTAINER_SEED_PENALTY` demotes class and
  file-residual chunks, but only above `CONTAINER_PENALTY_MIN_TOKENS`, because the cost is the
  problem and a cheap container has none. `NAME_TERM_BOOST` rewards a chunk whose declared name
  shares content words with the question, capped at two terms and withheld from three kinds of
  guaranteed non-answer: bulky containers, export aliases, and -- unless the question is about
  tests -- test definitions, which name the behaviour in the question's own words and so collect
  the boost more reliably than the implementation does.
  `pack_context(rerank_answerability=False)` restores the 2.2 ordering.

  Scaling the boost by the *fraction* of query terms a name covers was measured and rejected: it
  loses 9 of 12 held-out cells. The strong multiplier earns its keep; gating which chunks are
  eligible for it is what makes it safe.

- **Two held-out sets, holding out different things.**
  `benchmarks/real/tasks_holdout.json` is 40 lexical questions (plus 40 structural in
  `tasks_holdout_structural_40.json`) on the same four repositories, so it tests whether a change
  generalises to questions nobody tuned on. `benchmarks/real/tasks_holdout_repos.json` is 22
  questions on click 8.1.8 and axios 1.7.9 -- repositories that appear in no other task file, and
  the suite's only JavaScript -- so it tests whether a change generalises to code it never saw.
  Every line range was read off the pinned commit before any retriever ran. On the repository set
  the ranking change moves 67% -> 79% at 4,000 tokens and 71% -> 79% at 8,000, against grep's 58%
  and 67%, having never seen it; the uncomfortable half of the same table is that graph expansion
  adds **exactly zero** there at every budget while costing tokens. `bench_real_repos.py` grew a
  repository-set option of its own (a benchmark script argument, not a `repo2graph` CLI flag), and
  now drops tasks naming an unindexed repository rather than scoring them missed against an index
  that cannot hold their evidence.
  `test_the_benchmark_task_files_stay_disjoint` fails if the two corpora ever share a repository.

- **`tests/test_bench_tables_match_artifacts.py` — published numbers are now machine-checked.**
  Every benchmark table in `README.md`, `benchmarks/real/README.md` and `docs/comparison.md`
  carries an HTML-comment marker naming the artifact it was read from, and the test fails if any
  cell disagrees with that artifact, if a benchmark-shaped table carries no marker, or if a
  referenced artifact was generated from a dirty tree. It exists because a published table had
  silently been read off an intermediate artifact two phases old, understating two cells by
  2.4 pp each and recording a round target as missed by 1 pp when it had passed. The 18 existing
  doc-consistency tests check that links resolve and command lines parse, not that a number
  matches the JSON beside it.

  Four ideas were tried and rejected on the evidence, recorded in that page because each looked
  obviously right beforehand: path-term boosting (worth nothing, slightly negative at 4k),
  dropping test files from seeds (neutral on the dev set, harmful on the held-out set), raising
  `k` from 8 to 40 (+2 lexical, but structural falls 100% -> 80% as extra lexical seeds crowd
  out graph neighbours), and penalising containers by kind with no cost floor (dropped the demo
  fixture's 91-token `app/store.py` residual, caught by `test_demo.py` rather than by the
  benchmark).

- **Property-based tests, via `hypothesis` (G57).** `tests/test_properties.py` — 22 properties over
  UTF-8/surrogate decoding, chunk line slicing, and path normalization, the three areas where
  hand-written examples kept missing cases because they spell inputs the way the rest of the
  codebase produces them. Run under a fixed profile (`derandomize=True`, `max_examples=100`,
  `deadline=None`) so CI cannot go flaky; the properties were first verified at `max_examples=4000`
  non-derandomized, so the shipped setting is not hiding what the wider search found. Among them,
  a direct detector for CONTRIBUTING invariant 1: a source with no `\n` is exactly one line however
  many `U+2028` / `\x0b` / `\x0c` / `\x85` characters it holds, which is precisely what
  `splitlines()` would get wrong.
- **`--max-bytes`, `--max-edges` and `--limit-policy truncate|warn` (E40).** `--max-files` bounded
  discovery, but nothing bounded the two axes that actually decide a build's memory ceiling: total
  source bytes read, and total edges held. Edge count grows with how interconnected the code is,
  not with how many files it has, so a repository well under any file count can still produce
  millions of edges. `--max-bytes` keeps a *prefix* of discovery order rather than skipping an
  oversized file and continuing — skipping would make which small files are indexed depend on the
  sizes of files after them, so one large file inserted mid-repository could silently change the
  tail of the selection. Both ceilings record what they cut under `limits_hit` in `stats.json`,
  under **both** policies: `truncate` buys silence on stderr and nothing else, because a reader
  asking "are there really no callers of `f`, or was that edge dropped?" has no other way to tell.
  `tests/test_resource_limits.py`, including a determinism test pinning that two builds under one
  ceiling keep the same edges.
- **A total wall-clock bound on provider streaming, `MAX_ANSWER_SECONDS` (C26b).** `rag --answer`
  had two bounds and needed three. `HTTP_TIMEOUT` is a *socket* timeout: it measures the gap
  between reads and resets on every byte that arrives, so a host sending one byte at a time, for
  ever, never trips it. `MAX_ANSWER_BYTES` bounds total size, but a trickle that stays under the
  ceiling is never bounded by it either — together they permitted an indefinite hang on a stream
  that looked well behaved, against an endpoint `OLLAMA_HOST` makes operator-choosable. The
  stream now also stops after 600s measured on `time.monotonic()`, so an NTP step cannot cut a
  healthy answer short. `timed_out` is reported separately from `truncated`, with distinct
  wording: "too slow" and "too much" have different causes and different remedies.
- **A seventh `doctor` check: `MCP Server`.** An MCP client surfaces a failed SDK preflight only
  as "server failed to start" or an empty tool list — `server._require_sdk()` raises `SystemExit`
  with an accurate message and the client swallows it — so a wrong or absent `mcp` SDK was a
  setup failure users could not diagnose from inside their editor. `doctor` now runs the same
  gate without launching the server, importing `SDK_SPEC`/`_sdk_version`/`_sdk_major` from
  `mcp/server.py` rather than restating them, so a doctor that says ok and a server that refuses
  to start cannot disagree. An absent SDK is a **warning**, not a failure: `repo2graph[mcp]` is an
  extra and a CLI-only install is supported. It deliberately does not read the client's own
  configuration (`~/.claude.json`, Cursor's `mcp.json`) — those belong to other tools and
  `doctor --json` is documented as safe to paste into a bug report; a test enforces that over the
  module's AST. Index freshness remains deliberately out of `doctor`, owned by `index-status`.
- **`.github/remediation-tracking.md` and `.github/distribution.md`** — a verified status map for
  the 60-item security/correctness remediation spec, and the distribution plan with its claims
  checked against `benchmarks/real/`. They live in `.github/` beside `CONTRIBUTING.md`, **not** in
  `docs/`: `test_the_shipped_doc_set_is_the_documented_one` pins `docs/` to its five reference
  pages precisely to keep roadmap trackers and outreach material out of it, and that invariant is
  worth more than the convenience of putting them there.
- **Four new MCP tools**, taking the surface from six to ten — and then to **nine**, once
  `repo_impact` was removed later in this same window (see **Removed**); `docs/mcp.md` is written
  against the nine that ship
  ([#384](https://github.com/Srinivasan-78/repo2graph/issues/384),
  [#385](https://github.com/Srinivasan-78/repo2graph/issues/385),
  [#386](https://github.com/Srinivasan-78/repo2graph/issues/386),
  [#387](https://github.com/Srinivasan-78/repo2graph/issues/387)):
  - `repo_find_symbol` — name to `node_id`, so an agent that already knows a function name (from a
    traceback, a review comment, the user's question) no longer has to spend a budgeted
    `repo_search` round trip purely to learn an id format. Matches exactly, then
    case-insensitively, then on the last `qualname` segment.
  - `repo_read` — widen a `[cite: path:start-end]` window. Reads from `chunks.jsonl`, **never from
    disk**: chunks have already passed secret-path exclusion and content redaction, and over HTTP
    the client cannot open the path a citation names anyway. `file_residual` chunks concatenate
    non-contiguous spans, so a read covered only by one answers "not indexed" rather than
    returning the wrong lines.
  - `repo_path_between` — bounded bidirectional BFS over the existing adjacency, reporting each
    path's minimum edge confidence. `CO_CHANGE` is opt-in, never traversed by default.
  - `repo_blast_radius` — reverse reachability: the reverse `CALLS` closure by hop distance,
    reverse `INHERITS`, reverse `IMPORTS` on the containing file, and `CO_CHANGE` files by count.
    Named `repo_blast_radius`, not `repo_impact` as issue #387 proposed, because at the time
    `repo_impact` was taken by the PR-diff analyser — a different question. That tool is gone
    (see **Removed**), but the name stays: it is the one that was shipped and documented.
### Security

- **Grammars no longer download themselves at first build.**
  `tree-sitter-language-pack` is now pinned `>=0.7,<1.0`, and the upper bound is a security
  boundary rather than a compatibility one. `>=0.7` resolved to 1.x, whose wheel is 2.5 MB and
  whose sdist is 89 KB: the grammars are not in it, and roughly 21 MB of unsigned native code was
  fetched from the network on the first `get_parser()` call, into the process holding the user's
  source and — in CI — their tokens. 0.x compiles every grammar into the wheel (0.13.0 is 33 MB).
  The 1.x behaviour broke three promises at once: the README's and SECURITY.md's "building,
  querying and the MCP server make no network calls"; `uv.lock` and the SBOM, which described a
  loader rather than the code that executes; and the documented read-only container and
  egress-restricted CI, which simply could not work. 1.x exposes no offline switch —
  `PackConfig` carries only `cache_dir`, `languages` and `groups`, all pre-*download* controls —
  so raising the cap means vendoring grammars or dropping the claim.
  `tests/test_grammar_availability.py::test_language_pack_is_capped_below_1_0` asserts the bound
  so a dependency bump cannot quietly undo it.
- **An unloadable grammar now fails the build instead of publishing an empty graph.**
  `parse_source` answered an unavailable grammar with an empty-but-valid `ParsedFile`, making
  "the grammar did not load" byte-identical to "this file declares nothing". A build with no
  working grammars therefore reported `parsed == files`, wrote a graph of files and directories
  with zero symbols, calls or imports, and exited 0 — and `--incremental` cached that result, so
  the next build reproduced it without retrying — and every downstream query answered from that
  empty graph as though the repository really had no callers.
  `ParsedFile.grammar_unavailable` now records the distinction, `build` raises
  `ParseError` when supported files were found and no grammar loaded for any of them, and the
  message names the cause and the fix rather than leaving it looking like an empty repository.
- **…and the guard above now also holds for files read in slices.** A file over
  `max_file_bytes` is parsed by `_chunk_and_parse`, which carried `used_cpp` and
  `undecodable_slices` out of its slice loop but not `grammar_unavailable`, so the assembled
  `ChunkedParsedFile` inherited the field's `False` default. Such a file was counted in
  `stats["parsed"]` with zero symbols, and the total-failure check — which fires only on
  `_unavailable and not parsed` — could never see it. A repository whose only supported sources
  are large (an amalgamated single-header library, a big generated file) therefore still exited 0
  with an empty graph, and `--incremental` still cached it: exactly the failure the entry above
  exists to prevent, reachable through the one code path that did not report the flag.
  `tests/test_grammar_availability.py` covers the chunked path on both sides — grammar present
  and grammar gone — with a fixture assertion that the file really exceeds the limit, so the test
  cannot silently stop exercising the chunked reader.
- **A credential-shaped *expression* is no longer redacted as a credential.** The unquoted-value
  rule had an escape hatch for values that are only code, written as
  `not _json_secret_value_ok(value) and <identifier-shaped>`. The first conjunct cancelled the
  second for precisely the values the hatch was for: a mixed-case dotted attribute path has
  lower, upper and — because the dots count as symbol — three of the four character classes, so
  `_json_secret_value_ok` called it a real credential and the hatch never opened. `const apiKey =
  process.env.API_KEY;`, `secret = settings.SECRET_KEY` and `api_key = os.environ["API_KEY"]`
  shipped as `[REDACTED:CREDENTIAL_UNQUOTED]` into chunks, `nodes.jsonl`, `graph.html`, GraphML
  and Cypher under the default `redact-match` — and since signatures and docstrings now route
  through `redact_content` too, into five surfaces at once. The shape test (`_CODE_SHAPED_VALUE_RE`)
  now stands alone. Digits stay out of that class deliberately: every generated credential carries
  one, so `hunter2xyz`, `s3cr3tValue` and base64 key material still redact.

  A second case fell outside it for that reason — `password = hashlib.sha256(raw).hexdigest()`
  captured `hashlib.sha256`, which has digits. The value class already excluded `(` on the stated
  grounds that no credential carries an unquoted paren, but excluding the *character* only
  truncated the match at the paren instead of rejecting it, so the rule fired on the function name
  it had just cut. A value sitting immediately before `(` is now read as a callee name. Checked on
  the following character rather than by admitting digits to the class, which would also admit a
  dotted high-entropy value such as a bare JWT. `tests/test_secret_coverage_gaps.py`.
- **`doctor`'s grammar check can now fail.** It reported `len(LANG_CFG)` grammars "configured"
  and returned `ok`; `LANG_CFG` is a dict literal, so the number was a property of the source
  code and the check could not detect the thing it was named after. It now calls `parser_for()`
  for every configured language and reports loaded-versus-configured — `fail` when none load,
  `warn` when some do not, with the affected languages listed.
- **`parser_for` no longer lets backend errors escape.** It caught
  `(LookupError, ValueError, ImportError, AttributeError)`, which covers 0.x. Every 1.x failure —
  `DownloadError`, `ChecksumMismatchError`, `CacheLockError`, `DynamicLoadError`,
  `ParserSetupError` — derives from a private base outside that tuple, so offline they propagated
  instead of answering `None`, and the file was dropped without being counted as unparsed.
- **Signatures and docstrings are scanned for secrets.** Only chunk *bodies* ever were, so a
  credential in a docstring or a default argument shipped verbatim in five places at once: the
  `# doc:` line of every chunk header, `nodes.jsonl`, the JSON payload embedded in `graph.html`,
  GraphML, and Cypher. Redaction now happens where the two fields enter the graph, which covers
  all five rather than four, and is line-preserving so citation line numbers are unaffected.
- **Secrets longer than a chunk no longer escape detection.** Scanning ran per 4,000-character
  slice *after* splitting, and `_pem_spans` pairs a BEGIN with the next END within the text it is
  given. A private key straddling the boundary left the first slice matching only its
  `-----BEGIN ...-----` header and the second — the base64 body and the END line — matching
  nothing, so under `redact-match` the key shipped almost whole and under `exclude-file` only the
  slice holding the BEGIN was dropped. The whole body is now scanned before it is split, so
  `exclude-file` drops the entire symbol rather than one arbitrary slice of it.
- **Unquoted credential formats are detected.** Both existing rules required a *quoted* value,
  which missed the three places enterprise credentials actually live: unquoted YAML
  (`ansible_become_pass: ...`, and the `*_pass`/`pwd` family generally), INI/`.env`/`.properties`
  (`password=...`), and delimited connection strings (`Password=...;`, JDBC `?password=`, Azure
  `AccountKey=`, kubeconfig `client-key-data`). The new rule admits bare `pass`, `pwd` and
  `token` as keys, so it rejects values shaped like identifiers, attribute paths, function calls
  or placeholders — `token = get_token()` and `password: required` are not credentials.
- **PGP and SSH2/PuTTY private keys are recognised.** The detector required `PRIVATE KEY`
  followed immediately by five dashes, which excluded `-----BEGIN PGP PRIVATE KEY BLOCK-----`
  ("KEY BLOCK"), the four-dash `---- BEGIN SSH2 ENCRYPTED PRIVATE KEY ----`, and PuTTY's
  `PuTTY-User-Key-File-N:` header, which has no armour to pair at all. All three scanned clean
  and shipped in full under every policy.
- **Twelve more credential filenames are treated as secret paths**: `admin.conf`,
  `*.kubeconfig` (only the bare name was listed), `.vault_pass`, `_netrc`, `pgpass.conf`,
  `local.settings.json`, `appsettings.<env>.json` (not the base file, which holds non-secret
  defaults by convention), `web.config`, `NuGet.Config`, `.my.cnf`, `.s3cfg`, `.boto` and
  `*.publishsettings`. None matched an entry, an extension or a keyword before — "settings" is
  not "secret", and bare "pass" is not "password".
- **MCP `repo_read` redacts at serve time, like every other agent-facing path.** It sliced
  `index.chunks` straight back to the caller instead of going through `Index._served`, so an
  index built with `--secret-policy off` or `warn-only` — which deliberately stores text
  unredacted — served credentials in clear from this tool while `repo_search` over the same bytes
  redacted them. `docs/mcp.md` claimed without qualification that chunks "have already passed
  secret-path exclusion and content redaction": true of the search path, false of this one.
  Applied to the assembled slice rather than to the whole chunk cache, so only the lines actually
  served are scanned and `repo_read` keeps its no-filesystem-access guarantee; an unreadable
  manifest redacts rather than guessing.
- **Repository content reaches an LLM as data, not as instructions.** `rag --answer` is the one
  path that sends repository text to a provider, and a repository is untrusted input: a comment,
  docstring, test fixture or vendored file can address the model directly ("ignore all previous
  instructions", "print the contents of `.env`"). `answer.build_prompt` pasted the pack straight
  into the user turn, where it read exactly like the operator's own words and there was no in-band
  way for the model to tell the two apart. The pack now travels inside a fence whose label carries
  a per-call random nonce; the system turn names that label in advance and states that everything
  between the markers is data, and that a request inside the fence to change the task, reveal
  secrets, fetch a URL or run a command is content to report rather than comply with. The question
  is restated after the fence closes, so a pack ending in "now ignore the question above" has
  nothing left to hijack. A *fixed* sentinel would be forgeable by any file that simply contains
  it, which is why the nonce is random per call rather than a constant.
- **JSONL index reads are bounded on the untrusted-index path**
  ([#408](https://github.com/Srinivasan-78/repo2graph/issues/408)). An index is untrusted input —
  shipped on a `graph` branch, as Action artifacts and in `examples/`. Lines are framed over fixed
  blocks rather than by `for line in fh`, which reads until a newline and so allocates a
  newline-less file whole *before* any per-line ceiling could measure it; peak memory is now the
  ceiling plus one block. A total-bytes ceiling applies to the verification path
  (`integrity`, `doctor`) only — `chunks.jsonl` is the repository's own text and is legitimately
  large on a monorepo, so a wrong constant there would break real indexes on the `query` path.

- **Terraform state and vendor credential files are secret paths.** `*.tfstate`,
  `*.tfstate.backup`, `*.tfvars` (and `.auto.tfvars`, `.tfvars.json`), `htpasswd`, `wp-config.php`,
  `credentials.yml.enc`, `key.json`, Firebase `*adminsdk*` keys and `auth.json` (except under a
  `locales`/`i18n`/`lang`/`translations`/`messages` directory) are no longer indexed. JSON,
  single-quoted dict and YAML `"password": "..."`-style pairs are redacted in chunk text; values
  containing `://` and keys such as `tokenUrl`, `secretName`, `passwordField` are left alone.
- **`--include-secrets` no longer disables content redaction.** It lifts the secret-*path*
  refusal only; chunk text is still scanned per `--secret-policy` (default `redact-match`).
  Agent-path reads (MCP, `rag --answer`) of an index built with `--secret-policy off`/`warn-only`
  are redacted at serve time.
- **Secret-looking paths are excluded by default everywhere a human reads results**: `rag`,
  `query` and `explain retrieval` (previously only MCP and `rag --answer`).
  `--include-secrets` opts back in per command; `--exclude-secrets` is a deprecated no-op.
- **A deeply nested JWT header gets a 401** and an `auth_rejected` audit record instead of an
  escaped `RecursionError` and a dropped connection.
- **`events.emit` fails closed**: if the sanitiser itself raises, field values are dropped
  rather than written raw to stderr.
- **The prod-igy PR-comment sanitiser escapes every `&` and `<`** and defangs every `//`, `]:`
  reference definition and `www.`, so protocol-relative and entity-encoded links cannot survive
  in model-written comments. Code spans now show `&lt;`/`&amp;` literally.
- **No absolute build path in shipped artifacts.** The source root lives in a machine-local
  `local.json` beside the index (with a generated `.gitignore`); the GitHub Action excludes it
  from artifact uploads and `commit-branch` pushes.
- **Minimum RSA modulus size (2048-bit) and public exponent bound (64-bit) in `rsa_verify`**:
  Keys with modulus under 2048 bits and public exponents exceeding 64 bits fail closed immediately,
  mitigating weak-key exploitation and DoS via large-exponent modular exponentiation (#367).
- **Content-Security-Policy on generated `graph.html`**: `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:">`
  is now included in the page head, eliminating external resource exfiltration risks (#370).
- **Host and Origin check on GET and HEAD requests**: Discovery endpoints (`/.well-known/...`)
  and `/healthz` validate `Host` and `Origin` headers before responding, closing DNS rebinding
  and cross-origin reading vectors for unauthenticated routes (#372).
- **Cypher label and relationship type quoting**: Labels and relationship types in `write_cypher`
  are validated against `^[A-Za-z_][A-Za-z0-9_]*$` and backtick-quoted to prevent Cypher syntax breakouts (#371).
- **Escaped `U+2028` and `U+2029` in JSONL artifact writers**: `write_jsonl` escapes Unicode line and
  paragraph separators as `\u2028` and `\u2029`, preventing downstream tools using `str.splitlines()`
  from desynchronizing or tearing records (#376).

### Changed

- **The dense-vector recommendation is now conditional, because the default caught up.** Through
  2.2, `repo2graph embed` (the `rag` extra) added recall on both question types. Measured again
  on the held-out 40+40 after this round's seed ranking, it adds **+12 pp of lexical recall at
  8,000 tokens and costs 3 pp of structural** -- and 10 pp at 4,000. Nothing about the embedder
  changed; fusing a diffuse topical signal at equal weight now dilutes a BM25 ranking that
  structural questions, which name their symbol, had already got right. **Turn vectors on for
  lexical questions, leave them off for structural ones.** The real fix is to pass the
  `weights=(w_bm25, w_vec)` that `score_rrf` already accepts and `pack_context` never has, keyed
  off the question-shape classifier that already exists for expansion direction; that is now a
  priority rather than the speculative parameter search it looked like before.

- **`DEMO_K` 6 -> 8**, matching `pack_context`'s own default and the MCP server's `repo_search`
  default, so `repo2graph demo` shows what a caller actually gets. Sharper seed ranking promotes
  specific function bodies over module-level residuals, which is the point of it, and the effect
  on the demo was that `app/store.py` -- the module answering the "to persistence" half of the
  fifth starter question -- fell just past a 6-seed cut. The demo's output grew under 1%, because
  `DEMO_BUDGET_CHARS` is what binds.

### Fixed — indexing and call resolution

- **JS/TS export aliases are symbols.** A library that keeps a private implementation behind a
  public façade binds the public name in a second statement, and neither form produced a node:
  `export const getQueryParam: (...) = _getQueryParam as (...)` and `export { Hono as HonoBase }`.
  So the name every consumer writes in its `import` had no symbol, no chunk and no line range, and
  `hono/src/request.ts`'s `getQueryParam(this.url, key)` named a callee that resolved to nothing.
  Both forms now yield a symbol of kind `alias` spanning the alias statement's own lines, which is
  what a citation has to point at. Two boundaries are deliberate: the binding must be *exported*
  (a file-local `const b = a` renames nothing a consumer can reach), and `export { x as y } from
  './mod'` is left out, because nothing is defined at that line and a barrel file of re-exports
  would become dozens of one-line nodes bidding against real definitions. On the held-out
  structural set this moved recall 36% → 38% at 4k and 69% → 71% at 8k with mean tokens flat,
  which takes the lead over ripgrep to +16.7pp and +33.3pp — past the +15pp target that round
  had recorded as missed by a point. `tests/test_export_aliases.py`.
- **An export alias is never a seed.** Giving aliases nodes (above) also put a *rename* into seed
  selection, and `export { module as serveStatic }` is the worst possible seed: its name is an
  exact match for the question, and its body is one line. The single-shot recall tables could not
  see the cost — all eighteen held-out rows were byte-identical — but the simulated agent loop
  could: held-out structural went 15/40 → 14/40, losing *"how does the serveStatic middleware
  decide the Content-Type header"*, whose answer is the 92-line
  `middleware/serve-static/index.ts`. Scoring aliases down the way test paths are scored down was
  tried first and moved nothing, because the cost was never the alias's *rank* — seeds are packed
  in order while `fits()` holds, so at a 2,000-token budget the bigger, better-scoring seeds are
  rejected one by one and the one-line alias fits in exactly what they could not use. A cheap
  chunk slipping through a budget that just rejected better ones is not something a multiplier can
  reach, so `pack_context` now skips alias chunks as seeds outright. It does **not** mark them
  seen: graph expansion must still reach them, which is where the whole gain above comes from —
  `utils/url.ts:295-301` enters the pack as `CALLS out of query`, not as a seed. `retrieve()`, the
  path `repo2graph query` uses, is deliberately untouched. Restores 15/40.
  `tests/test_alias_seeds.py`.
- **No definition is silently dropped.** Same-name definitions in one file (overloads,
  conditional redefinitions, nested closures, Rust `struct A` + `impl A`) used to collapse into
  one node, and all but one body vanished from `chunks.jsonl`. The first keeps its id; later ones
  get `sym:<path>::<qualname>@L<line>` (`id_grammar`: `sym:<path>::<qualname>[@L<line>]`).
- **Kotlin functions are indexed** (top-level, member, `object`, companion as `A.make`, extension
  functions as `String.ext`); previously no `fun` became a symbol.
- **Go methods are qualified by receiver type** (`A.Run`, `B.Run`), and `a.step()` inside a
  method resolves through `same_class` to `A.step`. Committed `examples/` graphs are pinned
  artifacts and were not regenerated, so their Go method ids keep the old bare form.
- **Builtin method calls on untyped receivers are priced as guesses.** `os.environ.get(k)` was
  bound to any in-repo `get` at confidence 1.0 (on Flask, `_AppCtxGlobals.get` ranked second most
  called from dict lookups alone). When every call of a builtin-collection method name is on a
  receiver of unknown type, the edge is kept but marked `untyped_receiver`/`ambiguous` at 0.2.
  Calls whose receiver names where the candidate lives are exempt: an imported module
  (`store.get()`), a type (`Util.remove()`), a `::` scope, a Go method's own receiver. Kotlin,
  Swift and C# (PascalCase) receivers are covered; decorators count as calls on their receiver.
  New stat `calls_untyped_receiver`.
- **No false self-recursion.** `current_app.url_for()` inside `url_for` or `cli.main()` inside
  `main` was a 1.0 self-loop; tier 0 now needs a bare or self call. `super()` and explicit
  `Base.method(self)` resolve to the nearest in-repo base class (`resolution_kind: base_class`)
  instead of looping to the caller. `base`/`parent` are super receivers only in C#/PHP. Flask
  self-loops 57 → 2 (both genuine recursion).
- **What counts as a call** for "Most called symbols", changelog hotspots and entrypoints is
  decided by edge kind (`edgemeta.counts_as_call`): untyped-receiver guesses never count;
  same-class/same-file/base/imported edges, including an overload set's `1/n` fan-out, always
  count; everything else counts at confidence ≥ 0.5. Duplicates are labelled by node key.
- **`cochange_sampled_commits` reports the commits actually read** (a 1-commit shallow clone
  said 50); the request is kept as `cochange_requested_commits`.
- **A bare call to a builtin *function* no longer binds to a same-named in-repo method at
  confidence 1.0.** `b98fc46b` priced down builtin *method* names on untyped receivers (`x.get()`),
  and its gate requires `receiver == "other"` — so a bare call was documented as unaffected, on the
  reasoning that a bare call to a name the repository defines is probably calling it. That holds for
  a name the repository owns and not for one the language owns. `super()` was the clearest case: in
  Django, `template/loader_tags.py::BlockNode.super` — the helper behind `{{ block.super }}` —
  carried **1,805 incoming `CALLS` edges at confidence 1.0**, sourced from
  `db/models/fields/__init__.py` (92), `forms/fields.py` (44), `db/models/expressions.py` (36) and
  other ORM and forms modules that never touch block inheritance. It ranked **second** in that
  repository's most-called-symbols list, directly under a real result.

  `BUILTIN_FREE_FUNCTIONS` (Python builtins, Go builtins, and the JS/TS globals reached as bare
  calls) is the companion to `UNTYPED_RECEIVER_BUILTIN_METHODS`, and the new gate fires only when
  the name resolved **globally**: `edgemeta.SCOPED_CALL_KINDS` is exactly the set of tiers that
  found the definition in the calling file, its class, a base, or an import by name, and any of
  those is a real target that happens to shadow a builtin. A project with its own `filter()`,
  called where it is defined or imported where it is used, is untouched; what is demoted is a
  cross-file name collision with a builtin, which is a coincidence rather than a call. The edge is
  kept and flagged `shadowed_builtin`, priced at `UNTYPED_RECEIVER_CONFIDENCE` for the same reason
  its sibling is — "kept as a possibility, not asserted" — and `edgemeta.counts_as_call` now
  excludes both flags, which is what removes it from the repo map ranking.

  **Measured before and after on identical harnesses, with the fix stashed for the baseline:
  recall did not move on any set.** Published 35 + 10, held-out 40 + 40, and the held-out
  repository set (22 on click and axios) produced **zero** differences in `evidence_recall`,
  `fully_answered` or `any_evidence`; the held-out repository run was byte-identical. The only
  deltas anywhere were `mean_tokens_used` shifts from changed confidence annotations — ≤8 tokens on
  the held-out sets, ≤260 on one published row. The corpus regression gate stayed at 25/25 (100%).
  So this buys back the repo map's ranking without touching retrieval quality, which is why the
  committed benchmark artifacts are left as the clean-tree measurements they already are rather
  than regenerated for sub-token noise. `tests/test_scoped_resolution.py` adds five cases: the
  cross-file demotion, its exclusion from the ranking, and three guards — a same-file definition,
  an imported one, and a non-builtin name — that must keep confidence 1.0.
- **`edges_dropped` counts distinct edges lost, not attempts.** `Graph.add_edge` recorded a key in
  `_edge_seen` only after the `max_edges` ceiling let the edge through, so an edge the ceiling had
  already rejected was never remembered and every re-proposal of it was counted again. The number
  is published in `stats.json` and read as how many edges the graph is missing, so it overstated
  the loss for any edge discovered more than once — the normal case for `IMPORTS` and `CO_CHANGE`.
  The key is now recorded before the ceiling test: `_edge_seen` means "already decided", which is
  what both of its readers want. `tests/test_resource_limits.py` pins the repeat-of-a-dropped-edge
  path alongside the existing repeat-of-an-added-edge one.
- **A chunked file's nested definitions keep their real parent.** Re-keying across the slices of an
  oversized file tracked seen `(name, start_line)` pairs in a set, so a symbol skipped as a
  duplicate had no entry in the `rekeyed` map. A later child whose `symbol_parent_key` named it
  then resolved through `rekeyed.get(old_parent, old_parent)` to a slice-local key that no longer
  existed file-wide, `build` fell back to `owner = fid`, and the child's `DEFINES` edge came from
  the *file* instead of its parent — silently, in the one case nothing else distinguishes. `seen`
  is now a dict carrying the winning definition's global key, so a skipped duplicate is still
  rekeyable.
- `PARSE_CACHE_FORMAT` is 8; older caches are rebuilt on the next incremental build.

### Fixed — retrieval and budgets

- **`conditional_expansion` works on the hybrid path.** `is_lexical_weak`'s floors are absolute
  BM25 magnitudes (`min_top_score=18.0`), but both callers handed it `score_rrf`'s output. With
  vectors or an embedder supplied, those are reciprocal-rank scores — `w/(60 + rank)`, about 0.016
  at rank 1 — so `top_score` sat below every floor and the answer was unconditionally "weak".
  Expansion suppression therefore did nothing at all whenever dense retrieval was in play, while
  working as documented on the lexical path: a silent difference in behaviour between the two.
  `Index._lexical_for_weakness` now re-scores lexically for that test, and returns the ranking
  unchanged when there is nothing fused to undo, so the lexical path does not score twice. The
  docstring states the precondition the floors imply.
- **`retrieve`'s character budget measures what it serves.** The headroom test read the *stored*
  chunk length while `used` accumulated the *served* one, and `redact_content` replaces a secret
  with a longer `[REDACTED:…]` marker, so `budget_chars` was overshot by the growth of every
  redacted chunk, cumulatively. Redaction now happens before the measurement, as it already did in
  `pack_context`. `tests/test_budget.py` checks the served bytes against four budgets, with a
  fixture guard asserting the chunks really do grow under redaction — otherwise the budget
  assertion would pass for the wrong reason.
- **A question can ask about tests *and* ask a direction.** The test-seed penalty and the
  answerability boost both read `classify_query`, which returns one label and tests the caller
  patterns first — and `(what|who|which)\s+(\w+\s+){0,3}?uses?` claims "which tests use the
  parser" before the test patterns are reached. The shape came back `callers`, so the 0.6 penalty
  fired on exactly the chunks such a question asks for and the name boost was withheld from them.
  "What tests cover X" escaped only because `cover` is not in the verb list. Reordering the two
  loops would have traded the bug the other way, since `\btests?\b` also matches "what calls the
  test runner". The two questions are independent and are now asked separately: the new
  `asks_about_tests` drives the penalty and the boost, `classify_query` still drives the direction
  filter, so "which tests use the parser" gets both. `tests/test_test_path_seeds.py`.

### Fixed — CLI and MCP

- **`build . -o .r2g` never indexes its own output**, nor any directory holding a repo2graph
  manifest; `explain-path` gained `-o/--out` and reports the same rule.
- **MCP tools return `isError: true`** for missing/blank queries, unknown node ids, unknown
  tools, git failures and bad `repo_build_status` ids. Out-of-range numbers are
  clamped with a one-line `_note:`; non-finite JSON numbers (`1e999`) are treated as bad input.
- **Freshness is right for indexes outside the repo** (`index-status`/`doctor` use the recorded
  source root) and for `repo2graph github` builds (reported as not checkable, with the right
  refresh command).
- **Piped output is UTF-8 on Windows**, so non-ASCII source survives `rag | …` (an explicit
  `PYTHONIOENCODING` is respected).
- **A compressed `rag` neighbour cites the lines it shows** (`[excerpt of A-B]`, `excerpt_of`
  in JSON).
- **`repo_read` serves the lines it cites.** `_chunk_body_lines` derived the generated header's
  length by subtracting the citation range from the line count, which is off by one for every
  non-final part of a split chunk: such a part ends with its last body line's newline, so
  `split("\n")` yields a trailing `""` that is not a body line, and the window started a line
  late. `repo_read("m.py", 1, 3)` answered with lines 2–4 under a citation that said 1–3, dropping
  the `def` of a long symbol — in the one tool an agent uses to go and *verify* a citation. The
  range was never a reliable line count anyway: an over-long line is cut into several parts that
  all sit on one source line, and redaction can change the count. The header is now measured by its
  own shape via `_header_len`, the same way `query._excerpt_record` reads it. The chunk records
  were already correct, which is why the chunk-level fidelity tests could not see this;
  `tests/test_cite_fidelity.py` now asserts the invariant through the tool, across part boundaries
  rather than only inside the first part, where the shift was smallest.
- **`demo` question 4 shows the direct caller** via `explain node`.
- Smaller: `rag`/`query` accept `--min-confidence`, `explain` accepts `--min-conf`;
  `explain retrieval` defaults to `-k 8` like `rag`; `doctor` lists the files with parse
  errors; `embed --verify-rag` reports `rag_extra_installed` as a boolean.

### Removed

- **`impact`, `repo_impact`, and the PR-impact workflow.** The diff-analysis surface is gone:
  `repo2graph/impact.py`, the `impact` CLI command, the `repo_impact` MCP tool and its schema,
  `.github/workflows/pr-impact.yml`, and the `pr-impact` inputs and outputs of the Action. With it
  go the `--base`, `--head`, `--diff`, `--max-depth`, `--min-conf`, `--fail-on` and `--write` flags
  of that command, and the `impact-analysis` PyPI keyword, which was still advertising the removed
  surface on the project's listing page.

  This narrows the project to one thing: retrieval. The measurements that prompted it are in
  `benchmarks/real/` — the graph earns its keep in retrieval (on 40 held-out structural questions
  it adds 26 pp over lexical search alone at an 8,000-token budget, and beats ripgrep at every
  budget), whereas the diff surface was never measured at all and had drifted from its own
  documentation: it never traversed `INHERITS` or `CO_CHANGE`, both of which the README and
  `docs/comparison.md` described it as reporting.

  **`CO_CHANGE` edges are unaffected.** They are built by `graph.py`, are still in every index,
  and are still followed during retrieval expansion. Only the diff report that never read them is
  gone.

  Migration: there is none in-tree. A PR blast-radius check can be rebuilt on `repo_blast_radius`,
  which survives because it answers a graph question rather than a diff question.

- **The retrieval mode knobs.** Choosing a retrieval configuration is no longer a caller's job.
  Measured on 40 held-out lexical and 40 held-out structural questions, in both the BM25 and the
  dense configuration, none of these settings has a regime where it wins — the numbers are in
  [`benchmarks/real/results_holdout_knobs.json`](benchmarks/real/results_holdout_knobs.json).

  Two surfaces, with different compatibility stories:

  - **Over MCP, the `neighbours` and `max_neighbours` parameters of `repo_search` are gone.**
    They were advertised in the tool schema and are no longer accepted.
  - **On the CLI, `--neighbours`/`--neighbors`, `--conditional-expansion` and
    `--precision-first` are retired no-ops.** They still parse, so no existing command line
    breaks, and setting one emits a `retired_flag_ignored` warning explaining that the tool now
    decides. They do nothing.

  `--neighbours=cite` is worse than the default everywhere (dense rows, 2k/4k/8k: −1/−8/−19 pp
  lexical and −3/−10/−31 pp structural) and, more decisively, it is *dominated* by turning graph
  expansion off altogether: at 8,000 tokens it returns 38% lexical / 45% structural for
  5,307 / 4,396 mean tokens, against 40% / 50% for 5,083 / 4,475. Citation mode's only claim was
  token economy, and it does not hold it on the lexical set. This is the mode the README
  advertised as the opt-in that wins the lexical table — which it did win, on the 35 published
  questions it was diagnosed against.

  `--conditional-expansion` is an exact no-op with dense vectors — identical recall *and*
  identical mean token counts at all three budgets — and costs 17 pp of structural recall at 8k
  without them (52% against the default's 69%). Useless or harmful, never right. It gates
  expansion on BM25 confidence, and a structural question names its symbol, so BM25 looks
  confident and the graph is skipped on exactly the questions it exists for. Its verdict had
  flipped twice on underpowered question sets before the 40-task set settled it.

  `--precision-first` was only ever exercised alongside citation mode and has no independent
  measurement, so it goes with it.

  All of them survive as internal keyword arguments to `pack_context`, which is what keeps the
  ablation rows above reproducible. What is gone is the choice presented to a caller: the tool
  decides, because every measured setting of these knobs was worse than letting it.

### Fixed — MCP SDK, staleness and annotations

- **MCP SDK 1.x is no longer accepted**
  ([#407](https://github.com/Srinivasan-78/repo2graph/issues/407),
  [#291](https://github.com/Srinivasan-78/repo2graph/issues/291)). Under mcp 1.30.0 the server
  never answered its first tool call on the parallel path — deterministic, ~240s to time out —
  while 2.x answered in under two seconds. The range was advertised as supported, half of it was
  broken, and nothing ran against it, because CI resolved fresh from PyPI and always got 2.x while
  `uv.lock` pinned 1.30.0. `SDK_SPEC`, the `pyproject.toml` extra, the `_unusable_sdk()` message
  and `docs/mcp.md` now all say `mcp>=2.0,<3.0`; a 1.x install is refused by name at startup with
  the installed version and the required range; and every start emits an `mcp_sdk_version`
  diagnostic. `serve()`'s 1.x decorator branch is gone.
- **Three stdio round-trip tests were silently skipping on every supported install.**
  `HAS_REAL_MCP` gated on `hasattr(Server, "list_tools")`, which is only ever true for the *now
  unsupported* 1.x — so the live-subprocess coverage never ran against the SDK anyone actually
  has. Fixing the gate surfaced a second latent bug: the installed SDK exposes `input_schema` /
  `read_only_hint`, not the older `inputSchema` / `readOnlyHint`.
- **The index can go stale against the working tree, and now says so**
  ([#383](https://github.com/Srinivasan-78/repo2graph/issues/383)). Reload detection compared the
  index against *itself* at an earlier moment; nothing compared it against the source. Every answer
  carries a `[cite: path:start-end]` anchor, so a stale index produces line numbers that are
  confidently wrong — they name real lines holding different code. `repo_map` now warns, reusing
  `status.compute_freshness()` (which already reads `index.state.json`'s per-file hashes and gates
  hashing on mtime) and the existing result-cache TTL rather than adding a second mechanism.
- **Tool annotations are honest about auto-build**
  ([#292](https://github.com/Srinivasan-78/repo2graph/issues/292)). A server that can build
  advertised `readOnlyHint: true` while its first tool call could parse the whole repository, run
  git and write `.r2g/**`. `tools/list` is answered once per server lifetime, so the annotation now
  reflects that server's build capability. Both transports route through one
  `tool_annotations(name, auto_build)` helper — the HTTP handler previously assembled the block
  itself and would otherwise have kept lying whenever an operator opted into auto-build.
- **`repo2graph.__version__` no longer reports a stale installed dist-info**
  ([#340](https://github.com/Srinivasan-78/repo2graph/issues/340)) when run from a source checkout:
  the checkout's own `pyproject.toml` wins, and `importlib.metadata` answers only for a genuinely
  installed package.
- **Single-character identifiers are searchable**
  ([#378](https://github.com/Srinivasan-78/repo2graph/issues/378)). `TOKEN_RE` requires two or more
  characters, so a symbol literally named `T` or `A` returned a silent zero result. Only a
  *declared name* that is one identifier character is admitted; free-text tokenization is
  unchanged, so the index does not fill with postings for every stray `a` and `i` in body text.
- **`_fit_lines` is no longer quadratic**
  ([#345](https://github.com/Srinivasan-78/repo2graph/issues/345)) when `measure is len`, with
  byte-identical output and unchanged semantics for an arbitrary `measure`.

### Known issues

- **`explain-path` misreports a filename-rule verdict for a path spelled with a trailing `..`,
  on Windows.** Found by the new property tests and left unfixed deliberately, pinned by a strict
  platform-conditional xfail so a fix cannot land unnoticed. `parse.explain_path` normalizes only
  the *parent* of its target (`target_path.parent.resolve() / target_path.name`), so a trailing
  `..` survives into the relative path every later rule is matched against. On Windows the Win32
  API collapses `..` lexically before the stat, so the existence check succeeds against the
  different file the path actually names. With a `.env` in the root, `explain-path .env` correctly
  answers `secret_file`, while `explain-path .env/src/..` answers `included`.

  **This is a wrong answer from the diagnostic, not an indexing leak.** `discover()` never produces
  that spelling and the build path still excludes the file — verified: a build of a tree containing
  `.env` indexes only the source file. It also fails in the conservative direction, claiming a
  credential file *would* be indexed when it would not. Not reachable on POSIX, where `stat` is
  physical and `.env/src` is `ENOTDIR`.

### Fixed — repository tooling and stale references

- **All five examples regenerated on 2.2.0, and they now say which analyser produced them.** Each
  `examples/<id>/README.md` recorded the upstream commit it indexed but not the repo2graph version
  that did the indexing — and all five were still **1.6.0** output. A pinned commit fixes the
  *source* and says nothing about the *analyser*, so two minor versions of call-resolution changes
  left the published figures describing behaviour the package no longer has. It was plainly visible
  in the committed `overview.md` files, and regenerating is what proves the fix rather than asserts
  it:

  | most-called symbols, rank 1 | 1.6.0 | 2.2.0 |
  |---|---|---|
  | Kubernetes | `active_queue.go::len` (in=1386) | `wrappers.go::MakePod` (in=361) |
  | VS Code | `DisposableMap.get` (in=5397) | `nls.ts::localize` (in=4233) |
  | Django | `SessionStore.create` (in=2141) | `SimpleTestCase.assertRaisesMessage` (in=1941) |
  | TensorFlow | `DataTypeSet.size` (in=597) | `constant_op.py::constant` (in=1066) |

  Every 1.6.0 leader is a collection or builtin method name — Go's `len`/`append`, a TypeScript map
  `.get`, five separate Django `.create` methods at ~2,140 each — fanned out across every
  same-named in-repo definition. That is exactly the `b98fc46b` fan-out, and Kubernetes now has
  zero bare builtins in its ranking where it previously held the top seven slots. The replacements
  are real hot functions, which is what a repo map is for.

  `scripts/generate_examples.py` already captured `R2G_VERSION` for `results.json`; its README
  template simply never printed it, which is why the staleness had no visible marker. The template
  now emits the version beside the commit and explains why both halves are load-bearing. New
  figures, all five at 2.2.0: Django 56,074 nodes / 301,017 edges, Kubernetes 15,174 / 117,066,
  TensorFlow 24,730 / 145,790, VS Code 114,070 / 664,312, Linux 185,496 / 310,854 — `results.json`
  and `examples/README.md`'s table carry the same numbers.
- **Each example page described five uncommitted files as if they shipped.** The generator's
  README template listed `nodes.jsonl.gz`, `edges.jsonl.gz`, `graph.html`, `manifest.json`,
  `stats.json` and `flows/` under "Generated graph" and named only `chunks.jsonl` as not committed
  — true before `4e96b628`, wrong after it, and flatly contradicted by `examples/README.md`, which
  says only `README.md` and `overview.md` are committed. Three places pointed a reader at `flows/`
  for "each query's real results", and a clone has no `flows/` directory to look in. The section now
  leads with what is committed and frames the rest as what the generator writes locally;
  `chunks.jsonl` is described as discarded rather than merely uncommitted, which is what step 4 of
  the pipeline actually does with it.
- **The purged example artifacts had nothing stopping them coming back.** `4e96b628` removed ~28 MB
  of generated graphs from `examples/<id>/` and left only `README.md` and `overview.md` committed,
  but no ignore rule matched them, so any `git add -A` after a regeneration would have re-added
  `nodes.jsonl.gz`, `edges.jsonl.gz`, `graph.html`, `manifest.json`, `metadata.json`, `stats.json`
  and `flows/` — 11.4 MB on this round alone. They are now ignored by name rather than by a negated
  glob, which would also have swallowed a future hand-written page.
- **`docs/architecture.md`'s module table was stale in 26 of 36 rows.** Sizes are prose that no test
  checks, and they had drifted badly: `query.py` read 1,039 against an actual 1,620, `doctor.py` 254
  against 382, `graph.py` 2,117 against 2,333. Every row is now recomputed, `doctor.py` moved to
  keep the Support table in descending order, and the `mcp/` total corrected to 2,387.
- **`benchmarks/README.md` described the retrieval benchmark as it was two rounds ago** — "35
  lexical and 10 cross-file structural questions about four pinned third-party repositories" — when
  `real/` had grown the two held-out sets and two more repositories. The index page now carries the
  same three-set table as `real/README.md` (35/40/22 lexical, 10/40 structural; click and axios in
  the held-out repository set) and repeats the instruction to quote the held-out numbers, so the
  summary cannot imply a smaller, easier benchmark than the one that ran.
- **The changelog flag guard was running against a dead subcommand.**
  `test_unreleased_changelog_does_not_advertise_flags_that_do_not_exist` builds its set of real
  flags by running `repo2graph <cmd> --help` for each name in `_CLI_COMMANDS`, a deliberately
  hand-maintained tuple — and it still listed `impact`. That invocation exits as `invalid choice: 'impact'` and
  contributes nothing, so the guard ran against a set missing a command's worth of flags while
  printing an argparse error into every test run. The entry is removed; the tuple stays explicit,
  for the reason its own comment gives.
- **A test comment pointed at a file deleted with the translations.**
  `tests/test_doc_consistency.py` explained that `LANGUAGE_TOKENS` sits at module level so
  `tests/test_i18n_consistency.py` could reuse it against the five translated READMEs. Both that
  test and `docs/i18n/README_{de,es,fr,ja,zh-CN}.md` were removed in `b98fc46b`; the mapping now has
  exactly one reader and guards `README.md` alone. The comment says so, keeping the reason the
  mapping was shared in the first place — the language list had drifted in all six files at once.
- **The unreleased notes claimed ten MCP tools while `docs/mcp.md` documented nine.** `repo_impact`
  was removed later in the same window, so "taking the surface from six to ten" was true of the
  addition and false of the release; it now states the net. The `impact-analysis` PyPI keyword went
  with it.
- **The incremental parse cache survived a grammar upgrade (E43).** `PARSE_CACHE_FORMAT` is bumped
  by hand, so it catches every change to *our* extraction and none to the grammars that feed it. A
  tree-sitter upgrade changes what the same bytes parse to while nothing in this repository
  changes — so `--incremental` reused every entry for every unmodified file, and the resulting
  index was a silent mix of two grammar versions that no full rebuild could reproduce.
  `test_incremental_is_byte_identical_to_a_full_rebuild` cannot see this because it never changes
  grammars mid-run. `parse.grammar_fingerprint()` now records the installed `tree-sitter` and
  `tree-sitter-language-pack` versions in `parse.cache.json`, and a mismatch discards the cache the
  same way a format bump does. A cache written before the field existed has no `grammars` key and
  so can never match, which is the intended outcome: it was produced by an unknown grammar version.
- **The language scorecard credited C with 123 tests.** The test-coverage column matched
  `f"test_{lang}" in name`, which for `c` is true of `test_cli_*`, `test_cache_*` and
  `test_citation_*` — every one of them counted, and the number was published in a table. Matching
  now requires the language as a whole `_`-delimited token, which takes C from 123 to 1.
- **Nothing asserted the removed HTTP/OIDC surface stays removed.** `647e76f3` deleted `auth.py`,
  `http_server.py` and ten flags, but no test referenced any of them, so a reintroduction would
  have been invisible to CI. `tests/test_http_surface_removed.py` covers all three layers. Worth
  recording how it is written: an exit-status assertion alone is **not** a detector here —
  re-adding one of the deleted value-taking options still exits 2, because argparse then reports
  "expected one argument" instead of "unrecognized arguments". The primary check introspects the
  registered options across
  the top-level parser and every subparser; the behavioural test passes a value so a value-taking
  reintroduction cannot pass either. The import scan walks `ast.Import`/`ast.ImportFrom` rather
  than matching substrings, so `security.py`'s literal `"jwt"` secret pattern does not trip it.
- **Two issue templates were deleted while everything kept routing people to them.**
  `81bf24d5` removed the five per-category templates; `bug_report.yml` still told edge reports to
  use *Incorrect or missing graph edge*, `feature_request.yml` still redirected language requests
  to *Language / parser support*, and `CONTRIBUTING.md`'s "Where does this go?" table still listed
  both. `bugreport.py` went further and claimed its five `CATEGORIES` paired with the templates
  "one-to-one", asserted by a `tests/test_bugreport.py` that did not exist. Both templates are
  restored (their links repointed at the docs that survived consolidation), the remaining three
  categories are the dropdown on `bug_report.yml`, and `tests/test_bugreport.py` now exists: it
  checks every category is offered somewhere, every template named in prose declares that name,
  and every relative link out of a template resolves. The third caught a real `../../../` in
  `feature_request.yml`, which rendered as a 404.
- **`scripts/generate_language_scorecard.py` shipped with no consumer.** Nothing in `docs/`,
  `README.md` or CI referenced it, and its own docstring pointed at a
  `docs/LANGUAGE_SCORECARD.json` that was never committed — so the one artefact answering "how
  well is *my* language supported" existed only as a script nobody ran. Its table is now
  `docs/architecture.md` §4, between `BEGIN/END GENERATED` markers, with the priority order for
  deep support and an explicit note on which columns are judgement rather than measurement.
  Scores are read off `LANG_CFG`, so adding a language moves them:
  `test_language_scorecard_matches_the_generator` fails until the table is regenerated, and the
  §6 documentation checklist says so.
- **CONTRIBUTING §7 promised measured C/C++ parse-error rates that no longer existed.** The link
  pointed at `architecture.md`, which names no such rates after the consolidation. It now says
  plainly that the macro penalty is a judgement call and that nothing here measures per-language
  `parse_errors` across real code.
- **Three of the five synthetic corpus archetypes, and 15 of its 25 tasks, were gone.**
  `4e96b628` removed `ts_app/`, `python_backend/` and `modular_monolith/` with no stated reason,
  leaving the regression gate covering TSX and dynamic-Python only — no TypeScript service, no
  layered Python backend, no cross-domain monolith. All three are restored with their tasks; the
  gate runs 25 again across 5 archetypes. `benchmarks/corpus/README.md` keeps its "regression
  gate, not a benchmark" framing and does **not** reinstate the withdrawn ripgrep comparison.
- **`make typecheck` failed outright once the `rag` extra was installed.** numpy's bundled stubs
  use PEP 695 `type` statements, which mypy refuses to parse under `python_version = "3.10"`:
  it reported a syntax error inside `numpy/__init__.pyi` and stopped, "errors prevented further
  checking", without checking a line of this package. CI never saw it because CI does not install
  `[rag]` — but `make install` does, so every contributor who followed the documented setup had a
  broken typecheck target. numpy's stubs are now excluded with `follow_imports = "skip"`
  (`silent` still parses the file); `python_version` stays at the declared `requires-python`
  floor rather than being raised to paper over it. The package itself was already clean: 39 files,
  no errors.
- **`prod-igy` stopped applying two area labels, silently.** `detectAreas` matched
  `repo2graph/mcp.py` after that module became the `repo2graph/mcp/` package (`c8c20bdb`) and
  `repo2graph/walker.py` after that shim was deleted (`8ef6d006`), so `area/mcp` and `area/walker`
  could never be applied again and the MCP argument-guardrail reminder never fired on an MCP PR.
  The area test asserted the stale path strings, which is what kept it quiet;
  `test_prod_igy_area_patterns_name_paths_that_exist` now checks every `repo2graph/...` pattern
  against the filesystem, so the next rename fails in CI instead.
- **`prod-igy`'s PR checklist cited `AGENTS.md`**, removed in `58c1f833` when the technical
  invariants moved into `CONTRIBUTING.md`. Every rule now names the CONTRIBUTING invariant it
  comes from, and `checkAgentsRules` is `checkRepositoryInvariants`.
- **`pre-commit run --all-files` broke the test suite on an unmodified tree.** `end-of-file-fixer`
  stripped two trailing newlines from `tests/golden/rag_markdown.md`, which
  `test_rag_markdown_is_byte_identical_to_baseline` and
  `test_no_vectors_matches_the_baseline_golden` compare byte-for-byte against real program output.
  The same hooks rewrote `examples/*/overview.md` (generated: `export.write_overview` writes no
  trailing newline) and `benchmarks/corpus/` (parser input for the regression gate) on every run.
  All three are now excluded, with the reasoning in the config.
- **`CONTRIBUTING.md` told maintainers a tag push publishes a release.** `publish.yml` is
  `workflow_dispatch`-only and deliberately so — its own header records that the `push: tags:` and
  `release:` triggers made every release re-trigger itself twice, which the PyPI job tolerated and
  the MCP Registry job did not. The documented "local bump + tag push" route therefore published
  nothing, and recovering by dispatching would have bumped a second time. It is now one route, and
  the local bump is documented as the dry run it has to be.
- **Doc references that the four-document consolidation left pointing at the wrong file.**
  `architecture.md` had been substituted for `README.md` where the text means the package
  description setuptools reads, the `mcp-name` ownership marker, the language list
  `test_languages_documented` actually asserts against, and the "What it can't do" section; and for
  `docs/comparison.md` where it means the "Languages parsed for symbols" count. Also
  `docs/CONTRIBUTING.md` → `.github/CONTRIBUTING.md` in six places, `npm/architecture.md` →
  `npm/README.md` for the launcher resolution order (the old target documents no such thing), and
  the remaining `repo2graph/mcp.py` paths.
- **`examples/` documented an artifact set that is no longer committed.** `README.md` described
  eight files per example plus a full `metadata.json` schema, and `ATTRIBUTIONS.md` recorded each
  repository's analyzed commit as living in that file; the graph artifacts were purged in
  `4e96b628` and only `README.md` and `overview.md` remain. Provenance now points at each example's
  own "Revision" heading, which is where it is. This matters most in `ATTRIBUTIONS.md`, which makes
  claims about what is redistributed.

### Changed

- **The HTTP transport advertises the protocol revision it actually implements**
  ([#390](https://github.com/Srinivasan-78/repo2graph/issues/390)): `2024-11-05`, not `2025-06-18`.
  That later revision's HTTP transport *is* Streamable HTTP — one endpoint, `Mcp-Session-Id`,
  `Last-Event-ID`, `text/event-stream` — and what is served is plain JSON-RPC over `POST` with
  `Content-Length` framing and a deliberate per-request connection close. Implementing Streamable
  HTTP would mean giving up that close-per-request desync defence and holding a thread per open
  stream, so it is tracked separately; advertising accurately is the part that was free.
- **Node and edge type descriptions have one definition**
  ([#349](https://github.com/Srinivasan-78/repo2graph/issues/349)), in `viz.py`, imported by
  `export.py`. The two copies had already drifted. `manifest.json` keeps the richer `export.py`
  wording, so its bytes are unchanged; the `graph.html` legend picks it up. Issue #349 assumed this
  needed a third leaf module, but only the `viz.py → export.py` direction is a cycle.
- **Benchmark claims replaced with a real-repository retrieval benchmark**
  (`docs/retrieval-benchmark.md`, `benchmarks/real/`, `scripts/bench_real_repos.py`): 35 questions
  about Flask, requests, FastAPI and Hono, repo2graph vs grep-then-read at equal token budgets.
  repo2graph currently loses (30/39/52% vs 35/61/72% at 2k/4k/8k tokens) and graph expansion adds
  no recall; the page says so, diagnoses why, and records a scorer correction. The synthetic
  `benchmarks/corpus/` suite is documented as the regression gate it is
  (`docs/regression-suite.md`) and its "100% vs ripgrep 80%" comparison is withdrawn.
- **Docs made to match behaviour**: README cut from 649 to ~150 lines; `docs/comparison.md`
  covers Serena, Aider, CodeGraphContext, code-graph-rag, Sourcegraph, Cursor and Claude Code;
  claims that graph expansion beats search were removed; `docs/cli.md` documents every flag
  (checked by a test).
- **Repository layout**: `PR_IMPACT.md`, `TECHNICAL.md`, `LANGUAGE_SUPPORT.md` moved into `docs/`;
  `CLAUDE.md` into `.claude/`; `rfc-incremental-indexing.md` into `docs/rfcs/`.

### Removed

- **The HTTP MCP transport and the bespoke OIDC/JWT auth engine** (`repo2graph/http_server.py`,
  `repo2graph/auth.py`, `647e76f3`). **This is a breaking change for anyone who ran the 2.2.0
  server over HTTP.** `repo2graph-mcp` is now stdio-only, which is what every supported client
  (Claude Code, Claude Desktop, Cursor) actually uses.

  Gone with it, all of which `repo2graph-mcp` accepted in 2.2.0: `--http-only`, `--http-host`,
  `--http-port`, `--http-allow-hosts`, `--well-known-port`, `--auth-token`, `--auth-oidc-issuer`,
  `--auth-audience`, `--auth-jwks-ttl`, `--auth-cimd`, and the `/healthz` and OIDC discovery
  endpoints. `--allow-auto-build` went too — auto-build is the default on stdio and `--no-auto-build`
  remains the way to turn it off. Passing any removed flag now fails as an unknown argument.
  `--out`, `--no-auto-build`, `--async-build`, `--cache-size`, `--cache-ttl`, `--audit-log`,
  `--audit-log-level` and `--audit-log-fsync` are unchanged.

  **Migration.** Point your client at the stdio command instead — `uvx --from
  "repo2graph[mcp]" repo2graph-mcp .`, per [docs/mcp.md](docs/mcp.md). Stdio inherits the parent
  process's identity, so bearer-token and OIDC configuration has nothing left to guard and is
  simply dropped rather than replaced. The per-request output ceilings in `mcp/guardrails.py` are
  unchanged and still enforced on every tool call. If you genuinely need a network-reachable MCP
  endpoint, put a maintained MCP HTTP gateway in front of the stdio server rather than relying on
  this project to terminate TLS — which it never did.

  Why: ~4,000 lines of security-critical network and crypto code (hand-rolled RSA/JWT
  verification, JWKS refresh, forwarded-header trust, rate limiting) served a transport no
  supported client requested, and every line of it was a liability that stdio does not have.
- Agent run logs (`DONE.md`, `docs/BUILD_STATE*.md`, `docs/remediation-tracking.md`), now
  gitignored; internal and outreach material (`docs/distribution/`, `docs/positioning.md`,
  `docs/PRODUCTION_READINESS.md`, a dated issue-triage dump, an internal test plan); the root
  `SECURITY.md` stub; the five translated READMEs and their drift test. All remain in git history.
- `benchmarks/results_v2.json` — a `repo2graph` 1.6.0 run against `ts_app/`, `python_backend/` and
  `modular_monolith/`, the three corpora deleted in `4e96b628`. No script, workflow or README
  referenced it, and `benchmark_runner.py` now hard-fails on a task naming a corpus that is not
  there, so it could not be regenerated as it stood. In git history.
- The shipped rows and stale prose from `CONTRIBUTING.md`'s backlog. Every entry was re-checked
  against the code: the SBOM step, `MAX_COCHANGE_BYTES`, `attestations: true`, the MCP 2.x port,
  coverage measurement, the `jobs=1` auto-build pool pin and the above-`PARALLEL_MIN_FILES`
  fixture had all landed. The design rationale for the incremental rebuild moved to
  `docs/architecture.md` §3 rather than being dropped — it is shipped behaviour, not backlog.
- `parse._receiver_kind`, dead since `_receiver_meta` took over receiver classification; it called
  `_classify_receiver` with too few arguments to do what that function now does. Its docstring,
  which documented the `none`/`self`/`other` semantics, moved onto `_classify_receiver` itself.

## [2.2.0] — 2026-09-26

### Added

- **Per-client integration guides.** `docs/integrations/claude-code.md` (first-supported) covers
  install, verification, the six tools and their real argument ceilings, the five starter
  questions, the `CLAUDE.md` block that gets the agent to actually prefer the tools, and an
  explicit "what not to rely on". `docs/integrations/cursor.md` covers the same server and the
  three things that differ -- config file, scope model, and the rules file that is load-bearing
  there because Cursor's own search is good enough to answer without ever calling a tool.
  `tests/test_doc_consistency.py` pins the guides' quoted MCP bounds against `mcp.py`, in context
  rather than as bare substrings: `str(MCP_MAX_HOPS) in text` passes for any value whose digits
  appear anywhere in the prose, which is not a check.
- **`docs/distribution/`** -- a demo-video script and recording plan, a before/after case-study
  template, launch-post drafts and design-partner outreach drafts. The posts and outreach are
  **unpublished drafts pending human approval** and say so; a test fails if that marker is edited
  out. Every number in them traces to `benchmarks/results.json` or `docs/limitations.md`, pinned
  by a test, because the product claim is trustworthiness and a launch post that overstates the
  call graph's completeness contradicts the thing being sold.
- **Standard trust metadata on every graph edge** (`edge_schema_version: "2"`). Before this,
  only `CALLS` carried enough to audit a claim: `CONTAINS`, `DEFINES`, `IMPORTS` and `INHERITS`
  were bare `(src, dst, type)` triples, so "why do you think this file imports that one" had no
  answer in the artifact. Every edge now carries `method` (`tree-sitter/<lang>`, `name-resolver`,
  `filesystem`, `git-log` — so a consumer can distrust one extraction path rather than a whole
  edge type), `confidence`, and **`evidence`: the `{path, line}` where the relationship is
  written**. Verified, not asserted: `tests/test_output_schema.py` opens each cited line and
  checks the relationship is on it. `evidence` is `null` for `CONTAINS` and `CO_CHANGE` — a file
  being in a directory is not written anywhere, and inventing a line for it would be the
  fabricated citation this schema exists to prevent. `confidence` is defined precisely as
  P(`dst` is the correct target | the relationship at `evidence` exists), which is what lets an
  ambiguous name split 1/n across candidates while the call site stays certain; it never encodes
  dynamic dispatch, which `call_kind` carries. Normalisation happens at the single `add_edge`
  chokepoint so a future edge type cannot ship as a bare triple.
- **A "Confidence and limitations" segment on every `rag --answer`.** Computed by repo2graph
  from the pack that was actually sent, never asked of the model — a model rating its own
  confidence produces a number with no referent, and it cannot know what retrieval never showed
  it. Reports what the answer rests on, how many of the edges used were ambiguous, whether the
  budget truncated the pack (an answer from a truncated pack can be wrong *by omission*, and
  nothing else in the output would say so), and the standing limits. Same shape every time,
  including when there is nothing to caveat: a section that appears only when something is wrong
  teaches readers to skip it. Machine-readable form: `limits.confidence_report(pack)`.
- **`repo2graph bug-report`** — a diagnostic bundle that is useful to a maintainer and safe to
  post. Environment and versions, the non-passing `doctor` checks, index shape and freshness,
  and an edge-quality histogram (by type, by method, by confidence bucket) — often what actually
  explains a bad answer. **No file content, at any setting.** No environment variable values, no
  remote URL, no repository or branch name, no absolute paths. File paths are off by default (a
  path like `billing/stripe_migration_v2.py` says a lot about a private repo); changed files
  appear as 8-character fingerprints, and `--include-paths` opts in. The commit sha *is*
  included: it makes a wrong edge reproducible against a public repo and reveals nothing a
  private repo's own history does not.
- **Three new issue templates** — Stale index, Parser failure, Answer unhelpful — completing the
  five feedback categories, which now match `bug-report --category` one-to-one
  (`tests/test_output_schema.py` fails if a category has no template to file under). The
  existing edge template gained the category selector and the bundle field.
- **`docs/OUTPUT_SCHEMA.md`** — the contract for edge records, what `confidence` means and does
  not mean, where each surface puts its citations, and what the bundle does and does not carry.
- **`repo2graph index-status`** — one report joining `manifest.json`, `stats.json` and the working
  tree: the indexed commit and branch, when the index was built, file/symbol/edge counts, detected
  languages, what discovery skipped and why, parse failures, the index's size on disk, and whether
  the tree has moved since the build. `--json` for CI, and `--check` to exit 1 when the index is
  not current — which turns "the committed index matches this commit" into a reviewable gate.
  Freshness is `current`, `stale` or `unknown`, the last meaning nothing could be checked; "I could
  not tell" is a different claim from "it is out of date" and reporting the second when you mean the
  first trains people to ignore the field. `doctor`'s Index Freshness check and this command share
  one implementation (`status.compute_freshness`), pinned by
  `test_doctor_and_index_status_never_disagree`.
- **Git-aware provenance.** `source_revision` now records `base_branch` (from
  `refs/remotes/origin/HEAD`, falling back to the first of `main`/`master`/`develop`/`trunk` that
  exists — CI checkouts routinely lack the former), `merge_base`, `commits_ahead_of_base`, and a
  `dirty_files` count alongside the existing `dirty` flag.
- **`build --exclude-group NAME`** — named exclusion groups (`generated`, `vendor`, `build`,
  `dependencies`, `sensitive`, `all`), repeatable and composable with `--exclude`;
  `--exclude-group help` prints what each covers and builds nothing. One authored table
  (`exclusions.GROUPS`) backs both this flag and `doctor`'s "Generated / Vendored Code" check, so a
  shape the check can flag is always a shape the flag can exclude — the check's remediation now
  names a group rather than reconstructing globs. Patterns use `**/vendor/**`, not `vendor/**`:
  `parse._glob_re` anchors a pattern containing "/" at the repository root, so the latter misses
  `packages/web/vendor/...`, which is where a monorepo keeps all of it. Every glob is asserted
  against representative paths.
- **`docs/INDEXING.md`** — the pipeline, the determinism guarantees and what backs them, the
  four exclusion layers, how staleness is computed, and the failure-mode table.
  **`docs/rfc-incremental-indexing.md`** — measurements and a proposal.
- **`tests/test_determinism.py`** — reproducibility across five axes: two builds of one tree,
  `--jobs 1` vs `--jobs 4`, filesystem enumeration order, git vs `os.walk` discovery, and
  `--incremental` vs full.
- **`repo2graph demo`** — the command to run first. It writes a small bundled repository (an
  orders service: routes → auth guard → rules → SQL store, plus a test module and a JS client)
  into a scratch directory, builds a real index over it through the same `build()`/`dump_all()`
  path the `build` subcommand uses, and answers the five starter questions against it. No
  network, no API key, no repository of your own, nothing to configure. `-o DIR` and `--keep`
  leave the repo in place to poke at; `--full` prints each answer whole instead of its citation
  table plus the first cited block. The fixture lives as source strings in `repo2graph/demo.py`
  rather than as package data, so it is present under `uvx`, `pip`, Docker and a git checkout
  alike — a `package-data` entry is one edit away from silently dropping out of the wheel.
- **Five starter questions, authored once.** `demo.STARTER_QUESTIONS` is the single source for
  the prompts in `README.md`, `docs/quickstart.md` and the demo itself; each carries the
  copy-paste template for the reader's own repo, the form the demo actually runs, and the one
  line saying what it demonstrates. `tests/test_doc_consistency.py` fails if a docs copy drifts,
  and `tests/test_demo.py` asserts each question still cites the file that answers it — path
  membership hand-derived from the fixture source, never a value the code under test produced.
- **`docs/quickstart.md`** — two minutes from nothing installed to a cited answer, with the
  expected output at each step, the five prompts, the MCP one-liner, and a symptom → `doctor`
  check → fix table.
- **Six new `doctor` checks**, covering the rest of what a first run gets wrong:
  **uv / pip availability** (absent uv is not an error — only the `uvx` one-liner needs it —
  but neither uv nor pip is); **index freshness** (the manifest's commit against `HEAD`, the
  discovered file set against `index.state.json`, and a real sha256 for any file whose mtime is
  newer than the manifest's, so a checkout that rewrites every mtime does not read as stale);
  **parser coverage** (parse errors and grammar-less files *in this index*, as opposed to
  whether the grammars load at all); **ignored paths** (discovery mode and per-rule skip counts,
  warning past two thirds — the signature of a repo whose source sits under a
  `DEFAULT_SKIP_DIRS` name and therefore indexes cleanly while answering nothing);
  **generated / vendored code** that made it *into* the index, by path, by name and by
  `@generated`/`DO NOT EDIT` header, with the `--exclude` globs to rebuild with; and **MCP
  client configuration**, which finds the Claude Code, Claude Desktop, Cursor and Windsurf
  config files and catches the four failures that all surface to the user as "server failed to
  start" — a `command` not on `PATH`, `uvx` without `--from "repo2graph[mcp]"`, a relative or
  non-existent repository path, and JSON that does not parse. It never reads or echoes an
  entry's `env` values. Every scan over an index is bounded (`MAX_NODE_LINES`,
  `MAX_FRESHNESS_FILES`, `MAX_GENERATED_CONTENT_SCANS`), because `doctor` is the documented way
  to inspect an index built somewhere else.
- **`docs/THREAT_MODEL.md`** — the page the five existing security documents now hang off, and what
  issue #263 was actually asking for. Assets, five trust boundaries, and per-surface attacks with
  the mitigation and the open gap for each: hostile repository, hostile index (an index is
  untrusted input per `GHSA-6wrx-c2rg-mvm9`), prompt injection through retrieved source, secrets
  reaching the index, the HTTP MCP surface, and supply chain. Includes an explicit out-of-scope
  list and a table of every open security-relevant issue against the surface it belongs to, so the
  page does not read as though the surface is closed.
- **`docs/secure-configuration.md`** — copy-paste configurations: exclusion globs worth adding
  beyond the defaults (Terraform state, Helm prod values, test fixtures with real data), a
  `--network=none` build that *proves* the offline claim rather than asserting it, stdio and HTTP
  MCP, the Action, how to forbid `rag --answer` in a shared environment, deletion, and a checklist
  for a sensitive repository.
- **`docs/privacy-audit-2026-09-25.md`** — the data-handling audit behind the above: every outbound
  path and every write location enumerated from the code. 12 claims checked, 9 held as written,
  3 were incomplete and are corrected, none was false.
- **Repository governance docs.** **`docs/ARCHITECTURE.md`** — the contributor-facing module map
  (what each of the 27 modules owns, which way dependencies run, the two import cycles
  `export`↔`viz` and `mcp`↔`http_server`↔`tasks`, that `langs`/`walker`/`layout` are re-export
  shims rather than modules, and where a change of each kind goes).
  **`docs/parser-development.md`** — adding a language end to end: `LangConfig`'s four keys, how to
  find real tree-sitter node types, `_BASE_NODES` for inheritance clauses, optional per-language
  import resolution, the two tests to write, and the six documents a test will fail without.
  **`docs/TRIAGE.md`** — what makes an issue workable, the triage buckets, what qualifies as
  `good first issue`, and the proposed label taxonomy. **`docs/COMMUNITY.md`** — issues vs.
  Discussions routing and the Discussions categories. **`docs/good-first-issues.md`** — seven
  starter tasks, each with a file-and-line pointer, acceptance criteria, and the specific catch
  that makes it harder than it reads.
- **`docs/issue-triage-2026-09-25.md`** — a full pass over all 84 open issues. Classifies every one
  into a single bucket (15 bug, 29 enhancement, 2 documentation, 8 chore/ci/refactor, 2 duplicate,
  21 needs-reproduction, 4 proposed out-of-scope, 3 epic), and records what the pass turned up:
  two duplicates (#295⊂#315, #291 superseded by #407), five issues describing behaviour that
  already exists or is documented as deliberate (#289's edge filters, #283's MCP ceiling, #263's
  deployment docs, #287's residual-chunk threshold, #349's documented import cycle), and four
  issues about the same budget vocabulary with none referencing another. Drafted replies included;
  **nothing was applied** — no issue was closed, relabelled or commented on.
- **Two issue templates.** `incorrect_edge.yml` for a wrong or missing `CALLS`/`IMPORTS`/`INHERITS`
  edge, which gates on the documented blind spots (dynamic dispatch, reflection, DI, generated
  bindings) and on index freshness before the report is filed; and `language_support.yml`, which
  asks for the tree-sitter node types and points at the parser guide.
- **`tests/test_i18n_consistency.py`** — the first guard on any relationship
  between the six READMEs, which is why all six had drifted together. 25 cases
  pin, across every language at once: that each translation is linked from the
  English switcher; that every `LANG_CFG` grammar appears in every README (the
  exact drift recorded under *Changed* below); that `GraphRAG` never appears
  above the `## 📐` architecture heading; that `GraphRAG`, `BM25`,
  `pack_context()`, `AST` and `RRF` never appear before the `## 👥` persona
  heading; and that each file still carries the positioning anchors
  (`explain retrieval`, `build --incremental`, `[cite:`, `CO_CHANGE`,
  `repo2graph-mcp`). Emoji section markers are the boundary because they are the
  only headings identical in all six files. No assertion compares one README's
  prose to another's — a translation legitimately differs in every sentence.
  `test_doc_consistency.py`'s language-token map was hoisted to a module-level
  `LANGUAGE_TOKENS` so both suites share one source of truth.
- **`repo2graph impact`** — architectural impact analysis for a pull request, a branch or a
  unified diff, answered out of the graph rather than out of the diff. It intersects diff hunks
  with symbol spans to name the symbols that actually changed, then walks the graph for the
  callers, tests, modules and dependency paths that reach them, and reports which changed
  symbols are public API, which are disconnected from everything else in the diff, and a risk
  level with the evidence behind it. `--base`/`--head` for refs, `--diff FILE` or `--diff -` for
  a diff on stdin, `--max-depth` for caller hops, `--min-confidence` to drop ambiguous `CALLS`
  edges, `--write` for a file, and four `--format`s: `markdown`, `json`, `pr-comment` and
  **SARIF v2.1.0** for Code Scanning. The guardrails are the point: a claim is phrased as what
  the graph can support, never as definite runtime breakage, and `guardrails.stale_index_notice`
  says out loud when the index is not the tree the diff belongs to — the failure mode that
  otherwise looks exactly like a clean run. **`PR_IMPACT.md`** is the guide; the three committed
  fixtures under `tests/fixtures/impact/` pin the shape of each output format.
- **MCP tool `repo_impact`** — the same analysis as the sixth tool on the server, with its
  arguments clamped in the handler and its *output* bounded to the same 12k-token ceiling
  `repo_search` holds itself to, emitting a valid-JSON envelope for `format=json` rather than a
  cut that stops parsing.
- **`.github/workflows/pr-impact.yml`** — posts the blast radius on every PR as prod-igy, the
  repository's existing PR assistant, and uploads the SARIF to Code Scanning. It builds the
  index on the **head/merge** commit, never the base: an index built on the base cannot contain
  a file the PR adds, and its line numbers belong to a different version of every file the PR
  touches, so added files read as unreachable orphans and modified symbols resolve against the
  wrong lines — a green run with plausible, wrong numbers.
- **A five-archetype benchmark and regression corpus.** `benchmarks/corpus/` holds five
  self-contained repositories chosen for the shapes real answers break on — `python_backend`
  (layered FastAPI-style service), `ts_app` (Express-style controller/service/model stack),
  `frontend_app` (React components, hooks and context), `modular_monolith` (cross-domain events
  between six bounded contexts) and `dynamic_patterns` (registry, factory, dispatcher and barrel
  re-export, i.e. the call sites the graph is documented to miss). `benchmarks/tasks.json`
  defines 25 reproducible tasks with ground truth; `scripts/benchmark_runner.py` scores
  repo2graph against lexical search and a multi-hop agent baseline on correctness, citation
  accuracy, latency and context footprint, writing `benchmarks/results_v2.json`.
  `.github/workflows/benchmark.yml` runs it as a regression gate on Ubuntu and Windows.
  **`BENCHMARK.md`** documents the corpus, the tasks and the methodology.
- **A language quality scorecard and the roadmap behind it.**
  `scripts/generate_language_scorecard.py` audits every supported grammar on parser coverage,
  symbol-extraction breadth, call resolution, import resolution, test-to-implementation linking
  and framework-specific edges — read out of the code rather than out of a claim in a table
  (`--json`, `--detail`, `--output`).
  **`LANGUAGE_SUPPORT.md`** is the resulting strategy document — the tiering, what "supported"
  means per tier, and which ecosystem relationships (routes, test links, DI bindings, ORM
  entities) are syntactically invisible today. Four RFCs under `docs/rfcs/` work the plan
  through for the relationship graph, TypeScript/JavaScript, Python and the JVM-vs-Go choice;
  `docs/ROADMAP_LANGUAGE_ISSUES.md` breaks it into filed work.
- **`npm/` — an npm launcher for the MCP server**, so a Node-first editor can start it without
  the user having to know there is a Python package underneath. `npx repo2graph-mcp` ships no
  server code: it walks `PATH` itself and hands off, in order, to `uvx --from "repo2graph[mcp]"`,
  an already-installed `repo2graph-mcp`, or `pipx run --spec "repo2graph[mcp]"`, and otherwise
  prints the install instructions and exits non-zero — it never installs a Python toolchain
  behind the user's back. Every message it emits goes to stderr, because the MCP transport is
  stdio JSON-RPC and anything on stdout corrupts the stream.
  `tests/test_npm_launcher.py` pins the resolution order and the failure messages.
- **`repo2graph/schema.py`** — `TypedDict` shapes for every public record (`NodeRecord`,
  `EdgeRecord`, `ChunkRecord`, `NeighbourEdge`, `RetrievalResult`, `PackResult`,
  `EntrypointRecord`, `ManifestRecord`). It documents a contract rather than enforcing one:
  the producers keep returning `dict[str, Any]` on purpose, because a node's dict grows
  type-specific keys. Import it to statically check code that *consumes* the artifacts.
  `tests/test_schema.py` is the drift detector — it builds a real index and fails if a field
  declared required here is missing from a real record.
- **`CITATION.cff`**, so the repository can be cited, and
  **`docs/testing/test-suite-optimization.md`**, which records the audit behind the suite
  consolidation below.

### Changed

- **The README and all five translations carried the two errors corrected
  elsewhere in this release.** Their Security sections said `rag --answer` was
  "the one opt-in exception" to making no network calls — the same
  under-statement corrected in `PRIVACY.md`, and the more visible one, since the
  README is the front page. All six now distinguish "makes a network call" from
  "sends your code": four commands can reach the network and only `--answer`
  transmits anything of yours. Their contributing blocks all said
  `make lint test`, which runs two of the four gates CI runs; all six now say
  `make lint format-check typecheck test`, and the English one states the branch
  model. The README's "good first issues" pointer went to `docs/BACKLOG.md`,
  which does not have such a section; it now points at
  `docs/good-first-issues.md`.
- **`CODE_OF_CONDUCT.md` says what enforcement means on a one-maintainer
  project.** The Contributor Covenant text implies a body that does not exist
  here: the person who receives a report is the person who acts on it, and if the
  report concerns that person there is no internal escalation — GitHub's own
  abuse reporting is the independent route. Also states plainly that a security
  vulnerability is not a conduct issue and the conduct email is the wrong channel.
- **`docs/PRIVACY.md` corrected on three counts, all under-statements of scope rather
  than failed guarantees.** It described **two** opt-in network exceptions; there are
  **four** outbound paths — `rag --answer` (the only one that transmits source),
  `repo2graph github` (clones), `repo2graph embed` (downloads a ~90 MB model from
  huggingface.co on first use), and the OIDC JWKS fetch. It said every disk artifact
  is written only inside `-o`, which is true of `export.py` but not of the package:
  the build lock lands *beside* the output directory (`<repo>/..r2g.r2glock` for
  `-o .r2g`), `repo2graph github` clones into the system temp dir, and the embedding
  model caches under `~/.cache/huggingface/`. And its environment-variable table
  omitted `GOOGLE_API_KEY`, `GH_TOKEN`/`GITHUB_TOKEN`, `R2G_AUTH_TOKEN` and the two
  Windows path variables while stating no others were read — `R2G_AUTH_TOKEN`
  particularly mattered, since it is the way to pass the HTTP bearer token without
  putting it in argv where `ps` can read it. Added: a "Deleting everything" section
  covering all four locations, a "Keeping sensitive files out" section, and an
  explicit bar for any future analytics proposal (opt-in, disclosed before the first
  byte, and the no-socket tests extended rather than weakened).
- **`.github/CONTRIBUTING.md` corrected on two points that would have cost a
  contributor a round trip.** It said to fork and branch from `main`; feature and
  fix PRs target **`develop`**, and `main` only moves via a promotion PR or the
  release bump. And it listed two lint commands where CI runs three —
  `ruff format --check .` is a gate separate from `ruff check .`, and
  `make lint` alone does not include it. Also added: a routing table to the new
  guides, the full CI gate list, the test house style (pin literal values; prove
  a new test is a detector), and a pointer to the starter tasks — the "Good first
  issues" section previously said none were filed.
- **`.github/SECURITY.md` leads with how to report.** Reporting was the last
  section of seven; it is now the first, with a direct private-advisory link,
  acknowledgement and disclosure expectations, what to include, an explicit
  in-scope list, and the three things that are documented behaviour rather than
  vulnerabilities (a missing edge, a `1/n` ambiguous `CALLS` edge, a stale index).
- **`docs/publishing.md` documents the branch model and a pre-release checklist.**
  The automated release flow was covered; what was missing was that `develop` is
  promoted to `main` first, that `develop` is protected from the repo-wide
  auto-delete because promotion PRs use it as the head branch, and the six things
  to verify before cutting.
- **The bug and feature templates.** Bug report now gates on index freshness and
  on `docs/limitations.md`, asks for `repo2graph doctor` output, and redirects
  edge reports to the new template; its version placeholder was stale at `1.5.1`.
  Feature request became **Feature proposal** and now asks which surface it lands
  on, for one checkable acceptance criterion, and whether the filer wants to
  implement it. The issue chooser's contact links now route questions, support,
  ideas and showcases to Discussions.
- **Positioning rewritten around one outcome: "give coding agents trustworthy,
  cited answers about unfamiliar codebases."** The README hero no longer opens on
  "AST-driven code graphs & zero-dependency GraphRAG"; `GraphRAG`, `AST-driven`,
  `BM25` and `pack_context()` now appear only in "Architecture & token economics"
  and below. Three sections are new: **Who it's for** (four personas — onboarding
  developer, coding-agent user, PR reviewer, maintainer — each with the first
  command to run), **Why repo2graph instead of grep or vector search?** (a
  three-way table that concedes grep is the right tool for a literal string), and
  **What it does — and what it does not** (eight limitations on the first-time
  reader's path, not only in `docs/limitations.md`). The same outcome sentence now
  drives the PyPI summary (`pyproject.toml`), the MCP registry entry
  (`server.json`) and the Action's Marketplace blurb (`action.yml`); eight
  audience-facing PyPI keywords were added. The rationale, the jargon policy, the
  per-surface before/after audit and ready-to-paste copy for the surfaces that are
  not files in this repository (GitHub description and topics, the landing page)
  are in the new root **`POSITIONING.md`**.
- **`docs/limitations.md` gained two limitations that were real but undocumented:**
  dependency injection (the `CALLS` edge lands on the interface declaration or
  fans out across every same-named implementation, never on the class the
  container injected) and **stale indexes** (the index is a snapshot, nothing
  watches the filesystem, and `repo2graph doctor` checks index integrity and
  vector drift — not whether your working tree moved on). `docs/why-graph.md`
  gained an embedding-search section; `docs/architecture.md`'s "when to use" bullets
  became a persona routing table.
- **The parsed-grammar count is 17 everywhere.** `docs/comparison.md` said 15 in
  two places, the README's comparison table said 16, and all five translated
  READMEs said "16 grammars / 28 extensions"; `LANG_CFG` has held 17 grammars
  and 29 extensions since Lua landed.
- **The five translated READMEs carry the new positioning.**
  `docs/i18n/README_{de,es,fr,ja,zh-CN}.md` were retranslated — not reduced to a
  stub link — for the hero, the intro, the persona table, the grep/vector
  comparison and the does/does-not table, at the same level of abridgement they
  already used.
- **CI reorganised so a red check names what broke.** Lint, format, type check and the version
  surfaces moved out of the test matrix into a dedicated **Code Quality & Static Analysis** job
  that runs once instead of eleven times, and every job carries a descriptive name — *Test Suite
  (windows-latest, Python 3.13)*, *Windows CP1252 Non-UTF8 Pipeline Compatibility*, *Package
  Distribution & MCP Stdio Smoke Test*, *GitHub Action Composite Integration Test*, *Upstream
  Dependency Drift Canary* — rather than a job id. Jobs write a step summary with the numbers
  worth reading (branch coverage, benchmark deltas, audit findings), `PYTHONUNBUFFERED=1` so a
  hung job's logs are not still in a buffer, and uploaded artifacts carry explicit retention.
- **The test suite runs in parallel.** `pytest-xdist` is a `[dev]` dependency and `-n auto` is
  the default in both the `Makefile` and CI: ~1,700 mostly-independent cases with no shared
  database or fixture state parallelise almost linearly — 134s serial to 36s on 16 cores, 49s
  on a 4-vCPU runner. `make test-serial` is the escape hatch, because xdist reorders output and
  a failure is easier to read serially. `[tool.coverage.run] parallel = true` is what lets the
  workers' data combine. An audit of all 1,730 collected cases across 44 files backs this;
  `docs/testing/test-suite-optimization.md` records what it found.

### Fixed

- **The same tree indexed on two machines produced different artifacts.** Discovery order *is*
  artifact order — nodes are emitted as files are parsed, and edges and chunks follow. `git
  ls-files` sorts its output; `os.walk` returns whatever the filesystem hands back, alphabetical
  on NTFS and hash order on ext4 with `dir_index`. So a non-git build (a Docker image without git,
  a source tarball, a plain folder) produced `nodes.jsonl`, `edges.jsonl` and `chunks.jsonl` that
  differed byte-for-byte across machines while describing an identical graph — and no same-machine
  A/B could see it, because on NTFS the unsorted and sorted orders coincide. `discover()` now
  sorts both sources by the resolved path's **posix** form (`str(Path)` would sort on `\` on
  Windows and `/` elsewhere, which is the same divergence one level down). `--max-files N` made
  this worse than untidy: it takes the first N in discovery order, so two machines indexed
  *different subsets* of one tree.
- **Every build of a clean repository recorded `dirty: true`.** `BuildLock` writes
  `..r2g.r2glock` and `dump_all` stages artifacts in `..r2g.staging.<pid>.<hex>/`, both *beside*
  the output directory so they survive the transactional swap — so `.r2g/` in `.gitignore` covers
  neither, and provenance is captured from inside both windows. The manifest blamed the user's
  tree for repo2graph's own scratch files. Only untracked (`??`) entries are filtered; anything
  git is tracking is the user's, whatever it is called.
- **A build with any `--exclude` reported itself stale the instant it finished.** Freshness
  re-runs discovery to compare the tree against the index, and it did so with *default* filters —
  so every file the build deliberately excluded came back as newly added. `index.state.json` now
  records the filters the build actually used (`filters`), because there is nowhere else to
  recover `--exclude-group generated` from once the build exits, and the comparison applies them.
  An index written before that field existed falls back to defaults and says so rather than
  silently reporting phantom additions.
- **POSITIONING.md's stale limitation #6.** It said `doctor` "checks integrity and vector drift,
  not working-tree drift" -- true until `index-status` and the shared freshness check started
  detecting exactly that. Corrected to say what is now true *and* what still is not: detection is
  not subscription, and a file edited with its mtime preserved and left uncommitted is still
  missed.
- **The bug-report bundle could leak an absolute path through a diagnostic note.**
  `compute_freshness` interpolates an exception message into its notes, and an `OSError` carries
  the path that failed -- so a discovery failure put an absolute path *including a filename* into
  a bundle whose entire premise is that it contains neither. The happy path produces no such
  note, which is why the other privacy tests did not see it. Free text bound for the bundle is
  now scrubbed at the boundary rather than in each producer: the guarantee has to hold for prose
  written by code that has never heard of the bundle, including code added later.
- **The MCP `repo_neighbours` tool presented an ambiguous edge as fact.** It showed where the
  *neighbour* was defined but not the edge's own `evidence` -- so for "what calls this" an agent
  got a list of definitions with no way to open the lines that do the calling -- and it showed no
  confidence, so a name that matched three candidates read exactly like a unique resolution. Both
  are now in the output, the ambiguous ones marked `AMBIGUOUS <conf> of <n> candidates`. This was
  the one surface an agent actually reads, and it was the one surface the new edge metadata had
  not reached.
- **`docs/reference.md` claimed `CALLS_EXTERNAL` carries no `confidence`.** True until every edge
  type gained the standard trio, and wrong afterwards; the page also did not mention `method` or
  `evidence` at all. `docs/OUTPUT_SCHEMA.md` separately claimed the MCP tools return per-result
  `path`/`start_line` fields -- they return markdown strings. Both corrected, and
  `tests/test_doc_consistency.py` now fails if the two pages disagree about the standard fields.
- **Four new docs were unreachable from `docs/architecture.md`**, along with `docs/ACTION_SECURITY.md`,
  which predates this work. A doc nobody can reach from the index is a doc nobody reads; a test
  now enumerates `docs/*.md` and fails on any page that is neither linked nor explicitly marked a
  working note.
- **`explain-path` echoed every exclude glob instead of the one that matched.** Tolerable with a
  handful of hand-written patterns; unreadable once `--exclude-group` expands to sixty. The
  command exists to report the single rule that decided a path, so it now names that one pattern
  and carries it as `matched_glob` in the JSON.
- **`repo2graph impact` reported every diff as empty**, with exit code 0. `get_git_diff` placed
  `--` *before* the revision, so `git diff -U0 -- main...HEAD` read the revision as a pathspec:
  no match, no output, exit 0, "0 files changed / LOW risk". Nothing caught it because no test
  let `get_git_diff` reach git — the whole module fed `parse_unified_diff` static text. On this
  repository the command now reports 208 changed files where it reported 0. The same silent
  `{}`-with-exit-0 failure came from a user's gitconfig: `diff.noprefix` or
  `diff.mnemonicPrefix` makes git emit a real diff that `DIFF_GIT_RE` matches nothing in, so
  both are pinned off per invocation. When both the base-only and the base...head forms fail,
  both errors are now reported instead of one command running twice.
- **`_validate_ref` rejected the ordinary ways to name a diff base.** Its allowlist turned away
  `HEAD~1`, `HEAD^` and `main@{u}` — all illegal as refnames, all legal as revisions — along
  with legal refnames containing `+`, `#`, `=`, `,` or non-ASCII. The actual requirement is only
  "no leading `-`", which is what blocks git option injection (`--output=`, `--ext-cmd=`), so it
  is now a denylist that says so.
- **`parse_unified_diff` mis-parsed a diff that quotes a diff.** Hunk *body* lines were tested
  against the file-header branches first, so `++ b/x.py` (arriving as `+++ b/x.py`) renamed the
  enclosing file and dropped its entry, `-- /dev/null` flipped a file's status to "added", and
  `++++` lines vanished from `added_lines`. Separately, `current_hunk` was reset only on a
  `DIFF_GIT_RE` match, so `diff --cc` output and git-quoted paths left the previous file's hunk
  open and charged their lines to it — and `analyze_diff_impact` selects changed symbols from
  exactly that set, so the report named symbols the diff never touched.
- **A pre-authentication ReDoS in the secret scanner.** `PEM_BEGIN_RE`'s label character class
  contains every character of the `PRIVATE KEY` literal that follows it, so repeated incomplete
  headers backtrack quadratically — about 90s of CPU for a 1 MB body, reachable *before*
  authentication (`http_server._reject` calls `emit()` unconditionally) and not avoidable with
  `AuditConfig(level="none")`. Bounding the quantifier takes that to 0.4s; every real PEM label
  still matches.
- **`repo_impact` clamped its inputs but not its output.** A wide diff rendered ~17.9k tokens
  straight into an agent's context, past the 12k ceiling every other tool holds itself to. It
  also relayed git's stderr, which names the server's absolute index path.
- **`_is_secret_path` missed four shapes**: `env.local`, `kubeconfig`, `id_rsa.bak`, and
  multi-segment backups such as `.docker/config.json.bak`. No file in this repository is newly
  excluded.
- **Response-cache keys retained the serialised request body**, before any handler's length cap
  — roughly 257 MB for 256 large calls, in a cache whose *values* are bounded. Keys are now
  hashed to a fixed 64 hex characters.
- **The zizmor workflow-security gate could not fail.** `zizmor --format sarif` exits 0
  regardless of findings (the plain form exits 12), and a real unsuppressed `self-repository`
  finding was already sitting behind it. Gating now runs as its own pass and the SARIF upload
  still happens when it fails. Three `zizmor.yml` ignore pins had drifted off the constructs
  they were written for, silently un-suppressing one finding and leaving two as dead config;
  `lockfile.yml` dropped an unused `pull-requests: write`.
- **`impact --no-auto-build` was a dead flag**, declared with `dest="auto_build"` and never
  read, so a first run died with "no index at .r2g" and the documented default could not
  happen. CI was unaffected because the workflow builds explicitly, which is how it shipped
  green. `--diff -` was documented twice, including a copy-pasteable
  `git diff main...HEAD | repo2graph impact --diff -`, and exited "diff file - does not exist";
  it is now implemented, reading bytes and decoding utf8/surrogateescape so a cp1252 Windows
  stdin cannot raise before the diff is parsed.
- **Impact citations rendered as a Python dict.** Edge `evidence` is a `{"path", "line"}`
  record, but `impact.py` treated it as a `"path:line"` string, so Markdown printed
  `[cite: {'path': ..., 'line': 878}]` and the JSON disagreed with the committed sample
  fixture. The line-extraction guard `":" in evidence` was testing a mapping's *keys*, so a
  call site's line never won over the symbol's `start_line`. Both now route through
  `edgemeta.cite`. The reason 28 passing tests saw none of it: `MockIndex.add_edge` bypassed
  `edgemeta.normalize` and stored `evidence` as a string, so every citation assertion was
  checking the mock's shape instead of the schema's.
- **`PR_IMPACT.md`'s flag table described a parser that does not exist** — `-i` marked
  "Required" when it defaults to `.r2g`, a `-w` short flag that never existed, and no
  `--out`/`--no-auto-build`. Two tests now pin the table against the real parser in both
  directions.
- **`LANGUAGE_SUPPORT.md` and `BENCHMARK.md` linked through `file:///e:/Github/repo2graph/`.**
  86 absolute Windows file URLs that resolve for nobody, render as dead links on GitHub, and
  disclose the author's local directory layout. All are now repo-relative.

## [2.1.0] — 2026-09-24

### Added

- **`repo2graph explain`** — three subcommands that answer "why did the graph say
  that?" without reading JSONL by hand. `explain edge <src> <dst>` reports every
  edge between two nodes in either direction, with each edge's own attributes and
  both endpoints' file locations, and says *which* node is missing when there is
  no edge. `explain node <id>` gives a node's metadata, in/out degree and chunks.
  `explain retrieval <query>` traces a query end to end: tokens, the BM25 short
  list with per-candidate matched terms and which of them became seeds, every
  graph hop walked out of those seeds, and the final chunks tagged `seed` or
  `expanded_neighbor`. All three take `--json` (Issue #309).
- **Lua**, the seventeenth grammar with full treatment — functions, classes and
  calls — bringing the extension count to 29 (Issue #77).
- **In-repo import resolution for eight more languages.** `IMPORTS` edges now
  resolve to files for Rust, C#, PHP, Kotlin, Scala, Swift, Ruby and Bash, where
  before only Python, JS/TS, Go, Java and C/C++ did. Rust understands `crate::`,
  `super::`, `self::` and the crate name read out of `Cargo.toml`'s `[package]`;
  PHP maps PSR-4-ish `App\` prefixes onto `app/` and `src/`; Ruby distinguishes
  `require_relative` from `require`; Bash resolves `source`/`.` against the
  sourcing file's directory (Issue #355).
- **Structured import extraction for Swift, Ruby and Bash**, so those languages
  contribute modules, names and aliases to `ImportDetail` rather than only a raw
  string (Issue #343).
- **`repo2graph completion [bash|zsh|fish]`**, printing the shell setup line for
  tab completion. Completion itself comes from `argcomplete`, a new optional
  extra: `pip install "repo2graph[completion]"`. Nothing is required for the CLI
  to work without it (Issue #356).
- **An official `Dockerfile`** — multi-stage, non-root (10000:10000), and
  compatible with a read-only root filesystem and `--cap-drop=ALL`, matching the
  hardening `docs/ENTERPRISE_DEPLOYMENT.md` already asked operators to apply
  (Issue #327).
- **`--cochange-min`**, and CO_CHANGE edges that carry their own provenance. The
  co-change threshold was a hardcoded 3; it is now a flag, and every emitted edge
  records `cochange_count`, `sampled_commits` and `min_pairs` so a reader can see
  what evidence produced it and at what setting. `stats.json` gains
  `cochange_sampled_commits` and `cochange_min_pairs`. The docstring on
  `add_cochange` now states the whole formula — depth cap, `--no-merges`, the
  25-file bulk-commit filter, path filtering and the threshold — in one place
  (Issue #280).
- **`--allow-auto-build`** for `repo2graph-mcp`, which re-enables auto-building a
  missing index in HTTP mode. See the note under Changed (Issue #265).
- **Four new GitHub Action inputs.** `incremental`, `parse-policy` and
  `max-call-candidates` expose build flags that were previously CLI-only (Issues
  #346, #311). `commit-force` makes the push to `commit-branch` a plain push
  instead of a force-push; alongside it, the action now refuses outright to push
  from a fork pull request and warns on `pull_request`/`pull_request_target`,
  with the reasoning written up in `docs/ACTION_SECURITY.md` (Issue #310).

### Changed

- **HTTP mode no longer auto-builds a missing index.** Read-only network tool
  calls could previously trigger parser execution, file writes and git
  interactions on a server an operator had only pointed at a directory. A tool
  call against an unindexed directory now returns 503 with instructions instead.
  **Upgrade note:** an HTTP deployment that relied on the first tool call
  building the index must either pre-build it or pass `--allow-auto-build`.
  Local stdio mode is unchanged — it still builds on first use (Issue #265).

### Fixed

- **The version bump covered five files; the version was written in eleven.**
  `bump_version.py` rewrote `pyproject.toml`, `server.json`,
  `repo2graph/__init__.py`, `CHANGELOG.md` and `uv.lock`, and
  `check_version.py` verified the first three. Neither knew about the
  *documented* surfaces, so after 2.0.0 shipped the README still told users to
  write `uses: Srinivasan-78/repo2graph@v1` and described `@v1` as following
  "every 1.x release" — a live instruction to pin to a dead release line — while
  `docs/ENTERPRISE_DEPLOYMENT.md`, which tells operators to pin rather than
  float, showed `repo2graph[mcp]==1.6.0`. 29 stale sites across 7 files, now
  corrected to 2.0.0.
  The list is one table, `scripts/version_surfaces.py`, read by the bump, by the
  check, and by `publish.yml`'s `--files` (previously five hand-typed paths, one
  place for the same drift to recur). It is an allowlist, not a glob:
  `benchmarks/results.json` and `examples/*/manifest.json` record which version
  *produced* an artifact, and `docs/github-action.md`'s `repo2graph>=1.4,<2`
  illustrates the form of a range spec — a bump must leave all of those alone,
  which is asserted. A surface whose pattern matches nothing is now an error
  rather than a silent pass, since that is how the `@v1` drift went unnoticed.
  `check_version.py` also runs in CI now; it was a pre-commit hook and a
  release-time step and nothing in between, so it only fired for contributors
  who had installed pre-commit, and otherwise first fired at the point its own
  docstring calls "a burnt version number that PyPI will not let you reuse".
  `bump_version.py` now fails rather than warns when `uv` is missing, because
  the pypi job installs with `uv export --locked`.
- **`explain retrieval` traced a walk the retrieval never made.** It called
  `Index.expand()` without `edge_dirs`, so it inherited `DEFAULT_EDGE_DIRS`
  (`DEFINES: ("in",)`, `IMPORTS: ("out",)`, `INHERITS: ("out",)`) while the
  `Index.retrieve()` it printed twelve lines later passes `ALL_EDGE_DIRS` — the
  narrowing AGENTS.md already documents, in a new caller. The command reported
  `Runner.run → Runner DEFINES in` for a chunk that came back labelled
  `DEFINES out of main.py`, and dropped every DEFINES-out / IMPORTS-in /
  INHERITS-in hop from the trace entirely. Its seed list was also
  `list(a set)`, so the traced order — which is part of the traversal, since
  `expand()` walks its frontier in order under a per-hop cap — changed with
  `PYTHONHASHSEED`: three runs of one command, three answers. The seed loop now
  also honours `budget_chars` the way `retrieve()` does, so `selected_as_seed`
  cannot disagree with the seeds actually used on a large index.
- **The action's `parse-policy` input offered a value the CLI rejects.** It was
  documented and defaulted as `lenient`; `repo2graph build --parse-policy`
  accepts only `best-effort`, `warn`, `strict`. It survived because the step
  drops the value when it equals the default, so the one invalid value was also
  the one that never reached the CLI — any correction to that guard would have
  started forwarding it. Now `best-effort` throughout, with a test that reads
  the accepted values off the live parser rather than restating them.
- **Two builders could both hold the build lock.** `BuildLock` reclaimed a lock
  whose file was older than `stale_threshold` (1 hour) by unlinking it —
  regardless of whether the holder was alive. `_write_metadata` runs once, at
  acquire, so the file's mtime measures how long the holder has been *working*,
  not whether it is stuck: any build slower than the threshold was joined by a
  second one. Because `flock`/`msvcrt.locking` attach to an open file rather
  than a path, the second builder's lock on the freshly created file conflicted
  with nobody, and `dump_all`'s directory swap then ran twice over one index.
  A second, narrower instance of the same shape: `release()` unlinked the path
  unconditionally, so a waiter that opened the path just before that unlink
  locked a now-nameless file while the next process created and locked a new
  one. The OS lock is now the only authority on whether the lock is held — it
  is released by the kernel when its holder dies, so a lock file left by a
  crash is already acquirable and never needed reclaiming. `acquire()`
  additionally verifies that the descriptor it locked is still the file the
  path names, and `release()` only unlinks a name that still refers to its own
  file. `stale_threshold` now enriches the timeout diagnostic instead of
  licensing a takeover.
- **A failed index swap that also failed to roll back said nothing useful.**
  `_atomic_dir_swap` swallowed the restore error and re-raised the original, so
  an operator was left with a missing index and a `.<name>.backup.<pid>`
  directory they had no reason to look in. The raised error now names it.
- **The official image had no `git`.** `python:3.12-slim` does not ship it, and
  repo2graph shells out to git rather than reimplementing it: `walker.discover`
  prefers `git ls-files` and falls back to an `os.walk` that does not honour
  `.gitignore`, so the documented `docker run … repo2graph build /repo`
  indexed a different file set than every other way of running the same build,
  `--git-history` produced nothing, and `doctor` failed its own `check_git`.
  The runtime stage now installs git, and declares `safe.directory` through
  `GIT_CONFIG_*` rather than a config file, since the documented run is
  `--read-only` and mounts a host checkout owned by another uid.
- **`add_cochange`'s new formula docstring named the wrong cap.** It said
  `MAX_COCHANGE_COMMITS = 1000`; the constant is 5000, which is also what
  `docs/cli.md` tells operators.

### Security

- **Index files are read under a size ceiling.** `embed._npy_read` and
  `load_vectors`, `integrity.verify_artifacts` and `doctor` all read index
  files whole with `.read()`/`read_text()`. An index is routinely consumed from
  elsewhere — the `graph` branch, Action artifacts, `examples/` — and `doctor`
  on a received index is the documented way to check one, so those sizes are
  attacker-chosen and each read was an unbounded allocation driven by a file
  somebody else wrote. All five sites now go through `integrity.read_bounded`,
  which stats the open descriptor and refuses anything over its limit
  (`MAX_METADATA_BYTES` 256 MB for `manifest.json` and `vectors.meta.json`,
  `MAX_VECTORS_BYTES` 512 MB for `vectors.npy`). Bounding `vectors.npy` alone
  would not have closed this: `load_vectors` reads the sidecar first, so the
  whole allocation stayed reachable through that file.
- **`auth_modes` and `budget_tokens` are no longer redacted out of the audit
  log.** `SECRET_KEY_RE` matches by substring, so field names describing a
  *shape* — which auth is in force, how large a request was — were replaced
  with `[redacted:key:...]`, costing an operator the two fields most worth
  reading back and revealing nothing in exchange. A short explicit
  `NON_SECRET_KEYS` allowlist exempts them from the **name** test only; their
  values still go through the credential-shape, URL and path checks, so a
  token that turns up under one of those names is still redacted. The
  allowlist rather than a narrower regex: loosening the pattern would also stop
  matching names nobody has written yet, and that failure mode is a credential
  in a log.
- **The HTTP transport no longer tells a caller where its index lives.** Found
  by an audit of the transport, not reported. When no index exists,
  `open_index` raises `SystemExit` with a message written for an operator at a
  terminal that names the absolute index directory — twice — and `_call_tool`
  relayed `str(exc)` verbatim as the 503 body, so a caller (and the context
  window of any agent driving the server) received the host's filesystem
  layout. That is the disclosure `_public_repo_label` already refuses one
  endpoint over. The 503 stays actionable per #265 but uses placeholders
  (`repo2graph build <repo> -o <index-dir>`); the real directory goes to the
  audit log, which is server-side. The rest of the transport audit found no
  further issues: token comparison is constant-time, algorithm confusion is
  refused by an RSA-only table plus a `kty` check, `Host`/`Origin` are
  validated before the body is read, response headers are sanitised against
  splitting, bodies are `Content-Length`-only and size-capped with a
  `RecursionError` guard on deeply nested JSON, and the non-loopback bind
  guard fails closed.
- **Quadratic blowup scanning for PEM private keys (denial of service).** Found
  by a ReDoS pass over `secrets.py`, not reported. The `private_key` pattern was
  `BEGIN` followed by a lazy `[\s\S]*?` to an *optional* `END`, so every `BEGIN`
  whose `END` is missing re-scanned the entire remaining text before the
  optional group gave up — O(n²) in the number of `BEGIN` markers. Repository
  content is attacker-supplied on every build and `max_file_bytes` defaults to
  1.5 MB, so one committed file of repeated `-----BEGIN RSA PRIVATE KEY-----`
  lines cost roughly eight minutes of CPU per build (measured: 4× time per 2×
  input, 5.5s at 160 KB). `BEGIN` and `END` are now separate anchors paired by
  `_pem_spans` in one linear pass: the same 1.5 MB worst case takes 269 ms.
  Detection is unchanged — a complete block still spans `BEGIN` through `END`,
  an unterminated one still yields its header, and line-preserving redaction
  still holds. The other patterns were measured at the same sizes and are
  linear.
- **GHSA-mqm8-mc66-wjvj (high) — OIDC JWKS fetch could be downgraded to
  cleartext by redirect.** `auth._fetch_json` checked `https` on the URL it was
  handed, then called `urlopen`, which follows redirects using a handler that
  accepts `http`, `https` and `ftp`. An issuer's discovery document or
  `jwks_uri` could therefore `302` to `http://`, putting the signing keys on the
  wire in clear; an on-path attacker answering that request substitutes their
  own modulus and then satisfies every remaining check in the module — `alg`,
  `use`/`key_ops`, `iss`, `aud`, `exp` and a genuinely valid signature over
  their own key — to authenticate as any `sub`. Fetches now go through a
  module-level opener whose redirect handler refuses any non-`https` hop and
  caps redirects at 3. Affected deployments: `--auth-oidc-issuer` on the HTTP
  transport.
- **GHSA-f896-f643-87cf (medium) — `jwks_uri` was unconstrained.**
  `JWKSCache._resolve_jwks_uri` cross-checked the discovery document's `issuer`
  but took `jwks_uri` at face value, so one field in a document fetched over
  the network relocated the trust anchor for every authentication decision to
  any host. It must now share the issuer's scheme and host. The `issuer` field
  is also required rather than optional: the old `if declared and ...` skipped
  the mismatch check entirely when the field was absent, letting a document opt
  out of being compared by omitting it. RFC 8414 §3.2 makes it REQUIRED.
- **GHSA-6wrx-c2rg-mvm9 (medium) — `repo2graph doctor` was an arbitrary-file
  hash oracle.** `integrity.verify_artifacts` built each path to check as
  `out / rel_path` where `rel_path` is a key from the index's own
  `manifest.json` — untrusted input, since this project ships indexes on a
  `graph` branch, as Action artifacts and in `examples/`, with `doctor` as the
  documented way to check a received one. `pathlib` discards the left operand
  when the right is absolute, so an absolute key read any file the process
  could reach and echoed its real `sha256` into `report.errors`, while a
  missing file reported differently from a mismatching one, probing for
  existence. Keys that are absolute, or that resolve outside the index, are now
  reported as a corrupt manifest before anything is read. `checksums` keys were
  the only manifest-derived values used as paths; `written` and `entrypoints`
  were audited and are not.
- **`rag --answer` no longer lets a provider redirect walk off with the API key.**
  Found by sweeping the codebase for the class behind GHSA-mqm8-mc66-wjvj, not
  reported separately. `stream_answer` called `urlopen`, which follows
  redirects, and CPython's handler forwards every header except
  `content-length`/`content-type` to the new target whatever host or scheme it
  names — so a provider answering `302 http://elsewhere` was handed the
  `Authorization` / `x-api-key` / `x-goog-api-key` header in clear. It also made
  `_disclose` untrue, since the hostname printed to stderr before the first byte
  would not be where the request ended up. Provider redirects are now confined
  to the same origin, may not downgrade https to http, and are capped at 3; an
  http-to-https upgrade on the same host is still allowed, because `OLLAMA_HOST`
  is legitimately plain http. Sweep result for the three advisory classes:
  `auth.py` and `answer.py` were the only redirect-following calls (no others
  exist), `integrity.py` held the only untrusted path join, and there is no
  archive extraction, no `pickle`/`eval`/`yaml.load`, and no `shell=True`
  anywhere in the package.
- `secrets.scan_content_secrets` returns `(type, start, end)` spans instead of
  `(type, start, end, matched_text)`. No caller ever read the fourth element --
  `redact_content` wanted only its newline count and `chunks._process_chunk_content`
  reports `f[0]` -- so every scan built a list of live credentials that existed
  purely to be discarded, one `emit(..., findings=findings)` away from becoming
  the leak the module exists to prevent. Callers needing the bytes hold `text`
  and can slice the span. Internal API: not exported from `repo2graph.__all__`.

## [2.0.0] — 2026-09-22

### Removed

- `claude-code-review.yml` and `claude.yml`, and with them all automated code
  review. `claude.yml` had no author gate of any kind: any commenter, including
  a first-time contributor, could start a 30-minute model run by typing
  `@claude`. `claude-code-review.yml` excluded fork PRs entirely to keep its
  token away from untrusted heads, so it never reviewed the contributions most
  worth reviewing. prod-igy is now the only place a model is invoked.

### Added

- **Scoped call resolution and evidence transparency**: Call targets are resolved
  through 8 lexical proximity tiers, most specific first (`self_recursive` ->
  `same_class` -> `same_file` -> `import_alias`/`imported_symbol` -> `same_module`
  -> `unique_global_name` -> `ambiguous_global_name` -> `unresolved_external`)
  rather than blind global matching. `self_recursive` is a top-level function
  calling its own name, the one self-call with no second reading. A method's own
  name is never claimed outright -- the receiver is not recorded, so `self.f()`
  and `other.f()` are the same input here -- and resolves through `same_file`
  with the caller left in the candidate set.
  Each `CALLS` edge records `resolution_kind`, `candidate_count`, `scope_distance`,
  and `call_kind` (`static`, `dynamic`, `decorator`, `possible`); the
  `CALLS_EXTERNAL` edge used for `unresolved_external` carries `resolution_kind`
  and `candidate_count` but no `scope_distance`
  (Issues #270, #271, #273).
- **Import and alias resolution**: Multi-language AST import parsing extracts modules,
  imported symbols, and local aliases (`ImportDetail`), mapping calls to their aliased
  targets and tracking `imports_resolved` vs `imports_unresolved` in graph stats (Issue #272).
- **Graph quality metrics & reporting**: Added `quality_metrics` to `manifest.json`
  and enhanced `repo2graph stats` with a new `--format text` human-readable summary.
  `repo2graph stats` still prints the raw `stats.json` by default, unchanged from
  before this feature; `--format json` and `--json` are explicit spellings of that
  same default (Issue #274).
- **Parser strictness policies**: Added `--parse-policy best-effort|warn|strict` flag
  to `build` and `github` commands. In `strict` mode, tree-sitter AST syntax errors
  raise a typed `ParseError` and halt the build with clear diagnostics (Issue #275).
- **Subtyped inheritance & interface extraction**: Extracted class bases carry
  relationship subtypes (`INHERITS`, `IMPLEMENTS`, `EXTENDS`, `MIXES_IN`) alongside
  the primary `INHERITS` edge type for backwards compatibility. Built-in and framework
  base types without repo-local definitions are guarded against false cross-project
  linkages (Issue #276).
- **Path precedence hierarchy & explain-path**: Documented the real discovery
  precedence order `explain_path` evaluates (`outside_root` through
  `binary`/`included`, steps 0-10) and introduced `repo2graph explain-path <path>`,
  which checks one path against those rules and reports the single rule that
  decided it — precedence step, rule id, decision and reason — not a
  step-by-step trace (Issue #277).
- `repo2graph doctor [path]` command diagnosing Python version, package version,
  tree-sitter & grammar availability, Git integration, directory permissions,
  existing artifact integrity, vector correspondence, MCP SDK compatibility,
  and platform encoding. Safely probes LLM provider configuration without ever
  disclosing secret values or environment variables. Supports `--json` output
  (Issue #308). Fixed during review, before release: the provider-env probe
  was disclosing each key's last 2 characters and exact length; artifact
  integrity treated any top-level `agent/` directory or `chunks.jsonl` as a
  (possibly corrupt) repo2graph index, which false-positived on unrelated
  projects using those same generic names; the MCP SDK check only echoed the
  installed version instead of verifying `mcp.server.Server` is actually
  importable, the thing `repo2graph-mcp` needs; and a probe against a
  not-yet-created nested path left every directory it created behind instead
  of cleaning up.
- Automated documentation consistency test suite (`tests/test_doc_consistency.py`)
  guaranteeing zero drift between code and documentation across CLI subcommands,
  parser language configurations, GitHub Action inputs/outputs, and MCP registered
  tools (Issue #319).
- Secure-by-default secret exclusion across all artifact flows (`build`, `github`,
  auto-build `query`/`rag`, GitHub Action, and MCP). Sensitive files (`.env*`,
  private keys, certificates, credentials, `.ssh`, `.aws`, `.gnupg`) are excluded
  automatically. `--include-secrets` provides explicit opt-in (Issue #260).
- Content-aware secret scanning and line-preserving redaction in chunking. Detects
  AWS keys, GitHub tokens, Slack tokens, OpenAI keys, Google API keys, PEM private
  keys, JWTs, and database credentials with configurable policy (`--secret-policy
  redact-match|exclude-file|warn-only|off`) (Issue #261).
- Structured logging and audit log sanitization. Centralized sanitization with
  recursion depth ceiling (12), container size limits (128 items), cycle detection,
  and credential scrubbing for basic-auth URLs and sensitive headers (Issue #262).
- Dedicated secrets hardening test suite (`tests/test_secrets_hardening.py`) verifying
  path filtering, content redaction, false-positive resistance, log sanitization, and
  GitHub Action flag propagation.
- prod-igy reports check-run results for the PR head — counts, plus the names
  of failing checks. Read from `checks.listForRef`, which needs the new
  `checks: read` scope; a re-run supersedes the earlier result for that name,
  and an in-flight run counts as running rather than failing.
- prod-igy writes a plain-language description of the diff and, when the title
  is not Conventional Commits, suggests one. The suggestion is displayed only;
  prod-igy still calls `pulls.update` exactly once, to retarget the base branch,
  and never rewrites a contributor's title or description.

  Scope is deliberately narrow. The step is shown one diff and nothing else —
  no repository contents, no `AGENTS.md`, no tools — so it cannot review the
  change against code it has not seen. Labels remain entirely path- and
  size-derived. Off by default (`PRODIGY_AI` repository variable); model,
  token ceiling, effort, timeout, per-PR run and output caps are all
  `vars.PRODIGY_AI_*`.

  Contributors have no way to reach it: it fires only on `pull_request_target`,
  `workflow_dispatch`, and a `@prod-igy` comment from an OWNER / MEMBER /
  COLLABORATOR, and it takes no instruction from the pull request. Per-PR spend
  is tracked in a hidden ledger inside prod-igy's own comment, so a force-push
  loop cannot run it more than `PRODIGY_AI_MAX_RUNS_PER_PR` times.

  Every failure — absent SDK, absent or revoked key, unreachable endpoint,
  rate limit, timeout, unparseable response — omits the section and posts the
  same comment prod-igy posted before this existed. The reason is recorded in
  the run log, never in the comment.

## [1.6.0] — 2026-09-20

### Changed

- `publish.yml` takes a `branch` input, defaulting to `main`, and every stage
  reads it instead of assuming the dispatch ref. `workflow_dispatch` runs
  against whatever ref the operator picks, so dispatching from `develop`
  previously bumped *develop's* content while opening the release PR against a
  hardcoded `main` base. The checkout is now pinned to the named branch, and
  the four hardcoded `main` references (`--base`, `gh pr list`, `gh pr create`,
  and the fetch/checkout/pull that tags the merge) follow it.

- Every workflow job now carries `timeout-minutes`, and every workflow a
  `concurrency` group. All 19 jobs previously inherited GitHub's 6-hour
  default, so the worst-case ceiling across the fleet drops from 6,840 minutes
  to 510. Bounds are set well above measured run times — CI completes in
  ~4 minutes — because the point is to catch a hang, not to police a slow run.
  `cancel-in-progress` is decided per workflow rather than uniformly: releases,
  the two bots that answer humans, and the long dispatch-only jobs are never
  cancelled; pure checks on a pull request are.

### Fixed

- **Bug sweep: every open `bug`-labelled issue.** Twenty-six issues, grouped
  below by the module they land in. Each carries a regression test proven to
  fail against the unfixed code.

- Indexing correctness. `_chunk_and_parse` sliced a large file at raw byte
  offsets, so a multi-byte character straddling a boundary lost **two** slices
  — up to 3 MB of source — with `parse_errors` still `0` and the file node
  still written as healthy, which is why only non-ASCII trees were affected
  and no test noticed. Slices are now carried to a character boundary and
  genuinely undecodable ones are counted into `stats`. The same function
  buffered the whole file for its digest and line count, so the path
  `max_file_bytes` exists to bound had no bound at all; both are now streamed.
  `PARSE_CACHE_FORMAT` goes 1 → 2 for the entry-shape change. `discover()`'s
  `S_ISREG` guard could be waived by `--chunk-large-files`, letting a
  non-regular file reach a blocking `read()`. (#196, #248)

- MCP server robustness. `open_index` did a check-then-act on module-level
  caches with no lock while the HTTP transport serves on `ThreadingHTTPServer`,
  so two concurrent first tool calls both ran the whole build and raced through
  `atomic_write`; a per-directory lock now spans the check-build-cache
  sequence. `--http-only` without `--http-port` fell through to the stdio
  transport — the exact thing the flag disables — and its early return jumped
  over `audit.close()`. (#235, #203)

- MCP auto-build no longer pins `jobs=1`. Workers inherited fd 0 and fd 1,
  which under the stdio server are the client's JSON-RPC pipes, so a repo above
  64 files hung on its first tool call. Pool workers are now detached onto
  devnull at the fd level, so a large repo's first call costs what
  `repo2graph build` costs. Also adds the >64-file fixture whose absence let
  the parallel path go untested. (#90, #67)

- HTTP transport hardening. No socket timeout, so a client that sent
  `Content-Length` and withheld the body pinned a handler thread forever. A
  deeply nested JSON body raised `RecursionError` past `except ValueError` and
  killed the handler thread **before** authentication. A non-ASCII bearer
  credential raised `TypeError` out of `authenticate()` the same way, and a JWK
  with `kty: RSA` but no `n`/`e` raised `KeyError`. Nested tool arguments took
  the thread down through the audit logger's unbounded recursion. A
  `Transfer-Encoding: chunked` body was never read, leaving bytes in the socket
  to desync the next request. An ordinary client disconnect injected a
  multi-line Python traceback into the stderr stream this package promises is
  strict JSON-lines. (#197, #232, #233, #234, #238, #249, #199)

- Audit sink survivability. A typo'd `--audit-log` killed the server with a
  traceback before it started, and `flock` failing on NFS/FUSE/overlay silently
  dropped every record to the file sink on POSIX while the Windows branch
  handled it — the "no lock available: still write" fallthrough was
  unreachable. The per-record `os.fsync`, taken while holding both the thread
  lock and the file lock, is now opt-in via `--audit-log-fsync`. (#201, #200,
  #244)

- Input validation and bounds. `--ref` reached the git argv without the
  leading-dash check `parse_spec` applies to owner/repo, so a value like
  `--upload-pack=…` was parsed as a flag. `decode_jwt` ignored a JWK's RFC 7517
  `use`/`key_ops`, accepting an encryption-only key for signature
  verification. `_challenge()` interpolated `oidc_issuer` into
  `WWW-Authenticate` without the module's own header sanitiser — now applied
  structurally to every `extra_headers` value. The `rag --answer` provider
  response was read unbounded on both axes, per line and in total. (#237,
  #246, #243, #241)

- Artifact and packaging truthfulness. `manifest.json` hardcoded "up to 5"
  CALLS edges, so an index built with `--max-call-candidates 2` shipped a
  manifest telling an agent to calibrate against a number the build never used;
  the effective value is now formatted into the prose and emitted as a
  top-level key. `load_vectors` never checked the `format` marker
  `write_vectors` has always stamped, so an unrecognised pair would have
  produced plausible, wrong rankings rather than degrading to BM25. (#245,
  #242)

- Build cost and subprocess hygiene. `cpp --version` was re-probed for every
  C/C++ file with a parse error — two spawns per file on a macro-heavy tree —
  and neither `cpp` invocation passed `stdin=subprocess.DEVNULL`, so under the
  stdio server they inherited the client's pipe. `MAX_COCHANGE_BYTES` promised
  in its own comment to be enforced during the read and was applied after
  `capture_output()` had already buffered the whole git log. (#239, #240, #236)

- The composite action's `version` input reached pip for the first time. It
  gated on `[ -f $GITHUB_ACTION_PATH/pyproject.toml ]`, which is always true
  for a composite action, so the input was accepted and ignored and the Action
  never installed the signed PyPI artifact `publish.yml` produces. (#204)

- Audit-log `high_entropy` redaction no longer treats ordinary snake_case
  identifier queries (`test_iss25_…`, `resolve_import_python3_relative`) as
  credentials. Vendor shapes (`ghp_…`, `AKIA…`, JWTs, …) are unchanged.

- `dependency-review.yml` ends with a newline, which the repo's own
  `end-of-file-fixer` pre-commit hook requires.

### Security

- `prod-igy` no longer starts privileged triage from an outsider `issue_comment`.
  The workflow `if:` and the script both require `author_association` in
  (`OWNER`, `MEMBER`, `COLLABORATOR`). Bot comments also strip backticks from
  `headRef` / `baseRef` so a fork branch name cannot break a markdown code span.

## [1.5.4] — 2026-09-17

### Changed

- Release version 1.5.4.

## [1.5.3] — 2026-09-17

### Changed

- Release version 1.5.3.

## [1.5.2] — 2026-09-17

### Security

- The audit log's `error` field is now redacted the same way every other
  value is. A downstream exception's `str()` can echo caller input verbatim
  (a malformed request, an OS error including a path with an embedded
  token), and that field previously bypassed `sanitize_value`.
- Every MCP string argument (`query`, `node_id`, `task_id`) is now
  length-capped in its handler, matching the existing numeric clamps on
  `k`/`hops`/`limit`/`budget_tokens`. Nothing downstream crashed on an
  unbounded string, but tokenising or scoring against an arbitrarily long
  one was wasted CPU no real query or node id needs.
- `claude-code-review.yml` now skips forked-repo pull requests explicitly
  (`if: github.event.pull_request.head.repo.full_name == github.repository`)
  rather than relying implicitly on GitHub's default secret redaction for
  `pull_request`-from-fork runs.
- A whole-repository security audit — architecture, threat model, trust
  boundaries and a prioritized findings list with evidence — is at
  `docs/SECURITY-AUDIT.md`. See also
  `docs/PRODUCTION_READINESS.md` (since removed),
  `docs/PERFORMANCE.md`,
  `docs/PRIVACY.md` and
  `docs/ENTERPRISE_DEPLOYMENT.md` (all new).

## [1.5.1] — 2026-09-16

### Added

- MCP `ToolAnnotations` across stdio and HTTP transports: `readOnlyHint=True`,
  `destructiveHint=False`, `idempotentHint=True`, `openWorldHint=False`.
- Informative parameter descriptions documenting formats (`sym:pkg/mod.py::func`,
  `file:path`, `dir:path`), bounds, and default values across all tool schemas.
- macOS-style framed browser window containers, rounded corners, and soft
  ambient drop shadows for all documentation screenshots (`graph-overview.png`,
  `graph-zoom.png`, `graph-sidebar.png`).

### Changed

- Enhanced all 5 MCP tool descriptions (`repo_map`, `repo_search`,
  `repo_neighbours`, `repo_cache_stats`, `repo_build_status`) to meet top-tier
  Glama Tool Definition Quality Score (TDQS) standards: active purpose verbs,
  explicit sibling disambiguation, concrete "When to use" / "When NOT to use"
  guidelines, and exact return shape specifications.
- Modernized `README.md` hero section with center-aligned branding, single-row
  badge bar, centered overview map, and balanced side-by-side canvas/controls table.
- Expanded tool description character budget test in `test_mcp.py` to 2,500 chars.

### Fixed

- Restored AuthorMark watermark fingerprints across modified files and `README.md`,
  resolving CI provenance verification.

## [1.5.0] — 2026-09-16

### Added

- `repo2graph build --incremental` reuses parse results for files whose content
  hash and language are both unchanged, via a new `agent/parse.cache.json`
  artifact. The whole resolution phase — the global name index, `CALLS`
  confidences, `INHERITS`, entrypoints and reach — is recomputed on every build,
  so an incremental index is byte-for-byte identical to a full rebuild rather
  than merely close. The build report gains an `incremental` block.
- `repo2graph embed --verify-rag` self-tests the dense-retrieval path: vectors
  present, model id, dimension, active-embedder agreement and per-chunk
  coverage. Exits non-zero with an actionable message when anything is broken.
- An HTTP transport for `repo2graph-mcp` (`--http-port`, `--http-host`,
  `--http-only`), serving JSON-RPC at `POST /mcp`. Every answer still comes from
  the same `dispatch()` the stdio transport uses.
- Bearer-token and OIDC authentication for that transport (`--auth-token`,
  `--auth-oidc-issuer`, `--auth-audience`, `--auth-jwks-ttl`), implemented with
  the standard library only — no new runtime dependency. `alg` is taken from the
  key rather than the token, the full PKCS#1 v1.5 block is compared rather than
  scanned, `iss`/`aud`/`exp`/`nbf` are enforced, and token comparison is
  constant time.
- `--auth-cimd` publishes an RFC 7591 client metadata document at
  `/.well-known/oauth-client-metadata`.
- `/.well-known/mcp-server-metadata` describes the server, its tools, its auth
  modes and whether an index exists, without needing a session. Unauthenticated
  by necessity, and therefore carrying no repository content.
- Structured audit logging: one JSON line per tool call on stderr, with
  `--audit-log <path>` and `--audit-log-level {none,errors,all}`. Values are
  redacted on shape as well as on field name, keeping a length and a short
  fingerprint so occurrences correlate without the log holding the secret.
- A bounded, expiring result cache for tool calls (`--cache-size`,
  `--cache-ttl`), dropped wholesale on any index rebuild, plus a
  `repo_cache_stats` tool.
- `ttlMs`/`cacheScope` cache metadata on `tools/list` over the HTTP transport.
- `--async-build` builds a missing index on a background thread and returns a
  task id immediately; `repo_build_status` polls it.
- A `rag_fusion_disabled` warning on stderr when dense fusion abandons itself
  because a shortlisted chunk has no vector, plus `Index.fusion_coverage`.
- `.github/workflows/dependency-audit.yml` runs `pip-audit --strict` over the
  full tree including the `rag` and `mcp` extras, on every PR to `main` and
  weekly.
- A `windows-latest` CI job that runs with `PYTHONIOENCODING=cp1252` and stdout
  redirected and piped, over a repository whose source contains U+2192, U+00E9
  and U+4E2D.
- `.pre-commit-config.yaml` running ruff and a version-consistency check.
- `glama.json`, and `uv.lock` so hosted builds are reproducible.
- A `packaging` CI job that installs the package the way a third-party host does
  — once without the `mcp` extra, asserting the refusal stays a legible sentence
  on stderr with nothing on stdout, and once with it, driving a real stdio round
  trip through the installed console script via `scripts/mcp_roundtrip.py`.

### Changed

- **Breaking:** `--viz-nodes 0` now draws an empty graph instead of meaning "no
  cap". `--viz-nodes all` is the no-cap spelling. One spelling for the two most
  opposite intentions a caller can have meant a mistyped or defaulted-to-zero
  argument silently rendered the largest possible page.
- `relayout()` in `graph.html` settles the force layout in batches across
  animation frames with a visible progress bar, instead of one synchronous loop
  that blocked the browser's main thread. The iteration cap defaults to 500 and
  is configurable via `data-max-iterations` on the graph container.
- Every CLI command writes through a single guarded `_emit`; six previously
  called `print()` directly and could raise `UnicodeEncodeError` on a redirected
  Windows stdout.
- Dependabot moved from monthly to weekly, with assignees.
- All `actions/checkout` pins unified on the verified v7.0.1 SHA.

### Fixed

- `answer._disclose()` no longer receives the provider dict that carries the
  resolved API key, closing CodeQL alert #1
  (`py/clear-text-logging-sensitive-data`).
- A URL check in the test suite compares a parsed hostname rather than a
  substring of an unparsed URL, closing CodeQL alert #2
  (`py/incomplete-url-substring-sanitization`).
- Four workflow pins whose comments named a different tag than the SHA they
  pinned.
- `events.encodable` no longer flattens ordinary characters to ASCII when a
  stream reports an encoding Python does not have.
- The stdio server reported the **MCP SDK's** version as its own in
  `serverInfo`, because `Server()` was constructed without `version=` and the
  SDK fills that field from its own package — so clients saw `1.30.0` against a
  1.4.0 release, and the two transports disagreed about what they were.

### Security

- Binding the HTTP transport beyond loopback with no authentication configured
  is refused at startup.
- Request bodies on the HTTP transport are bounded.
- `claude.yml` and `claude-code-review.yml` gained top-level `permissions`
  defaults.

## [1.4.0] — 2026-09-15

### Added

- An MCP server, `repo2graph-mcp`, serving an index over stdio with three tools:
  `repo_map`, `repo_search` and `repo_neighbours`. Behind the `mcp` extra.
- The index is built on the first tool call when one does not exist yet, so
  adding the server to a client needs no separate setup step.
- Dense retrieval became reachable: `repo2graph embed` persists vectors and
  `rag --vectors` fuses them with BM25.
- Token-denominated budgets (`--budget-tokens`) alongside character budgets.
- Publishing to PyPI and the MCP Registry from a single release workflow.

### Changed

- README cut to a landing page; the reference moved into `docs/`.

### Fixed

- `git` is kept off stdin in subprocess calls, so it cannot block on the MCP
  server's JSON-RPC pipe.

## [1.3.0] — 2026-09-11

### Added

- A unified GraphRAG engine: citation-carrying retrieval, graph expansion and
  optional LLM answer streaming (`rag --answer`).
- GraphRAG inputs on the GitHub Action, bringing it to parity with the CLI.
- A provenance workflow and a signed-commit gate.
- `CODEOWNERS`.

### Changed

- `langs` and `walker` merged into `parse.py`; `layout` merged into `export.py`.
- The version reported by `--version` was corrected; it had been stuck at 0.1.0
  through v1.0 to v1.2.

## [1.2.0] — 2026-09-09

### Fixed

A whole-repository audit landed as one batch:

- `chunks.py`: line-span accuracy, named constants.
- `viz.py`: UX fixes and safety against `__R2G_DATA__` placeholder injection.
- `export.py`: GraphML hardening and export correctness.
- `parse.py`: cross-module string and correctness fixes.
- `walker.py`: discovery hygiene and cache-directory skipping.
- `query.py`: retrieval budget and scoring hygiene.
- `fetch.py`: hardening round 2.
- Artifacts are written atomically through sibling temp files; chunks stream to
  disk rather than being materialised.

## [1.1.2] — 2026-09-09

### Fixed

- A release-workflow step referenced the wrong step output.
- A test wrote a line-separator fixture without an explicit encoding.

## [1.1.1] — 2026-09-06

### Changed

- The authormark tooling was de-vendored; `networkx` was dropped, leaving graph
  layout and GraphML generation as pure Python.
- Workflow permissions hardened and actions pinned to SHAs.

## [1.0.1] — 2026-08-30

### Fixed

- `query` against a partial index exits with a clear message instead of
  crashing.

### Added

- Authorship watermarks across the source tree.

## [1.0.0] — 2026-08-26

### Added

- First release: tree-sitter parsing into a code graph, JSONL/GraphML/Cypher
  exports, an interactive HTML map, retrieval chunks, and a GitHub Action.

[Unreleased]: https://github.com/Srinivasan-78/repo2graph/compare/v1.5.0...HEAD
[1.5.0]: https://github.com/Srinivasan-78/repo2graph/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/Srinivasan-78/repo2graph/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/Srinivasan-78/repo2graph/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/Srinivasan-78/repo2graph/compare/v1.1.2...v1.2.0
[1.1.2]: https://github.com/Srinivasan-78/repo2graph/compare/v1.1.1...v1.1.2
[1.1.1]: https://github.com/Srinivasan-78/repo2graph/compare/v1.0.1...v1.1.1
[1.0.1]: https://github.com/Srinivasan-78/repo2graph/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/Srinivasan-78/repo2graph/releases/tag/v1.0.0
