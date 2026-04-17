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
from contextlib import asynccontextmanager
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
        git_ops._LAST_PULL[git_ops._pull_cache_key(fake_project)] -= 10.0
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
    fake_repo.remotes.origin.pull.side_effect = GitCommandError("pull", 1, b"", b"boom")
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

    # acquire_project calls ensure_repo(..., _retry_sync=False); accept the
    # private kwarg silently so the mock signature doesn't drift from the
    # real function's.
    def _raise_stale(_project, *, force_pull=False, **_kwargs):
        raise StaleRepoWarning("network unreachable")

    async def _run():
        with (
            patch("overleaf_mcp.git_ops.ensure_repo", side_effect=_raise_stale),
            patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo),
        ):
            async with acquire_project(fake_project) as ctx:
                assert ctx.repo is fake_repo
                assert len(ctx.warnings) == 1
                assert "could not refresh" in ctx.warnings[0]
                assert "network unreachable" in ctx.warnings[0]

    asyncio.run(_run())


def test_acquire_project_falls_back_on_auth_failure(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Bad-token auth failure MUST serve the cached snapshot with a
    user-actionable warning — never silent failure, never an exception
    bubbled past the tool.

    This is the v1 test gap: the generic stale fallback was covered, but
    the specific auth-failure path Overleaf returns on a revoked or
    rotated token was not. A future regression that re-raises auth errors
    instead of degrading would silently break every read tool against
    expired credentials. This test pins the behavior down.
    """
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    monkeypatch.setenv("OVERLEAF_PULL_TTL", "0")
    # Bypass the retry-jitter sleep — this test isn't about retry timing
    monkeypatch.setattr(git_ops, "_RETRY_DELAY_RANGE", (0.0, 0.0))
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()
    # The exact stderr text Overleaf returns on a bad token (also covered
    # by test_pull_does_not_retry_on_permanent_failure for the no-retry
    # invariant; this test covers the user-facing behavior end-to-end).
    fake_repo.remotes.origin.pull.side_effect = GitCommandError(
        "pull",
        128,
        b"",
        b"fatal: Authentication failed for 'https://git.overleaf.com/abc123/'",
    )

    async def _run():
        with patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
            async with acquire_project(fake_project, mode="read") as ctx:
                # Body still runs — agent gets working tree access
                assert ctx.repo is fake_repo
                assert len(ctx.warnings) == 1
                # Warning is human-readable and mentions the actual cause
                w = ctx.warnings[0]
                assert "could not refresh" in w
                assert "Authentication failed" in w, (
                    f"Auth-failure warning lost the underlying message: {w!r}"
                )

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

    envelope = _json.loads(_re.search(r"<mcp-envelope>(.*?)</mcp-envelope>", result).group(1))
    assert envelope["ok"] is False
    assert envelope["warnings"] == ["⚠ could not refresh: boom"]


def test_toolcontext_wrap_envelope_ok_false_on_error_prefix(monkeypatch: pytest.MonkeyPatch):
    """ok=false when the response begins with 'Error:'."""
    monkeypatch.setenv("OVERLEAF_STRUCTURED", "1")
    ctx = ToolContext(repo=MagicMock(), warnings=[])
    result = ctx.wrap("Error: File 'x.tex' not found")
    import re as _re
    import json as _json

    envelope = _json.loads(_re.search(r"<mcp-envelope>(.*?)</mcp-envelope>", result).group(1))
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
            f"Readers serialized (max={max_concurrent}); RW lock did not loosen the read path"
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
# _LAST_PULL key precision (project_id, token_hash) — HTTP-transport future-proof
# ---------------------------------------------------------------------------


def test_pull_cache_separates_clients_with_different_tokens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Same project_id, different tokens → separate freshness entries.

    Today this is benign because stdio MCP serves one client per process.
    But if/when HTTP transport multiplexes clients, client A's recent pull
    must NOT suppress client B's needed pull — they have different
    auth contexts and may legitimately see different repository state.
    """
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    monkeypatch.setenv("OVERLEAF_PULL_TTL", "60")  # large window
    (tmp_path / "shared-id").mkdir()

    # Two clients see the same project_id but hold different tokens.
    proj_a = ProjectConfig(name="a", project_id="shared-id", git_token="tok_A")
    proj_b = ProjectConfig(name="b", project_id="shared-id", git_token="tok_B")

    fake_repo = _make_fake_repo()
    with patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
        ensure_repo(proj_a)  # client A pulls — entry stored
        ensure_repo(proj_b)  # client B should ALSO pull — different token

    # Critical invariant: B is not piggybacking on A's freshness flag.
    assert fake_repo.remotes.origin.pull.call_count == 2, (
        "Client B's pull was suppressed by client A's TTL entry — _LAST_PULL key is too coarse"
    )


def test_pull_cache_same_token_still_dedupes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Same project + same token → second call hits cache (v1 invariant)."""
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    monkeypatch.setenv("OVERLEAF_PULL_TTL", "60")
    (tmp_path / "p1").mkdir()

    proj = ProjectConfig(name="x", project_id="p1", git_token="same_tok")

    fake_repo = _make_fake_repo()
    with patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
        ensure_repo(proj)
        ensure_repo(proj)

    # v1 behavior preserved: identical (project_id, token) hits the cache
    assert fake_repo.remotes.origin.pull.call_count == 1


# ---------------------------------------------------------------------------
# Git user stamping at clone time (moved out of per-write-tool path)
# ---------------------------------------------------------------------------


def test_ensure_repo_stamps_user_on_fresh_clone(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A fresh clone MUST have user.name + user.email stamped immediately.

    Before v2 this happened lazily inside every write tool's call to
    config_git_user(ctx.repo). v2 stamps once at clone time so the write
    path doesn't carry the redundant config-writer fsync.
    """
    from configparser import NoSectionError

    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    monkeypatch.setenv("OVERLEAF_GIT_AUTHOR_NAME", "Stamped User")
    monkeypatch.setenv("OVERLEAF_GIT_AUTHOR_EMAIL", "stamped@test.local")

    # Fresh clone: config has no [user] section
    fresh_clone = _make_fake_repo()
    fresh_clone.config_reader.return_value.get_value.side_effect = NoSectionError("user")
    config_writer = MagicMock()
    fresh_clone.config_writer.return_value.__enter__.return_value = config_writer

    with patch("overleaf_mcp.git_ops.Repo.clone_from", return_value=fresh_clone):
        ensure_repo(fake_project)

    # The set_value calls should include both user.name and user.email
    sets = [(c.args[0], c.args[1], c.args[2]) for c in config_writer.set_value.call_args_list]
    assert ("user", "name", "Stamped User") in sets
    assert ("user", "email", "stamped@test.local") in sets


def test_ensure_repo_does_not_re_stamp_existing_user(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """An already-stamped repo MUST NOT have its user re-written."""
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    (tmp_path / fake_project.project_id).mkdir()  # existing clone

    existing = _make_fake_repo()
    # config_reader().get_value("user", "name") returns successfully
    existing.config_reader.return_value.get_value.return_value = "Already Set"
    config_writer = MagicMock()
    existing.config_writer.return_value.__enter__.return_value = config_writer

    with patch("overleaf_mcp.git_ops.Repo", return_value=existing):
        ensure_repo(fake_project)

    # config_writer.set_value must NOT have been called — repo already had user
    assert config_writer.set_value.call_count == 0, (
        "ensure_repo re-stamped user.name on an already-configured repo"
    )


# ---------------------------------------------------------------------------
# OVERLEAF_TIMING latency observability
# ---------------------------------------------------------------------------


def test_timing_log_emitted_when_env_set(
    fake_project: ProjectConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    """OVERLEAF_TIMING=1 emits a structured per-acquire log line."""
    import logging

    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    monkeypatch.setenv("OVERLEAF_TIMING", "1")
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()
    caplog.set_level(logging.INFO, logger="overleaf_mcp.git_ops")

    async def _run():
        with patch("overleaf_mcp.git_ops.ensure_repo", return_value=fake_repo):
            async with acquire_project(fake_project, mode="read"):
                pass

    asyncio.run(_run())

    timing_lines = [r.message for r in caplog.records if "acquire_project" in r.message]
    assert timing_lines, "No timing log line emitted with OVERLEAF_TIMING=1"
    line = timing_lines[-1]
    # Stable JSON format documented in docs/API.md — parse the payload
    # rather than substring-matching, so future key additions don't trip
    # the assertion.
    assert line.startswith("acquire_project "), f"timing line missing stable prefix: {line!r}"
    payload = json.loads(line[len("acquire_project ") :])
    assert payload["project"] == "p123"
    assert payload["mode"] == "read"
    assert isinstance(payload["elapsed_ms"], (int, float))
    assert payload["stale"] is False  # happy path → not stale


def test_timing_log_silent_when_env_unset(
    fake_project: ProjectConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    """Without OVERLEAF_TIMING=1, no timing line is emitted (back-compat)."""
    import logging

    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    monkeypatch.delenv("OVERLEAF_TIMING", raising=False)
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()
    caplog.set_level(logging.INFO, logger="overleaf_mcp.git_ops")

    async def _run():
        with patch("overleaf_mcp.git_ops.ensure_repo", return_value=fake_repo):
            async with acquire_project(fake_project, mode="read"):
                pass

    asyncio.run(_run())

    timing_lines = [r.message for r in caplog.records if "acquire_project " in r.message]
    assert not timing_lines, f"Timing line leaked without OVERLEAF_TIMING=1: {timing_lines}"


def test_timing_log_marks_stale_on_fallback(
    fake_project: ProjectConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    """When the stale-snapshot path fires, the timing line records stale=true."""
    import logging

    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    monkeypatch.setenv("OVERLEAF_TIMING", "1")
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()
    caplog.set_level(logging.INFO, logger="overleaf_mcp.git_ops")

    def _raise_stale(_project, *, force_pull=False, **_kwargs):
        raise StaleRepoWarning("network unreachable")

    async def _run():
        with (
            patch("overleaf_mcp.git_ops.ensure_repo", side_effect=_raise_stale),
            patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo),
        ):
            async with acquire_project(fake_project, mode="read"):
                pass

    asyncio.run(_run())

    timing_lines = [r.message for r in caplog.records if "acquire_project " in r.message]
    assert timing_lines, "No timing line emitted on stale path"
    payload = json.loads(timing_lines[-1][len("acquire_project ") :])
    assert payload["stale"] is True


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
    transient = GitCommandError(
        "pull", 1, b"", b"early EOF\nfatal: the remote end hung up unexpectedly"
    )
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
        "pull",
        128,
        b"",
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
    assert git_ops._is_transient_pull_error("fatal: the remote end hung up unexpectedly")

    # Permanent patterns → False (must NOT be retried)
    assert not git_ops._is_transient_pull_error("Authentication failed")
    assert not git_ops._is_transient_pull_error("could not read Username")
    assert not git_ops._is_transient_pull_error("fatal: couldn't find remote ref refs/heads/main")
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


def test_read_file_truncates_oversized_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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
        # No mode= kwarg — must default to read. Mirrors the 3-caller pattern
        # from test_acquire_project_allows_concurrent_readers: a 2-caller
        # variant was flaky on slow CI runners because acquire_project's
        # exclusive Phase 1 can interleave with Phase 2 such that only one
        # caller ever holds the shared lock at a time. Three callers make
        # the scheduling window wide enough to reliably observe ≥2 concurrent.
        async with acquire_project(fake_project):
            current += 1
            max_concurrent = max(max_concurrent, current)
            await asyncio.sleep(0.05)
            current -= 1
            return tag

    async def _run():
        with patch("overleaf_mcp.git_ops.ensure_repo", return_value=fake_repo):
            await asyncio.gather(caller("a"), caller("b"), caller("c"))
        assert max_concurrent >= 2, (
            "Default mode is not read — callers without explicit mode= are still being serialized"
        )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Item 6 (OPTIMIZATION_PLAN_V2): tools/list schema byte budget
# ---------------------------------------------------------------------------
#
# The 15-tool catalogue ships in every tools/list response and lives in the
# client's context for the entire session. The plan's T7.5 argued that each
# byte is a tradeoff (better selection vs. token cost per turn); commit
# b3a4390 did the one-shot audit and tightened several Field descriptions.
#
# This test is the regression gate: it pins the post-audit size at a known
# ceiling so silent drift can't creep back in. When it fails, the author is
# forced to decide between (a) trimming their description or (b) raising the
# ceiling — both explicit actions that leave a paper trail in git blame.


def test_tools_list_schema_stays_under_byte_budget():
    """Cap the total bytes of the tools/list payload to prevent description
    bloat from silently expanding the per-turn context cost.

    The ceiling matches the plan's explicit ≤5 % growth target over the
    post-audit baseline (14 382 bytes at commit b3a4390 → 14 500 here,
    a small amount of breathing room for minor wording nudges).

    When this test fails:
    * Did you add a new tool? → bump CEILING deliberately in the same commit.
    * Did you expand an existing Field description? → confirm the gain is
      worth the per-turn token cost; if so, bump. If it was accidental
      prose, trim instead.

    Do NOT raise the ceiling to unstick CI without thinking about which
    of those two cases applies.
    """
    from overleaf_mcp.tools import list_tools as _list_tools

    tools = asyncio.run(_list_tools())

    # Same serialization shape FastMCP sends over the wire: name +
    # description + inputSchema. We don't bake in exact JSON formatting
    # assumptions (separators, key order) — json.dumps with defaults is
    # stable enough for a regression test.
    payload = [
        {
            "name": t.name,
            "description": t.description,
            "inputSchema": t.inputSchema,
        }
        for t in tools
    ]
    raw = json.dumps(payload, ensure_ascii=False)
    total_bytes = len(raw.encode("utf-8"))

    # Baseline at plan-commit b3a4390: 14 382 bytes. Ceiling set a hair
    # above at 15 100 (~5 % headroom) per plan's T7.5 target. Update this
    # number deliberately with a one-line commit when legitimate growth
    # happens.
    CEILING = 15_100

    assert total_bytes <= CEILING, (
        f"tools/list schema is {total_bytes} bytes, exceeds ceiling {CEILING}. "
        f"Either a tool was added (bump CEILING with a justifying commit) or "
        f"a Field description drifted longer (trim it — per-turn token cost "
        f"is real). See OPTIMIZATION_PLAN_V2.md T7.5 / item 6."
    )


# ---------------------------------------------------------------------------
# Contention: writer holds lock, readers queue, run concurrently post-release
# ---------------------------------------------------------------------------


def test_contention_writer_blocks_readers_then_readers_run_concurrently(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Writer holds the lock, two readers queue, verify readers run in parallel
    the moment the writer releases.

    This is the post-review behavioral test: it exercises the *dynamic*
    interaction between the exclusive and shared modes, not just each one
    in isolation. Previous tests showed (a) readers can be concurrent and
    (b) writers exclude readers — but neither proved that release
    ordering is correct. A bug in ``notify_all`` (e.g. waking only one
    waiter) would still pass the isolation tests but serialize the
    readers here.
    """
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()
    writer_entered = asyncio.Event()
    writer_release_ok = asyncio.Event()

    reader_max_concurrent = 0
    readers_in_body = 0

    async def writer():
        async with acquire_project(fake_project, mode="write", force_pull=True):
            writer_entered.set()
            # Hold the writer for a beat so readers queue up behind us.
            await asyncio.wait_for(writer_release_ok.wait(), timeout=1.0)

    async def reader(tag: str):
        nonlocal reader_max_concurrent, readers_in_body
        # Wait for writer to be in the critical section before requesting.
        # This guarantees we queue behind an active writer, not race it.
        await writer_entered.wait()
        async with acquire_project(fake_project, mode="read") as ctx:
            # ctx unused — just holding the lock. Acknowledge to pyright.
            del ctx
            readers_in_body += 1
            reader_max_concurrent = max(reader_max_concurrent, readers_in_body)
            await asyncio.sleep(0.02)
            readers_in_body -= 1
        return tag

    async def _run():
        with patch("overleaf_mcp.git_ops.ensure_repo", return_value=fake_repo):
            w = asyncio.create_task(writer())
            r1 = asyncio.create_task(reader("a"))
            r2 = asyncio.create_task(reader("b"))
            # Let readers queue up behind the writer, then release.
            await writer_entered.wait()
            await asyncio.sleep(0.02)  # give readers time to block on shared()
            writer_release_ok.set()
            results = await asyncio.gather(r1, r2, w)

        assert sorted(x for x in results if x is not None) == ["a", "b"]
        # Critical: after the writer released, the two readers ran in
        # parallel (max concurrency ≥ 2), not one-after-the-other.
        assert reader_max_concurrent >= 2, (
            f"Readers serialized after writer release (max={reader_max_concurrent}); "
            "writer.exit did not wake both shared waiters"
        )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Write-mode: lock held continuously across Phase 1 (refresh) and Phase 2 (body)
# ---------------------------------------------------------------------------


def test_write_mode_takes_single_continuous_exclusive_lock(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Happy-path write-mode MUST take exactly one exclusive lock and
    ZERO shared locks — the refresh and body run under a single,
    continuous exclusive hold.

    Why two counters? Two different regressions break this invariant:

    1. **Pre-fix structure** — Phase 1 takes exclusive, releases, Phase 2
       re-takes exclusive. Counter: ``exclusive=2, shared=0``.
       Caught by the first assertion.
    2. **Continuous-lock fall-through** — write-mode branch skipped, body
       runs under the read-mode shared lock. Counter:
       ``exclusive=1, shared=1``. Caught by the second assertion.

    A timing-based reader-mid-write probe couldn't reliably detect
    either: the pre-fix gap between release and re-acquire is µs wide
    and scheduler-dependent, and the shared fall-through produces no
    observable timing anomaly at all. Direct instrumentation of the
    lock state transitions is deterministic and catches both.
    """
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()
    rwlock = git_ops._rwlock_for(fake_project.project_id)

    exclusive_count = 0
    shared_count = 0
    original_exclusive = rwlock.exclusive
    original_shared = rwlock.shared

    @asynccontextmanager
    async def counting_exclusive():
        nonlocal exclusive_count
        async with original_exclusive():
            exclusive_count += 1
            yield

    @asynccontextmanager
    async def counting_shared():
        nonlocal shared_count
        async with original_shared():
            shared_count += 1
            yield

    # Instance-level rebind, not class-level — rwlock is reset between
    # tests by _reset_module_caches, so no cross-test leak.
    rwlock.exclusive = counting_exclusive  # type: ignore[method-assign]
    rwlock.shared = counting_shared  # type: ignore[method-assign]

    async def _run():
        with patch("overleaf_mcp.git_ops.ensure_repo", return_value=fake_repo):
            async with acquire_project(fake_project, mode="write", force_pull=True):
                pass

    asyncio.run(_run())

    assert exclusive_count == 1, (
        f"write-mode acquired exclusive {exclusive_count} times; expected "
        "1 (refresh + body under a single continuous lock). "
        "count=2 means Phase 1 released and Phase 2 re-acquired."
    )
    assert shared_count == 0, (
        f"write-mode acquired shared {shared_count} times; expected 0. "
        "shared>=1 means write-mode fell through to the read-mode body "
        "path, which runs tool writes under a reader lock — unsafe."
    )


# ---------------------------------------------------------------------------
# Retry backoff sleep must NOT hold the writer lock
# ---------------------------------------------------------------------------


def test_transient_retry_releases_lock_during_backoff(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """During the 0.5–1.5 s retry backoff, another tool call MUST be able
    to run against the same project.

    Pre-fix: ``time.sleep`` ran inside ``_run_blocking`` while the writer
    lock was held, blocking every concurrent tool against the same
    project for the full backoff duration. Post-fix: ``acquire_project``
    releases the writer lock, awaits the backoff via ``asyncio.sleep``,
    then re-acquires.
    """
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    monkeypatch.setenv("OVERLEAF_PULL_TTL", "0")
    # Backoff is long enough that a lock-holding implementation would
    # never finish before the competing reader's timeout. When the fix
    # is in place, the reader completes well before the backoff ends.
    monkeypatch.setattr(git_ops, "_RETRY_DELAY_RANGE", (0.3, 0.3))
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()
    # First pull: transient failure. Every subsequent pull (second writer
    # attempt, reader's pull) succeeds. Function form avoids the
    # StopIteration that an `itertools`-style list raises when the reader
    # bumps the counter past the list length.
    pull_counter = {"n": 0}

    def pull_side_effect(*_args, **_kwargs):
        pull_counter["n"] += 1
        if pull_counter["n"] == 1:
            raise GitCommandError("pull", 1, b"", b"early EOF")
        return None

    fake_repo.remotes.origin.pull.side_effect = pull_side_effect
    reader_entered_during_backoff = False

    async def write_caller_that_retries():
        async with acquire_project(fake_project, mode="write", force_pull=True):
            pass

    async def reader_during_backoff():
        nonlocal reader_entered_during_backoff
        # Give the writer a short head-start so it has: (a) acquired
        # exclusive, (b) run its first pull attempt in the worker thread,
        # (c) classified the GitCommandError as transient, (d) released
        # exclusive, (e) begun the asyncio.sleep(0.3) backoff. With mocked
        # pull raising immediately, steps (a)–(e) take microseconds to
        # single-digit milliseconds. 0.05s is two orders of magnitude more
        # than needed — plenty of margin for a loaded CI box.
        #
        # Proof of the fix: with the writer in its async backoff sleep
        # (lock released), a shared reader acquire completes well under
        # our 0.2s timeout. With the pre-fix behavior (sync time.sleep
        # inside _run_blocking while holding exclusive), reader would
        # block the full remaining ~0.25s and hit the timeout.
        await asyncio.sleep(0.05)
        try:
            await asyncio.wait_for(_take_reader_briefly(fake_project), timeout=0.2)
            reader_entered_during_backoff = True
        except asyncio.TimeoutError:
            pass

    async def _run():
        with patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo):
            await asyncio.gather(
                write_caller_that_retries(),
                reader_during_backoff(),
            )
        assert reader_entered_during_backoff, (
            "Reader was blocked during retry backoff — the writer lock is "
            "still being held across the asyncio.sleep, defeating the fix"
        )

    asyncio.run(_run())


async def _take_reader_briefly(project: ProjectConfig) -> None:
    """Acquire a shared lock, confirm it, and release. Used by the
    contention test to prove the writer released during its backoff.
    """
    async with acquire_project(project, mode="read") as _ctx:
        del _ctx
        # No body — just proving the acquisition succeeded.


# ---------------------------------------------------------------------------
# sync_project: transient retry must release the lock during backoff
# ---------------------------------------------------------------------------


def test_sync_project_retry_releases_lock_during_backoff(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Same invariant as the acquire_project retry test, but for
    ``sync_project``: when the first pull attempt fails transiently, the
    writer lock must be released during the backoff so a concurrent
    read tool can run.

    Pre-fix: sync_project called ``ensure_repo`` with the default
    ``_retry_sync=True``, so the retry ``time.sleep`` ran inside the
    worker thread while the coroutine held exclusive — blocking every
    other tool call against the same project for 0.5–1.5s.
    Post-fix: sync_project mirrors acquire_project's release-sleep-
    reacquire pattern at the async layer.
    """
    from overleaf_mcp.tools import sync_project as sync_project_tool

    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    monkeypatch.setenv("OVERLEAF_PULL_TTL", "0")
    monkeypatch.setattr(git_ops, "_RETRY_DELAY_RANGE", (0.3, 0.3))
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()
    pull_counter = {"n": 0}

    def pull_side_effect(*_args, **_kwargs):
        pull_counter["n"] += 1
        if pull_counter["n"] == 1:
            raise GitCommandError("pull", 1, b"", b"early EOF")
        return None

    fake_repo.remotes.origin.pull.side_effect = pull_side_effect
    # Not-dirty so sync_project proceeds into the refresh path
    fake_repo.is_dirty = MagicMock(return_value=False)
    reader_completed_during_backoff = False

    async def _run_sync():
        async with _reader_after_delay(fake_project, 0.05, 0.2) as completed:
            result = await sync_project_tool(
                project_id=fake_project.project_id,
                git_token=fake_project.git_token,
            )
            # sync_project succeeds after the retry — no error prefix
            assert not result.startswith("Error:"), f"sync_project returned an error: {result!r}"
            assert "Synced" in result
            # The reader's completion event is set iff it acquired shared
            # during the writer's backoff (lock released).
            return completed["done"]

    # Patch Repo in BOTH modules — tools.py uses ``Repo(repo_path)`` for
    # the is_dirty pre-flight; git_ops.py uses it for ensure_repo's
    # existing-clone path.
    with (
        patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo),
        patch("overleaf_mcp.tools.Repo", return_value=fake_repo),
    ):
        reader_completed_during_backoff = asyncio.run(_run_sync())

    assert reader_completed_during_backoff, (
        "Reader was blocked during sync_project's retry backoff — "
        "the writer lock is still held across the sleep"
    )


@asynccontextmanager
async def _reader_after_delay(project, start_delay, timeout):
    """Helper: spawn a reader coroutine that waits ``start_delay`` seconds,
    then tries to take a shared lock with a ``timeout`` ceiling. Yields
    a dict that gets ``done=True`` iff the reader acquired successfully.
    """
    state = {"done": False}

    async def _reader():
        await asyncio.sleep(start_delay)
        try:
            await asyncio.wait_for(_take_reader_briefly(project), timeout=timeout)
            state["done"] = True
        except asyncio.TimeoutError:
            pass

    task = asyncio.create_task(_reader())
    try:
        yield state
        await task
    except BaseException:
        task.cancel()
        raise


# ---------------------------------------------------------------------------
# URL redaction: tokens must never leak into log lines / warnings / envelopes
# ---------------------------------------------------------------------------


def test_redact_url_strips_basic_auth_userinfo():
    """The redaction helper replaces ``user:TOKEN@host`` with ``<redacted>@host``."""
    from overleaf_mcp.git_ops import _redact_url

    msg = "fatal: Authentication failed for 'https://git:SECRET_TOKEN@git.overleaf.com/abc/'"
    out = _redact_url(msg)
    assert "SECRET_TOKEN" not in out
    assert "<redacted>@git.overleaf.com/abc/" in out
    # Scheme + host retained so the message is still diagnostic
    assert "https://" in out


def test_redact_url_no_op_when_no_userinfo():
    """Strings without userinfo pass through unchanged."""
    from overleaf_mcp.git_ops import _redact_url

    assert _redact_url("no URL here") == "no URL here"
    assert _redact_url("https://git.overleaf.com/abc") == "https://git.overleaf.com/abc"


# ---------------------------------------------------------------------------
# asyncio.TimeoutError → stale fallback (Issue D from deep-RCA)
# ---------------------------------------------------------------------------


def test_timeout_falls_back_to_stale_snapshot_when_clone_exists(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A git-op TimeoutError on an existing clone MUST fall back to the
    local snapshot with a visible ⚠ warning — not propagate as an
    unhandled exception.

    Before Fix D, ``asyncio.wait_for`` in ``_run_blocking`` would raise
    ``asyncio.TimeoutError`` past every layer and surface to the MCP
    client as a generic transport error. Readers get no signal that
    their tool result is still usable against the last-known-good
    snapshot.
    """
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    (tmp_path / fake_project.project_id).mkdir()  # local clone exists

    fake_repo = _make_fake_repo()

    async def _raise_timeout(*_args, **_kwargs):
        raise asyncio.TimeoutError()

    async def _run():
        # Patch _run_blocking to short-circuit with TimeoutError so we
        # exercise the except branch without actually waiting on a
        # wedged subprocess.
        with (
            patch("overleaf_mcp.git_ops._run_blocking", side_effect=_raise_timeout),
            patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo),
        ):
            async with acquire_project(fake_project, mode="read") as ctx:
                assert ctx.repo is fake_repo, (
                    "TimeoutError fallback did not yield the local snapshot"
                )
                assert len(ctx.warnings) == 1, f"Expected exactly one warning, got {ctx.warnings!r}"
                w = ctx.warnings[0]
                assert "could not refresh" in w
                assert "timed out" in w
                # Seconds number is present — the user needs to know the
                # ceiling so they can raise OVERLEAF_GIT_TIMEOUT if the
                # project genuinely takes longer.
                assert "s" in w

    asyncio.run(_run())


def test_timeout_propagates_on_cold_start_when_no_snapshot(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Cold-start clone timeout has no snapshot to fall back to, so the
    TimeoutError MUST propagate. Silent success on a never-cloned
    project would be worse than a loud error — the tool body would
    execute against an empty / nonexistent repo.
    """
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    # Note: NO local clone created — project dir does NOT exist.
    assert not (tmp_path / fake_project.project_id).exists()

    async def _raise_timeout(*_args, **_kwargs):
        raise asyncio.TimeoutError()

    async def _run():
        with (
            patch("overleaf_mcp.git_ops._run_blocking", side_effect=_raise_timeout),
            pytest.raises(asyncio.TimeoutError),
        ):
            async with acquire_project(fake_project, mode="read"):
                pass  # pragma: no cover — acquire should raise before yield

    asyncio.run(_run())


def test_stale_warning_contains_redacted_url_not_token(
    fake_project: ProjectConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """When ensure_repo raises StaleRepoWarning, the embedded URL is redacted.

    This is the critical fix for token leakage identified in code review.
    The ``⚠ could not refresh`` warning that reaches tool output must
    never contain the Basic-auth token.
    """
    monkeypatch.setattr(git_ops, "TEMP_DIR", str(tmp_path))
    monkeypatch.setenv("OVERLEAF_PULL_TTL", "0")
    monkeypatch.setattr(git_ops, "_RETRY_DELAY_RANGE", (0.0, 0.0))
    (tmp_path / fake_project.project_id).mkdir()

    fake_repo = _make_fake_repo()
    # Git error with the token embedded in the URL — the exact shape of
    # what GitPython surfaces when the pull fails mid-request.
    fake_repo.remotes.origin.pull.side_effect = GitCommandError(
        "pull",
        128,
        b"",
        b"fatal: Authentication failed for 'https://git:LEAKED_TOKEN@git.overleaf.com/abc/'",
    )

    with (
        patch("overleaf_mcp.git_ops.Repo", return_value=fake_repo),
        pytest.raises(StaleRepoWarning) as excinfo,
    ):
        ensure_repo(fake_project)

    # Token must NOT appear in the exception message.
    assert "LEAKED_TOKEN" not in str(excinfo.value), (
        f"Token leaked into StaleRepoWarning: {excinfo.value}"
    )
    assert "<redacted>@" in str(excinfo.value)


def test_tools_list_schema_has_all_fifteen_tools():
    """Companion assertion: if the byte-budget test above gets relaxed
    to accommodate a new tool, this test is what prevents silent tool
    removal — the count MUST go up, not stay the same.

    As of 1.1.0: 15 tools. Tier-3 did not add or remove any.
    """
    from overleaf_mcp.tools import TOOLS, list_tools as _list_tools

    tools = asyncio.run(_list_tools())
    assert len(tools) == len(TOOLS) == 15, (
        f"Expected 15 tools, got {len(tools)} via list_tools() and "
        f"{len(TOOLS)} in the TOOLS dict. Either a tool was added (update "
        f"this number AND bump CEILING in the byte-budget test) or one was "
        f"removed (confirm that's intentional)."
    )
