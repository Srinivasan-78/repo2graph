"""The build-performance gate's arithmetic (#304). The fixtures themselves run
weekly in .github/workflows/perf.yml, not in the test suite."""

from __future__ import annotations

from scripts.perf_bench import FIXTURES, regressions


def test_a_slowdown_past_the_tolerance_is_reported():
    base = {"medium": {"build_seconds": 2.0, "peak_rss_mb": 100.0}}
    assert regressions({"medium": {"build_seconds": 3.9, "peak_rss_mb": 100}}, base) == []
    found = regressions({"medium": {"build_seconds": 4.1, "peak_rss_mb": 151}}, base)
    assert any("build_seconds" in f for f in found) and any("peak_rss_mb" in f for f in found)


def test_a_slower_machine_scales_only_the_time_limits():
    base = {"medium": {"build_seconds": 2.0, "peak_rss_mb": 100.0}}
    got = {"medium": {"build_seconds": 7.0, "peak_rss_mb": 160.0}}
    found = regressions(got, base, machine=2.0)
    assert not any("build_seconds" in f for f in found)
    assert any("peak_rss_mb" in f for f in found)


def test_noise_below_the_floor_is_not_gated():
    base = {"tiny": {"build_seconds": 0.1, "query_ms": 1.0}}
    assert regressions({"tiny": {"build_seconds": 0.9, "query_ms": 20.0}}, base) == []


def test_the_default_set_leaves_out_the_50k_file_fixture():
    from scripts.perf_bench import DEFAULT_SET

    assert "large" in FIXTURES and "large" not in DEFAULT_SET
    assert {"tiny", "medium", "many-small", "deep-tree", "call-heavy", "git-history"} <= set(
        DEFAULT_SET
    )
