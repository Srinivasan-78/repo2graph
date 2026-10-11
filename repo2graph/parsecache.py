"""The incremental parse cache: one file's parse result to JSON and back (#315)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .parse import ImportDetail, ParsedFile, Symbol


@dataclass
class ChunkedParsedFile(ParsedFile):
    """What `_chunk_and_parse` produces: a `ParsedFile` plus what it could not read.

    `undecodable_slices` does not belong on `ParsedFile` itself -- it is
    meaningless for the whole-file reader, which either decodes a file or does
    not. Every reader of the field uses `getattr(pf, ..., 0)`, the same way
    `build()` already reads `is_chunked` and `used_cpp`.
    """

    undecodable_slices: int = 0


# Bumped whenever a cache entry's shape changes. A cache written by an older
# repo2graph is ignored wholesale rather than half-read: a `Symbol` that gained
# a field would otherwise reconstruct with a silently wrong default, and a wrong
# symbol is exactly the "wrong in a way nothing detects" failure this feature
# was cut for in the first place.
# 2: chunked entries gained "undecodable_slices". A cache written by
# format 1 has no way to report it, and an incremental build restoring one would
# report a 0 where a full build reports the real count -- the one thing an
# incremental build is not allowed to do.
# 3: `Symbol` gained "call_details"/"base_details" and `ParsedFile` gained
# "import_details". A format-2 entry has none of the three: every cached call
# would silently reconstruct as call_kind='static' even where the original was
# a decorator/dynamic call, every cached base would reconstruct with
# subtype='INHERITS' regardless of what it actually was, and every cached
# file's import aliases would come back as [] -- an aliased call that should
# resolve `import_alias` would instead fall through to unresolved_external.
# 5: each "call_details" entry gained "receiver". A format-4 entry would restore
# every call as receiver-less, so an incremental build would keep binding
# `d.get()` to an in-repo `get` at 1.0 where a full build prices it as a guess.
# 6: call_details gained "receiver_head"/"receiver_static", decorator entries
# gained "receiver", and Kotlin/Swift/Go receivers are classified. A format-5
# entry has no head, so `store.get()` on an imported module would stay demoted
# on an incremental build while a full build binds it at 1.0.
# 7: `Symbol` gained "key"/"parent_key" (a later same-qualname definition in
# one file gets id `<qualname>@L<line>` instead of colliding with the first),
# Go methods are qualified `<ReceiverType>.<name>`, Kotlin `fun`s are indexed
# at all, and chunked files key duplicates as `@L<line>` rather than renaming
# the qualname `<qualname>_<n>`. A format-6 entry restores Go methods with bare
# qualnames and no Kotlin functions, so an incremental build would emit
# different node ids than a full one.
# 8: `base`/`parent` receivers classify as "self" only in C# / PHP (static
# `parent::`), and PHP `scoped_call_expression` calls are recorded. A format-7
# entry keeps a Python `parent.add()` as a super call (-> external) and drops
# PHP `Foo::bar()` calls, so an incremental build would disagree with a full one.
# Chunked files also re-key children per slice (a duplicate's children used to
# attach to the first same-name definition), which changes cached parent_key.
# 9: JS/TS export aliases are symbols of kind "alias" (`export const public =
# _private as T`, `export { Hono as HonoBase }`). A format-8 entry has none of
# them, so an incremental build would restore a file with fewer nodes than a
# full build gives it -- and every call to the aliased name would stay
# unresolved on the incremental side only.
PARSE_CACHE_FORMAT = 9


def cache_entry(
    lang: str | None, size: int, lines: int, pf: ParsedFile | None, digest: str
) -> dict[str, Any]:
    """Serialise one file's parse result for `parse.cache.json`.

    Args:
        lang: Language id the file was parsed as, or None for a non-code file.
        size: Length in bytes of the file as indexed.
        lines: Newline count + 1, as recorded on the file node.
        pf: The `ParsedFile` for this file, or None if it was not parsed.
        digest: sha256 hex digest of the bytes this entry describes.

    Returns:
        A JSON-serialisable dict holding everything `build()` needs to rebuild
        this file's nodes and edges without re-reading or re-parsing it.
    """
    return {
        "sha256": digest,
        "lang": lang or "",
        "size": size,
        "lines": lines,
        "parsed": None
        if pf is None
        else {
            "lang": pf.lang,
            "parse_errors": pf.parse_errors,
            "used_cpp": pf.used_cpp,
            "is_chunked": getattr(pf, "is_chunked", False),
            # Only the chunked reader can lose a slice, and only it sets this.
            "undecodable_slices": getattr(pf, "undecodable_slices", 0),
            "imports": list(pf.imports),
            # `refs` only when there are some: a default build's cache is unchanged.
            "symbols": [
                {k: v for k, v in asdict(s).items() if k != "refs" or v} for s in pf.symbols
            ],
            # Without this, entry_read() rebuilds import_details as [] and an
            # incremental cache hit loses every import alias -- an aliased call
            # that a full build resolves as `import_alias` falls back to
            # unresolved_external on the next incremental build instead.
            "import_details": [asdict(d) for d in getattr(pf, "import_details", [])],
            # Index-aligned with "imports", and the IMPORTS edge's `evidence`
            # comes from it. Without it here, an incremental build emits edges
            # with no evidence where a full build emits a line -- which the
            # byte-equality tests catch, and which would otherwise make a
            # cached index quietly less citable than a fresh one.
            "import_lines": list(getattr(pf, "import_lines", []) or []),
        },
    }


def entry_read(entry: dict[str, Any]) -> tuple[int, int, ParsedFile | None, str] | None:
    """Rebuild `_read_and_parse`'s result tuple from a cache entry.

    Args:
        entry: One record out of `parse.cache.json`.

    Returns:
        The `(size, lines, ParsedFile | None, digest)` tuple the build loop
        consumes, or None if the entry is malformed. A malformed entry is a
        cache miss, never an exception: a corrupt cache must cost a re-parse,
        not the build.
    """
    try:
        size, lines = int(entry["size"]), int(entry["lines"])
        digest = str(entry["sha256"])
        raw = entry.get("parsed")
        if raw is None:
            return size, lines, None, digest
        symbols = [Symbol(**s) for s in raw["symbols"]]
        import_details = [ImportDetail(**d) for d in raw.get("import_details", [])]
        import_lines = [int(n) for n in raw.get("import_lines", [])]
        pf: ParsedFile
        # A chunked entry restores as the same class a fresh chunked read
        # produces, so a cached file and a re-parsed one are indistinguishable
        # -- including to `==`, which a dataclass only answers true for within
        # one class.
        if raw.get("is_chunked"):
            pf = ChunkedParsedFile(
                lang=str(raw["lang"]),
                symbols=symbols,
                imports=[str(i) for i in raw["imports"]],
                parse_errors=int(raw.get("parse_errors") or 0),
                used_cpp=bool(raw.get("used_cpp") or False),
                is_chunked=True,
                undecodable_slices=int(raw.get("undecodable_slices") or 0),
                import_details=import_details,
                import_lines=import_lines,
            )
        else:
            pf = ParsedFile(
                lang=str(raw["lang"]),
                symbols=symbols,
                imports=[str(i) for i in raw["imports"]],
                parse_errors=int(raw.get("parse_errors") or 0),
                used_cpp=bool(raw.get("used_cpp") or False),
                is_chunked=False,
                import_details=import_details,
                import_lines=import_lines,
            )
        return size, lines, pf, digest
    except (KeyError, TypeError, ValueError):
        return None
