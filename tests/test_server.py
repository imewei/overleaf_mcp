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
