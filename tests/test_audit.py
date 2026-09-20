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
import os
import sys

import pytest

from repo2graph import audit as audit_mod
from repo2graph.audit import (
    MAX_SANITIZE_DEPTH,
    AuditConfig,
    AuditLogger,
    sanitize_params,
    sanitize_value,
    timer,
)


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


def test_identifier_queries_are_not_high_entropy():
    """Single-identifier repo_search queries are intentional and must remain.

    The old high_entropy gate treated any 24+ token-class string with a
    letter and a digit as a credential. `_` is in that class, so a
    snake_case name with a version digit matched — exactly the pinpoint
    queries Index._boost_identifiers exists to serve.
    """
    for text in (
        "resolve_import_python3_relative",
        "sha256_of_the_bytes_that_were_indexed",
        "test_iss25_query_constants_and_budget_bounds",
        "parse_source",
        "how does export write manifest",
    ):
        assert sanitize_value("query", text) == text, text

    params = sanitize_params({"query": "test_iss25_query_constants_and_budget_bounds"})
    assert params == {"query": "test_iss25_query_constants_and_budget_bounds"}


def test_high_entropy_and_vendor_shapes_still_redact_identifier_fields():
    """Tightening the identifier exemption must not open a hole for tokens."""
    blob = "aB3dE5fG7hJ9kL1mN3pQ5rS7tU9v"
    got = sanitize_value("query", blob)
    assert blob not in got
    assert got.startswith("[redacted:high_entropy")

    token = "ghp_" + "k" * 36
    got = sanitize_value("query", token)
    assert token not in got
    assert got.startswith("[redacted:github_token")


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


def test_a_record_that_will_not_serialise_still_produces_a_line():
    class Awkward:
        def __repr__(self):
            raise RuntimeError("no repr for you")

    log, stream = logger()
    log.record("repo_map", {"bad": Awkward()})
    (record,) = lines(stream)
    assert record["tool"] == "repo_map"


# ------------------------------------------------------- recursion depth ----
#
# Recursion here is driven entirely by an untrusted argument, and the
# RecursionError it used to raise escaped record() -- which http_server._reject
# calls from outside any try, on an *authentication failure*. A caller who
# cannot authenticate could therefore kill the handler thread. The cap is part
# of the contract, so these tests pin both the cap itself and the belt-and-
# braces fallback in record().


def nest(depth, leaf):
    """`leaf` wrapped in `depth` dicts: {"k": {"k": ... leaf ...}}."""
    value = leaf
    for _ in range(depth):
        value = {"k": value}
    return value


def test_nesting_far_past_the_recursion_limit_does_not_raise():
    """The literal repro from the issue: 3000 dicts deep."""
    got = sanitize_params(nest(3000, {"a": 1}))
    assert "[truncated:depth]" in json.dumps(got)


def test_the_depth_cap_truncates_rather_than_dropping_the_record():
    """Past the ceiling the record still exists and still says so."""
    deep = sanitize_params(nest(MAX_SANITIZE_DEPTH + 5, {"password": "hunter2"}))
    text = json.dumps(deep)
    assert "[truncated:depth]" in text
    assert "hunter2" not in text, "the cap must not become a way to smuggle a secret past redaction"


def test_ordinary_nesting_is_still_followed_all_the_way_down():
    """The cap is far beyond any real tool argument and must not bite."""
    got = sanitize_params(nest(5, {"password": "hunter2", "query": "how does routing work"}))
    inner = got
    for _ in range(5):
        inner = inner["k"]
    assert inner["query"] == "how does routing work"
    assert inner["password"].startswith("[redacted:key:")
    assert "[truncated:depth]" not in json.dumps(got)


def test_deeply_nested_arguments_still_produce_an_audit_record():
    """A hostile argument costs the record's contents, never the record."""
    log, stream = logger()
    log.record("repo_search", nest(3000, {"a": 1}), outcome="auth_rejected")
    (record,) = lines(stream)
    assert record["tool"] == "repo_search"
    assert record["outcome"] == "auth_rejected"


def test_params_that_cannot_be_sanitized_fall_back_to_a_record():
    """sanitize_params() runs inside the same try that guards json.dumps().

    A dict subclass whose items() raises is the general shape: anything the
    sanitizer itself trips over must degrade to the "could not be serialised"
    record rather than escaping into a caller with no except of its own.
    """

    class Hostile(dict):
        def items(self):
            raise RuntimeError("no items for you")

    log, stream = logger()
    log.record("repo_search", Hostile(query="x"), identity="user-3", outcome="auth_rejected")
    (record,) = lines(stream)
    assert record["error"] == "audit record could not be serialised"
    assert record["tool"] == "repo_search"
    assert record["identity"] == "user-3"
    assert record["outcome"] == "auth_rejected"
    assert record["params"] == {}


# --------------------------------------------------------- lock failures ----
#
# flock fails with OSError on a filesystem with no advisory locking (NFS
# without lockd, several FUSE and overlay mounts). That used to escape
# _acquire into write()'s `except Exception: return`, so the file sink lost the
# record while the stderr copy still appeared -- two sinks disagreeing with
# nothing to say so. The fallthrough the code already had ("still write") was
# unreachable for the case it was written for.

UNSUPPORTED = OSError(95, "Operation not supported")


class NoLockFcntl:
    """Stand-in for `fcntl` on a filesystem that refuses advisory locks.

    Injected into `sys.modules` so the POSIX branch of `_acquire` is exercised
    on Windows too. Without it that branch is dead code in CI's Windows leg,
    which is exactly where a POSIX-only regression would hide.
    """

    LOCK_EX = 2
    LOCK_UN = 8

    @staticmethod
    def flock(fd, operation):
        raise UNSUPPORTED


@pytest.fixture
def unwarned(monkeypatch):
    """Reset the once-per-process locking warning so a test can observe it."""
    monkeypatch.setattr(audit_mod, "_lock_warning_sent", False)


def read_lines(path):
    return [line for line in path.read_text(encoding="utf8").splitlines() if line.strip()]


def test_the_posix_branch_keeps_the_record_when_flock_raises(tmp_path, monkeypatch, unwarned):
    """Runs on every platform: the POSIX branch is simulated, not skipped."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setitem(sys.modules, "fcntl", NoLockFcntl)

    path = tmp_path / "audit.log"
    log = AuditLogger(AuditConfig(path=str(path)), stream=io.StringIO())
    log.record("repo_map", {"n": 1})
    log.record("repo_map", {"n": 2})
    log.close()

    rows = [json.loads(line) for line in read_lines(path)]
    assert [r["params"]["n"] for r in rows] == [1, 2]


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl is POSIX-only")
def test_a_real_flock_raising_oserror_still_appends(tmp_path, monkeypatch, unwarned):
    """The same thing against the real module, on the platform that has it."""
    import fcntl

    def refuse(fd, operation):
        raise UNSUPPORTED

    monkeypatch.setattr(fcntl, "flock", refuse)

    path = tmp_path / "audit.log"
    log = AuditLogger(AuditConfig(path=str(path)), stream=io.StringIO())
    log.record("repo_map", {"n": 7})
    log.close()

    (row,) = [json.loads(line) for line in read_lines(path)]
    assert row["params"]["n"] == 7


@pytest.mark.skipif(sys.platform != "win32", reason="msvcrt is Windows-only")
def test_the_win32_branch_keeps_the_record_when_locking_raises(tmp_path, monkeypatch, unwarned):
    """The win32 branch already caught OSError; keep it pinned that way."""
    import msvcrt

    def refuse(fd, mode, nbytes):
        raise UNSUPPORTED

    monkeypatch.setattr(msvcrt, "locking", refuse)

    path = tmp_path / "audit.log"
    log = AuditLogger(AuditConfig(path=str(path)), stream=io.StringIO())
    log.record("repo_map", {"n": 7})
    log.close()

    (row,) = [json.loads(line) for line in read_lines(path)]
    assert row["params"]["n"] == 7


def test_unavailable_locking_is_announced_once_not_per_record(
    tmp_path, monkeypatch, unwarned, capsys
):
    """An operator must learn that records are unserialised, not absent.

    Once: the condition is a property of the filesystem, so a warning per
    write would reproduce the audit log on stderr and bury itself.
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setitem(sys.modules, "fcntl", NoLockFcntl)

    path = tmp_path / "audit.log"
    log = AuditLogger(AuditConfig(path=str(path)), stream=io.StringIO())
    for i in range(5):
        log.record("repo_map", {"n": i})
    log.close()

    warnings = [
        json.loads(line)
        for line in capsys.readouterr().err.splitlines()
        if line.strip().startswith("{")
    ]
    events = [w for w in warnings if w.get("event") == "audit_lock_unavailable"]
    assert len(events) == 1, warnings
    assert events[0]["level"] == "warning"
    assert events[0]["path"] == str(path)
    assert len(read_lines(path)) == 5


# ------------------------------------------------------ opening the sink ----
#
# write() has always held the line that a broken sink is a degraded log rather
# than an outage. The constructor did not: it opened the file unguarded, and
# `repo2graph-mcp --audit-log /nonexistent/dir/audit.log` died with a raw
# traceback before the server started.


def test_a_missing_parent_directory_is_created(tmp_path):
    path = tmp_path / "logs" / "nested" / "audit.log"
    log = AuditLogger(AuditConfig(path=str(path)), stream=io.StringIO())
    log.record("repo_map", {"n": 1})
    log.close()
    (row,) = [json.loads(line) for line in read_lines(path)]
    assert row["params"]["n"] == 1


def test_a_sink_that_cannot_be_opened_does_not_stop_the_server(tmp_path, capsys):
    """Parent is a regular file: no directory can be made and no file opened."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("i am a file\n", encoding="utf8")
    path = blocker / "audit.log"

    log = AuditLogger(AuditConfig(path=str(path)))  # must not raise
    assert log._file is not None and log._file._fh is None
    log.record("repo_map", {"n": 1}, identity="user-4")  # must not raise
    log.close()  # must not raise

    err = capsys.readouterr().err
    records = [json.loads(line) for line in err.splitlines() if line.strip().startswith("{")]
    assert any(r.get("event") == "audit_sink_unavailable" for r in records), err
    calls = [r for r in records if r.get("event") == "tool_call"]
    assert len(calls) == 1 and calls[0]["identity"] == "user-4"


def test_an_unopenable_sink_reports_itself_once(tmp_path, capsys):
    """One line at construction, not one per record: there is nothing to retry."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("i am a file\n", encoding="utf8")

    log = AuditLogger(AuditConfig(path=str(blocker / "audit.log")), stream=io.StringIO())
    for i in range(4):
        log.record("repo_map", {"n": i})
    log.close()

    records = [
        json.loads(line)
        for line in capsys.readouterr().err.splitlines()
        if line.strip().startswith("{")
    ]
    assert sum(r.get("event") == "audit_sink_unavailable" for r in records) == 1, records


# ------------------------------------------------------------- durability ----
#
# fsync per record was taken while holding both the thread lock and the OS
# file lock, so on the ThreadingHTTPServer transport every concurrent request
# queued behind a disk sync. flush() is what the interleaving guarantee needs;
# fsync() adds crash durability for a copy that stderr already has.


@pytest.fixture
def fsync_calls(monkeypatch):
    """Count os.fsync calls while still performing them."""
    calls = []
    real = os.fsync

    def counting(fd):
        calls.append(fd)
        return real(fd)

    monkeypatch.setattr(os, "fsync", counting)
    return calls


def test_the_file_sink_does_not_fsync_per_record_by_default(tmp_path, fsync_calls):
    path = tmp_path / "audit.log"
    log = AuditLogger(AuditConfig(path=str(path)), stream=io.StringIO())
    for i in range(3):
        log.record("repo_map", {"n": i})
    assert fsync_calls == []
    # flush() alone still puts whole lines in the file, which is what the
    # interleaving guarantee rests on.
    assert len(read_lines(path)) == 3
    log.close()


def test_fsync_is_available_for_deployments_that_need_it(tmp_path, fsync_calls):
    path = tmp_path / "audit.log"
    log = AuditLogger(AuditConfig(path=str(path), fsync=True), stream=io.StringIO())
    for i in range(3):
        log.record("repo_map", {"n": i})
    assert len(fsync_calls) == 3
    assert len(read_lines(path)) == 3
    log.close()
