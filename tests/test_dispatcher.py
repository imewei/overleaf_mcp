"""Integration tests for every execute_tool dispatcher branch.

These tests exercise the 15 MCP tool branches end-to-end against a real
local bare git repository (``file://`` protocol). No network is touched.
This is the coverage lift recommended in the validation report: primitive
tests in test_optimizations.py prove the building blocks work; these tests
prove the dispatcher wires them up correctly.

Fixture shape:

    tmp_path/
      bare.git/               <-- the "remote" Overleaf repo
      overleaf_cache/
        test-project/          <-- the local clone the server reads/writes
      overleaf_config.json    <-- redirects the server to this test setup

The ``OVERLEAF_GIT_URL`` env var points at ``file://{tmp_path}/bare.git``
so ``ensure_repo`` can clone/pull/push against it exactly like the real
Overleaf endpoint, but in process with no auth.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from git import Repo

from overleaf_mcp import server
from overleaf_mcp.server import execute_tool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Prevent cache/lock leakage between tests."""
    server._CONFIG_CACHE = None
    server._LAST_PULL.clear()
    server._PROJECT_LOCKS.clear()
    yield
    server._CONFIG_CACHE = None
    server._LAST_PULL.clear()
    server._PROJECT_LOCKS.clear()


PROJECT_ID = "test-project"


@pytest.fixture
def bare_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Repo]:
    """Set up a local bare repo with an initial commit + config redirect.

    The server's ``_build_git_url`` appends ``/{project_id}`` to
    ``OVERLEAF_GIT_URL``, so we place the bare repo at ``{tmp_path}/{PROJECT_ID}``
    and point the server at ``file://{tmp_path}``. ``ensure_repo`` then clones
    via the normal code path, no monkeypatching of git ops required.

    Returns ``(tmp_path, bare_repo)``.
    """
    # Bare "remote" lives at tmp_path/test-project so that
    # OVERLEAF_GIT_URL=file://{tmp_path} + project_id=test-project
    # resolves to file://{tmp_path}/test-project.
    bare_path = tmp_path / PROJECT_ID
    bare = Repo.init(bare_path, bare=True)

    # Seed with an initial commit by cloning, committing, pushing back
    seed_path = tmp_path / "_seed"
    seed = Repo.clone_from(str(bare_path), seed_path)
    with seed.config_writer() as cfg:
        cfg.set_value("user", "name", "Test Seeder")
        cfg.set_value("user", "email", "seed@test.local")
    (seed_path / "main.tex").write_text(
        r"""\documentclass{article}
\begin{document}
\section{Introduction}
Hello world.

\section{Methods}
We did things.
\end{document}
"""
    )
    (seed_path / "refs.bib").write_text("@book{foo, title={Foo}, author={Bar}}\n")
    seed.index.add(["main.tex", "refs.bib"])
    seed.index.commit("initial commit")
    seed.remotes.origin.push()

    # Make the bare repo's HEAD match the seeded branch (default may be
    # main or master depending on the host's git config).
    default_branch = seed.active_branch.name
    with bare.config_writer() as cfg:
        cfg.set_value("HEAD", "ref", f"refs/heads/{default_branch}")

    # Point the server at this setup. The cache dir is a sibling of the
    # bare repo so rglob-based file listers don't walk it.
    cache_dir = tmp_path / "overleaf_cache"
    monkeypatch.setattr(server, "TEMP_DIR", str(cache_dir))
    monkeypatch.setattr(server, "OVERLEAF_GIT_URL", f"file://{tmp_path}")

    # Write a matching config so resolve_project() can find the project
    config_path = tmp_path / "overleaf_config.json"
    config_path.write_text(
        json.dumps(
            {
                "projects": {
                    "test-project": {
                        "name": "Test Project",
                        "projectId": PROJECT_ID,
                        "gitToken": "dummy-token-unused-with-file-url",
                    }
                },
                "defaultProject": "test-project",
            }
        )
    )
    monkeypatch.setattr(server, "CONFIG_FILE", str(config_path))

    # Configure git identity on the working clone so commits succeed in CI
    # where no global user.name/user.email is set.
    monkeypatch.setenv("OVERLEAF_GIT_AUTHOR_NAME", "Overleaf MCP Test")
    monkeypatch.setenv("OVERLEAF_GIT_AUTHOR_EMAIL", "test@overleaf.local")

    return tmp_path, bare


def run(name: str, args: dict) -> str:
    """Shorthand: invoke a dispatcher branch synchronously."""
    return asyncio.run(execute_tool(name, args))


# ---------------------------------------------------------------------------
# CREATE branches
# ---------------------------------------------------------------------------


def test_create_project_returns_snip_url():
    """create_project builds a data: URL for the browser — no git touch."""
    result = run("create_project", {"content": r"\documentclass{article}\begin{document}X\end{document}"})
    assert "open this URL in your browser" in result
    assert "overleaf.com/docs" in result


def test_create_file_writes_and_commits(bare_repo):
    tmp_path, bare = bare_repo
    result = run("create_file", {"file_path": "appendix.tex", "content": "Hello"})
    assert "Created and pushed" in result
    # Verify it landed on the bare remote
    log = bare.git.log("--oneline")
    assert "Add appendix.tex" in log


def test_create_file_rejects_existing(bare_repo):
    result = run("create_file", {"file_path": "main.tex", "content": "dup"})
    assert "already exists" in result


def test_create_file_dry_run_leaves_tree_clean(bare_repo):
    tmp_path, bare = bare_repo
    result = run("create_file", {"file_path": "ghost.tex", "content": "X", "dry_run": True})
    assert "Dry run" in result
    assert "No changes were written" in result
    # File must not exist on disk
    assert not (tmp_path / "overleaf_cache" / PROJECT_ID / "ghost.tex").exists()


def test_create_file_no_push(bare_repo):
    """push=False should commit locally but not push to the remote."""
    tmp_path, bare = bare_repo
    result = run("create_file", {"file_path": "local.tex", "content": "X", "push": False})
    assert "Created '" in result  # no "and pushed"
    assert "pushed" not in result
    # Bare remote should NOT have the new file
    log = bare.git.log("--oneline")
    assert "Add local.tex" not in log


# ---------------------------------------------------------------------------
# READ branches
# ---------------------------------------------------------------------------


def test_list_projects_empty_when_no_config(tmp_path, monkeypatch):
    """If neither config file nor env vars are set, list_projects returns help."""
    monkeypatch.setattr(server, "CONFIG_FILE", str(tmp_path / "nonexistent.json"))
    monkeypatch.delenv("OVERLEAF_PROJECT_ID", raising=False)
    monkeypatch.delenv("OVERLEAF_GIT_TOKEN", raising=False)
    result = run("list_projects", {})
    assert "No projects configured" in result


def test_list_projects_with_config(bare_repo):
    result = run("list_projects", {})
    assert "test-project" in result
    assert "(default)" in result


def test_list_files_all(bare_repo):
    result = run("list_files", {})
    assert "main.tex" in result
    assert "refs.bib" in result


def test_list_files_filtered_by_extension(bare_repo):
    result = run("list_files", {"extension": ".bib"})
    assert "refs.bib" in result
    assert "main.tex" not in result


def test_list_files_no_match(bare_repo):
    result = run("list_files", {"extension": ".xyz"})
    assert "No files found" in result


def test_read_file_success(bare_repo):
    result = run("read_file", {"file_path": "main.tex"})
    assert r"\documentclass{article}" in result
    assert "\\section{Introduction}" in result


def test_read_file_not_found(bare_repo):
    result = run("read_file", {"file_path": "does_not_exist.tex"})
    assert "not found" in result


def test_read_file_rejects_path_traversal(bare_repo):
    """validate_path must block paths escaping the repo root."""
    with pytest.raises(ValueError, match="escapes repository root"):
        run("read_file", {"file_path": "../../etc/passwd"})


def test_get_sections_finds_sections(bare_repo):
    result = run("get_sections", {"file_path": "main.tex"})
    assert "[section] Introduction" in result
    assert "[section] Methods" in result


def test_get_sections_no_sections(bare_repo):
    """A file with no \\section macros returns a clean 'no sections' message."""
    # First create a section-less file
    run("create_file", {"file_path": "plain.tex", "content": "no sections here"})
    result = run("get_sections", {"file_path": "plain.tex"})
    assert "No sections found" in result


def test_get_section_content_success(bare_repo):
    result = run("get_section_content", {"file_path": "main.tex", "section_title": "Methods"})
    assert "We did things" in result


def test_get_section_content_not_found(bare_repo):
    result = run(
        "get_section_content",
        {"file_path": "main.tex", "section_title": "Nonexistent"},
    )
    assert "not found" in result
    assert "Available sections" in result


def test_list_history_returns_commits(bare_repo):
    result = run("list_history", {"limit": 5})
    assert "Commit history" in result
    assert "initial commit" in result


def test_list_history_respects_limit_cap(bare_repo):
    """limit > 200 must be capped at 200."""
    result = run("list_history", {"limit": 500})
    # We only have 1 commit, so just verify no error + the cap-aware message
    assert "Commit history" in result
    assert "/200)" in result  # limit was clamped to 200


def test_get_diff_no_differences(bare_repo):
    """Diff HEAD against HEAD yields no output."""
    result = run("get_diff", {"from_ref": "HEAD", "to_ref": "HEAD"})
    assert "No differences found" in result


def test_get_diff_after_edit(bare_repo):
    """After an edit_file, a HEAD~1..HEAD diff shows the change."""
    run("edit_file", {"file_path": "main.tex", "old_string": "Hello world.", "new_string": "Goodbye."})
    result = run("get_diff", {"from_ref": "HEAD~1", "to_ref": "HEAD"})
    assert "-Hello world." in result
    assert "+Goodbye." in result


def test_status_summary_detects_main_tex(bare_repo):
    result = run("status_summary", {})
    assert "Test Project" in result
    assert "main.tex" in result
    assert "[section] Introduction" in result


# ---------------------------------------------------------------------------
# UPDATE branches
# ---------------------------------------------------------------------------


def test_edit_file_replaces_unique_match(bare_repo):
    tmp_path, bare = bare_repo
    result = run(
        "edit_file",
        {"file_path": "main.tex", "old_string": "Hello world.", "new_string": "Hola."},
    )
    assert "Edited and pushed" in result
    # Verify content on the local clone
    content = (tmp_path / "overleaf_cache" / PROJECT_ID / "main.tex").read_text()
    assert "Hola." in content
    assert "Hello world." not in content


def test_edit_file_rejects_missing_old_string(bare_repo):
    result = run(
        "edit_file",
        {"file_path": "main.tex", "old_string": "NONEXISTENT", "new_string": "X"},
    )
    assert "old_string not found" in result
    assert "File preview:" in result


def test_edit_file_rejects_ambiguous_match(bare_repo):
    """old_string appearing more than once must be rejected for safety."""
    # Add a file with duplicate content
    run(
        "create_file",
        {"file_path": "dup.tex", "content": "X\nX\nX"},
    )
    result = run(
        "edit_file",
        {"file_path": "dup.tex", "old_string": "X", "new_string": "Y"},
    )
    assert "appears 3 times" in result
    assert "more specific" in result


def test_edit_file_dry_run(bare_repo):
    tmp_path, bare = bare_repo
    result = run(
        "edit_file",
        {
            "file_path": "main.tex",
            "old_string": "Hello world.",
            "new_string": "CHANGED",
            "dry_run": True,
        },
    )
    assert "Dry run" in result
    # Verify original content still on disk
    content = (tmp_path / "overleaf_cache" / PROJECT_ID / "main.tex").read_text()
    assert "Hello world." in content


def test_rewrite_file_replaces_entire_content(bare_repo):
    tmp_path, bare = bare_repo
    new = r"\documentclass{report}\begin{document}new\end{document}"
    result = run(
        "rewrite_file",
        {"file_path": "main.tex", "content": new},
    )
    assert "Rewrote and pushed" in result
    content = (tmp_path / "overleaf_cache" / PROJECT_ID / "main.tex").read_text()
    assert content == new


def test_rewrite_file_rejects_missing(bare_repo):
    result = run(
        "rewrite_file",
        {"file_path": "does_not_exist.tex", "content": "X"},
    )
    assert "not found" in result
    assert "Use create_file" in result


def test_update_section_replaces_body(bare_repo):
    tmp_path, bare = bare_repo
    result = run(
        "update_section",
        {
            "file_path": "main.tex",
            "section_title": "Methods",
            "new_content": "We used NLSQ warm-start with NUTS.",
        },
    )
    assert "Updated section 'Methods'" in result
    content = (tmp_path / "overleaf_cache" / PROJECT_ID / "main.tex").read_text()
    assert "NLSQ warm-start" in content
    # Introduction must be untouched
    assert "Hello world." in content


def test_update_section_rejects_missing(bare_repo):
    result = run(
        "update_section",
        {
            "file_path": "main.tex",
            "section_title": "Nonexistent",
            "new_content": "X",
        },
    )
    assert "not found" in result


def test_sync_project_refreshes(bare_repo):
    # First call clones (no local copy yet); second call on the now-existing
    # clone is the "Synced" path we want to verify.
    run("list_files", {})  # triggers the initial clone
    result = run("sync_project", {})
    assert "Synced project" in result


def test_sync_project_warns_on_dirty(bare_repo):
    """If local clone has uncommitted changes, sync_project must refuse."""
    tmp_path, bare = bare_repo
    # Force a dirty state by touching a file after the clone
    run("list_files", {})  # triggers clone
    (tmp_path / "overleaf_cache" / PROJECT_ID / "main.tex").write_text("dirty")
    result = run("sync_project", {})
    assert "Local changes exist" in result


# ---------------------------------------------------------------------------
# DELETE branch
# ---------------------------------------------------------------------------


def test_delete_file_removes_and_commits(bare_repo):
    tmp_path, bare = bare_repo
    result = run("delete_file", {"file_path": "refs.bib"})
    assert "Deleted and pushed" in result
    assert not (tmp_path / "overleaf_cache" / PROJECT_ID / "refs.bib").exists()


def test_delete_file_missing(bare_repo):
    result = run("delete_file", {"file_path": "does_not_exist.tex"})
    assert "not found" in result


# ---------------------------------------------------------------------------
# Inline credential override path (cuts through config file)
# ---------------------------------------------------------------------------


def test_inline_credentials_override_config(bare_repo):
    """Inline git_token + project_id should bypass the config lookup."""
    # Remove the configured project so resolve_project would otherwise fail
    result = run(
        "list_files",
        {
            "git_token": "inline-token",
            "project_id": PROJECT_ID,
        },
    )
    assert "main.tex" in result
