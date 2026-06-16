---
date: 2026-06-16
status: hardened
risk: T2
area: tui
design: false
breaking: false
depends_on: ["02-cwd-only-daily", "03-per-dir-margin-config"]
depended_on_by: []
---

# Multi-select day rows + CSV copy

## Goal

Let the user pick which days go into a bill and copy them to the clipboard as CSV for pasting
into Google Sheets. Textual's `DataTable` has no native multi-row selection, so cctop adds a
hand-rolled selection set: `space` toggles the cursor's day in/out of a marked set (shown with
a marker), and `y` copies the marked days as CSV — or, if nothing is marked, the whole visible
table. "Done" means a user can mark a billing period's days, press `y`, and paste a clean
numeric CSV (no `$`, no `k`/`M` abbreviations) into a spreadsheet.

## Decisions (locked — workers apply verbatim, never override)

| ID | Decision | One-line rationale |
|----|----------|--------------------|
| D1 | Selection is a `set[str]` of day keys on `DailyScreen` (`self.selected`), initialized in a new `DailyScreen.__init__` that calls `super().__init__()`. `space` toggles the cursor row's day. Daily rows are added with `key=d.day`. | `DataTable` has no native multi-select (verified Textual 8.2.7); a day-keyed set is the minimal honest mechanism. An explicit `__init__` is the only safe place to init the set (not `on_mount`, not `on_show`). |
| D2 | A leftmost **marker column** (`key="mark"`, width 2, blank header) shows `●` for marked rows, blank otherwise. Added as the **first** `add_column` in `on_mount` (before `DAY`). | Visible selection state with zero extra widgets. |
| D3 | `y` copies CSV via `self.app.copy_to_clipboard(text)` (verified present, `app.py:1770` in Textual 8.2.7, `(self, text: str) -> None`): marked days if `self.selected` is non-empty, else **all visible days**. Copying never mutates `selected`. | The billing flow; "whole table when nothing marked" matches the user's request. |
| D4 | CSV columns (header, then one row per day, then a `TOTAL` row): `day,haiku_tokens,haiku_cost,sonnet_tokens,sonnet_cost,opus_tokens,opus_cost,fable_tokens,fable_cost,est_usd,client_usd`. Per-family order = `FAMILIES = ("haiku","sonnet","opus","fable")`. | Per-family token/cost split → spreadsheet-summable; `FAMILIES` is the canonical order. |
| D5 | CSV numbers are **raw**: tokens as integers (`298000`), dollars as 2-decimal floats (`6.72`) — no `$`, no thousands separators, no `human()` abbreviation. Built with the stdlib `csv` module. A family **absent** from `d.by_model` contributes `0` tokens and `0.00` cost (do NOT call `cost_of(None, …)` — it dereferences `usage.input` and raises). | Sheets must `SUM()` the columns; the None-guard prevents an `AttributeError` on any day missing a family (`model_cell` already guards None at app.py:77). |
| D6 | CSV building is a **pure function** `daily_csv(days: list[DayUsage]) -> str` in `app.py`, taking the already-chosen day list. Per-family cost via `cost_of(u, fam)` for present `u`; row `est_usd` = `d.cost`, `client_usd` = `d.client` (data.py). No rate literal in `app.py`. | Pure → unit-testable without the TUI; respects number discipline (rates stay in data.py). |
| D7 | `space`/`y` append to `DailyScreen.BINDINGS` (which spec `03` already created for `e`/`escape`). Selection is pruned to present days at the top of `refresh_daily` (`self.selected &= {d.day for d in app.days}`). | Bindings belong to the screen owning the table; stale selections must not survive a rescan (`r`). |
| D8 | The copy confirmation uses `self.app.notify(f"copied {n} day(s) to clipboard (CSV)", timeout=3)` — it does NOT overwrite the `#summary` Static. | A toast auto-dismisses; clobbering the summary would need a timer to restore it. `notify` is built for this (App.notify, Textual 8.2.7). |

## File Plan

| Path | Action | Layer | Summary |
|------|--------|-------|---------|
| src/cctop/app.py | MODIFY | tui | `DailyScreen`: add `__init__` initializing `self.selected: set[str] = set()` (D1); append `Binding("space","toggle_select","Mark")` and `Binding("y","copy_csv","Copy CSV")` to `BINDINGS`; in `on_mount` add the leftmost `mark` column (D2); in `refresh_daily` prune `self.selected` to present days, prepend the marker cell to each row's `cells` list (before `d.day`), and pass `key=d.day` to `add_row`. Add `action_toggle_select` (cursor row index → toggle `app.days[i].day`, guard empty/out-of-range, re-render) and `action_copy_csv` (marked-or-all → `daily_csv` → `copy_to_clipboard` → `notify`). Add module-level `daily_csv(days) -> str` using the stdlib `csv` module + the None-guard (D5). |
| README.md | MODIFY | other | Document `space` (mark/unmark a day) and `y` (copy marked days, or all, as CSV) in the key table and a short "Copy for billing" note. |
| tests/test_app.py | MODIFY | tests | AC-CSV-1..4 — `daily_csv` exact output incl. a day **missing a family** (the None-guard path); `space` toggles `selected`; `y` calls `copy_to_clipboard` with marked-only vs all-visible CSV. |

## UI

- **Marker column.** `on_mount` first runs `table.add_column("", key="mark", width=2)`, then the
  existing `DAY`/family/`EST $`/`CLIENT $` columns. `refresh_daily` prepends a marker cell:
  `Text("●", style="bold green")` when `d.day in self.selected`, else `Text("")`. Rows added with
  `key=d.day`.
- **Component API (embedded — workers do not query MCP; verified against `.venv`, Textual 8.2.7):**
  - `App.copy_to_clipboard(self, text: str) -> None` (app.py:1770) — writes to the system
    clipboard (OSC-52); no return; safe headless. Call `self.app.copy_to_clipboard(csv_text)`.
  - `App.notify(self, message, *, timeout=...)` — transient toast; use for the copy confirmation.
  - `DataTable.cursor_row -> int` (_data_table.py:836) — focused row index; `app.days[cursor_row]`
    is the cursor's day (row order == `app.days` order). `space`/`y` are NOT in `DataTable.BINDINGS`
    (verified), so they bubble to `DailyScreen`.
  - Per-screen `BINDINGS` append: `Binding("space","toggle_select","Mark")`,
    `Binding("y","copy_csv","Copy CSV")` with matching `action_toggle_select`/`action_copy_csv`.

## Behavior

- **Toggle (`space`):** `i = table.cursor_row`; if `0 <= i < len(app.days)`, `day =
  app.days[i].day`; toggle `day` in `self.selected`; `refresh_daily()` so the marker updates. On
  an empty table, no-op.
- **Copy (`y`):** `rows = [d for d in app.days if d.day in self.selected]` when `self.selected`
  is non-empty, else `list(app.days)`; `text = daily_csv(rows)`;
  `self.app.copy_to_clipboard(text)`; `self.app.notify(f"copied {len(rows)} day(s) to clipboard
  (CSV)", timeout=3)`.
- **`refresh_daily` (extended):** first `self.selected &= {d.day for d in app.days}` (drop days
  gone after a rescan); then build rows with the marker cell prepended and `key=d.day`.
- **`daily_csv(days)`:** write the D4 header; for each day, for each `fam` in `FAMILIES`,
  `u = d.by_model.get(fam)`, `toks = u.total if u else 0`, `cost = cost_of(u, fam) if u else 0.0`;
  emit `day, <toks>, f"{cost:.2f}"` per family, then `f"{d.cost:.2f}"`, `f"{d.client:.2f}"`;
  finally a `TOTAL` row summing each numeric column. Empty `days` → header + a `TOTAL` row of
  zeros (valid CSV, no crash).

## Acceptance Criteria

- **AC-CSV-1**: WHEN `daily_csv` is given a single `DayUsage(day="2026-06-16",
  by_model={"opus": <usage with total T costing 6.72 est>})` (margin 1.0 → client 6.72) THE
  SYSTEM SHALL emit the D4 header line, then the data row
  `2026-06-16,0,0.00,0,0.00,<T>,6.72,0,0.00,6.72,6.72` (haiku/sonnet/fable zeroed, opus present,
  then `est_usd,client_usd`), then a `TOTAL` row whose numeric columns equal that single row. →
  `test_app.py::test_daily_csv_shape_and_totals`
- **AC-CSV-2**: WHEN numeric fields are rendered THE SYSTEM SHALL use raw values — a token count
  of 298000 appears as `298000` (not `298k`), a cost of 6.72 as `6.72` (not `$6.72`) — AND a day
  missing a family SHALL render `0` / `0.00` for it without raising (the `cost_of(None,…)` guard).
  → `test_app.py::test_daily_csv_raw_numbers_and_missing_family`
- **AC-CSV-3**: WHEN `space` is pressed on the cursor row for day `2026-06-16` THE SYSTEM SHALL
  toggle membership — first press → `selected == {"2026-06-16"}`, second press → `selected ==
  set()`. → `test_app.py::test_space_toggles_selection`
- **AC-CSV-4**: WHEN `y` is pressed THE SYSTEM SHALL call `copy_to_clipboard` once with
  `daily_csv(selected days)` if any are marked, else `daily_csv(all visible days)`. With
  `copy_to_clipboard` spied: one day marked → argument equals `daily_csv([that day])`; none marked
  → equals `daily_csv(app.days)`. → `test_app.py::test_copy_csv_marked_vs_all`

## Assumptions (escalation triggers)

- A1: Textual 8.2.7 exposes `App.copy_to_clipboard(text)` and `App.notify(...)`, both safe to
  call in a headless `run_test()`. (Verified against `.venv`.) **If false:** consult the retainer —
  do not add a clipboard dependency without escalation (new dependency → orchestrator `uv sync`).
- A2: `DataTable.cursor_row` is a 0-based index aligned with `app.days` order. (Verified
  _data_table.py:836.) **If false:** map via
  `table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value` (rows keyed by day, D1).
- A3: Tests spy on `copy_to_clipboard` by monkeypatching it on the app instance, asserting the
  CSV argument (not real clipboard contents). **If false:** assert on `daily_csv` directly and on
  `selected` state.
- A4: Building CSV in `app.py` from `cost_of`/`d.cost`/`d.client` introduces no rate literal and
  does not violate number discipline (pipeline rules § Worker Rules). **If false:** move the
  cost-bearing serialization into `data.py`.

## Rationale

`DataTable` has no shift/ctrl range selection, so a day-keyed `set` + marker column is the
smallest mechanism that gives real multi-select; keying rows by `d.day` (D1) is what lets `space`
resolve "the cursor's day" deterministically. The refuter caught two real bugs the first draft
would have shipped: (1) `daily_csv` calling `cost_of(None, fam)` on any day missing a family
crashes (`cost_of` has no None guard, data.py:94) — D5 now mandates the `u if u else 0` guard,
and AC-CSV-2 exercises a missing-family day; (2) the original AC-CSV-1 "row ends `,6.72,6.72`"
was wrong — with only opus, the two zeroed fable columns sit between `opus_cost` and the totals,
so the row is `…,6.72,0,0.00,6.72,6.72`. Raw numbers (D4/D5) are the difference between a
summable paste and one a human retypes — `human()`/`cost_cell` are display-only and deliberately
not reused. The pure `daily_csv` (D6) is unit-testable without Textual and keeps dollar math
sourced from `data.py`. "Copy all when nothing marked" (D3) makes the whole-period case one
keystroke; pruning `selected` on refresh (D7) stops a vanished day riding into a bill; `notify`
(D8) avoids clobbering the summary. `depends_on` includes `03` to serialize the two specs' edits
to `DailyScreen.BINDINGS`/`__init__` and avoid a merge conflict. Watch during build: `cursor_row`
semantics (A2) — the keyed-row fallback is pre-thought.

## Canonical Delta

`docs/canonical/daily-cost-view.md`: add a **Billing export** subsection. The daily view supports
marking days with `space` (a `●` marker) and copying them with `y` as CSV to the system clipboard
— marked days, or the whole visible table when nothing is marked. The CSV has a header, one row
per day with raw per-family token and dollar columns plus `est_usd` / `client_usd`, and a `TOTAL`
row, formatted for direct paste into a spreadsheet (no `$` or abbreviated numbers).
