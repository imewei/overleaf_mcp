"""Regression tests for the performance/stability optimizations.

Covers:
  * load_config() mtime-based memoization
  * ensure_repo() TTL-based pull caching + force_pull bypass
  * StaleRepoWarning fallback via acquire_project()
  * ToolContext.wrap() output composition
  * _run_blocking timeout ceiling
  * Per-project asyncio.Lock serialization (concurrency race fix)

No real network or git remote is touched. Git interactions are replaced
with in-memory mocks so tests run in milliseconds on any host.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from overleaf_mcp import server
from overleaf_mcp.server import (
    Config,
    ProjectConfig,
    StaleRepoWarning,
    ToolContext,
    _lock_for,
    _run_blocking,
    acquire_project,
    ensure_repo,
    load_config,
)
from git import GitCommandError


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_module_caches():
    """Clear module-level state before every test (test isolation)."""
    server._CONFIG_CACHE = None
    server._LAST_PULL.clear()
    server._PROJECT_LOCKS.clear()
    yield
    server._CONFIG_CACHE = None
    server._LAST_PULL.clear()
    server._PROJECT_LOCKS.clear()


@pytest.fixture
def tmp_config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect CONFIG_FILE to a test-owned path under tmp_path."""
    path = tmp_path / "overleaf_config.json"
    monkeypatch.setattr(server, "CONFIG_FILE", str(path))
    return path


@pytest.fixture
def fake_project() -> ProjectConfig:
    return ProjectConfig(name="test", project_id="p123", git_token="tok")


def _make_fake_repo(origin_url: str = "") -> MagicMock:
    """A MagicMock shaped like a git.Repo with a settable origin URL."""
    repo = MagicMock()
    repo.remotes.origin.url = origin_url or ""
    repo.remotes.origin.pull = MagicMock()
    repo.remotes.origin.set_url = MagicMock(
        side_effect=lambda new: setattr(repo.remotes.origin, "url", new)
    )
    return repo


def _write_config(path: Path, default_name: str = "alpha") -> None:
    path.write_text(
        json.dumps(
            {
                "projects": {
                    "alpha": {
                        "name": "Alpha",
                        "projectId": "pA",
                        "gitToken": "tA",
                    }
                },
                "defaultProject": default_name,
            }
        )
    )


# ---------------------------------------------------------------------------
# load_config mtime memoization
# ---------------------------------------------------------------------------


def test_load_config_memoizes_unchanged_file(tmp_config_file: Path):
    """Parsing the same file twice should hit the cache, not re-invoke the parser."""
    _write_config(tmp_config_file)

    with patch(
        "overleaf_mcp.server._parse_config_file",
        wraps=server._parse_config_file,
    ) as spy:
        load_config()
        load_config()
        assert spy.call_count == 1


def test_load_config_reparses_on_mtime_change(tmp_config_file: Path):
    """A file mtime change must invalidate the cache."""
    _write_config(tmp_config_file)
    cfg1 = load_config()

    time.sleep(0.01)
    _write_config(tmp_config_file)
    # Force a distinct mtime (avoid filesystems with 1s resolution ambiguity)
    import os as _os
    stat = tmp_config_file.stat()
    _os.utime(tmp_config_file, (stat.st_atime, stat.st_mtime + 1.0))

    cfg2 = load_config()
    assert cfg1 is not cfg2
    assert isinstance(cfg2, Config)


# ---------------------------------------------------------------------------
# ensure_repo TTL behavior
# ---------------------------------------------------------------------------


def test_ensure_repo_skips_pull_within_ttl(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Second call within TTL window must NOT trigger a second pull."""
    monkeypatch.setattr(server, "TEMP_DIR", str(tmp_path))
    monkeypatch.setenv("OVERLEAF_PULL_TTL", "10")
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()
    with patch("overleaf_mcp.server.Repo", return_value=fake_repo):
        ensure_repo(fake_project)
        ensure_repo(fake_project)

    # Count-based: directly asserts the invariant "TTL suppresses network I/O"
    # without depending on wall-clock speed (flaky on slow CI).
    assert fake_repo.remotes.origin.pull.call_count == 1


def test_ensure_repo_force_pull_bypasses_ttl(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """force_pull=True must pull even when the TTL cache would suppress it."""
    monkeypatch.setattr(server, "TEMP_DIR", str(tmp_path))
    monkeypatch.setenv("OVERLEAF_PULL_TTL", "10")
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()
    with patch("overleaf_mcp.server.Repo", return_value=fake_repo):
        ensure_repo(fake_project, force_pull=False)
        ensure_repo(fake_project, force_pull=True)

    assert fake_repo.remotes.origin.pull.call_count == 2


def test_ensure_repo_pulls_after_ttl_expiry(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Expired TTL must re-trigger the pull."""
    monkeypatch.setattr(server, "TEMP_DIR", str(tmp_path))
    monkeypatch.setenv("OVERLEAF_PULL_TTL", "5")
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()
    with patch("overleaf_mcp.server.Repo", return_value=fake_repo):
        ensure_repo(fake_project)
        server._LAST_PULL[fake_project.project_id] -= 10.0
        ensure_repo(fake_project)

    assert fake_repo.remotes.origin.pull.call_count == 2


def test_ensure_repo_raises_stale_on_pull_failure(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """GitCommandError on pull must surface as StaleRepoWarning."""
    monkeypatch.setattr(server, "TEMP_DIR", str(tmp_path))
    monkeypatch.setenv("OVERLEAF_PULL_TTL", "0")
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()
    fake_repo.remotes.origin.pull.side_effect = GitCommandError(
        "pull", 1, b"", b"boom"
    )
    with (
        patch("overleaf_mcp.server.Repo", return_value=fake_repo),
        pytest.raises(StaleRepoWarning, match="boom"),
    ):
        ensure_repo(fake_project)


# ---------------------------------------------------------------------------
# acquire_project context manager
# ---------------------------------------------------------------------------


def test_acquire_project_yields_tool_context(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Happy path yields a ToolContext with no warnings."""
    monkeypatch.setattr(server, "TEMP_DIR", str(tmp_path))
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()

    async def _run():
        with patch("overleaf_mcp.server.ensure_repo", return_value=fake_repo):
            async with acquire_project(fake_project) as ctx:
                assert isinstance(ctx, ToolContext)
                assert ctx.repo is fake_repo
                assert ctx.warnings == []

    asyncio.run(_run())


def test_acquire_project_falls_back_on_stale(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """On StaleRepoWarning, context yields local repo + warning in ctx.warnings."""
    monkeypatch.setattr(server, "TEMP_DIR", str(tmp_path))
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()

    def _raise_stale(_project, *, force_pull=False):
        raise StaleRepoWarning("network unreachable")

    async def _run():
        with patch("overleaf_mcp.server.ensure_repo", side_effect=_raise_stale), \
             patch("overleaf_mcp.server.Repo", return_value=fake_repo):
            async with acquire_project(fake_project) as ctx:
                assert ctx.repo is fake_repo
                assert len(ctx.warnings) == 1
                assert "could not refresh" in ctx.warnings[0]
                assert "network unreachable" in ctx.warnings[0]

    asyncio.run(_run())


def test_acquire_project_releases_lock_on_exception(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Lock must be released even if the body raises (context-manager invariant)."""
    monkeypatch.setattr(server, "TEMP_DIR", str(tmp_path))
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()

    async def _run():
        lock = _lock_for(fake_project.project_id)
        with (
            patch("overleaf_mcp.server.ensure_repo", return_value=fake_repo),
            pytest.raises(RuntimeError, match="boom"),
        ):
            async with acquire_project(fake_project):
                raise RuntimeError("boom")
        # Lock must be releasable (re-acquirable) after the failed block.
        assert not lock.locked()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# ToolContext.wrap output composition
# ---------------------------------------------------------------------------


def test_toolcontext_wrap_noop_on_empty_warnings():
    ctx = ToolContext(repo=MagicMock(), warnings=[])
    assert ctx.wrap("hello") == "hello"


def test_toolcontext_wrap_appends_block():
    ctx = ToolContext(repo=MagicMock(), warnings=["⚠ one", "⚠ two"])
    assert ctx.wrap("hello") == "hello\n\n⚠ one\n⚠ two"


# ---------------------------------------------------------------------------
# Per-project lock serialization (concurrency race fix)
# ---------------------------------------------------------------------------


def test_acquire_project_serializes_same_project(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Two concurrent acquire_project() calls on the same project must not
    execute the critical section concurrently.

    This is the regression test for the race identified in code review:
    without the per-project lock, two parallel writers could both pass the
    TTL check and race on GitPython's index.
    """
    monkeypatch.setattr(server, "TEMP_DIR", str(tmp_path))
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()
    max_concurrent = 0
    current = 0

    async def body(tag: str):
        nonlocal max_concurrent, current
        async with acquire_project(fake_project, force_pull=True):
            current += 1
            max_concurrent = max(max_concurrent, current)
            # Hold the critical section long enough that a racing caller
            # would overlap if no lock were held.
            await asyncio.sleep(0.02)
            current -= 1
            return tag

    async def _run():
        with patch("overleaf_mcp.server.ensure_repo", return_value=fake_repo):
            results = await asyncio.gather(body("a"), body("b"), body("c"))
        assert sorted(results) == ["a", "b", "c"]
        # Critical invariant: the lock prevented parallel entry.
        assert max_concurrent == 1, (
            f"Critical section entered concurrently (max={max_concurrent}); "
            "per-project lock is not serializing"
        )

    asyncio.run(_run())


def test_acquire_project_independent_across_projects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Locks are per-project, so different projects must not serialize."""
    monkeypatch.setattr(server, "TEMP_DIR", str(tmp_path))

    proj_a = ProjectConfig(name="a", project_id="pA", git_token="t")
    proj_b = ProjectConfig(name="b", project_id="pB", git_token="t")
    (tmp_path / "pA").mkdir()
    (tmp_path / "pB").mkdir()

    fake_repo = _make_fake_repo()
    overlap = False

    a_entered = asyncio.Event()
    b_entered = asyncio.Event()

    async def hold_a():
        async with acquire_project(proj_a, force_pull=True):
            a_entered.set()
            # Wait for B to enter — proves they can run concurrently.
            await asyncio.wait_for(b_entered.wait(), timeout=1.0)

    async def hold_b():
        await a_entered.wait()
        async with acquire_project(proj_b, force_pull=True):
            nonlocal overlap
            overlap = True
            b_entered.set()

    async def _run():
        with patch("overleaf_mcp.server.ensure_repo", return_value=fake_repo):
            await asyncio.gather(hold_a(), hold_b())
        assert overlap, "Different projects should not serialize on one lock"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# _run_blocking timeout ceiling
# ---------------------------------------------------------------------------


def test_run_blocking_enforces_timeout(monkeypatch: pytest.MonkeyPatch):
    """A blocking op that exceeds OVERLEAF_GIT_TIMEOUT must raise TimeoutError."""
    monkeypatch.setenv("OVERLEAF_GIT_TIMEOUT", "0.05")

    def sleeper():
        time.sleep(0.5)
        return "done"

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(_run_blocking(sleeper))


def test_run_blocking_returns_value_under_timeout(monkeypatch: pytest.MonkeyPatch):
    """Fast ops return their value normally."""
    monkeypatch.setenv("OVERLEAF_GIT_TIMEOUT", "5")
    result = asyncio.run(_run_blocking(lambda: 42))
    assert result == 42
