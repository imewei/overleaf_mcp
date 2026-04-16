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

from overleaf_mcp import config as config_mod
from overleaf_mcp import git_ops
from overleaf_mcp.config import (
    Config,
    ProjectConfig,
    load_config,
)
from overleaf_mcp.git_ops import (
    StaleRepoWarning,
    ToolContext,
    _lock_for,
    _run_blocking,
    acquire_project,
    ensure_repo,
)
from git import GitCommandError


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_module_caches():
    """Clear module-level state before every test (test isolation)."""
    config_mod._CONFIG_CACHE = None
    git_ops._LAST_PULL.clear()
    git_ops._PROJECT_RWLOCKS.clear()
    yield
    config_mod._CONFIG_CACHE = None
    git_ops._LAST_PULL.clear()
    git_ops._PROJECT_RWLOCKS.clear()


@pytest.fixture
def tmp_config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect CONFIG_FILE to a test-owned path under tmp_path."""
    path = tmp_path / "overleaf_config.json"
    monkeypatch.setattr(config_mod, "CONFIG_FILE", str(path))
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
        "overleaf_mcp.config._parse_config_file",
        wraps=config_mod._parse_config_file,
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
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    monkeypatch.setenv("OVERLEAF_PULL_TTL", "10")
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()
    with patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
        ensure_repo(fake_project)
        ensure_repo(fake_project)

    # Count-based: directly asserts the invariant "TTL suppresses network I/O"
    # without depending on wall-clock speed (flaky on slow CI).
    assert fake_repo.remotes.origin.pull.call_count == 1


def test_ensure_repo_force_pull_bypasses_ttl(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """force_pull=True must pull even when the TTL cache would suppress it."""
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    monkeypatch.setenv("OVERLEAF_PULL_TTL", "10")
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()
    with patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
        ensure_repo(fake_project, force_pull=False)
        ensure_repo(fake_project, force_pull=True)

    assert fake_repo.remotes.origin.pull.call_count == 2


def test_ensure_repo_pulls_after_ttl_expiry(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Expired TTL must re-trigger the pull."""
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    monkeypatch.setenv("OVERLEAF_PULL_TTL", "5")
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()
    with patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
        ensure_repo(fake_project)
        git_ops._LAST_PULL[fake_project.project_id] -= 10.0
        ensure_repo(fake_project)

    assert fake_repo.remotes.origin.pull.call_count == 2


def test_shallow_clone_kwargs_off_by_default(monkeypatch: pytest.MonkeyPatch):
    """Without OVERLEAF_SHALLOW_CLONE=1, clone kwargs are empty (full clone)."""
    monkeypatch.delenv("OVERLEAF_SHALLOW_CLONE", raising=False)
    assert git_ops._shallow_clone_kwargs() == {}


def test_shallow_clone_kwargs_honors_depth(monkeypatch: pytest.MonkeyPatch):
    """OVERLEAF_SHALLOW_CLONE=1 enables shallow with configurable depth."""
    monkeypatch.setenv("OVERLEAF_SHALLOW_CLONE", "1")
    monkeypatch.setenv("OVERLEAF_SHALLOW_DEPTH", "5")
    assert git_ops._shallow_clone_kwargs() == {"depth": 5}


def test_shallow_clone_kwargs_defaults_to_depth_1(monkeypatch: pytest.MonkeyPatch):
    """Enabled but no depth set — defaults to 1."""
    monkeypatch.setenv("OVERLEAF_SHALLOW_CLONE", "1")
    monkeypatch.delenv("OVERLEAF_SHALLOW_DEPTH", raising=False)
    assert git_ops._shallow_clone_kwargs() == {"depth": 1}


def test_shallow_clone_kwargs_clamps_to_minimum_1(monkeypatch: pytest.MonkeyPatch):
    """Nonsense negative depth is clamped to 1 — `git clone --depth=0` is meaningless."""
    monkeypatch.setenv("OVERLEAF_SHALLOW_CLONE", "1")
    monkeypatch.setenv("OVERLEAF_SHALLOW_DEPTH", "-10")
    assert git_ops._shallow_clone_kwargs() == {"depth": 1}


def test_ensure_repo_raises_stale_on_pull_failure(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """GitCommandError on pull must surface as StaleRepoWarning."""
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    monkeypatch.setenv("OVERLEAF_PULL_TTL", "0")
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()
    fake_repo.remotes.origin.pull.side_effect = GitCommandError(
        "pull", 1, b"", b"boom"
    )
    with (
        patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo),
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
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()

    async def _run():
        with patch("overleaf_mcp.git_ops.ensure_repo", return_value=fake_repo):
            async with acquire_project(fake_project) as ctx:
                assert isinstance(ctx, ToolContext)
                assert ctx.repo is fake_repo
                assert ctx.warnings == []

    asyncio.run(_run())


def test_acquire_project_falls_back_on_stale(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """On StaleRepoWarning, context yields local repo + warning in ctx.warnings."""
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()

    def _raise_stale(_project, *, force_pull=False):
        raise StaleRepoWarning("network unreachable")

    async def _run():
        with patch("overleaf_mcp.git_ops.ensure_repo", side_effect=_raise_stale), \
             patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
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
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()

    async def _run():
        rwlock = _lock_for(fake_project.project_id)
        with (
            patch("overleaf_mcp.git_ops.ensure_repo", return_value=fake_repo),
            pytest.raises(RuntimeError, match="boom"),
        ):
            async with acquire_project(fake_project):
                raise RuntimeError("boom")
        # The proof of "lock released" is that we can re-acquire exclusive
        # without blocking. If the writer/reader counters leaked, this would
        # deadlock and pytest would time out.
        async with rwlock.exclusive():
            pass
        # Direct invariant assertions on the released state.
        assert rwlock._readers == 0
        assert rwlock._writer is False

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


def test_toolcontext_wrap_envelope_off_by_default(monkeypatch: pytest.MonkeyPatch):
    """Without OVERLEAF_STRUCTURED=1, no envelope is appended (back-compat)."""
    monkeypatch.delenv("OVERLEAF_STRUCTURED", raising=False)
    ctx = ToolContext(repo=MagicMock(), warnings=[])
    assert ctx.wrap("hello") == "hello"
    assert "<mcp-envelope>" not in ctx.wrap("hello")


def test_toolcontext_wrap_envelope_ok_true_on_success(monkeypatch: pytest.MonkeyPatch):
    """OVERLEAF_STRUCTURED=1 appends JSON envelope; ok=true for clean response."""
    monkeypatch.setenv("OVERLEAF_STRUCTURED", "1")
    ctx = ToolContext(repo=MagicMock(), warnings=[])
    result = ctx.wrap("Edited and pushed 'main.tex'")
    assert result.startswith("Edited and pushed 'main.tex'")  # human text preserved
    assert "<mcp-envelope>" in result
    # Parse the envelope to verify its contents
    import re as _re
    import json as _json
    match = _re.search(r"<mcp-envelope>(.*?)</mcp-envelope>", result)
    assert match is not None
    envelope = _json.loads(match.group(1))
    assert envelope == {"ok": True, "warnings": []}


def test_toolcontext_wrap_envelope_ok_false_with_warnings(monkeypatch: pytest.MonkeyPatch):
    """ok=false when warnings are present (stale-repo fallback)."""
    monkeypatch.setenv("OVERLEAF_STRUCTURED", "1")
    ctx = ToolContext(repo=MagicMock(), warnings=["⚠ could not refresh: boom"])
    result = ctx.wrap("Content of 'main.tex'")
    import re as _re
    import json as _json
    envelope = _json.loads(
        _re.search(r"<mcp-envelope>(.*?)</mcp-envelope>", result).group(1)
    )
    assert envelope["ok"] is False
    assert envelope["warnings"] == ["⚠ could not refresh: boom"]


def test_toolcontext_wrap_envelope_ok_false_on_error_prefix(monkeypatch: pytest.MonkeyPatch):
    """ok=false when the response begins with 'Error:'."""
    monkeypatch.setenv("OVERLEAF_STRUCTURED", "1")
    ctx = ToolContext(repo=MagicMock(), warnings=[])
    result = ctx.wrap("Error: File 'x.tex' not found")
    import re as _re
    import json as _json
    envelope = _json.loads(
        _re.search(r"<mcp-envelope>(.*?)</mcp-envelope>", result).group(1)
    )
    assert envelope["ok"] is False


# ---------------------------------------------------------------------------
# Per-project lock serialization (concurrency race fix)
# ---------------------------------------------------------------------------


def test_acquire_project_serializes_same_project(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Two concurrent write-mode acquisitions on the same project must not
    execute the critical section concurrently.

    This is the regression test for the race identified in code review:
    without the per-project lock, two parallel writers could both pass the
    TTL check and race on GitPython's index. v2 still serializes writers;
    only readers (mode="read") run concurrently — see the dedicated
    reader-concurrency tests at the bottom of this file.
    """
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()
    max_concurrent = 0
    current = 0

    async def body(tag: str):
        nonlocal max_concurrent, current
        async with acquire_project(fake_project, force_pull=True, mode="write"):
            current += 1
            max_concurrent = max(max_concurrent, current)
            # Hold the critical section long enough that a racing caller
            # would overlap if no lock were held.
            await asyncio.sleep(0.02)
            current -= 1
            return tag

    async def _run():
        with patch("overleaf_mcp.git_ops.ensure_repo", return_value=fake_repo):
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
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))

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
        with patch("overleaf_mcp.git_ops.ensure_repo", return_value=fake_repo):
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


# ---------------------------------------------------------------------------
# Reader-writer lock — concurrent readers + writer-priority exclusion
# ---------------------------------------------------------------------------


def test_acquire_project_allows_concurrent_readers(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Two read-mode acquisitions on the same project MUST run concurrently.

    This is the v2 perf optimization — read-only tools should no longer
    queue behind each other on the same project's lock. If max_concurrent
    is still 1, the RW split has not actually loosened read concurrency.
    """
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()
    max_concurrent = 0
    current = 0

    async def reader(tag: str):
        nonlocal max_concurrent, current
        async with acquire_project(fake_project, mode="read"):
            current += 1
            max_concurrent = max(max_concurrent, current)
            await asyncio.sleep(0.02)
            current -= 1
            return tag

    async def _run():
        with patch("overleaf_mcp.git_ops.ensure_repo", return_value=fake_repo):
            await asyncio.gather(reader("a"), reader("b"), reader("c"))
        assert max_concurrent >= 2, (
            f"Readers serialized (max={max_concurrent}); RW lock did not "
            "loosen the read path"
        )

    asyncio.run(_run())


def test_acquire_project_writer_excludes_readers(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A write acquisition MUST exclude all readers for its duration."""
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()
    in_writer = False
    overlap_with_writer = False

    async def writer():
        nonlocal in_writer
        async with acquire_project(fake_project, mode="write", force_pull=True):
            in_writer = True
            await asyncio.sleep(0.05)
            in_writer = False

    async def reader():
        nonlocal overlap_with_writer
        async with acquire_project(fake_project, mode="read"):
            if in_writer:
                overlap_with_writer = True

    async def _run():
        with patch("overleaf_mcp.git_ops.ensure_repo", return_value=fake_repo):
            # Start writer first, then a few readers that would overlap
            # if exclusion is not honored.
            w = asyncio.create_task(writer())
            await asyncio.sleep(0.005)  # let writer enter
            await asyncio.gather(reader(), reader(), reader(), w)
        assert not overlap_with_writer, (
            "Reader entered while writer held the lock — write exclusion broken"
        )

    asyncio.run(_run())


def test_acquire_project_writers_still_serialize(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Two writers on the same project MUST still serialize (v1 invariant)."""
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()
    max_concurrent = 0
    current = 0

    async def writer(tag: str):
        nonlocal max_concurrent, current
        async with acquire_project(fake_project, mode="write", force_pull=True):
            current += 1
            max_concurrent = max(max_concurrent, current)
            await asyncio.sleep(0.02)
            current -= 1
            return tag

    async def _run():
        with patch("overleaf_mcp.git_ops.ensure_repo", return_value=fake_repo):
            await asyncio.gather(writer("a"), writer("b"), writer("c"))
        assert max_concurrent == 1, (
            f"Writers ran concurrently (max={max_concurrent}); v1 race fix lost"
        )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Transient pull retry (one-shot with jitter)
# ---------------------------------------------------------------------------


def test_pull_retries_once_on_transient_failure(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """First pull fails with a transient error, second succeeds → no warning."""
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    monkeypatch.setenv("OVERLEAF_PULL_TTL", "0")
    # Skip the jitter sleep so the test runs fast
    monkeypatch.setattr(git_ops, "_RETRY_DELAY_RANGE", (0.0, 0.0))
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()
    transient = GitCommandError("pull", 1, b"", b"early EOF\nfatal: the remote end hung up unexpectedly")
    fake_repo.remotes.origin.pull.side_effect = [transient, None]

    with patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
        # Should NOT raise — the retry succeeds
        repo = ensure_repo(fake_project)
        assert repo is fake_repo

    assert fake_repo.remotes.origin.pull.call_count == 2


def test_pull_raises_stale_after_retry_exhausted(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Both attempts fail with transient error → StaleRepoWarning raised."""
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    monkeypatch.setenv("OVERLEAF_PULL_TTL", "0")
    monkeypatch.setattr(git_ops, "_RETRY_DELAY_RANGE", (0.0, 0.0))
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()
    transient = GitCommandError("pull", 1, b"", b"connection reset by peer")
    fake_repo.remotes.origin.pull.side_effect = transient

    with (
        patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo),
        pytest.raises(StaleRepoWarning),
    ):
        ensure_repo(fake_project)

    # Two attempts: original + retry
    assert fake_repo.remotes.origin.pull.call_count == 2


def test_pull_does_not_retry_on_permanent_failure(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Auth failure must NOT be retried — single attempt then StaleRepoWarning."""
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    monkeypatch.setenv("OVERLEAF_PULL_TTL", "0")
    monkeypatch.setattr(git_ops, "_RETRY_DELAY_RANGE", (0.0, 0.0))
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()
    # The exact text Overleaf returns on a bad token
    permanent = GitCommandError(
        "pull", 128, b"",
        b"fatal: Authentication failed for 'https://git.overleaf.com/...'",
    )
    fake_repo.remotes.origin.pull.side_effect = permanent

    with (
        patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo),
        pytest.raises(StaleRepoWarning, match="Authentication failed"),
    ):
        ensure_repo(fake_project)

    # Critical: no retry burned on a permanent failure
    assert fake_repo.remotes.origin.pull.call_count == 1


def test_is_transient_classification():
    """Spot-check the classifier — transient vs permanent error patterns."""
    # Transient patterns → True
    assert git_ops._is_transient_pull_error("early EOF")
    assert git_ops._is_transient_pull_error("connection reset by peer")
    assert git_ops._is_transient_pull_error("Could not resolve host: git.overleaf.com")
    assert git_ops._is_transient_pull_error("HTTP 502 Bad Gateway")
    assert git_ops._is_transient_pull_error("HTTP 503")
    assert git_ops._is_transient_pull_error("operation timed out")
    assert git_ops._is_transient_pull_error("broken pipe")
    assert git_ops._is_transient_pull_error(
        "fatal: the remote end hung up unexpectedly"
    )

    # Permanent patterns → False (must NOT be retried)
    assert not git_ops._is_transient_pull_error("Authentication failed")
    assert not git_ops._is_transient_pull_error("could not read Username")
    assert not git_ops._is_transient_pull_error(
        "fatal: couldn't find remote ref refs/heads/main"
    )
    assert not git_ops._is_transient_pull_error("Permission denied")
    assert not git_ops._is_transient_pull_error("invalid credentials")


# ---------------------------------------------------------------------------
# read_file max_bytes guardrail
# ---------------------------------------------------------------------------


def test_read_file_schema_has_max_bytes():
    """The read_file tool schema MUST expose a max_bytes parameter.

    Mirrors get_diff's existing max_output_chars guardrail — the asymmetry
    of letting read_file return arbitrarily large blobs is what v2 closes.
    """
    from overleaf_mcp.tools import list_tools

    tools = asyncio.run(list_tools())
    rf = next(t for t in tools if t.name == "read_file")
    props = rf.inputSchema["properties"]
    assert "max_bytes" in props, (
        f"read_file is missing max_bytes; properties present: {list(props)}"
    )


def test_read_file_truncates_oversized_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A file larger than max_bytes MUST be truncated with a visible marker."""
    from overleaf_mcp.tools import read_file as read_file_tool

    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    repo_dir = tmp_path / "p123"
    repo_dir.mkdir()
    big = repo_dir / "big.tex"
    big.write_text("X" * 5000)

    fake_repo = _make_fake_repo()
    with patch("overleaf_mcp.git_ops.ensure_repo", return_value=fake_repo):
        result = asyncio.run(
            read_file_tool(
                file_path="big.tex",
                git_token="t",
                project_id="p123",
                max_bytes=1000,
            )
        )

    # The truncated content fits under the limit; the marker is appended.
    assert "[file truncated" in result
    # Original 5000 chars must NOT all appear in output
    assert result.count("X") < 5000


def test_read_file_returns_full_content_under_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A file smaller than max_bytes MUST be returned in full with no marker."""
    from overleaf_mcp.tools import read_file as read_file_tool

    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    repo_dir = tmp_path / "p123"
    repo_dir.mkdir()
    small = repo_dir / "small.tex"
    small.write_text("hello world")

    fake_repo = _make_fake_repo()
    with patch("overleaf_mcp.git_ops.ensure_repo", return_value=fake_repo):
        result = asyncio.run(
            read_file_tool(
                file_path="small.tex",
                git_token="t",
                project_id="p123",
                max_bytes=1000,
            )
        )

    assert "hello world" in result
    assert "[file truncated" not in result


def test_acquire_project_default_mode_is_read(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Default mode (no kwarg) MUST behave as read — back-compat for any
    caller that hasn't been updated to pass mode= explicitly.
    """
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()
    max_concurrent = 0
    current = 0

    async def caller(tag: str):
        nonlocal max_concurrent, current
        # No mode= kwarg — must default to read
        async with acquire_project(fake_project):
            current += 1
            max_concurrent = max(max_concurrent, current)
            await asyncio.sleep(0.02)
            current -= 1
            return tag

    async def _run():
        with patch("overleaf_mcp.git_ops.ensure_repo", return_value=fake_repo):
            await asyncio.gather(caller("a"), caller("b"))
        assert max_concurrent == 2, (
            "Default mode is not read — callers without explicit mode= "
            "are still being serialized"
        )

    asyncio.run(_run())
