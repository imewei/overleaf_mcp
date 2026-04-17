API reference
=============

Auto-generated from docstrings in ``src/overleaf_mcp``. Every public
module, class, and function is documented below; the ``[source]`` link
on each symbol jumps to the exact line.

Module index
------------

.. list-table::
    :header-rows: 1
    :widths: 28 72

    * - Module
      - Responsibility
    * - :doc:`server`
      - Transport layer (FastMCP 3.x) — ``~40`` lines, tool-agnostic.
    * - :doc:`tools`
      - 15 async tool implementations + ``TOOLS`` dict + dispatcher shim.
    * - :doc:`git_ops`
      - Clone/pull, reader-writer lock, TTL cache, retry, timeouts, envelope.
    * - :doc:`config`
      - pydantic models + config-file / env-var loading with mtime cache.
    * - :doc:`latex`
      - Pure-function LaTeX section parser — no I/O, reusable in isolation.

Module detail
-------------

.. toctree::
    :maxdepth: 1

    server
    tools
    git_ops
    config
    latex
