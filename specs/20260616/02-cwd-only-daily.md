---
date: 2026-06-16
status: done
risk: T2
area: tui
design: false
breaking: true
depends_on: []
depended_on_by: ["03-per-dir-margin-config", "04-row-select-csv-copy"]
---

# cwd-only daily view (remove global scope, leaderboard, drill-down)

## Goal

Turn cctab from a multi-mode usage browser into a single-purpose, per-directory billing
view. The daily cost table becomes the sole screen, always scoped to the directory cctab
was launched in. The cross-directory leaderboard (`ProjectsScreen`), its session drill-down
(`SessionsScreen`), the global-scope toggle (`g` / `--global`), and the daily/projects mode
switch (`d` / `p`) are removed. "Done" means launching cctab shows only the launch cwd's
daily breakdown, the gate is green, and the app boots headlessly. This is the foundation the
per-directory margin (`03`) and CSV billing export (`04`) build on — it removes the
cross-directory aggregation that made a per-directory margin ambiguous.

## Decisions (locked — workers apply verbatim, never override)

| ID | Decision | One-line rationale |
|----|----------|--------------------|
| D1 | cctab is **always** scoped to the launch cwd; remove the `g` / `action_toggle_scope` global toggle and the `scope_cwd is None` (global) code path. `scope_cwd` is set once to the launch cwd and never changes. | The whole tool is now "this directory's costs"; global scope is the thing being removed. |
| D2 | Keep `MODES = {"daily": DailyScreen}` as a **single-entry** map with `DEFAULT_MODE = "daily"` (this preserves Textual's auto-mount boot path). Remove the `"projects"` entry, `ProjectsScreen`, `SessionsScreen`, `COLUMNS`, `SORTABLE`, and the `d` / `p` `switch_mode` bindings. `DailyScreen` is the only mode. | "Get rid of global view" — the leaderboard is the all-directories view. Keeping a 1-entry `MODES` is the smallest safe change (no `push_screen` restructure). |
| D3 | Remove `SessionsScreen` (the per-project drill-down) entirely — it was only reachable from a `ProjectsScreen` row. | Orphaned by D2; user chose daily-only (no drill-down) for now. |
| D4 | Remove the `--global` / `-g` CLI flag and `parse_scope`; `main()` just runs `CCTab()`. Replace `CCTab.__init__(self, global_scope=False)` with `CCTab.__init__(self, launch_cwd: str | None = None)` where `self._launch_cwd = launch_cwd or os.getcwd()` and `self.scope_cwd = self._launch_cwd`. | No global mode means no flag. The optional `launch_cwd` param gives tests a clean injection point (used by AC-CWD-2 and spec `03`). |
| D5 | Leave `data.py` untouched: `scan()`, `Project`, `Session`, `cwd_in_scope` stay (public, tested). Only the app's *use* of them is removed. | Don't churn the tested data layer; `scan_daily(cwd=...)` already does cwd scoping. |
| D6 | **Removed `app.py` imports:** `ModalScreen`, `Vertical`, `Input`, `Project`, `Session`, `scan`, `cwd_in_scope`. **Kept (still used by `DailyScreen`/helpers):** `Text`, `work`, `App`, `ComposeResult`, `Binding`, `Screen`, `DataTable`, `Footer`, `Static`, and from `cctab.data`: `FAMILIES`, `DayUsage`, `Usage` (the `isinstance` guard in `model_cell`, app.py:79), `client_cost` (summary, app.py:254), `cost_of` (`model_cell`, app.py:81), `scan_daily`, `shorten`. The module-level helper `num_cell` becomes unused — remove it; `model_cell`/`human`/`cost_cell` stay. | Ruff (gate) flags unused imports; keep the gate green. `cost_of`/`client_cost` are **kept** — they render the daily table. `Input` is re-added by `03`. |

## File Plan

| Path | Action | Layer | Summary |
|------|--------|-------|---------|
| src/cctab/app.py | MODIFY | tui | Remove `ProjectsScreen`, `SessionsScreen`, `COLUMNS`, `SORTABLE`, the `num_cell` helper, the `"projects"` `MODES` entry, the `g`/`d`/`p` bindings + `action_toggle_scope`, `parse_scope`/`--global`. **`CCTab.__init__`:** drop `global_scope`/`self.projects`, add `launch_cwd` param (D4), `scope_cwd = launch cwd`. **`load_data`:** drop the `get_screen("projects")`/`merge_by_cwd` block (app.py:463–468) and the `scan()` call; call only `scan_daily(cwd=self._launch_cwd)`; `self.call_from_thread(self._on_loaded, days)`. **`_on_loaded`:** new signature `(self, days: list[DayUsage])`, store `self.days`, re-render the daily screen. **`refresh_daily`:** remove the `scope_cwd is None` "global" branch (app.py:229–233) and the "— press g for global" empty-state hint (app.py:238–242); summary always shows `cwd: <shorten>`. Trim unused imports (D6). |
| README.md | MODIFY | other | Remove the leaderboard (`p`), global scope (`g` / `--global` / `-g`), merge-by-cwd (`m`), filter (`/`), and drill-down (`enter`) docs and key-table rows. State cctab is always scoped to the launch directory. |
| tests/test_app.py | MODIFY | tests | AC-CWD-1, AC-CWD-2, AC-CWD-3. **Replace (not patch)** the four existing tests that assert removed behavior — `test_app_mounts_and_handles_keys` (presses `p`/`m`/`slash`), `test_app_starts_in_daily_mode` (reads `app.current_mode`), `test_parse_scope_and_app_scope` (imports `parse_scope`, `CCTab(global_scope=True)`), `test_app_mode_and_scope_bindings` (presses `p`/`g`/`d`, asserts `scope_cwd` toggling). All reference symbols this spec deletes and would `AttributeError`/`ImportError`. |

## Behavior

- **Boot.** `MODES = {"daily": DailyScreen}` + `DEFAULT_MODE = "daily"` auto-mounts `DailyScreen`
  (unchanged Textual mechanism — only the `"projects"` entry is gone). `on_mount` still calls
  `load_data`.
- **Construction.** `CCTab(launch_cwd=None)` captures `launch_cwd or os.getcwd()` once into
  `self._launch_cwd` and sets `self.scope_cwd = self._launch_cwd` permanently. No `global_scope`
  arg, no `self.projects`.
- **Data load.** `load_data` (the `@work(thread=True)` worker) calls only
  `scan_daily(cwd=self._launch_cwd)`; it no longer calls `scan()` or reads
  `ProjectsScreen.merge_by_cwd`. `_on_loaded(self, days)` stores `self.days` and re-renders the
  daily screen (the only `isinstance(self.screen, DailyScreen)` branch left).
- **App bindings after removal:** `q` (quit), `r` (refresh). `g`, `d`, `p` are gone; pressing
  them does nothing.
- **Daily summary.** `refresh_daily` always shows `cwd: <shorten(launch_cwd)>` — no "global"
  branch, no "press g for global" hint. The empty state reads `no transcripts for <dir>`.

## Acceptance Criteria

- **AC-CWD-1**: WHEN the app mounts THE SYSTEM SHALL expose only the daily mode (`MODES.keys()
  == {"daily"}`) and register no `p`, `d`, or `g` binding — pressing `"p"` or `"g"` in a
  headless `run_test()` changes neither the active screen (still a `DailyScreen`) nor
  `scope_cwd`. → `test_app.py::test_single_daily_mode_no_scope_bindings`
- **AC-CWD-2**: WHEN the app loads data THE SYSTEM SHALL call `scan_daily` with `cwd` equal to
  the launch cwd (never `None`/global). Constructing `CCTab(launch_cwd="/x/y")` with `scan_daily`
  monkeypatched/spied records `cwd == "/x/y"`. → `test_app.py::test_load_data_scopes_to_launch_cwd`
- **AC-CWD-3**: WHEN `r` (refresh) is pressed THE SYSTEM SHALL reload and re-render the daily
  view without raising, and `q` SHALL still quit. → `test_app.py::test_refresh_and_quit_dispatch`

## Assumptions (escalation triggers)

- A1: `scan_daily(cwd=<launch cwd>)` (data.py:372–418) already returns correct cwd-scoped daily
  rows; it delegates scoping to `cwd_in_scope` (data.py:110–121), which folds in worktrees.
  **If false:** STOP — this spec assumes no data-layer change; a scoping bug is a separate spec.
- A2: `ProjectsScreen` / `SessionsScreen` are referenced only within `app.py` (and `MODES`),
  nothing external imports them. **If false:** remove the external reference in the same batch or
  return `blocked`.
- A3: The four `tests/test_app.py` tests named in the File Plan **will** fail (error, not just
  assert) after removal, because they touch deleted symbols (`current_mode`, `parse_scope`,
  `global_scope`, removed bindings). They must be replaced in the tests batch. **If false (a
  test survives untouched):** keep it; the AC tests are additive.

## Rationale

The per-directory client margin (spec `03`) was originally hard because the daily view summed
multiple directories into each row, so one markup had no meaning. Removing global scope makes
every view single-directory, dissolving that tension at the source. The leaderboard and
drill-down are the cross-directory surfaces; with billing as the goal they are noise, so they
go. Keeping a **single-entry `MODES`** (D2) rather than deleting the mode system is deliberate:
Textual auto-mounts the default mode, so a 1-entry map is the smallest change that keeps boot
working — deleting `MODES` outright would require a `push_screen`/`SCREENS` restructure and risks
an app that mounts no screen. We keep the data-layer functions (`scan`, `Project`,
`cwd_in_scope`) because they are public and unit-tested; only the app's consumption is deleted.
`breaking: true` because the CLI (`--global`) and key map change. Watch during build: ruff flags
every now-unused import (D6) — land import-clean; `num_cell` becomes dead and is removed; `Input`
intentionally returns in `03`. The `_on_loaded` signature change must update its
`call_from_thread` call site (app.py:471) in the same edit or the app crashes at runtime.

## Canonical Delta

`docs/canonical/daily-cost-view.md`: the daily cost view is now cctab's **only** screen and is
**always scoped to the launch directory**. Remove any description of the projects leaderboard,
the `g`/`--global` global-scope toggle, the `d`/`p` mode switch, and the session drill-down —
these surfaces no longer exist. The app-level key map is `q` (quit) and `r` (refresh); launch
cctab from the directory you want to bill.
