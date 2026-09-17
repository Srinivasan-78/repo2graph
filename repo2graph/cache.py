"""A bounded, expiring result cache for repeated tool calls.

Agents re-ask. A loop that reads a search result, follows a neighbour, then
re-runs the original query to re-read it is ordinary agent behaviour, and every
one of those repeats currently re-scores the whole BM25 index. The cache turns
the repeat into a dict lookup.

Three properties it must have, because a stale or unbounded cache in front of a
code index is worse than no cache:

* **Bounded.** LRU with a hard entry count. Tool results are markdown blocks of
  up to the token ceiling, so an unbounded cache is a slow memory leak in a
  long-lived server process.
* **Expiring.** A TTL, because the index it answers from is a file on disk that
  something else may rebuild. Sixty seconds is short enough that a rebuild
  during active use is noticed almost immediately.
* **Droppable.** `clear()` on any index rebuild, so a rebuilt index never
  serves an answer computed from the old one.

The cache key is canonical JSON of the arguments, not `frozenset(params.items())`.
The frozenset form looks equivalent and is not: it raises TypeError the moment
any argument is a list or a dict, both of which JSON-Schema tool arguments
permit, and it collapses `{"a": 1, "b": 2}` and `{"a": 2, "b": 1}` to different
keys only by luck of hashing rather than by construction. Canonical JSON is
total over every value a JSON-RPC caller can send, and equal inputs produce
equal keys by definition.
"""
import json
import threading
import time
from collections import OrderedDict
from typing import Any, Callable

# Entries kept before the least recently used is evicted.
DEFAULT_MAX_SIZE = 256
# Seconds an entry is served before it is recomputed.
DEFAULT_TTL = 60.0

# Cache metadata advertised on MCP list/read responses, in milliseconds.
# tools/list is the most stable thing this server exposes -- the tool set does
# not change while a process lives -- so it gets an hour and global scope.
TOOLS_LIST_TTL_MS = 3_600_000
# resources/list and resources/read answer from the index on disk, which can be
# rebuilt under a running server, so they are cached briefly and per session.
RESOURCES_LIST_TTL_MS = 60_000
RESOURCES_READ_TTL_MS = 30_000

CACHE_METADATA: dict[str, dict[str, Any]] = {
    "tools/list": {"ttlMs": TOOLS_LIST_TTL_MS, "cacheScope": "global"},
    "prompts/list": {"ttlMs": TOOLS_LIST_TTL_MS, "cacheScope": "global"},
    "resources/list": {"ttlMs": RESOURCES_LIST_TTL_MS, "cacheScope": "session"},
    "resources/read": {"ttlMs": RESOURCES_READ_TTL_MS, "cacheScope": "session"},
}

# Tool results worth caching. repo_map takes no arguments and is already cheap,
# but it is also the most repeated call an agent makes, so it caches too.
CACHEABLE_TOOLS = ("repo_map", "repo_search", "repo_neighbours")


def cache_metadata(method: str) -> dict[str, Any]:
    """The `ttlMs`/`cacheScope` hints for one MCP method.

    Args:
        method: An MCP method name, e.g. "tools/list".

    Returns:
        The hint mapping, or an empty dict for a method with no hints.
    """
    return dict(CACHE_METADATA.get(method, {}))


def make_key(tool: str, params: Any) -> str:
    """A total, stable cache key for one tool call.

    Args:
        tool: Tool name.
        params: The caller's arguments.

    Returns:
        A string key. Equal arguments always produce equal keys, whatever the
        insertion order, and no argument value can make this raise.
    """
    try:
        body = json.dumps(params or {}, sort_keys=True, default=str,
                          ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        # Unserialisable arguments cannot be compared for equality either, so
        # they get a key nothing else will match: a guaranteed miss beats a
        # wrong hit.
        body = repr(params)
    return f"{tool}\x00{body}"


class ResultCache:
    """A thread-safe LRU cache with per-entry expiry.

    Args:
        max_size: Entries kept before the least recently used is evicted. 0 or
            less disables the cache entirely, which stays a valid configuration
            rather than an error.
        ttl: Seconds an entry may be served for.
        clock: Monotonic time source, injected so tests can advance it without
            sleeping.
    """

    def __init__(self, max_size: int = DEFAULT_MAX_SIZE,
                 ttl: float = DEFAULT_TTL,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.max_size = int(max_size)
        self.ttl = float(ttl)
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    @property
    def enabled(self) -> bool:
        """False when the cache is configured away; every call is then a miss."""
        return self.max_size > 0 and self.ttl > 0

    def get(self, key: str) -> Any:
        """Return the cached value for `key`, or None when there is none.

        An expired entry is dropped and counted as a miss, so a caller cannot
        tell "expired" from "never seen" -- which is the point.

        Args:
            key: A key from `make_key`.

        Returns:
            The cached value, or None.
        """
        if not self.enabled:
            with self._lock:
                self.misses += 1
            return None
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None
            expires_at, value = entry
            if self._clock() >= expires_at:
                del self._entries[key]
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return value

    def put(self, key: str, value: Any) -> None:
        """Store `value` under `key`, evicting the oldest entry if needed."""
        if not self.enabled:
            return
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
            self._entries[key] = (self._clock() + self.ttl, value)
            while len(self._entries) > self.max_size:
                self._entries.popitem(last=False)
                self.evictions += 1

    def clear(self) -> int:
        """Drop every entry. Called whenever the index is rebuilt.

        Counters are deliberately *not* reset: they describe the life of the
        process, and zeroing them on every rebuild would hide exactly the churn
        an operator is looking at them to find.

        Returns:
            How many entries were dropped.
        """
        with self._lock:
            dropped = len(self._entries)
            self._entries.clear()
            return dropped

    def stats(self) -> dict[str, Any]:
        """Counters for the `repo_cache_stats` tool.

        Returns:
            hits, misses, size, max_size and ttl_s, plus eviction and hit-rate
            figures an operator needs to tell a working cache from a thrashing
            one. A cache at max_size with a hit rate near zero is sized wrong,
            and neither number says that alone.
        """
        with self._lock:
            total = self.hits + self.misses
            return {
                "hits": self.hits,
                "misses": self.misses,
                "size": len(self._entries),
                "max_size": self.max_size,
                "ttl_s": int(self.ttl),
                "evictions": self.evictions,
                "hit_rate": round(self.hits / total, 3) if total else 0.0,
                "enabled": self.enabled,
            }
