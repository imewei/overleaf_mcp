Client setup
============

The server talks MCP over stdio. Any MCP-aware client can use it; the
three most common are Claude Desktop, Claude Code, and VS Code with the
Claude extension.

.. important::

    Always use **absolute paths** for ``command`` and ``cwd``. Relative
    paths are resolved against the client's working directory, which is
    not your shell's working directory.

Claude Desktop
--------------

**Config file location**

.. list-table::
    :header-rows: 1
    :widths: 18 82

    * - Platform
      - Path
    * - macOS
      - ``~/Library/Application Support/Claude/claude_desktop_config.json``
    * - Windows
      - ``%APPDATA%\Claude\claude_desktop_config.json``
    * - Linux
      - ``~/.config/Claude/claude_desktop_config.json``

**Configuration**

.. code-block:: json

    {
      "mcpServers": {
        "overleaf": {
          "command": "/path/to/overleaf-mcp/.venv/bin/python",
          "args": ["-m", "overleaf_mcp.server"],
          "cwd": "/path/to/overleaf-mcp",
          "env": {
            "OVERLEAF_CONFIG_FILE": "/path/to/overleaf-mcp/overleaf_config.json",
            "OVERLEAF_TEMP_DIR": "/path/to/overleaf-mcp/overleaf_cache"
          }
        }
      }
    }

After saving, **fully quit and reopen Claude Desktop** (``Cmd+Q`` /
``Ctrl+Q``). Soft-closing the window is not enough.

Claude Code (CLI)
-----------------

Add the same ``mcpServers`` block to ``~/.claude/settings.json`` (user
scope) or ``.claude/settings.json`` (repo scope).

.. code-block:: json

    {
      "mcpServers": {
        "overleaf": {
          "command": "/path/to/overleaf-mcp/.venv/bin/python",
          "args": ["-m", "overleaf_mcp.server"],
          "cwd": "/path/to/overleaf-mcp",
          "env": {
            "OVERLEAF_CONFIG_FILE": "/path/to/overleaf-mcp/overleaf_config.json",
            "OVERLEAF_TEMP_DIR": "/path/to/overleaf-mcp/overleaf_cache"
          }
        }
      }
    }

Per-project config in ``.claude/settings.json`` takes precedence over
the user-scope file — handy for point one clone at each of several
projects without cross-pollution.

VS Code (Claude extension)
--------------------------

Add to ``settings.json``:

.. code-block:: json

    {
      "claude.mcpServers": {
        "overleaf": {
          "command": "/path/to/overleaf-mcp/.venv/bin/python",
          "args": ["-m", "overleaf_mcp.server"],
          "cwd": "/path/to/overleaf-mcp",
          "env": {
            "OVERLEAF_CONFIG_FILE": "/path/to/overleaf-mcp/overleaf_config.json",
            "OVERLEAF_TEMP_DIR": "/path/to/overleaf-mcp/overleaf_cache"
          }
        }
      }
    }

Use ``.vscode/settings.json`` for workspace-scoped config.

MCPB bundle (Claude Desktop, no-toolchain install)
--------------------------------------------------

The repo ships an MCPB bundler (``mcpb/build-mcpb.sh``) that produces a
single-file archive users can drag onto Claude Desktop:

.. code-block:: bash

    ./mcpb/build-mcpb.sh
    # → dist/overleaf-mcp-<version>.mcpb

The bundle is zero-toolchain for the end user: no ``pip``, no ``uv``,
no virtualenv. What's inside:

.. code-block:: text

    overleaf-mcp-<version>.mcpb
    ├── manifest.json           # identity + user_config schema
    └── server/
        ├── bootstrap.py        # prepends vendor/ to sys.path, calls main()
        ├── vendor/             # runtime deps (pip install --target)
        └── overleaf_mcp/       # source tree (copied, not symlinked)

What's **not** in the bundle:

- The Python interpreter (the host OS must provide it)
- The ``git`` binary (must still be on ``PATH``)
- Dev / docs dependencies

When users drop the bundle onto Claude Desktop, the host reads
``manifest.json`` and surfaces the ``user_config`` fields as a native
form. The Git token field is marked ``sensitive: true`` — Claude Desktop
stores it in the OS keychain.

Verifying the hookup
--------------------

After restarting the client, the "tools available" UI should list 15
entries starting with ``overleaf__``. If it doesn't, the most common
causes are:

- **Invalid JSON** in the settings file (trailing comma, missing quote)
- **Relative path** in ``command`` or ``cwd`` (must be absolute)
- **Missing ``git`` binary** in the environment the client launched with —
  Claude Desktop on macOS does *not* inherit your shell's ``PATH``;
  either install ``git`` system-wide or set ``PATH`` in the ``env`` block
- **``OVERLEAF_CONFIG_FILE`` unset** and no ``overleaf_config.json`` in
  the ``cwd`` — ``list_projects`` will return "No projects configured"

Open the client's MCP log panel (if available) and look for the
server's startup line.
