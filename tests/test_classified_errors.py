import os
import pytest

from repo2graph.events import is_debug_mode, reraise_if_debug
from repo2graph.audit import AuditLogger
from repo2graph.cli import main
from repo2graph.graph import build
from repo2graph.parse import BuildConfig


def test_is_debug_mode(monkeypatch):
    monkeypatch.delenv("REPO2GRAPH_DEBUG", raising=False)
    assert not is_debug_mode()

    monkeypatch.setenv("REPO2GRAPH_DEBUG", "1")
    assert is_debug_mode()

    monkeypatch.setenv("REPO2GRAPH_DEBUG", "true")
    assert is_debug_mode()

    monkeypatch.setenv("REPO2GRAPH_DEBUG", "0")
    assert not is_debug_mode()


def test_reraise_if_debug_raises_in_debug_mode(monkeypatch):
    monkeypatch.setenv("REPO2GRAPH_DEBUG", "1")
    with pytest.raises(ValueError, match="test error"):
        reraise_if_debug(ValueError("test error"))

    try:
        raise KeyError("context error")
    except KeyError:
        with pytest.raises(KeyError, match="context error"):
            reraise_if_debug()


def test_reraise_if_debug_silent_when_not_debug(monkeypatch):
    monkeypatch.delenv("REPO2GRAPH_DEBUG", raising=False)
    # Should not raise
    reraise_if_debug(ValueError("should be ignored"))
    try:
        raise KeyError("context error")
    except KeyError:
        reraise_if_debug()


def test_cli_debug_flag_enables_debug_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("REPO2GRAPH_DEBUG", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "f.py").write_text("x = 1\n")
    out = tmp_path / "idx"
    try:
        ret = main(["build", str(repo), "-o", str(out), "--debug"])
        assert ret == 0
        assert os.environ.get("REPO2GRAPH_DEBUG") == "1"
    finally:
        os.environ.pop("REPO2GRAPH_DEBUG", None)


def test_audit_serialization_resilience_and_debug_reraise(tmp_path, monkeypatch):
    from repo2graph.audit import AuditConfig

    sink = tmp_path / "audit.jsonl"
    logger = AuditLogger(AuditConfig(path=str(sink)))

    class BrokenDict(dict):
        def items(self):
            raise RuntimeError("broken items")

    # When not in debug mode: does not raise, records degraded record
    monkeypatch.delenv("REPO2GRAPH_DEBUG", raising=False)
    logger.record("test_tool", BrokenDict(), outcome="success")

    # When in debug mode: reraises the internal error
    monkeypatch.setenv("REPO2GRAPH_DEBUG", "1")
    with pytest.raises(RuntimeError, match="broken items"):
        logger.record("test_tool", BrokenDict(), outcome="success")


def test_syntax_errors_do_not_crash_build_best_effort(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "corrupt.py").write_text("def def def ::: !!!\n")
    g = build(repo, config=BuildConfig())
    assert "file:corrupt.py" in g.nodes
