"""Git operations, async plumbing, and per-project concurrency control.

This module owns everything that touches Git or the event loop:

  * ``ensure_repo`` — clone/pull with a TTL-based freshness cache
  * ``_run_blocking`` — funnel Git subprocess work off the asyncio loop
  * ``acquire_project`` — async context manager that serializes concurrent
    tool calls on the same project via a per-project ``asyncio.Lock`` and
    surfaces upstream failures as visible warnings on the tool response
  * ``ToolContext`` — the handle every tool branch uses to read the repo
    and attach warnings to its output
  * Path helpers (``get_repo_path``, ``validate_path``) and the Git user
    config helper (``config_git_user``)

No MCP protocol knowledge lives here — that stays in ``tools.py`` and
``server.py``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from git import GitCommandError, Repo

from .config import ProjectConfig, TEMP_DIR

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# Base URL for Overleaf's Git endpoint. Overridable via env primarily for
# testing (point at a local file:// bare repo) but also for self-hosted
# Overleaf deployments where the Git host differs from git.overleaf.com.
OVERLEAF_GIT_URL = os.environ.get("OVERLEAF_GIT_URL", "https://git.overleaf.com")

# Performance / stability knobs (all env-overridable)
#
# OVERLEAF_PULL_TTL: seconds within which a successful pull is considered
#   fresh enough to skip on subsequent reads. Write tools always pass
#   force_pull=True and bypass this cache.
# OVERLEAF_GIT_TIMEOUT: hard upper bound (seconds) on any blocking Git
#   operation. Protects the stdio reader from hanging on a network
#   black-hole.
# OVERLEAF_SHALLOW_CLONE: when "1", new clones use --depth=N shallow
#   fetch. Trades history depth for download size — a huge win on
#   multi-GB projects, breaks deep `list_history` queries. Off by default.
# OVERLEAF_SHALLOW_DEPTH: depth for shallow clones (default 1). Ignored
#   when OVERLEAF_SHALLOW_CLONE is off.
_DEFAULT_PULL_TTL = 30.0
_DEFAULT_GIT_TIMEOUT = 60.0
_DEFAULT_SHALLOW_DEPTH = 1

# Subprocess-level backstop for the asyncio timeout ceiling in
# _run_blocking. If Git can't sustain LIMIT bytes/sec for TIME seconds,
# it aborts — which is what lets the timed-out thread actually exit.
os.environ.setdefault("GIT_HTTP_LOW_SPEED_LIMIT", "1000")
os.environ.setdefault("GIT_HTTP_LOW_SPEED_TIME", "30")


class StaleRepoWarning(Exception):
    """Signal that we couldn't refresh from upstream but have a local snapshot.

    ``acquire_project`` catches this, serves the tool's response against
    the stale local copy, and appends a visible ``⚠ could not refresh``
    line so callers aren't misled into thinking they have live data.
    """


# Module-level state. Process-local, no cross-invocation persistence.
#
# _LAST_PULL:    project_id -> monotonic timestamp of last successful pull.
#                NOTE: keyed by project_id alone. This assumes a single
#                client (the stdio MCP model). If a future HTTP transport
#                multiplexes clients with potentially different tokens for
#                the same project_id, the key must extend to
#                (project_id, token_hash) to prevent client A's freshness
#                flag from suppressing a necessary pull for client B.
#
# _PROJECT_LOCKS: project_id -> asyncio.Lock. Serializes concurrent tool
#                calls against the same local clone. Without this, two
#                parallel writers can both pass the TTL check and race on
#                GitPython's non-thread-safe index. Lazily initialized in
#                _lock_for(); never cleared — lock objects are cheap and
#                projects don't churn in practice.
_LAST_PULL: dict[str, float] = {}
_PROJECT_LOCKS: dict[str, asyncio.Lock] = {}


def _lock_for(project_id: str) -> asyncio.Lock:
    """Return the per-project lock, creating it on first access."""
    lock = _PROJECT_LOCKS.get(project_id)
    if lock is None:
        lock = asyncio.Lock()
        _PROJECT_LOCKS[project_id] = lock
    return lock


def _pull_ttl() -> float:
    """Resolve the pull TTL from env at call time (allows test monkeypatching)."""
    try:
        return float(os.environ.get("OVERLEAF_PULL_TTL", _DEFAULT_PULL_TTL))
    except ValueError:
        return _DEFAULT_PULL_TTL


def _git_timeout() -> float:
    """Resolve the hard Git-op timeout from env at call time."""
    try:
        return float(os.environ.get("OVERLEAF_GIT_TIMEOUT", _DEFAULT_GIT_TIMEOUT))
    except ValueError:
        return _DEFAULT_GIT_TIMEOUT


def _shallow_clone_kwargs() -> dict[str, Any]:
    """Return extra kwargs for ``Repo.clone_from`` when shallow mode is on.

    Shallow clones (``--depth=N``) download only the N most recent commits
    rather than the full history. For multi-GB Overleaf projects this is
    the difference between a 30-second and a 30-minute cold start, at the
    cost of breaking ``git log`` beyond the shallow boundary — meaning
    ``list_history`` caps out at ``OVERLEAF_SHALLOW_DEPTH`` commits and
    ``get_diff`` against older refs fails. Off by default for correctness.
    """
    if os.environ.get("OVERLEAF_SHALLOW_CLONE") != "1":
        return {}
    try:
        depth = max(1, int(os.environ.get("OVERLEAF_SHALLOW_DEPTH", _DEFAULT_SHALLOW_DEPTH)))
    except ValueError:
        depth = _DEFAULT_SHALLOW_DEPTH
    return {"depth": depth}


def get_repo_path(project_id: str) -> Path:
    """Get the local repository path for a project."""
    return Path(TEMP_DIR) / project_id


def _build_git_url(project: ProjectConfig) -> str:
    """Build the authenticated Git remote URL for a project.

    Reads ``OVERLEAF_GIT_URL`` at call time (not import time) so tests can
    monkeypatch the module constant. For ``https://...`` URLs we embed the
    token as HTTPS Basic auth (``https://git:TOKEN@host/project_id``);
    for ``file://...`` URLs (test fixtures) the credentials are simply
    appended to the path without auth syntax.
    """
    base = OVERLEAF_GIT_URL
    if base.startswith("https://"):
        host = base[len("https://"):]
        return f"https://git:{project.git_token}@{host}/{project.project_id}"
    # file:// and other schemes — auth is meaningless, just append project id.
    return f"{base.rstrip('/')}/{project.project_id}"


def validate_path(base_path: Path, target_path: str) -> Path:
    """Validate that target path doesn't escape the repository root."""
    resolved = (base_path / target_path).resolve()
    if not str(resolved).startswith(str(base_path.resolve())):
        raise ValueError(f"Path '{target_path}' escapes repository root")
    return resolved


def ensure_repo(project: ProjectConfig, *, force_pull: bool = False) -> Repo:
    """Ensure the repository is cloned and acceptably fresh.

    * First call for a project: full clone (no TTL bypass possible).
    * Subsequent calls: pull only if the last successful pull is older than
      ``OVERLEAF_PULL_TTL`` seconds, OR if ``force_pull=True``. Write tools
      must pass ``force_pull=True`` so a commit is never based on stale state.
    * Pull failures on an existing clone are raised as ``StaleRepoWarning`` —
      caller decides whether to serve stale data with a warning or abort.

    NOTE: Callers must hold the per-project lock (see ``acquire_project``)
    when calling this. Without the lock, two concurrent callers can both
    pass the TTL check and race on GitPython's non-thread-safe index.
    """
    repo_path = get_repo_path(project.project_id)
    git_url = _build_git_url(project)

    if not repo_path.exists():
        # Cold start — no way to avoid the network. Clone synchronously.
        clone_kwargs = _shallow_clone_kwargs()
        if clone_kwargs:
            logger.info(
                "cloning project %s (cold start, shallow depth=%s)",
                project.project_id, clone_kwargs["depth"],
            )
        else:
            logger.info("cloning project %s (cold start)", project.project_id)
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        repo = Repo.clone_from(git_url, repo_path, **clone_kwargs)
        _LAST_PULL[project.project_id] = time.monotonic()
        return repo

    repo = Repo(repo_path)
    origin = repo.remotes.origin
    if origin.url != git_url:
        origin.set_url(git_url)

    now = time.monotonic()
    last = _LAST_PULL.get(project.project_id, 0.0)
    if not force_pull and (now - last) < _pull_ttl():
        # Within TTL window — skip the network entirely.
        logger.debug(
            "pull suppressed by TTL (age=%.1fs, ttl=%.1fs, project=%s)",
            now - last, _pull_ttl(), project.project_id,
        )
        return repo

    logger.debug(
        "pulling project %s (force=%s, age=%.1fs)",
        project.project_id, force_pull, now - last,
    )
    try:
        origin.pull()
        _LAST_PULL[project.project_id] = now
    except GitCommandError as e:
        # Caller gets the local snapshot with a warning attached.
        logger.warning("pull failed for %s: %s", project.project_id, e)
        raise StaleRepoWarning(str(e).strip()) from e
    return repo


async def _run_blocking(fn: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
    """Run a blocking callable off the event loop with a hard timeout.

    Every Git/subprocess op goes through here. The ``asyncio.to_thread``
    keeps the stdio reader responsive while the op runs; the ``wait_for``
    ceiling ensures a wedged Git process can't stall *the caller* forever.

    SUBPROCESS LIFETIME CAVEAT: ``asyncio.wait_for`` bounds caller latency
    but NOT the underlying OS thread or any subprocess it spawned. When the
    timeout fires, the coroutine raises ``asyncio.TimeoutError`` but the
    thread running the blocking function keeps executing until it exits on
    its own. For Git operations this is bounded separately by the
    ``GIT_HTTP_LOW_SPEED_TIME`` / ``GIT_HTTP_LOW_SPEED_LIMIT`` env vars
    (set to 30s / 1000 B/s at import time), which cause the git child
    process to self-abort on a wedged connection. Without that backstop,
    a black-holed TCP connection would leak a thread per timed-out call.
    """
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(fn, *args, **kwargs),
            timeout=_git_timeout(),
        )
    except asyncio.TimeoutError:
        logger.warning(
            "blocking op %s exceeded %ss ceiling — underlying thread may "
            "still be running until GIT_HTTP_LOW_SPEED_TIME expires",
            getattr(fn, "__qualname__", repr(fn)),
            _git_timeout(),
        )
        raise


@dataclass
class ToolContext:
    """Bundle passed to every tool branch.

    Attributes:
        repo: The prepared ``git.Repo`` handle.
        warnings: Any soft-failure warnings (e.g. stale-repo) to append to
            the tool's response via :meth:`wrap`.

    Usage pattern in every tool branch::

        async with acquire_project(project, force_pull=False) as ctx:
            # ctx.repo is ready; the per-project lock is held for the
            # duration of the `async with` block.
            ...
            return ctx.wrap("tool result string")
    """
    repo: Repo
    warnings: list[str] = field(default_factory=list)

    def wrap(self, result: str) -> str:
        """Format the tool response: human text + warnings + optional envelope.

        Layout:
            <human-readable result>
            <blank line>
            <warnings (one per line, if any)>
            <blank line>
            <mcp-envelope>{"ok":bool,"warnings":[...]}</mcp-envelope>  (opt-in)

        The JSON envelope is only appended when ``OVERLEAF_STRUCTURED=1`` is
        set, so existing clients consuming plain text are unaffected. Agentic
        clients can opt in and grep the envelope block for a reliable parse
        target. ``ok`` is ``True`` when no warnings and no "Error:" prefix;
        it's a heuristic but a practically useful one for "did this succeed?"
        """
        out = result
        if self.warnings:
            out += "\n\n" + "\n".join(self.warnings)
        if os.environ.get("OVERLEAF_STRUCTURED") == "1":
            ok = (
                not result.lstrip().startswith("Error:")
                and not self.warnings
            )
            envelope = {"ok": ok, "warnings": self.warnings}
            out += f"\n\n<mcp-envelope>{json.dumps(envelope)}</mcp-envelope>"
        return out


@asynccontextmanager
async def acquire_project(
    project: ProjectConfig, *, force_pull: bool = False
) -> AsyncIterator[ToolContext]:
    """Acquire per-project lock, prepare the repo, yield a ``ToolContext``.

    This is the single entry point for every tool branch. It:
      1. Acquires the per-project :class:`asyncio.Lock` (creating it on
         first use). The lock is held for the entire ``async with`` body,
         which serializes concurrent tool calls against the same project's
         local clone — necessary because GitPython is not thread-safe.
      2. Runs :func:`ensure_repo` off the event loop via
         :func:`_run_blocking`, honoring the TTL cache and force_pull flag.
      3. On :class:`StaleRepoWarning`, falls back to opening the local
         clone and attaches a user-visible warning.
      4. Yields a :class:`ToolContext` for the body to consume.
      5. Releases the lock automatically on exit (including on exception).
    """
    async with _lock_for(project.project_id):
        try:
            repo = await _run_blocking(ensure_repo, project, force_pull=force_pull)
            yield ToolContext(repo=repo)
        except StaleRepoWarning as w:
            logger.info("serving stale snapshot for %s: %s", project.project_id, w)
            repo_path = get_repo_path(project.project_id)
            repo = Repo(repo_path)
            yield ToolContext(
                repo=repo,
                warnings=[f"⚠ could not refresh from Overleaf: {w}"],
            )


def config_git_user(repo: Repo) -> None:
    """Configure git user.name/user.email if not already set on the repo.

    We catch the narrow set of errors GitPython raises when the key is
    missing from the config — ConfigParser's NoSectionError/NoOptionError.
    A bare ``except Exception`` previously swallowed programming errors
    here (e.g. repo API changes) and was the subject of commit f674243.
    """
    from configparser import NoOptionError, NoSectionError

    try:
        repo.config_reader().get_value("user", "name")
        return  # already set, nothing to do
    except (NoOptionError, NoSectionError):
        pass  # expected on fresh clones — fall through to set it

    name = os.environ.get("OVERLEAF_GIT_AUTHOR_NAME", "Overleaf MCP")
    email = os.environ.get("OVERLEAF_GIT_AUTHOR_EMAIL", "mcp@overleaf.local")

    with repo.config_writer() as config:
        config.set_value("user", "name", name)
        config.set_value("user", "email", email)
