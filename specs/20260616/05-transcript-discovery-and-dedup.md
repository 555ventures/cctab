---
date: 2026-06-16
status: done
risk: T2
area: data-aggregation
design: false
breaking: false
depends_on: []
depended_on_by: []
---

# Recursive transcript discovery + per-message dedup

## Goal

cctab's reported token totals are wrong in two compounding directions, both confirmed against
real `~/.claude/projects` logs. (1) `_parse_file` sums `message.usage` on **every** JSONL line,
but a single assistant message is written as **multiple lines sharing one `message.id`**, each
repeating the *identical* usage block — inflating totals ~2.5× (measured 2.49× on one live
transcript). (2) `_iter_files` only globs `<folder>/*.jsonl` (depth 2), so it **misses 2,298
nested subagent/workflow transcripts holding 1.35 B tokens — 59 % of all usage** — which is why
dynamic-workflow Sonnet delegation appears as ~0 on the dashboard. "Done" = each billed API
message (keyed by `message.id`) is counted exactly once, and nested transcripts are discovered
and folded into the correct directory, so `EST $` / `CLIENT $` become a defensible cost basis.

## Decisions (locked — workers apply verbatim, never override)

| ID | Decision | One-line rationale |
|----|----------|--------------------|
| D1 | The dedup unit is `message.id`; each id's usage is counted **once per scan run** (global across all files in that `scan`/`scan_daily` call). | Real logs repeat identical usage across content-block lines *and* across resumed-session files; `message.id` is the billing unit. Per-file dedup alone leaves cross-file duplicates (rejected). |
| D2 | Transcript discovery is **recursive** over `projects_dir` (`rglob("*.jsonl")`); nested `subagents/…` and `subagents/workflows/wf_*/agent-*.jsonl` transcripts are first-class and attributed by **their own internal `cwd`**. | 59 % of real usage lives at depth ≥3; every nested transcript carries an internal `cwd` (300/300 sampled), so existing cwd-attribution folds them in correctly. |
| D3 | File iteration is **deterministic**: files sorted ascending by path string. | "First occurrence wins" (D5) and cross-cwd attribution must be reproducible run-to-run. |
| D4 | A usage line **lacking** a string `message.id` is always counted, and never merged with another id-less line. | Without a key it cannot be deduped; counting each preserves totals for legacy/synthetic transcripts (and keeps existing id-less fixtures green). |
| D5 | When one `message.id` has **differing** usage across occurrences (~2.6 % of ids), the **first occurrence in scan order** (D3 file order, then line order) is counted; later occurrences contribute nothing. | Display-estimate tool, not an accounting ledger; first-occurrence is deterministic and the deltas are tiny cache-read variations. Max-total tie-break rejected as over-engineering (see A1). |
| D6 | In `scan_daily(cwd=X)`, scope filtering happens **before** a file can consume an id (resolve scope via `_first_cwd`, skip out-of-scope files entirely — do not parse them, do not add their ids to `seen_ids`). | Dedup must reduce double-counting without dropping legitimate in-scope usage; a global set marked while parsing every file would let an out-of-scope duplicate suppress the in-scope occurrence. This is the one subtle interaction between D1 and cwd scope. |
| D7 | Every discovered transcript file (incl. nested) counts as one `Usage.sessions`. | Simplest extension of current semantics; the `SESS` count is leaderboard-era (test/library only) and not on the daily view. |

## File Plan

| Path | Action | Layer | Summary |
|------|--------|-------|---------|
| `src/cctab/data.py` | MODIFY | data | Recursive deterministic `_iter_files` (rglob, sorted); global per-`message.id` dedup threaded through `_parse_file` + `scan` + `scan_daily`, scope-safe per D6; preserve best-effort parsing. |
| `tests/test_data.py` | MODIFY | tests | AC-DEDUP-1..4, AC-DISCOVER-1..4, AC-ROBUST-1. Extend `_write_session` to lift an optional `"id"` key into `entry["message"]["id"]` (a sibling of `usage`, NOT a key inside `usage`) — mirror the existing `"model"`/`"timestamp"` lifting at `tests/test_data.py:44-51`; add a nested-dir fixture helper that writes a transcript at a caller-given relative subpath (e.g. `<enc>/<session>/subagents/agent-x.jsonl`). |

## Contracts

Signature changes in `src/cctab/data.py` (the dedup set is threaded; public call sites keep
working with defaults):

```python
def _iter_files(projects_dir: Path) -> Iterable[Path]:
    """Yield every *.jsonl under projects_dir at ANY depth, sorted by path (D2, D3)."""

def _first_cwd(path: Path) -> str | None:
    """Best-effort: return the first line's `cwd` string, else None. Reads only until the
    first cwd is found (cheap). Never raises (OSError / JSONDecodeError swallowed). Used by
    scan_daily to scope-filter a file BEFORE its ids are consumed (D6)."""

def _parse_file(path: Path, seen_ids: set[str] | None = None) -> Session:
    """Parse one transcript. Each message.usage is counted at most once per *seen_ids*:
    a line whose str message.id is already in seen_ids contributes no usage (D1).
    seen_ids is mutated in place (new ids added). seen_ids=None → a fresh per-call set
    (per-file dedup; the standalone/test default). Lines lacking a str message.id are
    always counted (D4). Best-effort: never raises on malformed input or missing keys."""
```

`scan(...)` and `scan_daily(...)` keep their existing public signatures. The dedup set is
**global across the files each actually counts**, but the two differ in how they avoid the
scope/dedup hazard (D6):

- **`scan(...)`** (no scope): create one `seen_ids` and thread it through `_parse_file` for
  every file, in `_iter_files` order. Global dedup, simple.
- **`scan_daily(cwd=X)`** MUST scope-filter **before** a file can consume an id, or an
  out-of-scope duplicate would suppress the in-scope occurrence (D6, AC-DISCOVER-3). Required
  shape: create one `seen_ids`; for each file, resolve its scope cheaply via
  `_first_cwd(path)` (falling back to `path.parent.name` to match `_parse_file`'s own cwd
  fallback) and `cwd_in_scope(...)`; **skip out-of-scope files entirely** (do not parse, do
  not touch `seen_ids`); only for in-scope files call `_parse_file(path, seen_ids)` and
  aggregate. Because out-of-scope files never touch `seen_ids`, the in-scope result is
  independent of file order (AC-DISCOVER-3); among in-scope files, `_iter_files`' sorted order
  makes "first occurrence wins" deterministic (D5, AC-DEDUP-4). A single global `seen_ids`
  marked while parsing *every* file (including out-of-scope) is explicitly WRONG here.

`Usage`, `Session`, `Project`, `DayUsage` dataclasses are unchanged.

## Behavior

- **Numbers will move sharply and intentionally.** Per-directory totals drop ~2.5× from dedup
  and rise from newly-discovered nested usage; net direction depends on how much of a project's
  work is delegated to subagents/workflows. This is the correction, not a regression — the
  daily view, `EST $`, and `CLIENT $` all recompute from the deduped, fully-discovered numbers
  with no formatting change.
- **`scan_daily(cwd=X)` (the only TUI path):** discovers nested transcripts, filters by each
  session's internal `cwd` via the existing `cwd_in_scope` (worktrees/`.claude` still fold in),
  and counts each `message.id` once. Per D6, an id appearing in both an in-scope and an
  out-of-scope file is counted once **under the in-scope day/family**, never dropped.
- **`scan(merge_by_cwd=True)`:** nested transcripts group by their internal `cwd` into the right
  project. *Known quirk (out of scope):* `scan(merge_by_cwd=False)` keys by immediate folder
  name, so a nested file rows under `subagents`/`wf_*`; this path is test/library-only (the
  leaderboard was removed in spec `02`) and is left as-is.
- **`journal.jsonl` and other non-transcript nested files** parse to empty Sessions (0 usage
  lines, verified 0/120) — harmless; best-effort parsing already tolerates them.
- **Performance:** discovery grows from ~559 to ~2,857 files on a real corpus; the existing
  `progress(done, total)` callback already covers the larger count. Interactive launch is
  expected to stay acceptable (A5).

## Acceptance Criteria

- **AC-DEDUP-1**: WHEN a single transcript file has two usage lines sharing `message.id` `"m1"`,
  each with `input_tokens=100`, THE SYSTEM SHALL count that message once.
  (two `m1` lines @100 → `project.usage.input == 100`, not `200`) → `test_dedup_within_file`
  in `tests/test_data.py`
- **AC-DEDUP-2**: WHEN two different transcript files under the same cwd each contain a line with
  `message.id` `"m1"` (`input_tokens=100`), THE SYSTEM SHALL count it once across the merged
  project. (→ merged `input == 100`) → `test_dedup_across_files` in `tests/test_data.py`
- **AC-DEDUP-3**: WHEN two usage lines have **no** `message.id`, each `input_tokens=100`, THE
  SYSTEM SHALL count both. (→ `input == 200`) → `test_idless_lines_all_counted` in
  `tests/test_data.py`
- **AC-DEDUP-4**: WHEN `message.id` `"m1"` appears with differing usage in two files **in the
  same encoded folder** (so only the filename governs sort order) — `<enc>/a.jsonl`
  `input_tokens=100` and `<enc>/b.jsonl` `input_tokens=999` — THE SYSTEM SHALL count the
  first-in-path-order occurrence only. (full-path sort puts `<enc>/a.jsonl` < `<enc>/b.jsonl`
  → `input == 100`) → `test_differing_usage_first_wins` in `tests/test_data.py`
- **AC-DISCOVER-1**: WHEN a transcript sits at `<enc>/<session>/subagents/agent-x.jsonl` (depth
  4) with internal `cwd="/work/proj"` and `sonnet` usage `output_tokens=50`, THE SYSTEM SHALL
  discover it and fold it into the `/work/proj` project for both `scan(merge_by_cwd=True)` and
  `scan_daily(cwd="/work/proj")`. (→ project/day `sonnet.output == 50`) →
  `test_nested_subagent_discovered` in `tests/test_data.py`
- **AC-DISCOVER-2**: WHEN a transcript sits at
  `<enc>/<session>/subagents/workflows/wf_1/agent-y.jsonl` (depth 6) with internal
  `cwd="/work/proj"`, THE SYSTEM SHALL discover and attribute it to `/work/proj`. →
  `test_deep_workflow_transcript_discovered` in `tests/test_data.py`
- **AC-DISCOVER-3**: WHEN `message.id` `"m1"` appears in an in-scope nested file (cwd
  `/work/proj`) and also in an out-of-scope file (cwd `/other`), THE SYSTEM SHALL count `m1` once
  under `/work/proj` for `scan_daily(cwd="/work/proj")` — the out-of-scope copy must not suppress
  it. (→ `/work/proj` day total includes `m1`; result independent of file sort order) →
  `test_out_of_scope_dup_does_not_suppress` in `tests/test_data.py`
- **AC-DISCOVER-4**: WHEN a nested `journal.jsonl` contains lines with no `message.usage`, THE
  SYSTEM SHALL contribute zero tokens and SHALL NOT raise. → `test_journal_file_harmless` in
  `tests/test_data.py`
- **AC-ROBUST-1**: WHEN a discovered transcript (nested or top-level) contains a malformed JSON
  line, a missing `message`/`usage`, or is unreadable, THE SYSTEM SHALL skip it without raising
  (best-effort preserved). → `test_malformed_nested_does_not_raise` in `tests/test_data.py`

## Assumptions (escalation triggers)

- **A1**: Identical-usage-per-id is the norm; differing-usage ids (~2.6 % measured) are minor
  cache-read deltas. **If false** (many differing ids with material $ swing): revisit D5's
  tie-break toward max-total; consult retainer before changing the locked decision.
- **A2**: Cross-file duplicate ids are essentially same-cwd session-resume (67 ids in >1 file,
  all identical usage); cross-cwd id sharing is negligible. **If false:** D6 already mandates the
  scope-safe behavior; ensure the implementation filters scope before letting a file consume an
  id (don't pre-consume out-of-scope ids in `scan_daily`).
- **A3**: Every nested transcript carries an internal `cwd` (300/300 sampled). **If false** for
  some files: `_parse_file`'s `cwd or path.parent.name` fallback would misattribute them to
  `subagents`/`wf_*`; fallback — derive cwd from the top-level encoded ancestor folder name.
- **A4**: `journal.jsonl` and other nested non-transcript files carry no `message.usage` (0/120).
  **If false:** global dedup (D1) still prevents double-count and best-effort parsing prevents
  crashes; no action needed.
- **A5**: `rglob` over ~2,857 files stays fast enough for interactive launch. **If false:** the
  existing `progress` callback covers UX; deeper optimization (parallel parse) is a separate spec.
- **A6**: This change stays **read-only** under `~/.claude` (recursion only reads). **If false**
  (a write/exec/network path appears): upgrade to T3 and checkpoint per pipeline rules § Build.

## Rationale

Two bugs, both proven against live logs rather than inferred. The double-count (`_parse_file`
summing every line) dominates within a file (~2.5×) because Claude Code writes one JSONL line
per assistant **content block**, each carrying the same `message.usage` and the same
`message.id`. The fix is to count each `message.id` once — but a corpus probe showed 67 ids
recur **across** files (session resume re-logs prior messages), so per-file dedup is
insufficient and dedup must be **global per scan run** (D1). That global set interacts with
`scan_daily`'s cwd scope: a naïve "mark seen on every parse" would let an out-of-scope file
consume an id the in-scope file then skips, under-counting the in-scope view — hence D6 and
AC-DISCOVER-3 pin the safe behavior explicitly. Determinism (D3) makes "first occurrence wins"
(D5) reproducible; the differing-usage minority (2.6 %) isn't worth a max-total rule on a display
estimate. The missing-transcripts bug is pure discovery: `rglob` instead of one-level `glob`,
and since every nested file carries its own `cwd`, the existing attribution and `cwd_in_scope`
worktree-folding need no change. **Watch during execution:** the scope/dedup interaction (the
easiest place to introduce an under-count); the `sessions` count inflating (D7, accepted); and
launch latency at ~5× file count (A5). The numbers moving sharply is the *point* — it is the
correction the user has been asking for.

**Adversarial check (1 refuter, T2).** Fixed: the Contracts/`D6` scope-vs-dedup contradiction
(the central finding) — `scan_daily` now scope-filters via `_first_cwd` *before* a file can
consume an id, so the in-scope result is order-independent; the `_write_session` `"id"`
placement (`message.id`, sibling of `usage`); and AC-DEDUP-4's sort ambiguity (both files pinned
to one folder). **Rejected:** (a) "Canonical Delta file missing from File Plan" — by design;
shared invariants § Canonical Docs Loop has `/spec:review` apply the delta on `done`, it is not
a build-time row. (b) `scan(merge_by_cwd=False)` keying nested files under `subagents`/`wf_*` —
deliberately out of scope: that path is test/library-only (the leaderboard was removed in spec
`02`), and existing flat-file tests don't exercise nesting, so they stay green. (c) larger
`progress` total / app.py handler — a benign int through an unchanged callback contract.

## Canonical Delta

Update `docs/canonical/daily-cost-view.md`:

- Correct the data.py module-docstring claim recorded in canon — transcripts are **not** "one
  `*.jsonl` per session at one level"; Claude Code also writes **nested** subagent/workflow
  transcripts at depth ≥3 (`<cwd>/<session>/subagents/…`, and
  `subagents/workflows/wf_*/agent-*.jsonl`), each with its own internal `cwd`.
- Add a **"transcript discovery"** subsection: discovery is recursive (`_iter_files` →
  `rglob("*.jsonl")`, sorted for determinism); nested transcripts are first-class and attributed
  by their internal `cwd`, so `cwd_in_scope` folds workflow/subagent usage into the launch
  directory. Non-transcript nested files (`journal.jsonl`) parse to empty Sessions, harmless.
- Add a **"per-message dedup"** subsection: a single assistant message is written as multiple
  JSONL lines sharing one `message.id`, each repeating identical `message.usage`; cctab counts
  each `message.id` **once per scan run** (global), first-occurrence-wins in deterministic path
  order, id-less lines always counted. This is what makes token totals — and therefore `EST $`
  and `CLIENT $` — a defensible billing basis rather than a ~2.5× over-count.
