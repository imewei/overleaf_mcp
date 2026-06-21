Installation
============

Prerequisites
-------------

- **Python 3.10+** (tested on 3.10, 3.11, 3.12, 3.13, 3.14)
- **Git** (GitPython shells out to the system binary — no binary is bundled)
- An **Overleaf account with Git integration enabled** (requires a paid plan)

Install from source (pip)
-------------------------

.. code-block:: bash

    git clone https://github.com/imewei/overleaf-mcp.git
    cd overleaf-mcp

    python3 -m venv .venv
    source .venv/bin/activate        # On Windows: .venv\Scripts\activate

    pip install -e .

Install from source (uv — recommended)
--------------------------------------

.. code-block:: bash

    git clone https://github.com/imewei/overleaf-mcp.git
    cd overleaf-mcp

    uv sync                          # runtime deps only
    uv sync --extra dev              # + test / lint / type-check tools
    uv sync --extra docs             # + Sphinx toolchain (this site)

``uv sync`` resolves from ``pyproject.toml``. The ``uv.lock`` file is
``.gitignore``\d in this repo (CI resolves fresh on each run); if you
need byte-identical environments across machines, pin exact versions in
``pyproject.toml`` or check in ``uv.lock`` locally.

Install as an MCPB bundle (Claude Desktop)
-------------------------------------------

For a zero-toolchain install (no ``pip`` involved for the end user):

.. code-block:: bash

    # From a cloned repo
    ./mcpb/build-mcpb.sh
    # → dist/overleaf-mcp-1.1.0.mcpb

Drag ``dist/overleaf-mcp-<version>.mcpb`` onto Claude Desktop. The
bundle embeds the Python runtime dependencies (``mcp``, ``fastmcp``,
``gitpython``, ``pydantic``) but not the Python interpreter or the
``git`` binary — both must still be on ``PATH``.

Claude Desktop stores the Git token in the OS keychain (the ``git_token``
field in ``manifest.json`` is marked ``sensitive: true``).

Verifying the install
---------------------

.. code-block:: bash

    uv run overleaf-mcp --help       # prints nothing — stdio server, not a CLI
    uv run python -m overleaf_mcp.server  # starts the server on stdio

The server talks **stdio**; it expects an MCP client on the other end.
Use an MCP client (Claude Desktop / Claude Code / VS Code) to exercise
it — see :doc:`client_setup`.

Next steps
----------

- Create a config file — :doc:`configuration`
- Wire it up to Claude — :doc:`client_setup`
- Try your first tool call — :doc:`quickstart`
