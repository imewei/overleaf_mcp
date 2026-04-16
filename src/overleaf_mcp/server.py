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
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from git import Repo, GitCommandError
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
)
from pydantic import BaseModel


# Configuration
CONFIG_FILE = os.environ.get("OVERLEAF_CONFIG_FILE", "overleaf_config.json")
TEMP_DIR = os.environ.get("OVERLEAF_TEMP_DIR", "./overleaf_cache")
OVERLEAF_API_URL = "https://www.overleaf.com/docs"
OVERLEAF_GIT_URL = "https://git.overleaf.com"

# LaTeX section patterns
SECTION_PATTERN = re.compile(
    r"\\(part|chapter|section|subsection|subsubsection|paragraph|subparagraph)\*?\{([^}]+)\}",
    re.MULTILINE,
)


class ProjectConfig(BaseModel):
    """Configuration for an Overleaf project."""
    name: str
    project_id: str
    git_token: str


class Config(BaseModel):
    """Server configuration."""
    projects: dict[str, ProjectConfig]
    default_project: str | None = None


def load_config() -> Config:
    """Load configuration from file or environment."""
    config_path = Path(CONFIG_FILE)

    if config_path.exists():
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

    # Fallback to environment variables
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


def ensure_repo(project: ProjectConfig) -> Repo:
    """Ensure the repository is cloned and up to date."""
    repo_path = get_repo_path(project.project_id)
    git_url = f"https://git:{project.git_token}@git.overleaf.com/{project.project_id}"

    if repo_path.exists():
        repo = Repo(repo_path)
        # Pull latest changes
        try:
            origin = repo.remotes.origin
            origin.pull()
        except GitCommandError as e:
            # If pull fails, try to continue with existing state
            pass
        return repo

    # Clone the repository
    repo_path.parent.mkdir(parents=True, exist_ok=True)
    return Repo.clone_from(git_url, repo_path)


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
        if i + 1 < len(matches):
            end_pos = matches[i + 1].start()
        else:
            end_pos = len(content)

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
        project = get_project_config(arguments.get("project_name"))
        repo = ensure_repo(project)
        repo_path = get_repo_path(project.project_id)

        file_path = arguments["file_path"]
        content = arguments["content"]
        commit_message = arguments.get("commit_message", f"Add {file_path}")

        # Validate and create path
        target_path = validate_path(repo_path, file_path)

        if target_path.exists():
            return f"Error: File '{file_path}' already exists. Use edit_file to modify it."

        # Create parent directories if needed
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Write file
        target_path.write_text(content)

        # Configure git user if needed
        config_git_user(repo)

        # Git operations
        repo.index.add([file_path])
        repo.index.commit(commit_message)
        repo.remotes.origin.push()

        return f"Created and pushed '{file_path}'"

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
        project = get_project_config(arguments.get("project_name"))
        repo = ensure_repo(project)
        repo_path = get_repo_path(project.project_id)

        extension = arguments.get("extension", "")

        files = []
        for path in repo_path.rglob("*"):
            if path.is_file() and not any(part.startswith(".") for part in path.parts):
                rel_path = path.relative_to(repo_path)
                if not extension or path.suffix == extension:
                    files.append(str(rel_path))

        files.sort()

        if not files:
            return f"No files found{' with extension ' + extension if extension else ''}"

        return f"Files in project '{project.name}':\n" + "\n".join(f"  - {f}" for f in files)

    elif name == "read_file":
        project = get_project_config(arguments.get("project_name"))
        repo = ensure_repo(project)
        repo_path = get_repo_path(project.project_id)

        file_path = arguments["file_path"]
        target_path = validate_path(repo_path, file_path)

        if not target_path.exists():
            return f"Error: File '{file_path}' not found"

        content = target_path.read_text()
        return f"Content of '{file_path}':\n\n{content}"

    elif name == "get_sections":
        project = get_project_config(arguments.get("project_name"))
        repo = ensure_repo(project)
        repo_path = get_repo_path(project.project_id)

        file_path = arguments["file_path"]
        target_path = validate_path(repo_path, file_path)

        if not target_path.exists():
            return f"Error: File '{file_path}' not found"

        content = target_path.read_text()
        sections = parse_sections(content)

        if not sections:
            return f"No sections found in '{file_path}'"

        lines = [f"Sections in '{file_path}':"]
        for s in sections:
            lines.append(f"\n[{s['type']}] {s['title']}")
            lines.append(f"  Preview: {s['preview'][:100]}...")

        return "\n".join(lines)

    elif name == "get_section_content":
        project = get_project_config(arguments.get("project_name"))
        repo = ensure_repo(project)
        repo_path = get_repo_path(project.project_id)

        file_path = arguments["file_path"]
        section_title = arguments["section_title"]
        target_path = validate_path(repo_path, file_path)

        if not target_path.exists():
            return f"Error: File '{file_path}' not found"

        content = target_path.read_text()
        section_content = get_section_by_title(content, section_title)

        if section_content is None:
            sections = parse_sections(content)
            available = ", ".join(f"'{s['title']}'" for s in sections)
            return f"Section '{section_title}' not found. Available sections: {available}"

        return f"Content of section '{section_title}':\n\n{section_content}"

    elif name == "list_history":
        project = resolve_project(
            arguments.get("project_name"),
            arguments.get("git_token"),
            arguments.get("project_id"),
        )
        repo = ensure_repo(project)

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

        commits = list(repo.iter_commits(**kwargs))

        if not commits:
            return "No commits found"

        lines = [f"Commit history (showing {len(commits)}/{limit}):"]
        for c in commits:
            date = c.committed_datetime.strftime("%Y-%m-%d %H:%M")
            lines.append(f"\n{c.hexsha[:8]} | {date} | {c.author.name}")
            lines.append(f"  {c.message.strip()[:100]}")

        return "\n".join(lines)

    elif name == "get_diff":
        project = resolve_project(
            arguments.get("project_name"),
            arguments.get("git_token"),
            arguments.get("project_id"),
        )
        repo = ensure_repo(project)

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

        try:
            diff_args = [f"-U{context_lines}"]
            if to_ref:
                diff_args.extend([from_ref, to_ref])
            else:
                diff_args.append(from_ref)

            if path_filters:
                diff_args.append("--")
                diff_args.extend(path_filters)

            diff = repo.git.diff(*diff_args)
        except GitCommandError as e:
            return f"Error getting diff: {e}"

        if not diff:
            return "No differences found"

        truncated = len(diff) > max_output_chars
        if truncated:
            diff = diff[:max_output_chars]

        result = f"Diff:\n\n{diff}"
        if truncated:
            result += "\n\n[diff truncated]"
        return result

    elif name == "status_summary":
        project = resolve_project(
            arguments.get("project_name"),
            arguments.get("git_token"),
            arguments.get("project_id"),
        )
        repo = ensure_repo(project)
        repo_path = get_repo_path(project.project_id)

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
            head = repo.head.commit
            last_commit = (
                f"{head.hexsha[:8]} | "
                f"{head.committed_datetime.strftime('%Y-%m-%d %H:%M')} | "
                f"{head.author.name} | {head.message.strip()[:80]}"
            )
        except ValueError:
            last_commit = "(no commits)"

        # Branch
        try:
            branch = repo.active_branch.name
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

        return "\n".join(lines)

    # === UPDATE OPERATIONS ===

    elif name == "edit_file":
        project = get_project_config(arguments.get("project_name"))
        repo = ensure_repo(project)
        repo_path = get_repo_path(project.project_id)

        file_path = arguments["file_path"]
        old_string = arguments["old_string"]
        new_string = arguments["new_string"]
        commit_message = arguments.get("commit_message", f"Edit {file_path}")

        target_path = validate_path(repo_path, file_path)

        if not target_path.exists():
            return f"Error: File '{file_path}' not found"

        content = target_path.read_text()

        # Check if old_string exists
        if old_string not in content:
            # Show a preview of the file to help debug
            preview = content[:500] + "..." if len(content) > 500 else content
            return f"Error: old_string not found in '{file_path}'. File preview:\n{preview}"

        # Check for uniqueness
        count = content.count(old_string)
        if count > 1:
            return f"Error: old_string appears {count} times in '{file_path}'. Make it more specific to match exactly once."

        # Perform the replacement
        new_content = content.replace(old_string, new_string, 1)

        # Write file
        target_path.write_text(new_content)

        # Configure git user if needed
        config_git_user(repo)

        # Git operations
        repo.index.add([file_path])
        repo.index.commit(commit_message)
        repo.remotes.origin.push()

        return f"Edited and pushed '{file_path}'"

    elif name == "rewrite_file":
        project = get_project_config(arguments.get("project_name"))
        repo = ensure_repo(project)
        repo_path = get_repo_path(project.project_id)

        file_path = arguments["file_path"]
        content = arguments["content"]
        commit_message = arguments.get("commit_message", f"Rewrite {file_path}")

        target_path = validate_path(repo_path, file_path)

        if not target_path.exists():
            return f"Error: File '{file_path}' not found. Use create_file to create it."

        # Write file
        target_path.write_text(content)

        # Configure git user if needed
        config_git_user(repo)

        # Git operations
        repo.index.add([file_path])
        repo.index.commit(commit_message)
        repo.remotes.origin.push()

        return f"Rewrote and pushed '{file_path}'"

    elif name == "update_section":
        project = get_project_config(arguments.get("project_name"))
        repo = ensure_repo(project)
        repo_path = get_repo_path(project.project_id)

        file_path = arguments["file_path"]
        section_title = arguments["section_title"]
        new_content = arguments["new_content"]
        commit_message = arguments.get("commit_message", f"Update section '{section_title}'")

        target_path = validate_path(repo_path, file_path)

        if not target_path.exists():
            return f"Error: File '{file_path}' not found"

        content = target_path.read_text()
        sections = parse_sections(content)

        # Find the section
        section = None
        for s in sections:
            if s["title"].lower() == section_title.lower():
                section = s
                break

        if section is None:
            available = ", ".join(f"'{s['title']}'" for s in sections)
            return f"Section '{section_title}' not found. Available sections: {available}"

        # Find where the section header ends
        header_match = re.search(
            rf"\\{section['type']}\*?\{{{re.escape(section['title'])}\}}",
            content
        )
        if not header_match:
            return f"Could not locate section header for '{section_title}'"

        header_end = header_match.end()

        # Build new content
        new_file_content = (
            content[:header_end] +
            "\n" + new_content.strip() + "\n" +
            content[section["end_pos"]:]
        )

        # Write file
        target_path.write_text(new_file_content)

        # Configure git user if needed
        config_git_user(repo)

        # Git operations
        repo.index.add([file_path])
        repo.index.commit(commit_message)
        repo.remotes.origin.push()

        return f"Updated section '{section_title}' and pushed"

    elif name == "sync_project":
        project = get_project_config(arguments.get("project_name"))
        repo_path = get_repo_path(project.project_id)

        if not repo_path.exists():
            repo = ensure_repo(project)
            return f"Cloned project '{project.name}'"

        repo = Repo(repo_path)

        # Check for uncommitted changes
        if repo.is_dirty():
            return (
                "Warning: Local changes exist. "
                "Commit or discard them before syncing."
            )

        # Pull latest
        try:
            repo.remotes.origin.pull()
            return f"Synced project '{project.name}' with Overleaf"
        except GitCommandError as e:
            return f"Error syncing: {e}"

    # === DELETE OPERATIONS ===

    elif name == "delete_file":
        project = get_project_config(arguments.get("project_name"))
        repo = ensure_repo(project)
        repo_path = get_repo_path(project.project_id)

        file_path = arguments["file_path"]
        commit_message = arguments.get("commit_message", f"Delete {file_path}")

        target_path = validate_path(repo_path, file_path)

        if not target_path.exists():
            return f"Error: File '{file_path}' not found"

        # Configure git user if needed
        config_git_user(repo)

        # Git remove
        repo.index.remove([file_path])
        target_path.unlink()
        repo.index.commit(commit_message)
        repo.remotes.origin.push()

        return f"Deleted and pushed '{file_path}'"

    else:
        return f"Unknown tool: {name}"


def config_git_user(repo: Repo) -> None:
    """Configure git user if not already set."""
    try:
        repo.config_reader().get_value("user", "name")
    except:
        name = os.environ.get("OVERLEAF_GIT_AUTHOR_NAME", "Overleaf MCP")
        email = os.environ.get("OVERLEAF_GIT_AUTHOR_EMAIL", "mcp@overleaf.local")

        with repo.config_writer() as config:
            config.set_value("user", "name", name)
            config.set_value("user", "email", email)


def main():
    """Run the MCP server."""
    import asyncio

    async def run():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    asyncio.run(run())


if __name__ == "__main__":
    main()
