"""Targeted tests for the coverage gaps identified after Tier-3 (v2) shipped.

The v2 work moved overall branch coverage to 90 %. This file closes the
remaining holes — primarily error-path branches in config / tools that were
not exercised by the existing dispatcher integration tests.

Organized by source module. Each test names the specific line range it
targets so future maintainers can keep the coupling visible.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from git import GitCommandError

from overleaf_mcp import config as config_mod
from overleaf_mcp import git_ops
from overleaf_mcp.config import (
    Config,
    ProjectConfig,
    _env_config,
    get_project_config,
    load_config,
)
from overleaf_mcp.tools import (
    create_project,
    execute_tool,
    get_diff,
    list_history,
    list_tools,
)


@pytest.fixture(autouse=True)
def _reset_module_caches():
    """Test isolation — share the same shape as the other suites' autouse fixture."""
    config_mod._CONFIG_CACHE = None
    git_ops._LAST_PULL.clear()
    git_ops._PROJECT_RWLOCKS.clear()
    yield
    config_mod._CONFIG_CACHE = None
    git_ops._LAST_PULL.clear()
    git_ops._PROJECT_RWLOCKS.clear()


# ---------------------------------------------------------------------------
# config.py — _env_config (line 69 path) and get_project_config (lines 112,
# 117->120, 121-122)
# ---------------------------------------------------------------------------


def test_env_config_builds_default_project_when_both_vars_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """OVERLEAF_PROJECT_ID + OVERLEAF_GIT_TOKEN both set → 1-project Config."""
    # Hide the file-based config so the env fallback path engages
    monkeypatch.setattr(config_mod, "CONFIG_FILE", str(tmp_path / "no-such-file.json"))
    monkeypatch.setenv("OVERLEAF_PROJECT_ID", "envProj")
    monkeypatch.setenv("OVERLEAF_GIT_TOKEN", "envTok")

    cfg = _env_config()
    assert "default" in cfg.projects
    assert cfg.default_project == "default"
    assert cfg.projects["default"].project_id == "envProj"
    assert cfg.projects["default"].git_token == "envTok"


def test_env_config_returns_empty_when_only_one_var_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A half-configured env (only ID, no token) gives an empty Config — never partial."""
    monkeypatch.setattr(config_mod, "CONFIG_FILE", str(tmp_path / "no-such-file.json"))
    monkeypatch.setenv("OVERLEAF_PROJECT_ID", "lonely")
    monkeypatch.delenv("OVERLEAF_GIT_TOKEN", raising=False)

    cfg = _env_config()
    assert cfg.projects == {}
    assert cfg.default_project is None


def test_get_project_config_raises_when_no_projects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """No file, no env vars → ValueError with actionable message."""
    monkeypatch.setattr(config_mod, "CONFIG_FILE", str(tmp_path / "absent.json"))
    monkeypatch.delenv("OVERLEAF_PROJECT_ID", raising=False)
    monkeypatch.delenv("OVERLEAF_GIT_TOKEN", raising=False)

    with pytest.raises(ValueError, match="No projects configured"):
        get_project_config()


def test_get_project_config_falls_back_to_first_when_no_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """When defaultProject is unset, the first dict key is used."""
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(
        json.dumps(
            {
                "projects": {
                    "first": {"name": "First", "projectId": "p1", "gitToken": "t1"},
                    "second": {"name": "Second", "projectId": "p2", "gitToken": "t2"},
                }
                # No defaultProject key
            }
        )
    )
    monkeypatch.setattr(config_mod, "CONFIG_FILE", str(cfg_path))

    proj = get_project_config()
    assert proj.project_id == "p1"  # first key wins


def test_get_project_config_raises_for_unknown_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Asking for a non-existent project name lists what IS available."""
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(
        json.dumps(
            {
                "projects": {
                    "alpha": {"name": "Alpha", "projectId": "pA", "gitToken": "tA"},
                },
                "defaultProject": "alpha",
            }
        )
    )
    monkeypatch.setattr(config_mod, "CONFIG_FILE", str(cfg_path))

    with pytest.raises(ValueError, match="Project 'beta' not found"):
        get_project_config("beta")


def test_load_config_uses_env_fallback_when_file_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """The load_config -> _env_config path (line 104) when no file exists."""
    monkeypatch.setattr(config_mod, "CONFIG_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setenv("OVERLEAF_PROJECT_ID", "envOnly")
    monkeypatch.setenv("OVERLEAF_GIT_TOKEN", "envOnlyTok")

    cfg = load_config()
    assert isinstance(cfg, Config)
    assert cfg.projects["default"].project_id == "envOnly"


# ---------------------------------------------------------------------------
# git_ops.py — env-var parser ValueError fallbacks (lines 239-240, 247-248,
# 265-266) and the shallow-clone log line (line 322)
# ---------------------------------------------------------------------------


def test_pull_ttl_falls_back_on_garbage_env(monkeypatch: pytest.MonkeyPatch):
    """OVERLEAF_PULL_TTL=not-a-number → defaults to 30s, doesn't raise."""
    monkeypatch.setenv("OVERLEAF_PULL_TTL", "not-a-number")
    assert git_ops._pull_ttl() == git_ops._DEFAULT_PULL_TTL


def test_git_timeout_falls_back_on_garbage_env(monkeypatch: pytest.MonkeyPatch):
    """OVERLEAF_GIT_TIMEOUT=garbage → defaults to 60s, doesn't raise."""
    monkeypatch.setenv("OVERLEAF_GIT_TIMEOUT", "abc")
    assert git_ops._git_timeout() == git_ops._DEFAULT_GIT_TIMEOUT


def test_shallow_depth_falls_back_on_garbage_env(monkeypatch: pytest.MonkeyPatch):
    """OVERLEAF_SHALLOW_DEPTH=garbage → defaults to 1, doesn't raise."""
    monkeypatch.setenv("OVERLEAF_SHALLOW_CLONE", "1")
    monkeypatch.setenv("OVERLEAF_SHALLOW_DEPTH", "infinity")
    assert git_ops._shallow_clone_kwargs() == {"depth": git_ops._DEFAULT_SHALLOW_DEPTH}


def test_ensure_repo_logs_shallow_depth_on_cold_clone(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    """Cold clone with OVERLEAF_SHALLOW_CLONE=1 must log the depth (line 322)."""
    import logging

    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    monkeypatch.setenv("OVERLEAF_SHALLOW_CLONE", "1")
    monkeypatch.setenv("OVERLEAF_SHALLOW_DEPTH", "3")
    caplog.set_level(logging.INFO, logger="overleaf_mcp.git_ops")

    from configparser import NoSectionError

    proj = ProjectConfig(name="t", project_id="newProj", git_token="tok")
    fake_repo = MagicMock()
    # Fresh clone → no [user] section yet → config_git_user falls through
    # to set the value. We don't care about the writer details here, only
    # that the shallow-clone log line fires.
    fake_repo.config_reader.return_value.get_value.side_effect = NoSectionError("user")
    with patch("overleaf_mcp.git_ops.Repo.clone_from", return_value=fake_repo):
        git_ops.ensure_repo(proj)

    log_msgs = [r.message for r in caplog.records]
    shallow_logs = [m for m in log_msgs if "shallow depth=3" in m]
    assert shallow_logs, (
        f"Expected shallow-depth log line, got: {log_msgs}"
    )


# ---------------------------------------------------------------------------
# tools.py — create_project variants (lines 117-119, 128)
# ---------------------------------------------------------------------------


def test_create_project_with_zip_uses_zip_mime_type():
    """is_zip=True must produce a 'application/zip' data URL with content
    used verbatim (no double base64-encoding)."""
    pre_encoded_zip_payload = "UEsDBBQACAAIAAAA=="  # arbitrary base64-shaped string

    result = asyncio.run(
        create_project(
            content=pre_encoded_zip_payload,
            is_zip=True,
        )
    )

    assert "data:application/zip;base64," in result
    assert pre_encoded_zip_payload in result, (
        "Pre-encoded zip content should be embedded verbatim, not re-encoded"
    )


def test_create_project_passes_through_project_name():
    """When project_name is given, it lands in snip_name (form_data line 128)."""
    result = asyncio.run(
        create_project(
            content=r"\documentclass{article}\begin{document}X\end{document}",
            project_name="My Cool Paper",
        )
    )
    # snip_name= gets URL-encoded — "My%20Cool%20Paper"
    assert "snip_name=My%20Cool%20Paper" in result


# ---------------------------------------------------------------------------
# tools.py — list_history with filters + empty result (lines 410, 412, 414, 423)
# ---------------------------------------------------------------------------


def test_list_history_returns_no_commits_message_for_empty_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """When iter_commits returns [], the 'No commits found' branch fires."""
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    (tmp_path / "p1").mkdir()

    fake_repo = MagicMock()
    fake_repo.iter_commits.return_value = iter([])  # empty history
    fake_repo.config_reader.return_value.get_value.return_value = "stamped"

    with patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
        result = asyncio.run(
            list_history(git_token="tok", project_id="p1")
        )
    assert "No commits found" in result


def test_list_history_threads_all_filter_kwargs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """file_path, since, and until must all reach iter_commits as kwargs."""
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    (tmp_path / "p1").mkdir()

    fake_repo = MagicMock()
    fake_repo.iter_commits.return_value = iter([])
    fake_repo.config_reader.return_value.get_value.return_value = "stamped"

    with patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
        asyncio.run(
            list_history(
                file_path="main.tex",
                since="2026-01-01",
                until="2026-12-31",
                git_token="tok",
                project_id="p1",
            )
        )

    # Inspect the call_args for iter_commits
    call_kwargs = fake_repo.iter_commits.call_args.kwargs
    assert call_kwargs.get("paths") == "main.tex"
    assert call_kwargs.get("after") == "2026-01-01"
    assert call_kwargs.get("before") == "2026-12-31"


# ---------------------------------------------------------------------------
# tools.py — get_diff edges (lines 491, 493, 510-511, 516-517, 524, invalid mode)
# ---------------------------------------------------------------------------


def test_get_diff_rejects_unknown_mode_inline():
    """Bad mode is rejected before any project lookup happens (no fixture needed)."""
    result = asyncio.run(get_diff(mode="banana"))
    assert "unknown diff mode" in result
    assert "'banana'" in result


def test_get_diff_threads_path_filters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Both file_path and paths get appended after a -- separator."""
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    (tmp_path / "p1").mkdir()

    fake_repo = MagicMock()
    fake_repo.git.diff.return_value = ""
    fake_repo.config_reader.return_value.get_value.return_value = "stamped"

    with patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
        asyncio.run(
            get_diff(
                file_path="main.tex",
                paths=["chapter1.tex", "chapter2.tex"],
                git_token="tok",
                project_id="p1",
            )
        )

    args = list(fake_repo.git.diff.call_args.args)
    # The -- separator and both filter kinds must all be present
    assert "--" in args
    sep_idx = args.index("--")
    after_sep = args[sep_idx + 1:]
    assert "main.tex" in after_sep
    assert "chapter1.tex" in after_sep
    assert "chapter2.tex" in after_sep


def test_get_diff_returns_error_on_git_command_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A bad ref bubbles up as 'Error getting diff: ...' string (line 516-517)."""
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    (tmp_path / "p1").mkdir()

    fake_repo = MagicMock()
    fake_repo.git.diff.side_effect = GitCommandError(
        "diff", 128, b"", b"unknown revision 'badref'"
    )
    fake_repo.config_reader.return_value.get_value.return_value = "stamped"

    with patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
        result = asyncio.run(
            get_diff(from_ref="badref", git_token="tok", project_id="p1")
        )
    assert "Error getting diff" in result
    assert "unknown revision" in result


def test_get_diff_truncates_oversized_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A diff larger than max_output_chars is truncated with a marker."""
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    (tmp_path / "p1").mkdir()

    fake_repo = MagicMock()
    fake_repo.git.diff.return_value = "X" * 10000
    fake_repo.config_reader.return_value.get_value.return_value = "stamped"

    with patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
        result = asyncio.run(
            get_diff(
                max_output_chars=2000,  # min clamp; smallest valid limit
                git_token="tok",
                project_id="p1",
            )
        )
    assert "[diff truncated]" in result


# ---------------------------------------------------------------------------
# tools.py — execute_tool unknown name (line 929) and list_tools fallback
# ---------------------------------------------------------------------------


def test_execute_tool_returns_unknown_for_missing_name():
    """Dispatcher's safety net for unregistered tool names (line 929)."""
    result = asyncio.run(execute_tool("does_not_exist", {}))
    assert "Unknown tool" in result
    assert "does_not_exist" in result


def test_list_tools_handles_unannotated_param_gracefully():
    """A function in TOOLS without a real annotation falls back to {'type':'string'}.

    Targets the except block in list_tools (lines 962-963). We inject a
    no-annotation function temporarily and verify list_tools doesn't crash.
    """
    from typing import Annotated

    from pydantic import Field

    from overleaf_mcp import tools as tools_mod

    async def weird_tool(
        # An unresolvable forward reference makes TypeAdapter raise in
        # json_schema() — exercising the fallback branch (lines 962-963).
        # noqa pragmas are intentional: this is precisely the bad
        # annotation we want list_tools to recover from gracefully.
        x: Annotated["__never_defined_anywhere__", Field(description="weird")] = "x",  # type: ignore[name-defined,valid-type]  # noqa: UP037,F821
    ) -> str:
        return "ok"

    fake_registry = dict(tools_mod.TOOLS)
    fake_registry["__weird__"] = weird_tool

    with patch.object(tools_mod, "TOOLS", fake_registry):
        result = asyncio.run(list_tools())

    # Must not raise; the weird tool appears with the fallback string schema
    weird = next((t for t in result if t.name == "__weird__"), None)
    assert weird is not None
    assert weird.inputSchema["properties"]["x"]["type"] == "string"


# ---------------------------------------------------------------------------
# tools.py — read-tool "file not found" branches (lines 333, 366)
# ---------------------------------------------------------------------------


def _setup_repo_with_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **files: str
) -> MagicMock:
    """Helper: create a project dir under tmp_path with given file content,
    return a MagicMock Repo for use with patch('overleaf_mcp.git_ops.Repo').
    """
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    repo_dir = tmp_path / "p1"
    repo_dir.mkdir()
    for name, content in files.items():
        (repo_dir / name).write_text(content)
    fake_repo = MagicMock()
    fake_repo.config_reader.return_value.get_value.return_value = "stamped"
    return fake_repo


def test_get_sections_returns_error_for_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """get_sections on a non-existent file → 'Error: File not found' (line 333)."""
    from overleaf_mcp.tools import get_sections

    fake_repo = _setup_repo_with_files(tmp_path, monkeypatch)
    with patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
        result = asyncio.run(
            get_sections(file_path="missing.tex", git_token="t", project_id="p1")
        )
    assert "Error" in result and "missing.tex" in result


def test_get_section_content_returns_error_for_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """get_section_content on a non-existent file → error (line 366)."""
    from overleaf_mcp.tools import get_section_content

    fake_repo = _setup_repo_with_files(tmp_path, monkeypatch)
    with patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
        result = asyncio.run(
            get_section_content(
                file_path="absent.tex",
                section_title="Intro",
                git_token="t",
                project_id="p1",
            )
        )
    assert "Error" in result and "absent.tex" in result


# ---------------------------------------------------------------------------
# tools.py — status_summary edge cases (572-573, 578-579, 584-608)
# ---------------------------------------------------------------------------


def test_status_summary_handles_no_commits_and_detached_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Empty repo (no head commit) AND detached HEAD both degrade gracefully."""
    from overleaf_mcp.tools import status_summary

    fake_repo = _setup_repo_with_files(tmp_path, monkeypatch)
    # head.commit raises ValueError when there are no commits
    type(fake_repo.head).commit = property(
        lambda self: (_ for _ in ()).throw(ValueError("no commits"))
    )
    # active_branch raises TypeError when HEAD is detached
    type(fake_repo).active_branch = property(
        lambda self: (_ for _ in ()).throw(TypeError("detached"))
    )

    with patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
        result = asyncio.run(
            status_summary(git_token="t", project_id="p1")
        )

    assert "(no commits)" in result
    assert "(detached HEAD)" in result


def test_status_summary_no_main_tex_when_no_documentclass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """No .tex file with \\documentclass → '(no main .tex file detected)' (line 608)."""
    from overleaf_mcp.tools import status_summary

    # A .tex file that's clearly not a main document
    fake_repo = _setup_repo_with_files(
        tmp_path, monkeypatch, **{"snippet.tex": "Just a fragment\n\\section{X}\n"},
    )
    fake_repo.head.commit.hexsha = "deadbeef" * 5
    fake_repo.head.commit.committed_datetime.strftime.return_value = "2026-04-16 10:00"
    fake_repo.head.commit.author.name = "Author"
    fake_repo.head.commit.message = "msg"
    fake_repo.active_branch.name = "main"

    with patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
        result = asyncio.run(status_summary(git_token="t", project_id="p1"))

    assert "no main .tex file detected" in result


def test_status_summary_main_tex_with_no_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Main document found but contains no sections → '(no sections found)' (line 606)."""
    from overleaf_mcp.tools import status_summary

    # A main document with no \section commands
    fake_repo = _setup_repo_with_files(
        tmp_path, monkeypatch,
        **{"main.tex": r"\documentclass{article}\begin{document}body only\end{document}"},
    )
    fake_repo.head.commit.hexsha = "deadbeef" * 5
    fake_repo.head.commit.committed_datetime.strftime.return_value = "2026-04-16 10:00"
    fake_repo.head.commit.author.name = "Author"
    fake_repo.head.commit.message = "msg"
    fake_repo.active_branch.name = "main"

    with patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
        result = asyncio.run(status_summary(git_token="t", project_id="p1"))

    assert "Main document: main.tex" in result
    assert "(no sections found)" in result


# ---------------------------------------------------------------------------
# tools.py — write tool "file not found" + dry_run + push=False branches
# (lines 642, 707-708, 753, 776, 781-782, 877-878 + 672->675, 719->722,
# 802->805, 886->889)
# ---------------------------------------------------------------------------


def test_edit_file_returns_error_for_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """edit_file on a non-existent path → error message (line 642)."""
    from overleaf_mcp.tools import edit_file

    fake_repo = _setup_repo_with_files(tmp_path, monkeypatch)
    with patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
        result = asyncio.run(
            edit_file(
                file_path="ghost.tex",
                old_string="x",
                new_string="y",
                git_token="t",
                project_id="p1",
            )
        )
    assert "Error" in result and "ghost.tex" in result


def test_rewrite_file_dry_run_reports_size_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """dry_run on rewrite_file reports old/new sizes without writing (lines 707-708)."""
    from overleaf_mcp.tools import rewrite_file

    original = "abc" * 100  # 300 bytes
    fake_repo = _setup_repo_with_files(tmp_path, monkeypatch, **{"main.tex": original})

    with patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
        result = asyncio.run(
            rewrite_file(
                file_path="main.tex",
                content="new content",
                dry_run=True,
                git_token="t",
                project_id="p1",
            )
        )

    assert "Dry run" in result
    assert "300 bytes" in result  # existing size
    assert "11 chars" in result  # new content size
    # File must NOT have been written
    assert (tmp_path / "p1" / "main.tex").read_text() == original


def test_rewrite_file_skips_push_when_push_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """push=False commits but doesn't push (line 719->722 false branch)."""
    from overleaf_mcp.tools import rewrite_file

    fake_repo = _setup_repo_with_files(tmp_path, monkeypatch, **{"main.tex": "old"})
    with patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
        result = asyncio.run(
            rewrite_file(
                file_path="main.tex",
                content="new",
                push=False,
                git_token="t",
                project_id="p1",
            )
        )

    fake_repo.index.commit.assert_called()
    fake_repo.remotes.origin.push.assert_not_called()
    assert "and pushed" not in result  # the bool is False → omitted phrase


def test_update_section_returns_error_for_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """update_section on a non-existent file → error (line 753)."""
    from overleaf_mcp.tools import update_section

    fake_repo = _setup_repo_with_files(tmp_path, monkeypatch)
    with patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
        result = asyncio.run(
            update_section(
                file_path="ghost.tex",
                section_title="Intro",
                new_content="x",
                git_token="t",
                project_id="p1",
            )
        )
    assert "Error" in result


def test_update_section_dry_run_reports_size_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """dry_run on update_section reports body sizes without writing (781-782)."""
    from overleaf_mcp.tools import update_section

    original = (
        r"\documentclass{article}" "\n"
        r"\begin{document}" "\n"
        r"\section{Intro}" "\n"
        "Old body text here.\n"
        r"\section{Methods}" "\n"
        "Methods body.\n"
        r"\end{document}"
    )
    fake_repo = _setup_repo_with_files(
        tmp_path, monkeypatch, **{"main.tex": original}
    )
    with patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
        result = asyncio.run(
            update_section(
                file_path="main.tex",
                section_title="Intro",
                new_content="brand new body",
                dry_run=True,
                git_token="t",
                project_id="p1",
            )
        )

    assert "Dry run" in result
    assert "section 'Intro'" in result
    # File untouched
    assert (tmp_path / "p1" / "main.tex").read_text() == original


def test_delete_file_dry_run_reports_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """dry_run on delete_file reports the file size without unlinking (877-878)."""
    from overleaf_mcp.tools import delete_file

    payload = "X" * 1024
    fake_repo = _setup_repo_with_files(tmp_path, monkeypatch, **{"old.tex": payload})

    with patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
        result = asyncio.run(
            delete_file(
                file_path="old.tex",
                dry_run=True,
                git_token="t",
                project_id="p1",
            )
        )

    assert "Dry run" in result
    assert "1024 bytes" in result
    # File must still exist
    assert (tmp_path / "p1" / "old.tex").exists()


def test_delete_file_skips_push_when_push_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """push=False on delete_file: commits but no push (886->889)."""
    from overleaf_mcp.tools import delete_file

    fake_repo = _setup_repo_with_files(tmp_path, monkeypatch, **{"old.tex": "bye"})
    with patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
        result = asyncio.run(
            delete_file(
                file_path="old.tex",
                push=False,
                git_token="t",
                project_id="p1",
            )
        )

    fake_repo.index.commit.assert_called()
    fake_repo.remotes.origin.push.assert_not_called()
    assert "Deleted" in result and "and pushed" not in result


def test_update_section_skips_push_when_push_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """push=False on update_section: commit but no push (802->805)."""
    from overleaf_mcp.tools import update_section

    src = (
        r"\documentclass{article}" "\n"
        r"\begin{document}" "\n"
        r"\section{Intro}" "\n"
        "Body.\n"
        r"\end{document}"
    )
    fake_repo = _setup_repo_with_files(tmp_path, monkeypatch, **{"main.tex": src})
    with patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
        result = asyncio.run(
            update_section(
                file_path="main.tex",
                section_title="Intro",
                new_content="rewritten",
                push=False,
                git_token="t",
                project_id="p1",
            )
        )

    fake_repo.index.commit.assert_called()
    fake_repo.remotes.origin.push.assert_not_called()
    assert "Updated section 'Intro'" in result
    assert "and pushed" not in result


# ---------------------------------------------------------------------------
# tools.py — sync_project edge cases (832-833, 847-850)
# ---------------------------------------------------------------------------


def test_sync_project_clones_when_repo_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """sync_project on a never-cloned project goes through the cold-start
    branch (lines 832-833) and reports 'Cloned project'."""
    from overleaf_mcp.tools import sync_project

    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    # Note: don't pre-create the project dir — that's the trigger

    fake_clone = MagicMock()
    from configparser import NoSectionError

    fake_clone.config_reader.return_value.get_value.side_effect = NoSectionError("user")
    with patch("overleaf_mcp.git_ops.Repo.clone_from", return_value=fake_clone):
        result = asyncio.run(sync_project(git_token="t", project_id="newp"))
    assert "Cloned project" in result


def test_sync_project_reports_stale_warning_as_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """sync_project's contract is hard-error reporting (no silent fallback).
    StaleRepoWarning from ensure_repo must surface as 'Error syncing' (847-848)."""
    from overleaf_mcp.tools import sync_project

    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    monkeypatch.setattr(git_ops, "_RETRY_DELAY_RANGE", (0.0, 0.0))
    (tmp_path / "p1").mkdir()

    fake_repo = MagicMock()
    fake_repo.is_dirty.return_value = False
    fake_repo.config_reader.return_value.get_value.return_value = "stamped"
    fake_repo.remotes.origin.url = ""
    fake_repo.remotes.origin.pull.side_effect = GitCommandError(
        "pull", 128, b"", b"fatal: Authentication failed",
    )

    # sync_project does Repo(repo_path) inline (not via ensure_repo) for the
    # is_dirty check, so patch BOTH the git_ops import and the tools import.
    with (
        patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo),
        patch("overleaf_mcp.tools.Repo", return_value=fake_repo),
    ):
        result = asyncio.run(sync_project(git_token="t", project_id="p1"))

    assert "Error syncing" in result
    assert "Authentication failed" in result


def test_sync_project_warns_on_dirty_working_tree_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The dirty-tree branch returns its specific warning, not an error."""
    from overleaf_mcp.tools import sync_project

    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    (tmp_path / "p1").mkdir()

    fake_repo = MagicMock()
    fake_repo.is_dirty.return_value = True

    # Same dual-patch as above — sync_project's is_dirty path uses
    # tools.Repo directly.
    with (
        patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo),
        patch("overleaf_mcp.tools.Repo", return_value=fake_repo),
    ):
        result = asyncio.run(sync_project(git_token="t", project_id="p1"))

    assert "Local changes exist" in result
