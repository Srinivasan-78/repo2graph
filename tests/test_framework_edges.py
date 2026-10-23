"""`build --framework-edges`: ROUTES_TO / INJECTS / MODELS.

Three groups, in the order they matter:

1. the invariants that make an opt-in feature safe to ship -- a default build
   is byte-identical, and a flagged incremental build equals a flagged full one;
2. what the capture finds, per framework;
3. what it deliberately does **not** find, pinned so that a later change which
   starts finding call-registered routes has to come here and say so.
"""

from __future__ import annotations

import hashlib
import json
import shutil

import pytest

from repo2graph.cli import main
from repo2graph.export import path as artifact_path

# Artifacts that describe the graph. `manifest.json` carries a fresh `build_id`
# per build and `stats.json` legitimately records *why* a parse cache was
# invalidated, so neither belongs in a byte-identity comparison across runs.
GRAPH_ARTIFACTS = ("nodes.jsonl", "edges.jsonl", "chunks.jsonl")

FASTAPI = """\
from fastapi import Depends, FastAPI

from app.deps import current_user, get_db

app = FastAPI()


@app.get("/users/{uid}")
async def read_user(uid: int, db=Depends(get_db)):
    return db


@app.post("/users")
async def create_user(db=Depends(get_db), who=Depends(current_user)):
    return who
"""

DEPS = """\
def get_db():
    return {"conn": 1}


def current_user():
    return "alice"
"""

FLASK = """\
from flask import Flask

app = Flask(__name__)


@app.route("/legacy", methods=["POST", "PUT"])
def legacy_handler():
    return "ok"


@app.route("/health")
def health():
    return "ok"
"""

DJANGO = """\
from django.db import models


class Author(models.Model):
    name = models.CharField(max_length=100)


class Book(models.Model):
    author = models.ForeignKey("Author", on_delete=models.CASCADE)
    editor = models.ForeignKey(Author, on_delete=models.CASCADE)
    parent = models.ForeignKey("self", on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
"""

NEST = """\
import { Controller, Get, Post, UseGuards } from '@nestjs/common';

@Controller('users')
export class UsersController {
  @UseGuards(AuthGuard)
  @Get(':id')
  findOne(): string { return 'one'; }

  @Post()
  create(): string { return 'made'; }
}
"""

# Routing this pipeline cannot see, kept beside the routing it can so that the
# "deliberately not covered" tests are about real code rather than a strawman.
EXPRESS = """\
const express = require('express');
const app = express();

function listOrders(req, res) { res.send('orders'); }

app.get('/orders', listOrders);
app.post('/orders', (req, res) => res.send('made'));

module.exports = app;
"""

FILES = {
    "app/api.py": FASTAPI,
    "app/deps.py": DEPS,
    "app/legacy.py": FLASK,
    "blog/models.py": DJANGO,
    "web/src/users.controller.ts": NEST,
    "web/src/orders.js": EXPRESS,
}


def write_repo(root):
    for rel, text in FILES.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def build(root, out, *flags):
    """Build `root` into `out`, which must sit outside `root`.

    Outside deliberately: an output directory inside the scanned tree is itself
    discovered and counted in `skipped_gitignore`, which made two otherwise
    identical builds differ on that one stat.
    """
    assert out.resolve() not in (root.resolve(), *root.resolve().parents)
    shutil.rmtree(out, ignore_errors=True)
    rc = main(["build", str(root), "-o", str(out), "--formats", "jsonl,overview", *flags])
    assert rc in (0, None)
    return out


def records(out, name):
    text = artifact_path(out, name).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def digest(out):
    return {
        n: hashlib.sha256(artifact_path(out, n).read_bytes()).hexdigest() for n in GRAPH_ARTIFACTS
    }


def edges_of(out, etype):
    return [e for e in records(out, "edges.jsonl") if e["type"] == etype]


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    return write_repo(tmp_path_factory.mktemp("fwrepo") / "src")


@pytest.fixture(scope="module")
def plain(repo, tmp_path_factory):
    return build(repo, tmp_path_factory.mktemp("fwout") / "plain")


@pytest.fixture(scope="module")
def flagged(repo, tmp_path_factory):
    return build(repo, tmp_path_factory.mktemp("fwout") / "flagged", "--framework-edges")


# --------------------------------------------------------------------------
# 1. Opt-in invariants
# --------------------------------------------------------------------------


def test_a_default_build_is_unchanged_by_the_feature_existing(plain):
    """No framework edge, no route node, no framework stat without the flag."""
    assert edges_of(plain, "ROUTES_TO") == []
    assert edges_of(plain, "INJECTS") == []
    assert edges_of(plain, "MODELS") == []
    assert [n for n in records(plain, "nodes.jsonl") if n["type"] == "route"] == []
    stats = json.loads(artifact_path(plain, "stats.json").read_text(encoding="utf-8"))
    assert [k for k in stats if "framework" in k] == []


def test_two_default_builds_are_byte_identical(repo, tmp_path):
    a = build(repo, tmp_path / "a")
    b = build(repo, tmp_path / "b")
    assert digest(a) == digest(b)


def test_the_manifest_documents_the_types_only_when_they_are_present(plain, flagged):
    def manifest(out):
        return json.loads(artifact_path(out, "manifest.json").read_text(encoding="utf-8"))

    off, on = manifest(plain), manifest(flagged)
    for etype in ("ROUTES_TO", "INJECTS", "MODELS"):
        assert etype not in off["edge_types"]
        assert etype in on["edge_types"]
    assert "route" not in off["node_types"]
    assert "route" in on["node_types"]


def test_an_incremental_flagged_build_equals_a_full_one(repo, flagged, tmp_path):
    """The acceptance test for any capture that feeds the parse cache."""
    inc = tmp_path / "inc"
    shutil.copytree(flagged, inc)
    rc = main(
        [
            "build",
            str(repo),
            "-o",
            str(inc),
            "--formats",
            "jsonl,overview",
            "--framework-edges",
            "--incremental",
        ]
    )
    assert rc in (0, None)
    assert digest(inc) == digest(flagged)


def test_turning_the_flag_on_invalidates_a_cache_written_without_it(repo, plain, flagged, tmp_path):
    """A cached parse holds no framework facts, so it must not be reused.

    `parse_cache_identity` records `framework_edges`, and this is what proves
    the recording is actually consulted: the graph has to come out identical to
    a full flagged build, and `stats.json` has to say why the cache was
    dropped.
    """
    mixed = tmp_path / "mixed"
    shutil.copytree(plain, mixed)
    rc = main(
        [
            "build",
            str(repo),
            "-o",
            str(mixed),
            "--formats",
            "jsonl,overview",
            "--framework-edges",
            "--incremental",
        ]
    )
    assert rc in (0, None)
    assert digest(mixed) == digest(flagged)
    stats = json.loads(artifact_path(mixed, "stats.json").read_text(encoding="utf-8"))
    assert "framework_edges" in stats.get("parse_cache_invalidated", "")


# --------------------------------------------------------------------------
# 2. What it finds
# --------------------------------------------------------------------------


def route_map(out):
    """`{route node name: {handler qualnames}}`."""
    nodes = {n["id"]: n for n in records(out, "nodes.jsonl")}
    out_map: dict[str, set[str]] = {}
    for e in edges_of(out, "ROUTES_TO"):
        out_map.setdefault(nodes[e["src"]]["name"], set()).add(e["dst"].split("::", 1)[1])
    return out_map


def test_fastapi_routes_reach_their_handlers(flagged):
    routes = route_map(flagged)
    assert routes["GET /users/{uid}"] == {"read_user"}
    assert "create_user" in routes["POST /users"]


def test_a_flask_route_with_two_methods_is_two_routes(flagged):
    """`methods=["POST", "PUT"]` is two endpoints, not one with a list."""
    routes = route_map(flagged)
    assert routes["POST /legacy"] == {"legacy_handler"}
    assert routes["PUT /legacy"] == {"legacy_handler"}
    # `@app.route` with no `methods` is GET, which is Flask's own default.
    assert routes["GET /health"] == {"health"}


def test_a_nest_controller_prefix_is_joined_onto_its_methods(flagged):
    """The regression that made every NestJS route come out unprefixed.

    `@Controller('users')` is separated from its `class_declaration` by the
    `export` keyword token, so walking back one sibling found the methods'
    decorators but never the controller's own mount decorator.
    """
    routes = route_map(flagged)
    assert routes["GET /users/:id"] == {"UsersController.findOne"}
    assert "UsersController.create" in routes["POST /users"]
    assert "GET /:id" not in routes, "the controller prefix was not applied"


def test_duplicated_controller_class_name_disambiguates_prefixes(tmp_path):
    """Duplicated controller class name in one file resolves each prefix correctly."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "users.ts").write_text(
        "@Controller('alpha')\n"
        "export class UserController {\n"
        "  @Get(':id')\n"
        "  findOne(id: string) { return id; }\n"
        "}\n\n"
        "@Controller('beta')\n"
        "export class UserController {\n"
        "  @Get(':slug')\n"
        "  findSlug(slug: string) { return slug; }\n"
        "}\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    build(repo, out, "--framework-edges")
    routes = route_map(out)
    assert routes["GET /alpha/:id"] == {"UserController.findOne"}
    assert routes["GET /beta/:slug"] == {"UserController.findSlug"}


def test_a_route_node_is_shared_by_every_handler_that_declares_it(flagged):
    """Two handlers on one path is real; both edges are kept."""
    assert route_map(flagged)["POST /users"] == {"create_user", "UsersController.create"}


def test_a_stacked_guard_decorator_does_not_hide_the_route(flagged):
    """`@UseGuards(...)` sits above `@Get(':id')`, so only a walk finds it."""
    assert "GET /users/:id" in route_map(flagged)


def test_routes_are_exact_rather_than_resolved(flagged):
    for e in edges_of(flagged, "ROUTES_TO"):
        assert e["confidence"] == 1.0
        assert e["resolution_kind"] == "decorator"
        assert e["evidence"], "a route edge must cite the decorator line"


def test_fastapi_depends_becomes_an_injects_edge(flagged):
    got = {
        (e["src"].split("::", 1)[1], e["dst"].split("::", 1)[1])
        for e in edges_of(flagged, "INJECTS")
    }
    assert got == {
        ("read_user", "get_db"),
        ("create_user", "get_db"),
        ("create_user", "current_user"),
    }


def test_orm_relations_become_models_edges(flagged):
    by_target = {e["dst"].split("::", 1)[1]: e for e in edges_of(flagged, "MODELS")}
    assert set(by_target) == {"Author", "Book"}
    # `author` (string) and `editor` (identifier) both name Author. Edges are
    # identified by their endpoints, so that is one edge carrying both fields
    # rather than one of the two silently winning.
    author = by_target["Author"]
    assert author["count"] == 2
    assert author["fields"] == ["author", "editor"]
    assert author["relation"] == "ForeignKey"
    # `"self"` is Django's self-reference, kept as an honest self-edge.
    assert by_target["Book"]["resolution_kind"] == "self_reference"


def test_a_plain_orm_field_is_not_a_relation(flagged):
    """`CharField` names a column type, not another model."""
    assert all(e["relation"] != "CharField" for e in edges_of(flagged, "MODELS"))
    assert all(
        "title" not in (e.get("fields") or [e["field"]]) for e in edges_of(flagged, "MODELS")
    )


def test_stats_count_every_edge_kind(flagged):
    stats = json.loads(artifact_path(flagged, "stats.json").read_text(encoding="utf-8"))
    assert stats["framework_edges_routes"] == len(edges_of(flagged, "ROUTES_TO"))
    assert stats["framework_edges_injects"] == len(edges_of(flagged, "INJECTS"))
    assert stats["framework_edges_models"] == len(edges_of(flagged, "MODELS"))
    assert stats["framework_edges_unresolved"] == 0


# --------------------------------------------------------------------------
# 3. What it deliberately does not find
# --------------------------------------------------------------------------


def test_a_call_registered_route_gets_no_edge(flagged):
    """Express `app.get('/orders', listOrders)` is documented as uncovered.

    Pinned rather than left implicit: resolving call-registered routes is a
    real follow-up, and whoever does it should have to change this test and
    the paragraph in docs/architecture.md that promises the limit.
    """
    routes = route_map(flagged)
    assert not any(name.endswith("/orders") for name in routes), (
        "an Express route was resolved; update the docs and this test together"
    )
    # The handler is still an ordinary symbol -- only the route edge is absent.
    assert any(n["id"].endswith("::listOrders") for n in records(flagged, "nodes.jsonl"))


def test_an_unbound_dependency_is_counted_not_guessed(repo, tmp_path):
    """`Depends` on a name this repository does not define resolves to nothing."""
    extra = repo / "app" / "orphan.py"
    extra.write_text(
        "from fastapi import Depends\n\n\n"
        "@app.get('/x')\n"
        "def handler(dep=Depends(nowhere_at_all)):\n"
        "    return dep\n",
        encoding="utf-8",
    )
    try:
        out = build(repo, tmp_path / "orphan", "--framework-edges")
        stats = json.loads(artifact_path(out, "stats.json").read_text(encoding="utf-8"))
        assert stats["framework_edges_unresolved"] >= 1
        assert all("nowhere_at_all" not in e["dst"] for e in edges_of(out, "INJECTS"))
    finally:
        extra.unlink()


def test_a_language_outside_the_capture_set_gets_no_framework_edges(tmp_path):
    """Java's `@GetMapping` is documented as not covered yet."""
    root = tmp_path / "java"
    (root / "src").mkdir(parents=True)
    (root / "src" / "Api.java").write_text(
        'class Api {\n  @GetMapping("/ping")\n  public String ping() { return "pong"; }\n}\n',
        encoding="utf-8",
    )
    out = build(root, tmp_path / "javaout", "--framework-edges")
    assert edges_of(out, "ROUTES_TO") == []


def test_mock_patch_is_not_read_as_a_route(tmp_path):
    """unittest.mock.patch and bare @patch collision with PATCH method is rejected."""
    root = tmp_path / "mockrepo"
    root.mkdir()
    (root / "test_things.py").write_text(
        "from unittest import mock\n"
        "from unittest.mock import patch\n\n"
        "@patch('myapp.services.get_user')\n"
        "def test_user_lookup(mocked):\n"
        "    pass\n\n"
        "@mock.patch('myapp.db.connect')\n"
        "def test_connect(mocked):\n"
        "    pass\n",
        encoding="utf-8",
    )
    out = build(root, tmp_path / "mockout", "--framework-edges")
    assert edges_of(out, "ROUTES_TO") == []
    assert route_map(out) == {}
