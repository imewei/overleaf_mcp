# Overleaf MCP — Ultradeep Optimization Plan **v2**

> Author: ultra-think pass on 2026-04-16, after Tier-0/1/2 (v1) shipped.
> Scope: efficiency, effectiveness, performance stability of `overleaf_mcp`.
> Style: continues the v1 thought-trace numbering from `T7.x` onward.

---

## Executive Summary

`overleaf_mcp` v1.1 has already shipped every item from `OPTIMIZATION_PLAN.md`:
TTL pull cache, mtime-memoized config, async git via `asyncio.to_thread`, per-project
locks, hard timeouts, visible-staleness warnings, the module split, FastMCP 3.x
migration, structured envelope, shallow clones, MCPB bundle, mypy --strict clean,
100 % `server.py` coverage. The remaining wins are **second-order** — they target
read concurrency, context-window economy, agent-loop self-diagnosis, and a
narrow class of resilience bugs that the v1 work exposed but did not close.

### Recommended Tier-3 Actions (this plan)

| # | Change                                                      | Impact | Risk | LOC |
|---|-------------------------------------------------------------|--------|------|-----|
| 1 | **Reader-writer lock** in `acquire_project`                 | High   | Med  | ~40 |
| 2 | **`max_bytes` guardrail on `read_file`**                    | Med    | Low  | ~15 |
| 3 | **One-shot retry on transient pull failure** (with jitter)  | Med    | Low  | ~25 |
| 4 | **`OVERLEAF_TIMING=1`** — per-tool latency log line          | Low    | Low  | ~20 |
| 5 | **Move `config_git_user` to clone-time** (idempotent)       | Low    | Low  | ~10 |
| 6 | **Tool-description audit** — sharpen `Field(description=...)` | Med    | Low  | ~50 |
| 7 | **Cache key precision** — extend `_LAST_PULL` key to `(project_id, token_hash)` | Low (today) / Critical (post-HTTP-transport) | Low | ~10 |
| 8 | **Stale-snapshot test for auth failure** (gap from v1)       | Test   | Low  | ~30 |

---

## Ultra-Think Reasoning Trace (continues from v1)

### T7 — What v1 Did Not Solve

**T7.1 — Read concurrency is still serialized.**
`acquire_project()` takes a single `asyncio.Lock` per project. Two `read_file` calls
on the same project queue behind each other even though both are side-effect-free
after the (cached) pull. For an agent doing burst reads (`list_files` →
`get_sections` → `read_file × N`), wall-clock latency is the sum of per-call disk
work, not the max. This was an explicit design decision in v1 (correctness over
throughput), but the contract can be loosened safely.
**Confidence: High (0.90)**

**T7.2 — `read_file` has no output guardrail.**
`get_diff` already has `max_output_chars` (default 120 000, hard ceiling 500 000).
`read_file` returns the entire file. Overleaf projects routinely contain
multi-MB `main.tex`, supplementary `.bib`, and large generated `.tex` files. A
single `read_file` on such a file dumps the whole blob into Claude's context.
**Confidence: High (0.95)**

**T7.3 — Pull failures fall through to one outcome only.**
On a transient `git pull` failure (DNS blip, ephemeral 502 from Overleaf's git
proxy), the current code goes straight to `StaleRepoWarning` and serves cached
content. A single transparent retry with bounded jitter (e.g. 1× retry after
0.5–1.5 s) would convert most flake into success without changing the failure
contract for genuine outages.
**Confidence: Medium (0.75)** — depends on actual transient/permanent ratio at
the Overleaf git endpoint, which we have not measured. If most failures are
auth or ref-not-found, retries waste a round-trip. **Mitigation:** only retry
on a whitelist of transient error patterns (timeout, 5xx, connection reset).

**T7.4 — No latency observability.**
We added timeouts but no timing. Users (and we) cannot answer "is `git pull`
or LaTeX-section-parsing the bottleneck on this project?" without strace.
A single `OVERLEAF_TIMING=1` env flag emitting `tool=read_file project=xxx
phase=pull elapsed_ms=287` log lines costs ~20 LOC and makes every future perf
investigation cheap.
**Confidence: High (0.92)**

**T7.5 — Tool-schema bytes are recurring context cost.**
The 15-tool catalogue ships in every `tools/list` response — that text lives in
Claude's context for the duration of every session. Some `Field(description=...)`
strings are over-explained (the long `get_diff.mode` description is great
because it shapes selection); others are under-explained (e.g. `read_file`
description is "Read file contents" — it doesn't tell the model what to do
with binary files, what the size ceiling is, or that it's the right tool for
`.bib` and `.cls` too). Each character is a tradeoff: more text = better
selection by the model, but more tokens consumed every turn.
**Confidence: High (0.90)** — verifiable by computing `tools/list` token count
before and after the audit.

**T7.6 — Cache-key narrowness is a latent HTTP-transport bug.**
`_LAST_PULL` is keyed by `project_id` alone. The v1 code comment already flags
this: if a future HTTP transport multiplexes clients carrying different tokens
for the same `project_id`, client A's freshness flag would suppress a needed
pull for client B. Today's stdio model can only ever serve one client per
process so this is benign — but it is a one-line change to extend the key to
`(project_id, sha256(token)[:16])` now and avoid a future regression.
**Confidence: High (0.95)**

**T7.7 — `config_git_user` writes `.git/config` on every push.**
Every write tool calls `config_git_user(ctx.repo)` before commit. That's
`.git/config` mutation + fsync per write op. Once-per-clone (right after
`Repo.clone_from`) is sufficient — the values don't change between calls. The
saving is microseconds per write, but it removes the only fsync-class side
effect from the hot write path and simplifies the call sites.
**Confidence: Medium (0.70)** — needs verification that GitPython's
`Repo.clone_from` consistently produces a writable `.git/config` we can stamp.

### T8 — Branch Exploration: Read-Concurrency Strategy

**T8.1 — Strategy A: Shared/Exclusive lock (preferred).**
Replace `_PROJECT_LOCKS: dict[str, Lock]` with a custom `_RWLock` (small async
class: writer-priority, reader counter). Read tools acquire shared; write tools
acquire exclusive. The TTL pull check happens *outside* the read lock (under
the writer lock if a refresh is needed), so concurrent readers within a TTL
window all see the same fresh snapshot at zero extra cost.

Pros: full read concurrency; preserves write-vs-write and write-vs-read
serialization (the v1 race fix).
Cons: ~40 LOC of new primitive; needs concurrency tests.

**T8.2 — Strategy B: Snapshot-and-release.**
Acquire the lock just long enough to: refresh-if-stale + grab the commit SHA +
release. Then read at that SHA without holding the lock. Pros: simpler. Cons:
filesystem state can mutate under a reader if a concurrent writer commits
during the read — would require reading via `git show <sha>:<path>` rather than
the working tree, which is a much bigger semantic change.

**Decision (T8.3):** Strategy A. Strategy B's working-tree-vs-blob divergence
is too invasive for the win.
**Confidence: High (0.88)**

### T9 — Validation Strategy

For each item:

1. **Read-write lock** — port the v1 `test_acquire_project_serializes_same_project`
   test, then add `test_acquire_project_allows_concurrent_readers` that asserts
   `max_concurrent > 1` for two read-tagged acquisitions and still `== 1` for
   any pair containing a write-tagged acquisition.
2. **`max_bytes`** — schema-presence test + a fixture file > limit, asserting
   truncation marker appears in output.
3. **Pull retry** — patch `Repo.pull` to fail once-then-succeed; assert the
   tool returns cleanly with no `⚠` warning. Patch to fail-twice; assert
   `StaleRepoWarning` path engages.
4. **Timing flag** — env-on emits a log line; env-off does not.
5. **`config_git_user` in clone** — clone in a tmp_path; assert
   `.git/config` has `[user] name email` set immediately, and that subsequent
   write ops do not re-stamp it.
6. **Tool-description audit** — measure `tools/list` JSON size before/after,
   target ≤ 5 % growth net while specific descriptions improve.
7. **Cache key** — unit test: two `ProjectConfig` with same `project_id` but
   different `git_token` get separate `_LAST_PULL` entries.
8. **Stale-snapshot auth-failure test** — patch pull to raise the
   GitCommandError that Overleaf returns on bad token; assert the tool
   response includes the warning and the body still serves cached content.

---

## Risk Matrix

| Change                | Perf gain | Correctness risk          | Back-compat risk |
|-----------------------|-----------|---------------------------|------------------|
| RW lock               | ★★★       | Med — must keep write serialization | None — read-only callers see speedup |
| `max_bytes` on read   | ★         | None                      | None — default high enough that real reads succeed |
| One-shot pull retry   | ★         | None — ceiling unchanged  | None — successful path unchanged |
| `OVERLEAF_TIMING=1`   | 0         | None                      | None |
| `config_git_user` move| ★ (negligible) | Low — verify clone produces writable config | None |
| Tool-description audit | ★ (per-turn) | None                | None |
| Cache key extension   | 0 today   | None                      | None |
| Auth-failure test     | 0         | Catches bug class         | None |

---

## Suggested Commit Sequence

1. `feat(perf): reader-writer lock for per-project concurrency`
2. `feat(safety): max_bytes guardrail on read_file`
3. `feat(stability): one-shot retry for transient pull failures`
4. `feat(obs): OVERLEAF_TIMING=1 emits per-tool latency log line`
5. `refactor: stamp git user.name/email at clone time, not per-push`
6. `docs(tools): sharpen Field(description=...) for selection accuracy`
7. `refactor(safety): extend _LAST_PULL key with token hash`
8. `test: stale-snapshot fallback for auth failure`

Each commit is independently shippable and independently revertable. CI should
run the existing test suite plus the new tests for each item.

---

## What v2 Explicitly Does NOT Recommend

- **Per-file content cache.** Disk reads of LaTeX files are sub-millisecond on
  any modern SSD; the kernel page cache already does this. Adding our own layer
  is complexity without measurable win.
- **`git --batch` / persistent git daemon.** Process-startup cost is dwarfed by
  network round-trip on real Overleaf pulls. The TTL cache already removes
  >90 % of those round-trips for read bursts.
- **Custom thread-pool sizing for `asyncio.to_thread`.** Default executor is
  fine until concurrent project count exceeds ~10, which is far above realistic
  stdio-MCP usage.
- **Replacing the snip_uri `create_project` flow.** v1 already documented why
  this is a deliberate constraint of Overleaf's public API surface, not a bug.

---

## Files to Touch (v2)

- `src/overleaf_mcp/git_ops.py` — RW lock, retry, timing, `config_git_user`
  move, cache-key extension
- `src/overleaf_mcp/tools.py` — `max_bytes` on `read_file`, description audit
- `tests/test_optimizations.py` — RW lock, retry, timing, cache-key, auth-fail
- `tests/test_dispatcher.py` — `read_file` truncation end-to-end
- `README.md` — document `OVERLEAF_TIMING`, `read_file.max_bytes`
- `manifest.json` — add `OVERLEAF_TIMING` to `user_config` schema

---

## Closing Confidence Assessment

| Item | Wins | Gotchas | Net Confidence |
|------|------|---------|----------------|
| RW lock | 2-5× speedup on burst reads | Test the writer-priority invariant | 0.88 |
| max_bytes | Eliminates 5 MB context dumps | Pick default carefully (suggest 200 000) | 0.92 |
| Retry | Cuts flake-class failures | Whitelist patterns, don't blanket-retry | 0.78 |
| Timing | Free observability | None | 0.95 |
| config_git_user | Cleaner write path | Verify clone-time write actually persists | 0.72 |
| Description audit | Better tool selection | Subjective — measure with eval if possible | 0.85 |
| Cache key | Future-proofs HTTP transport | None | 0.95 |
| Auth-fail test | Closes test gap | None | 0.95 |

**Overall plan confidence: 0.86.** Tier-3 is smaller than Tier-0/1/2 in
absolute LOC because v1 already harvested the obvious wins. The remaining
gains require more design care per LOC — but each item is independently
valuable and can be sequenced without coupling.
