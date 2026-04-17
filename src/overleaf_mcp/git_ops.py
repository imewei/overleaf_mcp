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


class _TransientPullError(Exception):
    """Internal signal: pull failed transiently, one retry is advised.

    Distinguishes "retry-worthy network blip" from the permanent
    :class:`StaleRepoWarning` path. Raised by ``ensure_repo(..., _retry_sync=False)``
    so ``acquire_project`` can orchestrate the retry with an *async* sleep
    outside the writer lock, rather than letting a ``time.sleep`` inside
    the worker thread hold the exclusive lock against every concurrent tool.
    """


# Redacts ``user:password@`` or ``user@`` from http(s) URLs embedded in
# strings. Git remote URLs carry the auth token as HTTPS Basic
# (``https://git:TOKEN@host/...``), and ``GitCommandError.stderr`` often
# echoes the URL verbatim. Without redaction that text flows into log
# lines, the stale-repo warning attached to tool output, and the
# structured envelope — any of which may be captured by end users or
# shipped to observability pipelines. The replacement leaves the scheme
# and host intact so the message stays diagnostic.
_URL_USERINFO_PATTERN = re.compile(r"(https?://)[^/\s@]+@")


def _redact_url(msg: str) -> str:
    """Strip userinfo (``user:password@``) from HTTP(S) URLs in *msg*.

    Safe to call on any string — returns *msg* unchanged if no matches
    are found. Used at every boundary where a :class:`GitCommandError`
    message crosses into logs, exceptions, or tool output.
    """
    return _URL_USERINFO_PATTERN.sub(r"\1<redacted>@", msg)


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
        host = base[len("https://") :]
        return f"https://git:{project.git_token}@{host}/{project.project_id}"
    # file:// and other schemes — auth is meaningless, just append project id.
    return f"{base.rstrip('/')}/{project.project_id}"


def validate_path(base_path: Path, target_path: str) -> Path:
    """Validate that target path doesn't escape the repository root."""
    resolved = (base_path / target_path).resolve()
    if not str(resolved).startswith(str(base_path.resolve())):
        raise ValueError(f"Path '{target_path}' escapes repository root")
    return resolved


def ensure_repo(
    project: ProjectConfig,
    *,
    force_pull: bool = False,
    _retry_sync: bool = True,
) -> Repo:
    """Ensure the repository is cloned and acceptably fresh.

    * First call for a project: full clone (no TTL bypass possible).
    * Subsequent calls: pull only if the last successful pull is older than
      ``OVERLEAF_PULL_TTL`` seconds, OR if ``force_pull=True``. Write tools
      must pass ``force_pull=True`` so a commit is never based on stale state.
    * Pull failures on an existing clone are raised as ``StaleRepoWarning``
      (permanent) or :class:`_TransientPullError` (one retry advised) —
      caller decides whether to serve stale data with a warning or abort.

    The ``_retry_sync`` kwarg controls whether the one-shot transient retry
    runs inline (default, ``time.sleep`` in the same thread) or is deferred
    to the caller via :class:`_TransientPullError`. ``acquire_project`` uses
    ``_retry_sync=False`` so the retry sleep happens at the asyncio level
    with the writer lock released — see that function for the rationale.

    NOTE: Callers must hold the per-project lock (see ``acquire_project``)
    when calling this. Without the lock, two concurrent callers can both
    pass the TTL check and race on GitPython's non-thread-safe index.

    All user-visible messages (log lines, ``StaleRepoWarning`` payload,
    ``_TransientPullError`` payload) are URL-redacted via :func:`_redact_url`
    so the Basic-auth token embedded in the remote URL never leaks.
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
                project.project_id,
                clone_kwargs["depth"],
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
            now - last,
            _pull_ttl(),
            project.project_id,
        )
        return repo

    logger.debug(
        "pulling project %s (force=%s, age=%.1fs)",
        project.project_id,
        force_pull,
        now - last,
    )
    try:
        origin.pull()
        _LAST_PULL[cache_key] = now
    except GitCommandError as e:
        raw_msg = str(e).strip()
        redacted_msg = _redact_url(raw_msg)
        # Transient vs permanent: classify on the raw text (patterns don't
        # care about the redaction), surface only the redacted text.
        if _is_transient_pull_error(raw_msg):
            if not _retry_sync:
                # Caller (acquire_project) will handle the retry with an
                # async sleep outside the writer lock.
                raise _TransientPullError(redacted_msg) from e
            # Inline sync retry — kept for direct callers (tests,
            # sync_project) that aren't in the async-lock path.
            # Bandit B311 rationale: jitter for retry backoff, not a security
            # primitive. random.uniform is used purely to de-synchronize retries
            # across concurrent callers (avoid thundering-herd on a recovering
            # Overleaf endpoint). No cryptographic property is required.
            delay = random.uniform(*_RETRY_DELAY_RANGE)  # nosec B311
            logger.info(
                "pull failed transiently for %s (retry in %.2fs): %s",
                project.project_id,
                delay,
                redacted_msg,
            )
            time.sleep(delay)
            try:
                origin.pull()
                _LAST_PULL[cache_key] = time.monotonic()
                return repo
            except GitCommandError as retry_e:
                retry_redacted = _redact_url(str(retry_e).strip())
                logger.warning(
                    "pull failed twice for %s (giving up): %s",
                    project.project_id,
                    retry_redacted,
                )
                raise StaleRepoWarning(retry_redacted) from retry_e
        # Caller gets the local snapshot with a warning attached.
        logger.warning("pull failed for %s: %s", project.project_id, redacted_msg)
        raise StaleRepoWarning(redacted_msg) from e
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
            ok = not result.lstrip().startswith("Error:") and not self.warnings
            envelope = {"ok": ok, "warnings": self.warnings}
            out += f"\n\n<mcp-envelope>{json.dumps(envelope)}</mcp-envelope>"
        return out


async def _refresh_once(
    project: ProjectConfig, *, force_pull: bool
) -> tuple[Repo | None, list[str], bool, str | None]:
    """Single refresh attempt in a worker thread.

    Returns ``(repo, warnings, stale, transient_msg)``:
      * ``transient_msg`` non-``None`` → caller should retry (lock must
        be released first so the sleep doesn't starve concurrent tools);
        ``repo`` is ``None`` in this case.
      * ``stale=True`` → pull didn't complete (permanent failure OR
        ``OVERLEAF_GIT_TIMEOUT`` ceiling hit); ``repo`` is the local
        snapshot, ``warnings`` includes the user-facing refresh-failed
        line. Caller gets to serve a response against the last-known
        good state rather than bubbling an exception to the user.
      * Otherwise → happy path, fresh ``repo`` with no warnings.

    Raises :class:`asyncio.TimeoutError` **only** when the git op times
    out AND there is no local snapshot to fall back to (cold-start
    timeout). The hot-path timeout is absorbed into the stale-fallback
    branch so a wedged network never turns a read tool into an
    unhandled exception.
    """
    try:
        repo = await _run_blocking(ensure_repo, project, force_pull=force_pull, _retry_sync=False)
        return repo, [], False, None
    except _TransientPullError as te:
        return None, [], False, str(te).strip()
    except StaleRepoWarning as w:
        # ensure_repo already redacted the message; no double-redact needed.
        msg = str(w).strip()
        logger.info("serving stale snapshot for %s: %s", project.project_id, msg)
        repo = Repo(get_repo_path(project.project_id))
        return repo, [f"⚠ could not refresh from Overleaf: {msg}"], True, None
    except asyncio.TimeoutError:
        # Git op exceeded OVERLEAF_GIT_TIMEOUT. Parallel to the
        # StaleRepoWarning branch — we can't refresh, but we can serve
        # the local snapshot with a user-visible warning so the caller
        # isn't left with an unhandled exception. Cold-start case (no
        # local clone) has nothing to serve; re-raise so the tool call
        # fails cleanly with the timeout it was.
        timeout_s = _git_timeout()
        repo_path = get_repo_path(project.project_id)
        if not repo_path.exists():
            logger.warning(
                "cold-start clone for %s timed out after %.1fs; no local snapshot available",
                project.project_id,
                timeout_s,
            )
            raise
        logger.warning(
            "pull for %s timed out after %.1fs; serving local snapshot",
            project.project_id,
            timeout_s,
        )
        repo = Repo(repo_path)
        warning = f"⚠ could not refresh from Overleaf: pull timed out after {timeout_s:.1f}s"
        return repo, [warning], True, None


def _emit_timing_log(project: ProjectConfig, mode: str, elapsed_ms: float, stale: bool) -> None:
    """Emit the ``OVERLEAF_TIMING=1`` log line as JSON.

    Format (stable interface — see docs/API.md#observability):
        acquire_project {"project":"<id>","mode":"read|write","elapsed_ms":N.N,"stale":bool}

    JSON keeps the line parseable by downstream log pipelines without
    regex gymnastics. The ``acquire_project`` prefix is preserved so
    existing greps still locate the line.
    """
    # TODO(1.2.0 / HTTP transport): include the tool name in this payload.
    # Under stdio each MCP call flows through acquire_project exactly once,
    # so elapsed_ms is already effectively per-tool — the caller correlates
    # against the immediately preceding tool invocation. Once HTTP
    # multiplexes clients, concurrent calls against the same project become
    # indistinguishable by `mode` alone; add a `tool` field then.
    payload = {
        "project": project.project_id,
        "mode": mode,
        "elapsed_ms": round(elapsed_ms, 1),
        "stale": stale,
    }
    logger.info("acquire_project %s", json.dumps(payload))


@asynccontextmanager
async def acquire_project(
    project: ProjectConfig,
    *,
    force_pull: bool = False,
    mode: Literal["read", "write"] = "read",
) -> AsyncIterator[ToolContext]:
    """Acquire per-project RW lock, prepare the repo, yield a ``ToolContext``.

    This is the single entry point for every tool branch. Design:

      Phase 1 (refresh, exclusive): runs :func:`ensure_repo` under the
      writer lock. Holding exclusive here serializes concurrent pulls
      against the same project — necessary because two parallel pulls
      race on ``.git/HEAD`` and ``.git/index``. For TTL-cache-fresh calls
      the writer lock is held for ~µs (no I/O happens). On
      :class:`StaleRepoWarning` the local snapshot is served with a
      visible warning attached.

      Retry (transient pull failure): the writer lock is **released**
      before the backoff sleep and re-acquired for the second attempt,
      so a 0.5–1.5 s hiccup doesn't block every other tool call against
      the same project. This is why ``ensure_repo`` is invoked with
      ``_retry_sync=False`` — the async layer owns the retry pacing.

      Phase 2 (tool body): lock held per ``mode`` —
      ``mode="read"`` takes shared (multiple readers run concurrently);
      ``mode="write"`` holds the exclusive lock *continuously* from the
      refresh phase through the body, so no reader can slip in between
      refresh and write. The lock is never released between phases for
      writers — this is what preserves refresh→write atomicity even
      after the retry restructure.

    Mode defaults to ``read`` so any caller that omits the kwarg gets
    the safe-and-fast read path. Write tools must pass ``mode="write"``
    explicitly. Mixing ``force_pull=False`` with ``mode="write"`` is
    permitted but unusual — write tools normally pair both flags.

    Observability: when ``OVERLEAF_TIMING=1`` is set, emits a JSON log
    line on context exit with project, mode, elapsed_ms, and stale flag.
    Costs nothing when off (one env-var lookup per call). Format is
    documented in docs/API.md#observability as a stable interface.
    """
    rwlock = _rwlock_for(project.project_id)
    timing_on = os.environ.get("OVERLEAF_TIMING") == "1"
    started = time.monotonic() if timing_on else 0.0
    stale = False

    try:
        # --- First refresh attempt under exclusive lock ---
        async with rwlock.exclusive():
            repo, warnings, stale, transient_msg = await _refresh_once(
                project, force_pull=force_pull
            )
            if transient_msg is None and mode == "write":
                # Happy path / permanent stale fallback, write mode:
                # hold the exclusive lock straight through into the body
                # so no reader can slip in between refresh and write.
                # Type-narrowing assertion; _refresh_once's contract guarantees
                # non-None repo when transient_msg is None.
                assert repo is not None  # nosec B101
                yield ToolContext(repo=repo, warnings=warnings)
                return

        # --- Retry path: first attempt was transient. Sleep OUTSIDE the
        # lock so the 0.5–1.5 s backoff doesn't starve concurrent tools
        # that would otherwise be blocked on the writer lock. ---
        if transient_msg is not None:
            # Bandit B311 rationale: jitter for retry backoff, not a security primitive.
            delay = random.uniform(*_RETRY_DELAY_RANGE)  # nosec B311
            logger.info(
                "pull failed transiently for %s (retry in %.2fs): %s",
                project.project_id,
                delay,
                transient_msg,
            )
            await asyncio.sleep(delay)

            async with rwlock.exclusive():
                repo, warnings, stale, transient2 = await _refresh_once(
                    project, force_pull=force_pull
                )
                if transient2 is not None:
                    # Second transient failure → promote to stale fallback.
                    logger.warning(
                        "pull failed twice for %s (giving up): %s",
                        project.project_id,
                        transient2,
                    )
                    repo = Repo(get_repo_path(project.project_id))
                    warnings = [f"⚠ could not refresh from Overleaf: {transient2}"]
                    stale = True
                if mode == "write":
                    assert repo is not None  # nosec B101  # see note above
                    yield ToolContext(repo=repo, warnings=warnings)
                    return

        # --- Read-mode body under shared lock ---
        # Type-narrowing; read-mode fallthrough always sets repo.
        assert repo is not None  # nosec B101
        async with rwlock.shared():
            yield ToolContext(repo=repo, warnings=warnings)
    finally:
        if timing_on:
            elapsed_ms = (time.monotonic() - started) * 1000.0
            _emit_timing_log(project, mode, elapsed_ms, stale)


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
