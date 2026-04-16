# Changelog

All notable changes to this project. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning
follows [SemVer](https://semver.org/).

## [1.1.0] — 2026-04-16

The "agent-grade hardening" release. Focus: concurrency, latency,
observability, and mypy/coverage hygiene.

### Added

- **`status_summary` tool** — one-call project orientation (file inventory
  + last commit + main-doc section tree). Use this as the first tool on
  an unfamiliar project instead of three separate calls.
- **Reader-writer lock per project** — concurrent read tools (`read_file`,
  `list_files`, `get_sections`, `get_section_content`, `list_history`,
  `get_diff`, `status_summary`) now run in parallel against the same
  project. Writers keep exclusive access. Writer-priority prevents
  reader starvation under sustained read load.
- **Transient-failure retry** — one transparent retry with uniform random
  back-off on pull errors matching transient patterns (connection reset,
  HTTP 5xx, DNS hiccup, SSL handshake). Auth / ref errors skip the retry.
- **`max_bytes` guardrail on `read_file`** (default 200 000, clamped
  1 000–2 000 000). Truncation marked with `[file truncated at N bytes]`.
- **`OVERLEAF_TIMING=1`** opt-in observability — emits a structured
  `acquire_project project=... mode=... elapsed_ms=... stale=...` INFO
  log line on every tool call. Zero cost when off.
- **`OVERLEAF_STRUCTURED=1`** opt-in response envelope — appends
  `<mcp-envelope>{"ok":bool,"warnings":[...]}</mcp-envelope>` for agentic
  clients that want a parse target. Plain-text clients unaffected.
- **Shallow-clone support** — `OVERLEAF_SHALLOW_CLONE=1` + `OVERLEAF_SHALLOW_DEPTH`
  for multi-GB projects where cold-start matters more than history depth.
- **`get_diff` modes** — `unified` / `stat` / `name-only`; new `paths`
  (multi-file filter), `context_lines`, `max_output_chars` params.
- **`list_history` filters** — `since` / `until` Git date specs; hard cap
  raised to 200.
- **Dry-run + push toggles on all write tools** (`create_file`, `edit_file`,
  `rewrite_file`, `update_section`, `delete_file`).
- **Inline credential overrides on every project-aware tool** — pass
  `git_token` + `project_id` to bypass the config file per-call.
- **MCPB bundle** — `manifest.json` + `mcpb/build-mcpb.sh` + `mcpb/bootstrap.py`.
  Drag-and-drop install into Claude Desktop; embeds runtime Python deps
  (not interpreter, not git).
- **`docs/API.md`**, **`docs/ARCHITECTURE.md`**, **`docs/DEVELOPMENT.md`**,
  **`CHANGELOG.md`** — full doc set.
- **Makefile** — `make clean` (safe) and `make clean-all` (wipes `.venv`
  and `overleaf_cache/`).

### Changed

- **Module split** — monolithic `server.py` refactored into `server.py`
  (transport), `tools.py` (15 tool impls), `git_ops.py` (locking / TTL /
  async), `latex.py` (parser), `config.py` (pydantic + loader). FastMCP 3.x
  replaces hand-written JSON schemas (~500 LOC removed).
- **TTL-cached pulls** — read tools reuse a successful pull for
  `OVERLEAF_PULL_TTL` seconds (default 30). An agent exploring a project
  with multiple reads pays one round-trip per burst, not per tool call.
  Write tools always force-pull.
- **Visible staleness** — failed refresh with an existing local snapshot
  attaches a `⚠ could not refresh from Overleaf: ...` warning to the
  response instead of silently serving stale data.
- **Hard timeouts** — `OVERLEAF_GIT_TIMEOUT` (default 60s) ceiling on
  every blocking Git op via `asyncio.wait_for`. `GIT_HTTP_LOW_SPEED_LIMIT` /
  `..._TIME` act as a subprocess-level backstop.
- **Non-blocking** — all Git/subprocess work runs via `asyncio.to_thread`,
  so a slow push can't stall the MCP stdio reader.
- **`_LAST_PULL` cache key** extended from `project_id` to `(project_id,
  token_hash)` — forward-compat for multi-client HTTP transport where
  different clients hold different tokens.
- **Git user stamped at clone time**, not per-push — saves a redundant
  config-writer fsync on every commit.
- **`__version__`** bumped to `1.1.0`.

### Fixed

- **mypy `--strict` clean** (0 errors, down from 39). Two real bugs
  surfaced and fixed in the process — `GitPython`'s `Commit.message` is
  `str | bytes` and an f-string was producing literal `b'...'` text
  on bytes-returning commits.
- **`create_project` docstring** now documents the `snip_uri`-URL contract
  explicitly (Overleaf has no published REST endpoint for project
  creation; the human click is by design, not a limitation to work around).
- **`config_git_user`** no longer swallows programming errors in a bare
  `except Exception` — catches only `ConfigParser.NoOptionError` /
  `NoSectionError` as originally intended.
- **MCPB bundle** — stops stripping `*.dist-info` directories (required
  by `importlib.metadata.version()` calls in pydantic/fastmcp/mcp at
  runtime).

### Testing

- 128 tests, ~99% coverage on `src/overleaf_mcp` (up from 92 tests, 90%).
- New coverage: transport layer, stale-snapshot auth failure, rare
  pydantic paths, RW-lock semantics under contention.

---

## [1.0.0] — Initial

Initial release. 14 tools, monolithic `server.py`, raw MCP SDK,
synchronous Git, no per-project locking. Superseded by 1.1.0.
