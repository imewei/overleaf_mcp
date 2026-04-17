Usage patterns
==============

This page shows the conversational patterns that work well with the
server. The assistant picks the right tool; you pick the right ask.

For the full per-tool parameter reference, see :doc:`tools`.

Start here: orientation
-----------------------

For an unfamiliar project, the cheapest first call is ``status_summary``.
It combines ``list_files`` + ``list_history(limit=1)`` + ``get_sections``
on the detected main ``.tex`` file in a single TTL-cached pull.

.. code-block:: text

    "Summarize my thesis project — files, latest commit, section tree."

Reading
-------

.. code-block:: text

    "List all .tex files in my thesis."
    "Read chapters/introduction.tex."
    "What sections are in chapter1.tex?"
    "Get the content of the 'Methods' section."
    "Show me the last 10 commits that touched refs.bib."
    "What changed between HEAD~5 and HEAD on main.tex?"

Read tools respect the :envvar:`OVERLEAF_PULL_TTL` cache — a burst of
reads pays one network round-trip. Write tools always force-pull.

Editing
-------

``edit_file`` is the workhorse: it does a surgical find-and-replace, with
an explicit error if the target string appears zero or multiple times.

.. code-block:: text

    "In main.tex, replace 'teh' with 'the'."
    "In the 'Introduction' section of main.tex, replace
     'preliminary results' with 'final results'."

For whole-file replacements, use ``rewrite_file``. For section-level
replacements where you only know the title (not the exact text), use
``update_section``.

.. tip::

    Every write tool accepts ``dry_run=true`` — it will pull, validate the
    edit, and tell you what it *would* have written, without committing.
    Useful before a destructive rewrite.

Creating
--------

.. code-block:: text

    "Create a new file appendix.tex with a supplementary-materials section."
    "Add a bibliography file references.bib with this BibTeX content: …"

The top-level ``create_project`` tool doesn't do a server-to-server
create — it returns an ``overleaf.com/docs?snip_uri=…`` URL for the user
to click. That's the only supported path; see :ref:`tool-create-project`.

Pull-before-edit
----------------

Write tools pass ``force_pull=True`` under the hood. If someone edited
the project in the Overleaf web editor while your server was running,
the write tool pulls first — so your commit is on top of the latest
base, not a stale one.

If the upstream is unreachable and a cached snapshot is available, the
tool attaches a ``⚠ could not refresh from Overleaf: ...`` warning to
the response. The write still runs against the local snapshot; the
push may fail downstream (reported in the same response).

When a write-rejection happens
------------------------------

If Overleaf moved ahead and your commit was rejected at push time, ask
for an explicit resync:

.. code-block:: text

    "Sync the project and retry the edit."

``sync_project`` is the diagnostic escape hatch — it force-pulls,
bypasses the TTL cache, and reports hard errors verbatim (no
stale-snapshot fallback).

Multi-project workflows
-----------------------

Every tool accepts an optional ``project_name`` argument matching a key
in ``overleaf_config.json``:

.. code-block:: text

    "In the 'paper' project, list all .tex files."
    "Edit main.tex in 'my-thesis' and change the title to …"

If you omit ``project_name``, the server uses ``defaultProject`` from
the config, or the first project if no default is set.

Observability in an agent loop
------------------------------

Two opt-in env flags help when tuning the server:

- :envvar:`OVERLEAF_TIMING` = ``1`` — emit one
  ``acquire_project project=… mode=… elapsed_ms=… stale=…`` INFO log line
  per tool call. Useful for latency regressions and for tuning
  :envvar:`OVERLEAF_PULL_TTL`.
- :envvar:`OVERLEAF_STRUCTURED` = ``1`` — append
  ``<mcp-envelope>{"ok":bool,"warnings":[...]}</mcp-envelope>`` to every
  tool response. Gives agentic clients a reliable parse target;
  plain-text clients see no difference.

Both are zero-cost when off (one ``os.environ.get`` per call). See
:doc:`environment` for the full env reference.
