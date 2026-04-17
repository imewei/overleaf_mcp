Tool reference
==============

All 15 MCP tools, grouped by CRUD category. Schemas are inferred from
``Annotated[..., Field(...)]`` signatures in
:mod:`overleaf_mcp.tools` by FastMCP 3.x — the RST below is a
human-oriented summary, not the wire schema.

For the wire-level signatures, see the :doc:`api/tools` page.

.. contents:: On this page
    :local:
    :depth: 2

Common conventions
------------------

Every project-scoped tool accepts these optional kwargs:

.. list-table::
    :header-rows: 1
    :widths: 20 14 66

    * - Param
      - Type
      - Meaning
    * - ``project_name``
      - ``str | None``
      - Project key from ``overleaf_config.json``. Uses
        ``defaultProject`` if omitted.
    * - ``git_token``
      - ``str | None``
      - Inline Git token (bypasses the config file). Must be passed
        **with** ``project_id``.
    * - ``project_id``
      - ``str | None``
      - Inline project ID (bypasses the config file). Must be passed
        **with** ``git_token``.
    * - ``commit_message``
      - ``str | None``
      - Git commit message (write tools). Defaults to an action-specific
        string.
    * - ``dry_run``
      - ``bool``
      - If ``true``, report what would happen without writing or
        pushing. Default ``false``.
    * - ``push``
      - ``bool``
      - Push after commit. Default ``true``. Set ``false`` for
        local-only edits.

Refresh semantics
~~~~~~~~~~~~~~~~~

.. list-table::
    :header-rows: 1
    :widths: 22 10 10 58

    * - Category
      - ``force_pull``
      - ``mode``
      - TTL-cache respected?
    * - Read (``list_files``, ``read_file``, ``get_sections``,
        ``get_section_content``, ``list_history``, ``get_diff``,
        ``status_summary``)
      - ``false``
      - ``read``
      - Yes — may serve from local snapshot if <
        :envvar:`OVERLEAF_PULL_TTL` old
    * - Write (``create_file``, ``edit_file``, ``rewrite_file``,
        ``update_section``, ``delete_file``)
      - ``true``
      - ``write``
      - No — always pulls before commit
    * - Sync (``sync_project``)
      - ``true``
      - exclusive
      - No — always pulls, reports hard errors
    * - List/Create-project (``list_projects``, ``create_project``)
      - n/a
      - n/a
      - No Git touched

Read tools under the same project run **concurrently** (shared lock).
Write tools take the writer lock — one writer at a time per project,
across the whole process.

Response envelope
~~~~~~~~~~~~~~~~~

By default, every tool returns a plain-text string. Set
:envvar:`OVERLEAF_STRUCTURED` to ``1`` to append a parse-friendly tail:

.. code-block:: text

    <tool-response-body>

    <warnings, one per line, if any>

    <mcp-envelope>{"ok": true, "warnings": []}</mcp-envelope>

``ok`` is ``false`` when the body starts with ``Error:`` or any
warnings were attached.

Create
------

.. _tool-create-project:

``create_project``
~~~~~~~~~~~~~~~~~~

Create a new Overleaf project from LaTeX content or a base64-zipped
archive.

.. list-table::
    :header-rows: 1
    :widths: 18 14 14 54

    * - Param
      - Type
      - Default
      - Description
    * - ``content``
      - ``str``
      - —
      - LaTeX source or base64-encoded ZIP
    * - ``project_name``
      - ``str | None``
      - ``None``
      - Optional display name
    * - ``engine``
      - ``str``
      - ``"pdflatex"``
      - TeX engine hint
    * - ``is_zip``
      - ``bool``
      - ``false``
      - If ``true``, ``content`` is a base64 ZIP

**Returns:** an ``overleaf.com/docs?snip_uri=...`` URL the user clicks
to finish project creation.

.. admonition:: Why a URL, not a server-to-server call?
    :class: note

    Overleaf's only documented public endpoint for creating projects
    is the ``snip_uri`` form. Git tokens authenticate Git transport
    only; there is no published REST route for "create project".
    Session-cookie approaches used by some third-party libraries are
    ToS-risky. See the extended rationale in
    :func:`overleaf_mcp.tools.create_project`.

.. _tool-create-file:

``create_file``
~~~~~~~~~~~~~~~

Add a new file to an existing project. Commits + pushes immediately
(unless ``push=false``).

.. list-table::
    :header-rows: 1
    :widths: 20 14 14 52

    * - Param
      - Type
      - Required
      - Description
    * - ``file_path``
      - ``str``
      - ✓
      - Path relative to project root, e.g. ``chapters/intro.tex``
    * - ``content``
      - ``str``
      - ✓
      - File content
    * - ``commit_message``
      - ``str | None``
      -
      - Default: ``Add <file_path>``

**Errors:** Returns ``Error: File 'X' already exists`` if the path is
taken — use :ref:`tool-edit-file` / :ref:`tool-rewrite-file` instead.

Read
----

.. _tool-list-projects:

``list_projects``
~~~~~~~~~~~~~~~~~

List projects configured in ``overleaf_config.json``. No Git touched.

Returns a human-readable list with the default project marked.

.. _tool-list-files:

``list_files``
~~~~~~~~~~~~~~

List all non-dotfile paths in the project, sorted.

.. list-table::
    :header-rows: 1
    :widths: 18 14 16 52

    * - Param
      - Type
      - Default
      - Description
    * - ``extension``
      - ``str``
      - ``""``
      - Exact suffix match, case-sensitive, **including the leading dot**
        (e.g. ``.tex``). Empty = all.

.. _tool-read-file:

``read_file``
~~~~~~~~~~~~~

Read a text file. Guarded against runaway context usage.

.. list-table::
    :header-rows: 1
    :widths: 18 14 16 52

    * - Param
      - Type
      - Default
      - Description
    * - ``file_path``
      - ``str``
      - —
      - Path relative to project root
    * - ``max_bytes``
      - ``int``
      - ``200000``
      - Truncate output past this length. Clamped to ``[1000, 2000000]``.

When truncated, the response appends ``[file truncated at N bytes]``.
For large LaTeX documents, prefer :ref:`tool-get-section-content` —
it's smaller and avoids the ceiling.

.. _tool-get-sections:

``get_sections``
~~~~~~~~~~~~~~~~

Parse a LaTeX file and list its ``\\part`` / ``\\chapter`` /
``\\section`` / ``\\subsection`` / ``\\subsubsection`` / ``\\paragraph`` /
``\\subparagraph`` entries (starred variants supported).

Returns: type, title, and a 200-char preview of each section's body.

.. _tool-get-section-content:

``get_section_content``
~~~~~~~~~~~~~~~~~~~~~~~

Return the full body of a section matched by its title (case-insensitive).

Errors list the available section titles, so a typo produces an
actionable response rather than a dead-end.

.. _tool-list-history:

``list_history``
~~~~~~~~~~~~~~~~

Show git commit history.

.. list-table::
    :header-rows: 1
    :widths: 18 14 16 52

    * - Param
      - Type
      - Default
      - Description
    * - ``limit``
      - ``int``
      - ``20``
      - Max commits to return. Hard-capped at 200.
    * - ``file_path``
      - ``str | None``
      - ``None``
      - Restrict to commits touching this path
    * - ``since``
      - ``str | None``
      - ``None``
      - Git date spec (e.g. ``2025-01-01``, ``2.weeks``)
    * - ``until``
      - ``str | None``
      - ``None``
      - Git date spec

.. admonition:: Shallow-clone caveat
    :class: warning

    With :envvar:`OVERLEAF_SHALLOW_CLONE` = ``1``, history is capped at
    :envvar:`OVERLEAF_SHALLOW_DEPTH` — commits older than that are
    simply not in the local clone. The response appends a warning line
    when the depth cap may have truncated results.

.. _tool-get-diff:

``get_diff``
~~~~~~~~~~~~

Compare two refs / working tree.

.. list-table::
    :header-rows: 1
    :widths: 22 18 16 44

    * - Param
      - Type
      - Default
      - Description
    * - ``from_ref``
      - ``str``
      - ``"HEAD"``
      - Starting ref (commit / branch / ``HEAD~n``)
    * - ``to_ref``
      - ``str | None``
      - ``None``
      - Ending ref (default: working tree)
    * - ``file_path``
      - ``str | None``
      - ``None``
      - Single-file filter
    * - ``paths``
      - ``list[str] | None``
      - ``None``
      - Multi-file filter
    * - ``mode``
      - ``str``
      - ``"unified"``
      - ``unified`` / ``stat`` / ``name-only``
    * - ``context_lines``
      - ``int``
      - ``3``
      - Context lines for ``unified`` (clamped 0–10)
    * - ``max_output_chars``
      - ``int``
      - ``120000``
      - Truncate past this length (clamped 2 000–500 000)

``stat`` and ``name-only`` exist to keep agent token usage bounded: if
the agent just needs to know *which* files changed (not the content),
these are dramatically smaller than ``unified``.

.. _tool-status-summary:

``status_summary``
~~~~~~~~~~~~~~~~~~

One-call project orientation. Equivalent to ``list_files`` +
``list_history(limit=1)`` + ``get_sections`` on the detected main
``.tex``, but in a single pull-cache hit.

.. tip::

    **Use this as the first tool on an unfamiliar project.** Three
    separate calls would still be one pull (TTL-cached), but
    ``status_summary`` is a single structured response the agent can
    reason about without stitching.

Returns: project name, current branch, total / ``.tex`` file counts,
latest commit summary, and — if a file containing ``\\documentclass``
or ``\\begin{document}`` is found — its section tree.

Update
------

.. _tool-edit-file:

``edit_file`` (surgical edit)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Replace an exact string match. Old text must occur **exactly once**.

.. list-table::
    :header-rows: 1
    :widths: 18 14 14 54

    * - Param
      - Type
      - Required
      - Description
    * - ``file_path``
      - ``str``
      - ✓
      -
    * - ``old_string``
      - ``str``
      - ✓
      - Exact text to find, whitespace-sensitive
    * - ``new_string``
      - ``str``
      - ✓
      - Replacement

**Error policy:** if ``old_string`` appears zero or more than once, no
changes are written and the response describes the mismatch (zero) or
count (many). Safer than a regex-based replacer for LLM-generated edits.

.. _tool-rewrite-file:

``rewrite_file``
~~~~~~~~~~~~~~~~

Replace the entire file. Commits + pushes. Prefer :ref:`tool-edit-file`
for small changes — smaller diffs, easier human review.

.. _tool-update-section:

``update_section``
~~~~~~~~~~~~~~~~~~

Replace the body of a LaTeX section by its title. The header itself is
preserved; only the content between this section and the next is
rewritten.

.. _tool-sync-project:

``sync_project``
~~~~~~~~~~~~~~~~

Explicit force-pull. Reports hard errors (stale-snapshot fallback is
disabled for this tool — by design, this is the diagnostic escape
hatch).

.. list-table::
    :header-rows: 1
    :widths: 40 60

    * - Response
      - Meaning
    * - ``Cloned project '...'``
      - First pull; repo now exists
    * - ``Synced project '...'``
      - Pull succeeded
    * - ``Warning: Local changes exist.``
      - Working tree is dirty; refusing to pull
    * - ``Error syncing: ...``
      - Upstream refused; shown verbatim (this is where auth/ref
        errors surface)

Delete
------

.. _tool-delete-file:

``delete_file``
~~~~~~~~~~~~~~~

Delete a path and commit the removal. Standard ``dry_run`` / ``push``
semantics.

Error handling
--------------

Errors are returned as **text responses prefixed with** ``Error:`` —
they do not raise out of the tool. This keeps the transport simple
(one string-return contract) and lets the LLM read the error text
directly.

Soft failures (refresh couldn't reach Overleaf but a local snapshot
exists) attach a ``⚠ could not refresh from Overleaf: ...`` warning
line to the tool's body. Hard errors (misconfigured project, bad path,
bad auth on a write) return ``Error:`` prefixes.

All error text that surfaces a Git remote URL has its embedded
``user:password@`` userinfo replaced with ``<redacted>@`` before it
reaches logs or tool output — the Basic-auth token never leaks through
``GitCommandError.stderr``.

Observability
-------------

Setting :envvar:`OVERLEAF_TIMING` = ``1`` emits one INFO-level log
line per tool call from the ``overleaf_mcp.git_ops`` logger:

.. code-block:: text

    acquire_project {"project":"<id>","mode":"read|write","elapsed_ms":42.3,"stale":false}

Stability: the ``acquire_project`` prefix and the four JSON keys
(``project``, ``mode``, ``elapsed_ms``, ``stale``) are a **stable
interface** — external monitoring pipelines may parse this line
directly. Additional keys may be added in future releases but existing
keys will not be renamed or removed without a major version bump.

The line is silent when :envvar:`OVERLEAF_TIMING` is unset — zero cost
when off (one env-var lookup per tool call).

See :doc:`architecture` for the full soft-vs-hard taxonomy.
