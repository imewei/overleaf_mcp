Architecture
============

How the server is put together, with the **why** next to the **what**.
Start with :doc:`quickstart` if you're installing; read this page if
you're contributing code or integrating against unusual tool patterns.

Module layers
-------------

.. code-block:: text

    ┌───────────────────────────────────────────────────────────┐
    │ server.py       transport: FastMCP 3.x, stdio             │
    │                  (~40 lines, tool-agnostic)               │
    ├───────────────────────────────────────────────────────────┤
    │ tools.py        15 async tool implementations             │
    │                  + TOOLS dict + execute_tool() shim       │
    ├───────────────────────────────────────────────────────────┤
    │ git_ops.py      the engine room:                          │
    │                  - clone/pull with TTL cache              │
    │                  - asyncio RW lock per project            │
    │                  - _run_blocking → asyncio.wait_for       │
    │                  - ToolContext + response envelope        │
    ├───────────────────────────────────────────────────────────┤
    │ latex.py        pure re.finditer-based section parser     │
    │ config.py       pydantic models + mtime-cached file load  │
    └───────────────────────────────────────────────────────────┘

**Design invariant:** imports flow downward only. ``server.py`` imports
``tools.TOOLS``; ``tools.py`` imports ``git_ops`` and ``latex``;
``git_ops`` imports ``config``; ``latex`` / ``config`` import nothing
from inside the package. Each layer is testable in isolation and the
framework (FastMCP) is one swap away from being replaced.

Why separate ``tools.py`` from ``server.py``?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The transport layer knows nothing about Overleaf; the tools know
nothing about MCP framing. Two consequences:

1. **Tests call tools as plain async functions** — no MCP protocol in
   the test loop. See ``tests/test_dispatcher.py``.
2. **Framework swap** (raw MCP SDK → FastMCP 3.x) removed ~500 LOC of
   hand-written JSON schemas without touching a single tool body.

Request lifecycle
-----------------

.. code-block:: text

    ┌─────────────┐
    │ Client (LLM)│
    └──────┬──────┘
           │ JSON-RPC over stdio
           ▼
    ┌─────────────────────┐
    │ fastmcp.FastMCP     │
    │ (server.py, tiny)   │
    └──────┬──────────────┘
           │ dispatch by name
           ▼
    ┌─────────────────────────────────────────────┐
    │ tool function (tools.py)                    │
    │                                             │
    │ async with acquire_project(p, mode="..."):  │ ◄── single choke-point
    │     ctx.repo.index.add(...)                 │     (git_ops.py)
    │     await _run_blocking(commit, msg)        │
    │     return ctx.wrap("result")               │
    └─────────────────────────────────────────────┘

Every tool goes through :func:`~overleaf_mcp.git_ops.acquire_project`.
That context manager owns three guarantees:

1. The repo is **cloned or acceptably fresh** before the body runs.
2. The right **concurrency mode** is held for the body's duration.
3. Any stale-snapshot fallback emits a **visible warning** in the
   response — no silent staleness.

Freshness + concurrency (the two-phase dance)
---------------------------------------------

:func:`~overleaf_mcp.git_ops.acquire_project` is two phases
glued back-to-back.

Phase 1 — refresh (always exclusive)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    async with rwlock.exclusive():
        try:
            repo = await _run_blocking(ensure_repo, project, force_pull=...)
        except StaleRepoWarning as w:
            repo = Repo(get_repo_path(project.project_id))  # local snapshot
            warnings = [f"⚠ could not refresh from Overleaf: {w}"]
            stale = True

Why exclusive? Two concurrent pulls race on ``.git/HEAD`` and
``.git/index`` — GitPython isn't thread-safe at those touchpoints.
Serializing *just the refresh* is cheap: when :envvar:`OVERLEAF_PULL_TTL`
is still valid, ``ensure_repo`` is a wall-clock check + a ``Repo(...)``
open. Holding exclusive for microseconds is free.

Phase 2 — body (read-shared, write-exclusive)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    body_lock = rwlock.shared() if mode == "read" else rwlock.exclusive()
    async with body_lock:
        yield ToolContext(repo=repo, warnings=warnings)

Why split? Read tools only touch the working tree — they don't mutate
``.git/index`` or HEAD and can run concurrently. Write tools mutate
both, so they keep the old v1 "one at a time" serialization.

The lock is **writer-priority**: a pending writer blocks new readers,
which prevents reader starvation if a client hammers read tools while
a write is queued.

Why keyed per-project
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    _PROJECT_RWLOCKS: dict[str, _RWLock] = {}
    _LAST_PULL: dict[tuple[str, str], float] = {}

Each project has its own local clone at
``$OVERLEAF_TEMP_DIR/<project_id>``. Operations on project A and
project B don't share state — serializing them against each other
would artificially cap throughput with zero safety benefit.

The ``_LAST_PULL`` key is ``(project_id, token_hash)``, not just
``project_id``. Today (single-client stdio) this behaves identically
to keying on ``project_id`` alone. Once the server supports HTTP
multiplexing, client A's freshness flag can't suppress a needed pull
for client B holding a different token — they may legitimately see
different repo state.

Timeouts and retries
--------------------

Three layers guard against a wedged connection.

.. list-table::
    :header-rows: 1
    :widths: 10 40 50

    * - Layer
      - Mechanism
      - Bounds
    * - **asyncio**
      - ``asyncio.wait_for`` on every blocking call
      - :envvar:`OVERLEAF_GIT_TIMEOUT` (default 60s) — caller latency
    * - **libgit**
      - :envvar:`GIT_HTTP_LOW_SPEED_LIMIT` /
        :envvar:`GIT_HTTP_LOW_SPEED_TIME`
      - git child aborts when throughput drops below the floor
        (default 1000 B/s for 30s)
    * - **retry**
      - one-shot transparent retry on transient patterns
      - regex on ``str(GitCommandError)`` — see ``_TRANSIENT_PATTERNS``

The **caveat**: ``asyncio.wait_for`` bounds the coroutine, not the OS
thread or subprocess. Without layer 2, a black-holed TCP would leak a
thread per timed-out call. Layer 2 is what lets the thread actually
exit.

What counts as transient?
~~~~~~~~~~~~~~~~~~~~~~~~~

``_TRANSIENT_PATTERNS`` matches: ``early EOF``, ``connection reset``,
``broken pipe``, ``could not resolve host``, ``temporary failure in
name resolution``, ``operation timed out``, ``HTTP 5xx``, ``hung up
unexpectedly``, ``gnutls_handshake failed``, ``ssl handshake failed``.

Authentication errors, missing refs, and permissions are **not**
transient — retrying would just waste a round-trip on a clearly
broken request.

Retry delay is a uniform random draw from ``[0.5, 1.5]`` seconds
(tests monkey-patch this to ``(0, 0)``).

Stale-snapshot fallback
-----------------------

If Phase 1 ultimately fails — both the initial pull and the retry, or
the error wasn't transient to begin with —
:func:`~overleaf_mcp.git_ops.ensure_repo` raises
:class:`~overleaf_mcp.git_ops.StaleRepoWarning`.
``acquire_project`` catches it **only when a local clone already exists**:

- Clone exists → serve with ``warnings=["⚠ could not refresh ..."]``.
- No clone (cold start, no network) → error propagates out; no
  fallback possible.

Write tools and ``sync_project`` pass ``force_pull=True``. For write
tools the fallback still applies — they'll fail later at push time if
state really is diverged, preserving "work on what you can see"
ergonomics. For ``sync_project`` the fallback is **suppressed**: the
tool is the diagnostic escape hatch, so a hard error is the correct
response.

Observability
-------------

Two opt-in env flags, both zero-cost when off (single
``os.environ.get`` per call).

OVERLEAF_STRUCTURED
~~~~~~~~~~~~~~~~~~~

``ToolContext.wrap()`` appends:

.. code-block:: text

    <mcp-envelope>{"ok": bool, "warnings": [...]}</mcp-envelope>

``ok`` is ``true`` iff the body doesn't start with ``Error:`` and no
warnings are attached. This is a heuristic — the response text itself
is still human-oriented — but it gives agent clients a reliable
substring to grep for success.

OVERLEAF_TIMING
~~~~~~~~~~~~~~~

Emits one INFO log line per tool call on context exit:

.. code-block:: text

    acquire_project {"project":"abc123","mode":"read","elapsed_ms":4.2,"stale":false}

Useful for:

- Catching regressions after changes in ``git_ops.py``.
- Tuning :envvar:`OVERLEAF_PULL_TTL` — if ``elapsed_ms`` is consistently
  >500ms on read calls, the cache is missing.
- Debugging reader starvation (shouldn't happen under writer-priority,
  but it's nice to see the evidence).

What lives where (file-by-file)
-------------------------------

.. list-table::
    :header-rows: 1
    :widths: 24 16 60

    * - File
      - LOC
      - Responsibility
    * - ``server.py``
      - ~40
      - Transport only. Almost never changes.
    * - ``tools.py``
      - ~1000
      - 15 async tool implementations + ``TOOLS`` dict + dispatcher shim.
    * - ``git_ops.py``
      - ~750
      - Everything that touches Git or the event loop.
    * - ``latex.py``
      - ~60
      - ``parse_sections`` + ``get_section_by_title``. Pure, no I/O.
    * - ``config.py``
      - ~150
      - pydantic models + mtime-cached loader.

Testing shape
-------------

- ``tests/test_dispatcher.py`` — each tool called as a plain async
  function (no MCP protocol), end-to-end against a ``file://`` bare
  repo fixture.
- ``tests/test_optimizations.py`` — TTL cache, RW lock semantics,
  retry, stale-snapshot fallback, structured envelope, timing log.
- ``tests/test_coverage_gaps.py`` — the stragglers (error branches,
  rare pydantic paths).
- ``tests/test_server.py`` — FastMCP schema generation parity.
- ``tests/test_server_transport.py`` — ``main()`` wiring.

Coverage is at 99% (128 tests) as of the 1.1.0 line. The uncovered
branches are ``pragma: no cover``-marked defensive paths.

Non-goals
---------

Things the server intentionally **does not** do:

- **No polling for Overleaf web changes** — the ``git pull`` model is
  simpler and sufficient. The TTL cache is already as near-real-time
  as agent loops benefit from.
- **No conflict resolution UI** — if a write tool's commit is rejected
  because Overleaf moved ahead, the error propagates verbatim. The
  agent can re-pull with ``sync_project`` and retry.
- **No custom LaTeX engine or build-side integration** — we touch the
  source tree only. PDF compilation stays on Overleaf's side.
- **No attempt to work around the** ``create_project`` **URL flow** —
  see the rationale in the tool's docstring; there is no supported
  REST endpoint.
