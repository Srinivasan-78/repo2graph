"""Audit records: complete enough to investigate with, redacted enough to keep.

The redaction tests carry most of the weight. An audit log is written to be
retained and shipped to a SIEM, so a secret that reaches one has been copied out
of a short-lived process into durable storage read by people who did not
previously have it -- a strictly worse outcome than not logging at all. The
tests below therefore check both directions: that credentials never survive, and
that ordinary arguments are *not* mangled, because a log that redacts everything
is as useless as one that redacts nothing.
"""

import io
import json
import sys

import pytest

from repo2graph.audit import AuditConfig, AuditLogger, sanitize_params, sanitize_value, timer


def logger(level="all", path=None):
    """An AuditLogger writing to a StringIO, plus that buffer."""
    stream = io.StringIO()
    return AuditLogger(AuditConfig(level=level, path=path), stream=stream), stream


def lines(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


# ------------------------------------------------------------- records ----


def test_a_successful_call_records_every_required_field():
    log, stream = logger()
    with timer() as t:
        pass
    log.record(
        "repo_search",
        {"query": "how does routing work", "k": 8},
        identity="user-42",
        outcome="success",
        duration_ms=t.ms,
        result_tokens=1200,
    )

    (record,) = lines(stream)
    assert set(record) == {
        "ts",
        "event",
        "tool",
        "params",
        "identity",
        "outcome",
        "duration_ms",
        "result_tokens",
        "error",
    }
    assert record["event"] == "tool_call"
    assert record["tool"] == "repo_search"
    assert record["outcome"] == "success"
    assert record["identity"] == "user-42"
    assert record["result_tokens"] == 1200
    assert record["error"] is None
    assert record["params"]["query"] == "how does routing work"


def test_duration_is_a_positive_integer():
    """Zero reads as "never ran", so a real call must never round down to it."""
    log, stream = logger()
    with timer() as t:
        sum(range(1000))
    log.record("repo_map", {}, duration_ms=t.ms)
    (record,) = lines(stream)
    assert isinstance(record["duration_ms"], int) and record["duration_ms"] >= 1


def test_the_timestamp_is_iso8601_with_milliseconds():
    log, stream = logger()
    log.record("repo_map", {})
    ts = lines(stream)[0]["ts"]
    assert ts.endswith("Z") and "T" in ts
    assert len(ts.split(".")[-1]) == 4, ts  # 3 digits + "Z"


def test_an_auth_rejection_is_recorded_as_such():
    log, stream = logger()
    log.record(
        "repo_search",
        {"query": "x"},
        identity="anonymous",
        outcome="auth_rejected",
        error="invalid bearer token",
    )
    (record,) = lines(stream)
    assert record["outcome"] == "auth_rejected"
    assert record["error"] == "invalid bearer token"


def test_an_error_carries_its_message():
    log, stream = logger()
    log.record("repo_neighbours", {"node_id": "sym:x"}, outcome="error", error="node not found")
    (record,) = lines(stream)
    assert record["outcome"] == "error" and record["error"] == "node not found"


def test_a_secret_embedded_in_an_error_message_is_redacted():
    """A downstream exception's str() can echo caller input verbatim -- e.g. a
    malformed request or an OS error including a path with an embedded token.
    The error field must go through the same redaction as every other value.
    """
    log, stream = logger()
    log.record(
        "repo_search",
        {"query": "x"},
        outcome="error",
        error="upstream rejected token ghp_" + "k" * 36,
    )
    (record,) = lines(stream)
    assert "ghp_" + "k" * 36 not in record["error"]
    assert record["error"].startswith("[redacted:github_token")


def test_anonymous_is_the_default_identity():
    log, stream = logger()
    log.record("repo_map", {})
    assert lines(stream)[0]["identity"] == "anonymous"


# --------------------------------------------------------------- level ----


def test_level_none_emits_nothing():
    log, stream = logger(level="none")
    log.record("repo_search", {"query": "x"})
    log.record("repo_search", {"query": "x"}, outcome="error", error="boom")
    assert stream.getvalue() == ""
    assert log.enabled is False


def test_level_errors_keeps_only_failures():
    log, stream = logger(level="errors")
    log.record("repo_search", {"query": "x"}, outcome="success")
    log.record("repo_search", {"query": "x"}, outcome="error", error="boom")
    log.record("repo_search", {"query": "x"}, outcome="auth_rejected")

    got = [r["outcome"] for r in lines(stream)]
    assert got == ["error", "auth_rejected"]


def test_an_unknown_level_is_refused_at_construction():
    with pytest.raises(ValueError, match="audit level"):
        AuditLogger(AuditConfig(level="verbose"))


# ------------------------------------------------------------ redaction ----


@pytest.mark.parametrize(
    "value,shape",
    [
        ("AKIAIOSFODNN7EXAMPLE", "aws_access_key"),
        ("ghp_" + "a" * 36, "github_token"),
        ("xoxb-123456789012-abcdefghijkl", "slack_token"),
        ("sk-" + "A" * 32, "openai_key"),
        ("AIza" + "B" * 35, "google_key"),
        ("-----BEGIN RSA PRIVATE KEY-----", "private_key"),
        ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl", "jwt"),
        ("https://user:hunter2@example.com/repo.git", "basic_auth_url"),
    ],
)
def test_credential_shapes_are_redacted_wherever_they_appear(value, shape):
    """Shape, not key name: a model can put a secret in any field."""
    got = sanitize_value("query", value)
    assert value not in got, got
    assert got.startswith(f"[redacted:{shape}")


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "api_key",
        "apiKey",
        "secret",
        "token",
        "authorization",
        "private_key",
        "session",
        "cookie",
        "access-key",
    ],
)
def test_secret_named_fields_are_redacted_whatever_they_hold(key):
    """A credential in an obviously named field may look unremarkable."""
    got = sanitize_value(key, "cat")
    assert got.startswith("[redacted:key:")
    assert "cat" not in got


def test_a_redacted_value_keeps_its_length_and_a_fingerprint():
    """Enough to correlate two sightings, far too little to reverse."""
    secret = "ghp_" + "z" * 36
    got = sanitize_value("query", secret)
    assert f"len={len(secret)}" in got
    assert "fp=" in got
    assert sanitize_value("query", secret) == got, "fingerprint must be stable"
    assert sanitize_value("query", "ghp_" + "y" * 36) != got, "and discriminating"


def test_paths_matching_exclude_secrets_are_redacted():
    """The same definition the retrieval layer refuses to return."""
    for path in ("app/.env", "config/secrets.yaml", "home/.ssh/id_rsa", "deploy/server.pem"):
        got = sanitize_value("path", path)
        assert got.startswith("[redacted:secret_path"), (path, got)
        assert path not in got


def test_ordinary_arguments_survive_untouched():
    """A log that redacts everything is as useless as one that redacts nothing."""
    params = sanitize_params(
        {
            "query": "how does the pack stay inside its budget",
            "node_id": "sym:repo2graph/cli.py::cmd_rag",
            "k": 8,
            "hops": 2,
            "budget_tokens": 6000,
            "exclude": False,
            "nothing": None,
            "path": "repo2graph/query.py",
        }
    )
    assert params["query"] == "how does the pack stay inside its budget"
    assert params["node_id"] == "sym:repo2graph/cli.py::cmd_rag"
    assert params["k"] == 8 and params["hops"] == 2
    assert params["exclude"] is False and params["nothing"] is None
    assert params["path"] == "repo2graph/query.py"


def test_redaction_recurses_into_containers():
    got = sanitize_params(
        {"outer": {"inner": {"password": "hunter2"}}, "list": ["ghp_" + "q" * 36, "fine"]}
    )
    assert "hunter2" not in json.dumps(got)
    assert got["outer"]["inner"]["password"].startswith("[redacted:key:")
    assert got["list"][0].startswith("[redacted:github_token")
    assert got["list"][1] == "fine"


def test_a_very_long_value_is_truncated():
    """An argument is a record of the call, not a copy of its payload."""
    got = sanitize_value("query", "word " * 400)
    assert len(got) < 700 and "chars]" in got


def test_a_high_entropy_blob_is_redacted():
    got = sanitize_value("anything", "aB3dE5fG7hJ9kL1mN3pQ5rS7tU9v")
    assert got.startswith("[redacted:high_entropy")


def test_prose_is_not_mistaken_for_a_credential():
    """The entropy rule must not fire on real questions."""
    for text in (
        "how does authentication work",
        "where is the password reset handler defined",
        "repo2graph/query.py",
    ):
        assert not sanitize_value("query", text).startswith("[redacted")


def test_the_whole_record_never_contains_a_secret():
    log, stream = logger()
    log.record(
        "repo_search",
        {"query": "deploy with ghp_" + "k" * 36, "token": "hunter2"},
        identity="user-1",
    )
    raw = stream.getvalue()
    assert "hunter2" not in raw
    assert "ghp_" + "k" * 36 not in raw


# ---------------------------------------------------------------- file ----


def test_the_file_sink_receives_the_same_lines(tmp_path):
    path = tmp_path / "audit.log"
    log, stream = logger(path=str(path))
    log.record("repo_map", {}, identity="user-9")
    log.close()

    on_disk = [
        json.loads(line) for line in path.read_text(encoding="utf8").splitlines() if line.strip()
    ]
    assert on_disk == lines(stream)


def test_the_file_sink_appends_rather_than_truncates(tmp_path):
    path = tmp_path / "audit.log"
    for i in range(3):
        log = AuditLogger(AuditConfig(path=str(path)), stream=io.StringIO())
        log.record("repo_map", {"n": i})
        log.close()
    assert len(path.read_text(encoding="utf8").strip().splitlines()) == 3


def test_every_line_is_flushed_immediately(tmp_path):
    """A crash must not lose the record of the call that caused it."""
    path = tmp_path / "audit.log"
    log = AuditLogger(AuditConfig(path=str(path)), stream=io.StringIO())
    log.record("repo_map", {})
    assert path.read_text(encoding="utf8").strip(), "record was still buffered"
    log.close()


def test_two_loggers_on_one_file_interleave_whole_lines(tmp_path):
    """Several server processes may share one --audit-log."""
    path = tmp_path / "audit.log"
    a = AuditLogger(AuditConfig(path=str(path)), stream=io.StringIO())
    b = AuditLogger(AuditConfig(path=str(path)), stream=io.StringIO())
    for i in range(20):
        (a if i % 2 else b).record("repo_map", {"n": i})
    a.close()
    b.close()

    rows = [line for line in path.read_text(encoding="utf8").splitlines() if line.strip()]
    assert len(rows) == 20
    for line in rows:
        json.loads(line)  # every line is whole and parseable


def test_an_unwritable_file_sink_does_not_take_the_server_down(tmp_path):
    """A broken audit sink is a degraded log, not an outage."""
    path = tmp_path / "audit.log"
    log = AuditLogger(AuditConfig(path=str(path)), stream=io.StringIO())
    log._file._fh.close()  # simulate the sink failing mid-run
    log.record("repo_map", {})  # must not raise
    log.close()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock path")
def test_posix_flock_oserror_still_appends_the_record(tmp_path, monkeypatch, capsys):
    """fcntl.flock OSError must fall through to an unlocked write, not drop the line."""
    import fcntl

    from repo2graph.audit import _LockedAppender

    _LockedAppender._lock_unavailable_warned = False

    def _unsupported(_fd: int, _op: int) -> None:
        raise OSError(95, "Operation not supported")

    monkeypatch.setattr(fcntl, "flock", _unsupported)

    path = tmp_path / "audit.log"
    log = AuditLogger(AuditConfig(path=str(path)), stream=io.StringIO())
    log.record("repo_map", {"query": "flock-oserror"})
    log.record("repo_map", {"query": "second-write"})
    log.close()

    rows = [
        json.loads(line) for line in path.read_text(encoding="utf8").splitlines() if line.strip()
    ]
    assert [r["params"]["query"] for r in rows] == ["flock-oserror", "second-write"]

    diagnostics = [json.loads(line) for line in capsys.readouterr().err.split("\n") if line.strip()]
    lock_events = [d for d in diagnostics if d.get("event") == "audit_lock_unavailable"]
    assert len(lock_events) == 1
    assert lock_events[0]["errno"] == 95
    assert lock_events[0]["path"] == str(path)


def test_a_record_that_will_not_serialise_still_produces_a_line():
    class Awkward:
        def __repr__(self):
            raise RuntimeError("no repr for you")

    log, stream = logger()
    log.record("repo_map", {"bad": Awkward()})
    (record,) = lines(stream)
    assert record["tool"] == "repo_map"
