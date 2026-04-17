Quick start
===========

This page takes you from a fresh clone to a working tool call in about
five minutes. If you need more detail on any step, follow the links in
each section.

1. Install
----------

.. code-block:: bash

    git clone https://github.com/imewei/overleaf-mcp.git
    cd overleaf-mcp
    uv sync

Full detail: :doc:`installation`.

2. Get your Overleaf credentials
--------------------------------

1. **Project ID** — open the project in your browser, copy the last
   path segment of the URL:

   .. code-block:: text

       https://www.overleaf.com/project/abc123def456
                                        ^^^^^^^^^^^^

2. **Git token** — in the project: **Menu** → **Git** → **Generate token**.
   The URL shown is ``https://git:TOKEN@git.overleaf.com/...``; the token
   is the portion between ``git:`` and ``@``.

3. Create a config file
-----------------------

Save the file as ``overleaf_config.json`` in the repo root:

.. code-block:: json

    {
      "projects": {
        "my-thesis": {
          "name": "My PhD Thesis",
          "projectId": "abc123def456",
          "gitToken": "olp_xxxxxxxxxxxxxxxxxxxx"
        }
      },
      "defaultProject": "my-thesis"
    }

Multi-project and environment-variable alternatives: :doc:`configuration`.

4. Wire it up to a client
-------------------------

.. tab-set::

    .. tab-item:: Claude Desktop

        Add to ``~/Library/Application Support/Claude/claude_desktop_config.json``
        (macOS) — see :doc:`client_setup` for Windows/Linux paths:

        .. code-block:: json

            {
              "mcpServers": {
                "overleaf": {
                  "command": "/abs/path/to/overleaf-mcp/.venv/bin/python",
                  "args": ["-m", "overleaf_mcp.server"],
                  "cwd": "/abs/path/to/overleaf-mcp"
                }
              }
            }

        Restart Claude Desktop (``Cmd+Q`` / ``Ctrl+Q``, reopen).

    .. tab-item:: Claude Code

        Add the same ``mcpServers`` block to ``~/.claude/settings.json``.

    .. tab-item:: VS Code

        Add a ``claude.mcpServers`` block to ``.vscode/settings.json``.

5. Try your first tool call
---------------------------

Ask the assistant something that exercises a read tool:

.. code-block:: text

    List all .tex files in my thesis

For an unfamiliar project, ``status_summary`` is the best first call —
it combines file inventory + last commit + main-doc section tree in one
TTL-cached pull:

.. code-block:: text

    Summarize my thesis project — I just forgot where I left off

Common next questions
---------------------

.. dropdown:: How do I switch between multiple projects?
    :icon: question

    Add another entry to ``projects`` in ``overleaf_config.json``, and
    refer to it by key (``"paper"``, ``"thesis"``, etc.) when asking
    the assistant. See :doc:`configuration`.

.. dropdown:: My write tool failed with "local branch behind upstream". What now?
    :icon: question

    Someone edited the project in the Overleaf web editor while your
    server had a cached snapshot. Ask the assistant to
    ``sync_project`` — that force-pulls and bypasses the TTL cache. See
    :ref:`tool-sync-project`.

.. dropdown:: I see "⚠ could not refresh from Overleaf" — is my data safe?
    :icon: question

    Yes. The tool served its response from the last-good local snapshot
    and attached the warning so you know. One transparent retry already
    ran — the underlying error is persistent. Call ``sync_project``
    explicitly to see the hard error.
