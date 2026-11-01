# Documentation Audit

Consolidated from `AGENT-FINDINGS/06-documentation.md`, with orchestrator
verification and one severity re-rating.

## Verdict

**Documentation quality is high and unusually well-verified.** Zero broken
relative links or images across 45 markdown files. Python API signatures, CLI
flag tables, `action.yml` inputs/outputs and MCP tool names were checked against
source and found byte-accurate. README's benchmark percentages were independently
recomputed from `benchmarks/real/results_holdout_final.json` and matched exactly —
a claim-verification result worth stating plainly, because unsupported benchmark
numbers are the norm in this product category.

The findings are a small set of **staleness** issues, concentrated in places a
version bump should have swept but did not.

## Findings

| ID | Severity | Confidence | Location | Finding |
|---|---|---|---|---|
| DOC-01 | **MEDIUM** | Confirmed (orchestrator-verified) | `.github/SECURITY.md:210,219` | Claims CI tests "Python 3.10–3.13"; CI actually tests 3.11–3.14 |
| DOC-02 | **MEDIUM** | Confirmed | `README.md:22-27` | "No network calls" exception list omits `--neo4j-uri` and `rag --tokenizer tiktoken`, both listed in SECURITY.md |
| DOC-03 | LOW-MEDIUM | Confirmed | `README.md:231` | "The 3.x CLI… follows semver" — the project is on 4.1.0, with 5.0.0 released |
| DOC-04 | LOW | Confirmed | `docs/mcp.md:240` | "first eight" tools should read "first seven" (7 question + 2 server = 9) |
| DOC-05 | INFO | Confirmed | `tests/golden/cli_inventory.json` | Covers only 9 of ~17 subcommands while appearing exhaustive |

### DOC-01 — SECURITY.md misstates the tested Python matrix

**Orchestrator-verified.** `.github/SECURITY.md:210` and `:219` state the CI
matrix is Python 3.10–3.13. `.github/workflows/ci.yml:144` is
`["3.11", "3.12", "3.13", "3.14"]`. Both the floor and the ceiling are wrong.

**Severity re-rated from HIGH to MEDIUM.** The documentation specialist rated this
HIGH because SECURITY.md is the trust document enterprise reviewers read. That
reasoning is sound and the finding is real, but the consequence is a false
factual claim about test coverage, not an exploitable weakness or an instruction
that leads a user into insecure action. MEDIUM, with the note that it should be
fixed promptly *because* of where it lives.

Notably, `README.md:241` gets this exactly right — it states 3.11–3.14, 12 CI
cells, and even explains that 3.10 was supported through 4.1.0 and dropped after
EOL in October 2026. The 3.10-drop commit swept README and missed SECURITY.md.

### DOC-02 — README's network-exception list is incomplete

README enumerates the exceptions to "no network calls" as `rag --answer`,
`repo2graph github` / GitHub-spec targets, and `--vectors`/`embed`. SECURITY.md
additionally lists `--neo4j-uri` and `rag --tokenizer tiktoken`.

Both omissions are genuine outbound-network paths. `--neo4j-uri` is arguably
self-evident to anyone passing a database URI, but `rag --tokenizer tiktoken`
is not obvious at all — a user who has read README's guarantee would not expect a
tokenizer flag to reach the network. Since the no-network promise is the
project's central security claim and its enterprise-adoption argument, the
canonical list should be complete and identical in both documents.

**Recommendation:** make SECURITY.md's fuller list the single source of truth and
have README link to it rather than restate it. Effort: **S**.

### DOC-03 / DOC-04 — Version and count slips

README:231 refers to "the 3.x CLI" following semver while the repository is at
4.1.0 and v5.0.0 is published — a reader cannot tell which compatibility promise
applies to them. `docs/mcp.md:240` has an arithmetic slip in tool counts. Both
trivial. Effort: **S** each.

### DOC-05 — The CLI inventory golden file is not exhaustive

`tests/golden/cli_inventory.json` covers 9 of roughly 17 subcommands, omitting
`diff`, `explain-path`, `bug-report`, `index-status`, `demo`, `doctor`,
`completion` and `explain`. The file reads as a complete inventory, so its
existence creates false confidence that CLI-surface drift is guarded. It is
recorded as INFO because nothing is *wrong* — the gap is in coverage, and it
belongs with the testing findings.

## Undocumented surfaces

From the configuration review (`AGENT-FINDINGS/11-config-compat-ux.md`, CFG-01):
three environment variables are read by code but documented nowhere —
`REPO2GRAPH_CACHE_DIR`, `REPO2GRAPH_ALLOW_INSECURE_OLLAMA`, `GITHUB_SERVER_URL`.

`REPO2GRAPH_ALLOW_INSECURE_OLLAMA` matters most: it is a security-relevant
bypass, and a user can currently only discover it by triggering the error it
suppresses. An undocumented security bypass is worse than a documented one.
Effort: **S**.

## Completeness assessment

Can a newcomer succeed at each task?

| Task | Verdict |
|---|---|
| Understand the project | **Yes.** README opens with a concrete value statement and a demo GIF. |
| Install it | **Yes**, across all channels (pip/uvx, npm, Docker, Action, VS Code). |
| Configure it | **Mostly** — precedence and validation are correct and documented; three env vars are not. |
| Use the primary workflow | **Yes.** `demo` is a genuinely good first-run affordance, and `docs/cli.md` walks it with expected output. |
| Interpret output and citations | **Yes.** Citation format is explained up front. |
| Troubleshoot | **Partly.** `doctor` and `bug-report` exist, but `doctor` itself has a defect (COMPAT-01 in `DEPENDENCY-AND-COMPATIBILITY.md`) that makes it emit a false warning on most Python 3.13 installs. |
| Integrate it | **Yes.** MCP, CI and library paths each have dedicated docs. |
| Contribute | **Yes.** `CONTRIBUTING.md` plus Makefile targets that faithfully predict CI. |

## Verified-accurate claims

Recorded because verification that *passes* is a finding too:

- All relative links and image paths across 45 markdown files resolve. Zero broken.
- `docs/python-api.md` signatures match the real exports.
- `docs/cli.md` flag tables match `cli.py`.
- `action.yml` inputs/outputs match `tests/golden/action_inputs.json` / `action_outputs.json`.
- `docs/mcp.md` tool names match `mcp/tools.py` and `schemas.py`.
- README benchmark percentages recompute exactly from the committed raw results.
- `README.md:241`'s Python support matrix is correct and well-explained.
- Language-support claims align with the generated scorecard.

## A caveat on which docs users actually see

Everything above audits the `develop` branch. The GitHub **default branch is
`main`**, which sits at version 2.2.0 and is 240 commits behind. A visitor to the
repository, or anyone cloning it, reads the **2.2.0-era documentation**, not the
documentation audited here. See `REL-01` in `CI-CD-ASSESSMENT.md` — this
substantially outweighs every individual finding in this report.

## Limitations

- External links were catalogued and judged by inspection; they were not fetched,
  to avoid mass outbound requests.
- `CHANGELOG.md` (226 KB) was spot-checked, not read end to end.
- The `.github/outreach/*` documents were treated as internal drafts, not
  user-facing documentation.
