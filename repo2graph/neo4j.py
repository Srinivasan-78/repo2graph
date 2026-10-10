"""Push `graph.cypher` into Neo4j over its HTTP API (#401).

Standard library only, like every other network path here: the statements
`write_cypher` already wrote go to Neo4j's transactional endpoint
(`POST {uri}/db/{database}/tx/commit`) in bounded batches. Memgraph serves
the same endpoint shape.

- **Opt-in, and it says where before it sends.** `build` makes no network
  call unless `--neo4j-uri` is given, and this prints the host and database
  to stderr before the first byte.
- **The password never reaches argv.** `NEO4J_PASSWORD` (and `NEO4J_USER`,
  default `neo4j`) come from the environment; argv is visible to every user
  on the machine through `ps`.
- **Idempotent, so a retry is safe.** Every statement is a `MERGE`, behind a
  uniqueness constraint, so pushing the same graph twice changes nothing.
- **A partial push says so.** Batches commit one at a time; a failure names
  how many statements were applied, and `graph.cypher` stays on disk whole.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .events import diagnostic

BATCH_STATEMENTS = 500
TIMEOUT = 60


class PushError(RuntimeError):
    """A push that stopped part-way; the message says how far it got."""


def read_statements(path: str | Path) -> list[str]:
    """The statements of a `graph.cypher` file, one per line, without the `;`."""
    out = []
    for line in Path(path).read_text(encoding="utf8").split("\n"):
        line = line.strip()
        if line:
            out.append(line[:-1] if line.endswith(";") else line)
    return out


def endpoint(uri: str, database: str) -> str:
    parts = urllib.parse.urlsplit(uri.rstrip("/"))
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(
            f"--neo4j-uri must be an http(s) URL such as http://localhost:7474, got {uri!r}"
        )
    if parts.username or parts.password:
        raise ValueError(
            "--neo4j-uri must not carry credentials; set NEO4J_USER and NEO4J_PASSWORD"
        )
    db = urllib.parse.quote(database, safe="")
    return f"{parts.scheme}://{parts.netloc}{parts.path}/db/{db}/tx/commit"


def _headers(env: Any) -> dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    password = env.get("NEO4J_PASSWORD")
    if password:
        user = env.get("NEO4J_USER") or "neo4j"
        token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    return headers


def _post(url: str, headers: dict[str, str], statements: list[str]) -> None:
    from .answer import _OPENER

    body = json.dumps({"statements": [{"statement": s} for s in statements]}).encode("utf8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with _OPENER.open(req, timeout=TIMEOUT) as resp:
        reply = json.loads(resp.read(8 << 20) or b"{}")
    errors = reply.get("errors") if isinstance(reply, dict) else None
    if errors:
        first = errors[0] if isinstance(errors[0], dict) else {}
        raise PushError(f"{first.get('code', 'error')}: {first.get('message', errors[0])}")


def push(
    cypher_path: str | Path,
    uri: str,
    database: str = "neo4j",
    *,
    env: Any = None,
    batch: int = BATCH_STATEMENTS,
) -> int:
    """Send every statement in `cypher_path`; return how many were applied.

    Raises:
        ValueError: A malformed URI.
        PushError: The server refused, or the connection failed, part-way.
    """
    env = os.environ if env is None else env
    url = endpoint(uri, database)
    statements = read_statements(cypher_path)
    host = urllib.parse.urlsplit(url).hostname or "?"
    diagnostic(
        f"repo2graph: pushing {len(statements)} Cypher statements to neo4j at {host} "
        f"(database {database})"
    )
    headers = _headers(env)
    # The constraint is schema; Neo4j will not run it in a transaction that
    # also writes data, so it goes alone.
    batches = [statements[:1]] + [
        statements[i : i + batch] for i in range(1, len(statements), max(1, batch))
    ]
    applied = 0
    for chunk in batches:
        if not chunk:
            continue
        try:
            _post(url, headers, chunk)
        except (PushError, urllib.error.URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", None) or exc
            if isinstance(exc, urllib.error.HTTPError):
                reason = f"HTTP {exc.code}"
            raise PushError(
                f"neo4j push stopped after {applied} of {len(statements)} statements: {reason}. "
                f"{Path(cypher_path).name} is complete on disk; pushing again is safe."
            ) from None
        applied += len(chunk)
    return applied
