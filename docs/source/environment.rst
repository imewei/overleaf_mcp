Environment variables
=====================

All runtime knobs are environment-overridable so the same code runs in
development, CI, and production without edits.

Credentials and paths
---------------------

.. envvar:: OVERLEAF_CONFIG_FILE

    Path to the JSON config file. Default: ``overleaf_config.json``
    (resolved against the server's ``cwd``). Use an **absolute path** in
    client configs — the server's ``cwd`` is client-specific.

.. envvar:: OVERLEAF_TEMP_DIR

    Local cache directory where per-project clones live. Default:
    ``./overleaf_cache``. Each project gets a subdirectory named by its
    ``project_id``. Safe to delete between runs — the next tool call
    will re-clone.

.. envvar:: OVERLEAF_PROJECT_ID

    Single-project fallback. If :envvar:`OVERLEAF_CONFIG_FILE` points to
    a missing file, the server builds a synthetic one-project config
    from this + :envvar:`OVERLEAF_GIT_TOKEN`.

.. envvar:: OVERLEAF_GIT_TOKEN

    Single-project fallback. Must be set alongside
    :envvar:`OVERLEAF_PROJECT_ID`.

.. envvar:: OVERLEAF_GIT_URL

    Base URL for Overleaf's Git endpoint. Default:
    ``https://git.overleaf.com``. Override for self-hosted Overleaf
    deployments or for local testing (``file:///tmp/bare-repo`` fixtures).

Git author identity
-------------------

.. envvar:: OVERLEAF_GIT_AUTHOR_NAME

    Author name stamped on commits. Default: ``Overleaf MCP``.

.. envvar:: OVERLEAF_GIT_AUTHOR_EMAIL

    Author email stamped on commits. Default: ``mcp@overleaf.local``.

Performance and timeouts
------------------------

.. envvar:: OVERLEAF_PULL_TTL

    Seconds within which a successful pull is considered "fresh enough"
    to skip on subsequent **read-only** tool calls. Default: ``30``.

    - ``0`` disables the cache (pull on every call — the 1.0.x behavior).
    - ``30`` is a good default for interactive agent loops.

    Write tools always pass ``force_pull=True`` and ignore this cache.

.. envvar:: OVERLEAF_GIT_TIMEOUT

    Hard upper bound (seconds) on any blocking Git operation. Default:
    ``60``. Protects the MCP stdio reader from a wedged connection.

.. envvar:: OVERLEAF_SHALLOW_CLONE

    Set to ``1`` to use ``--depth=N`` shallow clones for new projects.
    Default: ``0`` (full history). A huge cold-start win on multi-GB
    projects, at the cost of limiting ``list_history`` to the shallow
    depth.

.. envvar:: OVERLEAF_SHALLOW_DEPTH

    Depth for shallow clones. Default: ``1``. Ignored when
    :envvar:`OVERLEAF_SHALLOW_CLONE` is ``0``.

.. envvar:: GIT_HTTP_LOW_SPEED_LIMIT

    Bytes/sec floor — Git's own subprocess aborts when throughput drops
    below this. Default: ``1000``. This is the **subprocess-level
    backstop** that lets an asyncio-cancelled thread actually exit.

.. envvar:: GIT_HTTP_LOW_SPEED_TIME

    Seconds that throughput must stay below
    :envvar:`GIT_HTTP_LOW_SPEED_LIMIT` before the subprocess aborts.
    Default: ``30``.

Observability
-------------

.. envvar:: OVERLEAF_STRUCTURED

    Set to ``1`` to append
    ``<mcp-envelope>{"ok":bool,"warnings":[...]}</mcp-envelope>`` to
    every tool response. Default: ``0`` (plain text). Zero cost when
    off — a single ``os.environ.get`` per call.

.. envvar:: OVERLEAF_TIMING

    Set to ``1`` to emit one structured INFO log line per tool call:

    .. code-block:: text

        acquire_project {"project":"<id>","mode":"read|write","elapsed_ms":42.3,"stale":false}

    The ``acquire_project`` prefix and the four JSON keys
    (``project``, ``mode``, ``elapsed_ms``, ``stale``) are a
    **stable interface** — external monitoring pipelines may parse this
    line. Additional keys may be added in future releases (e.g. ``tool``)
    but existing keys won't be renamed or removed without a major
    version bump.

    Default: ``0`` (silent). Zero cost when off.

Quick reference
---------------

.. list-table::
    :header-rows: 1
    :widths: 32 12 56

    * - Variable
      - Default
      - Summary
    * - :envvar:`OVERLEAF_CONFIG_FILE`
      - ``overleaf_config.json``
      - Config file path
    * - :envvar:`OVERLEAF_TEMP_DIR`
      - ``./overleaf_cache``
      - Local clone cache directory
    * - :envvar:`OVERLEAF_PROJECT_ID`
      - —
      - Single-project fallback project ID
    * - :envvar:`OVERLEAF_GIT_TOKEN`
      - —
      - Single-project fallback Git token
    * - :envvar:`OVERLEAF_GIT_URL`
      - ``https://git.overleaf.com``
      - Git endpoint (override for self-hosted / tests)
    * - :envvar:`OVERLEAF_GIT_AUTHOR_NAME`
      - ``Overleaf MCP``
      - Commit author name
    * - :envvar:`OVERLEAF_GIT_AUTHOR_EMAIL`
      - ``mcp@overleaf.local``
      - Commit author email
    * - :envvar:`OVERLEAF_PULL_TTL`
      - ``30``
      - Read-tool freshness cache (seconds)
    * - :envvar:`OVERLEAF_GIT_TIMEOUT`
      - ``60``
      - Hard Git-op ceiling (seconds)
    * - :envvar:`OVERLEAF_SHALLOW_CLONE`
      - ``0``
      - Enable ``--depth=N`` clones
    * - :envvar:`OVERLEAF_SHALLOW_DEPTH`
      - ``1``
      - Depth for shallow clones
    * - :envvar:`OVERLEAF_STRUCTURED`
      - ``0``
      - Append structured envelope to responses
    * - :envvar:`OVERLEAF_TIMING`
      - ``0``
      - Emit one INFO log line per tool call
    * - :envvar:`GIT_HTTP_LOW_SPEED_LIMIT`
      - ``1000``
      - Subprocess throughput floor (bytes/sec)
    * - :envvar:`GIT_HTTP_LOW_SPEED_TIME`
      - ``30``
      - Seconds below floor before subprocess aborts
