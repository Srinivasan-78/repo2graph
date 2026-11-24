"""The on-disk format contract: what is versioned, and what a mismatch does.

Five major releases shipped in under two months and nothing asserted what
happens to an index written by an older one. A schema change would have broken
every user's cache with no CI signal -- not because the code lacks version
handling, but because nothing pinned the handling it has.

What this module fixes in place:

1. **The version literals are pinned.** All five of them -- there are more
   than the names suggest, and they do not move together -- are asserted
   against hand-written values, so bumping one is a deliberate two-line edit
   with this file in the diff rather than an invisible consequence of an
   export change. Per CONTRIBUTING.md, the expected values here are
   hand-derived and never recomputed from the code under test.

2. **Each mismatch has an asserted outcome**, and they differ on purpose:
   the sqlite cache is *derived* data, so a format bump silently rebuilds it;
   the `.r2g` index is *source* data the user built, so an unreadable format is
   refused with an actionable message instead of being guessed at.

The failure this excludes is not "an old index raises" -- it is an old index
being read as if it were current and answering with wrong data.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from conftest import write_simple_repo

from repo2graph.cli import main
from repo2graph.export import EDGE_SCHEMA_VERSION, INDEX_SCHEMA_VERSION
from repo2graph.export import path as artifact_path
from repo2graph.query import SUPPORTED_INDEX_FORMATS, Index
from repo2graph.store import SQLITE_CACHE_NAME, SQLITE_STORE_FORMAT, SqliteGraphStore

# Hand-written, not imported arithmetic. Changing any of these means changing
# this file, which is the point.
#
# There are four of them, and they are independent. That is worth stating
# plainly because the names invite the assumption that they move together:
#
#   manifest `format`      -- "repo2graph/1". The *only* one a reader gates on;
#                             `Index` refuses anything outside
#                             `SUPPORTED_INDEX_FORMATS`.
#   manifest schema_version-- 1. Records the manifest's own shape. Written as a
#                             literal and not currently enforced by any reader.
#   INDEX_SCHEMA_VERSION   -- "2". The index/stats payload shape.
#   EDGE_SCHEMA_VERSION    -- "2". The edge record shape.
#   SQLITE_STORE_FORMAT    -- 2. The derived cache, which *is* enforced.
EXPECTED_INDEX_FORMATS = {"repo2graph/1"}
EXPECTED_MANIFEST_SCHEMA_VERSION = 1
EXPECTED_INDEX_SCHEMA_VERSION = "2"
EXPECTED_EDGE_SCHEMA_VERSION = "2"
EXPECTED_SQLITE_STORE_FORMAT = 2


@pytest.fixture
def built(tmp_path):
    """A real built index, and its output directory."""
    src = tmp_path / "src"
    src.mkdir()
    write_simple_repo(src)
    out = tmp_path / "out"
    assert main(["build", str(src), "-o", str(out), "--formats", "jsonl"]) == 0
    return out


# --------------------------------------------------------------------------
# 1. The version surfaces are pinned
# --------------------------------------------------------------------------


def test_the_index_format_identifier_is_what_readers_expect():
    """`SUPPORTED_INDEX_FORMATS` is the set `Index` agrees to open.

    Widening it is a compatibility promise and narrowing it breaks every index
    already on disk, so neither should happen as a side effect.
    """
    assert set(SUPPORTED_INDEX_FORMATS) == EXPECTED_INDEX_FORMATS


def test_the_artifact_schema_versions_are_pinned():
    assert INDEX_SCHEMA_VERSION == EXPECTED_INDEX_SCHEMA_VERSION
    assert EDGE_SCHEMA_VERSION == EXPECTED_EDGE_SCHEMA_VERSION


def test_the_sqlite_store_format_is_pinned():
    assert SQLITE_STORE_FORMAT == EXPECTED_SQLITE_STORE_FORMAT


def test_a_built_index_records_the_format_and_schema_version(built):
    """The versions reach the manifest. A reader that cannot find them cannot
    make a compatibility decision at all, which is the silent-wrong-answer
    case rather than the loud one.

    `schema_version` is the manifest's own shape and is deliberately *not*
    `INDEX_SCHEMA_VERSION`: they are 1 and "2" respectively, and asserting
    them separately is what keeps that from looking like a bug to the next
    reader who notices.
    """
    manifest = json.loads(artifact_path(built, "manifest.json").read_text(encoding="utf8"))
    assert manifest["format"] in EXPECTED_INDEX_FORMATS
    assert manifest["schema_version"] == EXPECTED_MANIFEST_SCHEMA_VERSION


# --------------------------------------------------------------------------
# 2. An unreadable .r2g index is refused, not guessed at
# --------------------------------------------------------------------------


def test_an_unsupported_index_format_is_refused_with_an_actionable_message(built):
    """A future index must not be read as if it were this one.

    The user built this index, so it is not ours to discard or silently
    rebuild; the only safe answers are "open it" or "say why not".
    """
    path = artifact_path(built, "manifest.json")
    manifest = json.loads(path.read_text(encoding="utf8"))
    manifest["format"] = "repo2graph/99"
    path.write_text(json.dumps(manifest), encoding="utf8")

    with pytest.raises(ValueError) as excinfo:
        Index(built)

    message = str(excinfo.value)
    assert "repo2graph/99" in message, f"the error does not name the format it refused: {message}"
    assert "repo2graph/1" in message, (
        f"the error does not say what it would accept, so it is not actionable: {message}"
    )


def test_the_refusal_happens_at_construction_not_at_first_read(built):
    """`Index(...)` itself refuses, so no partly-usable object exists.

    This is the stronger of the two possible designs and worth pinning as the
    contract: if construction succeeded and only some accessors validated, a
    caller could hold an `Index` over an unreadable format and get an answer
    from whichever path forgot to check. There is no such window -- the object
    is never created.
    """
    path = artifact_path(built, "manifest.json")
    manifest = json.loads(path.read_text(encoding="utf8"))
    manifest["format"] = "repo2graph/99"
    path.write_text(json.dumps(manifest), encoding="utf8")

    with pytest.raises(ValueError, match="repo2graph/99"):
        Index(built)


# --------------------------------------------------------------------------
# 3. The sqlite cache is derived, so a stale format rebuilds silently
# --------------------------------------------------------------------------


def test_a_cache_from_an_older_store_format_is_rebuilt_not_served(built):
    """The cache is derived data: regenerating it is free and correct.

    Written with a stale `format` in `meta`, it must be replaced rather than
    read. Serving it would answer from a schema the reader no longer
    understands -- the same wrong-data failure as above, with no user action
    available to cause or fix it.
    """
    from repo2graph import store as store_mod

    db = built / SQLITE_CACHE_NAME
    store = store_mod.open_graph_store(built, "sqlite")
    assert store is not None
    want_nodes = sorted(n["id"] for n in store.iter_nodes())
    store.close()
    assert db.is_file(), "the sqlite cache was not written beside the index"
    assert want_nodes

    # Age the cache: a format one behind, as a previous release would have left.
    con = sqlite3.connect(db)
    try:
        con.execute(
            "UPDATE meta SET value = ? WHERE key = 'format'",
            (str(EXPECTED_SQLITE_STORE_FORMAT - 1),),
        )
        con.commit()
    finally:
        con.close()
    # Closed before reopening: an open handle here is an exclusive lock on
    # Windows, and the rebuild below has to be able to replace the file.
    aged = SqliteGraphStore(db)
    try:
        assert aged.meta("format") == str(EXPECTED_SQLITE_STORE_FORMAT - 1)
    finally:
        aged.close()

    reopened = store_mod.open_graph_store(built, "sqlite")
    assert reopened is not None
    try:
        assert reopened.meta("format") == str(EXPECTED_SQLITE_STORE_FORMAT), (
            "a cache written by an older store format was served instead of rebuilt"
        )
        assert sorted(n["id"] for n in reopened.iter_nodes()) == want_nodes, (
            "the rebuilt cache does not hold the same graph as the index"
        )
    finally:
        reopened.close()


def test_a_cache_built_for_another_index_is_rebuilt_not_served(built):
    """The same protection keyed on `build_id` rather than format.

    Two indexes can occupy the same path over time; a cache whose `build_id`
    does not match the index beside it describes a different repository state.
    """
    from repo2graph import store as store_mod

    db = built / SQLITE_CACHE_NAME
    store = store_mod.open_graph_store(built, "sqlite")
    assert store is not None
    store.close()

    con = sqlite3.connect(db)
    try:
        con.execute("UPDATE meta SET value = 'some-other-build' WHERE key = 'build_id'")
        con.commit()
    finally:
        con.close()

    reopened = store_mod.open_graph_store(built, "sqlite")
    assert reopened is not None
    try:
        assert reopened.meta("build_id") != "some-other-build", (
            "a cache built for a different index was served"
        )
    finally:
        reopened.close()
