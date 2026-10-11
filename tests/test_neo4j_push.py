"""`build --neo4j-uri` pushes graph.cypher over Neo4j's HTTP API (#401)."""

from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from repo2graph.cli import main
from repo2graph.export import path as artifact_path
from repo2graph.neo4j import PushError, endpoint, push, read_statements


class FakeNeo4j:
    """Records each transactional request; can be told to fail the Nth."""

    def __init__(self, fail_on: int | None = None):
        self.requests: list[dict] = []
        self.fail_on = fail_on
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - http.server API
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(
                    {"path": self.path, "auth": self.headers.get("Authorization"), "body": body}
                )
                errors = []
                if outer.fail_on is not None and len(outer.requests) == outer.fail_on:
                    errors = [{"code": "Neo.ClientError.Statement.SyntaxError", "message": "bad"}]
                payload = json.dumps({"results": [], "errors": errors}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.uri = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def statements(self) -> list[str]:
        return [s["statement"] for r in self.requests for s in r["body"]["statements"]]


@pytest.fixture
def built(tmp_path, mini_repo, capsys):
    out = tmp_path / "idx"
    main(["build", str(mini_repo), "-o", str(out), "--formats", "jsonl,cypher"])
    capsys.readouterr()
    return out


def test_every_statement_arrives_in_order_with_the_constraint_alone(built, capsys):
    neo = FakeNeo4j()
    cypher = artifact_path(built, "graph.cypher")
    applied = push(cypher, neo.uri, "graphs", env={"NEO4J_PASSWORD": "pw"}, batch=7)
    assert neo.statements() == read_statements(cypher)
    assert applied == len(read_statements(cypher))
    first = neo.requests[0]["body"]["statements"]
    assert len(first) == 1 and first[0]["statement"].startswith("CREATE CONSTRAINT")
    assert all(len(r["body"]["statements"]) <= 7 for r in neo.requests)
    assert {r["path"] for r in neo.requests} == {"/db/graphs/tx/commit"}
    assert neo.requests[0]["auth"] == "Basic " + base64.b64encode(b"neo4j:pw").decode()
    assert "pushing" in capsys.readouterr().err  # the destination is named first


def test_no_password_sends_no_authorization(built):
    neo = FakeNeo4j()
    push(artifact_path(built, "graph.cypher"), neo.uri, env={})
    assert {r["auth"] for r in neo.requests} == {None}


def test_a_refused_batch_says_how_far_it_got(built):
    neo = FakeNeo4j(fail_on=3)
    with pytest.raises(
        PushError, match=r"stopped after \d+ of \d+ statements.*pushing again is safe"
    ):
        push(artifact_path(built, "graph.cypher"), neo.uri, env={}, batch=5)


def test_a_refused_connection_is_a_clean_error_and_leaves_graph_cypher(mini_repo, tmp_path):
    out = tmp_path / "idx2"
    with pytest.raises(SystemExit, match="neo4j push stopped after 0"):
        main(
            [
                "build",
                str(mini_repo),
                "-o",
                str(out),
                "--formats",
                "jsonl,cypher",
                "--neo4j-uri",
                "http://127.0.0.1:9",
            ]
        )
    assert artifact_path(out, "graph.cypher").is_file()


def test_cli_push_reports_in_the_build_json(tmp_path, mini_repo, capsys):
    neo = FakeNeo4j()
    out = tmp_path / "idx"
    main(
        [
            "build",
            str(mini_repo),
            "-o",
            str(out),
            "--formats",
            "cypher,jsonl",
            "--neo4j-uri",
            neo.uri,
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert report["neo4j"]["statements"] == len(neo.statements()) > 1


@pytest.mark.parametrize(
    "uri, message",
    [
        ("bolt://localhost:7687", "http"),
        ("http://neo4j:secret@localhost:7474", "credentials"),
    ],
)
def test_bad_uris_are_refused(uri, message):
    with pytest.raises(ValueError, match=message):
        endpoint(uri, "neo4j")


def test_neo4j_uri_needs_the_cypher_format(tmp_path, mini_repo):
    with pytest.raises(SystemExit, match="add cypher"):
        main(
            [
                "build",
                str(mini_repo),
                "-o",
                str(tmp_path / "o"),
                "--formats",
                "jsonl",
                "--neo4j-uri",
                "http://x",
            ]
        )
