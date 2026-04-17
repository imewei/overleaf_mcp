overleaf-mcp
============

.. rubric:: A Model Context Protocol server for Overleaf LaTeX projects.

**overleaf-mcp** exposes 15 MCP tools that let an AI assistant read,
search, edit, create, and delete files in an Overleaf project. It talks
to Overleaf over the supported Git integration — no scraping, no session
cookies, no ToS-risky endpoints.

.. grid:: 2
    :gutter: 3
    :margin: 2

    .. grid-item-card:: 🚀 Quick start
        :link: quickstart
        :link-type: doc

        Install, configure credentials, and get your first tool call
        through Claude Desktop in under five minutes.

    .. grid-item-card:: 🛠 Tool reference
        :link: tools
        :link-type: doc

        Full per-tool catalogue: parameters, defaults, refresh semantics,
        and error behavior for all 15 tools.

    .. grid-item-card:: 🏛 Architecture
        :link: architecture
        :link-type: doc

        How the refresh → lock → envelope pipeline works, and why it
        looks the way it does.

    .. grid-item-card:: 📖 API
        :link: api/index
        :link-type: doc

        Module-level Python API reference, auto-generated from
        docstrings in ``src/overleaf_mcp``.

Highlights
----------

- **15 tools, full CRUD** — create, read, update, delete on any Overleaf
  project reachable by Git token.
- **Reader-writer locking per project** — parallel reads, exclusive writes,
  writer priority so readers can't starve writers.
- **TTL-cached pulls** — agents exploring a project pay one network
  round-trip per burst, not per tool call.
- **Visible staleness** — if a refresh fails but a local snapshot exists,
  the response attaches a warning line instead of silently serving
  stale data.
- **Bounded hangs** — every Git operation has a hard timeout ceiling
  (``OVERLEAF_GIT_TIMEOUT``); a wedged connection can't freeze the server.
- **FastMCP 3.x** — JSON schemas are inferred from ``Annotated`` + ``Field``
  signatures; no hand-written schema code.

.. toctree::
   :maxdepth: 2
   :caption: Getting started
   :hidden:

   installation
   quickstart
   configuration
   client_setup

.. toctree::
   :maxdepth: 2
   :caption: Usage
   :hidden:

   usage
   tools
   environment

.. toctree::
   :maxdepth: 2
   :caption: Internals
   :hidden:

   architecture
   development
   changelog

.. toctree::
   :maxdepth: 2
   :caption: API reference
   :hidden:

   api/index

Indices
-------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
