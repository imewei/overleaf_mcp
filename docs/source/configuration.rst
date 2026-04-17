Configuration
=============

The server reads credentials from, in order of precedence:

1. **Inline credentials** passed per-tool-call (``git_token`` +
   ``project_id`` kwargs)
2. A JSON config file at ``$OVERLEAF_CONFIG_FILE`` (default:
   ``overleaf_config.json``)
3. The ``OVERLEAF_PROJECT_ID`` + ``OVERLEAF_GIT_TOKEN`` environment
   variables (single-project mode)

At least one of (2) or (3) must be in place before any project-scoped
tool will succeed — ``list_projects`` is the only tool that works
without credentials.

Getting your Overleaf credentials
---------------------------------

.. admonition:: Paid plan required
    :class: important

    Overleaf's Git integration is a paid-plan feature. Free accounts
    will not have the **Menu → Git** entry.

1. **Project ID** — the last path segment of the project URL:

   .. code-block:: text

       https://www.overleaf.com/project/abc123def456
                                        ^^^^^^^^^^^^

2. **Git token**:

   - Open the project in the Overleaf web editor
   - Click **Menu** in the top-left
   - Under **Sync**, click **Git**
   - If no token exists yet, click **Generate token**
   - The URL shown is ``https://git:<TOKEN>@git.overleaf.com/...``
   - The token is the portion between ``git:`` and ``@``

.. warning::

    Git tokens provide full read/write access to the project. Never
    commit them to version control. ``overleaf_config.json`` is in
    ``.gitignore`` by default — keep it that way.

JSON config file
----------------

The canonical multi-project format:

.. code-block:: json

    {
      "projects": {
        "my-thesis": {
          "name": "My PhD Thesis",
          "projectId": "abc123def456",
          "gitToken": "olp_xxxxxxxxxxxxxxxxxxxx"
        },
        "paper": {
          "name": "Research Paper",
          "projectId": "xyz789ghi012",
          "gitToken": "olp_yyyyyyyyyyyyyyyyyyyy"
        }
      },
      "defaultProject": "my-thesis"
    }

Field reference:

.. list-table::
    :header-rows: 1
    :widths: 18 14 68

    * - Key
      - Type
      - Meaning
    * - ``projects.<key>``
      - object
      - One project entry. ``<key>`` is the name used in ``project_name``
        tool arguments.
    * - ``projects.<key>.name``
      - string
      - Display name used in tool responses.
    * - ``projects.<key>.projectId``
      - string
      - The project ID (from the Overleaf URL).
    * - ``projects.<key>.gitToken``
      - string
      - The Git token (from **Menu → Git**).
    * - ``defaultProject``
      - string
      - Key of the project used when ``project_name`` is omitted from a
        tool call. If absent, the first project in ``projects`` wins.

Config caching
~~~~~~~~~~~~~~

The server caches the parsed config by file mtime (:func:`overleaf_mcp.config.load_config`).
An unchanged file parses exactly once regardless of how many tool calls
run in a single session. Edit the file and the next tool call picks up
the new content without a server restart.

Environment-variable fallback
-----------------------------

For single-project setups (CI, scripts, one-off tests) the file is
optional:

.. code-block:: bash

    export OVERLEAF_PROJECT_ID=abc123def456
    export OVERLEAF_GIT_TOKEN=olp_xxxxxxxxxxxxxxxxxxxx

When both are set and the config file is absent, the server behaves as
if ``projects.default`` existed with ``defaultProject: "default"``.

Inline credentials (per-call override)
--------------------------------------

Every project-scoped tool accepts optional ``git_token`` + ``project_id``
kwargs. Passing **both** bypasses the config file entirely, without
touching the module-level cache. Useful for:

- **Multi-tenant stateless clients** — one process serving projects owned
  by different users who each hold their own token
- **Testing** — point a tool call at a ``file://`` bare repo without
  editing or environment-setting

The project is tagged internally as ``inline-<8hex>`` where the hex is
the first 8 characters of ``SHA-256(token)``. Two callers holding
different tokens for the same ``project_id`` are distinguishable in log
lines; the token itself never appears in names, logs, or errors.

.. admonition:: You must pass both
    :class: warning

    Passing ``git_token`` alone (or ``project_id`` alone) raises
    ``Inline credentials require both 'git_token' and 'project_id'``.
    Partial inline credentials have no meaningful fallback — we don't
    silently merge one inline field with one file field.

Local cache directory
---------------------

The server clones each project to ``$OVERLEAF_TEMP_DIR/<project_id>``
(default: ``./overleaf_cache``). Subsequent tool calls reuse the clone.

- **Safe to delete** between server runs — the next tool call will
  re-clone.
- **Never commit it to Git** — it's a full local mirror of the Overleaf
  project, including its credentials in the remote URL.
- **Use an absolute path in production** — a relative ``./overleaf_cache``
  is resolved against the server's ``cwd``, which is client-specific
  (Claude Desktop vs Claude Code vs VS Code all choose differently).

See :doc:`environment` for the full environment-variable reference.
