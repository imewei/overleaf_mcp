``overleaf_mcp.git_ops``
========================

The engine room. Owns everything that touches Git or the asyncio event
loop: clone/pull with TTL cache, per-project reader-writer locking,
hard timeouts, transient-failure retry, and the ``ToolContext`` handle
every tool uses to compose its response.

.. automodule:: overleaf_mcp.git_ops
    :members:
    :undoc-members:
    :show-inheritance:
    :private-members: _RWLock, _TransientPullError, _redact_url, _run_blocking, _lock_for
