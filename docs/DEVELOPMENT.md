# Development Guide

Everything you need to hack on this server locally.

## Prerequisites

- **Python 3.10+** (tested on 3.10, 3.11, 3.12, 3.13)
- **Git** (GitPython shells out; we do not bundle a binary)
- **`uv`** (recommended) — resolves and installs against `pyproject.toml`
  faster than pip and produces a deterministic environment via `uv sync`.
  `pip install -e .` also works for the happy path.

> Note: `uv.lock` is currently `.gitignore`d (see `.gitignore` line 52).
> CI resolves dependencies fresh from `pyproject.toml` on each run. If
> you need byte-identical environments across machines, either check in
> `uv.lock` locally or pin exact versions in `pyproject.toml`.

## Setup

```bash
git clone https://github.com/imewei/overleaf-mcp.git
cd overleaf-mcp

# Install runtime + dev deps in a local .venv
uv sync --extra dev

# Activate (or prefix every command with `uv run`)
source .venv/bin/activate
```

## Commands

Tool config lives in `pyproject.toml`; there's no separate `setup.cfg`
or `.flake8`.

| Command | Purpose |
|---------|---------|
| `uv run pytest` | Run the test suite (128 tests, ~seconds) |
| `uv run pytest --cov=src/overleaf_mcp --cov-report=term-missing` | Coverage; goal ≥99% on `src/overleaf_mcp` |
| `uv run ruff check .` | Lint (`E`, `F`, `UP`, `B`, `SIM`) |
| `uv run ruff format .` | Format |
| `uv run mypy src/overleaf_mcp` | Type check (strict-clean on own code) |
| `uv run mypy --strict src/overleaf_mcp` | Strict mode (also clean) |
| `uv run bandit -r src/overleaf_mcp` | Security lint |
| `make clean` | Remove caches / build artifacts (safe) |
| `make clean-all` | `make clean` + wipe `.venv` and `overleaf_cache/` |

## Running the server locally

```bash
# Via the console script (set up by pyproject.toml)
uv run overleaf-mcp

# Or as a module
uv run python -m overleaf_mcp.server
```

The server talks **stdio** — it expects an MCP client on the other end.
For manual smoke tests, configure Claude Desktop / Claude Code to point
at your dev checkout (see `README.md` § Client Configuration).

## Module Boundaries

The layering is strict and worth preserving:

```
server.py  →  tools.py  →  git_ops.py  →  config.py
                 ↓
              latex.py
```

- `server.py` imports **only** `tools`. No FastMCP knowledge anywhere else.
- `tools.py` imports `config`, `git_ops`, `latex`. Exports `TOOLS` + `execute_tool`.
- `git_ops.py` imports `config`. Knows nothing about MCP framing.
- `latex.py` and `config.py` import nothing from the rest of the package.

When in doubt, **down, not sideways**. If `latex.py` starts needing
`git_ops`, the abstraction has broken and the fix is almost never "add
the import".

## Adding a New Tool

1. Write an `async def` in `tools.py` with `Annotated[..., Field(description=...)]`
   parameters and a clear docstring.
2. Wrap the body in `async with acquire_project(project, mode="read"|"write", force_pull=...)`.
3. Return `ctx.wrap(response_text)` — never a bare string. `wrap()` is what
   attaches the stale-snapshot warning and the optional structured envelope.
4. Add `"tool_name": tool_fn,` to the `TOOLS` dict at the bottom of the file.
5. Add tests in `tests/test_dispatcher.py` (end-to-end call) and in the
   category-specific test file if there's cross-cutting behavior.
6. Update `docs/API.md`.

No registration code changes needed — `server.py` iterates `TOOLS`.

## Testing Style

- **`pytest-asyncio` strict mode** — every async test must be marked
  `@pytest.mark.asyncio`. Accidentally omitted marks fail loudly.
- **Fixtures use a local `file://` bare repo**, not the real Overleaf
  endpoint. See `conftest.py` in the tests dir (if present) or the
  `tmp_bare_repo` setups inside each test file.
- **Patching pattern**: monkey-patch the module-level name where it's
  *used*, not where it's defined. E.g. `tools._run_blocking`, not
  `git_ops._run_blocking`, when you're testing a tool branch.
- **Coverage target**: 99%+ on `src/overleaf_mcp`. The remaining 1%
  is defensive paths marked `# pragma: no cover` in `pyproject.toml`.

## Release Flow

1. Bump version in **three places**: `pyproject.toml`, `manifest.json`,
   `src/overleaf_mcp/__init__.py`. Yes, three — this is a known papercut
   (see issue tracker).
2. Add a `CHANGELOG.md` entry under the new version heading.
3. Run full validation:
   ```bash
   uv run ruff check . && uv run mypy --strict src/overleaf_mcp && uv run pytest
   ```
4. Tag: `git tag -a v<version> -m "Release <version>"` and push tags.
5. Build the MCPB bundle: `./mcpb/build-mcpb.sh`. Verify the output
   (`dist/overleaf-mcp-<version>.mcpb`) loads in Claude Desktop.

## MCPB Bundle Notes

`mcpb/build-mcpb.sh` produces a single-file archive:

```
overleaf-mcp-<version>.mcpb
├── manifest.json           # identity + user_config schema
└── server/
    ├── bootstrap.py        # prepends vendor/ to sys.path, calls main()
    ├── vendor/             # pip install --target of runtime deps
    └── overleaf_mcp/       # source tree (copied, not symlinked)
```

What's **not** in the bundle:
- The Python interpreter (host supplies it)
- The `git` binary (host supplies it — cross-platform bundling is
  intractable; we depend on the user having git on PATH)
- Dev dependencies (only runtime deps are vendored)

What's safe to strip from `vendor/`: `__pycache__/`, `tests/`, `docs/`,
`examples/`, `locale/`, `*.pyi`. What's **not** safe to strip:
`*.dist-info` — pydantic/fastmcp/mcp all call `importlib.metadata.version(pkg)`
at runtime. Regression happened before; see `build-mcpb.sh` comments.

## Debugging Tips

| Symptom | First thing to check |
|---------|---------------------|
| Tool hangs, no response | `OVERLEAF_GIT_TIMEOUT` / `GIT_HTTP_LOW_SPEED_TIME` env |
| Every tool call is slow | Set `OVERLEAF_TIMING=1`; verify `OVERLEAF_PULL_TTL` isn't 0 |
| Write tool "rejected" | Local branch behind upstream — run `sync_project` |
| Sections not detected | `latex.py` regex matches starred variants but requires `{...}` (not bracketless). Check the source. |
| `import overleaf_mcp` fails in bundle | `*.dist-info` got stripped; rebuild with `build-mcpb.sh` unmodified |
| Concurrency regression | Grep for `_rwlock_for`; writer-priority must be preserved |

## Code Style

- **`from __future__ import annotations`** at the top of every module —
  allows PEP 604 `str | None` on 3.10.
- **Docstrings explain "why", not "what"**. If the code is obvious, no
  docstring is needed. If it's subtle, explain the subtlety.
- **`Annotated[T, Field(description=...)]`** over raw types on tool
  params — the `description` becomes the MCP schema's parameter doc
  that clients surface to users.
- **No `# type: ignore`** without a comment explaining why. Mypy strict
  passes; keep it that way.
