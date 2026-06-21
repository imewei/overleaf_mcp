# Graph Report - .  (2026-05-06)

## Corpus Check
- 40 files · ~213,254 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 702 nodes · 1088 edges · 70 communities (30 shown, 40 thin omitted)
- Extraction: 87% EXTRACTED · 13% INFERRED · 0% AMBIGUOUS · INFERRED: 145 edges (avg confidence: 0.79)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Core MCP Tools|Core MCP Tools]]
- [[_COMMUNITY_Git Ops & Tool Tests|Git Ops & Tool Tests]]
- [[_COMMUNITY_Optimizations Tests|Optimizations Tests]]
- [[_COMMUNITY_Dispatcher Tests|Dispatcher Tests]]
- [[_COMMUNITY_Dispatcher Test Functions|Dispatcher Test Functions]]
- [[_COMMUNITY_Optimization Test Logic|Optimization Test Logic]]
- [[_COMMUNITY_Optimization Test Concepts|Optimization Test Concepts]]
- [[_COMMUNITY_Git Ops Config|Git Ops Config]]
- [[_COMMUNITY_Server Tools Tests|Server Tools Tests]]
- [[_COMMUNITY_Coverage Gaps Tests|Coverage Gaps Tests]]
- [[_COMMUNITY_Config & Tools|Config & Tools]]
- [[_COMMUNITY_Architecture & Docs|Architecture & Docs]]
- [[_COMMUNITY_Config Exceptions|Config Exceptions]]
- [[_COMMUNITY_Coverage Test Rationale|Coverage Test Rationale]]
- [[_COMMUNITY_API Rationale|API Rationale]]
- [[_COMMUNITY_Git Ops Context|Git Ops Context]]
- [[_COMMUNITY_Config Logic|Config Logic]]
- [[_COMMUNITY_Server Test Logic|Server Test Logic]]
- [[_COMMUNITY_Config Loading|Config Loading]]
- [[_COMMUNITY_Project Config Logic|Project Config Logic]]
- [[_COMMUNITY_Docs Guides|Docs Guides]]
- [[_COMMUNITY_Usage Docs|Usage Docs]]
- [[_COMMUNITY_Server Transport Tests|Server Transport Tests]]
- [[_COMMUNITY_MCP Bundle Tests|MCP Bundle Tests]]
- [[_COMMUNITY_Project Tools|Project Tools]]
- [[_COMMUNITY_XPCS Figures|XPCS Figures]]
- [[_COMMUNITY_Development Docs|Development Docs]]
- [[_COMMUNITY_Bundle Context|Bundle Context]]
- [[_COMMUNITY_LaTeX Operations|LaTeX Operations]]
- [[_COMMUNITY_Optimization Context|Optimization Context]]
- [[_COMMUNITY_Coverage Context|Coverage Context]]
- [[_COMMUNITY_Update Context|Update Context]]
- [[_COMMUNITY_Sync Context|Sync Context]]
- [[_COMMUNITY_Module Reset Context|Module Reset Context]]
- [[_COMMUNITY_Summary Context|Summary Context]]
- [[_COMMUNITY_Rewrite Context|Rewrite Context]]
- [[_COMMUNITY_Dirty Tree Context|Dirty Tree Context]]
- [[_COMMUNITY_Head Context|Head Context]]
- [[_COMMUNITY_Env Fallback Context|Env Fallback Context]]
- [[_COMMUNITY_Diff Context|Diff Context]]
- [[_COMMUNITY_Clone Context|Clone Context]]
- [[_COMMUNITY_Missing File Context|Missing File Context]]
- [[_COMMUNITY_Sphinx Conf|Sphinx Conf]]
- [[_COMMUNITY_Init Rationale|Init Rationale]]
- [[_COMMUNITY_Environment Docs|Environment Docs]]
- [[_COMMUNITY_Git Ops Rationale|Git Ops Rationale]]
- [[_COMMUNITY_Git Ops Logic|Git Ops Logic]]
- [[_COMMUNITY_Bootstrap Docs|Bootstrap Docs]]
- [[_COMMUNITY_Coverage Rationale|Coverage Rationale]]
- [[_COMMUNITY_Conf Rationale|Conf Rationale]]
- [[_COMMUNITY_Conf Project|Conf Project]]
- [[_COMMUNITY_Conf Extensions|Conf Extensions]]
- [[_COMMUNITY_Conf Options|Conf Options]]
- [[_COMMUNITY_Default Pull TTL|Default Pull TTL]]
- [[_COMMUNITY_Default Git Timeout|Default Git Timeout]]
- [[_COMMUNITY_Default Shallow Depth|Default Shallow Depth]]
- [[_COMMUNITY_Retry Delay Range|Retry Delay Range]]
- [[_COMMUNITY_Transient Pull Error|Transient Pull Error]]
- [[_COMMUNITY_Last Pull|Last Pull]]
- [[_COMMUNITY_Tool Context Wrap|Tool Context Wrap]]
- [[_COMMUNITY_Reset Module Caches|Reset Module Caches]]
- [[_COMMUNITY_Init|Init]]
- [[_COMMUNITY_Init Version|Init Version]]
- [[_COMMUNITY_Changelog|Changelog]]
- [[_COMMUNITY_Uncached Rationale|Uncached Rationale]]
- [[_COMMUNITY_Readme|Readme]]
- [[_COMMUNITY_Client Setup|Client Setup]]
- [[_COMMUNITY_Configuration|Configuration]]
- [[_COMMUNITY_Changelog Overview|Changelog Overview]]

## God Nodes (most connected - your core abstractions)
1. `Overview of tests/test_optimizations.py` - 61 edges
2. `run()` - 41 edges
3. `Overview of tests/test_dispatcher.py` - 41 edges
4. `run` - 39 edges
5. `_make_fake_repo()` - 34 edges
6. `_make_fake_repo` - 33 edges
7. `ensure_repo()` - 24 edges
8. `acquire_project()` - 22 edges
9. `acquire_project` - 21 edges
10. `resolve_project()` - 20 edges

## Surprising Connections (you probably didn't know these)
- `fake_project()` --calls--> `ProjectConfig`  [INFERRED]
  tests/test_optimizations.py → src/overleaf_mcp/config.py
- `test_toolcontext_wrap_noop_on_empty_warnings()` --calls--> `ToolContext`  [INFERRED]
  tests/test_optimizations.py → src/overleaf_mcp/git_ops.py
- `test_toolcontext_wrap_appends_block()` --calls--> `ToolContext`  [INFERRED]
  tests/test_optimizations.py → src/overleaf_mcp/git_ops.py
- `test_run_blocking_enforces_timeout()` --calls--> `_run_blocking()`  [INFERRED]
  tests/test_optimizations.py → src/overleaf_mcp/git_ops.py
- `test_run_blocking_returns_value_under_timeout()` --calls--> `_run_blocking()`  [INFERRED]
  tests/test_optimizations.py → src/overleaf_mcp/git_ops.py

## Hyperedges (group relationships)
- **Module Dependencies** — development_server_py, development_tools_py, development_git_ops_py, development_config_py, development_latex_py [EXTRACTED 1.00]
- **MCPB Bundle Components** — development_mcpb_bundle, development_server_py, development_tools_py, development_git_ops_py [INFERRED 0.75]
- **XPCS Data Analysis Pipeline** — xpcs_fig1_scattering_pattern, xpcs_fig2_g2_correlation, xpcs_fig3_relaxation_dynamics [INFERRED 0.85]

## Communities (70 total, 40 thin omitted)

### Community 0 - "Core MCP Tools"
Cohesion: 0.06
Nodes (64): Configuration loading and project resolution.  This is the data layer of the ser, Resolve project config from inline credentials or config file.      Inline crede, resolve_project(), acquire_project(), _emit_timing_log(), exclusive(), get_repo_path(), _git_timeout() (+56 more)

### Community 1 - "Git Ops & Tool Tests"
Cohesion: 0.06
Nodes (67): resolve_project, TEMP_DIR, _emit_timing_log, _git_timeout, _lock_for, _project_rwlocks, _pull_ttl, _redact_url (+59 more)

### Community 2 - "Optimizations Tests"
Cohesion: 0.05
Nodes (66): _is_transient_pull_error, _shallow_clone_kwargs, _transient_patterns, test_shallow_depth_falls_back_on_garbage_env, _make_fake_repo, _raise_stale, _reset_module_caches, _write_config (+58 more)

### Community 3 - "Dispatcher Tests"
Cohesion: 0.05
Nodes (63): execute_tool(), Dispatch a tool call by name. Kept for test compatibility.      The MCP framewor, Dispatcher's safety net for unregistered tool names (line 929)., test_execute_tool_returns_unknown_for_missing_name(), bare_repo(), Integration tests for every execute_tool dispatcher branch.  These tests exercis, Shorthand: invoke a dispatcher branch synchronously., create_project builds a data: URL for the browser — no git touch. (+55 more)

### Community 4 - "Dispatcher Test Functions"
Cohesion: 0.09
Nodes (42): _reset_module_state, bare_repo, Overview of tests/test_dispatcher.py, run, test_create_file_dry_run_leaves_tree_clean, test_create_file_no_push, test_create_file_rejects_existing, test_create_file_writes_and_commits (+34 more)

### Community 5 - "Optimization Test Logic"
Cohesion: 0.05
Nodes (40): _make_fake_repo(), A file smaller than max_bytes MUST be returned in full with no marker., Default mode (no kwarg) MUST behave as read — back-compat for any     caller tha, Writer holds the lock, two readers queue, verify readers run in parallel     the, Happy-path write-mode MUST take exactly one exclusive lock and     ZERO shared l, During the 0.5–1.5 s retry backoff, another tool call MUST be able     to run ag, Same invariant as the acquire_project retry test, but for     ``sync_project``:, A git-op TimeoutError on an existing clone MUST fall back to the     local snaps (+32 more)

### Community 6 - "Optimization Test Concepts"
Cohesion: 0.06
Nodes (33): Regression tests for the performance/stability optimizations.  Covers:   * load_, Cap the total bytes of the tools/list payload to prevent description     bloat f, Acquire a shared lock, confirm it, and release. Used by the     contention test, Helper: spawn a reader coroutine that waits ``start_delay`` seconds,     then tr, The redaction helper replaces ``user:TOKEN@host`` with ``<redacted>@host``., Strings without userinfo pass through unchanged., Cold-start clone timeout has no snapshot to fall back to, so the     TimeoutErro, Companion assertion: if the byte-budget test above gets relaxed     to accommoda (+25 more)

### Community 7 - "Git Ops Config"
Cohesion: 0.06
Nodes (32): _build_git_url(), config_git_user(), ensure_repo(), _is_transient_pull_error(), _pull_cache_key(), _pull_ttl(), Return the (project_id, token_hash) key used by _LAST_PULL.      Token hash is t, Resolve the pull TTL from env at call time (allows test monkeypatching). (+24 more)

### Community 8 - "Server Tools Tests"
Cohesion: 0.09
Nodes (26): list_tools(), Return MCP-compatible ``Tool`` objects for every registered tool.      This is a, A function in TOOLS without a real annotation falls back to {'type':'string'}., test_list_tools_handles_unannotated_param_gracefully(), The read_file tool schema MUST expose a max_bytes parameter.      Mirrors get_di, test_read_file_schema_has_max_bytes(), The full tools/list JSON must stay under a generous size budget.      This paylo, All tools that accept project_name as a config key should also accept git_token (+18 more)

### Community 9 - "Coverage Gaps Tests"
Cohesion: 0.11
Nodes (17): Targeted tests for the coverage gaps identified after Tier-3 (v2) shipped.  The, The load_config -> _env_config path (line 104) when no file exists., OVERLEAF_PULL_TTL=not-a-number → defaults to 30s, doesn't raise., OVERLEAF_GIT_TIMEOUT=garbage → defaults to 60s, doesn't raise., When iter_commits returns [], the 'No commits found' branch fires., file_path, since, and until must all reach iter_commits as kwargs., Bad mode is rejected before any project lookup happens (no fixture needed)., A bad ref bubbles up as 'Error getting diff: ...' string (line 516-517). (+9 more)

### Community 10 - "Config & Tools"
Cohesion: 0.13
Nodes (18): _CONFIG_CACHE, _env_config, _parse_config_file, Config, CONFIG_FILE, get_project_config, load_config, ProjectConfig (+10 more)

### Community 11 - "Architecture & Docs"
Cohesion: 0.15
Nodes (17): architecture_rationale, config.py Rationale, fastmcp_fastmcp, git_ops.py Rationale, index_rationale, latex_rationale, main, mcp (+9 more)

### Community 12 - "Config Exceptions"
Cohesion: 0.13
Nodes (16): Exception, ProjectConfig, A single Overleaf project entry (name + id + auth token)., Signal that we couldn't refresh from upstream but have a local snapshot.      ``, Internal signal: pull failed transiently, one retry is advised.      Distinguish, StaleRepoWarning, _TransientPullError, Cold clone with OVERLEAF_SHALLOW_CLONE=1 must log the depth (line 322). (+8 more)

### Community 13 - "Coverage Test Rationale"
Cohesion: 0.12
Nodes (16): Helper: create a project dir under tmp_path with given file content,     return, get_sections on a non-existent file → 'Error: File not found' (line 333)., get_section_content on a non-existent file → error (line 366)., No .tex file with \\documentclass → '(no main .tex file detected)' (line 608)., dry_run on rewrite_file reports old/new sizes without writing (lines 707-708)., update_section on a non-existent file → error (line 753)., dry_run on update_section reports body sizes without writing (781-782)., push=False on delete_file: commits but no push (886->889). (+8 more)

### Community 14 - "API Rationale"
Cohesion: 0.12
Nodes (16): api_create_file, api_create_project, api_delete_file, api_edit_file, api_get_diff, api_get_section_content, api_get_sections, api_list_files (+8 more)

### Community 15 - "Git Ops Context"
Cohesion: 0.14
Nodes (13): Bundle passed to every tool branch.      Attributes:         repo: The prepared, Format the tool response: human text + warnings + optional envelope.          La, ToolContext, Without OVERLEAF_STRUCTURED=1, no envelope is appended (back-compat)., OVERLEAF_STRUCTURED=1 appends JSON envelope; ok=true for clean response., ok=false when warnings are present (stale-repo fallback)., ok=false when the response begins with 'Error:'., test_toolcontext_wrap_appends_block() (+5 more)

### Community 16 - "Config Logic"
Cohesion: 0.18
Nodes (11): BaseModel, Config, _env_config(), _parse_config_file(), Top-level server configuration loaded from overleaf_config.json., Parse overleaf_config.json into a Config. Pure function, no side effects., Build a Config from OVERLEAF_PROJECT_ID / OVERLEAF_GIT_TOKEN, or empty., OVERLEAF_PROJECT_ID + OVERLEAF_GIT_TOKEN both set → 1-project Config. (+3 more)

### Community 17 - "Server Test Logic"
Cohesion: 0.18
Nodes (11): Overview of tests/test_server.py, test_all_project_tools_have_inline_credentials, test_get_diff_schema_has_context_and_truncation, test_list_history_max_limit_200, test_list_history_since_until_params_in_schema, test_resolve_project_falls_back_to_config, test_resolve_project_inline_credentials, test_resolve_project_inline_requires_both (+3 more)

### Community 18 - "Config Loading"
Cohesion: 0.25
Nodes (9): load_config(), Load configuration from file or environment.      The file path is cached by mti, list_projects(), List all configured Overleaf projects., Parsing the same file twice should hit the cache, not re-invoke the parser., A file mtime change must invalidate the cache., test_load_config_memoizes_unchanged_file(), test_load_config_reparses_on_mtime_change() (+1 more)

### Community 19 - "Project Config Logic"
Cohesion: 0.25
Nodes (8): get_project_config(), Get configuration for a specific project., When defaultProject is unset, the first dict key is used., Asking for a non-existent project name lists what IS available., No file, no env vars → ValueError with actionable message., test_get_project_config_falls_back_to_first_when_no_default(), test_get_project_config_raises_for_unknown_project(), test_get_project_config_raises_when_no_projects()

### Community 20 - "Docs Guides"
Cohesion: 0.29
Nodes (7): Development Guide, development_mcpb_bundle, development_module_boundaries, development_rationale, Installation Guide, overleaf_config.json, Quickstart Guide

### Community 21 - "Usage Docs"
Cohesion: 0.29
Nodes (7): OVERLEAF_PULL_TTL, OVERLEAF_STRUCTURED, OVERLEAF_TIMING, edit_file, status_summary, sync_project, Usage Patterns

### Community 22 - "Server Transport Tests"
Cohesion: 0.33
Nodes (5): Transport-layer tests for server.py.  These tests exercise the FastMCP registrat, server.mcp must expose every function in TOOLS — no silent drops., main() must call mcp.run() with no arguments.      We stub mcp.run so the test d, test_every_tool_in_registry_is_registered_on_mcp(), test_main_delegates_to_mcp_run()

### Community 23 - "MCP Bundle Tests"
Cohesion: 0.4
Nodes (5): _find_bundle(), Post-bundle smoke test for the MCPB archive.  Recommendation from code review: t, Locate the most recent ``*.mcpb`` file under ``dist/``., Vendored pydantic's ``importlib.metadata.version()`` MUST resolve.      This pin, test_mcpb_bundle_importlib_metadata_resolves()

### Community 24 - "Project Tools"
Cohesion: 0.33
Nodes (6): create_project(), Create a new Overleaf project from LaTeX content.      Returns an ``overleaf.com, is_zip=True must produce a 'application/zip' data URL with content     used verb, When project_name is given, it lands in snip_name (form_data line 128)., test_create_project_passes_through_project_name(), test_create_project_with_zip_uses_zip_mime_type()

### Community 25 - "XPCS Figures"
Cohesion: 0.33
Nodes (6): XPCS Figure 1 Overview, 2D XPCS Speckle Scattering Pattern, Intensity Autocorrelation g2(q,t), XPCS Figure 2 Overview, XPCS Figure 3 Overview, Relaxation Rate vs Wavevector q

### Community 26 - "Development Docs"
Cohesion: 0.5
Nodes (5): config.py, git_ops.py, latex.py, server.py, tools.py

### Community 27 - "Bundle Context"
Cohesion: 1.0
Nodes (3): _find_bundle, Overview of tests/test_mcpb_bundle.py, test_mcpb_bundle_importlib_metadata_resolves

### Community 28 - "LaTeX Operations"
Cohesion: 0.67
Nodes (3): latex_get_section_by_title, latex_parse_sections, latex_section_pattern

## Knowledge Gaps
- **288 isolated node(s):** `Regression tests for the performance/stability optimizations.  Covers:   * load_`, `Clear module-level state before every test (test isolation).`, `Redirect CONFIG_FILE to a test-owned path under tmp_path.`, `A MagicMock shaped like a git.Repo with a settable origin URL.`, `Parsing the same file twice should hit the cache, not re-invoke the parser.` (+283 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **40 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `execute_tool()` connect `Dispatcher Tests` to `Core MCP Tools`?**
  _High betweenness centrality (0.083) - this node is a cross-community bridge._
- **Why does `list_tools()` connect `Server Tools Tests` to `Core MCP Tools`?**
  _High betweenness centrality (0.044) - this node is a cross-community bridge._
- **What connects `Regression tests for the performance/stability optimizations.  Covers:   * load_`, `Clear module-level state before every test (test isolation).`, `Redirect CONFIG_FILE to a test-owned path under tmp_path.` to the rest of the system?**
  _288 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Core MCP Tools` be split into smaller, more focused modules?**
  _Cohesion score 0.06 - nodes in this community are weakly interconnected._
- **Should `Git Ops & Tool Tests` be split into smaller, more focused modules?**
  _Cohesion score 0.06 - nodes in this community are weakly interconnected._
- **Should `Optimizations Tests` be split into smaller, more focused modules?**
  _Cohesion score 0.05 - nodes in this community are weakly interconnected._
- **Should `Dispatcher Tests` be split into smaller, more focused modules?**
  _Cohesion score 0.05 - nodes in this community are weakly interconnected._
