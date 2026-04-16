"""Transport-layer tests for server.py.

These tests exercise the FastMCP registration loop and main() without
spawning a stdio subprocess — a full end-to-end smoke test would require
pipe plumbing that isn't worth ~8 LOC of transport code. Instead we:

  1. Verify that importing server registers every tool from TOOLS. This
     catches regressions like "someone added a tool to TOOLS but the
     loop was reordered and skipped it".
  2. Verify main() delegates to mcp.run() with no arguments. This catches
     regressions like "someone added a required arg to run() and main()
     wasn't updated".
"""
from __future__ import annotations

import asyncio

import pytest

from overleaf_mcp import server
from overleaf_mcp.tools import TOOLS


def test_every_tool_in_registry_is_registered_on_mcp():
    """server.mcp must expose every function in TOOLS — no silent drops."""
    registered = {t.name for t in asyncio.run(server.mcp._list_tools())}
    expected = set(TOOLS.keys())
    assert registered == expected, (
        f"Registration drift — in TOOLS but not on mcp: {expected - registered}; "
        f"on mcp but not in TOOLS: {registered - expected}"
    )


def test_main_delegates_to_mcp_run(monkeypatch: pytest.MonkeyPatch):
    """main() must call mcp.run() with no arguments.

    We stub mcp.run so the test doesn't actually open stdio — the point
    of the test is to verify the one-line body of main(), not to run a
    live server.
    """
    calls: list[tuple] = []
    monkeypatch.setattr(server.mcp, "run", lambda *a, **kw: calls.append((a, kw)))
    server.main()
    assert calls == [((), {})], f"Expected run() called once with no args, got {calls}"
