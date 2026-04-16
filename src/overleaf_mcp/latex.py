"""Lightweight LaTeX section parsing.

Pure-function string utilities for locating ``\\section{...}``-style
macros and slicing a document into section bodies. No file I/O, no
dependencies on the rest of the project — safe to reuse standalone.
"""
from __future__ import annotations

import re
from typing import Any

# Matches \part, \chapter, \section, \subsection, \subsubsection, \paragraph,
# \subparagraph — with or without the starred (unnumbered) variant.
SECTION_PATTERN = re.compile(
    r"\\(part|chapter|section|subsection|subsubsection|paragraph|subparagraph)\*?\{([^}]+)\}",
    re.MULTILINE,
)


def parse_sections(content: str) -> list[dict[str, Any]]:
    """Parse LaTeX content and return the sections it contains.

    Each section dict has: ``type`` (e.g. "section"), ``title``, ``preview``
    (first 200 chars of the body), ``start_pos`` (offset of the section
    header), ``end_pos`` (offset where the next section begins, or end of
    content for the last section).
    """
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
    """Return the full content of a section matched by case-insensitive title."""
    sections = parse_sections(content)

    for section in sections:
        if section["title"].lower() == title.lower():
            return content[section["start_pos"]:section["end_pos"]]

    return None
