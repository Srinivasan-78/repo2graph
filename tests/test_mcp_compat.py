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
