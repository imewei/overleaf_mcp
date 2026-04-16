"""MCP tool implementations.

Every tool is a plain async function. Schema generation is handled at
registration time — :mod:`~overleaf_mcp.server` wraps each of these with
``@mcp.tool()`` from FastMCP, which infers the JSON schema from the
:class:`typing.Annotated` signatures and ``Field(description=...)``
metadata below. The tool description comes from each function's docstring.

Why functions-plus-TOOLS-dict rather than decorators here?
  * Tests can call each tool as a normal async function without going
    through the MCP protocol (see ``tests/test_dispatcher.py``).
  * ``execute_tool(name, arguments)`` is preserved as a thin dispatcher
    shim so the test suite's existing call pattern keeps working.
  * ``server.py`` stays tiny (pure transport) and can switch frameworks
    by re-registering the same functions with a different decorator.
"""
from __future__ import annotations

import base64
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from urllib.parse import quote

from git import GitCommandError, Repo
from pydantic import Field

from .config import load_config, resolve_project
from .git_ops import (
    StaleRepoWarning,
    _lock_for,
    _run_blocking,
    acquire_project,
    config_git_user,
    ensure_repo,
    get_repo_path,
    validate_path,
)
from .latex import get_section_by_title, parse_sections

logger = logging.getLogger(__name__)

OVERLEAF_API_URL = "https://www.overleaf.com/docs"


def _decode_msg(msg: str | bytes) -> str:
    """Normalize a GitPython commit message to ``str``.

    GitPython's ``Commit.message`` is typed ``str | bytes`` (it returns
    whichever encoding the commit object used). Formatting bytes in an
    f-string produces literal ``b'...'`` text in the response, which is
    a real bug that slipped through before mypy --strict caught it.
    """
    return msg if isinstance(msg, str) else msg.decode("utf-8", errors="replace")

# --- Common parameter annotations (reused across tools) -------------------

_ProjectName = Annotated[
    str | None,
    Field(description="Project identifier from config (uses default if not specified)"),
]
_GitToken = Annotated[
    str | None, Field(description="Git token override (bypasses config file)")
]
_ProjectId = Annotated[
    str | None, Field(description="Project ID override (bypasses config file)")
]
_CommitMessage = Annotated[str | None, Field(description="Git commit message")]
_DryRun = Annotated[
    bool,
    Field(
        description=(
            "If true, report what would happen without making changes "
            "(default: false)"
        )
    ),
]
_Push = Annotated[
    bool, Field(description="Whether to push after committing (default: true)")
]


# === CREATE OPERATIONS ===


async def create_project(
    content: Annotated[
        str,
        Field(description="LaTeX content for the project (raw .tex content or base64-encoded zip)"),
    ],
    project_name: Annotated[
        str | None, Field(description="Optional name for the project")
    ] = None,
    engine: Annotated[
        str,
        Field(description="TeX engine to use (default: pdflatex)"),
    ] = "pdflatex",
    is_zip: Annotated[
        bool, Field(description="If true, content is base64-encoded zip file")
    ] = False,
) -> str:
    """Create a new Overleaf project from LaTeX content.

    Returns an ``overleaf.com/docs?snip_uri=...`` URL that the user opens
    in their browser to create the project. The human click is by design,
    not a limitation we can work around.

    Rationale (verified 2026-04-16): Overleaf's only publicly documented
    "developer API" is this snip_uri form endpoint (see overleaf.com/devs).
    There is NO documented REST endpoint for server-to-server project
    creation — git tokens authenticate git transport only, and the
    available REST surface is limited to operations on already-existing
    projects. Third-party libraries (pyoverleaf etc.) rely on session
    cookies or reverse-engineered internal endpoints, which are ToS-risky
    and fragile. The snip_uri pattern is the supported path.
    """
    if is_zip:
        mime_type = "application/zip"
        data = content  # Already base64 encoded
    else:
        mime_type = "application/x-tex"
        data = base64.b64encode(content.encode()).decode()

    snip_uri = f"data:{mime_type};base64,{data}"

    form_data: dict[str, str] = {"snip_uri": snip_uri, "engine": engine}
    if project_name:
        form_data["snip_name"] = project_name

    params = "&".join(f"{k}={quote(str(v))}" for k, v in form_data.items())

    return (
        f"To create the project, open this URL in your browser:\n\n"
        f"{OVERLEAF_API_URL}?{params}\n\n"
        f"Or use the following form data to POST to {OVERLEAF_API_URL}:\n"
        f"- snip_uri: {snip_uri[:100]}...\n"
        f"- engine: {engine}"
    )


async def create_file(
    file_path: Annotated[
        str, Field(description="Path for the new file (e.g., 'chapters/intro.tex')")
    ],
    content: Annotated[str, Field(description="Content for the new file")],
    commit_message: _CommitMessage = None,
    project_name: _ProjectName = None,
    dry_run: _DryRun = False,
    push: _Push = True,
    git_token: _GitToken = None,
    project_id: _ProjectId = None,
) -> str:
    """Create a new file in an existing Overleaf project.

    Commits and pushes the changes immediately (unless ``push=False``).
    """
    project = resolve_project(project_name, git_token, project_id)
    repo_path = get_repo_path(project.project_id)
    msg = commit_message or f"Add {file_path}"

    async with acquire_project(project, force_pull=True, mode="write") as ctx:
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
        await _run_blocking(ctx.repo.index.commit, msg)
        if push:
            await _run_blocking(ctx.repo.remotes.origin.push)

        return ctx.wrap(f"Created{' and pushed' if push else ''} '{file_path}'")


# === READ OPERATIONS ===


async def list_projects() -> str:
    """List all configured Overleaf projects."""
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


async def list_files(
    extension: Annotated[
        str,
        Field(
            description=(
                "Filter by file extension (e.g., '.tex', '.bib'). "
                "Leave empty for all files."
            )
        ),
    ] = "",
    project_name: _ProjectName = None,
    git_token: _GitToken = None,
    project_id: _ProjectId = None,
) -> str:
    """List files in an Overleaf project."""
    project = resolve_project(project_name, git_token, project_id)
    repo_path = get_repo_path(project.project_id)

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


async def read_file(
    file_path: Annotated[str, Field(description="Path to the file within the project")],
    project_name: _ProjectName = None,
    git_token: _GitToken = None,
    project_id: _ProjectId = None,
) -> str:
    """Read the content of a file from an Overleaf project."""
    project = resolve_project(project_name, git_token, project_id)
    repo_path = get_repo_path(project.project_id)

    async with acquire_project(project, force_pull=False) as ctx:
        target_path = validate_path(repo_path, file_path)

        if not target_path.exists():
            return ctx.wrap(f"Error: File '{file_path}' not found")

        content = target_path.read_text()
        return ctx.wrap(f"Content of '{file_path}':\n\n{content}")


async def get_sections(
    file_path: Annotated[str, Field(description="Path to the LaTeX file")],
    project_name: _ProjectName = None,
    git_token: _GitToken = None,
    project_id: _ProjectId = None,
) -> str:
    """Parse a LaTeX file and extract its section structure.

    Returns section types, titles, and content previews.
    """
    project = resolve_project(project_name, git_token, project_id)
    repo_path = get_repo_path(project.project_id)

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


async def get_section_content(
    file_path: Annotated[str, Field(description="Path to the LaTeX file")],
    section_title: Annotated[
        str, Field(description="Title of the section to retrieve")
    ],
    project_name: _ProjectName = None,
    git_token: _GitToken = None,
    project_id: _ProjectId = None,
) -> str:
    """Get the full content of a specific section by its title."""
    project = resolve_project(project_name, git_token, project_id)
    repo_path = get_repo_path(project.project_id)

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


async def list_history(
    limit: Annotated[
        int,
        Field(description="Maximum number of commits to show (default: 20, max: 200)"),
    ] = 20,
    file_path: Annotated[
        str | None, Field(description="Filter history to a specific file")
    ] = None,
    since: Annotated[
        str | None,
        Field(description="Show commits after this date (e.g., '2025-01-01', '2.weeks')"),
    ] = None,
    until: Annotated[
        str | None,
        Field(description="Show commits before this date (e.g., '2025-06-01', '1.month')"),
    ] = None,
    project_name: _ProjectName = None,
    git_token: _GitToken = None,
    project_id: _ProjectId = None,
) -> str:
    """Show git commit history for the project."""
    project = resolve_project(project_name, git_token, project_id)
    limit = min(limit, 200)

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
            lines.append(f"  {_decode_msg(c.message).strip()[:100]}")

        return ctx.wrap("\n".join(lines))


async def get_diff(
    from_ref: Annotated[
        str,
        Field(description="Starting reference (commit hash, branch, or 'HEAD~n')"),
    ] = "HEAD",
    to_ref: Annotated[
        str | None, Field(description="Ending reference (default: working tree)")
    ] = None,
    file_path: Annotated[
        str | None, Field(description="Filter diff to a specific file")
    ] = None,
    paths: Annotated[
        list[str] | None, Field(description="Filter diff to multiple files")
    ] = None,
    mode: Annotated[
        str,
        Field(
            description=(
                "Output mode: 'unified' = full patch text (default), "
                "'stat' = file-level change counts (very compact), "
                "'name-only' = just the list of changed paths (ultra compact). "
                "Prefer 'stat' or 'name-only' for large diffs when an agent "
                "just needs to know WHICH files changed, not the contents."
            )
        ),
    ] = "unified",
    context_lines: Annotated[
        int,
        Field(description="Number of context lines in unified diff (0-10, default: 3). Ignored for stat/name-only."),
    ] = 3,
    max_output_chars: Annotated[
        int,
        Field(description="Truncate diff output to this many characters (default: 120000)"),
    ] = 120000,
    project_name: _ProjectName = None,
    git_token: _GitToken = None,
    project_id: _ProjectId = None,
) -> str:
    """Get git diff for the project or specific files.

    Supports three output modes:
    - ``unified`` (default): full unified patch with configurable context.
    - ``stat``: per-file change counts (``git diff --stat``).
    - ``name-only``: just the list of changed paths.
    """
    if mode not in {"unified", "stat", "name-only"}:
        return (
            f"Error: unknown diff mode '{mode}'. "
            "Valid modes: 'unified', 'stat', 'name-only'."
        )

    project = resolve_project(project_name, git_token, project_id)
    context_lines = max(0, min(10, context_lines))
    max_output_chars = max(2000, min(500000, max_output_chars))

    path_filters: list[str] = []
    if file_path:
        path_filters.append(file_path)
    if paths:
        path_filters.extend(paths)

    # Mode-specific git args. Unified takes -U<n>; stat/name-only replace
    # the format entirely and ignore context_lines.
    if mode == "unified":
        diff_args: list[str] = [f"-U{context_lines}"]
    elif mode == "stat":
        diff_args = ["--stat"]
    else:  # name-only
        diff_args = ["--name-only"]

    if to_ref:
        diff_args.extend([from_ref, to_ref])
    else:
        diff_args.append(from_ref)

    if path_filters:
        diff_args.append("--")
        diff_args.extend(path_filters)

    async with acquire_project(project, force_pull=False) as ctx:
        try:
            diff = await _run_blocking(ctx.repo.git.diff, *diff_args)
        except GitCommandError as e:
            return ctx.wrap(f"Error getting diff: {e}")

        if not diff:
            return ctx.wrap("No differences found")

        truncated = len(diff) > max_output_chars
        if truncated:
            diff = diff[:max_output_chars]

        # Label the output by mode so callers can tell what they got
        # without parsing the git output itself.
        header = {
            "unified": "Diff:",
            "stat": "Diff (stat):",
            "name-only": "Changed files:",
        }[mode]

        result = f"{header}\n\n{diff}"
        if truncated:
            result += "\n\n[diff truncated]"
        return ctx.wrap(result)


async def status_summary(
    project_name: _ProjectName = None,
    git_token: _GitToken = None,
    project_id: _ProjectId = None,
) -> str:
    """Get a comprehensive project status summary.

    Includes file counts, last commit, and main-document section structure.
    """
    project = resolve_project(project_name, git_token, project_id)
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
                f"{head.author.name} | {_decode_msg(head.message).strip()[:80]}"
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


async def edit_file(
    file_path: Annotated[str, Field(description="Path to the file to edit")],
    old_string: Annotated[
        str, Field(description="The exact text to find and replace")
    ],
    new_string: Annotated[str, Field(description="The text to replace it with")],
    commit_message: _CommitMessage = None,
    project_name: _ProjectName = None,
    dry_run: _DryRun = False,
    push: _Push = True,
    git_token: _GitToken = None,
    project_id: _ProjectId = None,
) -> str:
    """Make surgical edits to a file by replacing specific text.

    The ``old_string`` must match exactly (including whitespace) and must
    occur exactly once in the file. Commits and pushes immediately.
    """
    project = resolve_project(project_name, git_token, project_id)
    repo_path = get_repo_path(project.project_id)
    msg = commit_message or f"Edit {file_path}"

    async with acquire_project(project, force_pull=True, mode="write") as ctx:
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
        await _run_blocking(ctx.repo.index.commit, msg)
        if push:
            await _run_blocking(ctx.repo.remotes.origin.push)

        return ctx.wrap(f"Edited{' and pushed' if push else ''} '{file_path}'")


async def rewrite_file(
    file_path: Annotated[str, Field(description="Path to the file to rewrite")],
    content: Annotated[str, Field(description="New content for the entire file")],
    commit_message: _CommitMessage = None,
    project_name: _ProjectName = None,
    dry_run: _DryRun = False,
    push: _Push = True,
    git_token: _GitToken = None,
    project_id: _ProjectId = None,
) -> str:
    """Replace entire file contents. Commits and pushes immediately."""
    project = resolve_project(project_name, git_token, project_id)
    repo_path = get_repo_path(project.project_id)
    msg = commit_message or f"Rewrite {file_path}"

    async with acquire_project(project, force_pull=True, mode="write") as ctx:
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
        await _run_blocking(ctx.repo.index.commit, msg)
        if push:
            await _run_blocking(ctx.repo.remotes.origin.push)

        return ctx.wrap(f"Rewrote{' and pushed' if push else ''} '{file_path}'")


async def update_section(
    file_path: Annotated[str, Field(description="Path to the LaTeX file")],
    section_title: Annotated[
        str, Field(description="Title of the section to update")
    ],
    new_content: Annotated[
        str,
        Field(description="New content for the section (excluding the section header)"),
    ],
    commit_message: _CommitMessage = None,
    project_name: _ProjectName = None,
    dry_run: _DryRun = False,
    push: _Push = True,
    git_token: _GitToken = None,
    project_id: _ProjectId = None,
) -> str:
    """Update a specific section in a LaTeX file by its title.

    Commits and pushes immediately.
    """
    project = resolve_project(project_name, git_token, project_id)
    repo_path = get_repo_path(project.project_id)
    msg = commit_message or f"Update section '{section_title}'"

    async with acquire_project(project, force_pull=True, mode="write") as ctx:
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
            content,
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
            content[:header_end]
            + "\n"
            + new_content.strip()
            + "\n"
            + content[section["end_pos"]:]
        )

        target_path.write_text(new_file_content)

        config_git_user(ctx.repo)
        ctx.repo.index.add([file_path])
        await _run_blocking(ctx.repo.index.commit, msg)
        if push:
            await _run_blocking(ctx.repo.remotes.origin.push)

        return ctx.wrap(
            f"Updated section '{section_title}'{' and pushed' if push else ''}"
        )


async def sync_project(
    project_name: _ProjectName = None,
    git_token: _GitToken = None,
    project_id: _ProjectId = None,
) -> str:
    """Sync the local project with Overleaf (pull latest changes).

    This is the explicit "refresh now" escape hatch — it always force-pulls
    and reports refresh errors hard rather than falling back to stale
    state like the read tools do.
    """
    # Unlike the other tools, sync_project cannot use acquire_project()
    # directly: that helper swallows StaleRepoWarning and returns a
    # degraded ToolContext, whereas this tool's contract is to report
    # the refresh error to the caller. Instead we hold the per-project
    # lock manually (same serialization guarantee) and let the error
    # propagate out of ensure_repo.
    project = resolve_project(project_name, git_token, project_id)
    repo_path = get_repo_path(project.project_id)

    async with _lock_for(project.project_id).exclusive():
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


async def delete_file(
    file_path: Annotated[str, Field(description="Path to the file to delete")],
    commit_message: _CommitMessage = None,
    project_name: _ProjectName = None,
    dry_run: _DryRun = False,
    push: _Push = True,
    git_token: _GitToken = None,
    project_id: _ProjectId = None,
) -> str:
    """Delete a file from an Overleaf project. Commits and pushes immediately."""
    project = resolve_project(project_name, git_token, project_id)
    repo_path = get_repo_path(project.project_id)
    msg = commit_message or f"Delete {file_path}"

    async with acquire_project(project, force_pull=True, mode="write") as ctx:
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
        await _run_blocking(ctx.repo.index.commit, msg)
        if push:
            await _run_blocking(ctx.repo.remotes.origin.push)

        return ctx.wrap(f"Deleted{' and pushed' if push else ''} '{file_path}'")


# ---------------------------------------------------------------------------
# Registration table + dispatcher shim
# ---------------------------------------------------------------------------

# Name -> implementation. server.py iterates this to register each tool with
# the MCP framework; tests use execute_tool() to invoke tools by name.
# Typed as Callable[..., Awaitable[str]] — each tool is an async function
# returning the response text. Precise signatures vary per tool; FastMCP
# and the dispatcher shim both accept kwargs so ``...`` is accurate.
TOOLS: dict[str, Callable[..., Awaitable[str]]] = {
    "create_project": create_project,
    "create_file": create_file,
    "list_projects": list_projects,
    "list_files": list_files,
    "read_file": read_file,
    "get_sections": get_sections,
    "get_section_content": get_section_content,
    "list_history": list_history,
    "get_diff": get_diff,
    "status_summary": status_summary,
    "edit_file": edit_file,
    "rewrite_file": rewrite_file,
    "update_section": update_section,
    "sync_project": sync_project,
    "delete_file": delete_file,
}


async def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """Dispatch a tool call by name. Kept for test compatibility.

    The MCP framework in :mod:`~overleaf_mcp.server` calls the registered
    functions directly; tests that want a dict-based dispatch call this
    shim instead.
    """
    fn = TOOLS.get(name)
    if fn is None:
        return f"Unknown tool: {name}"
    return await fn(**arguments)


async def list_tools() -> list[Any]:
    """Return MCP-compatible ``Tool`` objects for every registered tool.

    This is a compatibility shim kept for tests that introspect the tool
    schemas (see ``tests/test_server.py``). Clients speaking MCP go through
    :mod:`~overleaf_mcp.server`'s FastMCP instance, not through here.

    The schemas are derived from each function's ``Annotated``/``Field``
    signature via ``pydantic.TypeAdapter`` — identical to what FastMCP
    emits over the wire, so test assertions on the schema shape stay
    truthful about what MCP clients actually see.
    """
    from inspect import Parameter, signature

    from mcp.types import Tool as MCPTool
    from pydantic import TypeAdapter

    out: list[Any] = []
    for name, fn in TOOLS.items():
        sig = signature(fn)
        properties: dict[str, Any] = {}
        required: list[str] = []
        for pname, param in sig.parameters.items():
            # Resolve the annotation through TypeAdapter to get a JSON
            # schema fragment — this handles Annotated[..., Field(...)]
            # and strips NoneType alternates the same way FastMCP does.
            annotation = param.annotation if param.annotation is not Parameter.empty else str
            try:
                schema = TypeAdapter(annotation).json_schema()
            except Exception:
                schema = {"type": "string"}
            # Strip pydantic-specific keys that aren't part of MCP schemas.
            schema.pop("title", None)
            if param.default is Parameter.empty:
                required.append(pname)
            else:
                schema["default"] = param.default
            properties[pname] = schema

        input_schema: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            input_schema["required"] = required

        out.append(
            MCPTool(
                name=name,
                description=(fn.__doc__ or "").strip().split("\n\n")[0],
                inputSchema=input_schema,
            )
        )
    return out
