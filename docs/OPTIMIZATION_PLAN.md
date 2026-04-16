# Overleaf MCP — Ultradeep Optimization Plan

**Generated:** 2026-04-15
**Mode:** `/ultra-think --depth=ultradeep`
**Scope:** Efficiency · Effectiveness · Performance Stability
**Target:** `src/overleaf_mcp/server.py` (1358 LOC, single file)

---

## Executive Summary

The Overleaf MCP is a functionally complete **Git-backed stdio MCP server** that exposes 15 tools. It works, but it leaks performance and stability in three ways:

1. **Every read tool pays a network round-trip** (`ensure_repo()` pulls unconditionally — server.py:145-165)
2. **Blocking Git subprocesses run on the asyncio event loop**, serializing all requests and risking stdio stalls
3. **Silent failure modes** (bare `except GitCommandError: pass`, error-as-success `TextContent`) hide real problems from clients

Fixing the top-5 hotspots below will roughly **halve average tool latency on cached projects, eliminate hang risk, and turn silent failures into actionable diagnostics** — with no API breakage.

### Recommended Tier-0 Actions (ship this week)

| # | Change | Impact | Risk | LOC |
|---|--------|--------|------|-----|
| 1 | Staleness-aware pull in `ensure_repo()` (TTL cache) | High | Low | ~25 |
| 2 | Memoize `load_config()` with mtime invalidation | Med | Low | ~15 |
| 3 | Wrap every Git call in `asyncio.to_thread()` | High | Low | ~40 |
| 4 | Add timeouts to `pull`/`push`/`clone` via `GIT_HTTP_LOW_SPEED_*` | High | Low | ~10 |
| 5 | Surface pull failures (warn in response, don't silently pass) | Med | Low | ~8 |

---

## Ultra-Think Reasoning Trace

### T1 — Problem Framing

- **T1.1** Core question: which changes in `overleaf_mcp` deliver the largest latency/stability gains per line of code touched, without breaking the 15-tool contract?
- **T1.2** Constraints: preserve stdio transport, preserve every tool name and schema, preserve `dry_run`/`push` toggles and inline credential overrides (commits eab8e59 and 29d87d7), preserve `overleaf_cache/` layout, run on Python ≥3.10.
- **T1.3** Assumptions: users are on trusted local machines; Overleaf Git endpoint is the only required upstream; concurrent tool calls from a single client are rare but possible; a human is watching for error output.
- **T1.4** Success criteria: (a) a warm `read_file` call on an unchanged project ≤ 50 ms excluding filesystem I/O, (b) no tool can block the stdio reader for > 30 s, (c) Git network failures are visible in tool output, (d) repeated tool calls don't re-parse `overleaf_config.json`.
- **T1.5** Framework: Systems Thinking (map call graph + feedback loops) + Root Cause Analysis on observed latency sinks.

### T2 — Call-Graph Model

```
client stdio → asyncio.run(main)
  → server.call_tool()               [server.py:740]
    → execute_tool()                  [server.py:750]
      → resolve_project()             [server.py:125]  ← load_config() EVERY CALL
      → ensure_repo()                 [server.py:145]  ← origin.pull() EVERY CALL
        → GitPython.remote.pull()     [blocking subprocess]
      → tool-specific filesystem ops
      → optional: commit + push       [blocking subprocess, no timeout]
```

**Observation (T2.1):** Every node in orange is called on every single tool invocation — including pure-read tools like `read_file`, `get_sections`, and `status_summary`. The fixed cost before any tool-specific work is at minimum one JSON parse + one network pull.

### T3 — Hotspot Analysis

#### T3.1 — `ensure_repo()` pulls on every call (server.py:145-165)

**Root cause:** The function conflates *"make sure the repo exists"* with *"make sure it's up to date."* Read-only tools don't need upstream freshness for most workflows; they need a consistent local snapshot.

**Evidence of waste:** A user calling `get_sections` → `read_file` → `get_section_content` on the same project pays three pulls back-to-back. Each pull is a full `git-upload-pack` negotiation over HTTPS.

**Fix (T3.1.1):**

```python
_PULL_TTL_SECONDS = 30  # default; override via OVERLEAF_PULL_TTL env
_last_pull: dict[str, float] = {}

def ensure_repo(project: ProjectConfig, *, force_pull: bool = False) -> Repo:
    repo_path = get_repo_path(project.project_id)
    git_url = f"https://git:{project.git_token}@git.overleaf.com/{project.project_id}"

    if not repo_path.exists():
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        return Repo.clone_from(git_url, repo_path)

    repo = Repo(repo_path)
    origin = repo.remotes.origin
    if origin.url != git_url:
        origin.set_url(git_url)

    now = time.monotonic()
    ttl = float(os.environ.get("OVERLEAF_PULL_TTL", _PULL_TTL_SECONDS))
    last = _last_pull.get(project.project_id, 0.0)
    if force_pull or (now - last) > ttl:
        try:
            origin.pull()
            _last_pull[project.project_id] = now
        except GitCommandError as e:
            # Surface, don't swallow
            raise StaleRepoWarning(str(e)) from e
    return repo
```

Write tools (`edit_file`, `rewrite_file`, `create_file`, `delete_file`, `update_section`, `sync_project`) pass `force_pull=True` to guarantee freshness before committing. Read tools fall through the TTL cache. `StaleRepoWarning` is a soft signal; the dispatcher catches it, serves the tool response, and appends `⚠ could not refresh: <reason>`.

**Expected gain:** Warm-hit latency drops from network-bound (100-500 ms) to fs-bound (1-10 ms) for 90 %+ of read sequences.

#### T3.2 — `load_config()` re-parses JSON per call (server.py:65)

**Fix (T3.2.1):** Memoize by `(path, mtime)`:

```python
_CONFIG_CACHE: tuple[float, Config] | None = None

def load_config() -> Config:
    global _CONFIG_CACHE
    config_path = Path(CONFIG_FILE)
    if config_path.exists():
        mtime = config_path.stat().st_mtime
        if _CONFIG_CACHE and _CONFIG_CACHE[0] == mtime:
            return _CONFIG_CACHE[1]
        cfg = _parse_config_file(config_path)
        _CONFIG_CACHE = (mtime, cfg)
        return cfg
    return _env_config()
```

**Gain:** Shaves a few ms × every tool call, and — more importantly — makes `get_project_config()` a pure function when the file hasn't moved, which is a precondition for T3.3.

#### T3.3 — Blocking Git on the event loop (server.py everywhere)

**Root cause:** `execute_tool()` is `async def`, but `ensure_repo()`, `origin.pull()`, `repo.git.commit()`, `origin.push()`, and even `Path.read_text()` all block the loop. Under concurrent tool calls — or even just a slow `git push` — the MCP stdio reader starves and the client sees tool timeouts.

**Fix (T3.3.1):** Introduce a single helper and funnel all Git/FS work through it:

```python
async def _run_blocking(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)
```

Then rewrite each tool branch to `await _run_blocking(ensure_repo, project, force_pull=...)` and `await _run_blocking(_do_commit_and_push, repo, message, push)`. This is mechanical and low-risk; the behavior is identical but the event loop stays responsive.

**Gain:** Concurrent calls from the same client no longer block each other. `sync_project` running in the background cannot stall a `list_files` reader.

#### T3.4 — No timeouts on Git network ops

**Root cause:** GitPython inherits Git's default (infinite) behavior. A mid-transfer TCP black-hole hangs the stdio server forever.

**Fix (T3.4.1):** Set environment on the git command. Either per-call via `repo.git.custom_environment(...)`, or globally:

```python
os.environ.setdefault("GIT_HTTP_LOW_SPEED_LIMIT", "1000")  # bytes/sec
os.environ.setdefault("GIT_HTTP_LOW_SPEED_TIME",  "30")    # seconds
```

For a hard ceiling, additionally wrap the `to_thread` call in `asyncio.wait_for(..., timeout=60)`; on timeout, cancel, and surface as a proper MCP error.

**Gain:** No infinite hangs. Worst case: tool returns a timeout error in ≤60 s.

#### T3.5 — Silent error swallowing (server.py:159, server.py:1328)

**Fix (T3.5.1):** Replace bare `except: pass` and bare `except Exception:` with scoped catches and explicit logging. The dispatcher at server.py:740-747 returning errors as `TextContent` success works, but we should **also** emit them via MCP's proper error path when appropriate. Short-term: prefix errors with `ERROR:` so clients can regex-detect; long-term: raise `McpError` from the SDK for programmatic errors.

### T4 — Tier-1 (next sprint)

1. **Split `server.py`** into `server.py` (transport + dispatcher), `tools/` (one file per tool or per category), `git_ops.py`, `latex.py`, `config.py`. The file is 1358 lines of flat `if name == "..."` branches — mechanically refactorable, reviewers will thank you.
2. **Structured tool responses.** MCP clients increasingly consume JSON-in-text. Return a `{"ok": bool, "data": ..., "warnings": [...]}` envelope serialized once; keep the human-readable top line. Preserves API while giving agents a parse target.
3. **Integration tests with a local git server.** Stand up a `pygit2`-backed bare repo in `tests/fixtures/`, point `OVERLEAF_GIT_URL` at `file://`, and cover the full read/write cycle. Current tests (7 schema checks) are necessary but not sufficient.
4. **Replace raw MCP SDK with FastMCP 3.x (jlowin's `fastmcp`).** Decorators remove the 500+ lines of schema boilerplate in `list_tools()`. Migration is one file at a time since transport is unchanged. This is the skill's recommended Python framework.
5. **Remove dead imports** (`httpx`, `base64` [used only in `create_project`], `io`, `shutil`, `subprocess`, `tempfile`, `zipfile`). `base64` stays; the rest are removals.

### T5 — Tier-2 (strategic)

- **Shared-repo concurrency lock.** A per-project `asyncio.Lock` around write operations prevents interleaved commit/push races on the same local clone.
- **Partial fetch.** For very large projects, a shallow `--depth=1` clone plus `--filter=blob:none` lazy blob fetch eliminates multi-GB checkouts.
- **Server-side diff.** `get_diff` currently calls Git and formats text. Adding `unified` and `stat` output modes lets Claude pick the right granularity.
- **Overleaf REST API for `create_project`.** Current implementation returns a `data:` URL for the user to paste into the browser — it doesn't actually create a project. Replacing with the documented `/api/project` endpoint closes this gap.
- **MCPB packaging.** `mcp-server-dev:build-mcpb` would ship the server bundled with its Python runtime, removing the `uv`/`pip` install step for end users. Appropriate given this is a local-machine-only server.

### T6 — Validation

- **T6.1** Do any changes break the tool contract? **No.** All new behavior is additive (TTL env var, optional `force_pull` kwarg) or internal (threading, memoization).
- **T6.2** Does the TTL cache create correctness hazards? Only if the user edits in Overleaf web UI and immediately reads via MCP within the TTL window. Mitigation: document the TTL, default 30 s (tight enough for interactive work, generous enough to amortize bursts), and keep `sync_project` as the explicit "refresh now" escape hatch.
- **T6.3** Confidence: **High (0.88)** on Tier-0; **Medium (0.70)** on Tier-1 (structured responses are API-visible even if backwards-compatible); **Medium (0.65)** on FastMCP migration (larger surface change).

---

## Risk Matrix

| Change | Perf gain | Correctness risk | Back-compat risk |
|---|---|---|---|
| TTL pull cache | ★★★★ | Low — user can `sync_project` | None (default behavior preserved for write ops) |
| `load_config` memoize | ★ | None (mtime invalidation) | None |
| `asyncio.to_thread` | ★★★ | None | None |
| Git timeouts | ★★★ | None | Hangs become errors (desired) |
| Surface pull errors | ★ | None | Tools now report `⚠ stale` |
| Module split | 0 | None | None |
| Structured JSON | ★ | None | Clients parsing free-text may regress |
| FastMCP migration | ★ | Medium | None (protocol-level identical) |

---

## Suggested Commit Sequence

1. `perf: memoize load_config by mtime`
2. `perf: add TTL-based pull cache to ensure_repo, propagate force_pull through writers`
3. `stability: wrap all Git/FS work in asyncio.to_thread`
4. `stability: set GIT_HTTP_LOW_SPEED_* timeouts + asyncio.wait_for hard ceiling`
5. `fix: surface GitCommandError on pull as a warning in tool response`
6. `chore: drop unused imports (io, shutil, subprocess, tempfile, zipfile, httpx)`
7. `refactor: split server.py into tools/, git_ops.py, config.py, latex.py`
8. `feat: return structured {ok, data, warnings} envelope alongside human text`

Each is independently shippable and reverts cleanly.

---

## Appendix — Files to Touch

- `src/overleaf_mcp/server.py` — all Tier-0 changes
- `tests/test_server.py` — add TTL-cache tests, add mocked `ensure_repo` tests
- `pyproject.toml` — remove unused deps if any drop out after import cleanup
- `docs/superpowers/plans/2025-04-15-upstream-feature-parity.md` — mark tasks complete
- `README.md` (if present) — document `OVERLEAF_PULL_TTL` env var
