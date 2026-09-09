# Backlog — deferred audit findings

Route back to the work the 2026-09 whole-repo audit found but did not fix in the
first batch. Full detail lives in `BUILD_STATE.md` (`## Plan` MASTER ISSUE TABLE
= all 53 findings; `## Improve` = the grouped backlog and loop retro).

**Epic:** [#31 — Epic: post-audit backlog](https://github.com/Srinivasan-78/repo2graph/issues/31)

| Issue | Scope | Covers | Priority |
|-------|-------|--------|----------|
| [#26](https://github.com/Srinivasan-78/repo2graph/issues/26) | fetch.py hardening round 2 | ISS-21, SH-2, SH-3, NC-4, NC-5 | P1 |
| [#21](https://github.com/Srinivasan-78/repo2graph/issues/21) | parse.py & cross-module string/correctness one-liners | ISS-03/04/05/09/11/12/41/42, NC-6 | P2 |
| [#23](https://github.com/Srinivasan-78/repo2graph/issues/23) | export.py correctness & GraphML hardening round 2 | ISS-28/29/30/31, SH-4 | P2 |
| [#24](https://github.com/Srinivasan-78/repo2graph/issues/24) | viz.py UX + safety | ISS-33/34/35/36 | P2 |
| [#25](https://github.com/Srinivasan-78/repo2graph/issues/25) | query.py retrieval budget + scoring hygiene | ISS-37/38/39 | P2 |
| [#27](https://github.com/Srinivasan-78/repo2graph/issues/27) | walker.py discovery hygiene | ISS-14/15, SH-5 | P2 |
| [#28](https://github.com/Srinivasan-78/repo2graph/issues/28) | Test coverage round 2 | ISS-52/53, SH-6, NC-1/2/3 | P2 |
| [#29](https://github.com/Srinivasan-78/repo2graph/issues/29) | CI, supply-chain & workflow/doc hygiene | ISS-43/46/47/48/49 | P2 |
| [#30](https://github.com/Srinivasan-78/repo2graph/issues/30) | authormark: refresh stale Fingerprint lines on batch-1 files (not merge-blocking — CI checks presence only) | AC-16, SH-7 | P2 |
| [#22](https://github.com/Srinivasan-78/repo2graph/issues/22) | chunks.py line-span accuracy, id scheme & tidy | ISS-23/24/25/26 | P3 |

All child issues carry the `backlog` label. To work one, run a `/build-app`
refactor loop scoped to a single issue (or a single module group).

## Shipped in batch 1

Branch `audit/batch-1-encoding-hardening` — 15 High/Med in-scope fixes
(ISS-01/02/06/07/13/16/17/18/19/22/27/44/45/50/51) + 5 one-liners
(ISS-08/10/20/32/40) + SH-1. Suite: 73 passed, 2 skipped.
