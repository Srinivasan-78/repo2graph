"""A bundled, self-contained demo repository and the five starter questions.

`repo2graph demo` materialises the fixture below into a scratch directory,
builds a real index over it with the same `build()` / `dump_all()` the `build`
subcommand uses, then answers five questions against it. It is the first-run
path: one command, no repository of your own, no API key, no network.

The fixture is held as source strings rather than shipped as package data on
purpose -- `[tool.setuptools.packages.find]` only picks up importable
packages, so a directory of `.py` fixtures would need a `package-data` entry
and would silently vanish from the wheel the first time that entry drifted.
Strings are in the module either way, under `uvx`, `pip`, Docker and a git
checkout alike.

Shape of the fixture, which is what makes all five starter questions
answerable on it: an HTTP route layer calls an auth guard and a service, the
service calls a billing helper and a store, and the store is the only thing
that touches SQL. That is one honest route-to-persistence path, one function
with two callers, one guard enforced in two places, and a test module that
imports the service.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# Keep every module's residue above chunks.py's 40-character `file_residual`
# floor. A file whose body is entirely `def`s and imports gets a node but no
# chunk, and a node with no chunk can be neither a seed nor a retrievable
# neighbour -- so each fixture module below carries a module-level constant or
# docstring. See AGENTS.md, "A file with little residue emits no file-level
# chunk".

_ROUTES_PY = '''\
"""HTTP route handlers for the orders API.

Every write route is guarded by `require_token` before it touches the
service layer; there is no other entry point into `OrderService`.
"""

from .auth import require_token
from .billing import RefundDeclined
from .service import OrderService

# The public surface of this service. Anything not listed here is internal
# and may change without a version bump.
ROUTE_TABLE = {
    "POST /orders": "create_order",
    "GET /orders/{order_id}": "get_order",
    "POST /orders/{order_id}/refund": "refund_order",
}

service = OrderService()


def create_order(request):
    """POST /orders -- place a new order for the authenticated customer."""
    actor = require_token(request)
    payload = request.json()
    order = service.place_order(actor, payload["sku"], payload["quantity"])
    return {"status": 201, "order": order}


def get_order(request, order_id):
    """GET /orders/{order_id} -- read one order back."""
    actor = require_token(request)
    order = service.load_order(actor, order_id)
    if order is None:
        return {"status": 404, "error": "no such order"}
    return {"status": 200, "order": order}


def refund_order(request, order_id):
    """POST /orders/{order_id}/refund -- reverse a settled order."""
    actor = require_token(request)
    try:
        order = service.refund_order(actor, order_id)
    except RefundDeclined as exc:
        return {"status": 409, "error": str(exc)}
    return {"status": 200, "order": order}
'''

_AUTH_PY = '''\
"""Authentication and authorisation for the orders API.

This module is where authentication is enforced: `require_token` is the
single guard every route calls before any handler body runs.
"""

# Tokens are opaque to this service; the issuer signs them and we only check
# the prefix and the scope claim that rides along with it.
TOKEN_PREFIX = "tok_"
REQUIRED_SCOPE = "orders:write"


class AuthError(Exception):
    """Raised when a request carries no usable credential."""


def parse_token(header_value):
    """Pull the bearer token out of an Authorization header value."""
    if not header_value or not header_value.startswith("Bearer "):
        raise AuthError("missing bearer token")
    token = header_value[len("Bearer ") :].strip()
    if not token.startswith(TOKEN_PREFIX):
        raise AuthError("malformed token")
    return token


def require_token(request):
    """Authenticate `request` and return the acting principal.

    Every route handler calls this first. It raises `AuthError` rather than
    returning a sentinel so a handler cannot forget to check the result.
    """
    token = parse_token(request.headers.get("Authorization"))
    actor = lookup_principal(token)
    if REQUIRED_SCOPE not in actor["scopes"]:
        raise AuthError("token lacks the orders:write scope")
    return actor


def lookup_principal(token):
    """Resolve a token to the principal record it was issued for."""
    return {"id": token[len(TOKEN_PREFIX) :], "scopes": [REQUIRED_SCOPE]}
'''

_SERVICE_PY = '''\
"""Order business rules: the layer between the routes and the store.

Nothing in here talks to the database directly -- every read and write goes
through `OrderStore`, so persistence can be swapped without touching these
rules.
"""

from .billing import RefundDeclined, charge, refund
from .store import OrderStore

# Orders above this total need a human to approve them before they settle.
REVIEW_THRESHOLD_CENTS = 50_000
UNIT_PRICE_CENTS = 1_250


class OrderService:
    """Applies the order rules and delegates persistence to `OrderStore`."""

    def __init__(self, store=None):
        self.store = store or OrderStore()

    def place_order(self, actor, sku, quantity):
        """Validate, charge, and persist one new order."""
        total = price_order(sku, quantity)
        receipt = charge(actor, total)
        record = {
            "customer": actor["id"],
            "sku": sku,
            "quantity": quantity,
            "total_cents": total,
            "receipt": receipt,
            "state": "review" if total >= REVIEW_THRESHOLD_CENTS else "settled",
        }
        return self.store.insert(record)

    def load_order(self, actor, order_id):
        """Read one order back, scoped to the acting customer."""
        order = self.store.get_by_id(order_id)
        if order is None or order["customer"] != actor["id"]:
            return None
        return order

    def refund_order(self, actor, order_id):
        """Reverse a settled order and write the new state back."""
        order = self.load_order(actor, order_id)
        if order is None:
            raise RefundDeclined("no such order for this customer")
        refund(actor, order["total_cents"])
        order["state"] = "refunded"
        return self.store.update(order)


def price_order(sku, quantity):
    """Total an order in cents. Called by the service and by the tests."""
    if quantity < 1:
        raise ValueError("quantity must be at least 1")
    return UNIT_PRICE_CENTS * quantity
'''

_STORE_PY = '''\
"""Persistence for orders. The only module in the service that writes SQL.

Every statement is parameterised; the table name is a module constant rather
than an argument so no caller can reach the schema.
"""

import sqlite3

TABLE = "orders"
CONNECT_STRING = "file:orders.db?mode=rwc"


class OrderStore:
    """A thin, synchronous SQLite-backed store for order records."""

    def __init__(self, connection=None):
        self.connection = connection or sqlite3.connect(CONNECT_STRING, uri=True)

    def insert(self, record):
        """Write one new order row and return it with its assigned id."""
        cursor = self.connection.execute(
            "INSERT INTO orders (customer, sku, quantity, total_cents, state)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                record["customer"],
                record["sku"],
                record["quantity"],
                record["total_cents"],
                record["state"],
            ),
        )
        self.connection.commit()
        return dict(record, id=cursor.lastrowid)

    def get_by_id(self, order_id):
        """Read one order row back by id, or None when it is not there."""
        row = self.connection.execute(
            "SELECT id, customer, sku, quantity, total_cents, state FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(zip(("id", "customer", "sku", "quantity", "total_cents", "state"), row))

    def update(self, record):
        """Write a changed order row back and return the stored form."""
        self.connection.execute(
            "UPDATE orders SET state = ? WHERE id = ?",
            (record["state"], record["id"]),
        )
        self.connection.commit()
        return record
'''

_BILLING_PY = '''\
"""Payment capture and reversal against the upstream billing provider."""

# The provider settles in cents and rejects anything it cannot round-trip.
PROVIDER = "acme-payments"
MAX_CHARGE_CENTS = 1_000_000


class RefundDeclined(Exception):
    """Raised when the provider refuses to reverse a settled charge."""


def charge(actor, amount_cents):
    """Capture `amount_cents` against the acting principal's account."""
    if amount_cents > MAX_CHARGE_CENTS:
        raise ValueError("charge exceeds the provider ceiling")
    return {"provider": PROVIDER, "customer": actor["id"], "amount": amount_cents}


def refund(actor, amount_cents):
    """Reverse a previously captured charge."""
    if amount_cents <= 0:
        raise RefundDeclined("nothing to refund")
    return {"provider": PROVIDER, "customer": actor["id"], "amount": -amount_cents}
'''

_TESTS_PY = '''\
"""Tests covering the order service and its pricing rules."""

from app.auth import AuthError, require_token
from app.service import OrderService, price_order


class FakeStore:
    """An in-memory stand-in for OrderStore used by every test below."""

    def __init__(self):
        self.rows = {}

    def insert(self, record):
        record = dict(record, id=len(self.rows) + 1)
        self.rows[record["id"]] = record
        return record

    def get_by_id(self, order_id):
        return self.rows.get(order_id)

    def update(self, record):
        self.rows[record["id"]] = record
        return record


ACTOR = {"id": "cust_1", "scopes": ["orders:write"]}


def test_price_order_multiplies_by_quantity():
    assert price_order("widget", 4) == 5000


def test_place_order_persists_through_the_store():
    service = OrderService(store=FakeStore())
    order = service.place_order(ACTOR, "widget", 2)
    assert order["id"] == 1
    assert order["state"] == "settled"


def test_load_order_is_scoped_to_the_customer():
    service = OrderService(store=FakeStore())
    service.place_order(ACTOR, "widget", 1)
    assert service.load_order({"id": "someone_else"}, 1) is None


def test_require_token_rejects_a_missing_header():
    class Request:
        headers = {}

    try:
        require_token(Request())
    except AuthError:
        return
    raise AssertionError("expected AuthError")
'''

_CLIENT_JS = """\
// Browser client for the orders API. Sends the bearer token the server's
// require_token guard expects on every request.

const API_ROOT = "/orders";
const TOKEN_HEADER = "Authorization";

export function authHeaders(token) {
  return { [TOKEN_HEADER]: `Bearer ${token}` };
}

export async function createOrder(token, sku, quantity) {
  const response = await fetch(API_ROOT, {
    method: "POST",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ sku, quantity }),
  });
  return response.json();
}

export async function getOrder(token, orderId) {
  const response = await fetch(`${API_ROOT}/${orderId}`, {
    headers: authHeaders(token),
  });
  return response.json();
}
"""

_README_MD = """\
# demo-orders

A deliberately small orders service, bundled with repo2graph so `repo2graph
demo` has something real to index on a machine that has just installed it.

    web/client.js  ->  app/routes.py  ->  app/service.py  ->  app/store.py
                            |                   |
                            v                   v
                       app/auth.py         app/billing.py

- `app/routes.py` is the HTTP surface; every write route calls the guard.
- `app/auth.py` holds the bearer-token guard.
- `app/service.py` holds the order rules and owns the store.
- `app/store.py` is the only module that writes SQL.
- `tests/test_orders.py` covers the service and the pricing rule.
"""

# relative path -> file body. Ordinary dict ordering is the write order.
DEMO_FILES: dict[str, str] = {
    "README.md": _README_MD,
    "app/__init__.py": (
        '"""The demo orders service: routes, auth, rules, billing, store."""\n\n'
        'SERVICE_NAME = "demo-orders"\n'
        'VERSION = "1.0.0"\n'
    ),
    "app/routes.py": _ROUTES_PY,
    "app/auth.py": _AUTH_PY,
    "app/service.py": _SERVICE_PY,
    "app/store.py": _STORE_PY,
    "app/billing.py": _BILLING_PY,
    "tests/test_orders.py": _TESTS_PY,
    "web/client.js": _CLIENT_JS,
}


@dataclass(frozen=True)
class StarterQuestion:
    """One of the five questions a new user should try first.

    `template` is the copy-paste form for the reader's own repository, with
    the part they substitute in angle brackets; `demo` is the same question
    already concretised against the bundled fixture, which is what
    `repo2graph demo` actually runs; `shows` is the one line explaining what
    the answer demonstrates about the graph.
    """

    template: str
    demo: str
    shows: str


# The single source of truth for the starter prompts. docs/quickstart.md,
# README.md and `repo2graph demo` all render this list, and
# tests/test_doc_consistency.py fails if a docs copy drifts from it.
STARTER_QUESTIONS: tuple[StarterQuestion, ...] = (
    StarterQuestion(
        template="Where is authentication enforced?",
        demo="Where is authentication enforced?",
        shows="the guard itself, plus every route that calls it",
    ),
    StarterQuestion(
        template="What calls <function>?",
        demo="What calls place_order?",
        shows="CALLS edges in, so callers come back even when the name is shadowed",
    ),
    StarterQuestion(
        template="What tests cover <module>?",
        demo="What tests cover the order service?",
        shows="IMPORTS edges from the test module back to the code under test",
    ),
    StarterQuestion(
        template="What would be affected by changing <api>?",
        demo="What would be affected by changing OrderStore.insert?",
        shows="the blast radius: direct callers and what they are called from",
    ),
    StarterQuestion(
        template="Trace <a request> from route to persistence.",
        demo="Trace an order request from route to persistence.",
        shows="a whole path across modules, each block cited to file and line",
    ),
)


def materialize(dest: Path) -> Path:
    """Write the bundled demo repository into `dest` and return it.

    `dest` is created if needed. Existing files with the same names are
    overwritten; nothing else in `dest` is touched, so pointing this at a
    directory that already holds an index re-materialises the sources without
    discarding the index.
    """
    dest = Path(dest)
    for rel, body in DEMO_FILES.items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        # newline="\n" so the fixture's bytes -- and therefore the graph built
        # over it -- are identical on Windows and POSIX.
        target.write_text(body, encoding="utf8", newline="\n")
    return dest


def build_demo_index(repo: Path, outdir: Path) -> dict[str, Any]:
    """Build an index over the materialised demo repo. Returns build stats."""
    from .chunks import iter_chunks
    from .export import dump_all
    from .graph import build
    from .parse import BuildConfig

    g = build(repo, config=BuildConfig(), jobs=1)
    written, n_chunks = dump_all(g, iter_chunks(g), outdir, {"jsonl", "overview", "html"}, 0)
    stats = dict(g.stats)
    stats["chunks"] = n_chunks
    stats["written"] = written
    return stats


def _brief(pack: dict[str, Any]) -> str:
    """A citation table plus the head of the first cited block.

    Never the pack's own markdown head: `pack_context` prepends the same repo
    map to every pack, so truncating the markdown to its first lines shows
    five identical maps and not one citation -- the opposite of what the demo
    is for. The blocks are what proves the answer is grounded, so those are
    what the demo prints.

    `split("\\n")` rather than `splitlines()`: chunk text is verbatim source,
    and a U+2028 in it is not a line break to anything that produced it.
    See AGENTS.md.
    """
    chunks = pack.get("chunks") or []
    if not chunks:
        return "      (no chunks matched -- the index is empty or the query hit nothing)"

    lines = [f"      {len(chunks)} cited blocks, {pack['used_chars']} chars:"]
    for c in chunks:
        start, end = c.get("start_line") or 1, c.get("end_line") or 1
        where = f"{c.get('path') or '?'}:{start}-{end}"
        name = c.get("qualname") or c.get("name") or ""
        lines.append(f"        {where:<28} {name:<30} {c.get('why') or ''}")

    first = chunks[0]
    body = (first.get("text") or "").split("\n")
    start, end = first.get("start_line") or 1, first.get("end_line") or 1
    lines.append("")
    lines.append(f"      [cite: {first.get('path') or '?'}:{start}-{end}]")
    for ln in body[:BRIEF_BODY_LINES]:
        lines.append(f"      | {ln}")
    if len(body) > BRIEF_BODY_LINES:
        lines.append(
            f"      | ... ({len(body) - BRIEF_BODY_LINES} more lines -- rerun with --full)"
        )
    return "\n".join(lines)


# Small enough that the whole demo stays readable in one terminal screen, and
# large enough that question 5 reaches all four modules on its path.
DEMO_K = 6
DEMO_HOPS = 1
DEMO_BUDGET_CHARS = 6000
BRIEF_BODY_LINES = 10


def run_demo(
    outdir: Path | str | None = None,
    *,
    keep: bool = False,
    brief: bool = True,
    emit: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Materialise, index and interrogate the bundled demo repository.

    Args:
        outdir: Where to put the demo repo. None uses a temp directory that
            is removed on the way out unless `keep` is set.
        keep: Leave the materialised repo and its index on disk, and print
            the follow-up commands that work against it.
        brief: Truncate each answer to `BRIEF_LINES` lines. False prints the
            whole cited pack for every question.
        emit: Where to write. The CLI passes its encoding-safe writer; the
            default `print` is for library callers and tests.

    Returns:
        A report dict: the repo path, the index path, the build stats, and
        one record per starter question with its cited paths.
    """
    from .query import Index

    if outdir is None:
        ephemeral = True
        root = Path(tempfile.mkdtemp(prefix="repo2graph-demo-"))
    else:
        ephemeral = False
        root = Path(outdir).expanduser()
        root.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {"repo": str(root), "index": str(root / ".r2g"), "questions": []}
    try:
        emit(f"1/3  Writing the bundled demo repository to {root}")
        materialize(root)
        emit(f"     {len(DEMO_FILES)} files: " + ", ".join(sorted(DEMO_FILES)))
        emit("")

        emit("2/3  Building the graph (no network, no API key, no config file)")
        index_dir = root / ".r2g"
        stats = build_demo_index(root, index_dir)
        report["stats"] = stats
        emit(
            "     {files} files parsed -> {nodes} nodes, {edges} edges, {chunks} chunks".format(
                files=stats.get("parsed", 0),
                nodes=stats.get("nodes", 0),
                edges=stats.get("edges", 0),
                chunks=stats.get("chunks", 0),
            )
        )
        emit("")

        emit("3/3  Five questions you can copy onto your own repository")
        idx = Index(index_dir)
        for n, q in enumerate(STARTER_QUESTIONS, 1):
            pack = idx.pack_context(
                q.demo,
                k=DEMO_K,
                hops=DEMO_HOPS,
                budget_chars=DEMO_BUDGET_CHARS,
                exclude_secrets=True,
            )
            cited = sorted({c.get("path", "") for c in pack["chunks"] if c.get("path")})
            report["questions"].append(
                {
                    "template": q.template,
                    "query": q.demo,
                    "shows": q.shows,
                    "cited_paths": cited,
                    "used_chars": pack["used_chars"],
                }
            )
            emit("")
            emit(f"  --- {n}/5  {q.demo}")
            emit(f"      ({q.shows})")
            emit(f'      $ repo2graph rag "{q.demo}" -o {index_dir}')
            emit("")
            emit(_brief(pack) if brief else pack["markdown"])

        emit("")
        emit("-" * 72)
        if keep or not ephemeral:
            emit(f"Demo repo kept at {root}. Try it yourself:")
            emit(f"  repo2graph rag 'what calls charge?' -o {index_dir}")
            emit(
                f"  repo2graph explain node 'sym:app/service.py::OrderService.place_order' -o {index_dir}"
            )
            emit(f"  open {index_dir / 'human' / 'graph.html'}")
        else:
            emit("Now run it on your own code:")
            emit("  repo2graph build . -o .r2g")
            emit("  repo2graph rag 'where is authentication enforced?' -o .r2g")
            emit("  repo2graph doctor .")
        return report
    finally:
        if ephemeral and not keep:
            shutil.rmtree(root, ignore_errors=True)
