# Architecture

How the server is put together, with the **why** next to the **what**. If
you're just installing the server, you want `README.md`. If you're
contributing code or integrating against unusual tool patterns, read
this.

---

## 1. Module Layers

```
┌───────────────────────────────────────────────────────────┐
│ server.py       transport: FastMCP 3.x, stdio             │
│                  (~40 lines, tool-agnostic)               │
├───────────────────────────────────────────────────────────┤
│ tools.py        15 async tool implementations              │
│                  + TOOLS dict + execute_tool() shim       │
├───────────────────────────────────────────────────────────┤
│ git_ops.py      the engine room:                           │
│                  - clone/pull with TTL cache               │
│                  - asyncio RW lock per project             │
│                  - _run_blocking → asyncio.wait_for         │
│                  - ToolContext + response envelope         │
├───────────────────────────────────────────────────────────┤
│ latex.py        pure re.finditer-based section parser      │
│ config.py       pydantic models + mtime-cached file load   │
└───────────────────────────────────────────────────────────┘
```

**Design invariant:** no layer imports downwards-only. `server.py`
imports `tools.TOOLS`; `tools.py` imports `git_ops` and `latex`;
`git_ops` imports `config`; `latex` / `config` import nothing from
inside the package. This means each layer is testable in isolation and
the framework (FastMCP) is one swap away from being replaced.

### Why separate `tools.py` from `server.py`?

The transport layer knows nothing about Overleaf; the tools know nothing
about MCP framing. Two consequences:

1. **Tests call tools as plain async functions** — no MCP protocol in the
   test loop. See `tests/test_dispatcher.py`.
2. **Framework swap** (raw MCP SDK → FastMCP 3.x in commit `58cea8e`)
   removed ~500 LOC of hand-written JSON schemas, without touching a
   single tool body.

---

## 2. Request Lifecycle

```
┌─────────────┐
│ Client (LLM)│
└──────┬──────┘
       │ JSON-RPC over stdio
       ▼
┌─────────────────────┐
│ fastmcp.FastMCP     │
│ (server.py, tiny)    │
└──────┬──────────────┘
       │ dispatch by name
       ▼
┌─────────────────────────────────────────────┐
│ tool function (tools.py)                     │
│                                               │
│ async with acquire_project(p, mode="..."):    │ ◄── single choke-point
│     ctx.repo.index.add(...)                   │     (git_ops.py)
│     await _run_blocking(commit, msg)          │
│     return ctx.wrap("result")                 │
└─────────────────────────────────────────────┘
```

Every tool goes through `acquire_project`. That context manager owns
three guarantees:

1. The repo is **cloned or acceptably fresh** before the body runs.
2. The right **concurrency mode** is held for the body's duration.
3. Any stale-snapshot fallback emits a **visible warning** in the
   response (no silent staleness).

---

## 3. Freshness + Concurrency (the two-phase dance)

`acquire_project` is two phases glued back-to-back:

### Phase 1 — Refresh (always exclusive)

```python
async with rwlock.exclusive():
    try:
        repo = await _run_blocking(ensure_repo, project, force_pull=...)
    except StaleRepoWarning as w:
        repo = Repo(get_repo_path(project.project_id))  # local snapshot
        warnings = [f"⚠ could not refresh from Overleaf: {w}"]
        stale = True
```

Why exclusive? Two concurrent pulls race on `.git/HEAD` and `.git/index`
— GitPython isn't thread-safe at those touchpoints. Serializing *just
the refresh* is cheap: when `OVERLEAF_PULL_TTL` is still valid,
`ensure_repo` is a wall-clock check + a `Repo(...)` open. Holding
exclusive for ~microseconds is free.

### Phase 2 — Body (read-shared, write-exclusive)

```python
body_lock = rwlock.shared() if mode == "read" else rwlock.exclusive()
async with body_lock:
    yield ToolContext(repo=repo, warnings=warnings)
```

Why split? Read tools only touch the working tree (`path.read_text`,
`ctx.repo.iter_commits`). They don't mutate `.git/index` or HEAD and
can run concurrently. Write tools mutate both, so they keep the old v1
"one at a time" serialization.

The lock is **writer-priority**: a pending writer blocks new readers,
which prevents reader starvation if a client hammers read tools while a
write is queued.

### Why is this keyed per-project?

```python
_PROJECT_RWLOCKS: dict[str, _RWLock] = {}
_LAST_PULL: dict[tuple[str, str], float] = {}
```

Each project has its own local clone at `$OVERLEAF_TEMP_DIR/<project_id>`.
Operations on project A and project B don't share state — serializing
them against each other would artificially cap throughput with zero
safety benefit.

The `_LAST_PULL` key is `(project_id, token_hash)`, not just
`project_id`. Today (single-client stdio) this behaves identically to
keying on project_id alone. Once the server supports HTTP multiplexing,
client A's freshness flag can't suppress a needed pull for client B
holding a different token — they may legitimately see different repo
state (one rotated / revoked / permissionless, one not).

---

## 4. Timeouts and Retries

Three layers guard against a wedged connection:

| Layer | Mechanism | Bounds |
|-------|-----------|--------|
| 1. asyncio | `asyncio.wait_for` on every blocking call | `OVERLEAF_GIT_TIMEOUT` (default 60s) — bounds **caller latency** |
| 2. libgit | `GIT_HTTP_LOW_SPEED_LIMIT` / `..._TIME` env | git child process self-aborts when throughput drops below the floor (default 1000 B/s for 30s) |
| 3. retry | one-shot transparent retry on transient patterns | regex on `str(GitCommandError)` — see `_TRANSIENT_PATTERNS` in `git_ops.py` |

The **caveat** is important: `asyncio.wait_for` bounds the coroutine,
not the OS thread or subprocess. Without layer 2, a black-holed TCP
would leak a thread per timed-out call. Layer 2 is what lets the thread
actually exit.

### What counts as transient?

`_TRANSIENT_PATTERNS` matches: `early EOF`, `connection reset`, `broken
pipe`, `could not resolve host`, `temporary failure in name resolution`,
`operation timed out`, `HTTP 5xx`, `hung up unexpectedly`,
`gnutls_handshake failed`, `ssl handshake failed`.

Authentication errors, missing refs, and permissions are **not**
transient — retrying would just waste a round-trip on a clearly-broken
request.

Retry delay is a uniform random draw from `[0.5, 1.5]s` (tests
monkey-patch this to `(0, 0)`).

---

## 5. Stale-Snapshot Fallback

If Phase 1 ultimately fails (both the initial pull and the retry, or
the error wasn't transient to begin with), `ensure_repo` raises
`StaleRepoWarning`. `acquire_project` catches it **only when a local
clone already exists**:

- Clone exists → serve with `warnings=["⚠ could not refresh ..."]`.
- No clone (cold start, no network) → error propagates out; no fallback
  possible.

Write tools and `sync_project` pass `force_pull=True`. For write tools
the fallback still applies (they'll fail later at push time if state
really is diverged) — this preserves "work on what you can see" ergonomics.
For `sync_project` the fallback is **suppressed**: the tool is the
diagnostic escape hatch, so a hard error is the correct response.

---

## 6. Observability

Two opt-in env flags, both zero-cost when off (single `os.environ.get`
per call):

### `OVERLEAF_STRUCTURED=1`

`ToolContext.wrap()` appends:

```
<mcp-envelope>{"ok": bool, "warnings": [...]}</mcp-envelope>
```

`ok` is `True` iff the body doesn't start with `Error:` and no warnings
are attached. This is a heuristic (the response text itself is still
human-oriented), but it gives agent clients a reliable substring to
grep for success.

### `OVERLEAF_TIMING=1`

Emits one INFO log line per tool call on context exit:

```
acquire_project project=abc123 mode=read elapsed_ms=4.2 stale=false
```

Useful for:
- Catching regressions after changes in `git_ops.py`.
- Tuning `OVERLEAF_PULL_TTL` — if `elapsed_ms` is consistently >500ms
  on read calls, the cache is missing.
- Debugging reader starvation (shouldn't happen under writer-priority,
  but it's nice to see the evidence).

---

## 7. What Lives Where (file-by-file)

### `server.py` (~40 LOC)

Just transport. Instantiates `FastMCP`, iterates `tools.TOOLS`,
registers each function. `main()` is a one-liner. This file almost
never changes.

### `tools.py` (the 15 tools)

Each tool is a plain `async def`. The `Annotated[..., Field(description=...)]`
signatures are read by FastMCP to generate the JSON schemas clients see.
Docstrings become tool descriptions.

The `TOOLS` dict at the bottom is the **single source of truth** for
"what tools exist". Adding a new tool is: (1) write the async function,
(2) add it to the dict. Registration and schema generation fall out.

### `git_ops.py` (the engine)

Everything that touches Git or the event loop. No tool logic here — just
the primitives tools compose:

- `_RWLock` — reader-writer lock with writer priority
- `_run_blocking` — asyncio.to_thread + wait_for
- `ensure_repo` — clone-or-pull with TTL
- `acquire_project` — the context manager every tool uses
- `validate_path` — path-escape guard (prevents `../` breakouts)
- `config_git_user` — idempotent `user.name` / `user.email` stamp

### `latex.py` (pure, no I/O)

`parse_sections(content)` and `get_section_by_title(content, title)`.
Zero dependencies on the rest of the package — reusable in isolation,
trivial to unit-test.

### `config.py` (pydantic)

`ProjectConfig`, `Config`, `load_config` (mtime-cached),
`get_project_config`, `resolve_project` (inline-credentials override).

---

## 8. Testing Shape

- `tests/test_dispatcher.py` — each tool called as a plain async function
  (no MCP protocol), end-to-end against a `file://` bare repo fixture
- `tests/test_optimizations.py` — TTL cache, RW lock semantics, retry,
  stale-snapshot fallback, structured envelope, timing log
- `tests/test_coverage_gaps.py` — the stragglers (error branches, rare
  pydantic paths)
- `tests/test_server.py` — FastMCP schema generation parity
- `tests/test_server_transport.py` — `main()` wiring

Coverage is at 99% (128 tests) as of the 1.1.0 line. The uncovered
branches are `pragma: no cover`-marked defensive paths.

---

## 9. Non-Goals

Things the server intentionally **does not** do:

- **No polling for Overleaf web changes** — the `git pull` model is
  simpler and sufficient. The TTL cache is already as near-real-time
  as agent loops benefit from.
- **No conflict resolution UI** — if a write tool's commit is rejected
  because Overleaf moved ahead, the error propagates verbatim. The
  agent can re-pull with `sync_project` and retry.
- **No custom LaTeX engine / build-side integration** — we touch the
  source tree only. PDF compilation stays on Overleaf's side.
- **No attempt to work around the `create_project` URL flow** — see
  the rationale in the `create_project` docstring; there is no
  supported REST endpoint.
