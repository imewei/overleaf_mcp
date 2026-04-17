#!/usr/bin/env python3
"""Overleaf MCP Server — transport layer (FastMCP 3.x).

This module is deliberately tiny. It:

  1. Instantiates a :class:`fastmcp.FastMCP` server.
  2. Registers each function in :data:`overleaf_mcp.tools.TOOLS` as an MCP
     tool via ``@mcp.tool`` — schema inferred from the function's
     ``Annotated[..., Field(...)]`` signature and docstring.
  3. Runs stdio in :func:`main`.

All business logic lives in:

  * :mod:`~overleaf_mcp.config`  — pydantic models + config file loading
  * :mod:`~overleaf_mcp.latex`   — pure LaTeX section parsing
  * :mod:`~overleaf_mcp.git_ops` — git + async + per-project locking
  * :mod:`~overleaf_mcp.tools`   — the 15 tool implementations

Framework swap (raw MCP SDK → FastMCP) removed ~500 LOC of hand-written
JSON schema boilerplate. All schemas are now derived from type hints.
"""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import TOOLS

# MCP server instance. FastMCP's ``@mcp.tool`` decorator reads each
# registered function's signature + docstring and auto-generates the
# ``tools/list`` response that Claude and other MCP clients consume.
mcp: FastMCP = FastMCP("overleaf-mcp")

# Register every tool. We do this at import time (not lazily) so the
# server is fully populated the moment ``mcp`` is accessed — matching
# the behaviour of the old hand-written list_tools() coroutine.
for _name, _fn in TOOLS.items():
    mcp.tool(name=_name)(_fn)


def main() -> None:
    """Run the MCP server on stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
