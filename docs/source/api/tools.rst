``overleaf_mcp.tools``
======================

The 15 MCP tool implementations plus the ``TOOLS`` dict that registers
them. Each tool is a plain ``async def`` — tests call them directly
without going through the MCP protocol (see ``tests/test_dispatcher.py``).

The ``TOOLS`` mapping (tool name → async function) at the bottom of the
module is the single source of truth for "what tools exist".
``server.py`` iterates it at import time to register everything with
FastMCP.

.. automodule:: overleaf_mcp.tools
    :members:
    :undoc-members:
    :show-inheritance:
