"""Opaque continuation cursors for paged MCP tool results (#389).

A cursor is self-contained: base64url JSON naming the tool, a digest of the
arguments that fix the result order, the position of the next page, the
identity of the index that answered, and an expiry -- signed with an HMAC
under a key drawn once per server process.

Self-contained rather than a handle into server-side state because there is
then no state to bound: a cursor costs the server nothing until it comes back,
no client can grow a table by asking for page one of a million queries, and a
cursor nobody returns simply expires. The HMAC is what makes that safe -- a
client cannot forge a position for a different query or index, and anything
it edits is refused rather than half-honoured. The per-process key means a
cursor does not survive a server restart, which a restart's possibly rebuilt
index would make suspect anyway.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from ..query import Index

# The plain-text line a paged result ends with while another page remains.
# Clients that only read text continue by passing <token> back as `cursor`.
NEXT_CURSOR_PREFIX = "next_cursor: "

# Seconds a cursor stays valid after it is issued.
CURSOR_TTL_S = 600

# Longest cursor accepted; anything issued here is well under it.
MAX_CURSOR_CHARS = 512

# Tokens kept free in every page for the `next_cursor` line, so a page plus its
# continuation still fits the ceiling the unpaged answer is held to.
CURSOR_RESERVE_TOKENS = 96

_KEY = secrets.token_bytes(32)
_SIG_BYTES = 16


class PagedText(str):
    """A tool result that carries the cursor for the page after it, if any.

    Behaves as a plain string; the stdio transport reads `next_cursor` to set
    the result's `_meta.nextCursor`.
    """

    next_cursor: str | None = None


class CursorError(Exception):
    """A cursor that cannot be honoured; the message is shown to the caller."""


def index_identity(index: Index) -> str:
    """A short fingerprint of the index build that answered."""
    try:
        build_id = index.manifest.get("build_id")
    except Exception:  # noqa: BLE001 - an unreadable manifest falls back to mtime
        build_id = None
    if not build_id:
        from .indexes import _index_mtime

        build_id = f"mtime:{_index_mtime(index.dir)!r}"
    return hashlib.sha256(str(build_id).encode("utf8")).hexdigest()[:16]


def args_digest(tool: str, ordering_args: dict[str, Any]) -> str:
    """Digest of the normalized arguments that determine a result's order."""
    body = json.dumps(ordering_args, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(f"{tool}\x00{body}".encode("utf8", "surrogatepass")).hexdigest()[:16]


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def encode_cursor(tool: str, digest: str, offset: int, identity: str) -> str:
    """Sign and encode the position of the next page."""
    payload = {
        "t": tool,
        "a": digest,
        "o": int(offset),
        "i": identity,
        "e": int(time.time()) + CURSOR_TTL_S,
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf8")
    sig = hmac.new(_KEY, body, hashlib.sha256).digest()[:_SIG_BYTES]
    return f"{_b64(body)}.{_b64(sig)}"


def decode_cursor(cursor: Any, tool: str, digest: str, identity: str) -> int:
    """The page offset a cursor names, or CursorError saying why it is refused.

    An empty cursor is the opt-in for paging and names the first page.
    """
    if cursor is None or cursor == "":
        return 0
    restart = f're-run {tool} without `cursor` (or with cursor="") to start over.'
    if not isinstance(cursor, str) or len(cursor) > MAX_CURSOR_CHARS or "." not in cursor:
        raise CursorError(f"invalid cursor: not a cursor this server issued; {restart}")
    body_b64, _, sig_b64 = cursor.partition(".")
    try:
        body, sig = _unb64(body_b64), _unb64(sig_b64)
    except (binascii.Error, ValueError):
        raise CursorError(f"invalid cursor: not a cursor this server issued; {restart}") from None
    expected = hmac.new(_KEY, body, hashlib.sha256).digest()[:_SIG_BYTES]
    if not hmac.compare_digest(sig, expected):
        raise CursorError(
            "invalid cursor: it was altered, or issued by another server process "
            f"(cursors do not survive a restart); {restart}"
        )
    try:
        payload = json.loads(body)
        offset = int(payload["o"])
        expires = int(payload["e"])
    except (ValueError, TypeError, KeyError):
        raise CursorError(f"invalid cursor: unreadable payload; {restart}") from None
    if payload.get("t") != tool:
        raise CursorError(
            f"invalid cursor: it was issued by {payload.get('t')!r}, not {tool}; {restart}"
        )
    if payload.get("i") != identity:
        raise CursorError(
            f"index was rebuilt; re-run the query without cursor. The cursor pages a "
            f"result from an earlier build of the index, so its positions no longer "
            f"line up -- {restart}"
        )
    if payload.get("a") != digest:
        raise CursorError(
            "invalid cursor: it belongs to a different query -- keep the arguments "
            f"that fix the order the same across pages; {restart}"
        )
    if time.time() > expires:
        raise CursorError(f"cursor expired (cursors last {CURSOR_TTL_S} seconds); {restart}")
    if offset < 0:
        raise CursorError(f"invalid cursor: negative position; {restart}")
    return offset


def with_next_cursor(text: str, next_cursor: str | None) -> PagedText:
    """Append the continuation line (when there is one) and attach the cursor."""
    out = PagedText(f"{text}\n\n{NEXT_CURSOR_PREFIX}{next_cursor}" if next_cursor else text)
    out.next_cursor = next_cursor
    return out
