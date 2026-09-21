# @authormark v1 -- do not remove (authorship watermark)⁠​​‌‌​​​‌​‌​​‌​‌‌​‌‌‌​​​‌​‌‌‌​​​​​‌‌​​​‌​​‌‌​‌‌​‌​‌​‌​​‌‌​‌‌​​‌​​​‌​​​​​‌​‌‌​​​‌​​‌‌​​‌‌​​‌​​‌​​‌​‌​​​‌​‌​‌​‌​​‌‌​​‌‌‌​​‌​​‌‌​​‌‌​‌​​​​‌‌​‌​​​‌​‌​‌‌​‌​‌‌​‌​​​‌​​​‌‌​‌​​‌​​‌‌​‌‌‌⁠
# Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
# Author: https://github.com/Srinivasan-78
# SPDX-License-Identifier: MIT
# Fingerprint: AMK1.1KqpbmSdAbfIES93CEkDi7
import pytest


def test_mcp_import_smoke():
    import repo2graph.mcp

    assert repo2graph.mcp.server is not None


def test_mcp_server_tools_registered():
    import repo2graph.mcp

    tools = repo2graph.mcp.server.tools
    assert "repo_map" in tools
    assert "repo_search" in tools
    assert "repo_neighbours" in tools
    assert "repo_cache_stats" in tools
    assert "repo_build_status" in tools


def test_startup_version_check():
    import mcp
    import repo2graph.mcp

    with pytest.raises(RuntimeError, match="repo2graph requires mcp>=1.0, found 0.9.0"):
        repo2graph.mcp.check_mcp_version("0.9.0")

    old_ver = getattr(mcp, "__version__", None)
    try:
        mcp.__version__ = "0.9.0"
        with pytest.raises(RuntimeError, match="repo2graph requires mcp>=1.0, found 0.9.0"):
            repo2graph.mcp.check_mcp_version()
    finally:
        if old_ver is not None:
            mcp.__version__ = old_ver
        else:
            delattr(mcp, "__version__")
