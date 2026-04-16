# Overleaf MCP Server

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that provides full CRUD operations for [Overleaf](https://www.overleaf.com/) LaTeX projects. Enables AI assistants to read, edit, create, and delete files in your Overleaf projects.

## Features

### 14 Tools for Complete Project Management

| Category | Tool | Description |
|----------|------|-------------|
| **Create** | `create_project` | Create new Overleaf projects from LaTeX content or ZIP files |
| | `create_file` | Add new files to existing projects |
| **Read** | `list_projects` | View all configured projects |
| | `list_files` | List files with optional extension filter |
| | `read_file` | Read file contents |
| | `get_sections` | Parse LaTeX structure (chapters, sections, subsections) |
| | `get_section_content` | Get full content of a specific section |
| | `list_history` | View git commit history |
| | `get_diff` | Compare changes between versions |
| **Update** | `edit_file` | **Surgical edit** - replace specific text (old_string → new_string) |
| | `rewrite_file` | Replace entire file contents |
| | `update_section` | Update a specific LaTeX section by title |
| | `sync_project` | Pull latest changes from Overleaf |
| **Delete** | `delete_file` | Remove files from projects |

### Key Capabilities

- **Git Integration**: Uses Overleaf's Git integration for reliable sync
- **Multi-Project Support**: Configure and switch between multiple projects
- **LaTeX-Aware**: Understands document structure for section-based operations
- **Auto-Push**: All write operations commit and push to Overleaf immediately
- **Local Caching**: Fast access with local repository cache
- **TTL-Cached Pulls**: Read-only tools reuse a fresh local snapshot for `OVERLEAF_PULL_TTL` seconds (default 30s) — an agent exploring a project pays ~1 network round-trip per burst, not per tool call
- **Non-Blocking**: All Git/subprocess work runs off the asyncio event loop, so a slow push never stalls the MCP stdio reader
- **Bounded Hangs**: Every Git op has a hard timeout ceiling (`OVERLEAF_GIT_TIMEOUT`, default 60s) — a wedged connection can no longer freeze the server indefinitely
- **Visible Staleness**: If a refresh attempt fails but a local snapshot is available, the tool response appends a `⚠ could not refresh from Overleaf: ...` warning instead of silently serving stale data

---

## Installation

### Prerequisites

- Python 3.10+
- Git
- Overleaf account with Git integration (requires paid plan)

### Install with pip

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/overleaf-mcp.git
cd overleaf-mcp

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install
pip install -e .
```

### Install with uv (faster)

```bash
git clone https://github.com/YOUR_USERNAME/overleaf-mcp.git
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
as a native form.

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

---

## Contributing

Contributions are welcome! Please open an issue or submit a pull request.

---

## License

MIT License - see [LICENSE](LICENSE) for details.

---

## Acknowledgments

- [Overleaf](https://www.overleaf.com/) for the Git integration
- [Model Context Protocol](https://modelcontextprotocol.io/) for the MCP specification
- [Anthropic](https://www.anthropic.com/) for Claude and the MCP SDK
