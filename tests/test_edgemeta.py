"""Tests for repo2graph.edgemeta module and counts_as_call helper."""

from repo2graph.edgemeta import (
    OVERVIEW_MIN_CALL_CONFIDENCE,
    SCOPED_CALL_KINDS,
    counts_as_call,
)


def test_counts_as_call_untyped_receiver():
    assert counts_as_call({"type": "CALLS", "untyped_receiver": True, "confidence": 1.0}) is False


def test_counts_as_call_scoped_kinds():
    for kind in SCOPED_CALL_KINDS:
        assert (
            counts_as_call({"type": "CALLS", "resolution_kind": kind, "confidence": 0.333}) is True
        )


def test_counts_as_call_confidence_threshold():
    assert counts_as_call({"type": "CALLS", "confidence": OVERVIEW_MIN_CALL_CONFIDENCE}) is True
    assert (
        counts_as_call({"type": "CALLS", "confidence": OVERVIEW_MIN_CALL_CONFIDENCE - 0.1}) is False
    )
    assert counts_as_call({"type": "CALLS"}) is True
