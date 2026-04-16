import pytest
from unittest.mock import patch, MagicMock
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

    tools = asyncio.get_event_loop().run_until_complete(list_tools())
    history_tool = next(t for t in tools if t.name == "list_history")
    props = history_tool.inputSchema["properties"]
    assert "since" in props
    assert "until" in props


def test_list_history_max_limit_200():
    """list_history should allow up to 200 commits."""
    import asyncio
    from overleaf_mcp.server import list_tools

    tools = asyncio.get_event_loop().run_until_complete(list_tools())
    history_tool = next(t for t in tools if t.name == "list_history")
    assert "200" in history_tool.inputSchema["properties"]["limit"]["description"]


def test_get_diff_schema_has_context_and_truncation():
    """get_diff tool schema should include context_lines, max_output_chars, paths."""
    import asyncio
    from overleaf_mcp.server import list_tools

    tools = asyncio.get_event_loop().run_until_complete(list_tools())
    diff_tool = next(t for t in tools if t.name == "get_diff")
    props = diff_tool.inputSchema["properties"]
    assert "context_lines" in props
    assert "max_output_chars" in props
    assert "paths" in props
