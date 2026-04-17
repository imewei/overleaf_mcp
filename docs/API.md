# Overleaf MCP — Tool Reference

Complete per-tool reference. The server exposes **15 MCP tools**, grouped
into CRUD categories. Schemas are inferred from `Annotated[..., Field(...)]`
signatures in `src/overleaf_mcp/tools.py` via FastMCP 3.x.

## Conventions

All tools share these common kwargs (each is optional, defaults noted):

| Param | Type | Description |
|-------|------|-------------|
| `project_name` | `str \| None` | Project key from `overleaf_config.json`. Uses `defaultProject` if omitted. |
| `git_token` | `str \| None` | Inline git token (bypasses config file). Must be passed **with** `project_id`. |
| `project_id` | `str \| None` | Inline project ID (bypasses config file). Must be passed **with** `git_token`. |
| `commit_message` | `str \| None` | Git commit message for write tools. Defaults to an action-specific string. |
| `dry_run` | `bool` | If `true`, report what would happen without writing or pushing (default `false`). |
| `push` | `bool` | Push after commit (default `true`). Set `false` for local-only edits. |

### Refresh semantics

| Category | `force_pull` | `mode` | TTL-cache respected? |
|----------|---|---|---|
| **Read** (`list_files`, `read_file`, `get_sections`, `get_section_content`, `list_history`, `get_diff`, `status_summary`) | `false` | `read` | Yes — may serve from local snapshot if < `OVERLEAF_PULL_TTL` old |
| **Write** (`create_file`, `edit_file`, `rewrite_file`, `update_section`, `delete_file`) | `true` | `write` | No — always pulls before commit |
| **Sync** (`sync_project`) | `true` | exclusive | No — always pulls, reports hard errors |
| **List/Create-project** (`list_projects`, `create_project`) | n/a | n/a | No Git touched |

Read tools under the same project run **concurrently** (shared lock).
Write tools take the writer lock — one writer at a time per project,
across the whole process.

### Response envelope

By default, every tool returns a plain-text string. Set
`OVERLEAF_STRUCTURED=1` to append a parse-friendly tail:

```
<tool-response-body>

<warnings, one per line, if any>

<mcp-envelope>{"ok": true, "warnings": []}</mcp-envelope>
```

`ok` is `false` when the body starts with `Error:` or any warnings were
attached.

---

## Create

### `create_project`

Create a new Overleaf project from LaTeX content or a base64-zipped archive.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `content` | `str` | — | LaTeX source or base64-encoded ZIP |
| `project_name` | `str \| None` | `None` | Optional display name |
| `engine` | `str` | `"pdflatex"` | TeX engine hint |
| `is_zip` | `bool` | `false` | If `true`, `content` is a base64 ZIP |

**Returns:** A `overleaf.com/docs?snip_uri=...` URL the user clicks to
finish project creation.

**Why a URL, not a server-to-server call?** Overleaf's only documented
public endpoint for creating projects is the `snip_uri` form. Git tokens
authenticate Git transport only; there is no published REST route for
"create project". Session-cookie approaches used by some third-party
libraries are ToS-risky. See the extended rationale in the docstring.

---

### `create_file`

Add a new file to an existing project. Commits + pushes.

| Param | Type | Required | Description |
|-------|------|---|-------------|
| `file_path` | `str` | ✓ | Path relative to project root, e.g. `chapters/intro.tex` |
| `content` | `str` | ✓ | File content |
| `commit_message` | `str \| None` | | Default: `Add <file_path>` |

**Errors:** Returns `Error: File 'X' already exists` if the path is taken
— use `edit_file` / `rewrite_file` instead.

---

## Read

### `list_projects`

List projects configured in `overleaf_config.json`. No Git touched.

Returns a human-readable list with the default project marked.

---

### `list_files`

List all non-dotfile paths in the project, sorted.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `extension` | `str` | `""` | Exact suffix match, case-sensitive, **including the leading dot** (e.g. `.tex`). Empty = all. |

---

### `read_file`

Read a text file. Guarded against runaway context usage.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `file_path` | `str` | — | Path relative to project root |
| `max_bytes` | `int` | `200000` | Truncate output past this length. Clamped to `[1000, 2000000]`. |

When truncated, appends `[file truncated at N bytes]`. For large LaTeX
documents, prefer `get_section_content` — it's smaller and avoids the
ceiling.

---

### `get_sections`

Parse a LaTeX file and list its `\part` / `\chapter` / `\section` /
`\subsection` / `\subsubsection` / `\paragraph` / `\subparagraph` entries
(starred variants supported).

Returns: type, title, and a 100-char preview of each section's body.

---

### `get_section_content`

Return the full body of a section matched by its title (case-insensitive).

Errors list the available section titles, so a typo produces an
actionable response rather than a dead-end.

---

### `list_history`

Show git commit history.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | `int` | `20` | Max commits to return. Hard-capped at 200. |
| `file_path` | `str \| None` | `None` | Restrict to commits touching this path |
| `since` | `str \| None` | `None` | Git date spec (e.g. `2025-01-01`, `2.weeks`) |
| `until` | `str \| None` | `None` | Git date spec |

**Caveat:** With `OVERLEAF_SHALLOW_CLONE=1`, history is capped at
`OVERLEAF_SHALLOW_DEPTH` — commits older than that are simply not in the
local clone.

---

### `get_diff`

Compare two refs / working tree.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `from_ref` | `str` | `"HEAD"` | Starting ref (commit / branch / `HEAD~n`) |
| `to_ref` | `str \| None` | `None` | Ending ref (default: working tree) |
| `file_path` | `str \| None` | `None` | Single-file filter |
| `paths` | `list[str] \| None` | `None` | Multi-file filter |
| `mode` | `str` | `"unified"` | `unified` / `stat` / `name-only` |
| `context_lines` | `int` | `3` | Context lines for `unified` (clamped 0–10) |
| `max_output_chars` | `int` | `120000` | Truncate past this length (clamped 2000–500000) |

`stat` and `name-only` exist to keep agent token usage bounded: if the
agent just needs to know *which* files changed (not the content), these
are dramatically smaller than `unified`.

---

### `status_summary`

One-call project orientation. Equivalent to `list_files` + `list_history(limit=1)`
+ `get_sections` on the detected main `.tex`, but in a single pull-cache
hit. **Use this as the first tool on an unfamiliar project.**

Returns: project name, current branch, total / `.tex` file counts,
latest commit summary, and — if a file containing `\documentclass` or
`\begin{document}` is found — its section tree.

---

## Update

### `edit_file` (surgical edit)

Replace an exact string match. Old text must occur **exactly once**.

| Param | Type | Required | Description |
|-------|------|---|-------------|
| `file_path` | `str` | ✓ | |
| `old_string` | `str` | ✓ | Exact text to find, whitespace-sensitive |
| `new_string` | `str` | ✓ | Replacement |

**Error policy:** If `old_string` appears zero or >1 times, no changes
are written and the response describes the mismatch (zero) or count
(many). Safer than a regex-based replacer for LLM-generated edits.

---

### `rewrite_file`

Replace the entire file. Commits + pushes. Prefer `edit_file` for small
changes — smaller diffs, easier human review.

---

### `update_section`

Replace the body of a LaTeX section by its title. The header itself is
preserved; only the content between this section and the next is rewritten.

---

### `sync_project`

Explicit force-pull. Reports hard errors (stale-snapshot fallback is
disabled for this tool — by design, this is the diagnostic escape hatch).

| Response | Meaning |
|---|---|
| `Cloned project '...'` | First pull; repo now exists |
| `Synced project '...'` | Pull succeeded |
| `Warning: Local changes exist.` | Working tree is dirty; refusing to pull |
| `Error syncing: ...` | Upstream refused; shown verbatim (this is where auth/ref errors surface) |

---

## Delete

### `delete_file`

Delete a path and commit the removal. Standard `dry_run` / `push`
semantics.

---

## Error Handling

Errors are returned as **text responses prefixed with `Error:`** — they
do not raise out of the tool. This keeps the transport simple (one
string-return contract) and lets the LLM read the error text directly.

Soft failures (e.g. refresh couldn't reach Overleaf but a local snapshot
exists) attach a `⚠ could not refresh from Overleaf: ...` warning line
to the tool's body. Hard errors (misconfigured project, bad path, bad
auth on a write) return `Error:` prefixes.

All error text that surfaces a Git remote URL has its embedded
`user:password@` userinfo replaced with `<redacted>@` before it reaches
logs or tool output — the Basic-auth token never leaks through
`GitCommandError.stderr`.

## Observability

Setting `OVERLEAF_TIMING=1` emits one INFO-level log line per tool call
from the `overleaf_mcp.git_ops` logger, formatted as:

```
acquire_project {"project":"<id>","mode":"read|write","elapsed_ms":42.3,"stale":false}
```

Stability: the `acquire_project ` prefix and the four JSON keys
(`project`, `mode`, `elapsed_ms`, `stale`) are a **stable interface** —
external monitoring pipelines may parse this line directly. Additional
keys may be added in future versions (e.g. `tool` under the HTTP
transport) but existing keys will not be renamed or removed without a
major version bump.

The line is silent when `OVERLEAF_TIMING` is unset — zero cost when off
(one env-var lookup per tool call).

See `docs/ARCHITECTURE.md` §3 for the full soft-vs-hard taxonomy.
