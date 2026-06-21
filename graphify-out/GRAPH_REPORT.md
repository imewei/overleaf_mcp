# Graph Report - .  (2026-06-21)

## Corpus Check
- 46 files · ~37,431 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 604 nodes · 1204 edges · 50 communities (25 shown, 25 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 59 edges (avg confidence: 0.6)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Tool Argument & Parameter Types|Tool Argument & Parameter Types]]
- [[_COMMUNITY_Dispatcher Integration Tests|Dispatcher Integration Tests]]
- [[_COMMUNITY_Architecture Concepts & Docs|Architecture Concepts & Docs]]
- [[_COMMUNITY_JSON Schema Properties|JSON Schema Properties]]
- [[_COMMUNITY_Tool Registry & Dispatcher|Tool Registry & Dispatcher]]
- [[_COMMUNITY_Tool Behavior Tests|Tool Behavior Tests]]
- [[_COMMUNITY_MCPB Manifest & Packaging|MCPB Manifest & Packaging]]
- [[_COMMUNITY_Config & Pydantic Models|Config & Pydantic Models]]
- [[_COMMUNITY_Config & Env Var Tests|Config & Env Var Tests]]
- [[_COMMUNITY_Coverage Gap Tests|Coverage Gap Tests]]
- [[_COMMUNITY_Shallow Clone & File Tests|Shallow Clone & File Tests]]
- [[_COMMUNITY_Performance Optimization Tests|Performance Optimization Tests]]
- [[_COMMUNITY_Lock & Concurrency Tests|Lock & Concurrency Tests]]
- [[_COMMUNITY_Server Tool Handlers|Server Tool Handlers]]
- [[_COMMUNITY_RW Lock & Retry Tests|RW Lock & Retry Tests]]
- [[_COMMUNITY_Cache & Auth Fallback Tests|Cache & Auth Fallback Tests]]
- [[_COMMUNITY_ToolContext & Response Wrapper|ToolContext & Response Wrapper]]
- [[_COMMUNITY_Development Guides & Docs|Development Guides & Docs]]
- [[_COMMUNITY_Env Vars & Tool Config|Env Vars & Tool Config]]
- [[_COMMUNITY_MCPB Bundle Tests|MCPB Bundle Tests]]
- [[_COMMUNITY_Timing & Logging Tests|Timing & Logging Tests]]
- [[_COMMUNITY_FastMCP Transport Tests|FastMCP Transport Tests]]
- [[_COMMUNITY_Core Module Files|Core Module Files]]
- [[_COMMUNITY_Server Bootstrap & Entry|Server Bootstrap & Entry]]
- [[_COMMUNITY_Documentation Rationale|Documentation Rationale]]
- [[_COMMUNITY_LaTeX Parsing Functions|LaTeX Parsing Functions]]
- [[_COMMUNITY_Env Config Variables|Env Config Variables]]
- [[_COMMUNITY_Release & Changelog Config|Release & Changelog Config]]
- [[_COMMUNITY_MCPB Build Script|MCPB Build Script]]
- [[_COMMUNITY_Package Init & Version|Package Init & Version]]
- [[_COMMUNITY_Sphinx Docs Config|Sphinx Docs Config]]
- [[_COMMUNITY_Env Config Unit Test|Env Config Unit Test]]
- [[_COMMUNITY_Pull TTL Cache Test|Pull TTL Cache Test]]
- [[_COMMUNITY_Stale Snapshot Fallback|Stale Snapshot Fallback]]
- [[_COMMUNITY_Token Redaction Test|Token Redaction Test]]
- [[_COMMUNITY_Force Pull Bypass Test|Force Pull Bypass Test]]
- [[_COMMUNITY_Acquire Stale Fallback|Acquire Stale Fallback]]
- [[_COMMUNITY_Concurrent Readers Test|Concurrent Readers Test]]
- [[_COMMUNITY_Transient Pull Retry Test|Transient Pull Retry Test]]
- [[_COMMUNITY_Permanent Auth Failure Test|Permanent Auth Failure Test]]
- [[_COMMUNITY_Changelog Overview|Changelog Overview]]
- [[_COMMUNITY_Changelog Rationale|Changelog Rationale]]
- [[_COMMUNITY_Client Setup Rationale|Client Setup Rationale]]
- [[_COMMUNITY_Configuration Rationale|Configuration Rationale]]
- [[_COMMUNITY_FastMCP Framework|FastMCP Framework]]
- [[_COMMUNITY_Graphify Cache Rationale|Graphify Cache Rationale]]
- [[_COMMUNITY_Package Init Rationale|Package Init Rationale]]
- [[_COMMUNITY_Package Version|Package Version]]
- [[_COMMUNITY_Overleaf MCP Project|Overleaf MCP Project]]
- [[_COMMUNITY_README Rationale|README Rationale]]

## God Nodes (most connected - your core abstractions)
1. `MonkeyPatch` - 48 edges
2. `run()` - 41 edges
3. `ProjectConfig` - 37 edges
4. `MonkeyPatch` - 34 edges
5. `_make_fake_repo()` - 34 edges
6. `ensure_repo()` - 27 edges
7. `ProjectConfig` - 26 edges
8. `acquire_project()` - 25 edges
9. `resolve_project()` - 21 edges
10. `ARCHITECTURE.md - Module Layers and Design Rationale` - 20 edges

## Surprising Connections (you probably didn't know these)
- `Shallow Clone Support (OVERLEAF_SHALLOW_CLONE=1) for large projects` --semantically_similar_to--> `TTL-Cached Pull (_LAST_PULL keyed on (project_id, token_hash))`  [INFERRED] [semantically similar]
  README.md → docs/ARCHITECTURE.md
- `CI Workflow (Lint, Type Check, Test Matrix)` --semantically_similar_to--> `Pre-commit Hooks Config (ruff, mypy, bandit, file hygiene)`  [INFERRED] [semantically similar]
  .github/workflows/ci.yml → .pre-commit-config.yaml
- `MonkeyPatch` --uses--> `ProjectConfig`  [INFERRED]
  tests/test_coverage_gaps.py → src/overleaf_mcp/config.py
- `Path` --uses--> `ProjectConfig`  [INFERRED]
  tests/test_coverage_gaps.py → src/overleaf_mcp/config.py
- `LogCaptureFixture` --uses--> `ProjectConfig`  [INFERRED]
  tests/test_optimizations.py → src/overleaf_mcp/config.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Module Dependencies** — development_server_py, development_tools_py, development_git_ops_py, development_config_py, development_latex_py [EXTRACTED 1.00]
- **MCPB Bundle Components** — development_mcpb_bundle, development_server_py, development_tools_py, development_git_ops_py [INFERRED 0.75]
- **Overleaf MCP Module Layer Stack (server → tools → git_ops → config, latex)** — concept_server_py_transport_layer, concept_tools_py_tool_impls, concept_git_ops_py_engine, concept_latex_py_section_parser, concept_config_py_pydantic_config [EXTRACTED 1.00]
- **acquire_project() Two-Phase Pipeline: RW Lock + TTL Cache + Stale Fallback + ToolContext** — concept_acquire_project_context_manager, concept_rw_lock_per_project, concept_ttl_pull_cache, concept_stale_snapshot_fallback, concept_tool_context_wrap [EXTRACTED 1.00]
- **CI Quality Gate: Ruff lint + mypy + bandit + pytest matrix** — workflows_ci_ci_workflow, overleaf_mcp_pre_commit_config, concept_uv_pyproject_no_lock [INFERRED 0.85]

## Communities (50 total, 25 thin omitted)

### Community 0 - "Tool Argument & Parameter Types"
Cohesion: 0.06
Nodes (96): _CommitMessage, description, _DryRun, Exception, Field, _GitToken, Resolve project config from inline credentials or config file.      Inline crede, resolve_project() (+88 more)

### Community 1 - "Dispatcher Integration Tests"
Cohesion: 0.05
Nodes (62): bare_repo(), MonkeyPatch, Path, Repo, Integration tests for every execute_tool dispatcher branch.  These tests exercis, Shorthand: invoke a dispatcher branch synchronously., create_project builds a data: URL for the browser — no git touch., push=False should commit locally but not push to the remote. (+54 more)

### Community 2 - "Architecture Concepts & Docs"
Cohesion: 0.09
Nodes (48): API Reference Index - Module Responsibility Table, 15 MCP Tools - Full CRUD on Overleaf Projects via Git, acquire_project() - Two-Phase Context Manager (Refresh + Concurrency), config.py - Pydantic Models + mtime-Cached Config Loader, Rationale: create_project returns snip_uri URL because no Overleaf REST endpoint for project creation exists, Credential Redaction - user:password@ in Git URLs replaced before logging, Downward-Only Import Invariant (no layer imports upward), Error Handling Contract: tool errors returned as 'Error:' prefixed text, never raised (+40 more)

### Community 3 - "JSON Schema Properties"
Cohesion: 0.06
Nodes (35): default, description, required, title, type, description, required, title (+27 more)

### Community 4 - "Tool Registry & Dispatcher"
Cohesion: 0.07
Nodes (31): execute_tool(), list_tools(), Dispatch a tool call by name. Kept for test compatibility.      The MCP framewor, Return MCP-compatible ``Tool`` objects for every registered tool.      This is a, Any, Dispatcher's safety net for unregistered tool names (line 929)., A function in TOOLS without a real annotation falls back to {'type':'string'}., test_execute_tool_returns_unknown_for_missing_name() (+23 more)

### Community 5 - "Tool Behavior Tests"
Cohesion: 0.09
Nodes (31): Path, Helper: create a project dir under tmp_path with given file content,     return, get_sections on a non-existent file → 'Error: File not found' (line 333)., get_section_content on a non-existent file → error (line 366)., Empty repo (no head commit) AND detached HEAD both degrade gracefully., No .tex file with \\documentclass → '(no main .tex file detected)' (line 608)., Main document found but contains no sections → '(no sections found)' (line 606)., edit_file on a non-existent path → error message (line 642). (+23 more)

### Community 6 - "MCPB Manifest & Packaging"
Cohesion: 0.08
Nodes (25): author, name, compatibility, claude_desktop, platforms, description, OVERLEAF_CONFIG_FILE, OVERLEAF_GIT_TIMEOUT (+17 more)

### Community 7 - "Config & Pydantic Models"
Cohesion: 0.16
Nodes (20): BaseModel, Config, _env_config(), get_project_config(), load_config(), _parse_config_file(), ProjectConfig, Configuration loading and project resolution.  This is the data layer of the ser (+12 more)

### Community 8 - "Config & Env Var Tests"
Cohesion: 0.10
Nodes (21): MonkeyPatch, When defaultProject is unset, the first dict key is used., OVERLEAF_PULL_TTL=not-a-number → defaults to 30s, doesn't raise., OVERLEAF_GIT_TIMEOUT=garbage → defaults to 60s, doesn't raise., OVERLEAF_SHALLOW_DEPTH=garbage → defaults to 1, doesn't raise., When iter_commits returns [], the 'No commits found' branch fires., file_path, since, and until must all reach iter_commits as kwargs., A diff larger than max_output_chars is truncated with a marker. (+13 more)

### Community 9 - "Coverage Gap Tests"
Cohesion: 0.10
Nodes (19): Targeted tests for the coverage gaps identified after Tier-3 (v2) shipped.  The, Asking for a non-existent project name lists what IS available., The load_config -> _env_config path (line 104) when no file exists., is_zip=True must produce a 'application/zip' data URL with content     used verb, When project_name is given, it lands in snip_name (form_data line 128)., Bad mode is rejected before any project lookup happens (no fixture needed)., Both file_path and paths get appended after a -- separator., A bad ref bubbles up as 'Error getting diff: ...' string (line 516-517). (+11 more)

### Community 10 - "Shallow Clone & File Tests"
Cohesion: 0.11
Nodes (19): MonkeyPatch, A file smaller than max_bytes MUST be returned in full with no marker., Without OVERLEAF_SHALLOW_CLONE=1, clone kwargs are empty (full clone)., OVERLEAF_SHALLOW_CLONE=1 enables shallow with configurable depth., Enabled but no depth set — defaults to 1., Nonsense negative depth is clamped to 1 — `git clone --depth=0` is meaningless., A blocking op that exceeds OVERLEAF_GIT_TIMEOUT must raise TimeoutError., Fast ops return their value normally. (+11 more)

### Community 11 - "Performance Optimization Tests"
Cohesion: 0.11
Nodes (17): Regression tests for the performance/stability optimizations.  Covers:   * load_, Cap the total bytes of the tools/list payload to prevent description     bloat f, Helper: spawn a reader coroutine that waits ``start_delay`` seconds,     then tr, The redaction helper replaces ``user:TOKEN@host`` with ``<redacted>@host``., Strings without userinfo pass through unchanged., Companion assertion: if the byte-budget test above gets relaxed     to accommoda, Lock must be released even if the body raises (context-manager invariant)., Clear module-level state before every test (test isolation). (+9 more)

### Community 12 - "Lock & Concurrency Tests"
Cohesion: 0.11
Nodes (18): fake_project(), ProjectConfig, Happy-path write-mode MUST take exactly one exclusive lock and     ZERO shared l, Acquire a shared lock, confirm it, and release. Used by the     contention test, Same invariant as the acquire_project retry test, but for     ``sync_project``:, Cold-start clone timeout has no snapshot to fall back to, so the     TimeoutErro, Expired TTL must re-trigger the pull., Happy path yields a ToolContext with no warnings. (+10 more)

### Community 13 - "Server Tool Handlers"
Cohesion: 0.12
Nodes (16): api_create_file, api_create_project, api_delete_file, api_edit_file, api_get_diff, api_get_section_content, api_get_sections, api_list_files (+8 more)

### Community 14 - "RW Lock & Retry Tests"
Cohesion: 0.12
Nodes (16): _make_fake_repo(), Writer holds the lock, two readers queue, verify readers run in parallel     the, During the 0.5–1.5 s retry backoff, another tool call MUST be able     to run ag, GitCommandError on pull must surface as StaleRepoWarning., Locks are per-project, so different projects must not serialize., Two writers on the same project MUST still serialize (v1 invariant)., A MagicMock shaped like a git.Repo with a settable origin URL., Both attempts fail with transient error → StaleRepoWarning raised. (+8 more)

### Community 15 - "Cache & Auth Fallback Tests"
Cohesion: 0.14
Nodes (16): Path, Default mode (no kwarg) MUST behave as read — back-compat for any     caller tha, Parsing the same file twice should hit the cache, not re-invoke the parser., A file mtime change must invalidate the cache., Bad-token auth failure MUST serve the cached snapshot with a     user-actionable, Redirect CONFIG_FILE to a test-owned path under tmp_path., A fresh clone MUST have user.name + user.email stamped immediately.      Before, An already-stamped repo MUST NOT have its user re-written. (+8 more)

### Community 16 - "ToolContext & Response Wrapper"
Cohesion: 0.14
Nodes (13): Bundle passed to every tool branch.      Attributes:         repo: The prepared, Format the tool response: human text + warnings + optional envelope.          La, ToolContext, Without OVERLEAF_STRUCTURED=1, no envelope is appended (back-compat)., OVERLEAF_STRUCTURED=1 appends JSON envelope; ok=true for clean response., ok=false when warnings are present (stale-repo fallback)., ok=false when the response begins with 'Error:'., test_toolcontext_wrap_appends_block() (+5 more)

### Community 17 - "Development Guides & Docs"
Cohesion: 0.29
Nodes (7): Development Guide, development_mcpb_bundle, development_module_boundaries, development_rationale, Installation Guide, overleaf_config.json, Quickstart Guide

### Community 18 - "Env Vars & Tool Config"
Cohesion: 0.29
Nodes (7): OVERLEAF_PULL_TTL, OVERLEAF_STRUCTURED, OVERLEAF_TIMING, edit_file, status_summary, sync_project, Usage Patterns

### Community 19 - "MCPB Bundle Tests"
Cohesion: 0.38
Nodes (6): _find_bundle(), Path, Post-bundle smoke test for the MCPB archive.  Recommendation from code review: t, Locate the most recent ``*.mcpb`` file under ``dist/``., Vendored pydantic's ``importlib.metadata.version()`` MUST resolve.      This pin, test_mcpb_bundle_importlib_metadata_resolves()

### Community 20 - "Timing & Logging Tests"
Cohesion: 0.29
Nodes (7): LogCaptureFixture, OVERLEAF_TIMING=1 emits a structured per-acquire log line., Without OVERLEAF_TIMING=1, no timing line is emitted (back-compat)., When the stale-snapshot path fires, the timing line records stale=true., test_timing_log_emitted_when_env_set(), test_timing_log_marks_stale_on_fallback(), test_timing_log_silent_when_env_unset()

### Community 21 - "FastMCP Transport Tests"
Cohesion: 0.29
Nodes (6): MonkeyPatch, Transport-layer tests for server.py.  These tests exercise the FastMCP registrat, server.mcp must expose every function in TOOLS — no silent drops., main() must call mcp.run() with no arguments.      We stub mcp.run so the test d, test_every_tool_in_registry_is_registered_on_mcp(), test_main_delegates_to_mcp_run()

### Community 22 - "Core Module Files"
Cohesion: 0.50
Nodes (5): config.py, git_ops.py, latex.py, server.py, tools.py

### Community 24 - "Documentation Rationale"
Cohesion: 0.67
Nodes (3): architecture_rationale, index_rationale, latex_rationale

### Community 25 - "LaTeX Parsing Functions"
Cohesion: 0.67
Nodes (3): latex_get_section_by_title, latex_parse_sections, latex_section_pattern

## Knowledge Gaps
- **71 isolated node(s):** `$schema`, `manifest_version`, `name`, `version`, `description` (+66 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **25 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ProjectConfig` connect `Config & Pydantic Models` to `Tool Argument & Parameter Types`, `Tool Registry & Dispatcher`, `Tool Behavior Tests`, `Config & Env Var Tests`, `Shallow Clone & File Tests`, `Lock & Concurrency Tests`, `Cache & Auth Fallback Tests`, `ToolContext & Response Wrapper`, `Timing & Logging Tests`?**
  _High betweenness centrality (0.047) - this node is a cross-community bridge._
- **Why does `MonkeyPatch` connect `Shallow Clone & File Tests` to `Tool Argument & Parameter Types`, `Pull TTL Cache Test`, `Token Redaction Test`, `Force Pull Bypass Test`, `Acquire Stale Fallback`, `Concurrent Readers Test`, `Transient Pull Retry Test`, `Config & Pydantic Models`, `Permanent Auth Failure Test`, `Stale Snapshot Fallback`, `Performance Optimization Tests`, `Lock & Concurrency Tests`, `RW Lock & Retry Tests`, `Cache & Auth Fallback Tests`, `ToolContext & Response Wrapper`, `Timing & Logging Tests`?**
  _High betweenness centrality (0.037) - this node is a cross-community bridge._
- **Why does `StaleRepoWarning` connect `Tool Argument & Parameter Types` to `Tool Registry & Dispatcher`, `Config & Pydantic Models`, `Shallow Clone & File Tests`, `Lock & Concurrency Tests`, `Cache & Auth Fallback Tests`, `Timing & Logging Tests`?**
  _High betweenness centrality (0.036) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `MonkeyPatch` (e.g. with `Config` and `ProjectConfig`) actually correct?**
  _`MonkeyPatch` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `Path` (e.g. with `Config` and `ProjectConfig`) actually correct?**
  _`Path` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `ProjectConfig` (e.g. with `Config` and `ProjectConfig`) actually correct?**
  _`ProjectConfig` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `MonkeyPatch` (e.g. with `Config` and `ProjectConfig`) actually correct?**
  _`MonkeyPatch` has 2 INFERRED edges - model-reasoned connections that need verification._
