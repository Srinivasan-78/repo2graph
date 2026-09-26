"""The result cache, and the ways a cache in front of a code index goes wrong.

A cache that only ever gets faster is easy. The tests that matter are the ones
where it must *not* answer: an expired entry, an entry for an index that has
since been rebuilt, and a key collision between two calls that differ only in an
argument the key forgot to include. The last of those is the quiet one -- it
returns a plausible answer to the wrong question.
"""

import json

import pytest

from repo2graph import mcp
from repo2graph.cache import (
    CACHE_METADATA,
    DEFAULT_MAX_SIZE,
    DEFAULT_TTL,
    ResultCache,
    cache_metadata,
    make_key,
)


class Clock:
    """A monotonic clock a test can advance without sleeping."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


# ----------------------------------------------------------------- keys ----


def test_equal_arguments_produce_equal_keys_whatever_the_order():
    assert make_key("t", {"a": 1, "b": 2}) == make_key("t", {"b": 2, "a": 1})


def test_different_arguments_produce_different_keys():
    assert make_key("t", {"a": 1}) != make_key("t", {"a": 2})
    assert make_key("t", {"a": 1}) != make_key("u", {"a": 1})


def test_a_key_distinguishes_every_argument_that_changes_the_answer():
    """The quiet failure: a key that drops an argument returns a wrong answer.

    repo_search's k, hops and budget_tokens all change the result, so two calls
    differing only in one of them must not share a cache entry.
    """
    base = {"query": "routing", "k": 8, "hops": 1, "budget_tokens": 6000}
    keys = {make_key("repo_search", base)}
    for field, other in (("query", "auth"), ("k", 20), ("hops", 3), ("budget_tokens", 12000)):
        keys.add(make_key("repo_search", {**base, field: other}))
    assert len(keys) == 5, "two distinct calls collided on one key"


def test_a_key_is_bounded_however_large_the_arguments_are():
    """A key that embeds the request body amplifies memory 256x.

    `dispatch` keys on the *raw* arguments, before a handler's own
    `_str(query, MCP_MAX_QUERY_CHARS)` cap applies. Storing the serialised params
    verbatim meant 256 `repo_search` calls with a ~1 MB query each -- every one
    inside the HTTP server's MAX_BODY_BYTES -- retained ~257 MB in *keys*, for a
    cache whose values are bounded. Bounded here means constant, not merely
    smaller: a 1 MB and a 4 MB argument must both yield the same key length.
    """
    small = make_key("repo_search", {"query": "x" * 10})
    big = make_key("repo_search", {"query": "x" * 1_000_000})
    bigger = make_key("repo_search", {"query": "y" * 4_000_000})

    assert len(small) == len(big) == len(bigger) < 200, (
        f"key length tracks argument size: {len(small)} / {len(big)} / {len(bigger)}"
    )
    # Bounding must not collapse distinct arguments onto one entry.
    assert big != bigger
    assert big != small


@pytest.mark.parametrize("bad", ["caf\udce9", "\ud800", "\udfff", "a\ud800b\udce9c"])
def test_a_key_survives_any_lone_surrogate_in_an_argument(bad):
    """`make_key` promises never to raise; hashing the body must not break that.

    Two separate routes put a lone surrogate in an argument: AGENTS.md requires
    git/repo bytes be decoded `utf8`/`surrogateescape`, which produces the
    U+DC80-U+DCFF range, and a JSON-RPC caller can simply send `"\\ud800"`.

    The parametrization is the detector, not decoration. `surrogateescape` encodes
    U+DC80-U+DCFF fine and raises `UnicodeEncodeError` on everything else in the
    surrogate block, so a test using only `"caf\\udce9"` stays green against the
    wrong codec -- which is exactly what an earlier version of this test did.
    `\\ud800` and `\\udfff` are the cases that actually separate `surrogatepass`
    (correct here) from `surrogateescape`.
    """
    key = make_key("repo_search", {"query": bad})
    assert isinstance(key, str)
    assert key == make_key("repo_search", {"query": bad})
    assert key != make_key("repo_search", {"query": "plain"})


def test_list_and_dict_arguments_do_not_raise():
    """frozenset(params.items()) raises here; canonical JSON does not.

    JSON-Schema tool arguments permit arrays and objects, so a key built from a
    frozenset of items is a TypeError waiting for the first caller who sends
    one -- which is why this cache keys on canonical JSON instead.
    """
    key = make_key("t", {"globs": ["a", "b"], "opts": {"deep": True}})
    assert isinstance(key, str)
    assert key == make_key("t", {"opts": {"deep": True}, "globs": ["a", "b"]})


def test_an_unserialisable_argument_is_a_miss_not_a_crash():
    class Weird:
        pass

    assert isinstance(make_key("t", {"x": Weird()}), str)


# ---------------------------------------------------------------- basic ----


def test_a_second_identical_call_hits():
    cache = ResultCache()
    key = make_key("repo_search", {"query": "x"})
    assert cache.get(key) is None
    cache.put(key, "result")
    assert cache.get(key) == "result"
    assert cache.stats()["hits"] == 1 and cache.stats()["misses"] == 1


def test_entries_expire_after_the_ttl():
    clock = Clock()
    cache = ResultCache(ttl=60.0, clock=clock)
    cache.put("k", "v")
    clock.advance(59)
    assert cache.get("k") == "v"
    clock.advance(2)
    assert cache.get("k") is None, "an entry outlived its TTL"


def test_an_expired_entry_counts_as_a_miss():
    clock = Clock()
    cache = ResultCache(ttl=10.0, clock=clock)
    cache.put("k", "v")
    clock.advance(11)
    cache.get("k")
    assert cache.stats()["misses"] == 1 and cache.stats()["hits"] == 0


def test_the_least_recently_used_entry_is_evicted_first():
    cache = ResultCache(max_size=3)
    for name in "abc":
        cache.put(name, name)
    cache.get("a")  # a is now the most recently used
    cache.put("d", "d")  # evicts b, the least recently used

    assert cache.get("b") is None
    assert cache.get("a") == "a"
    assert cache.get("d") == "d"
    assert cache.stats()["evictions"] == 1


def test_the_cache_never_exceeds_max_size():
    """Unbounded growth in a long-lived server is a slow memory leak."""
    cache = ResultCache(max_size=10)
    for i in range(500):
        cache.put(f"k{i}", "x" * 1000)
    assert cache.stats()["size"] == 10


# Either knob at zero turns the cache off outright -- a `put` is accepted and
# then never returned. The `ttl` row previously checked only the missing read;
# both now assert `enabled` too, since a cache that reports itself enabled while
# storing nothing is the shape that misleads a caller.
@pytest.mark.parametrize(
    "kwargs",
    [pytest.param({"max_size": 0}, id="size-zero"), pytest.param({"ttl": 0}, id="ttl-zero")],
)
def test_zero_disables_the_cache(kwargs):
    cache = ResultCache(**kwargs)
    cache.put("k", "v")
    assert cache.get("k") is None
    assert cache.enabled is False


def test_clear_drops_every_entry_but_keeps_the_counters():
    """Counters describe the process; zeroing them hides the churn being hunted."""
    cache = ResultCache()
    cache.put("k", "v")
    cache.get("k")
    assert cache.clear() == 1
    assert cache.get("k") is None
    stats = cache.stats()
    assert stats["size"] == 0
    assert stats["hits"] == 1, "clear() must not reset the counters"


def test_stats_reports_every_documented_field():
    stats = ResultCache().stats()
    for field in ("hits", "misses", "size", "max_size", "ttl_s"):
        assert field in stats, field
    assert stats["max_size"] == DEFAULT_MAX_SIZE
    assert stats["ttl_s"] == int(DEFAULT_TTL)


# ------------------------------------------------------------- metadata ----


@pytest.mark.parametrize(
    "method,ttl,scope",
    [
        ("tools/list", 3_600_000, "global"),
        ("resources/list", 60_000, "session"),
        ("resources/read", 30_000, "session"),
    ],
)
def test_cache_metadata_matches_the_documented_defaults(method, ttl, scope):
    meta = cache_metadata(method)
    assert meta["ttlMs"] == ttl
    assert meta["cacheScope"] == scope


def test_an_unknown_method_has_no_hints():
    assert cache_metadata("tools/call") == {}


def test_cache_metadata_is_a_copy():
    """A caller mutating what it got back must not edit the table."""
    got = cache_metadata("tools/list")
    got["ttlMs"] = 1
    assert CACHE_METADATA["tools/list"]["ttlMs"] == 3_600_000


# ------------------------------------------------------------- dispatch ----


def test_dispatch_serves_a_repeat_from_the_cache(mini_index, monkeypatch):
    """Two identical repo_search calls: the second must not re-score."""
    index = mcp.open_index(mini_index)
    cache = ResultCache()

    calls = []
    real = mcp.tool_repo_search

    def counting(*a, **kw):
        calls.append(1)
        return real(*a, **kw)

    monkeypatch.setattr(mcp, "tool_repo_search", counting)
    args = {"query": "route request", "k": 4}
    first = mcp.dispatch(index, "repo_search", args, cache=cache)
    second = mcp.dispatch(index, "repo_search", args, cache=cache)

    assert first == second
    assert len(calls) == 1, "the second call re-ran the search"
    assert cache.stats()["hits"] == 1


def test_dispatch_without_a_cache_still_works(mini_index):
    index = mcp.open_index(mini_index)
    assert mcp.dispatch(index, "repo_map", {}) == mcp.dispatch(index, "repo_map", {})


def test_a_different_query_is_not_served_from_the_cache(mini_index):
    index = mcp.open_index(mini_index)
    cache = ResultCache()
    mcp.dispatch(index, "repo_search", {"query": "route request"}, cache=cache)
    mcp.dispatch(index, "repo_search", {"query": "audit event"}, cache=cache)
    assert cache.stats()["hits"] == 0 and cache.stats()["misses"] == 2


def test_cache_stats_is_never_itself_cached(mini_index):
    """A cached stats call reports the counters as they were when stored."""
    index = mcp.open_index(mini_index)
    cache = ResultCache()
    mcp.dispatch(index, "repo_search", {"query": "route request"}, cache=cache)
    first = json.loads(mcp.dispatch(index, "repo_cache_stats", {}, cache=cache))
    mcp.dispatch(index, "repo_search", {"query": "route request"}, cache=cache)
    second = json.loads(mcp.dispatch(index, "repo_cache_stats", {}, cache=cache))

    assert second["hits"] > first["hits"], "stats were served from the cache"


def test_cache_stats_reports_disabled_without_a_cache(mini_index):
    index = mcp.open_index(mini_index)
    stats = json.loads(mcp.dispatch(index, "repo_cache_stats", {}))
    assert stats["enabled"] is False


def test_an_unknown_tool_is_not_cached(mini_index):
    """Otherwise a confused caller fills the cache with invented names."""
    index = mcp.open_index(mini_index)
    cache = ResultCache()
    for i in range(20):
        mcp.dispatch(index, f"made_up_{i}", {}, cache=cache)
    assert cache.stats()["size"] == 0


def test_cache_stats_appears_in_the_tool_list():
    assert "repo_cache_stats" in mcp.TOOL_DESCRIPTIONS
    assert "repo_cache_stats" in mcp.TOOL_SCHEMAS


def test_a_rebuild_clears_the_cache(tmp_path, monkeypatch):
    """A rebuilt index must never serve an answer computed from the old one."""
    from conftest import write_mini_repo

    repo = write_mini_repo(tmp_path)
    out = tmp_path / ".r2g"
    cache = ResultCache()
    cache.put("stale", "answer from the previous index")

    monkeypatch.setattr(mcp, "_INDEXES", {})
    mcp.open_index(out, repo, cache)  # no index yet -> builds one

    assert cache.get("stale") is None, "the cache survived a rebuild"
