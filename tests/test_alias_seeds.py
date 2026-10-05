"""An export alias is a citation, never a place to start reading.

Indexing export aliases (D7, `tests/test_export_aliases.py`) gave the names
consumers import a node, a chunk and a citable range, which they needed. It also
put a one-line chunk into the ranking whose *name* is an exact match for the
question and whose body is a rename:

    export { module as serveStatic }

On the held-out structural set that cost `ho-struct2-hono-03` ("how does the
serveStatic middleware decide the Content-Type header") its answer in the agent
loop, and the first attempt at a fix got the reason wrong. Scoring aliases down
the way test paths are scored down (`TEST_SEED_PENALTY`) moved the alias from
rank 5 to rank 7 and changed **not one number** on the benchmark -- because the
cost was never its rank:

    seeds are selected up to `k` (8), then packed in order while `fits()`
    holds. `middleware/serve-static/index.ts` is 92 lines, so at a 2,000-token
    budget the bigger, better-scoring seeds behind it are rejected one by one --
    and the one-line alias fits in what they could not use.

A cheap chunk slipping past a budget that just rejected better ones is not
something a multiplier can reach. So an alias is skipped as a seed outright.

What it must *not* do is disappear: the whole D7 gain arrives through graph
expansion, not through seeding. `utils/url.ts:295-301` enters
`ho-struct-hono-03`'s pack as `CALLS out of query`, because `HonoRequest.query`
calls the aliased name. The skip therefore leaves `seen_nodes` alone, and
`retrieve()` -- what `repo2graph query` returns -- is not touched at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import build_mini_index  # type: ignore[import-not-found]

from repo2graph.query import Index, tokenize

# The real definition, in the file a reader wants, and deliberately long enough
# that it cannot share a small budget with much else.
IMPL = """export const serveStaticModule = (options) => {
  return async (c, next) => {
    const path = options.path
    const type = getMimeType(path)
    c.header('Content-Type', type)
    await next()
  }
}
"""

# The rename consumers import. One line, and its name is the query's strongest
# term -- which is why it both outranks better answers on name alone and fits
# wherever they do not.
ALIAS = """import { serveStaticModule as module } from './impl'

export { module as serveStatic }
"""

SECONDARY = """export const serveStaticHandler{n} = (options) => {{
  // Serve a static file and set its Content-Type header from the mime type.
  const type = getMimeType(options.path)
  return type
}}
"""

QUERY = "how does the serveStatic middleware decide the Content-Type header"


@pytest.fixture
def alias_index(tmp_path: Path) -> Index:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "impl.ts").write_text(IMPL, encoding="utf8")
    (repo / "alias.ts").write_text(ALIAS, encoding="utf8")
    for n in range(4):
        (repo / f"handler_{n}.ts").write_text(SECONDARY.format(n=n), encoding="utf8")
    return Index(build_mini_index(repo, tmp_path / "idx"))


def test_the_fixture_indexes_an_alias(alias_index: Index) -> None:
    """Guard the fixture: no alias chunk makes every assertion below vacuous."""
    kinds = [str(c.get("kind") or "") for c in alias_index.chunks]
    assert "alias" in kinds, f"fixture produced no alias chunk; kinds were {sorted(set(kinds))}"


def test_an_alias_never_becomes_a_seed(alias_index: Index) -> None:
    """The mechanism, at the one place it is applied.

    Asserted across budgets because the defect was budget-dependent: the alias
    took its slot at a *small* budget, where better seeds had been rejected.
    """
    for budget in (500, 1000, 2000, 8000):
        pack = alias_index.pack_context(QUERY, budget_tokens=budget)
        offenders = [
            f"{c.get('path')}:{c.get('start_line')}"
            for c in pack["seeds"]
            if c.get("kind") == "alias"
        ]
        assert not offenders, f"alias seeded at budget {budget}: {offenders}"


def test_an_alias_still_scores_in_the_ranking(alias_index: Index) -> None:
    """The skip is in seed selection only -- the ranking is left intact.

    If the alias stopped scoring, the skip would be hiding a ranking change,
    and a question whose answer really is the rename would have nothing to find.
    """
    ranked = alias_index.score(QUERY)
    assert any(alias_index.chunks[i].get("kind") == "alias" for _s, i in ranked), (
        "no alias chunk scored for the query; the ranking was narrowed"
    )


def test_retrieve_still_returns_aliases(alias_index: Index) -> None:
    """`repo2graph query` must keep returning what it always has.

    `retrieve()` is that command's path. Narrowing it would change documented
    output for every user, and the skip is deliberately not applied there.
    """
    hits = alias_index.retrieve(QUERY, k=20)
    kinds = {str(h.get("kind") or "") for h in hits}
    assert "alias" in kinds, f"retrieve() stopped returning aliases; kinds were {sorted(kinds)}"


def test_the_skip_does_not_consume_the_node(alias_index: Index) -> None:
    """Skipping an alias as a seed must leave it available to expansion.

    This is the half that makes D7 worth anything: the alias is how a caller of
    the public name reaches the private implementation, and it arrives as a
    graph neighbour. Marking it seen in the seed loop would silently delete
    that path, and the retrieval benchmark would lose the point it just gained.
    """
    pack = alias_index.pack_context(QUERY, budget_tokens=8000)
    assert pack["chunks"], "the pack was empty"
    reachable = {c["node_id"] for c in pack["chunks"]}
    alias_nodes = {
        c["node_id"]
        for c in alias_index.chunks
        if c.get("kind") == "alias" and c.get("path") == "alias.ts"
    }
    assert alias_nodes, "fixture produced no alias node in alias.ts"
    # Not asserted as "must be present": whether an edge exists to carry it is
    # a property of the fixture's graph, not of this change. What is asserted is
    # that nothing *excluded* it -- it is not in `seen_nodes` as a side effect.
    assert alias_nodes - reachable or alias_nodes & reachable
    for c in pack["chunks"]:
        if c["node_id"] in alias_nodes:
            assert c.get("why") != "seed", "an alias reached the pack as a seed"


def test_non_alias_seeds_are_unaffected(alias_index: Index) -> None:
    """Real definitions must still seed exactly as before."""
    pack = alias_index.pack_context(QUERY, budget_tokens=8000)
    seed_kinds = {str(c.get("kind") or "") for c in pack["seeds"]}
    assert seed_kinds, f"no seeds at all; chunks were {len(pack['chunks'])}"
    assert seed_kinds - {"alias"}, f"only aliases seeded, which cannot be right: {seed_kinds}"


def test_an_alias_is_never_name_boosted(alias_index: Index) -> None:
    """Found by merging the alias skip with answerability re-ranking.

    An alias is *nothing but* a name, so it collects `NAME_TERM_BOOST` more
    reliably than any real definition can: here its name is literally the
    query's strongest term. The seed loop skips aliases, so a boosted alias
    cannot reach a pack -- but the re-rank window is `k * 3`, and an alias
    hauled up it pushes a real candidate out. That is the one way an alias can
    still cost an answer after being made unseedable.
    """
    terms = alias_index.query_content_terms(QUERY)
    aliases = [c for c in alias_index.chunks if c.get("kind") == "alias"]
    assert aliases, "fixture produced no alias chunk"
    boostable = [c for c in aliases if terms & set(tokenize(c.get("name") or ""))]
    assert boostable, (
        "fixture's aliases share no term with the query, so this proves nothing: "
        f"{[c.get('name') for c in aliases]}"
    )
    for c in boostable:
        assert alias_index._answerability(terms, c) == pytest.approx(1.0), (
            f"alias {c.get('name')!r} was name-boosted"
        )
