#!/usr/bin/env python3
"""
Overleaf MCP Server - Full CRUD operations for Overleaf projects.

This server provides comprehensive tools for working with Overleaf projects:
- Create: New projects via Overleaf API, new files in existing projects
- Read: List projects/files, read content, parse LaTeX sections
- Update: Edit files with git commit/push, update sections
- Delete: Remove files from projects

Uses Git integration for existing projects and Overleaf API for creating new ones.
"""

import asyncio
import base64
import json
import logging
import os
import re
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import quote

from git import Repo, GitCommandError
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
)
from pydantic import BaseModel

_T = TypeVar("_T")


# Structured logger. Level/handlers are caller-controlled (the MCP harness
# typically wires stderr). We log cache decisions at DEBUG, soft failures
# (StaleRepoWarning, timeouts) at WARNING, and never print warning text
# directly to stdout — stdout is the MCP stdio protocol stream.
logger = logging.getLogger("overleaf_mcp")


# Configuration
CONFIG_FILE = os.environ.get("OVERLEAF_CONFIG_FILE", "overleaf_config.json")
TEMP_DIR = os.environ.get("OVERLEAF_TEMP_DIR", "./overleaf_cache")
OVERLEAF_API_URL = "https://www.overleaf.com/docs"
# Base URL for Overleaf's Git endpoint. Overridable via env primarily for
# testing (point at a local file:// bare repo) but also for self-hosted
# Overleaf deployments where the Git host differs from git.overleaf.com.
OVERLEAF_GIT_URL = os.environ.get("OVERLEAF_GIT_URL", "https://git.overleaf.com")

# Performance / stability knobs (all env-overridable)
#
# OVERLEAF_PULL_TTL: seconds within which a successful pull is considered
#   fresh enough to skip on subsequent reads. Write tools always pass
#   force_pull=True and bypass this cache. Default 30s — short enough that
#   interactive edits round-trip quickly, long enough to amortize bursts
#   of read tools from an agent exploring a project.
# OVERLEAF_GIT_TIMEOUT: hard upper bound (seconds) on any blocking Git
#   operation. Protects the stdio reader from hanging forever on a
#   network black-hole.
_DEFAULT_PULL_TTL = 30.0
_DEFAULT_GIT_TIMEOUT = 60.0

# Low-speed abort: if Git can't sustain LIMIT bytes/sec for TIME seconds,
# it terminates. Belt-and-braces with the asyncio.wait_for ceiling.
os.environ.setdefault("GIT_HTTP_LOW_SPEED_LIMIT", "1000")
os.environ.setdefault("GIT_HTTP_LOW_SPEED_TIME", "30")

# LaTeX section patterns
SECTION_PATTERN = re.compile(
    r"\\(part|chapter|section|subsection|subsubsection|paragraph|subparagraph)\*?\{([^}]+)\}",
    re.MULTILINE,
)


class StaleRepoWarning(Exception):
    """Signal that we couldn't refresh from upstream but have a local snapshot.

    The dispatcher catches this, serves the tool's response against the stale
    local copy, and appends a visible ``⚠ could not refresh`` line so callers
    aren't misled into thinking they have live data.
    """


# Module-level state. Process-local, no cross-invocation persistence.
#
# _CONFIG_CACHE: (mtime, Config) — invalidated on overleaf_config.json change.
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
#                GitPython's index (which is not thread-safe). Lazily
#                initialized in _lock_for(); never cleared — lock objects
#                are cheap and projects don't churn in practice.
_CONFIG_CACHE: tuple[float, "Config"] | None = None
_LAST_PULL: dict[str, float] = {}
_PROJECT_LOCKS: dict[str, asyncio.Lock] = {}


def _lock_for(project_id: str) -> asyncio.Lock:
    """Return the per-project lock, creating it on first access."""
    lock = _PROJECT_LOCKS.get(project_id)
    if lock is None:
        lock = asyncio.Lock()
        _PROJECT_LOCKS[project_id] = lock
    return lock


class ProjectConfig(BaseModel):
    """Configuration for an Overleaf project."""
    name: str
    project_id: str
    git_token: str


class Config(BaseModel):
    """Server configuration."""
    projects: dict[str, ProjectConfig]
    default_project: str | None = None


def _parse_config_file(config_path: Path) -> Config:
    """Parse overleaf_config.json into a Config. Pure function, no side effects."""
    with open(config_path) as f:
        data = json.load(f)

    projects = {}
    for key, proj in data.get("projects", {}).items():
        projects[key] = ProjectConfig(
            name=proj.get("name", key),
            project_id=proj["projectId"],
            git_token=proj["gitToken"],
        )

    return Config(
        projects=projects,
        default_project=data.get("defaultProject"),
    )


def _env_config() -> Config:
    """Build a Config from OVERLEAF_PROJECT_ID / OVERLEAF_GIT_TOKEN, or empty."""
    project_id = os.environ.get("OVERLEAF_PROJECT_ID")
    git_token = os.environ.get("OVERLEAF_GIT_TOKEN")

    if project_id and git_token:
        return Config(
            projects={
                "default": ProjectConfig(
                    name="Default Project",
                    project_id=project_id,
                    git_token=git_token,
                )
            },
            default_project="default",
        )

    return Config(projects={})


def load_config() -> Config:
    """Load configuration from file or environment.

    The file path is cached by mtime — an unchanged file parses exactly once
    regardless of how many tool calls run. Environment-variable fallback is
    not cached (env mutations are opaque to us; re-reading is cheap anyway).
    """
    global _CONFIG_CACHE
    config_path = Path(CONFIG_FILE)

    if config_path.exists():
        mtime = config_path.stat().st_mtime
        if _CONFIG_CACHE is not None and _CONFIG_CACHE[0] == mtime:
            logger.debug("config cache hit (mtime=%s)", mtime)
            return _CONFIG_CACHE[1]
        logger.debug("config cache miss — parsing %s", config_path)
        cfg = _parse_config_file(config_path)
        _CONFIG_CACHE = (mtime, cfg)
        return cfg

    logger.debug("config file absent, using env-var fallback")
    return _env_config()


def get_project_config(project_name: str | None = None) -> ProjectConfig:
    """Get configuration for a specific project."""
    config = load_config()

    if not config.projects:
        raise ValueError(
            "No projects configured. Create overleaf_config.json or set "
            "OVERLEAF_PROJECT_ID and OVERLEAF_GIT_TOKEN environment variables."
        )

    if project_name is None:
        project_name = config.default_project or next(iter(config.projects.keys()))

    if project_name not in config.projects:
        available = ", ".join(config.projects.keys())
        raise ValueError(f"Project '{project_name}' not found. Available: {available}")

    return config.projects[project_name]


def resolve_project(
    project_name: str | None = None,
    git_token: str | None = None,
    project_id: str | None = None,
) -> ProjectConfig:
    """Resolve project config from inline credentials or config file."""
    if git_token or project_id:
        if not (git_token and project_id):
            raise ValueError(
                "Inline credentials require both 'git_token' and 'project_id'"
            )
        return ProjectConfig(name="inline", project_id=project_id, git_token=git_token)
    return get_project_config(project_name)


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
        logger.info("cloning project %s (cold start)", project.project_id)
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        repo = Repo.clone_from(git_url, repo_path)
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
        """Append any accumulated warnings to the tool's response string."""
        if not self.warnings:
            return result
        return result + "\n\n" + "\n".join(self.warnings)


@asynccontextmanager
async def acquire_project(
    project: ProjectConfig, *, force_pull: bool = False
):
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


def validate_path(base_path: Path, target_path: str) -> Path:
    """Validate that target path doesn't escape the repository."""
    resolved = (base_path / target_path).resolve()
    if not str(resolved).startswith(str(base_path.resolve())):
        raise ValueError(f"Path '{target_path}' escapes repository root")
    return resolved


def parse_sections(content: str) -> list[dict[str, Any]]:
    """Parse LaTeX content to extract sections."""
    sections = []
    matches = list(SECTION_PATTERN.finditer(content))

    for i, match in enumerate(matches):
        section_type = match.group(1)
        title = match.group(2)
        start_pos = match.end()

        # Find the end position (start of next section or end of content)
        end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(content)

        section_content = content[start_pos:end_pos].strip()
        preview = section_content[:200] + "..." if len(section_content) > 200 else section_content

        sections.append({
            "type": section_type,
            "title": title,
            "preview": preview,
            "start_pos": match.start(),
            "end_pos": end_pos,
        })

    return sections


def get_section_by_title(content: str, title: str) -> str | None:
    """Get the full content of a section by its title."""
    sections = parse_sections(content)

    for section in sections:
        if section["title"].lower() == title.lower():
            return content[section["start_pos"]:section["end_pos"]]

    return None


# Create the MCP server
server = Server("overleaf-mcp")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List all available tools."""
    return [
        # === CREATE OPERATIONS ===
        Tool(
            name="create_project",
            description=(
                "Create a new Overleaf project from LaTeX content. "
                "The project will open in Overleaf's web interface. "
                "Returns a URL to the new project."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "LaTeX content for the project (raw .tex content or base64-encoded zip)",
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Optional name for the project",
                    },
                    "engine": {
                        "type": "string",
                        "enum": ["pdflatex", "xelatex", "lualatex", "latex_dvipdf"],
                        "description": "TeX engine to use (default: pdflatex)",
                    },
                    "is_zip": {
                        "type": "boolean",
                        "description": "If true, content is base64-encoded zip file",
                    },
                },
                "required": ["content"],
            },
        ),
        Tool(
            name="create_file",
            description=(
                "Create a new file in an existing Overleaf project. "
                "Commits and pushes the changes immediately."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path for the new file (e.g., 'chapters/intro.tex')",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content for the new file",
                    },
                    "commit_message": {
                        "type": "string",
                        "description": "Git commit message",
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Project identifier from config (uses default if not specified)",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "If true, report what would happen without making changes (default: false)",
                    },
                    "push": {
                        "type": "boolean",
                        "description": "Whether to push after committing (default: true)",
                    },
                    "git_token": {
                        "type": "string",
                        "description": "Git token override (bypasses config file)",
                    },
                    "project_id": {
                        "type": "string",
                        "description": "Project ID override (bypasses config file)",
                    },
                },
                "required": ["file_path", "content"],
            },
        ),

        # === READ OPERATIONS ===
        Tool(
            name="list_projects",
            description="List all configured Overleaf projects.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="list_files",
            description="List files in an Overleaf project.",
            inputSchema={
                "type": "object",
                "properties": {
                    "extension": {
                        "type": "string",
                        "description": "Filter by file extension (e.g., '.tex', '.bib'). Leave empty for all files.",
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Project identifier from config (uses default if not specified)",
                    },
                    "git_token": {
                        "type": "string",
                        "description": "Git token override (bypasses config file)",
                    },
                    "project_id": {
                        "type": "string",
                        "description": "Project ID override (bypasses config file)",
                    },
                },
            },
        ),
        Tool(
            name="read_file",
            description="Read the content of a file from an Overleaf project.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file within the project",
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Project identifier from config (uses default if not specified)",
                    },
                    "git_token": {
                        "type": "string",
                        "description": "Git token override (bypasses config file)",
                    },
                    "project_id": {
                        "type": "string",
                        "description": "Project ID override (bypasses config file)",
                    },
                },
                "required": ["file_path"],
            },
        ),
        Tool(
            name="get_sections",
            description=(
                "Parse a LaTeX file and extract its section structure. "
                "Returns section types, titles, and content previews."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the LaTeX file",
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Project identifier from config (uses default if not specified)",
                    },
                    "git_token": {
                        "type": "string",
                        "description": "Git token override (bypasses config file)",
                    },
                    "project_id": {
                        "type": "string",
                        "description": "Project ID override (bypasses config file)",
                    },
                },
                "required": ["file_path"],
            },
        ),
        Tool(
            name="get_section_content",
            description="Get the full content of a specific section by its title.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the LaTeX file",
                    },
                    "section_title": {
                        "type": "string",
                        "description": "Title of the section to retrieve",
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Project identifier from config (uses default if not specified)",
                    },
                    "git_token": {
                        "type": "string",
                        "description": "Git token override (bypasses config file)",
                    },
                    "project_id": {
                        "type": "string",
                        "description": "Project ID override (bypasses config file)",
                    },
                },
                "required": ["file_path", "section_title"],
            },
        ),
        Tool(
            name="list_history",
            description="Show git commit history for the project.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of commits to show (default: 20, max: 200)",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Filter history to a specific file",
                    },
                    "since": {
                        "type": "string",
                        "description": "Show commits after this date (e.g., '2025-01-01', '2.weeks')",
                    },
                    "until": {
                        "type": "string",
                        "description": "Show commits before this date (e.g., '2025-06-01', '1.month')",
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Project identifier from config (uses default if not specified)",
                    },
                    "git_token": {
                        "type": "string",
                        "description": "Git token override (bypasses config file)",
                    },
                    "project_id": {
                        "type": "string",
                        "description": "Project ID override (bypasses config file)",
                    },
                },
            },
        ),
        Tool(
            name="get_diff",
            description="Get git diff for the project or specific files.",
            inputSchema={
                "type": "object",
                "properties": {
                    "from_ref": {
                        "type": "string",
                        "description": "Starting reference (commit hash, branch, or 'HEAD~n')",
                    },
                    "to_ref": {
                        "type": "string",
                        "description": "Ending reference (default: working tree)",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Filter diff to a specific file",
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter diff to multiple files",
                    },
                    "context_lines": {
                        "type": "integer",
                        "description": "Number of context lines in unified diff (0-10, default: 3)",
                    },
                    "max_output_chars": {
                        "type": "integer",
                        "description": "Truncate diff output to this many characters (default: 120000)",
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Project identifier from config (uses default if not specified)",
                    },
                    "git_token": {
                        "type": "string",
                        "description": "Git token override (bypasses config file)",
                    },
                    "project_id": {
                        "type": "string",
                        "description": "Project ID override (bypasses config file)",
                    },
                },
            },
        ),

        Tool(
            name="status_summary",
            description="Get a comprehensive project status summary including file counts, last commit, and document structure.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_name": {
                        "type": "string",
                        "description": "Project identifier from config (uses default if not specified)",
                    },
                    "git_token": {
                        "type": "string",
                        "description": "Git token override (bypasses config file)",
                    },
                    "project_id": {
                        "type": "string",
                        "description": "Project ID override (bypasses config file)",
                    },
                },
            },
        ),

        # === UPDATE OPERATIONS ===
        Tool(
            name="edit_file",
            description=(
                "Make surgical edits to a file by replacing specific text. "
                "The old_string must match exactly (including whitespace). "
                "Commits and pushes immediately."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to edit",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "The exact text to find and replace",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "The text to replace it with",
                    },
                    "commit_message": {
                        "type": "string",
                        "description": "Git commit message",
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Project identifier from config (uses default if not specified)",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "If true, report what would happen without making changes (default: false)",
                    },
                    "push": {
                        "type": "boolean",
                        "description": "Whether to push after committing (default: true)",
                    },
                    "git_token": {
                        "type": "string",
                        "description": "Git token override (bypasses config file)",
                    },
                    "project_id": {
                        "type": "string",
                        "description": "Project ID override (bypasses config file)",
                    },
                },
                "required": ["file_path", "old_string", "new_string"],
            },
        ),
        Tool(
            name="rewrite_file",
            description=(
                "Replace entire file contents. "
                "Commits and pushes immediately."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to rewrite",
                    },
                    "content": {
                        "type": "string",
                        "description": "New content for the entire file",
                    },
                    "commit_message": {
                        "type": "string",
                        "description": "Git commit message",
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Project identifier from config (uses default if not specified)",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "If true, report what would happen without making changes (default: false)",
                    },
                    "push": {
                        "type": "boolean",
                        "description": "Whether to push after committing (default: true)",
                    },
                    "git_token": {
                        "type": "string",
                        "description": "Git token override (bypasses config file)",
                    },
                    "project_id": {
                        "type": "string",
                        "description": "Project ID override (bypasses config file)",
                    },
                },
                "required": ["file_path", "content"],
            },
        ),
        Tool(
            name="update_section",
            description=(
                "Update a specific section in a LaTeX file by its title. "
                "Commits and pushes immediately."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the LaTeX file",
                    },
                    "section_title": {
                        "type": "string",
                        "description": "Title of the section to update",
                    },
                    "new_content": {
                        "type": "string",
                        "description": "New content for the section (excluding the section header)",
                    },
                    "commit_message": {
                        "type": "string",
                        "description": "Git commit message",
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Project identifier from config (uses default if not specified)",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "If true, report what would happen without making changes (default: false)",
                    },
                    "push": {
                        "type": "boolean",
                        "description": "Whether to push after committing (default: true)",
                    },
                    "git_token": {
                        "type": "string",
                        "description": "Git token override (bypasses config file)",
                    },
                    "project_id": {
                        "type": "string",
                        "description": "Project ID override (bypasses config file)",
                    },
                },
                "required": ["file_path", "section_title", "new_content"],
            },
        ),
        Tool(
            name="sync_project",
            description="Sync the local project with Overleaf (pull latest changes).",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_name": {
                        "type": "string",
                        "description": "Project identifier from config (uses default if not specified)",
                    },
                    "git_token": {
                        "type": "string",
                        "description": "Git token override (bypasses config file)",
                    },
                    "project_id": {
                        "type": "string",
                        "description": "Project ID override (bypasses config file)",
                    },
                },
            },
        ),

        # === DELETE OPERATIONS ===
        Tool(
            name="delete_file",
            description=(
                "Delete a file from an Overleaf project. "
                "Commits and pushes immediately."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to delete",
                    },
                    "commit_message": {
                        "type": "string",
                        "description": "Git commit message",
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Project identifier from config (uses default if not specified)",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "If true, report what would happen without making changes (default: false)",
                    },
                    "push": {
                        "type": "boolean",
                        "description": "Whether to push after committing (default: true)",
                    },
                    "git_token": {
                        "type": "string",
                        "description": "Git token override (bypasses config file)",
                    },
                    "project_id": {
                        "type": "string",
                        "description": "Project ID override (bypasses config file)",
                    },
                },
                "required": ["file_path"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls."""
    try:
        result = await execute_tool(name, arguments)
        return [TextContent(type="text", text=result)]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


async def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """Execute a tool and return the result."""

    # === CREATE OPERATIONS ===

    if name == "create_project":
        content = arguments["content"]
        project_name = arguments.get("project_name")
        engine = arguments.get("engine", "pdflatex")
        is_zip = arguments.get("is_zip", False)

        # Build the data URL
        if is_zip:
            mime_type = "application/zip"
            data = content  # Already base64 encoded
        else:
            mime_type = "application/x-tex"
            data = base64.b64encode(content.encode()).decode()

        snip_uri = f"data:{mime_type};base64,{data}"

        # Build form data
        form_data = {
            "snip_uri": snip_uri,
            "engine": engine,
        }
        if project_name:
            form_data["snip_name"] = project_name

        # Note: This creates a project in the user's browser, not directly via API
        # We return the URL for the user to open
        params = "&".join(f"{k}={quote(str(v))}" for k, v in form_data.items())

        return (
            f"To create the project, open this URL in your browser:\n\n"
            f"{OVERLEAF_API_URL}?{params}\n\n"
            f"Or use the following form data to POST to {OVERLEAF_API_URL}:\n"
            f"- snip_uri: {snip_uri[:100]}...\n"
            f"- engine: {engine}"
        )

    elif name == "create_file":
        project = resolve_project(
            arguments.get("project_name"),
            arguments.get("git_token"),
            arguments.get("project_id"),
        )
        repo_path = get_repo_path(project.project_id)

        file_path = arguments["file_path"]
        content = arguments["content"]
        commit_message = arguments.get("commit_message", f"Add {file_path}")
        dry_run = arguments.get("dry_run", False)
        push = arguments.get("push", True)

        # Lock held through commit+push so no other tool can race on the
        # index. force_pull=True ensures we commit on the latest base.
        async with acquire_project(project, force_pull=True) as ctx:
            target_path = validate_path(repo_path, file_path)

            if target_path.exists():
                return ctx.wrap(
                    f"Error: File '{file_path}' already exists. "
                    f"Use edit_file to modify it."
                )

            if dry_run:
                return ctx.wrap(
                    f"Dry run: would create '{file_path}'\n"
                    f"Content size: {len(content)} chars\n"
                    f"No changes were written."
                )

            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(content)

            config_git_user(ctx.repo)
            ctx.repo.index.add([file_path])
            await _run_blocking(ctx.repo.index.commit, commit_message)
            if push:
                await _run_blocking(ctx.repo.remotes.origin.push)

            return ctx.wrap(
                f"Created{' and pushed' if push else ''} '{file_path}'"
            )

    # === READ OPERATIONS ===

    elif name == "list_projects":
        config = load_config()

        if not config.projects:
            return (
                "No projects configured.\n\n"
                "Create 'overleaf_config.json' with:\n"
                "{\n"
                '  "projects": {\n'
                '    "my-project": {\n'
                '      "name": "My Project",\n'
                '      "projectId": "YOUR_PROJECT_ID",\n'
                '      "gitToken": "YOUR_GIT_TOKEN"\n'
                "    }\n"
                "  }\n"
                "}\n\n"
                "Or set OVERLEAF_PROJECT_ID and OVERLEAF_GIT_TOKEN environment variables."
            )

        lines = ["Configured projects:"]
        for key, proj in config.projects.items():
            default_marker = " (default)" if key == config.default_project else ""
            lines.append(f"  - {key}: {proj.name}{default_marker}")

        return "\n".join(lines)

    elif name == "list_files":
        project = resolve_project(
            arguments.get("project_name"),
            arguments.get("git_token"),
            arguments.get("project_id"),
        )
        repo_path = get_repo_path(project.project_id)
        extension = arguments.get("extension", "")

        async with acquire_project(project, force_pull=False) as ctx:
            files = []
            for path in repo_path.rglob("*"):
                if path.is_file() and not any(part.startswith(".") for part in path.parts):
                    rel_path = path.relative_to(repo_path)
                    if not extension or path.suffix == extension:
                        files.append(str(rel_path))

            files.sort()

            if not files:
                return ctx.wrap(
                    f"No files found{' with extension ' + extension if extension else ''}"
                )

            return ctx.wrap(
                f"Files in project '{project.name}':\n"
                + "\n".join(f"  - {f}" for f in files)
            )

    elif name == "read_file":
        project = resolve_project(
            arguments.get("project_name"),
            arguments.get("git_token"),
            arguments.get("project_id"),
        )
        repo_path = get_repo_path(project.project_id)
        file_path = arguments["file_path"]

        async with acquire_project(project, force_pull=False) as ctx:
            target_path = validate_path(repo_path, file_path)

            if not target_path.exists():
                return ctx.wrap(f"Error: File '{file_path}' not found")

            content = target_path.read_text()
            return ctx.wrap(f"Content of '{file_path}':\n\n{content}")

    elif name == "get_sections":
        project = resolve_project(
            arguments.get("project_name"),
            arguments.get("git_token"),
            arguments.get("project_id"),
        )
        repo_path = get_repo_path(project.project_id)
        file_path = arguments["file_path"]

        async with acquire_project(project, force_pull=False) as ctx:
            target_path = validate_path(repo_path, file_path)

            if not target_path.exists():
                return ctx.wrap(f"Error: File '{file_path}' not found")

            content = target_path.read_text()
            sections = parse_sections(content)

            if not sections:
                return ctx.wrap(f"No sections found in '{file_path}'")

            lines = [f"Sections in '{file_path}':"]
            for s in sections:
                lines.append(f"\n[{s['type']}] {s['title']}")
                lines.append(f"  Preview: {s['preview'][:100]}...")

            return ctx.wrap("\n".join(lines))

    elif name == "get_section_content":
        project = resolve_project(
            arguments.get("project_name"),
            arguments.get("git_token"),
            arguments.get("project_id"),
        )
        repo_path = get_repo_path(project.project_id)
        file_path = arguments["file_path"]
        section_title = arguments["section_title"]

        async with acquire_project(project, force_pull=False) as ctx:
            target_path = validate_path(repo_path, file_path)

            if not target_path.exists():
                return ctx.wrap(f"Error: File '{file_path}' not found")

            content = target_path.read_text()
            section_content = get_section_by_title(content, section_title)

            if section_content is None:
                sections = parse_sections(content)
                available = ", ".join(f"'{s['title']}'" for s in sections)
                return ctx.wrap(
                    f"Section '{section_title}' not found. "
                    f"Available sections: {available}"
                )

            return ctx.wrap(
                f"Content of section '{section_title}':\n\n{section_content}"
            )

    elif name == "list_history":
        project = resolve_project(
            arguments.get("project_name"),
            arguments.get("git_token"),
            arguments.get("project_id"),
        )
        limit = min(arguments.get("limit", 20), 200)
        file_path = arguments.get("file_path")
        since = arguments.get("since")
        until = arguments.get("until")

        kwargs: dict[str, Any] = {"max_count": limit}
        if file_path:
            kwargs["paths"] = file_path
        if since:
            kwargs["after"] = since
        if until:
            kwargs["before"] = until

        async with acquire_project(project, force_pull=False) as ctx:
            # iter_commits walks the object db via subprocess — off the loop.
            commits = await _run_blocking(
                lambda: list(ctx.repo.iter_commits(**kwargs))
            )

            if not commits:
                return ctx.wrap("No commits found")

            lines = [f"Commit history (showing {len(commits)}/{limit}):"]
            for c in commits:
                date = c.committed_datetime.strftime("%Y-%m-%d %H:%M")
                lines.append(f"\n{c.hexsha[:8]} | {date} | {c.author.name}")
                lines.append(f"  {c.message.strip()[:100]}")

            return ctx.wrap("\n".join(lines))

    elif name == "get_diff":
        project = resolve_project(
            arguments.get("project_name"),
            arguments.get("git_token"),
            arguments.get("project_id"),
        )
        from_ref = arguments.get("from_ref", "HEAD")
        to_ref = arguments.get("to_ref")
        context_lines = max(0, min(10, arguments.get("context_lines", 3)))
        max_output_chars = max(2000, min(500000, arguments.get("max_output_chars", 120000)))

        # Collect path filters (single file_path or multiple paths)
        path_filters: list[str] = []
        if arguments.get("file_path"):
            path_filters.append(arguments["file_path"])
        if arguments.get("paths"):
            path_filters.extend(arguments["paths"])

        diff_args = [f"-U{context_lines}"]
        if to_ref:
            diff_args.extend([from_ref, to_ref])
        else:
            diff_args.append(from_ref)

        if path_filters:
            diff_args.append("--")
            diff_args.extend(path_filters)

        async with acquire_project(project, force_pull=False) as ctx:
            try:
                # repo.git.diff shells out to `git diff` — off the loop, with timeout.
                diff = await _run_blocking(ctx.repo.git.diff, *diff_args)
            except GitCommandError as e:
                return ctx.wrap(f"Error getting diff: {e}")

            if not diff:
                return ctx.wrap("No differences found")

            truncated = len(diff) > max_output_chars
            if truncated:
                diff = diff[:max_output_chars]

            result = f"Diff:\n\n{diff}"
            if truncated:
                result += "\n\n[diff truncated]"
            return ctx.wrap(result)

    elif name == "status_summary":
        project = resolve_project(
            arguments.get("project_name"),
            arguments.get("git_token"),
            arguments.get("project_id"),
        )
        repo_path = get_repo_path(project.project_id)

        async with acquire_project(project, force_pull=False) as ctx:
            # File inventory
            all_files = []
            tex_files = []
            for p in repo_path.rglob("*"):
                if p.is_file() and not any(part.startswith(".") for part in p.parts):
                    rel = str(p.relative_to(repo_path))
                    all_files.append(rel)
                    if p.suffix == ".tex":
                        tex_files.append(rel)

            # Latest commit
            try:
                head = ctx.repo.head.commit
                last_commit = (
                    f"{head.hexsha[:8]} | "
                    f"{head.committed_datetime.strftime('%Y-%m-%d %H:%M')} | "
                    f"{head.author.name} | {head.message.strip()[:80]}"
                )
            except ValueError:
                last_commit = "(no commits)"

            # Branch
            try:
                branch = ctx.repo.active_branch.name
            except TypeError:
                branch = "(detached HEAD)"

            # Section structure of main .tex file
            structure_lines = []
            main_tex = None
            for tf in tex_files:
                full = repo_path / tf
                text = full.read_text(errors="replace")
                if r"\documentclass" in text or r"\begin{document}" in text:
                    main_tex = tf
                    sections = parse_sections(text)
                    for s in sections:
                        structure_lines.append(f"  [{s['type']}] {s['title']}")
                    break

            lines = [
                f"Project: {project.name}",
                f"Branch: {branch}",
                f"Files: {len(all_files)} total, {len(tex_files)} .tex",
                f"Last commit: {last_commit}",
            ]
            if main_tex:
                lines.append(f"\nMain document: {main_tex}")
                if structure_lines:
                    lines.append("Sections:")
                    lines.extend(structure_lines)
                else:
                    lines.append("(no sections found)")
            else:
                lines.append("\n(no main .tex file detected)")

            return ctx.wrap("\n".join(lines))

    # === UPDATE OPERATIONS ===

    elif name == "edit_file":
        project = resolve_project(
            arguments.get("project_name"),
            arguments.get("git_token"),
            arguments.get("project_id"),
        )
        repo_path = get_repo_path(project.project_id)
        file_path = arguments["file_path"]
        old_string = arguments["old_string"]
        new_string = arguments["new_string"]
        commit_message = arguments.get("commit_message", f"Edit {file_path}")
        dry_run = arguments.get("dry_run", False)
        push = arguments.get("push", True)

        async with acquire_project(project, force_pull=True) as ctx:
            target_path = validate_path(repo_path, file_path)

            if not target_path.exists():
                return ctx.wrap(f"Error: File '{file_path}' not found")

            content = target_path.read_text()

            if old_string not in content:
                preview = content[:500] + "..." if len(content) > 500 else content
                return ctx.wrap(
                    f"Error: old_string not found in '{file_path}'. "
                    f"File preview:\n{preview}"
                )

            count = content.count(old_string)
            if count > 1:
                return ctx.wrap(
                    f"Error: old_string appears {count} times in '{file_path}'. "
                    f"Make it more specific to match exactly once."
                )

            if dry_run:
                return ctx.wrap(
                    f"Dry run: would edit '{file_path}'\n"
                    f"Replacing {len(old_string)} chars with {len(new_string)} chars\n"
                    f"No changes were written."
                )

            new_content = content.replace(old_string, new_string, 1)
            target_path.write_text(new_content)

            config_git_user(ctx.repo)
            ctx.repo.index.add([file_path])
            await _run_blocking(ctx.repo.index.commit, commit_message)
            if push:
                await _run_blocking(ctx.repo.remotes.origin.push)

            return ctx.wrap(
                f"Edited{' and pushed' if push else ''} '{file_path}'"
            )

    elif name == "rewrite_file":
        project = resolve_project(
            arguments.get("project_name"),
            arguments.get("git_token"),
            arguments.get("project_id"),
        )
        repo_path = get_repo_path(project.project_id)
        file_path = arguments["file_path"]
        content = arguments["content"]
        commit_message = arguments.get("commit_message", f"Rewrite {file_path}")
        dry_run = arguments.get("dry_run", False)
        push = arguments.get("push", True)

        async with acquire_project(project, force_pull=True) as ctx:
            target_path = validate_path(repo_path, file_path)

            if not target_path.exists():
                return ctx.wrap(
                    f"Error: File '{file_path}' not found. "
                    f"Use create_file to create it."
                )

            if dry_run:
                existing_size = target_path.stat().st_size
                return ctx.wrap(
                    f"Dry run: would rewrite '{file_path}'\n"
                    f"Existing size: {existing_size} bytes\n"
                    f"New size: {len(content)} chars\n"
                    f"No changes were written."
                )

            target_path.write_text(content)

            config_git_user(ctx.repo)
            ctx.repo.index.add([file_path])
            await _run_blocking(ctx.repo.index.commit, commit_message)
            if push:
                await _run_blocking(ctx.repo.remotes.origin.push)

            return ctx.wrap(
                f"Rewrote{' and pushed' if push else ''} '{file_path}'"
            )

    elif name == "update_section":
        project = resolve_project(
            arguments.get("project_name"),
            arguments.get("git_token"),
            arguments.get("project_id"),
        )
        repo_path = get_repo_path(project.project_id)
        file_path = arguments["file_path"]
        section_title = arguments["section_title"]
        new_content = arguments["new_content"]
        commit_message = arguments.get(
            "commit_message", f"Update section '{section_title}'"
        )
        dry_run = arguments.get("dry_run", False)
        push = arguments.get("push", True)

        async with acquire_project(project, force_pull=True) as ctx:
            target_path = validate_path(repo_path, file_path)

            if not target_path.exists():
                return ctx.wrap(f"Error: File '{file_path}' not found")

            content = target_path.read_text()
            sections = parse_sections(content)

            section = None
            for s in sections:
                if s["title"].lower() == section_title.lower():
                    section = s
                    break

            if section is None:
                available = ", ".join(f"'{s['title']}'" for s in sections)
                return ctx.wrap(
                    f"Section '{section_title}' not found. "
                    f"Available sections: {available}"
                )

            header_match = re.search(
                rf"\\{section['type']}\*?\{{{re.escape(section['title'])}\}}",
                content
            )
            if not header_match:
                return ctx.wrap(
                    f"Could not locate section header for '{section_title}'"
                )

            if dry_run:
                old_len = section["end_pos"] - header_match.end()
                return ctx.wrap(
                    f"Dry run: would update section '{section_title}' in '{file_path}'\n"
                    f"Old section body: {old_len} chars\n"
                    f"New section body: {len(new_content)} chars\n"
                    f"No changes were written."
                )

            header_end = header_match.end()
            new_file_content = (
                content[:header_end] +
                "\n" + new_content.strip() + "\n" +
                content[section["end_pos"]:]
            )

            target_path.write_text(new_file_content)

            config_git_user(ctx.repo)
            ctx.repo.index.add([file_path])
            await _run_blocking(ctx.repo.index.commit, commit_message)
            if push:
                await _run_blocking(ctx.repo.remotes.origin.push)

            return ctx.wrap(
                f"Updated section '{section_title}'{' and pushed' if push else ''}"
            )

    elif name == "sync_project":
        # sync_project is the explicit "refresh now" escape hatch. It always
        # force-pulls (ignoring the TTL cache) and surfaces errors hard rather
        # than falling back to stale state — the user asked for fresh data.
        #
        # Unlike the other tools, sync_project cannot use acquire_project()
        # directly: that helper swallows StaleRepoWarning and returns a
        # degraded ToolContext, whereas this tool's contract is to report
        # the refresh error to the caller. Instead we hold the per-project
        # lock manually (same serialization guarantee) and let the error
        # propagate out of ensure_repo.
        project = resolve_project(
            arguments.get("project_name"),
            arguments.get("git_token"),
            arguments.get("project_id"),
        )
        repo_path = get_repo_path(project.project_id)

        async with _lock_for(project.project_id):
            if not repo_path.exists():
                await _run_blocking(ensure_repo, project, force_pull=True)
                return f"Cloned project '{project.name}'"

            # is_dirty() is safe to call here because we hold the lock — no
            # other tool can be mid-commit against this working tree.
            repo = Repo(repo_path)
            if repo.is_dirty():
                return (
                    "Warning: Local changes exist. "
                    "Commit or discard them before syncing."
                )

            try:
                await _run_blocking(ensure_repo, project, force_pull=True)
                return f"Synced project '{project.name}' with Overleaf"
            except StaleRepoWarning as w:
                return f"Error syncing: {w}"
            except GitCommandError as e:
                return f"Error syncing: {e}"

    # === DELETE OPERATIONS ===

    elif name == "delete_file":
        project = resolve_project(
            arguments.get("project_name"),
            arguments.get("git_token"),
            arguments.get("project_id"),
        )
        repo_path = get_repo_path(project.project_id)
        file_path = arguments["file_path"]
        commit_message = arguments.get("commit_message", f"Delete {file_path}")
        dry_run = arguments.get("dry_run", False)
        push = arguments.get("push", True)

        async with acquire_project(project, force_pull=True) as ctx:
            target_path = validate_path(repo_path, file_path)

            if not target_path.exists():
                return ctx.wrap(f"Error: File '{file_path}' not found")

            if dry_run:
                size = target_path.stat().st_size
                return ctx.wrap(
                    f"Dry run: would delete '{file_path}' ({size} bytes)\n"
                    f"No changes were written."
                )

            config_git_user(ctx.repo)
            ctx.repo.index.remove([file_path])
            target_path.unlink()
            await _run_blocking(ctx.repo.index.commit, commit_message)
            if push:
                await _run_blocking(ctx.repo.remotes.origin.push)

            return ctx.wrap(
                f"Deleted{' and pushed' if push else ''} '{file_path}'"
            )

    else:
        return f"Unknown tool: {name}"


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


def main() -> None:
    """Run the MCP server."""

    async def run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    asyncio.run(run())


if __name__ == "__main__":
    main()
