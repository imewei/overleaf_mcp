import pytest
from unittest.mock import patch
from overleaf_mcp.server import resolve_project, ProjectConfig


def test_resolve_project_inline_credentials():
    """Inline git_token + project_id should bypass config lookup."""
    result = resolve_project(
        project_name=None,
        git_token="tok_inline",
        project_id="proj_inline",
    )
    assert result.git_token == "tok_inline"
    assert result.project_id == "proj_inline"
    assert result.name == "inline"


def test_resolve_project_inline_requires_both():
    """Providing only one of git_token/project_id should raise."""
    with pytest.raises(ValueError, match="both"):
        resolve_project(project_name=None, git_token="tok", project_id=None)
    with pytest.raises(ValueError, match="both"):
        resolve_project(project_name=None, git_token=None, project_id="proj")


def test_resolve_project_falls_back_to_config():
    """When no inline credentials, delegates to get_project_config."""
    fake = ProjectConfig(name="Test", project_id="p1", git_token="t1")
    with patch("overleaf_mcp.server.get_project_config", return_value=fake) as mock:
        result = resolve_project(project_name="myproj")
        mock.assert_called_once_with("myproj")
        assert result.project_id == "p1"


def test_list_history_since_until_params_in_schema():
    """list_history tool schema should include since and until parameters."""
    import asyncio
    from overleaf_mcp.server import list_tools

    tools = asyncio.run(list_tools())
    history_tool = next(t for t in tools if t.name == "list_history")
    props = history_tool.inputSchema["properties"]
    assert "since" in props
    assert "until" in props


def test_list_history_max_limit_200():
    """list_history should allow up to 200 commits."""
    import asyncio
    from overleaf_mcp.server import list_tools

    tools = asyncio.run(list_tools())
    history_tool = next(t for t in tools if t.name == "list_history")
    assert "200" in history_tool.inputSchema["properties"]["limit"]["description"]


def test_get_diff_schema_has_context_and_truncation():
    """get_diff tool schema should include context_lines, max_output_chars, paths."""
    import asyncio
    from overleaf_mcp.server import list_tools

    tools = asyncio.run(list_tools())
    diff_tool = next(t for t in tools if t.name == "get_diff")
    props = diff_tool.inputSchema["properties"]
    assert "context_lines" in props
    assert "max_output_chars" in props
    assert "paths" in props


def test_status_summary_in_tool_list():
    """status_summary should be listed as an available tool."""
    import asyncio
    from overleaf_mcp.server import list_tools

    tools = asyncio.run(list_tools())
    names = [t.name for t in tools]
    assert "status_summary" in names


def test_write_tools_have_dry_run_and_push():
    """All write tools should have dry_run and push parameters."""
    import asyncio
    from overleaf_mcp.server import list_tools

    tools = asyncio.run(list_tools())
    write_tool_names = ["edit_file", "rewrite_file", "update_section", "create_file", "delete_file"]
    for tool_name in write_tool_names:
        tool = next(t for t in tools if t.name == tool_name)
        props = tool.inputSchema["properties"]
        assert "dry_run" in props, f"{tool_name} missing dry_run"
        assert "push" in props, f"{tool_name} missing push"


def test_all_project_tools_have_inline_credentials():
    """All tools that accept project_name as a config key should also accept git_token and project_id."""
    import asyncio
    from overleaf_mcp.server import list_tools

    # create_project uses project_name as a display label, not a config lookup key
    skip_tools = {"create_project"}

    tools = asyncio.run(list_tools())
    for tool in tools:
        if tool.name in skip_tools:
            continue
        props = tool.inputSchema.get("properties", {})
        if "project_name" in props:
            assert "git_token" in props, f"{tool.name} missing git_token"
            assert "project_id" in props, f"{tool.name} missing project_id"
