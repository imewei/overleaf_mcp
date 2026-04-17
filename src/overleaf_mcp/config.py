"""Configuration loading and project resolution.

This is the data layer of the server: pure models + file-or-env loading,
with mtime-based caching so repeated tool calls don't re-parse an unchanged
config file. No Git, no network, no async — just pydantic.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Config file path + local cache directory. Both are env-overridable so
# users can run multiple server instances pointing at separate projects
# without editing code.
CONFIG_FILE = os.environ.get("OVERLEAF_CONFIG_FILE", "overleaf_config.json")
TEMP_DIR = os.environ.get("OVERLEAF_TEMP_DIR", "./overleaf_cache")


class ProjectConfig(BaseModel):
    """A single Overleaf project entry (name + id + auth token)."""
    name: str
    project_id: str
    git_token: str


class Config(BaseModel):
    """Top-level server configuration loaded from overleaf_config.json."""
    projects: dict[str, ProjectConfig]
    default_project: str | None = None


# Module-level cache for load_config(): (mtime, Config). Invalidated
# whenever overleaf_config.json's mtime changes. Environment-variable
# fallback is not cached (env mutations are opaque to us).
_CONFIG_CACHE: tuple[float, Config] | None = None


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
    """Resolve project config from inline credentials or config file.

    Inline credentials (both ``git_token`` AND ``project_id``) bypass the
    config file entirely — useful for testing and for clients that want
    to pass credentials per-call without writing them to disk.
    """
    if git_token or project_id:
        if not (git_token and project_id):
            raise ValueError(
                "Inline credentials require both 'git_token' and 'project_id'"
            )
        # Tag the name with a token-hash prefix so two different tenants
        # passing the same project_id via inline creds are distinguishable
        # in log lines and error messages. The hash never leaks the token
        # itself — 8 hex chars of SHA-256 are one-way and collision-space
        # of 2^32 is plenty for disambiguating concurrent callers.
        token_hash = hashlib.sha256(git_token.encode()).hexdigest()[:8]
        return ProjectConfig(
            name=f"inline-{token_hash}",
            project_id=project_id,
            git_token=git_token,
        )
    return get_project_config(project_name)
