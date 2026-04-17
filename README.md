# Overleaf MCP Server

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that provides full CRUD operations for [Overleaf](https://www.overleaf.com/) LaTeX projects. Enables AI assistants to read, edit, create, and delete files in your Overleaf projects.

## Features

### 15 Tools for Complete Project Management

| Category | Tool | Description |
|----------|------|-------------|
| **Create** | `create_project` | Create new Overleaf projects from LaTeX content or ZIP files |
| | `create_file` | Add new files to existing projects |
| **Read** | `list_projects` | View all configured projects |
| | `list_files` | List files with optional extension filter |
| | `read_file` | Read file contents (bounded by `max_bytes`, default 200k) |
| | `get_sections` | Parse LaTeX structure (chapters, sections, subsections) |
| | `get_section_content` | Get full content of a specific section |
| | `list_history` | View git commit history (filterable by path, date) |
| | `get_diff` | Compare versions — `unified` / `stat` / `name-only` modes |
| | `status_summary` | One-call project overview (files + last commit + section tree) |
| **Update** | `edit_file` | **Surgical edit** — replace specific text (old_string → new_string) |
| | `rewrite_file` | Replace entire file contents |
| | `update_section` | Update a specific LaTeX section by title |
| | `sync_project` | Force-pull latest changes from Overleaf (bypasses TTL cache) |
| **Delete** | `delete_file` | Remove files from projects |

For the full per-tool parameter reference, see [`docs/API.md`](docs/API.md).

### Key Capabilities

- **Git Integration**: Uses Overleaf's Git integration for reliable sync
- **Multi-Project Support**: Configure and switch between multiple projects
- **LaTeX-Aware**: Understands document structure for section-based operations
- **Auto-Push**: All write operations commit and push to Overleaf immediately
- **Local Caching**: Fast access with local repository cache
- **TTL-Cached Pulls**: Read-only tools reuse a fresh local snapshot for `OVERLEAF_PULL_TTL` seconds (default 30s) — an agent exploring a project pays ~1 network round-trip per burst, not per tool call
- **Reader-Writer Concurrency**: Multiple read tools against the same project run in parallel; writers (commit/push) take exclusive access. Per-project serialization, not global.
- **Non-Blocking**: All Git/subprocess work runs off the asyncio event loop, so a slow push never stalls the MCP stdio reader
- **Bounded Hangs**: Every Git op has a hard timeout ceiling (`OVERLEAF_GIT_TIMEOUT`, default 60s) — a wedged connection can no longer freeze the server indefinitely
- **Transient-Failure Retry**: Pull failures that look transient (connection reset, HTTP 5xx, DNS blip) get one transparent retry with a short random back-off. Auth/ref errors fail fast (no wasted round-trip).
- **Visible Staleness**: If a refresh attempt fails but a local snapshot is available, the tool response appends a `⚠ could not refresh from Overleaf: ...` warning instead of silently serving stale data
- **Opt-In Structured Envelope**: Set `OVERLEAF_STRUCTURED=1` to append `<mcp-envelope>{"ok":bool,"warnings":[...]}</mcp-envelope>` to every tool response — gives agentic clients a reliable parse target. Off by default; plain-text clients unaffected.
- **Opt-In Timing Logs**: Set `OVERLEAF_TIMING=1` to emit a per-tool `acquire_project project=... mode=... elapsed_ms=... stale=...` INFO log line on every call — useful for latency regressions and tuning the TTL.

---

## Installation

### Prerequisites

- Python 3.10+
- Git
- Overleaf account with Git integration (requires paid plan)

### Install with pip

```bash
# Clone the repository
git clone https://github.com/imewei/overleaf-mcp.git
cd overleaf-mcp

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install
pip install -e .
```

### Install with uv (faster)

```bash
git clone https://github.com/imewei/overleaf-mcp.git
cd overleaf-mcp

uv venv
source .venv/bin/activate
uv pip install -e .
```

### Install as an MCPB bundle (Claude Desktop)

For a zero-toolchain install — users don't need to `pip install`, just
drag one file onto Claude Desktop:

```bash
# From a cloned repo, build the bundle
./mcpb/build-mcpb.sh
# → dist/overleaf-mcp-1.1.0.mcpb

# User: drag dist/overleaf-mcp-1.1.0.mcpb onto Claude Desktop
```

The bundle embeds Python dependencies (`mcp`, `fastmcp`, `gitpython`,
`pydantic`) but not the Python interpreter itself or `git` — both must
still be on the user's PATH. See `manifest.json` for the install-time
config fields (cache dir, Git token, etc.) that Claude Desktop surfaces
as a native form. Claude Desktop stores the Git token in the OS keychain
(the field is marked `sensitive: true` in the manifest).

---

## Configuration

### Step 1: Get Your Overleaf Credentials

1. **Open your Overleaf project** in the browser

2. **Get Project ID** from the URL:
   ```
   https://www.overleaf.com/project/YOUR_PROJECT_ID
                                    ^^^^^^^^^^^^^^^^
   ```

3. **Get Git Token**:
   - Click **Menu** (top-left)
   - Click **Git** under "Sync"
   - Click **Generate token** (if not already generated)
   - Copy the URL: `https://git:YOUR_TOKEN@git.overleaf.com/...`
   - Extract the token (the part between `git:` and `@`)

### Step 2: Create Configuration File

Create `overleaf_config.json` in the project directory:

```json
{
  "projects": {
    "my-thesis": {
      "name": "My PhD Thesis",
      "projectId": "abc123def456",
      "gitToken": "olp_xxxxxxxxxxxxxxxxxxxx"
    },
    "paper": {
      "name": "Research Paper",
      "projectId": "xyz789ghi012",
      "gitToken": "olp_yyyyyyyyyyyyyyyyyyyy"
    }
  },
  "defaultProject": "my-thesis"
}
```

### Alternative: Environment Variables

For single-project setups:

```bash
export OVERLEAF_PROJECT_ID="your_project_id"
export OVERLEAF_GIT_TOKEN="your_git_token"
```

---

## Client Configuration

### Claude Desktop

**Config file location:**
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

**Configuration:**

```json
{
  "mcpServers": {
    "overleaf": {
      "command": "/path/to/overleaf-mcp/.venv/bin/python",
      "args": ["-m", "overleaf_mcp.server"],
      "cwd": "/path/to/overleaf-mcp",
      "env": {
        "OVERLEAF_CONFIG_FILE": "/path/to/overleaf-mcp/overleaf_config.json",
        "OVERLEAF_TEMP_DIR": "/path/to/overleaf-mcp/overleaf_cache"
      }
    }
  }
}
```

**Example (macOS):**

```json
{
  "mcpServers": {
    "overleaf": {
      "command": "/Users/username/dev/overleaf-mcp/.venv/bin/python",
      "args": ["-m", "overleaf_mcp.server"],
      "cwd": "/Users/username/dev/overleaf-mcp",
      "env": {
        "OVERLEAF_CONFIG_FILE": "/Users/username/dev/overleaf-mcp/overleaf_config.json",
        "OVERLEAF_TEMP_DIR": "/Users/username/dev/overleaf-mcp/overleaf_cache"
      }
    }
  }
}
```

After saving, **restart Claude Desktop** (Cmd+Q / Ctrl+Q, then reopen).

---

### Claude Code (CLI)

Add to your Claude Code MCP settings (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "overleaf": {
      "command": "/path/to/overleaf-mcp/.venv/bin/python",
      "args": ["-m", "overleaf_mcp.server"],
      "cwd": "/path/to/overleaf-mcp",
      "env": {
        "OVERLEAF_CONFIG_FILE": "/path/to/overleaf-mcp/overleaf_config.json",
        "OVERLEAF_TEMP_DIR": "/path/to/overleaf-mcp/overleaf_cache"
      }
    }
  }
}
```

Or add per-project in `.claude/settings.json` in your project directory.

---

### VS Code (with Claude Extension)

Add to your VS Code settings (`settings.json`):

```json
{
  "claude.mcpServers": {
    "overleaf": {
      "command": "/path/to/overleaf-mcp/.venv/bin/python",
      "args": ["-m", "overleaf_mcp.server"],
      "cwd": "/path/to/overleaf-mcp",
      "env": {
        "OVERLEAF_CONFIG_FILE": "/path/to/overleaf-mcp/overleaf_config.json",
        "OVERLEAF_TEMP_DIR": "/path/to/overleaf-mcp/overleaf_cache"
      }
    }
  }
}
```

**Or** add to workspace settings (`.vscode/settings.json`) for project-specific config.

---

## Usage Examples

Once configured, you can ask the AI assistant:

### Reading Files
```
"List all .tex files in my thesis"
"Read the content of main.tex"
"What sections are in chapter1.tex?"
```

### Editing Content
```
"Edit main.tex and replace 'teh' with 'the'"
"Rewrite the abstract.tex file with this new content: ..."
"Update the 'Introduction' section with this new content: ..."
```

### Creating Files
```
"Create a new file called appendix.tex with a section for supplementary materials"
"Add a new bibliography file references.bib"
```

### Project Management
```
"Show me the last 10 commits"
"What changed since yesterday?"
"Sync the project to get latest changes"
```

### Section-Based Operations
```
"Get the content of the 'Methods' section"
"Update the 'Results' section with these findings: ..."
"What subsections are in chapter 2?"
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OVERLEAF_CONFIG_FILE` | `overleaf_config.json` | Path to configuration file |
| `OVERLEAF_TEMP_DIR` | `./overleaf_cache` | Local cache directory for git repos |
| `OVERLEAF_PROJECT_ID` | - | Default project ID (single-project mode) |
| `OVERLEAF_GIT_TOKEN` | - | Default git token (single-project mode) |
| `OVERLEAF_GIT_AUTHOR_NAME` | `Overleaf MCP` | Git commit author name |
| `OVERLEAF_GIT_AUTHOR_EMAIL` | `mcp@overleaf.local` | Git commit author email |
| `OVERLEAF_PULL_TTL` | `30` | Seconds within which a successful pull is considered fresh enough to skip on subsequent read-only tools. Write tools (`edit_file`, `rewrite_file`, `create_file`, `update_section`, `delete_file`) and `sync_project` always bypass this cache. Set to `0` to pull on every tool call (previous behavior). |
| `OVERLEAF_GIT_TIMEOUT` | `60` | Hard upper bound (seconds) on any blocking Git operation. Protects against an unresponsive Overleaf endpoint hanging the server. |
| `OVERLEAF_GIT_URL` | `https://git.overleaf.com` | Base URL for the Overleaf Git endpoint. Override for self-hosted deployments or test fixtures (point at a `file://` bare repo). |
| `OVERLEAF_SHALLOW_CLONE` | `0` | Set to `1` to use shallow clones (`--depth=N`) for new projects. Dramatically reduces cold-start time and disk usage for multi-GB projects, at the cost of limiting `list_history` to the shallow depth. |
| `OVERLEAF_SHALLOW_DEPTH` | `1` | Depth for shallow clones. Ignored when `OVERLEAF_SHALLOW_CLONE=0`. |
| `OVERLEAF_STRUCTURED` | `0` | Set to `1` to append `<mcp-envelope>{"ok":bool,"warnings":[...]}</mcp-envelope>` to every tool response. Useful for agentic clients that want a reliable parse target; off by default so plain-text clients are unaffected. |
| `OVERLEAF_TIMING` | `0` | Set to `1` to emit one structured `acquire_project project=... mode=... elapsed_ms=... stale=...` INFO log line per tool call. Zero cost when off (single env lookup). Useful for latency debugging and tuning `OVERLEAF_PULL_TTL`. |
| `GIT_HTTP_LOW_SPEED_LIMIT` | `1000` | Bytes/sec floor — Git aborts if throughput drops below this. |
| `GIT_HTTP_LOW_SPEED_TIME` | `30` | Seconds that throughput must stay below the limit before aborting. |

### Performance Notes

As of v1.1, the server caches successful `git pull` operations for
`OVERLEAF_PULL_TTL` seconds (default 30). This means an agent exploring a
project with multiple read tools (`list_files` → `read_file` → `get_sections` → ...)
pays at most **one** network round-trip across the burst. Write tools always
force a fresh pull to avoid committing on a stale base. Call `sync_project`
any time you need an explicit, bypass-the-cache refresh.

If an attempted pull fails but a local snapshot is available, the tool
returns its response against the cached copy and appends a
`⚠ could not refresh from Overleaf: ...` warning line. This makes silent
staleness impossible — callers always know whether data is live or cached.

---

## How It Works

```
┌─────────────────┐     MCP Protocol     ┌─────────────────┐
│  AI Assistant   │◄───────────────────►│  Overleaf MCP   │
│ (Claude, etc.)  │                      │     Server      │
└─────────────────┘                      └────────┬────────┘
                                                  │
                                                  │ Git (HTTPS)
                                                  ▼
                                         ┌─────────────────┐
                                         │    Overleaf     │
                                         │   Git Server    │
                                         └─────────────────┘
```

1. **Clone/Pull**: Server clones or pulls the latest from Overleaf's Git endpoint
2. **Local Operations**: Read/write operations happen on local cache
3. **Commit/Push**: Changes are committed and pushed back to Overleaf
4. **Real-time Sync**: Overleaf reflects changes immediately in the web editor

### Module Layout

```
src/overleaf_mcp/
├── server.py     transport layer (FastMCP 3.x) — tiny by design
├── tools.py      15 async tool implementations + dispatcher shim
├── git_ops.py    clone/pull, RW lock, TTL cache, timeouts, retry
├── latex.py      pure LaTeX section parser (re-usable, no I/O)
├── config.py     pydantic models + config-file / env-var loading
└── __init__.py   version
```

For a deeper walk-through of the refresh/lock/envelope pipeline, see
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Security Notes

- **Tokens are sensitive**: Git tokens provide full read/write access
- **Never commit secrets**: `overleaf_config.json` is gitignored by default
- **Use environment variables**: For CI/CD or shared environments
- **Token rotation**: Regenerate tokens periodically in Overleaf settings

---

## Troubleshooting

### "No projects configured"
- Ensure `overleaf_config.json` exists and has valid JSON
- Check `OVERLEAF_CONFIG_FILE` points to the correct path

### "Permission denied" or "Read-only filesystem"
- Set `OVERLEAF_TEMP_DIR` to an absolute writable path
- Ensure the cache directory exists and is writable

### "Authentication failed"
- Verify your git token is correct
- Check if the token has expired (regenerate in Overleaf)
- Ensure you have Git integration enabled (requires paid Overleaf plan)

### "Server not appearing in Claude"
- Restart Claude Desktop completely (Cmd+Q / Ctrl+Q)
- Check the config JSON is valid (no trailing commas)
- Verify Python path is correct (use absolute path to venv)

### "⚠ could not refresh from Overleaf" in a tool response
- This is a **soft warning**, not a failure. The tool served its response
  against the last-good local snapshot instead of aborting.
- One transparent retry already ran — the underlying error is persistent
  across a short delay (network outage, auth revoked, server 5xx).
- Call `sync_project` explicitly to see the hard error and diagnose.

### Latency debugging
- Set `OVERLEAF_TIMING=1` to get per-tool `acquire_project project=... mode=... elapsed_ms=... stale=...` INFO lines.
- If every tool shows >1s elapsed, check `OVERLEAF_PULL_TTL` — the default 30s means bursts of read calls pay one pull; setting it to `0` forces a pull per call.
- `mode=read` calls should run in parallel (reader-writer lock). `mode=write` calls serialize — one push at a time, by design.

---

## Documentation

| Doc | Purpose |
|-----|---------|
| [`README.md`](README.md) | Install, configure, usage (this file) |
| [`docs/API.md`](docs/API.md) | Per-tool parameter/return reference |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Module layers, request lifecycle, locking, caching |
| [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) | Dev setup, testing, lint/type, release flow |
| [`CHANGELOG.md`](CHANGELOG.md) | Version-by-version change log |

---

## Contributing

Contributions are welcome! See [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)
for the dev setup, test commands, and module boundaries. Please open an
issue before starting non-trivial work.

---

## License

MIT License - see [LICENSE](LICENSE) for details.

---

## Acknowledgments

- [Overleaf](https://www.overleaf.com/) for the Git integration
- [Model Context Protocol](https://modelcontextprotocol.io/) for the MCP specification
- [Anthropic](https://www.anthropic.com/) for Claude and the MCP SDK
