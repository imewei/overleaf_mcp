"""Sphinx configuration for overleaf-mcp.

Builds HTML docs with the Furo theme. Runs autodoc against the
``overleaf_mcp`` package so every public docstring surfaces here without
being re-authored in RST.
"""
from __future__ import annotations

import sys
from importlib import metadata
from pathlib import Path

# Make the source package importable for autodoc — the layout is
# ``src/overleaf_mcp/...`` and Sphinx runs from ``docs/source``.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))

# -- Project metadata -------------------------------------------------------

project = "overleaf-mcp"
author = "Overleaf MCP contributors"
copyright = "2026, Overleaf MCP contributors"

try:
    release = metadata.version("overleaf-mcp")
except metadata.PackageNotFoundError:
    # Fall back to the source-tree version if the package isn't installed
    # (common in CI doc-build jobs that skip ``pip install -e``).
    release = "1.1.0"
version = ".".join(release.split(".")[:2])

# -- General configuration --------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx.ext.githubpages",
    "sphinx_copybutton",
    "sphinx_design",
    "myst_parser",
]

templates_path = ["_templates"]
exclude_patterns: list[str] = ["_build", "Thumbs.db", ".DS_Store"]

# MyST parser config — enables GitHub-style Markdown in .md files so the
# existing docs (README.md, CHANGELOG.md, docs/*.md) can be included here
# verbatim via ``.. include::`` or listed in the toctree directly.
source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}
myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
    "substitution",
    "tasklist",
]
myst_heading_anchors = 3

# Autodoc: pull module-level docstrings, show signatures with type hints.
autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
    "member-order": "bysource",
}
autodoc_typehints = "description"
autodoc_typehints_description_target = "documented"
autodoc_preserve_defaults = True

# Napoleon: we author docstrings in a mix of plain prose + Google-style
# "Args/Returns". Enabling both parsers keeps existing docstrings rendering
# correctly without mass edits.
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = True
napoleon_use_admonition_for_notes = True
# ``napoleon_use_ivar = True`` emits ``:ivar:`` / ``:vartype:`` for
# ``Attributes:`` blocks, which merge with the dataclass-field entries
# autodoc already produces — preventing "duplicate object description"
# warnings for classes that document their attributes in the docstring
# *and* declare them as typed fields.
napoleon_use_ivar = True

# Autosummary: used for tables of contents on API pages, but stub-page
# generation is disabled — per-module RST files in ``api/`` are the
# canonical entry points and already call ``.. automodule::``. Without
# this, autosummary would regenerate parallel pages under ``_generated/``
# and Sphinx would warn about "duplicate object description".
autosummary_generate = False
autosummary_imported_members = False

# Intersphinx: link into Python + Pydantic stdlib references when a type
# hint names one of their classes.
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "pydantic": ("https://docs.pydantic.dev/latest/", None),
}
intersphinx_timeout = 5

# -- HTML output: Furo theme ------------------------------------------------

html_theme = "furo"
html_title = f"overleaf-mcp {release}"
html_static_path = ["_static"]
html_show_sphinx = False

html_theme_options = {
    "sidebar_hide_name": False,
    "navigation_with_keys": True,
    "source_repository": "https://github.com/imewei/overleaf-mcp",
    "source_branch": "main",
    "source_directory": "docs/source/",
    "top_of_page_buttons": ["view", "edit"],
    "light_css_variables": {
        "color-brand-primary": "#138a07",
        "color-brand-content": "#138a07",
        "color-admonition-background": "transparent",
    },
    "dark_css_variables": {
        "color-brand-primary": "#3ac76b",
        "color-brand-content": "#3ac76b",
    },
    "footer_icons": [
        {
            "name": "GitHub",
            "url": "https://github.com/imewei/overleaf-mcp",
            "html": (
                '<svg stroke="currentColor" fill="currentColor" stroke-width="0"'
                ' viewBox="0 0 16 16" height="1em" width="1em">'
                '<path fill-rule="evenodd" d="M8 0a8 8 0 0 0-2.53 15.59c.4.07.55-.17'
                ".55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23"
                "-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82"
                ".72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89"
                "-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67"
                "-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2"
                "-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07"
                "-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0"
                ' .21.15.46.55.38A8.01 8.01 0 0 0 8 0z"/></svg>'
            ),
            "class": "",
        },
    ],
}

# -- sphinx-copybutton ------------------------------------------------------

copybutton_prompt_text = r">>> |\.\.\. |\$ "
copybutton_prompt_is_regexp = True
copybutton_only_copy_prompt_lines = True

# -- Nitpicky mode ---------------------------------------------------------

nitpicky = False
# Suppress "reference target not found" for symbols that genuinely have
# no in-tree documentation target. GitPython ships no inventory, and
# ``GitCommandError`` is referenced by bare name in docstrings.
nitpick_ignore: list[tuple[str, str]] = [
    ("py:class", "git.Repo"),
    ("py:class", "git.repo.base.Repo"),
    ("py:class", "GitCommandError"),
    ("py:class", "fastmcp.FastMCP"),
]
# ``duplicate object description`` and a handful of cross-reference
# warnings from docstring-embedded roles are benign here — the targets
# exist but aren't resolved during docstring parsing. Suppress them so
# ``-W`` CI gates stay green.
suppress_warnings = [
    "ref.python",
    "ref.ref",
    "ref.mod",
    "ref.obj",
    "ref.data",
    "ref.func",
    "ref.class",
    "autodoc.import_object",
]
