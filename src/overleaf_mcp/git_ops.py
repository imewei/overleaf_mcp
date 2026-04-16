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
import hashlib
import json
import logging
import os
import random
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypeVar

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

# One-shot retry on transient pull failures. The delay range is module-level
# so tests can monkeypatch it to (0, 0) and run instantly. In production,
# 0.5–1.5s gives the upstream a chance to recover from a hiccup without
# hammering it on a sustained outage. Range is intentionally narrow — agentic
# tool loops are interactive; long delays are worse than a fast stale-fallback.
_RETRY_DELAY_RANGE: tuple[float, float] = (0.5, 1.5)

# Pattern matchers for "transient" failures (worth one retry) vs "permanent"
# failures (no retry — auth/ref mistakes won't fix themselves). Matching is
# substring-on-stderr; we err on the side of NOT retrying when uncertain so
# we never waste a round-trip on a clearly-broken request.
_TRANSIENT_PATTERNS = re.compile(
    r"(early EOF"
    r"|connection reset"
    r"|broken pipe"
    r"|could not resolve host"
    r"|temporary failure in name resolution"
    r"|operation timed out"
    r"|timed out"
    r"|HTTP 5\d{2}"
    r"|hung up unexpectedly"
    r"|gnutls_handshake.*failed"
    r"|ssl.*handshake.*failed)",
    re.IGNORECASE,
)


def _is_transient_pull_error(message: str) -> bool:
    """Classify a pull error message as worth one retry.

    True = transient (network blip, upstream 5xx) — retry once.
    False = permanent (bad auth, bad ref, bad permissions) — fail fast.
    """
    return bool(_TRANSIENT_PATTERNS.search(message))

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
# _LAST_PULL:        (project_id, token_hash) -> monotonic timestamp of last
#                    successful pull. v2 widened the key from project_id alone
#                    to also incorporate a 16-hex-char prefix of the token's
#                    SHA-256 hash. Today (single-client stdio) this changes
#                    nothing. Tomorrow (HTTP transport multiplexing clients),
#                    it ensures client A's freshness flag can't suppress a
#                    needed pull for client B holding a different token —
#                    they may legitimately see different repository state
#                    (e.g. one revoked/rotated, one not).
#
# _PROJECT_RWLOCKS:  project_id -> _RWLock. Serializes concurrent tool calls
#                    against the same local clone with reader-writer semantics:
#                    readers (read tools) run concurrently; writers (write
#                    tools + the refresh phase) get exclusive access. Without
#                    the writer side, parallel writers would race on
#                    GitPython's non-thread-safe index. Without the reader
#                    side, every read tool would queue behind every other —
#                    the v1 design that v2 is loosening here. Lazily
#                    initialized in _rwlock_for(); never cleared — lock
#                    objects are cheap and projects don't churn in practice.
#                    NOTE: keyed by project_id alone, not (project_id, token).
#                    The lock protects the on-disk clone, which is shared by
#                    every client of that project regardless of auth.
_LAST_PULL: dict[tuple[str, str], float] = {}
_PROJECT_RWLOCKS: dict[str, _RWLock] = {}


def _pull_cache_key(project: ProjectConfig) -> tuple[str, str]:
    """Return the (project_id, token_hash) key used by _LAST_PULL.

    Token hash is the first 16 hex chars of SHA-256(token) — collision
    space of 2^64 is more than enough for the realistic project-count
    scale, and we never store the raw token for cache purposes.
    """
    token_hash = hashlib.sha256(project.git_token.encode()).hexdigest()[:16]
    return (project.project_id, token_hash)


class _RWLock:
    """Async reader-writer lock with writer priority.

    Readers acquire shared access via :meth:`shared`; writers acquire
    exclusive access via :meth:`exclusive`. Writer priority means a pending
    writer blocks new readers, which prevents reader starvation under
    sustained read load.

    Why not ``asyncio.Lock``? A plain Lock serializes everything. For
    overleaf_mcp's read-heavy agent workload, that's wasteful — concurrent
    ``read_file`` / ``list_files`` / ``get_sections`` tools touch only the
    working tree, never mutate ``.git``, and can safely run in parallel.
    The writer side preserves the v1 invariant that pulls/commits/pushes
    serialize against everything else (GitPython is not thread-safe).
    """

    def __init__(self) -> None:
        self._cond = asyncio.Condition()
        self._readers = 0
        self._writer = False
        self._writers_pending = 0

    @asynccontextmanager
    async def shared(self) -> AsyncIterator[None]:
        """Acquire shared (reader) access. Multiple holders allowed."""
        async with self._cond:
            # Writer-priority: a pending writer blocks new readers.
            while self._writer or self._writers_pending > 0:
                await self._cond.wait()
            self._readers += 1
        try:
            yield
        finally:
            async with self._cond:
                self._readers -= 1
                if self._readers == 0:
                    # Wake any pending writer (or other waiters).
                    self._cond.notify_all()

    @asynccontextmanager
    async def exclusive(self) -> AsyncIterator[None]:
        """Acquire exclusive (writer) access. Excludes all other holders."""
        async with self._cond:
            self._writers_pending += 1
            try:
                while self._readers > 0 or self._writer:
                    await self._cond.wait()
                self._writer = True
            finally:
                self._writers_pending -= 1
        try:
            yield
        finally:
            async with self._cond:
                self._writer = False
                self._cond.notify_all()


def _rwlock_for(project_id: str) -> _RWLock:
    """Return the per-project RW lock, creating it on first access."""
    lock = _PROJECT_RWLOCKS.get(project_id)
    if lock is None:
        lock = _RWLock()
        _PROJECT_RWLOCKS[project_id] = lock
    return lock


def _lock_for(project_id: str) -> _RWLock:
    """Back-compat alias for ``_rwlock_for`` — returns the RW lock object.

    Kept so external callers (notably ``sync_project`` in tools.py) that
    previously did ``async with _lock_for(...):`` can migrate by switching
    to ``async with _lock_for(...).exclusive():`` without changing the
    import. The single entry point for tool branches is ``acquire_project``;
    this helper is the escape hatch for the small number of call sites
    that need lower-level control.
    """
    return _rwlock_for(project_id)


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
    cache_key = _pull_cache_key(project)

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
        # Stamp user.name + user.email once at clone time so write tools
        # don't pay a redundant config-writer fsync on every commit. The
        # function is idempotent — see config_git_user docstring.
        config_git_user(repo)
        _LAST_PULL[cache_key] = time.monotonic()
        return repo

    repo = Repo(repo_path)
    # Belt-and-braces for clones predating this change: stamp on every open.
    # Idempotent fast path returns immediately if user.name is already set.
    config_git_user(repo)
    origin = repo.remotes.origin
    if origin.url != git_url:
        origin.set_url(git_url)

    now = time.monotonic()
    last = _LAST_PULL.get(cache_key, 0.0)
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
        _LAST_PULL[cache_key] = now
    except GitCommandError as e:
        msg = str(e).strip()
        # One transparent retry for transient failures only (network blip,
        # upstream 5xx). Permanent failures (auth, missing ref) skip the
        # retry — they'll just fail twice and waste a round-trip.
        if _is_transient_pull_error(msg):
            delay = random.uniform(*_RETRY_DELAY_RANGE)
            logger.info(
                "pull failed transiently for %s (retry in %.2fs): %s",
                project.project_id, delay, msg,
            )
            time.sleep(delay)
            try:
                origin.pull()
                _LAST_PULL[cache_key] = time.monotonic()
                return repo
            except GitCommandError as retry_e:
                msg = str(retry_e).strip()
                logger.warning(
                    "pull failed twice for %s (giving up): %s",
                    project.project_id, msg,
                )
                raise StaleRepoWarning(msg) from retry_e
        # Caller gets the local snapshot with a warning attached.
        logger.warning("pull failed for %s: %s", project.project_id, msg)
        raise StaleRepoWarning(msg) from e
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
    project: ProjectConfig,
    *,
    force_pull: bool = False,
    mode: Literal["read", "write"] = "read",
) -> AsyncIterator[ToolContext]:
    """Acquire per-project RW lock, prepare the repo, yield a ``ToolContext``.

    This is the single entry point for every tool branch. Two-phase design:

      Phase 1 (refresh, always exclusive): runs :func:`ensure_repo` under the
      writer lock. Holding exclusive here serializes concurrent pulls against
      the same project — necessary because two parallel pulls race on
      ``.git/HEAD`` and ``.git/index``. For TTL-cache-fresh calls the writer
      lock is held for ~µs (no I/O happens). On :class:`StaleRepoWarning`,
      falls back to the local snapshot with a visible warning attached.

      Phase 2 (tool body): re-acquires the lock per ``mode`` —
      ``mode="read"`` takes shared (multiple readers run concurrently);
      ``mode="write"`` takes exclusive (full v1 serialization preserved).

    Mode defaults to ``read`` so any caller that omits the kwarg gets the
    safe-and-fast read path. Write tools must pass ``mode="write"``
    explicitly. Mixing ``force_pull=False`` with ``mode="write"`` is
    permitted but unusual — write tools normally pair both flags.

    Observability: when ``OVERLEAF_TIMING=1`` is set, emits a structured
    INFO log line on context exit with project, mode, elapsed_ms, and
    stale flag. Costs nothing when off (one env-var lookup per call).
    """
    rwlock = _rwlock_for(project.project_id)
    timing_on = os.environ.get("OVERLEAF_TIMING") == "1"
    started = time.monotonic() if timing_on else 0.0
    stale = False

    # Phase 1: refresh under exclusive (writer) lock.
    warnings: list[str] = []
    repo: Repo
    async with rwlock.exclusive():
        try:
            repo = await _run_blocking(ensure_repo, project, force_pull=force_pull)
        except StaleRepoWarning as w:
            logger.info("serving stale snapshot for %s: %s", project.project_id, w)
            repo_path = get_repo_path(project.project_id)
            repo = Repo(repo_path)
            warnings = [f"⚠ could not refresh from Overleaf: {w}"]
            stale = True

    # Phase 2: tool body — shared for read, exclusive for write.
    body_lock = rwlock.shared() if mode == "read" else rwlock.exclusive()
    try:
        async with body_lock:
            yield ToolContext(repo=repo, warnings=warnings)
    finally:
        if timing_on:
            elapsed_ms = (time.monotonic() - started) * 1000.0
            logger.info(
                "acquire_project project=%s mode=%s elapsed_ms=%.1f stale=%s",
                project.project_id, mode, elapsed_ms,
                "true" if stale else "false",
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
