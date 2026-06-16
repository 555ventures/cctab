# Canonical: daily-cost-view

Per-model, per-day cost reporting for cctop. Landed by `specs/20260616/01-daily-cost-by-model.md`.
Cost is **per-model accurate everywhere** — both the daily view and the leaderboard route dollars
through `cost_of`, never a single blended rate.

## Model-family rate table (`data.py`)

Rates and the margin are the **only** numeric knobs and live exclusively in `src/cctop/data.py`
(Worker Rule: number/cost discipline — never a `$/MTok` literal elsewhere).

- `RATES: dict[str, ModelRate]` keyed by canonical lowercase family. `ModelRate` is a frozen
  dataclass of `input`/`output`/`cache_write`/`cache_read` `$/MTok` rates.
- Each class is env-overridable per family via `CCTOP_RATE_<FAMILY>_<CLASS>` (e.g.
  `CCTOP_RATE_OPUS_INPUT`), resolved by the `_rate()` helper at module load.
- Families: `haiku`, `sonnet`, `opus`, `fable` (the displayed columns, in that left→right order
  via the `FAMILIES` tuple), plus two internal families with no column — `synthetic` (priced at
  **$0**) and `default` (unknown models, seeded from the legacy blended `RATE_*` constants so
  `Usage.cost` and AC-DATA-2 stay intact).
- `fable` is seeded at opus-tier rates as a visible placeholder (no public list price known at
  plan time); override via `CCTOP_RATE_FABLE_*` or update the seed when known.

**Adding a new model family is now T1:** mirror a `RATES` row + add the key to the `FAMILIES`
column tuple (a new column also needs its `add_column` in `DailyScreen.on_mount`). No spec needed.

## family resolution (`family_of`)

Case-insensitive **substring** match on the model id, in priority order opus → sonnet → haiku →
fable (survives version bumps like `claude-opus-4-8`). `"<synthetic>"` → `synthetic`; anything
else, including `None`, → `default`. Never raises.

## local-day bucketing (`_local_day`)

Each usage line's UTC `timestamp` is bucketed by **local calendar day** (matches a wall-clock
workday). The trailing `Z` is normalized to `+00:00` before `datetime.fromisoformat` (bare `Z` is
rejected before Python 3.11, and all real transcripts end in `Z`), then `.astimezone()` to local,
`%Y-%m-%d`. Missing/unparseable timestamp → the `"unknown"` sentinel, which is still counted and
**always sorts after** every real day (use the explicit two-list sort — a bare `reverse=True`
wrongly floats `"unknown"` to the front).

## client margin (`CCTOP_MARGIN` / `client_cost`)

`CLIENT $ = cost × CCTOP_MARGIN`, a **markup multiplier**, default `1.0` (client == cost).
`MARGIN` lives in `data.py`; `client_cost(cost)` is the single function that applies it — reuse it
everywhere (per-row cells via `DayUsage.client`, and summary totals), never an inline `× margin`.

## cwd scope (`cwd_in_scope`)

A session matches a scope when its transcript `cwd` **is the scope directory or nested beneath
it** (`cwd_in_scope(session_cwd, scope)`), so launching in a project folds in its
`.claude/worktrees/*` sessions; `scope=None` → global (everything matches). Used by both
`scan_daily(cwd=…)` and the leaderboard's render-time filter.

## two-view structure (Textual `MODES`)

`CCTop` is built on Textual **modes**: `MODES = {"daily": DailyScreen, "projects":
ProjectsScreen}`, `DEFAULT_MODE = "daily"`. `d`/`p` switch views, `g` (`action_toggle_scope`)
flips cwd↔global for the active view. The launch cwd is captured **once at construction**
(`self._launch_cwd`) and never re-read on rescans. `--global`/`-g` (parsed by `parse_scope` in
`app.py:main`) starts in global scope. A **single** threaded `@work` scan pass populates both
`app.projects` and `app.days`; each screen renders from app state on activation. `DailyScreen` is
day-rows × family-columns with `$cost(tokens)` cells (via `model_cell`) and `EST $`/`CLIENT $`
totals; `ProjectsScreen` is the leaderboard moved verbatim, scope-filtered, costed via the
accurate `Project.cost`.
